"""``kaskadctl fwd ...`` and ``kaskadctl net ...`` through kaskad.cli.main (stdout, stderr, exit codes)."""

import contextlib
import io
import json
import os

from kaskad import cli, compat, errors, nft, paths, state, sysrun, units
from tests.fakes_nft import F443, F51820, SS_SSHD, World, save_state, write_os_release
from tests.helpers import RootCase
import kaskad


class CliCase(RootCase):
    def setUp(self):
        super().setUp()
        self.w = World()
        self._old_runner = sysrun._default
        sysrun._default = self.w.runner
        self._ssh = os.environ.pop("SSH_CONNECTION", None)
        write_os_release(self)

    def tearDown(self):
        sysrun._default = self._old_runner
        if self._ssh is not None:
            os.environ["SSH_CONNECTION"] = self._ssh
        self.assertEqual(self.w.runner.unmatched, [])
        super().tearDown()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(list(argv))
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def run_json(self, *argv):
        rc, out, err = self.run_cli(*(argv + ("--json",)))
        self.assertEqual(len(out.strip().splitlines()), 1, out)          # exactly one JSON document
        return rc, json.loads(out), err

    def applied(self, fwds=(F443, F51820)):
        st = save_state(fwds)
        nft.apply(st, self.w.runner)
        return st


class FwdCliTest(CliCase):
    def test_list_in_the_2x_format(self):
        save_state([F51820, F443])
        rc, out, err = self.run_cli("fwd", "list")
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual(out, "tcp|443|203.0.113.5:443\nudp|51820|198.51.100.7:51820\n")
        self.assertEqual(self.w.runner.calls, [])

    def test_list_empty_prints_nothing(self):
        save_state()
        self.assertEqual(self.run_cli("fwd", "list"), (0, "", ""))

    def test_list_traffic(self):
        self.applied()
        self.w.nft.set_counter("fwd_tcp_443", 12, 3456)
        rc, out, _ = self.run_cli("fwd", "list", "--traffic")
        self.assertEqual(out, "tcp|443|203.0.113.5:443|12|3456\nudp|51820|198.51.100.7:51820|0|0\n")

    def test_list_json(self):
        save_state([F443])
        rc, doc, _ = self.run_json("fwd", "list")
        self.assertEqual(rc, 0)
        self.assertEqual(doc["forwards"][0]["to"], "203.0.113.5")
        self.assertTrue(doc["ok"])

    def test_engine_legacy_is_code_5(self):
        save_state([F443], engine="legacy")
        for argv in (("fwd", "list"), ("fwd", "add", "--proto", "tcp", "--port", "80", "--to", "203.0.113.5",
                                       "--to-port", "80"), ("fwd", "del", "--proto", "tcp", "--port", "443"),
                     ("fwd", "flush"), ("fwd", "stats")):
            rc, out, err = self.run_cli(*argv)
            self.assertEqual((rc, out), (errors.UNSUPPORTED, ""), argv)
            self.assertTrue(err.startswith("Ошибка: "), err)
        rc, doc, _ = self.run_json("fwd", "list")
        self.assertEqual((rc, doc["ok"], doc["code"]), (5, False, 5))

    def test_add(self):
        save_state()
        rc, out, err = self.run_cli("fwd", "add", "--proto", "tcp", "--port", "443", "--to", "203.0.113.5",
                                    "--to-port", "443", "--name", "Вена")
        self.assertEqual((rc, out, err), (0, "tcp: :443 -> 203.0.113.5:443\n", ""))
        self.assertEqual(state.find_forward(state.load(), "tcp", 443)["name"], "Вена")

    def test_add_warnings_go_to_stderr(self):
        save_state()
        self.w.sysctl.values["net.ipv4.ip_forward"] = "0"
        self.w.sysctl.fail_write = "sysctl: permission denied on key 'net.ipv4.ip_forward'\n"
        rc, out, err = self.run_cli("fwd", "add", "--proto", "udp", "--port", "51820", "--to", "198.51.100.7",
                                    "--to-port", "51820")
        self.assertEqual((rc, out), (0, "udp: :51820 -> 198.51.100.7:51820\n"))
        self.assertTrue(err.startswith("Внимание: не удалось включить net.ipv4.ip_forward"), err)

    def test_add_conflict_code_3_and_force(self):
        save_state()
        self.w.ss = SS_SSHD
        argv = ("fwd", "add", "--proto", "tcp", "--port", "22", "--to", "203.0.113.5", "--to-port", "22")
        rc, out, err = self.run_cli(*argv)
        self.assertEqual((rc, out), (errors.CONFLICT, ""))
        self.assertIn("Порт 22/tcp занят", err)
        rc, doc, _ = self.run_json(*argv)
        self.assertEqual((rc, doc["code"], doc["data"]["kind"]), (3, 3, "ssh"))
        self.assertEqual(doc["data"]["conflicts"][0]["kind"], "ssh")
        rc, out, _ = self.run_cli(*(argv + ("--force",)))
        self.assertEqual((rc, out), (0, "tcp: :22 -> 203.0.113.5:22\n"))

    def test_add_bad_arguments(self):
        save_state()
        rc, _, err = self.run_cli("fwd", "add", "--proto", "tcp", "--port", "70000", "--to", "203.0.113.5",
                                  "--to-port", "1")
        self.assertEqual(rc, errors.USAGE)
        self.assertIn("1–65535", err)
        rc, _, _ = self.run_cli("fwd", "add", "--proto", "tcp", "--port", "80")         # argparse
        self.assertEqual(rc, errors.USAGE)
        rc, _, _ = self.run_cli("fwd")
        self.assertEqual(rc, errors.USAGE)

    def test_del_and_flush(self):
        self.applied()
        self.assertEqual(self.run_cli("fwd", "del", "--proto", "tcp", "--port", "443"), (0, "Правило удалено.\n", ""))
        rc, out, err = self.run_cli("fwd", "del", "--proto", "tcp", "--port", "443")
        self.assertEqual((rc, out), (errors.ERROR, ""))
        self.assertIn("нет", err)
        self.applied()
        self.assertEqual(self.run_cli("fwd", "flush"), (0, "Удалено правил: 2.\n", ""))
        self.assertEqual(state.load()["forwards"], [])

    def test_stats(self):
        self.applied()
        self.w.nft.set_counter("fwd_udp_51820", 1500, 3 * 1024 * 1024)
        rc, out, _ = self.run_cli("fwd", "stats")
        lines = out.splitlines()
        self.assertEqual(rc, 0)
        self.assertTrue(lines[0].startswith("Проброс"))
        self.assertIn("udp :51820", lines[2])
        self.assertIn("1500", lines[2])
        self.assertIn("3,0 МБ", lines[2])
        self.assertIn("Амстердам", lines[2])
        rc, doc, _ = self.run_json("fwd", "stats")
        self.assertTrue(doc["table"])
        self.assertEqual(doc["stats"][1]["bytes"], 3 * 1024 * 1024)

    def test_stats_without_table(self):
        save_state([F443])
        rc, out, _ = self.run_cli("fwd", "stats")
        self.assertEqual(rc, 0)
        self.assertIn("Таблица kaskad не загружена", out)


class NetCliTest(CliCase):
    def test_status(self):
        self.applied()
        compat.ensure(self.w.runner)
        units.install(self.w.runner)
        rc, doc, _ = self.run_json("net", "status")
        self.assertEqual(rc, 0)
        self.assertEqual(doc["engine"], "nft")
        self.assertTrue(doc["support"]["supported"])
        self.assertEqual(doc["table"], {"present": True, "forwards": 2, "in_sync": True, "error": ""})
        self.assertEqual(doc["compat"], {"available": True, "present": True})
        self.assertTrue(doc["services"]["kaskad-net-watch.timer"]["active"])
        self.assertEqual(doc["nft"], "1.0.6")
        rc, out, _ = self.run_cli("net", "status")
        self.assertIn("движок: nftables", out)
        self.assertIn("таблица kaskad: есть, пробросов 2, совпадает с настройками", out)
        self.assertIn("kaskad-net-watch.timer: включена, работает", out)
        # the watch service is a oneshot the timer starts: not «включена, не работает»
        self.assertIn("kaskad-net-watch.service: по таймеру\n", out)
        self.assertNotIn("kaskad-net-watch.service: включена", out)

    def test_status_legacy_without_table(self):
        save_state(engine="legacy")
        rc, out, _ = self.run_cli("net", "status")
        self.assertEqual(rc, 0)
        self.assertIn("движок: iptables (старый)", out)
        self.assertIn("таблица kaskad: нет", out)
        self.assertIn("kaskad-net-watch.service: не установлена", out)

    def test_probe(self):
        rc, out, _ = self.run_cli("net", "probe")
        self.assertEqual(rc, 0)
        self.assertIn("можно включить", out)
        self.write("/etc/os-release", 'ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04.5 LTS"\n')
        rc, doc, _ = self.run_json("net", "probe")
        self.assertEqual((rc, doc["supported"], doc["code"]), (errors.UNSUPPORTED, False, "os"))
        self.assertIn("здесь Ubuntu 22.04.5 LTS", doc["reason"])

    def test_render(self):
        self.applied()
        self.w.nft.set_counter("fwd_tcp_443", 4, 40)
        rc, out, _ = self.run_cli("net", "render", "--full")
        self.assertTrue(out.startswith("table ip kaskad\ndelete table ip kaskad\n"))
        self.assertIn("packets 4 bytes 40", out)
        rc, out, _ = self.run_cli("net", "render")
        self.assertTrue(out.startswith("add table ip kaskad\n"))

    def test_apply(self):
        save_state([F443, F51820])
        rc, out, _ = self.run_cli("net", "apply", "--dry-run")
        self.assertTrue(out.startswith("add table ip kaskad\n"))
        self.assertEqual(self.w.nft_writes(), [])
        rc, out, _ = self.run_cli("net", "apply")
        self.assertEqual((rc, out), (0, "Таблица применена: добавлено 2, удалено 0, изменено 0.\n"))
        save_state([F443], engine="legacy")
        self.assertEqual(self.run_cli("net", "apply")[0], errors.UNSUPPORTED)

    def test_restore(self):
        self.applied()
        self.w.nft.flush_ruleset()
        rc, out, _ = self.run_cli("net", "restore")
        self.assertEqual(rc, 0)
        self.assertIn("загружена", out)
        self.assertEqual(len(self.w.nft.forwards()), 2)
        self.assertIn("NET restore", self.read("/var/log/kaskad.log"))

    def test_check(self):
        self.applied()
        compat.ensure(self.w.runner)
        self.assertEqual(self.run_cli("net", "check"), (0, "Всё в порядке.\n", ""))
        self.w.nft.flush_ruleset()
        rc, doc, _ = self.run_json("net", "check")
        self.assertEqual((rc, doc["ok"], [p["kind"] for p in doc["problems"]]), (1, False, ["table_missing"]))
        rc, out, _ = self.run_cli("net", "check")
        self.assertEqual(rc, 1)
        self.assertIn("таблицы nftables «kaskad» нет", out)
        self.assertEqual(self.run_cli("net", "check", "--repair", "--quiet"), (0, "", ""))
        self.assertEqual(len(self.w.nft.forwards()), 2)
        save_state(engine="legacy")
        self.assertIn("проверять нечего", self.run_cli("net", "check")[1])

    def test_save_counters(self):
        self.applied()
        self.w.nft.set_counter("fwd_tcp_443", 9, 900)
        self.assertEqual(self.run_cli("net", "save-counters", "--quiet"), (0, "", ""))
        self.assertIn("packets 9 bytes 900", self.read("/etc/kaskad/net/kaskad.nft"))
        self.assertEqual(self.run_cli("net", "save-counters"), (0, "Счётчики сохранены: 2.\n", ""))
        save_state(engine="legacy")
        self.assertEqual(self.run_cli("net", "save-counters", "--quiet"), (0, "", ""))

    def test_units_and_teardown(self):
        self.applied()
        compat.ensure(self.w.runner)
        rc, out, _ = self.run_cli("net", "install-units")
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(paths.systemd_dir(), units.NET_UNIT)))
        self.assertTrue(self.w.sysd.units[units.NET_UNIT]["active"])       # started: ExecStop will run at shutdown
        rc, out, _ = self.run_cli("net", "remove-units")
        self.assertEqual((rc, out), (0, "Службы движка удалены.\n"))
        self.assertFalse(os.path.exists(os.path.join(paths.systemd_dir(), units.NET_UNIT)))
        self.run_cli("net", "install-units")
        rc, doc, _ = self.run_json("net", "teardown")
        self.assertEqual((rc, doc["table_deleted"], doc["compat_removed"]), (0, True, 1))
        self.assertEqual(sorted(doc["units_removed"]), sorted(units.UNITS))
        self.assertIsNone(self.w.nft.table())
        self.assertFalse(compat.present(self.w.runner))
        self.assertIn("NET teardown", self.read("/var/log/kaskad.log"))
        self.assertEqual(state.load()["engine"], "nft")                   # the caller decides about state

    def test_version_still_works(self):
        self.assertEqual(self.run_cli("version"), (0, "Kaskad %s\n" % kaskad.__version__, ""))

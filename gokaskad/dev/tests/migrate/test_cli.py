"""kaskadctl migrate ...: JSON documents, texts, exit codes, no secrets in any output."""

import contextlib
import io
import json

from kaskad import cli, errors, state
from tests.helpers import RootCase, lock_held_elsewhere
from tests.migrate.env import SECRETS, Env


class CliTest(RootCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        for s in SECRETS:
            self.assertNotIn(s, out.getvalue())
            self.assertNotIn(s, err.getvalue())
        return rc, out.getvalue(), err.getvalue()

    def one_json(self, text):
        self.assertEqual(text.count("\n"), 1, text)
        return json.loads(text)

    def test_scan_json_and_text(self):
        Env(self, "pro_213").start()
        rc, out, _ = self.run_cli("migrate", "scan", "--json")
        self.assertEqual(rc, errors.OK)
        doc = self.one_json(out)
        for key in ("edition", "version", "engine", "command", "support", "iptables", "forwards", "foreign_dnat",
                    "masquerade", "conflicts", "persisted", "firewall", "services", "egress", "aliases",
                    "monitors", "listeners", "warnings", "summary"):
            self.assertIn(key, doc)
        self.assertEqual(doc["summary"]["import"], 3)
        self.assertTrue(doc["summary"]["has_work"])
        self.assertEqual(doc["forwards"][0]["rules"][0]["role"], "dnat")
        rc, out, _ = self.run_cli("migrate", "scan")
        self.assertIn("Установлено: Kaskad PRO 2.13, движок iptables", out)
        self.assertIn("tcp :443 → 203.0.113.5:443  из Kaskad PRO, перенесу", out)

    def test_plan(self):
        Env(self, "free_v11_two_rules").start()
        rc, out, _ = self.run_cli("migrate", "plan")
        self.assertEqual(rc, errors.OK)
        self.assertTrue(out.startswith("Перевод пробросов на nftables\n"))
        rc, out, _ = self.run_cli("migrate", "plan", "--json")
        doc = self.one_json(out)
        self.assertEqual([a["step"] for a in doc["actions"]],
                         ["snapshot", "state", "nft_apply", "compat", "units", "iptables_remove", "iptables_remove",
                          "persisted_edit", "verify"])
        self.assertIn("Перенесу 2 проброса", doc["preview"])

    def test_apply_needs_yes(self):
        env = Env(self, "pro_213").start()
        rc, out, err = self.run_cli("migrate", "apply")
        self.assertEqual(rc, errors.USAGE)
        self.assertIn("--yes", err)
        rc, out, err = self.run_cli("migrate", "apply", "--json")
        self.assertEqual(self.one_json(out)["code"], errors.USAGE)
        self.assertIsNone(env.nft.table)

    def test_apply_ok_then_nothing(self):
        Env(self, "pro_213").start()
        rc, out, _ = self.run_cli("migrate", "apply", "--yes", "--json")
        self.assertEqual(rc, errors.OK)
        doc = self.one_json(out)
        self.assertEqual((doc["ok"], doc["result"], doc["engine"]), (True, "ok", "nft"))
        rc, out, _ = self.run_cli("migrate", "apply", "--yes")
        self.assertEqual((rc, out), (errors.OK, "Переносить нечего.\n"))

    def test_apply_text(self):
        Env(self, "free_v10").start()
        rc, out, _ = self.run_cli("migrate", "apply", "--yes")
        self.assertEqual(rc, errors.OK)
        self.assertIn("Готово: пробросы работают через nftables.", out)
        self.assertIn("Перенесено: 2 проброса. Убрано правил iptables: 8.", out)

    def test_apply_text_shows_warnings(self):
        Env(self, "foreign").start()
        rc, out, _ = self.run_cli("migrate", "apply", "--yes")
        self.assertEqual(rc, errors.OK)
        self.assertIn("Внимание: tcp :8443: правило правили вручную, условия (-i eth0) будут сняты.", out)
        self.assertIn("Внимание: tcp :443: после правила Kaskad стоит чужое правило", out)

    def test_apply_text_shows_activation_warning(self):
        env = Env(self, "pro_213").start()

        def broken(runner):
            raise errors.KaskadError("systemctl start kaskad-net.service: Job failed")
        from unittest import mock
        with mock.patch("kaskad.units.activate", broken):
            rc, out, _ = self.run_cli("migrate", "apply", "--yes")
        self.assertEqual(rc, errors.OK)
        self.assertIn("Внимание: служба kaskad-net не запустилась: systemctl start kaskad-net.service: Job failed", out)
        self.assertTrue(env.units.installed)

    def test_apply_conflict_exit_3(self):
        Env(self, "docker_after").start()
        rc, out, err = self.run_cli("migrate", "apply", "--yes")
        self.assertEqual(rc, errors.CONFLICT)
        self.assertIn("Ошибка: Переезд сейчас невозможен", err)
        rc, out, err = self.run_cli("migrate", "apply", "--yes", "--json")
        doc = self.one_json(out)
        self.assertEqual((doc["ok"], doc["code"]), (False, errors.CONFLICT))
        self.assertEqual(doc["data"]["blockers"][0]["kind"], "docker_port")
        rc, out, err = self.run_cli("migrate", "apply", "--yes", "--force")
        self.assertEqual(rc, errors.OK)

    def test_apply_unsupported_exit_5(self):
        Env(self, "pro_213", supported=False).start()
        rc, _out, _err = self.run_cli("migrate", "apply", "--yes")
        self.assertEqual(rc, errors.UNSUPPORTED)

    def test_apply_rolled_back_exit_4(self):
        env = Env(self, "pro_213").start()
        env.nft.corrupt = lambda t: t.update(has_masquerade=False)
        rc, out, err = self.run_cli("migrate", "apply", "--yes", "--json")
        self.assertEqual(rc, errors.ROLLED_BACK)
        doc = self.one_json(out)
        self.assertEqual(doc["code"], errors.ROLLED_BACK)
        self.assertTrue(doc["data"]["problems"])
        self.assertEqual(state.load()["engine"], "legacy")

    def test_locked_exit_6(self):
        Env(self, "pro_213").start()
        from unittest import mock
        with lock_held_elsewhere():          # «busy»: another process holds it (T1: the same thread — NestedLockError)
            with mock.patch("kaskad.state.time.sleep", lambda s: None), \
                    mock.patch("kaskad.state.time.time", side_effect=[0, 100, 200, 300]):
                rc, _out, err = self.run_cli("migrate", "apply", "--yes")
        self.assertEqual(rc, errors.LOCKED)

    def test_rollback_flags(self):
        Env(self, "pro_213").start()
        self.run_cli("migrate", "apply", "--yes")
        rc, _out, err = self.run_cli("migrate", "rollback", "--yes")
        self.assertEqual(rc, errors.USAGE)
        rc, _out, err = self.run_cli("migrate", "rollback", "--to-legacy")
        self.assertEqual(rc, errors.USAGE)
        rc, out, _err = self.run_cli("migrate", "rollback", "--to-legacy", "--yes", "--json")
        self.assertEqual(rc, errors.OK)
        doc = self.one_json(out)
        self.assertEqual((doc["result"], doc["engine"], doc["forwards"]), ("ok", "legacy", 3))
        rc, out, _err = self.run_cli("migrate", "rollback", "--to-legacy", "--yes")
        self.assertEqual((rc, out), (errors.OK, "Движок уже iptables — возвращать нечего.\n"))

    def test_backups(self):
        Env(self, "pro_213").start()
        rc, out, _ = self.run_cli("migrate", "backups")
        self.assertEqual(out, "Резервных копий нет.\n")
        self.run_cli("migrate", "apply", "--yes")
        rc, out, _ = self.run_cli("migrate", "backups", "--json")
        doc = self.one_json(out)
        self.assertEqual(len(doc["backups"]), 1)
        self.assertEqual(doc["backups"][0]["from"]["version"], "2.13")
        rc, out, _ = self.run_cli("migrate", "backups")
        self.assertIn("/var/backups/kaskad/", out)

    def test_group_without_command(self):
        Env(self, "empty").start()
        rc, _out, err = self.run_cli("migrate")
        self.assertEqual(rc, errors.USAGE)

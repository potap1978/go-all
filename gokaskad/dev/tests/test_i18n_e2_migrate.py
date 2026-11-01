"""3.3.2 E2, group «migrate»: kaskad.migrate speaks English under use_lang("en") — the preview of the plan, the
text of kaskadctl migrate scan/plan/apply/rollback/backups, the help of the parser, errors of apply, rollback,
snapshot and the signal guard — and Russian, exactly as before, by default."""

import contextlib
import io
import json
import os
import re
import signal

from kaskad import cli, errors, state
from kaskad.errors import KaskadError
from kaskad.i18n import use_lang
from kaskad.migrate import apply as apply_mod
from kaskad.migrate import plan, rollback, scan, snapshot
from tests.helpers import RootCase
from tests.migrate import scripts
from tests.migrate.env import Env
from tests.migrate.test_interrupt import SignalCase, send

CYR = re.compile("[А-Яа-яЁё]")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def spec_server(case):
    """The server of SPEC §4.11 (test_plan.PreviewTest.test_spec_example), without aliases (their names are data)."""
    env = Env(case, "empty").start()
    e = env.ipt
    scripts.free_add(e, "tcp", 443, 443, "203.0.113.5")
    scripts.free_add(e, "tcp", 443, 443, "192.0.2.9")
    e.cmd("-t", "nat", "-N", "DOCKER")
    e.cmd("-t", "nat", "-A", "DOCKER", "-i", "docker0", "-j", "RETURN")
    e.cmd("-t", "nat", "-A", "DOCKER", "!", "-i", "docker0", "-p", "tcp", "--dport", "8080", "-j", "DNAT",
          "--to-destination", "172.17.0.2:80")
    e.cmd("-t", "nat", "-A", "PREROUTING", "-m", "addrtype", "--dst-type", "LOCAL", "-j", "DOCKER")
    scripts.pro213_add(e, "tcp", 8080, 80, "10.0.0.5")
    scripts.pro213_add(e, "udp", 51820, 51820, "198.51.100.7")
    scripts.pro213_add(e, "tcp", 8443, 443, "198.51.100.7")
    e.cmd("-t", "nat", "-A", "PREROUTING", "-p", "tcp", "--dport", "2222", "-j", "DNAT",
          "--to-destination", "10.0.0.7:22")
    return env


class _Case(RootCase):
    def no_cyr(self, text):
        self.assertIsNone(CYR.search(text), text)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(list(argv))
            except SystemExit as e:                    # --help
                rc = e.code
        return rc, out.getvalue(), err.getvalue()


class PreviewEnTest(_Case):
    def test_spec_example_in_english(self):
        env = spec_server(self)
        pl = plan.build(scan.collect(env.runner))
        with use_lang("en"):
            text = pl.preview(backup=self.path("/var/backups/kaskad/20261004T120000Z-pro-2.13"))
        self.no_cyr(text)
        self.assertEqual(text, "\n".join([
            "Switching forwards to nftables",
            "",
            "Will move 3 forwards:",
            "  tcp :443     → 203.0.113.5:443      from the free version",
            "  udp :51820   → 198.51.100.7:51820   from Kaskad PRO",
            "  tcp :8443    → 198.51.100.7:443     from Kaskad PRO",
            "Will not move:",
            "  tcp :443 → 192.0.2.9:443 — was not in effect: a second rule for the same port. Will remove it.",
            "  tcp :8080 → 10.0.0.5:80 — was not in effect: Docker takes the port earlier. Will leave the rule as is.",
            "Will stay as is: a foreign internet access rule via eth0 in iptables, 1 foreign DNAT rule, Docker rules, "
            "the Kaskad internet access rule via eth0 in iptables — another VPN on the server may rely on it.",
            "",
            "What changes:",
            "  • a forward works only for the addresses of this server",
            "  • each forward gets a traffic counter",
            "  • the nftables table “kaskad” replaces the Kaskad iptables rules",
            "Will remove from iptables: 15 rules.",
            "Backup: /var/backups/kaskad/20261004T120000Z-pro-2.13",
            "To undo: menu “Forwarding engine and migration” → “Return to iptables”.",
        ]))
        doc = pl.to_dict()                   # the plan document is built in Russian (default) — and in English
        self.assertIn("на него может опираться другой VPN на сервере", [k["needed_by"] for k in doc["kept"]])
        with use_lang("en"):
            doc = pl.to_dict()
        self.assertIn("another VPN on the server may rely on it", [k["needed_by"] for k in doc["kept"]])
        self.no_cyr(doc["preview"])

    def test_russian_unchanged(self):
        env = spec_server(self)
        text = plan.build(scan.collect(env.runner)).preview()
        self.assertTrue(text.startswith("Перевод пробросов на nftables\n\nПеренесу 3 проброса:\n"))
        self.assertIn("Останется как было: чужое правило выхода в интернет через eth0 в iptables, 1 чужое правило "
                      "DNAT, правила Docker, правило Kaskad для выхода в интернет через eth0 в iptables — на него "
                      "может опираться другой VPN на сервере.", text)
        self.assertIn("Уберу из iptables: 15 правил.", text)

    def test_warnings_and_skips(self):
        env = Env(self, "foreign").start()
        with use_lang("en"):                 # the scan says its conflicts in the language of the call too
            text = plan.build(scan.collect(env.runner)).preview()
        self.no_cyr(text)
        self.assertIn("tcp :8443: the rule was edited by hand, its conditions (-i eth0) will be dropped.", text)
        self.assertIn("it was not in effect and will not be after the switch either", text)
        self.assertIn("Kaskad 3 does not forward to such a target", text)
        self.assertIn("Note:", text)

    def test_empty_server_and_blockers(self):
        env = Env(self, "empty").start()
        with use_lang("en"):
            text = plan.build(scan.collect(env.runner)).preview()
        self.assertIn("No Kaskad forwards in iptables — will turn on the new forwarding engine.", text)
        self.no_cyr(text)
        self.tearDown()
        self.setUp()
        Env(self, "docker_after").start()
        with use_lang("en"):
            pl = plan.build(scan.collect(cli_runner()))
            text = pl.preview()
        self.assertTrue(pl.blocked)
        self.assertIn("The switch is not possible right now:", text)
        self.assertIn("the port is published by a Docker container", text)
        self.assertIn("with --force the port stays with Kaskad", text)
        self.no_cyr(text)

    def test_plural(self):
        with use_lang("en"):
            self.assertEqual([plan.forwards_word(n) for n in (1, 2, 5, 21)],
                             ["1 forward", "2 forwards", "5 forwards", "21 forwards"])
            self.assertEqual(plan.rules_word(1), "1 rule")
        self.assertEqual([plan.forwards_word(n) for n in (1, 2, 5, 21)],
                         ["1 проброс", "2 проброса", "5 пробросов", "21 проброс"])


def cli_runner():
    from kaskad import sysrun
    return sysrun.default_runner()


class CliEnTest(_Case):
    def test_scan_plan_apply_backups_rollback(self):
        Env(self, "pro_213").start()
        self.write("/etc/kaskad/aliases", "")           # the names of the fixture are data (Cyrillic)
        with use_lang("en"):
            rc, out, _ = self.run_cli("migrate", "scan")
            self.assertEqual(rc, errors.OK)
            self.no_cyr(out)
            self.assertIn("Installed: Kaskad PRO 2.13, forwarding engine iptables", out)
            self.assertIn("tcp :443 → 203.0.113.5:443  from Kaskad PRO, will move", out)
            self.assertIn("Total: 3 to move,", out)

            rc, out, _ = self.run_cli("migrate", "plan")
            self.no_cyr(out)
            self.assertTrue(out.startswith("Switching forwards to nftables\n"))

            rc, out, _ = self.run_cli("migrate", "backups")
            self.assertEqual(out, "No backups.\n")

            rc, out, err = self.run_cli("migrate", "apply")
            self.assertEqual(rc, errors.USAGE)
            self.assertEqual(err, "Confirmation needed: kaskadctl migrate apply --yes\n")
            rc, out, _ = self.run_cli("migrate", "apply", "--json")
            self.assertEqual(json.loads(out)["error"], "--yes is required")

            rc, out, _ = self.run_cli("migrate", "apply", "--yes")
            self.assertEqual(rc, errors.OK)
            self.no_cyr(out)
            self.assertIn("Done: the forwards work through nftables.", out)
            self.assertIn("Moved: 3 forwards. iptables rules removed:", out)
            self.assertIn("Backup: /var/backups/kaskad/", out)

            rc, out, _ = self.run_cli("migrate", "apply", "--yes")
            self.assertEqual(out, "Nothing to move.\n")

            rc, out, _ = self.run_cli("migrate", "backups")
            self.no_cyr(out)
            self.assertIn(": Kaskad PRO 2.13 → ", out)
            self.assertIn("before the switch", out)

            rc, out, err = self.run_cli("migrate", "rollback", "--yes")
            self.assertEqual(err, "Required: kaskadctl migrate rollback --to-legacy --yes\n")
            rc, out, _ = self.run_cli("migrate", "rollback", "--to-legacy", "--yes")
            self.assertEqual(rc, errors.OK)
            self.no_cyr(out)
            self.assertIn("The forwards are back in iptables (Kaskad 2.13 format): 3 forwards.", out)
            rc, out, _ = self.run_cli("migrate", "rollback", "--to-legacy", "--yes")
            self.assertEqual(out, "The forwarding engine is already iptables — nothing to return.\n")

    def test_russian_cli_unchanged(self):
        Env(self, "pro_213").start()
        rc, out, _ = self.run_cli("migrate", "scan")
        self.assertIn("Установлено: Kaskad PRO 2.13, движок iptables", out)
        self.assertIn("Итого: перенесу 3, уберу правил iptables", out)
        rc, out, err = self.run_cli("migrate", "apply")
        self.assertEqual(err, "Нужно подтверждение: kaskadctl migrate apply --yes\n")
        rc, out, _ = self.run_cli("migrate", "backups")
        self.assertEqual(out, "Резервных копий нет.\n")

    def test_help(self):
        Env(self, "empty").start()
        with use_lang("en"):
            _rc, out, _ = self.run_cli("migrate", "--help")
            self.assertIn("move the forwards to nftables", self.run_cli("--help")[1])
            _rc, apply_help, _ = self.run_cli("migrate", "apply", "--help")
        for text in (out, apply_help):
            for ru in ("что стоит на сервере", "перевести пробросы", "подтверждение", "не останавливаться"):
                self.assertNotIn(ru, text)
        self.assertIn("what is installed on the server (read-only)", out)
        self.assertIn("switch the forwards to nftables", out)
        self.assertIn("return the forwards to iptables (2.13 format)", out)
        self.assertIn("do not stop on conflicts", apply_help)
        self.assertIn("confirmation", apply_help)
        _rc, out, _ = self.run_cli("migrate", "--help")
        self.assertIn("что стоит на сервере (только чтение)", out)


class ErrorsEnTest(_Case):
    def test_rolled_back(self):
        env = Env(self, "pro_213").start()
        env.nft.corrupt = lambda t: t.update(has_masquerade=False)
        with use_lang("en"), self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.ROLLED_BACK)
        msg = str(cm.exception)
        self.no_cyr(msg)
        self.assertTrue(msg.startswith("The check after the switch failed: the table has no masquerade. "
                                       "Everything is back as it was. Backup: /var/backups/kaskad/"), msg)

    def test_blocked(self):
        Env(self, "docker_after").start()
        with use_lang("en"), self.assertRaises(KaskadError) as cm:
            apply_mod.run(cli_runner())
        self.assertEqual(cm.exception.code, errors.CONFLICT)
        self.assertTrue(str(cm.exception).startswith("The switch is not possible right now: tcp :"), str(cm.exception))
        self.no_cyr(str(cm.exception))

    def test_rollback_errors(self):
        env = Env(self, "pro_213").start()
        apply_mod.run(env.runner)
        env.runner._handlers.insert(0, (["ip", "route", "get", "8.8.8.8"], (2, "", "unreachable\n")))
        with use_lang("en"), self.assertRaises(KaskadError) as cm:
            rollback.run(env.runner)
        self.assertEqual(str(cm.exception), "could not detect the network interface (ip route get 8.8.8.8)")
        with self.assertRaises(KaskadError) as cm:
            rollback.run(env.runner)
        self.assertEqual(str(cm.exception), "не удалось определить сетевой интерфейс (ip route get 8.8.8.8)")

    def test_rollback_without_netfilter_persistent(self):
        env = Env(self, "pro_213", netfilter_persistent=False).start()
        apply_mod.run(env.runner)
        with use_lang("en"):
            res = rollback.run(env.runner)
        self.assertTrue(res["warnings"])
        for w in res["warnings"]:
            self.no_cyr(w)
        self.assertIn("The saved rules (/etc/iptables/rules.v4) were not updated: there is no netfilter-persistent. "
                      "The forwards may be gone after a reboot.", res["warnings"])

    def test_snapshot_damaged(self):
        env = Env(self, "pro_213").start()
        path = snapshot.create(env.runner, scan.collect(env.runner), None)
        with open(os.path.join(path, "iptables-save.txt"), "a") as f:
            f.write("-A INPUT -j DROP\n")
        with use_lang("en"), self.assertRaises(KaskadError) as cm:
            snapshot.read_text(path, "iptables-save.txt")
        self.assertEqual(str(cm.exception), "the file iptables-save.txt in the backup is damaged (sha256 mismatch)")

    def test_engine_health_in_english(self):
        env = Env(self, "pro_213").start()
        apply_mod.run(env.runner)
        env.nft.table = None
        with use_lang("en"):
            probs = scan.engine_health(scan.collect(env.runner))
        self.assertIn("the nftables table “kaskad” is not loaded", probs)
        self.assertEqual(scan.engine_health(scan.collect(env.runner))[0], "таблица nftables «kaskad» не загружена")


class SignalEnTest(SignalCase):
    def test_signal_message_in_english(self):
        env = Env(self, "pro_213").start()
        env.nft.after_apply = lambda: send(signal.SIGTERM)
        with use_lang("en"), self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.ROLLED_BACK)
        self.assertIn("The switch was interrupted: got the signal SIGTERM — the change was interrupted", str(cm.exception))
        self.assertIsNone(CYR.search(str(cm.exception)))
        self.assertEqual(state.load()["engine"], "legacy")
        self.assert_handlers_back()


class CatalogTest(RootCase):
    def test_same_keys_and_no_cyrillic_in_english(self):
        cat = os.path.join(ROOT, "lib", "kaskad", "i18n", "catalog")
        with open(os.path.join(cat, "ru", "migrate.json"), encoding="utf-8") as f:
            ru = json.load(f)
        with open(os.path.join(cat, "en", "migrate.json"), encoding="utf-8") as f:
            en = json.load(f)
        self.assertEqual(sorted(ru), sorted(en))
        for k, v in en.items():
            self.assertTrue(k.startswith("migrate."), k)
            self.assertIsNone(CYR.search(v), k)
            self.assertNotIn("{key}", v)
            self.assertNotIn("{lang}", v)

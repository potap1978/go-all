"""3.3.2 E2, the group «backup»: kaskad.backup, kaskad.cli_backup, kaskad.secrets speak the language of the context —
the refusals of a file, the preview and the import, the counts (two forms in English), the journal, the help of the
parser; the Russian stays what it was."""

import io
import json
import os
import re
import sys
import unittest
from unittest import mock

from kaskad import backup, cli, paths, secrets
from kaskad.errors import KaskadError, StateError
from kaskad import i18n
from kaskad.i18n import use_lang
from tests.test_backup import PW, BackupCase

CYR = re.compile("[А-Яа-яЁё]")
DATA = ("Мама", "Телефон", "Вена")                 # names of the test world: data, not words of Kaskad


def _clean(text):
    for d in DATA:
        text = text.replace(d, "")
    return text


def _err(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except (KaskadError, StateError) as e:
        return str(e)
    raise AssertionError("no refusal")


class WordsTest(unittest.TestCase):
    def test_constants_titles_and_counts(self):
        with use_lang("en"):
            self.assertEqual(backup.WRONG_PASSWORD, "Wrong backup password.")
            self.assertEqual(backup.TITLES["bot"], "Telegram bot")
            self.assertEqual(backup.SHORT["github"], "GitHub key")
            self.assertEqual(sorted(backup.TITLES), sorted(backup.PARTS))
            for msg in (backup.DAMAGED, backup.NO_UNDO, backup.NOT_A_COPY):
                self.assertIsNone(CYR.search(msg), msg)
            items = backup.here_items({"gateway": {"users": 1, "devices": 21, "inbounds": ["wg"]},
                                       "exits": {"keys": 2, "subs": 1}, "forwards": {"forwards": 5},
                                       "site": {"domain": "vpn.example.org"}, "bot": {"token": True},
                                       "mcp": {"keys": 3}})
            self.assertEqual([i["text"] for i in items],
                             ["1 user", "21 devices", "protocols: WireGuard", "2 exits", "1 exit subscription",
                              "5 forwards", "domain vpn.example.org", "Telegram bot", "3 AI agent keys"])
            self.assertEqual(backup._names(["a", "b", "c", "d", "e", "f", "g"]), "a, b, c, d, e and 2 more")
            self.assertEqual(backup._route_words({"via": "pool", "fail": "direct"}, {}), "via exits, on failure — direct")
        with use_lang("ru"):
            self.assertEqual(backup.WRONG_PASSWORD, "Неверный пароль копии.")
            self.assertEqual(backup.TITLES["bot"], "Telegram-бот")
            self.assertEqual(backup.SHORT["mcp"], "ключи агентов")
            items = backup.here_items({"gateway": {"users": 21, "devices": 3}, "exits": {"keys": 5, "subs": 2}})
            self.assertEqual([i["text"] for i in items],
                             ["21 пользователь", "3 устройства", "5 выходов", "2 подписки выходов"])
            self.assertEqual(backup._route_words({"via": "exit:x", "fail": "block"}, {"x": "Вена"}),
                             "через выход «Вена», при отказе — блок")

    def test_refusals_of_a_file_and_a_password(self):
        newer = "KASKAD-BACKUP 2\n{}\nAAAA\nMAC 00\n"
        awg = json.dumps({"kind": "kaskad-awg-export"})
        with use_lang("en"):
            msgs = [_err(backup.header_of, "just text"), _err(backup.header_of, newer), _err(backup.header_of, awg),
                    _err(backup.header_of, "KASKAD-BACKUP 1\n{}"), _err(backup.check_password, "short"),
                    _err(backup.check_password, "long enough\npassword"), _err(backup._parts, "gateway,nope"),
                    _err(secrets._check_key, "bad key"), _err(backup.read_snapshot, "/nonexistent")]
            for m in msgs:
                self.assertIsNone(CYR.search(m), m)
            self.assertIn("newer Kaskad (2)", msgs[1])
            self.assertIn("“Protocols → AmneziaWG engine → Import”", msgs[2])
            self.assertEqual(msgs[4], "The backup password must be at least 8 characters.")
            self.assertEqual(msgs[6], "Unknown backup parts: nope (available: %s)." % ", ".join(backup.PARTS))
            self.assertEqual(msgs[7], "invalid secret name: 'bad key'")
        with use_lang("ru"):
            self.assertEqual(_err(backup.check_password, "short"), "Пароль копии — не короче 8 знаков.")
            self.assertEqual(_err(secrets._check_key, "bad key"), "недопустимое имя секрета: 'bad key'")
            self.assertIn("обрезан", _err(backup.header_of, "KASKAD-BACKUP 1\n{}"))

    def test_a_broken_secrets_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = d + "/secrets.json"
            for body, en, ru in (("{", "is damaged", "повреждён"), ("[]", "expected an object", "ожидался объект"),
                                 ('{"schema": 9, "values": {}}', "schema 9 is not supported", "схема 9")):
                with open(p, "w") as f:
                    f.write(body)
                with use_lang("en"):
                    m = _err(secrets.load, p)
                    self.assertIn(en, m)
                    self.assertIsNone(CYR.search(m), m)
                with use_lang("ru"):
                    self.assertIn(ru, _err(secrets.load, p))

    def test_help_of_the_parser(self):
        with use_lang("en"):
            texts = []
            for argv in (["backup", "--help"], ["backup", "export", "--help"], ["backup", "import", "--help"]):
                out = io.StringIO()
                with mock.patch.object(sys, "stdout", out), self.assertRaises(SystemExit):
                    cli.build_parser().parse_args(argv)
                text = out.getvalue()
                ours = "\n".join(l for l in text.splitlines() if "--json" not in l)     # --json: kaskad.cli's
                self.assertIsNone(CYR.search(ours), text)
                texts.append(text)
            self.assertIn("load a backup: without --yes — preview only", texts[0])
            self.assertIn("the backup text to standard output", texts[1])
        with use_lang("ru"):
            out = io.StringIO()
            with mock.patch.object(sys, "stdout", out), self.assertRaises(SystemExit):
                cli.build_parser().parse_args(["backup", "--help"])
            self.assertIn("загрузить копию: без --yes — только предпросмотр", out.getvalue())


class FlowTest(BackupCase):
    """The real paths: export, preview, import, undo and a failure — in English, then the same in Russian. The server
    speaks English here too (KASKAD_LANG=en in its config): the journal is in the language of the server."""

    def setUp(self):
        super().setUp()
        i18n.set_lang("en")                     # under the test's KASKAD_ROOT
        self.server = "en"

    def new_node(self):
        """A fresh node speaks the language of the server too (the base helper rewrites /etc/kaskad/config); the
        import must keep it — the journal lines after it are checked."""
        super().new_node()
        i18n.set_lang(self.server)

    def test_export_preview_import_undo_in_english(self):
        self.old_node()
        with use_lang("en"):
            text = backup.export(self.w.runner, PW)["text"]
            self.new_node()
            self.here_ip = "151.101.1.69"
            doc = backup.unseal(self.w.runner, text, PW)
            pv = backup.preview(self.w.runner, doc)
            rows = [l for l in pv["text"] if l.startswith("• ")]
            self.assertTrue(rows)
            self.assertIn("• gateway — will be moved: users 1, devices 1, protocols: ", "\n".join(rows))
            self.assertTrue(any(w.startswith("Devices connect to 93.184.216.34, but this server is 151.101.1.69")
                                for w in pv["address"]["warnings"]), pv["address"])
            self.assertTrue(any(w.startswith("One bot token — one server") for w in pv["warnings"]))
            mine = rows + pv["address"]["warnings"] + [w for w in pv["warnings"] if w.startswith("One bot")]
            self.assertIsNone(CYR.search(_clean("\n".join(mine))), mine)
            said = []
            with mock.patch("kaskad.gw.net.detect_host", lambda runner: ("151.101.1.69", "route")):
                res = backup.apply(self.w.runner, doc, say=said.append)
            self.assertTrue(res["text"][0].startswith("Moved: "), res["text"])
            self.assertIn("Previous settings saved: ", res["text"][-1])
            self.assertTrue(any(s.startswith("Moving: gateway: users") for s in said), said)
            ours = [s for s in said if s.startswith(("Moving:", "Turning", "Removing", "Installing", "Restoring"))]
            self.assertIsNone(CYR.search("\n".join(ours + res["text"][:1] + res["text"][-1:] + res["notes"])), res)
            log = [l for l in self.log().splitlines() if "BACKUP" in l]
            self.assertTrue(any("BACKUP import: moved — " in l for l in log), log)
            res = backup.undo(self.w.runner)
            self.assertTrue(res["text"][0].startswith("Restored: "), res["text"])
            self.assertTrue(any("BACKUP undo: restored — " in l for l in self.log().splitlines()))

    def test_the_cli_in_english(self):
        self.old_node()
        out = paths.P("/root")
        with use_lang("en"), mock.patch.object(sys, "stdin", io.StringIO(PW + "\n")):
            rc, o, err = self.run_cli("backup", "export", "--file", out)
        self.assertEqual(rc, 0, err)
        self.assertTrue(o.startswith("Backup saved: "), o)
        self.assertIsNone(CYR.search(o), o)
        with use_lang("en"), mock.patch.object(sys, "stdin", io.StringIO(PW + "\n")):
            rc, o, err = self.run_cli("backup", "import", o.split(": ", 1)[1].split(" (", 1)[0])
        self.assertEqual(rc, 0, err)
        self.assertIn("What happens:", o)
        self.assertIn("To apply: the same command with --yes.", o)
        with use_lang("en"):
            rc, o, err = self.run_cli("backup", "import", "/nonexistent.kbk")
            self.assertIn("No file /nonexistent.kbk — check the path.", o + err)
            rc, o, err = self.run_cli("backup", "summary")
            self.assertTrue(o.startswith("The backup will hold:"), o)
            self.assertIsNone(CYR.search(o), o)
        with use_lang("ru"):
            rc, o, err = self.run_cli("backup", "summary")
            self.assertTrue(o.startswith("В копию войдёт:"), o)

    def test_a_failure_in_english(self):
        self.old_node()
        with use_lang("en"):
            text = backup.export(self.w.runner, PW)["text"]
            self.new_node()
            doc = backup.unseal(self.w.runner, text, PW)

            def broken(runner):
                raise KaskadError("apt failed")
            self.ads["awg1"].install = broken
            with self.assertRaises(KaskadError) as c:
                backup.apply(self.w.runner, doc)
            msg = str(c.exception)
            self.assertTrue(msg.startswith("The move failed at “gateway”: apt failed. Restored the previous settings"),
                            msg)
            self.assertIsNone(CYR.search(msg), msg)
            self.assertTrue(any("BACKUP import: failed at “gateway”" in l for l in self.log().splitlines()))


    def test_journal_in_the_language_of_the_server(self):
        """A request in English on a Russian server: the answer in English, the journal in Russian."""
        self.old_node()
        self.server = "ru"
        i18n.set_lang("ru")
        with use_lang("en"):
            text = backup.export(self.w.runner, PW)["text"]
            self.new_node()
            res = backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        self.assertIsNone(CYR.search("\n".join(res["text"]).split("/var/")[0]), res["text"])
        log = self.log()
        self.assertEqual(i18n.server_lang(), "ru")                       # the import kept the language of the server
        self.assertIn("BACKUP import: перенесено — ", log)
        self.assertNotIn("BACKUP import: moved", log)

if __name__ == "__main__":
    unittest.main()

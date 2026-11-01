"""3.3.2 E2, group «rules»: the English of ``kaskad.rules`` (CLI text, errors, help, the panel's errors, the status
line of ``gw status``) and the Russian as before. Data (names of users and devices) are ASCII here, so no Cyrillic
may appear in English at all."""

import argparse
import re
import unittest
import urllib.error

from kaskad import errors
from kaskad.i18n import use_lang
from kaskad.panel.routes import HttpError
from kaskad.rules import attribution, cli as rcli, dat, lists, ops, source, store
from kaskad.rules import panel as rpanel
from tests.rules_helpers import seed_small
from tests.test_rules_gw import RulesGwCase

CYR = re.compile("[А-Яа-яЁё]")


def _doc(**kw):
    doc = {"default": "ru_direct", "default_applied": "all_exit", "block_ads": True, "block_torrent": False,
           "ru_direct": 1, "lists": {"label": "x", "fallback": True, "kernel": {"cidrs": 12345, "ranges": 678, "v6": 9},
                                     "geoip": {"tag": None, "source": "github", "sha256": "ab" * 32},
                                     "geosite": {"tag": "202610040942", "source": "jsdelivr"},
                                     "text": {"source": "runetfreedom", "license": "GPL-3.0"},
                                     "domains": {"keyword": 3, "regexp": 4}},
           "groups": {"direct": 10, "force": 20, "block": 30},
           "table": {"loaded": False, "wanted": 2, "state": {"ver": 2, "ok": False, "error": "nft: timeout",
                                                            "next": 1700000000}},
           "dns": {"nftset": False, "version": "2.89"},
           "devices": [{"id": "d-1", "name": "Phone", "applied": "ru_direct", "from": "user"},
                       {"id": "d-2", "name": "Laptop", "applied": "all_exit", "from": "default"}],
           "last": {"at": "2026-10-04T09:42:00Z", "ok": False, "error": "geoip — timeout"},
           "timer": {"enabled": True}, "load_timer": {"enabled": False}}
    doc.update(kw)
    doc["attribution"] = attribution()
    return doc


def _errors():
    """The texts of the errors of the rules modules, in the current language."""
    out = []
    for fn in (lambda: list(dat.entries(b"\x0a")),
               lambda: list(dat.entries(b"\x00")),
               lambda: list(dat.entries(b"\x0a\x05ab")),
               lambda: list(dat.fields(b"\x0b")),
               lambda: lists.parse_range("10.0.0.9-10.0.0.1"),
               lambda: source.parse_sha(b"not-a-sha"),
               lambda: ops.set_blocks(None),
               lambda: ops.set_profile(None, "group", "x", "ru_direct"),
               lambda: ops._check_geoip(b""),
               lambda: rpanel._bool({"force": "yes"}, "force")):
        try:
            fn()
        except (ValueError, errors.KaskadError, source.FetchError, HttpError) as e:
            out.append(str(getattr(e, "message", None) or e))
    out.append(source._reason(urllib.error.URLError(OSError("timed out"))))
    out.append(source._reason(urllib.error.URLError("refused")))
    return out


def _help():
    p = argparse.ArgumentParser(prog="kaskadctl")
    rcli.register(p.add_subparsers(dest="group"))
    sub = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)][0].choices["rules"]
    out = [p.format_help(), sub.format_help()]
    for a in sub._actions:
        if isinstance(a, argparse._SubParsersAction):
            out.extend(c.format_help() for c in a.choices.values())
    return "\n".join(out)




class EnglishTest(unittest.TestCase):
    def test_status_lines_and_summary(self):
        with use_lang("en"):
            lines = rcli.status_lines(_doc())
            line = ops.summary(_doc())
        text = "\n".join(lines)
        self.assertIsNone(CYR.search(text), text)
        self.assertIsNone(CYR.search(line), line)
        self.assertEqual(lines[0], "Routing rules “Russian sites direct” (RoscomVPN lists)")
        self.assertIn("  geoip: no tag, source github, sha256 abababababab…", lines)
        self.assertIn("Russian networks: 12,345 (678 ranges), IPv6 skipped: 9", lines)
        self.assertIn("default: Russian sites direct (no lists yet — everything through the exit)", lines)
        self.assertIn("block ads: yes; torrents: follow the route", lines)
        self.assertIn("  d-1 Phone                Russian sites direct (from the user)", lines)
        # M6 332c №11: дата — в общем формате с пометкой UTC, как в меню (год — только если не текущий)
        self.assertTrue(any(re.fullmatch(r"last update: Oct 4(, 2026)?, 09:42 UTC — error: geoip — timeout", l)
                            for l in lines), lines)
        with use_lang("ru"):
            ru = rcli.status_lines(_doc())
        self.assertTrue(any(re.fullmatch(r"последнее обновление: 04\.10(\.2026)?, 09:42 UTC — ошибка: .*", l)
                            for l in ru), ru)
        self.assertIn("timer kaskad-gw-rules-load.timer: off", lines)
        self.assertIn("NOT loaded (nft: timeout; again at ", line)
        self.assertIn("nftset: not available — domains are ignored", line)
        self.assertTrue(lines[-1].startswith("Lists: RoscomVPN"))

    def test_no_lists_and_label(self):
        with use_lang("en"):
            self.assertEqual(ops.summary({"lists": None}),
                             "no RoscomVPN lists — all traffic follows the device routes (kaskadctl rules update)")
            self.assertEqual(ops.label(None), "none")
            self.assertEqual(ops.table_text({"loaded": True}), "loaded")
            self.assertEqual(ops.table_text({}), "unknown")

    def test_errors(self):
        with use_lang("en"):
            texts = _errors()
        self.assertEqual(len(texts), 12, texts)
        for t in texts:
            self.assertIsNone(CYR.search(t), t)
            self.assertNotIn("rules.", t)                    # a key that is not in the catalog
        self.assertIn("Nothing to change: specify ads or torrents.", texts)
        self.assertIn("timeout", texts)
        self.assertIn("force — true or false", texts)

    def test_busy_store(self):
        import fcntl
        import os
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as d, mock.patch.object(store, "root", lambda: d):
            fd = os.open(os.path.join(d, store.LOCK), os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                with use_lang("en"), self.assertRaises(errors.LockedError) as cm:
                    with store.update_lock():
                        pass
            finally:
                os.close(fd)
        self.assertEqual(str(cm.exception), "The lists are already being updated — wait a minute.")

    def test_help(self):
        with use_lang("en"):
            text = _help()
        self.assertIsNone(CYR.search(text), text)
        self.assertIn("rules “Russian sites direct” (RoscomVPN lists)", text)
        self.assertIn("download the lists now", text)
        self.assertIn("COMMAND", text)


class RussianTest(unittest.TestCase):
    def test_as_before(self):
        with use_lang("ru"):
            lines = rcli.status_lines(_doc())
            line = ops.summary(_doc())
            texts = _errors()
            help_text = _help()
        self.assertEqual(lines[0], "Правила «Россия напрямую» (списки RoscomVPN)")
        self.assertIn("сети России: 12 345 (диапазонов 678), IPv6 пропущено: 9", lines)
        self.assertIn("по умолчанию: Россия напрямую (пока списков нет — всё через выход)", lines)
        self.assertIn("  d-2 Laptop               всё через выход (по умолчанию)", lines)
        self.assertIn("блокировать рекламу: да; торренты: по маршруту", lines)
        self.assertIn("таймер kaskad-gw-rules.timer: включён", lines)
        self.assertIn("nftset: нет — домены не учитываются", line)
        self.assertIn("по умолчанию: всё через выход", line)
        self.assertIn("Нечего менять: укажите рекламу или торренты.", texts)
        self.assertIn("таймаут", texts)
        self.assertIn("файл оборван после заголовка поля", texts)
        self.assertIn("правила «Россия напрямую» (списки RoscomVPN)", help_text)
        self.assertTrue(attribution().startswith("Списки: RoscomVPN (hydraponique"))


class CliEnglishTest(RulesGwCase):
    """The real ``kaskadctl rules`` under English: status, a change, a refused value."""

    def test_cli(self):
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Mom")
        self.ok("gw", "device", "add", "Mom", "Phone", "--access", "wg")
        seed_small()
        with use_lang("en"):
            rc, out, err = self.run_cli("rules", "status")
            self.assertEqual(rc, 0, err)
            rc2, out2, _ = self.run_cli("rules", "default", "all_exit")
            rc3, out3, err3 = self.run_cli("rules", "block", "ads", "maybe")
            rc4, out4, _ = self.run_cli("rules", "block", "torrent", "on")
        for t in (out, out2, err3, out4):
            self.assertIsNone(CYR.search(t), t)
        self.assertIn("Routing rules “Russian sites direct” (RoscomVPN lists)", out)
        self.assertIn("lists: of Oct 4", out)                           # fmt_datetime (T-M6-332b №7)
        self.assertEqual((rc2, out2.strip()), (0, "Default profile: everything through the exit."))
        self.assertEqual(rc3, 2)
        self.assertIn("the value is on or off, not 'maybe'", err3)
        self.assertEqual((rc4, out4.strip()), (0, "Torrents: blocked."))
        with use_lang("ru"):
            rc, out, _ = self.run_cli("rules", "block", "torrent", "off")
        self.assertEqual(out.strip(), "Торренты: идут по маршруту.")



from tests.test_rules_store import RulesCase as RootCase  # noqa: E402 — small LIMITS of the fake lists
from tests.rules_helpers import FakeHttp, geoip_bytes, release, seed, sha_text, geosite_bytes  # noqa: E402

CYR_ = re.compile("[А-Яа-яЁё]")


class StatusCodesTest(RootCase):
    """status.json keeps the error and the warnings of the last update as codes + arguments: a reader in another
    language than the update's sees them in its own (rules status, the panel, the bot)."""

    QUIET = {"log": lambda text: None, "apply_live": False}

    def test_error_said_in_the_readers_language(self):
        with use_lang("ru"), self.assertRaises(errors.KaskadError) as cm:
            ops.update(http=FakeHttp(), **self.QUIET)
        self.assertIn("не скачались", str(cm.exception))
        st = store.load_status()
        self.assertIn("не скачались", st["error"])                         # the text of the moment: old readers
        self.assertEqual(st["error_msg"]["code"], "rules.update.none")
        self.assertNotIn("error_msg", ops.last_status())
        with use_lang("en"):
            last = ops.last_status()
            self.assertIsNone(CYR_.search(last["error"]), last["error"])
            self.assertIn("HTTP 404", last["error"])
            self.assertEqual(ops.status()["last"]["error"], last["error"])       # what rules status, panel, bot show
        with use_lang("ru"):
            self.assertEqual(ops.last_status()["error"], st["error"])

    def test_warnings_said_in_the_readers_language(self):
        seed()
        bad = geoip_bytes(1200)[:-5]                                        # cut short: does not decode
        http = release(geoip=bad)
        http.files[source.JSD_GEOIP] = bad
        http.files[source.JSD_GEOIP + ".sha256"] = sha_text(bad)
        http.files[source.GITHUB_GEOSITE + ".sha256"] = sha_text(geosite_bytes(), "release/geosite.dat")
        with use_lang("ru"):
            res = ops.update(http=http, **self.QUIET)
        self.assertTrue(any("не разобран" in w for w in res["warnings"]), res)
        with use_lang("en"):
            w = ops.last_status()["warnings"]
            self.assertTrue(w and all(CYR_.search(x) is None for x in w), w)
            self.assertTrue(any("could not be parsed" in x for x in w), w)

    def test_an_old_status_without_codes_as_it_is(self):
        store.save_status({"at": "2026-10-01T00:00:00Z", "ok": False, "changed": False, "error": "старый текст"})
        with use_lang("en"):
            self.assertEqual(ops.last_status()["error"], "старый текст")

if __name__ == "__main__":
    unittest.main()

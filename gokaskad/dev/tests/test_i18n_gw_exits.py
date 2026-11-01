"""3.3.2 E1-exits: English of gw/exits.py, gw/exitsubs.py, gw/subfetch.py, gw/xray.py — the same paths say it in the
language of the context, Russian stays as it was; the state of an exit on disk keeps its code (a record of an earlier
version — Russian words only — is shown in the language of the reader); the journal speaks the language of the
server."""

import json
import os
import re
import unittest

from kaskad.errors import KaskadError
from kaskad.gw import exits, exitsubs, subfetch, xray
from kaskad.i18n import check, use_lang
from tests.fakes_gw import VLESS_A, VLESS_B, awg_line, install_parser, write_pool
from tests.gwcase import GwCase
from tests.test_gw_exitsubs import IP_URL, SubsCase, b64

CYR = re.compile("[А-Яа-яЁё]")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = [os.path.join(ROOT, "lib", "kaskad", "gw", n) for n in ("exits.py", "exitsubs.py", "subfetch.py", "xray.py")]
AREAS = ["gw_exits", "gw_exitsubs", "gw_subfetch", "gw_xray"]


class CatalogTest(unittest.TestCase):
    def test_catalogs_complete_and_no_russian_in_code(self):
        check.assert_catalog_complete(self, AREAS)
        check.assert_no_cyrillic(self, *FILES)


class ExitsEnglishTest(GwCase):
    def no_cyr(self, text):
        self.assertIsNone(CYR.search(str(text or "")), text)

    def setUp(self):
        super().setUp()
        install_parser()
        self.awg2 = awg_line(2)
        self.awg3 = awg_line(3)
        write_pool([VLESS_A, self.awg2, self.awg3, "garbage-line"])
        self.pool = exits.pool(self.w.runner)
        self.vid, self.a2, self.a3, self.bad = [e["id"] for e in self.pool]

    def test_problems_and_states_in_english(self):
        with use_lang("en"):
            pool = exits.pool(self.w.runner)
            self.assertEqual(pool[3]["problem"], "unknown key format")
            self.assertEqual(pool[3]["problem_code"], "problem.unknown_format")
            t = exits.state_text
            self.assertEqual(t({"state": "alive", "usable": True, "detail": "отвечает", "handshake": 40}),
                             "working, handshake 40 s ago")
            self.assertEqual(t({"state": "alive", "usable": True, "detail": "нет ответа (сбой 2 из 3)", "silent": 15,
                                "fails": 2, "dies_after": 5}), "working, but no answer for 15 s (failed checks: 2 of 5 "
                                                               "in 2 min)")
            self.assertEqual(t({"state": "dead", "detail": "нет ответа", "silent": 35, "others": 2}),
                             "down: no answer for 35 s — clients are moving to another exit")
            self.assertEqual(t({"state": "dead", "detail": "нет интерфейса kawg2", "others": 0}),
                             "down: no interface kawg2 — no other working exits")
            self.assertEqual(t({"state": "unknown", "detail": exits.NOT_CHECKED}), "not checked yet")
            self.assertEqual(t({"state": "unknown", "gw_off": True}), "turn on the gateway to check exits")
            self.assertEqual(exits.ago_text(3725), "1 h 02 min")
            hint = exits.add_hint({})
            self.assertEqual(hint["text"], "Add a VPN key: in the web panel — “Exits” → “Add key”, in the SSH menu — "
                                           "gokaskad → 8 → 7 → 2 (“Add key”).")
            self.no_cyr(exits.NO_EXITS_OFFLINE + exits.NO_DATA + exits.SUBSCRIPTION_TEXT)
            with self.assertRaises(KaskadError) as cm:
                exits.preview(exits.load_parser(self.w.runner), "hello")
            self.assertEqual(str(cm.exception), exits.NO_KEYS_TEXT)
            self.no_cyr(str(cm.exception))
            self.assertEqual(exits.target({}, {"id": "d-1"}, [], set())["why"].split(" — ")[0],
                             "no live exits in the pool")
        with use_lang("ru"):
            self.assertTrue(exits.NO_EXITS_OFFLINE.startswith("Устройства шлюза сейчас без интернета"))
            self.assertEqual(exits.state_text({"state": "dead", "detail": "no answer", "silent": 35}),
                             "не работает: нет ответа 35 с")          # English words of a record: in Russian too

    def test_the_snapshot_keeps_the_code_and_the_reader_its_language(self):
        ids = [e["id"] for e in self.pool]
        self.w.leg(2)
        snap = exits.load_snapshot()
        for t in (0, 10, 20, 30):                    # kawg3 has no interface: dead after 4 failed checks (3.3.5)
            snap, _, _ = exits.merge(snap, exits.probe(self.w.runner, self.pool), ids, now=self.now + t)
        self.now += 30
        rec = snap["exits"][self.a3]
        self.assertEqual((rec["detail"], rec["detail_code"], rec["detail_args"]),
                         ("нет интерфейса kawg3", "detail.no_iface", {"iface": "kawg3"}))
        self.assertEqual(snap["exits"][self.bad]["detail_code"], "problem.unknown_format")
        exits.save_snapshot(snap)
        with use_lang("en"):
            e3 = next(e for e in exits.pool(self.w.runner) if e["id"] == self.a3)
            row = exits.public(e3, {}, exits.load_snapshot())
            self.assertEqual((row["detail"], row["detail_code"], row["state"]),
                             ("no interface kawg3", "detail.no_iface", "dead"))
            self.assertEqual(exits.state_text(row), "down: no interface kawg3 (for 30 s now)")
        self.w.kill_leg(2)                           # a working leg fails once: «(не прошло 1 из 5…)» — by code
        with use_lang("en"):
            snap2, _, _ = exits.merge(snap, exits.probe(self.w.runner, self.pool), ids, now=self.now + 10)
        rec = snap2["exits"][self.a2]
        self.assertEqual((rec["detail_code"], rec["detail_window"]), ("detail.no_iface", [1, 5]))
        self.assertEqual(exits.detail_text(rec), "нет интерфейса kawg2 (не прошло проверок: 1 из 5 за 2 мин)")
        with use_lang("en"):
            self.assertEqual(exits.detail_text(rec), "no interface kawg2 (failed checks: 1 of 5 in 2 min)")

    def test_a_record_of_an_earlier_version_is_read_in_english(self):
        snap = {"at": 1, "pool": [self.a3], "exits": {self.a3: {
            "alive": False, "fails": 3, "since": 1, "detail": "xray шлюза не отвечает (65 с) (сбой 1 из 2)"}}}
        e3 = next(e for e in self.pool if e["id"] == self.a3)
        with use_lang("en"):
            row = exits.public(e3, {}, snap)
            # «(сбой 1 из 2)» of 3.3.4 — failures in a row of the needed ones: told in its own words (REV-335 №5)
            self.assertEqual(row["detail"], "the gateway’s xray isn’t answering (65 s) (failure 1 of 2)")
            self.assertEqual(row["detail_code"], "detail.xray_silent")
            self.assertEqual(exits.why_text(row["detail"], 70), "the gateway’s xray isn’t answering (65 s) (for "
                                                                "70 s now)")
        self.assertEqual(exits.why_text("нет ответа (65 с) (сбой 1 из 2)", 70), "нет ответа 70 с (65 с)")
        with use_lang("en"):
            self.assertEqual(exits.why_text("нет ответа (65 с) (сбой 1 из 2)", 70), "no answer for 70 s (65 s)")

    def test_the_journal_of_the_legs_speaks_the_language_of_the_server(self):
        with use_lang("en"):
            res = exits.ensure_legs(self.w.runner, self.pool)
        self.assertTrue(res["ok"])
        self.assertIn("GW legs: ноги AmneziaWG в порядке, работающие не тронуты (kawg2 — не было интерфейса; kawg3 — не "
                      "было интерфейса)", self.read("/var/log/kaskad.log"))

    def test_xray_words_in_english(self):
        with use_lang("en"):
            self.assertTrue(xray.CUT_NOTE.startswith("VLESS will reconnect for everyone"))
            whats = [w for _, _, w in xray.internal_ports({}, [])]
            self.assertIn("pool TPROXY", whats)
            self.assertIn("exit metrics", whats)
            self.no_cyr(" ".join(whats))
            self.assertEqual(xray._words(("gw_xray.why.code", {"rc": 7}, None)), "code 7")
        self.assertTrue(xray.CUT_NOTE.startswith("VLESS переподключится у всех на пару секунд"))


class SubsEnglishTest(SubsCase):
    def test_add_refresh_and_failures_in_english(self):
        with use_lang("en"):
            res = self.add(IP_URL, b64([VLESS_A, VLESS_B]))
            self.assertEqual(res["text"], "Subscription added: subscription 1. Subscription 1: added 2, removed 0 (keys "
                                          "in the subscription: 2).")
            row = exitsubs.listing()[0]
            self.assertEqual((row["name"], row["state"], row["text"]),
                             ("subscription 1", "ok", "refreshed 1 min ago: 2 keys"))
            self.feed.bodies[IP_URL] = ""
            self.now += 60
            res = self.refresh()
            text = res["results"][0]["text"]
            self.assertEqual(text, "Subscription 1 wasn’t refreshed: The subscription is empty — it has no keys. Pool "
                                   "keys left unchanged.")
            self.assertEqual(text.count("left unchanged"), 1)          # said once (a code, not the words)
            self.feed.bodies[IP_URL] = KaskadError("x")
            res = self.refresh()
            self.assertTrue(res["results"][0]["text"].endswith("x. Pool keys left unchanged."))
            self.assertEqual(exitsubs.listing()[0]["state"], "error")
            with self.assertRaises(KaskadError) as cm:
                exitsubs.normalize("ftp://x")
            self.assertEqual(str(cm.exception), "A subscription is an http:// or https:// link with a server address.")
            out = exitsubs.delete(self.w.runner, "1")
            self.assertEqual(out["text"], "Subscription “subscription 1” deleted; keys removed: 2.")
        self.assertEqual(exitsubs.EMPTY_TEXT, "Подписка пуста — ключей в ней нет. Ключи пула не тронуты.")
        log = self.journal()
        self.assertIn("GW exit-sub add ", log)
        self.assertIn("(раз в сутки): ключей 2, добавлено 2", log)          # the server's language, not the request's
        self.assertIn("убрано ключей 2", log)
        self.assertIsNone(re.search(r"keys removed|daily\)", log), log)

    def test_russian_stays(self):
        res = self.add(IP_URL, b64([VLESS_A]))
        self.assertEqual(exitsubs.listing()[0]["text"], "обновлена 1 мин назад: ключей 1")
        self.assertIn("Подписка добавлена: подписка 1.", res["text"])
        self.now += 3 * 86400
        self.assertTrue(exitsubs.state_text(exitsubs.load()["subs"][0]).startswith("обновлена 3 дня назад"))
        with use_lang("en"):
            self.assertTrue(exitsubs.state_text(exitsubs.load()["subs"][0]).startswith("refreshed 3 days ago"))


class SubfetchEnglishTest(unittest.TestCase):
    def test_refusals_in_english(self):
        with use_lang("en"):
            for url, words in (("ftp://example.org/x", "Subscriptions only by an http:// or https:// link."),
                               ("http://u:p@example.org/x", "Subscription links with a login and password"),
                               ("http://10.0.0.1/x", "The subscription link points to an internal address (10.0.0.1)")):
                with self.assertRaises(KaskadError) as cm:
                    subfetch.check_url(url, resolve=lambda h: ["10.0.0.1"])
                self.assertTrue(str(cm.exception).startswith(words), str(cm.exception))
                self.assertIsNone(CYR.search(str(cm.exception)))
            self.assertEqual(str(subfetch.cert_name_error(["*.example.org"], "203.0.113.5", resolve=lambda h: [])),
                             "The certificate is issued for the domain example.org — add the subscription by the "
                             "domain or over http.")
        with self.assertRaises(KaskadError) as cm:
            subfetch.check_url("ftp://example.org/x")
        self.assertEqual(str(cm.exception), "Подписка — только по ссылке http:// или https://.")


if __name__ == "__main__":
    unittest.main()

"""3.3.2 E1-in: English of the gateway's inbound adapters (lib/kaskad/gw/inbounds/**) — the same paths say it in the
language of the context; the journal speaks the language of the server; constants other modules read stay lazy."""

import json
import os
import re
import unittest
from unittest import mock

from kaskad.i18n import check, use_lang
from tests.gw.tunnel_case import GwCase, device, gw_state
from tests.serverlang import server

CYR = re.compile("[А-Яа-яЁё]")
LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "gw", "inbounds")
AREAS = ["gw_in", "gw_in_accel", "gw_in_awg", "gw_in_ikev2", "gw_in_ipsec", "gw_in_l2tp", "gw_in_ocserv",
         "gw_in_openvpn", "gw_in_pki", "gw_in_pptp", "gw_in_socks5", "gw_in_sstp", "gw_in_vless", "gw_in_wg"]


def _texts(rec):
    """Every word a person sees in an issue() result (titles, notes, field labels) — not the configs."""
    out = []
    for r in rec["items"]:
        out.append(r["title"])
        out.extend(r.get("notes") or [])
        if r.get("format") == "fields":
            out.extend(row[0] for row in r["body"])
    return out


class CatalogTest(unittest.TestCase):
    def test_no_russian_left_in_code(self):
        check.assert_no_cyrillic(self, LIB)

    def test_catalogs_complete(self):
        check.assert_catalog_complete(self, AREAS)


class LazyConstantsTest(unittest.TestCase):
    """Constants other modules (and older tests) read keep working — in the language of the moment of the read."""

    def test_module_constants(self):
        from kaskad.gw.inbounds import ipsec, l2tp, openvpn, pptp, sstp, tunnel, vless_lists
        with use_lang("ru"):
            self.assertIn("только с основного адреса", openvpn.MH_WARNING)
            self.assertEqual(vless_lists.TITLES["ru"], "российские сайты")
            self.assertEqual(tunnel.RESTART_WHY["unit"], "новая версия службы входа")
            self.assertTrue(pptp.PS_NOTE.startswith("Сохраните файл"))
        with use_lang("en"):
            for text in (openvpn.MH_WARNING, ipsec.LEGACY_CA, ipsec.NO_HOST, l2tp.PLAIN_WARNING, pptp.WEAK,
                         sstp.REVOCATION_NOTE, sstp.DOMAIN_NOTE % "vpn.example.com", pptp.PS_NOTE % "x.ps1",
                         vless_lists.TITLES["int"], tunnel.RESTART_WHY.get("engine")):
                self.assertIsNone(CYR.search(text), text)
            self.assertEqual(vless_lists.TITLES["ru"], "Russian sites")
            self.assertEqual(dict(tunnel.RESTART_WHY)["mtu"], "MTU")
            self.assertIsNone(tunnel.RESTART_WHY.get("nope"))
        with self.assertRaises(AttributeError):
            pptp.NOT_A_TEXT                                       # noqa: B018 — the module __getattr__ refuses

    def test_restart_text(self):
        from kaskad.gw.inbounds import tunnel
        rec = {"at": 1791400000, "why": "mtu", "mtu_was": 1216, "mtu_now": 1184, "kind": "awg2"}
        with use_lang("en"):
            t = tunnel.restart_text(rec)
            self.assertIn("awg2 — ", t)
            self.assertIn(", reason: MTU 1216 → 1184", t)
            two = dict(rec, group=[rec, dict(rec, kind="awg3", why="engine")])
            self.assertIn("reasons: MTU 1216 → 1184 (awg2); AmneziaWG engine switch", tunnel.restart_text(two))
        with use_lang("ru"):
            self.assertIn(", причина: MTU 1216 → 1184", tunnel.restart_text(rec))

    def test_ppp_texts(self):
        from kaskad.gw.inbounds import accel
        with use_lang("en"):
            t = accel.kernel_refusal(["l2tp"], ["l2tp_ppp"])
            self.assertTrue(t.startswith("This server’s kernel is built without PPP (missing modules: l2tp_ppp)"), t)
            self.assertIsNone(CYR.search(t), t)


class IssueEnglishTest(GwCase):
    """issue() of real adapters: what the person reads comes out in English, the configs are untouched."""

    def test_socks5(self):
        st = gw_state({"socks5": {"enabled": True, "port": 1080, "udp": True}},
                      [device("d-pc", "Компьютер", "windows", ["socks5"], kind="pc")])
        s5 = self.adapter("socks5")
        with use_lang("en"):
            self.assertEqual(s5.prepare(st, self.sec), ["SOCKS5 password of d-pc"])
            rec = s5.issue(st, self.sec, "d-pc")
            words = _texts(rec)
            self.assertIn("SOCKS5 — link", words)
            self.assertIn("SOCKS5 — connection details", words)
            self.assertIn("UDP through the proxy: yes.", words)
            self.assertEqual([row[0] for row in rec["items"][-1]["body"]], ["Server", "Port", "Login", "Password"])
            for w in words:
                self.assertIsNone(CYR.search(w), w)
            st["gateway"]["devices"][0]["access"] = []
            with self.assertRaises(self.gw.base.InboundError) as c:
                s5.issue(st, self.sec, "d-pc")
            self.assertEqual(str(c.exception), "Device Компьютер doesn’t use the SOCKS5 protocol, is turned off, or has "
                                               "no password yet (kaskadctl gw apply).")
        with use_lang("ru"):
            st["gateway"]["devices"][0]["access"] = ["socks5"]
            self.assertIn("SOCKS5 — данные для подключения", _texts(s5.issue(st, self.sec, "d-pc")))

    def test_wg(self):
        st = gw_state({"wg": {"enabled": True, "port": 51820}}, [device("d-ph", "Телефон", "ios", ["wg"])])
        wg = self.adapter("wg")
        with use_lang("en"):
            done = wg.prepare(st, self.sec)
            self.assertEqual(done, ["server key", "device d-ph"])
            rec = wg.issue(st, self.sec, "d-ph")
            words = _texts(rec)
            self.assertEqual(words[:3], ["WireGuard — QR code", "For the WireGuard app on a phone.",
                                         "WireGuard — .conf file"])
            self.assertIn("WireGuard app: “+” → “Create from QR code”.", words)
            self.assertIn("[Interface]", rec["items"][1]["body"])           # the config itself: no words to translate
            with self.assertRaises(self.gw.base.InboundError) as c:
                wg.issue(gw_state({}, []), self.sec, "d-ph")
            self.assertEqual(str(c.exception), "Device d-ph not found.")


class JournalLanguageTest(GwCase):
    """The journal (/var/log/kaskad.log) speaks the language of the server, not of the request."""

    def test_restart_line_in_the_server_language(self):
        tunnel = self.gw.tunnel
        with server("ru"), use_lang("en"):
            tunnel.note_restart("awg2", "mtu", 1216, 1184, by="kaskadctl gw apply")
        with server("en"), use_lang("ru"):
            tunnel.note_restart("awg3", "engine", by="kaskadctl gw apply")
        log = self.log_text()
        self.assertIn("gw awg2: перезапуск входа, причина: MTU 1216 → 1184; кто: kaskadctl gw apply", log)
        self.assertIn("gw awg3: inbound restarted, reason: AmneziaWG engine switch (kernel module / userspace); by: kaskadctl "
                      "gw apply", log)
        with open(tunnel.restarts_path(), encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(doc["awg2"]["why"], "mtu")                          # on disk: the code, words at the show

    def test_apply_line_in_the_server_language(self):
        st = gw_state({"socks5": {"enabled": True, "port": 1080, "udp": True}},
                      [device("d-pc", "Компьютер", "windows", ["socks5"], kind="pc")])
        s5 = self.adapter("socks5")
        with server("en"), use_lang("ru"):
            s5.install(self.runner)
            s5.apply(st, self.sec, self.runner)
        log = self.log_text()
        self.assertIn("gw socks5: installed (an inbound of the gateway’s xray)", log)
        self.assertIn("gw socks5: applied; accounts: 1; created: ", log)


if __name__ == "__main__":
    unittest.main()

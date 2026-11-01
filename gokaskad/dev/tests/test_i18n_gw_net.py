"""3.3.2 E1-net: the English of the gateway's «where does it go» (gw/trace.py), the network part (gw/net.py), live.json
(gw/live.py), the DNS of the gateway (gw/dns.py), the interface watch (gw/ifwatch.py) and the collector
(gw/collect.py) — and the Russian as before. Journal lines speak the language of the server, whatever the language
of the request; live.json keeps how to say its texts again, so a reader in another language gets its own."""

import os
import re
import unittest
from unittest import mock

import json

from kaskad.errors import KaskadError
from kaskad.gw import collect, dns, live, net, ops, trace
from kaskad.i18n import check, use_lang
from kaskad.sysrun import FakeRunner
from tests.gw.test_trace import TraceCase
from tests.panel.panelcase import PanelCase
from tests.test_qs330b_reboot import IFACE, T0, Base as IfwatchBase
from tests.serverlang import server

CYR = re.compile("[А-Яа-яЁё]")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ["trace", "net", "live", "dns", "ifwatch", "collect"]
AREAS = ["gw_" + f for f in FILES]


def server_en():
    """The language of the server is English (env KASKAD_LANG, as the units of the node get it, and the saved one: the journal)."""
    return server("en")


def server_ru():
    return server("ru")


class StaticTest(unittest.TestCase):
    def test_no_russian_left_in_the_code(self):
        check.assert_no_cyrillic(self, *[os.path.join(ROOT, "lib", "kaskad", "gw", f + ".py") for f in FILES])

    def test_catalogs_complete(self):
        check.assert_catalog_complete(self, AREAS)


# ── trace ──────────────────────────────────────────────────────────────────

class TraceEnglishTest(TraceCase):
    def ascii_world(self, **kw):
        """The world of test_trace with ASCII names: no Cyrillic may come from the data."""
        st = self.world(**kw)
        g = st["gateway"]
        names = {"u-00000001": "Mom", "u-00000002": "Dacha"}
        for u in g["users"]:
            u["name"] = names[u["id"]]
        for d, n in zip(g["devices"], ("Phone", "Router", "Laptop")):
            d["name"] = n
        for e in (g.get("exits") or {}).values():
            e["name"] = "Vienna"
        return st

    def en(self, st, who, target, **kw):
        with use_lang("en"):
            res = self.run_trace(st, who, target, **kw)
            return res, "\n".join(trace.lines(res))

    def test_block_direct_and_route_in_english(self):
        st = self.ascii_world()
        res, text = self.en(st, "d-00000001", "ad.mail.ru")
        self.assertEqual(res["summary"], "Blocked: ads and telemetry")
        self.assertIn("Result: Blocked: ads and telemetry", text)
        self.assertIn("Device “Phone” (Mom), profile “Russian sites direct” (from the user)", text)
        self.assertIn("blocked for every device, whatever the profile", text)
        self.assertNotRegex(text.replace("Тестовый вход wg", "wg"), CYR)          # the fake adapter's title
        res, text = self.en(st, "d-00000001", "ru-hosted.example")
        self.assertEqual(res["verdict"], "direct")
        self.assertIn("Direct from server address 203.0.113.7", text)
        self.assertIn("in Russian networks", text)
        self.assertNotRegex(text.replace("Тестовый вход wg", "wg"), CYR)          # the fake adapter's title
        res, text = self.en(st, "d-00000002", "example.com")             # Dacha: route to Amsterdam, fail «block»
        self.assertIn("Route: exit “Amsterdam” (from the user)", text)
        self.assertIn("on exit failure — block", text)

    def test_the_same_answer_in_russian(self):
        st = self.world()
        with use_lang("ru"):
            res = self.run_trace(st, "d-00000001", "ad.mail.ru")
        self.assertEqual(res["summary"], "Заблокировано: реклама и телеметрия")
        self.assertEqual(trace.lines(res)[0], "Итог: Заблокировано: реклама и телеметрия")

    def test_errors_in_english(self):
        st = self.ascii_world()
        with use_lang("en"):
            for bad in ("", "x" * 3000, "::1", "999.1.1.1", "a b"):
                with self.assertRaises(KaskadError) as e:
                    trace.parse_target(bad)
                self.assertNotRegex(str(e.exception), CYR, bad)
            with self.assertRaises(KaskadError) as e:
                trace.trace(st, "nobody", "example.com")
            self.assertEqual(str(e.exception), "No device or user “nobody” (or the name is ambiguous — give the id "
                                               "d-… or u-…).")
            with self.assertRaises(KaskadError) as e:
                trace.trace(st, "-", "example.com", inbound="wg")
            self.assertIn("a new device has no protocols yet", str(e.exception))

    def test_constants_follow_the_language(self):
        with use_lang("en"):
            self.assertTrue(trace.VLESS_PING_NOTE.startswith("Ping (ICMP) does not pass"))
            self.assertEqual(trace.PROFILE_TITLES["all_exit"], "Everything through an exit")
            self.assertEqual(trace.KERNEL_HINT, "WireGuard, AmneziaWG, L2TP, IKEv2, OpenVPN and others")
        with use_lang("ru"):
            self.assertEqual(trace.PROFILE_TITLES["ru_direct"], "Россия напрямую")
        with self.assertRaises(AttributeError):
            trace.NO_SUCH_THING                                           # noqa: B018

    def test_resolver_cache_keeps_codes_not_words(self):
        def none(name):
            import socket
            raise socket.gaierror(getattr(socket, "EAI_NONAME", -2), "no")
        with mock.patch.object(trace, "_getaddrinfo", none):
            with use_lang("ru"):
                self.assertEqual(trace.resolve("gone.example"), ([], "такого домена нет"))
            with use_lang("en"):                                          # the cached answer, in English
                self.assertEqual(trace.resolve("gone.example"), ([], "no such domain"))


# ── live.json ──────────────────────────────────────────────────────────────

class LiveCase(PanelCase):
    """The world of test_panel_live (a gateway, one device on wg) — without its tests."""

    def setUp(self):
        super().setUp()
        self.gateway()
        self.did = ops.device_add(self.w.runner, self.uid, "Laptop", kind="laptop", platform="linux",
                                  access=["wg"])["device"]["id"]

    def doc(self):
        return json.loads(self.read("/run/kaskad/live.json"))


class LiveEnglishTest(LiveCase):
    def test_events_and_problems_said_again_in_the_language_of_the_reader(self):
        with server_ru():
            self.collect()
            self.ads["wg"].running = False
            self.collect()
        raw = self.doc()
        ev = raw["events"][-1]
        self.assertEqual((ev["kind"], ev["key"], ev["args"]), ("inbound_down", "gw_live.inbound_down",
                                                               {"title": ev["args"]["title"]}))
        self.assertTrue(ev["text"].startswith("Протокол "))                   # the server's language for old readers
        self.assertEqual(raw["i18n"]["lang"], "ru")
        title = ev["args"]["title"]
        with use_lang("en"):
            self.assertEqual(live.event_text(ev), "Protocol %s is down" % title)      # what the bot renders
            doc, _ = live.read()
            self.assertEqual(doc["events"][-1]["text"], "Protocol %s is down" % title)
            self.assertIn("Protocol %s is down" % title, doc["gateway"]["problems"])
            self.assertIn({"kind": "gw_inbound", "detail": "Protocol %s is down" % title}, doc["gateway"]["issues"])
            self.assertEqual(live.NO_POOL_TEXT, "No exit in the pool is working: clients routed through exits are "
                                                "blocked")
        with use_lang("ru"):
            doc, _ = live.read()
            self.assertEqual(doc["events"][-1]["text"], ev["text"])        # the same object: nothing to say again
            self.assertIn("Протокол %s не работает" % title, doc["gateway"]["problems"])

    def test_down_reason_in_english(self):
        r = FakeRunner()
        r.on(["systemctl", "show"], lambda argv, inp: (0, "Id=b.service\nLoadState=loaded\nActiveState=activating\n"
                                                         "SubState=auto-restart\nResult=exit-code\nExecMainStatus=2\n",
                                                         ""))
        with use_lang("en"):
            self.assertEqual(live.down_reason(r, ["b.service"]),
                             "service b.service exited with an error (code 2) — systemd is restarting it")
        m = live.down_reason_msg(r, ["b.service"])
        self.assertEqual(live.render(m, "ru"), "служба b.service завершилась с ошибкой (код 2) — systemd перезапускает её")

    def test_events_of_the_old_language_after_the_server_switched(self):
        # живая проверка 3.3.3, находка C: события, записанные по-русски, после смены языка сервера на английский —
        # лента «Overview» и тосты панели (alert SSE) показывали их по-русски: сборщик переносит события как есть,
        # а файл уже помечен lang=en, и читатель не пересказывал их
        with server_ru():
            self.collect()
            self.ads["wg"].running = False
            self.collect()
            self.ads["wg"].running = True
            self.collect()
        title = self.doc()["events"][-1]["args"]["title"]
        with server_en():
            self.collect()
            raw = self.doc()
            self.assertEqual(raw["i18n"]["lang"], "en")
            self.assertEqual([e["text"] for e in raw["events"][-2:]],
                             ["Protocol %s is down" % title, "Protocol %s is working again" % title])
            doc, _ = live.read()                                       # what the panel and its SSE alerts give
            self.assertFalse([e["text"] for e in doc["events"] if CYR.search(e["text"].replace(title, ""))])
        # файл, записанный сборщиком до правки: lang=en, а тексты событий — по-русски
        stale = {"v": live.V, "ts": 1, "events": [{"id": 1, "ts": 1, "kind": "inbound_down", "key": "gw_live.inbound_down",
                                                   "args": {"title": "L2TP/IPsec"},
                                                   "text": "Протокол L2TP/IPsec не работает"}],
                 "i18n": {"lang": "en", "problems": [], "why": {}}}
        out = live.localize(stale, "en")
        self.assertEqual(out["events"][0]["text"], "Protocol L2TP/IPsec is down")
        self.assertEqual(stale["events"][0]["text"], "Протокол L2TP/IPsec не работает")   # the shared doc is not changed
        self.assertIs(live.localize(out, "en"), out)                   # nothing to say again: the same object

    def test_old_file_without_keys_is_read_as_is(self):
        doc = {"v": 1, "ts": 1, "events": [{"id": 1, "ts": 1, "kind": "exit_dead", "text": "Выход «X» не работает"}]}
        with use_lang("en"):
            self.assertIs(live.localize(doc), doc)
            self.assertEqual(live.event_text(doc["events"][0]), "Выход «X» не работает")


# ── journal lines: the language of the server ─────────────────────────────

class CollectJournalTest(unittest.TestCase):
    def test_journal_lines_speak_the_server_language(self):
        lines = []
        with mock.patch.object(net, "flush_conntrack", lambda runner, st: 3):
            with server_ru(), use_lang("en"):                             # an English request on a Russian server
                collect._forget_connections({"gateway": {}}, None, lines.append)
            with server_en(), use_lang("ru"):
                collect._forget_connections({"gateway": {}}, None, lines.append)
        self.assertTrue(lines[0].startswith("GW repair: сброшены соединения клиентов"), lines[0])
        self.assertTrue(lines[1].startswith("GW repair: client connections of"), lines[1])
        with use_lang("en"):
            self.assertEqual([collect.mark_name(m) for m in (0x200, 0x201, None)],
                             ["VLESS pool (xray)", "direct", "none"])


class IfwatchJournalTest(IfwatchBase):
    def recreated(self):
        self.uptime(12)
        self.start(9)
        self.iface(7)
        self.observe(T0)
        self.uptime(42)
        self.start(31)
        self.iface(12)
        return self.observe(T0 + 30)

    def test_lines_in_english_on_an_english_server(self):
        with server_en(), use_lang("ru"):
            ln, = self.recreated()
        self.assertIn("GW inbound awg3 (%s): interface recreated 42 s after boot (ifindex 7 → 12)" % IFACE, ln)
        self.assertIn("AmneziaWG clients reconnect on their own in 15–25 s", ln)
        self.assertNotRegex(ln, CYR)

    def test_lines_in_russian_on_a_russian_server(self):
        with server_ru(), use_lang("en"):
            ln, = self.recreated()
        self.assertIn("интерфейс пересоздан через 42 с после загрузки", ln)


# ── the network part ───────────────────────────────────────────────────────

class NetEnglishTest(unittest.TestCase):
    def test_errors_and_problems(self):
        with use_lang("en"):
            for bad in ("10.0.0.1", "not a host!"):
                with self.assertRaises(KaskadError) as e:
                    net.parse_host(bad)
                self.assertNotRegex(str(e.exception), CYR)
            self.assertEqual(net.verify({}, None), ["table ip kaskad is missing"])
            h = {"id": "xray", "decision": {"table": "51820", "dev": "wg0"},
                 "culprits": [{"pref": 3999, "text": "not from all fwmark 0xca6c lookup 51820"}]}
            self.assertEqual(net.hijack_text(h), "a foreign rule “3999: not from all fwmark 0xca6c lookup 51820” "
                                                 "stands above the Kaskad rules: packets of the path through xray "
                                                 "(mark 0x200) go to table 51820 (dev wg0)")
            self.assertEqual(net._class_text("leg:3"), "packets of AmneziaWG leg kawg3 (mark 0x103)")
        with use_lang("ru"):
            self.assertTrue(net.hijack_text(h).startswith("чужое правило «3999: not from all"))
            self.assertEqual(net.verify({}, None), ["таблицы ip kaskad нет"])

    def test_the_sysctl_file_does_not_follow_the_language(self):
        self.assertTrue(net.RPF_TEXT.startswith("# Kaskad 3, шлюз:"))


# ── DNS of the gateway ─────────────────────────────────────────────────────

class DnsEnglishTest(unittest.TestCase):
    def test_texts(self):
        with use_lang("en"):
            self.assertEqual(dns._stale_detail(["config", "unit"]),
                             "Gateway DNS is in its old form (config and service): client queries go to port 5354, "
                             "but dnsmasq may listen on 53")
            self.assertEqual(dns._holders_text([{"procs": [], "addr": "", "port": 5354, "proto": "udp"}]),
                             "unknown process (*:5354/udp)")
        with use_lang("ru"):
            self.assertTrue(dns._stale_detail(["config"]).startswith("DNS шлюза в прежнем виде (конфиг)"))

    def test_files_on_disk_do_not_follow_the_language(self):
        with use_lang("en"):
            en = (dns.unit_text("/usr/sbin/dnsmasq"), dns.conf_text({}, True))
        with use_lang("ru"):
            ru = (dns.unit_text("/usr/sbin/dnsmasq"), dns.conf_text({}, True))
        self.assertEqual(en, ru)
        self.assertEqual(collect.unit_text(), collect.unit_text())


class ExitEventDetailTest(unittest.TestCase):
    """E1 при сведении с exits: «выход не работает: <причина>» — причина кодом выхода (detail_code), пересказ на языке
    читателя; читатель, который зовёт только tr(key, **args) (бот до 3.3.2), получает слова сборщика."""

    def test_detail_said_again_in_the_reader_language(self):
        from kaskad.gw import exits, live
        from kaskad.i18n import tr, use_lang
        drec = {"detail_code": "detail.no_iface", "detail_args": {"iface": "kawg3"}, "detail_fail": [1, 3]}
        with use_lang("ru"):
            words = exits.detail_text(drec)
        ev = {"key": "gw_live.event.exit_dead_why", "args": {"name": "fi-1", "detail": words, "detail_rec": drec}}
        self.assertEqual(live.event_text(ev, "ru"), "Выход «fi-1» не работает: " + words)
        en = live.event_text(ev, "en")
        self.assertTrue(en.startswith("Exit “fi-1” is down: no interface kawg3"), en)
        self.assertNotRegex(en, "[А-Яа-яЁё]")
        with use_lang("en"):                                  # a naive reader: the collector's words, no crash
            self.assertIn(words, tr(ev["key"], **ev["args"]))

    def test_collect_why_takes_the_code(self):
        from kaskad.gw import collect
        from kaskad.i18n import use_lang
        rec = {"alive": False, "fail_since": 100, "detail_code": "detail.no_answer"}
        with use_lang("en"):
            text = collect._why(rec, {}, 160)
        self.assertNotRegex(text, "[А-Яа-яЁё]")
        self.assertIn("no answer", text)


if __name__ == "__main__":
    unittest.main()

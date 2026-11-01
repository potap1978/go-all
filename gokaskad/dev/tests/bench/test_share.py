"""«Поделиться» проверкой — the safe answer (C9; k3/handoff/SPEC-node-score-schedule-share.md §5.1–5.2, «Сверка 24.10»):
the scanner over every combination of the switches on a bait node (addresses, domains, the panel behind nginx with its
secret prefix, the name of the host, people, devices, exits, keys), the types and ranges of every leaf, the filter of
the free text (the model of the CPU), no hoster ever, «Сначала проверьте сервер»."""

import ipaddress
import itertools
import json
import re
import unittest

from kaskad.bench import share as SH

KEY44 = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2"       # 44 signs of a key alphabet
PATH = "app-" + "Zx9" * 28 + "QW"                              # the secret prefix of the panel: app- + 86
BAIT_STATE = {
    "gateway": {"host": "192.0.2.10", "exits": [
        {"id": "e-0123456789", "name": "Выход Анны", "address": "203.0.113.5", "port": 51820, "key": KEY44},
        {"id": "e-0123456790", "name": "Netherlands fast", "server": "2001:db8::10", "sni": "cdn.example.org"}],
        "hostmon": {"geo": {"country": "FI", "ru": False, "src": "exits_geo", "ts": 1}}},
    "people": [{"id": "p-1", "name": "Анна", "devices": [{"name": "iPhone Анны", "ip": "10.77.1.10"}]},
               {"id": "p-2", "name": "Boris", "login": "boris-vpn"}],
    "site": {"domain": "vpn.example.com", "subs": [{"url": "https://sub.example.net/s/" + KEY44}]},
    "node": {"name": "node-anna"},
}
BAIT_CFG = {"proxy": {"on": True, "domain": "panel.example.com", "path": PATH},
            "public": {"on": True, "host": "198.51.100.7", "bind": "0.0.0.0", "port": 8443}}
SECRETS = SH.collect_secrets(BAIT_STATE, BAIT_CFG, ["node-anna", "node-anna.example.com"], ["198.51.100.7", "fe80::1"])
DOC = {"summary": {"last_at": 1761200000, "stale": False, "rel_total": 93, "rel_n": 6, "rel_need": 3,
                   "checked": {"part": "night", "hhmm": "04:10"}, "last_id": "abcdef0123"},
       "links": [{"id": "down", "fresh_raw": 735.25}, {"id": "up", "fresh_raw": 5.0},
                 {"id": "net_q", "fresh_raw": None, "parts": [{"test": "rtt", "fresh_raw": 9.4},
                                                            {"test": "jitter", "fresh_raw": 1.4}]},
                 {"id": "loss", "fresh_raw": 0.41}],
       "days": [{"day": "2026-10-%02d" % (i % 28 + 1), "color": ["green", "yellow", "red", "grey"][i % 4],
                 "reason": {"code": "loss", "msg": "Потери 3,1 % с 14:10"}} for i in range(30)],
       "rows": {"people": None}}
INV = {"nproc": 2, "mem_gb": 3.912, "cpu": "AMD EPYC 7763 64-Core Processor"}
TOP = {"v", "ok", "ready", "why", "date", "at", "where", "checked", "metrics", "rel_total", "rel_n", "rel_need", "days",
       "extra", "dropped"}
DOMAIN = re.compile(r"(?i)\b[\w-]{1,63}(?:\.[\w-]{1,63})*\.[^\W\d_]{2,24}\b")
COMBOS = [set(c) for n in range(len(SH.FLAGS) + 1) for c in itertools.combinations(SH.FLAGS, n)]


def strings(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)
    elif isinstance(x, str):
        yield x


def answer(flags, doc=None, inv=None, country="FI", port=1000):
    return SH.build(DOC if doc is None else doc, "2026-10-23", INV if inv is None else inv, port, country, flags,
                    SECRETS)


class SchemaCase(unittest.TestCase):
    def check_schema(self, a):
        self.assertEqual(set(a), TOP)
        self.assertEqual((a["v"], a["ok"], a["ready"], a["why"]), (1, True, True, None))
        self.assertRegex(a["date"], r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
        self.assertTrue(type(a["at"]) is int and a["at"] > 0)                    # время UTC и МСК рядом с датой
        w = a["where"]                                                             # «чьи это цифры» (владелец 24.10)
        self.assertEqual(set(w), {"down", "up", "anchors", "n"})
        for side in ("down", "up"):
            self.assertTrue(w[side] is None or SH.TARGET.fullmatch(w[side]), w)
        self.assertTrue(w["anchors"] in (None, "own") or (isinstance(w["anchors"], list) and set(w["anchors"]) <= {"cloudflare", "google", "yandex"}), w)
        self.assertTrue(w["n"] is None or type(w["n"]) is int)
        self.assertEqual(set(a["checked"]), {"part", "people"})
        self.assertIn(a["checked"]["part"], SH.PARTS + (None,))
        self.assertIsInstance(a["checked"]["people"], bool)
        for m in a["metrics"]:
            self.assertEqual(set(m), {"id", "v", "unit"})
            self.assertIn(m["id"], SH.UNITS)
            self.assertEqual(m["unit"], SH.UNITS[m["id"]])
            self.assertTrue(isinstance(m["v"], float) and 0 <= m["v"] <= SH.VALUE_MAX)
        self.assertTrue(a["rel_total"] is None or (type(a["rel_total"]) is int and 0 <= a["rel_total"] <= 300))
        self.assertTrue(type(a["rel_n"]) is int and type(a["rel_need"]) is int)
        self.assertEqual(len(a["days"]), 30)
        self.assertTrue(all(c in SH.COLORS for c in a["days"]))
        self.assertEqual(set(a["extra"]), {"tariff", "country", "cpu"})
        t = a["extra"]["tariff"]
        if t is not None:
            self.assertEqual(set(t), {"vcpu", "ram_gb", "port_mbit"})
            self.assertTrue(t["vcpu"] is None or type(t["vcpu"]) is int)
            self.assertTrue(t["ram_gb"] is None or isinstance(t["ram_gb"], float))
            self.assertTrue(t["port_mbit"] is None or type(t["port_mbit"]) is int)
        self.assertTrue(a["extra"]["country"] is None or a["extra"]["country"] in SH.ISO)
        self.assertTrue(set(a["dropped"]) <= {"country", "cpu"})

    def scan(self, a):
        """(б)–(д) of C9: no token parses as an address (with the port taken off), no domain, no secret, no key."""
        text = json.dumps(a, ensure_ascii=False)
        low = text.lower()
        for s in strings(a):
            for tok in re.split(r"[\s,;()\[\]<>\"']+", s):
                for cand in (tok, tok.rsplit(":", 1)[0]):
                    if cand:
                        with self.assertRaises(ValueError, msg=cand):
                            ipaddress.ip_address(cand)
            self.assertIsNone(DOMAIN.search(s), s)
            self.assertIsNone(re.search(r"[A-Za-z0-9+/=_-]{32,}", s), s)
        for v in SECRETS:
            self.assertNotIn(v.lower(), low, v)
        for word in ("hoster", "score", "band", "weak", "hhmm", "reason", "04:10", "abcdef0123", "Europe"):
            self.assertNotIn(word, text)

    def test_where_has_no_address(self):
        """«Чьи это цифры» (владелец 24.10): точки замера и якоря пинга — именами; адрес якоря не выходит никогда."""
        w = {"down": "cloudflare", "up": "allowed", "anchors": ["cloudflare", "google", "yandex"], "n": 3}
        a = SH.build(DOC, "2026-10-23", INV, 1000, "FI", set(SH.FLAGS), SECRETS, where=w)
        self.check_schema(a)
        self.scan(a)
        self.assertEqual(a["where"], w)
        for bad in ({"anchors": ["1.1.1.1"]}, {"anchors": ["cloudflare", "9.9.9.9"]}, {"down": "x/../y", "up": "UP"}):
            self.assertEqual(SH.build(DOC, "2026-10-23", where=bad)["where"], {"down": None, "up": None, "anchors": None, "n": None})

    def test_every_combination_of_the_switches(self):
        for flags in COMBOS:
            a = answer(flags)
            self.check_schema(a)
            self.scan(a)
            self.assertEqual(a["extra"]["tariff"] is not None, "tariff" in flags, flags)
            self.assertEqual(a["extra"]["country"] is not None, "country" in flags, flags)
            self.assertEqual(a["extra"]["cpu"] is not None, "cpu" in flags, flags)

    def test_the_frame(self):
        a = answer(set(SH.FLAGS))
        self.assertEqual([(m["id"], m["v"]) for m in a["metrics"]],
                         [("down", 735.25), ("up", 5.0), ("rtt", 9.4), ("loss", 0.41)])
        self.assertEqual((a["rel_total"], a["rel_n"], a["rel_need"], a["checked"]),
                         (93, 6, 3, {"part": "night", "people": False}))
        self.assertEqual(a["days"][:4], ["green", "yellow", "red", "grey"])
        self.assertEqual(a["extra"], {"tariff": {"vcpu": 2, "ram_gb": 3.9, "port_mbit": 1000}, "country": "FI",
                                      "cpu": "AMD EPYC 7763 64-Core Processor"})
        self.assertEqual(a["dropped"], [])

    def test_off_by_default(self):
        a = answer(())
        self.assertEqual(a["extra"], {"tariff": None, "country": None, "cpu": None})
        self.assertEqual(SH.build(DOC, "2026-10-23", INV, 1000, "FI", ["hoster", "ip", "name"], SECRETS)["extra"],
                         {"tariff": None, "country": None, "cpu": None})

    def test_no_check_and_stale(self):
        none = dict(DOC, summary=dict(DOC["summary"], last_at=None))
        self.assertEqual(SH.build(none, None, INV, 1000, "FI", SH.FLAGS, SECRETS),
                         {"v": 1, "ok": True, "ready": False, "why": "no_check"})
        stale = dict(DOC, summary=dict(DOC["summary"], stale=True))
        self.assertEqual(SH.build(stale, "2026-09-01", INV, 1000, "FI", SH.FLAGS, SECRETS),
                         {"v": 1, "ok": True, "ready": False, "why": "stale"})
        self.assertEqual(SH.build({}, "2026-09-01")["why"], "no_check")

    def test_foreign_values_in_the_document_do_not_pass(self):
        """(а) of C9: a value of another type or out of its range — never in the answer."""
        doc = json.loads(json.dumps(DOC))
        doc["summary"].update(rel_total="93; rm", rel_n=True, checked={"part": "<b>"})
        doc["links"][0]["fresh_raw"] = "192.0.2.10"
        doc["links"][1]["fresh_raw"] = float("nan")
        doc["links"][3]["fresh_raw"] = -1
        doc["days"][0]["color"] = "<b>"
        a = SH.build(doc, "2026-10-23", {"nproc": "2; rm", "mem_gb": 1e9, "cpu": None}, "1000", "192.0.2.10",
                     SH.FLAGS, SECRETS)
        self.check_schema(a)
        self.scan(a)
        self.assertEqual([m["id"] for m in a["metrics"]], ["rtt"])
        self.assertEqual((a["rel_total"], a["rel_n"], a["checked"]["part"], a["days"][0]), (None, 0, None, "grey"))
        self.assertEqual(a["extra"], {"tariff": None, "country": None, "cpu": None})
        self.assertEqual(a["dropped"], ["country"])
        for cc in ("XX", "fi", "RUS", "", 7):
            self.assertIsNone(answer({"country"}, country=cc)["extra"]["country"], cc)

    def test_the_model_of_the_cpu(self):
        good = ("Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz", "AMD EPYC 7763 64-Core Processor",
                "QEMU Virtual CPU version 2.5+", "ARMv8 Processor rev 1 (v8l)")
        for cpu in good:
            self.assertEqual(SH.share_text(cpu, SECRETS), (cpu, None), cpu)
            self.assertEqual(answer({"cpu"}, inv=dict(INV, cpu=cpu))["extra"]["cpu"], cpu)
        bad = ("vpn@example.com", "vpn.example.com", "192.0.2.10", "192.0.2.10:443", "１９２．０．２．１０",
               "example . com", "example。com", "сервер.рф", "Анна", "<script>", "CloudㅤVPS", "aezaꓸnet",
               "aezaᐧnet", "aezaࢇnet", "Aeza.net", "x" * 49, "a‮b", KEY44, "2001:db8::10",
               "[2001:db8::10]:443", "node-anna CPU", "Boris Xeon", "boris-vpn", "https://x", "www.x",
               "fe80::1", "cdn.example.org", "xn--e1afmkfd.xn--p1ai")
        for cpu in bad:
            text, why = SH.share_text(cpu, SECRETS)
            self.assertIsNone(text, cpu)
            self.assertTrue(why, cpu)
            a = answer({"cpu"}, inv=dict(INV, cpu=cpu))
            self.assertIsNone(a["extra"]["cpu"], cpu)
            self.assertEqual(a["dropped"], ["cpu"], cpu)
            self.scan(a)

    def test_the_secrets_of_the_node(self):
        for v in ("192.0.2.10", "203.0.113.5", "2001:db8::10", "cdn.example.org", "Выход Анны", "Анна", "iPhone Анны",
                  "boris-vpn", "vpn.example.com", "panel.example.com", PATH, "198.51.100.7", "node-anna", KEY44,
                  "Netherlands fast"):
            self.assertIn(v, SECRETS, v)
        self.assertNotIn("FI", SECRETS)                       # two letters — a false match, never a secret
        self.assertNotIn("exits_geo", SECRETS)                # a code, not a name or an address
        rf = SH.collect_secrets({"site": {"domain": "сервер.рф"}})
        self.assertIn("сервер.рф", rf)
        self.assertTrue(any(x.startswith("xn--") for x in rf), rf)     # both forms of a domain (idna)

    def test_the_last_guard(self):
        """A model of the CPU that holds a value of the secrets in any spelling never goes, even past share_text."""
        self.assertTrue(SH.leaks({"extra": {"cpu": "Node-Anna"}}, ["node-anna"]))
        self.assertFalse(SH.leaks({"extra": {"cpu": "AMD"}}, ["node-anna", "ab"]))


if __name__ == "__main__":
    unittest.main()

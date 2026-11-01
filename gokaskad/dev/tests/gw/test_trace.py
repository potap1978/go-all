"""«Куда пойдёт трафик?» (kaskad.gw.trace): the longest suffix among block / force / direct, the order block -> route
block -> force -> direct -> networks of Russia -> route, all_exit, dead exits and the fail policy, the networks not
loaded into the table, dnsmasq without nftset, SOCKS5, a person and a new device, the input, the resolver (timeout,
cache), ``kaskadctl gw trace`` and the highlight ids the scheme draws."""

import json
import os
import threading
import time
from unittest import mock

from kaskad import state
from kaskad.errors import USAGE, KaskadError
from kaskad.gw import dns, exits, net, trace
from kaskad.rules import apply as rapply
from tests.rules_helpers import consented, seed_small, small_limits
from tests.test_gw_ops import CliGwCase

D1, D2, D3 = "d-00000001", "d-00000002", "d-00000003"      # Телефон (Мама), Роутер на даче (Дача), Ноутбук (Мама)
U1, U2 = "u-00000001", "u-00000002"


def fake_dns(table):
    calls = []

    def resolver(name):
        calls.append(name)
        got = table.get(name)
        if got is None:
            return [], "такого домена нет"
        return list(got), None
    resolver.calls = calls
    return resolver


DNS = {"example.com": ["93.184.216.34"], "ru-hosted.example": ["77.88.0.5"], "mixed.example": ["8.8.4.4", "2.26.8.9"],
       "mixed-ru-first.example": ["2.26.8.9", "8.8.4.4"], "openai.com": ["5.8.1.1"]}


class TraceCase(CliGwCase):
    def setUp(self):
        super().setUp()
        self._limits = small_limits()
        self._limits.start()
        trace._dns_cache.clear()

    def tearDown(self):
        self._limits.stop()
        super().tearDown()

    def world(self, lists=True, loaded=True, nftset=True, default="all_exit", **kw):
        """The scenario of test_gw_net (wg + awg2; pool VLESS «Вена» line 1 + AmneziaWG «Amsterdam» line 2) with
        «Мама» (Телефон, Ноутбук) on «Россия напрямую»; Ноутбук routes to «Вена» with fail «direct», «Дача» to
        «Amsterdam» with fail «block»."""
        st = self.scenario(**kw)
        st["gateway"]["users"][0]["rules"] = "ru_direct"
        st["gateway"]["rules"] = {"default": default}
        st["gateway"]["host"] = "203.0.113.7"
        state.save(consented(st))          # a node of stage 1: «Россия напрямую» applies with the consent ⚠РФ (R2)
        if lists:
            seed_small()
            if loaded is None and os.path.exists(rapply.load_state_path()):
                os.unlink(rapply.load_state_path())
            if loaded is not None:
                rapply._save_load_state({"ver": net.ru_wanted(), "ok": bool(loaded), "at": "x", "seconds": 0,
                                         "elements": 3, "fails": 0})
        dns._save_features({"version": (2, 90), "nftset": nftset})
        return state.load()

    def run_trace(self, st, who, target, table=None):
        return trace.trace(st, who, target, resolver=fake_dns(DNS if table is None else table))

    def texts(self, res):
        return "\n".join(trace.lines(res))


class MatchTest(TraceCase):
    def test_longest_suffix_wins_and_names_its_category(self):
        st = self.world()
        ctx = trace.context(st)
        g, cats = ctx["groups"], ctx["cats"]
        m = trace.match_domain("www.youtube.com", g, cats)
        self.assertEqual((m["group"], m["suffix"], m["categories"]), ("force", "youtube.com", ["youtube"]))
        # music.youtube.com is in category-ru too, but youtube.com («всегда через выход») came first: the lists are
        # minimal, the direct entry under it is dropped — as dnsmasq would see it
        self.assertEqual(trace.match_domain("music.youtube.com", g, cats)["group"], "force")
        self.assertEqual(trace.match_domain("ad.mail.ru", g, cats)["group"], "block")
        # telemetry.example is in win-spy (block) and microsoft (direct): block, by its own category
        m = trace.match_domain("x.telemetry.example", g, cats)
        self.assertEqual((m["group"], m["categories"]), ("block", ["win-spy"]))
        self.assertEqual(trace.match_domain("mail.yandex.ru", g, cats)["categories"], ["category-ru"])
        self.assertEqual(trace.match_domain("whatismyipaddress.com", g, cats)["group"], "force")
        self.assertIsNone(trace.match_domain("example.com", g, cats))
        self.assertIsNone(trace.match_domain("notyoutube.com", g, cats))     # a suffix is a whole label

    def test_targets(self):
        p = trace.parse_target
        self.assertEqual(p("https://www.YouTube.com/watch?v=1")["value"], "www.youtube.com")
        self.assertEqual(p("ya.ru.")["value"], "ya.ru")
        self.assertEqual(p("user@host.example:8443/x")["value"], "host.example")
        self.assertEqual(p(" 8.8.8.8 "), {"input": "8.8.8.8", "kind": "ip", "value": "8.8.8.8"})
        self.assertEqual(p("1.1.1.1:53")["kind"], "ip")
        self.assertEqual(p("яндекс.рф")["value"], "xn--d1acpjx3f.xn--p1ai")
        for bad in ("", "   ", "localhost", "999.1.1.1", "1.2.3", "::1", "[2001:db8::1]:443", "a b", "-x.ru",
                    "x" * 3000):
            with self.assertRaises(KaskadError, msg=bad) as e:
                p(bad)
            self.assertEqual(e.exception.code, USAGE)


class DecisionTest(TraceCase):
    def test_block_is_global_and_first(self):
        st = self.world()
        for who in (D1, D2, "-"):                                       # ru_direct, all_exit, a new device
            res = self.run_trace(st, who, "ad.mail.ru")
            self.assertEqual((res["verdict"], res["reason"]), ("block", "ads"), who)
            self.assertEqual(res["highlight"]["category"], "ads")
            self.assertNotIn("block", res["highlight"]["nodes"])          # the filter drops it, not the route (М2)
        res = self.run_trace(st, D1, "ad.mail.ru")
        self.assertEqual(res["highlight"]["edges"], ["access:%s:wg" % D1, "flow:wg"])   # ends at the row of the filter
        self.assertEqual(res["highlight"]["rows"], {"filter": "ads"})
        self.assertIn("категория category-ads", self.texts(res))
        self.assertEqual(res["summary"], "Заблокировано: реклама и телеметрия")
        # torrents: only with their switch; off — by the route
        self.assertEqual(self.run_trace(st, D2, "tracker.example")["verdict"], "exit")
        st["gateway"]["rules"]["block_torrent"] = True
        res = self.run_trace(st, D2, "tracker.example")
        self.assertEqual((res["verdict"], res["reason"]), ("block", "torrent"))
        # the switch «Реклама» off: not blocked any more
        st["gateway"]["rules"]["block_ads"] = False
        self.assertEqual(self.run_trace(st, D2, "ad.mail.ru")["verdict"], "exit")

    def test_ru_direct_force_direct_ip_route(self):
        st = self.world()
        res = self.run_trace(st, D1, "youtube.com")
        self.assertEqual(res["verdict"], "exit")
        self.assertEqual(res["match"]["group"], "force")
        self.assertEqual(res["highlight"]["category"], "rest")
        # force wins over a Russian address (openai.com resolves into the networks of Russia in DNS above)
        res = self.run_trace(st, D1, "openai.com")
        self.assertEqual((res["verdict"], res["reason"]), ("exit", "route"))
        self.assertEqual(res["addresses"], [])                         # not even resolved: the domain decides
        res = self.run_trace(st, D1, "ya.ru")
        self.assertEqual(res["verdict"], "exit")                       # ya.ru is not in this small list
        res = self.run_trace(st, D1, "mail.yandex.ru")
        self.assertEqual((res["verdict"], res["reason"]), ("direct", "ru_domain"))
        self.assertEqual(res["summary"], "Напрямую с адреса сервера 203.0.113.7")
        self.assertEqual(res["highlight"]["category"], "ru")
        self.assertEqual(res["highlight"]["edges"], ["access:%s:wg" % D1, "flow:wg", "pass:ru_direct", "cat:ru_direct:ru"])
        self.assertEqual(res["highlight"]["rows"], {"filter": "pass", "rules:ru_direct": "ru"})
        self.assertNotIn("pool", res["highlight"]["nodes"])           # the route is not taken: no pool lit
        self.assertIn("direct", res["highlight"]["nodes"])
        # an address of the networks of Russia, given or resolved
        res = self.run_trace(st, D1, "77.88.1.200")
        self.assertEqual((res["verdict"], res["reason"]), ("direct", "ru_ip"))
        res = self.run_trace(st, D1, "ru-hosted.example")
        self.assertEqual((res["verdict"], res["addresses"]), ("direct", ["77.88.0.5"]))
        res = self.run_trace(st, D1, "8.8.8.8")
        self.assertEqual((res["verdict"], res["reason"]), ("exit", "route"))
        self.assertIn("не в сетях России", self.texts(res))
        res = self.run_trace(st, D1, "example.com")
        self.assertEqual(res["verdict"], "exit")
        self.assertIn("93.184.216.34", self.texts(res))

    def test_steps_and_the_exit_now(self):
        st = self.world()
        res = self.run_trace(st, D1, "youtube.com")
        steps = [s["text"] for s in res["steps"]]
        self.assertEqual(steps[0], "Устройство «Телефон» (Мама), профиль «Россия напрямую» (от пользователя)")
        self.assertEqual(steps[1], "youtube.com — список «всегда через выход» (категория youtube)")
        self.assertEqual(steps[2], "Маршрут: пул выходов (по умолчанию)")
        self.assertTrue(steps[3].startswith("Сейчас "), steps)
        self.assertTrue(res["summary"].startswith("Через выход"), res["summary"])
        hl = res["highlight"]
        for nid in (U1, "in:wg", "rules:ru_direct", "pool"):
            self.assertIn(nid, hl["nodes"])
        self.assertEqual(hl["devices"], [D1])
        self.assertEqual((hl["inbound"], hl["profile"], hl["via"], hl["category"]), ("wg", "ru_direct", "pool", "rest"))
        # ONE chain: device -> its inbound -> the filter -> the profile -> the route -> the exit now
        self.assertEqual(hl["edges"][:4], ["access:%s:wg" % D1, "flow:wg", "pass:ru_direct", "route:ru_direct:pool"])
        if hl["balancer"]:                                             # its hash picked the VLESS exit: the balancer
            self.assertEqual((hl["balancer"], hl["rows"]["pool"], len(hl["edges"])), ("vless", "vless", 4))
            # the line goes on to the exits — branching at its end only, where xray chooses (T-scheme-3.2.15, Н3)
            self.assertEqual(hl["alts"], ["member:" + self.vid])
            self.assertIn("exit:" + self.vid, hl["nodes"])
        else:
            self.assertEqual(hl["edges"][4:], ["member:" + hl["exit"]])
            self.assertEqual(hl["alts"], [])
        # Ноутбук: its own exit «Вена»
        res = self.run_trace(st, D3, "example.com")
        self.assertEqual(res["summary"], "Через выход «Вена»")
        self.assertEqual(res["exit"]["id"], self.vid)
        self.assertIn("exit:" + self.vid, res["highlight"]["nodes"])
        self.assertIn("Маршрут: выход «Вена» (своя у устройства)", self.texts(res))
        self.assertIn("Сейчас выход «Вена» — работает", self.texts(res))

    def test_all_exit_ignores_direct_and_force_but_not_block(self):
        st = self.world()
        res = self.run_trace(st, D2, "mail.yandex.ru")                  # Дача: all_exit (по умолчанию)
        self.assertEqual(res["verdict"], "exit")
        self.assertIn("при профиле «Всё через выход» не действует", self.texts(res))
        self.assertEqual(res["highlight"]["category"], "rest")
        self.assertIn("rules:all_exit", res["highlight"]["nodes"])
        self.assertEqual(self.run_trace(st, D2, "77.88.1.1")["verdict"], "exit")
        self.assertEqual(self.run_trace(st, D2, "ad.mail.ru")["verdict"], "block")

    def test_dead_exits_and_the_fail_policy(self):
        st = self.world(alive=False)
        res = self.run_trace(st, D1, "example.com")                    # pool, fail block, nothing alive
        self.assertEqual((res["verdict"], res["reason"]), ("block", "fail_block"))
        self.assertIn("В пуле нет живых выходов — блок", self.texts(res))
        self.assertEqual(res["summary"], "Заблокировано: в пуле нет живых выходов")
        # stage 1 (§1.3 отличие 5, §2.3; REV-s1-t4 З2): the block of the LAST STEP (exits unfit) keeps «Россия
        # напрямую» — Russian sites go directly, as nft (gw_ruonly) and xray (the fallback block) send them; was
        # (3.2.19, T-N5 №12): «a block stays a block». A block by choice (route «block») still closes Russia too
        # (test_route_block_and_direct); a foreign site is still blocked.
        res = self.run_trace(st, D1, "mail.yandex.ru")
        self.assertEqual((res["verdict"], res["reason"]), ("direct", "ru_domain"))
        self.assertNotIn("«напрямую» не действует", self.texts(res))
        self.assertIn("В пуле нет живых выходов — блок", self.texts(res))
        self.assertIn("закрыто всё, кроме сайтов России", self.texts(res))
        self.assertIn("direct", res["highlight"]["nodes"])
        self.assertEqual(res["highlight"]["target"], "direct")
        res = self.run_trace(st, D1, "77.88.1.1")                      # a Russian address: directly too
        self.assertEqual((res["verdict"], res["reason"]), ("direct", "ru_ip"))
        res = self.run_trace(st, D1, "openai.com")                     # not Russian: blocked
        self.assertEqual((res["verdict"], res["reason"]), ("block", "fail_block"))
        res = self.run_trace(st, D3, "example.com")                    # «Вена» dead, fail direct
        self.assertEqual((res["verdict"], res["reason"]), ("direct", "fail_direct"))
        self.assertIn("Выход «Вена» не работает — напрямую", self.texts(res))
        self.assertIn("exit:" + self.vid, res["highlight"]["nodes"])
        self.assertIn("direct", res["highlight"]["nodes"])
        self.assertEqual(res["highlight"]["edges"][-1], "fail:ru_direct:exit:%s:direct" % self.vid)   # rules -> the fail target
        self.assertEqual(res["highlight"]["target"], "direct")
        res = self.run_trace(st, D2, "example.com")                    # «Amsterdam» dead, fail block
        self.assertEqual((res["verdict"], res["reason"]), ("block", "fail_block"))

    def test_route_block_and_direct(self):
        st = self.world()
        st["gateway"]["devices"][0]["route"] = {"via": "block", "fail": "block"}
        res = self.run_trace(st, D1, "mail.yandex.ru")
        self.assertEqual((res["verdict"], res["reason"]), ("block", "route_block"))
        st["gateway"]["devices"][0]["route"] = {"via": "direct", "fail": "block"}
        res = self.run_trace(st, D1, "example.com")
        self.assertEqual((res["verdict"], res["reason"]), ("direct", "route_direct"))
        st["gateway"]["devices"][0]["enabled"] = False
        res = self.run_trace(st, D1, "example.com")
        self.assertEqual((res["verdict"], res["reason"]), ("block", "off"))

    def test_networks_not_loaded_into_the_table(self):
        st = self.world(loaded=False)
        res = self.run_trace(st, D1, "77.88.1.1")
        self.assertEqual((res["verdict"], res["reason"]), ("exit", "route"))
        self.assertIn("сети России сейчас не загружены в таблицу — по IP напрямую не пойдёт", self.texts(res))
        # domains of dnsmasq do not need the table
        self.assertEqual(self.run_trace(st, D1, "mail.yandex.ru")["verdict"], "direct")
        # unknown (no record of a load of these lists): say it, decide as the gateway should
        st = self.world(loaded=None)
        res = self.run_trace(st, D1, "77.88.1.1")
        self.assertEqual(res["verdict"], "direct")
        self.assertTrue(any("состояние неизвестно" in n for n in res["notes"]))

    def test_without_nftset_only_the_networks_count(self):
        st = self.world(nftset=False)
        res = self.run_trace(st, D1, "mail.yandex.ru")                 # direct domain not marked: by its address
        self.assertEqual(res["verdict"], "exit")
        self.assertTrue(any("без nftset" in n for n in res["notes"]))
        self.assertIn("но правило по домену сейчас не действует — решают адреса", self.texts(res))
        res = self.run_trace(st, D1, "openai.com")                     # force not marked: a Russian address -> direct
        self.assertEqual(res["verdict"], "direct")

    def test_without_lists(self):
        st = self.world(lists=False)
        res = self.run_trace(st, D1, "ad.mail.ru")
        self.assertEqual(res["verdict"], "exit")
        self.assertIn("списков доменов нет", self.texts(res))
        self.assertIn("списков RoscomVPN ещё нет — пока всё через выход", self.texts(res))
        self.assertEqual(res["profile"]["applied"], "all_exit")
        self.assertEqual(self.run_trace(st, D1, "77.88.1.1")["verdict"], "exit")

    def test_mixed_addresses_and_dns_failures(self):
        st = self.world()
        res = self.run_trace(st, D1, "mixed.example")                  # first address abroad
        self.assertEqual((res["verdict"], res["reason"]), ("exit", "route"))
        self.assertTrue(any("1 из 2" in n for n in res["notes"]))
        res = self.run_trace(st, D1, "mixed-ru-first.example")
        self.assertEqual(res["verdict"], "direct")
        res = self.run_trace(st, D1, "nowhere.example")
        self.assertEqual(res["verdict"], "exit")
        self.assertEqual(res["resolve"], {"ok": False, "error": "такого домена нет"})
        self.assertIn("Адреса nowhere.example не найдены", self.texts(res))

    def test_socks5(self):
        st = self.world()
        st["gateway"]["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        st["gateway"]["devices"][0]["access"] = ["socks5"]
        res = self.run_trace(st, D1, "mail.yandex.ru")
        self.assertTrue(res["who"]["socks"])
        self.assertTrue(any("SOCKS5" in n and "xray" in n for n in res["notes"]))
        # xray of the gateway has no list files yet: no domain rules, no networks
        self.assertEqual(res["verdict"], "exit")
        with mock.patch.object(rapply, "manifest", lambda: {"geoip": {"codes": ["DIRECT"]},
                                                          "geosite": {"codes": ["CATEGORY-RU"]}}):
            self.assertEqual(self.run_trace(st, D1, "mail.yandex.ru")["verdict"], "direct")
            self.assertEqual(self.run_trace(st, D1, "77.88.1.1")["verdict"], "direct")
        # all_exit on SOCKS5: said too (xray has keyword and regexp dnsmasq cannot say)
        st["gateway"]["devices"][1]["access"] = ["socks5"]
        self.assertTrue(any("SOCKS5" in n for n in self.run_trace(st, D2, "example.com")["notes"]))
        # both kinds of inbound: the path goes through ONE — the first given (the kernel), SOCKS5 is noted; through
        # SOCKS5 (chosen) xray decides and the chain lights that inbound alone
        st["gateway"]["devices"][0]["access"] = ["wg", "socks5"]
        res = self.run_trace(st, D1, "mail.yandex.ru")
        self.assertEqual((res["verdict"], res["who"]["inbound"]), ("direct", "wg"))
        self.assertTrue(any("Через SOCKS5 это устройство решал бы xray" in n for n in res["notes"]))
        self.assertEqual(res["highlight"]["edges"][:2], ["access:%s:wg" % D1, "flow:wg"])
        self.assertIn("(есть и SOCKS5 — путь через него проверяется отдельно)", self.texts(res))
        res = trace.trace(st, D1, "mail.yandex.ru", resolver=fake_dns(DNS), inbound="socks5")
        self.assertEqual((res["verdict"], res["who"]["inbound"], res["who"]["socks"]), ("exit", "socks5", True))
        self.assertEqual(res["highlight"]["edges"][:2], ["access:%s:socks5" % D1, "flow:socks5"])
        self.assertNotIn("in:wg", res["highlight"]["nodes"])
        # by the route — stage 1 (SE §2.2, §2.4): SOCKS5 takes the pin of the device as the kernel does, no balancer
        res = trace.trace(st, D1, "8.8.8.8", resolver=fake_dns(DNS), inbound="socks5")
        self.assertEqual((res["verdict"], res["highlight"]["balancer"]), ("exit", False))
        wg = trace.trace(st, D1, "8.8.8.8", resolver=fake_dns(DNS), inbound="wg")
        self.assertEqual(res["exit"]["id"], wg["exit"]["id"])
        self.assertIn(res["exit"]["id"], (self.vid, self.aid))
        for bad in ("awg2", "l2tp"):                                   # not given / not given and not installed
            with self.assertRaises(KaskadError) as e:
                trace.trace(st, D1, "8.8.8.8", resolver=fake_dns(DNS), inbound=bad)
            self.assertEqual(e.exception.code, USAGE)
            self.assertIn("нет протокола", str(e.exception))
        st["gateway"]["inbounds"]["socks5"]["enabled"] = False
        with self.assertRaises(KaskadError) as e:
            trace.trace(st, D1, "8.8.8.8", resolver=fake_dns(DNS), inbound="socks5")
        self.assertIn("выключен", str(e.exception))
        with self.assertRaises(KaskadError):
            trace.trace(st, U1, "8.8.8.8", resolver=fake_dns(DNS), inbound="wg")      # a person has no inbound

    def test_person_and_new_device(self):
        st = self.world()
        res = self.run_trace(st, U1, "mail.yandex.ru")                  # «Мама»: ru_direct from the person
        self.assertEqual((res["who"]["kind"], res["verdict"]), ("user", "direct"))
        self.assertIn("Новое устройство пользователя «Мама»", self.texts(res))
        self.assertEqual(res["highlight"]["devices"], [])
        self.assertIn(U1, res["highlight"]["nodes"])
        res = self.run_trace(st, "Дача", "example.com")                 # by name; own route «Amsterdam»
        self.assertEqual((res["who"]["kind"], res["summary"]), ("user", "Через выход «Amsterdam»"))
        res = self.run_trace(st, "-", "mail.yandex.ru")                 # default all_exit
        self.assertEqual((res["who"]["kind"], res["verdict"]), ("new", "exit"))
        self.assertEqual((res["highlight"]["user"], res["highlight"]["inbound"]), (None, None))
        self.assertEqual(res["highlight"]["edges"], ["pass:all_exit", "route:all_exit:pool"])   # no device, no inbound
        self.assertIn("rules:all_exit", res["highlight"]["nodes"])
        # a new device on the pool: the exit is picked later — every live one is lit
        self.assertTrue(all(("exit:" + e) in res["highlight"]["nodes"] for e in (self.vid, self.aid)))
        self.assertEqual(res["summary"], "Через выход пула")
        res = self.run_trace(st, "telefon-0001", "8.8.8.8")             # a device by its login
        self.assertEqual(res["who"]["id"], D1)
        with self.assertRaises(KaskadError):
            self.run_trace(st, "d-99999999", "8.8.8.8")

    def test_gateway_off_is_said(self):
        st = self.world(enabled=False)
        res = self.run_trace(st, D1, "example.com")
        self.assertTrue(any("Шлюз выключен" in n for n in res["notes"]))


class RfMarkTest(TraceCase):
    """R5 (docs/k3/SPEC-stage1-rf-mark-builtin.md §5.1, §9 «Трассировка»): a domain of an organisation of the registry
    ⚠РФ — ``rf: {org, name}`` and the line «⚠РФ: Meta» with its explanation (CLI, bot, panel — the same steps); any
    other target — no ``rf``; the mode ``need`` — «Россия напрямую не включена: нет подтверждения ⚠РФ»."""

    def test_marked_domain(self):
        st = self.world()
        for name in ("meta.com", "www.facebook.com", "instagram.com"):
            res = self.run_trace(st, D1, name, table={name: ["93.184.216.34"]})
            self.assertEqual(res["rf"], {"org": "meta", "name": "Meta"}, name)
            text = self.texts(res)
            self.assertIn("⚠РФ: Meta", text)
            self.assertIn("ресурс признан экстремистским в РФ", text)
            step = next(s for s in res["steps"] if s["text"] == "⚠РФ: Meta")
            self.assertEqual(step["tone"], "warn")
            self.assertIn("⚠РФ: Meta", trace.text_html(res, lambda x: x))     # the bot
            self.assertEqual(res["verdict"], "exit")                            # the mark changes no path
        res = self.run_trace(st, D1, "youtube.com")                             # listed, not of the registry
        self.assertIsNone(res["rf"])
        self.assertNotIn("⚠РФ", self.texts(res))

    def test_unmarked_targets(self):
        st = self.world()
        for target in ("example.com", "whatsapp.com", "web.whatsapp.com", "93.184.216.34"):
            res = self.run_trace(st, D1, target, table={target: ["93.184.216.34"]})
            self.assertIsNone(res["rf"], target)                                # WhatsApp — exclude of Meta
            self.assertNotIn("⚠РФ:", self.texts(res), target)

    def test_english(self):
        from kaskad import i18n
        st = self.world()
        with i18n.use_lang("en"):
            res = self.run_trace(st, D1, "meta.com", table={"meta.com": ["93.184.216.34"]})
            self.assertEqual(res["rf"]["name"], "Meta")
            self.assertIn("⚠RU: Meta", self.texts(res))
            self.assertIn("recognised as extremist in Russia", self.texts(res))

    def test_need_mode_is_explained(self):
        st = self.world()
        st["gateway"].pop("rf_consent", None)            # a hand edit or a foreign copy: chosen, no consent (gen 2)
        state.save(st)
        st = state.load()
        res = self.run_trace(st, D1, "meta.com", table={"meta.com": ["93.184.216.34"]})
        self.assertEqual((res["profile"]["profile"], res["profile"]["applied"], res["profile"]["why"]),
                         ("ru_direct", "all_exit", "no_consent"))
        text = self.texts(res)
        self.assertIn("Россия напрямую не включена: нет подтверждения ⚠РФ", text)
        self.assertEqual(res["rf"]["org"], "meta")
        res = self.run_trace(st, D1, "mail.yandex.ru")                           # Russia: not directly without it
        self.assertEqual(res["verdict"], "exit")


class ResolverTest(TraceCase):
    def test_timeout_and_cache(self):
        gate = threading.Event()

        def slow(name):
            gate.wait(5)
            return ["1.2.3.4"]
        with mock.patch.object(trace, "_getaddrinfo", slow):
            t0 = time.monotonic()
            ips, err = trace.resolve("slow.example", timeout=0.2)
            self.assertLess(time.monotonic() - t0, 2)
            self.assertEqual((ips, err), ([], "DNS не ответил за 0 с"))
        gate.set()
        calls = []

        def fast(name):
            calls.append(name)
            return ["93.184.216.34", "93.184.216.34", "2606:2800::1"]
        with mock.patch.object(trace, "_getaddrinfo", fast):
            self.assertEqual(trace.resolve("fast.example"), (["93.184.216.34", "93.184.216.34"], None))
            self.assertEqual(trace.resolve("fast.example")[0], ["93.184.216.34", "93.184.216.34"])
            self.assertEqual(calls, ["fast.example"])                   # the second answer came from the cache

    def test_ranges_are_searched(self):
        st = self.world()
        r = trace._ranges(trace.context(st)["v"])
        for ip, want in (("2.26.8.0", True), ("2.26.8.255", True), ("2.26.9.0", False), ("5.8.1.7", True),
                         ("5.8.3.0", False), ("77.88.1.255", True), ("1.1.1.1", False), ("255.255.255.255", False)):
            self.assertEqual(trace.in_ranges(ip, r), want, ip)


class CliTest(TraceCase):
    def test_cli_text_and_json(self):
        self.world()
        with mock.patch.object(trace, "resolve", fake_dns(DNS)):
            out, _ = self.ok("gw", "trace", D1, "youtube.com")
            self.assertTrue(out.startswith("Итог: Через выход"), out)
            self.assertIn("1. Устройство «Телефон» (Мама)", out)
            rc, doc, _ = self.run_json("gw", "trace", "-", "ad.mail.ru")
            self.assertEqual((rc, doc["verdict"]), (0, "block"))
            rc, out, err = self.run_cli("gw", "trace", D1, "not a domain")
            self.assertNotEqual(rc, 0)
            rc, out, err = self.run_cli("gw", "trace", "d-99999999", "ya.ru", "--json")
            self.assertNotEqual(rc, 0)
            self.assertFalse(json.loads(out)["ok"])
            # --inbound: the path through that inbound of the device; one it has not — a refusal
            rc, doc, _ = self.run_json("gw", "trace", D2, "example.com", "--inbound", "awg2")
            self.assertEqual((rc, doc["who"]["inbound"]), (0, "awg2"))
            self.assertIn("access:%s:awg2" % D2, doc["highlight"]["edges"])
            self.assertNotIn("access:%s:wg" % D2, doc["highlight"]["edges"])
            rc, doc, _ = self.run_json("gw", "trace", D2, "example.com")
            self.assertEqual(doc["who"]["inbound"], "awg2")                       # the first given
            rc, out, err = self.run_cli("gw", "trace", D1, "ya.ru", "--inbound", "awg2")
            self.assertNotEqual(rc, 0)
            self.assertIn("нет протокола", out + err)

    def test_no_command_is_run(self):
        st = self.world()
        before = len(self.w.runner.calls)
        self.run_trace(st, D1, "77.88.1.1")
        self.run_trace(st, D3, "youtube.com")
        self.assertEqual(len(self.w.runner.calls), before)
        self.assertTrue(exits.alive_ids(exits.load_snapshot()))

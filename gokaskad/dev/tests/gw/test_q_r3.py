"""П2, круг 3 по REV-q-r2 (российские группы):

* З-2 — observatory снова только «gx-»; российский xray-выход проверяет своя проба через ``probe-<id>`` (SOCKS5) к
  российским целям группы — DNS по TCP «A ya.ru» к 77.88.8.8:53 (SPEC-groups §6.1), с частотой группы: жив при
  недоступном Cloudflare, лёг — российский путь на резерв, российские AWG-ноги без проб observatory, xray не
  перезапускается на первом и последнем российском VLESS;
* З-3 — разбуженный выход «Лениво» участвует в проверке сети узла (``skip = off | (idle - only)``);
* З-1, П-1 — ``ss -K``: итог по факту (сокеты до и после тем же фильтром), только сокеты xray (MainPID);
* П-2 — чистка ``gateway.exits`` на пути сборщика; П-3 — роль группы в событии конца окна; П-4 — имя с таймаутом."""

import json
import socket
import struct
import threading
import time
from unittest import mock

from kaskad import secrets, state
from kaskad.gw import collect, exits, live, model, xray
from kaskad.gw import policy as P
from kaskad.sysrun import FakeRunner
from tests.gw.test_q_r2 import AWG_OB, VLESS_OB, cfg_to
from tests.gw.test_ru_target import HOME, RuWorld


class FakeRouter(object):
    """A SOCKS5 inbound like xray's ``probe-<id>``: grants every CONNECT AT ONCE (as xray does), then the far side
    answers the DNS query only when ``reach(ip, port)`` — a home router whose provider cuts some targets."""

    def __init__(self, reach):
        self.reach = reach
        self.asked = []
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        self.th = threading.Thread(target=self.serve, daemon=True)
        self.th.start()

    def serve(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            try:
                self.one(c)
            except (OSError, struct.error, IndexError):
                pass
            finally:
                c.close()

    def one(self, c):
        c.settimeout(3)
        c.recv(3)
        c.sendall(b"\x05\x00")
        req = exits._recv_n(c, 10)
        ip, port = socket.inet_ntoa(req[4:8]), struct.unpack(">H", req[8:10])[0]
        self.asked.append((ip, port))
        c.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)              # granted before dialing
        if not self.reach(ip, port):
            return                                                 # the outbound failed: xray closes
        n = struct.unpack(">H", exits._recv_n(c, 2))[0]
        q = exits._recv_n(c, n)
        ans = q[:2] + b"\x81\x80" + q[4:]
        c.sendall(struct.pack(">H", len(ans)) + ans)

    def close(self):
        self.srv.close()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


RU_ONLY = lambda ip, port: ip.startswith("77.88.")                # noqa: E731 — Cloudflare cut, Yandex there


class SocksProbeTest(RuWorld):
    """З-2: своя проба российского VLESS — к российским целям, по факту ответа DNS, не по «успеху» CONNECT."""

    def setUp(self):
        super().setUp()
        self._mem = collect.MEM
        collect.MEM = collect.Mem()

    def tearDown(self):
        collect.MEM = self._mem
        super().tearDown()

    def vless(self):
        return [r for r in self.ru_rows if exits.is_xray(r)][0]

    def router(self, reach):
        r = FakeRouter(reach)
        self.addCleanup(r.close)
        return r

    def plan(self, st, mono=1000.0, router=None):
        pool = self.pool()
        with mock.patch.object(xray, "probe_port", return_value=router.port if router else free_port()):
            return collect.probe_plan(st, P.load(st, pool), pool, mono)

    def test_the_plan_takes_the_russian_dns_targets(self):
        st = self.world()
        v = self.vless()
        p = self.plan(st)
        port, targets, name = p["socks"][v["id"]]
        self.assertEqual((targets, name), ([("77.88.8.8", 53), ("77.88.8.1", 53)], "ya.ru"))
        self.assertIn(v["id"], p["only"])
        self.assertNotIn(v["id"], p["targets"])                   # no mark: not connect_through
        self.assertNotIn(v["id"], [x[0] for x in p["http"]])      # no HTTP to Yandex in the frequent probes (Б3)
        awg = [r["id"] for r in self.ru_rows if r.get("kind") == "awg"][0]
        self.assertNotIn(awg, p["socks"])

    def test_by_the_period_of_its_group(self):
        st = self.world()
        v = self.vless()
        collect.MEM.awg_at[v["id"]] = 1000.0
        self.assertNotIn(v["id"], self.plan(st, 1003.0)["socks"])           # «Часто» — 10 s: not yet
        self.assertIn(v["id"], self.plan(st, 1010.0)["socks"])

    def test_alive_while_cloudflare_is_cut(self):
        st = self.world()
        v = self.vless()
        r = self.router(RU_ONLY)
        p = self.plan(st, router=r)
        own = exits.socks_probe(p["socks"])
        self.assertIs(own[v["id"]][0], True)
        self.assertIsInstance(own[v["id"]][1], int)
        self.assertEqual(r.asked, [("77.88.8.8", 53)])            # one target, the Russian one
        # the observatory does not know the exit: its own probe says «отвечает»
        got = exits.probe(self.w.runner, [v], only={v["id"]}, doc={"observatory": {}}, own=own, measure=True)
        self.assertIs(got[v["id"]]["alive"], True)
        self.assertEqual(got[v["id"]]["rtt"], own[v["id"]][1])
        self.assertNotIn("try_age", got[v["id"]])                 # not a probe of the observatory

    def test_second_target_when_the_first_is_cut(self):
        r = self.router(lambda ip, port: ip == "77.88.8.1")
        got = exits.socks_probe({"x": (r.port, [("77.88.8.8", 53), ("77.88.8.1", 53)], "ya.ru")})
        self.assertIs(got["x"][0], True)
        self.assertEqual(r.asked, [("77.88.8.8", 53), ("77.88.8.1", 53)])

    def test_dead_router_is_dead_and_the_reserve_takes_russia(self):
        st = self.world()
        v = self.vless()
        r = self.router(lambda ip, port: False)                   # CONNECT granted, nothing behind it
        own = exits.socks_probe({v["id"]: (r.port, [("77.88.8.8", 53)], "ya.ru")}, timeout=2)
        self.assertEqual(own[v["id"]], (False, None))
        with mock.patch.object(exits, "xray_age", return_value=3600.0):
            got = exits.probe(self.w.runner, [v], only={v["id"]}, doc={}, own=own)
        self.assertIs(got[v["id"]]["alive"], False)
        awg = [i for i in self.ru_ids if i != v["id"]][0]
        t = exits.ru_target(st, self.dev(st), self.dec(st, alive=[awg]))
        self.assertEqual((t["step"], t["exit"], t["group"]), ("exit", awg, HOME))

    def test_no_inbound_is_unknown(self):
        got = exits.socks_probe({"x": (free_port(), [("77.88.8.8", 53)], "ya.ru")})
        self.assertEqual(got["x"], (None, None))
        v = self.world() and self.vless()
        p = exits.probe(self.w.runner, [v], only={v["id"]}, doc={}, own={v["id"]: (None, None)})
        self.assertIsNone(p[v["id"]]["alive"])

    def test_failed_is_rechecked(self):
        st = self.world()
        v = self.vless()
        r = self.router(lambda ip, port: False)
        pool = self.pool()
        with mock.patch.object(xray, "probe_port", return_value=r.port), \
                mock.patch.object(exits, "PROBE_TIMEOUT", 1), \
                mock.patch.object(collect, "_mono", return_value=1000.0), \
                mock.patch.object(exits, "socks_probe", side_effect=lambda items: dict((k, (False, None)) for k in items)):
            collect.probe_round(self.w.runner, legs=False, st=st)
        self.assertIn(v["id"], collect.MEM.awg_failed)
        self.assertEqual(collect.MEM.awg_at[v["id"]], 1000.0)
        with mock.patch.object(xray, "probe_port", return_value=r.port):
            p = collect.probe_plan(st, P.load(st, pool), pool, 1000.0 + collect.RECHECK)
        self.assertIn(v["id"], p["socks"])


class ObservatoryForeignOnlyTest(RuWorld):
    """З-2: observatory — только «gx-»; первый и последний российский VLESS — горячо, без перезапуска xray."""

    def cfg(self, st, dec=None):
        return xray.render(st, secrets.load(), self.pool(), (), dec or self.dec(st))

    def test_never_gr(self):
        st = self.world()
        cfg = self.cfg(st)
        self.assertEqual(cfg["observatory"]["subjectSelector"], [exits.ABROAD_PREFIX])
        v = [r for r in self.ru_rows if exits.is_xray(r)][0]
        self.assertIn(xray.probe_tag(v["id"]), [i.get("tag") for i in cfg["inbounds"]])   # its own probe is drawn
        ru_awg = [o["tag"] for o in cfg["outbounds"] if o.get("tag", "").startswith(exits.RU_PREFIX)
                  and o.get("protocol") == "freedom"]
        self.assertTrue(ru_awg)
        for t in ru_awg:                       # the Russian AmneziaWG legs get no probe of the observatory
            self.assertFalse(any(t.startswith(s) for s in cfg["observatory"]["subjectSelector"]))
        self.assertEqual(xray.observatory_selector([r for r in self.ru_rows if exits.is_xray(r)]), [])

    def test_first_and_last_russian_vless_is_hot(self):
        st = self.world()
        dec = self.dec(st)
        v = [r for r in self.ru_rows if exits.is_xray(r)][0]
        on = self.cfg(st, dec)
        off = self.cfg(st, dict(dec, quota_off=[v["id"]]))
        self.assertNotIn(xray.probe_tag(v["id"]), [i.get("tag") for i in off["inbounds"]])
        self.assertEqual(xray._base(on, []), xray._base(off, []))
        self.assertIsNotNone(xray.hot_plan(on, off, []))
        self.assertIsNotNone(xray.hot_plan(off, on, []))


class NodeCheckTest(RuWorld):
    """З-3: разбуженный «Лениво» — в проверке сети узла; российский VLESS со своей пробой — свежий в этом такте."""

    def setUp(self):
        super().setUp()
        self._mem = collect.MEM
        collect.MEM = collect.Mem()

    def tearDown(self):
        collect.MEM = self._mem
        super().tearDown()

    def lazy(self, st, pool):
        pol = P.load(st, pool)
        P.group(pol, HOME)["checks"]["freq"] = {"mode": "lazy", "every_s": None}
        return pol

    def check(self, pool, probed, pol, plan):
        called = []
        with mock.patch.object(exits, "direct_probe", side_effect=lambda t: called.append(t) or True):
            out = collect.node_check(pool, probed, pol, plan)
        return out, called

    def test_woken_lazy_exit_counts(self):
        st = self.world()
        pool = self.pool()
        pol = self.lazy(st, pool)
        collect.MEM.health = dict((i, {"state": "ok"}) for i in self.ru_ids)
        for i in self.ru_ids:
            collect.MEM.awg_at[i] = 1000.0
        with mock.patch.object(xray, "probe_port", return_value=free_port()):
            plan = collect.probe_plan(st, pol, pool, 1000.0 + collect.LAZY_WAKE_S)
        woken = [i for i in self.ru_ids if i in plan["only"]]
        self.assertEqual(sorted(woken), sorted(self.ru_ids))
        self.assertTrue(set(woken) <= plan["idle"])
        out, called = self.check(pool, dict((i, {"alive": False}) for i in woken), pol, plan)
        self.assertTrue(out["checked"])                           # every Russian exit silent: the node is asked
        self.assertEqual(len(called), 2)

    def test_idle_not_woken_takes_no_part(self):
        st = self.world()
        pool = self.pool()
        pol = self.lazy(st, pool)
        collect.MEM.health = dict((i, {"state": "ok"}) for i in self.ru_ids)
        for i in self.ru_ids:
            collect.MEM.awg_at[i] = 1000.0
        with mock.patch.object(xray, "probe_port", return_value=free_port()):
            plan = collect.probe_plan(st, pol, pool, 1010.0)
        self.assertFalse(set(self.ru_ids) & plan["only"])
        out, called = self.check(pool, dict((i, {"alive": False}) for i in self.ru_ids), pol, plan)
        self.assertEqual((out["checked"], called), (False, []))

    def test_russian_vless_own_probe_is_fresh(self):
        st = self.world()
        pool = self.pool()
        pol = P.load(st, pool)
        v = [r["id"] for r in self.ru_rows if exits.is_xray(r)][0]
        plan = {"only": {v}, "socks": {v: (1, [], "ya.ru")}, "idle": set(), "off": set()}
        out, called = self.check(pool, {v: {"alive": True}}, pol, plan)      # no try_age: not «stale»
        self.assertEqual((out["checked"], called), (False, []))
        out, called = self.check(pool, {v: {"alive": False}}, pol, plan)
        self.assertTrue(out["checked"])


def ss_world(before, after, pid=4242, refuse=False):
    """A runner with ``ss``: the listing before the kill (``before``) and after it (``after``); ``-K`` itself prints
    nothing, as an old kernel does (EOPNOTSUPP is skipped silently, iproute2 ``show_one_inet_sock``)."""
    seen = {"killed": False}

    def ss(argv, input):
        if "-K" in argv:
            seen["killed"] = True
            return (0, "", "")
        if refuse:
            return (1, "", "ss: bad filter\n")
        return (0, after if seen["killed"] else before, "")
    r = FakeRunner(binaries={"ss"}).on(["ss"], ss)
    r.on(["systemctl", "show", "-p", "MainPID"], "%d\n" % pid)
    return r


def ss_line(lport, peer="203.0.113.32:443", pid=4242, name="xray"):
    return 'tcp   ESTAB 0      0      10.0.0.5:%d    %s users:(("%s",pid=%d,fd=9))' % (lport, peer, name, pid)


class CutFactTest(RuWorld):
    """З-1, П-1: обрыв ``ss -K`` — по факту и только сокеты процесса xray."""

    def setUp(self):
        super().setUp()
        self.logs = []
        p = mock.patch.object(xray.util, "log_action", side_effect=self.logs.append)
        p.start()
        self.addCleanup(p.stop)

    def test_closed_by_fact(self):
        before = "\n".join([ss_line(40001), ss_line(40002), ss_line(40003, pid=777, name="curl")])
        r = ss_world(before, ss_line(40002))
        self.assertEqual(xray.cut_ru_conns(r, cfg_to(AWG_OB["tag"]), {VLESS_OB["tag"]}), 1)
        kill = [a for a in r.commands(["ss", "-K"])]
        self.assertEqual(len(kill), 1)
        self.assertEqual(kill[0], xray.SS_KILL + ["dst", "203.0.113.32", "and", "dport", "=", ":443", "and", "(",
                                                 "sport", "=", ":40001", "or", "sport", "=", ":40002", ")"])
        self.assertNotIn(":40003", kill[0])                       # another program's socket to the router stays
        self.assertIn("1 of 2 connections of xray closed", self.logs[-1])

    def test_kernel_without_destroy_is_none(self):
        before = "\n".join([ss_line(40001), ss_line(40002)])
        r = ss_world(before, before)                              # rc 0, no words, nothing gone
        self.assertIsNone(xray.cut_ru_conns(r, cfg_to(AWG_OB["tag"]), {VLESS_OB["tag"]}))
        self.assertIn("NOT cut", self.logs[-1])

    def test_nothing_open_is_zero(self):
        r = ss_world("", "")
        self.assertEqual(xray.cut_ru_conns(r, cfg_to(AWG_OB["tag"]), {VLESS_OB["tag"]}), 0)
        self.assertEqual(r.commands(["ss", "-K"]), [])

    def test_mark_filter_and_ss_failure(self):
        r = ss_world(ss_line(40001, peer="203.0.113.31:51820"), "")
        self.assertEqual(xray.cut_ru_conns(r, cfg_to(AWG_OB["tag"]), {AWG_OB["tag"]}), 1)
        self.assertEqual(r.commands(["ss", "-K"])[0][len(xray.SS_KILL):len(xray.SS_KILL) + 3], ["fwmark", "=", "0x1c9"])
        self.assertIsNone(xray.cut_ru_conns(ss_world("", "", refuse=True), cfg_to(AWG_OB["tag"]), {AWG_OB["tag"]}))
        self.assertIsNone(xray.cut_ru_conns(FakeRunner(), cfg_to(AWG_OB["tag"]), {AWG_OB["tag"]}))

    def test_no_owners_shown_falls_back_to_the_address(self):
        plain = "tcp   ESTAB 0      0      10.0.0.5:40001    203.0.113.32:443"
        r = ss_world(plain, "")
        self.assertEqual(xray.cut_ru_conns(r, cfg_to(AWG_OB["tag"]), {VLESS_OB["tag"]}), 1)

    def test_slow_name_is_given_up(self):
        ob = dict(VLESS_OB, settings={"vnext": [{"address": "router.example.com", "port": 443, "users": [{}]}]})

        def slow(*a, **k):
            time.sleep(1.0)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.7", 443))]
        with mock.patch.object(socket, "getaddrinfo", side_effect=slow):
            t0 = time.monotonic()
            self.assertEqual(xray._resolve4("router.example.com", 443, wait=0.2), [])
            self.assertLess(time.monotonic() - t0, 0.9)
            self.assertEqual(xray._cut_filters(ob), [["dst", "198.51.100.7", "and", "dport", "=", ":443"]])


class PruneOnTheCollectorPathTest(RuWorld):
    """П-2: ``exits_tick`` чистит подсказки ``group`` старых записей (удалённая группа) и сохраняет state."""

    def test_exits_tick_prunes(self):
        st = self.world()
        model.gw(st)["exits"]["e-aaaaaaaaaa"] = {"group": "g-dead00"}
        model.gw(st)["exits"]["e-cccccccccc"] = {"group": "g-dead00", "name": "Дача"}
        state.save(st)
        pool = self.pool()
        probed = {"pool": pool, "probed": {}, "legs": None, "plan": {"only": set(), "targets": {}, "http": [],
                                                                     "socks": {}, "idle": set(), "off": set()},
                  "node": {}, "counters": {}}
        st = state.load()
        with mock.patch.object(xray, "apply", return_value={}), mock.patch.object(collect, "_apply_xray",
                                                                                return_value=None):
            try:
                collect.exits_tick(st, self.w.runner, log=lambda *a, **k: None, legs=False, probed=probed)
            except Exception:                                # noqa: BLE001 — the rest of the tick in the test world
                pass
        recs = model.gw(state.load())["exits"]
        self.assertNotIn("e-aaaaaaaaaa", recs)
        self.assertEqual(recs.get("e-cccccccccc"), {"name": "Дача"})


class WindowEndRoleTest(RuWorld):
    """П-3: событие конца окна российской группы — с ролью: «российские сайты закрыты», не «нет выхода в интернет»."""

    def test_group_args_carry_the_role(self):
        st = self.world()
        pool = self.pool()
        dec = {"policy": P.load(st, pool), "groups": {HOME: {"steps": {"d-1": "last_block"}}}}
        a = live.Live()._group_args(HOME, "group_last_step", dec, {}, {HOME: "Дом"})
        self.assertEqual(a.get("role"), "ru")
        text = live._said_s1("group_last_step", a, "ru")
        self.assertNotIn("нет выхода в интернет", text)
        a = live.Live()._group_args("main", "group_last_step", dec, {}, {"main": "Основная"})
        self.assertEqual(a.get("role"), "abroad")
        self.assertIn("нет выхода в интернет", live._said_s1("group_last_step", a, "ru"))

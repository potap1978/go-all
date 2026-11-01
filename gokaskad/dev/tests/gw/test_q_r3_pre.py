"""T-q, перед перепроверочным стендом (REV-q-r3): З-1 (gw apply и российский VLESS), З-2 (metrics-in у узла только
с российскими выходами), З-3 (порчи A, B, C новой пробы), П-1 (перепроверка отказа), П-2 (честный журнал обрыва)."""

import copy
import socket
import struct
import time
from unittest import mock

from kaskad import secrets, state
from kaskad.gw import collect, exits, ops, xray
from kaskad.gw import policy as P
from tests.gw.test_q_r2 import AWG_OB, VLESS_OB, cfg_to
from tests.gw.test_q_r3 import FakeRouter, free_port, ss_line, ss_world
from tests.gw.test_ru_target import HOME, RuWorld


class MemCase(RuWorld):
    def setUp(self):
        super().setUp()
        self._mem = collect.MEM
        collect.MEM = collect.Mem()
        self.addCleanup(setattr, collect, "MEM", self._mem)

    def vless(self):
        return [r for r in self.ru_rows if exits.is_xray(r)][0]


class ApplyKeepsRussianVlessTest(MemCase):
    """З-1: ``gw apply`` не пробует российский VLESS (его проверяет сборщик) — два применения через 70 с не дают
    ложного «упал»."""

    def apply_at(self, t):
        real = exits.probe
        asked = []

        def spy(runner, pool_exits, **kw):
            asked.append(kw.get("only"))
            with mock.patch.object(exits, "observatory_detail", return_value={}):
                return real(runner, pool_exits, **kw)
        with mock.patch.object(exits, "probe", side_effect=spy), \
                mock.patch.object(exits, "_now", return_value=t), mock.patch.object(exits, "_mono", return_value=t):
            ops._apply(state.load(), secrets.load(), self.w.runner, probe=True)
        return asked

    def test_two_applies_70_s_apart(self):
        self.world()
        v = self.vless()
        asked = self.apply_at(1000.0)
        self.assertTrue(asked and asked[0] is not None)
        self.assertNotIn(v["id"], asked[0])                       # left to the collector
        self.assertIn([r for r in self.ru_rows if r.get("kind") == "awg"][0]["id"], asked[0])
        self.apply_at(1070.0)
        rec = exits.load_snapshot()["exits"][v["id"]]
        self.assertIs(rec.get("alive"), True)
        self.assertNotIn("unknown_since", rec)
        st = state.load()
        pool = self.pool()
        snap = exits.load_snapshot()
        with mock.patch.object(exits, "load_snapshot", return_value=snap):
            o = collect._obs(st, pool, snap, {}, 1070.0, 1070.0)[v["id"]]
        self.assertFalse(o.get("silent"))


class MetricsForRussianOnlyTest(RuWorld):
    """З-2: узел только с российскими xray-выходами всё равно получает metrics-in (квота, статистика, «Лениво»)."""

    def test_ru_only_node_has_metrics(self):
        st = self.world()
        pool = [e for e in self.pool() if exits.role_of(e) == "ru"]
        cfg = xray.render(st, secrets.load(), pool, (), exits.decision(st, pool_exits=pool))
        tags = [i.get("tag") for i in cfg["inbounds"]]
        self.assertIn("metrics-in", tags)
        self.assertEqual(cfg.get("metrics"), {"tag": "metrics"})
        self.assertIn({"type": "field", "inboundTag": ["metrics-in"], "outboundTag": "metrics"},
                      cfg["routing"]["rules"])
        self.assertNotIn("observatory", cfg)                      # still never the observatory for gr-
        self.assertIn("stats", cfg)

    def test_awg_only_ru_node_unchanged(self):
        st = self.world()
        pool = [e for e in self.pool() if exits.role_of(e) == "ru" and e.get("kind") == "awg"]
        cfg = xray.render(st, secrets.load(), pool, (), exits.decision(st, pool_exits=pool))
        self.assertNotIn("metrics-in", [i.get("tag") for i in cfg["inbounds"]])


class LazySleepsRussianVlessTest(MemCase):
    """З-3 B: «Лениво» в простое усыпляет годный российский VLESS — проба раз в LAZY_WAKE_S, не каждый такт."""

    def lazy_world(self):
        st = self.world(save=False)
        for g in st["gateway"]["exit_policy"]["groups"]:
            if g.get("id") == HOME:
                g.setdefault("checks", {}).setdefault("freq", {})["mode"] = "lazy"
        state.save(st)
        return state.load()

    def plan(self, st, mono):
        pool = self.pool()
        with mock.patch.object(xray, "probe_port", return_value=free_port()), \
                mock.patch.object(collect, "_health_now", return_value={e["id"]: {"state": "ok", "alive": True}
                                                                       for e in pool}), \
                mock.patch.object(collect, "_busy", return_value=False):
            return collect.probe_plan(st, P.load(st, pool), pool, mono)

    def test_idle_good_vless_is_not_probed(self):
        st = self.lazy_world()
        v = self.vless()
        grp, _ = P.tier_of(P.load(st, self.pool()), v["id"])
        self.assertTrue(P.is_lazy(grp), "the group must be «Лениво» for this test")
        collect.MEM.awg_at[v["id"]] = 1000.0
        for dt in (10.0, P.LAZY_P * 2, collect.LAZY_WAKE_S - 10):  # past the lazy period P, before the wake
            p = self.plan(st, 1000.0 + dt)                         # good, idle
            self.assertIn(v["id"], p["idle"])
            self.assertNotIn(v["id"], p["socks"], dt)
            self.assertNotIn(v["id"], p["only"], dt)
        self.assertIn(v["id"], self.plan(st, 1000.0 + collect.LAZY_WAKE_S)["socks"])


class GraceAfterXrayStartTest(MemCase):
    """З-3 A: «лёг» своей пробы в первые XRAY_GRACE с после старта xray — «неизвестно»."""

    def test_false_right_after_start_is_unknown(self):
        self.world()
        v = self.vless()
        own = {v["id"]: (False, None)}
        with mock.patch.object(exits, "xray_age", return_value=exits.XRAY_GRACE - 5):
            p = exits.probe(self.w.runner, [v], only={v["id"]}, doc={}, own=own)
        self.assertIsNone(p[v["id"]]["alive"])
        with mock.patch.object(exits, "xray_age", return_value=exits.XRAY_GRACE + 5):
            p = exits.probe(self.w.runner, [v], only={v["id"]}, doc={}, own=own)
        self.assertIs(p[v["id"]]["alive"], False)


class WrongAnswerRouter(FakeRouter):
    """Отвечает на DNS чужим номером запроса (или без бита QR)."""

    def __init__(self, mangle):
        self.mangle = mangle
        super().__init__(lambda ip, port: True)

    def one(self, c):
        c.settimeout(3)
        c.recv(3)
        c.sendall(b"\x05\x00")
        req = exits._recv_n(c, 10)
        self.asked.append((socket.inet_ntoa(req[4:8]), struct.unpack(">H", req[8:10])[0]))
        c.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
        n = struct.unpack(">H", exits._recv_n(c, 2))[0]
        ans = self.mangle(exits._recv_n(c, n))
        c.sendall(struct.pack(">H", len(ans)) + ans)


class DnsAnswerCheckedTest(RuWorld):
    """З-3 C: удача — только ответ DNS с тем же номером запроса и битом QR."""

    def probe(self, mangle):
        r = WrongAnswerRouter(mangle)
        self.addCleanup(r.close)
        return exits.socks_probe({"x": (r.port, [("77.88.8.8", 53)], "ya.ru")}, timeout=2)["x"][0]

    def test_same_qid_is_alive(self):
        self.assertIs(self.probe(lambda q: q[:2] + b"\x81\x80" + q[4:]), True)

    def test_other_qid_is_dead(self):
        self.assertIs(self.probe(lambda q: bytes([q[0] ^ 0xFF, q[1]]) + b"\x81\x80" + q[4:]), False)

    def test_no_qr_bit_is_dead(self):
        self.assertIs(self.probe(lambda q: q[:2] + b"\x01\x80" + q[4:]), False)


class RecheckRussianVlessTest(MemCase):
    """П-1: отказ российского VLESS перепроверяется через RECHECK с, как у AWG (признак ``failing``)."""

    def test_failing_after_one_failure(self):
        self.world()
        v = self.vless()
        snap = exits.load_snapshot()
        snap["exits"][v["id"]] = {"alive": True, "since": 0, "fails": 0, "state": "ok", "reasons": []}
        exits.save_snapshot(snap)
        probed = {"pool": self.pool(), "probed": {v["id"]: {"alive": False, "detail_code": "detail.socks_dead"}},
                  "legs": None, "plan": {"only": {v["id"]}, "targets": {}, "http": [], "socks": {v["id"]: ()},
                                         "idle": set(), "off": set()}, "node": {}, "counters": {}}
        with mock.patch.object(xray, "apply", return_value={}), \
                mock.patch.object(collect, "_apply_xray", return_value=None):
            out = collect.exits_tick(state.load(), self.w.runner, log=lambda *a, **k: None, legs=False, probed=probed)
        rec = exits.load_snapshot()["exits"][v["id"]]
        self.assertTrue(rec.get("alive") and int(rec.get("fails") or 0) > 0, rec)
        self.assertTrue(out.get("failing"))


class CutJournalTest(RuWorld):
    """П-2: журнал обрыва говорит правду о способе."""

    def setUp(self):
        super().setUp()
        self.logs = []
        p = mock.patch.object(xray.util, "log_action", side_effect=self.logs.append)
        p.start()
        self.addCleanup(p.stop)

    def test_no_owners_says_any_process(self):
        plain = "tcp   ESTAB 0      0      10.0.0.5:40001    203.0.113.32:443"
        r = ss_world(plain, "")
        self.assertEqual(xray.cut_ru_conns(r, cfg_to(AWG_OB["tag"]), {VLESS_OB["tag"]}), 1)
        self.assertIn("any process: ss shows no owners", self.logs[-1])

    def test_owners_shown_no_mark(self):
        r = ss_world(ss_line(40001), "")
        self.assertEqual(xray.cut_ru_conns(r, cfg_to(AWG_OB["tag"]), {VLESS_OB["tag"]}), 1)
        self.assertNotIn("any process", self.logs[-1])

    def test_name_not_resolved_is_none(self):
        old = copy.deepcopy(cfg_to(AWG_OB["tag"]))         # cfg_to shares VLESS_OB: never change it in place
        for o in old["outbounds"]:
            if o.get("tag") == VLESS_OB["tag"]:
                o["settings"] = {"vnext": [{"address": "router.example.com", "port": 443, "users": [{}]}]}
        r = ss_world(ss_line(40001), "")
        with mock.patch.object(xray, "_resolve4", return_value=[]):
            self.assertIsNone(xray._cut_filters([o for o in old["outbounds"] if o.get("tag") == VLESS_OB["tag"]][0]))
            self.assertIsNone(xray.cut_ru_conns(r, old, {VLESS_OB["tag"]}))
        self.assertIn("NOT cut (the name of a router not resolved)", self.logs[-1])
        self.assertEqual(r.commands(["ss", "-K"]), [])

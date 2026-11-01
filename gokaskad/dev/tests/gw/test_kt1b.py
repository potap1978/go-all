"""Findings of the stand КТ1 of stage 1 (k3/stand/logs/T-KT1.md), part «b»: the traffic of the probes of the exits (Н3),
the times of the events (Н12, Н9), the words (Н2, Н4, Н8, Н11), the table after «rules profile» (Н5)."""

import errno
import json
import selectors
import socket
import time
import unittest
from unittest import mock

from kaskad import state
from kaskad.gw import cli as gcli, collect, exits, exitsubs, health, live, net, ops, policy as P, xray
from kaskad.i18n import tr
from tests.gw.test_collect_s1 import S1World
from tests.gw.test_health import E1, E2, Sim
from tests.gw.test_s1_b6 import CloseRuB6Case


# ── Н3: the traffic of the probes ──────────────────────────────────────────

REAL_CONNECT = exits.connect_through          # GwCase patches it with the fake node: the model takes the real one


# The silent targets — no network is asked: 192.0.2.x (TEST-NET-1) was «silent» only where nothing answers for it; the
# network of the Mac (24.10, a gateway 192.168.131.1) refused it at once and the tests fell (REV-3379 «было до»)
SILENT = ("192.0.2.1", "192.0.2.2", "192.0.2.3")


class _Counting(socket.socket):
    made = []
    silent = set()                  # the sockets whose SYN went to a silent target: never answered, never refused

    def connect_ex(self, addr):
        _Counting.made.append(addr)
        if addr[0] in SILENT:
            _Counting.silent.add(self)
            return errno.EINPROGRESS                                  # nothing leaves the machine
        return super().connect_ex(addr)


class _Selector(selectors.DefaultSelector):
    """A silent socket is never ready: it is not handed to the real selector at all (it waits out its time)."""

    def register(self, fileobj, events, data=None):
        if fileobj in _Counting.silent:
            return None
        return super().register(fileobj, events, data)

    def unregister(self, fileobj):
        if fileobj in _Counting.silent:
            return None
        return super().unregister(fileobj)


class ConnectOneByOneTest(unittest.TestCase):
    """``exits.connect_through`` (Н3): the targets of a leg one after another — a working leg costs ONE connection."""

    def setUp(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(16)
        self.port = self.srv.getsockname()[1]
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.closed = s.getsockname()[1]
        s.close()
        _Counting.made = []
        _Counting.silent = set()
        for p in (mock.patch.object(exits, "_mark", lambda s, m: None),
                  mock.patch.object(exits.socket, "socket", _Counting),
                  mock.patch.object(exits.selectors, "DefaultSelector", _Selector)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.srv.close)

    def test_an_answering_first_target_is_the_only_connection(self):
        got = exits.connect_through([0x102, 0x103], targets=(("127.0.0.1", self.port), ("192.0.2.1", 443),
                                                             ("192.0.2.2", 443)), timeout=2)
        self.assertEqual(got, {0x102: True, 0x103: True})
        self.assertEqual(len(_Counting.made), 2)                    # one per leg, not three

    def test_a_silent_first_target_brings_the_next_after_the_stagger(self):
        t = time.monotonic()
        got = exits.connect_through([0x102], targets=(("192.0.2.1", 443), ("127.0.0.1", self.port)), timeout=2,
                                    stagger=0.3)
        self.assertEqual(got, {0x102: True})
        self.assertGreaterEqual(time.monotonic() - t, 0.25)
        self.assertEqual(len(_Counting.made), 2)

    def test_the_silent_targets_never_reach_the_network(self):
        # a network that refuses TEST-NET-1 at once (the Mac 24.10): the silent target stays silent — the stub answers
        base = _Counting.__mro__[1]                                    # the real socket.socket (patched by setUp)
        real = base.connect_ex

        def refusing(sock, addr):
            return errno.ECONNREFUSED if addr[0] in SILENT else real(sock, addr)
        with mock.patch.object(base, "connect_ex", refusing):
            self.test_a_silent_first_target_brings_the_next_after_the_stagger()

    def test_a_refusal_brings_the_next_at_once(self):
        t = time.monotonic()
        got = exits.connect_through([0x102], targets=(("127.0.0.1", self.closed), ("127.0.0.1", self.port)),
                                    timeout=3, stagger=2)
        self.assertEqual(got, {0x102: True})
        self.assertLess(time.monotonic() - t, 1.5)

    def test_a_silent_leg_gets_every_target_within_the_timeout(self):
        t = time.monotonic()
        got = exits.connect_through([0x102], targets=(("192.0.2.1", 443), ("192.0.2.2", 443), ("192.0.2.3", 443)),
                                    timeout=0.9, stagger=0.2)
        self.assertEqual(got, {0x102: False})
        self.assertLess(time.monotonic() - t, 1.5)
        self.assertEqual(len(_Counting.made), 3)


# The model of the traffic of the probes ON THE WIRE (T-KT1 Н3: tcpdump of the legs of the stand, 2 min; the counters
# of 40 min): ≈ 34 packets every 10 s on a leg AmneziaWG 2.0 (≈ 260 B each on the wire with the obfuscation of the key
# of goAmnezia Pro) — 84 MB/day; 3.1 — the same packets, ≈ 450 B each — 143 MB/day. What makes them (the code of xray
# 26: the observatory keeps the connection of every probe open — no DisableKeepAlives — until connIdle; a TCP keepalive
# every 45 s on every idle socket by default):
OBS_PKTS = 10            # one probe of the observatory: connect 3, GET, the answer, the ACKs, the close at connIdle 4
KA_DEFAULT = 45          # s: xray's TCP keepalive (idle and interval) of a socket without sockopt tcpKeepAlive*
CONN_PKTS = 4            # one TCP connection of the probe «жив»: SYN, SYN-ACK, ACK, RST
HTTP_PKTS = 8            # one response probe of the collector (GET, the first line, RST)
REKEY_S = 120            # a WireGuard handshake every 2 min while packets go
HS_PKTS = {"2.0": 6, "3.1": 11}             # the handshake and the junk (Jc) — and I1–I5 of 3.1
WIRE = {"2.0": 260, "3.1": 450}            # bytes of one packet on the wire (the keys of the stand)
VLESS_PROBE = 12000      # bytes of one probe of the observatory through VLESS/Reality on the wire (T0, §6)
NORM = {"2.0": 60e6, "3.1": 60e6, "vless": 105e6}   # §6 «Стандарт»/«Как раньше»: AWG ≈ 55–60, VLESS ≈ 105 MB/day


class ProbeBudgetTest(S1World):
    """Н3: an hour of the collector (the real ticks, the fake node) and the config of xray it draws -> MB/day per exit;
    above the norm of §6 with the code of the stand (3 connections a probe, two probes of one exit, keepalives)."""

    def setUp(self):
        super().setUp()
        self.awg = next(e for e in exits.pool(self.w.runner) if e["kind"] == "awg")
        self.w.curl.observatory[self.awg["tag"]] = True
        self.w.curl.now = lambda: self.now
        self.w.curl.every = 11                                       # the cycle of xray: 10 s + the probes
        self.http = []
        real = exits.http_probe

        def http(marks, *a, **kw):
            self.http.extend(marks)
            return real(marks, *a, **kw)
        p = mock.patch.object(exits, "http_probe", http)
        p.start()
        self.addCleanup(p.stop)

    def conns_per_probe(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        self.addCleanup(srv.close)
        _Counting.made = []
        tg = [("127.0.0.1", srv.getsockname()[1])] + list(exits.probe_targets("abroad")["tcp"])[1:]
        with mock.patch.object(exits, "_mark", lambda s, m: None), mock.patch.object(exits.socket, "socket", _Counting):
            self.assertEqual(REAL_CONNECT([self.awg["mark"]], tg, timeout=2), {self.awg["mark"]: True})
        return len(_Counting.made)

    def day(self):
        n0, h0, start = len(self.w.connects), len(self.http), self.now
        hour = 3600
        while self.now - start < hour:
            self.round(10.5)                                         # the pause of 10 s + the round itself
        k = 86400.0 / hour
        tcp = k * sum(1 for c in self.w.connects[n0:] if self.awg["mark"] in c)
        http = k * sum(1 for m in self.http[h0:] if m == self.awg["mark"])
        with open(xray.config_path(), encoding="utf-8") as f:
            cfg = json.load(f)
        obs_s = int(str(cfg["observatory"]["probeInterval"]).rstrip("s"))
        obs = 86400.0 / max(obs_s, self.w.curl.every)
        conn_idle = cfg["policy"]["levels"]["0"]["connIdle"]
        ob = next(o for o in cfg["outbounds"] if o.get("tag") == self.awg["tag"])
        so = (ob.get("streamSettings") or {}).get("sockopt") or {}
        ka = so.get("tcpKeepAliveIdle") or KA_DEFAULT
        kas = 0 if (isinstance(ka, int) and ka < 0) else len(range(ka, conn_idle + 1, so.get("tcpKeepAliveInterval")
                                                                       or KA_DEFAULT))
        pkts = obs * (OBS_PKTS + 2 * kas) + tcp * self.conns_per_probe() * CONN_PKTS + http * HTTP_PKTS
        out = dict((line, (pkts + 86400.0 / REKEY_S * HS_PKTS[line]) * WIRE[line]) for line in WIRE)
        out["vless"] = obs * VLESS_PROBE
        return out

    def test_a_day_of_the_probes_of_one_exit_fits_the_norm(self):
        got = self.day()
        for kind, norm in sorted(NORM.items()):
            self.assertLessEqual(got[kind], norm, "%s: %.0f MB/day > %.0f" % (kind, got[kind] / 1e6, norm / 1e6))

    def test_a_failing_leg_is_probed_by_its_own_connection(self):
        """A failure of the observatory is never borrowed: the leg answers the probe of the collector."""
        self.round()
        self.w.curl.observatory[self.awg["tag"]] = False
        n0 = len(self.w.connects)
        self.round()
        self.round()
        self.assertGreaterEqual(sum(1 for c in self.w.connects[n0:] if self.awg["mark"] in c), 2)

    def test_a_full_outage_still_falls_in_about_a_minute(self):
        """The borrowed good probe of the observatory adds one round at most: «упал» ≈ 1 min as before (§6)."""
        for _ in range(15):
            self.round(10.5)
        t0 = self.now
        self.w.leg(2, probe=False)
        self.w.curl.observatory[self.awg["tag"]] = False
        dead = None
        while dead is None and self.now - t0 < 200:
            self.round(5 if exits.alive_ids(exits.load_snapshot()) and collect.MEM.awg_failed else 10.5)
            if self.awg["id"] not in exits.alive_ids(exits.load_snapshot()):
                dead = self.now - t0
        self.assertIsNotNone(dead)
        self.assertLessEqual(dead, 80)
        self.assertGreaterEqual(dead, 40)

    def test_a_good_probe_of_the_observatory_stands_for_one_round_only(self):
        self.w.curl.every = None
        self.w.curl.try_at = int(self.now)                         # a frozen probe of xray
        n0 = len(self.w.connects)
        for _ in range(4):
            self.round()
        self.assertGreaterEqual(sum(1 for c in self.w.connects[n0:] if self.awg["mark"] in c), 3)

    def test_the_worst_case_of_the_borrowed_probe_falls_within_75_s(self):
        """REV-s1-kt1b З1: the last good probe of the observatory stays «fresh» up to P + 2 s after the outage and is
        borrowed once — «упал» ≈ 60 s, at worst one round more (≈ 71 s), never two (the same probe, or a later one of
        a silent observatory, is not borrowed again). The observatory probes through the same leg: after the outage
        its last good probe (made just before) is all it has to say «alive» — kept for ``lag`` s, then it fails."""
        got = {}
        for lag in (0, 12, 30):
            self.setUp_again()
            for _ in range(15):
                self.round(10.5)
            t0 = self.now
            self.w.leg(2, probe=False)                               # the leg dies
            self.w.curl.try_at = int(t0 // self.w.curl.every * self.w.curl.every)   # its last good probe, frozen
            dead = None
            while dead is None and self.now - t0 < 200:
                if self.now - t0 >= lag:                             # the observatory's next probes fail
                    self.w.curl.try_at = None
                    self.w.curl.observatory[self.awg["tag"]] = False
                self.round(5 if collect.MEM.awg_failed else 10.5)
                if self.awg["id"] not in exits.alive_ids(exits.load_snapshot()):
                    dead = self.now - t0
            got[lag] = dead
        self.assertLessEqual(got[0], 65, got)
        for lag in (12, 30):
            self.assertIsNotNone(got[lag], got)
            self.assertLessEqual(got[lag], 75, got)
        self.assertEqual(got[12], got[30], got)                     # a fresh one is borrowed once, an old one never

    def setUp_again(self):
        collect.MEM.reset()
        self.w.leg(2)
        self.w.curl.try_at = None
        self.w.curl.observatory[self.awg["tag"]] = True
        self.w.curl._probes.clear()
        for _ in range(4):                                         # good again, published «ok»
            self.round(10.5)

    def test_the_gateway_outbound_has_no_keepalive_the_parser_one_stays(self):
        e = self.awg
        so = e["outbound"]["streamSettings"]["sockopt"]
        self.assertEqual(so["tcpKeepAliveIdle"], exits.AWG_KEEPALIVE_IDLE)
        self.assertGreater(exits.AWG_KEEPALIVE_IDLE, 300)
        parser = exits.load_parser()
        self.assertNotIn("tcpKeepAliveIdle", parser.awg_outbound({}, "x", 2)["streamSettings"]["sockopt"])


class VouchGuardsTest(unittest.TestCase):
    """REV-s1-kt1b З2, П1: each guard of ``collect.vouched_awg`` alone — the last own probe failed (``awg_failed``: the
    «half alive» exit — the observatory answers cp.cloudflare.com:80, our own 1.1.1.1/8.8.8.8/9.9.9.9:443 does not, our
    own decides), the freshness (not older than P + CHECK_SLACK), one probe of the observatory — once (P longer than
    its cycle), the targets of the group those of the observatory («abroad»), a failure never borrowed."""
    NOW = 1790000000

    def setUp(self):
        collect.MEM.reset()
        self.addCleanup(collect.MEM.reset)
        p = mock.patch.object(exits, "_now", lambda: self.NOW)
        p.start()
        self.addCleanup(p.stop)
        self.pool = [{"id": "e1", "kind": "awg", "tag": "gx-e1", "mark": 0x101}]

    def plan(self, period=10, role="abroad"):
        return {"targets": {"e1": exits.probe_targets(role)["tcp"]}, "period": {"e1": period}}

    def doc(self, ago, alive=True):
        rec = {"outbound_tag": "gx-e1", "last_try_time": self.NOW - ago, "delay": 120}
        if alive:
            rec["alive"] = True
        return {"observatory": {"gx-e1": rec}}

    def vouched(self, plan, doc):
        return collect.vouched_awg(None, self.pool, plan, doc)

    def test_a_good_fresh_probe_stands_for_ours(self):
        self.assertEqual(self.vouched(self.plan(), self.doc(3)), {"e1"})

    def test_the_last_own_probe_failed_our_own_decides(self):
        collect.MEM.awg_failed.add("e1")
        self.assertEqual(self.vouched(self.plan(), self.doc(1)), set())

    def test_an_old_probe_is_not_borrowed(self):
        self.assertEqual(self.vouched(self.plan(), self.doc(10 + exits.CHECK_SLACK + 1)), set())
        self.assertEqual(self.vouched(self.plan(), self.doc(-5)), set())          # from the future: a clock jump
        self.assertEqual(self.vouched(self.plan(), self.doc(10 + exits.CHECK_SLACK)), {"e1"})

    def test_one_probe_of_the_observatory_once(self):
        plan, doc = self.plan(period=120), self.doc(30)                           # «Редко»: P over its cycle
        self.assertEqual(self.vouched(plan, doc), {"e1"})
        self.assertEqual(self.vouched(plan, doc), set())
        self.assertEqual(self.vouched(plan, self.doc(20)), {"e1"})                # a new probe of it

    def test_other_targets_of_the_group_decide_by_our_own(self):
        self.assertNotEqual(exits.probe_targets("ru")["tcp"], exits.probe_targets("abroad")["tcp"])
        self.assertEqual(self.vouched(self.plan(role="ru"), self.doc(3)), set())

    def test_a_failure_is_never_borrowed(self):
        self.assertEqual(self.vouched(self.plan(), self.doc(3, alive=False)), set())
        self.assertEqual(self.vouched(self.plan(), None), set())


class AwgAloneMetricsTest(S1World):
    """REV-s1-kt1b П5: a pool of AmneziaWG alone and the xray of the gateway silent (it carries dns-in of such a pool
    too, so silent means failed or stopped) — its metrics (a curl) are asked at most once in OBS_QUIET_S, not every
    round; once xray answers, every round again (the probe of the observatory stands for ours)."""

    def test_no_curl_every_round_while_xray_is_silent(self):
        real_pool, real_dv = exits.pool, exits.debug_vars
        self.silent, self.asked = True, 0

        def dv(runner):
            self.asked += 1
            return None if self.silent else real_dv(runner)
        for p in (mock.patch.object(exits, "pool", lambda *a, **kw: [e for e in real_pool(*a, **kw)
                                                                       if e.get("kind") == "awg"]),
                  mock.patch.object(exits, "debug_vars", dv)):
            p.start()
            self.addCleanup(p.stop)
        awg = next(e for e in exits.pool(self.w.runner) if e["kind"] == "awg")
        self.w.curl.observatory[awg["tag"]] = True
        self.w.curl.now = lambda: self.now
        t0 = self.now
        for _ in range(30):
            self.round(10.5)
        span = self.now - t0
        self.assertGreater(self.asked, 0)
        self.assertLessEqual(self.asked, int(span // collect.OBS_QUIET_S) + 2, span)
        self.silent, n1 = False, self.asked                          # xray answers again: asked within OBS_QUIET_S
        for _ in range(int(collect.OBS_QUIET_S // 10.5) + 4):
            self.round(10.5)
        self.assertGreaterEqual(self.asked - n1, 3)


# ── Н12: back from «в запасе» ──────────────────────────────────────────────

class BackFromStandbyTest(unittest.TestCase):
    def standby_and_back(self):
        s = Sim("standard", exits=(E1, E2))

        def tick(t, ok1):
            s.tick(t, {E1: {"ok": ok1, "rtt": 50 if ok1 else None, "at": t}, E2: {"ok": True, "rtt": 50, "at": t}})
        t = 0
        while t <= 200:
            tick(t, True)
            t += 10
        for _ in range(3):                                          # three outages of 90 s: unstable
            end = t + 90
            while t <= end:
                tick(t, False)
                t += 5
            end = t + 210
            while t <= end:
                tick(t, True)
                t += 10
        self.assertEqual(s.state(E1), "standby")
        while s.first("exit_recovered") is None:
            tick(t, True)
            t += 12                                                 # a slow node: rounds 12 s apart
        return s, tick, t

    def test_a_15_s_outage_right_after_the_return_is_no_fall(self):
        s, tick, t = self.standby_and_back()
        t0 = t
        for dt in (0, 12):                                          # two good rounds after the return
            tick(t0 + dt, True)
        t = t0 + 24
        for dt in (0, 5, 10, 15):                                   # 15 s without an answer, rechecks every 5 s
            tick(t + dt, False)
        tick(t + 20, True)
        self.assertIsNone(s.first("exit_dead", after=t0))
        self.assertEqual(s.state(E1), "ok")

    def test_a_full_outage_after_the_return_falls_in_about_a_minute(self):
        s, tick, t = self.standby_and_back()
        t0 = t + 24
        tick(t, True)
        tick(t + 12, True)
        x = t0
        while s.first("exit_dead", after=t0) is None and x < t0 + 200:
            tick(x, False)
            x += 5                                                  # a failing leg: the recheck in 5 s
        took = s.first("exit_dead", after=t0) - t0
        self.assertGreaterEqual(took, 45)
        self.assertLessEqual(took, 75)


# ── Н9: the outage of the node ─────────────────────────────────────────────

class OutageFeedTest(unittest.TestCase):
    def test_a_closed_outage_is_one_line_of_the_feed(self):
        evs = [{"id": 1, "kind": "exit_dead"}, {"id": 2, "kind": "node_offline"}, {"id": 3, "kind": "node_offline"},
               {"id": 4, "kind": "node_online"}, {"id": 5, "kind": "node_offline"}]
        self.assertEqual([e["id"] for e in live.feed_events(evs)], [1, 4, 5])      # 5: the outage goes on
        self.assertEqual(live.feed_events([]), [])

    def test_no_summary_of_a_window_while_the_node_is_offline(self):
        lv = live.Live()
        calls = []
        with mock.patch.object(lv, "_window_end", lambda *a, **kw: calls.append(a)), \
                mock.patch.object(lv, "publish", lambda *a, **kw: None):
            for off in (True, False):
                try:
                    lv.full({}, None, {}, {"node_offline": off, "events": []}, [], now=1790000000.0)
                except Exception:                                   # noqa: BLE001 — only the call matters here
                    pass
        self.assertEqual(len(calls), 1)


# ── Н2, Н4, Н8, Н11: the words ─────────────────────────────────────────────

class WordsTest(unittest.TestCase):
    def test_never_more_failed_checks_than_checks(self):
        e = {"id": "e-1", "index": 1, "tag": "gx-e-1", "kind": "awg", "mark": 0x101, "iface": "kawg1"}
        row = exits.public(e, {}, {"exits": {"e-1": {"alive": False, "fails": 5, "fail_since": 1.0}}})
        self.assertEqual((row["fails"], row["dies_after"]), (5, 5))
        row = exits.public(e, {}, {"exits": {"e-1": {"alive": True, "fails": 2, "checks": 9}}})
        self.assertEqual(row["dies_after"], 9)

    def test_hysteria2_insecure_gets_the_words_of_quic(self):
        self.assertEqual(exitsubs._skip_code({"code": "insecure", "line": "hy2://p@198.51.100.7:443?insecure=1#x"}),
                         "insecure_quic")
        self.assertEqual(exitsubs._skip_code({"code": "insecure", "line": "vless://u@198.51.100.7:443"}), "insecure")
        why = exitsubs.skip_why(["insecure_quic"])
        self.assertEqual(why, tr("gw_exits.problem.key_insecure_quic", proto="Hysteria2"))
        self.assertNotIn(tr("gw_exitsubs.skip.insecure"), why)
        self.assertIn("pinSHA256", why)
        self.assertEqual(exitsubs._held_codes([], ["hy2://p@198.51.100.7:443?insecure=1#x"]), ["insecure_quic"])

    def test_reserve_words(self):
        now = 1790000000.0
        rec = {"state": "standby", "reasons": ["unstable"], "unstable": True, "unstable_until": now + 600,
               "falls": [now - 100, now - 200, now - 300]}
        got = ops.reserve_text(rec, now)
        self.assertTrue(got.startswith("в запасе: нестабилен (3 падения"), got)
        self.assertEqual(ops.reserve_text({"state": "standby", "reasons": ["manual_standby"]}, now), "в запасе")
        self.assertTrue(ops.reserve_text({"state": "ok", "unstable": True, "unstable_until": now + 60}, now)
                        .startswith("нестабилен · до "))
        self.assertEqual(ops.reserve_text({"state": "ok"}, now), "")


class CliWordsTest(S1World):
    def test_a_route_to_a_group_by_its_name(self):
        st = state.load()
        doc = P.load(st)
        doc["groups"].append({"id": "g-5a1e00", "name": "Стриминг", "role": "abroad", "tiers": [{"exits": []}]})
        with mock.patch.object(P, "load", lambda *a, **kw: doc):
            got = gcli._route_text({"via": "group:g-5a1e00", "fail": "direct"}, st=st)
        self.assertTrue(got.startswith(tr("gw_trace.via.group", name="Стриминг")), got)
        self.assertNotIn("group:", got)

    def test_an_exit_in_reserve_is_not_working(self):
        self.round()
        snap = exits.load_snapshot()
        rec = snap["exits"][self.aid]
        rec.update(state="standby", reasons=["unstable"], unstable=True, unstable_until=self.now + 600,
                   falls=[self.now - 60, self.now - 120, self.now - 180])
        exits.save_snapshot(snap)
        row = next(r for r in ops.exits_view(self.w.runner) if r["id"] == self.aid)
        self.assertTrue(row["text"].startswith("в запасе: нестабилен"), row["text"])


# ── Н5: «rules profile» puts the table back ────────────────────────────────

class XportsBackTest(CloseRuB6Case):
    def test_rules_profile_brings_back_a_lost_gw_xports(self):
        self.socks_world()
        t = self.w.nft.tables["kaskad"]
        del t["sets"][net.XPORTS]
        t["chains"]["gw_out"]["rules"] = [r for r in t["chains"]["gw_out"]["rules"] if net.XPORTS not in str(r)]
        rc, out, err = self.run_cli("rules", "profile", "user", "Мама", "all_exit")
        self.assertEqual(rc, 0, err)
        t = self.w.nft.tables["kaskad"]
        self.assertIn(net.XPORTS, t["sets"])
        self.assertTrue(any("@%s" % net.XPORTS in r["text"] and "reject" in r["text"]
                            for r in t["chains"]["gw_out"]["rules"]))


if __name__ == "__main__":
    unittest.main()

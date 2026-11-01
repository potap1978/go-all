"""BC1 (k3/handoff/SPEC-browser-channel.md §4.3–§4.7, §5.1, §8.1): the logic of the browser test without HTTP —
``kaskad.panel.speed``. Time is a parameter everywhere: the tests move it by hand."""

import json
import os
import threading
from unittest import mock

from kaskad import paths, sysrun, tz
from kaskad.panel import speed
from tests.helpers import RootCase

MB = 1000 * 1000
MIB = 1 << 20
GB = 1000 * MB
T0 = 1_760_000_000.0            # unix time of the tests (a fixed day of the node)


def lim(**kw):
    out = dict(speed.DEFAULTS)
    out.update(kw)
    return out


class Words(object):
    """The journal of a Speed: (key, kw) of every line."""

    def __init__(self):
        self.lines = []

    def __call__(self, key, **kw):
        self.lines.append((key, kw))

    def keys(self):
        return [k for k, _ in self.lines]


class SpeedCase(RootCase):
    def setUp(self):
        super().setUp()
        self.log = Words()
        self.cpu_value = None
        self.days = {}
        self.sp = self.make()

    def make(self):
        return speed.Speed(day=lambda now: self.days.get(int(now // 86400), "2026-10-19"),
                           to_midnight=lambda now: 1234, cpu=lambda: self.cpu_value, log=self.log)

    def doc(self):
        with open(speed.store_path(), encoding="utf-8") as f:
            return json.load(f)


# ── §5.1 limits ─────────────────────────────────────────────────────────────

class LimitsTest(RootCase):
    def st(self, value):
        return {"gateway": {"hostmon": {"speed": value}}}

    def test_no_key_is_every_default_without_notes(self):
        for st in ({}, {"gateway": {}}, {"gateway": {"hostmon": {}}}, None, []):
            self.assertEqual(speed.limits(st), (speed.DEFAULTS, []))

    def test_every_bad_value_is_its_default_or_its_bound(self):
        for bad in (0, -1, 10 ** 15, "5", True, 1.5, None):
            got, notes = speed.limits(self.st({"tests_hour": bad, "tests_day": bad, "side_bytes": bad,
                                               "day_bytes": bad}))
            for k, (lo, hi) in speed.CLAMPS.items():
                v = got[k]
                self.assertIs(type(v), int, (bad, k))
                if isinstance(bad, int) and not isinstance(bad, bool):
                    self.assertEqual(v, lo if bad < lo else hi, (bad, k))
                else:
                    self.assertEqual(v, speed.DEFAULTS[k], (bad, k))
            self.assertEqual(len(notes), 4, (bad, notes))

    def test_good_values_stay_an_extra_key_is_noted_and_ignored(self):
        got, notes = speed.limits(self.st({"tests_hour": 12, "tests_day": 1, "side_bytes": 50 * MB,
                                           "day_bytes": 50 * GB, "extra": 1}))
        self.assertEqual(got, {"tests_hour": 12, "tests_day": 1, "side_bytes": 50 * MB, "day_bytes": 50 * GB})
        self.assertEqual(notes, ["extra: unknown"])
        self.assertEqual(speed.limits(self.st("x"))[0], speed.DEFAULTS)

    def test_a_foreign_key_name_reaches_the_journal_only_by_safe_letters(self):
        # SEC П-1: a key from a foreign .kbk must not forge a journal line
        bad = "x\n[2026-10-19 03:00:00] пароль панели"
        got, notes = speed.limits(self.st({bad: 1, "\r\n": 2, "ok_Key9": 3}))
        self.assertEqual(got, speed.DEFAULTS)
        self.assertEqual(sorted(notes), ["?: unknown", "ok_Key9: unknown", "x20261019030000: unknown"])
        for n in notes:
            self.assertRegex(n, r"^[A-Za-z0-9_?]+: unknown$")

    def test_one_journal_line_per_change_of_the_state(self):
        log = Words()
        with mock.patch.object(speed, "_NOTED", [None]):
            st = self.st({"tests_hour": 0})
            for _ in range(3):
                speed.note_limits(st, speed.limits(st)[1], log)
            self.assertEqual(log.keys(), ["panel.speed.log.limits"])
            st2 = self.st({"tests_hour": 99})
            speed.note_limits(st2, speed.limits(st2)[1], log)
            self.assertEqual(len(log.lines), 2)
            speed.note_limits(st2, [], log)
            self.assertEqual(len(log.lines), 2)


# ── §4.4 the upload by the server's clock ──────────────────────────────────

def simulate(rate_bps, streams=4, ramp_step_ms=300, start_ms=500, end_ms=8000, req_bytes=4 * MIB, cut=True):
    """Buckets of 100 ms of ``streams`` overlapping uploads: stream i starts at start + i × step, each sends
    ``req_bytes`` requests back to back at its share of the link (the link is shared by the open streams); at
    ``end_ms`` every stream is cut in the middle of its request. -> (buckets, up_first, up_ramp, up_last)."""
    buckets = [0] * speed.BUCKETS_MAX
    first = last = None
    ramp = None
    step = 1                                         # ms
    left = {}
    most = 0
    t = start_ms
    while t < end_ms:
        open_now = [i for i in range(streams) if start_ms + i * ramp_step_ms <= t]
        for i in open_now:
            left.setdefault(i, req_bytes)
        if len(open_now) > most:
            most, ramp = len(open_now), float(t)
        per = rate_bps / 8.0 / 1000.0 * step / max(1, len(open_now))
        for i in open_now:
            n = min(per, left[i])
            left[i] -= n
            if left[i] <= 0:
                left[i] = req_bytes                  # the next request of this stream
            buckets[int(t // 100)] += n
            if n:
                first = t if first is None else first
                last = t
        t += step
    return [int(b) for b in buckets], float(first), ramp, float(last)


class UpRateTest(RootCase):
    def test_four_overlapping_streams_ramp_and_a_cut_end_within_five_percent(self):
        for rate in (1e6, 5e6, 87e6, 940e6):
            b, f, r, l = simulate(rate)
            got = speed.up_rate(b, f, r, l)
            self.assertIsNotNone(got["bps"], rate)
            self.assertLess(abs(got["bps"] - rate) / rate, 0.05, (rate, got["bps"]))
            self.assertEqual(got["window_ms"], len(got["buckets"]) * 100)
            self.assertGreaterEqual(got["window_ms"], 2000)

    def test_the_window_starts_after_first_plus_2s_and_ramp_plus_1s_and_drops_the_partial_last(self):
        b = [1000] * 100
        got = speed.up_rate(b, 150.0, 1450.0, 5050.0)
        # start = max(2150, 2450) -> the first whole bucket 25; up_last 5050 -> bucket 50 partial -> the end 49
        self.assertEqual(got["window_ms"], (49 - 25 + 1) * 100)
        self.assertEqual(got["bps"], 1000 * 8 * 10)

    def test_a_window_under_2s_has_no_number_and_no_upload_is_null(self):
        b = [5000] * 60
        got = speed.up_rate(b, 0.0, 0.0, 3900.0)
        self.assertIsNone(got["bps"])
        self.assertLess(got["window_ms"], 2000)
        self.assertIsNone(speed.up_rate(None, None, None, None))
        self.assertEqual(speed.up_rate([0] * 10, 0.0, 0.0, 500.0), {"bps": None, "window_ms": 0, "buckets": []})


# ── §4.5 the processor ─────────────────────────────────────────────────────

class CpuTest(RootCase):
    def test_cpu_stat_of_the_own_cgroup_v2(self):
        self.write("/proc/self/cgroup", "0::/system.slice/kaskad-panel.service\n")
        self.write("/sys/fs/cgroup/system.slice/kaskad-panel.service/cpu.stat",
                   "usage_usec 100\nnr_throttled 3\nthrottled_usec 777\n")
        self.assertEqual(speed.cpu_throttled(), 777)

    def test_no_file_v1_or_odd_lines_are_none(self):
        self.assertIsNone(speed.cpu_throttled())
        self.write("/proc/self/cgroup", "12:cpu,cpuacct:/system.slice/kaskad-panel.service\n")
        self.assertIsNone(speed.cpu_throttled())
        self.write("/proc/self/cgroup", "0::/../../etc\n")
        self.assertIsNone(speed.cpu_throttled())
        self.assertIsNone(speed.parse_cpu_stat("throttled_usec x\n"))

    def test_limited_by_sides(self):
        self.assertIs(speed.cpu_limited((10.0, 0), (12.0, 200001)), True)       # 10 % of 2 s = 200 000 µs
        self.assertIs(speed.cpu_limited((10.0, 0), (12.0, 200000)), False)
        self.assertIsNone(speed.cpu_limited((10.0, None), (12.0, 5)))
        self.assertIsNone(speed.cpu_limited(None, (12.0, 5)))
        self.assertIsNone(speed.cpu_limited((12.0, 0), (12.0, 5)))


# ── §4.7 how it was measured ───────────────────────────────────────────────

def ip_runner(get=None, default=None, rc=0, v6_get=None, v6_default=None):
    r = sysrun.FakeRunner()
    r.on(["ip", "-j", "-6", "route", "get"], (rc, json.dumps(v6_get if v6_get is not None else get), "err" if rc else ""))
    r.on(["ip", "-j", "-6", "route", "show", "default"],
         (0, json.dumps(v6_default if v6_default is not None else default), ""))
    r.on(["ip", "-j", "route", "get"], (rc, json.dumps(get), "err" if rc else ""))
    r.on(["ip", "-j", "route", "show", "default"], (0, json.dumps(default), ""))
    return r


DEFAULT = [{"dst": "default", "gateway": "203.0.113.1", "dev": "eth0", "flags": []}]


class ViaTest(RootCase):
    def test_via_by_listener(self):
        self.assertEqual(speed.via_of("local", lambda: True), "tunnel")
        self.assertEqual(speed.via_of("public", lambda: True), "direct")
        self.assertEqual(speed.via_of("proxy", lambda: False), "proxy")
        self.assertEqual(speed.via_of("proxy", lambda: True), "front")

        def boom():
            raise OSError("no systemctl")
        self.assertEqual(speed.via_of("proxy", boom), "proxy")

    def test_the_default_device_is_not_a_vpn(self):
        r = ip_runner([{"dst": "198.51.100.7", "gateway": "203.0.113.1", "dev": "eth0"}], DEFAULT)
        self.assertIs(speed.via_vpn("198.51.100.7", [], [], r), False)
        self.assertIn(["ip", "-j", "route", "get", "198.51.100.7"], r.commands())
        self.assertEqual(r.unmatched, [])

    def test_awg0_and_a_foreign_wg1_are_this_node(self):
        for dev in ("awg0", "wg1", "kgw0"):
            r = ip_runner([{"dst": "10.66.0.5", "dev": dev, "prefsrc": "10.66.0.1"}], DEFAULT)
            self.assertEqual(speed.via_vpn("10.66.0.5", [], [], r), "self", dev)

    def test_local_route_is_this_node_v4_and_v6_with_no_listen_addresses(self):
        r = ip_runner([{"type": "local", "dst": "203.0.113.5", "dev": "lo"}], DEFAULT)
        self.assertEqual(speed.via_vpn("203.0.113.5", [], [], r), "self")
        r = ip_runner(None, None, v6_get=[{"type": "local", "dst": "2001:db8::5", "dev": "lo"}],
                      v6_default=[{"dst": "default", "dev": "eth0"}])
        self.assertEqual(speed.via_vpn("2001:db8::5", [], [], r), "self")
        self.assertIn(["ip", "-j", "-6", "route", "get", "2001:db8::5"], r.commands())

    def test_ipv6_by_the_default_of_its_family(self):
        r = ip_runner(None, None, v6_get=[{"dst": "2001:db8::9", "dev": "awg0"}],
                      v6_default=[{"dst": "default", "dev": "eth0"}])
        self.assertEqual(speed.via_vpn("2001:db8::9", [], [], r), "self")
        r = ip_runner(None, None, v6_get=[{"dst": "2001:db8::9", "dev": "eth0"}],
                      v6_default=[{"dst": "default", "nexthops": [{"dev": "eth0"}, {"dev": "eth1"}]}])
        self.assertIs(speed.via_vpn("2001:db8::9", [], [], r), False)

    def test_own_address_and_a_cascade_node(self):
        r = ip_runner([{"dst": "198.51.100.20", "dev": "eth0"}], DEFAULT)
        self.assertEqual(speed.via_vpn("203.0.113.5", ["203.0.113.5"], [], r), "self")
        self.assertEqual(r.calls, [])                                      # the own address: no ip at all
        self.assertEqual(speed.via_vpn("198.51.100.20", [], ["198.51.100.20"], r), "cascade")
        self.assertEqual(speed.via_vpn("::ffff:198.51.100.20", [], ["198.51.100.20"], r), "cascade")

    def test_an_error_of_ip_is_unknown(self):
        r = ip_runner([], DEFAULT, rc=2)
        self.assertIsNone(speed.via_vpn("198.51.100.7", [], [], r))
        r = sysrun.FakeRunner()
        r.on(["ip"], (0, "not json", ""))
        self.assertIsNone(speed.via_vpn("198.51.100.7", [], [], r))
        r = ip_runner([{"dst": "198.51.100.7", "dev": "eth0"}], [])
        self.assertIsNone(speed.via_vpn("198.51.100.7", [], [], r))           # no default route: not known

    def test_not_an_address_never_runs_ip_and_the_arguments_are_a_list(self):
        r = ip_runner([], DEFAULT)
        for bad in ("1.2.3.4; id", "", "$(reboot)", "1.2.3.4 dev lo", None, "::1%lo"):
            self.assertIsNone(speed.via_vpn(bad, [], [], r), bad)
        self.assertEqual(r.calls, [])
        self.assertIs(speed.via_vpn("127.0.0.1", [], [], r), False)           # the tunnel: the route is not asked
        self.assertEqual(r.calls, [])
        speed.via_vpn("198.51.100.7", [], [], r)
        for argv, _ in r.calls:
            self.assertIsInstance(argv, list)
            self.assertEqual(argv[0], "ip")


# ── §4.3 the lease, the reserve, the counter ───────────────────────────────

class LeaseTest(SpeedCase):
    def test_start_gives_the_rests_of_the_lease_and_writes_the_whole_reserve(self):
        out = self.sp.start("k", lim(), T0, 100.0)
        self.assertRegex(out["id"], r"^[0-9a-f]{32}$")
        self.assertEqual(out["until_s"], 45)
        self.assertEqual(out["limits"], {"down_bytes": 800 * MB, "up_bytes": 800 * MB, "req_down_max": 256 * MIB,
                                         "req_up_max": 64 * MIB, "streams_max": 4, "side_s_max": 10})
        self.assertEqual((out["tests_left_hour"], out["tests_left_day"]), (5, 29))
        self.assertEqual(out["left_day_bytes"], 10 * GB - 1600 * MB)
        d = self.doc()
        self.assertEqual(d["2026-10-19"], {"rx": 800 * MB, "tx": 800 * MB, "n": 1})
        self.assertEqual(d["starts"], [T0])
        self.assertNotIn("ip", json.dumps(d))
        self.assertEqual(oct(os.stat(speed.store_path()).st_mode & 0o777), "0o600")

    def test_a_second_start_is_409_with_retry_after(self):
        self.sp.start("k", lim(), T0, 100.0)
        with self.assertRaises(speed.Refusal) as c:
            self.sp.start("other", lim(), T0 + 1, 101.0)
        self.assertEqual((c.exception.status, c.exception.kind), (409, "busy"))
        self.assertEqual(c.exception.data, {"busy": True, "retry_after": 9})        # quiet 10 s since the start

    def test_hour_with_limit_used_and_retry_after(self):
        for i in range(6):
            out = self.sp.start("k", lim(), T0 + i * 60, 100.0 + i * 60)
            L = self.sp.lease("k", out["id"], T0, 100.5 + i * 60)
            self.sp.moved(L, "down", 2 * MB)                                  # a real test: never given back
            self.sp.finish("k", out["id"], T0 + i * 60 + 1, 101.0 + i * 60)
        with self.assertRaises(speed.Refusal) as c:
            self.sp.start("k", lim(), T0 + 400, 500.0)
        e = c.exception
        self.assertEqual((e.status, e.kind), (429, "hour"))
        self.assertEqual((e.data["reason"], e.data["limit"], e.data["used"]), ("hour", 6, 6))
        self.assertEqual(e.data["retry_after"], 3600 - 400)                  # the first start leaves the hour
        out = self.sp.start("k", lim(tests_hour=7), T0 + 400, 500.0)
        self.assertEqual(out["tests_left_hour"], 0)

    def test_day_and_traffic_limits(self):
        doc = {"2026-10-19": {"rx": 0, "tx": 0, "n": 30}, "starts": [], "refunds": []}
        self.write(speed.store_path()[len(self.root):], json.dumps(doc))
        with self.assertRaises(speed.Refusal) as c:
            self.sp.start("k", lim(), T0, 100.0)
        self.assertEqual((c.exception.status, c.exception.kind), (429, "day"))
        self.assertEqual(c.exception.data, {"reason": "day", "limit": 30, "used": 30, "retry_after": 1234})
        doc["2026-10-19"] = {"rx": 5 * GB, "tx": 4960 * MB, "n": 3}
        self.write(speed.store_path()[len(self.root):], json.dumps(doc))
        with self.assertRaises(speed.Refusal) as c:
            self.sp.start("k", lim(), T0, 100.0)
        self.assertEqual(c.exception.data, {"reason": "traffic", "limit": 10 * GB, "used": 9960 * MB,
                                            "retry_after": 1234})

    def test_start_at_9_9_gb_gets_at_most_0_1_gb(self):
        doc = {"2026-10-19": {"rx": 5 * GB, "tx": 4900 * MB, "n": 3}, "starts": [], "refunds": []}
        self.write(speed.store_path()[len(self.root):], json.dumps(doc))
        out = self.sp.start("k", lim(), T0, 100.0)
        self.assertEqual(out["limits"]["down_bytes"] + out["limits"]["up_bytes"], 100 * MB)
        self.assertEqual(out["left_day_bytes"], 0)
        self.assertEqual(self.doc()["2026-10-19"]["rx"] + self.doc()["2026-10-19"]["tx"], 10 * GB)

    def test_the_key_binds_the_lease(self):
        out = self.sp.start(speed.bind_key({"token": "abc"}), lim(), T0, 100.0)
        self.sp.lease(speed.bind_key({"token": "abc"}), out["id"], T0, 101.0)
        for key in ("tunnel", speed.bind_key({"token": "abd"})):
            with self.assertRaises(speed.Refusal) as c:
                self.sp.lease(key, out["id"], T0, 101.0)
            self.assertEqual(c.exception.status, 403)
        with self.assertRaises(speed.Refusal) as c:
            self.sp.lease(speed.bind_key({"token": "abc"}), "f" * 32, T0, 101.0)
        self.assertEqual(c.exception.status, 403)
        for bad in (None, "", "x" * 32, out["id"].upper(), out["id"] + "0"):
            with self.assertRaises(speed.Refusal):
                self.sp.lease(speed.bind_key({"token": "abc"}), bad, T0, 101.0)
        self.assertEqual(speed.bind_key({"via": "tunnel", "ip": "127.0.0.1"}), "tunnel")
        self.assertEqual(speed.bind_key({"via": "password", "token": "abc"}),
                         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_45_s_end_and_410_then_quiet_10_s_only_without_open_streams(self):
        out = self.sp.start("k", lim(), T0, 100.0)
        L = self.sp.lease("k", out["id"], T0, 101.0)
        self.sp.take(L, "down", MIB, T0, 101.0)
        # an open stream 12 s without new requests: the lease lives
        self.assertIs(self.sp.lease("k", out["id"], T0, 113.0), L)
        self.sp.release(L, "down", MIB, MIB, T0, 113.0)
        self.assertIs(self.sp.lease("k", out["id"], T0, 122.9), L)
        with self.assertRaises(speed.Refusal) as c:
            self.sp.lease("k", out["id"], T0, 133.0)                       # 10 s quiet
        self.assertEqual((c.exception.status, c.exception.data), (410, {"expired": True}))
        with self.assertRaises(speed.Refusal) as c:
            self.sp.finish("k", out["id"], T0, 133.5)
        self.assertEqual(c.exception.status, 410)
        out = self.sp.start("k", lim(), T0 + 40, 140.0)
        L = self.sp.lease("k", out["id"], T0, 141.0)
        self.sp.take(L, "up", MIB, T0, 141.0)
        with self.assertRaises(speed.Refusal) as c:
            self.sp.lease("k", out["id"], T0, 185.0)                       # 45 s with a stream open
        self.assertEqual(c.exception.status, 410)
        with self.assertRaises(speed.Refusal) as c:
            self.sp.lease("other", out["id"], T0, 185.0)                   # another key: 403, not 410
        self.assertEqual(c.exception.status, 403)

    def test_reserve_four_downloads_of_256_mib_with_300_mib_left(self):
        doc = {"2026-10-19": {"rx": 0, "tx": 0, "n": 0}, "starts": [], "refunds": []}
        self.write(speed.store_path()[len(self.root):], json.dumps(doc))
        out = self.sp.start("k", lim(side_bytes=300 * MIB), T0, 100.0)
        L = self.sp.lease("k", out["id"], T0, 100.5)
        ok, refused = [], []
        barrier = threading.Barrier(4)

        def one():
            barrier.wait()
            try:
                self.sp.take(L, "down", 256 * MIB, T0, 101.0)
                ok.append(1)
            except speed.Refusal as e:
                refused.append(e)
        th = [threading.Thread(target=one) for _ in range(4)]
        for t in th:
            t.start()
        for t in th:
            t.join()
        self.assertEqual(len(ok), 1)
        self.assertEqual([(e.status, e.data["reason"], e.data["left"]) for e in refused],
                         [(429, "budget", 44 * MIB)] * 3)
        # what was not sent comes back
        self.sp.release(L, "down", 256 * MIB, 6 * MIB, T0, 102.0)
        self.assertEqual(L.left["down"], 294 * MIB)

    def test_429_streams_and_429_budget_each_by_its_reason(self):
        out = self.sp.start("k", lim(side_bytes=50 * MB), T0, 100.0)
        L = self.sp.lease("k", out["id"], T0, 100.5)
        for _ in range(4):
            self.sp.take(L, "up", MIB, T0, 101.0)
        with self.assertRaises(speed.Refusal) as c:
            self.sp.take(L, "up", 1, T0, 101.0)
        self.assertEqual((c.exception.status, c.exception.data), (429, {"reason": "streams"}))
        self.sp.take(L, "down", MIB, T0, 101.0)                              # the other side has its own streams
        with self.assertRaises(speed.Refusal) as c:
            self.sp.take(L, "down", 50 * MB, T0, 101.0)
        self.assertEqual((c.exception.status, c.exception.data["reason"]), (429, "budget"))
        self.assertEqual(c.exception.data["left"], 50 * MB - MIB)

    def test_finish_corrects_the_day_and_repeats_the_same_answer_60_s(self):
        out = self.sp.start("k", lim(), T0, 100.0)
        L = self.sp.lease("k", out["id"], T0, 100.5)
        self.sp.take(L, "down", 8 * MIB, T0, 101.0)
        self.sp.moved(L, "down", 5 * MIB)
        self.sp.release(L, "down", 8 * MIB, 5 * MIB, T0, 102.0)
        res = self.sp.finish("k", out["id"], T0 + 10, 110.0)
        self.assertEqual((res["bytes_down"], res["bytes_up"], res["up"]), (5 * MIB, 0, None))
        self.assertEqual(res["cpu_limited"], {"down": None, "up": None})
        self.assertEqual(self.doc()["2026-10-19"], {"rx": 0, "tx": 5 * MIB, "n": 1})
        self.assertEqual(self.sp.finish("k", out["id"], T0 + 30, 160.0), res)
        with self.assertRaises(speed.Refusal) as c:
            self.sp.finish("other", out["id"], T0 + 30, 160.0)
        self.assertEqual(c.exception.status, 403)
        with self.assertRaises(speed.Refusal) as c:
            self.sp.finish("k", out["id"], T0 + 80, 171.0)                     # after 60 s: no such lease
        self.assertEqual(c.exception.status, 403)
        with self.assertRaises(speed.Refusal) as c:
            self.sp.lease("k", out["id"], T0 + 11, 111.0)                      # closed by finish: 403
        self.assertEqual(c.exception.status, 403)
        done = [kw for k, kw in self.log.lines if k == "panel.speed.log.done"]
        self.assertEqual(done, [{"down": "5.2", "up": "0.0", "secs": 10}])

    def test_finish_before_release_gives_back_only_the_unmoved_reserve_once(self):
        # REV С-1: the client cuts its requests and sends finish (§6.2 п. 3, 6); the server sees the cut later
        out = self.sp.start("k", lim(), T0, 100.0)
        L = self.sp.lease("k", out["id"], T0, 100.5)
        for _ in range(4):
            self.sp.take(L, "up", 64 * MIB, T0, 101.0)
        self.sp.take(L, "down", 256 * MIB, T0, 101.0)
        self.sp.got(L, 10, 10000000, 102.0, 103.0)
        self.sp.moved(L, "down", 7 * MIB)
        res = self.sp.finish("k", out["id"], T0 + 5, 105.0)
        self.assertEqual((res["bytes_up"], res["bytes_down"]), (10000000, 7 * MIB))
        self.assertEqual(self.doc()["2026-10-19"], {"rx": 4 * 64 * MIB, "tx": 256 * MIB, "n": 1})
        for used in (4000000, 3000000, 2000000, 1000000):
            self.sp.release(L, "up", 64 * MIB, used, T0 + 6, 106.0)
        self.sp.release(L, "down", 256 * MIB, 7 * MIB, T0 + 6, 106.0)
        self.assertEqual(self.doc()["2026-10-19"], {"rx": 10000000, "tx": 7 * MIB, "n": 1})
        self.assertEqual((L.open, L.held), ({"down": 0, "up": 0}, {"down": 0, "up": 0}))
        # a repeated release (even with a bigger reserve) never takes the day below what really moved
        self.sp.release(L, "up", 64 * MIB, 0, T0 + 7, 107.0)
        self.sp.release(L, "down", 10 ** 12, 0, T0 + 7, 107.0)
        self.assertEqual(self.doc()["2026-10-19"], {"rx": 10000000, "tx": 7 * MIB, "n": 1})
        self.assertEqual(L.open, {"down": 0, "up": 0})

    def test_a_release_after_the_lease_gives_back_no_more_than_the_open_requests_hold(self):
        out = self.sp.start("k", lim(), T0, 100.0)
        L = self.sp.lease("k", out["id"], T0, 100.5)
        self.sp.take(L, "up", 8 * MIB, T0, 101.0)
        self.sp.take(L, "up", 8 * MIB, T0, 101.0)
        self.sp.got(L, 10, 2 * MB, 102.0, 102.0)
        self.sp.finish("k", out["id"], T0 + 3, 103.0)
        self.sp.release(L, "up", 8 * MIB, 2 * MB, T0 + 4, 104.0)
        self.sp.release(L, "up", 8 * MIB, 2 * MB, T0 + 4, 104.0)    # the second request — but it moved nothing
        self.assertEqual(self.doc()["2026-10-19"]["rx"], 2 * MB + 2 * MB)
        # a third (a repeat): nothing
        self.sp.release(L, "up", 8 * MIB, 0, T0 + 4, 104.0)
        self.assertEqual(self.doc()["2026-10-19"]["rx"], 4 * MB)

    def test_a_release_gives_back_at_most_what_the_side_holds_and_nothing_without_an_open_request(self):
        # REV-bc12-2 П-1: the promise is per side (one release per request is the caller's part), not per request
        out = self.sp.start("k", lim(), T0, 100.0)
        L = self.sp.lease("k", out["id"], T0, 100.5)
        self.sp.take(L, "up", 8 * MIB, T0, 101.0)
        self.sp.take(L, "up", 8 * MIB, T0, 101.0)
        self.sp.got(L, 10, 2 * MB, 102.0, 102.0)
        self.sp.finish("k", out["id"], T0 + 3, 103.0)
        self.sp.release(L, "up", 10 ** 12, 2 * MB, T0 + 4, 104.0)          # a wrong reserve: the held at most
        self.sp.release(L, "up", 8 * MIB, 0, T0 + 4, 104.0)                # the side holds nothing more
        self.assertEqual(self.doc()["2026-10-19"]["rx"], 2 * MB)
        self.assertEqual((L.open["up"], L.held["up"]), (0, 0))
        self.sp.release(L, "up", 8 * MIB, 0, T0 + 5, 105.0)                # no open request: nothing at all
        self.assertEqual(self.doc()["2026-10-19"]["rx"], 2 * MB)
        self.assertEqual((L.open["up"], L.held["up"]), (0, 0))

    def test_the_journal_counts_the_seconds_to_the_end_of_the_lease_not_to_a_late_reap(self):
        # REV П-1: a test of 2 s, the tab closed, the next call an hour later
        out = self.sp.start("k", lim(), T0, 100.0)
        L = self.sp.lease("k", out["id"], T0, 101.0)
        self.sp.take(L, "down", 8 * MIB, T0, 101.0)
        self.sp.moved(L, "down", 5 * MIB)
        self.sp.release(L, "down", 8 * MIB, 5 * MIB, T0, 102.0)
        self.sp.budget(lim(), T0 + 3600, 3700.0)
        done = [kw for k, kw in self.log.lines if k == "panel.speed.log.done"]
        self.assertEqual(done, [{"down": "5.2", "up": "0.0", "secs": 12}])

    def test_take_on_a_closed_lease_is_refused_and_reserves_nothing(self):
        out = self.sp.start("k", lim(), T0, 100.0)
        L = self.sp.lease("k", out["id"], T0, 100.5)
        self.sp.finish("k", out["id"], T0 + 1, 101.0)
        with self.assertRaises(speed.Refusal) as c:
            self.sp.take(L, "down", MIB, T0 + 2, 102.0)
        self.assertEqual(c.exception.status, 403)
        self.assertEqual((L.open, L.held), ({"down": 0, "up": 0}, {"down": 0, "up": 0}))
        out = self.sp.start("k", lim(), T0 + 10, 110.0)
        L = self.sp.lease("k", out["id"], T0 + 10, 110.5)
        with self.assertRaises(speed.Refusal) as c:
            self.sp.take(L, "up", MIB, T0 + 60, 160.0)                     # ended by time: 410
        self.assertEqual(c.exception.status, 410)
        self.assertEqual(L.open, {"down": 0, "up": 0})

    def test_a_crash_in_the_middle_keeps_the_whole_reserve(self):
        self.sp.start("k", lim(), T0, 100.0)
        sp2 = self.make()                                                     # the panel restarted
        self.assertEqual(self.doc()["2026-10-19"], {"rx": 800 * MB, "tx": 800 * MB, "n": 1})
        out = sp2.start("k", lim(), T0 + 5, 5.0)
        self.assertTrue(out["id"])
        self.assertEqual(self.doc()["2026-10-19"]["n"], 2)
        self.assertEqual(len(self.doc()["starts"]), 2)

    def test_failed_starts_are_given_back_six_an_hour(self):
        for i in range(7):
            out = self.sp.start("k", lim(), T0 + i * 10, 100.0 + i * 20)
            self.sp.finish("k", out["id"], T0 + i * 10 + 1, 101.0 + i * 20)
        d = self.doc()
        self.assertEqual(d["2026-10-19"]["n"], 1)                             # the seventh stays
        self.assertEqual(len(d["starts"]), 1)
        self.assertEqual(len(d["refunds"]), 6)
        self.assertEqual(d["2026-10-19"]["rx"] + d["2026-10-19"]["tx"], 0)
        # a start that moved ≥ 1 MB is never given back
        out = self.sp.start("k", lim(), T0 + 100, 300.0)
        L = self.sp.lease("k", out["id"], T0, 300.5)
        self.sp.moved(L, "down", 2 * MB)
        self.sp.finish("k", out["id"], T0 + 101, 301.0)
        self.assertEqual(self.doc()["2026-10-19"]["n"], 2)

    def test_the_counter_of_starts_survives_a_restart(self):
        for i in range(6):
            out = self.sp.start("k", lim(), T0 + i * 60, 100.0 + i * 60)
            L = self.sp.lease("k", out["id"], T0, 100.5 + i * 60)
            self.sp.moved(L, "down", 2 * MB)
            self.sp.finish("k", out["id"], T0 + i * 60 + 1, 101.0 + i * 60)
        sp2 = self.make()
        with self.assertRaises(speed.Refusal) as c:
            sp2.start("k", lim(), T0 + 400, 5.0)
        self.assertEqual(c.exception.kind, "hour")
        out = sp2.start("k", lim(), T0 + 3601, 10.0)                            # the first start left the hour
        self.assertTrue(out["id"])

    def test_35_days_broken_file_and_atomic_write(self):
        doc = dict(("2026-%02d-%02d" % (8 + i // 28, 1 + i % 28), {"rx": 1, "tx": 1, "n": 1}) for i in range(40))
        doc.update({"starts": [], "refunds": []})
        self.write(speed.store_path()[len(self.root):], json.dumps(doc))
        self.sp.start("k", lim(), T0, 100.0)
        d = self.doc()
        days = speed.days_of(d)
        self.assertEqual(len(days), 35)
        self.assertIn("2026-10-19", days)
        self.assertNotIn("2026-08-01", days)
        self.assertEqual([n for n in os.listdir(paths.panel_dir()) if n.startswith(".tmp")], [])
        self.write(speed.store_path()[len(self.root):], "{not json")
        sp2 = self.make()
        self.sp = sp2
        out = sp2.start("k", lim(), T0 + 3, 1000.0)
        self.assertTrue(out["id"])
        self.assertIn("panel.speed.log.counterBroken", self.log.keys())
        self.assertEqual(self.doc()["2026-10-19"]["n"], 1)
        for bad in ('{"2026-10-19": {"rx": -1, "tx": 0, "n": 0}}', '{"2026-10-19": {"rx": true}}', "[]",
                    '{"starts": "x"}', '{"bogus": 1}'):
            self.assertTrue(speed.parse_counts(bad)[1], bad)
        self.assertEqual(speed.parse_counts(None), ({"starts": [], "refunds": []}, False))

    def test_a_write_that_fails_refuses_the_start(self):
        with mock.patch.object(speed.util, "atomic_write", side_effect=OSError("read-only")):
            with self.assertRaises(speed.Refusal) as c:
                self.sp.start("k", lim(), T0, 100.0)
        self.assertEqual((c.exception.status, c.exception.kind), (500, "store"))
        self.assertIsNone(self.sp._lease)
        self.assertIn("panel.speed.log.counterUnsaved", self.log.keys())

    def test_the_day_of_the_start_by_kaskad_tz(self):
        real = speed.Speed(log=self.log, cpu=lambda: None)                  # the real day of the node: tz.day
        night = 1_760_907_599.0                                             # 2025-10-19 20:59:59 UTC = 23:59:59 +03
        with mock.patch.object(tz, "zone", return_value=tz.Zone.fixed("Test/Plus3", 3 * 3600)):
            self.assertEqual(speed._to_midnight(night), 1)
            out = real.start("k", lim(), night, 100.0)
            self.assertEqual(out["tests_left_day"], 29)
            real.finish("k", out["id"], night + 2, 102.0)                    # the end on the next day …
            out = real.start("k", lim(), night + 3, 103.0)
            d = self.doc()
        self.assertEqual(sorted(speed.days_of(d)), ["2025-10-19", "2025-10-20"])
        self.assertEqual(d["2025-10-19"], {"rx": 0, "tx": 0, "n": 0})        # … corrects the day of its start
        self.assertEqual(d["2025-10-20"]["n"], 1)
        self.assertEqual(out["tests_left_day"], 29)

    def test_cpu_limited_by_sides_from_snapshots(self):
        out = self.sp.start("k", lim(), T0, 100.0)
        L = self.sp.lease("k", out["id"], T0, 100.0)
        self.cpu_value = 0
        self.sp.take(L, "down", MIB, T0, 101.0)                               # the first down
        self.sp.release(L, "down", MIB, MIB, T0, 103.0)
        self.cpu_value = 900000                                               # 0.9 s of 2 s throttled
        self.sp.take(L, "up", MIB, T0, 103.0)                                 # the first up
        self.sp.release(L, "up", MIB, MIB, T0, 104.0)
        self.cpu_value = 950000                                               # 0.05 s of 2 s
        res = self.sp.finish("k", out["id"], T0, 105.0)
        self.assertEqual(res["cpu_limited"], {"down": True, "up": False})


class StreamTest(SpeedCase):
    """send_down / recv_up with a fake clock and fake sockets."""

    def lease(self, **kw):
        out = self.sp.start("k", lim(**kw), T0, 100.0)
        return out, self.sp.lease("k", out["id"], T0, 100.0)

    def test_down_sends_exactly_n_random_bytes_repeating_only_every_mib(self):
        out, L = self.lease()
        n = 3 * MIB + 12345
        self.sp.take(L, "down", n, T0, 100.0)
        got = bytearray()
        clock = Clock(100.0)
        sent = speed.send_down(self.sp, L, n, lambda data, timeout=None: got.extend(data), clock, lambda: T0)
        self.assertEqual((sent, len(got)), (n, n))
        self.assertEqual(bytes(got[:MIB]), bytes(got[MIB:2 * MIB]))
        self.assertNotEqual(bytes(got[:MIB]), b"\0" * MIB)
        for step in (1 << 12, 1 << 16, 1 << 18, 1 << 19):
            self.assertNotEqual(bytes(got[:step]), bytes(got[step:2 * step]), step)
        self.assertEqual(L.moved["down"], n)
        self.assertEqual((L.open["down"], L.left["down"]), (0, 800 * MB - n))

    def test_down_stops_at_the_deadline_and_gives_back_the_rest(self):
        out, L = self.lease()
        self.sp.take(L, "down", 256 * MIB, T0, 100.0)
        clock = Clock(100.0)
        timeouts = []

        def write(data, timeout=None):
            timeouts.append(timeout)
            clock.t += 1.0                                                # a slow client: 1 s per 64 KiB
        sent = speed.send_down(self.sp, L, 256 * MIB, write, clock, lambda: T0)
        self.assertEqual(sent, 15 * speed.CHUNK)
        self.assertLessEqual(max(timeouts), 15.0)
        self.assertEqual((L.open["down"], L.left["down"]), (0, 800 * MB - sent))

    def test_down_a_client_gone_gives_the_slot_and_the_reserve_back(self):
        out, L = self.lease()
        self.sp.take(L, "down", 8 * MIB, T0, 100.0)
        calls = [0]

        def write(data, timeout=None):
            calls[0] += 1
            if calls[0] > 3:
                raise BrokenPipeError()
        with self.assertRaises(BrokenPipeError):
            speed.send_down(self.sp, L, 8 * MIB, write, Clock(100.0), lambda: T0)
        self.assertEqual((L.open["down"], L.left["down"], L.moved["down"]), (0, 800 * MB - 3 * speed.CHUNK,
                                                                               3 * speed.CHUNK))

    def test_down_stops_when_the_lease_is_closed(self):
        out, L = self.lease()
        self.sp.take(L, "down", 8 * MIB, T0, 100.0)
        n = [0]

        def write(data, timeout=None):
            n[0] += 1
            if n[0] == 2:
                self.sp.finish("k", out["id"], T0, 101.0)
        sent = speed.send_down(self.sp, L, 8 * MIB, write, Clock(100.0), lambda: T0)
        self.assertEqual(sent, 2 * speed.CHUNK)

    def test_up_stops_at_the_end_of_the_lease_not_15_s_after_its_start(self):
        out, L = self.lease()
        self.sp.take(L, "down", MIB, T0, 100.0)                           # an open download keeps the lease
        self.sp.take(L, "up", 64 * MIB, T0, 135.0)                        # the lease ends at 145
        clock = Clock(135.0)
        got = speed.recv_up(self.sp, L, Reader([(1000, 1.0)] * 30, clock, 64 * MIB), clock, lambda: T0)
        self.assertEqual(got, 10 * 1000)
        self.assertEqual(clock.t, 145.0)

    def test_up_stops_when_the_lease_is_closed(self):
        out, L = self.lease()
        self.sp.take(L, "up", 64 * MIB, T0, 100.0)
        clock = Clock(100.0)
        sp = self.sp

        class Closing(Reader):
            def readinto1(self, mv, timeout=None):
                if len(self.steps) == 28:                                  # before the third read
                    sp.finish("k", out["id"], T0, clock.t)
                return Reader.readinto1(self, mv, timeout)
        got = speed.recv_up(self.sp, L, Closing([(1000, 0.1)] * 30, clock, 64 * MIB), clock, lambda: T0)
        self.assertEqual(got, 3 * 1000)
        self.assertEqual(L.open["up"], 0)

    def test_up_reads_once_per_socket_read_into_buckets_of_their_time(self):
        out, L = self.lease()
        self.sp.take(L, "up", 1000, T0, 100.0)
        clock = Clock(100.0)
        reader = Reader([(300, 0.05), (300, 0.1), (400, 0.2)], clock, 1000)
        got = speed.recv_up(self.sp, L, reader, clock, lambda: T0)
        self.assertEqual(got, 1000)
        self.assertEqual(L.buckets[0:4], [300, 300, 0, 400])
        self.assertAlmostEqual(L.up_first, 50.0, places=3)
        self.assertAlmostEqual(L.up_last, 350.0, places=3)
        self.assertEqual(L.moved["up"], 1000)
        self.assertEqual((L.open["up"], L.left["up"]), (0, 800 * MB - 1000))

    def test_up_a_client_gone_or_reset_is_quiet_and_gives_the_rest_back(self):
        out, L = self.lease()
        self.sp.take(L, "up", 4000, T0, 100.0)
        clock = Clock(100.0)
        got = speed.recv_up(self.sp, L, Reader([(1000, 0.01)], clock, 4000), clock, lambda: T0)   # then EOF
        self.assertEqual(got, 1000)
        self.assertEqual((L.open["up"], L.left["up"]), (0, 800 * MB - 1000))
        self.sp.take(L, "up", 4000, T0, 100.0)
        got = speed.recv_up(self.sp, L, Reader([(1000, 0.01), ConnectionResetError()], clock, 4000), clock,
                            lambda: T0)
        self.assertEqual(got, 1000)
        self.assertEqual(L.open["up"], 0)

    def test_up_ramp_is_the_first_time_the_most_streams_were_open(self):
        out, L = self.lease()
        for i, t in enumerate((101.0, 101.3, 101.6)):
            self.sp.take(L, "up", 10, T0, t)
        self.sp.release(L, "up", 10, 10, T0, 102.0)
        self.sp.take(L, "up", 10, T0, 102.5)                                 # three again: not a new ramp
        self.assertAlmostEqual(L.up_ramp, 1600.0, places=3)
        self.assertEqual(L.up_most, 3)


class Clock(object):
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Reader(object):
    """A BodyReader of steps (n, seconds the read took) or an exception; then 0 (EOF)."""

    def __init__(self, steps, clock, length):
        self.steps = list(steps)
        self.clock = clock
        self.left = length

    def readinto1(self, mv, timeout=None):
        if not self.steps:
            return 0
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        n, dt = step
        self.clock.t += dt
        self.left -= n
        return n

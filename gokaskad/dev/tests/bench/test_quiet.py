"""«Свободен» and «под нагрузкой» of the server check (SPEC-node-score-schedule-share NS-S8, §11.1 п. 17, 29–31; the
coordinator's batch of 18.10 З-1): the thresholds 2 Mbit/s and 20 %, OWN_SLACK and H, the larger of rx and tx, the
tunnels left out, the devices of the gateway only as an extra source, steal and iowait never «busy», no accounting —
``known: false``; the readers of /proc and of ``systemctl show`` on their samples."""

import os
import unittest
from unittest import mock

from kaskad.bench import quiet
from tests.helpers import RootCase

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "bench")
MB = 1e6 / 8                            # bytes per second of 1 Mbit/s


def fx(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def sample(t, rx=0, tx=0, rxp=0, txp=0, busy=0, total=0, unit=None, links=None, dev=None, in_unit=True, family=None,
           io=None, ncpu=None):
    return {"t": t, "ifs": {"eth0": (rx, rxp, tx, txp)}, "links": links or {"eth0": {"h": 14, "mtu": "1500"}},
            "cpu": (busy, total), "io": io, "ncpu": ncpu, "unit": unit, "dev": dev, "family": family,
            "in_unit": in_unit}


def unit(rx=0, tx=0, rxp=0, txp=0, cpu_ns=0):
    return {"rx": rx, "tx": tx, "rxp": rxp, "txp": txp, "cpu_ns": cpu_ns}


def window(people_mbps=0.0, cpu_pct=0.0, s=300, own_mbps=0.0, own_pps=0.0, iface_extra=0.0, h=14, mtu=1500,
           family=None, steal_pct=0.0, iowait_pct=0.0, own_cpu_pct=0.0, ncpu=2):
    """Two samples ``s`` apart: people + the unit's own traffic (+ ``iface_extra`` × own on the wire, + H per packet)."""
    own_b = own_mbps * MB * s
    wire = people_mbps * MB * s + own_b * (1 + iface_extra) + h * own_pps * s
    total = 100 * s * ncpu
    busy = (cpu_pct + own_cpu_pct) / 100.0 * total
    a = sample(0, unit=unit(), links={"eth0": {"h": h, "mtu": str(mtu)}}, family=family)
    b = sample(s, rx=int(wire), tx=int(people_mbps * MB * s // 10), rxp=0, busy=int(busy), total=int(total),
               unit=unit(rx=int(own_b), rxp=int(own_pps * s), cpu_ns=int(own_cpu_pct / 100.0 * s * ncpu * 1e9)),
               links={"eth0": {"h": h, "mtu": str(mtu)}}, family=family)
    del steal_pct, iowait_pct                 # never in «busy»: the busy jiffies are user + nice + system only
    return a, b


class SlackTest(unittest.TestCase):
    def test_values_of_the_spec(self):
        self.assertAlmostEqual(quiet.own_slack(14, 1500, 4) * 100, 5.06, places=2)     # Ethernet, 1500, IPv4
        self.assertAlmostEqual(quiet.own_slack(14, 1450, 6) * 100, 6.74, places=2)     # Ethernet, 1450, IPv6
        self.assertAlmostEqual(quiet.own_slack(0, 1420, 4) * 100, 4.30, places=2)      # not Ethernet, 1420, IPv4
        self.assertAlmostEqual(quiet.own_slack(14, 1500), quiet.own_slack(14, 1500, 6))  # unknown family -> 72
        self.assertEqual(quiet.own_slack(14, 9000, 4), quiet.own_slack(14, 1500, 4))   # seg = min(MTU, 1500)
        self.assertEqual(quiet.own_slack(14, "x", 4), quiet.own_slack(14, 1500, 4))

    def test_h(self):
        self.assertEqual(quiet.l2_header("1\n"), 14)
        for t in ("65534", "512", "772", "", "x"):
            self.assertEqual(quiet.l2_header(t), 0, t)


class DecideTest(unittest.TestCase):
    def test_traffic_threshold(self):
        self.assertTrue(quiet.decide(*window(1.9))["free"])
        d = quiet.decide(*window(2.1))
        self.assertFalse(d["free"])
        self.assertAlmostEqual(d["people_mbps"], 2.1, places=2)

    def test_cpu_threshold(self):
        self.assertTrue(quiet.decide(*window(cpu_pct=19))["free"])
        d = quiet.decide(*window(cpu_pct=21))
        self.assertFalse(d["free"])
        self.assertAlmostEqual(d["cpu_pct"], 21.0, places=1)

    def test_own_cpu_taken_off(self):
        d = quiet.decide(*window(cpu_pct=5, own_cpu_pct=95))
        self.assertTrue(d["free"])
        self.assertAlmostEqual(d["cpu_pct"], 5.0, places=1)

    def test_steal_and_iowait_never_busy(self):
        # /proc/stat: steal 30 %, iowait 60 % — the busy jiffies (user + nice + system) stay low
        a = sample(0, busy=0, total=0, unit=unit())
        b = sample(300, busy=int(0.05 * 60000), total=60000, unit=unit())
        self.assertTrue(quiet.decide(a, b)["free"])

    def test_larger_of_rx_and_tx(self):
        a = sample(0, unit=unit())
        b = sample(300, rx=int(0.5 * MB * 300), tx=int(3.0 * MB * 300), total=1, unit=unit())
        d = quiet.decide(a, b)
        self.assertFalse(d["free"])
        self.assertAlmostEqual(d["people_mbps"], 3.0, places=2)

    def test_own_traffic_is_not_load(self):
        # the unit pulls 900 Mbit/s; the interface sees 4.6 % more (GRO/TSO) and + 14 B per packet
        d = quiet.decide(*window(0.0, own_mbps=900, own_pps=75000, iface_extra=0.046, family=4))
        self.assertTrue(d["free"], d)
        self.assertLess(d["people_mbps"], 2)
        # IPv6, MTU 1450, per segment (+ 6.24 %)
        d = quiet.decide(*window(0.0, own_mbps=900, own_pps=75000, iface_extra=0.0624, mtu=1450, family=6))
        self.assertTrue(d["free"], d)
        # the same check + people 5 Mbit/s over the window of 5 min (the channel pulls 900 Mbit/s for 10 s of it) -> busy
        d = quiet.decide(*window(5.0, own_mbps=900 * 10 / 300.0, own_pps=75000 * 10 / 300.0, iface_extra=0.046,
                                 family=4))
        self.assertFalse(d["free"])
        self.assertGreater(d["people_mbps"], 4.5)
        # the price of the slack (NS-S8): while the channel itself pulls, people up to the slack of its traffic hide
        d = quiet.decide(*window(5.0, s=10, own_mbps=900, own_pps=75000, iface_extra=0.046, family=4))
        self.assertLess(d["people_mbps"], 5.0)

    def test_devices_only_add(self):
        # SOCKS5 has no counters (rx null): the interface decides — 30 Mbit/s is busy
        a = sample(0, unit=unit(), dev={"d1": (1000, 1000)})
        b = sample(300, rx=int(30 * MB * 300), total=1, unit=unit(), dev={"d1": (1000, 1000)})
        self.assertFalse(quiet.decide(a, b)["free"])
        # the devices see more than the interface (traffic of people hidden by our own slack) -> they count
        a = sample(0, unit=unit(), dev={"d1": (0, 0)})
        b = sample(300, total=1, unit=unit(), dev={"d1": (int(4 * MB * 300), 0)})
        self.assertAlmostEqual(quiet.decide(a, b)["people_mbps"], 4.0, places=2)

    def test_device_that_came_back_is_not_load(self):
        # REV-ns-b1 С-2: a phone back online carries the counters of its whole session (800 MB); only the devices
        # of both samples count, each by its own difference
        a = sample(0, unit=unit(), dev={"d1": (1000, 1000)})
        b = sample(10, total=1, unit=unit(), dev={"d1": (1000, 1000), "d2": (800 * 10 ** 6, 50 * 10 ** 6)})
        d = quiet.decide(a, b)
        self.assertTrue(d["free"], d)
        self.assertEqual(d["people_mbps"], 0.0)
        # one that went away never counts either; the one that stayed — by its difference
        a = sample(0, unit=unit(), dev={"d1": (0, 0), "d2": (10 ** 9, 10 ** 9)})
        b = sample(300, total=1, unit=unit(), dev={"d1": (int(3 * MB * 300), 0)})
        self.assertAlmostEqual(quiet.decide(a, b)["people_mbps"], 3.0, places=2)

    def test_iowait_is_the_disk_never_free(self):
        # З-9: iowait 25 % of all the time of the cores -> disk_busy; «free» (traffic, CPU) stays as it was
        d = quiet.decide(sample(0, unit=unit(), io=0), sample(10, total=2000, unit=unit(), io=500))
        self.assertEqual((d["io_pct"], d["disk_busy"], d["free"]), (25.0, True, True))
        d = quiet.decide(sample(0, unit=unit(), io=0), sample(10, total=2000, unit=unit(), io=399))
        self.assertFalse(d["disk_busy"])
        d = quiet.decide(sample(0, unit=unit()), sample(10, total=2000, unit=unit()))
        self.assertEqual((d["io_pct"], d["disk_busy"]), (None, False))

    def test_iowait_is_the_share_of_one_core(self):
        # REV-ns-b1-2 придирка 2: one process that waits on the disk on 8 vCPU — 1/8 of all the time of the cores is
        # iowait (12,5 %): as the share of ONE core it is 100 % -> busy; the line «cpu» of /proc/stat sums N cores
        a, b = sample(0, unit=unit(), io=0, ncpu=8), sample(10, total=8000, unit=unit(), io=1000, ncpu=8)
        d = quiet.decide(a, b)
        self.assertEqual((d["io_pct"], d["disk_busy"]), (100.0, True))
        d = quiet.decide(a, sample(10, total=8000, unit=unit(), io=150, ncpu=8))           # 15 % of one core
        self.assertEqual((d["io_pct"], d["disk_busy"]), (15.0, False))
        d = quiet.decide(a, sample(10, total=8000, unit=unit(), io=4000, ncpu=8))          # 4 cores wait: at most 100
        self.assertEqual(d["io_pct"], 100.0)
        self.assertEqual(quiet.cpu_count(fx("proc-stat.txt")), 1)
        self.assertEqual(quiet.cpu_count("cpu  1 2 3 4 5 6 7 8\ncpu0 1\ncpu1 1\ncpu12 1\nintr 1\n"), 3)
        self.assertIsNone(quiet.cpu_count("intr 1\n"))

    def test_counter_back_and_no_accounting(self):
        a = sample(0, rx=10 ** 9, unit=unit())
        b = sample(300, rx=5, total=1, unit=unit())                     # the interface came again
        self.assertTrue(quiet.decide(a, b)["free"])
        a = sample(0, unit=None)
        b = sample(300, total=1, unit=None)
        self.assertFalse(quiet.decide(a, b)["known"])                   # inside a unit without accounting
        self.assertTrue(quiet.decide(sample(0, in_unit=False), sample(300, total=1, in_unit=False))["known"])
        b = sample(300, total=1, unit=dict(unit(), rx=None))
        self.assertFalse(quiet.decide(sample(0, unit=unit()), b)["known"])


class ReadersTest(unittest.TestCase):
    def test_ifaces(self):
        self.assertEqual(quiet.ifaces(fx("proc-net-route.txt"), fx("proc-net-ipv6_route.txt")), ["eth0"])
        self.assertEqual(quiet.ifaces("", ""), [])
        r4 = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n" \
             "ens3\t00000000\t0100000A\t0003\t0\t0\t0\t00000000\n" \
             "tun0\t00000000\t00000000\t0001\t0\t0\t0\t00000000\n" \
             "ens4\t00000000\t0100000A\t0002\t0\t0\t0\t00000000\n"            # not up
        self.assertEqual(quiet.ifaces(r4, ""), ["ens3"])
        # the tunnels of the gateway never count: wg*, awg*, kawg* (the legs to the exits), tun*
        r4 = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n" + "".join(
            "%s\t00000000\t00000000\t0001\t0\t0\t0\t00000000\n" % n for n in ("kawg0", "awg1", "wg0", "tun3", "eth1"))
        self.assertEqual(quiet.ifaces(r4, ""), ["eth1"])

    def test_net_dev_and_stat(self):
        d = quiet.net_dev(fx("proc-net-dev.txt"))
        self.assertEqual(d["eth0"], (9876543210, 7654321, 1234567890, 2345678))
        self.assertIn("lo", d)
        self.assertEqual(quiet.cpu_stat(fx("proc-stat.txt")),
                         (10132153 + 290696 + 3084719,
                          10132153 + 290696 + 3084719 + 46828483 + 16683 + 0 + 25195 + 175628))
        self.assertIsNone(quiet.cpu_stat("intr 1"))
        self.assertEqual(quiet.cpu_iowait(fx("proc-stat.txt")), 16683)
        self.assertIsNone(quiet.cpu_iowait("intr 1"))

    def test_unit_counters(self):
        self.assertEqual(quiet.unit_counters(fx("systemctl-show-unit.txt")),
                         {"rx": 104857600, "tx": 2097152, "rxp": 72000, "txp": 36000, "cpu_ns": 41500000000})
        self.assertEqual(quiet.unit_counters(fx("systemctl-show-noacct.txt")), dict.fromkeys(
            ("rx", "tx", "rxp", "txp", "cpu_ns")))


class MeterTest(RootCase):
    def test_reads_the_files_of_the_node(self):
        for src, dst in (("proc-net-route.txt", "/proc/net/route"), ("proc-net-ipv6_route.txt", "/proc/net/ipv6_route"),
                         ("proc-net-dev.txt", "/proc/net/dev"), ("proc-stat.txt", "/proc/stat")):
            self.write(dst, fx(src))
        self.write("/sys/class/net/eth0/type", "1\n")
        self.write("/sys/class/net/eth0/mtu", "1450\n")
        self.write("/run/kaskad/live.json", '{"devices": {"d1": {"rx": 10, "tx": 20}, "d2": {"rx": null, "tx": null}}}')
        from kaskad import sysrun
        r = sysrun.FakeRunner().on(["systemctl", "show"], fx("systemctl-show-unit.txt"))
        s = quiet.Meter("kaskad-bench-0123456789.service", r).read()
        self.assertEqual(s["links"], {"eth0": {"h": 14, "mtu": "1450"}})
        self.assertEqual(s["ifs"]["eth0"][0], 9876543210)
        self.assertEqual(s["unit"]["rx"], 104857600)
        self.assertEqual(s["dev"], {"d1": (10, 20)})          # by device; one without counters is not there
        self.assertEqual((s["io"], s["ncpu"]), (16683, 1))
        self.assertEqual(r.commands()[0][:3], ["systemctl", "show", "kaskad-bench-0123456789.service"])
        self.assertFalse(r.unmatched)
        self.assertIsNone(quiet.Meter().read()["unit"])                    # before the unit: nothing to take off


class FakeMeter(object):
    def __init__(self, samples):
        self.samples = list(samples)

    def read(self):
        return self.samples.pop(0) if len(self.samples) > 1 else self.samples[0]


class WatchTest(unittest.TestCase):
    def test_window_and_worst(self):
        s = [sample(t * 10, rx=int(t * 10 * 0.5 * MB), total=t * 1000, busy=t * 50, unit=unit()) for t in range(4)]
        s.append(sample(40, rx=int(30 * 0.5 * MB + 10 * 9 * MB), total=4000, busy=200, unit=unit()))
        w = quiet.Watch(FakeMeter(s), sample_s=10, window_s=300)
        for _ in range(5):
            w.tick()
        r = w.result()
        self.assertTrue(r["under_load"])                                # the last 10 s pulled the 40 s over 2
        self.assertTrue(r["known"])
        self.assertEqual(r["n"], 1)                                     # shorter than 5 min: one decision, all of it

    def test_quiet_and_unknown(self):
        quiet_s = [sample(t * 10, total=t * 1000, unit=unit()) for t in range(3)]
        w = quiet.Watch(FakeMeter(quiet_s))
        for _ in range(3):
            w.tick()
        self.assertIs(w.result()["under_load"], False)
        unknown = [sample(t * 10, rx=int(t * 10 * 9 * MB), total=t * 1000, unit=None) for t in range(3)]
        w = quiet.Watch(FakeMeter(unknown))
        for _ in range(3):
            w.tick()
        self.assertIsNone(w.result()["under_load"])                    # load_unknown
        self.assertIsNone(quiet.Watch(FakeMeter(quiet_s)).result()["under_load"])

    def test_window_drops_old_samples(self):
        s = [sample(0, unit=unit()), sample(10, rx=int(10 * 9 * MB), total=1000, unit=unit()),
             sample(400, rx=int(10 * 9 * MB), total=2000, unit=unit()),
             sample(410, rx=int(10 * 9 * MB), total=3000, unit=unit())]
        w = quiet.Watch(FakeMeter(s), window_s=300)
        for _ in range(4):
            w.tick()
        self.assertEqual([x["t"] for x in w.samples], [10, 400, 410])   # 10 starts the window; the busy 0–10 is out
        self.assertIs(w.result()["under_load"], False)

    @staticmethod
    def ticks(samples, **kw):
        w = quiet.Watch(FakeMeter(samples), **kw)
        for _ in range(len(samples)):
            w.tick()
        return w

    def test_burst_in_a_short_check_is_not_load(self):
        # REV-ns-b1 С-1: 3 MB in the first 10 s of a 2-min check, then silence: 0.2 Mbit/s over the check — free
        xs = [sample(0, unit=unit())] + [sample(10 * k, rx=3 * 10 ** 6, total=1000 * k, unit=unit()) for k in range(1, 13)]
        r = self.ticks(xs).result()
        self.assertIs(r["under_load"], False, r)
        self.assertLess(r["people_max"], 0.3)
        # the same 2 min at 3 Mbit/s all the time — under load
        xs = [sample(10 * k, rx=int(3 * MB * 10 * k), total=1000 * k, unit=unit()) for k in range(13)]
        self.assertIs(self.ticks(xs).result()["under_load"], True)

    def test_moving_average_of_five_minutes(self):
        # 8 min; 6 Mbit/s for the first 30 s (0.6 over the first 5 min), then 25 Mbit/s for 10 s at 7:00 (0.83):
        # every full window stays below 2 — free
        def rx_at(t):
            return int(6 * MB * min(t, 30) + 25 * MB * max(0, min(t, 430) - 420))
        xs = [sample(t, rx=rx_at(t), total=100 * t, unit=unit()) for t in range(0, 490, 10)]
        w = self.ticks(xs)
        self.assertIs(w.result()["under_load"], False, w.result())
        self.assertTrue(all(f is False for f, _ in w.decisions[:29]))  # 0–290 s: no full window yet
        self.assertTrue(all(f for f, _ in w.decisions[29:]))
        self.assertEqual(w.decisions[29][1]["span_s"], 300.0)
        # 12 Mbit/s for the first minute: the first full window (0–300 s) has 2.4 — under load
        xs = [sample(t, rx=int(12 * MB * min(t, 60)), total=100 * t, unit=unit()) for t in range(0, 490, 10)]
        self.assertIs(self.ticks(xs).result()["under_load"], True)

    def test_a_failed_sample_is_left_out(self):
        # REV-ns-b1 З-1: the 3rd ``systemctl show`` failed (unit None inside the unit): left out, the load is known
        xs = [sample(10 * k, total=1000 * k, unit=None if k == 2 else unit(rx=k, tx=2 * k, cpu_ns=k))
              for k in range(6)]
        w = self.ticks(xs)
        r = w.result()
        self.assertEqual((r["under_load"], r["known"]), (False, True))
        self.assertEqual(w.last_unit()["rx"], 5)
        # the last one failed: the counters of the check — of the last good sample
        xs = [sample(10 * k, total=1000 * k, unit=None if k == 5 else unit(rx=k)) for k in range(6)]
        w = self.ticks(xs)
        self.assertEqual((w.result()["under_load"], w.last_unit()["rx"]), (False, 4))
        # every sample failed — only then «unknown»; a sample without accounting (a counter not set) is not good
        xs = [sample(10 * k, total=1000 * k, unit=dict(unit(), rx=None)) for k in range(4)]
        w = self.ticks(xs)
        self.assertEqual((w.result()["under_load"], w.result()["known"], w.last_unit()), (None, False, None))

    def test_thread_starts_and_stops(self):
        w = quiet.Watch(FakeMeter([sample(0, unit=unit())]), sample_s=0.01)
        with mock.patch.object(quiet, "_mono", side_effect=range(10 ** 6)):
            w.start()
            w.stop()
        self.assertGreaterEqual(len(w.samples), 1)


if __name__ == "__main__":
    unittest.main()

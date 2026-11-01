"""gw/hostmon.py (SPEC-node-score §2, §2.1, §3.2, §3.5; A2): every rule of §2 by every level that acts — on, held
between the thresholds, off only past «выкл»; «hold», «cover», the reasons of na; the state machine of §2.1 row by
row with «push», «pending» and the antispam; the deltas of the CPU; OOM of the node and of a service by the
cgroups; the IP certificate in hours; the forecast of the disk; the light probes for 1–5 points by series; the light
and the instant rules; hostmon.json; the time only as a parameter; the cost of a round."""

import json
import os
import random
import re
import statistics
import time
import unittest
from unittest import mock

from kaskad import i18n
from kaskad.gw import hostmon as H
from kaskad.gw import live

GIB = 1024 ** 3
A3 = ["1.1.1.1", "8.8.8.8", "77.88.8.8"]
CFG = {"enabled": True, "probes": True, "anchors": list(A3)}
NOW0 = 1790000000.0


def sample(**kw):
    """A healthy round of the collector (what ``derive`` gives), the fields of ``kw`` on top."""
    s = {"busy": 10.0, "steal": 0.5, "iowait": 1.0, "softirq": 0.2, "load_core": 0.2, "mem_pct": 50.0,
         "mem_avail": GIB, "swap_total": GIB, "swap_pct": 5.0, "psi_cpu": 1.0, "psi_mem": 1.0, "psi_io": 1.0,
         "drops": 0, "pkts": 1000, "errs": 0, "rx": 100, "tx": 100, "rx_rate": 10.0, "tx_rate": 10.0, "softnet": 0.0,
         "softnet_n": 0, "retrans": 0, "out_segs": 1000, "listen": 0.0, "listen_n": 0, "conntrack_pct": 10.0,
         "fd_sys": 1.0, "uptime": 100000.0, "virt": "kvm", "absent": [], "minute": False, "hour": False,
         "fs": None, "clock": None, "cg": None, "fd_proc": None, "certs": None, "oom_kill": None}
    s.update(kw)
    return s


def fs(mount="/", size_g=100.0, avail_g=50.0, files=1000, ffree=900):
    return {"mount": mount, "fsid": hash(mount), "size": int(size_g * GIB), "free": int(avail_g * GIB),
            "avail": int(avail_g * GIB), "files": files, "ffree": ffree}


def series(losses=None, n=3, rtt=(20.0, 21.0, 22.0), na=None, excl=None, addrs=A3, rtts=None):
    """One series of hostping: ``losses`` {addr: lost of n}, ``na`` {addr: why}, ``rtts`` {addr: [ms]}."""
    out = []
    for a in addrs:
        if na and a in na:
            out.append({"addr": a, "via": "icmp", "n": 0, "lost": 0, "runs": 0, "rtt": [], "na": na[a]})
            continue
        lost = (losses or {}).get(a, 0)
        r = list((rtts or {}).get(a, rtt))[:max(0, n - lost)]
        out.append({"addr": a, "via": "icmp", "n": n, "lost": lost, "runs": 1 if lost else 0, "rtt": r, "na": None})
    return {"anchors": out, "excl": bool(excl), "why": excl}


class Node(object):
    """Drives ``evaluate`` round by round on its own clocks (10 s a round, every 6th round the minute set)."""

    def __init__(self, cfg=None, **ctx):
        self.cfg = dict(CFG, **(cfg or {}))
        self.ctx = {"owner": "collector", "push": {}, "host_level": "crit", "probes_on": True,
                    "services": {"dns": True, "xray": True}, "inbounds_why": {}, "virt": "kvm", "uptime": 100000.0}
        self.ctx.update(ctx)
        self.st = {}
        self.now, self.mono, self.n = NOW0, 1000.0, 0
        self.minute = {"fs": [fs()], "clock": {"synced": True, "offset_s": 0.001}, "fd_proc": None,
                       "oom_kill": 0, "cg": None}
        self.events = []

    def tick(self, dt=10.0, probes=None, minute=None, hour=False, **kw):
        self.mono += dt
        self.now += dt
        self.n += 1
        is_min = (self.n % 6 == 0) if minute is None else minute
        s = sample(**kw)
        s["mono"] = self.mono
        if is_min:
            s["minute"] = True
            for k, v in self.minute.items():
                s.setdefault(k, v) if k in kw else s.__setitem__(k, v)
        if hour:
            s["hour"] = True
        ctx = dict(self.ctx, probes=probes)
        self.st, evs = H.evaluate(self.st, s, self.cfg, ctx, self.now, self.mono)
        self.events += evs
        return evs

    def run(self, k, **kw):
        out = []
        for _ in range(k):
            out += self.tick(**kw)
        return out

    def minutes(self, k, **kw):
        """``k`` minute rounds in a row (dt 60 s each, the minute set every time)."""
        out = []
        for _ in range(k):
            out += self.tick(dt=60.0, minute=True, **kw)
        return out

    def rs(self, rid):
        return self.st["rules"].get(rid) or {}

    def level(self, rid):
        return self.rs(rid).get("level")

    def said(self, rid):
        return self.rs(rid).get("said")

    def evs(self, rid):
        return [e for e in self.events if e["args"]["check"] == rid]


def ticks(rule):
    return int(H.RULES[rule]["window_s"] // 10) + 3 if H.RULES[rule]["window_s"] else 4


class WindowRulesTest(unittest.TestCase):
    """Every row of §2 with a window or an instant value: on -> held between the thresholds -> off past «выкл»."""

    CASES = [
        ("cpu", "busy", [(90, "warn"), (80, "warn"), (70, "ok")]),
        ("cpu", "busy", [(97, "crit"), (90, "crit"), (80, "warn"), (70, "ok")]),
        ("steal", "steal", [(12, "warn"), (7, "warn"), (3, "ok")]),
        ("iowait", "iowait", [(45, "warn"), (30, "warn"), (10, "ok")]),
        ("psi_cpu", "psi_cpu", [(25, "warn"), (15, "warn"), (5, "ok")]),
        ("psi_mem", "psi_mem", [(25, "warn"), (15, "warn"), (5, "ok")]),
        ("psi_io", "psi_io", [(25, "warn"), (15, "warn"), (5, "ok")]),
        ("softnet", "softnet", [(15, "warn"), (7, "warn"), (2, "ok")]),
        ("listen", "listen", [(2, "warn"), (0.7, "warn"), (0.1, "ok")]),
        ("listen", "listen", [(6, "crit"), (3, "crit"), (1, "warn"), (0.1, "ok")]),
        ("load", "load_core", [(2.5, "warn"), (1.9, "warn"), (1.0, "ok")]),
        ("mem", "mem_pct", [(7, "warn"), (12, "warn"), (20, "ok")]),
        ("swap", "swap_pct", [(93, "warn"), (87, "warn"), (50, "ok")]),
        ("swap", "swap_pct", [(99, "crit"), (96, "crit"), (88, "warn"), (50, "ok")]),
        ("conntrack", "conntrack_pct", [(92, "warn"), (87, "warn"), (50, "ok")]),
        ("conntrack", "conntrack_pct", [(96, "crit"), (92, "crit"), (88, "warn"), (50, "ok")]),
    ]

    def test_on_held_off(self):
        for rid, key, phases in self.CASES:
            node = Node()
            for v, want in phases:
                node.run(ticks(rid), **{key: float(v)})
                self.assertEqual(node.level(rid), want, (rid, phases, v))
                self.assertEqual(H._shown(node.rs(rid)), want, (rid, v))            # the view: pending ?? said

    def test_ratio_rules(self):
        for rid, key, base, phases in (
                ("drops", "drops", "pkts", [(3, "warn"), (1.5, "warn"), (0.5, "ok")]),
                ("drops", "drops", "pkts", [(2, "warn")]),                              # «≥ 2»
                ("nic_err", "errs", "pkts", [(1.4, "warn"), (0.7, "warn"), (0.2, "ok")]),
                ("retrans", "retrans", "out_segs", [(3, "warn"), (1.7, "warn"), (1, "ok")])):
            node = Node()
            for pct, want in phases:
                node.run(ticks(rid), **{key: int(pct * 10), base: 1000})
                self.assertEqual(node.level(rid), want, (rid, pct))

    def test_too_few_packets_say_nothing(self):
        node = Node()
        node.run(ticks("drops"), drops=50, pkts=100)                  # 50 % of 6000 packets in 10 min: < 10 000
        self.assertEqual(node.level("drops"), "ok")
        node.run(ticks("retrans"), retrans=10, out_segs=20)           # Δ OutSegs ≤ 1000 in 5 min
        self.assertEqual(node.level("retrans"), "ok")

    def test_a_draft_crit_gives_warn(self):
        """NS-D11, §10.1 п. 5, 17, 18: a crit with no source does not act — the value past ``crit_draft`` is warn."""
        for rid, kw in (("steal", {"steal": 40.0}), ("mem", {"mem_pct": 3.0}), ("psi_cpu", {"psi_cpu": 70.0}),
                        ("retrans", {"retrans": 80, "out_segs": 1000})):
            node = Node()
            node.run(ticks(rid), **kw)
            self.assertEqual(node.level(rid), "warn", rid)
            self.assertIsNone(H.RULES[rid]["crit"])
            self.assertIsNotNone(H.RULES[rid]["crit_draft"])
        for rid in ("steal", "iowait", "clock", "loss", "mem"):
            self.assertEqual(H.RULES[rid]["alarm"], "warn", rid)

    def test_hold_three_a_single_spike_is_nothing(self):
        node = Node()
        node.run(4)
        node.tick(mem_pct=5.0)
        node.run(2)
        self.assertEqual(node.level("mem"), "ok")
        self.assertEqual(node.evs("mem"), [])
        node.tick(mem_pct=5.0)
        node.tick(mem_pct=5.0)
        self.assertEqual(node.level("mem"), "ok")
        node.tick(mem_pct=5.0)
        self.assertEqual(node.level("mem"), "warn")
        self.assertEqual(len(node.evs("mem")), 1)

    def test_cover_under_80_pct_waits_for_history(self):
        node = Node()
        node.run(40, busy=99.0)                                         # 40 of 60 points of 10 min
        self.assertEqual(node.level("cpu"), "wait")
        self.assertEqual(node.rs("cpu")["why"], "wait")
        node.run(9, busy=99.0)
        self.assertEqual(node.level("cpu"), "crit")
        lt = H.light(node.st, "ru")
        self.assertEqual(lt["level"], "crit")

    def test_slow_rounds_still_cover(self):
        """The round of the collector is its own time + the pause of 10 s: 15 s apart the window still counts."""
        node = Node()
        for _ in range(45):
            node.tick(dt=15.0, busy=90.0)
        self.assertEqual(node.level("cpu"), "warn")

    def test_cpu_says_the_neighbours(self):
        node = Node()
        node.run(ticks("steal"), busy=90.0, steal=8.0)
        f = node.rs("cpu")["find"]
        self.assertEqual(f["key"], "gw_host.find.cpu.warn_steal")
        self.assertEqual(f["args"]["steal"], 8)

    def test_reasons_of_na(self):
        node = Node(virt="lxc")
        node.run(3, virt="lxc", absent=["psi", "conntrack"], psi_cpu=None, psi_mem=None, psi_io=None,
                 conntrack_pct=None, swap_total=0, swap_pct=None)
        self.assertEqual((node.level("steal"), node.rs("steal")["why"]), ("na", "container"))
        self.assertEqual(node.rs("psi_cpu")["why"], "no_psi")
        self.assertEqual(node.rs("conntrack")["why"], "no_conntrack")
        self.assertEqual(node.rs("swap")["why"], "no_swap")
        node = Node(cfg={"probes": False})
        node.run(2)
        for rid in ("loss", "rtt", "anchor"):
            self.assertEqual((node.level(rid), node.rs(rid)["why"]), ("na", "probes_off"))
        node = Node(cfg={"enabled": False})
        node.run(2)
        self.assertEqual(node.rs("cpu")["why"], "off")
        self.assertEqual(H.light(node.st, "ru")["level"], "off")
        node = Node()
        node.minute["fs"] = [fs(files=0, ffree=0)]
        node.run(6)
        self.assertEqual(node.rs("inodes")["why"], "no_inodes")


class DeltaTest(unittest.TestCase):
    """REV С-4: the rule of the deltas of ``derive``."""

    def raw(self, cpu, **kw):
        r = {"mono": kw.pop("mono", 10.0), "cpu": cpu, "cores": 2, "load": [0.1, 0.2, 0.4], "mem": None,
             "net": kw.pop("net", None), "ifaces": kw.pop("ifaces", ["eth0"]), "softnet": kw.pop("softnet", None)}
        r.update(kw)
        return r

    def test_iowait_going_down_makes_no_outlier(self):
        a = self.raw([100, 0, 50, 1000, 500, 0, 10, 5])
        b = self.raw([150, 0, 60, 1100, 400, 0, 12, 6], mono=20.0)        # iowait −100: «may decrease»
        s = H.derive(a, b)
        for k in ("busy", "steal", "iowait", "softirq"):
            self.assertTrue(0 <= s[k] <= 100, (k, s[k]))
        self.assertEqual(s["iowait"], 0.0)
        self.assertAlmostEqual(s["busy"], 100.0 * (50 + 10 + 2) / (50 + 10 + 100 + 2 + 1))

    def test_no_cpu_time_is_no_point(self):
        c = [100, 0, 50, 1000, 500, 0, 10, 5]
        s = H.derive(self.raw(c), self.raw(list(c), mono=20.0))
        self.assertIsNone(s["busy"])
        self.assertEqual(H.derive(None, self.raw(c))["busy"], None)
        self.assertEqual(H.derive(None, self.raw(c))["load_core"], 0.2)

    def test_a_counter_going_down_is_no_point(self):
        s = H.derive(self.raw(None, softnet={"dropped": 100, "squeezed": 50}),
                     self.raw(None, mono=20.0, softnet={"dropped": 3, "squeezed": 50}))
        self.assertIsNone(s["softnet_n"])
        self.assertIsNone(s["softnet"])
        s = H.derive(self.raw(None, softnet={"dropped": 100, "squeezed": 50}),
                     self.raw(None, mono=20.0, softnet={"dropped": 110, "squeezed": 60}))
        self.assertEqual(s["softnet_n"], 20)
        self.assertEqual(s["softnet"], 2.0)

    def test_the_interfaces_of_the_default_route_summed(self):
        def nd(rx, extra=True):
            d = {"eth0": {"rx_bytes": rx, "tx_bytes": 10, "rx_packets": 100, "tx_packets": 100, "rx_drop": 1,
                          "tx_drop": 1, "rx_errs": 0, "tx_errs": 0}}
            if extra:
                d["eth1"] = dict(d["eth0"])
            return d
        s = H.derive(self.raw(None, net=nd(1000), ifaces=["eth0", "eth1"]),
                     self.raw(None, mono=20.0, net=nd(2000), ifaces=["eth0", "eth1"]))
        self.assertEqual(s["rx"], 2000)
        self.assertEqual(s["rx_rate"], 200.0)
        self.assertEqual(s["pkts"], 0)
        s = H.derive(self.raw(None, net=nd(1000, extra=False), ifaces=["eth0", "eth1"]),
                     self.raw(None, mono=20.0, net=nd(2000), ifaces=["eth0", "eth1"]))
        self.assertIsNone(s["rx"])                                           # eth1 was not there before
        s = H.derive(self.raw(None, net=nd(5000)), self.raw(None, mono=20.0, net=nd(2000)))
        self.assertIsNone(s["rx"])                                           # the counter went down


class DiskTest(unittest.TestCase):
    def test_disk_warn_and_crit(self):
        node = Node()
        for avail, want in ((4, "warn"), (5.5, "warn"), (7, "ok"), (0.4, "crit"), (2, "crit"), (6, "ok")):
            node.minute["fs"] = [fs(avail_g=avail)]
            node.minutes(1)
            self.assertEqual(node.level("disk"), want, avail)
        node.minute["fs"] = [fs(size_g=1000, avail_g=60)]                     # 94 % but 60 GB free
        node.minutes(1)
        self.assertEqual(node.level("disk"), "ok")
        node.minute["fs"] = [fs(size_g=10, avail_g=0.15)]
        node.minutes(1)
        self.assertEqual(node.level("disk"), "crit")
        f = node.rs("disk")["find"]
        self.assertEqual(f["key"], "gw_host.find.disk.crit")
        self.assertEqual(f["args"]["mount"], "/")

    def test_the_worst_mount(self):
        node = Node()
        node.minute["fs"] = [fs("/", avail_g=50), fs("/var", avail_g=3)]
        node.minutes(1)
        self.assertEqual(node.level("disk"), "warn")
        self.assertEqual(node.rs("disk")["mount"], "/var")

    def test_inodes(self):
        node = Node()
        for ffree, want in ((70, "warn"), (130, "warn"), (200, "ok"), (10, "crit"), (40, "crit"), (60, "warn"),
                            (200, "ok")):
            node.minute["fs"] = [fs(files=1000, ffree=ffree)]
            node.minutes(1)
            self.assertEqual(node.level("inodes"), want, ffree)

    def test_disk_eta(self):
        node = Node()
        node.minute["fs"] = [fs(avail_g=20)]
        node.minutes(10)
        self.assertEqual(node.level("disk_eta"), "wait")                     # < 30 points
        node = Node()
        avail = 20.0
        for _ in range(35):                                                  # 0.1 GB a minute: ≈ 3.3 h left
            avail -= 0.1
            node.minute["fs"] = [fs(avail_g=avail)]
            node.minutes(1)
        self.assertEqual(node.level("disk_eta"), "warn")
        self.assertEqual(node.rs("disk_eta")["find"]["key"], "gw_host.find.disk_eta.warn")
        for _ in range(5):
            avail -= 1.0                                                     # 1 GB a minute: ≈ 12 min
            node.minute["fs"] = [fs(avail_g=avail)]
            node.minutes(1)
        self.assertEqual(node.level("disk_eta"), "crit")

    def test_disk_eta_goes_off(self):
        node = Node()
        avail = 20.0
        for _ in range(35):
            avail -= 0.1
            node.minute["fs"] = [fs(avail_g=avail)]
            node.minutes(1)
        self.assertEqual(node.level("disk_eta"), "warn")
        for _ in range(60):                                                  # the growth slows: > 12 h left
            avail -= 0.02
            node.minute["fs"] = [fs(avail_g=avail)]
            node.minutes(1)
        self.assertEqual(node.level("disk_eta"), "ok")

    def test_a_rotated_journal_is_no_forecast(self):
        node = Node()
        avail = 25.0
        for i in range(40):
            avail = avail - 0.1 if i != 30 else avail + 10.0                 # the journal rotated
            node.minute["fs"] = [fs(avail_g=avail)]
            node.minutes(1)
        self.assertEqual(node.level("disk_eta"), "ok")
        node = Node()
        for i in range(40):                                                  # 60 % used: no forecast at all
            node.minute["fs"] = [fs(avail_g=45 - 0.3 * i)]
            node.minutes(1)
            self.assertEqual(node.level("disk_eta"), "ok")


class MinuteRulesTest(unittest.TestCase):
    def test_fd_system_and_processes(self):
        node = Node()
        node.minutes(2, fd_sys=75.0)
        self.assertEqual(node.level("fd"), "ok")                             # hold 3
        node.minutes(1, fd_sys=75.0)
        self.assertEqual(node.level("fd"), "warn")
        node.minutes(1, fd_sys=67.0)
        self.assertEqual(node.level("fd"), "warn")
        node.minutes(1, fd_sys=60.0)
        self.assertEqual(node.level("fd"), "ok")
        node.minute["fd_proc"] = [{"who": "xray", "open": 920, "soft": 1000}]
        node.minutes(3)
        self.assertEqual(node.level("fd"), "crit")
        self.assertEqual(node.rs("fd")["find"]["args"]["who"], "xray")
        node.minute["fd_proc"] = [{"who": "xray", "open": 870, "soft": 1000}]
        node.minutes(1)
        self.assertEqual(node.level("fd"), "crit")
        node.minute["fd_proc"] = [{"who": "xray", "open": 10, "soft": 1000}]
        node.minutes(1)
        self.assertEqual(node.level("fd"), "ok")
        node.minute["fd_proc"] = None
        node.minutes(3, fd_sys=95.0)
        self.assertEqual(node.rs("fd")["find"]["args"]["who"], {"key": "gw_host.who.system", "args": {}})

    def test_clock(self):
        node = Node()
        node.minute["clock"] = {"synced": False, "offset_s": 0.0}
        node.minutes(2)
        self.assertEqual(node.level("clock"), "ok")
        node.minutes(1)
        self.assertEqual(node.level("clock"), "warn")
        self.assertEqual(node.rs("clock")["find"]["key"], "gw_host.find.clock.warn")
        node.minute["clock"] = {"synced": True, "offset_s": 0.04}
        node.minutes(1)
        self.assertEqual(node.level("clock"), "warn")                        # 0.04 is not back past 0.03
        node.minute["clock"] = {"synced": True, "offset_s": -0.01}
        node.minutes(1)
        self.assertEqual(node.level("clock"), "ok")
        node.minute["clock"] = {"synced": True, "offset_s": 0.06}
        node.minutes(3)
        self.assertEqual(node.rs("clock")["find"]["key"], "gw_host.find.clock.warn_offset")
        node.minute["clock"] = {"synced": True, "offset_s": 2.0}             # past the draft crit (1 s): warn
        node.minutes(3)
        self.assertEqual(node.level("clock"), "warn")

    def test_clock_just_after_boot(self):
        node = Node()
        node.minute["clock"] = {"synced": False, "offset_s": 0.0}
        node.minutes(5, uptime=300.0)
        self.assertEqual(node.level("clock"), "wait")
        self.assertEqual(node.events, [])


class OomTest(unittest.TestCase):
    """NS-D17, REV-2 С-1, REV-3 З-6, П-1: OOM of the node and of a group/service by the slices of cgroup v2."""

    def cg(self, slices, units=None, local=False):
        return {"v": 2, "local": local, "units": units or {},
                "slices": dict((k, {"oom": v[0], "oom_kill": v[1]}) for k, v in slices.items())}

    def go(self, mem, kill, slices, units=None, local=False, units0=None, slices0=None):
        node = Node()
        base = dict((k, (0, 0)) for k in (slices0 or slices))
        node.minute.update(oom_kill=0, cg=self.cg(base, units0 if units0 is not None else units, local))
        node.minutes(6, mem_pct=mem)
        node.minute.update(oom_kill=kill, cg=self.cg(slices, units, local))
        node.minutes(1, mem_pct=mem)
        return node

    def test_a_probe_at_its_limit_on_a_small_node(self):
        """The probe in its 96 MB on a node of 512 MB: oom_svc warn, oom ok — even with 6 % MemAvailable."""
        node = self.go(6.0, 1, {"system.slice": (1, 1), "user.slice": (0, 0)})
        self.assertEqual(node.level("oom_svc"), "warn")
        self.assertEqual(node.level("oom"), "ok")
        self.assertEqual(node.rs("oom_svc")["find"]["key"], "gw_host.find.oom_svc.warn_any")   # oneshot: no file
        self.assertEqual(node.rs("oom_svc")["advice"]["key"], "gw_host.advice.oom_svc_any")    # NS-D24: no «your»

    def test_a_named_unit(self):
        u0 = {"kaskad-panel.service": {"max": 402653184, "oom": 0, "oom_kill": 0}}
        u1 = {"kaskad-panel.service": {"max": 402653184, "oom": 1, "oom_kill": 1}}
        node = self.go(40.0, 1, {"system.slice": (1, 1)}, units=u1, units0=u0)
        f = node.rs("oom_svc")["find"]
        self.assertEqual(f["key"], "gw_host.find.oom_svc.warn")
        self.assertEqual(f["args"], {"unit": "kaskad-panel.service", "max": 384})
        self.assertEqual(node.rs("oom_svc")["advice"]["key"], "gw_host.advice.oom_svc")

    def test_a_unit_gone_the_same_minute_is_likely(self):
        u0 = {"kaskad-gw-probe.service": {"max": 100663296, "oom": 0, "oom_kill": 0}}
        node = self.go(40.0, 1, {"system.slice": (1, 1)}, units={"kaskad-gw-probe.service": None}, units0=u0)
        f = node.rs("oom_svc")["find"]
        self.assertEqual(f["key"], "gw_host.find.oom_svc.warn_likely")
        self.assertEqual(f["args"]["unit"], "kaskad-gw-probe.service")

    def test_a_kill_with_no_limit_by_the_memory(self):
        node = self.go(40.0, 1, {"system.slice": (0, 0), "user.slice": (0, 0)})
        self.assertEqual(node.level("oom"), "warn")                          # «лимит группы»: the memory was fine
        self.assertEqual(node.level("oom_svc"), "ok")
        node = self.go(6.0, 1, {"system.slice": (0, 0), "user.slice": (0, 0)})
        self.assertEqual(node.level("oom"), "crit")
        self.assertEqual(node.rs("oom")["find"]["args"], {"times": H.msg("gw_host.n.times", n=1), "min": 30})

    def test_the_swap_full_is_low_memory_too(self):
        node = self.go(40.0, 2, {"system.slice": (0, 0)})
        self.assertEqual(node.level("oom"), "warn")
        node2 = Node()
        node2.minute.update(oom_kill=0, cg=self.cg({"system.slice": (0, 0)}))
        node2.minutes(6, swap_pct=95.0)
        node2.minute.update(oom_kill=2, cg=self.cg({"system.slice": (0, 0)}))
        node2.minutes(1, swap_pct=95.0)
        self.assertEqual(node2.level("oom"), "crit")

    def test_user_slice_and_containers_are_the_limit_of_a_group(self):
        node = self.go(6.0, 1, {"system.slice": (0, 0), "user.slice": (1, 1)})
        self.assertEqual(node.level("oom"), "warn")
        self.assertEqual(node.level("oom_svc"), "ok")
        node = self.go(6.0, 1, {"system.slice": (0, 0), "lxc.payload.x": (1, 1)})
        self.assertEqual(node.level("oom"), "warn")                          # not crit: LXD at its own limit

    def test_a_global_kill_in_a_slice_is_not_its_limit(self):
        """REV-3 П-1: the global OOM raises oom_kill of the slice, not its oom — not in L."""
        node = self.go(6.0, 1, {"system.slice": (0, 1)})
        self.assertEqual(node.level("oom"), "crit")
        self.assertEqual(node.level("oom_svc"), "ok")

    def test_local_events_decide_by_the_memory_alone(self):
        node = self.go(40.0, 1, {"system.slice": (1, 1)}, local=True)
        self.assertEqual(node.level("oom"), "warn")
        node = self.go(6.0, 1, {"system.slice": (1, 1)}, local=True)
        self.assertEqual(node.level("oom"), "crit")

    def test_thirty_minutes_then_off(self):
        node = self.go(6.0, 1, {"system.slice": (0, 0)})
        self.assertEqual(node.level("oom"), "crit")
        node.minutes(28, mem_pct=6.0)
        self.assertEqual(node.level("oom"), "crit")
        node.minutes(3, mem_pct=6.0)
        self.assertEqual(node.level("oom"), "ok")

    def test_one_read_is_no_delta(self):
        node = Node()
        node.minutes(1)
        self.assertEqual(node.level("oom"), "wait")
        node.minutes(1)
        self.assertEqual(node.level("oom"), "ok")


class CertTest(unittest.TestCase):
    """NS-D13, REV Б-1, REV-2 П-3: the IP certificate in hours of ``renew_left_h``, the site's in days."""

    def ip(self, hours, try_ok=None, ago_h=None, state="valid"):
        return {"what": "ip", "hours_left": hours, "state": state, "renew_left_h": 72, "try_ok": try_ok,
                "try_at": (NOW0 + 3600 - ago_h * 3600) if ago_h is not None else None,   # the first tick: +1 h
                "reason": "limit"}

    def run_cert(self, *steps):
        node = Node()
        out = []
        for c in steps:
            node.tick(dt=3600.0, hour=True, certs=[c])
            out.append(node.level("cert"))
        return node, out

    def test_ip_certificate(self):
        _n, got = self.run_cert(self.ip(160))
        self.assertEqual(got, ["ok"])
        _n, got = self.run_cert(self.ip(70))                                # no try yet
        self.assertEqual(got, ["ok"])
        _n, got = self.run_cert(self.ip(70, False, 3))                      # a failed try before the renewal began
        self.assertEqual(got, ["ok"])
        node, got = self.run_cert(self.ip(70, False, 1))                    # failed an hour ago
        self.assertEqual(got, ["warn"])
        self.assertEqual(node.rs("cert")["find"]["key"], "gw_host.find.cert_ip.warn")
        self.assertEqual(node.rs("cert")["find"]["args"]["reason"], H.msg("gw_host.reason.limit"))   # З-3
        node, got = self.run_cert(self.ip(40), self.ip(20), self.ip(30), self.ip(60), self.ip(160))
        self.assertEqual(got, ["warn", "crit", "crit", "crit", "ok"])
        self.assertEqual([e["key"] for e in node.evs("cert")], ["gw_host.event.raise", "gw_host.event.raise",
                                                               "gw_host.event.clear"])
        _n, got = self.run_cert(self.ip(None, state="expired"))
        self.assertEqual(got, ["crit"])

    def test_site_certificate_has_no_problem(self):
        node = Node()
        node.tick(hour=True, certs=[{"what": "site", "name": "example.com", "days_left": 10}])
        self.assertEqual(node.level("cert"), "warn")
        lt = H.light(node.st, "ru")
        self.assertEqual(lt["top"][0]["id"], "cert")
        self.assertFalse(lt["top"][0]["problem"])
        self.assertEqual([a["problem"] for a in lt["alarms"]], [])          # warn is below the alarm (crit)
        node.tick(hour=True, certs=[{"what": "site", "name": "example.com", "days_left": 5}])
        lt = H.light(node.st, "ru")
        self.assertEqual(lt["alarms"][0]["id"], "cert")
        self.assertFalse(lt["alarms"][0]["problem"])                        # NS-D4: site_cert is there already
        node.tick(hour=True, certs=[{"what": "site", "name": "example.com", "days_left": 13}])
        self.assertEqual(node.level("cert"), "warn")
        node.tick(hour=True, certs=[{"what": "site", "name": "example.com", "days_left": 80}])
        self.assertEqual(node.level("cert"), "ok")

    def test_no_certificate_in_use(self):
        node = Node()
        node.run(3)
        self.assertEqual(node.level("cert"), "wait")                        # the hour set has not come yet
        node.tick(hour=True, certs=[])
        self.assertEqual((node.level("cert"), node.rs("cert")["why"]), ("na", "no_cert"))


class ProbesTest(unittest.TestCase):
    """§2 loss / rtt / anchor: by series of the points, the reduction for 1–5 points."""

    def feed(self, node, k, every=3, **kw):
        for i in range(k * every):
            node.tick(probes=series(**kw) if i % every == 0 else None)

    def test_one_point_cuts_pings(self):
        node = Node()
        self.feed(node, 20, losses={"8.8.8.8": 1})                            # 33 % at one point
        self.assertEqual(node.level("loss"), "ok")
        self.assertEqual(node.level("anchor"), "info")
        info = H.light(node.st, "ru")["info"]
        self.assertEqual([(i["id"], i["addr"]) for i in info], [("anchor", "8.8.8.8")])
        self.assertIn("8.8.8.8", info[0]["text"])
        self.assertEqual(node.events, [])

    def test_two_points_of_three(self):
        node = Node()
        self.feed(node, 20, n=50, losses={"8.8.8.8": 3, "1.1.1.1": 3})      # 6 %, 6 %, 0 %
        self.assertEqual(node.level("loss"), "warn")
        self.assertEqual(node.rs("loss")["value"], 3.0)
        self.assertEqual(node.rs("loss")["find"]["args"], {"pct": 3.0, "seen": 2, "of": 3})
        ev = node.evs("loss")
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0]["args"]["push"])                               # alarm warn (§10.1 п. 17)
        self.assertEqual(H.light(node.st, "ru")["alarms"][0]["kind"], "host_loss")

    def test_k_points(self):
        for addrs, losses, want in (
                (["1.1.1.1", "8.8.8.8"], {"1.1.1.1": 5, "8.8.8.8": 2}, 4.0),        # k = 2: the less
                (A3 + ["9.9.9.9"], {"1.1.1.1": 0, "8.8.8.8": 1, "77.88.8.8": 3, "9.9.9.9": 4}, 8.0 / 3),
                (A3 + ["9.9.9.9", "208.67.222.222"],                                 # k = 5: the two worst out
                 {"1.1.1.1": 0, "8.8.8.8": 1, "77.88.8.8": 3, "9.9.9.9": 4, "208.67.222.222": 5}, 8.0 / 3)):
            node = Node(cfg={"anchors": addrs})
            self.feed(node, 16, n=50, losses=losses, addrs=addrs)
            self.assertAlmostEqual(node.rs("loss")["value"], round(want, 1), places=1, msg=addrs)

    def test_one_point_alone_is_info(self):
        node = Node(cfg={"anchors": ["1.1.1.1"]})
        self.feed(node, 16, n=50, losses={"1.1.1.1": 2}, addrs=["1.1.1.1"])
        self.assertEqual(node.level("loss"), "info")
        self.assertEqual(node.events, [])
        lt = H.light(node.st, "ru")
        self.assertEqual(lt["alarms"], [])
        self.assertEqual(lt["info"][0]["id"], "one_point")

    def test_a_point_without_route_is_no_loss(self):
        node = Node(cfg={"anchors": A3 + ["2606:4700:4700::1111"]})
        self.feed(node, 16, na={"2606:4700:4700::1111": "no_route"}, addrs=A3 + ["2606:4700:4700::1111"])
        self.assertEqual(node.level("loss"), "ok")
        self.assertEqual(node.rs("loss")["value"], 0.0)

    def test_noisy_points(self):
        """REV-3 З-3: 20 of 26 series dropped as noisy (6 counted) — na «noisy» and a line; 8 of 26 — counted."""
        node = Node()
        for i in range(26):
            ex = "noisy" if i % 26 < 20 else None
            node.tick(probes=series(excl=ex, losses={"8.8.8.8": 1}))
            node.tick()
        self.assertEqual((node.level("loss"), node.rs("loss")["why"]), ("na", "noisy"))
        info = [i for i in H.light(node.st, "ru")["info"] if i["id"] == "noisy"]
        self.assertEqual([(i["n"], i["of"]) for i in info], [(20, 26)] * 3)
        node = Node()
        for i in range(26):
            node.tick(probes=series(excl="noisy" if i < 8 else None))
            node.tick()
        self.assertEqual(node.level("loss"), "ok")

    def test_alarm_held_while_points_go_noisy_or_away(self):
        node = Node()
        self.feed(node, 20, n=50, losses={"8.8.8.8": 3, "1.1.1.1": 3})
        self.assertEqual(node.said("loss"), "warn")
        n0 = len(node.events)
        for _ in range(20):                                                  # the window fills with noisy series
            node.tick(probes=series(excl="noisy"))
            node.tick()
            node.tick()
        self.assertEqual(node.rs("loss")["why"], "noisy")
        self.assertEqual(node.said("loss"), "warn")
        self.assertEqual(len(node.events), n0)

    def test_three_points_to_one_says_nothing(self):
        node = Node()
        self.feed(node, 20, n=50, losses={"8.8.8.8": 3, "1.1.1.1": 3})
        n0 = len(node.events)
        self.feed(node, 30, n=50, na={"8.8.8.8": "no_route", "77.88.8.8": "no_route"})
        self.assertEqual(node.level("loss"), "info")
        self.assertEqual(node.said("loss"), "warn")
        self.assertEqual(len(node.events), n0)
        top = H.light(node.st, "ru")["top"][0]
        self.assertEqual((top["id"], top["stale"]), ("loss", False))
        self.assertTrue(top["text"].startswith("Сеть сервера теряет"))

    def test_all_no_route_or_all_blocked(self):
        node = Node()
        self.feed(node, 20, n=50, losses={"8.8.8.8": 3, "1.1.1.1": 3})
        n0 = len(node.events)
        self.feed(node, 10, na=dict((a, "no_route") for a in A3))
        self.assertEqual((node.level("loss"), node.rs("loss")["why"]), ("na", "no_route"))
        self.assertEqual(len(node.events), n0)
        self.feed(node, 3, na=dict((a, "blocked") for a in A3))
        self.assertEqual(node.rs("loss")["why"], "blocked")
        ev = node.events[n0:]
        self.assertEqual([(e["key"], e["args"]["check"]) for e in ev], [("gw_host.event.na", "loss")])
        self.assertTrue(ev[0]["args"]["push"])
        self.assertIsNone(node.said("loss"))

    def test_probes_switched_off_with_an_alarm(self):
        node = Node()
        self.feed(node, 20, n=50, losses={"8.8.8.8": 3, "1.1.1.1": 3})
        n0 = len(node.events)
        node.cfg["probes"] = False
        node.run(3)
        ev = node.events[n0:]
        self.assertEqual([(e["key"], e["args"]["check"], e["args"]["why"]["key"]) for e in ev],
                         [("gw_host.event.na", "loss", "gw_host.why.probes_off")])
        self.assertNotIn("loss", [a["id"] for a in H.light(node.st, "ru")["alarms"]])

    def test_excluded_series_do_not_count(self):
        node = Node()
        self.feed(node, 20)
        for _ in range(10):
            node.tick(probes=series(excl="speedtest", losses=dict((a, 3) for a in A3)))
        self.assertEqual(node.level("loss"), "ok")
        self.assertEqual(node.rs("loss")["value"], 0.0)

    def test_the_network_down_says_nothing(self):
        node = Node()
        self.feed(node, 20, n=50, losses={"8.8.8.8": 3, "1.1.1.1": 3})
        n0 = len(node.events)
        node.ctx["node_offline"] = True
        for _ in range(500):                                                 # > WAIT_MAX
            node.tick(probes=series(losses=dict((a, 3) for a in A3)))
        self.assertEqual(node.level("loss"), "wait")
        self.assertEqual(node.said("loss"), "warn")
        self.assertEqual(len(node.events), n0)

    def test_series_by_the_pause_fill_the_window_in_ten_minutes(self):
        """The series come in the pause: 30 s × [0.7; 1.3] + 1.4 s, after a round of up to 8 s and a step of the
        pause of 2 s — after the first 10 min ``loss`` is not «wait» (REV С-2)."""
        rnd = random.Random(7)
        node = Node()
        due = node.mono + 30 * rnd.uniform(0.7, 1.3)
        start = node.mono
        while node.mono - start < 20 * 60:
            dt = 10.0 + rnd.uniform(0, 8.0) + rnd.choice([0, 2.0])
            pr = None
            if node.mono + dt >= due:
                pr = series()
                due = node.mono + dt + 1.4 + 30 * rnd.uniform(0.7, 1.3)
            node.tick(dt=dt, probes=pr)
            if node.mono - start > 10 * 60:
                self.assertNotEqual(node.level("loss"), "wait", node.mono - start)

    def test_loss_rtt_anchor_go_off(self):
        node = Node()
        node.st = {"norm": {"rtt": dict((a, [20.0] * 6) for a in A3), "hour": None}}
        self.feed(node, 20, n=50, losses={"8.8.8.8": 3, "1.1.1.1": 3, "77.88.8.8": 1},
                  rtts=dict((a, [80.0] * 3) for a in A3))
        self.assertEqual((node.level("loss"), node.level("rtt")), ("warn", "warn"))
        self.feed(node, 20, n=100, losses={"8.8.8.8": 6, "1.1.1.1": 3},                       # 1.5 % / 2.25×
                  rtts=dict((a, [45.0] * 3) for a in A3))
        self.assertEqual((node.level("loss"), node.level("rtt")), ("warn", "warn"))         # held between
        self.feed(node, 20, n=50, rtts=dict((a, [20.0] * 3) for a in A3))
        self.assertEqual((node.level("loss"), node.level("rtt")), ("ok", "ok"))
        self.feed(node, 20, n=50, losses={"8.8.8.8": 6})                                    # 12 % at one point
        self.assertEqual(node.level("anchor"), "info")
        self.feed(node, 20, n=50, losses={"8.8.8.8": 3})                                    # 6 %: held
        self.assertEqual(node.level("anchor"), "info")
        self.feed(node, 20, n=50, losses={"8.8.8.8": 2})                                    # 4 % < 5: off
        self.assertEqual(node.level("anchor"), "ok")

    def test_rtt_norms_by_point(self):
        node = Node()
        self.feed(node, 20, rtts={"1.1.1.1": [12.0] * 3, "8.8.8.8": [34.0] * 3, "77.88.8.8": [34.0] * 3})
        self.assertEqual(node.level("rtt"), "wait")                          # no norm yet
        node.st["norm"]["rtt"] = {"1.1.1.1": [12.0] * 6, "8.8.8.8": [34.0] * 6, "77.88.8.8": [34.0] * 6}
        node.run(1)
        self.assertEqual(node.level("rtt"), "ok")
        self.assertEqual(node.rs("rtt")["value"], 1.0)
        self.feed(node, 20, rtts={"1.1.1.1": [42.0] * 3, "8.8.8.8": [120.0] * 3, "77.88.8.8": [119.0] * 3})
        self.assertEqual(node.level("rtt"), "warn")
        self.assertEqual(node.rs("rtt")["find"]["args"]["norm"], 34)
        self.assertIsNone(H.RULES["rtt"]["alarm"])
        self.assertFalse([e for e in node.evs("rtt") if e["args"]["push"]])

    def test_the_norm_is_built_from_hourly_medians(self):
        node = Node()
        for h in range(8):
            node.tick(dt=3600.0, probes=series(rtt=(30.0, 30.0, 30.0)))
        self.assertGreaterEqual(len(node.st["norm"]["rtt"]["1.1.1.1"]), 6)
        node.cfg["anchors"] = ["1.1.1.1", "8.8.8.8"]
        node.run(1)
        self.assertNotIn("77.88.8.8", node.st["norm"]["rtt"])                 # removed point: its norm goes


class StepTest(unittest.TestCase):
    """§2.1 row by row on ``step`` (conntrack: alarm crit, a kind with events)."""

    R = H.RULES["conntrack"]

    def go(self, rs, level, mono, why=None, ctx=None, now=None, rule=None):
        return H.step(rs, {"level": level, "why": why, "find": {"key": "gw_host.find.conntrack." + level, "args": {}}
                           if level in ("warn", "crit") else None}, rule or self.R, ctx or {},
                      now if now is not None else NOW0 + mono, mono)

    def test_the_same_level_nothing(self):
        rs, ev = self.go({"said": "warn", "pending": "ok", "quiet_until": 500}, "warn", 100)
        self.assertIsNone(ev)
        self.assertIsNone(rs["pending"])

    def test_raise_from_null_or_ok(self):
        for said in (None, "ok"):
            rs, ev = self.go({"said": said}, "warn", 100)
            self.assertEqual((ev["key"], ev["args"]["level"], ev["args"]["from"]), ("gw_host.event.raise", "warn", "ok"))
            self.assertEqual(rs["said"], "warn")
            self.assertEqual(rs["quiet_until"], 100 + H.EVENT_QUIET)
            rs, ev = self.go({"said": said}, "crit", 100)
            self.assertEqual(ev["args"]["level"], "crit")

    def test_a_repeat_raise_within_the_window_is_not_told(self):
        rs = {"said": None, "last_ev": "raise", "last_lvl": "warn", "quiet_until": 900}
        rs, ev = self.go(rs, "warn", 100)
        self.assertIsNone(ev)
        self.assertEqual(rs["said"], "warn")
        rs, ev = self.go({"said": None, "last_ev": "raise", "last_lvl": "warn", "quiet_until": 900}, "crit", 100)
        self.assertEqual(ev["args"]["level"], "crit")                         # a new crit — always

    def test_warn_to_crit_at_once_but_after_a_lower(self):
        rs, ev = self.go({"said": "warn", "quiet_until": 900, "last_ev": "raise"}, "crit", 100)
        self.assertEqual((ev["key"], ev["args"]["from"]), ("gw_host.event.raise", "warn"))
        rs, ev = self.go({"said": "warn", "quiet_until": 900, "last_ev": "lower"}, "crit", 100)
        self.assertIsNone(ev)
        self.assertEqual((rs["said"], rs["pending"]), ("warn", "crit"))
        rs, ev = self.go(rs, "crit", 901)                                    # the end of the window
        self.assertEqual((ev["key"], ev["args"]["level"]), ("gw_host.event.raise", "crit"))
        self.assertIsNone(rs["pending"])

    def test_lower_and_clear_wait_for_the_window(self):
        rs, ev = self.go({"said": "crit", "quiet_until": 900}, "warn", 100)
        self.assertIsNone(ev)
        self.assertEqual(rs["pending"], "warn")
        rs2, ev = self.go(rs, "warn", 901)
        self.assertEqual((ev["key"], ev["args"]["from"]), ("gw_host.event.lower", "crit"))
        rs, ev = self.go({"said": "crit", "quiet_until": 50}, "warn", 100)
        self.assertEqual(ev["key"], "gw_host.event.lower")                    # outside — at once
        rs, ev = self.go({"said": "warn", "quiet_until": 900}, "ok", 100)
        self.assertEqual((ev, rs["pending"], rs["said"]), (None, "ok", "warn"))
        rs, ev = self.go(rs, "ok", 901)
        self.assertEqual(ev["key"], "gw_host.event.clear")
        self.assertEqual(rs["said"], "ok")

    def test_the_end_of_the_window_by_the_last_real_evaluation(self):
        rs, _ = self.go({"said": "crit", "quiet_until": 900}, "ok", 100)
        rs, ev = self.go(rs, "warn", 800)
        self.assertEqual((ev, rs["pending"]), (None, "warn"))
        rs, ev = self.go(rs, "wait", 901)                                   # a wait at the end: pending waits
        self.assertIsNone(ev)
        self.assertEqual(rs["pending"], "warn")
        rs, ev = self.go(rs, "crit", 950)                                   # back to said: pending silently gone
        self.assertIsNone(ev)
        self.assertIsNone(rs["pending"])
        rs, _ = self.go({"said": "warn", "quiet_until": 900}, "ok", 100)
        rs, ev = self.go(rs, "crit", 200)                                   # higher than said within the window
        self.assertEqual(ev["args"]["level"], "crit")

    def test_null_to_ok_silently(self):
        rs, ev = self.go({}, "ok", 100)
        self.assertEqual((rs["said"], ev), ("ok", None))

    def test_wait_info_and_temporary_na_hold_everything(self):
        base = {"said": "crit", "pushed": "crit", "since": 5.0, "pending": None}
        for level, why in (("wait", "wait"), ("info", "one_point"), ("na", "no_file"), ("na", "noisy"),
                           ("na", "no_route"), ("na", "error")):
            rs, ev = self.go(dict(base), level, 100, why)
            self.assertIsNone(ev)
            self.assertEqual((rs["said"], rs["pushed"], rs["since"]), ("crit", "crit", 5.0), (level, why))
            self.assertEqual(rs["wait_since"], 100)

    def test_wait_longer_than_wait_max_is_na_history(self):
        rs, _ = self.go({"said": "warn", "pushed": "warn"}, "wait", 100)
        rs, ev = self.go(rs, "wait", 100 + H.WAIT_MAX)
        self.assertIsNone(ev)
        rs, ev = self.go(rs, "wait", 101 + H.WAIT_MAX)
        self.assertEqual((ev["key"], ev["args"]["why"]["key"]), ("gw_host.event.na", "gw_host.why.history"))
        self.assertTrue(ev["args"]["push"])
        self.assertIsNone(rs["said"])
        rs, ev = self.go({"said": "crit"}, "na", 100, "no_file")
        rs, ev = self.go(rs, "na", 200 + H.WAIT_MAX, "no_file")              # REV-2 З-5: with its own reason
        self.assertEqual(ev["args"]["why"]["key"], "gw_host.why.no_file")
        rs, ev = self.go({"said": "ok"}, "wait", 100)
        rs, ev = self.go(rs, "wait", 200 + H.WAIT_MAX)
        self.assertIsNone(ev)                                                # said ok: nothing to take back

    def test_a_real_na(self):
        rs, ev = self.go({"said": "warn", "pending": "ok", "pushed": "warn"}, "na", 100, "no_gateway")
        self.assertEqual((ev["key"], ev["args"]["level"], ev["args"]["from"]), ("gw_host.event.na", "na", "warn"))
        self.assertEqual((rs["said"], rs["pending"], rs.get("pushed")), (None, None, None))
        for said in ("ok", None):
            rs, ev = self.go({"said": said}, "na", 100, "no_conntrack")
            self.assertEqual((rs["said"], ev), (None, None))

    def test_conntrack_swinging_96_89_gives_two_events_at_most(self):
        """REV-2 З-4: a swing at the border of crit for 15 min — ``raise crit`` at once and one by the window."""
        rs, evs = {"said": "ok"}, []
        lv = ["crit", "warn"] * 45
        for i, level in enumerate(lv):
            rs, ev = self.go(rs, level, 100 + 10 * i)
            if ev:
                evs.append(ev)
        self.assertLessEqual(len([e for e in evs if e["args"]["push"]]), 2)
        self.assertLessEqual(len(evs), 2)

    def test_conntrack_swinging_by_the_values(self):
        node = Node()
        for i in range(90):
            node.tick(conntrack_pct=96.0 if (i // 3) % 2 == 0 else 89.0)
        ev = node.evs("conntrack")
        self.assertLessEqual(len([e for e in ev if e["args"]["push"]]), 2)


class PushTest(unittest.TestCase):
    """§2.1 «Решение слать в бот»: ``args.push`` by the alarm of the rule, gateway.notify.host_level and the switch."""

    def chain(self, rule, levels, ctx=None):
        rs, out, mono = {}, [], 100.0
        for lv in levels:
            rs, ev = H.step(rs, {"level": lv, "find": {"key": "gw_host.find.x." + lv, "args": {}}}, H.RULES[rule],
                            ctx or {}, NOW0 + mono, mono)
            mono += H.EVENT_QUIET + 1
            if ev:
                out.append((ev["key"].rsplit(".", 1)[1], ev["args"]["push"]))
        return rs, out

    def test_crit_warn_ok_three_pushes(self):
        rs, got = self.chain("conntrack", ["ok", "crit", "warn", "ok"], {"host_level": "crit"})
        self.assertEqual(got, [("raise", True), ("lower", True), ("clear", True)])
        self.assertIsNone(rs["pushed"])

    def test_a_warn_below_the_alarm_is_not_pushed(self):
        _rs, got = self.chain("conntrack", ["ok", "warn", "ok"], {"host_level": "crit"})
        self.assertEqual(got, [("raise", False), ("clear", False)])
        _rs, got = self.chain("load", ["ok", "warn", "ok"], {})
        self.assertEqual(got, [("raise", False), ("clear", False)])

    def test_the_alarm_warn_rules(self):
        for rid in ("steal", "iowait", "clock", "loss", "mem"):
            _rs, got = self.chain(rid, ["ok", "warn", "ok"], {"host_level": "crit"})
            self.assertEqual(got, [("raise", True), ("clear", True)], rid)

    def test_host_level_warn_pushes_every_warn(self):
        _rs, got = self.chain("load", ["ok", "warn", "ok"], {"host_level": "warn"})
        self.assertEqual(got, [("raise", True), ("clear", True)])

    def test_the_switch_off(self):
        ctx = {"push": {"host_conntrack": False}}
        rs, got = self.chain("conntrack", ["ok", "crit", "warn", "ok"], ctx)
        self.assertEqual(got, [("raise", False), ("lower", False), ("clear", False)])
        rs, _ = self.chain("conntrack", ["ok", "crit"], {})
        self.assertEqual(rs["pushed"], "crit")
        rs, ev = H.step(dict(rs, quiet_until=None), {"level": "ok"}, H.RULES["conntrack"], ctx, NOW0, 1e6)
        self.assertFalse(ev["args"]["push"])
        self.assertIsNone(rs["pushed"])

    def test_pushed_lives_through_the_file(self):
        node = Node()
        node.run(ticks("conntrack"), conntrack_pct=97.0)
        self.assertEqual(node.rs("conntrack")["pushed"], "crit")
        doc = H.to_disk(node.st)
        st = H.from_disk(doc, 5.0, node.now)
        self.assertEqual(st["rules"]["conntrack"]["pushed"], "crit")


class AutomatonTest(unittest.TestCase):
    """§2.1 «Особые случаи» and the antispam on whole rounds."""

    def restart(self, node, owner="collector"):
        doc = H.to_disk(node.st)
        node.st = H.from_disk(doc, node.mono + 7.0, node.now)
        node.ctx["owner"] = owner
        node.mono += 7.0

    def test_a_restart_keeps_the_alarm_stale_and_quiet(self):
        node = Node()
        node.run(ticks("cpu"), busy=97.0)
        self.assertEqual(node.said("cpu"), "crit")
        n0 = len(node.events)
        self.restart(node)
        node.run(10, busy=97.0)
        self.assertEqual(node.level("cpu"), "wait")
        lt = H.light(node.st, "ru")
        self.assertEqual((lt["level"], lt["top"][0]["id"], lt["top"][0]["stale"]), ("crit", "cpu", True))
        self.assertTrue(lt["top"][0]["text"])
        self.assertIn("stale_msg", lt["top"][0])
        node.run(ticks("cpu"), busy=97.0)
        self.assertEqual(node.level("cpu"), "crit")
        self.assertEqual(len(node.events), n0)                               # the same level — silence
        node.run(ticks("cpu") + 100, busy=10.0)
        self.assertEqual([e["key"] for e in node.events[n0:]], ["gw_host.event.lower", "gw_host.event.clear"])

    def test_a_restart_then_ok_is_one_clear(self):
        node = Node()
        node.run(ticks("conntrack"), conntrack_pct=97.0)
        n0 = len(node.events)
        self.restart(node)
        node.run(100, conntrack_pct=10.0)
        ev = node.events[n0:]
        self.assertEqual([(e["key"], e["args"]["push"]) for e in ev], [("gw_host.event.clear", True)])

    def test_crit_wait_crit_says_nothing(self):
        node = Node()
        node.run(ticks("conntrack"), conntrack_pct=97.0)
        n0 = len(node.events)
        node.run(5, conntrack_pct=None)                                      # a temporary na (no_file)
        node.run(5, conntrack_pct=97.0)
        self.assertEqual(len(node.events), n0)

    def test_disk_one_bad_read_says_nothing(self):
        node = Node()
        node.minute["fs"] = [fs(avail_g=0.2)]
        node.minutes(1)
        self.assertEqual(node.said("disk"), "crit")
        n0 = len(node.events)
        node.minute["fs"] = None
        node.minutes(1)
        self.assertEqual((node.level("disk"), node.rs("disk")["why"]), ("na", "no_file"))
        self.assertEqual(node.said("disk"), "crit")
        node.minute["fs"] = [fs(avail_g=0.2)]
        node.minutes(1)
        self.assertEqual(len(node.events), n0)
        node.minute["fs"] = None
        node.minutes(62)
        ev = node.events[n0:]
        self.assertEqual([(e["args"]["check"], e["key"], e["args"]["why"]["key"]) for e in ev
                          if e["args"]["check"] == "disk"],
                         [("disk", "gw_host.event.na", "gw_host.why.no_file")])

    def test_a_clear_within_the_window_leaves_the_view_at_once(self):
        """REV-3 З-2: only the event waits — the problem leaves top and the alarms in the same round, a line «стало
        лучше» in info, the event ``clear`` at the end of the window only if still ok."""
        node = Node()
        node.run(5, mem_pct=5.0)
        self.assertEqual(node.said("mem"), "warn")
        n0 = len(node.events)
        node.run(1, mem_pct=40.0)
        lt = H.light(node.st, "ru")
        self.assertEqual(node.rs("mem")["pending"], "ok")
        self.assertNotIn("mem", [f["id"] for f in lt["top"]])
        self.assertNotIn("mem", [a["id"] for a in lt["alarms"]])
        better = [i for i in lt["info"] if i["id"] == "better"]
        self.assertEqual(better[0]["check"], "mem")
        self.assertAlmostEqual(better[0]["until"], node.now + (node.rs("mem")["quiet_until"] - node.mono))
        self.assertEqual(len(node.events), n0)
        node.run(int(H.EVENT_QUIET // 10) + 2, mem_pct=40.0)
        self.assertEqual([e["key"] for e in node.events[n0:]], ["gw_host.event.clear"])

    def test_a_lower_within_the_window_is_shown_at_once(self):
        node = Node()
        node.run(5, conntrack_pct=97.0)
        node.run(1, conntrack_pct=88.0)
        lt = H.light(node.st, "ru")
        self.assertEqual(node.rs("conntrack")["pending"], "warn")
        f = [x for x in lt["top"] if x["id"] == "conntrack"][0]
        self.assertEqual(f["level"], "warn")
        self.assertIsNotNone(f["confirm_until"])
        self.assertEqual(lt["level"], "warn")

    def test_collector_to_bot_and_back(self):
        node = Node()
        node.run(ticks("cpu"), busy=90.0, mem_pct=5.0)
        self.assertEqual((node.said("cpu"), node.said("mem")), ("warn", "warn"))
        doc = H.to_disk(node.st)
        n0 = len(node.events)
        prev = H.from_disk(doc, 50.0, node.now)
        raw = {"mono": 60.0, "mem": {"total": 1000, "avail": 50, "swap_total": 0, "swap_free": 0}, "cores": 1,
               "fs": [fs()], "clock": {"synced": True, "offset_s": 0.0}}
        st, evs = H.evaluate_instant(prev, raw, CFG, {"owner": "bot", "host_level": "crit"}, node.now + 60, 60.0)
        self.assertEqual([(e["args"]["check"], e["key"], e["args"]["why"]["key"]) for e in evs],
                         [("cpu", "gw_host.event.na", "gw_host.why.no_gateway")])
        self.assertEqual(st["rules"]["mem"]["said"], "warn")
        self.assertEqual(st["owner"], "bot")
        for i in range(3):
            st, evs = H.evaluate_instant(st, dict(raw, mono=120.0 + 60 * i), CFG, {"owner": "bot"},
                                         node.now + 120 + 60 * i, 120.0 + 60 * i)
            self.assertEqual(evs, [])
        node.st = H.from_disk(H.to_disk(st), node.mono + 300, node.now + 300)         # the collector again
        node.mono += 300
        node.run(10, mem_pct=5.0)
        self.assertEqual([e for e in node.events[n0:] if e["args"]["check"] == "mem"], [])

    def test_one_bad_rule_does_not_stop_the_others(self):
        node = Node()
        with mock.patch.dict(H.EVAL, {"cpu": mock.Mock(side_effect=ValueError("bad"))}):
            node.run(5, mem_pct=5.0)
        self.assertEqual((node.level("cpu"), node.rs("cpu")["why"]), ("na", "error"))
        self.assertEqual(node.said("mem"), "warn")

    def test_no_clock_inside(self):
        node = Node()
        with mock.patch.object(time, "time", mock.Mock(side_effect=AssertionError("time.time")) ), \
                mock.patch.object(time, "monotonic", mock.Mock(side_effect=AssertionError("monotonic"))):
            node.run(ticks("cpu"), busy=97.0, probes=series())
            node.minutes(2)
            H.minute_row(node.st, node.mono)
            H.ping_rows(node.st, node.mono)
            H.from_disk(H.to_disk(node.st), 1.0, 2.0)
            H.light(node.st, False)
        self.assertEqual(node.said("cpu"), "crit")

    def test_the_constants_of_live(self):
        self.assertEqual(H.EVENT_QUIET, live.EVENT_QUIET)
        self.assertEqual(sorted(H.HOST_KINDS), sorted("host_" + g for g in H.HOST_GROUPS))
        self.assertEqual(len(H.HOST_KINDS), 10)


class LightTest(unittest.TestCase):
    def test_levels_and_top(self):
        node = Node()
        self.assertEqual(H.light(node.st)["level"], "wait")
        node.run(2)
        self.assertEqual(H.light(node.st)["level"], "ok")
        node.run(ticks("cpu"), busy=97.0, mem_pct=5.0, conntrack_pct=92.0, load_core=3.0)
        lt = H.light(node.st, "en")
        self.assertEqual(lt["level"], "crit")
        self.assertEqual([f["id"] for f in lt["top"]], ["cpu", "load", "mem"])          # crit, then the order
        self.assertEqual(lt["top"][0]["text"], "The CPU has been 97% busy for 10 min — almost to the limit")
        self.assertTrue(lt["top"][0]["advice"])
        self.assertEqual(lt["checks"]["cpu"], {"level": "crit", "value": 97.0, "shown": "crit"})
        ru = H.light(node.st, "ru")
        self.assertEqual(ru["top"][0]["text"], "Процессор занят на 97% уже 10 мин — почти до предела")
        self.assertEqual(sorted(a["id"] for a in lt["alarms"]), ["cpu", "mem"])         # load: no alarm
        self.assertTrue(all(a["problem"] for a in lt["alarms"]))

    def test_services_paint_the_light_without_events(self):
        node = Node(services={"dns": False, "xray": True}, inbounds_why={"wg": True})
        node.run(2)
        lt = H.light(node.st, "en")
        self.assertEqual(lt["level"], "crit")
        self.assertEqual(lt["top"][0]["text"], "Not working: Gateway DNS, WireGuard")        # REV-ns-a12 З-4
        self.assertEqual(H.light(node.st, "ru")["top"][0]["text"], "Не работает: DNS шлюза, WireGuard")
        self.assertFalse(lt["alarms"][0]["problem"])                          # NS-D4: the problems are there
        self.assertEqual(node.evs("services"), [])

    def test_services_back(self):
        node = Node(services={"dns": False})
        node.run(2)
        self.assertEqual(node.level("services"), "crit")
        node.ctx["services"] = {"dns": True}
        node.run(1)
        self.assertEqual((node.level("services"), node.said("services")), ("ok", "ok"))
        self.assertEqual(H.light(node.st)["level"], "ok")

    def test_instant(self):
        raw = {"mono": 5.0, "mem": {"total": 1000, "avail": 50, "swap_total": 1000, "swap_free": 10}, "cores": 1,
               "fs": [fs(avail_g=0.2)], "conntrack": {"count": 10, "max": 100}, "fd": {"alloc": 1, "max": 100},
               "clock": {"synced": True, "offset_s": 0.0}, "absent": [],
               "certs": [{"what": "site", "name": "example.com", "days_left": 30}]}
        st = H.instant(raw, CFG, {"services": {"dns": True}}, now=NOW0)
        lt = H.light(st, "en")
        self.assertTrue(lt["instant"])
        self.assertEqual(lt["level"], "crit")
        self.assertEqual(sorted(lt["checks"]), sorted(H.INSTANT))
        self.assertEqual(lt["checks"]["mem"]["level"], "warn")                # no «hold» in one snapshot
        self.assertEqual(lt["checks"]["swap"]["level"], "crit")
        self.assertEqual(lt["checks"]["disk"]["level"], "crit")
        self.assertEqual(lt["checks"]["cert"]["level"], "ok")

    def test_say_numbers_and_times(self):
        m = {"key": "gw_host.find.load.warn", "args": {"per_core": 2.4}}
        self.assertEqual(H.say(m, "ru"), "Очередь процессов: 2,4 на ядро за 15 мин")
        self.assertEqual(H.say(m, "en"), "Run queue: 2.4 per core over 15 min")
        m = {"key": "gw_host.find.mem.warn", "args": {"pct": 7, "mb": 1400}}
        self.assertEqual(H.say(m, "ru"), "Мало свободной памяти: 7% (1\u202f400 МБ)")
        self.assertEqual(H.say(m, "en"), "Low free memory: 7% (1,400 MB)")
        m = {"key": "gw_host.better", "args": {"until_at": 1790000000}}
        self.assertRegex(H.say(m, "ru"), r"^стало лучше, подтверждаю до \d\d:\d\d$")
        ev = {"key": "gw_host.event.na", "args": {"what": {"key": "gw_host.check.loss", "args": {}},
                                                   "why": {"key": "gw_host.why.probes_off", "args": {}}}}
        self.assertEqual(H.event_text(ev, "ru"), "Сервер: Потери в сети — не проверяется: пробы выключены")


class RowsTest(unittest.TestCase):
    def test_minute_row(self):
        node = Node()
        node.minute["fs"] = [fs(avail_g=3)]
        node.run(6, busy=50.0, rx=1000, tx=2000)
        row = H.minute_row(node.st, node.mono)
        self.assertEqual(row["n"], 6)
        self.assertEqual(row["busy"], 50.0)
        self.assertEqual(row["rx"], 6000)
        self.assertEqual(row["tx"], 12000)
        self.assertEqual(row["disk_free"], 3 * GIB)
        self.assertEqual(row["clock_ok"], 1)
        self.assertEqual((row["level"], row["warn_min"], row["alarm_min"]), (1, 1, 0))     # disk warn, alarm crit
        self.assertIsNone(row["canary_ms"])
        self.assertEqual(row["ts"] % 60, 0)
        node.run(6, rx=None, tx=None)
        self.assertIsNone(H.minute_row(node.st, node.mono)["rx"])           # a minute without points — NULL

    def test_ping_rows(self):
        node = Node()
        node.tick(probes=series(losses={"8.8.8.8": 1}, rtts={"1.1.1.1": [10, 12, 11]}))
        node.tick(probes=series(excl="noisy"))
        rows = H.ping_rows(node.st, node.mono)
        self.assertEqual(rows["1.1.1.1"]["n"], 3)
        self.assertEqual(rows["1.1.1.1"]["rtt_med"], 11)
        self.assertEqual(rows["1.1.1.1"]["jit"], 1.5)
        self.assertEqual(rows["1.1.1.1"]["excl"], 1)
        self.assertEqual(rows["8.8.8.8"]["lost"], 1.0)
        self.assertIn("*", rows)


class TextsTest(unittest.TestCase):
    """The keys of gw_host: RU+EN, every key has a caller in hostmon (no orphans), D-122 (no «.ru», no y/n)."""

    SRC = open(H.__file__, encoding="utf-8").read()
    for _m in ("collect.py", "model.py"):                  # A5: the hint of the probes and the lines of the journal
        SRC += open(os.path.join(os.path.dirname(H.__file__), _m), encoding="utf-8").read()
    # A7: the way of a point (gw_host.via.*) and the hint of the bot on bash — said by the panel's route
    SRC += open(os.path.join(os.path.dirname(os.path.dirname(H.__file__)), "panel", "host.py"), encoding="utf-8").read()

    def test_every_key_is_used(self):
        cat = i18n.catalog("ru")
        keys = [k for k in cat if k.startswith("gw_host.")]
        self.assertGreater(len(keys), 100)
        lits = set(re.findall(r'"(gw_host\.[a-z_.]+)"', self.SRC))
        variants = set(re.findall(r'"(_[a-z_]+)"', self.SRC))
        whys = set(re.findall(r'"([a-z_]+)"', self.SRC))
        orphans = []
        for k in keys:
            parts = k.split(".")
            if k in lits:
                continue
            if parts[1] == "check" and parts[2] in H.RULES:
                continue
            if parts[1] == "advice" and (parts[2] in H.RULES or "gw_host.advice." + parts[2] in lits):
                continue
            if parts[1] == "why" and (parts[2] in H.REAL_WHY + H.TEMP_WHY or parts[2] in whys):
                continue
            if parts[1] == "event" and parts[2] in ("raise", "lower", "clear", "na"):
                continue
            if parts[1] == "find" and len(parts) == 4:
                lvl = parts[3].split("_")[0]
                rest = parts[3][len(lvl):]
                if (parts[2] in H.RULES or '"%s"' % parts[2] in self.SRC) and lvl in ("warn", "crit") and \
                        (not rest or rest in variants):
                    continue
            if parts[1] == "reason" and parts[2] in H.CERT_REASONS or parts[1] == "svc" and parts[2] in H.SERVICES:
                continue
            orphans.append(k)
        self.assertEqual(orphans, [])

    def test_every_rule_has_its_texts(self):
        cat_ru, cat_en = i18n.catalog("ru"), i18n.catalog("en")
        for r in H.HOST_RULES:
            self.assertIn("gw_host.check." + r["id"], cat_ru)
            for lv in ("warn", "crit"):
                if (lv == "warn" and r["warn"]) or (lv == "crit" and r["crit"]):
                    if r["id"] in ("anchor", "oom_svc", "cert", "clock", "services") and lv == "warn":
                        continue
                    self.assertIn("gw_host.find.%s.%s" % (r["id"], lv), cat_ru, r["id"])
                    self.assertIn("gw_host.find.%s.%s" % (r["id"], lv), cat_en, r["id"])
        for w in H.REAL_WHY + H.TEMP_WHY:
            self.assertIn("gw_host.why." + w, cat_ru)

    def test_d122(self):
        for lang in ("ru", "en"):
            for k, v in i18n.catalog(lang).items():
                if not k.startswith("gw_host."):
                    continue
                self.assertNotRegex(v, r"\.ru\b", k)
                self.assertNotRegex(v, r"\[[yYдД]/[nNнН]\]|\by/n\b", k)
                self.assertNotRegex(v, r"\b(?:1\.1\.1\.1|8\.8\.8\.8|77\.88\.8\.8)\b", k)   # addresses via {addr}
        self.assertEqual(i18n.catalog("ru")["gw_host.find.swap.warn"], "Своп занят на {pct}%")
        self.assertEqual(i18n.catalog("en")["gw_host.find.swap.warn"], "Swap is {pct}% full")


class Round2Test(unittest.TestCase):
    """REV-ns-a12 (the check of A1/A2, round 1): the reproductions of С-1, З-1…З-5, П-1, П-3, П-5 and the decisions
    NS-D23 (listen), NS-D24 (the OOM of the probe) — each red before round 2."""

    def restart(self, node, owner="collector", gap=7.0):
        node.st = H.from_disk(H.to_disk(node.st), node.mono + gap, node.now + gap)
        node.mono += gap
        node.now += gap
        node.ctx["owner"] = owner

    def oom_node(self):
        node = Node()
        node.minutes(3, mem_pct=5.0)
        node.minute["oom_kill"] = 2
        node.minutes(1, mem_pct=5.0)
        return node

    # С-1
    def test_oom_crit_then_a_restart_16_min_later_says_no_clear(self):
        node = self.oom_node()
        self.assertEqual(node.said("oom"), "crit")
        node.minutes(16, mem_pct=40.0)
        self.assertEqual(node.level("oom"), "crit")                          # 16 min: still in the window
        k = len(node.events)
        self.restart(node)
        node.minutes(29, mem_pct=40.0)
        self.assertEqual([e["key"] for e in node.events[k:] if e["args"]["check"] == "oom"], [])
        self.assertEqual(node.level("oom"), "wait")
        self.assertEqual(node.rs("oom")["why_args"], {"have": 28, "need": 30})
        self.assertEqual(H.light(node.st, "ru")["level"], "crit")            # the alarm stays, stale
        node.minutes(2, mem_pct=40.0)
        self.assertEqual([(e["key"], e["args"]["push"]) for e in node.events[k:] if e["args"]["check"] == "oom"],
                         [("gw_host.event.clear", True)])

    def test_a_new_oom_after_a_restart_is_said_at_once(self):
        node = self.oom_node()
        self.restart(node)
        node.minutes(3, mem_pct=5.0)
        node.minute["oom_kill"] = 3
        node.minutes(1, mem_pct=5.0)
        self.assertEqual(node.level("oom"), "crit")                          # no wait for the full window

    def test_oom_svc_holds_through_a_restart_too(self):
        node = Node()
        cg0 = {"v": 2, "local": False, "units": {}, "slices": {"system.slice": {"oom": 0, "oom_kill": 0}}}
        cg1 = {"v": 2, "local": False, "units": {}, "slices": {"system.slice": {"oom": 1, "oom_kill": 1}}}
        node.minute.update(cg=cg0)
        node.minutes(3)
        node.minute.update(oom_kill=1, cg=cg1)
        node.minutes(10)
        self.assertEqual(node.said("oom_svc"), "warn")
        k = len(node.events)
        self.restart(node)
        node.minutes(25)
        self.assertEqual([e for e in node.events[k:] if e["args"]["check"] == "oom_svc"], [])

    # З-1
    def test_the_hysteresis_of_a_mount_lives_through_a_restart(self):
        node = Node()
        node.minute["fs"] = [fs(size_g=100, avail_g=0.4)]
        node.minutes(2)
        node.minute["fs"] = [fs(size_g=100, avail_g=4.0)]                    # 96 %: between «выкл» 95 and «вкл» 98
        node.minutes(30)
        self.assertEqual(node.said("disk"), "crit")
        k = len(node.events)
        self.restart(node)
        node.minutes(3)
        self.assertEqual(node.said("disk"), "crit")
        self.assertEqual(node.events[k:], [])
        node.minute["fs"] = [fs(size_g=100, avail_g=4.0), fs("/srv", size_g=100, avail_g=50.0)]
        node.minutes(1)                                                      # a mount new in this run starts ok
        self.assertEqual(node.rs("disk")["sub"]["/srv"], "ok")

    def test_the_hysteresis_of_inodes_lives_through_the_bot(self):
        node = Node()
        node.minute["fs"] = [fs(files=1000, ffree=5)]
        node.minutes(2)
        node.minute["fs"] = [fs(files=1000, ffree=30)]                       # 97 %
        node.minutes(3)
        self.assertEqual(node.said("inodes"), "crit")
        st = H.from_disk(H.to_disk(node.st), node.mono + 7, node.now + 7)
        raw = {"mono": node.mono + 7, "fs": [fs(files=1000, ffree=30)], "mem": {"total": 100, "avail": 50}}
        st, evs = H.evaluate_instant(st, raw, CFG, {"owner": "bot"}, node.now + 7, node.mono + 7)
        self.assertEqual([e for e in evs if e["args"]["check"] == "inodes"], [])
        self.assertEqual(st["rules"]["inodes"]["said"], "crit")
        self.assertEqual((st["rules"]["inodes"]["level"], st["rules"]["inodes"]["pending"]), ("crit", None))

    # З-2
    def test_wait_says_how_much_history(self):
        node = Node()
        node.run(43)
        chk = H.light(node.st, "ru")["checks"]["cpu"]
        self.assertEqual((chk["level"], chk["why"]), ("wait", "wait"))
        self.assertEqual(chk["why_text"], "ждёт истории: 7 мин из 10")
        self.assertEqual(H.light(node.st, "en")["checks"]["cpu"]["why_text"], "waiting for history: 7 of 10 min")
        self.assertEqual(H.say(H.why_msg("wait"), "ru"), "ждёт истории")
        self.assertNotIn("why_text", H.light(node.st, False)["checks"]["cpu"])

    def test_no_text_keeps_a_placeholder(self):
        """Every text of light (top, info, checks) and of the events in RU and EN over many states: no «{…}»,
        no key, no None (the fuzz of REV-ns-a12, made a test)."""
        rnd = random.Random(7)
        bad = set()

        def chk(text, where):
            if "{" in text or "}" in text or "gw_host." in text or "None" in text:
                bad.add((where, text))
        for trial in range(6):
            node = Node(services={"dns": rnd.choice([True, False, None]), "xray": rnd.choice([True, False])},
                        inbounds_why={"vless": rnd.choice([True, False]), "awg2": rnd.choice([True, False])})
            for i in range(240):
                kw = {"busy": rnd.choice([10, 90, 97]), "steal": rnd.choice([0, 12, 30]), "iowait": rnd.choice([1, 45]),
                      "mem_pct": rnd.choice([50, 7, 3]), "swap_pct": rnd.choice([5, 93, 99]),
                      "conntrack_pct": rnd.choice([10, 92, 96]), "load_core": rnd.choice([0.2, 2.5]),
                      "psi_cpu": rnd.choice([1, 25]), "softnet": rnd.choice([0, 15]), "listen": rnd.choice([0, 2, 6])}
                if rnd.random() < 0.05:
                    kw[rnd.choice(list(kw))] = None
                minute = i % 6 == 0
                if minute:
                    kw["fs"] = [fs(avail_g=rnd.choice([50, 3, 0.3])),
                                fs("/var", size_g=10, avail_g=rnd.choice([5, 0.2]), files=100, ffree=rnd.choice([90, 1]))]
                    kw["clock"] = rnd.choice([{"synced": True, "offset_s": 0.001}, {"synced": True, "offset_s": 0.2},
                                              {"synced": False, "offset_s": 0.0}])
                    kw["oom_kill"] = i // 30
                hour = i % 60 == 0
                if hour:
                    kw["certs"] = rnd.choice([[], [{"what": "site", "name": "example.com", "days_left": 3}],
                                              [{"what": "ip", "hours_left": rnd.choice([70.0, 20.0]),
                                                "state": "expiring", "renew_left_h": 72, "try_ok": False,
                                                "try_at": node.now - 600, "reason": rnd.choice(
                                                    list(H.CERT_REASONS) + ["what"])}]])
                pr = series(losses={a: rnd.choice([0, 0, 1]) for a in A3},
                            rtts={"1.1.1.1": [rnd.choice([20.0, 90.0])] * 3})
                node.tick(minute=minute, hour=hour, probes=pr if rnd.random() < 0.8 else None, **kw)
                if rnd.random() < 0.02:
                    self.restart(node)
            for lang in ("ru", "en"):
                lt = H.light(node.st, lang)
                for f in lt["top"]:
                    chk(f["text"], "top")
                    chk(f["advice"], "advice")
                    for k in ("stale_msg", "better_msg"):
                        if f.get(k):
                            chk(H.say(f[k], lang), k)
                for it in lt["info"]:
                    chk(it["text"], "info")
                for rid, c in lt["checks"].items():
                    if "why_text" in c:
                        chk(c["why_text"], "why:" + rid)
                for e in node.events:
                    chk(H.event_text(e, lang), e["key"])
        self.assertEqual(sorted(bad), [])

    # З-3
    def test_the_reason_of_the_ip_certificate_in_words(self):
        for code in H.CERT_REASONS:
            node = Node()
            node.tick(hour=True, certs=[{"what": "ip", "hours_left": 20.0, "state": "expiring", "renew_left_h": 72,
                                         "try_ok": False, "try_at": node.now, "reason": code}])
            for lang in ("ru", "en"):
                text = H.light(node.st, lang)["top"][0]["text"]
                self.assertNotRegex(text, r":\s*%s$" % code, (code, lang))
                self.assertTrue(i18n.has("gw_host.reason." + code, lang))
        node = Node()
        node.tick(hour=True, certs=[{"what": "ip", "hours_left": 20.0, "state": "expiring", "renew_left_h": 72,
                                     "try_ok": False, "try_at": node.now, "reason": "challenge"}])
        self.assertEqual(H.light(node.st, "ru")["top"][0]["text"], "Сертификат для IP-адреса кончается через 20 ч, "
                         "продление не удалось: Let's Encrypt не достучался до порта 80")
        node = Node()
        node.tick(hour=True, certs=[{"what": "ip", "hours_left": 20.0, "state": "expiring", "renew_left_h": 72,
                                     "try_ok": False, "try_at": node.now, "reason": "something_new"}])
        self.assertEqual(node.rs("cert")["find"]["key"], "gw_host.find.cert_ip.crit_noreason")

    # З-4 (+ test_services_paint_the_light_without_events)
    def test_the_services_by_their_names(self):
        node = Node(services={"dns": True, "xray": False}, inbounds_why={"awg2": True, "vless": False})
        node.run(1)
        self.assertEqual(H.light(node.st, "ru")["top"][0]["text"], "Не работает: xray шлюза, AmneziaWG 2.0")
        self.assertEqual(H.light(node.st, "en")["top"][0]["text"], "Not working: Gateway xray, AmneziaWG 2.0")

    # З-5
    def test_russian_numbers(self):
        got = [H.say(H.msg("gw_host.n.times", n=n), "ru") for n in (1, 2, 5, 11, 21, 22)]
        self.assertEqual(got, ["1 раз", "2 раза", "5 раз", "11 раз", "21 раз", "22 раза"])
        self.assertEqual([H.say(H.msg("gw_host.n.times", n=n), "en") for n in (1, 2, 21)],
                         ["1 time", "2 times", "21 times"])
        self.assertEqual([H.say(H.msg("gw_host.n.x", n=x), "ru") for x in (5.0, 2.0, 3.4, 5.5, 21.0)],
                         ["в 5 раз", "в 2 раза", "в 3,4 раза", "в 5,5 раза", "в 21 раз"])
        self.assertEqual(H.say(H.msg("gw_host.n.x", n=3.4), "en"), "3.4×")
        f = H.msg("gw_host.find.listen.crit", n=1.0)
        self.assertEqual(H.say(f, "ru"), "Программы не справляются с соединениями: 1 отказ в секунду")
        self.assertEqual(H.say(dict(f, args={"n": 2.5}), "ru"),
                         "Программы не справляются с соединениями: 2,5 отказа в секунду")
        node = self.oom_node()
        self.assertEqual(H.light(node.st, "ru")["top"][0]["text"],
                         "Системе не хватило памяти, она убила программу (2 раза за 30 мин)")
        self.assertEqual(H.light(node.st, "en")["top"][0]["text"],
                         "The system ran out of memory and killed a program (2 times in 30 min)")

    def test_rtt_grew_five_times(self):
        node = Node()
        for _ in range(8):
            node.tick(hour=True, probes=series(rtts={a: [20.0, 20.0, 20.0] for a in A3}), dt=3600.0)
        node.run(30, probes=series(rtts={a: [100.0, 100.0, 100.0] for a in A3}))
        text = H.light(node.st, "ru")["top"][0]["text"]
        self.assertTrue(text.startswith("Отклик сети вырос в 5 раз:"), text)

    # NS-D23
    def test_listen_counts_listen_drops_only(self):
        r0 = {"mono": 0.0, "listen": {"overflows": 100, "drops": 100}}
        r1 = {"mono": 60.0, "listen": {"overflows": 160, "drops": 166}}
        s = H.derive(r0, r1)
        self.assertEqual((s["listen_n"], s["listen"]), (66, 1.1))

    # NS-D24 (revised 17.10, REV-ns-a12-2 З-10): by the mtime of probe-state.json, not by ``seq``
    def probe_oom(self, mts, seq_rare=True):
        """``mts`` — the mtime of probe-state.json at each minute read, as a function of the wall clock (None — no
        file); the last minute has the OOM of system.slice without a name of its own. ``probe_seq`` stays the same
        all the time (the seq of results grows in 1–2 % of minutes): the decision must not depend on it."""
        node = Node()
        cg = lambda o: {"v": 2, "local": False, "units": {}, "slices": {"system.slice": {"oom": o, "oom_kill": o}}}
        node.minute.update(cg=cg(0))
        for f in mts[:-1]:
            node.minute["probe_mt"] = f(node.now + 60.0)
            if seq_rare:
                node.ctx["probe_seq"] = 7
            node.minutes(1, mem_pct=40.0)
        node.minute["probe_mt"] = mts[-1](node.now + 60.0)
        node.minute.update(oom_kill=1, cg=cg(1))
        node.minutes(1, mem_pct=40.0)
        return node

    def test_the_probe_named_by_the_coincidence(self):
        every = lambda now: now - 5.0                                         # written every minute
        killed = [every, every, every, lambda now: now - 65.0]                # the last run did not write
        node = self.probe_oom(killed)
        rs = node.rs("oom_svc")
        self.assertEqual((rs["find"]["key"], rs["advice"]["key"]),
                         ("gw_host.find.oom_svc.warn_probe", "gw_host.advice.oom_svc"))
        self.assertEqual(H.light(node.st, "ru")["top"][0]["text"],
                         "Проба Kaskad, вероятно, упёрлась в свой лимит памяти (96 МБ) и была остановлена")
        node = self.probe_oom([every, every, every, lambda now: now - 1.0])    # the probe wrote: another service
        self.assertEqual((node.rs("oom_svc")["find"]["key"], node.rs("oom_svc")["advice"]["key"]),
                         ("gw_host.find.oom_svc.warn_any", "gw_host.advice.oom_svc_any"))
        node = self.probe_oom([lambda now: None] * 4)                        # no probe on this node
        self.assertEqual(node.rs("oom_svc")["find"]["key"], "gw_host.find.oom_svc.warn_any")
        old = lambda now: NOW0 - 3600.0                                       # the probe stopped long ago
        self.assertEqual(self.probe_oom([old] * 4).rs("oom_svc")["find"]["key"], "gw_host.find.oom_svc.warn_any")
        t = {}

        def gap(now):                                                         # a write, but 125 s after the last
            t.setdefault("w", now - 5.0)
            return t["w"]
        node = self.probe_oom([every, every, gap, lambda now: t["w"] + 125.0])
        self.assertEqual(node.rs("oom_svc")["find"]["key"], "gw_host.find.oom_svc.warn_probe")
        for lang in ("ru", "en"):
            adv = i18n.tr("gw_host.advice.oom_svc_any", lang).lower()
            self.assertNotIn("ваша", adv)
            self.assertNotIn("your", adv)
            self.assertNotIn("|", adv)                                        # REV-ns-a12-2 П-16

    def test_the_probe_state_path_is_the_probes(self):
        from kaskad.gw import hostprobe
        from kaskad.gw.probe import run
        self.assertEqual(hostprobe.probe_state_path(), run.state_path())

    def test_the_probe_constants_are_its_unit(self):
        from kaskad.gw.probe import unit
        self.assertEqual(H.PROBE_UNIT, unit.SERVICE)
        self.assertIn("MemoryMax=%dM" % (H.PROBE_MAX // H.MIB), unit.service_text())

    # П-1
    def test_both_certificates_crit_the_ip_one_is_the_problem(self):
        node = Node()
        node.tick(minute=True, hour=True, certs=[
            {"what": "site", "name": "example.com", "days_left": 3},
            {"what": "ip", "hours_left": 20.0, "state": "expiring", "renew_left_h": 72, "try_ok": None,
             "try_at": None, "reason": None}])
        lt = H.light(node.st, "ru")
        self.assertEqual([(a["id"], a["level"], a["problem"]) for a in lt["alarms"]], [("cert", "crit", True)])
        self.assertIn("IP", lt["top"][0]["text"])

    # П-3
    def test_quiet_until_lives_through_a_restart(self):
        node = Node()
        node.run(5, conntrack_pct=97.0)
        self.assertEqual(node.said("conntrack"), "crit")
        left = node.rs("conntrack")["quiet_until"] - node.mono
        k = len(node.events)
        self.restart(node, gap=7.0)
        self.assertAlmostEqual(node.rs("conntrack")["quiet_until"] - node.mono, left - 7.0, places=3)
        n = int((left - 7.0) // 10) - 1
        node.run(n, conntrack_pct=10.0)
        self.assertEqual(node.events[k:], [])                                # within the window: pending
        self.assertEqual(node.rs("conntrack")["pending"], "ok")
        node.run(3, conntrack_pct=10.0)
        self.assertEqual([e["key"] for e in node.events[k:]], ["gw_host.event.clear"])

    # П-5
    def test_a_wrong_type_never_stops_the_round(self):
        vals = [None, 0, -1, "x", [], {}, [1, 2], {"a": 1}, True, float("nan")]
        keys = ["cpu", "cores", "load", "uptime", "mem", "psi", "oom_kill", "net", "softnet", "tcp", "listen",
                "conntrack", "fd", "fs", "clock", "cg", "fd_proc", "certs", "virt", "ifaces", "absent", "mono"]
        good = {"mono": 10.0, "cpu": [1] * 8, "cores": 2, "load": [0.1, 0.2, 0.3], "ifaces": ["eth0"],
                "mem": {"total": 100, "avail": 50}, "minute": True, "hour": True, "oom_kill": 0,
                "net": {"eth0": {"rx_bytes": 1, "tx_bytes": 1, "rx_packets": 1, "tx_packets": 1, "rx_drop": 0,
                                 "tx_drop": 0, "rx_errs": 0, "tx_errs": 0}}}
        st = {}
        for k in keys:
            for v in vals:
                r1 = dict(good, mono=20.0)
                r1[k] = v
                s = H.derive(good, r1)
                st, _ev = H.evaluate(st, s, CFG, {"probes": {"anchors": [{"addr": "1.1.1.1", "n": "x"}, "y"]}},
                                     NOW0, 20.0)
                self.assertIn(st["rules"]["mem"]["level"], ("ok", "warn", "crit", "wait", "na"), (k, v))
                self.assertIn("cpu", st["rules"], (k, v))                    # every rule went on
        s = H.derive(good, dict(good, mono=20.0, fs=["/", {"mount": "/", "size": "x"}], certs=["x"]))
        st, _ev = H.evaluate({}, s, CFG, {}, NOW0, 20.0)
        H.light(st, "ru")
        H.minute_row(st, 20.0)


class CostTest(unittest.TestCase):
    """§5: derive + evaluate on full windows — the median ≤ 2 ms on the machine of the tests (the hard border 10 ms)."""

    def test_cost(self):
        node = Node()
        for i in range(130):
            node.tick(probes=series() if i % 3 == 0 else None)
        node.st["norm"]["rtt"] = dict((a, [20.0] * 24) for a in A3)
        prev_raw = {"mono": 1.0, "cpu": [1, 1, 1, 1, 1, 1, 1, 1], "cores": 2, "load": [0.1, 0.1, 0.1],
                    "mem": {"total": 2 * GIB, "avail": GIB, "swap_total": GIB, "swap_free": GIB},
                    "net": {"eth0": dict(rx_bytes=1, tx_bytes=1, rx_packets=1, tx_packets=1, rx_drop=0, tx_drop=0,
                                         rx_errs=0, tx_errs=0)}, "ifaces": ["eth0"],
                    "softnet": {"dropped": 0, "squeezed": 0}, "tcp": {"retrans": 0, "out_segs": 0},
                    "listen": {"overflows": 0, "drops": 0}, "conntrack": {"count": 1, "max": 10},
                    "fd": {"alloc": 1, "max": 100}}
        took = []
        for i in range(40):
            raw = dict(prev_raw, mono=prev_raw["mono"] + 10, cpu=[x + 10 * (i + 1) for x in prev_raw["cpu"]])
            t0 = time.perf_counter()
            s = H.derive(prev_raw, raw)
            s.update(minute=i % 6 == 0, fs=[fs()], clock={"synced": True, "offset_s": 0.0}, oom_kill=0)
            node.mono += 10
            node.now += 10
            node.st, _ = H.evaluate(node.st, s, node.cfg, dict(node.ctx, probes=series() if i % 3 == 0 else None),
                                    node.now, node.mono)
            took.append(time.perf_counter() - t0)
        self.assertLess(statistics.median(took), 0.010, statistics.median(took))



class Round3Test(unittest.TestCase):
    """REV-ns-a12-2 (A1/A2, round 2) with A5: З-8, З-9, П-12, П-13, П-15 — each red before."""

    def restart(self, node, gap=7.0):
        node.st = H.from_disk(json.loads(json.dumps(H.to_disk(node.st))), node.mono + gap, node.now + gap)
        node.mono += gap
        node.now += gap

    # З-8
    def test_two_mounts_crit_and_warn_through_a_restart_then_one_lower(self):
        node = Node()
        node.minute["fs"] = [fs(size_g=100, avail_g=1.0), fs("/var", size_g=75, avail_g=3.0)]   # 99 % / 96 %
        node.minutes(5)
        self.assertEqual(node.rs("disk")["sub"], {"/": "crit", "/var": "warn"})
        self.restart(node)
        self.assertEqual(node.rs("disk")["sub"], {"/": "crit", "/var": "warn"})
        k = len(node.events)
        node.minute["fs"] = [fs(size_g=100, avail_g=50.0), fs("/var", size_g=75, avail_g=3.0)]   # / cleaned
        node.minutes(20)
        evs = [e for e in node.events[k:] if e["args"]["check"] == "disk"]
        self.assertEqual([(e["key"], e["args"]["level"]) for e in evs], [("gw_host.event.lower", "warn")])
        self.assertEqual(node.said("disk"), "warn")

    def test_the_parts_of_hostmon_json_are_checked(self):
        doc = {"v": 1, "rules": {"disk": {"said": "crit", "sub": {"/": "crit", "/x": "bad", 5: "ok"}},
                                 "cert": {"said": "warn", "sub": ["site"]}}}
        st = H.from_disk(doc, 1.0, NOW0)
        self.assertEqual(st["rules"]["disk"]["sub"], {"/": "crit"})
        self.assertNotIn("sub", st["rules"]["cert"])
        many = dict(("/m%02d" % i, "ok") for i in range(40))
        st = H.from_disk({"rules": {"disk": {"said": "ok", "sub": many}}}, 1.0, NOW0)
        self.assertEqual(len(st["rules"]["disk"]["sub"]), H.SUB_MAX)

    # З-9
    def test_wait_of_a_minute_rule_says_its_numbers_in_every_round(self):
        node = Node()
        node.minutes(3, mem_pct=5.0)
        node.minute["oom_kill"] = 2
        node.minutes(1, mem_pct=5.0)
        self.assertEqual(node.said("oom"), "crit")
        self.restart(node)
        node.run(12, mem_pct=40.0)                                     # 10-s rounds, the minute every 6th
        texts = []
        for _ in range(12):
            node.tick(mem_pct=40.0)
            texts.append(H.light(node.st, "ru")["checks"]["oom"].get("why_text"))
        self.assertTrue(all(t and t.startswith("ждёт истории:") and "из 30" in t for t in texts), texts)

    # П-12 (1)
    def test_oom_crit_holds_through_a_restart_against_a_group_oom(self):
        node = Node()
        node.minutes(3, mem_pct=5.0)
        node.minute["oom_kill"] = 2
        node.minutes(1, mem_pct=5.0)
        self.assertEqual(node.said("oom"), "crit")
        self.restart(node)
        k = len(node.events)
        node.minutes(3, mem_pct=40.0)
        node.minute["oom_kill"] = 3                                    # the memory is fine: a limit of a group
        node.minutes(2, mem_pct=40.0)
        self.assertEqual([e for e in node.events[k:] if e["args"]["check"] == "oom"], [])
        self.assertEqual((node.level("oom"), node.said("oom")), ("wait", "crit"))

    # П-12 (2)
    def test_the_names_of_the_services_down_live_through_a_restart(self):
        node = Node()
        node.ctx["services"] = {"dns": False, "xray": True}
        node.run(4)
        self.assertEqual(node.said("services"), "crit")
        before = H.light(node.st, "ru")["top"][0]["text"]
        self.restart(node)
        self.assertEqual(H.light(node.st, "ru")["top"][0]["text"], before)
        self.assertIn(i18n.tr("gw_host.svc.dns", "ru"), before)

    # П-13
    def test_a_part_skipped_is_named_in_the_state(self):
        node = Node()
        def boom(*a):
            raise ValueError("bad")
        boom.__name__ = "_oom_minute"
        with mock.patch.object(H, "_oom_minute", boom):
            node.minutes(1)
        self.assertEqual(node.st["errors"], ["oom_minute: ValueError"])
        node.minutes(1)
        self.assertEqual(node.st["errors"], [])

    # П-15
    def test_a_failed_read_of_the_site_keeps_its_level_and_the_ip_goes_on(self):
        node = Node()
        node.tick(hour=True, certs=[{"what": "site", "name": "example.com", "days_left": 10.0}])
        self.assertEqual(node.level("cert"), "warn")
        node.tick(hour=True, dt=3600.0, certs=[{"what": "site", "failed": True},
                                               {"what": "ip", "hours_left": 300.0, "state": "valid",
                                                "renew_left_h": 72}])
        rs = node.rs("cert")
        self.assertEqual((rs["level"], rs["what"]), ("warn", "site"))
        self.assertAlmostEqual(rs["value"], 10.0 - 1 / 24.0, places=1)
        node = Node()                                                  # no site seen yet: the IP alone
        node.tick(hour=True, certs=[{"what": "site", "failed": True},
                                    {"what": "ip", "hours_left": 20.0, "state": "expiring", "renew_left_h": 72}])
        self.assertEqual((node.level("cert"), node.rs("cert")["what"]), ("crit", "ip"))

if __name__ == "__main__":
    unittest.main()

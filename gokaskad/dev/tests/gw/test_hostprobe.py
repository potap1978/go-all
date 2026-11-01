"""gw/hostprobe.py (SPEC-node-score §3.1, A1): the raw counters of the node from a fake /proc and /sys under
KASKAD_ROOT — every field, None on any error (never an exception), the kernel without PSI / nf_conntrack, a
container, cgroup v1/v2, memory.events of the top-level cgroups and a unit gone between two reads, the IP
certificate, the clock of one adjtimex(), the budget of opened files."""

import builtins
import errno
import os
import shutil
from unittest import mock

from kaskad.gw import collect, hostprobe, live
from tests.helpers import RootCase

STAT = """cpu  1000 10 500 80000 300 20 40 60 0 0
cpu0 500 5 250 40000 150 10 20 30 0 0
cpu1 500 5 250 40000 150 10 20 30 0 0
intr 12345
ctxt 999
"""
MEMINFO = """MemTotal:        2000000 kB
MemFree:          300000 kB
MemAvailable:     800000 kB
Buffers:           10000 kB
Cached:           400000 kB
SwapTotal:        1000000 kB
SwapFree:          900000 kB
"""
PSI = "some avg10=1.00 avg60=12.34 avg300=3.00 total=123\nfull avg10=0.00 avg60=0.50 avg300=0.00 total=1\n"
NETDEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:  100 1 0 0 0 0 0 0  100 1 0 0 0 0 0 0
  eth0: 5000 50 1 2 0 0 0 0 7000 70 3 4 0 0 0 0
  eth1: 600 6 0 0 0 0 0 0 800 8 0 0 0 0 0 0
"""
SOFTNET = "0000a000 00000002 00000003 00000000\n0000b000 00000001 00000004 00000000\n"
SNMP = """Ip: Forwarding DefaultTTL
Ip: 1 64
Tcp: RtoAlgorithm RtoMin RtoMax MaxConn ActiveOpens PassiveOpens AttemptFails EstabResets CurrEstab InSegs OutSegs RetransSegs InErrs OutRsts InCsumErrors
Tcp: 1 200 120000 -1 10 20 0 0 5 1000 2000 30 0 0 0
Udp: InDatagrams NoPorts
Udp: 5 6
"""
NETSTAT = """TcpExt: SyncookiesSent ListenOverflows ListenDrops
TcpExt: 0 7 9
IpExt: InNoRoutes
IpExt: 0
"""
ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
eth0\t00000000\t0102A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0
eth0\t0002A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0
"""
ROUTE6 = ("00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
          "fe800000000000000000000000000001 00000400 00000001 00000000 00000003 eth1\n"
          "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
          "00000000000000000000000000000000 ffffffff 00000001 00000000 00200200 lo\n")
EVENTS = "low 0\nhigh 0\nmax 3\noom {oom}\noom_kill {kill}\noom_group_kill 0\n"
LIMITS = """Limit                     Soft Limit           Hard Limit           Units
Max cpu time              unlimited            unlimited            seconds
Max open files            1048576              1048576              files
"""


class HostProbeCase(RootCase):
    def setUp(self):
        super().setUp()
        hostprobe.reset()
        self.addCleanup(hostprobe.reset)

    def tree(self, cg=True, psi=True, conntrack=True):
        w = self.write
        w("/proc/stat", STAT)
        w("/proc/loadavg", "0.10 0.20 0.30 1/100 1234\n")
        w("/proc/meminfo", MEMINFO)
        if psi:
            for k in ("cpu", "memory", "io"):
                w("/proc/pressure/" + k, PSI)
        w("/proc/vmstat", "nr_free_pages 1\noom_kill 4\npgfault 9\n")
        w("/proc/net/dev", NETDEV)
        w("/proc/net/softnet_stat", SOFTNET)
        w("/proc/net/snmp", SNMP)
        w("/proc/net/netstat", NETSTAT)
        w("/proc/net/route", ROUTE)
        if conntrack:
            w("/proc/sys/net/netfilter/nf_conntrack_count", "900\n")
            w("/proc/sys/net/netfilter/nf_conntrack_max", "1000\n")
        w("/proc/sys/fs/file-nr", "2048\t0\t9223372036854775807\n")
        w("/proc/uptime", "3600.50 7000.00\n")
        w("/proc/1/environ", "PATH=/bin\0HOME=/\0")
        w("/proc/cpuinfo", "processor\t: 0\nflags\t\t: fpu vme hypervisor sse\n")
        os.makedirs(self.path("/var/lib/kaskad"), exist_ok=True)
        if cg:
            w("/sys/fs/cgroup/cgroup.controllers", "cpu memory io\n")
            w("/proc/mounts", "cgroup2 /sys/fs/cgroup cgroup2 rw,nosuid,nodev,noexec,relatime,nsdelegate 0 0\n")
            w("/sys/fs/cgroup/system.slice/memory.events", EVENTS.format(oom=1, kill=2))
            w("/sys/fs/cgroup/user.slice/memory.events", EVENTS.format(oom=0, kill=0))
            os.makedirs(self.path("/sys/fs/cgroup/init.scope"), exist_ok=True)       # no memory.events — not a slice
            self.unit("kaskad-gw-probe.service", "100663296", oom=0, kill=0)
            self.unit("kaskad-panel.service", "max", oom=0, kill=0)                    # no limit: not in cg.units

    def unit(self, name, mx, oom=0, kill=0):
        self.write("/sys/fs/cgroup/system.slice/%s/memory.max" % name, mx + "\n")
        self.write("/sys/fs/cgroup/system.slice/%s/memory.events" % name, EVENTS.format(oom=oom, kill=kill))

    def procs(self):
        """xray of the gateway (pid 100, by its cgroup) and two amneziawg-go (200 with 3 files, 201 with 1)."""
        self.write("/sys/fs/cgroup/system.slice/%s/cgroup.procs" % hostprobe.XRAY_UNIT, "100\n")
        for pid, comm, nfd in ((100, "xray", 4), (200, "amneziawg-go", 3), (201, "amneziawg-go", 1)):
            self.write("/proc/%d/comm" % pid, comm + "\n")
            self.write("/proc/%d/limits" % pid, LIMITS)
            for i in range(nfd):
                self.write("/proc/%d/fd/%d" % (pid, i), "")


class ReadTest(HostProbeCase):
    def test_a_normal_node_gives_every_field(self):
        self.tree()
        self.procs()
        with mock.patch.object(hostprobe, "_adjtimex", lambda: (0, 0, 1500)):
            raw = hostprobe.read({}, 100.0, ("minute",))
        self.assertEqual(raw["cpu"], [1000, 10, 500, 80000, 300, 20, 40, 60])
        self.assertEqual(raw["cores"], 2)
        self.assertEqual(raw["load"], [0.1, 0.2, 0.3])
        self.assertEqual(raw["uptime"], 3600.5)
        self.assertEqual(raw["mem"], {"total": 2048000000, "avail": 819200000, "swap_total": 1024000000,
                                      "swap_free": 921600000})
        self.assertEqual(raw["psi"], {"cpu": 12.34, "memory": 12.34, "io": 12.34})
        self.assertEqual(raw["oom_kill"], 4)
        self.assertEqual(raw["net"]["eth0"], {"rx_bytes": 5000, "rx_packets": 50, "rx_errs": 1, "rx_drop": 2,
                                              "tx_bytes": 7000, "tx_packets": 70, "tx_errs": 3, "tx_drop": 4})
        self.assertIn("lo", raw["net"])
        self.assertEqual(raw["softnet"], {"dropped": 3, "squeezed": 7})
        self.assertEqual(raw["tcp"], {"retrans": 30, "out_segs": 2000})
        self.assertEqual(raw["listen"], {"overflows": 7, "drops": 9})
        self.assertEqual(raw["conntrack"], {"count": 900, "max": 1000})
        self.assertEqual(raw["fd"], {"alloc": 2048, "max": 9223372036854775807})
        self.assertEqual(raw["ifaces"], ["eth0"])
        self.assertEqual(raw["virt"], "kvm")
        self.assertEqual(raw["clock"], {"synced": True, "offset_s": 0.0015})
        self.assertEqual(len(raw["fs"]), 1)
        self.assertEqual(raw["fs"][0]["mount"], "/")
        for k in ("fsid", "size", "free", "avail", "files", "ffree"):
            self.assertIn(k, raw["fs"][0])
        self.assertEqual(raw["cg"]["v"], 2)
        self.assertFalse(raw["cg"]["local"])
        self.assertEqual(sorted(raw["cg"]["slices"]), ["system.slice", "user.slice"])      # machine.slice: none here
        self.assertEqual(raw["cg"]["slices"]["system.slice"], {"oom": 1, "oom_kill": 2})
        self.assertEqual(raw["cg"]["units"], {"kaskad-gw-probe.service": {"max": 100663296, "oom": 0,
                                                                          "oom_kill": 0}})
        self.assertEqual(raw["fd_proc"], [{"who": "xray", "open": 4, "soft": 1048576},
                                          {"who": "amneziawg-go", "open": 3, "soft": 1048576}])
        self.assertIsNone(raw["certs"])                                     # not the hour set
        self.assertEqual(raw["absent"], [])

    def test_the_round_set_alone(self):
        self.tree()
        raw = hostprobe.read({}, 100.0)
        for k in ("fs", "clock", "cg", "fd_proc", "certs"):
            self.assertIsNone(raw[k], k)
        self.assertEqual(raw["cpu"][0], 1000)
        self.assertEqual(raw["ifaces"], ["eth0"])                            # the first round reads the route

    def test_no_files_at_all_gives_none_without_exceptions(self):
        raw = hostprobe.read({}, 1.0, ("minute", "hour"))
        for k in ("cpu", "cores", "load", "uptime", "mem", "psi", "oom_kill", "net", "softnet", "tcp", "listen",
                  "conntrack", "fd", "ifaces", "cg", "fd_proc", "virt"):
            self.assertIsNone(raw[k], k)                                    # fs: the temp root is a file system
        self.assertEqual(raw["certs"], [])

    def test_broken_lines_and_empty_files_give_none(self):
        self.tree()
        for p, text in (("/proc/stat", "cpu  a b c d\n"), ("/proc/loadavg", "x y\n"), ("/proc/meminfo", "junk\n"),
                        ("/proc/vmstat", "oom_kill abc\n"), ("/proc/net/dev", "head\nhead\n eth0: 1 2 3\n"),
                        ("/proc/net/softnet_stat", "zz yy xx\n"), ("/proc/net/snmp", "Tcp: A B\nTcp: 1\n"),
                        ("/proc/net/netstat", ""), ("/proc/sys/net/netfilter/nf_conntrack_count", "lots\n"),
                        ("/proc/sys/fs/file-nr", "1\n"), ("/proc/uptime", ""), ("/proc/pressure/io", "full x\n")):
            self.write(p, text)
        self.write("/sys/fs/cgroup/system.slice/memory.events", "oom x\n")
        raw = hostprobe.read({}, 1.0, ("minute",))
        for k in ("cpu", "cores", "load", "mem", "oom_kill", "net", "softnet", "tcp", "listen", "conntrack", "fd",
                  "uptime", "psi"):
            self.assertIsNone(raw[k], k)
        self.assertNotIn("psi", raw["absent"])                              # a bad read is not «no PSI»
        self.assertNotIn("conntrack", raw["absent"])
        self.assertEqual(sorted(raw["cg"]["slices"]), ["user.slice"])       # the broken file is left out

    def test_a_kernel_without_psi_and_conntrack(self):
        self.tree(psi=False, conntrack=False)
        raw = hostprobe.read({}, 1.0)
        self.assertIsNone(raw["psi"])
        self.assertIsNone(raw["conntrack"])
        self.assertEqual(sorted(raw["absent"]), ["conntrack", "psi"])

    def test_psi_switched_off_by_the_kernel(self):
        """psi=0 on the command line: the files are there, reading them fails with EOPNOTSUPP — «no PSI»."""
        self.tree()
        real = builtins.open

        def fake(path, *a, **kw):
            if "/proc/pressure/" in str(path):
                raise OSError(errno.EOPNOTSUPP, "Operation not supported")
            return real(path, *a, **kw)
        with mock.patch("builtins.open", fake):
            raw = hostprobe.read({}, 1.0)
        self.assertIsNone(raw["psi"])
        self.assertIn("psi", raw["absent"])

    def test_containers(self):
        self.tree()
        self.write("/proc/1/environ", "PATH=/bin\0container=lxc\0")
        self.assertEqual(hostprobe.read({}, 1.0)["virt"], "lxc")
        hostprobe.reset()
        self.write("/proc/1/environ", "PATH=/bin\0")
        self.write("/run/systemd/container", "docker\n")
        self.assertEqual(hostprobe.virt(), "docker")
        hostprobe.reset()
        os.remove(self.path("/run/systemd/container"))
        os.makedirs(self.path("/proc/vz"))
        self.assertEqual(hostprobe.virt(), "openvz")
        hostprobe.reset()
        os.rmdir(self.path("/proc/vz"))
        self.write("/proc/cpuinfo", "flags\t: fpu sse\n")
        self.assertEqual(hostprobe.virt(), "none")

    def test_virt_is_read_once_per_process(self):
        self.tree()
        self.assertEqual(hostprobe.virt(), "kvm")
        self.write("/proc/1/environ", "container=lxc\0")
        self.assertEqual(hostprobe.virt(), "kvm")

    def test_default_routes_ipv6_and_two_interfaces(self):
        self.tree()
        self.write("/proc/net/ipv6_route", ROUTE6)
        raw = hostprobe.read({}, 1.0, ("minute",))
        self.assertEqual(raw["ifaces"], ["eth0", "eth1"])
        hostprobe.reset()
        self.write("/proc/net/route", ROUTE + "eth1\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n")
        os.remove(self.path("/proc/net/ipv6_route"))
        self.assertEqual(hostprobe.read({}, 1.0, ("minute",))["ifaces"], ["eth0", "eth1"])
        hostprobe.reset()
        os.remove(self.path("/proc/net/route"))
        self.write("/proc/net/ipv6_route", ROUTE6)
        self.assertEqual(hostprobe.read({}, 1.0, ("minute",))["ifaces"], ["eth1"])          # IPv6 alone

    def test_the_route_is_kept_a_minute(self):
        self.tree()
        self.assertEqual(hostprobe.read({}, 1.0)["ifaces"], ["eth0"])
        self.write("/proc/net/route", ROUTE.replace("eth0", "ens3"))
        self.assertEqual(hostprobe.read({}, 30.0)["ifaces"], ["eth0"])
        self.assertEqual(hostprobe.read({}, 62.0)["ifaces"], ["ens3"])

    def test_statvfs_one_record_per_file_system(self):
        self.tree()

        class S(object):
            def __init__(self, fsid):
                self.f_fsid, self.f_frsize, self.f_bsize = fsid, 4096, 4096
                self.f_blocks, self.f_bfree, self.f_bavail, self.f_files, self.f_ffree = 1000, 200, 150, 500, 100
        ids = {"/": 1, "/var": 1, "/var/lib/kaskad": 2}
        with mock.patch.object(hostprobe, "_statvfs", lambda p: S(ids[p[len(self.root):] or "/"])):
            fs = hostprobe.read({}, 1.0, ("minute",))["fs"]
        self.assertEqual([f["mount"] for f in fs], ["/", "/var/lib/kaskad"])
        self.assertEqual(fs[0]["size"], 4096000)
        self.assertEqual(fs[0]["avail"], 614400)
        self.assertEqual(fs[0]["free"], 819200)


class CgroupTest(HostProbeCase):
    def test_cgroup_v1_gives_no_cg(self):
        self.tree(cg=False)
        os.makedirs(self.path("/sys/fs/cgroup/memory"))
        raw = hostprobe.read({}, 1.0, ("minute",))
        self.assertIsNone(raw["cg"])
        self.assertIn("cg", raw["absent"])

    def test_memory_localevents(self):
        self.tree()
        self.write("/proc/mounts", "cgroup2 /sys/fs/cgroup cgroup2 rw,nosuid,nsdelegate,memory_localevents 0 0\n")
        self.assertTrue(hostprobe.read({}, 1.0, ("minute",))["cg"]["local"])

    def test_every_top_level_cgroup_with_memory_events(self):
        """REV-3 З-6: LXD (lxc.payload.<name>) and Docker with cgroupfs (docker/) are slices too; a top-level directory
        without memory.events is not."""
        self.tree()
        self.write("/sys/fs/cgroup/lxc.payload.x/memory.events", EVENTS.format(oom=0, kill=0))
        self.write("/sys/fs/cgroup/docker/memory.events", EVENTS.format(oom=5, kill=5))
        cg = hostprobe.read({}, 1.0, ("minute",))["cg"]
        self.assertEqual(sorted(cg["slices"]), ["docker", "lxc.payload.x", "system.slice", "user.slice"])
        self.assertEqual(cg["slices"]["docker"], {"oom": 5, "oom_kill": 5})
        self.assertEqual(cg["over"], 0)

    def test_at_most_eight_slices(self):
        self.tree()
        for i in range(10):
            self.write("/sys/fs/cgroup/lxc.payload.n%d/memory.events" % i, EVENTS.format(oom=0, kill=0))
        cg = hostprobe.read({}, 1.0, ("minute",))["cg"]
        self.assertEqual(len(cg["slices"]), hostprobe.SLICES_MAX)
        self.assertIn("system.slice", cg["slices"])
        self.assertIn("user.slice", cg["slices"])
        self.assertEqual(cg["over"], 12 - hostprobe.SLICES_MAX)

    def test_a_unit_gone_between_two_reads(self):
        """REV-2 С-1: the oneshot probe was in the list of the hour and its cgroup is gone the next minute: None for
        the unit, the slices read, no exception."""
        self.tree()
        cg = hostprobe.read({}, 1.0, ("minute",))["cg"]
        self.assertIsNotNone(cg["units"]["kaskad-gw-probe.service"])
        shutil.rmtree(self.path("/sys/fs/cgroup/system.slice/kaskad-gw-probe.service"))
        self.write("/sys/fs/cgroup/system.slice/memory.events", EVENTS.format(oom=2, kill=3))
        cg = hostprobe.read({}, 61.0, ("minute",))["cg"]
        self.assertEqual(cg["units"], {"kaskad-gw-probe.service": None})
        self.assertEqual(cg["slices"]["system.slice"], {"oom": 2, "oom_kill": 3})
        cg = hostprobe.read({}, 61.0 + hostprobe.LIST_EVERY, ("minute",))["cg"]     # the list of the next hour
        self.assertEqual(cg["units"], {})

    def test_a_new_unit_with_a_limit_waits_for_the_list_of_the_hour(self):
        self.tree()
        hostprobe.read({}, 1.0, ("minute",))
        self.unit("kaskad-panel2.service", "402653184")
        self.assertNotIn("kaskad-panel2.service", hostprobe.read({}, 61.0, ("minute",))["cg"]["units"])
        units = hostprobe.read({}, 1.0 + hostprobe.LIST_EVERY, ("minute",))["cg"]["units"]
        self.assertEqual(units["kaskad-panel2.service"]["max"], 402653184)

    def test_a_process_gone_gives_its_new_pid_next_minute(self):
        self.tree()
        self.procs()
        hostprobe.read({}, 1.0, ("minute",))
        shutil.rmtree(self.path("/proc/100"))
        self.write("/sys/fs/cgroup/system.slice/%s/cgroup.procs" % hostprobe.XRAY_UNIT, "300\n")
        self.write("/proc/300/comm", "xray\n")
        self.write("/proc/300/limits", LIMITS)
        self.write("/proc/300/fd/0", "")
        got = hostprobe.read({}, 61.0, ("minute",))["fd_proc"]
        self.assertEqual([x["who"] for x in got], ["amneziawg-go"])
        got = hostprobe.read({}, 121.0, ("minute",))["fd_proc"]
        self.assertEqual(got[0], {"who": "xray", "open": 1, "soft": 1048576})


class CertTest(HostProbeCase):
    def status(self, hours, last_try=None, state="valid"):
        return {"state": state, "ip": "203.0.113.5", "hours_left": hours, "days_left": round(hours / 24.0, 1),
                "renew_left_h": 72, "last_try": last_try, "staging": False}

    def test_the_ip_certificate_by_store_status(self):
        from kaskad.ipcert import store
        with mock.patch.object(store, "status", lambda ip=None: self.status(160.0)):
            certs = hostprobe.read({}, 1.0, ("hour",))["certs"]
        self.assertEqual(certs, [{"what": "ip", "hours_left": 160.0, "state": "valid", "renew_left_h": 72,
                                  "try_ok": None, "try_at": None, "reason": None}])
        lt = {"at": "2026-10-17T10:00:00Z", "ok": False, "reason": "rate_limited", "text": "…"}
        with mock.patch.object(store, "status", lambda ip=None: self.status(20.0, lt, "expiring")):
            certs = hostprobe.read({}, 1.0, ("hour",))["certs"]
        self.assertEqual(certs[0]["hours_left"], 20.0)
        self.assertEqual(certs[0]["state"], "expiring")
        self.assertIs(certs[0]["try_ok"], False)
        self.assertEqual(certs[0]["try_at"], 1792231200.0)
        self.assertEqual(certs[0]["reason"], "rate_limited")
        with mock.patch.object(store, "status", lambda ip=None: dict(self.status(1.0), state="none")):
            self.assertEqual(hostprobe.read({}, 1.0, ("hour",))["certs"], [])            # no certificate in use

    def test_the_site_certificate(self):
        from kaskad.site import certinfo, model as sm
        from kaskad.ipcert import store
        st = {"site": {"domain": "example.com", "mode": "domain"}}
        info = {"names": ["example.com"], "not_after_ts": 0}
        with mock.patch.object(sm, "copy_info", lambda: info), \
                mock.patch.object(certinfo, "days_left", lambda i: 6):
            certs = hostprobe.read({"st": st}, 1.0, ("hour",))["certs"]
        self.assertEqual(certs, [{"what": "site", "name": "example.com", "days_left": 6}])
        with mock.patch.object(sm, "copy_info", lambda: dict(info, names=["other.example.com"])):
            self.assertEqual(hostprobe.read({"st": st}, 1.0, ("hour",))["certs"], [])     # the copy is not for it
        # REV-ns-a12 П-4 / REV-ns-a12-2 П-15: a read of the site that failed is not «no certificate» (hostmon would
        # say «не проверяется» of an alarm) and does not stop the IP one: the site is marked «failed»
        with mock.patch.object(sm, "copy_info", mock.Mock(side_effect=OSError("broken"))):
            self.assertEqual(hostprobe.read({"st": st}, 1.0, ("hour",))["certs"], [{"what": "site", "failed": True}])
        with mock.patch.object(sm, "copy_info", mock.Mock(side_effect=OSError("broken"))), \
                mock.patch.object(store, "status", lambda ip=None: self.status(30.0)):
            certs = hostprobe.read({"st": st}, 1.0, ("hour",))["certs"]
        self.assertEqual([c["what"] for c in certs], ["site", "ip"])
        self.assertTrue(certs[0]["failed"])
        from kaskad.ipcert import store
        with mock.patch.object(sm, "copy_info", lambda: info), \
                mock.patch.object(certinfo, "days_left", lambda i: 6), \
                mock.patch.object(store, "status", mock.Mock(side_effect=OSError("broken"))):
            self.assertIsNone(hostprobe.read({"st": st}, 1.0, ("hour",))["certs"])


class ClockTest(HostProbeCase):
    def test_one_adjtimex_gives_synced_and_offset(self):
        with mock.patch.object(hostprobe, "_adjtimex", lambda: (0, 0, -25000)):
            self.assertEqual(hostprobe.clock(), {"synced": True, "offset_s": -0.025})
        with mock.patch.object(hostprobe, "_adjtimex", lambda: (5, hostprobe.STA_UNSYNC, 0)):
            self.assertEqual(hostprobe.clock()["synced"], False)
        with mock.patch.object(hostprobe, "_adjtimex", lambda: (0, hostprobe.STA_NANO, 1400000000)):
            self.assertEqual(hostprobe.clock(), {"synced": True, "offset_s": 1.4})
        with mock.patch.object(hostprobe, "_adjtimex", lambda: None):
            self.assertIsNone(hostprobe.clock())
        with mock.patch.object(hostprobe, "_adjtimex", mock.Mock(side_effect=OSError("no libc"))):
            self.assertIsNone(hostprobe.clock())

    def test_collect_clock_synced_wraps_it_with_timedatectl_behind(self):
        runner = mock.Mock()
        with mock.patch.object(hostprobe, "_adjtimex", lambda: (5, hostprobe.STA_UNSYNC, 0)):
            self.assertFalse(collect._clock_synced(runner))
        with mock.patch.object(hostprobe, "_adjtimex", lambda: (0, 0, 0)):
            self.assertTrue(collect._clock_synced(runner))
        runner.run.assert_not_called()
        runner.run.return_value = mock.Mock(ok=True, out="yes\n")
        with mock.patch.object(hostprobe, "_adjtimex", lambda: None):
            self.assertTrue(collect._clock_synced(runner))
        self.assertEqual(runner.run.call_args[0][0], ["timedatectl", "show", "-p", "NTPSynchronized", "--value"])

    def test_the_unit_of_xray_is_the_one_of_live(self):
        self.assertEqual(hostprobe.XRAY_UNIT, live.XRAY_UNIT)


class BudgetTest(HostProbeCase):
    """§5 «Бюджет», A1: opened files per full round ≤ 16, per minute round ≤ 34 (open + os.listdir)."""

    def count(self, fn):
        n = [0]
        real_open, real_list = builtins.open, os.listdir

        def o(*a, **kw):
            n[0] += 1
            return real_open(*a, **kw)

        def ls(*a, **kw):
            n[0] += 1
            return real_list(*a, **kw)
        with mock.patch("builtins.open", o), mock.patch("os.listdir", ls), \
                mock.patch.object(hostprobe, "_adjtimex", lambda: (0, 0, 0)):
            fn()
        return n[0]

    def test_opened_files(self):
        self.tree()
        self.procs()
        for i in range(8):                                          # the most the minute set reads
            self.write("/sys/fs/cgroup/lxc.payload.n%d/memory.events" % i, EVENTS.format(oom=0, kill=0))
        for i in range(4):
            self.unit("kaskad-x%d.service" % i, "1000000")
        with mock.patch.object(hostprobe, "_adjtimex", lambda: (0, 0, 0)):
            hostprobe.read({}, 1.0, ("minute",))                    # the facts of the process and the lists of the hour
        full = self.count(lambda: hostprobe.read({}, 11.0))
        minute = self.count(lambda: hostprobe.read({}, 61.0, ("minute",)))
        self.assertLessEqual(full, 16)
        self.assertLessEqual(minute, 34)
        self.assertGreaterEqual(minute, full + 8 + 4)                # really read: slices, units

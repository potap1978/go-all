"""A node for the tests of the server check: the files of /proc, /sys and /etc from tests/fixtures/bench under
KASKAD_ROOT, a FakeRunner that answers like sysbench 1.0.20, fio 3.33, OpenSSL 3.0.14, lscpu, lsblk, systemd-detect-virt,
``systemctl show`` and ``systemd-run``, and the patches every test of the check needs (the disk of the work directory,
the DNS, the probes, the sleeps)."""

import os
import re
from unittest import mock

from kaskad import state, sysrun
from kaskad.bench import channel, quiet, suite
from tests.helpers import RootCase

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "bench")
MID = "0123456789abcdef0123456789abcdef"
AEAD = "real/debian12/42-openssl-%s-aead-mr.out"      # «openssl speed -aead … -mr» of OpenSSL 3.0.14


def fx(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class StatVfs(object):
    def __init__(self, free_gib=20.0, used_pct=40.0, size_gib=40.0):
        self.f_frsize = 4096
        self.f_blocks = int(size_gib * 1024 ** 3 / 4096)
        self.f_bavail = int(free_gib * 1024 ** 3 / 4096)
        self.f_bfree = int(self.f_blocks * (1 - used_pct / 100.0))


class FakeProber(object):
    """3 anchors; ``lost`` — packets lost per series for each anchor."""

    def __init__(self, lost=(0, 0, 1), rtt=(10.0, 20.0, 30.0)):
        self.lost, self.rtt, self.n = lost, rtt, 0

    def series(self):
        self.n += 1
        out = []
        for i, a in enumerate(("1.1.1.1", "8.8.8.8", "77.88.8.8")):
            out.append({"addr": a, "via": "icmp", "n": 3, "lost": self.lost[i], "runs": 0,
                        "rtt": [self.rtt[i]] * (3 - self.lost[i]), "na": None})
        return {"anchors": out, "excl": False, "why": None, "trial": []}


def sample(t, rx=0, unit=None, busy=0, total=0, io=0):
    return {"t": t, "ifs": {"eth0": (rx, 0, 0, 0)}, "links": {"eth0": {"h": 14, "mtu": "1500"}}, "cpu": (busy, total),
            "io": io, "unit": unit, "dev": None, "family": None, "in_unit": True}


class FakeMeter(object):
    """Samples by a function of the number of the read: ``mbps`` of people, the unit's counters known or not."""

    def __init__(self, mbps=0.0, known=True, cpu_pct=0.0, io_pct=0.0, fail=()):
        self.mbps, self.known, self.cpu_pct, self.io_pct, self.i = mbps, known, cpu_pct, io_pct, 0
        self.fail = set(fail)               # the numbers of the reads whose ``systemctl show`` failed

    def read(self):
        self.i += 1
        t = self.i * 10.0
        u = {"rx": 1000 * self.i, "tx": 2000 * self.i, "rxp": 0, "txp": 0, "cpu_ns": 10 ** 9 * self.i} \
            if self.known and self.i not in self.fail else None
        return sample(t, rx=int(self.mbps * 1e6 / 8 * t), unit=u, busy=int(self.cpu_pct * t), total=int(100 * t),
                      io=int(self.io_pct * t))


def tcp_snap(sent, retr=0, dsack=0, mss=1448, rtt=30000, min_rtt=20000):
    return {"snd_mss": mss, "rtt": rtt, "min_rtt": min_rtt, "bytes_sent": sent, "bytes_retrans": retr,
            "dsack_dups": dsack, "bytes_acked": sent}


class FakeNet(object):
    """The network of the channel test (bench.channel.Net) — never a real one in the tests: ``down``/``up`` Mbit/s
    per try (a list — one value per try, the last repeats; None — the target failed), the calls in ``calls``."""

    def __init__(self, down=640.0, up=320.0, iperf_up=410.0, budget=False, lower=False, load=None, family=4,
                 use_probe=True):
        self.vals = {"down": down, "up": up, "iperf": iperf_up}
        self.use_probe = use_probe
        self.budget, self.lower, self.load, self.family = budget, lower, load, family
        self.calls = []

    def _next(self, key):
        v = self.vals[key]
        if isinstance(v, list):
            n = sum(1 for c in self.calls if c[0] == key)
            return v[min(n, len(v) - 1)]
        return v

    def _try(self, key, how, target, cap, probe):
        v = self._next(key)
        self.calls.append((key, target["id"], cap))
        up = key != "down"
        load = None
        if probe is not None and v is not None and self.use_probe:
            load = (probe(), probe())
        return {"value": v, "lower": self.lower, "budget": self.budget, "bytes": 1000, "family": self.family,
                "tcp": ({(0, 1): tcp_snap(10 ** 6)}, {(0, 1): tcp_snap(11 * 10 ** 6, retr=60000, rtt=38000)})
                if up and how == "http" else None, "load": load if load is not None else self.load,
                "ca": "bbr" if up and how == "http" else None, "why": None if v is not None else "no_target",
                "target": target["id"], "how": how}

    def http(self, side, target, cap, probe=None):
        return self._try(side, "http", target, cap, probe)

    def iperf(self, side, point, cap):
        assert point.get("addr"), "iperf3 without a checked address"
        return self._try("iperf", "iperf3", point, cap, None)

    def reach(self, point):
        return dict(point, addr="77.88.8.8")


class BenchCase(RootCase):
    TOOLS = ("sysbench", "fio", "openssl", "lscpu", "lsblk", "systemd-detect-virt")

    def setUp(self):
        super().setUp()
        for src, dst in (("cpuinfo-x86.txt", "/proc/cpuinfo"), ("meminfo.txt", "/proc/meminfo"),
                         ("proc-net-route.txt", "/proc/net/route"), ("proc-net-ipv6_route.txt", "/proc/net/ipv6_route"),
                         ("proc-net-dev.txt", "/proc/net/dev"), ("proc-stat.txt", "/proc/stat"),
                         ("os-release-debian12.txt", "/etc/os-release")):
            self.write(dst, fx(src))
        self.write("/sys/class/net/eth0/speed", "1000\n")
        self.write("/etc/machine-id", MID + "\n")
        self.write("/etc/resolv.conf", "# generated\nnameserver 127.0.0.53\noptions edns0\n")
        self.patches = [mock.patch.object(suite, "fs_type", return_value="ext4"),
                        mock.patch("os.statvfs", return_value=StatVfs()),
                        mock.patch.object(suite, "dns_once", return_value=12.5),
                        mock.patch("kaskad.gw.hostping.Prober", side_effect=lambda *a, **k: FakeProber()),
                        mock.patch.object(quiet, "_sleep", lambda s: None),
                        mock.patch.object(suite, "_nproc", return_value=2),
                        # the channel test never goes to the network in the tests (B3)
                        mock.patch.object(channel, "Net", FakeNet),
                        mock.patch.object(channel, "connect_ms", lambda *a, **k: None),
                        mock.patch.object(channel, "connect_addr", lambda *a, **k: (None, None)),
                        mock.patch.object(channel, "_sleep", lambda s: None)]
        for p in self.patches:
            p.start()
        self.fio_files = []

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        super().tearDown()

    def set_state(self, probes=True, kinds=("awg1", "vless")):
        st = state.empty()
        st["gateway"] = {"hostmon": {"probes": probes}, "inbounds": {k: {"enabled": True} for k in kinds}}
        state.save(st)
        return st

    def fio(self, argv, _input):
        path = [a.split("=", 1)[1] for a in argv if a.startswith("--filename=")][0]
        rw = [a.split("=", 1)[1] for a in argv if a.startswith("--rw=")][0]
        with open(path, "a") as f:
            f.write("x")
        self.fio_files.append(path)
        name = {"randread": "randread", "randwrite": "randwrite", "write": "fsync", "read": "read"}[rw]
        return 0, fx("fio-%s-debian12.json" % name), ""

    @staticmethod
    def sysbench_cpu(argv, _input):
        th = int([a for a in argv if a.startswith("--threads=")][0].split("=")[1])
        return 0, fx("sysbench-cpu-debian12.txt" if th == 1 else "sysbench-cpu-mt-ubuntu2404.txt"), ""

    def runner(self, have=None, extra=()):
        have = self.TOOLS if have is None else have
        r = sysrun.FakeRunner(binaries=have)
        for prefix, h in extra:
            r.on(prefix, h)
        r.on(["lscpu"], fx("lscpu-debian12.txt"))
        r.on(["lsblk"], fx("lsblk.txt"))
        r.on(["systemd-detect-virt"], "kvm\n")
        r.on(["sysbench", "cpu"], self.sysbench_cpu)
        r.on(["sysbench", "memory"], fx("sysbench-memory-debian12.txt"))
        r.on(["openssl", "speed", "-aead", "-evp", "chacha20-poly1305"], fx(AEAD % "chacha20-poly1305"))
        r.on(["openssl", "speed", "-aead", "-evp", "aes-128-gcm"], fx(AEAD % "aes-128-gcm"))
        r.on(["openssl", "version"], fx("real/debian12/00-openssl-version.out"))
        r.on(["fio"], self.fio)
        r.on(["systemctl", "show"], fx("systemctl-show-unit.txt"))
        r.on(["systemctl", "list-units"], "")
        r.on(["systemctl", "stop"], "")
        r.on(["systemd-run"], "")
        return r

    @staticmethod
    def by_test(rows):
        return dict((r["test"], r) for r in rows)

    @staticmethod
    def cmd(r, *prefix):
        return r.commands(list(prefix))

    @staticmethod
    def no_cyrillic(argvs):
        return not any(re.search("[А-Яа-яЁё]", a) for argv in argvs for a in argv)

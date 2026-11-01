"""The tests 0–7 of the server check (SPEC-node-score §6.2 B2; RESEARCH-node-score §2 rows 0–7, правка 18.10 П8): the
exact commands, the order (series A first, B last, the slot of the channel B3 between the disk and B), the values and
units of the rows, «не измерено (причина)» for every missing tool or condition, the time budget, the fio file only in
the work directory and removed after."""

import os
import socket
import struct
import sys
import threading
import types
import unittest
from unittest import mock

from kaskad.bench import suite
from tests.bench.benchcase import BenchCase, StatVfs, fx

FS_TYPE = suite.fs_type                 # the real ones (BenchCase replaces them for the other tests)
DNS_ONCE = suite.dns_once


class SuiteCase(BenchCase):
    def ctx(self, mode="quick", have=None, extra=(), left=1000.0, st=None):
        self.work = self.path("/var/lib/kaskad/bench/work-0123456789")
        os.makedirs(self.work, 0o700)
        r = self.runner(have, extra)
        st = st if st is not None else self.set_state()
        self.steps = []
        c = suite.Ctx(r, mode, self.work, suite._mono() + left, st=st,
                      progress=lambda s, i, n: self.steps.append(s) if i == 0 else None)
        return c, r


class FullRunTest(SuiteCase):
    def test_quick_rows_and_commands(self):
        c, r = self.ctx("quick")
        stages = []
        c.progress = lambda s, i, n: (self.steps.append(s), stages.append(c.stage)) if i == 0 else None
        rows = self.by_test(suite.run_all(c))
        self.assertEqual(self.steps, ["inv", "cpu_a", "now", "dns", "cpu_mt", "crypto", "mem", "disk", "net", "cpu_b"])
        self.assertEqual(stages, [(k, 10) for k in range(1, 11)])        # T-3377 Н-8: «этап k из m» only grows
        want = {"mem_gb": (2.062, "GB"), "cpu_1t": (1187.32, "ev/s"), "cpu_mt": (3051.4, "ev/s"),
                "now_loss": (0.0, "%"), "now_rtt": (20.0, "ms"), "dns": (12.5, "ms"), "chacha_1t": (1548.9041, "MB/s"),
                "aes_1t": (2969.5551, "MB/s"), "mem_bw": (10764.95, "MiB/s"), "disk_rr": (21456.3, "IOPS"),
                "disk_rw": (12001.7, "IOPS"), "disk_fsync": (1.2481, "ms"), "cpu_cv": (0.0, "%"),
                "cpu_sustain": (1.0, "x"),
                # the channel (B3, benchcase.FakeNet): 8c = 100 · 60 000 / 10^7 %, 38 − 20 ms
                "net_down": (640.0, "Mbit/s"), "net_up": (320.0, "Mbit/s"), "load_retr": (0.6, "%"),
                "load_rtt": (18.0, "ms")}
        self.assertEqual(sorted(rows), sorted(want))
        for t, (v, unit) in want.items():
            self.assertAlmostEqual(rows[t]["value"], v, places=2, msg=t)
            self.assertEqual((rows[t]["unit"], rows[t]["ok"], rows[t]["why"]), (unit, 1, None), t)
        cpu = self.cmd(r, "sysbench", "cpu")
        one = ["sysbench", "cpu", "--cpu-max-prime=10000", "--threads=1", "--time=2", "run"]
        self.assertEqual(cpu, [one] * 5 + [["sysbench", "cpu", "--cpu-max-prime=10000", "--threads=2", "--time=5",
                                            "run"]] + [one] * 5)
        # the coordinator, 19.10, п. 2: «-aead» for both ciphers; the version of OpenSSL goes to the inventory
        aead = ["openssl", "speed", "-aead", "-evp"]
        self.assertEqual(self.cmd(r, "openssl"),
                         [["openssl", "version"]]
                         + [aead + ["chacha20-poly1305", "-bytes", "1420", "-seconds", "1", "-mr"]] * 3
                         + [aead + ["aes-128-gcm", "-bytes", "1420", "-seconds", "1", "-mr"]] * 3)
        self.assertEqual(c.inv["openssl_ver"], "3.0.14")
        # п. 3: the memory by the time, 5 s (20 GiB went in 0,7 s)
        self.assertEqual(self.cmd(r, "sysbench", "memory"),
                         [["sysbench", "memory", "--memory-block-size=1M", "--memory-total-size=1T", "--threads=1",
                           "--time=5", "run"]])
        fio = self.cmd(r, "fio")
        path = os.path.join(self.work, "fio.tmp")
        base = ["fio", "--name=kbench", "--filename=" + path, "--size=1G", "--direct=1", "--ioengine=libaio",
                "--output-format=json"]
        self.assertEqual(fio, [base + ["--rw=randread", "--bs=4k", "--iodepth=32", "--runtime=10", "--time_based"],
                               base + ["--rw=randwrite", "--bs=4k", "--iodepth=32", "--io_size=1G", "--runtime=10"],
                               [a.replace("--size=1G", "--size=16M") for a in base]
                               + ["--rw=write", "--bs=4k", "--fsync=1", "--runtime=5"]])
        self.assertFalse(os.path.exists(path))                          # the file goes away
        self.assertEqual(set(self.fio_files), {path})                   # and fio never saw another one
        self.assertFalse(r.unmatched)
        self.assertTrue(self.no_cyrillic(r.commands()))

    def test_full_adds_seq_read_and_longer_all_cores(self):
        c, r = self.ctx("full")
        rows = self.by_test(suite.run_all(c))
        self.assertAlmostEqual(rows["disk_seq"]["value"], 512.3 * 1048576 / 1e6, delta=1)
        self.assertEqual(rows["disk_seq"]["unit"], "MB/s")
        self.assertIn(["sysbench", "cpu", "--cpu-max-prime=10000", "--threads=2", "--time=10", "run"], r.commands())
        self.assertEqual(self.cmd(r, "fio")[-1][-4:], ["--rw=read", "--bs=1M", "--runtime=5", "--time_based"])

    def test_inventory(self):
        c, r = self.ctx("quick", st=self.set_state(kinds=("awg1", "awg2", "vless", "socks5")))
        suite.inventory(c)
        inv = c.inv
        self.assertEqual(inv["cpu"], "AMD EPYC 7763 64-Core Processor")
        self.assertEqual(inv["flags"], ["aes", "avx2", "vaes"])
        self.assertEqual((inv["nproc"], inv["smt"], inv["mem_gb"], inv["virt"]), (2, 1, 2.062, "kvm"))
        self.assertEqual(inv["disks"], [{"rota": True, "size": 21474836480}, {"rota": False, "size": 107374182400}])
        self.assertEqual(inv["nic"], {"eth0": 1000})
        self.assertEqual(inv["os"], {"id": "debian", "version_id": "12"})
        self.assertEqual(inv["engines"], ["awg", "socks5", "vless"])
        self.assertNotIn("mid", inv)
        self.assertIsNone(inv["crash_mb"])                               # no kdump file — unknown
        for raw, want in (("268435456\n", 256), ("0\n", 0), ("junk\n", None), ("-5\n", None)):
            self.write("/sys/kernel/kexec_crash_size", raw)             # C2: the memory kdump keeps (RS 3.4)
            suite.inventory(c)
            self.assertEqual(c.inv["crash_mb"], want, raw)
        self.write("/sys/kernel/kexec_crash_size", "536870912\n")
        for virt, want in (("kvm", 512), ("none", 512), ("lxc", None), ("openvz", None), ("systemd-nspawn", None),
                           ("docker", None), ("", None)):
            r3 = self.runner()                                           # REV-ns-c2 З-4: the reserve of a VM only
            r3._handlers.insert(0, (["systemd-detect-virt"], virt + "\n"))
            c.runner = r3
            suite.inventory(c)
            self.assertEqual(c.inv["crash_mb"], want, virt)
        c.runner = r
        r2 = self.runner()
        r2._handlers.insert(0, (["systemd-detect-virt"], "kvm; rm -rf /\n"))
        c.runner = r2
        suite.inventory(c)
        self.assertIsNone(c.inv["virt"])                                 # an odd answer is not shown
        self.assertEqual(c.inv["openssl_ver"], "3.0.14")
        r2._handlers.insert(0, (["openssl", "version"], "LibreSSL 3.8.2\n"))
        suite.inventory(c)
        self.assertIsNone(c.inv["openssl_ver"])                          # not understood — none
        r = self.runner(have=("lscpu", "lsblk", "systemd-detect-virt"))
        c = suite.Ctx(r, "quick", self.work, suite._mono() + 100, st=c.st)
        suite.inventory(c)
        self.assertIsNone(c.inv["openssl_ver"])                          # no openssl: not asked
        self.assertFalse(self.cmd(r, "openssl"))
        with mock.patch.object(suite, "_fit_inv", wraps=suite._fit_inv) as fit:
            suite.inventory(c)
        fit.assert_called_once_with(c.inv)                               # REV-ns-b3-2 З-2 (M17): within 4 KB


class MissingTest(SuiteCase):
    def test_without_sysbench_and_fio(self):
        c, r = self.ctx(have=("openssl", "lscpu", "lsblk", "systemd-detect-virt"))
        rows = self.by_test(suite.run_all(c))
        for t in ("cpu_1t", "cpu_mt", "mem_bw", "disk_rr", "disk_rw", "disk_fsync", "cpu_cv", "cpu_sustain"):
            self.assertEqual((rows[t]["ok"], rows[t]["value"], rows[t]["why"]), (0, None, "no_pkgs"), t)
        self.assertEqual(rows["chacha_1t"]["ok"], 1)
        self.assertEqual(rows["dns"]["ok"], 1)
        self.assertFalse(self.cmd(r, "sysbench") or self.cmd(r, "fio"))

    def test_without_openssl(self):
        c, r = self.ctx(have=("sysbench", "fio"))
        rows = self.by_test(suite.run_all(c))
        self.assertEqual((rows["chacha_1t"]["why"], rows["aes_1t"]["why"]), ("no_openssl", "no_openssl"))
        self.assertFalse(self.cmd(r, "openssl"))

    def test_tools_that_fail_or_print_nonsense(self):
        c, r = self.ctx(extra=((["sysbench", "memory"], (1, "", "FATAL")),
                               (["openssl", "speed", "-aead", "-evp", "aes-128-gcm"], fx("openssl-err.txt")),
                               (["fio"], (0, fx("fio-error-direct.json"), ""))))
        rows = self.by_test(suite.run_all(c))
        self.assertEqual(rows["mem_bw"]["why"], "tool_failed")
        self.assertEqual(rows["aes_1t"]["why"], "tool_failed")
        self.assertEqual(rows["chacha_1t"]["ok"], 1)
        for t in ("disk_rr", "disk_rw", "disk_fsync"):
            self.assertEqual(rows[t]["why"], "tool_failed", t)
        self.assertEqual(rows["cpu_sustain"]["ok"], 1)                   # the check went on

    def test_fio_that_failed_is_not_a_number(self):
        # a good JSON with a non-zero exit code of fio is no result (an error of a job, a timeout in the middle)
        c, r = self.ctx(extra=((["fio"], (1, fx("fio-randread-debian12.json"), "fio: io_u error")),))
        suite.disk(c)
        self.assertEqual([x["why"] for x in c.rows], ["tool_failed"] * 3)

    def test_a_step_that_raises(self):
        # REV-ns-b1 З-2: a step that raises — its tests not written yet «не измерено», the next steps go
        def half(ctx):
            ctx.row("now_loss", 1.0)
            raise ValueError("bad answer")
        steps = tuple(("now", half, n, t) if name == "now" else (name, fn, n, t) for name, fn, n, t in suite.BASE)
        c, r = self.ctx()
        with mock.patch.object(suite, "BASE", steps):
            rows = self.by_test(suite.run_all(c))
        self.assertEqual((rows["now_loss"]["ok"], rows["now_rtt"]["why"]), (1, "tool_failed"))
        self.assertEqual(rows["dns"]["ok"], 1)
        self.assertEqual(rows["cpu_sustain"]["ok"], 1)
        self.assertEqual(len(c.rows), 18)
        # the stop goes up whatever step it hits
        def stop(ctx):
            raise suite.Cancelled()
        steps = tuple(("dns", stop, n, t) if name == "dns" else (name, fn, n, t) for name, fn, n, t in suite.BASE)
        c.rows = []
        with mock.patch.object(suite, "BASE", steps):
            with self.assertRaises(suite.Cancelled):
                suite.run_all(c)

    def test_two_of_three_openssl_runs(self):
        n = {"i": 0}

        def flaky(argv, _input):
            n["i"] += 1
            return (1, "", "x") if n["i"] == 2 else (0, "+H:1420\n+F:22:x:%d000000.0\n" % (n["i"] * 100), "")
        c, r = self.ctx(extra=((["openssl", "speed", "-aead", "-evp", "chacha20-poly1305"], flaky),))
        suite.crypto(c)
        self.assertEqual(self.by_test(c.rows)["chacha_1t"]["value"], 200.0)     # median of 100 and 300

    def test_partial_cpu_series(self):
        n = {"i": 0}

        def flaky(argv, _input):
            n["i"] += 1
            return (0, fx("sysbench-cpu-debian12.txt"), "") if n["i"] <= 2 else (1, "", "")
        c, r = self.ctx(extra=((["sysbench", "cpu", "--cpu-max-prime=10000", "--threads=1"], flaky),))
        suite.cpu_a(c)
        suite.cpu_b(c)
        rows = self.by_test(c.rows)
        self.assertEqual(rows["cpu_1t"]["value"], 1187.32)
        self.assertEqual((rows["cpu_cv"]["why"], rows["cpu_sustain"]["why"]), ("partial_series", "partial_series"))


class DiskTest(SuiteCase):
    def check(self, why, **kw):
        c, r = self.ctx()
        suite.disk(c)
        self.assertEqual([x["why"] for x in c.rows], [why] * 3)
        self.assertFalse(self.cmd(r, "fio"))

    def test_space(self):
        with mock.patch("os.statvfs", return_value=StatVfs(free_gib=2.9)):
            self.check("disk_space")

    def test_full(self):
        with mock.patch("os.statvfs", return_value=StatVfs(free_gib=10, used_pct=86)):
            self.check("disk_full")

    def test_bad_fs(self):
        for typ in ("tmpfs", "overlay", "zfs", None):
            with mock.patch.object(suite, "fs_type", return_value=typ):
                self.check("disk_fs")
            import shutil
            shutil.rmtree(self.work)

    def test_fs_type_by_mountinfo(self):
        mi = fx("mountinfo.txt")
        self.assertEqual(FS_TYPE("/var/lib/kaskad/bench", mi), "ext4")
        self.assertEqual(FS_TYPE("/run/kaskad", mi), "tmpfs")
        self.assertEqual(FS_TYPE("/var/lib/docker/x", mi), "overlay")
        self.assertEqual(FS_TYPE("/var/lib/dockerx", mi), "ext4")
        self.assertIsNone(FS_TYPE("/x", ""))

    def test_file_removed_when_stopped_in_the_middle(self):
        def stop(argv, _input):
            self.fio(argv, _input)
            raise suite.Cancelled()
        c, r = self.ctx(extra=((["fio", "--name=kbench"], stop),))
        with self.assertRaises(suite.Cancelled):
            suite.disk(c)
        self.assertTrue(self.fio_files)
        self.assertFalse(os.path.exists(self.fio_files[0]))


class NowAndDnsTest(SuiteCase):
    def test_probes_off(self):
        c, r = self.ctx(st=self.set_state(probes=False))
        suite.now_series(c)
        self.assertEqual([x["why"] for x in c.rows], ["probes_off", "probes_off"])

    def test_best_two_of_three(self):
        c, r = self.ctx()
        with mock.patch("kaskad.gw.hostping.Prober", side_effect=lambda *a, **k: __import__(
                "tests.bench.benchcase", fromlist=["FakeProber"]).FakeProber(lost=(0, 1, 2))):
            suite.now_series(c)
        rows = self.by_test(c.rows)
        # per anchor: 0 %, 33.3 %, 66.7 % -> the worst dropped -> 16.7 %
        self.assertAlmostEqual(rows["now_loss"]["value"], 100.0 / 6, places=2)
        self.assertEqual(c.series["anchors"][0], {"addr": "1.1.1.1", "n": 21, "lost": 0, "rtt_med": 10.0, "na": None})

    def test_the_series_does_not_wait_for_our_own_lock(self):
        self.assertFalse(suite._net().speed_busy(5.0))

    def test_dns(self):
        c, r = self.ctx()
        with mock.patch.object(suite, "dns_once", side_effect=[10.0, 30.0, None, 20.0, None, None, 40.0, 50.0, 60.0,
                                                                70.0]):
            suite.dns(c)
        self.assertEqual(c.rows[-1]["value"], 55.0)          # 3 unanswered count as 2000 ms in the median
        c.rows = []
        with mock.patch.object(suite, "dns_once", side_effect=[None] * 6 + [1.0] * 4):
            suite.dns(c)
        self.assertEqual(c.rows[-1]["why"], "dns_failed")
        self.write("/etc/resolv.conf", "nameserver not-an-ip\n")
        suite.dns(c)
        self.assertEqual(c.rows[-1]["why"], "no_resolver")

    def test_dns_of_the_gateway(self):
        # З-8: the gateway on — its dnsmasq (what the devices use), asked at its own port; the row says «kaskad»
        st = self.set_state()
        st["gateway"]["enabled"] = True
        c, r = self.ctx(st=st)
        up = mock.patch.object(suite, "addr_up", side_effect=lambda ip: ip == "10.77.0.1")   # kgw0 carries it
        up.start()
        self.addCleanup(up.stop)
        with mock.patch.object(suite, "dns_once", return_value=7.0) as q:
            suite.dns(c)
        self.assertEqual({(a[0], k["port"]) for a, k in q.call_args_list}, {("10.77.0.1", 5354)})
        self.assertEqual((c.rows[-1]["value"], c.rows[-1]["dns_src"]), (7.0, "kaskad"))
        self.assertNotIn("src", c.rows[-1])             # REV-ns-b1-2 З-2: the key of the contract with B12 is dns_src
        # the gateway's DNS silent: «почти не отвечает», still of «kaskad» (the resolv.conf is never asked instead)
        with mock.patch.object(suite, "dns_once", return_value=None) as q:
            suite.dns(c)
        self.assertEqual((c.rows[-1]["why"], c.rows[-1]["dns_src"]), ("dns_failed", "kaskad"))
        self.assertEqual({a[0] for a, k in q.call_args_list}, {"10.77.0.1"})
        # off — the system resolver of resolv.conf, port 53
        c.st = self.set_state()
        with mock.patch.object(suite, "dns_once", return_value=7.0) as q:
            suite.dns(c)
        self.assertEqual({(a[0], k["port"]) for a, k in q.call_args_list}, {("127.0.0.53", 53)})
        self.assertEqual(c.rows[-1]["dns_src"], "system")

    def test_dns_of_a_gateway_nobody_uses(self):
        # K-it1-calib п. 1: the gateway on, but its DNS serves nobody — no inbound (or none enabled), or kgw0 not up:
        # the system resolver, not «dns_failed» of «kaskad» in every check (before: 10.77.0.1:5354 whatever the node)
        self.write("/etc/resolv.conf", "nameserver 1.1.1.1\n")
        on = self.set_state(kinds=())
        on["gateway"]["enabled"] = True
        self.assertEqual(suite.resolver(on), ("1.1.1.1", 53, "system"))               # no inbound (before: kaskad)
        with mock.patch.object(suite, "addr_up", return_value=True):
            self.assertEqual(suite.resolver(on), ("1.1.1.1", 53, "system"))           # … even with kgw0 up
            off = self.set_state(kinds=("awg1",))
            off["gateway"]["enabled"] = True
            off["gateway"]["inbounds"]["awg1"]["enabled"] = False
            self.assertEqual(suite.resolver(off), ("1.1.1.1", 53, "system"))          # the only inbound off
        st = self.set_state(kinds=("awg1",))
        st["gateway"]["enabled"] = True
        with mock.patch.object(suite, "addr_up", return_value=False) as up:
            self.assertEqual(suite.resolver(st), ("1.1.1.1", 53, "system"))           # kgw0 down
        up.assert_called_once_with("10.77.0.1")
        with mock.patch.object(suite, "addr_up", return_value=True):
            self.assertEqual(suite.resolver(st), ("10.77.0.1", 5354, "kaskad"))       # people on it: as before
        # the whole test: the row says «system» and asks resolv.conf, never the gateway
        c, r = self.ctx(st=on)
        with mock.patch.object(suite, "addr_up", return_value=True), \
                mock.patch.object(suite, "dns_once", return_value=9.0) as q:
            suite.dns(c)
        self.assertEqual({(a[0], k["port"]) for a, k in q.call_args_list}, {("1.1.1.1", 53)})
        self.assertEqual((c.rows[-1]["value"], c.rows[-1]["dns_src"]), (9.0, "system"))

    def test_addr_up(self):
        self.assertTrue(suite.addr_up("127.0.0.1"))
        self.assertFalse(suite.addr_up("192.0.2.77"))       # TEST-NET-1: never an address of this host
        self.assertFalse(suite.addr_up("not-an-ip"))

    def test_resolver_and_query(self):
        # link-local needs its zone (lost by a plain address): the next nameserver goes
        self.write("/etc/resolv.conf", "search example.com\nnameserver fe80::1%eth0\nnameserver 169.254.169.254\n"
                                       "nameserver 1.1.1.1\n")
        self.assertEqual(suite.resolver(), ("1.1.1.1", 53, "system"))
        self.write("/etc/resolv.conf", "nameserver fe80::1%eth0\n")
        self.assertIsNone(suite.resolver())
        self.write("/etc/resolv.conf", "nameserver 2001:db8::53\n")
        self.assertEqual(suite.resolver({}), ("2001:db8::53", 53, "system"))
        q = suite.dns_query(0x1234, "abc.example.com")
        self.assertEqual(q[:4], b"\x12\x34\x01\x00")
        self.assertTrue(q.endswith(b"\x03abc\x07example\x03com\x00\x00\x01\x00\x01"))


class FakeDns(object):
    """A DNS server on 127.0.0.1 (a free port) that answers every query with ``rcode`` (the id and the question of the
    query, the bit QR) — ``noise`` first: a reply with another id, which is not the answer."""

    def __init__(self, rcode, noise=False):
        self.rcode, self.noise = rcode, noise
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind(("127.0.0.1", 0))
        self.port = self.s.getsockname()[1]
        self.t = threading.Thread(target=self.serve, daemon=True)
        self.t.start()

    def serve(self):
        while True:
            try:
                q, peer = self.s.recvfrom(512)
            except OSError:
                return
            qid = struct.unpack("!H", q[:2])[0]
            if self.noise:
                self.s.sendto(struct.pack("!HHHHHH", qid ^ 1, 0x8180, 1, 0, 0, 0) + q[12:], peer)
            self.s.sendto(struct.pack("!HHHHHH", qid, 0x8180 | self.rcode, 1, 0, 0, 0) + q[12:], peer)

    def close(self):
        self.s.close()


class DnsAnswerTest(SuiteCase):
    """REV-ns-b1-2 З-1: an answer is NOERROR or NXDOMAIN only. A dnsmasq of the gateway without upstream servers
    («fails closed», gw/dns.py) refuses at once — counted as an answer it gave the best time where people have no
    DNS. REFUSED, SERVFAIL, NOTIMP, FORMERR — the query failed."""

    def ask(self, rcode, noise=False):
        srv = FakeDns(rcode, noise)
        try:
            return DNS_ONCE("127.0.0.1", "abc.example.com", timeout=1.0, port=srv.port)
        finally:
            srv.close()

    def test_rcodes(self):
        for rc in (0, 3):                                   # NOERROR, NXDOMAIN
            ms = self.ask(rc)
            self.assertIsNotNone(ms, rc)
            self.assertLess(ms, 1000.0, rc)
        for rc in (1, 2, 4, 5):                             # FORMERR, SERVFAIL, NOTIMP, REFUSED
            self.assertIsNone(self.ask(rc), rc)
        self.assertIsNotNone(self.ask(3, noise=True))       # a reply with another id is skipped, ours counts

    def test_refusing_gateway_is_dns_failed(self):
        srv = FakeDns(5)
        try:
            c, r = self.ctx()
            with mock.patch.object(suite, "resolver", return_value=("127.0.0.1", srv.port, "kaskad")), \
                    mock.patch.object(suite, "dns_once", side_effect=lambda a, n, port: DNS_ONCE(a, n, 0.5, port)):
                suite.dns(c)
        finally:
            srv.close()
        self.assertEqual((c.rows[-1]["ok"], c.rows[-1]["why"], c.rows[-1]["dns_src"]), (0, "dns_failed", "kaskad"))


class OrderAndTimeTest(SuiteCase):
    def test_time_left(self):
        c, r = self.ctx(left=60.0)
        rows = self.by_test(suite.run_all(c))
        self.assertEqual(rows["mem_gb"]["ok"], 1)
        self.assertEqual(rows["disk_rr"]["why"], "time")             # 105 s of the disk do not fit 60
        self.assertEqual(rows["cpu_cv"]["ok"], 1)                     # the series B (25 s) still does
        self.assertFalse(self.cmd(r, "fio"))

    def test_channel_slot(self):
        mod = types.ModuleType("kaskad.bench.channel")
        seen = []

        def net(ctx):
            seen.append(list(self.steps))
            ctx.row("net_down", 640.0)
        mod.steps = lambda mode, skip=(), iperf=False: (("net", net, lambda m: 30, lambda m: ("net_down",)),)
        with mock.patch.dict(sys.modules, {"kaskad.bench.channel": mod}):
            import kaskad.bench as pkg
            with mock.patch.object(pkg, "channel", mod, create=True):
                c, r = self.ctx()
                suite.run_all(c)
        self.assertEqual(seen[0][-2:], ["disk", "net"])
        self.assertEqual(self.steps[-1], "cpu_b")
        self.assertIn("net_down", self.by_test(c.rows))
        self.assertEqual([s[0] for s in suite._channel("quick")], ["net"])     # the module of B3


if __name__ == "__main__":
    unittest.main()


class IperfTcpTest(unittest.TestCase):
    """D-144: 8c over 8b — ``suite.iperf_tcp(pid, port)``, the sampler of ``bench.channel``: the sock_diag dump of
    ``gw.hostpeople`` filtered by the REMOTE port, only the sockets of the process of iperf3 (its fds), the counters
    of ``tcp_info``; an old kernel that refuses the filter — once without it; no sock_diag or no fds — None."""

    def run_it(self, socks, inodes=(11, 12, 13, 14, 15), refuse=False, fail=None):
        from kaskad.gw import hostpeople as HP
        from tests.gw import test_hostpeople as T
        reqs = []

        def dump(req, deadline, budget):
            reqs.append(req)
            if fail is not None:
                raise fail
            fam, seq = req[16], struct.unpack_from("=I", req, 8)[0]
            if refuse and len(req) > 16 + 56:
                yield T.error(seq, 22)
                return
            yield b"".join(T.diag(sport, ck, ino, T.tcpinfo(sent=sent, retr=retr), fam=fam, seq=seq)
                           for (f, sport, ck, ino, sent, retr) in socks if f == fam) + T.done(seq)

        def fds(pid, into, deadline=None):
            self.assertEqual(pid, 4242)
            into.update(inodes)
            return True
        with mock.patch.object(HP, "netlink_dump", dump), mock.patch.object(HP, "sock_inodes", fds):
            return suite.iperf_tcp(4242, 5201), reqs

    def test_only_the_sockets_of_iperf3_to_the_port(self):
        socks = [(2, 40001, 1, 11, 10 ** 6, 0), (2, 40002, 2, 12, 2 * 10 ** 6, 1448), (10, 40003, 3, 13, 5, 0),
                 (2, 40004, 4, 99, 7 * 10 ** 6, 0)]                    # inode 99 — another process to the same port
        got, reqs = self.run_it(socks)
        self.assertEqual(sorted(got), [1, 2, 3])                           # без правки: AttributeError
        self.assertEqual(got[2], {"snd_mss": 1448, "rtt": 40000, "min_rtt": 30000, "bytes_sent": 2 * 10 ** 6,
                                  "bytes_retrans": 1448, "dsack_dups": 0})
        self.assertEqual([r[16] for r in reqs], [2, 10])                   # both families, filtered by the remote port
        code = reqs[0][16 + 56 + 4:]
        self.assertEqual(struct.unpack_from("=BBH", code, 0)[0], 4)        # D_GE
        self.assertEqual(struct.unpack_from("=BBH", code, 4)[2], 5201)

    def test_an_old_kernel_and_nothing_to_read(self):
        got, reqs = self.run_it([(2, 40001, 1, 11, 10 ** 6, 0)], refuse=True)
        self.assertEqual(sorted(got), [1])                                  # EINVAL — once more without the filter
        self.assertEqual([len(r) for r in reqs], [16 + 56 + 20, 16 + 56, 16 + 56 + 20, 16 + 56])
        self.assertIsNone(self.run_it([], fail=OSError(93, "no sock_diag"))[0])
        self.assertIsNone(self.run_it([(2, 40001, 1, 11, 10 ** 6, 0)], inodes=())[0])   # the fds not readable
        self.assertEqual(self.run_it([])[0], {})                            # read, no such connection

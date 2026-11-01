"""The readers of the tools of the server check (SPEC-node-score §6.2 B2): the outputs of sysbench 1.0.20, fio 3.28 /
3.33 / 3.36 (JSON), ``openssl speed -mr`` (3.0.x, 1.1.1, 3.6), lscpu, /proc/cpuinfo, lsblk, os-release.

The samples in tests/fixtures/bench are written by the formats of these versions (the sources of the tools); the stand
of B9 replaces them by captures of the real servers (Debian 12, Ubuntu 22.04 / 24.04) — the test names stay."""

import os
import unittest

from kaskad.bench import parse

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "bench")


def fx(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class SysbenchTest(unittest.TestCase):
    def test_cpu_of_every_system(self):
        for name, want in (("debian12", 1187.32), ("ubuntu2204", 942.07), ("ubuntu2404", 1534.88)):
            self.assertEqual(parse.sysbench_cpu(fx("sysbench-cpu-%s.txt" % name)), want, name)
        self.assertEqual(parse.sysbench_cpu(fx("sysbench-cpu-mt-ubuntu2404.txt")), 3051.40)

    def test_cpu_not_understood(self):
        for text in (fx("sysbench-cpu-bad.txt"), "", None, "events per second: x", "    events per second:  0.00\n"):
            self.assertIsNone(parse.sysbench_cpu(text), repr(text))

    def test_memory(self):
        for name, want in (("debian12", 10764.95), ("ubuntu2204", 8123.40), ("ubuntu2404", 14987.12)):
            self.assertEqual(parse.sysbench_memory(fx("sysbench-memory-%s.txt" % name)), want, name)
        self.assertIsNone(parse.sysbench_memory(fx("sysbench-cpu-debian12.txt")))
        self.assertIsNone(parse.sysbench_memory("0.00 MiB transferred (0.00 MiB/sec)"))


class OpensslTest(unittest.TestCase):
    def test_versions(self):
        for name, want in (("openssl-chacha-debian12.txt", 1123580412.39), ("openssl-aes-debian12.txt", 3489125003.52),
                           ("openssl-chacha-ubuntu2204.txt", 874512303.10), ("openssl-aes-ubuntu2404.txt", 4012339876.0),
                           ("openssl-chacha-111.txt", 1023400000.0), ("openssl-mac36.txt", 2071522980.0)):
            self.assertAlmostEqual(parse.openssl_speed(fx(name), 1420), want, places=2, msg=name)

    def test_errors_and_other_blocks(self):
        self.assertIsNone(parse.openssl_speed(fx("openssl-err.txt")))
        self.assertIsNone(parse.openssl_speed(""))
        self.assertIsNone(parse.openssl_speed("+H:16:64\n+F:22:x:1.0:2.0\n", 1420))      # not our block
        self.assertEqual(parse.openssl_speed("+H:16:1420\n+F:22:x:1.0:2.5\n", 1420), 2.5)
        self.assertIsNone(parse.openssl_speed("+H:1420\n+F:22:x:nan\n"))
        self.assertIsNone(parse.openssl_speed("+H:1420\n+F:22:x:-5\n"))
        # the stderr lines (+DT, +R) mixed in: still the +F
        self.assertEqual(parse.openssl_speed("+DT:x:1:1420\n+R:5:x:1.0\n+H:1420\n+F:1:x:7.0\n"), 7.0)

    def test_version(self):
        # the version goes to the inventory (``openssl_ver``): its change is a new base of the ciphers
        for text, want in (("OpenSSL 3.0.14 4 Jun 2024 (Library: OpenSSL 3.0.14 4 Jun 2024)\n", "3.0.14"),
                           ("OpenSSL 3.5.5 27 Jan 2026 (Library: OpenSSL 3.5.5 27 Jan 2026)", "3.5.5"),
                           ("OpenSSL 3.0.2 15 Mar 2022 (Library: OpenSSL 3.0.13 30 Jan 2024)", "3.0.13"),   # the library
                           ("OpenSSL 1.1.1w  11 Sep 2023\n", "1.1.1w"),
                           ("LibreSSL 3.8.2\n", None), ("", None), (None, None), ("OpenSSL 3.0.14x2", None),
                           ("OpenSSL 3.0\n", None), ("OpenSSL 3.0.14; rm -rf /", "3.0.14")):
            self.assertEqual(parse.openssl_version(text), want, repr(text))


class FioTest(unittest.TestCase):
    def test_four_jobs_of_every_version(self):
        want = {"debian12": (21456.3, 12001.7, 0.610304, 512.3), "ubuntu2204": (9876.5, 5432.1, 1.253376, 301.7),
                "ubuntu2404": (45012.8, 30111.2, 0.288768, 1802.0)}
        for name, (rr, rw, fs, seq) in want.items():
            self.assertEqual(parse.fio_iops(parse.fio_json(fx("fio-randread-%s.json" % name)), "read"), rr)
            self.assertEqual(parse.fio_iops(parse.fio_json(fx("fio-randwrite-%s.json" % name)), "write"), rw)
            self.assertAlmostEqual(parse.fio_clat_median_ms(parse.fio_json(fx("fio-fsync-%s.json" % name)), "write"),
                                   fs, places=6)
            mbs = parse.fio_bw_mbs(parse.fio_json(fx("fio-read-%s.json" % name)), "read")
            self.assertAlmostEqual(mbs, seq * 1048576 / 1e6, delta=1.0, msg=name)

    def test_fsync_is_the_write_with_its_fsync(self):
        # the coordinator, 19.10, п. 1: 1000 / IOPS of the fsync job, not the median clat (no fsync in it)
        self.assertAlmostEqual(parse.fio_fsync_ms(parse.fio_json(fx("fio-fsync-debian12.json"))), 1000.0 / 801.2,
                               places=9)
        for name in ("debian12", "ubuntu2204", "ubuntu2404"):
            doc = parse.fio_json(fx("fio-fsync-%s.json" % name))
            iops = parse.fio_iops(doc, "write")
            self.assertAlmostEqual(parse.fio_fsync_ms(doc), 1000.0 / iops, places=9, msg=name)
        self.assertIsNone(parse.fio_fsync_ms(parse.fio_json(fx("fio-error-direct.json"))))
        self.assertIsNone(parse.fio_fsync_ms(parse.fio_json(fx("fio-randread-debian12.json"))))   # no writes
        self.assertIsNone(parse.fio_fsync_ms(None))

    def test_lines_before_the_document(self):
        text = fx("fio-randread-debian12.json")
        self.assertTrue(text.startswith("kbench: Laying out IO file"))
        self.assertIsNotNone(parse.fio_json(text))

    def test_error_and_wrong_side(self):
        doc = parse.fio_json(fx("fio-error-direct.json"))
        self.assertIsNotNone(doc)
        self.assertIsNone(parse.fio_iops(doc, "read"))
        rr = parse.fio_json(fx("fio-randread-debian12.json"))
        self.assertIsNone(parse.fio_iops(rr, "write"))                   # no I/O on that side
        self.assertIsNone(parse.fio_clat_median_ms(rr, "write"))
        for text in ("", "not json", '{"jobs": "x"}', "{broken", None):
            self.assertIsNone(parse.fio_json(text))
        self.assertIsNone(parse.fio_iops({"jobs": []}, "read"))
        self.assertIsNone(parse.fio_bw_mbs(None, "read"))


class InventoryTest(unittest.TestCase):
    def test_cpuinfo(self):
        x = parse.cpuinfo(fx("cpuinfo-x86.txt"))
        self.assertEqual(x, {"model": "AMD EPYC 7763 64-Core Processor", "flags": ["aes", "avx2", "vaes"]})
        self.assertEqual(parse.cpuinfo(fx("cpuinfo-x86-noaes.txt"))["flags"], [])
        arm = parse.cpuinfo(fx("cpuinfo-arm.txt"))
        self.assertEqual(arm, {"model": None, "flags": ["aes"]})
        self.assertEqual(parse.cpuinfo("model name\t: A\x1b[31mB\x07 " + "x" * 200)["model"], ("A [31mB " + "x" * 200)[:80])

    def test_lscpu(self):
        self.assertEqual(parse.lscpu(fx("lscpu-debian12.txt")),
                         {"model": "AMD EPYC 7763 64-Core Processor", "threads_per_core": 1, "sockets": 1})
        arm = parse.lscpu(fx("lscpu-ubuntu2204-arm.txt"))
        self.assertEqual((arm["model"], arm["threads_per_core"], arm["sockets"]), ("Neoverse-N1", 1, None))

    def test_meminfo_lsblk_os(self):
        self.assertEqual(parse.meminfo_kb(fx("meminfo.txt")), 2014048)
        self.assertEqual(parse.meminfo_kb(fx("meminfo.txt"), "MemAvailable"), 1402312)
        self.assertIsNone(parse.meminfo_kb("x"))
        self.assertEqual(parse.lsblk(fx("lsblk.txt")),
                         [{"name": "vda", "rota": True, "size": 21474836480, "type": "disk"},
                          {"name": "nvme0n1", "rota": False, "size": 107374182400, "type": "disk"}])
        self.assertEqual(parse.os_release(fx("os-release-debian12.txt")),
                         {"id": "debian", "version_id": "12", "pretty": "Debian GNU/Linux 12 (bookworm)"})
        self.assertEqual(parse.os_release(fx("os-release-ubuntu2404.txt"))["version_id"], "24.04")

    def test_statistics(self):
        self.assertEqual(parse.median([3, 1, 2]), 2)
        self.assertIsNone(parse.median([None]))
        self.assertIsNone(parse.cv_pct([5]))
        self.assertAlmostEqual(parse.cv_pct([90, 110]), 14.142, places=2)



class RealOutputsTest(unittest.TestCase):
    """The captures of real servers (Aeza claude-test, 19.10: Debian 12 — sysbench 1.0.20, fio 3.33, OpenSSL 3.0.14,
    systemd 252; Ubuntu 22.04 — fio 3.28, OpenSSL 3.0.2, systemd 249; Ubuntu 24.04 — fio 3.36, OpenSSL 3.0.13;
    Debian 13 — fio 3.39, OpenSSL 3.5.5; k3/handoff/data/node-score-samples/raw-2026-10-19): every reader gives the
    number the tool printed. ``chacha``/``aes`` — without «-aead» (the old command), ``*_aead`` — what the check runs
    now; ``fsync`` — 1000 / IOPS of the fsync job, ``clat`` — the median clat that left the fsync out."""

    WANT = {
        "debian12": {"cpu_1t": 5129.9, "cpu_mt": 5100.81, "mem_bw": 29777.59, "chacha": 2110.6, "aes": 4341.7,
                     "chacha_aead": 1548.9, "aes_aead": 2969.6, "ver": "3.0.14",
                     "rr": 10099.37, "rw": 10070.37, "fsync": 0.2104, "clat": 0.0509, "seq": 3103.37},
        "ubuntu2204": {"cpu_1t": 4805.8, "cpu_mt": 5020.7, "mem_bw": 28554.11, "chacha": 1988.9, "aes": 4265.3,
                       "chacha_aead": 1446.9, "aes_aead": 2785.5, "ver": "3.0.2",
                       "rr": 10098.46, "rw": 10097.46, "fsync": 0.3950, "clat": 0.0681, "seq": 2417.74},
        "ubuntu2404": {"cpu_1t": 4968.98, "cpu_mt": 4840.11, "mem_bw": 27730.59, "chacha": 2060.6, "aes": 4127.5,
                       "chacha_aead": 1447.6, "aes_aead": 2873.2, "ver": "3.0.13",
                       "rr": 10099.17, "rw": 10064.78, "fsync": 0.6047, "clat": 0.0712, "seq": 2103.65},
        "debian13": {"cpu_1t": 5082.84, "cpu_mt": 5127.74, "mem_bw": 28390.86, "chacha": 2077.6, "aes": 2940.0,
                     "chacha_aead": 2105.5, "aes_aead": 2898.1, "ver": "3.5.5",
                     "rr": 10098.56, "rw": 10098.48, "fsync": 0.2742, "clat": 0.0499, "seq": 2521.74},
    }

    @staticmethod
    def rd(osn, name):
        return fx(os.path.join("real", osn, name + ".out"))

    def test_the_numbers_of_the_tools(self):
        for osn, w in self.WANT.items():
            rd = lambda n: self.rd(osn, n)                          # noqa: E731
            self.assertEqual(parse.sysbench_cpu(rd("30-sysbench-cpu-1t-2s-run1")), w["cpu_1t"], osn)
            self.assertEqual(parse.sysbench_cpu(rd("31-sysbench-cpu-mt-5s")), w["cpu_mt"], osn)
            self.assertEqual(parse.sysbench_memory(rd("50-sysbench-memory")), w["mem_bw"], osn)
            # «-evp chacha20-poly1305» without «-aead» works in OpenSSL 3.0.2 and 3.0.14
            self.assertAlmostEqual(parse.openssl_speed(rd("40-openssl-chacha20-poly1305-mr-run1")) / 1e6, w["chacha"],
                                   places=1, msg=osn)
            self.assertAlmostEqual(parse.openssl_speed(rd("40-openssl-aes-128-gcm-mr-run1")) / 1e6, w["aes"],
                                   places=1, msg=osn)
            for alg, k in (("chacha20-poly1305", "chacha_aead"), ("aes-128-gcm", "aes_aead")):
                self.assertAlmostEqual(parse.openssl_speed(rd("42-openssl-%s-aead-mr" % alg)) / 1e6, w[k], places=1,
                                       msg=osn)
            self.assertEqual(parse.openssl_version(rd("00-openssl-version")), w["ver"], osn)
            self.assertAlmostEqual(parse.fio_iops(parse.fio_json(rd("61-fio-randread")), "read"), w["rr"], places=2)
            self.assertAlmostEqual(parse.fio_iops(parse.fio_json(rd("62-fio-randwrite")), "write"), w["rw"], places=2)
            # the coordinator, 19.10, п. 1: the write with its fsync = 1000 / IOPS (0,21 / 0,40 / 0,60 / 0,27 ms);
            # the median clat left the fsync out and was 3–9 times less
            fs = parse.fio_json(rd("63-fio-fsync"))
            self.assertAlmostEqual(parse.fio_fsync_ms(fs), w["fsync"], places=4, msg=osn)
            self.assertAlmostEqual(parse.fio_clat_median_ms(fs, "write"), w["clat"], places=4, msg=osn)
            self.assertGreater(parse.fio_fsync_ms(fs) / parse.fio_clat_median_ms(fs, "write"), 3, osn)
            self.assertAlmostEqual(parse.fio_bw_mbs(parse.fio_json(rd("64-fio-read")), "read"), w["seq"], places=2)
            self.assertEqual(parse.lscpu(rd("21-lscpu"))["threads_per_core"], 1)
            self.assertEqual(parse.lsblk(rd("23-lsblk")), [{"name": "vda", "rota": True, "size": 32212254720,
                                                           "type": "disk"}])     # virtio says ROTA=1: the inventory only

    def test_systemctl_and_apt(self):
        from kaskad.bench import pkgs, quiet
        for osn in self.WANT:
            u = quiet.unit_counters(self.rd(osn, "71-systemctl-show-unit"))
            self.assertTrue(all(v is not None for v in u.values()), osn)
            self.assertIsNone(quiet.unit_counters(self.rd(osn, "72-systemctl-show-noacct"))["rx"])
            # the unit is gone: no counter at all — a sample the watch leaves out
            self.assertEqual(set(quiet.unit_counters(self.rd(osn, "74-systemctl-show-after")).values()), {None})
            names = set(n for n, _old in pkgs._INST.findall(self.rd(osn, "12-apt-sim")))
            self.assertTrue({"sysbench", "fio"} <= names, osn)
        # a fresh Debian 12 image: apt knows nothing before «apt-get update» (REV-ns-b1 З-5)
        self.assertIn("Unable to locate package sysbench", fx(os.path.join("real", "debian12",
                                                                            "10-apt-sim-noupdate.err")))


if __name__ == "__main__":
    unittest.main()

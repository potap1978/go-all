"""The run of the server check (SPEC-node-score §6.2 B1; SPEC-node-score-schedule-share NS-S4, NS-S8, NS-S9, §2.1,
§2.5 п. 5–6, §3.1–3.2): the transient unit and its limits, root only, no input of a person in a command, one check at
a time, the lock for the whole check, «свободен» before a manual check (busy -> a refusal with the numbers), the stop in
the middle, the queue file (format, 0600, atomically), the stale leftovers of a killed check, the marks of the
machine, the packages only by a manual check with consent."""

import json
import os
import signal
import stat
import subprocess
import sys
import time
from unittest import mock

from kaskad import paths, state
from kaskad.bench import channel, machine, marks, pkgs, run, suite
from kaskad.errors import KaskadError
from tests.bench.benchcase import MID, BenchCase, FakeMeter, FakeNet, fx
from tests.helpers import lock_held_elsewhere

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RID = "0123456789"
RUN_KEYS = {"id", "ts", "end_ts", "mode", "by", "sched_id", "retry_of", "inv", "status", "why", "why_args", "rx", "tx",
            "dur_s", "cpu_s", "under_load", "pkgs", "score", "scale_version", "links"}
TEST_KEYS = {"run", "test", "ts", "value", "unit", "scale_version", "mode", "under_load", "ok", "why", "score",
             "value_corr", "people", "ca"}
MINE = "kaskad-bench-%s.service loaded active running Kaskad 3: server check\n" % RID   # list-units inside the unit


class RunCase(BenchCase):
    def setUp(self):
        super().setUp()
        self.root_patch = mock.patch.object(run, "_euid", return_value=0)
        self.root_patch.start()
        self.set_state()

    def tearDown(self):
        self.root_patch.stop()
        super().tearDown()

    def spool(self, rid=RID):
        with open(os.path.join(run.spool_dir(), rid + ".json"), encoding="utf-8") as f:
            return json.load(f)

    def work(self, rid=RID):
        return run.work_dir(rid)

    def bench(self):
        return state.load()["gateway"]["hostmon"].get("bench") or {}


class CommandTest(RunCase):
    def test_unit_and_limits(self):
        argv = run.command(RID, "quick", "panel", lang="en")
        self.assertEqual(argv[:6], ["systemd-run", "--unit=kaskad-bench-0123456789.service",
                                    "--description=Kaskad 3: server check", "--collect", "--no-block", "--quiet"])
        props = [a.split("=", 1)[1] for a in argv if a.startswith("--property=")]
        self.assertEqual(props, ["CPUWeight=1", "IOWeight=1", "CPUSchedulingPolicy=idle", "Nice=19",
                                 "IOSchedulingClass=idle", "MemoryMax=256M", "RuntimeMaxSec=600", "IPAccounting=yes",
                                 "CPUAccounting=yes", "TimeoutStopSec=30",
                                 "ExecStopPost=/bin/rm -f " + os.path.join(run.work_dir(RID), "fio.tmp")])
        # З-7: a path that is not plain (a space, «%», a quote) — no ExecStopPost; the boot and the next check remove it
        for odd in ("/var/lib/kas kad/bench", "/var/lib/%n", '/var/lib/"x'):
            with mock.patch.object(run, "bench_dir", return_value=odd):
                self.assertFalse([a for a in run.command(RID, "quick", "cli") if "ExecStopPost" in a], odd)
        self.assertIn("--setenv=KASKAD_LANG=en", argv)
        i = argv.index("/usr/bin/python3")
        self.assertEqual(argv[i + 1:i + 3], ["-I", "-c"])
        self.assertEqual(argv[i + 4:], ["worker", "--id", RID, "--mode", "quick", "--by", "panel", "--precheck", "10"])
        sch = run.command(RID, "full", "schedule", sched_id="a1b2c3", precheck=0)
        self.assertEqual(sch[-6:], ["--precheck", "0", "--sched", "a1b2c3", "--skip", "8b,9"])   # NS-S5: always
        auto = run.command(RID, "quick", "auto", retry_of="abcdefabcd", force=True)
        self.assertEqual(auto[-5:], ["--retry-of", "abcdefabcd", "--force", "--skip", "8b,9"])
        auto = run.command(RID, "full", "auto", retry_of="abcdefabcd", skip=("up",), only=("net_bw", "cpu", "cpu"))
        self.assertEqual(auto[-4:], ["--skip", "8b,9,up", "--only", "cpu,net_bw"])
        for by in ("panel", "schedule"):                                # «only these» — the repeat alone
            with self.assertRaises(KaskadError):
                run.command(RID, "quick", by, sched_id="a1b2c3" if by == "schedule" else None, only=("up",))
        with self.assertRaises(KaskadError):
            run.command(RID, "quick", "auto", retry_of="abcdefabcd", only=("up; rm",))
        self.assertNotIn("--skip", run.command(RID, "quick", "panel"))  # a person's check: 8b by its consent

    def test_nothing_of_a_person_goes_in(self):
        for kw in ({"rid": "0123456789\n"}, {"rid": "../../etc"}, {"rid": "ABCDEF0123"}, {"mode": "quick;reboot"},
                   {"by": "web"}, {"by": "schedule", "sched_id": "a1\nExecStart=x"}, {"by": "schedule"},
                   {"by": "panel", "sched_id": "a1"}, {"by": "auto"}, {"by": "auto", "retry_of": "x" * 10},
                   {"by": "cli", "retry_of": RID}, {"rid": 123}):
            a = dict({"rid": RID, "mode": "quick", "by": "cli"}, **kw)
            with self.assertRaises(KaskadError, msg=kw):
                run.command(a.pop("rid"), a.pop("mode"), a.pop("by"), **a)

    def test_entry_of_the_unit_runs_the_package(self):
        argv = run.command(RID, "quick", "cli")
        code = argv[argv.index("-c") + 1].replace(paths.lib_current(), os.path.join(ROOT, "lib"))
        env = dict(os.environ, KASKAD_ROOT=self.root, LANG="en_US.UTF-8")
        p = subprocess.run([sys.executable, "-I", "-c", code, "worker", "--help"], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("--precheck", p.stdout)


class StartTest(RunCase):
    def test_root_only(self):
        with mock.patch.object(run, "_euid", return_value=1000):
            for fn in (lambda: run.start(), lambda: run.cancel(RID), lambda: run.worker(RID, "quick", "cli")):
                with self.assertRaises(KaskadError):
                    fn()

    def test_starts_the_unit(self):
        r = self.runner()
        with mock.patch.object(run, "new_id", return_value=RID):
            out = run.start("quick", "panel", runner=r)
        self.assertEqual(out, {"id": RID, "unit": "kaskad-bench-0123456789.service", "missing": []})
        sr = r.commands(["systemd-run"])
        self.assertEqual(len(sr), 1)
        self.assertEqual(sr[0][sr[0].index("--precheck") + 1], "10")
        self.assertFalse(r.commands(["env"]))                           # no apt

    def test_programs_already_there_set_the_mark(self):
        # REV-ns-b1 З-4: sysbench and fio are there — nothing to ask, and the schedule may arm (NS-S4)
        run.start("quick", "panel", runner=self.runner())
        self.assertEqual(self.bench()["pkgs"], "yes")
        st = state.load()
        st["gateway"]["hostmon"]["bench"]["pkgs"] = None
        state.save(st)
        run.start("quick", "schedule", sched_id="a1b2c3", runner=self.runner())
        self.assertIsNone(self.bench()["pkgs"])                        # the schedule never writes a consent

    def test_tmpfiles_line_for_a_reboot(self):
        # З-7: the fio file of a check cut by a reboot goes at the boot (only the file: meta.json stays for «killed»)
        run.start("quick", "cli", runner=self.runner())
        with open(run.tmpfiles_path(), encoding="utf-8") as f:
            text = f.read()
        # REV-ns-b1-2 придирка 1: «r!» — only at the boot; a «systemd-tmpfiles --remove» never takes the file of a
        # check that runs
        self.assertEqual(text.splitlines()[1].split()[:2], ["r!", "/var/lib/kaskad/bench/work-*/fio.tmp"])
        self.assertTrue(text.startswith("# Kaskad 3"))
        self.assertEqual(stat.S_IMODE(os.stat(run.tmpfiles_path()).st_mode), 0o644)
        with mock.patch.object(run.util, "atomic_write", side_effect=OSError("ro")):
            os.unlink(run.tmpfiles_path())
            run.start("quick", "cli", runner=self.runner())          # never the reason a check does not start

    def test_schedule_waits_itself(self):
        r = self.runner()
        run.start("quick", "schedule", sched_id="a1b2c3", runner=r)
        sr = r.commands(["systemd-run"])[0]
        self.assertEqual(sr[sr.index("--precheck") + 1], "0")

    def test_one_at_a_time(self):
        r = self.runner(extra=((["systemctl", "list-units"],
                                "kaskad-bench-abcdefabcd.service loaded active running Kaskad 3: server check\n"
                                "kaskad-bench-sched.service loaded active running x\n"),))
        with self.assertRaises(KaskadError) as cm:
            run.start("quick", "cli", runner=r)
        self.assertEqual(cm.exception.data, {"kind": "running", "id": "abcdefabcd"})
        self.assertFalse(r.commands(["systemd-run"]))

    def test_packages_with_consent_only(self):
        r = self.runner(have=("openssl",))
        with mock.patch("kaskad.gw.sys.apt_install", return_value=["sysbench", "fio"]) as apt:
            run.start("quick", "cli", runner=r)                        # not asked: nothing installed
            self.assertFalse(apt.called)
            self.assertIsNone(self.bench().get("pkgs"))
            out = run.start("quick", "cli", install=True, runner=r)
            apt.assert_called_once_with(r, ["sysbench", "fio"], mask=())
        self.assertEqual(out["missing"], ["sysbench", "fio"])           # the fake apt put nothing on the PATH
        b = self.bench()
        self.assertEqual(b["pkgs"], "yes")
        self.assertEqual(b["mid"], machine.mid())
        run.start("quick", "cli", install=False, runner=r)
        self.assertEqual(self.bench()["pkgs"], "no")

    def test_schedule_never_installs(self):
        r = self.runner(have=("openssl",))
        with mock.patch("kaskad.gw.sys.apt_install") as apt:
            for by, kw in (("schedule", {"sched_id": "a1"}), ("auto", {"retry_of": RID})):
                with self.assertRaises(KaskadError):
                    run.start("quick", by, install=True, runner=r, **kw)
            self.assertFalse(apt.called)
        self.assertFalse(r.commands(["systemd-run"]))
        with self.assertRaises(KaskadError):
            pkgs.install(r, ["sysbench", "nginx"], "cli")
        with self.assertRaises(KaskadError):
            pkgs.install(r, ["sysbench"], "schedule")

    def test_systemd_run_fails(self):
        r = self.runner(extra=((["systemd-run"], (1, "", "Failed to start transient service unit: Unit exists")),))
        with self.assertRaises(KaskadError):
            run.start("quick", "cli", runner=r)


class PlanTest(RunCase):
    # the place on the disk after the install (the coordinator, 19.10, п. 4): Installed-Size + the .deb in the cache
    # of apt, of the real outputs of 19.10; ``df`` before and after the install showed +71 / +69 / +64 / +76 MB
    # ubuntu2404: apt would also UPDATE libibverbs1 and ibverbs-providers already there (REV-ns-b1-3 П-3) — they are
    # named in the question and are not in the size (21 new packages, ≈ 61 MB instead of 23 and 63)
    REAL = {"debian12": (27, 46802, 21087500, 69), "ubuntu2204": (23, 52321, 14739188, 68),
            "ubuntu2404": (21, 48388, 11288668, 61), "debian13": (31, 51374, 22537052, 75)}
    UPGRADE = {"ubuntu2404": ["ibverbs-providers", "libibverbs1"]}

    def real_runner(self, osn):
        return self.runner(have=("openssl",), extra=(
            (["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-s"], fx("real/%s/12-apt-sim.out" % osn)),
            (["apt-cache", "show"], fx("real/%s/13-apt-cache-show.out" % osn))))

    def test_plan_and_question(self):
        for osn, (n, inst, deb, mb) in self.REAL.items():
            p = pkgs.plan(self.real_runner(osn))
            self.assertEqual(p["missing"], ["sysbench", "fio"])
            self.assertEqual(len(p["install"]), n, osn)
            self.assertEqual(p["upgrade"], self.UPGRADE.get(osn, []), osn)
            self.assertFalse(set(p["upgrade"]) & set(p["install"]), osn)
            self.assertTrue({"sysbench", "fio"} <= set(p["install"]), osn)
            self.assertEqual(p["kib"], inst + round(deb / 1024.0), osn)
            q = pkgs.question(p, "ru")
            self.assertEqual(q["default"], 1)
            self.assertEqual([x for x, _ in q["choices"]], [1, 2])
            self.assertIn("пакетов: %d, ≈ %d МБ на диске" % (n, mb), q["choices"][0][1], osn)
            self.assertIn("sysbench fio", q["text"])
            if osn in self.UPGRADE:
                self.assertIn("обновит уже стоящие пакеты: ibverbs-providers libibverbs1", q["text"], osn)
                self.assertIn("update packages already there: ibverbs-providers libibverbs1",
                              pkgs.question(p, "en")["text"], osn)
            else:
                self.assertNotIn("обновит", q["text"], osn)
            self.assertIn("about %d MB on disk" % mb, pkgs.question(p, "en")["choices"][0][1], osn)
        self.assertEqual(pkgs.question(p, "en")["choices"][1][1], "Cancel")
        # apt-cache without «Size» (or one of the packages short of a field): no number rather than a wrong one
        r = self.runner(have=("openssl",), extra=((["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-s"],
                                                   fx("apt-sim-debian12.txt")),
                                                  (["apt-cache", "show"], fx("apt-cache-show.txt"))))
        p = pkgs.plan(r)
        self.assertEqual(p["install"], ["fio", "libaio1", "libluajit-5.1-2", "libluajit-5.1-common", "libnuma1",
                                        "libpmem1", "sysbench"])
        self.assertIsNone(p["kib"])
        self.assertNotIn("МБ", pkgs.question(p, "ru")["choices"][0][1])

    def test_no_universe(self):
        self.write("/var/lib/apt/lists/archive.ubuntu.com_ubuntu_dists_noble_main_binary-amd64_Packages", "x")
        r = self.runner(have=(), extra=((["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-s"],
                                         (100, fx("apt-sim-ubuntu-nouniverse.txt"), "")),))
        p = pkgs.plan(r)
        self.assertFalse(p["available"])
        q = pkgs.question(p, "en")
        self.assertEqual(q["choices"], [])
        self.assertIn("universe", q["text"])

    def test_empty_lists_are_not_no_sources(self):
        # REV-ns-b1 З-5: a fresh image without package lists: apt knows nothing — that is not «not in the sources»
        sims = [(100, fx("apt-sim-ubuntu-nouniverse.txt"), ""), (0, fx("apt-sim-debian12.txt"), "")]
        r = self.runner(have=(), extra=((["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-s"],
                                         lambda argv, _i: sims.pop(0)),
                                        (["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "update"], ""),
                                        (["apt-cache", "show"], fx("apt-cache-show.txt"))))
        p = pkgs.plan(r)
        self.assertEqual((p["available"], p["kib"]), (None, None))
        self.assertFalse(r.commands(["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "update"]))   # no consent
        q = pkgs.question(p, "ru")
        self.assertEqual([n for n, _ in q["choices"]], [1, 2])         # «Установить», the size unknown
        self.assertNotIn("universe", q["text"])
        # the person agreed (a manual check): apt-get update once, then the simulation again
        sims[:] = [(100, fx("apt-sim-ubuntu-nouniverse.txt"), ""), (0, fx("apt-sim-debian12.txt"), "")]
        p = pkgs.plan(r, update=True, by="panel")
        self.assertTrue(p["available"])
        self.assertIn("sysbench", p["install"])
        up = r.commands(["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "update"])
        self.assertEqual(len(up), 1)
        self.assertIn("DPkg::Lock::Timeout=%d" % __import__("kaskad.aptlock", fromlist=["WAIT"]).WAIT, up[0])
        # the schedule never updates
        for by in ("schedule", "auto"):
            with self.assertRaises(KaskadError):
                pkgs.plan(r, update=True, by=by)
        # the update did not help: now it is «not in the sources»
        sims[:] = [(100, "", "")] * 2
        p = pkgs.plan(r, update=True, by="cli")
        self.assertEqual((p["available"], p["why"]), (False, "not_found"))
        self.assertIn("universe", pkgs.question(p, "ru")["text"])

    def test_update_failed_is_its_own_reason(self):
        # REV-ns-b1-2 придирка 4: «apt-get update» failed (no network) and the lists are still empty — the reason is
        # the lists, never «not in the sources … universe»
        r = self.runner(have=(), extra=((["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-s"],
                                         (100, fx("real/debian12/10-apt-sim-noupdate.out"),
                                          fx("real/debian12/10-apt-sim-noupdate.err"))),
                                        (["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "update"],
                                         (100, "", "E: Failed to fetch"))))
        p = pkgs.plan(r, update=True, by="panel")
        self.assertEqual((p["available"], p["why"], p["kib"]), (False, "no_lists", None))
        self.assertEqual(len(r.commands(["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-s"])), 1)
        for lang, word in (("ru", "списки пакетов"), ("en", "package lists")):
            q = pkgs.question(p, lang)
            self.assertIn(word, q["text"])
            self.assertNotIn("universe", q["text"])
            self.assertEqual(q["choices"], [])
        # the update failed, but some lists came (a mirror of several): the simulation goes again as before
        self.write("/var/lib/apt/lists/deb.debian.org_debian_dists_bookworm_main_binary-amd64_Packages", "x")
        r._handlers.insert(0, (["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-s"],
                               (0, fx("real/debian12/12-apt-sim.out"), "")))
        self.assertTrue(pkgs.plan(r, update=True, by="panel")["available"])

    def test_nothing_missing(self):
        r = self.runner()
        self.assertEqual(pkgs.plan(r), {"missing": [], "available": True, "install": [], "upgrade": [], "kib": 0,
                                         "why": None})
        self.assertFalse(r.commands(["env"]))


class WorkerTest(RunCase):
    def work_run(self, by="cli", mode="quick", runner=None, meter=None, **kw):
        self.last_meter = meter or FakeMeter()
        return run.worker(RID, mode, by, runner=runner or self.runner(), meter=self.last_meter, **kw)

    def test_ok_writes_the_queue(self):
        doc = self.work_run()
        self.assertEqual(doc, self.spool())
        self.assertEqual(set(doc), {"v", "run", "tests", "series"})
        self.assertEqual(doc["v"], 1)
        r = doc["run"]
        self.assertEqual(set(r), RUN_KEYS)
        self.assertEqual((r["id"], r["status"], r["why"], r["mode"], r["by"], r["under_load"], r["pkgs"]),
                         (RID, "ok", None, "quick", "cli", 0, 1))
        self.assertEqual(r["inv"]["engines"], ["awg", "vless"])
        m = self.last_meter
        self.assertEqual((r["rx"], r["tx"], r["cpu_s"]), (1000 * m.i, 2000 * m.i, float(m.i)))   # the last sample
        self.assertGreaterEqual(m.i, 3)                                    # before, at the start, at the end
        for t in doc["tests"]:
            self.assertEqual(set(t) - {"dns_src", "extra"}, TEST_KEYS)
            if t["test"] in ("net_down", "net_up") and t.get("value") is not None:   # до какой точки мерили (владелец 24.10)
                self.assertRegex(t["extra"]["target"], r"^[a-z0-9][a-z0-9-]{0,31}$")
            self.assertEqual((t["run"], t["mode"], t["under_load"]), (RID, "quick", 0))
        # REV-ns-b1-2 З-2: the source of the DNS is «dns_src» (the contract with the reader B12), never «src»
        self.assertEqual([(t["test"], t["dns_src"]) for t in doc["tests"] if "dns_src" in t], [("dns", "system")])
        self.assertFalse([t for t in doc["tests"] if "src" in t])
        self.assertEqual(r["inv"]["openssl_ver"], "3.0.14")
        self.assertEqual(len(doc["tests"]), 18)                           # 14 of B1/B2 + 4 of the channel (B3)
        sp = os.path.join(run.spool_dir(), RID + ".json")
        self.assertEqual(stat.S_IMODE(os.stat(sp).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(run.spool_dir()).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(run.bench_dir()).st_mode), 0o700)
        with open(run.results_path(), encoding="utf-8") as f:
            self.assertEqual(json.load(f), doc)
        self.assertFalse(os.path.exists(self.work()))
        self.assertFalse([n for n in os.listdir(run.spool_dir()) if n.startswith(".tmp")])
        # the lock is free again and its mtime says when the check ended
        import fcntl
        fd = os.open(run.lock_path(), os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.close(fd)
        self.assertLess(time.time() - os.stat(run.lock_path()).st_mtime, 60)
        # the mark of the first manual check, of this machine
        b = self.bench()
        self.assertEqual(b["first_manual_at"], r["end_ts"])
        self.assertEqual(b["mid"], machine.mid())
        self.assertNotIn(MID, json.dumps(state.load()))                  # never the raw machine id
        self.assertEqual(run.status(RID, self.runner())["state"], "done")

    def test_schedule_sets_no_mark(self):
        doc = self.work_run(by="schedule", sched_id="a1b2c3", precheck=0)
        self.assertEqual((doc["run"]["status"], doc["run"]["sched_id"]), ("ok", "a1b2c3"))
        self.assertIsNone(self.bench().get("first_manual_at"))

    def test_partial(self):
        doc = self.work_run(runner=self.runner(have=("openssl", "lscpu", "lsblk", "systemd-detect-virt")))
        self.assertEqual((doc["run"]["status"], doc["run"]["pkgs"]), ("partial", 0))
        self.assertTrue(any(t["why"] == "no_pkgs" for t in doc["tests"]))

    def test_locked(self):
        r = self.runner()
        with lock_held_elsewhere(run.lock_path()):
            doc = self.work_run(runner=r)
        self.assertEqual((doc["run"]["status"], doc["run"]["why"], doc["tests"]), ("skipped", "locked", []))
        self.assertFalse(r.commands(["sysbench"]) or r.commands(["fio"]) or r.commands(["openssl"]))

    def test_two_checks_at_once(self):
        # the lock is held by another check of the server (two starts raced) — «Уже идёт другая проверка сервера»
        r = self.runner(extra=((["systemctl", "list-units"],
                                MINE + "kaskad-bench-abcdefabcd.service loaded active running x\n"),))
        with lock_held_elsewhere(run.lock_path()):
            doc = self.work_run(runner=r)
        self.assertEqual((doc["run"]["status"], doc["run"]["why"]), ("skipped", "running"))
        self.assertEqual(run.why_text("running", None, "ru"), "Уже идёт другая проверка сервера")

    def test_the_unknown_country_is_in_the_cli(self):
        """REV-ns-b3-2 З-1: ``status`` (the CLI) gives the note «страна не определена» of test 8 as a text, RU/EN;
        the file of the check keeps the code only. The file goes through ``spool.put`` (B11: the strict writer of the
        queue — a real moment, rows by the contract), the cleaning is ``spool.prune`` (a check of 1970 is gone)."""
        t = int(time.time()) - 60
        doc = {"v": run.V, "run": dict(run._run_doc(RID, "quick", "cli", None, None, t), status="ok", end_ts=t + 30),
               "tests": [{"test": "net_down", "ok": 1, "value": 640.0, "unit": "Mbit/s",
                          "extra": {"geo": "unknown", "budget": True}},
                         {"test": "dns", "ok": 1, "value": 12.0, "unit": "ms"},
                         {"test": "net_up", "ok": 1, "value": 300.0, "unit": "Mbit/s"}]}
        run.write_doc(doc)
        got = dict((t["test"], t) for t in run.status(RID, self.runner())["result"]["tests"])
        self.assertEqual(got["net_down"]["geo_text"], run.geo_text("unknown"))
        self.assertNotIn("geo_text", got["dns"])
        self.assertNotIn("geo_text", got["net_up"])
        # a file the writer would refuse (a hand edit, an older writer): ``extra`` not a dict — no text, no crash
        bad = json.loads(json.dumps(doc))
        bad["tests"][2]["extra"] = "x"
        with open(os.path.join(run.spool_dir(), RID + ".json"), "w", encoding="utf-8") as f:
            json.dump(bad, f)
        got = dict((t["test"], t) for t in run.status(RID, self.runner())["result"]["tests"])
        self.assertNotIn("geo_text", got["net_up"])
        self.assertEqual(got["net_down"]["geo_text"], run.geo_text("unknown"))
        run.write_doc(doc)
        # REV-ns-p3a П-2: «as for Russia» — only the checks since П3-б (unknown_ru); an older row (unknown: the button
        # measured abroad then) keeps the careful text, never signed «as Russia» after the fact
        self.assertEqual(run.geo_text("unknown_ru", "ru"),
                         "Страна сервера не определена — канал измерен как для России")
        self.assertTrue(run.geo_text("unknown_ru", "en").endswith("measured as for Russia"))
        for lang in ("ru", "en"):
            old = run.geo_text("unknown", lang)
            self.assertNotRegex(old, "Росси|Russia")
            self.assertTrue(old.startswith(("Страна сервера не определена", "The country of the server is not known")))
        self.assertIsNone(run.geo_text("abroad", "ru"))
        self.assertIsNone(run.geo_text(None, "ru"))
        with open(os.path.join(run.spool_dir(), RID + ".json"), encoding="utf-8") as f:
            self.assertNotIn("geo_text", f.read())

    def test_only_the_inventory_is_not_a_measured_check(self):
        # придирка 1: nothing but the inventory — «partial», but no first manual check and results.json stays
        os.makedirs(run.bench_dir(), 0o700, exist_ok=True)
        with open(run.results_path(), "w") as f:
            f.write('{"old": 1}')
        self.set_state(probes=False)
        with mock.patch.object(suite, "dns_once", return_value=None), \
                mock.patch.object(channel, "Net", lambda: FakeNet(down=None, up=None)):
            doc = self.work_run(runner=self.runner(have=("lscpu", "lsblk", "systemd-detect-virt")))
        self.assertEqual(doc["run"]["status"], "partial")
        self.assertEqual([t["test"] for t in doc["tests"] if t["ok"]], ["mem_gb"])
        self.assertIsNone(self.bench().get("first_manual_at"))
        with open(run.results_path()) as f:
            self.assertEqual(f.read(), '{"old": 1}')
        self.assertEqual(self.spool()["run"]["status"], "partial")    # the queue has the record all the same

    def test_one_step_that_raises_never_ends_the_check(self):
        # REV-ns-b1 З-2: Prober.series raises OSError — the «now» rows «не измерено», the rest goes, results.json
        class Broken(object):
            def series(self):
                raise OSError("no socket")
        with mock.patch("kaskad.gw.hostping.Prober", side_effect=lambda *a, **k: Broken()):
            doc = self.work_run()
        rows = self.by_test(doc["tests"])
        self.assertEqual(doc["run"]["status"], "partial")
        self.assertEqual((rows["now_loss"]["why"], rows["now_rtt"]["why"]), ("tool_failed", "tool_failed"))
        self.assertEqual(len(doc["tests"]), 18)                           # 14 of B1/B2 + 4 of the channel (B3)
        self.assertTrue(rows["disk_fsync"]["ok"] and rows["cpu_sustain"]["ok"])
        self.assertTrue(os.path.exists(run.results_path()))

    def test_stop_is_never_swallowed_by_a_broad_except(self):
        # придирка 2: Cancelled is a BaseException — «except Exception» of a step (here _engines) lets it through
        self.assertFalse(issubclass(suite.Cancelled, Exception))
        with mock.patch("kaskad.gw.model.enabled_kinds", side_effect=run._Stop()):
            doc = self.work_run()
        self.assertEqual((doc["run"]["status"], doc["run"]["why"]), ("cancelled", "stopped"))

    def test_the_record_is_written_with_sigterm_ignored(self):
        seen = []
        real = run.write_doc

        def write(doc):
            seen.append(signal.getsignal(signal.SIGTERM))
            return real(doc)
        with mock.patch.object(run, "write_doc", write):
            self.work_run()
        self.assertEqual(seen, [signal.SIG_IGN])

    def test_busy_refused_with_the_numbers(self):
        r = self.runner()
        doc = self.work_run(runner=r, meter=FakeMeter(mbps=4.2))
        run_ = doc["run"]
        self.assertEqual((run_["status"], run_["why"]), ("skipped", "busy"))
        self.assertEqual(sorted(run_["why_args"]), ["cpu", "io", "mbps", "thr_cpu", "thr_io", "thr_mbps"])
        self.assertAlmostEqual(run_["why_args"]["mbps"], 4.2, places=2)
        self.assertEqual((run_["why_args"]["thr_mbps"], run_["why_args"]["thr_cpu"]), (2.0, 20.0))
        self.assertFalse(r.commands(["sysbench"]))
        self.assertEqual(run.why_text("busy", dict(run_["why_args"], mbps=4.2), "ru"),
                         "Сервер был занят: чужой трафик 4,2 Мбит/с при пороге 2 Мбит/с, процессор 0% при пороге 20%")
        self.assertFalse(os.path.exists(self.work()))
        self.assertIsNone(self.bench().get("first_manual_at"))
        self.assertFalse(os.path.exists(run.results_path()))           # the last numbers are not hidden by a skip
        doc = self.work_run(runner=self.runner(), meter=FakeMeter(cpu_pct=35.0))
        self.assertEqual(doc["run"]["why"], "busy")

    def test_busy_disk_before_the_start(self):
        # REV-ns-b1 З-9 (RS §2): iowait of the 10 s ≥ 20 % — «занят: диск»; below — the check goes
        r = self.runner()
        doc = self.work_run(runner=r, meter=FakeMeter(io_pct=25.0))
        run_ = doc["run"]
        self.assertEqual((run_["status"], run_["why"]), ("skipped", "busy_disk"))
        self.assertEqual((run_["why_args"]["io"], run_["why_args"]["thr_io"]), (25.0, 20.0))
        self.assertFalse(r.commands(["fio"]))
        self.assertEqual(run.why_text("busy_disk", run_["why_args"], "ru"),
                         "Сервер был занят диском: ожидание диска 25% при пороге 20%")
        self.assertEqual(run.why_text("busy_disk", run_["why_args"], "en"),
                         "The server was busy with the disk: disk wait 25% (limit 20%)")
        self.assertEqual(self.work_run(meter=FakeMeter(io_pct=19.0))["run"]["status"], "ok")
        # the schedule (precheck 0) never looks: its own 5 min are B11's
        doc = self.work_run(by="schedule", sched_id="a1", precheck=0, meter=FakeMeter(io_pct=90.0))
        self.assertEqual(doc["run"]["status"], "ok")

    def test_texts_of_the_reasons(self):
        args = {"mbps": 4.2, "thr_mbps": 2.0, "cpu": None, "thr_cpu": 20.0}
        self.assertEqual(run.why_text("busy", args, "ru"), "Сервер был занят: чужой трафик 4,2 Мбит/с при пороге 2 Мбит/с")
        self.assertEqual(run.why_text("busy", args, "en"), "The server was busy: other traffic 4.2 Mbit/s (limit 2 Mbit/s)")
        self.assertNotIn("?", run.why_text("busy", args, "ru"))
        self.assertIn("3 ГиБ", run.why_text("disk_space", None, "ru"))
        self.assertIn("3 GiB", run.why_text("disk_space", None, "en"))
        self.assertEqual(suite.FIO_FREE_MIN, 3 * 1024 ** 3)            # the text says the unit of the threshold

    def test_no_raw_placeholder_ever(self):
        """B1↔B12: the args of a reason may be short (``busy`` with ``mbps`` only, an old queue file) — a raw
        «{cpu}» never reaches a person: a shorter text, or the text without numbers."""
        from kaskad import i18n
        self.assertEqual(run.why_text("busy", {"mbps": 4.2}, "ru"), "Сервер был занят")
        self.assertEqual(run.why_text("busy", {"mbps": 4.2}, "en"), "The server was busy")
        self.assertEqual(run.why_text("busy_disk", {"io": 30}, "ru"), "Сервер был занят диском")
        self.assertEqual(run.why_text("busy", {"mbps": 4.2, "thr_mbps": 2, "cpu": True, "thr_cpu": 20}, "en"),
                         "The server was busy: other traffic 4.2 Mbit/s (limit 2 Mbit/s)")
        self.assertEqual(run.why_text("missed", {"n": 3}, "ru"),
                         "Пропущено проверок по расписанию: 3 — сервер был выключен или таймер не сработал")
        self.assertNotIn("{", run.why_text("missed", None, "en"))
        for lang in ("ru", "en"):
            codes = [k[len("bench_run.why."):] for k in i18n.catalog(lang) if k.startswith("bench_run.why.")]
            self.assertGreater(len(codes), 20)
            for code in codes:
                for args in (None, {}, {"mbps": 1}, {"n": "x"}, {"cpu": float("nan")}):
                    text = run.why_text(code, args, lang)
                    self.assertNotIn("{", text, (code, args, lang))
                    self.assertNotIn("}", text, (code, args, lang))
                    self.assertTrue(text and text != code, (code, args, lang))

    def test_busy_forced_runs_under_load(self):
        doc = self.work_run(meter=FakeMeter(mbps=4.2), force=True)
        r = doc["run"]
        self.assertEqual((r["status"], r["under_load"]), ("ok", 1))
        self.assertAlmostEqual(r["why_args"]["forced"]["mbps"], 4.2, places=2)
        self.assertTrue(all(t["under_load"] == 1 for t in doc["tests"]))

    def test_forced_without_accounting_is_still_under_load(self):
        doc = self.work_run(meter=FakeMeter(mbps=4.2, known=False), force=True)
        self.assertEqual(doc["run"]["under_load"], 1)

    def test_no_accounting(self):
        doc = self.work_run(meter=FakeMeter(known=False))
        r = doc["run"]
        self.assertEqual((r["status"], r["under_load"], r["why_args"]["load"]), ("ok", None, "unknown"))

    def test_one_failed_sample_keeps_the_load_known(self):
        # REV-ns-b1 З-1: the 3rd ``systemctl show`` (the first of the watch) failed — left out, the load is known
        doc = self.work_run(meter=FakeMeter(fail=(3,)))
        r = doc["run"]
        self.assertEqual((r["under_load"], "load" in r["why_args"]), (0, False))
        m = self.last_meter
        self.assertEqual(r["rx"], 1000 * m.i)
        # the last one failed: the counters of the unit — of the last good sample, never null
        meter = FakeMeter()
        meter.fail = {4}
        with mock.patch.object(channel, "Net", lambda: FakeNet(use_probe=False)):   # no sample of the channel
            doc = self.work_run(meter=meter)
        self.assertEqual((doc["run"]["under_load"], doc["run"]["rx"]), (0, 3000))

    def stop_at(self, prefix):
        def h(argv, _input):
            if argv[0] == "fio":
                self.fio(argv, _input)
            os.kill(os.getpid(), signal.SIGTERM)
            return 0, "", ""
        return self.runner(extra=((prefix, h),))

    def test_stop_in_the_middle(self):
        r = self.stop_at(["sysbench", "memory"])
        doc = self.work_run(runner=r)
        run_ = doc["run"]
        self.assertEqual((run_["status"], run_["why"]), ("cancelled", "stopped"))
        names = [t["test"] for t in doc["tests"]]
        self.assertIn("aes_1t", names)
        self.assertNotIn("mem_bw", names)
        self.assertFalse(r.commands(["fio"]))
        self.assertFalse(os.path.exists(self.work()))
        self.assertIs(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)   # the handler of the test process is back

    def test_after_the_record_the_lock_is_free_and_stop_works(self):
        """REV З-7: the drops and the repeat are worked out after the queue file is written and the lock of the speed
        tests is let go; SIGTERM there («Остановить») ends the count — the record stays, no repeat."""
        from kaskad.bench import sched_run
        seen = []

        def replay(doc):
            seen.append((sched_run.try_lock(run.lock_path()), os.path.exists(os.path.join(run.spool_dir(),
                                                                                         RID + ".json"))))
            os.kill(os.getpid(), signal.SIGTERM)
            return ["up"]
        with mock.patch.object(run, "drops_of", side_effect=replay), \
                mock.patch.object(sched_run, "plan_retry") as plan:
            doc = self.work_run()
        self.assertEqual(seen, [(True, True)])
        plan.assert_not_called()
        self.assertEqual(doc["run"]["status"], "ok")
        self.assertEqual(self.spool()["run"]["status"], "ok")
        self.assertIs(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)

    def test_stop_during_fio_removes_the_file(self):
        doc = self.work_run(runner=self.stop_at(["fio", "--name=kbench"]))
        self.assertEqual(doc["run"]["status"], "cancelled")
        self.assertTrue(self.fio_files)
        self.assertFalse(os.path.exists(self.fio_files[0]))

    def test_end_of_runtime_is_timeout(self):
        t = {"v": 1000.0}

        def mono():
            return t["v"]

        def late(argv, _input):
            t["v"] += run.RUNTIME_MAX_S
            os.kill(os.getpid(), signal.SIGTERM)
            return 0, "", ""
        with mock.patch.object(run, "_mono", mono):
            doc = self.work_run(runner=self.runner(extra=((["sysbench", "memory"], late),)))
        self.assertEqual((doc["run"]["status"], doc["run"]["why"]), ("cancelled", "timeout"))

    def test_error_is_a_record(self):
        with mock.patch.object(suite, "run_all", side_effect=RuntimeError("boom /etc/secret")):
            doc = self.work_run()
        self.assertEqual((doc["run"]["status"], doc["run"]["why"], doc["run"]["why_args"]["type"]),
                         ("failed", "error", "RuntimeError"))
        self.assertNotIn("secret", json.dumps(doc))

    def test_stale_leftovers(self):
        stale = run.work_dir("abcdefabcd")
        os.makedirs(stale)
        with open(os.path.join(stale, "meta.json"), "w") as f:
            json.dump({"mode": "full", "by": "panel", "ts": 1791190000}, f)
        with open(os.path.join(stale, "fio.tmp"), "w") as f:
            f.write("x")
        live = run.work_dir("bbbbbbbbbb")
        os.makedirs(live)
        r = self.runner(extra=((["systemctl", "list-units"],
                                MINE + "kaskad-bench-bbbbbbbbbb.service loaded active running x\n"),))
        self.work_run(runner=r)
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.isdir(live))                             # a check that runs is not touched
        killed = self.spool("abcdefabcd")["run"]
        self.assertEqual((killed["status"], killed["why"], killed["mode"], killed["ts"]),
                         ("failed", "killed", "full", 1791190000))

    def stale_dir(self, rid="abcdefabcd", **meta):
        d = run.work_dir(rid)
        os.makedirs(d)
        with open(os.path.join(d, "meta.json"), "w") as f:
            json.dump(dict({"mode": "quick", "by": "schedule", "ts": 1791190000}, **meta), f)
        return d

    def test_stale_only_under_the_lock_and_with_our_unit_seen(self):
        # REV-ns-b1 З-3: list-units empty or failed — «no unit» is not proven: nothing removed
        d = self.stale_dir()
        for answer in ("", (1, "", "Failed to connect to bus: timeout")):
            self.work_run(runner=self.runner(extra=((["systemctl", "list-units"], answer),)))
            self.assertTrue(os.path.isdir(d), answer)
        self.assertFalse(os.path.exists(os.path.join(run.spool_dir(), "abcdefabcd.json")))
        # the lock is not ours (another check holds it): no cleaning at all, not even a list-units for it
        r = self.runner(extra=((["systemctl", "list-units"], MINE),))
        with lock_held_elsewhere(run.lock_path()):
            self.work_run(runner=r)
        self.assertTrue(os.path.isdir(d))

    def test_killed_record_keeps_the_schedule_and_its_last_mark(self):
        # REV-ns-b1 З-6: sched_id / retry_of of the killed check; its end — the last mark, not the next start
        d = self.stale_dir(sched_id="a1b2c3", retry_of=None)
        with open(os.path.join(d, "progress.json"), "w") as f:
            f.write("{}")
        os.utime(os.path.join(d, "meta.json"), (1791190000, 1791190000))
        os.utime(os.path.join(d, "progress.json"), (1791190095, 1791190095))
        d2 = self.stale_dir("cdcdcdcdcd", by="auto", retry_of="abababab12")
        os.utime(os.path.join(d2, "meta.json"), (1791190007, 1791190007))
        self.work_run(runner=self.runner(extra=((["systemctl", "list-units"], MINE),)))
        k = self.spool("abcdefabcd")["run"]
        self.assertEqual((k["status"], k["why"], k["by"], k["sched_id"], k["retry_of"], k["end_ts"]),
                         ("failed", "killed", "schedule", "a1b2c3", None, 1791190095))
        k = self.spool("cdcdcdcdcd")["run"]
        self.assertEqual((k["by"], k["sched_id"], k["retry_of"], k["end_ts"]), ("auto", None, "abababab12", 1791190007))
        # and our own meta.json carries both for the next one
        seen = {}
        real = run.util.atomic_write

        def aw(path, data, mode=0o600):
            if path.endswith("meta.json"):
                seen.update(json.loads(data))
            return real(path, data, mode)
        with mock.patch.object(run.util, "atomic_write", aw):
            os.unlink(os.path.join(run.spool_dir(), RID + ".json"))
            self.work_run(by="auto", retry_of="abababab12", precheck=0)
        self.assertEqual((seen["sched_id"], seen["retry_of"], seen["by"]), (None, "abababab12", "auto"))

    def test_killed_record_never_takes_junk_of_meta(self):
        # REV-ns-b1-2 придирка 5: sched_id / retry_of of a meta.json that is not ours (junk, a line break, a list) —
        # null in the record, the record is still written
        for i, (sid, rof) in enumerate((("a1\nExecStart=x", "abababab12\n"), (["a1"], 12), ("A" * 40, "x" * 10))):
            rid = "%010x" % (0xabc0 + i)
            self.stale_dir(rid, sched_id=sid, retry_of=rof)
            self.work_run(runner=self.runner(extra=((["systemctl", "list-units"], MINE),)))
            k = self.spool(rid)["run"]
            self.assertEqual((k["status"], k["why"], k["sched_id"], k["retry_of"]), ("failed", "killed", None, None),
                             repr((sid, rof)))
            os.unlink(os.path.join(run.spool_dir(), RID + ".json"))

    def test_prune_spool(self):
        """One cleaning (REV-ns-b12-2): ``spool.prune`` by ``run.ts`` — old checks go, at most MAX_FILES, the old
        tariff files go too (without the gateway nobody else cuts them), a broken file goes to bad/."""
        from kaskad.bench import spool
        now = int(time.time())

        def put(rid, ts):
            r = run._run_doc(rid, "quick", "cli", None, None, ts)
            r.update(status="skipped", why="busy", end_ts=ts)
            return spool.put({"v": run.V, "run": r, "tests": []})
        for i in range(7):
            put("%010x" % i, now - 1000 + i)
        put("ffffffffff", now - spool.KEEP_S - 10)
        with open(os.path.join(run.spool_dir(), "eeeeeeeeee.json"), "w") as f:
            f.write("{}")
        spool.put_epoch(now - spool.KEEP_S - 10)
        spool.put_epoch(now - 50)
        with mock.patch.object(spool, "MAX_FILES", 5):
            self.assertEqual(run.prune_spool(now), 4)
        names = sorted(n for n in os.listdir(run.spool_dir()) if n != "bad")
        self.assertEqual(names, ["%010x.json" % i for i in range(2, 7)] + ["epoch-%010d-tariff.json" % (now - 50)])
        self.assertEqual(os.listdir(os.path.join(run.spool_dir(), "bad")), ["eeeeeeeeee.json"])
        for n in names:
            self.assertEqual(os.stat(os.path.join(run.spool_dir(), n)).st_mode & 0o777, 0o600)

    def test_the_file_of_a_check_goes_through_spool_put(self):
        from kaskad.bench import spool
        with mock.patch.object(spool, "put", wraps=spool.put) as put:
            doc = self.work_run()
        put.assert_called_once()
        self.assertEqual(spool.load_run(os.path.join(run.spool_dir(), doc["run"]["id"] + ".json"))[0]["id"],
                         doc["run"]["id"])


class CancelStatusTest(RunCase):
    def test_cancel(self):
        r = self.runner(extra=((["systemctl", "list-units"],
                                "kaskad-bench-0123456789.service loaded active running x\n"),))
        self.assertTrue(run.cancel(RID, r))
        self.assertEqual(r.commands(["systemctl", "stop"]), [["systemctl", "stop", "--no-block",
                                                              "kaskad-bench-0123456789.service"]])
        self.assertFalse(run.cancel("abcdefabcd", r))                     # not running: nothing to stop
        with self.assertRaises(KaskadError):
            run.cancel("kaskad-gw-collect", r)

    def test_the_progress_says_the_stage(self):
        """T-3377 Н-8: progress.json — the step i of n and the stage k of m of run_all (read at the call: the writer
        is made before the ctx); without a stage — null, as a check started before the update."""
        os.makedirs(self.work(), 0o700)
        box = {"stage": None}
        write = run._progress(self.work(), RID, lambda: box["stage"])
        write("inv", 0, 0)
        with open(os.path.join(self.work(), "progress.json")) as f:
            self.assertEqual({k: v for k, v in json.load(f).items() if k != "ts"},
                             {"id": RID, "step": "inv", "i": 0, "n": 0, "k": None, "m": None})
        box["stage"] = (3, 10)
        write("dns", 2, 3)
        with open(os.path.join(self.work(), "progress.json")) as f:
            doc = json.load(f)
        self.assertEqual([doc[k] for k in ("step", "i", "n", "k", "m")], ["dns", 2, 3, 3, 10])

    def test_status_running(self):
        os.makedirs(self.work(), 0o700)
        with open(os.path.join(self.work(), "progress.json"), "w") as f:
            json.dump({"id": RID, "step": "disk", "i": 2, "n": 3}, f)
        r = self.runner(extra=((["systemctl", "list-units"],
                                "kaskad-bench-0123456789.service loaded active running x\n"),))
        self.assertEqual(run.status(RID, r), {"state": "running",
                                              "progress": {"id": RID, "step": "disk", "i": 2, "n": 3}})
        self.assertEqual(run.status("abcdefabcd", r), {"state": "unknown"})
        for bad in ("../../etc/passwd", "0123456789\n", "ABCDEF0123", None):
            with self.assertRaises(KaskadError, msg=bad):
                run.status(bad, r)
        self.assertFalse([c for c in r.commands() if "passwd" in " ".join(c)])


class MarksTest(RunCase):
    def test_machine_id_forms(self):
        for text, ok in ((MID + "\n", True), (MID, True), ("", False), ("uninitialized\n", False),
                         (MID + "\n\n", False), (MID[:31] + "\n", False), (MID.upper() + "\n", False)):
            self.write("/etc/machine-id", text)
            self.assertEqual(machine.mid() is not None, ok, repr(text))
        os.unlink(self.path("/etc/machine-id"))
        self.assertIsNone(machine.mid())
        self.assertFalse(marks.record(first_manual=True))
        self.assertEqual(self.bench(), {})

    def test_mid_is_hmac(self):
        import hashlib
        import hmac
        self.assertEqual(machine.mid(), hmac.new(MID.encode(), b"kaskad-bench", hashlib.sha256).hexdigest()[:32])

    def test_other_machine_resets_every_mark(self):
        st = state.load()
        st["gateway"]["hostmon"]["bench"] = {"mid": "f" * 32, "pkgs": "yes", "first_manual_at": 1, "armed_at": 2,
                                             "schedule": [{"id": "a1"}]}
        state.save(st)
        marks.record(first_manual=True, now=1791190000)
        b = self.bench()
        self.assertEqual((b["first_manual_at"], b["pkgs"], b["armed_at"], b["mid"]),
                         (1791190000, None, None, machine.mid()))
        self.assertEqual(b["schedule"], [{"id": "a1"}])                   # the entries stay
        marks.record(first_manual=True, now=1791199999)
        self.assertEqual(self.bench()["first_manual_at"], 1791190000)    # the first one stays
        marks.record(pkgs="yes")
        self.assertEqual(self.bench()["pkgs"], "yes")
        self.assertIsNotNone(self.bench()["armed_at"])                    # the rule of B10: both marks — armed
        with self.assertRaises(ValueError):
            marks.record(pkgs="maybe")

    def test_one_function_of_the_marks(self):
        # REV-ns-b1-2 З-3: the marks go through machine.mark of B10 — one rule of the change of machine
        with mock.patch.object(machine, "mark", wraps=machine.mark) as mk:
            marks.record(first_manual=True, pkgs="yes", now=1791190000)
        self.assertEqual([c.args[1:3] for c in mk.call_args_list], [("first_manual_at", 1791190000), ("pkgs", "yes")])
        self.assertFalse(hasattr(marks, "machine_mid"))

    def test_written_only_when_changed(self):
        # REV-ns-b1-2 придирка 3: a manual start with the programs there every time — no lock, no save, no AFTER
        seen = []
        with mock.patch.object(marks, "AFTER", [lambda st: seen.append(1)]):
            self.assertTrue(marks.record(pkgs="yes"))
            with mock.patch.object(state, "save", wraps=state.save) as sv, \
                    mock.patch.object(state, "locked", wraps=state.locked) as lk:
                self.assertFalse(marks.record(pkgs="yes"))
                run.start("quick", "panel", runner=self.runner())
                run.start("quick", "cli", runner=self.runner())
                self.assertFalse(sv.called or lk.called)
            self.assertTrue(marks.record(pkgs="no"))
        self.assertEqual(seen, [1, 1])

    def test_a_failed_mark_never_stops_the_check(self):
        # REV-ns-b1-2 придирка 3: a broken state.json or a busy lock — the check starts, a line of the journal
        lines = []
        log = lambda key, **kw: lines.append((key, kw))                   # noqa: E731
        with mock.patch.object(run.util, "log_tr", side_effect=log):
            with open(paths.state(), "w") as f:
                f.write("{broken")
            r = self.runner()
            with mock.patch.object(run, "new_id", return_value=RID):
                out = run.start("quick", "panel", runner=r)
            self.assertEqual(out["id"], RID)
            self.assertEqual(len(r.commands(["systemd-run"])), 1)
            self.assertIn(("bench_run.log.mark_failed", {"id": RID, "why": "StateError"}), lines)
        self.set_state()
        lines[:] = []
        t0 = time.time()
        with mock.patch.object(marks, "TIMEOUT", 0.3), mock.patch.object(run.util, "log_tr", side_effect=log), \
                lock_held_elsewhere(paths.lock()):
            r = self.runner()
            run.start("quick", "cli", runner=r)
        self.assertLess(time.time() - t0, 5)
        self.assertEqual(len(r.commands(["systemd-run"])), 1)
        self.assertEqual([k for k, kw in lines if k == "bench_run.log.mark_failed"], ["bench_run.log.mark_failed"])

"""The channel in the check of the node (B3): the glue of ``bench.channel`` in ``bench.suite`` (the country of the node,
the allowed upload point, the load, the family), the extension point of the schedule and the repeat (``skip``,
``iperf`` of ``run.start``/``worker``/the command), the contract of the queue with B12 (``bench.spool.rows``) — and
the batch of REV-ns-b1-3: З-1 (the inventory within the limits of the queue), П-1 (DNS id and QR, partial apt lists,
OSError of a mark), П-2 (``parse._num``), П-3 (updates of packages), П-4 (the two texts of a failed mark)."""

import io
import json
import os
import socket
import struct
import threading
import unittest
from unittest import mock

from kaskad import paths, state
from kaskad.bench import __main__ as M
from kaskad.bench import channel, parse, pkgs, run, spool, suite
from kaskad.errors import KaskadError
from tests.bench.benchcase import FakeMeter, FakeNet, fx
from tests.bench.test_run import RID, RunCase


class ExtensionPointTest(RunCase):
    def test_command_carries_iperf_and_skip(self):
        argv = run.command(RID, "full", "panel", iperf=True, skip=("up", "8b"))
        self.assertEqual(argv[-3:], ["--iperf", "--skip", "8b,up"])
        self.assertNotIn("--iperf", run.command(RID, "quick", "panel"))
        self.assertNotIn("--skip", run.command(RID, "quick", "panel"))

    def test_8b_never_by_the_schedule_or_the_repeat(self):
        for by, kw in (("schedule", {"sched_id": "a1"}), ("auto", {"retry_of": "0123456789"})):
            with self.assertRaises(KaskadError):
                run.command(RID, "full", by, iperf=True, **kw)
            # the repeat and the schedule of a node in Russia leave out 8b, 9 and the upload (B11)
            argv = run.command(RID, "full", by, skip=("8b", "9", "up"), **kw)
            self.assertEqual(argv[-2:], ["--skip", "8b,9,up"])
        with self.assertRaises(KaskadError):
            run.command(RID, "full", "panel", skip=("disk",))

    def test_8b_by_every_person_never_by_the_schedule(self):
        """D-144 (владелец 29.10): 8b — on by default in a full check of a person, MCP too (its description and the
        price say the line «the point sees the address»; ``no_upload_ru`` turns it off); the schedule and the repeat —
        never (test_8b_never_by_the_schedule_or_the_repeat)."""
        for by in ("panel", "bot", "ssh", "cli", "mcp"):
            self.assertIn("--iperf", run.command(RID, "full", by, iperf=True))

    def test_cli_passes_them_to_the_worker(self):
        with mock.patch.object(run, "worker", return_value={"run": {"status": "ok"}}) as w, \
                mock.patch.object(M.sys, "stdout", io.StringIO()):
            M.main(["worker", "--id", RID, "--mode", "full", "--by", "panel", "--iperf", "--skip", "up,8b"])
        self.assertEqual(w.call_args.kwargs["skip"], ("up", "8b"))
        self.assertTrue(w.call_args.kwargs["iperf"])
        with mock.patch.object(run, "start", return_value={}) as s, mock.patch.object(M.sys, "stdout", io.StringIO()):
            M.main(["start", "--mode", "full", "--iperf"])
        self.assertTrue(s.call_args.kwargs["iperf"])
        self.assertEqual(s.call_args.kwargs["skip"], ())

    def test_start_installs_iperf3_by_the_same_answer(self):
        r = self.runner(have=self.TOOLS)                     # sysbench and fio there, iperf3 not
        with mock.patch.object(pkgs, "install", return_value=["iperf3"]) as inst, \
                mock.patch.object(run, "new_id", return_value=RID):
            out = run.start("full", "panel", install=True, runner=r, iperf=True)
        self.assertEqual(inst.call_args.args[1], ["iperf3"])
        self.assertEqual(out["missing"], ["iperf3"])        # the fake apt installed nothing
        self.assertIn("--iperf", r.commands(["systemd-run"])[0])
        with mock.patch.object(pkgs, "install") as inst:
            run.start("full", "panel", install=True, runner=self.runner(have=self.TOOLS))
        inst.assert_not_called()                             # without 8b iperf3 is not a missing package
        # D-144: the product passes what its price showed — abroad 8b allowed by default installs no iperf3 (not asked)
        with mock.patch.object(pkgs, "install") as inst, mock.patch.object(run, "new_id", return_value=RID):
            out = run.start("full", "panel", install=True, runner=self.runner(have=self.TOOLS), iperf=True,
                            with_iperf3=False)
        inst.assert_not_called()
        self.assertEqual(out["missing"], [])
        with mock.patch.object(pkgs, "install", return_value=["iperf3"]) as inst, \
                mock.patch.object(run, "new_id", return_value=RID):
            run.start("full", "panel", install=True, runner=self.runner(have=self.TOOLS), iperf=False, with_iperf3=True)
        self.assertEqual(inst.call_args.args[1], ["iperf3"])   # in Russia, the upload turned off: as the price showed

    def test_pkgs_know_iperf3(self):
        r = self.runner(have=self.TOOLS)
        self.assertEqual(pkgs.missing(r), [])
        self.assertEqual(pkgs.missing(r, iperf=True), ["iperf3"])

        def q(missing, lang="ru", **kw):
            p = dict({"missing": missing, "available": True, "install": missing, "upgrade": [], "kib": 400,
                      "why": None}, **kw)
            return pkgs.question(p, lang)["text"]
        # REV З-4: the text by what is missing — iperf3 alone is never «the tests of the processor …»
        self.assertEqual(q(["iperf3"]), "Для замера отдачи до точек провайдеров нужна программа iperf3. Установить её?")
        self.assertEqual(q(["iperf3"], "en"),
                         "The upload test to the points of the providers needs iperf3. Install it?")
        self.assertEqual(q(["sysbench", "iperf3"]), "Для тестов процессора, памяти и диска нужны программы sysbench, "
                         "для замера отдачи до точек провайдеров — iperf3. Установить их?")
        self.assertTrue(q(["sysbench", "fio"], "en").startswith("The CPU, memory and disk tests need"))
        self.assertTrue(q(["sysbench", "iperf3"], "en").startswith("The CPU, memory and disk tests need"))
        self.assertIn("без замера отдачи до точек провайдеров", q(["iperf3"], available=False))
        self.assertIn("без замера отдачи до точек провайдеров", q(["iperf3"], available=False, why="no_lists"))
        self.assertIn("без тестов процессора", q(["fio", "iperf3"], available=False))
        with self.assertRaises(KaskadError):
            pkgs.install(r, ["iperf"], "panel")

    def test_the_question_and_the_install_know_iperf3(self):
        """REV З-4: ``plan --iperf`` asks of iperf3 too — the same set ``start --install --iperf`` installs; SEC П-5:
        the unit of the iperf3 server is masked."""
        r = self.runner(have=self.TOOLS)
        with mock.patch.object(pkgs, "_sim", return_value=mock.Mock(ok=True, out="Inst iperf3 (3.16-1 Debian)\n")):
            self.assertEqual(pkgs.plan(r)["missing"], [])
            self.assertEqual(pkgs.plan(r, iperf=True)["missing"], ["iperf3"])
        with mock.patch.object(pkgs, "plan", return_value={"missing": [], "available": True, "install": [],
                                                            "upgrade": [], "kib": 0, "why": None}) as pl, \
                mock.patch.object(M.sysrun, "default_runner", return_value=r), \
                mock.patch.object(M.sys, "stdout", io.StringIO()):
            M.main(["plan", "--iperf"])
        self.assertTrue(pl.call_args.kwargs["iperf"])
        from kaskad.gw import sys as gsys
        with mock.patch.object(gsys, "apt_install", return_value=["iperf3"]) as ai:
            pkgs.install(r, ["iperf3"], "panel")
            self.assertEqual(ai.call_args.kwargs["mask"], ("iperf3.service",))
            pkgs.install(r, ["fio"], "panel")
            self.assertEqual(ai.call_args.kwargs["mask"], ())


class WorkerChannelTest(RunCase):
    def work(self, by="cli", net=None, **kw):
        self.meter = FakeMeter()
        with mock.patch.object(channel, "Net", lambda: net or FakeNet()):
            return run.worker(RID, "quick", by, runner=self.runner(have=self.TOOLS + ("iperf3",)),
                              meter=self.meter, **kw)

    def test_rows_go_through_the_queue_contract(self):
        doc = self.work(net=FakeNet(budget=True))
        rows = dict((t["test"], t) for t in doc["tests"])
        self.assertEqual(doc["run"]["status"], "ok")
        self.assertEqual((rows["net_down"]["value"], rows["net_down"]["unit"]), (640.0, "Mbit/s"))
        self.assertEqual(rows["net_down"]["extra"], {"budget": True, "target": "cloudflare"})
        self.assertEqual(rows["net_up"]["ca"], "bbr")
        self.assertIsNotNone(rows["net_down"]["people"])                    # the counters of the meter, NS-S16
        self.assertIsNone(rows["net_down"]["value_corr"])                    # no history — no L
        self.assertEqual(self.meter.family, 4)                              # OWN_SLACK by the family of the test
        run_row, tests = spool.rows(json.loads(json.dumps(doc)))            # B12 takes the whole file
        got = dict((t["test"], t) for t in tests)
        self.assertEqual(json.loads(got["net_down"]["extra"]), {"budget": True, "target": "cloudflare"})
        self.assertEqual(got["load_rtt"]["ca"], "bbr")
        self.assertEqual(got["net_up"]["people"], rows["net_up"]["people"])

    def test_8c_over_a_short_window_goes_to_the_history(self):
        """Н-1 (3.3.7.13): the upload hit the cap early and 8c has a short window — ``extra.win`` of both rows goes
        through the queue contract (``spool.rows``) to the history (``history._test``: ``win``, the hint of the panel);
        a bad ``win`` makes the file bad, as every known field."""
        from kaskad.bench import history

        class Short(FakeNet):
            def _try(self, key, how, target, cap, probe):
                r = FakeNet._try(self, key, how, target, cap, probe)
                if key == "up":
                    r.update(lower=True, budget=True, win=1.42)
                return r
        doc = self.work(net=Short())
        rows = dict((t["test"], t) for t in doc["tests"])
        self.assertEqual((rows["load_retr"]["extra"], rows["load_rtt"]["extra"]), ({"win": 1.42}, {"win": 1.42}))
        self.assertIsNotNone(rows["load_retr"]["value"])               # без правки: «cap_early», без значения
        _run, tests = spool.rows(json.loads(json.dumps(doc)))
        got = dict((t["test"], t) for t in tests)
        self.assertEqual(json.loads(got["load_rtt"]["extra"]), {"win": 1.42})
        self.assertEqual(history._test(got["load_retr"])["win"], 1.42)
        self.assertNotIn("win", history._test(got["net_up"]))
        self.assertNotIn("win", history._test(dict(got["load_retr"], extra=json.dumps({"win": "1.4"}))))
        for bad in ("1.4", -1, 61, True):
            with self.assertRaises(ValueError):
                spool.extra({"win": bad})
        self.assertEqual(spool.extra({"win": 1.42, "budget": True}), {"win": 1.42, "budget": True})

    def test_skip_up_by_the_extension_point(self):
        doc = self.work(by="auto", retry_of="0123456789", skip=("8b", "up"))
        rows = dict((t["test"], t) for t in doc["tests"])
        self.assertEqual([rows[t]["why"] for t in ("net_up", "load_retr", "load_rtt")], ["excluded"] * 3)
        self.assertEqual(rows["net_down"]["ok"], 1)

    def test_8b_by_the_button_of_a_node_in_russia(self):
        st = self.set_state()
        st["gateway"]["hostmon"]["bench"] = {"ru": "yes"}
        state.save(st)
        net = FakeNet()
        doc = self.work(by="panel", net=net, iperf=True)
        rows = dict((t["test"], t) for t in doc["tests"])
        self.assertEqual([c[:2] for c in net.calls], [("down", "selectel"), ("iperf", "hostkey-msk")])
        self.assertEqual(rows["net_up"]["value"], 410.0)
        self.assertEqual(rows["load_retr"]["why"], "no_tcp_info")      # 8c over 8b: the fake net has no sampler
        # the same node by the schedule: no 8b, no upload
        net = FakeNet()
        doc = self.work(by="schedule", net=net, sched_id="a1")
        rows = dict((t["test"], t) for t in doc["tests"])
        self.assertEqual(rows["net_up"]["why"], "no_upload_ru")
        self.assertEqual([c[0] for c in net.calls], ["down"])

    def test_unknown_country_by_who_started(self):
        """REV З-3 = SEC З-5: the country unknown — measured as in Russia (no upload); П3-а after B9: the button the
        same way (the worker still tells the block who started it — it no longer changes the country)."""
        with mock.patch.object(suite, "node_ru", return_value=None):
            net = FakeNet()
            doc = self.work(by="schedule", net=net, sched_id="a1")
            rows = dict((t["test"], t) for t in doc["tests"])
            self.assertEqual([c[:2] for c in net.calls], [("down", "selectel")])
            self.assertEqual(rows["net_up"]["why"], "no_upload_ru")
            net = FakeNet()
            doc = self.work(by="panel", net=net)
            rows = dict((t["test"], t) for t in doc["tests"])
            self.assertEqual([c[:2] for c in net.calls], [("down", "selectel")])
            self.assertEqual(rows["net_up"]["why"], "no_upload_ru")

    def test_people_of_the_same_side(self):
        """REV З-5 (M4): the download takes the received traffic of others, the upload the sent one."""
        from tests.bench.benchcase import sample
        a = sample(10.0, unit={"rx": 0, "tx": 0, "rxp": 0, "txp": 0, "cpu_ns": 0})
        b = sample(20.0, unit={"rx": 0, "tx": 0, "rxp": 0, "txp": 0, "cpu_ns": 0})
        a["ifs"]["eth0"], b["ifs"]["eth0"] = (0, 0, 0, 0), (125 * 10 ** 6, 0, 25 * 10 ** 6, 0)
        self.assertAlmostEqual(suite._people(a, b, "down"), 100.0)
        self.assertAlmostEqual(suite._people(a, b, "up"), 20.0)

    def test_texts_of_the_reasons(self):
        for code in ("no_target", "no_upload_ru", "excluded", "no_iperf3", "points_busy", "no_tcp_info", "no_upload",
                     "cap_early", "reconnect", "upload_off"):
            for lang in ("ru", "en"):
                self.assertNotEqual(run.why_text(code, None, lang), code, (code, lang))
        from kaskad.i18n import tr
        self.assertEqual(tr("bench_run.load.line", "en", retr="0.4", rtt=8),
                         "Under load: 0.4% of data sent again, delay up by 8 ms")
        # REV С-1 = SEC З-4: the points one by one — the chosen one, and the next one when it does not answer
        self.assertIn("выбранная точка, а если она недоступна — следующая по списку, увидит адрес этого сервера",
                      run.iperf_consent("ru"))
        self.assertIn("the chosen point, and when it does not answer the next one of the list, will see the address "
                      "of this server", run.iperf_consent("en"))
        self.assertEqual(tr("bench_run.why.no_upload", "ru"),
                         "Не измерено: строка считается по замеру отдачи, а его не было")
        # D-144: turned off by the person — the reason in words, not «не измерено»
        self.assertTrue(run.why_text("upload_off", None, "ru").startswith("Выключено при запуске проверки"))
        self.assertTrue(run.why_text("upload_off", None, "en").startswith("Turned off at the start of the check"))


class NodeRuTest(RunCase):
    def test_the_order_of_the_sources(self):
        st = {"gateway": {"hostmon": {"bench": {"ru": "no"}}}}
        self.assertTrue(suite.node_ru(st, last_pass={"direct_loc": "RU"}))      # the geo pass first
        self.assertFalse(suite.node_ru({}, last_pass={"direct_loc": "de"}))
        self.assertFalse(suite.node_ru(st, last_pass={}))                       # then the setting
        st["gateway"]["hostmon"]["bench"]["ru"] = "yes"
        self.assertTrue(suite.node_ru(st, last_pass={"direct_loc": "bad!"}))
        series = {"anchors": [{"addr": "1.1.1.1", "rtt_med": 40.0}, {"addr": "77.88.8.8", "rtt_med": 3.0}]}
        self.assertTrue(suite.node_ru({}, series, last_pass={}))                 # then the delays of the series
        asked = []
        ms = lambda h, p: asked.append((h, p)) or (5.0 if h == "1.1.1.1" else 30.0)   # noqa: E731
        self.assertFalse(suite.node_ru({}, None, last_pass={}, ms=ms))           # else 3 TCP connections each
        self.assertEqual(asked, [("77.88.8.8", 53)] * 3 + [("1.1.1.1", 53)] * 3)
        self.assertIsNone(suite.node_ru({}, None, last_pass={}, ms=lambda h, p: None))
        # SEC З-5: never by one connection, never without a margin
        vals = {"77.88.8.8": iter([None, None, 2.0]), "1.1.1.1": iter([40.0, 41.0, 39.0])}
        self.assertIsNone(suite.node_ru({}, None, last_pass={}, ms=lambda h, p: next(vals[h])))   # 1 answer of 3
        vals = {"77.88.8.8": iter([9.0, 2.0, None]), "1.1.1.1": iter([40.0, 41.0, 39.0])}
        self.assertTrue(suite.node_ru({}, None, last_pass={}, ms=lambda h, p: next(vals[h])))     # the least: 2
        self.assertIsNone(suite.node_ru({}, None, last_pass={}, ms=lambda h, p: 1.3 if h == "1.1.1.1" else 1.2))

    def test_the_geo_pass_of_the_exits(self):
        from kaskad.gw.probe import run as prun
        os.makedirs(os.path.dirname(prun.state_path()), exist_ok=True)
        doc = prun.read_state()
        doc["last_pass"] = {"direct_loc": "RU", "locs": {}, "at": 1}
        with open(prun.state_path(), "w") as f:
            json.dump(doc, f)
        self.assertTrue(suite.node_ru({}, None))

    def test_the_allowed_point(self):
        st = {"gateway": {"hostmon": {"bench": {"ru_point": {"url": "https://up.example.com/u", "full": 500}}}}}
        self.assertEqual(suite.ru_point(st, "full"), ("https://up.example.com/u", 500))
        self.assertEqual(suite.ru_point(st, "quick"), ("https://up.example.com/u", None))
        self.assertEqual(suite.ru_point({}, "full"), (None, None))


# ── REV-ns-b1-3 ─────────────────────────────────────────────────────────────

class InventoryLimitsTest(RunCase):
    """З-1: one field out of the limits of the queue made the whole file of the check bad (B12 -> bad/)."""

    def inv(self, lscpu=None, lsblk=None, osr=None, nics=0):
        if osr:
            self.write("/etc/os-release", osr)
        if nics:
            route = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
            for i in range(nics):
                route += "eth%d\t00000000\t0101A8C0\t0003\t0\t0\t%d\t00000000\t0\t0\t0\n" % (i, i)
                self.write("/sys/class/net/eth%d/speed" % i, "1000\n")
            self.write("/proc/net/route", route)
        r = self.runner()
        if lscpu is not None:
            r._handlers.insert(0, (["lscpu"], lscpu))
        if lsblk is not None:
            r._handlers.insert(0, (["lsblk"], lsblk))
        c = suite.Ctx(r, "quick", self.path("/tmp/w"), suite._mono() + 100, st=self.set_state())
        suite.inventory(c)
        return c.inv

    def test_five_cases_of_the_review(self):
        disks = "".join("vd%s 1 %d disk\n" % (chr(97 + i % 26) * (1 + i // 26), 10 ** 10) for i in range(65))
        inv = self.inv(lsblk=disks)
        self.assertEqual(len(inv["disks"]), 64)
        inv = self.inv(nics=33)
        self.assertEqual(len(inv["nic"]), 32)
        ls = fx("lscpu-debian12.txt").replace("Thread(s) per core:                 1",
                                               "Thread(s) per core:                 0")
        self.assertIn("Thread(s) per core:                 0", ls)
        self.assertIsNone(self.inv(lscpu=ls)["smt"])
        self.write("/proc/cpuinfo", fx("cpuinfo-x86.txt").replace("model name\t: ", "model name\t: AMD­", 1))
        inv = self.inv(osr='ID=%s\nVERSION_ID="12"\n' % ("d" * 70))
        self.assertNotIn("­", inv["cpu"])
        self.assertTrue(inv["cpu"].isprintable())
        self.assertEqual(len(inv["os"]["id"]), 64)
        self.assertIsNotNone(spool.inv(json.loads(json.dumps(inv))))          # the reader of B12 takes it

    def test_the_whole_inventory_within_4_kb(self):
        """REV П-3: every field within its limit, but all of them at their limits — above MAX_INV (4096) before; the
        last disks, then interfaces, then flags go until it fits (the reader of B12 takes it)."""
        inv = {"cpu": "C" * 128, "flags": ["f%02d" % i for i in range(200)], "nproc": 4096, "smt": 2,
               "mem_gb": 1024.5, "disks": [{"rota": 1, "size": 10 ** 15 + i} for i in range(64)],
               "nic": dict(("eth-%026d" % i, 100000.0) for i in range(32)), "virt": "kvm", "kernel": "k" * 64,
               "os": {"id": "d" * 64, "version_id": "1" * 64}, "engines": ["xray", "awg"], "openssl_ver": "o" * 64}
        self.assertGreater(suite._inv_len(inv), spool.MAX_INV)
        with self.assertRaises(ValueError):
            spool.inv(json.loads(json.dumps(inv)))
        suite._fit_inv(inv)
        self.assertLessEqual(suite._inv_len(inv), spool.MAX_INV)
        self.assertIsNotNone(spool.inv(json.loads(json.dumps(inv))))
        self.assertTrue(inv["disks"] and inv["nic"])
        self.assertEqual(len(inv["flags"]), 200)                              # the disks and interfaces went first
        inv["flags"] = ["f%03d" % i for i in range(600)]
        suite._fit_inv(inv)
        self.assertLessEqual(len(inv["flags"]), suite.INV_FLAGS)              # the limit of the field (512)
        self.assertIsNotNone(spool.inv(json.loads(json.dumps(inv))))


class DnsReviewTest(RunCase):
    """П-1: the id of the answer and the bit QR, each with a case that tells them apart."""

    def ask(self, flags_of):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))

        def serve():
            try:
                q, peer = s.recvfrom(512)
            except OSError:
                return
            qid = struct.unpack("!H", q[:2])[0]
            for xid, flags in flags_of(qid):
                s.sendto(struct.pack("!HHHHHH", xid, flags, 1, 0, 0, 0) + q[12:], peer)
        th = threading.Thread(target=serve, daemon=True)
        th.start()
        try:
            return DNS_ONCE("127.0.0.1", "abc.example.com", 0.5, s.getsockname()[1])
        finally:
            s.close()

    def test_a_noise_of_another_id_never_answers(self):
        # another id with NOERROR, ours REFUSED — the query failed (an answer taken without the id would be 0)
        self.assertIsNone(self.ask(lambda q: [(q ^ 1, 0x8180), (q, 0x8185)]))
        self.assertIsNone(self.ask(lambda q: [(q ^ 1, 0x8180)]))               # only the noise
        self.assertIsNotNone(self.ask(lambda q: [(q ^ 1, 0x8185), (q, 0x8183)]))

    def test_a_reflected_query_is_not_an_answer(self):
        self.assertIsNone(self.ask(lambda q: [(q, 0x0100)]))                   # QR = 0, NOERROR
        self.assertIsNotNone(self.ask(lambda q: [(q, 0x0100), (q, 0x8180)]))


DNS_ONCE = suite.dns_once                   # the real one (BenchCase replaces it in setUp)


class AptAndMarksReviewTest(RunCase):
    def test_partial_lists_after_a_failed_update(self):
        # П-1: the simulation fails on empty lists; «apt-get update» fails, yet some lists came — simulate again
        calls = []

        def sim(argv, _input):
            calls.append("sim")
            return (100, "", "E: Unable to locate package sysbench") if len(calls) == 1 else \
                (0, fx("real/debian12/12-apt-sim.out"), "")

        def update(argv, _input):
            calls.append("update")
            self.write("/var/lib/apt/lists/deb.debian.org_debian_dists_bookworm_main_binary-amd64_Packages", "x")
            return 100, "", "E: Failed to fetch one of the mirrors"
        r = self.runner(have=(), extra=((["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-s"], sim),
                                        (["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "update"], update),
                                        (["apt-cache", "show"], fx("real/debian12/13-apt-cache-show.out"))))
        p = pkgs.plan(r, update=True, by="panel")
        self.assertEqual(calls, ["sim", "update", "sim"])
        self.assertTrue(p["available"])
        self.assertEqual(len(p["install"]), 27)

    def test_oserror_of_the_state_is_a_line_of_the_journal(self):
        lines = []
        log = lambda key, **kw: lines.append((key, kw))                   # noqa: E731
        with mock.patch.object(run.util, "log_tr", side_effect=log), \
                mock.patch.object(state, "save", side_effect=OSError("read-only")), \
                mock.patch.object(run, "new_id", return_value=RID):
            run.start("quick", "panel", runner=self.runner())
        self.assertIn(("bench_run.log.mark_failed", {"id": RID, "why": "OSError"}), lines)
        # П-4: the mark of the first manual check at the end — its own text («the result is saved»)
        lines[:] = []
        with mock.patch.object(run.util, "log_tr", side_effect=log), \
                mock.patch.object(state, "save", side_effect=OSError("read-only")), \
                mock.patch.object(channel, "Net", FakeNet):
            run.worker(RID, "quick", "panel", runner=self.runner(), meter=FakeMeter())
        self.assertIn(("bench_run.log.mark_failed_end", {"id": RID, "why": "OSError"}), lines)
        from kaskad.i18n import tr
        self.assertIn("результат проверки сохранён", tr("bench_run.log.mark_failed_end", "ru", id=RID, why="x"))
        self.assertNotIn("первой ручной", tr("bench_run.log.mark_failed", "ru", id=RID, why="x"))


class NumReviewTest(unittest.TestCase):
    def test_num(self):
        self.assertIsNone(parse._num(10 ** 400))
        self.assertIsNone(parse._num(True))
        self.assertIsNone(parse._num(False))
        self.assertEqual(parse._num("12.5"), 12.5)
        doc = parse.fio_json('{"jobs": [{"error": 0, "write": {"total_ios": 5, "iops": %s, "bw_bytes": %s}}]}'
                             % ("9" * 400, "9" * 400))
        self.assertIsNone(parse.fio_iops(doc, "write"))
        self.assertIsNone(parse.fio_bw_mbs(doc, "write"))
        self.assertIsNone(parse.fio_fsync_ms(doc))
        doc = parse.fio_json('{"jobs": [{"error": 0, "write": {"total_ios": 5, "iops": true}}]}')
        self.assertIsNone(parse.fio_iops(doc, "write"))
        self.assertIsNone(parse.fio_fsync_ms(doc))                    # was 1000 ms from «true»


if __name__ == "__main__":
    unittest.main()

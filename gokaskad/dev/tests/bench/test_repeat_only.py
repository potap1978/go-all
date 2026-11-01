"""The repeat and the schedule through the extension point of B3 (the join of B11 and B3, 19.10; SS §2.8 п. 2, NS-S5):
the repeat measures again only the tests of the links that fell (``only``), never 8b and 9, never an upload of a node
in Russia but to the allowed point, never more traffic than its original; the schedule never 8b and 9; a «full» check
by the schedule of a node in Russia uploads to the allowed point only."""

import itertools
from unittest import mock

from kaskad import score, state, sysrun
from kaskad.bench import channel, run, sched_run, sched_unit, suite
from kaskad.errors import KaskadError
from tests.bench.benchcase import FakeMeter, FakeNet
from tests.bench.test_run import RID, RunCase


def _tests_of(steps, mode):
    """The rows the steps can give, the channel by its sides (a skipped side gives «excluded» only)."""
    out = set()
    for name, _fn, _need, tests in steps:
        out.update(tests(mode))
    return out


def _link_tests():
    """{link: the tests the score counts it by} — from the catalogue of the score itself (``score.load_spec()``;
    crypto — both ciphers, whichever the node's engines use)."""
    spec = score.load_spec()
    groups = dict((g["id"], [t["id"] for t in g.get("tests", ())]) for g in spec["groups"])
    out = {}
    for b in spec["links"]:
        if b["id"] in score.TEST_LINKS:
            out[b["id"]] = set(t for g in b.get("groups", ()) for t in groups[g]) | set(b.get("tests", ()))
    return out


class OnlyPlanTest(RunCase):
    def test_the_links_are_those_of_the_score(self):
        self.assertEqual(suite.LINKS, score.TEST_LINKS)
        self.assertEqual(set(suite.LINK_STEPS), set(score.TEST_LINKS))
        self.assertEqual(set(suite.LINK_SIDES), set(score.TEST_LINKS))

    def test_each_link_gets_all_its_tests_and_no_step_more(self):
        """SS §2.8 п. 2: ``down``/``up`` — test 8 of that side, ``net_bw`` — both, ``crypto`` — test 5, ``cpu`` —
        tests 3A, 4, 5; besides them the inventory alone (no traffic)."""
        side_of = {"net_down": "down", "net_up": "up"}
        self.assertEqual(set(_link_tests()), set(score.TEST_LINKS))      # every link of the tests is checked
        for link, need in _link_tests().items():
            for mode in run.MODES:
                st = suite.steps(mode, only=(link,))
                names = [s[0] for s in st]
                self.assertLessEqual(need, _tests_of(st, mode), (link, mode))
                sides = set(side_of[t] for t in need if t in side_of)
                self.assertEqual(set(suite.only_plan((link,))[1]), sides, link)
                for name, _fn, _need, tests in st:
                    if name in suite.ONLY_ALWAYS:
                        continue
                    self.assertTrue(set(tests(mode)) & need, (link, name))   # every step serves the link
                self.assertNotIn("cpu_b", names, link)
                self.assertNotIn("disk", names, link)

    def test_a_repeat_is_never_more_than_its_original(self):
        """Its steps ⊆ the steps of the original (the schedule: no 8b, 9), the sides of the channel ⊆, the same mode
        — the same caps of a side: the traffic of a repeat is never more than the one of its original."""
        for mode in run.MODES:
            orig = [s[0] for s in suite.steps(mode, skip=run.AUTO_SKIP)]
            for n in range(1, len(suite.LINKS) + 1):
                for links in itertools.combinations(suite.LINKS, n):
                    names = [s[0] for s in suite.steps(mode, only=links)]
                    self.assertLessEqual(set(names), set(orig), links)
                    self.assertEqual(names, [x for x in orig if x in names], links)     # the same order

    def test_the_sides_of_the_channel_of_a_repeat(self):
        """REV-ns-join З-2 (M1): in all 31 sets of links the channel gets exactly the sides they need (``down`` —
        the download, ``up`` — the upload, ``net_bw`` — both), the other side in its ``skip``, 8b and 9 always in it,
        never ``iperf``; no side — no channel step. A repeat of the download alone never measures the upload."""
        seen = []
        real = channel.steps

        def spy(mode, skip=(), iperf=False):
            seen.append((frozenset(skip), iperf))
            return real(mode, skip=skip, iperf=iperf)
        with mock.patch.object(channel, "steps", side_effect=spy):
            for mode in run.MODES:
                for n in range(1, len(suite.LINKS) + 1):
                    for links in itertools.combinations(suite.LINKS, n):
                        want = set()
                        for x in links:
                            want.update({"down": {"down"}, "up": {"up"}, "net_bw": {"down", "up"}}.get(x, ()))
                        del seen[:]
                        suite.steps(mode, skip=run.AUTO_SKIP, only=links)
                        if not want:
                            self.assertEqual(seen, [], (mode, links))
                            continue
                        self.assertEqual(len(seen), 1, (mode, links))
                        skip, iperf = seen[0]
                        self.assertFalse(iperf, links)
                        self.assertLessEqual({"8b", "9"}, skip, links)
                        self.assertEqual({"down", "up"} - skip, want, (mode, links))

    def test_a_bad_link_is_refused(self):
        with self.assertRaises(ValueError):
            suite.only_plan(("up", "disk"))
        with self.assertRaises(KaskadError):
            run.command(RID, "quick", "auto", retry_of="abcdefabcd", only=("disk",))


class RepeatWorkerTest(RunCase):
    def work(self, by="auto", net=None, mode="quick", **kw):
        self.meter = FakeMeter()
        if by == "auto":
            kw.setdefault("retry_of", "0123456789")
        with mock.patch.object(channel, "Net", lambda: net or FakeNet()):
            return run.worker(RID, mode, by, runner=self.runner(have=self.TOOLS + ("iperf3",)), meter=self.meter,
                              **kw)

    def measured(self, doc):
        return sorted(t["test"] for t in doc["tests"] if t.get("why") != "excluded")

    def test_the_repeat_of_the_upload_measures_the_upload_alone(self):
        st = self.set_state()
        st["gateway"]["hostmon"]["bench"] = {"ru": "no"}
        state.save(st)
        net = FakeNet()
        doc = self.work(net=net, only=("up",))
        self.assertEqual([c[0] for c in net.calls], ["up"])
        rows = dict((t["test"], t) for t in doc["tests"])
        self.assertEqual(rows["net_down"]["why"], "excluded")
        self.assertEqual(rows["net_up"]["value"], 320.0)
        self.assertEqual(self.measured(doc), ["load_retr", "load_rtt", "mem_gb", "net_up"])

    def test_the_repeat_of_the_processor(self):
        net = FakeNet()
        doc = self.work(net=net, only=("cpu",))
        self.assertEqual(net.calls, [])                                     # no traffic
        self.assertEqual(self.measured(doc), ["aes_1t", "chacha_1t", "cpu_1t", "cpu_mt", "mem_gb"])

    def test_the_repeat_of_the_channel_both_sides_never_8b(self):
        st = self.set_state()
        st["gateway"]["hostmon"]["bench"] = {"ru": "yes"}
        state.save(st)
        net = FakeNet()
        doc = self.work(net=net, only=("net_bw",), mode="full")
        rows = dict((t["test"], t) for t in doc["tests"])
        self.assertEqual([c[:2] for c in net.calls], [("down", "selectel")])   # no upload, no iperf3
        self.assertEqual(rows["net_up"]["why"], "no_upload_ru")

    def test_a_full_check_by_the_schedule_of_a_node_in_russia(self):
        """NS-S5: tests 0–8 of the «full», no 8b and 9; the upload — to the allowed point only."""
        st = self.set_state()
        st["gateway"]["hostmon"]["bench"] = {"ru": "yes"}
        state.save(st)
        net = FakeNet()
        doc = self.work(by="schedule", sched_id="a1", net=net, mode="full")
        self.assertEqual([c[0] for c in net.calls], ["down"])
        self.assertIn("disk_rr", self.measured(doc))                      # the whole check otherwise
        st = state.load()
        st["gateway"]["hostmon"]["bench"]["ru_point"] = {"url": "https://up.example.com/u", "full": 500}
        state.save(st)
        net = FakeNet()
        self.work(by="schedule", sched_id="a1", net=net, mode="full")
        self.assertEqual([c[:2] for c in net.calls], [("down", "selectel"), ("up", "allowed")])

    def test_8b_never_by_the_schedule_or_the_repeat_even_asked(self):
        for by, kw in (("schedule", {"sched_id": "a1"}), ("auto", {"only": ("up",)})):
            with self.assertRaises(KaskadError):
                self.work(by=by, iperf=True, **kw)
        argv = run.command(RID, "full", "auto", retry_of="abcdefabcd", only=("up",))
        self.assertIn("8b,9", argv[argv.index("--skip") + 1])


class RepeatPlanTest(RunCase):
    """The planner keeps the links that fell; the timer carries them; the repeat passes them to ``run.start``."""

    def test_the_timer_carries_the_links(self):
        r = sysrun.FakeRunner()
        r.on(["systemd-run"], "")
        sched_unit.schedule_retry(r, "0123456789", s=3600, links=["up", "cpu", "up"])
        argv = r.commands(["systemd-run"])[0]
        self.assertEqual(argv[-4:], ["--retry", "0123456789", "--links", "cpu,up"])
        # REV-ns-join З-1: a timer without its links is never set (the repeat would refuse it)
        for bad in (["up --x"], [], None, "up", ["up", "UP"]):
            with self.assertRaises(KaskadError, msg=bad):
                sched_unit.schedule_retry(r, "0123456789", s=3600, links=bad)
        self.assertEqual(len(r.commands(["systemd-run"])), 1)

    def test_the_repeat_starts_with_its_links_alone(self):
        got = []

        def start(mode, by="cli", **kw):
            got.append((mode, by, kw.get("only"), tuple(kw.get("skip") or ())))
            return {"id": "0000000001"}
        with mock.patch.object(sched_run, "_retry_still", return_value=None), \
                mock.patch.object(sched_run, "_gate", return_value=None), \
                mock.patch.object(sched_run, "_source", return_value={"mode": "full"}), \
                mock.patch.object(run, "start", side_effect=start):
            out = sched_run.retry("0123456789", sysrun.FakeRunner(), links=["up", "cpu", "up"])
        self.assertEqual(out, {"id": "0000000001"})
        self.assertEqual(got, [("full", "auto", ["cpu", "up"], ("8b", "9"))])

    def test_broken_links_refuse_the_repeat(self):
        """REV-ns-join З-1: ``--links`` with a word that is not a link, or without links at all — no repeat (a row
        ``skipped`` with ``retry_links`` and a line in the log), never «all the links»."""
        from kaskad import util
        for bad in (["foo"], ["UP"], [], None, ["up", "bad"], ["up", "net_bw --x"]):
            started, logged = [], []
            with mock.patch.object(sched_run, "_retry_still", return_value=None), \
                    mock.patch.object(sched_run, "_gate", return_value=None), \
                    mock.patch.object(sched_run, "_source", return_value={"mode": "full"}), \
                    mock.patch.object(util, "log_tr", side_effect=lambda key, **kw: logged.append((key, kw))), \
                    mock.patch.object(run, "start", side_effect=lambda *a, **kw: started.append(kw)):
                out = sched_run.retry("0123456789", sysrun.FakeRunner(), links=bad)
            self.assertEqual(out, {"skip": "retry_links"}, bad)
            self.assertEqual(started, [], bad)
            self.assertIn(("bench.log.retry_bad_links", {"id": "0123456789"}), logged, bad)
        from kaskad.bench import spool
        rows = [spool.load_run(path)[0] for path, _id in spool.scan()[0]]
        rows = [r for r in rows if r["status"] == "skipped"]
        self.assertEqual(len(rows), 6)
        self.assertEqual(set((r["by"], r["retry_of"], r["why"]) for r in rows),
                         {("auto", "0123456789", "retry_links")})
        self.assertIn("retry_links", sched_run.NO_START)                 # not one of the 2 repeats a day

    def test_the_cli_passes_the_links(self):
        from kaskad import cli
        seen = []
        with mock.patch.object(sched_run, "retry", side_effect=lambda rid, links=None: seen.append((rid, links)) or {}):
            cli.main(["host", "bench", "--retry", "0123456789", "--links", "down,net_bw"])
            cli.main(["host", "bench", "--retry", "0123456789"])
        self.assertEqual(seen, [("0123456789", ["down", "net_bw"]), ("0123456789", [])])

    def test_a_node_in_russia_without_a_point_never_repeats_its_upload(self):
        st = {"gateway": {"hostmon": {"bench": {"ru": "yes"}}}}
        self.assertEqual(suite.repeat_links(["up", "net_bw", "cpu"], st), ["net_bw", "cpu"])
        st["gateway"]["hostmon"]["bench"]["ru_point"] = {"url": "https://up.example.com/u"}
        self.assertEqual(suite.repeat_links(["up"], st), ["up"])
        self.assertEqual(suite.repeat_links(["up"], {}), ["up"])                         # unknown: the check decides
        self.assertEqual(suite.repeat_links(["up"], {"gateway": {"hostmon": {"bench": {"ru": "no"}}}}), ["up"])

    def test_the_original_plans_only_what_the_repeat_can_measure(self):
        st = self.set_state()
        st["gateway"]["hostmon"]["bench"] = {"ru": "yes"}
        state.save(st)
        doc = {"run": {"id": RID, "status": "ok", "by": "panel", "end_ts": 1760000000}}
        planned = []
        with mock.patch.object(run, "drops_of", return_value=["up"]), \
                mock.patch.object(sched_run, "plan_retry", side_effect=lambda *a, **k: planned.append(a[2]) or {}):
            with mock.patch.object(run.util, "log_tr") as lt:
                self.assertIsNone(run.retry_after(doc, sysrun.FakeRunner()))
            self.assertEqual(planned, [])
            self.assertEqual([c.args[0] for c in lt.call_args_list], ["bench_run.log.retry_cannot"])
            with mock.patch.object(run, "drops_of", return_value=["up", "crypto"]):
                run.retry_after(doc, sysrun.FakeRunner())
        self.assertEqual(planned, [["crypto"]])

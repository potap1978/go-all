"""B11 круг 2: the events bench_drop of a check go into its queue file (SS §3.2, §2.8 п. 6; REV-ns-b11 table п. 1) —
worked out by the runner in ONE replay with the links of its repeat, after the record is on the disk and the lock of
the speed tests is let go (REV З-7)."""

from unittest import mock

from kaskad import score as S, sysrun
from kaskad.bench import history, run, spool
from tests.bench.test_sched_run import HERE, Case, ent
from tests.score_hist import ABS, S4, Hist


def head(r, end=300):
    h = dict((k, r[k]) for k in ("id", "ts", "mode", "by", "status") if r.get(k) is not None)
    if r.get("retry_of"):
        h["retry_of"] = r["retry_of"]
    h["end_ts"] = r["ts"] + end
    return h


class DropsTest(Case):
    def setUp(self):
        super().setUp()
        self.save([ent("03:45", [3], "aaaaaa")])
        self.runner = sysrun.FakeRunner()
        self.runner.on(["systemd-run"], "")
        h = Hist()
        d = h.usual(S4, n=6)                                          # «обычно» of rel (NS-D30)
        h.add(d, dict(S4, net_up=5), by="panel")                     # S4: upload 1300 -> 5
        h.add(d, dict(S4, net_up=5), hh=6, retry=True)                # the repeat confirms
        self.h = h
        self.n = 6
        for r in h.runs[:self.n]:
            spool.put(spool.check_doc(head(r), r["vals"]))

    def check(self, r):
        doc = spool.check_doc(head(r), r["vals"])
        spool.put(doc)                                                # _finish wrote it before retry_after
        with mock.patch("kaskad.bench.machine.mid", return_value=HERE):
            res = run.retry_after(doc, self.runner)
        return res, spool.load(spool.spool_dir() + "/%s.json" % r["id"])

    def test_the_original_sets_the_repeat_and_the_repeat_writes_the_event(self):
        orig, rep = self.h.runs[self.n], self.h.runs[self.n + 1]
        res, doc = self.check(orig)
        self.assertIn("set", res)
        self.assertEqual(len(self.runner.commands(["systemd-run"])), 1)
        want = [e for e in S.drop_events(ABS, self.h.runs[:self.n + 1])[0] if e["run"] == orig["id"]]
        self.assertEqual(doc.get("drops") or [], [dict((f, e[f]) for f in spool.DROP_KEYS if e.get(f) is not None)
                                                  for e in want])
        res, doc = self.check(rep)
        self.assertIsNone(res, "a repeat sets no repeat")
        self.assertEqual(len(self.runner.commands(["systemd-run"])), 1)
        got = [(d["link"], d["cond"], d["auto"], d.get("value"), d.get("now")) for d in doc["drops"]]
        self.assertIn(("up", "rel", True, 5, 0), got)                 # NS-D33: rel 0 (5 of 1300)
        rows = spool.drops(doc)                                       # the one writer takes them as they are
        self.assertTrue(rows and all(x["run"] == rep["id"] for x in rows))
        evs = history.sample(rep["ts"] + 400)["events"]
        self.assertIn(("up", rep["id"]), [(e["link"], e["run"]) for e in evs])

    def test_one_replay_gives_the_same_links(self):
        """``retry_wanted(_state=…)`` of the one replay = ``retry_wanted`` with its own replay."""
        runs = self.h.runs[:self.n + 1]
        pre = {"ts": runs[-1]["ts"]}
        S.drop_events(ABS, runs, _pre=pre)
        self.assertEqual(S.retry_wanted(ABS, runs, runs[-1], _state=pre["state"]),
                         S.retry_wanted(ABS, runs, runs[-1]))
        self.assertIn("up", S.retry_wanted(ABS, runs, runs[-1]))

    def test_a_skipped_check_works_out_nothing(self):
        with mock.patch.object(run, "drops_of") as d:
            self.assertIsNone(run.retry_after({"run": {"id": "0000000001", "status": "skipped"}}, self.runner))
        d.assert_not_called()

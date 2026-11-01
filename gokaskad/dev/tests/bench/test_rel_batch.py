"""UI-C (k3/scratchpad/uiC/API-CONTRACT.md §2–3): ``score.check_rel`` of many checks with ``score.rel_cx`` (the list of
the checks with ``rel``) gives exactly the answer of ``check_rel`` one by one — on random histories with gaps, failed
checks and tests, both modes of the channel, the load, automatic repeats (a repeat of a repeat, a repeat «before» its
original), new bases; ``tests`` of ``check_rel`` — «обычно», its base days and rel of every test of the links."""

import random
import unittest
from unittest import mock

from kaskad import score as S

DAY = 86400
T0 = 1790000000
BASE = {"aes_1t": 7000, "chacha_1t": 2400, "cpu_1t": 2700, "cpu_canary_cv": 1.5, "cpu_cv": 1.2, "cpu_mt": 10500,
        "cpu_sustain": 0.99, "dns": 5, "net_down": 1700, "net_up": 1300, "rtt": 2.5, "steal_p95": 0.1,
        "jitter": 0.3, "loss": 0.01}


def day_of(ts):
    return "%04d-%02d-%02d" % __import__("time").gmtime(ts)[:3]


def history(seed, n=160):
    rnd = random.Random(seed)
    runs, ts = [], T0
    for i in range(n):
        ts += rnd.choice((3600, 6 * 3600, DAY, DAY, 2 * DAY, 3 * DAY)) + (rnd.choice((40, 70)) * DAY
                                                                           if rnd.random() < 0.03 else 0)
        mode = rnd.choice(("full", "quick", "quick"))
        st = rnd.choice(("ok",) * 8 + ("partial", "failed", "skipped"))
        vals = {}
        for k, v in BASE.items():
            if rnd.random() < 0.1:
                continue                                  # not measured
            m = {"v": v * rnd.uniform(0.4, 1.4), "mode": mode}
            if rnd.random() < 0.05:
                m["ok"] = False
            if k in S.CHANNEL and rnd.random() < 0.3:
                m["corr"] = m["v"] * 1.1
            if k == "loss":
                m.update(n=8640.0, lo=m["v"] / 2)
            vals[k] = m
        vals["_engines"] = rnd.choice((["awg"], ["vless"], ["awg", "vless"]))
        vals["_cpu_flags"] = rnd.choice((["aes"], []))
        ul = rnd.choice((False, False, False, None, True))
        runs.append({"id": "%010x" % i, "ts": ts, "mode": mode, "by": "schedule", "retry_of": None, "status": st,
                     "under_load": ul, "vals": vals})
        if st in S.GOOD_STATUS and rnd.random() < 0.15:  # an automatic repeat of the channel
            rid = "a%09x" % i
            runs.append({"id": rid, "ts": ts + 900, "mode": mode, "by": "auto", "retry_of": "%010x" % i,
                         "status": "ok", "under_load": False,
                         "vals": {"net_down": {"v": 1700.0 * rnd.uniform(0.5, 1.2), "mode": mode}}})
            if rnd.random() < 0.3:                      # a repeat of the repeat (the runner never makes one)
                runs.append({"id": "b%09x" % i, "ts": ts + 1800, "mode": mode, "by": "auto", "retry_of": rid,
                             "status": "ok", "under_load": False, "vals": {"net_up": {"v": 1250.0, "mode": mode}}})
    # a repeat whose original is later than itself (broken clocks): at its moment it is no repeat
    late = runs[len(runs) // 2]
    runs.append({"id": "c000000001", "ts": late["ts"] - 2 * DAY, "mode": "quick", "by": "auto",
                 "retry_of": late["id"], "status": "ok", "under_load": False,
                 "vals": {"net_down": {"v": 900.0, "mode": "quick"}}})
    runs.sort(key=lambda r: (r["ts"], r["id"]))
    eps = [{"ts": runs[n // 3]["ts"] + 60, "kind": "cpu", "args": {}},
           {"ts": runs[2 * n // 3]["ts"] + 60, "kind": "openssl", "args": {}},
           {"ts": runs[-5]["ts"] + 60, "kind": "tariff", "args": {}}]
    return runs, eps


class RelBatchTest(unittest.TestCase):
    def same(self, seed, n=160):
        runs, eps = history(seed, n)
        spec = S.load_spec()
        cx = S.rel_cx(runs, day_of)
        one = [S.check_rel(spec, runs, r, day_of=day_of, epochs=eps) for r in runs]
        many = [S.check_rel(spec, runs, r, epochs=eps, _cx=cx) for r in runs]
        for r, a, b in zip(runs, one, many):
            self.assertEqual(a, b, (seed, r["id"]))
        return one

    def test_the_same_answer_as_one_by_one(self):
        got = []
        for seed in range(6):
            got += self.same(seed)
        self.assertGreater(sum(1 for x in got if x["rel_total"] is not None), 200)      # the histories have rel
        self.assertTrue(any(x["rel_total"] is not None and x["rel_total"] != 100 for x in got))

    def test_the_same_with_another_thinning(self):
        with mock.patch.object(S, "DEV_SPACING_DAYS", 2):
            self.same(11, 90)

    def test_repeats_out_of_order(self):
        """The cases of the shared points the random histories may miss: a repeat whose original is later than the
        check (not a repeat at it: its own day), a repeat earlier than its original (it goes to the original's day,
        later than its own) and a repeat of a repeat whose original falls before the base (the pair goes together)."""
        spec = S.load_spec()
        run = lambda i, day, v, **kw: dict({"id": i, "ts": T0 + day * DAY + 3600, "mode": "full", "by": "panel",
                                            "retry_of": None, "status": "ok", "under_load": False,
                                            "vals": {"net_down": {"v": float(v), "mode": "full"}}}, **kw)
        late = [run("0000000003", 3, 1700), run("0000000006", 6, 1800), run("0000000007", 7, 1700),
                run("0000000008", 8, 1700), run("0000000009", 9, 1700), run("0000000012", 12, 1700),
                run("a000000005", 5, 100, by="auto", retry_of="0000000012")]
        pair = [run("0000000000", 0, 1700), run("a000000065", 65, 5000, by="auto", retry_of="0000000000"),
                dict(run("b000000065", 65, 100, by="auto", retry_of="a000000065"), ts=T0 + 65 * DAY + 7200),
                run("0000000068", 68, 1700), run("0000000070", 70, 1800), run("0000000125", 125, 1700),
                run("0000000126", 126, 1700), run("0000000127", 127, 1700)]
        early = [run("a000000002", 2, 5000, by="auto", retry_of="0000000045"), run("0000000045", 45, 1700),
                 run("0000000050", 50, 1700), run("0000000055", 55, 1800), run("0000000100", 100, 1700),
                 run("0000000101", 101, 1700), run("0000000102", 102, 1700)]      # a repeat before its original
        for runs, at, n in ((late, "0000000009", 3), (pair, "0000000127", 3), (early, "0000000102", 3)):
            runs.sort(key=lambda r: (r["ts"], r["id"]))
            r = next(x for x in runs if x["id"] == at)
            one = S.check_rel(spec, runs, r, day_of=day_of)
            self.assertEqual(one["tests"]["net_down"]["usual_n"], n, at)
            self.assertEqual(S.check_rel(spec, runs, r, _cx=S.rel_cx(runs, day_of)), one, at)

    def test_tests_of_a_check(self):
        """``tests``: every test of the links once (a test of two links — the same numbers), ``usual`` — «обычно»
        of rel (None below REL_N_MIN base days), its base days, rel; a failed check — the rels None."""
        runs = []
        for i in range(8):
            runs.append({"id": "%010x" % i, "ts": T0 + i * DAY, "mode": "full", "by": "panel", "retry_of": None,
                         "status": "ok", "under_load": False,
                         "vals": dict((k, {"v": float(v) / (2 if i == 7 and k == "net_down" else 1), "mode": "full"})
                                      for k, v in BASE.items())})
        spec = S.load_spec()
        last = S.check_rel(spec, runs, runs[-1], day_of=day_of)
        self.assertEqual(set(last), {"rel_total", "links", "tests"})
        r6 = lambda x: dict(x, usual=round(x["usual"], 6))                           # exp(median(log))
        self.assertEqual(r6(last["tests"]["net_down"]), {"usual": 1700.0, "usual_n": 5, "rel": 50})
        self.assertEqual(r6(last["tests"]["net_up"]), {"usual": 1300.0, "usual_n": 5, "rel": 100})
        self.assertEqual(last["links"]["down"], 50)
        self.assertNotIn("net_cv", last["tests"])                                     # no «обычно» of its own
        tids = set(t for b in spec["links"] for t in S.link_tests(spec, b, ["awg", "vless"], ["aes"])[0])
        self.assertEqual(set(last["tests"]), tids - {"net_cv"})
        first = S.check_rel(spec, runs, runs[2], day_of=day_of)
        self.assertEqual(first["tests"]["net_down"], {"usual": None, "usual_n": 0, "rel": None})
        failed = dict(runs[-1], status="failed")
        out = S.check_rel(spec, runs[:-1] + [failed], failed, day_of=day_of)
        self.assertIsNone(out["rel_total"])
        self.assertTrue(all(x["rel"] is None for x in out["tests"].values()))
        self.assertEqual(out["tests"]["net_down"]["usual_n"], 4)     # the base before the 3 last good values


if __name__ == "__main__":
    unittest.main()

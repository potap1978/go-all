"""The score over the history is the score over the same values (круг 2, С-1; SS §3.1, §4; B4 score.py d2af76d5):
the same checks given straight to ``kaskad.score.view`` and carried runner → queue (``spool.check_doc`` + ``put``) →
base (``drain``) → ``history.view`` give the same answer, JSON for JSON — for every set of raw values of the samples
of k3/handoff/data/node-score-samples (tests/fixtures/score_samples.json: abs, fresh S4), the cases of the critic
(VLESS with a weak ChaCha, loss 0.5 % of 600 samples …), a history long enough for rel (NS-D30) and a history with an
automatic repeat and drops (NS-D33). The relative scale to the tariff (C2) and the anchors of the curves are gone."""

import json

from kaskad import score as S
from kaskad.bench import history, spool
from kaskad.gw import metrics
from tests.helpers import RootCase
from tests.score_hist import ABS, DAY, FIX, S4, T0, day_of, hhmm_of

NOW = T0 + 20 * DAY


WHY = {}


def coded(vals):
    """The samples of appendix C carry Russian reasons («ждёт истории»); a check carries codes (score.py: «reasons are
    codes»; bench.why — [a-z0-9_.]): the same value with its reason as a code."""
    out = {}
    for k, m in vals.items():
        if isinstance(m, dict) and isinstance(m.get("why"), str) and not spool._CODE.fullmatch(m["why"]):
            m = dict(m, why=WHY.setdefault(m["why"], "sample.why%d" % len(WHY)))
        out[k] = m
    return out


def run(i, vals, mode="full", ts=None, **kw):
    r = {"id": "%010x" % (i + 1), "ts": ts or (T0 + i * 3600), "mode": mode, "by": "panel", "status": "ok",
         "retry_of": None, "under_load": False, "vals": S.with_mode(coded(vals), mode)}
    r.update(kw)
    return r


def norm(x):
    """A value of the base is REAL: 45 and 45.0 are the same number (JSON of the API reads them alike)."""
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, dict):
        return dict((k, norm(v)) for k, v in x.items())
    if isinstance(x, (list, tuple)):
        return [norm(v) for v in x]
    return repr(x)


def dumps(x):
    return json.dumps(norm(x), sort_keys=True, ensure_ascii=False)


class Case(RootCase):
    def via(self, spec, runs, now, base, **kw):
        """runs -> the files of the runner (with the drops it worked out by score.drop_events) -> the queue (and the
        base when ``base``) -> history.view."""
        evs = S.drop_events(spec, runs, day_of=day_of)[0]
        for r in runs:
            head = dict((k, r[k]) for k in ("id", "ts", "mode", "by", "status") if r.get(k) is not None)
            if r.get("retry_of"):
                head["retry_of"] = r["retry_of"]
            if r.get("under_load") is not None:
                head["under_load"] = r["under_load"]
            spool.put(spool.check_doc(head, r["vals"], drops=[e for e in evs if e["run"] == r["id"]]))
        if base:
            w = metrics.open_writer()
            try:
                self.assertEqual(spool.drain(w, now, limit=10 ** 4)["bad"], 0)
            finally:
                w.close()
        return history.view(spec, now, day_of=day_of, hhmm_of=hhmm_of, **kw)

    def same(self, name, spec, runs, now=NOW, **kw):
        want = S.view(spec, runs, now, day_of=day_of, hhmm_of=hhmm_of, **kw)
        for base in (False, True):
            self.reset()
            got = self.via(spec, runs, now, base, **kw)
            self.assertEqual(dumps(got), dumps(want), "%s (%s)" % (name, "base" if base else "queue"))
        return want

    def reset(self):
        import shutil
        shutil.rmtree(spool.spool_dir(), ignore_errors=True)
        w = metrics.open_writer()
        try:
            with w.conn:
                for t in ("bench_run", "bench", "bench_drop", "bench_epoch"):
                    w.conn.execute("DELETE FROM %s" % t)
        finally:
            w.close()


class SamplesTest(Case):
    def test_every_sample_one_check(self):
        n = 0
        for i, c in enumerate(FIX["abs"]):
            self.same("abs %s" % c["name"], ABS, [run(i, c["vals"], c.get("mode") or "full")])
            n += 1
        self.assertEqual(n, 70)

    def test_fresh_of_three_checks(self):
        for c in FIX["fresh_s4"]:
            runs = [run(i, dict(S4, net_up=v), ts=T0 + i * DAY) for i, v in enumerate(c["net_up"])]
            got = self.same("fresh %s" % c["net_up"], ABS, runs, now=T0 + 3 * DAY)
            self.assertEqual(next(x for x in got["links"] if x["id"] == "up")["fresh_raw"], c["median"])

    def test_rel_through_the_history(self):
        """Eight days of S4, then three with half the download: rel of the links and rel_total through the base are
        those of score.view (NS-D30, NS-D31) — the numbers are there, not only nulls."""
        runs = [run(i, S4, ts=T0 + i * DAY + 4 * 3600) for i in range(8)]
        runs += [run(8 + i, dict(S4, net_down=S4["net_down"] / 2), ts=T0 + (8 + i) * DAY + 4 * 3600) for i in range(3)]
        got = self.same("rel", ABS, runs, now=T0 + 11 * DAY)
        self.assertEqual(next(x for x in got["links"] if x["id"] == "down")["rel"], 50)
        self.assertEqual(got["summary"]["rel_total"], 100)

    def test_the_cases_of_the_critic(self):
        """REV-ns-b12 С-1 (scripts/shape.py): on round 1 these went 844 → 579 and 910 → 850 through the history."""
        for name, vals in (("vless only, chacha 300", dict(S4, chacha_1t=300, _engines=["vless"])),
                           ("loss 0.5 n=600", dict(S4, loss={"v": 0.5, "n": 600, "lo": 0.08})),
                           ("jitter n=200 waits", dict(S4, jitter={"v": 0.25, "n": 200})),
                           ("no AES", dict(S4, _cpu_flags=[])),
                           ("vless with AES", dict(S4, _engines=["vless"], _cpu_flags=["aes"])),
                           ("canary waits", dict(S4, cpu_canary_cv={"v": None, "wait": True})),
                           ("a failed test", dict(S4, cpu_1t={"v": 900, "ok": False})),
                           ("dns of the system", dict(S4, dns={"v": 30, "src": "system"}))):
            got = self.same(name, ABS, [run(0, vals)], now=T0 + DAY)
            self.assertIsNotNone(got["summary"]["last_id"], name)
        a = S.view(ABS, [run(0, dict(S4, chacha_1t=300, _engines=["vless"]))], T0 + DAY, day_of=day_of)
        b = S.view(ABS, [run(0, dict(S4, chacha_1t=300))], T0 + DAY, day_of=day_of)
        crypto = [[(p["test"], p["fresh_raw"]) for p in next(x for x in v["links"] if x["id"] == "crypto")["parts"]]
                  for v in (a, b)]
        self.assertEqual(crypto, [[("aes_1t", 7000.0)], [("chacha_1t", 300.0), ("aes_1t", 7000.0)]])   # _engines

    def test_the_channel_under_load_quick_and_budget(self):
        runs = [run(0, dict(S4, net_down={"v": 300, "corr": 900, "people": 250}), under_load=True, ts=T0),
                run(1, dict(S4, net_up={"v": 100, "budget": True}), mode="quick", ts=T0 + DAY, under_load=None),
                run(2, dict(S4), ts=T0 + 2 * DAY)]
        self.same("load, quick, budget", ABS, runs, now=T0 + 3 * DAY)

    def test_a_history_with_an_automatic_repeat_and_drops(self):
        """Fifteen days of S4, then the channel falls (a drop of the link «down»), an automatic repeat — the events
        the runner stores are the ones score works out over the whole history."""
        runs = [run(i, S4, ts=T0 + i * DAY + 4 * 3600) for i in range(15)]
        runs.append(run(15, dict(S4, net_down=40), ts=T0 + 15 * DAY + 4 * 3600))
        runs.append(run(16, dict((k, v) for k, v in S4.items() if k == "net_down"), ts=T0 + 15 * DAY + 5 * 3600,
                        by="auto", retry_of=runs[-1]["id"]))
        runs[-1]["vals"]["net_down"]["v"] = 45
        self.assertTrue(S.drop_events(ABS, runs, day_of=day_of)[0])  # the case does carry events
        self.same("repeat and drops", ABS, runs, now=T0 + 16 * DAY)


class B1FileTest(Case):
    """Договор с B1 (координатор 19.10): a file exactly in B1's format (tests/bench/test_spool.b1_doc: the rows of
    bench/run.py, the whole inventory, the series, why_args with forced) → drain → base → sample() → score.view() — the
    same answer as score.view over the values B1 measured, with the engines and the cpu flags of its inventory."""

    def test_a_file_of_b1_scores_as_its_values(self):
        from tests.bench.test_spool import b1_doc
        totals = []                                                   # the cipher of the link crypto of each file
        for n, (engines, flags) in enumerate(((["awg", "vless"], ["fpu", "aes", "avx2"]), (["vless"], ["fpu", "sse2"]),
                                              (["vless"], ["aes"]))):
            self.reset()
            ts = T0 + 4 * 3600
            doc = b1_doc("0b1c2d3e%02x" % n, ts=ts, status="ok")
            doc["run"]["inv"].update(engines=engines, flags=flags)
            vals = dict((k, v) for k, v in S4.items() if not isinstance(v, dict) and not k.startswith("_"))
            vals["chacha_1t"] = 300                                          # the weak ChaCha of the critic
            vals.pop("disk_rw")                                              # failed below: no fio
            doc["tests"] = [{"run": doc["run"]["id"], "test": k, "ts": ts + i, "value": v, "unit": None,
                             "scale_version": None, "mode": "full", "under_load": 1, "ok": 1, "why": None,
                             "score": None, "value_corr": None, "people": None, "ca": None}
                            for i, (k, v) in enumerate(sorted(vals.items()))]
            doc["tests"].append({"run": doc["run"]["id"], "test": "disk_rw", "ts": ts + 99, "value": None,
                                 "unit": None, "scale_version": None, "mode": "full", "under_load": 1, "ok": 0,
                                 "why": "no_pkgs", "score": None, "value_corr": None, "people": None, "ca": None})
            doc["tests"][[t["test"] for t in doc["tests"]].index("dns")]["dns_src"] = "kaskad"
            doc["run"]["under_load"] = 0
            direct = dict((k, {"v": v}) for k, v in vals.items())
            direct["disk_rw"] = {"v": None, "ok": False, "why": "no_pkgs"}
            direct.update(_engines=engines, _cpu_flags=flags)
            want = S.view(ABS, [run(0, direct, ts=ts, id=doc["run"]["id"])], T0 + DAY, day_of=day_of, hhmm_of=hhmm_of)
            spool.put(doc)
            w = metrics.open_writer()
            try:
                self.assertEqual(spool.drain(w, T0 + DAY)["bad"], 0)
            finally:
                w.close()
            got = history.view(ABS, T0 + DAY, day_of=day_of, hhmm_of=hhmm_of)
            for x in (got, want):           # F itself (C5) is compared too (REV-ns-c5 П-1): only the flags are stored sorted
                x["vals"]["_cpu_flags"] = sorted(x["vals"]["_cpu_flags"])
            self.assertEqual(dumps(got), dumps(want), (engines, flags))
            totals.append(next(x for x in got["links"] if x["id"] == "crypto")["test"])
        self.assertEqual(totals, ["chacha_1t", "chacha_1t", "aes_1t"])  # vless: the flag aes of the inventory decides

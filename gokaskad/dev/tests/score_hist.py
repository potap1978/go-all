"""Helpers of the history tests of lib/kaskad/score.py (B4): runs on hand-made days, UTC clock. The catalogue of the
tests and the links — ``score.load_spec()`` (the scale 0–1000 and ``score-curves.json`` are gone, NS-D34; ``ABS`` keeps
its name for the callers); ``S4`` — the raw values of the sample S4 (tests/fixtures/score_samples.json)."""

import calendar
import json
import os
import time

from kaskad import score as S

DAY = 86400
T0 = calendar.timegm((2026, 9, 1, 0, 0, 0, 0, 0, 0))     # 2026-09-01 00:00 UTC
ABS = S.load_spec()
T = S.tests_of(ABS)
FIX = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "score_samples.json"),
                     encoding="utf-8"))
S4 = next(c["vals"] for c in FIX["abs"] if c["name"] == "S4 повезло")


def day_of(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def hhmm_of(ts):
    return time.strftime("%H:%M", time.gmtime(ts))


def at(day, hh=4, mm=0):
    return T0 + day * DAY + hh * 3600 + mm * 60


def iso(day):
    return day_of(at(day, 12))


class Hist(object):
    """A list of runs: add(day, vals) -> id; retry=True — an automatic repeat of the last run."""

    def __init__(self):
        self.runs = []

    def add(self, day, vals, hh=4, mm=0, mode="full", by="schedule", retry=False, under_load=False, status="ok",
            retry_of=None):
        rid = "%010x" % (len(self.runs) + 1)
        r = {"id": rid, "ts": at(day, hh, mm), "mode": mode, "by": by, "retry_of": None, "status": status,
             "under_load": under_load, "vals": vals}
        if retry or retry_of:
            r["by"], r["retry_of"] = "auto", retry_of or self.runs[-1]["id"]
        self.runs.append(r)
        return rid

    def series(self, tid, days_vals, mode="full", start=0, step=7, hh=4):
        """One value per day: days start, start+step, …"""
        d = start
        for v in days_vals:
            self.add(d, {tid: v}, hh=hh, mode=mode)
            d += step
        return d

    def usual(self, vals, n=5, start=0, step=7):
        """``n`` ordinary checks of ``vals`` on days start, start+step, …; -> the next day. Five are what «обычно» of
        rel needs at the check after them (NS-D30: usual_n >= 3 base days before the window of the 3 last values)."""
        d = start
        for _ in range(n):
            self.add(d, dict(vals))
            d += step
        return d

    def view(self, now, **kw):
        kw.setdefault("day_of", day_of)
        kw.setdefault("hhmm_of", hhmm_of)
        return S.view(kw.pop("spec", ABS), self.runs, now, **kw)

    def link(self, now, lid, **kw):
        return next(x for x in self.view(now, **kw)["links"] if x["id"] == lid)

    def events(self, **kw):
        return S.drop_events(kw.pop("spec", ABS), self.runs, day_of=day_of, **kw)[0]

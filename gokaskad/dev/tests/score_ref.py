"""The part of the sample of RESEARCH-node-score.md A.4 (k3/handoff/data/node-score-samples/score.py, 17.10) that is
still used after the cut of 23.10 (NS-D34: the curves, the totals and the cpu scale are gone): the loss link — the
Wilson bound, «seen by >= 2 of 3 anchors», loss_value. Copied verbatim below this docstring: the independent reference
of tests/test_score.py (lib/kaskad/score.py must give the same on random inputs). Do not edit — copy again if the
sample changes."""

import math

Z = 1.96                            # Wilson lower bound for the loss link (one-sided 97.5 %)


def wilson_lo(k, n, z=Z):
    """Lower Wilson bound of k successes in n trials (0 when k == 0)."""
    if n <= 0 or k <= 0:
        return 0.0
    p = k / float(n)
    return max(0.0, (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n))


def best2(xs):
    """'Seen by >= 2 of 3 anchors': the worst anchor is dropped, the mean of the other two; 2 -> the better; 1 -> it."""
    xs = sorted(xs)
    keep = xs[:2] if len(xs) == 3 else xs[:1]
    return sum(keep) / len(keep)


def loss_value(points):
    """points: per anchor {"n": sent, "k": lost, "e": events} over rounds outside the node's own channel test.
    An event is a run of consecutive lost packets of one anchor (consecutive dark rounds are one event).
    -> {"v": node loss % = best2 of the per-anchor shares, "n": sent over all anchors,
        "lo": best2 of the per-anchor lower bounds % - the value of the link, "points": kept for the findings of one anchor}
    The share is in packets (k / n); events only widen the bound (clustering): D = k / e packets per event,
    lo = Wilson(k / D, n / D) = Wilson(e, n * e / k). Lone losses (e = k) -> Wilson(k, n); one long outage -> the share
    of its packets with the margin of one event."""
    pts = [p for p in points if p["n"] > 0]
    if not pts:
        return {"v": None, "why": "нет проб"}

    def lo(p):
        if p["k"] <= 0:
            return 0.0
        d = max(1.0, p["k"] / max(p["e"], min(p["k"], 1.0)))      # packets per event, >= 1; no events given = one
        return 100.0 * wilson_lo(p["k"] / d, p["n"] / d)
    return {"v": best2([100.0 * p["k"] / p["n"] for p in pts]), "n": sum(p["n"] for p in pts),
            "lo": best2([lo(p) for p in pts]), "points": points}

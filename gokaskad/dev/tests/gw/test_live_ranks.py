"""PLAN-order Ш2: the rows of «strength» of the tiers — in the decision in memory (``assign``), in ``live.groups``
(``live.groups_now`` → ``ranks_view``), never in pins.json (``collect._pins_doc``, ``pins.normalize``)."""

import json
import unittest

from kaskad.gw import assign as A, collect, live, pins as PN, policy as P

E = ["e-%010d" % i for i in range(1, 6)]


def doc_two_tiers():
    d = P.default_doc("compat")
    g = d["groups"][0]
    g["tiers"] = [P.default_tier("t1", E[:4]), P.default_tier("t2", [E[4]])]
    g["tiers"][0]["method"] = "best_rtt"
    return d


M = {E[0]: {"kind": "awg", "rtt": 11}, E[1]: {"kind": "awg", "rtt": 95.04}, E[2]: {"kind": "awg", "rtt": 12},
     E[3]: {"kind": "awg", "rtt": 12}, E[4]: {"kind": "xray", "rtt": 40}}
ST = dict.fromkeys(E, "ok")


class RanksTest(unittest.TestCase):
    def test_in_the_decision_and_in_live_not_in_pins(self):
        dec = A.assign({"main": [{"id": "d-1"}]}, ST, doc_two_tiers(), None, M, {}, 100)
        ranks = dec["groups"]["main"]["ranks"]
        self.assertEqual(sorted(ranks), ["t1", "t2"])
        self.assertEqual([[x["id"] for x in row] for row in ranks["t1"]["rows"]], [[E[0], E[2], E[3]], [E[1]]])
        g = live.groups_now(dec)["main"]
        self.assertEqual(g["active_tier"], "t1")
        t1 = g["ranks"]["t1"]
        self.assertEqual((t1["by"], t1["queue"], t1["order"], t1["klass"], t1["nodata"], t1["out"]),
                         ("rtt", True, "auto", "ok", False, []))
        self.assertEqual(t1["rows"][0], [{"id": E[0], "v": 11, "share": 0.333}, {"id": E[2], "v": 12, "share": 0.333},
                                         {"id": E[3], "v": 12, "share": 0.333}])
        self.assertEqual(t1["rows"][1], [{"id": E[1], "v": 95, "share": 0.0}])       # 95.04 → 95: rounded to 0.1
        self.assertEqual(g["ranks"]["t2"]["by"], "even")
        pdoc = collect._pins_doc(dec)
        self.assertNotIn("ranks", json.dumps(pdoc))
        pdoc["groups"]["main"]["ranks"] = ranks                                       # even if one came in
        self.assertNotIn("ranks", PN.normalize(dict(pdoc, version=PN.VERSION), now=200)["groups"]["main"])
        dec2 = A.assign({"main": [{"id": "d-1"}]}, dict(ST, **{E[0]: "off"}), doc_two_tiers(), dec, M, {}, 110)
        self.assertEqual([[x["id"] for x in row] for row in dec2["groups"]["main"]["ranks"]["t1"]["rows"]],
                         [[E[2], E[3]], [E[1]]])                                      # counted again, never carried
        self.assertEqual(dec2["groups"]["main"]["ranks"]["t1"]["out"], [{"id": E[0], "why": "off"}])

    def test_frozen_node_still_shows_the_rows_and_no_decision_no_field(self):
        dec = A.assign({}, ST, doc_two_tiers(), None, M, {"offline": True}, 100)
        self.assertIn("t1", live.groups_now(dec)["main"]["ranks"])
        self.assertEqual(live.groups_now(None), {})
        self.assertNotIn("ranks", live.groups_now({"groups": {"main": {"active_tier": "t1"}}})["main"])

    def test_limits_and_junk(self):
        ids = ["e-%010d" % i for i in range(300)]
        tier = {"id": "t1", "exits": ids, "method": "weights", "weights": dict((e, 1 + i % 100) for i, e in enumerate(ids))}
        r = A.rows(tier, dict.fromkeys(ids, "ok"), {})
        many = dict(("t%d" % i, r) for i in range(1, 15))
        v = live.ranks_view(many)
        self.assertEqual(len(v), live.RANK_TIERS)
        self.assertEqual(sum(len(row) for row in v["t1"]["rows"]) + len(v["t1"]["out"]), live.RANK_EXITS)
        size = len(json.dumps(v, ensure_ascii=False, separators=(",", ":")))
        self.assertLess(size, 64 * 1024, size)                                       # the worst case stays small
        junk = {"t1": {"by": "evil", "rows": []}, 5: r, "t2": {"by": "rtt", "rows": [[{"id": 1}, "x", {"id": "e-1",
                "v": float("nan"), "share": "1"}]], "out": [{"id": "e-2", "why": "x" * 99}, None], "klass": "bad"}}
        self.assertEqual(live.ranks_view(junk), {"t2": {"by": "rtt", "queue": False, "order": "auto", "klass": None,
                                                        "nodata": False, "rows": [[{"id": "e-1", "v": None, "share": 0.0}]],
                                                        "out": [{"id": "e-2", "why": "x" * 16}]}})
        self.assertEqual(live.ranks_view(None), {})


if __name__ == "__main__":
    unittest.main()

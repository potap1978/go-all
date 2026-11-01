"""B13: ``schedule.check_body`` — the strict body of a request of the schedule (pure; no panel, no state)."""

import unittest

from kaskad.bench import schedule as S

OK = {"time": "03:45", "mode": "quick", "days": [1, 2, 3]}


def codes(doc):
    return [e["code"] for e in S.check_body(doc)[2]]


class CheckBodyTest(unittest.TestCase):
    def test_good(self):
        back = dict(OK, id="a1b2c3", on=True, quiet=False, monthly=False, since=1791190000, off_why=None)
        entries, opts, errors = S.check_body({"schedule": [OK, back], "confirm": True, "retry": False,
                                              "tz": "Europe/Moscow"})
        self.assertEqual(errors, [])
        self.assertEqual(entries, [OK, back])
        self.assertEqual(opts, {"confirm": True, "retry": False, "tz": "Europe/Moscow"})

    def test_shape(self):
        for doc in (None, [], "x", 1, {}, {"Schedule": []}):
            self.assertEqual(codes(doc), ["body"], doc)
        self.assertEqual(codes({"schedule": "x"}), ["list"])
        self.assertEqual(codes({"schedule": [OK] * 13}), ["total"])
        self.assertEqual(codes({"schedule": [OK] * 12}), [])

    def test_unknown_fields_types_lengths(self):
        self.assertEqual(codes({"schedule": [], "a": 1, "b": 2}), ["field", "field"])
        self.assertEqual(codes({"schedule": [dict(OK, x=1)]}), ["field"])
        self.assertEqual(codes({"schedule": [], "confirm": 1}), ["type"])
        self.assertEqual(codes({"schedule": [], "retry": None}), ["type"])
        self.assertEqual(codes({"schedule": [dict(OK, time="0" * 65)]}), ["long"])
        self.assertEqual(codes({"schedule": [dict(OK, days=list(range(8)))]}), ["long"])
        self.assertEqual(codes({"schedule": [dict(OK, since=-1)]}), ["type"])
        self.assertEqual(codes({"schedule": [dict(OK, since=1.5)]}), ["type"])
        self.assertEqual(codes({"schedule": [dict(OK, off_why=[])]}), ["type"])

    def test_zone_by_fullmatch(self):
        for z in ("Europe/Moscow\n", "../etc/passwd", "a" * 65, "", 3, "Europe/Moscow Asia/Tokyo"):
            self.assertEqual(codes({"schedule": [], "tz": z}), ["tz"], z)

    def test_key_shown_only_when_plain(self):
        errs = S.check_body({"schedule": [], "<img>": 1, "note": 2})[2]
        self.assertEqual([e["args"]["field"] for e in errs], ["?", "note"])

    def test_errors_are_bounded(self):
        doc = dict(("k%d" % i, i) for i in range(100))
        doc["schedule"] = []
        self.assertEqual(len(S.check_body(doc)[2]), S.ERR_MAX)

    def test_every_code_has_texts(self):
        from kaskad.i18n import tr
        for c in ("body", "field", "type", "long", "tz", "list", "total"):
            self.assertNotIn("bench.sched.err", tr("bench.sched.err." + c, field="k", n=1, max=2))


if __name__ == "__main__":
    unittest.main()

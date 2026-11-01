"""The pins of devices (pins.json), the counters of the quota (traffic.json) and the paths of stage 1 — kaskad.gw.pins,
kaskad.paths (docs/k3/SPEC-stage1-exits.md §3.4, §3.5, §5.6, §11.1 «pins.py»; stage 1, T3)."""

import calendar
import glob
import json
import os
from unittest import mock

from kaskad import paths, tz
from kaskad.gw import pins
from tests.helpers import RootCase

NOW = 1760000000
H = 3600


def utc(y, mo, d, h=0, mi=0):
    return calendar.timegm((y, mo, d, h, mi, 0, 0, 0, 0))


def doc_with(*items, gw_all=None):
    """items: (gid, device, exit, tier)."""
    d = pins.empty()
    for gid, did, ex, tier in items:
        g = d["groups"].setdefault(gid, pins.empty_group())
        g["pins"][did] = {"exit": ex, "tier": tier, "since": NOW - 100, "why": "new"}
        g["active_tier"] = "t1"
    d["gw_all"] = gw_all
    return d


class PathsTest(RootCase):
    def test_stage1_paths(self):
        want = {"pins_json": "/var/lib/kaskad/gw/pins.json", "traffic_json": "/var/lib/kaskad/gw/traffic.json",
                "metrics_db": "/var/lib/kaskad/metrics.db", "probe_results": "/var/lib/kaskad/gw/probe-results.json",
                "xray_decision": "/etc/kaskad/gw/xray.decision", "xray_seq": "/var/lib/kaskad/gw/xray-seq",
                "xray_rejected": "/run/kaskad/xray.rejected", "xray_apply_lock": "/run/kaskad/xray-apply.lock",
                "gw_var_dir": "/var/lib/kaskad/gw"}
        for fn, p in want.items():
            self.assertEqual(getattr(paths, fn)(), self.root + p, fn)


class PinsFileTest(RootCase):
    def setUp(self):
        super().setUp()
        self.mono = [1000.0]
        p = mock.patch.object(pins, "_mono", lambda: self.mono[0])
        p.start()
        self.addCleanup(p.stop)

    def disk(self):
        with open(paths.pins_json(), encoding="utf-8") as f:
            return json.load(f)

    def test_written_at_once_when_pins_change(self):
        s = pins.Store()
        d = s.load(NOW)
        self.assertEqual(d, pins.empty())
        d = doc_with(("main", "d1", "e-a", "t1"), gw_all="gx-e-a")
        self.assertTrue(s.save(d, NOW))
        self.assertEqual(pins.read(NOW)["groups"]["main"]["pins"]["d1"]["exit"], "e-a")   # another process sees it
        self.assertEqual(self.disk()["gw_all"], "gx-e-a")
        # the very next tick (no time passed) moves d1: written at once, not after 60 s
        d["groups"]["main"]["pins"]["d1"].update(exit="e-b", why="evict")
        self.assertTrue(s.save(d, NOW + 1))
        self.assertEqual(self.disk()["groups"]["main"]["pins"]["d1"]["exit"], "e-b")
        self.assertEqual(self.disk()["saved"], NOW + 1)
        # a change of the active tier / last step is a decision too
        d["groups"]["main"]["last_step"] = "block"
        self.assertTrue(s.save(d, NOW + 2))
        self.assertEqual(self.disk()["groups"]["main"]["last_step"], "block")

    def test_slow_fields_once_a_minute(self):
        s = pins.Store()
        s.load(NOW)
        d = doc_with(("main", "d1", "e-a", "t1"))
        self.assertTrue(s.save(d, NOW))
        self.assertFalse(s.save(d, NOW + 10))                         # nothing changed
        d["groups"]["main"]["tier_good_since"]["t1"] = NOW + 10
        self.assertFalse(s.save(d, NOW + 10))                         # only tier_good_since: wait
        self.assertNotIn("t1", self.disk()["groups"]["main"]["tier_good_since"])
        self.mono[0] += 61
        self.assertTrue(s.save(d, NOW + 71))
        self.assertEqual(self.disk()["groups"]["main"]["tier_good_since"]["t1"], NOW + 10)
        d["groups"]["main"]["tier_good_since"]["t1"] = NOW + 80
        self.assertFalse(s.save(d, NOW + 80))
        self.assertTrue(s.save(d, NOW + 81, force=True))              # SIGTERM: at once
        self.assertEqual(self.disk()["groups"]["main"]["tier_good_since"]["t1"], NOW + 80)

    def test_atomic_and_private(self):
        s = pins.Store()
        s.load(NOW)
        s.save(doc_with(("main", "d1", "e-a", "t1")), NOW)
        self.assertEqual(oct(os.stat(paths.pins_json()).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(paths.gw_var_dir()).st_mode & 0o777), "0o700")      # made by us
        os.chmod(paths.gw_var_dir(), 0o755)                           # gw/inlog.py keeps it 0755
        s.save(doc_with(("main", "d1", "e-c", "t1")), NOW + 2)
        self.assertEqual(oct(os.stat(paths.gw_var_dir()).st_mode & 0o777), "0o755")      # an existing one: its mode
        with mock.patch("os.rename", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                s.save(doc_with(("main", "d1", "e-b", "t1")), NOW + 1)
        self.assertEqual(self.disk()["groups"]["main"]["pins"]["d1"]["exit"], "e-c")      # the old file whole
        self.assertEqual(glob.glob(os.path.join(paths.gw_var_dir(), ".tmp.*")), [])

    def test_times_clamped_on_reading(self):
        d = doc_with(("main", "d1", "e-a", "t1"))
        d["groups"]["main"]["pins"]["d1"]["since"] = NOW + 7200          # the clock jumped back after it was written
        d["groups"]["main"]["tier_good_since"] = {"t1": NOW + 7200, "t2": NOW - 5}
        self.write("/var/lib/kaskad/gw/pins.json", json.dumps(d))
        for got in (pins.Store().load(NOW), pins.read(NOW)):
            g = got["groups"]["main"]
            self.assertEqual(g["pins"]["d1"]["since"], NOW)
            self.assertEqual(g["tier_good_since"], {"t1": NOW, "t2": NOW - 5})

    def test_broken_file_set_aside(self):
        for text in ("{not json", json.dumps({"version": 2, "groups": {}}), json.dumps([1, 2])):
            self.write("/var/lib/kaskad/gw/pins.json", text)
            self.assertEqual(pins.read(NOW), pins.empty())                 # a reader does not touch it
            self.assertTrue(os.path.exists(paths.pins_json()))
            with mock.patch("time.time", return_value=NOW):
                self.assertEqual(pins.Store().load(NOW), pins.empty())
            self.assertFalse(os.path.exists(paths.pins_json()))
            bad = paths.pins_json() + ".bad-%d" % NOW
            with open(bad, encoding="utf-8") as f:
                self.assertEqual(f.read(), text)
            os.unlink(bad)
            self.assertIn("pins.json: broken", self.read("/var/log/kaskad.log"))

    def test_broken_entries_dropped_not_the_file(self):
        d = doc_with(("main", "d1", "e-a", "t1"))
        d["groups"]["main"]["pins"]["d2"] = {"exit": "", "tier": "t1"}
        d["groups"]["main"]["pins"]["d3"] = "e-a"
        d["groups"]["main"]["pins"]["d4"] = {"exit": "e-c", "tier": "t1", "why": "magic"}
        d["groups"]["main"]["last_step"] = "nowhere"
        d["groups"]["bad"] = []
        self.write("/var/lib/kaskad/gw/pins.json", json.dumps(d))
        got = pins.Store().load(NOW)
        self.assertEqual(sorted(got["groups"]["main"]["pins"]), ["d1", "d4"])
        self.assertEqual(got["groups"]["main"]["pins"]["d4"]["why"], "new")
        self.assertIsNone(got["groups"]["main"]["last_step"])
        self.assertNotIn("bad", got["groups"])
        self.assertTrue(os.path.exists(paths.pins_json()))

    def test_start_after_reboot_takes_the_table(self):
        s = pins.Store()
        s.load(NOW)
        d = doc_with(("main", "d1", "e-a", "t1"), ("main", "d2", "e-b", "t2"), gw_all="gx-e-a")
        d["groups"]["main"]["tier_good_since"] = {"t1": NOW - 50}
        s.save(d, NOW)
        again = pins.Store().load(NOW + 600)                            # a new collector after a reboot
        self.assertEqual(pins.decision_key(again), pins.decision_key(d))
        self.assertEqual(again["groups"]["main"]["tier_good_since"], {"t1": NOW - 50})
        self.assertEqual(again["gw_all"], "gx-e-a")

    def test_prune_deleted_devices_and_groups(self):
        d = doc_with(("main", "d1", "e-a", "t1"), ("main", "d2", "e-b", "t1"), ("stream", "d3", "e-c", "t1"),
                     ("old", "d4", "e-d", "t1"))
        # d2 deleted from the model; d3's route moved it from «stream» into «main»; the group «old» deleted
        gone = pins.prune(d, {"main": ["d1", "d3"], "stream": []})
        self.assertEqual(sorted(gone), [("main", "d2"), ("old", "d4"), ("stream", "d3")])
        self.assertEqual(sorted(d["groups"]["main"]["pins"]), ["d1"])
        self.assertEqual(d["groups"]["stream"]["pins"], {})
        self.assertNotIn("old", d["groups"])
        self.assertEqual(pins.prune(d, {"main": ["d1", "d3"], "stream": []}), [])

    def test_handover_moves_pins_to_the_other_copy(self):
        d = doc_with(("main", "d1", "e-a", "t1"), ("main", "d2", "e-b", "t1"), ("stream", "d3", "e-a", "t1"),
                     gw_all="gx-e-a")
        where = {"e-a2": ("main", "t2")}.get
        n = pins.handover(d, {"e-a": "e-a2"}, where)
        self.assertEqual(n, 1)
        self.assertEqual(d["groups"]["main"]["pins"]["d1"], {"exit": "e-a2", "tier": "t2", "since": NOW - 100,
                                                            "why": "new"})
        self.assertNotIn("d3", d["groups"]["stream"]["pins"])         # the copy is in another group: picked anew
        self.assertEqual(d["groups"]["main"]["pins"]["d2"]["exit"], "e-b")
        self.assertEqual(d["gw_all"], "gx-e-a2")
        d3 = doc_with(("main", "d1", "e-a", "t1"), gw_all="gx-e-a")
        pins.handover(d3, {"e-a": "e-a2"}, {"e-a2": ("stream", "t1")}.get)
        self.assertEqual(d3["gw_all"], "gx-e-a")                      # the copy is out of «Основная»: not moved
        d2 = doc_with(("main", "d1", "e-a", "t1"))
        self.assertEqual(pins.handover(d2, {"e-a": "e-a2"}), 1)       # no placement known: in place
        self.assertEqual(d2["groups"]["main"]["pins"]["d1"]["tier"], "t1")

    def test_handover_file_seen_by_the_collector(self):
        s = pins.Store()
        s.load(NOW)
        s.save(doc_with(("main", "d1", "e-a", "t1")), NOW)
        self.assertEqual(pins.handover_file({"e-a": "e-a2"}, now=NOW + 5), 1)     # ops, under the state lock
        self.assertEqual(s.doc(NOW + 6)["groups"]["main"]["pins"]["d1"]["exit"], "e-a2")
        self.assertEqual(pins.handover_file({"e-x": "e-y"}, now=NOW + 7), 0)
        os.unlink(paths.pins_json())
        self.assertEqual(pins.handover_file({"e-a2": "e-a"}, now=NOW + 8), 0)      # no file: nothing written
        self.assertFalse(os.path.exists(paths.pins_json()))

    def test_handover_keeps_unsaved_tier_good_since(self):
        """REV-s1-t3 П7: tier_good_since is written once in 60 s; the file of handover_file must not undo it. The same
        for ``told_tier`` (REV-s1fin С-1): the feed must not say again a fall it said."""
        mono = [1000.0]
        with mock.patch.object(pins, "_mono", lambda: mono[0]):
            s = pins.Store()
            d = s.load(NOW)
            d = doc_with(("main", "d1", "e-a", "t1"), ("stream", "d2", "e-b", "t1"))
            d["groups"]["main"]["tier_good_since"] = {"t1": NOW - 50}
            d["groups"]["main"]["told_tier"] = "t1"
            s.save(d, NOW)
            d = s.doc(NOW + 10)
            d["groups"]["main"]["tier_good_since"] = {"t1": NOW - 50, "t2": NOW + 5}
            d["groups"]["stream"]["tier_good_since"] = {"t9": NOW + 6}
            d["groups"]["main"]["told_tier"] = "t2"
            self.assertFalse(s.save(d, NOW + 10))                     # only the slow field: not yet on the disk
            self.assertEqual(pins.read(NOW + 10)["groups"]["main"]["told_tier"], "t1")
            self.assertEqual(pins.handover_file({"e-a": "e-a2"}, now=NOW + 11), 1)
            got = s.doc(NOW + 12)
        self.assertEqual(got["groups"]["main"]["pins"]["d1"]["exit"], "e-a2")          # the decision of the file
        self.assertEqual(got["groups"]["main"]["tier_good_since"], {"t1": NOW - 50, "t2": NOW + 5})
        self.assertEqual(got["groups"]["stream"]["tier_good_since"], {"t9": NOW + 6})
        self.assertEqual(got["groups"]["main"]["told_tier"], "t2")
        self.assertNotIn("told_tier", got["groups"]["stream"])        # a group it was never said for: as before

    def test_clamp_states(self):
        snap = {"exits": {"e-a": {"off_until": NOW + 99999, "unstable_until": NOW + 99999, "since": NOW + 5,
                                  "state_since": NOW + 5},
                          "e-b": {"off_until": NOW + 30, "unstable_until": NOW + 60, "since": NOW - 5}}}
        pins.clamp_states(snap, NOW, off_max_s=600)
        self.assertEqual(snap["exits"]["e-a"], {"off_until": NOW + 600, "unstable_until": NOW + 900, "since": NOW,
                                                "state_since": NOW})
        self.assertEqual(snap["exits"]["e-b"], {"off_until": NOW + 30, "unstable_until": NOW + 60, "since": NOW - 5})


class TrafficTest(RootCase):
    def setUp(self):
        super().setUp()
        self.z = tz.Zone("Fixed/Moscow", types=[(3 * 3600, False)])
        self.mono = [1000.0]
        p = mock.patch.object(pins, "_mono", lambda: self.mono[0])
        p.start()
        self.addCleanup(p.stop)

    def test_rule_of_deltas(self):
        self.assertEqual(pins.delta(150, 100), 50)
        self.assertEqual(pins.delta(100, 100), 0)
        self.assertEqual(pins.delta(30, 100), 30)                      # xray restarted: the counter from zero
        self.assertEqual(pins.delta(0, 0), 0)

    def test_day_by_the_zone_of_the_node(self):
        d = pins.traffic_empty()
        q = {"period": "month", "limit_gb": 1, "reset_day": 1}
        t = utc(2030, 5, 10, 20, 50)                                   # 23:50 MSK
        self.assertEqual(pins.traffic_add(d, "e-a", 100, 100, t, q, self.z), (0, 0))    # the point of reference
        self.assertEqual(pins.traffic_add(d, "e-a", 400, 300, t + 300, q, self.z), (300, 200))
        r = d["exits"]["e-a"]
        self.assertEqual((r["day"], r["day_bytes"], r["period_bytes"]), ("2030-05-10", 500, 500))
        pins.traffic_add(d, "e-a", 500, 400, t + 900, q, self.z)       # 00:05 MSK: a new day
        self.assertEqual((r["day"], r["day_bytes"], r["period_bytes"]), ("2030-05-11", 200, 700))
        pins.traffic_add(d, "e-a", 50, 10, t + 960, q, self.z)         # xray restarted
        self.assertEqual((r["day_bytes"], r["last"]), (260, {"rx": 50, "tx": 10}))

    def test_month_period_from_reset_day(self):
        q = {"period": "month", "limit_gb": 1, "reset_day": 15}
        self.assertEqual(pins.period_start(q, utc(2030, 5, 14, 20, 59), self.z), "2030-04-15")
        self.assertEqual(pins.period_start(q, utc(2030, 5, 14, 21, 0), self.z), "2030-05-15")    # 00:00 MSK of 15th
        self.assertEqual(pins.period_start(q, utc(2030, 1, 3), self.z), "2029-12-15")
        self.assertEqual(pins.next_reset(q, utc(2030, 12, 20), self.z), "2031-01-15")
        self.assertEqual(pins.period_start({"period": "day"}, utc(2030, 5, 14, 21, 0), self.z), "2030-05-15")
        self.assertEqual(pins.next_reset({"period": "day"}, utc(2030, 12, 31, 12), self.z), "2031-01-01")
        d = pins.traffic_empty()
        pins.traffic_add(d, "e-a", 0, 0, utc(2030, 5, 14, 20), q, self.z)
        pins.traffic_add(d, "e-a", 10 ** 6, 0, utc(2030, 5, 14, 20, 30), q, self.z)
        self.assertEqual(pins.used(d, "e-a", q, utc(2030, 5, 14, 20, 30), self.z), 10 ** 6)
        self.assertEqual(pins.used(d, "e-a", q, utc(2030, 5, 14, 21, 1), self.z), 0)            # the reset day
        pins.traffic_add(d, "e-a", 3 * 10 ** 6, 0, utc(2030, 5, 14, 21, 1), q, self.z)
        self.assertEqual(d["exits"]["e-a"]["period_bytes"], 2 * 10 ** 6)

    def test_warned_once_per_period_across_restarts(self):
        q = {"period": "month", "limit_gb": 0.001, "reset_day": 1, "warn_pct": 80}    # 1 MB
        t = utc(2030, 5, 10, 12)
        st = pins.TrafficStore()
        d = st.load()
        pins.traffic_add(d, "e-a", 0, 0, t, q, self.z)
        pins.traffic_add(d, "e-a", 850000, 0, t + 60, q, self.z)
        self.assertEqual(pins.quota_due(d, "e-a", q, t + 60, self.z), [80])
        self.assertEqual(pins.quota_due(d, "e-a", q, t + 61, self.z), [])
        self.assertTrue(st.save(d))
        d = pins.TrafficStore().load()                                 # the collector restarted
        self.assertEqual(pins.quota_due(d, "e-a", q, t + 120, self.z), [])
        pins.traffic_add(d, "e-a", 1000001, 0, t + 180, q, self.z)
        self.assertEqual(pins.quota_due(d, "e-a", q, t + 180, self.z), [100])
        # the next period: both again; the old period forgotten
        nxt = utc(2030, 6, 1, 1)
        pins.traffic_add(d, "e-a", 2100001, 0, nxt, q, self.z)
        self.assertEqual(pins.quota_due(d, "e-a", q, nxt, self.z), [80, 100])
        self.assertEqual(list(d["exits"]["e-a"]["warned"]), ["2030-06-01"])

    def test_store_once_a_minute_and_broken_file(self):
        st = pins.TrafficStore()
        d = st.load()
        self.assertTrue(st.save(d))
        self.assertFalse(st.save(d))
        self.mono[0] += 60
        self.assertTrue(st.save(d))
        self.assertFalse(st.save(d))
        self.assertTrue(st.save(d, force=True))
        self.assertEqual(oct(os.stat(paths.traffic_json()).st_mode & 0o777), "0o600")
        self.write("/var/lib/kaskad/gw/traffic.json", "{oops")
        self.assertEqual(pins.traffic_read(), pins.traffic_empty())
        self.assertEqual(pins.TrafficStore().load(), pins.traffic_empty())
        self.assertEqual(len(glob.glob(paths.traffic_json() + ".bad-*")), 1)

    def test_reading_is_tolerant(self):
        self.write("/var/lib/kaskad/gw/traffic.json", json.dumps({"version": 1, "exits": {
            "e-a": {"day": "2030-05-10", "day_bytes": -5, "period_start": "2030-05-01", "period_bytes": 7,
                    "last": {"rx": "x", "tx": 3}, "warned": {"2030-05-01": [80, "y"]}}, "": {}}}))
        d = pins.traffic_read()
        self.assertEqual(list(d["exits"]), ["e-a"])
        self.assertEqual(d["exits"]["e-a"], {"day": "2030-05-10", "day_bytes": 0, "period_start": "2030-05-01",
                                             "period_bytes": 7, "last": {"rx": None, "tx": 3},
                                             "warned": {"2030-05-01": [80]}})
        q = {"period": "month", "limit_gb": 1, "reset_day": 1}
        t = utc(2030, 5, 10, 12)
        self.assertEqual(pins.traffic_add(d, "e-a", 10 ** 9, 13, t, q, self.z), (0, 10))   # rx: no reference
        self.assertEqual(d["exits"]["e-a"]["period_bytes"], 17)

    # REV-s1-t3 С1: no point of reference -> the reading becomes it; the whole counter is no traffic of the period
    def test_first_reading_after_a_broken_file_counts_nothing(self):
        q = {"period": "month", "reset_day": 1, "limit_gb": 100, "warn_pct": 80, "action": "remove"}
        t = utc(2026, 10, 20, 12)
        self.write("/var/lib/kaskad/gw/traffic.json", "{broken")
        d = pins.TrafficStore().load()
        self.assertEqual(pins.traffic_add(d, "e1", 200 * 10 ** 9, 50 * 10 ** 9, t, q, self.z), (0, 0))
        self.assertEqual(pins.used(d, "e1", q, t, self.z), 0)
        self.assertEqual(pins.quota_due(d, "e1", q, t, self.z), [])
        self.assertEqual(pins.traffic_add(d, "e1", 200 * 10 ** 9 + 700, 50 * 10 ** 9 + 300, t + 60, q, self.z),
                         (700, 300))
        self.assertEqual(pins.used(d, "e1", q, t + 60, self.z), 1000)
        d2 = pins.traffic_empty()                                      # a new exit whose kawgN is long up
        self.assertEqual(pins.traffic_add(d2, "e2", 5 * 10 ** 9, 0, t, q, self.z), (0, 0))

    # REV-s1-t3 З3: a counter missing in a tick keeps its reference
    def test_missing_counter_keeps_last(self):
        q = {"period": "month", "reset_day": 1, "limit_gb": 10}
        t = utc(2026, 10, 20, 12)
        d = pins.traffic_empty()
        pins.traffic_add(d, "e2", 5 * 10 ** 9, 7, t, q, self.z)
        self.assertEqual(pins.traffic_add(d, "e2", None, 9, t + 60, q, self.z), (0, 2))
        self.assertEqual(d["exits"]["e2"]["last"], {"rx": 5 * 10 ** 9, "tx": 9})
        self.assertEqual(pins.traffic_add(d, "e2", 5 * 10 ** 9 + 1000, 9, t + 120, q, self.z), (1000, 0))
        self.assertEqual(pins.traffic_add(d, "e2", "junk", -1, t + 180, q, self.z), (0, 0))
        self.assertEqual(d["exits"]["e2"]["period_bytes"], 1002)

    # REV-s1-t3 С2: the zone moved west — the day and the period do not go back (§3.7: from the nearest midnight)
    def test_zone_to_the_west_keeps_day_and_period(self):
        msk = tz.Zone("Fixed/MSK", types=[(3 * 3600, False)])
        ny = tz.Zone("Fixed/NY", types=[(-4 * 3600, False)])
        q = {"period": "month", "reset_day": 21, "limit_gb": 100, "warn_pct": 80}
        t = utc(2026, 10, 20, 22)                                      # 01:00 MSK on the 21st, 18:00 NY on the 20th
        d = pins.traffic_empty()
        pins.traffic_add(d, "e", 0, 0, t - 5 * H, q, msk)
        pins.traffic_add(d, "e", 40 * 10 ** 9, 0, t - 4 * H, q, msk)   # the old period
        pins.traffic_add(d, "e", 45 * 10 ** 9, 0, t, q, msk)           # 5 GB on the reset day by MSK
        r = d["exits"]["e"]
        self.assertEqual((r["day"], r["period_start"], r["period_bytes"]), ("2026-10-21", "2026-10-21", 5 * 10 ** 9))
        pins.traffic_add(d, "e", 45 * 10 ** 9 + 1000, 0, t + 60, q, ny)   # the owner picked New York
        self.assertEqual((r["day"], r["day_bytes"], r["period_start"], r["period_bytes"]),
                         ("2026-10-21", 5 * 10 ** 9 + 1000, "2026-10-21", 5 * 10 ** 9 + 1000))
        self.assertEqual(pins.used(d, "e", q, t + 60, ny), 5 * 10 ** 9 + 1000)
        self.assertEqual(pins.period_of(d, "e", q, t + 60, ny), "2026-10-21")
        self.assertEqual(pins.next_reset(q, t + 60, ny, start=pins.period_of(d, "e", q, t + 60, ny)), "2026-11-21")
        pins.traffic_add(d, "e", 46 * 10 ** 9, 0, t + 6 * H + 60, q, ny)   # after NY midnight: the 21st by NY too
        self.assertEqual((r["day"], r["day_bytes"], r["period_bytes"]), ("2026-10-21", 6 * 10 ** 9, 6 * 10 ** 9))
        pins.traffic_add(d, "e", 47 * 10 ** 9, 0, t + 30 * H + 60, q, ny)  # the 22nd by NY: a new day, same period
        self.assertEqual((r["day"], r["day_bytes"], r["period_bytes"]), ("2026-10-22", 10 ** 9, 7 * 10 ** 9))

    # REV-s1-t3 С2: the clock jumped back at boot (§3.4) — the period and warned stay, no second push
    def test_clock_back_keeps_period_and_warned(self):
        q = {"period": "month", "reset_day": 1, "limit_gb": 10, "warn_pct": 80, "action": "remove"}
        now = utc(2026, 10, 20, 12)
        d = pins.traffic_empty()
        pins.traffic_add(d, "e1", 0, 0, now, q, self.z)
        pins.traffic_add(d, "e1", 10 * 10 ** 9 + 500, 0, now + 60, q, self.z)
        self.assertEqual(pins.quota_due(d, "e1", q, now + 60, self.z), [80, 100])
        back = utc(2026, 9, 28, 12)                                    # boot: the clock of the old period
        pins.traffic_add(d, "e1", 1000, 0, back, q, self.z)            # xray anew: cur < last -> cur
        r = d["exits"]["e1"]
        self.assertEqual((r["day"], r["period_start"], r["period_bytes"]), ("2026-10-20", "2026-10-01",
                                                                         10 * 10 ** 9 + 1500))
        self.assertEqual(r["warned"], {"2026-10-01": [80, 100]})
        self.assertEqual(pins.used(d, "e1", q, back, self.z), 10 * 10 ** 9 + 1500)
        self.assertEqual(pins.quota_due(d, "e1", q, back, self.z), [])
        pins.traffic_add(d, "e1", 2000, 0, now + 3600, q, self.z)      # NTP: the clock is right again
        self.assertEqual(pins.quota_due(d, "e1", q, now + 3600, self.z), [])
        self.assertEqual(r["period_bytes"], 10 * 10 ** 9 + 2500)
        nxt = utc(2026, 11, 1, 1)                                      # the real next period still starts
        pins.traffic_add(d, "e1", 3000, 0, nxt, q, self.z)
        self.assertEqual((r["period_start"], r["period_bytes"], r["warned"]), ("2026-11-01", 1000, {}))

    # REV-s1-t3 З1: a raised limit takes back the marks the use is below again
    def test_raised_limit_rearms_the_marks(self):
        q = {"period": "month", "reset_day": 1, "limit_gb": 10, "warn_pct": 80}
        now = utc(2026, 10, 20, 12)
        d = pins.traffic_empty()
        pins.traffic_add(d, "e4", 0, 0, now, q, self.z)
        pins.traffic_add(d, "e4", 10500 * 10 ** 6, 0, now + 60, q, self.z)
        self.assertEqual(pins.quota_due(d, "e4", q, now + 60, self.z), [80, 100])
        q12 = dict(q, limit_gb=12)                                     # 87.5 %: 100 taken back, 80 stays
        self.assertEqual(pins.quota_due(d, "e4", q12, now + 61, self.z), [])
        self.assertEqual(d["exits"]["e4"]["warned"], {"2026-10-01": [80]})
        pins.traffic_add(d, "e4", 12500 * 10 ** 6, 0, now + 120, q12, self.z)
        self.assertEqual(pins.quota_due(d, "e4", q12, now + 120, self.z), [100])
        q20 = dict(q, limit_gb=20)                                     # 62.5 %: both taken back
        self.assertEqual(pins.quota_due(d, "e4", q20, now + 121, self.z), [])
        pins.traffic_add(d, "e4", 16500 * 10 ** 6, 0, now + 180, q20, self.z)
        self.assertEqual(pins.quota_due(d, "e4", q20, now + 180, self.z), [80])

    # REV-s1-t3 П1: warn_pct out of 50–99 is clamped — never one threshold twice
    def test_warn_pct_clamped(self):
        now = utc(2026, 10, 20, 12)
        for warn, due in ((100, [99, 100]), (150, [99, 100]), (10, [50, 100]), (None, [80, 100])):
            q = {"period": "month", "reset_day": 1, "limit_gb": 10, "warn_pct": warn}
            d = pins.traffic_empty()
            pins.traffic_add(d, "e3", 0, 0, now, q, self.z)
            pins.traffic_add(d, "e3", 11 * 10 ** 9, 0, now + 60, q, self.z)
            self.assertEqual(pins.quota_due(d, "e3", q, now + 60, self.z), due, warn)

    # REV-s1-t3-2 Н1: the period of the exit off the grid of the quota (it only goes forward) — the reset is still the
    # next point of the grid, not «start + 1 month»
    def test_next_reset_after_a_period_off_the_grid(self):
        now = utc(2026, 10, 16, 9)
        d = pins.traffic_empty()
        q10 = {"period": "month", "limit_gb": 10, "reset_day": 10}
        pins.traffic_add(d, "e-a", 0, 0, now, q10, self.z)
        q21 = dict(q10, reset_day=21)                                  # 16.10: reset_day 10 -> 21
        start = pins.period_of(d, "e-a", q21, now, self.z)
        self.assertEqual(start, "2026-10-10")                          # the period of the exit stays
        self.assertEqual(pins.next_reset(q21, now, self.z, start=start), "2026-10-21")
        q1 = {"period": "month", "limit_gb": 10, "reset_day": 1}
        dd = pins.traffic_empty()
        pins.traffic_add(dd, "e-a", 0, 0, now, {"period": "day", "limit_gb": 10}, self.z)    # day -> month
        start = pins.period_of(dd, "e-a", q1, now, self.z)
        self.assertEqual(start, "2026-10-16")
        self.assertEqual(pins.next_reset(q1, now, self.z, start=start), "2026-11-01")
        q28 = dict(q1, reset_day=28)
        self.assertEqual(pins.next_reset(q28, now, self.z, start="2026-12-28"), "2027-01-28")   # on the grid
        self.assertEqual(pins.next_reset(q28, now, self.z, start="2026-12-30"), "2027-01-28")   # past it, December
        self.assertEqual(pins.next_reset({"period": "day"}, now, self.z, start="2026-10-20"), "2026-10-21")
        q31 = dict(q1, reset_day=31)                                   # REV-s1-t3b П-а: reset_day > 28 -> 28
        self.assertEqual(pins.next_reset(q31, now, self.z), "2026-10-28")
        self.assertEqual(pins.next_reset(q31, now, self.z, start="2026-10-28"), "2026-11-28")

    # REV-s1-t3-2 Н2: a day / period that is no real date is dropped on reading — it would be later than any day and
    # freeze both (they only go forward); next_reset ignores such a start
    def test_bad_dates_do_not_freeze_the_period(self):
        q = {"period": "month", "limit_gb": 10, "reset_day": 1}
        now = utc(2026, 11, 20, 9)
        for bad in ("2026/10/01", "zzzzzzzzzz", "2026-02-30", "2026-13-01", "0000-01-01", "2026-1-001", 20261001,
                    "2026-10-01\n",
                    "\u0662\u0660\u0662\u0666-\u0661\u0660-\u0660\u0661",           # Arabic-Indic digits (REV-s1-t3b Н-а)
                    "\uff12\uff10\uff12\uff16-\uff11\uff10-\uff10\uff11"):          # fullwidth digits
            d = {"version": 1, "exits": {"e-a": {"day": bad, "period_start": bad, "day_bytes": 5 * 10 ** 9,
                                                 "period_bytes": 5 * 10 ** 9, "last": {"rx": 0, "tx": 0}}}}
            d = pins.traffic_normalize(d)
            r = d["exits"]["e-a"]
            self.assertEqual((r["day"], r["period_start"]), (None, None), bad)
            pins.traffic_add(d, "e-a", 100, 0, now, q, self.z)
            r = d["exits"]["e-a"]
            self.assertEqual((r["day"], r["period_start"], r["period_bytes"]), ("2026-11-20", "2026-11-01", 100), bad)
            self.assertEqual(pins.next_reset(q, now, self.z, start=bad), "2026-12-01", bad)
            raw = {"exits": {"e-a": {"period_start": bad, "period_bytes": 7}}}           # not read through the store
            self.assertEqual(pins.period_of(raw, "e-a", q, now, self.z), "2026-11-01", bad)
        self.assertEqual(pins.traffic_normalize({"version": 1, "exits": {"e-a": {"day": "2028-02-29"}}})
                         ["exits"]["e-a"]["day"], "2028-02-29")                          # a leap day is a day

    # REV-s1-t3-2 П3 (SG §2.2 П3): a change of role — a new leg, its counters from 0: the first reading counts
    def test_new_leg_counts_its_first_reading(self):
        q = {"period": "month", "limit_gb": 10, "reset_day": 1}
        now = utc(2026, 10, 16, 9)
        d = pins.traffic_empty()
        pins.traffic_add(d, "e-a", 0, 0, now, q, self.z)
        pins.traffic_add(d, "e-a", 9000, 1000, now + 60, q, self.z)
        d["exits"]["e-a"]["warned"] = {"2026-10-01": [80]}
        r = pins.traffic_new_leg(d, "e-a")
        self.assertEqual(r["last"], {"rx": 0, "tx": 0})
        self.assertEqual((r["period_start"], r["period_bytes"], r["warned"]), ("2026-10-01", 10000, {"2026-10-01": [80]}))
        self.assertEqual(pins.traffic_add(d, "e-a", 300, 200, now + 120, q, self.z), (300, 200))   # not lost
        self.assertEqual(d["exits"]["e-a"]["period_bytes"], 10500)
        self.assertEqual(pins.traffic_new_leg(d, "e-new")["last"], {"rx": 0, "tx": 0})     # no record yet

"""Круг 1, Т2, №41: «Удалить историю активности» без выключения «Хранить» (DELETE /api/gw/activity,
policy_ops.activity_wipe -> metrics.wipe_dev; сборщик — activity.wipe по meta.dev_wiped). Стёрто сейчас; окна,
накопленные до удаления (открытое, ждущие, в /run, в ходу у раунда), в базу после него не попадают; счётчики сессий
не дают ложного пика. Каждый тест, кроме миграции, падает на коде до №41."""

import json
import os
import sqlite3
import unittest
from unittest import mock

from kaskad import activity as A, state
from kaskad.errors import KaskadError
from kaskad.gw import collect, hostprobe, live, metrics, model, policy_ops
from tests.gw.test_collect_activity import ActWorld
from tests.panel.test_panel_activity import ActApiCase
from tests.helpers import RootCase


def win(ts, rows, rounds=30, secs=300):
    return {"ts": ts, "rounds": rounds, "secs": secs, "rows": rows}


class CoreTest(unittest.TestCase):
    """Переносимый блок: ``activity.wipe`` — чистая функция над накопителем."""

    def acc(self):
        a = A.new(1000 * 300)
        a["at"] = 1000 * 300 + 120.0
        a["prev"] = {"d-1|wg|10.0.0.2": [5000, 6000, 1000 * 300 + 120.0]}
        a["cur"] = {"d-1": [120.0, 60.0, 4000, 500]}
        a["rounds"], a["secs"] = 12, 120.0
        a["wait"] = [win(999 * 300, {"d-1": [300, 0, 1, 1]}), win(998 * 300, {"d-1": [300, 0, 1, 1]})]
        return a

    def test_before_and_at_go_after_stays_counters_stay(self):
        a = A.wipe(self.acc(), 1000 * 300 + 60)                 # the wipe inside the open window
        self.assertEqual((a["cur"], a["rounds"], a["secs"], a["wait"]), ({}, 0, 0.0, []))
        self.assertEqual(a["prev"], self.acc()["prev"])            # no false peak: the counters go on
        self.assertEqual(a["bucket"], 1000 * 300)
        a = A.wipe(self.acc(), 998 * 300)                         # an old wipe: only the window of then
        self.assertEqual([w["ts"] for w in a["wait"]], [999 * 300])
        self.assertEqual(a["cur"], self.acc()["cur"])

    def test_no_mark_or_garbage_changes_nothing(self):
        for at in (None, True, "1", float("nan"), -5):
            with self.subTest(at=at):
                self.assertEqual(A.wipe(self.acc(), at), self.acc())


class BaseTest(RootCase):
    """``metrics.wipe_dev`` и сторож ``write_dev``."""

    def setUp(self):
        super().setUp()
        self.b = 1_700_000_100 // 300 * 300
        w = metrics.open_writer()
        try:
            for i in range(3):
                self.assertTrue(w.write_dev(win(self.b + 300 * i, {"d-aaaa": [300, 100, 5000, 600]})))
        finally:
            w.close()

    def count(self):
        conn = sqlite3.connect(metrics.paths.metrics_db())
        try:
            rows = sum(conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in metrics.DEV_TABLES)
            nodes = conn.execute("SELECT COUNT(*) FROM dev_node").fetchone()[0]
            meta = dict(conn.execute("SELECT k, v FROM meta").fetchall())
            return rows, nodes, meta
        finally:
            conn.close()

    def test_wipe_erases_and_marks(self):
        before = metrics.dev_info()["bytes"]
        res, why = metrics.wipe_dev(self.b + 400)
        self.assertIsNone(why)
        self.assertEqual(res["bytes"], before)
        rows, nodes, meta = self.count()
        self.assertEqual((rows, nodes), (0, 0))
        self.assertNotIn("dev_flush", meta)
        self.assertNotIn("dev_since", meta)
        self.assertEqual(meta["dev_wiped"], str(self.b + 400))
        self.assertEqual(metrics.dev_info()["bytes"], 0)
        self.assertEqual(metrics.wipe_dev(self.b + 500)[0]["bytes"], 0)    # again: nothing, no error

    def test_the_writer_never_writes_a_window_of_before_the_wipe(self):
        metrics.wipe_dev(self.b + 400)                             # inside the window b+300
        w = metrics.open_writer()
        try:
            for ts in (self.b, self.b + 300):                      # before / the window of the wipe
                self.assertTrue(w.write_dev(win(ts, {"d-aaaa": [300, 100, 5000, 600]})))   # nothing to retry
            self.assertEqual(self.count()[:2], (0, 0))
            self.assertTrue(w.write_dev(win(self.b + 600, {"d-aaaa": [300, 100, 5000, 600]})))
            self.assertEqual(w.meta("dev_flush"), str(self.b + 600))
        finally:
            w.close()
        self.assertEqual(self.count()[:2], (3, 2))                 # m5 + h1 + d1, the node by 300 and 3600

    def test_no_base_is_not_made(self):
        os.remove(metrics.paths.metrics_db())
        self.assertEqual(metrics.wipe_dev(self.b), (None, "nodb"))
        self.assertFalse(os.path.exists(metrics.paths.metrics_db()))
        with mock.patch.object(metrics, "sqlite3", None):
            self.assertEqual(metrics.wipe_dev(self.b), (None, "nosqlite"))

    def test_busy_erases_nothing(self):
        rows = self.count()[0]
        other = sqlite3.connect(metrics.paths.metrics_db(), isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            with mock.patch.object(metrics, "WIPE_WAIT", 0.2):
                self.assertEqual(metrics.wipe_dev(self.b + 400), (None, "busy"))
            other.execute("ROLLBACK")
        finally:
            other.close()
        self.assertEqual(self.count()[0], rows)
        self.assertGreater(rows, 0)
        self.assertNotIn("dev_wiped", self.count()[2])

    def test_a_base_of_3_3_7_11_without_the_mark(self):
        """Миграция: база 3.3.7.11 (meta без dev_wiped) — окна пишутся как раньше, метки нет."""
        rows, _n, meta = self.count()
        self.assertNotIn("dev_wiped", meta)
        w = metrics.open_writer()
        try:
            self.assertTrue(w.write_dev(win(self.b + 900, {"d-aaaa": [300, 100, 5000, 600]})))
            self.assertIsNone(collect._act_wiped(w))
            self.assertEqual(w.meta("dev_flush"), str(self.b + 900))
        finally:
            w.close()
        self.assertGreater(self.count()[0], rows)


class CollectorTest(ActWorld):
    def wipe(self, **kw):
        return policy_ops.activity_wipe(now=self.now, **kw)

    def run_doc(self):
        return json.loads(self.read("/run/kaskad/gw/activity.json"))

    def two_windows_and_some_more(self):
        self.online.add(self.did)
        self.to_next_window((5000, 500), self.did)                  # the first sight counts 0
        self.to_next_window((5000, 500), self.did)
        for _ in range(4):                                         # the open window has traffic of before the wipe
            self.traffic(self.did, 5000, 500)
            self.round()
        self.assertTrue(self.rows())
        self.assertIn(self.did, self.live_act()["cur"])

    def test_wiped_now_nothing_comes_back_no_peak(self):
        self.two_windows_and_some_more()
        dry = self.wipe(dry=True)
        self.assertEqual((dry["dry"], dry["bytes"] > 0), (True, True))
        self.assertTrue(self.rows())                                # a dry run erases nothing
        ver = policy_ops.activity_get()["ver"]
        res = self.wipe()
        self.assertEqual(res["freed"], dry["bytes"])
        self.assertEqual((res["keep"], res["ver"], res["bytes"]), (True, ver, 0))   # «Хранить» untouched
        self.assertTrue(model.activity_keep(state.load()))
        for t in metrics.DEV_TABLES:
            self.assertEqual(self.rows(t), [], t)
        self.assertIn("GW activity: the history of activity wiped", self.log())
        self.traffic(self.did, 5000, 500)
        self.round()
        self.assertEqual(self.live_act().get("cur"), {})            # the open window of the wipe — empty
        self.assertIsNone(self.live_act().get("ts"))                # the window it named is erased
        self.assertEqual(self.run_doc()["cur"], {})
        self.to_next_window((5000, 500), self.did)
        self.assertEqual(self.rows(), [])                           # the window of the wipe is never written
        self.to_next_window((5000, 500), self.did)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][4:], (30 * 5000, 30 * 500))        # a whole window, no peak of the counters
        self.assertEqual(self.live_act()["ts"], rows[0][0])

    def test_windows_waiting_before_the_wipe_never_land(self):
        self.online.add(self.did)
        with mock.patch.object(hostprobe, "clock", lambda: {"synced": False, "offset_s": 0.0}):
            for _ in range(3):
                self.to_next_window((1000, 0), self.did)
            self.assertEqual(len(collect.MEM.act["acc"]["wait"]), 3)
            self.wipe()
            self.round()
            self.assertEqual(collect.MEM.act["acc"]["wait"], [])
        self.to_next_window((1000, 0), self.did)                    # synchronised: nothing of before it
        self.assertEqual(self.rows(), [])
        self.to_next_window((1000, 0), self.did)
        self.assertEqual(len(self.rows()), 1)

    def test_a_round_under_way_cannot_write_after_the_wipe(self):
        """Гонка: раунд прочитал метку до удаления (его накопитель её не видел) и закрыл окно после — ``write_dev``
        видит метку в своей транзакции и окно не пишет."""
        self.two_windows_and_some_more()
        self.wipe()
        with mock.patch.object(collect, "_act_wiped", lambda w=None: None):
            self.to_next_window((5000, 500), self.did)
        self.assertEqual(self.rows(), [])

    def test_a_restart_after_the_wipe(self):
        """Сборщик перезапущен после удаления: накопитель из /run (сохранён до него) не приносит прошлое."""
        self.online.add(self.did)
        with mock.patch.object(hostprobe, "clock", lambda: {"synced": False, "offset_s": 0.0}):
            self.to_next_window((1000, 0), self.did)
            self.to_next_window((1000, 0), self.did)
        self.assertTrue(self.run_doc()["wait"])
        self.wipe()
        collect.MEM.reset()                                         # a restart of the collector process
        with mock.patch.object(collect, "LIVE", live.Live()):
            self.to_next_window((1000, 0), self.did)
            self.assertEqual(self.rows(), [])
            self.to_next_window((1000, 0), self.did)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0][4], 30 * 1000)              # no peak after the restart either

    def test_a_light_round_shows_none_of_it(self):
        self.two_windows_and_some_more()
        self.wipe()
        self.traffic(self.did, 5000, 500)
        st = state.load()
        self.now += 3
        self.assertTrue(collect._activity_light(st, self.fake_sessions(st, None), self.now))
        self.assertEqual(collect.LIVE.activity_open["cur"], {})


class RefusalTest(ActWorld):
    def test_off_nosqlite_nodb(self):
        policy_ops.activity_set(False, want_ver=policy_ops.activity_get()["ver"])
        for dry in (True, False):
            with self.assertRaises(KaskadError) as c:
                policy_ops.activity_wipe(dry=dry)
            self.assertEqual((c.exception.key, c.exception.status), ("activity_off", 409))
            self.assertIn("выключено", str(c.exception))
        self.assertFalse(model.activity_keep(state.load()))         # the switch untouched
        policy_ops.activity_set(True, want_ver=policy_ops.activity_get()["ver"])
        with mock.patch.object(metrics, "sqlite3", None):
            with self.assertRaises(KaskadError) as c:
                policy_ops.activity_wipe()
            self.assertEqual(c.exception.key, "nosqlite")
        self.assertFalse(os.path.exists(self.path("/var/lib/kaskad/metrics.db")))   # no round yet: no base
        with self.assertRaises(KaskadError) as c:
            policy_ops.activity_wipe(dry=True)
        self.assertEqual((c.exception.key, c.exception.status), ("nodb", 409))

    def test_busy(self):
        self.online.add(self.did)
        self.to_next_window((1000, 0), self.did)
        with mock.patch.object(metrics, "wipe_dev", lambda at: (None, "busy")):
            with self.assertRaises(KaskadError) as c:
                policy_ops.activity_wipe()
        self.assertEqual((c.exception.key, c.exception.status), ("db_busy", 409))


class ApiTest(ActApiCase):
    """DELETE /api/gw/activity: админ, X-Kaskad-Admin; ?dry=1 — сколько освободится; запись — freed и настройки."""

    def test_the_contract(self):
        self.put(win(self.b - 600, {self.d1: [300, 100, 5000, 600]}), win(self.b - 300, {self.d2: [300, 0, 10, 10]}))
        self.assertEqual(self.call("DELETE", "/api/gw/activity", csrf=False).status, 403)
        self.assertEqual(self.call("DELETE", "/api/gw/activity", tunnel=False).status, 401)
        self.assertTrue(self.ok("GET", "/api/gw/activity")["activity"])
        before = self.ok("GET", "/api/gw/activity/settings")
        dry = self.ok("DELETE", "/api/gw/activity?dry=1")
        self.assertEqual((dry["dry"], dry["bytes"]), (True, before["bytes"]))
        self.assertGreater(dry["bytes"], 0)
        out = self.ok("DELETE", "/api/gw/activity")
        self.assertEqual((out["freed"], out["keep"], out["ver"], out["bytes"]), (before["bytes"], True, before["ver"], 0))
        summary = self.ok("GET", "/api/gw/activity")
        self.assertNotIn(self.d1, json.dumps(summary))
        doc = self.err(409, "PUT", "/api/gw/activity/settings", {"keep": False, "ver": "000000000000"})
        self.assertEqual(doc["code"], "object_changed")
        self.ok("PUT", "/api/gw/activity/settings", {"keep": False, "ver": before["ver"]})
        doc = self.err(409, "DELETE", "/api/gw/activity")
        self.assertEqual(doc["code"], "activity_off")


if __name__ == "__main__":
    unittest.main()

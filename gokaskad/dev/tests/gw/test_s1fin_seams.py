"""А4 (dev-s1fin): the seams of the three parts merged together — Н-3 (a repeated key «e-…-201» is no exit of its own),
М-2 (the name of the exit in its events) and the automatic refresh ≤ 5 s (the light rounds of the collector while a
panel watches, 3.3.7.11). Each part has its own tests; these check them in one sequence of rounds:

* the collector (``gw/collect.py``): with the same key in upstream.txt and upstream-ru.txt (the move of a role, or by
  hand through the SSH menu) and a Russian group, the light rounds never place the pool nor write the policy (the
  race of Н-3 gets no new chance every 2–5 s), the open minute of «Выходы» has the exit and never its repeat, the next
  point of m1 takes the same bytes, and neither m1 nor the summary of «Выходы» has the repeat;
* the events (М-2 × the stream of the panel): an exit event without a name of its own (``exit_country_changed``) comes
  to the panel as one ``alert`` with the name; the live and light rounds written every 2–5 s after it carry it in
  live.json with the name and never make it news again."""

import json
import socket
import sqlite3
import time
from unittest import mock

from kaskad import state
from kaskad.gw import collect, exits, live, metrics, policy
from kaskad.panel import sse
from tests.fakes_gw import write_ru_pool
from tests.gw.test_collect_activity import ActWorld
from tests.gw.test_s1_ru_pool_ops import HOME
from tests.panel.panelcase import PanelCase
from tests.panel.test_panel_sse import SseClient


def _policy_text():
    return json.dumps(state.load()["gateway"]["exit_policy"], sort_keys=True)


class CollectorSeamTest(ActWorld):
    """Н-3 × the light round: the AmneziaWG exit (kawg2, its counters in /sys) is also a Russian key."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(collect, "_clock_synced", lambda runner: True)
        p.start()
        self.addCleanup(p.stop)
        self.ctr_x = [10 ** 6, 2 * 10 ** 6]
        self.put()
        with state.transaction() as st:                               # a Russian group: 3.3.7.10 put the repeat there
            st["gateway"]["exit_policy"]["groups"].append(policy.default_group(HOME, "ru", name="Дом"))
        line = [ln for ln in exits.pool_lines("abroad") if exits.exit_id(ln) == self.aid]
        self.assertEqual(len(line), 1)
        write_ru_pool(line)
        self.rep = [e["id"] for e in exits.pool(self.w.runner, roles=exits.ROLES) if e.get("dup_of") == self.aid]
        self.assertEqual(len(self.rep), 1)
        self.assertTrue(policy.repeat_id(self.rep[0]))

    def put(self, drx=0, dtx=0):
        self.ctr_x[0] += drx
        self.ctr_x[1] += dtx
        self.write("/sys/class/net/kawg2/statistics/rx_bytes", "%d\n" % self.ctr_x[0])
        self.write("/sys/class/net/kawg2/statistics/tx_bytes", "%d\n" % self.ctr_x[1])

    def doc(self):
        return json.loads(self.read("/run/kaskad/live.json"))

    def m1_exits(self):
        conn = sqlite3.connect(self.path("/var/lib/kaskad/metrics.db"))
        try:
            return sorted(r[0] for r in conn.execute("SELECT DISTINCT exit FROM m1").fetchall())
        finally:
            conn.close()

    def test_a_repeated_key_in_the_rounds_of_a_watched_panel(self):
        self.online.add(self.did)
        self.round()                                                     # the first point of m1: the base
        self.assertIn(self.rep[0], [e["id"] for e in collect.MEM.light_pool])   # the pool of the round has it
        pol = _policy_text()
        self.assertNotIn(self.rep[0], pol)                               # the full round did not place it (Н-3)
        self.put(1000, 3000)
        for _ in range(3):                                               # a panel watches: light rounds in the pause
            self.traffic(self.did, 5000, 500)
            self.assertTrue(collect._light_round(self.w.runner))
        self.assertEqual(_policy_text(), pol)                            # no placement, no write of the policy
        m = self.doc()["metrics"]
        self.assertEqual(m["cur"].get(self.aid), [1000, 3000])
        self.assertEqual([k for k in m["cur"] if policy.repeat_id(k)], [])
        self.assertIn(self.did, self.doc()["activity"]["cur"])           # «Пользователи» moves in the same rounds
        self.put(7, 9)
        self.round(61)                                                   # the next point: the same bytes, one exit
        self.assertEqual(self.doc()["metrics"]["cur"].get(self.aid), [0, 0])
        got = self.m1_exits()
        self.assertIn(self.aid, got)
        self.assertEqual([e for e in got if policy.repeat_id(e)], [])
        conn = sqlite3.connect(self.path("/var/lib/kaskad/metrics.db"))
        try:
            last = conn.execute("SELECT rx, tx FROM m1 WHERE exit = ? ORDER BY ts DESC LIMIT 1", (self.aid,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(last, (1007, 3009))
        summary = metrics.exit_summary(int(self.now) // 60 * 60 + 60)
        self.assertIn(self.aid, summary["exits"])
        self.assertEqual([e for e in summary["exits"] if policy.repeat_id(e)], [])
        self.assertEqual(_policy_text(), pol)

    def db_events(self):
        conn = sqlite3.connect(self.path("/var/lib/kaskad/metrics.db"))
        try:
            return conn.execute("SELECT key, exit, args FROM events").fetchall()
        finally:
            conn.close()

    def test_no_event_of_the_repeat_while_the_key_is_in_both_files(self):
        """T-s1fin (СЕРЬЁЗНО): ``gw exit-move <eid> --group <ru>`` (and «Вернуть как было») hold the key in both files
        for a round; the repeat «e-…-201» is never judged (not parsed — «off», ``disabled``), and its first round put
        «Выход «<the name of the exit>» выключен» into the feed and metrics.db.events while the exit worked. No event of
        the repeat — neither the round it appears in nor the next ones — and none of the exit it repeats."""
        line = [ln for ln in exits.pool_lines("abroad") if exits.exit_id(ln) == self.aid]
        write_ru_pool([])                                                # the key in upstream.txt only: settled
        for _ in range(3):
            self.round()
        write_ru_pool(line)                                              # the move: one round in both files
        self.round()
        write_ru_pool([])
        self.round()
        write_ru_pool(line)                                              # by hand, for several rounds
        for _ in range(3):
            self.round()
        rep = self.rep[0]
        self.assertIsNotNone((collect.MEM.health or {}).get(rep))       # the repeat was in the rounds (the pool has it)
        feed = [(e.get("kind"), e.get("exit"), e.get("text")) for e in self.doc()["events"]]
        self.assertEqual([e for e in feed if e[1] in (rep, self.aid)], [], feed)
        self.assertEqual([e for e in feed if "выключен" in (e[2] or "")], [], feed)
        db = self.db_events()
        self.assertEqual([r for r in db if r[1] in (rep, self.aid) or rep in (r[2] or "")], [], db)


class EventStreamSeamTest(PanelCase):
    """М-2 × the stream: the name of the exit in the alert; the rounds of the refresh never repeat the alert."""

    def setUp(self):
        super().setUp()
        self.gateway()

    def drain(self, s, secs):
        """The kinds of the events of the stream during ``secs`` s (a quiet stream ends the wait early)."""
        kinds, end = [], time.time() + secs
        while time.time() < end:
            try:
                kinds.append(s.next(timeout=max(0.05, end - time.time()))[0])
            except (socket.timeout, OSError):
                break
        return kinds

    def test_the_name_comes_once_and_stays(self):
        s = SseClient(self.port)
        self.addCleanup(s.close)
        s.until("live")
        collect.LIVE.publish([dict(kind="exit_country_changed", exit=self.vid, group="main", role="abroad", old="NL",
                                   new="DE", all_exits=False)], {self.vid: "Вена"}, {"main": "Основная"}, {},
                             time.time())
        self.collect()                                                   # the full round writes it into live.json
        alert = s.until("alert")
        self.assertEqual((alert["kind"], alert["args"].get("name")), ("exit_country_changed", "Вена"))
        self.assertIn("Вена", alert["text"])
        with mock.patch.object(live, "want_fresh", lambda: True):        # the refresh of a watched panel (≤ 5 s)
            for _ in range(2):
                self.assertTrue(collect._light_round(self.w.runner))
                collect._live_round(lambda: collect.LIVE.fast(self.w.runner))
                time.sleep(sse.POLL + 0.2)
        kinds = self.drain(s, sse.POLL + 0.5)
        self.assertIn("live", kinds)                                     # the stream saw the writes of the rounds
        self.assertNotIn("alert", kinds)                                 # never news again
        evs = [e for e in json.loads(self.read("/run/kaskad/live.json"))["events"]
               if e.get("kind") == "exit_country_changed"]
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0]["id"], evs[0]["args"].get("name"), evs[0]["text"]),
                         (alert["id"], "Вена", alert["text"]))


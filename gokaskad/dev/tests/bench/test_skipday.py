"""B11: the push «2 scheduled checks skipped in a day» (SPEC-node-score-schedule-share §2.5 п. 7): the nominal day,
only busy/locked count, the mark skip-push.json written by the bot only and only growing, the switch, the feed event
of the collector, the bot without the gateway (yesterday, then today)."""

import json
import os
from unittest import mock

from kaskad import state, tz
from kaskad.bench import run as brun, skipday, spool
from kaskad.gw import collect, metrics
from tests.bot.fake_tg import OWNER
from tests.bot import test_monitor as TM
from tests.helpers import RootCase

D = 86400


def skip(rid, ts, why="busy"):
    r = brun._run_doc(rid, "quick", "schedule", "aaaaaa", None, ts)
    r.update(status="skipped", why=why, end_ts=ts + 3900)
    spool.put({"v": brun.V, "run": r, "tests": []})


def msk(day, hh, mm):
    y, m, d = (int(x) for x in day.split("-"))
    return tz.to_utc(y, m, d, hh, mm)


class CountTest(RootCase):
    def test_nominal_day_and_codes(self):
        day = "2026-10-19"
        S = "schedule"
        runs = [{"status": "skipped", "why": "busy", "ts": msk(day, 21, 0), "by": S},
                {"status": "skipped", "why": "busy", "ts": msk(day, 23, 30), "by": S},   # decided at 00:35 the next day
                {"status": "skipped", "why": "disabled", "ts": msk(day, 12, 0), "by": S},
                {"status": "skipped", "why": "running", "ts": msk(day, 13, 0), "by": S},
                {"status": "skipped", "why": "missed", "ts": msk(day, 14, 0), "by": S},
                {"status": "skipped", "why": "busy", "ts": msk(day, 14, 30), "by": "panel"},     # REV З-5: a manual
                {"status": "skipped", "why": "locked", "ts": msk(day, 14, 40), "by": "auto"},    # and a repeat — no
                {"status": "ok", "why": None, "ts": msk(day, 15, 0), "by": S}]
        self.assertEqual(skipday.counts(runs), {day: 2})
        now = msk("2026-10-20", 0, 35)
        self.assertEqual(skipday.days_to_tell(runs, now), [(day, 2)])
        self.assertIn("Вчера пропущено проверок по расписанию: 2", skipday.text(day, 2, now, "ru"))
        self.assertIn("2 scheduled checks skipped yesterday", skipday.text(day, 2, now, "en"))
        two = [{"status": "skipped", "why": "busy", "ts": msk(day, 23, 30), "by": S},
               {"status": "skipped", "why": "locked", "ts": msk("2026-10-20", 1, 30), "by": S}]
        self.assertEqual(skipday.days_to_tell(two, now + 3600), [], "different nominal days — silent")

    def test_a_mark_from_the_future_is_no_mark(self):
        """SEC П-2 (E5): the clock ran ahead once — the mark 2099 silences nothing and is overwritten."""
        os.makedirs(os.path.dirname(skipday.path()), exist_ok=True)
        with open(skipday.path(), "w") as f:
            f.write(json.dumps({"day": "2099-01-01", "sent": True}))
        now = msk("2026-10-19", 12, 0)
        runs = [{"status": "skipped", "why": "busy", "ts": msk("2026-10-19", h, 0), "by": "schedule"} for h in (3, 9)]
        self.assertEqual(skipday.mark(now), "")
        self.assertEqual(skipday.days_to_tell(runs, now), [("2026-10-19", 2)])
        self.assertTrue(skipday.write("2026-10-19", True))
        self.assertEqual(spool._push_day(), "2026-10-19")

    def test_the_mark_only_grows_and_is_0600(self):
        self.assertTrue(skipday.write("2026-10-19", True))
        self.assertEqual(os.stat(skipday.path()).st_mode & 0o777, 0o600)
        self.assertFalse(skipday.write("2026-10-18", True))
        self.assertFalse(skipday.write("2026-10-19", False))
        self.assertEqual(json.loads(open(skipday.path()).read()), {"day": "2026-10-19", "sent": True})
        self.assertTrue(skipday.decided("../x"))
        self.assertIn("17.10", skipday.text("2026-10-17", 2, msk("2026-10-19", 12, 0), "ru"))
        self.assertIn("Oct 17", skipday.text("2026-10-17", 2, msk("2026-10-19", 12, 0), "en"))


class CollectorTest(RootCase):
    def setUp(self):
        super().setUp()
        self.w = metrics.open_writer()
        self.addCleanup(self.w.close)

    def test_the_feed_event_and_the_mark_after_the_bot(self):
        day = "2026-10-19"
        skip("0000000001", msk(day, 3, 45))
        skip("0000000002", msk(day, 9, 45), why="locked")
        res = {}
        collect._bench_spool(self.w, res, msk(day, 10, 50))
        self.assertEqual([(e["kind"], e["key"], e["args"]) for e in res["events"]],
                         [("bench_skipped_day", "bench.skipday.feed", {"day": day, "n": 2})])
        skip("0000000003", msk(day, 15, 45))                             # the third — no second event
        res = {}
        collect._bench_spool(self.w, res, msk(day, 16, 50))
        self.assertNotIn("events", res)
        self.assertFalse(os.path.exists(skipday.path()), "the collector never writes the mark")

    def test_gateway_on_after_the_bot_pushed(self):
        """The queue with D-1 and D taken after the bot's pushes of both (mark D) -> no event."""
        skipday.write("2026-10-19", True)
        for i, day in enumerate(("2026-10-18", "2026-10-18", "2026-10-19", "2026-10-19")):
            skip("000000001%d" % i, msk(day, 3 + 6 * (i % 2), 45))
        res = {}
        collect._bench_spool(self.w, res, msk("2026-10-19", 20, 0))
        self.assertNotIn("events", res)


class BotFeedTest(TM.S1PushCase):
    def texts(self):
        return [t for t in self.tg.texts(OWNER) if "пропущено" in t]

    def test_one_push_then_silence(self):
        t = self.now
        day = tz.day(t)
        e = TM.ev("bench_skipped_day", t, key="bench.skipday.feed", day=day, n=2)
        self.live([e])
        self.tick()
        self.assertEqual(len(self.texts()), 1)
        self.assertIn("Сегодня пропущено проверок по расписанию: 2", self.texts()[0])
        self.assertNotIn("Шлюз", self.texts()[0])
        self.assertEqual(json.loads(open(skipday.path()).read()), {"day": day, "sent": True})
        self.live([e, TM.ev("bench_skipped_day", t + 5, key="bench.skipday.feed", day=day, n=2)])   # the same again
        self.tick()
        self.assertEqual(len(self.texts()), 1)

    def test_switch_off_is_final(self):
        self.switches(bench_skipped_day=False)
        t = self.now
        day = tz.day(t)
        self.live([TM.ev("bench_skipped_day", t, key="bench.skipday.feed", day=day, n=2)])
        self.tick()
        self.assertEqual(self.texts(), [])
        self.assertEqual(json.loads(open(skipday.path()).read()), {"day": day, "sent": False})
        self.switches(bench_skipped_day=True)
        self.live([TM.ev("bench_skipped_day", t + 5, key="bench.skipday.feed", day=day, n=3)])
        self.tick()
        self.assertEqual(self.texts(), [])


    def test_the_collector_through_live_to_the_bot(self):
        """REV З-8: the event of the collector goes through ``Live.publish`` (gw/live.py) into live.json and the bot
        pushes it once — not a live.json written by hand."""
        from kaskad.gw import live
        day = tz.day(self.now - 60)
        skip("0000000001", self.now - 120)
        skip("0000000002", self.now - 60, why="locked")
        w = metrics.open_writer()
        self.addCleanup(w.close)
        res = {}
        collect._bench_spool(w, res, self.now)
        L = live.Live()
        L._loaded = True
        L.publish(res["events"], {}, {}, {}, self.now)
        feed = [e for e in L.events if e.get("kind") == "bench_skipped_day"]
        self.assertEqual([(e["key"], e["args"]) for e in feed], [("bench.skipday.feed", {"day": day, "n": 2})])
        self.assertIn("Пропущено проверок по расписанию за %s: 2" % day, feed[0]["text"])
        self.live(L.events)
        self.tick()
        self.assertEqual(len(self.texts()), 1)
        self.assertIn("пропущено проверок по расписанию: 2", self.texts()[0])
        self.assertEqual(json.loads(open(skipday.path()).read()), {"day": day, "sent": True})

    def test_with_the_message_of_the_outage_the_mark_is_written(self):
        """REV З-6: the skips sent within «Сервер был без связи…» (no node_online for NODE_HOLD) — the day decided."""
        from kaskad.bot import monitor
        t = self.now
        day = tz.day(t)
        self.live([TM.ev("node_offline", t, since=t),
                   TM.ev("bench_skipped_day", t + 10, key="bench.skipday.feed", day=day, n=2)])
        self.tick()
        self.assertEqual(self.texts(), [])
        self.now = t + monitor.NODE_HOLD + 60
        self.tick()
        self.assertEqual(len(self.texts()), 1)
        self.assertIn("без связи", self.texts()[0])
        self.assertEqual(json.loads(open(skipday.path()).read()), {"day": day, "sent": True})

    def test_an_old_outage_is_decided_unsent(self):
        from kaskad.bot import monitor
        t = self.now
        day = tz.day(t)
        self.live([TM.ev("node_offline", t, since=t),
                   TM.ev("bench_skipped_day", t + 10, key="bench.skipday.feed", day=day, n=2)])
        self.now = t + monitor.NODE_HOLD + monitor.GW_MAX_AGE + 120
        self.tick()
        self.assertEqual(self.texts(), [])
        self.assertEqual(json.loads(open(skipday.path()).read()), {"day": day, "sent": False})


class BotAloneTest(TM.HostTickTest):
    """The harness of HostTickTest (a node without the gateway), not its tests."""
    def texts(self):
        return [t for t in self.tg.texts(OWNER) if "пропущено" in t]

    def test_yesterday_and_today_exactly_two_pushes_in_10_ticks(self):
        self.gateway(False)
        today = tz.day(self.now)
        yesterday = tz.day(self.now - D)
        for i, (day, hh) in enumerate(((yesterday, 3), (yesterday, 9), (today, 0), (today, 0))):
            skip("00000000a%d" % i, min(msk(day, hh, 15 * i), self.now - 60))
        self.minute(10)
        self.assertEqual(len(self.texts()), 2, self.texts())
        self.assertTrue(self.texts()[0].startswith("⏭ Вчера"))
        self.assertTrue(self.texts()[1].startswith("⏭ Сегодня"))
        self.assertEqual(json.loads(open(skipday.path()).read())["day"], today)

    def fill(self):
        today = tz.day(self.now)
        yesterday = tz.day(self.now - D)
        for i, (day, hh) in enumerate(((yesterday, 3), (yesterday, 9), (today, 0), (today, 0))):
            skip("00000000a%d" % i, min(msk(day, hh, 15 * i), self.now - 60))

    def test_yesterday_not_sent_today_waits(self):
        """REV П-2: «yesterday» not sent — «today» is not sent either (else the mark of today hides yesterday)."""
        self.gateway(False)
        self.fill()
        with mock.patch.object(self.mon, "_send", return_value=False):
            self.minute(1)
        self.assertFalse(os.path.exists(skipday.path()))
        self.minute(1)
        self.assertEqual([t[:8] for t in self.texts()], ["⏭ Вчера ", "⏭ Сегодн"])

    def test_a_mark_not_written_is_not_pushed_again(self):
        """REV П-3: the mark fails (a full disk) — the bot remembers the day, no push every minute."""
        self.gateway(False)
        self.fill()
        with mock.patch.object(skipday, "write", side_effect=OSError("full")):
            self.minute(5)
        self.assertEqual(len(self.texts()), 2)

    def test_the_queue_is_read_only_when_it_changed(self):
        """SEC П-4: the same queue, the day and the mark — no reading of the whole queue every minute."""
        from kaskad.bench import history
        self.gateway(False)
        self.fill()
        self.minute(1)
        with mock.patch.object(history, "runs", wraps=history.runs) as runs:
            self.minute(3)
            self.assertEqual(runs.call_count, 0)
            os.utime(spool.spool_dir(), ns=(1, 1))
            self.minute(1)
            # the skips read the queue once (the push of the checks, B7 З-4, reads it on its own)
            self.assertEqual(len([c for c in runs.call_args_list if c.kwargs.get("by") == "schedule"]), 1)

    def test_manual_and_repeat_skips_are_no_push(self):
        """REV З-5 (SEC E6): busy/locked of a manual check and of a repeat — no push."""
        self.gateway(False)
        for i, by in enumerate(("panel", "auto", "panel")):
            r = brun._run_doc("00000000c%d" % i, "quick", by, None, "0000000001" if by == "auto" else None,
                              self.now - 600 * (i + 1))
            r.update(status="skipped", why="busy" if i else "locked", end_ts=self.now - 60)
            spool.put({"v": brun.V, "run": r, "tests": []})
        self.minute(2)
        self.assertEqual(self.texts(), [])

    def test_with_the_gateway_the_bot_does_not_count(self):
        self.gateway(True)
        today = tz.day(self.now)
        for i in range(2):
            skip("00000000b%d" % i, self.now - 600 * (i + 1))
        self.minute(3)
        self.assertEqual(self.texts(), [])


for _n in dir(TM.HostTickTest):
    if _n.startswith("test") and _n not in BotAloneTest.__dict__:
        setattr(BotAloneTest, _n, None)

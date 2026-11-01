"""B7: the event ``bench_done`` (k3/handoff/SPEC-node-score.md §6.2 B7, §5 «События»; SS §2.5 п. 7): a check of the
server that ended (ok/partial/failed, not an automatic repeat, not skipped or stopped) gives one event when the collector
takes its file of the queue; the collector adds ``rel`` — ``rel_total`` of the node after it (NS-D31: «N от
обычного»); the layer publishes it with the words in the reader's language; the bot sends it by its own switch
``bench_done`` (on by default), as a line of the node — without the title of the gateway. Круг 2: the mark
done-push.json (written by the bot only, only grows) — a file of the queue taken again gives no second push (П-1);
without the gateway the bot reads the queue itself (З-4, as the skips of B11). The cut of 23.10 (NS-D34): the words go
by the status of the check, never by a verdict — no total, band or «weak link»."""

import json
import os
from unittest import mock

from kaskad import i18n
from kaskad.bench import donepush, spool
from kaskad.bot import monitor
from kaskad.gw import collect, live, metrics, model
from tests.bot.fake_tg import OWNER
from tests.bot import test_monitor as TM
from tests.gw.test_metrics_bench import check
from tests.helpers import RootCase

NOW = 1760000000 // 3600 * 3600 + 1800


class DrainTest(RootCase):
    def setUp(self):
        super().setUp()
        self.w = metrics.open_writer()
        self.addCleanup(self.w.close)

    def done(self):
        return [e for e in spool.drain(self.w, NOW)["events"] if e["kind"] == "bench_done"]

    def test_a_check_that_ended(self):
        spool.put(check("0000000001", NOW - 600, mode="quick"))
        self.assertEqual(self.done(), [{"kind": "bench_done", "args": {
            "ts": NOW - 600, "id": "0000000001", "mode": "quick", "by": "panel", "status": "ok", "why": None}}])
        self.assertEqual(self.done(), [])                              # taken once: no second event

    def test_a_file_taken_again_is_told_once_the_bot_decided(self):
        """П-1: the same file of the queue taken again (a crash between the write and the removal); once the bot wrote
        the mark, no event."""
        spool.put(check("0000000001", NOW - 600, mode="quick"))
        self.assertEqual(len(self.done()), 1)
        self.assertTrue(donepush.write(NOW - 600, "0000000001"))
        spool.put(check("0000000001", NOW - 600, mode="quick"))
        self.assertEqual(self.done(), [])
        self.assertFalse(donepush.write(NOW - 900, "0000000009"))       # the mark only grows
        self.assertEqual(json.loads(open(donepush.path()).read()), {"ts": NOW - 600, "id": "0000000001"})
        self.assertEqual(os.stat(donepush.path()).st_mode & 0o777, 0o600)

    def test_a_file_not_removed_gives_no_event(self):
        """REV-ns-b7-2 П-7: without the bot no one writes the mark — a file left in the queue gives no event every
        minute."""
        spool.put(check("0000000001", NOW - 600, mode="quick"))
        with mock.patch.object(spool, "_drop", return_value=0):
            self.assertEqual(self.done(), [])
            self.assertEqual(self.done(), [])

    def test_an_old_check_is_no_news_with_the_gateway_too(self):
        """REV-ns-b7-2 З-7: the collector was down — a check that ended more than an hour ago gives no event."""
        spool.put(check("0000000001", NOW - donepush.MAX_AGE - 700, mode="quick"))
        spool.put(check("0000000002", NOW - donepush.MAX_AGE + 60, mode="quick"))
        self.assertEqual([e["args"]["id"] for e in self.done()], ["0000000002"])

    def test_a_mark_from_the_future_is_no_mark(self):
        """REV-ns-b7-2 П-10 (as skipday): a mark later than tomorrow would silence the pushes till then."""
        donepush.write(NOW + 2 * 86400, "0000000009")
        self.assertIsNone(donepush.mark(now=NOW))
        self.assertFalse(donepush.told(NOW - 600, "0000000001", donepush.mark(now=NOW) or (0, "")))
        os.unlink(donepush.path())
        donepush.write(NOW + 3600, "0000000009")
        self.assertEqual(donepush.mark(now=NOW), (NOW + 3600, "0000000009"))

    def test_failed_partial_yes_skipped_cancelled_auto_no(self):
        spool.put(check("0000000001", NOW - 900, status="failed", why="error", tests=[]))
        spool.put(check("0000000002", NOW - 800, status="partial"))
        spool.put(check("0000000003", NOW - 700, status="skipped", why="busy", tests=[]))
        spool.put(check("0000000004", NOW - 600, status="cancelled", why="stopped", tests=[]))
        spool.put(check("0000000005", NOW - 500, by="auto", retry_of="0000000002"))
        got = self.done()
        self.assertEqual([(e["args"]["id"], e["args"]["status"], e["args"]["why"]) for e in got],
                         [("0000000001", "failed", "error"), ("0000000002", "partial", None)])


class CollectorTest(RootCase):
    def test_the_event_of_the_round_with_rel(self):
        """NS-D31: the args carry ``rel`` = summary.rel_total of the node after the check — nothing of a score."""
        w = metrics.open_writer()
        self.addCleanup(w.close)
        spool.put(check("0000000001", NOW - 600))
        res = {}
        doc = {"summary": {"rel_total": 96, "rel_n": 5}, "score": {"fresh_total": 712.0, "band": "good"}}
        with mock.patch("kaskad.bench.hostscore.score_doc", return_value=doc):
            collect._bench_spool(w, res, NOW)
        self.assertEqual(res["events"], [{"kind": "bench_done", "args": {
            "ts": NOW - 600, "id": "0000000001", "mode": "full", "by": "panel", "status": "ok", "why": None,
            "rel": 96}}])
        self.assertNotIn("bench_events", res)
        self.assertFalse(hasattr(donepush, "with_score"))

    def test_too_little_history_rel_is_none(self):
        w = metrics.open_writer()
        self.addCleanup(w.close)
        spool.put(check("0000000001", NOW - 600))
        res = {}
        with mock.patch("kaskad.bench.hostscore.score_doc", return_value={"summary": {"rel_total": None}}):
            collect._bench_spool(w, res, NOW)
        self.assertEqual([e["args"]["rel"] for e in res["events"]], [None])

    def test_the_score_not_read_the_event_still_goes(self):
        w = metrics.open_writer()
        self.addCleanup(w.close)
        spool.put(check("0000000001", NOW - 600))
        res = {}
        with mock.patch("kaskad.bench.hostscore.score_doc", side_effect=RuntimeError("boom")):
            collect._bench_spool(w, res, NOW)
        self.assertEqual([e["args"].get("rel") for e in res["events"]], [None])
        self.assertFalse({"total", "band", "weak"} & set(res["events"][0]["args"]))


class LayerTest(RootCase):
    def publish(self, args):
        L = live.Live()
        L._loaded = True
        L.publish([{"kind": "bench_done", "args": args}], {}, {}, {"exits": {}}, NOW)
        (ev,) = L.take_published()
        return ev

    def test_words_of_every_variant(self):
        """NS-D34: by the status of the check — the end with «N от обычного» (rel), the end without a number, a partial
        check, the failure with its reason; RU and EN, the whole line."""
        base = {"id": "0000000001", "mode": "quick", "by": "schedule", "status": "ok", "why": None}
        cases = [(dict(base, rel=96), "gw_live.event.bench_done",
                  "Проверка сервера (быстрая) закончена: 96 от обычного", "Server check (quick) is done: 96 of usual"),
                 (dict(base, rel=104.6, mode="full"), "gw_live.event.bench_done",
                  "Проверка сервера (полная) закончена: 105 от обычного", "Server check (full) is done: 105 of usual"),
                 (dict(base, rel=None), "gw_live.event.bench_done_bare",
                  "Проверка сервера (быстрая) закончена", "Server check (quick) is done"),
                 (dict(base, status="partial", rel=80), "gw_live.event.bench_partial",
                  "Проверка сервера (быстрая) закончена, не все тесты прошли",
                  "Server check (quick) is done, not every test passed"),
                 (dict(base, status="failed", why="error", mode="full"), "gw_live.event.bench_failed",
                  "Проверка сервера (полная) не удалась. Проверка прервалась ошибкой",
                  "Server check (full) failed. The check stopped on an error")]
        for args, key, ru, en in cases:
            ev = self.publish(args)
            self.assertEqual((ev["kind"], ev["key"]), ("bench_done", key))
            t_ru, t_en = live.event_text(ev, "ru"), live.event_text(ev, "en")
            self.assertEqual((t_ru, t_en), (ru, en), args)
            self.assertNotRegex(t_en, "[А-Яа-яЁё]")
            self.assertNotIn("{", t_ru + t_en)

    def test_an_old_score_in_the_args_is_never_said(self):
        """Args of before the cut (total/band/weak) — not a word of them: the end without a number."""
        ev = self.publish({"id": "0000000001", "mode": "quick", "by": "panel", "status": "ok", "total": 50.0,
                           "band": "barely", "weak": "up"})
        self.assertEqual(ev["key"], "gw_live.event.bench_done_bare")
        self.assertEqual(live.event_text(ev, "ru"), "Проверка сервера (быстрая) закончена")
        self.assertFalse({"total", "band", "weak", "band_text", "weak_text"} & set(ev["args"]))

    def test_bench_args(self):
        """The args of the feed: rel a whole number (or None; a bool is not a number), the codes as they are and the
        words as msgs; never total, band, weak or their texts."""
        a = live.bench_args({"ts": NOW, "id": "0000000001", "mode": "full", "by": "panel", "status": "ok",
                             "why": None, "rel": 95.5, "total": 712.0, "band": "good", "weak": "up"})
        self.assertEqual(a, {"ts": NOW, "id": "0000000001", "mode": "full", "by": "panel", "status": "ok", "why": None,
                             "rel": 96, "mode_text": {"key": "host_bench.mode_in.full"}})
        self.assertIsInstance(a["rel"], int)
        self.assertIsNone(live.bench_args({"status": "ok", "rel": True})["rel"])
        self.assertIsNone(live.bench_args({"status": "ok", "rel": "96"})["rel"])
        f = live.bench_args({"status": "failed", "why": "error", "mode": "quick"})
        self.assertEqual((f["rel"], f["why_text"]), (None, {"key": "bench_run.why.error"}))
        self.assertEqual(live.bench_key({"status": "ok", "rel": True}), "gw_live.event.bench_done_bare")
        self.assertEqual(live.bench_key({"status": "partial"}), "gw_live.event.bench_partial")
        self.assertEqual(live.bench_key({"status": "failed", "rel": 96}), "gw_live.event.bench_failed")

    def test_the_bot_sends_it_by_its_switch_as_a_line_of_the_node(self):
        ev = self.publish({"id": "0000000001", "mode": "full", "by": "schedule", "status": "ok", "rel": 96})
        self.assertIs(model.NOTIFY_PUSH["bench_done"], True)
        self.assertTrue(monitor.sends(ev, {}))
        self.assertFalse(monitor.sends(ev, {"bench_done": False}))
        self.assertEqual(monitor.gw_icon(ev), "📊")
        with i18n.use_lang("ru"):
            msg = monitor.gw_message([monitor.HostLine(monitor.gw_line(ev))])
        self.assertNotIn(i18n.tr("bot.mon.gw_title", "ru"), msg)
        self.assertIn("закончена: 96 от обычного", msg)


def put(rid, ts, **kw):
    spool.put(check(rid, ts, **kw))


class BotGatewayTest(TM.S1PushCase):
    """With the gateway: the event of live.json, through ``Monitor`` (``NODE_KINDS``) — a line of the node."""
    def texts(self):
        return [t for t in self.tg.texts(OWNER) if "Проверка сервера" in t]

    def event(self, ts, rid="0000000001"):
        L = live.Live()
        L._loaded = True
        L.publish([{"kind": "bench_done", "args": {"ts": int(self.start - 400), "id": rid, "mode": "full", "by": "panel",
                                                    "status": "ok", "rel": 96}}],
                  {}, {}, {"exits": {}}, ts)
        return [e for e in L.events if e.get("kind") == "bench_done"]

    def setUp(self):
        super().setUp()
        self.start = self.now

    def test_a_line_of_the_node_once(self):
        evs = self.event(self.now)
        self.live(evs + self.event(self.now + 5))                  # the queue gave it again: one push (П-1)
        self.tick()
        self.assertEqual(len(self.texts()), 1, self.tg.texts(OWNER))
        self.assertTrue(self.texts()[0].startswith("📊 Проверка сервера (полная) закончена: 96 от обычного"),
                        self.texts())
        self.assertNotIn(i18n.tr("bot.mon.gw_title", "ru"), self.texts()[0])   # REV З-3 (A4): no title «🛡 Шлюз»
        self.assertEqual(json.loads(open(donepush.path()).read()), {"ts": int(self.start - 400), "id": "0000000001"})

    def test_the_mark_is_written_after_the_hold_message(self):
        """REV-ns-b7-2 П-6: «Сервер был без связи … За это время: проверка сервера — 1» decides the check too."""
        t = self.now
        L = live.Live()
        L._loaded = True
        L.publish([{"kind": "node_offline", "args": {"since": t}}], {}, {}, {"exits": {}}, t)
        evs = [e for e in L.events if e.get("kind") == "node_offline"] + self.event(t + 10)
        self.live(evs)
        self.tick()
        self.assertFalse(os.path.exists(donepush.path()))
        self.now = t + monitor.NODE_HOLD + 60
        self.tick()
        self.assertTrue(any("Сервер был без связи" in x for x in self.tg.texts(OWNER)), self.tg.texts(OWNER))
        self.assertEqual(json.loads(open(donepush.path()).read()), {"ts": int(self.start - 400), "id": "0000000001"})

    def test_switch_off_decided_too(self):
        self.switches(bench_done=False)
        self.live(self.event(self.now))
        self.tick()
        self.assertEqual(self.texts(), [])
        self.assertTrue(os.path.exists(donepush.path()))


class BotAloneTest(TM.HostTickTest):
    """Without the gateway (З-4): the bot reads the queue — the same path as the push of the skips (B11)."""
    def texts(self):
        return [t for t in self.tg.texts(OWNER) if "Проверка сервера" in t]

    def test_one_push_per_check_then_silence(self):
        self.gateway(False)
        put("0000000001", self.now - 400, mode="quick")
        self.minute(3)
        self.assertEqual(len(self.texts()), 1, self.tg.texts(OWNER))
        self.assertTrue(self.texts()[0].startswith("📊 Проверка сервера (быстрая)"), self.texts())
        self.assertNotIn(i18n.tr("bot.mon.gw_title", "ru"), self.texts()[0])
        self.assertEqual(json.loads(open(donepush.path()).read())["id"], "0000000001")
        put("0000000002", self.now - 300, status="failed", why="error", tests=[])
        self.minute(2)
        self.assertEqual(len(self.texts()), 2)
        self.assertIn("не удалась", self.texts()[1])

    def test_english(self):
        self.gateway(False)
        put("0000000001", self.now - 400, mode="quick")
        with mock.patch.object(self.mon.app, "lang", return_value="en"):
            self.minute(1)
        got = [t for t in self.tg.texts(OWNER) if "Server check" in t]
        self.assertEqual(len(got), 1, self.tg.texts(OWNER))
        self.assertNotRegex(got[0], "[А-Яа-яЁё]")

    def test_old_auto_and_skipped_no_push(self):
        self.gateway(False)
        put("0000000001", self.now - 2 * 3600)                                   # ended long ago: no news
        put("0000000004", self.now - 3600 - 800)                # read (the window of the history), ended > 1 h ago
        put("0000000002", self.now - 400, by="auto", retry_of="0000000001")
        put("0000000003", self.now - 300, status="skipped", why="busy", tests=[])
        self.minute(2)
        self.assertEqual(self.texts(), [])

    def test_switch_off_is_decided(self):
        self.gateway(False)
        st = __import__("kaskad.state", fromlist=["state"])
        doc = st.load()
        doc.setdefault("gateway", {})["notify"] = {"push": {"bench_done": False}}
        st.save(doc)
        put("0000000001", self.now - 400)
        self.minute(2)
        self.assertEqual(self.texts(), [])
        self.assertEqual(json.loads(open(donepush.path()).read())["id"], "0000000001")

    def test_not_sent_tried_again(self):
        self.gateway(False)
        put("0000000001", self.now - 400)
        with mock.patch.object(self.mon, "_send", return_value=False):
            self.minute(1)
        self.assertFalse(os.path.exists(donepush.path()))
        os.utime(spool.spool_dir(), ns=(1, 1))
        self.minute(1)
        self.assertEqual(len(self.texts()), 1)

    def test_with_the_gateway_the_bot_does_not_read(self):
        self.gateway(True)
        put("0000000001", self.now - 400)
        self.minute(2)
        self.assertEqual(self.texts(), [])


for _n in dir(TM.HostTickTest):
    if _n.startswith("test") and _n not in BotAloneTest.__dict__:
        setattr(BotAloneTest, _n, None)
for _n in dir(TM.S1PushCase):
    if _n.startswith("test") and _n not in BotGatewayTest.__dict__:
        setattr(BotGatewayTest, _n, None)

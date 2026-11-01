"""The receiver of the bot: the offset is saved after the batch was handled, stale updates are not executed,
429 waits retry_after, failures back off ×2 up to 60 s, errors are logged without the token, one lane per
chat (order kept) and one job per chat, dialogs live 30 minutes and any menu button cancels them, the daemon
loop stops on SIGTERM."""

import io
import json
import logging
import os
import signal
import threading
import time

from kaskad.bot import app as bapp
from kaskad.bot import menus, registry, store
from tests.bot.botcase import BotCase
from tests.bot.fake_tg import OWNER, TOKEN


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def offset_doc(case):
    p = case.path("/etc/kaskad/bot_state/offset.json")
    return load(p) if os.path.exists(p) else None


class OffsetTest(BotCase):
    def test_offset_is_saved_only_after_the_batch_was_handled(self):
        self.make_app()
        gate = threading.Event()
        entered = threading.Event()

        def slow(ctx, data):
            entered.set()
            gate.wait(10)
            ctx.reply("готово")

        registry.CALLBACKS.insert(0, ("t_slow", False, False, slow))
        self.addCleanup(registry.CALLBACKS.remove, ("t_slow", False, False, slow))
        upd = self.tg.press(OWNER, "t_slow")
        t = threading.Thread(target=self.app.poll_once)
        t.start()
        self.assertTrue(entered.wait(5))
        self.assertIsNone(offset_doc(self))                      # still being handled: nothing confirmed
        # a crash now: a new process starts from the saved offset and gets the same press again
        first = self.app
        second = self.make_app()
        self.assertEqual(second.offset, 0)
        self.assertEqual([u["update_id"] for u in second.api.get_updates(second.offset, timeout=0)],
                         [upd["update_id"]])
        self.app = first
        gate.set()
        t.join(10)
        self.assertEqual(offset_doc(self)["offset"], upd["update_id"] + 1)
        self.assertEqual(offset_doc(self)["bot"], TOKEN.split(":")[0])
        self.poll()
        self.assertEqual(self.tg.offsets[-1], upd["update_id"] + 1)

    def test_a_failing_handler_does_not_block_the_offset(self):
        self.make_app()

        def broken(ctx, data):
            raise RuntimeError("boom " + TOKEN)

        registry.CALLBACKS.insert(0, ("t_broken", False, False, broken))
        self.addCleanup(registry.CALLBACKS.remove, ("t_broken", False, False, broken))
        buf = io.StringIO()
        bapp.setup_logging(buf)
        upd = self.tg.press(OWNER, "t_broken")
        self.poll()
        self.assertEqual(offset_doc(self)["offset"], upd["update_id"] + 1)
        self.assertIn("Ошибка бота", self.last_text())
        self.assertIn("boom", buf.getvalue())
        self.assertNotIn(TOKEN.split(":")[1], buf.getvalue())

    def test_bash_offset_is_taken_once_and_never_written(self):
        self.write("/etc/kaskad/bot_state/.offset", "41\n")
        app = self.make_app()
        self.assertEqual(app.offset, 41)
        self.tg.next_update = 41
        self.say("/id")
        self.assertEqual(self.read("/etc/kaskad/bot_state/.offset"), "41\n")
        self.assertEqual(offset_doc(self)["offset"], 42)

    def test_another_token_starts_from_zero(self):
        self.write("/etc/kaskad/bot_state/offset.json", json.dumps({"offset": 500, "bot": "999", "last_poll": 1}))
        self.assertEqual(self.make_app().offset, 0)


class StaleTest(BotCase):
    def test_old_messages_are_not_executed(self):
        self.make_app()
        self.tg.message(OWNER, "/start", date=self.now - 700)
        self.tg.message(OWNER, "/menu", date=self.now - 650)
        self.tg.message(OWNER, "/id", date=self.now - 30)
        self.poll()
        texts = self.tg.texts(OWNER)
        self.assertEqual(texts.count(menus.stale_message()), 1)
        self.assertFalse([t for t in texts if "Выберите действие" in t])
        self.assertIn("Ваш Chat ID: <code>%d</code>" % OWNER, texts)

    def test_presses_waiting_after_a_downtime_are_not_executed(self):
        self.write("/etc/kaskad/bot_state/offset.json", json.dumps({"offset": 0, "bot": TOKEN.split(":")[0],
                                                                     "last_poll": self.now - 3600}))
        app = self.make_app()
        app.last_poll = self.now - 3600                       # what offset.json said: down for an hour
        self.press("fa_y")
        answers = self.tg.sent("answerCallbackQuery")
        self.assertEqual(answers[-1].params.get("text"), menus.stale_toast())
        self.assertEqual(self.tg.sent("editMessageText"), [])
        self.press("m")                                        # pressed now: works
        self.assertIn("Выберите действие", self.last_text())

    def test_a_late_answer_without_a_downtime_still_executes(self):
        """The bot was polling (no gap): a press Telegram calls too old to answer is a minute old at most."""
        self.make_app()
        self.tg.fail("answerCallbackQuery", 400,
                     "Bad Request: query is too old and response timeout expired or query ID is invalid")
        self.press("m")
        self.assertIn("Выберите действие", self.last_text())

    def test_after_a_downtime_a_press_too_old_even_for_a_toast_gets_a_message(self):
        app = self.make_app()
        app.last_poll = self.now - 1200
        self.tg.fail("answerCallbackQuery", 400, "Bad Request: query is too old")
        self.press("fa_y")
        self.assertEqual(self.tg.sent("editMessageText"), [])
        self.assertEqual(self.last_text(), menus.stale_button())

    def test_old_buttons_of_the_bash_bot_never_delete_by_number(self):
        self.make_app()
        self.press("dry_1")
        self.assertIn("прежнего меню", self.last_text())
        self.press("zz_unknown")
        self.assertEqual(self.last_text(), menus.old_button())


class BackoffTest(BotCase):
    def test_429_waits_retry_after(self):
        app = self.make_app()
        self.tg.fail("getUpdates", 429, "Too Many Requests: retry after 7", retry_after=7)
        self.assertIsNone(app.poll_once())
        self.assertEqual(self.sleeps, [7])
        self.assertEqual(app.backoff, 0)
        self.assertEqual(app.poll_once(), 0)

    def test_failures_back_off_twice_longer_up_to_a_minute(self):
        app = self.make_app()
        buf = io.StringIO()
        bapp.setup_logging(buf)
        self.tg.fail("getUpdates", 502, "Bad Gateway", times=3)
        for _ in range(3):
            self.assertIsNone(app.poll_once())
        self.assertEqual(self.sleeps, [1, 2, 4])
        self.assertEqual(app.poll_once(), 0)                   # success resets
        self.tg.fail("getUpdates", 502, "Bad Gateway", drop=True, times=1)
        app.poll_once()                                        # a broken connection — the same backoff
        self.assertEqual(self.sleeps[-1], 1)
        app.backoff = 32
        self.tg.fail("getUpdates", 500, "x", times=2)
        app.poll_once()
        app.poll_once()
        self.assertEqual(self.sleeps[-2:], [60, 60])
        self.assertIn("Bad Gateway", buf.getvalue())
        self.assertEqual(buf.getvalue().count("Bad Gateway"), 1)  # the same error is logged once
        self.assertNotIn(TOKEN.split(":")[1], buf.getvalue())

    def test_a_refused_token_waits_a_minute(self):
        app = self.make_app()
        self.tg.fail("getUpdates", 401, "Unauthorized")
        app.poll_once()
        self.assertEqual(self.sleeps, [60])
        self.assertIn("BOT_TOKEN", app.last_error)

    def test_sends_wait_retry_after_once(self):
        self.make_app()
        self.tg.fail("sendMessage", 429, "Too Many Requests: retry after 3", retry_after=3)
        self.say("/id")
        self.assertEqual(self.sleeps, [3])
        self.assertEqual([c.ok for c in self.tg.sent("sendMessage", failed=True)], [False, True])
        self.assertIn("Chat ID", self.last_text())


class LanesTest(BotCase):
    def test_updates_of_a_chat_run_in_order(self):
        self.make_app()
        self.tg.press(OWNER, "a_cp_udp")
        self.tg.message(OWNER, "999.1.1.1", date=self.now)
        self.tg.message(OWNER, "198.51.100.7", date=self.now)
        self.write("/etc/kaskad/state.json", '{"schema": 1, "engine": "nft", "forwards": [], "migrations": [], '
                                             '"gateway": {}}')
        self.poll()
        texts = self.tg.texts(OWNER)
        self.assertIn("Введите IP", texts[0])
        self.assertIn("Некорректный IP", texts[1])
        self.assertIn("<b>ВХОДЯЩИЙ</b> порт", texts[2])

    def test_one_job_per_chat_and_the_menu_answers_meanwhile(self):
        self.make_app()
        self.write("/etc/kaskad/state.json", json.dumps({"schema": 1, "engine": "nft", "migrations": [], "gateway": {},
                                                         "forwards": [{"proto": "tcp", "port": 443, "to": "203.0.113.5",
                                                                       "to_port": 443, "name": "", "origin": "k3",
                                                                       "added": "2026-10-05T00:00:00Z"}]}))
        gate = threading.Event()
        started = threading.Event()
        real = self.app.prober.smart

        def slow(ip, timeout=3, port=None, proto=None):
            started.set()
            gate.wait(10)
            return real(ip, timeout, port, proto)

        self.app.prober.smart = slow
        self.tg.press(OWNER, "p10:203.0.113.5")
        self.app.poll_once()
        self.assertTrue(started.wait(5))
        self.tg.press(OWNER, "po:203.0.113.5")
        self.app.poll_once()
        self.assertTrue(any("Уже выполняется: ping 10×" in t for t in self.tg.texts(OWNER)))
        self.tg.press(OWNER, "m")
        self.app.poll_once()                                    # the lane is free: the menu answers now
        self.assertTrue(any("Выберите действие" in t for t in self.tg.texts(OWNER)))
        gate.set()
        self.assertTrue(self.app.scheduler.wait_idle(15))
        self.assertIn("(10x)</b>", self.last_text())
        self.assertIn("Потеряно: 10/10", self.last_text())
        self.assertEqual(self.sleeps, [1] * 9)


class DialogTest(BotCase):
    def setUp(self):
        super(DialogTest, self).setUp()
        self.write("/etc/kaskad/state.json", '{"schema": 1, "engine": "nft", "forwards": [], "migrations": [], '
                                             '"gateway": {}}')
        self.make_app()

    def test_dialogs_expire_after_30_minutes(self):
        self.press("a_u")
        self.assertTrue(os.path.exists(self.path("/etc/kaskad/bot_state/%d.json" % OWNER)))
        self.now += 31 * 60
        self.say("198.51.100.7")
        self.assertEqual(self.last_text(), menus.dialog_expired())
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/bot_state/%d.json" % OWNER)))
        self.say("198.51.100.7")
        self.assertEqual(self.last_text(), "/start или /menu")

    def test_any_menu_button_cancels_the_dialog(self):
        self.press("a_u")
        self.press("pm")
        self.say("198.51.100.7")
        self.assertEqual(self.last_text(), "/start или /menu")
        self.press("a_u")
        self.say("/menu")
        self.say("198.51.100.7")
        self.assertEqual(self.last_text(), "/start или /menu")

    def test_bash_dialog_files_are_left_alone(self):
        self.write("/etc/kaskad/bot_state/%d" % OWNER, "STATE=awaiting_ip\nPROTO=udp\n")
        self.write("/etc/kaskad/bot_state/system", "UPDATE_TOKEN=ghp_x\n")
        self.press("a_u")
        self.press("m")
        self.assertEqual(self.read("/etc/kaskad/bot_state/%d" % OWNER), "STATE=awaiting_ip\nPROTO=udp\n")
        self.assertEqual(self.read("/etc/kaskad/bot_state/system"), "UPDATE_TOKEN=ghp_x\n")


class DaemonTest(BotCase):
    def test_run_until_sigterm(self):
        app = self.make_app(sleep=None, monitor=False)
        app.scheduler.stop(1)                                   # run() starts its own workers
        from kaskad.bot.scheduler import Scheduler
        app.scheduler = Scheduler(2, on_error=app._worker_error)
        self.tg.message(OWNER, "/start", date=self.now)
        before = signal.getsignal(signal.SIGTERM)

        def kill():
            deadline = time.time() + 10
            while time.time() < deadline and not any("Выберите действие" in t for t in self.tg.texts(OWNER)):
                time.sleep(0.05)
            os.kill(os.getpid(), signal.SIGTERM)

        threading.Thread(target=kill, daemon=True).start()
        t0 = time.time()
        self.assertEqual(app.run(), 0)
        self.assertLess(time.time() - t0, 10)
        self.assertIs(signal.getsignal(signal.SIGTERM), before)          # handlers given back
        methods = [c.method for c in self.tg.sent()]
        self.assertIn("setMyCommands", methods)
        self.assertIn("setChatMenuButton", methods)
        status = load(self.path("/run/kaskad/bot.json"))
        self.assertFalse(status["running"])
        self.assertEqual(status["username"], "kaskad_test_bot")
        self.assertNotIn(TOKEN.split(":")[1], self.read("/run/kaskad/bot.json"))
        log = self.read("/var/log/kaskad.log")
        self.assertIn("Bot daemon started (Python", log)
        self.assertIn("Bot daemon stopped", log)
        self.assertGreater(offset_doc(self)["offset"], 0)

    def test_without_a_token_it_waits(self):
        self.write_config(BOT_TOKEN="")
        app = self.make_app(sleep=None)
        app.scheduler.stop(1)
        from kaskad.bot.scheduler import Scheduler
        app.scheduler = Scheduler(1)
        threading.Timer(0.5, app.stop_event.set).start()
        self.assertEqual(app.run(), 0)
        self.assertEqual(self.tg.sent("getUpdates"), [])
        self.assertIn("BOT_TOKEN", app.last_error)


class StoreTest(BotCase):
    def test_strangers_counter_survives_a_restart(self):
        s = store.Strangers(lambda: self.now)
        self.assertTrue(s.due(42))
        self.assertFalse(store.Strangers(lambda: self.now + 3600).due(42))
        self.assertTrue(store.Strangers(lambda: self.now + 86401).due(42))
        self.assertTrue(store.Strangers(lambda: self.now).due(42, 60, kind="u:"))     # its own counter

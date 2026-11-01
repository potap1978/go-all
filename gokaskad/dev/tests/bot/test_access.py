"""Who may use the bot: BOT_CHAT_ID and BOT_ADMINS; strangers get «Нет доступа» once a day per chat and are
never handled — even when that answer could not be sent; an unbound bot only tells how to bind it."""

import os

from kaskad.bot import menus
from tests.bot.botcase import BotCase
from tests.bot.fake_tg import OWNER

STRANGER = 777000999
ADMIN = 424242


class AccessTest(BotCase):
    def test_owner_gets_the_menu(self):
        self.make_app()
        self.say("/start")
        self.assertIn("<b>Kaskad PRO v", self.last_text())
        self.assertIn("IP: <code>203.0.113.10</code>", self.last_text())
        self.assertEqual(self.buttons()[0], ("🔀 AWG", "a_u"))

    def test_stranger_once_a_day_and_never_handled(self):
        self.make_app()
        self.say("/start", chat=STRANGER)
        self.assertEqual(self.tg.texts(STRANGER), [menus.no_access(STRANGER)])
        self.say("/menu", chat=STRANGER)
        self.press("fa_y", chat=STRANGER)
        self.assertEqual(len(self.tg.texts(STRANGER)), 1)               # nothing more today
        self.assertEqual([c.params.get("text") for c in self.tg.sent("answerCallbackQuery")], [])
        self.now += 86400 + 1
        self.press("fa_y", chat=STRANGER)
        self.assertEqual(self.tg.sent("answerCallbackQuery")[-1].params.get("text"), "⛔ Нет доступа")
        self.assertEqual(self.tg.sent("editMessageText"), [])            # the press did nothing
        self.assertIn("чужой чат %d" % STRANGER, self.read("/var/log/kaskad.log"))

    def test_a_failed_answer_does_not_let_a_stranger_in(self):
        self.make_app()
        self.write("/etc/kaskad/state.json", '{"schema": 1, "engine": "nft", "forwards": [], "migrations": [], '
                                             '"gateway": {}}')
        self.tg.fail("sendMessage", 500, "Internal Server Error", when=lambda p: p.get("chat_id") == STRANGER)
        self.say("/start", chat=STRANGER)
        self.say("/start", chat=STRANGER)
        self.press("a_u", chat=STRANGER)
        sent = self.tg.sent("sendMessage", "editMessageText", chat=STRANGER, failed=True)
        self.assertEqual(len(sent), 1)                                   # only the failed «Нет доступа»
        self.assertIs(sent[0].ok, False)
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/bot_state/%d.json" % STRANGER)))

    def test_admins_from_bot_admins(self):
        self.write_config(BOT_ADMINS="%d, 5" % ADMIN)
        self.make_app()
        self.say("/id", chat=ADMIN)
        self.assertEqual(self.last_text(ADMIN), "Ваш Chat ID: <code>%d</code>" % ADMIN)

    def test_config_changes_are_picked_up(self):
        app = self.make_app()
        self.say("/id", chat=ADMIN)
        self.assertEqual(self.tg.texts(ADMIN), [menus.no_access(ADMIN)])
        self.write_config(BOT_ADMINS=str(ADMIN))
        os.utime(self.path("/etc/kaskad/config"), (1, 1))                # a different signature for sure
        self.assertTrue(app.refresh_config())
        self.say("/id", chat=ADMIN)
        self.assertEqual(self.last_text(ADMIN), "Ваш Chat ID: <code>%d</code>" % ADMIN)

    def test_unbound_bot_tells_how_to_bind(self):
        self.write_config(BOT_CHAT_ID="")
        self.make_app()
        self.say("/start", chat=STRANGER)
        self.assertEqual(self.tg.texts(STRANGER), [menus.not_bound(STRANGER)])
        self.assertIn("меню 8 → 3", self.last_text(STRANGER))
        self.say("/start", chat=STRANGER)
        self.assertEqual(len(self.tg.texts(STRANGER)), 1)               # not more often than once a minute
        self.now += 61
        self.say("/start", chat=STRANGER)
        self.assertEqual(len(self.tg.texts(STRANGER)), 2)
        self.press("m", chat=OWNER)
        self.assertEqual(self.tg.sent("editMessageText"), [])
        self.assertEqual(self.tg.sent("answerCallbackQuery")[-1].params.get("text"),
                         "⛔ Бот ещё не привязан к владельцу")

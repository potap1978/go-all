"""The bot in two languages (3.3.2): the catalog bot.json is complete in ru and en, no Russian is left in the code
of lib/kaskad/bot, the bot speaks the server's language (KASKAD_LANG), «Язык / Language» changes it from the
owner's chat, the menu of commands (setMyCommands) follows the language."""

import json
import os
import re
import unittest

from kaskad.bot import menus, registry
from kaskad.bot.lang import tr, trn
from kaskad.i18n import check
from tests.bot.botcase import BotCase
from tests.bot.fake_tg import OWNER

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATALOG = os.path.join(ROOT, "lib", "kaskad", "i18n", "catalog")
BOT_DIR = os.path.join(ROOT, "lib", "kaskad", "bot")
CYR = re.compile(u"[Ѐ-ӿ]")
ADMIN = 424242


def load(lang):
    with open(os.path.join(CATALOG, lang, "bot.json"), "r", encoding="utf-8") as f:
        return json.load(f)


class CatalogTest(unittest.TestCase):
    def test_ru_and_en_have_the_same_keys_and_placeholders(self):
        check.assert_catalog_complete(self, ["bot"])           # keys, number forms, placeholders, no Russian in en
        for k in load("ru"):
            self.assertTrue(k.startswith("bot."), k)

    def test_english_has_no_russian_and_no_empty_texts(self):
        for k, v in load("en").items():
            self.assertFalse(CYR.search(v), "%s: %s" % (k, v))
            self.assertTrue(v.strip(), k)
        for k, v in load("ru").items():
            self.assertTrue(v.strip(), k)

    def test_number_forms(self):
        self.assertEqual(trn("bot.mon.checks", 3, "ru"), "3 проверки подряд")
        self.assertEqual(trn("bot.mon.checks", 5, "ru"), "5 проверок подряд")
        self.assertEqual(trn("bot.mon.checks", 1, "en"), "1 check in a row")
        self.assertEqual(trn("bot.mon.checks", 3, "en"), "3 checks in a row")


class CodeTest(unittest.TestCase):
    def test_no_russian_for_people_left_in_the_bot(self):
        check.assert_no_cyrillic(self, BOT_DIR)

    def test_every_key_used_in_the_bot_is_in_the_catalog(self):
        ru = load("ru")
        keys = set()
        for fn in sorted(os.listdir(BOT_DIR)):
            if fn.endswith(".py"):
                with open(os.path.join(BOT_DIR, fn), "r", encoding="utf-8") as f:
                    keys |= set(re.findall(r"""\btrn?\(\s*["'](bot\.[A-Za-z0-9_.]+)["']""", f.read()))
        dynamic = [k for k in keys if k.endswith((".", "_"))]                # "bot.cmd." + name and the like
        missing = [k for k in sorted(set(keys) - set(dynamic)) if k not in ru]
        for prefix in dynamic:
            self.assertTrue(any(k.startswith(prefix) for k in ru), prefix)
        self.assertEqual(missing, [])


class EnglishBotTest(BotCase):
    def setUp(self):
        super(EnglishBotTest, self).setUp()
        self.write_config(KASKAD_LANG="en")

    def test_main_menu_help_and_buttons_in_english(self):
        self.make_app()
        self.say("/start")
        self.assertIn("Choose an action", self.last_text())
        labels = [t for t, _ in self.buttons()]
        self.assertIn("📋 Forwards", labels)
        self.assertIn("🌍 Language", labels)
        for t in labels + [self.last_text()]:
            self.assertFalse(CYR.search(t), t)
        self.say("/help")
        self.assertIn("commands", self.last_text())
        self.assertFalse(CYR.search(self.last_text()))
        self.press("sys")
        self.assertIn("System information", self.last_text())
        self.press("mm")
        self.assertIn("Monitoring", self.last_text())

    def test_unknown_text_and_stale_dialog_in_english(self):
        self.make_app()
        self.say("hello")
        self.assertEqual(self.last_text(), "/start or /menu")

    def test_reply_keyboard_matches_both_languages(self):
        self.write_config(KASKAD_LANG="en", MENU_STYLE="reply")
        self.make_app()
        self.say("/start")
        rows = self.last().markup()["keyboard"]
        texts = [b["text"] for row in rows for b in row]
        self.assertIn("💻 System", texts)
        self.say("💻 System")
        self.assertIn("System information", self.last_text())
        self.say("💻 Система")                       # a keyboard of the previous language still on the screen
        self.assertIn("System information", self.last_text())

    def test_strangers_and_unbound_in_the_servers_language(self):
        self.make_app()
        self.say("/start", chat=424242)
        self.assertIn("No access", self.tg.texts(424242)[-1])

    def test_monitor_alert_in_english(self):
        from kaskad.bot import monitors
        monitors.write("198.51.100.7", 10, 100, 0)
        self.make_app(monitor=True)
        m = monitors.get("198.51.100.7")
        for _ in range(3):
            self.app.monitor.judge(m, None, self.now)
        self.assertIn("Not responding: 3 checks in a row.", self.last_text())


class GatewayEventTest(unittest.TestCase):
    def test_the_core_retells_the_event_when_it_can(self):
        from unittest import mock
        from kaskad.bot import monitor
        from kaskad.gw import live
        ev = {"ts": 1, "kind": "exit_dead", "text": "Выход «A» не работает", "exit": "e-1"}
        with mock.patch.object(live, "event_text", lambda e: "Exit “A” is down", create=True):
            self.assertEqual(monitor.event_text(ev), "Exit “A” is down")
        if not hasattr(live, "event_text"):                       # the core without it: the collector's text
            self.assertEqual(monitor.event_text(ev), "Выход «A» не работает")
            self.assertEqual(monitor.event_text({"kind": "exit_alive"}), tr("bot.mon.gw_exit_alive"))

    def test_a_gateway_event_in_english_through_the_real_core(self):
        # сведение 3.3.2: kaskad.gw.live.event_text есть в ядре E1 — бот на en говорит событие шлюза по-английски
        # (сборщик писал по-русски: text на русском, key/args — коды), без подмены функции ядра
        from kaskad.bot import monitor
        from kaskad.gw import live
        from kaskad.i18n import use_lang
        self.assertTrue(callable(getattr(live, "event_text", None)))
        ev = {"ts": 1, "kind": "exit_dead", "text": "Выход «Вена» не работает", "exit": "e-1",
              "key": "gw_live.event.exit_dead", "args": {"name": "Vienna"}}
        with use_lang("en"):
            out = monitor.event_text(ev)
        self.assertEqual(out, "Exit “Vienna” is down")
        self.assertIsNone(CYR.search(out))
        with use_lang("ru"):
            self.assertEqual(monitor.event_text(ev), "Выход «Vienna» не работает")


class LanguageSwitchTest(BotCase):
    def test_owner_switches_the_server_language_and_the_commands_follow(self):
        self.make_app()
        self.press("lang")
        self.assertIn("Язык / Language", self.last_text())
        self.assertIn("lang:en", [d for _, d in self.buttons()])
        self.press("lang:en")
        self.assertRegex(self.read("/etc/kaskad/config"), r'(?m)^KASKAD_LANG="?en"?$')
        cmds = self.tg.sent("setMyCommands")
        self.assertTrue(cmds)
        descr = dict((c["command"], c["description"]) for c in cmds[-1].params["commands"])
        self.assertEqual(descr["help"], "Help")
        self.assertIn("lang", descr)
        self.assertIn("Choose an action", self.last_text())
        self.say("/lang ru")
        self.assertRegex(self.read("/etc/kaskad/config"), r'(?m)^KASKAD_LANG="?ru"?$')
        descr = dict((c["command"], c["description"]) for c in self.tg.sent("setMyCommands")[-1].params["commands"])
        self.assertEqual(descr["help"], "Справка")
        self.assertIn("Выберите действие", self.last_text())
        self.say("/lang de")
        self.assertIn("/lang ru", self.last_text())

    def test_only_the_owners_chat_changes_the_language(self):
        self.write_config(BOT_ADMINS=str(ADMIN))
        self.make_app()
        self.press("lang:en", chat=ADMIN)
        self.assertIn("BOT_CHAT_ID", self.last_text(ADMIN))
        self.assertNotIn("KASKAD_LANG", self.read("/etc/kaskad/config"))

    def test_menu_of_commands_resynced_after_a_change_elsewhere(self):
        self.make_app()
        self.app.commands_wanted = True
        self.assertTrue(self.app.sync_commands(force=True))
        self.assertEqual(self.app.commands_lang, "ru")
        self.write_config(KASKAD_LANG="en")         # the SSH menu or the panel changed it
        self.app.refresh_config()
        self.poll()
        descr = dict((c["command"], c["description"]) for c in self.tg.sent("setMyCommands")[-1].params["commands"])
        self.assertEqual(descr["menu"], "Menu")
        self.assertEqual(self.app.commands_lang, "en")

    def test_every_menu_button_has_a_handler_in_both_languages(self):
        registry.load()
        for lang in ("ru", "en"):
            from kaskad.bot.lang import use_lang
            with use_lang(lang):
                for row in menus.main_inline():
                    for _, data in row:
                        self.assertIsNotNone(registry.find_callback(data)[0], data)
                for row in menus.reply_rows():
                    for t in row:
                        if t != "/inline":
                            self.assertIn(t, registry.REPLIES, t)
        self.assertIn("/lang", registry.COMMANDS)
        self.assertEqual(tr("bot.menu.btn_lang", "en"), "🌍 Language")


if __name__ == "__main__":
    unittest.main()

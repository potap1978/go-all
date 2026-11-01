"""T13 (SPEC-stage1-rename §4.5): the bot shows the new names on every screen (escaped), the label of an inbound in the
status, refuses a bad new name in its own steps and asks again (the rule of names.check), the step of the server name
of a forward goes through ``aliases.rename``; a button with a name of 40 characters fits ``menus.BUTTON_TEXT``."""

from kaskad import state
from kaskad.bot import aliases, menus
from kaskad.gw import model, ops, rename
from tests.bot.test_forwards import FwdCase
from tests.fakes_nft import save_state
from tests.bot.test_gateway import GwBotCase


class BotNamesTest(GwBotCase):
    POOL = False

    def setUp(self):
        super(BotNamesTest, self).setUp()
        self.gateway_on()
        self.make_app()

    def test_new_names_shown_escaped(self):
        rename.rename(self.runner, "device", self.did, "<b>Тел</b> & Ко", via="panel")
        rename.rename(self.runner, "user", self.uid, "Мама <i>", via="panel")
        self.press("gwu:" + self.uid)
        text = self.last_text()
        self.assertIn("👤 <b>Мама &lt;i&gt;</b>", text)
        self.assertIn("&lt;b&gt;Тел&lt;/b&gt; &amp; Ко", text)
        self.assertNotIn("<b>Тел</b>", text)
        self.assertIn(("📱 <b>Тел</b> & Ко", "gwd:" + self.did), self.buttons())   # a button is plain text

    def test_inbound_label_in_the_status(self):
        rename.rename(self.runner, "inbound", "wg", "для <роутеров>")
        self.press("gw")
        self.assertIn("— для &lt;роутеров&gt;", self.last_text())

    def test_a_long_name_fits_a_button(self):
        name = "Ж" * 40
        rename.rename(self.runner, "device", self.did, name)
        self.press("gwu:" + self.uid)
        labels = [t for t, d in self.buttons() if d == "gwd:" + self.did]
        self.assertEqual(len(labels), 1)
        self.assertLessEqual(len(labels[0]), menus.BUTTON_TEXT)
        self.assertIn(name, labels[0])

    def test_bad_new_names_are_asked_again(self):
        self.press("gwan")
        self.say("x" * 41)
        self.assertIn("длиннее 40 знаков", self.last_text())
        self.assertIsNone(model.find_user(state.load(), "x" * 41))
        self.say("мама")                                       # a duplicate of a person (case-insensitive): refused
        self.assertIn("уже есть", self.last_text())
        self.say("Папа")
        self.assertIn("Пользователь «Папа» добавлен", self.last_text())
        self.press("gwpl:linux")
        self.say("a|b")
        self.assertIn("недопустимый знак U+007C", self.last_text())
        self.say("Ноутбук")
        self.assertIn("Протоколы устройства «Ноутбук»", self.last_text())


class BotServerNameTest(FwdCase):
    def setUp(self):
        super(BotServerNameTest, self).setUp()
        save_state()
        self.make_app()

    def test_the_name_of_the_server_is_checked_and_journaled(self):
        self.run_dialog("a_t", "198.51.100.8", "443")
        self.say("Вена\\Рига")
        self.assertIn("недопустимый знак U+005C", self.last_text())
        self.say("Вена")
        self.say("-")
        self.assertEqual(aliases.get("198.51.100.8")["name"], "Вена")
        self.assertIn("RENAME server 198.51.100.8: «» -> «Вена» (via=bot)", self.read("/var/log/kaskad.log"))

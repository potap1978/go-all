"""«📤 Поделиться проверкой» in the bot (C11; SPEC-node-score-schedule-share §5.5, «Сверка 24.10» п. 5; the design r1):
the panel behind nginx, in the open mode and only through the tunnel; the message is new, protected (no forward), without
a preview, and leaves the chat in 5 minutes when it carries the secret prefix; no sign-in link is made; no check or a
stale one — «Сначала проверьте сервер»; no panel; the button of «💻 Система» and /share; English."""

import os
import sys
import types
from unittest import mock

from kaskad import paths
from kaskad.bot import share as BS
from kaskad.panel import conf
from tests.bot.botcase import BotCase

PATH = "app-" + "Ab9" * 28 + "QZ"
SUMMARY = {"last_at": 1761200000, "stale": False, "rel_total": 93, "rel_n": 6, "rel_need": 3}


class ShareBotTest(BotCase):
    def setUp(self):
        super(ShareBotTest, self).setUp()
        self.make_app()
        self.cfg = conf.load()
        self.sm = dict(SUMMARY)
        self.unit = os.path.join(paths.systemd_dir(), conf.UNIT)          # the panel is installed
        os.makedirs(os.path.dirname(self.unit), exist_ok=True)
        with open(self.unit, "w") as f:
            f.write("[Unit]\n")
        # the sign-in link is never made by «Поделиться»: a call fails the test
        auth = types.ModuleType("kaskad.panel.auth")

        def login_link(*a, **k):
            raise AssertionError("login_link called by share")
        auth.login_link = login_link
        for p in (mock.patch.object(conf, "load", lambda *a, **k: self.cfg),
                  mock.patch.object(BS, "_doc", lambda: {"summary": self.sm}),
                  mock.patch.dict(sys.modules, {"kaskad.panel.auth": auth})):
            p.start()
            self.addCleanup(p.stop)

    def nginx(self):
        self.cfg["proxy"].update(on=True, domain="panel.example.com", path=PATH)

    def url_buttons(self):
        m = self.last().markup()
        return [b.get("url") for row in m.get("inline_keyboard") or [] for b in row if b.get("url")]

    def test_behind_nginx(self):
        self.nginx()
        self.press("shr")
        msg = self.last()
        self.assertEqual(msg.method, "sendMessage")                          # a new message, the screen stays
        self.assertIs(msg.params.get("protect_content"), True)
        self.assertEqual(msg.params["link_preview_options"], {"is_disabled": True})
        lines = msg.text.split("\n")
        self.assertEqual(lines[0], "📤 <b>Поделиться проверкой</b>")
        self.assertTrue(lines[1].startswith("Сервер в целом — 93 от обычного за 30 дней."), lines[1])
        link = "https://panel.example.com/%s/#overview/now?open=share" % PATH
        self.assertIn('<a href="%s">%s</a>' % (link, link), msg.text)
        self.assertEqual(self.url_buttons(), [link])
        self.assertEqual([d for _, d in msg.buttons() if d], ["gwl", "m"])
        for no in ("login?t=", "token", "192.0.2", "open mode", "открытый режим"):
            self.assertNotIn(no, msg.text)
        self.now += 299
        self.app.expire_due()
        self.assertEqual(self.tg.sent("deleteMessage"), [])
        self.now += 2
        self.app.expire_due()
        self.assertEqual(len(self.tg.sent("deleteMessage")), 1)
        self.assertNotIn(PATH, self.read("/var/log/kaskad.log"))

    def test_open_mode(self):
        self.cfg["proxy"].update(on=False, path=PATH)
        self.cfg["public"].update(on=True, port=8443, host="203.0.113.10")
        with mock.patch.object(conf, "site_cert", lambda: None):
            self.press("shr")
        self.assertEqual(self.url_buttons(), ["https://203.0.113.10:8443/%s/#overview/now?open=share" % PATH])
        self.assertIs(self.last().params.get("protect_content"), True)

    def test_tunnel_only(self):
        self.press("shr")
        msg = self.last()
        self.assertNotIn("://", msg.text)
        self.assertEqual(self.url_buttons(), [])
        self.assertIn("Панель открывается только через SSH-туннель", msg.text)
        self.assertEqual([d for _, d in msg.buttons() if d], ["gwl", "m"])
        self.now += 400
        self.app.expire_due()
        self.assertEqual(self.tg.sent("deleteMessage"), [])                  # nothing secret: nothing to delete

    def test_nothing_to_compare_and_no_check(self):
        self.nginx()
        self.sm.update(rel_total=None, rel_n=1)
        self.press("shr")
        self.assertIn("Сравнивать пока не с чем: звеньев с историей 1 из 3.", self.last_text())
        for sm in ({"last_at": None}, dict(SUMMARY, stale=True)):
            self.sm = sm
            self.press("shr")
            msg = self.last()
            self.assertIn("Сначала проверьте сервер", msg.text)
            self.assertNotIn("://", msg.text)
            self.assertEqual(self.url_buttons(), [])

    def test_no_panel(self):
        os.remove(self.unit)
        self.press("shr")
        self.assertIn("Веб-панель не установлена", self.last_text())

    def test_a_link_telegram_does_not_take_as_a_button(self):
        self.nginx()
        self.tg.fail("sendMessage", status=400, description="Bad Request: BUTTON_URL_INVALID",
                     when=lambda p: "url" in str(p.get("reply_markup")))
        self.press("shr")
        msg = self.last()
        self.assertEqual(self.url_buttons(), [])
        self.assertIn("#overview/now?open=share", msg.text)                 # the link stays in the text
        self.assertIs(msg.params.get("protect_content"), True)

    def test_the_button_of_the_system_and_the_command(self):
        self.press("sys")
        self.assertIn("shr", [d for _, d in self.buttons()])
        self.nginx()
        self.say("/share")
        self.assertIn("Сервер в целом — 93", self.last_text())
        self.assertIs(self.last().params.get("protect_content"), True)

    def test_english(self):
        self.nginx()
        from kaskad.bot.lang import use_lang
        with use_lang("en"):
            text, url, present = BS.text_and_url()
        self.assertTrue(present and url)
        self.assertIn("Overall the server is at 93 of usual over 30 days.", text)
        self.assertFalse(any("Ѐ" <= c <= "ӿ" for c in text), text)

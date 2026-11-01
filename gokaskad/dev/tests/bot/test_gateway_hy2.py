"""The bot never issues Hysteria2 (D-063, SPEC-stage1-hy2-inbound §5 «Бот», Р5): on the issue button of a device whose
issue comes out as Hysteria2 — a hint RU+EN «the panel or the person's page /p/» and the buttons of its other inbounds
(Hysteria2 is not among them); no hysteria2:// link and no QR in any message."""

from unittest import mock

from kaskad.gw import ops
from tests.bot.test_gateway import GwBotCase

LINK = "hysteria2://SECRETAUTH0123456789abcdef@203.0.113.10:443/?sni=203.0.113.10#x"


def fake_issue(available):
    def issue(runner, ref, inbound=None, os_name=None, where="menu"):
        kind = inbound or "hy2"
        return {"ok": True, "kind": kind, "name": "Телефон", "os": "android", "available": list(available),
                "howto": ["step"], "support": {}, "alternatives": [],
                "items": [{"format": "link", "title": "Hysteria2", "body": LINK, "filename": None, "notes": []},
                          {"format": "qr", "title": "QR", "body": [LINK], "filename": None, "notes": []}]}
    return issue


class Hy2BotTest(GwBotCase):
    def setUp(self):
        super(Hy2BotTest, self).setUp()
        self.gateway_on()
        self.make_app()

    def sent_texts(self):
        return [m.text or "" for m in self.tg.sent("sendMessage", "sendDocument", "sendPhoto")]

    def test_the_default_hysteria2_gives_the_hint_and_the_other_inbounds(self):
        with mock.patch.object(ops, "issue", fake_issue(["hy2", "vless"])):
            self.press("gwi:" + self.did)
        self.assertIn("Hysteria2 выдаётся в панели («Выдать настройки») или на странице пользователя /p/.", self.last_text())
        btns = self.buttons()
        self.assertIn("gwis:%s:vless" % self.did, [d for _, d in btns])
        self.assertNotIn("gwis:%s:hy2" % self.did, [d for _, d in btns])
        self.assertIn("gwd:" + self.did, [d for _, d in btns])
        self.assertFalse(any("hysteria2://" in t or "SECRETAUTH" in t for t in self.sent_texts()))
        self.assertEqual(self.tg.sent("sendPhoto"), [])                     # no QR either

    def test_only_hysteria2_gives_the_hint_and_back(self):
        with mock.patch.object(ops, "issue", fake_issue(["hy2"])):
            self.press("gwis:%s:hy2" % self.did)
        self.assertIn("/p/", self.last_text())
        self.assertEqual([d for _, d in self.buttons()], ["gwd:" + self.did])
        self.assertFalse(any("hysteria2://" in t for t in self.sent_texts()))

    def test_the_hint_in_english(self):
        from kaskad.i18n import tr
        self.assertEqual(tr("bot.gw.hy2_elsewhere", "en"),
                         "Hysteria2 is issued in the panel (\"Issue settings\") or on the user’s /p/ page.")

    def test_vless_is_issued_as_before(self):
        with mock.patch.object(ops, "issue", fake_issue(["vless", "hy2"])):
            self.press("gwis:%s:vless" % self.did)
        self.assertNotIn("/p/", self.last_text())
        self.assertNotIn("gwis:%s:hy2" % self.did, [d for _, d in self.buttons()])


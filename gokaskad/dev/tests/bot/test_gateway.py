"""The gateway in the bot on the fake node of work package 4 (dummy wg/awg2 inbounds, a pool of two exits):
the status screen from live.json, people and devices with pagination, adding a device (inbounds preselected
by ops.offers), route, new keys, delete, issuing (documents, QR as PNG through qrencode with the payload on
stdin, vpn:// and fields as text), exits, the panel's one-time login link."""

import json
import os
import re
import sys
import types
from unittest import mock

from kaskad import state
from kaskad.bot import gateway, menus
from kaskad.gw import exits, model, ops
from tests.bot.botcase import BotMixin
from tests.bot.fake_tg import OWNER
from tests.fakes_gw import VLESS_A, awg_line, install_parser, write_pool
from tests.fakes_nft import F443, save_state
from tests.gwcase import GwCase


class GwBotCase(BotMixin, GwCase):
    POOL = True

    def setUp(self):
        super(GwBotCase, self).setUp()
        self.runner = self.w.runner
        self.bot_setup()

    def gateway_on(self):
        r = self.w.runner
        self.ads = self.dummies()
        save_state([F443])
        if self.POOL:
            install_parser()
            awg = awg_line(2)
            write_pool([VLESS_A, awg])
            self.vid, self.aid = exits.exit_id(VLESS_A), exits.exit_id(awg)
            self.w.leg(2)
        ops.enable(r)
        ops.inbound_install(r, "wg")
        ops.inbound_install(r, "awg2")
        self.uid = ops.user_add(r, "Мама")["id"]
        self.did = ops.device_add(r, self.uid, "Телефон", kind="phone", platform="android", access=["wg"])["device"]["id"]
        self.dev = model.find_device(state.load(), self.did)

    def live(self, **kw):
        doc = {"v": 1, "ts": self.now - 5}
        doc.update(kw)
        self.write("/run/kaskad/live.json", json.dumps(doc))


class StatusTest(GwBotCase):
    def test_not_configured(self):
        save_state()
        self.make_app()
        self.press("gw")
        self.assertIn("не настроен", self.last_text())
        self.assertIn("17", self.last_text())

    def test_status_with_and_without_the_collector(self):
        self.gateway_on()
        self.make_app()
        self.press("gw")
        text = self.last_text()
        self.assertIn("🛡 <b>Шлюз</b>: включён", text)
        self.assertIn("Адрес для клиентов: <code>203.0.113.10</code>", text)
        self.assertIn("• WireGuard — — нет данных", text)
        self.assertIn("Сборщик ещё не прислал данных", text)
        self.assertEqual([d for _, d in self.buttons()][:3], ["gwp", "gwx", "gwl"])
        self.live(gateway={"enabled": True, "problems": ["DNS шлюза <не работает>"]},
                  inbounds={"wg": {"running": True, "sessions": 2, "ports": "51820/udp"},
                            "awg2": {"running": False, "sessions": 0}},
                  exits={self.vid: {"alive": True, "label": "Vienna"}, self.aid: {"alive": False}},
                  devices={self.did: {"online": True, "inbound": "wg", "rx": 1048576, "tx": 2048}})
        self.press("gw")
        text = self.last_text()
        self.assertIn("• WireGuard — 🟢 работает, сессий 2 (51820/udp)", text)
        self.assertIn("• AmneziaWG 2.0 — 🔴 НЕ работает", text)
        self.assertIn("<b>Выходы</b>: работают 1 из 2", text)
        self.assertIn("<b>Пользователи</b>: 1, устройств 1, в сети 1", text)
        self.assertIn("⚠️ DNS шлюза &lt;не работает&gt;", text)
        self.assertNotIn("Сборщик", text)
        self.write("/run/kaskad/live.json", '{"v": 1, "ts": "x", "inbounds": [1], "exits": "?", "gateway": '
                                            '{"problems": "строка"}, "devices": 5}')
        self.press("gw")                                                 # garbage = no data, not a crash
        self.assertIn("— нет данных", self.last_text())


class PeopleTest(GwBotCase):
    def setUp(self):
        super(PeopleTest, self).setUp()
        self.gateway_on()
        self.make_app()

    def test_people_user_device(self):
        self.press("gwp")
        self.assertIn("👤 Мама — устройств 1", self.last_text())
        self.assertIn(("👤 Мама (1)", "gwu:" + self.uid), self.buttons())
        self.press("gwu:" + self.uid)
        self.assertIn("📱 Телефон — Android · WireGuard", self.last_text())
        self.assertIn(("📱 Телефон", "gwd:" + self.did), self.buttons())
        self.live(devices={self.did: {"online": True, "inbound": "wg", "rx": 1048576, "tx": 2048}})
        self.press("gwd:" + self.did)
        text = self.last_text()
        self.assertIn("Платформа: Android · логин <code>%s</code>" % self.dev["login"], text)
        self.assertIn("Протоколы: WireGuard (10.77.1.10)", text)
        self.assertIn("Маршрут: группа «Основная» (как по умолчанию)", text)
        # rx — что прислало устройство (1 МБ), tx — что узел ему отдал (2 КБ): ↓ «скачало» = tx, ↑ «отправило» = rx
        self.assertIn("Сейчас: 🟢 в сети (WireGuard), ↓ 2,0 КБ ↑ 1,0 МБ", text)
        self.assertEqual([d for _, d in self.buttons()],
                         ["gwi:" + self.did, "gwr:d:" + self.did, "gwk:" + self.did, "gwt:" + self.did,
                          "gwdl:" + self.did, "gwu:" + self.uid])
        self.press("gwd:d-ffffffff")
        self.assertEqual(self.last_text(), "Устройства уже нет.")

    def test_pagination_of_people(self):
        with state.transaction() as st:
            for i in range(34):
                model.add_user(st, "Пользователь %02d" % i)
        self.press("gwp")
        self.assertEqual(len(self.data_of("gwu:")), 30)
        self.assertIn(("дальше ➡️", "gwp~30"), self.buttons())
        self.press("gwp~30")
        self.assertEqual(len(self.data_of("gwu:")), 5)


class AddDeviceTest(GwBotCase):
    POOL = False

    def setUp(self):
        super(AddDeviceTest, self).setUp()
        self.gateway_on()
        self.make_app()

    def test_add_with_inbounds_preselected_by_the_offers(self):
        self.press("gwa")
        self.assertEqual(self.buttons()[:2], [("👤 Мама", "gwau:" + self.uid), ("➕ Новый пользователь", "gwan")])
        self.press("gwau:" + self.uid)
        self.assertIn(("Android", "gwpl:android"), self.buttons())
        self.press("gwpl:android")
        self.assertEqual(self.last_text(), "Android. Название устройства (или <code>-</code> — «Телефон»):")
        self.say("Планшет <i>")
        self.assertEqual(self.buttons()[:3], [("✅ AmneziaWG 2.0", "gwat:awg2"), ("⬜ WireGuard", "gwat:wg"),
                                              ("✔️ Готово", "gwag")])
        self.assertIn("AmneziaVPN: ссылка vpn://", self.last_text())       # the note of the offer
        self.press("gwat:wg")
        self.assertEqual(self.buttons()[:2], [("✅ AmneziaWG 2.0", "gwat:awg2"), ("✅ WireGuard", "gwat:wg")])
        self.press("gwat:awg2")
        self.press("gwat:wg")
        self.press("gwag")
        self.assertIn("Отметьте хотя бы один протокол", self.last_text())    # nothing marked: not added
        self.press("gwat:awg2")
        self.press("gwag")
        d = [x for x in model.devices(state.load()) if x["name"] == "Планшет <i>"][0]
        self.assertEqual((d["platform"], d["kind"], d["access"], d["user"]), ("android", "phone", ["awg2"], self.uid))
        self.assertIn("✅ Устройство «Планшет &lt;i&gt;» добавлено, логин <code>%s</code>, протоколы: AmneziaWG 2.0."
                      % d["login"], self.last_text())
        self.assertEqual(self.buttons()[0], ("📤 Выдать настройки", "gwi:" + d["id"]))
        self.press("gwag")                                               # the dialog is over
        self.assertEqual(self.last_text(), menus.stale_button())
        self.press("gwau:" + self.uid)
        self.now += 31 * 60
        self.app.last_poll = self.now - 1                                # the bot kept polling meanwhile
        self.press("gwpl:android")
        self.assertEqual(self.last_text(), menus.dialog_expired())

    def test_new_user_first(self):
        self.press("gwan")
        self.say("Папа")
        self.assertIn("✅ Пользователь «Папа» добавлен.\nЧто это за устройство?", self.last_text())
        self.press("gwpl:linux")
        self.say("-")
        self.assertIn("Протоколы устройства «Компьютер» (Linux)", self.last_text())
        self.assertEqual(self.buttons()[0], ("✅ WireGuard", "gwat:wg"))   # Linux: WireGuard first
        self.press("gwag")
        u = model.find_user(state.load(), "Папа")
        self.assertEqual([x["name"] for x in model.devices(state.load(), u["id"])], ["Компьютер"])

    def test_a_menu_button_cancels_the_adding(self):
        self.press("gwau:" + self.uid)
        self.press("gw")
        self.press("gwpl:android")
        self.assertEqual(self.last_text(), menus.stale_button())


class ChangeTest(GwBotCase):
    def setUp(self):
        super(ChangeTest, self).setUp()
        self.gateway_on()
        self.make_app()

    def route(self):
        return model.find_device(state.load(), self.did).get("route")

    def test_route(self):
        self.press("gwr:d:" + self.did)
        data = [d for _, d in self.buttons()]
        self.assertEqual(data[:2], ["gwrs:d:%s:inherit" % self.did, "gwrs:d:%s:pool" % self.did])
        # REV-s1-t10-2 п. 3: the pool is the group «Основная» — the button says it as the screen does
        self.assertEqual(self.buttons()[1][0], "Группа «Основная» — живой выход группы")
        self.assertIn("gwrs:d:%s:%s" % (self.did, self.vid), data)
        self.assertIn("gwrs:d:%s:block" % self.did, data)
        self.press("gwrs:d:%s:%s" % (self.did, self.vid))
        self.assertEqual([d for _, d in self.buttons()][:2], ["gwrf:d:%s:%s:b" % (self.did, self.vid),
                                                              "gwrf:d:%s:%s:d" % (self.did, self.vid)])
        # T10 (D-003): «блок» маршрута — как у группы выхода, словами; данные кнопок прежние
        self.assertEqual([t for t, _ in self.buttons()][:2], ["Как у группы «Основная» — блок (по умолчанию)",
                                                              "Напрямую — с адреса сервера"])
        self.assertIn("Если выходы этого маршрута недоступны:", self.last_text())
        self.assertIsNone(self.route())                                  # only asked so far
        self.press("gwrf:d:%s:%s:d" % (self.did, self.vid))
        self.assertEqual(self.route(), {"via": "exit:" + self.vid, "fail": "direct"})
        self.assertIn("✅ Маршрут «Телефон»: выход «%s», при отказе — напрямую." % self.vid, self.last_text())
        self.press("gwrs:d:%s:block" % self.did)
        self.assertEqual(self.route(), {"via": "block", "fail": "direct"})  # a new target keeps the policy (Н1)
        self.press("gwrs:d:%s:inherit" % self.did)
        self.assertIsNone(self.route())
        self.assertIn("Как у пользователя «Мама» — сейчас: по умолчанию сервера: группа «Основная»", self.last_text())   # owner, 3.3.2
        self.press("gwr:u:" + self.uid)
        self.assertEqual(self.buttons()[0], ("Как по умолчанию — сейчас: группа «Основная»", "gwrs:u:%s:inherit" % self.uid))
        self.assertIn("«Как по умолчанию»: сменится маршрут по умолчанию", self.last_text())
        self.press("gwrs:u:%s:direct" % self.uid)
        self.assertEqual(model.find_user(state.load(), self.uid)["route"], {"via": "direct", "fail": "block"})
        self.press("gwrs:d:%s:e-zz" % self.did)
        self.assertEqual(self.last_text(), menus.old_button())

    def test_new_keys_and_delete(self):
        self.press("gwk:" + self.did)
        self.assertIn("Его подключения прервутся", self.last_text())
        self.assertIn("GW device add", self.read("/var/log/kaskad.log"))
        self.assertNotIn("GW device reset", self.read("/var/log/kaskad.log"))
        self.press("gwky:" + self.did)
        self.assertIn("✅ «Телефон»: новые ключи и пароли — WireGuard.", self.last_text())
        self.assertIn("GW device reset: Телефон", self.read("/var/log/kaskad.log"))
        self.press("gwdl:" + self.did)
        self.assertIn("Удалить устройство «Телефон»?", self.last_text())
        self.assertIsNotNone(model.find_device(state.load(), self.did))
        self.press("gwdly:" + self.did)
        self.assertEqual(self.last_text().split("\n")[0], "✅ Устройство «Телефон» удалено.")
        self.assertIsNone(model.find_device(state.load(), self.did))

    def test_exits(self):
        self.press("gwx")
        text = self.last_text()
        self.assertIn("🚪 <b>Выходы</b>: работают", text)
        # the kind of the exit key by the protocol's name, never the code «(vless)», «(awg 3.1)» (T-M6-332 №2)
        self.assertIn("Vienna (VLESS)", text)
        self.assertRegex(text, r"Amsterdam \(AmneziaWG \d\.\d\)")
        self.assertNotIn("(vless)", text)
        self.assertNotIn("(awg", text)

    def test_an_empty_pool_says_where_to_add_a_key(self):
        """3.2.21: the panel's button and the SSH menu with the number the menu really has (17 → 3 → 3 while the default
        route goes through the exits, else → 2); «без интернета» while the default route blocks."""
        write_pool([])
        self.press("gwx")
        text = self.last_text()
        self.assertIn("🚪 В пуле нет выходов.", text)
        self.assertIn("«Выходы» → «Добавить ключ»", text)
        self.assertIn("gokaskad → 17 → 3 → 3 («Добавить ключ выхода»)", text)
        self.assertIn("Устройства шлюза сейчас без интернета", text)
        ops.default_route(self.w.runner, "direct")
        self.press("gwx")
        self.assertIn("gokaskad → 17 → 3 → 2 («Добавить ключ выхода»)", self.last_text())
        self.assertNotIn("без интернета", self.last_text())


FAKE_QRENCODE = """#!%s
import hashlib, json, os, sys
data = sys.stdin.buffer.read()
with open(os.environ["FAKE_QR_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if b"BROKEN" in data:
    sys.stderr.write("Failed to encode the input data\\n")
    sys.exit(1)
sys.stdout.buffer.write(b"\\x89PNG\\r\\n\\x1a\\n" + hashlib.sha256(data).hexdigest().encode())
"""


class IssueTest(GwBotCase):
    POOL = False

    def setUp(self):
        super(IssueTest, self).setUp()
        self.gateway_on()
        self.make_app()

    def test_conf_as_a_document(self):
        ops.device_grant(self.w.runner, self.did, "awg2")
        self.press("gwi:" + self.did)
        msgs = self.tg.sent("sendMessage", "sendDocument")
        self.assertIn("📤 <b>Настройки «Телефон»</b> — AmneziaWG 2.0 (Android)", msgs[0].text)   # Android: awg2 first
        doc = self.tg.sent("sendDocument")[0]
        login = self.dev["login"]
        self.assertEqual(doc.files["document"][0], "%s-awg2.conf" % login)
        self.assertIn(b"PrivateKey = DEVKEY-%s" % self.did.encode(), doc.files["document"][1])
        self.assertIn(("Другой протокол: WireGuard", "gwis:%s:wg" % self.did), self.buttons())
        self.press("gwis:%s:wg" % self.did)
        self.assertEqual(self.tg.sent("sendDocument")[-1].files["document"][0], "%s-wg.conf" % login)
        self.assertNotIn("DEVKEY", self.read("/var/log/kaskad.log"))

    def test_the_steps_speak_telegram(self):
        """T-G1b N4: the bot asks the core for steps in its own words (a link is a message — a tap copies it; a file is
        a document) and sends them with the header, before the records; no words of the SSH menu."""
        from kaskad.gw import issue_os
        ops.device_grant(self.w.runner, self.did, "awg2")
        self.press("gwi:" + self.did)
        head = self.tg.sent("sendMessage")[0].text
        steps = issue_os.howto("android", "awg2", where="bot")
        self.assertIn("\n\n<b>Как подключить</b>\n1. ", head)
        self.assertIn("2. " + steps[1], head)
        self.assertIn("Telegram скопирует её целиком", head)
        for menu_word in ("PuTTY", "Сохранить в файлы", "этого экрана", "gokaskad"):
            self.assertNotIn(menu_word, head)

    def rich(self, *records):
        ad = self.ads["wg"]

        def issue(st, sec, device, os_name=None):
            recs = [dict({"filename": None, "notes": []}, **r) for r in records]
            return dict(recs[0], inbound="wg", items=recs)

        ad.issue = issue

    def test_qr_png_links_and_fields(self):
        self.rich({"format": "qr", "title": "QR для AmneziaVPN", "body": ["SECRET-QR-1", "SECRET-QR-2"],
                   "notes": ["Кодов: 2"]},
                  {"format": "vpn", "title": "Ссылка", "body": "vpn://SECRETVPN<&>"},
                  {"format": "fields", "title": "Данные", "body": [["Сервер", "203.0.113.10"], ["Пароль", "p<a>ss&"]]},
                  {"format": "guide", "title": "Команда", "body": "sudo wg-quick up ./kaskad-wg.conf"},
                  {"format": "link", "title": "Telegram", "body": "tg://socks?server=203.0.113.10&port=1080"})
        bindir = self.path("/fakebin")
        os.makedirs(bindir)
        exe = os.path.join(bindir, "qrencode")
        with open(exe, "w") as f:
            f.write(FAKE_QRENCODE % sys.executable)
        os.chmod(exe, 0o755)
        qrlog = self.path("/qr.log")
        with mock.patch.object(gateway, "qrencode_path", lambda: exe), \
                mock.patch.dict(os.environ, {"FAKE_QR_LOG": qrlog}):
            self.press("gwi:" + self.did)
        photos = self.tg.sent("sendPhoto")
        import hashlib
        self.assertEqual([p.files["photo"][1] for p in photos],
                         [b"\x89PNG\r\n\x1a\n" + hashlib.sha256(s).hexdigest().encode() for s in (b"SECRET-QR-1", b"SECRET-QR-2")])
        self.assertEqual(photos[0].params["caption"], "<b>QR для AmneziaVPN</b> (1 из 2)\n• Кодов: 2")
        with open(qrlog) as f:
            argv = f.read()
        self.assertNotIn("SECRET", argv)                                 # the payload went on stdin
        self.assertIn('"-t", "PNG"', argv)
        texts = self.tg.texts(OWNER)
        self.assertIn("<b>Ссылка</b>\n<code>vpn://SECRETVPN&lt;&amp;&gt;</code>", texts)
        self.assertIn("<b>Данные</b>\nСервер: <code>203.0.113.10</code>\nПароль: <code>p&lt;a&gt;ss&amp;</code>", texts)
        self.assertIn("<b>Команда</b>\n<pre>sudo wg-quick up ./kaskad-wg.conf</pre>", texts)
        self.assertIn("<b>Telegram</b>\n<code>tg://socks?server=203.0.113.10&amp;port=1080</code>", texts)
        self.assertIn("Готово: 5 записей.", texts[-1])
        self.assertNotIn("SECRET", self.read("/var/log/kaskad.log"))

    def test_qrencode_missing_or_failing(self):
        self.rich({"format": "qr", "title": "QR", "body": ["SECRET"]})
        with mock.patch.object(gateway, "qrencode_path", lambda: None):
            self.press("gwi:" + self.did)
        self.assertIn("нет программы qrencode", self.tg.texts(OWNER)[1])
        self.assertIn("apt install qrencode", self.tg.texts(OWNER)[1])
        self.assertIn("17", self.tg.texts(OWNER)[1])
        self.assertEqual(self.tg.sent("sendPhoto"), [])
        self.rich({"format": "qr", "title": "QR", "body": ["BROKEN"]})
        bindir = self.path("/fakebin")
        os.makedirs(bindir)
        exe = os.path.join(bindir, "qrencode")
        with open(exe, "w") as f:
            f.write(FAKE_QRENCODE % sys.executable)
        os.chmod(exe, 0o755)
        with mock.patch.object(gateway, "qrencode_path", lambda: exe), \
                mock.patch.dict(os.environ, {"FAKE_QR_LOG": self.path("/qr.log")}):
            self.press("gwi:" + self.did)
        self.assertTrue(any("❌ qrencode не нарисовал QR: Failed to encode" in t for t in self.tg.texts(OWNER)))

    def test_a_long_vpn_link_goes_as_a_file(self):
        self.rich({"format": "vpn", "title": "Ссылка", "body": "vpn://" + "A" * 5000})
        self.press("gwi:" + self.did)
        doc = self.tg.sent("sendDocument")[0]
        self.assertEqual(doc.files["document"][0], "kaskad-wg-vpn.txt")
        self.assertTrue(doc.files["document"][1].startswith(b"vpn://AAAA"))


class PanelLinkTest(GwBotCase):
    def setUp(self):
        super(PanelLinkTest, self).setUp()
        save_state()
        self.make_app()

    def fake(self, result):
        """kaskad.panel.auth of WP-P1: login_link(ttl=3600, by="bot") -> {"url": str|None, "token": str, "ttl": int}."""
        mod = types.ModuleType("kaskad.panel.auth")
        self.calls = []

        def login_link(ttl=None, by=None):
            self.calls.append((ttl, by))
            return result

        mod.login_link = login_link
        return mock.patch.dict(sys.modules, {"kaskad.panel.auth": mod})

    def test_no_panel_installed(self):
        with mock.patch.dict(sys.modules, {"kaskad.panel.auth": None}):
            self.press("gwl")
        self.assertIn("🔑 Веб-панель не установлена", self.last_text())
        with self.fake({"url": None, "token": "T", "ttl": 3600}):          # module there, service not installed
            self.press("gwl")
        self.assertEqual(self.calls, [(3600, "bot")])
        self.assertIn("🔑 Веб-панель не установлена", self.last_text())

    def test_tunnel_only(self):
        with self.fake({"url": "http://127.0.0.1:1980/login?t=ONE-TIME&1", "token": "ONE-TIME&1", "ttl": 3600}):
            self.press("gwl")
        msg = self.last()
        self.assertEqual(msg.method, "sendMessage")
        self.assertIn("Панель открыта только на самом сервере.", msg.text)
        self.assertIn("<code>ssh -L 1980:127.0.0.1:1980 root@203.0.113.10</code>", msg.text)
        self.assertIn("действует 60 мин):\nhttp://127.0.0.1:1980/login?t=ONE-TIME&amp;1", msg.text)
        self.assertEqual(msg.params["link_preview_options"], {"is_disabled": True})
        self.assertNotIn("ONE-TIME", self.read("/var/log/kaskad.log"))

    def test_open_mode(self):
        with self.fake({"url": "https://203.0.113.10:41234/login?t=TOK", "token": "TOK", "ttl": 1800,
                        "fingerprint": "AB:CD"}):
            self.press("gwl")
        text = self.last_text()
        self.assertIn("Ссылка одноразовая, действует 30 мин:\nhttps://203.0.113.10:41234/login?t=TOK", text)
        self.assertNotIn("ssh -L", text)
        self.assertIn("<code>AB:CD</code>", text)

    def test_the_link_leaves_the_chat_when_it_expires(self):
        # 3.3.7.5 (З-5, П-1): the link carries the secret path of the panel and a sign-in token — the message is
        # deleted after 5 minutes (as «Показать адрес»), though the link itself lives 30
        with self.fake({"url": "https://203.0.113.10:41234/app-%s/login?t=TOK" % ("s" * 86), "token": "TOK",
                        "ttl": 1800}):
            self.press("gwl")
        self.now += 299
        self.app.expire_due()
        self.assertEqual(self.tg.sent("deleteMessage"), [])
        self.now += 2
        self.poll()
        dels = self.tg.sent("deleteMessage")
        self.assertEqual(len(dels), 1)
        self.assertEqual(dels[0].params["chat_id"], OWNER)


class TraceTest(GwBotCase):
    """«Куда пойдёт трафик?» in the bot: from a device's screen or the gateway's (a new device with the defaults), then
    every text is a domain or an IP — the answer of kaskad.gw.trace, step by step; a bad text keeps the dialog."""

    def setUp(self):
        super(TraceTest, self).setUp()
        self.gateway_on()
        exits.save_snapshot({"at": self.now, "pool": [self.vid, self.aid],
                             "exits": {self.vid: {"alive": True, "since": self.now, "detail": "xray: доступен"},
                                       self.aid: {"alive": True, "since": self.now, "detail": "работает"}}})
        self.make_app()

    def test_trace_from_the_device_and_the_gateway(self):
        self.press("gw")
        self.assertIn("gwt:-", [d for _, d in self.buttons()])
        self.press("gwt:" + self.did)
        self.assertIn("Куда пойдёт трафик?", self.last_text())
        self.assertIn("«Телефон»", self.last_text())
        self.say("youtube.com")
        text = self.last_text()
        self.assertIn("1. Устройство «Телефон» (Мама)", text)
        self.assertIn("<b>Через ", text)
        self.assertIn("<i>Сайт откроется с адреса", text)                 # where the site sees it from — at once
        self.assertIn(("⬅️ Устройство", "gwd:" + self.did), self.buttons())
        self.say("not a domain")                                         # the dialog stays: try again
        self.assertIn("❌ «not a domain» — не домен", self.last_text())
        self.say("8.8.8.8")
        self.assertIn("8.8.8.8", self.last_text())
        self.press("gwt:-")
        self.assertIn("новое устройство с настройками по умолчанию", self.last_text())
        self.say("https://www.youtube.com/watch?v=1")
        self.assertIn("Новое устройство с настройками по умолчанию", self.last_text())
        self.press("gwt:d-ffffffff")
        self.assertEqual(self.last_text(), "Устройства уже нет.")
        self.press("gwt:../x")
        self.assertNotIn("Куда пойдёт", self.last_text())


class EngineTest(GwBotCase):
    """The engine of AmneziaWG in the bot (k3/SPEC-awg-engine.md §3–§4): the advice on the gateway screen after an
    update (one button), the explanation instead of it with a line 3.1, the engine screen and the switch as a job."""
    POOL = False

    def setUp(self):
        super(EngineTest, self).setUp()
        self.gateway_on()

    def engine(self, **rec):
        with state.transaction() as st:
            st["gateway"]["awg_engine"] = rec

    def test_the_advice_after_an_update_and_the_switch(self):
        from kaskad.gw import awgengine
        self.engine(pending=True)
        self.make_app()
        self.press("gw")
        self.assertIn("⚙️ <b>Совет:</b> перевести AmneziaWG на модуль ядра", self.last_text())
        self.assertEqual(self.buttons()[0], ("🚀 Перейти на модуль ядра", "gwek"))
        self.assertIn(("⚙️ Движок AmneziaWG", "gwe"), self.buttons())
        self.press("gwek")
        self.assertIn("≈200 МБ", self.last_text())
        self.assertIn(("✅ Да, перейти", "gweky"), self.buttons())
        calls = []
        with mock.patch.object(awgengine, "switch", lambda runner, target, **kw: calls.append((target, kw.get("why")))
                               or {"ok": True, "engine": target, "was": "program", "backup": "/x", "warnings": []}):
            self.press("gweky")
        self.assertEqual(calls, [("kernel", "person:bot")])
        self.assertIn("✅ AmneziaWG работает: модуль ядра", self.last_text())

    def test_an_inbound_31_goes_onto_the_module_with_its_set(self):
        with state.transaction() as st:
            st["gateway"]["inbounds"]["awg3"] = {"enabled": True, "port": 41003}
            st["gateway"]["awg_engine"] = {"pending": True}
        self.make_app()
        self.press("gw")
        self.assertNotIn("пока нельзя", self.last_text())
        self.assertEqual(self.buttons()[0], ("🚀 Перейти на модуль ядра", "gwek"))

    def test_a_leg_15_explains_instead_of_the_button(self):
        from kaskad.gw import exits
        leg = {"id": "e-0000000003", "kind": "awg", "iface": "kawg3", "version": "", "name": "", "host": "x",
               "where": "AmneziaWG · 198.51.100.3"}
        leg31 = dict(leg, id="e-0000000004", iface="kawg4", version="3.1", where="AmneziaWG 3.1 · 198.51.100.4")
        with state.transaction() as st:
            st["gateway"]["awg_engine"] = {"pending": True}
        with mock.patch.object(exits, "pool", lambda runner=None, parser=None, roles=("abroad",): [leg31, dict(leg31, version="1.5")]):
            self.make_app()
            self.press("gw")
            self.assertNotIn("пока нельзя", self.last_text())       # legs of 3.1 and 1.5: onto the module too
            self.assertNotIn("#222", self.last_text())
        with state.transaction() as st:
            st["gateway"]["awg_engine"] = {"pending": True}
        with mock.patch.object(exits, "pool", lambda runner=None, parser=None, roles=("abroad",): [leg]):
            self.make_app()
            self.press("gw")
            self.assertIn("Модуль ядра AmneziaWG пока нельзя:</b> Выход «", self.last_text())
            self.assertIn("ключ AmneziaWG неизвестной линии", self.last_text())
            self.assertNotIn("пока не умеет", self.last_text())
            self.assertNotIn("gwek", [d for _, d in self.buttons()])
            self.press("gwe")
            self.assertIn("Сейчас мешает", self.last_text())
            self.assertNotIn("gwek", [d for _, d in self.buttons()])

    def test_nothing_is_offered_to_a_node_that_has_not_been_asked(self):
        self.make_app()
        self.press("gw")
        self.assertNotIn("Совет:", self.last_text())
        self.assertEqual([d for _, d in self.buttons()][:3], ["gwp", "gwx", "gwl"])
        self.press("gwe")
        self.assertIn("Движок AmneziaWG: программа", self.last_text())


class MtuTest(GwBotCase):
    """§6.1: a narrow card of the node on the gateway screen — the warning and two buttons: new settings with a
    smaller MTU (a confirmation, then the devices to give them to) and «оставить как есть»."""
    POOL = False

    def setUp(self):
        super(MtuTest, self).setUp()
        self.gateway_on()
        ops.device_grant(self.w.runner, self.did, "awg2")
        with state.transaction() as st:
            st["gateway"]["inbounds"]["awg2"]["params"] = {"S4": "60", "MTU": "1280"}
            st["gateway"]["mtu"] = {"nic": {"dev": "eth0", "mtu": 1400}}
        self.write("/proc/net/route", "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
                                      "eth0\t00000000\t0102A8C0\t0003\t0\t0\t0\t00000000\n")
        self.write("/sys/class/net/eth0/mtu", "1400\n")

    def test_warning_reissue_with_the_devices(self):
        self.make_app()
        self.press("gw")
        self.assertIn("⚠️ AmneziaWG 2.0: карта сервера 1400 уже, чем нужно клиентам с MTU 1280 при S4 = 60",
                      self.last_text())
        self.assertEqual(self.buttons()[:2], [("📉 Новые настройки (MTU 1248)", "gwmr:awg2"), ("✋ Оставить", "gwmk:awg2")])
        self.press("gwmr:awg2")
        self.assertIn("MTU в настройках клиентов станет 1248 вместо 1280", self.last_text())
        self.assertIn(("✅ Да", "gwmry:awg2"), self.buttons())
        self.press("gwmry:awg2")
        self.assertIn("✅ AmneziaWG 2.0: MTU клиентов 1280 → 1248", self.last_text())
        self.assertIn("Телефон (Мама)", self.last_text())
        self.assertEqual(state.load()["gateway"]["inbounds"]["awg2"]["params"]["MTU"], "1248")
        self.press("gw")
        self.assertNotIn("gwmr:awg2", [d for _, d in self.buttons()])
        self.press("gwmr:awg2")
        self.assertIn("менять нечего", self.last_text())

    def test_keep_and_bad_input(self):
        self.make_app()
        self.press("gwmk:awg2")
        self.assertIn("Оставлено как есть", self.last_text())
        self.assertEqual(state.load()["gateway"]["mtu"]["keep"], {"awg2": 1400})
        self.press("gw")
        self.assertNotIn("уже, чем нужно", self.last_text())
        self.assertEqual(state.load()["gateway"]["inbounds"]["awg2"]["params"]["MTU"], "1280")
        self.press("gwmry:l2tp")
        self.assertEqual(self.last_text(), "Неизвестный протокол.")

    def test_s4_below_1232_list_then_new_parameters(self):
        from kaskad.vendor import awgkeys
        with state.transaction() as st:
            st["gateway"]["inbounds"]["awg2"]["params"] = dict(awgkeys.gen_params("2.0"), S4="120")
            st["gateway"]["mtu"]["nic"]["mtu"] = 1500
        self.write("/sys/class/net/eth0/mtu", "1500\n")
        self.make_app()
        self.press("gw")
        self.assertIn("⚠️ AmneziaWG 2.0: S4 = 120 — чтобы мобильные сети не тормозили, сервер уменьшил MTU до 1200",
                      self.last_text())
        self.assertEqual(self.buttons()[:2], [("📉 Новые настройки (S4 ≤ 40)", "gwms:awg2"), ("✋ Оставить", "gwmk:awg2")])
        self.press("gwms:awg2")
        self.assertIn("перестанут подключаться", self.last_text())
        self.assertIn("• Телефон (Мама)", self.last_text())
        self.assertEqual(state.load()["gateway"]["inbounds"]["awg2"]["params"]["S4"], "120")    # nothing yet
        self.press("gwmsy:awg2")
        self.assertIn("✅ AmneziaWG 2.0: S4 120 →", self.last_text())
        self.assertTrue(15 <= int(state.load()["gateway"]["inbounds"]["awg2"]["params"]["S4"]) <= 40)
        self.press("gw")
        # F1 (3.3.7.15): the list «ждут» names a protocol by registry.title (here the dummy adapter's title)
        from kaskad.gw import registry
        self.assertIn("⏳ %s: ждут новых настроек — Телефон (Мама)" % registry.title("awg2"), self.last_text())
        self.assertNotIn("gwms:awg2", [d for _, d in self.buttons()])
        self.press("gwms:awg2")
        self.assertIn("менять нечего", self.last_text())


CYRILLIC = re.compile("[А-Яа-яЁё]")


class EnglishTest(GwBotCase):
    """3.3.2: the language of the server (KASKAD_LANG of /etc/kaskad/config) is the language of the bot — the gateway,
    people, a device, adding a device and issuing speak English; only the person's own data (names) stay as typed."""
    POOL = False

    def setUp(self):
        super(EnglishTest, self).setUp()
        self.write_config(KASKAD_LANG="en")
        self.gateway_on()
        self.make_app()

    def english(self, text, *data):
        for d in ("Мама", "Телефон") + data:
            text = text.replace(d, "")
        self.assertIsNone(CYRILLIC.search(text), text)

    def screen(self, *data):
        self.english(self.last_text(), *data)
        for label, _ in self.buttons():
            self.english(label, *data)

    def test_gateway_people_device(self):
        self.press("gw")
        text = self.last_text()
        self.assertIn("🛡 <b>Gateway</b>: on", text)
        self.assertIn("Client address: <code>203.0.113.10</code>", text)
        self.assertIn("• WireGuard — — no data", text)
        self.assertIn("<b>Users</b>: 1, devices: 1", text)
        self.assertIn("The collector hasn’t sent any data yet", text)
        self.assertIn(("👥 Users & devices", "gwp"), self.buttons())
        self.assertIn(("🌐 Domain", "sd"), self.buttons())
        self.screen()
        self.press("gwp")
        self.assertIn("👤 Мама — devices: 1", self.last_text())
        self.screen()
        self.press("gwu:" + self.uid)
        self.assertIn("📱 Телефон — Android · WireGuard", self.last_text())
        self.assertIn("Route: default (group “Main”)", self.last_text())
        self.screen()
        self.live(devices={self.did: {"online": False}})
        self.press("gwd:" + self.did)
        text = self.last_text()
        self.assertIn("Platform: Android · login <code>%s</code>" % self.dev["login"], text)
        self.assertIn("Route: group “Main” (default)", text)
        self.assertIn("Now: offline", text)
        self.assertEqual(self.buttons()[0], ("📤 Issue settings", "gwi:" + self.did))
        self.screen()
        self.press("gwr:d:" + self.did)
        self.assertEqual(self.buttons()[0], ("Same as user “Мама” — now: server default: group “Main”", "gwrs:d:%s:inherit" % self.did))
        self.screen()
        self.press("gwrs:d:%s:direct" % self.did)
        self.assertIn("✅ Route of “Телефон”: direct.", self.last_text())
        self.press("gwd:d-ffffffff")
        self.assertEqual(self.last_text(), "This device no longer exists.")
        self.press("gwk:" + self.did)
        self.screen()
        self.press("gwdl:" + self.did)
        self.assertIn("🗑 Delete the device “Телефон”?", self.last_text())
        self.screen()

    def test_add_device_and_issue(self):
        self.press("gwa")
        self.assertIn("Whose is it?", self.last_text())
        self.assertIn(("➕ New user", "gwan"), self.buttons())
        self.screen()
        self.press("gwau:" + self.uid)
        self.assertIn("What kind of device is it?", self.last_text())
        self.assertIn(("Keenetic router", "gwpl:keenetic"), self.buttons())
        self.screen()
        self.press("gwpl:android")
        self.assertEqual(self.last_text(), "Android. The device name (or <code>-</code> for “Phone”):")
        self.say("-")
        self.assertIn("Protocols of the device “Phone” (Android):", self.last_text())
        self.assertIn(("✔️ Done", "gwag"), self.buttons())
        self.english("\n".join(l for l in self.last_text().splitlines()               # notes of the offers: the core's
                                if not l.startswith(("✅", "⬜", "•"))))
        self.press("gwag")
        d = [x for x in model.devices(state.load()) if x["name"] == "Phone"][0]
        self.assertIn("✅ The device “Phone” is added, login <code>%s</code>, protocols: AmneziaWG 2.0." % d["login"],
                      self.last_text())
        self.screen()
        self.press("gwi:" + d["id"])
        msgs = self.tg.sent("sendMessage", "sendDocument")
        head = [m.text for m in msgs if m.method == "sendMessage" and "📤" in (m.text or "")][-1]
        self.assertIn("📤 <b>Settings of “Phone”</b> — AmneziaWG 2.0 (Android)", head)
        self.assertIn("⚠️ These are the device’s keys", head)
        self.assertIn("<b>How to connect</b>", head)
        self.assertIn("Done: 1 record.", self.last_text())
        self.screen()

    def test_qrencode_missing_and_the_panel(self):
        ad = self.ads["wg"]
        ad.issue = lambda st, sec, device, os_name=None: dict(inbound="wg", items=[
            {"format": "qr", "title": "QR", "body": ["SECRET"], "filename": None, "notes": []}])
        with mock.patch.object(gateway, "qrencode_path", lambda: None):
            self.press("gwi:" + self.did)
        text = [t for t in self.tg.texts(OWNER) if "qrencode" in t][-1]
        self.assertIn("No QR code: the server has no qrencode program", text)
        self.english(text)
        with mock.patch.dict(sys.modules, {"kaskad.panel.auth": None}):
            self.press("gwl")
        self.assertIn("🔑 The web panel isn’t installed", self.last_text())
        self.screen()

    def test_the_reply_button_in_both_languages(self):
        self.say("🛡 Gateway")
        self.assertIn("🛡 <b>Gateway</b>: on", self.last_text())
        self.say("🛡 Шлюз")                                              # a keyboard of the previous language
        self.assertIn("🛡 <b>Gateway</b>: on", self.last_text())

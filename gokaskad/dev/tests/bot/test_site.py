"""«🌐 Домен» in the bot (docs/k3/SPEC-wave4.md §4.1, B-лицо) on the fake node of the site tests (a fake certbot, DNS
answers without a resolver, the clock of 2026-10-07): the screen, the domain by text, DNS now and waiting, the
certificate as a job («⏳ …», then the result) and every refusal in words, SSTP / OpenConnect with the number of
devices and a confirmation, removing the domain with a confirmation, the panel's link on the domain's certificate."""

import re
import sys
import types
from unittest import mock

from kaskad import state
from kaskad.bot import menus, registry
from kaskad.site import dns, model
from tests.bot.botcase import BotMixin
from tests.gw.tunnel_case import device
from tests.gwcase import GwCase
from tests.helpers import zone
from tests.site.sitecase import DOMAIN, SiteFakes
from tests.site.test_cert import NGINX_80


class SiteBotCase(SiteFakes, BotMixin, GwCase):
    def setUp(self):
        super(SiteBotCase, self).setUp()
        self.runner = self.w.runner
        self.bot_setup()
        self.site_fakes()
        state.save(state.empty())

    def texts(self):
        return self.tg.texts()

    def gateway_with_sstp(self):
        st = state.load()
        st["gateway"] = {"enabled": False, "host": "203.0.113.10",
                         "users": [{"id": "u-1", "name": "Мама", "route": None, "enabled": True, "added": "x"}],
                         "devices": [device("d-win", "Ноутбук", "windows", ["sstp", "ocserv"]),
                                     device("d-and", "Пиксель", "android", ["sstp"])],
                         "inbounds": {"sstp": {"enabled": True, "port": 4433}}}
        state.save(st)


class ScreenTest(SiteBotCase):
    def test_the_button_is_in_the_main_menu_and_on_the_gateway_screen(self):
        registry.load()
        self.assertIn(("🌐 Домен", "sd"), [b for row in menus.main_inline() for b in row])
        self.assertIn("🌐 Домен", [b for row in menus.reply_rows() for b in row])
        self.make_app()
        self.press("gw")
        self.assertIn("sd", [d for _, d in self.buttons()])

    def test_reply_keyboard_opens_the_same_screen(self):
        self.make_app()
        self.say("🌐 Домен")
        self.assertIn("🌐 <b>Домен</b> не задан", self.last_text())

    def test_without_a_domain(self):
        self.make_app()
        self.press("sd")
        text = self.last_text()
        self.assertIn("🌐 <b>Домен</b> не задан", text)
        self.assertIn("A-запись: значение — <code>203.0.113.10</code>", text)
        self.assertEqual([d for _, d in self.buttons()], ["sdn", "m"])

    def test_status_with_the_certificate(self):
        self.gateway_with_sstp()
        self.with_domain(issued="le")
        self.make_app()
        self.press("sd")
        text = self.last_text()
        self.assertIn("🌐 <b>Домен</b>: <code>vpn.example.com</code>", text)
        self.assertIn("DNS: ✅ все A-записи ведут на этот сервер", text)
        self.assertIn("Сертификат: 🔒 Let's Encrypt до 30.12.2026 (осталось 83 дн.)", text)
        self.assertIn("Им пользуются: пока никто", text)
        self.assertIn("Адрес для клиентов — не домен (сменить: SSH-меню 17 → 4)", text)
        self.assertIn("Сайт-маскировка: выключена (сертификат без сайта)", text)
        self.assertEqual([d for _, d in self.buttons()], ["sdd", "sdc", "sdu", "sdm", "sdt:1", "sdn", "sdx", "sd", "m"])
        self.assertIn("🔒 Проверить сертификат", [t for t, _ in self.buttons()])
        self.assertIn("🌐 Включить сайт (случайный шаблон)", [t for t, _ in self.buttons()])

    def test_without_lets_encrypt_the_site_is_not_offered(self):
        self.with_domain()
        self.make_app()
        self.press("sd")
        self.assertEqual([d for _, d in self.buttons()], ["sdd", "sdc", "sdu", "sdn", "sdx", "sd", "m"])

    def test_port_80_taken_is_on_the_screen(self):
        self.with_domain()
        self.w.ss = NGINX_80
        self.make_app()
        self.press("sd")
        self.assertIn("⚠️ Порт 80/tcp занят:", self.last_text())
        self.assertIn("nginx", self.last_text())


class DomainTest(SiteBotCase):
    def test_set_by_text_bad_name_keeps_the_dialog(self):
        self.make_app()
        self.press("sdn")
        self.assertIn("Пришлите домен", self.last_text())
        self.say("not a domain")
        self.assertTrue(self.last_text().startswith("❌"))
        self.assertIn("Ещё раз — домен:", self.last_text())
        self.say("vpn.example.com")
        text = self.last_text()
        self.assertIn("✅ Домен <code>vpn.example.com</code> задан.", text)
        self.assertIn("DNS: ✅", text)
        self.assertEqual(self.site()["domain"], DOMAIN)
        self.assertIn("sdc", [d for _, d in self.buttons()])
        self.say("other.example.org")                       # the dialog is over: a text is no longer a domain
        self.assertEqual(self.site()["domain"], DOMAIN)

    def test_idn(self):
        self.make_app()
        self.press("sdn")
        self.answers[dns.A] = ("ok", ["203.0.113.10"])
        self.say("впн.пример.рф")
        self.assertIn("<code>впн.пример.рф</code>", self.last_text())
        self.assertTrue(self.site()["domain"].startswith("xn--"))


class ChangeTest(SiteBotCase):
    """T-M6-qs330d №1: a working domain is replaced only after the new name is checked and has its certificate."""

    def test_no_a_record_keeps_the_working_domain(self):
        self.with_domain(issued="le")
        self.answers_for["other.example.org"] = {dns.A: ("nxdomain", []), dns.AAAA: ("nxdomain", [])}
        self.make_app()
        self.press("sdn")
        self.say("other.example.org")
        texts = self.texts()
        self.assertIn("⏳ Проверяю DNS <code>other.example.org</code>", texts[-2])
        self.assertIn("❌ Домен не сменён.", texts[-1])
        self.assertIn("vpn.example.com работает как раньше", texts[-1])
        s = self.site()
        self.assertEqual((s["domain"], s["cert"]), (DOMAIN, "letsencrypt"))

    def test_switch(self):
        self.with_domain(issued="le")
        self.certbot.pem = "other"
        self.make_app()
        self.press("sdn")
        self.say("other.example.org")
        self.assertIn("✅ Домен сменён: <code>vpn.example.com</code> → <code>other.example.org</code>", self.last_text())
        s = self.site()
        self.assertEqual((s["domain"], s["cert"]), ("other.example.org", "letsencrypt"))


class DnsTest(SiteBotCase):
    def test_not_ready_then_waiting_up_to_5_minutes(self):
        self.with_domain()
        self.answers[dns.A] = ("ok", ["198.51.100.7"])
        self.make_app()
        self.press("sdd")
        text = self.last_text()
        self.assertIn("DNS: ⚠️ A-записи ведут не на этот сервер (A: 198.51.100.7", text)
        self.assertIn("<i>Домен vpn.example.com указывает на 198.51.100.7", text)
        self.assertEqual([d for _, d in self.buttons()], ["sdw", "sdd", "sd"])
        self.press("sdw")
        texts = self.texts()
        self.assertIn("⏳ Жду DNS до 5 минут", texts[-2])
        self.assertIn("⌛ За 5 минут DNS не обновился.", texts[-1])
        self.assertEqual(set(self.slept), {15})               # every 15 s, not longer than 5 minutes
        self.assertLessEqual(sum(self.slept), 300)

    def test_waiting_ends_when_ready(self):
        self.with_domain()
        self.make_app()
        self.press("sdw")
        self.assertIn("✅ DNS готов.", self.last_text())
        self.assertEqual(self.slept, [])
        self.assertEqual([d for _, d in self.buttons()], ["sdc", "sd"])

    def test_partial_allows_issuing_anyway(self):
        self.with_domain()
        self.answers[dns.A] = ("ok", ["203.0.113.10", "198.51.100.7"])
        self.make_app()
        self.press("sdd")
        self.assertIn("sdc", [d for _, d in self.buttons()])


class IssueTest(SiteBotCase):
    def test_success_first_the_hourglass_then_the_result(self):
        self.gateway_with_sstp()
        self.with_domain()
        self.make_app()
        self.press("sdc")
        texts = self.texts()
        self.assertIn("⏳ Выпускаю сертификат Let's Encrypt для <code>vpn.example.com</code>", texts[-2])
        self.assertIn("✅ Сертификат Let's Encrypt для vpn.example.com выпущен.", texts[-1])
        self.assertIn("Действует до 30.12.2026 (осталось 83 дн.), продлевается сам.", texts[-1])
        self.assertIn("⚠️ Почта для Let's Encrypt не задана", texts[-1])
        self.assertEqual([d for _, d in self.buttons()], ["sdu", "sd"])
        self.assertEqual(self.site()["cert"], "letsencrypt")

    def refused(self, mode=None):
        if mode:
            self.certbot_present()
            self.certbot.mode = mode
        self.make_app()
        self.press("sdc")
        self.assertIn("⏳ Выпускаю", self.texts()[-2])
        self.assertEqual([d for _, d in self.buttons()], ["sdc", "sd"])
        return self.last_text()

    def test_limit_with_the_time_to_retry(self):
        self.with_domain()
        with zone("UTC"):                                    # the clock of the server: UTC on most VPS
            text = self.refused("limit")
        self.assertIn("❌ Сертификат не выпущен: лимит Let's Encrypt.", text)
        self.assertIn("Повторить можно 08.10, 12:00 UTC", text)        # 3.3.2: the date of the language

    def test_net(self):
        self.with_domain()
        text = self.refused("net")
        self.assertIn("❌ Сертификат не выпущен: нет связи с Let's Encrypt.", text)
        self.assertIn("не достучался до Let's Encrypt", text)

    def test_dns_at_lets_encrypt(self):
        self.with_domain()
        text = self.refused("dns")
        self.assertIn("❌ Сертификат не выпущен: Let's Encrypt не достучался до сервера по домену.", text)
        self.assertIn("порт 80", text)

    def test_dns_points_elsewhere_before_certbot(self):
        self.with_domain()
        self.answers[dns.A] = ("ok", ["198.51.100.7"])
        text = self.refused()
        self.assertIn("Let's Encrypt не достучался до сервера по домену", text)
        self.assertIn("198.51.100.7", text)
        self.assertEqual(self.certbot.calls, [])

    def test_port_80_names_who_holds_it(self):
        self.with_domain()
        self.w.ss = NGINX_80
        text = self.refused()
        self.assertIn("❌ Сертификат не выпущен: порт 80 занят.", text)
        self.assertIn("nginx", text)
        self.assertIn("Kaskad чужие службы не останавливает", text)
        self.assertNotIn("подключения к нему снаружи", text)

    def test_apt(self):
        self.with_domain()
        self.w.pkgs.fail = "E: Unable to locate package certbot\n"
        text = self.refused()
        self.assertIn("❌ Сертификат не выпущен: не удалось поставить certbot.", text)
        self.assertIn("повторите через несколько минут", text)

    def test_without_a_domain(self):
        self.make_app()
        self.press("sdc")
        self.assertIn("Домен не задан", self.last_text())
        self.assertEqual(self.certbot.calls, [])


class UseTest(SiteBotCase):
    def test_sstp_with_the_number_of_devices_and_a_confirmation(self):
        self.gateway_with_sstp()
        self.with_domain(issued="le")
        self.make_app()
        self.press("sdu")
        text = self.last_text()
        self.assertIn("• SSTP — самоподписанный; при переводе на домен нужны новые настройки: 2", text)
        self.assertIn("• OpenConnect — самоподписанный; при переводе на домен нужны новые настройки: 1", text)
        self.press("sdu:sstp")
        text = self.last_text()
        self.assertIn("🔐 Перевести SSTP на сертификат домена?", text)
        self.assertIn("Новые настройки понадобятся устройствам: <b>2</b>.", text)
        self.assertIn("Выдайте их заново", text)
        self.assertFalse(self.site()["use"]["sstp"])                 # the question changes nothing
        self.assertEqual([d for _, d in self.buttons()], ["sduy:sstp:on", "sdu"])
        self.press("sduy:sstp:on")
        self.assertIn("✅ SSTP: сертификат домена.", self.last_text())
        self.assertTrue(self.site()["use"]["sstp"])
        self.press("sdu")
        self.assertIn("• SSTP — на сертификате домена; при возврате на самоподписанный нужны новые настройки: 2",
                      self.last_text())
        self.press("sdu:sstp")
        self.assertIn("Вернуть SSTP на самоподписанный", self.last_text())
        self.press("sduy:sstp:off")
        self.assertFalse(self.site()["use"]["sstp"])

    def test_without_a_certificate(self):
        self.gateway_with_sstp()
        self.with_domain()
        self.make_app()
        self.press("sdu:sstp")
        self.assertTrue(self.last_text().startswith("❌"))
        self.assertIn("cert issue", self.last_text())
        self.assertEqual([d for _, d in self.buttons()], ["sdc", "sd"])

    def test_old_buttons(self):
        self.make_app()
        self.press("sdu:ikev2")
        self.assertEqual(self.last_text(), menus.old_button())
        self.press("sduy:sstp:maybe")
        self.assertEqual(self.last_text(), menus.old_button())


class ClearTest(SiteBotCase):
    def test_consequences_then_removal(self):
        self.gateway_with_sstp()
        self.with_domain(issued="le")
        self.make_app()
        self.press("sduy:sstp:on")
        self.press("sdx")
        text = self.last_text()
        self.assertIn("🗑 Убрать домен <code>vpn.example.com</code>? Что будет:", text)
        self.assertIn("• SSTP вернутся на самоподписанный сертификат и адрес узла — настройки придётся выдать заново "
                      "(устройств: 2; SSTP: 2);", text)
        self.assertIn("продлеваться не будет", text)
        self.assertEqual(self.site()["domain"], DOMAIN)               # the question changes nothing
        self.assertEqual([d for _, d in self.buttons()], ["sdxy", "sd"])
        self.press("sdxy")
        text = self.last_text()
        self.assertIn("✅ Домен убран", text)
        self.assertIn("Выдайте их заново", text)
        self.assertIn("Сертификат Let's Encrypt остался:", text)
        self.assertEqual(self.site()["domain"], "")


class PanelLinkTest(SiteBotCase):
    def fake_link(self):
        mod = types.ModuleType("kaskad.panel.auth")
        mod.login_link = lambda ttl=None, by=None: {"url": "https://vpn.example.com:41234/login?t=TOK", "ttl": 3600,
                                                     "fingerprint": "AB:CD"}
        return mock.patch.dict(sys.modules, {"kaskad.panel.auth": mod})

    def test_lets_encrypt_instead_of_the_fingerprint(self):
        self.with_domain(issued="le")
        self.make_app()
        with self.fake_link():
            self.press("gwl")
        text = self.last_text()
        self.assertIn("Сертификат Let's Encrypt для vpn.example.com, до 30.12.2026.", text)
        self.assertNotIn("AB:CD", text)
        self.assertNotIn("самоподписанный", text)

    def test_self_signed_keeps_the_fingerprint(self):
        self.with_domain()
        self.make_app()
        with self.fake_link():
            self.press("gwl")
        self.assertIn("<code>AB:CD</code>", self.last_text())
        self.assertIsNone(model.active(state.load()))


CYRILLIC = re.compile("[А-Яа-яЁё]")


class EnglishTest(SiteBotCase):
    """KASKAD_LANG=en (3.3.2): the screen of the domain, DNS, the certificate, SSTP / OpenConnect and removing the
    domain speak English — no Cyrillic in the texts and the buttons of the bot."""

    def setUp(self):
        super(EnglishTest, self).setUp()
        self.write_config(KASKAD_LANG="en")

    def assertEnglish(self, *texts):
        for t in texts + tuple(t for t, _ in self.buttons()):
            self.assertIsNone(CYRILLIC.search(t), t)

    def test_without_a_domain_by_the_reply_button_of_either_language(self):
        self.make_app()
        self.say("🌐 Domain")
        text = self.last_text()
        self.assertIn("🌐 <b>Domain</b> isn’t set.", text)
        self.assertIn("A record in the DNS settings of the domain: value — <code>203.0.113.10</code>", text)
        self.assertEqual(self.buttons(), [("✏️ Set domain", "sdn"), ("⬅️ Menu", "m")])
        self.assertEnglish(text)
        self.say("🌐 Домен")                    # a reply keyboard of the other language still on the screen
        self.assertIn("🌐 <b>Domain</b> isn’t set.", self.last_text())
        self.press("sdn")
        self.assertIn("✏️ Send the domain, for example <code>vpn.example.com</code>", self.last_text())
        self.assertEnglish(self.last_text())

    def test_status_with_the_certificate(self):
        self.gateway_with_sstp()
        self.with_domain(issued="le")
        self.make_app()
        self.press("sd")
        text = self.last_text()
        self.assertIn("🌐 <b>Domain</b>: <code>vpn.example.com</code>", text)
        self.assertIn("DNS: ✅ all A records point to this server", text)
        self.assertIn("Certificate: 🔒 Let’s Encrypt, valid until Dec 30, 2026 (83 days left)", text)
        self.assertIn("Used by: nobody yet", text)
        self.assertIn("Masking site: off (certificate without a site)", text)
        self.assertIn("(to change it: SSH menu 17 → 4)", text)
        self.assertIn("🌐 Turn on site (random template)", [t for t, _ in self.buttons()])
        self.assertEnglish(text)

    def test_dns_wait_and_issue_are_jobs_in_english(self):
        self.with_domain()
        self.answers[dns.A] = ("ok", ["198.51.100.7"])
        self.make_app()
        self.press("sdd")
        self.assertIn("DNS: ⚠️ the A records don’t point to this server (A: 198.51.100.7; server address:",
                      self.last_text())
        self.assertEqual([t for t, _ in self.buttons()], ["⏳ Wait up to 5 minutes", "🔎 Check again", "⬅️ Domain"])
        self.press("sdw")
        texts = self.texts()
        self.assertIn("⏳ Waiting for DNS up to 5 minutes", texts[-2])
        self.assertIn("⌛ DNS didn’t update within 5 minutes.", texts[-1])
        self.answers[dns.A] = ("ok", ["203.0.113.10"])
        self.w.ss = NGINX_80
        self.press("sdc")
        texts = self.texts()
        self.assertIn("⏳ Issuing a Let’s Encrypt certificate for <code>vpn.example.com</code>", texts[-2])
        self.assertIn("❌ Certificate not issued: port 80 is in use.", texts[-1])
        self.assertEqual([t for t, _ in self.buttons()], ["🔄 Retry", "⬅️ Domain"])

    def test_sstp_and_removing_the_domain(self):
        self.gateway_with_sstp()
        self.with_domain(issued="le")
        self.make_app()
        self.press("sdu")
        text = self.last_text()
        self.assertIn("🔐 <b>SSTP and OpenConnect on the domain certificate</b>", text)
        self.assertIn("• SSTP — self-signed; new settings needed when moving to the domain: 2", text)
        self.assertIn("SSTP → to the domain", [t for t, _ in self.buttons()])
        self.assertEnglish(text)
        self.press("sdu:sstp")
        self.assertIn("🔐 Move SSTP to the domain certificate?", self.last_text())
        self.assertIn("Devices that will need new settings: <b>2</b>.", self.last_text())
        self.assertEqual([t for t, _ in self.buttons()], ["✅ Yes, move", "⬅️ Cancel"])
        self.press("sduy:sstp:on")
        self.assertIn("✅ SSTP: domain certificate.", self.last_text())
        self.press("sdx")
        text = self.last_text()
        self.assertIn("🗑 Remove domain <code>vpn.example.com</code>? What happens:", text)
        self.assertIn("• SSTP: back to the self-signed certificate and the server’s address — settings will need "
                      "reissuing (devices: 2; SSTP: 2);", text)
        self.assertEnglish(text)
        self.press("sdxy")
        self.assertIn("✅ Domain removed", self.last_text())
        self.assertEqual(self.buttons(), [("🌐 Domain", "sd"), ("⬅️ Menu", "m")])

    def test_old_button(self):
        self.make_app()
        self.press("sdu:ikev2")
        self.assertEqual(self.last_text(), menus.old_button())     # the server's language: en
        self.assertIn("⌛", self.last_text())
        self.assertEnglish(self.last_text())

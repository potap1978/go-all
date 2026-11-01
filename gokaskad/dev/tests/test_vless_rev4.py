"""REV-vless4 «было до» 1 (§3.6): «Убрать домен» в боте при VLESS на TLS этого домена и устройствах — цена VLESS в
вопросе до действия (текст ядра, как у «Выключить сайт»), «Да» передаёт ``yes`` (и сайт Reality, который бот подобрал и
назвал); без согласия ничего не меняется; без устройств — без цены. Кнопка без цены (sdxy, в том числе отправленная до
3.3.7.8) ``yes`` не передаёт: ядро отказывает ценой, бот спрашивает снова уже с ней.

REV-vless-fix З1: в «Да» (sdxy:Y<n>) — число устройств, на которое дано согласие; устройство пришло или ушло после
вопроса — старая «Да» ничего не меняет, бот спрашивает снова с новой ценой."""

import re
import unittest
from unittest import mock

from kaskad import state
from kaskad.site import model as smodel
from kaskad.site import ops as site_ops
from tests import test_gw_vless_front as F
from tests import test_vless_mode as VM
from tests.test_vless_sh3 import _Ctx
from tests.test_vless_want import WantCase

NO_LINKS = {"domain": "", "count": 0, "port": True}       # the panel is not on the domain: the port stays as it is


class BotClearPriceTest(WantCase):
    def setUp(self):
        super().setUp()
        from kaskad.bot import site as bsite
        self.bsite = bsite
        p = mock.patch.object(site_ops, "people_links", return_value=dict(NO_LINKS))
        p.start()
        self.addCleanup(p.stop)
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(self.rec()["security"], "tls")
        self.ctx = _Ctx(self.w.runner)

    def ask(self):
        self.bsite.cb_clear_ask(self.ctx, "sdx")
        return self.ctx.said[-1][0], self.ctx.buttons()

    def press(self, data):
        from kaskad.bot import registry
        registry.load()
        fn, _ = registry.find_callback(data)
        self.assertIsNotNone(fn, data)
        fn(self.ctx, data)

    def k(self, n):
        """The key of the devices of VLESS now (``_price_key``: «<n>.<print>», REV-3378-fix придирка 4)."""
        from kaskad.gw import ops as gops
        k = self.bsite._price_key(gops._reissue_devices(state.load(), "vless"))
        self.assertTrue(k.startswith("%d." % n), k)
        return k

    def test_the_price_in_the_question_and_yes(self):
        before = VM.raw()
        text, btns = self.ask()
        self.assertIn("выдать заново", text)                          # the price of the core, before the action
        self.assertIn("Телефон", text)
        self.assertIn("Убрать домен", text)
        self.assertEqual(btns, ["sdxy:Y" + self.k(1), "sd"])                     # the consent: 1 device
        self.assertEqual(VM.raw(), before)                            # the question changes nothing
        self.press("sd")                                              # «Отмена» — nothing either
        self.assertEqual(VM.raw(), before)
        self.press("sdxy:Y" + self.k(1))
        self.assertIn("Домен убран", self.ctx.said[-1][0])
        self.assertEqual(smodel.domain_of(state.load()), "")
        self.assertEqual(self.rec()["security"], "reality")

    def test_no_yes_changes_nothing_and_asks_again(self):
        """A button without the price (sent before 3.3.7.8, or no devices when asked): the core refuses — the bot asks
        again, now with the price; nothing changed."""
        before = VM.raw()
        self.press("sdxy")
        self.assertEqual(VM.raw(), before)
        self.assertEqual(smodel.domain_of(state.load()), F.DOMAIN)
        self.assertEqual(self.rec()["security"], "tls")
        self.assertIn("выдать заново", self.ctx.said[-1][0])
        self.assertEqual(self.ctx.buttons(), ["sdxy:Y" + self.k(1), "sd"])

    def test_the_bot_picks_and_names_the_cover(self):
        self.put(mask="own", sni=F.DOMAIN, dest="%s:443" % F.DOMAIN)  # TLS, no cover kept: need_pick
        before = VM.raw()
        text, btns = self.ask()
        data = [b for b in btns if b.startswith("sdxy")][0]
        self.assertTrue(data.startswith("sdxy:Y%s:" % self.k(1)), data)
        site = data[len("sdxy:Y%s:" % self.k(1)):]
        self.assertIn(site, text)                                     # named in the question
        self.assertNotIn("--vless-site", text)                        # no flags of the CLI in Telegram
        self.assertEqual(VM.raw(), before)
        self.press(data)
        self.assertEqual(smodel.domain_of(state.load()), "")
        self.assertEqual((self.rec()["security"], self.rec()["sni"]), ("reality", site))

    def test_no_cover_passed_no_yes(self):
        self.put(mask="own", sni=F.DOMAIN, dest="%s:443" % F.DOMAIN)
        before = VM.raw()
        with mock.patch.object(self.bsite, "_pick_cover", return_value=None):
            text, btns = self.ask()
        self.assertIn("Не удалось подобрать сайт маскировки", text)
        self.assertIn("VLESS уйдёт на Reality под чужим сайтом", text)          # REV-vless-fix П3: VLESS may be on
        self.assertNotIn("с TLS на Reality", text)                            # Reality with the own site too
        self.assertEqual(btns, ["sdx", "sd"])                         # «Повторить» and «Отмена», no «Да»
        self.assertEqual(VM.raw(), before)

    def test_a_forged_site_is_an_old_button(self):
        before = VM.raw()
        for data in ("sdxy:Y:bad host;rm", "sdxy:N", "sdxy:Yx", "sdxy:Y1:bad host;rm", "sdxy:Y²", "sdxy:Y1x"):
            self.press(data)
            self.assertEqual(VM.raw(), before, data)
        self.assertEqual(smodel.domain_of(state.load()), F.DOMAIN)

    def test_stale_yes_after_a_new_device(self):
        """REV-vless-fix З1 (repro_stale): asked with 1 device, a 2nd came later — the old «Да» changes nothing, the
        question again with the new price (2 devices) and the new consent in the button; that one works."""
        text, btns = self.ask()
        self.assertIn("устройств — 1", text)
        old = btns[0]
        self.add_device("Ноутбук", "windows")                         # a device after the question
        before = VM.raw()
        self.press(old)
        self.assertEqual(VM.raw(), before)                            # nothing changed
        self.assertEqual(smodel.domain_of(state.load()), F.DOMAIN)
        self.assertEqual(self.rec()["security"], "tls")
        self.assertIn("устройств — 2", self.ctx.said[-1][0])          # the question again, the new price
        self.assertIn("Ноутбук", self.ctx.said[-1][0])
        self.assertEqual(self.ctx.buttons(), ["sdxy:Y" + self.k(2), "sd"])
        self.press("sdxy:Y" + self.k(2))
        self.assertIn("Домен убран", self.ctx.said[-1][0])
        self.assertEqual(smodel.domain_of(state.load()), "")
        self.assertEqual(self.rec()["security"], "reality")

    def test_stale_yes_with_the_cover_after_a_new_device(self):
        """The same with the cover the bot named (sdxy:Y1:<site>): a new device — nothing, the question again."""
        self.put(mask="own", sni=F.DOMAIN, dest="%s:443" % F.DOMAIN)
        _, btns = self.ask()
        old = btns[0]
        self.assertTrue(old.startswith("sdxy:Y%s:" % self.k(1)), old)
        self.add_device("Ноутбук", "windows")
        before = VM.raw()
        self.press(old)
        self.assertEqual(VM.raw(), before)
        self.assertEqual(smodel.domain_of(state.load()), F.DOMAIN)
        self.assertTrue(self.ctx.buttons()[0].startswith("sdxy:Y%s:" % self.k(2)), self.ctx.buttons())

    def test_stale_yes_after_the_device_went(self):
        """The device of the price deleted after the question: the old «Да» — the question again, now without a price
        (no devices: no ``yes`` in the button); nothing changed by the old one."""
        _, btns = self.ask()
        self.ok("gw", "device", "del", self.dev["id"])
        before = VM.raw()
        self.press(btns[0])
        self.assertEqual(VM.raw(), before)
        self.assertEqual(smodel.domain_of(state.load()), F.DOMAIN)
        self.assertEqual(self.ctx.buttons(), ["sdxy", "sd"])

    def test_yes_without_the_count_asks_again(self):
        """sdxy:Y[:<site>] of a 3.3.7.8 build before REV-vless-fix: no count of the consent — the question again."""
        before = VM.raw()
        self.press("sdxy:Y")
        self.assertEqual(VM.raw(), before)
        self.assertIn("устройств — 1", self.ctx.said[-1][0])
        self.assertEqual(self.ctx.buttons(), ["sdxy:Y" + self.k(1), "sd"])
        self.press("sdxy:Y:www.example.com")
        self.assertEqual(VM.raw(), before)
        self.assertEqual(smodel.domain_of(state.load()), F.DOMAIN)

    def test_without_devices_no_price(self):
        self.ok("gw", "device", "del", self.dev["id"])
        text, btns = self.ask()
        self.assertNotIn("выдать заново", text)
        self.assertEqual(btns, ["sdxy", "sd"])
        self.press("sdxy")
        self.assertIn("Домен убран", self.ctx.said[-1][0])
        self.assertEqual(smodel.domain_of(state.load()), "")


class ClearPriceTextTest(WantCase):
    """REV-vless-fix З2: цена «Убрать домен» говорит, как вернуть TLS через домен (сайта без домена нет), — не
    «снова включите сайт»; у «Выключить сайт» строка прежняя. П2: по-английски — «Reality (backup)», без «fallback»."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(site_ops, "people_links", return_value=dict(NO_LINKS))
        p.start()
        self.addCleanup(p.stop)
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(self.rec()["security"], "tls")

    def price(self, drop_domain, st=None, lang=None):
        from kaskad.errors import KaskadError
        from kaskad.i18n import use_lang
        with use_lang(lang):
            with self.assertRaises(KaskadError) as cm:
                site_ops.vless_price(st or state.load(), self.w.runner, "domain", vless_site="www.example.com",
                                     drop_domain=drop_domain)
        return cm.exception.data["text"]

    def test_tls_back(self):
        clear, off = self.price(True), self.price(False)
        self.assertIn("Чтобы вернуть TLS — снова задайте домен, выпустите сертификат и включите сайт.", clear)
        self.assertNotIn("снова включите сайт", clear)
        self.assertIn("Чтобы вернуть TLS — снова включите сайт.", off)               # the site off: true there
        self.assertNotIn("задайте домен", off)
        en = self.price(True, lang="en")
        self.assertIn("To get TLS back, set the domain again, issue its certificate and turn the site on.", en)
        self.assertIn("VLESS will switch to Reality (backup) posing as www.example.com.", en)
        self.assertNotIn("fallback", en)
        self.assertIn("give them out again", en)

    def test_the_choice_of_reality_with_the_own_site(self):
        """Variant «b» (Reality with the own site): the choice goes — without the domain there is no own site."""
        from kaskad.gw import model as gm
        st = state.load()
        gm.inbound(st, "vless").update(want="reality", reality_consent={"at": "2026-10-20T10:00:00Z", "via": "panel",
                                                                        "risks": "r1b"})
        clear, off = self.price(True, st), self.price(False, st)
        self.assertIn("своего сайта без домена нет", clear)
        self.assertIn("Когда домен снова зададут и включат сайт, можно будет перейти на TLS.", clear)
        self.assertNotIn("Когда сайт снова включат", clear)
        self.assertIn("Когда сайт снова включат, VLESS перейдёт на TLS.", off)
        self.assertIn("there is no own site without the domain", self.price(True, st, lang="en"))


class TextsTest(unittest.TestCase):
    """REV-vless-fix П2, П3, П5 — по каталогу: EN цены ядра без «fallback», «give … out again» везде в EN, подбор не
    удался — без «с TLS»."""

    def cat(self, lang, name):
        import json
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "i18n",
                            "catalog", lang, name + ".json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_texts(self):
        en = self.cat("en", "site")
        for k, v in en.items():
            if k.startswith("site.ops.vless_price_"):
                self.assertNotIn("fallback", v, k)
                for got in re.findall(r"give [^.;—]{0,30}?again", v):           # «give … out again»
                    self.assertIn(" out again", got, k)
        self.assertIn("Reality (backup)", en["site.ops.vless_price_off"])
        setup = self.cat("en", "setup")
        self.assertEqual(setup["setup.steps.domainVlessAnyway"], "Remove the domain and give the VLESS keys out again")
        self.assertEqual(setup["setup.steps.siteVlessAnyway"], "Switch the site on and give the VLESS keys out again")
        for lang, bad in (("ru", "с TLS на Reality"), ("en", "from TLS to Reality")):
            self.assertNotIn(bad, self.cat(lang, "bot")["bot.site.clear_no_pick"], lang)
        self.assertIn("as a foreign site, and none fit", self.cat("en", "bot")["bot.site.clear_no_pick"])


if __name__ == "__main__":
    unittest.main()

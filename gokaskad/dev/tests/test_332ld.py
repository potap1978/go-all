"""3.3.2, live check T-M6-332 №1 and №3.

№1 — the mail of Let's Encrypt on the stage «Домен» of the wizard: the step ``cert`` knows «no mail» by its code
(``site.ops`` ``warning_codes`` / ``warning_items``), not by the words — on both languages ``email.need`` is true
without a mail and false with one; the other warnings stay, the text of «no mail» is not among them.

№3 — ``Description=`` of the systemd units of Kaskad: plain English always, the same under ``ru`` and ``en``."""

import os
import re
import unittest
from unittest import mock

from kaskad import i18n, paths, sysrun, units, util
from kaskad.i18n import use_lang
from kaskad.panel import auth
from kaskad.setup import steps
from kaskad.site import dns, ops
from tests.helpers import RootCase
from tests.setup.test_setup_core import new_link, open_panel
from tests.site.sitecase import DOMAIN, SiteCase
from tests.test_qs330d_domain import NEW, SwitchCase

CYR = re.compile("[А-Яа-яЁё]")


class MailOnTheDomainStage(SiteCase):
    def setUp(self):
        super().setUp()
        open_panel()
        new_link()
        for p in (mock.patch.object(auth, "SCRYPT_N", 1 << 10), mock.patch.object(steps, "online", lambda force=False: True)):
            p.start()
            self.addCleanup(p.stop)
        steps.run("password", {"password": "Correct-Horse-9"}, self.w.runner)

    def cert_step(self):
        with mock.patch.object(steps, "cert_served", lambda runner: True):
            return steps.run("cert", {}, self.w.runner)

    def ready(self):
        self.answers = {dns.A: ("ok", ["203.0.113.10"]), dns.AAAA: ("ok", [])}
        self.certbot_present()
        ops.set_domain(self.w.runner, DOMAIN)

    def no_mail(self, lang):
        self.ready()
        with use_lang(lang):
            out = self.cert_step()
            self.assertEqual(out["state"], "ok", out)
            email = out["data"]["email"]
            self.assertIs(email["need"], True)
            self.assertEqual(email["text"], ops.warning(ops.NO_EMAIL))
            self.assertNotIn(ops.warning(ops.NO_EMAIL), out["data"]["warnings"])
        return email["text"]

    def test_no_mail_ru(self):
        self.assertTrue(CYR.search(self.no_mail("ru")))

    def test_no_mail_en(self):
        self.assertIsNone(CYR.search(self.no_mail("en")))

    def with_mail(self, lang):
        self.ready()
        ops.set_email(self.w.runner, "admin@example.com")
        with use_lang(lang):
            out = self.cert_step()
        self.assertEqual(out["state"], "ok", out)
        self.assertEqual(out["data"]["email"], {"need": False})

    def test_with_mail_ru(self):
        self.with_mail("ru")

    def test_with_mail_en(self):
        self.with_mail("en")

    def test_the_words_of_another_language_do_not_matter(self):
        """The result made in one language, split in the other: still by the code."""
        self.ready()
        with use_lang("en"):
            r = ops.issue(self.w.runner)
        with use_lang("ru"):
            rest, email = steps._email_split(r)
        self.assertIs(email["need"], True)
        self.assertEqual(rest, [i["text"] for i in r["warning_items"] if i["code"] != ops.NO_EMAIL])
        self.assertNotIn(i18n.tr("site.ops.no_email", "en"), rest)
        self.assertEqual(len(rest), len(r["warnings"]) - 1)

    def test_uncoded(self):
        r = {"warnings": ["a", "b"], "warning_codes": [ops.NO_EMAIL],
             "warning_items": [{"code": None, "text": "a"}, {"code": ops.NO_EMAIL, "text": "b"}]}
        self.assertEqual(ops.uncoded(r, ops.NO_EMAIL), ["a"])
        self.assertEqual(ops.uncoded({"warnings": ["x"]}, ops.NO_EMAIL), ["x"])


class MailOnTheDomainSwitch(SwitchCase):
    """The change of a working domain on the wizard: «no mail» is not among the warnings of the switch (as since 3.3.0),
    its code goes on — the stage shows the field."""

    def test_switch_without_mail(self):
        self.certbot.pem = "other"
        for lang in ("en", "ru"):
            with self.subTest(lang=lang), use_lang(lang):
                res = ops.set_domain(self.w.runner, NEW if lang == "en" else DOMAIN)
                self.assertEqual(res["warning_codes"], [ops.NO_EMAIL])
                self.assertNotIn(ops.warning(ops.NO_EMAIL), res["warnings"])
                rest, email = steps._email_split(res)
                self.assertEqual((email["need"], email["text"]), (True, ops.warning(ops.NO_EMAIL)))
                self.assertEqual(len(rest), len(res["warnings"]))

    def test_switch_with_mail(self):
        self.certbot.pem = "other"
        ops.set_email(self.w.runner, "admin@example.com")
        res = ops.set_domain(self.w.runner, NEW)
        self.assertEqual(res["warning_codes"], [])
        self.assertEqual(steps._email_split(res)[1], {"need": False})


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESC = re.compile(r"(?m)^Description=(.*)$")


def all_units():
    """{name: text} of every unit Kaskad writes from Python (the inbounds of the gateway: their golden files below)."""
    from kaskad.gw import awgkmod, collect, dns as gdns, exitsubs, xray
    from kaskad.ipcert import ops as iops
    from kaskad.panel import cli as pcli
    from kaskad.rules import timer
    from kaskad.site import acme
    runner = sysrun.FakeRunner(binaries=["nft"])
    out = dict(units.unit_texts(runner))
    svc, tmr = iops.unit_texts()
    out.update({
        pcli.UNIT: pcli.unit_text(), collect.UNIT: collect.unit_text(), gdns.UNIT: gdns.unit_text("/usr/sbin/dnsmasq"),
        xray.UNIT: xray.unit_text("/usr/local/bin/xray"), exitsubs.SERVICE: exitsubs.service_text(),
        exitsubs.TIMER: exitsubs.timer_text(), timer.SERVICE: timer.service_text(), timer.TIMER: timer.timer_text(),
        timer.LOAD_SERVICE: timer.load_service_text(), timer.LOAD_TIMER: timer.load_timer_text(),
        acme.RENEW_UNIT: acme._renew_service(), acme.RENEW_TIMER: acme._renew_timer(),
        acme.WATCH_UNIT: acme._watch_service(), acme.WATCH_TIMER: acme._watch_timer(),
        iops.UNIT: svc, iops.TIMER: tmr, awgkmod.BOOT_UNIT: awgkmod.boot_unit_text()})
    return out


def descriptions(texts):
    got = {}
    for name, text in texts.items():
        d = DESC.findall(text)
        assert len(d) == 1, (name, d)
        got[name] = d[0]
    return got


class UnitCaptionsTest(RootCase):
    """№3: ``Description=`` — plain English (ASCII) always: the language of the server or of a request changes nothing
    in a unit file (no rewrite, no restart from a change of the language)."""

    def test_english_and_the_same_on_both_languages(self):
        got = {}
        for lang in ("ru", "en"):
            with mock.patch.dict(os.environ, {"KASKAD_LANG": lang}), use_lang(lang):
                got[lang] = all_units()
        self.assertGreaterEqual(len(got["ru"]), 19)
        self.assertEqual(got["ru"], got["en"])                   # the whole unit, not only its caption
        for name, d in descriptions(got["ru"]).items():
            with self.subTest(unit=name):
                self.assertTrue(d.startswith("Kaskad 3"), d)
                self.assertTrue(d.isascii(), d)
                self.assertIsNone(CYR.search(d), d)

    def test_golden_inbound_units_and_install_sh(self):
        gold = os.path.join(ROOT, "tests", "fixtures", "gw")
        texts = {}
        for top, _, files in os.walk(gold):
            for f in files:
                if f.endswith((".service", ".timer")):
                    texts[f] = util.read_text(os.path.join(top, f))
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as f:
            sh = f.read()
        texts.update(("install.sh:%d" % n, line) for n, line in enumerate(sh.splitlines(), 1)
                     if re.match(r"\s*Description=", line) or '"Description=' in line)
        self.assertGreater(len(texts), 10)
        for name, text in texts.items():
            for d in re.findall(r"Description=([^\n\"]*)", text):
                with self.subTest(unit=name):
                    self.assertTrue(d.isascii(), d)

    def test_a_change_of_the_language_touches_no_unit(self):
        from kaskad.gw import sys as gsys
        sysd = paths.systemd_dir()
        os.makedirs(sysd, exist_ok=True)
        for name, text in all_units().items():
            util.atomic_write(os.path.join(sysd, name), text, 0o644)
        before = dict((n, util.read_text(os.path.join(sysd, n))) for n in os.listdir(sysd))
        with mock.patch.object(gsys, "write_unit", side_effect=AssertionError("a unit written")), \
                mock.patch.object(gsys, "systemd", side_effect=AssertionError("systemctl")), \
                mock.patch.dict(os.environ, {"KASKAD_LANG": "ru"}):            # set_lang sets it too: not past the test
            for lang in ("en", "ru", "en"):
                i18n.set_lang(lang)
                self.assertEqual(i18n.saved_lang(), lang)
        after = dict((n, util.read_text(os.path.join(sysd, n))) for n in os.listdir(sysd))
        self.assertEqual(before, after)
        self.assertEqual(all_units(), before)                    # what an update would write: the same files


class UpdateRestartsTest(RootCase):
    """An update that only changes the caption of a unit: daemon-reload, no restart (VLESS on kaskad-gw-xray keeps its
    connections); any other line changed — one restart."""

    def setUp(self):
        super().setUp()
        from kaskad.gw import xray
        self.unit = xray.UNIT
        self.new = xray.unit_text("/usr/local/bin/xray")
        self.old = self.new.replace("Description=Kaskad 3: gateway xray (VLESS exits, DNS, SOCKS5)",
                                    "Description=Kaskad 3: xray шлюза (выходы VLESS, DNS, SOCKS5)")
        self.assertNotEqual(self.old, self.new)
        os.makedirs(paths.systemd_dir(), exist_ok=True)
        self.runner = sysrun.FakeRunner()
        self.runner.on(["systemctl", "is-active"], "active\n").on(["systemctl"], (0, "", ""))

    def ensure(self, old):
        from kaskad.gw import sys as gsys
        util.atomic_write(gsys.unit_path(self.unit), old, 0o644)
        self.runner.calls = []
        r = gsys.ensure_unit(self.runner, self.unit, self.new)
        cmds = [a for a in self.runner.commands(["systemctl"]) if a[1] in ("restart", "daemon-reload", "start")]
        self.assertEqual(util.read_text(gsys.unit_path(self.unit)), self.new)
        return r, cmds

    def test_caption_only_no_restart(self):
        r, cmds = self.ensure(self.old)
        self.assertEqual((r["written"], r["restarted"], r["started"]), (True, False, False))
        self.assertEqual(cmds, [["systemctl", "daemon-reload"]])

    def test_another_line_one_restart(self):
        r, cmds = self.ensure(self.old.replace("Restart=", "Restart=no\n#"))
        self.assertTrue(r["restarted"])
        self.assertEqual(cmds, [["systemctl", "daemon-reload"], ["systemctl", "restart", self.unit]])

    def test_same_text_nothing(self):
        r, cmds = self.ensure(self.new)
        self.assertEqual((r["written"], r["restarted"]), (False, False))
        self.assertEqual(cmds, [])

    def test_dns_of_3_3_1_is_not_old(self):
        """kaskad-dns with the Russian caption of 3.3.1 is not «of 3.1.0» (that would restart dnsmasq)."""
        from kaskad.gw import dns as gdns, sys as gsys
        text = gdns.unit_text("/usr/sbin/dnsmasq")
        old = re.sub(r"(?m)^Description=.*$", "Description=Kaskad 3: DNS шлюза (dnsmasq на адресе шлюза, порт %d)"
                     % gdns.PORT, text)
        util.atomic_write(gsys.unit_path(gdns.UNIT), old, 0o644)
        with mock.patch.object(gdns, "binary", lambda runner: "/usr/sbin/dnsmasq"):
            self.assertEqual(gdns.stale(self.runner), [])
            util.atomic_write(gsys.unit_path(gdns.UNIT), old.replace("Type=forking", "Type=simple"), 0o644)
            self.assertEqual(gdns.stale(self.runner), ["unit"])


if __name__ == "__main__":
    unittest.main()

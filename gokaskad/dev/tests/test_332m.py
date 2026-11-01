"""3.3.2, the final live check T-M6-332b on 339df04: the last small findings.

№2  an update rewrites the units whose owners write them only on an action of their own (kaskad-exit-sub, kaskad-site,
    kaskad-cert-renew, kaskad-ipcert, the AmneziaWG kernel module): a new caption — daemon-reload, no restart;
№3  the answers of the wizard steps (``site-email`` and the others) — from the catalog, in the language of the call;
№4  a second check of a domain whose certificate is still valid shows the field of the mail again while there is none;
№7  the date of the routing lists — the common format of the language (fmt_datetime), «UTC» kept;
№9  ``kaskadctl site email`` without an argument — the mail now (or «not set»), not an error;
№13 SSH 18 → 3 / 4 (turn off access from the internet, ask for the password in the tunnel) — a question with digits
    first, the new state after (the screens — tools/k3_install_test.sh, «веб-панель»);
and the small ones: one word per notion in the panel (Protocols, Platform), the mock of the wizard knows ``site-email``."""

import contextlib
import io
import json
import os
import re
import unittest
from unittest import mock

from kaskad import cli, i18n, paths, sysrun, unit_refresh, util
from kaskad.gw import sys as gsys
from kaskad.i18n import check, use_lang
from kaskad.panel import auth
from kaskad.setup import steps
from kaskad.site import dns, ops
from tests.helpers import RootCase
from tests.setup.test_setup_core import new_link, open_panel
from tests.site.sitecase import DOMAIN, SiteCase
from tests.test_332ld import all_units

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CYR = re.compile("[А-Яа-яЁё]")
DESC = re.compile(r"(?m)^Description=.*$")

# the captions of 3.3.1 (and of 3.3.2 before 238acd1) the live server kept after the update (T-M6-332b №2)
OLD = {
    "kaskad-exit-sub.service": "Kaskad 3: подписки выходов — ключи пула «Прокси для API Telegram»",
    "kaskad-exit-sub.timer": "Kaskad 3: подписки выходов раз в сутки",
    "kaskad-site.service": "Kaskad 3: сторож срока сертификата домена",
    "kaskad-site.timer": "Kaskad 3: сторож срока сертификата домена раз в сутки",
}


def _with_caption(text, caption):
    return DESC.sub(lambda m: "Description=" + caption, text, count=1)


class UnitRefreshTest(RootCase):
    def setUp(self):
        super().setUp()
        os.makedirs(paths.systemd_dir(), exist_ok=True)
        self.runner = sysrun.FakeRunner()
        self.runner.on(["systemctl"], (0, "", ""))
        # None — the owner wants no unit now (the timer of the schedule of an unarmed node, B11): nothing to refresh
        self.new = dict((name, t) for name, t in ((n, fn()) for n, fn, _ in unit_refresh.registry()) if t is not None)

    def put(self, name, text):
        util.atomic_write(gsys.unit_path(name), text, 0o644)

    def systemctl(self):
        return [a[1:] for a in self.runner.commands(["systemctl"])]

    def test_russian_captions_of_the_live_server_become_english_without_a_restart(self):
        for name, caption in OLD.items():
            self.put(name, _with_caption(self.new[name], caption))
        res = unit_refresh.refresh(self.runner)
        self.assertEqual(sorted(res["written"]), sorted(OLD))
        self.assertEqual((res["restarted"], res["left"]), ([], []))
        for name in OLD:
            text = util.read_text(gsys.unit_path(name))
            self.assertEqual(text, self.new[name])
            self.assertIsNone(CYR.search(text), name)
        self.assertEqual(self.systemctl(), [["daemon-reload"]])             # one reload, nothing restarted or started

    def test_absent_units_are_not_created(self):
        res = unit_refresh.refresh(self.runner)
        self.assertEqual(res["written"], [])
        self.assertEqual(sorted(os.listdir(paths.systemd_dir())), [])
        self.assertEqual(self.systemctl(), [])

    def test_up_to_date_nothing(self):
        for name, text in self.new.items():
            self.put(name, text)
        self.assertEqual(unit_refresh.refresh(self.runner)["written"], [])
        self.assertEqual(self.systemctl(), [])

    def test_timer_with_another_schedule_restarted(self):
        name = "kaskad-exit-sub.timer"
        self.put(name, self.new[name].replace("RandomizedDelaySec=1h", "RandomizedDelaySec=3h"))
        res = unit_refresh.refresh(self.runner)
        self.assertEqual((res["written"], res["restarted"]), ([name], [name]))
        self.assertEqual(self.systemctl(), [["daemon-reload"], ["try-restart", name]])

    def test_oneshot_with_other_lines_rewritten_not_started(self):
        name = "kaskad-site.service"
        self.put(name, self.new[name].replace("TimeoutStartSec=15min", "TimeoutStartSec=5min"))
        res = unit_refresh.refresh(self.runner)
        self.assertEqual((res["written"], res["restarted"]), ([name], []))
        self.assertEqual(self.systemctl(), [["daemon-reload"]])

    def test_running_service_caption_only(self):
        from kaskad.gw import collect
        self.put(collect.UNIT, _with_caption(self.new[collect.UNIT], "Kaskad 3: сторож выходов и сессии шлюза"))
        res = unit_refresh.refresh(self.runner)
        self.assertEqual(res["written"], [collect.UNIT])
        self.assertEqual(self.systemctl(), [["daemon-reload"]])

    def test_running_service_with_hard_lines_left_to_its_owner(self):
        from kaskad.gw import collect
        old = self.new[collect.UNIT].replace("RestartSec=10", "RestartSec=5")
        self.put(collect.UNIT, old)
        res = unit_refresh.refresh(self.runner)
        self.assertEqual((res["written"], res["left"]), ([], [collect.UNIT]))
        self.assertEqual(util.read_text(gsys.unit_path(collect.UNIT)), old)
        self.assertEqual(self.systemctl(), [])

    def test_every_unit_of_kaskad_is_refreshed_by_an_update(self):
        """Every unit written from Python: in this list, or written by the owner the update calls anyway."""
        from kaskad import units
        from kaskad.gw import dns as gdns, xray
        from kaskad.panel import cli as pcli
        by_owner = {pcli.UNIT: "panel unit --refresh", gdns.UNIT: "gw apply", xray.UNIT: "gw apply"}
        by_owner.update((u, "net install-units") for u in units.UNITS)
        mine = set(self.new)
        for name in all_units():
            with self.subTest(unit=name):
                self.assertTrue(name in mine or name in by_owner, name)
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as f:
            sh = f.read()
        body = re.search(r"^k3_post_update\(\) \{\n(.*?)^\}", sh, re.S | re.M).group(1)
        for cmd in ("k3ctl update units --quiet", "k3ctl panel unit --refresh", "k3ctl net install-units",
                    "k3ctl gw apply"):
            self.assertIn(cmd, body)
        self.assertIn("LOG_UNITS_REFRESH_FAIL", body)

    def test_cli(self):
        self.put("kaskad-site.timer", _with_caption(self.new["kaskad-site.timer"], OLD["kaskad-site.timer"]))
        out = io.StringIO()
        with mock.patch("kaskad.cli_update._r", lambda: self.runner), contextlib.redirect_stdout(out), \
                use_lang("en"):
            rc = cli.main(["update", "units", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())["written"], ["kaskad-site.timer"])
        out = io.StringIO()
        with mock.patch("kaskad.cli_update._r", lambda: self.runner), contextlib.redirect_stdout(out), \
                use_lang("en"):
            cli.main(["update", "units"])
        self.assertEqual(out.getvalue().strip(), "The unit texts are up to date.")
        out = io.StringIO()
        with mock.patch("kaskad.cli_update._r", lambda: self.runner), contextlib.redirect_stdout(out):
            cli.main(["update", "units", "--quiet"])
        self.assertEqual(out.getvalue(), "")


class StepsSpeakTheCatalogTest(unittest.TestCase):
    """№3: no Russian left in the answers of the wizard and of the panel API (a unit caption — plain English)."""

    def test_no_cyrillic(self):
        check.assert_no_cyrillic(self, os.path.join(ROOT, "lib", "kaskad", "setup"),
                                 os.path.join(ROOT, "lib", "kaskad", "panel", "api.py"))

    def test_job_unit_caption(self):
        with open(os.path.join(ROOT, "lib", "kaskad", "setup", "jobs.py"), encoding="utf-8") as f:
            self.assertIn('"--description=Kaskad 3: a step of the quick setup"', f.read())
        with open(os.path.join(ROOT, "lib", "kaskad", "site", "templates.py"), encoding="utf-8") as f:
            self.assertIn('"--description=Kaskad 3: site template catalog index"', f.read())


class DomainStageMailTest(SiteCase):
    def setUp(self):
        super().setUp()
        open_panel()
        new_link()
        for p in (mock.patch.object(auth, "SCRYPT_N", 1 << 10), mock.patch.object(steps, "online", lambda force=False: True)):
            p.start()
            self.addCleanup(p.stop)
        steps.run("password", {"password": "Correct-Horse-9"}, self.w.runner)
        self.answers = {dns.A: ("ok", ["203.0.113.10"]), dns.AAAA: ("ok", [])}
        self.certbot_present()
        ops.set_domain(self.w.runner, DOMAIN)

    def cert_step(self):
        with mock.patch.object(steps, "cert_served", lambda runner: True):
            return steps.run("cert", {}, self.w.runner)

    def test_site_email_answer_in_the_language_of_the_call(self):
        """№3: «Email for Let’s Encrypt: …» on English, «Почта для Let's Encrypt: …» on Russian; cleared — the same."""
        for lang in ("en", "ru"):
            with self.subTest(lang=lang), use_lang(lang):
                out = steps.run("site-email", {"email": "qa@example.com"}, self.w.runner)
                self.assertEqual(out["state"], "ok", out)
                self.assertEqual(out["text"], i18n.tr("setup.steps.siteEmailSet", email="qa@example.com"))
                gone = steps.run("site-email", {"email": ""}, self.w.runner)
                self.assertEqual(gone["text"], i18n.tr("setup.steps.siteEmailCleared"))
                for text in (out["text"], gone["text"]):
                    self.assertEqual(bool(CYR.search(text)), lang == "ru", text)

    def test_check_again_without_mail_shows_the_field(self):
        """№4: the certificate is still valid (nothing issued, no code «no mail» in the result) — the field is back."""
        first = self.cert_step()
        self.assertIs(first["data"]["email"]["need"], True)
        r = ops.issue(self.w.runner)
        self.assertTrue(r["kept"] and not r["issued"], r)
        self.assertNotIn(ops.NO_EMAIL, r.get("warning_codes") or [])     # the branch «still valid» of the live check
        for lang in ("en", "ru"):
            with self.subTest(lang=lang), use_lang(lang):
                again = self.cert_step()
                self.assertEqual(again["state"], "ok", again)
                self.assertEqual(again["data"]["email"], {"need": True, "text": ops.warning(ops.NO_EMAIL)})
        ops.set_email(self.w.runner, "admin@example.com")
        self.assertEqual(self.cert_step()["data"]["email"], {"need": False})

    def site_email_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("kaskad.site.cli._r", lambda: self.w.runner), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            rc = cli.main(["site", "email"] + list(argv))
        return rc, out.getvalue().strip(), err.getvalue().strip()

    def test_site_email_without_argument_shows_it(self):
        """№9: ``kaskadctl site email`` — the mail now, not «either an email or --clear»."""
        with use_lang("en"):
            self.assertEqual(self.site_email_cli(), (0, "Email for Let’s Encrypt: not set.", ""))
        with use_lang("ru"):
            self.assertEqual(self.site_email_cli()[1], "Почта для Let's Encrypt: не задана.")
        ops.set_email(self.w.runner, "admin@example.com")
        with use_lang("en"):
            self.assertEqual(self.site_email_cli()[1], "Email for Let’s Encrypt: admin@example.com.")
            rc, out, _ = self.site_email_cli("--json")
            self.assertEqual((rc, json.loads(out)), (0, {"ok": True, "email": "admin@example.com"}))
            rc, _, err = self.site_email_cli("x@example.com", "--clear")
            self.assertNotEqual(rc, 0)
            self.assertIn("either an email or --clear", err)


class ListsDateTest(unittest.TestCase):
    """№7: «Lists: of Oct 10, 09:45 UTC (geoip 202610100945)» / «списки от 10.10, 09:45 UTC …», not ISO with seconds."""

    def version(self, date):
        class V(object):
            meta = {"geoip": {"date": date, "tag": "202610100945"}}
        return V()

    def test_label(self):
        from kaskad.rules import ops as rops
        v = self.version("2026-10-10T09:45:00Z")
        with use_lang("en"):
            got = rops.label(v)
        self.assertRegex(got, r"^of Oct 10(, 2026)?, 09:45 UTC \(geoip 202610100945\)$")
        with use_lang("ru"):
            got = rops.label(v)
        self.assertRegex(got, r"^от 10\.10(\.2026)?, 09:45 UTC \(geoip 202610100945\)$")
        with use_lang("en"):
            self.assertEqual(rops.label(self.version("not a date")), "of not a date (geoip 202610100945)")


class PanelWordsTest(unittest.TestCase):
    """One word per notion in the panel: the protocols of a device — «Protocols / Протоколы» (the word of the whole
    product, not «Connection methods»); the system of a device — «Platform / Платформа», as in the wizard."""

    def read(self, name):
        with open(os.path.join(STATIC, name), encoding="utf-8") as f:
            return f.read()

    def test_words(self):
        for lang, words in (("en", ("Protocols", "Platform")), ("ru", ("Протоколы", "Платформа"))):
            web, graph = self.read("i18n-%s.js" % lang), self.read("graph-i18n-%s.js" % lang)
            for old in ("Connection methods", "Which system", "Способы подключения", "Для какой системы"):
                self.assertNotIn(old, web + graph)
            self.assertIn('"pp.access": "%s"' % words[0], web)
            self.assertIn("'graph.acc.title': '%s'" % words[0], graph)
            self.assertIn("  platform: '%s'," % words[1], self.read("give-i18n-%s.js" % lang))     # the window (3.3.2)
            self.assertIn('"pp.platform": "%s"' % words[1], web)
        with open(os.path.join(STATIC, "setup", "setup-i18n-en.js"), encoding="utf-8") as f:
            self.assertIn('"d.platform": "Platform"', f.read())


class WizardMockTest(unittest.TestCase):
    """The mock of the wizard: «site-email» is a short step (setup.js calls it without a job) — in STEP."""

    def test_site_email_in_step(self):
        with open(os.path.join(STATIC, "setup", "dev-mock-setup.js"), encoding="utf-8") as f:
            js = f.read()
        step = js[js.index("  var STEP = {"):js.index("  var PLAN = {")]
        self.assertIn('"site-email": function (b)', step)
        self.assertEqual(js.count('"site-email": function'), 1)


class IssuedNamesTest(unittest.TestCase):
    """Owner, 3.3.2: issued settings are named by brand + protocol in the apps (AmneziaVPN showed «Server 1»): vpn://
    — ``description``, VLESS — ``#…``, the subscription — its title; a .conf — by its file name (≤ 15 characters of
    [a-zA-Z0-9_=+.-], the tunnel name on Android). Real issuance — tests/gw/test_awg.py, test_wg.py, test_vless.py."""

    def test_rule(self):
        from kaskad.gw import issue_os
        self.assertEqual(issue_os.app_label("AmneziaWG 3.1"), "goKaskad PRO · AmneziaWG 3.1")
        # owner №31 (2026-10-11): the device's name; no device — the brand and the protocol
        want = {("wg", None): "goKaskadPro-wg.conf", ("awg1", "1.5"): "goKaskadPro-awg15.conf",
                ("awg1", "1.0"): "goKaskadPro-awg10.conf", ("awg2", "2.0"): "goKaskadPro-awg20.conf",
                ("awg3", "3.1"): "goKaskadPro-awg31.conf", ("awg3", None): "goKaskadPro-awg31.conf"}
        for (kind, line), name in want.items():
            got = issue_os.conf_filename(kind, line)
            self.assertEqual(got, name)
            self.assertRegex(got[:-len(".conf")], r"^[a-zA-Z0-9_=+.-]{1,32}$")

    def test_no_old_names_left(self):
        """One rule everywhere: the wizard, the panel, /p/, the bot and SSH take the file name of the issuance."""
        for top, _, files in os.walk(os.path.join(ROOT, "lib", "kaskad")):
            for f in files:
                if f.endswith((".py", ".js", ".json")):
                    with open(os.path.join(top, f), encoding="utf-8") as fh:
                        text = fh.read()
                    for old in ("kaskad-wg.conf", "kaskad-awg1.conf", "kaskad-awg2.conf", "kaskad-awg3.conf",
                                '"Kaskad · %s', "Kaskad · %s · AWG"):
                        self.assertNotIn(old, text, os.path.join(top, f))


ROUTE_ST = {"schema": 1, "gateway": {
    "users": [{"id": "u-00000001", "name": "Мама", "route": {"via": "pool", "fail": "direct"}},
              {"id": "u-00000002", "name": "Папа"}],
    "devices": [{"id": "d-00000001", "name": "Телефон", "user": "u-00000001", "login": "d1"},
                {"id": "d-00000002", "name": "Ноутбук", "user": "u-00000002", "login": "d2"}],
    "routes": {"default": {"via": "direct", "fail": "block"}}}}


class InheritRouteLabelTest(unittest.TestCase):
    """Owner, 3.3.2: «Как у человека» — with the name and the route in force now; a person — «Как по умолчанию — сейчас»."""

    def test_bot(self):
        from kaskad.bot import gateway as bg
        for lang, want in (("ru", ("Как у пользователя «Мама» — сейчас: группа «Основная», при отказе — напрямую",
                                   "Как у пользователя «Папа» — сейчас: по умолчанию сервера: напрямую",
                                   "Как по умолчанию — сейчас: напрямую")),
                           ("en", ("Same as user “Мама” — now: group “Main”, on failure — direct",
                                   "Same as user “Папа” — now: server default: direct",
                                   "Same as default — now: direct"))):
            with self.subTest(lang=lang), use_lang(lang):
                got = (bg._inherit_label(ROUTE_ST, "d", ROUTE_ST["gateway"]["devices"][0]),
                       bg._inherit_label(ROUTE_ST, "d", ROUTE_ST["gateway"]["devices"][1]),
                       bg._inherit_label(ROUTE_ST, "u", ROUTE_ST["gateway"]["users"][0]))
                self.assertEqual(got, want)

    @unittest.skipUnless(__import__("shutil").which("jq"), "нет jq")
    def test_ssh(self):
        import subprocess
        import tempfile
        from tests.test_i18n_terms_sh import bash4
        b = bash4()
        if not b:
            self.skipTest("нужен bash 4+")
        d = tempfile.mkdtemp(prefix="k3-route-")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        with open(os.path.join(d, "state.json"), "w", encoding="utf-8") as f:
            json.dump(ROUTE_ST, f, ensure_ascii=False)
        script = ('source "%s" --lib >/dev/null 2>&1; KASKAD_DIR="%s"; for l in ru en; do K3_LANG=$l; '
                  'k3_gw_inherit_label device d-00000001; k3_gw_inherit_label device d-00000002; '
                  'k3_gw_inherit_label user u-00000001; done' % (os.path.join(ROOT, "install.sh"), d))
        out = subprocess.run([b, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60,
                             env=dict(os.environ, LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")).stdout.decode("utf-8")
        self.assertEqual(out.splitlines(), [
            "Как у пользователя «Мама» — сейчас: пул выходов, при отказе — напрямую",
            "Как у пользователя «Папа» — сейчас: по умолчанию сервера: напрямую",
            "Как по умолчанию — сейчас: напрямую",
            "Same as user “Мама” — now: exit pool, on failure — direct",
            "Same as user “Папа” — now: server default: direct",
            "Same as default — now: direct"])

    def test_panel(self):
        with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
            app = f.read()
        self.assertIn('inheritLabel: inheritOwnerLabel(findUser(d.user))', app)
        self.assertIn('inheritLabel: inheritOwnerLabel(findUser(st.user))', app)
        self.assertEqual(app.count('inheritLabel: inheritDefaultLabel()'), 2)
        self.assertIn("route.setInherit(inheritOwnerLabel(findUser(v)))", app)        # another owner in «New device»
        for lang, want in (("en", "Same as user “{name}” — now: {r}"), ("ru", "Как у пользователя «{name}» — сейчас: {r}")):
            with open(os.path.join(STATIC, "i18n-%s.js" % lang), encoding="utf-8") as f:
                self.assertIn('"route.inheritOwnerNow": "%s"' % want, f.read())


FAKE_APPS = {
    "schema": 1,
    "apps": {"wireguard": {"name": "WireGuard", "links": {"ios": [{"kind": "appstore", "url": "https://apps.apple.com/app/id1441195209"}],
                                                          "windows": [{"kind": "site", "url": "https://www.wireguard.com/install/"},
                                                                      {"kind": "site", "url": "javascript:alert(1)"}]}},
             "defaultvpn": {"name": "DefaultVPN", "from_vendor": "defaultvpn"},
             "amneziavpn": {"name": "AmneziaVPN", "from_vendor": "amneziavpn"}},
    "protocols": {"wg": {"ios": ["wireguard"], "windows": ["wireguard"]},
                  "awg3": {"ios": ["defaultvpn", "amneziavpn"]},
                  "l2tp": {"windows": "builtin"}},
    "builtin_note": {"l2tp": {"windows": {"ru": "Встроено в Windows", "en": "Built into Windows"}}}}


class InstallAppsTest(unittest.TestCase):
    """«Установите приложение» (owner, 3.3.2): apps of the pair protocol + system with store links from the catalogs;
    no app — an honest line, the protocol is not swapped."""

    def setUp(self):
        from kaskad.gw import install_apps
        self.m = install_apps
        p = mock.patch.dict(install_apps._cache, {"doc": FAKE_APPS})
        p.start()
        self.addCleanup(p.stop)

    def test_links_recommended_first_https_only(self):
        with use_lang("en"):
            b = self.m.block("wg", "windows")
        self.assertTrue(b["supported"])
        self.assertEqual([(a["name"], a["recommended"]) for a in b["apps"]], [("WireGuard", True)])
        self.assertEqual([(l["label"], l["url"]) for l in b["apps"][0]["links"]], [("Website", "https://www.wireguard.com/install/")])

    def test_amneziawg_links_from_the_vendor_catalog(self):
        b = self.m.block("awg3", "ios")
        self.assertEqual([a["id"] for a in b["apps"]], ["defaultvpn", "amneziavpn"])
        self.assertTrue(b["apps"][0]["recommended"])
        self.assertTrue(all(l["url"].startswith("https://apps.apple.com/") for a in b["apps"] for l in a["links"]))
        with use_lang("ru"):
            note = self.m.block("awg3", "ios")["apps"][1]["links"][0]["note"]
        self.assertIn("российском App Store", note)                        # AmneziaVPN is hidden there

    def test_builtin_and_none(self):
        with use_lang("ru"):
            self.assertEqual(self.m.block("l2tp", "windows"), {"supported": True, "builtin": "Встроено в Windows", "apps": [], "text": ""})
            none = self.m.block("vless", "keenetic")
        self.assertEqual(none["supported"], False)
        self.assertEqual(none["text"], "VLESS на Keenetic — нет приложения: выберите другой протокол или систему.")
        with use_lang("en"):
            self.assertEqual(self.m.block("vless", "macos")["text"],
                             "VLESS on macOS — there is no app: choose another protocol or system.")

    def test_the_real_catalog(self):
        """The shipped catalog: every link https, every app of a protocol known, AmneziaWG apps from the vendor."""
        self.m._cache.pop("doc", None)
        doc = self.m.catalog()
        self.assertEqual(doc.get("schema"), 1)
        from kaskad.gw import issue_os, model
        apps = doc["apps"]
        for kind, per in doc["protocols"].items():
            self.assertIn(kind, issue_os.TITLES)
            for plat, ids in per.items():
                self.assertIn(plat, model.PLATFORMS)
                if ids == "builtin":
                    continue
                for i in ids:
                    self.assertIn(i, apps, (kind, plat))
                with use_lang("en"):
                    b = self.m.block(kind, plat)
                self.assertTrue(b["supported"], (kind, plat, b))
                for a in b["apps"]:
                    for l in a["links"]:
                        self.assertRegex(l["url"], r"^https://[^\s\"'<>]+$")
        for i, a in apps.items():
            for plat, links in (a.get("links") or {}).items():
                for l in links:
                    self.assertRegex(l["url"], r"^https://", (i, plat))
        # iPhone: DefaultVPN first for every AmneziaWG line it supports (AmneziaVPN is hidden in the Russian App Store)
        for kind in ("awg2", "awg3"):
            ids = doc["protocols"][kind].get("ios")
            self.assertEqual(ids[0], "defaultvpn", kind)

    def test_mocks_from_the_catalogs(self):
        """The mocks of the panel and the wizard show the apps of the real catalogs (tools/gen_mock_install.py)."""
        import subprocess
        import sys
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gen_mock_install.py"), "--check"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout.decode("utf-8", "replace"))

    def test_ops_issue_carries_it(self):
        with open(os.path.join(ROOT, "lib", "kaskad", "gw", "ops.py"), encoding="utf-8") as f:
            self.assertIn('"install": install_apps.block(kind, os_name)', f.read())


class IssueUxStaticTest(unittest.TestCase):
    """The choices of the system and the protocol are independent (no reset of one by the other), the order of the
    window as goAmnezia's, every key QR of one size, store QRs smaller and equal, protocol chips open the issuance."""

    def read(self, name):
        with open(os.path.join(STATIC, name), encoding="utf-8") as f:
            return f.read()

    def test_choice_kept(self):
        app = self.read("app.js")
        issue = app[app.index("  function openIssue(dev, kind) {"):app.index("  // ── 19. Выходы")]
        # 3.3.2, the new window (give.js): the platform and the protocol are two rows, one does not reset the other
        self.assertIn("onPlatform: (p) => { st.os = p; run(); }", issue)
        self.assertIn("onProto: (k) => { st.inbound = k; run(); }", issue)
        self.assertNotIn('st.inbound = ""', issue)
        give = self.read("give.js")
        sel = give[give.index("    function selectors(vm) {"):give.index("    function protoRow(vm, labelless) {")]
        self.assertLess(sel.index("chipRow(T('platform')"), sel.index("out.push(protoRow(vm))"))     # system → protocol
        self.assertIn("st.app = ''; st.routerRow = false; o.onPlatform(p);", sel)    # a new platform — its recommended app
        setup = self.read("setup/setup.js")
        self.assertIn('on: { click: () => { u.pf = p.id; loadOffers(p.id); render(); } }', setup)
        self.assertIn('t("d.noApp", { p: inboundTitle(u.inp), os: pfTitle(u.pf) })', setup)

    def test_qr_sizes(self):
        self.assertIn(".kk-item .kq { --kq-size: 360px; }", self.read("styles.css"))
        self.assertNotIn("kq--big", self.read("styles.css") + self.read("components.css") + self.read("app.js"))
        self.assertIn("--kq-size: 360px;", self.read("setup/qs.css"))
        self.assertIn("max-width: 360px", self.read("user.css"))
        for name, sel in (("styles.css", ".kk-store__qr"), ("setup/qs.css", ".qs-store__qr"), ("user.css", ".up-store-qr")):
            css = self.read(name)
            rule = css[css.index(sel + " {"):]
            rule = rule[:rule.index("}")]
            self.assertIn("width: 120px", rule, name)
            self.assertIn("height: 120px", rule, name)

    def test_protocol_chips(self):
        app = self.read("app.js")
        self.assertIn("onclick: () => openIssue(findDevice(d.id), k)", app)
        self.assertIn('xtSlots(protoSort(d.access), (k) => protoChip(d, k), "proto")', app)     # UI-4: строка устройства таблицы К1; Т3 — столбец сворачивается
        for lang, want in (("en", "Issue settings: {name}"), ("ru", "Выдать настройки: {name}")):
            self.assertIn('"pp.protoIssue": "%s"' % want, self.read("i18n-%s.js" % lang))
        user = self.read("user.js")                  # /p/: a chip of a device opens its issuance under the list
        self.assertIn("class: 'gs-chip' + (lv === 'none' ? ' is-dim' : ''), 'data-kind': inb.kind", user)   # FIX-332g №5
        self.assertIn("showInbound(holder, dev, inb.kind)", user)


from tests.userpage_case import PageCase  # noqa: E402


class PageInstallTest(PageCase):
    """/p/: «Установите приложение» of every protocol of a device, with the QR of every store link drawn by the server
    (the page draws no QR itself); a router without an app — the honest line."""

    def test_store_links_with_qr(self):
        with use_lang("ru"):
            doc = self.keys_json(self.token())
        phone = doc["devices"][0]                                          # android: wg, awg2
        wg = next(i for i in phone["inbounds"] if i["kind"] == "wg")
        inst = wg["install"]
        self.assertTrue(inst["supported"])
        self.assertEqual(inst["apps"][0]["name"], "WireGuard")
        self.assertTrue(inst["apps"][0]["recommended"])
        for ln in inst["apps"][0]["links"]:
            self.assertTrue(ln["url"].startswith("https://"))
            self.assertGreaterEqual(ln["qr"]["n"], 21)
        self.assertIn(inst["apps"][0]["links"][0]["url"], [inp for _, inp in self.qr_calls])


if __name__ == "__main__":
    unittest.main()

"""3.3.2 critic-fix (T-332 №1, 2, 18, 19, 31): the labels of other apps in the English texts are those of their
current English interface, checked against their sources (2026-10-11):

* AmneziaVPN — «+» → “Insert” (pastes the key) / “QR code” / “File with connection settings”, “Connect”
  (amnezia-client 5.0.3.0, PageSetupWizardConfigSource.qml); DefaultVPN — the same per docs.amnezia.org, its QR item
  is not confirmed → neutral words;
* AmneziaWG / WireGuard — Android “Scan from QR code”, iOS “Create from QR code”, Windows “Import tunnel(s) from file”,
  “Add Tunnel” → “Add empty tunnel…”, macOS “Import Tunnel(s) from File…”, “Add Empty Tunnel…” (strings.xml,
  Localizable.strings, messages.gotext.json); AmneziaWG does not take a vpn:// link;
* Keenetic — “Import from a file”, “Create connection”, “Secret key”, “Use for accessing the Internet”
  (support.keenetic.com); AmneziaWG 1.5/2.0 from KeeneticOS 5.1.1, 1.0 from 4.2;
* OpenWrt LuCI — “Load configuration…” (luci-proto-wireguard);
* v2RayTun, Streisand — no source → neutral words without a quoted label.

One action has one name on every surface: the Python catalogs, the web dictionaries, the SSH menu."""

import json
import os
import re
import unittest

from kaskad.gw import issue_os
from kaskad.i18n import use_lang

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAT = os.path.join(ROOT, "lib/kaskad/i18n/catalog")
STATIC = os.path.join(ROOT, "lib/kaskad/panel/static")

# Labels that are not in the apps (or not confirmed): “quoted” as if a button.
STALE = ("“Paste”", "“Paste from clipboard”", "“Insert from clipboard”", "“Import from file”", "“Load from file”",
         "“Add connection”", "“Scan QR code”", "“Import from clipboard”", "“Load configuration”",
         "“Use for accessing the internet”", "“Add empty tunnel”", "“Import Tunnel(s) from File”")
# Our own buttons that share a word with an app label.
OWN = {"setup.steps.subFound"}


def _catalog(lang):
    out = {}
    for name in sorted(os.listdir(os.path.join(CAT, lang))):
        with open(os.path.join(CAT, lang, name), encoding="utf-8") as f:
            out.update(json.load(f))
    return out


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _web_value(src, key):
    m = re.search(r'^\s*"?%s"?:\s*(["\'])((?:\\.|(?!\1).)*)\1' % re.escape(key), src, re.M)
    return m.group(2) if m else None


class AppLabels(unittest.TestCase):
    def test_no_stale_labels_anywhere_in_english(self):
        texts = {k: v for k, v in _catalog("en").items() if k not in OWN}
        for rel in ("lib/kaskad/panel/static/i18n-en.js", "lib/kaskad/panel/static/user-i18n-en.js",
                    "lib/kaskad/panel/static/setup/setup-i18n-en.js", "lib/kaskad/vendor/clients.json"):
            texts[rel] = _read(rel)
        sh = os.path.join(ROOT, "lib/kaskad/i18n/sh")
        for name in sorted(os.listdir(sh)):
            if name.endswith(".en.sh"):
                texts[name] = _read(os.path.join("lib/kaskad/i18n/sh", name))
        for where, text in texts.items():
            for label in STALE:
                self.assertNotIn(label, text, "%s: %s" % (where, label))

    def test_amneziawg_is_never_offered_a_vpn_link(self):
        for lang in ("en", "ru"):
            cat = _catalog(lang)
            for k in ("gw_issue.offer.awg_vpn", "gw_issue.offer.awg_vpn_qr", "gw_issue.offer.awg_vpn_conf"):
                self.assertNotRegex(cat[k], r"AmneziaWG[^;]*vpn://", "%s %s" % (lang, k))
                self.assertIn(".conf", cat[k].split(";")[1], "%s %s" % (lang, k))

    def test_wireguard_steps_name_the_label_of_the_platform(self):
        with use_lang("en"):
            want = {"android": "“Scan from QR code”", "ios": "“Create from QR code”",
                    "windows": "“Add empty tunnel…”", "macos": "“Add Empty Tunnel…”"}
            for p, label in want.items():
                self.assertIn(label, " ".join(issue_os.howto(p, "wg", where="panel")), p)
            bot_mac = " ".join(issue_os.howto("macos", "wg", where="bot"))
            self.assertIn("“Import Tunnel(s) from File…”", bot_mac)
            self.assertIn("“Import tunnel(s) from file”", " ".join(issue_os.howto("windows", "wg", where="bot")))
        with use_lang("ru"):                     # the Russian labels of the Russian interface (critic2 №4)
            self.assertIn("«Сканировать QR-код»", " ".join(issue_os.howto("android", "wg", where="panel")))
            self.assertIn("«Создать из QR-кода»", " ".join(issue_os.howto("ios", "wg", where="panel")))
            win = " ".join(issue_os.howto("windows", "wg", where="panel"))
            self.assertIn("«Добавить туннель» → «Добавить пустой туннель…»", win)
            self.assertIn("«Подключить»", win)
            mac = " ".join(issue_os.howto("macos", "wg", where="panel"))
            self.assertIn("«Добавить пустой туннель…»", mac)
            self.assertNotIn("«Подключить»", mac)       # the macOS app says «Подключен» — the step is neutral
            self.assertIn("«Импорт туннелей из файла…»", " ".join(issue_os.howto("macos", "wg", where="bot")))

    def test_amneziavpn_insert_and_keenetic_labels(self):
        with use_lang("en"):
            awg = " ".join(issue_os.howto("android", "awg3", where="panel"))
            self.assertIn("“Insert” (pastes the key from the clipboard) → “Continue”, then “Connect”", awg)
            kee = " ".join(issue_os.steps("keenetic", "l2tp", where="panel"))
            for label in ("“Create connection”", "“Secret key”", "“Use for accessing the Internet”"):
                self.assertIn(label, kee)
            self.assertIn("“Import from a file”", " ".join(issue_os.howto("keenetic", "awg2", where="panel")))

    def test_qr_how_is_one_text_on_every_page(self):
        # 3.3.2 FIX-332f: one window (give.js) for the panel, /p/ and the wizard — the steps in the apps live in one
        # dictionary (give-i18n path.*), the words of the server's steps («Paste»/«Вставить») and of the window agree
        for lang, paste in (("en", "Paste from clipboard"), ("ru", "Вставить из буфера")):
            give = _read("lib/kaskad/panel/static/give-i18n-%s.js" % lang)
            for app in ("defaultvpn", "amneziavpn"):
                self.assertRegex(give, r"\n    %s: '\+ › %s › " % (app, paste), (lang, app))
        for n in ("i18n-en.js", "setup/setup-i18n-en.js", "user-i18n-en.js", "i18n-ru.js", "setup/setup-i18n-ru.js", "user-i18n-ru.js"):
            self.assertNotIn("qr_how_", _read("lib/kaskad/panel/static/" + n), n)
            self.assertNotIn("qr.how_", _read("lib/kaskad/panel/static/" + n), n)

    def test_keenetic_versions_agree(self):
        for lang in ("en", "ru"):
            pn = re.search(r"^M_%s_GW_PN_AWG='(.*)'$" % lang,
                           _read("lib/kaskad/i18n/sh/gw.%s.sh" % lang), re.M).group(1)
            self.assertIn("5.1.1+", pn)
            self.assertNotRegex(pn, r"\b5\.0\b")                # «Keenetic before 5.0» was wrong
            self.assertIn("4.2", pn)
            self.assertIn("5.1.1", _catalog(lang)["gw_issue.offer.keenetic_awg1"])
            self.assertIn("5.1.1+", _web_value(_read("lib/kaskad/panel/static/i18n-%s.js" % lang), "ibd.awg2"))


class RussianAppLabels(unittest.TestCase):
    """critic2 №1–9: the Russian labels of other apps are those of their Russian interface (decision after the second
    critique — an exception to «Russian as in 3.3.1»): AmneziaVPN 5.0.3.0 amneziavpn_ru_RU.ts («Вставить»,
    «Продолжить», «QR-код», «Подключиться»); WireGuard / AmneziaWG values-ru, ru.lproj, locales/ru; LuCI po/ru;
    Keenetic support.netcraze.ru («Добавить подключение», «Секретный ключ»); Apple mh35561 / mchlp2963 by macOS
    version. Not confirmed by a source — neutral words without a quoted label (Hiddify/v2RayTun paste, DefaultVPN,
    iOS «add a VPN configuration», Android certificates, Xiaomi, the Windows terminal)."""

    STALE_RU = ("«Вставить из буфера»", "«Сканировать QR»", "«Импорт из буфера обмена»", "«Добавить подписку»",
                "«Импорт из буфера»", "«Импортировать туннели из файла»", "«Загрузить из файла»",
                "«Использовать для выхода в интернет»", "«Load configuration»", "«Терминал (администратор)»",
                "«Добавить службу»", "«Сертификат ЦС»", "«получен от сервера»", "«Подключить».»")

    def test_no_stale_russian_labels(self):
        texts = {k: v for k, v in _catalog("ru").items() if k not in OWN and k != "gw_issue.howto.wg_connect"}
        for rel in ("i18n-ru.js", "user-i18n-ru.js", "setup/setup-i18n-ru.js"):
            texts[rel] = _read("lib/kaskad/panel/static/" + rel)
        for where, text in texts.items():
            for label in self.STALE_RU:
                self.assertNotIn(label, text, "%s: %s" % (where, label))

    def test_amneziavpn(self):
        with use_lang("ru"):
            awg = " ".join(issue_os.howto("android", "awg3", where="panel"))
            self.assertIn("«+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться»", awg)
            ios = issue_os.howto("ios", "awg3", where="panel")[-1]
            self.assertEqual(ios, "В DefaultVPN: нажмите «+», вставьте ключ из буфера и подключитесь.")
        with use_lang("en"):
            self.assertEqual(issue_os.howto("ios", "awg3", where="panel")[-1],
                             "In DefaultVPN: tap “+”, insert the key from the clipboard and connect.")
    def test_luci_keenetic_macos(self):
        ru, en = _catalog("ru"), _catalog("en")
        for k in ("gw_in_wg.hint.openwrt", "gw_in_awg.hint.openwrt", "gw_issue.howto.ui_openwrt"):
            self.assertIn("«Загрузка конфигурации…»", ru[k], k)
        self.assertIn("«Секретный ключ»", ru["gw_issue.steps.keenetic_l2tp"])
        self.assertIn("«Добавить подключение»", ru["gw_issue.steps.keenetic_add"])
        for k in ("gw_issue.note.keenetic", "gw_in_awg.hint.keenetic_31"):
            self.assertIn("5.2 Alpha 11", ru[k], k)
            self.assertIn("5.2 Alpha 11", en[k], k)
        for k in ("gw_issue.mac.install", "gw_in_ikev2.how.macos"):
            self.assertIn("Основные → Управление устройством (macOS 13–14: Конфиденциальность и безопасность → Профили)",
                          ru[k], k)
            self.assertIn("General → Device Management (macOS 13–14: Privacy and Security → Profiles)", en[k], k)
        self.assertIn("Сеть → меню действий (…) → «Добавить конфигурацию VPN» → «L2TP через IPSec»",
                      ru["gw_issue.steps.macos_l2tp_add"])
        self.assertIn("Network → the Action pop-up menu (…) → “Add VPN Configuration”", en["gw_issue.steps.macos_l2tp_add"])


if __name__ == "__main__":
    unittest.main()

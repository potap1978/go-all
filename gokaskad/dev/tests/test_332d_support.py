"""3.3.2 (FIX-332d): the support table «платформа × протокол» (k3/handoff/SPEC-issue-fallbacks.md), the catalog of
apps by the research of 2026-10-11 (icons in the product, fakes, «не проверено»), ``support`` / ``alternatives`` of an
issuance and the names of .conf files (owner, OWNER-FEEDBACK-3.3 №31)."""

import json
import os
import re
import struct
import unittest

from kaskad.gw import install_apps, issue_os, model
from kaskad.i18n import use_lang

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
HTTPS = re.compile(r"^https://[^\s\"'<>]+$")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class TableTest(unittest.TestCase):
    def setUp(self):
        install_apps._cache.pop("doc", None)
        self.doc = install_apps.catalog()

    def test_full_14_by_12(self):
        self.assertEqual(len(model.PLATFORMS), 14)
        self.assertEqual(len(issue_os.TITLES), 13)                       # T12: + Hysteria2 (hy2)
        sup = self.doc["support"]
        self.assertEqual(sorted(sup), sorted(model.PLATFORMS))
        for p in model.PLATFORMS:
            protos = [r["proto"] for r in sup[p]]
            self.assertEqual(sorted(protos), sorted(issue_os.TITLES), p)      # every protocol once, nothing missing
            self.assertEqual(install_apps.order(p), protos, p)
            for r in sup[p]:
                self.assertIn(r["level"], install_apps.LEVELS, (p, r))
                self.assertLessEqual(set(r), {"proto", "level", "min", "guide", "note", "links", "unverified"}, (p, r))
                for f in ("min", "note", "guide"):
                    if f in r:
                        self.assertEqual(sorted(r[f]), ["en", "ru"], (p, r["proto"], f))
                        self.assertTrue(r[f]["ru"] and r[f]["en"], (p, r["proto"], f))
                for u in list((r.get("guide") or {}).values()) + [l["url"] for l in r.get("links") or []]:
                    self.assertRegex(u, HTTPS, (p, r["proto"]))

    def test_levels_agree_with_the_install_block(self):
        """A pair the table calls native/app/package has its apps or its built-in client; «none» has none."""
        for p in model.PLATFORMS:
            for k in issue_os.TITLES:
                lv = install_apps.entry(k, p)["level"]
                with use_lang("en"):
                    b = install_apps.block(k, p)
                if lv in ("native", "app", "package"):
                    self.assertTrue(b["supported"], (p, k, lv))
                if lv == "none":
                    self.assertFalse(b["supported"], (p, k))

    def test_research_facts(self):
        e = install_apps.entry
        self.assertEqual(e("awg3", "keenetic")["level"], "native")
        self.assertIn("5.2 Alpha 11", e("awg3", "keenetic")["min"]["ru"])
        self.assertIn("AWG Manager", e("awg3", "keenetic")["note"]["ru"])
        self.assertEqual(e("vless", "mikrotik")["level"], "workaround")
        self.assertEqual(e("vless", "mikrotik")["guide"]["ru"], "https://anten-ka.com/guides/no30/")
        self.assertEqual(e("awg3", "huawei")["level"], "none")
        self.assertEqual(e("pptp", "ios")["level"], "none")
        self.assertEqual(e("l2tp", "android")["level"], "none")
        self.assertEqual(e("awg3", "openwrt")["level"], "package")
        self.assertEqual(install_apps.order("huawei")[:2], ["l2tp", "pptp"])
        self.assertEqual(install_apps.order("keenetic")[0], "awg2")       # the main channel 5.1 takes 2.0

    def test_apps_links_icons_fakes(self):
        apps = self.doc["apps"]
        self.assertNotIn("nekobox", apps)                                   # stands still since 02.2026
        for i, a in apps.items():
            for plat, links in (a.get("links") or {}).items():
                for l in links:
                    self.assertRegex(l["url"], HTTPS, (i, plat))
                    self.assertNotIn("search", l["url"], (i, plat))        # a direct link by id, never a search
            icon = a.get("icon")
            self.assertEqual(icon, "apps/%s.png" % i)
            with open(os.path.join(STATIC, icon), "rb") as f:
                head = f.read(24)
                size = 24 + len(f.read())
            self.assertEqual(head[:8], b"\x89PNG\r\n\x1a\n", i)
            w, h = struct.unpack(">II", head[16:24])
            self.assertTrue(64 <= w <= 96 and w == h, (i, w, h))
            self.assertLess(size, 20000, i)
            self.assertEqual(install_apps.icon_of(icon), icon)
            for f in ("fakes", "unverified"):
                for plat in a.get(f) or []:
                    self.assertIn(plat, model.PLATFORMS, (i, f))
        self.assertEqual(install_apps.icon_of("../app.js"), "")
        with use_lang("ru"):
            ios = install_apps.block("vless", "ios")["apps"]
            self.assertEqual([a["id"] for a in ios][:3], ["karing", "incy", "shadowrocket"])
            self.assertTrue(next(a for a in ios if a["id"] == "incy")["fakes"])
            self.assertEqual(next(a for a in ios if a["id"] == "incy")["unverified"], "")     # VLESS of incy is fine
            awg = install_apps.block("awg3", "ios")["apps"]
            self.assertEqual(awg[0]["id"], "defaultvpn")
            self.assertTrue(next(a for a in awg if a["id"] == "incy")["unverified"])      # AmneziaWG 2.0/3.1 — not
            self.assertEqual(ios[0]["icon"], "apps/karing.png")
            self.assertEqual(install_apps.block("awg3", "keenetic")["apps"][1]["id"], "awg-manager")
        with use_lang("en"):                                                # T-M6-332c №7: names RU/EN
            names = [a["name"] for a in install_apps.block("awg2", "mikrotik")["apps"]]
            self.assertIn("MikroTik (container)", names)
            self.assertFalse(any(re.search("[а-яё]", n, re.I) for n in names))

    def test_vless_steps_name_the_first_app(self):
        first = {"ios": "karing", "macos": "karing", "windows": "v2rayn", "linux": "v2rayn", "android": "v2raytun"}
        for p, app in first.items():
            self.assertEqual(self.doc["protocols"]["vless"][p][0], app, p)
            self.assertEqual(issue_os.vless_apps(p)[0].lower(), app, p)


class SupportTest(unittest.TestCase):
    def test_alternatives_by_state(self):
        alts = install_apps.alternatives("awg3", "huawei", server=["l2tp"], device=["awg3"], where="panel")
        self.assertEqual([(a["proto"], a["state"]) for a in alts], [("l2tp", "device_off"), ("pptp", "server_off")])
        alts = install_apps.alternatives("vless", "android", server=["awg3", "wg", "vless"], device=["awg3", "vless"])
        st = dict((a["proto"], a["state"]) for a in alts)
        self.assertEqual((st["awg3"], st["wg"], st["ikev2"]), ("ready", "device_off", "server_off"))
        self.assertNotIn("vless", st)                                          # not the protocol itself
        self.assertNotIn("l2tp", st)                                           # none on Android 12+
        self.assertEqual([a["proto"] for a in alts][0], "awg3")                # the order of recommendation
        known = install_apps.alternatives("vless", "android", known=["wg"])
        self.assertEqual([a["proto"] for a in known], ["wg"])

    def test_texts(self):
        with use_lang("ru"):
            alts = install_apps.alternatives("awg3", "huawei", server=[], device=[], where="page")
            s = install_apps.support("awg3", "huawei", alts, where="page")
            self.assertEqual(s["level"], "none")
            self.assertEqual(s["text"], "Huawei не поддерживает AmneziaWG 3.1. Подойдёт:")
            self.assertEqual(s["hints"][1], "• Попросите администратора включить L2TP/IPsec")
            self.assertEqual(s["levels"]["l2tp"], "native")
            w = install_apps.support("vless", "mikrotik", install_apps.alternatives("vless", "mikrotik"))
            self.assertEqual(w["text"], "MikroTik не умеет VLESS штатно. Можно обходным путём:")
            self.assertIn("Инструкция: https://anten-ka.com/guides/no30/", w["hints"])
            self.assertTrue(w["hints"][-1].startswith("Другие протоколы для этой системы: WireGuard"))
            k = install_apps.support("awg3", "keenetic", [])
            self.assertEqual(k["text"], "")
            self.assertIn("Нужно: KeeneticOS 5.2 Alpha 11+", k["hints"][0])
            self.assertEqual([l["url"] for l in k["links"]][1], "https://awgm.hoaxisr.ru/install/")
            self.assertEqual(install_apps.support("ikev2", "tplink", [])["text"],
                             "Для TP-Link у нас нет подходящего протокола.")
            self.assertEqual(install_apps.support("wg", "tplink", [])["unverified"], "Не проверено на устройстве — проверьте сами")
            menu = install_apps.alternatives("awg3", "huawei", where="menu")[0]["text"]
            self.assertIn("17 → 1", menu)
        with use_lang("en"):
            s = install_apps.support("awg3", "huawei", install_apps.alternatives("awg3", "huawei", where="page"), where="page")
            self.assertEqual(s["text"], "Huawei does not support AmneziaWG 3.1. These will do:")
            self.assertEqual(s["hints"][1], "• Ask the administrator to turn on L2TP/IPsec")

    def test_ops_issue_and_the_screens_carry_it(self):
        ops = _read("lib/kaskad/gw/ops.py")
        self.assertIn('"support": install_apps.support(kind, os_name, alts, where=where), "alternatives": alts', ops)
        self.assertIn('inb["support"] = _support_out(got.get("support"), got.get("alternatives"))',
                      _read("lib/kaskad/panel/userpage.py"))
        self.assertIn('sup.get("hints")', _read("lib/kaskad/bot/gateway.py"))
        self.assertIn('sup.get("hints")', _read("lib/kaskad/gw/cli.py"))
        self.assertIn(".support.hints[]?", _read("install.sh"))
        app = _read("lib/kaskad/panel/static/app.js")
        # 3.3.2: the window is give.js (panel, /p/, wizard); support and alternatives come in the issuance
        self.assertIn('onEnable: (k) => { d.close(); goNav("inbounds", { open: k }); }', app)
        self.assertIn("API.devices.access(dev.id, { kind: k, on: true })", app)
        give = _read("lib/kaskad/panel/static/give.js")
        self.assertIn("var alts = arr(r.alternatives).length ? arr(r.alternatives) : arr(s.alternatives);", give)
        self.assertIn("alternatives: s && Array.isArray(s.alternatives) ? s.alternatives : []", _read("lib/kaskad/panel/static/user.js"))

    def test_userpage_support_out(self):
        from kaskad.panel import userpage
        out = userpage._support_out({"level": "none", "text": "x", "guide": "http://bad", "links": [{"url": "https://a/"}]},
                                    [{"proto": "l2tp", "title": "L2TP/IPsec", "state": "server_off", "text": "y"}])
        self.assertEqual((out["guide"], [l["url"] for l in out["links"]]), ("", ["https://a/"]))
        self.assertEqual(out["alternatives"][0]["state"], "server_off")
        self.assertTrue(userpage.ICON_PAT.match("apps/karing.png"))
        self.assertFalse(userpage.ICON_PAT.match("apps/../user.js"))


class M6FindingsTest(unittest.TestCase):
    """T-M6-332c №5, №8: the reason of a switched-off protocol is reachable without hover; the VLESS hint is not one
    text for every platform."""

    def test_off_chip(self):
        app = _read("lib/kaskad/panel/static/app.js")
        i = app.index("function protoChip(d, k)")
        chip = app[i:i + 900]
        self.assertIn('"aria-label": t("pp.protoOff", { name })', chip)
        self.assertIn('onclick: () => toast(t("pp.protoOff", { name })', chip)

    def test_no_single_vless_hint(self):
        for rel in ("lib/kaskad/panel/static/app.js", "lib/kaskad/panel/static/user.js",
                    "lib/kaskad/panel/static/setup/setup.js"):
            self.assertNotIn("how_vless", _read(rel), rel)
        self.assertIn("if (prev && prev.os !== r.os) { st.app = ''; }", _read("lib/kaskad/panel/static/give.js"))


class ConfNameTest(unittest.TestCase):
    def test_names(self):
        f = issue_os.conf_filename
        mama = {"name": "Телефон мамы", "access": ["awg3"]}
        self.assertEqual(f("awg3", device=mama, platform_name="ios"), "goKaskadPro-telefon-mamy.conf")
        self.assertEqual(f("awg3", device=mama, platform_name="android"), "gKP-telefon-mam.conf")
        two = dict(mama, access=["awg3", "wg", "vless"])
        self.assertEqual(f("awg3", device=two, platform_name="windows"), "goKaskadPro-telefon-mamy-awg31.conf")
        self.assertEqual(f("wg", device=two, platform_name="linux"), "gKP-telefon-wg.conf")
        self.assertEqual(f("awg2", device=two, platform_name="androidtv"), "gKP-telef-awg20.conf")
        self.assertEqual(f("wg", device={"name": "😀 ✈", "access": ["wg"]}, platform_name="keenetic"), "goKaskadPro-wg.conf")
        self.assertEqual(issue_os.translit("Ёлка  Юли / iPad"), "elka-yuli-iPad")
        long_name = {"name": "Очень длинное имя компьютера бухгалтерии на втором этаже", "access": ["wg", "awg1"]}
        for p in model.PLATFORMS:
            for kind in issue_os.CONF_KINDS:
                for dev in (mama, two, long_name, {"name": "", "access": []}):
                    name = f(kind, device=dev, platform_name=p)[:-len(".conf")]
                    limit = 15 if p in ("android", "androidtv", "linux", "other") else 32
                    self.assertRegex(name, r"^[A-Za-z0-9_.-]{1,%d}$" % limit, (p, kind, dev["name"]))
                    self.assertTrue(name.startswith("gKP-" if limit == 15 else "goKaskadPro-"), name)
                    if len([k for k in dev["access"] if k in issue_os.CONF_KINDS]) > 1:
                        self.assertTrue(name.endswith("-" + issue_os.conf_code(kind)), name)

    def test_brand_in_apps_stays(self):
        self.assertEqual(issue_os.app_label("VLESS"), "goKaskad PRO · VLESS")
        # owner, T-M6-332c №9: the device's name after the protocol (vless «#…», the subscription, vpn:// description)
        self.assertEqual(issue_os.app_label("VLESS", "iPhone  Маши "), "goKaskad PRO · VLESS · iPhone Маши")
        self.assertEqual(issue_os.app_label("AmneziaWG 3.1", "Ноутбук"), "goKaskad PRO · AmneziaWG 3.1 · Ноутбук")
        self.assertIn('issue_os.app_label("VLESS", device.get("name")', _read("lib/kaskad/gw/inbounds/vless.py"))
        self.assertIn('issue_os.app_label("AmneziaWG %s" % line, dev.get("name"))', _read("lib/kaskad/gw/inbounds/awg.py"))


class PayloadTest(unittest.TestCase):
    def test_icons_ship(self):
        self.assertIn('".png"', _read("tools/build_payload.py"))


if __name__ == "__main__":
    unittest.main()

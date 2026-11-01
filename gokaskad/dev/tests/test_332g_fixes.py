"""3.3.2 FIX-332g: the critic's findings on the «Выдать настройки» window (k3/stand/logs/T-332-critic3.md).

Every place that mounts give.js asks for an app's icon at ONE address that a real route of the server answers (the
panel, the wizard under /setup-<secret>/, /p/ with and without a site prefix), a broken icon becomes the letter tile."""

import json
import os
import re
import unittest
from urllib.parse import urljoin, urlparse

from kaskad import i18n
from kaskad.gw import install_apps
from kaskad.panel import routes
from kaskad.panel.routes import Request

from tests.helpers import RootCase
from tests.test_332f_give import HAS_NODE, issuance, prelude, read, run_node

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def icon_base(name, pattern):
    m = re.search(pattern, read(name))
    assert m, (name, pattern)
    return m.group(1)


def get(path):
    """GET ``path`` through the route table of the panel's server (server.py + userpage.py), as a browser asks."""
    import kaskad.panel.server  # noqa: F401 — the static routes
    import kaskad.panel.userpage  # noqa: F401 — /p/…
    r, m = routes.find("GET", path)
    if r in (None, "method"):
        return 404, None
    try:
        resp = r.handler(Request("GET", path, match=m))
    except routes.HttpError as e:
        return e.status, None
    return resp.status, resp.headers.get("Content-Type")


class IconRoutesTest(unittest.TestCase):
    """№1, №6: the icon's address of every page is answered by a route (routes.find + the handler), not a 404."""

    def icons(self):
        with open(os.path.join(ROOT, "lib", "kaskad", "gw", "install_apps.json"), encoding="utf-8") as f:
            apps = json.load(f)["apps"]
        out = sorted(set(a["icon"] for a in (apps.values() if isinstance(apps, dict) else apps) if a.get("icon")))
        self.assertTrue(out)
        return out

    def test_every_page_asks_a_route_that_answers(self):
        panel = icon_base("app.js", r'iconBase: "([^"]*)"')
        wizard = icon_base(os.path.join("setup", "setup.js"), r'iconBase: "([^"]*)"')
        self.assertIn("iconBase: siteBase() + '/p/'", read("user.js"))
        pages = (("panel", "https://h/", panel, ""), ("wizard", "https://h/setup-Abc123/", wizard, ""),
                 ("/p/", "https://h/p/%s/" % ("A" * 43), "/p/", ""))
        for icon in self.icons():
            self.assertRegex(icon, r"^apps/[a-z0-9-]+\.png$")
            for who, page, base, _ in pages:
                url = urljoin(page, base + icon)
                status, ctype = get(urlparse(url).path)
                self.assertEqual((who, status, ctype), (who, 200, "image/png"), url)
        # the old addresses of 3.3.2 before the fix are what the critic saw: /apps/<id>.png has no route
        self.assertEqual(get("/apps/karing.png")[0], 404)

    def test_p_under_a_prefix_keeps_its_icons(self):
        # user.js: siteBase() of /prefix/p/<token>/ is «/prefix»; nginx strips it, the route sees /p/apps/…
        js = prelude() + r"""
const R = %s;
const g = G.mount({ viewer: "person", lang: "ru", iconBase: "/prefix/p/", onProto: () => {} });
document.body.appendChild(g.el); g.set(R);
const imgs = g.el.all((n) => n.tagName === "IMG").map((n) => n.getAttribute("src"));
const vm = G.model(Object.assign({}, R, { install: { apps: [{ id: "x", name: "X", icon: "/p/apps/karing.png" },
  { id: "y", name: "Y", icon: "/x/p/apps/a.png" }, { id: "z", name: "Z", icon: "javascript:apps/a.png" }] } }), {});
console.log(JSON.stringify({ imgs, model: vm.apps.map((a) => a.icon) }));
""" % json.dumps(issuance("awg3", "ios"))
        if not HAS_NODE:
            self.skipTest("нет node")
        got = run_node(js)
        self.assertTrue(got["imgs"])
        self.assertTrue(all(re.match(r"^/prefix/p/apps/[a-z0-9-]+\.png$", s) for s in got["imgs"]), got["imgs"])
        self.assertEqual(got["model"], ["apps/karing.png", "", ""])   # one form; foreign paths are dropped
        self.assertEqual(get("/p/apps/karing.png")[0], 200)

    @unittest.skipUnless(HAS_NODE, "нет node")
    def test_broken_icon_becomes_the_letter(self):
        js = prelude() + r"""
const R = %s;
const g = G.mount({ viewer: "admin", lang: "ru", iconBase: "static/", onProto: () => {} });
document.body.appendChild(g.el); g.set(R);
const img = g.el.all((n) => n.tagName === "IMG")[0];
const src = img.getAttribute("src");
fire(img, "error");
const letters = g.el.all((n) => n.className && n.className.indexOf("gs-appic--none") >= 0).map((n) => n.textContent);
g.set(R, true);                                       // the next render does not ask the broken address again
const again = g.el.all((n) => n.tagName === "IMG").map((n) => n.getAttribute("src")).filter((s) => s === src).length;
console.log(JSON.stringify({ src, letters, again }));
""" % json.dumps(issuance("awg3", "ios"))
        got = run_node(js)
        self.assertTrue(got["src"].startswith("static/apps/"))
        self.assertEqual(len(got["letters"]), 1)
        self.assertRegex(got["letters"][0], r"^[A-Z]$")
        self.assertEqual(got["again"], 0)


@unittest.skipUnless(HAS_NODE, "нет node")
class WizardStateBTest(unittest.TestCase):
    """№2: in the wizard the owner is the administrator — actions, never «ask your administrator»."""

    def model(self, r, **o):
        js = prelude(o.get("lang", "ru")) + "console.log(JSON.stringify(G.model(%s, %s)));" % (json.dumps(r), json.dumps(o))
        return run_node(js)

    def test_wizard_gets_actions_and_no_admin_words(self):
        r = issuance("awg3", "huawei", server=("awg3", "l2tp"), device=("awg3",))
        vm = self.model(r, viewer="wizard", actions={"grant": True, "enable": True})
        self.assertEqual([(x["proto"], x["act"]) for x in vm["alts"]], [("l2tp", "grant"), ("pptp", "enable")])
        for lang in ("ru", "en"):                                     # no buttons — a neutral line, not «the admin»
            bare = self.model(r, viewer="wizard", lang=lang)
            asks = [x["ask"] for x in bare["alts"]]
            self.assertTrue(all(asks), asks)
            self.assertFalse([a for a in asks if re.search("администратор|admin", a, re.I)], asks)
        self.assertEqual([x["ask"] for x in self.model(r, viewer="wizard")["alts"]],
                         ["Добавьте L2TP/IPsec устройству в разделе «Пользователи и устройства»", "Включите PPTP в разделе «Протоколы»"])
        person = self.model(issuance("awg3", "huawei", server=("awg3", "l2tp"), device=("awg3",), where="page"), viewer="person")
        self.assertTrue(all(x["ask"].startswith("Попросите администратора") for x in person["alts"]))

    def test_wizard_mounts_with_both_actions(self):
        src = read(os.path.join("setup", "setup.js"))
        m = re.search(r'G\.mount\(\{ viewer: "wizard".*?iconBase', src, re.S)
        self.assertTrue(m)
        self.assertIn('onGrant: async (k) => { const r = await step("grant", { device: u.device, inbound: k })', m.group(0))
        self.assertIn('onEnable: () => open("protocols")', m.group(0))


class WizardGrantStepTest(RootCase):
    """№2: the step «grant» adds a protocol of the server to the wizard's device; one not on the server — «Протоколы»."""

    def setUp(self):
        RootCase.setUp(self)
        from kaskad import sysrun
        from kaskad.panel import auth, conf
        from kaskad.setup import link, model as smodel, steps
        from unittest import mock
        conf.save({"schema": 1, "public": {"on": True, "port": 41234, "bind": "203.0.113.5", "host": "203.0.113.5",
                                           "cert_host": "203.0.113.5"}})
        smodel.update(lambda d: link.issue(d))
        self.steps, self.mock = steps, mock
        self.runner = sysrun.FakeRunner()
        self.patches = [mock.patch.object(auth, "SCRYPT_N", 1 << 10), mock.patch.object(steps, "online", lambda force=False: True)]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        steps.run("password", {"password": "Correct-Horse-9"}, self.runner)

    def test_grant(self):
        calls = []
        grant = lambda runner, ref, kind, on=True: calls.append((ref, kind)) or {"device": {"id": ref, "access": ["awg3", kind]}}
        with self.mock.patch("kaskad.gw.model.enabled_kinds", lambda st: ["awg3", "l2tp"]), \
                self.mock.patch("kaskad.gw.ops.device_grant", grant), self.mock.patch("kaskad.state.load", lambda: {}):
            ok = self.steps.run("grant", {"device": "d-12345678", "inbound": "l2tp"}, self.runner)
            off = self.steps.run("grant", {"device": "d-12345678", "inbound": "pptp"}, self.runner)
        self.assertEqual(ok["state"], "ok", ok)
        self.assertEqual(ok["data"]["access"], ["awg3", "l2tp"])
        self.assertEqual(calls, [("d-12345678", "l2tp")])
        self.assertEqual((off["error"]["code"], off["error"]["action"]), ("no_inbound", {"stage": "protocols"}))


class WizardPollTest(unittest.TestCase):
    """№3, №17: the poll of the connection does not rebuild the page when nothing changed; focus inside the issue
    window comes back by data-gk; the window is destroyed when step 6 is gone; no English words — no English page."""

    def test_source(self):
        src = read(os.path.join("setup", "setup.js"))
        tick = src[src.index("function startConnPoll()"):src.index("async function issueDevice()")]
        self.assertIn("if (u.connKey === key) return;", tick)
        self.assertLess(tick.index("if (u.connKey === key) return;"), tick.index("render();"))
        rem = src[src.index("function remember()"):src.index("function draw()")]
        self.assertIn('a.closest(".qs-give") ? a.getAttribute("data-gk")', rem)
        self.assertIn("'.qs-give [data-gk=\"' + esc(f.gk) + '\"]'", rem)
        self.assertIn("CSS.escape", rem)
        self.assertIn("dropGive();", src[src.index("function draw()"):src.index("function connRows()")])
        self.assertIn("u.give.g.destroy(); } catch (e) { /* уже */ } u.give = null;", src)
        en = src[src.index("async function loadEn()"):src.index("async function usePageLang(")]
        self.assertIn('load("../give-i18n-en.js" + (v ? "?v=" + encodeURIComponent(v) : "")); } catch (e) { return false; }', en)


@unittest.skipUnless(HAS_NODE, "нет node")
class GiveBehaviourTest(unittest.TestCase):
    def model(self, r, **o):
        js = prelude(o.get("lang", "ru")) + "console.log(JSON.stringify(G.model(%s, %s)));" % (json.dumps(r), json.dumps(o))
        return run_node(js)

    def test_format_of_openvpn_and_openconnect(self):
        """№4: the app row tells the format the server gives: .ovpn, the fields — never «.conf»."""
        for p in ("ios", "linux", "android", "windows", "macos"):
            vm = self.model(issuance("openvpn", p, device=("openvpn",), server=("openvpn",)))
            fm = set((a["fmtWord"], a["ext"]) for a in vm.get("apps") or [])
            self.assertTrue(fm, p)
            self.assertEqual(fm, {("файл", ".ovpn")}, p)
        for p in ("windows", "macos", "linux", "android", "ios"):
            vm = self.model(issuance("ocserv", p, device=("ocserv",), server=("ocserv",)))
            fm = set((a["fmtWord"], a["ext"]) for a in vm.get("apps") or [])
            if fm:
                self.assertEqual(fm, {("данные для ввода", "")}, p)
        en = self.model(issuance("ocserv", "windows", lang="en", device=("ocserv",), server=("ocserv",)), lang="en")
        self.assertEqual(set((a["fmtWord"], a["ext"]) for a in en["apps"]), {("details to enter", "")})
        self.assertEqual(self.model(issuance("awg3", "ios"), app="amneziawg")["key"]["ext"], ".conf")   # unchanged

    def test_dom_aria_esc_escape(self):
        """№4 aria-label, №13 Esc on the document first closes the note, №14 no control inside a radio, the router
        is a button with a list, the note with links is a group, one live region, №19 a quote in an id."""
        r = issuance("openvpn", "ios", device=("openvpn", "awg3"), server=("openvpn", "awg3"))
        js = prelude() + r"""
const [R, RA, RK] = %s;
const g = G.mount({ viewer: "admin", lang: "ru", selectors: true, onPlatform: () => {}, onProto: () => {} });
document.body.appendChild(g.el); g.set(R);
const root = g.el, out = {};
const radios = root.all((n) => n.getAttribute && n.getAttribute("role") === "radio");
out.nested = radios.filter((n) => n.all((x) => x !== n && (x.tagName === "BUTTON" || x.tagName === "A" || (x.getAttribute && x.getAttribute("role") === "radio"))).length).length;
out.labels = root.all((n) => n.getAttribute && /^app:/.test(n.getAttribute("data-gk") || "")).map((n) => n.getAttribute("aria-label"));
const rt = gk(root, "router");
out.router = [rt.getAttribute("role"), rt.getAttribute("aria-haspopup"), rt.getAttribute("aria-checked")];
out.live = root.all((n) => n.getAttribute && n.getAttribute("aria-live")).length;
g.set(RA);                                                                // AmneziaWG on iPhone: notes grey under the apps, the pair note
out.nested2 = root.all((n) => n.getAttribute && n.getAttribute("role") === "radio").filter((n) => n.all((x) => x !== n && (x.tagName === "BUTTON" || x.tagName === "A")).length).length;
out.tips = root.all((n) => n.getAttribute && /^tip:/.test(n.getAttribute("data-gk") || "")).length;
let dialogEsc = 0;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") dialogEsc++; });   // the dialog's Esc (bubble)
g.set(RK);                                                               // the note of a step: «Подготовьте роутер»
const tipBtn = root.all((n) => n.getAttribute && /^tip:/.test(n.getAttribute("data-gk") || ""))[0];
fire(tipBtn.parentNode, "mouseenter");                                  // the note opened by the mouse
out.tipRole = root.querySelector(".gs-tip") && root.querySelector(".gs-tip").getAttribute("role");
document.activeElement = document.body;                                   // the focus is outside the window
fire(document.body, "keydown", { key: "Escape" });
out.afterEsc1 = [has(root, "gs-tip"), dialogEsc];
fire(document.body, "keydown", { key: "Escape" });
out.afterEsc2 = dialogEsc;
// №19: an id with a quote does not throw (focusKey through CSS.escape / its fallback)
const R2 = JSON.parse(JSON.stringify(RA)); R2.install.apps[0].id = 'a"b'; 
let threw = false; try { g.set(R2); fire(root.all((n) => n.getAttribute && n.getAttribute("role") === "radio")[1], "click"); } catch (e) { threw = String(e); }
out.threw = threw;
console.log(JSON.stringify(out));
""" % json.dumps([r, issuance("awg3", "ios"), issuance("awg3", "keenetic")])
        got = run_node(js)
        self.assertEqual((got["nested"], got["nested2"]), (0, 0))
        self.assertEqual(got["tips"], 0)                                   # круг 1 (R-it1-tri): у строк приложений треугольников нет
        self.assertTrue(got["labels"])
        self.assertTrue(all(l.endswith("файл .ovpn") for l in got["labels"]), got["labels"])
        self.assertEqual(got["router"], [None, "listbox", None])
        self.assertEqual(got["live"], 1)
        self.assertEqual(got["tipRole"], "group")
        self.assertEqual(got["afterEsc1"], [False, 0])                     # the first Esc — only the note
        self.assertEqual(got["afterEsc2"], 1)                              # the next one — the dialog
        self.assertFalse(got["threw"])

    def test_guide_label_and_file_url(self):
        """№18: «наша инструкция» only for anten-ka.com itself; file.url only of /p/…/file/…"""
        js = prelude() + r"""
const base = { kind: "awg3", os: "keenetic", items: [{ format: "conf", filename: "a.conf", body: "", file: "", notes: [] }], support: { level: "native" } };
const f = (file) => { const r = JSON.parse(JSON.stringify(base)); r.items[0].file = file; return G.model(r, {}).rt.file; };
const tok = "A".repeat(43), gr = "B".repeat(32);
const out = { ok: f("/p/" + tok + "/file/" + gr + "/a.conf"), pref: f("/x/p/" + tok + "/file/" + gr + "/a.conf"),
  js: f("javascript:alert(1)"), ext: f("https://evil.example/a.conf"), rel: f("//evil/p/" + tok + "/file/" + gr + "/a.conf") };
const g = G.mount({ viewer: "admin", lang: "ru", onProto: () => {} });
document.body.appendChild(g.el);
const lab = (url) => { g.set({ kind: "vless", os: "mikrotik", items: [], support: { level: "workaround", guide: url } }); 
  const a = g.el.all((n) => n.tagName === "A" && n.getAttribute("href") === url)[0]; return a ? a.textContent : null; };
out.ours = lab("https://anten-ka.com/guides/no30/"); out.www = lab("https://www.anten-ka.com/x");
out.fake = lab("https://x.example/?anten-ka.com"); out.fake2 = lab("https://anten-ka.com.evil.example/");
console.log(JSON.stringify(out));
"""
        got = run_node(js)
        self.assertEqual(got["ok"]["url"], "/p/" + "A" * 43 + "/file/" + "B" * 32 + "/a.conf")
        self.assertTrue(got["pref"]["url"].startswith("/x/p/"))
        self.assertIsNone(got["js"])                                       # no url, no body — no file at all
        self.assertIsNone(got["ext"])
        self.assertIsNone(got["rel"])
        self.assertTrue(got["ours"].startswith("наша инструкция"))
        self.assertTrue(got["www"].startswith("наша инструкция"))
        self.assertFalse(got["fake"].startswith("наша"))
        self.assertFalse(got["fake2"].startswith("наша"))


class PhoneCssTest(unittest.TestCase):
    """№11 a long app name wraps, №12 the touch targets on the phone are 44×44."""

    def test_css(self):
        from tests.test_setup_layout_332 import find
        css = read("give.css")
        n, b = find(css, ".gs-app__n", ""), find(css, ".gs-app__n b", "")
        self.assertEqual(n.get("flex-wrap"), "wrap")
        self.assertEqual(b.get("white-space"), "normal")
        self.assertNotIn("text-overflow", b)
        self.assertEqual(find(css, ".gs-btn--icon", "max-width: 767px").get("width"), "44px")
        self.assertEqual(find(css, ".gs-textbtn", "max-width: 767px").get("min-height"), "44px")


class PersonPageLevelsTest(unittest.TestCase):
    """№5: /p/ gets the levels of the platform and draws the chips as the panel: dimmed «none», a triangle by
    a workaround."""

    def test_levels_reach_the_page(self):
        from kaskad.i18n import use_lang
        from kaskad.panel import userpage
        with use_lang("ru"):
            hu = install_apps.support("awg2", "huawei", [], where="page")
            mt = install_apps.support("vless", "mikrotik", [], where="page")
        out = userpage._support_out(hu, [])
        self.assertEqual(out["levels"].get("awg2"), "none")
        self.assertEqual(userpage._support_out(mt, [])["levels"].get("vless"), "workaround")
        bad = userpage._support_out({"level": "native", "levels": {"awg3": "<b>", "wg": "native", "vless": "app"}}, [])
        self.assertEqual(bad["levels"], {"wg": "native", "vless": "app"})  # only the words of the catalog
        self.assertEqual(userpage._support_out({"levels": ["x"]}, [])["levels"], {})

    def test_user_js_draws_them(self):
        src = read("user.js")
        row = src[src.index("function deviceRow("):]
        self.assertIn("sup.levels[inb.kind]", row)
        self.assertIn("'gs-chip' + (lv === 'none' ? ' is-dim' : '')", row)
        self.assertIn("G0.t(LANG, 'chipNone', {os: plat, proto: name})", row)
        self.assertIn("if (lv === 'workaround' && G0 && G0.triangle) { b.appendChild(G0.triangle(15)); }", row)
        self.assertIn("triangle: triangle", read("give.js"))


class ConfNamesTest(unittest.TestCase):
    """№7: «+» and «=» never reach a .conf name (the page /p/ would hide «Скачать»); №16: two devices with one start of
    the name stay two names on Android/Linux, the platform's word is not spent of 15."""

    def test_alphabet_agrees_with_the_page(self):
        from kaskad.gw import issue_os
        from kaskad.panel import userpage
        f = issue_os.conf_filename
        for name in ("Pixel 6+", "a=b", "C++ ноут", "x+=y", "Телефон мамы", "😀 ✈"):
            for p in ("android", "linux", "ios", "windows", "keenetic", "other"):
                for acc in (["awg3"], ["awg3", "wg"]):
                    fn = f("awg3", device={"id": "d-0a1b2c3d", "name": name, "access": acc}, platform_name=p)
                    self.assertTrue(userpage.FILE_NAME_RE.match(fn), fn)
                    self.assertNotRegex(fn, r"[+=]")
        self.assertEqual(issue_os.translit("Pixel 6+"), "Pixel-6")

    def test_short_names_differ(self):
        from kaskad.gw import issue_os
        f = issue_os.conf_filename
        mama = {"id": "d-1234563f", "name": "Телефон мамы", "access": ["awg3"]}
        papa = {"id": "d-123456a1", "name": "Телефон папы", "access": ["awg3"]}
        for p in ("android", "androidtv", "linux"):
            for acc in (["awg3"], ["awg3", "wg"], ["awg3", "awg2", "wg"]):
                a = f("awg3", device=dict(mama, access=acc), platform_name=p)
                b = f("awg3", device=dict(papa, access=acc), platform_name=p)
                self.assertNotEqual(a, b, (p, acc))
                self.assertLessEqual(len(a) - len(".conf"), 15)
                self.assertLessEqual(len(b) - len(".conf"), 15)
        self.assertEqual(f("awg3", device=mama, platform_name="android"), "gKP-telefon-3f.conf")
        # the wizard's «Мама · Android»: the platform's word goes, the name fits whole
        wiz = {"id": "d-1", "name": "Мама · Android", "access": ["awg3"]}
        self.assertEqual(f("awg3", device=wiz, platform_name="android"), "gKP-mama.conf")
        self.assertEqual(f("awg3", device=dict(wiz, name="Мама · Android TV"), platform_name="androidtv"), "gKP-mama.conf")
        self.assertEqual(f("awg3", device=dict(wiz, name="Android"), platform_name="android"), "gKP-Android.conf")
        # iPhone, Windows, routers — 32, as before
        self.assertEqual(f("awg3", device=mama, platform_name="ios"), "goKaskadPro-telefon-mamy.conf")


class WordsTest(unittest.TestCase):
    """№8 «Issue settings» by the glossary everywhere; №9 the typo «on on»."""

    def test_issue_settings(self):
        en = read("i18n-en.js") + read("give-i18n-en.js")
        self.assertNotIn("Share settings", en)
        self.assertIn('"pp.k1.giveOf": "Issue settings — {name}"', read("i18n-en.js"))   # Т3: заголовок окна выдачи
        self.assertIn('"pp.issue": "Issue settings"', read("i18n-en.js"))
        self.assertIn("title: 'Issue settings'", read("give-i18n-en.js"))

    def test_on_on(self):
        with open(os.path.join(ROOT, "lib", "kaskad", "i18n", "catalog", "en", "gw_issue.json"), encoding="utf-8") as f:
            cat = json.load(f)
        self.assertEqual(cat["gw_issue.support.alt_device"], "{proto} — enabled on the server: add it to the device")
        self.assertNotIn("on on ", read("dev-mock-install.js"))


@unittest.skipUnless(HAS_NODE, "нет node")
class StateAAlsoTest(unittest.TestCase):
    """№10 (SPEC п. 2): state А shows a short line of the other protocols; a ready one switches to it."""

    def test_also_line(self):
        r = issuance("vless", "mikrotik")
        js = prelude() + r"""
const R = %s, calls = [];
const g = G.mount({ viewer: "admin", lang: "ru", onProto: (k) => calls.push(k) });
document.body.appendChild(g.el); g.set(R);
const p = g.el.querySelector(".gs-also");
const btns = g.el.all((n) => n.getAttribute && /^also:/.test(n.getAttribute("data-gk") || ""));
if (btns[0]) fire(btns[0], "click");
console.log(JSON.stringify({ text: p ? p.textContent : null, btns: btns.map((b) => b.getAttribute("data-gk")), calls }));
""" % json.dumps(r)
        got = run_node(js)
        self.assertTrue(got["text"].startswith("Другие протоколы для этой системы: "), got["text"])
        ready = [a["proto"] for a in r["alternatives"] if a["state"] == "ready"]
        self.assertEqual(got["btns"], ["also:" + k for k in ready])
        self.assertEqual(got["calls"], ready[:1])
        shown = [a for a in r["alternatives"] if a["state"] == "ready"] + [a for a in r["alternatives"] if a["state"] != "ready"]
        for a in shown[:4]:
            self.assertIn(a["title"], got["text"])
        self.assertNotIn(shown[4]["title"], got["text"])                    # short: four at most


class BranchNameTest(unittest.TestCase):
    """№15: one check of the update branch for the environment and for self_update."""

    def test_branch_ok(self):
        import subprocess
        with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as f:
            src = f.read()
        fn = re.search(r"^k3_branch_ok\(\) \{\n.*?^\}\n", src, re.M | re.S).group(0)
        su = src[src.index("self_update() {"):]
        self.assertIn('if ! k3_branch_ok "$branch"; then', su[:600])
        good = ["main", "test", "k3", "k3-332", "release/3.3.2", "v2.13", "a_b"]
        bad = ["-x", "/etc/passwd", ".", "a//b", "k3/", "x.lock", "a b", "a;b", "$(id)", "a..b", "", "é", "a\nb",
               "x.", "a/.b", "a/-b", "..", "a/b.lock"]
        script = fn + 'for b in "$@"; do if k3_branch_ok "$b"; then echo 1; else echo 0; fi; done\n'
        r = subprocess.run(["/bin/bash", "-c", script, "x"] + good + bad, stdout=subprocess.PIPE,
                           env=dict(i18n.strict_env(), LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8", PATH="/usr/bin:/bin"),
                           timeout=30)   # П3-б: the strict mode of tr() goes on (REV-ns-p3a З-1)
        got = r.stdout.decode().split("\n")[:len(good) + len(bad)]
        self.assertEqual(dict(zip(good + bad, got)), dict([(g, "1") for g in good] + [(b_, "0") for b_ in bad]))


if __name__ == "__main__":
    unittest.main()

"""Kaskad 3.3.2, executor A — the wizard «Быстрый старт» and the QR codes (k3/handoff/OWNER-FEEDBACK-3.3.md):

    №1   the wizard opens dark, light — by the switch; the choice in the key of «Вид» shared with the panel
         (kaskad-appearance-v1), the theme of the OS is not taken by default;
    №3   stage 2: while the plan loads — the same title, the same grid with as many cards: the list takes their place;
         the gutter of the scroll bar is always there (a page that grew does not narrow and reflow the grid);
    №4   L2TP/IPsec (fixed ports only) says its ports as the others do;
    №5   «Почта для Let's Encrypt не задана» — with a field for the mail right in the note (step «site-email»);
    №7   stage 6: the row of inputs keeps its place while loading, the button stays after the issue;
    №8, №10  one component for every QR (qr-slides.js): the app and the format under the code, the parts of a key one
         after another in one frame, by themselves every 2 seconds (no pause, no buttons — owner, 3.3.2); the same rule of
         «for which app» in JS and in Python (kaskad.qr.qr_for) for the person's page;
    №12, №14  stage 6 has «Подключение» and «Сводка» only, never wide;
    №13  «Сводка»: the name of the person is not repeated in the name of the device.
Node parts are skipped without node."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kaskad import qr as qrmod, sysrun
from kaskad.panel import auth, userpage
from kaskad.setup import jobs, steps
from kaskad.site import ops as sops
from kaskad.vendor import awgkeys
from tests import jsparts
from tests.helpers import RootCase
from tests.setup.test_setup_core import new_link, open_panel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")


def read(rel):
    with open(os.path.join(STATIC, rel), encoding="utf-8") as f:
        return f.read()


def node(src):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.unlink(path)
    if r.returncode:
        raise AssertionError(r.stderr.decode("utf-8", "replace")[-3000:])
    return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])


def block(js, start):
    """``start … { … }`` of the source (an assignment like ``VIEW.done = () => {``)."""
    i = js.index(start)
    return js[i:jsparts.close_of(js, js.index("{", i)) + 1] + ";"


HAS_NODE = bool(shutil.which("node"))


# ── №1 the theme of the wizard ──────────────────────────────────────────────

THEME_JS = r"""
function run(stored, osLight, set) {
  const store = {};
  if (stored !== undefined) store["kaskad-appearance-v1"] = stored;
  const attrs = {}, applied = [];
  const sandbox = {
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } },
    matchMedia: (q) => ({ matches: /light/.test(q) ? osLight : !osLight }),
    document: { documentElement: { setAttribute: (k, v) => { attrs[k] = v; } } },
  };
  sandbox.window = sandbox;
  sandbox.KaskadLook = { apply: (root, accent, th) => applied.push([accent, th]) };
  new Function("window", "localStorage", "matchMedia", "document", SRC)(sandbox, sandbox.localStorage, sandbox.matchMedia, sandbox.document);
  const first = { theme: attrs["data-theme"], accent: applied[0] && applied[0][0], api: sandbox.KaskadSetupLook.theme() };
  if (set) sandbox.KaskadSetupLook.set(set);
  let kept = null;
  try { kept = JSON.parse(store["kaskad-appearance-v1"]); } catch (e) { kept = store["kaskad-appearance-v1"] || null; }
  return { first, stored: kept === undefined ? null : kept,
           after: sandbox.KaskadSetupLook.theme() };
}
console.log(JSON.stringify({
  fresh: run(undefined, true),
  freshDark: run(undefined, false),
  light: run(JSON.stringify({ v: 3, theme: "light", accent: "coral", density: "min" }), false),
  dark: run(JSON.stringify({ v: 3, theme: "dark" }), true),
  system: run(JSON.stringify({ v: 3, theme: "system" }), true),
  oldSystem: run(JSON.stringify({ v: 2, theme: "system" }), true),
  noTheme: run(JSON.stringify({ v: 3, accent: "mint" }), true),
  broken: run("{not json", true),
  toLight: run(JSON.stringify({ theme: "dark", accent: "ga", radius: 4 }), true, "light"),
  newKey: run(undefined, true, "light"),
  brokenSet: run("{not json", true, "dark"),
  bad: run(undefined, true, "purple"),
}));
"""


@unittest.skipUnless(HAS_NODE, "нет node")
class ThemeTest(unittest.TestCase):
    def test_dark_by_default_light_by_the_switch_one_key_with_the_panel(self):
        got = node("const SRC = %s;\n%s" % (json.dumps(read("setup/look.js")), THEME_JS))
        # no choice yet — dark, even when the OS is light (the owner: not from prefers-color-scheme)
        self.assertEqual(got["fresh"]["first"], {"theme": "dark", "accent": "#45D0C0", "api": "dark"})
        self.assertEqual(got["freshDark"]["first"]["theme"], "dark")
        self.assertIsNone(got["fresh"]["stored"])                       # opening the wizard writes nothing
        self.assertEqual(got["light"]["first"], {"theme": "light", "accent": "oklch(.53 .17 288)", "api": "light"})
        self.assertEqual(got["dark"]["first"]["theme"], "dark")
        self.assertEqual(got["system"]["first"]["theme"], "light")      # «как в системе» chosen in the panel — followed
        self.assertEqual(got["oldSystem"]["first"]["theme"], "dark")    # the default of the old versions (v < 3)
        self.assertEqual(got["noTheme"]["first"]["theme"], "dark")
        self.assertEqual(got["broken"]["first"]["theme"], "dark")
        # the switch changes only the theme of the panel's «Вид», in its format (v: 3, the panel's migrateLook done)
        self.assertEqual(got["toLight"]["stored"], {"v": 3, "theme": "light", "accent": "auto", "radius": 4})
        self.assertEqual(got["toLight"]["after"], "light")
        self.assertEqual(got["newKey"]["stored"], {"v": 3, "theme": "light"})
        self.assertIsNone(got["bad"]["stored"])
        self.assertEqual(got["broken"]["stored"], "{not json")          # read only
        self.assertEqual(got["brokenSet"]["stored"], {"v": 3, "theme": "dark"})

    def test_the_key_is_the_panels(self):
        self.assertIn('const LOOK_KEY = "kaskad-appearance-v1";', read("app.js"))
        self.assertIn('var KEY = "kaskad-appearance-v1";', read("setup/look.js"))

    def test_before_the_first_paint_and_no_os_theme_in_the_page(self):
        html = read("setup/index.html")
        boot, look = html.index('src="../boot.js'), html.index('<script src="look.js?v=__KASKAD_V__"></script>')
        self.assertLess(boot, look)
        self.assertLess(look, html.index("<link rel=\"stylesheet\""))  # synchronous, in <head>, before the styles
        js = read("setup/setup.js")
        apply = jsparts.function(js, "applyLook")
        self.assertNotIn("prefers-color-scheme", apply)
        self.assertIn("savedTheme()", apply)
        self.assertIn("window.KaskadSetupLook.set(th)", jsparts.function(js, "setTheme"))
        self.assertIn("themeBtn(false)", js)                             # the rail
        self.assertIn("themeBtn(true)", js)                              # the bar of the phone
        i18n = read("setup/setup-i18n-ru.js")
        for k in ("look.toLight", "look.toDark"):
            self.assertIn('"%s":' % k, i18n)


# ── №8, №10 the QR component ────────────────────────────────────────────────

DOM_JS = r"""
class El {
  constructor(tag) { this.tagName = tag; this.children = []; this.parent = null; this.attrs = {}; this.hidden = false;
    this.className = ""; this._text = ""; this.on = {}; this.title = ""; this.type = ""; }
  appendChild(c) { if (c.parent) c.parent.removeChild(c); c.parent = this; this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); c.parent = null; return c; }
  get firstChild() { return this.children[0] || null; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(ev, fn) { (this.on[ev] = this.on[ev] || []).push(fn); }
  fire(ev, e) { (this.on[ev] || []).forEach((fn) => fn(e || {})); }
  contains(x) { while (x) { if (x === this) return true; x = x.parent; } return false; }
  get isConnected() { let x = this; while (x.parent) x = x.parent; return x === BODY; }
  find(cls) { if ((" " + this.className + " ").indexOf(" " + cls + " ") >= 0) return this; for (const c of this.children) { const f = c.find(cls); if (f) return f; } return null; }
  all(cls) { const out = []; (function w(n) { if ((" " + n.className + " ").indexOf(" " + cls + " ") >= 0) out.push(n); n.children.forEach(w); })(this); return out; }
}
const BODY = new El("body");
const TIMERS = [];
global.document = { createElement: (t) => new El(t), hidden: false };
global.setInterval = (fn, ms) => { TIMERS.push({ fn, ms, on: true }); return TIMERS.length; };
global.clearInterval = (id) => { if (TIMERS[id - 1]) TIMERS[id - 1].on = false; };
let REDUCED = false;
global.window = global;
global.matchMedia = (q) => ({ matches: /reduce/.test(q) ? REDUCED : false });
const tick = () => TIMERS.filter((x) => x.on).forEach((x) => x.fn());
"""

SLIDES_JS = r"""
const K = window.KaskadQRSlides;
const texts = { part: "Часть {i} из {n}" };
const shown = (s) => s.el.find("kq__frame").children.map((p, i) => (p.hidden ? null : i)).filter((x) => x !== null);
const seen = [];
const s = K.make({ count: 2, label: "DefaultVPN и AmneziaVPN · vpn://", texts, draw: (i) => { const e = document.createElement("i"); e.textContent = "part" + i; return e; }, onShow: (i) => seen.push(i) });
const before = { frames: s.el.find("kq__frame").children.length, shown: shown(s), of: s.el.find("kq__of").textContent, timers: TIMERS.length, ms: TIMERS[0].ms };
tick();                                                        // not on the page yet — the timer waits
const notMounted = shown(s);
BODY.appendChild(s.el);
tick();
const auto1 = { shown: shown(s), of: s.el.find("kq__of").textContent, frames: s.el.find("kq__frame").children.length };
tick();
const auto2 = shown(s);
s.el.fire("pointerenter", { pointerType: "mouse" });            // the mouse over the code — no pause (the camera of a phone)
tick();
const hover = shown(s);
s.el.fire("focusin", {});
s.el.find("kq__frame").fire("click");                          // a click on the code — no pause either
tick();
const clicked = shown(s);
const buttons = !!(s.el.find("kq__btn") || s.el.find("kq__nav"));
BODY.removeChild(s.el);                                        // gone from the page — the timer stops itself
tick();
const stopped = TIMERS[0].on;
REDUCED = true;                                                // the change is functional: with reduced motion too
const r = K.make({ count: 3, label: "x", texts, draw: () => document.createElement("i") });
BODY.appendChild(r.el);
TIMERS[1].fn(); TIMERS[1].fn();
const reduced = { timers: TIMERS.length, shown: shown(r) };
REDUCED = false;
const one = K.make({ count: 1, label: "AmneziaWG · файл .conf", texts, draw: () => document.createElement("i") });
const single = { of: !!one.el.find("kq__of"), label: one.el.find("kq__app").textContent, timers: TIMERS.length };
const kinds = {};
Object.keys(SAMPLES).forEach((k) => { if (typeof SAMPLES[k] === "string") kinds[k] = K.kind(SAMPLES[k]); });
console.log(JSON.stringify({ before, notMounted, auto1, auto2, hover, clicked, buttons, stopped, reduced,
  single, seen, label: s.el.find("kq__app").textContent, part: K.part(SAMPLES.part2), kinds }));
"""


def samples():
    # an AmneziaVPN key too long for one code (3.1): its parts as vendor/awgkeys cuts them; the bytes are noise
    raw = bytes((i * 7919 + 13) % 251 for i in range(2000))
    vpn = "vpn://" + awgkeys.b64url_encode(raw)
    chunks = awgkeys.qr_chunks(vpn)
    return {"vpn": vpn, "part1": chunks[0], "part2": chunks[-1], "n": len(chunks),
            "vless": "vless://uuid@203.0.113.10:443?security=reality#Мама",
            "awgconf": "[Interface]\nPrivateKey = k\nAddress = 10.66.0.2/32\nJc = 4\nJmin = 40\nH1 = 1\n",
            "wgconf": "[Interface]\nPrivateKey = k\nAddress = 10.66.0.2/32\n\n[Peer]\nPublicKey = p\n",
            "url": "https://203.0.113.10:41234/p/" + "A" * 43 + "/sub", "text": "просто текст",
            "b64": "QUJDREVGR0hJSktMTU5PUA"}


class QrKindPythonTest(unittest.TestCase):
    def test_the_rule(self):
        s = samples()
        self.assertGreater(s["n"], 1)                                   # 3.1 in parts, as the owner saw it
        want = {"vpn": "vpn", "part1": "vpnpart", "part2": "vpnpart", "vless": "vless", "awgconf": "awgconf",
                "wgconf": "wgconf", "url": "url", "text": "text", "b64": "text"}
        self.assertEqual(dict((k, qrmod.qr_for(s[k])) for k in want), want)
        self.assertEqual(qrmod.qr_part(s["part2"]), {"n": s["n"], "i": s["n"] - 1})
        self.assertIsNone(qrmod.qr_part("vpn://AAAA"))


@unittest.skipUnless(HAS_NODE, "нет node")
class QrSlidesTest(unittest.TestCase):
    def test_one_frame_parts_change_by_themselves_every_2_seconds(self):
        """Owner, 3.3.2: the parts change by themselves every 2 seconds, round and round, with no action of a person —
        no pause under the mouse or on a click (the camera of a phone would stay on one part), no buttons, the same with
        prefers-reduced-motion; only «Часть 1 из 2» under the frame."""
        s = samples()
        got = node(DOM_JS + read("qr-slides.js") + "\nconst SAMPLES = %s;\n" % json.dumps(s) + SLIDES_JS)
        self.assertEqual(got["before"], {"frames": 1, "shown": [0], "of": "Часть 1 из 2", "timers": 1, "ms": 2000})
        self.assertEqual(got["notMounted"], [0])
        self.assertEqual(got["auto1"], {"shown": [1], "of": "Часть 2 из 2", "frames": 2})   # one frame, one code shown
        self.assertEqual(got["auto2"], [0])                                                # and round again
        self.assertEqual(got["hover"], [1])
        self.assertEqual(got["clicked"], [0])
        self.assertFalse(got["buttons"])
        self.assertFalse(got["stopped"])
        self.assertEqual(got["reduced"], {"timers": 2, "shown": [2]})
        self.assertEqual(got["single"], {"of": False, "label": "AmneziaWG · файл .conf", "timers": 2})
        self.assertEqual(got["label"], "DefaultVPN и AmneziaVPN · vpn://")
        self.assertEqual(got["seen"][:3], [0, 1, 0])
        self.assertEqual(got["part"], {"n": s["n"], "i": s["n"] - 1})
        # the same rule as the server's (the person's page gets qr_for from it)
        self.assertEqual(got["kinds"], dict((k, qrmod.qr_for(v)) for k, v in s.items() if isinstance(v, str)))

    def test_every_page_shows_its_qr_through_it(self):
        # 3.3.2, the new window: the panel, /p/ and the wizard draw the issuance with give.js, its key QR — qr-slides.js;
        # the panel's page and subscription dialogs keep qrCard
        setup, app, user, give = read("setup/setup.js"), read("app.js"), read("user.js"), read("give.js")
        self.assertIn("k.make(", jsparts.function(give, "keyQr"))
        # «Режим стрима»: qrCard рисует через qrCardDraw (в режиме — только после «Показать QR»)
        self.assertIn('loadScript("qr-slides.js")', jsparts.function(app, "qrCardDraw"))
        self.assertIn("K.make(", jsparts.function(app, "qrCardDraw"))
        self.assertIn("return qrCardDraw(payloads, title);", jsparts.function(app, "qrCard"))
        self.assertIn("G.mount(", jsparts.function(setup, "issueCard"))
        self.assertIn("window.KaskadGive.mount(", jsparts.function(user, "showInbound"))
        self.assertIn('loadScript("qr-slides.js")', jsparts.function(app, "giveLoad"))
        # one pace for every page (owner, 3.3.2: every 2 s) — no page sets its own
        for body in (jsparts.function(give, "keyQr"), jsparts.function(app, "qrCard")):
            self.assertNotIn("interval", body)
        self.assertIn("var INTERVAL = 2000;", read("qr-slides.js"))
        self.assertIn('src="../qr-slides.js?v=__KASKAD_V__"', read("setup/index.html"))
        self.assertIn('src="{{BASE}}/p/qr-slides.js?v={{V_QRS}}"', read("user.html"))
        # the labels: the app and the format, in every dictionary of a page that shows a QR
        for k in ("forVpn", "forAwg", "forWg", "forVless", "forUrl"):          # the panel's page and subscription QR
            self.assertIn('"qr.%s":' % k, read("i18n-ru.js"))
        self.assertIn("  part: 'Часть {i} из {n}',", read("give-i18n-ru.js"))     # the window (panel, /p/, wizard)
        self.assertIn('"issue.qrOf": "Часть {i} из {n}"', read("i18n-ru.js"))


APPS_JS = r"""
const K = window.KaskadQRSlides;
const S_ = SAMPLES;
const ios = [{ format: "vpn", body: S_.vpn }, { format: "qr", body: [S_.part1, S_.part2] }, { format: "qr", body: [S_.awgconf] },
  { format: "conf", body: S_.awgconf, filename: "kaskad-awg3.conf" }];
const page = [{ format: "vpn", body: S_.vpn }, { format: "qr", qr: [{}, {}], qr_for: "vpnpart" }, { format: "qr", qr: [{}], qr_for: "awgconf" },
  { format: "conf", body: S_.awgconf }];                                    // the person's page: codes as paths + qr_for
const wg = [{ format: "qr", body: [S_.wgconf] }, { format: "conf", body: S_.wgconf }];
const vless = [{ format: "qr", body: [S_.vless] }, { format: "link", body: S_.vless }];
const l2tp = [{ format: "fields", body: [["Сервер", "x"]] }, { format: "ps1", body: "Add-VpnConnection" }];
const shape = (g) => ({ apps: g.groups.map((x) => x.app + ":" + x.extra.map((e) => e.format).join(",")), rest: g.rest.map((e) => e.format) });
const built = [];
const sw = K.switcher({ groups: K.group(ios).groups, label: (g) => "L-" + g.app, build: (g, i) => { built.push(i); const e = document.createElement("div"); e.textContent = g.app; return e; } });
BODY.appendChild(sw.el);
const seg = sw.el.find("kq-apps__seg"), bodies = () => sw.el.find("kq-apps__body").children;
const before = { btns: seg.children.map((b) => b.textContent + "=" + b.getAttribute("aria-pressed")), shown: bodies().filter((b) => !b.hidden).map((b) => b.textContent), built: built.slice() };
seg.children[1].fire("click");
const after = { btns: seg.children.map((b) => b.getAttribute("aria-pressed")), shown: bodies().filter((b) => !b.hidden).map((b) => b.textContent), n: bodies().length };
const one = K.switcher({ groups: K.group(wg).groups, build: () => document.createElement("i") });
const hs = K.switcher({ groups: K.group(ios).groups, hint: (g) => "H-" + K.hintId(g.app, "ios"), build: () => document.createElement("i") });
const hint = [hs.el.find("kq-apps__hint").textContent];
hs.el.find("kq-apps__seg").children[1].fire("click");
hint.push(hs.el.find("kq-apps__hint").textContent);
const ids = [K.hintId("amnezia", "ios"), K.hintId("amnezia", "android"), K.hintId("amnezia", "windows"), K.hintId("awg", "ios"), K.hintId("wg", "ios"), K.hintId("vless", "ios"), K.hintId("qr0", "ios")];
console.log(JSON.stringify({ ios: shape(K.group(ios)), page: shape(K.group(page)), wg: shape(K.group(wg)), vless: shape(K.group(vless)),
  l2tp: shape(K.group(l2tp)), before, after, oneSeg: !!one.el.find("kq-apps__seg"), hint, ids }));
"""


@unittest.skipUnless(HAS_NODE, "нет node")
class OneFrameTest(unittest.TestCase):
    """One QR frame per issuance (the coordinator of 3.3.2: «QR рядом не показывать»): the app — segments above it."""

    def test_group_and_switch(self):
        got = node(DOM_JS + read("qr-slides.js") + "\nconst SAMPLES = %s;\n" % json.dumps(samples()) + APPS_JS)
        self.assertEqual(got["ios"], {"apps": ["amnezia:vpn", "awg:conf"], "rest": []})
        self.assertEqual(got["page"], got["ios"])
        self.assertEqual(got["wg"], {"apps": ["wg:conf"], "rest": []})
        self.assertEqual(got["vless"], {"apps": ["vless:link"], "rest": []})
        self.assertEqual(got["l2tp"], {"apps": [], "rest": ["fields", "ps1"]})
        # the first app (recommended for the platform: the first code of the issuance) — shown, the other not built yet
        self.assertEqual(got["before"], {"btns": ["L-amnezia=true", "L-awg=false"], "shown": ["amnezia"], "built": [0]})
        self.assertEqual(got["after"], {"btns": ["false", "true"], "shown": ["awg"], "n": 2})
        self.assertFalse(got["oneSeg"])                                     # one app — no segments
        self.assertEqual(got["hint"], ["H-amnezia_ios", "H-awg"])            # the hint follows the switch
        self.assertEqual(got["ids"], ["amnezia_ios", "amnezia_android", "amnezia", "awg", "wg", "vless", ""])

    def test_every_page_uses_one_frame(self):
        # 3.3.2, design k3/design/issue-qr: ONE QR on the screen — the key's; the app picked in step 1 sets which code
        # (vpn:// parts for DefaultVPN/AmneziaVPN, the .conf for AmneziaWG…); the store's QR only in its card, the key's
        # QR is blurred and covered meanwhile; one window component for the panel, /p/ and the wizard
        setup, app, user, give = read("setup/setup.js"), read("app.js"), read("user.js"), read("give.js")
        block = jsparts.function(give, "keyBlock")
        self.assertEqual(block.count("keyQr(vm, covered)"), 1)
        self.assertIn("covered = !!st.card && !phone", block)
        self.assertIn("var bun = b.by[want0] || b.by[b.order[0]] || null;", give)
        # Т3: ключ устройства — вкладка «Статический ключ» окна «Выдать настройки» (issueView); openIssue ведёт туда же
        self.assertIn("G.mount(", jsparts.function(app, "issueView"))
        self.assertIn("return openGive(", jsparts.function(app, "openIssue"))
        self.assertIn("G.mount(", jsparts.function(setup, "issueCard"))
        self.assertIn("window.KaskadGive.mount(", jsparts.function(user, "showInbound"))
        for name in ("app.js", "user.js", "setup/setup.js"):
            self.assertNotIn("K.switcher(", read(name), name)
        # «Включите VPN» — the path in the chosen app (give-i18n path.*), else the server's step
        self.assertIn("'+ › Вставить из буфера › Продолжить'", read("give-i18n-ru.js"))
        self.assertIn("'+ › Paste from clipboard › Continue'", read("give-i18n-en.js"))


class UserPageQrForTest(unittest.TestCase):
    def test_item_says_for_which_app(self):
        s = samples()
        with mock.patch.object(userpage, "qr_matrix", lambda runner, t: {"d": "M0 0h1v1h-1z", "n": 21}):
            parts = userpage._item_out(None, "t" * 43, "g" * 32, {"format": "qr", "title": "AmneziaWG 3.1 — QR",
                                                                  "body": [s["part1"], s["part2"]]}, True)
            conf = userpage._item_out(None, "t" * 43, "g" * 32, {"format": "qr", "title": "x", "body": [s["awgconf"]]},
                                      True)
        self.assertEqual((parts["qr_for"], len(parts["qr"])), ("vpnpart", 2))
        self.assertEqual(conf["qr_for"], "awgconf")
        self.assertIn("qr-slides.js", userpage.ASSETS)
        self.assertIn("V_QRS", userpage.render_shell("Мама", "n", "ios", False).decode("utf-8") + "V_QRS")

    def test_the_shell_points_at_the_component(self):
        text = userpage.render_shell("Мама", "n", "ios", False).decode("utf-8")
        self.assertRegex(text, r'/p/qr-slides\.js\?v=[0-9a-f]{10}"')


# ── stage 2 and 6: nothing moves under the pointer (№3, №7), stage 6 (№12, №13, №14) ───────────────────────────────

STUBS = r"""
const RU = %s;
const t = (k, v) => { let s = RU[k] || k; Object.keys(v || {}).forEach((x) => { s = s.split("{" + x + "}").join(v[x]); }); return s; };
const h = (tag, a, ...kids) => ({ tag, a: a || {}, kids: [].concat(...kids).filter((x) => x !== null && x !== undefined && x !== false) });
const arr = (x) => (Array.isArray(x) ? x : []);
const S = { res: {}, busy: {}, st: { answers: {} }, product: { protocol_cards: %s } };
const U = {};
const ui = (k) => (U[k] = U[k] || {});
const render = () => {};
const running = () => null, jobFor = () => null, modePending = () => null;
const modeCards = () => ({ tag: "mode" });
const liveList = () => ({ tag: "live" }), runningNote = () => [], resNote = (r) => ({ tag: "res" }), ghCard = () => ({ tag: "gh" });
const btn = (label, kind, act, o) => ({ tag: "btn", label, k: (o || {}).k, disabled: !!(o || {}).disabled });
const note = (tone, title) => ({ tag: "note", tone, title });
const chip = () => ({ tag: "chip" }), box = () => ({ tag: "box" }), icon = () => ({ tag: "icon" });
const choice = (o) => ({ tag: "choice", k: o.k });
const card = (a, ...kids) => ({ tag: "card", a: a || {}, kids: [].concat(...kids).filter((x) => x !== null && x !== undefined && x !== false) });
const field = (label, input) => ({ tag: "field", label });
const input = (k) => ({ tag: "input", k });
const installProtocols = () => {}, reload = () => {}, issueDevice = () => {}, open = () => {}, loadOffers = () => {};
const platformsList = () => [{ id: "ios", title: "iPhone и iPad" }, { id: "android", title: "Android" }];
const pfTitle = (id) => id;
const issueCard = () => ({ tag: "issue" }), connRows = () => [{ tag: "conn" }];
const VIEW = {};
const sig = (n) => (n && n.tag ? n.tag + (n.a && n.a.class ? "." + n.a.class.split(" ")[0] : "") : String(n));
"""


def stubs():
    from kaskad.setup import product
    return STUBS % (json.dumps(i18n_dict()), json.dumps(product.PROTOCOLS))


def i18n_dict():
    src = read("setup/setup-i18n-ru.js")
    return dict(re.findall(r'^\s*"([a-zA-Z0-9.]+)":\s*"((?:[^"\\]|\\.)*)"', src, flags=re.M))


PROTO_JS = r"""
%s
%s
%s
%s
%s
const before = VIEW.protocols();
S.res["protocols-plan"] = { ok: true, state: "ok", data: { gateway: { supported: true }, base_mb: 40, github: {}, module: {},
  protocols: Object.keys(S.product.protocol_cards).map((k) => ({ kind: k, title: k, why: "для чего", tags: [], awg: false, port: null, fixed: [] })) } };
const after = VIEW.protocols();
const grid = (v) => v.main.find((x) => x.a && x.a.class === "qs-grid-protos");
console.log(JSON.stringify({ before: before.main.map(sig), after: after.main.map(sig),
  nBefore: grid(before).kids.length, nAfter: grid(after).kids.length, skel: grid(before).kids[0].a.class,
  status: before.main[1].kids[1].a.role }));
"""

CONNECT_JS = r"""
%s
%s
%s
%s
const u = ui("done");
Object.assign(u, { pf: "ios", who: "Мама", offers: { ios: { loading: true, offers: [], notes: [] } } });
const shape = (main) => { const c = main[0]; return { kids: c.kids.map(sig), btn: c.kids.find((x) => x.a && x.a.class === "qs-btns").kids[0] }; };
const loading = shape(connectView());
u.offers.ios = { offers: [{ inbound: "awg3", title: "AmneziaWG 3.1", note: "AmneziaVPN или AmneziaWG", enabled: true }], notes: [] };
const loaded = shape(connectView());
Object.assign(u, { issue: { name: "Мама · iPhone и iPad", items: [] }, device: "d-1", devPf: "ios", inp: "awg3", devWho: "Мама" });
const issued = shape(connectView());
console.log(JSON.stringify({ loading, loaded, issued }));
"""


@unittest.skipUnless(HAS_NODE, "нет node")
class StableLayoutTest(unittest.TestCase):
    def test_stage2_the_list_takes_the_place_of_its_blanks(self):
        js = read("setup/setup.js")
        got = node(PROTO_JS % (stubs(), jsparts.function(js, "protoSkel"), jsparts.function(js, "portText"),
                               "", block(js, "VIEW.protocols = () => {")))
        # everything up to the grid is the same before and after the plan: the title row, then the grid
        self.assertEqual(got["before"][:3], ["mode", "div.qs-row", "div.qs-grid-protos"])
        self.assertEqual(got["after"][:3], got["before"][:3])
        self.assertEqual(got["before"][3], "p.qs-small")                   # the line of the ports has its place too
        self.assertEqual(got["after"][3], "p.qs-small")
        self.assertEqual(got["nBefore"], got["nAfter"])                    # as many blanks as the cards of the product
        self.assertEqual(got["skel"], "qs-choice qs-choice--proto qs-sk")
        self.assertEqual(got["status"], "status")                          # «Смотрю, что можно поставить…» is said

    def test_stage2_css_keeps_the_width_and_the_height(self):
        css = read("setup/qs.css")
        self.assertRegex(css, r"\.qs-scroll \{[^}]*scrollbar-gutter: stable")
        self.assertRegex(css, r"\.qs \.qs-grid-protos > \.qs-choice--proto \{ min-height: \d+px")
        self.assertRegex(css, r"\.qs-pill\.qs-sk \{ width: \d+px")
        self.assertRegex(css, r"\.qs-inp-note \{ min-height:")

    def test_stage6_the_issue_button_keeps_its_place(self):
        js = read("setup/setup.js")
        got = node(CONNECT_JS % (stubs(), jsparts.function(js, "offersOf"), jsparts.function(js, "inputPills"),
                                 jsparts.function(js, "connectView")))
        self.assertEqual(got["loading"]["kids"], got["loaded"]["kids"])      # the row of inputs is there while loading
        self.assertEqual(got["loaded"]["kids"], got["issued"]["kids"])       # and the button after the issue
        self.assertIn("p.qs-small", got["loading"]["kids"])
        self.assertEqual((got["loaded"]["btn"]["k"], got["loaded"]["btn"]["disabled"]), ("issue", False))
        self.assertEqual((got["issued"]["btn"]["k"], got["issued"]["btn"]["label"], got["issued"]["btn"]["disabled"]),
                         ("issue", "Настройки выданы", True))

    def test_stage6_two_tabs_never_wide(self):
        js = read("setup/setup.js")
        view = block(js, "VIEW.done = () => {")
        self.assertIn('[["connect", t("d.tConnect")], ["summary", t("d.tSummary")]]', view)
        self.assertNotIn("wide", view)
        for gone in ("function schemeView", "function tourView", "function loadTrace", "function schemeModel"):
            self.assertNotIn(gone, js)
        self.assertNotIn('"scheme"', read("setup/dev-mock-setup.js").split("var VIDEOS")[-1].split("};")[0])

    def test_summary_says_the_name_once(self):
        js = read("setup/setup.js")
        got = node("%s\n%s\nconsole.log(JSON.stringify([%s]));" % (stubs(), jsparts.function(js, "peopleText"), ", ".join(
            "peopleText(%s)" % json.dumps(p) for p in (
                {"name": "Телефон", "devices": ["Телефон · iPhone и iPad"]},
                {"name": "Мама", "devices": ["Мама · iPhone и iPad", "Ноутбук"]},
                {"name": "Дача", "devices": ["Дача"]},
                {"name": "Боря", "devices": []}))))
        self.assertEqual(got, ["Телефон: iPhone и iPad", "Мама: iPhone и iPad, Ноутбук", "Дача", "Боря"])


# ── №4, №5 on the server ────────────────────────────────────────────────────

class StepsCase(RootCase):
    def setUp(self):
        RootCase.setUp(self)
        open_panel()
        new_link()
        self.runner = sysrun.FakeRunner()
        for p in (mock.patch.object(auth, "SCRYPT_N", 1 << 10), mock.patch.object(steps, "online", lambda force=False: True)):
            p.start()
            self.addCleanup(p.stop)
        steps.run("password", {"password": "Correct-Horse-9"}, self.runner)


class PortsSaidTest(StepsCase):
    def test_fixed_ports_are_said(self):
        self.assertEqual(steps._ports_said(None, [{"proto": "udp", "port": 1701}, {"proto": "udp", "port": 500},
                                                  {"proto": "udp", "port": 4500}]), " · порты 1701, 500, 4500/udp")
        self.assertEqual(steps._ports_said(None, [{"proto": "tcp", "port": 1723}]), " · порт 1723/tcp")
        self.assertEqual(steps._ports_said(51820, [{"proto": "udp", "port": 1701}]), " · порт 51820")
        self.assertEqual(steps._ports_said(None, None), "")

    def test_l2tp_in_the_job(self):
        plan = [{"kind": "l2tp", "title": "L2TP/IPsec", "fixed": [{"proto": "udp", "port": 1701, "conflicts": []},
                                                                 {"proto": "udp", "port": 500, "conflicts": []},
                                                                 {"proto": "udp", "port": 4500, "conflicts": []}]},
                {"kind": "wg", "title": "WireGuard", "fixed": []}]
        rows = []

        class P(jobs.Progress):
            def _flush(self):
                rows.append([dict(r) for r in self.rows])
        install = lambda runner, kind, **kw: {"ok": True, "record": {"port": 51820} if kind == "wg" else {"psk": "x"}}
        with mock.patch("kaskad.gw.ops.inbound_plan", lambda r, kinds, st=None: [p for p in plan if p["kind"] in kinds]), \
                mock.patch("kaskad.gw.ops.inbound_install", install), \
                mock.patch("kaskad.gw.ops.enable", lambda r: {"ok": True}):
            prog = P(None, "protocols")
            prog.job_id = "x"
            steps.run("protocols", {"kinds": ["l2tp", "wg"]}, self.runner, prog)
        last = dict((r["item"], r) for r in rows[-1])
        self.assertEqual(last["l2tp"]["detail"], "установлен · порты 1701, 500, 4500/udp")
        self.assertEqual(last["wg"]["detail"], "установлен · порт 51820")


class EmailTest(StepsCase):
    def cert(self, w):
        r = dict({"cert": {"names": ["vpn.example.com"], "not_after": "2027-01-01"}, "issued": True}, **w)
        with mock.patch.object(sops, "replaces_working", lambda name: False), mock.patch.object(sops, "issue", lambda runner, **kw: r), \
                mock.patch.object(sops, "status", lambda runner: {}), mock.patch.object(steps, "cert_served", lambda runner: True):
            return steps.run("cert", {}, self.runner)

    def test_no_mail_goes_apart_from_the_warnings(self):
        # the result of site.ops.issue: texts + the code of «no mail» (live check 3.3.2 №1: the step compared the text
        # with the code «no_email» — need was never true)
        r = {"warnings": [sops.warning(sops.NO_EMAIL), "Порт 80 был закрыт — открыл."], "warning_codes": [sops.NO_EMAIL],
             "warning_items": [{"code": sops.NO_EMAIL, "text": sops.warning(sops.NO_EMAIL)},
                               {"code": None, "text": "Порт 80 был закрыт — открыл."}]}
        out = self.cert(r)
        self.assertEqual(out["data"]["warnings"], ["Порт 80 был закрыт — открыл."])
        self.assertEqual(out["data"]["email"], {"need": True, "text": sops.warning(sops.NO_EMAIL)})
        # «need» — by the fact, the mail in the settings (T-M6-332b №4): no code and no mail — still the field
        out = self.cert({"warnings": [], "warning_codes": [], "warning_items": []})
        self.assertEqual((out["data"]["warnings"], out["data"]["email"]["need"]), ([], True))
        sops.set_email(self.runner, "admin@example.com")
        out = self.cert({"warnings": [], "warning_codes": [], "warning_items": []})
        self.assertEqual((out["data"]["warnings"], out["data"]["email"]), ([], {"need": False}))

    def test_the_step_sets_the_mail_as_the_cli(self):
        got = []

        def set_email(runner, email):
            got.append(email)
            return {"ok": True, "email": email, "warnings": []}
        with mock.patch.object(sops, "set_email", set_email):
            ok = steps.run("site-email", {"email": "admin@example.com"}, self.runner)
        self.assertEqual((ok["state"], ok["data"]["email"], got), ("ok", "admin@example.com", ["admin@example.com"]))
        bad = steps.run("site-email", {"email": "not a mail"}, self.runner)
        self.assertEqual((bad["state"], bad["error"]["field"]), ("error", "email"))

    def test_the_state_says_whether_the_mail_is_given(self):
        self.assertIs(steps.site_now()["email"], False)
        sops.set_email(self.runner, "admin@example.com")
        self.assertIs(steps.site_now()["email"], True)


MAIL_JS = r"""
%s
const isMail = %s
%s
const step = async () => ({ ok: true });
const out = {};
const need = { ok: true, data: { email: { need: true, text: "Почта для Let's Encrypt не задана — …" } } };
out.need = emailNote(need);
out.no = emailNote({ ok: true, data: { email: { need: false } } });
S.st = { site: { email: true } };
out.given = emailNote(need);
S.st = { site: { email: false } };
S.res["site-email"] = { ok: true, state: "ok", text: "Почта для Let's Encrypt: a@b.cd.", data: { email: "a@b.cd" } };
out.saved = emailNote(need);
console.log(JSON.stringify({ need: out.need && { tone: out.need.tone, title: out.need.title, input: JSON.stringify(out.need).indexOf("le-email") >= 0 },
  no: out.no, given: out.given, saved: out.saved && out.saved.tone, mail: [isMail("a@b.cd"), isMail("a@b"), isMail(" ")] }));
"""


@unittest.skipUnless(HAS_NODE, "нет node")
class EmailNoteTest(unittest.TestCase):
    def test_the_field_is_in_the_note(self):
        js = read("setup/setup.js")
        m = re.search(r"const isMail = (.*?);\n", js)
        scope = stubs().replace("const note = (tone, title) => ({ tag: \"note\", tone, title });",
                                "const note = (tone, title, text, o) => ({ tag: \"note\", tone, title, extra: (o || {}).extra });")
        got = node(MAIL_JS % (scope, m.group(1) + ";", jsparts.function(js, "emailNote")))
        self.assertEqual(got["need"], {"tone": "warning", "title": "Почта для Let's Encrypt — по желанию", "input": True})
        self.assertIsNone(got["no"])
        self.assertIsNone(got["given"])
        self.assertEqual(got["saved"], "success")
        self.assertEqual(got["mail"], [True, False, False])
        self.assertIn("emailNote(res)", jsparts.function(js, "certCard"))
        self.assertIn("Настройки", i18n_dict()["dm.emailLater"])


if __name__ == "__main__":
    unittest.main()

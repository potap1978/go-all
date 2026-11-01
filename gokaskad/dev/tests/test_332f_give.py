"""3.3.2 FIX-332f: the «Выдать настройки» window (give.js) — one component for the panel, the person's page /p/ and
step 6 of the wizard (design k3/design/issue-qr/export-2026-10-11, HANDOFF-give-settings.md).

The model and the behaviour run in node over a small DOM, with the issuances built from the REAL catalog
(gw/install_apps.json: install, support, alternatives) and items shaped as the adapters give them: ONE QR on the screen,
the store's QR in its card with the key's QR covered, Esc and a click outside close the card and the note, the dimmed
and the «workaround» chips, the states А/Б/В, the router's steps, the phone (QR folded, no «QR · install»), English
without a Cyrillic letter on every platform × protocol."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from kaskad.gw import install_apps, issue_os, model
from kaskad.i18n import use_lang
from kaskad import qr as qrfit
from kaskad.vendor import awgkeys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CYR = re.compile("[А-Яа-яЁё]")
HAS_NODE = bool(shutil.which("node"))


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def keys():
    raw = bytes((i * 7919 + 13) % 251 for i in range(1150))       # a compressed 3.1 key is 1130–1180 bytes
    vpn = "vpn://" + awgkeys.b64url_encode(raw)
    # parts as awg.py cuts them since 3.3.3 №38 (qr.part_size: two at most, the frame sized for them)
    return {"vpn": vpn, "parts": awgkeys.qr_chunks_from_bytes(raw, chunk_size=qrfit.part_size(len(raw))),
            "awgconf": "[Interface]\nPrivateKey = k\nAddress = 10.66.0.2/32\nJc = 4\nJmin = 40\nH1 = 1\n",
            "wgconf": "[Interface]\nPrivateKey = k\nAddress = 10.66.0.2/32\n\n[Peer]\nPublicKey = p\n",
            "vless": "vless://uuid@203.0.113.10:443?security=reality#goKaskad%20PRO"}


def items(kind, p):
    """The items of an issuance as the adapters make them (awg.py, wg.py, vless.py, the built-in clients)."""
    k = keys()
    routers = ("keenetic", "mikrotik", "openwrt", "tplink", "huawei", "xiaomi", "asus")
    if kind.startswith("awg"):
        conf = {"format": "conf", "title": "conf", "body": k["awgconf"], "filename": "goKaskadPro-mama-awg31.conf", "notes": ["n"]}
        if p in routers:
            return [conf]
        return [{"format": "vpn", "title": "vpn", "body": k["vpn"], "filename": "", "notes": []},
                {"format": "qr", "title": "qr app", "body": k["parts"], "filename": "", "notes": []},
                {"format": "qr", "title": "qr conf", "body": [k["awgconf"]], "filename": "", "notes": []}, conf]
    if kind == "wg":
        conf = {"format": "conf", "title": "conf", "body": k["wgconf"], "filename": "goKaskadPro-mama-wg.conf", "notes": []}
        if p == "mikrotik":
            return [{"format": "conf", "title": "rsc", "body": "/interface wireguard add name=kaskad-wg", "filename": "kaskad-wg.rsc",
                     "notes": []}, conf]
        return [{"format": "qr", "title": "qr", "body": [k["wgconf"]], "filename": "", "notes": []}, conf]
    if kind == "vless":
        return [{"format": "qr", "title": "qr", "body": [k["vless"]], "filename": "", "notes": []},
                {"format": "link", "title": "link", "body": k["vless"], "filename": "", "notes": []},
                {"format": "fields", "title": "fields", "body": [["Address", "203.0.113.10"]], "filename": "", "notes": []}]
    if kind == "openvpn":
        return [{"format": "ovpn", "title": "ovpn", "body": "client\n", "filename": "kaskad.ovpn", "notes": []}]
    return [{"format": "fields", "title": "fields", "body": [["Server", "203.0.113.10"], ["Password", "x"]], "filename": "", "notes": []}]


def issuance(kind, p, lang="ru", server=("awg3", "awg2", "wg", "vless", "l2tp"), device=("awg3", "awg2", "wg", "vless"),
             where="panel"):
    with use_lang(lang):
        alts = install_apps.alternatives(kind, p, server=list(server), device=list(device), where=where)
        return {"kind": kind, "os": p, "items": items(kind, p), "available": [k for k in device if k in server],
                "howto": [], "install": install_apps.block(kind, p),
                "support": install_apps.support(kind, p, alts, where=where), "alternatives": alts}


# A small DOM for node: elements, attributes, [data-gk="…"] lookups, bubbling events with stopPropagation, the
# capture listeners of the document, focus, timers by hand.
DOM_JS = r"""
class Node_ {
  constructor(tag) { this.tagName = String(tag).toUpperCase(); this.children = []; this.parentNode = null; this.attrs = {};
    this.on = {}; this.hidden = false; this._text = ""; this.style = { setProperty() {} }; this.className = ""; }
  get classList() { const n = this; return { contains: (c) => (" " + n.className + " ").indexOf(" " + c + " ") >= 0,
    toggle: (c, on) => { const has = (" " + n.className + " ").indexOf(" " + c + " ") >= 0; if (on && !has) n.className += " " + c; if (!on && has) n.className = (" " + n.className + " ").replace(" " + c + " ", " ").trim(); },
    add: (c) => { n.className += " " + c; } }; }
  appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = this; this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); c.parentNode = null; return c; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  replaceChild(n, o) { const i = this.children.indexOf(o); if (n.parentNode) n.parentNode.removeChild(n); n.parentNode = this;
    this.children[i] = n; o.parentNode = null; return o; }
  get firstChild() { return this.children[0] || null; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "class") this.className = String(v); }
  getAttribute(k) { return k === "class" ? this.className : (k in this.attrs ? this.attrs[k] : null); }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(ev, fn) { (this.on[ev] = this.on[ev] || []).push(fn); }
  removeEventListener(ev, fn) { this.on[ev] = (this.on[ev] || []).filter((x) => x !== fn); }
  contains(x) { while (x) { if (x === this) return true; x = x.parentNode; } return false; }
  get isConnected() { let x = this; while (x.parentNode) x = x.parentNode; return x === document.body; }
  focus() { document.activeElement = this; }
  click() { fire(this, "click"); }
  walk(f) { f(this); this.children.forEach((c) => c.walk && c.walk(f)); }
  all(pred) { const out = []; this.walk((n) => { if (pred(n)) out.push(n); }); return out; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    let m;
    if ((m = /^\[([a-z-]+)="([^"]*)"\]$/.exec(sel))) return this.all((n) => n !== this && n.getAttribute && n.getAttribute(m[1]) === m[2]);
    if ((m = /^\[([a-z-]+)\^="([^"]*)"\]$/.exec(sel))) return this.all((n) => n !== this && n.getAttribute && (n.getAttribute(m[1]) || "").indexOf(m[2]) === 0);
    if ((m = /^\.([\w-]+)$/.exec(sel))) return this.all((n) => n !== this && n.classList && n.classList.contains(m[1]));
    if ((m = /^\[role="(\w+)"\]$/.exec(sel))) return this.all((n) => n !== this && n.getAttribute && n.getAttribute("role") === m[1]);
    if (sel === "button") return this.all((n) => n !== this && n.tagName === "BUTTON");
    return [];
  }
}
class Text_ { constructor(t) { this.textContent = String(t); this.parentNode = null; } walk(f) { f(this); } }
const DOC_ON = {};
global.document = {
  createElement: (t) => new Node_(t), createElementNS: (ns, t) => new Node_(t), createTextNode: (t) => new Text_(t),
  body: null, activeElement: null, hidden: false,
  addEventListener: (ev, fn, cap) => { (DOC_ON[ev] = DOC_ON[ev] || []).push({ fn, cap: !!cap }); },
  removeEventListener: (ev, fn) => { DOC_ON[ev] = (DOC_ON[ev] || []).filter((x) => x.fn !== fn); },
};
document.body = new Node_("body");
global.window = global;
let PHONE = false;
const MQ = [];
global.matchMedia = (q) => { const m = { get matches() { return /max-width/.test(q) ? PHONE : false; }, addEventListener: (e, f) => MQ.push(f) }; return m; };
const TIMERS = [];
global.setInterval = (fn) => { TIMERS.push(fn); return TIMERS.length; };
global.clearInterval = () => {};
global.setTimeout = (fn) => { fn(); return 1; };
global.clearTimeout = () => {};
const sync = (v) => ({ then: (f) => sync(f ? f(v) : v) });             // a promise that answers at once
Object.defineProperty(global, "navigator", { configurable: true,                 // node has its own navigator
  value: { clipboard: { writeText: (t) => { COPIED.push(t); return sync(undefined); } } } });
const COPIED = [];
function fire(target, type, props) {
  let stopped = false;
  const ev = Object.assign({ type, target, key: undefined, stopPropagation() { stopped = true; }, preventDefault() {} }, props || {});
  (DOC_ON[type] || []).filter((x) => x.cap).forEach((x) => x.fn(ev));
  for (let n = target; n && !stopped; n = n.parentNode) (n.on && n.on[type] || []).slice().forEach((fn) => fn(ev));
  if (!stopped) (DOC_ON[type] || []).filter((x) => !x.cap).forEach((x) => x.fn(ev));
}
const gk = (root, k) => root.querySelector('[data-gk="' + k + '"]');
const has = (root, cls) => !!root.querySelector("." + cls);
"""


def run_node(src):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    finally:
        os.unlink(path)
    if r.returncode:
        raise AssertionError(r.stderr.decode("utf-8", "replace")[-3000:])
    return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])


def prelude(lang="ru"):
    i18n = read("give-i18n-ru.js") + (("\n" + read("give-i18n-en.js")) if lang == "en" else "")
    return DOM_JS + read("qr-slides.js") + "\n" + i18n + "\n" + read("give.js") + "\nconst G = window.KaskadGive;\n"


@unittest.skipUnless(HAS_NODE, "нет node")
class ModelTest(unittest.TestCase):
    def model(self, r, **o):
        o.setdefault("lang", "ru")
        js = prelude(o["lang"]) + "console.log(JSON.stringify(G.model(%s, %s)));" % (json.dumps(r), json.dumps(o))
        return run_node(js)

    def test_iphone_awg31_apps_and_one_key(self):
        r = issuance("awg3", "ios")
        vm = self.model(r, device={"name": "Телефон мамы"})
        self.assertEqual(vm["state"], "ok")
        ids = [a["id"] for a in vm["apps"]]
        self.assertEqual(ids, [a["id"] for a in r["install"]["apps"]])              # the catalog's apps and order
        self.assertEqual(ids[0], "defaultvpn")
        self.assertTrue(vm["apps"][0]["recommended"])
        self.assertFalse(any(a["recommended"] for a in vm["apps"][1:]))
        self.assertTrue(all(a["icon"].startswith("apps/") for a in vm["apps"]))   # the icons of the product
        # DefaultVPN takes the vpn:// link: the QR is its parts (2), «Скопировать ключ» copies the whole link
        self.assertEqual((vm["key"]["bundle"], vm["key"]["parts"], vm["key"]["ext"]), ("amnezia", len(keys()["parts"]), "vpn://"))
        self.assertEqual(vm["key"]["copy"], keys()["vpn"])
        self.assertTrue(vm["key"]["file"]["name"].endswith(".conf"))
        self.assertEqual(vm["keyName"], "goKaskad PRO · AmneziaWG 3.1 · Телефон мамы")
        self.assertEqual(vm["path"], ["+", "Вставить из буфера", "Продолжить"])
        # AmneziaWG takes the .conf: its own QR (one code) — the window never shows two
        vm2 = self.model(r, app="amneziawg")
        self.assertEqual((vm2["key"]["bundle"], vm2["key"]["parts"], vm2["key"]["ext"]), ("awg", 1, ".conf"))
        self.assertEqual(vm2["key"]["copy"], keys()["awgconf"])
        # the note of DefaultVPN («official fork…») is only in its tip
        self.assertTrue(vm["apps"][0]["tip"]["lines"])

    def test_vless_iphone_and_chips(self):
        vm = self.model(issuance("vless", "ios"))
        self.assertEqual(vm["apps"][0]["id"], "karing")
        self.assertEqual((vm["key"]["bundle"], vm["key"]["ext"], vm["key"]["file"]), ("vless", "vless://", None))
        self.assertEqual([c["kind"] for c in vm["chips"]], ["awg3", "awg2", "wg", "vless"])
        self.assertEqual([c["kind"] for c in vm["chips"] if c["on"]], ["vless"])

    def test_dimmed_and_workaround_chips(self):
        hu = self.model(issuance("awg3", "huawei", device=("awg3", "awg2", "wg", "vless", "l2tp")))
        dim = dict((c["kind"], c["dim"]) for c in hu["chips"])
        self.assertEqual(dim, {"awg3": True, "awg2": True, "wg": True, "vless": True, "l2tp": False})
        self.assertEqual(hu["chips"][1]["tip"], "Huawei не поддерживает AmneziaWG 2.0")
        mt = self.model(issuance("vless", "mikrotik"))
        work = dict((c["kind"], c["work"]) for c in mt["chips"])
        self.assertEqual(work, {"awg3": True, "awg2": True, "wg": False, "vless": True})
        self.assertFalse(any(c["dim"] for c in mt["chips"]))

    def test_states_a_b_c(self):
        a = self.model(issuance("vless", "mikrotik"))                       # А — a workaround: the banner + router steps
        self.assertEqual(a["state"], "work")
        self.assertEqual(a["workText"], "MikroTik не умеет VLESS штатно.")
        self.assertEqual(a["guide"], "https://anten-ka.com/guides/no30/")
        self.assertEqual((a["rt"]["how"], a["rt"]["check"]), ("link", ["WinBox", "Container"]))
        b = self.model(issuance("awg3", "huawei", server=("awg3", "l2tp"), device=("awg3", "l2tp")),
                       actions={"grant": True, "enable": True})
        self.assertEqual(b["state"], "none")                                # Б — «Подойдёт:» by the order of the catalog
        self.assertEqual(b["noneText"], "Huawei не поддерживает AmneziaWG 3.1.")
        self.assertEqual([(x["proto"], x["state"], x["act"]) for x in b["alts"]],
                         [("l2tp", "ready", "pick"), ("pptp", "server_off", "enable")])
        self.assertTrue(b["alts"][0]["note"])                               # «Без шифрования…» — a triangle by L2TP
        dev = self.model(issuance("awg3", "huawei", server=("awg3", "l2tp", "pptp"), device=("awg3",)),
                         actions={"grant": True, "enable": True})
        self.assertEqual([x["act"] for x in dev["alts"]], ["grant", "grant"])
        p = self.model(issuance("awg3", "huawei", server=("awg3", "l2tp"), device=("awg3", "l2tp"), where="page"),
                       viewer="person")                                     # /p/: no admin's buttons
        self.assertEqual([(x["act"], x["status"]) for x in p["alts"]], [("pick", "Готов к подключению"), ("ask", "Не включён на сервере")])
        self.assertEqual(p["alts"][1]["ask"], "Попросите администратора включить PPTP")
        c = self.model({"kind": "awg3", "os": "huawei", "items": [], "support": {"level": "none", "guide": "https://anten-ka.com/x"},
                        "alternatives": []})
        self.assertEqual((c["state"], c["noneText"]), ("nothing", "Для Huawei у нас нет подходящего протокола."))

    def test_router_keenetic_awg31(self):
        vm = self.model(issuance("awg3", "keenetic"))
        self.assertEqual(vm["state"], "ok")
        self.assertTrue(vm["router"])
        self.assertEqual(vm["rt"]["how"], "file")
        self.assertIn("WireGuard VPN", vm["rt"]["prep"])
        lines = json.dumps(vm["note"]["lines"], ensure_ascii=False)
        self.assertIn("5.2 Alpha 11", lines)                                 # the condition of the pair, in the tip
        self.assertTrue(any("awg-manager" in l["url"] or "keenetic" in l["url"] for l in vm["note"]["links"]))
        mt = self.model(issuance("wg", "mikrotik"))
        self.assertEqual((mt["rt"]["how"], mt["rt"]["term"]), ("cmd", "Терминал RouterOS"))
        hu = self.model(issuance("l2tp", "huawei", device=("l2tp",)))
        self.assertEqual(hu["rt"]["how"], "fields")

    def test_english_has_no_cyrillic_anywhere(self):
        combos = []
        for p in model.PLATFORMS:
            for kind in issue_os.TITLES:
                combos.append(issuance(kind, p, lang="en", device=("awg3", "awg2", "wg", "vless", "l2tp", kind)))
        js = prelude("en") + "const R = %s;\nconst out = [];\nfor (const r of R) { for (const v of ['admin', 'person']) " \
             "{ const m = G.model(r, { lang: 'en', viewer: v, device: { name: 'Mom' }, actions: { grant: true, enable: true } }); " \
             "out.push([r.os + ':' + r.kind + ':' + v, JSON.stringify(m)]); } }\n" \
             "console.log(JSON.stringify(out.filter((x) => /[А-Яа-яЁё]/.test(x[1])).map((x) => x[0])));" % json.dumps(combos)
        self.assertEqual(run_node(js), [])


@unittest.skipUnless(HAS_NODE, "нет node")
class BehaviourTest(unittest.TestCase):
    """The window as people use it: the store's card, the key's QR covered, Esc, the notes, the phone."""

    def test_card_tip_esc_phone(self):
        r = issuance("awg3", "ios")
        js = prelude() + r"""
const R = %s, RK = %s;
const out = {};
const g = G.mount({ viewer: "admin", lang: "ru", selectors: true, device: { name: "Телефон мамы" },
  onPlatform: () => {}, onProto: () => {}, drawQr: () => document.createElement("i"), drawLinkQr: () => document.createElement("b") });
document.body.appendChild(g.el);
g.set(R);
const root = g.el;
out.qrs = root.querySelectorAll(".gs-qr").length;
out.storeBtns = root.querySelectorAll('[data-gk^="store:"]').length;
fire(gk(root, "store:defaultvpn"), "click");                       // «QR · установить» — the card, the key covered
out.card = { open: has(root, "gs-card"), cover: has(root, "gs-cover"), covered: root.querySelector(".gs-qr").className,
  expanded: gk(root, "store:defaultvpn").getAttribute("aria-expanded") };
fire(gk(root, "store:amneziavpn"), "click");                         // one card at a time
out.oneCard = root.querySelectorAll(".gs-card").length;
fire(root.querySelector(".gs-card"), "keydown", { key: "Escape" });   // Esc closes it
out.afterEsc = { card: has(root, "gs-card"), cover: has(root, "gs-cover"), focus: document.activeElement && document.activeElement.getAttribute("data-gk") };
fire(gk(root, "store:defaultvpn"), "click");
fire(gk(root, "showkey"), "click");                                  // «Показать ключ» closes it too
out.afterShowKey = has(root, "gs-card");
// круг 1 (R-it1-tri): у строк приложений треугольников нет — пометки серой строкой; у пары «iPhone × AmneziaWG 3.1»
// пометки больше нет (решение координатора 29.10: она повторяла серую пометку incy), под заголовком — инструкция пары
out.appTips = root.all((n) => n.getAttribute && /^tip:/.test(n.getAttribute("data-gk") || "")).length;
out.notes = root.querySelectorAll(".gs-app__nt-t").length;
// механизм подсказки (нажатие закрепляет, одна за раз, нажатие мимо снимает) — у шага роутера «Подготовьте роутер»
const gr = G.mount({ viewer: "admin", lang: "ru", selectors: true, onPlatform: () => {}, onProto: () => {} });
document.body.appendChild(gr.el); gr.set(RK);
fire(gk(gr.el, "tip:router"), "click");                              // a note: a click pins it
out.tip = { open: has(gr.el, "gs-tip"), expanded: gk(gr.el, "tip:router").getAttribute("aria-expanded") };
out.oneTip = gr.el.querySelectorAll(".gs-tip").length;
fire(document.body, "click");                                        // a click outside unpins
out.afterOutside = has(gr.el, "gs-tip");
fire(gk(root, "app:amneziawg"), "click");                            // another app — its format, still one QR
out.app = { qrs: root.querySelectorAll(".gs-qr").length, checked: gk(root, "app:amneziawg").getAttribute("aria-checked") };
fire(gk(root, "copykey"), "click");
out.copied = COPIED[COPIED.length - 1] === %s;
PHONE = true; MQ.forEach((f) => f());                                // the phone: QR folded, no «QR · установить»
out.phone = { storeBtns: root.querySelectorAll('[data-gk^="store:"]').length, qrs: root.querySelectorAll(".gs-qr").length,
  fold: !!gk(root, "qrph"), open: !!root.querySelector(".gs-open") };
fire(gk(root, "qrph"), "click");
out.phoneOpen = root.querySelectorAll(".gs-qr").length;
console.log(JSON.stringify(out));
""" % (json.dumps(r), json.dumps(issuance("awg3", "keenetic")), json.dumps(keys()["awgconf"]))
        got = run_node(js)
        self.assertEqual(got["qrs"], 1)
        self.assertEqual(got["storeBtns"], len(r["install"]["apps"]))
        self.assertEqual(got["card"], {"open": True, "cover": True, "covered": "gs-qr is-covered", "expanded": "true"})
        self.assertEqual(got["oneCard"], 1)
        self.assertEqual(got["afterEsc"], {"card": False, "cover": False, "focus": "store:amneziavpn"})
        self.assertFalse(got["afterShowKey"])
        self.assertEqual((got["appTips"], got["notes"] > 0), (0, True), got)
        self.assertEqual(got["tip"], {"open": True, "expanded": "true"})
        self.assertEqual(got["oneTip"], 1)
        self.assertFalse(got["afterOutside"])
        self.assertEqual(got["app"], {"qrs": 1, "checked": "true"})
        self.assertTrue(got["copied"])
        self.assertEqual(got["phone"], {"storeBtns": 0, "qrs": 0, "fold": True, "open": True})
        self.assertEqual(got["phoneOpen"], 1)

    def test_alternatives_buttons_and_person(self):
        r = issuance("awg3", "huawei", server=("awg3", "l2tp"), device=("awg3", "l2tp"))
        js = prelude() + r"""
const R = %s, calls = [];
const mk = (viewer, extra) => { const g = G.mount(Object.assign({ viewer, lang: "ru", onProto: (k) => calls.push("proto:" + k) }, extra || {}));
  document.body.appendChild(g.el); g.set(R); return g.el; };
const a = mk("admin", { onGrant: (k) => calls.push("grant:" + k), onEnable: (k) => calls.push("enable:" + k) });
fire(gk(a, "alt:l2tp"), "click"); fire(gk(a, "alt:pptp"), "click");
const p = mk("person");
console.log(JSON.stringify({ calls, adminQr: a.querySelectorAll(".gs-qr").length, personBtn: !!gk(p, "alt:pptp"),
  ask: p.querySelector(".gs-alt__ask").textContent }));
""" % json.dumps(r)
        got = run_node(js)
        self.assertEqual(got["calls"], ["proto:l2tp", "enable:pptp"])
        self.assertEqual(got["adminQr"], 0)                                  # Б: no QR, no app steps
        self.assertFalse(got["personBtn"])
        self.assertEqual(got["ask"], "Попросите администратора включить PPTP")


class WiringTest(unittest.TestCase):
    """Who loads the component and its words; the /p/ route serves them; the new tokens of the design."""

    def test_pages_load_it(self):
        self.assertIn('href="give.css?v=__KASKAD_V__"', read("index.html"))
        self.assertIn('loadScript("give.js")', read("app.js"))
        setup = read("setup/index.html")
        for need in ('href="../give.css?v=__KASKAD_V__"', 'src="../give.js?v=__KASKAD_V__"', 'src="../give-i18n-ru.js?v=__KASKAD_V__"'):
            self.assertIn(need, setup)
        self.assertIn('load("../give-i18n-en.js"', read("setup/setup.js"))
        user = read("user.html")
        for need in ("/p/give.js?v={{V_GIVE}}", "/p/give.css?v={{V_GCSS}}", "/p/give-i18n-{{LANG}}.js?v={{V_GI18N}}",
                     "/p/icons.js?v={{V_ICONS}}"):
            self.assertIn(need, user)
        from kaskad.panel import userpage
        for name in ("give.js", "give.css", "give-i18n-ru.js", "give-i18n-en.js", "icons.js"):
            self.assertIn(name, userpage.ASSETS)

    def test_new_tokens(self):
        tokens, user = read("tokens.css"), read("user.css")
        for t in ("--surface-raised:", "--popover-shadow:", "--warning-icon:"):
            self.assertEqual(tokens.count(t), 2, t)                          # dark and light
            self.assertEqual(user.count(t), 2, t)
        self.assertIn("--warning-icon: oklch(.78 .16 72)", tokens)
        self.assertIn("--warning-icon-stroke: var(--warning-text)", tokens)

    def test_icons_of_the_component_exist(self):
        have = set(re.findall(r'^\s*"([a-z0-9-]+)": \[[FES],', read("icons.js"), flags=re.M))
        used = set(re.findall(r"\bic\('([a-z0-9-]+)'", read("give.js"))) | set(re.findall(r"\bicon\('([a-z0-9-]+)'", read("user.js")))
        used |= set(re.findall(r"'([a-z0-9-]+)'", re.search(r"var DEV_ICON = \{(.*?)\};", read("user.js")).group(1)))
        self.assertTrue(used)
        self.assertEqual(sorted(used - have), [])

    def test_words_of_both_languages(self):
        from tests.test_i18n_web import dictionary
        ru, en = dictionary("give-i18n-ru.js"), dictionary("give-i18n-en.js")
        self.assertEqual(sorted(ru), sorted(en))
        self.assertEqual([k for k, v in en.items() if CYR.search(v)], [])
        # HANDOFF §5
        for k, want in (("title", "Issue settings"), ("s1", "Install the app"), ("qrInstall", "QR · install"),
                        ("cover", "Install the app first"), ("showKey", "Show key"), ("part", "Part {i} of {n}"),
                        ("qrOther", "QR for another device"), ("footPerson", "This link is personal — don’t share it."),
                        ("fits", "These will work:"), ("ask", "Ask your admin to enable {protocol}".replace("{protocol}", "{proto}"))):
            self.assertEqual(en[k], want, k)
        self.assertEqual(ru["footAdmin"], "Здесь ключ — показывайте только самому пользователю. Панель его не хранит.")


if __name__ == "__main__":
    unittest.main()

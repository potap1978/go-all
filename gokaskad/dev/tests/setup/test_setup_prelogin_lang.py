"""3.3.2, живая проверка: на странице входа мастера нет переключателя языка. Теперь он есть на экранах до входа (вход,
ссылка, истекла…) — тот же вид, что в шапке мастера после входа (.qs-lang, RU/EN). До входа он меняет ТОЛЬКО язык этой
страницы: без запроса к серверу (язык сервера без сессии менять нельзя — POST /api/setup/lang отвечает 401), выбор
хранит браузер (localStorage в try/catch); после входа выбор стирается и действует язык сервера.

Настоящие функции setup.js (tests/jsparts.py) в node с поддельными DOM, хранилищем и API. Без node — пропуск."""

import json
import os
import re
import shutil
import subprocess
import unittest

from tests import jsparts

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib", "kaskad",
                      "panel", "static", "setup")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def scope():
    js = read("setup.js")
    parts = [jsparts.const(js, "PRE_LANG")]
    parts += [jsparts.function(js, n) for n in ("plural", "loadEn", "usePageLang", "prefLang", "keepPrefLang",
                                                "loadLang", "langSwitch", "start")]
    return """
const makePage = (STORE) => (function () {
  const out = { calls: [], toasts: [], renders: 0, loaded: [] };
  const localStorage = {
    getItem: (k) => { if (STORE.broken) throw new Error("denied"); return Object.prototype.hasOwnProperty.call(STORE.data, k) ? STORE.data[k] : null; },
    setItem: (k, v) => { if (STORE.broken) throw new Error("denied"); STORE.data[k] = String(v); },
    removeItem: (k) => { if (STORE.broken) throw new Error("denied"); delete STORE.data[k]; } };
  const attrs = {};
  const document = { title: "", documentElement: { setAttribute: (k, v) => { attrs[k] = v; } }, querySelector: () => null };
  const window = { KASKAD_SETUP_I18N: { "c.docTitle": "Быстрый старт", "c.lang": "Язык", "c.langRu": "Русский", "c.langEn": "English", "sys.error": "Ошибка" } };
  const KL = { lang: SERVER, locale: SERVER === "en" ? "en-GB" : "ru-RU", form: (n, f) => "KL:" + f };
  const SERVER_LANG = KL.lang;
  let LANG = SERVER_LANG;
  let LOCALE = KL.locale;
  const L_RU = window.KASKAD_SETUP_I18N;
  let L_EN = null;
  let L = L_RU;
  let NF1 = null;
  function t(key, vars) { return Object.prototype.hasOwnProperty.call(L, key) ? L[key] : key; }
  const S = { sys: null, langBusy: false, session: {}, busy: {} };
  let es = null;
  const load = async (src) => { out.loaded.push(src); window.KASKAD_SETUP_I18N_EN = { "c.docTitle": "Quick Start", "c.lang": "Language", "sys.error": "Error" }; };
  const render = () => { out.renders++; };
  const toast = (x) => out.toasts.push(x);
  const h = (tag, a, ...kids) => ({ tag, a: a || {}, kids: [].concat(...kids) });
  let SESSION = { auth: false, reason: "password" };
  const api = async (path, body) => { out.calls.push(path); if (path === "session") return { status: 200, body: SESSION }; if (path === "lang") return { status: 200, body: { ok: true } }; return { status: 200, body: {} }; };
  const location = { reload: () => out.calls.push("reload") };
  const applyLook = () => {};
  const showSys = (k) => { S.sys = { kind: k }; };
  const loadState = async () => { out.calls.push("state:" + LANG); };
  const loadIpcert = async () => {};
  const checkNet = async () => {};
  const startEvents = () => {};
%s
  const click = async (v) => { const sw = langSwitch(); const b = sw.kids.find((x) => x.a["data-k"] === "lang-" + v); await b.a.on.click(); return sw; };
  return { S, out, STORE, attrs, document, click, langSwitch, loadLang, start, plural, prefLang, keepPrefLang,
           lang: () => LANG, text: (k) => t(k), setSession: (s) => { SESSION = s; } };
})();
""" % "\n".join(parts)


NODE = r"""
const SERVER = process.argv[1];
%s
const assert = (c, m) => { if (!c) { console.error("FAIL " + m); process.exit(1); } };
(async () => {
  const STORE = { data: {}, broken: false };
  const P = makePage(STORE), OTHER = SERVER === "ru" ? "en" : "ru";
  // 1. the login screen: the switch is there, the same look as in the wizard (RU/EN, aria-pressed)
  await P.loadLang();
  assert(P.lang() === SERVER, "server language first");
  P.S.sys = { kind: "login" };
  let sw = P.langSwitch();
  assert(sw.a.class === "qs-lang" && sw.kids.length === 2 && sw.kids.map((b) => b.kids[0]).join() === "RU,EN", "same switch");
  assert(sw.kids.find((b) => b.a["data-k"] === "lang-" + SERVER).a["aria-pressed"] === "true", "pressed = page language");
  // 2. a pick before the login: this page only, no request to the server, the choice is kept by the browser
  await P.click(OTHER);
  assert(P.lang() === OTHER, "page language changed: " + P.lang());
  assert(P.out.calls.indexOf("lang") < 0 && P.out.calls.indexOf("reload") < 0, "no request, no reload: " + P.out.calls);
  assert(P.STORE.data[%s] === OTHER, "kept in the browser");
  assert(P.attrs.lang === OTHER, "<html lang>");
  assert(P.document.title === (OTHER === "en" ? "Quick Start" : "Быстрый старт"), "tab title: " + P.document.title);
  assert(!P.S.langBusy && P.out.toasts.length === 0, "done quietly");
  // 3. plural of the page's language, not of boot.js (KL is the server's)
  if (OTHER === "en") assert(P.plural(1, "one|other") === "one" && P.plural(5, "one|other") === "other", "en plural");
  else assert(P.plural(5, "одна|несколько|много") === "много" && P.plural(3, "одна|несколько|много") === "несколько", "ru plural");
  // 4. the next load of the page before the login: the choice is back
  const P2 = makePage(STORE);
  P2.S.sys = { kind: "login" };
  await P2.loadLang();
  assert(P2.lang() === OTHER, "choice kept for the page before the login");
  // 5. after the login: the choice goes, the server's language rules (state asked already in it)
  P2.setSession({ auth: true });
  await P2.start();
  assert(P2.lang() === SERVER, "server language after the login: " + P2.lang());
  assert(!(%s in P2.STORE.data), "choice forgotten after the login");
  assert(P2.out.calls.indexOf("state:" + SERVER) >= 0, "state in the server language: " + P2.out.calls);
  assert(P2.S.sys === null, "wizard screen");
  // 6. after the login the switch is the server's one again: POST lang, then a reload
  P2.out.calls.length = 0;
  await P2.click(OTHER);
  assert(P2.out.calls.join() === "lang,reload", "server switch after the login: " + P2.out.calls);
  // 7. a closed storage (private mode): no exception, the page still switches
  P2.STORE.broken = true;
  P2.S.sys = { kind: "link" };
  P2.S.langBusy = false;                                 // after the reload of step 6 — a fresh page
  assert(P2.prefLang() === "", "no storage — no choice");
  P2.keepPrefLang("en");
  const before = P2.lang(), to = before === "ru" ? "en" : "ru";
  await P2.click(to);
  assert(P2.lang() === to, "switches without storage");
  console.log("ok");
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "нет node")
class PreLoginLanguageTest(unittest.TestCase):
    def run_node(self, server):
        key = json.dumps("kaskad-setup-prelogin-lang")
        src = NODE % (scope(), key, key)
        r = subprocess.run(["node", "-e", src, server], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode() + r.stdout.decode())
        self.assertIn("ok", r.stdout.decode())

    def test_server_in_russian(self):
        self.run_node("ru")

    def test_server_in_english(self):
        self.run_node("en")


class PreLoginLanguageStaticTest(unittest.TestCase):
    def test_every_screen_before_the_login_has_the_switch(self):
        js = read("setup.js")
        sys = jsparts.function(js, "sysScreen")
        self.assertRegex(sys, r'class: "qs-sys__head" }, h\("span", \{ class: "qs-sys__cap"[^)]*\), langSwitch\(\)')
        self.assertIn('const PRE_LANG = "kaskad-setup-prelogin-lang";', js)
        # the header of every request follows the page's language (texts of the login screen from the server)
        self.assertIn('"X-Kaskad-Lang": LANG', jsparts.function(js, "api"))
        css = read("qs.css")
        self.assertRegex(css, r"\.qs-sys__head \{[^}]*display: flex")
        # only the language goes to the browser's storage — never a password or a token
        stores = re.findall(r"localStorage\.setItem\(([^,]+),", js)
        self.assertEqual(stores, ["PRE_LANG"])


if __name__ == "__main__":
    unittest.main()

"""The move of the wizard to the domain and the sticky «нет связи» (T-M6-qs330 №2) in node: the REAL functions of
setup.js (tests/jsparts.py) against the REAL answer of POST /api/setup/move of the panel server — the page takes the
token before the certificate, goes to the address of the domain by itself after it, and without a connection says
«страница по IP больше не открывается» with the link instead of «проверьте интернет»; the layout keeps the banner
outside the scroll; the A-record tiles stand in one row (POLISH п.6). Skipped without node."""

import json
import os
import re
import shutil
import subprocess
import unittest
from unittest import mock

from tests import jsparts
from tests.setup.test_setup_web import PW, SetupWebCase

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib", "kaskad",
                      "panel", "static", "setup")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def scope():
    js = read("setup.js")
    parts = [jsparts.const(js, "onDomain"), jsparts.const(js, "MOVE_TRIES")]
    parts += [jsparts.const(js, n) for n in ("domName", "domFull")]
    parts += [jsparts.function(js, n) for n in ("alerts", "domBody", "prepMove", "startCert", "moveSoon", "moveReady", "goMove",
                                                "moveNote")]
    return """
const page = (function () {
  const out = { assigned: [], timers: [], steps: [], calls: [], toasts: [], pauses: [] };
  let MOCK = false;
  let NOW = 1000;
  const now = () => NOW;
  const S = { noConn: false, busy: {}, move: null, moveAt: 0, moveStay: false, moveCheck: "", res: {} };
  const pause = async (ms) => { out.pauses.push(ms); };
  const U = { domain: { name: "vpn.example.com" } };
  const ui = (k) => (U[k] = U[k] || {});
  const location = { hostname: "203.0.113.10", assign: (u) => out.assigned.push(u) };
  const t = (key, vars) => key + (vars ? " " + JSON.stringify(vars) : "");
  const h = (tag, a, ...kids) => ({ tag, a, kids: [].concat(...kids) });
  const note = (tone, title, text, o) => {
    const extra = [].concat((o && o.extra) || []);
    const row = extra.find((x) => x && x.tag === "div") || { kids: [] };
    return { tone, title, text, code: (extra.find((x) => x && x.code) || {}).code, btns: ((o && o.btns) || []).concat(row.kids) };
  };
  const code = (text) => ({ code: text });
  const btn = (label, kind, act, o) => ({ label, k: (o || {}).k, act });
  const render = () => {};
  const toast = (x) => out.toasts.push(x);
  const setTimeout = (fn, ms) => { out.timers.push([fn, ms]); return out.timers.length; };
  let API = null;
  const api = async (path, body) => { out.calls.push(path); return API(path, body); };
  const step = async (name, body) => { out.steps.push(name); return { ok: true }; };
%s
  return { S, ui, location, out, alerts, prepMove, startCert, moveSoon, goMove, moveNote,
           setApi: (f) => { API = f; }, tick: (s) => { NOW += s; } };
})();
""" % "\n".join(parts)


NODE = r"""
const ANSWER = JSON.parse(process.argv[1]), READY = JSON.parse(process.argv[2]), LATE = JSON.parse(process.argv[3]);
%s
const assert = (c, m) => { if (!c) { console.error("FAIL " + m); process.exit(1); } };
(async () => {
  const P = page;
  let up = true;
  let served = 0;                                         // move-ready: LATE до served-го вопроса, потом READY
  P.setApi(async (path) => {
    if (!up) throw Object.assign(new Error("net"), { net: true });
    if (path === "move-ready") return { status: 200, body: served-- > 0 ? LATE : READY };
    return { status: 200, body: ANSWER };
  });
  // 1. the certificate starts on the IP page: the token of the move is taken first, then the step
  await P.startCert();
  assert(P.out.calls[0] === "move" && P.out.steps[0] === "cert", "move before cert: " + P.out.calls + " / " + P.out.steps);
  assert(P.S.move && P.S.move.url === ANSWER.url && P.S.move.host === "vpn.example.com", "S.move");
  // 2. the connection is lost while it runs: the sticky banner names the domain and leads there
  P.S.noConn = true;
  let a = P.alerts();
  assert(a.length === 1 && a[0].title === "sys.ipGone", "ipGone: " + JSON.stringify(a));
  assert(a[0].code === ANSWER.clean && a[0].code.indexOf("?t=") < 0, "clean address shown, no token");
  assert(a[0].text.indexOf("vpn.example.com") > 0, "names the domain");
  up = false;
  P.tick(120);
  await a[0].btns[0].act();                              // stale token, no connection: the one it has
  assert(P.out.assigned.pop() === ANSWER.url, "button opens the domain");
  up = true;
  // 3. a real outage on the domain itself (or before the move) — the neutral text, no «интернет на этом устройстве»
  P.location.hostname = "vpn.example.com";
  a = P.alerts();
  assert(a.length === 1 && a[0].title === "sys.noConn" && a[0].text === "sys.noConnB", "neutral on the domain");
  P.location.hostname = "203.0.113.10";
  const saved = P.S.move; P.S.move = null;
  assert(P.alerts()[0].title === "sys.noConn", "neutral without a move");
  P.S.move = saved;
  P.S.noConn = false;
  assert(P.alerts().length === 0, "no banner with a connection");
  // 4. the certificate is issued: the move note, then the page goes to the domain by itself (a fresh token)
  P.S.res.cert = { ok: true };
  P.out.calls.length = 0;
  P.moveSoon();
  await new Promise((ok) => globalThis.setTimeout(ok, 0));
  assert(P.S.moveAt > 0 && P.moveNote() && P.moveNote().title.indexOf("dm.moveT") === 0, "move note");
  const timer = P.out.timers.pop();
  assert(timer[1] >= 3000, "a few seconds to read: " + timer[1]);
  P.tick(120);                                          // the token is old by now: taken again before leaving
  served = 2;                                           // the panel answers the domain with the old certificate twice
  await timer[0]();
  await new Promise((ok) => globalThis.setTimeout(ok, 0));
  const asked = P.out.calls.filter((c) => c === "move-ready").length;
  assert(asked === 3, "asked the server until the domain is served: " + asked);
  assert(P.out.calls.lastIndexOf("move") > P.out.calls.lastIndexOf("move-ready"), "the token after the check");
  assert(P.out.assigned.pop() === ANSWER.url && P.S.moveCheck === "", "auto move with a fresh token, only when ready");
  // 4b. the domain never answers with its certificate: the page stays, says why, «Перейти сейчас» still works
  P.out.calls.length = 0; P.out.pauses.length = 0;
  P.moveSoon();
  await new Promise((ok) => globalThis.setTimeout(ok, 0));
  served = 1000;
  await P.out.timers.pop()[0]();
  await new Promise((ok) => globalThis.setTimeout(ok, 0));
  assert(P.out.assigned.length === 0, "no move without the domain's certificate");
  assert(P.out.calls.filter((c) => c === "move-ready").length === 10 && P.out.pauses.length === 9, "10 tries: " + P.out.calls);
  let n = P.moveNote();
  assert(P.S.moveCheck === "late" && n.tone === "warning" && n.text.indexOf("dm.moveLate") === 0, "late note: " + JSON.stringify(n));
  const go = n.btns.find((b) => b.k === "move-go");
  assert(go, "«Перейти сейчас» stays");
  await go.act();
  assert(P.out.assigned.pop() === ANSWER.url, "the button goes by the owner's choice");
  // 4c. no connection at all while checking: the same — stays
  P.S.moveCheck = ""; up = false;
  P.moveSoon();
  await new Promise((ok) => globalThis.setTimeout(ok, 0));
  const t4 = P.out.timers.pop();
  assert(t4, "the countdown again");
  await t4[0]();
  await new Promise((ok) => globalThis.setTimeout(ok, 0));
  assert(P.out.assigned.length === 0 && P.S.moveCheck === "late", "no move without an answer");
  up = true; served = 0; P.S.moveCheck = "";
  // 5. «Остаться здесь»: no move, no note
  P.S.moveStay = true;
  P.moveSoon();
  assert(P.moveNote() === null && P.out.timers.length === 0, "stay");
  // 6. already on the domain: no token asked, no move
  P.S.moveStay = false; P.out.calls.length = 0; P.out.steps.length = 0;
  P.location.hostname = "VPN.example.com";
  await P.startCert();
  P.moveSoon();
  assert(P.out.calls.length === 0 && P.out.steps[0] === "cert" && P.moveNote() === null && P.out.timers.length === 0, "on domain");
  console.log("ok");
})().catch((e) => { console.error("FAIL", e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "нет node")
class MoveJsTest(SetupWebCase):
    def test_move_and_sticky_banner_against_the_server(self):
        tok, _, _ = self.enter()
        r = self.scall("POST", "/api/setup/step/password", {"password": PW}, cookie=tok)
        cookie, _ = self.setup_cookie(r)
        self.public_on()
        with mock.patch("kaskad.site.model.domain_of", return_value="vpn.example.com"):
            answer = self.scall("POST", "/api/setup/move", {}, cookie=cookie).json()
        self.assertTrue(answer["ok"], answer)
        late = self.scall("GET", "/api/setup/move-ready", cookie=cookie).json()          # no domain certificate yet
        self.assertEqual((late["ok"], late["ready"], late["reason"]), (True, False, "no_cert"), late)
        from tests.panel.test_panel_sni import site_fixture
        from kaskad.panel import conf
        with mock.patch.object(conf, "site_cert", return_value=("vpn.example.com", site_fixture("le.pem"),
                                                                 site_fixture("le.key"))):
            ready = self.scall("GET", "/api/setup/move-ready", cookie=cookie).json()   # a real handshake, SNI
        self.assertEqual((ready["ok"], ready["ready"], ready["host"]), (True, True, "vpn.example.com"), ready)
        p = subprocess.run(["node", "-e", NODE % scope(), json.dumps(answer), json.dumps(ready), json.dumps(late)],
                           stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertIn("ok", p.stdout)


class LayoutTest(unittest.TestCase):
    def test_banner_slot_outside_the_scroll(self):
        js = read("setup.js")
        self.assertIn("D.bar, D.alerts, D.scroll, D.foot", js)            # between the bar and the scroll: always seen
        self.assertIn("D.alerts.replaceChildren(...alerts())", js)
        self.assertNotIn('note("warning", t("sys.noConn"), t("sys.noConnB")));\n    return out', js)
        css = read("qs.css")
        self.assertRegex(css, r"\.qs-alerts \{ flex: none;")
        self.assertIn(".qs-alerts:empty { display: none; }", css)

    def test_texts_say_what_happened(self):
        i18n = read("setup-i18n-ru.js")
        self.assertNotIn("проверьте интернет на этом устройстве", i18n)
        for k in ("sys.ipGone", "sys.ipGoneB", "sys.ipGoneGo", "dm.moveT", "dm.moveB", "dm.moveGo", "dm.moveStay",
                  "dm.moveWait", "dm.moveLate"):
            self.assertRegex(i18n, r'"%s":' % re.escape(k))
        self.assertNotIn("перестанет открывать панель", i18n)

    def test_a_record_rows_with_copy(self):
        # 3.3.3 (UX-333 §6): запись A — строки «поле · значение · Скопировать» (110px 1fr auto), на телефоне уже
        css = read("qs.css")
        self.assertRegex(css, r"(?m)^\.qs-arec__r \{[^}]*grid-template-columns: 110px minmax\(0, 1fr\) auto;")
        phone = css[css.index("@media (max-width: 767px)"):]
        self.assertIn(".qs-arec__r { grid-template-columns: 84px minmax(0, 1fr) auto;", phone)
        js = read("setup.js")
        self.assertIn('r[1] !== "—" ? copyBtn(r[1]) : h("span")', js)


if __name__ == "__main__":
    unittest.main()

"""F19b: what the button «Выйти» does on the page — the REAL ``logout``, ``wipePage`` of app.js (cut out by
tests/jsparts.py) run in node against stubs of the server's answers:

    * 200 / 401 — the login screen (stopTransport, resetData, showLogin), no message;
    * the node answered with an error — «Выйти не удалось — сеанс ещё открыт» and its text;
    * no answer (time is out, the network) — «Выход не подтверждён», WITHOUT the request's general text (it says
      «the change may have applied — data re-read», which logout never does) — SEC-ui1-f19, finding 2;
    * a second press while the first waits — no second request;
    * after sign-out nothing of the node stays in the page's sections, head, side panel or toasts (finding 8).

Skipped without node."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import jsparts

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def run(script):
    js = read("app.js")
    src = """
const log = [];
const t = (k) => k;
const toast = (title, desc, kind) => log.push(["toast", title, desc, kind]);
const stopTransport = () => log.push(["stop"]);
const resetData = () => log.push(["reset"]);
const showLogin = () => log.push(["login"]);
let answer = null;                       // () => Promise of API.logout
const API = { logout: () => { log.push(["request"]); return answer(); } };
class ApiError extends Error { constructor(m, status, code, data, body) { super(m); this.status = status; this.code = code; this.body = body || null; } }
function nodes(n) { return Array.from({ length: n }, () => ({ firstChild: {}, replaceChildren() { this.firstChild = null; } })); }
const page = { "#main > .gx-page": nodes(3), "#head": nodes(1), "#side": nodes(1), "#toastHost": nodes(1) };
const document = { querySelectorAll: (sel) => page[sel] || [] };
const tick = () => new Promise((r) => setTimeout(r, 0));
%s
%s
(async () => {
%s
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
""" % (jsparts.function(js, "logout").replace("function logout", "let loggingOut = false;\nfunction logout", 1),
       jsparts.function(js, "wipePage"), script)
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "t.js")
        with open(p, "w", encoding="utf-8") as f:
            f.write(src)
        res = subprocess.run(["node", p], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    if res.returncode != 0:
        raise AssertionError("node: %s" % res.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(res.stdout.decode("utf-8").strip().splitlines()[-1])


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class LogoutButtonTest(unittest.TestCase):
    def one(self, answer):
        return run("""
answer = %s;
logout();
await tick(); await tick();
console.log(JSON.stringify(log));
""" % answer)

    def test_ok_goes_to_the_login_screen(self):
        self.assertEqual(self.one("() => Promise.resolve({ ok: true })"), [["request"], ["stop"], ["reset"], ["login"]])

    def test_401_goes_to_the_login_screen(self):
        self.assertEqual(self.one('() => Promise.reject(new ApiError("нужен вход", 401, "auth"))'),
                         [["request"], ["stop"], ["reset"], ["login"]])

    def test_an_error_of_the_node_says_the_session_is_still_open(self):
        self.assertEqual(self.one('() => Promise.reject(new ApiError("текст узла", 500, null, null, '
                                  '{ ok: false, error: "текст узла" }))'),
                         [["request"], ["toast", "c.logoutFail", "текст узла", "danger"]])

    def test_an_answer_not_of_the_panel_says_not_confirmed(self):
        """SEC-f19b 3: nginx answers 502 (the panel is not running) or 504 (it did not answer) — no JSON body, the
        panel said nothing: «not confirmed», not «the session is still open»."""
        for status in (502, 504, 500):
            got = self.one('() => Promise.reject(new ApiError("Сервер ответил ошибкой %d", %d, null))' % (status, status))
            self.assertEqual(got, [["request"], ["toast", "c.logoutUnconfirmed", "", "danger"]], status)

    def test_no_answer_says_not_confirmed_without_the_general_text(self):
        for code in ("timeout", "net"):
            got = self.one('() => Promise.reject(new ApiError("err.timeoutChange", 0, "%s"))' % code)
            self.assertEqual(got, [["request"], ["toast", "c.logoutUnconfirmed", "", "danger"]], code)

    def test_a_second_press_sends_no_second_request(self):
        got = run("""
let release;
answer = () => new Promise((ok) => { release = ok; });
logout(); logout();
await tick();
const before = log.filter((x) => x[0] === "request").length;
release({ ok: true });
await tick(); await tick();
logout();                                 // after the answer: a press works again
await tick();
console.log(JSON.stringify({ before, after: log.filter((x) => x[0] === "request").length }));
""")
        self.assertEqual(got, {"before": 1, "after": 2})

    def test_wipe_leaves_nothing_of_the_node_in_the_page(self):
        got = run("""
wipePage();
console.log(JSON.stringify(Object.keys(page).map((k) => [k, page[k].every((n) => n.firstChild === null)])));
""")
        self.assertEqual(got, [[k, True] for k in ("#main > .gx-page", "#head", "#side", "#toastHost")])

    def test_show_login_forgets_and_wipes(self):
        """showLogin — the gate of «Выйти» and of every 401 (the second tab): the data in memory and in the page go."""
        src = jsparts.function(read("app.js"), "showLogin")
        head = src[:src.index('const host = $("#loginHost")')]
        self.assertIn("resetData();", head)
        self.assertIn("wipePage();", head)

    def test_texts_ru_en(self):
        for name in ("i18n-ru.js", "i18n-en.js"):
            src = read(name)
            self.assertIn('"c.logoutUnconfirmed":', src, name)
        self.assertIn("Выход не подтверждён", read("i18n-ru.js"))
        self.assertIn("Sign-out not confirmed", read("i18n-en.js"))


if __name__ == "__main__":
    unittest.main()

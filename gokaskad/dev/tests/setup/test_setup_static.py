"""The page of the quick setup and its mock: no inline code or styles (CSP of the panel), no outside addresses,
relative addresses only (the page lives under /setup-<secret>/), every route and step the page calls exists on the
server, every text key exists, every step of the server has an answer in the mock and the mock answers in the server's
shapes (product, rail, trace), the mock walks a long step to its end (node, when it is there)."""

import json
import os
import re
import shutil
import subprocess
import unittest

from tests import jsparts

from kaskad.panel import routes
from kaskad.setup import model, product, steps, web  # noqa: F401 — web registers the routes of /api/setup/*

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib", "kaskad",
                      "panel", "static", "setup")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def code(name):
    """The text of a script or a style without its comments (they name what is forbidden, to explain why)."""
    text = re.sub(r"/\*.*?\*/", "", read(name), flags=re.S)
    return re.sub(r"(?m)(^|\s)//\s.*$", r"\1", text)


def found(pattern, text):
    m = re.search(pattern, text)
    return text[max(0, m.start() - 60):m.end() + 60] if m else None


class PageTest(unittest.TestCase):
    def test_no_inline_code_and_relative_addresses(self):
        html = read("index.html")
        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)")
        self.assertNotIn("<style", html)
        self.assertNotRegex(html, r"\son[a-z]+=")
        self.assertIn('src="setup.js?v=__KASKAD_V__"', html)
        js = code("setup.js")
        self.assertIn('"api/setup/"', js)
        self.assertNotRegex(js, r"[\"']/api/")                 # never an absolute path: the secret prefix comes first
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("dev-mock-setup.js", html)            # the mock only by ?mock=1
        self.assertNotIn("dev-view-setup", html)
        # the mock never wakes up on the panel's address: not under /setup-<secret>/ and only on this computer
        self.assertRegex(js, r"const DEV = !/\\/setup-.*location\.hostname")
        self.assertIn("localhost|127", js)

    def test_no_inline_styles_and_no_outside_addresses(self):
        html = read("index.html")
        self.assertNotRegex(html, r"\sstyle=")
        for ref in re.findall(r'(?:src|href)="([^"]+)"', html):
            self.assertFalse(re.match(r"^(?:[a-z]+:|//|/)", ref) and not ref.startswith("data:,"), ref)
        for name in ("setup.js", "dev-mock-setup.js", "dev-view-setup.js", "setup-i18n-ru.js"):
            js = code(name)
            if name == "setup.js":
                # the one thing the browser keeps: the language of the page before the login (3.3.2) — only by
                # its key, only in these two functions; never a secret
                for fn in ("prefLang", "keepPrefLang"):
                    part = jsparts.function(js, fn)
                    self.assertEqual(set(re.findall(r"localStorage\.\w+\(([^,)]+)", part)), {"PRE_LANG"}, fn)
                    js = js.replace(part, "")
            for pat in (r"innerHTML|outerHTML|insertAdjacentHTML|document\.write",
                        r"setAttribute\(\s*[\"']style",
                        r"\.style\.(?!setProperty\(\"--qs-hue\")",          # CSSOM only for the hue on <html>
                        r"\.cssText",
                        r"(?:fetch|EventSource|XMLHttpRequest|import)\(\s*[\"'`](?:https?:)?//",
                        r"\.src\s*=\s*[\"'`](?:https?:)?//",
                        r"localStorage|sessionStorage|indexedDB",               # no secret is ever kept
                        r"console\.(?:log|info|debug|warn|error)"):           # nothing goes to the console
                self.assertIsNone(found(pat, js), "%s: %s" % (name, pat))
        for name in ("qs.css", "dev-view-setup.css"):
            css = code(name)
            self.assertIsNone(found(r"url\(\s*[\"']?(?:https?:)?//", css), name)
            self.assertNotIn("@import", css, name)

    def test_routes_the_page_calls_exist(self):
        js = code("setup.js") + code("dev-view-setup.js")
        api = set(a for a in re.findall(r'\bapi\("([a-z/?=0-9-]+)"', js) if not a.endswith("/"))
        names = set(re.findall(r'\bstep\("([a-z0-9-]+)"', js)) | set(p[5:] for p in api if p.startswith("step/"))
        api = set(p.split("?")[0] for p in api if not p.startswith("step/")) | {"events"}
        self.assertTrue({"session", "state", "login", "ipcert", "net", "job", "finish"} <= api, api)
        for p in sorted(api):
            for method in ("GET", "POST"):
                if routes.find(method, "/api/setup/" + p)[0] not in (None, "method"):
                    break
            else:
                self.fail("нет маршрута /api/setup/%s" % p)
        self.assertIn("stage", js)
        self.assertNotIn(routes.find("POST", "/api/setup/stage")[0], (None, "method"))
        self.assertNotIn(routes.find("POST", "/api/setup/step/scan")[0], (None, "method"))
        missing = sorted(n for n in names if n not in steps.STEPS)
        self.assertEqual(missing, [], "шагов нет на сервере: %s" % ", ".join(missing))
        # the page covers the steps of the stages (not finish: its own route; trace: the scheme left the wizard for the
        # first acquaintance with the panel, 3.3.2 №14 — the step stays for the CLI and the API; rules — the item
        # «Россия напрямую» of «Дополнительно» since R7)
        unused = sorted(set(steps.STEPS) - names - {"finish", "trace"})
        self.assertEqual(unused, [], "шаги без экрана: %s" % ", ".join(unused))

    def test_every_text_key_exists(self):
        js = code("setup.js")
        i18n = read("setup-i18n-ru.js")
        keys = set(re.findall(r'^\s*"([a-zA-Z0-9.]+)":', i18n, flags=re.M))
        used = set(re.findall(r'\btn?\("([a-zA-Z0-9.]+)"(?!\s*\+)', js))
        # keys built from a list: «head.<этап>», «det.<этап>» (only the rare details, DETAILS), «w.lv<N>», qr.for<вид>
        for k in ("welcome", "import", "protocols", "forward", "exits", "domain", "extras", "done", "doneClassic"):
            if k != "welcome":
                used |= {"head." + k, "head." + k + "Why"}
        dets = re.search(r"const DETAILS = \{(.*?)\};", js).group(1)
        for k in re.findall(r':\s*"([a-zA-Z]+)"', dets):
            used |= {"det." + k, "det." + k + "B"}
        used |= {"w.lv%d" % i for i in range(5)}
        used |= {"imp.p." + p for p in ("gateway", "exits", "forwards", "site", "bot", "mcp", "panel", "github")}
        used |= {"sys.used", "sys.usedB", "sys.unknown", "sys.unknownB"}
        missing = sorted(k for k in used if k not in keys)
        self.assertEqual(missing, [], "нет текстов: %s" % ", ".join(missing))
        # 3.3.2 №16: no dead texts — every key is used by the page (directly or by a list above)
        direct = set(re.findall(r'"([a-zA-Z0-9._]+)"', js))
        dead = sorted(k for k in keys if k not in used and k not in direct)
        self.assertEqual(dead, [], "лишние тексты: %s" % ", ".join(dead))

    def test_mock_answers_every_step(self):
        mock = read("dev-mock-setup.js")
        names = set(re.findall(r'^\s{4}"?([a-z0-9-]+)"?: function', mock, flags=re.M))
        missing = sorted(n for n in steps.STEPS if n not in names)
        self.assertEqual(missing, [], "в макете нет шагов: %s" % ", ".join(missing))
        long_server = sorted(n for n, s in steps.STEPS.items() if s.long)
        m = re.search(r"var LONG = \{(.*?)\};", mock, flags=re.S)
        long_mock = sorted(re.findall(r'"?([a-z0-9-]+)"?: 1', m.group(1)))
        self.assertEqual(long_server, long_mock)
        for flag, var in (("network", "NET"), ("secret", "SECRET")):
            m = re.search(r"var %s = \{(.*?)\};" % var, mock, flags=re.S)
            self.assertEqual(sorted(n for n, s in steps.STEPS.items() if getattr(s, flag)),
                             sorted(re.findall(r'"?([a-z0-9-]+)"?: 1', m.group(1))), var)

    @unittest.skipUnless(shutil.which("node"), "нет node")
    def test_mock_runs_a_long_step(self):
        script = r"""
globalThis.window = globalThis;
globalThis.location = new URL("http://127.0.0.1/setup/index.html?mock=1&lat=0");
require(process.argv[1]);
const M = window.KASKAD_SETUP_MOCK;
const post = (p, b) => M.fetch("api/setup/" + p, {method: "POST", headers: {"X-Kaskad-Admin": "1"}, body: JSON.stringify(b)});
(async () => {
  let r = await post("step/password", {password: "short"});
  if ((await r.json()).error.code !== "weak") throw "weak";
  r = await post("step/password", {password: "Correct-Horse-9"});
  if ((await r.json()).state !== "ok") throw "pw";
  r = await post("step/protocols", {kinds: ["awg2", "wg", "pptp"]});
  if (r.status !== 202) throw "202";
  const es = new M.EventSource("api/setup/events");
  es.addEventListener("job", e => {
    const j = JSON.parse(e.data);
    if (j.state === "done") { console.log(j.result.state, j.items.map(i => i.item + ":" + i.state).join(",")); process.exit(0); }
  });
})().catch(e => { console.error("FAIL", e); process.exit(1); });
"""
        p = subprocess.run(["node", "-e", script, os.path.join(STATIC, "dev-mock-setup.js")], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("partial", p.stdout)
        self.assertIn("module:ok", p.stdout)
        self.assertIn("pptp:err", p.stdout)

    @unittest.skipUnless(shutil.which("node"), "нет node")
    def test_scripts_parse(self):
        for name in ("setup.js", "dev-mock-setup.js", "dev-view-setup.js", "setup-i18n-ru.js"):
            r = subprocess.run(["node", "--check", os.path.join(STATIC, name)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
            self.assertEqual(r.returncode, 0, r.stderr)


NODE_MOCK = r"""
globalThis.window = globalThis;
globalThis.location = new URL("http://127.0.0.1/setup/index.html?mock=1&lat=0&pw=1&" + (process.argv[2] || ""));
require(process.argv[1]);
const M = window.KASKAD_SETUP_MOCK;
const get = (p) => M.fetch("api/setup/" + p, {}).then((r) => r.json());
const post = (p, b) => M.fetch("api/setup/" + p, {method: "POST", headers: {"X-Kaskad-Admin": "1"}, body: JSON.stringify(b)}).then((r) => r.json());
(async () => {
  const out = {product: await get("product"), state: await get("state"), trace: await post("step/trace", {target: "foreign"}),
               mode: await post("step/mode", {mode: "classic"})};
  out.classic = await get("state");
  console.log(JSON.stringify(out));
})().catch((e) => { console.error("FAIL", e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "нет node")
class MockShapesTest(unittest.TestCase):
    """The mock answers in the shapes of the server: the page tried on the mock works on the server."""

    @classmethod
    def setUpClass(cls):
        p = subprocess.run(["node", "-e", NODE_MOCK, os.path.join(STATIC, "dev-mock-setup.js"), "step=protocols"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        if p.returncode != 0:
            raise AssertionError(p.stderr)
        cls.out = json.loads(p.stdout)

    def test_mock_product_is_the_server_one(self):
        got = dict(self.out["product"])
        got.pop("ok")
        for mode in ("node",):
            want = json.loads(json.dumps(product.doc(mode)))
            for k in ("version",):
                got.pop(k, None)
                want.pop(k, None)
            # the partner addresses live in product.HOSTING only (OWNER-FEEDBACK №45): the mock has stand-ins
            for doc in (got, want):
                for x in doc["links"]["hosting"]:
                    self.assertTrue(x.pop("url").startswith("https://"))
                self.assertTrue(doc["links"].pop("domain_buy").startswith("https://"))   # AdminVPS from HOSTING (№47)
            self.assertEqual(got, want)

    def test_mock_state_and_rail_are_the_server_shape(self):
        doc = model.load()
        doc.update(password_set=True, current="protocols", stages={"import": "done"})
        want = model.public_view(doc)
        st = self.out["state"]
        self.assertTrue(set(want) <= set(st), set(want) - set(st))
        self.assertTrue({"ok", "job", "product"} <= set(st))
        self.assertEqual(st["rail"], json.loads(json.dumps(model.rail(doc))))
        doc.update(mode="classic", current="forward")
        self.assertEqual(self.out["classic"]["rail"], json.loads(json.dumps(model.rail(doc))))
        self.assertEqual(self.out["mode"]["data"]["rail"]["current"], "forward")

    def test_mock_trace_has_the_keys_of_the_server(self):
        # kaskad.gw.trace.trace: the answer and what steps.s_trace adds (target, host)
        keys = {"ok", "query", "who", "profile", "route", "match", "addresses", "resolve", "exit", "exits", "server",
                "verdict", "reason", "summary", "steps", "notes", "origin", "highlight", "target", "host"}
        data = self.out["trace"]["data"]
        self.assertTrue(keys <= set(data), keys - set(data))
        self.assertIn(data["verdict"], ("exit", "direct", "block"))
        self.assertTrue({"name", "inbound"} <= set(data["who"]))
        self.assertTrue({"via", "fail"} <= set(data["route"]))
        with open(os.path.join(os.path.dirname(STATIC), "..", "..", "gw", "trace.py"), encoding="utf-8") as f:
            trace_src = f.read()
        for k in ("query", "who", "profile", "route", "match", "addresses", "resolve", "exit", "exits", "server"):
            self.assertIn('"%s"' % k, trace_src)                  # the list above follows the module


if __name__ == "__main__":
    unittest.main()

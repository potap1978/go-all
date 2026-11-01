"""VLESS Reality как вход в панели: список входов app.js совпадает с ядром (model.INBOUND_KINDS), у каждого — значок,
короткое имя схемы и тексты; входы xray схемы (xrayIn) — ровно model.XRAY_KINDS; карточка зовёт маршруты
/api/gw/vless/* с подтверждением «Новые ключи» и «Всё равно взять» на отказ проверки; имя сайта маскировки читается
и из ссылки (vlHost); макет (?mock=1) отвечает на эти маршруты так же, как сервер."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from kaskad.gw import model
from tests import jsparts

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def run_node(script):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(script)
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if r.returncode != 0:
        raise AssertionError(r.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])


class VlessStaticTest(unittest.TestCase):
    def setUp(self):
        self.js = read("app.js")
        self.graph = read("graph.js")

    def test_kinds_are_the_cores(self):
        m = re.search(r"const KINDS = \[([^\]]*)\]", self.js)
        self.assertEqual(tuple(re.findall(r'"([^"]+)"', m.group(1))), model.INBOUND_KINDS)
        icons = re.search(r"const IB_ICON = \{(.*?)\};", self.js, flags=re.S).group(1)
        self.assertEqual(sorted(re.findall(r"(\w+): \"", icons)), sorted(model.INBOUND_KINDS))
        short = re.search(r"function shortKind\(k\) \{ var m = \{(.*?)\};", self.graph).group(1)
        self.assertEqual(sorted(re.findall(r"(\w+): '", short)), sorted(model.INBOUND_KINDS))

    def test_xray_inbounds_of_the_scheme(self):
        body = jsparts.function(self.graph, "xrayIn")
        self.assertEqual(sorted(re.findall(r"k === '(\w+)'", body)), sorted(model.XRAY_KINDS))
        # the inbound of the PATH, not the VLESS exits of the pool: no other place tests the inbound by name
        self.assertNotIn("P.inbound === 'socks5'", self.graph)
        self.assertNotIn("indexOf('socks5')", self.graph)

    def test_texts(self):
        i18n = read("i18n-ru.js")
        self.assertIn('"ibt.vless": "VLESS"', i18n)
        self.assertIn('"ibd.vless": "VLESS Reality: телефоны и компьютеры (v2RayTun, Hiddify, Streisand, v2rayN); '
                      'маскируется под чужой сайт. С доменом и сайтом узла — TLS на 443."', i18n)
        g = read("graph-i18n-ru.js")
        for key in ("graph.trace.newInbound", "graph.filter.help", "graph.focus.devViaSocks"):
            line = next(x for x in g.splitlines() if "'%s'" % key in x)
            self.assertIn("VLESS", line, key)

    def test_card_calls_and_confirmations(self):
        api = self.js[self.js.index("    vless: {"):]
        api = api[:api.index("    },")]
        for need in ('request("GET", "/api/gw/vless/masks")', 'request("POST", "/api/gw/vless/masks/check", {})',
                     'request("PUT", "/api/gw/vless/mask", b)', 'request("POST", "/api/gw/vless/rekey", {})',
                     'request("POST", "/api/gw/vless/mode", b)'):
            self.assertIn(need, api)
        rekey = jsparts.function(self.js, "vlessRekey")
        self.assertLess(rekey.index("confirmDialog("), rekey.index("API.vless.rekey()"))   # ask first
        mask = jsparts.function(self.js, "openVlessMask")
        self.assertIn("e.status !== 400 || e.code !== 2", mask)              # only the refusal of the check
        self.assertIn("check ? { site } : { site, check: false }", mask)      # «Всё равно взять»
        self.assertIn("API.vless.check()", mask)
        done = jsparts.function(self.js, "vlessDone")
        self.assertIn("reissueDialog(r.title, what, devs)", done)             # whom to give the links again
        self.assertIn("record: isObj(r.record) ? r.record : null", self.js)   # the card's data from GET inbounds


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js и макет не запустить")
class VlessNodeTest(unittest.TestCase):
    def test_host_of_a_cover_site(self):
        js = read("app.js")
        out = run_node("%s\n%s\nconsole.log(JSON.stringify(%s.map(vlHost)));" % (
            jsparts.const(js, "VL_HOST"), jsparts.const(js, "vlHost"),
            json.dumps(["www.Apple.com", "https://www.apple.com/ru/", "vk.com.", "ya.ru:443", "1.2.3.4", "localhost",
                        "bad_host.com", "", "a" * 64 + ".com"])))
        self.assertEqual(out, ["www.apple.com", "www.apple.com", "vk.com", "ya.ru", "", "", "", "", ""])

    def test_mock_answers_like_the_server(self):
        out = run_node(r"""
globalThis.window = globalThis;
globalThis.location = new URL("http://127.0.0.1/index.html?mock=1&lat=0");
require(%s);
const call = async (m, p, body) => { const r = await window.KASKAD_MOCK.fetch(p, { method: m, headers: { "X-Kaskad-Admin": "1" },
  body: body ? JSON.stringify(body) : undefined }); return [r.status, await r.json()]; };
(async () => {
  const out = {};
  out.ib = (await call("GET", "/api/gw/inbounds"))[1].inbounds.filter((r) => r.kind === "vless")[0];
  out.masks = (await call("GET", "/api/gw/vless/masks"))[1];
  out.checked = (await call("POST", "/api/gw/vless/masks/check", {}))[1];
  out.bad = await call("PUT", "/api/gw/vless/mask", { site: "ya.ru" });
  out.anyway = (await call("PUT", "/api/gw/vless/mask", { site: "ya.ru", check: false }))[1];
  out.rekey = (await call("POST", "/api/gw/vless/rekey", {}))[1];
  console.log(JSON.stringify(out));
  process.exit(0);
})();
""" % json.dumps(os.path.join(STATIC, "dev-mock.js")))
        rec = out["ib"]["record"]
        self.assertEqual(out["ib"]["options"], [])
        # запись протокола и вид режима ядра (gw.ops.vless_view, VLESS Ш3): причина, «исправить», рекомендация, согласие
        self.assertEqual(sorted(rec), ["consent", "country", "dest", "domain", "enabled", "fix", "forced", "fp", "legacy", "mask", "mask_check",
                                       "mask_list", "node_domain", "pending", "port", "pub", "reality_risks", "reason", "reason_text",
                                       "recommend", "security", "short_id", "sni", "want"])
        self.assertTrue(all(m["check"] is None for m in out["masks"]["masks"]))
        self.assertTrue(all(isinstance(m["check"], dict) for m in out["checked"]["masks"]))
        self.assertEqual([m["sni"] for m in out["masks"]["masks"] if m["current"]], [rec["sni"]])
        self.assertEqual((out["bad"][0], out["bad"][1]["code"]), (400, 2))
        self.assertIn("не подходит для маскировки", out["bad"][1]["error"])
        self.assertEqual((out["anyway"]["sni"], out["anyway"]["mask_check"]["ok"], out["anyway"]["changed"]), ("ya.ru", None, True))
        names = [r["name"] for r in out["anyway"]["reissue"]]
        self.assertIn("vless-g3", names)
        self.assertEqual(sorted(out["anyway"]["reissue"][0]), ["id", "name", "platform", "user", "user_name"])
        self.assertNotEqual(out["rekey"]["pub"], rec["pub"])
        self.assertEqual([r["name"] for r in out["rekey"]["reissue"]], names)


if __name__ == "__main__":
    unittest.main()

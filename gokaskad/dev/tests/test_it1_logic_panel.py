"""Круг 1 правок по видео владельца (27.10), пакет Л1 — логика панели без DOM: настоящие куски app.js и pages/*.js
в node (tests/jsparts.py). Сервер — tests/panel/test_it1_logic.py.

* П1: у каждого теста проверки (bench: suite, channel, score) — подпись RU/EN в «Истории» (не «load_retr»).
* П2: установленные протоколы стоят на своих местах, новый — в конец (ibOrderOf).
* П3: пояснение карточки, равное пояснению части, не повторяется (stNoRepeat); «Сейчас в этом поясе …» — только для
  выбранного, ещё не сохранённого пояса (tmModel).
* №8: «Сейчас» без значения — короткая причина вместо «—», полный текст сервера — подсказкой строки (diag.js).
* №39: «Вернуть на IP» — PUT /api/gw/host {to_ip}, окно говорит, кому нужны новые настройки (siteHostIp).
* №38: проверка сайта сама — при открытии страницы и после смены шаблона; свежая (≤ 5 мин) — из памяти.
Node-less: skipped."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from tests import jsparts

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def run(prelude, parts, script):
    src = prelude + "\n" + "\n".join(parts) + "\n(async () => {\n%s\n})().catch((e) => { console.log(JSON.stringify(" \
        "{crash: String(e && e.stack || e)})); });\n" % script
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.unlink(path)
    out = res.stdout.decode("utf-8").strip().splitlines()
    if res.returncode != 0 or not out:
        raise AssertionError("node: %s" % res.stderr.decode("utf-8", "replace")[-2000:])
    doc = json.loads(out[-1])
    if "crash" in doc:
        raise AssertionError(doc["crash"])
    return doc


STUB = r"""
const t = (k, v) => k + (v ? JSON.stringify(v) : "");
const tn = (k, n, v) => k + "#" + n + (v ? JSON.stringify(v) : "");
const DASH = "—";
const arr = (x) => (Array.isArray(x) ? x : []);
const isObj = (x) => !!x && typeof x === "object" && !Array.isArray(x);
const num = (x) => (typeof x === "number" && isFinite(x) ? x : null);
const known = (x) => x !== null && x !== undefined;
"""


class TestNamesTest(unittest.TestCase):
    """П1: «Load_retr», «Load_rtt» в карточке проверки — у каждого теста своя подпись (RU и EN)."""

    def test_every_test_of_a_check_has_a_name(self):
        from kaskad import score as S
        from kaskad.bench import channel, suite
        ids = set(suite.UNITS) | set(channel.TESTS) | set(S.tests_of(S.load_spec()))
        m = re.search(r"const TEST = \{(.*?)\};", read("pages/diag.js"), re.S)
        mapped = dict(re.findall(r'(\w+): "(dg\.test\.\w+)"', m.group(1)))
        self.assertEqual(sorted(ids - set(mapped)), [])                   # без правки: load_retr, load_rtt
        for lang in ("ru", "en"):
            words = dict(re.findall(r'"(dg\.test\.\w+)": "([^"]+)"', read("pages/diag-i18n-%s.js" % lang)))
            for tid in ids:
                self.assertTrue(words.get(mapped[tid]), (lang, tid))
                self.assertNotIn("_", words[mapped[tid]], (lang, tid))


@unittest.skipUnless(shutil.which("node"), "нет node")
class ProtocolOrderTest(unittest.TestCase):
    """П2: после установки OpenVPN не встаёт 4-м, а SOCKS5 не уезжает: новый — в конец, порядок помнит браузер."""

    def test_order(self):
        js = read("app.js")
        out = run(STUB, [jsparts.function(js, "ibOrderOf")], r"""
console.log(JSON.stringify({
  first: ibOrderOf(["wg", "awg2", "vless", "socks5"], null),
  added: ibOrderOf(["wg", "awg2", "vless", "openvpn", "socks5"], ["wg", "awg2", "vless", "socks5"]),
  gone: ibOrderOf(["wg", "vless", "openvpn"], ["wg", "awg2", "vless", "socks5", "openvpn"]),
  junk: ibOrderOf(["wg", "vless"], ["vless", 5, "vless", null, "wg"]),
}));
""")
        self.assertEqual(out["first"], ["wg", "awg2", "vless", "socks5"])
        self.assertEqual(out["added"], ["wg", "awg2", "vless", "socks5", "openvpn"])   # новый — в конец
        self.assertEqual(out["gone"], ["wg", "vless", "openvpn"])
        self.assertEqual(out["junk"], ["vless", "wg"])

    def test_both_lists_use_it(self):
        js = read("app.js")
        # Т3 (Б r3): телефон — тот же вид (ibGridView), своего списка нет
        self.assertIn("ibInstOrder(", jsparts.function(js, "ibGridView"))
        phone = js[js.index("PAGES.inbounds.phone = function"):]
        self.assertIn("PAGES.inbounds.build(root, n);", phone[:phone.index("};")])
        self.assertNotIn("function ibMlist", js)


@unittest.skipUnless(shutil.which("node"), "нет node")
class NoRepeatTest(unittest.TestCase):
    """П3: подзаголовок карточки «Шлюз» равен подзаголовку части — показывается один раз."""

    def test_the_card_line_equal_to_the_part_line_goes(self):
        js = read("app.js")
        out = run(STUB, [jsparts.function(js, "stNoRepeat")], r"""
const gone = [];
const node = (text) => ({ textContent: text, remove() { gone.push(text); } });
const part = { querySelectorAll: (sel) => (sel === ".gx-card__tt > .gx-desc" ? [node(" Адрес и сеть шлюза. "), node("Другое")] : []) };
stNoRepeat(part, "Адрес и сеть шлюза.");
stNoRepeat(part, "");
console.log(JSON.stringify({ gone }));
""")
        self.assertEqual(out["gone"], [" Адрес и сеть шлюза. "])

    def test_desktop_and_phone_use_it(self):
        js = read("app.js")
        self.assertEqual(js.count("stNoRepeat(part, ST_DESC[id]") + js.count("stNoRepeat(body, ST_DESC[id]"), 2)

    def test_time_hint_only_for_a_new_zone(self):
        js = read("app.js")
        parts = [jsparts.const(js, n) for n in ("tzf", "tmCount")] + [jsparts.function(js, n) for n in (
            "tmModel", "tmWall", "tmOffset", "tmOffText", "tmOsClock")]
        out = run(STUB + 'const LOCALE = "ru-RU";\nconst tmNow = () => 1761300000;\n', parts, r"""
tzf.doc = { tz: "Europe/Moscow", offset: 10800, zones: ["Europe/Moscow", "Asia/Tokyo"], os_tz: "UTC" };
tzf.base = { tz: "Europe/Moscow" }; tzf.draft = { tz: "Europe/Moscow" };
const same = tmModel();
tzf.draft = { tz: "Asia/Tokyo" };
const other = tmModel();
console.log(JSON.stringify({ same: same.hint, head: same.head, other: other.hint }));
""")
        self.assertEqual(out["same"], "")                                  # без правки: строка повторяла шапку
        self.assertTrue(out["head"].startswith("tm.nowAt"), out)
        self.assertTrue(out["other"].startswith("tm.zoneNow"), out)


@unittest.skipUnless(shutil.which("node"), "нет node")
class NowReasonTest(unittest.TestCase):
    """№8: «Отдача / Отклик / Дрожание: —» — вместо «—» причина сервера (why), полный текст — подсказкой строки."""

    def test_reason(self):
        js = read("pages/diag.js")
        parts = [jsparts.const(js, "WHY"), jsparts.const(js, "whyShort"), jsparts.const(js, "rowTitle"), jsparts.function(js, "rowOf")]
        out = run(STUB + "const markText = () => \"mark\";\n", parts, r"""
const doc = { links: [{ id: "up", source: "tests", fresh_raw: null, why: "failed", why_text: "Не измерено: сервер в России" },
  { id: "net_q", source: "probes", parts: [{ test: "rtt", fresh_raw: null, why: "probes", why_text: "Не измерено: пробы" },
    { test: "jitter", fresh_raw: 0.5, why: null, why_text: null }] },
  { id: "down", source: "tests", fresh_raw: 800, why: null }, { id: "loss", source: "probes", fresh_raw: null, why: "evil" }] };
const up = rowOf(doc, { id: "up", link: "up" }), rtt = rowOf(doc, { id: "rtt", link: "net_q", test: "rtt" });
const jit = rowOf(doc, { id: "jit", link: "net_q", test: "jitter" }), down = rowOf(doc, { id: "down", link: "down" });
const odd = rowOf(doc, { id: "loss", link: "loss" });
console.log(JSON.stringify({ up: whyShort(up), rtt: whyShort(rtt), jit: whyShort(jit), down: whyShort(down), odd: whyShort(odd),
  title: rowTitle(up, null, {}, 3), titleDown: rowTitle(down, null, {}, 3) }));
""")
        self.assertEqual(out["up"], "dg.why.failed")
        self.assertEqual(out["rtt"], "dg.why.probes")
        self.assertEqual((out["jit"], out["down"], out["odd"]), ("", "", ""))     # измерено / неизвестный код — без слов
        self.assertEqual(out["title"], "Не измерено: сервер в России. mark")
        self.assertEqual(out["titleDown"], "mark")

    def test_the_exact_reason_of_the_runner(self):
        """Л3 №8: узел в России — «в России не меряется», а не «ошибка замера»; выключено и нет точки — свои слова.
        Каждый код сервера (hostscore.WHY_CODES) — со своим словом панели."""
        js = read("pages/diag.js")
        parts = [jsparts.const(js, "WHY"), jsparts.const(js, "whyShort"), jsparts.const(js, "rowTitle"), jsparts.function(js, "rowOf")]
        out = run(STUB + "const markText = () => \"mark\";\n", parts, r"""
const one = (why) => whyShort(rowOf({ links: [{ id: "up", source: "tests", fresh_raw: null, why }] }, { id: "up", link: "up" }));
console.log(JSON.stringify({ ru: one("no_upload_ru"), off: one("excluded"), nt: one("no_target"), ni: one("no_iperf3"), failed: one("failed") }));
""")
        self.assertEqual(out, {"ru": "dg.why.inRu", "off": "dg.why.off", "nt": "dg.why.nopt", "ni": "dg.why.nopt", "failed": "dg.why.failed"})
        from kaskad.bench import hostscore as HS
        codes = set(re.findall(r"(\w+): \"dg\.why", jsparts.const(js, "WHY")))
        self.assertEqual(sorted(set(HS.WHY_CODES) - codes), [])

    def test_the_radar_knows_every_code(self):
        """Л3 №8: подсказка сектора и aria-label радара (ovb.js NDW) — слово на каждый код сервера, RU и EN."""
        from kaskad.bench import hostscore as HS
        js = read("pages/ovb.js")
        ndw = jsparts.const(js, "NDW")
        self.assertEqual(sorted(set(HS.WHY_CODES) - set(re.findall(r"(\w+): \"", ndw))), [])
        for lang in ("ru", "en"):
            words = set(re.findall(r'"(ovb\.ndw\.\w+)":', read("pages/ovb-i18n-%s.js" % lang)))
            self.assertEqual(sorted(set(re.findall(r': "(ovb\.ndw\.\w+)"', ndw)) - words), [], lang)

    def test_both_languages(self):
        js = read("pages/diag.js")
        keys = set(re.findall(r'"(dg\.why\.\w+)"', jsparts.const(js, "WHY")))
        for lang in ("ru", "en"):
            words = dict(re.findall(r'"(dg\.why\.\w+)": "([^"]+)"', read("pages/diag-i18n-%s.js" % lang)))
            self.assertEqual(sorted(keys - set(words)), [], lang)


@unittest.skipUnless(shutil.which("node"), "нет node")
class BackToIpTest(unittest.TestCase):
    """№39: «Подключать по домену» включили — «Вернуть на IP» в той же карточке, с предупреждением о настройках."""

    def scope(self):
        js = read("app.js")
        return [jsparts.function(js, n) for n in ("siteHostHit", "siteHostIp")]

    def test_the_flow(self):
        out = run(STUB + r"""
const calls = [];
const R = { people: { loaded: true, data: { users: [{ id: "u1", name: "Мама" }],
  devices: [{ id: "d1", name: "Телефон", user: "u1", access: ["ikev2"] }, { id: "d2", name: "Ноутбук", user: "u1", access: ["wg"] }] } } };
let SITE = { domain: "vpn.example.com", name: "vpn.example.com", use: { sstp: false } };
const rd = () => SITE;
const loadQuiet = async () => {};
const oneAtATime = (k, fn) => fn();
const siteNames = (rows) => rows.map((r) => r.name).join(", ");
let ANSWER = true, DIALOG = null;
const confirmDialog = async (o) => { DIALOG = o; return ANSWER; };
const siteRun = (k, b, fn) => fn();
const act = async (fn, o) => { calls.push(o.reload); return fn(); };
const API = { gw: { host: async (b) => { calls.push(b); return { ok: true, host: "203.0.113.10", auto: true }; } } };
let REISSUE = null;
const siteReissue = (rows, title, what) => { REISSUE = { rows, title, what }; };
""", self.scope(), r"""
ANSWER = false;
await siteHostIp();
const asked = DIALOG, noCall = calls.length;
ANSWER = true;
await siteHostIp();
console.log(JSON.stringify({ asked, noCall, calls, REISSUE }));
""")
        self.assertEqual(out["noCall"], 0)                                 # «Отмена» — ничего не меняется
        d = out["asked"]
        self.assertEqual((d["title"], d["okLabel"]), ("site.hostIpT", "site.hostIpOk"))
        self.assertEqual(d["list"][0], 'site.hostAffected#1{"names":"Телефон"}')    # IKEv2 — новые настройки
        self.assertEqual(d["list"][1:], ['site.hostIpKeep{"domain":"vpn.example.com"}', "site.hostIpDomain"])
        self.assertEqual(out["calls"][1], {"to_ip": True})
        self.assertIn("site", out["calls"][0])
        self.assertIn("gw", out["calls"][0])                               # оба места — «Шлюз» и «Домен и сайт»
        self.assertEqual(out["REISSUE"]["title"], "IKEv2")
        self.assertEqual(out["REISSUE"]["what"], 'site.hostIpWhat{"ip":"203.0.113.10"}')

    def test_the_card_has_the_button_only_by_domain(self):
        # круг 1 П4 (решение 9): «Адрес для клиентов» — в «Шлюзе» (gwAddr), «Вернуть на IP» — только в виде «по домену»
        card = jsparts.function(read("app.js"), "gwAddr")
        dom = card[card.index("if (v.byDom) {"):card.index("return box;")]
        self.assertIn('U.p(t("site.hostOn"', dom)
        self.assertIn('t("site.hostIpBtn"), "tertiary", siteHostIp', dom)
        self.assertEqual(card.count("siteHostIp"), 1)

    def test_texts(self):
        for lang in ("ru", "en"):
            words = dict(re.findall(r'"(site\.hostIp\w*)": "([^"]+)"', read("i18n-%s.js" % lang)))
            self.assertEqual(sorted(words), ["site.hostIpBtn", "site.hostIpDomain", "site.hostIpDone", "site.hostIpKeep",
                                             "site.hostIpOk", "site.hostIpT", "site.hostIpWhat", "site.hostIpX"], lang)
            self.assertIn("{domain}", words["site.hostIpKeep"])


@unittest.skipUnless(shutil.which("node"), "нет node")
class SiteAutoCheckTest(unittest.TestCase):
    """№38: проверка сайта сама — при открытии страницы и после смены шаблона; свежий результат (≤ 5 мин) — из памяти."""

    def scope(self):
        js = read("app.js")
        return [jsparts.const(js, "toDate"), jsparts.const(js, "SITE_CHECK_FRESH_S"),
                jsparts.function(js, "siteCheckStale"), jsparts.function(js, "siteAutoCheck")]

    def test_stale(self):
        out = run(STUB, self.scope(), r"""
const T0 = Date.parse("2026-10-27T12:00:00Z");
const c = { checked_at: "2026-10-27T12:00:00Z" };
console.log(JSON.stringify({
  never: siteCheckStale(null, null, T0),
  noAt: siteCheckStale({ verdict: "ok" }, null, T0),
  fresh: siteCheckStale(c, { deployedAt: "2026-10-27T11:00:00Z" }, T0 + 299000),
  old: siteCheckStale(c, null, T0 + 301000),
  newTpl: siteCheckStale(c, { deployedAt: "2026-10-27T12:00:30Z" }, T0 + 60000),
}));
""")
        self.assertEqual(out, {"never": True, "noAt": True, "fresh": False, "old": True, "newTpl": True})

    def test_auto(self):
        out = run(STUB + r"""
let siteAutoAt = 0;
const SB = { check: "" };
let SITE = null, calls = 0, loads = 0;
const rd = () => SITE;
const siteMaskOn = (s) => !!(s && s.mode === "site");
const oneAtATime = (k, fn) => fn();
const siteRun = (k, b, fn) => fn();
const loadQuiet = async () => { loads++; };
const API = { site: { check: async () => { calls++; const e = new Error("bad"); e.status = 200; throw e; } } };
""", self.scope(), r"""
const iso = (ms) => new Date(ms).toISOString();
const now = Date.now();
SITE = { mode: "off", site: { check: null } };
await siteAutoCheck(false);
const off = calls;                                                    // сайт выключен — не проверяем
SITE = { mode: "site", site: { check: { verdict: "ok", checked_at: iso(now - 60000) }, template: { deployedAt: iso(now - 3600000) } } };
await siteAutoCheck(false);
const fresh = calls;                                                  // минуту назад — из памяти
SITE.site.check.checked_at = iso(now - 600000);
await siteAutoCheck(false);
await new Promise((r) => setTimeout(r, 10));
const stale = calls;                                                  // 10 мин — проверяем сами
await siteAutoCheck(false);
const again = calls;                                                  // не удалась — не крутим по кругу
await siteAutoCheck(true);
await new Promise((r) => setTimeout(r, 10));
console.log(JSON.stringify({ off, fresh, stale, again, forced: calls, loads }));
""")
        self.assertEqual(out, {"off": 0, "fresh": 0, "stale": 1, "again": 1, "forced": 2, "loads": 2})

    def test_wired(self):
        js = read("app.js")
        self.assertIn("siteAutoCheck(false)", jsparts.function(js, "siteCheckCard"))       # открытие страницы
        self.assertIn("siteAutoCheck(true)", jsparts.function(js, "siteChooseTemplate"))   # новый шаблон


class ExitsHourNameTest(unittest.TestCase):
    """Л3 (стенд it1, кадр n2b-xh-1440): «Выходы · час» на Сводке 1440 — имя выхода резалось до «AmneziaWG 2.0 · …»
    (карточка 347 px). В макете Б1 имя читается целиком: строка та же (имя и состояние · линия 96 · цифра 74), длинное
    имя переносится в своей колонке, без многоточия."""

    def rule(self, sel):
        css = read("ovb.css")
        m = re.search(r"(?m)^" + re.escape(sel) + r" \{([^}]*)\}", css)
        self.assertIsNotNone(m, sel)
        return m.group(1)

    def test_the_name_is_whole(self):
        name = self.rule(".kb-xn__b > span:first-child")
        self.assertNotIn("ellipsis", name)                      # без правки: white-space: nowrap; text-overflow: ellipsis
        self.assertNotIn("nowrap", name)
        self.assertIn("overflow-wrap: anywhere", name)
        self.assertIn("grid-template-columns: minmax(0, 1fr) 96px 74px", self.rule(".kb-xn__r"))   # раскладка макета

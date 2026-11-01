"""UI-2 (пустоты «Протоколов» и «Обзора» на данных живого узла 3.3.7.3): сцена заглушки «как на узле» и правила, которые
легко потерять. Живая раскладка — tools/k3_layout_check.mjs (виды void и colspread, сцена scene=node3373, ширины до 2000)."""

import copy
import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import unittest

from tests import jsparts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
SCENE = os.path.join(STATIC, "dev-mock-scene-node3373.js")
_spec = importlib.util.spec_from_file_location("scene_scrub", os.path.join(ROOT, "tools", "scene_scrub.py"))
scrub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scrub)


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def scene():
    src = read("dev-mock-scene-node3373.js")
    m = re.search(r"\[\"node3373\"\] = (\{.*\});\s*$", src, re.S)
    assert m, "сцена не разобрана"
    return json.loads(m.group(1))


class SceneTest(unittest.TestCase):
    def test_scene_has_what_the_layout_needs(self):
        s = scene()
        for path in ("/api/overview", "/api/gw", "/api/gw/inbounds", "/api/gw/exits", "/api/gw/people", "/api/forwards", "/api/rules"):
            self.assertIn(path, s["get"])
        self.assertTrue(s["live"]["inbounds"])
        self.assertGreater(s["at"], 1.7e9)
        kinds = [x["kind"] for x in s["get"]["/api/gw/inbounds"]["inbounds"] if x.get("installed")]
        self.assertEqual(len(kinds), 6, kinds)                         # 6 протоколов, как на узле
        self.assertGreaterEqual(len(s["get"]["/api/overview"]["events"]), 8)   # «События» длиннее левой колонки

    def test_scene_has_no_secrets(self):
        """REV-ui2 С2: каждая сцена (dev-mock-scene-*.js) — ни одного адреса вне разрешённых диапазонов, ни одного домена
        вне example.com/.net/.org, ни одного UUID, кроме подделки, сертификат — условный (tools/scene_scrub.audit)."""
        scenes = sorted(glob.glob(os.path.join(STATIC, "dev-mock-scene-*.js")))
        self.assertIn(SCENE, scenes)
        for path in scenes:
            _, obj, src = scrub.load_scene(path)
            self.assertEqual(scrub.audit(obj), [], os.path.basename(path))
            self.assertNotIn("PRIVATE KEY", src)
            self.assertNotRegex(src, r"(?i)\"(password|token|psk|uuid|private_?key)\":\s*\"[^\"]{6,}")

    def test_audit_catches_a_spoiled_scene(self):
        """Порча → красная: в сцену подложены публичный адрес (в том числе 1.x–10.x — дыра круга 1), чужой домен, настоящий
        UUID, точный сертификат — audit() ловит каждое."""
        _, obj, _ = scrub.load_scene(SCENE)
        ib = obj["get"]["/api/gw/inbounds"]["inbounds"][0]
        spoils = [("addr", "vless 8.210.11.4:443"), ("addr", "5.42.77.123"), ("addr", "1.1.1.1"), ("addr", "2a01:4f8:c0c:1::1"),
                  ("host", "vpn.realnode.uk"), ("note", "см. https://github.com/x"), ("id", "3f2b8c1e-9a7d-4c2e-b1f0-6d5e4a3b2c1d"),
                  ("not_after", "2027-01-18T02:59:03Z"), ("issuer", "YE1"),
                  # REV-ui2-2 С1: адрес перед точкой, домен IDN и punycode, домен после «_», IPv6 после «слово:»
                  ("text", "Выход недоступен: 8.210.11.4."), ("why", "адрес 5.42.77.123. Проверьте"), ("domain_unicode", "мойузел.рф"),
                  ("domain", "xn--e1afmkfd.xn--p1ai"), ("note", "_x.example-real.net"), ("note", "_acme-challenge.mynode7.realnode.uk"),
                  ("note", "узел мойузел.рф."), ("dns", "ip:2a01:4f8:c0c:1::1"), ("dns", "[2a01:4f8::1]:443"), ("host", "bot.realnode.uk")]
        for key, val in spoils:
            o = copy.deepcopy(obj)
            o["get"]["/api/gw/inbounds"]["inbounds"][0] = dict(ib, **{key: val})
            self.assertNotEqual(scrub.audit(o), [], "порча не поймана: %s" % key)
        for ok in ("10.77.1.10", "192.168.1.1", "100.64.0.1", "198.18.0.1", "198.51.100.7", "203.0.113.5", "0.0.0.0", "fd00::1",
                   "gw_host.why.anchor", "gw_live.event.exit_recovered", "Kaskad 3.3.7.", "08:24:00", "т.е. и т.д.", "node.example.com."):
            o = copy.deepcopy(obj)
            o["get"]["/api/gw/inbounds"]["inbounds"][0] = dict(ib, addr=ok)
            self.assertEqual(scrub.audit(o), [], ok)

    def test_scrub_cleans_what_round_one_let_through(self):
        """Синтетика REV-ui2 С2: всё, что круг 1 пропускал как есть, после чистки заменено, audit() чист, исходных строк нет."""
        raw = {"inbounds": [{"kind": "vless", "where": "vless 8.210.11.4:443", "ip": "5.42.77.123", "id": "3f2b8c1e-9a7d-4c2e-b1f0-6d5e4a3b2c1d",
                             "short_ids": ["a1b2c3d4e5", "7f"], "short_id": "ab12", "path": "/s3cr3tPath", "pass": "hunter22",
                             "obfs_password": "obfsSecret1", "pbk": "Xk3jN9_pL2mQ7vR4tY8wZ1aB5cD6eF0gH2iJ3kL4mN5", "sid": "c0ffee",
                             "fingerprint": "AB:CD:EF:01:23:45", "not_after": "2027-01-18T02:59:03Z", "issuer": "YE1",
                             "host": "mynode7.realnode.uk", "version": "3.3.7.3-a7e1689", "checked_at": "2026-10-20T03:54:36Z",
                             "params": {"H1": "1234567-2345678", "S1": "37", "Jc": "4", "I1": "<b 0xdeadbeef01>"}}],
               "resolvers": ["1.1.1.1", "77.88.8.8"], "dns": "2a01:4f8:c0c:1::1", "email": "a.person@icloud.com", "chat_id": 987654321}
        sc = scrub.Scrub()
        out = sc.walk(raw)
        text = json.dumps(out, ensure_ascii=False)
        self.assertEqual(scrub.audit(out), [])
        self.assertEqual(sc.leaks(text), [])
        for orig in ("8.210.11.4", "5.42.77", "3f2b8c1e", "a1b2c3d4e5", "s3cr3tPath", "hunter22", "obfsSecret1", "Xk3jN9", "c0ffee",
                     "AB:CD:EF", "02:59:03", "YE1", "realnode", "mynode7", "1.1.1.1", "77.88.8.8", "2a01:", "icloud", "987654321",
                     "1234567-2345678", "deadbeef01", "3.3.7.3"):
            self.assertNotIn(orig, text)
        ib = out["inbounds"][0]
        self.assertTrue(scrub.FAKE_UUID_RE.match(ib["id"]))
        self.assertEqual((len(ib["short_ids"][0]), len(ib["short_ids"][1]), len(ib["short_id"])), (10, 2, 4))   # та же длина
        self.assertEqual((ib["not_after"], ib["issuer"], ib["version"], ib["checked_at"]),
                         ("2030-01-01T00:00:00Z", "Example CA", "3.3.7-a7e1689", "2026-10-20T03:54:00Z"))
        self.assertTrue(ib["host"].endswith("example.com") and ib["params"]["I1"].startswith("<b 0x"))

    def test_scrub_cleans_round_two_holes(self):
        """REV-ui2-2 С1: адрес в конце фразы, домены IDN/punycode и после «_», IPv6 после «слово:» — заменены, audit() и leaks()
        чисты; ключи текстов (gw_host.why.anchor) — не домены; подделка H1–H4 AWG — в пределах 0…2^32−1, начало ≤ конца."""
        raw = {"last_error": "Let's Encrypt выдал сертификат не на 8.210.11.4.", "why": "адрес 5.42.77.123. Проверьте",
               "domain_unicode": "мойузел.рф", "domain": "xn--e1afmkfd.xn--p1ai", "note": "_acme-challenge.mynode7.realnode.uk",
               "txt": "_x.example-real.net", "dns": "ip:2a01:4f8:c0c:1::1, [2a01:4f8::2]:443.", "msg": "gw_host.why.anchor",
               "awg": {"H1": "4254925457-5737734474", "H4": "4233044443-1633310241", "Jmin": "90", "Jmax": "10"}}
        sc = scrub.Scrub()
        out = sc.walk(raw)
        text = json.dumps(out, ensure_ascii=False)
        self.assertEqual(scrub.audit(out), [])
        self.assertEqual(sc.leaks(text), [])
        for orig in ("8.210.11.4", "5.42.77", "мойузел", "xn--", "realnode", "mynode7", "example-real", "2a01:"):
            self.assertNotIn(orig, text)
        self.assertEqual(out["msg"], "gw_host.why.anchor")
        self.assertTrue(out["last_error"].endswith("не на 203.0.113.10."), out["last_error"])
        hs = []
        for k in ("H1", "H4"):
            a, b = (int(v) for v in out["awg"][k].split("-"))
            self.assertTrue(5 <= a <= b <= 2 ** 32 - 1, (k, a, b))
            hs.append((a, b))
        self.assertLess(hs[0][1], hs[1][0])                      # диапазоны H не пересекаются
        for jmin, jmax in (("90", "10"), ("23", "34"), ("8", "80"), ("15", "150"), (40, 1000)):
            j = scrub.Scrub().walk({"Jmin": jmin, "Jmax": jmax})
            self.assertLessEqual(int(j["Jmin"]), int(j["Jmax"]), (jmin, jmax, j))

    def test_check_looks_at_the_file_head(self):
        """REV-ui2-2 П3: шапка файла сцены — IPv4, IPv6, домен и UUID вне правил ловятся."""
        for spoil in ("8.210.11.4", "2a01:4f8::1", "mynode7.realnode.uk", "3f2b8c1e-9a7d-4c2e-b1f0-6d5e4a3b2c1d"):
            self.assertNotEqual(scrub.head_audit("/* сцена с узла %s */\n" % spoil), [], spoil)
        self.assertEqual(scrub.head_audit(open(SCENE, encoding="utf-8").read().split("(window.KASKAD_MOCK_SCENES", 1)[0]), [])

    def test_scene_is_loaded_only_in_mock(self):
        app = read("app.js")
        self.assertIn('if (MOCK && /^[a-z0-9]{1,32}$/.test(scene)) { try { await loadScript("dev-mock-scene-" + scene + ".js"); await loadScript("dev-mock-scene.js"); }', app)
        self.assertNotIn("dev-mock-scene", read("index.html"))
        loader = read("dev-mock-scene.js")
        self.assertIn('if (method !== "GET" || !Object.prototype.hasOwnProperty.call(S.get, key)) { return base(url, init); }', loader)

    @unittest.skipUnless(shutil.which("node"), "нет node")
    def test_loader_answers_get_and_shifts_time(self):
        js = r"""
global.location = { search: "?mock=1&scene=node3373", href: "http://x/index.html?mock=1&scene=node3373" };
global.window = global; window.KASKAD_MOCK = { fetch: (u, i) => Promise.resolve("base"), EventSource: function () {} };
require(process.argv[1]); require(process.argv[2]);
const S = window.KASKAD_MOCK_SCENES.node3373;
(async () => {
  const r = await window.KASKAD_MOCK.fetch("/api/gw/inbounds", {});
  const j = await r.json();
  const other = await window.KASKAD_MOCK.fetch("/api/gw/inbounds", { method: "POST" });
  const ts = (await (await window.KASKAD_MOCK.fetch("/api/overview", {})).json()).live.ts;
  console.log(JSON.stringify([j.inbounds.length === S.get["/api/gw/inbounds"].inbounds.length, other === "base", Math.abs(ts - Date.now() / 1000) < 600]));
})();
"""
        out = subprocess.run(["node", "-e", js, os.path.join(STATIC, "dev-mock-scene-node3373.js"), os.path.join(STATIC, "dev-mock-scene.js")],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        self.assertEqual(out.stdout.decode().strip(), "[true,true,true]", out.stdout.decode())


class CardsAndOverviewTest(unittest.TestCase):
    def test_plaques_are_one_line_on_view(self):
        """Плашки (extraBody) и предупреждение — одна строка в ряду «Подробнее», целиком — внутри; «⋯» — в первой строке."""
        ux = read("ux.js")
        self.assertIn("var more = plaques.concat((o.extraMore || []).filter(Boolean));", ux)
        self.assertIn("body.filter(danger).forEach(function (n) { row2.appendChild(n); });", ux)     # тревога — на виду целиком
        self.assertNotIn("(o.extraBody || []).forEach(function (n) { if (n) { row2.appendChild(n); } });", ux)
        css = read("ux.css")
        self.assertIn(".ux-pc__btns { display: flex; flex-wrap: wrap; gap: 8px; min-width: 0; flex: 1 1 0; }", css)
        self.assertIn(".ux-pc__acts > .ux-menuw { align-self: flex-start; }", css)

    def test_overview_columns_and_tiles(self):
        app = read("app.js")
        self.assertIn('list.setAttribute("data-flow-fill", "");', app)
        self.assertIn("if (window.ResizeObserver) { const ro = new ResizeObserver(later);", app)   # подбор — в кадре (П1)
        self.assertIn(".gx-flow .gx-list__i[hidden] { display: none; }", read("components.css"))
        for f in ("i18n-ru.js", "i18n-en.js"):
            self.assertIn('"ov.eventsMore": ', read(f))
        # круг 1 Т1: «Обзор › Сводка» — вариант Б (часть ovb: «Сервер сейчас», трафик и выходы, здоровье и события);
        # карточек «Протоколы», «Выходы», «Пробросы» и плиток gx-kpi нет
        self.assertIn('return [ovbMount(el("div", { class: "kb" }), "summary")];', app)
        css = read("styles.css")
        self.assertIn(".kk-part__cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);", css)
        self.assertIn("@container kkpart (max-width: 719.98px) { .kk-part__cols { grid-template-columns: minmax(0, 1fr); } }", css)
        for gone in ("function gwFold", "function ovExits", "function ovInbounds", "function ovForwards", "gx-kpi__split"):
            self.assertNotIn(gone, app)

    def test_summary_tiles(self):
        """Было (nav2 r1, UI-A1, Н4): четыре плитки старой «Сводки» (ovTiles) — кнопки в свой раздел, неизвестное — «—»,
        «все выключены» — не «выходов нет». REV-it1 №4: старый «Обзор» снят как недостижимый, проверки по смыслу — на
        живой Сводке (часть ovb) в node: tests/panel/test_ovb_live (выходы, вердикт, плитки здоровья — кнопки в
        «Здоровье сервера»). Здесь — что старых плиток нет и что Сводка — часть ovb."""
        app = read("app.js")
        for gone in ("function ovTiles", "function ovAttCard", "function ovHost", "function ovMhost", "function ovMkpis",
                     "function ovAttention", "function ovMevents", "function hostHealthView", "kk-ov__tile"):
            self.assertNotIn(gone, app)
        self.assertNotIn("kk-ov__tile", read("styles.css"))
        self.assertIn('return [ovbMount(el("div", { class: "kb" }), "summary")];', app)
        here = os.path.dirname(os.path.abspath(__file__))
        self.assertTrue(os.path.isfile(os.path.join(here, "panel", "test_ovb_live.py")))

    def test_layout_check_has_the_scene(self):
        with open(os.path.join(ROOT, "tools", "k3_layout_check.mjs"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('const SCENE = "scene=node3373", SCENE_W = 2000;', src)
        self.assertIn("[SCENE]: [\"overview\", \"inbounds\"]", src)
        self.assertIn("out.push(['void'", src)
        self.assertIn("out.push(['colspread'", src)


FLOW_JS = r"""
// поток в две колонки: левая карточка — низ LEFT; правая — «События», низ = TOP + ROW * видимых строк (+ MORE с кнопкой)
const out = {};
let LEFT = 0, COLS = "2";
const TOP = 100, ROW = 50, MORE = 30;
const t = (k, o) => k + ":" + (o && o.n);
const fmtInt = (n) => String(n);
const document = { activeElement: null };
const getComputedStyle = (n) => ({ columnCount: COLS });
function node(cls, parent) {
  const n = { className: cls, children: [], hidden: false, parent: parent || null, dataset: {}, tabIndex: 0, isConnected: true,
    classList: { contains: (c) => (" " + n.className + " ").indexOf(" " + c + " ") >= 0 },
    get nextElementSibling() { const p = n.parent; return p ? p.children[p.children.indexOf(n) + 1] || null : null; },
    after(x) { const p = n.parent; x.parent = p; p.children.splice(p.children.indexOf(n) + 1, 0, x); },
    remove() { const p = n.parent; p.children.splice(p.children.indexOf(n), 1); n.parent = null; },
    replaceChildren(...k) { n.children = k; k.forEach((x) => { x.parent = n; }); },
    focus() { document.activeElement = n; },
    querySelector(sel) {
      const want = sel.replace(/\[data-flow-fill\]/, "@fill").split(/,\s*/).map((x) => x.replace(/^\./, ""));
      const walk = (x) => { for (const c of x.children) { if (want.some((w) => (" " + c.className + " ").indexOf(" " + w + " ") >= 0)) return c; const r = walk(c); if (r) return r; } return null; };
      return walk(n);
    } };
  if (parent) parent.children.push(n);
  return n;
}
const el = (tag, o) => node(o.class);
const U = { btn: (label, kind, fn) => { const b = node("btn"); b.label = label; b.click = fn; return b; } };
function scene(tones) {
  const box = node("gx-flow");
  const left = node("gx-flow__i", box);
  const right = node("gx-flow__i", box);
  const fill = node("gx-list @fill", right);
  const rows = tones.map((tone, i) => { const r = node("gx-list__i", fill); r.i = i; if (tone) node("gx-list__ic gx-list__ic--" + tone, r); return r; });
  left.getBoundingClientRect = () => ({ left: 0, bottom: LEFT, height: LEFT });
  right.getBoundingClientRect = () => { const vis = rows.filter((r) => !r.hidden).length, more = fill.nextElementSibling ? MORE : 0;
    return { left: 600, bottom: TOP + ROW * vis + more, height: 1 }; };
  return { box, fill, rows };
}
const state = (s) => ({ hidden: s.rows.filter((r) => r.hidden).map((r) => r.i), more: s.fill.nextElementSibling ? s.fill.nextElementSibling.children[0].label : null });
"""


@unittest.skipUnless(shutil.which("node"), "нет node")
class FlowFillBehaviourTest(unittest.TestCase):
    """REV-ui2 С1, П2, П4: поведение flowFill из app.js в node (а не наличие строк): красные и жёлтые строки «Событий» не
    прячутся никогда, кнопка «Показать ещё N» считает только спрятанные, после нажатия фокус — на первой раскрытой строке."""

    def run_js(self, body):
        app = read("app.js")
        src = FLOW_JS + "\n".join([jsparts.const(app, "FLOW_FILL_MIN"), jsparts.const(app, "flowLoud"), jsparts.function(app, "flowFill")]) \
            + "\n" + body + "\nconsole.log(JSON.stringify(out));"
        res = subprocess.run(["node", "-e", src], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr.decode("utf-8", "replace")[-2000:])
        return json.loads(res.stdout.decode().strip().splitlines()[-1])

    def test_alarm_at_the_end_stays_on_view(self):
        out = self.run_js(r"""
LEFT = TOP + ROW * 5;                                   // ровно — при 5 видимых строках из 10
let s = scene(["success", "", "success", "", "", "success", "", "", "", "danger"]);
flowFill(s.box); out.a = state(s);
s = scene(["danger", "danger", "warning", "danger", "", "danger", "warning", "success", "danger", "danger"]);
flowFill(s.box); out.b = state(s);                       // одна нейтральная — прячется только она
s = scene(["", "", "", "", "", "danger", "success", "", "", ""]);
flowFill(s.box); out.e = state(s);                       // REV-ui2-2 З1: «снова работает» (success) рядом с аварией — на виду
s = scene(["danger", "danger", "danger", "danger", "danger", "danger"]);
flowFill(s.box); out.c = state(s);                       // прятать нечего — кнопки нет
COLS = "1"; s = scene(["", "", "", "", "", "", "", "", "", ""]);
flowFill(s.box); out.d = state(s);                       // одна колонка — всё на виду
""")
        self.assertNotIn(9, out["a"]["hidden"])                          # красная последняя — на виду
        self.assertTrue(out["a"]["hidden"])
        self.assertEqual(out["a"]["more"], "ov.eventsMore:%d" % len(out["a"]["hidden"]))
        self.assertTrue(set(out["a"]["hidden"]) <= {1, 3, 4, 6, 7, 8})            # прячутся только строки без тона
        self.assertTrue(out["e"]["hidden"])
        self.assertTrue(set(out["e"]["hidden"]) <= {0, 1, 2, 3, 4, 7, 8, 9})
        self.assertEqual(out["b"], {"hidden": [4], "more": "ov.eventsMore:1"})
        self.assertEqual(out["c"], {"hidden": [], "more": None})
        self.assertEqual(out["d"], {"hidden": [], "more": None})

    def test_show_more_opens_all_and_keeps_focus(self):
        out = self.run_js(r"""
LEFT = TOP + ROW * 4;
const s = scene(["danger", "", "", "", "", "", "", ""]);
flowFill(s.box);
out.before = state(s);
const first = s.rows.find((r) => r.hidden);
s.fill.nextElementSibling.children[0].click();
out.after = state(s);
out.focus = document.activeElement === first;
out.tab = first.tabIndex;
""")
        self.assertTrue(out["before"]["hidden"])
        self.assertNotIn(0, out["before"]["hidden"])
        self.assertEqual(out["after"], {"hidden": [], "more": None})
        self.assertTrue(out["focus"])
        self.assertEqual(out["tab"], -1)


class EventToneTest(unittest.TestCase):
    """REV-ui2-2 С2: у каждого вида события ленты (live.FEED_KINDS) есть тон — evLook из app.js в node; FEED_KINDS сверен с
    теми, кто пишет события (live._event, collect.py, health.py ctx.ev без request, model.NOTIFY_PUSH) — новый вид без тона
    или вне списка роняет тест."""

    def emitted_kinds(self):
        from kaskad.gw import live, model
        gw = os.path.join(ROOT, "lib", "kaskad", "gw")
        src = {n: open(os.path.join(gw, n + ".py"), encoding="utf-8").read() for n in ("live", "collect", "health")}
        kinds = set(live.EXIT_STATE_KINDS) | set(live.GROUP_PREFIX_KINDS) | set(live.SAID_KINDS)
        kinds |= set(re.findall(r'_event\(\s*"([a-z_]+)"', src["live"]))
        kinds |= set(re.findall(r'(?:\.append\(|ev = )\{"kind": "([a-z_]+)"', src["collect"]))
        for m in re.finditer(r'ctx\.ev\("([a-z_]+)"((?:[^()]|\([^()]*\))*)\)', src["health"]):
            if "request=True" not in m.group(2):
                kinds.add(m.group(1))
        kinds |= {k for k in model.NOTIFY_PUSH if k != "coalesce"}
        return kinds

    def test_feed_kinds_cover_the_writers(self):
        from kaskad.gw import live
        kinds = self.emitted_kinds()
        self.assertGreaterEqual(len(kinds), 20)
        self.assertEqual(sorted(kinds - set(live.FEED_KINDS)), [])

    @unittest.skipUnless(shutil.which("node"), "нет node")
    def test_every_event_kind_has_a_tone(self):
        from kaskad.gw import live
        app = read("app.js")
        js = "\n".join(jsparts.const(app, n) for n in ("EV_ICON", "EV_LOOK", "HOST_LOOK", "evLook")) + r"""
const kinds = JSON.parse(process.argv[1]);
const out = {};
for (const k of kinds) out[k] = evLook({ kind: k });
out["host:crit"] = evLook({ kind: "host_cpu", key: "gw_host.event.raise", args: { level: "crit" } });
out["host:clear"] = evLook({ kind: "host_cpu", key: "gw_host.event.clear", args: {} });
out["other"] = evLook({ kind: "something_new" });
console.log(JSON.stringify(out));
"""
        res = subprocess.run(["node", "-e", js, json.dumps(list(live.FEED_KINDS))], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr.decode("utf-8", "replace")[-2000:])
        look = json.loads(res.stdout.decode())
        self.assertEqual(sorted(k for k in live.FEED_KINDS if not look[k][1]), [], "вид события без тона")
        want = {"danger": ("exit_dead", "inbound_down", "group_last_step", "xray_ru_open", "xray_rejected", "node_offline"),
                "warning": ("exit_degraded", "exit_quota", "exit_country_changed", "exit_failover", "exit_standby", "ru_guard",
                            "speedtest_budget", "exit_removed"),
                "success": ("exit_alive", "inbound_up", "exit_recovered", "group_restored", "node_online", "xray_ru_closed")}
        for tone, ks in want.items():
            for k in ks:
                self.assertEqual(look[k][1], tone, k)
        self.assertEqual((look["host:crit"][1], look["host:clear"][1], look["other"][1]), ("danger", "success", ""))
        have = set(re.findall(r'^\s*"([a-z0-9-]+)": \[[FES],', read("icons.js"), flags=re.M))
        self.assertEqual(sorted({v[0] for v in look.values()} - have), [], "значков нет в icons.js")


@unittest.skipUnless(shutil.which("node"), "нет node")
class WarnOrderBehaviourTest(unittest.TestCase):
    """REV-ui2 З2, П4: карточка протокола в node — главное предупреждение (warnLine, у Hysteria2 — UDP и РФ) на виду первым,
    плашки-предупреждения — за «+N», плашка danger — на виду целиком."""

    def test_hy2_udp_first(self):
        from tests.test_333d_ux import prelude
        from tests.test_332f_give import run_node
        out = run_node(prelude() + r"""
const plaque = (tone, text) => { const n = document.createElement("div"); n.className = "gx-pnote gx-pnote--" + tone; n.textContent = text; return n; };
const row = { kind: "hy2", title: "Hysteria2", engine: "x", desc: "d", enabled: true, devices: 1, sessions: 0, ports: ["443/udp"], running: true };
const mk = (body) => X.protocolCard(row, { lang: "ru", onToggle() {}, onPort() {}, onLog() {}, onRestart: () => Promise.resolve({}), onDelete: () => Promise.resolve({}), onOption() {},
  extraBody: body, extraMore: [], warnLine: ["UDP в РФ не проверен — держите рядом VLESS", "Сертификат самоподписанный"] });
const c = mk([plaque("warning", "Без домена — самоподписанный сертификат")]);
const r = { first: byCls(c, "ux-pc__warnT")[0].textContent, n: byCls(c, "ux-pc__warnN")[0].textContent,
  title: byCls(c, "ux-pc__warn")[0].getAttribute("title").split("\n") };
const d = mk([plaque("danger", "Сертификат не читается")]);
const desc = byCls(d, "ux-pc__desc")[0];
r.danger = desc.children.some((x) => x.classList && x.classList.contains("gx-pnote--danger"));
r.dfirst = byCls(d, "ux-pc__warnT")[0].textContent;
console.log(JSON.stringify(r));
""")
        self.assertEqual(out["first"], "UDP в РФ не проверен — держите рядом VLESS")
        self.assertEqual(out["n"], "+2")
        self.assertEqual(out["title"], ["UDP в РФ не проверен — держите рядом VLESS", "Сертификат самоподписанный", "Без домена — самоподписанный сертификат"])
        self.assertTrue(out["danger"])
        self.assertEqual(out["dfirst"], "UDP в РФ не проверен — держите рядом VLESS")

    def test_hy2_udp_note_once(self):
        """Решение владельца 20.10 (UI-2 круг 3, вместо З2 REV-ui2-2): у Hysteria2 про UDP и мобильные сети — одна нейтральная
        заметка внизу «Подробнее» (rec.note с сервера), без жёлтой строки, без фразы в описании и без второй плашки.
        На живой раскладке то же проверяет вид hy2udp в tools/k3_layout_check.mjs."""
        app = read("app.js")
        card = jsparts.function(app, "ibUxCard")
        self.assertNotIn("warns.unshift", card)
        self.assertNotIn("hy2Warn", app)
        info = jsparts.function(app, "hy2Info")
        self.assertIn('w !== String(rec.note || "")', info)                  # заметка не повторяется плашкой
        kv, note = info.index("out.push(U.kv(rows, { stack: true }));"), info.index('if (rec.note) out.push(el("div", { class: "gx-pnote", "data-note": "hy2-udp" }')
        self.assertLess(kv, note)                                              # внизу, после параметров
        self.assertNotIn("gx-pnote--warning", info[note:])
        for name in ("i18n-ru.js", "i18n-en.js"):
            src = read(name)
            self.assertNotIn('"ib.hy2Warn"', src)
            self.assertNotIn("UDP", re.search(r'"ibd\.hy2": "([^"]+)"', src).group(1))
        cat = {lang: json.load(open(os.path.join(ROOT, "lib", "kaskad", "i18n", "catalog", lang, "gw_in_hy2.json"), encoding="utf-8"))["gw_in_hy2.udp_note"]
               for lang in ("ru", "en")}
        self.assertEqual(cat["ru"], "Hysteria2 работает по UDP (QUIC). Если в мобильной сети не подключается — используйте VLESS.")
        self.assertEqual(cat["en"], "Hysteria2 runs over UDP (QUIC). If it does not connect on a mobile network, use VLESS.")
        with open(os.path.join(ROOT, "lib", "kaskad", "gw", "ops.py"), encoding="utf-8") as f:
            ops = f.read()
        self.assertIn('out["warnings"] = [tr("gw_in_hy2.warn.self_cert")] if mode == "self" else []', ops)
        self.assertIn('out["note"] = tr("gw_in_hy2.udp_note")', ops)
        with open(os.path.join(ROOT, "lib", "kaskad", "panel", "api.py"), encoding="utf-8") as f:
            self.assertIn('"ports_view", "warnings", "note"))', f.read())
        for path in ("/api/gw", "/api/gw/inbounds"):                           # сцена — в форме ответа 3.3.7.4
            rec = [x for x in scene()["get"][path]["inbounds"] if x["kind"] == "hy2"][0]["record"]
            self.assertEqual(rec["note"], cat["ru"])
            self.assertFalse([w for w in rec["warnings"] if "UDP" in w])
        self.assertIn("['hy2udp'", open(os.path.join(ROOT, "tools", "k3_layout_check.mjs"), encoding="utf-8").read())

if __name__ == "__main__":
    unittest.main()

"""Карточка VLESS на «Протоколах» (3.3.x, VLESS Ш4 — SPEC-vless-tls-default §4, макет vless-tls r1): главное действие —
кнопкой, остальное — меню «⋯» (HANDOFF §12), удаление — последним опасным пунктом; меню — общий доступный компонент
U.menu / openMenu. Режим — в имени («VLESS · TLS» / «VLESS · Reality»), причина Reality — третьей строкой; раскрытая —
«Reality — причина» с «исправить» или плашка с ценой, всегда «Маскировка VLESS»; «Режима по сайту узла» нет. Окно
«Маскировка VLESS», согласие r1a/r1b (по умолчанию «Назад»), цена Ц1–Ц4, телефон. Макет (?mock=1&vless=…) — все
причины и отказы ядра."""

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


def run_node(script):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(script)
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if r.returncode != 0:
        raise AssertionError(r.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])


def i18n_vals():
    return dict(re.findall(r'^\s*"([A-Za-z0-9_.]+)":\s*"(.*)",?$', read("i18n-ru.js"), flags=re.M))


class MenuComponentTest(unittest.TestCase):
    """Меню «⋯» доступно: кнопка с подписью и состоянием, список role=menu, клавиатура, закрытие и возврат фокуса."""

    def setUp(self):
        self.js = read("app.js")

    def test_button(self):
        m = self.js[self.js.index("    menu(items, o = {}) {"):]
        m = m[:m.index("\n    },\n") + 7]
        for need in ('icon: "ellipsis"', 'tip: o.tip || t("c.more")', 'b.setAttribute("aria-haspopup", "menu")',
                     'b.setAttribute("aria-expanded", "false")', "openMenu(b, items, o)", '"ArrowDown"', '"ArrowUp"'):
            self.assertIn(need, m)
        self.assertEqual(i18n_vals()["c.more"], "Ещё действия")
        # U.btn без текста: tip становится aria-label и title кнопки-значка
        btn = self.js[self.js.index("    btn(label, kind, onClick, o = {}) {"):]
        self.assertIn('"aria-label": o.aria || (label ? null : (o.tip || null))', btn[:btn.index("\n    },\n")])

    def test_list(self):
        f = jsparts.function(self.js, "openMenu")
        for need in ('role: "menu"', 'role: "menuitem"', 'tabindex: "-1"', 'role: "separator"', 'anchor.setAttribute("aria-expanded", "true")',
                     'anchor.setAttribute("aria-expanded", "false")', "pushLayer(entry)", "dropLayer(entry)", "restoreFocus(anchor)",
                     '"ArrowDown"', '"ArrowUp"', '"Home"', '"End"', '"Tab"', 'bd.addEventListener("click"',
                     'window.addEventListener("scroll", onMove, true)', 'window.addEventListener("resize", onMove)', 'box.style.left = "0px"',
                     "go(o.last ? -1 : 0)", "U.busyWhile(anchor, it.on())", "gx-menu__item--danger"):
            self.assertIn(need, f)
        # фокус возвращается на «⋯» ДО действия: окно действия запомнит её и вернёт фокус туда же
        self.assertLess(f.index("close(null);"), f.index("U.busyWhile(anchor, it.on())"))
        # Esc — общий обработчик слоёв: верхний слой (меню) закрывается
        self.assertIn('if (ev.key === "Escape" && closeTopLayer()) ev.preventDefault();', self.js)
        # перерисованная карточка: меню старой кнопки закрывается
        self.assertIn("menuEntry.anchor.isConnected", jsparts.function(self.js, "menuSync"))
        self.assertIn("menuSync();", jsparts.function(self.js, "refreshNow"))

    def test_css(self):
        css = read("components.css")
        for need in (".gx-menu {", ".gx-menu-bd {", ".gx-menu__item {", ".gx-menu__item--danger", ".gx-menu__sep {",
                     '.gx-menu-btn[aria-expanded="true"]', '[data-device="phone"] .gx-menu__item { height: 44px'):
            self.assertIn(need, css)
        self.assertIn(".gx-pbtns .gx-pmenu { flex: 0 0 auto; }", read("styles.css"))


STUBS = r"""
const t = (k, v) => k + (v ? JSON.stringify(v) : "");
const tn = (k, n, v) => k + "#" + n + (v ? JSON.stringify(v) : "");
const isObj = (x) => !!x && typeof x === "object" && !Array.isArray(x);
const arr = (x) => (Array.isArray(x) ? x : []);
const DASH = "—";
const FOCUS = [], DIALOGS = [], PICK = [], CALLS = [], ANSWERS = [], TOASTS = [], ASKS = [], ASKQ = [];
const withBusy = async (b, f) => f();
const setTimeout = (f) => f();
const isPhone = () => false;
const mkNode = (tag, attrs, kids) => {
  const n = { el: tag, cls: (attrs && attrs.class) || "", text: attrs && attrs.text, kids: kids.flat().filter(Boolean), isConnected: true };
  n.classList = { add: (c) => { n.cls += " " + c; }, contains: (c) => n.cls.split(" ").indexOf(c) >= 0 };
  n.append = (...k) => { n.kids.push(...k.flat().filter(Boolean)); };
  n.setAttribute = () => 0;
  return n;
};
const el = (tag, attrs, ...kids) => mkNode(tag, attrs, kids);
const txt = (s) => ({ text: s });
const U = {
  btn(label, kind, on, o) { const b = { btn: label, kind, on, cls: [], isConnected: true }; b.classList = { add: (c) => b.cls.push(c) }; b.focus = () => FOCUS.push(label); return b; },
  menu(items, o) { return { menu: items.map((i) => ({ label: i.label, danger: !!i.danger, sep: !!i.sep, icon: i.icon })), cls: o.cls }; },
  kv(rows) { return { kv: rows.filter(Boolean).map((r) => ({ k: r.k, v: r.v, s: r.s || "", chips: (r.chips || []).filter(Boolean).map((c) => c.chip) })) }; },
  chip(label, tone, o) { return { chip: label, tone: tone || "", tip: o && o.tip }; },
  trash(name, on) { return { trash: name }; },
  askDelete() { return 0; },
};
const icon = (name) => ({ icon: name });
const fmtInt = String, fmtTs = (x) => "ts:" + x, fmtDay = (x) => "day:" + x;
const R = { people: { loaded: false }, site: { loaded: false }, inbounds: { loaded: false } };
const location = { hash: "" };
const toast = (a, b, c) => TOASTS.push([a, c]);
function openDialog(o) {
  const d = { o, foot: null, closed: false, box: { classList: { add() {} } } };
  d.setFoot = (b) => { d.foot = b; const want = PICK.shift(); if (want !== undefined) Promise.resolve().then(() => b[want].on()); };
  d.close = () => { if (!d.closed) { d.closed = true; if (o.onClose) o.onClose(null); } };
  DIALOGS.push(d);
  return d;
}
const err409 = (data) => Object.assign(new Error("409"), { status: 409, data });
const act = async (fn, o) => { try { return await fn(); } catch (e) { if (o && o.onError && (await o.onError(e))) return null; throw e; } };
const API = { inbounds: { uninstall: () => 0 }, vless: { mode: async (b) => { CALLS.push(b); const a = ANSWERS.shift(); if (a instanceof Error) throw a; return a; },
  // T-3378 Н-2: чтение «что спросит действие»; не задано в тесте — ошибка чтения (действие спросит отказом, как раньше)
  ask: async (q) => { ASKQ.push(q); const a = ASKS.shift(); if (a === undefined) throw new Error("no ask"); if (a instanceof Error) throw a; return a; },
  check: async () => ({ ok: true, masks: [{ sni: "own.example", mask: "own", check: { ok: true } }, { sni: "bad.example", mask: "list", check: { ok: false } }, { sni: "good.example", mask: "list", check: { ok: true } }] }) } };
const ALL_GW = [];
const toggleInbound = () => 0, psOpen = () => 0, openVlessMask = () => 0, vlessRekey = () => 0, hy2Menu = () => [];
const vlToTls = () => 0, vlReality = () => 0, vlSiteOn = () => 0;
const shape = (nodes) => nodes.map((n) => n.menu ? { menu: n.menu, cls: n.cls } : n.trash ? { trash: n.trash } : n.el ? { el: n.cls } : { btn: n.btn, kind: n.kind, cls: n.cls });
// узел → дерево классов и текстов (для сравнения в Python)
const flat = (n) => !n ? null : n.btn !== undefined ? { btn: n.btn, kind: n.kind } : n.chip !== undefined ? { chip: n.chip, tone: n.tone } : n.kv ? { kv: n.kv }
  : n.el ? Object.assign({ cls: n.cls.trim() }, n.text ? { text: n.text } : {}, n.kids.length ? { kids: n.kids.map(flat) } : {}) : n.text !== undefined ? { text: n.text } : n.icon ? { icon: n.icon } : n;
"""

APP_CONSTS = ("FIXED_PORT_KINDS", "psCan", "IB_MENU_FROM", "vlSec", "VL_LIST", "VL_MASK", "VL_REASONS", "VL_REASON_T", "VL_REC", "VL_NO_TLS", "vlRec", "vlDomain",
              "vlPanel443", "vlTitle", "vlNames", "vlDevName", "vlPendingText", "vlRow", "VL_FIX", "vlCurSite", "vlPriceOf",
              "vlPriceRead")
APP_FUNCS = ("ibActs", "vlListText", "vlCheckChip", "vlTileChip", "vlPeopleNames", "vlReasonText", "vlHint", "vlMenu", "vlFixRow",
             "vlRecPlate", "vlTop", "vlessInfo", "vlMore", "vlRun", "vlSitePriceOpts", "vlSiteAnswer", "vlPriceDialog", "vlConsentDialog",
             "vlAsk", "vlSiteDom")


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class CardLogicTest(unittest.TestCase):
    """VLESS Ш4 (SPEC-vless-tls-default §4, макет vless-tls r1): карточка по записи ядра (vless_view) — режим в имени,
    причина третьей строкой, в раскрытой — «Reality — причина» с «исправить» или плашка с ценой, всегда «Маскировка
    VLESS»; меню «⋯» без «Режима по сайту узла»."""

    def setUp(self):
        js = read("app.js")
        self.parts = "\n".join([STUBS] + [jsparts.const(js, n) for n in APP_CONSTS] + [jsparts.function(js, n) for n in APP_FUNCS])

    def run_js(self, body):
        return run_node(self.parts + "\n(async () => {\n" + body + "\n})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });")

    def test_actions(self):
        out = self.run_js(r"""
const rec = (sec) => (sec === undefined ? { sni: "vk.com" } : { security: sec, domain: "vpn.example.net" });
const r = (kind, record, enabled) => ({ kind, title: kind, enabled: enabled !== false, installed: true, record });
console.log(JSON.stringify({
  reality: shape(ibActs(r("vless", rec("reality")))), old: shape(ibActs(r("vless", rec()))), noRec: shape(ibActs(r("vless", null))),
  tls: shape(ibActs(r("vless", rec("tls"), false))), wg: shape(ibActs(r("wg", null))), l2tp: shape(ibActs(r("l2tp", null))),
}));
""")
        mask = {"label": "vl.menuMask", "danger": False, "sep": False, "icon": "globe"}
        # 3.3.7.15 (№13 r2): «Настройки» — порт, MTU, TLS одним окном (бывший «Порт»)
        sett = {"label": "ps.settings", "danger": False, "sep": False, "icon": "sliders"}
        menu = {"menu": [sett, mask, {"label": "vl.menuRekey", "danger": False, "sep": False, "icon": "key"}], "cls": "gx-pmenu"}
        main = {"btn": "ib.disable", "kind": "tertiary", "cls": []}
        tail = lambda title: [{"el": "gx-spacer"}, {"trash": title}]                  # noqa: E731
        # Reality: «Маскировка VLESS…» и «Новый ключ Reality…» в «⋯»; «Режима по сайту узла» (vl.syncBtn) нет
        self.assertEqual(out["reality"], [main, menu] + tail("vless"))
        self.assertEqual(out["old"], out["reality"])            # поля security нет (старое ядро) — Reality
        self.assertEqual(out["noRec"], out["reality"])
        # TLS: порт — всегда 443 перед сайтом, ключей Reality нет; «Настройки» — версии TLS (F4); «Маскировка VLESS…» —
        # и здесь (вернуться на Reality)
        self.assertEqual(out["tls"], [{"btn": "ib.enable", "kind": "tertiary", "cls": []}, {"menu": [sett, mask], "cls": "gx-pmenu"}] + tail("vless"))
        self.assertEqual(out["wg"], [main, {"btn": "ps.settings", "kind": "ghost", "cls": []}] + tail("wg"))
        self.assertEqual(out["l2tp"], [main] + tail("l2tp"))
        self.assertNotIn("vl.syncBtn", read("app.js"))
        self.assertNotIn("vl.syncBtn", i18n_vals())

    # записи ядра по причинам (§3.1, таблица «Причина → карточка» HANDOFF-vless)
    RECS = r"""
const base = { security: "reality", sni: "www.microsoft.com", mask: "list", node_domain: "vpn.example.net", port: 4443 };
const R0 = {
  tls: { security: "tls", domain: "vpn.example.net", node_domain: "vpn.example.net", port: 443 },
  no_domain: Object.assign({}, base, { node_domain: "", reason: "no_domain", forced: true, fix: { do: "domain" } }),
  no_cert: Object.assign({}, base, { reason: "no_cert", forced: true, fix: { do: "cert" } }),
  busy: Object.assign({}, base, { reason: "busy_443", forced: true, reason_text: "goAmnezia", fix: null }),
  panel: Object.assign({}, base, { reason: "busy_443", forced: true, fix: { do: "panel_port" } }),
  no_site: Object.assign({}, base, { reason: "no_site", forced: true, fix: { do: "site_on", text: "Включите сайт" } }),
  failed: Object.assign({}, base, { reason: "tls_failed", forced: true, tls_error: { why: "xray упал" }, fix: { do: "mode_tls" } }),
  ready: Object.assign({}, base, { reason: "ready", recommend: { mode: "tls", devices: 2 } }),
  legacy: Object.assign({}, base, { reason: "legacy", recommend: { mode: "tls", devices: 2 } }),
  consent_bad: Object.assign({}, base, { reason: "consent_bad", recommend: { mode: "tls", devices: 2 } }),
  chosen: Object.assign({}, base, { reason: "chosen", consent: { at: "2026-10-20T10:00:00Z", ok: true }, fix: { do: "mode_tls" } }),
  pending: Object.assign({}, base, { reason: "ready", pending: { at: "A", busy: false } }),
};
const row = (rec) => ({ kind: "vless", title: "VLESS", record: rec, devices: 2, ports: ["4443/tcp"] });
"""

    def test_hint_third_line(self):
        out = self.run_js(self.RECS + r"""
const o = {}; for (const k of Object.keys(R0)) o[k] = vlHint(row(R0[k]));
o.title = [vlTitle(row(R0.tls)), vlTitle(row(R0.legacy)), vlTitle({ title: "VLESS", record: null })];
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["title"], ["VLESS · TLS", "VLESS · Reality", "VLESS · Reality"])     # режим — в имени
        self.assertIsNone(out["tls"])                                                             # у TLS третьей строки нет
        self.assertEqual(out["no_domain"], {"text": "vl.reason.no_domain", "rec": False})
        self.assertEqual(out["no_cert"], {"text": "vl.reason.no_cert", "rec": False})
        self.assertEqual(out["busy"], {"text": 'vl.reason.busy_443{"who":"goAmnezia"}', "rec": False})
        self.assertEqual(out["panel"], {"text": "vl.reason.panel_443", "rec": False})
        self.assertEqual(out["no_site"], {"text": "vl.reason.no_site", "rec": False})
        self.assertEqual(out["failed"], {"text": 'vl.reason.tls_failed{"why":"xray упал"}', "rec": False})
        self.assertEqual(out["chosen"], {"text": 'vl.reason.chosen{"date":"day:2026-10-20T10:00:00Z"}', "rec": False})
        for k in ("ready", "legacy", "consent_bad"):                                              # рекомендация — акцентом
            self.assertEqual(out[k], {"text": "vl.reason." + k, "rec": True})
        self.assertEqual(out["pending"], {"text": 'vl.pending{"at":"ts:A"}', "rec": False})

    def test_expanded(self):
        out = self.run_js(self.RECS + r"""
const o = {};
for (const k of Object.keys(R0)) { const info = vlessInfo(row(R0[k])); o[k] = info.map(flat); }
o.phone = vlessInfo(row(R0.legacy), true).map(flat);
console.log(JSON.stringify(o));
""")
        side = lambda k: [x for x in out[k] if x.get("cls") == "kk-vside"][0]["kids"]          # noqa: E731
        btns = lambda node: [b["btn"] for b in json.loads(json.dumps(node), object_hook=lambda d: d).get("kids", [])[-1].get("kids", []) if "btn" in b]  # noqa: E731
        # режим — первой строкой раскрытой
        self.assertEqual(out["tls"][0]["kv"][0]["v"], 'vl.modeTls{"domain":"vpn.example.net"}')
        self.assertEqual(out["legacy"][0]["kv"][0]["v"], "Reality · www.microsoft.com")
        # TLS: справа только «Маскировка VLESS» с «Маскировка…»
        self.assertEqual(len(side("tls")), 1)
        self.assertEqual(side("tls")[0]["cls"], "kk-vrow")
        self.assertEqual(btns(side("tls")[0]), ["vl.maskBtn"])
        # рекомендация (legacy, ready, consent_bad): плашка с ценой и двумя кнопками, потом «Маскировка VLESS»
        for k, keep in (("legacy", "vl.keepReality"), ("ready", "vl.keepReality"), ("consent_bad", "vl.confirmReality")):
            plate = side(k)[0]
            self.assertEqual(plate["cls"], "gx-vrec", k)
            self.assertEqual(plate["kids"][0]["kids"][1]["text"], " vl.recPrice#2" + '{"list":""}')
            self.assertEqual([b["btn"] for b in plate["kids"][1]["kids"]], ["vl.toTls", keep])
            self.assertEqual(side(k)[1]["cls"], "kk-vrow")
        # вынужденный Reality и chosen: строка «Reality — причина» с «исправить»
        fix = {"no_domain": "vl.fix.domain", "no_cert": "vl.fix.cert", "panel": "vl.fix.panel", "no_site": "vl.fix.siteOn", "failed": "vl.fix.retry", "chosen": "vl.toTls"}
        for k, label in fix.items():
            self.assertEqual(side(k)[0]["cls"], "kk-vrow", k)
            self.assertEqual(btns(side(k)[0]), [label], k)
        self.assertEqual(btns(side("busy")[0]), [])                    # 443 занят соседом — исправить нечем, только подсказка
        self.assertIn("vl.fix.free443", json.dumps(side("busy")[0], ensure_ascii=False))
        self.assertEqual(side("no_site")[0]["kids"][1]["kids"][0]["text"], "Reality — vl.reason.no_site")
        self.assertIn("vl.pending", json.dumps(side("pending")[0], ensure_ascii=False))
        # «Подробнее» — последним; на телефоне (деталь протокола) режим и строки — над карточкой, здесь — только «Подробнее»
        self.assertEqual(out["legacy"][-1]["cls"], "gx-details kk-vmore")
        self.assertEqual([x["cls"] for x in out["phone"]], ["gx-details kk-vmore"])

    def test_dqa3378_plate_names_and_site_hint(self):
        """DQA-3378 М-1: имена в плашке — из рекомендации ядра (recommend.reissue), без загрузки «Людей»; ядро без
        reissue — из «Людей» (не загружены — без имён). К4: под «Reality — сайт выключен» — «TLS включится вместе с
        сайтом на домене»."""
        out = self.run_js(self.RECS + r"""
const devs = [{ id: "d1", name: "iPhone", user_name: "Анна" }, { id: "d2", name: "Android", user_name: "" }, { id: "d3", name: "iPad", user_name: "Борис" }, { id: "d4", name: "Mac", user_name: "" }];
const withNames = Object.assign({}, R0.legacy, { recommend: { mode: "tls", devices: 4, reissue: devs } });
const o = { names: flat(vlRecPlate(row(withNames))).kids[0].kids[1].text, old: flat(vlRecPlate(row(R0.legacy))).kids[0].kids[1].text };
o.site = flat(vlFixRow(row(R0.no_site))).kids[1].kids.map((k) => k.text);
o.busy = flat(vlFixRow(row(R0.busy))).kids[1].kids.map((k) => k.text);
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["names"], ' vl.recPrice#4{"list":" (vl.namesMore{\\"list\\":\\"iPhone (Анна), Android, iPad (Борис)\\",\\"n\\":\\"1\\"})"}')
        self.assertEqual(out["old"], ' vl.recPrice#2{"list":""}')
        self.assertEqual(out["site"], ["Reality — vl.reason.no_site", "vl.fix.siteOnSub"])
        self.assertEqual(out["busy"][1], "vl.fix.free443")
        js = read("app.js")
        # М-7: строка VLESS в списке (Т3: и на телефоне — та же строка) — режим в имени, причина или рекомендация строкой
        # под ней (акцентом — рекомендация; ux.js rowWrap: o.hint)
        card = jsparts.function(js, "ibUxCard")
        for need in ('title: r.kind === "vless" ? vlTitle(r) : r.shown || r.title', 'hint: r.kind === "vless" ? vlHint(r) : null'):
            self.assertIn(need, card)
        self.assertIn("'kb2-pl__al ux-pc__hint' + (o.hint.rec ? ' ux-pc__hint--rec' : '')", read("ux.js"))
        self.assertIn(".ux-pc__hint--rec { color: var(--accent-text); }", read("ux.css"))
        # М-6: «порт · устройства» VLESS — в самой строке (колонки «Устройства» и «Порт»), детали во весь экран нет
        self.assertIn("cells: row ? { dev: ibDevNode(r), roles: ibRoles(r.kind), meta: ibMeta(r) } : null", card)
        self.assertNotIn("function ibDetail", js)
        css = read("components.css")
        self.assertIn(".gx-impact__det[open] > .gx-impact__sum > .gi { transform: rotate(180deg); }", css)

    def test_dqa3378_template_tokens(self):
        """Ш-1…Ш-3: размеры и радиусы новых блоков VLESS — токенами шкалы; моноширинный пункт радио — только в окне
        маскировки."""
        comp, ux, qs = read("components.css"), read("ux.css"), read("setup/qs.css")
        for sel in (".gx-vgrp__s {", ".kk-vrow__b > span {"):
            line = [x for x in comp.splitlines() if x.startswith(sel)][0]
            self.assertIn("font-size: var(--fs-xs)", line)
        self.assertIn(".ux-pc__hint { min-width: 0; font-size: var(--fs-xs);", ux)
        self.assertIn(".gx-dlg--mask .gx-radio__t .gx-mono { font-size: var(--fs-sm); }", comp)
        self.assertNotIn("\n.gx-radio__t .gx-mono", comp)
        self.assertNotIn("13.5px", [x for x in comp.splitlines() if "gx-radio__t .gx-mono" in x][0])
        self.assertIn("border-radius: var(--r-tag)", [x for x in qs.splitlines() if x.startswith(".qs-vimpact__m")][0])

    def test_consent_and_price_flow(self):
        """Окно согласия (§4.3) — текст ядра; «Назад» основная и в фокусе; согласие → тот же запрос с accept_risks,
        risks, site и yes; цена (vless_change) → окно цены (фокус — «Отмена») → тот же запрос с yes."""
        out = self.run_js(r"""
const call = (x) => API.vless.mode(Object.assign({ mode: "reality", site: "a.example" }, x));
const consent = err409({ need: "reality_consent", site: "a.example", risks: "r1a", title: "Reality вместо", block: "Начало:\n- риск один;\n- риск два\n\nХвост", phrase: "Понимаю" });
const price = err409({ need: "vless_change", text: "Цена ядра", devices: [{ name: "Тел", user_name: "Мама" }] });
const o = {};
// 0) как отвечает ядро (REV-vless4 З2): цена — строкой в тексте согласия, после «Понимаю риски» — сразу итог, второго окна нет
ANSWERS.push(consent, { ok: true, changed: true }); PICK.push(0);
o.real = await vlRun(call, { busy: "b" });
o.realCalls = CALLS.splice(0); o.realDialogs = DIALOGS.splice(0).length; FOCUS.splice(0);
// 1) защитный путь: ядро после согласия всё же спросило цену → окно цены → готово
ANSWERS.push(consent, price, { ok: true, changed: true }); PICK.push(0, 1);
o.ok = await vlRun(call, { busy: "b" });
o.calls = CALLS.splice(0);
o.consent = { title: DIALOGS[0].o.title, alert: DIALOGS[0].o.alert, backdrop: DIALOGS[0].o.backdrop, foot: DIALOGS[0].foot.map((b) => [b.btn, b.kind]), blocks: DIALOGS[0].o.blocks.map(flat) };
o.price = { foot: DIALOGS[1].foot.map((b) => [b.btn, b.kind]), blocks: DIALOGS[1].o.blocks.filter(Boolean).map(flat) };
o.focus = FOCUS.splice(0); DIALOGS.splice(0);
// 2) «Назад» — ничего не отправлено после отказа
ANSWERS.push(consent); PICK.push(1);
o.back = await vlRun(call, { busy: "b" }); o.backCalls = CALLS.splice(0).length;
// 3) Esc / щелчок мимо — тоже «Назад»
ANSWERS.push(consent);
const d3 = DIALOGS.length, p3 = vlRun(call, { busy: "b" });
for (let i = 0; i < 50 && DIALOGS.length === d3; i++) await new Promise((r) => setImmediate(r));
DIALOGS[DIALOGS.length - 1].close(); o.esc = await p3; o.escCalls = CALLS.splice(0).length;
// 4) «Отмена» в окне цены
ANSWERS.push(price); PICK.push(0);
o.cancel = await vlRun(call, { busy: "b" }); o.cancelCalls = CALLS.splice(0).length;
// 5) согласие без сайта (ядро ещё не подобрало) — окна нет, ничего не меняется
ANSWERS.push(err409({ need: "reality_consent", site: null, risks: "r1a" }));
const n0 = DIALOGS.length; o.noSite = await vlRun(call, { busy: "b" }); o.noSiteDialogs = DIALOGS.length - n0; CALLS.splice(0);
// 6) чужая ошибка — onError, без окон
ANSWERS.push(Object.assign(new Error("x"), { status: 500 })); let seen = 0;
o.other = await vlRun(call, { busy: "b", onError: () => { seen++; return true; } }); o.seen = seen;
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["real"], {"ok": True, "changed": True})
        self.assertEqual(out["realCalls"], [{"mode": "reality", "site": "a.example"},
                                            {"mode": "reality", "site": "a.example", "accept_risks": True, "risks": "r1a", "yes": True}])
        self.assertEqual(out["realDialogs"], 1)                                                 # только согласие
        self.assertEqual(out["ok"], {"ok": True, "changed": True})
        self.assertEqual(out["calls"], [{"mode": "reality", "site": "a.example"},
                                        {"mode": "reality", "site": "a.example", "accept_risks": True, "risks": "r1a", "yes": True},
                                        {"mode": "reality", "site": "a.example", "accept_risks": True, "risks": "r1a", "yes": True}])
        c = out["consent"]
        self.assertEqual((c["title"], c["alert"], c["backdrop"]), ("Reality вместо", True, True))
        self.assertEqual(c["foot"], [["Понимаю", "tertiary"], ["vl.back", "primary"]])        # «Назад» — основная
        self.assertEqual([b["cls"] for b in c["blocks"]], ["gx-vtx", "gx-risk", "gx-vtx gx-vtx--2"])
        self.assertEqual([li["text"] for li in c["blocks"][1]["kids"]], ["риск один;", "риск два"])
        self.assertEqual(out["price"]["foot"], [["c.cancel", "tertiary"], ["c.apply", "primary"]])
        self.assertEqual(out["price"]["blocks"][0], {"cls": "gx-vtx", "text": "Цена ядра"})
        self.assertIn("Тел (Мама)", json.dumps(out["price"]["blocks"][-1], ensure_ascii=False))
        self.assertEqual(out["focus"], ["vl.back", "c.cancel"])                                 # по умолчанию — «Назад» и «Отмена»
        self.assertEqual((out["back"], out["backCalls"]), (None, 1))
        self.assertEqual((out["esc"], out["escCalls"]), (None, 1))
        self.assertEqual((out["cancel"], out["cancelCalls"]), (None, 1))
        self.assertEqual((out["noSite"], out["noSiteDialogs"]), (None, 0))
        self.assertEqual((out["other"], out["seen"]), (None, 1))

    def test_the_read_asks_first(self):
        """T-3378 Н-2: окно согласия и цены — по чтению GET gw/vless/ask (200 с need), действие — сразу с ответом: ни
        одного 409 на обычном пути; чтение не удалось — как раньше, отказом ядра."""
        out = self.run_js(r"""
const call = (x) => API.vless.mode(Object.assign({ mode: "reality", site: "a.example" }, x));
const o = {};
ASKS.push({ ok: true, need: "reality_consent", site: "a.example", risks: "r1a", title: "T", block: "b", phrase: "P" });
ANSWERS.push({ ok: true, changed: true }); PICK.push(0);
o.consent = await vlRun(call, { busy: "b", ask: { do: "reality", site: "a.example" } }); o.consentCalls = CALLS.splice(0); o.q = ASKQ.splice(0);
ASKS.push({ ok: true, need: "reality_consent", site: "a.example", risks: "r1a", title: "T", block: "b", phrase: "P" }); PICK.push(1);
o.back = await vlRun(call, { busy: "b", ask: { do: "reality", site: "a.example" } }); o.backCalls = CALLS.splice(0).length;
ASKS.push({ ok: true, need: "vless_change", text: "Цена", devices: [{ name: "Тел" }] }); ANSWERS.push({ ok: true }); PICK.push(1);
o.price = await vlRun(call, { busy: "b", ask: { do: "tls" } }); o.priceCalls = CALLS.splice(0);
ASKS.push({ ok: true, need: null }); ANSWERS.push({ ok: true });
const n0 = DIALOGS.length; o.none = await vlRun(call, { busy: "b", ask: { do: "tls" } }); o.noneCalls = CALLS.splice(0); o.noneDialogs = DIALOGS.length - n0;
ASKS.push(new Error("net")); ANSWERS.push(err409({ need: "vless_change", text: "Ц", devices: [] }), { ok: true }); PICK.push(1);
o.fallback = await vlRun(call, { busy: "b", ask: { do: "tls" } }); o.fallbackCalls = CALLS.splice(0);
console.log(JSON.stringify(o));
""")
        yes = {"mode": "reality", "site": "a.example", "accept_risks": True, "risks": "r1a", "yes": True}
        self.assertEqual((out["consent"], out["consentCalls"], out["q"]), ({"ok": True, "changed": True}, [yes],
                                                                          [{"do": "reality", "site": "a.example"}]))
        self.assertEqual((out["back"], out["backCalls"]), (None, 0))                # «Назад» — ничего не отправлено
        self.assertEqual(out["priceCalls"], [{"mode": "reality", "site": "a.example", "yes": True}])
        self.assertEqual((out["noneCalls"], out["noneDialogs"]), ([{"mode": "reality", "site": "a.example"}], 0))
        self.assertEqual(out["fallbackCalls"], [{"mode": "reality", "site": "a.example"},
                                                {"mode": "reality", "site": "a.example", "yes": True}])
        js = read("app.js")
        self.assertIn('ask: { do: "tls" }', jsparts.function(js, "vlToTls"))
        self.assertIn('ask: { do: "reality", site }', jsparts.function(js, "vlReality"))
        self.assertIn('ask: { do: "site_on" }', jsparts.function(js, "vlSiteOn"))
        self.assertIn('vlAsk({ do: "clear" }', jsparts.function(js, "siteClear"))
        self.assertIn('vlAsk({ do: on ? "site_on" : "site_off" }', jsparts.function(js, "siteSetMode"))

    def test_site_price(self):
        """Ц1/Ц2 (§3.6, §3.7): строки панели по отказу ядра; сайт Reality не назван — первый, что отвечает по TLS 1.3
        (не свой и не «не подходит»), и он уходит в запрос как vless_site."""
        out = self.run_js(r"""
const dd = { need: "vless_change", devices: [{ name: "a" }, { name: "b" }], site: "x.example" };
const o = { off: vlSitePriceOpts(dd, "off", { items: [["доп", ""], ["вкладка", "warn", "triangle-exclamation"]], topics: ["тема"] }), on: vlSitePriceOpts(dd, "on"), port: vlSitePriceOpts(Object.assign({}, dd, { to: { security: "reality", port: 8443 } }), "on") };
o.clear = vlSitePriceOpts(dd, "clear");
// DQA-3378 М-2: общие пункты — одной строкой последним пунктом списка, раскрытием; М-4 — красная кнопка
PICK.push(1); o.foldAns = await vlPriceDialog(o.off);
const fd = DIALOGS[DIALOGS.length - 1];
o.foldUl = fd.o.blocks.filter(Boolean).map(flat)[1];
o.foldOk = { btn: fd.foot[1].btn, kind: fd.foot[1].kind };
PICK.push(1); await vlPriceDialog(o.on); o.onOk = DIALOGS[DIALOGS.length - 1].foot[1].kind;
PICK.push(1); o.ans = await vlSiteAnswer({ need_pick: true, devices: [{ name: "a" }] }, "off");
o.picked = DIALOGS[DIALOGS.length - 1].o.blocks.filter(Boolean).map(flat)[0];
PICK.push(1); o.ansClear = await vlSiteAnswer({ need_pick: true, devices: [{ name: "a" }] }, "clear");
PICK.push(1); o.ansClear2 = await vlSiteAnswer({ site: "x.example", devices: [{ name: "a" }] }, "clear");
PICK.push(0); o.no = await vlSiteAnswer({ site: "x.example", devices: [] }, "off");
PICK.push(1); o.on2 = await vlSiteAnswer({ devices: [{ name: "a" }] }, "on");
o.of = vlPriceOf(err409({ need: "vless_change" })) !== null; o.of2 = vlPriceOf(err409({ need: "other" }));
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["off"]["title"], "vl.off.q")
        # М-2: в списке — три пункта макета Ц1 (+ предупреждение, если есть); общие — в fold под одной строкой
        self.assertEqual(out["off"]["lines"], [['vl.off.l1{"site":"x.example"}', ""], ['vl.off.short{"site":"x.example"}', ""], ["vl.off.price#2", "warn"],
                                               ["vl.off.back", "", "power"], ["вкладка", "warn", "triangle-exclamation"]])
        self.assertEqual(out["off"]["fold"], {"label": 'vl.moreT{"list":"тема"}', "items": [["доп", ""]]})
        self.assertIsNone(out["clear"]["fold"])
        # М-4: выключение сайта и «Убрать домен» — опасное действие (красная кнопка), включение — нет
        self.assertEqual((out["off"]["danger"], out["clear"]["danger"], out["on"].get("danger")), (True, True, None))
        self.assertTrue(out["foldAns"])
        self.assertEqual(out["foldOk"], {"btn": "site.maskOff", "kind": "danger"})
        self.assertEqual(out["onOk"], "primary")
        last = out["foldUl"]["kids"][-1]
        self.assertEqual(last["cls"], "gx-impact__i gx-impact__more")
        det = last["kids"][0]
        self.assertEqual(det["cls"], "gx-impact__det")
        self.assertEqual(det["kids"][0]["kids"][1], {"cls": "gx-impact__st", "text": 'vl.moreT{"list":"тема"}'})
        self.assertEqual(det["kids"][1]["cls"], "gx-impact gx-impact--in")
        self.assertEqual(det["kids"][1]["kids"][0]["kids"][1], {"cls": "", "text": "доп"})
        self.assertEqual(len(out["foldUl"]["kids"]), 5)                 # short, цена, как вернуть, вкладка, «Что ещё»
        # REV-vless-fix З2: «Убрать домен» — свой вопрос и кнопка, как вернуть TLS — через домен (не «снова включите сайт»)
        self.assertEqual((out["clear"]["title"], out["clear"]["ok"]), ("vl.clear.q", "site.clear"))
        self.assertEqual(out["clear"]["lines"], [['vl.off.l1{"site":"x.example"}', ""], ['vl.off.short{"site":"x.example"}', ""], ["vl.off.price#2", "warn"],
                                                 ["vl.clear.back", "", "globe"]])
        self.assertEqual((out["off"]["title"], out["off"]["ok"]), ("vl.off.q", "site.maskOff"))
        self.assertEqual(out["ansClear"], {"yes": True, "vless_site": "good.example"})          # сайт Reality уходит в запрос
        self.assertEqual(out["ansClear2"], {"yes": True, "vless_site": "x.example"})
        self.assertIn('vlSiteAnswer(price, "clear", more)', jsparts.function(read("app.js"), "siteClear"))
        self.assertNotIn('vlSiteAnswer(price, "off")', jsparts.function(read("app.js"), "siteClear"))
        # М-3, макет Ц2: что откроется на домене (абзацем), цена, подписки, откат
        self.assertEqual(out["on"]["lines"], [['vl.on.lead{"domain":"—"}', ""], ["vl.on.tlsPrice#2", "warn"], ["vl.r2t.subs", "", "arrow-rotate-right"],
                                              ["vl.on.back", "", "shield"]])
        self.assertEqual(out["port"]["lines"], [['vl.on.port#2{"port":8443}', "warn"]])
        self.assertEqual(out["ans"], {"yes": True, "vless_site": "good.example"})
        self.assertEqual(out["picked"], {"cls": "gx-vtx", "text": 'vl.off.l1{"site":"good.example"}'})       # первая строка — абзацем
        self.assertIsNone(out["no"])
        self.assertEqual(out["on2"], {"yes": True})
        self.assertEqual((out["of"], out["of2"]), (True, None))

    def test_tile_chip(self):
        out = self.run_js('console.log(JSON.stringify([vlTileChip({ ok: true, h2: true }), vlTileChip({ ok: true, h2: false }), '
                          'vlTileChip({ ok: false, why: "нет TLS 1.3" }), vlTileChip(null), vlTileChip({ ok: null })]));')
        self.assertEqual(out[:3], [{"chip": "TLS 1.3 · h2", "tone": "success", "tip": "vl.tlsH2"}, {"chip": "TLS 1.3", "tone": "success", "tip": "vl.tlsOnly"},
                                   {"chip": "vl.checkBad", "tone": "danger", "tip": "нет TLS 1.3"}])
        self.assertEqual(out[3:], [None, None])

    def test_texts(self):
        """RU и EN — по таблице HANDOFF-vless §7 (ключи панели; составные строки макета — по частям). В EN — «server»,
        а не «node» макета: правило терминов панели (test_i18n_terms_web.test_server_not_node)."""
        ru, en = i18n_vals(), dict(re.findall(r'^\s*"([A-Za-z0-9_.]+)":\s*"(.*)",?$', read("i18n-en.js"), flags=re.M))
        want = {
            "vl.mask.title": ("Маскировка VLESS", "VLESS masking"),
            "vl.mask.nowTls": ("Сейчас: TLS · {domain}", "Now: TLS · {domain}"),
            "vl.mask.nowReality": ("Сейчас: Reality · {site}", "Now: Reality · {site}"),
            "vl.tls.t": ("Свой сертификат (TLS)", "Own certificate (TLS)"),
            "vl.tls.rec": ("рекомендуем", "recommended"),
            "vl.current": ("сейчас", "now"),
            "vl.tls.d": ("VLESS на 443 с сертификатом {domain}; всё постороннее видит обычный сайт", "VLESS on 443 with the {domain} certificate; everyone else sees an ordinary site"),
            "vl.tls.siteOn": ("На домене включится обычный сайт; панель будет и по адресу https://{domain}/…", "An ordinary site will be turned on for the domain; the panel will also be at https://{domain}/…"),
            "vl.tls.whyNoCert": ("Сначала выпустите сертификат: Настройки → Домен и сайт", "Issue the certificate first: Settings → Domain and site"),
            "vl.tls.whyNoDomain": ("Нужен свой домен — Настройки → Домен и сайт", "You need your own domain — Settings → Domain and site"),
            # REV-vless4 З1: сам узел на TLS не переходит — после освобождения 443 TLS выбирают в окне или на карточке
            "vl.tls.whyBusy443": ("443 занят: {who}. Освободите 443 — затем выберите TLS здесь", "443 is taken: {who}. Free 443, then choose TLS here"),
            # REV-vless-fix П4: при выключенном сайте на карточке «Включить сайт на домене», а не «Перейти на TLS»
            "vl.fix.free443": ("Освободите 443 — затем карточка покажет, как перейти на TLS", "Free 443 — the card then shows how to switch to TLS"),
            "vl.tls.whyPanel443": ("443 занят открытым режимом панели — переведите его на свой порт", "443 is taken by the panel’s open mode — move it to its own port"),
            "vl.tls.buy": ("Купить домен", "Buy a domain"),
            "vl.reality.t": ("Reality — если нет домена или вы сознательно выбираете его", "Reality — if there is no domain or you choose it knowingly"),
            "vl.kept": ("прежний", "previous"), "vl.kept.d": ("Старые ссылки Reality могут заработать", "Old Reality links may work again"),
            "vl.own": ("свой сайт", "own site"), "vl.own.d": ("Адрес и сертификат совпадают; VLESS уйдёт с 443 на {port}", "Address and certificate match; VLESS moves from 443 to {port}"),
            "vl.own.dPort": ("Адрес и сертификат совпадают; VLESS останется на порту {port}", "Address and certificate match; VLESS stays on port {port}"),
            "vl.auto": ("Подобрать автоматически", "Pick automatically"),
            "vl.auto.d": ("Первый сайт списка, который отвечает с узла по TLS 1.3; имя покажем до применения", "The first site on the list that answers the server over TLS 1.3; its name is shown before applying"),
            "vl.custom2": ("Другой сайт…", "Another site…"), "vl.custom.d": ("Свой адрес — узел проверит его по TLS 1.3", "Your own address — the server checks it over TLS 1.3"),
            "vl.checkAll": ("Проверить все с узла", "Check all from the server"), "vl.checkingAll": ("Проверяю с узла…", "Checking from the server…"),
            "vl.price.same": ("Выбран текущий режим — ничего не меняется", "Current mode selected — nothing changes"),
            "vl.price.next": ("дальше — риски Reality", "next — Reality risks"),
            "vl.price.none": ("Ссылки не изменятся — устройств VLESS пока нет", "Links stay the same — no VLESS devices yet"),
            "vl.modeTls": ("TLS · свой домен {domain}", "TLS · own domain {domain}"),
            "vl.reason.no_domain": ("нет домена — запасной вариант", "no domain — Reality"),
            "vl.reason.no_cert": ("ждём сертификат домена", "waiting for the domain certificate"),
            "vl.reason.busy_443": ("443 занят: {who}", "443 is taken: {who}"),
            "vl.reason.panel_443": ("443 занят открытым режимом панели", "443 is taken by the panel’s open mode"),
            "vl.reason.no_site": ("сайт на домене выключен", "the site on the domain is off"),
            "vl.reason.tls_failed": ("TLS не включился: {why}", "TLS did not turn on: {why}"),
            "vl.reason.ready": ("TLS уже можно — нужна перевыдача ключей", "TLS is possible now — keys have to be given out again"),
            "vl.reason.chosen": ("выбран вами {date}", "chosen by you on {date}"),
            "vl.reason.consent_bad": ("согласие не найдено", "consent not found"),
            "vl.reason.legacy": ("рекомендуем перейти на TLS", "we recommend switching to TLS"),
            "vl.recB": ("Можно перейти на TLS со своим доменом.", "You can switch to TLS with your own domain."),
            "vl.toTls": ("Перейти на TLS", "Switch to TLS"), "vl.keepReality": ("Оставить Reality", "Keep Reality"),
            "vl.confirmReality": ("Подтвердить Reality", "Confirm Reality"),
            "vl.maskBtn": ("Маскировка…", "Masking…"), "vl.menuMask": ("Маскировка VLESS…", "VLESS masking…"),
            "vl.phrase": ("Понимаю риски, выбрать Reality", "I understand the risks, choose Reality"), "vl.back": ("Назад", "Back"),
            "vl.r2t.q": ("Перейти на TLS?", "Switch to TLS?"),
            "vl.r2t.l1": ("VLESS встанет на 443 со своим сертификатом {domain}.", "VLESS moves to 443 with its own {domain} certificate."),
            "vl.r2t.subs": ("Подписки обновятся сами при следующем обновлении в приложении.", "Subscriptions update by themselves on the next refresh in the app."),
            "vl.r2t.back": ("Если TLS не включится, узел вернёт всё как было — ссылки Reality останутся рабочими.", "If TLS does not turn on, the server puts everything back — Reality links keep working."),
            "vl.off.q": ("Выключить сайт на домене?", "Turn off the site on the domain?"),
            "vl.off.l1": ("Сайт на домене выключится, VLESS перейдёт на запасной Reality под {site}.", "The site on the domain turns off, VLESS switches to Reality (backup) as {site}."),
            "vl.off.back": ("Чтобы вернуть TLS — снова включите сайт.", "To get TLS back, turn the site on again."),
            # REV-vless-fix З2: «Убрать домен» — сайта без домена нет, вернуть TLS — домен, сертификат и сайт
            "vl.clear.q": ("Убрать домен?", "Remove the domain?"),
            "vl.clear.back": ("Чтобы вернуть TLS — снова задайте домен, выпустите сертификат и включите сайт.",
                              "To get TLS back, set the domain again, issue its certificate and turn the site on."),
            "vl.on.q": ("Включить сайт на домене?", "Turn on the site on the domain?"),
            "vl.on.back": ("Если TLS не включится, сайт выключится обратно, а Reality останется как был.", "If TLS does not turn on, the site turns off again and Reality stays as it was."),
            "vl.reissue.who": ("Кому выдать заново:", "Give out again to:"),
        }
        for k, (r, e) in want.items():
            self.assertEqual((ru.get(k), en.get(k)), (r, e), k)
        # множественное число (one/few/many | one/other) — начало как в таблице
        for k, r, e in (("vl.price.reissue", "Ключи VLESS нужно выдать заново: {n} устройств", "VLESS keys have to be given out again: {n} device"),
                        ("vl.r2t.price", "Ссылки VLESS, выданные раньше, перестанут работать. Выдать заново: {n} устройств", "VLESS links given out earlier will stop working. Give out again: {n} device"),
                        ("vl.off.price", "Ссылки VLESS — выдать заново ({n} устройств", "VLESS links — give out again ({n} device")):
            self.assertTrue(all(x.startswith(r) for x in ru[k].split("|")), k)
            self.assertTrue(all(x.startswith(e) for x in en[k].split("|")), k)
            self.assertEqual((len(ru[k].split("|")), len(en[k].split("|"))), (3, 2), k)
        self.assertNotIn("чужой сайт", ru["vl.descTls"])
        # REV-vless4 З1: обещания самоперехода на TLS нет; П1: «запасной» по-английски — backup, не fallback
        for k in ("vl.fix.free443", "vl.tls.whyBusy443"):
            self.assertNotRegex(ru[k], r"\bсам\b", k)
            self.assertNotIn("by itself", en[k], k)
        self.assertEqual(sorted(k for k, v in en.items() if k.startswith("vl.") and "fallback" in v), [])
        js = read("app.js")
        self.assertIn('vlSec(r.record) === "tls" ? t("vl.descTls") : t("ibd." + r.kind)', js)      # описание карточки
        mask = jsparts.function(js, "openVlessMask")
        self.assertIn("vlListText(st.list, st.country)", mask)                                   # строка над списком
        # все ключи vl.* из app.js есть в обоих языках
        used = set(re.findall(r'\bt\("(vl\.[A-Za-z0-9_.]+)"', js)) | set(re.findall(r'\btn\("(vl\.[A-Za-z0-9_.]+)"', js))
        used = {k for k in used if not k.endswith(".")}
        used |= {"vl.reason." + k for k in ("no_domain", "no_cert", "busy_443", "no_site", "tls_failed", "ready", "chosen", "consent_bad", "legacy")}
        self.assertEqual(sorted(k for k in used if k not in ru), [])
        self.assertEqual(sorted(k for k in used if k not in en), [])


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class WindowTest(unittest.TestCase):
    """Окно «Маскировка VLESS» (§4.1, кадры О1–О4): TLS первым с «рекомендуем», недоступный — с причиной и «исправить»;
    ниже блок Reality; цена — строкой внизу до нажатия; согласие — только при выборе Reality, когда TLS возможен."""

    def test_source(self):
        f = jsparts.function(read("app.js"), "openVlessMask")
        # TLS — первым пунктом, блок Reality (vl.reality.t) — после него
        self.assertLess(f.index("tlsOpt()"), f.index('t("vl.reality.t")'))
        self.assertIn('U.chip(t("vl.tls.rec"), "accent")', f)
        for need in ('t("vl.tls.whyNoDomain")', 't("vl.tls.whyNoCert")', 't("vl.tls.whyPanel443")', 't("vl.tls.whyBusy443"',
                     't("vl.tls.buy")', 'dis: !tlsOk', 't("vl.price.same")', 't("vl.price.next")', 't("vl.price.siteOn")', '"gx-vfoot"'):
            self.assertIn(need, f)
        # «Купить домен» — только партнёрская ссылка https из GET /api/hosting
        self.assertIn('/^https:\\/\\//.test(String(buy.url || ""))', f)
        self.assertIn('rel: "noopener"', f)

    def test_phone(self):
        """Телефон (Т1–Т4; Т3 Б r3 — тот же вид, что на компьютере): строка VLESS — имя с режимом; раскрытая — режим,
        плашка или причина и «Маскировка VLESS» (vlessInfo полностью; кнопка «Маскировка…» — одна, в строке кнопок её нет —
        REV-it1-b З-1); низ окна листом —
        цена строкой сверху, «Отмена · Применить» в ряд."""
        js = read("app.js")
        card = jsparts.function(js, "ibUxCard")
        self.assertIn('title: r.kind === "vless" ? vlTitle(r) : r.shown || r.title', card)
        self.assertIn("vlessInfo(r, !fold)", card)                    # строка списка (fold) — всё, без урезанной карточки
        self.assertIn("ibUxCard(r, true, true)", jsparts.function(js, "ibList"))
        self.assertIn("menu.push(...vlMenu(r, !!row));", card)          # строка: без второй «Маскировка VLESS…»
        self.assertIn('const out = noMask ? [] : [{ label: t("vl.menuMask")', jsparts.function(js, "vlMenu"))
        info = jsparts.function(js, "vlessInfo")
        for need in ("vlTop(r)", 't("vl.mode")', "() => openVlessMask(r)"):
            self.assertIn(need, info)
        self.assertNotIn("function vlPhone", js)
        css = read("components.css")
        self.assertIn('[data-device="phone"] .gx-dlg-bd--sheet .gx-dlg__foot.gx-vfoot > .ga-btn:not(:first-child):not(:last-child) { order: 0; flex-basis: 0; }', css)
        self.assertIn('[data-device="phone"] .gx-vfoot > .gx-impact { flex-basis: 100%; order: -2; }', css)

    def test_settings_price_loads_protocols(self):
        """Ц1/Ц3 из «Настроек»: протоколы там не грузятся — без тихой подгрузки VLESS на TLS не видно, и вместо одного
        окна цены выходило два вопроса подряд (общий и цена ядра), а в смене домена не было строки VLESS."""
        js = read("app.js")
        mode = jsparts.function(js, "siteSetMode")
        self.assertLess(mode.index('loadQuiet("inbounds")'), mode.index("vlTlsDevs()"))
        dom = jsparts.function(js, "siteSaveDomain")
        self.assertLess(dom.index('loadQuiet("inbounds")'), dom.index("vlDomainWarn()"))


@unittest.skipUnless(shutil.which("node"), "нет node: макет не запустить")
class MockModesTest(unittest.TestCase):
    def mock(self, query):
        return run_node(r"""
globalThis.window = globalThis;
globalThis.location = new URL("http://127.0.0.1/index.html?mock=1&lat=0%s");
require(%s);
const call = async (m, p, body) => { const r = await window.KASKAD_MOCK.fetch(p, { method: m, headers: { "X-Kaskad-Admin": "1" },
  body: body ? JSON.stringify(body) : undefined }); return [r.status, await r.json()]; };
(async () => {
  const out = {};
  out.ib = (await call("GET", "/api/gw/inbounds"))[1].inbounds.filter((r) => r.kind === "vless")[0];
  out.masks = (await call("GET", "/api/gw/vless/masks"))[1];
  out.rekey = await call("POST", "/api/gw/vless/rekey", {});
  out.auto = await call("PUT", "/api/gw/vless/mask", { site: "auto" });
  console.log(JSON.stringify(out));
  process.exit(0);
})();
""" % (query, json.dumps(os.path.join(STATIC, "dev-mock.js"))))

    def test_default_is_reality_abroad(self):
        out = self.mock("")
        rec = out["ib"]["record"]
        self.assertEqual((rec["security"], rec["mask_list"], rec["country"]["code"]), ("reality", "int", "DE"))
        self.assertEqual((rec["domain"], rec["node_domain"], rec["reason"]), ("", "", "no_domain"))     # домена нет
        self.assertEqual((out["masks"]["list"], out["masks"]["country"]["code"]), ("int", "DE"))
        self.assertNotIn("ya.ru", [m["sni"] for m in out["masks"]["masks"]])
        self.assertEqual(out["rekey"][0], 200)

    def test_russia(self):
        out = self.mock("&vless=ru")
        rec = out["ib"]["record"]
        self.assertEqual((rec["mask_list"], rec["country"]["code"], rec["sni"]), ("ru", "RU", "vk.com"))
        self.assertIn("ya.ru", [m["sni"] for m in out["masks"]["masks"]])
        self.assertEqual([m["sni"] for m in out["masks"]["masks"] if m["current"]], ["vk.com"])

    def test_tls(self):
        out = self.mock("&vless=tls")
        rec = out["ib"]["record"]
        self.assertEqual((rec["security"], rec["domain"], rec["port"]), ("tls", "vpn.example.net", 443))
        self.assertEqual(out["ib"]["ports"], ["443/tcp"])
        self.assertEqual((out["rekey"][0], out["auto"][0]), (409, 409))      # ни ключей, ни сайта маскировки у TLS

    def test_block(self):
        rec = self.mock("&vless=block")["ib"]["record"]
        # как vless_view ядра: у Reality domain пуст, домен узла — в node_domain; TLS нельзя — причина busy_443
        self.assertEqual((rec["security"], rec["domain"], rec["node_domain"], rec["reason"]), ("reality", "", "vpn.example.net", "busy_443"))
        self.assertIn("nginx", rec["tls_block"])



@unittest.skipUnless(shutil.which("node"), "нет node: макет не запустить")
class MockVlessTest(unittest.TestCase):
    """Макет (?mock=1&vless=…) отвечает как ядро Ш1–Ш3: vless_view по причинам, POST /api/gw/vless/mode с отказами
    reality_consent и vless_change, цена VLESS у включения и выключения сайта (Ц1/Ц2)."""

    def mock(self, query, steps):
        return run_node(r"""
globalThis.window = globalThis;
globalThis.location = new URL("http://127.0.0.1/index.html?mock=1&lat=0%s");
const st0 = globalThis.setTimeout; globalThis.setTimeout = (f) => st0(f, 0);
require(%s);
const call = async (m, p, body) => { const r = await window.KASKAD_MOCK.fetch(p, { method: m, headers: { "X-Kaskad-Admin": "1" },
  body: body ? JSON.stringify(body) : undefined }); const j = await r.json(); return [r.status, j]; };
const vl = async () => (await call("GET", "/api/gw/inbounds"))[1].inbounds.filter((r) => r.kind === "vless")[0].record;
(async () => {
  const out = {};
%s
  console.log(JSON.stringify(out));
  process.exit(0);
})();
""" % (query, json.dumps(os.path.join(STATIC, "dev-mock.js")), steps))

    def test_reasons(self):
        want = {"": "no_domain", "nocert": "no_cert", "busy": "busy_443", "panel443": "busy_443", "nosite": "no_site", "failed": "tls_failed",
                "ready": "ready", "chosen": "chosen", "consent": "consent_bad", "legacy": "legacy", "pending": "ready", "tls": ""}
        for mode, reason in want.items():
            rec = self.mock("&vless=" + mode, "  out.rec = await vl();")["rec"]
            self.assertEqual(rec["reason"], reason, mode)
            self.assertEqual(rec["security"], "tls" if mode == "tls" else "reality", mode)
            self.assertEqual(bool(rec.get("recommend")), reason in ("ready", "legacy", "consent_bad"), mode)
            self.assertEqual(bool(rec.get("pending")), mode == "pending", mode)
            self.assertEqual(rec["legacy"], mode == "legacy", mode)
            self.assertNotIn("_vl", rec)
        rec = self.mock("&vless=panel443", "  out.rec = await vl();")["rec"]
        self.assertEqual(rec["fix"]["do"], "panel_port")
        rec = self.mock("&vless=chosen", "  out.rec = await vl();")["rec"]
        self.assertTrue(rec["consent"]["at"])
        rec = self.mock("&vless=failed", "  out.rec = await vl();")["rec"]
        self.assertTrue(rec["tls_error"]["why"])

    def test_mode_flow(self):
        out = self.mock("&vless=legacy", r"""
  out.tls1 = await call("POST", "/api/gw/vless/mode", { mode: "tls" });
  out.tls2 = await call("POST", "/api/gw/vless/mode", { mode: "tls", yes: true });
  out.afterTls = await vl();
  out.r1 = await call("POST", "/api/gw/vless/mode", { mode: "reality", site: "dl.google.com" });
  const risks = out.r1[1].data && out.r1[1].data.risks;
  out.r2 = await call("POST", "/api/gw/vless/mode", { mode: "reality", site: "dl.google.com", accept_risks: true, risks });
  out.r3 = await call("POST", "/api/gw/vless/mode", { mode: "reality", site: "dl.google.com", accept_risks: true, risks, yes: true });
  out.afterR = await vl();
""")
        need = lambda r: (r[0], (r[1].get("data") or {}).get("need"))                  # noqa: E731
        self.assertEqual(need(out["tls1"]), (409, "vless_change"))
        self.assertTrue(out["tls1"][1]["data"]["devices"])
        self.assertEqual(out["tls2"][0], 200)
        self.assertEqual((out["afterTls"]["security"], out["afterTls"]["reason"]), ("tls", ""))
        self.assertEqual(need(out["r1"]), (409, "reality_consent"))
        d = out["r1"][1]["data"]
        self.assertEqual((d["risks"], d["site"]), ("r1a", "dl.google.com"))
        self.assertIn("dl.google.com", d["block"])
        # REV-vless4 З2: при устройствах цена — строкой в тексте согласия (панель шлёт согласие сразу с yes)
        self.assertIn("выдать заново", d["block"])
        self.assertEqual(need(out["r2"]), (409, "vless_change"))
        self.assertEqual(out["r3"][0], 200)
        self.assertEqual((out["afterR"]["security"], out["afterR"]["sni"], out["afterR"]["reason"]), ("reality", "dl.google.com", "chosen"))

    def test_the_mock_answers_the_read(self):
        """T-3378 Н-2: макет отвечает на GET gw/vless/ask, как сервер: 200 с need и данными отказа, ничего не меняя."""
        a = self.mock("&vless=legacy", r"""
  out.tls = await call("GET", "/api/gw/vless/ask?do=tls");
  out.r = await call("GET", "/api/gw/vless/ask?do=reality&site=dl.google.com");
  out.bad = await call("GET", "/api/gw/vless/ask?do=x");
  out.rec = await vl();
""")
        self.assertEqual((a["tls"][0], a["tls"][1]["need"]), (200, "vless_change"))
        self.assertTrue(a["tls"][1]["devices"])
        self.assertEqual((a["r"][0], a["r"][1]["need"], a["r"][1]["site"]), (200, "reality_consent", "dl.google.com"))
        self.assertIn("dl.google.com", a["r"][1]["block"])
        self.assertEqual(a["bad"][0], 400)
        self.assertNotEqual(a["rec"]["security"], "tls")                              # the read changed nothing
        b = self.mock("&vless=tls&site=on", r"""
  out.off = await call("GET", "/api/gw/vless/ask?do=site_off");
  out.clear = await call("GET", "/api/gw/vless/ask?do=clear");
  out.rec = await vl();
""")
        self.assertEqual([b[k][1]["need"] for k in ("off", "clear")], ["vless_change", "vless_change"])
        self.assertEqual(b["rec"]["security"], "tls")
        c = self.mock("&vless=nosite&site=le", '  out.on = await call("GET", "/api/gw/vless/ask?do=site_on");')
        self.assertEqual((c["on"][0], c["on"][1]["need"]), (200, "vless_change"))

    def test_site_gates(self):
        off = self.mock("&vless=tls&site=on", r"""
  out.a = await call("PUT", "/api/site/mode", { mode: "domain" });
  const site = out.a[1].data && (out.a[1].data.site || "dl.google.com");
  out.b = await call("PUT", "/api/site/mode", { mode: "domain", yes: true, vless_site: site });
  out.rec = await vl();
""")
        self.assertEqual((off["a"][0], off["a"][1]["data"]["need"]), (409, "vless_change"))
        self.assertEqual(off["b"][0], 200)
        self.assertEqual((off["rec"]["security"], off["rec"]["reason"]), ("reality", "no_site"))
        on = self.mock("&vless=nosite&site=le", r"""
  out.a = await call("PUT", "/api/site/mode", { mode: "site", template: "random" });
  out.b = await call("PUT", "/api/site/mode", { mode: "site", template: "random", yes: true });
  out.rec = await vl();
""")
        self.assertEqual((on["a"][0], on["a"][1]["data"]["need"]), (409, "vless_change"))
        self.assertEqual(on["b"][0], 200)
        self.assertEqual(on["rec"]["security"], "tls")


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class RevVless4Test(unittest.TestCase):
    """REV-vless4: П2 — выключение сайта решает «общий вопрос или окно цены» по свежим протоколам, не по кэшу; П3 —
    подпись «свой сайт» в окне «Маскировка VLESS» — с портом из записи, а не 8443 всегда."""

    def harness(self, funcs, consts=(), pre=""):
        js = read("app.js")
        stubs = STUBS.replace("openVlessMask = () => 0, ", "")
        stubs = stubs.replace("const d = { o, foot: null, closed: false, box: { classList: { add() {} } } };",
                              "const d = { o, foot: { classList: { add() {} } }, closed: false, box: { classList: { add() {} }, isConnected: true } };")
        stubs = stubs.replace("d.setFoot = (b) => { d.foot = b;", "d.setFoot = (b) => { d.btns = b;")
        stubs = stubs.replace("  n.setAttribute = () => 0;", "  n.setAttribute = () => 0;\n  n.replaceChildren = (...k) => { n.kids = k.flat().filter(Boolean); };")
        stubs = stubs.replace("const U = {", "const RADIO = [];\nconst U = {\n  radio(items, o) { RADIO.push(items); return { radio: 1 }; }, field() { return { _hint: {} }; },\n"
                              "  input() { return { value: \"\", focus() {} }; }, al() { return { al: 1 }; },")
        assert stubs.count("RADIO") >= 2 and "replaceChildren" in stubs and "d.btns = b" in stubs
        extra = "let hostingDoc = null;\nconst vlessDone = () => 0;\n" + pre
        parts = [stubs, extra] + [jsparts.const(js, n) for n in APP_CONSTS + tuple(consts)] + \
            [jsparts.function(js, n) for n in APP_FUNCS + tuple(funcs)]
        return lambda body: run_node("\n".join(parts) + "\n(async () => {\n" + body +
                                     "\n})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });")

    def test_own_site_port(self):
        run = self.harness(("openVlessMask",), ("vlHost",))
        out = run(r"""
API.vless.masks = async () => ({ ok: true, masks: [] });
const own = (rec) => { RADIO.splice(0); openVlessMask({ kind: "vless", devices: 1, record: rec });
  const it = RADIO.flat().find((x) => arr(x.chips).some((c) => c && c.chip === "vl.own")); return it ? it.desc : null; };
console.log(JSON.stringify({
  r4443: own({ security: "reality", port: 4443, mask: "list", sni: "dl.google.com", reason: "chosen", node_domain: "vpn.example.com" }),
  r8443: own({ security: "reality", port: 8443, mask: "list", sni: "dl.google.com", reason: "chosen", node_domain: "vpn.example.com" }),
  tls: own({ security: "tls", port: 443, domain: "vpn.example.com", node_domain: "vpn.example.com" }),
  noPort: own({ security: "tls", domain: "vpn.example.com", node_domain: "vpn.example.com" }) }));
""")
        self.assertEqual(out["r4443"], 'vl.own.dPort{"port":4443}')       # VLESS не на 443 — «останется на порту 4443»
        self.assertEqual(out["r8443"], 'vl.own.dPort{"port":8443}')
        self.assertEqual(out["tls"], 'vl.own.d{"port":8443}')             # с 443 (TLS) — на 8443, как ядро
        self.assertEqual(out["noPort"], 'vl.own.d{"port":8443}')

    def test_site_off_fresh_protocols(self):
        run = self.harness(("siteSetMode",), ("vlTlsDevs",), pre=r"""
const oneAtATime = (k, f) => f();
const SITE = { domain: "vpn.example.com", mode: "site" };
const rd = (k) => (k === "site" ? SITE : R[k].data);
const tabVia = () => "port";
let ASKED = 0, RUNS = 0, FRESH = [];
const confirmDialog = async () => { ASKED++; return false; };
const loadQuiet = async (k) => { R[k] = { loaded: true, data: FRESH }; return FRESH; };
const siteRun = async () => { RUNS++; return null; };
const siteModeBody = () => ({}), siteRefusal = () => false, siteFail = () => true, noticeDialog = async () => 0;
let siteTemplateChoice = null, sitePanelFull = "";
""")
        out = run(r"""
const vl = (n) => [{ kind: "vless", installed: true, devices: n, record: { security: "tls" } }];
const o = {};
// кэш: VLESS на TLS с устройством; свежие протоколы: устройство удалили (другая вкладка, бот) — общий вопрос нужен
R.inbounds = { loaded: true, data: vl(1) }; FRESH = vl(0); ASKED = 0; RUNS = 0;
await siteSetMode(false); o.gone = [ASKED, RUNS];
// кэш без устройств, а свежие — с устройством: общий вопрос не задаём, его строки уйдут в окно цены ядра (Ц1)
R.inbounds = { loaded: true, data: vl(0) }; FRESH = vl(1); ASKED = 0; RUNS = 0;
await siteSetMode(false); o.came = [ASKED, RUNS];
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["gone"], [1, 0])          # общий вопрос задан (и «Отмена» — ничего не отправлено)
        self.assertEqual(out["came"], [0, 1])          # сразу запрос: ядро ответит ценой (Ц1)

    def test_site_off_by_the_read(self):
        """T-3378 Н-2: цена выключения сайта — чтением: есть — одно окно цены (Ц1) без общего вопроса и без запроса до
        ответа; нет — общий вопрос (кэш протоколов не нужен)."""
        run = self.harness(("siteSetMode",), ("vlTlsDevs",), pre=r"""
const oneAtATime = (k, f) => f();
const SITE = { domain: "vpn.example.com", mode: "site" };
const rd = (k) => (k === "site" ? SITE : R[k].data);
const tabVia = () => "port";
let ASKED = 0, RUNS = 0, LOADS = 0;
const confirmDialog = async () => { ASKED++; return false; };
const loadQuiet = async (k) => { LOADS++; return []; };
const siteRun = async () => { RUNS++; return null; };
const siteModeBody = () => ({}), siteRefusal = () => false, siteFail = () => true, noticeDialog = async () => 0;
let siteTemplateChoice = null, sitePanelFull = "";
""")
        out = run(r"""
R.inbounds = { loaded: true, data: [] };
const o = {};
ASKS.push({ ok: true, need: "vless_change", kind: "site", text: "Ц", site: "vk.com", devices: [{ name: "a" }] }); PICK.push(0);
await siteSetMode(false); o.price = [ASKED, RUNS, LOADS, DIALOGS.length, ASKQ.splice(0)];
ASKS.push({ ok: true, need: null }); DIALOGS.splice(0);
await siteSetMode(false); o.none = [ASKED, RUNS, LOADS, DIALOGS.length];
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["price"], [0, 0, 0, 1, [{"do": "site_off"}]])     # одно окно цены, «Отмена» — ничего
        self.assertEqual(out["none"], [1, 0, 0, 0])                            # цены нет — общий вопрос


if __name__ == "__main__":
    unittest.main()

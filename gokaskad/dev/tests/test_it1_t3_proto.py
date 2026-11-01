"""Круг 1 Т3 — «Протоколы» по варианту Б r3 (Д2, Claude Design export-2026-10-28-it1-d2r3; кадры Пр1, Пр2, Т2, О1–О4,
Т3; слова владельца — k3/stand/logs/VIDEO-FEEDBACK-2026-10-27.md №11–№15).

* №11 — схема «Устройства → Сервер → Выходы» над списком: входы — установленные протоколы (gw/inbounds), ключи выходов —
  lib/kaskad/gw/exits.py (KINDS awg: AmneziaWG и WireGuard; xray: PROTO_TITLES); «и входом, и выходом» — is-both, «только
  для выходов» — тонкий пунктир is-only (и в легенде); колонка «Для чего».
* №12 — раскрытая строка: описание и строка кнопок; точки устройств (зелёная — на связи) и «N из M на связи».
* №15 — все действия кнопками, «⋯» нет; «Удалить протокол» — с подтверждением на месте. Ничего не потеряно: Включить /
  Выключить, Сменить порт, Перезапустить, Применить настройки, свои действия (VLESS, Hysteria2), Журнал, Подпись…,
  переключатели протокола, движок AmneziaWG и сертификат SSTP/OC — в раскрытой строке.
* №14 — окно установки: «Рекомендуемый / Прежний / Ближайший свободный / Случайный свободный / Свой порт», у каждого —
  свободен ли он; номер — на «Установить на N»; занятый — «Установить» недоступна.
* телефон (Т2) — тот же вид: строки раскрываются на месте, детали во весь экран нет; из поиска — строка раскрыта.

ux.js и куски app.js — в node (учебный DOM tests/test_333d_ux.prelude, tests/jsparts.py); вёрстка — детектор
tools/k3_layout_check.mjs, удержание подтверждения — tools/k3_hold_check.mjs (tests/test_335_hold.py)."""
import json
import re
import shutil
import unittest

from kaskad.gw import exits
from tests import jsparts
from tests.test_332f_give import run_node
from tests.test_333d_ux import prelude, read

APP = read("app.js")
HAS_NODE = bool(shutil.which("node"))

ROW = r"""
const base = { kind: "wg", title: "WireGuard", engine: "", desc: "Быстрый туннель.", enabled: true, devices: 3, sessions: 1, ports: ["51820/udp"], running: true };
const calls = [];
const opts = (o) => Object.assign({ lang: "ru", row: true, onToggle: (on) => calls.push("toggle:" + on), onPort: () => calls.push("port"), onLog: () => calls.push("log"),
  onRestart: () => Promise.resolve({}), onApply: () => calls.push("apply"), onDelete: () => { calls.push("delete"); return Promise.resolve({}); },
  onOption: (k, on) => calls.push("opt:" + k + ":" + on), onOpen: (on) => calls.push("open:" + on) }, o);
const btns = (c) => byCls(c, "ux-pc__btns")[0].children.map((b) => b.textContent);
const menus = (c) => c.all((n) => n.getAttribute && n.getAttribute("aria-haspopup") === "menu").length;
const del = (c) => byCls(c, "ux-pc__del")[0];
"""


@unittest.skipUnless(HAS_NODE, "нет node")
class RowCardTest(unittest.TestCase):
    """ProtocolCard o.row (ux.js): строка списка «Протоколов» — все действия кнопками, «⋯» нет (№12, №15)."""

    def run_row(self, body):
        return run_node(prelude() + ROW + "(async () => {\n" + body + "\n})();")

    def test_every_action_is_a_button(self):
        out = self.run_row(r"""
const r = {};
const mine = [{ label: "Маскировка VLESS…", on: () => calls.push("mask") }, { label: "Новый ключ Reality…", on: () => calls.push("rekey") },
  { label: "Подпись…", on: () => calls.push("rename"), tail: true }];
const wg = X.protocolCard(base, opts({ extraMenu: [{ label: "Подпись…", on: () => {}, tail: true }] }));
r.wg = btns(wg); r.wgMenu = menus(wg); r.wgDel = del(wg).textContent;
const vl = X.protocolCard(Object.assign({}, base, { kind: "vless", title: "VLESS · Reality", fixedPort: true, ports: ["443/tcp"] }), opts({ extraMenu: mine }));
r.vl = btns(vl); r.vlMenu = menus(vl);
btnText(vl, "Маскировка").click(); btnText(vl, "Подпись").click(); btnText(vl, "Журнал").click();
const off = X.protocolCard(Object.assign({}, base, { enabled: false, running: false }), opts({}));
r.off = btns(off);
const down = X.protocolCard(Object.assign({}, base, { running: false, fail: { why: "other", human: "Служба не запустилась" } }), opts({}));
r.down = btns(down);
const busy = X.protocolCard(Object.assign({}, base, { running: false, fail: { why: "busy", port: 51820, proto: "udp" } }), opts({}));
r.busy = btns(busy);
const cert = X.protocolCard(Object.assign({}, base, { kind: "hy2", title: "Hysteria2", running: true, fail: { why: "cert", human: "Сертификат негоден" } }), opts({}));
r.cert = btns(cert);
// переключатели протокола (SOCKS5 UDP, L2TP без шифрования, маскировка Hysteria2) — кнопками role="switch"
const sw = X.protocolCard(Object.assign({}, base, { kind: "socks5", title: "SOCKS5", switches: [{ key: "udp", title: "UDP через SOCKS5", sub: "Нужен программам", on: true }] }), opts({}));
const s = sw.all((n) => n.getAttribute && n.getAttribute("role") === "switch")[0];
r.sw = [s.textContent, s.getAttribute("aria-checked"), s.getAttribute("title"), s.className.indexOf("ux-btn--sm") >= 0];
s.click();
r.calls = calls;
console.log(JSON.stringify(r));
""")
        self.assertEqual(out["wg"], ["Выключить", "Сменить порт", "Журнал", "Подпись…"])
        self.assertEqual((out["wgMenu"], out["vlMenu"]), (0, 0))                 # «⋯» нет
        self.assertEqual(out["wgDel"], "Удалить протокол")
        # порядок макета Пр1: главные · свои · Журнал · Подпись…; у VLESS TLS порта нет (fixedPort)
        self.assertEqual(out["vl"], ["Выключить", "Маскировка VLESS…", "Новый ключ Reality…", "Журнал", "Подпись…"])
        self.assertEqual(out["off"], ["Включить", "Сменить порт", "Журнал"])
        # не работает — «Перезапустить» и то, что было в «⋯» (Выключить, Сменить порт)
        self.assertEqual(out["down"], ["Перезапустить", "Выключить", "Сменить порт", "Журнал"])
        self.assertEqual(out["busy"], ["Сменить порт", "Выключить", "Журнал"])
        self.assertEqual(out["cert"], ["Применить настройки", "Выключить", "Сменить порт", "Журнал"])
        self.assertEqual(out["sw"], ["UDP через SOCKS5", "true", "Нужен программам", True])
        self.assertEqual(out["calls"], ["mask", "rename", "log", "opt:udp:false"])

    def test_delete_asks_in_place(self):
        out = self.run_row(r"""
const c = X.protocolCard(base, opts({ delText: "Устройства на этом протоколе (3) не будут подключаться, пока его не поставят снова." }));
document.body.appendChild(c);
const r = {};
const open = () => del(c).click();
open();
const q = byCls(c, "ux-confirm")[0];
r.cls = q.className; r.role = q.getAttribute("role"); r.text = byCls(q, "ux-confirm__t")[0].textContent; r.f0 = document.activeElement.textContent;
fire(document.activeElement, "keydown", { key: "Escape" });
r.esc = [byCls(c, "ux-confirm").length, document.activeElement === del(c)];
open(); btnText(byCls(c, "ux-confirm")[0], "Отмена").click();
r.cancel = [byCls(c, "ux-confirm").length, document.activeElement === del(c)];
open(); c.all((n) => n.tagName === "BUTTON" && n.textContent === "Удалить")[0].click(); await flush();
r.done = [byCls(c, "ux-confirm").length, document.activeElement === del(c)];
r.calls = calls;
const plain = X.protocolCard(Object.assign({}, base, { devices: 0 }), opts({}));
del(plain).click();
r.plain = byCls(byCls(plain, "ux-confirm")[0], "ux-confirm__t")[0].textContent;
console.log(JSON.stringify(r));
""")
        self.assertEqual(out["cls"], "ux-confirm ux-confirm--row")
        self.assertEqual(out["role"], "alertdialog")
        self.assertEqual(out["text"], "Удалить WireGuard? Устройства на этом протоколе (3) не будут подключаться, пока его не поставят снова.")
        self.assertEqual(out["f0"], "Отмена")                                 # безопасный выбор — первым
        for k in ("esc", "cancel", "done"):
            self.assertEqual(out[k], [0, True], k)                             # фокус — на «Удалить протокол», откуда пришли
        self.assertEqual(out["calls"], ["delete"])
        self.assertEqual(out["plain"], "Удалить WireGuard? Устройства с ним перестанут подключаться.")

    def test_row_head_and_body(self):
        out = self.run_row(r"""
const cell = (cls, text) => { const n = document.createElement("span"); n.className = cls; n.textContent = text; return n; };
const c = X.protocolCard(Object.assign({}, base, { kind: "l2tp", title: "L2TP/IPsec", engine: "", ports: ["1701/udp", "500/udp", "4500/udp"] }),
  opts({ cells: { dev: cell("kb2-dv", "1 из 3 на связи"), roles: cell("kb2-rl", "устройства"), meta: cell("kb2-pl__meta", "Порт: 1701, 500, 4500/udp") } }));
const head = byCls(c, "kb2-pl__r")[0], body = byCls(c, "kb2-pl__b")[0];
const r = { tag: head.tagName, cls: c.className, kids: head.children.map((n) => n.className.split(" ")[0]), port: byCls(c, "kb2-port")[0].getAttribute("title"),
  st: byCls(c, "kb2-st")[0].className, exp0: head.getAttribute("aria-expanded"), hid0: body.hidden, ctl: head.getAttribute("aria-controls") === body.getAttribute("id") };
head.click();
r.exp1 = head.getAttribute("aria-expanded"); r.hid1 = body.hidden; r.open = c.classList.contains("is-open");
r.body = body.children.map((n) => n.className.split(" ")[0]);
r.desc = byCls(body, "ux-pc__d")[0].textContent;
// поломка — красной строкой под строкой (в раскрытой — в описании), у VLESS — причина Reality строкой
const down = X.protocolCard(Object.assign({}, base, { running: false, fail: { why: "other", human: "Служба не запустилась" } }), opts({}));
const al = byCls(down, "kb2-pl__al")[0];
r.alert = [al.textContent, al.hidden]; byCls(down, "kb2-pl__r")[0].click(); r.alertOpen = al.hidden;
const vl = X.protocolCard(Object.assign({}, base, { kind: "vless", title: "VLESS · Reality" }), opts({ hint: { text: "нет домена — запасной вариант", rec: false } }));
r.hint = byCls(vl, "ux-pc__hint")[0].textContent;
r.calls = calls;
console.log(JSON.stringify(r));
""")
        self.assertEqual(out["tag"], "BUTTON")                                # строка — настоящая кнопка (Enter, пробел)
        self.assertIn("ux-pc--row", out["cls"])
        self.assertEqual(out["kids"], ["kb2-pl__ic", "kb2-pl__n", "kb2-dv", "kb2-port", "kb2-rl", "kb2-pl__chev"])
        self.assertEqual(out["port"], "3 порта: 1701/udp — L2TP, 500/udp и 4500/udp — IPsec")
        self.assertEqual(out["st"], "kb2-st kb2-st--ok")
        self.assertEqual((out["exp0"], out["hid0"], out["ctl"]), ("false", True, True))
        self.assertEqual((out["exp1"], out["hid1"], out["open"]), ("true", False, True))
        self.assertEqual(out["body"], ["ux-pc__desc", "kb2-pl__meta", "ux-pc__acts"])   # описание · порт и «для чего» · кнопки
        self.assertEqual(out["desc"], "Быстрый туннель.")
        self.assertEqual(out["alert"], ["Служба не запустилась", False])
        self.assertTrue(out["alertOpen"])
        self.assertEqual(out["hint"], "нет домена — запасной вариант")
        self.assertEqual(out["calls"], ["open:true", "open:true"])


HOST = r"""
const t = (k, v) => k + (v ? JSON.stringify(v) : ""), tn = (k, n) => k + "#" + n;
const DASH = "—", arr = (x) => (Array.isArray(x) ? x : []), isObj = (x) => !!x && typeof x === "object" && !Array.isArray(x);
const known = (x) => x !== null && x !== undefined, fmtInt = (n) => String(n);
const validPort = (v) => /^\d{1,5}$/.test(String(v).trim()) && Number(v) >= 1 && Number(v) <= 65535;
let OFF = false, LIVE = {}, R = { people: { loaded: false } };
const liveOff = () => OFF, LV = () => LIVE, ibSessions = (r) => (OFF ? null : r.sessions);
"""


EL_JS = r"""const el = (tag, a, ...kids) => { const n = document.createElement(tag); Object.entries(a || {}).forEach(([k, v]) => { if (v === null || v === undefined) return; if (k === "class") n.className = v; else if (k === "text") n.textContent = v; else n.setAttribute(k, v); });
  kids.flat().forEach((c) => { if (c) n.appendChild(c); }); return n; };
"""


@unittest.skipUnless(HAS_NODE, "нет node")
class HostLogicTest(unittest.TestCase):
    def run_app(self, names, body, pre=""):
        parts = [pre]
        for n in names:
            try:
                parts.append(jsparts.function(APP, n))
            except KeyError:
                parts.append(jsparts.const(APP, n))
        return run_node(prelude() + HOST + "\n".join(parts) + "\n(async () => {\n" + body + "\n})();")

    def test_online_of_a_protocol(self):
        """«N из M на связи»: устройства из live.json (online и inbound), в опросе — из GET people; без устройств — сессии
        входа (SOCKS5), не больше M; сборщик молчит — N неизвестно."""
        out = self.run_app(["ibOnline"], r"""
const r = {};
LIVE = { devices: { a: { online: true, inbound: "wg" }, b: { online: true, inbound: "awg2" }, c: { online: false, inbound: "wg" }, d: { online: true, inbound: "wg" } } };
r.wg = ibOnline({ kind: "wg", devices: 3, sessions: 5 });
r.awg = ibOnline({ kind: "awg2", devices: 4, sessions: 0 });
r.cap = ibOnline({ kind: "wg", devices: 1, sessions: 0 });
r.socks = ibOnline({ kind: "socks5", devices: 3, sessions: 7 });
r.none = ibOnline({ kind: "socks5", devices: 3, sessions: null });
LIVE = {}; R = { people: { loaded: true, data: { devices: [{ live: { online: true, inbound: "hy2" } }, { live: null }] } } };
r.poll = ibOnline({ kind: "hy2", devices: 2, sessions: 0 });
r.noDevs = ibOnline({ kind: "hy2", devices: null, sessions: 0 });
OFF = true;
r.off = ibOnline({ kind: "wg", devices: 3, sessions: 1 });
console.log(JSON.stringify(r));
""")
        self.assertEqual(out["wg"], {"n": 2, "m": 3})
        self.assertEqual(out["awg"], {"n": 1, "m": 4})
        self.assertEqual(out["cap"], {"n": 1, "m": 1})
        self.assertEqual(out["socks"], {"n": 3, "m": 3})
        self.assertEqual(out["none"], {"n": 0, "m": 3})
        self.assertEqual(out["poll"], {"n": 1, "m": 2})
        self.assertEqual(out["noDevs"], {"n": 1, "m": None})
        self.assertEqual(out["off"], {"n": None, "m": 3})

    def test_device_dots(self):
        """Сначала графика, потом цифры: точки (зелёная — на связи), затем «N из M на связи»; больше 12 — полоса долей."""
        out = self.run_app(["IB_PIPS", "ibDevNode"], r"""
const shape = (v) => { V = v; const n = ibDevNode({ kind: "wg" }); return n.children.map((k) => [k.className, k.children.map((i) => i.className || "").join(","), k.textContent]); };
console.log(JSON.stringify({ two: shape({ n: 2, m: 3 }), unknown: shape({ n: null, m: 2 }), zero: shape({ n: 0, m: 0 }), none: shape({ n: 0, m: null }),
  many: shape({ n: 5, m: 20 }), tip: (V = { n: 1, m: 2 }, ibDevNode({ kind: "wg" }).children[1].getAttribute("title")) }));
""", pre=EL_JS + r"""
let V = null;
const blk = (c, b, o) => el(o.tag, { class: o.cls }, b(c()));
const ibOnline = () => V;
""")
        self.assertEqual(out["two"], [["kb2-pips", "is-on,is-on,", ""], ["kb2-dv__t", "", 'ib.devOn{"n":"2","m":"3"}']])
        self.assertEqual(out["unknown"], [["kb2-pips", ",", ""], ["kb2-dv__t", "", "ib.devN#2"]])
        self.assertEqual(out["zero"], [["kb2-dv__t", "", "ib.devNone"]])
        self.assertEqual(out["none"], [["kb2-dv__t", "", "—"]])
        self.assertEqual(out["many"][0][0], "kb2-dvbar")
        self.assertEqual(out["many"][1][2], 'ib.devOn{"n":"5","m":"20"}')
        self.assertEqual(out["tip"], 'ib.devOn{"n":"1","m":"2"}')                 # узкая колонка — многоточие с подсказкой

    def test_port_options(self):
        """№14 (О1–О4, Т3): варианты порта из ответа сервера (ops.port_offer) и какой выбран сразу."""
        out = self.run_app(["ibPortRecs", "ibPortOpts"], r"""
const sh = (m) => ({ sel: m.sel, opts: m.opts.map((o) => [o.kind, o.port, o.free, o.why].join("|")) });
const rec = (p, why) => ({ port: p, free: !why, why: why || "" });
console.log(JSON.stringify({
  free: sh(ibPortOpts({ port: 1194, recommended: [rec(1194)] }, 41873)),
  busy: sh(ibPortOpts({ port: 1195, recommended: [rec(1194, "его слушает openvpn")] }, "")),
  kept: sh(ibPortOpts({ port: 30001, recommended: [rec(1194)], kept: 30001, kept_busy: "" }, 41873)),
  keptBusy: sh(ibPortOpts({ port: 1194, recommended: [rec(1194)], kept: 30001, kept_busy: "его занимает протокол шлюза WireGuard" }, "")),
  awg: sh(ibPortOpts({ port: 48123, recommended: [] }, "")),
  none: sh(ibPortOpts(null)), bad: sh(ibPortOpts({ port: "x" })),
}));
""")
        self.assertEqual(out["free"], {"sel": "rec0", "opts": ["rec|1194|true|", "rnd|41873|true|", "own|||"]})
        self.assertEqual(out["busy"], {"sel": "near", "opts": ["rec|1194|false|его слушает openvpn", "near|1195|true|", "rnd|||", "own|||"]})
        self.assertEqual(out["kept"], {"sel": "kept", "opts": ["rec|1194|true|", "kept|30001|true|", "rnd|41873|true|", "own|||"]})
        self.assertEqual(out["keptBusy"]["sel"], "rec0")
        self.assertIn("kept|30001|false|его занимает протокол шлюза WireGuard", out["keptBusy"]["opts"])
        # без рекомендуемых (AmneziaWG) — подобранный сервером случайный, он и выбран
        self.assertEqual(out["awg"], {"sel": "rnd", "opts": ["rnd|48123|true|", "own|||"]})
        self.assertEqual(out["none"], {"sel": "own", "opts": ["own|||"]})
        self.assertEqual(out["bad"], {"sel": "own", "opts": ["own|||"]})

    def test_kept_notes(self):
        """REV-it1-b З-2: прежний порт занят — зелёной «заработают снова» нет, одна заметка без причины (причина — в
        строке варианта «занят: …»); свободен или сервер не ответил — зелёная на месте."""
        out = self.run_app(["ibKeptNotes"], r"""
console.log(JSON.stringify({
  before: ibKeptNotes({ kept: ["30001/udp"] }, null),
  free: ibKeptNotes({ kept: ["30001/udp"] }, { port: 30001, kept: 30001, kept_busy: "" }),
  busy: ibKeptNotes({ kept: ["30001/udp"] }, { port: 1194, kept: 30001, kept_busy: "его занимает протокол шлюза WireGuard" }),
  fixed: ibKeptNotes({ kept: [] }, null), none: ibKeptNotes({ kept: null }, { port: 1194, kept: null, kept_busy: "" }),
}));
""")
        self.assertEqual(out["before"], {"ok": 'ib.keptOpt{"ports":"30001/udp"}', "busy": ""})
        self.assertEqual(out["free"], out["before"])
        self.assertEqual(out["busy"], {"ok": "", "busy": 'ib.keptBusy{"port":"30001"}'})
        self.assertEqual(out["fixed"], {"ok": "ib.keptFixed", "busy": ""})
        self.assertEqual(out["none"], {"ok": "", "busy": ""})
        for lang in ("ru", "en"):
            words = dict(re.findall(r'"(ib\.\w+)": "([^"]+)"', read("i18n-%s.js" % lang)))
            self.assertNotIn("{why}", words["ib.keptBusy"], lang)          # причина — только в строке варианта
            self.assertNotRegex(words["ib.keptBusy"], r"занят|taken", lang)

    def test_flow_scheme(self):
        """№11: схема — входы (установленные, акцентом — те, что бывают и выходом), ключи выходов (пунктиром — только для
        выходов), легенда тем же пунктиром."""
        out = self.run_app(["EXIT_KEYS", "IB_EXIT_OF", "IB_BOTH", "ibFlow"], r"""
const chips = (n) => byCls(n, "kb2-chip").map((c) => c.textContent + (c.className.indexOf("is-both") >= 0 ? "+" : c.className.indexOf("is-only") >= 0 ? "~" : ""));
const inst = [{ kind: "wg", title: "WireGuard" }, { kind: "awg2", title: "AmneziaWG 2.0", shown: "AmneziaWG 2.0" }, { kind: "socks5", title: "SOCKS5" }, { kind: "vless", title: "VLESS", shown: "VLESS" }];
const f = ibFlow(inst, el("span", { class: "tools" }));
const r = { chips: chips(f), lanes: byCls(f, "kb2-lane__t").map((n) => n.textContent), nodes: byCls(f, "kb2-node").map((n) => n.textContent), tools: byCls(f, "tools").length,
  lg: byCls(f, "kb2-lg")[0].children.map((s) => [s.children[0].className || "", s.textContent]), sub: byCls(f, "kb2-h__s").length };
PH = true;
const p = ibFlow([], null);
r.phone = { lg: byCls(p, "kb2-lg")[0].textContent, sub: byCls(p, "kb2-h__s").length, none: byCls(p, "kb2-lane__no").map((n) => n.textContent) };
console.log(JSON.stringify(r));
""", pre=EL_JS + r"""
const txt = (s) => document.createTextNode(s), icon = () => el("i"), hostName = () => "vpn.example.com";
let PH = false; const isPhone = () => PH;
""")
        self.assertEqual(out["chips"], ["WireGuard+", "AmneziaWG 2.0+", "SOCKS5", "VLESS+",
                                        "AmneziaWG+", "WireGuard+", "VLESS+", "Hysteria2+", "Shadowsocks~", "Trojan~", "VMess~"])
        self.assertEqual(out["lanes"], ['ib.flowIn{"n":"4"}', "ib.flowEx#7"])
        self.assertEqual(out["nodes"], ["ib.flowDevsib.flowDevsS", "ib.flowSrvvpn.example.com", "ib.flowExitsib.flowExitsS"])
        self.assertEqual(out["tools"], 1)                                     # кнопки движка AmneziaWG без его строки
        self.assertEqual(out["lg"], [["", "ib.lgBoth"], ["is-only", "ib.lgOnly"]])
        self.assertEqual(out["sub"], 1)
        self.assertEqual(out["phone"], {"lg": "ib.lgBothSib.lgOnlyS", "sub": 0, "none": ["ib.flowInNone"]})


class ExitKeysTest(unittest.TestCase):
    """Ключи выходов на схеме — те, что понимает разбор ключей выходов (gw/exits.py), а входы с меткой «выходы» — из
    канона протоколов панели."""

    def test_exit_keys_match_the_parser(self):
        keys = json.loads(jsparts.const(APP, "EXIT_KEYS").split("=", 1)[1].strip().rstrip(";"))
        self.assertEqual(exits.KINDS, ("awg", "xray"))
        self.assertEqual(set(keys), {"AmneziaWG", "WireGuard"} | set(exits.PROTO_TITLES.values()))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn('return "WireGuard" if wg else', open(exits.__file__, encoding="utf-8").read())      # простой WG — выход awg
        both = dict(re.findall(r'(\w+): "([A-Za-z0-9]+)"', jsparts.const(APP, "IB_EXIT_OF")))
        canon = json.loads(jsparts.const(APP, "PROTO_CANON").split("=", 1)[1].strip().rstrip(";"))
        self.assertTrue(set(both) <= set(canon), both)
        self.assertTrue(set(both.values()) <= set(keys))
        self.assertEqual(set(both), {"wg", "awg1", "awg2", "awg3", "vless", "hy2"})


class PageTest(unittest.TestCase):
    """Страница: схема, список строк, «Можно установить» в две колонки; телефон — тот же вид, деталь во весь экран убрана."""

    def test_page_and_phone(self):
        view = jsparts.function(APP, "ibGridView")
        self.assertIn("const out = [ibFlow(inst, noAwg && !phone ? ibAwgTools() : null)];", view)
        self.assertIn("if (inst.length) out.push(ibList(inst));", view)
        self.assertIn('el("div", { class: "kk-igrid kb2-av" }, avail.concat(na))', view)
        self.assertNotIn("foldGrid", view)
        lst = jsparts.function(APP, "ibList")
        self.assertIn("inst.map((r) => ibUxCard(r, true, true))", lst)
        self.assertNotIn('el("span"),', lst)                                  # шапка без пустых ячеек (PHANTOM ui_audit)
        card = jsparts.function(APP, "ibUxCard")
        self.assertIn("row: !!row, cells: row ? { dev: ibDevNode(r), roles: ibRoles(r.kind), meta: ibMeta(r) } : null,", card)
        self.assertIn('delText: row && Number(r.devices) > 0 ? t("ib.uninstallWarn", { n: fmtInt(r.devices) }) : "",', card)
        self.assertIn("if (row) menu.push(ren);", card)                       # «Подпись…» — после «Журнала»
        self.assertIn("if (isAwgKind(r.kind)) extraMore.push(awgEngine());", card)               # движок AmneziaWG — в строке AWG
        self.assertIn("extraMore.push(ibCertSw(r.kind))", card)                                   # сертификат SSTP/OC
        self.assertIn("switches: (IB_OPTIONS[r.kind] || [])", card)                               # переключатели протокола
        phone = APP[APP.index("PAGES.inbounds.phone = function"):]
        phone = phone[:phone.index("};") + 2]
        self.assertIn("PAGES.inbounds.build(root, n);", phone)
        self.assertIn("ibSetOpen(n.focus, true)", phone)                      # из поиска и «Обзора» — строка раскрыта
        self.assertIn("PAGES.inbounds.findPhone = true;", APP)
        for gone in ("function ibMlist", "function ibDetail", "function ibFocusAddr", "function vlPhone", "const vlHeroLine"):
            self.assertNotIn(gone, APP)
        row = jsparts.function(APP, "ibRow")
        self.assertIn('IB_EXIT_OF[r.kind] ? el("span", { class: "kb2-role kb2-role--x"', row)
        self.assertIn('U.btn(phone ? "" : t("ib.install")', row)

    def test_install_dialog(self):
        inst = jsparts.function(APP, "openInstall")
        self.assertIn('el("div", { class: "kb2-pp", role: "radiogroup", "aria-label": t("ib.port") })', inst)
        self.assertIn('el("input", { type: "radio", name, id: rid', inst)
        self.assertIn('t("ib.installOn", { port: p.port })', inst)
        self.assertIn("okBtn.disabled = p.bad;", inst)
        self.assertIn("API.inbounds.offer(r.kind, { random: 1 })", inst)
        self.assertIn("ask({ port: v })", inst)
        self.assertIn('tip: t("ib.portAnother")', inst)
        self.assertIn('ownIn.setAttribute("aria-invalid"', inst)

    def test_texts_ru_en(self):
        # REV-it1-b (вершина): выходы бывают и российские («Дом») — подпись схемы без «зарубежные» / «abroad»
        for lang, bad in (("ru", r"зарубеж"), ("en", r"abroad|foreign")):
            sub = dict(re.findall(r'"(ib\.\w+)": "([^"]+)"', read("i18n-%s.js" % lang)))["ib.flowExitsS"]
            self.assertNotRegex(sub, bad, lang)
            self.assertRegex(sub, r"интернет|internet", lang)
        keys = ("ib.flowTitle", "ib.flowSub", "ib.flowDevs", "ib.flowDevsS", "ib.flowSrv", "ib.flowExits", "ib.flowExitsS", "ib.flowIn",
                "ib.flowInNone", "ib.flowEx", "ib.lgBoth", "ib.lgOnly", "ib.lgBothS", "ib.lgOnlyS", "ib.colProto", "ib.colDev", "ib.colFor",
                "ib.forDev", "ib.forEx", "ib.forExAlso", "ib.forExTip", "ib.devOn", "ib.devN", "ib.devNone", "ib.ppRec", "ib.ppKept",
                "ib.ppNear", "ib.ppRnd", "ib.ppOwn", "ib.ppFree", "ib.ppBusy", "ib.ppRndHint", "ib.ppRndFail", "ib.ppOwnHint", "ib.portPh",
                "ib.portAnother", "ib.installOn", "ib.keptOpt")
        for lang in ("ru", "en"):
            words = dict(re.findall(r'"(ib\.\w+)": "([^"]+)"', read("i18n-%s.js" % lang)))
            for k in keys:
                self.assertTrue(words.get(k), (lang, k))
            self.assertIn("{port}", words["ib.installOn"])
            self.assertIn("{why}", words["ib.ppBusy"])
            self.assertEqual(words["ib.devN"].count("|"), 2 if lang == "ru" else 1)
        ru = dict(re.findall(r'"(ib\.\w+)": "([^"]+)"', read("i18n-ru.js")))
        self.assertEqual((ru["ib.installOn"], ru["ib.devOn"], ru["ib.colFor"]), ("Установить на {port}", "{n} из {m} на связи", "Для чего"))
        self.assertNotIn("Люди", " ".join(ru[k] for k in keys))

    def test_css(self):
        css = read("ux.css")
        # «только для выходов» — тонкий пунктир и в метке, и в легенде (r3: была двойная рамка)
        self.assertIn(".kb2-chip.is-only { background: none; color: var(--text-2); border: 1px dashed var(--text-3);", css)
        self.assertIn(".kb2-lg i.is-only { background: none; box-shadow: none; border: 1px dashed var(--text-3);", css)
        self.assertIn(".kk-igrid.kb2-av { grid-template-columns: repeat(2, minmax(0, 1fr));", css)
        self.assertIn(".kb2-pl__hd > :first-child { grid-column: 2; }", css)
        self.assertIn(".kb2-pl__hd, .kb2-pl__r { grid-template-columns: 2.25rem minmax(0, 11rem) minmax(0, 1fr) 7.5rem 1.5rem; }", css)   # 768: «N из M» целиком
        self.assertIn(".kb2-pl__meta > .kb2-pl__mk { display: block; }", css)                     # «Порт: 443/tcp» с пробелом
        self.assertIn("@container kb2 (max-width: 719px) {\n  .kk-igrid.kb2-av { grid-template-columns: minmax(0, 1fr); }", css)
        self.assertIn(".kb2-pl__hd, .kb2-pl__r { display: grid; grid-template-columns: 2.25rem 13.75rem minmax(0, 1fr) 7.5rem 12.5rem 1.5rem;", css)
        self.assertIn("@media (max-width: 767px) {\n  .kb2-pp__r { min-height: 56px; }\n  .kb2-pp__b > b { display: grid; }", css)   # Т3: номер — строкой


if __name__ == "__main__":
    unittest.main()

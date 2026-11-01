"""UI-3 ч.2–3 (владелец 20.10): компактные карточки «Протоколов» и движок AmneziaWG в раскрытой карточке AWG.

Карточка протокола в node (ux.js, o.collapsible): свёрнута по умолчанию, нажатие на шапку (и Enter/пробел) раскрывает,
повтор сворачивает; открыто ли — у владельца (o.open / o.onOpen), поэтому состояние переживает пересборку; красная
причина «не работает», плашка danger и o.alertLine видны строкой в свёрнутой; жёлтое — значком у имени, текст в
раскрытой; у Hysteria2 заметка про UDP — одна, внизу раскрытой; кнопки — основные слева, «⋯» справа.
app.js: на «Протоколах» нет отдельного блока движка, он — в подробностях карточки AWG, поиск ведёт к нему.
Живая раскладка (одна высота свёрнутых в ряду, пустоты, 6 протоколов node3373 в первом экране на 1440) —
tools/k3_layout_check.mjs (виды pcrow, pcfirst; раскрытые — вариант «open»)."""
import os
import re
import shutil
import unittest

from tests import jsparts
from tests.test_333d_ux import prelude, read
from tests.test_332f_give import run_node

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(shutil.which("node"), "нет node")
class FoldBehaviourTest(unittest.TestCase):
    def run_cards(self, body):
        return run_node(prelude() + r"""
const plaque = (tone, text, note) => { const n = document.createElement("div"); n.className = "gx-pnote" + (tone ? " gx-pnote--" + tone : ""); n.textContent = text; if (note) n.setAttribute("data-note", note); return n; };
const base = { kind: "wg", title: "WireGuard", engine: "ядро Linux", desc: "Быстрый. Второе предложение описания для раскрытой.", enabled: true, devices: 2, sessions: 0, ports: ["51820/udp"], running: true };
const opts = (o) => Object.assign({ lang: "ru", collapsible: true, onToggle() {}, onPort() {}, onLog() {}, onRestart: () => Promise.resolve({}), onDelete: () => Promise.resolve({}), onOption() {} }, o || {});
const head = (c) => byCls(c, "ux-pc__tog")[0], bodyOf = (c) => byCls(c, "ux-pc__body")[0], alertOf = (c) => byCls(c, "ux-pc__alert")[0] || null;
const st = (c) => ({ exp: head(c).getAttribute("aria-expanded"), open: c.classList.contains("is-open"), hidden: bodyOf(c).hidden, alert: alertOf(c) ? (alertOf(c).hidden ? "hidden" : alertOf(c).textContent) : null });
""" + body)

    def test_toggle_and_memory(self):
        out = self.run_cards(r"""
const seen = [];
const c = X.protocolCard(base, opts({ onOpen: (on) => seen.push(on) }));
document.body.appendChild(c);
const r = { role: head(c).getAttribute("role"), tab: head(c).getAttribute("tabindex"), ctl: head(c).getAttribute("aria-controls") === bodyOf(c).getAttribute("id"), s0: st(c) };
head(c).click(); r.s1 = st(c);
head(c).click(); r.s2 = st(c);
fire(head(c), "keydown", { key: "Enter" }); r.s3 = st(c);
fire(head(c), "keydown", { key: " " }); r.s4 = st(c);
fire(byCls(c, "ux-pc__title")[0], "keydown", { key: "Enter" }); r.s5 = st(c);     // клавиша не на самой шапке — не наша
r.seen = seen;
// пересборка (обновление данных): открыто ли — от владельца карточки
const c2 = X.protocolCard(base, opts({ open: true }));
r.rebuilt = st(c2);
r.sum = byCls(c, "ux-pc__sum")[0].textContent;
r.eng = byCls(c, "ux-pc__eng")[0].textContent;
r.desc = byCls(c, "ux-pc__d")[0].textContent;
r.more = byCls(c, "ux-pc__more").length;
// ряд действий: основные слева, «⋯» справа той же строкой
const acts = byCls(c, "ux-pc__acts")[0];
r.acts = acts.children.map((n) => n.className);
console.log(JSON.stringify(r));
""")
        self.assertEqual((out["role"], out["tab"], out["ctl"]), ("button", "0", True))
        self.assertEqual(out["s0"], {"exp": "false", "open": False, "hidden": True, "alert": None})
        self.assertEqual(out["s1"], {"exp": "true", "open": True, "hidden": False, "alert": None})
        self.assertEqual(out["s2"]["exp"], "false")
        self.assertTrue(out["s3"]["open"])
        self.assertFalse(out["s4"]["open"])
        self.assertFalse(out["s5"]["open"])
        self.assertEqual(out["seen"], [True, False, True, False])
        self.assertEqual(out["rebuilt"], {"exp": "true", "open": True, "hidden": False, "alert": None})
        self.assertEqual(out["sum"], "51820/udp · 2 устр. · 0 сессий")
        self.assertEqual(out["eng"], "ядро Linux")
        self.assertEqual(out["desc"], "Быстрый. Второе предложение описания для раскрытой.")       # целиком, без «Подробнее»
        self.assertEqual(out["more"], 0)
        self.assertEqual(out["acts"], ["ux-pc__btns", "ux-spacer", "ux-menuw"])

    def test_engine_only_when_given(self):
        # UI-A2: моноширинная строка движка — только у AmneziaWG; без движка строки нет совсем (раньше — «—»)
        out = self.run_cards(r"""
const none = Object.assign({}, base, { engine: "" });
const r = { fold: byCls(X.protocolCard(none, opts()), "ux-pc__eng").length,
  flat: byCls(X.protocolCard(none, opts({ collapsible: false })), "ux-pc__eng").length,
  awg: txtOf(byCls(X.protocolCard(Object.assign({}, base, { kind: "awg2", engine: "модуль ядра" }), opts()), "ux-pc__eng")[0]) };
console.log(JSON.stringify(r));
""")
        self.assertEqual(out, {"fold": 0, "flat": 0, "awg": "модуль ядра"})

    def test_engine_line_awg_only(self):
        app = read("app.js")
        out = run_node("const t = (k) => ({ \"awg.kernel\": \"модуль ядра\" })[k] || k;\n" + jsparts.const(app, "isAwgKind") + "\n"
                       + jsparts.const(app, "ibEngineLine") + r"""
const rows = [{ kind: "awg2", core: "kernel", engine: "amneziawg-go" }, { kind: "awg3", core: "userspace", engine: "amneziawg-go" },
  { kind: "wg", engine: "wireguard" }, { kind: "vless", engine: "xray" }, { kind: "hy2", engine: "hysteria" }];
console.log(JSON.stringify(rows.map(ibEngineLine)));
""")
        self.assertEqual(out, ["модуль ядра", "amneziawg-go", "", "", ""])

    def test_red_line_when_folded(self):
        out = self.run_cards(r"""
const r = {};
const down = X.protocolCard(Object.assign({}, base, { running: false, fail: { why: "busy", port: 51820, proto: "udp" } }), opts());
r.busy = st(down);
head(down).click(); r.busyOpen = st(down);
r.reasonInBody = byCls(bodyOf(down), "ux-reason").length;
const other = X.protocolCard(Object.assign({}, base, { running: false, fail: { why: "other", human: "Служба не запустилась: нет файла" } }), opts());
r.other = st(other).alert;
const loud = X.protocolCard(base, opts({ extraBody: [plaque("danger", "Сертификат не читается")] }));
r.loud = st(loud).alert;
const eng = X.protocolCard(Object.assign({}, base, { kind: "awg2" }), opts({ alertLine: ["модуль ядра не собрался"] }));
r.eng = st(eng).alert;
const closed = X.protocolCard(Object.assign({}, base, { running: true, fail: { why: "closed" } }), opts());
r.closed = st(closed).alert;
const yellow = X.protocolCard(base, opts({ extraBody: [plaque("warning", "Порт закрыт у хостера?")] }));
r.yellow = { alert: st(yellow).alert, flag: (byCls(yellow, "ux-pc__flag")[0] || { getAttribute: () => null }).getAttribute("title"),
  inBody: byCls(bodyOf(yellow), "gx-pnote--warning").length };
console.log(JSON.stringify(r));
""")
        self.assertIn("51820", out["busy"]["alert"])
        self.assertEqual(out["busyOpen"]["alert"], "hidden")          # раскрыта — причина целиком в теле, строки нет
        self.assertEqual(out["reasonInBody"], 1)
        self.assertEqual(out["other"], "Служба не запустилась: нет файла")
        self.assertEqual(out["loud"], "Сертификат не читается")
        self.assertEqual(out["eng"], "модуль ядра не собрался")
        self.assertIsNone(out["closed"])                              # жёлтое (нет подключений) — не красной строкой
        self.assertIsNone(out["yellow"]["alert"])
        self.assertEqual(out["yellow"]["flag"], "Порт закрыт у хостера?")
        self.assertEqual(out["yellow"]["inBody"], 1)

    def test_hy2_note_once_at_bottom(self):
        out = self.run_cards(r"""
const kv = document.createElement("div"); kv.className = "gx-kv"; kv.textContent = "Сертификат: свой домен";
const note = plaque("", "Hysteria2 работает по UDP (QUIC). Если в мобильной сети не подключается — используйте VLESS.", "hy2-udp");
const c = X.protocolCard(Object.assign({}, base, { kind: "hy2", title: "Hysteria2", ports: ["443/udp"] }),
  opts({ extraBody: [plaque("warning", "Без домена — самоподписанный сертификат")], extraMore: [kv, note] }));
const desc = byCls(c, "ux-pc__desc")[0];
const hits = c.all((n) => !(n.children || []).length && /UDP/.test(n.textContent || "") && /VLESS/.test(n.textContent || ""));
console.log(JSON.stringify({ n: hits.length, last: desc.children[desc.children.length - 1].getAttribute("data-note"),
  inBody: bodyOf(c).contains(hits[0]), warn: byCls(c, "ux-pc__warn").length, flag: byCls(c, "ux-pc__flag")[0].getAttribute("title") }));
""")
        self.assertEqual(out["n"], 1)
        self.assertEqual(out["last"], "hy2-udp")
        self.assertTrue(out["inBody"])
        self.assertEqual(out["warn"], 0)                               # жёлтой строки нет: предупреждение — плашкой
        self.assertEqual(out["flag"], "Без домена — самоподписанный сертификат")

    def test_phone_detail_unchanged(self):
        """Без o.collapsible (деталь протокола на телефоне) — карточка как была: шапка, «Подробнее», без шапки-кнопки."""
        out = self.run_cards(r"""
const kv = document.createElement("div"); kv.className = "gx-kv"; kv.textContent = "x";
const c = X.protocolCard(base, Object.assign(opts({ extraMore: [kv] }), { collapsible: false }));
console.log(JSON.stringify({ tog: byCls(c, "ux-pc__tog").length, more: byCls(c, "ux-pc__more").length, fold: c.classList.contains("ux-pc--fold") }));
""")
        self.assertEqual(out, {"tog": 0, "more": 1, "fold": False})


class AwgEngineTest(unittest.TestCase):
    """UI-3 ч.3: движок AmneziaWG, экспорт и импорт — в подробностях раскрытой карточки AWG, не отдельным блоком."""

    def test_no_separate_block(self):
        app = read("app.js")
        build = app[app.index("PAGES.inbounds = {"):app.index("// ── Протоколы на телефоне")]
        view = jsparts.function(app, "ibGridView")                     # отрисовка списка (UI-3 круг 2 — отдельной функцией)
        self.assertNotIn("awgSection", app)
        self.assertNotIn("awgMrow", app)
        self.assertIn("root.append(note, ovMtu(), grid);", build)
        self.assertIn("ibGridView);", build)
        # Т3 (Б r3): установленные — списком строк (ibList → ibUxCard(r, true, true), раскрываются на месте), не сеткой
        self.assertIn("inst.map((r) => ibUxCard(r, true, true))", jsparts.function(app, "ibList"))
        self.assertIn("if (inst.length) out.push(ibList(inst));", view)
        self.assertNotIn("foldGrid", view)
        self.assertIn("ibRow", view)                                   # «Можно установить» — строками
        phone = app[app.index("PAGES.inbounds.phone = function"):]
        self.assertIn("PAGES.inbounds.build(root, n);", phone[:400])   # телефон — тот же вид (Т2), движок — там же

    def test_in_awg_card(self):
        app = read("app.js")
        card = jsparts.function(app, "ibUxCard")
        self.assertIn("if (isAwgKind(r.kind)) extraMore.push(awgEngine());", card)
        self.assertIn("alertLine: isAwgKind(r.kind) && R.awg.loaded ? awgWarnOf(R.awg.data) : []", card)
        # 3.3.7.15 V1: раскрытие ещё и спрашивает «Порт · MTU · TLS» и «Применено …» (psPeek, дорисовка на месте)
        self.assertIn("collapsible: !!fold && !row, open: ibOpen.has(r.kind), onOpen: (open) => { ibSetOpen(r.kind, open); if (open && psCan(r)) psPeek(r.kind); }", card)
        self.assertIn("row: !!row,", card)                             # строка списка раскрывается так же (ux.js rowWrap)
        eng = jsparts.function(app, "awgEngine")
        self.assertIn('"data-find": "inbounds.awg"', eng)
        for k in ("awg.toProgram", "awg.toKernel", "awg.export", "awg.import", "awg.engineIs", "awg.short"):
            self.assertIn('t("%s"' % k, eng)
        self.assertNotIn("U.sec(", eng)                                # без подсветки отдельной карточкой
        self.assertIn('U.al("danger"', eng)                            # предупреждения движка — красным
        self.assertTrue(re.search(r'const isAwgKind = \(k\) => /\^awg\\d\$/', app))
        for lang in ("ru", "en"):
            src = read("i18n-%s.js" % lang)
            for k in ("awg.engineIs", "awg.short"):
                self.assertIn('"%s":' % k, src)

    def test_search_opens_awg_card(self):
        app = read("app.js")
        self.assertIn('{ id: "inbounds.awg", kind: "card", label: "awg.title", hint: "awg.desc", syn: "find.awgSyn", need: "awg", at: { focus: "inbounds.awg" } }', app)
        self.assertIn('if (n && n.focus === "inbounds.awg") ibWantAwg = true;', app)
        self.assertIn("if (ibWantAwg && R.inbounds.loaded) { const k = awgFocusKind(); ibWantAwg = false; if (k) ibSetOpen(k, true); }", app)
        # телефон (Т3: детали во весь экран нет) — тот же build: «движок AmneziaWG» раскрывает строку AWG, протокол — свою
        phone = app[app.index("PAGES.inbounds.phone = function"):]
        phone = phone[:phone.index("};") + 2]
        self.assertIn("if (n && PROTO_CANON.indexOf(n.focus) >= 0) ibSetOpen(n.focus, true);", phone)
        self.assertIn("PAGES.inbounds.build(root, n);", phone)
        self.assertNotIn("function ibDetail", app)
        for lang in ("ru", "en"):
            syn = re.search(r'"find\.awgSyn": "([^"]+)"', read("i18n-%s.js" % lang)).group(1)
            self.assertIn("kernel", syn)

    def test_open_memory_is_safe(self):
        app = read("app.js")
        self.assertIn('const IB_OPEN_KEY = "kaskad-ib-open";', app)
        i = app.index("const ibOpen = new Set(")
        self.assertIn("try {", app[i:i + 300])
        fn = jsparts.function(app, "ibSetOpen")
        self.assertIn("try { localStorage.setItem(IB_OPEN_KEY", fn)


if __name__ == "__main__":
    unittest.main()

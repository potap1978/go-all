"""3.3.7.15 Д1 — доводка вида №13 r2 по сверке с Claude Design (stand/logs/D-3315-view-n13.md) и общее подменю.

Ядро: строки перезапуска «Что изменится» начинаются с поля («Порт: …», «MTU: …», «Порт и TLS: …»), а не с протокола —
протокол уже в заголовке окна (Н1). Панель (чистые части app.js в node, как tests/test_r15_view.py): VLESS · TLS / SOCKS5 /
Hysteria2 без жёстких пометок применяются из окна (Н14), своё 443 перед сайтом — предупреждением, а не «свободен» (Е11),
после ошибки «Применить» недоступна до правки (Е5, Е10), отметка сети шлюза не у самого края полосы (Ш3). Разметка и
стили — строками (pset.css, components.css, ovb.js)."""

import json
import os
import unittest

from kaskad import i18n
from kaskad.gw import ops
from tests.test_r15_view import STUBS, read, run_node
from tests import jsparts


class RestartWordsTest(unittest.TestCase):
    """Н1: «Порт: без перезапуска …», «MTU: меняется на ходу …» — с поля; одна перезагрузка на два поля — «Порт и TLS»."""

    def rows(self, kind, fields, lang="ru"):
        with i18n.use_lang(lang):
            return [(r["what"], r["word"], r["text"]) for r in ops._restart_rows({}, kind, fields)["rows"]]

    def test_rows_start_with_the_field(self):
        self.assertEqual(self.rows("wg", ["port", "mtu"]), [
            ("port", "syncconf", "Порт: без перезапуска — новый порт подхватывается на ходу."),
            ("mtu", "live", "MTU: меняется на ходу, без перезапуска.")])
        self.assertEqual(self.rows("wg", ["port", "mtu"], "en"), [
            ("port", "syncconf", "Port: no restart — the new port is taken on the fly."),
            ("mtu", "live", "MTU: changes on the fly, no restart.")])

    def test_one_restart_of_two_fields_is_one_line(self):
        self.assertEqual(self.rows("sstp", ["port", "tls"]), [
            ("port", "sstp", "Порт и TLS: перезапуск accel-ppp — на несколько секунд прервутся и подключения L2TP и PPTP.")])
        self.assertEqual(self.rows("vless", ["tls"], "en")[0][2].split(":")[0], "TLS")
        self.assertEqual(self.rows("ocserv", ["port", "tls"], "en")[0][2].split(":")[0], "Port and TLS")

    def test_no_title_prefix_in_any_word(self):
        for kind, fields in (("awg2", ["port", "mtu"]), ("openvpn", ["port"]), ("ocserv", ["port"]), ("hy2", ["port"]),
                             ("socks5", ["port"]), ("vless", ["port", "tls"])):
            for lang in ("ru", "en"):
                for what, word, text in self.rows(kind, fields, lang):
                    head = text.split(":")[0]
                    self.assertIn(head, ("Порт", "MTU", "TLS", "Порт и TLS", "Port", "Port and TLS"), (kind, lang, text))
                    self.assertNotIn("{", text)


class WindowLogicTest(unittest.TestCase):
    """Н14 (решение координатора к вопросу 5): via_xray без жёстких пометок и без задетого администратора — окно и есть
    подтверждение; Е11: свой 443 перед сайтом по плану сервера (front)."""

    @classmethod
    def setUpClass(cls):
        js = read("app.js")
        cls.parts = "\n".join([STUBS % read("i18n-ru.js")] + [jsparts.function(js, "psHit"), jsparts.const(js, "psDirect"),
                                                             jsparts.const(js, "psFrontAt")])

    def test_direct_only_for_xray_without_hard_marks_and_hit(self):
        out = run_node(self.parts + r"""
const x = { via_xray: true, need_yes: true, reissue: { hard: [], soft: [{ id: "d1" }] }, admin_hit: null, hit_text: "" };
console.log(JSON.stringify([psDirect(x), psDirect(Object.assign({}, x, { reissue: { hard: [{ id: "d1" }], soft: [] } })),
  psDirect(Object.assign({}, x, { admin_hit: "pool" })), psDirect(Object.assign({}, x, { hit_text: "Вы подключены через VLESS" })),
  psDirect(Object.assign({}, x, { via_xray: false })), psDirect(null), psDirect({ via_xray: true })]));
""")
        self.assertEqual(out, [True, False, False, False, False, False, True])

    def test_front_at_own_port(self):
        out = run_node(self.parts + r"""
const p = { front: true, changed: { port: { old: 4443, new: 443 } } };
console.log(JSON.stringify([psFrontAt(p, "443"), psFrontAt(p, " 443 "), psFrontAt(p, "8443"), psFrontAt(Object.assign({}, p, { front: false }), "443"),
  psFrontAt(null, "443"), psFrontAt({ front: true }, "443")]));
""")
        self.assertEqual(out, [True, True, False, False, False, False])


class WindowMarkupTest(unittest.TestCase):
    """Разметка окна и строк: подвал (Н14, Е5, Е10, Е11), полоска TLS (Н3), подтверждение (Н10), полоса сети (Ш3, Ш4),
    «Обзор» (О1), «Пользователи» (П1, П2), подменю (вопрос V2)."""

    @classmethod
    def setUpClass(cls):
        cls.app, cls.css, cls.comp = read("app.js"), read("pset.css"), read("components.css")
        cls.ru, cls.en = read("i18n-ru.js"), read("i18n-en.js")

    def test_footer_of_the_window(self):
        a = self.app
        self.assertIn("if (yes === undefined && psDirect(st.plan)) yes = true;", a)                       # Н14
        self.assertIn('b.n && p && p.need_yes && !psDirect(p) ? t("ps.footYes") : ""', a)
        self.assertIn('U.btn(t("ps.safeBtn"), "tertiary", null, { md: true, dis: true, tip: t("ps.noSafeFront") })', a)   # Е11
        self.assertIn('front && b.n ? t("ps.noSafeFront")', a)
        self.assertIn("const dis = b.bad || b.empty || !b.n || st.lock;", a)                              # Е5, Е10
        self.assertIn('st.lock = true; showErr(t("ps.changedElse"), U.btn(t("ps.reload")', a)
        self.assertIn("function changed() { errBox.hidden = true; st.lock = false;", a)
        self.assertIn('front ? t("ps.ownFront")', a)                                                       # Е11 поле
        for key, ru, en in (("ps.reload", "Обновить", "Reload"), ("ps.noSafeFront", "Для перехода на TLS отката нет", "No rollback for the switch to TLS"),
                            ("ps.ownFront", "занят сайтом — VLESS встанет перед ним", "taken by the site — VLESS will stand in front of it")):
            self.assertIn('"%s": "%s"' % (key, ru), self.ru)
            self.assertIn('"%s": "%s"' % (key, en), self.en)
        self.assertIn(".kb2-pp__s.is-warn { color: var(--warning-text); }", self.css)

    def test_tls_strip_gap_chip_and_arrow(self):
        self.assertIn('const tlsBox = el("div", { class: "gx-tlsb" });', self.app)
        self.assertIn(".gx-tlsb { display: grid; gap: 10px; min-width: 0; }", self.css)
        self.assertIn(".gx-dlg--pset .kb2-pp__r .gx-chip--tag, .gx-dlg--pset .gx-radio__i .gx-chip--tag { background: var(--surface-3);", self.css)
        self.assertIn(".gx-pflow__l > .gx-pflow__a { width: 22px; height: 22px; padding: 0; border-radius: 50%;", self.css)

    def test_confirm_hint_above_buttons_on_phone(self):
        self.assertIn('hint = el("span", { class: "gx-hint gx-pset__need" });', self.app)
        self.assertIn('[data-device="phone"] .gx-dlg--price .gx-dlg__foot > .gx-pset__need { order: 1; }', self.css)

    def test_gateway_bar(self):
        self.assertIn('n.style.setProperty("left", "calc(8px + (100% - 16px) * " + (left / 100).toFixed(4) + ")")', self.app)
        self.assertIn('short = a === "10" ? "10/8" : a === "172" ? "172.16/12" : "192.168/16"', self.app)
        self.assertIn(".gx-gwr { container: gwr / inline-size; }", self.css)
        self.assertRegex(self.css, r"@container gwr \(max-width: 520px\) \{\s*\.gx-gwr__bar \{ grid-template-columns: repeat\(3, minmax\(0, 1fr\)\); \}")

    def test_wait_lines(self):
        self.assertIn(".kb2-wait { align-items: flex-start; white-space: normal; overflow: visible;", self.css)       # Н8
        self.assertIn('add({ key: "wait:" + w.kind, tone: "warning", icon: "key", glyph: "key", ', self.app)        # О1
        ovb = read(os.path.join("pages", "ovb.js"))
        self.assertIn('K.icon(r.glyph || (r.tone === "danger" ? "circle-exclamation"', ovb)
        # П1: колонка имени шире при пометках, слова целиком или значок
        self.assertIn('if (list.querySelector(".gx-xt__nl > .gx-wait")) { tb.classList.add("kk-pp-wait"); psWaitFit(tb); }', self.app)
        self.assertIn(".kk-dn--md .gx-xt--k1.kk-pp-wait > * { --pnw: clamp(15.5rem, 100cqw - 18.625rem - 300px, 19.5rem); }", self.css)
        self.assertIn(".kk-dn--md .gx-xt--k1.kk-pp-wait, .kk-dn--lg .gx-xt--k1.kk-pp-wait { --cc-min: 96; }", self.css)
        self.assertIn(".gx-chip.gx-wait.is-icon > .gx-wait__t { display: none; }", self.css)
        # П2: телефон — короче словом, как в таблице
        self.assertIn(".kk-prow__t > .gx-chip[data-row]::after { content: attr(data-row); }", read("people.css"))

    def test_settings_subnav_is_compact(self):
        self.assertIn(".gx-subnav { position: sticky; top: 0; display: grid; gap: var(--sp-2); min-width: 0; }", self.comp)
        self.assertIn(".gx-subnav__i--soon .gx-subnav__l { flex: 1 1 auto; max-width: 100%; }", self.comp)


class ApplyResultTest(unittest.TestCase):
    """REV-3315-view С-1, З-4: итог применения — тост словами сервера. Окно сети показывает предупреждения записи сверх плана
    («"Вернуть прежнюю сеть" для этой смены не будет») тоном «внимание»; after_failed — «внимание» в обоих окнах."""

    LAST = "Не записано «последнее применение» (/var/lib/kaskad/settings-last.json: нет места) — «Вернуть прежнюю сеть» для этой смены не будет."
    HOME = "Подсети шлюза 10.0.1.0/24 совпадают с частыми домашними сетями."
    HIT = "Вы подключены через сеть шлюза: после применения связь может оборваться."

    @classmethod
    def setUpClass(cls):
        js = read("app.js")
        cls.parts = "\n".join([STUBS % read("i18n-ru.js")] + [jsparts.const(js, n) for n in ("psAfterFailed", "gwNetWarns")]
                              + [jsparts.function(js, n) for n in ("gwNetToastOf", "psToastOf")])

    def node(self, body):
        return run_node(self.parts + "\nconst LAST = %s, HOME = %s, HIT = %s;\n" % tuple(json.dumps(x, ensure_ascii=False) for x in (self.LAST, self.HOME, self.HIT)) + body)

    def test_net_warnings_beyond_the_plan(self):
        out = self.node(r"""
const plan = { warnings: [HOME, HIT] };
console.log(JSON.stringify([gwNetWarns({ warnings: [HOME, HIT, LAST], plan }), gwNetWarns({ warnings: [HOME], plan }), gwNetWarns({ warnings: [LAST, 5, "", null] }),
  gwNetWarns(null), gwNetWarns({ warnings: "x" })]));
""")
        self.assertEqual(out, [[self.LAST], [], [self.LAST], [], []])

    def test_net_toast_tone(self):
        out = self.node(r"""
const plan = { warnings: [HOME] };
console.log(JSON.stringify([
  gwNetToastOf({ ok: true, text: "Сеть шлюза сменена.", warnings: [HOME, LAST], plan, pending: null }),
  gwNetToastOf({ ok: true, text: "Сеть шлюза сменена.", warnings: [HOME], plan, pending: null }),
  gwNetToastOf({ ok: true, after_failed: true, text: "Последний шаг не прошёл.", warnings: [HOME, LAST], plan }),
  gwNetToastOf({ ok: true, text: "x", warnings: [HOME], plan, pending: { token: "t" } }),
  gwNetToastOf({ ok: true, text: "x", warnings: [LAST], plan, pending: { token: "t" } }),
  gwNetToastOf({})]));
""")
        self.assertEqual(out, [["Сеть шлюза сменена.", self.LAST, "warning"], ["Сеть шлюза сменена.", "", "success"],
                               ["Последний шаг не прошёл.", self.LAST, "warning"], None, ["x", self.LAST, "warning"],
                               ["Сеть шлюза сменена.", "", "success"]])

    def test_protocol_toast_tone(self):
        out = self.node(r"""
const r = { kind: "wg", title: "WireGuard" };
console.log(JSON.stringify([psToastOf(r, { ok: true, after_failed: true, text: "Порт записан, перезапуск не прошёл.", warnings: ["w1"] }),
  psToastOf(r, { ok: true, text: "", warnings: [] }), psToastOf(r, { ok: true, pending: { token: "t" } })]));
""")
        self.assertEqual(out, [["Порт записан, перезапуск не прошёл.", "w1", "warning"], ["WireGuard: применено.", "", "success"], None])


class WindowFlowTest(unittest.TestCase):
    """REV-3315-view З-1…З-3 (поведение — детектор, правила behave:*, K3_PAGES=behave): окно сети не подтверждает новый план
    само; полоса ожидания не пересобирается опросом; Ш5 на «Выходах» — итог пакета с кнопкой и кнопка у ошибки карточки."""

    @classmethod
    def setUpClass(cls):
        cls.app, cls.ux, cls.mock = read("app.js"), read("ux.js"), read("dev-mock.js")

    def test_net_window_does_not_confirm_a_new_plan_itself(self):
        net = jsparts.function(self.app, "gwNetDraw")
        self.assertNotIn("apply(true)", net)
        self.assertIn("if (st.plan && st.plan.need_yes) body.yes = true;", net)
        self.assertIn('paintFoot(); showErr(t("gw.replan")); return;', net)
        for f in ("i18n-ru.js", "i18n-en.js"):
            self.assertIn('"gw.replan": ', read(f))
        self.assertIn('if (body.dry && psOn("netreplan"))', self.mock)

    def test_pending_bar_is_kept_between_polls(self):
        pr = jsparts.function(self.app, "pendRender")
        self.assertIn('if (was && was.getAttribute("data-pend") === key) { pendPaint(); return; }', pr)
        self.assertIn('"data-pend": key', pr)

    def test_suggest_base_in_the_key_card(self):
        self.assertNotIn("gwSuggestToast(r.suggest_base); return; }", self.app)
        self.assertIn("const act = gwSuggestAct(r && r.suggest_base)", self.app)
        # З-1 REV-3315-fin: сеть сменили из окна — карточка перепроверяет ключ (поведение — behave:sugnet)
        self.assertIn('errFix: (e) => gwSuggestAct(e && isObj(e.data) ? e.data.suggest_base : "", () => ux.recheck())', self.app)
        self.assertIn("on: () => gwNetOpen({ base, onDone: done })", self.app)
        self.assertEqual(self.app.count("gwNetDone(pre, res);"), 2)          # «Применить» и «Вернуть прежнюю сеть»
        self.assertIn("recheck: function () { if (dead || S.adding) { return; } S.err = ''; S.fix = null; S.act = null; changed(); },", self.ux)
        self.assertIn("function noticeDialog({ title, text, list, tone, act, ico })", self.app)
        self.assertIn("S.act = !S.fix && o.errFix ? o.errFix(e) : null;", self.ux)
        self.assertIn("'data-ux-k': 'err-fix'", self.ux)
        self.assertIn("if (same) out.suggest_base = free;", self.mock)
        # Н-2 REV-3315-fin: ядро помечает ключ в сети шлюза уже в предпросмотре — мок так же, кнопка у предупреждения и в
        # окне «Добавить ключ» (после смены сети — перепроверка); поведение — behave:sugnet, behave:sugdlg
        self.assertIn("x.suggest_base = free;", self.mock)
        self.assertIn("S.dupeAct = bad && bad.suggest_base && o.errFix ? o.errFix({ data: { suggest_base: String(bad.suggest_base) } }) : null;", self.ux)
        self.assertIn("if (sug) out.push(gwSuggestBtn(sug, again));", self.app)
        self.assertIn("return keyPreview(r, () => { if (d.el.isConnected) check(); });", self.app)

    def test_net_mock_answers_like_the_core(self):
        # ядро (ops.net_base): warnings = план + запись, plan в ответе — по ним панель отбирает строки сверх плана (С-1)
        self.assertIn("pending: pend2, plan: plan2,", self.mock)
        self.assertIn('warnings: w2.concat(nol ? [ML("Не записано «последнее применение»', self.mock)


class ViewR2Test(unittest.TestCase):
    """Повторная сверка Claude Design (stand/logs/D-3315-view-n13-r2.md). Поведение — детектор: behave:e11, behave:sugbase,
    правило pswait (П1 подпись колонки, П2 телефон); строки ниже — разметка."""

    @classmethod
    def setUpClass(cls):
        cls.app, cls.css, cls.ux = read("app.js"), read("pset.css"), read("ux.js")

    def test_e11_warning_has_a_triangle(self):
        self.assertIn('rows[k].s.replaceChildren(...(w ? [icon("triangle-exclamation", 12)] : []), txt(text));', self.app)
        self.assertIn(".kb2-pp__s.is-warn > .gi {", self.css)

    def test_e5_limit_uses_the_minus_sign(self):
        # уже было: «предел {base} − S4 {s4}» — знак U+2212 (Onest рисует его шириной «+»)
        for f in ("i18n-ru.js", "i18n-en.js"):
            self.assertRegex(read(f), r'"ps\.mtuS4": "[^"]*\{base\} \u2212 S4 \{s4\}"')

    def test_p1_full_activity_label_when_it_fits(self):
        self.assertIn('h.classList.toggle("is-short", full.getClientRects().length > 0 && full.scrollWidth > full.clientWidth + 1);', self.app)
        self.assertNotIn("@container xt (max-width: calc(38.125rem + 324px))", self.css)
        self.assertIn(".kk-dn--md .gx-xt--k1.kk-pp-wait .gx-xt__hc.is-short:not(.is-cc) > .gx-xt__hl.kk-nmd,", self.css)

    def test_p2_phone_row_keeps_the_route_word(self):
        self.assertIn(".kk-prow__t:has(> .gx-waiti) > .kk-ucp { display: none; }", self.css)

    def test_sh5_batch_tone_and_add_locked(self):
        self.assertIn('ico: tone === "warning" ? "triangle-exclamation" : null,', self.app)
        self.assertIn("addBtn.disabled = S.adding || ty === 'empty' || ty === 'bad' || !!S.dupe || (!!S.err && !!S.act);", self.ux)


class SettingsNavToolTest(unittest.TestCase):
    """П-1 REV-3315-view: k3_settings_nav снова ловит переполнение области (прежний fit), подменю меряется отдельно (sub);
    поведение — tests.test_ui5_settings.ChromeTest.test_notify_compact (мутация «подменю +40 px» падает)."""

    def test_fit_is_strict_again(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "tools", "k3_settings_nav.mjs"), encoding="utf-8") as f:
            nav = f.read()
        self.assertIn("return { fit: m.scrollHeight <= m.clientHeight + 1, over: m.scrollHeight - m.clientHeight, sub,", nav)
        self.assertIn("const sn = document.querySelector('nav.gx-subnav[data-subnav=\"settings\"]');", nav)


if __name__ == "__main__":
    unittest.main()

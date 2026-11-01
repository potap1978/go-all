"""UI-1 (вёрстка панели по проверке T-ui-audit): правила, которые легко потерять при следующей правке.
Живая раскладка — k3/stand/ui_audit.js (VOID, ROWSPREAD, PHANTOM, OVER) и tools/k3_layout_check.mjs."""

import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def fn(src, name):
    m = re.search(r"\n  function %s\(.*?\n  }\n" % re.escape(name), src, re.S)
    assert m, name
    return m.group(0)


class ProtocolCardTest(unittest.TestCase):
    def test_details_of_an_inbound_go_to_more_not_body(self):
        """F01: подробности VLESS/Hysteria2 (режим, маска, ключи, сертификат) — в «Подробнее», в extraBody — только
        предупреждения и плашки «что-то не так»."""
        src = read("app.js")
        card = fn(src, "ibUxCard")
        self.assertNotIn("extra.push(...vlessInfo(r))", card)
        self.assertNotIn("extra.push(...hy2Info(r))", card)
        self.assertIn("extraMore", card)
        self.assertIn("extra.push(...info.filter(loud))", card)
        ux = read("ux.js")
        self.assertIn("h('details', { class: 'ux-pc__more' }", ux)
        self.assertNotIn("'ux-pc__more', open", ux)                  # закрыт по умолчанию

    def test_details_named_and_warning_on_view(self):
        """REV-ui1 придирки 2–3: у «Подробнее» имя для диктора с протоколом; предупреждение Hysteria2 (сертификат,
        UDP в мобильных сетях) — строкой на виду в ряду «Подробнее», а не только внутри. Открытое «Подробнее» после
        перерисовки — tests/test_335_hold (inbounds-more)."""
        ux = read("ux.js")
        self.assertIn("'aria-label': t(lang, 'pc.details') + ': ' + r.title", ux)
        self.assertIn("if (o.moreOpen) { det.open = true; }", ux)
        card = fn(read("app.js"), "ibUxCard")
        # UI-2 круг 3 (решение владельца 20.10): у Hysteria2 про UDP жёлтой строки нет — одна заметка внизу «Подробнее»
        # (tests/test_ui2_layout.WarnOrderBehaviourTest.test_hy2_udp_note_once)
        self.assertNotIn("warns.unshift", card)
        self.assertIn("warnLine: warns.filter(Boolean),", card)
        self.assertIn("moreOpen: ibMoreOpen.has(r.kind)", card)

    @unittest.skipUnless(shutil.which("node"), "нет node")
    def test_desc_and_log_line_are_short(self):
        """F01, UI-2: длинное описание — первая фраза или её часть до «;» на виду (≤ 100 знаков, «;» — точкой), остальное — с
        большой буквы в «Подробнее»; последняя фраза — предупреждение Hysteria2; F02: строка журнала без управляющих символов, ≤ 160."""
        ux = read("ux.js")
        m = re.search(r"  var DESC_SHORT = .*?\n  var LOG_MAX = 160;\n  function logLine\(s\) \{.*?\n  }\n", ux, re.S)
        self.assertTrue(m)
        js = m.group(0) + r"""
const long = "Hysteria2: телефоны и компьютеры (v2RayTun, Hiddify); QUIC по UDP, маскировка Salamander. Блокировки UDP в мобильных сетях не проверены — держите рядом VLESS.";
const p = descParts(long), q = descParts("Короткое описание. Вторая фраза.");
const l = logLine("2026/10/19 [Warning] malformed \"\\x03\\x01\\x00\" \u0007" + " x".repeat(200));
const t = descParts("VLESS + TLS: телефоны и компьютеры (v2RayTun, Hiddify, Streisand, v2rayN); работает на 443 с сертификатом домена узла, постороннему отвечает сайт узла.");
console.log(JSON.stringify([p[0] === "Hysteria2: телефоны и компьютеры (v2RayTun, Hiddify).", p[1].startsWith("QUIC по UDP"), q[1] === "", /[\u0000-\u001f]|\\x0/.test(l), l.length <= 160,
  descLast(long).startsWith("Блокировки"), descLast("Одна фраза.") === "", t[0].endsWith("v2rayN).") && t[0].length <= 100, t[1].startsWith("Работает на 443")]));
"""
        out = subprocess.run(["node", "-e", js], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        self.assertEqual(out.stdout.decode().strip(), "[true,true,true,false,true,true,true,true,true]", out.stdout.decode())


class LayoutRulesTest(unittest.TestCase):
    def test_empty_slots_take_no_gap(self):
        """F14 (REV-ui1 З-2): пустых узлов в сетке с gap нет. Само поведение проверяет готовая раскладка —
        tools/k3_layout_check.mjs, вид «phantom» (каждый раздел, 390…1440, RU и EN); здесь — что проверка на месте и
        что блок без содержимого убирается из раскладки (правило :empty не срабатывало: внутри лежал пустой узел)."""
        self.assertIn("out.push(['phantom'", open(os.path.join(ROOT, "tools", "k3_layout_check.mjs"), encoding="utf-8").read())
        self.assertIn('b.node.toggleAttribute("data-blk-empty"', read("app.js"))
        self.assertIn("[data-blk-empty] { display: none !important; }", read("styles.css"))
        card = fn(read("app.js"), "ibCard")
        self.assertIn("if (btns.childElementCount) acts.append(btns);", card)

    def test_give_rows_one_layout_columns_by_content(self):
        """F07/F08: строки приложений одной раскладки; столбцы окна — по содержимому."""
        css = read("give.css")
        self.assertRegex(css, r"\.gs-app__main \{ flex: 1 1 100%;")
        self.assertRegex(css, r"\.gs-cols \{[^}]*align-items: flex-start;")
        self.assertRegex(css, r"\[data-device=\"phone\"\] \.gs-dlg \.gx-dlg__foot > \.ga-btn \{ flex: none;")     # F11

    def test_switch_rows_and_columns(self):
        """F03/F04/F06: «Настройки» — одна колонка (UI-5: часть рядом с подменю на всю ширину, без 960); строка
        переключателя ≤ 720; «Обзор» — части (nav2 r1): «Сводка» — «Требует внимания» и «События» в две колонки."""
        comp = read("components.css")
        self.assertRegex(comp, r"\.gx-sw \{[^}]*max-width: 720px;")
        self.assertNotIn("gx-cols--rows", comp)                        # REV-ui1 З-1: «Настройки» — одна колонка
        self.assertNotIn('.gx-page[data-page="settings"] { max-width: 960px; }', comp)
        self.assertIn('.gx-page[data-page="settings"] .gx-cols { flex-direction: column; align-items: stretch; }', comp)
        self.assertIn(".gx-flow { columns: 2;", comp)
        app = read("app.js")
        self.assertNotIn("U.flow([", app)                                # UI-A1: карточек потока на «Обзоре» нет
        self.assertIn('return [ovbMount(el("div", { class: "kb" }), "summary")];', app)        # круг 1 Т1: Сводка — часть ovb

    def test_groups_exit_row_by_part_width(self):
        """nav2 r1 (UI-A1 2a, k3_layout_check 768–1024: overlap/ellipsis): «Выходы › Группы» стоят рядом с подменю — строка
        выхода переходит в узкую раскладку по ширине части (контейнер kkpart), а не окна; имя выхода — с подсказкой."""
        css = read("styles.css")
        m = re.search(r"@container kkpart \(max-width: 719\.98px\) \{\n  \.kk-eg-exit \{ grid-template-columns: auto minmax\(0, 1fr\) auto;", css)
        self.assertTrue(m)
        with open(os.path.join(STATIC, "pages", "exits-groups.js"), encoding="utf-8") as f:
            self.assertIn('el("b", { text: exitName(id), title: exitName(id) })', f.read())

    def test_head_title_whole_at_1100(self):
        """nav2 r1 (UI-A1 2a, сверка с эталоном H6 на 1100): рядом с подменю заголовок раздела в шапке — целиком
        («Люди и устройства», «Маршрут по умолчанию»); место уступают поле поиска (сжимается первым) и «Вид» — значком,
        подпись — подсказкой. Замер — tools/k3_layout_check.mjs (ширины 1100/1200, ellipsis) и снимки сверки."""
        css = read("styles.css")
        m = re.search(r"@media \(min-width: 1100px\) \{\n(.*?)\n\}", css, re.S)   # Т3: и шире 1280
        self.assertTrue(m)
        # З-1 (REV-3313-final): поле поиска растёт от 0 (до 340, «крупнее» — до 180), заголовок сжимается последним —
        # когда поле уже значком; замер порядка уступок — детектор headprio в tools/k3_layout_check.mjs
        self.assertIn(".gx-head__title { flex-shrink: 1; max-width: 55%; }", m.group(1))
        self.assertIn(".gx-head .kk-hfind { flex: 1000 0 0; max-width: 340px; }", m.group(1))
        self.assertIn('html[data-fs="l"] .gx-head .kk-hfind { max-width: 180px; }', m.group(1))
        # «крупнее» уже 1100 — поле значком, как у всех (find.css): основа 180 без @media перебивала его (на 1000 «Св…»)
        self.assertNotRegex(css.split("@media (min-width: 1100px)")[0], r'html\[data-fs="l"\] \.gx-head \.kk-hfind \{')
        # Т3 №17: «Вид» значком — на всех окнах уже 1280 (с «A A A» подпись не влезала на 720 на «крупнее»)
        m2 = re.search(r"@media \(max-width: 1279\.98px\) \{\n(.*?)\n\}", css, re.S)
        self.assertTrue(m2)
        self.assertIn(".gx-viewbtn__t { display: none; }", m2.group(1))
        app = read("app.js")
        self.assertIn('class: "gx-viewbtn", title: t("sh.view"),', app)
        self.assertIn('el("span", { class: "gx-viewbtn__t", text: t("sh.view") })', app)

    def test_page_change_closes_sheets(self):
        """F10: смена раздела закрывает боковую панель."""
        set_page = fn(read("app.js"), "setPage")
        self.assertIn('l.kind === "sheet"', set_page)

    def test_logout_waits_for_the_node(self):
        """F19: страница уходит на вход только после ответа узла (401 — тоже), сбой — сообщение, а не вид выхода."""
        lo = fn(read("app.js"), "logout")
        self.assertIn("API.logout().then(out,", lo)
        self.assertNotIn(".finally(() => {\n      stopTransport();", lo)
        self.assertIn("e.status === 401", lo)


if __name__ == "__main__":
    unittest.main()

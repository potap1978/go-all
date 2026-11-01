"""3.3.7.15 V2: «Настройки › Сервер › Адреса» (pages/addresses.js, вид Claude Design r1) — места по сообщению
координатора после Б3 и по проверке REV-3315-b3:

* F12б — кнопка «Разнести выходы» ведёт в POST /api/gw/addresses/spread (через окно подтверждения сервера);
* П-5 — подпись «смена перезапускает xray» один раз на блок (решение координатора Д2): у переключателя «для выхода»
  раскрытой строки и одной строкой «Выхода», не подсказкой у выбора «Напрямую» (RU и EN);
* З-2 — предупреждение mip.route_out («адрес маршрута не для выхода, а выходы без своего адреса идут с него»)
  показывается, если оно есть в ответе GET /api/gw/addresses;
* окно подтверждения — ширины gx-dlg--risk (600 px, кадры Д5–Д7): openDialog ставит --dlg-w на само окно;
* полоса ролей: колонка не шире кнопки (адреса соседей не наезжают), обрезанный адрес — с полным в title.

Доводка Д2 (stand/logs/D-3315-view-adr.md) — классы ниже SpreadTest…DialogAndMapTest: «eth0 · второй адрес», «IPv6
выключен в ядре», полоса без IPv6, подпись колонки по ширине колонки, «На этом же интерфейсе» переносом, «Выход» на узкой
части, схема с выходом по IPv6 за «ещё N», строка «Адреса» телефона, подпись листа «Разложить заново».

Дочистка Д2b (stand/logs/D-3315-view-adr-r2.md) — классы в конце файла: «Ещё N выходов» склоняется общим K.tn, «Смена
«Для выхода» перезапускает xray», схема и выбор — одна подпись «по маршруту системы», выбор «Приёма» под окном
подтверждения виден нажатым, строка телефона без «нет на сервере», подзаголовок адреса = строка «Интерфейс», раскрытая
строка на части ≥ 760 — дорожки 2 × 2 и выбор слева, цифры справа (option-Д1-grid).

Без Chrome — проверки исходника и чистые функции части в node; поведение — мок (?mock=1&adr=c1…c10, &ex6=1) и
tools/k3_layout_check.mjs (adrgrid, adrfit)."""
import json
import os
import re
import shutil
import subprocess
import unittest

from tests.test_332f_give import STATIC as STATIC_DIR, read

APP = read("app.js")
ADR = read("pages/addresses.js")
RU = read("pages/addresses-i18n-ru.js")
EN = read("pages/addresses-i18n-en.js")
CSS = read("addresses.css")
MOCK = read("dev-mock-adr.js")


def key(src, k):
    m = re.search(r'"%s":\s*"((?:[^"\\]|\\.)*)"' % re.escape(k), src)
    return m.group(1) if m else None


class SpreadTest(unittest.TestCase):
    def test_api_route(self):
        self.assertRegex(APP, r'spread:\s*\(b\)\s*=>\s*request\("POST",\s*"/api/gw/addresses/spread",\s*b\)')

    def test_button_calls_spread_through_write(self):
        # кнопка в «Выходе» — data-adr-spread; вызов — через write(): ver, окно подтверждения сервера, отказ 409
        self.assertRegex(ADR, r'U\.btn\(t\("adr\.oSpread"\), "tertiary", \(\) => spread\(\)')
        self.assertIn("sp.dataset.adrSpread", ADR)
        m = re.search(r"const spread = \(\) => write\(\{([^\n]*)", ADR)
        self.assertTrue(m, "нет spread()")
        self.assertIn("call: K.API.addresses.spread", m.group(1))

    def test_texts_ru_en(self):
        for k in ("adr.oSpread", "adr.oSpreadH", "adr.dlg.spread", "adr.btn.spread"):
            self.assertTrue(key(RU, k), "нет RU " + k)
            self.assertTrue(key(EN, k), "нет EN " + k)
            self.assertNotEqual(key(RU, k), key(EN, k), k)

    def test_mock_answers_spread(self):
        # мок отвечает так же, как ядро: без yes — 409 confirm, меньше двух адресов «для выхода» — 409 с причиной
        self.assertIn("/api/gw/addresses/spread", MOCK)


class XrayRestartTest(unittest.TestCase):
    """REV-3315-b3 П-5: смена «Напрямую» и «для выхода» перезапускает xray (xray.py) — подпись у обоих мест."""

    def test_texts(self):
        self.assertIn("xray", key(RU, "adr.xrayD") or "")
        self.assertIn("xray", key(EN, "adr.xrayD") or "")

    def test_switch_text_names_what_changes(self):
        """Д2b (Д1, М3): «Смена «Для выхода» перезапускает xray.» — как «Смена «Напрямую»…» в «Выходе»."""
        self.assertIn("«%s»" % key(RU, "adr.fOut"), key(RU, "adr.xrayD") or "")
        self.assertIn("“%s”" % key(EN, "adr.fOut"), key(EN, "adr.xrayD") or "")

    def test_out_switch_has_it(self):
        line = next((x for x in ADR.splitlines() if 't("adr.swOut")' in x), "")
        self.assertIn('t("adr.xrayD")', line)

    def test_out_block_says_it_once(self):
        """Д2: в «Выходе» — одна строка на блок, не подсказка у выбора «Напрямую» (кадр Д1: фраза была трижды)."""
        line = next((x for x in ADR.splitlines() if 'sel(t("adr.oDirect")' in x), "")
        self.assertNotIn("adr.xrayD", line)
        m = re.search(r"function outBlock\(\).*?\n  \}", ADR, re.S)
        self.assertTrue(m, "нет outBlock()")
        self.assertEqual(m.group(0).count('t("adr.oXray")'), 1)
        self.assertNotIn("adr.xrayD", m.group(0))
        for src in (RU, EN):
            self.assertIn("xray", key(src, "adr.oXray") or "")


class RouteOutTest(unittest.TestCase):
    """REV-3315-b3 З-2: GET /api/gw/addresses отдаёт mip.route_out {ip, ids} — вид его показывает, если он есть."""

    def test_view_reads_route_out(self):
        self.assertRegex(ADR, r"mip\(\)\.route_out")
        self.assertIn('t("adr.routeLoose"', ADR)

    def test_texts(self):
        for src in (RU, EN):
            v = key(src, "adr.routeLoose") or ""
            self.assertIn("{ip}", v)
            self.assertIn("{names}", v)

    def test_mock_has_route_out(self):
        self.assertIn("route_out", MOCK)


class RouteLabelTest(unittest.TestCase):
    """П1: тексты ядра («путь назад» F12б, отказ not_out) ссылаются на выбор у выхода по имени — подпись в панели та же."""

    def test_core_names_the_panel_label(self):
        import json
        import os
        cat = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "i18n", "catalog")
        for lang, src, q in (("ru", RU, "«%s»"), ("en", EN, "“%s”")):
            with open(os.path.join(cat, lang, "gw_mip_spread.json"), encoding="utf-8") as f:
                back = json.load(f)["gw_mip_spread.back"]
            label = key(src, "adr.oByRoute")
            self.assertTrue(label)
            self.assertIn(q % label, back, lang)      # в кавычках — целиком, не часть («по маршруту» ≠ «по маршруту системы»)


class WriteWarningsTest(unittest.TestCase):
    """Записи адресов отвечают warnings (panel/api.py _warnings: свои и apply.warnings) — запись прошла, но не всё
    применилось; вид показывает их тостом «внимание», а не молчит."""

    def test_done_shows_warnings(self):
        m = re.search(r"const done = \(res\) => \{(.*?)\n    \};", ADR, re.S)
        self.assertTrue(m, "нет done() в write()")
        body = m.group(1)
        self.assertIn("res.warnings", body)
        self.assertIn("res.apply.warnings", body)
        self.assertIn('warns.length ? "warning"', body)


class DialogAndMapTest(unittest.TestCase):
    def test_confirm_dialog_risk_width(self):
        m = re.search(r"function ask\(.*?\n  \}", ADR, re.S)
        self.assertTrue(m, "нет функции ask()")
        self.assertIn('size: "600px"', m.group(0))
        self.assertIn("gx-dlg--risk", m.group(0))

    def test_map_column_not_wider_than_button(self):
        self.assertRegex(CSS, r"\.kk-adr-map__c\s*\{\s*grid-template-columns:\s*minmax\(0,\s*1fr\);\s*\}")

    def test_cut_address_has_title(self):
        self.assertRegex(ADR, r'class: "kk-adr-map__ip", text: r\.ip, title: r\.ip')
        self.assertRegex(ADR, r'class: "kk-adr-map__ips", text: sh\[r\.ip\] \|\| r\.ip, title: r\.ip')


# ── доводка Д2 (stand/logs/D-3315-view-adr.md) ──────────────────────────────────────────────────────────────────────
APPJS = APP
I18N_RU, I18N_EN = read("i18n-ru.js"), read("i18n-en.js")
GROUPS = read("pages/exits-groups.js")


def fn(src, name):
    m = re.search(r"\n  (?:function %s\(|const %s = ).*?\n  \}" % (name, name), src, re.S)
    return m.group(0) if m else ""


class SecondaryTest(unittest.TestCase):
    """Кадры Д5, М3: «eth0 · второй адрес» — по addresses[].secondary ядра (только IPv4, только true)."""

    def test_iface_row(self):
        line = next((x for x in ADR.splitlines() if 't("adr.kv.iface")' in x and "row(" in x), "")
        self.assertIn("ifaceText(r)", line)
        m = re.search(r"const ifaceText = \(r\) => \{.*?\};", ADR, re.S)
        self.assertTrue(m, "нет ifaceText")
        self.assertIn("A.secondary === true", m.group(0))
        self.assertIn('t("adr.kv.second")', m.group(0))
        self.assertIn("!v6", m.group(0))
        self.assertEqual((key(RU, "adr.kv.second"), key(EN, "adr.kv.second")), ("второй адрес", "second address"))

    def test_mock(self):
        self.assertRegex(MOCK, r"secondary: a\.origin === 'manual' \? null : a\.ip === IP2")


class V6OffTest(unittest.TestCase):
    """С3 «IPv6 выключен в ядре» отдельно от С2 «IPv6 нет» — по ipv6_off ядра; и в списке, и на телефоне."""

    def test_empty_text(self):
        f = next((x for x in ADR.splitlines() if "const noV6 = " in x), "")
        self.assertIn("ipv6_off === true", f)
        self.assertIn('"adr.v6off"', f)
        self.assertIn('"adr.noV6"', f)
        self.assertIn("noV6()", fn(ADR, "listBlock"))
        self.assertIn("noV6()", fn(ADR, "phoneList"))
        self.assertNotIn('t("adr.noV6")', fn(ADR, "listBlock") + fn(ADR, "phoneList"))
        self.assertEqual(key(RU, "adr.v6off"), "IPv6 выключен в ядре.")
        self.assertEqual(key(EN, "adr.v6off"), "IPv6 is turned off in the kernel.")

    def test_mock_c3(self):
        self.assertIn("ipv6_off: scene === 'c3'", MOCK)


class MapNo6Test(unittest.TestCase):
    """С2, С3: без IPv6 в полосе нет дорожек «Выдача IPv6», «Срок IPv6» и знака «не бывает»; сетка — на две строки
    меньше."""

    def test_lanes(self):
        m = fn(ADR, "mapBlock")
        self.assertIn("no6 = !v6.length", m)
        self.assertIn('no6 ? null : t("adr.lane.give")', m)
        self.assertIn('no6 ? null : t("adr.lane.life")', m)
        self.assertIn('!(no6 && p[0] === "give")', m)
        self.assertIn('!(no6 && k[0] === "na")', m)
        self.assertIn('(no6 ? " is-no6" : "")', m)

    def test_rows(self):
        self.assertRegex(CSS, r"\.kk-adr-map\.is-no6 > \.kk-adr-map__lg, \.kk-adr-map\.is-no6 > \.kk-adr-map__c \{ "
                              r"grid-template-rows: 22px repeat\(3, 18px\) 22px; \}")


class ShortLabelTest(unittest.TestCase):
    """С2: короткая подпись колонки («.10») — по ширине самой колонки (fitMap), а не порогом части 620."""

    def test_no_part_threshold(self):
        m = re.search(r"@container kkpart \(max-width: 619\.98px\) \{(.*?)\n\}", CSS, re.S)
        self.assertTrue(m)
        self.assertNotIn("kk-adr-map__ip", m.group(1))
        self.assertIn(".kk-adr-map__c.is-short .kk-adr-map__ip { display: none; }", CSS)
        self.assertIn(".kk-adr-map__c.is-short .kk-adr-map__ips { display: block; }", CSS)

    def test_fit(self):
        f = fn(ADR, "fitMap")
        self.assertIn("ip.scrollWidth > ip.clientWidth", f)
        self.assertIn('classList.toggle("is-short"', f)
        self.assertIn("new ResizeObserver(fit)", f)
        self.assertIn("fitMap();", fn(ADR, "paint"))
        self.assertIn("fitRO.disconnect()", fn(ADR, "leave"))


class SameIfaceTest(unittest.TestCase):
    """Д1: «На этом же интерфейсе» — адреса переносом целиком (было одной строкой с многоточием)."""

    def test_wrap(self):
        line = next((x for x in ADR.splitlines() if 't("adr.kv.same")' in x), "")
        self.assertNotIn("mono: true", line)
        self.assertIn("kk-adr-ips", line)
        self.assertIn("r.sel || r.node ||", ADR)
        self.assertRegex(CSS, r"\.kk-adr-ips__i \{ display: inline-block; max-width: 100%;[^}]*overflow-wrap: anywhere;")


class OutNarrowTest(unittest.TestCase):
    """С2: «Выход» — свой контейнер; узкий — подпись над выбором; середина схемы не уже 56 px."""

    def test_container_and_stack(self):
        self.assertIn(".kk-adr-out { container: kkadrout / inline-size; }", CSS)
        m = re.search(r"@container kkadrout \(max-width: 559\.98px\) \{(.*?)\n\}", CSS, re.S)
        self.assertTrue(m, "нет порога «Выхода»")
        self.assertIn(".kk-adr-out .gx-kv__k { grid-column: 1 / -1;", m.group(1))

    def test_scheme_middle(self):
        cols = re.findall(r"\.kk-adr-sch \{[^}]*grid-template-columns: ([^;]+);", CSS)
        self.assertTrue(cols)
        for c in cols:
            self.assertNotRegex(c, r"(^|\s)(28|[1-4]\d|5[0-5])px(\s|$)", c)    # середина — не уже 56 px
        self.assertNotIn("@container kkpart (max-width: 559.98px)", CSS)


@unittest.skipUnless(shutil.which("node"), "нет node: чистые функции части не проверены")
class SchPickTest(unittest.TestCase):
    """Д1 с выходом по IPv6 (мок &ex6=1): выходов больше строк схемы — в «ещё N» уходят выходы с адреса маршрута, а не
    единственный у узла «маршрут IPv6» (узел слева оставался без линии)."""

    def pick(self, nodes, prim, room):
        js = ("const fs = require('fs'); let part = null; global.window = { KaskadPanel: { el() {}, t: (k) => k, U: {}, "
              "part(n, d) { part = d; } } };\n"
              "eval(fs.readFileSync(%s, 'utf8'));\n"
              "process.stdout.write(JSON.stringify(part.pure.schPick(%s, %s, %d).map((n) => n.b)));"
              % (json.dumps(os.path.join(STATIC_DIR, "pages", "addresses.js")), json.dumps(nodes), json.dumps(prim), room))
        r = subprocess.run(["node", "-e", js], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode()[-400:])
        return json.loads(r.stdout.decode())

    def test_room(self):
        n = [{"b": x, "from": "P"} for x in "abc"]
        self.assertEqual(self.pick(n, "P", 4), ["a", "b", "c"])                # все входят — порядок как был

    def test_own_lines_stay(self):
        n = [{"b": "vienna", "from": "P"}, {"b": "hel", "from": "B"}, {"b": "tokyo", "from": "P"}, {"b": "new", "from": "P"},
             {"b": "stock", "from": "2001:db8:10::5"}]
        self.assertEqual(self.pick(n, "P", 4), ["hel", "stock", "vienna"])     # IPv6 и свой адрес — не в «ещё N»
        n2 = [{"b": "x1", "from": "B"}, {"b": "x2", "from": "B"}, {"b": "x3", "from": "P"}, {"b": "x4", "from": "P"},
              {"b": "x5", "from": "P"}]
        self.assertEqual(self.pick(n2, "P", 4), ["x1", "x2", "x3"])


class PhoneRowTest(unittest.TestCase):
    """М1: строка «Адреса» (как в макете), счёт — как в группах «Адресов» (Д1): пропавший адрес считается; Д2b — без
    хвоста «нет на сервере: N» (на узком экране обрежется первым, предупреждение — у строки адреса)."""

    def test_title(self):
        line = next((x for x in APPJS.splitlines() if "const row = (id) =>" in x and "K.mrow" in x), "")
        self.assertIn('id === "addresses" ? "adr.short" : r[2]', line)
        self.assertIn("title: tt", line)

    def test_count(self):
        self.assertIn("counts: () => (S.doc ? { v4: rows4().length, v6: rows6().length } : null),", ADR)
        self.assertIn('return c ? t("adr.rowS", { n4: c.v4, n6: c.v6 }) : t("m.stAdrS");', APPJS)
        for src in (APPJS, I18N_RU, I18N_EN):
            self.assertNotIn("adr.rowSG", src)
        self.assertEqual(key(I18N_RU, "adr.rowS"), "{n4} IPv4 · {n6} IPv6")


class RegroupSheetTest(unittest.TestCase):
    """Р2т: подпись листа «Разложить заново» — уровни · ключи · устройства, без «выбрали: …»."""

    def test_sub(self):
        self.assertIn("ruMeta(g, true) : groupMeta(g)", GROUPS)
        self.assertIn("const ruMeta = (g, noWho) =>", GROUPS)
        self.assertIn("!noWho && w.known", GROUPS)


# ── Д2b: дочистка по повторной сверке Claude Design (stand/logs/D-3315-view-adr-r2.md) ──────────────────────────────
@unittest.skipUnless(shutil.which("node"), "нет node: склонение не проверено")
class MorePluralTest(unittest.TestCase):
    """Д1-v6: узел схемы «Ещё 2 выходов» — «Ещё 2 выхода» (1 выход, 2 выхода, 5 выходов): общий K.tn, формы через «|»."""

    def test_code_uses_tn(self):
        self.assertIn('s: K.tn("adr.sch.moreS", n)', ADR)
        self.assertIn('b: t("adr.sch.more", { n })', ADR)
        self.assertIn("const tn = (key, n, vars) =>", APPJS)                 # общий помощник ядра: формы через «|»

    def forms(self, lang, src, ns):
        # тот же выбор формы, что pluralIdx/tn в app.js: Intl.PluralRules one → 0, few → 1, иначе 2 (не больше числа форм)
        js = ("const f = %s.split('|'), pr = new Intl.PluralRules(%s); process.stdout.write(JSON.stringify(%s.map((n) => {"
              "const c = pr.select(n); return f[Math.min(c === 'one' ? 0 : c === 'few' ? 1 : 2, f.length - 1)]; })));"
              % (json.dumps(key(src, "adr.sch.moreS") or ""), json.dumps(lang), json.dumps(ns)))
        r = subprocess.run(["node", "-e", js], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode()[-400:])
        return json.loads(r.stdout.decode())

    def test_ru(self):
        self.assertEqual(self.forms("ru", RU, [1, 2, 4, 5, 11, 21, 22]), ["выход", "выхода", "выхода", "выходов", "выходов", "выход", "выхода"])

    def test_en(self):
        self.assertEqual(self.forms("en", EN, [1, 2, 5, 21]), ["exit", "exits", "exits", "exits"])


class SchemeRouteLabelTest(unittest.TestCase):
    """Д1, С2, С3: выход по маршруту — «по маршруту системы» и в выборе, и подписью узла схемы справа (один ключ)."""

    def test_one_key(self):
        line = next((x for x in ADR.splitlines() if "const exNodes = " in x), "")
        self.assertIn('s === "main" ? t("adr.oByRoute")', line)
        self.assertIn('[["main", t("adr.oByRoute")]]', ADR)
        self.assertEqual((key(RU, "adr.oByRoute"), key(EN, "adr.oByRoute")), ("по маршруту системы", "by the system’s route"))


class PressedUnderDialogTest(unittest.TestCase):
    """Д5 (D-3315-view-adr-r3): пока открыто подтверждение «Не принимать», «Принимать» — в обычном нажатом виде gx-seg
    (aria-pressed="true", фон --segment, текст --foreground), без кольца (кольцо акцентом в системе — фокус с клавиатуры);
    «Отмена» ничего не меняет, согласие — нажатым станет новое значение после ответа сервера (перерисовка строки)."""

    def test_click_only_asks(self):
        line = next((x for x in ADR.splitlines() if "setIn(r, o)" in x and "addEventListener" in x), "")
        self.assertEqual(line.strip(), 'b.addEventListener("click", () => { if (dis || o === ic || S.busy) return; setIn(r, o); });')

    def test_dialog_leaves_the_page_alone(self):
        m = re.search(r"function ask\(o, cons\) \{.*?\n  \}", ADR, re.S)
        self.assertTrue(m, "нет ask()")
        self.assertIn("onClose: () => resolve(res) });", m.group(0))
        body = m.group(0).replace('d.box.classList.add("gx-dlg--risk")', "")       # класс самого окна — не страницы
        for bad in ("classList", "aria-pressed", "S.hold"):
            self.assertNotIn(bad, body)
        self.assertNotIn("is-held", ADR)

    def test_pressed_look_is_the_system_one(self):
        # ни одно правило addresses.css не перекрашивает нажатое значение gx-seg (кольцо, тень, рамка, фон, цвет)
        for sel, body in re.findall(r"([^{}]*gx-seg[^{}]*)\{([^}]*)\}", CSS):
            if "aria-pressed" in sel or "is-held" in sel:
                self.assertNotRegex(body, r"box-shadow|outline|border|background|color", sel.strip())
        self.assertIn('.gx-seg > button[aria-pressed="true"], .gx-seg > button[aria-selected="true"] { background: var(--segment); '
                      'box-shadow: var(--segment-shadow); color: var(--foreground); }', read("components.css"))

    def test_write_redraws_after_the_answer(self):
        m = re.search(r"function write\(o\) \{.*?\n  \}", ADR, re.S)
        self.assertTrue(m, "нет write()")
        self.assertIn("return reload(false).then(() => true);", m.group(0))              # согласие → ответ → перерисовка
        self.assertIn("if (!yes) return false;", m.group(0))                             # «Отмена» — без записи и перерисовки


class MockAlertTest(unittest.TestCase):
    """Д1, Д5 r3: тост «Выход «Токио» снова работает (пример сообщения alert)» — демонстрация мока: текст есть только в
    dev-mock*.js (грузятся при ?mock=1), не в коде и словарях панели."""

    def test_only_in_the_mock(self):
        for root, _dirs, names in os.walk(STATIC_DIR):
            for n in names:
                if not n.endswith((".js", ".html", ".css")) or n.startswith("dev-mock"):
                    continue
                with open(os.path.join(root, n), encoding="utf-8") as f:
                    src = f.read()
                self.assertNotIn("пример сообщения alert", src, n)
                self.assertNotIn("sample alert message", src, n)


class PhoneSubTest(unittest.TestCase):
    """М3: подзаголовок адреса во весь экран — то же значение «Интерфейс», что в цифрах («eth0 · второй адрес»)."""

    def test_sub(self):
        m = re.search(r"function phoneDetail\(r\) \{.*?\n  \}", ADR, re.S)
        self.assertTrue(m, "нет phoneDetail()")
        self.assertIn('t("adr.det.sub", { iface: ifaceText(r),', m.group(0))


class DetGridTest(unittest.TestCase):
    """Д1 (option-Д1-grid Claude Design): часть ≥ 760 — слева дорожки 2 × 2 → «Приём» → «Для выхода», справа цифры от
    верха дорожек, 1,15 : 1, промежуток 32; уже — одна колонка в порядке разметки; дорожки 2 × 2 на любой ширине."""

    def wide(self):
        m = re.search(r"@container kkpart \(min-width: 760px\) \{(.*?)\n\}", CSS, re.S)
        self.assertTrue(m, "нет порога 760")
        return m.group(1)

    def test_wide_grid(self):
        w = self.wide()
        self.assertIn("grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr); grid-template-rows: auto 1fr; "
                      'grid-template-areas: "lanes kv" "ctl kv"; column-gap: 32px;', w)
        for k, a in (("kk-adr-lanes", "lanes"), ("kk-adr-ctl", "ctl"), ("gx-kv", "kv")):
            self.assertIn(".kk-adr-det > .%s { grid-area: %s; }" % (k, a), w)
        self.assertIn(".kk-adr-det > .kk-adr-al { grid-column: 1 / -1; }", w)
        self.assertNotIn(".kk-adr-det > .kk-adr-lanes, ", w)                 # дорожки больше не во всю ширину

    def test_lanes_two_by_two(self):
        self.assertIn(".kk-adr-lanes { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));", CSS)
        self.assertNotRegex(CSS, r"\.kk-adr-lanes \{[^}]*repeat\(4")

    def test_markup_order(self):
        self.assertIn('return el("div", { class: "kk-adr-det" }, lanesEl, el("div", { class: "kk-adr-ctl" }, fIn, fOut), kvEl(kv));', ADR)

    def test_detector(self):
        with open(os.path.join(STATIC_DIR, "..", "..", "..", "..", "tools", "k3_layout_check.mjs"), encoding="utf-8") as f:
            lc = f.read()
        self.assertIn("пустота под «Для выхода» ' + gap + ' px > 56", lc)
        self.assertIn("if (gap > 56)", lc)
        self.assertIn("pw >= 760 && F && lv", lc)
        self.assertIn("дорожки раскрытой строки не 2 × 2", lc)

    def test_value_with_link(self):
        """Часть 1460 (окно 2000): «Адрес для клиентов: да» рвалось по буквам рядом со ссылкой «изменить — в «Шлюзе»»."""
        self.assertIn(".kk-adr-det .gx-kv__text:has(+ .gx-link) { flex: 0 1 auto; }", CSS)


@unittest.skipUnless(shutil.which("node"), "нет node")
class LostStaleTest(unittest.TestCase):
    """П7 (REV-3315-p256 П-1): ответ записи потерян, перечитывание идёт, а «Проверить» начала другое чтение — ответ
    перечитывания устарел (reload → false), но выбор не откатывается вслепую: запись могла пройти, новое чтение само
    нарисует то, что на сервере. Неудачное своё перечитывание — выбор возвращается, как раньше. Сама lost() из
    addresses.js — в node с заглушками reload/later/K."""

    def run_lost(self):
        m = re.search(r"\n    const lost = \(\) => (.*?\n    \}\);)\n", ADR, re.S)
        self.assertTrue(m, "нет lost() в write()")
        js = """
let undone = 0, toasts = 0, reload = null;
const S = { seq: 0, busy: true }, K = { toast() { toasts++; } }, t = (k) => k, later = () => Promise.resolve(), DROP_MS = 0;
const o = { undo() { undone++; } };
const lost = () => %s
(async () => {
  const out = {};
  reload = () => { S.seq++; return Promise.resolve().then(() => { S.seq++; return false; }); };   // «Проверить» тем временем
  S.busy = true; out.stale = [await lost(), undone, S.busy];
  reload = () => { S.seq++; return Promise.resolve(false); };                                      // не перечиталось
  undone = 0; S.busy = true; out.failed = [await lost(), undone, S.busy];
  reload = () => { S.seq++; return Promise.resolve(true); };                                       // перечиталось
  undone = 0; S.busy = true; out.ok = [await lost(), undone, toasts, S.busy];
  console.log(JSON.stringify(out));
})();
""" % m.group(1)
        r = subprocess.run(["node", "-e", js], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])

    def test_a_stale_reread_keeps_the_choice(self):
        out = self.run_lost()
        self.assertEqual(out["stale"], [False, 0, False])       # без отката выбора, «Применить» отперта
        self.assertEqual(out["failed"], [False, 1, False])      # своё чтение не вышло — выбор прежний
        self.assertEqual(out["ok"], [True, 0, 1, False])        # прочитано — пояснение тостом


if __name__ == "__main__":
    unittest.main()

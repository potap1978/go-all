"""3.3.6 — search over the panel «как в Настройках iPhone» (BACKLOG-3.3.6 п. 3, RESEARCH-panel-custom-search §5):
the model of find.js (normalisation, «ё = е», ranking, groups, «Недавние», marks) in node, the real inventory of app.js
searched by the model (RU and EN), and the static contract of the search UI in app.js (roles, keys, address-only
navigation, no inline style). The anchors in the DOM (record ↔ [data-find] of its section) — tools/k3_find_check.mjs."""

import json
import os
import re
import unittest

from tests import jsparts
from tests.test_panel_f0 import APP, HAS_NODE, nav_scope, read, run_node, window_with


def model_js():
    return "const window = globalThis;\n" + read("find.js") + "\nconst F = window.KaskadFind;\n"


REC = """
const R = [
  { id: "settings.look", kind: "card", label: "Вид", words: ["Вид", "тема", "тёмная тема", "акцент"], path: ["Настройки"] },
  { id: "settings.password", kind: "action", label: "Пароль панели", words: ["Пароль панели", "пароль", "логин"], path: ["Настройки", "Доступ к панели"] },
  { id: "settings.access", kind: "card", label: "Доступ к панели", words: ["Доступ к панели", "вход"], path: ["Настройки"] },
  { id: "settings.reqPw", kind: "field", label: "Пароль и в SSH-туннеле", words: ["Пароль и в SSH-туннеле"], path: ["Настройки", "Доступ к панели"] },
  { id: "people", kind: "section", label: "Пользователи и устройства", words: ["Пользователи и устройства", "пользователи"], path: [] },
  { id: "u-1", kind: "person", label: "Пётр", sub: "2 устройства" },
  { id: "u-2", kind: "person", label: "Анна Петрова", sub: "1 устройство" },
  { id: "d-1", kind: "device", label: "iPhone Петра", sub: "устройство · Пётр", words: ["192.0.2.15"] },
  { id: "e-1", kind: "exit", label: "Финляндия", sub: "VLESS · fi.example.com" },
  { id: "fw-tcp-443", kind: "forward", label: "TCP 443 → 192.0.2.10:443", sub: "сайт", words: ["443", "сайт"] },
];
"""


@unittest.skipUnless(HAS_NODE, "no node")
class ModelTest(unittest.TestCase):
    def run_js(self, body):
        return run_node(model_js() + REC + body)

    def test_norm_yo_case_and_punctuation(self):
        out = self.run_js("""console.log(JSON.stringify([F.norm("Тёмная  ТЕМА"), F.norm("ИИ-агент"), F.norm("«Вид»"), F.norm("192.0.2.5"), F.norm(null)]));""")
        self.assertEqual(out, ["темная тема", "ии агент", "вид", "192 0 2 5", ""])

    def test_owner_example_password(self):
        """«пароль» → «Доступ к панели → Пароль панели» первым в своей группе; точная подпись выше синонима."""
        out = self.run_js("""
const m = F.model(R, "пароль");
console.log(JSON.stringify(m.groups.map((g) => [g.id, g.rows.map((r) => r.rec.id)])));""")
        groups = dict(out)
        self.assertEqual(out[0][0], "actions")                               # «Пароль панели» — лучшее совпадение
        self.assertEqual(groups["actions"], ["settings.password"])
        self.assertEqual(groups["settings"], ["settings.reqPw"])              # начало подписи, но не вся подпись

    def test_yo_equals_e_and_case(self):
        out = self.run_js("""
const ids = (q) => F.model(R, q).groups.flatMap((g) => g.rows.map((r) => r.rec.id));
console.log(JSON.stringify([ids("темная"), ids("ТЁМНАЯ"), ids("петр"), ids("ПЁТР")]));""")
        self.assertEqual(out[0], ["settings.look"])
        self.assertEqual(out[1], ["settings.look"])
        self.assertEqual(out[2][0], "u-1")                                   # точное имя — первым
        self.assertIn("u-2", out[2])                                          # «Петрова» — начало слова
        self.assertIn("d-1", out[2])                                          # «Петра»
        self.assertEqual(out[2], out[3])

    def test_every_word_must_match_and_objects_by_data(self):
        out = self.run_js("""
const ids = (q) => F.model(R, q).groups.flatMap((g) => g.rows.map((r) => r.rec.id));
console.log(JSON.stringify([ids("пароль ssh"), ids("пароль космос"), ids("192.0.2.15"), ids("443"), ids("fi.example"), ids("я")]));""")
        self.assertEqual(out[0], ["settings.reqPw"])
        self.assertEqual(out[1], [])
        self.assertEqual(out[2], ["d-1"])                                     # IP устройства
        self.assertEqual(out[3], ["fw-tcp-443"])                              # порт проброса
        self.assertEqual(out[4], ["e-1"])                                     # адрес выхода
        self.assertEqual(out[5], [])                                          # одна буква — только с начала слова

    def test_port_words(self):
        """REV-T-336 Н-5: слова по портам — «443/tcp», «host:443», «[::1]:443», число; адрес без порта и IPv6 без скобок — нет."""
        out = self.run_js("""
const P = ["порт", "port"];
console.log(JSON.stringify([F.portWords(["443/tcp", "30256/udp", "443/udp"], P), F.portWords(["vienna.example.net:51820"], P),
  F.portWords(["[2001:db8::1]:8443"], []), F.portWords(["198.51.100.23", "2001:db8::1", "fi.example", "", null, "70000/udp", "0"], P), F.portWords(443, ["port"])]));""")
        self.assertEqual(out[0], ["443", "порт 443", "port 443", "30256", "порт 30256", "port 30256"])
        self.assertEqual(out[1], ["51820", "порт 51820", "port 51820"])
        self.assertEqual(out[2], ["8443"])
        self.assertEqual(out[3], [])
        self.assertEqual(out[4], ["443", "port 443"])

    def test_records_find_protocol_by_port(self):
        """REV-T-336 Н-5: настоящий findRecords из app.js — «порт 443», «port 443» и «30256» находят протокол на этом
        порту, порт выхода (host:port) — тоже; проброс на том же порту при «порт 443» стоит выше выхода; на английском
        русское «порт» тоже ищется."""
        parts = "\n".join([jsparts.const(APP, "KS"), jsparts.const(APP, "SM"), jsparts.const(APP, "ibPortList"), jsparts.function(APP, "findNoDup"), jsparts.const(APP, "exAbRows"), jsparts.function(APP, "findRecords")])
        for lang in ("ru", "en"):
            files = ["i18n-ru.js"] + (["i18n-en.js"] if lang == "en" else [])
            out = run_node(window_with(lang, files) + read("find.js") + """
let DICT = LANG === "en" ? Object.assign({}, window.KASKAD_I18N, window.KASKAD_I18N_EN) : window.KASKAD_I18N;
const t = (k) => (DICT[k] === undefined ? "MISSING:" + k : DICT[k]);
const tn = (k, n) => String(n), qs = (o) => "?focus=" + o.focus, sectionCatalog = () => [], FIND_NEED = {}, navOk = () => true;
const SECTION_BY = { forwards: { focus: "fw" } }, exitKindText = (r) => r.kind, inboundShort = (k) => k, ibState = () => ({ text: "" });
const LIVE = { awg2: { ports: "30256/udp" } }, lvInb = (k) => LIVE[k] || null;
const R = { people: {}, exits: { loaded: true, data: { rows: [{ id: "e-1", kind: "vless", name: "Хельсинки", host: "hel.example.net:443" },
    { id: "e-2", kind: "awg", name: "Вена", host: "vienna.example.net" }] } },
  inbounds: { loaded: true, data: [{ kind: "vless", title: "VLESS", ports: ["443/tcp"] }, { kind: "awg2", title: "AmneziaWG 2.0", ports: ["48445/udp"] },
    { kind: "wg", title: "WireGuard", ports: ["51820/udp"] }] },
  forwards: { loaded: true, data: [{ proto: "udp", port: 443, to: "203.0.113.50", to_port: 443, name: "" }] } };
%s
const recs = findRecords();
const ids = (q) => window.KaskadFind.model(recs, q).groups.flatMap((g) => g.rows.map((r) => r.rec.id));
console.log(JSON.stringify(["порт 443", "port 443", "30256", "порт 30256", "48445", "порт 51820"].map(ids)));""" % parts)
            self.assertEqual(out[0][:1], ["fw-udp-443"], lang)                   # «порт 443»: проброс первым
            self.assertEqual(sorted(out[0]), ["e-1", "fw-udp-443", "ib:vless"], lang)
            self.assertEqual(out[1], out[0], lang)                               # «port 443» — то же
            self.assertEqual(out[2], ["ib:awg2"], lang)                          # порт из живого круга сборщика
            self.assertEqual(out[3], ["ib:awg2"], lang)
            self.assertEqual(out[4], [], lang)                                   # устаревший порт описи не ищется
            self.assertEqual(out[5], ["ib:wg"], lang)

    def test_groups_limit_and_more(self):
        out = self.run_js("""
const many = [];
for (let i = 0; i < 12; i++) many.push({ id: "u-" + i, kind: "person", label: "Иван " + i });
many.push({ id: "people", kind: "section", label: "Пользователи", words: ["ивановы"] });
const a = F.model(many, "иван");
const b = F.model(many, "иван", { open: { people: true } });
const e = F.model(many, "  ");
console.log(JSON.stringify([a.total, a.groups.map((g) => [g.id, g.rows.length, g.more]), b.groups.map((g) => [g.id, g.rows.length, g.more]), e]));""")
        self.assertEqual(out[0], 13)
        self.assertEqual(out[1], [["people", 8, 4], ["sections", 1, 0]])
        self.assertEqual(out[2], [["people", 12, 0], ["sections", 1, 0]])
        self.assertEqual(out[3], {"q": "", "groups": [], "total": 0})

    def test_more_never_takes_rows_of_other_groups(self):
        """REV-336-search З-5: «Ещё N» одной группы не забирает её 8 строк у других; раскрыть нельзя — stuck
        («уточните запрос»), а не мёртвая строка; короткая раскрытая группа раскрывается целиком."""
        out = self.run_js("""
const recs = [];
for (let i = 0; i < 100; i++) recs.push({ id: "p" + i, kind: "person", label: "Анна " + i });
for (let i = 0; i < 10; i++) recs.push({ id: "e" + i, kind: "exit", label: "Анкара " + i });
const g = (o) => F.model(recs, "ан", o).groups.map((x) => [x.id, x.rows.length, x.more, x.stuck]);
console.log(JSON.stringify([g({}), g({ open: { people: true } }), g({ open: { people: true, exits: true } }), g({ open: { exits: true } })]));""")
        self.assertEqual(out[0], [["people", 8, 92, False], ["exits", 8, 2, False]])
        self.assertEqual(out[1], [["people", 52, 48, True], ["exits", 8, 2, False]])     # выходы остались, их «Ещё 2» живая
        self.assertEqual(out[2], [["people", 50, 50, True], ["exits", 10, 0, False]])
        self.assertEqual(out[3], [["people", 8, 92, False], ["exits", 10, 0, False]])
        for case in out:
            self.assertLessEqual(sum(x[1] for x in case), 60)

    def test_part_of_word_only_when_nothing_better(self):
        """REV-336-search П-3: «порт» не находит «экспорт», «ключ» — «Выключить», если есть совпадения с начала слова;
        одни части слова — показываются (поиск по куску имени)."""
        out = self.run_js("""
const recs = [{ id: "bk", kind: "action", label: "Скачать копию", words: ["экспорт"] }, { id: "fw", kind: "forward", label: "TCP 443", words: ["порт"] },
  { id: "off", kind: "action", label: "Выключить шлюз" }, { id: "key", kind: "card", label: "Ключ ИИ-агента" }];
const ids = (q) => F.model(recs, q).groups.flatMap((g) => g.rows.map((r) => r.rec.id));
console.log(JSON.stringify([ids("порт"), ids("ключ"), ids("кспор"), F.model(recs, "порт").total]));""")
        self.assertEqual(out, [["fw"], ["key"], ["bk"], 1])

    def test_parts_group_and_own_group(self):
        """UI-A2 (кадр Н2): части разделов и будущие части — группа «parts» («Части разделов»); запись может назвать
        группу сама (r.group из ORDER — карточка, которая и есть часть), неизвестная группа — по виду."""
        out = self.run_js("""
const P = [
  { id: "exits.part.list", kind: "part", label: "Все выходы", path: ["Выходы"] },
  { id: "exits.soon.ru", kind: "soon", label: "Российские группы", path: ["Выходы", "Скоро"] },
  { id: "exits.route", kind: "card", label: "Маршрут по умолчанию", path: ["Выходы", "Маршрут по умолчанию"], group: "parts" },
  { id: "x.card", kind: "card", label: "Маршрутизация", group: "nope" },
];
console.log(JSON.stringify([P.map(F.groupOf), F.ORDER, F.model(P, "маршрут").groups.map((g) => [g.id, g.rows.map((r) => r.rec.id)])]));""")
        self.assertEqual(out[0], ["parts", "parts", "parts", "settings"])
        self.assertLess(out[1].index("settings"), out[1].index("parts"))
        self.assertLess(out[1].index("parts"), out[1].index("sections"))
        self.assertEqual(out[2], [["settings", ["x.card"]], ["parts", ["exits.route"]]])      # равные — по ORDER

    def test_marks_split_words_as_norm_does(self):
        """REV-336-search П-4: <mark> — с начала слова по тем же разделителям, что и поиск."""
        out = self.run_js("""console.log(JSON.stringify([F.hits("<script>x</script>", "script"), F.hits("[core] беспроводной", "core"), F.hits("a)b", "b")]));""")
        self.assertEqual(out, [[[1, 7]], [[1, 5]], [[2, 3]]])

    def test_hits_and_recent(self):
        out = self.run_js("""
console.log(JSON.stringify([F.hits("Пароль панели", "пан"), F.hits("Тёмная тема", "темная"), F.hits("Анна Петрова", "петр анна"),
  F.hits("abc", ""), F.recentPush(["a", "b", "c"], "b", 3), F.recentPush(["a", "b", "c"], "z", 3), F.recentPush(null, "x")]));""")
        self.assertEqual(out[0], [[7, 10]])                                   # начало слова «панели», не «Пароль»
        self.assertEqual(out[1], [[0, 6]])
        self.assertEqual(out[2], [[0, 4], [5, 9]])
        self.assertEqual(out[3], [])
        self.assertEqual(out[4], ["b", "a", "c"])
        self.assertEqual(out[5], ["z", "a", "b"])
        self.assertEqual(out[6], ["x"])


@unittest.skipUnless(HAS_NODE, "no node")
class CatalogSearchTest(unittest.TestCase):
    """The real inventory of app.js (sectionCatalog) searched by find.js: what the owner types finds the right place."""

    def search(self, lang, queries):
        files = ["i18n-ru.js", "ux-i18n-ru.js"] + (["i18n-en.js", "ux-i18n-en.js"] if lang == "en" else [])
        return run_node(window_with(lang, files) + read("find.js") + """
const location = { hash: "" };
let DICT = LANG === "en" ? Object.assign({}, window.KASKAD_I18N, window.KASKAD_I18N_EN) : window.KASKAD_I18N;
const t = (k) => (DICT[k] === undefined ? "MISSING:" + k : DICT[k]);
%s
const cat = sectionCatalog();
const first = (q) => { const m = window.KaskadFind.model(cat, q); return m.groups.length ? [m.groups[0].rows[0].rec.id, m.groups[0].rows[0].rec.href, m.groups[0].rows[0].rec.path] : null; };
console.log(JSON.stringify({ n: cat.length, hints: cat.filter((r) => /\\{\\w+\\}/.test(r.hint || "") || /MISSING/.test(JSON.stringify(r))).map((r) => r.id), res: %s.map((q) => [q, first(q)]) }));
""" % (nav_scope(), json.dumps(queries)))

    def test_russian(self):
        out = self.search("ru", ["пароль", "тема", "тёмная", "впн", "бот", "реклама", "бэкап", "домен", "сертификат", "проброс", "добавить проброс", "порт",
                                 "маршрут", "выключить шлюз", "знакомство", "ии агент", "адрес для клиентов", "язык", "language",
                                 "в сети", "без выхода", "лимиты", "куда пойдёт", "ключи и подписки", "все выходы"])
        self.assertEqual(out["hints"], [])                                    # подсказки без {подстановок} и без пропусков
        self.assertGreater(out["n"], 50)
        res = dict((q, r) for q, r in out["res"])
        self.assertEqual(res["пароль"][:3], ["settings.password", "#settings?tab=access&open=password", ["Настройки", "Доступ к панели"]])
        self.assertEqual(res["тема"][0], "settings.look")
        self.assertEqual(res["тёмная"][0], "settings.look")
        self.assertEqual(res["впн"][0], "inbounds")
        self.assertEqual(res["бот"][0], "settings.bot")
        self.assertEqual(res["реклама"][0], "rules.ads")
        self.assertEqual(res["бэкап"][0], "settings.backup")
        self.assertEqual(res["домен"][0], "settings.domain")
        self.assertEqual(res["сертификат"][0], "settings.cert")
        self.assertEqual(res["проброс"][0], "forwards")                       # раздел «Пробросы» — с начала подписи
        self.assertEqual(res["добавить проброс"][0], "forwards.add")
        self.assertEqual(res["маршрут"][0], "exits.route")
        self.assertEqual(res["выключить шлюз"][0], "settings.gwOff")
        self.assertEqual(res["знакомство"][0], "settings.tour")
        self.assertEqual(res["ии агент"][0], "settings.mcp")
        self.assertEqual(res["язык"][0], "settings.look")                    # REV-336-search З-6: язык меняется во «Виде»
        self.assertEqual(res["language"][0], "settings.look")
        self.assertIn(res["адрес для клиентов"][0], ("settings.host", "settings.siteHost"))
        # nav2 r1: части разделов и фильтры «Людей» — записью «Раздел › Группа», адрес части; будущие — без адреса
        self.assertEqual(res["в сети"], ["people.part.on", "#people?show=on", ["Пользователи и устройства", "Показать"]])
        self.assertEqual(res["без выхода"][:2], ["people.part.noexit", "#people?show=noexit"])
        self.assertEqual(res["лимиты"][:2], ["people.soon.limits", ""])
        self.assertEqual(res["куда пойдёт"][:2], ["rules.part.trace", "#rules?tab=trace"])
        self.assertEqual(res["ключи и подписки"][:2], ["exits.part.keys", "#exits?tab=keys"])
        self.assertEqual(res["все выходы"][:2], ["exits.part.list", "#exits?tab=list"])

    def test_english_and_russian_words_on_english(self):
        out = self.search("en", ["password", "theme", "backup", "пароль", "subscription", "language", "язык", "online", "where traffic"])
        self.assertEqual(out["hints"], [])
        res = dict((q, r) for q, r in out["res"])
        self.assertEqual(res["password"][:3], ["settings.password", "#settings?tab=access&open=password", ["Settings", "Panel access"]])
        self.assertEqual(res["theme"][0], "settings.look")
        self.assertEqual(res["backup"][0], "settings.backup")
        self.assertEqual(res["пароль"][0], "settings.password")              # русские подписи ищутся и на английском
        self.assertEqual(res["subscription"][0], "exits.sub")
        self.assertEqual(res["language"][0], "settings.look")
        self.assertEqual(res["язык"][0], "settings.look")
        self.assertEqual(res["online"], ["people.part.on", "#people?show=on", ["Users & devices", "Show"]])
        self.assertEqual(res["where traffic"][:2], ["rules.part.trace", "#rules?tab=trace"])

    def test_every_need_is_known(self):
        """REV-336-search П-5: условие need каждой записи описи есть в FIND_NEED (неизвестное — записи нет совсем)."""
        body = APP[APP.index("  const SECTIONS = ["):APP.index("  const SECTION_BY = ")]
        needs = set(re.findall(r'need: "(\w+)"', body))
        known = set(re.findall(r"^    (\w+): \(\) =>", APP[APP.index("  const FIND_NEED = {"):APP.index("  const FIND_GROUP = ")], re.M))
        self.assertTrue(needs)
        self.assertEqual(needs - known, set())
        self.assertIn("fwAdd", needs)                                         # П-6: «Добавить проброс» — только где кнопка есть
        self.assertIn("Object.prototype.hasOwnProperty.call(FIND_NEED, r.need) && FIND_NEED[r.need]()", APP)


class UiContractTest(unittest.TestCase):
    """The search UI of app.js (§11b): roles, keys, recent in try/catch, navigation only by address, no inline style."""

    def test_roles_keys_and_address_only(self):
        body = APP[APP.index("  // ── 11b. Поиск по панели"):APP.index("  // ── 12. Разделы")]
        for need in ('role: "combobox"', '"aria-controls": "findRes"', '"aria-autocomplete": "list"', 'role: "listbox"', 'role: "option"',
                     '"aria-selected"', "aria-activedescendant", '"aria-live": "polite"', '"aria-modal": "true"', 'role: "group"'):
            self.assertIn(need, body)
        for key in ('"ArrowDown"', '"ArrowUp"', '"Enter"', 'k === "k"', 'ev.key === "/"'):
            self.assertIn(key, body)
        self.assertIn("ev.isComposing", body)                                 # набор через IME не выбирает строку
        # «Недавние» — localStorage только в try/catch
        for m in re.finditer(r"localStorage\.(?:getItem|setItem)", body):
            before = body[:m.start()]
            self.assertGreater(before.rfind("try {"), before.rfind("}"), "localStorage вне try")
        # выбор — только адресом (hashchange → setPage); ни одного прямого вызова окна или действия
        go = jsparts.function(APP, "findGo")
        self.assertIn("location.hash = h", go)
        self.assertIn("setPage(n.page, false)", go)
        self.assertNotRegex(body, r"\bopen(?:Password|LoginLink|Backup\w+|AddUser|AddDevice|AddForward)\(|addExitDialog\(|backupUndo\(|introAgain\(")
        self.assertNotIn("style=", body)
        self.assertNotIn("innerHTML", body)
        # телефон: поле 16 px, строки ≥ 44 px, окно во весь экран (find.css)
        css = read("find.css")
        self.assertRegex(css, r'\[data-device="phone"\] \.gx-find__q \{[^}]*font-size: max\(1rem, 16px\)')
        self.assertRegex(css, r'\[data-device="phone"\] \.gx-cmdk__r \{ min-height: 56px; \}')
        self.assertRegex(css, r'\.kk-hfind--m \{[^}]*height: 44px')
        self.assertRegex(css, r'\[data-device="phone"\] \.gx-find \{ width: 100%; height: var\(--vvh, 100dvh\)')
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,6}\b|rgba?\(|oklch\(")      # только токены

    def test_cmdk_results(self):
        """UI-A2: выдача gx-cmdk по кадру Н2 — подпись группы сверху (у первой на компьютере — «· Enter — перейти», для
        глаз), строка: значок · название + путь второй строкой · ↵ у выбранной (.is-act); будущая часть — aria-disabled,
        метка выпуска gx-soon, Enter не открывает; путь «Раздел › Часть»; окно у поля шапки — координатами через CSSOM."""
        body = APP[APP.index("  // ── 11b. Поиск по панели"):APP.index("  // ── 12. Разделы")]
        for need in ('class: "gx-cmdk__cap"', '"aria-hidden": "true", text: " · " + t("find.enterGo")', 'r.classList.toggle("is-act", on)',
                     'el("kbd", { class: "ga-kbd", text: "↵" })', 'row.setAttribute("aria-disabled", "true")', 'el("span", { class: "gx-soon", text: r.soon })',
                     'if (x.rec.kind === "soon") return;', 'path.concat(r.label).join(" › ")', 'box.style.setProperty(k, Math.round(v) + "px")',
                     'window.removeEventListener("resize", place)', 'parts: "find.gParts"'):
            self.assertIn(need, body)
        self.assertNotIn("gx-find__foot", body)                              # подсказки клавиш — в подписи группы
        css = read("find.css")
        for need in (".gx-cmdk__cap {", ".gx-cmdk__r.is-act { background: var(--surface-2); }", ".gx-cmdk__r--soon {",
                     ".gx-find--at > .gx-find__in { position: absolute; left: var(--fx); top: var(--fy); width: var(--fw); height: var(--fh);",
                     ".gx-find--at > .gx-cmdk { position: absolute; left: var(--lx); top: calc(var(--fy) + var(--fh) + 6px); width: var(--lw);"):
            self.assertIn(need, css)
        self.assertRegex(css, r"\.gx-find \{ width: min\(100%, 520px\);")

    def test_part_and_card_not_twice(self):
        """UI-A2: часть раздела с тем же именем, что карточка этой части, — одной строкой: карточка (синонимы, якорь,
        «Недавние») в группе частей, с путём «Раздел › Часть» и значком части; карточки нет (need) — остаётся часть."""
        fn = jsparts.function(APP, "findNoDup")
        out = run_node(fn + """
const mk = () => [
  { id: "exits.route", kind: "card", sec: "exits", tab: "default", label: "Маршрут по умолчанию", path: ["Выходы"], icon: "" },
  { id: "exits.part.default", kind: "part", sec: "exits", tab: "default", label: "Маршрут по умолчанию", path: ["Выходы"], icon: "route" },
  { id: "exits.part.list", kind: "part", sec: "exits", tab: "list", label: "Все выходы", path: ["Выходы"], icon: "globe" },
  { id: "rules.part.profile", kind: "part", sec: "rules", tab: "profile", label: "Профиль по умолчанию", path: ["Правила"], icon: "route" },
];
const a = findNoDup(mk());
console.log(JSON.stringify([a.map((r) => [r.id, r.group || "", r.path, r.icon]), findNoDup(mk().slice(1)).map((r) => r.id)]));""")
        self.assertEqual(out[0], [["exits.route", "parts", ["Выходы", "Маршрут по умолчанию"], "route"], ["exits.part.list", "", ["Выходы"], "globe"],
                                  ["rules.part.profile", "", ["Правила"], "route"]])
        self.assertEqual(out[1], ["exits.part.default", "exits.part.list", "rules.part.profile"])
        self.assertIn("const out = findNoDup(sectionCatalog().filter(", APP)  # после условий need

    def test_mac_keys_and_tour(self):
        """REV-336-search З-3, З-4: на Mac — только ⌘K (Ctrl+K в поле — системное); Mac узнаётся и по «macOS» (Chrome
        отдаёт userAgentData.platform = "macOS" — с большой буквы M нет); поиск не поверх тура; автоповтор Enter (П-7)."""
        self.assertIn("const MAC = /mac|iphone|ipad/i.test(", APP)
        self.assertIn("/mac|iphone|ipad/i.test(", read("ux.js"))
        self.assertIn("(MAC ? ev.metaKey && !ev.ctrlKey : ev.ctrlKey && !ev.metaKey) && !ev.shiftKey && k === \"k\"", APP)
        self.assertIn("if (findEntry || authGate || tour || !window.KaskadFind) return null;", APP)
        self.assertIn("ev.repeat && (ev.key === \"Enter\" || ev.key === \" \") && Date.now() - findChoseAt < 2000", APP)
        out = run_node("console.log(JSON.stringify(['macOS', 'MacIntel', 'iPhone', 'Windows', 'Linux x86_64', 'Android'].map((p) => /mac|iphone|ipad/i.test(p))));")
        self.assertEqual(out, [True, True, True, False, False, False])

    def test_actions_open_only_windows(self):
        """Действие описи (kind: action) — ?open=<окно>, которое раздел открывает сам (p.opens); опасных окон в p.opens нет."""
        body = APP[APP.index("  const SECTIONS = ["):APP.index("  const SECTION_BY = ")]
        acts = re.findall(r'\{ id: "([a-z]+)\.\w+", kind: "action"[^\n]*at: \{[^}]*open: "(\w+)"', body)
        self.assertTrue(acts)
        for sec, op in acts:
            m = re.search(r"  PAGES\.%s = \{\n(?:[^\n]*\n){0,4}?\s+opens: \{([^\n]*)\}," % sec, APP)
            self.assertIsNotNone(m, sec)
            self.assertRegex(m.group(1), r"\b%s: \(\) =>" % op, (sec, op))
        opens = " ".join(re.findall(r"\n\s+opens: \{([^\n]*)\},", APP))
        self.assertNotRegex(opens, r"backupUndo|Disable|disable|publicOff|delete|logout|\.del\(")
        # общий обработчик в buildPage снимает ?open= с адреса до открытия (обновление страницы окно не повторит)
        bp = jsparts.function(APP, "buildPage")
        self.assertIn("dropOpen(id, n.open)", bp)
        self.assertIn("Object.prototype.hasOwnProperty.call(p.opens, n.open)", bp)


if __name__ == "__main__":
    unittest.main()

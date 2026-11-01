"""F0 — the frame of the panel (k3/handoff/UX-FOUNDATION.md §3.3, §10 F0): the inventory of sections SECTIONS and the
one address scheme (?tab, ?focus, ?open checked by it), the lazy sections pages/<id>.js (K.loadPage, K.page), the
dictionaries of Kit and of the sections with the Russian fallback and without overwriting the core, Kit.init and
KaskadT.init with the helpers, tn and the layers, the templates Tpl.head/tabs/state, the object K, the route
/pages/<name>.js of the server (with and without the secret prefix), the format of the search inventory (3.3.6) and
jsparts.find. The JS runs in node: the real pieces of app.js cut by name (tests/jsparts.py), kit.js and templates.js
whole."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kaskad.panel import server
from tests import jsparts
from tests.panel.panelcase import PanelCase
from tests.panel.test_panel_proxy import PREFIX, DOMAIN, ProxyCase

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")
HAS_NODE = shutil.which("node") is not None


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


APP = read("app.js")


def run_node(src):
    """Run ``src`` in node; it prints one JSON line."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "f0.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    out = r.stdout.decode("utf-8").strip().splitlines()
    if r.returncode != 0 or not out:
        raise AssertionError("node: %s" % r.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(out[-1])


def window_with(lang, files):
    """JS: a fake window with the dictionaries ``files`` loaded (as index.html and boot() do), LANG and a console that
    keeps its errors."""
    loads = "\n".join("(function () { %s\n})();" % read(n) for n in files)
    return """
const window = globalThis;
globalThis.ERRORS = [];
const console = { error: (m) => { ERRORS.push(String(m && m.message || m)); }, warn: () => {}, log: (m) => process.stdout.write(m + "\\n") };
const LANG = %s;
%s
""" % (json.dumps(lang), loads)


def nav_scope(mock=True):
    """navState and sectionCatalog of app.js; ``mock`` — the page was opened with ?mock=1 (MOCK of app.js)."""
    parts = ["let MOCK = %s;" % ("true" if mock else "false")]
    # nav2 r1: фильтр «Людей» в адресе (PP_SHOW) и части разделов в поиске (N2, N2_ARG, n2Soon); констант RELnn с
    # номером будущего выпуска в 3.3.7.10 нет (серые пункты с номером ушли) — появится снова, добавить сюда
    parts += [jsparts.const(APP, n) for n in ("qs", "FOCUS_ID", "TRACE_REF", "PP_SHOW", "SECTIONS", "SECTION_BY", "secOf", "isItem", "navOk",
                                              "N2", "N2_ARG", "n2Soon")]
    parts += [jsparts.function(APP, n) for n in ("navState", "sectionCatalog")]
    return "\n".join(parts)


def dict_scope():
    """t, tn, raw and addDict of app.js over DICT (the core dictionary as boot() builds it)."""
    parts = [jsparts.const(APP, n) for n in ("missing", "fill", "raw", "t", "pluralIdx", "tn", "DICT_OWNER")]
    parts += [jsparts.function(APP, "addDict")]
    return """
const fmtInt = (n) => String(n);
let DICT = LANG === "en" ? Object.assign({}, window.KASKAD_I18N || {}, window.KASKAD_I18N_EN || {}) : (window.KASKAD_I18N || {});
%s
""" % "\n".join(parts)


@unittest.skipUnless(HAS_NODE, "no node")
class AddressTest(unittest.TestCase):
    """One address scheme for every section: navState takes it by the inventory — a section of pages/ that is not
    loaded yet works by its link; ?tab, ?focus, ?open that the section does not know are dropped; old addresses keep."""

    def test_navstate_by_the_inventory(self):
        out = run_node(window_with("ru", []) + """
const location = { hash: "" };
const t = (k) => k;
%s
const go = (h) => { location.hash = h; return navState(); };
console.log(JSON.stringify({
  oldPeople: go("#people?focus=u-1a2b3c4d&open=route"), oldDev: go("#people?focus=d-1a2b3c4d&open=settings"),
  badOpen: go("#people?focus=u-1a2b3c4d&open=evil"), badFocus: go("#people?focus=../x"),
  lazy: go("#dev-kit?tab=empty"), lazyBadTab: go("#dev-kit?tab=nope"), tabElsewhere: go("#people?tab=empty"),
  settings: go("#settings?open=bot"), settingsBad: go("#settings?open=route"), inbounds: go("#inbounds?open=vless"),
  inboundsBad: go("#inbounds?open=VLESS<"), focusOnOverview: go("#overview?focus=u-1a2b3c4d"),
  unknown: go("#nosuch?tab=empty"), empty: go(""), trace: go("#scheme?trace=u-1a2b3c4d:youtube.com&pin=1"),
  show: [go("#people?show=noexit").show, go("#people?show=on&focus=u-1a2b3c4d").show, go("#people?show=evil").show, go("#people?show=constructor").show,
    go("#exits?show=on").show]
}));
""" % nav_scope())
        self.assertEqual(out["oldPeople"], {"page": "people", "tab": "", "focus": "u-1a2b3c4d", "open": "route", "from": "",
                                            "view": "", "trace": None, "pin": False, "show": ""})
        # nav2 r1: фильтр «Людей» в адресе — только известный и только у «Людей»
        self.assertEqual(out["show"], ["noexit", "on", "", "", ""])
        self.assertEqual((out["oldDev"]["focus"], out["oldDev"]["open"]), ("d-1a2b3c4d", "settings"))
        self.assertEqual((out["badOpen"]["focus"], out["badOpen"]["open"]), ("u-1a2b3c4d", ""))
        self.assertIsNone(out["badFocus"]["focus"])
        self.assertEqual((out["lazy"]["page"], out["lazy"]["tab"]), ("dev-kit", "empty"))
        self.assertEqual((out["lazyBadTab"]["page"], out["lazyBadTab"]["tab"]), ("dev-kit", ""))
        self.assertEqual(out["tabElsewhere"]["tab"], "")
        self.assertEqual(out["settings"]["open"], "bot")
        self.assertEqual(out["settingsBad"]["open"], "")
        self.assertEqual(out["inbounds"]["open"], "vless")
        self.assertEqual(out["inboundsBad"]["open"], "")
        self.assertIsNone(out["focusOnOverview"]["focus"])
        self.assertEqual((out["unknown"]["page"], out["unknown"]["tab"]), ("overview", ""))
        self.assertEqual(out["empty"]["page"], "overview")
        self.assertEqual((out["trace"]["page"], out["trace"]["trace"], out["trace"]["pin"]), ("scheme", "u-1a2b3c4d:youtube.com", True))

    PROTO = ("constructor", "__proto__", "toString", "valueOf", "hasOwnProperty", "isPrototypeOf", "__defineGetter__")

    def test_names_of_the_object_prototype_are_not_sections(self):
        """С-1 (REV-s1-f0): #constructor, #__proto__, #toString … — «Обзор», без падения (раньше — TypeError в
        navState, панель пустая); ?focus=/?open= с такими именами отбрасываются."""
        out = run_node(window_with("ru", []) + """
const location = { hash: "" };
const t = (k) => k;
%s
const r = Object.create(null);                     // ключ __proto__ — свой
for (const name of %s) {
  location.hash = "#" + name + "?tab=constructor&focus=__proto__&open=toString";
  try { const n = navState(); r[name] = [n.page, n.tab, n.focus, n.open, secOf(name)]; } catch (e) { r[name] = "THROW " + e.message; }
}
r.settingsFocus = (location.hash = "#settings?focus=constructor", navState().focus);
console.log(JSON.stringify(r));
""" % (nav_scope(), json.dumps(self.PROTO)))
        for name in self.PROTO:
            self.assertEqual(out[name], ["overview", "", None, "", None], name)
        self.assertIsNone(out["settingsFocus"])

    def test_dev_section_only_with_mock(self):
        """З-3 (UX §7): #dev-kit — только при ?mock=1; на живой панели — как неизвестный адрес."""
        js = """
const location = { hash: "" };
const t = (k) => k;
%s
location.hash = "#dev-kit?tab=empty";
const n = navState();
console.log(JSON.stringify({ page: n.page, tab: n.tab, sec: !!secOf("dev-kit") }));
"""
        live = run_node(window_with("ru", []) + js % nav_scope(mock=False))
        mock = run_node(window_with("ru", []) + js % nav_scope(mock=True))
        self.assertEqual(live, {"page": "overview", "tab": "", "sec": False})
        self.assertEqual(mock, {"page": "dev-kit", "tab": "empty", "sec": True})

    def test_focus_takes_the_records_of_the_section(self):
        """З-1: ?focus=<id записи описи> — у раздела этой записи; чужой id, запись другого раздела — отброшены."""
        out = run_node(window_with("ru", []) + """
const location = { hash: "" };
const t = (k) => k;
%s
const go = (h) => { location.hash = h; return navState(); };
console.log(JSON.stringify({
  bot: go("#settings?focus=settings.bot&open=bot"), mcp: go("#settings?focus=settings.mcp"),
  evil: go("#settings?focus=settings.evil"), quote: go('#settings?focus=settings.bot"]'),
  other: go("#people?focus=settings.bot"), people: go("#people?focus=u-1a2b3c4d")
}));
""" % nav_scope())
        self.assertEqual((out["bot"]["focus"], out["bot"]["open"]), ("settings.bot", "bot"))
        self.assertEqual(out["mcp"]["focus"], "settings.mcp")
        self.assertIsNone(out["evil"]["focus"])
        self.assertIsNone(out["quote"]["focus"])
        self.assertIsNone(out["other"]["focus"])
        self.assertEqual(out["people"]["focus"], "u-1a2b3c4d")


@unittest.skipUnless(HAS_NODE, "no node")
class SearchInventoryTest(unittest.TestCase):
    """The format of the inventory for the search «как в Настройках iPhone» (3.3.6, BACKLOG-3.3.6 п. 3): every record —
    id, path (section → tab → card), label and synonyms RU/EN, an anchor that the address scheme accepts."""

    def catalog(self, lang):
        files = ["i18n-ru.js", "ux-i18n-ru.js"] + (["i18n-en.js", "ux-i18n-en.js"] if lang == "en" else [])
        return run_node(window_with(lang, files) + """
const location = { hash: "" };
let DICT = LANG === "en" ? Object.assign({}, window.KASKAD_I18N, window.KASKAD_I18N_EN) : window.KASKAD_I18N;
const t = (k) => (DICT[k] === undefined ? "MISSING:" + k : DICT[k]);
%s
const cat = sectionCatalog();
const back = cat.filter((r) => r.kind !== "soon").map((r) => { location.hash = r.href; const n = navState(); return [r.id, n.page, n.tab, n.focus, n.open, n.show]; });
console.log(JSON.stringify({ cat, back }));
""" % nav_scope())

    def test_records_and_anchors(self):
        for lang in ("ru", "en"):
            out = self.catalog(lang)
            cat = dict((r["id"], r) for r in out["cat"])
            self.assertEqual(len(cat), len(out["cat"]), "id повторяется")
            self.assertNotIn("MISSING", json.dumps(out["cat"]), lang)
            self.assertNotIn("dev-kit", cat)                                      # служебный раздел — не в поиске
            for sid in ("overview", "people", "settings"):
                self.assertEqual(cat[sid]["kind"], "section")
            bot, mcp = cat["settings.bot"], cat["settings.mcp"]
            self.assertEqual((bot["href"], mcp["href"]), ("#settings?tab=bot&focus=settings.bot&open=bot", "#settings?tab=bot&focus=settings.mcp&open=mcp"))   # UI-5: часть «Бот и ИИ-агент»
            for rec in (bot, mcp):
                self.assertTrue(rec["hint"], rec["id"])                         # подсказка — для поиска (п. 3)
            self.assertEqual(bot["kind"], "card")
            if lang == "ru":
                self.assertEqual(bot["path"], ["Настройки", "Бот и ИИ-агент"])
                self.assertEqual(bot["label"], "Telegram-бот")
                self.assertIn("уведомления", bot["hint"])
                # подсказка — подзаголовок карточки общего компонента, без точки (одна строка, REV-s1-f0-2 П-3)
                self.assertEqual(bot["hint"], "Управление сервером с телефона и уведомления о событиях")
                self.assertIn("телеграм", bot["words"])
                self.assertEqual(cat["settings"]["words"][:2], ["Настройки", "параметры"])      # раздел — с синонимами (3.3.6)
                # пример владельца: «пароль» → «Доступ к панели → Пароль панели» (действие: окно пароля, BACKLOG-3.3.6 п. 3)
                pw = cat["settings.password"]
                self.assertEqual((pw["label"], pw["path"], pw["kind"]), ("Пароль панели", ["Настройки", "Доступ к панели"], "action"))
                self.assertEqual(pw["href"], "#settings?tab=access&open=password")
                self.assertIn("пароль", pw["words"])
                # подпись не повторяется в пути: карточка «Доступ к панели» — путь «Настройки»
                self.assertEqual(cat["settings.access"]["path"], ["Настройки"])
            else:
                self.assertEqual(bot["path"], ["Settings", "Bot and AI agent"])
                self.assertEqual(mcp["label"], "AI agent key")
                self.assertIn("Claude", mcp["hint"])
                self.assertEqual(mcp["hint"], "Claude Code, Claude Desktop or Cursor manage the node")
                self.assertIn("agent", mcp["words"])
                self.assertIn("агент", mcp["words"])                            # русские синонимы ищутся и на английском
                self.assertIn("Ключ ИИ-агента", mcp["words"])
            # якорь — адрес, который navState принимает целиком (раздел, вкладка, объект, окно)
            for rid, page, tab, focus, open_, show in out["back"]:
                rec = cat[rid]
                q = dict(p.split("=", 1) for p in rec["href"].partition("?")[2].split("&") if p)
                self.assertEqual(page, rec["href"][1:].split("?")[0], rid)
                self.assertEqual((tab or None, focus, open_ or None, show or None), (q.get("tab"), q.get("focus"), q.get("open"), q.get("show")), rid)
            # nav2 r1: будущие пункты подменю — в поиске серым, без адреса (Enter их не открывает)
            soon = [r for r in out["cat"] if r["kind"] == "soon"]
            self.assertTrue(soon)
            self.assertEqual([r["href"] for r in soon], [""] * len(soon))
            self.assertIn("people.soon.limits", cat)
            self.assertEqual(cat["people.part.on"]["href"], "#people?show=on")

    def test_keys_of_the_inventory_are_in_both_core_dictionaries(self):
        """Labels and synonyms must be known before a lazy section loads: only the core dictionaries (RU and EN)."""
        body = APP[APP.index("  const SECTIONS = ["):APP.index("  const SECTION_BY = ")]
        keys = set(re.findall(r'(?:label|group|syn|hint): "([A-Za-z0-9_.]+)"', body))
        self.assertIn("find.botSyn", keys)
        for name in ("i18n-ru.js", "i18n-en.js"):
            have = set(re.findall(r'^\s*"([A-Za-z0-9_.]+)":', read(name), flags=re.M))
            self.assertEqual(sorted(keys - have), [], name)
        # hintUx — подзаголовок карточки общего компонента: ключ словарей ux.js (ux-i18n-ru.js — в index.html, -en.js —
        # в boot() до разделов), копии в словаре ядра нет (REV-s1-f0-2 П-3)
        ux = set(re.findall(r'hintUx: "([A-Za-z0-9_.]+)"', body))
        self.assertEqual(ux, {"tb.sub", "ak.sub"})
        for name in ("ux-i18n-ru.js", "ux-i18n-en.js"):
            have = set(re.findall(r'^\s*"([A-Za-z0-9_.]+)":', read(name), flags=re.M))
            self.assertEqual(sorted(ux - have), [], name)
        for name in ("i18n-ru.js", "i18n-en.js"):
            self.assertNotRegex(read(name), r'"find\.\w+Hint"', name)

    def test_every_record_has_its_element(self):
        """REV-s1-f0-2 З-2: каждая запись описи помечена на своём настоящем элементе (data-find = id записи), у подсветки
        есть вид — иначе ?focus= молча ничего не показывает."""
        body = APP[APP.index("  const SECTIONS = ["):APP.index("  const SECTION_BY = ")]
        recs = re.findall(r'\{ id: "([a-z-]+\.[A-Za-z0-9_.-]+)", kind:([^\n]*)', body)
        ids = [r for r, _tail in recs]
        self.assertIn("settings.bot", ids)
        self.assertGreater(len(ids), 40)                                             # 3.3.6: каждая настройка и действие
        rest = APP.replace(body, "")
        # B15: ленивый раздел из pages/ (K.page) помечает свои записи сам — его код тоже. Части разделов ядра (K.part)
        # держат и свои якоря вне описи (overview.sum, exits.group.<id>…) — для обратной проверки их нет, но запись описи
        # может жить в части: круг 1 Т1 перенёс «Здоровье сервера» в pages/ovb.js, и якорь overview.host — только там
        # (старый «Обзор» в app.js снят, REV-it1 №4). Её код ищется для записей, не для «каждый якорь — запись».
        pages = os.path.join(STATIC, "pages")
        parts = ""
        for n in sorted(os.listdir(pages)):
            if not n.endswith(".js") or "-i18n-" in n:
                continue
            if "K.page(" in read("pages/" + n):
                rest += "\n" + read("pages/" + n)
            elif "K.part(" in read("pages/" + n):
                parts += "\n" + read("pages/" + n)
        self.assertIn('"data-find": "overview.host"', parts)                        # запись живёт в части ovb
        # 3.3.6: якорь — o.find у U.btn/sw/field/code/sec, findAt(id, узел) или dataset.find; у раздела телефона и компьютера бывает своя
        # разметка — тогда помечены обе (в DOM раздела — одна, проверка tools/k3_find_check.mjs). mark: false — действие
        # без одной кнопки (окно открывает адрес ?open=)
        for rid, tail in recs:
            marks = len(re.findall(r'(?:dataset\.find = |"data-find": |find: |findAt\()"%s"' % re.escape(rid), rest + parts))
            if "mark: false" in tail:
                self.assertEqual(marks, 0, rid)
                self.assertIn("open: ", tail, rid)
            else:
                self.assertGreaterEqual(marks, 1, rid)
        # и наоборот: каждый якорь в коде — запись описи
        found = set(re.findall(r'(?:dataset\.find = |"data-find": |find: |findAt\()"([a-z]+\.[A-Za-z0-9_.-]+)"', rest))
        self.assertEqual(sorted(found - set(ids)), [])
        css = read("styles.css")
        self.assertRegex(css, r"\[data-find\]\.is-flash \{ box-shadow: 0 0 0 3px var\(--accent\)")
        self.assertRegex(css, r"\[data-find\] \{ scroll-margin-top: 8px;")          # П-1: рамку не срезает край
        self.assertNotIn("is-found", css + APP)                                      # П-2: одна подсветка — is-flash


@unittest.skipUnless(HAS_NODE, "no node")
class DictionariesTest(unittest.TestCase):
    """The dictionary of Kit goes into DICT in boot() after the core one; a section's — in K.loadPage; English over
    Russian, a missing English key stays Russian; a key of the core (or of another dictionary) is never overwritten."""

    def test_kit_in_dict_ru_and_en(self):
        for lang, want in (("ru", "Здесь пока пусто"), ("en", "Nothing here yet")):
            files = ["i18n-ru.js", "kit-i18n-ru.js"] + (["i18n-en.js", "kit-i18n-en.js"] if lang == "en" else [])
            out = run_node(window_with(lang, files) + dict_scope() + """
const core = window.KASKAD_I18N["nav.settings"];
const taken = addDict("kit");
console.log(JSON.stringify({ kit: t("kit.empty"), tabs: t("kit.tabs"), nav: t("nav.settings"), taken, errors: ERRORS,
                             coreUntouched: window.KASKAD_I18N["kit.empty"] === undefined && window.KASKAD_I18N["nav.settings"] === core }));
""")
            self.assertEqual(out["kit"], want, lang)
            self.assertEqual((out["taken"], out["errors"]), (0, []), lang)
            self.assertTrue(out["coreUntouched"], lang)
            self.assertEqual(out["nav"], "Настройки" if lang == "ru" else "Settings")

    def test_fallback_and_no_overwrite(self):
        out = run_node(window_with("en", ["i18n-ru.js", "i18n-en.js"]) + dict_scope() + """
window.KASKAD_I18N_ADD = { sec: { "sx.a": "А", "sx.b": "Б", "nav.settings": "ЧУЖОЕ" }, other: { "sx.a": "ДРУГОЕ", "ox.c": "В" } };
window.KASKAD_I18N_ADD_EN = { sec: { "sx.a": "A", "nav.settings": "FOREIGN" } };
const t1 = addDict("sec");
const again = addDict("sec");                              // повторный вход в раздел — те же ключи, не ошибка
const t2 = addDict("other");
console.log(JSON.stringify({ a: t("sx.a"), b: t("sx.b"), nav: t("nav.settings"), c: t("ox.c"), t1, again, t2, errors: ERRORS }));
""")
        self.assertEqual(out["a"], "A")                       # английский поверх русского
        self.assertEqual(out["b"], "Б")                       # нет английского — русский запас
        self.assertEqual(out["nav"], "Settings")              # ключ ядра не перезаписан
        self.assertEqual(out["c"], "В")
        self.assertEqual((out["t1"], out["again"], out["t2"]), (2, 2, 1))
        self.assertEqual(len(out["errors"]), 5)
        self.assertTrue(all(e.startswith("i18n: key ") for e in out["errors"]), out["errors"])

    def test_boot_builds_dict_then_kit_then_init(self):
        boot = jsparts.function(APP, "boot")
        i_ux, i_kit = boot.index('loadScript("ux-i18n-en.js")'), boot.index('loadScript("kit-i18n-en.js")')
        i_dict, i_add, i_init = boot.index("DICT = Object.assign("), boot.index('addDict("kit")'), boot.index("initParts()")
        self.assertLess(i_ux, i_kit)
        self.assertLess(i_kit, i_dict)
        self.assertLess(i_dict, i_add)
        self.assertLess(i_add, i_init)
        self.assertLess(i_init, boot.index("start()"))


@unittest.skipUnless(HAS_NODE, "no node")
class LazySectionTest(unittest.TestCase):
    """K.loadPage: the section's Russian dictionary, with English the English one over it (missing — Russian), the
    merge, then pages/<id>.js that registers itself by K.page; a failure is a rejection and the next entry loads again."""

    def scope(self, lang):
        parts = [jsparts.const(APP, n) for n in ("qs", "FOCUS_ID", "SECTIONS", "SECTION_BY", "secOf", "pageLoads", "DICT_OWNER")]
        parts += [jsparts.function(APP, n) for n in ("addDict", "page", "loadPage")]
        return window_with(lang, []) + """
let MOCK = true;
const scripts = {};
let DICT = { "nav.settings": "Settings" };
const PAGES = { overview: { res: [] } };
const LOADED = [];
const FAIL = new Set();
const FILES = {
  "pages/dev-kit-i18n-ru.js": () => { window.KASKAD_I18N_ADD = { "dev-kit": { "dk.a": "А", "dk.b": "Б" } }; },
  "pages/dev-kit-i18n-en.js": () => { window.KASKAD_I18N_ADD_EN = { "dev-kit": { "dk.a": "A" } }; },
  "pages/dev-kit.js": () => { page("dev-kit", { build() {}, nav() { return true; } }); },
};
function loadScript(name) {
  LOADED.push(name);
  if (FAIL.has(name) || !FILES[name]) return Promise.reject(new Error("not loaded: " + name));
  FILES[name]();
  return Promise.resolve();
}
%s
""" % "\n".join(parts)

    def test_en_success_order_and_dictionary(self):
        out = run_node(self.scope("en") + """
(async () => {
  const p1 = loadPage("dev-kit"), p2 = loadPage("dev-kit");
  const same = p1 === p2;
  const def = await p1;
  const again = await loadPage("dev-kit");
  console.log(JSON.stringify({ same, loaded: LOADED, a: DICT["dk.a"], b: DICT["dk.b"], res: def.res, sameDef: again === def,
                               errors: ERRORS }));
})();
""")
        self.assertTrue(out["same"])                          # один запрос на раздел
        self.assertEqual(out["loaded"], ["pages/dev-kit-i18n-ru.js", "pages/dev-kit-i18n-en.js", "pages/dev-kit.js"])
        self.assertEqual((out["a"], out["b"]), ("A", "Б"))
        self.assertEqual(out["res"], [])
        self.assertTrue(out["sameDef"])
        self.assertEqual(out["errors"], [])

    def test_ru_and_missing_english_and_retry(self):
        out = run_node(self.scope("en") + """
(async () => {
  FAIL.add("pages/dev-kit-i18n-en.js");
  FAIL.add("pages/dev-kit.js");
  let first = null;
  try { await loadPage("dev-kit"); } catch (e) { first = e.message; }
  FAIL.delete("pages/dev-kit.js");
  await loadPage("dev-kit");
  console.log(JSON.stringify({ first, loaded: LOADED, a: DICT["dk.a"], ok: !!PAGES["dev-kit"], errors: ERRORS }));
})();
""")
        self.assertEqual(out["first"], "not loaded: pages/dev-kit.js")
        self.assertEqual(out["a"], "А")                       # английского словаря нет — русский текст
        self.assertTrue(out["ok"])
        self.assertEqual(out["loaded"].count("pages/dev-kit.js"), 2)            # после ошибки — заново
        self.assertEqual(out["errors"], [])

    def test_refusals(self):
        out = run_node(self.scope("ru") + """
(async () => {
  const r = {};
  for (const id of ["nosuch", "overview", "settings"]) { try { await loadPage(id); r[id] = "ok"; } catch (e) { r[id] = e.message; } }
  r.ovIsCore = (await Promise.resolve(PAGES.overview)) === PAGES.overview;
  r.pageCore = page("overview", { build() {} });
  r.pageUnknown = page("nosuch", { build() {} });
  r.pageNoBuild = page("dev-kit", {});
  r.pageOk = page("dev-kit", { build() {} });
  r.pageTwice = page("dev-kit", { build() {} });
  FILES["pages/dev-kit.js"] = () => {};                    // файл есть, а раздел себя не зарегистрировал
  delete PAGES["dev-kit"];
  try { await loadPage("dev-kit"); r.silent = "ok"; } catch (e) { r.silent = e.message; }
  console.log(JSON.stringify(Object.assign(r, { errors: ERRORS })));
})();
""")
        self.assertEqual(out["nosuch"], "unknown section: nosuch")
        self.assertEqual(out["overview"], "ok")               # раздел ядра уже есть
        self.assertEqual(out["settings"], "not a lazy section: settings")
        self.assertEqual((out["pageCore"], out["pageUnknown"], out["pageNoBuild"], out["pageOk"], out["pageTwice"]),
                         (False, False, False, True, False))
        self.assertEqual(out["silent"], "section did not register: dev-kit")
        self.assertEqual(len(out["errors"]), 4)


@unittest.skipUnless(HAS_NODE, "no node")
class KitAndTemplatesTest(unittest.TestCase):
    """kit.js and templates.js: nothing at load; a call before init — an error in the console and an exception; init
    checks its helpers and layers; Tpl.head/tabs/state are built from the passed U.* only."""

    @property
    def SCOPE(self):
        return self.SCOPE_SRC % (read("kit.js"), read("templates.js"))

    SCOPE_SRC = """
const window = globalThis;
globalThis.ERRORS = [];
globalThis.console = { error: (m) => { ERRORS.push(String(m)); }, log: (m) => process.stdout.write(m + "\\n") };
%s
%s
const CALLS = [];
const node = (tag, a, kids) => ({ tag, a: a || {}, kids: (kids || []).flat().filter(Boolean), append(...k) { this.kids.push(...k.flat().filter(Boolean)); } });
const el = (tag, a, ...kids) => node(tag, a, kids);
const U = {
  btn: (label, kind, fn, o) => { CALLS.push(["btn", label, kind, !!(o && o.md)]); return node("button", { label, kind }); },
  menu: (items, o) => { CALLS.push(["menu", items.length, o.label]); return node("menu", {}); },
  seg: (items, o) => { CALLS.push(["seg", items.map((x) => [x.label, x.on]), o.tabs, o.aria]); items[2].pick(); items[0].pick(); return node("seg", {}); },
  skel: (n, o) => { CALLS.push(["skel", n, o.card]); return node("skel", {}); },
  empty: (ic, title, text, o) => { CALLS.push(["empty", ic, title, text, o.btns.length]); return node("empty", {}); },
  state: (kind, msg, retry, title) => { CALLS.push(["state", kind, msg, typeof retry, title]); return node("state", {}); },
};
const fn = () => null;
const layers = { openDialog: fn, openSheet: fn, openMenu: fn, confirmDialog: fn, typedConfirm: fn, toast: fn };
const H = { el, t: (k) => "T:" + k, tn: (k, n) => k + n, icon: fn, isPhone: () => false, U, layers };
"""

    def test_before_init_is_an_error(self):
        out = run_node(self.SCOPE + """
const r = {};
for (const [name, call] of [["head", () => KaskadT.head({})], ["tabs", () => KaskadT.tabs({})], ["state", () => KaskadT.state("loading")],
                            ["kit", () => Kit.need("x")]]) {
  try { call(); r[name] = "no error"; } catch (e) { r[name] = e.message; }
}
r.ready = [Kit.ready(), KaskadT.ready()];
try { Kit.init({ el, t: H.t }); } catch (e) { r.kitMissing = e.message; }
try { KaskadT.init(Object.assign({}, H, { layers: { openDialog: fn } })); } catch (e) { r.tplMissing = e.message; }
try { KaskadT.init(H); } catch (e) { r.tplNoKit = e.message; }
r.after = [Kit.ready(), KaskadT.ready()];
console.log(JSON.stringify(Object.assign(r, { errors: ERRORS })));
""")
        self.assertEqual(out["head"], "KaskadT.head: KaskadT.init was not called")
        self.assertEqual(out["tabs"], "KaskadT.tabs: KaskadT.init was not called")
        self.assertEqual(out["state"], "KaskadT.state: KaskadT.init was not called")
        self.assertEqual(out["kit"], "Kit.x: Kit.init was not called")
        self.assertEqual(out["ready"], [False, False])
        self.assertEqual(out["kitMissing"], "Kit.init: missing tn, icon, isPhone, U, layers")
        self.assertEqual(out["tplMissing"], "KaskadT.init: missing Kit, layers.openSheet, layers.openMenu, layers.confirmDialog, "
                                            "layers.typedConfirm, layers.toast")
        self.assertEqual(out["tplNoKit"], "KaskadT.init: missing Kit")
        self.assertEqual(out["after"], [False, False])
        self.assertEqual(len(out["errors"]), 7)              # каждая ошибка — и в консоли

    def test_templates_after_init(self):
        out = run_node(self.SCOPE + """
Kit.init(H);
KaskadT.init(Object.assign({ Kit }, H));
const picked = [];
const head = KaskadT.head({ title: "Заголовок", text: "Пояснение", action: { label: "Действие", onClick: fn }, more: [{ label: "x", on: fn }] });
const bare = KaskadT.head({ title: "Только заголовок" });
KaskadT.tabs({ items: [{ id: "a", label: "A" }, { id: "b", label: "B" }, { id: "c", label: "C" }], value: "a", onChange: (id) => picked.push(id) });
KaskadT.state("loading");
KaskadT.state("loading", { rows: 5, card: true });
KaskadT.state("empty", { text: "пусто", action: node("button", {}) });
KaskadT.state("error", { text: "сбой", onRetry: fn });
KaskadT.state("error", { title: "Заголовок сбоя", text: "сбой 2" });
let unknown = null;
try { KaskadT.state("weird"); } catch (e) { unknown = e.message; }
const cls = (n) => n.a.class;
console.log(JSON.stringify({ calls: CALLS, picked, unknown, ready: [Kit.ready(), KaskadT.ready()],
  head: [cls(head), head.kids.map(cls), head.kids[0].kids[0].kids.map((k) => [k.tag, k.a.class, k.a.text])],
  bare: bare.kids.length, errors: ERRORS }));
""")
        self.assertEqual(out["ready"], [True, True])
        self.assertEqual(out["head"][0], "gx-card__head")
        self.assertEqual(out["head"][1], ["gx-card__lead", "gx-card__acts"])
        self.assertEqual(out["head"][2], [["h2", "gx-h2", "Заголовок"], ["div", "gx-desc", "Пояснение"]])
        self.assertEqual(out["bare"], 1)                      # без действий — без ряда действий
        calls = out["calls"]
        self.assertIn(["btn", "Действие", "primary", True], calls)
        self.assertIn(["menu", 1, "Заголовок"], calls)
        self.assertIn(["seg", [["A", True], ["B", False], ["C", False]], True, "T:kit.tabs"], calls)
        self.assertEqual(out["picked"], ["c"])                # своя вкладка — без перехода
        self.assertIn(["skel", 3, False], calls)
        self.assertIn(["skel", 5, True], calls)
        self.assertIn(["empty", "circle-info", "T:kit.empty", "пусто", 1], calls)
        self.assertIn(["state", "error", "сбой", "function", None], calls)
        self.assertIn(["state", "error", "сбой 2", "object", "Заголовок сбоя"], calls)       # заголовок ошибки (З-2)
        self.assertEqual(out["unknown"], "KaskadT.state: unknown kind weird")

    def test_state_of_the_panel_takes_the_title(self):
        """U.state панели (app.js, настоящий) — заголовок ошибки свой, без него — общий «Не загрузилось» (З-2)."""
        i = APP.index("    state(kind, message, retry, title) {")
        method = APP[i:jsparts.close_of(APP, APP.index("{", i)) + 1]
        out = run_node("""
const t = (k) => "T:" + k;
const U = { skel: (n) => ["skel", n], al: (tone, title, text, o) => [tone, title, text, o.btns.length], btn: () => "btn",
%s };
console.log(JSON.stringify([U.state("error", "сбой", () => {}, "Свой заголовок"), U.state("error", "сбой"), U.state("loading")]));
""" % method)
        self.assertEqual(out, [["danger", "Свой заголовок", "сбой", 1], ["danger", "T:err.loadFailed", "сбой", 0], ["skel", 3]])

    def test_files_only_declare(self):
        """At load the files touch nothing but window.Kit / window.KaskadT (UX §3.3: no cycle with app.js)."""
        for name, glob_ in (("kit.js", "Kit"), ("templates.js", "KaskadT")):
            src = read(name)
            self.assertNotIn("KaskadPanel", src.split("*/", 1)[1], name)          # K не зовут
            self.assertEqual(re.findall(r"window\.(\w+)", src.split("*/", 1)[1]), [glob_], name)
            self.assertNotIn("`", src, name)                                    # tests/jsparts.py без шаблонных строк


class CoreWiringTest(unittest.TestCase):
    """app.js: init gets every helper, tn and the layers; K is frozen and carries what UX §3.3 lists; a section that is
    not in index.html gets its <section>; the hash handler and «Обновить» survive a section that is not loaded yet."""

    def test_init_parts(self):
        body = jsparts.function(APP, "initParts")
        self.assertIn("const h = { el, t, tn, icon, isPhone, U, layers: LAYERS };", body)
        self.assertIn("window.Kit.init(h)", body)
        self.assertIn("window.KaskadT.init(Object.assign({ Kit: window.Kit }, h))", body)
        # 3.3.6: + openBottom (лист снизу на телефоне); T8: + confirmLife — жизнь подтверждения на месте для Kit.confirmAction
        self.assertIn("const LAYERS = Object.freeze({ openDialog, openSheet, openMenu, confirmDialog, typedConfirm, toast, openBottom, confirmLife });", APP)

    def test_k(self):
        m = re.search(r"window\.KaskadPanel = Object\.freeze\(\{(.*?)\}\);", APP, flags=re.S)
        self.assertIsNotNone(m)
        names = set(x.strip().split(":")[0] for x in m.group(1).split(","))
        for need in ("el", "t", "tn", "icon", "U", "Tpl", "Kit", "API", "R", "load", "loadAll", "blk", "openDialog",
                     "openSheet", "openMenu", "confirmDialog", "typedConfirm", "toast", "goNav", "page", "loadPage",
                     "navState", "fmtInt"):
            self.assertIn(need, names)
        self.assertLess(APP.index("window.KaskadPanel ="), APP.index("  // ── 14. Обзор"))

    def test_refresh_survives_a_section_not_loaded_yet(self):
        # поведение setPage/buildPage/buildLazy/hashchange — PageBehaviourTest (node, поддельный DOM)
        self.assertIn("(PAGES[S.page] ? PAGES[S.page].res : [])", jsparts.function(APP, "manualRefresh"))
        for t_ in ("PAGES", "NAV_BY", "pageLoads"):
            self.assertRegex(jsparts.const(APP, t_), r"=\s*Object\.create\(null\)", t_)    # С-1: таблицы без прототипа


def hashchange_handler():
    """The anonymous «hashchange» handler of app.js, whole."""
    i = APP.index('window.addEventListener("hashchange", ')
    return APP[i:jsparts.close_of(APP, APP.index("(", i)) + 1] + ";"


@unittest.skipUnless(HAS_NODE, "no node")
class PageBehaviourTest(unittest.TestCase):
    """setPage / buildPage / buildLazy / loadPage / loadScript / focusFind and the «hashchange» handler of app.js — the
    real code (cut by name) over a fake DOM: the section is shown, «Повторить» loads the section without a reload, an
    outdated answer is dropped, F5 keeps the tab, ?focus=<record> finds and highlights its element (З-2, З-1, С-1, П-1)."""

    def scope(self, mock=True):
        consts = ["qs", "FOCUS_ID", "TRACE_REF", "SECTIONS", "SECTION_BY", "secOf", "isItem", "navOk", "pageLoads",
                  "scripts", "reducedMotion", "pageView", "stDeskTab", "stHash", "pageDirty", "pageSave", "tabOfHash",
                  "KS", "SM"]                                          # «Режим стрима»: setPage прячет показанное (в node — null)
        parts = [jsparts.const(APP, n) for n in consts]
        # UI-5: части «Настроек» (stTabOf — функция: часть записи описи по ?focus=); набор частей — как в app.js
        parts.append("const ST_PARTS = { look: 1, access: 1, gateway: 1, bot: 1, site: 1, github: 1, notify: 1, backup: 1, about: 1 }, nf = { missing: false };")
        parts.append(jsparts.const(APP, "stHas"))
        parts.append(jsparts.function(APP, "stTabOf"))
        parts.append(jsparts.const(APP, "hashShown", keyword="let"))         # адрес на экране (вопрос при уходе, T8)
        self.assertEqual(jsparts.const(APP, "FLASH_MS"), "const FLASH_MS = 2000;")       # подсветка — 2 с (BACKLOG-3.3.6 п. 3)
        self.assertEqual(jsparts.const(APP, "FIND_WAIT_MS"), "const FIND_WAIT_MS = 1500;")  # данные раздела — не дольше 1,5 с
        parts.append("const FLASH_MS = 150, FIND_WAIT_MS = 100;")                          # в тесте — короче
        parts.append(jsparts.const(APP, "lazyWait", keyword="let"))
        parts.append(jsparts.const(APP, "navSeq", keyword="let"))
        parts += [jsparts.function(APP, n) for n in ("navState", "pageFromHash", "canonHash", "pageSection", "buildLazy", "namesWait", "buildPage",
                                                     "dropFocus", "focusFind", "flashNode", "scrollKeep", "settingsFocusUx", "setPage",
                                                     "page", "loadPage", "loadScript")]
        parts.append(hashchange_handler())
        return window_with("ru", []) + """
let MOCK = %s;
const SELF_V = "";
const HANDLERS = {};
window.addEventListener = (name, fn) => { HANDLERS[name] = fn; };
let REDUCED = false;
window.matchMedia = () => ({ matches: REDUCED });
const location = { hash: "" };
const history = { replaceState: (a, b, h) => { location.hash = h; } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// поддельный DOM: узлы с dataset/classList/hidden, $ по «тег#id[data-x="v"]» и потомкам через пробел
class N {
  constructor(tag, a) {
    this.tag = tag; this.kids = []; this.parent = null; this.dataset = {}; this.hidden = false; this.cls = new Set();
    this.id = ""; this.scrolls = [];
    const me = this;
    this.classList = { add: (c) => me.cls.add(c), remove: (c) => me.cls.delete(c), contains: (c) => me.cls.has(c), toggle: (c, on) => (on ? me.cls.add(c) : me.cls.delete(c)) };
    for (const [k, v] of Object.entries(a || {})) {
      if (k === "class") String(v).split(" ").filter(Boolean).forEach((c) => this.cls.add(c));
      else if (k === "hidden") this.hidden = !!v;
      else if (k === "id") this.id = v;
      else if (k.indexOf("data-") === 0) this.dataset[k.slice(5).replace(/-([a-z])/g, (m, c) => c.toUpperCase())] = String(v);
      else this[k] = v;
    }
  }
  append(...k) { k.flat().filter(Boolean).forEach((x) => { x.parent = this; this.kids.push(x); }); }
  replaceChildren(...k) { this.kids = []; this.append(...k); }
  remove() { if (this.parent) this.parent.kids = this.parent.kids.filter((x) => x !== this); }
  scrollIntoView(o) { this.scrolls.push(o || null); }
  getBoundingClientRect() { return { top: 0, height: 0 }; }
  addEventListener() {}
  removeEventListener() {}
  all() { return this.kids.reduce((m, k) => m.concat([k], k.all()), []); }
}
const el = (tag, a, ...kids) => { const n = new N(tag, a); n.append(...kids); return n; };
const ROOT = el("div", {}, el("main", { id: "main" }, el("section", { "data-page": "overview" }), el("section", { "data-page": "settings" })));
const one = (sel) => (n) => {
  const m = /^([a-z]+)?(?:\\.([\\w-]+))?(?:#([\\w-]+))?(?:\\[([\\w-]+)(?:="([^"]*)")?\\])?$/.exec(sel);
  if (!m) throw new Error("selector " + sel);
  const key = m[4] ? m[4].slice(5).replace(/-([a-z])/g, (x, c) => c.toUpperCase()) : null;
  return (!m[1] || n.tag === m[1]) && (!m[2] || n.cls.has(m[2])) && (!m[3] || n.id === m[3]) && (!key || (m[5] === undefined ? key in n.dataset : n.dataset[key] === m[5]));
};
const $$ = (sel, root) => sel.split(" ").reduce((nodes, part, i) => {
  const pool = i === 0 ? nodes : nodes.reduce((m, n) => m.concat(n.all()), []);
  return pool.filter(one(part));
}, (root || ROOT).all());
const $ = (sel, root) => $$(sel, root)[0] || null;
// загрузка файлов: <script> «грузится» через DELAY мс; файла нет или FAIL — onerror
const LOADED = [], FAIL = new Set(), DELAY = {};
let BUILDS = 0, OV_BUILDS = 0;
const NAVS = [];
const register = () => page("dev-kit", { build(root) { BUILDS += 1; root.append(el("div", { class: "dk" })); }, nav(n) { NAVS.push(n.tab); return true; } });
const FILES = { "pages/dev-kit-i18n-ru.js": () => {}, "pages/dev-kit.js": register };
const document = {
  title: "",
  addEventListener() {},
  removeEventListener() {},
  createElement: (tag) => el(tag, {}),
  head: { append(s) {
    const name = s.src.split("?")[0];
    LOADED.push(name);
    setTimeout(() => { if (FAIL.has(name) || !FILES[name]) s.onerror(); else { FILES[name](); s.onload(); } }, DELAY[name] || 0);
  } },
};
const t = (k) => k;
const addDict = () => 0;
const authGate = false;
const S = { page: "overview" };
const layerStack = [];                                  // слои (UI-1 F10: смена раздела закрывает листы)
const renderShell = () => {}, pageTitle = (id) => id;
const isPhone = () => false, mq = () => false, MID_MQ = "";            // вид раздела (pageView): компьютер
let DATA = () => Promise.resolve();                     // данные раздела (loadAll): тест задаёт, когда они придут
const loadAll = () => DATA();
const loadQuiet = () => Promise.resolve();              // 3.3.7.15 V1: полоса ожидания F7 — buildPage спрашивает pending
const U = {
  skel: () => el("div", { class: "skel" }),
  state: (kind, text, retry, title) => { const n = el("div", { class: "err" }); n.retry = retry; n.text = text; n.title = title; return n; },
  al: (k, a, b) => el("div", { class: "al", text: b }),
};
const PAGES = Object.create(null);
PAGES.overview = { res: [], build(root) { OV_BUILDS += 1; root.append(el("div", { class: "ov" })); } };
let SETTINGS_LATE = false, TALL = false;
window.innerHeight = 800;
// как настоящий раздел: ?open=bot|mcp раскрывает карточку и сразу стирает адрес до #settings (settingsFocusUx — настоящий)
PAGES.settings = { res: [], build(root) {
  const bot = el("section", { class: "ux-fold", "data-ux": "bot" });
  bot.dataset.find = "settings.bot";
  if (TALL) bot.getBoundingClientRect = () => ({ height: 2000 });           // открытая карточка выше экрана
  root.append(bot);
  if (SETTINGS_LATE) setTimeout(() => { const m = el("section", {}); m.dataset.find = "settings.mcp"; root.append(m); }, 200);
  if (navState().open === "bot" || navState().open === "mcp") settingsFocusUx(navState().open);
} };
const botNode = () => $('section[data-page="settings"] [data-find="settings.bot"]');
const sec = (id) => $('section[data-page="' + id + '"]');
const view = (id) => { const n = sec(id); return n ? { hidden: n.hidden, kids: n.kids.map((k) => [...k.cls].join(" ")) } : null; };
%s
""" % ("true" if mock else "false", "\n".join(parts))

    def run_case(self, body, mock=True):
        return run_node(self.scope(mock) + "\n(async () => {\n" + body + "\n})().catch((e) => console.log(JSON.stringify({ THROW: String(e && e.stack || e) })));\n")

    def test_show_section_and_keep_the_address(self):
        """Первый вход по адресу (как F5): секция ленивого раздела создана и видна, остальные скрыты; адрес с ?tab= не
        переписан; после загрузки — раздел собран один раз; вкладка «Назад» — без пересборки."""
        out = self.run_case("""
location.hash = "#dev-kit?tab=empty";
setPage(pageFromHash(), false);
const early = { dk: view("dev-kit"), ov: view("overview"), hash: location.hash };
await sleep(30);
const built = { dk: view("dev-kit"), builds: BUILDS, hash: location.hash };
location.hash = "#dev-kit?tab=error";
HANDLERS.hashchange();
const tab = { builds: BUILDS, navs: NAVS.slice(), dk: view("dev-kit") };
location.hash = "#overview";
HANDLERS.hashchange();
console.log(JSON.stringify({ early, built, tab, back: { page: S.page, dk: view("dev-kit"), ov: view("overview") } }));
""")
        self.assertNotIn("THROW", out)
        self.assertEqual(out["early"]["dk"], {"hidden": False, "kids": ["skel"]})       # видна сразу, скелетон
        self.assertTrue(out["early"]["ov"]["hidden"])
        self.assertEqual(out["early"]["hash"], "#dev-kit?tab=empty")                  # F5 сохраняет вкладку
        self.assertEqual(out["built"], {"dk": {"hidden": False, "kids": ["dk"]}, "builds": 1, "hash": "#dev-kit?tab=empty"})
        self.assertEqual(out["tab"]["builds"], 1)                                     # вкладка — без пересборки
        self.assertEqual(out["tab"]["navs"], ["error"])
        self.assertEqual(out["back"]["page"], "overview")
        self.assertTrue(out["back"]["dk"]["hidden"])
        self.assertFalse(out["back"]["ov"]["hidden"])

    def test_menu_entry_rewrites_the_address(self):
        out = self.run_case("""
location.hash = "#overview";
setPage("dev-kit");
console.log(JSON.stringify({ hash: location.hash, page: S.page }));
""")
        self.assertEqual(out, {"hash": "#dev-kit", "page": "dev-kit"})

    def test_retry_loads_the_section_without_a_reload(self):
        """Файл раздела не загрузился → ошибка с «Повторить» (свой заголовок, П-2); «Повторить» грузит снова и собирает
        раздел; файл загрузился, но раздел себя не зарегистрировал — «Повторить» тоже грузит файл заново (П-1)."""
        out = self.run_case("""
FAIL.add("pages/dev-kit.js");
location.hash = "#dev-kit";
setPage("dev-kit", false);
await sleep(30);
const err = $('section[data-page="dev-kit"] div');
const first = { cls: [...err.cls], title: err.title, text: err.text, retry: typeof err.retry, builds: BUILDS };
FAIL.delete("pages/dev-kit.js");
FILES["pages/dev-kit.js"] = () => {};                     // загрузился, но K.page не позвал
err.retry();
await sleep(30);
const silent = { cls: [...$('section[data-page="dev-kit"] div').cls], builds: BUILDS };
FILES["pages/dev-kit.js"] = register;
$('section[data-page="dev-kit"] div').retry();
await sleep(30);
console.log(JSON.stringify({ first, silent, after: view("dev-kit"), builds: BUILDS,
                             loads: LOADED.filter((x) => x === "pages/dev-kit.js").length }));
""")
        self.assertNotIn("THROW", out)
        self.assertEqual(out["first"], {"cls": ["err"], "title": "err.pageLoadT", "text": "err.pageLoad", "retry": "function", "builds": 0})
        self.assertEqual(out["silent"], {"cls": ["err"], "builds": 0})
        self.assertEqual(out["after"], {"hidden": False, "kids": ["dk"]})
        self.assertEqual(out["builds"], 1)
        self.assertEqual(out["loads"], 3)                                             # каждый «Повторить» — новый запрос

    def test_outdated_answer_is_dropped(self):
        """Ушли из раздела до ответа — ответ не собирает ни его, ни текущий раздел; вошли дважды — сборка одна."""
        out = self.run_case("""
DELAY["pages/dev-kit.js"] = 40;
setPage("dev-kit");
setPage("overview");
const ov0 = OV_BUILDS;
await sleep(80);
const left = { page: S.page, builds: BUILDS, ov: OV_BUILDS - ov0, dk: view("dev-kit") };
delete PAGES["dev-kit"]; delete pageLoads["dev-kit"]; delete scripts["pages/dev-kit.js"]; delete scripts["pages/dev-kit-i18n-ru.js"];
setPage("dev-kit");
setPage("overview");
setPage("dev-kit");
await sleep(80);
console.log(JSON.stringify({ left, twice: { builds: BUILDS, dk: view("dev-kit") } }));
""")
        self.assertNotIn("THROW", out)
        self.assertEqual(out["left"]["page"], "overview")
        self.assertEqual(out["left"]["builds"], 0)
        self.assertEqual(out["left"]["ov"], 0)                                        # «Обзор» не пересобран чужим ответом
        self.assertEqual(out["twice"]["builds"], 1)
        self.assertEqual(out["twice"]["dk"], {"hidden": False, "kids": ["dk"]})

    def test_names_of_the_prototype_and_dev_without_mock(self):
        """С-1: setPage/pageSection/page/loadPage с именами прототипа — «Обзор», без падения и без лишней секции;
        З-3: без ?mock=1 #dev-kit — как неизвестный адрес, раздел не грузится и не регистрируется."""
        body = """
const r = Object.create(null);                     // ключ __proto__ — свой
for (const name of ["constructor", "__proto__", "toString", "hasOwnProperty", "valueOf"]) {
  try {
    setPage(name);
    const hash = location.hash;
    location.hash = "#" + name; HANDLERS.hashchange();
    let lp; try { await loadPage(name); lp = "ok"; } catch (e) { lp = e.message; }
    r[name] = [S.page, hash, pageSection(name), page(name, { build() {} }), lp, S.page];
  } catch (e) { r[name] = "THROW " + e.message; }
}
location.hash = "#dev-kit?tab=empty"; HANDLERS.hashchange();
r.dev = [S.page, !!sec("dev-kit"), LOADED.length, page("dev-kit", { build() {} })];
let lp; try { await loadPage("dev-kit"); lp = "ok"; } catch (e) { lp = e.message; }
r.devLoad = lp;
console.log(JSON.stringify(r));
"""
        out = self.run_case(body, mock=False)
        self.assertNotIn("THROW", out)
        for name in ("constructor", "__proto__", "toString", "hasOwnProperty", "valueOf"):
            self.assertEqual(out[name][:4], ["overview", "#overview", None, False], name)
            self.assertEqual(out[name][4:], ["unknown section: " + name, "overview"], name)
        self.assertEqual(out["dev"], ["overview", False, 0, False])
        self.assertEqual(out["devLoad"], "unknown section: dev-kit")

    def test_focus_finds_and_highlights(self):
        """З-1: #settings?focus=<id записи> — элемент [data-find] прокручен в вид и подсвечен (класс), подсветка снята
        по таймеру; элемент, который появился после данных, тоже найден; чужой id — ничего, без падения; меньше
        движения — прокрутка без анимации."""
        out = self.run_case("""
location.hash = "#settings?focus=settings.bot&open=bot";
setPage(pageFromHash(), false);
await sleep(5);                                           // после данных раздела (loadAll)
const bot = $('section[data-page="settings"] [data-find="settings.bot"]');
const now = { on: bot.classList.contains("is-flash"), scrolls: bot.scrolls };
await sleep(200);
const later = bot.classList.contains("is-flash");
SETTINGS_LATE = true; REDUCED = true;
location.hash = "#settings?focus=settings.mcp"; HANDLERS.hashchange();
await sleep(360);                                         // поиск каждые 150 мс: элемент пришёл на 200-й, найден на 300-й
const mcp = $('section[data-page="settings"] [data-find="settings.mcp"]');
const late = { on: mcp.classList.contains("is-flash"), scrolls: mcp.scrolls };
SETTINGS_LATE = false;
const r = { now, later, late };
for (const h of ["#settings?focus=settings.evil", "#settings?focus=constructor", "#settings?focus=%22%5D"]) {
  location.hash = h; HANDLERS.hashchange();
  await sleep(5);
  const b = $('section[data-page="settings"] [data-find="settings.bot"]');
  r[h] = [b.classList.contains("is-flash"), b.scrolls.length];
}
TALL = true; REDUCED = false;
location.hash = "#settings?focus=settings.bot"; HANDLERS.hashchange();
await sleep(5);
r.tall = $('section[data-page="settings"] [data-find="settings.bot"]').scrolls;
console.log(JSON.stringify(r));
""")
        self.assertNotIn("THROW", out)
        # REV-336-2 З-1: прокрутка всегда без анимации — плавная доезжала мимо карточки, пока раздел дособирался
        self.assertEqual(out["now"], {"on": True, "scrolls": [{"block": "center", "behavior": "auto"}]})
        self.assertFalse(out["later"])                                               # подсветка снята по таймеру
        self.assertEqual(out["late"], {"on": True, "scrolls": [{"block": "center", "behavior": "auto"}]})
        for h in ("#settings?focus=settings.evil", "#settings?focus=constructor", "#settings?focus=%22%5D"):
            self.assertEqual(out[h], [False, 0], h)
        self.assertEqual(out["tall"], [{"block": "start", "behavior": "auto"}])     # выше экрана — с начала
        self.assertEqual(out["now"]["scrolls"], [{"block": "center", "behavior": "auto"}])     # одна прокрутка, не две

    def test_focus_once_per_visit(self):
        """REV-s1-f0-2 З-1: подсветили — focus ушёл из адреса (остальное на месте), пересборка раздела (смена «Вида»,
        поворот телефона) подсветку и прокрутку не повторяет."""
        out = self.run_case("""
location.hash = "#settings?focus=settings.bot&open=host";
setPage(pageFromHash(), false);
await sleep(5);
const first = { on: botNode().classList.contains("is-flash"), hash: location.hash };
buildPage();                                              // rebuildPage: «Вид», переход ширины через порог
await sleep(250);
const again = { on: botNode().classList.contains("is-flash"), scrolls: botNode().scrolls.length, hash: location.hash };
location.hash = "#settings?focus=settings.bot"; HANDLERS.hashchange();
await sleep(5);
const plain = location.hash;
buildPage(); await sleep(250);
console.log(JSON.stringify({ first, again, plain, plainAgain: botNode().scrolls.length }));
""")
        self.assertNotIn("THROW", out)
        self.assertEqual(out["first"], {"on": True, "hash": "#settings?open=host"})
        self.assertEqual(out["again"], {"on": False, "scrolls": 0, "hash": "#settings?open=host"})
        self.assertEqual(out["plain"], "#settings")
        self.assertEqual(out["plainAgain"], 0)

    def test_focus_waits_for_the_data_but_not_forever(self):
        """З-2: подсветка — после данных раздела (не раньше: блоки выше ещё не приняли размер), но данные, которые не
        пришли, ждём не дольше FIND_WAIT_MS."""
        out = self.run_case("""
DATA = () => sleep(60);
location.hash = "#settings?focus=settings.bot";
setPage(pageFromHash(), false);
await sleep(20);
const early = [botNode().classList.contains("is-flash"), botNode().scrolls.length];
await sleep(80);
const after = [botNode().classList.contains("is-flash"), botNode().scrolls.length];
DATA = () => new Promise(() => {});                       // ресурс завис
location.hash = "#settings?focus=settings.bot"; HANDLERS.hashchange();
await sleep(40);
const hung = [botNode().classList.contains("is-flash"), botNode().scrolls.length];
await sleep(110);
const capped = [botNode().classList.contains("is-flash"), botNode().scrolls.length];
console.log(JSON.stringify({ early, after, hung, capped }));
""")
        self.assertNotIn("THROW", out)
        self.assertEqual(out["early"], [False, 0])
        self.assertEqual(out["after"], [True, 1])
        self.assertEqual(out["hung"], [False, 0])
        self.assertEqual(out["capped"], [True, 1])

    def test_a_section_that_clears_its_address(self):
        """З-2: настоящие «Настройки» на ?open=bot стирают адрес до #settings во время сборки (settingsFocusUx) — focus
        прочитан до сборки, подсветка есть; прокрутка одна (settingsFocusUx при focus не крутит). Без focus — карточку
        прокручивает и подсвечивает settingsFocusUx (одна подсветка на панель, П-2)."""
        out = self.run_case("""
location.hash = "#settings?focus=settings.bot&open=bot";
setPage(pageFromHash(), false);
await sleep(120);
const found = { on: botNode().classList.contains("is-flash"), scrolls: botNode().scrolls, hash: location.hash };
location.hash = "#settings?open=bot"; HANDLERS.hashchange();
await sleep(100);
const open = { on: botNode().classList.contains("is-flash"), scrolls: botNode().scrolls, hash: location.hash };
console.log(JSON.stringify({ found, open }));
""")
        self.assertNotIn("THROW", out)
        self.assertEqual(out["found"], {"on": True, "scrolls": [{"block": "center", "behavior": "auto"}], "hash": "#settings?tab=bot"})   # UI-5: адрес — часть на экране
        self.assertEqual(out["open"], {"on": True, "scrolls": [{"block": "start", "behavior": "auto"}], "hash": "#settings?tab=bot"})

    def test_left_and_came_back(self):
        """З-2: ушли из раздела, пока ждали данные, и вернулись без focus — прежний переход ничего не подсвечивает."""
        out = self.run_case("""
DATA = () => sleep(60);
location.hash = "#settings?focus=settings.bot";
setPage(pageFromHash(), false);
setPage("overview");
setPage("settings");
await sleep(250);
console.log(JSON.stringify([botNode().classList.contains("is-flash"), botNode().scrolls.length, S.page]));
""")
        self.assertNotIn("THROW", out)
        self.assertEqual(out, [False, 0, "settings"])


class JspartsFindTest(unittest.TestCase):
    def test_find_in_a_list_of_files(self):
        srcs = [("a.js", "  function one() { return 1; }\n"), ("b.js", "  function two() { return '}'; }\n  const N = 2;\n")]
        self.assertEqual(jsparts.find(srcs, "two"), "function two() { return '}'; }")
        self.assertEqual(jsparts.find([s for _n, s in srcs], "one"), "function one() { return 1; }")
        self.assertEqual(jsparts.find(srcs, "N", kind="const"), "const N = 2;")
        with self.assertRaises(KeyError) as cm:
            jsparts.find(srcs, "three")
        self.assertIn("a.js, b.js", str(cm.exception))

    def test_panel_scripts(self):
        names = [n for n, _s in jsparts.panel_scripts(STATIC)]
        self.assertEqual(names[:3], ["kit.js", "templates.js", "app.js"])
        self.assertIn("pages/dev-kit.js", names)
        self.assertFalse([n for n in names if "-i18n-" in n])
        self.assertIn("function navState()", jsparts.find(jsparts.panel_scripts(STATIC), "navState"))


class PagesRouteTest(PanelCase):
    """/pages/<name>.js — a white list: one flat name of a script next to index.html; nothing else under /pages/."""

    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(dir=self.root)
        os.makedirs(os.path.join(self.dir, "pages", "sub"))
        for rel, body in (("index.html", "<!doctype html>"), ("app.js", "app();"), ("pages/x.js", "x();\n" * 80),
                          ("pages/x-i18n-ru.js", "ru();"), ("pages/x.css", "a{}"), ("pages/.h.js", "no"),
                          ("pages/sub/y.js", "no")):
            with open(os.path.join(self.dir, rel), "w", encoding="utf-8") as f:
                f.write(body)
        p = mock.patch.object(server, "STATIC", server.Static(self.dir))
        p.start()
        self.addCleanup(p.stop)

    def test_white_list(self):
        v = server.STATIC.version
        r = self.call("GET", "/pages/x.js?v=" + v, tunnel=False)
        self.assertEqual((r.status, r.headers["content-type"]), (200, "text/javascript; charset=utf-8"))
        self.assertEqual(r.headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertEqual(r.body, ("x();\n" * 80).encode())
        self.assertEqual(self.call("GET", "/pages/x-i18n-ru.js", tunnel=False).status, 200)
        for path in ("/pages/../app.js", "/pages/%2e%2e/app.js", "/pages/sub/y.js", "/pages/.h.js", "/pages/x.css",
                     "/pages/nothing.js", "/pages/", "/pages/x.js/", "/pages//x.js"):
            self.assertEqual(self.call("GET", path, tunnel=False).status, 404, path)


class PagesRealFilesTest(PanelCase):
    def test_the_shipped_section(self):
        for name in ("dev-kit.js", "dev-kit-i18n-ru.js", "dev-kit-i18n-en.js"):
            r = self.call("GET", "/pages/" + name, tunnel=False)
            self.assertEqual(r.status, 200, name)
            self.assertEqual(r.body, read("pages/" + name).encode())
        self.assertEqual(self.call("GET", "/kit.js", tunnel=False).status, 200)
        self.assertEqual(self.call("GET", "/templates.js", tunnel=False).status, 200)


class PagesUnderThePrefixTest(ProxyCase):
    def test_under_the_secret_prefix_and_not_outside(self):
        self.proxy_on(path=PREFIX, domain=DOMAIN)
        r = self.pcall("GET", "/%s/" % PREFIX)
        self.assertIn(b'src="kit.js?v=', r.body)                       # относительный адрес: под префиксом
        for name in ("pages/dev-kit.js", "pages/dev-kit-i18n-en.js", "kit.js", "templates.js", "kit-i18n-ru.js"):
            self.assertEqual(self.pcall("GET", "/%s/%s" % (PREFIX, name)).status, 200, name)
        self.assertEqual(self.pcall("GET", "/pages/dev-kit.js").status, 404)
        self.assertEqual(self.pcall("GET", "/%s/pages/../app.js" % PREFIX).status, 404)


if __name__ == "__main__":
    unittest.main()

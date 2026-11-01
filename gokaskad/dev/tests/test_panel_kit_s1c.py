"""T8 круг 2 (REV-s1-t8-a З-4, З-9, П-5): поведение компонентов Kit и кнопок «сразу» в node — вместо поиска строк.

Kit.rename: имя после записи — подпись сервера; дубль — вопрос «Оставить такое же?» и повтор с allow_duplicate; двойной
Enter — один запрос. Kit.saveBar.save() (вопрос «Сохранить / Не сохранять / Остаться»): ошибка — правки на месте и
false, повтор — true; неверные поля — без запроса. Плитка «Выходов» (app.js exitToggleAct): несохранённые правки группы
выхода — кнопка неактивна с причиной."""
import json
import os
import re
import unittest

from tests import jsparts
from tests.test_panel_kit_s1 import HAS_NODE, STATIC, read, run_node, scope


def kscope():
    # поддельному полю ввода нужен select() — Kit.rename выделяет имя при правке
    # и значение из o.value, как у U.input панели
    src = scope().replace("focus() {} contains()", "focus() {} select() {} contains()")
    old = 'input: (o) => el("input", {})'
    assert old in src
    return src.replace(old, 'input: (o) => { const n = el("input", {}); n.value = o && o.value !== undefined ? String(o.value) : ""; return n; }')


@unittest.skipUnless(HAS_NODE, "no node")
class KitBehaviourTest(unittest.TestCase):
    def test_rename(self):
        out = run_node(kscope() + r"""
let calls = [], rej = null, pend = [];
const rn = Kit.rename({ value: "Старое", aria: "Имя", save: (v, dup) => {
  calls.push([v, !!dup]);
  if (rej) { const e = rej; rej = null; return Promise.reject(e); }
  return new Promise((res) => pend.push(() => res({ label: v + " (сервер)" })));
} });
const pencil = () => rn.el.all().filter((n) => n.tag === "button")[0];
const input = () => rn.el.all().find((n) => n.tag === "input");
const enter = () => input().on.keydown({ key: "Enter", preventDefault() {} });
out.shown0 = texts(rn.el);
pencil().click(); await tick(5);
input().value = "Новое";
enter(); enter();                                                   // двойной Enter — один запрос (П-5)
out.calls1 = calls.length;
pend.shift()(); await tick(5);
out.shown1 = texts(rn.el);
out.editing1 = rn.editing();
// дубль: 409 confirm_duplicate — вопрос на месте, «Оставить» — тот же текст с allow_duplicate
pencil().click(); await tick(5);
input().value = "Вена";
rej = { status: 409, code: 3, data: { need: "confirm_duplicate" } };
enter(); await tick(5);
out.dupQ = texts(rn.el);
find(rn.el, "Оставить").click(); await tick(5);
out.calls2 = calls.slice(-2);
pend.shift()(); await tick(5);
out.shown2 = texts(rn.el);
console.log(JSON.stringify(out));
""")
        self.assertIn("Старое", out["shown0"])
        self.assertEqual(out["calls1"], 1)
        self.assertIn("Новое (сервер)", out["shown1"])                     # подпись из ответа сервера (порча M10)
        self.assertFalse(out["editing1"])
        self.assertIn("Имя «Вена» уже есть. Оставить такое же?", out["dupQ"])   # порча M4
        self.assertEqual(out["calls2"], [["Вена", False], ["Вена", True]])
        self.assertIn("Вена (сервер)", out["shown2"])

    def test_save_bar_save_for_the_leave_question(self):
        out = run_node(kscope() + r"""
let n = 2, saves = 0, fail = null, valid = true;
const sb = Kit.saveBar({ count: () => n, valid: () => valid,
  onSave: () => { saves++; if (fail) { const e = fail; fail = null; return Promise.reject(e); } n = 0; return Promise.resolve({ ok: true }); },
  onCancel() {}, onSaved() {} });
out.dirty0 = sb.dirty();
valid = false; out.invalid = await sb.save(); out.savesInvalid = saves; valid = true;
fail = { status: 500, code: 1, message: "Сервер отказал" };
out.failed = await sb.save(); out.failedText = texts(sb.el); out.dirty1 = sb.dirty();
out.ok = await sb.save(); out.dirty2 = sb.dirty(); out.saves = saves;
out.empty = await sb.save(); out.savesEmpty = saves;
// 409 и перекладка списком — false, правки (список «Применить снова») остаются несохранёнными
n = 1;
const sb2 = Kit.saveBar({ count: () => n, onSave: () => Promise.reject({ status: 409, code: "policy_changed", data: {} }),
  onCancel() {}, onSaved() {}, onConflict: () => Promise.resolve({ mode: "list", listText: "Группу «Основная» изменили, пока вы правили" }), list: () => [] });
out.list = await sb2.save(); n = 0; out.listDirty = sb2.dirty();
// слияние t8b+t8c: форма без прогона, 409 и чужая правка других ключей — «rebased» (t8b); первый save() — false
// (пользователь видит «нажмите «Сохранить» ещё раз»), второй — сохраняет, как активная кнопка «Сохранить»
let m = 1, conflict = true, saves3 = 0;
const sb3 = Kit.saveBar({ count: () => m, onSave: () => { saves3++; if (conflict) { conflict = false; return Promise.reject({ status: 409, code: "object_changed", data: {} }); } m = 0; return Promise.resolve({ ok: true }); },
  onCancel() {}, onSaved() {}, onConflict: () => Promise.resolve({ mode: "silent" }) });
out.rebased1 = await sb3.save(); out.rebasedText = texts(sb3.el);
out.rebased2 = await sb3.save(); out.rebasedSaves = saves3; out.rebasedDirty = sb3.dirty();
console.log(JSON.stringify(out));
""")
        self.assertTrue(out["dirty0"])
        self.assertEqual((out["invalid"], out["savesInvalid"]), (False, 0))   # неверные поля — без запроса
        self.assertFalse(out["failed"])
        self.assertIn("Сервер отказал", out["failedText"])
        self.assertTrue(out["dirty1"])
        self.assertEqual((out["ok"], out["dirty2"], out["saves"]), (True, False, 2))
        self.assertEqual((out["empty"], out["savesEmpty"]), (True, 2))      # нечего сохранять — уходить можно
        self.assertEqual((out["list"], out["listDirty"]), (False, True))      # «Применить снова» — тоже несохранённое
        self.assertFalse(out["rebased1"])
        self.assertTrue(any("нажмите «Сохранить» ещё раз" in x for x in out["rebasedText"]))
        self.assertEqual((out["rebased2"], out["rebasedSaves"], out["rebasedDirty"]), (True, 2, False))

    def test_tile_buttons_off_while_the_group_has_edits(self):
        """«Выключить» на плитке выхода (app.js exitToggleAct): у группы выхода правки — неактивна с причиной; у
        выхода другой группы — активна (порча M3)."""
        js = read("app.js")
        out = run_node(kscope() + "\nconst PARTS = {};\n" + jsparts.const(js, "groupDirty") + "\n" + jsparts.function(js, "exitToggleCA") + "\n"
                       + jsparts.const(js, "exitToggleAct") + r"""
const API = { exits: { enabled: () => Promise.resolve({}) } };
const exitsRev = () => 1, exitImpact = () => () => [], exitActsDone = () => () => {};
PARTS["exits-groups"] = { dirtyGroup: (gid) => gid === "main" };
const btnOf = (root) => root.all().filter((n) => n.tag === "button")[0];
const a = btnOf(exitToggleAct({ id: "e-1", name: "Вена", enabled: true, group: "main" }));
const b = btnOf(exitToggleAct({ id: "e-2", name: "Хельсинки", enabled: true, group: "g-5f1a2b" }));
out.main = [a.disabled, a.title]; out.other = [b.disabled, b.title];
console.log(JSON.stringify(out));
""")
        self.assertEqual(out["main"], [True, "Сначала сохраните или отмените правки"])
        self.assertEqual(out["other"], [False, ""])


@unittest.skipUnless(HAS_NODE, "no node")
class SpeedDirtyTest(unittest.TestCase):
    def test_bad_field_and_list_count_as_unsaved(self):
        """Слияние t8b+t8c: «Тест скорости — общее» с красным полем (черновик не менялся) и со списком «Применить снова»
        после 409 — несохранённое для вопроса при уходе; иначе уход молча теряет правку."""
        src = read("pages/exits-groups.js")
        code = "\n".join([jsparts.const(src, "same"), "let speedForm;", jsparts.const(src, "speedDirty")]) + r"""
const base = { max_mb: 10 };
const out = {};
speedForm = { base: null, draft: null, bad: {}, list: [] }; out.none = speedDirty();
speedForm = { base, draft: { max_mb: 10 }, bad: {}, list: [] }; out.clean = speedDirty();
speedForm = { base, draft: { max_mb: 20 }, bad: {}, list: [] }; out.edit = speedDirty();
speedForm = { base, draft: { max_mb: 10 }, bad: { max_mb: true }, list: [] }; out.bad = speedDirty();
speedForm = { base, draft: { max_mb: 10 }, bad: {}, list: [{ key: "max_mb", mine: 20 }] }; out.list = speedDirty();
console.log(JSON.stringify(out));
"""
        out = run_node(code)
        self.assertEqual(out, {"none": False, "clean": False, "edit": True, "bad": True, "list": True})


class GroupWordsTest(unittest.TestCase):
    def test_no_core_jargon_in_the_groups_texts(self):
        """REV-s1-t8-2 П-2: тексты вкладки «Группы» — без слова «ядро» / «core» (человеку не нужно знать, кто проверяет)."""
        import re
        for lang, word in (("ru", r"ядр[оауе]"), ("en", r"\bcore\b")):
            src = read("pages/exits-groups-i18n-%s.js" % lang)
            vals = re.findall(r'^\s*"ep\.[^"]+":\s*"((?:[^"\\]|\\.)*)"', src, re.M)
            self.assertGreater(len(vals), 100, lang)
            bad = [v for v in vals if re.search(word, v, re.I)]
            self.assertEqual(bad, [], lang)



@unittest.skipUnless(HAS_NODE, "no node")
class NowLineTest(unittest.TestCase):
    def test_all_exits_down_since_shows_the_date_when_not_today(self):
        """REV-s1-t8m П-3: «сейчас: все выходы недоступны с …» — время через K.fmtTs (сегодня — ЧЧ:ММ, раньше — с датой)."""
        src = read("pages/exits-groups.js")
        code = "\n".join([jsparts.function(src, "nowLine")]) + r"""
const t = (k, a) => k + (a && a.t !== undefined ? "|" + a.t : "");
const U = { chip: (text, tone) => [text, tone] };
const K = { fmtTs: (ts) => "TS" + ts };
const ROWS = { a: { enabled: true, pstate: "dead", since: 1700000000 }, b: { enabled: true, pstate: "dead", since: 1700000500 } };
const rowOf = (id) => ROWS[id] || null;
const tierLabel = (tr, i) => String(i + 1);
console.log(JSON.stringify({ block: nowLine({ fail: "block", tiers: [{ exits: ["a", "b"] }] }), direct: nowLine({ fail: "direct", tiers: [{ exits: ["a"] }] }) }));
"""
        out = run_node(code)
        self.assertEqual(out["block"], ["ep.nowBlockSince|TS1700000500", "danger"])
        self.assertEqual(out["direct"], ["ep.nowDirectSince|TS1700000000", "danger"])



@unittest.skipUnless(HAS_NODE, "no node")
class DnsLineTest(unittest.TestCase):
    def test_the_honest_dns_line_of_r_g9(self):
        """Р-Г9 (SPEC-stage1-exit-groups §5): «Основная» лежит и её последний шаг — блок — DNS узла молчит, российские
        сайты по имени не открываются ни у кого. Строка у «Основной» при «Блок» (по черновику) и у российской группы
        (по сохранённой «Основной»); у зарубежной группы и при «Напрямую» — нет. Тексты RU и EN есть."""
        src = read("pages/exits-groups.js")
        code = jsparts.function(src, "dnsLine") + r"""
const t = (k, a) => k + (a && a.g ? "|" + a.g : "");
let MAIN = { id: "main", fail: "block" };
const groupOf = (gid) => (gid === "main" ? MAIN : null);
const groupLabel = (gid) => (gid === "main" ? "Основная" : gid);
const out = {};
out.mainBlock = dnsLine("main", { id: "main", fail: "block" });
out.mainDirect = dnsLine("main", { id: "main", fail: "direct" });
out.ru = dnsLine("g-ru0001", { id: "g-ru0001", role: "ru", fail: "block" });
out.abroad = dnsLine("g-5f1a2b", { id: "g-5f1a2b", role: "abroad", fail: "block" });
MAIN = { id: "main", fail: "direct" }; out.ruMainDirect = dnsLine("g-ru0001", { id: "g-ru0001", role: "ru", fail: "block" });
console.log(JSON.stringify(out));
"""
        out = run_node(code)
        self.assertEqual(out, {"mainBlock": "ep.dnsMainBlock", "mainDirect": "", "ru": "ep.dnsRuGroup|Основная",
                               "abroad": "", "ruMainDirect": ""})
        ru, en = read("pages/exits-groups-i18n-ru.js"), read("pages/exits-groups-i18n-en.js")
        self.assertIn("даже у пользователей с «Россия напрямую»", ru)
        self.assertIn("even for users with “Russian sites direct”", en)
        # REV-s1-t8m-3 З-1: gw-all берёт выход следующей зарубежной группы (assign.gw_all) — строка говорит о них; у
        # xray-устройств российской группы с прокси-выходом имя разрешает роутер (§4.4) — оговорка в обеих строках
        lines = {lang: dict((k, json.loads('"%s"' % v)) for k, v in re.findall(r'^  "(ep\.dns\w+)": "((?:[^"\\]|\\.)*)"', src, re.M))
                 for lang, src in (("ru", ru), ("en", en))}
        for key in ("ep.dnsMainBlock", "ep.dnsRuGroup"):
            self.assertIn("и выходы других зарубежных групп", lines["ru"][key])
            self.assertIn("the exits of the other foreign groups", lines["en"][key])
            self.assertIn("им адреса узнаёт роутер с выходом группы", lines["ru"][key])
            self.assertIn("the router with the group’s exit looks up addresses", lines["en"][key])
            self.assertNotIn("ни у кого", lines["ru"][key])
            self.assertNotIn("for anyone", lines["en"][key])
        for key in ("ep.dnsMainBlock", "ep.dnsRuGroup"):
            self.assertIn('"%s"' % key, ru)
            self.assertIn('"%s"' % key, en)


@unittest.skipUnless(HAS_NODE, "no node")
class PerConnectionOnlyMainTest(unittest.TestCase):
    def test_the_switch_is_in_the_main_group_only(self):
        """Итоговая строгая 3.3.7 (README-сверка, п. 4): «По соединениям» ядро принимает только у «Основной»
        (policy.validate: ep.err.per_connection_main) — у другой группы переключателя нет, иначе сохранение — ошибка."""
        src = read("pages/exits-groups.js")
        code = "\n".join([
            "const t = (k) => k;",
            "const el = (tag, a, ...kids) => ({ tag, a, kids: kids.filter((k) => k !== null && k !== undefined), addEventListener() {} });",
            "const swLive = (title) => ({ sw: title });",
            "const numIn = () => ({ num: true });",
            "const U = { input: () => ({ addEventListener() {} }) };",
            "const touchPreset = () => {}; const changed = () => {};",
            "const scnBlock = () => null; const K = { fmtInt: String, icon: () => null };",   # сценарии (Т2 №30) — проверка в Chrome, test_it1_t2_panel
            # «Ещё» (пачка А п. 3): редкие переключатели — в том же блоке, под раскрытием; «Куда встают новые» — не переключатель
            "const RARE_OPEN = {}; const rareOn = () => true; const newTierRow = () => null; const CSS = { escape: String };",
            jsparts.const(src, "sub"),
            "const mk = (id) => ({ id, return_to_primary: { enabled: true, hold_min: 5 }, night_rebalance: { enabled: false, at: '04:00' }, "
            "evict_degraded: true, drop_on_degrade: false, per_connection: false, flap_standby: true });",
            "const forms = { main: { draft: mk('main'), base: mk('main') }, 'g-5f1a2b': { draft: mk('g-5f1a2b'), base: mk('g-5f1a2b') } };",
            jsparts.function(src, "behaviourBlock"),
            "const sws = (n) => JSON.stringify(n).match(/\"sw\":\"[^\"]+\"/g).map((x) => x.slice(6, -1));",
            "console.log(JSON.stringify({ main: sws(behaviourBlock('main')), other: sws(behaviourBlock('g-5f1a2b')) }));"])
        out = run_node(code)
        self.assertIn("ep.perConn", out["main"])
        self.assertNotIn("ep.perConn", out["other"])
        self.assertEqual([x for x in out["main"] if x != "ep.perConn"], out["other"])


if __name__ == "__main__":
    unittest.main()

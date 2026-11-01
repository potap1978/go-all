"""Круг 1 правок по видео владельца (27.10), пакет Л1, №29 — группы выходов (pages/exits-groups.js в node, настоящие
функции через tests/jsparts.py, окна и кнопки — заглушки).

* а: в «⋯» выхода группы — «Убрать из группы»: из зарубежной — перенос в «Основную» (сухой прогон, как перенос);
  из «Основной» и российской — окно со словами и путём к переносу или карточке ключа (без запроса к серверу).
* б: несохранённые правки группы не выключают «Удалить группу»: окно предупреждает, что они отменятся.
* в: последняя российская группа с ключами (409 ru_last) — не «ошибка с Повторить», а что сделать и кнопки к ключам."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from tests import jsparts

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


STUB = r"""
const LOG = [];
const t = (k, v) => k + (v ? JSON.stringify(v) : "");
const el = (tag, a) => ({ tag, a, kids: [], replaceChildren(...k) { this.kids = k; }, append(...k) { this.kids.push(...k); } });
const U = {
  btn: (label, kind, fn, o) => ({ btn: label, kind, fn, o: o || {}, dataset: {} }),
  p: (x) => ({ p: x }),
  al: (tone, title, text, o) => ({ al: tone, title, text, o: o || {} }),
  danger: (title, text, kids) => ({ danger: title, kids }),
  field: () => ({ field: true }), select: () => ({ select: true }),
  menu: (items, o) => ({ menu: items, o: o || {}, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } }),
};
const renameGroup = (g) => LOG.push("rename:" + g);
let DIALOG = null;
const K = { API: { groups: {}, exits: {} }, tn: (k, n) => k + "#" + n, toast: () => {}, icon: (n) => ({ icon: n }),
  openDialog: (o) => { DIALOG = { o, foot: null, closed: false, body: { append: () => {} } };
    DIALOG.setFoot = (b) => { DIALOG.foot = b; }; DIALOG.close = () => { DIALOG.closed = true; }; return DIALOG; } };
let CA = null;
const Kit = { confirmAction: (o) => { CA = o; return { el: { ca: true }, refresh: () => {} }; }, errKind: () => "", impactLines: () => [] };
const GROUPS = [{ id: "main", role: "abroad" }, { id: "g2", role: "abroad" }, { id: "home", role: "ru" }];
const pol = () => ({ policy: { groups: GROUPS } });
const groupOf = (id) => GROUPS.find((g) => g.id === id) || null;
const groupLabel = (id) => "«" + id + "»";
const exitName = (id) => "N:" + id;
const rowOf = (id) => ({ id });
let DIRTY = {};
const dirtyOf = (g) => !!DIRTY[g];
const unsavedWhy = (...gids) => (gids.some((g) => g && dirtyOf(g)) ? "kit.unsavedFirst" : "");
const exUnsaved = () => "";
const forms = {};
const readRev = () => 1, reread = () => {}, impact = () => () => [], track = (x) => x, actionsLive = new Set();
const openExit = (id) => LOG.push("open:" + id), moveDialog = (id) => LOG.push("move:" + id);
const isNeed = (e) => !!e && e.status === 409 && e.code === "need";
const tick = () => new Promise((r) => setTimeout(r, 0));
"""


def run(parts, script):
    src = STUB + "\n" + "\n".join(parts) + "\n(async () => {\n%s\n})().catch((e) => { console.log(JSON.stringify(" \
        "{crash: String(e && e.stack || e)})); });\n" % script
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.unlink(path)
    out = res.stdout.decode("utf-8").strip().splitlines()
    if res.returncode != 0 or not out:
        raise AssertionError("node: %s" % res.stderr.decode("utf-8", "replace")[-2000:])
    doc = json.loads(out[-1])
    if "crash" in doc:
        raise AssertionError(doc["crash"])
    return doc


@unittest.skipUnless(shutil.which("node"), "нет node")
class GroupsTest(unittest.TestCase):
    def scope(self):
        js = read("pages/exits-groups.js")
        return [jsparts.const(js, "isRuLast"), jsparts.function(js, "mergeNeed"), jsparts.function(js, "delParams"),
                jsparts.function(js, "groupMenu"), jsparts.function(js, "delGroupDialog"), jsparts.function(js, "removeDialog")]

    def test_remove_from_a_foreign_group_moves_to_main(self):
        out = run(self.scope(), r"""
const calls = [];
K.API.exits.move = async (id, body, dry) => { calls.push([id, body, !!dry]); return { ok: true }; };
removeDialog("e1", "g2");
await CA.dry(); await CA.run(7);
DIRTY = { main: true };
console.log(JSON.stringify({ title: DIALOG.o.title, text: DIALOG.o.blocks[0].p, label: CA.label, dis: CA.dis(), calls }));
""")
        self.assertEqual(out["title"], 'ep.removeTitle{"name":"N:e1"}')
        self.assertEqual(out["text"], 'ep.removeText{"g":"«g2»","main":"«main»"}')
        self.assertEqual(out["label"], "ep.removeBtn")
        self.assertEqual(out["calls"], [["e1", {"group": "main"}, True], ["e1", {"group": "main", "rev": 7}, False]])
        self.assertEqual(out["dis"], "kit.unsavedFirst")                  # правки в «Основной» — сначала сохранить

    def test_remove_from_main_or_a_russian_group_explains(self):
        out = run(self.scope(), r"""
K.API.exits.move = async () => { throw new Error("no call"); };
const r = {};
for (const [id, gid] of [["e1", "main"], ["r1", "home"]]) {
  CA = null; removeDialog(id, gid);
  r[gid] = { text: DIALOG.o.blocks[0].p, foot: DIALOG.foot.map((b) => b.btn), ca: CA };
}
DIALOG.foot[2].fn();
r.log = LOG;
console.log(JSON.stringify(r));
""")
        self.assertEqual(out["main"]["text"], 'ep.removeMain{"g":"«main»"}')
        self.assertEqual(out["home"]["text"], 'ep.removeRu{"g":"«main»"}')
        self.assertEqual(out["home"]["foot"], ["c.cancel", "ep.details", "ep.moveTo"])
        self.assertIsNone(out["home"]["ca"])                                # запроса к серверу нет
        self.assertEqual(out["log"], ["move:r1"])

    def test_menu_has_the_item(self):
        js = read("pages/exits-groups.js")
        self.assertIn('{ label: t("ep.removeFrom"), on: () => removeDialog(id, gid) }', js)          # выход группы
        self.assertIn('on: () => removeDialog(r.id, r.group) }', js)                                  # российский ключ
        for lang in ("ru", "en"):
            words = read("pages/exits-groups-i18n-%s.js" % lang)
            for k in ("ep.removeFrom", "ep.removeTitle", "ep.removeText", "ep.removeBtn", "ep.removeBtnLow", "ep.removeMain",
                      "ep.removeRu", "ep.delDirty", "ep.delRuLastT", "ep.delRuKey", "ep.delRuDropX", "ep.delGroupKeys", "ep.delRuSubs",
                      "ep.delGroupMenu", "ep.delGroupTextRu"):
                self.assertIn('"%s": "' % k, words, (lang, k))

    def test_unsaved_edits_do_not_block_the_delete(self):
        out = run(self.scope(), r"""
K.API.groups.del = async () => ({ ok: true });
DIRTY = { g2: true };
// №29 (Т2): «Удалить группу…» — пункт «⋯» шапки карточки; у «Основной» его нет
const m = groupMenu("g2"), mm = groupMenu("main");
m.menu[1].on();
await tick();
console.log(JSON.stringify({ items: m.menu.map((x) => x.label), main: mm.menu.map((x) => x.label), danger: !!m.menu[1].danger, warn: DIALOG.o.blocks[1], dis: CA.dis() }));
""")
        self.assertEqual(out["items"], ["kit.rename", "ep.delGroupMenu"])
        self.assertEqual(out["main"], ["kit.rename"])
        self.assertTrue(out["danger"])
        self.assertEqual(out["warn"]["title"], 'ep.delDirty{"g":"«g2»"}')
        self.assertEqual(out["dis"], "")                                    # своя правка не держит удаление
        self.assertNotIn("egDel", jsparts.function(read("pages/exits-groups.js"), "refreshButtons"))

    def test_the_last_russian_group(self):
        """Решение владельца 8а (Т2): последняя российская группа с ключами — удалить вместе с ключами одним действием:
        список ключей (каждый можно открыть), что будет словами, кнопка «Удалить группу и ключи»; запрос — exits=drop и
        reassign=direct (российские сайты выбравших — с сервера напрямую)."""
        out = run(self.scope(), r"""
const calls = [];
K.API.groups.del = async (gid, q) => { calls.push(Object.assign({}, q));
  if (!q.exits) { const e = new Error("x"); e.status = 409; e.code = "ru_last"; e.data = { exits: ["r1", "r2"] }; throw e; }
  if (!q.reassign) { const e = new Error("x"); e.status = 409; e.code = "need"; e.data = { refs: { users: ["u1"], devices: [] } }; throw e; }
  return { ok: true, moves: [] }; };
delGroupDialog("home");
for (let i = 0; i < 6; i++) await tick();
const fields = DIALOG.o.blocks[DIALOG.o.blocks.length - 1];
const al = fields.kids[0], keys = fields.kids[1];
keys.kids = keys.kids || [];
const dis = CA.dis(), label = CA.label, lead = DIALOG.o.blocks[0].p;
await CA.run(7);
console.log(JSON.stringify({ tone: al.al, title: al.title, text: al.text, dis, label, calls, lead }));
""")
        self.assertEqual(out["tone"], "warning")
        self.assertEqual(out["title"], 'ep.delRuLastT{"g":"«home»"}')
        self.assertEqual(out["text"], "ep.delRuDropX")
        self.assertEqual(out["dis"], "")                                     # можно: без правки было «нельзя»
        self.assertEqual(out["label"], "ep.delGroupKeys")
        self.assertEqual(out["calls"][0], {"dry": 1, "ask": 1})                # пробы — с ask (пачка А: без 409)
        self.assertEqual(out["calls"][1], {"dry": 1, "exits": "drop", "ask": 1})
        self.assertEqual(out["calls"][-1], {"exits": "drop", "reassign": "direct", "rev": 7})

    def test_the_probe_answers_without_409(self):
        """Пачка А (владелец 28.10): пробное удаление не пишет 409 в консоль — окно спрашивает с ask=1, сервер отвечает
        200 {ask: {code, data}}; окно то же, что по 409 (список ключей, «кого куда»). До правки окно слало пробу без ask и
        получало 409."""
        out = run(self.scope(), r"""
const calls = [], errors = [];
K.API.groups.del = async (gid, q) => { calls.push(Object.assign({}, q));
  const need = !q.exits ? { code: "ru_last", data: { exits: ["r1", "r2"] } } : !q.reassign ? { code: "need", data: { refs: { users: ["u1"], devices: [] } } } : null;
  if (need && q.dry && q.ask) return { ok: true, ask: need };
  if (need) { errors.push(q); const e = new Error("x"); e.status = 409; e.code = need.code; e.data = need.data; throw e; }
  return { ok: true, moves: [] }; };
delGroupDialog("home");
for (let i = 0; i < 6; i++) await tick();
const fields = DIALOG.o.blocks[DIALOG.o.blocks.length - 1];
const dis = CA.dis(), label = CA.label;
await CA.dry(); await CA.run(7);
console.log(JSON.stringify({ tone: fields.kids[0].al, dis, label, calls, errors }));
""")
        self.assertEqual(out["errors"], [])
        self.assertEqual((out["tone"], out["dis"], out["label"]), ("warning", "", "ep.delGroupKeys"))
        self.assertEqual(out["calls"][-1], {"exits": "drop", "reassign": "direct", "rev": 7})

    def test_the_last_russian_group_with_a_subscription(self):
        out = run(self.scope(), r"""
K.API.groups.del = async (gid, q) => { if (!q.exits) { const e = new Error("x"); e.status = 409; e.code = "ru_last"; e.data = { exits: ["r1"] }; throw e; }
  const e = new Error("x"); e.status = 409; e.code = "need"; e.data = { uses: ["subs"] }; throw e; };
delGroupDialog("home");
for (let i = 0; i < 6; i++) await tick();
console.log(JSON.stringify({ dis: CA.dis() }));
""")
        self.assertEqual(out["dis"], "ep.delRuSubs")                        # подписка вернула бы ключи — сначала её

    def test_russian_group_own_text(self):
        """У российской группы — свой текст окна: «встанут последними шагами …», не «на последний уровень»."""
        out = run(self.scope(), r"""
GROUPS.push({ id: "g9", role: "ru" });
K.API.groups.del = async () => { const e = new Error("x"); e.status = 409; e.code = "need"; e.data = { exits: ["r1"] }; throw e; };
delGroupDialog("home");
console.log(JSON.stringify({ lead: DIALOG.o.blocks[0].p }));
""")
        self.assertEqual(out["lead"], 'ep.delGroupTextRu{"g":"«home»"}')


if __name__ == "__main__":
    unittest.main()

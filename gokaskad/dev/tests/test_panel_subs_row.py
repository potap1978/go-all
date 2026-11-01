"""Kaskad 3.3.0, the panel, «Выходы» → «Подписки»: the row of actions of a tile never sticks out of it.

At 390 px a subscription tile with «Обновить» + «Вернуть исключённые (1)» + the trash was wider than the tile (335 px
of buttons in 284 px; at 1440 — 315 in 312): the row of a tile with icons (kk-exit, gx-tc__btns--row) does not wrap,
and its buttons do not shrink. Now the buttons of such a row are a group of their own (gx-tc__acts) that takes the free
width and wraps inside: what does not fit goes to the next line of the group, the icons (pencil, trash) stay at the
right on the first line — where the other tiles have them. A row without icons is as it was.

The real U.cards of app.js (cut out by tests/jsparts.py) in node with a fake DOM; the rules of components.css and
styles.css as text. Node-less: the node part is skipped."""

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


def node(src):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.unlink(path)
    if r.returncode:
        raise AssertionError(r.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])


def method(src, name):
    """``name(…) {…}`` — a method of the object U (``    cards(items) {``)."""
    m = re.search(r"(?m)^[ \t]*%s\s*(?=\()" % re.escape(name), src)
    if m is None:
        raise KeyError("method %s" % name)
    body = src.index("{", jsparts.close_of(src, m.end()))
    return src[m.start():jsparts.close_of(src, body) + 1].strip()


def rule(css, selector):
    """The declarations of the rule ``selector { … }`` (the selector alone before the brace)."""
    m = re.search(r"(?m)^%s \{([^}]*)\}" % re.escape(selector), css)
    if m is None:
        raise KeyError(selector)
    return dict((k.strip(), v.strip()) for k, v in (d.split(":", 1) for d in m.group(1).split(";") if d.strip()))


@unittest.skipUnless(shutil.which("node"), "нет node")
class CardRowTest(unittest.TestCase):
    def rows(self, items):
        """The rows of actions U.cards builds for ``items`` (JS source of the array): a button — its text (an icon —
        its class), the group — {class: [its buttons]}."""
        src = """
const flat = (a) => a.reduce((o, x) => o.concat(Array.isArray(x) ? flat(x) : (x === null || x === undefined || x === false ? [] : [x])), []);
const node = (tag, cls, text, kids) => ({ tag, cls, text, kids: flat(kids), append(...xs) { this.kids.push(...flat(xs)); } });
const el = (tag, a, ...kids) => node(tag, (a && a.class) || "", (a && a.text) || "", kids);
const txt = (s) => node("#text", "", s, []);
const icon = (name) => node("svg", "gi", name, []);
const TONE_ICON = {};
const btn = (label, cls) => node("button", "ga-btn" + (cls ? " " + cls : ""), label, []);
const U = { trash: () => btn("", "gx-trash"), askDelete: () => null,
%s
};
const shape = (n) => (n.tag === "button" ? n.text || n.cls.split(" ").pop() : { [n.cls]: n.kids.map(shape) });
const rows = U.cards(%s).kids.map((card) => card.kids.find((k) => k.cls.split(" ").indexOf("gx-tc__btns") >= 0))
  .map((r) => (r ? { cls: r.cls, kids: r.kids.map(shape) } : null));
console.log(JSON.stringify(rows));
""" % (method(read("app.js"), "cards"), items)
        return node(src)

    def test_subscription_buttons_are_a_group_and_the_trash_stays_last(self):
        rows = self.rows("""[
  { cls: "kk-exit", title: "sub.example.net", btns: [btn("Обновить"), btn("Вернуть исключённые (1)")], trash: btn("", "gx-trash") },
  { cls: "kk-exit", title: "подписка 2", btns: [btn("Обновить"), null], trash: btn("", "gx-trash") }]""")
        row = {"cls": "gx-tc__btns gx-tc__btns--row"}
        self.assertEqual(rows, [dict(row, kids=[{"gx-tc__acts": ["Обновить", "Вернуть исключённые (1)"]}, "gx-trash"]),
                                dict(row, kids=[{"gx-tc__acts": ["Обновить"]}, "gx-trash"])])

    def test_exit_tiles_keep_pencil_and_trash_at_the_right(self):
        rows = self.rows("""[
  { cls: "kk-exit", title: "Вена", btns: [btn("Выключить")], icons: [btn("", "pencil")], del: { name: "Вена", run: () => null } },
  { cls: "kk-exit", title: "Вена (повтор)", btns: [], icons: [], del: { name: "Вена", run: () => null } }]""")
        self.assertEqual([r["kids"] for r in rows], [[{"gx-tc__acts": ["Выключить"]}, "pencil", "gx-trash"],
                                                     [{"gx-tc__acts": []}, "gx-trash"]])

    def test_a_row_without_icons_is_as_it_was(self):
        rows = self.rows('[{ title: "x", btns: [btn("А"), btn("Б")] }, { title: "y" }]')
        self.assertEqual(rows, [{"cls": "gx-tc__btns", "kids": ["А", "Б"]}, None])


class SubsCardTest(unittest.TestCase):
    def test_the_subscription_tile_goes_through_the_card_row(self):
        js = read("app.js")
        body = jsparts.function(js, "exitSubsCard")
        self.assertIn('cls: "kk-exit", title: x.name', body)
        self.assertIn('btns: [U.btn(t("xs.refresh"), "tertiary", () => refreshSub(x)),', body)
        self.assertIn('x.excluded ? U.btn(t("xs.restore", { n: x.excluded }), "tertiary"', body)
        self.assertIn('trash: U.trash(x.name, () => deleteSub(x), { tip: t("xs.delTip") }),', body)
        self.assertIn('ics.length ? el("span", { class: "gx-tc__acts" }, btns) : btns, ics));', js)


class RowCssTest(unittest.TestCase):
    def test_the_group_wraps_inside_the_free_width(self):
        css = read("components.css")
        acts = rule(css, ".gx-tc__acts")
        self.assertEqual((acts["display"], acts["flex-wrap"], acts["flex"], acts["min-width"]), ("flex", "wrap", "1 1 auto", "0"))
        self.assertEqual(acts["gap"], "inherit")                      # the gap of the row (kk-exit: var(--sp-2))
        self.assertEqual(rule(css, ".gx-trash"), {"flex": "none"})    # the trash never shrinks
        self.assertEqual(rule(css, ".ga-btn")["max-width"], "100%")   # one button wider than the group: capped by it
        # the row itself stays one line (the group and the icons); the icons — on the group's first line
        self.assertEqual(rule(css, ".gx-tc__btns--row")["align-items"], "flex-start")
        self.assertEqual(rule(read("styles.css"), ".kk-exit .gx-tc__btns")["flex-wrap"], "nowrap")

    def test_nothing_makes_the_group_one_line(self):
        for name in ("components.css", "styles.css", "tokens.css"):
            for sel, body in re.findall(r"([^{}]*)\{([^}]*)\}", read(name)):
                if "gx-tc__acts" in sel:
                    self.assertNotIn("nowrap", body, "%s: %s" % (name, sel.strip()))

    def test_the_confirmation_in_place_stays_centered(self):
        css = read("components.css")
        # .is-confirm (align-items: center) comes after the row's flex-start: «Удалить «…»? Отмена · Удалить» on one line
        self.assertEqual(rule(css, ".is-confirm")["align-items"], "center")
        self.assertLess(css.index("\n.gx-tc__btns--row { align-items: flex-start; }"), css.index("\n.is-confirm {"))


if __name__ == "__main__":
    unittest.main()

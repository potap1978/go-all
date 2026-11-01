"""3.3.7.14: «Exits › All exits» — the row over the table at 1000 with «Larger» text (Claude Design r1 «to the right»,
frames Е2/Е3/Е5, k3/stand/logs/D-tour.md).

An empty spacer ``gx-toolbar__sp`` stood between two 10 px gaps of the flex row: when «+ Add key» wrapped, the spacer
stayed and took 10 px (``phantom``), and «+» went to the start of the second line. Now the view switch and «Add key»
are a right group ``kk-ex__end``: «+» that does not fit goes under the view switch, at the right edge; 1440 as before.
The geometry is checked in a real Chrome by ``tools/k3_layout_check.mjs`` (``exend`` and ``phantom``;
``K3_FONT=l K3_PAGES=exits node tools/k3_layout_check.mjs 1000,1440``); these are the offline guards of the rules
that make it hold, and of the checker's own rule, so that it is not dropped silently."""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def rule(css, selector):
    """The declarations of all top-level rules with exactly this selector, the later ones winning (as in the cascade)."""
    out = None
    for m in re.finditer(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css):
        out = dict(out or {}, **dict((k.strip(), v.strip()) for k, v in (d.split(":", 1) for d in m.group(1).split(";") if ":" in d)))
    return out


class ExitsBarTest(unittest.TestCase):
    def test_the_view_switch_and_add_are_a_right_group_not_behind_a_spacer(self):
        js = read(STATIC, "app.js")
        self.assertIn('const bar = el("div", { class: "gx-toolbar kk-ex__bar" }, seg, info, el("div", { class: "kk-ex__end" }, dn, add));', js)
        line = next(x for x in js.splitlines() if 'class: "gx-toolbar kk-ex__bar"' in x)
        self.assertNotIn("gx-toolbar__sp", line)
        # the spacer stays for the other rows («Users», «Forwards» — the owner's decision: only «Exits»)
        self.assertEqual(js.count('el("span", { class: "gx-toolbar__sp" })'), 3)

    def test_the_group_grows_and_presses_the_buttons_right(self):
        css = read(STATIC, "styles.css")
        self.assertEqual(rule(css, ".kk-ex__end"), {"flex": "1 1 0", "min-width": "min-content", "display": "flex", "flex-wrap": "wrap",
                                                    "justify-content": "flex-end", "align-items": "center", "gap": "8px 10px"})
        # phone: the row is a column; the nested button is not «.gx-toolbar > .ga-btn» any more — its own full-width rule
        self.assertEqual(rule(css, '[data-device="phone"] .kk-ex__end'), {"display": "grid", "grid-template-columns": "minmax(0, 1fr)"})
        self.assertEqual(rule(css, '[data-device="phone"] .kk-ex__end > .ga-btn'), {"width": "100%", "justify-content": "center"})
        # up to 1280 the view switch and «+» are icons — the rule reaches «+» inside the group too
        self.assertIn(".kk-ex__bar .kk-ex__add { min-width: 0; padding: 0 12px; gap: 0; }", css)

    def test_the_checker_keeps_the_rule(self):
        tool = read(ROOT, "tools", "k3_layout_check.mjs")
        self.assertIn("if (de.dataset.device !== 'phone') for (const bar of document.querySelectorAll('.kk-ex__bar')) {", tool)
        self.assertIn("if (a && Math.abs(a.right - b.right) > 1.5) out.push(['exend', name(add),", tool)
        self.assertIn("if (Math.abs(a.left - d.right - 10) > 1.5) out.push(['exend', name(dn),", tool)
        self.assertIn("else if (d && Math.abs(d.right - b.right) > 1.5) out.push(['exend', name(dn),", tool)


if __name__ == "__main__":
    unittest.main()

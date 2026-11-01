"""static/graph.js, graph.css, graph-i18n-ru.js (WP-P3) without a JS engine: the DOM is built only with
createElement/textContent (no innerHTML and friends, no eval), nothing is loaded from outside, nothing breaks the
panel's CSP (no inline styles), no endless CSS animations, brackets balance, every text key has a Russian text,
the export is window.KaskadGraph = {mount, unmount}. With node installed — also ``node --check``."""

import os
import re
import shutil
import subprocess
import unittest

from kaskad.gw import model

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")
SVG_NS = "http://www.w3.org/2000/svg"


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def strip_comments(src):
    """JS/CSS without /* */ and // comments (strings and regex literals kept; good enough for the checks below)."""
    out, i, n, quote = [], 0, len(src), None
    while i < n:
        c = src[i]
        if quote:
            out.append(c)
            if c == "\\":
                out.append(src[i + 1:i + 2])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
            out.append(c)
            i += 1
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif src.startswith("//", i) and (i == 0 or src[i - 1] != ":"):
            j = src.find("\n", i)
            i = n if j < 0 else j
        else:
            out.append(c)
            i += 1
    return "".join(out)


_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_WORDS = ("return", "typeof", "case", "in", "of", "new", "delete", "void", "throw")


def balance(src):
    """Brackets of a JS source balance (strings, comments and regex literals skipped). Raises ValueError."""
    pairs = {")": "(", "]": "[", "}": "{"}
    stack, i, n, prev, word = [], 0, len(src), "", ""
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                raise ValueError("unclosed comment at %d" % i)
            i = j + 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\n":
                    raise ValueError("line break inside a string at %d" % i)
                j += 2 if src[j] == "\\" else 1
            if j >= n:
                raise ValueError("unclosed string at %d" % i)
            i, prev, word = j + 1, "a", ""
            continue
        if c == "/" and (prev in _REGEX_AFTER or prev == "" or word in _REGEX_WORDS):
            j, klass = i + 1, False
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "\n":
                    raise ValueError("line break inside a regex at %d" % i)
                if src[j] == "[":
                    klass = True
                elif src[j] == "]":
                    klass = False
                elif src[j] == "/" and not klass:
                    break
                j += 1
            j += 1
            while j < n and src[j].isalpha():
                j += 1
            i, prev, word = j, "a", ""
            continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            word, prev, i = src[i:j], "a", j
            continue
        if c in "([{":
            stack.append((c, i))
        elif c in ")]}":
            if not stack or stack[-1][0] != pairs[c]:
                raise ValueError("unbalanced %r at %d" % (c, i))
            stack.pop()
        prev, word = c, ""
        i += 1
    if stack:
        raise ValueError("unclosed %r at %d" % stack[-1])


def dictionary():
    src = read("graph-i18n-ru.js")
    return dict(re.findall(r"'(graph\.[A-Za-z0-9_.]+)':\s*'((?:[^'\\]|\\.)*)'", src))


class GraphJsTest(unittest.TestCase):
    def setUp(self):
        self.js = read("graph.js")
        self.code = strip_comments(self.js)

    def test_dom_is_built_safely(self):
        for bad in (".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write", "DOMParser",
                    "createContextualFragment"):
            self.assertNotIn(bad, self.code, bad)
        for rx in (r"\beval\s*\(", r"\bnew\s+Function\b", r"\bset(Timeout|Interval)\s*\(\s*['\"]", r"\bsetInterval\s*\("):
            self.assertIsNone(re.search(rx, self.code), rx)
        self.assertNotIn("`", self.code)                                  # ES5, no templates with markup

    def test_nothing_from_outside_and_csp(self):
        for name in ("graph.js", "graph.css", "graph-i18n-ru.js"):
            text = read(name).replace(SVG_NS, "")
            self.assertIsNone(re.search(r"https?://|//[a-z0-9.-]+\.[a-z]{2,}/", text), name)
        self.assertNotIn("@import", read("graph.css"))
        self.assertIsNone(re.search(r"url\(\s*['\"]?(https?:|//)", read("graph.css")))
        self.assertIsNone(re.search(r"setAttribute\(\s*['\"]style['\"]", self.code))        # CSP style-src 'self'
        self.assertIsNone(re.search(r"\bstyle=", self.code))
        self.assertIsNone(re.search(r"\bfetch\s*\(|XMLHttpRequest|WebSocket|EventSource", self.code))   # only ctx.api

    def test_brackets_and_export(self):
        for name in ("graph.js", "graph-i18n-ru.js"):
            balance(read(name))
        self.assertIn("window.KaskadGraph = { mount: mount, unmount: unmount, show: show };", self.code)
        self.assertRegex(self.code, r"function mount\(el, ctx\)")
        self.assertRegex(self.code, r"function unmount\(\)")

    def test_balance_catches_slips(self):
        balance("var a = s.replace(/[)\\]]/g, '(') + (b / c);")
        for bad in ("f(a;", "var s = 'x;", "if (a) { b();", "x = [1, 2);"):
            with self.assertRaises(ValueError):
                balance(bad)

    @unittest.skipUnless(shutil.which("node"), "node не установлен")
    def test_node_check(self):
        for name in ("graph.js", "graph-i18n-ru.js"):
            res = subprocess.run(["node", "--check", os.path.join(STATIC, name)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)

    def test_every_text_key_has_a_russian_text(self):
        d = dictionary()
        used = set(k for k in re.findall(r"\btn?\('(graph\.[A-Za-z0-9_.]+)'", self.code) if not k.endswith("."))
        self.assertTrue(used)
        self.assertEqual(sorted(k for k in used if k not in d), [])
        dynamic = {"graph.kind.": model.DEVICE_KINDS, "graph.platform.": model.PLATFORMS,
                   "graph.from.": ("device", "user", "default"), "graph.pfrom.": ("device", "user", "default"),
                   "graph.fail.": model.FAILS,
                   "graph.trace.fail.": model.FAILS,
                   "graph.edge.": ("conn", "flow", "pass", "route", "fail", "member", "rule"),
                   "graph.edge.help.": ("conn", "flow", "pass", "route", "fail", "member", "rule"),
                   "graph.edge.failNow.": model.FAILS, "graph.prof.short.": ("ru_direct", "all_exit"),
                   "graph.exit.kind.": ("vless", "awg", "bad", "missing")}
        for prefix in re.findall(r"'(graph\.[A-Za-z0-9_.]+\.)' \+", self.code):
            self.assertIn(prefix, dynamic, prefix)
            self.assertEqual([v for v in dynamic[prefix] if prefix + v not in d], [], prefix)
        # keys picked by a variable (ruOpen -> t(ru.why)) are written whole in the code: each has its text
        for k in re.findall(r"why: '(graph\.[A-Za-z0-9_.]+)'", self.code):
            self.assertIn(k, d, k)
        for k, v in d.items():
            if "|" in v:
                self.assertEqual(len(v.split("|")), 3, k)                 # Russian: 1, 2–4, 5+
            self.assertNotRegex(v, r"<[a-z/]", k)                         # texts, not markup

    def test_on_the_map_the_highlight(self):
        """«На схеме»: the highlight reuses «Куда пойдёт» (traceOf with every inbound) and «Только активные» (its arrows
        stay visible); a click past it does not take it off — its button and Esc do; the inherited routes stay dashed;
        the plate above the map has «Вернуться к настройкам» (to the list — «Вернуться к списку»)."""
        c = self.code
        self.assertIn("dids.forEach(function (did) { merge(traceOf(did)); });", c)      # one path per device
        self.assertIn("if (S.trace && S.trace.edges[e.k]) return true;", c)                  # «Выделить активные»
        self.assertIn("if (!edgeLive(e)) c += ' is-idle';", c)                              # dimmed, not hidden (М3)
        self.assertIn("function clearTrace() { if (!S.trace || S.trace.focus) return;", c)
        self.assertIn("case 'focus-off': setFocus(null); break;", c)
        self.assertIn("if (S.focus) { e.preventDefault(); setFocus(null); }", c)
        self.assertIn("f.from === 'list' ? t('graph.focus.backList') : t('graph.focus.back')", c)
        self.assertIn("S.ctx.showOnMap(id, 'list')", c)
        css = strip_comments(read("graph.css"))
        self.assertIn(".kg-e--fail.is-trace .kg-e__base { stroke-dasharray: 6 5; }", css)
        d = dictionary()
        self.assertEqual(d["graph.focus.back"], "Вернуться к настройкам")
        self.assertEqual(d["graph.focus.off"], "Снять подсветку")
        i18n = read("graph-i18n-ru.js")
        self.assertLess(i18n.index("// на схеме: подсветка человека"), i18n.index("'graph.focus.back'"))

    def test_no_fail_policy_for_block_and_direct(self):
        """«блок» и «напрямую» могут хранить политику отказа (model.kept_fail) — поле её не показывает, шаг пути тоже."""
        c = self.code
        self.assertIn("if (r && (r.via === 'direct' || r.via === 'block')) { sel.appendChild(h('option', { value: '', selected: true, text: t('graph.fail.none') })); return sel; }", c)
        self.assertIn("(P.via !== 'direct' && P.via !== 'block' ? ' · ' + t('graph.trace.fail.' + P.fail) : '')", c)

    def test_css_has_no_endless_animation_and_respects_reduced_motion(self):
        css = strip_comments(read("graph.css"))
        self.assertNotIn("infinite", css)
        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn("var(--accent", css)                               # on the design tokens of the panel
        balance(css.replace("'", "").replace('"', ""))

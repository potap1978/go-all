"""Cut real pieces (functions, classes, const statements) out of the panel's scripts by name, to run them in node
against real answers of the server (tests/test_panel_js_contract.py). A small scanner, not a parser: it skips
strings, comments and regex literals the way tests/test_panel_graph_js.balance() does — enough for these files
(ES2017, no template literals)."""

import json
import re

_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_WORDS = ("return", "typeof", "case", "in", "of", "new", "delete", "void", "throw")
_CLOSE = {"(": ")", "[": "]", "{": "}"}


def _scan(src, i, stop):
    """Walk ``src`` from ``i``; ``stop(c, depth, i)`` is asked at every punctuation character outside strings,
    comments and regex literals (``depth`` before the character). Returns the index where it said True."""
    n, depth, prev, word = len(src), 0, "", ""
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
        if stop(c, depth, i):
            return i
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        prev, word = c, ""
        i += 1
    raise ValueError("ran off the end")


def close_of(src, i):
    """Index of the bracket closing the one at ``i``."""
    if src[i] not in _CLOSE:
        raise ValueError("not an opening bracket at %d: %r" % (i, src[i]))
    return _scan(src, i, lambda c, depth, at: at > i and depth == 1 and c in ")]}")


def function(src, name):
    """``function name(…) {…}`` (also ``async function``) — its whole source."""
    m = re.search(r"(?m)^[ \t]*(?:async\s+)?function\s+%s\s*(?=\()" % re.escape(name), src)
    if m is None:
        raise KeyError("function %s" % name)
    params_end = close_of(src, m.end())
    body = src.index("{", params_end)
    return src[m.start():close_of(src, body) + 1].strip()


def klass(src, name):
    """``class name … {…}``."""
    m = re.search(r"(?m)^[ \t]*class\s+%s\b[^{]*" % re.escape(name), src)
    if m is None:
        raise KeyError("class %s" % name)
    return src[m.start():close_of(src, m.end()) + 1].strip()


def const(src, name, keyword="const"):
    """``const name = …;`` — the statement up to its ``;`` at the outer level."""
    m = re.search(r"(?m)^[ \t]*%s\s+%s\s*=" % (keyword, re.escape(name)), src)
    if m is None:
        raise KeyError("%s %s" % (keyword, name))
    end = _scan(src, m.end(), lambda c, depth, at: depth == 0 and c == ";")
    return src[m.start():end + 1].strip()


def assign(src, target):
    """``target = …;`` at the start of a line (``PAGES.rules = {…};``) — the statement up to its ``;``."""
    m = re.search(r"(?m)^[ \t]*%s\s*=" % re.escape(target), src)
    if m is None:
        raise KeyError(target)
    end = _scan(src, m.end(), lambda c, depth, at: depth == 0 and c == ";")
    return src[m.start():end + 1].strip()


def dates(boot_src, name="DATES", lang="ru", locale="ru-RU", now="2026-10-11T12:00:00"):
    """``const DATES = kaskadDates(…)`` for a scope in node: the date formats of the pages (boot.js, 3.3.2 — what
    app.js and setup.js take from window.KaskadLang). ``now`` — the moment whose year is «this year» (node's clock)."""
    return "%s\nconst %s = kaskadDates(%s, %s, () => new Date(%s));" % (
        function(boot_src, "kaskadDates"), name, json.dumps(lang), json.dumps(locale), json.dumps(now))


def find(srcs, name, kind="function", keyword="const"):
    """A piece found in the first of several scripts that has it (F0, UX-FOUNDATION §3.3: a section of the panel moves
    out of app.js into pages/<id>.js, kit.js or templates.js — a test that names the function keeps working).
    ``srcs`` — sources, or (label, source) pairs; ``kind`` — function | klass | const. KeyError lists where it looked."""
    cut = {"function": function, "klass": klass, "const": lambda src, n: const(src, n, keyword)}[kind]
    seen = []
    for i, item in enumerate(srcs):
        label, src = item if isinstance(item, tuple) else ("#%d" % i, item)
        seen.append(label)
        try:
            return cut(src, name)
        except KeyError:
            continue
    raise KeyError("%s %s (not in %s)" % (kind, name, ", ".join(seen)))


def panel_scripts(static_dir):
    """[(name, source)] of the code of the panel in the order of loading: kit.js, templates.js, app.js, then the lazy
    sections pages/<id>.js (dictionaries left out) — for ``find`` and for checks over all of the panel's code."""
    import os
    names = [n for n in ("kit.js", "templates.js", "app.js") if os.path.isfile(os.path.join(static_dir, n))]
    pages = os.path.join(static_dir, "pages")
    if os.path.isdir(pages):
        names += ["pages/" + n for n in sorted(os.listdir(pages)) if n.endswith(".js") and "-i18n-" not in n]
    out = []
    for n in names:
        with open(os.path.join(static_dir, n), encoding="utf-8") as f:
            out.append((n, f.read()))
    return out

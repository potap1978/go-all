"""Checks of the translation (tests, kaskadctl): catalogs are complete, code shows no Russian left untranslated.

    catalog_problems(areas=None, catalog_dir=None)
        ["<area>: ..."] — every area file of every language parses, is a flat {str: str}, keys begin with
        "<area>.", no key is in two areas, ru and en have the same keys (number forms: ru «одна|несколько|много»,
        en «one|other» when the Russian text has 3 «|» forms), the same placeholders {name} per key,
        every text formats, no Cyrillic in English. [] — all good. ``areas`` — only these (None: all found).
    cyrillic_strings(*paths)
        ["file:line: text"] — string literals with Cyrillic in the Python files (dirs: recursively) that a person
        could see. Not counted: comments, docstrings (of a module, class, function), the arguments of tr()/trn()
        (keys) and any literal on a line with the mark ``# i18n: skip`` (Russian that must stay — e.g. a pattern
        matched against the output of a Russian-speaking program; say why in the same comment).
    assert_* — the same as unittest assertions with the list in the message.
"""

import ast
import json
import os
import re
import string

from . import CATALOG_DIR, LANGS, PLURAL_FORMS

CYR = re.compile(u"[\u0400-\u04FF]")
SKIP_MARK = re.compile(r"#\s*i18n:\s*skip\b")


def _fields(text):
    out = set()
    try:
        for _lit, name, spec, _conv in string.Formatter().parse(text):
            if name is not None:
                out.add(re.split(r"[.\[]", name, maxsplit=1)[0])
                if spec:
                    out |= _fields(spec)
    except ValueError as e:
        out.add("!bad format: %s" % e)
    return out


def _areas(catalog_dir):
    found = set()
    for lang in LANGS:
        try:
            found |= set(n[:-5] for n in os.listdir(os.path.join(catalog_dir, lang))
                         if n.endswith(".json") and not n.startswith("."))
        except OSError:
            pass
    return sorted(found)


def _read(path):
    """(dict or None, problem or None)."""
    if not os.path.isfile(path):
        return None, "нет файла"
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, IOError, ValueError) as e:
        return None, "не читается: %s" % e
    if not isinstance(doc, dict):
        return None, "не объект JSON"
    return doc, None


def catalog_problems(areas=None, catalog_dir=None):
    catalog_dir = catalog_dir or CATALOG_DIR
    out = []
    seen = {}                                   # (lang, key) -> area
    for area in (areas or _areas(catalog_dir)):
        docs = {}
        for lang in LANGS:
            path = os.path.join(catalog_dir, lang, area + ".json")
            doc, err = _read(path)
            if err:
                out.append("%s: %s/%s.json: %s" % (area, lang, area, err))
                continue
            docs[lang] = doc
            for k, v in doc.items():
                if not isinstance(v, str):
                    out.append("%s: %s: %s — значение не строка" % (area, lang, k))
                if not k.startswith(area + "."):
                    out.append("%s: %s: %s — ключ не начинается с «%s.»" % (area, lang, k, area))
                if (lang, k) in seen:
                    out.append("%s: %s: %s — ключ уже есть в области %s" % (area, lang, k, seen[(lang, k)]))
                seen[(lang, k)] = area
                if isinstance(v, str):
                    if any(f.startswith("!") for f in _fields(v)):
                        out.append("%s: %s: %s — плохой формат: %r" % (area, lang, k, v))
                    if lang != "ru" and CYR.search(v):
                        out.append("%s: %s: %s — кириллица в переводе: %r" % (area, lang, k, v))
        if len(docs) != len(LANGS):
            continue
        ru, en = docs["ru"], docs["en"]
        for k in sorted(set(ru) | set(en)):
            missing = [lang for lang in LANGS if k not in docs[lang]]
            if missing:
                out.append("%s: %s — нет в %s" % (area, k, ", ".join(missing)))
                continue
            r, e = ru[k], en[k]
            if not (isinstance(r, str) and isinstance(e, str)):
                continue
            nr, ne = r.count("|") + 1, e.count("|") + 1
            # «|» — the forms of a trn() text (ru 3, en 2), or the same literal «|» in both («ru|en» in a help line)
            if nr != ne and (nr, ne) != (PLURAL_FORMS["ru"], PLURAL_FORMS["en"]):
                out.append("%s: %s — формы числа через «|»: ru %d, en %d, нужно ru %d и en %d (или поровну)" % (
                    area, k, nr, ne, PLURAL_FORMS["ru"], PLURAL_FORMS["en"]))
            fr, fe = _fields(r), _fields(e)
            if nr > 1:
                fr.discard("n")                 # trn() fills {n}; a form may leave the number out («один выход»)
                fe.discard("n")
            if fr != fe:
                out.append("%s: %s — подстановки ru %s ≠ en %s" % (area, k, sorted(fr), sorted(fe)))
    return out


def _docstring_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _tr_arg_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            if name in ("tr", "trn", "_t") and node.args:
                for sub in ast.walk(node.args[0]):
                    ids.add(id(sub))
    return ids


def _py_files(paths):
    for p in paths:
        if os.path.isdir(p):
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and not d.startswith("."))
                for fn in sorted(filenames):
                    if fn.endswith(".py"):
                        yield os.path.join(dirpath, fn)
        else:
            yield p


def cyrillic_strings(*paths):
    out = []
    for path in _py_files(paths):
        hits = []
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        lines = src.splitlines()
        tree = ast.parse(src, filename=path)
        skip = _docstring_ids(tree) | _tr_arg_ids(tree)
        span = {}                               # parts of an f-string: the lines of the f-string (3.8-3.11 misplace them)
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                for sub in ast.walk(node):
                    span[id(sub)] = (node.lineno, getattr(node, "end_lineno", None) or node.lineno)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in skip:
                continue
            if not CYR.search(node.value):
                continue
            first, last = span.get(id(node)) or (getattr(node, "lineno", 1), None)
            last = last or getattr(node, "end_lineno", None) or first
            if any(SKIP_MARK.search(lines[i - 1]) for i in range(first, last + 1) if 0 < i <= len(lines)):
                continue
            shown = node.value.strip().splitlines()[0][:80] if node.value.strip() else node.value
            hits.append((first, "%s:%d: %s" % (path, first, shown)))
        out.extend(h for _l, h in sorted(hits, key=lambda x: x[0]))
    return out


def assert_catalog_complete(case, areas=None, catalog_dir=None):
    """unittest: ``assert_catalog_complete(self, ["gw"])``."""
    probs = catalog_problems(areas, catalog_dir)
    case.assertEqual(probs, [], "каталоги перевода:\n  " + "\n  ".join(probs))


def assert_no_cyrillic(case, *paths):
    """unittest: ``assert_no_cyrillic(self, "lib/kaskad/gw")`` — no Russian a person would see is left in code."""
    found = cyrillic_strings(*paths)
    case.assertEqual(found, [], "%d строк с кириллицей вне каталогов (tr()/«# i18n: skip»):\n  %s"
                     % (len(found), "\n  ".join(found[:60])))

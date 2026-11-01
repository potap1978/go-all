"""3.3.2 E2: the English of the rest of the Python core (k3/handoff/I18N-3.3.2.md).

* every module of E2's area has no Cyrillic in its strings for people — they go through ``tr()``; a string that must
  stay Russian (a regex over Russian text of a 2.x file, the decoy content of a site…) carries ``# i18n: skip`` on the
  line where it starts;
* every literal key of ``tr("…")`` there is in the ru and the en catalog;
* every catalog file those keys live in: the keys of ru == en, the placeholders ``{name}`` of each string are equal,
  each string formats.
"""

import ast
import glob
import json
import os
import re
import string
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib", "kaskad")
CAT = os.path.join(LIB, "i18n", "catalog")
OTHERS = ("setup", "panel", "gw", "bot", "mcp", "i18n", "vendor")      # C, C, E1, F, not translated, E1, data
CYR = re.compile("[А-Яа-яЁё]")
KEEP = "# i18n: skip"


def modules():
    out = []
    for f in sorted(glob.glob(os.path.join(LIB, "**", "*.py"), recursive=True)):
        rel = os.path.relpath(f, LIB).replace(os.sep, "/")
        if rel.split("/")[0] in OTHERS:
            continue
        out.append(rel)
    return out


def _docstrings(tree):
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            b = n.body[0]
            if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str):
                ids.add(id(b.value))
    return ids


def _parse(rel):
    with open(os.path.join(LIB, rel), encoding="utf-8") as f:
        src = f.read()
    return src.splitlines(), ast.parse(src)


def _fields(s):
    return sorted({f for _, f, _, _ in string.Formatter().parse(s) if f is not None})


def _load(lang, area):
    p = os.path.join(CAT, lang, area + ".json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


KEY_ARG = {"tr": 0, "trn": 0, "msg": 0, "msg_n": 0, "err": 1}     # util.msg/msg_n/err keep a catalog key too


def tr_keys():
    """{key: "module:line"} of every literal catalog key of tr(…), trn(…), util.msg(…), util.msg_n(…),
    util.err(cls, …) in E2's modules."""
    out = {}
    for rel in modules():
        _, tree = _parse(rel)
        for n in ast.walk(tree):
            name = getattr(n.func, "id", getattr(n.func, "attr", None)) if isinstance(n, ast.Call) else None
            i = KEY_ARG.get(name)
            if (i is not None and len(n.args) > i and isinstance(n.args[i], ast.Constant)
                    and isinstance(n.args[i].value, str) and "." in n.args[i].value):
                out.setdefault(n.args[i].value, "%s:%d" % (rel, n.lineno))
    return out


def trn_keys():
    """{key} of every literal first argument of trn(…) in E2's modules: their values hold the forms through «|»."""
    out = set()
    for rel in modules():
        _, tree = _parse(rel)
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == "trn"
                    and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
                out.add(n.args[0].value)
    return out


class _Any(object):
    def __format__(self, spec):
        return "x"


class NoCyrillic(unittest.TestCase):
    def test_strings_for_people_go_through_tr(self):
        bad = []
        for rel in modules():
            lines, tree = _parse(rel)
            doc = _docstrings(tree)
            for n in ast.walk(tree):
                if (isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc
                        and CYR.search(n.value) and KEEP not in lines[n.lineno - 1]):
                    bad.append("%s:%d: %s" % (rel, n.lineno, lines[n.lineno - 1].strip()[:120]))
        self.assertEqual(bad, [], "Cyrillic outside the catalogs (tr() or «%s»):\n%s" % (KEEP, "\n".join(bad)))


class Catalogs(unittest.TestCase):
    def test_keys_exist(self):
        keys = tr_keys()
        self.assertTrue(keys)
        miss = []
        for k, where in sorted(keys.items()):
            area = k.split(".", 1)[0]
            for lang in ("ru", "en"):
                try:
                    cat = _load(lang, area)
                except OSError:
                    cat = {}
                if k not in cat:
                    miss.append("%s/%s.json: %s (%s)" % (lang, area, k, where))
        self.assertEqual(miss, [])

    def test_plural_forms(self):
        """trn keys: ru 1–3 forms, en 1–2 (one|other); no leftover .one/.few/.many triples of util.tr_plural."""
        keys = trn_keys()
        self.assertTrue(keys)
        for k in sorted(keys):
            area = k.split(".", 1)[0]
            ru, en = _load("ru", area)[k], _load("en", area)[k]
            self.assertIn(len(ru.split("|")), (1, 2, 3), k)
            self.assertIn(len(en.split("|")), (1, 2), k)
        for area in sorted({k.split(".", 1)[0] for k in tr_keys()}):
            left = [k for k in _load("ru", area) if k.endswith((".few", ".many"))]
            self.assertEqual(left, [], area)

    def test_areas_complete(self):
        areas = sorted({k.split(".", 1)[0] for k in tr_keys()})
        for area in areas:
            with self.subTest(area=area):
                ru, en = _load("ru", area), _load("en", area)
                self.assertEqual(sorted(set(ru) - set(en)), [], "%s: no English" % area)
                self.assertEqual(sorted(set(en) - set(ru)), [], "%s: no Russian" % area)
                for k in ru:
                    self.assertTrue(k.startswith(area + "."), "%s: key %s of another area" % (area, k))
                    self.assertIsInstance(ru[k], str, k)
                    self.assertIsInstance(en[k], str, k)
                    self.assertEqual(_fields(ru[k]), _fields(en[k]), "%s: placeholders differ" % k)
                    self.assertFalse({"key", "lang"} & set(_fields(ru[k])), "%s: {key}/{lang} clash with tr()" % k)
                    self.assertTrue(en[k].strip(), "%s: empty English" % k)
                    self.assertIsNone(CYR.search(en[k]), "%s: Cyrillic in English: %s" % (k, en[k]))
                    kw = {f: _Any() for f in _fields(ru[k])}
                    ru[k].format(**kw)
                    en[k].format(**kw)


if __name__ == "__main__":
    unittest.main()

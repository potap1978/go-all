#!/usr/bin/env python3
"""Английские словари веба вслед за русскими (3.3.2, docs/k3/GLOSSARY-en.md).

    python3 tools/i18n_web_sync.py [--pending tools/i18n-pending/*.json] [--check]

Для каждой пары static/**/<имя>-ru.js → <имя>-en.js английский файл собирается заново по русскому: тот же порядок
ключей и разделов, шапка и обёртка английского файла остаются его собственными. Текст ключа — из текущего
английского файла, а если в pending-файле есть перевод (новый или изменённый русский текст) — оттуда. Ключи, которых
больше нет в русском словаре, уходят. Ключ без перевода остаётся русским и попадает в список (код 1) — тест полноты
(tests/test_i18n_web.py) тоже его не пропустит.

pending — {"_about": …, "<путь от static>": {ключ: {"ru": русский текст, "en": перевод}}}; перевод берётся, только
если русский текст ключа сейчас тот же (поменялся — нужен новый). Группы страницы человека — «группа.ключ».
--check — ничего не пишет, только говорит, что поменялось бы."""

import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
PAIRS = ("i18n-ru.js", "graph-i18n-ru.js", "setup/setup-i18n-ru.js", "user-i18n-ru.js", "kit-i18n-ru.js",
         "pages/dev-kit-i18n-ru.js", "pages/diag-i18n-ru.js", "pages/speed-i18n-ru.js",
         "pages/ovb-i18n-ru.js")
CYR = re.compile("[А-Яа-яЁё]")
OWN_NAME = {"lk.langRu", "c.langRu", "lang_ru"}

_ENTRY = re.compile(r"""("[A-Za-z0-9_.]+"|'[A-Za-z0-9_.]+'|\b[A-Za-z_][A-Za-z0-9_]*)\s*:\s*("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')""")
_GROUP = re.compile(r"""^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\{\s*$""")


def _unq(tok):
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), tok[1:-1])


def _q(text, ch):
    s = text.replace("\\", "\\\\").replace("\n", "\\n").replace(ch, "\\" + ch)
    return ch + s + ch


def _split(src):
    """(head up to and with the «= {» line, body after it)."""
    lines = src.split("\n")
    i = next(n for n, l in enumerate(lines) if re.search(r"=\s*\{\s*$", l))
    return "\n".join(lines[:i + 1]), lines[i + 1:]


def entries(lines):
    """[(line index, match, full key)] of every «key: 'text'» in the body (groups give «group.key»)."""
    out, group = [], None
    for n, line in enumerate(lines):
        if line.strip().startswith("//"):
            continue
        g = _GROUP.match(line)
        if g:
            group = g.group(1)
            continue
        for m in _ENTRY.finditer(line):
            k = m.group(1).strip("\"'")
            out.append((n, m, (group + "." + k) if group else k))
        if group and re.match(r"^\s*\}", line):
            group = None
    return out


def values(path):
    with open(path, encoding="utf-8") as f:
        _, body = _split(f.read())
    return dict((k, _unq(m.group(2))) for _, m, k in entries(body))


def sync(ru_rel, pending, write=True):
    ru_path = os.path.join(STATIC, ru_rel)
    en_path = ru_path.replace("-ru.js", "-en.js")
    with open(ru_path, encoding="utf-8") as f:
        ru_src = f.read()
    with open(en_path, encoding="utf-8") as f:
        en_src = f.read()
    en_head, _ = _split(en_src)
    _, body = _split(ru_src)
    have = values(en_path)
    ru_now = values(ru_path)
    new = dict((k, v["en"]) for k, v in pending.get(ru_rel, {}).items() if ru_now.get(k) == v["ru"])
    missing, changed = [], 0
    rows = entries(body)
    by_line = {}
    for n, m, k in rows:
        by_line.setdefault(n, []).append((m, k))
    for n, items in by_line.items():
        line = body[n]
        for m, k in sorted(items, key=lambda x: -x[0].start()):
            text = new.get(k, have.get(k))
            if text is None:
                missing.append(k)
                continue
            if text != have.get(k):
                changed += 1
            ch = m.group(2)[0]
            line = line[:m.start(2)] + _q(text, ch) + line[m.end(2):]
        body[n] = line
    keys = set(k for _, _, k in rows)
    gone = sorted(set(have) - keys)
    out = en_head + "\n" + "\n".join(body)
    if write and out != en_src:
        with open(en_path, "w", encoding="utf-8") as f:
            f.write(out)
    left = [k for k in missing if k not in OWN_NAME]
    print("%s: переведено новых/изменённых %d, убрано %d, без перевода %d%s" % (
        os.path.relpath(en_path, STATIC), changed, len(gone), len(left), (": " + ", ".join(left)) if left else ""))
    return left


def main(argv):
    check = "--check" in argv
    files = [a for a in argv if a.endswith(".json")] or sorted(glob.glob(os.path.join(ROOT, "tools", "i18n-pending",
                                                                                         "*.json")))
    pending = {}
    for p in files:
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
        for rel, d in doc.items():
            if rel.startswith("_"):
                continue
            pending.setdefault(rel, {}).update(d)
    bad = []
    for rel in PAIRS:
        bad += sync(rel, pending, write=not check)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

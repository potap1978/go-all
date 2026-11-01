#!/usr/bin/env python3
"""Cyrillic left in the code of install.sh functions (texts of the SSH menu must come from the catalogs).

Usage: python3 tools/i18n_h/nocyr.py install.sh REGEX [REGEX ...]

Checks every shell function (outside the K3_PAYLOAD block) whose name fully matches one of the regexes: the
code of the function, without comments (whole-line and trailing ones, outside quotes), must hold no Cyrillic.
A line marked with a comment «# TODO i18n-exchange» (the menu matches a text of the core — waits for the
machine exchange of I18N-3.3.2.md) or «# i18n-keep: <reason>» is skipped. Prints «LINE FUNC: code» for each
hit and «FUNCS=N» last; exit 1 on hits, 2 when no function matched.
"""
import re
import sys

CYR = re.compile("[Ѐ-ӿ]")
FUNC = re.compile(r"^([A-Za-z0-9_]+)\(\)\s*\{")
MARKS = ("# TODO i18n-exchange", "# i18n-keep")


def code_of(line, state):
    """The line without its comment; state — the open quote carried from the previous line ('' / "'" / '"')."""
    out, q, prev = [], state, " "
    i = 0
    while i < len(line):
        c = line[i]
        if q == "'":
            if c == "'":
                q = ""
        elif q == '"':
            if c == "\\" and i + 1 < len(line):
                out.append(c + line[i + 1]); i += 2; prev = "x"; continue
            if c == '"':
                q = ""
        else:
            if c == "\\" and i + 1 < len(line):
                out.append(c + line[i + 1]); i += 2; prev = "x"; continue
            if c == "#" and (prev.isspace() or prev in ";(|&"):
                break
            if c in "'\"":
                q = c
        out.append(c)
        prev = c
        i += 1
    return "".join(out), q


def main(argv):
    path, pats = argv[1], [re.compile(p) for p in argv[2:]]
    lines = open(path, encoding="utf-8").read().split("\n")
    funcs, skip, cur = 0, False, None
    hits, state = [], ""
    for n, line in enumerate(lines, 1):
        if line.startswith("# >>> K3_PAYLOAD"):
            skip = True
        if line.startswith("# <<< K3_PAYLOAD"):
            skip = False
            continue
        if skip:
            continue
        m = FUNC.match(line)
        if m and not state:
            cur = m.group(1) if any(p.fullmatch(m.group(1)) for p in pats) else None
            funcs += 1 if cur else 0
            continue
        if cur is None:
            continue
        if not state and line.startswith("}"):
            cur = None
            continue
        if not state and line.lstrip().startswith("#"):
            continue
        code, state = code_of(line, state)
        if any(mk in line for mk in MARKS):
            continue
        if CYR.search(code):
            hits.append("%d %s: %s" % (n, cur, line.strip()[:150]))
    for h in hits:
        print(h)
    print("FUNCS=%d" % funcs)
    if not funcs:
        return 2
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

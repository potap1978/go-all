#!/usr/bin/env python3
"""Fail if Kaskad 3 Python code uses syntax or stdlib APIs newer than Python 3.8.

Servers run Debian 11+/Ubuntu 20.04+ (python 3.8+), development happens on newer Pythons, so
this catches the usual slips: syntax via ``ast.parse(feature_version=(3, 8))`` and a deny-list of
3.9+ APIs and idioms. Usage: python3 tools/k3_py_compat.py <dir-or-file>...
"""

import ast
import os
import re
import sys

DENY = [
    (re.compile(r"\.removeprefix\("), "str.removeprefix (3.9)"),
    (re.compile(r"\.removesuffix\("), "str.removesuffix (3.9)"),
    (re.compile(r"^\s*import zoneinfo|from zoneinfo"), "zoneinfo (3.9)"),
    (re.compile(r"functools\.cache\b|@cache\b"), "functools.cache (3.9)"),
    (re.compile(r"\bmath\.lcm\("), "math.lcm (3.9)"),
    (re.compile(r"\.is_relative_to\("), "Path.is_relative_to (3.9)"),
    (re.compile(r"\bstrict=True\)"), "zip(strict=) (3.10)"),
    (re.compile(r"\bTypeAlias\b|\bParamSpec\b"), "typing 3.10+"),
    (re.compile(r"\btomllib\b"), "tomllib (3.11)"),
    (re.compile(r"\bdatetime\.UTC\b"), "datetime.UTC (3.11)"),
    (re.compile(r"\bExceptionGroup\b|except\*"), "exception groups (3.11)"),
    (re.compile(r"\bitertools\.(pairwise|batched)\("), "itertools.pairwise/batched (3.10/3.12)"),
    (re.compile(r"\bbisect\.\w+\([^)]*key="), "bisect key= (3.10)"),
    (re.compile(r"\bshlex\.join\("), "ok in 3.8"),  # placeholder kept for documentation
]
# PEP 585/604 annotations evaluated at runtime break 3.8: list[str], dict[str, int], X | None.
RUNTIME_GENERIC = re.compile(r"(?<![\w.])(list|dict|set|tuple|type)\[")


def check_file(path, problems):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    try:
        tree = ast.parse(src, filename=path, feature_version=(3, 8))
    except SyntaxError as e:
        problems.append("%s:%s: syntax newer than 3.8: %s" % (path, e.lineno, e.msg))
        return
    has_future = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                     and any(a.name == "annotations" for a in n.names) for n in tree.body)
    for i, line in enumerate(src.splitlines(), 1):
        code = line.split("#", 1)[0]
        for rx, what in DENY:
            if what.startswith("ok "):
                continue
            if rx.search(code):
                problems.append("%s:%d: %s" % (path, i, what))
    if not has_future:
        # annotations without the __future__ import are evaluated at definition time
        for node in ast.walk(tree):
            anns = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                anns = [a.annotation for a in node.args.args + node.args.kwonlyargs if a.annotation]
                if node.returns:
                    anns.append(node.returns)
            elif isinstance(node, ast.AnnAssign):
                anns = [node.annotation]
            for a in anns:
                seg = ast.get_source_segment(src, a) or ""
                if RUNTIME_GENERIC.search(seg) or re.search(r"\w\s*\|\s*(None|\w)", seg):
                    problems.append("%s:%d: annotation %r needs 'from __future__ import annotations' on 3.8"
                                    % (path, a.lineno, seg))


def main(argv):
    problems = []
    for target in argv or ["lib", "tests"]:
        if os.path.isfile(target):
            check_file(target, problems)
            continue
        for d, _, files in os.walk(target):
            for n in sorted(files):
                if n.endswith(".py"):
                    check_file(os.path.join(d, n), problems)
    for p in problems:
        print("  " + p)
    if problems:
        print("python 3.8 compatibility: %d problem(s)" % len(problems))
        return 1
    print("python 3.8 compatibility: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

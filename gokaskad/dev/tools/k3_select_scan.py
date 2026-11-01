#!/usr/bin/env python3
"""k3_select_scan.py — which folders of the repository a test goes through whole (os.walk, os.listdir, os.scandir,
glob.glob, Path.glob/rglob/iterdir, «git grep», «grep -r», «find»): such a test reads every file there, not a file by
name, so a change anywhere in the folder needs it (REV-speed С1). Used by tools/test_select.py.

How the folder is found — the source is read, not run:
  * the argument of the call is followed through the names of the module (ROOT = dirname(dirname(abspath(__file__))),
    os.path.join, Path(...) / "x", .parent, .parents[n], f-strings, «for lang in ("ru", "en")», dict literals),
    the constants of the imported modules (i18n.CATALOG_DIR) and the return values of the functions of lib/ and tests/;
  * a folder given to a function as an argument (``check.assert_no_cyrillic(self, BOT_DIR)`` → os.walk inside
    lib/kaskad/i18n/check.py) — at the call site of the test, through any number of functions;
  * a value of the run time (tempfile, paths.P(…) under the fake root, self.d = mkdtemp()) — not a folder of the
    repository, skipped;
  * a helper of tests/ (not test*.py) that goes through a folder — every test importing it.

What the source cannot say (a folder from a parameter of the test itself, «git grep» over the tree): the module says it
with a line ``# k3-select: dirs=<folder>,<folder>`` (paths from the root of the repository, «.» — all of it; «!tests»,
«!*.md» — but not these: a folder or a name pattern). Without
the line such a module is picked for any change (the whole repository) and tests/test_k3_test_tools.py fails — a new
case is seen at once.

Reads (REV-speed-2 С5) — the same reading for the files a test reads by a path put together from parts (open,
read_text, runpy, a copy, a command line; through the helpers given the name); a read the source cannot place is
marked ``# k3-select: files=<pattern>,…`` («!<pattern>» — but not these; ``files=!*`` — a file of the run time, a temp
root: no file of the repository, the module goes by its imports only). text_templates — a net in the text of any source (JS, shell too): a quoted
name with a placeholder. Reads.core_runs (З5) — the core a test runs in a process of its own.
"""

import ast
import builtins
import fnmatch
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK_RE = re.compile(r"^[ \t]*#[ \t]*k3-select:[ \t]*dirs=([^\s—]+)", re.M)
WILD = "\x00"                      # a piece of a path nobody can know from the source
TOK = re.compile("\x01([A-Za-z_][A-Za-z0-9_]*)\x01")   # «the value of parameter X» inside a function
OPAQUE = "opaque"                  # a value of the run time: not a folder of the repository
STAR = "_star_"                    # tok(STAR + p): the parts of «*p» joined into one path (join(ROOT, *p))
SCAN_CALLS = {"os.walk", "os.listdir", "os.scandir", "glob.glob", "glob.iglob", "os.fwalk"}
MAX_ALTS = 48
MAX_DEPTH = 6


def tok(name):
    return "\x01%s\x01" % name


class Info:
    """What the source of one module says: names, functions, imports, the calls and where they are."""

    def __init__(self, scan, mod, rel, tree):
        self.mod, self.rel, self.file = mod, rel, os.path.join(ROOT, rel)
        self.repo_mod = scan.repo_mod
        self.top, self.funcs, self.attrs, self.names, self.classes = {}, {}, {}, {}, set()
        self.locals, self.method = {}, set()
        self.calls, self.lists = [], []       # (Call, scope); (List/Tuple of a shell command, scope)
        self.pkg = mod if rel.endswith("__init__.py") else mod.rpartition(".")[0]
        if tree is not None:
            self.visit_body(tree.body, (), None)

    # ── collecting ──
    def bind(self, table, name, b):
        table.setdefault(name, []).append(b)

    def bind_target(self, table, target, value):
        if isinstance(target, ast.Name):
            self.bind(table, target.id, ("expr", value))
        elif isinstance(target, (ast.Tuple, ast.List)):
            vals = value.elts if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(target.elts) \
                else None
            for i, t in enumerate(target.elts):
                if isinstance(t, ast.Name):
                    self.bind(table, t.id, ("expr", vals[i]) if vals else ("part", value))

    def bind_for(self, table, target, it):
        for name in names_in(target):
            self.bind(table, name, ("for", it, target, name))

    def imports(self, node):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    self.names[a.asname] = self.mod_or_ext(a.name)
                else:
                    head = a.name.split(".")[0]
                    self.names[head] = self.mod_or_ext(head)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = self.pkg.split(".")
                if node.level > 1:
                    base = base[:len(base) - (node.level - 1)]
                prefix = ".".join(base + ([node.module] if node.module else []))
            else:
                prefix = node.module or ""
            for a in node.names:
                full = prefix + "." + a.name
                if self.repo_mod(full):
                    self.names[a.asname or a.name] = ("mod", full)
                elif self.repo_mod(prefix):
                    self.names[a.asname or a.name] = ("attr", prefix, a.name)
                else:
                    self.names[a.asname or a.name] = ("ext", full)

    def mod_or_ext(self, name):
        return ("mod", name) if self.repo_mod(name) else ("ext", name)

    def visit_body(self, body, scope, cls):
        for node in body:
            self.visit(node, scope, cls)

    def visit(self, node, scope, cls):
        table = self.locals[id(scope[-1])] if scope else (self.attrs if cls else self.top)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.funcs.setdefault(node.name, []).append(node)
            if cls:
                self.method.add(id(node))
            loc = self.locals.setdefault(id(node), {})
            a = node.args
            for p in getattr(a, "posonlyargs", []) + a.args + a.kwonlyargs:
                self.bind(loc, p.arg, ("param", p.arg))
            for p in (a.vararg, a.kwarg):
                if p:
                    self.bind(loc, p.arg, ("param", p.arg))
            for d in a.defaults + [x for x in a.kw_defaults if x]:
                self.visit(d, scope, cls)
            self.visit_body(node.body, scope + (node,), None)
            return
        if isinstance(node, ast.ClassDef):
            self.classes.add(node.name)
            self.visit_body(node.body, scope, node.name)
            return
        if isinstance(node, ast.Lambda):
            self.visit(node.body, scope, cls)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            self.imports(node)
            return
        if isinstance(node, ast.Assign):
            for t in node.targets:
                self.bind_target(table, t, node.value)
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in ("self", "cls"):
                    self.bind(self.attrs, t.attr, ("expr", node.value, scope))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            self.bind_target(table, node.target, node.value)
        elif isinstance(node, ast.NamedExpr if hasattr(ast, "NamedExpr") else ()):
            self.bind_target(table, node.target, node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self.bind_for(table, node.target, node.iter)
        elif isinstance(node, ast.comprehension):
            self.bind_for(table, node.target, node.iter)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            for name in names_in(node.optional_vars):
                self.bind(table, name, ("opaque",))
        elif isinstance(node, ast.ExceptHandler) and node.name:
            self.bind(table, node.name, ("opaque",))
        elif isinstance(node, ast.Call):
            self.calls.append((node, scope))
        elif isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            self.lists.append((node, scope))
        for child in ast.iter_child_nodes(node):
            self.visit(child, scope, cls)


def names_in(target):
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [n for t in target.elts for n in names_in(t)]
    if isinstance(target, ast.Starred):
        return names_in(target.value)
    return []


def const_str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def union(results):
    """Several possible values: the paths together; an unknown one adds «?»; only run-time values — run time."""
    out, unknown, opaque = [], False, False
    for r in results:
        if r is None:
            unknown = True
        elif r == OPAQUE:
            opaque = True
        else:
            for s in r:
                if s not in out:
                    out.append(s)
    if unknown:
        if out:
            out.append(WILD)
        else:
            return None
    if not out:
        return OPAQUE if opaque else None
    return out[:MAX_ALTS]


def product(parts, glue):
    """Every combination of the parts (a list of results); a run-time part — run time, an unknown one — «?»."""
    combos = [[]]
    for r in parts:
        if r == OPAQUE:
            return OPAQUE
        alts = [WILD] if r is None else r
        combos = [c + [a] for c in combos for a in alts][:MAX_ALTS]
    return [glue(c) for c in combos]


def pjoin(items):
    out = ""
    for x in items:
        out = x if (x.startswith("/") or not out) else (out.rstrip("/") + "/" + x)
    return out


def dirname(s):
    return s.rpartition("/")[0] if "/" in s else ""


class Scan:
    MARK = MARK_RE
    SHELL = True             # «git grep», «grep -r», «find» in a list literal
    DEEP = False             # a run-time part joined to a known path: anything below it (Reads), not «run time»

    def __init__(self, files, trees, src=None):
        """files: {module: repo path}; trees: {module: ast.Module or None} — lib/ (kaskad.*) and tests/ (tests.*);
        src: {module: text} (for the marks; default — the files on disk)."""
        self.files = files
        self.trees = trees
        self.src = src
        self.infos = {}
        self.memo = {}
        self.busy = set()
        self.summ = {}           # id(FunctionDef) -> {template}: what the function goes through (tokens = its params)
        self.owner = {}          # id(FunctionDef) -> (Info, FunctionDef)
        self.dirs = {}           # tests module -> {repo dir}
        self.unresolved = {}     # tests module -> [line]
        self.marks = {}          # tests module -> {repo dir} from «# k3-select: dirs=…»
        self.excludes = {}       # tests module -> [«!tests», «!*.md» of the mark]

    def repo_mod(self, name):
        return name in self.files

    def info(self, mod):
        if mod not in self.infos:
            self.infos[mod] = Info(self, mod, self.files[mod], self.trees.get(mod)) if mod in self.files else None
        return self.infos[mod]

    # ── values ──
    def ev(self, info, node, scope, depth=0):
        if depth > MAX_DEPTH:
            return None
        s = const_str(node)
        if s is not None:
            return [s]
        if isinstance(node, ast.Constant):
            return OPAQUE
        if isinstance(node, ast.Name):
            return self.ev_name(info, node.id, scope, depth)
        if isinstance(node, ast.Attribute):
            return self.ev_attr(info, node, scope, depth)
        if isinstance(node, ast.Call):
            return self.ev_call(info, node, scope, depth)
        if isinstance(node, ast.BinOp):
            left, right = self.ev(info, node.left, scope, depth), self.ev(info, node.right, scope, depth)
            if isinstance(node.op, ast.Div):
                return self.joined([left, right])
            if isinstance(node.op, ast.Add):
                if any(isinstance(x, (ast.List, ast.Tuple, ast.ListComp)) for x in (node.left, node.right)):
                    return union([left, right])          # a command line put together: its words, not one string
                return product([left, right], "".join)
            if isinstance(node.op, ast.Mod) and left not in (None, OPAQUE):
                args = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
                vals = [self.ev(info, a, scope, depth) for a in args]
                return product([left] + vals, lambda c: fmt_percent(c[0], c[1:]))
            return OPAQUE if OPAQUE in (left, right) else None
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.FormattedValue):
                    parts.append(self.ev(info, v.value, scope, depth))
                else:
                    parts.append([const_str(v) or ""])
            return product([[WILD] if p == OPAQUE else p for p in parts], "".join)
        if isinstance(node, ast.IfExp):
            return union([self.ev(info, node.body, scope, depth), self.ev(info, node.orelse, scope, depth)])
        if isinstance(node, ast.BoolOp):
            return union([self.ev(info, v, scope, depth) for v in node.values])
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            # a list of folders given where one is expected (check.assert_no_cyrillic(self, [a, b])): each of them
            return union([self.ev(info, e.value if isinstance(e, ast.Starred) else e, scope, depth)
                          for e in node.elts]) if node.elts else OPAQUE
        if isinstance(node, ast.Starred):
            return self.ev(info, node.value, scope, depth)
        if isinstance(node, ast.Subscript):
            v = node.value
            if isinstance(v, ast.Attribute) and v.attr == "parents":
                k = node.slice.value if isinstance(node.slice, ast.Index) else node.slice   # 3.8: ast.Index
                if isinstance(k, ast.Constant) and isinstance(k.value, int):
                    r = self.ev(info, v.value, scope, depth)
                    if r in (None, OPAQUE):
                        return r
                    for _ in range(k.value + 1):
                        r = [dirname(x) for x in r]
                    return r
            return OPAQUE if self.ev(info, v, scope, depth) == OPAQUE else None
        if isinstance(node, (ast.Dict, ast.DictComp, ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.Compare,
                             ast.UnaryOp, ast.Lambda)):
            return OPAQUE
        return None

    def ev_bindings(self, info, binds, scope, depth):
        res = []
        for b in binds:
            kind = b[0]
            if kind == "expr":
                sc = b[2] if len(b) > 2 else scope
                res.append(self.ev(info, b[1], sc, depth))
            elif kind == "param":
                res.append([tok(b[1])])
            elif kind == "for":
                res.append(self.ev_for(info, b[1], b[2], b[3], scope, depth))
            elif kind == "opaque":
                res.append(OPAQUE)
            elif kind == "part":
                # a, b = f(): a run-time value stays one; anything else — unknown
                r = self.ev(info, b[1], scope, depth)
                res.append(OPAQUE if r == OPAQUE else None)
            else:
                res.append(None)
        return union(res)

    def lookup(self, info, name, scope):
        """(bindings, the scope they live in) of a name, innermost first; None — not a name of this module."""
        for i in range(len(scope) - 1, -1, -1):
            b = info.locals.get(id(scope[i]), {}).get(name)
            if b:
                return b, scope[:i + 1]
        if name in info.top:
            return info.top[name], ()
        return None, None

    def ev_name(self, info, name, scope, depth):
        if name == "__file__":
            return [info.file]
        binds, sc = self.lookup(info, name, scope)
        if binds is not None:
            if sc:
                key = (id(sc[-1]), name)
                if key in self.busy:
                    # top = top or self.site: inside its own new value the name is still the parameter
                    par = [b for b in binds if b[0] == "param"]
                    return [tok(par[0][1])] if par else None
                self.busy.add(key)
                try:
                    return self.ev_bindings(info, binds, sc, depth)
                finally:
                    self.busy.discard(key)
            key = (info.mod, name)
            if key in self.memo:
                return self.memo[key]
            if key in self.busy:
                return None
            self.busy.add(key)
            try:
                r = self.ev_bindings(info, binds, (), 0)
            finally:
                self.busy.discard(key)
            self.memo[key] = r
            return r
        imp = info.names.get(name)
        if imp:
            if imp[0] == "attr":
                other = self.info(imp[1])
                return self.ev_name(other, imp[2], (), depth) if other else OPAQUE
            return OPAQUE
        if name in info.funcs or name in info.classes or hasattr(builtins, name):
            return OPAQUE
        return None

    def module_of(self, info, node, scope):
        """The repository module a Name/Attribute stands for (``check``, ``kaskad.i18n``) or None."""
        if isinstance(node, ast.Name):
            if self.lookup(info, node.id, scope)[0] is not None:
                return None
            imp = info.names.get(node.id)
            if imp and imp[0] == "mod":
                return imp[1]
            if imp and imp[0] == "attr" and self.repo_mod(imp[1] + "." + imp[2]):
                return imp[1] + "." + imp[2]
            return None
        if isinstance(node, ast.Attribute):
            base = self.module_of(info, node.value, scope)
            if base and self.repo_mod(base + "." + node.attr):
                return base + "." + node.attr
        return None

    def dotted(self, info, node, scope):
        """«os.path.join», «pathlib.Path», «str» — the outside name a Name/Attribute stands for, or None."""
        if isinstance(node, ast.Name):
            if self.lookup(info, node.id, scope)[0] is not None:
                return None
            imp = info.names.get(node.id)
            if imp and imp[0] == "ext":
                return imp[1]
            if not imp and node.id not in info.funcs and hasattr(builtins, node.id):
                return node.id
            return None
        if isinstance(node, ast.Attribute):
            base = self.dotted(info, node.value, scope)
            return base + "." + node.attr if base else None
        return None

    def ev_attr(self, info, node, scope, depth):
        v = node.value
        if isinstance(v, ast.Name) and (v.id in ("self", "cls") or (v.id in info.classes and
                                                                    self.lookup(info, v.id, scope)[0] is None)):
            b = info.attrs.get(node.attr)
            if b:
                return self.ev_bindings(info, b, (), depth)
            for other in self.helpers_of(info):             # set by a base class from a helper of tests/
                if other.attrs.get(node.attr):
                    return self.ev_bindings(other, other.attrs[node.attr], (), depth)
            return None
        mod = self.module_of(info, v, scope)
        if mod:
            if self.repo_mod(mod + "." + node.attr):
                return OPAQUE
            other = self.info(mod)
            return self.ev_name(other, node.attr, (), depth) if other else OPAQUE
        if self.dotted(info, v, scope):
            return OPAQUE                                    # os.sep, sys.argv, tempfile.tempdir …
        if node.attr == "parent":
            r = self.ev(info, v, scope, depth)
            return r if r in (None, OPAQUE) else [dirname(x) for x in r]
        if node.attr in ("name", "stem", "suffix", "parts"):
            return OPAQUE
        return None

    def ev_for(self, info, it, target, name, scope, depth):
        """The value of loop variable ``name`` (``for name in it``)."""
        elems = self.elements(info, it, scope, depth)
        if elems is None:
            # for p in paths (a parameter): each p — a value of the parameter
            if isinstance(target, ast.Name):
                r = self.ev(info, it, scope, depth)
                return r if r not in (None, OPAQUE) and all(TOK.search(x) for x in r) else (
                    OPAQUE if r == OPAQUE else None)
            return None
        out = []
        for e in elems:
            node = pick(e, target, name)
            out.append(None if node is None else self.ev(info, node, scope, depth))
        return union(out)

    def elements(self, info, it, scope, depth, hops=0):
        """The literal elements of an iterable (tuple/list/set; dict .items/.keys/.values; sorted()/list() of them)."""
        if hops > 3:
            return None
        if isinstance(it, (ast.Tuple, ast.List, ast.Set)):
            return list(it.elts)
        if isinstance(it, ast.Dict):
            return list(it.keys)
        if isinstance(it, ast.Call):
            f = it.func
            if isinstance(f, ast.Attribute) and f.attr in ("items", "keys", "values") and not it.args:
                d = self.literal(info, f.value, scope)
                if isinstance(d, ast.Dict):
                    if f.attr == "items":
                        return [("pair", k, v) for k, v in zip(d.keys, d.values)]
                    return list(d.keys if f.attr == "keys" else d.values)
                return None
            if isinstance(f, ast.Name) and f.id in ("sorted", "list", "tuple", "set", "reversed") and it.args:
                return self.elements(info, it.args[0], scope, depth, hops + 1)
            return None
        if isinstance(it, ast.Name):
            lit = self.literal(info, it, scope)
            return self.elements(info, lit, scope, depth, hops + 1) if lit is not None else None
        return None

    def literal(self, info, node, scope):
        """The literal a name stands for (one binding of it, a tuple/list/dict), else None."""
        for _ in range(3):
            if not isinstance(node, ast.Name):
                return node
            binds, sc = self.lookup(info, node.id, scope)
            if not binds or len(binds) != 1 or binds[0][0] != "expr":
                return None
            node, scope = binds[0][1], sc
        return node if not isinstance(node, ast.Name) else None

    def ev_call(self, info, node, scope, depth):
        f = node.func
        d = self.dotted(info, f, scope)
        args = node.args
        if d in ("os.path.join", "posixpath.join"):
            return self.joined([self.ev_part(info, a, scope, depth) for a in args]) if args else None
        if d in ("pathlib.Path", "pathlib.PurePath", "pathlib.PosixPath", "pathlib.PurePosixPath"):
            return self.joined([self.ev_part(info, a, scope, depth) for a in args]) if args else OPAQUE
        if d == "os.path.dirname" and args:
            r = self.ev(info, args[0], scope, depth)
            return r if r in (None, OPAQUE) else [dirname(x) for x in r]
        if d in ("os.path.abspath", "os.path.realpath", "os.path.normpath", "os.fspath", "str") and args:
            r = self.ev(info, args[0], scope, depth)
            return r if r in (None, OPAQUE) else [norm(x) for x in r]
        if isinstance(f, ast.Attribute) and d is None:
            if f.attr in ("resolve", "absolute", "expanduser") and not args:
                return self.ev(info, f.value, scope, depth)
            if f.attr == "joinpath":
                return self.joined([self.ev_part(info, a, scope, depth) for a in [f.value] + list(args)])
            if f.attr in ("rstrip", "strip") and const_str(f.value) is None:
                return self.ev(info, f.value, scope, depth)
        target = self.function(info, f, scope)
        if target is not None:
            other, defs, method = target
            res = []
            for fd in defs:
                rets = [self.ev(other, r.value, (fd,), depth + 1) for r in returns(fd)]
                if not rets:
                    res.append(OPAQUE)
                    continue
                vals = self.bind_args(other, fd, method, node, info, scope, depth)
                res.append(substitute(union(rets), vals, self.DEEP))
            return union(res)
        return OPAQUE

    def function(self, info, f, scope):
        """The functions of lib/ or tests/ a call goes to: (Info of their module, [FunctionDef], called as a method)."""
        if isinstance(f, ast.Name):
            if self.lookup(info, f.id, scope)[0] is not None:
                return None
            if f.id in info.funcs:
                return info, info.funcs[f.id], False
            imp = info.names.get(f.id)
            if imp and imp[0] == "attr":
                other = self.info(imp[1])
                if other and imp[2] in other.funcs:
                    return other, other.funcs[imp[2]], False
            return None
        if isinstance(f, ast.Attribute):
            v = f.value
            if isinstance(v, ast.Name) and v.id in ("self", "cls"):
                if f.attr in info.funcs:
                    return info, info.funcs[f.attr], True
                for other in self.helpers_of(info):        # a method of a base class from a helper of tests/
                    if f.attr in other.funcs:
                        return other, other.funcs[f.attr], True
                return None
            mod = self.module_of(info, v, scope)
            if mod:
                other = self.info(mod)
                if other and f.attr in other.funcs:
                    return other, other.funcs[f.attr], False
        return None

    def helpers_of(self, info):
        out = []
        for imp in info.names.values():
            m = imp[1] if imp[0] in ("mod", "attr") else None
            if m and m.startswith("tests"):
                o = self.info(m)
                if o is not None and o is not info:
                    out.append(o)
        return out

    def bind_args(self, callee, fd, method, call, info, scope, depth):
        """{parameter of ``fd``: its value at this call} (the defaults of ``fd`` for the ones not given)."""
        a = fd.args
        pos = [p.arg for p in getattr(a, "posonlyargs", []) + a.args]
        if method and callee.method and id(fd) in callee.method and pos and pos[0] in ("self", "cls"):
            pos = pos[1:]
        vals = {}
        defaults = dict(zip([p.arg for p in (getattr(a, "posonlyargs", []) + a.args)][-len(a.defaults):],
                            a.defaults)) if a.defaults else {}
        for p, dflt in zip(a.kwonlyargs, a.kw_defaults):
            if dflt is not None:
                defaults[p.arg] = dflt
        extra = []
        i = 0
        for arg in call.args:
            if isinstance(arg, ast.Starred):
                if a.vararg:
                    extra.append(arg.value)
                elif i < len(pos):
                    vals[pos[i]] = self.ev(info, arg.value, scope, depth)
                    i += 1
                continue
            if i < len(pos):
                vals[pos[i]] = self.ev(info, arg, scope, depth)
                i += 1
            else:
                extra.append(arg)
        if a.vararg:
            vals[a.vararg.arg] = union([self.ev(info, e, scope, depth) for e in extra]) if extra else OPAQUE
            vals[STAR + a.vararg.arg] = product([self.ev(info, e, scope, depth) for e in extra], pjoin) \
                if extra else [""]
        for kw in call.keywords:
            if kw.arg:
                vals[kw.arg] = self.ev(info, kw.value, scope, depth)
        for p, dflt in defaults.items():
            if p not in vals:
                vals[p] = self.ev(callee, dflt, (), depth + 1)
        return vals

    # ── what is gone through ──
    def targets(self, info, call, scope):
        """[(node, strict)]: what this call goes through (strict — a value nobody can tell is a finding)."""
        node = self.scan_target(info, call, scope)
        return [] if node is None else [(node, True)]

    def scan_target(self, info, call, scope):
        """The node naming the folder a call goes through, or None (not a scan)."""
        f = call.func
        d = self.dotted(info, f, scope)
        if d in SCAN_CALLS:
            if call.args:
                return call.args[0]
            for kw in call.keywords:
                if kw.arg in ("top", "path", "pathname"):
                    return kw.value
            return ast.Constant(value=".")
        if isinstance(f, ast.Attribute) and d is None and (f.attr in ("rglob", "iterdir") or (
                f.attr == "glob" and call.args)):
            return f.value
        return None

    def shell_scan(self, node):
        """A command in a list literal that goes through folders: git grep / git ls-files / grep -r / find / rg."""
        words = [const_str(e) for e in node.elts]
        if not words or words[0] is None:
            return False
        w0 = os.path.basename(words[0])
        if w0 == "git" and len(words) > 1 and words[1] in ("grep", "ls-files", "ls-tree"):
            return True
        if w0 in ("grep", "egrep") and any(w and w.startswith("-") and not w.startswith("--") and
                                           ("r" in w or "R" in w) for w in words[1:]):
            return True
        return w0 in ("find", "rg")

    def ev_part(self, info, node, scope, depth):
        """A part of a joined path; «*parts» of the function's own «*parts» — those parts joined, not each of them
        (``def read(*parts): open(os.path.join(ROOT, *parts))`` with read("docs", "k3", "X.md"))."""
        if isinstance(node, ast.Starred) and isinstance(node.value, ast.Name) and scope:
            va = getattr(getattr(scope[-1], "args", None), "vararg", None)
            if va is not None and va.arg == node.value.id:
                return [tok(STAR + va.arg)]
        return self.ev(info, node, scope, depth)

    def joined(self, parts):
        """os.path.join, Path, «/», joinpath of the results of the parts. With DEEP a run-time part after a known start
        (``join(STATIC, icon)``, ``icon`` from the code under test or a JSON fixture) is «anything below the start»: a
        whole component of WILD is «.+» in file_pattern (REV-speed-3 С7), not a read nobody can place."""
        if self.DEEP and parts and parts[0] not in (None, OPAQUE) and OPAQUE in parts[1:]:
            parts = [parts[0]] + [[WILD] if p == OPAQUE else p for p in parts[1:]]
        return product(parts, pjoin)

    def classify(self, info, fd_scope, res, line, strict=True):
        """Where the result of a scan goes: the summary of the function (it depends on its parameters) or the
        folders of the module."""
        if res == OPAQUE:
            return
        alts = [WILD] if res is None else res
        own = self.params(fd_scope[-1]) if fd_scope else set()
        for s in alts:
            names = set(TOK.findall(s))
            if names and fd_scope and names <= own and (info.mod.startswith("tests") or TOK.match(s)):
                self.summ.setdefault(id(fd_scope[-1]), set()).add((s, strict))
                self.owner[id(fd_scope[-1])] = (info, fd_scope[-1])
                continue
            if not info.mod.startswith("tests"):
                continue        # lib: a folder fixed in its code is the code's own business (the catalogs, the
                                # static — rules of test_select.py); only a folder a caller hands over counts
            self.place(info, s, line, strict)

    def call_strict(self, info, call, scope, strict):
        """Is an unknown value at this call of a function that goes through its parameter a finding?"""
        return strict

    def place(self, info, s, line, strict):
        """A folder a test goes through (``s`` — its path as the source says it)."""
        rel = repo_rel(s)
        if rel is None:
            return
        if rel is False:
            self.unresolved.setdefault(info.mod, []).append(line)
            return
        self.dirs.setdefault(info.mod, set()).add(rel)

    @staticmethod
    def params(fd):
        a = fd.args
        out = {p.arg for p in getattr(a, "posonlyargs", []) + a.args + a.kwonlyargs}
        out.update(p.arg for p in (a.vararg, a.kwarg) if p)
        if a.vararg:
            out.add(STAR + a.vararg.arg)
        return out

    def run(self):
        mods = sorted(self.files)
        infos = [self.info(m) for m in mods]
        for info in infos:
            text = self.src.get(info.mod, "") if self.src is not None else read_text(info.file)
            for m in self.MARK.finditer(text):
                words = [x.strip() for x in m.group(1).split(",") if x.strip()]
                self.marks.setdefault(info.mod, set()).update(norm_rel(x) for x in words if not x.startswith("!"))
                self.excludes.setdefault(info.mod, []).extend(x[1:].strip("/") for x in words if x.startswith("!"))
            for call, scope in info.calls:
                for node, strict in self.targets(info, call, scope):
                    self.classify(info, scope, self.ev(info, node, scope), call.lineno, strict)
            if info.mod.startswith("tests") and self.SHELL:
                for lst, scope in info.lists:
                    if self.shell_scan(lst):
                        self.unresolved.setdefault(info.mod, []).append(lst.lineno)
        # the functions that go through what they are given: at every call of them, with the arguments there
        done = set()
        for _ in range(8):
            if not self.summ:
                break
            names = {self.owner[k][1].name for k in self.summ}
            grew = False
            for info in infos:
                for call, scope in info.calls:
                    f = call.func
                    nm = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
                    if nm not in names:
                        continue
                    target = self.function(info, f, scope)
                    if target is None:
                        continue
                    other, defs, method = target
                    for fd in defs:
                        for tpl, strict in sorted(self.summ.get(id(fd), ())):
                            key = (id(call), id(fd), tpl, strict)
                            if key in done:
                                continue
                            done.add(key)
                            vals = self.bind_args(other, fd, method, call, info, scope, 0)
                            before = sum(len(v) for v in self.summ.values())
                            self.classify(info, scope, substitute([tpl], vals, self.DEEP), call.lineno,
                                          self.call_strict(info, call, scope, strict))
                            grew = grew or sum(len(v) for v in self.summ.values()) != before
            if not grew:
                break
        return self

    def folders(self, mod):
        """The folders ``mod`` goes through ("" — all of the repository): its own, the marked ones, and the whole
        repository for a scan nobody could place without a mark."""
        out = set(self.dirs.get(mod, ())) | set(self.marks.get(mod, ()))
        if self.unresolved.get(mod) and mod not in self.marks:
            out.add("")
        return out


    def covers(self, mod, rel):
        """The folder of ``mod`` that holds ``rel`` ("" — all of the repository), None — none (or a «!…» of its mark
        leaves ``rel`` out)."""
        for x in self.excludes.get(mod, ()):
            if any(c in x for c in "*?[") and (fnmatch.fnmatch(rel, x) or fnmatch.fnmatch(os.path.basename(rel), x)):
                return None
            if rel == x or rel.startswith(x + "/"):
                return None
        for f in sorted(self.folders(mod)):
            if f == "" or rel == f or rel.startswith(f + "/"):
                return f
        return None


FILES_MARK_RE = re.compile(r"^[ \t]*#[ \t]*k3-select:[ \t]*files=([^\s—]+)", re.M)
# the calls that read a file by its path: the index of the argument naming it
READ_CALLS = {"open": 0, "io.open": 0, "codecs.open": 0, "builtins.open": 0, "runpy.run_path": 0,
              "importlib.util.spec_from_file_location": 1, "importlib.machinery.SourceFileLoader": 1}
# the calls that may take a file of the repository among other things (a copy, a command): a path found counts, an
# unknown value does not (it is mostly not a path)
LOOSE_CALLS = {"shutil.copy", "shutil.copy2", "shutil.copyfile", "shutil.copytree", "subprocess.run",
               "subprocess.Popen", "subprocess.call", "subprocess.check_call", "subprocess.check_output"}


class Reads(Scan):
    """The files of the repository a test (or a helper of tests/) reads by a path built from parts:
    ``read("give-i18n-%s.js" % lang)`` with ``def read(name): open(os.path.join(STATIC, name))``,
    ``open(CAT % lang)``, f-strings, ``+``, a helper's function given the name (REV-speed-2 С5). The same reading of
    the source as for the folders; the result is a pattern of the path. A path nobody can tell from the source
    (``open(os.path.join(DIR, name))`` with ``name`` from outside) — a line ``# k3-select: files=<pattern>,…``
    (fnmatch, from the root of the repository) or the module goes for any change."""
    MARK = FILES_MARK_RE
    SHELL = False
    DEEP = True

    def __init__(self, base):
        Scan.__init__(self, base.files, base.trees, base.src)
        self.infos = base.infos      # the memo is its own: with DEEP a joined path has another value (С7)
        self.pats = {}           # tests module -> {regex: the path as text (* — a part nobody knows)}

    def targets(self, info, call, scope):
        f, args = call.func, call.args
        d = self.dotted(info, f, scope)
        if d in READ_CALLS:
            i = READ_CALLS[d]
            node = args[i] if len(args) > i else next(
                (k.value for k in call.keywords if k.arg in ("file", "path", "location", "filename")), None)
            if node is None or isinstance(node, ast.Starred):
                return []
            mode = args[1] if len(args) > 1 and i == 0 else next((k.value for k in call.keywords if k.arg == "mode"),
                                                                  None)
            if d != "runpy.run_path" and mode is not None and set(const_str(mode) or "r") & set("wax"):
                return []                                  # written, not read
            return [(node, self.strict(info, [node], scope))]
        if d in LOOSE_CALLS and args and not isinstance(args[0], ast.Starred):
            return [(args[0], False)]
        if isinstance(f, ast.Attribute) and d is None:
            if f.attr in ("read_text", "read_bytes"):
                return [(f.value, True)]
            if f.attr == "open" and (not args or const_str(args[0]) is not None):
                return [(f.value, False)]                  # Path(…).open("r"); .open of anything else — not a path
        return []

    def strict(self, info, nodes, scope):
        """Is a path nobody can tell from these nodes a finding? Not when it comes from a walk of a folder (for d, _,
        fs in os.walk(X): open(join(d, f)) — that folder's rule) or from a value of the run time (an element of a
        list — argv of a fake command, the result of a function, a module's __file__)."""
        return any(self.pathlike(info, n, scope) for n in nodes) and not any(self.walked(info, n, scope)
                                                                              for n in nodes)

    def call_strict(self, info, call, scope, strict):
        return strict and self.strict(info, list(call.args) + [k.value for k in call.keywords], scope)

    def pathlike(self, info, node, scope, hops=0):
        if hops > 3:
            return False
        if isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            return True
        if isinstance(node, ast.Call):
            d = self.dotted(info, node.func, scope)
            return d in ("os.path.join", "posixpath.join", "os.path.abspath", "os.path.realpath", "os.path.normpath",
                         "os.path.dirname", "pathlib.Path", "pathlib.PurePath", "pathlib.PosixPath") or (
                isinstance(node.func, ast.Attribute) and node.func.attr in ("joinpath", "format"))
        if isinstance(node, ast.Attribute):
            return node.attr != "__file__"
        if isinstance(node, ast.Name):
            binds, sc = self.lookup(info, node.id, scope)
            for b in binds or ():
                if b[0] == "param" or b[0] == "for" or (
                        b[0] == "expr" and self.pathlike(info, b[1], b[2] if len(b) > 2 else sc, hops + 1)):
                    return True
            return False
        return False

    def walked(self, info, node, scope, hops=0):
        """Does ``node`` use a name that a walk of a folder gives (a loop over os.walk/listdir/glob, or over a name
        bound to one)?"""
        for n in ast.walk(node):
            if not isinstance(n, ast.Name) or hops > 3:
                continue
            binds, sc = self.lookup(info, n.id, scope)
            for b in binds or ():
                src = b[1] if b[0] in ("for", "expr") else None
                if src is None:
                    continue
                for c in ast.walk(src):
                    if isinstance(c, ast.Call) and self.scan_target(info, c, sc) is not None:
                        return True
                if self.walked(info, src, sc, hops + 1):
                    return True
        return False

    def place(self, info, s, line, strict):
        pat = file_pattern(s)
        if pat is None:
            return
        if pat is False:
            if strict:
                self.unresolved.setdefault(info.mod, []).append(line)
            return
        self.pats.setdefault(info.mod, {})[pat[0]] = pat[1]

    def folders(self, mod):
        raise NotImplementedError

    def core_runs(self):
        """{tests module: {kaskad module, maybe not a module (a name of one)}} — the core a test (or a helper) runs in a process of its own: «-m
        kaskad.mcp», [sys.executable, ".../lib/kaskad/x.py"], the code of «-c» or a script it writes (its imports).
        What runs there is not in the map of the test's own process (REV-speed-2 З5)."""
        out = {}
        mod_re = re.compile(r"^kaskad(\.\w+)*$")
        for mod in sorted(self.files):
            info = self.info(mod) if mod.startswith("tests") else None
            tree = self.trees.get(mod)
            if info is None or tree is None:
                continue
            found = set()
            for lst, scope in info.lists:
                words = [const_str(e) for e in lst.elts]
                for i in range(len(words) - 1):
                    if words[i] == "-m" and words[i + 1] and mod_re.match(words[i + 1]):
                        found.update((words[i + 1], words[i + 1] + ".__main__"))
                if lst.elts and self.is_python(info, lst.elts[0], scope):
                    for e in lst.elts[1:]:
                        r = self.ev(info, e, scope)
                        for v in (r if isinstance(r, list) else ()):
                            m = re.search(r"(?:^|/)lib/(kaskad/[\w/]+)\.py$", v)
                            if m:
                                found.add(m.group(1).replace("/", ".").replace(".__init__", "").replace(
                                    ".__main__", ""))
            for node in ast.walk(tree):
                v = const_str(node)
                if not v or "kaskad" not in v:
                    continue
                for m in re.finditer(r"(?:^|[\s\"'])-m\s+(kaskad(?:\.\w+)*)", v):
                    found.update((m.group(1), m.group(1) + ".__main__"))
                for m in re.finditer(r"^[ \t]*(?:from[ \t]+(kaskad(?:\.\w+)*)[ \t]+import[ \t]+([\w, \t]+)|"
                                     r"import[ \t]+(kaskad(?:\.\w+)*))", v, re.M):
                    if m.group(1):
                        found.add(m.group(1))
                        found.update(m.group(1) + "." + n.strip() for n in m.group(2).split(",") if n.strip())
                    else:
                        found.add(m.group(3))
            if found:
                out[mod] = found
        return out

    def is_python(self, info, node, scope):
        d = self.dotted(info, node, scope)
        if d == "sys.executable":
            return True
        v = const_str(node)
        return bool(v) and os.path.basename(v).startswith("python")

    def covers(self, mod, rel):
        """What of ``mod`` names ``rel`` (the pattern as text; "" — a read nobody could place, so anything), None —
        nothing."""
        for x in self.excludes.get(mod, ()):
            if fnmatch.fnmatch(rel, x) or rel == x or rel.startswith(x + "/"):
                return None
        for rx, text in sorted(self.pats.get(mod, {}).items()):
            if re.search(rx, rel):
                return text
        for g in sorted(self.marks.get(mod, ())):
            if g == "" or fnmatch.fnmatch(rel, g) or rel.startswith(g + "/"):
                return g or "."
        if self.unresolved.get(mod) and mod not in self.marks:
            return ""
        return None


def file_pattern(s):
    """(regex, text) of a path of the repository a read names; None — outside the repository; False — nobody can
    tell which file (no word of it is known)."""
    s = TOK.sub(WILD, s or WILD)
    if s.startswith("/"):
        if s != ROOT and not s.startswith(ROOT + "/"):
            return None
        s = s[len(ROOT) + 1:]
    while s.startswith("./"):
        s = s[2:]
    comps = [c for c in s.split("/") if c not in ("", ".")]
    out = []
    for c in comps:
        if c == "..":
            if not out or out[-1] == WILD:
                return None if not out else False
            out.pop()
        else:
            out.append(c)
    wild = re.compile("[%s*?]+" % WILD)
    if not wild.sub("", "".join(out)).strip("."):
        return False
    lead = 0
    while lead < len(out) and not wild.sub("", out[lead]):
        lead += 1                               # the start nobody knows: the path is matched by its end
    rx = []
    for c in out[lead:]:
        rx.append(".+" if not wild.sub("", c) else "".join(
            "[^/]*" if wild.fullmatch(p) else re.escape(p) for p in re.split("([%s*?]+)" % WILD, c) if p))
    text = "/".join(out).replace(WILD, "*")
    return ("(?:^|/)" if lead else "^") + "/".join(rx) + "(?:/|$)", text


# a name put together in the text of any source (Python, JS, shell): "give-i18n-%s.js", f"egress.{lang}.sh",
# `setup-i18n-${lang}.js`, "$lang.json" — a quoted string with a placeholder whose last part is a file name with an
# extension and a word besides it, or in a folder with a word ("apps/%s.png")
_QUOTED = re.compile(r'"([^"\n\\]*)"|\'([^\'\n\\]*)\'|`([^`\\]*)`')
_HOLE = re.compile(r"%\([A-Za-z_]\w*\)[-#0-9.]*[sdrif]|%[-#0-9.]*[sdrif]|\$?\{[^{}\s]*\}|\$[A-Za-z_]\w*|[*?]")
_EXT = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,5}$")


def text_templates(text):
    """{regex: the name as text} of the file names a source puts together from parts (REV-speed-2 С5) — a net
    under Reads for what it does not follow (JS, shell, a call it does not know)."""
    out = {}
    for m in _QUOTED.finditer(text or ""):
        s = next(g for g in m.groups() if g is not None)
        if not s or len(s) > 200 or any(c.isspace() for c in s) or "://" in s or not _HOLE.search(s):
            continue
        t = _HOLE.sub(WILD, s)
        last = t.rsplit("/", 1)[-1]
        if WILD not in t or not _EXT.search(last) or WILD in _EXT.search(last).group(0):
            continue
        if not _EXT.sub("", last).replace(WILD, "").strip(".-_") and not any(
                c.replace(WILD, "").strip(".-_") for c in t.split("/")[:-1]):
            continue                            # «*.json», «%s.sh»: no word of the name nor of a folder — the folder
                                                # rules; «apps/%s.png» — by its folder (REV-speed-3 С7)
        pat = file_pattern(t if t.startswith("/") else WILD + "/" + t)    # where it lies — unknown: by its end
        if pat:
            out[pat[0]] = pat[1]
    return out


def returns(fd):
    out = []
    stack = list(fd.body)
    while stack:
        n = stack.pop()
        if isinstance(n, ast.Return) and n.value is not None:
            out.append(n)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(n))
    return out


def pick(elem, target, name):
    """The part of one loop element that ``name`` gets (for k, v in …)."""
    if isinstance(target, ast.Name):
        return None if isinstance(elem, tuple) else elem
    if isinstance(target, (ast.Tuple, ast.List)):
        parts = list(elem[1:]) if isinstance(elem, tuple) else (
            elem.elts if isinstance(elem, (ast.Tuple, ast.List)) else None)
        if parts is None or len(parts) != len(target.elts):
            return None
        for t, p in zip(target.elts, parts):
            if isinstance(t, ast.Name) and t.id == name:
                return p
            if isinstance(t, (ast.Tuple, ast.List)) and name in names_in(t):
                return pick(p, t, name)
    return None


def substitute(res, vals, deep=False):
    """A result with the parameter tokens of a function — with the values given at the call. ``deep``: a run-time
    value given for a whole part of the path after a known start — anything below the start (Scan.joined, С7)."""
    if res in (None, OPAQUE):
        return res
    out = []
    for s in res:
        names = TOK.findall(s)
        if not names:
            out.append(s)
            continue
        pieces = TOK.split(s)            # text, name, text, name, …
        parts = []
        for i, p in enumerate(pieces):
            if i % 2 == 0:
                parts.append([p])
            else:
                v = vals.get(p, None)
                if v == OPAQUE and deep and i > 1 and "".join(pieces[:i]).strip("/") and pieces[i - 1].endswith("/") \
                        and (i + 1 == len(pieces) or pieces[i + 1] == "" or pieces[i + 1].startswith("/")):
                    v = [WILD]
                parts.append(v)
        r = product(parts, "".join)
        if r == OPAQUE:
            continue
        out.extend(r)
    return out[:MAX_ALTS] if out else OPAQUE


def fmt_percent(fmt, vals):
    it = iter(vals)
    return re.sub(r"%[-0-9.]*[sdrif]", lambda m: next(it, WILD), fmt)


def norm(s):
    if s.startswith("/"):
        return os.path.normpath(s)
    return s


def norm_rel(x):
    x = x.strip().strip("/")
    return "" if x in (".", "") else os.path.normpath(x)


def repo_rel(s):
    """The folder of the repository a scanned path stands for: «lib/kaskad», "" (all of it); None — outside the
    repository; False — nobody can tell (an unknown start)."""
    if not s or s.startswith(WILD) or TOK.match(s):
        return False
    if s.startswith("/"):
        if s == ROOT:
            return ""
        if not s.startswith(ROOT + "/"):
            return None
        rel = s[len(ROOT) + 1:]
    else:
        rel = s[2:] if s.startswith("./") else ("" if s == "." else s)
    cut = [i for i in (rel.find(c) for c in (WILD, "*", "?", "[", "\x01")) if i >= 0]
    if cut:
        rel = dirname(rel[:min(cut)])
    rel = os.path.normpath(rel) if rel else ""
    if rel.startswith(".."):
        return None
    return "" if rel == "." else rel


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""

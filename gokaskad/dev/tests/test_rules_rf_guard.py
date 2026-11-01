"""R2 — the guard of the second line (docs/k3/SPEC-stage1-rf-mark-builtin.md §3.5, §9 «Рубеж 2»): a HINT on obvious
mistakes, not the proof. Since B7 круг 2 (D-136) the proof is behavioural — ``tests/gw/test_ru_invariant.py`` draws
hundreds of random states through the real renderers and checks that no device goes directly to Russia without the
permission ⚠РФ — and the last line at run time (``xray.ru_guard``, ``gw.ruguard``). Ways round this guard exist and are
not chased (a short substring, a name built at run time, the default read past ``rules_conf``, a rule «direct» drawn
past ``rules.apply``): the invariant and the last line catch what they do.

«Россия напрямую» applies only through ``rules.apply.profile`` / ``rules.apply.ru_ok`` (the lists AND the consent
⚠РФ). Counted, each per function, with its NUMBER (one more in a listed function is red too):

* the profile names: ``ru_direct`` and ``all_exit`` (with two final profiles «not all_exit» IS «ru_direct»); a piece
  of one of 6+ characters with «_» («ru_dir», «_direct», «all_ex»); a sum of string constants that gives one
  (``"ru_" + "direct"``); a string holding one as a word («rules:ru_direct»); anything bound to one — a constant of a
  module (``PROFILE_RU``, ``rfgate.RU``) and its aliases, by ``=``, ``:=``, a tuple unpacking, ``for``, a parameter by
  its default, an attribute of a class, ``getattr`` with a constant name, ``str()``, an f-string, a holder of one of
  them only, an item picked from a holder of both (``P[0]``, ``max(P)``, ``ROUTE.get(p)``);
* COMPARE — ``==``/``!=``/``in``/``not in``/ordering/``match`` with one of the above, a substring test with such a
  piece, ``operator.eq``, ``RU.__eq__(p)``; a holder of BOTH profiles on the right of ``in`` (``p in RULE_PROFILES``)
  checks a known value — not counted;
* USE — every other reference to a profile name in code (docstrings and bare strings do not count);
* ASKED — ``profile_of`` called or taken, and ``rules_conf(st)["default"]`` / ``.get("default")``.

Not counted on purpose (no false alarm on ordinary code, REV-s1-b7 придирка 5): words shorter than 6 characters or
without «_» — ``"direct" in outboundTag``, ``kind.startswith("exit")``, ``k.startswith("all_")``, ``"_exit"``.

A place that is not listed (or listed with fewer) makes this test red until it is listed below with the reason; a
listed place that does less makes it red too: the lists say only what is true. Parsed with ``ast`` — comments do not count, only code."""

import ast
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad")
RU = "ru_direct"
ALL = "all_exit"
PROFILE_OF = "profile_of"
RULES_CONF = "rules_conf"
_HOLDERS = ("frozenset", "set", "tuple", "list", "dict", "sorted")

# (file of lib/kaskad, function) -> (how many comparisons, why it may compare with ru_direct)
COMPARE_OK = {
    ("rules/apply.py", "profile"): (1, "the one place: what a device asks for -> what applies (lists + consent)"),
    ("rules/apply.py", "ru_ok"): (1, "reads «applied» of profile()"),
    ("rules/rfgate.py", "_points"): (2, "where ru_direct is CHOSEN — the check of a write (рубеж 1), not the action"),
    ("rules/ops.py", "status"): (1, "counts devices by «applied» of rules.apply.profile"),
    ("rules/ops.py", "summary"): (1, "reads «default_applied» of status() (rules.apply.profile)"),
    ("gw/trace.py", "trace"): (3, "reads «applied» of rules.apply.profile: the lists of Russia at a fallback «block», "
                                  "the fail step of Russia, the note «Россия напрямую» (texts of the mode need — R5)"),
    ("gw/trace.py", "_match_note"): (1, "reads «applied» of rules.apply.profile, passed by trace()"),
    ("panel/scheme.py", "build"): (5, "the card of each profile (what it means, how many chose it) — the chosen "
                                      "profile; the mark «не действует: нет подтверждения ⚠РФ» — R4; T15 Q6: the "
                                      "line «Сайты и сети России» -> Russian group is drawn when the card of "
                                      "«Россия напрямую» is on the scheme (whose choice acts — the lines ru_via, "
                                      "exits.ru_target over rules.apply)"),
    ("panel/scheme.py", "_applied_profile"): (1, "the chosen profile -> all_exit unless rules.apply.ru_ok"),
}

# (file, function) -> (how many reads, why it may read the profile a device ASKS for)
ASKED_OK = {
    ("rules/apply.py", "profile"): (1, "the one reader of model.profile_of for the action"),
    ("gw/cli.py", "scheme_doc"): (1, "the scheme of the MCP agent shows the chosen profile, nothing is routed by it "
                                     "(«applied» there — R7)"),
    ("panel/scheme.py", "build"): (1, "the scheme shows the chosen profile (its own profile_of; the mark need — R4)"),
    ("panel/scheme.py", "profile_of"): (1, "the scheme's own profile_of: model.profile_of looked up by name"),
    ("panel/scheme.py", "_inherited_rules_text"): (1, "the text «what a removed own profile becomes» names the "
                                                      "chosen one"),
    ("panel/scheme.py", "_applied_profile"): (1, "the chosen profile, then rules.apply.ru_ok"),
    # B7 (REV-s1-b6-sec Н-3): the default of the node read past rules.apply.profile
    ("backup.py", "collect"): (1, "the copy keeps the default as it acts (no decision; restored through the state)"),
    ("gw/model.py", "profile_of"): (1, "the definer: a device without its own -> the default of the node"),
    ("gw/model.py", "set_rules_conf"): (1, "the write of the default (рубеж 1 checks it in rules.ops)"),
    ("panel/scheme.py", "default_profile"): (1, "the scheme shows the chosen default (applied — _applied_profile)"),
    ("rules/ops.py", "rules_ver"): (1, "the version of the lists follows the default (no routing by it)"),
    ("rules/ops.py", "status"): (1, "the field «default» of the answer (the chosen one; «applied» — profile())"),
    ("rules/rfgate.py", "_points"): (1, "where ru_direct is CHOSEN — the check of a write (рубеж 1)"),
    ("setup/steps.py", "_rules_view"): (1, "the item «Россия напрямую» of the wizard shows the chosen default; on — "
                                           "only with rf.mode full (R7), nothing is routed by it"),
}

# (file, function) -> (how many, why the string ru_direct may stand there outside a comparison)
USE_OK = {
    ("gw/model.py", "<module>"): (5, "the profiles known (RULE_PROFILES, DEFAULT_PROFILES: two names each) and the "
                                     "default of 3.3.x (RULES_DEFAULTS) — read through rules_conf / rules.apply.profile"),
    ("rules/rfgate.py", "<module>"): (1, "RU — the constant of the check of a write (рубеж 1)"),
    ("panel/scheme.py", "<module>"): (4, "PROFILE_RU, PROFILE_ALL and PROFILE_TITLES of them — the cards and the texts "
                                         "of the scheme"),
    ("gw/trace.py", "<module>"): (2, "_PROFILES — the names of the profiles for the text"),
    ("rules/cli.py", "<module>"): (2, "PROFILES — the names of the profiles for the text"),
    ("rules/cli.py", "status_lines"): (1, "the count «ru_direct» of status() (by «applied»)"),
    ("rules/ops.py", "status"): (1, "the field «ru_direct» of the answer — the count by «applied»"),
    ("gw/hooks.py", "hook"): (3, "the field «ru_direct» of the answer of the hook = rules.apply.device_rd (ru_ok)"),
    ("setup/steps.py", "s_rules"): (4, "the key of the request body and the value for rules.ops.set_default "
                                       "(рубеж 1: ru_direct or all_exit)"),
    # B7 (REV-s1-b6 №2, -sec Н-3): all_exit and the references to the constants count too
    ("backup.py", "_apply_gateway"): (1, "a copy of 3.3.x without its default gets RULES_DEFAULTS written (the default "
                                         "of 3.3.x); the consent ⚠РФ is checked by rules.apply.profile as on any node"),
    ("gw/model.py", "parse_profile"): (2, "the check of a value written: RULE_PROFILES / DEFAULT_PROFILES allowed"),
    ("gw/model.py", "pin_default"): (1, "a new node writes all_exit (the safe default)"),
    ("gw/model.py", "rules_conf"): (2, "the effective conf: RULES_DEFAULTS, all_exit on a new node"),
    ("gw/trace.py", "__getattr__"): (1, "PROFILE_TITLES: the titles of _PROFILES"),
    ("gw/trace.py", "chain"): (6, "the ids of the scheme for a profile not given: «rules:all_exit» (texts only)"),
    ("panel/scheme.py", "_applied_profile"): (1, "all_exit unless rules.apply.ru_ok"),
    ("panel/scheme.py", "build"): (3, "the cards: [PROFILE_ALL] without the model's support; T15 Q6: the id "
                                      "«rules:ru_direct» of the card as the source of the lines ru_via and ru_link"),
    ("panel/scheme.py", "default_profile"): (2, "all_exit without the model's support or on an error"),
    ("panel/scheme.py", "profile_names"): (2, "the cards in order when the model names none"),
    ("panel/scheme.py", "profile_of"): (2, "all_exit without the model's support"),
    ("panel/scheme.py", "profile_title"): (1, "PROFILE_TITLES — the title of a card"),
    ("rules/apply.py", "profile"): (1, "the one place: all_exit when ru_direct is not allowed (the lists, ⚠РФ)"),
    ("rules/cli.py", "register"): (2, "choices of argparse: DEFAULT_PROFILES / RULE_PROFILES (the check of a value)"),
}


def _parse_all(root=ROOT):
    trees = {}
    for dp, _, fns in os.walk(root):
        for fn in sorted(fns):
            if fn.endswith(".py"):
                p = os.path.join(dp, fn)
                with open(p, encoding="utf-8") as f:
                    trees[os.path.relpath(p, root).replace(os.sep, "/")] = ast.parse(f.read(), p)
    return trees


def _base(modname):
    return (modname or "").rsplit(".", 1)[-1]


def _modname(rel):
    return _base(rel[:-3].replace("/", "."))


def _ru_str(v):
    """The string ru_direct or a long piece of it («ru_dir», «_direct»)."""
    return isinstance(v, str) and (v == RU or (_long_piece(v) and (RU.startswith(v) or RU.endswith(v))))


def _all_str(v):
    """B7: the other final profile — all_exit or a long piece of it («all_ex», «l_exit»). With two final profiles,
    «not all_exit» IS «ru_direct» (REV-s1-b6-sec Н-3: the profile told by what it is not)."""
    return isinstance(v, str) and (v == ALL or (_long_piece(v) and (ALL.startswith(v) or ALL.endswith(v))))


def _long_piece(v):
    """A piece that says a profile, not an ordinary word: 6+ characters with «_» (придирка 5 REV-s1-b7)."""
    return len(v) >= 6 and "_" in v


_NAMED = re.compile(r"(?<![A-Za-z0-9_.])(%s|%s)(?![A-Za-z0-9_])" % (RU, ALL))


def _names_profile(v):
    """A string that holds a profile as a word of its own (``"rules:ru_direct"``, ``"cat:all_exit:ru"``) — not a part
    of another name (``gw_all_exit``, ``all_exits``) nor the tail of a catalog key (``rules.profile.ru_direct``)."""
    return isinstance(v, str) and bool(_NAMED.search(v))


def _kind_of_str(v):
    return "ru" if _ru_str(v) else ("all" if _all_str(v) else None)


def _piece(v):
    """A string a substring test may tell a profile by: holds a profile, or is a long piece of one («ru_dir»,
    «_direct», «l_exit»); «direct», «exit», «all_» are ordinary words (придирка 5 REV-s1-b7)."""
    if not isinstance(v, str):
        return False
    return _kind_of_str(v) is not None or _names_profile(v) or (_long_piece(v) and (v in RU or v in ALL))


def _fold(n):
    """The value of a sum of string constants (``"ru_" + "direct"``), else None."""
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return n.value
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
        a, b = _fold(n.left), _fold(n.right)
        return a + b if a is not None and b is not None else None
    return None


def _join(ks):
    """The kind of a holder or of a choice between values: "mixed" — it holds BOTH final profiles (a check of a known
    value — ``p in RULE_PROFILES`` — tells nothing of Russia); "ru" / "all" — it holds one of them only (``p in
    ("ru_direct", "x")`` IS ``p == "ru_direct"``, ``p in ("all_exit", "inherit")`` IS ``p != "ru_direct"``)."""
    ks = set(k for k in ks if k)
    if not ks:
        return None
    r = bool(ks & {"ru", "mixed"})
    a = bool(ks & {"all", "mixed"})
    return "mixed" if r and a else ("ru" if r else "all")


def _bare_strings(tree):
    """ids of the Constant nodes of docstrings and bare string statements — not code."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            out.add(id(n.value))
    return out


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_RANK = {None: 0, "mixed": 1, "all": 2, "ru": 3}
_SUBSTR = ("startswith", "endswith", "find", "rfind", "index", "rindex", "count", "partition", "rpartition", "split",
           "rsplit", "replace", "removeprefix", "removesuffix", "__contains__", "__eq__", "__ne__")
_REGEX = ("match", "search", "fullmatch", "findall", "finditer", "sub", "subn", "split", "compile")
_ORDER = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _own(scope):
    """The nodes of scope itself — not of the functions and classes inside it."""
    todo = list(ast.iter_child_nodes(scope))
    while todo:
        n = todo.pop()
        yield n
        if not isinstance(n, _SCOPES):
            todo.extend(ast.iter_child_nodes(n))


def _imports(tree, consts):
    """(alias -> module basename, {name: kind} imported from a module of consts, aliases of profile_of, aliases of
    rules_conf)."""
    mods, names, asked, confs = {}, {}, set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name in consts:                        # from . import rfgate (as x)
                    mods[a.asname or a.name] = a.name
                elif a.name in consts.get(_base(node.module), {}):
                    names[a.asname or a.name] = consts[_base(node.module)][a.name]    # from .rfgate import RU as X
                if a.name == PROFILE_OF:
                    asked.add(a.asname or a.name)
                if a.name == RULES_CONF:
                    confs.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if _base(a.name) in consts:
                    mods[a.asname or _base(a.name)] = _base(a.name)
    return mods, names, asked, confs


class _Kinds(object):
    """What an expression is: "ru" — the profile ru_direct (the string, a piece of it, a name or a constant bound to it,
    an item of a holder of it, a holder of it without all_exit); "all" — the same of all_exit; "mixed" — a holder of
    both (``p in PROFILES`` is a check of a known value; ``PROFILES[1]``, ``ROUTE.get(p)`` pick one); None — neither.
    ``attrs`` — every name a module binds to a kind: ``<anything>.RU`` is the constant even when its module is not
    known here (``importlib.import_module(…).RU``)."""

    def __init__(self, consts, mods, classattrs, attrs=None):
        self.consts, self.mods, self.classattrs = consts, mods, classattrs
        self.attrs = attrs or {}
        self.chain = []                                     # [{name: kind}] — the scopes, the innermost last

    def name(self, n):
        for d in reversed(self.chain):
            if n in d:
                return d[n]
        return None

    def top_name(self, n):
        """The kind of ``n`` when it is a name of the module (a constant, an import of one), not a local one."""
        for d in reversed(self.chain[1:]):
            if n in d:
                return None
        return self.chain[0].get(n) if self.chain else None

    def of(self, n):
        if isinstance(n, ast.Constant):
            return _kind_of_str(n.value)
        if isinstance(n, ast.Name):
            return self.name(n.id)
        if isinstance(n, ast.Attribute):
            if isinstance(n.value, ast.Name):
                mod = self.mods.get(n.value.id)
                if mod is not None and n.attr in self.consts.get(mod, {}):
                    return self.consts[mod][n.attr]
                return self.classattrs.get((n.value.id, n.attr))     # a known name: its module says
            if isinstance(n.value, ast.Attribute) and n.value.attr in self.consts:
                return self.consts[n.value.attr].get(n.attr)          # kaskad.rules.rfgate.RU
            return self.attrs.get(n.attr)          # importlib.import_module(…).RU: by the name of the constant
        if isinstance(n, (ast.Tuple, ast.List, ast.Set)):
            return _join(self.of(e) for e in n.elts)
        if isinstance(n, ast.Dict):
            return _join(self.of(e) for e in list(n.keys) + list(n.values) if e is not None)
        if isinstance(n, ast.Subscript):
            k = self.of(n.value)
            return "ru" if k in ("ru", "mixed") else k
        if isinstance(n, ast.Starred):
            return self.of(n.value)
        if isinstance(n, ast.JoinedStr):                     # f"{rfgate.RU}" — the profile; f"x:{p}" — a new string
            return self._every(v.value if isinstance(v, ast.FormattedValue) else v for v in n.values)
        if isinstance(n, ast.BinOp):
            folded = _fold(n)
            if folded is not None:                          # "ru_" + "direct" IS the name; "on" + "_exit" is not
                return _kind_of_str(folded)
            ks = (self.of(n.left), self.of(n.right))
            if all(ks):                                     # "ru_" + "direct", PROFILES + EXTRA
                k = _join(ks)
                return "ru" if k == "mixed" and not any(isinstance(x, (ast.Tuple, ast.List)) or
                                                        self.of(x) == "mixed" for x in (n.left, n.right)) else k
            if "mixed" in ks:
                return "mixed"                               # a holder grown
            told = [k for k in ks if k in ("ru", "all")]
            return told[0] if told else None                 # "ru_" + x builds it; "rules:" + p is a new string
        if isinstance(n, ast.BoolOp):                        # a value of each branch is needed: the constant of a
            return self._every(n.values)                     # branch is a place of its own anyway
        if isinstance(n, ast.IfExp):
            return self._every((n.body, n.orelse))
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id == "getattr" and len(n.args) >= 2 and \
                    isinstance(n.args[0], ast.Name) and isinstance(n.args[1], ast.Constant):
                k = self.consts.get(self.mods.get(n.args[0].id, ""), {}).get(n.args[1].value)
                return k or self.attrs.get(n.args[1].value)
            if isinstance(f, ast.Name) and f.id in ("max", "min", "next") and n.args:
                k = _join(self.of(a) for a in n.args)       # one picked: max(DEFAULT_PROFILES) IS a profile
                return "ru" if k == "mixed" else k
            if isinstance(f, ast.Name) and f.id in _HOLDERS + ("str", "iter", "reversed", "zip", "enumerate", "map",
                                                               "filter") and n.args:
                return _join(self.of(a) for a in n.args)
            if isinstance(f, ast.Attribute) and f.attr in ("get", "pop", "setdefault") and self.of(f.value):
                k = self.of(f.value)
                return "ru" if k in ("ru", "mixed") else k
            if isinstance(f, ast.Attribute) and f.attr in ("copy", "format", "lower", "upper", "strip", "join"):
                return _join([self.of(f.value)] + [self.of(a) for a in n.args])
        return None

    def _every(self, nodes):
        ks = [self.of(x) for x in nodes]
        return _join(ks) if ks and all(ks) else None


def _targets(node):
    ts = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [x.id for t in ts for x in ast.walk(t) if isinstance(x, ast.Name)]


def _pairs(target, value, kinds):
    """(name, kind) a target takes from a value: by position when both are a tuple / list of one length (``R, A =
    rfgate.RU, "x"``), else every name of the target the kind of the whole value."""
    if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) \
            and len(target.elts) == len(value.elts) and not any(isinstance(e, ast.Starred) for e in target.elts):
        out = []
        for t, v in zip(target.elts, value.elts):
            out.extend(_pairs(t, v, kinds))
        return out
    k = kinds.of(value) if value is not None else None
    return [(x.id, k) for x in ast.walk(target) if isinstance(x, ast.Name)] if k else []


def _item_kind(k):
    """The kind of an item of something of kind ``k``: ``for R in (rfgate.RU,)`` — R is ru; of a holder of both — each
    profile in turn (the cards of the scheme): no kind, the holder taken is the place (a USE)."""
    return None if k == "mixed" else k


def _bind(scope, kinds):
    """{name: kind} of the names scope itself binds to something tied to a profile — by ``=``, ``:=``, ``+=``, an
    annotation, a tuple unpacking (by position), the target of ``for`` and of a comprehension, a parameter by its
    default (REV-s1-b6 №2) — until nothing more is found."""
    own = {}
    kinds.chain.append(own)
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        a = scope.args
        pos = list(getattr(a, "posonlyargs", [])) + list(a.args)
        defaults = [(x.arg, d) for x, d in zip(pos[len(pos) - len(a.defaults):], a.defaults)]
        defaults += [(x.arg, d) for x, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None]
        kinds.chain.pop()                                   # defaults are of the scope around the function
        for name, d in defaults:
            k = kinds.of(d)
            if k:
                own[name] = k
        kinds.chain.append(own)
    while True:
        n0 = dict(own)
        for node in _own(scope):
            rows = []
            if isinstance(node, ast.Assign) and node.value is not None:
                for t in node.targets:
                    rows += _pairs(t, node.value, kinds)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and node.value is not None:
                rows += _pairs(node.target, node.value, kinds)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                k = _item_kind(kinds.of(node.iter))
                if k:
                    rows += [(x.id, k) for x in ast.walk(node.target) if isinstance(x, ast.Name)]
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                rows += _pairs(node.optional_vars, node.context_expr, kinds)
            for t, k in rows:
                if _RANK[k] > _RANK[own.get(t)]:
                    own[t] = k
        if own == n0:
            break
    kinds.chain.pop()
    return own


class _Finder(ast.NodeVisitor):
    """The places of one module. consts — {module basename: {name: kind}} bound at the top of the modules."""

    def __init__(self, rel, tree, consts):
        self.rel = rel
        mods, imported, self.asked_names, self.conf_names = _imports(tree, consts)
        attrs = {}
        for d in consts.values():
            for k, v in d.items():
                attrs[k] = _join((attrs.get(k), v))
        classattrs = {}
        self.kinds = _Kinds(consts, mods, classattrs, attrs)
        top = dict(imported)
        top.update(consts.get(_modname(rel), {}))
        self.kinds.chain.append(top)
        for cls in [c for c in ast.walk(tree) if isinstance(c, ast.ClassDef)]:
            for k, v in _bind(cls, self.kinds).items():
                classattrs[(cls.name, k)] = v
        top.update(_bind(tree, self.kinds))
        self.bare = _bare_strings(tree)
        self.fn = []
        self.confs = [set()]                                # names bound to a call of rules_conf, per scope
        self.compares, self.asked, self.uses = [], [], []
        self._done = set()                                  # nodes told already (a comparison, a check of a value)
        self.visit(tree)

    def kind(self, n):
        return self.kinds.of(n)

    def told(self, n):
        """A profile told by ``n``: one of them, not a holder of both."""
        return self.kind(n) in ("ru", "all")

    def where(self):
        return ".".join(self.fn) or "<module>"

    def _confs_of(self, node):
        out = set()
        for n in _own(node):
            if isinstance(n, ast.Assign) and self._is_conf_call(n.value):
                out.update(_targets(n))
        return out

    def _is_conf_call(self, n):
        if not isinstance(n, ast.Call):
            return False
        f = n.func
        return (isinstance(f, ast.Attribute) and f.attr == RULES_CONF) or \
            (isinstance(f, ast.Name) and (f.id == RULES_CONF or f.id in self.conf_names))

    def _scope(self, node):
        self.kinds.chain.append(_bind(node, self.kinds))
        self.confs.append(self._confs_of(node))
        self.fn.append(node.name)
        self.generic_visit(node)
        self.fn.pop()
        self.confs.pop()
        self.kinds.chain.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

    def visit_Lambda(self, node):
        self.kinds.chain.append(_bind(node, self.kinds))
        self.generic_visit(node)
        self.kinds.chain.pop()

    def visit_Module(self, node):
        self.confs = [self._confs_of(node)]
        self.generic_visit(node)

    def _compare(self, node, line):
        self.compares.append((self.where(), line))
        self._done.update(id(x) for x in ast.walk(node))

    def visit_Compare(self, node):
        if id(node) in self._done:
            return self.generic_visit(node)
        sides = [node.left] + list(node.comparators)
        subst = any(isinstance(op, (ast.In, ast.NotIn)) and isinstance(a, ast.Constant) and _piece(a.value)
                    and not isinstance(b, (ast.Tuple, ast.List, ast.Set, ast.Dict))
                    for op, a, b in zip(node.ops, sides, sides[1:]))      # "direct" in p (REV-s1-b6-sec Н-3)
        named = any(isinstance(x, ast.Constant) and _names_profile(x.value) for x in sides)   # nid == "rules:ru_direct"
        if subst or named or any(self.told(x) for x in sides):
            self._compare(node, node.lineno)
        elif all(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            for x in node.comparators:                     # p in RULE_PROFILES: a check of a known value
                if self.kind(x) == "mixed":
                    self._done.update(id(y) for y in ast.walk(x))
        self.generic_visit(node)

    def visit_MatchValue(self, node):                       # match p: case "ru_direct": (Python 3.10+)
        if self.kind(node.value):
            self._compare(node, node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node):
        f = node.func
        if id(node) not in self._done and isinstance(f, ast.Attribute):
            args = list(node.args)
            if f.attr in _SUBSTR and (any(self.kind(a) or isinstance(a, ast.Constant) and _piece(a.value)
                                          for a in args) or self.told(f.value)):
                self._compare(node, node.lineno)          # p.endswith("direct"), rfgate.RU.__eq__(p)
            elif f.attr in _REGEX and args and isinstance(args[0], ast.Constant) and _piece(args[0].value):
                self._compare(node, node.lineno)          # re.search("direct", p)
            elif f.attr in ("eq", "ne", "contains", "is_", "is_not") and any(self.kind(a) for a in args):
                self._compare(node, node.lineno)          # operator.eq(p, rfgate.RU)
        if self._is_default_read(node):
            self.asked.append((self.where(), node.lineno))
        self.generic_visit(node)

    def visit_Subscript(self, node):
        if self._is_default_read(node):
            self.asked.append((self.where(), node.lineno))
        self.generic_visit(node)

    def _is_default_read(self, node):
        """``rules_conf(st)["default"]``, ``.get("default")``, of the call or of a name bound to it in this scope — the
        default profile read past ``rules.apply.profile`` (REV-s1-b6-sec Н-3)."""
        if isinstance(node, ast.Subscript):
            base, key = node.value, node.slice
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "pop") \
                and node.args:
            base, key = node.func.value, node.args[0]
        else:
            return False
        if getattr(key, "value", None) != "default" or not isinstance(key, ast.Constant):
            return False
        return self._is_conf_call(base) or (isinstance(base, ast.Name) and base.id in self.confs[-1])

    def _use(self, node):
        if id(node) not in self._done:
            self.uses.append((self.where(), node.lineno))
            self._done.update(id(x) for x in ast.walk(node))

    def visit_Constant(self, node):
        if id(node) in self.bare:
            return
        if node.value == PROFILE_OF:                        # getattr(model, "profile_of"), _model_fn("profile_of")
            self.asked.append((self.where(), node.lineno))
        elif (_kind_of_str(node.value) or _names_profile(node.value)) and id(node) not in self._done:
            self._use(node)                                 # "ru_direct", "rules:all_exit"

    def visit_Name(self, node):
        if node.id in self.asked_names or node.id == PROFILE_OF:
            self.asked.append((self.where(), node.lineno))
        elif isinstance(node.ctx, ast.Load) and self.kinds.top_name(node.id):
            # a constant of the module or an import of one — any reference (REV-s1-b6 №2: return it, a default of a
            # parameter, a key of a lookup…); a local alias was a place where it was bound, its comparisons count
            self._use(node)

    def visit_Attribute(self, node):
        if node.attr == PROFILE_OF:                         # model.profile_of(…) or model.profile_of taken
            self.asked.append((self.where(), node.lineno))
        elif isinstance(node.ctx, ast.Load) and self.kind(node) and id(node) not in self._done:
            self._use(node)                                 # rfgate.RU, scheme.PROFILE_RU, <module>.RU
            return
        self.generic_visit(node)


def _module_names(rel, tree, consts):
    """{name: kind} bound at the top of a module to anything tied to a profile (after the consts known so far)."""
    mods, imported, _, _ = _imports(tree, consts)
    kinds = _Kinds(consts, mods, {})
    top = dict(imported)
    top.update(consts.get(_modname(rel), {}))
    kinds.chain.append(top)
    out = {}
    for st in tree.body:
        rows = []
        if isinstance(st, ast.Assign) and st.value is not None:
            for t in st.targets:
                rows += _pairs(t, st.value, kinds)
        elif isinstance(st, ast.AnnAssign) and st.value is not None:
            rows += _pairs(st.target, st.value, kinds)
        for t, k in rows:
            out[t] = top[t] = k
    return out


def scan(trees):
    """-> ({(file, qualified function): [lines]} of comparisons, the same of the reads of profile_of, the same of the
    other uses of the string ru_direct)."""
    consts = {}
    for _ in range(4):                                      # aliases across modules: X = rfgate.RU, then Y = mod.X
        before = sum(len(v) for v in consts.values())
        for rel, tree in trees.items():
            c = _module_names(rel, tree, consts)
            if c:
                consts.setdefault(_modname(rel), {}).update(c)
        if sum(len(v) for v in consts.values()) == before:
            break
    cmp_, asked, uses = {}, {}, {}
    for rel, tree in trees.items():
        f = _Finder(rel, tree, consts)
        for out, rows in ((cmp_, f.compares), (asked, f.asked), (uses, f.uses)):
            for fn, line in rows:
                out.setdefault((rel, fn), []).append(line)
    return cmp_, asked, uses


def _counts(found):
    return dict((k, len(v)) for k, v in found.items())


def _wanted(listed):
    return dict((k, v[0]) for k, v in listed.items())


class GuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cmp, cls.asked, cls.uses = scan(_parse_all())

    def check(self, found, listed, what):
        got, want = _counts(found), _wanted(listed)
        more = dict((k, (n, want.get(k, 0))) for k, n in got.items() if n > want.get(k, 0))
        self.assertEqual(more, {}, "%s past rules.apply.profile/ru_ok (place: (found, listed)) — read "
                                   "profile(...)['applied'] or ru_ok(...), or list it with the number and the reason"
                         % what)

    def test_every_comparison_with_ru_direct_is_listed(self):
        self.check(self.cmp, COMPARE_OK, "a comparison with ru_direct")

    def test_every_read_of_the_asked_profile_is_listed(self):
        self.check(self.asked, ASKED_OK, "a read of profile_of")

    def test_every_other_use_of_the_string_is_listed(self):
        self.check(self.uses, USE_OK, "the string ru_direct")

    def test_the_lists_hold_only_true_places(self):
        for found, listed in ((self.cmp, COMPARE_OK), (self.asked, ASKED_OK), (self.uses, USE_OK)):
            got = _counts(found)
            self.assertEqual(sorted((k, n, got.get(k, 0)) for k, n in _wanted(listed).items() if got.get(k, 0) < n), [])

    def test_the_readers_of_the_action_go_through_ru_ok(self):
        """rd (gw_rd, gw_ruonly), device_rd (the hook), kernel_clients (dnsmasq), xray_wanted, _list_rules (xray_rules,
        ``ls-``), user_chain — no comparison of their own, each calls ru_ok."""
        with open(os.path.join(ROOT, "rules", "apply.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        fns = dict((n.name, n) for n in tree.body if isinstance(n, ast.FunctionDef))
        for name in ("rd", "device_rd", "kernel_clients", "xray_wanted", "_list_rules", "user_chain"):
            calls = [c.func.id for c in ast.walk(fns[name]) if isinstance(c, ast.Call)
                     and isinstance(c.func, ast.Name)]
            self.assertIn("ru_ok", calls, name)
            self.assertNotIn(("rules/apply.py", name), self.cmp, name)

    BASE = {"rules/rfgate.py": 'RU = "ru_direct"\n',
            "panel/scheme.py": 'PROFILE_RU = "ru_direct"\nPROFILE_ALL = "all_exit"\n',
            "gw/model.py": 'RULE_PROFILES = ("inherit", "all_exit", "ru_direct")\n'
                           'DEFAULT_PROFILES = ("all_exit", "ru_direct")\n',
            "gw/inbounds/vless_lists.py": 'RU = ("yandex.ru",)\n'}

    def planted(self, src):
        trees = dict((k, ast.parse(v)) for k, v in self.BASE.items())
        trees["gw/new.py"] = ast.parse(src)
        return [dict((k[1], len(v)) for k, v in found.items() if k[0] == "gw/new.py") for found in scan(trees)]

    def test_a_planted_place_is_caught(self):
        """The guard catches ``!=``, ``==``, ``in`` with the string, PROFILE_RU and rfgate.RU — and the call."""
        cmp_, asked, _ = self.planted(
            "from ..rules import rfgate\nfrom ..panel.scheme import PROFILE_RU as P\n"
            "def a(p):\n    return p != 'ru_direct'\n"
            "def b(p):\n    return p == P\n"
            "def c(p):\n    return p in ('all_exit', rfgate.RU) and p in (rfgate.RU,)\n"
            "def d(st, x):\n    return model.profile_of(st, x)\n"
            "def e(p):\n    return p == 'gw_ru'\n")
        self.assertEqual(cmp_, {"a": 1, "b": 1, "c": 1})                    # a holder of all profiles is no compare
        self.assertEqual(asked, {"d": 1})
        try:
            m = ast.parse("def f(p):\n    match p:\n        case 'ru_direct':\n            return 1\n")
        except SyntaxError:                                     # Python < 3.10: no match
            return
        self.assertIn(("gw/m.py", "f"), scan({"gw/m.py": m})[0])

    def test_the_ways_round_are_caught(self):
        """REV-s1-r2 1: aliases of the import and of the constant, getattr, the string in a dict / tuple / set, a class
        attribute, startswith, a sum of pieces, a local alias."""
        cases = {
            "dict_key": "ROUTE = {'ru_direct': 'direct', 'all_exit': 'exit'}\ndef f(p):\n    return ROUTE[p] == 'x'\n",
            "getattr_const": "from ..rules import rfgate\ndef f(p):\n    return p == getattr(rfgate, 'RU')\n",
            "local_alias": "def f(p):\n    R = 'ru_direct'\n    return p == R\n",
            "module_alias": "from ..rules import rfgate\nX = rfgate.RU\ndef f(p):\n    return p == X\n",
            "alias_profile_of": "from ..gw.model import profile_of as po\ndef f(st, d):\n    return po(st, d)\n",
            "getattr_profile_of": "from ..gw import model\ndef f(st, d):\n    return getattr(model, 'profile_of')(st, d)\n",
            "taken_profile_of": "from ..gw import model\ndef f(st, d):\n    g = model.profile_of\n    return g(st, d)\n",
            "startswith": "def f(p):\n    return p.startswith('ru_dir')\n",
            "tuple_index": "P = ('ru_direct', 'all_exit')\ndef f(p):\n    return p == P[0]\n",
            "concat": "def f(p):\n    return p == 'ru_' + 'direct'\n",
            "class_attr": "class C:\n    RU = 'ru_direct'\ndef f(p):\n    return p == C.RU\n",
            "frozenset": "RUS = frozenset({'ru_direct'})\ndef f(p):\n    return p in RUS\n",
            "of_a_value": "def f(d):\n    return d['rules'] == 'ru_direct'\n",
            "chain": "from ..rules import rfgate\nX = rfgate.RU\nY = (X,)\ndef f(p):\n    return p in Y\n",
        }
        for name, src in cases.items():
            cmp_, asked, uses = self.planted(src)
            self.assertEqual((asked if "profile_of" in name else cmp_).get("f"), 1, name)
        cmp_, _, uses = self.planted(cases["dict_key"])
        self.assertEqual(uses, {"<module>": 2})                              # the keys themselves — uses to list
        self.assertEqual(self.planted(cases["taken_profile_of"])[1], {"f": 1})
        # one more comparison in a listed function is red: the number counts, not the function
        cmp_, _, _ = self.planted("def f(p):\n    return p == 'ru_direct' or p == 'ru_direct'\n")
        self.assertEqual(cmp_, {"f": 2})
        # a function of the same name elsewhere is not the listed one; a local alias stays in its function
        cmp_, _, _ = self.planted("def f(p):\n    R = 'ru_direct'\n    return 1\ndef g(R):\n    return R == 'x'\n")
        self.assertEqual(cmp_, {})


    def test_the_obvious_forms_are_caught(self):
        """B7 (REV-s1-b6 №2, REV-s1-b6-sec Н-3): the obvious forms that name a profile or read the asked value are a
        place (COMPARE, USE or ASKED of the function ``f``). Not «the class»: D-136 — the invariant proves it."""
        imp = "from ..rules import rfgate\nfrom ..gw import model\n"
        cases = {
            # the ways of REV-s1-b6 №2
            "default_arg": (imp + "def f(p, R=rfgate.RU):\n    return p == R\n", "cmp"),
            "for_target": (imp + "def f(p):\n    for R in (rfgate.RU,):\n        return p == R\n", "cmp"),
            "tuple_unpack": (imp + "def f(p):\n    R, A = rfgate.RU, 'all_exit'\n    return p == R\n", "cmp"),
            "dict_lookup": (imp + "ROUTE = {rfgate.RU: 'direct'}\ndef f(p):\n    return ROUTE.get(p, 'exit')\n", "use"),
            "fn_returns": (imp + "def f():\n    return rfgate.RU\n", "use"),
            "str_call": (imp + "def f(p):\n    return p == str(rfgate.RU)\n", "cmp"),
            "fstring": (imp + "def f(p):\n    return p == f'{rfgate.RU}'\n", "cmp"),
            "operator_eq": ("import operator\n" + imp + "def f(p):\n    return operator.eq(p, rfgate.RU)\n", "cmp"),
            "dunder_eq": (imp + "def f(p):\n    return rfgate.RU.__eq__(p)\n", "cmp"),
            "import_module": ("import importlib\ndef f(p):\n"
                              "    return p == importlib.import_module('kaskad.rules.rfgate').RU\n", "cmp"),
            "comprehension": (imp + "def f(ps):\n    return [x for x in ps if x == rfgate.RU]\n", "cmp"),
            "walrus": (imp + "def f(p):\n    return (r := rfgate.RU) and p == r\n", "cmp"),
            "with_alias": (imp + "def f(p):\n    for x in [rfgate.RU]:\n        y = x\n    return p == y\n", "cmp"),
            # the ways of REV-s1-b6-sec Н-3 (long pieces; short ones — ordinary words, see below)
            "substring_in": ("def f(p):\n    return '_direct' in p\n", "cmp"),
            "endswith": ("def f(p):\n    return p.endswith('_direct')\n", "cmp"),
            "find": ("def f(p):\n    return p.find('ru_dir') >= 0\n", "cmp"),
            "regex": ("import re\ndef f(p):\n    return re.search('ru_direct', p)\n", "cmp"),
            "not_all_exit": ("def f(p):\n    return p != 'all_exit'\n", "cmp"),
            "not_in_others": ("def f(p):\n    return p not in ('all_exit', 'inherit')\n", "cmp"),
            "in_with_stranger": ("def f(p):\n    return p in ('ru_direct', 'zzz')\n", "cmp"),
            "max_of_profiles": (imp + "def f(p):\n    return p == max(model.DEFAULT_PROFILES)\n", "cmp"),
            "zip_of_profiles": (imp + "def f(p):\n"
                                "    return dict(zip(model.DEFAULT_PROFILES, (False, True))).get(p)\n", "use"),
            "ordering": ("def f(p):\n    return p > 'all_exit'\n", "cmp"),
            "named_in_string": ("def f(nid):\n    return nid == 'rules:ru_direct'\n", "cmp"),
            "conf_default": (imp + "def f(st):\n    return model.rules_conf(st)['default']\n", "asked"),
            "conf_get": (imp + "def f(st):\n    c = model.rules_conf(st)\n    return c.get('default')\n", "asked"),
            "conf_alias": ("from ..gw.model import rules_conf as rc\ndef f(st):\n    return rc(st)['default']\n",
                           "asked"),
            "planted_or": (imp + "def f(d, st):\n    p = d.get('rules') or model.rules_conf(st)['default']\n"
                           "    return '_direct' in p\n", "cmp+asked"),
        }
        for name, (src, want) in cases.items():
            if name == "walrus" and not hasattr(ast, "NamedExpr"):
                continue
            cmp_, asked, uses = self.planted(src)
            got = {"cmp": cmp_, "asked": asked, "use": uses}
            for part in want.split("+"):
                self.assertGreaterEqual(got[part].get("f", 0), 1, (name, part, cmp_, asked, uses))

    def test_no_place_where_no_profile_is_named(self):
        """The class is not wider than the profiles: other words, other names, other keys are no places."""
        cases = {
            "fail_step": "def f(s):\n    return s == 'direct' or s in ('direct', 'block')\n",
            "a_route": "def f(r):\n    return r.get('outboundTag') == 'direct'\n",
            "another_ru": "from ..gw.inbounds import vless_lists\ndef f(h):\n    return h in vless_lists.RU\n",
            "words_with_it": "def f(k):\n    return k == 'gw_all_exit' or k.startswith('all_exits')\n",
            "catalog_key": "def f(t):\n    return t('rules.profile.ru_direct')\n",
            "a_check_of_a_value": "from ..gw import model\ndef f(p):\n    return p in model.RULE_PROFILES\n",
            "a_new_string": "from ..gw import model\ndef f(p):\n    for x in model.RULE_PROFILES:\n"
                            "        y = 'rules:' + x\n    return y == p\n",
            "routing_rules": "def f(cfg):\n    return [r for r in cfg['routing']['rules'] if r.get('user')]\n",
            # REV-s1-b7 придирка 5 (F1–F7): ordinary words of Kaskad
            "event_kind": "def f(ev):\n    return ev['kind'].startswith('exit')\n",
            "outbound_tag": "def f(r):\n    return 'direct' in r['outboundTag']\n",
            "name_tail": "def f(name):\n    return name.endswith('_exit')\n",
            "key_head": "def f(k):\n    return k.startswith('all_')\n",
            "split": "def f(s):\n    return s.split('direct')\n",
            "short_piece": "def f(s):\n    return 'rect' in s\n",
            "a_sum_of_another": "X = 'on' + '_exit'\ndef f(k):\n    return k == X\n",
        }
        for name, src in cases.items():
            cmp_, asked, uses = self.planted(src)
            if name == "a_new_string":                       # the holder iterated is the one place: a use
                self.assertEqual((cmp_, asked, uses), ({}, {}, {"f": 1}), name)
                continue
            self.assertEqual((cmp_, asked, uses), ({}, {}, {}), name)


if __name__ == "__main__":
    unittest.main()

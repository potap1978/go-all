r"""The registry of the ⚠РФ mark (docs/k3/SPEC-stage1-rf-mark-builtin.md §2): organisations recognised as extremist
in the Russian Federation (Meta without WhatsApp, FBK) and their domains — the data file ``rf_mark.json`` next to
this module (shared with stage 2: iplist, own lists, rules). stdlib only.

    load(path=None) -> Registry   reads and checks the file; a broken file is a KaskadError (RU/EN), never an empty
                                  registry: a silent «nothing is marked» is worse than an error
    default()                     the registry of the package, loaded once (reload() drops it: tests)
    org_of(name)                  org id of a DNS name: suffix match over ``domains``, ``exclude`` is stronger
    org_of_entry(type, value)     org id of a geosite entry: ``domain:``/``full:`` as org_of; ``keyword:`` — a
                                  substring of the name in xray, so the entry is marked when the word (at least
                                  KEYWORD_MIN characters) lies inside a domain of the registry (``facebook`` in
                                  ``facebook.com``), ends with one on a label boundary (``meta.ai``; ``telegram.me``
                                  does not «contain» ``m.me``) or, after a dot, begins one (``m.facebook``,
                                  ``www.instagram``: the part after the dot — KEYWORD_MIN characters at least);
                                  ``regexp:`` — see regexp_entry. A domain under ``exclude`` never marks anything
    regexp_entry(expr)            (org id or None, why unchecked or None). Marked when the literal tail of the
                                  expression — the text every name it matches ENDS with (``$``/``\z`` after it or
                                  the end of the expression: what matches with ``$`` matches without it too —
                                  ``^graph\.instagram\.com``; escapes taken off, small alternatives and ``?``
                                  expanded: ``^edge\.(a|b)\.com$``) — ends with a domain of the registry on a label
                                  boundary, in the case RE2 reads it (``(?i)`` — any case; a letter of upper case
                                  outside a class finds nothing: ``^www\.FACEBOOK\.com$``, ``^.*WWW\.x\.fb\.com$``,
                                  ``^A.*\.fb\.com$``): the whole tail when ``^`` or a «.»
                                  stands before it, else without its first label, which may go on to the left
                                  (``\.fbcdn\.net$``, ``^(.+\.)?x\.fbcdn\.net$``); a tail under an ``exclude``
                                  does not mark by itself — the expression goes on to the checks and the run below
                                  (no trial name lies under an exclude); or when it finds a trial name:
                                  ``<domain>``, ``x.``, ``www.``, ``m.<domain>``. The distinctive words (``meta``,
                                  ``threads``) are for ``keyword:`` only: ``^meta\.ua$``, ``^facebook\.com\.example\.org$``
                                  are not marked. xray runs RE2 (linear time), Python a backtracking engine, so an
                                  expression is refused before any run when it is longer than REGEXP_MAX or nested
                                  deeper than REGEXP_DEPTH_MAX groups («long»), when the two
                                  engines read it differently («syntax»: ``\p{..}``, ``\C``, ``\Q..\E``, ``\Z``,
                                  ``\u``/``\U``/``\N{..}`` and every escape RE2 has not, back references,
                                  look-arounds, atomic / possessive, POSIX ``[[:alpha:]]``, flags other than ``ims``,
                                  ``(?flags)`` not at the start, ``{,n}``, a repeat count over 1000 — alone or nested
                                  as RE2 multiplies them, a count with a leading zero ``a{01}`` — a literal in RE2)
                                  or when its shape backtracks («dangerous»: a variable repeat inside a repeat of more
                                  than one pass — ``(.*){20}``, ``(a+)+``, ``(?:.?){30}``, but not a repeat of one
                                  class followed by a character it cannot match, ``([a-z0-9-]+\.)+`` —
                                  an alternative under one whose branches do not start with distinct literals, more
                                  than two variable items in a row — ``.*.*.*``). ``\z`` (RE2: end of text) is run as
                                  Python ``\Z``. What is left runs in a separate process: REGEXP_TIMEOUT_S seconds
                                  per expression, then the process is killed («timeout»). An unchecked expression is
                                  never a silent «not marked»: scan counts it. Without ``runner`` every call starts
                                  its own process and has no budget: for many expressions call scan or keep one
                                  _Runner (stage 2, own lists)
    scan(cats, stats=None)        {group: [(code, type, value, org)]} — the marked entries of the built-in groups
                                  (force, direct, block); ``cats`` is the result of ``dat.geosite``; ``stats`` gets
                                  «entries», «unknown» (entries of a type this module does not know, also in
                                  «entries»), «regexp», «regexp_unchecked» and «regexp_unchecked_by» {reason: n}
                                  (reasons: long, syntax, dangerous, timeout, budget — REGEXP_BUDGET_S seconds of
                                  runs in the worker for the whole scan, its start included; what needs no run — a
                                  literal mark, long, syntax, dangerous — is checked after the budget too). The worst
                                  case is about WORKER_START_S + REGEXP_BUDGET_S + REGEXP_TIMEOUT_S (15.5 s), a
                                  real list — hundredths of a second. scan starts a process: call it when the lists
                                  change and keep the result — never on every request of the panel
    names(org_ids, lang)          «Meta, ФБК» — the names for the block
    block_text(org_ids, lang), consent_text(lang)  the texts of the block and of the check box (i18n rules.rf.*)

The functions of the module but load, default, reload and consent_text take ``reg=`` (a Registry); without it —
default(). The same methods are on Registry.
"""

import datetime
import json
import os
import re
import select
import subprocess
import sys
import threading
import time
import warnings

from .. import errors, util
from ..i18n import get_lang, tr
from . import lists

try:                                     # the parser of the re module: re._parser on 3.11+, sre_parse before
    from re import _parser as _sre_parse
except ImportError:                      # pragma: no cover — Python 3.8-3.10
    import sre_parse as _sre_parse

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rf_mark.json")
SCHEMA = 1
KEYWORD_MIN = 4          # «cdn», «me» would mark everything
REGEXP_MAX = 512
REGEXP_DEPTH_MAX = 32    # nested groups: deeper is «long» — the checks recurse, and a RecursionError is no answer
TAILS_MAX = 64           # literal tails of one expression: more alternatives are not expanded
TAILS_WAYS_MAX = 256     # ways through the alternatives that end an expression: more are not read
TAILS_WORK_MAX = 8192    # texts built for the literal tails of one expression: then the reading stops
REGEXP_BUDGET_S = 5.0    # one scan: no run in the worker after this many seconds of runs (counted in stats)
REGEXP_TIMEOUT_S = 0.5   # one expression in the worker process; then it is killed
WORKER_START_S = 10.0    # the worker answers «ready» within this time or every expression is «timeout»
RE2_REPEAT_MAX = 1000    # RE2: a count over 1000, alone or as a product of nested counts, does not compile
UNCHECKED = ("long", "syntax", "dangerous", "timeout", "budget")
TRIAL_PREFIXES = ("", "x.", "www.", "m.")
GROUPS = (("force", lists.FORCE), ("direct", lists.DIRECT), ("block", lists.BLOCK_ADS + lists.BLOCK_TORRENT))
LANGS = ("ru", "en")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")           # fromisoformat of 3.11+ also takes «20261015», «2026-W42-4»
_POSIX_CLASS = re.compile(r"\[:\^?[A-Za-z]+:\]")
_INLINE_FLAGS = re.compile(r"\(\?([A-Za-z-]*)([:)])")
_ESCAPES = frozenset("afnrtvx0dDsSwWbBAz")          # RE2 and Python read these the same (\z -> \Z); else: syntax
_ESCAPES_IN_CLASS = frozenset("afnrtvx0dDsSwW")
_DOMAIN_TEXT = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+")
_COUNT = re.compile(r"\{(\d+)(?:,(\d*))?\}")
_IGNORECASE = int(re.IGNORECASE)
_BEGIN_AT = ("AT_BEGINNING", "AT_BEGINNING_STRING")
_END_AT = ("AT_END", "AT_END_STRING")
_REPEATS = ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT")
_TYPES = {"domain": "domain", "full": "full", "keyword": "keyword", "plain": "keyword", "regexp": "regexp",
          "regex": "regexp"}

_lock = threading.Lock()
_default = [None]


_bad_said = set()


def _bad(path, error):
    """The error of a broken registry: the text for a person has no path and no technical words (REV-338 П-2) — the
    path and the error go to the journal, once per process for the same pair, and to ``data``."""
    if (path, error) not in _bad_said:
        _bad_said.add((path, error))
        util.log_action("RULES rf-registry: %s is broken (%s)" % (path, error))
    return errors.KaskadError(tr("rules.rf.bad"), data={"path": path, "error": error})


def _date(v):
    if not isinstance(v, str) or not _DATE.match(v):
        return False
    try:
        datetime.date.fromisoformat(v)
    except ValueError:
        return False
    return True


def _text2(v):
    return isinstance(v, dict) and all(isinstance(v.get(lang), str) and v[lang].strip() for lang in LANGS)


def _domains(v, where):
    if not isinstance(v, list):
        raise ValueError("%s: not a list" % where)
    out = []
    for i, d in enumerate(v):
        n = lists.norm_domain(d) if isinstance(d, str) else None
        if n is None or n != d or "." not in n:
            raise ValueError("%s[%d]: not a domain: %r" % (where, i, d))
        out.append(n)
    if len(set(out)) != len(out):
        raise ValueError("%s: a domain twice" % where)
    return out


def _check(doc):
    """Raises ValueError (a technical text, the same in both languages) when the document is not a registry."""
    if not isinstance(doc, dict):
        raise ValueError("not a JSON object")
    # sources[].checked — only for a page that was really opened; a source may have none
    if doc.get("schema") != SCHEMA:
        raise ValueError("schema %r, expected %d" % (doc.get("schema"), SCHEMA))
    for k in ("version", "checked"):
        if not _date(doc.get(k)):
            raise ValueError("%s: not a date YYYY-MM-DD" % k)
    srcs = doc.get("sources")
    if not isinstance(srcs, list) or not srcs:
        raise ValueError("sources: empty or not a list")
    src_ids = set()
    for i, s in enumerate(srcs):
        where = "sources[%d]" % i
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not _ID.match(s["id"]):
            raise ValueError("%s.id: bad" % where)
        if s["id"] in src_ids:
            raise ValueError("%s.id: %s twice" % (where, s["id"]))
        if not _text2(s.get("title")):
            raise ValueError("%s.title: needs ru and en" % where)
        if not (isinstance(s.get("url"), str) and s["url"].startswith(("https://", "http://"))):
            raise ValueError("%s.url: bad" % where)
        if "checked" in s and not _date(s["checked"]):
            raise ValueError("%s.checked: not a date" % where)
        src_ids.add(s["id"])
    orgs = doc.get("orgs")
    if not isinstance(orgs, list) or not orgs:
        raise ValueError("orgs: empty or not a list")
    ids = set()
    for i, o in enumerate(orgs):
        where = "orgs[%d]" % i
        if not isinstance(o, dict) or not isinstance(o.get("id"), str) or not _ID.match(o["id"]):
            raise ValueError("%s.id: bad" % where)
        if o["id"] in ids:
            raise ValueError("%s.id: %s twice" % (where, o["id"]))
        ids.add(o["id"])
        if not _text2(o.get("names")):
            raise ValueError("%s.names: needs ru and en" % where)
        if not (isinstance(o.get("status"), str) and o["status"]):
            raise ValueError("%s.status: bad" % where)
        if not _date(o.get("since")):
            raise ValueError("%s.since: not a date" % where)
        if o.get("source") not in src_ids:
            raise ValueError("%s.source: unknown %r" % (where, o.get("source")))
        doms = _domains(o.get("domains"), where + ".domains")
        excl = _domains(o.get("exclude", []), where + ".exclude")
        both = set(doms) & set(excl)
        if both:
            raise ValueError("%s: %s both in domains and exclude" % (where, sorted(both)[0]))
        if not isinstance(o.get("iplist", []), list):
            raise ValueError("%s.iplist: not a list" % where)
        also = o.get("also", [])
        if not isinstance(also, list):
            raise ValueError("%s.also: not a list" % where)
        for j, a in enumerate(also):
            if not (isinstance(a, dict) and isinstance(a.get("status"), str) and _date(a.get("since"))
                    and a.get("source", o["source"]) in src_ids):
                raise ValueError("%s.also[%d]: bad" % (where, j))


def _under(name, suffixes):
    """The longest of ``suffixes`` that ``name`` equals or lies under; '' when none."""
    best = ""
    for s in suffixes:
        if (name == s or name.endswith("." + s)) and len(s) > len(best):
            best = s
    return best


class Registry:
    """A checked registry. ``orgs`` — the organisations in the order of the file."""

    def __init__(self, doc, path=None):
        _check(doc)
        self.doc = doc
        self.path = path
        self.version = doc["version"]
        self.checked = doc["checked"]
        self.orgs = list(doc["orgs"])
        self.by_id = {o["id"]: o for o in self.orgs}
        self.own = [(o["id"], d) for o in self.orgs for d in o["domains"] if self.org_of(d) == o["id"]]
        # regexp, the literal tail: a name marks only when its last label is the last label of a domain of the
        # registry (``_under``: a suffix on a label boundary) — a tail that can no longer get one is not read on
        # (``_tail_hope``): the worst parse without a run stays within milliseconds (D-099)
        self.tlds = frozenset(d.rsplit(".", 1)[-1] for _, d in self.own)
        self.tld_ends = frozenset(t[i:] for t in self.tlds for i in range(len(t)))
        # … and once a dot of every tail has after it at least the longest domain (or exclude) of the registry, what
        # lies to the left changes no organisation: the tail is not read further (``before`` keeps the rest)
        self.suffix_max = max([len(d) for o in self.orgs for d in list(o["domains"]) + list(o.get("exclude") or ())]
                              or [0])
        # regexp: the trial names (an excluded one is not tried)
        self.trials = [(oid, p + d) for oid, d in self.own for p in TRIAL_PREFIXES if self.org_of(p + d) == oid]

    # ── names ──────────────────────────────────────────────────────────────
    def org_of(self, name):
        """org id of a DNS name or None. Several organisations — the longest matching domain wins; a name under
        an ``exclude`` of an organisation is never that organisation's."""
        n = lists.norm_domain(name)
        if n is None:
            return None
        best, best_len = None, 0
        for o in self.orgs:
            if _under(n, o.get("exclude", ())):
                continue
            hit = _under(n, o["domains"])
            if len(hit) > best_len:
                best, best_len = o["id"], len(hit)
        return best

    def excluded(self, name):
        """True when ``name`` lies under an ``exclude`` of some organisation (WhatsApp)."""
        n = lists.norm_domain(name)
        return bool(n) and any(_under(n, o.get("exclude", ())) for o in self.orgs)

    def _keyword(self, word):
        k = str(word or "").strip().lower()
        if len(k) < KEYWORD_MIN:
            return None
        # the word ends with a domain of the registry on a label boundary: «meta.ai», «www.facebook.com»
        labels = k.split(".")
        for j in range(len(labels)):
            cand = ".".join(labels[j:])
            if "." in cand and lists.norm_domain(cand) == cand:
                org = self.org_of(cand)           # the longest plain name decides
                if org or self.excluded(cand):
                    return org                    # «whatsapp.com» stays unmarked
                break
        # the word lies inside a domain of the registry: «facebook» in facebook.com («whatsapp» is in no domain:
        # the domains under an exclude are not in self.own)
        for oid, d in self.own:
            if k in d:
                return oid
        # after a dot the word begins a domain of the registry: «m.facebook» -> m.facebook.com, «www.instagram»
        for j in range(1, len(labels)):
            tail = ".".join(labels[j:])
            if len(tail) >= KEYWORD_MIN:
                for oid, d in self.own:
                    if d.startswith(tail):
                        return oid
        return None

    def _literal_org(self, tree):
        """org id when a literal tail of the expression ends with a domain of the registry on a label boundary (see
        the module); None otherwise. Nothing is run."""
        icase = bool(tree.state.flags & _IGNORECASE)
        for tail, whole, before, ic in _tails(list(tree), icase, self._tail_hope, self._tail_enough):
            if tail != tail.lower():
                continue                         # upper case (the first label too): RE2 finds no lower-case name
            labels = tail.split(".")
            if not whole:
                labels = labels[1:]              # «x» + «facebook.com»: the first label may go on to the left
            name = ".".join(labels).lstrip(".")
            if not _DOMAIN_TEXT.fullmatch(name):
                continue
            org = self.org_of(name)              # as a domain: under an exclude (whatsapp.com) — None
            if org and not _upper_literal(before, ic):
                return org                       # «^A.*\.facebook\.com$» finds no lower-case name either
        return None

    def _tail_hope(self, tail):
        """A literal tail (read from the right) may still end a name of the registry: its last label is one of the
        registry (a dot before it) or the end of one (no dot yet). Upper case is no hope either (RE2 finds no
        lower-case name; ``_literal_org`` skips such a tail)."""
        if not tail:
            return True
        if "." in tail:
            return tail.rsplit(".", 1)[-1] in self.tlds
        return tail in self.tld_ends

    def _tail_enough(self, tail):
        """After the first dot of the tail lies at least the longest domain of the registry: reading on to the left
        changes no organisation that ``org_of`` gives."""
        p = tail.find(".")
        return p >= 0 and len(tail) - p - 1 >= self.suffix_max

    def regexp_entry(self, expr, runner=None):
        """(org id or None, None) for a checked expression; (None, reason) when it was not run (see the module).
        ``runner`` — a _Runner of these trial names (scan keeps one for all its expressions, with the budget)."""
        py, tree, why = _regexp_prepare(expr)
        if why:
            return None, why
        try:
            org = self._literal_org(tree)
            if org:
                return org, None
            if _dangerous(tree, icase=bool(tree.state.flags & _IGNORECASE)):
                return None, "dangerous"
        except RecursionError:                   # a deep caller: a refusal, never an exception out of the parse
            return None, "long"
        if runner is None:
            with _Runner(n for _, n in self.trials) as own:
                hit = own.run(py)
        else:
            hit = runner.run(py)
        if isinstance(hit, str):
            return None, hit
        return (self.trials[hit][0], None) if 0 <= hit < len(self.trials) else (None, None)

    def _regexp(self, expr):
        return self.regexp_entry(expr)[0]

    def org_of_entry(self, typ, value):
        """org id of a geosite entry (``typ`` — a number of rules.dat or a word: domain, full, keyword, regexp)."""
        t = _type_name(typ)
        if t in ("domain", "full"):
            return self.org_of(value)
        if t == "keyword":
            return self._keyword(value)
        if t == "regexp":
            return self._regexp(value)
        return None

    # ── lists ──────────────────────────────────────────────────────────────
    def scan(self, cats, stats=None):
        """{"force"|"direct"|"block": [(code, type, value, org)]} — the marked entries of the built-in groups.
        ``cats`` — {CODE: [(type, value)]} of ``dat.geosite`` (codes in any case). ``stats`` (a dict) gets
        «entries», «unknown», «regexp», «regexp_unchecked» and «regexp_unchecked_by» {reason: n} (see the
        module) — an unchecked expression is counted, never a silent «not marked». One worker process for all the
        expressions of the scan; at most REGEXP_TIMEOUT_S per expression and about REGEXP_BUDGET_S of runs for all
        (what needs no run is checked after the budget too)."""
        upper = {str(k).upper(): v for k, v in (cats or {}).items()}
        out = {}
        seen_n = n_rx = n_unknown = 0
        why_n = dict.fromkeys(UNCHECKED, 0)
        with _Runner((n for _, n in self.trials), REGEXP_BUDGET_S) as runner:    # starts at the first run
            for group, codes in GROUPS:
                rows = []
                seen = set()
                for code in codes:
                    for typ, value in upper.get(code.upper(), ()):
                        t = _type_name(typ)
                        key = (code, t if t else ("?", repr(typ)), value)
                        if key in seen:
                            continue
                        seen.add(key)
                        seen_n += 1
                        if t is None:
                            n_unknown += 1               # a type of a newer rules.dat: counted, not guessed
                            continue
                        if t == "regexp":
                            n_rx += 1
                            org, why = self.regexp_entry(value, runner)
                            if why:
                                why_n[why] += 1
                        else:
                            org = self.org_of_entry(t, value)
                        if org:
                            rows.append((code, t, value, org))
                out[group] = rows
        if stats is not None:
            stats.update({"entries": seen_n, "unknown": n_unknown, "regexp": n_rx,
                          "regexp_unchecked": sum(why_n.values()), "regexp_unchecked_by": why_n})
        return out

    # ── texts ──────────────────────────────────────────────────────────────
    def names(self, org_ids, lang=None):
        """«Meta, ФБК»: the names in ``lang`` in the order given, unknown ids as they are, repeats once."""
        lg = get_lang(lang)
        out = []
        for oid in org_ids or ():
            o = self.by_id.get(oid)
            n = o["names"].get(lg) or o["names"]["ru"] if o else str(oid)
            if n not in out:
                out.append(n)
        return ", ".join(out)

    def block_text(self, org_ids, lang=None):
        return tr("rules.rf.block", lang, names=self.names(org_ids, lang))


def _translate(expr):
    """RE2 text -> (Python text, None) or (None, "syntax"): ``\\z`` becomes ``\\Z``; what the two engines read
    differently is refused (see the module)."""
    out = []
    i, n = 0, len(expr)
    in_class = False
    depth = 0
    while i < n:
        c = expr[i]
        if c == "\\":
            if i + 1 >= n:
                return None, "syntax"
            e = expr[i + 1]
            known = _ESCAPES_IN_CLASS if in_class else _ESCAPES
            # an escaped ASCII punctuation is the character in both; a letter, a digit 1-9 (back reference in
            # Python, octal in RE2) or a non-ASCII character after «\» — RE2 refuses or Python reads it otherwise
            if not (e in known or (e < "\x80" and not e.isalnum())):
                return None, "syntax"
            out.append("\\Z" if e == "z" else c + e)
            i += 2
            continue
        if in_class:
            if c == "]":
                in_class = False
        elif c == "[":
            in_class = True
            out.append(c)
            i += 1
            if i < n and expr[i] == "^":
                out.append("^")
                i += 1
            if i < n and expr[i] == "]":         # «[]a]» — «]» first is a character
                out.append("]")
                i += 1
            continue
        elif c == ")":
            depth = max(depth - 1, 0)
        elif c == "(":
            depth += 1
            if depth > REGEXP_DEPTH_MAX:
                return None, "long"             # every check recurses into the groups
            rest = expr[i + 2:i + 4] if expr.startswith("(?", i) else ""
            if rest[:1] in ("=", "!", ">", "#", "(") or rest in ("<=", "<!") or expr.startswith("(?P=", i):
                return None, "syntax"
            m = _INLINE_FLAGS.match(expr, i)
            if m:
                if set(m.group(1)) - set("ims-"):
                    return None, "syntax"       # «U» is ungreedy in RE2 and unicode in Python, «x» verbose only here
                if m.group(2) == ")" and i:
                    return None, "syntax"       # RE2: from here on; Python 3.11+: an error, 3.8-3.10: the whole text
        elif c == "{" and expr.startswith("{,", i):
            return None, "syntax"               # RE2: the characters «{,»; Python: a repeat from 0
        elif c == "{" and _leading_zero(expr, i):
            return None, "syntax"               # RE2 (Go parseInt): «a{01}» is the characters; Python: a repeat
        elif c in "*+?}" and i + 1 < n and expr[i + 1] == "+":
            return None, "syntax"               # possessive: RE2 has none, Python only since 3.11
        out.append(c)
        i += 1
    if in_class:
        return None, "syntax"
    if _POSIX_CLASS.search(expr):
        return None, "syntax"
    return "".join(out), None


def _op(op):
    return getattr(op, "name", None) or repr(op)


def _first_literal(alt):
    items = list(alt)
    if items and _op(items[0][0]) == "LITERAL":
        return items[0][1]
    return None


def _subs(op, av):
    """The sub-sequences of one item of a parsed expression."""
    name = _op(op)
    if name in _REPEATS:
        return [av[2]]
    if name == "SUBPATTERN":
        return [av[-1]]
    if name == "ATOMIC_GROUP":
        return [av]
    if name == "BRANCH":
        return list(av[1])
    return []


def _variable(op, av):
    """The item matches texts of different lengths through a repeat («.*», «a?», «(x+)», «(a|b+)»)."""
    if _op(op) in _REPEATS and av[0] != av[1]:
        return True
    return any(_variable(o, a) for sub in _subs(op, av) for o, a in sub)


def _leading_zero(expr, i):
    """``expr[i:]`` is a count ``{n}``, ``{n,}``, ``{n,m}`` with a number written with a leading zero (``{01}``)."""
    m = _COUNT.match(expr, i)
    return bool(m) and any(g and len(g) > 1 and g[0] == "0" for g in m.groups())


def _icase(op, av, icase):
    """IGNORECASE inside ``(?i:...)`` / ``(?-i:...)``; the same as outside for every other item."""
    if _op(op) == "SUBPATTERN" and len(av) == 4:
        if av[1] & _IGNORECASE:
            return True
        if av[2] & _IGNORECASE:
            return False
    return icase


def _char(op, av):
    """The one literal character of an item (``a``, ``\\.``, ``[.]``) or None."""
    name = _op(op)
    if name == "LITERAL":
        return chr(av)
    if name == "IN" and len(av) == 1 and _op(av[0][0]) == "LITERAL":
        return chr(av[0][1])
    return None


def _in_category(cat, ch):
    """``ch`` in ``\\d``/``\\w``/``\\s`` of Python (wider than RE2's ASCII ones: the safe side); None — unknown."""
    base = {"DIGIT": ch.isdecimal(), "WORD": ch.isalnum() or ch == "_", "SPACE": ch.isspace()}
    name = cat[len("CATEGORY_"):] if cat.startswith("CATEGORY_") else cat
    if name in base:
        return base[name]
    if name.startswith("NOT_") and name[4:] in base:
        return not base[name[4:]]
    return None


def _eats(op, av, ch, icase):
    """True when the one-character item can match ``ch``; None when it is not such an item or unknown."""
    chars = {ch, ch.lower(), ch.upper()} if icase else {ch}
    name = _op(op)
    if name == "LITERAL":
        return chr(av) in chars or (icase and chr(av).lower() in chars)
    if name == "NOT_LITERAL":
        return not (chr(av) in chars or (icase and chr(av).lower() in chars))
    if name == "ANY":
        return True
    if name != "IN":
        return None
    neg = hit = False
    for o, a in av:
        n = _op(o)
        if n == "NEGATE":
            neg = True
        elif n == "LITERAL":
            hit = hit or chr(a) in chars or (icase and chr(a).lower() in chars)
        elif n == "RANGE":
            hit = hit or any(a[0] <= ord(c) <= a[1] for c in chars)
        elif n == "CATEGORY":
            r = _in_category(_op(a), ch)
            if r is None:
                return None
            hit = hit or r
        else:
            return None
    return hit != neg


def _split_once(item, nxt, icase):
    """A repeat of one character class followed by a literal it cannot match (``[a-z0-9-]+\\.``): its length is
    forced, nothing to backtrack into (REV-s1-w0-3 Н2)."""
    body = list(item)
    sep = _char(*nxt) if nxt is not None else None
    if sep is None or len(body) != 1:
        return False
    return _eats(body[0][0], body[0][1], sep, icase) is False


def _dangerous(sub, in_repeat=False, icase=False):
    """The shapes of a long backtracking (see the module): a variable repeat inside a repeat of more than one pass
    (``(a+)+``, ``(.*){20}``, ``(?:.?){30}``) — unless it is one class followed by a literal it cannot match
    (``([a-z0-9-]+\\.)+``), an alternative under such a repeat whose branches do not start with distinct literals
    (``(a|a)*``), more than two variable items in a row (``.*.*.*``; a zero-width ``^``, ``\\b`` does not break the
    row, a literal does)."""
    row = 0
    items = list(sub)
    for k, (op, av) in enumerate(items):
        name = _op(op)
        if name in _REPEATS:
            lo, hi, item = av
            if in_repeat and lo != hi and not _split_once(item, items[k + 1] if k + 1 < len(items) else None,
                                                          icase):
                return True
            if _dangerous(item, in_repeat or hi > 1, icase):
                return True
        elif name == "BRANCH":
            alts = av[1]
            if in_repeat and len(alts) > 1:
                firsts = [_first_literal(a) for a in alts]
                if None in firsts or len(set(firsts)) != len(firsts):
                    return True
            if any(_dangerous(a, in_repeat, icase) for a in alts):
                return True
        else:
            ic = _icase(op, av, icase)
            if any(_dangerous(s, in_repeat, ic) for s in _subs(op, av)):
                return True
        if _variable(op, av):
            row += 1
            if row > 2:
                return True
        elif name not in ("AT",):
            row = 0
    return False


def _re2_limits(sub, budget=RE2_REPEAT_MAX):
    """False when RE2 refuses the counts: a bound over 1000, or nested counted repeats whose product is over 1000
    (``(a{100}){100}``) — RE2's repeatIsValid. Python takes both."""
    for op, av in sub:
        b = budget
        if _op(op) in _REPEATS:
            lo, hi = av[0], av[1]
            m = lo if hi == _sre_parse.MAXREPEAT else hi
            if lo > RE2_REPEAT_MAX or m > b:
                return False
            if m > 0:
                b //= m
        for s in _subs(op, av):
            if not _re2_limits(s, b):
                return False
    return True


def _join(left, right, work=None):
    """Every text of ``left`` followed by every text of ``right``, or None when more than TAILS_MAX. ``work`` — a
    one-item list, the budget of texts left for one expression (TAILS_WORK_MAX): spent — None."""
    if work is not None:
        work[0] -= len(left) * len(right)
        if work[0] < 0:
            return None
    out = list(dict.fromkeys(a + b for a in left for b in right))
    return out if len(out) <= TAILS_MAX else None


def _finite(sub, icase, work=None):
    """All the texts a sequence matches when they are a few literal ones (``(www|m)\\.x``, ``(-a)?``), else None.
    Lower case under IGNORECASE."""
    out = [""]
    for op, av in sub:
        piece = _finite_item(op, av, icase, work)
        out = None if piece is None else _join(out, piece, work)
        if out is None:
            return None
    return out


def _finite_item(op, av, icase, work=None):
    ch = _char(op, av)
    if ch is not None:
        return [ch.lower() if icase else ch]
    name = _op(op)
    if name == "SUBPATTERN":
        return _finite(av[-1], _icase(op, av, icase), work)
    if name == "BRANCH":
        out = []
        for alt in av[1]:
            f = _finite(alt, icase, work)
            if f is None:
                return None
            out.extend(f)
        out = list(dict.fromkeys(out))
        return out if len(out) <= TAILS_MAX else None
    if name in _REPEATS and av[1] <= 1:          # «x?»
        f = _finite(av[2], icase, work)
        return None if f is None else ([""] + f if av[0] == 0 else f)
    return None


def _once(f):
    box = []

    def get():
        if not box:
            box.append(f())
        return box[0]
    return get


def _label_start(items, then, active=None):
    """True when every text the sequence ``items`` matches ends with «.» or stands at the start of the name (``^``);
    ``then()`` — the answer for the empty text (what lies before ``items``). ``active`` — a one-item list, the calls
    of this function under way (into a group, into an alternative, back over an item that is not one character):
    more than REGEXP_DEPTH_MAX — False (the first label may go on to the left: the safe side). So the stack it takes
    does not grow with the length of the expression, and the answer does not depend on the stack of the caller.
    ``rest`` is computed once: ``(|\\b)`` × n is not 2^n ways."""
    if active is None:
        active = [0]
    if active[0] >= REGEXP_DEPTH_MAX:
        return False
    active[0] += 1
    try:
        for k in range(len(items) - 1, -1, -1):
            op, av = items[k]
            ch = _char(op, av)
            if ch is not None:
                return ch == "."
            name = _op(op)
            if name == "AT":
                if _op(av) in _BEGIN_AT:
                    return True
                continue                         # zero-width: «\b», «$»
            rest = _once(lambda k=k: _label_start(items[:k], then, active))
            if name == "SUBPATTERN":
                return _label_start(list(av[-1]), rest, active)
            if name == "BRANCH":
                return all(_label_start(list(a), rest, active) for a in av[1])
            if name in _REPEATS:
                if av[1] == 0:
                    continue
                return _label_start(list(av[2]), rest, active) and (av[0] > 0 or rest())
            return False
        return then()
    finally:
        active[0] -= 1


def _upper_literal(items, icase):
    """A letter of upper case outside a class that every text of the sequence has (not in an alternative, not under
    ``?``/``*``; IGNORECASE off): RE2 finds no name in lower case. Nesting is bounded by REGEXP_DEPTH_MAX."""
    for op, av in items:
        ch = _char(op, av)
        if ch is not None:
            if not icase and ch != ch.lower():
                return True
            continue
        name = _op(op)
        if name == "SUBPATTERN":
            if _upper_literal(av[-1], _icase(op, av, icase)):
                return True
        elif name in _REPEATS and av[0] > 0:
            if _upper_literal(av[2], icase):
                return True
    return False


def _tails(items, icase, hope=None, enough=None):
    """[(tail, whole, before, icase)]: the literal texts that the names matched by the sequence ``items`` END with
    (``$``/``\\z`` after them or the end of the expression — what matches without ``$`` matches with it too; each
    alternative that ends the expression — each); ``whole`` — the first label of the tail is whole (``^`` or «.»
    before it); ``before`` — the items before the tail. Without recursion over the length: one list of ways, each way
    read once (``(|)`` × 170 — 170 ways, not 2^170), at most TAILS_WAYS_MAX ways and TAILS_WORK_MAX texts; what is
    over is not read (the safe side: no mark, the expression goes on to the other checks). ``hope(tail)`` — False: the
    tail can end no name that marks, whatever lies to its left — it is dropped, a way without tails gives nothing;
    ``enough(tail)`` True for every tail — the way is not read further to the left."""
    out = []
    todo = [(list(items), icase)]
    seen = set()
    work = [TAILS_WORK_MAX]
    while todo and len(seen) < TAILS_WAYS_MAX and work[0] > 0:
        seq, ic = todo.pop()
        key = (ic, tuple(map(id, seq)))
        if key in seen:
            continue
        seen.add(key)
        if not seq:
            continue                             # an empty way: nothing it ends with
        op, av = seq[-1]
        name = _op(op)
        if name == "BRANCH":                     # each alternative, the first one read first
            todo.extend((seq[:-1] + list(alt), ic) for alt in reversed(av[1]))
            continue
        if name == "SUBPATTERN":
            if _icase(op, av, ic) == ic:
                todo.append((seq[:-1] + list(av[-1]), ic))
            continue
        if name == "AT":
            if _op(av) not in _END_AT:
                continue                         # «\b», «^» at the end: not read
            i = len(seq) - 1
        else:
            i = len(seq)
        tails = [""]
        while i:
            piece = _finite_item(seq[i - 1][0], seq[i - 1][1], ic, work)
            new = None if piece is None else _join(piece, tails, work)
            if new is None:
                break
            if hope is not None:
                new = [t for t in new if hope(t)]
            tails, i = new, i - 1
            if not tails or (enough is not None and all(enough(t) for t in tails)):
                break
        tails = [t for t in tails if t]
        if tails:
            whole = _label_start(seq[:i], lambda: False)
            out.extend((t, whole, seq[:i], ic) for t in tails)
    return out


def _regexp_prepare(expr):
    """(Python text, parsed tree, None) or (None, None, reason) — the checks that need no run: long, syntax."""
    if not isinstance(expr, str) or not expr:
        return None, None, "syntax"
    if len(expr) > REGEXP_MAX:
        return None, None, "long"
    py, why = _translate(expr)
    if py is None:
        return None, None, why
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")      # FutureWarning of «[[», «--» in a class: not for the CLI output
            tree = _sre_parse.parse(py)
        if not _re2_limits(tree):
            return None, None, "syntax"
    except RecursionError:
        return None, None, "long"
    except (re.error, OverflowError, ValueError, TypeError):
        return None, None, "syntax"
    return py, tree, None


# The worker: reads the trial names (one JSON line), says «ready», then for every JSON line with a Python
# expression answers the index of the first trial name it finds, -1 (none) or -2 (does not compile).
_WORKER = """import json, re, sys, warnings
warnings.simplefilter("ignore")
names = json.loads(sys.stdin.readline())
sys.stdout.write("ready\\n"); sys.stdout.flush()
while True:
    line = sys.stdin.readline()
    if not line:
        break
    try:
        rx = re.compile(json.loads(line))
        hit = next((i for i, s in enumerate(names) if rx.search(s)), -1)
    except Exception:
        hit = -2
    sys.stdout.write("%d\\n" % hit); sys.stdout.flush()
"""


class _Runner:
    """Runs Python expressions against the trial names in a separate process: an expression over
    REGEXP_TIMEOUT_S seconds is killed with the process (the next one starts a new process). run() -> the index of
    the trial name found, -1, or "syntax" / "timeout" / "budget" (``budget`` seconds of runs spent: no more runs)."""

    def __init__(self, names, budget=None):
        self.names = list(names)
        self.proc = None
        self.broken = False
        self.budget = budget                     # seconds of runs (start of the worker included); None — no limit
        self.spent = 0.0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        p, self.proc = self.proc, None
        if p is None:
            return
        try:
            p.kill()
        except OSError:
            pass
        for f in (p.stdin, p.stdout):
            try:
                f.close()
            except OSError:
                pass
        try:
            p.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):  # pragma: no cover — a killed process always ends
            pass

    def _line(self, timeout):
        """One line of the worker or None (no answer in time, or it ended)."""
        fd = self.proc.stdout.fileno()
        deadline = time.monotonic() + timeout
        buf = b""
        while not buf.endswith(b"\n"):
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                continue
            chunk = os.read(fd, 64)
            if not chunk:
                return None
            buf += chunk
        return buf.decode("ascii", "replace").strip()

    def _start(self):
        try:
            self.proc = subprocess.Popen([sys.executable or "python3", "-I", "-S", "-c", _WORKER],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         close_fds=True)
            self.proc.stdin.write((json.dumps(self.names) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
            if self._line(WORKER_START_S) == "ready":
                return True
        except (OSError, ValueError):
            pass
        self.close()
        self.broken = True                       # no worker — no expression is run in this process instead
        return False

    def run(self, py):
        if self.broken:
            return "timeout"                     # the worker never started: «timeout» for all, not «budget» (З-1)
        if self.budget is not None and self.spent > self.budget:
            return "budget"
        t0 = time.monotonic()
        try:
            return self._run(py)
        finally:
            self.spent += time.monotonic() - t0

    def _run(self, py):
        if self.broken or (self.proc is None and not self._start()):
            return "timeout"
        try:
            self.proc.stdin.write((json.dumps(py) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except (OSError, ValueError):
            self.close()
            return "timeout"
        got = self._line(REGEXP_TIMEOUT_S)
        if got is None:
            self.close()
            return "timeout"
        try:
            hit = int(got)
        except ValueError:
            self.close()
            return "timeout"
        return "syntax" if hit == -2 else hit


def _type_name(typ):
    if isinstance(typ, bool):
        return None
    if isinstance(typ, int):
        from .dat import TYPE_NAMES
        return TYPE_NAMES.get(typ)
    return _TYPES.get(str(typ or "").strip().lower())


def load(path=None):
    """Registry of ``path`` (the file of the package by default). KaskadError when it is missing or broken."""
    p = path or PATH
    try:
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        raise _bad(p, "%s: %s" % (type(e).__name__, e)) from None
    try:
        return Registry(doc, p)
    except ValueError as e:
        raise _bad(p, str(e)) from None


def default():
    with _lock:
        if _default[0] is None:
            _default[0] = load()
        return _default[0]


def reload():
    with _lock:
        _default[0] = None


def org_of(name, reg=None):
    return (reg or default()).org_of(name)


def org_of_entry(typ, value, reg=None):
    return (reg or default()).org_of_entry(typ, value)


def regexp_entry(expr, reg=None):
    return (reg or default()).regexp_entry(expr)


def scan(cats, reg=None, stats=None):
    return (reg or default()).scan(cats, stats=stats)


def names(org_ids, lang=None, reg=None):
    return (reg or default()).names(org_ids, lang)


def block_text(org_ids, lang=None, reg=None):
    return (reg or default()).block_text(org_ids, lang)


def consent_text(lang=None):
    return tr("rules.rf.consent", lang)

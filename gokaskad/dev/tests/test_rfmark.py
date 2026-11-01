"""Stage 1, R1: the registry of the ⚠РФ mark and its parser (docs/k3/SPEC-stage1-rf-mark-builtin.md §2, §7, §9):
suffixes and ``exclude`` (WhatsApp), ``keyword:``/``regexp:`` of geosite, ``scan`` of the built-in groups, the texts
of the block and of the check box in RU/EN, a broken registry is an error — never an empty registry. Domains of the
tests: example.com and invented ones (the real ones only where the spec names them)."""

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from unittest import mock

from kaskad import errors
from kaskad.i18n import check as icheck
from kaskad.i18n import use_lang
from kaskad.rules import dat, rfmark
from tests.rules_helpers import geosite_dat

CYR = re.compile("[А-Яа-яЁё]")
HERE = os.path.dirname(os.path.abspath(__file__))
# the exact registry: a domain removed or added without a decision fails here (REV-s1-w0 П3)
META = {"facebook.com", "fb.com", "fb.me", "fb.watch", "fb.gg", "fbcdn.net", "fbsbx.com", "facebook.net",
        "facebookmail.com", "messenger.com", "m.me", "instagram.com", "cdninstagram.com", "instagr.am", "ig.me",
        "threads.net", "threads.com", "meta.com", "meta.ai", "llama.com", "llamameta.net", "metademolab.com",
        "oculus.com", "oculuscdn.com", "oculusvr.com", "workplace.com", "wit.ai", "mapillary.com", "atdmt.com",
        "tfbnw.net"}


def _doc(**orgs_kw):
    """A small registry: «acme» with example.com and an excluded chat.example.com."""
    org = {"id": "acme", "names": {"ru": "Акме", "en": "Acme"}, "status": "extremist", "since": "2020-01-02",
           "source": "src", "domains": ["example.com", "acme-cdn.example.net"], "exclude": ["chat.example.com"],
           "iplist": []}
    org.update(orgs_kw)
    return {"schema": 1, "version": "2026-10-15", "checked": "2026-10-15",
            "sources": [{"id": "src", "title": {"ru": "Источник", "en": "Source"}, "url": "https://example.com/list",
                         "checked": "2026-10-15"}],
            "orgs": [org]}


class RealRegistryTest(unittest.TestCase):
    """The file of the package: who is marked and who is not (owner's rule 14.10)."""

    def setUp(self):
        rfmark.reload()
        self.reg = rfmark.default()

    def test_loads_and_is_cached(self):
        self.assertIs(rfmark.default(), self.reg)
        self.assertEqual(sorted(self.reg.by_id), ["fbk", "meta"])
        self.assertRegex(self.reg.version, r"^\d{4}-\d\d-\d\d$")

    def test_meta_all_resources_but_whatsapp(self):
        for name in ("facebook.com", "www.facebook.com", "static.xx.fbcdn.net", "instagram.com", "threads.net",
                     "about.meta.com", "meta.ai", "llamameta.net", "metademolab.com", "oculus.com", "m.me",
                     "FACEBOOK.COM.", "messenger.com"):
            self.assertEqual(rfmark.org_of(name), "meta", name)
        for name in ("whatsapp.com", "web.whatsapp.com", "whatsapp.net", "mmg.whatsapp.net", "wa.me"):
            self.assertIsNone(rfmark.org_of(name), name)

    def test_fbk(self):
        self.assertEqual(rfmark.org_of("navalny.com"), "fbk")
        self.assertEqual(rfmark.org_of("www.navalny.com"), "fbk")
        self.assertIsNone(rfmark.org_of("fbk.info"))         # no confirmation but Wikipedia — not listed

    def test_not_marked(self):
        """Twitter/X, LinkedIn, TikTok, media and look-alikes of the spec (§1) are not marked."""
        for name in ("twitter.com", "x.com", "linkedin.com", "tiktok.com", "example.com", "pm.me", "telegram.me",
                     "metal.example.com", "notfacebook.com", "facebook.com.example.com", "", None, "not a name"):
            self.assertIsNone(rfmark.org_of(name), name)

    def test_exact_composition(self):
        meta, fbk = self.reg.by_id["meta"], self.reg.by_id["fbk"]
        self.assertEqual(set(meta["domains"]), META)
        self.assertEqual(len(meta["domains"]), len(META))
        self.assertEqual(meta["exclude"], ["whatsapp.com", "whatsapp.net", "wa.me"])
        self.assertEqual(fbk["domains"], ["navalny.com"])
        self.assertEqual(fbk["exclude"], [])
        for d in META:
            self.assertEqual(rfmark.org_of(d), "meta", d)

    def test_sources(self):
        """Meta and FBK — the list of Rosfinmonitoring (items 17, 675), FBK «terrorist» — the list of the FSB; no
        media, no Wikipedia; «checked» only where a page was opened."""
        src = {s["id"]: s for s in self.reg.doc["sources"]}
        self.assertEqual(self.reg.by_id["meta"]["source"], "rosfinmonitoring")
        self.assertEqual(self.reg.by_id["fbk"]["source"], "rosfinmonitoring")
        self.assertEqual(self.reg.by_id["fbk"]["also"][0]["source"], "fsb-terrorist")
        self.assertTrue(src["rosfinmonitoring"]["url"].startswith("https://www.fedsfm.ru/"))
        self.assertNotIn("minjust-extremist", src)
        raw = json.dumps(self.reg.doc, ensure_ascii=False).lower()
        for bad in ("wikipedia", "zona.media", "meduza", "novaya"):
            self.assertNotIn(bad, raw)

    def test_registry_content(self):
        meta, fbk = self.reg.by_id["meta"], self.reg.by_id["fbk"]
        self.assertEqual((meta["since"], fbk["since"]), ("2022-03-21", "2021-06-09"))
        self.assertEqual([a["status"] for a in fbk.get("also", [])], ["terrorist"])
        self.assertIn("whatsapp.com", meta["exclude"])
        everything = [d for o in self.reg.orgs for d in o["domains"] + o.get("exclude", [])]
        self.assertFalse([d for d in everything if d.endswith(".ru")], "no .ru in the registry")
        for bad in ("twitter.com", "x.com", "linkedin.com", "tiktok.com", "whatsapp.com"):
            self.assertNotIn(bad, [d for o in self.reg.orgs for d in o["domains"]])
        for o in self.reg.orgs:
            self.assertTrue(o["names"]["ru"] and o["names"]["en"])
            self.assertIsNone(CYR.search(o["names"]["en"]))
            self.assertEqual(o["iplist"], [], "iplist — stage 2")

    def test_entries_of_the_real_list(self):
        """§1: the Meta matches in CATEGORY-GEOBLOCK-RU of RoscomVPN 14.10 — four ``domain:`` and ``keyword:meta.ai``."""
        for v in ("meta.com", "llamameta.net", "metademolab.com", "oculus.com"):
            self.assertEqual(rfmark.org_of_entry("domain", v), "meta", v)
            self.assertEqual(rfmark.org_of_entry(dat.DOMAIN, v), "meta", v)
        self.assertEqual(rfmark.org_of_entry(dat.PLAIN, "meta.ai"), "meta")
        self.assertEqual(rfmark.org_of_entry(dat.FULL, "www.instagram.com"), "meta")
        for v in ("pm.me", "metal.example.com", "telegram.me"):
            self.assertIsNone(rfmark.org_of_entry(dat.DOMAIN, v), v)


class KeywordRegexpTest(unittest.TestCase):

    def test_keyword(self):
        kw = lambda v: rfmark.org_of_entry("keyword", v)     # noqa: E731
        self.assertEqual(kw("meta.ai"), "meta")               # contains a domain of the registry
        self.assertEqual(kw("facebook"), "meta")              # lies inside facebook.com
        self.assertEqual(kw("face"), "meta")                  # 4 characters — enough
        self.assertEqual(kw("www.facebook.com"), "meta")
        self.assertEqual(kw("facebook.co"), "meta")           # inside facebook.com
        self.assertIsNone(kw("cdn"))                          # shorter than 4
        self.assertIsNone(kw("fac"))
        self.assertIsNone(kw("whatsapp"))                     # only WhatsApp
        self.assertIsNone(kw("whatsapp.com"))
        self.assertIsNone(kw("telegram.me"))                  # ends with «m.me», but not on a label boundary
        self.assertIsNone(kw("example"))
        self.assertIsNone(kw(""))
        # after a dot the word begins a domain: xray matches m.facebook.com, www.instagram.com (REV-s1-w0 П5)
        self.assertEqual(kw("m.facebook"), "meta")
        self.assertEqual(kw("www.instagram"), "meta")
        self.assertEqual(kw("static.fbcdn"), "meta")
        self.assertIsNone(kw("x.me"))                         # «me» — shorter than 4
        self.assertIsNone(kw("www.whatsapp"))                 # only WhatsApp
        self.assertIsNone(kw("www.example"))

    def test_regexp(self):
        rx = lambda v: rfmark.org_of_entry(dat.REGEX, v)     # noqa: E731
        self.assertEqual(rx(r"(^|\.)facebook\.com$"), "meta")
        self.assertEqual(rx(r"^.+\.fbcdn\.net$"), "meta")    # finds x.fbcdn.net
        self.assertIsNone(rx(r"^example\.com$"))
        self.assertIsNone(rx(r"whatsapp\.(com|net)$"))       # only the excluded domains
        self.assertIsNone(rx("("))                           # broken — not marked, no exception
        self.assertIsNone(rx("facebook" + "x?" * 300))       # longer than REGEXP_MAX
        self.assertEqual(rx(r"facebook\.com\z"), "meta")      # RE2 \z — Python \Z on every version
        self.assertEqual(rx(r"(?i)^FACEBOOK\.COM$"), "meta")
        self.assertIsNone(rfmark.org_of_entry(9, "facebook.com"))   # an unknown type of a newer format

    def test_regexp_refused_before_running(self):
        """REV-s1-w0 П1: a backtracking bomb is refused before any run (it took 225 s), RE2-only syntax is counted."""
        reg = rfmark.default()
        cases = {r"^(.*.*)*z$": "dangerous", r"^(.*.*.*.*)*z$": "dangerous", r"(a+)+$": "dangerous",
                 r"(a|a)*$": "dangerous", r"^(.+\.)*example\.org$": "dangerous", r"(\w*)*example": "dangerous",
                 r"\pL+\.facebook\.com": "syntax", r"\p{Greek}": "syntax", r"facebook\.com\Z": "syntax",
                 r"[[:alpha:]]+\.facebook\.com": "syntax", r"(?=face)facebook": "syntax", r"(a)\1": "syntax",
                 r"face++book": "syntax", r"(?U)facebook": "syntax", r"(?>a)": "syntax", r"[\z]": "syntax",
                 r"\Qa.b\E": "syntax", "(": "syntax", "": "syntax", "f" * 513: "long"}
        for expr, why in cases.items():
            with self.subTest(expr=expr[:40]):
                t0 = time.monotonic()
                with warnings.catch_warnings(record=True) as w:
                    warnings.simplefilter("always")
                    self.assertEqual(reg.regexp_entry(expr), (None, why))
                self.assertEqual(w, [], "no FutureWarning in the output of CLI")
                self.assertLess(time.monotonic() - t0, 0.5)
        for expr in (r"(com|net)+", r"^(www\.)?facebook\.com$", r"face(book)?\.com", r"a{2,3}"):
            self.assertNotEqual(reg.regexp_entry(expr)[1], "dangerous", expr)

    def test_exclude_is_stronger(self):
        reg = rfmark.Registry(_doc())
        self.assertEqual(reg.org_of("example.com"), "acme")
        self.assertEqual(reg.org_of("www.example.com"), "acme")
        self.assertIsNone(reg.org_of("chat.example.com"))
        self.assertIsNone(reg.org_of("a.chat.example.com"))
        self.assertIsNone(reg.org_of_entry("keyword", "chat.example.com"))
        self.assertIsNone(reg.org_of_entry("keyword", "x.chat.example.com"))
        self.assertEqual(reg.org_of_entry("keyword", "acme-cdn"), "acme")
        self.assertIsNone(reg.org_of_entry("regexp", r"^chat\.example\.com$"))
        self.assertEqual(reg.org_of_entry("regexp", r"example\.com$"), "acme")

    def test_longest_domain_of_two_orgs_wins(self):
        doc = _doc()
        doc["orgs"].append({"id": "sub", "names": {"ru": "Саб", "en": "Sub"}, "status": "extremist",
                            "since": "2020-01-02", "source": "src", "domains": ["sub.example.com"]})
        reg = rfmark.Registry(doc)
        self.assertEqual(reg.org_of("a.sub.example.com"), "sub")
        self.assertEqual(reg.org_of("a.example.com"), "acme")


def _bounded(code, timeout):
    """Runs ``code`` (it prints one JSON line) in a separate Python with this package: a hang fails the test after
    ``timeout`` seconds instead of hanging it (REV-s1-w0-2: a bomb took more than 300 s)."""
    lib = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(rfmark.__file__))))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((lib, os.path.dirname(HERE))))
    env.pop("K3_LAYOUT", None)
    try:
        p = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise AssertionError("hung for more than %s s" % timeout) from None
    if p.returncode:
        raise AssertionError(p.stderr[-2000:])
    return json.loads(p.stdout.strip().splitlines()[-1])


class RegexpReview2Test(unittest.TestCase):
    """REV-s1-w0-2: no expression holds the parse longer than the limit (B1), regexp of a subdomain (B2), what RE2
    and Python read differently (B3), the checks the tests did not hold (B4)."""

    def test_bombs_of_the_review_are_refused_at_once(self):
        """B1 a): a variable repeat inside {n} or inside a repeat, a chain of variable repeats — «dangerous» before
        any run. The table of the review (each took from 1.5 s to more than 300 s)."""
        bombs = [r"(.*){20}z", r"(.*){15}z", r"(.*){30}z$", r"((.*){5}){5}z", r"(?:.?){30}z", r"(?:.?){24}z",
                 r"(?:.?){20}z", ".*" * 20 + "z", ".*" * 10 + "z", r"(?:\w*){12}z", r"(.*a){20}$", r".*.*.*z",
                 r"(?:a?){3}b", r"[a-z]+[0-9]*[a-z]+x", r"(x+)?(y+)?(z+)?w"]
        code = ("import json, time\nfrom kaskad.rules import rfmark\nout = []\n"
                "for e in %r:\n    t = time.monotonic(); r = rfmark.default().regexp_entry(e)\n"
                "    out.append([e, list(r), time.monotonic() - t])\nprint(json.dumps(out))\n" % (bombs,))
        for expr, got, took in _bounded(code, 30):
            with self.subTest(expr=expr):
                self.assertEqual(got, [None, "dangerous"])
                self.assertLess(took, 0.2)
        stats = {}
        rfmark.scan({"category-ads": [(dat.REGEX, e) for e in bombs]}, stats=stats)
        self.assertEqual(stats["regexp_unchecked_by"]["dangerous"], len(bombs))
        # not refused: one pass of «?» over a variable part, two variable items in a row, a fixed repeat of a
        # fixed part, alternatives with distinct first letters
        for expr in (r"^(.+\.)?example\.org$", r"^[a-z]+[0-9]*\.example\.org$", r"(?:ab){3}", r"(com|net)+",
                     r"^(www\.)?facebook\.com$", r"a{2,3}", r"^.+\.fbcdn\.net$"):
            self.assertNotEqual(rfmark.regexp_entry(expr)[1], "dangerous", expr)

    def test_timeout_kills_the_worker_and_the_parse_goes_on(self):
        """B1 b): what passes the shape check runs in a worker process — REGEXP_TIMEOUT_S per expression, then
        «timeout» in stats, the process killed, the next entry checked by a new one."""
        code = r'''
import json, time, sys
sys.path.insert(0, %r)
from tests.test_rfmark import _doc
from kaskad.rules import rfmark, dat
reg = rfmark.Registry(_doc(domains=["a" * 30 + ".com"], exclude=[]))
slow = ".*a" * 12 + "Q"                      # passes the shape check: C(30, 12) ways on aaa...a.com
t = time.monotonic(); one = reg.regexp_entry(slow); one_s = time.monotonic() - t
stats = {}
t = time.monotonic()
got = reg.scan({"category-geoblock-ru": [(dat.REGEX, slow), (dat.REGEX, slow + "R"), (dat.REGEX, r"a\.com$"),
                                         (dat.DOMAIN, "a" * 30 + ".com")]}, stats=stats)
print(json.dumps([list(one), one_s, got["force"], stats, time.monotonic() - t]))
''' % os.path.dirname(HERE)
        one, one_s, force, stats, scan_s = _bounded(code, 30)
        self.assertEqual(one, [None, "timeout"])
        self.assertLess(one_s, rfmark.REGEXP_TIMEOUT_S + 1.0)
        self.assertEqual([r[2] for r in force], [r"a\.com$", "a" * 30 + ".com"])   # the parse went on
        self.assertEqual(stats["regexp_unchecked_by"]["timeout"], 2)
        self.assertEqual(stats["regexp_unchecked"], 2)
        self.assertLess(scan_s, 2 * rfmark.REGEXP_TIMEOUT_S + 2.0)

    def test_worker_is_killed(self):
        reg = rfmark.Registry(_doc(domains=["a" * 30 + ".com"], exclude=[]))
        with rfmark._Runner(n for _, n in reg.trials) as runner:
            self.assertEqual(runner.run(r"a\.com$"), 0)
            first = runner.proc
            self.assertEqual(runner.run(".*a" * 12 + "Q"), "timeout")
            self.assertIsNone(runner.proc)
            self.assertIsNotNone(first.poll())                  # killed, not left running
            self.assertEqual(runner.run(r"zzz"), -1)            # a new worker
            self.assertEqual(runner.run(r"(?<=a)b"), -1)        # Python runs it (the shape check is not here)
            self.assertEqual(runner.run(r"(?<=a+)b"), "syntax")  # does not compile in Python
            second = runner.proc
        self.assertIsNotNone(second.poll())                     # closed with the scan

    def test_no_worker_no_run_in_this_process(self):
        """Without a worker an expression is «timeout», never run here (it could hang the panel or the CLI)."""
        with mock.patch.object(rfmark.sys, "executable", "/nonexistent/python3"):
            self.assertEqual(rfmark.regexp_entry(r"^.+\.fbcdn\.net$"), ("meta", None))    # a literal: no run
            self.assertEqual(rfmark.regexp_entry(r"^x\.[a-z]+\.net$"), (None, "timeout"))
            stats = {}
            rfmark.scan({"category-ads": [(dat.REGEX, r"^x\.[a-z]+\.net$"), (dat.REGEX, r"^y\.[a-z]+\.net$")]},
                        stats=stats)
        self.assertEqual(stats["regexp_unchecked_by"]["timeout"], 2)

    def test_regexp_of_a_subdomain(self):
        """B2: a literal part with a domain of the registry or its distinctive word marks without a run; trial names
        <domain>, x., www., m.<domain>."""
        rx = rfmark.regexp_entry
        for expr in (r"^www\.facebook\.com$", r"^(www|m)\.instagram\.com$", r"^static\.xx\.fbcdn\.net$",
                     r"^facebook[.]com$", r"(?i)^WWW\.FACEBOOK\.COM$", r"^(.+\.)*facebook\.com$",
                     r"^facebook\.", r"^m\.facebook\.", r"^(.*\.)?instagram\.", r"^m\.me$",
                     r"^www\.[a-z]+book\.com$", r"^m\.[a-z]+\.com$", r"^x\.[a-z]+\.net$"):
            with self.subTest(expr=expr):
                self.assertEqual(rx(expr), ("meta", None))
        for expr in (r"^www\.whatsapp\.com$", r"^web\.whatsapp\.", r"^wa\.me$", r"^metadata\.example\.org$",
                     r"^notfacebook\.com$", r"telegram\.me$", r"^xfacebook\.com$", r"^w\.[a-z]+\.com$",
                     r"^book\.example\.org$", r"^www\.(wa|whatsapp)\.(me|com)$"):
            with self.subTest(expr=expr):
                self.assertEqual(rx(expr), (None, None))
        reg = rfmark.Registry(_doc())
        self.assertEqual(reg.regexp_entry(r"^chat\.example\.com$"), (None, None))       # exclude is stronger
        self.assertEqual(reg.regexp_entry(r"^www\.chat\.example\.com$"), (None, None))
        self.assertEqual(reg.regexp_entry(r"^api\.example\.com$"), ("acme", None))
        self.assertEqual(reg.regexp_entry(r"^m\.example\."), ("acme", None))             # the word «example»

    def test_re2_and_python_differ_syntax(self):
        """B3: what RE2 (xray) and Python read differently is «syntax» on every Python (3.9, 3.11, 3.14)."""
        rx = rfmark.regexp_entry
        for expr in (r"FACE(?i)book", r"face(?s)book", r"(?i)(?s)facebook", r"x{,5}", r"facebook{,1}\.com",
                     r"\U00000066acebook", r"\u0066acebook", r"\N{LATIN SMALL LETTER F}acebook", r"\x{66}acebook",
                     r"\Gfacebook", r"\efacebook", r"\cAfacebook", r"\12facebook", r"\8", r"\éfacebook",
                     r"[\N{LATIN SMALL LETTER F}]acebook", r"[\b]facebook", r"[\z]", r"a{1001}", r"a{0,100000}",
                     r"a{1001,}", r"(?:a{100}){100}", r"(?:(?:a{10}){10}){11}", r"(?x)face book",
                     r"(?a)facebook", r"(?L)facebook", r"(?u)facebook", r"(?U)facebook"):
            with self.subTest(expr=expr):
                with warnings.catch_warnings(record=True) as w:
                    warnings.simplefilter("always")
                    self.assertEqual(rx(expr), (None, "syntax"))
                self.assertEqual(w, [])
        for expr, want in ((r"(?i:FACE)book", "meta"), (r"(?i)FACEBOOK", "meta"), (r"(?-i:face)book", "meta"),
                           (r"(?s)^facebook\.com$", "meta"), (r"a{1000}", None), (r"(?:a{10}){100}", None),
                           (r"\_\-\.\/facebook", None), (r"\x66acebook\.com", "meta"), (r"\0", None),
                           (r"[\x66]acebook\.com", "meta"), (r"\tfacebook", None), (r"x{1, 3}", None)):
            with self.subTest(expr=expr):
                self.assertEqual(rx(expr), (want, None))

    def test_future_warning_reaches_the_parser(self):
        """B4: these reach the parser of re, which warns (FutureWarning) — nothing gets into the output."""
        for expr in (r"[[]a", r"[a-z--b]", r"[a&&b]x", r"[a||b]x", r"[a~~b]x"):
            with self.subTest(expr=expr):
                with warnings.catch_warnings(record=True) as w:
                    warnings.simplefilter("always")
                    self.assertIsNone(rfmark.regexp_entry(expr)[1])
                self.assertEqual(w, [])


class RegexpReview3Test(unittest.TestCase):
    """REV-s1-w0-3: the literal tail of an expression (Н1), a nested repeat with a separator it cannot eat (Н2), a deep
    expression (Н5), what RE2 reads otherwise (Н6). The samples of the review."""

    def test_literal_tail_only(self):
        """Н1: a literal part marks only when it ENDS the name (``$``/``\\z`` or the end of the expression, REV-4 З1)
        with a domain of the registry on a label boundary, case as RE2 reads it (REV-4 П1: an upper-case letter
        outside a class finds no name); a label equal to a «word» marks nothing in a regexp (only in keyword:)."""
        rx = rfmark.regexp_entry
        for expr in (r"^(.+\.)?meta\.ua$", r"^meta\.wikimedia\.org$", r"^threads\.example\.org$",
                     r"^messenger\.example\.org$", r"^workplace\.example\.org$", r"^llama\.example\.org$",
                     r"^oculus\.example\.org$", r"^api\.meta\.example\.org$", r"^facebook\.com\.example\.org$",
                     r"^fb\.com\.example\.org$", r"^m\.me\.example\.org$", r"facebook\.com\.example\.org",
                     r"^www\.FACEBOOK\.com$", r"^FACEBOOK\.COM$", r"^www\.facebook\.com[a-z]+$",
                     r"^[a-z]+facebook\.com$", r"^x(-y)?facebook\.com$", r"\bfacebook\.com\.example\.org$",
                     r"(?i)^www\.(?-i:FACEBOOK)\.com$", r"^.*WWW\.facebook\.com$", r"^A.*\.facebook\.com$",
                     r"^(?:B|C)?WWW\.facebook\.com", r"^(.+\.)?whatsapp\.(com|net)$", r"^mmg\.whatsapp\.net$", r"^(.*\.)?wa\.me$"):
            with self.subTest(expr=expr):
                self.assertEqual(rx(expr), (None, None))
        self.assertIsNone(rfmark.org_of_entry("domain", "meta.ua"))
        self.assertIsNone(rfmark.org_of_entry("keyword", "meta.ua"))
        marked = {r"^www\.facebook\.com$": "meta", r"^(www|m)\.instagram\.com$": "meta",
                  r"^static\.xx\.fbcdn\.net$": "meta", r"^edge-chat\.(facebook|messenger)\.com$": "meta",
                  r"^scontent(-[a-z0-9]+)?\.xx\.fbcdn\.net$": "meta",
                  r"^instagram\.f[a-z]{3}\d+-\d+\.fna\.fbcdn\.net$": "meta",
                  r"^(.+\.)?(facebook|instagram)\.com$": "meta", r"^(.+\.)*facebook\.com$": "meta",
                  r"^([a-z0-9-]+\.)+facebook\.com$": "meta", r"(^|\.)facebook\.com$": "meta",
                  r"\.fbcdn\.net$": "meta", r"^.+\.fbcdn\.net$": "meta", r"^(www\.)?navalny\.com$": "fbk",
                  r"(?i)^www\.FACEBOOK\.com$": "meta", r"^a\.example\.org$|^b\.fbcdn\.net$": "meta",
                  r"^(?:a\.example\.org|b\.fbcdn\.net)$": "meta", r"^www\.facebook\.com\z": "meta",
                  r"^static\.xx\.fbcdn\.net(\.example\.org)?$": "meta", r"^(www\.)?face(book)?\.com$": "meta",
                  r"^www\.(?i:FACEBOOK)\.com$": "meta", r"(?i)^.*WWW\.facebook\.com$": "meta",
                  # REV-s1-w0-4 З1: the end of the expression is «$» — what matches with it matches without it too
                  r"^graph\.instagram\.com": "meta", r"^edge-mqtt\.facebook\.com": "meta",
                  r"^static\.xx\.fbcdn\.net": "meta", r"^scontent.*\.fbcdn\.net": "meta",
                  r"scontent-[a-z0-9-]+\.xx\.fbcdn\.net": "meta",
                  r"^instagram\.f[a-z]{3}\d+-\d+\.fna\.fbcdn\.net": "meta",
                  r"^www\.facebook\.com|^x\.example\.org$": "meta", r"^(?:x\.example\.org|www\.fbcdn\.net)": "meta",
                  r"^(www\.)?facebook\.com(\.)?": "meta"}
        # without a worker only what needs no run can mark: these are marked by the literal tail alone
        with mock.patch.object(rfmark.sys, "executable", "/nonexistent/python3"):
            for expr, org in marked.items():
                with self.subTest(expr=expr):
                    self.assertEqual(rx(expr), (org, None))
            for expr in (r"^meta\.wikimedia\.org$", r"^static\.xx\.fbcdn\.net\.", r"^meta\.ua",
                         r"^facebook\.com\.example\.org", r"^www\.facebook\.co", r"^www\.facebook\.com\b",
                         r"^www\.facebook\.com(?:x)+"):
                with self.subTest(expr=expr):                                    # needs a run: no literal mark
                    self.assertEqual(rx(expr), (None, "timeout"))
        reg = rfmark.Registry(_doc())
        self.assertEqual(reg.regexp_entry(r"^(www\.)?chat\.example\.com$"), (None, None))   # exclude is stronger

    def test_unambiguous_separator_is_not_dangerous(self):
        """Н2: ``([a-z0-9-]+\\.)+`` — the class cannot eat the dot after it, one way to split: not «dangerous»."""
        rx = rfmark.regexp_entry
        for expr in (r"^([a-z0-9-]+\.)+example\.com$", r"^(?:[a-z0-9-]+\.)*example\.com$", r"^(\w+\.)+example\.com$",
                     r"^([^.]+\.)+example\.com$", r"^(a+b)+$", r"^([a-z]+\.[0-9]+-)+x$"):
            with self.subTest(expr=expr):
                self.assertEqual(rx(expr), (None, None))
        self.assertEqual(rx(r"^([a-z0-9-]+\.)+fb(cdn|sbx)\.net$"), ("meta", None))
        for expr in (r"^([a-z0-9.-]+\.)+x$", r"^(.+\.)+x$", r"^([a-z]+\.[a-z]+)+x$", r"(a+b?)+$", r"(\w+\w)+$",
                     r"(a+a)+$", r"([a-z]+[a-z.]\.)+x$", r"(?i)(a+A)+$", r"((a+\.)+)+$", r"([^.]+[.a])+$",
                     r"(\d+1)+$", r"(\s+ )+$", r"([\w.]+\.)+x$"):
            with self.subTest(expr=expr):
                self.assertEqual(rx(expr), (None, "dangerous"))

    def test_deep_expression_is_refused_not_raised(self):
        """Н5: 255 nested groups — a refusal on every Python and at any depth of the caller, never a RecursionError
        out of the parse."""
        deep = "(" * 255 + "a" + ")" * 255
        d = rfmark.REGEXP_DEPTH_MAX
        ok = "^" + "(" * d + r"www\.facebook\.com" + ")" * d + "$"            # a literal mark: no run
        over = "^" + "(" * (d + 1) + r"www\.facebook\.com" + ")" * (d + 1) + "$"
        self.assertEqual(rfmark.regexp_entry(deep), (None, "long"))
        self.assertEqual(rfmark.regexp_entry(over), (None, "long"))
        self.assertEqual(rfmark.regexp_entry(ok), ("meta", None))
        self.assertEqual(rfmark.regexp_entry(r"[(((((((((((((((((((((((((((((((((((((]"), (None, None))
        self.assertEqual(rfmark.regexp_entry("\\(" * 40), (None, None))
        reg = rfmark.default()
        got = []

        def at_depth(n):
            if n:
                return at_depth(n - 1)
            for e in (deep, over, ok, "(?:" * 20 + "(.*)" + ")*" * 20 + "z"):
                got.append(reg.regexp_entry(e))

        depth = len(__import__("inspect").stack(0))
        at_depth(sys.getrecursionlimit() - depth - 60)
        self.assertEqual([g[1] for g in got[:2]], ["long", "long"])
        self.assertIn(got[2], [("meta", None), (None, "long")])
        self.assertIn(got[3][1], ("dangerous", "long"))

    def test_static_checks_stay_linear(self):
        """Each way through the alternatives that end the expression is read once: ``(|)`` × 170 is not 2^170 ways.
        REV-s1-w0-4 З3: ``rest`` of _label_start is computed once — ``(|\\b)`` × 100 is not 2^100 calls; the time of
        the checks in this process is bounded and the answer is not «long» where the expression is a lawful one."""
        legal = ("^" + r"(|\b)" * 100 + "x$", "^" + r"(\b|)" * 100 + "x$", "^" + r"(|\.)" * 100 + "x$", "(|$)" * 127,
                 "^" + r"(\b|\B|\b\B|\B\b|)" * 7 + "x$", "(|)" * 170, "(a|)" * 128, "((|)|(|))" * 56)
        with mock.patch.object(rfmark.sys, "executable", "/nonexistent/python3"):
            for expr in legal + ("(a$|b$)" * 70, "^" + "(a|b)" * 100 + "$", "(?:\\.)?" * 70 + "x$",
                                 "^" + "(ab|cd)" * 60 + "$"):
                with self.subTest(expr=expr[:20]):
                    t0 = time.monotonic()
                    got = rfmark.regexp_entry(expr)
                    self.assertLess(time.monotonic() - t0, 0.05)
                    if expr in legal:
                        self.assertEqual(got, (None, "timeout"))     # read without a run, then the run: no worker
        calls = [0]
        real = rfmark._label_start

        def counted(*a, **kw):
            calls[0] += 1
            if calls[0] > 2000:
                raise AssertionError("_label_start: more than 2000 calls — rest is not computed once")
            return real(*a, **kw)

        reg = rfmark.default()
        with mock.patch.object(rfmark, "_label_start", counted):
            # the tail «x.com» may still end a name of the registry (R3: a hopeless tail such as «x» is not walked)
            for expr in ("^" + r"(\b|\B|\b\B|\B\b|)" * 7 + r"x\.com$", "^" + r"(|\b)" * 7 + r"x\.com$"):
                with self.subTest(expr=expr[:20]):
                    calls[0] = 0
                    self.assertIsNone(reg._literal_org(rfmark._regexp_prepare(expr)[1]))
                    self.assertGreater(calls[0], 0)
            for expr in ("(|$)" * 127, "(|)" * 170, "(|\\b)" * 100):       # no literal tail: no walk to the left
                with self.subTest(expr=expr[:20]):
                    calls[0] = 0
                    self.assertIsNone(reg._literal_org(rfmark._regexp_prepare(expr)[1]))
                    self.assertEqual(calls[0], 0)

    def test_static_checks_do_not_depend_on_the_caller_stack(self):
        """REV-s1-w0-4 З2: the walk to the left of a tail is bounded by REGEXP_DEPTH_MAX calls, not by the length of
        the expression: the same answer at any depth of the caller, never «long» for a lawful expression."""
        reg = rfmark.default()
        exprs = ("(|$)" * 127, "(|$)" * 80, "(|$)" * 40, "^" + r"(|[a-z]+\.)" * 40 + r"fbcdn\.net$",
                 "^" + r"(|\b)" * 100 + "x$", "(|)" * 170, r"^(.+\.)?x\.fbcdn\.net$", r"^graph\.instagram\.com")

        def static(e):
            py, tree, why = rfmark._regexp_prepare(e)
            if why:
                return why
            try:
                org = reg._literal_org(tree)
                if org:
                    return org
                return "dangerous" if rfmark._dangerous(tree) else "run"
            except RecursionError:
                return "long"

        def at_depth(n, e):
            return at_depth(n - 1, e) if n else static(e)

        for expr in exprs:
            with self.subTest(expr=expr[:20]):
                first = static(expr)
                self.assertNotEqual(first, "long")
                self.assertEqual([at_depth(n, expr) for n in (100, 300)], [first, first])
        self.assertEqual(static(r"^(.+\.)?x\.fbcdn\.net$"), "meta")

    def test_re2_reads_otherwise(self):
        """Н6: a count with a leading zero is a literal in RE2 (Go parseInt), a repeat in Python — «syntax»."""
        rx = rfmark.regexp_entry
        for expr in (r"a{01}", r"a{00}", r"a{1,02}", r"x{01,}", r"^facebook\.com{01}$"):
            with self.subTest(expr=expr):
                self.assertEqual(rx(expr), (None, "syntax"))
        for expr in (r"a{0}", r"a{10}", r"a{0,1}", r"a{1,10}", r"[{01}]", r"\{01}"):
            with self.subTest(expr=expr):
                self.assertIsNone(rx(expr)[1])


class ScanTest(unittest.TestCase):

    def test_scan_of_a_parsed_geosite(self):
        """§9: category-geoblock-ru with meta.com, keyword:meta.ai, example.com -> 2 marked entries."""
        raw = geosite_dat({"CATEGORY-GEOBLOCK-RU": ["meta.com", (dat.PLAIN, "meta.ai"), "example.com",
                                                    (dat.PLAIN, "cdn")],
                           "CATEGORY-RU": ["example.org", (dat.FULL, "web.whatsapp.com")],
                           "CATEGORY-ADS": [(dat.REGEX, r"\.fbcdn\.net$"), "ads.example.com",
                                            (dat.REGEX, r"^(.*.*)*z$"), (dat.REGEX, r"\pL+")],
                           "YOUTUBE": ["youtube.com"],
                           "UNRELATED": ["facebook.com"]})
        stats = {}
        got = rfmark.scan(dat.geosite(raw), stats=stats)
        self.assertEqual(set(got), {"force", "direct", "block"})
        self.assertEqual(got["force"], [("category-geoblock-ru", "domain", "meta.com", "meta"),
                                        ("category-geoblock-ru", "keyword", "meta.ai", "meta")])
        self.assertEqual(got["direct"], [])
        self.assertEqual(got["block"], [("category-ads", "regexp", r"\.fbcdn\.net$", "meta")])
        self.assertEqual(stats["entries"], 11)                # UNRELATED is no built-in group
        self.assertEqual(stats["regexp"], 3)
        self.assertEqual(stats["regexp_unchecked"], 2)
        self.assertEqual(stats["regexp_unchecked_by"],
                         {"long": 0, "dangerous": 1, "syntax": 1, "timeout": 0, "budget": 0})
        self.assertEqual(stats["unknown"], 0)

    def test_unknown_types_are_counted(self):
        """B7: an entry of a type of a newer rules.dat is counted in stats, not guessed and not dropped silently."""
        stats = {}
        got = rfmark.scan({"category-ads": [(77, "facebook.com"), (77, "facebook.com"), ("weird", "x"),
                                            (dat.DOMAIN, "meta.com")]}, stats=stats)
        self.assertEqual([r[2] for r in got["block"]], ["meta.com"])
        self.assertEqual((stats["entries"], stats["unknown"]), (3, 2))

    def test_scan_empty(self):
        self.assertEqual(rfmark.scan({}), {"force": [], "direct": [], "block": []})
        self.assertEqual(rfmark.scan(None), {"force": [], "direct": [], "block": []})

    def test_regexp_budget(self):
        """REV-s1-w0-3 Н3: the budget stops only the runs in the worker; a literal mark, long, syntax and dangerous
        need no run and are counted after the budget is spent too."""
        stats = {}
        rows = [(dat.REGEX, r"^x\.[a-z]+\.net$"), (dat.REGEX, r"^www\.facebook\.com$"),
                (dat.REGEX, r"^(.+\.)?fbcdn\.net$"), (dat.REGEX, r"\pL"), (dat.REGEX, r"(a+)+$"),
                (dat.REGEX, "f" * 513), (dat.REGEX, r"^y\.[a-z]+\.net$"), (dat.DOMAIN, "meta.com")]
        with mock.patch.object(rfmark, "REGEXP_BUDGET_S", -1.0):
            got = rfmark.scan({"category-geoblock-ru": rows}, stats=stats)
        self.assertEqual([r[2] for r in got["force"]], [r"^www\.facebook\.com$", r"^(.+\.)?fbcdn\.net$", "meta.com"])
        self.assertEqual(stats["regexp_unchecked_by"],
                         {"long": 1, "syntax": 1, "dangerous": 1, "timeout": 0, "budget": 2})
        self.assertEqual(stats["regexp_unchecked"], 5)


class TextsTest(unittest.TestCase):

    def test_names(self):
        self.assertEqual(rfmark.names(["meta", "fbk"], "ru"), "Meta, ФБК")
        self.assertEqual(rfmark.names(["meta", "fbk", "meta"], "en"), "Meta, FBK")
        self.assertEqual(rfmark.names([], "ru"), "")

    def test_block_and_consent_owner_wording(self):
        """§7.1, §7.2 — the owner's words, verbatim."""
        self.assertEqual(rfmark.block_text(["meta"], "ru"),
                         "⚠ Данная организация признана экстремистской на территории Российской Федерации (Meta)")
        self.assertEqual(rfmark.block_text(["meta", "fbk"], "ru"),
                         "⚠ Данная организация признана экстремистской на территории Российской Федерации (Meta, ФБК)")
        self.assertEqual(rfmark.block_text(["meta"], "en"),
                         "⚠ This organisation is recognised as extremist in the Russian Federation (Meta)")
        self.assertEqual(rfmark.consent_text("ru"), "Скрипт не используется на территории РФ")
        self.assertEqual(rfmark.consent_text("en"), "The script is not used in the Russian Federation")

    def test_badge_en(self):
        """The mark in English is «⚠RU» (REV-s1-w0 П6), in Russian «⚠РФ»."""
        with use_lang("en"):
            with self.assertRaises(errors.KaskadError) as cm:
                rfmark.load("/nonexistent/rf_mark.json")
        self.assertIn("⚠RU", str(cm.exception))
        self.assertNotIn("⚠RF", str(cm.exception))

    def test_catalog(self):
        self.assertEqual(icheck.catalog_problems(["rules"]), [])
        icheck.assert_no_cyrillic(self, os.path.join(os.path.dirname(rfmark.__file__), "rfmark.py"))


class BrokenRegistryTest(unittest.TestCase):
    """A broken file is a KaskadError with a RU/EN text — never «nothing is marked»."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def _write(self, doc, raw=None):
        p = os.path.join(self.tmp, "rf_mark.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(raw if raw is not None else json.dumps(doc))
        return p

    def _broken(self, doc=None, raw=None):
        p = self._write(doc, raw) if (doc is not None or raw is not None) else os.path.join(self.tmp, "none.json")
        with use_lang("ru"):
            with self.assertRaises(errors.KaskadError) as cm:
                rfmark.load(p)
        self.assertIn("Реестр ⚠РФ", str(cm.exception))
        self.assertNotIn(p, str(cm.exception))                 # REV-338 П-2: the path — in the journal and data only
        self.assertNotIn("/", str(cm.exception))
        self.assertEqual(cm.exception.data["path"], p)
        with use_lang("en"):
            with self.assertRaises(errors.KaskadError) as cm:
                rfmark.load(p)
        self.assertIsNone(CYR.search(str(cm.exception)), str(cm.exception))
        return cm.exception

    def test_good_small_registry(self):
        self.assertEqual(rfmark.load(self._write(_doc())).org_of("example.com"), "acme")
        d = _doc()
        del d["sources"][0]["checked"]                    # a source nobody could open has no «checked»
        self.assertEqual(rfmark.load(self._write(d)).org_of("example.com"), "acme")

    def test_broken(self):
        self._broken()                                    # no file
        self._broken(raw="{not json")
        self._broken(raw="[]")
        self._broken(raw="")
        cases = []
        d = _doc(); d["schema"] = 2; cases.append(d)
        d = _doc(); d["orgs"] = []; cases.append(d)
        d = _doc(); d["sources"] = []; cases.append(d)
        cases.append(_doc(domains=["Example.com"]))       # not normalised
        cases.append(_doc(domains=["example.com", "example.com"]))
        cases.append(_doc(domains="example.com"))
        cases.append(_doc(domains=["not a domain"]))
        cases.append(_doc(domains=["com"]))               # a whole zone
        cases.append(_doc(exclude=["example.com"]))       # both in domains and exclude
        cases.append(_doc(source="nowhere"))
        cases.append(_doc(since="21.03.2022"))
        cases.append(_doc(names={"ru": "Акме"}))
        cases.append(_doc(id="Bad Id"))
        cases.append(_doc(also=[{"status": "terrorist", "since": "soon"}]))
        cases.append(_doc(also=5))
        cases.append(_doc(also=None))
        cases.append(_doc(since="20200102"))              # fromisoformat of 3.11+ takes it, the registry does not
        cases.append(_doc(since="2020-W01-4"))
        d = _doc(); d["orgs"].append(copy.deepcopy(d["orgs"][0])); cases.append(d)    # id twice
        for doc in cases:
            with self.subTest(doc=json.dumps(doc)[:120]):
                e = self._broken(doc)
                self.assertTrue(e.data.get("error"))


if __name__ == "__main__":
    unittest.main()

"""English in the web (3.3.2, k3/handoff/I18N-3.3.2.md, docs/k3/GLOSSARY-en.md): the dictionaries of the panel, the
«Схема», the quick setup and the person's page come in pairs — every Russian key has its English one and back, the
same {placeholders}, plural forms one|other, no Cyrillic in English; no Cyrillic in the code of the pages outside the
dictionaries (a short list of allowed places); the pages take the language of the server (<html lang>), the person's
page — the visitor's cookie; the language of the server changes only with the admin's or the wizard's session."""

import os
import re
import unittest
from unittest import mock

from kaskad.panel import lang as weblang
from tests.panel.panelcase import PanelCase
from tests.setup.test_setup_web import SetupWebCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CYR = re.compile("[А-Яа-яЁё]")

PAIRS = (("i18n-ru.js", "i18n-en.js"), ("graph-i18n-ru.js", "graph-i18n-en.js"),
         ("setup/setup-i18n-ru.js", "setup/setup-i18n-en.js"), ("user-i18n-ru.js", "user-i18n-en.js"),
         ("give-i18n-ru.js", "give-i18n-en.js"), ("kit-i18n-ru.js", "kit-i18n-en.js"),
         ("pages/dev-kit-i18n-ru.js", "pages/dev-kit-i18n-en.js"),
         ("pages/exits-groups-i18n-ru.js", "pages/exits-groups-i18n-en.js"), ("pages/speed-i18n-ru.js", "pages/speed-i18n-en.js"),
         ("pages/ovb-i18n-ru.js", "pages/ovb-i18n-en.js"))
# names of the languages are written in their own language in both dictionaries
OWN_NAME = {"lk.langRu", "c.langRu", "lang_ru"}


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


# ── a tiny reader of the dictionaries: strings, comments, regexes skipped the way the browser does ──

_TOKEN = re.compile(r"""\s+|//[^\n]*|/\*.*?\*/|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|[A-Za-z_$][\w$.]*|[{}:,;=()\[\]]|.""",
                    re.S)


def _unq(tok):
    body = tok[1:-1]
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), body)


def _tokens(src):
    for m in _TOKEN.finditer(src):
        t = m.group(0)
        if t.isspace() or t.startswith("//") or t.startswith("/*"):
            continue
        yield t


def dictionary(name):
    """{key: text} of the first object literal of the file (nested objects: «group.key»)."""
    toks = list(_tokens(read(name)))
    i = toks.index("{", next(k for k, t in enumerate(toks) if t in ("=",) and toks[k + 1] == "{"))
    out = {}

    def obj(i, prefix):
        assert toks[i] == "{"
        i += 1
        while toks[i] != "}":
            key = _unq(toks[i]) if toks[i][0] in "\"'" else toks[i]
            assert toks[i + 1] == ":", (name, key, toks[i + 1])
            i += 2
            if toks[i] == "{":
                i = obj(i, prefix + key + ".")
            else:
                assert toks[i][0] in "\"'", (name, key, toks[i])
                out[prefix + key] = _unq(toks[i])
                i += 1
            if toks[i] == ",":
                i += 1
        return i + 1

    obj(i, "")
    return out


def holes(text):
    return set(re.findall(r"\{(\w+)\}", text))


class DictionaryPairsTest(unittest.TestCase):
    def test_same_keys(self):
        for ru, en in PAIRS:
            a, b = dictionary(ru), dictionary(en)
            self.assertTrue(len(a) > (0 if ru.startswith(("kit-", "pages/")) else 20), ru)   # Kit и разделы (F0) — маленькие
            self.assertEqual(sorted(set(a) - set(b)), [], "нет в %s" % en)
            self.assertEqual(sorted(set(b) - set(a)), [], "лишние в %s" % en)

    def test_same_placeholders(self):
        bad = []
        for ru, en in PAIRS:
            a, b = dictionary(ru), dictionary(en)
            for k in a:
                if k in b and holes(a[k]) != holes(b[k]):
                    bad.append("%s %s: %s ≠ %s" % (en, k, sorted(holes(a[k])), sorted(holes(b[k]))))
        self.assertEqual(bad, [])

    def test_plural_forms(self):
        """«one|few|many» → «one|other»: an English text has a form for one and one for the rest."""
        bad = []
        for ru, en in PAIRS:
            a, b = dictionary(ru), dictionary(en)
            for k in a:
                if k in b and a[k].count("|") == 2 and b[k].count("|") not in (1, 2):
                    bad.append("%s %s" % (en, k))
        self.assertEqual(bad, [])

    def test_english_is_english(self):
        bad = []
        for _, en in PAIRS:
            for k, v in dictionary(en).items():
                if k not in OWN_NAME and CYR.search(v):
                    bad.append("%s %s" % (en, k))
                if "<" in v and re.search(r"<[a-z/]", v) and k not in ("x.ipPh",):
                    bad.append("%s %s: разметка" % (en, k))
        self.assertEqual(bad, [])

    def test_names_of_the_languages(self):
        for ru, en in PAIRS[0:1] + PAIRS[2:]:
            a, b = dictionary(ru), dictionary(en)
            for k in OWN_NAME & set(a):
                self.assertEqual(a[k], b[k], k)


# ── no Cyrillic in the code of the pages ─────────────────────────────────────

def literals(src):
    """(line, literal) of every string, template and regex literal outside comments."""
    out, i, n, prev = [], 0, len(src), ""
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            i = src.find("*/", i + 2) + 2
            continue
        if c in "'\"`":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append((src.count("\n", 0, i) + 1, src[i:j + 1]))
            i, prev = j + 1, "a"
            continue
        if c == "/" and prev in "(,=:[!&|?{};+-*%<>~^":
            j, klass = i + 1, False
            while j < n and src[j] != "\n":
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "[":
                    klass = True
                elif src[j] == "]":
                    klass = False
                elif src[j] == "/" and not klass:
                    break
                j += 1
            out.append((src.count("\n", 0, i) + 1, src[i:j + 1]))
            i, prev = j + 1, "a"
            continue
        if not c.isspace():
            prev = "a" if (c.isalnum() or c in "_$") else c
        i += 1
    return out


CODE = ("app.js", "graph.js", "user.js", "boot.js", "qr.js", "icons.js", "setup/setup.js", "kit.js", "templates.js",
        "pages/dev-kit.js")
# allowed: the strength of a password counts Cyrillic letters too
ALLOWED = {("setup/setup.js", "/[a-zа-яё]/"), ("setup/setup.js", "/[A-ZА-ЯЁ]/"),
           ("setup/setup.js", "/[^A-Za-zА-Яа-яЁё0-9]/")}


class NoCyrillicInCodeTest(unittest.TestCase):
    def test_scripts(self):
        bad = []
        for name in CODE:
            for line, lit in literals(read(name)):
                if CYR.search(lit) and (name, lit) not in ALLOWED:
                    bad.append("%s:%d %s" % (name, line, lit[:80]))
        self.assertEqual(bad, [], "тексты — только в словарях")

    def test_pages(self):
        """The HTML of the pages has no Russian text outside comments: a <noscript> says it in both languages."""
        for name in ("index.html", "setup/index.html", "user.html"):
            html = re.sub(r"<!--.*?-->", "", read(name), flags=re.S)
            for m in re.finditer(r"<noscript>(.*?)</noscript>", html, flags=re.S):
                self.assertRegex(m.group(1), r"[A-Za-z]{4}", name)
            html = re.sub(r"<noscript>.*?</noscript>", "", html, flags=re.S)
            self.assertIsNone(CYR.search(html), "%s: %s" % (name, CYR.search(html) and html[CYR.search(html).start() - 40:][:80]))


# ── where the language comes from ────────────────────────────────────────────

class PagesTakeTheServerLanguageTest(unittest.TestCase):
    def test_marks(self):
        self.assertIn('<html lang="__KASKAD_LANG__"', read("index.html"))
        self.assertIn('<html lang="__KASKAD_LANG__"', read("setup/index.html"))
        u = read("user.html")
        self.assertIn('<html lang="{{LANG}}">', u)
        self.assertIn("/p/user-i18n-{{LANG}}.js?v={{V_I18N}}", u)

    def test_boot_reads_html_lang_and_scripts_send_it(self):
        self.assertIn('getAttribute("lang")', read("boot.js"))
        for name in ("app.js", "setup/setup.js", "user.js"):
            self.assertRegex(read(name), r"[\"']X-Kaskad-Lang[\"']\s*:\s*LANG", name)
        self.assertIn('loadScript("i18n-en.js")', read("app.js"))
        self.assertIn("setup-i18n-en.js", read("setup/setup.js"))
        self.assertIn("graph-i18n-en.js", read("graph.js"))

    def test_static_response_fills_the_language_and_the_etag(self):
        from kaskad.panel import routes, server
        req = routes.Request("GET", "/", {}, {}, b"", None, None, "127.0.0.1", "http")
        with mock.patch.object(weblang, "server_lang", return_value="en"):
            en = server.static_response(req, "index.html")
        with mock.patch.object(weblang, "server_lang", return_value="ru"):
            ru = server.static_response(req, "index.html")
        self.assertIn(b'<html lang="en"', en.body)
        self.assertIn(b'<html lang="ru"', ru.body)
        self.assertNotIn(b"__KASKAD_LANG__", en.body)
        self.assertNotEqual(en.headers["ETag"], ru.headers["ETag"])
        self.assertTrue(en.headers["ETag"].endswith('-en"'))

    def test_user_shell_language(self):
        from kaskad.panel import userpage
        body = userpage.render_shell("Anna", "n" * 32, "ios", False, lang="en").decode("utf-8")
        self.assertIn('<html lang="en">', body)
        self.assertIn("/p/user-i18n-en.js?v=", body)
        with mock.patch.object(weblang, "server_lang", return_value="ru"):
            body = userpage.render_shell("Anna", "n" * 32, "ios", False, lang=None).decode("utf-8")
        self.assertIn('<html lang="ru">', body)
        self.assertIn("/p/user-i18n-ru.js?v=", body)

    def test_reading_the_language(self):
        self.assertEqual(weblang.request_lang({"x-kaskad-lang": "en"}), "en")
        self.assertEqual(weblang.request_lang({"x-kaskad-lang": "EN-us"}), "en")
        self.assertIsNone(weblang.request_lang({"x-kaskad-lang": "de"}))
        self.assertIsNone(weblang.request_lang({}))
        self.assertEqual(weblang.cookie_lang({"cookie": "a=1; kaskad_lang=en; b=2"}), "en")
        self.assertIsNone(weblang.cookie_lang({"cookie": "kaskad_lang=fr"}))


class PanelLanguageNeedsTheAdminTest(PanelCase):
    """POST /api/lang: without the admin's session (or without the CSRF header) — refused, the language stays."""

    def test_refused_without_the_admin(self):
        with mock.patch.object(weblang, "set_server_lang") as setter:
            self.assertEqual(self.call("POST", "/api/lang", {"lang": "en"}, tunnel=False).status, 401)
            self.assertEqual(self.call("POST", "/api/lang", {"lang": "en"}, csrf=False).status, 403)
            setter.assert_not_called()

    def test_the_admin_changes_it(self):
        with mock.patch.object(weblang, "set_server_lang", return_value="en") as setter:
            r = self.call("POST", "/api/lang", {"lang": "en"})
            self.assertEqual(r.status, 200, r)
            self.assertEqual(r.json()["lang"], "en")
            setter.assert_called_once_with("en")
            self.assertEqual(self.call("POST", "/api/lang", {"lang": "de"}).status, 400)
            setter.assert_called_once_with("en")


class WizardLanguageNeedsItsSessionTest(SetupWebCase):
    """POST /api/setup/lang: only with the wizard's session — the screens of the link and the password have no switch."""

    def test_refused_without_a_session(self):
        with mock.patch.object(weblang, "set_server_lang") as setter:
            self.assertEqual(self.scall("POST", "/api/setup/lang", {"lang": "en"}).status, 401)
            self.assertEqual(self.scall("POST", "/api/setup/lang", {"lang": "en"}, cookie="x" * 30).status, 401)
            setter.assert_not_called()

    def test_the_session_changes_it(self):
        tok, _, _ = self.enter()
        with mock.patch.object(weblang, "set_server_lang", return_value="en") as setter:
            r = self.scall("POST", "/api/setup/lang", {"lang": "en"}, cookie=tok)
            self.assertEqual(r.status, 200, r)
            setter.assert_called_once_with("en")


class JobsSpeakTheLanguageTest(unittest.TestCase):
    def test_systemd_run_gets_the_language(self):
        from kaskad.setup import jobs
        seen = []

        class R(object):
            def which(self, name):
                return "/usr/bin/" + name

            def run(self, argv, timeout=None, **kw):
                seen.append(argv)
                return mock.Mock(ok=True, rc=0, out="", err="")

        from kaskad import i18n
        with mock.patch.dict(os.environ, {"INVOCATION_ID": "x"}), mock.patch.object(jobs, "LAUNCH", None), \
                i18n.use_lang("en"):
            jobs._launch("abc", "protocols", R())
        self.assertIn("--setenv=KASKAD_LANG=en", seen[0])


if __name__ == "__main__":
    unittest.main()


# ── texts of the server for the web: the catalogs setup.json and panel.json ──

CATALOG = os.path.join(ROOT, "lib", "kaskad", "i18n", "catalog")
AREAS = {"setup": os.path.join(ROOT, "lib", "kaskad", "setup"), "panel": os.path.join(ROOT, "lib", "kaskad", "panel")}


def catalog(lang, area):
    import json
    with open(os.path.join(CATALOG, lang, area + ".json"), encoding="utf-8") as f:
        return json.load(f)


class ServerCatalogsTest(unittest.TestCase):
    def test_pairs(self):
        for area in AREAS:
            ru, en = catalog("ru", area), catalog("en", area)
            self.assertGreater(len(ru), 100, area)
            self.assertEqual(sorted(set(ru) ^ set(en)), [], area)
            for k in ru:
                self.assertTrue(k.startswith(area + "."), k)
                self.assertEqual(holes(ru[k]), holes(en[k]), k)
                self.assertIsNone(CYR.search(en[k]), "en %s" % k)
                if ru[k].count("|") == 2:
                    self.assertEqual(en[k].count("|"), 1, "формы числа: %s" % k)

    def test_every_key_of_the_code_is_there(self):
        """Every «setup.…»/«panel.…» literal of the Python of the area (tr/trn calls and the keys kept in constants) is
        in the catalog; every key of the catalog is used (keys built from a word — listed)."""
        import ast
        built = re.compile(r"^panel\.scheme\.(pool|direct|block)$")
        for area, d in AREAS.items():
            ru = catalog("ru", area)
            used = set()
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(".py"):
                    continue
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    tree = ast.parse(f.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                            and re.match(r"^%s\.[a-z][A-Za-z0-9]*\.[A-Za-z0-9_.]+$" % area, node.value):
                        used.add(node.value)
                        self.assertIn(node.value, ru, "%s: нет в каталоге %s" % (fn, node.value))
            unused = sorted(k for k in set(ru) - used if not built.match(k))
            self.assertEqual(unused, [], "ключи каталога %s без кода" % area)

    def test_tags_of_the_protocol_cards(self):
        """setup.js colours the tags of the protocol cards by its dictionary (pr.tagRec, pr.tagWeak)."""
        from kaskad.setup import product
        for lang, dic in (("ru", "setup/setup-i18n-ru.js"), ("en", "setup/setup-i18n-en.js")):
            words = dictionary(dic)
            cat = catalog(lang, "setup")
            self.assertEqual(cat[product.TAG_REC], words["pr.tagRec"], lang)
            self.assertEqual(cat[product.TAG_WEAK], words["pr.tagWeak"], lang)


class SseDropsTheServiceFieldTest(unittest.TestCase):
    def test_i18n_of_live_json_is_not_streamed(self):
        from kaskad.panel import sse
        self.assertNotIn("i18n", sse._view({"exits": {}, "seq": 3, "i18n": {"lang": "ru"}}))

"""The page of a person (WP-U): the static files (what a browser gets) and ``kaskadctl page``."""

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import unittest

from kaskad import cli, errors, secrets
from kaskad.panel import userpage
from tests.userpage_case import HOST, NAME1, NAME2, UID1, UID2, PageCase

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def slurp(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


class StaticFilesTest(unittest.TestCase):
    NAMES = ("user.html", "user.css", "user.js", "user-i18n-ru.js")

    def test_files_are_there(self):
        for n in self.NAMES:
            self.assertTrue(os.path.isfile(os.path.join(STATIC, n)), n)

    def test_html_is_a_shell_with_marks_and_no_inline_code(self):
        h = slurp("user.html")
        for mark in ("NONCE", "OS", "STATE", "NAME", "V_CSS", "V_JS", "V_I18N"):
            self.assertIn("{{%s}}" % mark, h)
        self.assertRegex(h, r'<html lang="\{\{LANG\}\}">')                 # the visitor's or the server's language (3.3.2)
        self.assertIn("viewport-fit=cover", h)
        self.assertNotRegex(h, r"<script(?![^>]*\bsrc=)")                  # every script is a file
        self.assertNotRegex(h, r"<style")
        self.assertNotRegex(h, r"\son[a-z]+\s*=")                          # no inline handlers
        self.assertNotIn("style=", h)
        self.assertNotRegex(h, r"https?://")
        for src in re.findall(r'(?:src|href)="([^"]+)"', h):
            if re.match(r"^\?lang=(ru|en)$", src):                        # RU · EN: the same page in another language
                continue
            if src == "data:,":                                            # the empty icon of the panel: no request
                continue
            self.assertTrue(src.startswith("{{BASE}}/p/"), src)            # own files only, under the prefix
        self.assertNotRegex(h, r"(PRIVKEY|PASSWD|vpn://|BEGIN)")

    def test_js_does_not_build_html_from_text(self):
        js = slurp("user.js")
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                    "javascript:", "localStorage", "sessionStorage", "document.cookie", "XMLHttpRequest", "importScripts",
                    "createContextualFragment"):
            self.assertFalse(bad in js, bad)
        self.assertNotRegex(js, r"setAttribute\(\s*['\"]on", "inline handler set from script")
        self.assertIn("textContent", js)
        self.assertIn("isTrusted", js)                                      # the gesture check
        self.assertIn("maxTouchPoints", js)                                 # iPadOS
        self.assertEqual(sorted(set(re.findall(r"https?://[^\s'\")]+", js))), ["http://www.w3.org/2000/svg"])

    def test_js_files_have_no_external_addresses(self):
        for n in ("user-i18n-ru.js", "user.css"):
            self.assertIsNone(re.search(r"https?://", slurp(n)), n)
        self.assertIsNone(re.search(r"@import|url\(", slurp("user.css")))

    def test_css_has_the_tokens_dark_theme_and_touch_targets(self):
        css = slurp("user.css")
        # 3.3.2: dark by default (not the system's), light — the button in the header, for the visit
        self.assertNotIn("prefers-color-scheme", css)
        self.assertIn("color-scheme: dark;", css[:css.index(':root[data-theme="light"]')])
        self.assertIn(':root[data-theme="light"] {', css)
        self.assertIn("--accent", css)
        self.assertIn("min-height: 44px", css)
        self.assertIn("env(safe-area-inset-bottom)", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_every_text_the_page_asks_for_exists(self):
        js, html, i18n = slurp("user.js"), slurp("user.html"), slurp("user-i18n-ru.js")
        keys = set(re.findall(r"\bt\(\s*'([a-z_]+)'", js)) | set(re.findall(r'data-t="([a-z_]+)"', html))
        have = set(re.findall(r"^\s{2}([a-z_]+):", i18n, re.M))
        self.assertEqual(sorted(keys - have), [], "texts used by the page but missing in user-i18n-ru.js")
        for group in set(re.findall(r"\btn\(\s*'([a-z]+)'", js)):
            self.assertIn(group, have)
        self.assertRegex(i18n, r"[А-Яа-я]")

    def test_words_in_the_html_come_from_the_dictionary(self):
        """3.3.2: the HTML holds no words — the script puts them from the dictionary of the page's language (without
        JavaScript only the bilingual <noscript> is shown); every key is in both dictionaries."""
        html = slurp("user.html")
        pairs = re.findall(r'data-t="([a-z_]+)">([^<]*)<', html)
        self.assertEqual(sorted(k for k, _ in pairs), ["brand", "for_name", "inapp", "lead", "reveal", "secret", "title"])
        for key, text in pairs:
            self.assertEqual(text, "", key)
            for n in ("user-i18n-ru.js", "user-i18n-en.js"):
                self.assertRegex(slurp(n), r"\n  %s: '" % key, (n, key))

    def test_the_dictionary_has_the_platforms_of_the_gateway(self):
        from kaskad.gw import model
        i18n = slurp("user-i18n-ru.js")
        block = re.search(r"platforms:\s*\{(.*?)\}", i18n, re.S).group(1)
        for p in model.PLATFORMS:
            self.assertRegex(block, r"\b%s:" % p, p)
        js = slurp("give.js")                       # 3.3.2: the page draws the issuance with give.js (the panel's window)
        order = re.search(r"var DEVICES = \[(.*?)\];", js, re.S).group(1) + re.search(r"var ROUTERS = \[(.*?)\];", js, re.S).group(1)
        for p in model.PLATFORMS:
            self.assertIn("'%s'" % p, order + "'other'", p)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_javascript_parses(self):
        for n in ("user.js", "user-i18n-ru.js"):
            res = subprocess.run(["node", "--check", os.path.join(STATIC, n)], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(res.returncode, 0, "%s: %s" % (n, res.stderr))

    def test_no_secret_looking_text_in_any_file(self):
        for n in self.NAMES:
            self.assertNotRegex(slurp(n), r"(BEGIN [A-Z ]*PRIVATE|PrivateKey\s*=|password\s*[:=]\s*['\"][^'\"]+)", n)


class ServedFilesTest(PageCase):
    def test_assets_are_served_with_their_types(self):
        for name, ctype in userpage.ASSETS.items():
            resp = self.call("GET", "/p/" + name)
            self.assertEqual(resp.status, 200, name)
            self.assertEqual(resp.headers["Content-Type"], ctype)
            self.assertEqual(resp.body, slurp(name).encode("utf-8"))
            self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")

    def test_immutable_only_with_the_matching_version(self):
        digest = hashlib.sha256(slurp("user.js").encode("utf-8")).hexdigest()[:10]
        self.assertIn("immutable", self.call("GET", "/p/user.js", query={"v": digest}).headers["Cache-Control"])
        self.assertEqual(self.call("GET", "/p/user.js").headers["Cache-Control"], "no-cache")
        self.assertEqual(self.call("GET", "/p/user.js", query={"v": "0000000000"}).headers["Cache-Control"], "no-cache")

    def test_the_page_asks_for_no_favicon(self):
        # Т-3312 стенд У §2.6: без <link rel=icon> браузер просит /favicon.ico — 404 в консоли посетителя. Значок —
        # как у панели (пустой data:,), и CSP страницы его пропускает (img-src 'self' data:, как у панели).
        resp = self.call("GET", "/p/%s" % self.token())
        head = resp.body.decode("utf-8").split("</head>")[0]
        self.assertEqual(re.findall(r'<link rel="icon" href="([^"]*)">', head), ["data:,"])
        img = [d.split()[1:] for d in resp.headers["Content-Security-Policy"].split(";") if d.split()[:1] == ["img-src"]]
        self.assertEqual(img, [["'self'", "data:"]])

    def test_the_shell_points_at_the_current_versions(self):
        text = self.call("GET", "/p/%s" % self.token()).body.decode("utf-8")
        for name in ("user.css", "user.js", "user-i18n-ru.js"):
            digest = hashlib.sha256(slurp(name).encode("utf-8")).hexdigest()[:10]
            self.assertIn("/p/%s?v=%s" % (name, digest), text)

    def test_other_names_are_the_ordinary_404(self):
        for name in ("user.html", "user.txt", "app.js", "../user.js", "styles.css"):
            self.assertEqual(self.sig(self.call("GET", "/p/" + name, ip="198.51.100.60")), self.sig(userpage.not_found()), name)

    def test_the_template_cannot_be_fetched_raw(self):
        self.assertEqual(self.call("GET", "/p/user.html").status, 404)


class CliTest(PageCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.main(list(argv))
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def run_json(self, *argv):
        rc, out, err = self.run_cli(*(argv + ("--json",)))
        self.assertEqual(len(out.strip().splitlines()), 1, out + err)
        return rc, json.loads(out), err

    def test_the_group_is_registered(self):
        parser = cli.build_parser()
        args = parser.parse_args(["page", "on", "Дача"])
        self.assertEqual(args.user, "Дача")
        self.assertIn("kaskad.panel.userpage_cli", cli.GROUPS)

    def test_on_url_rotate_off(self):
        rc, out, err = self.run_cli("page", "on", NAME2)
        self.assertEqual(rc, 0, err)
        tok = userpage._stored_tokens()[UID2]
        self.assertIn("Страница для «Дача» включена.", out)
        self.assertIn("Ссылка: /p/%s" % tok, out)
        self.assertIn("Внимание:", out)                                    # the panel is closed: the page cannot be opened yet
        self.assertIn("Доступ из интернета", out)
        rc, out, _ = self.run_cli("page", "url", UID2)
        self.assertEqual(rc, 0)
        self.assertIn("/p/%s" % tok, out)
        rc, out, _ = self.run_cli("page", "rotate", NAME2)
        new = userpage._stored_tokens()[UID2]
        self.assertNotEqual(tok, new)
        self.assertIn("/p/%s" % new, out)
        self.assertNotIn(tok, out)
        rc, out, _ = self.run_cli("page", "off", NAME2)
        self.assertEqual(rc, 0)
        self.assertIn("ссылка больше не открывается", out)
        self.assertNotIn("user.%s.page" % UID2, secrets.load())
        rc, out, _ = self.run_cli("page", "off", NAME2)
        self.assertEqual(rc, 0)
        self.assertIn("не была включена", out)

    def test_url_of_a_page_that_is_off(self):
        rc, out, err = self.run_cli("page", "url", UID1)
        self.assertEqual(rc, errors.ERROR)
        self.assertIn("страница не включена", err)
        rc, doc, _ = self.run_json("page", "url", UID1)
        self.assertEqual((rc, doc["ok"]), (errors.ERROR, False))

    def test_unknown_user(self):
        for verb in ("on", "off", "rotate", "url"):
            rc, out, err = self.run_cli("page", verb, "Никто")
            self.assertEqual(rc, errors.ERROR, verb)
            self.assertIn("Никто", err)

    def test_json_everywhere_is_one_document(self):
        rc, doc, _ = self.run_json("page", "on", UID1)
        self.assertEqual(rc, 0)
        self.assertTrue(doc["ok"] and doc["enabled"])
        self.assertEqual(doc["user"]["name"], NAME1)
        self.assertRegex(doc["path"], r"^/p/[A-Za-z0-9_-]{43}$")
        self.assertFalse(doc["public"])
        self.assertIn("Доступ из интернета", doc["note"])
        rc, doc2, _ = self.run_json("page", "url", UID1)
        self.assertEqual(doc2["path"], doc["path"])
        rc, doc3, _ = self.run_json("page", "rotate", UID1)
        self.assertNotEqual(doc3["path"], doc["path"])
        rc, doc4, _ = self.run_json("page", "off", UID1)
        self.assertEqual((doc4["ok"], doc4["enabled"], doc4["was_enabled"]), (True, False, True))
        rc, doc5, _ = self.run_json("page", "list")
        self.assertEqual(doc5, {"ok": True, "pages": []})

    def test_public_mode_gives_the_full_link_and_no_warning(self):
        self.write_access(public=True, port=41234)
        rc, out, _ = self.run_cli("page", "on", UID1)
        tok = userpage._stored_tokens()[UID1]
        self.assertIn("Ссылка: https://%s:41234/p/%s" % (HOST, tok), out)
        self.assertNotIn("Доступ из интернета", out)
        rc, doc, _ = self.run_json("page", "url", UID1)
        self.assertEqual(doc["url"], "https://%s:41234/p/%s" % (HOST, tok))
        self.assertTrue(doc["public"])
        self.assertEqual(doc["note"], "")

    def test_no_qrencode_is_told(self):
        self.w.runner.binaries.discard("qrencode")
        rc, out, _ = self.run_cli("page", "on", UID1)
        self.assertIn("apt-get install qrencode", out)
        rc, doc, _ = self.run_json("page", "url", UID1)
        self.assertFalse(doc["qr"])
        self.assertIn("qrencode", doc["qr_note"])

    def test_list_shows_no_links_unless_asked(self):
        self.run_cli("page", "on", UID1)
        self.run_cli("page", "on", UID2)
        tok = userpage._stored_tokens()[UID1]
        rc, out, _ = self.run_cli("page", "list")
        self.assertEqual(rc, 0)
        self.assertIn(NAME2, out)
        self.assertIn(UID1, out)
        self.assertNotIn(tok, out)                                         # the secret stays out of a plain list
        rc, doc, _ = self.run_json("page", "list")
        self.assertEqual([r["name"] for r in doc["pages"]], [NAME2, NAME1])
        self.assertTrue(all("url" not in r and "path" not in r for r in doc["pages"]))
        rc, out, _ = self.run_cli("page", "list", "--urls")
        self.assertIn("/p/%s" % tok, out)
        rc, doc, _ = self.run_json("page", "list", "--urls")
        self.assertTrue(all(r["path"].startswith("/p/") for r in doc["pages"]))

    def test_empty_list(self):
        rc, out, _ = self.run_cli("page", "list")
        self.assertEqual(rc, 0)
        self.assertIn("Страниц нет", out)

    def test_a_disabled_person_is_marked(self):
        from kaskad import state
        from kaskad.gw import model
        self.run_cli("page", "on", UID2)
        st = state.load()
        model.get_user(st, UID2)["enabled"] = False
        state.save(st)
        rc, out, _ = self.run_cli("page", "list")
        self.assertIn("пользователь выключен", out)

    def test_usage_errors(self):
        rc, out, err = self.run_cli("page")
        self.assertEqual(rc, errors.USAGE)


if __name__ == "__main__":
    unittest.main()

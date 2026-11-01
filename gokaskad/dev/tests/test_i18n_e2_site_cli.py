"""3.3.2 E2, group site_cli: ``kaskadctl site …`` (site/cli.py) and the templates of the cover site (site/templates.py)
speak English under use_lang("en") and say exactly what they said before in Russian (the default).

The data stays as it is: names and categories of templates come from the catalog, texts of site/ops.py are of
another module — the checks here give the commands controlled data (mocked ops) or look only at our lines.
"""

import argparse
import contextlib
import io
import json
import os
import re
import unittest
from unittest import mock

from kaskad import cli, errors
from kaskad.i18n import use_lang
from kaskad.site import cli as site_cli
from kaskad.site import ops
from kaskad.site import templates as T
from tests.site.sitecase import SiteCase

CYR = re.compile("[А-Яа-яЁё]")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAT = os.path.join(ROOT, "lib", "kaskad", "i18n", "catalog")


def _cat(lang, area):
    with open(os.path.join(CAT, lang, area + ".json"), encoding="utf-8") as f:
        return json.load(f)


def _args(**kw):
    return argparse.Namespace(**dict(dict(json=False, quiet=False), **kw))


def _run(fn, args, **patches):
    """site_cli.<fn>(args) with ops.<name> replaced -> (rc or «E: text», stdout + stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.multiple(ops, **dict(patches, status=ops.status)), mock.patch.object(site_cli, "_r", lambda: None), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = fn(args)
        except errors.KaskadError as e:
            rc = "E: %s" % e
    return rc, out.getvalue() + err.getvalue()


DOCS = [
    {"domain": None, "mode": "off", "port80": {"free": True}, "cert": None, "use": {}, "deps": {}},
    {"domain": "xn--e1afmkfd.example", "domain_unicode": "пример.example", "mode": "site", "email": None,
     "port80": {"who": "nginx"}, "renew": {"by": "certbot.timer"}, "deps": {"certbot": True},
     "use": {"panel": True, "sstp": True}, "use_impact": {"sstp": 2, "ocserv": 1}, "host_is_domain": False,
     "cert": {"kind": "letsencrypt", "not_after": "2026-12-30T00:00:00", "days_left": 81, "staging": True,
              "issuer": "R11"},
     "dns": {"state": "partial", "checked_at": "2026-10-10 12:00", "a": ["192.0.2.1"], "aaaa": [], "own": [],
             "ready": False},
     "site": {"port443": {"free": True, "xray": True}, "panel_url_masked": None}},
    {"domain": "vpn.example.com", "mode": "domain", "port80": {"free": True}, "host_is_domain": True,
     "cert": {"kind": "selfsigned", "not_after": "2036-01-01T00:00:00"}, "dns": None, "use": {"ocserv": True},
     "site": {"port443": {"free": False, "who": "haproxy"}}},
]


class EnglishTest(unittest.TestCase):
    def assertEnglish(self, text, what=""):
        self.assertTrue(text.strip(), what)
        self.assertIsNone(CYR.search(text), "%s: %s" % (what, text))
        self.assertNotIn("site_cli.", text, what)          # a key without a catalog string
        self.assertNotIn("site_tpl.", text, what)

    def test_status_lines(self):
        with use_lang("en"):
            for doc in DOCS:
                lines = site_cli.status_lines(doc)
                # the name of the domain is data (IDN as it was typed)
                self.assertEnglish("\n".join(l.replace("пример", "x") for l in lines), "status")
            self.assertIn("Domain: not set (everything is on the server’s self-signed certificate). Set: "
                          "kaskadctl site domain <name>", site_cli.status_lines(DOCS[0]))
            self.assertIn("Port 443/tcp: held by the gateway’s xray, but the site is not behind it — the site won’t "
                          "open on the domain: kaskadctl gw vless sync", site_cli.status_lines(DOCS[1]))
            self.assertIn("Certificate: Let’s Encrypt (TEST, staging), valid until Dec 30, 2026 (81 d left), issuer R11",
                          site_cli.status_lines(DOCS[1]))

    def test_every_word_by_code(self):
        with use_lang("en"):
            for st in site_cli.DNS_STATES:
                self.assertEnglish(site_cli.dns_lines({"state": st})[0], st)
            self.assertEqual(site_cli.dns_text("weird"), "weird")
            for p in ({"free": False, "who": "x"}, {"free": True, "xray": True, "behind": True}, {"free": True}):
                for mode in ("site", "domain"):
                    self.assertEnglish(site_cli.port443_line(p, mode))
            self.assertEqual(site_cli.dns_lines(None), ["DNS: not checked yet"])
        for lang in ("ru", "en"):
            cat = _cat(lang, "site_cli")
            for code in site_cli.DNS_STATES:
                self.assertIn("site_cli.dns.state." + code, cat)
            for code in site_cli.TICK_ACTIONS:
                self.assertIn("site_cli.tick." + code, cat)
            for code in site_cli.CHECK_VERDICTS:
                self.assertIn("site_cli.check.verdict." + code, cat)
            tpl = _cat(lang, "site_tpl")
            for code in T.TEXT_CODES:
                self.assertIn("site_tpl.text." + code, tpl)
            for code in T.HEALTH_MARKS:
                self.assertIn("site_tpl.health." + code, tpl)

    def test_commands(self):
        tick = lambda act: (lambda r: {"action": act, "days_left": 12})
        check = lambda v: (lambda r: {"verdict": v, "ok": v == "ok", "why": "x", "fix": "y", "warnings": ["z"]})
        mode = {"template": {"name": "Coffee House"}, "vless": {"changed": True, "security": "tls"}}
        cases = [
            (site_cli.cmd_domain, _args(clear=True, name="x"), {}),
            (site_cli.cmd_domain, _args(clear=False, name=None), {}),
            (site_cli.cmd_domain, _args(clear=True, name=None), {"clear": lambda r, **kw: {"kept": ["vpn.example.com"]}}),
            (site_cli.cmd_domain, _args(clear=False, name="b.example"), {"set_domain": lambda r, n, say=None: {
                "changed_from": "a.example", "site": {"domain": "b.example"}, "cert": None, "warnings": ["w"]}}),
            (site_cli.cmd_domain, _args(clear=False, name="b.example"), {"set_domain": lambda r, n, say=None: {
                "site": {"domain": "b.example"}, "dns": {"state": "ok", "ready": True}, "warnings": []}}),
            (site_cli.cmd_email, _args(clear=True, email="a@b"), {}),
            (site_cli.cmd_email, _args(clear=True, email=None), {"set_email": lambda r, e: {"email": None}}),
            (site_cli.cmd_issue, _args(staging=False), {"issue": lambda r, staging: {
                "text": "Issued.", "cert": None, "warnings": ["nm"], "warning_codes": ["no_email"], "consumers": [{"name": "panel"}]}}),
            (site_cli.cmd_renew, _args(), {"renew": lambda r: {"renewed": True, "cert": None}}),
            (site_cli.cmd_renew, _args(), {"renew": lambda r: {"renewed": False, "cert": None}}),
            (site_cli.cmd_installed, _args(), {"cert_installed": lambda r, lineage=None: {"skipped": "lineage"}}),
            (site_cli.cmd_installed, _args(), {"cert_installed": lambda r, lineage=None: {"skipped": "nodomain"}}),
            (site_cli.cmd_installed, _args(), {"cert_installed": lambda r, lineage=None: {"consumers": []}}),
            (site_cli.cmd_use, _args(value="maybe", kind="sstp", dry_run=False), {}),
            (site_cli.cmd_remove, _args(), {"remove": lambda r: {"kept": ["a"], "packages": ["certbot"]}}),
            (site_cli.cmd_mode, _args(mode="site", template=None, random=False),
             {"mode": lambda r, m, template_id=None, fresh=False, **kw: mode}),
            (site_cli.cmd_mode, _args(mode="domain", template=None, random=False),
             {"mode": lambda r, m, template_id=None, fresh=False, **kw: {"vless": {"changed": True, "port": 8443}}}),
            (site_cli.cmd_panel_url, _args(show=False), {"panel_url": lambda show: {"url": "https://x/***"}}),
            (site_cli.cmd_panel_path, _args(), {"rotate_panel_path": lambda r: {"url": "https://x/p"}}),
            (site_cli.cmd_domain, _args(clear=False, name="x"), {"set_domain": mock.Mock(side_effect=ops.Refusal(
                "dns", "Not ready."))}),
        ]
        for on in ("on", "off"):
            for dry in (True, False):
                cases.append((site_cli.cmd_use, _args(value=on, kind="sstp", dry_run=dry), {
                    "TITLES": {"sstp": "SSTP"}, "use": lambda r, k, o, dry_run=False: {
                        "dry_run": dry_run, "affected": [1], "use": {"sstp": o}, "warnings": ["w"]}}))
        cases += [(site_cli.cmd_tick, _args(), {"tick": tick(a)}) for a in site_cli.TICK_ACTIONS]
        cases += [(site_cli.cmd_check, _args(), {"check": check(v)}) for v in site_cli.CHECK_VERDICTS]
        with use_lang("en"):
            for fn, args, patches in cases:
                rc, text = _run(fn, args, **patches)
                self.assertEnglish(str(rc) + "\n" + text, fn.__name__)
            self.assertEqual(_run(site_cli.cmd_renew, _args(), renew=lambda r: {"renewed": False, "cert": None})[1],
                             "Not due for renewal yet — certbot changed nothing.\nCertificate: none\n")
            self.assertEqual(_run(site_cli.cmd_use, _args(value="maybe", kind="sstp", dry_run=False))[0],
                             "E: the value must be on or off, not 'maybe'")

    def test_help_of_the_parser(self):
        with use_lang("en"):
            p = argparse.ArgumentParser(prog="kaskadctl")
            site_cli.register(p.add_subparsers())
            seen = []

            def walk(pp):
                seen.append(pp.format_help())
                for a in pp._actions:
                    if isinstance(a, argparse._SubParsersAction):
                        for c in a.choices.values():
                            walk(c)
            walk(p)
        text = "\n".join(seen)
        self.assertEnglish(text, "help")
        for cmd in ("templates", "templates-index", "template", "cert", "panel-path"):
            self.assertIn(cmd, text)
        self.assertIn("COMMAND", text)
        self.assertIn("domain, DNS and Let’s Encrypt certificate", text)
        self.assertIn("site template catalog: categories, search, pages of 25", text)

    def test_templates_words(self):
        with use_lang("en"):
            for code in T.TEXT_CODES:
                self.assertEnglish(T.text(code, "gh_x"), code)
                self.assertIn("gh_x", T.text(code, "gh_x"))
                self.assertEqual(T.TEXTS[code] % "gh_x", T.text(code, "gh_x"))      # the shim of site/ops.py
            self.assertEqual(T.text("timeout", "a"), "Template “a” didn’t download in 90 seconds: GitHub is slow or "
                                                     "the repository is too big.")
            self.assertEqual(T.text("nonsense", "a"), T.text("download_failed", "a"))
            for e in (T._missing(), T._damaged(), T._damaged("own words")):
                self.assertEnglish(str(e))
            for st in T.HEALTH_MARKS:
                self.assertEnglish(T._health_mark(st), st)
            self.assertEqual(T._health_mark("alive"), "")
            self.assertEqual(T._fmt_n(33467), "33,467")
            with self.assertRaises(errors.KaskadError) as e:
                T._check_root("/")
            self.assertEnglish(str(e.exception))
            with self.assertRaises(errors.KaskadError) as e:
                T.deploy(None, "../x")
            self.assertEqual(str(e.exception), "“../x” is not a template id from the catalog: Latin letters, digits, "
                                               "“_”, “-”, “.”.")
        self.assertEqual(sorted(T.TEXTS), sorted(T.TEXT_CODES))
        self.assertIsNone(T.TEXTS.get("nope"))

    def test_template_commands(self):
        out = io.StringIO()
        sts = [{"site": False, "root": "/var/www/kaskad"}, {"site": True, "stub": True, "root": "/r"},
               {"site": True, "stub": False, "id": "t1", "source": None, "deployed_at": None, "root": "/r"},
               {"site": True, "stub": False, "id": None, "root": "/r"}]
        sets = [{"ok": True, "fallback": True, "errors": [{"text": "e1"}], "warnings": ["w"], "backup": "/b"},
                {"ok": True, "fallback": False, "id": "t", "name": "Coffee", "root": "/r", "warnings": [],
                 "backup": None},
                {"ok": False, "fallback": False, "text": "Gone.", "errors": [{"text": "a"}, {"text": "b"}],
                 "warnings": []}]
        cat = {"categories": [{"id": "landing", "title": "Landing pages", "count": 1200}], "total": 33467}
        items = [{"id": "a%d" % i, "name": "Name", "source": "github", "license": "MIT", "preview": "https://p"}
                 for i in range(3)]
        health = {"a0": {"state": "gone"}, "a1": {"state": "no_preview"}, "a2": {"state": "unknown"}}
        with use_lang("en"), contextlib.redirect_stdout(out), mock.patch.object(T, "_runner", lambda: None):
            for st in sts:
                with mock.patch.object(T, "status", lambda st=st: st):
                    T.cmd_template_status(_args())
            for r in sets:
                with mock.patch.object(ops, "template_set", lambda rr, i, r=r: r), \
                        mock.patch.object(T, "get", lambda i: {"author": "Jane"}):
                    T.cmd_template_set(_args(random=True))
            for built in (True, False):
                with mock.patch.object(T, "build_index", lambda b=built: {"built": b, "total": 33467,
                                                                          "categories": 12}):
                    T.cmd_templates_index(_args(fetch=False))
            for category, query, found in ((None, "", items), ("landing", "cafe", items), ("zzz", "x", [])):
                res = {"items": [dict(x) for x in found], "query": query, "page": 1, "pages": 2, "total": 77,
                       "per_page": 25}
                with mock.patch.multiple(T, prepare=lambda r: None, catalog=lambda: cat,
                                         search=lambda c, q, p, res=res: res), \
                        mock.patch.object(T.tplhealth, "cached", lambda ids: health):
                    T.cmd_templates(_args(category=category, query=query, page=1, check=False))
        text = out.getvalue()
        self.assertEnglish(text, "templates")
        for want in ("The site isn’t deployed yet (/var/www/kaskad is empty).", "Thanks to Jane for the open source code.",
                     "Template not installed: Gone. The previous site is untouched.",
                     "Site template catalog: 33,467 templates under free licenses.",
                     "All categories · search “x” — 77 found, page 1 of 2", "  Nothing found — try another word.",
                     " · removed by its author — can’t be chosen", "Site template catalog index is up to date"):
            self.assertIn(want, text)


class RussianAsBeforeTest(unittest.TestCase):
    """The default language: the words of 3.3.1, byte for byte (the SSH menu and tests read them)."""

    def test_lines(self):
        with use_lang("ru"):
            self.assertEqual(site_cli.status_lines(DOCS[0])[0], "Домен: не задан (всё на самоподписанном сертификате "
                                                                "узла). Задать: kaskadctl site domain <имя>")
            self.assertEqual(site_cli.port443_line({"free": True}, "site"), "Порт 443/tcp: на нём сайт (nginx).")
            self.assertEqual(site_cli.dns_lines({"state": "none", "ready": False}),
                             ["DNS: A-записи нет ()", "  A: нет; AAAA: нет; адреса сервера: неизвестны",
                              "  готов к выпуску сертификата: нет"])
            self.assertIn("Сертификат: Let's Encrypt (ТЕСТОВЫЙ, staging), до 30.12.2026 (осталось 81 дн.), издатель R11",
                          site_cli.status_lines(DOCS[1]))
            self.assertEqual(_run(site_cli.cmd_use, _args(value="on", kind="sstp", dry_run=True), TITLES={"sstp": "SSTP"},
                                  use=lambda r, k, o, dry_run=False: {"dry_run": True, "affected": [1, 2]})[1],
                             "Проверка без изменений: SSTP переедет на сертификат домена; новые настройки понадобятся "
                             "устройствам: 2.\n")

    def test_templates(self):
        with use_lang("ru"):
            self.assertEqual(T.TEXTS["gone"] % "gh_dist", "Шаблон «gh_dist» удалён автором — выберите другой.")
            self.assertEqual(T.TEXTS["timeout"], "Шаблон «%s» не скачался за 90 секунд: GitHub отвечает медленно или "
                                                 "репозиторий слишком большой.")
            self.assertEqual(T.TEXTS["too_big"] % "a", "Шаблон «a» больше 100 МБ — для сайта-маскировки это слишком "
                                                       "много, выберите другой.")
            self.assertEqual(T._health_mark("no_preview"), " · образец снят, шаблон ставится")
            self.assertEqual(T._fmt_n(33467), "33 467")
            self.assertEqual(str(T._damaged()), "Каталог шаблонов сайта на этом сервере повреждён — его нужно скачать "
                                                "заново.")


class RealCliTest(SiteCase):
    """The whole command through kaskadctl, in English: the lines of site/cli.py."""

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), use_lang("en"):
            rc = cli.main(["site"] + list(argv))
        return rc, out.getvalue() + err.getvalue()

    def test_status_and_refusals(self):
        rc, text = self.run_cli("status")
        self.assertEqual(rc, 0)
        self.assertIn("Domain: not set", text)
        self.assertIn("Port 80/tcp: free", text)
        self.assertIn("Domain certificate used by: panel — no", text)
        rc, text = self.run_cli("domain")
        self.assertEqual(rc, errors.USAGE)
        self.assertIn("specify a domain or --clear", text)
        rc, text = self.run_cli("template", "status")
        self.assertEqual(rc, 0)
        self.assertEnglish = EnglishTest.assertEnglish.__get__(self)
        self.assertEnglish(text, "template status")


if __name__ == "__main__":
    unittest.main()

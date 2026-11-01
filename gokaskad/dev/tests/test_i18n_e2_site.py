"""3.3.2 E2, group «site»: the domain, its certificate and the cover site (kaskad.site: ops, front, acme, nginx, model,
dns, panel, consumers, tplhealth, certinfo) speak English under ``use_lang("en")`` — and the Russian text as before by
default. Warnings other modules act on are codes (``ops.NO_EMAIL``, ``DEFAULT_PAGE``, ``NGINX_BOOT`` in
``warning_codes``); their texts — ``ops.warning(code)`` in the language of the call."""

import os
import re
import unittest
from unittest import mock

from kaskad import state
from kaskad.errors import KaskadError
from kaskad import i18n
from kaskad.i18n import tr, trn, use_lang
from kaskad.site import acme, certinfo, dns, front, model, nginx, ops, tplhealth
from tests.helpers import zone
from tests.site.sitecase import DAY, DOMAIN, NOW, SiteCase

CYR = re.compile("[А-Яа-яЁё]")
NGINX_80 = 'tcp   LISTEN 0      511          0.0.0.0:80         0.0.0.0:*    users:(("nginx",pid=812,fd=6))\n'


class _Res(object):
    def __init__(self, out="", err=""):
        self.out, self.err, self.ok = out, err, False


def _err(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except (KaskadError, ValueError) as e:
        return str(e)
    raise AssertionError("no error from %r" % (fn,))


class Pure(unittest.TestCase):
    def english(self, text):
        self.assertTrue(text and text.strip(), text)
        self.assertIsNone(CYR.search(text), text)
        return text

    def test_domain_names(self):
        bad = ("", "1.2.3.4", "localhost", "*.example.com", "a..example.com", "x" * 64 + ".com", "bad_name.example.com",
               "example.123", ("a" * 60 + ".") * 5 + "com")
        with use_lang("en"):
            for name in bad:
                with self.subTest(name=name):
                    self.english(_err(model.parse_domain, name))
            self.assertIn("IP address", _err(model.parse_domain, "1.2.3.4"))
            self.assertIn("vpn.example.com", _err(model.parse_domain, ""))
            self.english(_err(model.parse_email, "nope"))
        self.assertEqual(_err(model.parse_domain, "1.2.3.4"),
                         "«1.2.3.4» — это IP-адрес, а нужен домен. Домен — имя вида vpn.example.com (можно кириллицей: "
                         "впн.пример.рф).")
        self.assertEqual(_err(model.parse_domain, "localhost"),
                         "«localhost» — не домен: нужна хотя бы одна точка. Домен — имя вида vpn.example.com (можно "
                         "кириллицей: впн.пример.рф).")

    def test_dns_advice(self):
        own = (["203.0.113.10"], [])
        docs = {
            "none": dns.evaluate("ok", [], [], *own),
            "elsewhere": dns.evaluate("ok", ["198.51.100.1"], [], *own),
            "partial": dns.evaluate("ok", ["198.51.100.1", "203.0.113.10"], [], *own),
            "unknown": dns.evaluate("fail", [], [], *own),
            "no_own": dns.evaluate("ok", ["198.51.100.1"], [], [], []),
            "cloudflare": dns.evaluate("ok", ["104.16.0.1"], [], *own),
            "aaaa": dns.evaluate("ok", ["203.0.113.10"], ["2001:db8::1"], *own),
        }
        with use_lang("en"):
            for k, d in docs.items():
                with self.subTest(state=k):
                    self.assertIn(DOMAIN, self.english(dns.advice(DOMAIN, d)))
            self.assertIn("“DNS only”", dns.advice(DOMAIN, docs["cloudflare"]))
        self.assertEqual(dns.advice(DOMAIN, docs["unknown"]),
                         "DNS не ответил — A-записи домена vpn.example.com проверить не удалось. Повторите через минуту.")

    def test_acme_refusals(self):
        with zone("UTC"), mock.patch.object(acme, "_now", lambda: NOW):
            retry = "2026-10-08T12:00:00Z"
            with use_lang("en"):
                for reason, r, kind in (("limit", retry, "dup"), ("limit", retry, "failed"), ("limit", None, "failed"),
                                        ("limit", retry, "other"), ("limit", None, "other"), ("port80", None, None),
                                        ("net", None, None), ("dns", None, None)):
                    with self.subTest(reason=reason, kind=kind, retry=r):
                        self.english(acme.refusal_text(DOMAIN, reason, r, kind))
                self.assertIn("Oct 8, 12:00 UTC (in 24 h)", acme.refusal_text(DOMAIN, "limit", retry, "dup"))
                self.assertIn("in about an hour", acme.refusal_text(DOMAIN, "limit", None, "failed"))
                self.assertEqual(acme.when_text("2026-10-07T12:30:00Z"), "Oct 7, 12:30 UTC (in less than an hour)")
                self.assertEqual(acme.when_text("2026-10-10T12:00:00Z"), "Oct 10, 12:00 UTC (in 3 days)")
            self.assertEqual(acme.when_text("2026-10-10T12:00:00Z"), "10.10, 12:00 UTC (через 3 дн.)")
            self.assertEqual(acme.refusal_text(DOMAIN, "limit", retry, "other"),
                             "Let's Encrypt временно не выдаёт сертификаты домену vpn.example.com (лимит). Повторить "
                             "можно 08.10, 12:00 UTC (через 24 ч).")

    def test_ops_texts(self):
        p = {"who": "nginx (pid 812)"}
        rows = [{"name": "phone", "user_name": "Ann", "kind": "sstp"}] + \
            [{"name": "d%d" % i, "user_name": "", "kind": "ocserv"} for i in range(12)]
        with use_lang("en"):
            for text in (ops.port80_text(p), ops.port80_text(p, "renew"), ops.port443_text(p),
                         ops._neighbour_text(["a.conf"]), ops.port80_site_text(p), ops._kept_text(DOMAIN),
                         ops.reissue_text(rows, tr("site.ops.what.domain_changed")),
                         ops._nginx_test_text(nginx.NginxTestError("x:1", ours=True)),
                         ops._nginx_test_text(nginx.NginxTestError("x:1", ours=False)),
                         _err(ops.mode, None, "bad"), ops.warning(ops.NO_EMAIL), ops.warning(ops.DEFAULT_PAGE),
                         ops.warning(ops.NGINX_BOOT)):
                self.english(text)
            self.assertIn("issue the certificate again", ops.port80_text(p))
            self.assertIn("renew the certificate again", ops.port80_text(p, "renew"))
            self.assertIn("SSTP, OpenConnect: ", ops.reissue_text(rows, "x"))
            self.assertIn("phone (Ann)", ops.reissue_text(rows, "x"))
            self.assertTrue(ops.reissue_text(rows, "x").endswith(" and 3 more."))
            # warnings other modules act on: a code, the text in the language of the call
            self.assertTrue(ops.warning(ops.NO_EMAIL).startswith("No email is set for Let’s Encrypt"))
            self.assertEqual(ops.warning(ops.DEFAULT_PAGE), tr("site.ops.default_page"))
        self.assertEqual(ops.port80_text(p, "renew"),
                         "Порт 80/tcp занят: nginx (pid 812). Для проверки домена Let's Encrypt certbot должен временно "
                         "занять этот порт. Уберите этот проброс или остановите службу, затем повторите продление. "
                         "Kaskad чужие службы не останавливает.")
        self.assertTrue(ops.reissue_text(rows, "x").endswith(" и ещё 3."))
        self.assertTrue(ops.warning(ops.NO_EMAIL).startswith("Почта для Let's Encrypt не задана"))
        self.assertEqual(ops.warning(ops.DEFAULT_PAGE), "на порту 80 отвечает страница nginx по умолчанию — сервер выдаёт себя")

    def test_small_modules(self):
        with use_lang("en"):
            for s in tplhealth.STATES + ("nonsense",):
                self.english(tplhealth.doc(s)["text"])
            self.english(_err(certinfo.parse, None))
            self.english(_err(certinfo.parse, "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"))
            tail = nginx._tail(_Res(err="nginx: [emerg] /app-%s/ x\n" % ("a" * 30)), "")
            self.assertEqual(tail, "nginx: [emerg] /app-<hidden path>/ x")
            self.assertEqual(nginx._tail(_Res()), "nginx gave no details")
            self.assertEqual(front.site_ready({})[1], "the server has no domain")
            self.english(trn("site.ops.expiring", 1, domain=DOMAIN, date="2026-10-08", why="x"))
            self.assertIn("in 1 day (", trn("site.ops.expiring", 1, domain=DOMAIN, date="d", why="x"))
            self.assertIn("in 5 days (", trn("site.ops.expiring", 5, domain=DOMAIN, date="d", why="x"))
        self.assertEqual(tplhealth.doc("gone")["text"], "Шаблон удалён автором — выбрать нельзя")
        self.assertEqual(nginx._tail(_Res()), "без пояснения nginx")
        # without a panel path (reload, stop, enable) the text stays whole — "".replace put the mark between letters
        self.assertEqual(nginx._tail(_Res(err="nginx: [emerg] bad\n")), "nginx: [emerg] bad")
        self.assertEqual(nginx._tail(_Res(err="x /app-%s/ /secret-path/\n" % ("b" * 20)), "secret-path"),
                         "x /app-<скрытый путь>/ /<скрытый путь>/")
        self.assertEqual(trn("site.ops.expiring", 2, domain="a", date="b", why="c"),
                         "Сертификат домена a истекает через 2 дн. (b), продлить не удалось: c")

    def test_panel_arguments(self):
        from kaskad.panel.routes import HttpError
        from kaskad.site import panel

        class Req(object):
            def __init__(self, body):
                self.body = body
                self.query = {"page": "x"}

            def json(self):
                return self.body
        calls = ((panel.put_domain, {}), (panel.post_dns, {"wait": 999}), (panel.put_email, {"email": 1}),
                 (panel.put_use, {"kind": "x"}), (panel.put_use, {"kind": "sstp"}), (panel.put_mode, {"mode": "x"}),
                 (panel.put_mode, {"mode": "site", "template": 5}), (panel.post_cert, {"staging": "yes"}),
                 (panel.get_templates, {}), (panel.post_templates_check, {"ids": "x"}),
                 (panel.post_templates_check, {"ids": ["a"] * 26}))
        for lang in ("en", "ru"):
            for fn, body in calls:
                with self.subTest(fn=fn.__name__, body=body, lang=lang), use_lang(lang):
                    with self.assertRaises(HttpError) as c:
                        fn(Req(body))
                    self.assertEqual(c.exception.status, 400)
                    if lang == "en":
                        self.english(c.exception.text)
                    else:
                        self.assertTrue(CYR.search(c.exception.text), c.exception.text)


class Flows(SiteCase):
    """Real paths of ``kaskadctl site …`` / the panel in English."""

    def english(self, text):
        self.assertTrue(text and text.strip(), text)
        self.assertIsNone(CYR.search(text), text)

    def test_issue_and_refusals(self):
        i18n.set_lang("en")                     # the journal: the server's language (/etc/kaskad/config)
        with use_lang("en"):
            self.answers = {dns.A: ("ok", []), dns.AAAA: ("ok", [])}
            ops.set_domain(self.w.runner, DOMAIN)
            with self.assertRaises(ops.Refusal) as c:
                ops.issue(self.w.runner)
            self.english(c.exception.doc["text"])
            self.assertIn("no A record", c.exception.doc["text"])
            self.answers = {dns.A: ("ok", ["203.0.113.10"]), dns.AAAA: ("ok", [])}
            self.certbot_present()
            self.w.ss = NGINX_80
            with self.assertRaises(ops.Refusal) as c:
                ops.issue(self.w.runner)
            self.english(c.exception.doc["text"])
            self.w.ss = ""
            for mode in ("limit", "net", "dns"):
                self.certbot.mode = mode
                with self.assertRaises(ops.Refusal) as c:
                    ops.issue(self.w.runner)
                self.english(c.exception.doc["text"])
            self.certbot.mode = "ok"
            res = ops.issue(self.w.runner)
            self.english(res["text"])
            for w in res["warnings"]:
                self.english(w)
            self.assertIn(ops.NO_EMAIL, res["warning_codes"])           # site/cli.py acts on the code
            self.assertIn(ops.warning(ops.NO_EMAIL), res["warnings"])
            self.english(ops.issue(self.w.runner)["text"])               # kept: valid for … more days
            for kind in ("sstp", "ocserv"):
                got = ops.use(self.w.runner, kind, True, dry_run=True)
                for w in got["warnings"]:
                    self.english(w)
            self.english(_err(ops.use, self.w.runner, "x", True))
            self.english(_err(ops.panel_url))
            self.english(_err(ops.check_dns, self.w.runner, wait=9999))
            self.english(_err(ops.mode, self.w.runner, "nope"))
        log = self.log_text()
        self.assertIn("SITE: domain vpn.example.com set (DNS: none)", log)
        self.assertIn("SITE: Let’s Encrypt refused (limit) for vpn.example.com", log)
        self.assertIn("SITE: certificate vpn.example.com issued (until 2026-12-30)", log)

    def test_journal_in_the_language_of_the_server(self):
        """A request in English on a Russian server: the answer in English, the journal in Russian."""
        i18n.set_lang("ru")
        with use_lang("en"):
            ops.set_domain(self.w.runner, DOMAIN)
            res = ops.issue(self.w.runner)
        self.english(res["text"])
        log = self.log_text()
        self.assertIn("SITE: домен vpn.example.com задан (DNS: ok)", log)
        self.assertIn("SITE: сертификат vpn.example.com выпущен (до 2026-12-30)", log)
        self.assertIsNone(re.search("SITE: (domain|certificate) ", log))

    def test_russian_as_before(self):
        ops.set_domain(self.w.runner, DOMAIN)
        res = ops.issue(self.w.runner)
        self.assertEqual(res["text"], "Сертификат Let's Encrypt для vpn.example.com выпущен.")
        self.assertIn(ops.warning(ops.NO_EMAIL), res["warnings"])
        self.assertEqual(ops.issue(self.w.runner)["text"],
                         "Сертификат vpn.example.com действителен ещё 83 дн. — не перевыпускаю (лимиты Let's Encrypt), "
                         "только автопродление.")
        self.assertEqual(_err(ops.use, self.w.runner, "x", True), "протокол — sstp или ocserv")
        self.assertIn("SITE: сертификат vpn.example.com выпущен (до 2026-12-30)", self.log_text())

    def test_watchdog_in_english(self):
        with use_lang("en"):
            self.with_domain(issued="le")
            self.clock = NOW + 80 * DAY                     # 3 days left: renew, certbot fails
            self.certbot.mode = "net"
            res = ops.tick(self.w.runner)
        self.assertEqual(res["action"], "failed")
        self.english(res["text"])
        self.assertIn("expires in 3 days", res["text"])
        self.english(self.notified[-1])

    def test_clear_and_removal(self):
        i18n.set_lang("en")
        with use_lang("en"):
            self.with_domain(issued="le")
            ops.use(self.w.runner, "sstp", True)
            res = ops.clear(self.w.runner)
            if res.get("text"):
                self.english(res["text"])
            ops.remove(self.w.runner)
        self.assertIn("SITE: domain vpn.example.com removed", self.log_text())
        self.assertNotIn("site", state.load())


if __name__ == "__main__":
    unittest.main()

"""3.3.2 E2: English of ipcert (ops, acme, http01, crypto, cli), ghkey, qr, compat, aptlock, boot — the same paths say
it in the language of the context; Russian stays as it was."""

import argparse
import io
import json
import re
import time
import unittest
import urllib.error
from unittest import mock

from kaskad import aptlock, boot, compat, ghkey, qr, sysrun
from kaskad.errors import KaskadError
from kaskad.i18n import use_lang
from tests.helpers import zone
from kaskad.ipcert import acme, cli as icli, crypto as kc, http01, ops
from kaskad.ipcert.http01 import Refusal
from tests.ipcert.test_ipcert import IP, IpCase

CYR = re.compile("[А-Яа-яЁё]")


class IpcertEnglishTest(IpCase):
    def no_cyr(self, text):
        self.assertIsNone(CYR.search(text or ""), text)

    def test_issue_and_keep_in_english(self):
        steps = []
        with use_lang("en"):
            res = self.issue(progress=lambda i, s, d="": steps.append(d))
            self.assertEqual(res["text"], "Let’s Encrypt certificate for %s issued: valid for ≈ 6 days, renews itself."
                             % IP)
            self.assertIn("Let’s Encrypt is checking port 80 (a brief listener on port 80)", steps)
            self.no_cyr("\n".join(steps))
            kept = self.issue()
            self.assertTrue(kept["text"].startswith("The certificate for %s is valid for another " % IP), kept["text"])
            self.assertEqual(ops.LIFETIME_TEXT, "≈ 6 days")
        with use_lang("ru"):
            self.assertEqual(ops.LIFETIME_TEXT, "≈ 6 дней")
            self.assertIn("не перевыпускаю", self.issue()["text"])

    def test_refusals_in_english(self):
        self.ca.fail_check = {"type": acme.PROBLEM + "connection", "detail": "Timeout during connect"}
        with use_lang("en"), self.assertRaises(Refusal) as cm:
            self.issue()
        self.assertEqual(cm.exception.reason, "challenge")
        self.assertIn("couldn’t reach port 80", cm.exception.text)
        self.assertIn("hosting provider’s firewall", cm.exception.text)
        self.no_cyr(cm.exception.text)
        with use_lang("en"), self.assertRaises(Refusal) as cm:
            self.issue(ip="10.0.0.5")
        self.assertEqual(cm.exception.text, "The address 10.0.0.5 is not public: Let’s Encrypt issues certificates "
                                            "only for addresses reachable from the internet. Kaskad keeps its own "
                                            "certificate.")
        with use_lang("ru"), self.assertRaises(Refusal) as cm:
            self.issue(ip="10.0.0.5")
        self.assertEqual(cm.exception.text, "Адрес 10.0.0.5 не публичный: Let's Encrypt выдаёт сертификаты только на "
                                            "адреса, доступные из интернета. Работаем на своём сертификате.")

    def test_limit_with_retry_after(self):
        self.ca.fail_order = (429, {"type": acme.PROBLEM + "rateLimited",
                                    "detail": "retry after 2026-10-09 12:00:00 UTC"})
        with use_lang("en"), self.assertRaises(Refusal) as cm:
            self.issue()
        self.assertRegex(cm.exception.text, r"^Let’s Encrypt is temporarily not issuing a certificate for %s \(rate "
                                            r"limit\): you can retry after [A-Z][a-z]{2} \d{1,2}(, \d{4})?, \d\d:\d\d UTC\. Kaskad keeps "
                                            r"its own certificate for now\.$" % re.escape(IP))

    def test_acme_problem_texts(self):
        body = json.dumps({"type": acme.PROBLEM + "serverInternal", "detail": "boom"}).encode()
        with use_lang("en"):
            e = acme.problem_error(500, {}, body, "order")
            self.assertEqual(e.text, "Let’s Encrypt returned an error during “order” (500): boom")
            self.assertEqual(acme.problem_error(500, {}, b"").text, "Let’s Encrypt returned an error (500): no explanation")
            self.no_cyr(acme.problem_error(400, {}, json.dumps({"type": acme.PROBLEM + "rejectedIdentifier"}).encode())
                        .text)
        with use_lang("ru"):
            self.assertEqual(acme.problem_error(500, {}, body, "заказ").text,
                             "Let's Encrypt ответил ошибкой при «заказ» (500): boom")
            self.assertEqual(acme.problem_error(500, {}, b"").text, "Let's Encrypt ответил ошибкой (500): без объяснений")

    def test_net_error(self):
        with use_lang("en"), mock.patch("urllib.request.urlopen", side_effect=OSError("down")), \
                self.assertRaises(acme.AcmeError) as cm:
            acme.http_request("GET", "https://example.invalid/")
        self.assertTrue(cm.exception.text.startswith("The server couldn’t reach Let’s Encrypt (down)"), cm.exception.text)

    def test_port80_and_crypto(self):
        with use_lang("en"), mock.patch.object(http01, "holders", return_value=["nginx (pid 77)"]), \
                mock.patch("kaskad.site.nginx.ours", return_value=False), self.assertRaises(Refusal) as cm:
            http01.choose(self.runner, {"engine": "nft"})
        self.assertTrue(cm.exception.text.startswith("Port 80/tcp is in use: nginx (pid 77). "), cm.exception.text)
        self.no_cyr(cm.exception.text)
        runner = sysrun.FakeRunner(binaries=["openssl"]).on(["openssl"], (1, "", ""))
        with use_lang("en"), self.assertRaises(KaskadError) as cm:
            kc.Openssl(runner).gen_rsa(self.path("/tmp/k.pem"))
        self.assertEqual(str(cm.exception), "account key failed: code 1")
        with use_lang("en"), self.assertRaises(ValueError) as cm:
            kc.pem_der("nothing")
        self.assertEqual(str(cm.exception), "no PEM block")

    def test_cli_status_lines_and_help(self):
        doc = {"state": "valid", "ip": IP, "not_after": "2026-10-16T12:00:00Z", "hours_left": 150, "staging": True,
               "needed": False, "timer": True, "last_try": {"ok": False, "at": "2026-10-09T10:00:00Z", "text": "x"}}
        with use_lang("en"), zone("UTC"):
            year = "" if time.gmtime().tm_year == 2026 else ", 2026"          # the year — only when not this one
            self.assertEqual(icli._status_lines(doc), [
                "Let’s Encrypt IP certificate: valid",
                "Address: %s · until Oct 16%s, 12:00 UTC (150 h left) · test (staging)" % (IP, year),
                "Needed now: no (the panel isn’t open to the internet) · renewal timer: present",
                "Last attempt Oct 9%s, 10:00: x" % year])
            p = argparse.ArgumentParser(prog="kaskadctl")
            icli.register(p.add_subparsers())
            out = p.format_help() + p._subparsers._group_actions[0].choices["ipcert"].format_help()
            self.assertIn("Let’s Encrypt certificate for the IP address", out)
            self.assertIn("COMMAND", out)
            self.no_cyr(out)
        with use_lang("ru"):
            self.assertEqual(icli._status_lines(doc)[0], "Сертификат Let's Encrypt на IP: действует")
            self.assertEqual(icli._status_lines(dict(doc, state="other_ip"))[0],
                             "Сертификат Let's Encrypt на IP: выпущен на другой адрес (%s)" % IP)

    def test_cli_refusal_and_remove(self):
        args = argparse.Namespace(json=False, quiet=False)
        err, out = io.StringIO(), io.StringIO()
        with use_lang("en"), mock.patch("sys.stderr", err), mock.patch("sys.stdout", out), \
                mock.patch.object(ops, "remove", return_value={"ok": True, "units": [], "files": []}):
            icli._refused(args, Refusal("private", "why"))
            icli.cmd_remove(args)
        self.assertEqual(err.getvalue(), "IP certificate not issued: why\n")
        self.assertEqual(out.getvalue(), "There was no IP certificate.\n")


class MiscEnglishTest(IpCase):
    def test_ghkey(self):
        def refuse(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)
        with use_lang("en"):
            self.assertTrue(ghkey.BAD_FORMAT.startswith("This doesn’t look like a GitHub key"))
            with mock.patch.object(ghkey, "open_github", side_effect=refuse):
                self.assertEqual(ghkey.check("ghp_" + "a" * 36)["error"],
                                 "GitHub didn’t accept the key (response 401): it’s wrong, revoked or expired.")
            with mock.patch.object(ghkey, "open_github", side_effect=urllib.error.URLError("x")):
                self.assertEqual(ghkey.check("ghp_" + "a" * 36)["error"],
                                 "No connection to GitHub (raw.githubusercontent.com): the key wasn’t checked.")
        with use_lang("ru"):
            self.assertTrue(ghkey.BAD_FORMAT.startswith("Это не похоже на ключ GitHub"))
        with self.assertRaises(AttributeError):
            ghkey.NO_SUCH_THING

    def test_qr(self):
        res = {"items": [{"format": "qr", "body": "vless://x"}]}
        with use_lang("en"), mock.patch.object(qr, "qrencode_path", return_value=None):
            qr.add_pngs(res)
            self.assertTrue(qr.MISSING_TEXT.startswith("QR code not drawn: qrencode is not installed"))
        self.assertTrue(res["items"][0]["png_error"].startswith("QR code not drawn"), res)
        self.assertIsNone(CYR.search(res["items"][0]["png_error"]))
        with use_lang("ru"):
            self.assertTrue(qr.MISSING_TEXT.startswith("QR-код не нарисован"))

    def test_compat(self):
        runner = sysrun.FakeRunner(binaries=["iptables"]).on(["iptables", "-w", "5", "-C"], (1, "", "")) \
            .on(["iptables", "-w", "5", "-I"], (2, "", "Couldn’t load match\n"))
        with use_lang("en"), self.assertRaises(KaskadError) as cm:
            compat.ensure(runner)
        self.assertEqual(str(cm.exception), "iptables rejected the compatibility rule (kaskad:compat): "
                                            "Couldn’t load match")

    def test_aptlock(self):
        h = {"pid": 4321, "name": "unattended-upgrade"}
        with use_lang("en"):
            self.assertEqual(aptlock.WAIT_LINE, "The system is installing updates (apt is busy) — waiting up to 10 "
                                                "minutes…")
            self.assertEqual(aptlock.busy_text(h, 600), "apt is busy for more than 10 minutes: the lock is held by "
                                                        "unattended-upgrade (pid 4321) — the system is installing "
                                                        "updates. Try again when it’s done.")
            self.assertEqual(aptlock.busy_text(None, 30), "apt is busy for more than 30 s — the system is installing "
                                                          "updates. Try again later.")
            self.assertIsNone(CYR.search(aptlock.BUSY_LINE))
        with use_lang("ru"):
            self.assertEqual(aptlock.WAIT_LINE, "Система ставит обновления (apt занят) — жду до 10 минут…")
            self.assertEqual(aptlock.BUSY_LINE, "Kaskad уже ставит пакеты (другое окно или бот) — жду…")
            self.assertEqual(aptlock.busy_text(None, 30), "apt занят дольше 30 с — система ставит обновления. "
                                                          "Повторите позже.")
        self.assertIn("службы пакетов не запускать", aptlock.POLICY_RC_D)      # the marker of the file stays

    def test_boot_summary(self):
        with use_lang("en"):
            self.assertEqual(boot.summary([boot.KINDS["drift"], boot.KINDS["gw_routing"]], "NET"),
                             "NET after a reboot: gateway rules restored (device routes in the nftables table, "
                             "gateway routing)")
        with use_lang("ru"):
            self.assertIsNone(boot.summary([boot.KINDS["drift"]], "NET"))       # told already after this boot
            self.assertEqual(boot.summary([boot.KINDS["gw_ip6"]], "GW"),
                             "GW после перезагрузки: правила шлюза восстановлены (таблица IPv6 шлюза)")



class HoldersTailTest(unittest.TestCase):
    """http01.holders cuts the forward's tail of a conflict in the language it was said in (conflicts.effects)."""

    def test_tail_cut_in_both_languages(self):
        from kaskad import conflicts
        from kaskad.gw import ops as gops
        for lang in ("ru", "en"):
            with use_lang(lang):
                detail = "nginx (pid 77)" + conflicts.effect("listener")
                with mock.patch.object(gops, "port_conflicts", return_value=[{"kind": "listener", "detail": detail}]):
                    self.assertEqual(http01.holders({}, None), ["nginx (pid 77)"], lang)

if __name__ == "__main__":
    unittest.main()

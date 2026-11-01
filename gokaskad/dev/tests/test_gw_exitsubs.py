"""kaskad.gw.exitsubs — subscriptions of the exits: add, refresh (only its keys), failures, ownership, the limit, goAmnezia
formats, IP and domain, the timer, secrets never in the journal or argv (docs/k3/SPEC-subs-backup.md §1)."""

import base64
import io
import json
import os
import re
import stat
import sys
import urllib.parse
from unittest import mock

from kaskad import cli, paths
from kaskad.errors import ConflictError, KaskadError
from kaskad.gw import exits, exitsubs, subfetch
from kaskad.i18n import use_lang
from tests.fakes_gw import VLESS_A, VLESS_B, awg_conf, install_parser, write_pool
from tests.gwcase import GwCase
from tests.test_gw_exits import conf_of, vpn_of

GA_SECRET = "Q" * 43
GA_URL = "https://vpn.example.org/s-%s/all.vpn" % GA_SECRET
IP_URL = "http://203.0.113.50:2096/sub/abcdef0123456789"


def vless(n, name="k"):
    return ("vless://%08d-2222-3333-4444-555555555555@198.51.100.%d:443?type=tcp&security=reality"
            "&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com&fp=chrome#%s%d"
            % (n, n % 250 + 1, name, n))


def b64(lines):
    return base64.b64encode("\n".join(lines).encode()).decode()


def amneziawg_link(conf, name):
    """A line of the incy subscription of goAmnezia Pro (all.txt): amneziawg://<base64url .conf without =>#<name>."""
    return "amneziawg://%s#%s" % (base64.urlsafe_b64encode(conf.encode()).decode().rstrip("="),
                                  urllib.parse.quote(name))


class Feed(object):
    """The servers of the subscriptions: url -> body (str) or an exception; records every download."""

    def __init__(self):
        self.bodies = {}
        self.calls = []
        self.pins = {}

    def __call__(self, url, pin=None, new_pin=False):
        self.calls.append((url, pin, new_pin))
        body = self.bodies.get(url)
        if isinstance(body, Exception):
            raise body
        if body is None:
            raise KaskadError("Сервер подписки ответил 404: подписки по этому адресу нет (отозвана или перевыпущена).")
        return body, self.pins.get(url) if new_pin else pin


class SubsCase(GwCase):
    def setUp(self):
        super().setUp()
        install_parser()
        self.feed = Feed()
        p = mock.patch.object(exitsubs, "_now", lambda: self.now)
        p.start()
        self.addCleanup(p.stop)

    def pool(self):
        return exits.pool_lines()

    def add(self, url, body, auto=True):
        self.feed.bodies[exitsubs.normalize(url)[0]] = body
        return exitsubs.add(self.w.runner, url, auto=auto, fetcher=self.feed)

    def refresh(self, ref=None, **kw):
        return exitsubs.refresh(self.w.runner, ref, fetcher=self.feed, **kw)

    def journal(self):
        return self.read("/var/log/kaskad.log") if os.path.exists(self.path("/var/log/kaskad.log")) else ""


class AddRefreshTest(SubsCase):
    def test_add_takes_the_keys_and_owns_them(self):
        res = self.add(IP_URL, b64([VLESS_A, VLESS_B]))
        self.assertEqual(self.pool(), [VLESS_A, VLESS_B])
        sub = res["sub"]
        self.assertEqual((sub["name"], sub["domain"], sub["keys"], sub["auto"]), ("подписка 1", "", 2, True))
        self.assertEqual(res["result"]["added"], 2)
        self.assertEqual(stat.S_IMODE(os.stat(exitsubs.path()).st_mode), 0o600)
        self.assertEqual(self.feed.calls, [(IP_URL, None, True)])
        # the row of an exit says where it came from
        src = exitsubs.owners()
        self.assertEqual(src[exits.exit_id(VLESS_A)]["name"], "подписка 1")

    def test_refresh_changes_only_the_keys_of_the_subscription(self):
        manual = vless(7, "manual")
        write_pool([manual])
        self.add(GA_URL, b64([VLESS_A, VLESS_B]))
        other = vless(8, "other")
        self.add(IP_URL, other)
        self.assertEqual(self.pool(), [manual, VLESS_A, VLESS_B, other])
        new = vless(9, "new")
        self.feed.bodies[GA_URL] = b64([VLESS_B, new])             # A gone, «new» came
        res = self.refresh("vpn.example.org")
        self.assertTrue(res["ok"])
        self.assertEqual(self.pool(), [manual, VLESS_B, other, new])
        r = res["results"][0]
        self.assertEqual((r["added"], r["removed"], r["count"]), (1, 1, 2))
        self.assertIn("добавлено 1, убрано 1", r["text"])
        # nothing changed: the pool is not rewritten
        n = len(self.w.script.replace_inputs)
        res = self.refresh()
        self.assertEqual(len(self.w.script.replace_inputs), n)
        self.assertTrue(all("без изменений" in r["text"] for r in res["results"]))

    def test_a_manual_key_listed_by_the_subscription_is_not_its_own(self):
        write_pool([VLESS_A])
        res = self.add(IP_URL, b64([VLESS_A, VLESS_B]))
        self.assertEqual(res["sub"]["exits"], [exits.exit_id(VLESS_B)])     # only VLESS_B is its own
        self.assertEqual(res["sub"]["keys"], 2)                  # both of its keys are in the pool (T-M6-qs330b №9)
        self.feed.bodies[IP_URL] = VLESS_B                       # the subscription dropped A
        self.refresh()
        self.assertEqual(self.pool(), [VLESS_A, VLESS_B])        # the manual key stays

    def test_a_key_removed_by_hand_comes_back_with_the_refresh(self):
        self.add(IP_URL, b64([VLESS_A, VLESS_B]))
        write_pool([VLESS_B])
        self.refresh()
        self.assertEqual(self.pool(), [VLESS_B, VLESS_A])

    def test_a_dropped_key_another_subscription_lists_changes_hands(self):
        self.add(IP_URL, VLESS_A)
        self.add(GA_URL, b64([VLESS_A, VLESS_B]))               # A is in the pool already: not GA's own
        self.feed.bodies[IP_URL] = vless(5)
        self.refresh("1")
        self.assertIn(VLESS_A, self.pool())                     # GA still lists it: it stays, GA owns it now
        self.assertIn(exits.exit_id(VLESS_A), exitsubs.load()["subs"][1]["keys"])
        self.feed.bodies[GA_URL] = VLESS_B
        self.refresh("vpn.example.org")
        self.assertNotIn(VLESS_A, self.pool())

    def test_the_same_subscription_twice_is_refused(self):
        self.add(GA_URL, VLESS_A)
        with self.assertRaises(ConflictError):
            self.add(GA_URL.replace("vpn.example.org", "VPN.example.org"), VLESS_A)

    def test_the_pool_changed_meanwhile_is_computed_again(self):
        write_pool([VLESS_A])
        self.w.script.replace_race = [[VLESS_A, vless(3)]]       # another window added a key just before
        self.add(IP_URL, VLESS_B)
        self.assertEqual(self.pool(), [VLESS_A, vless(3), VLESS_B])
        self.assertEqual(len(self.w.script.replace_inputs), 2)

    def test_manual_subscription_is_skipped_by_the_timer(self):
        self.add(IP_URL, VLESS_A, auto=False)
        self.feed.bodies[IP_URL] = VLESS_B
        res = self.refresh(auto_only=True)
        self.assertEqual(res["results"], [])
        self.assertEqual(self.pool(), [VLESS_A])


class FailuresTest(SubsCase):
    def test_a_failed_download_leaves_the_pool_and_says_why(self):
        self.add(IP_URL, b64([VLESS_A, VLESS_B]))
        self.feed.bodies[IP_URL] = KaskadError("Сервер подписки не ответил за 20 с.")
        self.now += 30 * 3600
        res = self.refresh()
        self.assertFalse(res["ok"])
        self.assertEqual(self.pool(), [VLESS_A, VLESS_B])
        row = exitsubs.listing()[0]
        self.assertTrue(row["stale"])
        self.assertEqual(row["text"], "не обновлялась 30 ч: Сервер подписки не ответил за 20 с.")

    def test_an_empty_answer_never_wipes_the_keys(self):
        self.add(IP_URL, b64([VLESS_A]))
        for body, words in (("", "пуста"), ("   \n", "пуста"), ("hello", "нет ключей"),
                            ("proxies:\n  - name: x\nproxy-groups:\n", "профиль Clash"),
                            ("<!doctype html><html></html>", "веб-страницу")):
            self.feed.bodies[IP_URL] = body
            res = self.refresh()
            self.assertFalse(res["ok"])
            self.assertIn(words, res["results"][0]["error"])
            self.assertEqual(self.pool(), [VLESS_A], body)

    def test_a_failed_add_keeps_nothing(self):
        with self.assertRaises(KaskadError):
            self.add(IP_URL, KaskadError("Сервер подписки ответил 403."))
        self.assertEqual(exitsubs.load()["subs"], [])
        self.assertEqual(self.pool(), [])

    def test_a_failed_rebuild_keeps_nothing(self):
        self.w.script.replace_rc = 1
        with self.assertRaises(KaskadError) as c:
            self.add(IP_URL, VLESS_A)
        self.assertIn("Конфиг xray невалиден", str(c.exception))
        self.assertEqual(exitsubs.load()["subs"], [])

    def test_the_limit_of_the_pool_is_said(self):
        write_pool([vless(i) for i in range(1, 99)])            # 98 keys
        res = self.add(IP_URL, b64([vless(200), vless(201), vless(202)]))
        self.assertEqual(len(self.pool()), 100)
        self.assertEqual((res["result"]["added"], res["result"]["full"]), (2, 1))
        self.assertIn("Предел пула (100 ключей): не вошло 1", res["text"])
        self.assertIn("не вошло в пул", exitsubs.listing()[0]["text"])

    def test_the_lock_of_the_pool_is_said(self):
        self.w.script.replace_rc = 6
        with self.assertRaises(KaskadError) as c:
            self.add(IP_URL, VLESS_A)
        self.assertIn("другое окно", str(c.exception))


class FormatsTest(SubsCase):
    def test_goamnezia_all_vpn(self):
        body = "\n".join(vpn_of(conf_of(line, n), "Аня · AmneziaWG %s" % line)
                         for n, line in enumerate(("3.1", "2.0", "1.5", "1.0"), 1))
        res = self.add(GA_URL, body)
        self.assertEqual(res["result"]["added"], 4)
        self.assertTrue(all(l.startswith("awg://") for l in self.pool()))
        self.assertEqual(res["sub"]["name"], "vpn.example.org")

    def test_goamnezia_all_txt_of_incy(self):
        url = "https://vpn.example.org/s-%s/all.txt" % GA_SECRET
        body = "\n".join(amneziawg_link(conf_of(line, n), "AmneziaWG %s" % line)
                         for n, line in enumerate(("3.1", "2.0"), 1))
        res = self.add(url, body)
        self.assertEqual(res["result"]["added"], 2)
        names = [e["name"] for e in exits.pool(self.w.runner)]
        self.assertEqual(names, ["AmneziaWG 3.1", "AmneziaWG 2.0"])

    def test_goamnezia_conf_of_one_line(self):
        url = "https://vpn.example.org/s-%s/awg20.conf" % GA_SECRET
        res = self.add(url, conf_of("2.0", 3))
        self.assertEqual(res["result"]["added"], 1)

    def test_goamnezia_yaml_or_no_suffix_become_all_vpn(self):
        for suffix in ("all.yaml", "mihomo.yaml", "", "/"):
            u = "https://vpn.example.org/s-%s%s" % (GA_SECRET, ("/" + suffix) if suffix not in ("", "/") else suffix)
            url, note = exitsubs.normalize(u)
            self.assertEqual(url, GA_URL, u)
            self.assertIn("all.vpn", note)
        self.assertEqual(exitsubs.normalize(GA_URL), (GA_URL, ""))

    def test_mixed_base64_list_of_vless_and_vpn(self):
        body = b64([VLESS_A, vpn_of(conf_of("2.0", 4), "Офис")])
        res = self.add(IP_URL, body)
        self.assertEqual(res["result"]["added"], 2)

    def test_names_by_ip_and_by_domain(self):
        self.add(IP_URL, VLESS_A)
        self.add("http://198.51.100.77/sub", VLESS_B)
        self.add(GA_URL, vless(4))
        self.assertEqual([r["name"] for r in exitsubs.listing()], ["подписка 1", "подписка 2", "vpn.example.org"])
        self.assertEqual([r["domain"] for r in exitsubs.listing()], ["", "", "vpn.example.org"])

    def test_bad_addresses(self):
        for u in ("", "ftp://x/y", "vless://abc", "https://a b/", "https://", "x" * 3000):
            with self.assertRaises(KaskadError):
                exitsubs.normalize(u)


class DeleteTimerTest(SubsCase):
    def test_delete_takes_its_keys_away_or_keeps_them(self):
        write_pool([vless(7)])
        self.add(IP_URL, b64([VLESS_A, VLESS_B]))
        self.add(GA_URL, vless(9))
        res = exitsubs.delete(self.w.runner, "1")
        self.assertEqual(res["removed"], 2)
        self.assertEqual(self.pool(), [vless(7), vless(9)])
        res = exitsubs.delete(self.w.runner, "vpn.example.org", keep_keys=True)
        self.assertEqual(self.pool(), [vless(7), vless(9)])
        self.assertIn("остались в пуле", res["text"])
        with self.assertRaises(KaskadError):
            exitsubs.delete(self.w.runner, "s-000000")

    def test_the_timer_lives_while_a_daily_subscription_does(self):
        unit = os.path.join(paths.systemd_dir(), exitsubs.TIMER)
        self.add(IP_URL, VLESS_A, auto=False)
        self.assertFalse(os.path.exists(unit))
        self.add(GA_URL, VLESS_B)
        self.assertTrue(os.path.exists(unit))
        with open(os.path.join(paths.systemd_dir(), exitsubs.SERVICE)) as f:
            self.assertIn("gw exit-sub refresh --auto --quiet", f.read())
        exitsubs.set_auto(self.w.runner, "vpn.example.org", False)
        self.assertFalse(os.path.exists(unit))
        exitsubs.set_auto(self.w.runner, "1", True)
        self.assertTrue(os.path.exists(unit))
        exitsubs.delete(self.w.runner, "1")
        exitsubs.delete(self.w.runner, "vpn.example.org")
        self.assertFalse(os.path.exists(unit))


class SecretsTest(SubsCase):
    def test_the_address_and_the_keys_never_reach_the_journal_argv_or_answers(self):
        res = self.add(IP_URL, b64([VLESS_A]))
        self.feed.bodies[IP_URL] = b64([VLESS_B])
        out = [res, self.refresh(), exitsubs.listing(), exitsubs.delete(self.w.runner, "1")]
        text = json.dumps(out, ensure_ascii=False) + self.journal()
        for secret in ("abcdef0123456789", "203.0.113.50", "11111111-2222", "66666666-7777"):
            self.assertNotIn(secret, text)
        for argv, _ in self.w.runner.calls:
            self.assertFalse(any("abcdef0123456789" in a or "vless://" in a for a in argv), argv)
        self.assertIn("GW exit-sub add s-", self.journal())

    def test_cli_reads_the_address_from_stdin(self):
        self.feed.bodies[IP_URL] = VLESS_A
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(IP_URL + "\n")), mock.patch.object(sys, "stdout", out), \
                mock.patch.object(subfetch, "fetch_sub", self.feed):
            rc = cli.main(["gw", "exit-sub", "add", "--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(out.getvalue())
        self.assertEqual(doc["sub"]["name"], "подписка 1")
        self.assertNotIn("abcdef", out.getvalue())
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            self.assertEqual(cli.main(["gw", "exit-sub", "list", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue())["subs"][0]["keys"], 1)
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            self.assertEqual(cli.main(["gw", "exits", "--json"]), 0)
        doc = json.loads(out.getvalue())
        self.assertEqual(doc["exits"][0]["source"]["name"], "подписка 1")
        self.assertEqual(doc["subs"][0]["id"], exitsubs.load()["subs"][0]["id"])


class Words334Test(SubsCase):
    """Stand T-334: «has 1 keys», «not refreshed for 0 h», «Subscription subscription 1:» — RU and EN."""

    def test_a_failure_within_the_hour_says_less_than_an_hour(self):
        self.add(IP_URL, b64([VLESS_A]))
        self.feed.bodies[IP_URL] = KaskadError("Сервер подписки не ответил за 20 с.")
        self.now += 600
        self.assertFalse(self.refresh()["ok"])
        sub = exitsubs.load()["subs"][0]
        self.assertEqual(exitsubs.state_text(sub, self.now), "не обновлялась меньше часа: Сервер подписки не ответил за 20 с.")
        with use_lang("en"):
            self.assertTrue(exitsubs.state_text(sub, self.now).startswith("not refreshed for less than an hour: "))
        self.assertTrue(exitsubs.state_text(sub, self.now + 2 * 3600).startswith("не обновлялась 2 ч: "))

    def test_all_keys_skipped_counts_the_keys(self):
        bad = "vless://33333333-4444-5555-6666-777777777777@198.51.100.22:8443?type=ws&security=tls" \
              "&allowInsecure=1&sni=a.example.com#Bad"
        bad2 = bad.replace("198.51.100.22", "198.51.100.23")
        with use_lang("en"):
            with self.assertRaises(KaskadError) as c:
                self.add(IP_URL, b64([bad]))
        self.assertIn("The subscription has 1 key, but xray won’t accept it — ", str(c.exception))
        with use_lang("en"):
            with self.assertRaises(KaskadError) as c:
                self.add(IP_URL, b64([bad, bad2]))
        self.assertIn("The subscription has 2 keys, but xray won’t accept any of them — ", str(c.exception))
        with self.assertRaises(KaskadError) as c:
            self.add(IP_URL, b64([bad]))
        self.assertIn("Из 1 ключа подписки xray не примет ни одного — ", str(c.exception))

    def test_gw_exits_names_a_failed_subscription_once(self):
        self.add(IP_URL, b64([VLESS_A]))
        self.feed.bodies[IP_URL] = KaskadError("Сервер подписки не ответил за 20 с.")
        self.now += 600
        self.assertFalse(self.refresh()["ok"])
        for lang, want in (("ru", "Подписка 1: не обновлялась меньше часа"),
                           ("en", "Subscription 1: not refreshed for less than an hour")):
            out = io.StringIO()
            with use_lang(lang), mock.patch.object(sys, "stdout", out):
                self.assertEqual(cli.main(["gw", "exits"]), 0)
            self.assertIn(want, out.getvalue())
            self.assertNotIn("ubscription subscription", out.getvalue())
            self.assertNotIn("одписка подписка", out.getvalue())


class OldParserTest(SubsCase):
    def test_an_old_parser_on_disk_is_written_again(self):
        p = paths.egress_parser()
        with open(p, encoding="utf-8") as f:
            text = f.read()
        with open(p, "w", encoding="utf-8") as f:
            f.write(re.sub(r"PARSER_VERSION = \d+", "PARSER_VERSION = 8", text))
        os.utime(p, (1, 1))
        calls = []
        orig = self.w.script.__call__

        def ensure(argv, inp):
            calls.append(argv[1:])
            if argv[1:] == ["--egress-ensure-parser"]:
                install_parser()
                return 0, "", ""
            return orig(argv, inp)
        self.w.runner._handlers.insert(0, ([paths.script_copy(), "--egress-ensure-parser"], ensure))
        self.add(IP_URL, amneziawg_link(conf_of("2.0", 2), "x"))
        self.assertIn(["--egress-ensure-parser"], calls)
        self.assertEqual(len(self.pool()), 1)


class SubfetchPinTest(GwCase):
    """subfetch.fetch_sub: a self-signed certificate of an IP over https is taken on its first fingerprint; another one
    later is refused; a name always goes through the CAs."""

    def getter(self, pin_seen):
        def get(scheme, host, port, path, ip, pin=None, limit=0, new_pin=False):
            pin_seen.append((host, pin, new_pin))
            return subfetch.SubAnswer(200, None, b"vless://x", "ab" * 32 if new_pin else None)
        return get

    def test_pin_is_kept_and_passed(self):
        seen = []
        text, pin = subfetch.fetch_sub("https://93.184.216.34/s", new_pin=True, getter=self.getter(seen))
        self.assertEqual(pin, "ab" * 32)
        text, pin2 = subfetch.fetch_sub("https://93.184.216.34/s", pin=pin, getter=self.getter(seen))
        self.assertEqual(pin2, pin)
        self.assertEqual(seen[1], ("93.184.216.34", pin, False))

    def test_internal_addresses_are_refused(self):
        for u in ("http://127.0.0.1/s", "http://10.0.0.1/s", "http://169.254.169.254/latest", "http://[::1]/s"):
            with self.assertRaises(KaskadError):
                subfetch.fetch_sub(u, getter=self.getter([]))

    def test_404_and_size(self):
        def g404(*a, **kw):
            return subfetch.SubAnswer(404, None, b"")
        with self.assertRaises(KaskadError) as c:
            subfetch.fetch_sub("http://93.184.216.34/s", getter=g404)
        self.assertIn("404", str(c.exception))

        def big(*a, **kw):
            return subfetch.SubAnswer(200, None, b"x" * (kw["limit"] + 1))
        with self.assertRaises(KaskadError):
            subfetch.fetch_sub("http://93.184.216.34/s", getter=big)


class SubfetchTlsTest(GwCase):
    """subfetch._get against a real local https server with a self-signed certificate (openssl makes it)."""

    def setUp(self):
        super().setUp()
        import shutil
        import ssl
        import subprocess
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        if not shutil.which("openssl"):
            self.skipTest("нет openssl")
        d = self.path("/tls")
        os.makedirs(d)
        key, crt = os.path.join(d, "k.pem"), os.path.join(d, "c.pem")
        r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", crt,
                            "-days", "1", "-subj", "/CN=kaskad-test"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode != 0:
            self.skipTest("openssl req не сработал")

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = VLESS_A.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.srv.handle_error = lambda request, address: None      # refused handshakes are the point here
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(crt, key)
        self.srv.socket = ctx.wrap_socket(self.srv.socket, server_side=True)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def tearDown(self):
        if hasattr(self, "srv"):
            self.srv.shutdown()
            self.srv.server_close()
        super().tearDown()

    def get(self, host, **kw):
        return subfetch._get("https", host, self.port, "/sub", "127.0.0.1", **kw)

    def test_self_signed_ip_is_pinned_on_first_use_and_checked_later(self):
        import ssl
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.get("127.0.0.1")                              # no pin, not adding: refused
        ans = self.get("127.0.0.1", new_pin=True)
        self.assertEqual((ans.status, ans.body), (200, VLESS_A.encode()))
        self.assertRegex(ans.pin, r"^[0-9a-f]{64}$")
        ans2 = self.get("127.0.0.1", pin=ans.pin)
        self.assertEqual(ans2.status, 200)
        with self.assertRaises(KaskadError) as c:
            self.get("127.0.0.1", pin="0" * 64)
        self.assertIn("Сертификат сервера подписки сменился", str(c.exception))

    def test_a_name_is_never_pinned(self):
        import ssl
        with self.assertRaises(ssl.SSLCertVerificationError):
            self.get("sub.example.org", new_pin=True)

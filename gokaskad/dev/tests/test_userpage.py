"""The page of a person (WP-U, kaskad.panel.userpage): the token, one 404 for everything that is not a live link, the ban,
the shell without secrets, the nonce, the settings, the files, QR codes through qrencode's stdin, the admin routes."""

import json
import os
import re
import shutil
import threading
import unittest
from unittest import mock

from kaskad import errors, secrets, snapshot, state, sysrun
from kaskad import qr as qrmod
from kaskad.gw import model, ops, registry
from kaskad.panel import routes, userpage
from tests.helpers import lock_held_elsewhere
from tests.userpage_case import (HOST, IP, NAME1, NAME2, UA_IPHONE, UID1, UID2, PageCase, matrix_of_path, passwd,
                                 priv, qr_rows, svg_of)

UA_ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"
UA_WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
UA_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
UA_TELEGRAM = "TelegramBot (like TwitterBot)"


class TokenTest(PageCase):
    def test_enable_makes_a_43_char_token_in_the_secrets_file(self):
        link = userpage.enable(UID1)
        token = userpage._stored_tokens()[UID1]
        self.assertRegex(token, r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(link, "/p/" + token)                              # not public: just the path
        self.assertEqual(secrets.get("user.%s.page" % UID1), token)
        self.assertEqual(os.stat(self.secret_path()).st_mode & 0o777, 0o600)
        self.assertEqual(userpage.enable(UID1), link)                      # on again: the same link
        self.assertEqual(userpage.url(UID1), link)
        self.assertIsNone(userpage.url(UID2))                              # no page for the other user

    def test_tokens_are_unique_and_random(self):
        seen = set()
        for _ in range(50):
            seen.add(userpage.new_token())
        self.assertEqual(len(seen), 50)
        self.assertTrue(all(userpage.valid_token(t) for t in seen))

    def test_user_by_name_or_id(self):
        a = userpage.enable(NAME2)
        self.assertEqual(userpage.url(UID2), a)
        self.assertEqual(userpage.url("дача"), a)                          # case-insensitive name, like gw

    def test_rotate_kills_the_old_link(self):
        old = self.token()
        new_link = userpage.rotate(UID1)
        new = userpage._stored_tokens()[UID1]
        self.assertNotEqual(old, new)
        self.assertEqual(new_link, "/p/" + new)
        self.assertEqual(self.shell(old).status, 404)
        self.assertEqual(self.shell(new).status, 200)

    def test_rotate_without_a_page_makes_one(self):
        self.assertTrue(userpage.rotate(UID2).startswith("/p/"))
        self.assertIn(UID2, userpage._stored_tokens())

    def test_disable_kills_the_link_and_removes_the_secret(self):
        tok = self.token()
        self.assertTrue(userpage.disable(UID1))
        self.assertFalse(userpage.disable(UID1))                           # already off
        self.assertIsNone(userpage.url(UID1))
        self.assertNotIn("user.%s.page" % UID1, secrets.load())
        self.assertEqual(self.shell(tok).status, 404)

    def test_unknown_user(self):
        for fn in (userpage.enable, userpage.rotate, userpage.disable, userpage.url, userpage.status):
            with self.assertRaises(errors.KaskadError):
                fn("u-ffffffff")

    def test_stale_tokens_of_deleted_users_do_not_open_and_are_pruned(self):
        tok = self.token(UID2)
        st = state.load()
        g = model.gw(st)
        g["devices"] = [d for d in g["devices"] if d["user"] != UID2]
        g["users"] = [u for u in g["users"] if u["id"] != UID2]
        state.save(st)
        self.assertEqual(self.shell(tok).status, 404)                      # no such person any more
        self.assertEqual([r["user"] for r in userpage.pages()], [])
        userpage.enable(UID1)                                              # any change sweeps the leftovers
        self.assertNotIn("user.%s.page" % UID2, secrets.load())

    def test_pages_list(self):
        userpage.enable(UID2)
        userpage.enable(UID1)
        rows = userpage.pages()
        self.assertEqual([r["user"] for r in rows], [UID2, UID1])          # by name: «Дача» before «Мама»
        self.assertTrue(all(r["path"].startswith("/p/") and r["url"] is None for r in rows))
        self.assertEqual(userpage.page_users(), {UID1: True, UID2: True})

    def test_url_follows_the_public_mode_of_the_panel(self):
        self.token()
        st = userpage.status(UID1)
        self.assertFalse(st["public"])
        self.assertIsNone(st["url"])
        self.assertIn("Доступ из интернета", st["note"])                   # the note says the page needs the public mode
        self.write_access(public=True, port=41234)
        snapshot.forget()
        tok = userpage._stored_tokens()[UID1]
        self.assertEqual(userpage.url(UID1), "https://%s:41234/p/%s" % (HOST, tok))
        st = userpage.status(UID1)
        self.assertTrue(st["public"])
        self.assertEqual(st["note"], "")
        self.write_access(public=True, port=443, host="vpn.example.org")   # a domain and the default port
        snapshot.forget()
        self.assertEqual(userpage.url(UID1), "https://vpn.example.org/p/%s" % tok)
        self.write_access(public=False, port=41234)
        snapshot.forget()
        self.assertEqual(userpage.url(UID1), "/p/%s" % tok)

    def test_public_mode_without_an_address(self):
        self.token()
        stt = state.load()
        model.gw(stt)["host"] = ""
        state.save(stt)
        self.write_access(public=True, port=41234)
        snapshot.forget()
        st = userpage.status(UID1)
        self.assertIsNone(st["url"])
        self.assertIn("gw host", st["note"])

    def test_ipv6_literal_is_bracketed(self):
        acc = {"scheme": "https", "port": 41234}
        self.assertEqual(userpage._format_url(acc, "2001:db8::1", "T"), "https://[2001:db8::1]:41234/p/T")

    def test_changes_are_logged_without_the_token(self):
        tok = self.token()
        userpage.rotate(UID1)
        userpage.disable(UID1)
        with open(self.path("/var/log/kaskad.log"), encoding="utf-8") as f:
            log = f.read()
        self.assertIn("Страница пользователя включена", log)
        self.assertIn("ссылка заменена", log)
        self.assertIn("выключена", log)
        self.assertNotIn(tok, log)

    def test_qr_status_says_how_to_install(self):
        self.assertEqual(userpage.qr_status(), {"available": True, "note": ""})
        self.w.runner.binaries.discard("qrencode")
        st = userpage.qr_status()
        self.assertFalse(st["available"])
        self.assertIn("apt-get install qrencode", st["note"])


class NotFoundTest(PageCase):
    """A link that opens nothing — one and the same page «Ссылка больше не действует»; any other path — one 404."""

    def test_one_answer_for_everything_that_is_not_a_live_link(self):
        tok = self.token()
        wrong = "A" * 43
        short = "A" * 20
        links = [("GET", "/p/%s" % wrong), ("GET", "/p/%s/" % wrong), ("GET", "/p/%s" % short),
                 ("GET", "/p/%s" % (tok + "x")), ("GET", "/p/%s" % tok[:-12])]                # a cut or longer link
        probes = [
            ("POST", "/p/%s/keys" % wrong),
            ("GET", "/p/%s/keys" % wrong), ("GET", "/p/%s/file/%s/kaskad-wg.conf" % (wrong, "B" * 32)),
            ("GET", "/p/"), ("GET", "/p"), ("GET", "/p/nothing/here"), ("GET", "/p/nothing"),
            ("POST", "/p/%s" % wrong), ("PUT", "/p/%s" % wrong), ("DELETE", "/p/%s" % wrong),
            ("GET", "/p/%s/unknown" % wrong), ("POST", "/p/whatever"),
        ]
        baseline = self.sig(userpage.not_found())
        gone = self.sig(userpage.gone_page())
        self.assertEqual((baseline[0], gone[0]), (404, 404))
        for method, path in probes:
            with self.subTest(method=method, path=path):
                resp = self.call(method, path, ip="198.51.100.%d" % (hash((method, path)) % 200 + 1))
                self.assertEqual(self.sig(resp), baseline)
        for method, path in links:
            with self.subTest(method=method, path=path):
                resp = self.call(method, path, ip="198.51.100.%d" % (hash((method, path)) % 200 + 1))
                self.assertEqual(self.sig(resp), gone)
        # a live link with a path that does not exist: the same 404 as well
        self.assertEqual(self.sig(self.call("GET", "/p/%s/nothing" % tok)), baseline)
        self.assertEqual(self.sig(self.call("GET", "/p/%s/file/%s/kaskad-wg.conf" % (tok, "B" * 32))), baseline)

    def test_an_old_link_says_so_and_nothing_else(self):
        """T-G1b N8: a replaced or switched-off link gave a bare «404 Not Found». Now the page says the link no longer
        works and whom to ask — the same bytes for every link that opens nothing (one that never existed, a replaced
        one, a switched-off one): nothing tells whether it ever existed."""
        old = self.token()
        userpage.rotate(UID1)
        off = self.token(UID2)
        userpage.disable(UID2)
        answers = [self.shell(t, ip="198.51.100.%d" % i) for i, t in enumerate((old, off, "Z" * 43), 50)]
        self.assertEqual(len(set(self.sig(r) for r in answers)), 1)
        text = answers[0].body.decode("utf-8")
        self.assertEqual(answers[0].status, 404)
        self.assertIn('name="kaskad-state" content="gone"', text)
        self.assertIn('name="kaskad-nonce" content=""', text)             # nothing to reveal
        self.assertIn('<b id="up-name"></b>', text)                        # no name
        for t in (old, off):
            self.assertNotIn(t, text)
        self.assertEqual(answers[0].headers["Content-Security-Policy"], userpage.CSP)
        self.assertEqual(answers[0].headers["Cache-Control"], userpage.SECURE_HEADERS["Cache-Control"])
        i18n = self.read_static("user-i18n-ru.js")
        js = self.read_static("user.js")
        self.assertIn("gone_title: 'Ссылка больше не действует'", i18n)
        self.assertIn("Попросите новую ссылку у того, кто её прислал.", i18n)
        self.assertIn("if (meta('kaskad-state') === 'gone') { showGone(); return; }", js)

    def read_static(self, name):
        with open(os.path.join(userpage._STATIC, name), encoding="utf-8") as f:
            return f.read()

    def test_404_carries_nothing_of_the_real_answers(self):
        body = userpage.not_found().body
        self.assertIn(b"404 Not Found", body)
        self.assertNotIn(b"Kaskad", body)
        self.assertEqual(set(userpage.not_found().headers), {"Content-Type"})     # no CSP, no no-store: like a plain site

    def test_a_deleted_persons_link_is_the_same_404(self):
        tok = self.token(UID2)
        st = state.load()
        g = model.gw(st)
        g["devices"] = [d for d in g["devices"] if d["user"] != UID2]
        g["users"] = [u for u in g["users"] if u["id"] != UID2]
        state.save(st)
        wrong = self.call("GET", "/p/%s" % ("Q" * 43), ip="198.51.100.31")
        gone = self.call("GET", "/p/%s" % tok, ip="198.51.100.32")
        self.assertEqual(self.sig(wrong), self.sig(gone))

    def test_no_pages_at_all(self):
        self.assertEqual(self.sig(self.shell("A" * 43)), self.sig(userpage.gone_page()))

    def test_the_server_answers_a_banned_address_the_same_way(self):
        from kaskad.panel import server
        self.assertEqual(self.sig(server.page_refused("GET", "/p/" + "A" * 43)), self.sig(userpage.gone_page()))
        self.assertEqual(self.sig(server.page_refused("HEAD", "/p/" + "A" * 43 + "/")), self.sig(userpage.gone_page()))
        for method, path in (("POST", "/p/" + "A" * 43 + "/keys"), ("GET", "/p/x/y"), ("GET", "/t/good")):
            self.assertEqual(self.sig(server.page_refused(method, path)), self.sig(userpage.not_found()), path)

    def test_static_files_do_not_depend_on_a_link_and_are_not_a_miss(self):
        for _ in range(30):
            self.assertEqual(self.call("GET", "/p/user.css", ip="198.51.100.40").status, 200)
        self.assertFalse(userpage.GUARDS.banned("198.51.100.40"))


class BanTest(PageCase):
    def misses(self, n, ip=IP):
        for i in range(n):
            self.assertEqual(self.shell(("%043d" % i).replace("0", "x"), ip=ip).status, 404)

    def test_ten_misses_in_five_minutes_ban_for_fifteen(self):
        tok = self.token()
        self.misses(9)
        self.assertEqual(self.shell(tok).status, 200)                       # the 10th is a hit: no ban yet
        self.misses(1)                                                      # the 10th miss
        self.assertTrue(userpage.GUARDS.banned(IP))
        self.assertEqual(self.sig(self.shell(tok)), self.sig(userpage.gone_page()))     # even the right one: as a wrong
        self.assertEqual(self.call("POST", "/p/%s/keys" % tok, body={"nonce": "x" * 32}).status, 404)
        self.assertEqual(self.shell(tok, ip="198.51.100.99").status, 200)    # other addresses are not affected
        self.clock[0] += 899
        self.assertEqual(self.shell(tok).status, 404)
        self.clock[0] += 2
        self.assertEqual(self.shell(tok).status, 200)                        # 15 minutes passed

    def test_misses_older_than_five_minutes_do_not_count(self):
        tok = self.token()
        self.misses(9)
        self.clock[0] += 301
        self.misses(9)
        self.assertEqual(self.shell(tok).status, 200)

    def test_hits_do_not_count(self):
        tok = self.token()
        for _ in range(40):
            self.assertEqual(self.shell(tok).status, 200)

    def test_unknown_subpath_of_a_live_link_is_no_miss(self):
        tok = self.token()
        for i in range(20):
            self.assertEqual(self.call("GET", "/p/%s/x%d" % (tok, i)).status, 404)
        self.assertEqual(self.shell(tok).status, 200)

    def test_other_paths_under_p_count(self):
        for i in range(10):
            self.call("GET", "/p/zzz%d" % i)
        self.assertTrue(userpage.GUARDS.banned(IP))

    def test_ipv6_misses_count_per_64_and_mapped_addresses_as_ipv4(self):
        tok = self.token()
        for i in range(10):
            self.shell("%043d" % i, ip="2001:db8:1:2:%x::%x" % (i, i + 1))      # ten hosts of one /64
        self.assertTrue(userpage.GUARDS.banned("2001:db8:1:2:ffff:ffff:ffff:ffff"))
        self.assertEqual(self.shell(tok, ip="2001:db8:1:2:abcd::1").status, 404)
        self.assertEqual(self.shell(tok, ip="2001:db8:1:3::1").status, 200)      # the next /64 is another host
        for i in range(10):
            self.shell("%043d" % i, ip="::ffff:198.51.100.99")
        self.assertTrue(userpage.GUARDS.banned("198.51.100.99"))
        self.assertEqual(userpage.ip_key("fe80::1%eth0"), "fe80:0000:0000:0000::/64")
        self.assertEqual(userpage.ip_key("not an address"), "not an address")
        self.assertEqual(userpage.ip_key("-"), "-")

    def test_the_tables_are_bounded(self):
        for i in range(userpage.TABLE_MAX + 50):
            userpage.GUARDS.miss("10.%d.%d.%d" % (i // 65536, i // 256 % 256, i % 256))
        self.assertLessEqual(len(userpage.GUARDS._misses), userpage.TABLE_MAX)

    def test_the_ban_is_logged_once_in_a_while_without_a_token(self):
        self.misses(10)
        with open(self.path("/var/log/kaskad.log"), encoding="utf-8") as f:
            log = f.read()
        self.assertIn("заблокирован на 15 мин", log)
        self.assertIn(IP, log)


class ShellTest(PageCase):
    def test_first_html_has_no_secrets(self):
        tok = self.token()
        userpage.enable(UID2)
        resp = self.shell(tok)
        self.assertEqual(resp.status, 200)
        text = resp.body.decode("utf-8")
        self.assert_no_secrets(text)
        self.assertNotIn(tok, text)                                        # not even the link itself (it is a secret too)
        self.assertNotIn(userpage._stored_tokens()[UID2], text)
        self.assertNotIn("WireGuard", text)
        for d in ("Телефон", "iPhone Маши", "Ноутбук"):
            self.assertNotIn(d, text)                                      # no device list before the gesture

    def test_the_secrets_exist_in_the_fixture(self):
        self.assertGreater(len(self.all_secret_values()), 10)              # the check above is not vacuous

    def test_name_is_escaped(self):
        text = self.shell(self.token()).body.decode("utf-8")
        self.assertIn("Мама &lt;b&gt;x&lt;/b&gt; &amp; &quot;кавычки&quot;", text)
        self.assertNotIn("<b>x</b>", text)

    def test_a_name_that_looks_like_a_mark_stays_a_name(self):
        tok = self.token()
        st = state.load()
        model.get_user(st, UID1)["name"] = "{{NONCE}} {{OS}} {{V_JS}}"
        state.save(st)
        text = self.shell(tok).body.decode("utf-8")
        self.assertIn('<b id="up-name">{{NONCE}} {{OS}} {{V_JS}}</b>', text)         # not filled in a second time
        self.assertEqual(len(re.findall(r'content="[A-Za-z0-9_-]{32}"', text)), 1)    # one nonce, in the meta only

    def test_headers(self):
        resp = self.shell(self.token())
        h = resp.headers
        self.assertEqual(h["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("no-store", h["Cache-Control"])
        self.assertIn("default-src 'none'", h["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", h["Content-Security-Policy"])
        self.assertNotIn("http", h["Content-Security-Policy"])
        self.assertIn("noindex", h["X-Robots-Tag"])
        self.assertEqual(h["Referrer-Policy"], "no-referrer")
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")

    def test_assets_are_linked_without_the_link_and_without_external_addresses(self):
        text = self.shell(self.token()).body.decode("utf-8")
        for name in ("user.css", "user.js", "user-i18n-ru.js"):
            self.assertRegex(text, r'/p/%s\?v=[0-9a-f]{10}"' % re.escape(name))
        self.assertNotRegex(text, r"https?://")
        self.assertNotIn("<script>", text)                                 # no inline script
        self.assertNotRegex(text, r"\son[a-z]+=")                          # no inline handlers
        self.assertNotIn("style=", text)

    def test_nonce_is_in_the_meta_and_differs_every_time(self):
        tok = self.token()
        a, b = self.nonce_of(self.shell(tok)), self.nonce_of(self.shell(tok))
        self.assertNotEqual(a, b)
        self.assertRegex(a, r"^[A-Za-z0-9_-]{32}$")

    def test_os_hint(self):
        tok = self.token()
        for ua, want in ((UA_IPHONE, "ios"), (UA_ANDROID, "android"), (UA_WINDOWS, "windows"), (UA_MAC, "macos")):
            text = self.shell(tok, headers={"user-agent": ua}).body.decode("utf-8")
            self.assertIn('name="kaskad-os" content="%s"' % want, text)

    def test_trailing_slash_is_the_same_page(self):
        tok = self.token()
        self.assertEqual(self.call("GET", "/p/%s/" % tok).status, 200)

    def test_a_link_preview_robot_gets_no_name_and_no_nonce(self):
        tok = self.token()
        resp = self.shell(tok, headers={"user-agent": UA_TELEGRAM})
        text = resp.body.decode("utf-8")
        self.assertEqual(resp.status, 200)
        self.assertNotIn("Мама", text)
        self.assertIn('name="kaskad-nonce" content=""', text)

    def test_a_paused_person_sees_the_state_and_gets_no_nonce(self):
        tok = self.token()
        st = state.load()
        model.get_user(st, UID1)["enabled"] = False
        state.save(st)
        text = self.shell(tok).body.decode("utf-8")
        self.assertIn('name="kaskad-state" content="paused"', text)
        self.assertIn('name="kaskad-nonce" content=""', text)
        self.assertEqual(self.call("POST", "/p/%s/keys" % tok, body={"nonce": ""}).status, 409)

    def test_page_routes_are_registered_with_auth_page(self):
        for method, path in (("GET", "/p/%s" % ("A" * 43)), ("POST", "/p/%s/keys" % ("A" * 43)),
                             ("GET", "/p/%s/file/%s/a.conf" % ("A" * 43, "B" * 32)), ("GET", "/p/anything")):
            route, _ = routes.find(method, path)
            self.assertEqual(route.auth, "page", (method, path))
            self.assertFalse(route.csrf)
        route, _ = routes.find("GET", "/p/user.js")
        self.assertEqual(route.auth, "public")

    def test_the_module_is_listed_for_the_server(self):
        from kaskad import panel
        self.assertIn("kaskad.panel.userpage", panel.MODULES)

    def test_a_missing_template_is_a_clean_error(self):
        tok = self.token()
        with mock.patch.object(userpage, "_STATIC", self.root), mock.patch.dict(userpage._asset_cache, clear=True):
            resp = self.shell(tok)
        self.assertEqual(resp.status, 500)
        self.assertNotIn(tok, resp.body.decode("utf-8"))


class NonceTest(PageCase):
    def test_a_nonce_works_once(self):
        tok = self.token()
        nonce = self.nonce_of(self.shell(tok))
        self.assertEqual(self.keys(tok, nonce).status, 200)
        again = self.keys(tok, nonce)
        self.assertEqual(again.status, 409)
        self.assertEqual(json.loads(again.body.decode("utf-8"))["reason"], "reveal")
        self.assertNotIn("PRIVKEY", again.body.decode("utf-8"))

    def test_a_nonce_lives_five_minutes(self):
        tok = self.token()
        nonce = self.nonce_of(self.shell(tok))
        self.clock[0] += 301
        self.assertEqual(self.keys(tok, nonce).status, 409)
        nonce = self.nonce_of(self.shell(tok))
        self.clock[0] += 299
        self.assertEqual(self.keys(tok, nonce).status, 200)

    def test_a_nonce_of_another_page_is_refused(self):
        a, b = self.token(UID1), self.token(UID2)
        nonce_a = self.nonce_of(self.shell(a))
        self.assertEqual(self.keys(b, nonce_a).status, 409)
        self.assertEqual(self.keys(a, nonce_a).status, 200)                 # and was not spent by the attempt on b

    def test_no_nonce_and_garbage_are_refused(self):
        tok = self.token()
        for body in ({}, {"nonce": ""}, {"nonce": 5}, {"nonce": "x" * 32}, {"nonce": ["a"]}):
            with self.subTest(body=body):
                self.assertEqual(self.call("POST", "/p/%s/keys" % tok, body=body).status, 409)
        self.assertEqual(self.call("POST", "/p/%s/keys" % tok, body=b"not json").status, 409)

    def test_every_answer_brings_the_next_nonce(self):
        tok = self.token()
        doc = self.keys_json(tok)
        self.assertEqual(self.keys(tok, doc["nonce"]).status, 200)

    def test_the_nonce_table_is_bounded_and_forgets_the_old(self):
        tok = self.token()
        first = self.nonce_of(self.shell(tok))
        for _ in range(userpage.TABLE_MAX + 5):
            userpage.GUARDS.nonce_issue("page")
        self.assertEqual(self.keys(tok, first).status, 409)
        self.assertLessEqual(len(userpage.GUARDS._nonces), userpage.TABLE_MAX)

    def test_keys_only_by_post(self):
        tok = self.token()
        resp = self.call("GET", "/p/%s/keys" % tok)
        self.assertEqual(resp.status, 405)
        self.assertEqual(resp.headers["Allow"], "POST")
        self.assertNotIn("PRIVKEY", resp.body.decode("utf-8"))
        self.assertNotIn("nonce", json.loads(resp.body.decode("utf-8")))

    def test_robots_forms_and_other_sites_get_a_404(self):
        tok = self.token()
        nonce = self.nonce_of(self.shell(tok))
        base = self.sig(userpage.not_found())
        self.assertEqual(self.sig(self.keys(tok, nonce, headers={"user-agent": UA_TELEGRAM})), base)
        self.assertEqual(self.sig(self.keys(tok, nonce, headers={"content-type": "application/x-www-form-urlencoded"})), base)
        self.assertEqual(self.sig(self.keys(tok, nonce, headers={"sec-fetch-site": "cross-site"})), base)
        self.assertEqual(self.keys(tok, nonce).status, 200)                  # none of those spent the nonce


class KeysTest(PageCase):
    def test_devices_of_this_person_only_with_every_item(self):
        tok = self.token()
        doc = self.keys_json(tok)
        self.assertEqual(doc["user"], {"id": UID1, "name": NAME1})
        self.assertEqual([d["name"] for d in doc["devices"]], ["Телефон", "iPhone Маши", "Ноутбук", "Старый планшет"])
        self.assertNotIn("Роутер на даче", json.dumps(doc, ensure_ascii=False))     # the other person's device
        self.assertNotIn(passwd("d-00000005"), json.dumps(doc))
        phone = doc["devices"][0]
        self.assertEqual((phone["platform"], phone["kind"], phone["enabled"]), ("android", "phone", True))
        wg = next(i for i in phone["inbounds"] if i["kind"] == "wg")
        fmts = [it["format"] for it in wg["items"]]
        self.assertEqual(fmts, ["vpn", "qr", "conf", "fields", "guide", "link", "mobileconfig", "ps1", "ovpn", "conf"])
        vpn = wg["items"][0]
        self.assertEqual(vpn["body"], "vpn://" + priv("d-00000001", "wg"))
        self.assertEqual(vpn["notes"], ["Вставьте ссылку в приложение."])
        fields = wg["items"][3]["fields"]
        self.assertEqual(fields, [["Сервер", HOST], ["Пароль", passwd("d-00000001")]])
        self.assertEqual(wg["items"][4]["body"], "Откройте приложение.\nНажмите «+».")
        self.assertEqual(doc["gateway"], {"enabled": True, "host": HOST})
        self.assertTrue(doc["qr"])

    def test_steps_come_in_the_words_of_this_page(self):
        """T-G1b N4: «Как подключить» of each way in, in the words of this page — «Скопировать», «Скачать файл»;
        never the menu's «Сохранить в файлы», PuTTY or a path of the SSH menu."""
        from kaskad.gw import issue_os
        doc = self.keys_json(self.token())
        phone, iphone, laptop = doc["devices"][:3]
        wg = next(i for i in phone["inbounds"] if i["kind"] == "wg")
        self.assertEqual(wg["howto"], issue_os.howto("android", "wg", where="page"))
        self.assertIn("другом экране", wg["howto"][2])                     # its own screen cannot be scanned
        ike = next(i for i in laptop["inbounds"] if i["kind"] == "ikev2")
        self.assertEqual(ike["howto"][0], "Скачайте скрипт kaskad-ikev2.ps1 кнопкой «Скачать файл» ниже.")
        text = json.dumps(doc, ensure_ascii=False)
        for menu_word in ("Сохранить в файлы", "PuTTY", "gokaskad →", "Termius"):
            self.assertNotIn(menu_word, text)

    def test_inbounds_best_first_by_the_platform(self):
        doc = self.keys_json(self.token())
        by = dict((d["name"], d) for d in doc["devices"])
        self.assertEqual([i["kind"] for i in by["Телефон"]["inbounds"]], ["awg2", "wg"])        # Android: AmneziaWG first
        self.assertEqual([i["recommended"] for i in by["Телефон"]["inbounds"]], [True, False])
        self.assertEqual([i["kind"] for i in by["Ноутбук"]["inbounds"]], ["ikev2", "wg"])        # Windows: IKEv2, then the rest
        self.assertEqual([i["title"] for i in by["Телефон"]["inbounds"]], ["AmneziaWG 2.0", "WireGuard"])

    def test_a_disabled_device_is_listed_without_settings(self):
        tablet = next(d for d in self.keys_json(self.token())["devices"] if d["name"] == "Старый планшет")
        self.assertFalse(tablet["enabled"])
        self.assertEqual(tablet["inbounds"], [])

    def test_a_person_without_devices(self):
        st = state.load()
        g = model.gw(st)
        g["devices"] = [d for d in g["devices"] if d["user"] != UID2]
        state.save(st)
        doc = self.keys_json(self.token(UID2))
        self.assertEqual(doc["devices"], [])

    def test_inbound_off_or_missing_is_not_offered(self):
        st = state.load()
        model.gw(st)["inbounds"]["awg2"]["enabled"] = False
        state.save(st)
        phone = self.keys_json(self.token())["devices"][0]
        self.assertEqual([i["kind"] for i in phone["inbounds"]], ["wg"])

    def test_an_adapter_error_is_reported_for_that_inbound_only(self):
        from kaskad.gw.inbounds.base import InboundError

        def boom(st, sec, device, os=None):
            raise InboundError("Сертификат ещё не создан: kaskadctl gw apply.")
        self.ads["awg2"].issue = boom
        phone = self.keys_json(self.token())["devices"][0]
        awg2 = phone["inbounds"][0]
        self.assertEqual(awg2["kind"], "awg2")
        self.assertEqual(awg2["error"], "Сертификат ещё не создан: kaskadctl gw apply.")
        self.assertEqual(awg2["items"], [])
        self.assertIsNone(phone["inbounds"][1]["error"])
        self.assertTrue(phone["inbounds"][1]["items"])

    def test_a_bug_in_an_adapter_does_not_leak_details(self):
        def boom(st, sec, device, os=None):
            raise RuntimeError("secret detail %s" % priv("d-00000001", "wg"))
        self.ads["awg2"].issue = boom
        resp = self.keys(self.token())
        self.assertEqual(resp.status, 200)
        awg2 = json.loads(resp.body.decode("utf-8"))["devices"][0]["inbounds"][0]
        self.assertEqual(awg2["error"], "Не удалось подготовить настройки этого протокола.")
        self.assertNotIn("secret detail", resp.body.decode("utf-8"))
        with open(self.path("/var/log/kaskad.log"), encoding="utf-8") as f:
            self.assertNotIn(priv("d-00000001", "wg"), f.read())

    def test_response_is_never_cached_and_has_the_page_headers(self):
        resp = self.keys(self.token())
        self.assertEqual(resp.headers["Content-Type"], "application/json; charset=utf-8")
        self.assertIn("no-store", resp.headers["Cache-Control"])
        self.assertIn("default-src 'none'", resp.headers["Content-Security-Policy"])
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")

    def test_paused_person_gets_no_settings(self):
        tok = self.token()
        nonce = self.nonce_of(self.shell(tok))
        st = state.load()
        model.get_user(st, UID1)["enabled"] = False
        state.save(st)
        resp = self.keys(tok, nonce)
        self.assertEqual(resp.status, 403)
        doc = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(doc["reason"], "paused")
        self.assertNotIn("PRIVKEY", resp.body.decode("utf-8"))

    def test_gateway_off_is_told(self):
        st = state.load()
        model.gw(st)["enabled"] = False
        state.save(st)
        self.assertFalse(self.keys_json(self.token())["gateway"]["enabled"])

    def test_the_person_vanishing_during_the_build_is_a_404(self):
        tok = self.token()
        nonce = self.nonce_of(self.shell(tok))
        st = state.load()
        g = model.gw(st)
        g["devices"] = [d for d in g["devices"] if d["user"] != UID1]
        g["users"] = [u for u in g["users"] if u["id"] != UID1]
        state.save(st)
        self.assertEqual(self.keys(tok, nonce).status, 404)

    def test_busy_when_a_change_holds_the_lock(self):
        tok = self.token()
        nonce = self.nonce_of(self.shell(tok))
        with mock.patch.object(userpage, "LOCK_WAIT", 0.2), lock_held_elsewhere():     # T1: another process
            resp = self.keys(tok, nonce)
        self.assertEqual(resp.status, 503)
        self.assertEqual(resp.headers["Retry-After"], str(userpage.RETRY_AFTER))
        doc = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(doc["reason"], "busy")
        self.assertEqual(self.keys(tok, doc["nonce"]).status, 200)           # the next nonce in the busy answer works

    def test_busy_when_every_place_is_taken(self):
        tok = self.token()
        nonce = self.nonce_of(self.shell(tok))
        places = [userpage._BUILD.acquire() for _ in range(userpage.BUILD_PARALLEL)]
        try:
            with mock.patch.object(userpage, "BUILD_WAIT", 0.05):
                resp = self.keys(tok, nonce)
            self.assertEqual(resp.status, 503)
        finally:
            for _ in places:
                userpage._BUILD.release()
        self.assertEqual(self.keys(tok, json.loads(resp.body.decode("utf-8"))["nonce"]).status, 200)

    def test_keys_are_not_in_the_log(self):
        self.keys(self.token())
        with open(self.path("/var/log/kaskad.log"), encoding="utf-8") as f:
            log = f.read()
        self.assertIn("настройки показаны", log)
        for pat in ("PRIVKEY", "PASSWD", "QRPAYLOAD"):
            self.assertNotIn(pat, log)

    def test_unknown_format_and_odd_bodies_do_not_break_the_answer(self):
        def odd(st, sec, device, os=None):
            items = [{"format": "weird", "title": "x", "body": {"a": 1}, "filename": "a.bin", "notes": [None, "ok"]},
                     {"format": "fields", "title": "f", "body": [["a", "b"], "junk", ["only"]], "filename": "", "notes": []},
                     {"format": "conf", "title": "c", "body": "text", "filename": "../../etc/passwd", "notes": []},
                     {"format": "qr", "title": "q", "body": "single text", "filename": None, "notes": []}]
            return {"items": items}
        self.ads["awg2"].issue = odd
        awg2 = self.keys_json(self.token())["devices"][0]["inbounds"][0]
        weird, fields, conf, qr = awg2["items"]
        self.assertEqual(weird["body"], "")
        self.assertEqual(weird["notes"], ["ok"])
        self.assertEqual(fields["fields"], [["a", "b"]])
        self.assertNotIn("file", conf)                                       # an unsafe file name gets no link
        self.assertEqual(len(qr["qr"]), 1)


class FileTest(PageCase):
    def first_file(self, doc, dev=0, kind="wg", name="kaskad-wg.conf"):
        inb = next(i for i in doc["devices"][dev]["inbounds"] if i["kind"] == kind)
        return next(it for it in inb["items"] if it.get("filename") == name)

    def test_files_are_listed_with_a_grant_link(self):
        tok = self.token()
        doc = self.keys_json(tok)
        it = self.first_file(doc)
        self.assertRegex(it["file"], r"^/p/%s/file/[A-Za-z0-9_-]{32}/kaskad-wg\.conf$" % tok)
        self.assertEqual(it["filename"], "kaskad-wg.conf")
        for fmt in ("vpn", "link", "guide"):                                  # these have no file
            self.assertTrue(all("file" not in x for x in doc["devices"][0]["inbounds"][1]["items"] if x["format"] == fmt))

    def test_download(self):
        tok = self.token()
        it = self.first_file(self.keys_json(tok))
        resp = self.call("GET", it["file"])
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.body.decode("utf-8"), "[Interface]\nPrivateKey = %s\nEndpoint = %s\n"
                         % (priv("d-00000001", "wg"), HOST))
        self.assertEqual(resp.headers["Content-Disposition"], 'attachment; filename="kaskad-wg.conf"')
        self.assertEqual(resp.headers["Content-Type"], "application/octet-stream")
        self.assertIn("no-store", resp.headers["Cache-Control"])
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")

    def test_content_types_by_extension(self):
        tok = self.token()
        doc = self.keys_json(tok)
        want = {"kaskad-wg.mobileconfig": "application/x-apple-aspen-config", "kaskad-wg.ovpn": "application/x-openvpn-profile",
                "kaskad-wg-ca.crt": "application/x-x509-ca-cert", "kaskad-wg.ps1": "application/octet-stream"}
        for name, ctype in want.items():
            it = self.first_file(doc, name=name)
            self.assertEqual(self.call("GET", it["file"]).headers["Content-Type"], ctype, name)

    def test_each_device_and_inbound_has_its_own_file(self):
        tok = self.token()
        doc = self.keys_json(tok)
        phone_wg = self.call("GET", self.first_file(doc, dev=0)["file"]).body.decode("utf-8")
        iphone_wg = self.call("GET", self.first_file(doc, dev=1)["file"]).body.decode("utf-8")
        phone_awg = self.call("GET", self.first_file(doc, dev=0, kind="awg2", name="kaskad-awg2.conf")["file"]).body.decode("utf-8")
        self.assertIn(priv("d-00000001", "wg"), phone_wg)
        self.assertIn(priv("d-00000002", "wg"), iphone_wg)
        self.assertIn(priv("d-00000001", "awg2"), phone_awg)

    def test_the_file_is_built_fresh(self):
        tok = self.token()
        it = self.first_file(self.keys_json(tok))
        sec = secrets.load()
        sec.set("dev.d-00000001.wg.private", "ROTATED-KEY-abcdefghijklmnop")
        sec.save()
        snapshot.forget()
        self.assertIn("ROTATED-KEY-abcdefghijklmnop", self.call("GET", it["file"]).body.decode("utf-8"))

    def test_a_grant_lives_one_hour(self):
        tok = self.token()
        it = self.first_file(self.keys_json(tok))
        self.clock[0] += 3599
        self.assertEqual(self.call("GET", it["file"]).status, 200)
        self.clock[0] += 2
        self.assertEqual(self.sig(self.call("GET", it["file"])), self.sig(userpage.not_found()))

    def test_a_grant_works_only_for_its_page_and_its_file_name(self):
        a, b = self.token(UID1), self.token(UID2)
        it = self.first_file(self.keys_json(a))
        grant = it["file"].split("/")[4]
        base = self.sig(userpage.not_found())
        self.assertEqual(self.sig(self.call("GET", "/p/%s/file/%s/kaskad-wg.conf" % (b, grant))), base)     # another page
        self.assertEqual(self.sig(self.call("GET", "/p/%s/file/%s/other.conf" % (a, grant))), base)         # not a file of it
        self.assertEqual(self.sig(self.call("GET", "/p/%s/file/%s/kaskad-wg.conf" % (a, "Z" * 32))), base)  # unknown grant
        self.assertEqual(self.sig(self.call("GET", "/p/%s/file/%s/vpn" % (a, grant))), base)                # not a file format

    def test_no_files_for_robots_a_paused_person_or_a_vanished_device(self):
        tok = self.token()
        it = self.first_file(self.keys_json(tok))
        base = self.sig(userpage.not_found())
        self.assertEqual(self.sig(self.call("GET", it["file"], headers={"user-agent": UA_TELEGRAM})), base)
        st = state.load()
        g = model.gw(st)
        g["devices"] = [d for d in g["devices"] if d["id"] != "d-00000001"]
        state.save(st)
        self.assertEqual(self.sig(self.call("GET", it["file"])), base)
        st = state.load()
        model.get_user(st, UID1)["enabled"] = False
        state.save(st)
        self.assertEqual(self.sig(self.call("GET", self.first_file(doc=self.fake_doc(tok), dev=0)["file"])), base)

    def fake_doc(self, tok):
        # a link as the earlier answer would have carried it (the person is paused now: no new answer)
        grant = userpage.GUARDS.grant_issue(userpage.page_key(tok), "d-00000002", "wg", "ios")
        return {"devices": [{"inbounds": [{"kind": "wg", "items": [
            {"filename": "kaskad-wg.conf", "file": "/p/%s/file/%s/kaskad-wg.conf" % (tok, grant)}]}]}]}

    def test_a_wrong_link_with_a_good_grant_is_a_miss(self):
        tok = self.token()
        it = self.first_file(self.keys_json(tok))
        grant = it["file"].split("/")[4]
        for _ in range(10):
            self.call("GET", "/p/%s/file/%s/kaskad-wg.conf" % ("W" * 43, grant), ip="198.51.100.50")
        self.assertTrue(userpage.GUARDS.banned("198.51.100.50"))

    def test_the_grant_table_is_bounded(self):
        for i in range(userpage.TABLE_MAX + 10):
            userpage.GUARDS.grant_issue("page", "d", "wg", "ios")
        self.assertLessEqual(len(userpage.GUARDS._grants), userpage.TABLE_MAX)


class QrTest(PageCase):
    def test_qrencode_gets_the_text_on_stdin_and_never_in_argv(self):
        tok = self.token()
        self.keys_json(tok)
        self.assertTrue(self.qr_calls)
        for argv, inp in self.qr_calls:
            # the byte mode and the level as the panel picks it (qr.version_of, 3.3.3 №38) — the same version on /p/
            self.assertEqual(argv, ["qrencode", "-8", "-l", qrmod.version_of(inp)[1], "-t", "SVG", "-m", "0", "-o", "-"])
            if inp.startswith("https://"):                               # «Установите приложение»: public store links
                continue
            self.assertTrue(inp.startswith("QRPAYLOAD-"))
            self.assertFalse(any("QRPAYLOAD" in a for a in argv))
            self.assertFalse(any("PRIVKEY" in a for a in argv))
        self.assertEqual(sorted(set(inp for _, inp in self.qr_calls if not inp.startswith("https://")))[:2],
                         ["QRPAYLOAD-%s-1" % priv("d-00000001", "awg2"), "QRPAYLOAD-%s-2" % priv("d-00000001", "awg2")])

    def test_response_carries_the_matrix_as_path_data(self):
        doc = self.keys_json(self.token())
        wg = next(i for i in doc["devices"][1]["inbounds"] if i["kind"] == "wg")
        qr = next(it for it in wg["items"] if it["format"] == "qr")
        key = priv("d-00000002", "wg")
        self.assertEqual(len(qr["qr"]), 2)
        for i, code in enumerate(qr["qr"], 1):
            self.assertEqual(code["n"], 21)
            self.assertEqual(matrix_of_path(code["d"], 21), qr_rows("QRPAYLOAD-%s-%d" % (key, i)))
        self.assertNotIn("QRPAYLOAD", json.dumps(doc))                      # the text behind a code is not sent
        self.assertNotIn("<svg", json.dumps(doc))                           # and no markup: the page draws it

    def test_every_shape_of_qrencode_output_is_read(self):
        rows = qr_rows("hello")
        for form in ("rect", "rle", "use", "path"):
            for margin in (0, 4):
                with self.subTest(form=form, margin=margin):
                    got = userpage.parse_qr_svg(svg_of(rows, form, margin))
                    self.assertIsNotNone(got)
                    self.assertEqual(got["n"], 21)
                    self.assertEqual(matrix_of_path(got["d"], 21), rows)

    def test_a_bigger_code(self):
        rows = qr_rows("a much longer text " * 20, n=61)
        got = userpage.parse_qr_svg(svg_of(rows, "rle", 4))
        self.assertEqual(got["n"], 61)
        self.assertEqual(matrix_of_path(got["d"], 61), rows)
        self.assertLess(len(got["d"]), 6000)                                # compact: runs, not modules

    def test_odd_svg_is_refused(self):
        rows = qr_rows("x")
        good = svg_of(rows)
        for name, svg in (("empty", ""), ("no viewBox", good.replace('viewBox="0 0 21 21"', "")),
                          ("not square", good.replace('viewBox="0 0 21 21"', 'viewBox="0 0 21 25"')),
                          ("too small", good.replace('viewBox="0 0 21 21"', 'viewBox="0 0 20 20"')),
                          ("no modules", svg_of([[0] * 21 for _ in range(21)])),
                          ("broken finder", svg_of([[0] + r[1:] for r in rows])),
                          ("text", "<html>not a qr</html>"), (None, None), ("huge", "x" * (userpage.QR_MAX_SVG + 1))):
            with self.subTest(name):
                self.assertIsNone(userpage.parse_qr_svg(svg))

    def test_no_qrencode_means_no_qr_but_the_rest_stays(self):
        self.w.runner.binaries.discard("qrencode")
        doc = self.keys_json(self.token())
        self.assertFalse(doc["qr"])
        wg = next(i for i in doc["devices"][1]["inbounds"] if i["kind"] == "wg")
        qr = next(it for it in wg["items"] if it["format"] == "qr")
        self.assertIsNone(qr["qr"])
        self.assertTrue(any(it["format"] == "vpn" and it["body"] for it in wg["items"]))
        self.assertEqual(self.qr_calls, [])

    def test_a_code_that_cannot_be_drawn_shows_its_text_only_when_that_is_the_one_way(self):
        self.w.runner.binaries.discard("qrencode")

        def issue(st, sec, device, os=None):
            items = [{"format": "qr", "title": "alone", "body": ["ONLY-WAY-TEXT"], "filename": None, "notes": []},
                     {"format": "qr", "title": "parts", "body": ["PART-1", "PART-2"], "filename": None, "notes": []},
                     {"format": "qr", "title": "beside", "body": ["[Interface]\nA = 1\n"], "filename": None, "notes": []},
                     {"format": "conf", "title": "conf", "body": "[Interface]\nA = 1\n", "filename": "a.conf", "notes": []}]
            return {"items": items}
        self.ads["awg2"].issue = issue
        alone, parts, beside, conf = self.keys_json(self.token())["devices"][0]["inbounds"][0]["items"]
        self.assertEqual((alone["qr"], alone["texts"]), (None, ["ONLY-WAY-TEXT"]))
        self.assertEqual((parts["qr"], "texts" in parts), (None, False))       # parts of a code are no text to paste
        self.assertEqual((beside["qr"], "texts" in beside), (None, False))     # the .conf beside it carries the text
        self.assertEqual(conf["body"], "[Interface]\nA = 1\n")

    def test_a_drawn_code_carries_no_text(self):
        doc = self.keys_json(self.token())
        for dev in doc["devices"]:
            for inb in dev["inbounds"]:
                for it in inb["items"]:
                    self.assertNotIn("texts", it)

    def test_a_failing_qrencode_or_too_long_text(self):
        self.qr_fail = True
        doc = self.keys_json(self.token())
        wg = next(i for i in doc["devices"][1]["inbounds"] if i["kind"] == "wg")
        self.assertIsNone(next(it for it in wg["items"] if it["format"] == "qr")["qr"])

    def test_every_form_through_the_page(self):
        for form in ("use", "path", "rle"):
            self.qr_form = form
            doc = self.keys_json(self.token())
            wg = next(i for i in doc["devices"][1]["inbounds"] if i["kind"] == "wg")
            qr = next(it for it in wg["items"] if it["format"] == "qr")
            self.assertEqual(len(qr["qr"]), 2, form)

    def test_text_that_is_not_text_gives_nothing(self):
        for bad in ("", None, 5, ["a"]):
            self.assertIsNone(userpage.qr_matrix(sysrun.default_runner(), bad))
        self.assertEqual(self.qr_calls, [])

    @unittest.skipUnless(shutil.which("qrencode"), "qrencode is not installed")
    def test_the_real_qrencode(self):
        """The real binary of this machine: its SVG is read into the same matrix its ASCII output shows."""
        runner = sysrun.SystemRunner()
        for text in ("hello", "vpn://" + "A" * 300, "https://example.org/" + "x" * 800):
            got = userpage.qr_matrix(runner, text)
            self.assertIsNotNone(got, text[:20])
            res = runner.run(["qrencode", "-8", "-l", qrmod.version_of(text)[1], "-t", "ASCII", "-m", "0", "-o", "-"],
                             input=text)
            # the byte mode (-8): exactly the version of version_of (KaskadQR) — /p/ and the panel do not differ at the
            # limit (R-333-qr №9; without -8 qrencode mixed modes and could make it one version less)
            self.assertEqual((got["n"] - 17) // 4, qrmod.version_of(text)[0])
            ascii_rows = [[1 if line[i:i + 2] == "##" else 0 for i in range(0, len(line), 2)]
                          for line in res.out.splitlines() if line.strip("\r")]
            self.assertEqual(got["n"], len(ascii_rows))
            self.assertEqual(matrix_of_path(got["d"], got["n"]), ascii_rows)


class LinksOfPeopleTest(PageCase):
    """The link belongs to the person: it dies with him (``gw user del``) and outlives «Удалить шлюз» that keeps people."""

    def setUp(self):
        super().setUp()
        st = state.load()
        model.gw(st)["enabled"] = False                       # nothing of the table and the routing to undo here
        state.save(st)

    def test_deleting_a_person_removes_his_link_and_only_his(self):
        a, b = self.token(UID1), self.token(UID2)
        ops.user_del(sysrun.default_runner(), UID2, force=True)
        self.assertNotIn("user.%s.page" % UID2, secrets.load())
        self.assertIn("user.%s.page" % UID1, secrets.load())
        self.assertEqual(self.shell(b).status, 404)
        self.assertEqual(self.shell(a).status, 200)

    def test_purge_keeps_the_links_of_the_people_it_keeps_and_drops_them_with_the_people(self):
        a = self.token(UID1)
        ops.purge(sysrun.default_runner())
        self.assertEqual(sorted(k for k in secrets.load() if k.startswith("user.")), ["user.%s.page" % UID1])
        self.assertEqual(self.shell(a).status, 200)           # the same link works after the gateway is back
        ops.purge(sysrun.default_runner(), users=True)
        self.assertEqual(dict(secrets.load()), {})
        self.assertEqual(self.shell(a).status, 404)


class RealAdaptersTest(PageCase):
    """The page with the REAL WireGuard and AmneziaWG adapters (their keys are pure Python): a person gets exactly what
    ``kaskadctl gw issue`` gives, and nothing of it is in the first HTML."""

    def setUp(self):
        super().setUp()
        registry.reset()                                      # the adapters of the modules instead of the test ones
        st = state.load()
        model.gw(st)["inbounds"] = {"wg": {"enabled": True, "port": 51820, "subnet": "10.77.1.0/24"},
                                    "awg2": {"enabled": True, "port": 41023, "subnet": "10.77.3.0/24", "line": "2.0"}}
        sec = secrets.load()
        for key in [k for k in sec if k.startswith("dev.") and (".wg." in k or ".awg2." in k)]:
            sec.delete(key)                                   # the fixture's stand-in keys: the adapters make real ones
        for kind in ("wg", "awg2"):
            registry.get(kind).prepare(st, sec)
        state.save(st)
        sec.save()

    def test_android_phone_gets_amneziawg_first_then_wireguard(self):
        tok = self.token()
        self.assert_no_secrets(self.shell(tok).body.decode("utf-8"))
        doc = self.keys_json(tok)
        phone = doc["devices"][0]
        self.assertEqual([i["kind"] for i in phone["inbounds"]], ["awg2", "wg"])
        awg, wg = phone["inbounds"]
        self.assertIsNone(awg["error"])
        self.assertEqual([it["format"] for it in awg["items"]], ["vpn", "qr", "qr", "conf"])
        self.assertTrue(awg["items"][0]["body"].startswith("vpn://"))
        self.assertTrue(all(c["n"] >= 21 for it in awg["items"] if it["format"] == "qr" for c in it["qr"]))
        self.assertEqual([it["format"] for it in wg["items"]], ["qr", "conf"])
        conf = wg["items"][1]
        self.assertEqual(conf["filename"], "gKP-telefon-wg.conf")       # Android: ≤ 15, wg + awg2 (owner №31)
        text = self.call("GET", conf["file"]).body.decode("utf-8")
        self.assertIn("[Interface]", text)
        self.assertIn("PrivateKey = %s" % secrets.get("dev.d-00000001.wg.private"), text)
        self.assertIn("Endpoint = %s:51820" % HOST, text)
        self.assertIn(conf["body"], text)                                  # the file is the text on the screen
        stdin = [inp for _, inp in self.qr_calls]
        self.assertIn(conf["body"], stdin)                                 # and the QR code carries the same text
        self.assertFalse(any("PrivateKey" in a for argv, _ in self.qr_calls for a in argv))

    def test_inbounds_that_are_not_prepared_say_so_for_that_inbound_only(self):
        sec = secrets.load()
        sec.delete("dev.d-00000001.awg2.private")
        sec.save()
        snapshot.forget()
        phone = self.keys_json(self.token())["devices"][0]
        awg = phone["inbounds"][0]
        self.assertIn("gw apply", awg["error"])
        self.assertEqual(phone["inbounds"][1]["error"], None)


class AdminRoutesTest(PageCase):
    def test_status_and_changes(self):
        route, _ = routes.find("POST", "/api/gw/users/%s/page" % UID1)
        self.assertEqual((route.auth, route.csrf), ("admin", True))             # a change needs the CSRF header
        route, _ = routes.find("GET", "/api/gw/users/%s/page" % UID1)
        self.assertEqual((route.auth, route.csrf), ("admin", False))
        doc = json.loads(self.call("GET", "/api/gw/users/%s/page" % UID1).body.decode("utf-8"))
        self.assertEqual((doc["ok"], doc["enabled"], doc["path"]), (True, False, None))
        doc = json.loads(self.call("POST", "/api/gw/users/%s/page" % UID1, body={"action": "enable"}).body.decode("utf-8"))
        self.assertTrue(doc["enabled"])
        self.assertRegex(doc["path"], r"^/p/[A-Za-z0-9_-]{43}$")
        first = doc["path"]
        doc = json.loads(self.call("POST", "/api/gw/users/%s/page" % UID1, body={"action": "rotate"}).body.decode("utf-8"))
        self.assertNotEqual(doc["path"], first)
        doc = json.loads(self.call("POST", "/api/gw/users/%s/page" % UID1, body={"action": "disable"}).body.decode("utf-8"))
        self.assertFalse(doc["enabled"])

    def test_errors(self):
        resp = self.call("POST", "/api/gw/users/%s/page" % UID1, body={"action": "explode"})
        self.assertEqual(resp.status, 400)
        resp = self.call("GET", "/api/gw/users/u-ffffffff/page")
        self.assertEqual(resp.status, 404)
        self.assertFalse(json.loads(resp.body.decode("utf-8"))["ok"])


class DetectOsTest(unittest.TestCase):
    def test_user_agents(self):
        cases = {
            UA_IPHONE: "ios",
            "Mozilla/5.0 (iPad; CPU OS 12_5 like Mac OS X) AppleWebKit/605.1.15 Version/12.1 Mobile/15E148 Safari/604.1": "ios",
            UA_ANDROID: "android",
            "Mozilla/5.0 (Linux; Android 9; AFTMM Build/PS7233; wv) AppleWebKit/537.36 Chrome/99 Mobile Safari/537.36": "androidtv",
            "Mozilla/5.0 (Linux; Android 11; BRAVIA 4K UR3) AppleWebKit/537.36 Chrome/99 Safari/537.36": "androidtv",
            "Mozilla/5.0 (Linux; Android 12; SHIELD Android TV) AppleWebKit/537.36 Chrome/99 Safari/537.36": "androidtv",
            "Mozilla/5.0 (Linux; Android 10; Draft X) AppleWebKit/537.36 Chrome/99 Mobile Safari/537.36": "android",
            UA_WINDOWS: "windows",
            UA_MAC: "macos",                                    # an iPad in desktop mode too: the page tells it by touch
            "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0": "linux",
            "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 Chrome/99 Safari/537.36": "linux",
            "curl/8.4.0": "other", "": "other", None: "other",
        }
        for ua, want in cases.items():
            self.assertEqual(userpage.detect_os(ua), want, ua)

    def test_robots(self):
        for ua in ("TelegramBot (like TwitterBot)", "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
                   "WhatsApp/2.23.20.0 A", "Slackbot-LinkExpanding 1.0", "Mozilla/5.0 (compatible; YandexBot/3.0)",
                   "facebookexternalhit/1.1", "Discordbot/2.0"):
            self.assertTrue(userpage.is_bot(ua), ua)
        for ua in (UA_IPHONE, UA_ANDROID, UA_WINDOWS, UA_MAC, "", None):
            self.assertFalse(userpage.is_bot(ua), ua)


class TokenLookupTest(PageCase):
    def test_lookup_compares_every_token_without_an_early_exit(self):
        toks = [self.token(UID1), self.token(UID2)]
        calls = []
        real = userpage.hmac.compare_digest

        def spy(a, b):
            calls.append(a)
            return real(a, b)
        with mock.patch.object(userpage.hmac, "compare_digest", spy):
            self.assertEqual(userpage.find_token(toks[0]), UID1)
        self.assertEqual(len(calls), 2)                                     # both compared, though the first matched

    def test_malformed_tokens_never_reach_the_comparison(self):
        self.token()
        with mock.patch.object(userpage.hmac, "compare_digest", side_effect=AssertionError("compared")):
            for bad in ("", "short", "A" * 42, "A" * 44, "A" * 42 + "é", None, 5, "A" * 42 + "="):
                self.assertIsNone(userpage.find_token(bad))

    def test_a_broken_secrets_file_opens_nothing(self):
        tok = self.token()
        with open(self.secret_path(), "w", encoding="utf-8") as f:
            f.write("{broken")
        snapshot.forget()
        self.assertEqual(self.shell(tok).status, 404)

    def test_other_secrets_are_not_tokens(self):
        sec = secrets.load()
        sec.set("dev.d-00000001.page", "A" * 43)
        sec.set("user.%s.pagex" % UID1, "A" * 43)
        sec.set("user.u-1.page", "A" * 43)
        sec.save()
        snapshot.forget()
        self.assertEqual(self.shell("A" * 43).status, 404)

    def test_concurrent_requests(self):
        tok = self.token()
        out, errs = [], []

        def work():
            try:
                for _ in range(20):
                    out.append(self.shell(tok).status)
                    out.append(self.shell("B" * 43, ip="198.51.100.77").status)
            except Exception as e:                                          # noqa: BLE001
                errs.append(e)
        ts = [threading.Thread(target=work) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        self.assertEqual(out.count(200), 80)


if __name__ == "__main__":
    unittest.main()

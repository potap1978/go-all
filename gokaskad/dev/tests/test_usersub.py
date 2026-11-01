"""The subscription for apps (kaskad.panel.userpage, docs/k3/SPEC-vless-inbound.md §8): ``GET /p/<token>/sub``
— base64 of the vless:// links of the person's active devices, then the amneziawg:// lines of their WireGuard and
AmneziaWG (owner 24.10; the details — tests/test_sub_awg.py); its own token (not the page's), rotate and revoke,
one 404 and one ban with the page, the token never in the log, state.json or argv; the CLI and the admin routes."""

import base64
import contextlib
import io
import json
import os
from unittest import mock

from kaskad import cli, errors, secrets, state
from kaskad.gw import model, ops, registry
from kaskad.panel import routes, userpage
from tests.helpers import lock_held_elsewhere
from tests.userpage_case import HOST, IP, NAME1, NAME2, UID1, UID2, PageCase, RichInbound, _rec, priv

UA_V2RAYTUN = "v2raytun/ios"
UA_HIDDIFY = "HiddifyNext/2.5.7 (android) like ClashMeta v2ray sing-box"


class VlessLike(RichInbound):
    """An adapter that issues like the real VLESS one: a link vless://, a QR of it, fields; and a socks5:// link of
    another kind (SOCKS5 gives socks5://, not the socks:// of v2rayN — it must stay out of the subscription)."""

    def issue(self, st, sec, device, os=None):
        dev = device["id"]
        key = sec.get("dev.%s.%s.private" % (dev, self.kind), "")
        if self.kind == "vless":
            url = "vless://%s@%s:8443?security=reality#Kaskad%%20%s" % (key, HOST, dev)
            items = [_rec("link", "VLESS — ссылка", url), _rec("qr", "VLESS — QR", [url]),
                     _rec("fields", "VLESS — поля", [["UUID", key]])]
        else:
            items = [_rec("link", "SOCKS5 — ссылка", "socks5://u:%s@%s:1080" % (key, HOST)),
                     _rec("link", "SOCKS5 — Telegram", "tg://socks?server=%s&pass=%s" % (HOST, key))]
        return dict(items[0], inbound=self.kind, items=items)


class SubCase(PageCase):
    def setUp(self):
        super().setUp()
        for kind in ("vless", "socks5"):
            ad = VlessLike(kind=kind)
            registry.register(ad)
            self.ads[kind] = ad
        st = state.load()
        g = st["gateway"]
        g["inbounds"]["vless"] = {"enabled": True, "port": 8443}
        g["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        access = {"d-00000001": ["wg", "vless", "socks5"], "d-00000002": ["vless"], "d-00000003": ["wg", "ikev2"],
                  "d-00000004": ["vless"], "d-00000005": ["vless"]}
        for d in g["devices"]:
            d["access"] = access[d["id"]]
        state.save(st)
        sec = secrets.load()
        for d in g["devices"]:
            for kind in d["access"]:
                sec.set("dev.%s.%s.private" % (d["id"], kind), priv(d["id"], kind))
        sec.save()

    def sub_token(self, uid=UID1):
        userpage.sub_enable(uid)
        return userpage._stored_sub_tokens()[uid]

    def sub(self, token, ua=UA_V2RAYTUN, **kw):
        return self.call("GET", "/p/%s/sub" % token, headers={"user-agent": ua}, **kw)

    def links(self, resp):
        self.assertEqual(resp.status, 200, resp.body[:200])
        text = base64.b64decode(resp.body, validate=True).decode("utf-8")
        return text.split("\n") if text else []

    def log(self):
        with open(self.path("/var/log/kaskad.log"), encoding="utf-8") as f:
            return f.read()


class FormatTest(SubCase):
    def test_base64_of_the_vless_links_of_active_devices(self):
        r = self.sub(self.sub_token())
        self.assertEqual(r.headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertRegex(r.body.decode("ascii"), r"^[A-Za-z0-9+/]+={0,2}$")       # standard base64 with padding
        self.assertEqual(len(r.body) % 4, 0)
        links = self.links(r)
        # Телефон and iPhone of Мама: the disabled tablet, the laptop without VLESS and Дача's router are not here
        self.assertEqual(links[:2], ["vless://%s@%s:8443?security=reality#Kaskad%%20%s" % (priv(d, "vless"), HOST, d)
                                     for d in ("d-00000001", "d-00000002")])
        # then WireGuard of Телефон and Ноутбук as amneziawg:// (24.10): the disabled tablet and Дача's router — not
        self.assertEqual([x.split("://")[0] for x in links], ["vless", "vless", "amneziawg", "amneziawg"])
        confs = [base64.urlsafe_b64decode(x[12:].split("#")[0] + "==").decode() for x in links[2:]]
        self.assertEqual([priv(d, "wg") in c for d, c in zip(("d-00000001", "d-00000003"), confs)], [True, True])
        body = base64.b64decode(r.body).decode("utf-8")
        for d in ("d-00000004", "d-00000005"):
            self.assertNotIn(priv(d, "wg"), body + "".join(confs))
        self.assertNotIn("socks5://", body)                                     # SOCKS5: not the socks:// of apps
        self.assertNotIn("tg://", body)
        self.assertNotIn("\n\n", body)
        self.assertFalse(body.endswith("\n"))

    def test_headers_for_the_apps(self):
        r = self.sub(self.sub_token())
        title = r.headers["profile-title"]
        self.assertTrue(title.startswith("base64:"))
        self.assertEqual(base64.b64decode(title[7:]).decode("utf-8"), "goKaskad PRO · %s" % NAME1)
        self.assertEqual(r.headers["profile-update-interval"], "12")
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        self.assertNotIn("Content-Disposition", r.headers)
        self.assertEqual(r.headers["X-Robots-Tag"], "noindex, nofollow")

    def test_other_apps_get_the_same(self):
        tok = self.sub_token()
        self.assertEqual(self.links(self.sub(tok, ua=UA_HIDDIFY)), self.links(self.sub(tok)))

    def test_only_enabled_devices_and_installed_inbounds(self):
        tok = self.sub_token()
        st = state.load()
        model.get_device(st, "d-00000002")["enabled"] = False
        state.save(st)
        vless = lambda: [x for x in self.links(self.sub(tok)) if x.startswith("vless://")]   # noqa: E731
        self.assertEqual(len(vless()), 1)
        st = state.load()
        st["gateway"]["inbounds"]["vless"]["enabled"] = False
        state.save(st)
        self.assertEqual(vless(), [])
        self.assertEqual(len(self.links(self.sub(tok))), 2)                     # WireGuard of Телефон and Ноутбук
        st = state.load()
        st["gateway"]["inbounds"]["wg"]["enabled"] = False
        state.save(st)
        self.assertEqual(self.links(self.sub(tok)), [])

    def test_no_devices_for_the_subscription_is_an_empty_200_and_no_miss(self):
        tok = self.sub_token(UID2)
        st = state.load()
        model.get_device(st, "d-00000005")["access"] = ["ikev2"]           # no VLESS, Hysteria2, WireGuard, AmneziaWG
        state.save(st)
        for _ in range(12):
            r = self.sub(tok)
            self.assertEqual((r.status, r.body), (200, b""))
        self.assertFalse(userpage.GUARDS.banned(IP))
        self.assertEqual(userpage.sub_status(UID2)["devices"], 0)
        self.assertIn("пустая", userpage.sub_status(UID2)["empty_note"])

    def test_a_paused_person_gets_an_empty_list(self):
        tok = self.sub_token()
        st = state.load()
        model.get_user(st, UID1)["enabled"] = False
        state.save(st)
        r = self.sub(tok)
        self.assertEqual((r.status, r.body), (200, b""))
        self.assertIn("приостановлен", userpage.sub_status(UID1)["empty_note"])

    def test_one_broken_device_does_not_fail_the_rest(self):
        tok = self.sub_token()
        sec = secrets.load()
        orig = self.ads["vless"].issue

        def flaky(st, sec_, device, os=None):
            if device["id"] == "d-00000001":
                raise RuntimeError("vless://SECRET-IN-ERROR")
            return orig(st, sec_, device, os)
        self.ads["vless"].issue = flaky
        self.assertEqual(len([x for x in self.links(self.sub(tok)) if x.startswith("vless://")]), 1)
        self.assertNotIn("SECRET-IN-ERROR", self.log())
        self.assertTrue(sec)

    def test_busy_when_a_change_holds_the_lock(self):
        tok = self.sub_token()
        with lock_held_elsewhere():                      # «busy» — another process (T1: not the same thread)
            orig = userpage.LOCK_WAIT
            userpage.LOCK_WAIT = 0.05
            try:
                r = self.sub(tok)
            finally:
                userpage.LOCK_WAIT = orig
        self.assertEqual(r.status, 503)
        self.assertEqual(r.headers["Retry-After"], str(userpage.RETRY_AFTER))

    def test_a_link_preview_robot_gets_no_keys(self):
        r = self.sub(self.sub_token(), ua="TelegramBot (like TwitterBot)")
        self.assertEqual(r.status, 404)
        self.assertNotIn(b"vless", r.body)

    def test_route_is_a_page_route_before_the_catch_all(self):
        r, _ = routes.find("GET", "/p/%s/sub" % ("A" * 43))
        self.assertIs(r.handler, userpage.subscription)
        self.assertEqual(r.auth, "page")
        self.assertEqual(routes.find("POST", "/p/%s/sub" % ("A" * 43))[0].handler, userpage._anything_else)


class TokenTest(SubCase):
    def test_its_own_43_char_token_in_the_secrets_file(self):
        link = userpage.sub_enable(UID1)
        tok = userpage._stored_sub_tokens()[UID1]
        self.assertRegex(tok, r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(len(base64.urlsafe_b64decode(tok + "=")), 32)          # 256 bits
        self.assertEqual(link, "/p/%s/sub" % tok)
        self.assertEqual(secrets.get("user.%s.sub" % UID1), tok)
        self.assertEqual(os.stat(self.secret_path()).st_mode & 0o777, 0o600)
        self.assertEqual(userpage.sub_enable(UID1), link)                        # on again: the same link
        self.assertIsNone(userpage.sub_url(UID2))

    def test_independent_of_the_page(self):
        page = self.token()
        tok = self.sub_token()
        self.assertNotEqual(page, tok)
        self.assertEqual(self.call("GET", "/p/%s/sub" % page).status, 404)     # the page's token opens no subscription
        self.assertEqual(self.shell(tok).status, 404)                            # and the subscription's no page
        self.assertEqual(self.call("POST", "/p/%s/keys" % tok, body={"nonce": "x" * 32}).status, 404)
        userpage.disable(UID1)                                                   # the page off: the subscription lives
        self.assertEqual(self.sub(tok).status, 200)
        userpage.enable(UID1)
        userpage.sub_disable(UID1)                                               # revoked: the page lives
        self.assertEqual(self.shell(userpage._stored_tokens()[UID1]).status, 200)

    def test_rotate_kills_the_old_link(self):
        old = self.sub_token()
        new_link = userpage.sub_rotate(UID1)
        new = userpage._stored_sub_tokens()[UID1]
        self.assertNotEqual(old, new)
        self.assertEqual(new_link, "/p/%s/sub" % new)
        self.assertEqual(self.sub(old).status, 404)
        self.assertEqual(self.sub(new).status, 200)

    def test_revoke(self):
        tok = self.sub_token()
        self.assertTrue(userpage.sub_disable(UID1))
        self.assertFalse(userpage.sub_disable(UID1))
        self.assertNotIn("user.%s.sub" % UID1, secrets.load())
        r = self.sub(tok)
        self.assertEqual(self.sig(r), self.sig(userpage.not_found()))
        self.assertFalse(userpage.sub_status(UID1)["enabled"])

    def test_deleting_the_person_removes_the_token(self):
        tok = self.sub_token(UID2)
        ops.user_del(self.w.runner, UID2, force=True)
        self.assertNotIn("user.%s.sub" % UID2, secrets.load())
        self.assertEqual(self.sub(tok).status, 404)

    def test_stale_tokens_are_pruned(self):
        sec = secrets.load()
        sec.set("user.u-0000dead.sub", userpage.new_token())
        sec.save()
        self.sub_token()
        self.assertNotIn("user.u-0000dead.sub", secrets.load())

    def test_purge_keeping_people_keeps_their_subscription(self):
        tok = self.sub_token()
        keep = []
        orig = secrets.Store.delete

        def spy(self_, key):
            keep.append(key)
            return orig(self_, key)
        with mock.patch.object(secrets.Store, "delete", spy):
            ops.purge(self.w.runner, users=False)
        self.assertIn("dev.d-00000001.vless.private", keep)                     # the keys of the inbounds went
        self.assertNotIn("user.%s.sub" % UID1, keep)
        self.assertEqual(secrets.load().get("user.%s.sub" % UID1), tok)

    def test_the_token_is_not_in_state_log_or_argv(self):
        tok = self.sub_token()
        self.sub(tok)
        userpage.sub_rotate(UID1)
        new = userpage._stored_sub_tokens()[UID1]
        self.sub(new)
        userpage.sub_disable(UID1)
        with open(self.path("/etc/kaskad/state.json"), encoding="utf-8") as f:
            st = f.read()
        log = self.log()
        argv = json.dumps([c[0] if isinstance(c, tuple) else c for c in self.w.runner.calls])
        for t in (tok, new):
            self.assertNotIn(t, st)
            self.assertNotIn(t, log)
            self.assertNotIn(t, argv)
        self.assertIn("Подписка для приложений включена", log)
        self.assertIn("Подписка: отдана", log)
        self.assertIn("отозвана", log)
        self.assertNotIn("PRIVKEY", log)

    def test_public_link(self):
        self.write_access(public=True, port=41234)
        tok = self.sub_token()
        self.assertEqual(userpage.sub_url(UID1), "https://%s:41234/p/%s/sub" % (HOST, tok))
        self.write_access(public=True, port=443, host="vpn.example.com")
        self.assertEqual(userpage.sub_url(UID1), "https://vpn.example.com/p/%s/sub" % tok)
        doc = userpage.sub_status(UID1)
        self.assertEqual((doc["public"], doc["note"], doc["devices"]), (True, "", 3))    # + Ноутбук with WireGuard


class BanTest(SubCase):
    def test_unknown_token_is_the_404_and_a_miss_of_the_one_ban(self):
        tok = self.sub_token()
        page = self.token()
        for i in range(9):
            r = self.sub(("%043d" % i).replace("0", "s"))
            self.assertEqual(self.sig(r), self.sig(userpage.not_found()))
        self.assertEqual(self.sub(tok).status, 200)
        self.assertEqual(self.shell(("%043d" % 1).replace("0", "q")).status, 404)    # a miss of the page: the 10th
        self.assertTrue(userpage.GUARDS.banned(IP))
        self.assertEqual(self.sub(tok).status, 404)                                  # banned: the right one too
        self.assertEqual(self.shell(page).status, 404)
        self.assertEqual(self.sub(tok, ip="198.51.100.99").status, 200)

    def test_the_server_counts_a_404_of_the_subscription(self):
        from kaskad.panel import server
        self.assertTrue(server.PAGE_PATH.match("/p/%s/sub" % ("A" * 43)))        # the journal: «/p/…», no token


class AdminTest(SubCase):
    def admin(self, method, uid, body=None):
        return self.call(method, "/api/gw/users/%s/sub" % uid, body=body)

    def test_routes(self):
        r = self.admin("GET", UID1)
        doc = json.loads(r.body)
        self.assertEqual((doc["ok"], doc["enabled"], doc["url"], doc["path"]), (True, False, None, None))
        doc = json.loads(self.admin("POST", UID1, {"action": "enable"}).body)
        self.assertRegex(doc["path"], r"^/p/[A-Za-z0-9_-]{43}/sub$")
        self.assertEqual((doc["enabled"], doc["devices"], doc["interval"]), (True, 3, 12))   # + Ноутбук (WireGuard)
        self.assertIn("Доступ из интернета", doc["note"])
        doc2 = json.loads(self.admin("POST", UID1, {"action": "rotate"}).body)
        self.assertNotEqual(doc2["path"], doc["path"])
        doc3 = json.loads(self.admin("POST", UID1, {"action": "disable"}).body)
        self.assertFalse(doc3["enabled"])
        self.assertEqual(self.admin("POST", UID1, {"action": "nope"}).status, 400)
        self.assertEqual(self.admin("GET", "u-0000dead").status, 404)
        for method in ("GET", "POST"):
            r, _ = routes.find(method, "/api/gw/users/%s/sub" % UID1)
            self.assertEqual(r.auth, "admin")

    def test_the_apps_of_the_subscription(self):
        """О5 (R-it1-fix): GET и POST отдают ещё apps — приложения подписки по платформам каталога (install_apps.sub_blocks);
        прежние поля те же. Порядок платформ JSON не держит (ключи сортируются) — панель ставит их по своему списку."""
        from kaskad.gw import install_apps
        ids = lambda apps: {p: [a["id"] for a in b["apps"]] for p, b in apps.items()}
        want = ids(install_apps.sub_blocks())
        for doc in (json.loads(self.admin("GET", UID1).body), json.loads(self.admin("POST", UID1, {"action": "enable"}).body)):
            self.assertEqual(ids(doc["apps"]), want)
            self.assertTrue(doc["apps"]["ios"]["apps"][0]["recommended"])
            self.assertTrue({"user", "enabled", "path", "url", "public", "note", "devices", "empty_note", "interval", "qr",
                             "qr_note"} <= set(doc))


class CliTest(SubCase):
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

    def test_on_url_rotate_off_list(self):
        rc, out, err = self.run_cli("page", "sub", "on", NAME2)
        self.assertEqual(rc, 0, err)
        tok = userpage._stored_sub_tokens()[UID2]
        self.assertIn("Подписка для «Дача» включена.", out)
        self.assertIn("Ссылка: /p/%s/sub" % tok, out)
        self.assertIn("v2RayTun", out)
        rc, out, _ = self.run_cli("page", "sub", "url", UID2)
        self.assertIn("/p/%s/sub" % tok, out)
        rc, out, _ = self.run_cli("page", "sub", "list")
        self.assertIn(NAME2, out)
        self.assertNotIn(tok, out)                                              # no links in the list
        rc, out, _ = self.run_cli("page", "sub", "rotate", NAME2)
        new = userpage._stored_sub_tokens()[UID2]
        self.assertNotEqual(tok, new)
        self.assertIn("/p/%s/sub" % new, out)
        rc, out, _ = self.run_cli("page", "sub", "off", NAME2)
        self.assertEqual(rc, 0)
        self.assertIn("отозвана", out)
        rc, out, _ = self.run_cli("page", "sub", "off", NAME2)
        self.assertIn("не была включена", out)
        rc, out, err = self.run_cli("page", "sub", "url", UID2)
        self.assertEqual(rc, errors.ERROR)
        self.assertIn("подписка не включена", err)
        rc, out, _ = self.run_cli("page", "sub", "list")
        self.assertIn("Подписок нет", out)

    def test_json(self):
        rc, doc, _ = self.run_json("page", "sub", "on", UID1)
        self.assertEqual(rc, 0)
        self.assertTrue(doc["ok"] and doc["enabled"])
        self.assertEqual(doc["devices"], 3)                                     # + Ноутбук (WireGuard)
        rc, doc2, _ = self.run_json("page", "sub", "list")
        self.assertEqual(doc2, {"ok": True, "subs": [{"user": UID1, "name": NAME1, "enabled": True, "devices": 3}]})
        rc, doc3, _ = self.run_json("page", "sub", "off", UID1)
        self.assertEqual((doc3["enabled"], doc3["was_enabled"]), (False, True))
        rc, out, err = self.run_cli("page", "sub")
        self.assertEqual(rc, errors.USAGE)

    def test_json_has_no_apps(self):
        """Д-1 (REV-it1-b): каталог приложений подписки — только в ответе панели; CLI ``page sub … --json``
        (через него и MCP ``page_sub_*``) его не несёт."""
        for verb in ("on", "url", "rotate"):
            rc, doc, _ = self.run_json("page", "sub", verb, UID1)
            self.assertEqual(rc, 0, verb)
            self.assertNotIn("apps", doc, verb)
        rc, doc, _ = self.run_json("page", "sub", "off", UID1)
        self.assertNotIn("apps", doc)
        self.assertNotIn("apps", userpage.sub_status(UID1))


class PeopleMarkTest(SubCase):
    def test_the_list_of_people_marks_a_subscription(self):
        from kaskad.panel import api
        self.assertEqual(api._subs(), {})
        self.sub_token(UID2)
        self.assertEqual(api._subs(), {UID2: True})
        self.assertEqual(userpage.sub_users(), {UID2: True})

"""VLESS and the site of the node on one TCP/443 (docs/k3/SPEC-vless-inbound.md §4.4, §5; kaskad/site/front.py): the
domain with its site — VLESS + TLS on 443 with the certificate of the domain, everything else handed to nginx behind
xray (a Unix socket with the PROXY protocol); only an IP — Reality by the country of the server. The switches of the
mode (``site mode site|domain``, the domain cleared, ``gw vless sync``), what the devices are told, the renewal of the
certificate without a restart, nginx following xray (stop, disable, uninstall), a refusal of nginx undone."""

import json
import os
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from kaskad import paths, secrets, state, util
from kaskad.gw import model, xray
from kaskad.gw.inbounds import vless as vin
from kaskad.panel import conf
from kaskad.site import consumers, front, nginx, templates
from kaskad.site import ops as site_ops
from tests.fakes_nft import save_state
from tests.site.sitecase import DOMAIN, FIX, SiteFakes
from tests.test_gw_vless import VlessCase


class FakeNginx(object):
    """``nginx -v | -t | -T`` over the files under KASKAD_ROOT: ``-T`` prints our vhost as nginx would (its path and
    text); ``refuse`` — a text in a vhost that makes ``-t`` fail (an nginx without the realip module …)."""

    def __init__(self):
        self.refuse = None
        self.tests = 0

    def __call__(self, argv, inp):
        a = argv[1:]
        if a == ["-v"]:
            return 0, "", "nginx version: nginx/1.24.0\n"
        if a == ["-t"]:
            self.tests += 1
            text = util.read_text(nginx.vhost_path()) or ""
            if self.refuse and self.refuse in text:
                return 1, "", "nginx: [emerg] unknown directive \"set_real_ip_from\" in %s:9\n" % nginx.VHOST
            return 0, "", "nginx: configuration file /etc/nginx/nginx.conf test is successful\n"
        if a == ["-T"]:
            out = ""
            for path, name in ((nginx.vhost_path(), nginx.ENABLED), (nginx.acme_vhost_path(), nginx.ACME_ENABLED)):
                text = util.read_text(path)
                if text:
                    out += "# configuration file %s:\n%s\n" % (name, text)
            return 0, out, ""
        return 1, "", "fake nginx: %r\n" % (a,)


class FrontCase(SiteFakes, VlessCase):
    def setUp(self):
        super().setUp()
        self.site_fakes()
        self.nginx = FakeNginx()
        self.w.runner.binaries.add("nginx")
        self.w.runner.on(["nginx"], self.nginx)
        self.w.sysd.units["nginx"] = {"enabled": True, "active": True, "packaged": True}
        deployed = {"ok": True, "id": "demo", "name": "Демо", "source": "repo", "fallback": False}
        from kaskad.setup import steps
        # the network steps of the wizard (site, cert, dns) ask steps.online() — a real TCP to 1.1.1.1/8.8.8.8: under
        # load or without the network the step said «offline» and the tests of 3.3.x failed (REV-speed-4)
        for p in (mock.patch.object(templates, "deploy", return_value=deployed),
                  mock.patch.object(consumers, "_reload_panel", return_value=False),
                  mock.patch.object(steps, "online", lambda force=False: True)):
            p.start()
            self.addCleanup(p.stop)
        save_state([])
        self.ok("gw", "enable")
        self.ok("gw", "inbound", "install", "vless")
        self.ok("gw", "user", "add", "Мама")
        self.dev = self.add_device("Телефон", "android")
        self.with_domain(issued="le")

    def vless_ib(self):
        return [i for i in self.cfg()["inbounds"] if i["tag"] == "gw-vless"][0]

    def site_next_to_reality(self):
        """The site on while VLESS stays on Reality next to it (8443) — no TLS tried (SPEC-vless-tls-default Ш2: a
        refused TLS no longer leaves the site on, the snapshot comes back): for a moment a choice of Reality steps
        aside, then the record gets its ``want`` and consent back."""
        from kaskad import state
        from kaskad.gw import model as gm
        st = state.load()
        rec = gm.inbound(st, "vless")
        was = dict((k, rec.get(k)) for k in ("want", "reality_consent"))
        rec.update(want="reality", reality_consent={"at": "2026-10-20T10:00:00Z", "via": "panel", "risks": "r1a"})
        state.save(st)
        site_ops.mode(self.w.runner, "site", yes=True)
        st = state.load()
        rec = gm.inbound(st, "vless")
        for k, v in was.items():
            if v is None:
                rec.pop(k, None)
            else:
                rec[k] = v
        state.save(st)
        self.assertEqual((self.rec()["security"], self.rec()["port"]), ("reality", 8443))

    def vhost(self):
        return util.read_text(nginx.vhost_path()) or ""

    def link(self, dev_id):
        rc, doc, err = self.run_json("gw", "issue", dev_id, "--inbound", "vless", "--os", "windows")
        self.assertEqual(rc, 0, err)
        body = [i for i in doc["items"] if i["format"] == "link"][0]["body"]
        u = urlsplit(body)
        return u, dict((k, v[0]) for k, v in parse_qs(u.query).items()), doc

    def xray_restarts(self):
        return self.w.sysd.restarts.get(xray.UNIT, 0)


class SiteModeSwitchesVlessTest(FrontCase):
    def test_the_site_puts_vless_in_front_with_tls_and_back(self):
        rec = self.rec()
        self.assertEqual((rec["port"], rec["security"]), (443, "reality"))      # only an IP: Reality on 443
        uid = secrets.load()["dev.%s.vless.uuid" % self.dev["id"]]

        got = site_ops.mode(self.w.runner, "site", yes=True)                              # the site: VLESS in front of it
        self.assertEqual(got["site"]["mode"], "site")
        v = got["vless"]
        self.assertEqual((v["changed"], v["security"], v["old"], v["port"]), (True, "tls", "reality", 443))
        self.assertEqual([r["id"] for r in v["reissue"]], [self.dev["id"]])
        self.assertIn("TLS со своим доменом %s" % DOMAIN, v["text"])
        self.assertIn("Телефон (Мама)", v["text"])
        rec = self.rec()
        self.assertEqual((rec["security"], rec["port"]), ("tls", 443))
        # xray: VLESS + TLS on 443, the domain's certificate, unknown SNI refused, the rest to the site's socket
        ib = self.vless_ib()
        self.assertEqual((ib["port"], ib["listen"]), (443, "0.0.0.0"))
        ss = ib["streamSettings"]
        self.assertEqual(ss["security"], "tls")
        tls = ss["tlsSettings"]
        self.assertEqual((tls["serverName"], tls["rejectUnknownSni"], tls["alpn"]), (DOMAIN, True, ["http/1.1"]))
        chain, key = vin.tls_files()
        # no ocspStapling (T-M6-qs330b №14): Let's Encrypt has no OCSP — xray warned at every start; the hourly hot
        # reload of the files stays (xray's default)
        self.assertEqual(tls["certificates"], [{"certificateFile": chain, "keyFile": key}])
        self.assertEqual(ib["settings"]["fallbacks"], [{"dest": front.sock_path(), "xver": 1}])
        self.assertEqual(ib["settings"]["clients"][0]["flow"], "xtls-rprx-vision")
        self.assertNotIn("realitySettings", ss)
        with open(key, "rb") as f, open(os.path.join(paths.P("/etc/kaskad/site/tls/current"), "privkey.pem"),
                                         "rb") as g:
            self.assertEqual(f.read(), g.read())
        self.assertEqual(os.stat(key).st_mode & 0o777, 0o640)
        self.assertEqual(os.stat(vin.tls_dir()).st_mode & 0o777, 0o750)
        # nginx: behind xray — the socket with the PROXY protocol, no TCP 443, no TLS of its own
        text = self.vhost()
        self.assertIn("listen unix:%s proxy_protocol;" % front.sock_path(), text)
        self.assertIn("real_ip_header proxy_protocol;", text)
        self.assertIn("set_real_ip_from unix:;", text)
        self.assertIn("absolute_redirect off;", text)
        self.assertNotIn("listen 443", text)
        self.assertNotIn("ssl_certificate", text)
        self.assertNotIn("ssl_reject_handshake", text)
        self.assertIn("location ^~ /p/", text)                                  # the pages of people and the panel
        self.assertIn("/api/backup/import {", text)                              # the copy of 20 MB behind xray too
        self.assertNotEqual(conf.load()["public"]["port"], 443)                  # the open mode never takes 443
        self.assertIn("listen 80;", text)                                        # 80 stays nginx's
        self.assertTrue(front.nginx_behind() and front.xray_fronts())
        self.assertEqual(os.stat(front.sock_dir()).st_mode & 0o777, 0o750)
        self.assertIn("d /run/kaskad-vless 0750 root kaskad-xray -", util.read_text(front.tmpfiles_path()))
        # the link: TLS, the domain, no Reality keys
        u, q, doc = self.link(self.dev["id"])
        self.assertEqual((u.username, u.hostname, u.port), (uid, DOMAIN, 443))
        self.assertEqual((q["security"], q["sni"], q["alpn"], q["flow"], q["fp"]),
                         ("tls", DOMAIN, "http/1.1", "xtls-rprx-vision", "chrome"))
        self.assertNotIn("pbk", q)
        self.assertNotIn("sid", q)
        self.assertIn("VLESS TLS", doc["items"][0]["title"])
        show = self.run_json("gw", "vless", "show")[1]
        self.assertEqual((show["security"], show["domain"]), ("tls", DOMAIN))

        # a new device in TLS mode: live, no restart
        restarts = self.xray_restarts()
        d2 = self.add_device("Ноутбук", "windows")
        self.assertEqual(self.xray_restarts(), restarts)
        self.assertIn(d2["login"], [c["email"] for c in self.vless_ib()["settings"]["clients"]])

        # the renewal of the certificate: a new copy for xray, which re-reads it itself — no restart
        self.put_live("le2")
        consumers.cert_installed(self.w.runner)
        with open(chain) as f:
            self.assertEqual(f.read(), open(os.path.join(FIX, "le2.pem")).read())
        self.assertEqual(self.xray_restarts(), restarts)

        # the site off: Reality again, on 443 (nginx left it), the cover by the country; nginx no longer behind
        got = site_ops.mode(self.w.runner, "domain", yes=True)
        v = got["vless"]
        self.assertEqual((v["security"], v["old"], v["port"]), ("reality", "tls", 443))
        self.assertEqual(sorted(r["id"] for r in v["reissue"]), sorted([self.dev["id"], d2["id"]]))
        rec = self.rec()
        self.assertEqual((rec["security"], rec["sni"], rec["mask"]), ("reality", "www.microsoft.com", "list"))
        ss = self.vless_ib()["streamSettings"]
        self.assertEqual((ss["security"], ss["realitySettings"]["dest"]), ("reality", "www.microsoft.com:443"))
        self.assertFalse(os.path.exists(vin.tls_dir()))
        self.assertFalse(front.xray_fronts())
        u, q, _ = self.link(self.dev["id"])
        self.assertEqual((q["security"], q["sni"], u.port), ("reality", "www.microsoft.com", 443))

    def test_the_subscription_follows_the_mode(self):
        from kaskad.panel import userpage
        user = model.users(state.load())[0]
        _, links = userpage.build_sub(user, runner=self.w.runner)
        self.assertEqual([parse_qs(urlsplit(x).query)["security"][0] for x in links], ["reality"])
        site_ops.mode(self.w.runner, "site", yes=True)
        _, links = userpage.build_sub(user, runner=self.w.runner)
        u = urlsplit(links[0])
        self.assertEqual((u.hostname, u.port, parse_qs(u.query)["security"][0]), (DOMAIN, 443, "tls"))

    def test_the_domain_cleared_brings_reality_back(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(self.rec()["security"], "tls")
        got = site_ops.clear(self.w.runner, yes=True)
        self.assertTrue(got["ok"])
        rec = self.rec()
        self.assertEqual((rec["security"], rec["port"], rec["mask"]), ("reality", 443, "list"))
        self.assertNotIn("listen unix:", self.vhost())
        self.assertFalse(front.xray_fronts())

    def test_gateway_off_and_vless_removed_give_443_back_to_nginx(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertIn("listen unix:", self.vhost())
        self.ok("gw", "disable")                                                # xray stops: nginx back on 443
        self.assertNotIn("listen unix:", self.vhost())
        self.assertIn("listen 443 ssl", self.vhost())
        self.ok("gw", "enable")                                                 # and behind it again
        self.assertIn("listen unix:", self.vhost())
        self.assertTrue(front.xray_fronts())
        self.ok("gw", "inbound", "disable", "vless")
        self.assertIn("listen 443 ssl", self.vhost())
        self.ok("gw", "inbound", "enable", "vless")
        self.assertIn("listen unix:", self.vhost())
        self.ok("gw", "inbound", "uninstall", "vless")
        self.assertIn("listen 443 ssl", self.vhost())
        self.assertFalse(os.path.exists(vin.tls_dir()))
        rc, doc, err = self.run_json("gw", "inbound", "install", "vless")      # back: in front of the site at once
        self.assertEqual(rc, 0, err)
        self.assertEqual((self.rec()["security"], self.rec()["port"]), ("tls", 443))
        self.assertIn("listen unix:", self.vhost())

    def test_nginx_refusing_the_socket_leaves_vless_on_reality_next_to_the_site(self):
        """Ш2 (SPEC-vless-tls-default §3.5 п.6, З-2): the site on would switch VLESS to TLS, nginx refuses the socket —
        the transition is undone by the snapshot: the site off again, VLESS on 443 as it was (the link byte for byte)."""
        from kaskad.errors import KaskadError
        before = self.link(self.dev["id"])
        self.nginx.refuse = "set_real_ip_from"
        with self.assertRaises(KaskadError) as cm:
            site_ops.mode(self.w.runner, "site", yes=True)
        v = cm.exception.data["vless"]
        self.assertEqual((v["ok"], v["restored"], cm.exception.data["reason"]), (False, True, "vless_tls"))
        self.assertIn("Ничего не изменилось", str(cm.exception))
        rec = self.rec()
        self.assertEqual((rec["security"], rec["port"], rec["sni"]), ("reality", 443, "www.microsoft.com"))
        self.assertNotIn("mode_pending", rec)
        self.assertEqual(self.link(self.dev["id"]), before)
        from kaskad.site import model as smodel
        from kaskad import state as kstate
        self.assertEqual(smodel.peek(kstate.load())["mode"], "domain")
        self.assertFalse(front.xray_fronts())
        self.assertEqual(self.vless_ib()["port"], 443)

    def test_in_tls_mode_the_port_and_the_cover_site_are_refused(self):
        from kaskad import errors
        site_ops.mode(self.w.runner, "site", yes=True)
        rc, doc, _ = self.run_json("gw", "inbound", "port", "vless", "8443")
        self.assertEqual(rc, errors.CONFLICT)
        self.assertIn("стоит на 443 перед сайтом", doc["error"])
        rc, doc, _ = self.run_json("gw", "vless", "mask", "dl.google.com")
        self.assertEqual(rc, errors.USAGE)
        self.assertIn("режиме TLS", doc["error"])
        self.assertEqual((self.rec()["port"], self.rec()["security"]), (443, "tls"))
        self.clock += 200 * 86400                                               # the certificate ran out: the layout
        self.ok("gw", "apply")                                                  # of 443 stays (no flip by itself)
        self.assertTrue(front.xray_fronts())
        self.assertIn("listen unix:", self.vhost())

    def test_a_stopped_xray_never_keeps_the_site_behind_it(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.ok("gw", "disable")                                                # xray.json still says TLS on 443
        self.assertIn("listen 443 ssl", self.vhost())
        self.assertFalse(front.xray_fronts(self.w.runner))
        self.ok("gw", "vless", "sync")
        self.assertIn("listen 443 ssl", self.vhost())
        site_ops.rotate_panel_path(self.w.runner)                               # a rewrite of the vhost: still in front
        self.assertIn("listen 443 ssl", self.vhost())
        self.assertNotIn("listen unix:", self.vhost())

    def test_vless_moved_to_443_next_to_the_site_goes_tls_in_front(self):
        """A record of 3.3.7 (no ``want``; since Ш3 a new install writes ``want`` "tls" — its twin in
        test_vless_want: the port refused with the hint «gw vless mode tls»)."""
        from kaskad import state
        from kaskad.gw import model as gm
        st = state.load()
        gm.inbound(st, "vless").pop("want", None)
        state.save(st)
        self.site_next_to_reality()                                             # VLESS stepped aside to 8443
        self.nginx.refuse = "set_real_ip_from"
        from kaskad import errors
        rc, doc, _ = self.run_json("gw", "inbound", "port", "vless", "443")     # TLS not possible: refused, as is
        self.assertEqual(rc, errors.CONFLICT)
        self.assertEqual(self.rec()["port"], 8443)
        self.nginx.refuse = None
        rc, doc, err = self.run_json("gw", "inbound", "port", "vless", "443")
        self.assertEqual(rc, 0, err)
        self.assertEqual((self.rec()["port"], self.rec()["security"]), (443, "tls"))
        self.assertIn("listen unix:", self.vhost())

    def test_a_renewal_with_the_same_key_restarts_xray_once(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        restarts = self.xray_restarts()
        chain, key = vin.tls_files()
        with open(os.path.join(FIX, "le2.pem")) as f:                            # a new chain, the key as it was
            new_chain = f.read()
        cur = os.path.realpath(model_path("fullchain"))
        os.chmod(cur, 0o644)
        with open(cur, "w") as f:
            f.write(new_chain)
        got = consumers.reload(self.w.runner, state.load())
        self.assertIn({"name": "vless", "action": "restart"}, got)
        self.assertEqual(self.xray_restarts(), restarts + 1)
        self.assertEqual(open(chain).read(), new_chain)

    def test_ipv4_inside_ipv6_is_blocked_for_clients(self):
        """[::ffff:127.0.0.1]:10854 — the API of xray: xray unmaps an IPv4-mapped destination before routing, so
        127.0.0.0/8 catches it. ``::ffff:0:0/96`` itself must NOT be in the rule (T-M6-qs330b №1): xray refuses
        the whole config («invalid network mask for router: 96») — 443 never listened on a clean node."""
        priv = [r for r in self.cfg()["routing"]["rules"] if "ip" in r and r.get("outboundTag") == "block"][0]
        self.assertIn("127.0.0.0/8", priv["ip"])
        self.assertFalse([c for c in priv["ip"] if c.lower().startswith("::ffff:")], priv["ip"])
        self.assertEqual(xray.blocked_ips(["::ffff:203.0.113.7/128", "203.0.113.7/32"])[-1], "203.0.113.7/32")

    def test_sync_reports_why_not_tls(self):
        # T-3378 Н-17: the site never on under this domain, a device — «TLS уже можно» (the site goes on with TLS); a
        # record of 3.3.7 (no ``want``) follows the site as before
        legacy = self.rec().get("want") is None
        rc, doc, err = self.run_json("gw", "vless", "sync")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["changed"], doc["security"]), (False, "reality"))
        self.assertIn("сайт на домене не включён" if legacy else "уже можно", doc["why"])
        self.assertEqual("tls_block" in self.run_json("gw", "vless", "show")[1], legacy)
        # the site switched off by hand (§3.6): why not TLS — the site is off
        site_ops.mode(self.w.runner, "site", yes=True)
        site_ops.mode(self.w.runner, "domain", yes=True)
        rc, doc, err = self.run_json("gw", "vless", "sync")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["changed"], doc["security"]), (False, "reality"))
        self.assertIn("сайт на домене не включён", doc["why"])
        show = self.run_json("gw", "vless", "show")[1]
        self.assertIn("сайт на домене не включён", show["tls_block"])


def model_path(name):
    from kaskad.site import model as site_model
    return site_model.fullchain_path() if name == "fullchain" else site_model.privkey_path()


class SchemeTest(FrontCase):
    def test_the_consequences_say_vless_reconnects_when_a_device_leaves(self):
        import copy
        from kaskad.panel import scheme
        st = state.load()
        new = copy.deepcopy(st)
        [d for d in new["gateway"]["devices"] if d["id"] == self.dev["id"]][0]["enabled"] = False
        got = scheme.disruptions(st, new, None, ready=False)
        self.assertEqual([x["what"] for x in got], ["xray"])
        self.assertIn("VLESS переподключится у всех на пару секунд", got[0]["text"])
        new2 = copy.deepcopy(st)
        new2["gateway"]["devices"][0]["name"] = "Другое имя"                    # nothing leaves: nothing said
        self.assertEqual(scheme.disruptions(st, new2, None, ready=False), [])


class CountryTest(FrontCase):
    def test_a_russian_server_hides_behind_a_russian_site(self):
        self.ok("gw", "inbound", "uninstall", "vless")
        st = state.load()
        st["gateway"]["removed_inbounds"] = {}
        state.save(st)
        self.country = {"code": "RU", "source": "ipinfo"}
        rc, doc, err = self.run_json("gw", "inbound", "install", "vless")
        self.assertEqual(rc, 0, err)
        rec = self.rec()
        self.assertEqual((rec["sni"], rec["mask_list"], rec["country"]["code"]), ("ya.ru", "ru", "RU"))
        self.assertTrue(any("российские сайты" in n for n in doc["notes"]), doc["notes"])
        rc, doc, _ = self.run_json("gw", "vless", "masks")
        self.assertEqual((doc["list"], doc["country"]["code"], doc["masks"][0]["sni"]), ("ru", "RU", "ya.ru"))


class HotPlanTest(FrontCase):
    """``hot_plan``: only users and their chains differ — the API; anything else — a restart."""

    def test_what_goes_live_and_what_restarts(self):
        old = self.cfg()
        new = json.loads(json.dumps(old))
        ib = [i for i in new["inbounds"] if i["tag"] == "gw-vless"][0]
        ib["settings"]["clients"].append({"id": "11111111-2222-4333-8444-555555555555", "email": "x-1",
                                          "flow": "xtls-rprx-vision"})
        new["routing"]["rules"].append({"type": "field", "inboundTag": ["gw-vless"], "user": ["x-1"],
                                        "outboundTag": "direct", "ruleTag": "vl-x-1-abc-0"})
        plan = xray.hot_plan(old, new, ["gw-vless"])
        self.assertEqual((len(plan["add_users"]), len(plan["add_rules"]), plan["rm_users"], plan["rm_rules"]),
                         (1, 1, [], []))
        self.assertIsNone(xray.hot_plan(new, old, ["gw-vless"]))               # a user leaves: a restart cuts it
        moved = json.loads(json.dumps(new))                                    # its route changes: live
        moved["routing"]["rules"][-1] = dict(moved["routing"]["rules"][-1], outboundTag="direct", ruleTag="vl-x-1-def-0")
        plan = xray.hot_plan(new, moved, ["gw-vless"])
        self.assertEqual((plan["rm_rules"], [r["ruleTag"] for r in plan["add_rules"]]), (["vl-x-1-abc-0"], ["vl-x-1-def-0"]))
        blocked = json.loads(json.dumps(new))                                  # …to «block»: a restart
        blocked["routing"]["rules"][-1] = dict(blocked["routing"]["rules"][-1], outboundTag="block", ruleTag="vl-x-1-blk-0")
        self.assertIsNone(xray.hot_plan(new, blocked, ["gw-vless"]))
        other = json.loads(json.dumps(new))
        other["outbounds"].append({"tag": "x", "protocol": "freedom"})
        self.assertIsNone(xray.hot_plan(old, other, ["gw-vless"]))             # anything else: a restart
        no_api = json.loads(json.dumps(old))
        no_api["api"]["services"] = ["StatsService"]
        self.assertIsNone(xray.hot_plan(no_api, new, ["gw-vless"]))             # an xray without the services
        self.assertIsNone(xray.hot_plan(old, old, ["gw-vless"]))
        self.assertIsNone(xray.hot_plan(None, new, ["gw-vless"]))

    def test_the_api_listens_on_the_loopback_only_and_clients_cannot_reach_it(self):
        cfg = self.cfg()
        st_in = [i for i in cfg["inbounds"] if i["tag"] == "stats-in"][0]
        self.assertEqual((st_in["listen"], st_in["port"]), ("127.0.0.1", xray.STATS_PORT))
        self.assertEqual(cfg["routing"]["rules"][0], {"type": "field", "inboundTag": ["stats-in"],
                                                      "outboundTag": "gw-stats"})
        priv = [r for r in cfg["routing"]["rules"] if "ip" in r and r.get("outboundTag") == "block"][0]
        self.assertIn("gw-vless", priv["inboundTag"])
        self.assertIn("127.0.0.0/8", priv["ip"])                                 # a client to 127.0.0.1: blocked
        self.assertEqual(cfg["outbounds"][0]["tag"], "block")
        self.assertNotIn(xray.STATS_PORT, [i["port"] for i in cfg["inbounds"] if i.get("listen") != "127.0.0.1"])
        self.assertEqual(set(cfg["api"]["services"]), {"StatsService", "HandlerService", "RoutingService"})


class BackupTest(FrontCase):
    """A copy of a node with VLESS + TLS on its domain restored on a node where that site is not (yet): Reality."""

    def test_a_tls_record_from_another_node_comes_back_as_reality(self):
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(self.rec()["security"], "tls")
        rec = dict(self.rec())
        self.ok("gw", "inbound", "uninstall", "vless")
        site_ops.mode(self.w.runner, "domain", yes=True)
        site_ops.clear(self.w.runner, yes=True)                                           # the new node: no domain yet
        st = state.load()
        model.keep_inbound(st, "vless", rec)
        state.save(st)
        rc, doc, err = self.run_json("gw", "inbound", "install", "vless")
        self.assertEqual(rc, 0, err)
        now = self.rec()
        self.assertEqual((now["security"], now["port"], now["mask"]), ("reality", 443, "list"))
        prev = doc["previous"]
        self.assertFalse(prev["restored"])
        self.assertIn("режим VLESS (Reality / TLS)", prev["changed"])
        self.assertIn("Телефон (Мама)", prev["text"])
        self.assertTrue(any("VLESS — Reality" in n for n in doc["notes"]), doc["notes"])


class BackupFlowTest(FrontCase):
    """``backup export`` on a node with VLESS + TLS on its domain, ``backup import`` on a fresh node (its site not on yet):
    VLESS comes back on Reality by this node, the devices are told to take the links again, the note says TLS comes
    back with the site."""

    def test_a_copy_with_tls_lands_on_reality_and_says_so(self):
        from kaskad import backup
        from kaskad.gw import ops as gops
        from tests.test_backup import PW, FakeOpenssl
        self.w.runner.binaries.add("openssl")
        self.w.runner.on(["openssl"], FakeOpenssl(self.w.runner))
        for p in (mock.patch.object(backup, "ITER", 100000),
                  mock.patch.object(backup, "_here_ip", lambda runner: state.load()["gateway"].get("host") or "")):
            p.start()
            self.addCleanup(p.stop)
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(self.rec()["security"], "tls")
        text = backup.export(self.w.runner, PW)["text"]
        # a fresh node: no gateway, no domain
        site_ops.mode(self.w.runner, "domain", yes=True)
        site_ops.clear(self.w.runner, yes=True)
        gops.purge(self.w.runner, users=True)
        save_state([])
        doc = backup.unseal(self.w.runner, text, PW)
        res = backup.apply(self.w.runner, doc)
        rec = self.rec()
        self.assertEqual((rec["security"], rec["port"], rec["mask"]), ("reality", 443, "list"))
        self.assertTrue(any("VLESS: режим VLESS (Reality / TLS)" in w for w in res["warnings"]),
                        res["warnings"])
        self.assertTrue(any("VLESS на старом сервере работал в режиме TLS на домене %s" % DOMAIN in n
                            for n in res["notes"]), res["notes"])
        self.assertFalse(front.xray_fronts())
        u, q, _ = self.link(self.dev["id"])
        self.assertEqual((q["security"], u.port), ("reality", 443))


class WizardTest(FrontCase):
    """The wizard «Быстрый старт» goes through the same functions: its step «site» switches VLESS to TLS as ``site mode
    site`` does, its step «issue» gives the link of the mode."""

    def test_the_site_step_of_the_wizard_puts_vless_on_tls(self):
        from kaskad.setup import model as smodel, steps
        smodel.update(lambda d: d.update(password_set=True))
        # the device has a link of VLESS: the price first (REV-vless-sh3 заметно 4), the step again with ``yes``
        out = steps.run("site", {"template": "random"}, self.w.runner)
        self.assertEqual((out["state"], out["data"].get("need")), ("error", "vless_change"), out)
        self.assertNotEqual(self.rec()["security"], "tls")
        out = steps.run("site", {"template": "random", "yes": True}, self.w.runner)
        self.assertEqual(out["state"], "ok", out)
        self.assertEqual((out["data"]["vless"]["security"], out["data"]["vless"]["changed"]), ("tls", True))
        self.assertEqual(self.rec()["security"], "tls")
        got = steps.run("issue", {"device": self.dev["id"], "inbound": "vless", "os": "windows"}, self.w.runner)
        body = [i for i in got["data"]["items"] if i["format"] == "link"][0]["body"]
        u = urlsplit(body)
        self.assertEqual((u.hostname, u.port, parse_qs(u.query)["security"][0]), (DOMAIN, 443, "tls"))

    def test_the_protocol_card_of_the_wizard(self):
        from kaskad.setup import product
        why = product.protocol_card("vless")["why"]
        # one short line «для чего» (3.3.2 №16): TLS with the domain or Reality — said in the video of the stage
        self.assertIn("v2RayTun", why)
        self.assertLessEqual(len(why), 60)
        self.assertFalse(product.protocol_card("vless")["on"])

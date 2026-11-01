"""Hysteria2 inbound with the real core (docs/k3/SPEC-stage1-hy2-inbound.md) through ``kaskadctl gw ...``: the port
(443/udp -> 8443/udp), the gateway xray (the inbound, users «<login>.h2», the hot add through gRPC, Salamander, the
self-signed certificate and its pin, the domain's own copy), port hopping (one UFW rule, the neighbours both ways,
one ``ss``), issuance (the link, QR, the subscription), the secrets nowhere but in issuance, removal without a trace."""

import json
import os
from unittest import mock
from urllib.parse import parse_qs, unquote, urlsplit

from kaskad import errors, paths, qr, secrets, state, util
from kaskad.gw import model, ops, registry, xray, xraygrpc
from kaskad.gw.inbounds import base
from kaskad.gw.inbounds import hy2 as hin
from kaskad.gw.inbounds import vless as vin
from kaskad.panel import userpage
from tests.gw.tunnel_fakes import FakeOpenssl
from tests.test_gw_integration import RealAdapterCase

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "gw", "tunnel")


def rule_for(cfg, **match):
    for r in cfg["routing"]["rules"]:
        if all(r.get(k) == v for k, v in match.items()):
            return r
    return None


class Hy2Case(RealAdapterCase):
    def setUp(self):
        super().setUp()
        self.ssl = FakeOpenssl()
        self.w.runner.binaries.add("openssl")
        self.w.runner.on(["openssl"], self.ssl)
        self.grpc = []                       # (api, tag, email, account) of every live add of a user

        def add_user(api, tag, email, account, timeout=8, curl=None):
            self.grpc.append((api, tag, email, account))
            return True
        p = mock.patch.object(xraygrpc, "add_user", add_user)
        p.start()
        self.addCleanup(p.stop)
        old = vin._checker["fn"]
        vin._checker["fn"] = lambda host, port=443, timeout=5.0: {"ok": True, "tls": "TLSv1.3", "alpn": "h2",
                                                                  "h2": True, "why": ""}
        self.addCleanup(vin._checker.__setitem__, "fn", old)
        old_geo = vin.vless_lists._geo["fn"]
        vin.vless_lists._geo["fn"] = lambda st=None: {"code": "", "source": ""}
        self.addCleanup(vin.vless_lists._geo.__setitem__, "fn", old_geo)

    def cfg(self):
        return json.loads(self.read("/etc/kaskad/gw/xray.json"))

    def ib(self):
        got = [i for i in self.cfg()["inbounds"] if i["tag"] == hin.TAG]
        return got[0] if got else None

    def rec(self):
        return model.inbound(state.load(), "hy2")

    def prod(self, path):
        return path.replace(self.root, "")

    def restarts(self):
        return sum(1 for argv, _ in self.w.runner.calls if argv[:3] == ["systemctl", "restart", xray.UNIT])

    def add_device(self, name="Телефон", platform="android", access="hy2"):
        rc, doc, err = self.run_json("gw", "device", "add", "Мама", name, "--platform", platform, "--access", access)
        self.assertEqual(rc, 0, err)
        return doc["device"]

    def install(self, *extra):
        self.enabled_world()
        rc, doc, err = self.run_json("gw", "inbound", "install", "hy2", *extra)
        self.assertEqual(rc, 0, err)
        self.ok("gw", "user", "add", "Мама")
        return doc

    def link(self, did):
        rc, doc, err = self.run_json("gw", "issue", did, "--inbound", "hy2")
        self.assertEqual(rc, 0, err)
        it = [i for i in doc["items"] if i["format"] == "link"][0]
        return it["body"], doc


class LifeTest(Hy2Case):
    def test_the_whole_life_of_a_hysteria2_device(self):
        self.assertNotIsInstance(registry.get("hy2"), base.DummyInbound)
        doc = self.install()
        rec = self.rec()
        self.assertEqual((rec["port"], rec["obfs"], rec["cert"], rec["cert_mode"], rec["hop"], rec["congestion"]),
                         (443, True, "domain", "self", None, "bbr"))        # no domain: the self-signed one
        sec = secrets.load()
        self.assertEqual(len(sec[hin.OBFS_KEY]), 32)
        self.assertEqual(rec["pin"], hin.pin_of(sec[hin.SELF_CRT]))
        self.assertRegex(rec["pin"], r"^[0-9a-f]{64}$")
        self.assertNotIn(sec[hin.OBFS_KEY], json.dumps(doc))
        self.assertIn(("443/udp", "kaskad-gw hy2"), self.w.ufw.rules)
        ib = self.ib()
        self.assertEqual((ib["port"], ib["protocol"], ib["listen"], ib["settings"]),
                         (443, "hysteria", "0.0.0.0", {"version": 2, "clients": []}))      # no device: there, empty
        ss = ib["streamSettings"]
        self.assertEqual((ss["network"], ss["security"], ss["hysteriaSettings"], ss["tlsSettings"]["alpn"]),
                         ("hysteria", "tls", {"version": 2}, ["h3"]))
        self.assertEqual(ss["finalmask"], {"udp": [{"type": "salamander", "settings": {"password": sec[hin.OBFS_KEY]}}],
                                           "quicParams": {"congestion": "bbr"}})
        crt, key = ss["tlsSettings"]["certificates"][0]["certificateFile"], ss["tlsSettings"]["certificates"][0]["keyFile"]
        self.assertEqual((self.prod(crt), self.prod(key)), ("/opt/kaskad/gw/tls-hy2/self/cert.pem",
                                                           "/opt/kaskad/gw/tls-hy2/self/key.pem"))
        self.assertEqual(self.read(self.prod(crt)), sec[hin.SELF_CRT])
        self.assertEqual(oct(os.stat(key).st_mode & 0o777), "0o640")
        self.assertEqual(oct(os.stat(os.path.dirname(key)).st_mode & 0o777), "0o750")
        pol = self.cfg()["policy"]
        self.assertFalse(any(k.startswith("statsInbound") for k in json.dumps(pol).split('"')))   # T0: never
        from tests import realbin
        if realbin.in_fakes():                                             # the real xray took every config written
            self.assertTrue(self.w.xray.real and all(self.w.xray.real), self.w.xray.real)

        restarts = self.restarts()
        d = self.add_device()
        self.assertEqual(self.restarts(), restarts)                       # live: gRPC AlterInbound, no restart
        auth = secrets.load()[hin.auth_key(d["id"])]
        self.assertEqual([(t, e) for _, t, e, _ in self.grpc], [(hin.TAG, d["login"] + ".h2")])
        self.assertEqual(self.grpc[0][3], (hin.ACCOUNT_TYPE, xraygrpc.field(1, auth)))
        added = [docs for v, _, docs in self.w.xray.api if v == "adrules"]
        self.assertTrue(added and all(r["ruleTag"].startswith("h2-") for r in added[0][0]["routing"]["rules"]))
        ib = self.ib()
        self.assertEqual(ib["settings"]["clients"], [{"auth": auth, "level": 0, "email": d["login"] + ".h2"}])
        mine = rule_for(self.cfg(), inboundTag=[hin.TAG], user=[d["login"] + ".h2"])
        self.assertEqual(mine["outboundTag"], self.target_of(d["id"])["outboundTag"])
        self.assertTrue(mine["ruleTag"].startswith("h2-%s-" % d["login"]))

        url, doc = self.link(d["id"])
        u = urlsplit(url)
        q = dict((k, v[0]) for k, v in parse_qs(u.query).items())
        self.assertEqual((u.scheme, unquote(u.username), u.hostname, u.port, u.path), ("hysteria2", auth,
                                                                                       "203.0.113.10", 443, "/"))
        self.assertEqual(q, {"sni": "203.0.113.10", "obfs": "salamander", "obfs-password": sec[hin.OBFS_KEY],
                             "insecure": "1", "pinSHA256": rec["pin"]})
        self.assertEqual(unquote(u.fragment), "goKaskad PRO · Hysteria2 · Телефон")
        self.assertEqual(qr.qr_for(url), "hy2")
        red = util.redact("issued " + url)                      # the journal: neither the auth nor the Salamander password
        self.assertNotIn(auth, red)
        self.assertNotIn(sec[hin.OBFS_KEY], red)
        self.assertIn("v2RayTun", " ".join(doc["howto"]))                  # the app of the steps (issue_os _HY2_APP)

        # secrets: only in xray.json (root) and in issuance
        for v in (sec[hin.OBFS_KEY], auth):
            for argv in (("gw", "status"), ("gw", "device", "list"), ("gw", "inbound", "list"), ("gw", "sessions"),
                         ("gw", "hy2", "show"), ("gw", "hy2", "show", "--json"), ("gw", "scheme")):
                self.assertNotIn(v, self.run_cli(*argv)[1], argv)
            self.assertNotIn(v, self.log())
            self.assertNotIn(v, json.dumps(state.load()))
        for argv, inp in self.w.runner.calls:                     # never in argv
            self.assertFalse(any(auth in a or sec[hin.OBFS_KEY] in a for a in argv), argv)

        # a new secret of the device: the old link dead, its open QUIC connection must go — a restart (T0: rmu does not)
        restarts = self.restarts()
        self.ok("gw", "device", "reset", d["id"], "--inbound", "hy2")
        auth2 = secrets.load()[hin.auth_key(d["id"])]
        self.assertNotEqual(auth, auth2)
        self.assertEqual([c["auth"] for c in self.ib()["settings"]["clients"]], [auth2])
        self.assertEqual(self.restarts(), restarts + 1)
        restarts = self.restarts()
        self.ok("gw", "device", "disable", d["id"])               # off: out of xray, its secret kept — a restart
        self.assertEqual(self.ib()["settings"]["clients"], [])
        self.assertEqual(self.restarts(), restarts + 1)
        self.ok("gw", "device", "enable", d["id"])

        # removal: no trace — the UFW rule, its directory, its own certificates, the inbound of xray
        self.ok("gw", "inbound", "uninstall", "hy2")
        self.assertNotIn(("443/udp", "kaskad-gw hy2"), self.w.ufw.rules)
        self.assertFalse(os.path.exists(paths.P(hin.TLS_DIR)))
        self.assertFalse(os.path.exists(os.path.join(paths.gw_dir(), "hy2")))
        self.assertIsNone(self.ib())
        self.assertIsNone(rule_for(self.cfg(), inboundTag=[hin.TAG]))
        # a reinstall: the same port, the same Salamander password and pin — the links given out work again
        rc, doc, err = self.run_json("gw", "inbound", "install", "hy2")
        self.assertEqual(rc, 0, err)
        self.assertEqual((self.rec()["port"], self.rec()["pin"]), (443, rec["pin"]))
        self.assertEqual(self.ib()["streamSettings"]["finalmask"]["udp"][0]["settings"]["password"], sec[hin.OBFS_KEY])

    def test_a_device_on_vless_and_hysteria2_is_two_users_counted_once_each(self):
        self.install()
        rc, _, err = self.run_json("gw", "inbound", "install", "vless")
        self.assertEqual(rc, 0, err)
        d = self.add_device(access="vless,hy2")
        cfg = self.cfg()
        emails = dict((i["tag"], [c["email"] for c in i["settings"]["clients"]]) for i in cfg["inbounds"]
                      if i["tag"] in ("gw-vless", hin.TAG))
        self.assertEqual(emails, {"gw-vless": [d["login"]], hin.TAG: [d["login"] + ".h2"]})     # never the same email
        # the counters: VLESS reads its user, Hysteria2 its own — their sum is the traffic of the device, no double
        self.w.xray.stats = {d["login"]: (100, 1000), d["login"] + ".h2": (7, 70)}
        users_v = {d["login"]: d["id"]}
        users_h = {d["login"] + ".h2": d["id"]}
        _, rx_v, tx_v = registry.get("vless").traffic(self.w.runner, users_v)
        _, rx_h, tx_h = registry.get("hy2").traffic(self.w.runner, users_h)
        self.assertEqual((rx_v, tx_v, rx_h, tx_h), (100, 1000, 7, 70))

    def test_vless_reality_next_to_hysteria2_on_the_domain_keeps_its_copy(self):
        # С3: VLESS (Reality, no site) removes /opt/kaskad/gw/tls on every apply — Hysteria2 has its own copy
        self.enabled_world()
        with open(os.path.join(FIX, "server.crt"), encoding="utf-8") as f:
            self.write("/etc/kaskad/site/tls/current/fullchain.pem", f.read())
        self.write("/etc/kaskad/site/tls/current/privkey.pem", "-----BEGIN PRIVATE KEY-----\nK\n-----END PRIVATE KEY-----\n")
        with state.transaction() as st:
            st["site"] = {"domain": "example.com", "mode": "domain", "cert": "letsencrypt"}
        rc, _, err = self.run_json("gw", "inbound", "install", "hy2")
        self.assertEqual(rc, 0, err)
        rc, _, err = self.run_json("gw", "inbound", "install", "vless")
        self.assertEqual(rc, 0, err)
        self.ok("gw", "user", "add", "Мама")
        d = self.add_device()
        self.assertEqual(self.rec()["cert_mode"], "domain")
        chain = self.ib()["streamSettings"]["tlsSettings"]["certificates"][0]["certificateFile"]
        self.assertEqual(self.prod(chain), "/opt/kaskad/gw/tls-hy2/domain/fullchain.pem")
        for _ in range(2):
            self.ok("gw", "apply")
        self.ok("gw", "inbound", "uninstall", "vless")
        self.assertTrue(os.path.isfile(chain))
        url, _ = self.link(d["id"])
        u = urlsplit(url)
        q = dict((k, v[0]) for k, v in parse_qs(u.query).items())
        self.assertEqual((u.hostname, q["sni"]), ("example.com", "example.com"))
        self.assertNotIn("insecure", q)
        self.assertNotIn("pinSHA256", q)
        pin = self.rec()["pin"]
        # the domain away: back to the self-signed pair — the same pin as before (self -> domain -> self)
        with state.transaction() as st:
            st["site"] = {"domain": "", "mode": "off", "cert": ""}
        from kaskad.site import consumers
        got = consumers.reload(self.w.runner, state.load())
        hy = [c for c in got if c["name"] == "hy2"][0]
        self.assertEqual(hy["action"], "switch")
        self.assertEqual([r["device"] for r in hy["reissue"]], [d["id"]])           # REV С-2: whom to give links again
        self.assertEqual((self.rec()["cert_mode"], self.rec()["pin"]), ("self", pin))
        self.assertNotIn("tls-hy2/domain", self.read("/etc/kaskad/gw/xray.json"))
        self.assertTrue(os.path.exists(chain))         # SEC З-1: the copy stays while the running config may name it
        self.ok("gw", "apply")
        self.assertFalse(os.path.exists(chain))                     # its own copy of the domain gone, only its own
        url, _ = self.link(d["id"])
        self.assertIn("pinSHA256=" + pin, url)

    def test_rekey_restarts_xray(self):
        # SEC С-1: the config is the same, the files are not — xray must take the new pair at once
        self.install()
        self.add_device()
        before = self.restarts()
        rc, doc, err = self.run_json("gw", "hy2", "rekey", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.restarts(), before + 1)

    def test_a_reinstall_brings_the_range_back_only_when_it_fits(self):
        # REV С-1: a neighbour took a port of the range while Hysteria2 was away — no range (xray would not start)
        self.install()
        self.ok("gw", "inbound", "option", "hy2", "hop", "20000-20099")
        self.ok("gw", "inbound", "uninstall", "hy2")
        rc, _, err = self.run_json("gw", "inbound", "install", "wg", "--port", "20050")
        self.assertEqual(rc, 0, err)
        rc, doc, err = self.run_json("gw", "inbound", "install", "hy2")
        self.assertEqual(rc, 0, err)
        self.assertIsNone(self.rec()["hop"])
        self.assertTrue(any("20000-20099" in n and "20050/udp" in n for n in doc["notes"]), doc["notes"])
        self.assertFalse(doc["previous"]["restored"])                 # round 2 Н-1: the links given out changed
        self.assertEqual(self.ib()["port"], 443)
        # it fits — it comes back; a port asked inside it — no range
        self.ok("gw", "inbound", "option", "hy2", "hop", "21000-21009")
        self.ok("gw", "inbound", "uninstall", "hy2")
        rc, doc, err = self.run_json("gw", "inbound", "install", "hy2")
        self.assertEqual((rc, self.rec()["hop"]), (0, "21000-21009"), err)
        self.assertTrue(doc["previous"]["restored"])
        self.ok("gw", "inbound", "uninstall", "hy2")
        rc, doc, err = self.run_json("gw", "inbound", "install", "hy2", "--port", "21005")
        self.assertEqual((rc, self.rec()["port"], self.rec()["hop"]), (0, 21005, None), err)

    def test_the_mask_and_the_port_list_whom_to_give_the_links_again(self):
        # REV З-1, З-2: the answers carry «reissue», the option of the panel for hop/cert answers too
        self.install()
        d = self.add_device()
        rc, doc, err = self.run_json("gw", "inbound", "option", "hy2", "obfs", "off")
        self.assertEqual(rc, 0, err)
        self.assertEqual([r["id"] for r in doc["reissue"]], [d["id"]])
        self.assertNotIn("udp", self.ib()["streamSettings"]["finalmask"])           # no Salamander in xray
        rc, doc, err = self.run_json("gw", "inbound", "port", "hy2", "8443")
        self.assertEqual(rc, 0, err)
        self.assertEqual([r["id"] for r in doc["reissue"]], [d["id"]])
        res = ops.inbound_option(self.w.runner, "hy2", "cert", "self")
        self.assertEqual((res["key"], res["value"]), ("cert", "self"))
        self.assertEqual(res["reissue"], [])                       # no domain: the mode is the same, the links too

    def test_the_site_says_whom_to_give_hysteria2_again(self):
        # REV С-2: site.ops takes the rows of Hysteria2 out of the consumers into «affected» / the warning
        from kaskad.site import ops as site_ops
        rows = [{"device": "d-1", "name": "Телефон", "kind": "hy2", "user_name": "Мама"}]
        self.assertEqual(site_ops.hy2_reissue([{"name": "panel", "action": "reload"},
                                               {"name": "hy2", "action": "switch", "reissue": rows}]), rows)
        self.assertEqual(site_ops.hy2_reissue([{"name": "panel"}]), [])
        self.assertIn("Hysteria2", site_ops.reissue_text(rows, "x"))
        self.assertIn("Телефон (Мама)", site_ops.reissue_text(rows, "x"))

    def test_the_old_xray_is_refused_with_words(self):
        with mock.patch.object(xray, "XRAY_VERSION", "26.1.23"):
            req = registry.get("hy2").requirements(self.w.runner)
        self.assertFalse(req["supported"])
        self.assertIn("26.3.27", req["why"])
        self.assertIn("26.1.23", req["why"])


class PortsTest(Hy2Case):
    def test_443_busy_by_ocserv_takes_8443(self):
        self.enabled_world()
        self.w.ss = 'udp   UNCONN 0      0      0.0.0.0:443    0.0.0.0:*    users:(("ocserv-main",pid=77,fd=5))\n'
        rc, doc, err = self.run_json("gw", "inbound", "install", "hy2")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.rec()["port"], 8443)
        self.assertTrue(any("443/udp" in n and "8443" in n for n in doc["notes"]), doc["notes"])

    def test_port_hopping_one_ufw_rule_one_ss_and_the_link(self):
        self.install()
        d = self.add_device()
        ss_before = len(self.w.runner.commands(["ss"]))
        ufw_before = len([c for c in self.w.runner.commands(["ufw"]) if "allow" in c])
        rc, doc, err = self.run_json("gw", "inbound", "option", "hy2", "hop", "20000-20099")
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["hop"], "20000-20099")
        self.assertTrue(doc["reissue"])                                  # the links given out — again
        self.assertIn(("20000:20099/udp", "kaskad-gw hy2"), self.w.ufw.rules)
        allows = [c for c in self.w.runner.commands(["ufw"]) if "allow" in c][ufw_before:]
        self.assertEqual(len([c for c in allows if "20000:20099/udp" in c]), 1)
        self.assertLessEqual(len(self.w.runner.commands(["ss"])) - ss_before, 3)   # one for the range, not 100
        ss0 = len(self.w.runner.commands(["ss"]))
        self.assertEqual(ops.hop_conflicts(state.load(), self.w.runner, 30000, 30099), [])
        self.assertEqual(len(self.w.runner.commands(["ss"])) - ss0, 1)           # the check of the range: one ss
        self.assertEqual(self.ib()["port"], "443,20000-20099")
        st = state.load()
        self.assertEqual(registry.get("hy2").ports_view(st), [("udp", "443"), ("udp", "20000–20099")])
        self.assertEqual(len(registry.get("hy2").ports(st)), 101)
        url, _ = self.link(d["id"])
        self.assertIn("@203.0.113.10:443,20000-20099/?", url)
        # 101 ports, outside 1024–65535, the port inside the range — refused
        for bad in ("20000-20100", "100-150", "x"):
            rc, doc, _ = self.run_json("gw", "inbound", "option", "hy2", "hop", bad)
            self.assertEqual(rc, errors.USAGE, (bad, doc))
        rc, doc, _ = self.run_json("gw", "inbound", "port", "hy2", "20050")       # the port inside its own range
        self.assertEqual(rc, errors.CONFLICT, doc)
        self.assertEqual(self.rec()["port"], 443)
        # off: the rule of the range goes
        self.ok("gw", "inbound", "option", "hy2", "hop", "off")
        self.assertNotIn(("20000:20099/udp", "kaskad-gw hy2"), self.w.ufw.rules)
        self.assertEqual(self.ib()["port"], 443)

    def test_the_cli_and_mcp_way_gw_hy2_set_and_rekey(self):
        self.install()
        d = self.add_device()
        rc, doc, err = self.run_json("gw", "hy2", "set", "--hop", "21000-21009", "--cert", "self")
        self.assertEqual(rc, 0, err)
        self.assertEqual((doc["hop"], doc["cert"], [r["id"] for r in doc["reissue"]]), ("21000-21009", "self", [d["id"]]))
        rc, doc, _ = self.run_json("gw", "hy2", "set")
        self.assertEqual(rc, errors.USAGE, doc)
        rc, doc, _ = self.run_json("gw", "hy2", "rekey")                  # asks for --yes
        self.assertEqual(rc, errors.USAGE, doc)
        rc, doc, err = self.run_json("gw", "hy2", "rekey", "--yes")
        self.assertEqual(rc, 0, err)
        self.assertEqual([r["id"] for r in doc["reissue"]], [d["id"]])
        self.assertRegex(doc["pin"], r"^[0-9a-f]{64}$")
        out, _ = self.ok("gw", "hy2", "show")
        self.assertIn("Смена портов: 21000-21009.", out)
        self.assertIn("самоподписанный ⚠", out)

    def test_neighbours_see_the_range_both_ways(self):
        self.install()
        self.ok("gw", "inbound", "option", "hy2", "hop", "30000-30010")
        st = state.load()
        # the other way: a forward or an inbound on a port inside the range is refused — also with xray off
        self.ok("gw", "inbound", "disable", "hy2")
        found = ops.port_conflicts(state.load(), self.w.runner, "wg", "udp", 30005)
        self.assertTrue(any(c["kind"] == "gateway" and "Hysteria2" in c["detail"] for c in found), found)
        self.ok("gw", "inbound", "enable", "hy2")
        # a range over a port of another inbound — refused with the list of the busy ones
        rc, _, err = self.run_json("gw", "inbound", "install", "wg", "--port", "31005")
        self.assertEqual(rc, 0, err)
        rc, doc, _ = self.run_json("gw", "inbound", "option", "hy2", "hop", "31000-31010")
        self.assertEqual(rc, errors.CONFLICT, doc)
        self.assertIn("31005/udp", doc["error"])
        self.assertEqual(model.inbound(state.load(), "hy2")["hop"], "30000-30010")
        self.assertIsNotNone(st)

    def test_the_card_of_the_panel_and_the_overview_show_one_range(self):
        self.install()
        self.ok("gw", "inbound", "option", "hy2", "hop", "20000-20099")
        ad = registry.get("hy2")
        self.assertEqual(ops.ports_shown(ad, state.load()), ["443/udp", "20000–20099/udp"])
        rc, doc, err = self.run_json("gw", "inbound", "list")
        self.assertEqual(rc, 0, err)
        row = [r for r in doc["inbounds"] if r["kind"] == "hy2"][0] if isinstance(doc, dict) else \
            [r for r in doc if r["kind"] == "hy2"][0]
        self.assertEqual(row["ports"], ["443/udp", "20000–20099/udp"])          # not 101 entries


class SubscriptionTest(Hy2Case):
    def test_the_subscription_has_the_hysteria2_link(self):
        self.assertEqual(userpage.SUB_KINDS["hy2"], ("hysteria2://",))
        self.install()
        rc, _, err = self.run_json("gw", "inbound", "install", "vless")
        self.assertEqual(rc, 0, err)
        d = self.add_device(access="vless,hy2")
        user = model.find_user(state.load(), d["user"])
        _, links = userpage.build_sub(user, self.w.runner)
        self.assertEqual([l.split("://", 1)[0] for l in links], ["vless", "hysteria2"])           # VLESS and Hysteria2
        url, _ = self.link(d["id"])
        self.assertIn(url, links)

    def test_a_long_cyrillic_name_is_one_scannable_code(self):
        # §4.1: the link with a Cyrillic name of 40 characters (percent-encoded — three times the bytes) and the range is
        # ONE code a phone takes from the frame of a computer (≤ 2 parts — it needs no parts at all)
        self.install()
        self.ok("gw", "inbound", "option", "hy2", "hop", "20000-20099")
        d = self.add_device(name="Ж" * 40)
        url, doc = self.link(d["id"])
        self.assertGreater(len(url), 400)
        self.assertTrue(qr.scannable(url), qr.version_of(url))
        q = [i for i in doc["items"] if i["format"] == "qr"][0]
        self.assertEqual(q["body"], [url])


class StatusKT3Test(Hy2Case):
    """KT3-4: ``ss -Hlnup`` (one kind) prints no Netid column — a live Hysteria2 was «does not work» everywhere."""
    REAL = ('UNCONN 0      0                  *:443              *:*    users:(("xray",pid=95256,fd=9))\n'
            'UNCONN 0      0      127.0.0.53%lo:53         0.0.0.0:*    users:(("systemd-resolve",pid=500,fd=13))\n')

    def test_the_real_ss_of_one_kind_is_read(self):
        from kaskad import conflicts
        rows = conflicts.parse_ss(self.REAL, proto="udp")
        self.assertEqual(rows[0], {"proto": "udp", "addr": "*", "port": 443, "procs": ["xray"], "pids": [95256]})
        self.assertEqual((rows[1]["addr"], rows[1]["port"]), ("127.0.0.53", 53))
        self.assertEqual(conflicts.parse_ss("State Recv-Q Send-Q Local Address:Port Peer Address:Port\n" + self.REAL,
                                            proto="udp")[0]["port"], 443)      # a header of an old ss — skipped
        self.assertEqual(conflicts.parse_ss(self.REAL), [])                    # kind unknown: nothing guessed

    def test_a_live_hysteria2_works_in_the_list_and_the_status(self):
        self.install()
        argvs = []
        real = self.w.ss_with_xray

        def ss(argv, inp):
            argvs.append(list(argv))
            return real(argv, inp)
        self.w.runner._handlers.insert(0, (["ss"], ss))
        rc, doc, err = self.run_json("gw", "inbound", "list")
        self.assertEqual(rc, 0, err)
        rows = doc["inbounds"] if isinstance(doc, dict) else doc
        row = [r for r in rows if r["kind"] == "hy2"][0]
        self.assertTrue(row["running"], row)
        self.assertNotIn(hin.tr("gw_in_hy2.warn.not_listening", port=443), row.get("warnings") or [])
        self.assertIn(["ss", "-Hlnup"], argvs)                      # the fake answered as the real one: no Netid
        _, out, _ = real(["ss", "-Hlnup"], None)
        self.assertTrue(out.lstrip().startswith("UNCONN"), out)


class DomainAtOnceKT3Test(Hy2Case):
    """KT3-5: certbot's deploy hook copied the pair before ``site cert issue`` recorded «letsencrypt» — the second
    ``cert_installed`` saw the same copy and told nobody: Hysteria2 stayed self-signed until some later full apply."""

    def test_the_issue_switches_hysteria2_at_once_and_says_whom(self):
        self.install()
        d = self.add_device()
        self.assertEqual(self.rec()["cert_mode"], "self")
        with open(os.path.join(FIX, "server.crt"), encoding="utf-8") as f:
            self.write("/etc/kaskad/site/tls/current/fullchain.pem", f.read())
        self.write("/etc/kaskad/site/tls/current/privkey.pem", "-----BEGIN PRIVATE KEY-----\nK\n-----END PRIVATE KEY-----\n")
        from kaskad.site import consumers, ops as site_ops
        with state.transaction() as st:
            st["site"] = {"domain": "example.com", "mode": "domain", "cert": "letsencrypt"}
        with mock.patch.object(consumers, "install_copy", lambda domain: (False, {"not_after": "2027-01-17"})):
            got = consumers.cert_installed(self.w.runner)                  # the copy is the hook's already
        self.assertEqual([(c["name"], c["action"]) for c in got["consumers"]], [("hy2", "switch")])
        self.assertEqual([r["device"] for r in got["consumers"][0]["reissue"]], [d["id"]])
        self.assertEqual(self.rec()["cert_mode"], "domain")
        self.assertIn("tls-hy2/domain", self.read("/etc/kaskad/gw/xray.json"))
        self.assertIn("Hysteria2", site_ops.reissue_text(site_ops.hy2_reissue(got["consumers"]), "x"))
        with mock.patch.object(consumers, "install_copy", lambda domain: (False, {"not_after": "2027-01-17"})):
            self.assertEqual(consumers.cert_installed(self.w.runner)["consumers"], [])   # the same now: nothing
        doc = site_ops.status(self.w.runner)
        self.assertIs(doc["use"]["hy2"], True)
        from kaskad.site import cli as site_cli
        self.assertTrue(any("Hysteria2 — да" in ln for ln in site_cli.status_lines(doc)))


class Rev3373ApplyTest(Hy2Case):
    """REV-3373 З-3: «Применить настройки» на карточке Hysteria2 при негодном сертификате — копия для xray пишется
    заново из secrets.json (тот же pin, ключи устройств прежние), xray перезапускается: конфиг тот же, и одно применение
    оставило бы ему негодную пару, прочитанную при старте."""

    @staticmethod
    def text(path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_a_broken_self_copy_is_written_again_and_xray_restarts(self):
        from kaskad.gw import inlog, ops
        self.install()
        d = self.add_device()
        pin = self.rec()["pin"]
        crt = hin.self_files()[0]
        good = self.text(crt)
        with open(crt, "w", encoding="utf-8") as f:
            f.write("not a certificate\n")
        self.assertEqual(inlog.cert_bad("hy2", state.load()), "unreadable")
        before = self.restarts()
        res = ops.hy2_apply(self.w.runner)
        self.assertEqual(self.text(crt), good)
        self.assertEqual(self.restarts(), before + 1)
        self.assertTrue(res["restarted"])
        self.assertEqual((res["reissue"], self.rec()["pin"]), ([], pin))          # the same pair: no links again
        url, _ = self.link(d["id"])
        self.assertIn("pinSHA256=" + pin, url)

    def test_the_button_restarts_xray_even_when_the_copy_is_already_right(self):
        # REV-3373-pre 2: another apply (a device added) may have written the copy already — xray still runs the old pair
        from kaskad.gw import ops
        self.install()
        self.w.runner.on(["systemctl", "is-active"], lambda argv, inp: (0, "active\n", ""))
        before = self.restarts()
        res = ops.hy2_apply(self.w.runner)
        self.assertEqual(self.restarts(), before + 1)
        self.assertTrue(res["restarted"])
        self.assertEqual(res["reissue"], [])

    def test_domain_lost_the_mode_turns_self_says_whom_and_restarts_once(self):
        # REV-3373-pre 1 + 4: the site's pair is gone — Hysteria2 goes self-signed, the links gain the pin: whom to give
        # them again; the apply restarted xray for the new config — the button does not restart it a second time
        from kaskad.gw import ops
        self.enabled_world()
        with open(os.path.join(FIX, "server.crt"), encoding="utf-8") as f:
            self.write("/etc/kaskad/site/tls/current/fullchain.pem", f.read())
        self.write("/etc/kaskad/site/tls/current/privkey.pem", "-----BEGIN PRIVATE KEY-----\nK\n-----END PRIVATE KEY-----\n")
        with state.transaction() as st:
            st["site"] = {"domain": "example.com", "mode": "domain", "cert": "letsencrypt"}
        rc, _, err = self.run_json("gw", "inbound", "install", "hy2")
        self.assertEqual(rc, 0, err)
        self.ok("gw", "user", "add", "Мама")
        d = self.add_device()
        self.assertEqual(self.rec()["cert_mode"], "domain")
        self.w.runner.on(["systemctl", "is-active"], lambda argv, inp: (0, "active\n", ""))
        os.remove(self.path("/etc/kaskad/site/tls/current/fullchain.pem"))
        before = self.restarts()
        res = ops.hy2_apply(self.w.runner)
        self.assertEqual(self.rec()["cert_mode"], "self")
        self.assertEqual([r["id"] for r in res["reissue"]], [d["id"]])
        self.assertIn("Мама", res["text"])
        self.assertTrue(res["restarted"])
        self.assertEqual(self.restarts(), before + 1)
        url, _ = self.link(d["id"])
        self.assertIn("pinSHA256=", url)

    def test_not_installed_is_refused(self):
        from kaskad.errors import KaskadError
        from kaskad.gw import ops
        self.enabled_world()
        with self.assertRaises(KaskadError):
            ops.hy2_apply(self.w.runner)

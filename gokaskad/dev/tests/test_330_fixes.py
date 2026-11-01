"""Kaskad 3.3.0 — the owner's decisions and the findings of the live link with goAmnezia (T-X6):

    * a key of a subscription removed by hand is excluded — no refresh brings it back; «Вернуть исключённые»;
    * one server once in the pool: vpn:// and .conf of one peer (the public key of the peer + host:port) are not added
      twice; repeats already there — a warning of «Проверка сервера»;
    * a subscription by https://IP whose certificate a CA issued to a domain — the reason in words, the domain named;
    * the formats of goAmnezia Pro (all.txt — amneziawg:// lines as its pages_server writes them, all.yaml — mihomo);
    * «gw collect --help» says «--once»; exits of a gateway that is off — «включите шлюз».
"""

import base64
import json
import os
import re
import shutil
import ssl
import subprocess
import threading
import urllib.parse
from unittest import mock

from kaskad import summary
from kaskad.errors import ConflictError, KaskadError
from kaskad.gw import exits, exitsubs, ops, subfetch
from tests.fakes_gw import VLESS_A, VLESS_B, write_pool
from tests.gwcase import GwCase
from tests.test_gw_exits import conf_of, vpn_of
from tests.test_gw_exitsubs import IP_URL, SubsCase, b64
from tests.panel.panelcase import PanelCase
from tests.test_gw_ops import CliGwCase


def ga_txt_line(conf, name):
    """A line of all.txt exactly as goAmnezia Pro writes it (lib/pages_server.py amneziawg_link)."""
    body = (conf.rstrip("\n") + "\n").encode("utf-8")
    return "amneziawg://%s#%s" % (base64.urlsafe_b64encode(body).decode("ascii").rstrip("="),
                                  urllib.parse.quote(name, safe=""))


def lines_of(text):
    return exits.load_parser().extract(text)


# ── 1. excluded keys ───────────────────────────────────────────────────────

class ExcludedTest(SubsCase):
    def test_a_key_removed_by_hand_is_not_brought_back(self):
        self.add(IP_URL, b64([VLESS_A, VLESS_B]))
        a = exits.exit_id(VLESS_A)
        ops.exit_del(self.w.runner, a)                       # the panel, the menu, the bot — all through egress_pool_drop
        self.assertEqual(self.read("/etc/kaskad/egress/subs-excluded.pending"), a + "\n")   # an id, never a key
        row = exitsubs.listing()[0]
        self.assertEqual((row["excluded"], row["keys"]), (1, 1))       # told before it is folded
        res = self.refresh()
        self.assertTrue(res["ok"])
        self.assertEqual(self.pool(), [VLESS_B])
        self.assertFalse(os.path.exists(exitsubs.pending_path()))
        sub = exitsubs.load()["subs"][0]
        self.assertEqual((sub["excluded"], sub["keys"]), ([a], [exits.exit_id(VLESS_B)]))
        row = exitsubs.listing()[0]
        self.assertEqual(row["excluded"], 1)
        self.assertIn("исключено вручную: 1", row["text"])
        self.refresh()                                        # and again: still out
        self.assertEqual(self.pool(), [VLESS_B])

    def test_restore_brings_them_back(self):
        self.add(IP_URL, b64([VLESS_A, VLESS_B]))
        ops.exit_del(self.w.runner, exits.exit_id(VLESS_A))
        self.refresh()
        res = exitsubs.restore(self.w.runner, "1", fetcher=self.feed)
        self.assertTrue(res["ok"])
        self.assertEqual(res["restored"], 1)
        self.assertIn("возвращены", res["text"])
        self.assertEqual(sorted(self.pool()), sorted([VLESS_A, VLESS_B]))
        self.assertEqual(exitsubs.load()["subs"][0]["excluded"], [])
        self.assertEqual(exitsubs.listing()[0]["excluded"], 0)

    def test_restore_that_failed_to_download_still_forgets(self):
        self.add(IP_URL, b64([VLESS_A, VLESS_B]))
        ops.exit_del(self.w.runner, exits.exit_id(VLESS_A))
        self.feed.bodies[IP_URL] = KaskadError("Сервер подписки не ответил за 20 с.")
        res = exitsubs.restore(self.w.runner, "1", fetcher=self.feed)
        self.assertFalse(res["ok"])
        self.assertIn("вернутся при следующем обновлении", res["text"])
        self.assertEqual(exitsubs.load()["subs"][0]["excluded"], [])
        self.feed.bodies[IP_URL] = b64([VLESS_A, VLESS_B])
        self.refresh()
        self.assertIn(VLESS_A, self.pool())

    def test_a_removal_no_subscription_lists_is_forgotten(self):
        self.add(IP_URL, VLESS_B)
        write_pool([VLESS_B, VLESS_A])                        # a key by hand
        ops.exit_del(self.w.runner, exits.exit_id(VLESS_A))
        self.refresh()
        self.assertEqual(exitsubs.load()["subs"][0]["excluded"], [])
        self.assertFalse(os.path.exists(exitsubs.pending_path()))

    def test_without_subscriptions_no_pending_file(self):
        write_pool([VLESS_A, VLESS_B])
        ops.exit_del(self.w.runner, exits.exit_id(VLESS_A))
        self.assertFalse(os.path.exists(exitsubs.pending_path()))

    def test_the_excluded_are_capped(self):
        doc = {"schema": 1, "next": 2, "subs": [{"id": "s-aaaaaa", "n": 1, "url": IP_URL, "auto": True,
                                                 "keys": [], "seen": ["e-%010x" % i for i in range(300)]}]}
        doc = json.loads(json.dumps(doc))
        exitsubs.save(doc)
        doc = exitsubs.load()
        exitsubs._exclude(doc, ["e-%010x" % i for i in range(300)])
        self.assertEqual(len(doc["subs"][0]["excluded"]), exitsubs.EXCLUDED_MAX)
        self.assertEqual(doc["subs"][0]["excluded"][-1], "e-%010x" % 299)


class RestoreCliTest(CliGwCase):
    def test_cli_and_mcp(self):
        from tests.fakes_gw import install_parser
        install_parser()
        feed = {IP_URL: b64([VLESS_A, VLESS_B])}
        with mock.patch.object(subfetch, "fetch_sub", lambda url, pin=None, new_pin=False: (feed[url], None)):
            exitsubs.add(self.w.runner, IP_URL)
            ops.exit_del(self.w.runner, exits.exit_id(VLESS_A))
            rc, doc, _ = self.run_json("gw", "exit-sub", "restore", "1")
        self.assertEqual((rc, doc["ok"], doc["restored"]), (0, True, 1))
        self.assertIn(VLESS_A, exits.pool_lines())
        import kaskad
        with open(os.path.join(os.path.dirname(kaskad.__file__), "mcp", "actions.json"), encoding="utf-8") as f:
            acts = json.load(f)
        self.assertEqual(acts["actions"]["exit_sub_restore"]["cmd"], ["gw", "exit-sub", "restore"])


# ── 2. one server once ─────────────────────────────────────────────────────

class SamePeerTest(SubsCase):
    def setUp(self):
        super().setUp()
        self.conf = conf_of("2.0", 3)
        self.by_conf = lines_of(self.conf)[0]
        self.by_vpn = lines_of(vpn_of(self.conf, "Офис"))[0]

    def test_vpn_and_conf_of_one_peer_are_one_server(self):
        self.assertNotEqual(exits.exit_id(self.by_conf), exits.exit_id(self.by_vpn))
        self.assertEqual(exits.peer_key(self.by_conf), exits.peer_key(self.by_vpn))
        other = lines_of(conf_of("2.0", 4))[0]
        self.assertNotEqual(exits.peer_key(self.by_conf), exits.peer_key(other))
        # VLESS — by uuid@host:port (T-M6-qs330 №6), other keys — by their text
        self.assertEqual(exits.peer_key(VLESS_A), "vless|11111111-2222-3333-4444-555555555555|198.51.100.20:443")
        self.assertEqual(exits.peer_key("ss://whatever"), exits.exit_id("ss://whatever"))
        self.assertEqual(exits.same_peers([self.by_conf, other, self.by_vpn]), [[1, 3]])

    def test_a_subscription_does_not_add_a_server_the_pool_has(self):
        write_pool([self.by_conf])                               # added by hand as .conf
        res = self.add(IP_URL, vpn_of(self.conf, "Офис"))       # the subscription brings the vpn:// of it
        self.assertEqual(res["result"]["added"], 0)
        self.assertEqual(self.pool(), [self.by_conf])
        self.assertEqual(exitsubs.load()["subs"][0]["keys"], [])

    def test_one_answer_with_both_gives_one_key(self):
        res = self.add(IP_URL, "\n".join([vpn_of(self.conf, "Офис"), ga_txt_line(self.conf, "Офис")]))
        self.assertEqual(res["result"]["added"], 1)
        self.assertEqual(len(self.pool()), 1)

    def test_preview_and_add_say_already_in_the_pool(self):
        write_pool([self.by_conf])
        res = ops.exit_preview(self.w.runner, vpn_of(self.conf, "Офис"))
        eid = exits.exit_id(self.by_conf)
        self.assertEqual([(x["in_pool"], x.get("same_peer"), x.get("same_how")) for x in res["items"]],
                         [(True, eid, "text")])
        self.assertIn("этот ключ уже есть как %s" % eid, res["items"][0]["same_text"])
        self.assertTrue(res["items"][0]["same_text"].endswith(", записан иначе"))
        self.assertEqual(res["text"], "Все ключи уже есть в пуле.")
        self.w.script.add_result = (4, "Уже в пуле — второй раз не добавляю: 1. Этот ключ уже есть (ключ № 1): …\n"
                                       "Новых ключей не добавлено (дубликаты или пусто).\n", "")
        with self.assertRaises(ConflictError) as c:
            ops.exit_add(self.w.runner, vpn_of(self.conf, "Офис"))
        self.assertTrue(str(c.exception).startswith("Второй раз не добавляю: этот ключ уже есть как %s" % eid),
                        str(c.exception))

    def test_repeats_in_the_pool_are_marked_and_told_by_the_check(self):
        other = lines_of(conf_of("2.0", 4))[0]
        write_pool([self.by_conf, other, self.by_vpn])
        pool = exits.pool(self.w.runner)
        self.assertEqual([e.get("same_peer") for e in pool], [None, None, pool[0]["id"]])
        doc = summary.summary()
        probs = [p for p in doc["problems"] if p["kind"] == "exits_same"]
        self.assertEqual(len(probs), 1, doc["problems"])
        self.assertIn("добавлен дважды (и как «Офис»): по одному адресу, но записан по-разному", probs[0]["detail"])
        self.assertEqual(probs[0]["item"], 8)                 # 8 → 7 → F3 deletes (stand T-335-release С1)
        self.assertNotIn("PrivateKey", json.dumps(doc))
        write_pool([self.by_conf, other])
        self.assertEqual([p for p in summary.summary()["problems"] if p["kind"] == "exits_same"], [])


# ── 1. the certificate of a domain at an address by IP ────────────────────

def socket_free_port():
    from tests.netports import free_port
    return free_port()


class CertDomainTest(GwCase):
    """subfetch._get against a local https server with a certificate a (test) CA issued to sub.example.org."""

    def setUp(self):
        super().setUp()
        from http.server import BaseHTTPRequestHandler, HTTPServer
        if not shutil.which("openssl"):
            self.skipTest("нет openssl")
        d = self.path("/tls")
        os.makedirs(d)
        j = lambda n: os.path.join(d, n)                         # noqa: E731
        with open(j("ext.cnf"), "w") as f:
            f.write("subjectAltName=DNS:sub.example.org\nbasicConstraints=critical,CA:FALSE\n"
                    "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
                    "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n")
        run = lambda *a: subprocess.run(a, stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode   # noqa: E731
        rcs = [run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", j("ca.key"), "-out",
                   j("ca.pem"), "-days", "2", "-subj", "/CN=Kaskad Test CA",
                   "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                   "-addext", "subjectKeyIdentifier=hash"),
               run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", j("leaf.key"), "-out", j("leaf.csr"),
                   "-subj", "/CN=sub.example.org"),
               run("openssl", "x509", "-req", "-in", j("leaf.csr"), "-CA", j("ca.pem"), "-CAkey", j("ca.key"),
                   "-CAcreateserial", "-out", j("leaf.pem"), "-days", "1", "-extfile", j("ext.cnf"))]
        if any(rcs):
            self.skipTest("openssl не выпустил сертификаты")
        self.ca = j("ca.pem")

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
        self.srv.handle_error = lambda request, address: None
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(j("leaf.pem"), j("leaf.key"))
        self.srv.socket = ctx.wrap_socket(self.srv.socket, server_side=True)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        p = mock.patch.object(subfetch, "_ctx", lambda: ssl.create_default_context(cafile=self.ca))
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        if hasattr(self, "srv"):
            self.srv.shutdown()
            self.srv.server_close()
        super().tearDown()

    def get(self, host, **kw):
        return subfetch._get("https", host, self.port, "/sub", "127.0.0.1", **kw)

    def test_by_the_domain_it_works(self):
        with mock.patch.object(subfetch, "_resolve", lambda h: ["127.0.0.1"]):
            self.assertEqual(self.get("sub.example.org").status, 200)

    def test_by_ip_the_domain_is_named_never_pinned(self):
        with mock.patch.object(subfetch, "_resolve", lambda h: ["127.0.0.1"]):
            with self.assertRaises(KaskadError) as c:
                self.get("127.0.0.1", new_pin=True)
        self.assertEqual(str(c.exception), "Сертификат выдан на домен sub.example.org — добавьте подписку по домену "
                                           "или по http.")
        self.assertEqual(c.exception.data, {"cert_domain": "sub.example.org", "same_server": True})
        with mock.patch.object(subfetch, "_resolve", lambda h: ["198.51.100.9"]):
            with self.assertRaises(KaskadError) as c:
                self.get("127.0.0.1", new_pin=True)
        self.assertFalse(c.exception.data["same_server"])     # the domain is elsewhere: no address offered

    def test_a_subscription_pinned_before_keeps_working_while_the_print_is_the_same(self):
        import hashlib
        with open(os.path.join(os.path.dirname(self.ca), "leaf.pem")) as f:
            pem = f.read()
        fp = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
        self.assertEqual(self.get("127.0.0.1", pin=fp).status, 200)
        with self.assertRaises(KaskadError) as c:
            self.get("127.0.0.1", pin="0" * 64)               # renewed: the domain is named, not «сменился»
        self.assertIn("выдан на домен sub.example.org", str(c.exception))

    def checked(self, host):
        """``subfetch.check_cert`` against the test server (check_url would refuse 127.0.0.1 as an inner address)."""
        with mock.patch.object(subfetch, "check_url", lambda url, resolve=None: ("https", host, self.port, "/sub",
                                                                                  "127.0.0.1")):
            return subfetch.check_cert("https://%s:%d/sub" % (host, self.port))

    def test_preview_checks_the_certificate_as_the_add_does(self):
        """T-M6-qs330b №10: the preview said «Сервер подписки найден» for https://<IP> with a certificate of a domain,
        the add refused it. check_cert — the same check without a download: the domain named, ``data`` for the page."""
        with mock.patch.object(subfetch, "_resolve", lambda h: ["127.0.0.1"]):
            self.assertIsNone(self.checked("sub.example.org"))              # by the domain: fine
            with self.assertRaises(KaskadError) as c:
                self.checked("127.0.0.1")
            with self.assertRaises(KaskadError) as add:
                self.get("127.0.0.1", new_pin=True)
        self.assertEqual(str(c.exception), str(add.exception))              # the same words as the add
        self.assertEqual(c.exception.data, {"cert_domain": "sub.example.org", "same_server": True})
        with self.assertRaises(KaskadError):
            self.checked("other.example.org")                               # a name the certificate is not for

    def test_preview_of_the_wizard_refuses_with_the_domain_in_json(self):
        from kaskad.setup import steps
        with mock.patch.object(subfetch, "_resolve", lambda h: ["127.0.0.1"]), \
                mock.patch.object(subfetch, "check_url", lambda url, resolve=None: ("https", "127.0.0.1", self.port,
                                                                                    "/sub", "127.0.0.1")), \
                mock.patch.object(exitsubs, "find_same", lambda url: None):
            out = steps.sub_check("https://203.0.113.5:%d/sub" % self.port)
        self.assertEqual((out["ok"], out["error"]["code"]), (False, "cert_name"), out)
        self.assertEqual(out["data"], {"cert_domain": "sub.example.org", "same_server": True})
        self.assertIn("выдан на домен sub.example.org", out["text"])
        self.assertNotIn("203.0.113.5", json.dumps(out))                   # the address never in the answer

    def test_no_answer_is_not_a_refusal_of_the_preview(self):
        s = socket_free_port()
        with mock.patch.object(subfetch, "check_url", lambda url, resolve=None: ("https", "127.0.0.1", s, "/", "127.0.0.1")):
            self.assertIsNone(subfetch.check_cert("https://127.0.0.1:%d/" % s))
        with mock.patch.object(subfetch, "check_url", lambda url, resolve=None: ("http", "127.0.0.1", s, "/", "127.0.0.1")):
            self.assertIsNone(subfetch.check_cert("http://127.0.0.1:%d/" % s))

    def test_the_answer_of_add_carries_no_address(self):
        err = subfetch.cert_name_error(["*.example.org", "example.org"], "203.0.113.5", resolve=lambda h: [])
        self.assertEqual(err.data, {"cert_domain": "example.org", "same_server": False})
        self.assertNotIn("203.0.113.5", str(err))


# ── 1. the formats of goAmnezia Pro ────────────────────────────────────────

class GoAmneziaFormatsTest(SubsCase):
    def test_all_txt_as_goamnezia_writes_it(self):
        url = "https://x6.example.org/s-%s/all.txt" % ("Z" * 43)
        body = "\n".join(ga_txt_line(conf_of(line, n), "AmneziaWG %s" % line)
                         for n, line in enumerate(("3.1", "2.0", "1.5"), 1)) + "\n"
        res = self.add(url, body)
        self.assertEqual(res["result"]["added"], 3)
        pool = exits.pool(self.w.runner)
        self.assertEqual([e["name"] for e in pool], ["AmneziaWG 3.1", "AmneziaWG 2.0", "AmneziaWG 1.5"])
        self.assertEqual([e["version"] for e in pool], ["3.1", "2.0", "1.5"])

    def test_all_yaml_says_take_all_vpn(self):
        body = ("mixed-port: 7890\nproxies:\n  - name: \"Аня · AmneziaWG 2.0\"\n    type: wireguard\n"
                "    server: 203.0.113.7\n    port: 51820\n    amnezia-wg-option:\n      jc: 4\nproxy-groups:\n"
                "  - name: PROXY\n    type: select\n")
        with self.assertRaises(KaskadError) as c:
            self.add("https://other.example.org/profile.yaml", body)
        self.assertIn("all.vpn", str(c.exception))
        self.assertEqual(self.pool(), [])
        url, note = exitsubs.normalize("https://x6.example.org/s-%s/all.yaml" % ("Z" * 43))
        self.assertTrue(url.endswith("/all.vpn"))
        self.assertIn("Clash", note)


# ── 5. words ───────────────────────────────────────────────────────────────

class WordsTest(CliGwCase):
    def test_collect_help_says_once(self):
        from kaskad.gw import cli as gcli
        self.assertIn("для разового запуска — --once", gcli.COLLECT_HELP)
        rc, out, err = self.run_cli("gw", "collect", "--help")
        self.assertIn("--once", out)
        self.assertIn("разового запуска", " ".join(out.split()))

    def test_where_has_the_port_for_everyone(self):
        rows = exits.set_where([{"kind": "awg", "version": "1.5", "host": "203.0.113.121:51820"},
                                {"kind": "awg", "version": "2.0", "host": "m6.example.org:8635"},
                                {"kind": "vless", "host": "198.51.100.20:443"}])
        self.assertEqual([r["where"] for r in rows], ["AmneziaWG 1.5 · 203.0.113.121:51820",
                                                      "AmneziaWG 2.0 · m6.example.org:8635",
                                                      "VLESS · 198.51.100.20:443"])
        self.assertTrue(all(re.search(r":\d+$", r["where"]) for r in rows))


# ── the panel: «Вернуть исключённые», the domain of a certificate, the limit of a copy, the old name of a button ──

class PanelTest(GwCase):
    def test_the_mock_excludes_and_restores(self):
        from tests.test_panel_js_contract import STATIC, node
        out = node(r"""
globalThis.window = globalThis;
globalThis.location = new URL("http://127.0.0.1/index.html?mock=1&lat=0");
require(DATA.mock);
const call = async (m, p, body) => { const r = await window.KASKAD_MOCK.fetch(p, { method: m, headers: { "X-Kaskad-Admin": "1" },
  body: body ? JSON.stringify(body) : undefined }); return [r.status, await r.json()]; };
const out = {};
let sub = (await call("GET", "/api/gw/exits"))[1].subs[0];
out.before = [sub.excluded, /исключено вручную: 1/.test(sub.text)];
await call("DELETE", "/api/gw/exits/e-0f0f0f0f0f?rev=" + (await call("GET", "/api/gw/exits"))[1].rev);   // SE §7.9: с rev
sub = (await call("GET", "/api/gw/exits"))[1].subs[0];
out.after = sub.excluded;
const r = (await call("POST", "/api/gw/exit-subs/" + sub.id + "/restore"))[1];
out.restore = [r.ok, r.restored, r.subs[0].excluded];
const bad = await call("POST", "/api/gw/exit-subs", { url: "https://203.0.113.5:8443/s-certdomain/all.vpn" });
out.cert = [bad[0], bad[1].data, JSON.stringify(bad[1]).indexOf("203.0.113.5") >= 0];
console.log(JSON.stringify(out));
process.exit(0);
""", {"mock": os.path.join(STATIC, "dev-mock.js")})
        self.assertEqual(out["before"], [1, True])
        self.assertEqual(out["after"], 2)
        self.assertEqual(out["restore"], [True, 2, 0])
        self.assertEqual(out["cert"], [400, {"cert_domain": "sub.example.net", "same_server": True}, False])

    def test_app_js(self):
        from tests.test_panel_js_contract import STATIC
        with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertNotIn("gdanger", js)                                    # the old name of «dsoft» is gone
        self.assertIn('restore: (id) => request("POST", "/api/gw/exit-subs/" + enc(id) + "/restore", {})', js)
        self.assertIn("subUrlWithHost(url, dom)", js)
        self.assertIn("e.data.same_server === true", js)
        self.assertIn("const BACKUP_BODY_MAX = 20 * 1024 * 1024;", js)


# ── 6. several addresses on the node, stage 0 (k3/SPEC-multi-ip.md) ───────

class MultiIpTest(GwCase):
    def test_openvpn_udp_answers_from_the_address_it_was_asked_on(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "fixtures", "gw", "tunnel", "golden", "openvpn-udp.conf")) as f:
            self.assertIn("\nexplicit-exit-notify 1\nmultihome\n", f.read())       # the server config of UDP
        with open(os.path.join(here, "fixtures", "gw", "tunnel", "golden", "openvpn-tcp.conf")) as f:
            self.assertNotIn("multihome", f.read())                               # TCP needs none

    def test_a_build_without_pktinfo_is_told(self):
        from kaskad.gw.inbounds import openvpn
        b = self.path("/usr/sbin/openvpn")
        os.makedirs(os.path.dirname(b), exist_ok=True)
        with open(b, "wb") as f:
            f.write(b"\x7fELF" + b"x" * (3 << 20) + b"OpenVPN 2.6.12 x86_64 [SSL (OpenSSL)] [MH/PKTINFO] [AEAD]")
        self.assertTrue(openvpn.multihome_supported(b))        # the mark beyond the first megabyte is found
        with open(b, "wb") as f:
            f.write(b"\x7fELF OpenVPN 2.6.12 [SSL (OpenSSL)] [AEAD]")
        self.assertFalse(openvpn.multihome_supported(b))
        self.assertIsNone(openvpn.multihome_supported(self.path("/nothing")))
        self.assertIn("только с основного адреса", openvpn.MH_WARNING)

    def test_the_addresses_of_the_open_mode(self):
        from kaskad.panel import conf
        self.w.ip.links["eth0"]["addrs"].append("203.0.113.77/32")
        self.w.ip.add_link("kgw-wg", "wireguard", ["10.77.1.1/24"])       # the gateway's own: never
        self.w.ip.add_link("docker0", "bridge", ["172.17.0.1/16"])
        self.assertEqual(conf.listen_addresses(self.w.runner, "203.0.113.10"), ["203.0.113.10", "203.0.113.77"])
        self.assertEqual(conf.listen_addresses(self.w.runner, "127.0.0.1"), ["127.0.0.1"])   # bind elsewhere
        self.assertEqual(conf.listen_addresses(self.w.runner, ""), [])


class PanelAddressesTest(PanelCase):
    def test_one_listener_per_address_with_the_same_checks(self):
        from kaskad.panel import conf
        with mock.patch.object(conf, "listen_addresses", lambda runner, bind: [bind, "::1"]):
            port = self.public_on()
            keys = sorted(k for k in self.panel.servers if k.startswith("public"))
            self.assertEqual(keys, ["public", "public@::1"], self.panel.errors)
            self.assertEqual(self.panel.servers["public@::1"][0].kind, "public")    # the same checks as on bind
            self.assertIn("[::1]:%d" % port, [x.get("addr") for x in conf.read_status()["listen"]])
            self.assertIn("::1", self.panel.servers["public"][0].names)
            with mock.patch.object(conf, "listen_addresses", lambda runner, bind: [bind]):   # the address went away
                self.assertTrue(self.panel.refresh_addresses())
            self.assertEqual(sorted(k for k in self.panel.servers if k.startswith("public")), ["public"])
            cfg = conf.load()
            cfg["public"]["on"] = False
            conf.save(cfg)
            self.panel.reload()
            self.assertEqual([k for k in self.panel.servers if k.startswith("public")], [])


# ── 7. the design system by handoff v2 ─────────────────────────────────────

class DesignV2Test(GwCase):
    def read_static(self, name):
        from tests.test_panel_js_contract import STATIC
        with open(os.path.join(STATIC, name), encoding="utf-8") as f:
            return f.read()

    def test_one_palette_the_one_of_v2(self):
        css = self.read_static("tokens.css")
        dark = css[css.index('[data-hero][data-theme="dark"] {'):]
        dark = dark[:dark.index("}")]
        light = css[css.index('[data-hero][data-theme="light"] {'):]
        light = light[:light.index("}")]
        for v in ("--background: oklch(.15 .012 255)", "--surface: oklch(.205 .015 255)", "--border: oklch(.31 .016 255)",
                  "--danger-strong: oklch(.55 .2 25)"):                    # 3.3.3: белый текст ≥ 4,5 (было .6 — 4,36)
            self.assertIn(v, dark)
        for v in ("--neutral-hue: 288", "--background: oklch(.972 .004 var(--neutral-hue))", "--danger-strong"):
            self.assertIn(v, light)
        self.assertNotIn("oklch(.13 .005 286)", css)                              # the palette of v1 is gone
        self.assertIn("background: var(--danger-strong, var(--danger))", self.read_static("components.css"))

    def test_the_default_accent_is_the_one_of_the_wizard(self):
        from tests.test_panel_js_contract import STATIC, node
        out = node(r"""
const noop = () => {};
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem: noop };
globalThis.matchMedia = (q) => ({ matches: /dark/.test(q) });
const props = {};
globalThis.document = { documentElement: { setAttribute: noop, style: { setProperty: (k, v) => { props[k] = v; }, removeProperty: noop } } };
require(DATA.boot);
const L = window.KaskadLook;
const out = { first: props["--accent"], dark: L.accents("dark", 0).auto, light: L.accents("light", 0).auto,
  blue: L.accents("dark", 0).ga, rd: L.tones(L.accents("dark", 0).auto, "dark").ratio,
  rl: L.tones(L.accents("light", 0).auto, "light").ratio };
console.log(JSON.stringify(out));
process.exit(0);
""", {"boot": os.path.join(STATIC, "boot.js")})
        self.assertEqual((out["dark"], out["light"]), ("#45D0C0", "oklch(0.53 0.17 288)"))
        self.assertEqual(out["first"], "#45D0C0")                                  # the first frame, dark system theme
        self.assertEqual(out["blue"], "oklch(0.54 0.13 222)")                      # the blue «Каскад» stays a theme
        self.assertGreaterEqual(out["rd"], 4.5)                                    # text on the accent by contrast
        self.assertGreaterEqual(out["rl"], 4.5)
        js = self.read_static("app.js")
        self.assertIn('const LOOK_DEFAULTS = { theme: "dark", accent: "auto",', js)            # 3.3.2: dark by default
        self.assertIn('ga: { name: "lk.pKaskadBlue", accent: "ga"', js)
        from kaskad.panel import api
        self.assertEqual(api.APPEARANCE_DEFAULTS["accent"], "auto")
        self.assertIn("ga", api.APPEARANCE_ENUMS["accent"])                       # a saved choice stays valid

    def test_tiles_delete_with_a_trash_and_a_confirmation_in_place(self):
        js = self.read_static("app.js")
        self.assertIn("if (c.del) ics.push(U.trash(c.del.name, (b) => U.askDelete(b.closest(\".gx-tc__btns\"), c.del)));", js)
        self.assertIn('row.replaceChildren(el("span", { class: "gx-confirm__q", text: q, title: q }), no, yes);', js)
        # этап 1 (T8, SE §7.3): «Удалить ключ» у плитки выхода — кнопка над объектом, Kit.confirmAction со своим ?dry=1
        self.assertIn('icons: bare ? [] : (exitOwn(r.id) ? [U.btn("", "tertiary", () => renameExit(r), { icon: "pencil", tip: t("ex.rename"), aria: t("ex.renameName", { name: r.name }) })] : []).concat(exitDelAct(r)),', js)
        self.assertIn("dry: () => API.exits.del(r.id, null, true), run: (rev) => API.exits.del(r.id, rev),", js)
        self.assertIn('U.trash(r.title, (b) => U.askDelete(b.closest(".gx-pbtns")', js)          # protocols
        self.assertIn('return [main].concat(mid, el("span", { class: "gx-spacer" }), trash);', js)  # VLESS too (k3-vless2)
        # people (3.3.3 UX-333 §5: «Поделиться ▾» и «⋯»): «Удалить человека» — в «⋯»; с устройствами — сразу окно с именем
        # (T-M6-qs330b №11), без них — подтверждение
        self.assertIn('if (u.devices.length) delUser(u.id); else if (await confirmDialog({ title: t("pp.delUser")', js)
        i18n = self.read_static("i18n-ru.js")
        self.assertIn('"c.delAsk": "Удалить «{name}»?"', i18n)

    def test_the_density_is_three_cards_of_a_radiogroup(self):
        js = self.read_static("app.js")
        self.assertIn('role: "radiogroup", "aria-label": t("lk.density")', js)
        self.assertIn('role: "radio", "aria-checked": String(on)', js)
        self.assertIn('el("small", { class: "gx-dens__d", text: t(descKey) })', js)
        self.assertIn('look.density === "detailed" ? { k: t("site.maskAddr")', js)              # the preview: rows


class AppearanceMigrationTest(PanelCase):
    """3.3.0: a «Вид» saved before with the old default blue gets the new default once; the rest stays."""

    def save_old(self, ap, v=None):
        from kaskad import util as kutil
        from kaskad.panel import conf
        doc = {"schema": 1, "appearance": ap}
        if v is not None:
            doc["v"] = v
        os.makedirs(os.path.dirname(conf.appearance_path()), exist_ok=True)
        kutil.atomic_write(conf.appearance_path(), json.dumps(doc), 0o600)

    def test_the_server_migrates_only_the_old_blue(self):
        from kaskad.panel import api, conf
        self.save_old(dict(api.APPEARANCE_DEFAULTS, accent="ga", theme="dark", density="min"))
        ap = self.ok("GET", "/api/appearance")["appearance"]
        self.assertEqual((ap["accent"], ap["theme"], ap["density"]), ("auto", "dark", "min"))
        self.save_old(dict(api.APPEARANCE_DEFAULTS, accent="coral", tint="warm"))
        ap = self.ok("GET", "/api/appearance")["appearance"]
        self.assertEqual((ap["accent"], ap["tint"]), ("coral", "warm"))          # another accent — untouched
        # the blue chosen now stays: PUT writes the mark of the version
        self.ok("PUT", "/api/appearance", {"accent": "ga"})
        with open(conf.appearance_path(), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["v"], api.APPEARANCE_V)
        self.assertEqual(self.ok("GET", "/api/appearance")["appearance"]["accent"], "ga")
        self.save_old(dict(api.APPEARANCE_DEFAULTS, accent="ga"), v=2)
        self.assertEqual(self.ok("GET", "/api/appearance")["appearance"]["accent"], "ga")

    def test_the_browser_migrates_once(self):
        from tests.test_panel_js_contract import STATIC, node
        out = node(r"""
const noop = () => {};
const run = (stored) => {
  globalThis.window = globalThis;
  globalThis.localStorage = { getItem: () => stored, setItem: noop };
  globalThis.matchMedia = (q) => ({ matches: /dark/.test(q) });
  const props = {};
  globalThis.document = { documentElement: { setAttribute: noop, style: { setProperty: (k, v) => { props[k] = v; }, removeProperty: noop } } };
  delete require.cache[require.resolve(DATA.boot)];
  require(DATA.boot);
  return props["--accent"];
};
console.log(JSON.stringify({ old: run(JSON.stringify({ theme: "dark", accent: "ga" })),
  mine: run(JSON.stringify({ theme: "dark", accent: "ga", v: 2 })), coral: run(JSON.stringify({ theme: "dark", accent: "coral" })) }));
process.exit(0);
""", {"boot": os.path.join(STATIC, "boot.js")})
        self.assertEqual(out["old"], "#45D0C0")                     # the old default blue — the new default
        self.assertEqual(out["mine"], "oklch(0.54 0.13 222)")       # the blue chosen after 3.3.0 stays
        self.assertEqual(out["coral"], "oklch(0.68 0.17 38)")
        with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn('if (v < 2 && out.accent === "ga") out = Object.assign({}, out, { accent: "auto" });', js)
        self.assertIn("let look = normLook(migrateLook(", js)
        self.assertIn("lsSet(LOOK_KEY, lookStored());", js)

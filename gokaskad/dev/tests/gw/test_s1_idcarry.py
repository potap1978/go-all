"""Stage 1, T11 (SPEC-stage1-exits §3.8 «Что добавляет этап 1», §9.1): a subscription that renames a key of the new
schemes keeps its exit through the mechanism of 3.3.5 (no change of ``plan``): exits.peer_key of hy2/ss/trojan/vmess on
stdlib — ``plan`` gets «renamed», the id of the exit and the place of its line stay; another password, uuid or server —
a new exit. The stores of stage 1 (the policy, exit_pref, the routes) do not change on a rename. A second Hysteria2 key at
the address of another one is left out with «dup_endpoint» (against both files of the pool); the line a renamed key
replaces is no duplicate. peer_key/identity never go to subs.json in clear."""

import base64
import json
import urllib.parse

from kaskad import state
from kaskad.gw import exits, exitsubs, policy
from tests.fakes_gw import VLESS_B, write_pool
from tests.test_335_rename import RenameCase
from tests.test_gw_exitsubs import IP_URL

REAL_NOW, REAL_SUBS_NOW = exits._now, exitsubs._now        # before any case patches them (TrailingDotTest)


def b64(lines):
    return base64.b64encode("\n".join(lines).encode()).decode()


def ss_b64(cred):
    return base64.urlsafe_b64encode(cred.encode()).decode().rstrip("=")


def vmess(ps, uid="bbbbbbbb-2222-3333-4444-555555555555", add="198.51.100.44", path="/vm"):
    j = {"v": "2", "ps": ps, "add": add, "port": "443", "id": uid, "aid": "0", "scy": "auto", "net": "ws",
         "host": "vm.example.com", "path": path, "tls": "tls", "sni": "vm.example.com"}
    return "vmess://" + base64.b64encode(json.dumps(j).encode()).decode()


def q(name):
    return urllib.parse.quote(name)


# protocol -> (key(name), the same key with other parameters, another credential, another server)
KEYS = {
    "hy2": (lambda n: "hy2://authOne@198.51.100.41:443?sni=hy.example.com&obfs=salamander&obfs-password=o1#" + q(n),
            lambda n: "hysteria2://authOne@198.51.100.41:443/?sni=hy2.example.com&alpn=h3#" + q(n),
            lambda n: "hy2://authTwo@198.51.100.41:8443?sni=hy.example.com#" + q(n),
            lambda n: "hy2://authOne@198.51.100.51:443?sni=hy.example.com#" + q(n)),
    "ss": (lambda n: "ss://" + ss_b64("chacha20-ietf-poly1305:ssOne") + "@198.51.100.42:8388#" + q(n),
           lambda n: "ss://" + base64.b64encode(b"chacha20-ietf-poly1305:ssOne@198.51.100.42:8388").decode() + "#" + q(n),
           lambda n: "ss://" + ss_b64("chacha20-ietf-poly1305:ssTwo") + "@198.51.100.42:8388#" + q(n),
           lambda n: "ss://" + ss_b64("chacha20-ietf-poly1305:ssOne") + "@198.51.100.52:8388#" + q(n)),
    "trojan": (lambda n: "trojan://tjOne@198.51.100.43:443?security=tls&sni=tj.example.com#" + q(n),
               lambda n: "trojan://tjOne@198.51.100.43:443?security=tls&sni=tj.example.com&type=ws&path=%2Fx#" + q(n),
               lambda n: "trojan://tjTwo@198.51.100.43:443?security=tls&sni=tj.example.com#" + q(n),
               lambda n: "trojan://tjOne@198.51.100.53:443?security=tls&sni=tj.example.com#" + q(n)),
    "vmess": (lambda n: vmess(n), lambda n: vmess(n, path="/other"),
              lambda n: vmess(n, uid="cccccccc-2222-3333-4444-555555555555"), lambda n: vmess(n, add="198.51.100.54")),
}


class RenameEveryProtocolTest(RenameCase):
    def check(self, proto):
        key, other_params, other_cred, other_server = KEYS[proto]
        self.add(IP_URL, b64([key("10GB left"), VLESS_B]))
        eid = self.ids()[0]
        self.route_all(eid)
        before = self.routes()
        for name in ("9GB left", "8GB left"):
            self.feed.bodies[IP_URL] = b64([key(name), VLESS_B])
            r = self.refresh()["results"][0]
            self.assertEqual((r["added"], r["removed"], r["updated"]), (0, 0, 1), (proto, r["text"]))
            self.assertEqual(self.pool(), [key(name), VLESS_B])
            self.assertEqual(self.ids()[0], eid, proto)
            self.assertEqual(self.routes(), before, proto)
        self.feed.bodies[IP_URL] = b64([other_params("7GB left"), VLESS_B])     # other parameters: the same exit
        self.refresh()
        self.assertEqual(self.ids()[0], eid, proto)
        for new in (other_cred, other_server):                                   # another credential or server: new
            self.feed.bodies[IP_URL] = b64([new("x"), VLESS_B])
            self.refresh()
            self.assertNotIn(eid, self.ids(), proto)
            self.assertIn(exits.exit_id(new("x")), self.ids(), proto)
        doc = json.dumps(exitsubs.load(), ensure_ascii=False)
        for secret in ("authOne", "ssOne", "tjOne", "bbbbbbbb-2222"):
            self.assertNotIn(secret, doc)                                         # only 16 hex of identity

    def test_hy2(self):
        self.check("hy2")

    def test_ss(self):
        self.check("ss")

    def test_trojan(self):
        self.check("trojan")

    def test_vmess(self):
        self.check("vmess")

    def test_ss_sip002_and_old_form_are_one_exit(self):
        key, old_form = KEYS["ss"][0], KEYS["ss"][1]
        self.add(IP_URL, b64([key("A")]))
        eid = self.ids()[0]
        self.feed.bodies[IP_URL] = b64([old_form("A")])
        r = self.refresh()["results"][0]
        self.assertEqual((r["added"], r["removed"], r["updated"]), (0, 0, 1), r["text"])
        self.assertEqual(self.ids(), [eid])


class StoresGuardTest(RenameCase):
    def test_a_rename_changes_no_store_of_stage_1(self):
        """The policy (group, tier), exit_pref (manual standby, quota, speed_skip), the routes and the name — by the id
        of the exit: a rename changes none of them."""
        key = KEYS["trojan"][0]
        self.add(IP_URL, b64([key("10GB"), VLESS_B]))
        eid = self.ids()[0]
        self.route_all(eid, enabled=True)
        with state.transaction() as st:
            g = st["gateway"]
            g["exit_policy"] = {"version": policy.VERSION, "tg_proxy_group": "main",
                                "groups": [policy.default_group("main", exits=[eid])]}
            g["exits"][eid].update(manual="standby", speed_skip=True, quota={"gb": 5, "then": "standby"})
        from kaskad.gw import rev
        rev.current(state.load())       # the edit above went past Python: counted now (T7: a write of a subscription
        #                                 counts it too, §3.1) — the rename itself changes nothing
        snap = json.dumps(state.load()["gateway"], sort_keys=True)
        self.feed.bodies[IP_URL] = b64([key("9GB"), VLESS_B])
        self.refresh()
        self.assertEqual(self.ids()[0], eid)
        self.assertEqual(json.dumps(state.load()["gateway"], sort_keys=True), snap)


class DupEndpointTest(RenameCase):
    A = "hy2://authA@198.51.100.61:443?sni=hy.example.com#A"
    B = "hy2://authB@198.51.100.61:443?sni=hy.example.com#B"

    def test_second_hy2_at_one_address_is_left_out(self):
        self.add(IP_URL, b64([self.A, self.B, VLESS_B]))
        self.assertEqual(self.pool(), [self.A, VLESS_B])
        sub = exitsubs.load()["subs"][0]
        self.assertIn("dup_endpoint", sub.get("skip_codes") or [])

    def test_against_the_other_file_of_the_pool(self):
        from kaskad.gw import rev
        with open(rev.ru_pool_path(), "w") as f:
            f.write(self.A + "\n")
        self.add(IP_URL, b64([self.B, VLESS_B]))
        self.assertEqual(self.pool(), [VLESS_B])

    def test_the_line_a_rename_replaces_is_no_duplicate(self):
        self.add(IP_URL, b64([self.A]))
        eid = self.ids()[0]
        self.feed.bodies[IP_URL] = b64([self.A.replace("#A", "#A%20renamed")])
        r = self.refresh()["results"][0]
        self.assertEqual((r["updated"], r["skipped"]), (1, 0), r["text"])
        self.assertEqual(self.ids(), [eid])

    def test_provider_gives_a_new_password_at_the_same_address(self):
        """The old key leaves the pool in the same update — the new one is no duplicate of it."""
        self.add(IP_URL, b64([self.A]))
        self.feed.bodies[IP_URL] = b64([self.B])
        r = self.refresh()["results"][0]
        self.assertEqual(self.pool(), [self.B], r["text"])


def _at(proto, host, name="n"):
    """A key of ``proto`` at ``host`` (domain keys: the dot at the end is about domains)."""
    return {"hy2": "hy2://authOne@%s:443?sni=hy.example.com#%s" % (host, name),
            "ss": "ss://" + ss_b64("chacha20-ietf-poly1305:ssOne") + "@%s:8388#%s" % (host, name),
            "trojan": "trojan://tjOne@%s:443?security=tls&sni=tj.example.com#%s" % (host, name),
            "vmess": vmess(name, add=host),
            "vless": "vless://%s@%s:443?security=tls&sni=v.example.com&type=tcp#%s"
                     % ("dddddddd-2222-3333-4444-555555555555", host, name)}[proto]


class TrailingDotTest(RenameCase):
    """REV-s1-rel-3 З-1: the core takes the host of a key as the parser 3.3.6 does — one dot at the end (and «。», «．»
    after IDNA) is dropped, an empty label is no host. Else the menu (egress_parse.py peers) calls «srv.example.com.»
    a repeat, while the panel and the core add it: two legs with one account, and a subscription that writes «h.»
    instead of «h» changes the id of the exit."""
    HOSTS = ("srv.example.com.", "SRV.example.com。", "srv.example.com．")

    @classmethod
    def tearDownClass(cls):
        # the subtests set the case up anew: nothing of theirs may outlive the class (the review of the core pack, 1)
        super().tearDownClass()
        assert exits._now is REAL_NOW and exitsubs._now is REAL_SUBS_NOW, "a patch of the case leaked"

    def test_peer_key_is_the_one_of_the_bare_host_and_of_the_parser(self):
        from tests.test_334_xray import parser
        p = parser()
        for h in self.HOSTS:                       # VLESS: urlsplit on both sides, the same string (no change here)
            self.assertEqual(p.peer_key(_at("vless", h)), exits.peer_key(_at("vless", h)), h)
        for proto in ("hy2", "ss", "trojan", "vmess"):
            bare = exits.peer_key(_at(proto, "srv.example.com"))
            self.assertIn("srv.example.com:", bare, proto)
            for h in self.HOSTS:
                line = _at(proto, h)
                self.assertEqual(exits.peer_key(line), bare, (proto, h))
                self.assertEqual(p.peer_key(line), exits.peer_key(line), (proto, h))
            self.assertEqual(exits.peer_key(_at(proto, "198.51.100.44.")), exits.peer_key(_at(proto, "198.51.100.44")),
                             proto)

    def test_an_empty_label_is_no_host(self):
        # «..» alone and between labels, its forms after IDNA / NFKC too (。 ． ｡ ﹒ — the review of the core pack, 5)
        for h in ("srv..example.com", "srv.example.com..", ".", "..", "local..host", "local\u3002\u3002host",
                  "local\uff0e\uff0ehost", "local\uff61\uff61host", "local\ufe52\ufe52host"):
            for proto in ("hy2", "ss", "trojan", "vmess"):
                self.assertEqual(exits.new_peer(_at(proto, h)), "", (proto, h))
            self.assertIsNone(exits._host(h), h)
        self.assertIsNone(exits._host("::1."))
        self.assertEqual(exits._host("local\ufe52host"), "local.host")          # one such dot is a dot

    def test_a_subscription_that_adds_the_dot_keeps_the_exit(self):
        for proto in ("hy2", "trojan"):
            with self.subTest(proto=proto):
                # a whole cycle, not a second setUp over the first: GwCase keeps its patches in a list a second setUp
                # overwrites — the first ones would never stop and leak into every later module of «k3_test.sh -j1»
                self.tearDown()
                self.doCleanups()
                self.setUp()
                self.add(IP_URL, b64([_at(proto, "srv.example.com", "A"), VLESS_B]))
                eid = self.ids()[0]
                self.feed.bodies[IP_URL] = b64([_at(proto, "srv.example.com.", "A"), VLESS_B])
                r = self.refresh()["results"][0]
                self.assertEqual((r["added"], r["removed"]), (0, 0), (proto, r["text"]))
                self.assertEqual(self.ids()[0], eid, proto)

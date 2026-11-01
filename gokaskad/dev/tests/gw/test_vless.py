"""VLESS Reality inbound (docs/k3/SPEC-vless-inbound.md): keys and UUIDs, what it gives the gateway xray (inbound with
Reality, routing by user), the cover site and its check, UFW, sessions from xray's counters, issuance by platform."""

import base64
import json
import os
from urllib.parse import parse_qs, unquote, urlsplit

from kaskad import paths
from tests.gw.tunnel_case import GwCase, device, gw_state

VLESS = {"vless": {"enabled": True, "port": 8443, "sni": "www.microsoft.com", "dest": "www.microsoft.com:443",
                   "mask": "list"}}
# RFC 7748 §6.1: Alice's private key and its public key
ALICE = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
ALICE_PUB = bytes.fromhex("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")


def b64url(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def devices():
    return [
        device("d-pc", "Компьютер", "windows", ["vless"], kind="pc"),                        # user u-1: default pool
        device("d-guest", "Гость", "android", ["vless"], user="u-2"),                        # user u-2: direct
        device("d-vienna", "Через Вену", "ios", ["vless"], route={"via": "exit:e-3fa9c1d2b4", "fail": "block"}),
        device("d-off", "Выключен", "linux", ["vless"], enabled=False),
        device("d-wg", "Без VLESS", "ios", ["wg"]),
    ]


def ok_check(host, port=443, timeout=5.0):
    return {"ok": True, "tls": "TLSv1.3", "alpn": "h2", "h2": True, "why": ""}


class NoShuffle(object):
    """The order of a list as it is (pick_mask shuffles it on a node)."""

    def shuffle(self, seq):
        return None


class VlessCase(GwCase):
    def setUp(self):
        super().setUp()
        self.st = gw_state(VLESS, devices())
        self.vl = self.adapter("vless")
        self.mod = self.gw.vless
        self.checks = []
        self._old_checker = self.mod._checker["fn"]
        self.mod._checker["fn"] = self.fake_check
        self.addCleanup(self.mod._checker.__setitem__, "fn", self._old_checker)
        self.answer = {}                         # host -> check result; missing — TLS 1.3 with HTTP/2
        self.country = {"code": "", "source": ""}
        lists = self.lists = self.mod.vless_lists
        old_geo, old_rng = lists._geo["fn"], self.mod._rng
        lists._geo["fn"] = lambda st=None: dict(self.country)
        self.mod._rng = NoShuffle()
        self.addCleanup(lists._geo.__setitem__, "fn", old_geo)
        self.addCleanup(setattr, self.mod, "_rng", old_rng)

    def fake_check(self, host, port=443, timeout=5.0):
        self.checks.append(host)
        return dict(self.answer.get(host) or ok_check(host))

    def dev(self, dev_id):
        return [d for d in self.st["gateway"]["devices"] if d["id"] == dev_id][0]

    @staticmethod
    def slurp(path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def cfg(self):
        return self.st["gateway"]["inbounds"]["vless"]


class KeysTest(VlessCase):
    def test_reality_keys_are_x25519_in_base64url(self):
        self.assertEqual(self.mod.public_of(b64url(ALICE)), b64url(ALICE_PUB))
        priv = self.mod.new_private()
        raw = base64.urlsafe_b64decode(priv + "=")
        self.assertEqual((len(priv), len(raw)), (43, 32))
        self.assertNotIn("=", priv)
        self.assertEqual((raw[0] & 7, raw[31] & 128, raw[31] & 64), (0, 0, 64))      # clamped as `xray x25519`
        self.assertNotEqual(priv, self.mod.new_private())
        self.assertRegex(self.mod.new_short_id(), r"^[0-9a-f]{16}$")
        with self.assertRaises(self.gw.base.InboundError):
            self.mod.public_of("not-a-key")

    def test_prepare_makes_the_node_key_short_id_and_a_uuid_per_device(self):
        done = self.vl.prepare(self.st, self.sec)
        self.assertEqual(done, ["ключ узла VLESS", "shortId", "UUID VLESS d-guest", "UUID VLESS d-pc",
                                "UUID VLESS d-vienna"])
        priv = self.sec["vless.server.private"]
        self.assertEqual(self.cfg()["pub"], self.mod.public_of(priv))
        self.assertRegex(self.cfg()["short_id"], r"^[0-9a-f]{16}$")
        self.assertEqual((self.cfg()["fp"], self.cfg()["mask"]), ("chrome", "list"))
        uuids = sorted(k for k in self.sec if k.startswith("dev."))
        self.assertEqual(uuids, ["dev.d-guest.vless.uuid", "dev.d-pc.vless.uuid", "dev.d-vienna.vless.uuid"])
        self.assertEqual(len(set(self.sec[k] for k in uuids)), 3)                     # each device its own
        for k in uuids:
            self.assertRegex(self.sec[k], r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
        before = (dict(self.sec), dict(self.cfg()))
        self.assertEqual(self.vl.prepare(self.st, self.sec), [])                      # idempotent
        self.assertEqual((dict(self.sec), dict(self.cfg())), before)
        self.assertNotIn(priv, json.dumps(self.st))                                   # the private key: secrets only

    def test_rekey_changes_the_node_key_and_short_id_not_the_uuids(self):
        self.vl.prepare(self.st, self.sec)
        old = (self.sec["vless.server.private"], self.cfg()["pub"], self.cfg()["short_id"], self.sec["dev.d-pc.vless.uuid"])
        self.assertEqual(self.vl.rekey(self.st, self.sec), ["ключ узла VLESS", "shortId"])
        new = (self.sec["vless.server.private"], self.cfg()["pub"], self.cfg()["short_id"], self.sec["dev.d-pc.vless.uuid"])
        self.assertTrue(all(a != b for a, b in zip(old[:3], new[:3])))
        self.assertEqual(old[3], new[3])
        self.assertEqual(new[1], self.mod.public_of(new[0]))

    def test_revoke_and_reset_touch_only_its_own_uuid(self):
        self.assertEqual(self.vl.secret_prefixes("d-pc"), ["dev.d-pc.vless."])
        self.assertEqual(self.vl.reset_secrets("d-pc"), ["dev.d-pc.vless."])


class XrayTest(VlessCase):
    def test_inbound_with_reality_and_a_client_per_device(self):
        self.vl.prepare(self.st, self.sec)
        (ib,) = self.vl.xray_inbounds(self.st, self.sec)
        self.assertEqual((ib["tag"], ib["listen"], ib["port"], ib["protocol"]), ("gw-vless", "0.0.0.0", 8443, "vless"))
        self.assertNotIn("sniffing", ib)                         # IPOnDemand checks the real destination only
        self.assertEqual(ib["settings"]["decryption"], "none")
        self.assertEqual([c["email"] for c in ib["settings"]["clients"]], ["dev-guest", "dev-pc", "dev-vienna"])
        for c in ib["settings"]["clients"]:
            self.assertEqual(c["flow"], "xtls-rprx-vision")
            self.assertEqual(c["id"], self.sec["dev.d-%s.vless.uuid" % c["email"][4:]])
        ss = ib["streamSettings"]
        self.assertEqual((ss["network"], ss["security"]), ("tcp", "reality"))
        self.assertEqual(ss["realitySettings"], {"show": False, "dest": "www.microsoft.com:443", "xver": 0,
                                                 "serverNames": ["www.microsoft.com"],
                                                 "privateKey": self.sec["vless.server.private"],
                                                 "shortIds": [self.cfg()["short_id"]]})

    def test_routing_by_the_route_of_each_device(self):
        self.vl.prepare(self.st, self.sec)
        got = self.vl.xray_routing(self.st, self.sec)
        tags = [r.pop("ruleTag") for r in got]
        self.assertEqual(got, [                       # one chain per user (sorted by login), no closing rule
            {"type": "field", "inboundTag": ["gw-vless"], "user": ["dev-guest"], "outboundTag": "direct"},
            {"type": "field", "inboundTag": ["gw-vless"], "user": ["dev-pc"], "balancerTag": "pool"},
            {"type": "field", "inboundTag": ["gw-vless"], "user": ["dev-vienna"], "outboundTag": "exit-e-3fa9c1d2b4"},
        ])
        self.assertRegex(tags[0], r"^vl-dev-guest-[0-9a-f]{10}-0$")
        self.assertEqual(len(set(tags)), 3)
        self.assertEqual([r["ruleTag"] for r in self.vl.xray_routing(self.st, self.sec)], tags)    # stable
        self.dev("d-pc")["route"] = {"via": "direct", "fail": "block"}                           # another route: another tag
        again = [r["ruleTag"] for r in self.vl.xray_routing(self.st, self.sec)]
        self.assertEqual((again[0], again[2]), (tags[0], tags[2]))
        self.assertNotEqual(again[1], tags[1])
        seen = []
        custom = self.vl.xray_routing(self.st, self.sec, target=lambda r, d: seen.append((r, d["id"])) or {"outboundTag": "x-" + r["via"]})
        self.assertEqual(custom[0]["outboundTag"], "x-direct")
        self.assertIn(("exit:e-3fa9c1d2b4", "block", "d-vienna"), [(r["via"], r["fail"], d) for r, d in seen])   # the fail
        # policy and the device go along (stage 1: the target of a device is its pin, §2.4)

    def test_off_empty_or_without_keys_gives_nothing(self):
        self.assertEqual(self.vl.xray_inbounds(self.st, self.sec), [])          # no keys, no UUIDs yet
        self.vl.prepare(self.st, self.sec)
        for d in self.st["gateway"]["devices"]:                                 # no device: the inbound stays (the
            d["enabled"] = False                                                # first one comes without a restart)
        (ib,) = self.vl.xray_inbounds(self.st, self.sec)
        self.assertEqual((ib["settings"]["clients"], self.vl.xray_routing(self.st, self.sec)), ([], []))
        for d in self.st["gateway"]["devices"]:
            d["enabled"] = True
        self.cfg()["enabled"] = False
        self.assertEqual((self.vl.xray_inbounds(self.st, self.sec), self.vl.xray_routing(self.st, self.sec)), ([], []))
        self.cfg()["enabled"] = True
        self.st["gateway"]["enabled"] = False
        self.assertEqual(self.vl.xray_inbounds(self.st, self.sec), [])

    def test_a_disabled_device_or_person_leaves_xray(self):
        self.vl.prepare(self.st, self.sec)
        self.dev("d-pc")["enabled"] = False
        self.st["gateway"]["users"][1]["enabled"] = False                       # u-2: d-guest
        (ib,) = self.vl.xray_inbounds(self.st, self.sec)
        self.assertEqual([c["email"] for c in ib["settings"]["clients"]], ["dev-vienna"])


class MaskTest(VlessCase):
    def test_pick_takes_the_first_site_that_answers_tls13(self):
        self.answer["www.microsoft.com"] = {"ok": False, "tls": "", "alpn": "", "h2": False, "why": "сайт недоступен"}
        self.answer["dl.google.com"] = {"ok": True, "tls": "TLSv1.3", "alpn": "http/1.1", "h2": False, "why": ""}
        rec = self.mod.pick_mask(self.st)                     # TLS 1.3 with HTTP/2 first, TLS 1.3 alone is kept aside
        self.assertEqual((rec["sni"], rec["dest"], rec["mask"]), ("www.apple.com", "www.apple.com:443", "list"))
        self.assertTrue(rec["mask_check"]["ok"])
        self.assertEqual(self.checks, ["www.microsoft.com", "dl.google.com", "www.apple.com"])
        self.assertEqual((rec["mask_list"], rec["country"]), ("int", {"code": "", "source": ""}))
        for s in self.lists.INT[:self.mod.PICK_TRIES]:
            self.answer[s] = dict(self.answer.get("dl.google.com"))
        self.answer["www.microsoft.com"] = self.answer["dl.google.com"]
        self.assertEqual(self.mod.pick_mask(self.st)["sni"], "www.microsoft.com")   # no HTTP/2 anywhere: TLS 1.3

    def test_the_list_follows_the_country_of_the_server(self):
        self.country = {"code": "RU", "source": "ipinfo"}
        rec = self.mod.pick_mask(self.st)
        self.assertEqual((rec["sni"], rec["mask_list"], rec["country"]["code"]), ("ya.ru", "ru", "RU"))
        rows = self.mod.masks(self.st)
        self.assertEqual([r["sni"] for r in rows], list(self.lists.RU))
        self.country = {"code": "DE", "source": "ip-api"}
        self.assertEqual(self.mod.pick_mask(self.st)["sni"], "www.microsoft.com")
        self.cfg()["country"] = {"code": "RU", "source": "local"}                 # the record keeps the country
        self.assertEqual(self.mod.country_of(self.st), {"code": "RU", "source": "local"})
        self.assertEqual(self.mod.pick_mask(self.st)["mask_list"], "ru")
        self.mod._rng = __import__("random").Random(7)                            # on a node: a random order
        picks = set()
        for seed in range(6):
            self.mod._rng.seed(seed)
            picks.add(self.mod.pick_mask(self.st)["sni"])
        self.assertGreater(len(picks), 1)
        self.assertTrue(picks <= set(self.lists.RU))

    def test_detect_asks_ipinfo_then_ip_api_then_the_local_ranges(self):
        lists = self.lists
        asked = []

        def get(url, timeout):
            asked.append(url)
            if "ipinfo" in url:
                raise OSError("timeout")
            return "FI\n"
        old_get, old_root = lists._get, os.environ.pop("KASKAD_ROOT", None)
        lists._get = get
        try:
            st = {"gateway": {"host": "45.10.20.30"}}
            self.assertEqual(lists.detect(st), {"code": "FI", "source": "ip-api"})
            self.assertEqual(asked, ["https://ipinfo.io/45.10.20.30/country",
                                     "http://ip-api.com/line/45.10.20.30?fields=countryCode"])
            lists._get = lambda url, timeout: "<html>"                         # garbage — not a country
            self.assertEqual(lists.detect({"gateway": {"host": "vpn.example.com"}}), {"code": "", "source": ""})
        finally:
            lists._get = old_get
            if old_root is not None:
                os.environ["KASKAD_ROOT"] = old_root
        lists._get = lambda url, timeout: self.fail("no network under KASKAD_ROOT")
        self.addCleanup(setattr, lists, "_get", old_get)
        self.assertEqual(lists.detect({"gateway": {"host": "45.10.20.30"}}), {"code": "", "source": ""})
        self.assertEqual(lists.list_of("ru"), ("ru", lists.RU))
        self.assertEqual(lists.list_of(""), ("int", lists.INT))

    def test_nothing_answers_the_first_one_with_its_failed_check(self):
        for s in self.lists.INT:
            self.answer[s] = {"ok": False, "tls": "", "alpn": "", "h2": False, "why": "нет сети"}
        rec = self.mod.pick_mask(self.st)
        self.assertEqual((rec["sni"], rec["mask_check"]["ok"]), ("www.microsoft.com", False))
        self.assertEqual(len(self.checks), self.mod.PICK_TRIES)                   # not the whole list
        fields, notes = self.vl.pre_install(None, self.st)
        self.assertIn("не ответил по TLS 1.3 (нет сети)", notes[0])
        with self.assertRaises(self.gw.base.InboundError):
            self.mod.mask_fields(self.st, "auto")

    def test_the_own_site_of_the_node_first(self):
        self.st["site"] = {"domain": "vpn.example.com", "mode": "site"}
        self.assertEqual(self.vl.port_candidates(self.st), [8443])                # 443 is the site's
        rows = self.mod.masks(self.st)
        self.assertEqual((rows[0]["sni"], rows[0]["mask"]), ("vpn.example.com", "own"))
        rec = self.mod.pick_mask(self.st)
        self.assertEqual((rec["sni"], rec["dest"], rec["mask"]), ("vpn.example.com", "vpn.example.com:443", "own"))
        self.assertEqual(self.mod.mask_fields(self.st, "own")["sni"], "vpn.example.com")
        self.st["site"]["mode"] = "domain"                                        # no site on 443: not «own»
        self.assertEqual(self.vl.port_candidates(self.st), [443, 8443])
        with self.assertRaises(self.gw.base.InboundError):
            self.mod.mask_fields(self.st, "own")

    def test_a_site_of_the_list_a_custom_one_and_refusals(self):
        self.assertEqual(self.mod.mask_fields(self.st, "VK.com")["mask"], "list")
        rec = self.mod.mask_fields(self.st, "https://cdn.example.org/")
        self.assertEqual((rec["sni"], rec["mask"]), ("cdn.example.org", "custom"))
        for bad in ("10.0.0.1", "local", "a b.com", "-x.com", ""):
            with self.assertRaises(self.gw.base.InboundError, msg=bad):
                self.mod.mask_fields(self.st, bad)
        self.answer["old.example.org"] = {"ok": False, "tls": "TLSv1.2", "alpn": "", "h2": False,
                                          "why": "сайт ответил TLSv1.2, а нужен TLS 1.3"}
        with self.assertRaises(self.gw.base.InboundError) as e:
            self.mod.mask_fields(self.st, "old.example.org")
        self.assertIn("TLS 1.3", str(e.exception))
        rec = self.mod.mask_fields(self.st, "old.example.org", check=False)            # the person insists
        self.assertEqual((rec["sni"], rec["mask_check"]["ok"]), ("old.example.org", None))

    def test_the_real_check_says_why_without_network(self):
        got = self._old_checker("127.0.0.1", port=1, timeout=0.5)                 # nobody listens there
        self.assertFalse(got["ok"])
        self.assertIn("недоступен", got["why"])


class ApplyTest(VlessCase):
    def test_apply_opens_tcp_only_and_status(self):
        self.node.ufw_active = True
        self.vl.install(self.runner)
        res = self.vl.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "xray")
        self.assertEqual(self.node.ufw, ["allow 8443/tcp comment kaskad-gw vless"])
        info = json.loads(self.slurp(self.vl.state_path()))
        self.assertEqual(info, {"port": 8443, "sni": "www.microsoft.com", "accounts": 3, "security": "reality",
                                "users": {"dev-guest": "d-guest", "dev-pc": "d-pc", "dev-vienna": "d-vienna"}})
        self.assert_no_secrets(self.slurp(self.vl.state_path()))
        self.assertEqual(oct(os.stat(self.vl.state_path()).st_mode & 0o777), "0o600")
        st = self.vl.status(self.runner)                                          # xray not running
        self.assertEqual((st["installed"], st["running"], st["port"]), (True, False, 8443))
        self.cfg()["enabled"] = False
        self.assertEqual(self.vl.apply(self.st, self.sec, self.runner)["action"], "off")
        self.assertEqual(self.node.ufw[-1], "delete allow 8443/tcp")
        self.vl.uninstall(self.runner)
        self.assertFalse(self.vl.status(self.runner)["installed"])

    def test_idle_without_devices_is_not_broken(self):
        st = gw_state(VLESS, [])
        self.vl.apply(st, self.sec, self.runner)
        # by the fact (T-M6-qs330b №1): idle is no breakage, but «работает» only while xray of the gateway runs
        s = self.vl.status(self.runner)
        self.assertEqual((s["running"], s.get("idle")), (False, True))
        self.assertIn("xray шлюза не работает", s["warnings"][0])
        self.assertIn("пока нет", s["warnings"][-1])
        self.node.sysd.u("kaskad-gw-xray.service")["active"] = True
        s = self.vl.status(self.runner)
        self.assertEqual((s["running"], s.get("idle")), (True, True))
        self.assertEqual(len(s["warnings"]), 1)
        self.assertIn("пока нет", s["warnings"][0])


class SessionsTest(VlessCase):
    def setUp(self):
        super().setUp()
        self.vl.apply(self.st, self.sec, self.runner)
        self.node.sysd.u("kaskad-gw-xray.service")["active"] = True
        self.xray = os.path.join(paths.opt(), "gw", "xray")
        self.write(self.xray[len(self.root):], "#!/bin/sh\n", 0o755)
        self.stats = {"dev-pc": (100, 2000), "dev-guest": (0, 0), "socks-user": (5, 5)}
        self.answers = 0
        self.runner._handlers.insert(0, ([self.xray, "api", "statsquery"], self._stats))     # before the catch-all
        self.now = 1791190000.0
        self.mod._now = lambda: self.now
        self.addCleanup(setattr, self.mod, "_now", __import__("time").time)

    def _stats(self, argv, inp):
        self.answers += 1
        self.assertIn("--server=127.0.0.1:%d" % self.mod.STATS_PORT, argv)
        stat = []
        for lg, (up, down) in self.stats.items():
            if up:
                stat.append({"name": "user>>>%s>>>traffic>>>uplink" % lg, "value": str(up)})
            if down:
                stat.append({"name": "user>>>%s>>>traffic>>>downlink" % lg, "value": down})
        return 0, json.dumps({"stat": stat}), ""

    def test_growing_counters_make_a_device_online(self):
        s = self.vl.status(self.runner)
        self.assertEqual(s["sessions"], [{"device": "d-pc", "ip": None, "since": 1791190000, "last": 1791190000,
                                          "rx": 100, "tx": 2000}])       # last — the last growth (3.3.7.15 С-1)
        self.assertEqual((s["rx"], s["tx"]), (100, 2000))                        # SOCKS5 users are not counted
        self.now += 60
        s = self.vl.status(self.runner)                                          # no growth for 60 s: still online
        self.assertEqual([x["device"] for x in s["sessions"]], ["d-pc"])
        self.now += 100                                                          # 160 s without growth: gone
        self.assertEqual(self.vl.status(self.runner)["sessions"], [])
        self.stats["dev-pc"] = (150, 2500)                                       # back: a new «since»
        self.now += 10
        s = self.vl.status(self.runner)
        self.assertEqual(s["sessions"][0]["since"], int(self.now))
        self.stats["dev-guest"] = (10, 10)
        self.now += 10
        s = self.vl.status(self.runner)
        self.assertEqual(sorted(x["device"] for x in s["sessions"]), ["d-guest", "d-pc"])
        by = dict((x["device"], x) for x in s["sessions"])
        self.assertEqual((by["d-pc"]["since"], by["d-guest"]["since"]), (int(self.now) - 10, int(self.now)))
        kept = json.loads(self.slurp(self.vl.traffic_path()))
        self.assertNotIn("socks-user", kept)
        self.assertEqual(oct(os.stat(self.vl.traffic_path()).st_mode & 0o777), "0o600")

    def test_a_restart_of_xray_zeroes_the_counters(self):
        self.vl.status(self.runner)
        self.now += 300
        self.assertEqual(self.vl.status(self.runner)["sessions"], [])
        self.stats["dev-pc"] = (10, 20)                                          # smaller: xray restarted, traffic
        s = self.vl.status(self.runner)
        self.assertEqual([(x["device"], x["rx"]) for x in s["sessions"]], [("d-pc", 10)])

    def test_xray_silent_or_down_means_no_sessions(self):
        self.runner._handlers.insert(0, ([self.xray, "api", "statsquery"], lambda a, i: (1, "", "connection refused")))
        self.assertEqual(self.vl.status(self.runner)["sessions"], [])
        self.node.sysd.u("kaskad-gw-xray.service")["active"] = False
        s = self.vl.status(self.runner)
        self.assertEqual((s["running"], s["sessions"]), (False, []))


class IssueTest(VlessCase):
    def setUp(self):
        super().setUp()
        self.vl.prepare(self.st, self.sec)

    def parse(self, url):
        u = urlsplit(url)
        return u, dict((k, v[0]) for k, v in parse_qs(u.query).items())

    def test_link_qr_and_fields(self):
        res = self.vl.issue(self.st, self.sec, self.dev("d-pc"), None)              # Windows: the link first
        self.assertEqual([i["format"] for i in res["items"]], ["link", "qr", "fields"])
        url = res["body"]
        u, q = self.parse(url)
        self.assertEqual((u.scheme, u.username, u.hostname, u.port),
                         ("vless", self.sec["dev.d-pc.vless.uuid"], "203.0.113.10", 8443))
        self.assertEqual(q, {"encryption": "none", "flow": "xtls-rprx-vision", "security": "reality",
                             "sni": "www.microsoft.com", "fp": "chrome", "pbk": self.cfg()["pub"],
                             "sid": self.cfg()["short_id"], "type": "tcp", "headerType": "none"})
        self.assertEqual(unquote(u.fragment), "goKaskad PRO · VLESS · Компьютер")   # brand · protocol · device (№9)
        self.assertEqual(res["items"][1]["body"], [url])
        fields = dict(res["items"][2]["body"])
        self.assertEqual((fields["UUID"], fields["SNI"], fields["shortId (sid)"]),
                         (self.sec["dev.d-pc.vless.uuid"], "www.microsoft.com", self.cfg()["short_id"]))
        self.assertIn("Hiddify", " ".join(res["notes"]))
        text = json.dumps(res, ensure_ascii=False)
        self.assertNotIn(self.sec["vless.server.private"], text)                  # the node's key never leaves
        self.assertNotIn(self.sec["dev.d-guest.vless.uuid"], text)                # nor another device's UUID

    def test_phones_get_the_qr_first_and_their_apps(self):
        res = self.vl.issue(self.st, self.sec, self.dev("d-vienna"), None)          # iOS
        self.assertEqual(res["format"], "qr")
        self.assertIn("Karing", " ".join(res["items"][1]["notes"]))         # in the Russian App Store (2026-10-11)
        self.assertNotIn("Streisand", " ".join(res["items"][1]["notes"]))   # not in the Russian App Store
        self.assertEqual(self.vl.issue(self.st, self.sec, self.dev("d-pc"), "android")["format"], "qr")

    def test_a_name_host_in_the_link(self):
        self.st["gateway"]["host"] = "vpn.example.com"
        u, _ = self.parse(self.vl.issue(self.st, self.sec, self.dev("d-pc"), None)["body"])
        self.assertEqual(u.hostname, "vpn.example.com")

    def test_refusals(self):
        for dev_id in ("d-off", "d-wg"):
            with self.assertRaises(self.gw.base.InboundError):
                self.vl.issue(self.st, self.sec, self.dev(dev_id), None)


class HowtoTest(VlessCase):
    def test_steps_by_platform_and_screen(self):
        h = self.gw.issue_os.howto
        ios = h("ios", "vless")
        self.assertIn("Karing", ios[0])                          # Streisand and Hiddify are not in the Russian App Store
        self.assertIn("QR", " ".join(ios))
        page = h("ios", "vless", where="page")                     # the page on the phone itself: no QR to scan
        self.assertIn("«Скопировать»", page[1])
        self.assertIn("импортируйте ссылку из буфера обмена", page[3])
        self.assertNotIn("Наведите камеру", " ".join(page))
        win = h("windows", "vless", where="panel")
        self.assertIn("v2rayN", win[0])                          # Hiddify stands still since 03.2026
        self.assertIn("«Скопировать»", win[1])
        self.assertIn("импортируйте ссылку из буфера обмена", win[2])
        self.assertEqual(h("keenetic", "vless"), [])                # routers: no client of ours
        self.assertEqual(self.gw.issue_os.vless_apps("android")[:2], ["v2RayTun", "Happ"])
        self.assertEqual(self.gw.issue_os.vless_apps_note("mikrotik"), "")
        self.assertEqual(self.gw.issue_os.TITLES["vless"], "VLESS")

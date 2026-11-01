"""SOCKS5 inbound: what it gives the gateway xray (inbound, routing by user), UFW, issuance links."""

import json
from urllib.parse import unquote

from tests.gw.tunnel_case import GwCase, device, gw_state

SOCKS = {"socks5": {"enabled": True, "port": 1080, "udp": True}}


def devices():
    return [
        device("d-pc", "Компьютер", "windows", ["socks5"], kind="pc"),                       # user u-1: default pool
        device("d-guest", "Гость", "android", ["socks5"], user="u-2"),                       # user u-2: direct
        device("d-vienna", "Через Вену", "linux", ["socks5"], route={"via": "exit:e-3fa9c1d2b4", "fail": "block"}),
        device("d-off", "Выключен", "linux", ["socks5"], enabled=False),
        device("d-wg", "Без SOCKS", "ios", ["wg"]),
    ]


class SocksCase(GwCase):
    def setUp(self):
        super().setUp()
        self.st = gw_state(SOCKS, devices())
        self.s5 = self.adapter("socks5")

    def dev(self, dev_id):
        return [d for d in self.st["gateway"]["devices"] if d["id"] == dev_id][0]


class XrayTest(SocksCase):
    def test_inbound_and_routing(self):
        self.assertEqual(self.s5.prepare(self.st, self.sec),
                         ["пароль SOCKS5 d-guest", "пароль SOCKS5 d-pc", "пароль SOCKS5 d-vienna"])
        self.assertEqual(sorted(k for k in self.sec if k.startswith("dev.")),
                         ["dev.d-guest.socks5.password", "dev.d-pc.socks5.password", "dev.d-vienna.socks5.password"])
        inb = self.s5.xray_inbounds(self.st, self.sec)
        self.golden("socks5-xray-inbounds.json", json.dumps(inb, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
        self.assertEqual([a["user"] for a in inb[0]["settings"]["accounts"]], ["dev-guest", "dev-pc", "dev-vienna"])
        rules = self.s5.xray_routing(self.st, self.sec)
        tags = [r.pop("ruleTag") for r in rules]
        self.assertEqual(rules, [            # stage 1 (§2.4): one chain per account, by login; no closing «block»
            {"type": "field", "inboundTag": ["gw-socks5"], "user": ["dev-guest"], "outboundTag": "direct"},
            {"type": "field", "inboundTag": ["gw-socks5"], "user": ["dev-pc"], "balancerTag": "pool"},
            {"type": "field", "inboundTag": ["gw-socks5"], "user": ["dev-vienna"], "outboundTag": "exit-e-3fa9c1d2b4"},
        ])
        self.assertRegex(tags[0], r"^sk-dev-guest-[0-9a-f]{10}-0$")
        self.assertEqual([r["ruleTag"] for r in self.s5.xray_routing(self.st, self.sec)], tags)     # stable
        seen = []
        custom = self.s5.xray_routing(self.st, self.sec,
                                      target=lambda r, d: seen.append(d["id"]) or {"outboundTag": "via-" + r["via"]})
        self.assertEqual(custom[0]["outboundTag"], "via-direct")
        self.assertEqual(sorted(set(seen)), ["d-guest", "d-pc", "d-vienna"])       # the device goes along
        self.assertEqual((self.s5.xray_hot, self.s5.xray_hot_prefix, self.s5.xray_add_user), (True, "sk-", None))

    def test_off_or_empty_gives_nothing(self):
        self.assertEqual(self.s5.xray_inbounds(self.st, self.sec), [])          # no passwords yet
        self.s5.prepare(self.st, self.sec)
        self.st["gateway"]["inbounds"]["socks5"]["enabled"] = False
        self.assertEqual((self.s5.xray_inbounds(self.st, self.sec), self.s5.xray_routing(self.st, self.sec)), ([], []))

    def test_route_target(self):
        t = self.gw.socks5.route_target
        self.assertEqual(t(None), {"balancerTag": "pool"})
        self.assertEqual(t("block"), {"outboundTag": "block"})
        self.assertEqual(t({"via": "exit:"}), {"outboundTag": "block"})


class SecretsTest(SocksCase):
    def test_revoke_and_reset_touch_only_its_own_password(self):
        self.assertEqual(self.s5.secret_prefixes("d-pc"), ["dev.d-pc.socks5."])
        self.assertEqual(self.s5.reset_secrets("d-pc"), ["dev.d-pc.socks5."])


class ApplyTest(SocksCase):
    def test_apply_opens_tcp_and_udp(self):
        self.node.ufw_active = True
        self.s5.install(self.runner)
        res = self.s5.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "xray")
        self.assertEqual(self.node.ufw, ["allow 1080/tcp comment kaskad-gw socks5", "allow 1080/udp comment kaskad-gw socks5"])
        self.st["gateway"]["inbounds"]["socks5"]["udp"] = False
        self.s5.apply(self.st, self.sec, self.runner)
        self.assertIn("delete allow 1080/udp", self.node.ufw)
        self.node.sysd.u("kaskad-gw-xray.service")["active"] = True
        st = self.s5.status(self.runner)
        self.assertEqual((st["installed"], st["running"], st["port"]), (True, True, 1080))
        self.s5.uninstall(self.runner)
        self.assertEqual(self.node.ufw[-1], "delete allow 1080/tcp")
        self.assertFalse(self.s5.status(self.runner)["installed"])


class IssueTest(SocksCase):
    def test_links(self):
        self.s5.prepare(self.st, self.sec)
        self.sec["dev.d-pc.socks5.password"] = "p@ss:w/rd"
        self.sec["dev.d-pc.password"] = "SharedOfL2tpIke"                       # never through SOCKS5
        res = self.s5.issue(self.st, self.sec, self.dev("d-pc"), None)
        self.assertNotIn("SharedOfL2tpIke", json.dumps(res))
        self.assertIn("Пароль SOCKS5 — свой", " ".join(res["items"][2]["notes"]))
        self.assertEqual([i["format"] for i in res["items"]], ["link", "link", "fields"])
        self.assertEqual(res["body"], "socks5://dev-pc:p%40ss%3Aw%2Frd@203.0.113.10:1080")
        tg = res["items"][1]["body"]
        self.assertTrue(tg.startswith("tg://socks?server=203.0.113.10&port=1080&user=dev-pc&pass="))
        self.assertEqual(unquote(tg.rsplit("pass=", 1)[1]), "p@ss:w/rd")
        self.assertIn("https://t.me/socks?server=203.0.113.10", res["items"][1]["notes"][1])
        res = self.s5.issue(self.st, self.sec, self.dev("d-guest"), None)       # a phone: Telegram link first
        self.assertTrue(res["body"].startswith("tg://socks?"))

    def test_refusals(self):
        self.s5.prepare(self.st, self.sec)
        for dev_id in ("d-off", "d-wg"):
            with self.assertRaises(self.gw.base.InboundError):
                self.s5.issue(self.st, self.sec, self.dev(dev_id), None)

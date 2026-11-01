"""F2 (3.3.7.15, PLAN-n13 Ш2, В5): MTU вручную у WireGuard и AmneziaWG 1.5/2.0/3.1 — предел min(1400, карта) − 80 −
S4 (``mtu.manual_check``), применение на ходу без перезапуска (``ops._set_mtu`` в одном применении F3), зажим MSS по
новому серверному MTU, выдача (WG .conf, AWG vpn:// и строка amneziawg:// подписки) несёт новый MTU."""

import base64
import copy

from kaskad.errors import USAGE, KaskadError
from kaskad.gw import mtu as gwmtu, ops
from kaskad.gw.inbounds import tunnel
from kaskad.panel import userpage
from kaskad.vendor import awgkeys
from tests.gw.test_awg import AwgCase
from tests.gw.test_wg import WgCase
from tests.helpers import RootCase


def world(card=1500, **inbounds):
    st = {"gateway": {"mtu": {"nic": {"dev": "eth0", "mtu": card}}, "inbounds": {}}}
    for kind, s4 in inbounds.items():
        rec = {"enabled": True, "port": 40000}
        if kind != "wg":
            rec["line"] = {"awg1": "1.5", "awg2": "2.0", "awg3": "3.1"}[kind]
            rec["params"] = {"S4": str(s4), "MTU": "1280"}
        st["gateway"]["inbounds"][kind] = rec
    return st


class ManualCheckTest(RootCase):
    def ok(self, st, kind, n):
        return gwmtu.manual_check(st, kind, n)

    def no(self, st, kind, n):
        before = copy.deepcopy(st)
        with self.assertRaises(KaskadError, msg=(kind, n)) as cm:
            gwmtu.manual_check(st, kind, n)
        self.assertEqual(cm.exception.code, USAGE)
        self.assertEqual(st, before)                                        # the state is not touched
        return str(cm.exception)

    def test_wireguard_on_a_card_of_1500(self):
        st = world(wg=0)
        res = self.ok(st, "wg", 1320)
        self.assertEqual((res["ok"], res["limit"], res["server"], res["warnings"]), (True, 1320, 1312, []))
        self.assertEqual(res["outer"], 1400)
        text = self.no(st, "wg", 1321)
        self.assertIn("1400", text)
        self.assertIn("не поднимется выше 1312", text)
        self.no(st, "wg", 1420)

    def test_the_edges(self):
        self.assertIn("от 1280 до 1320", self.no(world(wg=0), "wg", 1279))
        for bad in (True, "1300", 1300.0, None):
            self.no(world(wg=0), "wg", bad)
        self.ok(world(awg2=40), "awg2", 1280)
        self.no(world(awg2=40), "awg2", 1281)
        self.ok(world(awg2=15), "awg2", 1305)
        self.no(world(awg2=15), "awg2", 1306)
        self.ok(world(awg3=12), "awg3", 1308)
        self.no(world(awg3=12), "awg3", 1309)
        self.ok(world(awg1=15), "awg1", 1305)
        self.assertEqual(self.ok(world(1380, wg=0), "wg", 1300)["limit"], 1300)
        self.no(world(1380, wg=0), "wg", 1301)
        self.assertIn("WireGuard и AmneziaWG", self.no(world(wg=0), "openvpn", 1300))

    def test_a_limit_below_1280_closes_the_field(self):
        st = world(awg2=60)
        v = gwmtu.manual_view(st, "awg2")
        self.assertEqual((v["open"], v["limit"], v["min"]), (False, 1260, 1280))
        self.assertIn("S4 = 60", v["why"])
        self.assertEqual(self.no(st, "awg2", 1280), v["why"])
        # a value below 1280 put by «новые настройки с меньшим MTU» is shown as it is
        st = world(1300, wg=0)
        st["gateway"]["inbounds"]["wg"]["mtu"] = 1216
        v = gwmtu.manual_view(st, "wg")
        self.assertEqual((v["cur"], v["open"], v["limit"]), (1216, False, 1220))


class WgLiveTest(WgCase):
    def test_the_mtu_goes_onto_the_live_interface_and_the_mss_follows(self):
        self.st["gateway"]["mtu"] = {"nic": {"dev": "eth0", "mtu": 1500}}
        self.seed()
        self.installed()
        self.wg.apply(self.st, self.sec, self.runner)
        self.node.sysd.calls[:] = []
        ops._set_mtu(self.st, "wg", 1300)
        self.assertEqual(self.st["gateway"]["inbounds"]["wg"]["mtu"], 1300)
        res = self.wg.apply(self.st, self.sec, self.runner)
        self.assertEqual((res["action"], res["changed"]), ("mtu", ["env"]))
        self.assertNotIn("restart", self.node.sysd.verbs("kaskad-gw-wg.service"))
        self.assertIn(["link", "set", "dev", "kgw-wg", "mtu", "1300"], [c[1:] for c in self.node.commands()
                                                                       if c[0].endswith("ip")])
        self.assertIsNone(tunnel.last_restart())
        mss = "\n".join(gwmtu.mss_rules(self.st, {"wg": ["kgw-wg"]}))
        self.assertIn('iifname "kgw-wg" tcp flags syn tcp option maxseg size > 1260 tcp option maxseg size set 1260', mss)
        conf = self.wg.issue(self.st, self.sec, self.dev("d-phone"), "linux")["items"]
        conf = [i["body"] for i in conf if i["format"] == "conf"][0]
        self.assertIn("MTU = 1300", conf)


class AwgLiveTest(AwgCase):
    def setUp(self):
        super().setUp()
        self.seed()
        self.st["gateway"]["mtu"] = {"nic": {"dev": "eth0", "mtu": 1500}}

    def test_awg3_live_and_its_issue(self):
        self.core("/etc/kaskad/egress/awg")
        a = self.adapter("awg3")
        a.install(self.runner)
        a.apply(self.st, self.sec, self.runner)
        self.node.sysd.calls[:] = []
        ops._set_mtu(self.st, "awg3", 1300)
        self.assertEqual(self.st["gateway"]["inbounds"]["awg3"]["params"]["MTU"], "1300")
        res = a.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "mtu")                              # not «restarted»: MTU is no server param
        self.assertNotIn("restart", self.node.sysd.verbs("kaskad-gw-awg3.service"))
        self.assertIn(["link", "set", "dev", "kgw-awg3", "mtu", "1296"], [c[1:] for c in self.node.commands()
                                                                         if c[0].endswith("ip")])
        got = a.issue(self.st, self.sec, self.dev("d-phone"), "android")
        self.assertEqual(str(awgkeys.vpn_decode(got["body"])["params"]["MTU"]), "1300")
        conf = [i["body"] for i in got["items"] if i["format"] == "conf"][0]
        self.assertIn("MTU = 1300", conf)
        line = userpage.amneziawg_link(conf, "Телефон")                      # the line of the subscription
        body = line[len("amneziawg://"):].split("#")[0]
        self.assertIn("MTU = 1300", base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8"))

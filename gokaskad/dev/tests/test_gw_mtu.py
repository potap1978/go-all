"""Безопасный MTU (k3/SPEC-awg-engine.md §6.1, kaskad/gw/mtu.py): формулы по таблице проекта
(k3/stand/logs/design-awg-mtu.md §1), серверный MTU для карт 1500/1450/1400/1380/1300 и S4 15/60/120/150, ноги,
карта сервера, план в state, MSS по интерфейсам, узкая карта (предупреждение, «новые настройки», «оставить»),
ноги в gw apply, egress_parse.py считает ноги так же."""

import base64
import json
import os

from kaskad import state
from kaskad.gw import exits
from kaskad.gw import mtu as gwmtu
from kaskad.sysrun import FakeRunner
from kaskad.vendor import awgkeys
from tests.fakes_gw import VLESS_A, awg_line, install_parser, write_pool
from tests.helpers import RootCase
from tests.test_gw_ops import CliGwCase

CARDS = (1500, 1450, 1400, 1380, 1300)
S4S = (15, 60, 120, 150)

# server MTU = min(1280, max(1024, floor16(min(1400, card) − 80 − S4))) — TCP first: below 1232 when it must
SERVER = {
    1500: {0: 1280, 12: 1280, 15: 1280, 60: 1248, 120: 1200, 150: 1168},
    1450: {0: 1280, 12: 1280, 15: 1280, 60: 1248, 120: 1200, 150: 1168},
    1400: {0: 1280, 12: 1280, 15: 1280, 60: 1248, 120: 1200, 150: 1168},
    1380: {0: 1280, 12: 1280, 15: 1280, 60: 1232, 120: 1168, 150: 1136},
    1300: {0: 1216, 12: 1200, 15: 1200, 60: 1152, 120: 1088, 150: 1056},
}
# what is offered to awg2 (2.0) with a client of 1280: s4 — the server is below 1232 and a new S4 fixes it (budget
# ≥ 1327); mtu — a narrow card; None — nothing
OFFER = {
    1500: {15: None, 60: None, 120: "s4", 150: "s4"},
    1450: {15: None, 60: None, 120: "s4", 150: "s4"},
    1400: {15: None, 60: "mtu", 120: "s4", 150: "s4"},
    1380: {15: None, 60: "mtu", 120: "s4", 150: "s4"},
    1300: {15: "mtu", 60: "mtu", 120: "mtu", 150: "mtu"},
}
# narrow: the card < 80 + S4 + 1280 (the clients' packets do not pass the card)
NARROW = {
    1500: {150},
    1450: {120, 150},
    1400: {60, 120, 150},
    1380: {60, 120, 150},
    1300: {0, 12, 15, 60, 120, 150},
}


def proc_route(rows):
    head = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
    return head + "".join("%s\t%s\t0102A8C0\t0003\t0\t0\t%d\t%s\t0\t0\t0\n" % r for r in rows)


class BytesWordTest(RootCase):
    def test_bytes_agree_with_the_number(self):
        # T-N5 3.2.20, Н-4: «нужно 1372 байт» — должно «1372 байта»
        from kaskad import util
        got = {n: util.n_bytes(n) for n in (1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 101, 111, 1360, 1372, 1415, 1421)}
        self.assertEqual(got, {1: "1 байт", 2: "2 байта", 4: "4 байта", 5: "5 байт", 11: "11 байт", 12: "12 байт",
                               14: "14 байт", 21: "21 байт", 22: "22 байта", 25: "25 байт", 101: "101 байт",
                               111: "111 байт", 1360: "1360 байт", 1372: "1372 байта", 1415: "1415 байт",
                               1421: "1421 байт"})

    def test_mtu_card_texts(self):
        # 3.1 при S4 = 12 и карте 1250: пакету 1372 байта; WG (S4 = 0): 1360 байт
        p31 = {"client": 1280, "s4": 12, "new_client": 1200}
        p_wg = {"client": 1280, "s4": 0, "new_client": 1200}
        self.assertIn("нужно 1372 байта (MTU клиента + 80 + S4)", gwmtu.offer_detail("awg3", p31, 1250))
        self.assertIn("пакету нужно 1372 байта: MTU клиента", gwmtu.narrow_text("awg3", p31, 1250))
        self.assertIn("нужно 1360 байт (MTU клиента", gwmtu.offer_detail("wg", p_wg, 1250))
        self.assertNotIn("1372 байт ", gwmtu.offer_detail("awg3", p31, 1250))


class FormulaTest(RootCase):
    def test_outer_packet_by_the_table_of_the_project(self):
        # design-awg-mtu.md §1, IPv6 column at M = 1280: WG and 1.x 1360, 2.0 (S4 = 120) 1480, 3.1 1372
        self.assertEqual(gwmtu.outer(1280, gwmtu.s4_of({})), 1360)
        self.assertEqual(gwmtu.outer(1280, gwmtu.s4_of({"S4": "120"})), 1480)
        self.assertEqual(gwmtu.outer(1280, gwmtu.s4_of({"S4": "12"})), 1372)
        self.assertEqual(gwmtu.outer(1280, gwmtu.s4_of({"S4": "150"})), 1510)
        self.assertEqual(gwmtu.OVERHEAD, 80)
        self.assertEqual(gwmtu.s4_of({"S4": ""}), 0)
        self.assertEqual(gwmtu.s4_of({"S4": "x"}), 0)

    def test_budget_and_s4_of_new_inbounds(self):
        for card, budget, s4 in ((1500, 1400, 40), (1450, 1400, 40), (1400, 1400, 40), (1380, 1380, 20),
                                 (1300, 1300, -60)):
            self.assertEqual(gwmtu.budget_of(card), budget)
            self.assertEqual(awgkeys.s4_max(budget), s4)
            p = awgkeys.gen_params("2.0", budget=budget)
            self.assertLessEqual(int(p["S4"]), max(15, s4))
        self.assertEqual(int(awgkeys.gen_params("3.1", budget=1300)["S4"]), 12)   # 3.1 keeps S4 = 12

    def test_server_mtu_for_cards_and_s4(self):
        for card in CARDS:
            for s4 in (0, 12) + S4S:
                with self.subTest(card=card, s4=s4):
                    srv = gwmtu.server_mtu(gwmtu.budget_of(card), s4, 1280)
                    self.assertEqual(srv, SERVER[card][s4])
                    self.assertEqual(srv % 16, 0)                   # the client pads to 16 by its own MTU
                    self.assertTrue(1024 <= srv <= 1280)
                    if srv >= 1232 or card > 1300:                 # the outer packet fits the budget
                        self.assertLessEqual(gwmtu.outer(srv, s4), gwmtu.budget_of(card))
                    self.assertEqual(gwmtu.narrow(card, s4, 1280), s4 in NARROW[card])
        self.assertEqual(gwmtu.server_mtu(1300, 300, 1280), 1024)    # the sanitary floor

    def test_offers_for_cards_and_s4(self):
        for card in CARDS:
            for s4 in S4S:
                with self.subTest(card=card, s4=s4):
                    st = st_with(card, {"S4": str(s4), "MTU": "1280"}, wg=False)
                    p = gwmtu.inbound_plan(st, "awg2")
                    self.assertEqual(p["offer"], OFFER[card][s4])
                    self.assertEqual(p["low"], SERVER[card][s4] < 1232)
                    if p["offer"] == "s4":                          # the new S4 brings the server back to ≥ 1232
                        new = gwmtu.server_mtu(gwmtu.budget_of(card), p["new_s4_max"], 1280)
                        self.assertGreaterEqual(new, 1232)
        # WireGuard and 1.x have no S4 of ours: below 1232 only on a narrow card, and the offer is the MTU
        st = st_with(1300)
        self.assertEqual((gwmtu.inbound_plan(st, "wg")["server"], gwmtu.inbound_plan(st, "wg")["offer"]), (1216, "mtu"))

    def test_server_never_above_the_client_and_follows_a_small_one(self):
        self.assertEqual(gwmtu.server_mtu(1400, 0, 1200), 1200)    # reissued to 1200: the server too
        self.assertEqual(gwmtu.server_mtu(1400, 0, 1420), 1312)    # a client 1420 (imported): the budget rules
        self.assertTrue(gwmtu.narrow(1500, 0, 1420) is False)
        self.assertTrue(gwmtu.narrow(1500, 30, 1420))

    def test_reissue_mtu(self):
        cases = {(1500, 150): 1232, (1450, 120): 1232, (1450, 150): 1216, (1400, 60): 1248, (1400, 120): 1200,
                 (1400, 150): 1200, (1380, 60): 1232, (1300, 0): 1216, (1300, 15): 1200, (1300, 120): 1200}
        for (card, s4), want in cases.items():
            with self.subTest(card=card, s4=s4):
                got = gwmtu.reissue_mtu(gwmtu.budget_of(card), card, s4)
                self.assertEqual(got, want)
                self.assertEqual(got % 16, 0)
        # after it the clients fit the card — except where even 1200 does not (S4 150 on 1400): the warning stays
        self.assertFalse(gwmtu.narrow(1450, 150, 1216))
        self.assertTrue(gwmtu.narrow(1400, 150, 1200))

    def test_legs(self):
        cases = {(1500, 0): 1280, (1500, 12): 1280, (1500, 120): 1280, (1500, 150): 1264, (1500, 200): 1232,
                 (1450, 15): 1280, (1450, 150): 1232, (1400, 0): 1280, (1300, 15): 1232,
                 (9000, 0): 1280}
        for (card, s4), want in cases.items():
            with self.subTest(card=card, s4=s4):
                self.assertEqual(gwmtu.leg_mtu(card, s4), want)

    def test_the_egress_parser_counts_legs_the_same(self):
        install_parser()
        parser = exits.load_parser()
        for card in CARDS + (9000, 1280):
            for s4 in (0, 12) + S4S + (200, 500):
                rec = {"params": {"S4": str(s4)} if s4 else {}}
                self.assertEqual(parser.leg_mtu(rec, card), gwmtu.leg_mtu(card, s4), (card, s4))


class NicTest(RootCase):
    def test_default_route_with_the_lowest_metric(self):
        self.write("/proc/net/route", proc_route([("ens4", "00000000", 200, "00000000"),
                                                  ("ens3", "00000000", 100, "00000000"),
                                                  ("ens3", "0002A8C0", 0, "00FFFFFF")]))
        self.write("/sys/class/net/ens3/mtu", "1450\n")
        self.write("/sys/class/net/ens4/mtu", "1500\n")
        self.assertEqual(gwmtu.read_nic(), {"dev": "ens3", "mtu": 1450, "read": True})

    def test_unreadable_is_1500(self):
        self.assertEqual(gwmtu.read_nic()["mtu"], 1500)
        self.write("/proc/net/route", proc_route([("eth0", "00000000", 0, "00000000")]))
        self.assertEqual(gwmtu.read_nic(), {"dev": "eth0", "mtu": 1500, "read": False})
        self.write("/sys/class/net/eth0/mtu", "garbage\n")
        self.assertFalse(gwmtu.read_nic()["read"])


def st_with(card, params=None, wg=True, keep=None, keep_s4=None):
    st = {"gateway": {"inbounds": {}}}
    if wg:
        st["gateway"]["inbounds"]["wg"] = {"enabled": True}
    if params is not None:
        st["gateway"]["inbounds"]["awg2"] = {"enabled": True, "line": "2.0", "params": params}
    st["gateway"]["mtu"] = {"nic": {"dev": "eth0", "mtu": card}}
    if keep:
        st["gateway"]["mtu"]["keep"] = keep
    if keep_s4:
        st["gateway"]["mtu"]["keep_s4"] = keep_s4
    return st


class PlanTest(RootCase):
    def test_refresh_writes_the_plan_and_logs_changes(self):
        st = st_with(1500, {"S4": "120", "MTU": "1280"})
        notes = gwmtu.refresh(st, {"dev": "eth0", "mtu": 1500})
        m = st["gateway"]["mtu"]
        self.assertEqual((m["nic"], m["budget"]), ({"dev": "eth0", "mtu": 1500}, 1400))
        self.assertEqual(m["inbounds"]["awg2"], {"server": 1200, "client": 1280, "s4": 120, "outer": 1400,
                                                 "narrow": False, "low": True, "offer": "s4", "new_s4_max": 40,
                                                 "kept_s4": False})
        self.assertEqual(m["inbounds"]["wg"]["server"], 1280)
        self.assertEqual(notes, [])
        notes = gwmtu.refresh(st, {"dev": "eth0", "mtu": 1300})              # the card got narrower
        self.assertEqual(notes, ["MTU сервера входа awg2: 1200 → 1088 (карта 1300, бюджет 1300, S4 = 120)",
                                 "MTU сервера входа wg: 1280 → 1216 (карта 1300, бюджет 1300, S4 = 0)"])
        self.assertTrue(m["inbounds"]["awg2"]["narrow"])
        self.assertEqual(m["inbounds"]["awg2"]["offer"], "mtu")
        self.assertEqual(m["inbounds"]["awg2"]["new_client"], 1200)

    def test_keep_s4_is_for_one_s4(self):
        st = st_with(1500, {"S4": "120", "MTU": "1280"}, keep_s4={"awg2": 120})
        gwmtu.refresh(st, {"dev": "eth0", "mtu": 1500})
        p = gwmtu.inbound_plan(st, "awg2")
        self.assertEqual((p["offer"], p["kept_s4"], p["low"]), (None, True, True))
        st["gateway"]["inbounds"]["awg2"]["params"]["S4"] = "130"         # another S4: ask again
        gwmtu.refresh(st, {"dev": "eth0", "mtu": 1500})
        self.assertNotIn("keep_s4", st["gateway"]["mtu"])
        self.assertEqual(gwmtu.inbound_plan(st, "awg2")["offer"], "s4")

    def test_keep_is_for_one_card(self):
        st = st_with(1400, {"S4": "60", "MTU": "1280"}, keep={"awg2": 1400, "wg": 1400, "awg3": 1400})
        gwmtu.refresh(st, {"dev": "eth0", "mtu": 1400})
        self.assertTrue(gwmtu.inbound_plan(st, "awg2")["kept"])
        self.assertEqual(st["gateway"]["mtu"]["keep"], {"awg2": 1400, "wg": 1400})    # awg3 is not installed
        gwmtu.refresh(st, {"dev": "eth0", "mtu": 1380})                     # another card: ask again
        self.assertNotIn("keep", st["gateway"]["mtu"])
        self.assertFalse(gwmtu.inbound_plan(st, "awg2")["kept"])

    def test_the_wg_client_mtu_is_stored_after_a_reissue(self):
        st = st_with(1300)
        self.assertEqual(gwmtu.client_mtu(st, "wg"), 1280)
        st["gateway"]["inbounds"]["wg"]["mtu"] = 1216
        self.assertEqual((gwmtu.client_mtu(st, "wg"), gwmtu.server_for(st, "wg")), (1216, 1216))
        self.assertFalse(gwmtu.inbound_plan(st, "wg")["narrow"])

    def test_view_and_status_lines(self):
        st = st_with(1400, {"S4": "60", "MTU": "1280"})
        st["gateway"]["mtu"]["legs"] = {"kawg2": {"mtu": 1280, "s4": 0}}
        v = gwmtu.view(st, {"dev": "eth0", "mtu": 1400, "read": True})
        self.assertEqual(v["ask"], ["awg2"])
        lines = gwmtu.status_lines(v)
        self.assertEqual(lines[0], "MTU: карта 1400 (eth0), бюджет 1400")
        self.assertIn("  вход wg: сервер 1280, клиент 1280", lines)
        self.assertIn("  вход awg2: сервер 1248, клиент 1280", lines)
        self.assertIn("  нога kawg2: 1280", lines)
        self.assertTrue(any("карта сервера 1400 уже, чем нужно клиентам с MTU 1280 при S4 = 60 (пакету нужно 1420 байт: MTU клиента "
                            "+ 80 + S4)" in l
                            for l in lines))
        self.assertIn("      новые настройки: kaskadctl gw mtu reissue awg2 · оставить: kaskadctl gw mtu keep awg2", lines)
        self.assertFalse(any("уже, чем нужно" in l for l in gwmtu.status_lines(v, with_text=False)))
        v2 = gwmtu.view(st, {"dev": "eth0", "mtu": 1500, "read": True})
        self.assertEqual(v2["nic_now"], 1500)
        self.assertIn("  ! карта сейчас 1500 — применится при kaskadctl gw apply", gwmtu.status_lines(v2))
        st["gateway"]["mtu"]["keep"] = {"awg2": 1400}
        v3 = gwmtu.view(st)
        self.assertEqual(v3["ask"], [])
        self.assertIn("    узкая карта: оставлено как есть (клиентам с MTU 1280 не уложиться)", gwmtu.status_lines(v3))


class MssTest(RootCase):
    def test_each_interface_both_ways_and_a_backstop_for_unknown_legs(self):
        st = st_with(1500, {"S4": "120", "MTU": "1280"})
        st["gateway"]["mtu"]["legs"] = {"kawg10": {"mtu": 1264}, "kawg2": {"mtu": 1280}}
        rules = gwmtu.mss_rules(st, {"wg": ["kgw-wg"], "awg2": ["kgw-awg2"]})
        clamp = "tcp flags syn tcp option maxseg size > %d tcp option maxseg size set %d"
        self.assertEqual(rules, [
            'iifname "kgw-wg" ' + clamp % (1240, 1240), 'oifname "kgw-wg" ' + clamp % (1240, 1240),
            'iifname "kgw-awg2" ' + clamp % (1160, 1160), 'oifname "kgw-awg2" ' + clamp % (1160, 1160),
            'iifname "kawg2" ' + clamp % (1240, 1240), 'oifname "kawg2" ' + clamp % (1240, 1240),
            'iifname "kawg10" ' + clamp % (1224, 1224), 'oifname "kawg10" ' + clamp % (1224, 1224),
            'iifname "kawg*" iifname != "kawg2" iifname != "kawg10" ' + clamp % (1192, 1192),
            'oifname "kawg*" oifname != "kawg2" oifname != "kawg10" ' + clamp % (1192, 1192)])

    def test_no_inbound_names_no_rules_for_them(self):
        st = st_with(1500, {"S4": "15", "MTU": "1280"})
        rules = gwmtu.mss_rules(st, {})
        self.assertEqual(len(rules), 2)                                  # only the backstop of the legs
        self.assertTrue(all('"kawg*"' in r for r in rules))


class LegsTest(RootCase):
    def test_sync_writes_the_env_and_the_live_interface(self):
        st = st_with(1500)
        self.write("/etc/kaskad/egress/awg/.kawg1.env", "ADDR=10.8.1.5/32\nTABLE=257\n")
        self.write("/etc/kaskad/egress/awg/.kawg2.env", "ADDR=10.8.2.5/32\nTABLE=258\nMTU=1264\n")
        self.write("/sys/class/net/kawg1/mtu", "1200\n")
        self.write("/sys/class/net/kawg2/mtu", "1264\n")
        r = FakeRunner()
        r.on(["ip", "link", "set"], lambda a, i: (0, "", ""))
        pool = [{"kind": "awg", "iface": "kawg1", "s4": 0}, {"kind": "awg", "iface": "kawg2", "s4": 150},
                {"kind": "vless", "iface": None}, {"kind": "awg", "iface": "kawg3; rm -rf /", "s4": 0}]
        notes = gwmtu.sync_legs(st, r, pool)
        self.assertEqual(self.read("/etc/kaskad/egress/awg/.kawg1.env"), "ADDR=10.8.1.5/32\nTABLE=257\nMTU=1280\n")
        self.assertEqual(self.read("/etc/kaskad/egress/awg/.kawg2.env"), "ADDR=10.8.2.5/32\nTABLE=258\nMTU=1264\n")
        self.assertEqual(oct(os.stat(self.path("/etc/kaskad/egress/awg/.kawg1.env")).st_mode & 0o777), "0o600")
        self.assertEqual(r.commands(["ip", "link", "set"]), [["ip", "link", "set", "dev", "kawg1", "mtu", "1280"]])
        self.assertEqual(notes, ["MTU ноги kawg1: 1200 → 1280"])
        self.assertEqual(st["gateway"]["mtu"]["legs"], {"kawg1": {"mtu": 1280, "s4": 0},
                                                        "kawg2": {"mtu": 1264, "s4": 150}})
        self.assertEqual(gwmtu.leg_mtus(st), {"kawg1": 1280, "kawg2": 1264})

    def test_the_pool_knows_the_s4_of_a_key(self):
        install_parser()
        parser = exits.load_parser()
        conf = ("[Interface]\nAddress = 10.8.1.5/32\nPrivateKey = %s\nJc = 4\nJmin = 40\nJmax = 70\nS1 = 50\n"
                "S2 = 60\nS3 = 20\nS4 = 150\nH1 = 100-200\nH2 = 300-400\nH3 = 500-600\nH4 = 700-800\n\n[Peer]\n"
                "PublicKey = %s\nAllowedIPs = 0.0.0.0/0\nEndpoint = 198.51.100.40:51820\n"
                % (base64.b64encode(b"a" * 32).decode(), base64.b64encode(b"b" * 32).decode()))
        line = "awg://" + parser._b64e(json.dumps(parser.awg_from_conf(conf, "S4")))
        write_pool([VLESS_A, awg_line(2), line])
        pool = exits.pool(None)
        self.assertEqual([(e["iface"], e.get("s4")) for e in pool], [(None, None), ("kawg2", 0), ("kawg3", 150)])


class GatewayTest(CliGwCase):
    """gw apply reads the card, writes the plan and the legs; gw status and gw mtu show it; a narrow card: the
    warning, «новые настройки» (the devices to give them to) and «оставить как есть»."""

    def card(self, mtu):
        self.write("/proc/net/route", proc_route([("eth0", "00000000", 0, "00000000")]))
        self.write("/sys/class/net/eth0/mtu", "%d\n" % mtu)

    def world(self, card=1500, s4="120"):
        self.card(card)
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "inbound", "install", "awg2")
        with state.transaction() as st:
            st["gateway"]["inbounds"]["awg2"]["line"] = "2.0"
            st["gateway"]["inbounds"]["awg2"]["params"] = {"S4": s4, "MTU": "1280"}
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "user", "add", "Дача")
        self.ok("gw", "device", "add", "Дача", "Роутер на даче", "--access", "awg2,wg")
        self.ok("gw", "device", "add", "Мама", "Телефон", "--access", "awg2")
        self.ok("gw", "device", "add", "Мама", "Ноутбук", "--access", "wg")
        self.write("/etc/kaskad/egress/awg/.kawg2.env", "ADDR=10.8.2.5/32\nTABLE=258\n")
        self.ok("gw", "apply")
        return state.load()

    def test_apply_writes_the_plan_legs_and_mss(self):
        st = self.world(1500, "120")
        m = st["gateway"]["mtu"]
        self.assertEqual((m["nic"], m["budget"]), ({"dev": "eth0", "mtu": 1500}, 1400))
        self.assertEqual(m["inbounds"]["awg2"]["server"], 1200)
        self.assertEqual(m["inbounds"]["wg"]["server"], 1280)
        self.assertEqual(m["legs"], {"kawg2": {"mtu": 1280, "s4": 0}})
        self.assertEqual(self.read("/etc/kaskad/egress/awg/.kawg2.env"), "ADDR=10.8.2.5/32\nTABLE=258\nMTU=1280\n")
        nft_text = self.read("/etc/kaskad/net/kaskad.nft")
        self.assertIn('iifname "kgw-awg2" tcp flags syn tcp option maxseg size > 1160 tcp option maxseg size set 1160',
                      nft_text)
        self.assertIn('oifname "kawg2" tcp flags syn tcp option maxseg size > 1240 tcp option maxseg size set 1240',
                      nft_text)
        out, _ = self.ok("gw", "status")
        self.assertIn("MTU: карта 1500 (eth0), бюджет 1400", out)
        self.assertIn("  вход awg2: сервер 1200, клиент 1280", out)
        self.assertIn("  нога kawg2: 1280", out)
        self.assertNotIn("уже, чем нужно", out)
        self.assertIn("! AmneziaWG 2.0: S4 = 120 — чтобы мобильные сети не тормозили, сервер уменьшил MTU до 1200; "
                      "HTTP/3 через этот протокол перейдёт на TCP. Полное лечение — новые настройки с S4 ≤ 40.", out)
        self.assertIn("kaskadctl gw mtu s4 awg2", out)

    def test_status_names_the_last_restart_of_an_inbound(self):
        # T-N5 3.2.19, №3: a restart from the timer or the watchdog has no one to warn — gw status says it
        from kaskad.gw.inbounds import tunnel
        self.world(1500, "120")
        out, _ = self.ok("gw", "status")
        self.assertNotIn("последний перезапуск входа", out)
        tunnel.note_restart("awg2", "mtu", 1216, 1184)
        out, _ = self.ok("gw", "status")
        self.assertRegex(out, r"  последний перезапуск входа: awg2 — \d\d\.\d\d(\.\d{4})?, \d\d:\d\d, причина: MTU 1216 → 1184")
        rc, doc, _ = self.run_json("gw", "mtu")
        self.assertEqual((doc["restart"]["kind"], doc["restart"]["why"]), ("awg2", "mtu"))
        self.assertIn("клиенты AmneziaWG переподключатся сами за 15–25 с", self.log())

    def test_status_names_every_inbound_restarted_in_one_action(self):
        # T-N5 3.2.19-r2, Н-3: the engine switch restarted awg1, awg2 and awg3 — gw status named only awg1
        from unittest import mock
        from kaskad.gw.inbounds import tunnel
        self.world(1500, "120")
        t = [1791190000]
        with mock.patch.object(tunnel, "now", lambda: t[0]):
            tunnel.note_restart("awg2", "mtu", 1280, 1184)               # an hour before: another action
            t[0] += 3600
            for k in ("awg3", "awg1", "awg2"):
                tunnel.note_restart(k, "engine")
                t[0] += 7
        out, _ = self.ok("gw", "status")
        self.assertRegex(out, r"  последний перезапуск входов: awg1, awg2, awg3 — \d\d\.\d\d(\.\d{4})?, \d\d:\d\d, "
                              r"причина: смена движка \(модуль ядра / программа\)\n")
        rc, doc, _ = self.run_json("gw", "mtu")
        self.assertEqual(doc["restart"]["kinds"], ["awg1", "awg2", "awg3"])
        # one action, different reasons: each reason with its inbounds
        with mock.patch.object(tunnel, "now", lambda: t[0] + 3600):
            tunnel.note_restart("awg1", "mtu", 1280, 1168)
            tunnel.note_restart("awg2", "mtu", 1280, 1136)
        last = tunnel.last_restart()
        self.assertEqual(last["kinds"], ["awg1", "awg2"])
        self.assertIn("причины: MTU 1280 → 1168 (awg1); MTU 1280 → 1136 (awg2)", tunnel.restart_text(last))
        # one inbound — as before, «входа»
        with mock.patch.object(tunnel, "now", lambda: t[0] + 7200):
            tunnel.note_restart("awg3", "unit")
        out, _ = self.ok("gw", "status")
        self.assertRegex(out, r"  последний перезапуск входа: awg3 — \d\d\.\d\d(\.\d{4})?, \d\d:\d\d, причина: новая версия службы входа")

    def test_a_narrow_card_warns_and_asks(self):
        self.world(1400, "60")
        out, _ = self.ok("gw", "status")
        self.assertIn("! AmneziaWG 2.0: карта сервера 1400 уже, чем нужно клиентам с MTU 1280 при S4 = 60", out)
        self.assertEqual(out.count("уже, чем нужно"), 1)                     # once: at the inbound
        self.assertIn("kaskadctl gw mtu reissue awg2", out)
        rc, doc, _ = self.run_json("gw", "mtu")
        self.assertEqual((rc, doc["ask"], doc["nic"]), (0, ["awg2"], 1400))
        row = [r for r in doc["inbounds"] if r["kind"] == "awg2"][0]
        self.assertEqual((row["server"], row["client"], row["new_client"]), (1248, 1280, 1248))
        # «новые настройки с меньшим MTU»: the client MTU of the inbound, the server follows, the devices are named
        rc, doc, _ = self.run_json("gw", "mtu", "reissue", "awg2")
        self.assertEqual((rc, doc["was"], doc["client"], doc["server"], doc["changed"]), (0, 1280, 1248, 1248, True))
        self.assertEqual([d["name"] for d in doc["devices"]], ["Роутер на даче", "Телефон"])
        self.assertIn("MTU клиентов 1280 → 1248", doc["text"])
        self.assertIn("Телефон (Мама)", doc["text"])
        st = state.load()
        self.assertEqual(st["gateway"]["inbounds"]["awg2"]["params"]["MTU"], "1248")
        self.assertEqual(st["gateway"]["inbounds"]["awg2"]["params"]["S4"], "60")       # nothing else changed
        self.assertIsNone(st["gateway"]["mtu"]["inbounds"]["awg2"]["offer"])
        self.assertIn("клиентский MTU 1280 → 1248", self.log())
        rc, doc, _ = self.run_json("gw", "mtu", "reissue", "awg2")                     # again: nothing to change
        self.assertFalse(doc["changed"])
        self.assertIn("менять нечего", doc["text"])

    def test_keep_as_it_is(self):
        self.world(1400, "60")
        rc, doc, _ = self.run_json("gw", "mtu", "keep", "awg2")
        self.assertEqual((rc, doc["kept"], doc["nic"]), (0, True, 1400))
        self.assertEqual(state.load()["gateway"]["mtu"]["keep"], {"awg2": 1400})
        out, _ = self.ok("gw", "status")
        self.assertNotIn("! AmneziaWG 2.0: карта", out)
        self.assertIn("узкая карта: оставлено как есть", out)
        rc, doc, _ = self.run_json("gw", "mtu")
        self.assertEqual(doc["ask"], [])
        self.assertEqual(state.load()["gateway"]["inbounds"]["awg2"]["params"]["MTU"], "1280")   # nothing reissued
        self.card(1380)                                                    # another card: the question comes back
        self.ok("gw", "apply")
        rc, doc, _ = self.run_json("gw", "mtu")
        self.assertEqual(doc["ask"], ["awg2"])

    def test_s4_below_1232_new_parameters_after_the_list(self):
        self.world(1500, "120")
        with state.transaction() as st:                                    # full parameters of a 2.0 inbound
            p = dict(awgkeys.gen_params("2.0"), S4="120")
            st["gateway"]["inbounds"]["awg2"]["params"] = p
        self.ok("gw", "apply")
        before = state.load()["gateway"]["inbounds"]["awg2"]["params"]
        rc, doc, _ = self.run_json("gw", "mtu")
        row = [r for r in doc["inbounds"] if r["kind"] == "awg2"][0]
        self.assertEqual((doc["ask"], row["offer"], row["server"], row["new_s4_max"]), (["awg2"], "s4", 1200, 40))
        # F-2 first: who needs new settings — nothing changes yet
        rc, doc, _ = self.run_json("gw", "mtu", "s4", "awg2")
        self.assertEqual((rc, doc["done"]), (0, False))
        self.assertEqual([d["name"] for d in doc["devices"]], ["Роутер на даче", "Телефон"])
        self.assertIn("перестанут подключаться — выдать заново нужно всем устройствам этого протокола: Роутер на даче "
                      "(Дача), Телефон (Мама)", doc["text"])
        self.assertEqual(state.load()["gateway"]["inbounds"]["awg2"]["params"], before)
        out, _ = self.ok("gw", "mtu", "s4", "awg2")
        self.assertIn("Подтвердить: kaskadctl gw mtu s4 awg2 --yes", out)
        from unittest import mock                                          # the menu: a machine line, not words
        with mock.patch.dict(os.environ, {"K3_MARKS": "1"}):
            out, _ = self.ok("gw", "mtu", "s4", "awg2")
        self.assertIn("@K3:confirm kaskadctl gw mtu s4 awg2 --yes", out)
        self.assertNotIn("Подтвердить", out)
        rc, doc, _ = self.run_json("gw", "mtu", "s4", "awg2", "--yes")
        self.assertTrue(doc["done"])
        after = state.load()["gateway"]["inbounds"]["awg2"]["params"]
        self.assertTrue(15 <= int(after["S4"]) <= 40)
        self.assertEqual(doc["server"], 1280)
        for k in ("S1", "S2", "H1", "H2", "H3", "H4", "I1", "MTU", "Jc"):
            self.assertEqual(after.get(k) or "", before.get(k) or "", k)    # only S3/S4 change
        self.assertIn("больше не подключатся", doc["text"])
        st = state.load()
        self.assertIsNone(st["gateway"]["mtu"]["inbounds"]["awg2"]["offer"])
        # the marks «ждут новых настроек»: in gw mtu, gone once the device is given its settings
        out, _ = self.ok("gw", "status")
        self.assertIn("    ждут новых настроек: Роутер на даче (Дача), Телефон (Мама)", out)
        tel = [d for d in st["gateway"]["devices"] if d["name"] == "Телефон"][0]
        self.ok("gw", "issue", tel["id"], "--inbound", "awg2")
        out, _ = self.ok("gw", "status")
        self.assertIn("    ждут новых настроек: Роутер на даче (Дача)", out)
        self.assertIn("новые параметры, S4 120", self.log())

    def test_keep_s4(self):
        self.world(1500, "120")
        rc, doc, _ = self.run_json("gw", "mtu", "keep", "awg2")
        self.assertEqual((doc["what"], doc["s4"]), ("s4", 120))
        self.assertEqual(state.load()["gateway"]["mtu"]["keep_s4"], {"awg2": 120})
        out, _ = self.ok("gw", "status")
        self.assertIn("    S4 = 120: оставлено как есть (сервер 1200, HTTP/3 через вход — по TCP)", out)
        self.assertNotIn("! AmneziaWG 2.0: S4", out)
        rc, doc, _ = self.run_json("gw", "mtu", "s4", "awg2", "--yes")   # a change of mind still works
        self.assertTrue(doc["done"])
        self.assertNotIn("keep_s4", state.load()["gateway"]["mtu"])
        rc, doc, _ = self.run_json("gw", "mtu", "s4", "awg2", "--yes")   # nothing left to fix
        self.assertEqual((doc["done"], doc.get("nothing")), (False, True))

    def test_refusals(self):
        self.world(1500, "15")
        rc, out, err = self.run_cli("gw", "mtu", "reissue", "awg3")
        self.assertNotEqual(rc, 0)
        self.assertIn("не установлен", err)
        rc, out, err = self.run_cli("gw", "mtu", "keep", "l2tp")
        self.assertNotEqual(rc, 0)

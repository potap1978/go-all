"""3.3.6, безопасность (SEC-hotfix-cmdinj-3 С-1, С-2, П-1; круг 2 — REV-336-awgaddr и SEC-336-awgaddr, решения
координатора в BACKLOG-3.3.6.md, запись SEC-336-awgaddr).

С-1: Address ключа AmneziaWG вешается на ногу в корневом пространстве узла, и ядро всегда ставит для него «local <адрес>»
(таблица local, раньше всех правил шлюза). Поэтому адрес клиента — только частный (10/8, 172.16/12, 192.168/16,
100.64/10, fc00::/7), не адрес сети и не широковещательный, не в сетях устройств шлюза, занятых на этом узле, не в сетях
интерфейсов и маршрутов узла, не его роутер и не его DNS. Адрес, который узел уже держит как /32 (WARP goTelegram
«gtwarp» 172.16.0.2/32), — годный: он и так «свой» (REV С-1).
П-1 и SEC З-1: Endpoint ключа — не сам узел; имя разрешает сам Kaskad, в конфиг ноги идёт проверенный IP; localhost и
числовые записи IPv4 — отказ; свой код self_ep и свой текст.
С-2: после обновления новая версия снимает ноги, которых нет в awglist (метка закрыта ДО остановки ноги и такой
остаётся — REV З-1), во временном юните kaskad-legs-fix (REV З-2), и пересоздаёт ноги с маршрутом своей подсети (ноги /32
не трогает — REV П-2).

Круг 3 (SEC-336-awgaddr-2, REV-336-awgaddr-2): списки в DNS не ходят — имя Endpoint разрешается только для конфига ноги
(awgconf: сборка — одним awgdns на все имена, старт юнита — --egress-awg-conf) и при добавлении ключа, параллельно, одним
ожиданием; не разрешилось или запрещено — нога закрыта, прежний конфиг остаётся только у того же сервера (С-1); Endpoint —
не роутер, не DNS узла и не служба хостера (П-1); Kaskad → Kaskad на одной базе — gw_net с адресом и сетью (З-3); сервер
VLESS — не этот узел (Б-3); знаки-теги — только во флаге, и в ответах kaskad-mcp (П-2).

Каждая проверка падает на коде до правки (5383956 — круг 1, 321d4f1 — круг 2, bf60614 — круг 3): там «1.1.1.1» — годный
Address, ключ WARP рядом с gtwarp — bad_addr, «127.1:53» — годный Endpoint, правило метки снимается раньше ноги, ZWJ
режется, нога N после сдвига пула и упавшего DNS остаётся с конфигом прежнего ключа N."""

import importlib.util
import io
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kaskad.gw import addr, exits
from kaskad.sysrun import FakeRunner
from tests.fakes_gw import INSTALL_SH, FakeIp, awg_conf, parser_source
from tests.test_334_xray import Quiet, parser
from tests.test_3351_cmdinj import function

# Address настоящих клиентов: Amnezia, wg-easy, WARP, CGNAT, ULA, Mullvad (fc00:bbbb:…), без префикса; устройство
# другого узла Kaskad (10.77.x.y — база его шлюза; на этом узле шлюза нет — SEC З-4)
GOOD = (("10.8.1.2/32", "10.8.1.2/32"), ("10.8.0.2/24", "10.8.0.2/24"), ("172.16.0.2/32", "172.16.0.2/32"),
        ("100.64.0.2/10", "100.64.0.2/10"), ("fd42:42:42::2/64", "fd42:42:42::2/64"), ("10.13.13.2", "10.13.13.2/32"),
        ("192.168.77.2/24", "192.168.77.2/24"), ("10.0.0.2/16", "10.0.0.2/16"),
        ("fc00:bbbb:bbbb:bb01::1:5/128", "fc00:bbbb:bbbb:bb01::1:5/128"), ("10.77.1.10/32", "10.77.1.10/32"),
        ("10.78.0.9/32", "10.78.0.9/32"), ("172.27.0.9/32", "172.27.0.9/32"))
# публичные, loopback, link-local, метаданные хостера, широковещательные, 0/8, 240/4, ::1, ::ffff:, адрес и broadcast
# своей подсети, сеть шире частной
BAD = ("1.1.1.1", "8.8.8.8/32", "169.254.169.254", "169.254.1.2/16", "127.0.0.1/8", "127.0.0.2/8", "255.255.255.255/32",
       "240.0.0.1/32", "0.1.2.3/8", "::1/128", "::ffff:1.2.3.4/128", "::ffff:10.1.2.3/128", "fe80::1/64", "fe80::2/64",
       "2606:4700:110:8a36::2/128", "2001:db8::2/64", "10.8.0.0/24", "10.8.0.255/24", "172.16.0.2/8", "198.51.100.2/32",
       "100.128.0.1/32", "100.100.100.200/32", "fd00:ec2::254/128", "fc00::1/48")
EMPTY = {"addrs": [], "nets": [], "held": [], "gws": [], "ns": [], "dsts": []}


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def node(addrs=(), gws=(), ns=(), dsts=(), peers=()):
    """_node_now узла: [(интерфейс, адрес/префикс)] (свои адреса /32 — «held», кроме ног kawgN), [(интерфейс, адрес,
    дальний конец/префикс)] — «peer», роутеры, DNS, [(интерфейс, сеть назначения)]."""
    v = {"addrs": [], "nets": [], "held": [], "gws": [ipaddress.ip_address(g) for g in gws],
         "ns": [ipaddress.ip_address(x) for x in ns], "dsts": [(d, ipaddress.ip_network(n)) for d, n in dsts]}
    for dev, a in addrs:
        i = ipaddress.ip_interface(a)
        v["addrs"].append((dev, i.ip)); v["nets"].append((dev, i.network))
        if i.network.prefixlen == i.max_prefixlen and not dev.startswith("kawg"):
            v["held"].append(i.ip)
    for dev, a, far in peers:
        i, f = ipaddress.ip_interface(a), ipaddress.ip_interface(far)
        v["addrs"] += [(dev, i.ip), (dev, f.ip)]; v["nets"] += [(dev, i.network), (dev, f.network)]
        v["held"].append(i.ip)
    return v


class AddressTest(Quiet):
    def setUp(self):
        super().setUp()
        self.p = parser()
        self.empty = mock.patch.object(self.p, "_node_now", return_value=EMPTY)
        self.empty.start()
        self.addCleanup(self.empty.stop)

    def skipped(self, p, conf):
        good, skipped = p.extract_report(conf)
        return len(good), [x["code"] for x in skipped]

    def test_real_clients_pass(self):
        p = self.p
        for a, want in GOOD:
            self.assertEqual(p._awg_ifaddr(a), want, a)
            self.assertEqual(self.skipped(p, awg_conf(address=a)), (1, []), a)

    def test_only_private_addresses(self):
        """С-1: всё, что не частный адрес клиента, — bad_addr, ключ не в пул."""
        p = self.p
        for a in BAD:
            self.assertIsNone(p._awg_ifaddr(a), a)
            self.assertEqual(self.skipped(p, awg_conf(address=a)), (0, ["bad_addr"]), a)
            self.assertEqual(p.awg_problem({"peer": {"endpoint": "198.51.100.31:51820"}, "address": a}), "bad_addr", a)

    def test_fc00_is_private(self):
        """REV П-5: IPv6 — fc00::/7 целиком (Mullvad пишет fc00:bbbb:…), не только fd00::/8."""
        self.assertIn("fc00::/7", self.p._AWG_PRIVATE)
        self.assertNotIn("fd00::/8", self.p._AWG_PRIVATE)
        self.assertEqual(self.p.awg_from_conf(awg_conf(address="fc00:bbbb:bbbb:bb01::1:5/128"))["address"],
                         "fc00:bbbb:bbbb:bb01::1:5/128")

    def test_gateway_ifaces_as_the_gateway(self):
        p = self.p
        self.assertFalse(hasattr(p, "_GW_BASES"))          # SEC З-4: не «все четыре базы всегда»
        self.assertEqual(set(p._GW_IFACES), set(addr.OWN_IFACES) - {"kawg"})

    def test_kaskad_to_kaskad(self):
        """SEC З-4: ключ устройства другого узла Kaskad (10.77.x.y/32) — годный выход, пока свой шлюз не занял эту базу;
        шлюз на этом узле (адрес на kgw-*, ppp*) — его /16 занят, другие базы свободны."""
        p = self.p
        self.assertEqual(p._awg_ifaddr("10.77.1.10/32"), "10.77.1.10/32")
        n = node([("eth0", "203.0.113.5/24"), ("kgw-wg", "10.77.1.1/24"), ("ppp0", "10.79.5.1/32")])
        with mock.patch.object(p, "_node_now", return_value=n):
            for a, net in (("10.77.1.10/32", "10.77.0.0/16"), ("10.77.200.3/32", "10.77.0.0/16"),
                           ("10.79.0.9/32", "10.79.0.0/16")):
                self.assertIsNone(p._awg_ifaddr(a), a)
                self.assertEqual(self.skipped(p, awg_conf(address=a)), (0, ["gw_net"]), a)
                # REV-336-awgaddr-2 З-3: отказ называет адрес и занятую сеть шлюза этого узла
                self.assertEqual(p.extract_report(awg_conf(address=a))[1][0]["addr"], a.split("/")[0])
                self.assertEqual(p.extract_report(awg_conf(address=a))[1][0]["net"], net)
                self.assertEqual(p.awg_gw_net({"address": a}), (a.split("/")[0], net))
            for a in ("10.78.0.9/32", "172.27.0.9/32"):
                self.assertEqual(p._awg_ifaddr(a), a, a)
                self.assertIsNone(p.awg_gw_net({"address": a}))

    def test_choose_base_skips_a_leg(self):
        """SEC З-4, вторая половина: шлюз, включённый после такого ключа, не берёт базу его ноги (адрес ноги kawgN для
        choose_base занят, как /32)."""
        ip = FakeIp()
        r = FakeRunner(binaries=["ip"]).on(["ip"], ip)
        self.assertEqual(addr.choose_base(r)[0], "10.77.0.0/16")
        ip.add_link("kawg3", "none", ["10.77.1.10/32"])
        base, skipped = addr.choose_base(r)
        self.assertEqual(base, "10.78.0.0/16")
        self.assertIn("10.77.1.10/32", skipped[0])
        ip.add_link("kawg4", "none", ["10.8.1.5/24"])        # префикс ключа — не сеть ноги (noprefixroute)
        self.assertEqual(addr.choose_base(r)[0], "10.78.0.0/16")

    def test_warp_next_to_another_warp(self):
        """REV С-1: на узле уже есть туннель WARP goTelegram (gtwarp) или goWARP (warp) с 172.16.0.2/32 — ключ WARP
        (тот же 172.16.0.2) годный: адрес уже «свой», вторая копия ничего не перехватывает. Сеть интерфейса шире /32,
        в которую адрес попал, — по-прежнему отказ; дальний конец «peer» — не свой адрес узла, тоже отказ."""
        p = self.p
        warp2 = awg_conf(address="172.16.0.2/32").replace(
            "Address = 172.16.0.2/32", "Address = 172.16.0.2/32\nAddress = 2606:4700:110:8a36:1e2b:3c4d:5e6f:7a8b/128")
        for dev in ("gtwarp", "warp"):
            n = node([("eth0", "203.0.113.5/24"), (dev, "172.16.0.2/32")])
            with mock.patch.object(p, "_node_now", return_value=n):
                self.assertEqual(p._awg_ifaddr("172.16.0.2/32"), "172.16.0.2/32", dev)
                self.assertEqual(self.skipped(p, warp2), (1, []), dev)
                self.assertEqual(self.skipped(p, awg_conf(address="172.16.0.2/32, fd01:5ca1:ab1e::2/128")), (1, []))
        n = node([("eth1", "10.0.0.5/24")], peers=[("tun0", "10.9.0.1", "10.9.0.2/32")])
        with mock.patch.object(p, "_node_now", return_value=n):
            self.assertIsNone(p._awg_ifaddr("10.0.0.77/32"))
            self.assertIsNone(p._awg_ifaddr("10.0.0.5/32"))      # адрес узла, но в сети /24 — сеть занята
            self.assertIsNone(p._awg_ifaddr("10.9.0.2/32"))      # дальний конец туннеля — не узел
            self.assertEqual(p._awg_ifaddr("10.9.0.1/32"), "10.9.0.1/32")
        # туннель шлюза — /32 на kgw/ppp: база шлюза всё равно занята
        with mock.patch.object(p, "_node_now", return_value=node([("ppp0", "10.77.5.1/32")])):
            self.assertIsNone(p._awg_ifaddr("10.77.5.1/32"))

    def test_routes_and_name_servers(self):
        """SEC З-2: сети назначения маршрутов всех таблиц (не ног kawgN) и DNS узла — отказ. Раскладки: AWS (DNS VPC за
        роутером), частная сеть Hetzner (10.0.0.0/16 via), Alibaba (DNS, метаданные), Tailscale (пиры в таблице 52)."""
        p = self.p
        cases = (
            (node([("eth0", "172.31.20.5/20")], gws=["172.31.16.1"], ns=["172.31.0.2"]), "172.31.0.2/32",
             "172.31.0.3/32"),
            (node([("eth0", "203.0.113.5/32"), ("ens10", "10.0.0.2/32")], gws=["10.0.0.1"],
                  dsts=[("ens10", "10.0.0.0/16"), ("ens10", "10.0.0.1/32")]), "10.0.0.3/32", "10.1.0.3/32"),
            (node([("eth0", "172.20.1.5/24")], ns=["100.100.2.136", "100.100.2.138"]), "100.100.2.136/32",
             "100.100.3.1/32"),
            (node([("eth0", "172.20.1.5/24")]), "100.100.100.200/32", "100.100.100.201/32"),
            (node([("eth0", "203.0.113.5/24"), ("tailscale0", "100.88.1.2/32")],
                  dsts=[("tailscale0", "100.101.1.3/32"), ("tailscale0", "100.100.100.100/32")]), "100.101.1.3/32",
             "100.101.1.4/32"),
        )
        for n, bad, ok in cases:
            with mock.patch.object(p, "_node_now", return_value=n):
                self.assertIsNone(p._awg_ifaddr(bad), bad)
                self.assertEqual(self.skipped(p, awg_conf(address=bad)), (0, ["bad_addr"]), bad)
                self.assertEqual(p._awg_ifaddr(ok), ok, ok)
        # Hetzner: свой /32 внутри маршрута шире /32 — сеть занята; маршрут ноги kawgN — мимо
        n = node([("ens10", "10.0.0.2/32")], dsts=[("ens10", "10.0.0.0/16"), ("kawg1", "10.8.1.0/24")])
        with mock.patch.object(p, "_node_now", return_value=n):
            self.assertIsNone(p._awg_ifaddr("10.0.0.2/32"))
            self.assertEqual(p._awg_ifaddr("10.8.1.5/32"), "10.8.1.5/32")
        # точный /32 маршрута к адресу, который узел держит сам (WARP), — как С-1
        n = node([("gtwarp", "172.16.0.2/32")], dsts=[("gtwarp", "172.16.0.2/32")])
        with mock.patch.object(p, "_node_now", return_value=n):
            self.assertEqual(p._awg_ifaddr("172.16.0.2/32"), "172.16.0.2/32")

    def test_networks_of_the_node(self):
        """Адрес в сети интерфейса узла (LAN хостера, docker, вход шлюза) или его роутер — bad_addr; адрес другой ноги
        kawgN (тот же 10.8.0.2 у двух ключей одного сервера) — годный."""
        p = self.p
        n = node([("eth0", "10.0.0.5/24"), ("docker0", "172.17.0.1/16"), ("kgw-wg", "10.90.1.1/24"),
                  ("kawg1", "10.8.0.2/32"), ("eth1", "fd10::5/64")], gws=["172.31.1.1"])
        with mock.patch.object(p, "_node_now", return_value=n):
            for a in ("10.0.0.1/32", "10.0.0.77/24", "172.17.0.2/16", "10.90.1.10/32", "10.90.200.3/32", "172.31.1.1/32",
                      "fd10::9/128"):
                self.assertIsNone(p._awg_ifaddr(a), a)
                code = "gw_net" if a.startswith("10.90.") else "bad_addr"   # сеть шлюза — своим кодом (З-3)
                self.assertEqual(self.skipped(p, awg_conf(address=a)), (0, [code]), a)
            for a in ("10.8.0.2/32", "10.8.0.2/24", "10.0.1.5/32", "172.31.1.2/32", "fd11::2/128"):
                self.assertTrue(p._awg_ifaddr(a), a)
        # ключ без Address — нога берёт 10.8.0.2/32: тоже проверяется
        with mock.patch.object(p, "_node_now", return_value=node([("eth0", "10.8.0.1/24")])):
            self.assertEqual(p.awg_problem({"peer": {"endpoint": "198.51.100.31:51820"}}), "bad_addr")

    def test_saved_base_of_the_gateway(self):
        """База шлюза не из списка (kaskadctl gw enable --base) — из state.json рядом с каталогом разборщика."""
        d = tempfile.mkdtemp(prefix="k3-awgaddr-")
        try:
            os.makedirs(os.path.join(d, "egress"))
            path = os.path.join(d, "egress", "egress_parse.py")
            with open(path, "w", encoding="utf-8") as f:
                f.write(parser_source())
            with open(os.path.join(d, "state.json"), "w", encoding="utf-8") as f:
                json.dump({"gateway": {"net": {"base": "10.91.0.0/16"}}}, f)
            spec = importlib.util.spec_from_file_location("ep336addr", path)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            with mock.patch.object(m, "_node_now", return_value=EMPTY):
                self.assertIsNone(m._awg_ifaddr("10.91.3.4/32"))
                self.assertEqual(m._awg_ifaddr("10.92.3.4/32"), "10.92.3.4/32")
                self.assertEqual(m._awg_ifaddr("10.77.3.4/32"), "10.77.3.4/32")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_node_read_by_ip(self):
        """_node_now читает «ip -o addr show», маршруты всех таблиц (одноадресные, не default, не ноги kawgN; роутеры —
        через via) и resolv.conf (и systemd-resolved)."""
        p = self.p
        self.empty.stop()
        outs = {
            ("addr", "show"): "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever\n"
                              "2: eth0    inet 10.0.0.5/24 brd 10.0.0.255 scope global eth0\\       valid_lft forever\n"
                              "2: eth0    inet6 fd10::5/64 scope global \\       valid_lft forever\n"
                              "7: ppp0    inet 10.77.5.1 peer 10.77.5.10/32 scope global ppp0\\  valid_lft forever\n"
                              "8: gtwarp    inet 172.16.0.2/32 scope global gtwarp\\  valid_lft forever\n"
                              "9: veth1@if8    inet 172.18.0.1/16 scope global veth1\n"
                              "10: kawg1    inet 10.8.1.5/32 scope global noprefixroute kawg1\n",
            ("-4", "route", "show", "table", "all"): "default via 172.31.1.1 dev eth0 proto dhcp\n"
                                                     "10.9.0.0/16 via 10.0.0.1 dev eth0\n"
                                                     "default via 10.66.0.1 dev kawg3\n"
                                                     "10.8.1.0/24 dev kawg1 proto kernel scope link src 10.8.1.5\n"
                                                     "100.101.1.3 dev tailscale0 table 52\n"
                                                     "unreachable default table 258 metric 1000\n"
                                                     "local 10.0.0.5 dev eth0 table local proto kernel scope host src 10.0.0.5\n"
                                                     "broadcast 10.0.0.255 dev eth0 table local proto kernel scope link\n"
                                                     "unicast 10.66.0.0/24 dev eth9 table 7\n",
            ("-6", "route", "show", "table", "all"): "default via fe80::1 dev eth0 proto ra metric 1024\n"
                                                     "multicast ff00::/8 dev eth0 table local proto kernel metric 256\n",
        }

        def run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout=outs.get(tuple(cmd[2:]), ""))
        d = tempfile.mkdtemp(prefix="k3-336-resolv-")
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "a"), "w") as f:
            f.write("# x\nnameserver 127.0.0.53\noptions edns0\n")
        with open(os.path.join(d, "b"), "w") as f:
            f.write("nameserver 172.31.0.2\nnameserver 2001:4860:4860::8888\nsearch ec2.internal\n")
        p._node_cache.clear()
        try:
            with mock.patch("sys.platform", "linux"), mock.patch("subprocess.run", side_effect=run), \
                    mock.patch.object(p, "_RESOLV", (os.path.join(d, "a"), os.path.join(d, "b"), os.path.join(d, "c"))):
                v = p._node_now()
        finally:
            p._node_cache.clear()
        self.assertIn(("eth0", ipaddress.ip_network("10.0.0.0/24")), v["nets"])
        self.assertIn(("veth1", ipaddress.ip_network("172.18.0.0/16")), v["nets"])
        got = {str(x) for _, x in v["addrs"]}
        self.assertTrue({"127.0.0.1", "10.0.0.5", "fd10::5", "10.77.5.1", "10.77.5.10", "172.18.0.1"} <= got, got)
        self.assertEqual(sorted(str(g) for g in v["gws"]), ["10.0.0.1", "172.31.1.1", "fe80::1"])
        self.assertEqual(sorted(str(x) for x in v["held"]), ["10.77.5.1", "172.16.0.2"])   # не «peer», не kawgN
        self.assertEqual(sorted((dv, str(n)) for dv, n in v["dsts"]),
                         [("eth0", "10.9.0.0/16"), ("eth9", "10.66.0.0/24"), ("tailscale0", "100.101.1.3/32")])
        self.assertEqual([str(x) for x in v["ns"]], ["127.0.0.53", "172.31.0.2", "2001:4860:4860::8888"])

    def test_not_linux_reads_nothing(self):
        p = self.p
        self.empty.stop()
        p._node_cache.clear()
        try:
            with mock.patch("sys.platform", "darwin"), mock.patch("subprocess.run") as run:
                self.assertEqual(p._node_now(), EMPTY)
            run.assert_not_called()
        finally:
            p._node_cache.clear()

    def test_warp_two_address_lines(self):
        """WARP (wgcf): «Address = 172.16.0.2/32» и «Address = 2606:4700:…/128» двумя строками. Раньше брался последний
        (публичный IPv6, без IPv4 нога не везёт ничего), теперь — годный IPv4."""
        p = self.p
        conf = awg_conf(address="172.16.0.2/32").replace(
            "Address = 172.16.0.2/32", "Address = 172.16.0.2/32\nAddress = 2606:4700:110:8a36:1e2b:3c4d:5e6f:7a8b/128")
        self.assertEqual(p.awg_from_conf(conf)["address"], "172.16.0.2/32")
        self.assertEqual(self.skipped(p, conf), (1, []))
        self.assertEqual(p.awg_from_conf(awg_conf(address="2606:4700::2/128, 172.16.0.2/32"))["address"], "172.16.0.2/32")
        self.assertEqual(p.awg_from_conf(awg_conf(address="fd42:42:42::2/64, 10.66.66.2/32"))["address"], "10.66.66.2/32")
        self.assertEqual(p.awg_from_conf(awg_conf(address="fd42:42:42::2/64"))["address"], "fd42:42:42::2/64")
        self.assertEqual(self.skipped(p, awg_conf(address="2606:4700::2/128")), (0, ["bad_addr"]))


class DnsCase(Quiet):
    """getaddrinfo подменён: ``self.dns`` — {имя: [адреса] | исключение | ("sleep", с)}; ``self.asked`` — кого спросили.
    Разборщик — настоящий: _resolve_many, _resolve, кэш (чистится до и после)."""

    def setUp(self):
        super().setUp()
        self.p = parser()
        self.dns, self.asked = {}, []
        self.p._dns_cache.clear()
        self.addCleanup(self.p._dns_cache.clear)
        for target, kw in (("socket.getaddrinfo", {"side_effect": self.gai}),
                           ("socket.gethostname", {"return_value": "kaskad-test-node"})):
            m = mock.patch(target, **kw)
            m.start()
            self.addCleanup(m.stop)
        m = mock.patch.object(self.p, "_node_now", return_value=EMPTY)
        m.start()
        self.addCleanup(m.stop)

    def gai(self, host, *a, **kw):
        import time
        self.asked.append(host)
        got = self.dns.get(host, OSError("NXDOMAIN"))
        if isinstance(got, tuple):
            time.sleep(got[1])
            raise OSError("timeout")
        if isinstance(got, Exception):
            raise got
        return [((10 if ":" in x else 2), 2, 17, "", (x, 0) if ":" not in x else (x, 0, 0, 0)) for x in got]


class EndpointTest(DnsCase):
    """П-1, SEC З-1: Endpoint ключа — не сам узел: рукопожатие и I1–I5 ключа не уходят в службы узла или к метаданным
    хостера. Имя разрешает Kaskad, в конфиг ноги — проверенный IP; свой код self_ep. Круг 3: списки в DNS не ходят —
    имя разрешается только для конфига ноги (awgconf) и при добавлении ключа (extract_report)."""

    def problem(self, ep, resolve=False):
        return self.p.awg_problem({"peer": {"endpoint": ep}, "address": "10.8.1.5/32"}, resolve)

    def skip(self, ep):
        good, skipped = self.p.extract_report(awg_conf(endpoint=ep))
        return good, [x["code"] for x in skipped]

    def test_endpoint_of_the_node_refused(self):
        for ep in ("127.0.0.1:53", "127.5.5.5:51820", "[::1]:53", "169.254.169.254:80", "[::ffff:127.0.0.1]:53",
                   "0.0.0.0:51820", "0.1.2.3:51820", "255.255.255.255:51820", "224.0.0.1:51820", "[fe80::1]:51820",
                   "240.0.0.1:51820"):
            self.assertEqual(self.problem(ep), "self_ep", ep)
            self.assertEqual(self.skip(ep), ([], ["self_ep"]), ep)

    def test_names_and_numbers_of_the_node(self):
        """SEC З-1: localhost, имена systemd и числовые записи IPv4 (getaddrinfo читает их как inet_aton) — отказ без
        DNS; числовая запись не этого сервера — тоже не адрес (bad_link)."""
        for ep in ("localhost:53", "foo.localhost:53", "LocalHost:53", "ip6-localhost:53", "_gateway:51820",
                   "2130706433:53", "127.1:53", "0x7f000001:53", "0x7f.1:53", "127.0.0.01:53", "2852039166:80",
                   "169.254.43518:80"):
            self.assertEqual(self.problem(ep, True), "self_ep", ep)
            self.assertEqual(self.skip(ep), ([], ["self_ep"]), ep)
        for ep in ("3405803777:51820", "203.0.113.1.5:51820", "198.51.100.010:51820", "box.0x1f:51820"):
            self.assertEqual(self.problem(ep, True), "bad_link", ep)
        with mock.patch("socket.gethostname", return_value="Node-7.example.net"):
            self.assertEqual(self.problem("node-7:51820", True), "self_ep")
            self.assertEqual(self.problem("node-7.example.net:51820", True), "self_ep")
        self.assertEqual(self.asked, [])

    def test_lists_never_ask_dns(self):
        """SEC-336-awgaddr-2 З-1, REV-336-awgaddr-2 З-2: awglist, problems, config, list и ядро шлюза (awg_problem
        без resolve) имя не разрешают — даже то, что указывает на этот сервер: его закроет нога (awgconf)."""
        self.dns["awg.example.com"] = ["127.0.0.1"]
        self.assertEqual(self.problem("awg.example.com:443"), "")
        pool = "awg://" + self.p._b64e(json.dumps(self.p.awg_from_conf(awg_conf(endpoint="awg.example.com:443"), "x")))
        cfg, n = self.p.build_config([pool], 10808, "http://x/", "leastPing", "10s", 100, 10851, 0)
        self.assertEqual(n, 1)
        self.assertEqual(self.asked, [])

    def test_name_resolved_and_checked(self):
        """Имя разрешает Kaskad: любой из адресов — этот сервер — self_ep; годное — в конфиг ноги идёт IP (первый
        ответ, IPv4 первым, IPv6 в скобках), а не имя; не разрешилось — ключ не отвергается (DNS мог лечь), но
        конфига ноги нет."""
        p = self.p
        rec = p.awg_from_conf(awg_conf(endpoint="awg.example.com:443"), "x")
        for ips in (["127.0.0.1"], ["198.51.100.31", "127.0.0.1"], ["169.254.169.254"], ["::1"]):
            p._dns_cache.clear(); self.dns["awg.example.com"] = ips
            self.assertEqual(self.problem("awg.example.com:443", True), "self_ep", ips)
            self.assertEqual(self.skip("awg.example.com:443"), ([], ["self_ep"]), ips)
            with self.assertRaises(ValueError):
                p.awg_setconf(rec)
        p._dns_cache.clear(); self.dns["awg.example.com"] = ["2001:db8::31", "198.51.100.31", "198.51.100.4"]
        self.assertEqual(self.problem("awg.example.com:443", True), "")
        conf = p.awg_setconf(rec)
        self.assertIn("\nEndpoint = 198.51.100.31:443\n", conf)
        self.assertNotIn("example.com", conf)
        p._dns_cache.clear(); self.dns["awg.example.com"] = ["2001:db8::31"]
        self.assertIn("\nEndpoint = [2001:db8::31]:443\n", p.awg_setconf(rec))
        p._dns_cache.clear(); del self.dns["awg.example.com"]
        self.assertEqual(self.problem("awg.example.com:443", True), "")
        self.assertEqual(len(self.skip("awg.example.com:443")[0]), 1)
        with self.assertRaises(ValueError):
            p.awg_setconf(rec)
        # IP в ключе — как есть, без DNS
        self.asked.clear()
        rec = p.awg_from_conf(awg_conf(endpoint="[2001:db8::7]:51820"), "x")
        self.assertIn("\nEndpoint = [2001:db8::7]:51820\n", p.awg_setconf(rec))
        self.assertEqual(self.asked, [])

    def test_addresses_of_the_node_refused(self):
        """Адрес узла, его роутер, его DNS и адреса служб хостеров — не сервер ключа (SEC-336-awgaddr-2 П-1: та же
        проверка, что у Address)."""
        p = self.p
        n = node([("eth0", "203.0.113.5/24"), ("eth0", "2001:db8:5::5/64"), ("kawg1", "10.8.0.2/32"),
                  ("kgw-wg", "10.95.1.1/24")], gws=["172.31.16.1", "203.0.113.1"], ns=["172.31.0.2", "fd00:ec2::253"])
        with mock.patch.object(p, "_node_now", return_value=n):
            for ep in ("203.0.113.5:51820", "[2001:db8:5::5]:51820", "10.8.0.2:51820", "10.95.1.20:51820",
                       "3405803781:51820", "172.31.0.2:53", "172.31.16.1:53", "203.0.113.1:51820",
                       "100.100.100.200:80", "[fd00:ec2::254]:80", "[fd00:ec2::253]:53"):
                self.assertEqual(self.problem(ep), "self_ep", ep)
            for ep in ("203.0.113.6:51820", "198.51.100.31:51820", "192.168.1.5:51820", "10.77.0.1:51820",
                       "172.31.0.3:51820"):
                self.assertEqual(self.problem(ep), "", ep)
            self.dns["awg.example.com"] = ["203.0.113.5"]
            self.assertEqual(self.problem("awg.example.com:443", True), "self_ep")

    def test_awgconf_writes_the_ip(self):
        """awgconf (файл ноги) — Endpoint как IP; ключ с Endpoint этого сервера — код 3, конфига нет."""
        pool = "\n".join("awg://" + self.p._b64e(json.dumps(self.p.awg_from_conf(awg_conf(n=i, endpoint=ep), "k")))
                         for i, ep in ((1, "198.51.100.31:51820"), (2, "127.1:53"))) + "\n"
        r = subprocess.run(["python3", self.p.__file__, "awgconf", "1"], input=pool, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("\nEndpoint = 198.51.100.31:51820\n", r.stdout)
        r = subprocess.run(["python3", self.p.__file__, "awgconf", "2"], input=pool, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout), (3, ""))


class PrevConfTest(DnsCase):
    """SEC-336-awgaddr-2 С-1, REV-336-awgaddr-2 П-1: прежний конфиг ноги (--prev) оставляет свой IP только тому же
    серверу ([Peer] PublicKey тот же); другой ключ под тем же номером его не наследует никогда."""

    def setUp(self):
        super().setUp()
        self.a = self.p.awg_from_conf(awg_conf(n=1, endpoint="awg-a.example.net:51820"), "A")
        self.b = self.p.awg_from_conf(awg_conf(n=2, endpoint="awg-b.example.net:51820"), "B")
        self.dns["awg-a.example.net"] = ["198.51.100.9"]
        self.prev = self.p._awg_conf_peer(self.p.awg_setconf(self.a))
        self.p._dns_cache.clear()

    def test_prev_read(self):
        self.assertEqual(self.prev, (self.a["peer"]["public_key"], ipaddress.ip_address("198.51.100.9")))
        self.assertIsNone(self.p._awg_conf_peer("[Peer]\nPublicKey = x\nEndpoint = awg.example.net:51820\n"))
        self.assertIsNone(self.p._awg_conf_peer(""))

    def test_another_key_never_gets_the_old_config(self):
        """С-1: под номером 1 теперь ключ B, DNS молчит — конфига нет (а не конфиг A)."""
        with self.assertRaises(ValueError):
            self.p.awg_setconf(self.b, prev=self.prev)

    def test_same_server_keeps_its_ip(self):
        """Тот же сервер, DNS молчит — прежний IP (нога живёт до следующей попытки); но не адрес узла."""
        self.dns.pop("awg-a.example.net")
        self.assertIn("\nEndpoint = 198.51.100.9:51820\n", self.p.awg_setconf(self.a, prev=self.prev))
        n = node([("eth0", "198.51.100.9/24")])
        with mock.patch.object(self.p, "_node_now", return_value=n):
            with self.assertRaises(ValueError):
                self.p.awg_setconf(self.a, prev=self.prev)

    def test_same_answers_same_ip(self):
        """П-1: имя даёт тот же набор в другом порядке — IP прежний (конфиг тот же, нога не перезапускается); прежнего
        IP среди ответов нет — первый ответ; имя теперь указывает на этот сервер — конфига нет."""
        self.dns["awg-a.example.net"] = ["198.51.100.7", "198.51.100.9"]
        self.assertIn("\nEndpoint = 198.51.100.9:51820\n", self.p.awg_setconf(self.a, prev=self.prev))
        self.p._dns_cache.clear(); self.dns["awg-a.example.net"] = ["198.51.100.7", "198.51.100.8"]
        self.assertIn("\nEndpoint = 198.51.100.7:51820\n", self.p.awg_setconf(self.a, prev=self.prev))
        self.p._dns_cache.clear(); self.dns["awg-a.example.net"] = ["127.0.0.1"]
        with self.assertRaises(ValueError):
            self.p.awg_setconf(self.a, prev=self.prev)


class ResolveTest(DnsCase):
    def test_resolve_cached_and_bounded(self):
        """_resolve: getaddrinfo с пределом ожидания, ответ — без повторов, порядок ответа, IPv4 первым; кэш с TTL."""
        p = self.p
        self.dns["a.example"] = ["198.51.100.31", "2001:db8::31", "198.51.100.31", "198.51.100.4"]
        self.assertEqual([str(x) for x in p._resolve("a.example")], ["198.51.100.31", "198.51.100.4", "2001:db8::31"])
        p._resolve("a.example")
        self.assertEqual(self.asked, ["a.example"])
        self.assertIsNone(p._resolve("b.example"))
        import time
        self.dns["slow.example"] = ("sleep", 5)
        with mock.patch.object(p, "_DNS_WAIT", 0.2):
            t0 = time.monotonic()
            self.assertIsNone(p._resolve("slow.example"))
            self.assertLess(time.monotonic() - t0, 2)

    def test_many_names_one_wait(self):
        """SEC-336-awgaddr-2 З-1: 20 имён, DNS спит 10 с — одно общее ожидание на вызов, а не 20; добавление ключей
        (extract_report) тоже."""
        import time
        p = self.p
        names = ["k%d.example.net" % i for i in range(20)]
        for h in names:
            self.dns[h] = ("sleep", 10)
        with mock.patch.object(p, "_DNS_WAIT", 0.5):
            t0 = time.monotonic()
            got = p._resolve_many(names)
            self.assertLess(time.monotonic() - t0, 2)
            self.assertEqual(got, {h: None for h in names})
            p._dns_cache.clear()
            text = "".join(awg_conf(n=i, endpoint="%s:51820" % h) for i, h in enumerate(names, 1))
            t0 = time.monotonic()
            good, skipped = p.extract_report(text)
            self.assertLess(time.monotonic() - t0, 2)
            self.assertEqual((len(good), skipped), (20, []))

    def test_awgdns_and_seed(self):
        """awgdns — ответы всех имён пула одним вызовом; awgconf --dns берёт их, не спрашивая DNS снова; IP, имена
        узла и числовые формы в awgdns не попадают."""
        p = self.p
        self.dns["awg-a.example.net"] = ["198.51.100.9"]
        lines = [p.awg_from_conf(awg_conf(n=i, endpoint=ep), "k") for i, ep in
                 ((1, "awg-a.example.net:51820"), (2, "nx.example.net:51820"), (3, "198.51.100.5:51820"),
                  (4, "localhost:51820"), (5, "2130706433:53"))]
        pool = "\n".join("awg://" + p._b64e(json.dumps(r)) for r in lines) + "\n"
        names = [x for x in (p._awg_line_name(l) for l in pool.splitlines()) if x]
        self.assertEqual(names, ["awg-a.example.net", "nx.example.net"])
        d = tempfile.mkdtemp(prefix="k3-awgdns-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "dns.json")
        with open(path, "w") as f:
            json.dump({h: [str(x) for x in v] if v else None for h, v in p._resolve_many(names).items()}, f)
        p._dns_cache.clear(); self.asked.clear()
        self.dns["awg-a.example.net"] = ["127.0.0.1"]           # DNS «передумал» — берётся ответ сборки
        p._dns_seed(path)
        self.assertIn("\nEndpoint = 198.51.100.9:51820\n", p.awg_setconf(lines[0]))
        with self.assertRaises(ValueError):
            p.awg_setconf(lines[1])
        self.assertEqual(self.asked, [])
        with open(path, "w") as f:                                # в файле — адрес узла: проверка та же
            json.dump({"awg-a.example.net": ["127.0.0.1"]}, f)
        p._dns_cache.clear(); p._dns_seed(path)
        with self.assertRaises(ValueError):
            p.awg_setconf(lines[0])


class FormatCharsTest(Quiet):
    """REV-336-hotfix-merge П-3, REV-336-awgaddr П-1, SEC П-4: в терминал — без управляющих и без знаков формата,
    которые переворачивают строку или прячут текст; U+200D (составные эмодзи), теги флагов и знаки, которых Python
    3.9 ещё не знает (🫠, Юникод 14), — остаются."""

    BIDI = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200e\u200f\u061c\ufeff\u2028\u2029\u00ad\u2060"
    KEEP = ("👨\u200d👩\u200d👧 Семья", "🏳\ufe0f\u200d🌈", "\U0001fae0 melt", "\U0001fa77 pink",
            "🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f", "Хельсинки 🇫🇮 №1", "1\ufe0f\u20e3",
            "\u0646\u0645\u200c\u0627", "\ue0b0 nerd")

    def test_term_and_names(self):
        p = parser()
        for ch in self.BIDI:
            self.assertEqual(p._term("a%sb" % ch), "a b", hex(ord(ch)))
            self.assertEqual(exits.term_text("a%sb\x1b" % ch), "a b", hex(ord(ch)))
            line = "vless://11111111-2222-3333-4444-555555555555@198.51.100.20:443?type=tcp#a%s%%1bb" % ch
            self.assertEqual(p._skip_name(line), "a b", hex(ord(ch)))
        self.assertEqual(p._term("a\x1b]0;x\x07\x9bb"), "a ]0;x b")
        for s in self.KEEP:
            self.assertEqual(p._term(s), s, ascii(s))
            self.assertEqual(exits.term_text(s), s, ascii(s))
            line = "vless://11111111-2222-3333-4444-555555555555@198.51.100.20:443?type=tcp#" + \
                __import__("urllib.parse").parse.quote(s)
            self.assertEqual(p._skip_name(line), s, ascii(s))

    def test_menu_snippets_same_rule(self):
        """Два вывода 9d951eb (передача маршрутов, имена выходов в меню) — тем же списком, без unicodedata."""
        src = read(INSTALL_SH)
        rule = re.search(r'_TERM_UNSAFE = re\.compile\("(\[[^"]+\])"\)', src).group(1)
        for fn in ("egress_handover", "_egr_names_refresh"):
            body = function(fn)
            self.assertNotIn("unicodedata", body, fn)
            self.assertIn(rule, body, fn)
        self.assertEqual(src.count("unicodedata.category(c)[0] == \"C\""), 0)
        self.assertEqual(src.count(rule), 3)
        self.assertEqual(exits._UNSAFE.pattern, rule.encode().decode("unicode_escape"))


def lookup_text(key, lang):
    from kaskad.i18n import lookup
    return lookup(key, lang)


class TextsTest(unittest.TestCase):
    """Отвергнутый Address — «вне частных сетей … ключ не подходит, попросите новый»; Endpoint этого сервера — «адрес
    сервера в ключе указывает на этот сервер» (REV П-3, SEC П-3). RU и EN, меню, ядро, панель."""

    def texts(self, code, panel):
        root = os.path.dirname(INSTALL_SH)
        sh = {lang: read(os.path.join(root, "lib/kaskad/i18n/sh/egress.%s.sh" % lang)) for lang in ("ru", "en")}
        out = {"ru": [], "en": []}
        for lang in ("ru", "en"):
            out[lang] += re.findall(r"(?m)^M_%s_EGRESS_\w*_%s='(.*)'$" % (lang, code.upper()), sh[lang])
            for area in ("gw_ops", "gw_exitsubs", "gw_exits"):
                cat = json.loads(read(os.path.join(root, "lib/kaskad/i18n/catalog/%s/%s.json" % (lang, area))))
                out[lang] += [v for k, v in cat.items() if k.endswith("." + code) or k == "gw_exits.problem." + panel]
        return out

    def test_bad_addr_texts(self):
        texts = self.texts("bad_addr", "awg_bad_addr")
        self.assertEqual((len(texts["ru"]), len(texts["en"])), (8, 8))
        for t in texts["ru"]:
            self.assertIn("вне частных сетей", t)
            self.assertRegex(t, r"попросите новы[йе]")
            self.assertNotIn("не разобран", t); self.assertNotIn("заново", t)
        for t in texts["en"]:
            self.assertIn("outside private networks", t)
            self.assertRegex(t, r"ask for (a new one|new ones)")
            self.assertNotIn("couldn’t be read", t); self.assertNotIn("again", t)

    def test_self_ep_texts(self):
        """Круг 3: self_ep — и у VLESS (Б-3), поэтому без «AmneziaWG»."""
        texts = self.texts("self_ep", "awg_self_ep")
        self.assertEqual((len(texts["ru"]), len(texts["en"])), (8, 8))
        for t in texts["ru"]:
            self.assertIn("адрес сервера в ключе указывает на этот сервер", t)
            self.assertRegex(t, r"попросите новы[йе]")
            self.assertNotIn("не разобран", t)
        for t in texts["en"]:
            self.assertIn("the server address in the key points to this server", t)
            self.assertRegex(t, r"ask for (a new one|new ones)")
        for lang in ("ru", "en"):
            self.assertEqual(lookup_text("gw_exits.problem.vless_self_ep", lang), lookup_text("gw_exits.problem.awg_self_ep", lang))

    def test_gw_net_texts(self):
        """REV-336-awgaddr-2 З-3: Kaskad → Kaskad на одной базе — текст называет сеть шлюза (и адрес, где ключ один) и
        что делать: сменить базу шлюза на одном из серверов, а не «попросите новый»."""
        texts = self.texts("gw_net", "awg_gw_net")
        self.assertEqual((len(texts["ru"]), len(texts["en"])), (8, 8))
        # REV-336-awgaddr-3 П-3: простыми словами, где это сделать и чем грозит (ключи устройств — заново)
        for t in texts["ru"]:
            self.assertIn("сеть шлюза этого сервера", t)
            self.assertRegex(t, r"[Пп]ереведите шлюз одного из серверов на другую сеть")
            # 3.3.7.15 F6 (З-1): сеть шлюза меняется в панели — подсказка ведёт туда, а не к отказу gw enable --base
            self.assertIn("(в панели: Настройки › Шлюз › Сеть шлюза)", t)
            self.assertNotIn("enable --base", t)
            self.assertIn("устройствам переведённого шлюза придётся выдать ключи заново", t)
            self.assertNotIn("базу", t)
            self.assertNotIn("попросите нов", t)
            self.assertTrue("{net}" in t or "%s" in t, t)
        for t in texts["en"]:
            self.assertIn("gateway network of this server", t)
            self.assertRegex(t, r"[Mm]ove the gateway of one of the servers to another network")
            self.assertIn("(in the panel: Settings › Gateway › Gateway network)", t)
            self.assertNotIn("enable --base", t)
            self.assertIn("the devices of the gateway you move will need new keys", t)
            self.assertNotIn("base on", t)
            self.assertNotIn("ask for", t)
        from kaskad.i18n import tr
        self.assertEqual(tr("gw_exits.problem.awg_gw_net", "ru", addr="10.77.0.10", net="10.77.0.0/16"),
                         "адрес клиента 10.77.0.10 попадает в сеть шлюза этого сервера 10.77.0.0/16 — переведите шлюз "
                         "одного из серверов на другую сеть (в панели: Настройки › Шлюз › Сеть шлюза); устройствам "
                         "переведённого шлюза придётся выдать ключи заново")

    def test_self_ep_is_a_skip_code(self):
        from kaskad.gw import exitsubs, ops
        self.assertIn("self_ep", ops.SKIP_CODES)
        self.assertIn("self_ep", exitsubs.SKIP_CODES)
        self.assertEqual(exits.AWG_PROBLEMS, {"bad_addr": "awg_bad_addr", "self_ep": "awg_self_ep", "gw_net": "awg_gw_net"})
        for c in ("problem.awg_self_ep", "problem.awg_gw_net", "problem.vless_self_ep"):
            self.assertIn(c, exits.DETAIL_CODES)
        self.assertIn("self_ep", exits.VLESS_PROBLEMS)
        self.assertIn("gw_net", ops.SKIP_CODES)
        self.assertIn("gw_net", exitsubs.SKIP_CODES)
        self.assertEqual(exits.PARSER_MIN, 28)               # стык с этапом 1: v28 и PARSER_MIN 28 (SPEC-todo-coordinator, REV-336-dot З-1)
        self.assertEqual(parser().PARSER_VERSION, 28)         # 3.3.6 R-336-heal: код 4, флаги — три; 25: селекторы; 26: точка в конце; 27: REV-336-dot-sec; 28: + этап 1
        for body in (function("egress_skip_report"), function("egress_awg_warn_text")):
            self.assertIn("self_ep)", body)
            self.assertIn("gw_net)", body)


# С-2: путь обновления — функции install.sh как есть (план, снятие ноги, маршрутизация ноги); вокруг — заглушки: ip
# с памятью (интерфейсы, правила fwmark, маршруты подсети) в файлах, юниты, пересборка. После КАЖДОЙ заглушки — проверка
# окна: у метки, правило которой было до починки (нога прежней версии), правило есть всё время (REV З-1, SEC З-3).
STUBS = r'''
LOG="$W/log"; : > "$LOG"; : >> "$W/rules"; AWG_DIR="$W/awg"; K3_UNIT_DIR="$W/units"; K3_OPT="$W/opt"
AWG_DNS_FILE="$W/run/kaskad/egress/awgdns.json"; KASKAD_ROOT="$W"
mkdir -p "$AWG_DIR" "$K3_UNIT_DIR"
window() {
    local m
    for m in $(cat "$W/watch" 2>/dev/null); do grep -qx "$m $m" "$W/rules" || echo "OPEN $m after: $*" >> "$LOG"; done
}
log_action() { echo "log $*" >> "$LOG"; }
k3_log_lang() { echo ru; }
tf() { echo "$*"; }
egress_pool_locked() { echo "locked $1" >> "$LOG"; "$@"; }
egress_leg_known() { cat "$W/known" 2>/dev/null; }
awg_go_bin() { [ -e "$W/core" ] && echo /x/amneziawg-go; }
awg_tool_bin() { [ -e "$W/core" ] && echo /x/awg; }
egress_awg_info() { :; }
egress_awg_line_of() { echo 1.0; }
k3_networkd_keep_foreign() { :; }
egress_awg_leg_up() { echo "up $1 $2 $5 lazy=${9:-}" >> "$LOG"; window "up $2"; }
egress_rebuild() { echo "rebuild" >> "$LOG"; window rebuild; [ ! -e "$W/rebuild_fails" ]; }
k3_gw_pool_changed() { echo "gw apply" >> "$LOG"; }
systemctl() { echo "systemctl $*" >> "$LOG"; window "systemctl $*"; return 0; }
pkill() { echo "pkill $*" >> "$LOG"; window "pkill $*"; return 1; }
drop_line() { awk -v k="$2" '!d && $0 == k { d = 1; next } 1' "$1" > "$1.t"; mv -f "$1.t" "$1"; }
ip() {
    local rc=0
    echo "ip $*" >> "$LOG"
    case "$*" in
        "-o link show") awk '{ printf "%d: %s: <POINTOPOINT,UP>\n", NR + 9, $1 }' "$W/links" ;;
        "link show dev "*) grep -qx "$4" "$W/links" || rc=1 ;;
        "link del "*) grep -qx "$3" "$W/links" && drop_line "$W/links" "$3" ;;
        "-o addr show dev "*) cat "$W/addr.$5" 2>/dev/null ;;
        "-4 route show table main dev "*) cat "$W/route4.$7" 2>/dev/null ;;
        "-6 route show table main dev "*) cat "$W/route6.$7" 2>/dev/null ;;
        "-4 route show table "[0-9]*) cat "$W/table.$5" 2>/dev/null ;;
        "-4 route del "*) drop_line "$W/route4.$6" "$(grep "^$4 " "$W/route4.$6" | head -1)" ;;
        "-6 route del "*) drop_line "$W/route6.$6" "$(grep "^$4 " "$W/route6.$6" | head -1)" ;;
        "-4 rule show") awk '{ printf "%d:\tfrom all fwmark 0x%x lookup %s\n", 1000 + NR, $1, $2 }' "$W/rules" ;;
        "rule add fwmark "*) echo "$4 $6" >> "$W/rules" ;;
        "rule del fwmark "*) if grep -qx "$4 $6" "$W/rules"; then drop_line "$W/rules" "$4 $6"; else rc=1; fi ;;
        *) ;;
    esac
    window "ip $*"
    return "$rc"
}
'''
FUNCS = ("egress_legs_post_update_fix", "egress_legs_fix_run", "egress_legs_fix_plan", "egress_leg_subnet_routes",
         "egress_legs_post_update_now", "egress_awg_leg_remove", "egress_awg_route_leg", "egress_leg_unit",
         "egress_awg_sync", "egress_awg_dns_begin", "egress_awg_dns_end", "k3_run_dir_make", "egress_leg_table_clear",
             "egress_awg_lists", "egress_up_file", "egress_any_keys")  # T15 Q4: ноги обоих файлов


def fn(name):
    """Функция install.sh; однострочная «name() { …; }» — только её строка (function() из test_3351_cmdinj захватил бы
    и следующие за ней функции до первой «}» в начале строки)."""
    m = re.search(r"^%s\(\) \{[^\n]*\}\n" % re.escape(name), read(INSTALL_SH), re.M)
    return m.group(0) if m else function(name)


class LegsCase(unittest.TestCase):
    def setUp(self):
        self.w = tempfile.mkdtemp(prefix="k3-336-legs-")
        self.addCleanup(shutil.rmtree, self.w, True)
        self.p = parser()
        self.parse = self.p.__file__

    def write(self, name, text):
        with open(os.path.join(self.w, name), "w") as f:
            f.write(text)

    def pool(self, *recs):
        lines = []
        for i, (address, endpoint) in enumerate(recs, 1):
            rec = self.p.awg_from_conf(awg_conf(n=i, endpoint=endpoint), "k%d" % i)
            rec["address"] = address                          # как в пуле 3.3.5: туда попадало и такое
            lines.append("awg://" + self.p._b64e(json.dumps(rec)))
        lines.append("vless://11111111-2222-3333-4444-555555555555@198.51.100.20:443?type=tcp&security=none#v")
        self.write("up.txt", "\n".join(lines) + "\n")

    def run_sh(self, entry, extra=""):
        body = "".join(fn(f) for f in FUNCS)
        script = 'W="$1"; EGRESS_UP="$W/up.txt"; EGRESS_PARSE_PY="$2"\n' + STUBS + extra + body + entry + "\n"
        r = subprocess.run(["bash", "-c", script, "_", self.w, self.parse], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.w, "log")) as f:
            return f.read().splitlines()

    @staticmethod
    def acts(log):
        return [ln for ln in log if not ln.startswith(("log ", "locked ", "ip -o ", "ip link show", "ip -4 rule show",
                                                        "ip -4 route show", "ip -6 route show"))]

    def rules(self):
        with open(os.path.join(self.w, "rules")) as f:
            return f.read().split("\n")


class PostUpdateTest(LegsCase):
    """С-2: k3_post_update новой версии чинит ноги прежней без перезагрузки."""

    def setUp(self):
        super().setUp()
        self.pool(("10.8.1.5/24", "198.51.100.31:51820"), ("8.8.8.8/32", "198.51.100.32:51820"))
        self.write("links", "kawg1\nkawg2\n")
        self.write("known", "kawg1\nkawg2\nkawg5\n")          # kawg5 — файлы ноги без интерфейса и без ключа
        self.write("rules", "257 257\n258 258\n")
        self.write("watch", "257\n258\n")
        self.write("addr.kawg1", "10: kawg1    inet 10.8.1.5/24 scope global kawg1\\       valid_lft forever\n")
        self.write("route4.kawg1", "10.8.1.0/24 proto kernel scope link src 10.8.1.5\n")

    def test_bad_leg_removed_closed_rebuilt(self):
        """Нога ключа, которого нет в awglist (Address 8.8.8.8 из пула 3.3.5), и нога без ключа — прочь; метка закрыта ДО
        остановки ноги (ни одного мгновения без правила) и остаётся закрытой после пересборки; нога годного ключа
        с маршрутом подсети — пересоздаётся (не lazy)."""
        open(os.path.join(self.w, "core"), "w").close()
        log = self.run_sh("egress_legs_fix_run")
        self.assertEqual(log[0], "locked egress_legs_post_update_now")
        self.assertFalse([ln for ln in log if ln.startswith("OPEN")], log)
        acts = self.acts(log)
        self.assertFalse([a for a in acts if a.startswith(("ip rule del", "ip route flush"))], acts)
        i261, s5 = acts.index("ip rule add fwmark 261 table 261"), acts.index(
            "systemctl disable --no-reload --now kaskad-leg@kawg5.service")
        self.assertLess(i261, s5, acts)                           # закрыта до остановки
        self.assertIn("ip link del kawg2", acts)
        self.assertEqual([a for a in acts if a.startswith(("up ", "rebuild", "gw apply"))],
                         ["up 1 kawg1 10.8.1.5/24 lazy=", "rebuild"])
        self.assertEqual(sorted(x for x in self.rules() if x), ["257 257", "258 258", "261 261"])
        self.assertTrue(any("EGRESS_LOG_LEG_DROPPED kawg2" in ln for ln in log), log)
        self.assertTrue(any("EGRESS_LOG_LEG_PREFIX kawg1" in ln for ln in log), log)
        self.assertFalse(os.path.exists(os.path.join(self.w, "awg", ".kawg2.conf")))

    def test_rebuild_failed_mark_stays_closed_gw_applied(self):
        """Пересборка не прошла — метка закрыта, а gw apply всё равно идёт (REV З-1, второе; З-2)."""
        open(os.path.join(self.w, "core"), "w").close()
        open(os.path.join(self.w, "rebuild_fails"), "w").close()
        log = self.run_sh("egress_legs_fix_run")
        acts = self.acts(log)
        self.assertEqual([a for a in acts if a in ("rebuild", "gw apply")], ["rebuild", "gw apply"])
        self.assertFalse([ln for ln in log if ln.startswith("OPEN")], log)
        self.assertIn("258 258", self.rules())

    def test_fixed_legs_untouched(self):
        """Всё уже по правилам (маршрута подсети нет, лишних ног нет) — ни снятия, ни перезапуска, ни пересборки, ни
        юнита: так при каждом старте демонов."""
        self.write("known", "kawg1\n"); self.write("links", "kawg1\n"); self.write("route4.kawg1", "")
        self.pool(("10.8.1.5/24", "198.51.100.31:51820"))
        open(os.path.join(self.w, "core"), "w").close()
        log = self.run_sh("egress_legs_post_update_fix", extra=self.UNIT)
        self.assertEqual(self.acts(log), [])

    def test_leg_32_not_restarted(self):
        """REV П-2, SEC П-2: нога /32 (ключи Amnezia) без noprefixroute — у неё маршрута подсети нет и не было: не
        перезапускается."""
        self.write("known", "kawg1\n"); self.write("links", "kawg1\n"); self.write("route4.kawg1", "")
        self.pool(("10.8.1.5/32", "198.51.100.31:51820"))
        self.write("addr.kawg1", "10: kawg1    inet 10.8.1.5/32 scope global kawg1\\       valid_lft forever\n")
        open(os.path.join(self.w, "core"), "w").close()
        self.assertEqual(self.acts(self.run_sh("egress_legs_fix_run")), [])

    def test_no_core_route_of_the_subnet_removed_once(self):
        """Ядра AmneziaWG нет (ногу не пересоздать) — маршрут подсети «proto kernel» снимается руками, в журнале — так и
        сказано; следующий старт ничего не находит и строку не повторяет."""
        self.write("known", "kawg1\n"); self.write("links", "kawg1\n")
        self.pool(("10.8.1.5/24", "198.51.100.31:51820"))
        self.write("route6.kawg1", "fd42::/64 proto kernel metric 256\nfe80::/64 proto kernel metric 256\n")
        log = self.run_sh("egress_legs_fix_run")
        acts = self.acts(log)
        self.assertIn("ip -4 route del 10.8.1.0/24 dev kawg1 proto kernel table main", acts)
        self.assertIn("ip -6 route del fd42::/64 dev kawg1 proto kernel table main", acts)
        self.assertFalse([ln for ln in acts if "fe80" in ln and "del" in ln], acts)
        self.assertFalse([ln for ln in acts if ln.startswith(("up ", "systemctl", "rebuild"))], acts)
        self.assertTrue(any("EGRESS_LOG_LEG_PREFIX_ROUTE kawg1" in ln for ln in log), log)
        self.assertFalse(any("EGRESS_LOG_LEG_PREFIX kawg1" in ln for ln in log), log)
        log = self.run_sh("egress_legs_fix_run")
        self.assertEqual([ln for ln in log if ln.startswith("log ")], [])
        self.assertEqual(self.acts(log), [])

    def test_unreadable_pool_untouched(self):
        """Разборщик не прочитал пул — ни одна нога не снимается."""
        self.parse = os.path.join(self.w, "missing.py")
        self.assertEqual(self.acts(self.run_sh("egress_legs_fix_run")), [])
        self.assertEqual(self.acts(self.run_sh("egress_legs_post_update_fix", extra=self.UNIT)), [])

    UNIT = r'''
mkdir -p "$K3_OPT/bin"; printf '#!/bin/sh\n' > "$K3_OPT/bin/gokaskad"; chmod +x "$K3_OPT/bin/gokaskad"
systemd-run() { echo "systemd-run $*" >> "$LOG"; }
'''

    def test_fix_in_its_own_unit(self):
        """REV З-2, SEC П-1: при старте демона (и в --k3-post-update) — только проверка; снятие и пересборка — во
        временном юните kaskad-legs-fix через 30 с: бот не перезапускает сам себя, gw apply не теряется."""
        open(os.path.join(self.w, "core"), "w").close()
        log = self.run_sh("egress_legs_post_update_fix", extra=self.UNIT)
        acts = self.acts(log)
        self.assertEqual(acts, ["systemd-run --unit=kaskad-legs-fix --on-active=30 --quiet --collect "
                                "%s/opt/bin/gokaskad --egress-legs-fix" % self.w], acts)
        self.assertEqual(sorted(x for x in self.rules() if x), ["257 257", "258 258"])
        # без systemd-run — здесь же, под блокировкой
        log = self.run_sh("egress_legs_post_update_fix")
        self.assertIn("locked egress_legs_post_update_now", log)
        self.assertIn("rebuild", self.acts(log))

    def test_dispatch_and_post_update(self):
        """--egress-legs-fix — вход юнита; k3_post_update зовёт починку ног после MTU ног (разборщик этой версии уже на
        диске)."""
        src = read(INSTALL_SH)
        self.assertRegex(src, r"(?m)^    --egress-legs-fix\) egress_legs_fix_run ;;")
        body = function("k3_post_update")
        self.assertRegex(body, r"egress_legs_mtu_refresh\n(?:\s*#.*\n)*\s*egress_legs_post_update_fix\n")
        self.assertLess(body.index("egress_ensure_parser"), body.index("egress_legs_post_update_fix"))
        self.assertNotIn("egress_rebuild", fn("egress_legs_post_update_fix"))


class SyncWindowTest(LegsCase):
    """REV З-1 (было до правки): пересборка пула снимает ногу ключа, которого больше нет, — до перезапуска xray бота
    (он ещё метит её меткой). Метка закрыта до остановки ноги и не открывается."""

    def test_removed_leg_stays_closed(self):
        self.pool(("10.8.1.5/32", "198.51.100.31:51820"))
        self.write("links", "kawg1\nkawg2\n"); self.write("known", "kawg1\nkawg2\n")
        self.write("rules", "257 257\n258 258\n"); self.write("watch", "257\n258\n")
        open(os.path.join(self.w, "core"), "w").close()
        log = self.run_sh("egress_awg_sync")
        self.assertFalse([ln for ln in log if ln.startswith("OPEN")], log)
        acts = self.acts(log)
        self.assertIn("ip link del kawg2", acts)
        self.assertIn("up 1 kawg1 10.8.1.5/32 lazy=lazy", acts)
        self.assertFalse([a for a in acts if a.startswith(("ip rule del", "ip route flush"))], acts)
        self.assertEqual(sorted(x for x in self.rules() if x), ["257 257", "258 258"])

    def test_teardown_still_clears(self):
        """Все ноги прочь (пул опустел, удаление Kaskad) — правила и таблицы снимаются, как раньше; из таблицы — только
        своё (3.3.7.14: не ip route flush — чужие маршруты в ней остаются, tests/test_33714_fixes.py)."""
        self.write("links", "kawg2\n"); self.write("rules", "258 258\n")
        self.write("table.258", "unreachable default metric 1000 \n")
        log = self.run_sh("egress_awg_leg_remove kawg2")
        self.assertIn("ip rule del fwmark 258 table 258", self.acts(log))
        self.assertIn("ip route del unreachable default metric 1000 table 258", self.acts(log))
        self.assertEqual([x for x in self.rules() if x], [])



# Круг 3 (SEC-336-awgaddr-2 С-1, З-1; REV-336-awgaddr-2 З-1, П-1): настоящие egress_awg_sync, egress_awg_leg_up,
# egress_awg_leg_files, egress_awg_conf_write, egress_awg_leg_close, egress_awg_leg_remove, egress_legs_mtu_refresh,
# egress_awg_conf_unit и разборщик; DNS подменён (sitecustomize: getaddrinfo по карте K3_FAKE_DNS, иначе «нет имени»).
FAKE_DNS = r'''
import json, os, socket, time
def _gai(host, *a, **k):
    with open(os.environ["K3_FAKE_DNS"]) as f:
        m = json.load(f)
    with open(os.environ["K3_FAKE_DNS"] + ".asked", "a") as f:
        f.write(host + "\n")
    v = m.get(host)
    if v == "sleep":
        time.sleep(30)
    if not isinstance(v, list):
        raise socket.gaierror(-2, "Name or service not known")
    return [(2, 2, 17, "", (x, 0)) for x in v]
socket.getaddrinfo = _gai
'''
LEG_STUBS = r'''
egress_leg_template() { return 1; }
egress_leg_set_mode() { return 1; }
egress_awg_leg_mode() { echo userspace; }
egress_nic_mtu() { echo 1500; }
egress_ensure_parser() { :; }
egress_leg_dropin() { echo "$W/units/kaskad-leg@$1.service.d/kernel.conf"; }
'''
LEG_FUNCS = ("egress_awg_sync", "egress_awg_leg_up", "egress_awg_leg_files", "egress_awg_conf_write",
             "egress_awg_conf_unit", "egress_awg_dns_begin", "egress_awg_dns_end", "egress_awg_leg_close",
             "egress_awg_leg_remove", "egress_awg_route_leg", "egress_leg_unit", "egress_legs_mtu_refresh",
             "egress_pool_has_awg", "egress_file_age", "k3_run_dir_make", "egress_awg_nodns_since",
             "egress_awg_leg_revived", "egress_leg_held",               # REV-s1-q4-2 С-1: нога ждёт применения
             "egress_awg_lists", "egress_up_file", "egress_any_keys")  # T15 Q4: ноги обоих файлов


class LegConfBase(LegsCase):
    """Ноги с настоящими функциями install.sh и подменённым DNS (тестов здесь нет: их берут LegConfTest и test_336_heal)."""

    FUNCS = LEG_FUNCS

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.w, "site"))
        self.write("site/sitecustomize.py", FAKE_DNS)
        self.dns({})
        self.write("links", "kawg1\n"); self.write("known", ""); self.write("rules", "")
        open(os.path.join(self.w, "core"), "w").close()

    def dns(self, m):
        self.write("dns.json", json.dumps(m))

    def keys(self, *ns_eps):
        """Пул: ключи awg_conf(n) с Endpoint ep — по порядку (номер ноги = номер строки)."""
        lines = ["awg://" + self.p._b64e(json.dumps(self.p.awg_from_conf(awg_conf(n=n, endpoint=ep), "k%d" % n)))
                 for n, ep in ns_eps]
        self.write("up.txt", "\n".join(lines) + "\n")

    def run_sh(self, entry, extra=""):
        env = dict(os.environ, PYTHONPATH=os.path.join(self.w, "site"), K3_FAKE_DNS=os.path.join(self.w, "dns.json"))
        body = "".join(fn(f) for f in self.FUNCS)
        script = 'W="$1"; EGRESS_UP="$W/up.txt"; EGRESS_PARSE_PY="$2"\n' + STUBS + LEG_STUBS + extra + body + entry + "\n"
        r = subprocess.run(["bash", "-c", script, "_", self.w, self.parse], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=120, env=env)
        self.out = r.stdout
        with open(os.path.join(self.w, "log")) as f:
            return r.returncode, f.read().splitlines()

    def conf(self, n=1):
        try:
            with open(os.path.join(self.w, "awg", ".kawg%d.conf" % n)) as f:
                return f.read()
        except OSError:
            return None

    def peer(self, n):
        return "PublicKey = %s" % self.p.awg_from_conf(awg_conf(n=n))["peer"]["public_key"]

    def stale_answers(self, age=120):
        """Ответы DNS последней сборки (AWG_DNS_FILE, /run/kaskad/egress/awgdns.json) — старше ``age`` с."""
        f = self.dns_file()
        if os.path.exists(f):
            import time
            t = time.time() - age
            os.utime(f, (t, t))

    def dns_file(self):
        return os.path.join(self.w, "run", "kaskad", "egress", "awgdns.json")

    def build_a(self):
        """Нога 1 — ключ A по имени, DNS ответил 203.0.113.9."""
        self.keys((1, "awg-a.example.net:51820"))
        self.dns({"awg-a.example.net": ["203.0.113.9"]})
        rc, log = self.run_sh("egress_awg_sync")
        self.assertEqual(rc, 0)
        self.assertIn(self.peer(1), self.conf())
        self.assertIn("Endpoint = 203.0.113.9:51820", self.conf())


class LegConfTest(LegConfBase):
    """SEC-336-awgaddr-2 С-1: имя Endpoint не разрешилось — нога N закрыта, конфига прежнего ключа N у неё нет."""

    def test_shift_and_dead_dns_close_the_leg(self):
        """Удаление ключа A сдвинуло пул: под номером 1 — ключ B, его имя не разрешилось. Нога 1 закрыта (юнит снят,
        файлы прочь, правило метки 257 на месте), конфига A с IP A у неё нет; рестарта со старым конфигом нет."""
        self.build_a()
        self.keys((2, "awg-b.example.net:51820"))
        self.dns({"awg-a.example.net": ["203.0.113.9"]})          # B — «нет имени»
        open(os.path.join(self.w, "log"), "w").close()
        rc, log = self.run_sh("egress_awg_sync")
        self.assertEqual(rc, 0)
        self.assertIsNone(self.conf())
        self.assertFalse(os.path.exists(os.path.join(self.w, "awg", ".kawg1.env")))
        acts = self.acts(log)
        self.assertIn("systemctl disable --no-reload --now kaskad-leg@kawg1.service", acts)
        self.assertNotIn("systemctl restart kaskad-leg@kawg1.service", acts)
        self.assertIn("257 257", self.rules())
        self.assertFalse([a for a in acts if a.startswith(("ip rule del", "ip route flush"))], acts)
        # имя не разрешилось — своя строка журнала: нога ждёт DNS (R-336-heal, REV-336-awgaddr-3 З-1)
        self.assertTrue([ln for ln in log if ln.startswith("log ") and "EGRESS_LOG_LEG_CLOSED_DNS kawg1" in ln], log)
        self.assertFalse([f for f in os.listdir(os.path.join(self.w, "awg")) if f.startswith(".dns.")])

    def test_shift_and_bad_dns_answer_close_the_leg(self):
        """То же, если имя B теперь указывает на этот сервер (127.0.0.1): конфига нет."""
        self.build_a()
        self.keys((2, "awg-b.example.net:51820"))
        self.dns({"awg-b.example.net": ["127.0.0.1"]})
        rc, log = self.run_sh("egress_awg_sync")
        self.assertIsNone(self.conf())
        self.assertIn("257 257", self.rules())

    def test_same_server_dead_dns_keeps_its_ip(self):
        """Тот же ключ (тот же сервер), DNS лёг — нога живёт на прежнем IP, без перезапуска (конфиг тот же)."""
        self.build_a()
        self.dns({})
        open(os.path.join(self.w, "log"), "w").close()
        rc, log = self.run_sh("egress_awg_sync")
        self.assertIn("Endpoint = 203.0.113.9:51820", self.conf())
        acts = self.acts(log)
        self.assertNotIn("systemctl restart kaskad-leg@kawg1.service", acts)
        self.assertNotIn("systemctl disable --no-reload --now kaskad-leg@kawg1.service", acts)

    def test_one_dns_round_per_build(self):
        """З-1: сборка из трёх ног с именами — имена спрошены одним awgdns (каждое — раз), ноги берут ответ сборки."""
        self.keys((1, "a1.example.net:51820"), (2, "a2.example.net:51820"), (3, "a3.example.net:51820"))
        self.dns({"a1.example.net": ["203.0.113.1"], "a2.example.net": ["203.0.113.2"], "a3.example.net": ["203.0.113.3"]})
        self.write("links", "kawg1\nkawg2\nkawg3\n")
        rc, log = self.run_sh("egress_awg_sync")
        self.assertEqual(rc, 0)
        for i in (1, 2, 3):
            self.assertIn("Endpoint = 203.0.113.%d:51820" % i, self.conf(i))
        with open(os.path.join(self.w, "dns.json.asked")) as f:
            self.assertEqual(sorted(f.read().split()), ["a1.example.net", "a2.example.net", "a3.example.net"])

    def test_silent_dns_build_bounded(self):
        """З-1: 10 ног с именами, DNS молчит (30 с на запрос) — сборка ждёт DNS один раз (awgdns, 2 с), а не 10 × 2 с;
        все ноги закрыты."""
        import time
        entry = "egress_awg_sync"
        self.keys(*[(i, "s%d.example.net:51820" % i) for i in range(1, 11)])
        self.write("links", "".join("kawg%d\n" % i for i in range(1, 11)))
        # R-speed-2b: та же сборка, когда DNS сразу не отвечает ничего, — сколько она идёт на этой машине сейчас (рядом
        # прогоны с Chrome); граница ниже — на ожидание DNS сверх этого (под -j 12 всё время было 9,6 с при 8,0)
        self.dns({})
        t0 = time.monotonic()
        rc, log = self.run_sh(entry)
        base = time.monotonic() - t0
        self.assertEqual(rc, 0, log)
        self.dns({"s%d.example.net" % i: "sleep" for i in range(1, 11)})
        t0 = time.monotonic()
        rc, log = self.run_sh(entry)
        took = time.monotonic() - t0
        self.assertEqual(rc, 0)
        self.assertLess(took - base, 2.0 + 10 * 0.6, (took, base))
        for i in range(1, 11):
            self.assertIsNone(self.conf(i))
            self.assertIn("%d %d" % (256 + i, 256 + i), self.rules())

    def test_unit_start_rebuilds_the_config(self):
        """REV-336-awgaddr-2 З-1: старт юнита (--egress-awg-conf) собирает конфиг заново: имя переехало — новый IP;
        DNS лёг — прежний IP того же сервера; пул сдвинулся и DNS лёг — конфига нет, код 1."""
        self.build_a()
        self.dns({"awg-a.example.net": ["203.0.113.10"]})
        self.stale_answers()                 # ответы сборки старше минуты — старт юнита спрашивает DNS сам (R-336-heal П-1)
        rc, _ = self.run_sh("egress_awg_conf_unit kawg1")
        self.assertEqual(rc, 0)
        self.assertIn("Endpoint = 203.0.113.10:51820", self.conf())
        self.dns({})
        rc, _ = self.run_sh("egress_awg_conf_unit kawg1")
        self.assertEqual(rc, 0)
        self.assertIn("Endpoint = 203.0.113.10:51820", self.conf())
        self.keys((2, "awg-b.example.net:51820"))
        rc, _ = self.run_sh("egress_awg_conf_unit kawg1")
        self.assertEqual(rc, 1)
        self.assertIsNone(self.conf())
        rc, _ = self.run_sh("egress_awg_conf_unit 'kawg1;x'")
        self.assertEqual(rc, 1)

    def test_conf_of_335_with_a_name(self):
        """Хвост З-1: конфиг ноги 3.3.5 с именем (awg разрешил бы его сам при перезапуске). После обновления DNS
        молчит — нога закрыта, файла с именем нет; DNS ответил — в конфиге IP."""
        self.keys((1, "awg-a.example.net:51820"))
        os.makedirs(os.path.join(self.w, "awg"), exist_ok=True)
        old = "[Interface]\nPrivateKey = x\n\n[Peer]\n%s\nEndpoint = awg-a.example.net:51820\n" % self.peer(1)
        self.write("awg/.kawg1.conf", old)
        self.write("awg/.kawg1.env", "ADDR=10.8.1.5/32\nTABLE=257\nMTU=1280\n")
        rc, log = self.run_sh("egress_legs_mtu_refresh")
        self.assertEqual(rc, 0)
        self.assertIsNone(self.conf())
        self.assertIn("systemctl disable --no-reload --now kaskad-leg@kawg1.service", self.acts(log))
        self.assertIn("257 257", self.rules())
        self.write("awg/.kawg1.conf", old)
        self.write("awg/.kawg1.env", "ADDR=10.8.1.5/32\nTABLE=257\nMTU=1280\n")
        self.dns({"awg-a.example.net": ["203.0.113.9"]})
        rc, log = self.run_sh("egress_legs_mtu_refresh")
        self.assertIn("Endpoint = 203.0.113.9:51820", self.conf())
        self.assertNotIn("example.net", self.conf())

    def test_unit_template_rebuilds_on_start(self):
        """Шаблон юнита: после правил (--egress-awg-routes, «-») — --egress-awg-conf %i без «-» (нет конфига — нет
        ноги), до awg setconf; режим --egress-awg-conf в разборе аргументов."""
        body = function("egress_leg_template")
        i = body.index("ExecStartPre=-$cmd --egress-awg-routes %i\nExecStartPre=$cmd --egress-awg-conf %i\n")
        self.assertLess(i, body.index("setconf %i"))
        self.assertRegex(read(INSTALL_SH), r'(?m)^    --egress-awg-conf\) egress_awg_conf_unit "\$\{2:-\}" \|\| exit 1 ;;')


class VlessServerTest(DnsCase):
    """SEC-336-awgaddr-2 Б-3: сервер VLESS — не этот узел и не его службы (127/8, ::1, localhost, числовые формы IPv4,
    169.254.169.254) — та же проверка, что у Endpoint; код self_ep (числовая форма не узла — bad_link); outbound нет."""

    V = "vless://11111111-2222-3333-4444-555555555555@%s?type=ws&path=/admin/x&security=none#v"

    def test_refused(self):
        p = self.p
        for hp in ("127.0.0.1:8080", "[::1]:8080", "localhost:8080", "2130706433:8080", "127.1:8080",
                   "169.254.169.254:80", "0x7f.1:8080"):
            line = self.V % hp
            self.assertEqual(p.vless_problem(line), "self_ep", hp)
            self.assertIsNone(p.vless_outbound(line, "t"), hp)
            self.assertEqual([x["code"] for x in p.extract_report(line)[1]], ["self_ep"], hp)
        self.assertEqual(p.vless_problem(self.V % "3405803777:443"), "bad_link")
        for hp in ("198.51.100.20:443", "vless.example.com:443"):
            self.assertEqual(p.vless_problem(self.V % hp), "", hp)
            self.assertTrue(p.vless_outbound(self.V % hp, "t"), hp)
        self.assertEqual(self.asked, [])                          # xray разрешает имя сам: здесь DNS не нужен
        cfg, n = p.build_config([self.V % "127.0.0.1:8080", self.V % "198.51.100.20:443"], 10808, "http://x/",
                                "leastPing", "10s", 100, 10851, 0)
        self.assertEqual([o["settings"]["vnext"][0]["address"] for o in cfg["outbounds"] if o["protocol"] == "vless"],
                         ["198.51.100.20"])

    def test_trailing_dot(self):
        """REV-s1-rel Б-1 (R-336-dot): имя с точкой в конце — то же имя (корень DNS; getaddrinfo и xray так и читают):
        «localhost.», «127.0.0.1.» и прочие формы узла с точкой — self_ep, как без неё; точка после IDNA («。», «．») —
        так же; пустая метка («localhost..», «.») и IPv6 с точкой («::1.») — не адрес; законные имена с точкой годны, в
        outbound — без неё. Имена, указывающие на узел через DNS («127.0.0.1.nip.io»), для xray не разрешаются (решение
        3.3.6) — годны, как и раньше."""
        p = self.p
        for h in ("localhost.", "LOCALHOST.", "127.0.0.1.", "0.0.0.0.", "169.254.169.254.", "LocalHost.LocalDomain.",
                  "foo.localhost.", "0x7f.1.", "2130706433.", "localhost\u3002", "127.0.0.1\uff0e"):
            line = self.V % (h + ":8080")
            self.assertEqual(p.vless_problem(line), "self_ep", h)
            self.assertIsNone(p.vless_outbound(line, "t"), h)
            self.assertEqual([x["code"] for x in p.extract_report(line)[1]], ["self_ep"], h)
            ep = {"peer": {"endpoint": h + ":51820"}, "address": "10.8.1.5/32"}
            self.assertEqual(p.awg_problem(ep), "self_ep", h)
            self.assertEqual(p.awg_problem(ep, True), "self_ep", h)
        with mock.patch("socket.gethostname", return_value="Node-7.example.net"):
            self.assertEqual(p.vless_problem(self.V % "node-7.example.net.:443"), "self_ep")
        for h in ("localhost..", ".", "a..b.", "::1.", "[::1].", "localhost\u3002\u3002"):
            self.assertEqual(p.vless_problem(self.V % (h + ":443")), "bad_link", h)
            self.assertIsNone(p._host(h), h)
        for h, bare in (("vless.example.com.", "vless.example.com"), ("Vless.Example.COM.", "vless.example.com"),
                        ("198.51.100.20.", "198.51.100.20"), ("127.0.0.1.nip.io", "127.0.0.1.nip.io")):
            self.assertEqual(p.vless_problem(self.V % (h + ":443")), "", h)
            self.assertEqual(p.vless_outbound(self.V % (h + ":443"), "t")["settings"]["vnext"][0]["address"], bare, h)
        self.assertEqual(p._host("::1"), "::1")
        self.assertEqual(self.asked, [])

    def test_no_tofu_probe_of_the_node(self):
        line = "vless://11111111-2222-3333-4444-555555555555@127.0.0.1:8443?type=tcp&security=tls&allowInsecure=1#v"
        self.assertEqual(self.p._tofu_insecure_servers([line]), set())

    def test_tofu_snap_skips_the_node(self):
        """REV-336-dot-sec Н-1: сама проба TOFU (tofu_snap) не стучится в узел и его службы — тот же _server_problem, что
        у --prune; в пробу идёт проверенный хост (_host: без точки в конце), SNI по умолчанию — он же (как у xray)."""
        p = self.p
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        m = mock.patch.object(p, "_TOFU_FILE", os.path.join(tmp, "tofu-pins.json"))
        m.start()
        self.addCleanup(m.stop)
        seen = []

        def fetch(h, port, sni, timeout, alpn):
            seen.append((h, port, sni))
            return "ab" * 32
        key = "vless://11111111-2222-3333-4444-555555555555@%s:8443?type=tcp&security=tls&allowInsecure=1#v"
        node = [key % h for h in ("127.0.0.1", "localhost.", "169.254.169.254", "[::1]", "metadata",
                                  "metadata.google.internal.", "127.1")]
        self.assertEqual(p.tofu_snap(node, first=True, fetch=fetch), [])
        self.assertEqual(seen, [])
        got = p.tofu_snap([key % "Vless.Example.COM."], first=True, fetch=fetch)
        self.assertEqual([x[0] for x in got], ["pinned"])
        self.assertEqual(seen, [("vless.example.com", 8443, "vless.example.com")])
        self.assertEqual(self.asked, [])

    def test_names_of_the_node_and_the_hoster(self):
        """REV-336-dot-sec Н-2, П-1: имена узла и хостера узнаются без DNS — одна метка (не число: «metadata»,
        «instance-data», «metadata.» после снятия точки), *.localhost.localdomain (systemd-resolved), *.internal
        (зона хостеров) — self_ep и у VLESS, и у Endpoint; законные имена из двух и более меток не задеты."""
        p = self.p
        for h in ("metadata", "metadata.", "METADATA", "instance-data", "vpn", "foo.localhost.localdomain",
                  "foo.localhost.localdomain.", "metadata.google.internal", "instance-data.ec2.internal",
                  "a.b.internal."):
            self.assertEqual(p.vless_problem(self.V % (h + ":443")), "self_ep", h)
            self.assertIsNone(p.vless_outbound(self.V % (h + ":443"), "t"), h)
            ep = {"peer": {"endpoint": h + ":51820"}, "address": "10.8.1.5/32"}
            self.assertEqual(p.awg_problem(ep), "self_ep", h)
        self.assertEqual(p.vless_problem(self.V % "3405803777:443"), "bad_link")   # одна метка-число — не имя
        for h in ("vless.example.com", "a.example", "internal.example.com", "localdomain.example.net",
                  "metadata.example.com", "xn--80ak6aa92e.com", "my-vpn.example.org."):
            self.assertEqual(p.vless_problem(self.V % (h + ":443")), "", h)
            self.assertTrue(p.vless_outbound(self.V % (h + ":443"), "t"), h)
        self.assertEqual(self.asked, [])

    def test_no_double_dot_after_idna(self):
        """REV-336-dot-sec П-2: «﹒» (U+FE52) после IDNA — «.»: «local﹒﹒host» → «local..host», пустая метка — не хост."""
        p = self.p
        for h in ("local\ufe52\ufe52host", "a\ufe52\ufe52b.example.com"):
            self.assertIsNone(p._host(h), h)
            self.assertEqual(p.vless_problem(self.V % (h + ":443")), "bad_link", h)
        self.assertEqual(p._host("a\ufe52example.com"), "a.example.com")

    def test_gateway_words(self):
        """Ядро шлюза: выход VLESS на этот сервер — своими словами (problem.vless_self_ep)."""
        line = self.V % "127.0.0.1:8080"
        self.assertEqual(exits._vless_problem(self.p, line, "unparsed"), "self_ep")
        rows = exits.preview(self.p, line)
        self.assertEqual(rows[0]["problem"], exits.word("problem.vless_self_ep"))


class GwNetWordsTest(DnsCase):
    """REV-336-awgaddr-2 З-3: Kaskad → Kaskad на одной базе — меню, панель, подписка и выходы шлюза называют адрес и
    сеть шлюза этого узла."""

    def setUp(self):
        super().setUp()
        n = node([("eth0", "203.0.113.5/24"), ("kgw-wg", "10.77.1.1/24")])
        m = mock.patch.object(self.p, "_node_now", return_value=n)
        m.start()
        self.addCleanup(m.stop)
        self.conf = awg_conf(address="10.77.0.10/32")

    def test_parser_outputs(self):
        pool = self.p.extract_report(self.conf)[1][0]["line"] + "\n"
        self.assertEqual(self.p.awg_problem(json.loads(self.p._b64d(pool.strip()[6:]))), "gw_net")
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(pool)), mock.patch("sys.stdout", out), \
                mock.patch("sys.argv", ["egress_parse.py", "problems"]):
            self.p.main()
        self.assertEqual(out.getvalue(), "1\tgw_net\t10.77.0.10\t10.77.0.0/16\n")
        err = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(self.conf)), mock.patch("sys.stdout", io.StringIO()), \
                mock.patch("sys.stderr", err), mock.patch("sys.argv", ["egress_parse.py", "extract"]):
            self.p.main()
        self.assertRegex(err.getvalue(), r"(?m)^K3SKIP\tgw_net\t[^\t\n]+\t10\.77\.0\.10\t10\.77\.0\.0/16$")

    def test_gateway_words(self):
        from kaskad.i18n import tr
        want = tr("gw_exits.problem.awg_gw_net", addr="10.77.0.10", net="10.77.0.0/16")
        self.assertIn("10.77.0.10", want)
        self.assertEqual(exits.preview(self.p, self.conf)[0]["problem"], want)
        line = self.p.extract_report(self.conf)[1][0]["line"]
        rec = exits._awg_record(self.p, line[6:])
        self.assertEqual(exits._gw_net_args(self.p, rec, "gw_net"), {"addr": "10.77.0.10", "net": "10.77.0.0/16"})

    def test_marks_of_the_script(self):
        from kaskad.gw import ops
        from kaskad.i18n import tr
        self.assertIn("10.77.0.0/16", ops._warn_text([], 0, ["skipped", "gw_net", "2", "10.77.0.0/16"]))
        self.assertIn("10.77.0.10", ops._warn_text([], 0, ["vless_key", "3", "gw_net", "10.77.0.10", "10.77.0.0/16"]))
        self.assertEqual(ops._coded_fail("skipped", ["skipped", "gw_net", "10.77.0.0/16"]),
                         tr("gw_ops.script_fail.skipped.gw_net", net="10.77.0.0/16"))
        self.assertIn("?", ops._warn_text([], 0, ["skipped", "gw_net", "2", "$(x)"]))      # чужое в метке — «?»

    def test_subscription_words(self):
        from kaskad.gw import exitsubs
        with mock.patch.object(exits, "gw_base_text", return_value="10.77.0.0/16"):
            self.assertIn("10.77.0.0/16", exitsubs.skip_why(["gw_net"]))

    def test_menu(self):
        """egress_skip_report: строка ключа — адрес и сеть, причины — сеть, метки панели — с сетью."""
        w = tempfile.mkdtemp(prefix="k3-gwnet-")
        self.addCleanup(shutil.rmtree, w, True)
        with open(os.path.join(w, ".extract.skip"), "w") as f:
            f.write("K3SKIP\tgw_net\tДом\t10.77.0.10\t10.77.0.0/16\nK3SKIP\tbad_addr\tX\n")
        script = ('EGRESS_DIR="$1"; YELLOW=""; NC=""; tf() { echo "$*"; }; t() { echo "$*"; }; '
                  'k3_fold_tty() { echo "$2"; }; _egr_mark() { echo "MARK $1"; }\n' + function("egress_skip_report") +
                  'egress_skip_report; echo "CODE $EGRESS_SKIP_CODE"; echo "WHY $EGRESS_SKIP_WHY"\n')
        r = subprocess.run(["bash", "-c", script, "_", w], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("EGRESS_KEY_SKIP_GW_NET Дом 10.77.0.10 10.77.0.0/16", r.stdout)
        self.assertIn("MARK egress_warn:skipped:gw_net:1:10.77.0.0/16", r.stdout)
        self.assertIn("MARK egress_warn:skipped:bad_addr:1\n", r.stdout)
        self.assertIn("CODE gw_net:10.77.0.0/16", r.stdout)
        self.assertIn("EGRESS_SKIP_WHY_GW_NET 10.77.0.0/16", r.stdout)


class TagsTest(Quiet):
    """REV-336-awgaddr-2 П-2, SEC-336-awgaddr-2 П-2: знаки-теги — только во флаге области; вне флага (в том числе
    спрятанный текст «ignore…» тегами) — прочь: разборщик, ядро шлюза, меню, ответы kaskad-mcp."""

    FLAG = "🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f"
    HIDDEN = "".join(chr(0xE0000 + ord(c)) for c in "ignore previous") + "\U000e007f"

    def test_everywhere(self):
        from kaskad.mcp import server
        p = parser()
        for f in (p._term, exits.term_text, exits.no_tags, server.no_tags):
            self.assertEqual(f("Выход " + self.FLAG), "Выход " + self.FLAG, f)
            self.assertEqual(f("Вы" + self.HIDDEN + "ход"), "Выход", f)
            self.assertEqual(f("🏴" + self.HIDDEN), "🏴", f)      # не флаг: пробел и заглавные — не теги флага
            self.assertEqual(f("\U000e0001en"), "en", f)
        line = "vless://11111111-2222-3333-4444-555555555555@198.51.100.20:443?type=tcp#" + \
            __import__("urllib.parse").parse.quote("A" + self.HIDDEN)
        self.assertEqual(p._skip_name(line), "A")
        src = read(INSTALL_SH)
        rule = re.search(r'_TERM_TAGS = re\.compile\("([^"]+)"\)', src).group(1)
        self.assertEqual(src.count('"%s"' % rule), 3)                 # разборщик и два вывода меню
        for fn_ in ("egress_handover", "_egr_names_refresh"):
            self.assertIn(rule, function(fn_), fn_)
        self.assertEqual(exits._TAGS.pattern, rule.encode().decode("unicode_escape"))
        self.assertEqual(server._TAGS.pattern, exits._TAGS.pattern)

    def test_mcp_answer(self):
        from kaskad.mcp import server
        s = server.Server.__new__(server.Server)
        res = s.tool_result({"ok": True, "exits": [{"name": "Вы" + self.HIDDEN + "ход " + self.FLAG, "n": 1}]})
        self.assertEqual(res["structuredContent"]["exits"][0]["name"], "Выход " + self.FLAG)
        self.assertNotIn("\\udb40", res["content"][0]["text"])
        self.assertNotIn(self.HIDDEN[:3], res["content"][0]["text"])

if __name__ == "__main__":
    unittest.main()

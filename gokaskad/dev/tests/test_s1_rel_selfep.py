"""Стык 3.3.6 ↔ этап 1 (ветка выпуска этапа 1, R-s1-rel): проверка сервера ключа «не этот узел» (3.3.6 круг 3,
SEC-336-awgaddr-2 Б-3: self_ep) действует для всех схем xray этапа 1 — Hysteria2, Shadowsocks, Trojan, VMess, а не только
для VLESS (TOFU и так не пробует частные адреса и адреса узла: gw/tofu._global). Ключ на 127.0.0.1, ::1, localhost, 169.254.169.254 или числовую форму адреса узла не попадает в пул, xray бота
туда не ходит; ядро шлюза называет причину своими словами (problem.key_self_ep)."""

import unittest
from unittest import mock

from kaskad.gw import exits, exitsubs, ops
from tests.test_336_awgaddr import DnsCase, node
from tests.test_egress_parse_s1 import FIX, b64, vmess

SELF = ("127.0.0.1", "[::1]", "localhost", "169.254.169.254", "2130706433", "0x7f.1")


def links(host):
    """Ключ каждой схемы этапа 1 на сервер ``host`` (секреты выдуманы)."""
    return {
        "hysteria2": "hy2://hyAuthSecret1@%s:443/?sni=hy.example.com#Hy" % host,
        "shadowsocks": "ss://" + b64("chacha20-ietf-poly1305:ssSecret1", url=True).rstrip("=") + "@%s:8388#SS" % host,
        "trojan": "trojan://tjSecret1@%s:443?security=tls&sni=tj.example.com&type=tcp#Tj" % host,
        "vmess": vmess(add=host.strip("[]")),
    }


class OtherSchemesSelfEp(DnsCase):
    def test_refused_for_every_scheme(self):
        p = self.p
        for host in SELF:
            for proto, line in links(host).items():
                with self.subTest(host=host, proto=proto):
                    self.assertEqual(p.link_problem(line), "self_ep")
                    self.assertEqual(p.pool_problem(line), "self_ep")       # ключ пула: предупреждение, не нога
                    self.assertIsNone(p.xray_outbound(line, "t"))
                    self.assertEqual([x["code"] for x in p.extract_report(line)[1]], ["self_ep"])

    def test_dot_at_the_end_and_names_without_dns(self):
        """3.3.6 v26–v27 (REV-s1-rel Б-1, REV-336-dot-sec) — для схем этапа 1 тот же _host и _self_name, что у VLESS:
        точка в конце («localhost.», «127.0.0.1.», «LOCALHOST。») — тот же сервер; имена узла и хостера без DNS (одна
        метка, *.internal, *.localhost.localdomain, «metadata») — этот сервер; пустая метка — не адрес."""
        p = self.p
        for host in ("localhost.", "127.0.0.1.", "LOCALHOST。", "metadata", "metadata.google.internal",
                     "instance-data.ec2.internal", "a.localhost.localdomain", "kaskad-node"):
            for proto, line in links(host).items():
                with self.subTest(host=host, proto=proto):
                    self.assertEqual(p.link_problem(line), "self_ep")
                    self.assertIsNone(p.xray_outbound(line, "t"))
        for host in ("localhost..", "a..example.com"):
            for proto, line in links(host).items():
                with self.subTest(host=host, proto=proto):
                    self.assertEqual(p.link_problem(line), "bad_link")
        for proto, line in links("srv.example.com.").items():          # чужой сервер с точкой в конце — годен
            with self.subTest(proto=proto):
                self.assertEqual(p.link_problem(line), "")

    def test_the_addresses_of_the_node(self):
        """REV-s1-rel П-1: свой публичный адрес узла (IPv4, IPv6, ::ffff-форма, числовая форма), его шлюз и DNS — тот же
        self_ep для hy2/ss/trojan/vmess, что у VLESS; без данных узла те же адреса — чужие серверы (тест держит именно
        адреса узла, а не петлю)."""
        own = ("203.0.113.5", "[::ffff:203.0.113.5]", "3405803781", "[2001:db8:5::5]", "203.0.113.1", "203.0.113.53")
        for host in own[:1] + own[3:]:
            for proto, line in links(host).items():
                with self.subTest(host=host, proto=proto, node="none"):
                    self.assertEqual(self.p.link_problem(line), "", "без данных узла адрес чужой")
        here = node(addrs=[("eth0", "203.0.113.5/24"), ("eth0", "2001:db8:5::5/64")], gws=["203.0.113.1"],
                    ns=["203.0.113.53"])
        with mock.patch.object(self.p, "_node_now", return_value=here):
            for host in own:
                for proto, line in links(host).items():
                    with self.subTest(host=host, proto=proto):
                        self.assertEqual(self.p.link_problem(line), "self_ep")
                        self.assertEqual(self.p.pool_problem(line), "self_ep")
                        self.assertIsNone(self.p.xray_outbound(line, "t"))
            for proto, line in links("203.0.113.6").items():            # сосед по сети узла — не узел
                with self.subTest(proto=proto, host="neighbour"):
                    self.assertEqual(self.p.link_problem(line), "")

    def test_numeric_form_not_of_the_node_is_bad_link(self):
        for proto, line in links("3405803777").items():
            with self.subTest(proto=proto):
                self.assertEqual(self.p.link_problem(line), "bad_link")

    def test_other_servers_pass(self):
        p = self.p
        for host in ("198.51.100.20", "srv.example.com"):
            for proto, line in links(host).items():
                with self.subTest(host=host, proto=proto):
                    self.assertEqual(p.link_problem(line), "")
                    self.assertTrue(p.xray_outbound(line, "t"))
        for name in ("hy2", "ss_sip002", "trojan_tls", "trojan_ws", "vmess_tls"):
            self.assertEqual(p.link_problem(FIX[name]), "", name)

    def test_build_config_leaves_them_out(self):
        good = links("198.51.100.20")["trojan"]
        cfg, n = self.p.build_config([links("127.0.0.1")["trojan"], good], 10808, "http://x/", "leastPing", "10s",
                                     100, 10851, 0)
        addrs = [o["settings"]["servers"][0]["address"] for o in cfg["outbounds"] if o.get("protocol") == "trojan"]
        self.assertEqual(addrs, ["198.51.100.20"])

    def test_gateway_words(self):
        line = links("127.0.0.1")["trojan"]
        rows = exits.preview(self.p, line)
        self.assertEqual(rows[0]["problem"], exits.word("problem.key_self_ep", proto=exits.proto_title("trojan")))
        self.assertIn("problem.key_self_ep", exits.DETAIL_CODES)
        self.assertNotEqual(exits.word("problem.key_self_ep", proto="Trojan"), "problem.key_self_ep")

    def test_codes_known_everywhere(self):
        for codes in (ops.SKIP_CODES, exitsubs.SKIP_CODES):
            self.assertIn("self_ep", codes)
            self.assertIn("gw_net", codes)
            self.assertIn("unsupported_scheme", codes)
        self.assertEqual(exits.PARSER_MIN, 28)
        self.assertEqual(self.p.PARSER_VERSION, 28)


if __name__ == "__main__":
    unittest.main()

"""Круг 1 правок по видео владельца (27.10), пакет Л2 «порт при установке протокола» (№14) — сервер панели.

GET /api/gw/inbounds/<kind>/port (ops.port_offer): поле окна «Установить …?» сразу с портом — рекомендованный протокола
(OpenVPN 1194), если свободен, иначе ближайший свободный; список рекомендованных с тем, что их держит; ``?port=N`` —
занят ли N (система по TCP/UDP протокола, другой протокол — и выключенный, служба Kaskad, проброс); ``?random=1`` —
случайный свободный. Чужой ввод — строго: число 1–65535 (в запросе и в теле установки/смены порта)."""

from kaskad import state
from kaskad.gw import ops
from tests.fakes_gw import register_dummies
from tests.panel.panelcase import PanelCase


def udp(port, prog="openvpn", pid=99):
    return 'udp   UNCONN 0      0      0.0.0.0:%d    0.0.0.0:*    users:(("%s",pid=%d,fd=8))\n' % (port, prog, pid)


def tcp(port, prog="nginx", pid=98):
    return 'tcp   LISTEN 0      128    0.0.0.0:%d    0.0.0.0:*    users:(("%s",pid=%d,fd=7))\n' % (port, prog, pid)


class PortOfferTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.gateway()
        register_dummies(("openvpn", {"title": "OpenVPN", "default_port": 1194}),
                         ("pptp", {"title": "PPTP", "proto": None, "fixed_ports": (("tcp", 1723),)}),
                         ("awg3", {"title": "AmneziaWG 3.1", "random_port": True}))

    def offer(self, kind="openvpn", q=""):
        return self.ok("GET", "/api/gw/inbounds/%s/port%s" % (kind, q))

    def test_the_field_comes_filled_with_the_recommended_port(self):
        doc = self.offer()                                              # без правки: 404 — пустое поле
        self.assertEqual((doc["port"], doc["protos"], doc["fixed"]), (1194, ["udp"], False))
        self.assertEqual(doc["recommended"], [{"port": 1194, "free": True, "why": ""}])

    def test_busy_recommended_gives_the_nearest_free_one(self):
        self.w.ss = udp(1194) + tcp(1195)                               # 1195/tcp OpenVPN по UDP не мешает
        doc = self.offer()
        self.assertFalse(doc["recommended"][0]["free"])
        self.assertIn("openvpn", doc["recommended"][0]["why"])
        self.assertEqual(doc["port"], 1195)

    def test_the_nearest_skips_other_protocols_ports(self):
        register_dummies(("socks5", {"title": "SOCKS5", "default_port": 1195, "proto": "tcp"}))
        self.w.ss = udp(1194)
        self.assertEqual(self.offer()["port"], 1193)                     # 1195 — рекомендованный SOCKS5

    def test_check_says_what_holds_the_port(self):
        self.ok("POST", "/api/forwards", {"proto": "udp", "port": 40001, "to": "198.51.100.7", "to_port": 1})
        chk = self.offer(q="?port=40001")["check"]
        self.assertEqual((chk["port"], chk["free"]), (40001, False))
        self.assertIn("198.51.100.7", chk["why"])
        self.assertTrue(self.offer(q="?port=40002")["check"]["free"])

    def test_a_switched_off_protocol_keeps_its_port(self):
        wg = state.load()["gateway"]["inbounds"]["wg"]["port"]
        self.ok("POST", "/api/gw/inbounds/wg/disable")
        chk = self.offer(q="?port=%d" % wg)["check"]
        self.assertFalse(chk["free"])                                    # назначенный, но не запущенный
        self.assertIn("выключен", chk["why"])

    def test_kaskad_service_ports_are_busy(self):
        for port, word in ((12345, "TPROXY"), (1980, "панель"), (5354, "dnsmasq"), (12450, "выходов")):
            chk = self.offer(q="?port=%d" % port)["check"]
            self.assertFalse(chk["free"], port)                          # без правки: 127.0.0.1 — «свободен»
            self.assertIn("служебный порт Kaskad", chk["why"])
            self.assertIn(word, chk["why"])
        # и установка сама откажет на нём (общая проверка ops.port_conflicts), а не только окно
        doc = self.err(409, "POST", "/api/gw/inbounds/openvpn/install", {"port": 12345})
        self.assertIn("служебный порт Kaskad", doc["error"])

    def test_random_gives_a_free_port(self):
        self.w.ss = udp(1194)
        for _ in range(5):
            p = self.offer(q="?random=1")["port"]
            self.assertTrue(ops.RANDOM_PORTS[0] <= p < ops.RANDOM_PORTS[1], p)
            self.assertTrue(self.offer(q="?port=%d" % p)["check"]["free"])

    def test_drawn_port_protocol_has_no_recommended(self):
        doc = self.offer("awg3")
        self.assertEqual(doc["recommended"], [])
        self.assertTrue(doc["port"])                                     # случайный свободный

    def test_fixed_ports_protocol_has_no_field(self):
        doc = self.offer("pptp")
        self.assertEqual((doc["fixed"], doc["port"], doc["recommended"]), (True, None, []))

    def test_kept_port_comes_back_or_is_said_busy(self):
        self.ok("POST", "/api/gw/inbounds/openvpn/install", {"port": 1300})
        self.ok("POST", "/api/gw/inbounds/openvpn/uninstall")
        doc = self.offer()
        self.assertEqual((doc["port"], doc["kept"], doc["kept_busy"]), (1300, 1300, ""))
        self.w.ss = udp(1300, "xl2tpd")
        doc = self.offer()
        self.assertEqual(doc["kept"], 1300)
        self.assertIn("xl2tpd", doc["kept_busy"])
        self.assertEqual(doc["port"], 1194)
        doc = self.ok("POST", "/api/gw/inbounds/openvpn/install", {"port": doc["port"]})
        self.assertEqual(doc["previous"]["changed"], ["порт 1300 → 1194"])   # окно потом скажет, кому выдать заново

    def test_installed_protocol_offers_its_own_port(self):
        wg = state.load()["gateway"]["inbounds"]["wg"]["port"]
        doc = self.offer("wg", "?port=%d" % wg)
        self.assertEqual(doc["port"], wg)
        self.assertTrue(doc["check"]["free"])                            # свой порт — не конфликт

    def test_the_offer_changes_nothing(self):
        before = state.load()
        self.offer(q="?port=40000")
        self.offer(q="?random=1")
        self.assertEqual(state.load(), before)


class StrictPortInputTest(PanelCase):
    """Чужой ввод: порт — целое 1–65535 (число или 1–5 цифр ASCII); int() пускал «+80», « 80», «8_0», «٨٠»."""

    def setUp(self):
        super().setUp()
        self.gateway()
        register_dummies(("openvpn", {"title": "OpenVPN", "default_port": 1194}))

    def test_query(self):
        for bad in ("abc", "0", "65536", "%2B80", "%2080", "8_0", "%D9%A8%D9%A0", "1194.0", "-1", "123456"):
            self.err(400, "GET", "/api/gw/inbounds/openvpn/port?port=" + bad)
        for bad in ("2", "yes", "true"):
            self.err(400, "GET", "/api/gw/inbounds/openvpn/port?random=" + bad)
        self.err(400, "GET", "/api/gw/inbounds/nope1/port")              # неизвестный протокол

    def test_install_and_port_bodies(self):
        for bad in ("8_0", " 80", "+80", True, 80.0, [80], {"p": 80}, 0, 70000, "٨٠"):
            self.err(400, "POST", "/api/gw/inbounds/openvpn/install", {"port": bad})
            self.err(400, "PUT", "/api/gw/inbounds/wg/port", {"port": bad})
        self.assertNotIn("openvpn", state.load()["gateway"]["inbounds"])   # ничего не поставилось
        self.assertEqual(self.ok("PUT", "/api/gw/inbounds/wg/port", {"port": "51999"})["port"], 51999)
        self.assertEqual(self.ok("POST", "/api/gw/inbounds/openvpn/install", {"port": 1194})["record"]["port"], 1194)

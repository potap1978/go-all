"""The §4 matrix: what each platform gets first, availability, plans per device, the app catalog."""

import unittest

from tests.gw.tunnel_case import device, gw_state
from tests.gwstub import Stubbed


class MatrixTest(unittest.TestCase):
    def setUp(self):
        self.stub = Stubbed().__enter__()
        self.addCleanup(self.stub.__exit__, None, None, None)
        self.m = self.stub.issue_os

    def first(self, platform, available=None):
        o = self.m.offers(platform, available)
        return (o[0]["inbound"], o[0]["format"]) if o else None

    def test_first_offer_per_platform(self):
        expect = {
            "ios": ("awg3", "vpn"), "macos": ("awg3", "vpn"), "android": ("awg3", "vpn"),
            "androidtv": ("awg3", "vpn"), "windows": ("awg3", "vpn"), "keenetic": ("awg2", "conf"),
            "mikrotik": ("wg", "conf"), "openwrt": ("awg2", "conf"), "tplink": ("l2tp", "guide"),
            "huawei": ("l2tp", "guide"), "xiaomi": ("l2tp", "guide"), "asus": ("l2tp", "guide"),
            "linux": ("wg", "conf"), "other": ("wg", "conf"),
        }
        self.assertEqual(sorted(expect), sorted(self.m.PLATFORMS))
        for p, first in expect.items():
            self.assertEqual(self.first(p), first, p)

    def test_more_rows_follow_the_spec(self):
        kinds = lambda p: [o["inbound"] for o in self.m.offers(p)]           # noqa: E731
        self.assertEqual(kinds("ios")[3:], ["vless", "hy2", "ikev2", "wg", "ocserv"])
        self.assertEqual(kinds("android")[3:], ["vless", "hy2", "wg", "ikev2", "openvpn"])
        self.assertEqual(kinds("windows")[3:], ["vless", "hy2", "ikev2", "sstp", "l2tp", "openvpn", "ocserv"])
        self.assertEqual(kinds("keenetic"), ["awg2", "awg1", "wg", "l2tp"])
        self.assertEqual(kinds("mikrotik"), ["wg", "l2tp", "sstp", "openvpn"])
        self.assertEqual(kinds("asus"), ["l2tp", "openvpn", "pptp"])
        self.assertEqual(kinds("linux"), ["wg", "awg3", "awg2", "awg1", "vless", "hy2", "openvpn", "ocserv", "socks5"])
        for p in ("keenetic", "mikrotik", "openwrt", "tplink", "huawei", "xiaomi", "asus"):   # routers: no client
            self.assertNotIn("vless", kinds(p), p)
        self.assertIn("5.1.1", self.m.offers("keenetic")[0]["note"])

    def test_availability_and_aliases(self):
        self.assertEqual(self.first("ios", ["wg", "awg2"]), ("awg2", "vpn"))
        self.assertEqual(self.first("keenetic", ["awg1", "wg"]), ("awg1", "conf"))
        self.assertIsNone(self.m.first("tplink", ["wg"]))
        self.assertEqual(self.m.platform("Android_TV"), "androidtv")
        self.assertEqual(self.m.platform("iPhone"), "ios")
        self.assertEqual(self.m.platform("смартфон"), "other")

    def test_plan_for_a_device(self):
        st = gw_state({"wg": {"enabled": True}, "awg2": {"enabled": True}, "awg3": {"enabled": False},
                       "openvpn": {"enabled": True}},
                      [device("d-1", "Телефон", "android", ["awg3", "awg2", "wg", "socks5"])])
        plan = self.m.plan(st, st["gateway"]["devices"][0])
        self.assertEqual(plan["platform"], "android")
        self.assertEqual([o["inbound"] for o in plan["offers"]], ["awg2", "wg"])  # awg3 off, socks5 not installed
        self.assertEqual([o["inbound"] for o in plan["missing"]], ["awg3", "awg1", "vless", "hy2", "ikev2", "openvpn"])
        plan = self.m.plan(st, st["gateway"]["devices"][0], os="linux")
        self.assertEqual([o["inbound"] for o in plan["offers"]], ["wg", "awg2"])
        self.assertEqual(self.m.plan(st, device("d-2", "ТВ", "androidtv", []))["notes"],
                         ["На ТВ нет камеры: ссылку vpn:// вставьте из буфера или загрузите файл .conf."])

    def test_app_catalog(self):
        ios = self.m.apps("ios", "3.1")
        self.assertEqual([a["name"] for a in ios][:2], ["AmneziaVPN", "AmneziaWG"])
        self.assertEqual(ios[0]["min"], "5.0.1")
        self.assertIn("vpn://", ios[0]["imports"])
        self.assertEqual(self.m.apps("keenetic", "3.1"), [])
        self.assertEqual([(a["name"], a["min"]) for a in self.m.apps("keenetic", "2.0")], [("Keenetic / Netcraze", "5.1.1")])
        self.assertEqual(self.m.apps("tplink", "2.0"), [])
        note = self.m.apps_note("windows", "3.1")
        self.assertTrue(note.startswith("Приложения: AmneziaVPN 5.0.1.5+ (vpn://"), note)
        self.assertNotIn("v2RayTun", note)                                       # no 3.1 there
        self.assertEqual(self.m.apps_note("huawei", "1.5"), "")

    def test_howto_four_steps_for_a_beginner(self):
        steps = self.m.howto("android", "awg3")
        self.assertEqual(len(steps), 4)
        self.assertIn("AmneziaVPN 5.0.1.5 или новее из Google Play", steps[0])
        self.assertIn("от vpn:// до конца", steps[1])
        self.assertIn("«Избранное» Telegram", steps[2])
        self.assertIn("на телефон", steps[2])
        self.assertEqual(steps[3], "В AmneziaVPN: «+» → «Вставить» (ключ из буфера) → «Продолжить», затем «Подключиться».")
        ios = self.m.howto("ios", "awg3")
        self.assertIn("DefaultVPN", ios[0])                               # AmneziaVPN is hidden in the Russian store
        self.assertIn("App Store", ios[0])
        self.assertIn("на компьютер", self.m.howto("windows", "awg2")[2])
        self.assertIn("импорт из файла", self.m.howto("keenetic", "awg2")[1])
        self.assertIn("Сохранить в файлы", self.m.howto("keenetic", "awg2")[0])
        self.assertIn("Создать из QR-кода", self.m.howto("ios", "wg")[1])
        self.assertIn("пустой туннель", self.m.howto("windows", "wg")[2])
        self.assertIn("OpenVPN Connect", self.m.howto("android", "openvpn")[0])
        for p, k in (("tplink", "l2tp"), ("linux", "awg3"), ("linux", "wg"), ("android", "ikev2"), ("linux", "openvpn")):
            self.assertEqual(self.m.howto(p, k), [], (p, k))
        for p in self.m.PLATFORMS:                                       # never longer than four short steps
            for k in self.m.TITLES:
                steps = self.m.howto(p, k)
                self.assertLessEqual(len(steps), 4, (p, k))
                self.assertTrue(all(len(s) <= 240 for s in steps), (p, k))

    def test_howto_built_in_clients_set_up_by_a_file(self):
        # T-G1 C1-1: Windows IKEv2/SSTP began with three screens of PowerShell — now the steps come first: save the
        # script, PowerShell as administrator, the command, where to connect (the login and the password are below)
        for kind, name in (("ikev2", "Kaskad IKEv2"), ("sstp", "Kaskad SSTP"), ("l2tp", "Kaskad L2TP"),
                           ("pptp", "Kaskad PPTP")):
            steps = self.m.howto("windows", kind)
            self.assertEqual(len(steps), 4, kind)
            self.assertIn("Сохранить в файлы", steps[0])
            self.assertIn("kaskad-%s.ps1" % kind, steps[0])
            self.assertIn("администратор", steps[1])
            self.assertEqual(steps[2], "Выполните: powershell -ExecutionPolicy Bypass -File .\\kaskad-%s.ps1" % kind)
            self.assertIn("«%s» → «Подключиться»" % name, steps[3])
            self.assertIn("«Данные для подключения»", steps[3])
            self.assertLessEqual(len(" 3. " + steps[2]), 78)                 # the command fits one line of 80×24
        ios, mac = self.m.howto("ios", "ikev2"), self.m.howto("macos", "ikev2")
        self.assertIn("kaskad-ikev2.mobileconfig", ios[0])
        self.assertIn("«Профиль загружен» → «Установить»", ios[2])
        self.assertIn("→ «Установить»", mac[1])
        self.assertIn("VPN → «Kaskad IKEv2»", mac[2])
        for kind in ("wg", "l2tp", "sstp", "pptp"):
            steps = self.m.howto("mikrotik", kind)
            self.assertIn("192.168.88.0/24", steps[0])
            self.assertIn("/import file-name=kaskad-%s.rsc" % kind, steps[1])
            self.assertIn("Interfaces → kaskad-%s" % kind, steps[2])
        # the steps need the file: an issuance without it (Windows L2TP while IPsec is off: fields only) gets none
        self.assertEqual(self.m.howto("windows", "l2tp", []), [])
        self.assertEqual(self.m.howto("windows", "l2tp", ["kaskad-l2tp.ps1"]), self.m.howto("windows", "l2tp"))
        self.assertEqual(self.m.howto("mikrotik", "wg", ["kaskad-wg.conf"]), [])
        self.assertEqual(self.m.howto("android", "awg3", []), self.m.howto("android", "awg3"))  # no file needed

    def test_offer_notes_do_not_repeat_the_protocol(self):
        # T-G1 C1-4: the access picker shows «<protocol> — <note>»: «PPTP — PPTP — только если протокол включён»
        import re
        for p in self.m.PLATFORMS:
            for o in self.m.offers(p):
                title = self.m.TITLES[o["inbound"]]
                self.assertIsNone(re.match(re.escape(title) + r"\s*([:,]|—|$)", o["note"]), (p, o))
        tp = dict((o["inbound"], o["note"]) for o in self.m.offers("tplink"))
        self.assertEqual(tp["pptp"], "только если протокол включён")
        self.assertIn("без IPsec", tp["l2tp"])

    def test_howto_in_the_words_of_each_screen(self):
        """T-G1b N4: the panel showed the menu's steps — «выделите её мышью (в PuTTY…)», «Сохранить в файлы в конце
        этого экрана», «gokaskad → 17 → 1 → 5 → 2». The same steps for each screen in its own words: the menu (the
        default — its texts did not change), the panel («Скопировать», «Скачать»), the person's page («Скачать
        файл»), the bot (a link is a message, a file is a document)."""
        menu_words = ("PuTTY", "Cmd+C", "Сохранить в файлы", "этого экрана", "Termius", "WinSCP", "gokaskad")
        for w in ("panel", "page", "bot"):
            for p in self.m.PLATFORMS:
                for k in self.m.TITLES:
                    steps = self.m.howto(p, k, where=w)
                    self.assertEqual(len(steps), len(self.m.howto(p, k)), (w, p, k))      # the same flow
                    self.assertTrue(all(len(s) <= 240 for s in steps), (w, p, k))
                    for word in menu_words:
                        self.assertNotIn(word, " ".join(steps), (w, p, k, word))
            for p, k in (("tplink", "l2tp"), ("huawei", "l2tp")):
                self.assertNotIn("gokaskad → 17 → 1 → 5 (L2TP/IPsec)", " ".join(self.m.steps(p, k, where=w)))
            self.assertNotIn("inbounds.l2tp.plain", self.m.plain_off_note(w))
        panel = self.m.howto("android", "awg3", where="panel")
        self.assertEqual(panel[1],
                         "Скопируйте ссылку vpn:// ниже кнопкой «Скопировать» — она берёт всю ссылку, до конца.")
        self.assertEqual(self.m.howto("windows", "ikev2", where="panel")[0],
                         "Скачайте скрипт kaskad-ikev2.ps1 кнопкой «Скачать» ниже.")
        self.assertEqual(self.m.howto("keenetic", "awg2", where="page")[0],
                         "Скачайте файл кнопкой «Скачать файл» ниже.")
        self.assertIn("нажмите на неё — Telegram скопирует", self.m.howto("android", "awg3", where="bot")[1])
        self.assertIn("«Импорт туннелей из файла»", self.m.howto("windows", "wg", where="bot")[2])   # a document
        self.assertIn("«Протоколы» → L2TP/IPsec → «L2TP без шифрования»",
                      self.m.steps("tplink", "l2tp", where="panel")[2])
        self.assertIn("попросите того, кто прислал вам ссылку", self.m.steps("tplink", "l2tp", where="page")[2])
        # the default is the menu, and ops.issue sets the reader for the guides the adapters build
        self.assertEqual(self.m.howto("android", "awg3"), self.m.howto("android", "awg3", where="menu"))
        self.assertIn("PuTTY", self.m.howto("android", "awg3")[1])
        with self.m.issued_for("panel"):
            self.assertIn("«Протоколы»", self.m.steps("tplink", "l2tp")[2])
            self.assertIn("«Протоколы»", self.m.plain_off_note())
            self.assertEqual(self.m.howto("windows", "sstp"), self.m.howto("windows", "sstp", where="panel"))
            with self.m.issued_for("nobody"):                              # an unknown reader: the menu
                self.assertIn("gokaskad → 17", self.m.steps("tplink", "l2tp")[2])
        self.assertIn("gokaskad → 17 → 1 → 5 (L2TP/IPsec) → 2", self.m.steps("tplink", "l2tp")[2])

    def test_tplink_says_where_to_look_and_where_to_switch_plain_l2tp(self):
        steps = self.m.steps("tplink", "l2tp")
        self.assertIn("VPN-клиент", steps[0])
        self.assertIn("этот роутер не подойдёт", steps[0])                # many TP-Link have no VPN client at all
        self.assertNotIn("прошивк", " ".join(steps))                      # nobody knows their firmware
        self.assertIn("gokaskad → 17 → 1 → 5 (L2TP/IPsec) → 2 (L2TP без шифрования)", steps[2])
        self.assertIn("роутер не подойдёт", self.m.NOTES["tplink"][0])


class MenuPathTest(unittest.TestCase):
    def test_plain_l2tp_path_follows_the_order_of_the_protocols_screen(self):
        from kaskad.gw import issue_os, model
        self.assertEqual(model.INBOUND_KINDS.index("l2tp") + 1, 5)          # «Протоколы» lists them in this order
        self.assertTrue(issue_os.L2TP_PLAIN_PATH.startswith("17 → 1 → 5 (L2TP/IPsec) → 2 "))

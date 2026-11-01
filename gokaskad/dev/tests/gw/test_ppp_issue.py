"""Issuance of L2TP, PPTP, SSTP and IKEv2 per system: PowerShell for Windows (ASCII, no device password),
the .mobileconfig (a valid plist with the CA and the IKEv2 payload), Android 12+ fields, RouterOS scripts,
router guides, warnings (plain L2TP, PPTP), refusals; secrets never reach the log."""

import plistlib

from tests.gw.ppp_case import INBOUNDS, PppCase, device, gw_state
from tests.gw.tunnel_fakes import fixture

PSK = "PskOfTheNode23456789abcd"


def devices():
    return [
        device("d-win", "Ноутбук Мамы", "windows", ["l2tp", "pptp", "sstp", "ikev2"], login="noutbuk-3c4d", kind="laptop"),
        device("d-iphone", "Айфон", "ios", ["ikev2", "l2tp"], login="ayfon-1a2b"),
        device("d-mac", "Макбук", "macos", ["ikev2", "l2tp"], login="makbuk-9f8e", kind="laptop"),
        device("d-android", "Пиксель", "android", ["ikev2", "l2tp", "sstp"], login="piksel-5d6c"),
        device("d-mt", "MikroTik", "mikrotik", ["l2tp", "sstp", "pptp"], login="mikrotik-7b7b", kind="router"),
        device("d-tp", "TP-Link", "tplink", ["l2tp", "pptp"], login="tplink-1c1c", kind="router"),
        device("d-xi", "Xiaomi", "xiaomi", ["l2tp"], login="xiaomi-2d2d", kind="router"),
        device("d-lin", "Сервер", "linux", ["ikev2", "sstp", "l2tp"], login="server-3e3e", kind="pc"),
    ]


class IssueCase(PppCase):
    def setUp(self):
        super().setUp()
        self.st = gw_state(INBOUNDS, devices())
        self.sec["l2tp.psk"] = PSK
        for d in self.st["gateway"]["devices"]:
            self.sec["dev.%s.password" % d["id"]] = "Pass%sX7" % d["login"].split("-")[0].capitalize()
        self.ad = dict((k, self.adapter(k)) for k in ("l2tp", "pptp", "sstp", "ikev2"))
        for k in ("l2tp", "sstp", "ikev2"):
            self.ad[k].prepare(self.st, self.sec, self.runner)

    def issue(self, kind, dev_id, os=None):
        return self.ad[kind].issue(self.st, self.sec, self.dev(self.st, dev_id), os)

    def item(self, res, fmt):
        return [i for i in res["items"] if i["format"] == fmt][0]

    def pw(self, dev_id):
        return self.sec["dev.%s.password" % dev_id]


class WindowsTest(IssueCase):
    def check_ps1(self, body, dev_id):
        body.encode("ascii")                                                             # PowerShell 5.1 safe
        self.assertNotIn(self.pw(dev_id), body)                                          # Windows asks for it
        self.assertIn("$ErrorActionPreference = 'Stop'", body)
        self.assertIn("-AllUserConnection -Name $name", body)

    def test_ikev2_script(self):
        res = self.issue("ikev2", "d-win")
        self.assertEqual([i["format"] for i in res["items"]], ["ps1", "fields"])
        body = res["body"]
        self.check_ps1(body, "d-win")
        self.assertIn("$server = '203.0.113.10'", body)
        self.assertIn(fixture("ikev2-ca-ip.crt").strip().splitlines()[1], body)          # the CA, imported
        self.assertNotIn("Remove-Item -Path", body)                                      # no earlier CA to drop
        notes = " ".join(res["notes"])
        self.assertIn("подтверждает только этот сервер (203.0.113.10)", notes)
        self.assertIn("Cert:\\LocalMachine\\Root", body)
        self.assertIn("-TunnelType Ikev2 -AuthenticationMethod Eap -EncryptionLevel Required", body)
        self.assertIn("-AuthenticationTransformConstants GCMAES256 -CipherTransformConstants GCMAES256 "
                      "-EncryptionMethod AES256 -IntegrityCheckMethod SHA256 -DHGroup Group14 -PfsGroup None", body)
        self.assertEqual(res["filename"], "kaskad-ikev2.ps1")
        self.assertIn(["Пароль", self.pw("d-win")], res["items"][1]["body"])
        self.golden("ikev2-windows.ps1", body)

    def test_l2tp_script_carries_the_psk_in_a_variable(self):
        res = self.issue("l2tp", "d-win")
        body = res["body"]
        self.check_ps1(body, "d-win")
        self.assertIn("$psk = '%s'" % PSK, body)
        self.assertEqual(body.count(PSK), 1)
        self.assertIn("-TunnelType L2tp -AuthenticationMethod MSChapv2 -EncryptionLevel Required -RememberCredential "
                      "-Force -L2tpPsk $psk", body)
        self.assertIn("AssumeUDPEncapsulationContextOnSendRule -PropertyType DWord -Value 2", body)
        self.assertIn(["Общий ключ IPsec (PSK)", PSK], res["items"][1]["body"])

    def test_sstp_and_pptp_scripts(self):
        res = self.issue("sstp", "d-win")
        body = res["body"]
        self.check_ps1(body, "d-win")
        self.assertIn("$server = '203.0.113.10:4433'", body)
        self.assertIn("NoCertRevocationCheck -PropertyType DWord -Value 1", body)
        self.assertIn("на все подключения SSTP этого компьютера", " ".join(res["notes"]))
        self.assertIn(fixture("ocserv.crt").strip().splitlines()[1], body)               # the node's certificate
        fields = dict(self.item(res, "fields")["body"])
        self.assertEqual(fields["Отпечаток сертификата (SHA-256)"],
                         self.gw.pki.sha256_fingerprint(fixture("ocserv.crt")))
        res = self.issue("pptp", "d-win")
        self.check_ps1(res["body"], "d-win")
        self.assertIn("-TunnelType Pptp -AuthenticationMethod MSChapv2 -EncryptionLevel Required", res["body"])
        self.assertIn("слабое шифрование", " ".join(self.item(res, "fields")["notes"]))
        fields = dict(self.item(res, "fields")["body"])
        pptp_pw = self.sec["dev.d-win.pptp.password"]
        self.assertEqual((fields["Логин"], fields["Пароль"]), ("noutbuk-3c4d-pptp", pptp_pw))   # its own account
        self.assertNotEqual(pptp_pw, self.pw("d-win"))
        self.assertNotIn(self.pw("d-win"), str(res))                                   # the shared one never
        self.assertIn("свои логин и пароль", " ".join(self.item(res, "fields")["notes"]))


    def test_steps_only_when_the_script_is_issued(self):
        # the menu's steps for Windows (T-G1 C1-1: «Сохраните скрипт kaskad-l2tp.ps1…») go with the script; L2TP
        # without IPsec has no script (fields only) — and no steps about one
        from kaskad.gw import issue_os
        files = lambda res: [i["filename"] for i in res["items"] if i["filename"]]          # noqa: E731
        res = self.issue("l2tp", "d-win")
        self.assertEqual(issue_os.howto("windows", "l2tp", files(res))[2],
                         "Выполните: powershell -ExecutionPolicy Bypass -File .\\kaskad-l2tp.ps1")
        self.st["gateway"]["inbounds"]["l2tp"].update({"ipsec": False, "plain": True})
        res = self.issue("l2tp", "d-win")
        self.assertEqual([i["format"] for i in res["items"]], ["fields"])
        self.assertEqual(issue_os.howto("windows", "l2tp", files(res)), [])
        for kind in ("ikev2", "sstp", "pptp"):                                           # always with the script
            self.assertEqual(len(issue_os.howto("windows", kind, files(self.issue(kind, "d-win")))), 4, kind)

    def test_ikev2_server_check_names_what_the_platform_gets(self):
        # stand T-G2b, N-5: Windows gets no kaskad-ikev2-ca.crt (the script trusts the CA) — the field must not send
        # the person after a file he has not got; iOS/macOS — the profile; the others — the .crt they are given
        def check(dev_id):
            res = self.issue("ikev2", dev_id)
            names = [i["filename"] for i in res["items"] if i["filename"]]
            return dict(self.item(res, "fields")["body"])["Проверка сервера"], names
        text, names = check("d-win")
        self.assertNotIn("kaskad-ikev2-ca.crt", names)
        self.assertEqual(text, "сертификат центра Kaskad — внутри скрипта kaskad-ikev2.ps1, отдельного файла не нужно")
        self.assertIn("kaskad-ikev2.ps1", names)
        for dev_id in ("d-iphone", "d-mac"):
            text, names = check(dev_id)
            self.assertEqual(text, "сертификат центра Kaskad — внутри профиля kaskad-ikev2.mobileconfig", dev_id)
            self.assertIn("kaskad-ikev2.mobileconfig", names)
        for dev_id in ("d-android", "d-lin"):
            text, names = check(dev_id)
            self.assertEqual(text, "сертификат центра Kaskad, файл kaskad-ikev2-ca.crt", dev_id)
            self.assertIn("kaskad-ikev2-ca.crt", names)

    def test_step_texts_in_the_words_of_every_screen(self):
        # stand T-G2b, N-5: MikroTik step 1 is a condition («Если сеть роутера не …»), Windows step 2 names the
        # command — in the menu, the panel, the page and the bot alike
        from kaskad.gw import issue_os
        for where in issue_os.WHERE:
            for kind in ("l2tp", "sstp", "pptp", "wg"):
                text = " ".join(issue_os.howto("mikrotik", kind, where=where))
                self.assertIn("Если сеть роутера не 192.168.88.0/24 (так у MikroTik по умолчанию), замените её в "
                              "скрипте на свою.", text, (where, kind))
                self.assertNotIn(" Сеть роутера не", text, (where, kind))
            for kind in ("ikev2", "sstp", "l2tp", "pptp"):
                steps = issue_os.howto("windows", kind, where=where)
                self.assertTrue(steps[1].endswith("перейдите в папку со скриптом командой cd <путь к папке>."),
                                (where, kind, steps[1]))
                self.assertNotIn("cd и путь", " ".join(steps), (where, kind))


class AppleAndroidTest(IssueCase):
    def test_mobileconfig_is_a_valid_profile(self):
        res = self.issue("ikev2", "d-iphone")
        self.assertEqual((res["format"], res["filename"]), ("mobileconfig", "kaskad-ikev2.mobileconfig"))
        doc = plistlib.loads(res["body"].encode("utf-8"))
        self.assertEqual(doc["PayloadType"], "Configuration")
        ca, vpn = doc["PayloadContent"]
        self.assertEqual(ca["PayloadType"], "com.apple.security.root")
        self.assertEqual(ca["PayloadContent"], self.gw.pki.pem_der(fixture("ikev2-ca-ip.crt")))
        self.assertEqual(vpn["PayloadType"], "com.apple.vpn.managed")
        ike = vpn["IKEv2"]
        self.assertEqual((ike["RemoteAddress"], ike["RemoteIdentifier"], ike["LocalIdentifier"]),
                         ("203.0.113.10", "203.0.113.10", "ayfon-1a2b"))
        self.assertEqual((ike["AuthenticationMethod"], ike["ExtendedAuthEnabled"], ike["AuthName"], ike["AuthPassword"]),
                         ("None", 1, "ayfon-1a2b", self.pw("d-iphone")))
        self.assertEqual(ike["IKESecurityAssociationParameters"],
                         {"EncryptionAlgorithm": "AES-256", "IntegrityAlgorithm": "SHA2-256", "DiffieHellmanGroup": 14,
                          "LifeTimeInMinutes": 1440})
        self.assertEqual(ike["ServerCertificateIssuerCommonName"], "Kaskad IKEv2 CA 203.0.113.10 7f3a1c")
        self.assertEqual(ca["PayloadDisplayName"], "Kaskad IKEv2 CA 203.0.113.10 7f3a1c")
        self.assertEqual(vpn["IPv4"], {"OverridePrimary": 1})
        again = plistlib.loads(self.issue("ikev2", "d-iphone")["body"].encode("utf-8"))
        self.assertEqual(again["PayloadUUID"], doc["PayloadUUID"])                      # a re-issue replaces it
        other = plistlib.loads(self.issue("ikev2", "d-mac")["body"].encode("utf-8"))
        self.assertNotEqual(other["PayloadUUID"], doc["PayloadUUID"])
        self.assertIn("Профили", " ".join(self.issue("ikev2", "d-mac")["notes"]))

    def test_apple_l2tp_is_built_in_android_has_none(self):
        res = self.issue("l2tp", "d-iphone")
        self.assertEqual([i["format"] for i in res["items"]], ["guide", "fields"])
        self.assertIn("VPN и управление устройством", res["body"])
        res = self.issue("l2tp", "d-android")
        self.assertEqual([i["format"] for i in res["items"]], ["fields"])
        self.assertIn("Android 12 и новее не создаёт подключения PPTP и L2TP", res["notes"][0])

    def test_android_ikev2_fields_and_ca_file(self):
        res = self.issue("ikev2", "d-android")
        self.assertEqual([i["format"] for i in res["items"]], ["guide", "conf", "fields"])
        self.assertIn("IKEv2/IPSec MSCHAPv2", res["body"])
        ca = res["items"][1]
        self.assertEqual((ca["filename"], ca["body"]), ("kaskad-ikev2-ca.crt", fixture("ikev2-ca-ip.crt")))
        self.assertIn("подтверждает только этот сервер", " ".join(ca["notes"]))
        fields = dict(res["items"][2]["body"])
        self.assertEqual((fields["Сервер"], fields["Логин (EAP)"]), ("203.0.113.10", "piksel-5d6c"))

    def test_android_sstp_app_and_apple_without_sstp(self):
        res = self.issue("sstp", "d-android")
        self.assertIn("Open SSTP Client", res["body"])
        res = self.issue("sstp", "d-android", "macos")                                   # the OS chosen at issuance
        self.assertIn("Встроенного SSTP в macOS нет", self.item(res, "fields")["notes"][0])


class RoutersTest(IssueCase):
    def test_mikrotik_scripts(self):
        res = self.issue("l2tp", "d-mt")
        self.assertEqual((res["format"], res["filename"]), ("conf", "kaskad-l2tp.rsc"))
        self.golden("l2tp-mikrotik.rsc", res["body"])
        self.assertIn('use-ipsec=yes ipsec-secret="%s"' % PSK, res["body"])
        res = self.issue("sstp", "d-mt")
        self.assertIn("/interface sstp-client add name=kaskad-sstp connect-to=203.0.113.10 port=4433", res["body"])
        self.assertIn("verify-server-certificate=no", res["body"])
        self.assertEqual([i["format"] for i in res["items"]], ["conf", "fields", "conf"])
        res = self.issue("pptp", "d-mt")
        self.assertIn("/interface pptp-client add name=kaskad-pptp", res["body"])
        self.assertIn("allow=mschap2", res["body"])
        self.assertIn('user="mikrotik-7b7b-pptp" password="%s"' % self.sec["dev.d-mt.pptp.password"], res["body"])

    def test_router_guides_and_plain_l2tp(self):
        res = self.issue("l2tp", "d-tp")
        self.assertEqual([i["format"] for i in res["items"]], ["guide", "fields"])
        self.assertIn("VPN-клиент", res["body"])
        res = self.issue("l2tp", "d-xi")
        notes = " ".join(self.item(res, "fields")["notes"])
        self.assertIn("только без IPsec, а на узле он выключен", notes)
        self.st["gateway"]["inbounds"]["l2tp"]["plain"] = True
        res = self.issue("l2tp", "d-xi")
        notes = " ".join(self.item(res, "fields")["notes"])
        self.assertIn("L2TP без IPsec включён", notes)
        self.assertNotIn("на узле он выключен", notes)
        self.assertIn(["Тип", "L2TP/IPsec с общим ключом (или L2TP без IPsec)"], self.item(res, "fields")["body"])
        self.st["gateway"]["inbounds"]["l2tp"].update({"ipsec": False})
        fields = self.item(self.issue("l2tp", "d-xi"), "fields")["body"]
        self.assertEqual(fields[1], ["Тип", "L2TP без IPsec"])
        self.assertNotIn(PSK, str(fields))

    def test_pptp_router_guide_warns(self):
        res = self.issue("pptp", "d-tp")
        self.assertIn("PPTP", res["body"])
        self.assertIn("слабое шифрование", " ".join(self.item(res, "fields")["notes"]))


class LinuxTest(IssueCase):
    def test_commands(self):
        res = self.issue("ikev2", "d-lin")
        self.assertEqual(res["body"], "sudo charon-cmd --host 203.0.113.10 --remote-identity 203.0.113.10 "
                                      "--identity server-3e3e --cert kaskad-ikev2-ca.crt --profile ikev2-eap")
        res = self.issue("sstp", "d-lin")
        pw = self.pw("d-lin")
        self.assertEqual(res["body"], "sudo install -m 600 kaskad-sstp.options /etc/ppp/kaskad-sstp.options\n"
                                      "sudo sstpc --ca-cert kaskad-sstp.crt 203.0.113.10:4433 file "
                                      "/etc/ppp/kaskad-sstp.options require-mschap-v2 noauth usepeerdns")
        self.assertNotIn(pw, res["body"])                                                # not in argv, not in history
        opts = res["items"][1]
        self.assertEqual((opts["format"], opts["filename"]), ("conf", "kaskad-sstp.options"))
        self.assertIn('\nuser "server-3e3e"\npassword "%s"\n' % pw, opts["body"])
        self.assertIn("права 600", " ".join(res["notes"]))
        self.assertEqual(self.gw.sstp.options_text('a"b', "p\\w"), "# Kaskad SSTP: login and password for pppd "
                         "(sstpc ... file /etc/ppp/kaskad-sstp.options)\n# Keep it root-only (chmod 600): the password "
                         "is in it.\nuser \"a\\\"b\"\npassword \"p\\\\w\"\n")
        res = self.issue("l2tp", "d-lin")
        self.assertIn("network-manager-l2tp", res["body"])


class RefusalTest(IssueCase):
    def test_not_granted_disabled_or_not_ready(self):
        E = self.gw.base.InboundError
        with self.assertRaises(E):
            self.issue("pptp", "d-iphone")                                               # no pptp access
        self.dev(self.st, "d-win")["enabled"] = False
        for kind in ("l2tp", "pptp", "sstp", "ikev2"):
            with self.assertRaises(E):
                self.issue(kind, "d-win")
        del self.sec["ikev2.ca.crt"]
        with self.assertRaises(E) as cm:
            self.issue("ikev2", "d-iphone")
        self.assertIn("kaskadctl gw apply", str(cm.exception))
        self.st["gateway"]["inbounds"]["l2tp"].update({"ipsec": False, "plain": False})
        with self.assertRaises(E):
            self.issue("l2tp", "d-tp")

    def test_secrets_stay_out_of_the_log(self):
        self.installed("l2tp", "pptp", "sstp", "ikev2")
        for kind in ("l2tp", "pptp", "sstp", "ikev2"):
            self.ad[kind].apply(self.st, self.sec, self.runner)
            for d in self.st["gateway"]["devices"]:
                if kind in d["access"]:
                    try:
                        self.issue(kind, d["id"])
                    except self.gw.base.InboundError:
                        pass
            self.ad[kind].status(self.runner)
        self.assert_no_secrets(self.log_text())

"""strongSwan of L2TP/IPsec and IKEv2: packages without autostart, our unit instead of strongswan.service,
the RSA PKI, golden swanctl.conf, ``swanctl --load-all`` without dropping SAs, SAs that lost their right
terminated one by one, the shared UFW ports, status from list-sas, the updown hook."""

import json
import os

from kaskad import paths
from tests.gw.ppp_case import INBOUNDS, PppCase, device, gw_state, ike_sa
from tests.gw.tunnel_fakes import FAKE_KEY, fixture

UNIT = "kaskad-gw-ipsec.service"


def devices():
    return [device("d-iphone", "Айфон", "ios", ["ikev2"], login="ayfon-1a2b"),
            device("d-win", "Ноутбук", "windows", ["ikev2", "l2tp"], login="noutbuk-3c4d", kind="laptop"),
            device("d-router", "Роутер на даче", "keenetic", ["l2tp"], login="dacha-5e6f", kind="router"),
            device("d-off", "Старый телефон", "android", ["ikev2"], login="old-phone", enabled=False)]


class IpsecCase(PppCase):
    def setUp(self):
        super().setUp()
        self.ips = self.gw.ipsec
        inbounds = dict((k, dict(INBOUNDS[k])) for k in ("l2tp", "ikev2"))
        self.st = gw_state(inbounds, devices())
        self.sec.update({"dev.d-iphone.password": "IphonePass2345", "dev.d-win.password": "WinPass6789xyz",
                         "dev.d-router.password": "RouterPass2345", "l2tp.psk": "PskOfTheNode23456789abcd"})
        self.l2tp, self.ikev2 = self.adapter("l2tp"), self.adapter("ikev2")

    def ready(self):
        self.installed("l2tp", "ikev2")
        self.l2tp.apply(self.st, self.sec, self.runner)
        self.ikev2.apply(self.st, self.sec, self.runner)
        self.runner.calls[:] = []
        self.node.sysd.calls[:] = []

    def conf(self):
        return self.read("/etc/swanctl/conf.d/kaskad.conf")

    def swanctl_calls(self):
        return [a[1:] for a, _ in self.runner.calls if os.path.basename(a[0]) == "swanctl"]


class InstallTest(IpsecCase):
    def test_packages_without_autostart_and_our_unit(self):
        self.ikev2.install(self.runner)
        self.assertEqual(self.node.apt, [(self.ips.PACKAGES, True)])
        self.assertEqual(self.node.sysd.verbs("strongswan.service"), ["is-active", "is-enabled", "disable --now", "mask"])
        self.assertEqual(self.node.modprobed, ["esp4", "xfrm_user"])
        self.golden("kaskad-gw-ipsec.service", self.read("/etc/systemd/system/kaskad-gw-ipsec.service"))
        self.assertEqual(self.ips.installed_kinds(), ["ikev2"])
        self.node.apt[:] = []
        self.ikev2.install(self.runner)                                                  # idempotent
        self.assertEqual(self.node.apt, [])

    def test_foreign_ike_daemon_and_kernel_without_ipsec_are_refused(self):
        self.node.sysd.u("strongswan-starter.service")["active"] = True
        with self.assertRaises(self.gw.base.InboundError) as cm:
            self.ikev2.install(self.runner)
        self.assertIn("strongswan-starter.service", str(cm.exception))
        self.assertFalse(self.ikev2.requirements(self.runner)["supported"])
        self.assertIn("strongswan-starter.service", self.l2tp.requirements(self.runner)["why"])
        self.node.sysd.u("strongswan-starter.service")["active"] = False
        self.node.modules.discard("esp4")
        with self.assertRaises(self.gw.base.InboundError) as cm:
            self.ikev2.install(self.runner)
        self.assertIn("нет IPsec (нет модулей: esp4)", str(cm.exception))
        self.assertEqual(self.node.apt, [])

    def test_enabled_foreign_daemon_would_start_at_boot(self):
        self.node.sysd.u("strongswan.service")["enabled"] = True                         # the system's, not ours
        with self.assertRaises(self.gw.base.InboundError) as cm:
            self.ikev2.install(self.runner)
        self.assertIn("systemctl disable --now strongswan.service", str(cm.exception))

    def test_our_own_strongswan_package_is_not_foreign(self):
        self.ikev2.install(self.runner)
        self.node.sysd.u("strongswan.service")["active"] = True                          # e.g. started by hand
        self.assertEqual(self.ips.foreign_units(self.runner), [])

    def test_charon_without_the_packet_copying_plugins(self):
        # стенд T-O, O-10: dhcp и farp копировали каждый пакет узла в userspace — 15–50 % ЦП при любом трафике
        self.ready()
        text = open(paths.P("/etc/strongswan.d/kaskad-gw.conf"), encoding="utf-8").read()
        self.assertIn("charon-systemd {", text)
        for name in ("dhcp", "farp", "forecast"):
            self.assertIn("%s {\n            load = no" % name, text)

    def test_uninstall_keeps_the_engine_for_the_other_inbound(self):
        self.node.ufw_active = True
        self.ready()
        self.ikev2.uninstall(self.runner)
        self.assertTrue(os.path.exists(os.path.join(paths.systemd_dir(), UNIT)))
        self.assertIn("allow 500/udp comment kaskad-gw ipsec", self.node.ufw)
        self.assertNotIn("delete allow 500/udp", self.node.ufw)                          # L2TP/IPsec still needs it
        self.l2tp.uninstall(self.runner)
        self.assertFalse(os.path.exists(os.path.join(paths.systemd_dir(), UNIT)))
        self.assertEqual(self.node.ufw[-4:], ["delete allow 1701/udp", "delete allow 500/udp", "delete allow 4500/udp",
                                              "delete allow proto esp from any to any"])
        self.assertFalse(os.path.exists(paths.P("/etc/swanctl/conf.d/kaskad.conf")))
        self.assertFalse(os.path.exists(paths.P("/etc/swanctl/private/kaskad-ikev2.pem")))
        self.assertFalse(os.path.exists(paths.P("/etc/strongswan.d/kaskad-gw.conf")))     # плагины — вместе с движком
        self.assertEqual(self.node.sysd.verbs("strongswan.service")[-1], "unmask")
        self.assertIn("ikev2.ca.key", self.sec)                                          # the CA stays for a reinstall


class PkiRenderTest(IpsecCase):
    def test_rsa_ca_and_server_certificate_for_the_host(self):
        done = self.ips.prepare(self.st, self.sec, self.runner)
        self.assertEqual(done, ["центр сертификации IKEv2 для 203.0.113.10", "сертификат сервера IKEv2 для 203.0.113.10"])
        ca_cnf, srv_cnf = self.node.ssl.configs
        self.assertRegex(ca_cnf, r"\nCN = Kaskad IKEv2 CA 203\.0\.113\.10 [0-9a-f]{6}\n")            # a name per node
        self.assertIn("basicConstraints = critical, CA:TRUE, pathlen:0", ca_cnf)
        self.assertIn("nameConstraints = critical, permitted;IP:203.0.113.10/255.255.255.255, "
                      "permitted;DNS:203.0.113.10", ca_cnf)
        self.assertIn("extendedKeyUsage = serverAuth, 1.3.6.1.5.5.8.2.2", ca_cnf)          # no other purpose
        self.assertIn("extendedKeyUsage = serverAuth, 1.3.6.1.5.5.8.2.2", srv_cnf)
        self.assertIn("keyUsage = critical, digitalSignature, keyEncipherment", srv_cnf)
        self.assertIn("IP.1 = 203.0.113.10", srv_cnf)
        genpkey = [a for a in self.node.commands("openssl") if a[1] == "genpkey"]
        self.assertEqual([a[2:6] for a in genpkey], [["-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"]] * 2)
        self.assertEqual(self.st["gateway"]["inbounds"]["ikev2"]["cert_host"], "203.0.113.10")
        self.assertEqual(self.ips.prepare(self.st, self.sec, self.runner), [])           # nothing new
        old_ca = self.sec["ikev2.ca.crt"]
        self.st["gateway"]["host"] = "vpn.example.org"
        self.assertEqual(self.ips.prepare(self.st, self.sec, self.runner),
                         ["центр сертификации IKEv2 для vpn.example.org (адрес узла сменился — выдайте устройствам "
                          "IKEv2 настройки заново)", "сертификат сервера IKEv2 для vpn.example.org"])
        self.assertIn("nameConstraints = critical, permitted;DNS:vpn.example.org, permitted;IP:0.0.0.0/255.255.255.255",
                      self.node.ssl.configs[-2])
        self.assertIn("DNS.1 = vpn.example.org", self.node.ssl.configs[-1])
        self.assertEqual(self.ips.ca_scope(self.sec["ikev2.ca.crt"]), ["DNS:vpn.example.org", "IP:0.0.0.0/255.255.255.255"])
        self.assertEqual(self.st["gateway"]["inbounds"]["ikev2"]["retired_ca"], [self.gw.pki.thumbprint(old_ca)])

    def test_golden_swanctl_conf_and_updown(self):
        self.ips.prepare(self.st, self.sec, self.runner)
        files = self.files(self.ips.render(self.st, self.sec))
        self.golden("swanctl-kaskad.conf", files["/etc/swanctl/conf.d/kaskad.conf"][0])
        self.golden("ipsec-updown.sh", files["/etc/kaskad/gw/ipsec/updown.sh"][0])
        self.assertEqual(files["/etc/swanctl/conf.d/kaskad.conf"][1], 0o600)
        self.assertEqual(files["/etc/swanctl/private/kaskad-ikev2.pem"][1], 0o600)
        self.assertEqual(files["/etc/swanctl/x509ca/kaskad-ca.pem"][1], 0o644)
        self.assertEqual(json.loads(files["/etc/kaskad/gw/ipsec/users.json"][0]),
                         {"ayfon-1a2b": "d-iphone", "noutbuk-3c4d": "d-win"})
        conf = files["/etc/swanctl/conf.d/kaskad.conf"][0]
        self.assertIn("aes256-sha256-modp2048", conf.split("kaskad-ikev2 {")[1].split("\n")[2])   # first proposal
        self.assertIn("aes256-sha1-modp1024", conf)
        self.assertNotIn("WinPass6789xyz", conf)                                         # secrets are hex
        self.assertIn("0x" + "WinPass6789xyz".encode().hex(), conf)

    def test_only_what_is_enabled(self):
        self.st["gateway"]["inbounds"]["l2tp"]["ipsec"] = False
        self.st["gateway"]["inbounds"]["l2tp"]["plain"] = True
        self.ips.prepare(self.st, self.sec, self.runner)
        conf = self.ips.render(self.st, self.sec)[self.ips.conf_file()][0]
        self.assertNotIn("kaskad-l2tp", conf)
        self.assertNotIn("ike-kaskad-l2tp", conf)
        self.st["gateway"]["inbounds"]["ikev2"]["enabled"] = False
        files = self.ips.render(self.st, self.sec)
        self.assertNotIn("kaskad-ikev2 {", files[self.ips.conf_file()][0])
        self.assertFalse(any(p.endswith(".pem") for p in files))


class CaScopeTest(IpsecCase):
    """The IKEv2 CA goes into the trusted roots of the devices: it vouches for this node only (nameConstraints);
    a CA of an earlier build (none) is kept until the owner rotates it."""

    def legacy(self):
        self.sec.update({"ikev2.ca.key": FAKE_KEY % 90, "ikev2.ca.crt": fixture("ca.crt"),
                         "ikev2.server.key": FAKE_KEY % 91, "ikev2.server.crt": fixture("server.crt")})
        self.st["gateway"]["inbounds"]["ikev2"]["cert_host"] = "203.0.113.10"

    def test_names_of_new_cas_differ(self):
        a, b = self.ips.new_ca_cn("203.0.113.10"), self.ips.new_ca_cn("203.0.113.10")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("Kaskad IKEv2 CA 203.0.113.10 "))
        self.assertLessEqual(len(self.ips.new_ca_cn("x" * 60 + ".example.org")), 64)

    def test_legacy_ca_is_kept_and_reported(self):
        self.legacy()
        self.assertEqual(self.ips.prepare(self.st, self.sec, self.runner), [])
        self.assertTrue(self.ips.ca_legacy(self.sec))
        self.st["gateway"]["host"] = "198.51.100.1"
        self.assertEqual(self.ips.prepare(self.st, self.sec, self.runner),
                         ["сертификат сервера IKEv2 для 198.51.100.1"])                  # no silent new CA
        self.assertEqual(self.sec["ikev2.ca.crt"], fixture("ca.crt"))
        self.st["gateway"]["host"] = "203.0.113.10"
        self.installed("ikev2")
        self.ikev2.apply(self.st, self.sec, self.runner)
        s = self.ikev2.status(self.runner)
        self.assertEqual(s["warnings"], [self.ips.LEGACY_CA])
        self.assertIn("kaskadctl gw pki rotate ikev2", s["warnings"][0])
        res = self.ikev2.issue(self.st, self.sec, self.dev(self.st, "d-win"), "windows")
        notes = " ".join(n for it in res["items"] for n in it["notes"])
        self.assertIn("старого образца", notes)
        self.assertNotIn("Remove-Item -Path", res["body"])                               # nothing replaced yet

    def test_rotation_replaces_the_ca_and_windows_drops_the_old_root(self):
        self.legacy()
        self.installed("ikev2")
        self.ikev2.apply(self.st, self.sec, self.runner)
        self.runner.calls[:] = []
        done = self.ikev2.rotate_pki(self.st, self.sec, self.runner)
        self.assertEqual(done, ["центр сертификации IKEv2 для 203.0.113.10", "сертификат сервера IKEv2 для 203.0.113.10"])
        self.assertFalse(self.ips.ca_legacy(self.sec))
        thumb = self.gw.pki.thumbprint(fixture("ca.crt"))
        self.assertEqual(self.st["gateway"]["inbounds"]["ikev2"]["retired_ca"], [thumb])
        res = self.ikev2.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "loaded")                                        # swanctl --load-all, no restart
        self.assertNotIn("restart", self.node.sysd.verbs(UNIT))
        self.assertEqual(self.read("/etc/swanctl/x509ca/kaskad-ca.pem"), fixture("ikev2-ca-ip.crt"))
        self.assertNotIn("warnings", self.ikev2.status(self.runner))
        body = self.ikev2.issue(self.st, self.sec, self.dev(self.st, "d-win"), "windows")["body"]
        self.assertIn("foreach ($t in @('%s')) {" % thumb, body)
        self.assertIn("Remove-Item -Path ('Cert:\\LocalMachine\\Root\\' + $t) -ErrorAction SilentlyContinue", body)
        self.assertLess(body.index("Remove-Item -Path"), body.index("Import-Certificate"))
        body.encode("ascii")
        mc = self.ikev2.issue(self.st, self.sec, self.dev(self.st, "d-iphone"), "ios")["body"]
        self.assertIn("<string>Kaskad IKEv2 CA 203.0.113.10 7f3a1c</string>", mc)       # CN of the new CA

    def test_install_before_the_address_is_known(self):
        # the install wizard: inbounds go in before gw enable finds the address — no refusal, no certificate yet
        self.st["gateway"]["host"] = ""
        done = self.ips.prepare(self.st, self.sec, self.runner)
        self.assertEqual(done, [])                                                      # passwords exist already
        self.assertNotIn("ikev2.ca.crt", self.sec)
        self.assertEqual(self.node.ssl.calls, [])
        with self.assertRaises(self.gw.base.InboundError) as cm:
            self.ips.render(self.st, self.sec)
        self.assertIn("kaskadctl gw host", str(cm.exception))
        self.st["gateway"]["host"] = "203.0.113.10"                                      # gw enable found it
        self.assertEqual(self.ips.prepare(self.st, self.sec, self.runner),
                         ["центр сертификации IKEv2 для 203.0.113.10", "сертификат сервера IKEv2 для 203.0.113.10"])
        self.ips.render(self.st, self.sec)

    def test_rotation_needs_the_inbound_and_the_address(self):
        self.st["gateway"]["host"] = ""
        with self.assertRaises(self.gw.base.InboundError):
            self.ikev2.rotate_pki(self.st, self.sec, self.runner)
        del self.st["gateway"]["inbounds"]["ikev2"]
        with self.assertRaises(self.gw.base.InboundError):
            self.ikev2.rotate_pki(self.st, self.sec, self.runner)


class ApplyTest(IpsecCase):
    def test_first_apply_starts_and_opens_shared_ports_once(self):
        self.node.ufw_active = True
        self.installed("l2tp", "ikev2")
        res = self.l2tp.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], {"accel": "started", "ipsec": "started"})
        res = self.ikev2.apply(self.st, self.sec, self.runner)
        self.assertEqual((res["action"], res["changed"]), ("unchanged", []))
        self.assertEqual(self.swanctl_calls(), [])                                       # the unit loads at start
        self.assertEqual(self.node.ufw, ["allow 500/udp comment kaskad-gw ipsec", "allow 4500/udp comment kaskad-gw ipsec",
                                         "allow proto esp from any to any comment kaskad-gw ipsec",
                                         "allow 1701/udp comment kaskad-gw l2tp"])
        self.assertTrue(self.l2tp.status(self.runner)["running"])
        self.assertTrue(self.ikev2.status(self.runner)["running"])

    def test_device_changes_load_without_restart_and_cut_the_removed(self):
        self.ready()
        self.st["gateway"]["devices"].append(device("d-new", "Планшет", "ios", ["ikev2"], login="planshet-7a8b"))
        res = self.ikev2.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "loaded")
        self.assertEqual(self.swanctl_calls(), [["--load-all", "--noprompt"]])
        self.assertEqual(self.node.sysd.verbs(UNIT), ["is-active", "is-enabled"])        # no restart
        self.assertIn("id = planshet-7a8b", self.conf())
        self.node.sas = (ike_sa(3, "kaskad-ikev2", eap="ayfon-1a2b", vip="10.77.8.10") +
                         ike_sa(5, "kaskad-ikev2", eap="noutbuk-3c4d", vip="10.77.8.11") +
                         ike_sa(7, "kaskad-l2tp", host="198.51.100.9"))
        self.st["gateway"]["devices"] = [d for d in self.st["gateway"]["devices"] if d["id"] != "d-iphone"]
        res = self.ikev2.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["cut"], ["kaskad-ikev2 ayfon-1a2b"])
        self.assertEqual(self.node.ike_terminated, [3])                                  # the others stay
        self.assertNotIn("ayfon-1a2b", self.conf())

    def test_new_password_terminates_only_that_device(self):
        self.ready()
        self.assertEqual(self.ikev2.reset_secrets("d-win"), ["dev.d-win.password"])
        self.node.sas = (ike_sa(3, "kaskad-ikev2", eap="ayfon-1a2b", vip="10.77.8.10") +
                         ike_sa(5, "kaskad-ikev2", eap="noutbuk-3c4d", vip="10.77.8.11"))
        self.sec["dev.d-win.password"] = "NewPassAfterReset9"                    # gw device reset
        res = self.ikev2.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "loaded")                                # swanctl --load-all, no restart
        self.assertEqual(res["cut"], ["kaskad-ikev2 noutbuk-3c4d"])
        self.assertEqual(self.node.ike_terminated, [5])                          # the iPhone stays
        self.assertNotIn("restart", self.node.sysd.verbs(UNIT))
        self.assertEqual(self.ips.eap_secrets(self.conf())["noutbuk-3c4d"], "0x" + b"NewPassAfterReset9".hex())

    def test_l2tp_ipsec_off_terminates_its_sas_and_keeps_shared_ports(self):
        self.node.ufw_active = True
        self.ready()
        self.node.ufw[:] = []
        self.node.sas = ike_sa(5, "kaskad-ikev2", eap="noutbuk-3c4d", vip="10.77.8.11") + ike_sa(7, "kaskad-l2tp")
        self.st["gateway"]["inbounds"]["l2tp"].update({"ipsec": False, "plain": True})
        res = self.l2tp.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"]["ipsec"], "loaded")
        self.assertEqual(self.node.ike_terminated, [7])
        self.assertNotIn("kaskad-l2tp", self.conf())
        self.assertEqual(self.node.ufw, [])                                              # IKEv2 keeps 500/4500/ESP
        self.assertIn("L2TP без IPsec включён", res["warnings"][0])
        self.st["gateway"]["inbounds"]["ikev2"]["enabled"] = False
        res = self.ikev2.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "stopped")
        self.assertEqual(self.node.ufw, ["delete allow 500/udp", "delete allow 4500/udp",
                                         "delete allow proto esp from any to any"])
        self.assertFalse(os.path.exists(paths.P("/etc/swanctl/x509/kaskad-ikev2.pem")))  # IKEv2 files gone

    def test_load_failure_is_reported_and_new_address_reloads_the_certificate(self):
        self.ready()
        self.st["gateway"]["host"] = "vpn.example.org"
        res = self.ikev2.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "loaded")
        self.assertIn("id = vpn.example.org", self.conf())
        self.node.load_rc = 22
        self.st["gateway"]["net"]["dns"] = "10.77.0.2"
        with self.assertRaises(self.gw.base.InboundError) as cm:
            self.ikev2.apply(self.st, self.sec, self.runner)
        self.assertIn("strongSwan не принял настройки", str(cm.exception))
        self.assertIn("invalid value for: proposals", str(cm.exception))


class StatusTest(IpsecCase):
    def test_sessions_of_ikev2_from_list_sas(self):
        self.ready()
        self.node.sas = (ike_sa(5, "kaskad-ikev2", eap="noutbuk-3c4d", vip="10.77.8.11", established=60,
                                bytes_in=100, bytes_out=200) + ike_sa(7, "kaskad-l2tp"))
        s = self.ikev2.status(self.runner)
        self.assertEqual((s["installed"], s["running"], s["port"]), (True, True, 500))
        self.assertEqual(len(s["sessions"]), 1)
        one = s["sessions"][0]
        self.assertEqual((one["device"], one["login"], one["ip"], one["rx"], one["tx"]),
                         ("d-win", "noutbuk-3c4d", "10.77.8.11", 100, 200))
        self.assertIsInstance(one["since"], int)

    def test_l2tp_needs_both_daemons(self):
        self.ready()
        self.node.sysd.u(UNIT)["active"] = False
        s = self.l2tp.status(self.runner)
        self.assertEqual((s["installed"], s["running"], s["ipsec"], s["plain"]), (True, False, True, False))
        self.assertEqual(s["units"], ["kaskad-gw-accel.service", UNIT])

    def test_parse_sas(self):
        sas = self.ips.parse_sas(ike_sa(5, "kaskad-ikev2", eap="a-1", vip="10.77.8.12") + "list-sas reply {\n}\n")
        self.assertEqual(len(sas), 1)
        sa = sas[0]
        self.assertEqual((sa["name"], sa["uniqueid"], sa["remote-eap-id"], sa["remote-vips"]),
                         ("kaskad-ikev2", "5", "a-1", ["10.77.8.12"]))
        self.assertEqual(sa["children"][0]["bytes-in"], "1500")
        self.assertEqual(sa["children"][0]["local-ts"], ["0.0.0.0/0"])
        self.assertEqual(self.ips.parse_sas(""), [])


class UnitRefreshTest(IpsecCase):
    def test_an_older_unit_is_rewritten_without_dropping_sas(self):
        # stand, 2026-10-04: the unit of the first build had ExecStartPost without «-»; a connection that
        # did not load took the daemon and every SA down. apply() must bring the new text, not restart.
        self.ready()
        unit = paths.P("/etc/systemd/system/" + UNIT)
        fresh = self.read_abs(unit)
        with open(unit, "w", encoding="utf-8") as f:
            f.write(fresh.replace("ExecStartPost=-", "ExecStartPost="))
        res = self.ikev2.apply(self.st, self.sec, self.runner)
        self.assertEqual(self.read_abs(unit), fresh)
        self.assertIn(["daemon-reload"], self.node.sysd.calls)
        self.assertNotIn("restart", self.node.sysd.verbs(UNIT))
        self.assertEqual(res["action"], "unchanged")
        self.assertEqual(self.swanctl_calls(), [])                                   # SAs untouched
        res = self.ikev2.apply(self.st, self.sec, self.runner)
        self.assertEqual((res["action"], res["changed"]), ("unchanged", []))

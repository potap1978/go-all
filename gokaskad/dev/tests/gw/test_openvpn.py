"""OpenVPN inbound: PKI through openssl, golden configs, packages, ccd without restarts, status, .ovpn."""

import json
import os
import stat

from kaskad import paths
from tests.gw.tunnel_case import GwCase, device, gw_state, slurp
from tests.gw.tunnel_fakes import STATIC_KEY, fixture

OVPN = {"openvpn": {"enabled": True, "udp_port": 1194, "tcp_port": 0}}
STATUS = """TITLE,OpenVPN 2.6.14 x86_64-pc-linux-gnu [SSL (OpenSSL)] [LZO] [LZ4] [EPOLL] [MH/PKTINFO] [AEAD]
TIME,2026-10-04 12:00:00,1759579200
HEADER,CLIENT_LIST,Common Name,Real Address,Virtual Address,Virtual IPv6 Address,Bytes Received,Bytes Sent,Connected Since,Connected Since (time_t),Username,Client ID,Peer ID,Data Channel Cipher
CLIENT_LIST,d-laptop,198.51.100.20:51234,10.77.9.10,,12345,67890,2026-10-04 11:00:00,1759575600,UNDEF,0,0,AES-256-GCM
HEADER,ROUTING_TABLE,Virtual Address,Common Name,Real Address,Last Ref,Last Ref (time_t)
ROUTING_TABLE,10.77.9.10,d-laptop,198.51.100.20:51234,2026-10-04 12:00:00,1759579200
GLOBAL_STATS,Max bcast/mcast queue length,0
GLOBAL_STATS,dco_enabled,0
END
"""


def devices():
    return [
        device("d-laptop", "Ноутбук", "windows", ["openvpn"], kind="laptop"),
        device("d-asus", "ASUS дома", "asus", ["openvpn"], kind="router"),
        device("d-phone", "Телефон", "ios", ["wg"]),
    ]


class OvpnCase(GwCase):
    def setUp(self):
        super().setUp()
        self.st = gw_state(OVPN, devices())
        self.ov = self.adapter("openvpn")
        self.kills = []
        self.ov.management = lambda sock, cmds: self.kills.append((self.prod(sock), list(cmds))) or ["SUCCESS"]

    def dev(self, dev_id):
        return [d for d in self.st["gateway"]["devices"] if d["id"] == dev_id][0]

    def ready(self):
        self.ov.install(self.runner)
        self.ov.apply(self.st, self.sec, self.runner)
        self.node.sysd.calls[:] = []


class PkiTest(OvpnCase):
    def test_prepare_builds_the_pki_once(self):
        done = self.ov.prepare(self.st, self.sec, self.runner)
        self.assertEqual(done, ["адрес ovpn_u d-asus", "адрес ovpn_u d-laptop", "центр сертификации",
                                "сертификат сервера", "ключ tls-crypt", "сертификат d-asus", "сертификат d-laptop",
                                "список отзыва (отозвано: 0)"])
        self.assertEqual(self.node.ssl.calls, ["genpkey", "req",                      # CA
                                               "genpkey", "req", "x509",              # server
                                               "genpkey", "req", "x509",              # d-asus
                                               "genpkey", "req", "x509",              # d-laptop
                                               "ca"])                                 # the (empty) CRL
        self.assertEqual(self.sec["ovpn.ca.crt"], fixture("ca.crt"))
        self.assertEqual(self.sec["ovpn.server.crt"], fixture("server.crt"))
        self.assertEqual(self.sec["dev.d-laptop.ovpn.crt"], fixture("client.crt"))
        self.assertTrue(self.sec["dev.d-laptop.ovpn.key"].startswith("-----BEGIN PRIVATE KEY-----"))
        self.assertEqual(self.sec["ovpn.tls_crypt"], STATIC_KEY)
        self.assertEqual(self.dev("d-laptop")["ips"], {"ovpn_u": "10.77.9.11"})       # sorted by id: d-asus first
        self.assertEqual(os.listdir(self.ov.dir()), [])                             # temporary files are gone
        self.assert_no_secrets(" ".join(" ".join(a) for a, _ in self.runner.calls))   # keys never in argv
        self.assertEqual(self.ov.prepare(self.st, self.sec, self.runner), [])
        self.assertEqual(len(self.node.ssl.calls), 12)

    def test_new_ca_reissues_everything(self):
        self.ov.prepare(self.st, self.sec, self.runner)
        del self.sec["ovpn.ca.key"]
        done = self.ov.prepare(self.st, self.sec, self.runner)
        self.assertEqual(done, ["центр сертификации", "сертификат сервера", "сертификат d-asus", "сертификат d-laptop",
                                "список отзыва (отозвано: 0)"])                    # a new book for the new CA

    def test_unsafe_device_id_is_skipped(self):
        self.st["gateway"]["devices"].append(device("d/../x", "Странный", "linux", ["openvpn"]))
        done = self.ov.prepare(self.st, self.sec, self.runner)
        self.assertNotIn("сертификат d/../x", done)
        self.assertIn("gw openvpn: пропущены устройства с неподходящим id: d/../x", self.log_text())
        files = self.files(self.ov.render(self.st, self.sec))
        self.assertEqual([p for p in files if "ccd-udp/" in p],
                         ["/etc/openvpn/kaskad/ccd-udp/d-asus", "/etc/openvpn/kaskad/ccd-udp/d-laptop"])

    def test_without_runner_it_refuses_to_generate(self):
        with self.assertRaises(self.gw.base.InboundError):
            self.ov.prepare(self.st, self.sec, None)


class RenderTest(OvpnCase):
    def test_golden_udp_and_tcp(self):
        self.st["gateway"]["inbounds"]["openvpn"]["tcp_port"] = 8443
        self.ov.prepare(self.st, self.sec, self.runner)
        files = self.files(self.ov.render(self.st, self.sec))
        d, pub = "/etc/kaskad/gw/openvpn/", "/etc/openvpn/kaskad/"
        self.assertEqual(sorted(files), sorted([d + n for n in (
            "ca.crt", "server.crt", "server.key", "tc.key", "tcp.conf", "udp.conf")] + [pub + n for n in (
            "ccd-tcp/d-asus", "ccd-tcp/d-laptop", "ccd-udp/d-asus", "ccd-udp/d-laptop", "crl.pem")]))
        self.assertTrue(all(m == 0o600 for p, (_, m) in files.items() if p.startswith(d)))     # keys: root only
        self.assertTrue(all(m == 0o644 for p, (_, m) in files.items() if p.startswith(pub)))   # read as nobody
        self.golden("openvpn-udp.conf", files[d + "udp.conf"][0])
        self.golden("openvpn-tcp.conf", files[d + "tcp.conf"][0])
        udp = self.prod(files[d + "udp.conf"][0])
        for line in ("user nobody", "group nogroup", "persist-key", "persist-tun", "crl-verify /etc/openvpn/kaskad/crl.pem",
                     "client-config-dir /etc/openvpn/kaskad/ccd-udp"):
            self.assertIn("\n%s\n" % line, udp)
        self.assertNotIn("block-ipv6", udp)                                         # 2.6 clients refuse it pushed
        self.assertIn('push "redirect-gateway def1 ipv6"', udp)
        self.assertEqual(files[pub + "ccd-udp/d-laptop"][0], "ifconfig-push 10.77.9.11 255.255.255.0\n")
        self.assertEqual(files[pub + "ccd-tcp/d-laptop"][0], "ifconfig-push 10.77.10.11 255.255.255.0\n")
        self.assertIn("-----BEGIN X509 CRL-----", files[pub + "crl.pem"][0])
        self.assertNotIn("ca.key", " ".join(files))                                 # the CA key stays in secrets

    def test_golden_unit(self):
        units = self.ov.unit_texts(self.runner)
        self.assertEqual(sorted(units), ["kaskad-gw-ovpn-tcp.service", "kaskad-gw-ovpn-udp.service"])
        self.golden("kaskad-gw-ovpn-udp.service", units["kaskad-gw-ovpn-udp.service"])


class InstallTest(OvpnCase):
    def test_fresh_install_masks_the_package_unit(self):
        self.ov.install(self.runner)
        self.assertEqual(self.node.apt, [(["openvpn", "openssl"], True)])
        self.assertEqual(self.node.sysd.verbs("openvpn.service"), ["disable --now", "mask"])
        self.assertTrue(self.node.sysd.units["openvpn.service"]["masked"])
        rec = json.loads(slurp(paths.P("/etc/kaskad/gw/packages.json")))
        self.assertEqual(rec["openvpn"], {"by_kaskad": True, "masked": ["openvpn.service"]})
        self.assertEqual(self.node.modprobed, ["tun"])
        self.node.sysd.calls[:] = []
        self.ov.install(self.runner)                                                # again: nothing to mask
        self.assertEqual(self.node.sysd.verbs("openvpn.service"), [])
        self.ov.uninstall(self.runner)
        self.assertEqual(self.node.sysd.verbs("openvpn.service"), ["unmask"])
        self.assertFalse(self.node.sysd.units["openvpn.service"]["masked"])
        self.node.sysd.calls[:] = []
        self.ov.install(self.runner)                                                # Kaskad installed it: mask again
        self.assertEqual(self.node.sysd.verbs("openvpn.service"), ["disable --now", "mask"])

    def test_the_shared_gateway_directory_keeps_its_mode(self):
        os.makedirs(paths.P("/etc/kaskad/gw"), 0o755)
        os.chmod(paths.P("/etc/kaskad/gw"), 0o755)                                  # the core's choice
        self.ov.install(self.runner)
        self.assertEqual(os.stat(paths.P("/etc/kaskad/gw")).st_mode & 0o777, 0o755)
        self.assertEqual(os.stat(self.ov.dir()).st_mode & 0o777, 0o700)

    def test_own_openvpn_of_the_server_is_left_alone(self):
        self.node.pkgs.update(["openvpn", "openssl"])
        self.ov.install(self.runner)
        self.assertEqual(self.node.apt, [])
        self.assertEqual(self.node.sysd.verbs("openvpn.service"), [])
        self.ov.uninstall(self.runner)
        self.assertEqual(self.node.sysd.verbs("openvpn.service"), [])


class ApplyTest(OvpnCase):
    def test_first_apply_starts_udp_only(self):
        self.ov.install(self.runner)
        res = self.ov.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], {"udp": "started", "tcp": "off"})
        self.assertTrue(os.path.isdir(self.ov.ccd_dir("udp")))

    def test_devices_change_without_restart_and_removed_are_cut(self):
        self.ready()
        self.st["gateway"]["devices"].append(device("d-new", "Новый", "android", ["openvpn"]))
        res = self.ov.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], {"udp": "unchanged", "tcp": "off"})
        self.assertIn("ccd-udp/d-new", res["changed"])
        self.assertNotIn("restart", self.node.sysd.verbs("kaskad-gw-ovpn-udp.service"))
        self.assertEqual(self.kills, [])
        sock = self.ov.run_dir("udp") + "/mgmt.sock"
        os.makedirs(os.path.dirname(sock))
        open(sock, "w").close()
        self.dev("d-asus")["enabled"] = False
        res = self.ov.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"]["udp"], "cut d-asus")
        self.assertEqual(self.kills, [("/run/kaskad-gw-ovpn-udp/mgmt.sock", ["kill d-asus"])])
        self.assertFalse(os.path.exists(os.path.join(self.ov.ccd_dir("udp"), "d-asus")))

    def test_tcp_instance_on_and_off(self):
        self.node.ufw_active = True
        self.ready()
        self.st["gateway"]["inbounds"]["openvpn"]["tcp_port"] = 8443
        res = self.ov.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], {"udp": "unchanged", "tcp": "started"})
        self.assertEqual(self.dev("d-laptop")["ips"]["ovpn_t"], "10.77.10.11")
        self.assertEqual(self.node.ufw[-2:], ["allow 1194/udp comment kaskad-gw openvpn",
                                              "allow 8443/tcp comment kaskad-gw openvpn"])
        self.st["gateway"]["inbounds"]["openvpn"]["tcp_port"] = 0
        res = self.ov.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], {"udp": "unchanged", "tcp": "stopped"})
        self.assertFalse(os.path.exists(self.ov.conf_path("tcp")))
        self.assertFalse(os.path.exists(self.ov.ccd_dir("tcp")))
        self.assertIn("delete allow 8443/tcp", self.node.ufw)

    def test_server_config_change_restarts_that_instance(self):
        self.ready()
        self.st["gateway"]["net"]["dns"] = "10.77.0.2"
        res = self.ov.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], {"udp": "restarted", "tcp": "off"})

    def test_gateway_off_stops_everything(self):
        self.ready()
        self.st["gateway"]["enabled"] = False
        self.assertEqual(self.ov.apply(self.st, self.sec, self.runner)["action"], {"udp": "stopped", "tcp": "off"})


class CrlTest(OvpnCase):
    """A certificate that is not the device's any more goes into the CRL (re-read by OpenVPN at the next
    handshake: no restart) and its session is killed; the files the server reads as nobody are readable."""

    def setUp(self):
        super().setUp()
        self.node.ssl.client_certs = ["client.crt", "client2.crt", "client3.crt"]

    def crl_serials(self):
        return self.node.ssl.crls[-1]

    def mgmt(self):
        sock = self.ov.run_dir("udp") + "/mgmt.sock"
        os.makedirs(os.path.dirname(sock), exist_ok=True)
        open(sock, "w").close()

    def test_reset_revokes_the_old_certificate_and_kills_the_session(self):
        self.assertEqual(self.ov.secret_prefixes("d-laptop"), ["dev.d-laptop.ovpn."])   # revoke and reset
        self.assertEqual(self.ov.reset_secrets("d-laptop"), ["dev.d-laptop.ovpn."])
        self.ready()
        self.mgmt()
        old = self.gw.pki.serial_hex(self.sec["dev.d-laptop.ovpn.crt"])
        book = json.loads(self.sec["ovpn.crl.book"])
        self.assertEqual(sorted(book["issued"]), ["d-asus", "d-laptop"])
        self.assertEqual(book["revoked"], {})
        for k in [k for k in self.sec if k.startswith("dev.d-laptop.ovpn.")]:   # gw device reset --inbound openvpn
            del self.sec[k]
        res = self.ov.apply(self.st, self.sec, self.runner)
        self.assertIn("список отзыва (отозвано: 1)", res["prepared"])
        self.assertEqual(self.crl_serials(), [old])
        new = self.gw.pki.serial_hex(self.sec["dev.d-laptop.ovpn.crt"])
        self.assertNotEqual(new, old)
        self.assertEqual(res["action"], {"udp": "cut d-laptop", "tcp": "off"})  # no restart for a new CRL
        self.assertEqual(self.kills, [("/run/kaskad-gw-ovpn-udp/mgmt.sock", ["kill d-laptop"])])
        self.assertNotIn("restart", self.node.sysd.verbs("kaskad-gw-ovpn-udp.service"))
        self.assertIn("crl.pem", res["changed"])
        self.assertIn(old, self.read("/etc/openvpn/kaskad/crl.pem"))
        self.assertEqual(self.ov.apply(self.st, self.sec, self.runner)["prepared"], [])     # once

    def test_deleted_or_revoked_device_is_revoked_a_disabled_one_is_not(self):
        self.ready()
        asus = self.gw.pki.serial_hex(self.sec["dev.d-asus.ovpn.crt"])
        laptop = self.gw.pki.serial_hex(self.sec["dev.d-laptop.ovpn.crt"])
        self.dev("d-laptop")["enabled"] = False                                 # ccd-exclusive refuses it, it may return
        self.ov.apply(self.st, self.sec, self.runner)
        self.assertEqual(json.loads(self.sec["ovpn.crl.book"])["revoked"], {})
        self.st["gateway"]["devices"] = [d for d in self.st["gateway"]["devices"] if d["id"] != "d-asus"]
        for k in [k for k in self.sec if k.startswith("dev.d-asus.")]:        # gw device del
            del self.sec[k]
        self.ov.apply(self.st, self.sec, self.runner)
        self.assertEqual(self.crl_serials(), [asus])
        self.assertNotIn(laptop, self.crl_serials())

    def test_a_new_ca_starts_a_new_list(self):
        self.ready()
        for k in [k for k in self.sec if k.startswith("dev.d-asus.")]:
            del self.sec[k]
        self.ov.apply(self.st, self.sec, self.runner)
        self.assertEqual(len(self.crl_serials()), 1)
        del self.sec["ovpn.ca.key"]
        self.ov.apply(self.st, self.sec, self.runner)
        self.assertEqual(self.crl_serials(), [])                               # serials of the old CA mean nothing

    def test_files_the_server_reads_as_nobody_and_the_old_ccd(self):
        os.makedirs(self.path("/etc/kaskad/gw/openvpn/ccd-udp"))
        self.write("/etc/kaskad/gw/openvpn/ccd-udp/d-asus", "ifconfig-push 10.77.9.10 255.255.255.0\n")
        self.ready()
        self.assertFalse(os.path.exists(self.path("/etc/kaskad/gw/openvpn/ccd-udp")))  # an earlier build's, root-only
        for p in ("/etc/openvpn", "/etc/openvpn/kaskad", "/etc/openvpn/kaskad/ccd-udp"):
            self.assertEqual(stat.S_IMODE(os.stat(self.path(p)).st_mode), 0o755, p)
        for p in ("/etc/openvpn/kaskad/crl.pem", "/etc/openvpn/kaskad/ccd-udp/d-asus"):
            self.assertEqual(stat.S_IMODE(os.stat(self.path(p)).st_mode), 0o644, p)
        self.assertEqual(stat.S_IMODE(os.stat(self.ov.dir()).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.path("/etc/kaskad/gw/openvpn/server.key")).st_mode), 0o600)
        self.ov.uninstall(self.runner)
        self.assertFalse(os.path.exists(self.path("/etc/openvpn/kaskad")))


class StatusTest(OvpnCase):
    def test_sessions_from_the_status_file(self):
        self.ready()
        self.write("/run/kaskad-gw-ovpn-udp/status", STATUS)
        st = self.ov.status(self.runner)
        self.assertEqual((st["installed"], st["running"], st["port"]), (True, True, 1194))
        self.assertEqual(st["instances"]["tcp"], {"running": False, "port": None, "unit": "kaskad-gw-ovpn-tcp.service"})
        self.assertEqual(st["sessions"], [{"device": "d-laptop", "ip": "10.77.9.10", "since": 1759575600,
                                           "rx": 12345, "tx": 67890, "transport": "udp"}])
        self.assert_no_secrets(json.dumps(st))


class IssueTest(OvpnCase):
    def test_windows_ovpn_with_udp_and_tcp(self):
        self.st["gateway"]["inbounds"]["openvpn"]["tcp_port"] = 8443
        self.ov.prepare(self.st, self.sec, self.runner)
        res = self.ov.issue(self.st, self.sec, self.dev("d-laptop"), None)
        self.assertEqual((res["format"], res["filename"]), ("ovpn", "kaskad-openvpn.ovpn"))
        body = res["body"]
        self.golden("openvpn-client-laptop.ovpn", body)
        self.assertIn("remote 203.0.113.10 1194 udp\nremote 203.0.113.10 8443 tcp\n", body)
        self.assertIn("\nignore-unknown-option block-ipv6\nblock-ipv6\n", body)         # the client's own
        self.assertIn("block-outside-dns", body)
        self.assertIn(self.sec["dev.d-laptop.ovpn.key"].strip(), body)
        self.assertNotIn(self.sec["ovpn.ca.key"].strip(), body)
        res = self.ov.issue(self.st, self.sec, self.dev("d-asus"), None)
        self.assertNotIn("block-outside-dns", res["body"])
        self.assertIn("VPN-клиент", res["notes"][0])

    def test_refusals(self):
        self.ov.prepare(self.st, self.sec, self.runner)
        with self.assertRaises(self.gw.base.InboundError):
            self.ov.issue(self.st, self.sec, self.dev("d-phone"), "ios")
        self.st["gateway"]["devices"].append(device("d-late", "Поздний", "android", ["openvpn"]))
        with self.assertRaises(self.gw.base.InboundError) as cm:
            self.ov.issue(self.st, self.sec, "d-late", None)
        self.assertIn("kaskadctl gw apply", str(cm.exception))

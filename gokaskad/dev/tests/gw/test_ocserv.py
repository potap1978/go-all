"""OpenConnect (ocserv) inbound: certificate, password hashes through openssl, golden configs, reloads
without dropping sessions, status from occtl, issuance with the pin."""

import json
import os
import stat

from kaskad import paths
from tests.gw.tunnel_case import GwCase, device, gw_state, slurp
from tests.gw.tunnel_fakes import fixture

OC = {"ocserv": {"enabled": True, "port": 4443}}
PIN = "pin-sha256:cSNhd8G4JPu2l2QJFWwno+7Rb7uwqK+yNrS1UZcgWk8="      # openssl x509 -pubkey | pkey -outform der | sha256
USERS = json.dumps([{
    "ID": 87, "Username": "dev-mac", "Groupname": "*", "State": "connected", "vhost": "default",
    "Device": "kgw-oc0", "MTU": "1434", "Remote IP": "198.51.100.20", "Location": "unknown",
    "Local Device IP": "203.0.113.10", "IPv4": "10.77.11.10", "P-t-P IPv4": "10.77.11.1",
    "User-Agent": "Open AnyConnect VPN Agent v9.12", "RX": "1235", "TX": "2345", "_RX": "1.2 KB",
    "_TX": "2.3 KB", "Average RX": "0 bytes/sec", "Average TX": "0 bytes/sec", "DPD": "90", "KeepAlive": "32400",
    "Connected at": "2026-10-04 11:00", "_Connected at": " 1h:00m", "raw_connected_at": 1759575600,
}])


def devices():
    return [
        device("d-mac", "Макбук", "macos", ["ocserv"], kind="laptop"),
        device("d-lin", "Linux", "linux", ["ocserv"], kind="pc"),
        device("d-bad", "Плохой логин", "windows", ["ocserv"], login="../etc/passwd"),
        device("d-wg", "Только WireGuard", "ios", ["wg"]),
    ]


class OcCase(GwCase):
    def setUp(self):
        super().setUp()
        self.st = gw_state(OC, devices())
        self.oc = self.adapter("ocserv")

    def dev(self, dev_id):
        return [d for d in self.st["gateway"]["devices"] if d["id"] == dev_id][0]

    def ready(self):
        self.oc.install(self.runner)
        self.oc.apply(self.st, self.sec, self.runner)
        self.node.sysd.calls[:] = []


class PrepareTest(OcCase):
    def test_certificate_passwords_hashes(self):
        self.oc.prepare(self.st, self.sec, self.runner)
        self.assertIn("gw ocserv: пропущены устройства с неподходящим или повторным логином: d-bad", self.log_text())
        self.assertEqual(self.sec["ocserv.server.crt"], fixture("ocserv.crt"))
        self.assertEqual(self.node.ssl.calls, ["genpkey", "req", "passwd", "passwd"])
        self.assertEqual(self.dev("d-lin")["ips"], {"ocserv": "10.77.11.10"})
        self.assertEqual(self.dev("d-mac")["ips"], {"ocserv": "10.77.11.11"})
        self.assertNotIn("ips", {k: v for k, v in self.dev("d-bad").items() if v})
        pw = self.sec["dev.d-mac.password"]
        argv = [a for a, _ in self.runner.calls if a[:2] == ["openssl", "passwd"]]
        self.assertEqual(len(argv), 2)
        for a in argv:
            self.assertNotIn(pw, " ".join(a))                                       # stdin only
        self.assertIn((pw + "\n"), [i for a, i in self.runner.calls if a[:2] == ["openssl", "passwd"]])
        salt = self.gw.ocserv.salt_for(self.sec["ocserv.pepper"], "d-mac", pw)
        self.assertTrue(self.sec["dev.d-mac.ocserv.hash"].startswith("$6$%s$" % salt))
        self.assertEqual(self.oc.prepare(self.st, self.sec, self.runner), [])      # stable: no new hashes
        self.assertEqual(self.node.ssl.calls.count("passwd"), 2)
        self.sec["dev.d-mac.password"] = "НовыйPass77"                              # password changed elsewhere
        self.assertEqual(self.oc.prepare(self.st, self.sec, self.runner), ["хэш пароля d-mac"])

    def test_duplicate_login_keeps_the_first_device(self):
        self.st["gateway"]["devices"].append(device("d-zzz", "Двойник", "linux", ["ocserv"], login="dev-mac"))
        self.oc.prepare(self.st, self.sec, self.runner)
        passwd = self.oc.render(self.st, self.sec)[self.oc.path("ocpasswd")][0]
        self.assertEqual([ln.split(":")[0] for ln in passwd.splitlines()], ["dev-lin", "dev-mac"])
        self.assertNotIn("dev.d-zzz.ocserv.hash", self.sec)
        self.assertIn("повторным логином: d-bad, d-zzz", self.log_text())

    def test_cert_san_is_the_node_address(self):
        self.oc.prepare(self.st, self.sec, self.runner)
        cnf = self.node.ssl.configs[0]
        self.assertIn("CN = 203.0.113.10", cnf)
        self.assertIn("extendedKeyUsage = serverAuth", cnf)
        self.assertIn("IP.1 = 203.0.113.10", cnf)
        self.assertNotIn("authorityKeyIdentifier", cnf)                         # self-signed leaf

    def test_pin(self):
        self.assertEqual(self.gw.pki.spki_pin(fixture("ocserv.crt")), PIN)


class RenderTest(OcCase):
    def test_golden(self):
        self.oc.prepare(self.st, self.sec, self.runner)
        files = self.files(self.oc.render(self.st, self.sec))
        d = "/etc/kaskad/gw/ocserv/"
        self.assertEqual(sorted(files), [d + n for n in ("config-per-user/dev-lin", "config-per-user/dev-mac", "hook.sh",
                                                         "ocpasswd", "ocserv.conf", "server-cert.pem", "server-key.pem")])
        self.golden("ocserv.conf", files[d + "ocserv.conf"][0])
        self.golden("ocserv-hook.sh", files[d + "hook.sh"][0])
        self.assertEqual(files[d + "hook.sh"][1], 0o755)
        self.assertEqual(files[d + "config-per-user/dev-mac"][0], "# device d-mac\nexplicit-ipv4 = 10.77.11.11\n")
        lines = files[d + "ocpasswd"][0].splitlines()
        self.assertEqual([ln.split(":")[:2] for ln in lines], [["dev-lin", "*"], ["dev-mac", "*"]])
        self.assertTrue(all(ln.split(":", 2)[2].startswith("$6$") for ln in lines))

    def test_golden_unit(self):
        self.golden("kaskad-gw-ocserv.service", self.oc.unit_texts(self.runner)["kaskad-gw-ocserv.service"])
        self.assertEqual(self.oc.ifaces(), ["kgw-oc*"])


class ApplyTest(OcCase):
    def test_install_masks_the_package_unit(self):
        self.oc.install(self.runner)
        self.assertEqual(self.node.apt, [(["ocserv", "openssl"], True)])
        self.assertEqual(self.node.sysd.verbs("ocserv.service"), ["disable --now", "mask"])

    def test_first_apply_starts(self):
        self.oc.install(self.runner)
        res = self.oc.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "started")
        hook = os.path.join(self.oc.dir(), "hook.sh")
        self.assertEqual(stat.S_IMODE(os.stat(hook).st_mode), 0o755)

    def test_users_change_without_restart_and_removed_are_cut(self):
        self.ready()
        self.st["gateway"]["devices"].append(device("d-new", "Новый", "android", ["ocserv"]))
        res = self.oc.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "unchanged")
        self.assertIn("ocpasswd", res["changed"])
        self.assertEqual(self.node.sysd.verbs("kaskad-gw-ocserv.service"), ["is-active", "is-enabled"])
        self.dev("d-mac")["access"] = ["wg"]
        res = self.oc.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "unchanged, cut dev-mac")
        self.assertEqual(self.node.disconnects, ["dev-mac"])
        self.assertFalse(os.path.exists(os.path.join(self.oc.users_dir(), "dev-mac")))
        occtl = [a for a, _ in self.runner.calls if a[0] == "/usr/sbin/occtl"][-1]
        self.assertEqual(self.prod(" ".join(occtl)),
                         "/usr/sbin/occtl -s /run/kaskad-gw-ocserv/occtl.sock disconnect user dev-mac")

    def test_new_password_cuts_only_that_user(self):
        self.ready()
        self.assertEqual(self.oc.reset_secrets("d-lin"), ["dev.d-lin.password", "dev.d-lin.ocserv."])
        del self.sec["dev.d-lin.password"]                                      # gw device reset
        del self.sec["dev.d-lin.ocserv.hash"]
        res = self.oc.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "unchanged, cut dev-lin")                # no restart, no reload
        self.assertEqual(self.node.disconnects, ["dev-lin"])                     # dev-mac stays connected
        self.assertIn("dev-lin:*:$6$", self.read("/etc/kaskad/gw/ocserv/ocpasswd"))

    def test_settings_reload_port_restart(self):
        self.node.ufw_active = True
        self.ready()
        self.st["gateway"]["net"]["dns"] = "10.77.0.2"
        self.dev("d-lin")["enabled"] = False
        res = self.oc.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "reloaded, cut dev-lin")                  # SIGHUP keeps sessions
        self.assertIn("reload", self.node.sysd.verbs("kaskad-gw-ocserv.service"))
        self.st["gateway"]["inbounds"]["ocserv"]["port"] = 4444
        self.assertEqual(self.oc.apply(self.st, self.sec, self.runner)["action"], "restarted")
        self.assertEqual(self.node.ufw[-4:], ["delete allow 4443/tcp", "delete allow 4443/udp",
                                              "allow 4444/tcp comment kaskad-gw ocserv",
                                              "allow 4444/udp comment kaskad-gw ocserv"])


class StatusTest(OcCase):
    def test_sessions_from_occtl(self):
        self.ready()
        self.node.occtl_users = USERS
        st = self.oc.status(self.runner)
        self.assertEqual((st["installed"], st["running"], st["port"]), (True, True, 4443))
        self.assertEqual(st["sessions"], [{"device": "d-mac", "login": "dev-mac", "ip": "10.77.11.10",
                                           "since": 1759575600, "rx": 1235, "tx": 2345}])
        self.assert_no_secrets(json.dumps(st))


class IssueTest(OcCase):
    def test_fields_and_guides(self):
        self.oc.prepare(self.st, self.sec, self.runner)
        res = self.oc.issue(self.st, self.sec, self.dev("d-mac"), None)
        self.assertEqual([i["format"] for i in res["items"]], ["fields", "guide"])
        self.assertEqual(res["body"], [["Сервер", "https://203.0.113.10:4443"], ["Логин", "dev-mac"],
                                       ["Пароль", self.sec["dev.d-mac.password"]], ["Отпечаток сертификата", PIN]])
        self.assertIn("Cisco Secure Client", res["items"][1]["body"])
        res = self.oc.issue(self.st, self.sec, self.dev("d-lin"), None)
        self.assertEqual(res["body"], "sudo openconnect --protocol=anyconnect --user=dev-lin --servercert %s "
                                      "https://203.0.113.10:4443" % PIN)

    def test_refusals(self):
        self.oc.prepare(self.st, self.sec, self.runner)
        for dev_id in ("d-bad", "d-wg"):
            with self.assertRaises(self.gw.base.InboundError):
                self.oc.issue(self.st, self.sec, self.dev(dev_id), None)

"""Seeded random sequences over every tunnel adapter: devices come and go, get disabled, lose access,
ports move, inbounds and the gateway switch off and on — after each step every config must list
exactly the devices that may connect, issuance must work for each of them, units must follow the state."""

import os
import random

from tests.gw.tunnel_case import PARAMS, GwCase, device, gw_state, slurp

KINDS = ["wg", "awg1", "awg2", "awg3", "openvpn", "ocserv", "socks5"]
INBOUNDS = {"wg": {"enabled": True, "port": 51820}, "awg1": {"enabled": True, "port": 31000, "line": "1.5"},
            "awg2": {"enabled": True, "port": 32000, "params": PARAMS["2.0"]}, "awg3": {"enabled": True},
            "openvpn": {"enabled": True, "udp_port": 1194, "tcp_port": 0},
            "ocserv": {"enabled": True, "port": 4443}, "socks5": {"enabled": True, "port": 1080}}


class SequenceTest(GwCase):
    def setUp(self):
        super().setUp()
        self.node.ufw_active = True
        self.write("/etc/kaskad/egress/awg/amneziawg-go", "fake disable_cookies", 0o755)
        self.write("/etc/kaskad/egress/awg/awg", "fake DisableCookies", 0o755)

    def allowed(self, st, kind):
        users = {u["id"]: u for u in st["gateway"]["users"]}
        return sorted(d["id"] for d in st["gateway"]["devices"]
                      if kind in d["access"] and d.get("enabled", True) and users[d["user"]].get("enabled", True))

    def check(self, st):
        for kind in ("wg", "awg1", "awg2", "awg3"):
            text = slurp(self.adapter(kind).conf_path())
            ids = sorted(ln.split()[-1] for ln in text.splitlines() if ln.startswith("# device "))
            self.assertEqual(ids, self.allowed(st, kind), kind)
            ips = [ln for ln in text.splitlines() if ln.startswith("AllowedIPs")]
            self.assertEqual(len(ips), len(set(ips)), kind)
        ov = self.adapter("openvpn")
        for inst in ov.instances(st):
            self.assertEqual(sorted(os.listdir(ov.ccd_dir(inst))), self.allowed(st, "openvpn"))
        oc = self.adapter("ocserv")
        logins = sorted(ln.split(":")[0] for ln in slurp(oc.path("ocpasswd")).splitlines())
        devs = {d["id"]: d for d in st["gateway"]["devices"]}
        self.assertEqual(logins, sorted(devs[i]["login"] for i in self.allowed(st, "ocserv")))
        inb = self.adapter("socks5").xray_inbounds(st, self.sec)
        on = st["gateway"]["enabled"] and st["gateway"]["inbounds"]["socks5"].get("enabled", True)
        self.assertEqual(sorted(a["user"] for a in inb[0]["settings"]["accounts"]) if inb else [],
                         sorted(devs[i]["login"] for i in self.allowed(st, "socks5")) if on else [])
        for kind in KINDS:
            for dev_id in self.allowed(st, kind):
                self.adapter(kind).issue(st, self.sec, dev_id, None)
        units = {"wg": "kaskad-gw-wg.service", "awg1": "kaskad-gw-awg1.service", "awg2": "kaskad-gw-awg2.service",
                 "awg3": "kaskad-gw-awg3.service", "openvpn": "kaskad-gw-ovpn-udp.service",
                 "ocserv": "kaskad-gw-ocserv.service"}
        for kind, unit in units.items():
            want = self.gw.tunnel.wanted(st, kind)
            self.assertEqual(self.node.sysd.units.get(unit, {}).get("active", False), want, kind)

    def run_seed(self, seed, steps=30):
        rnd = random.Random(seed)
        st = gw_state(INBOUNDS, [])
        for kind in KINDS:
            self.adapter(kind).install(self.runner)
        n = 0
        for _ in range(steps):
            devs = st["gateway"]["devices"]
            op = rnd.choice(["add", "add", "del", "disable", "enable", "access", "port", "toggle", "gw", "user"])
            if op == "add" or not devs:
                n += 1
                devs.append(device("d-%03d" % n, "Устройство %d" % n,
                                   rnd.choice(["ios", "android", "linux", "windows", "keenetic", "mikrotik"]),
                                   rnd.sample(KINDS, rnd.randint(1, len(KINDS))), user=rnd.choice(["u-1", "u-2"])))
            elif op == "del":
                devs.remove(rnd.choice(devs))
            elif op in ("disable", "enable"):
                rnd.choice(devs)["enabled"] = op == "enable"
            elif op == "access":
                rnd.choice(devs)["access"] = rnd.sample(KINDS, rnd.randint(0, 3))
            elif op == "port":
                st["gateway"]["inbounds"][rnd.choice(KINDS[:4] + KINDS[5:])]["port"] = rnd.randint(20000, 60000)
                st["gateway"]["inbounds"]["openvpn"]["tcp_port"] = rnd.choice([0, 0, 8443])
            elif op == "toggle":
                cfg = st["gateway"]["inbounds"][rnd.choice(KINDS)]
                cfg["enabled"] = not cfg.get("enabled", True)
            elif op == "gw":
                st["gateway"]["enabled"] = not st["gateway"]["enabled"]
            else:
                u = rnd.choice(st["gateway"]["users"])
                u["enabled"] = not u.get("enabled", True)
            for kind in KINDS:
                self.adapter(kind).apply(st, self.sec, self.runner)
            self.check(st)

    def test_seed_1(self):
        self.run_seed(1)

    def test_seed_7(self):
        self.run_seed(7)

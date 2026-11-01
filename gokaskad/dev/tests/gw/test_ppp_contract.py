"""The adapters of work package 6 as the core sees them (SPEC §3, §7): registration, the interface, names of
units and interfaces, defaults; the stand-ins leave no trace; no secret in apply results, status,
requirements or the log; seeded random sequences keep every config in step with the state."""

import json
import random
import re
import sys
import unittest

import kaskad
from tests.gw.ppp_case import INBOUNDS, PppCase, PppNode, device, gw_state
from tests.gwstub import PPP_ADAPTERS, Stubbed

KINDS = ["ikev2", "l2tp", "pptp", "sstp"]
METHODS = ("requirements", "install", "uninstall", "render", "apply", "status", "issue", "ifaces", "default_port",
           "prepare", "addr_kinds", "unit_texts", "units", "defaults")
IFACE = re.compile(r"^[A-Za-z0-9_.-]{1,15}\*?$")               # what the core's table accepts (gw/net.py)


class StubHygieneTest(unittest.TestCase):
    def test_sys_modules_restored(self):
        before = {k: v for k, v in sys.modules.items() if k == "kaskad.secrets" or k.startswith("kaskad.gw")}
        attrs = {a: getattr(kaskad, a, None) for a in ("gw", "secrets")}
        with Stubbed(PPP_ADAPTERS) as gw:
            self.assertEqual(gw.registry.kinds(), KINDS)
        after = {k: v for k, v in sys.modules.items() if k == "kaskad.secrets" or k.startswith("kaskad.gw")}
        self.assertEqual(after, before)
        self.assertEqual({a: getattr(kaskad, a, None) for a in ("gw", "secrets")}, attrs)


class InterfaceTest(unittest.TestCase):
    def setUp(self):
        self.gw = Stubbed(PPP_ADAPTERS).__enter__()
        self.addCleanup(self.gw.__exit__, None, None, None)

    def test_every_adapter_implements_the_interface(self):
        for kind in KINDS:
            cls = self.gw.registry.get(kind)
            self.assertTrue(issubclass(cls, self.gw.base.Inbound), kind)
            self.assertEqual(cls.kind, kind)
            self.assertTrue(cls.engine and cls.title, kind)
            for m in METHODS:
                self.assertIn(m, dir(cls), "%s.%s" % (kind, m))

    def test_names_ports_defaults(self):
        a = self.gw.adapter
        self.assertEqual({k: a(k).ifaces() for k in KINDS},
                         {"l2tp": ["kgw-l2tp*"], "pptp": ["kgw-pptp*"], "sstp": ["kgw-sstp*"], "ikev2": []})
        for k in KINDS:
            for name in a(k).ifaces():
                self.assertRegex(name, IFACE)
        self.assertEqual({k: a(k).units() for k in KINDS},
                         {"l2tp": ["kaskad-gw-accel.service", "kaskad-gw-ipsec.service"],
                          "pptp": ["kaskad-gw-accel.service"], "sstp": ["kaskad-gw-accel.service"],
                          "ikev2": ["kaskad-gw-ipsec.service"]})
        self.assertEqual({k: a(k).default_port() for k in KINDS}, {"l2tp": 0, "pptp": 0, "sstp": 4433, "ikev2": 0})
        self.assertEqual({k: a(k).addr_kinds() for k in KINDS},
                         {"l2tp": ["ppp"], "pptp": ["ppp"], "sstp": ["ppp"], "ikev2": []})
        self.assertEqual(a("l2tp").defaults({}), {"ipsec": True, "plain": False})
        self.assertEqual(a("ikev2").defaults({}), {"cert": "self"})
        self.assertEqual(a("pptp").defaults({}), {})
        self.assertTrue(a("ikev2").dynamic)
        runner = PppNode().runner
        self.assertEqual(sorted(a("l2tp").unit_texts(runner)), ["kaskad-gw-accel.service", "kaskad-gw-ipsec.service"])
        self.assertEqual(sorted(a("ikev2").unit_texts(runner)), ["kaskad-gw-ipsec.service"])


class NoLeakTest(PppCase):
    def test_results_status_requirements_and_log_have_no_secrets(self):
        st = gw_state(INBOUNDS, [device("d-1", "Всё сразу", "windows", ["l2tp", "pptp", "sstp", "ikev2"], kind="pc")])
        out = []
        for kind in KINDS:
            a = self.adapter(kind)
            a.install(self.runner)
            out.append(json.dumps(a.apply(st, self.sec, self.runner), ensure_ascii=False))
            out.append(json.dumps(a.status(self.runner), ensure_ascii=False))
            out.append(json.dumps(a.requirements(self.runner), ensure_ascii=False))
        for key in ("dev.d-1.password", "l2tp.psk", "ikev2.ca.key", "ikev2.server.key", "tls.server.key"):
            self.assertIn(key, self.sec)
        self.assert_no_secrets("\n".join(out) + self.log_text())
        self.assert_no_secrets(json.dumps(st, ensure_ascii=False))


class SequenceTest(PppCase):
    """Devices come and go, lose access, get disabled; inbounds and the gateway switch; plain L2TP and IPsec
    toggle — after each step the logins of chap-secrets/allow and of swanctl are exactly the allowed devices,
    the units follow the state, issuance works for every allowed device."""

    def allowed(self, st, kind):
        """Logins allowed on a PPP inbound: the device login; PPTP — <login>-pptp (its own account)."""
        users = {u["id"]: u for u in st["gateway"]["users"]}
        tail = "-pptp" if kind == "pptp" else ""
        return sorted(d["login"] + tail for d in st["gateway"]["devices"]
                      if kind in d["access"] and d.get("enabled", True) and users[d["user"]].get("enabled", True))

    def check(self, st):
        acc, ips = self.gw.accel, self.gw.ipsec
        for kind in ("l2tp", "pptp", "sstp"):
            want = self.allowed(st, kind) if acc.wanted(st, kind) else []
            self.assertEqual(self.read("/etc/kaskad/gw/accel/allow/%s" % kind).split(), want, kind)
        chap = sorted(acc.chap_entries(self.read("/etc/kaskad/gw/accel/chap-secrets")))
        self.assertEqual(chap, sorted(set(lg for k in ("l2tp", "pptp", "sstp") if acc.wanted(st, k)
                                          for lg in self.allowed(st, k))))
        ips_by_login = dict((lg, e[1]) for lg, e in acc.chap_entries(self.read("/etc/kaskad/gw/accel/chap-secrets"))
                            .items())
        owners = json.loads(self.read("/etc/kaskad/gw/accel/users.json"))
        by_dev = {}
        for lg, ip in ips_by_login.items():
            by_dev.setdefault(owners[lg], set()).add(ip)
        self.assertTrue(all(len(v) == 1 for v in by_dev.values()))                     # one address per device
        self.assertEqual(len(set(ip for v in by_dev.values() for ip in v)), len(by_dev))  # ... of its own
        conf = self.read("/etc/swanctl/conf.d/kaskad.conf") if ips.kinds_wanted(st) else ""
        eap = sorted(re.findall(r"^        id = (\S+)$", conf, re.M))
        self.assertEqual(eap, self.allowed(st, "ikev2") if ips.ikev2_on(st) else [])
        self.assertEqual("kaskad-l2tp {" in conf, ips.l2tp_on(st))
        units = self.node.sysd.units
        self.assertEqual(units.get("kaskad-gw-accel.service", {}).get("active", False), bool(acc.kinds_wanted(st)))
        self.assertEqual(units.get("kaskad-gw-ipsec.service", {}).get("active", False), bool(ips.kinds_wanted(st)))
        for kind in KINDS:
            on = acc.wanted(st, kind) if kind != "ikev2" else ips.ikev2_on(st)
            if not on:
                continue
            for d in st["gateway"]["devices"]:
                if d["login"] + ("-pptp" if kind == "pptp" else "") in self.allowed(st, kind):
                    self.adapter(kind).issue(st, self.sec, d["id"], None)

    def run_seed(self, seed, steps=25):
        rnd = random.Random(seed)
        st = gw_state(INBOUNDS, [])
        self.node.ufw_active = True
        for kind in KINDS:
            self.adapter(kind).install(self.runner)
        n = 0
        for _ in range(steps):
            devs = st["gateway"]["devices"]
            op = rnd.choice(["add", "add", "del", "disable", "enable", "access", "toggle", "gw", "user", "plain"])
            if op == "add" or not devs:
                n += 1
                devs.append(device("d-%03d" % n, "Устройство %d" % n, rnd.choice(["ios", "windows", "tplink", "mikrotik"]),
                                   rnd.sample(KINDS, rnd.randint(1, 4)), login="dev-%03d" % n,
                                   user=rnd.choice(["u-1", "u-2"])))
            elif op == "del":
                devs.remove(rnd.choice(devs))
            elif op in ("disable", "enable"):
                rnd.choice(devs)["enabled"] = op == "enable"
            elif op == "access":
                rnd.choice(devs)["access"] = rnd.sample(KINDS, rnd.randint(0, 3))
            elif op == "toggle":
                cfg = st["gateway"]["inbounds"][rnd.choice(KINDS)]
                cfg["enabled"] = not cfg.get("enabled", True)
            elif op == "gw":
                st["gateway"]["enabled"] = not st["gateway"]["enabled"]
            elif op == "user":
                u = rnd.choice(st["gateway"]["users"])
                u["enabled"] = not u.get("enabled", True)
            else:
                cfg = st["gateway"]["inbounds"]["l2tp"]
                cfg[rnd.choice(["plain", "ipsec"])] = rnd.choice([True, False])
            for kind in KINDS:
                self.adapter(kind).apply(st, self.sec, self.runner)
            self.check(st)

    def test_seed_3(self):
        self.run_seed(3)

    def test_seed_11(self):
        self.run_seed(11)

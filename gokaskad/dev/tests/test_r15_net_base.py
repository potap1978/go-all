"""3.3.7.15 F6 — «Сеть шлюза» /16 (SPEC-R1 §3 F6; PLAN-n13 Ш4 А, Б-6, В8, В10, Е2, Е3, З-1): the network of the gateway
changed by one action (``ops.net_base``) and only there — ``gw enable --base`` refuses a move of the addresses of
devices (Б-6) and any network outside RFC 1918; the refusals before any lock (something of the node in the network,
the Address of a key of an exit) with ``suggest_base``; the plan «Что изменится» and the confirmation; the write in one
``_change`` through ``pending.guard(scope «base»)`` (F7: the administrator in the old or the new network waits for a
confirmation, a live wait — 409); the connections of the old network cut (``_renet``); the marks «ждут новых настроек»
(F1); «Вернуть прежнюю сеть» (``net_base_undo``); the API; the texts of З-1; the old versions read the new state."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from kaskad import paths, state, util
from kaskad.errors import USAGE, ConflictError, KaskadError
from kaskad.gw import addr, exits, model, ops, pending, reissue
from kaskad.gw.inbounds import accel
from kaskad.i18n import tr
from tests.fakes_gw import VLESS_A, awg_line, register_dummies, write_pool
from tests.gw.ppp_case import ike_sa
from tests.panel.panelcase import PanelCase

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKER = "10.78.0.0/16 dev docker0 proto kernel scope link src 10.78.0.1"


class NetCase(PanelCase):
    def dummies(self):
        return register_dummies(("wg", {"ifaces": ["kgw-wg"]}),
                                ("awg2", {"default_port": 41023, "ifaces": ["kgw-awg2"]}),
                                ("sstp", {"default_port": 4433, "proto": "tcp"}),
                                ("ikev2", {"default_port": 500, "dynamic": True}),
                                ("ocserv", {"default_port": 8443, "proto": "tcp"}))

    def setUp(self):
        super().setUp()
        self.runs, self.stops, self.active = [], [], set()
        self.ppp, self.cut_ppp, self.sas, self.cut_sas = [], [], "", []
        r = self.w.runner
        mine = [(["systemd-run"], self._systemd_run),
                (["systemctl", "is-active"], lambda argv, inp: (0, "active\n", "") if argv[2][:-6] in self.active
                 else (3, "inactive\n", "")),
                (["systemctl", "stop"], lambda argv, inp: (self.stops.append(argv[2]) or 0, "", "")),
                ([accel.accel_cmd()], self._accel),
                (["/usr/sbin/swanctl"], self._swanctl)]
        r._handlers[:0] = mine
        self.gateway(inbounds=("wg", "awg2", "sstp"))
        self.did = ops.device_add(r, self.uid, "Ноутбук", kind="laptop", platform="windows",
                                  access=["wg"])["device"]["id"]
        self.ip = self.dev(self.did)["ips"]["wg"]

    def _systemd_run(self, argv, inp):
        self.runs.append(argv)
        return 0, "", ""

    def _accel(self, argv, inp):
        a = argv[argv.index("-p") + 2:]
        if "-P" in argv:
            a = argv[argv.index("-P") + 2:]
        if a[:2] == ["show", "sessions"]:
            head = "ifname | username | ip | type | state | uptime-raw | rx-bytes-raw | tx-bytes-raw"
            rows = ["%s | %s | %s | sstp | active | 30 | 1 | 2" % s for s in self.ppp]
            return 0, "\n".join([head, "-" * 40] + rows) + "\n", ""
        if a[:2] == ["terminate", "if"]:
            self.cut_ppp.append(a[2:])
            return 0, "", ""
        return 0, "", ""

    def _swanctl(self, argv, inp):
        if "--list-sas" in argv:
            return 0, self.sas, ""
        if "--terminate" in argv:
            self.cut_sas.append(argv[argv.index("--ike-id") + 1])
            return 0, "", ""
        return 0, "", ""

    # ── helpers ──
    def dev(self, did):
        return model.get_device(state.load(), did)

    def base(self):
        return str(addr.base(state.load()))

    def last(self):
        try:
            with open(ops.settings_last_path(), encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            return {}

    def admin(self):
        return {"via": "panel", "ip": self.ip}

    def events(self):
        d = paths.gw_pending_events()
        out = []
        for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            with open(os.path.join(d, n), encoding="utf-8") as f:
                out.append(json.load(f))
        return out


class EnableBaseTest(NetCase):
    def test_enable_base_with_devices_refused(self):
        """Б-6: ``gw enable --base`` (and POST /api/gw/enable {base}) with devices moved their addresses without a
        word; now — a refusal that names the place of the panel, nothing changed. Fails before F6: the base moved."""
        before = state.load()
        with self.assertRaises(ConflictError) as cm:
            ops.enable(self.w.runner, base="10.78.0.0/16")
        self.assertIn("Настройки › Шлюз › Сеть шлюза", str(cm.exception))
        self.assertNotIn("enable --base", str(cm.exception))
        self.assertEqual(cm.exception.data["why"], "net_base")
        self.assertEqual(self.base(), "10.77.0.0/16")
        self.assertEqual(self.dev(self.did)["ips"], model.get_device(before, self.did)["ips"])
        ops.enable(self.w.runner, base="10.77.0.0/16")                 # the same network: as before
        self.assertEqual(self.base(), "10.77.0.0/16")

    def test_enable_base_outside_rfc1918_refused(self):
        """`gw enable --base 127.1.0.0/16` (169.254, 198.18, 100.64) passed ``parse_base`` (``is_private``) — now a
        USAGE before any package or lock. Fails before F6: 127.1.0.0/16 was taken."""
        for bad in ("127.1.0.0/16", "169.254.0.0/16", "198.18.0.0/16", "100.64.0.0/16", "0.1.0.0/16"):
            with self.subTest(base=bad):
                with self.assertRaises(KaskadError) as cm:
                    ops.enable(self.w.runner, base=bad)
                self.assertEqual(cm.exception.code, USAGE)
                self.assertEqual(cm.exception.data, {"field": "base"})
                self.assertEqual(self.base(), "10.77.0.0/16")

    def test_restore_of_a_copy_still_moves(self):
        """``backup._apply_gateway`` passes ``rebase_ok``: the network of the copy with its devices, as before."""
        ops.enable(self.w.runner, base="10.78.0.0/16", rebase_ok=True)
        self.assertEqual(self.base(), "10.78.0.0/16")
        self.assertTrue(self.dev(self.did)["ips"]["wg"].startswith("10.78.1."))
        from kaskad import backup
        with open(backup.__file__, encoding="utf-8") as f:
            self.assertIn("rebase_ok=True", f.read())

    def test_overlap_warning_names_the_panel(self):
        """З-1: the warning of a base that overlaps the node — the place of the panel, not ``gw enable --base``."""
        self.w.ip.routes["main"].append("10.77.0.0/16 dev docker0 proto kernel scope link src 10.77.0.1")
        res = ops.enable(self.w.runner)
        w = [x for x in res["warnings"] if "10.77.0.0/16" in x and "docker0" in x]
        self.assertTrue(w, res["warnings"])
        self.assertIn("Настройки › Шлюз › Сеть шлюза", w[0])
        self.assertNotIn("enable --base", w[0])


class AddrTest(unittest.TestCase):
    def test_rfc1918_and_home(self):
        import ipaddress
        for good in ("10.77.0.0/16", "172.16.0.0/16", "172.31.0.0/16", "192.168.0.0/16"):
            self.assertTrue(addr.rfc1918(ipaddress.IPv4Network(good)), good)
            self.assertEqual(str(addr.new_base(good)), good)
        for bad in ("127.1.0.0/16", "169.254.0.0/16", "198.18.0.0/16", "100.64.0.0/16", "172.32.0.0/16",
                    "240.1.0.0/16"):
            with self.assertRaises(KaskadError):
                addr.new_base(bad)
        self.assertEqual(addr.parse_base("198.18.0.0/16").prefixlen, 16)     # reading stays as it was
        self.assertEqual(addr.home_clash("192.168.0.0/16"), ["192.168.0.0/24", "192.168.1.0/24", "192.168.8.0/24"])
        self.assertEqual(addr.home_clash("10.0.0.0/16"), ["10.0.0.0/24", "10.0.1.0/24"])
        self.assertEqual(addr.home_clash("10.1.0.0/16"), ["10.1.1.0/24"])
        self.assertEqual(addr.home_clash("172.16.0.0/16"), ["172.16.0.0/24"])
        self.assertEqual(addr.home_clash("10.78.0.0/16"), [])


class RefuseTest(NetCase):
    def test_bad_networks_refused_before_anything(self):
        for bad in ("127.1.0.0/16", "169.254.0.0/16", "198.18.0.0/16", "100.64.0.0/16", "10.77.0.0/24", "x", None):
            with self.subTest(base=bad):
                with self.assertRaises(KaskadError) as cm:
                    ops.net_base(self.w.runner, bad, yes=True)
                self.assertEqual(cm.exception.code, USAGE)
        self.assertEqual(self.base(), "10.77.0.0/16")
        self.assertEqual(self.runs, [])

    def test_devices_without_yes_change_nothing(self):
        """Devices hold addresses: the change needs ``yes`` — ConflictError with the plan, nothing written."""
        before = json.dumps(state.load(), sort_keys=True)
        with self.assertRaises(ConflictError) as cm:
            ops.net_base(self.w.runner, "10.78.0.0/16")
        plan = cm.exception.data["plan"]
        self.assertEqual(cm.exception.data["need"], "yes")
        self.assertEqual(plan["changed"], {"base": {"old": "10.77.0.0/16", "new": "10.78.0.0/16"}})
        self.assertEqual(plan["dns"], {"old": "10.77.0.1", "new": "10.78.0.1"})
        from kaskad.gw import registry
        self.assertTrue(any(registry.title("wg") in ln and "Ноутбук" in ln and "не подключится" in ln
                            for ln in plan["lines"]), plan["lines"])
        self.assertTrue(any(registry.title("sstp") in ln and "переподключатся сами" in ln for ln in plan["lines"]),
                        plan["lines"])
        self.assertNotIn("st_new", plan)
        self.assertEqual(json.dumps(state.load(), sort_keys=True), before)
        dry = ops.net_base(self.w.runner, "10.78.0.0/16", dry=True)
        self.assertTrue(dry["dry"])
        self.assertEqual(dry["plan"]["changed"], plan["changed"])
        self.assertEqual(json.dumps(state.load(), sort_keys=True), before)
        self.assertNotIn("base", self.last())

    def test_wide_home_warns_and_needs_yes(self):
        """В10, Е2: 192.168.0.0/16 — a warning and ``yes`` even without devices; a popular home /24 — a warning.
        REV-3315-g2 Н-4: 192.168.0.0/16 — that one warning only (its home /24 are inside it: no second line)."""
        ops.device_del(self.w.runner, self.did)
        plan = ops.net_base(self.w.runner, "192.168.0.0/16", dry=True)["plan"]
        self.assertTrue(plan["need_yes"])
        self.assertEqual(plan["warnings"], [tr("gw_net.base.wide_home")])
        with self.assertRaises(ConflictError):
            ops.net_base(self.w.runner, "192.168.0.0/16")
        self.assertEqual(self.base(), "10.77.0.0/16")
        plan = ops.net_base(self.w.runner, "10.1.0.0/16", dry=True)["plan"]
        self.assertFalse(plan["need_yes"])                              # no device holds an address: no question
        self.assertTrue(any("10.1.1.0/24" in w for w in plan["warnings"]), plan["warnings"])

    def test_docker_network_refused_with_a_free_one(self):
        self.w.ip.routes["main"].append(DOCKER)
        with self.assertRaises(ConflictError) as cm:
            ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        self.assertEqual(cm.exception.data["why"], "busy")
        self.assertIn("docker0", str(cm.exception))
        self.assertEqual(cm.exception.data["suggest_base"], "10.79.0.0/16")
        self.assertEqual(self.base(), "10.77.0.0/16")
        view = ops.net_view(self.w.runner)
        c = dict((x["base"], x) for x in view["candidates"])
        self.assertTrue(c["10.77.0.0/16"]["current"])
        self.assertFalse(c["10.78.0.0/16"]["free"])
        self.assertTrue(c["10.79.0.0/16"]["free"])
        self.assertIn("10.78.0.0/16", [t["net"] for t in view["taken"]])
        self.assertEqual(view["suggest_base"], "10.79.0.0/16")

    def test_leg_and_exit_key_inside_refused(self):
        """The address of a leg kawgN (a running exit) and the Address of a key of the pool (not running) in the new
        network — refused (the exit would stop: gw_net of the parser), with ``suggest_base``."""
        with self.assertRaises(ConflictError) as cm:                     # kawg2 holds 10.8.2.5/32 (the fake leg)
            ops.net_base(self.w.runner, "10.8.0.0/16", yes=True)
        self.assertEqual(cm.exception.data["why"], "busy")
        self.assertIn("kawg2", str(cm.exception))
        write_pool([VLESS_A, awg_line(2), awg_line(3, name="Рига", address="10.90.0.7/32")])
        with self.assertRaises(ConflictError) as cm:
            ops.net_base(self.w.runner, "10.90.0.0/16", yes=True)
        self.assertEqual(cm.exception.data["why"], "exit_key")
        self.assertEqual(cm.exception.data["keys"], ["10.90.0.7"])
        self.assertIn("Рига", str(cm.exception))
        self.assertEqual(cm.exception.data["suggest_base"], "10.78.0.0/16")
        self.assertEqual(self.base(), "10.77.0.0/16")
        view = ops.net_view(self.w.runner)
        self.assertIn("10.90.0.7/32", [t["net"] for t in view["taken"]])

    def test_same_network_nothing(self):
        res = ops.net_base(self.w.runner, "10.77.0.0/16")
        self.assertEqual((res["changed"], res["pending"], res["id"]), ({}, None, None))
        self.assertIn("менять нечего", res["text"])

    def test_ver(self):
        ver = ops.net_view(self.w.runner)["ver"]
        with self.assertRaises(KaskadError) as cm:
            ops.net_base(self.w.runner, "10.78.0.0/16", yes=True, ver="000000000000")
        self.assertEqual(getattr(cm.exception, "key", None), "object_changed")
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True, ver=ver)
        self.assertEqual(res["base"], "10.78.0.0/16")
        self.assertNotEqual(ops.net_view(self.w.runner)["ver"], ver)


class ApplyTest(NetCase):
    def setUp(self):
        super().setUp()
        r = self.w.runner
        self.mt = ops.device_add(r, self.uid, "hAP", kind="router", platform="mikrotik", access=["sstp"])["device"]["id"]
        self.win = ops.device_add(r, self.uid, "ПК", kind="laptop", platform="windows", access=["sstp"])["device"]["id"]
        with state.locked():
            st = state.load()
            st["forwards"] = [{"proto": "tcp", "port": 8080, "to": self.ip, "to_port": 80, "name": "", "origin": "k3",
                               "added": ""}]
            state.save(st)

    def test_everything_moves(self):
        """After the change: the addresses of devices moved by the same shift, ``subnet`` of every kind = addr.subnet,
        the DNS, a forward to a device, conntrack of the old network, the hard mark ``net`` at WireGuard and at SSTP
        of MikroTik (not at SSTP of Windows), the record «Вернуть прежнюю сеть»."""
        self.w.conntrack.calls[:] = []
        old_ppp = self.dev(self.mt)["ips"]["ppp"]
        res = ops.net_base(self.w.runner, "172.20.0.0/16", yes=True)
        st = state.load()
        self.assertEqual((res["base"], res["old"], res["dns"]), ("172.20.0.0/16", "10.77.0.0/16", "172.20.0.1"))
        self.assertEqual(model.gw(st)["net"], {"base": "172.20.0.0/16", "dns": "172.20.0.1"})
        self.assertEqual(self.dev(self.did)["ips"]["wg"], self.ip.replace("10.77.", "172.20.", 1))
        self.assertEqual(self.dev(self.mt)["ips"]["ppp"], old_ppp.replace("10.77.", "172.20.", 1))
        for kind in model.installed_kinds(st):
            if kind in addr.KIND_SUBNET:
                self.assertEqual(model.inbound(st, kind)["subnet"], addr.subnet(kind, st), kind)
        self.assertEqual(st["forwards"][0]["to"], self.ip.replace("10.77.", "172.20.", 1))
        self.assertIn(["-D", "-s", "10.77.0.0/16"], self.w.conntrack.calls)
        marks = reissue.load()
        self.assertEqual(marks["wg"][self.did]["what"], ["net"])
        self.assertTrue(marks["wg"][self.did]["hard"])
        self.assertEqual(marks["wg"][self.did]["by"], res["id"])
        self.assertIn(self.mt, marks.get("sstp", {}))
        self.assertNotIn(self.win, marks.get("sstp", {}))
        last = self.last()["base"]
        self.assertEqual((last["id"], last["old"]["base"], last["new"]["base"]), (res["id"], "10.77.0.0/16",
                                                                                  "172.20.0.0/16"))
        self.assertEqual(sorted(last["marked"]), ["sstp", "wg"])
        self.assertIsNone(res["pending"])                               # no administrator given: nothing to hit
        self.assertIn("172.20.0.0/16", res["text"])
        self.assertEqual(res["forwards"], [{"proto": "tcp", "port": 8080, "old": self.ip,
                                            "new": self.ip.replace("10.77.", "172.20.", 1)}])
        self.assertNotIn("SERVERKEY", json.dumps(res, ensure_ascii=False, default=str))
        view = ops.net_view(self.w.runner)
        self.assertEqual(view["last"]["old"], "10.77.0.0/16")

    def test_old_sessions_cut(self):
        """``_renet``: the PPP sessions and the IKEv2 SAs of the old network go, the others stay; OpenConnect —
        its logins of the old network disconnected."""
        ops.inbound_install(self.w.runner, "ikev2")
        ops.inbound_install(self.w.runner, "ocserv")
        oc = []
        self.ads["ocserv"].disconnect = lambda runner, logins: oc.extend(logins)
        self.ads["ocserv"].sessions = [{"login": "a", "ip": "10.77.11.10"}, {"login": "b", "ip": "198.51.100.4"}]
        self.ppp = [("ppp0", "hap", "10.77.5.10"), ("ppp1", "other", "192.0.2.9")]
        self.sas = ike_sa(5, "kaskad-ikev2", eap="x", vip="10.77.8.11") + ike_sa(6, "kaskad-ikev2", eap="y",
                                                                                   vip="192.0.2.8")
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        self.assertIn(["ppp0", "soft"], self.cut_ppp)
        self.assertNotIn(["ppp1", "soft"], self.cut_ppp)
        self.assertEqual(self.cut_sas, ["5"])
        self.assertEqual(oc, ["a"])
        self.assertEqual((res["cut"]["ikev2"], res["cut"]["ocserv"]), (1, 1))

    def test_a_write_of_a_protocol_keeps_the_record(self):
        """settings-last.json is shared with F3: a write of a protocol keeps ``base`` (``ops._last_read``). Fails
        before F6: the record of the network was dropped by the next apply of a protocol."""
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        ops._last_write("wg", {"id": "a-00000001", "at": 1, "old": {"port": 1}, "new": {"port": 2}})
        self.assertEqual(self.last()["base"]["id"], res["id"])
        self.assertEqual(ops._last_read()["base"]["id"], res["id"])


class PendingTest(NetCase):
    def test_admin_in_the_old_network_waits_and_undo_drops_it(self):
        """F7 × F6: the administrator through WireGuard (his address in the old network) — the wait of «base», the
        timer of 2 min; «Вернуть прежнюю сеть» by hand — the network back, the wait and its timer gone, the marks
        off."""
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True, who=self.admin())
        p = res["pending"]
        self.assertEqual(p["scope"], "base")
        self.assertTrue(any("--on-active=120s" in a for a in self.runs[-1]), self.runs)
        self.assertEqual(pending.read()["id"], res["id"])
        self.assertIn("Вы подключены через сеть шлюза", " ".join(res["plan"]["warnings"]))
        self.assertIn("kaskadctl gw pending confirm", res["text"])
        out = ops.net_base_undo(self.w.runner)
        self.assertEqual(out["base"], "10.77.0.0/16")
        self.assertEqual(self.base(), "10.77.0.0/16")
        self.assertEqual(self.dev(self.did)["ips"]["wg"], self.ip)
        self.assertIsNone(pending.read())
        self.assertTrue(any(u.startswith("kaskad-gw-pending-" + p["token"]) for u in self.stops), self.stops)
        self.assertNotIn(self.did, reissue.load().get("wg", {}))
        self.assertNotIn("base", self.last())
        with self.assertRaises(KaskadError) as cm:
            ops.net_base_undo(self.w.runner)
        self.assertEqual(cm.exception.code, USAGE)

    def test_admin_in_the_new_network_waits_too(self):
        new_ip = self.ip.replace("10.77.", "10.78.", 1)
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True, who={"via": "panel", "ip": new_ip})
        self.assertIsNotNone(res["pending"])

    def test_admin_outside_the_gateway_networks_no_wait(self):
        """The panel opened from outside the tunnel (an address of neither network): no hit — no wait."""
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True, who={"via": "panel", "ip": "203.0.113.5"})
        self.assertIsNone(res["pending"])
        self.assertIsNone(pending.read())

    def test_not_confirmed_reverts(self):
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True, who=self.admin())
        tok = res["pending"]["token"]
        done = pending.expire(self.w.runner, tok, now=res["pending"]["deadline"] + 1)
        self.assertEqual(done["done"], "reverted")
        self.assertEqual(self.base(), "10.77.0.0/16")
        self.assertEqual(self.dev(self.did)["ips"]["wg"], self.ip)
        self.assertIsNone(pending.read())
        ev = self.events()[-1]
        self.assertEqual((ev["kind"], ev["scope"], ev["why"]), ("settings_reverted", "base", "no_confirm"))
        self.assertEqual(pending.event_text("settings_reverted", ev, "ru"),
                         "Сеть шлюза возвращена: подтверждения за 2 мин не было")

    def test_confirmed_stays(self):
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True, who=self.admin())
        pending.confirm(self.w.runner, res["pending"]["token"])
        self.assertEqual(pending.expire(self.w.runner, res["pending"]["token"])["done"], "nothing")
        self.assertEqual(self.base(), "10.78.0.0/16")

    def test_live_wait_of_a_protocol_refuses(self):
        """С-3: a live wait of WireGuard — the change of the network (it touches every protocol) is 409, settings-last
        of WireGuard and the network untouched."""
        ops._last_write("wg", {"id": "a-0000000a", "at": 1, "old": {"port": 51820}, "new": {"port": 51999}})
        pending.arm(self.w.runner, "wg", "a-0000000a")
        before = self.last()["wg"]
        with self.assertRaises(KaskadError) as cm:
            ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        self.assertEqual(cm.exception.key, "pending_busy")
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.last()["wg"], before)
        self.assertNotIn("base", self.last())
        self.assertEqual(self.base(), "10.77.0.0/16")

    def test_no_record_no_apply_for_the_hit(self):
        """«Добавка координатора 3»: settings-last can't be written and the administrator would be cut — 409
        ``pending_no_undo``, nothing applied."""
        with mock.patch.object(ops, "_last_can_write", lambda: False):
            with self.assertRaises(KaskadError) as cm:
                ops.net_base(self.w.runner, "10.78.0.0/16", yes=True, who=self.admin())
        self.assertEqual(cm.exception.key, "pending_no_undo")
        self.assertEqual(self.base(), "10.77.0.0/16")

    def test_no_record_said_in_the_plan(self):
        """«Добавка координатора 3»: the plan («Что изменится», dry) says the reason before «Применить» — only with the
        hit (without it the change goes without a wait, as in the contract)."""
        no_undo = tr("gw_ops.settings.no_undo", title=tr("gw_pending.base"), err=ops.settings_last_path())
        with mock.patch.object(ops, "_last_can_write", lambda: False):
            hit = ops.net_base(self.w.runner, "10.78.0.0/16", dry=True, who=self.admin())
            plain = ops.net_base(self.w.runner, "10.78.0.0/16", dry=True)
        self.assertIn(no_undo, hit["plan"]["warnings"])
        self.assertNotIn(no_undo, plain["plan"]["warnings"])
        self.assertNotIn(no_undo, ops.net_base(self.w.runner, "10.78.0.0/16", dry=True, who=self.admin())["plan"]
                         ["warnings"])
        self.assertEqual(self.base(), "10.77.0.0/16")

    def test_revert_of_a_changed_network_is_refused(self):
        """The network changed by another write in the window (a restore of a copy): the timer reverts nothing —
        «changed», the record goes."""
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True, who=self.admin())
        ops.enable(self.w.runner, base="10.79.0.0/16", rebase_ok=True)
        done = pending.expire(self.w.runner, res["pending"]["token"], now=res["pending"]["deadline"] + 1)
        self.assertEqual(done["done"], "failed")
        self.assertEqual(done["event"]["why"], "changed")
        self.assertEqual(self.base(), "10.79.0.0/16")
        self.assertNotIn("base", self.last())


class PreviewTest(NetCase):
    def test_my_new_settings_count_as_given_for_the_same_network(self):
        """П5 З-2: the device that downloaded its settings of the new network has no mark «net» after the change of
        exactly that network; «Вернуть прежнюю сеть» marks it again (it holds the new network)."""
        other = ops.device_add(self.w.runner, self.uid, "Планшет", kind="tablet", platform="android",
                               access=["wg"])["device"]["id"]
        ops.issue_preview(self.w.runner, None, "wg", {"base": "10.78.0.0/16"}, who=self.admin())
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        self.assertEqual(sorted(reissue.load()["wg"]), [other])
        self.assertEqual([r["id"] for r in res["reissue"]["wg"]], [other])
        self.assertIn(self.did, self.last()["base"]["marked"]["wg"])          # the undo knows it holds the new one
        ops.net_base_undo(self.w.runner)
        m = reissue.load()["wg"]
        self.assertEqual(sorted(m), [self.did])
        self.assertEqual((m[self.did]["what"], m[self.did]["hard"]), (["net"], True))

    def test_my_new_settings_of_the_new_network(self):
        """F7 «Скачать мои новые настройки» for F6: the administrator through WireGuard gets his settings of the new
        network before «Применить» (else his tunnel goes and only SSH confirms) — the device found by its address of
        now, its Address and the DNS of the new network, nothing written."""
        before = util.read_text(paths.state())
        res = ops.issue_preview(self.w.runner, None, "wg", {"base": "10.78.0.0/16"}, who=self.admin())
        self.assertEqual(res["device"], self.did)
        body = res["items"][0]["body"]
        self.assertIn("Address = %s" % self.ip.replace("10.77.", "10.78.", 1), body)
        self.assertEqual(util.read_text(paths.state()), before)
        with self.assertRaises(KaskadError) as cm:
            ops.issue_preview(self.w.runner, None, "wg", {"base": "127.1.0.0/16"}, who=self.admin())
        self.assertEqual(cm.exception.code, USAGE)
        r = self.call("POST", "/api/gw/inbounds/wg/settings/issue", {"device": self.did, "base": "10.78.0.0/16"})
        self.assertEqual(r.status, 200, r)
        self.assertIn("10.78.1.", r.json()["items"][0]["body"])
        self.assertEqual(self.base(), "10.77.0.0/16")
        r = self.call("POST", "/api/gw/inbounds/wg/settings/issue", {"device": self.did})     # no base: as before
        self.assertEqual(r.status, 200, r)
        self.assertIn("Address = %s" % self.ip, r.json()["items"][0]["body"])

    def test_base_of_the_preview_is_checked_as_f6(self):
        """The field ``base`` of «Скачать мои новые настройки» is foreign input: garbage — 400 (``../``, 127.0.0.0,
        empty, not a string, not a /16, outside RFC 1918), a network taken on the node or holding a key of an exit —
        409 as ``net_base``; nothing written."""
        before = util.read_text(paths.state())
        url = "/api/gw/inbounds/wg/settings/issue"
        for bad in ("../", "../../etc/passwd", "127.0.0.0", "127.0.0.0/16", "", "   ", None, 1078, True,
                    ["10.78.0.0/16"], {"base": "10.78.0.0/16"}, "10.78.0.0/24", "10.78.0.1/16", "169.254.0.0/16",
                    "x" * 40):
            with self.subTest(base=bad):
                doc = self.err(400, "POST", url, {"device": self.did, "base": bad})
                self.assertIn(doc["code"], ("field", USAGE), doc)
        self.w.ip.routes["main"].append(DOCKER)
        doc = self.err(409, "POST", url, {"device": self.did, "base": "10.78.0.0/16"})
        self.assertEqual(doc["data"]["why"], "busy")
        write_pool([VLESS_A, awg_line(2), awg_line(3, name="Рига", address="10.90.0.7/32")])
        self.assertEqual(self.err(409, "POST", url, {"device": self.did, "base": "10.90.0.0/16"})["data"]["why"],
                         "exit_key")
        for bad in ("../", "127.0.0.0", "", 5):
            with self.subTest(core=bad):
                with self.assertRaises(KaskadError) as cm:
                    ops.issue_preview(self.w.runner, self.did, "wg", {"base": bad})
                self.assertEqual(cm.exception.code, USAGE)
        self.assertEqual(util.read_text(paths.state()), before)


class UndoTest(NetCase):
    def test_undo_twice_and_moved(self):
        a = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        ops.net_base(self.w.runner, "10.79.0.0/16", yes=True)
        out = ops.net_base_undo(self.w.runner)                          # the last change only
        self.assertEqual((out["base"], out["old"]), ("10.78.0.0/16", "10.79.0.0/16"))
        self.assertEqual(self.dev(self.did)["ips"]["wg"], self.ip.replace("10.77.", "10.78.", 1))
        with self.assertRaises(KaskadError):
            ops.net_base_undo(self.w.runner)
        self.assertIsNotNone(a["id"])
        ops.net_base(self.w.runner, "10.80.0.0/16", yes=True)
        ops.enable(self.w.runner, base="10.81.0.0/16", rebase_ok=True)   # changed by another action since
        with self.assertRaises(ConflictError) as cm:
            ops.net_base_undo(self.w.runner)
        self.assertEqual(cm.exception.data["why"], "moved")
        self.assertNotIn("base", self.last())
        self.assertEqual(self.base(), "10.81.0.0/16")

    def test_given_meanwhile_marked_again(self):
        """A device given the settings of the new network meanwhile gets the hard mark again after the undo; one that
        never got them — no mark (its old settings work again)."""
        other = ops.device_add(self.w.runner, self.uid, "Телефон", kind="phone", platform="android",
                               access=["wg"])["device"]["id"]
        ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        reissue.issued("wg", other)
        out = ops.net_base_undo(self.w.runner)
        marks = reissue.load().get("wg", {})
        self.assertNotIn(self.did, marks)
        self.assertEqual(marks[other]["what"], ["net"])
        self.assertEqual(out["reissue"], {"wg": [other]})
        self.assertIn("Телефон", out["text"])

    def test_failed_undo_by_hand_keeps_the_wait(self):
        """«Вернуть прежнюю сеть» by hand refused (the old network taken since): the wait of «base» and its timer stay
        — the revert of the timer is still there (the wait goes only once the network is back)."""
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True, who=self.admin())
        self.w.ip.routes["main"].append("10.77.0.0/16 dev docker0 proto kernel scope link src 10.77.0.1")
        with self.assertRaises(ConflictError):
            ops.net_base_undo(self.w.runner)
        self.assertEqual(pending.read()["token"], res["pending"]["token"])
        self.assertEqual(self.stops, [])

    def test_old_network_taken_since(self):
        ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        self.w.ip.routes["main"].append("10.77.0.0/16 dev docker0 proto kernel scope link src 10.77.0.1")
        with self.assertRaises(ConflictError) as cm:
            ops.net_base_undo(self.w.runner)
        self.assertEqual(cm.exception.data["why"], "busy")
        self.assertEqual(self.base(), "10.78.0.0/16")
        self.assertIn("base", self.last())


class ApiTest(NetCase):
    def test_routes(self):
        v = self.ok("GET", "/api/gw/net")
        self.assertEqual((v["base"], v["dns"], v["default"]), ("10.77.0.0/16", "10.77.0.1", "10.77.0.0/16"))
        self.assertEqual(v["suggest_base"], "10.78.0.0/16")
        self.assertIsNone(v["last"])
        self.assertTrue(v["ver"])
        d = self.ok("PUT", "/api/gw/net/base", {"base": "10.78.0.0/16", "dry": True})
        self.assertTrue(d["dry"])
        self.assertEqual(d["plan"]["changed"]["base"]["new"], "10.78.0.0/16")
        self.assertEqual(self.err(400, "PUT", "/api/gw/net/base", {"base": "10.78.0.0/16", "yes": True})["code"],
                         "ver_required")
        self.assertEqual(self.err(400, "PUT", "/api/gw/net/base", {"base": "10.78.0.0/16", "net": "x"})["code"],
                         "field")
        self.err(400, "PUT", "/api/gw/net/base", {"base": "127.1.0.0/16", "dry": True})
        self.err(400, "PUT", "/api/gw/net/base", {"base": "10.78.0.0/16", "dry": "yes"})
        e = self.err(409, "PUT", "/api/gw/net/base", {"base": "10.78.0.0/16", "ver": v["ver"]})
        self.assertEqual(e["data"]["need"], "yes")
        self.assertEqual(self.err(409, "PUT", "/api/gw/net/base", {"base": "10.78.0.0/16", "yes": True,
                                                                    "ver": "000000000000"})["code"], "object_changed")
        r = self.ok("PUT", "/api/gw/net/base", {"base": "10.78.0.0/16", "yes": True, "ver": v["ver"]})
        self.assertEqual(r["base"], "10.78.0.0/16")
        self.assertNotIn("apply", r)
        self.assertIsNone(r["pending"])
        self.assertEqual(self.ok("GET", "/api/gw/net")["last"]["old"], "10.77.0.0/16")
        self.err(400, "POST", "/api/gw/net/undo", {"x": 1})
        u = self.ok("POST", "/api/gw/net/undo", {})
        self.assertEqual(u["base"], "10.77.0.0/16")
        self.assertEqual(self.err(400, "POST", "/api/gw/net/undo", {})["code"], USAGE)


class ExitKeyTest(NetCase):
    def test_refused_key_says_the_panel_and_a_free_network(self):
        """Е3, З-1: the key refused for its Address in the network of this gateway — the words name «Сеть шлюза» of the
        panel (never ``gw enable --base``) and a free network, ``data.suggest_base`` for the button."""
        self.w.script.add_result = (1, "⚠ ключ\n@K3:egress_fail:skipped:gw_net:10.77.0.0/16\n", "")
        with self.assertRaises(KaskadError) as cm:
            ops.exit_add(self.w.runner, VLESS_A.replace("A", "B", 1))
        text = str(cm.exception)
        self.assertIn("Настройки › Шлюз › Сеть шлюза", text)
        self.assertNotIn("enable --base", text)
        self.assertIn("10.78.0.0/16", text)
        self.assertEqual(cm.exception.data["suggest_base"], "10.78.0.0/16")

    def test_preview_row_carries_the_free_network(self):
        """Е3 in the preview: the row of a key whose Address is in this gateway's network — its problem names the
        panel, ``suggest_base`` for the button «Сменить сеть шлюза на …»; the internal flag never leaves."""
        from tests.fakes_gw import awg_conf
        res = ops.exit_preview(self.w.runner, awg_conf(5, address="10.77.0.10/32"))
        row = [x for x in res["items"] if x.get("problem")][0]
        self.assertIn("Настройки › Шлюз › Сеть шлюза", row["problem"])
        self.assertEqual(row["suggest_base"], "10.78.0.0/16")
        self.assertNotIn("gw_net", row)
        ok = ops.exit_preview(self.w.runner, awg_conf(6, address="10.8.6.5/32"))
        self.assertTrue(all("suggest_base" not in x for x in ok["items"]))

    def test_partly_added_carries_the_hint(self):
        self.w.script.add_result = (0, "@K3:egress_warn:skipped:gw_net:1:10.77.0.0/16\n", "")
        res = ops.exit_add(self.w.runner, VLESS_A.replace("A", "C", 1))
        self.assertEqual(res["suggest_base"], "10.78.0.0/16")
        self.assertTrue(any("Сеть шлюза" in w for w in res["warnings"]), res["warnings"])


class TextsTest(unittest.TestCase):
    def test_no_enable_base_hint_left(self):
        """З-1: ``grep -rn "enable --base" lib/kaskad/i18n install.sh README.md`` — empty."""
        hits = []
        for top in (os.path.join(REPO, "lib", "kaskad", "i18n"),):
            for d, _dirs, files in os.walk(top):
                for n in files:
                    p = os.path.join(d, n)
                    with open(p, encoding="utf-8", errors="replace") as f:
                        if "enable --base" in f.read():
                            hits.append(p)
        for n in ("install.sh", "README.md"):
            with open(os.path.join(REPO, n), encoding="utf-8", errors="replace") as f:
                if "enable --base" in f.read():
                    hits.append(n)
        self.assertEqual(hits, [])

    def test_en_has_no_cyrillic(self):
        import re
        with open(os.path.join(REPO, "lib", "kaskad", "i18n", "catalog", "en", "gw_net.json"), encoding="utf-8") as f:
            doc = json.load(f)
        for k, v in doc.items():
            if k.startswith("gw_net.base."):
                self.assertIsNone(re.search("[А-Яа-яЁё]", v), k)


class OldDataTest(NetCase):
    def test_old_state_with_an_odd_base_reads_and_moves(self):
        """«Старые данные → новая»: a base 3.3.7.14 let in (198.18.0.0/16 — ``is_private``) still reads; the new
        version moves away from it."""
        with state.locked():
            st = state.load()
            ops._net_mutate(st, addr.base(st), addr.parse_base("198.18.0.0/16"))
            state.save(st)
        self.assertEqual(ops.net_view(self.w.runner)["base"], "198.18.0.0/16")
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        self.assertEqual(res["base"], "10.78.0.0/16")
        self.assertTrue(self.dev(self.did)["ips"]["wg"].startswith("10.78.1."))

    def test_old_versions_read_the_new_state(self):
        """«Прошлая версия читает новые»: 3.3.7.14 (fc5ef683) and 3.3.7.13 (bc541e29) read the state after the change
        (the base, the DNS, the addresses and subnets) as their own; settings-last.json they never read."""
        ops.net_base(self.w.runner, "172.20.0.0/16", yes=True)
        raw = util.read_text(paths.state())
        code = r"""
import json, sys, os
from kaskad import paths, state
from kaskad.gw import addr, model
os.makedirs(os.path.dirname(paths.state()), exist_ok=True)
open(paths.state(), "w").write(sys.stdin.read())
st = state.load()
d = model.gw(st)["devices"][-1]
print(json.dumps({"base": str(addr.base(st)), "dns": addr.dns_ip(st), "wg": addr.subnet("wg", st),
                  "ip": d["ips"].get("wg"), "ok": addr.valid_client_ip(st, d["ips"].get("wg"), "wg")}))
"""
        for rev in ("fc5ef683", "bc541e29"):
            with self.subTest(rev=rev):
                d = self.old_tree(rev)
                root = tempfile.mkdtemp(prefix="kaskad-oldroot-")
                self.addCleanup(subprocess.run, ["rm", "-rf", root])
                env = dict(os.environ, PYTHONPATH=os.path.join(d, "lib"), KASKAD_ROOT=root, LANG="C.UTF-8")
                r = subprocess.run([sys.executable, "-c", code], input=raw.encode(), capture_output=True, env=env,
                                   timeout=120)
                self.assertEqual(r.returncode, 0, r.stderr[-1500:])
                got = json.loads(r.stdout.decode())
                self.assertEqual((got["base"], got["dns"], got["wg"]), ("172.20.0.0/16", "172.20.0.1", "172.20.1.0/24"))
                self.assertTrue(got["ok"], got)

    def old_tree(self, rev):
        try:
            ok = subprocess.run(["git", "-C", REPO, "cat-file", "-e", rev + "^{commit}"], capture_output=True,
                                timeout=30)
        except (OSError, subprocess.SubprocessError):
            raise unittest.SkipTest("no git")
        if ok.returncode != 0:
            raise unittest.SkipTest("no commit %s" % rev)
        d = tempfile.mkdtemp(prefix="kaskad-old-")
        self.addCleanup(subprocess.run, ["rm", "-rf", d])
        arc = subprocess.run(["git", "-C", REPO, "archive", rev, "lib"], capture_output=True, timeout=120)
        subprocess.run(["tar", "-x", "-C", d], input=arc.stdout, check=True, timeout=120)
        return d


class FixG2Test(NetCase):
    """REV-3315-g2: Н-1 (the undo by hand under a live wait of a protocol), Н-2 (a failure after the write), Н-4 (SSTP
    of MikroTik once in «Что изменится»), Н-5 (the push says the old network is taken), Н-6 (the undo by hand — RFC 1918
    only; the timer brings back what was)."""

    def _fail_after_write(self, new):
        """``_net_marks`` raises on the state after the write (the plan reads the state before it) — a failure after
        a made ``_change``, as in the probe P2 of the reviewer."""
        real = ops._net_marks

        def marks(st):
            if str(addr.base(st)) == new:
                raise TypeError("boom after the write")
            return real(st)
        return mock.patch.object(ops, "_net_marks", marks)

    def test_n1_undo_by_hand_while_a_protocol_waits_is_409(self):
        """Н-1: «Вернуть прежнюю сеть» moves the addresses of WireGuard — while the wait of WireGuard is live it is
        refused as ``pending.guard`` refuses a change of the network (409 ``pending_busy``): the network, its record
        and the wait of WireGuard untouched. Fails before the fix: the network went back under the wait of wg."""
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        self.assertIsNone(res["pending"])
        ops._last_write("wg", {"id": "a-0000000a", "at": 1, "old": {"port": 51820}, "new": {"port": 51999}})
        pending.arm(self.w.runner, "wg", "a-0000000a")
        with self.assertRaises(KaskadError) as cm:
            ops.net_base_undo(self.w.runner)
        self.assertEqual((cm.exception.key, cm.exception.status), ("pending_busy", 409))
        self.assertEqual(self.base(), "10.78.0.0/16")
        self.assertEqual(self.last()["base"]["id"], res["id"])
        self.assertEqual(pending.read()["scope"], "wg")
        self.assertEqual(self.stops, [])
        doc = self.err(409, "POST", "/api/gw/net/undo", {})            # the route of the panel: the same 409
        self.assertEqual(doc["code"], "pending_busy")
        self.assertEqual(self.base(), "10.78.0.0/16")
        pending.disarm(self.w.runner)                                   # the wait gone — the undo goes
        self.assertEqual(ops.net_base_undo(self.w.runner)["base"], "10.77.0.0/16")

    def test_n2_a_failure_after_the_write_keeps_the_wait_and_the_record(self):
        """Н-2: the network is written, a step after it fails — the answer is ``ok`` with the words of it, the wait
        of F7 and the record «Вернуть прежнюю сеть» stay (the timer reverts; by hand — back). Fails before the fix:
        500, the new network without the wait and without the record."""
        with self._fail_after_write("10.78.0.0/16"):
            res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True, who=self.admin())
        self.assertTrue(res["ok"])
        self.assertEqual(self.base(), "10.78.0.0/16")
        self.assertIn("TypeError", res["text"])
        self.assertIn("Вернуть прежнюю сеть", res["text"])
        self.assertNotIn("boom", json.dumps(res, ensure_ascii=False, default=str))   # the words of it — the journal
        self.assertEqual(pending.read()["id"], res["id"])
        self.assertEqual(res["pending"]["token"], pending.read()["token"])
        self.assertEqual(self.last()["base"]["id"], res["id"])
        self.assertIn("boom after the write", util.read_text(paths.log()))
        done = pending.expire(self.w.runner, res["pending"]["token"], now=res["pending"]["deadline"] + 1)
        self.assertEqual(done["done"], "reverted")
        self.assertEqual(self.base(), "10.77.0.0/16")
        self.assertEqual(self.dev(self.did)["ips"]["wg"], self.ip)
        # no wait (no administrator hit): the record stays — «Вернуть прежнюю сеть» by hand
        with self._fail_after_write("10.79.0.0/16"):
            res = ops.net_base(self.w.runner, "10.79.0.0/16", yes=True)
        self.assertIsNone(res["pending"])
        self.assertEqual(self.last()["base"]["id"], res["id"])
        self.assertEqual(ops.net_base_undo(self.w.runner)["base"], "10.77.0.0/16")

    def test_n4_sstp_of_mikrotik_only_is_not_among_who_reconnects(self):
        """Н-4: SSTP with MikroTik devices only — in the line «не подключится до новой выдачи» and NOT in «Остальные
        переподключатся сами»; with a Windows device of SSTP too — in both (Windows reconnects)."""
        from kaskad.gw import registry
        r = self.w.runner
        ops.device_add(r, self.uid, "hAP", kind="router", platform="mikrotik", access=["sstp"])
        sstp = registry.title("sstp")
        lines = ops.net_base(r, "10.78.0.0/16", dry=True)["plan"]["lines"]
        self.assertTrue(any(sstp in ln and "MikroTik" in ln for ln in lines), lines)
        self.assertFalse(any(sstp in ln and "переподключатся сами" in ln for ln in lines), lines)
        ops.device_add(r, self.uid, "ПК", kind="laptop", platform="windows", access=["sstp"])
        lines = ops.net_base(r, "10.78.0.0/16", dry=True)["plan"]["lines"]
        self.assertTrue(any(sstp in ln and "переподключатся сами" in ln for ln in lines), lines)

    def test_n5_the_old_network_taken_the_push_says_why(self):
        """Н-5: the timer could not bring the network back — the old one is taken by something of the node now: the
        event says «net_busy» and the push says why (RU and EN). Fails before the fix: «error», no reason."""
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True, who=self.admin())
        self.w.ip.routes["main"].append("10.77.0.0/16 dev docker0 proto kernel scope link src 10.77.0.1")
        done = pending.expire(self.w.runner, res["pending"]["token"], now=res["pending"]["deadline"] + 1)
        self.assertEqual(done["done"], "failed")
        self.assertEqual(done["event"]["why"], "net_busy")
        ev = self.events()[-1]
        self.assertEqual((ev["kind"], ev["scope"], ev["why"]), ("settings_revert_failed", "base", "net_busy"))
        got, _files = pending.peek()
        self.assertEqual(got[-1]["why"], "net_busy")
        self.assertIn("прежнюю сеть теперь занимает", pending.event_text(ev["kind"], ev, "ru"))
        self.assertIn("holds the previous network", pending.event_text(ev["kind"], ev, "en"))
        self.assertEqual(self.base(), "10.78.0.0/16")
        # a protocol's refusal of the same shape is not «net_busy» (only the network of the gateway)
        self.assertEqual(pending._final_why(ConflictError("x", data={"why": "busy"}), "wg"), "error")

    def _odd_base(self, base="198.18.0.0/16"):
        with state.locked():
            st = state.load()
            ops._net_mutate(st, addr.base(st), addr.parse_base(base))
            state.save(st)
        return self.dev(self.did)["ips"]["wg"]

    def test_n6_undo_by_hand_to_a_network_outside_rfc1918_refused(self):
        """Н-6: the network before was outside RFC 1918 (3.3.7.14 let any private /16 in): «Вернуть прежнюю сеть» by
        hand is refused (a new write of the network is RFC 1918 only), the network stays, the record goes (it can
        never be done by hand). Fails before the fix: 198.18.0.0/16 written back."""
        self._odd_base()
        ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        with self.assertRaises(KaskadError) as cm:
            ops.net_base_undo(self.w.runner)
        self.assertEqual(cm.exception.code, USAGE)
        self.assertEqual(cm.exception.data["why"], "rfc1918")
        self.assertIn("198.18.0.0/16", str(cm.exception))
        self.assertEqual(self.base(), "10.78.0.0/16")
        self.assertNotIn("base", self.last())
        self.assertIsNone(ops.net_view(self.w.runner)["last"])

    def test_n6_the_timer_brings_back_the_network_that_was(self):
        """Н-6, the other side: the timer of F7 is the way back of an administrator cut off — it brings back the
        network that was, even outside RFC 1918 (by hand under that very wait — the same, REV-3315-g3 Г3-1: FixG3Test).
        """
        ip = self._odd_base()
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True, who={"via": "panel", "ip": ip})
        self.assertIsNotNone(res["pending"])
        self.assertEqual(self.last()["base"]["id"], res["id"])
        self.assertEqual(pending.read()["token"], res["pending"]["token"])
        done = pending.expire(self.w.runner, res["pending"]["token"], now=res["pending"]["deadline"] + 1)
        self.assertEqual(done["done"], "reverted")
        self.assertEqual(self.base(), "198.18.0.0/16")
        self.assertEqual(self.dev(self.did)["ips"]["wg"], ip)

    def test_hit_text_alone_in_the_plan(self):
        """CONTRACT-pending-3315 добавка 4 (просьба V1): the plan of F6 carries ``hit_text`` — the words of the hit
        (also in ``warnings`` as before); no hit — ""."""
        hit = ops.net_base(self.w.runner, "10.78.0.0/16", dry=True, who=self.admin())["plan"]
        self.assertEqual(hit["hit_text"], pending.hit_text("base", "base"))
        self.assertTrue(hit["hit_text"])
        self.assertIn(hit["hit_text"], hit["warnings"])
        plain = ops.net_base(self.w.runner, "10.78.0.0/16", dry=True)["plan"]
        self.assertEqual(plain["hit_text"], "")
        self.assertEqual(plain["admin_hit"], None)

    def test_n8_the_words(self):
        """Н-8: no record — the words of the button of the network; the help of ``--base`` says the networks and the
        panel; one form of the refusal of a network outside RFC 1918 (read or new)."""
        with mock.patch.object(ops, "_last_can_write", lambda: False):
            res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        self.assertIn(tr("gw_net.base.last_failed", err=ops.settings_last_path()), res["warnings"])
        self.assertTrue(any("«Вернуть прежнюю сеть»" in w for w in res["warnings"]), res["warnings"])
        self.assertFalse(any("«Вернуть прежние»" in w for w in res["warnings"]), res["warnings"])
        for lang in ("ru", "en"):
            self.assertIn("192.168.0.0/16", tr("gw_cli.help.base", lang))
            self.assertIn("172.16.0.0/12", tr("gw_cli.help.base", lang))
        for bad in ("8.8.0.0/16", "127.1.0.0/16"):
            with self.subTest(base=bad):
                with self.assertRaises(KaskadError) as cm:
                    addr.new_base(bad)
                self.assertIn("/16 внутри 10.0.0.0/8, 172.16.0.0/12 или 192.168.0.0/16", str(cm.exception))


class FixG3Test(NetCase):
    """REV-3315-g3: Г3-1 (no button «Вернуть прежнюю сеть» that always refuses; by hand under the wait of that very
    change — what its timer does), Г3-2 (Ctrl+C right after the write cuts nothing), Г3-4 (no record — no button named;
    the DNS of the new network), CONTRACT-pending-3315 добавка 6: ``after_failed`` (the flag of the panel)."""

    _fail_after_write = FixG2Test._fail_after_write
    _odd_base = FixG2Test._odd_base

    def test_g31_no_button_that_always_refuses(self):
        """The network before was 198.18/16 (3.3.7.14), the change without a wait: the button is not offered — by hand
        it would always be refused. Fails before the fix: ``last`` filled, the click refused."""
        self._odd_base()
        ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        self.assertIsNone(ops.net_view(self.w.runner)["last"])
        ops.net_base(self.w.runner, "10.79.0.0/16", yes=True)               # an old network inside RFC 1918: offered
        self.assertEqual(ops.net_view(self.w.runner)["last"]["old"], "10.78.0.0/16")

    def test_g31_by_hand_under_its_own_wait_as_the_timer(self):
        """The same, the administrator in the network — the wait of «base» is live: the button is there and does what
        the timer would in 2 minutes (198.18/16 back, no wait, no record). Fails before the fix: «вернуть нельзя»."""
        ip = self._odd_base()
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True, who={"via": "panel", "ip": ip})
        self.assertIsNotNone(res["pending"])
        self.assertEqual(ops.net_view(self.w.runner)["last"]["old"], "198.18.0.0/16")
        got = ops.net_base_undo(self.w.runner)
        self.assertEqual((got["base"], self.base()), ("198.18.0.0/16", "198.18.0.0/16"))
        self.assertIsNone(pending.read())
        self.assertNotIn("base", self.last())
        self.assertEqual(self.dev(self.did)["ips"]["wg"], ip)

    def test_g31_a_wait_of_another_change_still_refuses(self):
        """Only the wait of that very change (its id): a wait of «base» with another id — refused as before, the
        record kept for the timer, the button not offered."""
        self._odd_base()
        res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        pending.arm(self.w.runner, "base", "a-0000000b")
        self.assertIsNone(ops.net_view(self.w.runner)["last"])
        with self.assertRaises(KaskadError) as cm:
            ops.net_base_undo(self.w.runner)
        self.assertEqual(cm.exception.data["why"], "rfc1918")
        self.assertEqual((self.base(), self.last()["base"]["id"]), ("10.78.0.0/16", res["id"]))
        self.assertEqual(pending.read()["id"], "a-0000000b")

    def test_g32_ctrl_c_right_after_the_write(self):
        """SIGINT right after ``_change`` of F6 (in ``_net_marks``): the change runs to its end — ``ok``, the wait and
        the record stay. Fails before the fix: KeyboardInterrupt, the wait and the record dropped."""
        real = ops._net_marks

        def marks(st):
            if str(addr.base(st)) == "10.78.0.0/16":
                os.kill(os.getpid(), signal.SIGINT)                         # Ctrl+C in the window after the write
            return real(st)
        try:
            with mock.patch.object(ops, "_net_marks", marks):
                res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True, who=self.admin())
        except KeyboardInterrupt:
            self.fail("Ctrl+C after the write cut the change of the network")
        self.assertTrue(res["ok"])
        self.assertNotIn("after_failed", res)
        self.assertEqual(self.base(), "10.78.0.0/16")
        self.assertEqual(pending.read()["id"], res["id"])
        self.assertEqual(self.last()["base"]["id"], res["id"])

    def test_g34_after_failed_the_flag_the_button_and_the_dns(self):
        """A step after the write failed: ``after_failed: true`` (the API too), the DNS of the new network; no record
        — the words name no button (the warnings say «не будет»). Fails before the fix: no flag, «нажмите «Вернуть
        прежнюю сеть»» without a record, ``dns`` None."""
        with self._fail_after_write("10.78.0.0/16"):
            res = ops.net_base(self.w.runner, "10.78.0.0/16", yes=True)
        self.assertIs(res["after_failed"], True)
        self.assertEqual(res["dns"], "10.78.0.1")
        self.assertIn("«Вернуть прежнюю сеть»", res["text"])                # the record is there: the button is
        with self._fail_after_write("10.79.0.0/16"), mock.patch.object(ops, "_last_can_write", lambda: False):
            res = ops.net_base(self.w.runner, "10.79.0.0/16", yes=True)
        self.assertIs(res["after_failed"], True)
        self.assertEqual(res["dns"], "10.79.0.1")
        self.assertIn("TypeError", res["text"])
        self.assertNotIn("Вернуть прежнюю сеть", res["text"])
        self.assertIn(tr("gw_net.base.last_failed", err=ops.settings_last_path()), res["warnings"])
        self.assertIsNone(ops.net_view(self.w.runner)["last"])
        ok = ops.net_base(self.w.runner, "10.80.0.0/16", yes=True)
        self.assertNotIn("after_failed", ok)
        ver = self.ok("GET", "/api/gw/net")["ver"]
        with self._fail_after_write("10.81.0.0/16"):
            doc = self.ok("PUT", "/api/gw/net/base", {"base": "10.81.0.0/16", "ver": ver, "yes": True})
        self.assertIs(doc["after_failed"], True)
        self.assertEqual(self.base(), "10.81.0.0/16")
        for lang in ("ru", "en"):
            self.assertNotIn("«", tr("gw_net.base.after_failed_no_undo", lang, old="a", new="b", err="E"))
            self.assertNotIn("“", tr("gw_net.base.after_failed_no_undo", lang, old="a", new="b", err="E"))


if __name__ == "__main__":
    unittest.main()

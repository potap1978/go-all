"""T-M6-qs330b №8: after ``systemctl reboot`` a client of the inbound AmneziaWG 3.1 (kernel module) lost it twice.

The offline part: who recreates an inbound's interface is now told in one line (gw/ifwatch.py, the start records of the
gate drop-in, tunnel.note_restart with the command), the collector waits for the module's boot decision, and right
after a reboot the devices moved to another exit are told (collect._tell_moves). The heal of the legs is in
tools/k3_install_test.sh (section 16)."""

import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

from kaskad import util
from kaskad.gw import awgkmod, collect, exits, gate, ifwatch, registry
from kaskad.gw.inbounds import tunnel
from kaskad.sysrun import FakeRunner
from tests.helpers import RootCase

T0 = 1791580000
UNIT = "kaskad-gw-awg3.service"
IFACE = "kgw-awg3"


def gw_state(*kinds):
    return {"gateway": {"enabled": True, "inbounds": dict((k, {"enabled": True}) for k in kinds)}}


class Base(RootCase):
    def setUp(self):
        super().setUp()
        registry.reset()
        self.logged = []
        self.st = gw_state("awg3", "wg", "l2tp")

    def tearDown(self):
        registry.reset()
        super().tearDown()

    def uptime(self, s):
        self.write("/proc/uptime", "%d.42 100.00\n" % s)

    def iface(self, idx, name=IFACE):
        if idx is None:
            shutil.rmtree(self.path("/sys/class/net/%s" % name), ignore_errors=True)
        else:
            self.write("/sys/class/net/%s/ifindex" % name, "%d\n" % idx)

    def start(self, up, unit=UNIT):
        p = gate.start_record(unit)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a") as f:
            f.write("%d\n" % up)

    def kernel_unit(self):
        self.write("/etc/systemd/system/" + UNIT, "[Service]\nExecStart=/usr/sbin/ip link add dev %s type amneziawg\n"
                   % IFACE, 0o644)

    def observe(self, now, stats=None):
        return ifwatch.observe(self.st, None, stats or {}, log=self.logged.append, now=now)


class IfwatchTest(Base):
    def test_one_start_after_the_boot_is_quiet(self):
        self.uptime(12)
        self.start(9)
        self.iface(7)
        self.assertEqual(self.observe(T0), [])
        self.uptime(22)
        self.assertEqual(self.observe(T0 + 10), [])                       # nothing changed
        self.assertTrue(os.path.exists(ifwatch.state_path()))
        self.assertEqual(os.stat(ifwatch.state_path()).st_mode & 0o777, 0o600)

    def test_a_second_start_by_systemd_is_told_with_who(self):
        self.kernel_unit()
        self.uptime(12)
        self.start(9)
        self.iface(7)
        self.observe(T0)
        self.uptime(42)
        self.start(31)                                                    # a restart: stop deleted it, start made it
        self.iface(12)
        lines = self.observe(T0 + 30)
        self.assertEqual(len(lines), 1)
        ln = lines[0]
        self.assertIn("GW вход awg3 (kgw-awg3): интерфейс пересоздан через 42 с после загрузки (ifindex 7 → 12)", ln)
        self.assertIn("клиенты AmneziaWG переподключатся сами за 15–25 с", ln)
        self.assertIn("служба kaskad-gw-awg3.service запущена заново (в 31 с), Kaskad её не перезапускал", ln)
        self.assertIn("journalctl -b -u kaskad-gw-awg3.service", ln)
        self.assertIn("движок: модуль ядра", ln)
        self.assertEqual(self.logged, lines)
        self.assertEqual(self.observe(T0 + 40), [])                       # told once

    def test_a_restart_by_kaskad_names_the_reason_and_the_command(self):
        self.uptime(12)
        self.start(9)
        self.iface(7)
        self.observe(T0)
        with mock.patch.object(tunnel, "now", lambda: T0 + 5):
            tunnel.note_restart("awg3", "engine", by="kaskadctl gw accel boot (kaskad-gw-awgkmod.service)")
        self.start(15)
        self.iface(9)
        ln = self.observe(T0 + 10)[0]
        self.assertIn("кто: Kaskad — причина «смена движка (модуль ядра / программа)», кто: kaskadctl gw accel boot "
                      "(kaskad-gw-awgkmod.service)", ln)
        self.assertNotIn("Kaskad её не перезапускал", ln)

    def test_an_old_restart_of_kaskad_does_not_explain_a_new_one(self):
        with mock.patch.object(tunnel, "now", lambda: T0 - 600):
            tunnel.note_restart("awg3", "mtu", by="kaskadctl gw apply")
        self.uptime(12)
        self.start(9)
        self.iface(7)
        self.observe(T0)
        self.start(25)
        self.iface(8)
        self.assertIn("Kaskad её не перезапускал", self.observe(T0 + 10)[0])

    def test_recreated_without_a_start_of_the_unit(self):
        self.uptime(12)
        self.start(9)
        self.iface(7)
        self.observe(T0)
        self.iface(8)
        ln = self.observe(T0 + 10)[0]
        self.assertIn("служба не перезапускалась — интерфейс пересоздан в обход неё", ln)
        self.assertIn("выгрузка модуля amneziawg", ln)

    def test_gone_is_told_once(self):
        self.uptime(12)
        self.start(9)
        self.iface(7)
        self.observe(T0)
        self.iface(None)
        lines = self.observe(T0 + 10)
        self.assertEqual(len(lines), 1)
        self.assertIn("интерфейса нет", lines[0])
        self.assertEqual(self.observe(T0 + 20), [])

    def test_sessions_reset_on_the_same_interface(self):
        self.uptime(12)
        self.start(9)
        self.iface(7)
        s1 = {"awg3": {"sessions": [{"device": "d-1", "rx": 5000, "tx": 7000}, {"device": "d-2", "rx": 10, "tx": 10}]}}
        self.observe(T0, s1)
        s2 = {"awg3": {"sessions": [{"device": "d-1", "rx": 100, "tx": 200}, {"device": "d-2", "rx": 50, "tx": 50}]}}
        ln = self.observe(T0 + 10, s2)[0]
        self.assertIn("сессии клиентов сброшены без пересоздания интерфейса", ln)
        self.assertIn("(1 из 2: счётчики обнулились)", ln)
        self.assertEqual(self.observe(T0 + 20, s2), [])                   # counters only grow: quiet

    def test_the_first_round_tells_a_restart_made_before_the_collector(self):
        self.uptime(40)
        self.start(9)
        self.start(31)
        self.iface(12)
        self.start(10, unit="kaskad-gw-wg.service")                       # WireGuard started once: nothing
        self.iface(3, "kgw-wg")
        lines = self.observe(T0)
        self.assertEqual(len(lines), 1)
        self.assertIn("GW вход awg3 (kgw-awg3): после загрузки служба kaskad-gw-awg3.service запускалась 2 раза "
                      "(в 9 с, в 31 с)", lines[0])
        self.assertIn("клиентам AmneziaWG до 15–25 с", lines[0])

    def test_wireguard_is_watched_too_and_other_kinds_are_not(self):
        self.uptime(12)
        self.start(9, unit="kaskad-gw-wg.service")
        self.iface(3, "kgw-wg")
        self.observe(T0)
        self.start(20, unit="kaskad-gw-wg.service")
        self.iface(4, "kgw-wg")
        ln = self.observe(T0 + 10)[0]
        self.assertIn("GW вход wg (kgw-wg)", ln)
        self.assertIn("клиенты WireGuard переподключатся", ln)
        self.assertEqual(sorted(ifwatch._load()["kinds"]), ["awg3", "wg"])   # l2tp is not of START_KINDS

    def test_the_collector_survives_a_broken_watch(self):
        with mock.patch.object(ifwatch, "observe", side_effect=ValueError("x")), \
                mock.patch.object(collect, "_now", lambda: T0), \
                mock.patch.object(util, "log_action", self.logged.append):
            collect._live_errors.pop("ifwatch", None)
            collect._note_error("ifwatch", ValueError("x"))
            collect._note_error("ifwatch", ValueError("x"))              # hourly at most
        self.assertEqual(self.logged, ["GW collect: ifwatch — ValueError: x"])


class WhoTest(Base):
    def test_who_names_the_command_and_the_unit(self):
        self.write("/proc/self/cgroup", "0::/system.slice/kaskad-net-watch.service\n")
        with mock.patch.object(sys, "argv", ["/opt/kaskad/bin/kaskadctl", "net", "check", "--repair", "--quiet"]):
            self.assertEqual(tunnel.who(), "kaskadctl net check (kaskad-net-watch.service)")
        self.write("/proc/self/cgroup", "0::/user.slice/user-0.slice/session-3.scope\n")
        with mock.patch.object(sys, "argv", ["/opt/kaskad/bin/kaskadctl", "gw", "apply"]):
            self.assertEqual(tunnel.who(), "kaskadctl gw apply (session-3.scope)")

    def test_note_restart_records_and_logs_who(self):
        self.write("/proc/self/cgroup", "0::/system.slice/kaskad-gw-collect.service\n")
        with mock.patch.object(sys, "argv", ["/opt/kaskad/bin/kaskadctl", "gw", "collect", "--interval", "10"]), \
                mock.patch.object(util, "log_action", self.logged.append):
            tunnel.note_restart("awg3", "unit")
        rec = tunnel.read_json(tunnel.restarts_path(), {})["awg3"]
        self.assertEqual(rec["by"], "kaskadctl gw collect (kaskad-gw-collect.service)")
        self.assertIn("причина: новая версия службы входа; кто: kaskadctl gw collect (kaskad-gw-collect.service)",
                      self.logged[0])


class GateStartRecordTest(Base):
    def runner(self):
        r = FakeRunner(binaries=("nft",))
        r.on(["systemctl"], (0, "", ""))
        return r

    def test_the_drop_in_of_wireguard_style_units_records_each_start(self):
        for u in (UNIT, "kaskad-gw-wg.service", "kaskad-gw-accel.service"):
            self.write("/etc/systemd/system/" + u, "[Service]\nExecStart=/bin/true\n", 0o644)
        st = gw_state("awg3", "wg", "l2tp")
        changed = gate.sync(self.runner(), st)
        self.assertIn(UNIT, changed)
        awg = self.read("/etc/systemd/system/%s.d/kaskad-gate.conf" % UNIT)
        self.assertIn("ExecStartPre=+/bin/sh -c 'exec /usr/sbin/nft list chain ip kaskad gw_pre >/dev/null'\n", awg)
        self.assertIn(gate.start_line(UNIT, IFACE) + "\n", awg)
        self.assertIn("Kaskad: inbound kgw-wg start #", self.read("/etc/systemd/system/kaskad-gw-wg.service.d/kaskad-gate.conf"))
        l2tp = self.read("/etc/systemd/system/kaskad-gw-accel.service.d/kaskad-gate.conf")
        self.assertNotIn("start.", l2tp)                                  # PPP has no single interface
        self.assertEqual(gate.sync(self.runner(), st), [])                # the second time nothing changes

    def test_the_line_is_valid_for_systemd_and_records_for_real(self):
        line = gate.start_line(UNIT, IFACE)
        self.assertTrue(line.startswith("ExecStartPre=-+/bin/sh -c '"))
        self.assertTrue(line.endswith("'"))
        self.assertNotIn("%", line)                                       # no systemd specifier by mistake
        body = line[len("ExecStartPre=-+/bin/sh -c '"):-1]
        self.assertNotIn("'", body)
        self.assertNotRegex(body.replace("$$", ""), r"\$")                # every $ of the shell is $$ (systemd)
        # what systemd hands to /bin/sh: $$ -> $; the node's /proc and /sys -> the test root
        script = body.replace("$$", "$").replace("/proc/uptime", self.path("/proc/uptime"))
        self.uptime(31)
        out = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, "Kaskad: inbound kgw-awg3 start #1 since boot, 31 s after it\n")   # neutral English
        self.uptime(44)
        out = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True)
        self.assertEqual(out.stdout, "Kaskad: inbound kgw-awg3 start #2 since boot, 44 s after it\n")
        self.assertEqual(ifwatch.starts(UNIT), [31, 44])
        self.assertEqual(os.stat(self.path("/run/kaskad/gw")).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(self.path("/run/kaskad")).st_mode & 0o777, 0o711)   # passable for nginx


class BootOrderTest(unittest.TestCase):
    def test_the_collector_waits_for_the_module_decision(self):
        self.assertEqual(collect.AWGKMOD_UNIT, awgkmod.BOOT_UNIT)
        after = [ln for ln in collect.unit_text().splitlines() if ln.startswith("After=")][0]
        self.assertEqual(after.split("=", 1)[1].split(), ["kaskad-net.service", "network-online.target",
                                                          "kaskad-gw-awgkmod.service"])

    def test_mark_names(self):
        lines = ["awg://a", "vless://b@c:1"]                  # T1: an xray exit by the protocol of its line
        self.assertEqual([collect.mark_name(m, lines) for m in (0x103, 0x200, 0x201, 0x2ff, 0x302, None)],
                         ["kawg3", "пул VLESS (xray)", "напрямую", "блок", "VLESS 2", "нет"])
        self.assertEqual(collect.mark_name(0x302), "выход 2")


class MovesAfterBootTest(Base):
    def test_moves_are_told_with_device_names(self):
        st = {"gateway": {"enabled": True, "devices": [{"id": "d-1", "name": "qs2-awg31", "ips": {"awg3": "10.77.4.2"}},
                                                       {"id": "d-2", "name": "wg", "ips": {"wg": "10.77.1.10"}}]}}
        before = {"10.77.4.2": 0x103, "10.77.1.10": 0x102}
        with mock.patch.object(collect.peek, "read", return_value={"x": 1}), \
                mock.patch.object(collect.net, "map_elems", return_value={"10.77.4.2": 0x105, "10.77.1.10": 0x102}):
            collect._tell_moves(st, None, before, self.logged.append)
        self.assertEqual(self.logged, ["GW после перезагрузки: устройства переведены на другие выходы — "
                                       "10.77.4.2 (qs2-awg31): kawg3 → kawg5"])
        self.assertEqual(exits.AWG_MARK_BASE, 256)


if __name__ == "__main__":
    unittest.main()


# ── the exits right after a reboot: legs still coming up are not «dead» ──────
from tests.fakes_gw import T0 as GW_T0                    # noqa: E402
from tests.test_gw_ops import CliGwCase                  # noqa: E402


class LegsAtBootTest(CliGwCase):
    """A device pinned to the AmneziaWG exit (fail policy «block»). At boot the legs come up under systemd while the
    snapshot still says «alive»: they were counted as failed probes («нет интерфейса») and the devices moved away and
    back (T-M6-qs330b №8). Now such a leg is UNKNOWN for at most exits.UNKNOWN_MAX s."""

    def setUp(self):
        super().setUp()
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")
        self.ok("gw", "route", "exit:" + self.aid)
        self.w.leg(2)

    def round(self, dt=5):
        res = collect.tick(self.w.runner)["exits"]
        self.now += dt
        return res

    def booted(self, seconds=30):
        self.write("/proc/uptime", "%.2f 100.00\n" % seconds)

    def test_a_leg_still_coming_up_keeps_its_devices_then_dies_if_it_never_comes(self):
        self.booted()
        self.w.kill_leg(2)
        self.w.script.fail_legs.add(2)                              # its unit has not brought it up yet
        start = self.now
        died = None
        for _ in range(30):
            r = self.round()
            # (the VLESS exit of this world never answered since it was added: told once as dead — T-M6-qs330d №4)
            if [c for c in r["changes"] if c["id"] == self.aid]:
                died = self.now - start
                break
            self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x102})   # stays: no move away and back
        self.assertIsNotNone(died)
        self.assertGreaterEqual(died, exits.UNKNOWN_MAX)               # dead, but only after its grace
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x2ff})       # fail-closed as before
        self.assertIn("ещё поднимается после перезагрузки", self.read("/etc/kaskad/gw/exits.json")
                      + self.log())

    def test_a_leg_that_comes_up_in_time_never_moves_anybody(self):
        self.booted()
        self.w.kill_leg(2)
        self.w.script.fail_legs.add(2)
        for _ in range(4):
            self.assertEqual(self.round()["changes"], [])
        self.w.script.fail_legs.discard(2)
        self.w.leg(2)
        for _ in range(3):
            self.assertEqual(self.round()["changes"], [])
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x102})
        self.assertNotIn("не работает", self.log())

    def test_without_a_reboot_a_missing_leg_dies_as_before(self):
        self.w.kill_leg(2)
        self.w.script.fail_legs.add(2)
        changes = [self.round()["changes"] for _ in range(10)]     # 3.3.5: half of the checks (one per 10 s)
        mine = [c for c in changes if c]
        self.assertEqual(changes[:2], [[], []])
        self.assertEqual(mine[0], [{"id": self.aid, "was": True, "alive": False}])
        self.assertLessEqual(changes.index(mine[0]), 7)           # a short history: 4 checks, one per 10 s — by 35 s

    def test_a_leg_just_up_and_silent_gets_its_grace(self):
        self.round()                                                 # (not only at boot: a heal restarts a leg too)
        self.w.leg(2, probe=False)                                   # up, its first handshake not done yet
        self.w.sysd.units["kaskad-leg@kawg2.service"] = {"active": True, "since_mono": self.now - GW_T0 + 1000 - 5}
        for _ in range(4):                                           # 5 … 20 s old: not believed
            self.assertEqual(self.round()["changes"], [])
        changes = [self.round()["changes"] for _ in range(10)]      # 25 s and older: failures count again
        self.assertIn([{"id": self.aid, "was": True, "alive": False}], changes)
        self.assertLessEqual(changes.index([{"id": self.aid, "was": True, "alive": False}]), 7)   # by 35 s

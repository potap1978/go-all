"""AmneziaWG inbounds on the kernel module (WP-K, gw/awgkmod.py): the unit per mode and line, the decision
(acceleration on, module loaded, config safe, no failure on this kernel), switching = recreating the interface,
the fallback of a module unit that does not start, «ядро/программа» in status."""

import json
import os
from unittest import mock

from kaskad import paths, util
from kaskad.vendor import awgkeys
from tests.gw.test_awg import INBOUNDS, AwgCase
from tests.gw.tunnel_case import device, slurp


def stat_mode(path):
    return os.stat(path).st_mode & 0o777

KERNEL = "6.8.0-50-generic"


class KmodCase(AwgCase):
    def setUp(self):
        super().setUp()
        self.seed()
        self.core_d = self.core("/etc/kaskad/egress/awg")
        self.kmod = self.gw.awg.awgkmod
        self.module_ok = True
        self.modprobes = []
        r = self.runner
        # before the catch-all of the fake server (FakeRunner: the first matching prefix wins)
        r._handlers.insert(0, (["uname", "-r"], lambda a, i: (0, KERNEL + "\n", "")))
        r._handlers.insert(0, (["modprobe", self.kmod.NAME], self._modprobe))

    def _modprobe(self, argv, input):
        self.modprobes.append(argv)
        if not self.module_ok:
            return 1, "", "modprobe: FATAL: Module amneziawg not found in directory /lib/modules/%s\n" % KERNEL
        os.makedirs(self.kmod.module_dir(), exist_ok=True)
        return 0, "", ""

    def accel(self, on=True, loaded=True, set31=False, **extra):
        """Acceleration on/off; ``loaded`` — the module in memory; ``set31`` — it carries the set of 3.1 (its mark
        in /sys/module/amneziawg/version)."""
        doc = dict({"enabled": on}, **extra)
        self.write("/etc/kaskad/gw/awg-kmod.json", json.dumps(doc))
        if loaded:
            os.makedirs(self.kmod.module_dir(), exist_ok=True)
            self.write("/sys/module/amneziawg/version",
                       "3.1.20260812%s\n" % (("+" + self.kmod.SET_ID) if set31 else ""))

    def unit(self, kind):
        return slurp(os.path.join(paths.systemd_dir(), "kaskad-gw-%s.service" % kind))

    def install_all(self):
        for kind in INBOUNDS:
            self.adapter(kind).install(self.runner)


class UnitTest(KmodCase):
    def test_off_is_the_old_unit_and_asks_nothing(self):
        a = self.adapter("awg2")
        self.golden("kaskad-gw-awg2.service", a.unit_texts(self.runner)["kaskad-gw-awg2.service"])
        self.assertEqual(self.modprobes, [])
        self.assertEqual([c for c, _ in self.runner.calls if c[:2] == ["uname", "-r"]], [])
        self.accel(on=False)
        self.golden("kaskad-gw-awg2.service", a.unit_texts(self.runner)["kaskad-gw-awg2.service"])
        self.assertEqual(self.modprobes, [])

    def test_kernel_unit_per_line(self):
        self.accel()
        a2 = self.adapter("awg2")
        text = a2.unit_texts(self.runner)["kaskad-gw-awg2.service"]
        self.golden("kaskad-gw-awg2-kernel.service", text)
        self.assertIn("Type=oneshot\nRemainAfterExit=yes", text)
        self.assertNotIn("amneziawg-go", text)
        self.assertIn("ExecStopPost=-/usr/sbin/ip link delete dev kgw-awg2", text)
        self.assertEqual(a2.core_mode(self.runner), "kernel")
        self.assertEqual(self.adapter("awg1").core_mode(self.runner), "kernel")                 # 1.5 (and 1.0)
        self.assertEqual(self.adapter("awg3").core_mode(self.runner), "userspace")   # 3.1: a module without the set
        self.assertIn(" -f kgw-awg3", self.adapter("awg3").unit_texts(self.runner)["kaskad-gw-awg3.service"])

    def test_the_set_of_31_moves_awg3_into_the_kernel(self):
        self.accel(set31=True)
        a3 = self.adapter("awg3")
        a3.prepare(self.st, self.sec)
        files = a3.render(self.st, self.sec)
        for path, (text, mode) in files.items():
            util.atomic_write(path, text, mode)
        self.assertEqual(self.kmod.conf_problems(files[a3.conf_path()][0]), [])
        self.assertEqual(a3.core_mode(self.runner), "kernel")
        text = a3.unit_texts(self.runner)["kaskad-gw-awg3.service"]
        self.assertIn("ExecStart=/usr/sbin/ip link add dev kgw-awg3 type amneziawg", text)
        self.assertIn("syncconf kgw-awg3 %s" % a3.sync_path(), text)
        self.accel(set31=False)                                    # the same module without its mark: the program
        self.assertEqual(a3.core_mode(self.runner), "userspace")
        self.assertEqual(self.adapter("awg2").core_mode(self.runner), "kernel")
        self.accel(set31=True, set_failed={"kernel": KERNEL, "reason": "x"})   # the mark in memory decides
        self.assertEqual(a3.core_mode(self.runner), "kernel")

    def test_module_missing_keeps_the_program(self):
        self.accel(loaded=False)
        self.module_ok = False
        self.assertEqual(self.adapter("awg2").core_mode(self.runner), "userspace")
        self.assertEqual(self.modprobes, [["modprobe", "amneziawg"]])
        self.module_ok = True                                       # rebuilt for this kernel: modprobe loads it
        self.assertEqual(self.adapter("awg2").core_mode(self.runner), "kernel")

    def test_a_config_unsafe_for_the_module_keeps_the_program(self):
        self.accel()
        a = self.adapter("awg2")
        a.prepare(self.st, self.sec)
        files = a.render(self.st, self.sec)
        conf = files[a.conf_path()][0]
        util.atomic_write(a.conf_path(), conf.replace("Jmin = 10", "Jmin = 90"), 0o600)    # Jmin > Jmax: panic (#254)
        self.assertEqual(a.core_mode(self.runner), "userspace")
        util.atomic_write(a.conf_path(), conf, 0o600)
        self.assertEqual(a.core_mode(self.runner), "kernel")

    def test_a_failure_on_this_kernel_keeps_the_program(self):
        self.accel(failed={"awg2": {"kernel": KERNEL, "reason": "x"}})
        self.assertEqual(self.adapter("awg2").core_mode(self.runner), "userspace")
        self.assertEqual(self.adapter("awg1").core_mode(self.runner), "kernel")
        self.accel(failed={"awg2": {"kernel": "6.8.0-49-generic", "reason": "x"}})          # another kernel: try again
        self.assertEqual(self.adapter("awg2").core_mode(self.runner), "kernel")

    def test_core_dir_of_a_kernel_unit(self):
        self.accel()
        a = self.adapter("awg2")
        a.install(self.runner)
        self.assertIn("type amneziawg", self.unit("awg2"))
        self.assertEqual(a.core_dir(self.runner), self.core_d)
        self.assertEqual(a.tool(self.runner), os.path.join(self.core_d, "awg"))


class SwitchTest(KmodCase):
    def test_switching_recreates_the_interface(self):
        self.install_all()
        a = self.adapter("awg2")
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "started")
        self.assertEqual(a.status(self.runner)["core"], "userspace")
        self.accel()                                                  # gw accel on
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "restarted")
        self.assertIn("ExecStart=/usr/sbin/ip link add dev kgw-awg2 type amneziawg", self.unit("awg2"))
        self.assertEqual(a.status(self.runner)["core"], "kernel")
        self.assertEqual(self.node.sysd.verbs("kaskad-gw-awg2.service").count("restart"), 1)
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "unchanged")
        self.accel(on=False)                                          # gw accel off
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "restarted")
        self.assertIn(" -f kgw-awg2", self.unit("awg2"))
        self.assertEqual(self.node.sysd.verbs("kaskad-gw-awg2.service").count("restart"), 2)

    def test_awgkmod_switch_inbounds_restarts_only_what_changed(self):
        self.install_all()
        for kind in INBOUNDS:
            self.adapter(kind).apply(self.st, self.sec, self.runner)
        self.accel()
        out = self.kmod.switch_inbounds(self.runner)
        self.assertEqual([(r["kind"], r["was"], r["core"], r["restarted"]) for r in out],
                         [("awg1", "userspace", "kernel", True), ("awg2", "userspace", "kernel", True),
                          ("awg3", "userspace", "userspace", False)])
        self.assertNotIn("restart", self.node.sysd.verbs("kaskad-gw-awg3.service"))

    def test_a_module_unit_that_does_not_start_falls_back(self):
        self.install_all()
        self.accel()
        orig = self.node.sysd
        unit = os.path.join(paths.systemd_dir(), "kaskad-gw-awg2.service")

        def sysd(argv, input):
            starting = argv[1] in ("start", "restart") or (argv[1] == "enable" and "--now" in argv)
            if starting and "kaskad-gw-awg2.service" in argv and "type amneziawg" in (util.read_text(unit) or ""):
                return 1, "", "Job for kaskad-gw-awg2.service failed because the control process exited with error code.\n"
            return orig(argv, input)
        self.runner._handlers = [(p, sysd if p == ["systemctl"] else h) for p, h in self.runner._handlers]
        a = self.adapter("awg2")
        res = a.apply(self.st, self.sec, self.runner)
        self.assertEqual(res["action"], "started")
        self.assertIn(" -f kgw-awg2", self.unit("awg2"))                     # amneziawg-go now
        doc = json.loads(self.read("/etc/kaskad/gw/awg-kmod.json"))
        self.assertEqual(doc["failed"]["awg2"]["kernel"], KERNEL)
        st = a.status(self.runner)
        self.assertEqual(st["core"], "userspace")
        self.assertTrue(any("на модуле ядра не поднялся" in w for w in st["warnings"]))
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "unchanged")   # no flapping
        self.assertIn("на модуле ядра не поднялся", self.log_text())

    def test_status_warns_when_the_module_is_gone(self):
        self.install_all()
        a = self.adapter("awg2")
        a.apply(self.st, self.sec, self.runner)
        self.accel(loaded=False, fallback={"kernel": KERNEL, "reason": "модуль не собрался под ядро %s" % KERNEL})
        st = a.status(self.runner)
        self.assertEqual(st["core"], "userspace")
        self.assertEqual(st["warnings"], ["откат на amneziawg-go: модуль не собрался под ядро %s" % KERNEL])
        self.assertEqual(self.adapter("awg3").status(self.runner).get("warnings", []), st["warnings"])   # 3.1 too
        self.accel(loaded=False, set_failed={"kernel": KERNEL, "reason": "x"})
        self.assertEqual(self.adapter("awg3").status(self.runner).get("warnings", []), [])   # 3.1 without the set


class LiveInterfaceTest(KmodCase):
    """#253: a live interface of the module takes only peers — syncconf never carries HeaderProtectionKey or I1–I5;
    a change of the masking recreates the interface."""

    def setUp(self):
        super().setUp()
        self.install_all()
        self.accel(set31=True)

    def test_peers_only_into_a_live_module_interface(self):
        a = self.adapter("awg3")
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "started")
        self.assertEqual(a.unit_core(), "kernel")
        full = slurp(a.conf_path())
        self.assertIn("HeaderProtectionKey = ", full)
        self.assertIn("I1 = ", full)
        self.st["gateway"]["devices"].append(device("d-tab", "Планшет", "android", ["awg3"], kind="tablet"))
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "synced")
        tool, verb, iface, text = self.node.syncs[-1]
        self.assertEqual((tool, verb, iface), ("awg", "syncconf", "kgw-awg3"))
        self.assertIn("AllowedIPs = 10.77.4.", text)
        self.assertEqual(text.count("[Peer]"), 2)
        self.assertIn("PrivateKey = ", text)
        self.assertIn("ListenPort = 51911", text)
        for k in ("HeaderProtectionKey", "I1", "Jc", "S1", "S4", "H1", "RandomTrailers", "DisableCookies"):
            self.assertNotIn("\n%s = " % k, text, k)
        self.assertEqual(stat_mode(a.sync_path()), 0o600)
        self.assertNotIn("restart", self.node.sysd.verbs("kaskad-gw-awg3.service"))

    def test_a_change_of_the_masking_recreates_the_interface(self):
        a = self.adapter("awg3")
        a.apply(self.st, self.sec, self.runner)
        n = len(self.node.syncs)
        for k, v in (("HeaderProtectionKey", awgkeys.genkey()), ("S4", "13"), ("Jc", "5"), ("H2", "7")):
            self.st["gateway"]["inbounds"]["awg3"]["params"][k] = v
            self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "restarted", k)
        self.assertEqual([s for s in self.node.syncs[n:] if s[1] == "syncconf"], [])        # never syncconf
        self.assertEqual(self.node.sysd.verbs("kaskad-gw-awg3.service").count("restart"), 4)
        # I1–I5 the server keeps as comments (the clients send them): a change is no restart, nor in syncconf
        self.st["gateway"]["inbounds"]["awg3"]["params"]["I1"] = "<r 4>"
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "synced")
        self.assertNotIn("\nI1 = ", self.node.syncs[-1][3])
        unit = self.unit("awg3")
        self.assertIn("ExecStartPre=-/usr/sbin/ip link delete dev kgw-awg3", unit)          # a fresh interface
        self.assertIn("ExecStopPost=-/usr/sbin/ip link delete dev kgw-awg3", unit)

    def test_the_program_still_takes_the_whole_config(self):
        self.accel(on=False)
        a = self.adapter("awg3")
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "started")
        self.assertEqual(a.unit_core(), "userspace")
        self.st["gateway"]["devices"].append(device("d-tab", "Планшет", "android", ["awg3"], kind="tablet"))
        self.assertEqual(a.apply(self.st, self.sec, self.runner)["action"], "synced")
        self.assertEqual(self.node.syncs[-1][2], "kgw-awg3")
        self.assertIn("HeaderProtectionKey = ", self.node.syncs[-1][3])       # amneziawg-go: as before

"""units.py — kaskad-net.service, kaskad-net-watch.service/.timer."""

import os
import stat

from kaskad import paths, units
from kaskad.errors import KaskadError
from kaskad.sysrun import FakeRunner
from tests.fakes_nft import World
from tests.helpers import RootCase


def directives(text):
    out = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out.setdefault(k, []).append(v)
    return out


class UnitsTest(RootCase):
    def setUp(self):
        super().setUp()
        self.w = World()

    def tearDown(self):
        self.assertEqual(self.w.runner.unmatched, [])
        super().tearDown()

    def test_unit_texts(self):
        texts = units.unit_texts(self.w.runner)
        self.assertEqual(sorted(texts), ["kaskad-net-watch.service", "kaskad-net-watch.timer", "kaskad-net.service"])
        net = directives(texts["kaskad-net.service"])
        nft_file = paths.nft_file()
        ctl = paths.kaskadctl()
        self.assertEqual(net["ExecStart"], ["/usr/sbin/nft -f %s" % nft_file])        # nft from runner.which
        self.assertEqual(net["ExecStartPost"], ["-%s net check --repair --quiet" % ctl])
        self.assertEqual(net["ExecStop"], ["-%s net save-counters --quiet" % ctl])
        self.assertEqual(net["Type"], ["oneshot"])
        self.assertEqual(net["RemainAfterExit"], ["yes"])
        self.assertEqual(net["DefaultDependencies"], ["no"])
        self.assertEqual(net["After"], ["local-fs.target nftables.service netfilter-persistent.service "
                                        "ufw.service firewalld.service"])
        self.assertEqual(net["Before"], ["network-pre.target shutdown.target"])
        self.assertEqual(net["Wants"], ["network-pre.target"])
        self.assertEqual(net["Conflicts"], ["shutdown.target"])
        self.assertEqual(net["ConditionPathExists"], [nft_file])
        self.assertEqual(net["WantedBy"], ["multi-user.target"])
        watch = directives(texts["kaskad-net-watch.service"])
        self.assertEqual(watch["ExecStart"], ["%s net check --repair --quiet" % ctl])
        self.assertEqual(watch["Type"], ["oneshot"])
        timer = directives(texts["kaskad-net-watch.timer"])
        self.assertEqual((timer["OnBootSec"], timer["OnUnitActiveSec"]), (["2min"], ["1min"]))
        self.assertEqual(timer["WantedBy"], ["timers.target"])
        self.assertIn("ExecStart=/usr/sbin/nft", units.unit_texts(FakeRunner())[units.NET_UNIT])   # fallback path

    def test_install_writes_enables_and_is_idempotent(self):
        res = units.install(self.w.runner)
        self.assertEqual(sorted(res["changed"]), sorted(units.UNITS))
        for name in units.UNITS:
            p = os.path.join(paths.systemd_dir(), name)
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o644)
        self.assertEqual(self.w.sysd.calls, [["systemctl", "daemon-reload"],
                                             ["systemctl", "enable", "--no-reload", "kaskad-net.service"],
                                             ["systemctl", "enable", "--no-reload", "--now", "kaskad-net-watch.timer"]])
        self.assertEqual(self.w.sysd.units["kaskad-net.service"], {"enabled": True, "active": False})
        self.assertEqual(self.w.sysd.units["kaskad-net-watch.timer"], {"enabled": True, "active": True})
        res = units.install(self.w.runner)
        self.assertEqual(res["changed"], [])
        self.assertEqual(self.w.sysd.reloads, 1)

    def test_install_rewrites_a_changed_unit(self):
        units.install(self.w.runner)
        p = os.path.join(paths.systemd_dir(), units.NET_UNIT)
        with open(p, "a", encoding="utf-8") as f:
            f.write("# edited\n")
        self.assertEqual(units.install(self.w.runner)["changed"], [units.NET_UNIT])
        self.assertEqual(self.w.sysd.reloads, 2)

    def test_install_fails_loudly(self):
        self.w.sysd.fail["enable"] = "Failed to enable unit: Access denied\n"
        with self.assertRaises(KaskadError) as cm:
            units.install(self.w.runner)
        self.assertIn("Access denied", str(cm.exception))

    def test_remove(self):
        units.install(self.w.runner)
        wants = os.path.join(paths.systemd_dir(), "timers.target.wants")
        os.makedirs(wants)
        os.symlink("/nonexistent/kaskad-net-watch.timer", os.path.join(wants, units.WATCH_TIMER))
        res = units.remove(self.w.runner)
        self.assertEqual(sorted(res["removed"]), sorted(units.UNITS))
        for name in units.UNITS:
            self.assertFalse(os.path.lexists(os.path.join(paths.systemd_dir(), name)))
        self.assertFalse(os.path.lexists(os.path.join(wants, units.WATCH_TIMER)))
        self.assertIn(["systemctl", "disable", "--now", "kaskad-net-watch.timer"], self.w.sysd.calls)
        self.assertIn(["systemctl", "disable", "kaskad-net.service"], self.w.sysd.calls)
        self.assertNotIn(["systemctl", "stop", "kaskad-net.service"], self.w.sysd.calls)   # never a blocking stop
        self.assertIn(["systemctl", "stop", "--no-block", "kaskad-net.service"], self.w.sysd.calls)
        self.assertEqual(self.w.sysd.calls[-1], ["systemctl", "daemon-reload"])
        self.assertEqual(self.w.sysd.units["kaskad-net-watch.timer"], {"enabled": False, "active": False})
        res = units.remove(self.w.runner)                       # absent: no error
        self.assertEqual(res["removed"], [])

    def test_activate_starts_the_service_once(self):
        # a unit that was only enabled is not stopped at shutdown: ExecStop (counters) would not run
        units.install(self.w.runner)
        self.assertFalse(self.w.sysd.units["kaskad-net.service"]["active"])
        self.assertTrue(units.activate(self.w.runner))
        self.assertTrue(self.w.sysd.units["kaskad-net.service"]["active"])
        self.assertFalse(units.activate(self.w.runner))                      # already active: no restart
        self.assertEqual(sum(1 for c in self.w.sysd.calls if c[1:2] == ["start"]), 1)

    def test_status(self):
        st = units.status(self.w.runner)
        self.assertEqual(st, dict((n, {"enabled": False, "active": False}) for n in units.UNITS))
        units.install(self.w.runner)
        st = units.status(self.w.runner)
        self.assertEqual(st["kaskad-net.service"], {"enabled": True, "active": False})
        self.assertEqual(st["kaskad-net-watch.timer"], {"enabled": True, "active": True})
        self.assertEqual(st["kaskad-net-watch.service"], {"enabled": True, "active": False})   # static

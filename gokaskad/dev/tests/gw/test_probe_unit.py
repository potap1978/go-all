"""The units of the probes (docs/k3/SPEC-stage1-exits §5.7, §10.3, §11.1 «probe/unit.py»; stage 1, T6): the timer with
``OnBootSec``, the service without ``CPUQuota``; ``gw enable`` writes all three; ``gw disable`` and ``gw purge`` remove
all three; ``kaskadctl update units`` knows them; the entry of the units runs the code of the payload."""

import os
import subprocess
import sys

from kaskad import paths, unit_refresh
from kaskad.gw import sys as gsys
from kaskad.gw.probe import unit
from tests.test_gw_ops import CliGwCase

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def lines(text):
    return [ln.strip() for ln in text.splitlines()]


class TextsTest(CliGwCase):
    def test_timer_and_service(self):
        t = lines(unit.timer_text())
        for want in ("OnBootSec=2min", "OnUnitActiveSec=60", "RandomizedDelaySec=10", "Unit=kaskad-gw-probe.service",
                     "WantedBy=timers.target"):
            self.assertIn(want, t)
        for text in (unit.service_text(), unit.template_text()):
            s = lines(text)
            for want in ("Type=oneshot", "Nice=10", "IOSchedulingClass=idle", "MemoryMax=96M", "TimeoutStartSec=120"):
                self.assertIn(want, s)
            self.assertFalse([ln for ln in s if ln.startswith("CPUQuota")])
        self.assertIn(" --exit %i", unit.template_text())
        self.assertEqual((unit.SERVICE, unit.TIMER, unit.TEMPLATE),
                         ("kaskad-gw-probe.service", "kaskad-gw-probe.timer", "kaskad-gw-probe@.service"))

    def test_refresh_knows_the_three(self):
        names = dict((n, k) for n, _, k in unit_refresh.registry())
        self.assertEqual((names[unit.SERVICE], names[unit.TEMPLATE], names[unit.TIMER]),
                         (unit_refresh.ONESHOT, unit_refresh.ONESHOT, unit_refresh.TIMER))

    def test_entry_runs_the_package(self):
        # the ExecStart of the units: runpy of kaskad.gw.probe from the lib on the path (here — the repository's)
        line = [ln for ln in unit.service_text().splitlines() if ln.startswith("ExecStart=")][0]
        code = line.split('-c "', 1)[1].rsplit('"', 1)[0].replace(paths.lib_current(), os.path.join(ROOT, "lib"))
        env = dict(os.environ, KASKAD_ROOT=self.root, LANG="en_US.UTF-8")
        p = subprocess.run([sys.executable, "-I", "-c", code, "--help"], capture_output=True, text=True, env=env,
                           timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("--exit", p.stdout)


ALL = sorted(("kaskad-gw-probe.service", "kaskad-gw-probe.timer", "kaskad-gw-probe@.service"))   # not from the code


class LifecycleTest(CliGwCase):
    def files(self):
        return sorted(n for n in ALL if os.path.exists(gsys.unit_path(n)))

    def test_enable_writes_disable_removes(self):
        self.enabled_world()
        self.assertEqual(self.files(), ALL)
        self.assertIn(["systemctl", "enable", "--no-reload", unit.TIMER], self.w.runner.commands(["systemctl"]))
        self.ok("gw", "disable")
        self.assertEqual(self.files(), [])                       # «list-unit-files 'kaskad-gw-probe*'» is empty
        self.assertIn(["systemctl", "stop", "kaskad-gw-probe@*.service"], self.w.runner.commands(["systemctl"]))
        self.ok("gw", "enable")
        self.assertEqual(self.files(), ALL)

    def test_purge_removes(self):
        self.enabled_world()
        self.ok("gw", "purge", "--yes")
        self.assertEqual(self.files(), [])

    def test_purge_of_a_gateway_already_off(self):
        self.enabled_world()
        for n, fn, _ in unit.REFRESH:
            gsys.write_unit(n, fn())
        self.ok("gw", "disable")
        for n, fn, _ in unit.REFRESH:                            # left by an older version or by hand
            gsys.write_unit(n, fn())
        self.ok("gw", "purge", "--yes")
        self.assertEqual(self.files(), [])

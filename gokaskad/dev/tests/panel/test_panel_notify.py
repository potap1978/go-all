"""T-M6 №9: the notify socket of kaskad-panel (Type=notify, NotifyAccess=main) belongs to the main process alone —
children of the panel (apt, certbot, xray, gokaskad) do not inherit NOTIFY_SOCKET, so systemd has nothing to log as
«Got notification message from PID …, but reception only permitted for main PID»."""

import inspect
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from kaskad import sysrun
from kaskad.panel import cli as panel_cli
from kaskad.panel import server

SHOW = [sys.executable, "-c", "import os; print(os.environ.get('NOTIFY_SOCKET'))"]


class NotifyTest(unittest.TestCase):
    def test_taken_out_of_the_environment(self):
        with mock.patch.dict(os.environ, {"NOTIFY_SOCKET": "/run/systemd/notify"}):
            self.assertEqual(server._take_notify_socket(), "/run/systemd/notify")
            self.assertNotIn("NOTIFY_SOCKET", os.environ)
            out = subprocess.run(SHOW, stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()
            self.assertEqual(out, "None")
            self.assertIsNone(server._take_notify_socket())

    def test_ready_goes_to_the_saved_socket(self):
        d = tempfile.mkdtemp(prefix="kn")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "n")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(s.close)
        s.bind(path)
        s.settimeout(2)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NOTIFY_SOCKET", None)
            self.assertTrue(server._notify_ready(path))
        self.assertEqual(s.recv(64), b"READY=1")
        self.assertFalse(server._notify_ready(""))

    def test_commands_of_the_core_never_get_it(self):
        with mock.patch.dict(os.environ, {"NOTIFY_SOCKET": "@/org/freedesktop/systemd1/notify"}):
            r = sysrun.SystemRunner()
            self.assertNotIn("NOTIFY_SOCKET", r.env)
            self.assertEqual(r.run(SHOW).out.strip(), "None")
            self.assertEqual(os.environ["NOTIFY_SOCKET"], "@/org/freedesktop/systemd1/notify")   # the process keeps it

    def test_serve_takes_it_before_anything_runs(self):
        src = inspect.getsource(server.serve)
        self.assertLess(src.index("_take_notify_socket()"), src.index("sysrun.default_runner()"))
        self.assertIn("_notify_ready(notify)", src)
        self.assertIn("NotifyAccess=main", panel_cli.unit_text())     # not «all»: children may not move the unit


if __name__ == "__main__":
    unittest.main()

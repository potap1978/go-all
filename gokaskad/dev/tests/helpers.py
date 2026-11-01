"""Shared test helpers: a temporary KASKAD_ROOT per test case."""

import contextlib
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from kaskad.gw import awgengine


@contextlib.contextmanager
def zone(tz):
    """The local time zone of this process for a while (``TZ`` + time.tzset): texts that say the clock of the server."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = tz
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


@contextlib.contextmanager
def lock_held_elsewhere(path=None):
    """The state lock (``paths.lock()``) held by ANOTHER process for a while — the «busy» of a test (LockedError, code
    6). The same thread taking it again is no «busy» but an error of the code (NestedLockError, code 1; SPEC-stage1-exits
    §3.1, §11.1), so a test of «busy» never holds the lock itself."""
    import subprocess
    import sys
    from kaskad import paths
    lp = path or paths.lock()
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    code = ("import fcntl, os, sys, time\n"
            "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\nprint('ok', flush=True)\nsys.stdin.read()\n")
    p = subprocess.Popen([sys.executable, "-c", code, lp], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        if p.stdout.readline().strip() != b"ok":
            raise AssertionError("the lock holder did not start")
        yield
    finally:
        p.stdin.close()
        p.wait(10)
        p.stdout.close()


def legacy_state(st=None):
    """A state of an UPDATED node (``gen 1``, SPEC-stage1-exits §1.3) — for tests that build it with ``state.empty()``
    (a new node since stage 1: «Стандарт», «нестабилен») and expect the behaviour of an old one."""
    from kaskad import state
    st = st if st is not None else state.empty()
    st["gen"] = 1
    return st


class RootCase(unittest.TestCase):
    """Every path from kaskad.paths resolves inside a fresh temp dir for the duration of a test."""

    def setUp(self):
        self._old_root = os.environ.get("KASKAD_ROOT")
        self.root = tempfile.mkdtemp(prefix="kaskad-test-")
        os.environ["KASKAD_ROOT"] = self.root
        # the module of the kernel by default (gw/awgengine.py) runs host commands on the first AmneziaWG inbound and
        # after gw apply: off for every test but its own (tests/gw/test_awg_engine.py switches it on)
        self._auto_engine = mock.patch.object(awgengine, "AUTO", False)
        self._auto_engine.start()

    def tearDown(self):
        self._auto_engine.stop()
        if self._old_root is None:
            os.environ.pop("KASKAD_ROOT", None)
        else:
            os.environ["KASKAD_ROOT"] = self._old_root
        shutil.rmtree(self.root, ignore_errors=True)

    def path(self, abs_path):
        return self.root + abs_path

    def write(self, abs_path, text, mode=0o600):
        p = self.path(abs_path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(p, mode)
        return p

    def read(self, abs_path):
        with open(self.path(abs_path), encoding="utf-8") as f:
            return f.read()

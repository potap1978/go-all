"""kaskad.aptlock — apt busy on a fresh server: who holds the dpkg lock (asked, never taken), one line before
waiting, a clear error naming the holder after 10 minutes; gw.sys.apt_install waits the same way."""

import contextlib
import fcntl
import io
import os
import signal
import subprocess
import sys
from unittest import mock

from kaskad import aptlock, netcheck, nft, paths
from kaskad.errors import KaskadError
from kaskad.gw import sys as gsys
from kaskad.migrate import interrupt
from kaskad.sysrun import Result
from tests.fakes_nft import F443, World, save_state
from tests.gwcase import GwCase
from tests.helpers import RootCase

UU = {"path": "/var/lib/dpkg/lock-frontend", "pid": 4321, "name": "unattended-upgrade"}
HOLD = ("import fcntl, os, sys, time\n"
        "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o640)\n"
        "fcntl.lockf(fd, fcntl.LOCK_EX)\n"           # a classic fcntl write lock — what dpkg and apt take
        "print('locked', flush=True)\n"
        "time.sleep(60)\n")
TRY_LOCK = ("import fcntl, os, sys\n"
            "fd = os.open(sys.argv[1], os.O_RDWR)\n"
            "try:\n"
            "    fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "except OSError:\n"
            "    sys.exit(1)\n")


class HolderTest(RootCase):
    def hold(self, abs_path):
        """A child process holding the lock, as dpkg does (a lock of our own process would not conflict)."""
        p = self.path(abs_path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        child = subprocess.Popen([sys.executable, "-c", HOLD, p], stdout=subprocess.PIPE)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        self.assertEqual(child.stdout.readline().strip(), b"locked")
        child.stdout.close()
        return child

    def test_free_apt(self):
        self.assertIsNone(aptlock.holder())                       # no lock files at all
        for p in paths.apt_locks():
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").close()
        self.assertIsNone(aptlock.holder())                       # files, nobody holds them

    def test_held_lock_is_asked_about_never_taken(self):
        child = self.hold("/var/lib/dpkg/lock")
        self.write("/proc/%d/cmdline" % child.pid, "/usr/bin/python3\0/usr/bin/unattended-upgrade\0--download-only\0")
        h = aptlock.holder()
        self.assertEqual(h, {"path": self.path("/var/lib/dpkg/lock"), "pid": child.pid, "name": "unattended-upgrade"})
        self.assertEqual(aptlock.who(h), "unattended-upgrade (pid %d)" % child.pid)
        self.assertEqual(aptlock.holder(), h)                     # asking twice changes nothing
        rc = subprocess.call([sys.executable, "-c", TRY_LOCK, self.path("/var/lib/dpkg/lock")])
        self.assertEqual(rc, 1)                                   # the child still holds it: we took nothing
        child.kill()
        child.wait()
        self.assertIsNone(aptlock.holder())

    def test_names_of_holders(self):
        cases = [("/usr/bin/python3\0/usr/bin/unattended-upgrade\0", "unattended-upgrade"),
                 ("/usr/bin/apt-get\0install\0-y\0nftables\0", "apt-get"),
                 ("/usr/bin/dpkg\0--status-fd\0" "23\0--configure\0", "dpkg"),
                 ("/bin/sh\0-c\0apt-get update\0", "sh")]
        for pid, (cmdline, want) in enumerate(cases, 101):
            self.write("/proc/%d/cmdline" % pid, cmdline)
            self.assertEqual(aptlock._name(pid), want)
        self.write("/proc/200/comm", "packagekitd\n")             # no cmdline (a kernel thread, a zombie)
        self.assertEqual(aptlock._name(200), "packagekitd")
        self.assertEqual(aptlock._name(201), "")
        self.assertEqual(aptlock._name(0), "")
        self.assertEqual(aptlock.who({"pid": 0, "name": ""}), "")
        self.assertEqual(aptlock.who({"pid": 77, "name": ""}), "pid 77")


class WaitTest(RootCase):
    def setUp(self):
        super().setUp()
        self.clock = [0.0]
        self.held = []

        def sleep(s):
            self.clock[0] += s
        self._p = [mock.patch.object(aptlock, "_now", lambda: self.clock[0]),
                   mock.patch.object(aptlock, "_sleep", sleep),
                   mock.patch.object(aptlock, "holder", lambda: self.held.pop(0) if self.held else None)]
        for p in self._p:
            p.start()

    def tearDown(self):
        for p in self._p:
            p.stop()
        super().tearDown()

    def test_free_apt_costs_no_wait_and_no_line(self):
        said = []
        self.assertEqual(aptlock.wait(say=said.append), 0)
        self.assertEqual((said, self.clock[0]), ([], 0.0))

    def test_one_line_then_wait_until_free(self):
        self.held = [UU] * 4                                    # held at the first look and 3 polls more
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(aptlock.wait(), 8)
        self.assertEqual(err.getvalue(), "Система ставит обновления (apt занят) — жду до 10 минут…\n")
        self.assertIn("APT: ждал 8 с, пока система ставила обновления (unattended-upgrade (pid 4321))",
                      self.read("/var/log/kaskad.log"))

    def test_ten_minutes_then_a_clear_error_naming_the_holder(self):
        self.held = [UU] * 1000
        said = []
        with self.assertRaises(KaskadError) as cm:
            aptlock.wait(say=said.append)
        self.assertEqual(str(cm.exception), "apt занят дольше 10 минут: блокировку держит unattended-upgrade (pid 4321) "
                                            "— система ставит обновления. Повторите, когда закончит.")
        self.assertEqual((said, self.clock[0]), ([aptlock.WAIT_LINE], 600.0))
        self.held = [{"path": "/var/lib/dpkg/lock", "pid": 0, "name": ""}] * 1000
        with self.assertRaises(KaskadError) as cm:
            aptlock.wait(say=said.append)
        self.assertEqual(str(cm.exception), "apt занят дольше 10 минут — система ставит обновления. Повторите позже.")

    def test_the_holder_may_change_while_waiting(self):
        apt = {"path": "/var/lib/apt/lists/lock", "pid": 99, "name": "apt-get"}
        self.held = [apt] * 2 + [UU] * 1000                     # apt-daily (update), then unattended-upgrade
        with self.assertRaises(KaskadError) as cm:
            aptlock.wait(say=lambda t: None)
        self.assertIn("unattended-upgrade (pid 4321)", str(cm.exception))

    def test_failure_text_of_an_apt_get_that_hit_the_lock(self):
        err = ("E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 4321 (unattended-upgr)\n"
               "N: Be aware that removing the lock file is not a solution and may break your system.\n"
               "E: Unable to acquire the dpkg frontend lock (/var/lib/dpkg/lock-frontend), is another process using it?\n")
        res = Result(["apt-get"], 100, "", err)
        self.assertEqual(aptlock.failure_text(res), "apt занят: блокировку держит unattended-upgr (pid 4321) — система "
                                                    "ставит обновления. Повторите, когда закончит.")
        self.write("/proc/4321/cmdline", "/usr/bin/python3\0/usr/bin/unattended-upgrade\0")
        self.assertIn("unattended-upgrade (pid 4321)", aptlock.failure_text(res))          # the full name
        self.assertIsNone(aptlock.failure_text(Result(["apt-get"], 100, "", "E: Unable to locate package nosuch\n")))


class AptInstallTest(GwCase):
    """gw.sys.apt_install (packages of the gateway and its inbounds) waits for apt like migrate does."""

    def spy(self):
        timeouts = []
        run = self.w.runner.run

        def spy(argv, input=None, check=False, timeout=60):
            if "apt-get" in argv:
                timeouts.append(timeout)
            return run(argv, input=input, check=check, timeout=timeout)
        self.w.runner.run = spy
        return timeouts

    def test_waits_says_once_and_runs_apt_with_its_own_lock_wait(self):
        held = [UU] * 3
        timeouts = self.spy()
        err = io.StringIO()
        with mock.patch.object(aptlock, "holder", lambda: held.pop(0) if held else None), \
                mock.patch.object(aptlock, "_sleep", lambda s: None), contextlib.redirect_stderr(err):
            self.assertEqual(gsys.apt_install(self.w.runner, ["strongswan"]), ["strongswan"])
        self.assertEqual(err.getvalue(), aptlock.WAIT_LINE + "\n")
        installs = [c for c in self.w.runner.commands(["env"]) if "install" in c]
        self.assertEqual(len(installs), 1)
        self.assertIn("DPkg::Lock::Timeout=600", installs[0])
        self.assertTrue(timeouts and all(t > aptlock.WAIT for t in timeouts), timeouts)
        self.assertFalse(os.path.exists(paths.policy_rc_d()))

    def test_busy_for_ten_minutes_installs_nothing(self):
        clock = [0.0]

        def sleep(s):
            clock[0] += s
        with mock.patch.object(aptlock, "holder", lambda: UU), mock.patch.object(aptlock, "_sleep", sleep), \
                mock.patch.object(aptlock, "_now", lambda: clock[0]), mock.patch("sys.stderr"):
            with self.assertRaises(KaskadError) as cm:
                gsys.apt_install(self.w.runner, ["strongswan"])
        self.assertIn("apt занят дольше 10 минут: блокировку держит unattended-upgrade (pid 4321)", str(cm.exception))
        self.assertEqual([c for c in self.w.runner.commands(["env"]) if "install" in c], [])
        self.assertFalse(os.path.exists(paths.policy_rc_d()))                 # policy-rc.d is ours to remove

    def hold_install_lock(self):
        """Another install of Kaskad (the bot) holds the install lock: an own open file description — flock
        conflicts between two of them even inside one process."""
        p = paths.apt_install_lock()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def test_one_install_of_kaskad_at_a_time(self):
        fd = self.hold_install_lock()
        policy_during = []

        def sleep(s):                                       # the other install finishes during our first pause
            policy_during.append(os.path.exists(paths.policy_rc_d()))
            os.close(fd)
        err = io.StringIO()
        with mock.patch.object(aptlock, "_sleep", sleep), contextlib.redirect_stderr(err):
            self.assertEqual(gsys.apt_install(self.w.runner, ["strongswan"]), ["strongswan"])
        self.assertEqual(err.getvalue(), aptlock.BUSY_LINE + "\n")
        self.assertEqual(policy_during, [False])            # ours appears only once the other is done
        self.assertEqual(self.w.pkgs.policy_seen, [True])
        self.assertFalse(os.path.exists(paths.policy_rc_d()))

    def test_another_install_for_too_long(self):
        fd = self.hold_install_lock()
        self.addCleanup(os.close, fd)
        clock = [0.0]

        def sleep(s):
            clock[0] += s
        with mock.patch.object(aptlock, "_sleep", sleep), mock.patch.object(aptlock, "_now", lambda: clock[0]), \
                mock.patch("sys.stderr"):
            with self.assertRaises(KaskadError) as cm:
                gsys.apt_install(self.w.runner, ["strongswan"])
        self.assertIn("Kaskad ставит пакеты в другом окне или из бота дольше 30 минут", str(cm.exception))
        self.assertEqual([c for c in self.w.runner.commands(["env"]) if "install" in c], [])

    def test_sighup_during_apt_never_leaves_policy_rc_d(self):
        """The SSH session drops while apt-get runs: the signal is remembered, apt-get finishes, policy-rc.d goes,
        then the install stops (no retry)."""
        apt = self.w.pkgs.apt

        def apt_with_hangup(argv, inp):
            os.kill(os.getpid(), signal.SIGHUP)
            self.assertTrue(os.path.exists(paths.policy_rc_d()))    # still alive here: the signal waits
            return apt(argv, inp)
        self.w.runner._handlers.insert(0, (["env", "DEBIAN_FRONTEND=noninteractive", "NEEDRESTART_SUSPEND=1", "apt-get"], apt_with_hangup))
        before = signal.getsignal(signal.SIGHUP)
        with self.assertRaises(interrupt.Interrupted) as cm:
            gsys.apt_install(self.w.runner, ["strongswan"])
        self.assertIn("SIGHUP", str(cm.exception))
        self.assertFalse(os.path.exists(paths.policy_rc_d()))
        self.assertEqual(len([c for c in self.w.runner.commands(["env"]) if "install" in c]), 1)
        self.assertEqual(signal.getsignal(signal.SIGHUP), before)                 # handlers given back
        self.assertEqual(gsys.apt_install(self.w.runner, ["strongswan"]), [])     # and installed indeed

    def test_sighup_while_waiting_for_apt_stops_before_policy_rc_d(self):
        def holder():
            os.kill(os.getpid(), signal.SIGTERM)
            return UU
        with mock.patch.object(aptlock, "holder", holder), mock.patch("sys.stderr"):
            with self.assertRaises(interrupt.Interrupted):
                gsys.apt_install(self.w.runner, ["strongswan"])
        self.assertFalse(os.path.exists(paths.policy_rc_d()))
        self.assertEqual([c for c in self.w.runner.commands(["env"]) if "install" in c], [])

    def test_lock_taken_again_by_the_time_apt_starts(self):
        self.w.pkgs.fail = ("E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 4321 "
                            "(unattended-upgr)\n")
        with self.assertRaises(KaskadError) as cm:
            gsys.apt_install(self.w.runner, ["strongswan"])
        self.assertEqual(str(cm.exception), "Пакеты strongswan не установлены: apt занят: блокировку держит "
                                            "unattended-upgr (pid 4321) — система ставит обновления. Повторите, когда закончит.")
        self.assertEqual(cm.exception.data, {"kind": "apt_busy"})      # the SSH menu decides by it, not by the words
        self.assertFalse([c for c in self.w.runner.commands(["env"]) if "update" in c])   # update would hit it too
        self.assertEqual(len([c for c in self.w.runner.commands(["env"]) if "install" in c]), 2)


class PolicyLeftoverTest(RootCase):
    """What a killed install of Kaskad (SIGKILL, OOM, a power cut — no ``finally``) leaves: our policy-rc.d.
    The minute watchdog removes it once no install of Kaskad runs and apt/dpkg are idle; a foreign one stays."""

    def setUp(self):
        super().setUp()
        self.w = World()
        nft.apply(save_state([F443]), self.w.runner)

    def policy(self, res):
        return [p for p in res["problems"] if p["kind"] == "policy_rc_d"]

    def test_left_behind_is_reported_then_removed(self):
        self.write("/usr/sbin/policy-rc.d", aptlock.POLICY_RC_D, 0o755)
        res = netcheck.check(self.w.runner, repair=False)
        self.assertEqual(len(self.policy(res)), 1)
        self.assertTrue(os.path.exists(paths.policy_rc_d()))                 # a report changes nothing
        res = netcheck.check(self.w.runner, repair=True)
        self.assertEqual([p["repaired"] for p in self.policy(res)], [True])
        self.assertFalse(os.path.exists(paths.policy_rc_d()))
        self.assertIn("NET repair: %s после прерванной установки пакетов — убрал" % paths.policy_rc_d(),
                      self.read("/var/log/kaskad.log"))
        self.assertEqual(self.policy(netcheck.check(self.w.runner, repair=False)), [])

    def test_a_foreign_one_or_one_in_use_stays(self):
        self.write("/usr/sbin/policy-rc.d", "#!/bin/sh\nexit 101\n", 0o755)             # Docker images ship one
        self.assertEqual(self.policy(netcheck.check(self.w.runner, repair=True)), [])
        self.assertTrue(os.path.exists(paths.policy_rc_d()))
        self.write("/usr/sbin/policy-rc.d", aptlock.POLICY_RC_D, 0o755)
        p = paths.apt_install_lock()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)                                  # an install of Kaskad is running
        try:
            self.assertEqual(self.policy(netcheck.check(self.w.runner, repair=True)), [])
            self.assertTrue(os.path.exists(paths.policy_rc_d()))
        finally:
            os.close(fd)
        with mock.patch.object(aptlock, "holder", lambda: UU):          # unattended-upgrades is at work
            self.assertEqual(self.policy(netcheck.check(self.w.runner, repair=True)), [])
            self.assertTrue(os.path.exists(paths.policy_rc_d()))
        self.assertEqual([p["repaired"] for p in self.policy(netcheck.check(self.w.runner, repair=True))], [True])
        self.assertFalse(os.path.exists(paths.policy_rc_d()))

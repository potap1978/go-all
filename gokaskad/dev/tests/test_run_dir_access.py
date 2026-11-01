"""/run/kaskad lets nginx (www-data) through to the panel's socket /run/kaskad/panel/proxy.sock (3.2.22).

3.2.21 on the owner's node O: the lock of the pool (install.sh egress_pool_locked) made /run/kaskad 0700 at every change
of the pool, and the bot did the same every 30 s (``util.ensure_dir(paths.run_dir(), 0o700)``) — the panel on the domain
answered 502. The guard: no path of the code (bash or Python) makes /run/kaskad stricter than 0711; what finds it
closed gives the passage back (the panel at the start and every RUN_DIR_EVERY s, the collector with each live.json);
``site check`` names the cause and the fix.
"""

import os
import re
import stat
import types
import time
import unittest
from unittest import mock

from kaskad import aptlock, boot, paths, state, util
from kaskad.bot import app as bot_app
from kaskad.gw import live, ops
from kaskad.panel import conf as pconf
from kaskad.panel import server as pserver
from kaskad.site import templates
from tests.helpers import RootCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib", "kaskad")


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def install_sh_code():
    """install.sh without the generated K3_PAYLOAD and without comments."""
    with open(os.path.join(ROOT, "install.sh"), encoding="utf-8") as f:
        text = f.read()
    a, b = text.index("# >>> K3_PAYLOAD"), text.index("# <<< K3_PAYLOAD")
    code = text[:a] + text[b:]
    return "\n".join(ln for ln in code.splitlines() if not ln.lstrip().startswith("#"))


class StaticGuardTest(unittest.TestCase):
    def test_no_python_code_closes_the_run_dir(self):
        bad = []
        rx = re.compile(r"ensure_dir\(\s*paths\.run_dir\(\)|(makedirs|chmod)\(\s*paths\.(run_dir|gw_run_dir|panel_run_dir)\(\)")
        for d, _, files in os.walk(LIB):
            for name in files:
                if not name.endswith(".py") or "vendor" in d:
                    continue
                p = os.path.join(d, name)
                with open(p, encoding="utf-8") as f:
                    for n, ln in enumerate(f, 1):
                        if rx.search(ln):
                            bad.append("%s:%d: %s" % (os.path.relpath(p, ROOT), n, ln.strip()))
        self.assertEqual(bad, [], "/run/kaskad создаётся и правится только через util.ensure_run_dir")

    def test_no_bash_code_closes_the_run_dir(self):
        code = install_sh_code()
        self.assertNotRegex(code, r'chmod\s+0?7[0-5]0\s+"\$\{(EGRESS_POOL_LOCK|K3_LOCK)%/\*\}"')
        self.assertNotRegex(code, r'chmod\s+0?7[0-5]0\s+"?\$\{?K3_RUNDIR')
        for fn in ("egress_pool_locked", "k3_legacy_locked"):
            body = code[code.index(fn + "() {"):]
            body = body[:body.index("\n}\n")]
            self.assertIn("k3_run_dir_make", body, fn)
            self.assertNotIn("chmod 700", body, fn)


class RunDirTest(RootCase):
    """Every writer of /run/kaskad keeps the passage (≥ 0711): from 0755, from nothing, and from a closed 0700."""

    def setUp(self):
        super().setUp()
        self.run = paths.run_dir()

    def open_run(self, m):
        os.makedirs(self.run, exist_ok=True)
        os.chmod(self.run, m)

    def writers(self):
        def bot_status():
            fake = types.SimpleNamespace(_status_at=0.0, started=0, last_poll=None, last_error=None, offset=0,
                                         username="", cfg=types.SimpleNamespace(bound=True, proxy=""),
                                         proxy_error=None, via="direct", fallback_since=None, clock=time.time)
            bot_app.App.write_status(fake, force=True)

        def state_lock():
            with state.locked():
                pass

        def apt_lock():
            os.close(aptlock._open_install_lock())

        def site_lock():
            with templates._RootLock():
                pass
        def leases_lock():
            from kaskad.gw import hooks
            with hooks._leases_lock():
                pass

        def legs_lock():
            from kaskad.gw import exits
            util.ensure_dir(os.path.dirname(exits._legs_lock()), 0o700)

        def panel_status():
            pconf.write_status({"v": 1}) if hasattr(pconf, "write_status") else util.ensure_dir(paths.panel_run_dir(), 0o710)
        return [("state.locked", state_lock), ("aptlock", apt_lock), ("live.touch_want", live.touch_want),
                ("hooks._leases_lock", leases_lock), ("legs lock dir", legs_lock), ("panel status", panel_status),
                ("atomic_write gw/", lambda: util.atomic_write(os.path.join(paths.gw_run_dir(), "sessions.json"), "[]")),
                ("live.write", lambda: live.write({"ts": 1})), ("bot.write_status", bot_status),
                ("boot.done", boot.done), ("templates._RootLock", site_lock),
                ("util.atomic_write", lambda: util.atomic_write(os.path.join(self.run, "x.json"), "{}"))]

    def test_writers_keep_an_open_dir_open(self):
        for name, fn in self.writers():
            self.open_run(0o755)
            fn()
            self.assertEqual(mode(self.run), 0o755, name)

    def test_writers_create_it_passable(self):
        for name, fn in self.writers():
            if os.path.isdir(self.run):
                for x in os.listdir(self.run):
                    p = os.path.join(self.run, x)
                    if os.path.isdir(p):
                        import shutil
                        shutil.rmtree(p)
                    else:
                        os.unlink(p)
                os.rmdir(self.run)
            fn()
            self.assertEqual(mode(self.run) & 0o011, 0o011, name)

    def test_a_closed_dir_gets_the_passage_back(self):
        bot = dict(self.writers())["bot.write_status"]
        for name, fn in (("live.write", lambda: live.write({"ts": 1})), ("panel", pserver._run_dir_ok),
                         ("bot.write_status", bot)):
            self.open_run(0o700)
            fn()
            self.assertEqual(mode(self.run), 0o711, name)

    def wipe(self):
        import shutil
        shutil.rmtree(self.run, ignore_errors=True)

    def test_writers_under_a_strict_umask(self):
        """A hardened root shell (umask 077 or 027): os.makedirs makes a missing parent by the umask — /run/kaskad
        must still come out passable whoever creates it first (the critic of 3.2.22)."""
        for um in (0o077, 0o027):
            for name, fn in self.writers():
                self.wipe()
                old = os.umask(um)
                try:
                    fn()
                finally:
                    os.umask(old)
                self.assertEqual(mode(self.run) & 0o011, 0o011, "%s, umask %04o" % (name, um))

    def test_ensure_run_dir_never_takes_bits_away(self):
        self.open_run(0o755)
        util.ensure_run_dir()
        self.assertEqual(mode(self.run), 0o755)
        self.open_run(0o750)
        util.ensure_run_dir()
        self.assertEqual(mode(self.run), 0o751)


class ProxyAccessTest(RootCase):
    def setUp(self):
        super().setUp()
        self.gid = mock.patch.object(pconf, "_proxy_gid", return_value=None)      # no www-data on the test machine
        self.gid.start()
        self.addCleanup(self.gid.stop)

    def make(self, run_mode, panel_mode=0o711, sock_mode=0o666):
        run, pdir = paths.run_dir(), paths.panel_run_dir()
        os.makedirs(pdir, exist_ok=True)
        with open(pconf.proxy_sock_path(), "w"):
            pass
        os.chmod(pconf.proxy_sock_path(), sock_mode)
        os.chmod(pdir, panel_mode)
        os.chmod(run, run_mode)

    def test_closed_run_dir_is_named_with_its_mode(self):
        self.make(0o700)
        p = pconf.proxy_access_problem()
        self.assertIn("закрыт для nginx", p["why"])
        self.assertIn("0700", p["why"])
        self.assertIn("chmod 711 %s" % paths.run_dir(), p["fix"])

    def test_open_dirs_and_a_listening_panel(self):
        self.make(0o711)
        with mock.patch.object(pconf, "_answers", return_value=True):
            self.assertIsNone(pconf.proxy_access_problem())

    def test_a_stale_socket_of_a_fallen_panel(self):
        self.make(0o711)                                  # a plain file in place of the socket: nobody answers
        p = pconf.proxy_access_problem()
        self.assertIn("не отвечает", p["why"])
        self.assertEqual(p["fix"], pconf.fix_restart())

    def test_no_socket(self):
        self.make(0o711)
        os.unlink(pconf.proxy_sock_path())
        p = pconf.proxy_access_problem()
        self.assertIn("нет сокета панели", p["why"])
        self.assertEqual(p["fix"], pconf.fix_restart())


class ModuleBuildTest(RootCase):
    """«Собираю модуль ядра AmneziaWG — 1–3 минуты»: the preview tells the panel when the first AmneziaWG key builds
    the module (3.2.21: the owner took the wait for a fallen server)."""

    AWG = {"id": "e-1", "kind": "awg", "title": "AmneziaWG 2.0", "in_pool": False}
    AWG31 = {"id": "e-2", "kind": "awg", "title": "AmneziaWG 3.1", "in_pool": False}
    VLESS = {"id": "e-3", "kind": "vless", "title": "VLESS", "in_pool": False}

    def build(self, items, loaded=False, set_ok=False, want=None):
        from kaskad.gw import awgengine, awgkmod
        with mock.patch.object(awgkmod, "module_loaded", return_value=loaded), \
                mock.patch.object(awgkmod, "set_loaded", return_value=set_ok), \
                mock.patch.object(awgengine, "record", return_value={"want": want}):
            return ops.module_build(items)

    def test_cases(self):
        self.assertTrue(self.build([self.AWG]))
        self.assertTrue(self.build([self.AWG], want="kernel"))
        self.assertFalse(self.build([self.AWG], want="program"))
        self.assertFalse(self.build([self.VLESS]))
        self.assertFalse(self.build([dict(self.AWG, in_pool=True)]))
        self.assertFalse(self.build([self.AWG], loaded=True))
        self.assertTrue(self.build([self.AWG31], loaded=True, set_ok=False))
        self.assertFalse(self.build([self.AWG31], loaded=True, set_ok=True))


if __name__ == "__main__":
    unittest.main()

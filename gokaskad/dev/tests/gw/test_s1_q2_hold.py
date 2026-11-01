"""T15 Q2 — находки REV-s1-q4-3 по метке «ждут применения» (.ru-hold) в ядре.

З-1: запись российских ключей оборвана между меткой и файлом (kill -9, перезагрузка) — метка ждёт файла, которого не
будет. Отпечаток другой и блокировка пула свободна (никто не пишет) — ядро снимает метку по файлу, который есть; пул
занят — ждёт.
З-2: сверка и снятие метки — под legs.lock (bash пишет метку под ним): метку, записанную во время сверки, не стирают.
gw apply под bash получает его замок по наследству (KASKAD_LEGS_LOCK_FD) и себя не ждёт.
З-3: xray не применил (lock_timeout, deferred) — метка стоит; строки метки — в сбросе соединений gw apply и сборщика.
П-1: сброс файлов при смене роли — под блокировкой состояния."""

import contextlib
import fcntl
import os
import threading
import time
from unittest import mock

from kaskad import paths, state
from kaskad.gw import collect, exits, ops, xray
from tests.fakes_gw import VLESS_A, VLESS_B, write_pool
from tests.gw.test_s1_q4_r3 import HeldLegsCoreTest, write_hold
from tests.gw.test_s1_ru_pool_ops import HOME, RuCase
from tests.test_gw_ops import CliGwCase


@contextlib.contextmanager
def pool_lock_held():
    """install.sh egress_pool_locked at work: another open file holds the lock of the pool."""
    p = paths.P("/run/kaskad/egress-pool.lock")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        yield
    finally:
        os.close(fd)


def legs_lock_fd():
    p = exits._legs_lock()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


class _Held(CliGwCase):
    world = HeldLegsCoreTest.world

    def pool(self):
        return exits.pool(self.w.runner, roles=exits.ROLES)


def age_hold(s=None):
    """The mark written ``s`` seconds ago (HOLD_ORPHAN_S + 1 by default) — by the clock the core reads."""
    p = os.path.join(paths.egress_dir(), "awg", ".ru-hold")
    t = time.time() - (exits.HOLD_ORPHAN_S + 1 if s is None else s)
    os.utime(p, (t, t))


class OrphanHoldTest(_Held):
    """З-1: the mark of a file never written (the write cut off by kill -9 after the mark, before the file)."""

    def test_pool_free_gw_apply_releases_by_the_file_there_is(self):
        self.world()
        write_hold("0123456789abcdef", "kawg201")
        age_hold()
        self.ok("gw", "apply")
        self.assertIsNone(exits.ru_hold())
        self.assertIn("kawg201", self.w.ip.links)
        self.assertIn("cut off before its file was written", self.log())

    def test_pool_busy_waits_then_the_collector_releases(self):
        self.world()
        write_hold("0123456789abcdef", "kawg201")
        with pool_lock_held():                                  # the writer is inside: the file may still come
            self.ok("gw", "apply")
            collect.tick(self.w.runner)
            self.assertEqual(exits.ru_hold()["hash"], "0123456789abcdef")
            self.assertNotIn("kawg201", self.w.ip.links)
        self.now += 400                                         # past HOLD_RETRY_S
        age_hold()
        collect.MEM.hold_try = None
        collect.tick(self.w.runner)
        self.assertIsNone(exits.ru_hold())
        self.assertIn("kawg201", self.w.ip.links)

    def test_a_young_mark_of_another_file_stays_though_the_pool_is_free(self):
        """SEC-s1-q2 П-2: a writer without flock (no util-linux, no /run/kaskad) holds no lock of the pool — its mark
        written a moment ago is a live write, never a cut-off one: it goes only HOLD_ORPHAN_S old."""
        self.world()
        rows = self.pool()
        write_hold("0123456789abcdef", "kawg201")
        age_hold(exits.HOLD_ORPHAN_S - 5)
        self.assertFalse(exits.ru_hold_release(rows, "test"))
        self.assertIsNotNone(exits.ru_hold())
        age_hold(-(exits.HOLD_ORPHAN_S + 60))                   # a clock set back: «from the future» is young
        self.assertFalse(exits.ru_hold_release(rows, "test"))
        age_hold()
        self.assertTrue(exits.ru_hold_release(rows, "test"))
        self.assertIsNone(exits.ru_hold())

    def test_orphan_needs_the_lines_it_was_applied_by(self):
        """Another fingerprint and the pool free, but the file is not what the table was applied by — the mark stays."""
        self.world()
        rows = self.pool()
        from tests.gw.test_pool_ru import write_ru
        write_ru([self.ru_awg, VLESS_B])
        write_hold("0123456789abcdef", "kawg201")
        self.assertFalse(exits.ru_hold_release(rows, "test"))
        self.assertIsNotNone(exits.ru_hold())


class ReleaseUnderLegsLockTest(_Held):
    """З-2: install.sh rewrites the mark (a new change) while the core checks the old one — the new mark survives."""

    def test_a_mark_written_meanwhile_is_never_unlinked(self):
        self.world()
        pool = self.pool()
        hp = os.path.join(paths.egress_dir(), "awg", ".ru-hold")
        started, wrote = threading.Event(), threading.Event()

        def bash_writes_a_new_mark():                           # egress_ru_apply_now: flock -w 30 legs.lock, the mark
            started.wait(5)
            fd = legs_lock_fd()
            try:
                with open(hp + ".new", "w") as f:
                    f.write("deadbeefdeadbeef\nkawg201\nkawg202\n")
                os.replace(hp + ".new", hp)
                wrote.set()
            finally:
                os.close(fd)
        th = threading.Thread(target=bash_writes_a_new_mark)
        th.start()
        real = exits.pool_lines

        def checking(role="abroad"):
            out = real(role)
            if role == "ru" and not started.is_set():
                started.set()
                wrote.wait(0.5)                                 # the writer's chance to cut in between check and unlink
            return out
        try:
            with mock.patch.object(exits, "pool_lines", side_effect=checking):
                self.assertTrue(exits.ru_hold_release(pool, "collector"))
        finally:
            th.join(10)
        self.assertEqual(exits.ru_hold()["hash"], "deadbeefdeadbeef")      # the newer mark is still there

    def test_busy_legs_lock_no_release(self):
        self.world()
        fd = legs_lock_fd()                                     # the watchdog or install.sh holds it
        try:
            self.assertFalse(exits.ru_hold_release(self.pool(), "collector", wait=0.3))
        finally:
            os.close(fd)
        self.assertIsNotNone(exits.ru_hold())
        self.assertTrue(exits.ru_hold_release(self.pool(), "collector", wait=0.3))

    def test_gw_apply_under_bash_takes_its_lock(self):
        """install.sh runs gw apply under its legs.lock and hands it down: released at once, no wait for ourselves."""
        self.world()
        fd = legs_lock_fd()
        try:
            with mock.patch.dict(os.environ, {exits.LEGS_LOCK_FD: str(fd)}):
                t0 = time.monotonic()
                self.assertTrue(exits.ru_hold_release(self.pool(), "gw apply", wait=3))
                self.assertLess(time.monotonic() - t0, 1)
        finally:
            os.close(fd)

    def test_a_descriptor_of_another_file_is_no_lock(self):
        self.world()
        other = os.open(self.path("/run/kaskad/other.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        fd = legs_lock_fd()
        try:
            for raw in (str(other), "x", "999"):
                with self.subTest(raw=raw), mock.patch.dict(os.environ, {exits.LEGS_LOCK_FD: raw}):
                    self.assertFalse(exits.ru_hold_release(self.pool(), "gw apply", wait=0.3))
        finally:
            os.close(fd)
            os.close(other)
        self.assertIsNotNone(exits.ru_hold())


class XrayNotAppliedTest(_Held):
    """З-3 (порчи P3, P4): xray of the gateway still on the old config — the legs stay closed."""

    def test_applied_ok_refuses_every_flag(self):
        for k in ("error", "lock_timeout", "stale", "deferred", "xports_missing", "restart_owed", "ru_open"):
            with self.subTest(k=k):
                self.assertFalse(xray.applied_ok({k: True}))
        self.assertTrue(xray.applied_ok({}))
        self.assertFalse(xray.applied_ok(None))

    def test_gw_apply_lock_timeout_or_deferred_keeps_the_mark(self):
        for res in ({"lock_timeout": True}, {"deferred": True}):
            with self.subTest(res=res):
                self.world()
                with mock.patch.object(xray, "apply_now", return_value=(res, None)):
                    self.ok("gw", "apply")
                self.assertIsNotNone(exits.ru_hold())
                self.assertNotIn("kawg201", self.w.ip.links)

    def test_collector_deferred_keeps_the_mark(self):
        self.world()
        self.w.xray.fail = "Failed to build outbound config\n"
        self.ok("gw", "apply")                                  # the mark stays, the snapshot is the new one
        self.w.xray.fail = None
        self.now += 400
        collect.MEM.hold_try = None
        with mock.patch.object(collect, "_apply_xray", return_value={"deferred": True}):
            res = collect.tick(self.w.runner)
        self.assertTrue(res["exits"].get("nft_ok"))
        self.assertIsNotNone(exits.ru_hold())
        self.assertNotIn("kawg201", self.w.ip.links)


class HeldLinesFlushedTest(_Held):
    """З-3 (порчи P5, P7): the snapshot is already the new one (an earlier apply wrote it, its xray failed) — the old
    connections under the marks of the held lines are forgotten only by the lines of the mark."""

    def held_after_a_failed_apply(self):
        self.world()
        self.w.xray.fail = "Failed to build outbound config\n"
        self.ok("gw", "apply")
        self.assertIsNotNone(exits.ru_hold())

    def test_gw_apply_forgets_the_held_lines(self):
        self.held_after_a_failed_apply()
        with mock.patch.object(ops, "forget_renumbered", return_value=0) as fr:
            self.ok("gw", "apply")
        self.assertTrue(fr.called)
        self.assertIn(201, fr.call_args[0][1])

    def test_the_collector_forgets_the_held_lines(self):
        self.held_after_a_failed_apply()
        self.now += 400
        collect.MEM.hold_try = None
        with mock.patch.object(collect, "_forget_renumbered", return_value=0) as fr:
            res = collect.tick(self.w.runner)
        self.assertFalse(res["exits"].get("pool_changed"))
        self.assertTrue(fr.called)
        self.assertIn(201, fr.call_args[0][1])


class RoleResetUnderStateLockTest(RuCase):
    """REV-s1-q4-3 П-1: the files of the reset of a role change (the snapshot, pins.json) are written under the state
    lock — the collector, that reads and writes them in one tick under it, never puts the old record back."""

    def test_reset_files_under_the_state_lock(self):
        write_pool([VLESS_A, VLESS_B])
        eid = exits.exit_id(VLESS_A)
        seen, real = [], ops.role_reset_files

        def spy(e):
            seen.append(state.held())
            return real(e)
        with mock.patch.object(ops, "role_reset_files", side_effect=spy):
            res = self.move(eid, HOME)
        self.assertEqual(seen, [True])
        self.assertIn("role", res)

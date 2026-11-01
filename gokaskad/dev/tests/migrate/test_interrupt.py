"""SIGHUP / SIGTERM in the middle of ``migrate apply`` and ``migrate rollback`` (critic #1b): the
signal fails the running step, the automatic rollback runs to the end (a second signal does not
cut it), and the previous handlers are back afterwards.

The test process installs its own no-op handlers first: if the guard did not work, the signal
would only be recorded, not end the test run.
"""

import os
import signal
from unittest import mock

from kaskad import errors, state
from kaskad.errors import KaskadError
from kaskad.migrate import apply as apply_mod
from kaskad.migrate import interrupt
from kaskad.migrate import rollback
from tests.helpers import RootCase
from tests.migrate.env import Env


def send(sig):
    os.kill(os.getpid(), sig)


class SignalCase(RootCase):
    def setUp(self):
        super().setUp()
        self.caught = []

        def mine(signum, frame):
            self.caught.append(signum)
        self.mine = mine
        self.old = dict((s, signal.signal(s, mine)) for s in (signal.SIGHUP, signal.SIGTERM))

    def tearDown(self):
        for s, h in self.old.items():
            signal.signal(s, h)
        super().tearDown()

    def assert_handlers_back(self):
        self.assertIs(signal.getsignal(signal.SIGHUP), self.mine)
        self.assertIs(signal.getsignal(signal.SIGTERM), self.mine)
        self.assertEqual(self.caught, [])                 # the guard got them, not the fallback
        self.assertIsNone(interrupt.fired())

    def once(self, fn):
        """Injection for FakeIptables that acts on the first matching call only."""
        done = []

        def inj(argv, inp):
            if done:
                return None
            res = fn(argv, inp)
            if res is not None:
                done.append(1)
            return res
        return inj


class ApplySignalTest(SignalCase):
    def env(self):
        return Env(self, "pro_213", services={"kaskad-bot.service": ("enabled", "active")}).start()

    def test_sigterm_inside_a_step_rolls_back(self):
        env = self.env()
        before = env.snapshot_state()
        env.nft.after_apply = lambda: send(signal.SIGTERM)
        with self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.ROLLED_BACK)
        self.assertIn("Переезд прервался: получен сигнал SIGTERM", str(cm.exception))
        self.assertIn("Вернул как было", str(cm.exception))
        self.assertEqual(env.snapshot_state(), before)
        st = state.load()
        self.assertEqual((st["engine"], st["migrations"][-1]["result"]), ("legacy", "rolled_back"))
        self.assert_handlers_back()

    def test_sighup_after_iptables_restore_committed_restores_that_table(self):
        env = self.env()
        before = env.snapshot_state()

        def commit_then_hangup(argv, inp):
            if argv[0] == "iptables-restore" and "--noflush" in argv and inp.startswith("*filter"):
                rc, err = env.ipt.restore(inp, noflush=True)           # the transaction went through...
                self.assertEqual(rc, 0, err)
                send(signal.SIGHUP)                                    # ...and the SSH session dropped
            return None
        env.ipt.inject(self.once(commit_then_hangup))
        with self.assertRaises(KaskadError) as cm:
            apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.ROLLED_BACK)
        self.assertIn("получен сигнал SIGHUP", str(cm.exception))
        self.assertEqual(sorted(e[1] for e in env.restores("full")), ["filter", "nat"])
        self.assertEqual(env.snapshot_state(), before)                  # nothing lost
        self.assertIsNone(env.nft.table)
        self.assertFalse(env.units.installed)
        self.assert_handlers_back()

    def test_second_signal_does_not_cut_the_rollback(self):
        env = self.env()
        before = env.snapshot_state()
        env.nft.after_apply = lambda: send(signal.SIGTERM)
        real_delete = env.nft.delete_table

        def delete_and_get_killed_again(runner):
            send(signal.SIGHUP)
            send(signal.SIGTERM)
            return real_delete(runner)
        with mock.patch("kaskad.nft.delete_table", delete_and_get_killed_again):
            with self.assertRaises(KaskadError) as cm:
                apply_mod.run(env.runner)
        self.assertEqual(cm.exception.code, errors.ROLLED_BACK)
        self.assertEqual(env.snapshot_state(), before)
        self.assertEqual(state.load()["migrations"][-1]["result"], "rolled_back")
        self.assert_handlers_back()

    def test_signal_after_the_verified_switch_is_finished(self):
        env = self.env()
        real_activate = env.units.activate

        def activate(runner):
            send(signal.SIGHUP)                      # menu closed while kaskad-net.service starts
            return real_activate(runner)
        with mock.patch("kaskad.units.activate", activate):
            res = apply_mod.run(env.runner)
        self.assertEqual(res["result"], "ok")
        self.assertIn(("units.activate",), env.events)
        self.assertEqual(state.load()["engine"], "nft")
        self.assert_handlers_back()

    def test_handlers_back_after_a_normal_run(self):
        env = self.env()
        apply_mod.run(env.runner)
        self.assert_handlers_back()


class RollbackSignalTest(SignalCase):
    def migrated(self):
        env = Env(self, "pro_213").start()
        apply_mod.run(env.runner)
        self.assertEqual(state.load()["engine"], "nft")
        return env

    def test_signal_while_writing_the_rules_puts_iptables_back(self):
        env = self.migrated()
        ipt_before = env.ipt.state()

        def commit_then_hangup(argv, inp):
            if argv[0] == "iptables-restore" and "--noflush" in argv and inp.startswith("*filter"):
                rc, err = env.ipt.restore(inp, noflush=True)
                self.assertEqual(rc, 0, err)
                send(signal.SIGHUP)
            return None
        env.ipt.inject(self.once(commit_then_hangup))
        with self.assertRaises(KaskadError) as cm:
            rollback.run(env.runner)
        self.assertIn("Вернуть iptables не вышло: получен сигнал SIGHUP", str(cm.exception))
        self.assertEqual(env.ipt.state(), ipt_before)                 # nat and filter back to the snapshot
        self.assertIsNotNone(env.nft.table)
        self.assertEqual(state.load()["engine"], "nft")
        self.assert_handlers_back()

    def test_ctrl_c_while_writing_the_rules_puts_iptables_back(self):
        env = self.migrated()
        ipt_before = env.ipt.state()

        def interrupted(argv, inp):
            if argv[0] == "iptables-restore" and "--noflush" in argv and inp.startswith("*filter"):
                raise KeyboardInterrupt()
            return None
        env.ipt.inject(self.once(interrupted))
        with self.assertRaises(KaskadError):
            rollback.run(env.runner)
        self.assertEqual(env.ipt.state(), ipt_before)
        self.assertEqual(state.load()["engine"], "nft")

    def test_signal_after_the_rules_are_written_is_finished(self):
        env = self.migrated()
        real_delete = env.nft.delete_table

        def delete(runner):
            send(signal.SIGTERM)
            return real_delete(runner)
        with mock.patch("kaskad.nft.delete_table", delete):
            res = rollback.run(env.runner)
        self.assertEqual(res["result"], "ok")
        self.assertIsNone(env.nft.table)
        self.assertFalse(env.units.installed)
        self.assertEqual(state.load()["engine"], "legacy")
        self.assert_handlers_back()

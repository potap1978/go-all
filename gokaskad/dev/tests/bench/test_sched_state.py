"""B10: the schedule in state.json — the check at a read (``model.bench`` pure, ``bench.fix_state`` / ``fix_in``)
and the binding to the machine (``bench.machine``; SPEC-node-score-schedule-share §2.1, NS-S4, §11.1 п. 9, 19, 28,
31)."""

import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import unittest
from unittest import mock

from kaskad import bench, paths, state, util
from kaskad.bench import machine
from kaskad.gw import model
from tests.helpers import RootCase

HEX = "0123456789abcdef0123456789abcdef"
OTHER = "f" * 32


def ent(t, days, mode="quick", **kw):
    e = {"time": t, "mode": mode, "days": list(days)}
    e.update(kw)
    return e


class MachineIdTest(RootCase):

    def put(self, data):
        p = self.path("/etc/machine-id")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)

    def test_the_usual_file_with_a_newline(self):
        """machine-id(5): one line WITH a newline at the end — the norm; the HMAC is of the 32 hex without it."""
        self.put((HEX + "\n").encode())
        self.assertEqual(machine.machine_id(), HEX)
        want = hmac.new(HEX.encode(), b"kaskad-bench", hashlib.sha256).hexdigest()[:32]
        self.assertEqual(machine.mid(), want)
        self.put(HEX.encode())
        self.assertEqual(machine.mid(), want)

    def test_no_id(self):
        self.assertIsNone(machine.mid())                        # no file
        for data in (b"", b"uninitialized\n", (HEX + "\n\n").encode(), (HEX[:31] + "\n").encode(),
                     (HEX.upper() + "\n").encode(), (" " + HEX + "\n").encode(), b"\xff" * 33, b"x" * 5000):
            self.put(data)
            self.assertIsNone(machine.mid(), data[:40])

    def test_raw_id_never_in_the_state(self):
        self.put((HEX + "\n").encode())
        st = state.empty()
        machine.mark(st, "first_manual_at", 100, 100)
        machine.mark(st, "pkgs", "yes", 101)
        text = json.dumps(st)
        self.assertNotIn(HEX, text)
        b = st["gateway"]["hostmon"]["bench"]
        self.assertRegex(b["mid"], r"\A[0-9a-f]{32}\Z")
        self.assertEqual(b["armed_at"], 101)


class ArmedTest(unittest.TestCase):
    HERE = machine.mid_of(HEX)

    def st(self, **b):
        return {"gateway": {"hostmon": {"bench": b}}}

    def why(self, st, here=None):
        return model.bench(st, here=self.HERE if here is None else here)["armed_why"]

    def test_reasons(self):
        self.assertEqual(self.why(self.st()), "no_manual")
        self.assertEqual(self.why(self.st(mid=self.HERE, first_manual_at=5)), "no_pkgs")
        self.assertEqual(self.why(self.st(mid=self.HERE, first_manual_at=5, pkgs="no")), "no_pkgs")
        self.assertEqual(self.why(self.st(mid=machine.mid_of(OTHER), first_manual_at=5, pkgs="yes")), "other_machine")
        self.assertEqual(self.why(self.st(first_manual_at=5, pkgs="yes")), "other_machine")
        self.assertEqual(self.why(self.st(mid=self.HERE, first_manual_at=5, pkgs="yes"), here=""), "no_machine_id")
        m = model.bench(self.st(mid=self.HERE, first_manual_at=5, pkgs="yes", armed_at=6), here=self.HERE)
        self.assertTrue(m["armed"])
        self.assertIsNone(m["armed_why"])
        self.assertEqual((m["first_manual_at"], m["pkgs"], m["armed_at"]), (5, "yes", 6))

    def test_other_machine_marks_count_as_none(self):
        m = model.bench(self.st(mid=machine.mid_of(OTHER), first_manual_at=5, pkgs="yes", armed_at=6),
                        here=self.HERE)
        self.assertEqual((m["first_manual_at"], m["pkgs"], m["armed_at"], m["armed"]), (None, None, None, False))

    def test_a_manual_check_here_does_not_revive_the_consent_of_another(self):
        """§2.1 «Правило записи при смене машины»: another ``mid``, ``pkgs: "yes"``, a manual check here ->
        ``pkgs: null``, ``armed_why: no_pkgs`` (not armed)."""
        st = self.st(mid=machine.mid_of(OTHER), first_manual_at=5, pkgs="yes", armed_at=6)
        self.assertIsNone(machine.mark(st, "first_manual_at", 50, 50, here=self.HERE))
        b = st["gateway"]["hostmon"]["bench"]
        self.assertEqual((b["mid"], b["first_manual_at"], b["pkgs"], b["armed_at"]), (self.HERE, 50, None, None))
        m = model.bench(st, here=self.HERE)
        self.assertEqual((m["armed"], m["armed_why"], m["pkgs"]), (False, "no_pkgs", None))
        machine.mark(st, "pkgs", "yes", 60, here=self.HERE)
        m = model.bench(st, here=self.HERE)
        self.assertEqual((m["armed"], m["armed_at"]), (True, 60))
        machine.mark(st, "pkgs", "no", 70, here=self.HERE)
        self.assertEqual((model.bench(st, here=self.HERE)["armed"], b["armed_at"]), (False, None))

    def test_no_machine_id_no_mark(self):
        st = self.st()
        self.assertEqual(machine.mark(st, "first_manual_at", 5, 5, here=None), "no_machine_id")
        self.assertEqual(st, self.st())
        self.assertEqual(model.bench(st, here=None)["warn"], ["no_machine_id"])

    def test_bad_marks(self):
        with self.assertRaises(ValueError):
            machine.mark(self.st(), "armed_at", 5, 5, here=self.HERE)
        with self.assertRaises(ValueError):
            machine.mark(self.st(), "pkgs", "maybe", 5, here=self.HERE)
        m = model.bench(self.st(mid=self.HERE, first_manual_at="5", pkgs="YES"), here=self.HERE)
        self.assertEqual((m["first_manual_at"], m["pkgs"], m["armed_why"]), (None, None, "no_manual"))


class ModelBenchTest(unittest.TestCase):

    def test_defaults(self):
        m = model.bench({}, here="x" * 32)
        self.assertEqual(m["schedule"], [])
        self.assertEqual(m["fix"], [])
        self.assertTrue(m["retry"])
        self.assertFalse(m["armed"])
        self.assertFalse(model.bench({"gateway": {"hostmon": {"bench": {"retry": False}}}}, here="")["retry"])
        for junk in ({"gateway": "x"}, {"gateway": {"hostmon": []}}, {"gateway": {"hostmon": {"bench": 5}}}):
            self.assertEqual(model.bench(junk, here="")["schedule"], [])

    def test_pure_no_lock_no_write_no_journal(self):
        st = {"gateway": {"hostmon": {"bench": {"schedule": [ent("03:45\nExecStart=x", [1], id="aaaaaa")]}}}}
        before = json.dumps(st, sort_keys=True)
        with mock.patch.object(state, "locked", side_effect=AssertionError("lock taken")), \
                mock.patch.object(state, "save", side_effect=AssertionError("saved")), \
                mock.patch.object(util, "log_action", side_effect=AssertionError("journal")):
            m = model.bench(st, here="")
        self.assertEqual(json.dumps(st, sort_keys=True), before)
        self.assertEqual((m["schedule"][0]["on"], m["schedule"][0]["off_why"]), (False, "time"))
        self.assertEqual(m["fix"], [{"op": "set", "i": 0, "fields": {"on": False, "off_why": "time"}}])


class FixStateTest(RootCase):
    """``bench.fix_state``: a transaction of its own, re-read under the lock, one line of the journal per save."""

    BAD = ent("03:45\nExecStart=/bin/sh", [1], id="aaaaaa", on=True)
    GOOD = ent("09:00", [1], id="bbbbbb", on=True)

    def setUp(self):
        super().setUp()
        util.ensure_dir(paths.etc(), 0o700)
        st = state.empty()
        st["gateway"] = {"hostmon": {"bench": {"schedule": [dict(self.BAD), dict(self.GOOD)]}}}
        state.save(st)
        self.lines = []
        p = mock.patch.object(util, "log_action", side_effect=self.lines.append)
        p.start()
        self.addCleanup(p.stop)

    def saved(self):
        return state.load()["gateway"]["hostmon"]["bench"]["schedule"]

    def test_bad_entry_saved_off_one_line_for_ten_reads(self):
        for _ in range(10):
            m = bench.fix_state(here="")
            self.assertEqual((m["schedule"][0]["on"], m["schedule"][0]["off_why"]), (False, "time"))
        self.assertEqual(len(self.lines), 1, self.lines)
        sch = self.saved()
        self.assertEqual((sch[0]["on"], sch[0]["off_why"]), (False, "time"))
        self.assertEqual(sch[0]["time"], self.BAD["time"], "only on/off_why change: the rest of the entry stays")
        self.assertEqual(sch[1], self.GOOD)

    def test_re_read_under_the_lock(self):
        """Another edit made between a read and the save is not overwritten: the state is read under the lock."""
        real = state.locked

        def edit_then_lock(timeout=15.0):
            st = state.load()
            st["gateway"]["hostmon"]["enabled"] = False      # «another window» saves before the lock is ours
            state.save(st)
            return real(timeout)

        with mock.patch.object(state, "locked", side_effect=edit_then_lock):
            m = bench.fix_state(here="")
        self.assertTrue(m["saved"])
        st = state.load()
        self.assertIs(st["gateway"]["hostmon"]["enabled"], False)
        self.assertFalse(st["gateway"]["hostmon"]["bench"]["schedule"][0]["on"])

    def test_lock_busy_answer_without_saving(self):
        """§11.1 п. 31: the lock held by another process -> the answer in ≤ 1 s with the entry off, the file
        unchanged, no 500."""
        before = util.read_text(paths.state())
        util.ensure_run_dir(os.path.dirname(paths.lock()))
        holder = subprocess.Popen(
            [sys.executable, "-c", "import fcntl,os,sys,time; fd=os.open(sys.argv[1], os.O_RDWR|os.O_CREAT, 0o600);"
             "fcntl.flock(fd, fcntl.LOCK_EX); print('held', flush=True); time.sleep(30)", paths.lock()],
            stdout=subprocess.PIPE)
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), b"held")
        t = time.time()
        m = bench.fix_state(timeout=0.5, here="")
        self.assertLessEqual(time.time() - t, 1.0)
        self.assertFalse(m["saved"])
        self.assertEqual((m["schedule"][0]["on"], m["schedule"][0]["off_why"]), (False, "time"))
        self.assertEqual(util.read_text(paths.state()), before)
        self.assertEqual(self.lines, [])

    def test_nested_inside_a_transaction_is_no_error(self):
        """PUT of the schedule and the restore of a .kbk run inside ``state.transaction``: ``fix_in`` changes the
        state they save (no NestedLockError); ``fix_state`` there answers unsaved instead of raising."""
        with state.transaction() as st:
            m = bench.fix_in(st, here="")
            self.assertFalse(m["schedule"][0]["on"])
            self.assertFalse(bench.fix_state(here="")["saved"])
            self.assertEqual(self.lines, [], "the line of the journal — after the save of the transaction")
        bench.log_fixed(m)
        sch = self.saved()
        self.assertEqual((sch[0]["on"], sch[0]["off_why"]), (False, "time"))
        self.assertEqual(len(self.lines), 1)
        self.assertFalse(bench.fix_state(here="")["saved"], "the next read finds nothing to fix")
        self.assertEqual(len(self.lines), 1)

    def test_restore_of_a_kbk_with_a_broken_entry(self):
        """A .kbk carries ``gateway`` whole (backup.py): its broken entry is off after the restore's own save."""
        kbk_gateway = {"hostmon": {"bench": {"schedule": [ent("25:00", [1], id="cccccc"), "junk",
                                                          ent("03:00", [1, 1], id="dddddd")],
                                             "mid": machine.mid_of(OTHER), "pkgs": "yes",
                                             "first_manual_at": 5}}}
        with state.transaction() as st:
            st["gateway"] = kbk_gateway
            bench.fix_in(st, here=machine.mid_of(HEX))
        b = state.load()["gateway"]["hostmon"]["bench"]
        self.assertEqual([(e["id"], e["on"], e["off_why"]) for e in b["schedule"]],
                         [("cccccc", False, "time"), ("dddddd", False, "days")])
        m = model.bench(state.load(), here=machine.mid_of(HEX))
        self.assertEqual((m["armed"], m["armed_why"]), (False, "other_machine"))


if __name__ == "__main__":
    unittest.main()

    def test_no_line_when_the_save_fails(self):
        """REV-ns-b10 П-2: «fixed» is said after the save: a failed save says nothing (the next read fixes again)."""
        with mock.patch.object(state, "save", side_effect=OSError("disk full")):
            m = bench.fix_state(here="")
        self.assertFalse(m["saved"])
        self.assertEqual(self.lines, [])
        self.assertTrue(bench.fix_state(here="")["saved"])
        self.assertEqual(len(self.lines), 1)

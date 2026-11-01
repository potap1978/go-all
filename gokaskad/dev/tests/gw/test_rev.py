"""The version of the exit policy — kaskad.gw.rev (SPEC-stage1-exits §3.1 «Кто поднимает rev», «Счёт — два входа»;
stage 1, T1) and the guard against a nested state lock (state.locked -> NestedLockError, проверки 18–20)."""

import copy
import os
import subprocess
import sys
import threading
import time
import unittest

from kaskad import paths, state
from kaskad.errors import ERROR, LOCKED, LockedError, NestedLockError
from kaskad.gw import exits, hooks, rev
from tests.fakes_nft import make_state
from tests.helpers import RootCase

A = "vless://11111111-2222-3333-4444-555555555555@198.51.100.20:443?type=tcp&security=reality#Vienna"
B = "vless://66666666-7777-8888-9999-000000000000@198.51.100.21:8443?type=ws&security=tls#Helsinki"
RU = "vless://aaaaaaaa-2222-3333-4444-555555555555@203.0.113.7:443?type=tcp&security=reality#Moscow"

POLICY = {"version": 1, "hint_seen": False, "new_exits_group": "main", "tg_proxy_group": "main",
          "probes": {"freq": "often", "speed": {"url": "default", "budget_mb": 100}},
          "groups": [{"id": "main", "name": "", "role": "abroad", "preset": "compat", "fail": "block",
                      "tiers": [{"id": "t1", "name": "", "exits": [], "method": "even"}]}]}


class RevCase(RootCase):
    def setUp(self):
        super().setUp()
        st = make_state([])
        st["gateway"] = {"exits": {}, "routes": {"default": {"via": "pool", "fail": "block"}}}
        state.save(st)

    def pool(self, *lines, ru=None):
        for p, ls in ((paths.egress_up(), lines), (rev.ru_pool_path(), ru)):
            if ls is not None:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write("".join(l + "\n" for l in ls))

    def st(self):
        return state.load()


class FingerprintTest(RevCase):
    """What the forms change moves it; names, hint_seen, probes.speed, country and the hint group do not (§3.1)."""

    def base(self):
        st = self.st()
        st["gateway"]["exit_policy"] = copy.deepcopy(POLICY)
        st["gateway"]["exits"] = {exits.exit_id(A): {"enabled": True, "name": "Вена"}}
        return st

    def test_outside_the_fingerprint(self):
        self.pool(A, B)
        st = self.base()
        sig = rev.fingerprint(st)
        changes = [
            lambda s: s["gateway"]["exit_policy"].update(hint_seen=True),
            lambda s: s["gateway"]["exit_policy"]["groups"][0].update(name="Своя"),
            lambda s: s["gateway"]["exit_policy"]["groups"][0]["tiers"][0].update(name="Быстрые"),
            lambda s: s["gateway"]["exit_policy"]["probes"]["speed"].update(budget_mb=500),
            lambda s: s["gateway"]["exits"][exits.exit_id(A)].update(name="Другое имя"),
            lambda s: s["gateway"]["exits"][exits.exit_id(A)].update(group="g-abcdef"),
            lambda s: s["gateway"]["exits"][exits.exit_id(A)].update(country={"expected": "AT", "notified": 1}),
            lambda s: s["gateway"]["exits"].update({exits.exit_id(B): {"name": "только имя"}}),
            lambda s: s["gateway"]["exits"].update({"e-0000000001": {"enabled": True}}),   # = the default
            lambda s: s["gateway"]["routes"].update(default={"via": "direct", "fail": "block"}),
            lambda s: s["gateway"].update(users=[{"id": "u-1", "name": "Мама", "route": {"via": "direct"}}]),
        ]
        for i, change in enumerate(changes):
            s2 = copy.deepcopy(st)
            change(s2)
            self.assertEqual(rev.fingerprint(s2), sig, "change %d moved the fingerprint" % i)

    def test_inside_the_fingerprint(self):
        self.pool(A, B)
        st = self.base()
        sig = rev.fingerprint(st)
        eid = exits.exit_id(A)
        changes = [
            lambda s: s["gateway"]["exits"][eid].update(enabled=False),
            lambda s: s["gateway"]["exits"][eid].update(quota={"gb": 10}),
            lambda s: s["gateway"]["exits"][eid].update(manual="standby", manual_why="ремонт"),
            lambda s: s["gateway"]["exits"][eid].update(speed_skip=True),
            lambda s: s["gateway"]["exit_policy"].update(tg_proxy_group="g-abcdef"),
            lambda s: s["gateway"]["exit_policy"]["groups"][0].update(preset="standard"),
            lambda s: s["gateway"]["exit_policy"]["groups"][0]["tiers"][0]["exits"].append(eid),
            lambda s: s["gateway"]["exit_policy"]["probes"].update(freq="rare"),
        ]
        for i, change in enumerate(changes):
            s2 = copy.deepcopy(st)
            change(s2)
            self.assertNotEqual(rev.fingerprint(s2), sig, "change %d did not move the fingerprint" % i)

    def test_defaults_written_explicitly_are_no_change(self):
        """REV-s1-t1 З-3: every field of exit_pref with a default (§3.1) written equal to it counts as absent — a write
        «no change» with the fields spelled out never raises the rev (another open form would get a false 409)."""
        self.pool(A, B)
        st = self.base()
        sig = rev.fingerprint(st)
        eid = exits.exit_id(A)
        written = [{"manual": None}, {"manual": False}, {"manual": ""}, {"manual_why": None}, {"manual_why": ""},
                   {"speed_skip": False}, {"speed_skip": None}, {"quota": None}, {"quota": {}}, {"enabled": True},
                   {"enabled": True, "manual": None, "manual_why": None, "speed_skip": False, "quota": None}]
        for i, rec in enumerate(written):
            s2 = copy.deepcopy(st)
            s2["gateway"]["exits"][eid].update(rec)
            self.assertEqual(rev.fingerprint(s2), sig, "defaults %d moved the fingerprint: %r" % (i, rec))
            s3 = copy.deepcopy(st)
            s3["gateway"]["exits"][exits.exit_id(B)] = dict(rec, name="B")     # a new record of the defaults only
            self.assertEqual(rev.fingerprint(s3), sig, "a record of defaults %d moved it: %r" % (i, rec))
        st["gateway"]["policy_rev"], st["gateway"]["policy_sig"] = 4, sig
        st["gateway"]["exits"][eid].update(manual=None, manual_why=None, speed_skip=False, quota=None)
        self.assertEqual(rev.note(st), 4)                    # the rev stays

    def test_both_pool_files_by_id_without_names(self):
        self.pool(A)
        st = self.base()
        sig = rev.fingerprint(st)
        self.pool(A.replace("#Vienna", "#Wien"))            # another name of the same line: another id — another pool
        self.assertNotEqual(rev.fingerprint(st), sig)
        self.pool(A, B)
        sig2 = rev.fingerprint(st)
        self.pool(B, A)                                      # the order of the lines: the marks 0x300+N move
        self.assertNotEqual(rev.fingerprint(st), sig2)
        self.pool(A, B, ru=[RU])                             # upstream-ru.txt (T15) counts too
        self.assertNotEqual(rev.fingerprint(st), sig2)
        self.assertEqual(rev.pool_ids(), {"abroad": [exits.exit_id(A), exits.exit_id(B)], "ru": [exits.exit_id(RU)]})
        self.assertEqual(rev.ru_pool_path(), os.path.join(os.path.dirname(paths.egress_up()), "upstream-ru.txt"))


class NoteCurrentBumpTest(RevCase):
    def test_a_node_of_33x_starts_at_1(self):
        st = self.st()
        self.assertEqual(rev.rev_of(st), 0)
        self.assertEqual(rev.note(st), 1)                    # no fields: the first count gives 1 (придирка 3)
        self.assertEqual(rev.note(st), 1)                    # nothing changed: the same
        st["gateway"]["exits"]["e-1234567890"] = {"enabled": False}
        self.assertEqual(rev.note(st), 2)
        st["gateway"]["exits"]["e-1234567890"] = {"enabled": True}
        self.assertEqual(rev.note(st), 3)                    # set and taken back by Python: + 2 in all

    def test_note_inside_a_transaction_does_not_wait(self):
        t0 = time.monotonic()
        with state.transaction() as st:
            got = rev.note(st)                               # under the lock already taken: no lock of its own
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(got, 1)
        saved = self.st()["gateway"]
        self.assertEqual((saved["policy_rev"], saved["policy_sig"]), (1, rev.fingerprint(self.st())))

    def test_current_the_same_returns_its_st(self):
        with state.transaction() as st:
            rev.note(st)
        st = self.st()
        got, st2 = rev.current(st)
        self.assertEqual(got, 1)
        self.assertIs(st2, st)                               # nothing written: the data given

    def test_current_counts_in_its_own_transaction_and_returns_the_fresh_st(self):
        with state.transaction() as st:
            rev.note(st)
        old = self.st()                                      # a read of the panel
        with state.transaction() as st:                      # another writer: a key and a name meanwhile
            st["gateway"]["exits"]["e-1234567890"] = {"enabled": False, "name": "свежее"}
        self.pool(A)                                         # and the pool by bash (no Python on the way)
        got, fresh = rev.current(old)
        self.assertEqual(got, 2)
        self.assertIsNot(fresh, old)
        self.assertEqual(fresh["gateway"]["exits"]["e-1234567890"]["name"], "свежее")   # the data of THIS read
        self.assertEqual(fresh["gateway"]["policy_rev"], 2)
        self.assertEqual(self.st()["gateway"]["policy_rev"], 2)                         # saved
        self.assertEqual(rev.current(self.st())[0], 2)                                  # and counted once

    def test_current_under_the_lock_is_an_error_at_once(self):
        with state.transaction() as st:
            t0 = time.monotonic()
            with self.assertRaises(LockedError) as cm:      # still a LockedError for old handlers of «busy»
                rev.current(st)
            self.assertLess(time.monotonic() - t0, 1.0)     # no 15 s wait
        self.assertIsInstance(cm.exception, NestedLockError)
        self.assertEqual(cm.exception.code, ERROR)          # an error of the code, not «busy» (проверка 20, З-2)
        self.assertNotEqual(cm.exception.code, LOCKED)

    def test_bump_never_takes_the_copys_rev(self):
        here = self.st()
        here["gateway"]["policy_rev"] = 7
        copy_st = copy.deepcopy(here)
        copy_st["gateway"]["policy_rev"] = 3
        self.assertEqual(rev.bump(copy_st, before=here), 8)  # max(7, 3) + 1
        copy_st["gateway"]["policy_rev"] = 12
        self.assertEqual(rev.bump(copy_st, before=here), 13)
        self.assertEqual(rev.bump(copy.deepcopy(copy_st), before=None), 14)
        self.assertEqual(copy_st["gateway"]["policy_sig"], rev.fingerprint(copy_st))
        self.assertEqual(rev.note(copy_st), 13)              # the restore is counted already


    def test_after_purge_the_count_goes_on_from_the_base(self):
        """REV-s1-t1 З-6: gw purge leaves a random 31-bit base outside the empty gateway; note and bump go on from it."""
        st = self.st()
        st["gateway"] = {}
        base = rev.seed(st)
        self.assertTrue(1 <= base < 2 ** 31 - 1)
        self.assertEqual(st["gateway"], {})
        self.assertEqual(rev.rev_of(st), base)
        bumped = copy.deepcopy(st)
        self.assertEqual(rev.note(st), base + 1)
        self.assertNotIn(rev.BASE_KEY, st)
        copy_st = copy.deepcopy(bumped)                      # a copy with the gateway restored right after the purge
        copy_st["gateway"] = {"policy_rev": 3}
        self.assertEqual(rev.bump(copy_st, before=bumped), base + 1)
        self.assertNotIn(rev.BASE_KEY, copy_st)
        seen = set(rev.seed({}) for _ in range(20))
        self.assertGreater(len(seen), 1)                     # random, not a constant


class NestedLockTest(RevCase):
    def test_nested_lock_at_once_and_its_code(self):
        with state.locked():
            t0 = time.monotonic()
            with self.assertRaises(NestedLockError) as cm:
                with state.locked():
                    pass
            self.assertLess(time.monotonic() - t0, 1.0)
            with self.assertRaises(LockedError):
                with state.transaction():
                    pass
            self.assertTrue(state.held())
        self.assertFalse(state.held())
        self.assertEqual(cm.exception.code, ERROR)
        with state.locked():                                 # released: taken again as always
            pass

    def test_mark_is_per_thread(self):
        out = {}
        with state.locked():
            def other():
                try:
                    with state.locked(timeout=0.3):
                        out["took"] = True
                except LockedError as e:
                    out["err"] = e
            th = threading.Thread(target=other)
            th.start()
            th.join(5)
        self.assertNotIn("took", out)
        self.assertIs(type(out["err"]), LockedError)        # another thread waits as before: «busy», not nested
        self.assertEqual(out["err"].code, LOCKED)

    def test_busy_held_by_another_process(self):
        """«Занято» — the lock held by another PROCESS (not the same thread): LockedError with LOCKED (§11.1)."""
        code = ("import fcntl, os, sys, time\n"
                "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
                "fcntl.flock(fd, fcntl.LOCK_EX)\nprint('ok', flush=True)\ntime.sleep(5)\n")
        lp = paths.lock()
        os.makedirs(os.path.dirname(lp), exist_ok=True)
        p = subprocess.Popen([sys.executable, "-c", code, lp], stdout=subprocess.PIPE)
        try:
            self.assertEqual(p.stdout.readline().strip(), b"ok")
            with self.assertRaises(LockedError) as cm:
                with state.locked(timeout=0.3):
                    pass
            self.assertNotIsInstance(cm.exception, NestedLockError)
            self.assertEqual(cm.exception.code, LOCKED)
        finally:
            p.kill()
            p.wait()
            p.stdout.close()

    def test_hooks_maybe_lock_goes_on_without_it(self):
        with state.locked():
            t0 = time.monotonic()
            with hooks._maybe_state_lock() as got:
                self.assertFalse(got)                        # the same thread holds it: on without it, at once
            self.assertLess(time.monotonic() - t0, 1.0)


if __name__ == "__main__":
    unittest.main()

"""The generation of state.json (SPEC-stage1-exits §1.3, §11.1 «state.py»; T2): a file born on the code of stage 1 —
``gen 2`` (a new node), a file of the code before it — no field, read as ``gen 1`` (an updated node). Nothing writes it
on an update; ``migrate apply``, ``backup.apply`` and ``gw purge`` keep the generation of the node."""

import json
import unittest

from kaskad import paths, state, util
from tests.helpers import RootCase, legacy_state

FILE_333 = {"schema": 1, "engine": "nft", "created": "2026-09-01T00:00:00Z", "updated": "2026-09-02T00:00:00Z",
            "forwards": [], "migrations": [], "gateway": {"enabled": True}}


class GenerationTest(RootCase):
    def test_no_file_is_a_new_node(self):
        st = state.load()
        self.assertEqual(st["gen"], state.GEN)
        self.assertEqual(state.GEN, 2)
        self.assertTrue(state.born_new(st))
        self.assertTrue(state.born_new(state.empty()))

    def test_a_file_of_333_is_generation_1_and_stays_so(self):
        util.atomic_write(paths.state(), json.dumps(FILE_333), 0o600)
        st = state.load()
        self.assertEqual(st["gen"], 1)                        # not 2 of empty(): set before the missing keys
        self.assertFalse(state.born_new(st))
        state.save(st)
        with open(paths.state(), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["gen"], 1)          # written as it is: never «upgraded» by a save
        with state.transaction() as st:
            st["forwards"] = []
        self.assertEqual(state.load()["gen"], 1)

    def test_a_born_new_file_keeps_its_generation(self):
        state.save(state.empty())
        self.assertEqual(state.load()["gen"], 2)

    def test_born_new_of_odd_values(self):
        # only the integer 1 is an updated node; a missing or broken field is a NEW one — the safe side for ⚠РФ
        # (SEC-s1-r2 З1: before, "2"/true/2.0/no key read as «updated» and let a hand-edited ru_direct apply as legacy)
        for v, want in ((1, False), (2, True), (3, True), (True, True), (False, True), ("1", True), ("2", True),
                        (1.0, True), (2.0, True), (0, True), (-1, True), (None, True)):
            self.assertEqual(state.born_new({"gen": v}), want, v)
        self.assertTrue(state.born_new({}))
        self.assertTrue(state.born_new(None))
        self.assertFalse(state.born_new(legacy_state()))


class MigrateGenerationTest(RootCase):
    def test_migrate_apply_on_a_node_without_state_is_generation_2(self):
        from kaskad.migrate import apply as apply_mod
        from tests.migrate.env import Env
        env = Env(self, "empty").start()
        self.assertFalse(state.exists())
        self.assertEqual(apply_mod.run(env.runner)["result"], "ok")
        self.assertEqual(state.load()["gen"], 2)


class KeepsGenerationTest(unittest.TestCase):
    """backup.apply and gw purge keep the generation of the node they run on."""

    def test_backup_apply_and_purge(self):
        from tests import test_backup as tb
        from kaskad import backup
        from kaskad.gw import ops

        class Case(tb.BackupCase):
            def runTest(self):
                self.old_node()
                with state.transaction() as st:
                    st["gen"] = 1                              # an updated node
                text = backup.export(self.w.runner, tb.PW)["text"]
                with state.transaction() as st:
                    st["gen"] = 2                              # the copy goes to a node born on stage 1
                backup.apply(self.w.runner, backup.unseal(self.w.runner, text, tb.PW))
                self.assertEqual(state.load()["gen"], 2)
                with state.transaction() as st:
                    st["gen"] = 1
                backup.apply(self.w.runner, backup.unseal(self.w.runner, text, tb.PW))
                self.assertEqual(state.load()["gen"], 1)
                ops.purge(self.w.runner, users=True)
                st = state.load()
                # purge empties the gateway but the marks ⚠РФ (rf §3.6): «the migration is done» of this gen 1 node
                self.assertEqual(st["gateway"], {"rf_gate": 1})
                self.assertEqual(st["gen"], 1)                 # purge empties the gateway, never the top key

        res = unittest.TestResult()
        Case().run(res)
        self.assertEqual(res.errors + res.failures, [], (res.errors + res.failures)[:1])


if __name__ == "__main__":
    unittest.main()

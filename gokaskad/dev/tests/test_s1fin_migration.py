"""А4 (dev-s1fin = 3.3.7.10 + Н-2 + Н-3/М-1/М-2 + автообновление ≤ 5 с), the migration (the owner's rule 24.10: «старые
данные → новая» и откат в каждом выпуске).

The data are written by the RELEASED 3.3.7.10 itself — the tree of 44cee282 (``git archive``) runs
``tests/s1fin_old_side.py`` in a subprocess, the whole fake root of each world copied out — and read by this version;
then the root this version left is read by 3.3.7.10 again (the rollback):

| Data of the node | 3.3.7.10 → this version | rollback to 3.3.7.10 |
|---|---|---|
| the policy with the repeat «e-…-201» in «Дом» (Н-3: the key in upstream.txt and upstream-ru.txt; 3.3.7.10 refuses every write) | ``PolicyTest``: the view without it, a write goes, the collector takes it out of the stored policy and never puts it back | 3.3.7.10 reads and writes the clean policy; its own collector puts the repeat back while the key stays in both files — its old error, not of the data |
| the snapshot for «Вернуть как было» taken by 3.3.7.10 before an import of another domain (Н-2) | ``SnapshotTest``: the undo of this version brings back the site, the certificate on disk and VLESS on TLS, the same links | 3.3.7.10 reads the node (site, letsencrypt, TLS 443, the links) and both snapshots (its own and the one the undo took) |
| live.json and metrics.db of the collector (automatic refresh ≤ 5 s, 3.3.7.11) | ``LiveTest``: the collector starts on the files of 3.3.7.10 («в сети с» kept, the events kept), the open minute comes with its first point, the history of «Выходы» stays | 3.3.7.10 reads live.json with ``metrics.at/cur`` (its collector, the overview of the panel) and metrics.db |

**One command for the whole merge:** ``python3 -m unittest tests.test_s1fin_migration`` — these cases and, by
``load_tests``, the migration cases of the parts that live with them (PARTS; no copy here) and the seams of
``tests.gw.test_s1fin_seams``. No git or no commit (a tarball) — skipped."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from kaskad import backup, paths, state
from kaskad.gw import collect, exits, live, metrics, policy, policy_ops
from tests.fakes_gw import VLESS_A, VLESS_B
from tests.gw.test_s1_ru_pool_ops import HOME, RuCase
from tests.s1fin_old_side import _LiveWorld, _place, get, load_root, put, repeats, save_root
from tests.test_n2_undo_site import OTHER, _UndoCase
from tests.test_gw_vless_front import DOMAIN

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REV = "44cee282"                                   # 3.3.7.10 = dev = test
RUNS = {"make": 3, "read": 1}                      # the cases of tests/s1fin_old_side.py per mode
COPIED = ("tests/s1fin_old_side.py", "tests/test_n2_undo_site.py")
PARTS = (
    "tests.gw.test_n3_exit_dup.MigrationTest",                       # the stored shapes: a weight, known=None
    "tests.test_users_ui_lines.ExitLineTest.test_the_open_minute_goes_onto_its_answer_only",   # live.json without cur
    "tests.gw.test_s1fin_seams",
)
_TMP = {}


def tearDownModule():
    if _TMP.get("dir"):
        shutil.rmtree(_TMP["dir"], ignore_errors=True)


def _tmp():
    if not _TMP.get("dir"):
        _TMP["dir"] = tempfile.mkdtemp(prefix="k3-s1fin-mig-")
    return _TMP["dir"]


def tree():
    """lib/ and tests/ of 3.3.7.10 with the old side copied in. SkipTest without git or the commit."""
    d = os.path.join(_tmp(), REV)
    if os.path.isdir(d):
        return d
    try:
        ok = subprocess.run(["git", "-C", REPO, "cat-file", "-e", REV + "^{commit}"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise unittest.SkipTest("no git")
    if ok.returncode != 0:
        raise unittest.SkipTest("no commit %s" % REV)
    os.makedirs(d + ".part")
    arc = subprocess.run(["git", "-C", REPO, "archive", REV, "lib", "tests", "install.sh"], capture_output=True, timeout=120)
    if arc.returncode != 0:
        raise unittest.SkipTest("git archive %s: %s" % (REV, arc.stderr[-300:]))
    subprocess.run(["tar", "-x", "-C", d + ".part"], input=arc.stdout, check=True, timeout=120)
    for rel in COPIED:
        shutil.copyfile(os.path.join(REPO, rel), os.path.join(d + ".part", rel))
    os.rename(d + ".part", d)
    return d


def old(mode, io, case=None):
    """The old side in its own tree; a failure — the tail of its output."""
    d = tree()
    env = dict(os.environ)
    env.pop("KASKAD_ROOT", None)
    env.pop("K3_TR_STRICT_LOG", None)          # the strictness of the old texts is not under test here
    env.update(PYTHONPATH=os.pathsep.join([os.path.join(d, "lib"), d]), S1FIN_MODE=mode, S1FIN_IO=io,
               S1FIN_CASE=case or "")
    p = subprocess.run([sys.executable, "-m", "unittest", "tests.s1fin_old_side"], cwd=d, env=env, capture_output=True,
                       text=True, timeout=900)
    ran = re.search(r"^Ran (\d+) test", p.stderr, re.M)
    skipped = re.search(r"skipped=(\d+)", p.stderr)
    done = (int(ran.group(1)) if ran else 0) - (int(skipped.group(1)) if skipped else 0)
    if p.returncode != 0 or done != RUNS[mode]:              # every case of the mode really ran
        raise AssertionError("old side %s %s:\n%s" % (mode, case, p.stderr[-6000:]))


def made():
    """What 3.3.7.10 itself wrote (``make``), once per run."""
    if "made" not in _TMP:
        io = tempfile.mkdtemp(dir=_tmp(), prefix="made-")
        old("make", io)
        _TMP["made"] = io
    return _TMP["made"]


def back(case, extra=None):
    """The rollback: the root of this world read by 3.3.7.10 (``read``) -> what it said."""
    io = tempfile.mkdtemp(dir=_tmp())
    save_root("root", io)
    if extra:
        for name, obj in extra.items():
            put(name, obj, io)
    old("read", io, case)
    return get("read.json", io)


class PolicyTest(RuCase):
    def test_forward_and_back(self):
        io = made()
        a, b = exits.exit_id(VLESS_A), exits.exit_id(VLESS_B)
        facts = get("policy.json", io)
        self.assertEqual(facts["home"], [a + "-201"])               # 3.3.7.10 put the repeat into «Дом»
        self.assertIn(a + "-201", facts["write_error"] or "")      # and refused a write of the policy
        load_root("policy-root", io)
        self.assertIn(a + "-201", json.dumps(state.load()["gateway"]["exit_policy"]))
        # this version: the view without it, a write goes, the collector takes it out and never puts it back
        view = policy.load(state.load())
        self.assertEqual(repeats(view), [])
        self.assertEqual(policy.group_of(view, a)["id"], policy.MAIN)
        self.assertTrue(policy_ops.exit_move(self.w.runner, b, policy.MAIN)["ok"])
        self.assertNotIn(a + "-201", json.dumps(state.load()["gateway"]["exit_policy"]))
        self.assertEqual(_place(self.w.runner), [])
        self.assertEqual(repeats(state.load()["gateway"]["exit_policy"]), [])
        # the move into «Дом» that ended «Wrong exit id» on 3.3.7.10 (the stand, Н-3): the same id, one file
        self.assertTrue(self.move(a, HOME)["ok"])
        self.assertEqual((self.group_of(a), self.ru_ids()), (HOME, [a]))
        self.assertEqual(repeats(state.load()["gateway"]["exit_policy"]), [])
        # the rollback: 3.3.7.10 reads and writes the policy of this version, its collector puts nothing back
        got = back("policy")
        self.assertEqual((got["view_repeats"], got["a_group"], got["write_ok"]), ([], HOME, True))
        self.assertEqual((got["placed_again"], got["stored_repeats"], got["ru"]), ([], [], [VLESS_A]))


class SnapshotTest(_UndoCase):
    def test_forward_and_back(self):
        io = made()
        facts = get("snap.json", io)
        self.assertEqual([facts["after_import"][i] for i in (0, 2, 3)], [OTHER, "", "reality"])   # 3.3.7.10 lost them
        load_root("snap-root", io)
        self.assertEqual(backup.last_snapshot(), os.path.join(paths.root(), facts["snapshot"]))
        self.before = tuple(facts["before"])                        # the links the devices of 3.3.7.10 were given
        res = backup.undo(self.w.runner)                            # «Вернуть как было» after the update
        self.assert_as_before(res)
        got = back("snap")
        self.assertEqual(got["site"], [DOMAIN, "site", "letsencrypt"])
        self.assertEqual(got["vless"], ["tls", 443])
        self.assertEqual(got["links"], facts["before"])
        self.assertEqual(len(got["snapshots"]), 2)                  # 3.3.7.10's, and the one the undo took
        self.assertEqual(got["snapshots_read"], got["snapshots"])


class LiveTest(_LiveWorld):
    def test_forward_and_back(self):
        io = made()
        facts = get("live.json", io)
        load_root("live-root", io)
        self.now, self.did, aid = facts["now"], facts["did"], facts["aid"]
        self.assertEqual(sorted(facts["metrics"]), ["ts"])          # 3.3.7.10: no open minute
        with mock.patch.object(collect, "LIVE", live.Live()):       # the collector of this version starts on its files
            self.online.add(self.did)
            self.counters(10 ** 6 + 7000, 2 * 10 ** 6 + 21000)
            self.round(61)                                          # the update took a minute: its first point
            doc = json.loads(self.read("/run/kaskad/live.json"))
            self.assertEqual(doc["devices"][self.did]["since"], facts["since"])     # «в сети с» kept
            self.assertTrue(set(facts["events"]) <= set(e["id"] for e in doc["events"]))
            self.assertGreater(doc["metrics"]["ts"], facts["metrics"]["ts"])
            self.assertEqual(doc["metrics"]["cur"].get(aid), [0, 0])
            self.traffic(self.did, 40000, 4000)
            self.counters(10 ** 6 + 7500, 2 * 10 ** 6 + 21700)
            with mock.patch.object(live, "want_fresh", lambda: True):
                self.assertTrue(collect._light_round(self.w.runner))
            doc = json.loads(self.read("/run/kaskad/live.json"))
            self.assertEqual(doc["metrics"]["cur"][aid], [500, 700])
            self.assertIn(self.did, doc["activity"]["cur"])
        self.assertIn(aid, metrics.exit_summary(int(self.now) // 60 * 60)["exits"])    # the history of 3.3.7.10
        got = back("live", {"now.json": {"now": self.now, "did": self.did}})
        self.assertEqual(got["new_metrics"], ["at", "cur", "ts"])  # 3.3.7.10 really read the new live.json
        self.assertIn("ts", got["round_metrics"])
        self.assertEqual(got["since"], facts["since"])              # kept through both restarts
        self.assertIn("devices", got["overview"])
        self.assertIn(aid, got["summary"])


def load_tests(loader, tests, pattern):
    """This module + the migration cases of the parts (PARTS) — the whole migration of А4 by one command. A name that
    is gone fails the run (``loadTestsFromName`` gives a failing test), so the table cannot rot."""
    tests.addTests(loader.loadTestsFromNames(PARTS))
    return tests


if __name__ == "__main__":
    unittest.main()

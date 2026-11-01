"""Q6/Q7, the migration (the owner's rule 24.10: a node of 3.3.7.9 behaves as before after the update, until the owner
chooses a Russian group; the rollback reads the data of this version).

The data are written by the RELEASED code itself — the tree of a commit (``git archive``) runs ``tests/q67_old_side.py``
in a subprocess — and read by this one, and back:

1. 3.3.7.9 / 3.3.7.8 (through a version) → this one, nobody chose the Russian group «Дом»: ``live.no_exit``, the
   people of the panel (without the new fields ``ru_via``, ``ru``, ``ru_net``) and ``gw groups`` — exactly as the
   old version had them;
2. the group chosen (``ru_via`` written by the old version) — read as it is; the one change: its last step is no
   more «нет выхода в интернет» of the device (``no_exit_ru``, SPEC-groups §8.3), the foreign path as it was;
3. the rollback: 3.3.7.9 and 3.3.7.8 read state.json with ``ru_via`` written by ``kaskadctl rules ru-via`` and
   live.json with ``no_exit_ru`` — without an error, the choice kept, no false «нет выхода».

No git or no commit (a tarball) — skipped."""

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from kaskad import paths, snapshot, state
from kaskad.gw import live, policy_ops
from tests.gw.test_ru_target import D1, HOME, RuWorld
from tests.q67_old_side import dec, live_doc, pins, write

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD = (("3.3.7.9", "52506ae6"), ("3.3.7.8", "abe39661"))
RUNS = {"make": 2, "read": 1}
NEW_KEYS = ("ru_via", "ru", "ru_net")
_TMP = {}


def tearDownModule():
    if _TMP.get("dir"):
        shutil.rmtree(_TMP["dir"], ignore_errors=True)


def _tmp():
    if not _TMP.get("dir"):
        _TMP["dir"] = tempfile.mkdtemp(prefix="k3-q67-mig-")
    return _TMP["dir"]


def tree(rev):
    """lib/ and tests/ of a commit, with the old side copied in. SkipTest without git or the commit."""
    d = os.path.join(_tmp(), rev)
    if os.path.isdir(d):
        return d
    try:
        ok = subprocess.run(["git", "-C", REPO, "cat-file", "-e", rev + "^{commit}"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise unittest.SkipTest("no git")
    if ok.returncode != 0:
        raise unittest.SkipTest("no commit %s" % rev)
    os.makedirs(d + ".part")
    arc = subprocess.run(["git", "-C", REPO, "archive", rev, "lib", "tests", "install.sh"], capture_output=True, timeout=120)
    if arc.returncode != 0:
        raise unittest.SkipTest("git archive %s: %s" % (rev, arc.stderr[-300:]))
    subprocess.run(["tar", "-x", "-C", d + ".part"], input=arc.stdout, check=True, timeout=120)
    shutil.copyfile(os.path.join(REPO, "tests", "q67_old_side.py"), os.path.join(d + ".part", "tests", "q67_old_side.py"))
    os.rename(d + ".part", d)
    return d


def old(rev, mode, io):
    """Runs the old side of ``rev`` in its own tree; a failure — the tail of its output."""
    d = tree(rev)
    env = dict(os.environ)
    env.pop("KASKAD_ROOT", None)
    env.pop("K3_TR_STRICT_LOG", None)
    env.update(PYTHONPATH=os.pathsep.join([os.path.join(d, "lib"), d]), Q67_MODE=mode, Q67_IO=io)
    p = subprocess.run([sys.executable, "-m", "unittest", "tests.q67_old_side"], cwd=d, env=env, capture_output=True,
                       text=True, timeout=900)
    ran = re.search(r"^Ran (\d+) test", p.stderr, re.M)
    skipped = re.search(r"skipped=(\d+)", p.stderr)
    done = (int(ran.group(1)) if ran else 0) - (int(skipped.group(1)) if skipped else 0)
    if p.returncode != 0 or done != RUNS[mode]:
        raise AssertionError("old side %s %s:\n%s" % (rev, mode, p.stderr[-6000:]))


def made(rev):
    key = "made-" + rev
    if key not in _TMP:
        io = os.path.join(_tmp(), key)
        os.makedirs(io)
        old(rev, "make", io)
        _TMP[key] = io
    return _TMP[key]


def get(io, name):
    with open(os.path.join(io, name), encoding="utf-8") as f:
        return json.load(f)


def strip(doc):
    """The people of the panel without the fields of this step; ``live`` and ``ts``-like values are the same input."""
    doc = copy.deepcopy(doc)
    for row in (doc.get("users") or []) + (doc.get("devices") or []):
        for k in NEW_KEYS:
            row.pop(k, None)
    return doc


class MigrationCase(RuWorld):
    def people(self):
        from kaskad.panel import api
        snapshot.forget()
        return api._people_view(True)

    def on_old_state(self, io, name):
        """The world, then the state.json the old version wrote, the live.json and pins of a collector of THIS
        version on that decision."""
        self.world(via=None)
        state.save(get(io, name))
        st = state.load()
        return st


def _as_old(g):
    """A group of ``policy_ops.groups`` without what PLAN-order Ш3 added (``per_connection``, ``method`` and ``order``
    of a tier): the rest — as the old release gave it."""
    g = dict(g, label=None)
    g.pop("per_connection", None)
    g["tiers"] = [dict((k, v) for k, v in t.items() if k not in ("method", "order")) for t in g.get("tiers") or []]
    return g


class ForwardTest(MigrationCase):
    def test_nobody_chose_the_group_everything_as_before(self):
        for ver, rev in OLD:
            with self.subTest(ver):
                io = made(rev)
                st = self.on_old_state(io, "a_state.json")
                d = dec(st, {})
                self.assertEqual(live.no_exit(d), get(io, "a_no_exit.json"))
                self.assertEqual(live.no_exit(d, ru=True), {})
                write(paths.live_json(), live_doc(live.no_exit(d), {"no_exit_ru": live.no_exit(d, ru=True)}))
                write(paths.pins_json(), pins(self.ru_ids))
                mine = self.people()
                self.assertEqual(strip(mine), strip(get(io, "a_people.json")))
                for row in mine["devices"]:
                    self.assertEqual(row["ru_net"]["step"], "direct")           # nobody chose: directly, as before
                theirs = get(io, "a_groups.json")
                ours = policy_ops.groups()
                self.assertEqual([_as_old(g) for g in ours["groups"]],
                                 [dict(g, label=None) for g in theirs["groups"]])
                self.assertEqual(set(t["order"] for g in ours["groups"] for t in g["tiers"]), {"auto"})
                self.assertEqual(ours["no_exit"], theirs["no_exit"])

    def test_the_chosen_group_is_read_as_it_is(self):
        for ver, rev in OLD:
            with self.subTest(ver):
                io = made(rev)
                st = self.on_old_state(io, "b_state.json")
                uid = st["gateway"]["users"][0]["id"]
                self.assertEqual(st["gateway"]["users"][0]["ru_via"], HOME)
                d = dec(st, {D1: "last_block"})
                self.assertEqual(get(io, "b_no_exit.json")[D1]["group"], HOME)    # the old one: «нет выхода»
                self.assertNotIn(D1, live.no_exit(d))                               # this one: not the foreign path
                self.assertEqual(live.no_exit(d, ru=True)[D1]["step"], "last_block")
                write(paths.live_json(), live_doc(live.no_exit(d), {"no_exit_ru": live.no_exit(d, ru=True)}))
                write(paths.pins_json(), pins(self.ru_ids))
                mine = self.people()
                dev = [x for x in mine["devices"] if x["id"] == D1][0]
                self.assertNotIn((dev.get("net") or {}).get("step"), ("last_block", "last_direct"))
                self.assertEqual(dev["ru_net"]["step"], "last_block")
                user = [x for x in mine["users"] if x["id"] == uid][0]
                self.assertEqual((user["ru_via"], user["ru"]["via"]), (HOME, HOME))
                old_dev = [x for x in get(io, "b_people.json")["devices"] if x["id"] == D1][0]
                self.assertEqual(old_dev["net"]["step"], "last_block")             # the false «нет выхода» of before


class RollbackTest(MigrationCase):
    def test_the_old_version_reads_the_new_data(self):
        self.world(via=None)
        uid = state.load()["gateway"]["users"][0]["id"]
        rc, out, err = self.run_cli("rules", "ru-via", "user", uid, HOME, "--yes")
        self.assertEqual(rc, 0, err)
        d = dec(state.load(), {D1: "last_block"})
        io = os.path.join(_tmp(), "new-%d" % os.getpid())
        os.makedirs(io, exist_ok=True)
        for name, doc in (("new_state.json", state.load()),
                          # live.json сборщика 3.3.7.11 целиком: + groups и policy.node_ru_offline (экраны Q6)
                          ("new_live.json", live_doc(live.no_exit(d), {"no_exit_ru": live.no_exit(d, ru=True), "groups": live.groups_now(d),
                                                                       "policy": {"node_offline": False, "node_ru_offline": True}})),
                          ("new_pins.json", pins(self.ru_ids))):
            with open(os.path.join(io, name), "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, sort_keys=True)
        for ver, rev in OLD:
            with self.subTest(ver):
                if os.path.exists(os.path.join(io, "read.json")):
                    os.unlink(os.path.join(io, "read.json"))
                old(rev, "read", io)
                got = get(io, "read.json")
                self.assertEqual(got["ru_via"][uid], HOME)                           # the choice kept
                dev = [x for x in got["people"]["devices"] if x["id"] == D1][0]
                self.assertNotIn((dev.get("net") or {}).get("step"), ("last_block", "last_direct"))
                grp = [g for g in got["rules"]["ru_groups"] if g["id"] == HOME][0]
                self.assertEqual(grp["users"], [uid])
                self.assertIn(HOME, [g["id"] for g in got["groups"]["groups"]])

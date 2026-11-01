"""3.3.7.10, the migration (the owner's rule 24.10: «старые данные → новая» и откат в каждом выпуске).

The data are written by the RELEASED code itself — the tree of a commit (``git archive``) runs ``tests/s3310_old_side.py``
in a subprocess — and read by this one, and back:

1. 3.3.7.9 → this version, and through a version (3.3.7.8 → this one): the tokens of the personal page and of the
   subscription (``user.<id>.page`` / ``user.<id>.sub`` in secrets.json) keep their addresses and their kind (a page
   token is never a subscription one); a fetch of the subscription writes neither state.json nor secrets.json nor the
   marks of the window; the body keeps the vless:// and hysteria2:// lines of the old version — the same text, the same
   places — and gets the lines amneziawg:// of AmneziaWG and WireGuard after them; the keys of the page, the status of
   the page and of the subscription, the users and devices of the panel — as the old version said;
2. the rollback: 3.3.7.9 reads the data of this one — state.json and secrets.json after a fetch and a window, live.json
   of this collector (the open window of activity and the minute of metrics.db, new in 3.3.7.10) — without an error:
   the same tokens and addresses, its body of the subscription is the vless:// and hysteria2:// part of this one, the
   keys of the page, the users and devices, the overview of the panel.

The rows of «Пользователи» (01b), the window «Подписка / Статический ключ» and the automatic refresh keep nothing new
on the disk (the panel's own state: the open groups — sessionStorage, as in 3.3.7.9). No git or no commit (a
tarball) — skipped.

3. The checks of the server (``BenchDataTest``): the history and the schedule written by 3.3.7.9 are read by this
   version as they are — the old checks have no point of test 8 (``target``), so the frame of «Поделиться» has no
   line of the point under download and upload (``where.down/up`` None; the ping line names the anchors the node has
   now), the schedule — the same entries; reading writes nothing; back: 3.3.7.9
   reads the history of this version with ``bench.extra.target`` (the list, one check, the comparison) and its
   schedule without an error.

**The release, one command** (the rule «старые данные → новая» и откат for the whole 3.3.7.10):
``python3 -m unittest tests.test_33710_migration`` runs the cases above and, by ``load_tests``, the migration cases
of the parts merged into 3.3.7.10 — each lives with its part (no copy here), RELEASE_PARTS lists them:

| Data of the node | 3.3.7.9 (and 3.3.7.8) → 3.3.7.10 | rollback to 3.3.7.9 |
|---|---|---|
| tokens /p/ and the subscription, users and devices | ForwardTest | RollbackTest |
| the Russian groups (``ru_via``, ``no_exit_ru``) | test_q67_migration.ForwardTest | test_q67_migration.RollbackTest |
| the bot (``tg_proxy_group``, «Напрямую») | gw.test_tg_direct …test_migration_3379_state_goes_as_it_went | gw.test_tg_direct.RollbackTest |
| the checks: history, schedule, the frame of «Поделиться» | BenchDataTest, test_share_migration | BenchDataTest, test_share_migration (the news) |
| the vhost of the test from the browser, its counter | site.test_nginx …update_from_3379…, panel.test_speed_routes.FromOldDataTest | site.test_nginx …downgrade_to_3379… |
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

from kaskad import paths, state
from kaskad.gw import collect, live, model, xray
from kaskad.panel import userpage
from tests.s3310_old_side import FILES, SubWorld, get

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD = (("3.3.7.8", "abe39661"), ("3.3.7.9", "52506ae6"))
PREV = "52506ae6"
RUNS = {"make": 1, "read": 1}     # the cases of tests/s3310_old_side.py per mode
AWG = "amneziawg://"
_TMP = {}


def tearDownModule():
    if _TMP.get("dir"):
        shutil.rmtree(_TMP["dir"], ignore_errors=True)


def _tmp():
    if not _TMP.get("dir"):
        _TMP["dir"] = tempfile.mkdtemp(prefix="k3-33710-mig-")
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
    shutil.copyfile(os.path.join(REPO, "tests", "s3310_old_side.py"), os.path.join(d + ".part", "tests", "s3310_old_side.py"))
    os.rename(d + ".part", d)
    return d


def old(rev, mode, io):
    """Runs the old side of ``rev`` in its own tree; a failure — the tail of its output."""
    d = tree(rev)
    env = dict(os.environ)
    env.pop("KASKAD_ROOT", None)
    env.pop("K3_TR_STRICT_LOG", None)          # the strictness of the old texts is not under test here
    env.update(PYTHONPATH=os.pathsep.join([os.path.join(d, "lib"), d]), S3310_MODE=mode, S3310_IO=io)
    p = subprocess.run([sys.executable, "-m", "unittest", "tests.s3310_old_side"], cwd=d, env=env, capture_output=True,
                       text=True, timeout=900)
    ran = re.search(r"^Ran (\d+) test", p.stderr, re.M)
    skipped = re.search(r"skipped=(\d+)", p.stderr)
    done = (int(ran.group(1)) if ran else 0) - (int(skipped.group(1)) if skipped else 0)
    if p.returncode != 0 or done != RUNS[mode]:              # every case of the mode really ran
        raise AssertionError("old side %s %s:\n%s" % (rev, mode, p.stderr[-6000:]))


def made(rev):
    """What ``rev`` itself wrote (``make``), once per run."""
    key = "made-" + rev
    if key not in _TMP:
        io = os.path.join(_tmp(), key)
        os.makedirs(io)
        old(rev, "make", io)
        _TMP[key] = io
    return _TMP[key]


def _names(links):
    return [urllib.parse.unquote(l.rpartition("#")[2]) for l in links]


def _blob(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


class ForwardTest(SubWorld):
    """1: the data of 3.3.7.8 / 3.3.7.9 read by this version."""

    def test_the_tokens_the_subscription_and_the_panel_stay(self):
        self.world()                                                # the fake node; the data are the old version's
        for ver, rev in OLD:
            with self.subTest(ver):
                io = made(rev)
                was = get("made.json", io)
                self.put_files(io)
                uid = was["uid"]
                # a fetch of the subscription first: nothing new on the disk for the old version to read back
                files = [path() for _, path in FILES]
                before = [_blob(f) for f in files]
                links = userpage.build_sub(model.find_user(state.load(), uid), self.w.runner)[1]
                self.assertEqual([_blob(f) for f in files], before)
                now = self.facts(uid)
                for key in ("page", "sub", "url", "sub_url", "status", "sub_status", "keys", "users", "devices"):
                    self.assertEqual(now[key], was[key], key)
                self.assertEqual((now["page_of"], now["sub_of"]), (uid, uid))
                self.assertEqual((now["page_as_sub"], now["sub_as_page"]), (None, None))   # each token keeps its kind
                self.assertTrue(now["sub_url"].endswith("/p/%s/sub" % was["sub"]))
                # the lines of the old version — the same text, the same places; AmneziaWG and WireGuard after them
                self.assertEqual([l.split("://")[0] for l in was["links"]], ["vless", "hysteria2", "vless"])
                self.assertEqual(links, now["links"])
                self.assertEqual(links[:len(was["links"])], was["links"])
                self.assertEqual(_names(links[len(was["links"]):]),
                                 ["goKaskad PRO · AmneziaWG 2.0 · Телефон", "goKaskad PRO · WireGuard · Ноутбук"])
                self.assertTrue(all(l.startswith(AWG) for l in links[len(was["links"]):]))
                self.assertEqual(now["schema"], 4)                  # metrics.db: no new schema in 3.3.7.10
                if ver == "3.3.7.9":
                    self.assertEqual(now["schema"], was["schema"])


class RollbackTest(SubWorld):
    """2: 3.3.7.9 reads what this version wrote."""

    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(live, "_now", lambda: self.now), mock.patch.object(live, "_mono", lambda: self.now),
                  mock.patch.object(collect, "LIVE", live.Live()), mock.patch.object(collect, "MEM", collect.Mem()),
                  mock.patch.object(collect, "sessions", self.fake_sessions)):
            p.start()
            self.addCleanup(p.stop)
        self.online, self.ctr = set(), {}

    def fake_sessions(self, st, runner, stats=None):
        return [{"kind": "wg", "device": d, "ip": "10.77.1.%d" % (10 + i), "since": int(self.now) - 60,
                 "rx": self.ctr[d][0], "tx": self.ctr[d][1]} for i, d in enumerate(sorted(self.online))]

    def collector(self, did):
        """Rounds of this collector with traffic of ``did`` past a window of 5 min of activity: live.json gets the
        open window and the stamp of the window written (3.3.7.10)."""
        self.w.sysd.units[xray.UNIT]["active"] = True
        self.w.curl.observatory = {"gx-" + self.vid: True}
        self.online.add(did)
        self.ctr[did] = [10 ** 9, 10 ** 9]
        start = int(self.now) // 300
        while int(self.now) // 300 == start or self.now % 300 < 30:   # past the start of the next window, 3 rounds in
            self.ctr[did][0] += 40000
            self.ctr[did][1] += 4000
            self.now += 10
            collect.tick(self.w.runner)
        with open(paths.live_json(), encoding="utf-8") as f:
            return json.load(f)

    def test_the_old_version_reads_the_new_data(self):
        uid, phone = self.world()
        rc, _, err = self.run_json("gw", "issue", phone, "--inbound", "awg2")      # the window: «выдали»
        self.assertEqual(rc, 0, err)
        doc = self.collector(phone)
        self.assertIn(phone, doc["activity"]["cur"])                # the open window of 3.3.7.10 is in live.json
        self.assertIsNotNone(doc["activity"]["ts"])
        new = self.facts(uid)
        self.assertEqual([l.split("://")[0] for l in new["links"]], ["vless", "hysteria2", "vless", "amneziawg", "amneziawg"])
        io = tempfile.mkdtemp(dir=_tmp())
        with open(os.path.join(io, "new.json"), "w", encoding="utf-8") as f:
            json.dump(new, f, ensure_ascii=False)
        for name, path in FILES + (("live.json", paths.live_json),):
            shutil.copyfile(path(), os.path.join(io, name))
        old(PREV, "read", io)                                       # no error in the old page, subscription and panel
        got = get("read.json", io)
        for key in ("page", "sub", "url", "sub_url", "status", "sub_status", "keys", "users", "devices"):
            self.assertEqual(got[key], new[key], key)
        self.assertEqual((got["page_of"], got["sub_of"], got["page_as_sub"], got["sub_as_page"]), (uid, uid, None, None))
        self.assertEqual(got["links"], [l for l in new["links"] if not l.startswith(AWG)])   # its own part, as it was
        self.assertIn("activity", got["live_keys"])                 # the old version really read the new live.json
        self.assertIn("devices", got["overview"])
        self.assertEqual(got["schema"], new["schema"])


# ── 3: the checks of the server — the history (with the point of test 8) and the schedule ─────────────────────────
from tests import test_share_migration as share_mig      # noqa: E402 — its old tree, its 3.3.7.9 writer (no copy)

SCHED = [{"time": "04:10", "mode": "quick", "days": [1, 3, 5]}]
OLD_SCHED = r"""
import json, sys, time
from kaskad import host_sched
now = int(sys.argv[1])
m = host_sched.read(now)
doc = host_sched.save(json.loads(sys.argv[2]), {"confirm": True}, m["etag"], now)
print(json.dumps({"schedule": doc["schedule"]}))
"""
OLD_READ = r"""
import json, sys
from kaskad import host_sched
from kaskad.bench import history
now = int(sys.argv[1])
rows = history.runs(now=now)["runs"]
one = history.run(sys.argv[2])
cmp = history.compare(sys.argv[3], sys.argv[2])
print(json.dumps({"ids": [r["id"] for r in rows], "tests": {t["test"]: t for t in one["tests"]},
                  "cmp": sorted(r["test"] for r in cmp["rows"]), "schedule": host_sched.read(now)["schedule"]}))
"""


class BenchDataTest(unittest.TestCase):
    """3: the history and the schedule of the checks across the update and the rollback."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="k3-33710-bench-")
        cls.tree = share_mig.old_tree(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        from kaskad.gw import metrics
        if not metrics.available():
            self.skipTest("no sqlite3")
        self.root = tempfile.mkdtemp(prefix="root-", dir=self.tmp)
        self.now = int(share_mig.time.time())
        share_mig.run_old(self.tree, self.root, share_mig.MAKE, json.dumps(share_mig.VALS), json.dumps(share_mig.INV),
                          str(self.now))
        self.sched = share_mig.run_old(self.tree, self.root, OLD_SCHED, str(self.now), json.dumps(SCHED))["schedule"]
        from kaskad.bench import hostscore as HS
        HS.reset()
        self.addCleanup(HS.reset)
        p = mock.patch.dict(os.environ, {"KASKAD_ROOT": self.root})
        p.start()
        self.addCleanup(p.stop)

    def files(self):
        out = {}
        for d, _dirs, names in os.walk(self.root):
            for n in names:
                with open(os.path.join(d, n), "rb") as f:
                    out[os.path.join(d, n)] = f.read()
        return out

    def test_the_old_checks_and_schedule_are_read_as_they_are(self):
        from kaskad import host_sched
        from kaskad.bench import history
        from kaskad.bench import share as SH
        self.assertEqual([(e["time"], e["mode"], e["days"]) for e in self.sched], [("04:10", "quick", [1, 3, 5])])
        before = self.files()
        rows = history.runs(now=self.now)["runs"]
        self.assertEqual(len(rows), 6)
        for r in rows:
            tests = {t["test"]: t for t in history.run(r["id"])["tests"]}
            self.assertEqual((tests["net_down"]["value"], tests["net_up"]["value"]), (1700, 1300))
            self.assertEqual((tests["net_down"]["target"], tests["net_up"]["target"]), (None, None))   # no point yet
        where = SH.node_share([])["where"]                          # no point of test 8 — no line under the speeds;
        self.assertEqual((where["down"], where["up"]), (None, None))   # the ping line — the anchors of the node now
        self.assertEqual((where["anchors"], where["n"]), (["cloudflare", "google", "yandex"], 3))
        self.assertEqual(host_sched.read(self.now)["schedule"], self.sched)
        self.assertEqual(self.files(), before)                     # reading the old data writes nothing

    def test_the_old_version_reads_the_history_and_schedule_of_this_one(self):
        from kaskad import score as S
        from kaskad.bench import history, spool
        from kaskad.bench import share as SH
        rid = "%010x" % 200
        doc = spool.check_doc({"id": rid, "ts": self.now - 600, "mode": "full", "by": "panel", "status": "ok",
                               "inv": share_mig.INV}, S.with_mode(dict(share_mig.VALS), "full"))
        for t in doc["tests"]:                                     # as test 8 of this version writes it (channel.py)
            if t["test"] in ("net_down", "net_up"):
                t["extra"] = dict(t.get("extra") or {}, target="cloudflare")
        spool.put(doc)
        new = {t["test"]: t for t in history.run(rid)["tests"]}
        self.assertEqual((new["net_down"]["target"], new["net_up"]["target"]), ("cloudflare", "cloudflare"))
        self.assertEqual(SH.node_share([])["where"]["down"], "cloudflare")
        prev = history.runs(now=self.now)["runs"][1]["id"]         # an old check of 3.3.7.9 to compare with
        got = share_mig.run_old(self.tree, self.root, OLD_READ, str(self.now), rid, prev)
        self.assertEqual(got["ids"][0], rid)                       # the list of 3.3.7.9 has the new check first
        self.assertEqual((got["tests"]["net_down"]["value"], got["tests"]["net_up"]["value"]), (1700, 1300))
        self.assertNotIn("target", got["tests"]["net_down"])      # the key is new: 3.3.7.9 does not show it
        self.assertIn("net_down", got["cmp"])
        self.assertEqual(got["schedule"], self.sched)


RELEASE_PARTS = (
    "tests.test_q67_migration.ForwardTest", "tests.test_q67_migration.RollbackTest",
    "tests.gw.test_tg_direct.DirectPolicyTest.test_migration_3379_state_goes_as_it_went",
    "tests.gw.test_tg_direct.RollbackTest",
    "tests.test_share_migration.ShareMigrationTest",
    "tests.site.test_nginx.VhostRefreshTest.test_update_from_3379_adds_only_the_speed_lines_by_a_reload",
    "tests.site.test_nginx.VhostRefreshTest.test_downgrade_to_3379_leaves_a_valid_vhost",
    "tests.panel.test_speed_routes.FromOldDataTest",
)


def load_tests(loader, tests, pattern):
    """This module + the migration cases of the parts (RELEASE_PARTS) — the whole migration of 3.3.7.10 by one command.
    A name that is gone fails the run (``loadTestsFromName`` gives a failing test), so the table cannot rot."""
    tests.addTests(loader.loadTestsFromNames(RELEASE_PARTS))
    return tests


if __name__ == "__main__":
    unittest.main()

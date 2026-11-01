"""«Поделиться» (C9–C11), the migration (the owner's rule 24.10: an update from the released version and the way back).
The data are written by the RELEASED code itself — 3.3.7.9 (52506ae6), ``git archive`` of its lib/ in a subprocess —
and read by this one, and back:

1. 3.3.7.9 -> this one: the checks, the state and the panel settings left by 3.3.7.9 give the frame of the latest check
   (``bench.share.node_share`` — GET /api/host/share and ``kaskadctl host share``); reading it writes nothing on the
   node; the switches stay off (no tariff, country or CPU unless asked);
2. the rollback: 3.3.7.9 reads what this one leaves — the news «a check ended» with ``worse`` in the feed (the args of
   ``bench_done`` in live.json) — and says it by its own rules («закончена: N от обычного»), no raw key, no «{…}».
   «Поделиться» keeps no file and no key of state (the prefs of the window live in the browser), so nothing else is
   left to read back.
No git or no commit (a tarball) — skipped."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREV = "52506ae6"                                     # 3.3.7.9
DAY = 86400
VALS = {"aes_1t": 7000, "chacha_1t": 2400, "cpu_1t": 2700, "cpu_mt": 10500, "disk_fsync": 0.15, "disk_rr": 130000,
        "disk_rw": 85000, "dns": 5, "jitter": {"n": 17280, "v": 0.25},
        "loss": {"lo": 0.0015905403461511298, "n": 25920.0, "v": 0.01, "points": [{"e": 0.864, "k": 0.864, "n": 8640.0}] * 3},
        "mem_bw": 20000, "mem_gb": 8, "net_cv": 4, "net_down": 1700, "net_up": 1300, "rtt": 2.5, "steal_p95": 0.1}
INV = {"cpu": "AMD EPYC 7763 64-Core Processor", "nproc": 2, "mem_gb": 3.912}

# the old side: the same calls as tests/panel/test_panel_share.py, in the code of 3.3.7.9
MAKE = r"""
import json, sys, time
from kaskad import state, score as S
from kaskad.bench import spool
from kaskad.gw import metrics
from kaskad.panel import conf
vals, inv, now = json.loads(sys.argv[1]), json.loads(sys.argv[2]), int(sys.argv[3])
metrics.open_writer().close()
for n, d in enumerate(range(10, 4, -1)):
    h = {"id": "%010x" % (n + 1), "ts": now - d * 86400, "mode": "full", "by": "schedule", "status": "ok", "inv": inv}
    spool.put(spool.check_doc(h, S.with_mode(dict(vals), "full")))
st = state.load()
st.setdefault("gateway", {})["host"] = "192.0.2.10"
st["gateway"].setdefault("hostmon", {})["geo"] = {"country": "FI", "ru": False, "src": "exits_geo", "ts": now}
state.save(st)
cfg = conf.load()
cfg["proxy"].update(on=True, domain="panel.example.com", path="app-" + "Qw7" * 28 + "ZZ")
conf.save(cfg)
print(json.dumps({"ok": True}))
"""
READ = r"""
import json, sys
from kaskad.gw import live
ev = json.loads(sys.argv[1])
print(json.dumps({"ru": live.event_text(ev, "ru"), "en": live.event_text(ev, "en")}, ensure_ascii=False))
"""


def old_tree(tmp):
    """lib/ of 3.3.7.9; SkipTest without git or the commit."""
    d = os.path.join(tmp, PREV)
    try:
        ok = subprocess.run(["git", "-C", REPO, "cat-file", "-e", PREV + "^{commit}"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise unittest.SkipTest("no git")
    if ok.returncode != 0:
        raise unittest.SkipTest("no commit %s" % PREV)
    arc = subprocess.run(["git", "-C", REPO, "archive", PREV, "lib"], capture_output=True, timeout=120)
    if arc.returncode != 0:
        raise unittest.SkipTest("git archive %s: %s" % (PREV, arc.stderr[-300:]))
    os.makedirs(d)
    subprocess.run(["tar", "-x", "-C", d], input=arc.stdout, check=True, timeout=120)
    return d


def run_old(tree, root, code, *args):
    env = dict(os.environ, PYTHONPATH=os.path.join(tree, "lib"), KASKAD_ROOT=root, KASKAD_LANG="ru")
    env.pop("K3_TR_STRICT_LOG", None)                 # the strictness of the old texts is not under test here
    p = subprocess.run([sys.executable, "-c", code] + list(args), cwd=tree, env=env, capture_output=True, text=True,
                       timeout=300)
    if p.returncode != 0:
        raise AssertionError("3.3.7.9: %s" % p.stderr[-3000:])
    return json.loads(p.stdout.strip().splitlines()[-1])


class ShareMigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="k3-share-mig-")
        cls.tree = old_tree(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        from kaskad.gw import metrics
        if not metrics.available():
            self.skipTest("no sqlite3")
        self.root = tempfile.mkdtemp(prefix="root-", dir=self.tmp)
        self.now = int(time.time())
        run_old(self.tree, self.root, MAKE, json.dumps(VALS), json.dumps(INV), str(self.now))
        from kaskad.bench import hostscore as HS
        from kaskad.bench import share as SH
        from kaskad.gw import hostprobe
        HS.reset()
        self.addCleanup(HS.reset)
        for p in (mock.patch.dict(os.environ, {"KASKAD_ROOT": self.root}),
                  mock.patch.object(SH, "_hostnames", lambda: ["node-anna"]),
                  mock.patch.object(hostprobe, "own_addrs", lambda mono=None: ("198.51.100.7", "fe80::1"))):
            p.start()
            self.addCleanup(p.stop)

    def files(self):
        out = {}
        for d, _dirs, names in os.walk(self.root):
            for n in names:
                p = os.path.join(d, n)
                with open(p, "rb") as f:
                    out[p] = f.read()
        return out

    def test_the_data_of_3379_give_the_frame(self):
        from kaskad import state, tz
        from kaskad.bench import share as SH
        a = SH.node_share([])
        self.assertTrue(a["ready"], a)
        self.assertEqual(a["date"], tz.day(self.now - 5 * DAY))
        self.assertEqual([(m["id"], m["v"]) for m in a["metrics"]][:2], [("down", 1700.0), ("up", 1300.0)])
        self.assertEqual(a["extra"], {"tariff": None, "country": None, "cpu": None})        # nothing on by itself
        before, st = self.files(), state.load()
        b = SH.node_share(["tariff", "country", "cpu"])
        self.assertEqual(b["extra"]["country"], "FI")
        self.assertEqual(b["extra"]["cpu"], INV["cpu"])
        text = json.dumps(b, ensure_ascii=False)
        for bait in ("192.0.2.10", "198.51.100.7", "panel.example.com", "app-", "node-anna"):
            self.assertNotIn(bait, text)
        after = self.files()
        self.assertEqual(sorted(after), sorted(before))            # no new file: the update writes nothing for it
        self.assertEqual(state.load(), st)

    def test_the_old_version_reads_the_news_of_this_one(self):
        from kaskad.bench import donepush, spool
        from kaskad.bench import hostscore as HS
        from kaskad import score as S
        for n, d in enumerate((3, 2, 1)):
            h = {"id": "%010x" % (100 + n), "ts": self.now - d * DAY + 3600, "mode": "full", "by": "panel",
                 "status": "ok", "inv": INV}
            spool.put(spool.check_doc(h, S.with_mode(dict(VALS, net_down=850), "full")))
        HS.reset()
        args = donepush.with_rel({"ts": self.now, "id": "%010x" % 102, "mode": "full", "by": "panel", "status": "ok",
                                  "why": None})
        self.assertEqual(args["worse"]["link"], "down")             # this version: «Сервер заметно хуже, чем обычно»
        # as the feed of this version keeps it in live.json (live.LiveState._event: key, args, text)
        from kaskad.gw import live
        ev = {"id": self.now * 1000, "ts": self.now, "kind": "bench_done", "text": "", "key": live.bench_key(args),
              "args": live.bench_args(args)}
        ev["text"] = live.event_text(ev)
        ev = json.loads(json.dumps(ev))
        self.assertEqual(ev["key"], "gw_live.event.bench_worse")
        self.assertIn("заметно хуже", ev["text"])
        said = run_old(self.tree, self.root, READ, json.dumps(ev))
        for lang, text in said.items():
            self.assertTrue(text, lang)
            self.assertNotIn("{", text, lang)
            self.assertNotIn("gw_live.", text, lang)
        self.assertIn("закончена", said["ru"])
        self.assertNotIn("заметно хуже", said["ru"])                # the old one says it by its own rules


if __name__ == "__main__":
    unittest.main()

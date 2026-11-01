"""The queue of the checks — bench/spool (SPEC-node-score-schedule-share.md §3.2, NS-S9, NS-S17; B12): the format and
its limits, 0600 and atomic files, the names, prune (400 days, 3000 files), the collector's drain (a transaction a
file, a broken file to bad/, the base failing — the file waits, results.json never carried, the queue gathered while
the collector was stopped), the event «2 skipped in a day»."""

import json
import os
import stat
import time
from unittest import mock

from kaskad import paths
from kaskad.bench import spool
from kaskad.gw import collect, metrics
from tests.gw.test_collect_host import HostWorld
from tests.gw.test_metrics_bench import BAD_RUN, BAD_TEST, INV, check
from tests.helpers import RootCase

D = 86400
NOW = 1760000000 // 3600 * 3600 + 1800          # 2025-10-09 08:30 UTC = 11:30 in Moscow (the default zone)


def rel_drop():
    """A good event bench_drop of NS-D33 (the drop of the download to 40 of usual)."""
    return {"link": "down", "cond": "rel", "day": "2030-01-02", "matched": ["rel"], "push": True, "now": 40,
            "test": "net_down", "value": 240.0, "usual": 600.0, "unit": "Мбит/с"}


def sp(name=""):
    return os.path.join(paths.bench_spool(), name)


class FormatTest(RootCase):
    def test_put_writes_0600_atomic_by_id(self):
        p = spool.put(check(inv=INV))
        self.assertEqual(p, sp("a1b2c3d4e5.json"))
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(sp()).st_mode), 0o700)
        self.assertEqual(os.listdir(sp()), ["a1b2c3d4e5.json"])          # no temporary file left
        run, tests = spool.load_run(p)
        self.assertEqual((run["id"], run["status"], len(tests)), ("a1b2c3d4e5", "ok", 2))

    def test_put_refuses_a_bad_check_and_a_big_one(self):
        with self.assertRaises(ValueError):
            spool.put(check(rid="../etc/x"))
        big = check(tests=[{"test": "t%02d" % i, "unit": "u", "why": "a" * 64, "value": 1.0} for i in range(64)],
                    why_args=dict(("k%02d" % i, "x" * 64) for i in range(16)), inv=dict(INV, cpu="c" * 128))
        big["run"]["pad"] = "z" * spool.MAX_BYTES
        with self.assertRaises(ValueError):
            spool.put(big)
        self.assertFalse(os.path.exists(sp()) and os.listdir(sp()))

    def test_the_limits_of_values(self):
        """С-2 круг 2: strict — a path, a text with spaces, an unknown key, a number out of its range: the file is bad
        (``put`` refuses it: the runner gets ValueError, the collector never sees it)."""
        run, tests = spool.rows(check(inv={"cpu": "AMD EPYC", "nproc": 2, "mem_gb": 3.8, "engines": ["vless", "awg"]},
                                      why="busy", why_args={"mbps": 4.2, "limit": 2, "ok": True}))
        self.assertEqual(json.loads(run["inv"])["engines"], ["awg", "vless"])
        self.assertEqual(json.loads(run["why_args"]), {"limit": 2, "mbps": 4.2, "ok": 1})
        self.assertEqual(run["why"], "busy")
        for kw in BAD_RUN:
            with self.assertRaises(ValueError, msg=kw):
                spool.put(check(**kw))
        for t in BAD_TEST:
            with self.assertRaises(ValueError, msg=t):
                spool.put(check(tests=[t]))
        self.assertFalse(os.path.exists(sp()) and os.listdir(sp()))

    def test_check_doc_from_the_values_of_score(self):
        """С-1 круг 2: the file of a check from the values in the shape of kaskad.score — every field score reads has
        its place; a field without one is refused (never dropped silently)."""
        doc = spool.check_doc({"id": "a1b2c3d4e5", "ts": NOW, "mode": "full", "by": "panel", "status": "ok"},
                              {"net_down": {"v": 420.0, "corr": 600.0, "people": 180.0, "budget": True},
                               "loss": {"v": 0.5, "n": 600, "lo": 0.08, "points": [{"n": 600, "k": 3, "e": 1}]},
                               "dns": {"v": 12.0, "src": "kaskad"}, "rel_port": {"v": 0.4, "src": "user"},
                               "cpu_canary_cv": {"v": None, "wait": True}, "cpu_1t": {"v": 900, "ok": False,
                                                                                     "why": "x"},
                               "mem_gb": 2, "_engines": ["vless"], "_cpu_flags": []},
                              more={"net_down": {"unit": "Мбит/с", "ca": "bbr"}})
        run, tests = spool.rows(doc)
        self.assertEqual(json.loads(run["meta"]), {"cpu_flags": [], "engines": ["vless"]})
        t = dict((x["test"], x) for x in tests)
        self.assertEqual(json.loads(t["loss"]["extra"]), {"lo": 0.08, "n": 600, "points": [{"e": 1, "k": 3, "n": 600}]})
        self.assertEqual((t["net_down"]["value_corr"], t["net_down"]["people"], t["net_down"]["ca"]), (600.0, 180.0, "bbr"))
        self.assertEqual((t["cpu_1t"]["ok"], t["cpu_1t"]["why"]), (0, "x"))
        self.assertEqual(json.loads(t["dns"]["extra"]), {"src": "kaskad"})
        for bad in ({"net_down": {"v": 1, "raw": 2}}, {"_star": 1}, {"net_down": {"v": 1, "under_load": True}}):
            with self.assertRaises(ValueError):
                spool.check_doc({"id": "a1b2c3d4e5"}, bad)

    def test_epoch_kinds(self):
        self.assertEqual(spool.epoch_kinds(INV, INV), [])
        self.assertEqual(spool.epoch_kinds(None, INV), [])
        self.assertEqual([k for k, _ in spool.epoch_kinds(INV, dict(INV, nproc=4))], ["cpu"])
        self.assertEqual([k for k, _ in spool.epoch_kinds(INV, dict(INV, mem_gb=3.8 * 1.09))], [])
        self.assertEqual([k for k, _ in spool.epoch_kinds(INV, dict(INV, mem_gb=3.8 * 0.85))], ["mem"])
        self.assertEqual([k for k, _ in spool.epoch_kinds(INV, dict(INV, engines=["awg"]))], ["engines"])
        self.assertEqual(spool.epoch_kinds(INV, dict(INV, cpu="Intel"))[0][1], {"nproc": 2, "was_nproc": 2})

    def test_epoch_kinds_as_score_inv_changes(self):
        """The rule of score.inv_changes (B4; to replace epoch_kinds at the merge): a key known on one side only is
        unknown, not changed (B6: B1 writes engines/cpu null when the state cannot be read); the engines compare as
        sets; mem only when both are known."""
        kinds = lambda a, b: [k for k, _ in spool.epoch_kinds(a, b)]
        self.assertEqual(kinds({"cpu": "x"}, {"cpu": "x", "nproc": 2}), [])
        self.assertEqual(kinds({"cpu": "x", "nproc": 1}, {"cpu": "x", "nproc": 2}), ["cpu"])
        self.assertEqual(kinds({"cpu": "x", "engines": ["awg", "vless"]}, {"cpu": "x", "engines": ["vless", "awg"]}),
                         [])
        self.assertEqual(kinds({"cpu": "x", "engines": ["awg"]}, {"cpu": "x"}), [])
        self.assertEqual(kinds({"cpu": "x", "engines": ["awg"]}, {"cpu": "x", "engines": None}), [])
        self.assertEqual(kinds({"cpu": "x", "engines": ["awg"]}, {"cpu": None, "engines": ["awg"]}), [])
        self.assertEqual(kinds({"cpu": "x", "engines": ["awg"]}, {"cpu": "x", "engines": []}), ["engines"])
        self.assertEqual(kinds({"cpu": "x", "mem_gb": 4.0}, {"cpu": "x"}), [])
        self.assertEqual(kinds({"cpu": "x", "mem_gb": 4.0}, {"cpu": "x", "mem_gb": 4.41}), ["mem"])
        self.assertEqual(kinds({"cpu": "x", "mem_gb": 4.0}, {"cpu": "x", "mem_gb": 4.39}), [])

    def test_the_boundary_of_memory_10_percent(self):
        """П-4 круг 2: ± 10 % of MemTotal — the boundary of the rule of score.inv_changes (one source): −10 % exactly
        and ± 9.9 % — no event, ± 10.1 % — «mem»."""
        from kaskad import score
        kinds = lambda b: [k for k, _ in spool.epoch_kinds({"cpu": "x", "mem_gb": 10.0}, {"cpu": "x", "mem_gb": b})]
        for b, want in ((9.0, []), (11.0, []), (9.01, []), (10.99, []), (8.99, ["mem"]), (11.01, ["mem"])):
            self.assertEqual(kinds(b), want, b)
            self.assertEqual(score.inv_changes({"cpu": "x", "mem_gb": 10.0}, {"cpu": "x", "mem_gb": b}), want, b)
        with mock.patch.object(score, "inv_changes", return_value=["cpu"]):   # the rule is score's, not a copy
            self.assertEqual(kinds(10.0), ["cpu"])

    def test_openssl_ver_reaches_inv_changes(self):
        """B4 круг 3 (score.inv_changes compares openssl_ver, kind «openssl»): the version is in bench_run.inv and is
        handed to the rule; the event carries the versions for the line of the screen."""
        from kaskad import score
        seen = []

        def rule(prev, cur):
            seen.append((prev.get("openssl_ver"), cur.get("openssl_ver")))
            return ["openssl"] if prev.get("openssl_ver") != cur.get("openssl_ver") else []

        with mock.patch.object(score, "inv_changes", side_effect=rule):
            got = spool.epoch_kinds(json.dumps(dict(INV, openssl_ver="3.0.2")), dict(INV, openssl_ver="3.0.13"))
        self.assertEqual(seen, [("3.0.2", "3.0.13")])
        self.assertEqual(got, [("openssl", {"ver": "3.0.13", "was": "3.0.2"})])
        self.assertIn("openssl", spool.EPOCH_KINDS)

    def test_drops_format(self):
        """NS-D33: one condition «rel» (rel of the link <= 50); now — rel 0…300; test/value/usual/unit — the raw
        numbers."""
        doc = check()
        doc["drops"] = [{"link": "down", "cond": "rel", "matched": ["rel"], "run": "a1b2c3d4e5"}]
        (d,) = spool.drops(doc)
        self.assertEqual((d["link"], d["cond"], d["day"]), ("down", "rel", None))
        self.assertEqual(json.loads(d["args"]), {"matched": ["rel"], "push": False, "auto": False})
        doc["drops"] = [rel_drop()]
        (d,) = spool.drops(doc)                                                  # the good one is taken whole
        self.assertEqual(json.loads(d["args"]), {"matched": ["rel"], "push": True, "auto": False, "now": 40,
                                                 "test": "net_down", "value": 240.0, "usual": 600.0, "unit": "Мбит/с"})
        for edge in (0, 300):
            doc["drops"] = [dict(rel_drop(), now=edge)]
            self.assertEqual(json.loads(spool.drops(doc)[0]["args"])["now"], edge)
        for bad in ({"cond": "x"}, {"cond": 50}, {"cond": True}, {"matched": ["rel", "x"]}, {"matched": [300]},
                    {"matched": ["rel", "rel"]}, {"now": 301}, {"now": -1}, {"now": 5000}, {"unit": "a\nb"},
                    {"matched": "rel"}, {"push": 1}):
            doc["drops"] = [dict(rel_drop(), **bad)]
            with self.assertRaises(ValueError, msg=bad):
                spool.drops(doc)
        doc["drops"] = []
        self.assertEqual(spool.drop_event(d)["cond"], "rel")
        self.assertEqual(spool.drop_event(dict(d, cond="300"))["cond"], None)     # an old row: no condition of now
        self.assertEqual(spool.drops(check()), [])
        doc["drops"] = [{"link": "down"}]
        with self.assertRaises(ValueError):
            spool.put(doc)

    def test_drops_of_the_old_scale_are_skipped_the_check_stays(self):
        """NS-D34: a queue file written before the update may carry events of the removed scale 0…1000 (cond 100,
        300, "fall"; now — a score). They mean nothing now: skipped, not an error — the check itself is stored, not
        sent to bad/; an event of now next to them is kept."""
        doc = check()
        old = [{"link": "down", "cond": 300, "matched": [300, "fall"], "now": 420, "usual": 610},
               {"link": "up", "cond": "fall", "now": 380}, {"link": "cpu", "cond": "100", "now": 90},
               {"link": "crypto", "cond": 100, "now": 95}]
        doc["drops"] = old
        self.assertEqual(spool.drops(doc), [])
        doc["drops"] = old[1:] + [rel_drop()]
        (d,) = spool.drops(doc)
        self.assertEqual((d["link"], d["cond"], json.loads(d["args"])["now"]), ("down", "rel", 40))
        doc["drops"] = old
        path = spool.put(doc)                                           # the file is written and reads back
        run, tests, drops = spool.load_full(path)
        self.assertEqual((run["id"], drops), ("a1b2c3d4e5", []))
        for bad in ({"cond": 300.0}, {"cond": False}):                  # not a condition of the old scale either
            doc["drops"] = [dict(rel_drop(), **bad)]
            with self.assertRaises(ValueError, msg=bad):
                spool.drops(doc)

    def test_load_refuses_links_and_big_files(self):
        os.makedirs(sp())
        target = os.path.join(self.root, "secret.json")
        with open(target, "w") as f:
            json.dump(check(), f)
        os.symlink(target, sp("a1b2c3d4e5.json"))
        with self.assertRaises(OSError):
            spool.load(sp("a1b2c3d4e5.json"))
        with open(sp("0000000001.json"), "w") as f:
            f.write(" " * (spool.MAX_BYTES + 1))
        with self.assertRaises(ValueError):
            spool.load(sp("0000000001.json"))
        os.mkdir(sp("0000000002.json"))
        with self.assertRaises(ValueError):
            spool.load(sp("0000000002.json"))

    def test_only_its_names_are_read(self):
        os.makedirs(sp())
        for n in ("a1b2c3d4e5.json", ".tmp.abc", "results.json", "A1B2C3D4E5.json", "a1b2c3d4e5.json.bak",
                  "epoch-1760000000-tariff.json", "epoch-1-tariff.json", "x.json"):
            with open(sp(n), "w") as f:
                f.write("{}")
        runs, epochs = spool.scan()
        self.assertEqual([os.path.basename(p) for p, _ in runs], ["a1b2c3d4e5.json"])
        self.assertEqual(epochs, [(sp("epoch-1760000000-tariff.json"), 1760000000)])

    def test_put_epoch(self):
        p = spool.put_epoch(NOW, {"mbps": 1000, "note": "a.b"})
        self.assertEqual(os.path.basename(p), "epoch-%d-tariff.json" % NOW)
        self.assertEqual(spool.load_epoch(p), (NOW, "tariff", {"mbps": 1000, "note": "a.b"}))
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        for a in ({"note": "a/b"}, {"x": {"y": {}}}, "x", {"Bad": 1}):
            with self.assertRaises(ValueError, msg=a):
                spool.put_epoch(NOW + 1, a)
        self.assertEqual(os.path.basename(spool.put_epoch(NOW + 2, spool.args({"note": "a/b", "mbps": 5}))),
                         "epoch-%d-tariff.json" % (NOW + 2))                 # args() cleans first if need be
        with self.assertRaises(ValueError):
            spool.put_epoch("x")


class PruneTest(RootCase):
    def test_older_than_400_days_go(self):
        spool.put(check("0000000001", NOW - 401 * D))
        spool.put(check("0000000002", NOW - 399 * D))
        spool.put_epoch(NOW - 401 * D)
        spool.put_epoch(NOW - D)
        self.assertEqual(spool.prune(NOW), 2)
        self.assertEqual(sorted(os.listdir(sp())), ["0000000002.json", "epoch-%d-tariff.json" % (NOW - D)])

    def test_at_most_3000_the_oldest_go(self):
        with mock.patch.object(spool, "MAX_FILES", 30), mock.patch.object(spool, "MAX_EPOCH_FILES", 3):
            for i in range(35):
                spool.put(check("%010x" % i, NOW - (100 - i) * 3600))
            for i in range(5):
                spool.put_epoch(NOW - i * 60)
            self.assertEqual(spool.prune(NOW), 7)
        left = sorted(n for n in os.listdir(sp()) if not n.startswith("epoch"))
        self.assertEqual(left, ["%010x.json" % i for i in range(5, 35)])
        self.assertEqual(len([n for n in os.listdir(sp()) if n.startswith("epoch")]), 3)
        self.assertEqual(spool.MAX_FILES, 3000)

    def test_a_broken_file_goes_to_bad(self):
        os.makedirs(sp())
        with open(sp("0000000009.json"), "w") as f:
            f.write("{not json")
        spool.prune(NOW)
        self.assertEqual(os.listdir(spool.bad_dir()), ["0000000009.json"])
        self.assertEqual(stat.S_IMODE(os.stat(spool.bad_dir()).st_mode), 0o700)
        self.assertIn("0000000009.json is broken", self.read("/var/log/kaskad.log"))


class DrainTest(RootCase):
    def setUp(self):
        super().setUp()
        self.w = metrics.open_writer()
        self.addCleanup(self.w.close)

    def q(self, sql, *a):
        return self.w.conn.execute(sql, a).fetchall()

    def test_the_queue_into_the_base(self):
        spool.put(check("0000000001", NOW - 2 * D, inv=INV))
        spool.put(check("0000000002", NOW - D, inv=dict(INV, nproc=4)))
        spool.put_epoch(NOW - 3600, {"mbps": 500})
        doc = check("0000000003", NOW - 600)
        doc["drops"] = [{"link": "up", "cond": "rel", "day": "2025-10-09"}]
        spool.put(doc)
        got = spool.drain(self.w, NOW)
        self.assertEqual(self.q("SELECT run, link, cond FROM bench_drop"), [("0000000003", "up", "rel")])
        self.q("DELETE FROM bench_run WHERE id = '0000000003'")
        self.assertEqual((got["taken"], got["bad"]), (4, 0))
        self.assertEqual(os.listdir(sp()), [])
        self.assertEqual(self.q("SELECT id FROM bench_run ORDER BY ts"), [("0000000001",), ("0000000002",)])
        self.assertEqual(self.q("SELECT kind FROM bench_epoch ORDER BY ts"), [("cpu",), ("tariff",)])

    def test_the_second_write_between_the_reading_and_the_removal_stays(self):
        """REV-ns-b11-2 З-1: the runner writes the check again with its ``drops`` (``run.retry_after``) while the
        collector holds the first version — the collector removes only the file it read; the second one stays and
        brings its drops at the next minute."""
        doc = check("0000000003", NOW - 600)
        spool.put(doc)                                   # _finish: the first write, no drops
        real = self.w.write_bench

        def write_bench(d):
            ok = real(d)
            spool.put(dict(doc, drops=[{"link": "up", "cond": "rel", "day": "2025-10-09"}]))   # the second write
            return ok
        self.w.write_bench = write_bench
        got = spool.drain(self.w, NOW)
        self.w.write_bench = real
        self.assertEqual(got["taken"], 0)
        self.assertEqual(os.listdir(sp()), ["0000000003.json"])
        self.assertEqual(self.q("SELECT run, link FROM bench_drop"), [])
        got = spool.drain(self.w, NOW)
        self.assertEqual(got["taken"], 1)
        self.assertEqual(os.listdir(sp()), [])
        self.assertEqual(self.q("SELECT run, link FROM bench_drop"), [("0000000003", "up")])
        self.assertEqual(self.q("SELECT id FROM bench_run"), [("0000000003",)])

    def test_a_second_write_in_the_window_of_the_removal_stays(self):
        """REV-ns-join П-2 / SEC-ns-join П-2 (опыт test_q3_drop_toctou): the runner replaces the file right after the
        collector looked at it and before it removed it — the newer version stays (the collector takes the file by
        ``rename`` and puts a newer one back), no ``.drop`` is left."""
        doc = check("0000000003", NOW - 600)
        path = spool.put(doc)
        got = []
        spool.load(path, got)
        real = os.rename
        fired = []

        def racing_rename(a, b):
            if not fired and b.endswith(spool.DROP_SUFFIX):
                fired.append(1)
                spool.put(dict(doc, drops=[{"link": "up", "cond": "rel", "day": "2025-10-09"}]))
            return real(a, b)
        with mock.patch.object(spool.os, "rename", racing_rename):
            self.assertEqual(spool._drop(path, got[0]), 0)
        self.assertEqual(fired, [1])
        self.assertEqual(os.listdir(sp()), ["0000000003.json"])
        self.assertEqual(len(spool.load(path)["drops"]), 1)                       # the second version
        self.assertEqual(spool._drop(path, got[0]), 0)                              # a plain mismatch: stays
        got = []
        spool.load(path, got)
        self.assertEqual(spool._drop(path, got[0]), 1)
        self.assertEqual(os.listdir(sp()), [])

    def test_a_drop_left_by_a_cut_collector_goes_in_an_hour(self):
        spool.put(check("0000000003", NOW - 600))
        left = os.path.join(sp(), "0000000004.json" + spool.DROP_SUFFIX)
        with open(left, "w") as f:
            f.write("{}")
        other = os.path.join(sp(), "notes.drop")
        with open(other, "w") as f:
            f.write("{}")
        real = time.time()
        self.assertEqual(spool._old_drops(real), 0)                                 # fresh: may be in its window
        self.assertEqual(spool._old_drops(real + 7200), 1)
        self.assertEqual(sorted(os.listdir(sp())), ["0000000003.json", "notes.drop"])

    def test_a_file_changed_in_place_stays_too(self):
        """The same inode rewritten (not by our writer) — the time or the size differ: not removed."""
        spool.put(check("0000000003", NOW - 600))
        real = self.w.write_bench

        def write_bench(d):
            ok = real(d)
            with open(sp("0000000003.json"), "a") as f:
                f.write(" ")
            return ok
        self.w.write_bench = write_bench
        self.assertEqual(spool.drain(self.w, NOW)["taken"], 0)
        self.w.write_bench = real
        self.assertEqual(os.listdir(sp()), ["0000000003.json"])
        self.assertEqual(spool.drain(self.w, NOW)["taken"], 1)

    def test_a_broken_file_aside_the_rest_go_on(self):
        spool.put(check("0000000001", NOW - D))
        with open(sp("0000000002.json"), "w") as f:
            f.write('{"v": 1, "run": {"id": "0000000002"}}')
        with open(sp("0000000003.json"), "w") as f:
            json.dump(check("0000000004"), f)                       # the id is not the name
        with open(sp("epoch-1760000000-tariff.json"), "w") as f:
            f.write("[]")
        got = spool.drain(self.w, NOW)
        self.assertEqual((got["taken"], got["bad"]), (1, 3))
        self.assertEqual(sorted(os.listdir(spool.bad_dir())), ["0000000002.json", "0000000003.json",
                                                               "epoch-1760000000-tariff.json"])
        self.assertEqual(self.q("SELECT id FROM bench_run"), [("0000000001",)])

    def test_any_error_of_the_parse_not_only_value_error(self):
        """REV-ns-b12-2 З-1: on Python 3.10/3.11 a deeply nested file gives RecursionError, not ValueError — the
        readers and the cleaning catch ANY error of one file: drain (the file to bad/, the next one taken), prune,
        read_one and read_all answer."""
        real = spool.load
        for n, exc in enumerate((RecursionError, TypeError)):
            bad, good = "%010d" % (10 * n + 1), "%010d" % (10 * n + 2)
            ep = NOW - 3600 - n
            spool.put(check(bad, NOW - D))
            spool.put(check(good, NOW - D))
            spool.put_epoch(ep, {"mbps": 500})

            def load(path, sig=None, bad=bad, ep=ep):
                if os.path.basename(path) in (bad + ".json", "epoch-%010d-tariff.json" % ep):
                    raise exc("deep")
                return real(path, sig)
            with mock.patch.object(spool, "load", load):
                self.assertIsNone(spool.read_one(sp(bad + ".json")))
                runs, eps = spool.read_all()
                self.assertEqual([r[0]["id"] for r in runs], [good])
                self.assertEqual(eps, [])
                spool.prune(NOW)
                self.assertIn(bad + ".json", os.listdir(spool.bad_dir()))
                spool.put(check(bad, NOW - D))
                got = spool.drain(self.w, NOW)
            self.assertEqual((got["taken"], got["bad"]), (1, 2), exc)
            self.assertEqual(self.q("SELECT id FROM bench_run WHERE id IN (?, ?)", bad, good), [(good,)])
            self.assertEqual(os.listdir(sp()), ["bad"])

    def poisoned(self):
        """Every kind of a bad field (С-2 круг 2), each in a file of its own written past ``put`` (as a broken runner
        or a hand would), ids 1…; -> [names]."""
        docs = [check(**kw) for kw in BAD_RUN] + [check(tests=[t]) for t in BAD_TEST]
        for bad in ({"matched": 300}, {"matched": True}, {"matched": [200]}, {"push": "yes"}, {"now": "x"},
                    {"run": "0000000fff"}, {"cond": "x"}):
            d = check()
            d["drops"] = [dict(rel_drop(), **bad)]
            docs.append(d)
        docs += [dict(check(), drops="x"), dict(check(), drops=[5]), dict(check(), series=[1]),
                 dict(check(), series={"anchors": [{"addr": "a b c d" * 20}]}), dict(check(), v=True),
                 dict(check(), v=1.0)]
        os.makedirs(sp(), exist_ok=True)
        names = []
        for i, d in enumerate(docs):
            rid = "%010x" % (i + 1)
            d["run"]["id"] = rid
            with open(sp(rid + ".json"), "w") as f:
                f.write(json.dumps(d, allow_nan=True))
            names.append(rid + ".json")
        return names

    def test_any_bad_field_goes_to_bad_and_the_queue_goes_on(self):
        """С-2 круг 2: one file with any bad field never stops the queue: it goes to bad/ with a line, the good one
        next to it is taken (the round-1 «matched: 300» stopped drain and the reader with TypeError)."""
        names = self.poisoned()
        spool.put(check("ffffffffff", NOW - 60))
        with mock.patch.object(spool, "MAX_BAD", 1000):
            got = spool.drain(self.w, NOW, limit=1000)
        self.assertEqual((got["taken"], got["bad"]), (1, len(names)))
        self.assertEqual(sorted(os.listdir(spool.bad_dir())), sorted(names))
        self.assertEqual(os.listdir(sp()), ["bad"])
        self.assertEqual(self.q("SELECT id FROM bench_run"), [("ffffffffff",)])
        log = self.read("/var/log/kaskad.log")
        self.assertEqual(log.count("is broken"), len(names))
        self.assertNotIn("/etc/x", log)                              # the reason names the field, never the value

    def test_a_bad_field_in_a_file_the_collector_writes_is_none(self):
        """The writer of the base too: any error of a field — None (aside), never an exception out of write_bench."""
        for name in self.poisoned():
            with open(sp(name)) as f:
                self.assertIsNone(self.w.write_bench(json.load(f)), name)

    def test_the_reader_and_prune_skip_a_bad_file_with_a_line(self):
        from kaskad.bench import history
        names = self.poisoned()
        spool.put(check("ffffffffff", NOW - 60))
        self.assertEqual([r["id"] for r in history.runs(now=NOW)["runs"]], ["ffffffffff"])
        self.assertEqual(history.run("0000000001"), None)
        self.assertEqual(len(history.sample(NOW)["runs"]), 1)
        log = self.read("/var/log/kaskad.log")
        self.assertEqual(log.count("skipped by the reader"), len(names))   # once a file, not once a request
        history.runs(now=NOW)
        self.assertEqual(self.read("/var/log/kaskad.log").count("skipped by the reader"), len(names))
        self.assertEqual(len(os.listdir(sp())), len(names) + 1)       # the reader moves nothing
        with mock.patch.object(spool, "MAX_BAD", 1000):
            spool.prune(NOW)
        self.assertEqual(sorted(os.listdir(spool.bad_dir())), sorted(names))
        self.assertEqual(sorted(os.listdir(sp())), ["bad", "ffffffffff.json"])

    def test_a_bad_tariff_file(self):
        os.makedirs(sp(), exist_ok=True)
        for i, doc in enumerate(({"v": 1, "args": {"note": "a/b"}}, {"v": 1, "args": 5}, {"v": "1"})):
            with open(sp("epoch-176000000%d-tariff.json" % i), "w") as f:
                json.dump(doc, f)
        self.assertEqual(spool.drain(self.w, NOW)["bad"], 3)
        self.assertEqual(len(os.listdir(spool.bad_dir())), 3)

    def test_the_base_failing_the_file_waits(self):
        spool.put(check("0000000001", NOW - D))
        with mock.patch.object(self.w, "write_bench", return_value=False):
            self.assertEqual(spool.drain(self.w, NOW)["taken"], 0)
        self.assertEqual(os.listdir(sp()), ["0000000001.json"])
        self.assertEqual(spool.drain(self.w, NOW)["taken"], 1)
        self.assertEqual(spool.drain(self.w, NOW)["taken"], 0)     # nothing twice

    def test_a_crash_after_the_commit_writes_no_second_row(self):
        spool.put(check("0000000001", NOW - D))
        with mock.patch.object(spool, "_drop", return_value=0):
            spool.drain(self.w, NOW)                                 # written, the file not removed
        spool.drain(self.w, NOW)
        self.assertEqual(self.q("SELECT COUNT(*) FROM bench_run"), [(1,)])
        self.assertEqual(self.q("SELECT COUNT(*) FROM bench"), [(2,)])

    def test_at_most_200_a_minute_oldest_first(self):
        for i in range(205):
            spool.put(check("%010x" % i, NOW - (300 - i) * 60, tests=[]))
        self.assertEqual(spool.drain(self.w, NOW)["taken"], 200)
        self.assertEqual(sorted(os.listdir(sp())), ["%010x.json" % i for i in range(200, 205)])

    def skip(self, rid, ts, why="busy", end_ts=None):
        spool.put(check(rid, ts, status="skipped", why=why, tests=[], score=None, mode="quick", by="schedule",
                        end_ts=end_ts or ts + 60))

    def test_two_skipped_of_a_nominal_day_one_event(self):
        """§2.5 п. 7: busy/locked counted by the nominal day (ts, the zone of the node): 1 → 2 gives the event, the third
        none, disabled not counted, a day not later than skip-push.json none."""
        day0 = 1760043600                                            # 2025-10-09 21:00 UTC = 2025-10-10 00:00 MSK
        self.skip("0000000001", day0 + 10 * 3600)
        self.assertEqual(spool.drain(self.w, NOW)["events"], [])
        self.skip("0000000002", day0 + 2 * 3600, why="disabled")
        self.assertEqual(spool.drain(self.w, NOW)["events"], [])
        self.skip("0000000003", day0 + 23 * 3600 + 1800, why="locked",   # 23:30 of the same day,
                  end_ts=day0 + 24 * 3600 + 35 * 60)                             # decided at 00:35 of the next
        self.assertEqual(spool.drain(self.w, NOW)["events"],
                         [{"kind": "bench_skipped_day", "args": {"day": "2025-10-10", "n": 2}}])
        self.skip("0000000004", day0 + 5 * 3600)
        self.assertEqual(spool.drain(self.w, NOW)["events"], [])     # the third: none
        self.skip("0000000005", day0 + 24 * 3600 + 1800)             # 00:30 of the next day: a day of its own
        self.assertEqual(spool.drain(self.w, NOW)["events"], [])
        self.write(spool.skip_push_path()[len(self.root):], json.dumps({"day": "2025-10-11", "sent": True}))
        self.skip("0000000006", day0 + 25 * 3600)
        self.assertEqual(spool.drain(self.w, NOW)["events"], [])     # the bot decided that day already

    def test_the_event_survives_a_crash_between_the_commit_and_the_removal(self):
        """П-1 круг 2: the second skip written, the collector died before removing the file — taken again, it still
        gives the event (skip-push.json keeps it from a second push, not «new» here)."""
        day0 = 1760043600
        self.skip("0000000001", day0 + 10 * 3600)
        spool.drain(self.w, NOW)
        self.skip("0000000002", day0 + 11 * 3600)
        with mock.patch.object(spool, "_drop", return_value=0):
            spool.drain(self.w, NOW)                                 # written, the event lost with the process
        self.assertEqual(spool.drain(self.w, NOW)["events"],
                         [{"kind": "bench_skipped_day", "args": {"day": "2025-10-10", "n": 2}}])


class CollectorTest(HostWorld):
    """The collector carries the queue (NS-S9), never results.json; a queue gathered while it was stopped goes in."""

    def db(self, sql):
        import sqlite3
        c = sqlite3.connect(self.path("/var/lib/kaskad/metrics.db"))
        try:
            return c.execute(sql).fetchall()
        finally:
            c.close()

    def test_the_queue_of_a_stopped_collector_and_no_results_json(self):
        for i in range(3):
            spool.put(check("000000000%d" % i, int(self.now) - (3 - i) * D))
        self.write("/var/lib/kaskad/bench/results.json", json.dumps(check("ffffffffff", int(self.now) - 60)))
        self.minutes(1)
        self.assertEqual(self.db("SELECT id FROM bench_run ORDER BY ts"),
                         [("0000000000",), ("0000000001",), ("0000000002",)])
        self.assertEqual(os.listdir(sp()), [])
        self.assertTrue(os.path.exists(self.path("/var/lib/kaskad/bench/results.json")))   # left alone
        spool.put(check("0000000003", int(self.now) - 60))
        self.minutes(1)
        self.assertEqual(len(self.db("SELECT id FROM bench_run")), 4)

    def test_a_failing_queue_never_stops_the_tick(self):
        spool.put(check("0000000001", int(self.now) - 60))
        with mock.patch.object(spool, "drain", side_effect=RuntimeError("boom")):
            self.minutes(1)
        self.assertTrue(self.db("SELECT ts FROM m1"))
        self.minutes(1)
        self.assertEqual(self.db("SELECT id FROM bench_run"), [("0000000001",)])

    def test_the_skip_event_is_handed_over(self):
        res = {}
        w = collect._writer()
        for rid, h in (("0000000001", 1), ("0000000002", 2)):
            spool.put(check(rid, int(self.now) - h * 60, status="skipped", why="busy", tests=[], by="schedule"))
        collect._bench_spool(w, res, int(self.now))
        self.assertEqual([e["kind"] for e in res.get("bench_events", [])], ["bench_skipped_day"])


def b1_doc(rid="0b1c2d3e4f", ts=NOW - 3600, dns="dns_src", **run):
    """A check file exactly as B1 writes it (wt-ns-b1 66557fc4, bench/run.py ``_run_doc``/``_bench_rows``/``_finish``,
    bench/suite.py ``inventory``/``now_series``): every field of bench_run, the rows with ``run``, the source of the DNS
    (``dns`` — its key: ``dns_src`` of the договор 19.10, ``src`` as in 66557fc4), the whole inventory, ``why_args``
    with ``forced`` and nulls, the «now» series."""
    r = {"id": rid, "ts": ts, "end_ts": ts + 300, "mode": "full", "by": "panel", "sched_id": None, "retry_of": None,
         "inv": {"cpu": "AMD EPYC 7763 64-Core Processor", "flags": ["fpu", "aes", "avx2", "sse4_2"], "nproc": 2,
                 "smt": 1, "mem_gb": 4.102, "disks": [{"rota": False, "size": 42949672960}], "nic": {"eth0": None},
                 "virt": "kvm", "kernel": "6.8.0-45-generic", "os": {"id": "ubuntu", "version_id": "24.04"},
                 "engines": ["awg", "vless"]},
         "status": "partial", "why": None, "why_args": {"forced": {"mbps": 4.2, "thr_mbps": 2, "cpu": 31.0,
                                                                  "thr_cpu": 20, "io": None, "thr_io": 20},
                                                       "people_max": 3.1, "cpu_max": None, "load": "unknown"},
         "rx": 123456, "tx": 654321, "dur_s": 280.5, "cpu_s": 31.25, "under_load": 1, "pkgs": 0, "score": None,
         "scale_version": None, "links": None}
    r.update(run)
    rows = [{"run": rid, "test": "mem_gb", "ts": ts + 5, "value": 4.102, "unit": "GB", "scale_version": None,
             "mode": "full", "under_load": 1, "ok": 1, "why": None, "score": None, "value_corr": None, "people": None,
             "ca": None},
            {"run": rid, "test": "dns", "ts": ts + 40, "value": 12.5, "unit": "ms", "scale_version": None,
             "mode": "full", "under_load": 1, "ok": 1, "why": None, "score": None, "value_corr": None, "people": None,
             "ca": None, dns: "system"},
            {"run": rid, "test": "cpu_1t", "ts": ts + 60, "value": None, "unit": "ev/s", "scale_version": None,
             "mode": "full", "under_load": 1, "ok": 0, "why": "no_pkgs", "score": None, "value_corr": None,
             "people": None, "ca": None}]
    return {"v": 1, "run": r, "tests": rows,
            "series": {"anchors": [{"addr": "192.0.2.1", "n": 21, "lost": 0, "rtt_med": 31.5, "na": None},
                                   {"addr": "198.51.100.7", "n": 0, "lost": 0, "rtt_med": None, "na": "no_route"}]}}


class B1JointTest(RootCase):
    """Договор с B1 (координатор 19.10): the queue takes B1's files as they are — known fields strictly, an unknown
    key not kept (a line once a key); the whole inventory and the series in bench_run.meta, what score reads of it
    given by sample(); the DNS source ``dns_src`` in bench.extra (history only); a killed check with its sched_id,
    retry_of and the time of its last mark; the skip codes busy/locked of SS §2.5."""

    def setUp(self):
        super().setUp()
        self.w = metrics.open_writer()
        self.addCleanup(self.w.close)
        spool._told_keys.clear()

    q = DrainTest.q

    def test_a_finished_check_of_b1(self):
        from kaskad.bench import history
        doc = b1_doc()
        spool.put(doc)
        self.assertEqual(spool.drain(self.w, NOW)["bad"], 0)
        (inv, meta, wa) = self.q("SELECT inv, meta, why_args FROM bench_run")[0]
        self.assertEqual(json.loads(inv), {"cpu": "AMD EPYC 7763 64-Core Processor", "engines": ["awg", "vless"],
                                           "mem_gb": 4.102, "nproc": 2})      # what score.inv_changes compares
        m = json.loads(meta)
        want_inv = dict(doc["run"]["inv"], flags=sorted(doc["run"]["inv"]["flags"]))
        self.assertEqual(m["inv"], want_inv)                                 # the whole inventory
        self.assertEqual(m["series"], doc["series"])
        self.assertEqual(json.loads(wa), doc["run"]["why_args"])              # forced and the nulls kept
        self.assertEqual(json.loads(self.q("SELECT extra FROM bench WHERE test = 'dns'")[0][0]), {"dns_src": "system"})
        (r,) = history.sample(NOW)["runs"]
        self.assertEqual(r["vals"]["dns"], {"v": 12.5, "mode": "full"})     # dns_src: score does not read it
        self.assertEqual((r["vals"]["_engines"], r["vals"]["_cpu_flags"]), (["awg", "vless"], ["aes", "avx2", "fpu",
                                                                                               "sse4_2"]))
        self.assertEqual(r["vals"]["cpu_1t"], {"v": None, "mode": "full", "ok": False, "why": "no_pkgs"})
        got = history.run("0b1c2d3e4f")
        self.assertEqual(dict((t["test"], t["dns_src"]) for t in got["tests"])["dns"], "system")
        log = self.path("/var/log/kaskad.log")                                 # the договор: every field is known
        self.assertFalse(os.path.exists(log) and "unknown field" in open(log, encoding="utf-8").read())

    def test_the_unknown_country_of_the_channel(self):
        """REV-ns-b3-2 З-1: ``extra.geo`` of test 8 is kept (history only, score never reads it) and the history
        gives its text for a person, RU and EN; another value — a bad file, never kept."""
        from kaskad.bench import history
        doc = b1_doc()
        doc["tests"].append({"run": doc["run"]["id"], "test": "net_down", "ts": NOW - 3500, "value": 640.0,
                             "unit": "Mbit/s", "scale_version": None, "mode": "full", "under_load": 1, "ok": 1,
                             "why": None, "score": None, "value_corr": None, "people": None, "ca": None,
                             "extra": {"geo": "unknown", "budget": True}})
        spool.put(doc)
        self.assertEqual(spool.drain(self.w, NOW)["bad"], 0)
        self.assertEqual(json.loads(self.q("SELECT extra FROM bench WHERE test = 'net_down'")[0][0]),
                         {"geo": "unknown", "budget": True})
        (r,) = history.sample(NOW)["runs"]
        self.assertEqual(r["vals"]["net_down"], {"v": 640.0, "mode": "full", "budget": True})   # no geo for score
        for lang, word in (("ru", "Страна сервера не определена"), ("en", "The country of the server is not known")):
            t = dict((t["test"], t) for t in history.run(doc["run"]["id"], lang)["tests"])
            self.assertNotRegex(t["net_down"]["geo_text"], "Росси|Russia")           # REV-ns-p3a П-2: an old row
            self.assertEqual(t["net_down"]["geo"], "unknown")
            self.assertTrue(t["net_down"]["geo_text"].startswith(word), t["net_down"]["geo_text"])
            self.assertEqual((t["dns"]["geo"], t["dns"]["geo_text"]), (None, None))
        with self.assertRaises(ValueError):
            spool.extra({"geo": "moscow"})
        self.assertEqual(spool.extra({"geo": "unknown_ru"}), {"geo": "unknown_ru"})      # since П3-б (REV-ns-p3a П-2)

    def test_the_spelling_of_66557fc4_and_unknown_keys(self):
        """B1 66557fc4 still writes ``src``; B2 adds ``openssl_ver``: the file is taken, ``src`` not kept (a line once),
        ``openssl_ver`` kept in bench_run.inv (score.inv_changes reads it, B4 круг 3)."""
        d = b1_doc(dns="src")
        d["run"]["inv"]["openssl_ver"] = "3.0.13"
        d["run"]["inv"]["new_hint"] = 1
        d["run"]["a\nb"] = 1                             # REV-ns-b12-2 П-2: a name with a newline never reaches the log
        spool.put(d)
        spool.put(b1_doc("0b1c2d3e40", dns="src"))
        self.assertEqual(spool.drain(self.w, NOW)["bad"], 0)
        self.assertEqual(self.q("SELECT extra FROM bench WHERE test = 'dns'"), [(None,), (None,)])
        self.assertEqual(json.loads(self.q("SELECT inv FROM bench_run WHERE id = '0b1c2d3e4f'")[0][0])["openssl_ver"],
                         "3.0.13")
        log = self.read("/var/log/kaskad.log")
        self.assertEqual(log.count("an unknown field test.src is not kept"), 1)
        self.assertEqual(log.count("an unknown field run.? is not kept"), 1)
        self.assertNotIn("a\nb", log)
        self.assertEqual(log.count("an unknown field run.inv.new_hint is not kept"), 1)
        self.assertEqual(os.listdir(sp()), [])

    def test_the_bad_fields_of_b1_still_go_to_bad(self):
        for rid, bad in (("0b1c2d3e41", lambda d: d["tests"][1].update(dns_src="8.8.8.8")),
                         ("0b1c2d3e42", lambda d: d["run"]["why_args"]["forced"].update(err="Errno 2: /etc/x")),
                         ("0b1c2d3e43", lambda d: d["series"]["anchors"][0].update(n=-1)),
                         ("0b1c2d3e44", lambda d: d["run"]["inv"].update(disks=[{"rota": 5}])),
                         ("0b1c2d3e45", lambda d: d["run"]["inv"].update(flags=["f%031d" % i for i in range(140)]))):
            d = b1_doc(rid)
            bad(d)
            with self.assertRaises(ValueError, msg=rid):
                spool.put(d)

    def test_a_killed_check_of_b1(self):
        """B1 ``clean_stale``: failed / killed, sched_id and retry_of kept, end_ts — the last mark, no tests."""
        r = b1_doc("0b1c2d3e4a", ts=NOW - 7200, sched_id="s-night_1", retry_of="0b1c2d3e49", by="auto",
                   status="failed", why="killed", inv=None, why_args=None, end_ts=NOW - 7200 + 95, rx=None, tx=None,
                   dur_s=None, cpu_s=None, under_load=None, pkgs=None)
        r["tests"] = []
        del r["series"]
        spool.put(r)
        self.assertEqual(spool.drain(self.w, NOW)["bad"], 0)
        self.assertEqual(self.q("SELECT status, why, sched_id, retry_of, end_ts, by, meta FROM bench_run"),
                         [("failed", "killed", "s-night_1", "0b1c2d3e49", NOW - 7200 + 95, "auto", None)])

    def test_skips_of_b1_are_counted(self):
        day0 = 1760043600
        for i, (why, by) in enumerate((("busy", "panel"), ("busy", "schedule"), ("locked", "schedule"),
                                       ("locked", "auto"), ("busy", "panel"))):
            d = b1_doc("0b1c2d3e5%d" % i, ts=day0 + (i + 1) * 3600, status="skipped", why=why, inv=None,
                       why_args={"mbps": 4.2, "io": None}, by=by, retry_of="0b1c2d3e49" if by == "auto" else None)
            del d["series"]
            spool.put(dict(d, tests=[]))
        self.assertEqual(spool.drain(self.w, NOW)["events"],      # REV З-5: only the skips of the schedule count
                         [{"kind": "bench_skipped_day", "args": {"day": "2025-10-10", "n": 2}}])

    def test_a_manual_skip_in_the_base_is_not_one_of_two(self):
        """REV З-5: the count of the day in the base (``_skips_of_day``) — only ``by = schedule``: a manual skip
        already in the base and one skip of the schedule give no event."""
        day0 = 1760043600
        for i, by in enumerate(("panel", "schedule")):
            d = b1_doc("0b1c2d3e6%d" % i, ts=day0 + (i + 1) * 3600, status="skipped", why="busy", inv=None,
                       why_args=None, by=by, retry_of="0b1c2d3e49" if by == "auto" else None)
            del d["series"]
            spool.put(dict(d, tests=[]))
        self.assertEqual(spool.drain(self.w, NOW)["events"], [])

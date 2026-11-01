"""kaskad.site.templates — the index of the catalog on disk (T-M6-qs330 №3: «Выбрать вручную» killed the panel by
memory — the 14 MB catalog was parsed inside MemoryMax=200M).

    build      the file, its header, nothing rebuilt when fresh, a damaged catalog writes nothing
    staleness  another catalog (size/mtime/sha), another format, a broken index file — rebuilt by the first read
    atomic     a failure while writing leaves the previous index and no temp files; a reader keeps its inode
    pages      categories as runs, pages past the end, escaping of TAB/LF/backslash, duplicates, key collisions
    apart      under systemd the build is ``kaskadctl site templates-index`` in ``systemd-run --scope``; its errors
               come back as the same KaskadError; the real separate process on the real catalog
    memory     the panel's process on a catalog of the real scale (33 467 templates, ~14 MB) stays small
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import threading
import unittest
from unittest import mock

from kaskad import sysrun
from kaskad.errors import KaskadError
from kaskad.site import templates as T
from tests.site import fixtures_templates as fx
from tests.site.test_templates import REPO_ROOT, Base

REAL_SHA = "ca31e8d0d6a58687a73cef43fc3c13efe0b2d9f9afa1d257dbe85951a03028e1"


class IndexBase(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(T, "BUILD_APART", False)
        p.start()
        self.addCleanup(p.stop)
        self.put_catalog()

    def put_doc(self, doc):
        """Another catalog on disk (its sha256 patched in, a new mtime)."""
        raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        p = mock.patch.object(T, "CATALOG_SHA256", T._sha(raw))
        p.start()
        self.addCleanup(p.stop)
        path = self.put_catalog(raw)
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
        return path

    def header(self):
        with open(T.index_path(), "rb") as f:
            return T._read_header(f)[0]

    def leftovers(self):
        return sorted(n for n in os.listdir(os.path.dirname(T.index_path())) if n.startswith("." + T.INDEX_NAME))


class TestBuild(IndexBase):

    def test_build_writes_the_index_with_its_header(self):
        r = T.build_index()
        self.assertEqual(r, {"built": True, "total": 17, "categories": 7})
        hdr = self.header()
        sig = T._file_sig(T.catalog_path())
        self.assertEqual((hdr["format"], hdr["src_sha"], hdr["src_size"], hdr["src_mtime_ns"]),
                         (T.INDEX_FORMAT, fx.catalog_sha(), sig[0], sig[1]))
        self.assertEqual([(c["id"], c["start"], c["count"]) for c in hdr["categories"]],
                         [("business", 0, 3), ("landing", 3, 9), ("portfolio", 12, 1), ("other", 13, 2),
                          ("education", 15, 1), ("restaurant", 16, 1), ("legal", 17, 0)])
        self.assertEqual(oct(os.stat(T.index_path()).st_mode & 0o777), "0o644")
        self.assertEqual(T.build_index()["built"], False)          # fresh: nothing is parsed again
        self.assertEqual(self.leftovers(), [])

    def test_reads_build_it_once(self):
        with mock.patch.object(T, "build_index", wraps=T.build_index) as b:
            T.catalog()
            T.search(category="landing")
            T.get("gh_good")
            T.by_short_key(T.short_key("gh_good"))
            T.pick_random()
        self.assertEqual(b.call_count, 1)

    def test_damaged_catalog_writes_nothing(self):
        self.put_catalog(fx.catalog_bytes() + b" ")
        with self.assertRaises(T.CatalogMissing) as cm:
            T.catalog()
        self.assertEqual(cm.exception.data["reason"], "catalog_damaged")
        self.assertFalse(os.path.exists(T.index_path()))

    def test_no_catalog_no_build(self):
        os.unlink(T.catalog_path())
        with mock.patch.object(T, "build_index") as b, self.assertRaises(T.CatalogMissing) as cm:
            T.search()
        self.assertEqual(cm.exception.data["reason"], "catalog_missing")
        b.assert_not_called()

    def test_reads_never_parse_the_catalog(self):
        T.build_index()
        with mock.patch.object(T.json, "load", side_effect=AssertionError("parsed")), \
                mock.patch.object(T, "_catalog_records", side_effect=AssertionError("parsed")):
            T.catalog()
            T.search(query="кафе")
            T.search(category="landing", page=2, per_page=4)
            self.assertEqual(T.get("gh_good")["name"], "Good Business")
            T.pick_random()


class TestStale(IndexBase):

    def test_newer_catalog_is_indexed_again(self):
        self.assertEqual(T.catalog()["total"], 17)
        doc = fx.catalog_doc()
        doc["categories"][0]["templates"].append(fx._t("gh_new", "Новый шаблон", fx.GH + "new"))
        self.put_doc(doc)
        self.assertFalse(T.index_fresh())
        self.assertEqual(T.catalog()["total"], 18)
        self.assertEqual(T.get("gh_new")["name"], "Новый шаблон")
        self.assertTrue(T.index_fresh())

    def test_same_bytes_new_mtime_is_rebuilt(self):
        T.build_index()
        st = os.stat(T.catalog_path())
        os.utime(T.catalog_path(), ns=(st.st_atime_ns, st.st_mtime_ns + 5 * 10 ** 9))
        self.assertFalse(T.index_fresh())
        self.assertEqual(T.build_index()["built"], True)

    def test_new_format_is_rebuilt(self):
        T.build_index()
        with mock.patch.object(T, "INDEX_FORMAT", T.INDEX_FORMAT + 1):
            self.assertFalse(T.index_fresh())
            self.assertEqual(T.catalog()["total"], 17)
            self.assertEqual(self.header()["format"], T.INDEX_FORMAT)

    def test_other_sha_is_rebuilt_then_refused(self):
        T.build_index()
        with mock.patch.object(T, "CATALOG_SHA256", "0" * 64):     # Kaskad updated: another catalog is expected
            self.assertFalse(T.index_fresh())
            with self.assertRaises(T.CatalogMissing):
                T.catalog()

    def test_broken_index_file_is_rebuilt(self):
        for junk in (b"", b"garbage\n", T.MAGIC + b"{not json\n", T.MAGIC + b"[1]\n", T.MAGIC + b"{\"format\": 1"):
            with open(T.index_path(), "wb") as f:
                f.write(junk)
            self.assertEqual(T.catalog()["total"], 17, junk)

    def test_cut_short_index_is_built_again(self):
        T.build_index()
        size = os.path.getsize(T.index_path())
        for cut in (40, size // 2):
            with open(T.index_path(), "r+b") as f:
                f.truncate(size - cut)
            self.assertFalse(T.index_fresh())
            self.assertEqual(T.get("gh_trattoria")["name"], "Trattoria")
            self.assertEqual(os.path.getsize(T.index_path()), size)


class TestAtomic(IndexBase):

    def test_failure_while_writing_keeps_the_previous_index(self):
        T.build_index()
        with open(T.index_path(), "rb") as f:
            before = f.read()
        doc = fx.catalog_doc()
        doc["categories"][0]["templates"].append(fx._t("gh_new", "New", fx.GH + "new"))
        self.put_doc(doc)
        calls = []

        def boom(rec):
            calls.append(rec)
            if len(calls) > 5:
                raise OSError(28, "No space left on device")
            return real(rec)
        real = T._line
        with mock.patch.object(T, "_line", boom), self.assertRaises(OSError):
            T.build_index()
        with open(T.index_path(), "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(self.leftovers(), [])

    def test_failed_rename_leaves_no_temp(self):
        with mock.patch.object(T.os, "rename", side_effect=OSError(5, "EIO")), self.assertRaises(OSError):
            T.build_index()
        self.assertFalse(os.path.exists(T.index_path()))
        self.assertEqual(self.leftovers(), [])

    def test_open_reader_keeps_its_file_while_the_index_is_replaced(self):
        T.build_index()
        ix = T._open_index()
        try:
            doc = fx.catalog_doc()
            doc["categories"][0]["templates"].insert(0, fx._t("gh_first", "First", fx.GH + "first"))
            self.put_doc(doc)
            T.build_index()
            self.assertEqual(ix.record(0)[T._I_ID], "gh_good")         # the old inode
        finally:
            ix.f.close()
        self.assertEqual(T.search(category="business")["items"][0]["id"], "gh_first")

    def test_concurrent_builds_one_valid_file(self):
        errors = []

        def run():
            try:
                T.build_index()
            except Exception as e:          # noqa: BLE001
                errors.append(e)
        ts = [threading.Thread(target=run) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(T.catalog()["total"], 17)
        self.assertEqual(self.leftovers(), [])


class TestPages(IndexBase):

    def test_odd_characters_round_trip(self):
        doc = fx.catalog_doc()
        doc["categories"][0]["templates"].append(fx._t(
            "gh_odd", "Tab\there \\ back", fx.GH + "odd", description="строка 1\nстрока 2\r\\n не перевод\tтаб"))
        doc["categories"][1]["templates"].append(fx._t("gh_good", "Duplicate of the first", fx.GH + "dup"))
        doc["categories"][1]["templates"].append({"name": "no id"})
        doc["categories"].append("not a category")
        self.put_doc(doc)
        it = T.get("gh_odd")
        self.assertEqual((it["name"], it["description"]), ("Tab\there \\ back", "строка 1\nстрока 2\r\\n не перевод\tтаб"))
        self.assertEqual(T.get("gh_good")["name"], "Good Business")         # the first category keeps a duplicate
        self.assertEqual(T.catalog()["total"], 18)
        self.assertEqual([i["id"] for i in T.search(query="here \\ back")["items"]], ["gh_odd"])
        self.assertEqual([i["id"] for i in T.search(query="\\n не")["items"]], ["gh_odd"])
        self.assertEqual(T.search(query="1 строка")["total"], 0)            # LF is not a space
        self.assertEqual([i["id"] for i in T.search(query="СТРОКА 2")["items"]], ["gh_odd"])

    def test_pages_of_a_category_and_of_a_search(self):
        doc = fx.catalog_doc()
        doc["categories"][5]["templates"] += [fx._t("gh_r%02d" % i, "Ресторан %02d" % i, fx.GH + "r%d" % i)
                                              for i in range(60)]
        self.put_doc(doc)
        all_ids = [r[T._I_ID] for r in T.iter_records(category="restaurant")]
        self.assertEqual(len(all_ids), 61)
        got = []
        for p in range(1, 5):
            res = T.search(category="restaurant", page=p)
            got += [i["id"] for i in res["items"]]
            self.assertEqual((res["pages"], res["total"]), (3, 61))
        self.assertEqual(got[:61], all_ids)                       # pages 1..3, then page 4 = the last again
        self.assertEqual(got[61:], all_ids[50:])
        # names first, then descriptions («ресторан» is in the description of gh_trattoria)
        res = T.search(query="ресторан", per_page=100)
        self.assertEqual(res["total"], 61)
        self.assertEqual(res["items"][-1]["id"], "gh_trattoria")
        self.assertEqual([i["id"] for i in T.search(query="ресторан", page=6, per_page=10)["items"]],
                         ["gh_r%02d" % i for i in range(50, 60)])
        self.assertEqual([i["id"] for i in T.search(query="ресторан", page=7, per_page=10)["items"]], ["gh_trattoria"])

    def test_short_key_collisions(self):
        T.build_index()
        with mock.patch.object(T, "short_key", lambda tid: "abcdefabcdef"):
            T.build_index()                     # fresh: the old keys stay — rebuild with the colliding ones
            os.unlink(T.index_path())
            T.build_index()
            for tid in ("gh_good", "gh_trattoria", "h5up_alpha"):
                self.assertEqual(T.get(tid)["id"], tid)
            self.assertEqual(T.by_short_key("abcdefabcdef"), "gh_good")        # the first in the catalog
            self.assertIsNone(T.get("gh_absent"))

    def test_bad_keys(self):
        for k in (None, "", "ABCDEF123456", "abc", "zzzzzzzzzzzz", 12):
            self.assertIsNone(T.by_short_key(k))
        for tid in (None, "", 5, "nope"):
            self.assertIsNone(T.get(tid))


class TestStatusNeverBuilds(IndexBase):

    def test_site_status_names_the_template_only_from_a_ready_index(self):
        from kaskad.site import ops
        s = {"template_id": None, "template_source": None, "deployed_at": None}
        got = {"id": "gh_good", "source": "https://github.com/fx/good", "deployed_at": "2026-10-09T10:00:00Z"}
        with mock.patch.object(T, "status", lambda root=None: got), \
                mock.patch.object(T, "build_index", side_effect=AssertionError("status built the index")):
            self.assertEqual(ops._template_doc(s)["name"], "gh_good")
        T.build_index()
        with mock.patch.object(T, "status", lambda root=None: got):
            self.assertEqual(ops._template_doc(s)["name"], "Good Business")


# ── apart: the build in a separate process ──────────────────────────────────

class TestApart(IndexBase):

    def setUp(self):
        super().setUp()
        T.BUILD_APART = True                       # the patch of IndexBase restores it
        self.runner = sysrun.FakeRunner(binaries=["systemd-run"])
        self.child = []

        def child(argv, input):
            self.child.append(argv)
            if "--fetch" in argv:
                T.fetch_catalog(None)
            return 0, json.dumps(dict(T.build_index(), ok=True)) + "\n", ""
        self.child_fn = child
        self.runner.on(["systemd-run"], child)
        self.runner.on([sys.executable], child)
        p = mock.patch.object(sysrun, "default_runner", lambda: self.runner)
        p.start()
        self.addCleanup(p.stop)

    def test_under_systemd_in_a_scope(self):
        with mock.patch.dict(os.environ, {"INVOCATION_ID": "x"}):
            self.assertEqual(T.catalog()["total"], 17)
            T.search(query="кафе")
        self.assertEqual(len(self.child), 1)
        argv = self.child[0]
        self.assertEqual(argv[:4], ["systemd-run", "--scope", "--quiet", "--collect"])
        self.assertEqual(argv[-3:], ["site", "templates-index", "--json"])

    def test_the_installed_kaskadctl_is_used(self):
        ctl = T.paths.kaskadctl()
        os.makedirs(os.path.dirname(ctl))
        with open(ctl, "w") as f:
            f.write("#!/bin/sh\n")
        self.runner.on([ctl], self.child_fn)
        T.catalog()
        self.assertEqual(self.child[0][:4], [ctl, "site", "templates-index", "--json"])

    def test_without_systemd_a_plain_child(self):
        os.environ.pop("INVOCATION_ID", None)
        T.catalog()
        self.assertEqual(self.child[0][:3], [sys.executable, "-m", "kaskad"])
        env = self.runner.envs[0][1]
        self.assertIn(os.path.join(REPO_ROOT, "lib"), env["PYTHONPATH"].split(os.pathsep))
        self.assertEqual(env["KASKAD_ROOT"], os.environ["KASKAD_ROOT"])

    def test_prepare_fetches_apart(self):
        os.unlink(T.catalog_path())
        os.environ["KASKAD_ASSETS_ROOTS"] = ""
        with mock.patch.object(T, "fetch_catalog", side_effect=lambda r=None: self.put_catalog()):
            T.prepare(None)
        self.assertIn("--fetch", self.child[0])
        self.assertTrue(T.index_fresh())
        T.prepare(None)                                 # fresh: no process at all
        self.assertEqual(len(self.child), 1)

    def test_errors_of_the_child_come_back(self):
        cases = [
            ({"ok": False, "error": "Нет ключа", "code": 1, "data": {"reason": "nokey", "needs_key": True}}, 1,
             KaskadError, "nokey"),
            ({"ok": False, "error": "Повреждён", "code": 1, "data": {"reason": "catalog_damaged"}}, 1,
             T.CatalogMissing, "catalog_damaged"),
            (None, 137, KaskadError, "index_failed"),
        ]
        for doc, rc, cls, reason in cases:
            self.runner._handlers = []
            self.runner.on([sys.executable], lambda a, i, doc=doc, rc=rc: (rc, json.dumps(doc) if doc else "", "Killed"))
            with self.assertRaises(cls) as cm:
                T.prepare(None)
            self.assertEqual(cm.exception.data.get("reason"), reason)
            if reason == "nokey":
                self.assertTrue(cm.exception.data["needs_key"])
            if reason == "index_failed":
                self.assertIn("137", str(cm.exception))

    def test_missing_catalog_spawns_nothing_for_a_read(self):
        os.unlink(T.catalog_path())
        with self.assertRaises(T.CatalogMissing):
            T.search()
        self.assertEqual(self.child, [])


class TestApartReal(Base):
    """The real separate process (``python -m kaskad site templates-index``) on the real catalog of the repository:
    the calling process never parses it."""

    def test_real_child_builds_the_real_catalog(self):
        import gzip
        import tracemalloc
        with open(os.path.join(REPO_ROOT, T.CATALOG_ASSET), "rb") as f:
            raw = gzip.decompress(f.read())
        with mock.patch.object(T, "CATALOG_SHA256", REAL_SHA), mock.patch.object(T, "BUILD_APART", True), \
                mock.patch.object(sysrun, "default_runner", lambda: sysrun.SystemRunner()):
            self.put_catalog(raw)
            del raw
            os.environ.pop("INVOCATION_ID", None)
            tracemalloc.start()
            try:
                cat = T.catalog()
                res = T.search(category="restaurant", page=3)
                found = T.search(query="ресторан")
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
        self.assertEqual((cat["total"], len(cat["categories"])), (33467, 18))
        self.assertEqual(len(res["items"]), 25)
        self.assertGreater(found["total"], 100)
        self.assertLess(peak, 8 * 1024 * 1024, "the caller parsed the catalog: %d bytes" % peak)


class TestCliIndex(Base):

    def test_templates_index_command(self):
        from kaskad import cli
        self.put_catalog()
        p = subprocess.run([sys.executable, "-m", "kaskad", "site", "templates-index", "--json"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                           env=dict(os.environ, PYTHONPATH=os.path.join(REPO_ROOT, "lib")))
        # the child does not know the patched sha of the fixture: a clear «damaged», nothing written
        doc = json.loads(p.stdout)
        self.assertEqual((p.returncode != 0, doc["ok"], doc["data"]["reason"]), (True, False, "catalog_damaged"))
        self.assertFalse(os.path.exists(T.index_path()))
        # stdout, not a patched cli.emit: the CLI keeps the handlers it builds on the first call (a patched emit would
        # stay in them and swallow the output of later tests — test_backup.CipherTest)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(["site", "templates-index", "--json"]), 0)
        self.assertEqual(json.loads(buf.getvalue().strip().splitlines()[-1]),
                         {"ok": True, "built": True, "total": 17, "categories": 7})


# ── memory: the panel's process on a catalog of the real scale ─────────────

class TestMemory(unittest.TestCase):

    def test_panel_process_stays_small_on_33k_templates(self):
        sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
        try:
            import tpl_index_mem
        finally:
            sys.path.remove(os.path.join(REPO_ROOT, "tools"))
        raw = tpl_index_mem.synth_catalog()
        self.assertGreater(len(raw), 12 * 1024 * 1024)
        out = tpl_index_mem.measure(raw)
        panel = out["panel"]
        self.assertEqual(panel["total"], 33467)
        self.assertLess(panel["py"], 8 * 1024 * 1024, out)                       # was ~60–120 MB of Python objects
        self.assertLess(panel["rss"] - panel["rss_base"], 30 * 1024 * 1024, out)  # was +108…+189 MB of RSS
        self.assertIn("build", out)


if __name__ == "__main__":
    unittest.main()

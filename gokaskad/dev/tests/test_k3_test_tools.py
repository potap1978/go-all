"""The test tooling itself (R-speed, 2026-10-15): tools/k3_unit_par.py runs every module the old «unittest discover»
ran, counts like unittest, records the map of what the tests call; tools/test_select.py picks tests by the rules of
its docstring (the folders a test goes through whole — tools/k3_select_scan.py); tools/k3_test.sh takes
-j/--quick/--parts and fails when a module or a step fails (REV-speed С1–С4, З1–З3); a file read by a name put
together from parts, the core run in a process of its own, TERM at once (REV-speed-2 С5, З4, З5)."""

import ast
import fnmatch
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    spec = importlib.util.spec_from_file_location("k3tool_" + name, os.path.join(ROOT, "tools", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


par = load("k3_unit_par")
sel = load("test_select")
scan = load("k3_select_scan")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class UnitParTest(unittest.TestCase):
    def test_discover_is_unittest_discover(self):
        """The same modules as «python3 -m unittest discover -s tests -t .» (packages with __init__.py, test*.py)."""
        mods = par.discover()
        self.assertEqual(len(mods), len(set(mods)))
        self.assertIn("tests.site.test_names", mods)
        self.assertIn(__name__ if __name__.startswith("tests.") else "tests.test_k3_test_tools", mods)
        self.assertNotIn("tests.helpers", mods)
        found = set()
        for d, dirs, files in os.walk(os.path.join(ROOT, "tests")):
            dirs[:] = [x for x in dirs if os.path.isfile(os.path.join(d, x, "__init__.py"))]
            for f in files:
                if f.startswith("test") and f.endswith(".py"):
                    found.add(os.path.relpath(os.path.join(d, f), ROOT)[:-3].replace(os.sep, "."))
        self.assertEqual(set(mods), found)

    def test_parse_counts_like_unittest(self):
        self.assertEqual(par.parse("x\nRan 12 tests in 1.0s\n\nOK (skipped=2)\n"),
                         (12, {"skipped": 2, "failures": 0, "errors": 0, "expected failures": 0,
                               "unexpected successes": 0}, True))
        ran, counts, ok = par.parse("Ran 5 tests in 0.1s\n\nFAILED (failures=1, errors=2, skipped=1)\n")
        self.assertEqual((ran, counts["failures"], counts["errors"], counts["skipped"], ok), (5, 1, 2, 1, False))
        self.assertEqual(par.parse("Traceback …\nImportError: x\n")[2], False)       # no summary — not green

    def test_markers(self):
        self.assertTrue(par.SPLIT_RE.search(read("tests/test_qs333_layout.py")))
        self.assertEqual(par.SERIAL_RE.search("# k3-par: serial=chrome — why\n").group(1), "chrome")
        self.assertEqual(par.to_module("tests/site/test_names.py"), "tests.site.test_names")
        self.assertEqual(par.to_module("tests.site.test_names"), "tests.site.test_names")

    def test_run_counts_fails_and_maps(self):
        """A real run of two small modules: the numbers, a failure reported with its module, the map written."""
        cache = tempfile.mkdtemp(prefix="k3par-")
        self.addCleanup(shutil.rmtree, cache, True)
        env = dict(os.environ, XDG_CACHE_HOME=cache, K3_PAR_LOCKS=cache)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "k3_unit_par.py"), "-j", "2",
                            "tests.test_state", "tests.site.test_names"], cwd=ROOT, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        self.assertIn("UNIT OK", r.stdout)
        mapf = os.path.join(cache, "kaskad-tests", "test-maps.json")
        if hasattr(sys, "monitoring"):                              # 3.12+: the map; before — none (REV-speed С4)
            with open(mapf, encoding="utf-8") as f:
                doc = json.load(f)
            mods = doc["repos"][par.repo_key()]["modules"]
            self.assertIn("lib/kaskad/state.py", mods["tests.test_state"])
            self.assertIn("lib/kaskad/site/model.py", mods["tests.site.test_names"])
        else:
            self.assertFalse(os.path.exists(mapf))
        # a module that does not import — FAILED with its name, exit 1
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "k3_unit_par.py"), "tests.no_such_module"],
                           cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True)
        self.assertEqual(r.returncode, 1, r.stdout[-2000:])
        self.assertIn("UNIT FAILED", r.stdout)
        self.assertIn("tests.no_such_module", r.stdout)

    @unittest.skipUnless(hasattr(sys, "monitoring"), "the map needs sys.monitoring (3.12+)")
    def test_map_is_a_union_per_repository(self):
        """З1: a run adds to what the map of this repository holds (never replaces it); another repository's map and
        the old form (test-map.json, v1) are left alone."""
        cache = tempfile.mkdtemp(prefix="k3par-")
        self.addCleanup(shutil.rmtree, cache, True)
        d = os.path.join(cache, "kaskad-tests")
        os.makedirs(d)
        key = par.repo_key()
        with open(os.path.join(d, "test-maps.json"), "w", encoding="utf-8") as f:
            json.dump({"v": 2, "repos": {key: {"modules": {"tests.test_state": ["lib/kaskad/zz_seen_before.py"]}},
                                         "/other/repo": {"modules": {"tests.test_state": ["x.py"]}}}}, f)
        env = dict(os.environ, XDG_CACHE_HOME=cache, K3_PAR_LOCKS=cache)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "k3_unit_par.py"), "tests.test_state"],
                           cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:])
        with open(os.path.join(d, "test-maps.json"), encoding="utf-8") as f:
            doc = json.load(f)
        got = doc["repos"][key]["modules"]["tests.test_state"]
        self.assertIn("lib/kaskad/zz_seen_before.py", got)            # kept
        self.assertIn("lib/kaskad/state.py", got)                      # added
        self.assertEqual(doc["repos"]["/other/repo"]["modules"]["tests.test_state"], ["x.py"])
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}):
            os.environ.pop("K3_TEST_MAP", None)
            self.assertIn("lib/kaskad/zz_seen_before.py", sel.load_map()["tests.test_state"])

    def test_report_counts_modules_without_a_summary(self):
        """З2: a module that died without unittest's summary (os._exit, a timeout) is an error in the numbers too;
        П3: a module without tests («NO TESTS RAN», 3.12+) is green, as in «unittest discover» of -j 1."""
        self.assertTrue(par.parse("\nRan 0 tests in 0.000s\n\nNO TESTS RAN\n")[2])
        self.assertEqual(par.parse("Ran 3 tests in 0.1s\nOK\nRan 5 tests in 0.2s\n\nOK\n")[0], 5)   # П4: the last one
        r = par.Runner.__new__(par.Runner)
        r.mods, r.jobs, r.wall, r.logdir = ["a", "b", "c"], 2, 1.0, tempfile.mkdtemp(prefix="k3par-")
        self.addCleanup(shutil.rmtree, r.logdir, True)
        z = {"skipped": 0, "failures": 0, "errors": 0, "expected failures": 0, "unexpected successes": 0}
        r.results = {
            "a": {"rc": 0, "secs": 1, "ran": 4, "counts": dict(z), "good": True, "cases": [], "log": ""},
            "b": {"rc": 1, "secs": 1, "ran": 2, "counts": dict(z, failures=1), "good": False, "cases": [], "log": ""},
            "c": {"rc": "timeout", "secs": 1, "ran": 0, "counts": dict(z), "good": False, "cases": [], "log": ""},
        }
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(r.report(), 1)
        self.assertIn("failures=1", out.getvalue())
        self.assertIn("errors=1", out.getvalue())
        self.assertIn("упали (2): b c", out.getvalue())


    def test_no_module_starts_after_the_stop(self):
        """A module taken by a worker after the stop collected the running ones is not started (no orphan)."""
        r = par.Runner(["tests.test_state"], 1, False, 30, use_map=False)
        self.addCleanup(shutil.rmtree, r.logdir, True)
        r.stopping = True
        with mock.patch.object(par.subprocess, "Popen") as popen:
            r.run_one("tests.test_state")
        popen.assert_not_called()
        self.assertEqual(r.results, {})

    def test_times_of_parallel_runners_are_kept(self):
        """The times of the modules: a runner adds its own to what is there (under an flock), not its old copy."""
        cache = tempfile.mkdtemp(prefix="k3par-")
        self.addCleanup(shutil.rmtree, cache, True)
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}):
            for job in ("tests.a", "tests.b"):
                r = par.Runner.__new__(par.Runner)
                r.times, r.results = {}, {job: {"rc": 0, "secs": 1.5}}
                r.save_times()
            with open(par.cache_file(), encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"tests.a": 1.5, "tests.b": 1.5})

    def test_unit_timeout_is_checked(self):
        for v in ("abc", "0", "-5"):
            r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "k3_unit_par.py"), "tests.test_state"],
                               cwd=ROOT, env=dict(os.environ, K3_UNIT_TIMEOUT=v), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True)
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("K3_UNIT_TIMEOUT ждёт число секунд", r.stdout)
            self.assertNotIn("Traceback", r.stdout)
            r = subprocess.run(["bash", os.path.join(ROOT, "tools", "k3_test.sh"), "--parts", "unit"], cwd=ROOT,
                               env=dict(os.environ, K3_UNIT_TIMEOUT=v), stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True)
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("K3_UNIT_TIMEOUT", r.stdout)


class SelectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="k3sel-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.mapf = os.path.join(self.tmp, "map.json")
        p = mock.patch.dict(os.environ, {"K3_TEST_MAP": self.mapf})
        p.start()
        self.addCleanup(p.stop)

    def put_map(self, modules):
        with open(self.mapf, "w", encoding="utf-8") as f:
            json.dump({"modules": modules}, f)

    def pick(self, files, old=None):
        return sel.select(files, "HEAD", old_text=(old or (lambda rel: read(rel))))

    SCANNED = re.compile(r"^(?P<rel>\S+): (?P<src>\S+) проходит (?:весь репозиторий|(?P<kind>папку|файл) (?P<dir>\S+) целиком)"
                         r"(?:, тест его импортирует)?$")

    def assert_only_scanners(self, s, rel):
        """Every unit picked for ``rel`` goes through it whole: the whole tree, or a folder holding ``rel`` that its
        source names in quotes — a guard of the whole repository or of a folder (test_336_mock_addresses) is right
        to be picked, anything else (a text reader, an import, the map, a folder the parse did not understand,
        another folder) is too much."""
        rel = rel.replace(os.sep, "/")
        for m, why in s.units.items():
            for w in why:
                hit = self.SCANNED.match(w)
                self.assertTrue(hit, "%s: лишний выбор — %s" % (m, w))
                self.assertEqual(hit.group("rel"), rel, w)
                d, src = hit.group("dir"), read(hit.group("src"))
                marked = {x for line in re.findall(r"#\s*k3-select:\s*dirs=(\S+)", src) for x in line.split(",")}
                if d is None:                                            # the whole tree: marked «.», git grep, walk
                    self.assertTrue("." in marked or '"git", "grep"' in src or "os.walk(ROOT)" in src,
                                    "%s не проходит всё дерево" % hit.group("src"))
                    # …but not what the mark leaves out («!tests», «!*.md»; REV-speed-joins П-1)
                    for x in (y[1:].rstrip("/") for y in marked if y.startswith("!")):
                        self.assertFalse(fnmatch.fnmatch(rel, x) or fnmatch.fnmatch(os.path.basename(rel), x)
                                         or rel.startswith(x + "/"), "%s: %s исключён пометкой «!%s»" % (m, rel, x))
                else:
                    self.assertTrue(rel == d or rel.startswith(d.rstrip("/") + "/"), w)
                    self.assertEqual(hit.group("kind"), "папку" if os.path.isdir(os.path.join(ROOT, d)) else "файл", w)
                    self.assertTrue('"%s"' % d in src or "'%s'" % d in src or d in marked,
                                    "%s не называет папку %s" % (hit.group("src"), d))

    def test_lib_module_by_map_and_imports(self):
        self.put_map({"tests.test_state": ["lib/kaskad/state.py"], "tests.site.test_names": ["lib/kaskad/paths.py"]})
        s = self.pick(["lib/kaskad/state.py"])
        self.assertIn("tests.test_state", s.units)
        self.assertNotIn("tests.site.test_names", s.units)              # in the map, does not call state.py
        self.assertIn("compat", s.parts)
        self.assertIn("payload", s.parts)
        self.assertNotIn("installer", s.parts)
        self.assertFalse(s.everything)
        # a test module not in the map yet — by the imports (here: every one but the two above)
        self.assertGreater(len(s.units), 2)

    def test_lib_module_without_map_by_imports(self):
        s = self.pick(["lib/kaskad/site/model.py"])
        self.assertIn("tests.site.test_names", s.units)                  # imports it directly
        self.assertTrue(any("карты тестов нет" in n for n in s.notes), s.notes)

    def test_install_sh_payload_only(self):
        """build_payload.py rewrote only the K3_PAYLOAD block — the payload check, not the installer test."""
        new = read("install.sh")
        old = sel.PAYLOAD_BLOCK.sub("# >>> K3_PAYLOAD old\nK3_LIB_SHA=\"x\"\n# <<< K3_PAYLOAD\n", new)
        self.assertNotEqual(old, new)
        s = self.pick(["install.sh"], old=lambda rel: old)
        self.assertEqual(set(s.parts) - {"unit"}, {"payload"})
        # the unit modules here: only the ones going through install.sh whole (git grep of test_333_wizard_ux, the
        # guard of real addresses test_336_mock_addresses) — not its text readers, the installer tests
        self.assert_only_scanners(s, "install.sh")
        s = self.pick(["install.sh"], old=lambda rel: old.replace("\nset -", "\n# changed\nset -", 1))
        self.assertTrue({"installer", "bashn", "egress_parser", "egress_xray", "rules_adopt"} <= set(s.parts))

    def test_compat_for_every_file_the_step_checks(self):
        """REV-speed-joins П-2: k3_test.sh checks tools/k3_text_check.py for Python 3.8 too — a change there picks compat."""
        self.assertIn("tools/k3_text_check.py", sel.compat_targets())
        self.assertIn("compat", self.pick(["tools/k3_text_check.py"]).parts)
        self.assertNotIn("compat", self.pick(["tools/k3_proc.py"]).parts)              # not handed to the step

    def test_a_scanned_file_is_called_a_file(self):
        """REV-speed-joins П-3: install.sh is a file, not a folder."""
        new = read("install.sh")
        old = sel.PAYLOAD_BLOCK.sub("# >>> K3_PAYLOAD old\nK3_LIB_SHA=\"x\"\n# <<< K3_PAYLOAD\n", new)
        why = [w for ws in self.pick(["install.sh"], old=lambda rel: old).units.values() for w in ws]
        self.assertTrue(any("проходит файл install.sh целиком" in w for w in why), why)
        self.assertFalse(any("папку install.sh" in w for w in why), why)

    def test_static_layout_and_chrome_units(self):
        s = self.pick(["lib/kaskad/panel/static/ux.css"])
        self.assertIn("layout", s.parts)
        self.assertIn("tests.test_333q_layout", s.units)
        self.assertIn("tests.test_qs333_layout", s.units)               # setup/index.html loads ux.css
        self.assertTrue(any(m.split(".")[-1].startswith("test_panel") for m in s.units))
        s = self.pick(["lib/kaskad/panel/static/app.js"])
        self.assertNotIn("tests.test_qs333_layout", s.units)            # the wizard does not load app.js

    def test_catalog_key_selects_its_tests(self):
        rel = "lib/kaskad/i18n/catalog/ru/state.json"
        doc = json.loads(read(rel))
        key = next(k for k, v in sorted(doc.items()) if isinstance(v, str))
        changed = dict(doc)
        changed[key] = doc[key] + " (было)"
        s = self.pick([rel], old=lambda r: json.dumps(changed, ensure_ascii=False))
        self.assertTrue(all(m.split(".")[-1].startswith("test_i18n") or
                            any(key in w or "текст" in w for w in s.units[m]) or
                            any(w.startswith(rel + ":") for w in s.units[m]) for m in s.units), s.units)
        self.assertIn("tests.test_i18n", s.units)

    def test_text_readers_and_unknown(self):
        s = self.pick(["tools/k3_layout_check.mjs"])
        self.assertIn("layout", s.parts)
        self.assertIn("tests.test_333p_layout", s.units)                 # read_tool("k3_layout_check.mjs")
        s = self.pick(["tests/__init__.py"])
        self.assertTrue(s.everything)
        self.assertEqual(set(s.units), set(par.discover()))
        for rel in (os.path.join("docs", "zz" + "-note.md"), "zz" + "-note.md"):   # split: this file must not name it
            s = self.pick([rel])
            self.assertEqual(set(s.parts), {"unit"} if s.units else set(), rel)
            self.assertFalse(s.everything, rel)
            self.assert_only_scanners(s, rel)                            # nobody reads it; a guard walking docs may

    def test_helper_selects_its_importers(self):
        s = self.pick(["tests/site/sitecase.py"])                       # no map: every importer
        self.assertIn("tests.site.test_names", s.units)
        self.assertNotIn("tests.test_state", s.units)

    def test_helper_by_map(self):
        """A helper the map knows: the tests calling it and the ones importing it themselves, not every importer."""
        mods = par.discover()
        self.put_map(dict({m: [] for m in mods}, **{"tests.test_state": ["tests/fakes_gw.py"]}))
        s = self.pick(["tests/fakes_gw.py"])
        self.assertIn("tests.test_state", s.units)
        direct = [m for m in mods if "from tests.fakes_gw import" in read(m.replace(".", "/") + ".py")
                  or "from tests import fakes_gw" in read(m.replace(".", "/") + ".py")]
        self.assertTrue(direct)
        for m in direct:
            self.assertIn(m, s.units)
        self.assertLess(len(s.units), len(mods) // 2, sorted(s.units))

    def quiet_map(self):
        """A map where no test calls anything: what is picked comes from the other rules only."""
        self.put_map({m: [] for m in par.discover()})

    def assert_scanned(self, rel, test):
        s = self.pick([rel])
        self.assertIn(test, s.units, "%s не выбран для %s" % (test, rel))
        self.assertTrue(any("проходит" in w for w in s.units[test]), s.units[test])

    def test_folder_scanners_are_picked(self):
        """С1: the tests going through whole folders (os.walk, glob, listdir; through check.assert_no_cyrillic of
        lib) are picked for a change anywhere inside — the cases of REV-speed."""
        self.quiet_map()
        self.assert_scanned("lib/kaskad/panel/static/setup/setup-i18n-ru.js", "tests.test_333_no_ru_domain")
        self.assert_scanned("lib/kaskad/i18n/sh/menu.ru.sh", "tests.test_333_no_ru_domain")
        self.assert_scanned("lib/kaskad/bot/onboard.py", "tests.bot.test_lang")
        for t in ("tests.test_merge_332", "tests.test_332lc_codes", "tests.test_dates_332"):
            self.assert_scanned("lib/kaskad/state.py", t)
        self.assert_scanned("lib/kaskad/gw/peek.py", "tests.test_i18n_gw")
        self.assert_scanned("tests/fixtures/iptables/x/iptables-save.txt", "tests.migrate.test_ipt_parse")
        # a helper going through a folder (tests/migrate/env.py, the fixtures) — its importers
        s = self.pick(["tests/fixtures/iptables/x/iptables-save.txt"])
        self.assertTrue(any("tests/migrate/env.py" in w for m in s.units for w in s.units[m]), s.units)

    def test_every_scan_of_the_tests_is_placed(self):
        """Every os.walk/glob/listdir/«git grep» of tests/ is either understood or marked «# k3-select: dirs=…» —
        else that module goes for any change; a new unplaced one shows here."""
        sc = sel.scans()
        bad = dict((m, lines) for m, lines in sc.unresolved.items() if m not in sc.marks)
        self.assertEqual(bad, {}, "пометьте «# k3-select: dirs=<папки>» (tools/k3_select_scan.py)")
        self.assertEqual(sc.folders("tests.test_333_wizard_ux"), {""})       # git grep over the tree: marked «.»
        self.assertEqual(sc.covers("tests.test_333_wizard_ux", "lib/kaskad/setup/product.py"), "")
        self.assertIsNone(sc.covers("tests.test_333_wizard_ux", "docs/x.md"))      # «!*.md»
        self.assertIsNone(sc.covers("tests.test_333_wizard_ux", "tests/test_state.py"))   # «!tests»

    def test_scan_follows_names_and_calls(self):
        """The reading of k3_select_scan.py on a small tree: constants, a loop over a tuple, a folder handed to a
        function of lib, a run-time folder (skipped), a helper's folder, an unknown one, a mark."""
        root = scan.ROOT
        src = {
            "kaskad.zz": "import os\n"
                         "def walk_all(case, *paths):\n    return _files(paths)\n"
                         "def _files(paths):\n    for p in paths:\n        for d, _, fs in os.walk(p):\n"
                         "            yield fs\n",
            "tests.test_zz": "import os, glob, tempfile\nfrom kaskad import zz\n"
                             "ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
                             "CAT = os.path.join(ROOT, 'lib', 'kaskad', 'i18n', 'catalog')\n"
                             "class T:\n    def setUp(self):\n        self.d = tempfile.mkdtemp()\n"
                             "    def test(self):\n        for lang in ('ru', 'en'):\n"
                             "            glob.glob(os.path.join(CAT, lang, '*.json'))\n"
                             "        list(zz.walk_all(self, os.path.join(ROOT, 'lib', 'kaskad', 'bot')))\n"
                             "        os.listdir(self.d)\n",
            "tests.helper_zz": "import os\nFIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'zz')\n"
                               "def names():\n    return os.listdir(FIX)\n",
            "tests.test_unknown": "import os\ndef test(d):\n    os.walk(d)\ndef caller(x):\n    test(x.y)\n",
            "tests.test_marked": "import subprocess\n# k3-select: dirs=lib/kaskad,tools\n"
                                 "subprocess.run(['git', 'grep', 'x'])\n",
        }
        files = dict((m, ("lib/" if m.startswith("kaskad") else "") + m.replace(".", "/") + ".py") for m in src)
        sc = scan.Scan(files, dict((m, ast.parse(t)) for m, t in src.items()), src).run()
        self.assertEqual(sc.folders("tests.test_zz"), {"lib/kaskad/i18n/catalog/ru", "lib/kaskad/i18n/catalog/en",
                                                        "lib/kaskad/bot"})
        self.assertEqual(sc.folders("tests.helper_zz"), {"tests/fixtures/zz"})
        self.assertEqual(sc.folders("tests.test_unknown"), {""})               # nobody can tell: everything
        self.assertTrue(sc.unresolved["tests.test_unknown"])
        self.assertEqual(sc.folders("tests.test_marked"), {"lib/kaskad", "tools"})
        self.assertNotIn("kaskad.zz", sc.dirs)                                 # lib's own folders do not count
        self.assertTrue(root)

    def test_names_put_together_are_followed(self):
        """С5 (REV-speed-2): a test reading a file by a name put together from parts — «egress.%s.sh» (mutation E),
        read(«give-i18n-%s.js» % lang) (G2) — is picked for that file."""
        self.quiet_map()
        for rel, test in (("lib/kaskad/i18n/sh/egress.en.sh", "tests.test_egress_i18n"),
                          ("lib/kaskad/i18n/sh/egress.ru.sh", "tests.test_egress_i18n"),
                          ("lib/kaskad/panel/static/give-i18n-en.js", "tests.test_332n_final"),
                          ("lib/kaskad/panel/static/give-i18n-en.js", "tests.test_i18n_apps_labels"),
                          ("lib/kaskad/panel/static/give-i18n-ru.js", "tests.test_i18n_apps_labels")):
            s = self.pick([rel])
            self.assertIn(test, s.units, "%s не выбран для %s" % (test, rel))
            self.assertTrue(any("шаблону" in w or "из частей" in w for w in s.units[test]), s.units[test])

    def test_run_time_name_in_a_known_folder(self):
        """С7 (REV-speed-3): test_332d_support reads «os.path.join(STATIC, icon)», «icon» from the catalog of the code
        under test («apps/%s.png») — a change of an icon picks it, by the folder."""
        self.quiet_map()
        s = self.pick(["lib/kaskad/panel/static/apps/amneziavpn.png"])
        self.assertIn("tests.test_332d_support", s.units)

    def test_every_read_of_the_tests_is_placed(self):
        """Every read of a file by a path the source cannot tell is marked «# k3-select: files=…» — else that module
        goes for any change; a new one shows here."""
        rd = sel.reads()
        bad = dict((m, lines) for m, lines in rd.unresolved.items() if m not in rd.marks)
        self.assertEqual(bad, {}, "пометьте «# k3-select: files=<шаблоны>» (tools/k3_select_scan.py, Reads)")

    def test_reads_follow_names_and_calls(self):
        """The reads of k3_select_scan.Reads on a small tree: a name put together and given to a helper, a write, a
        walked folder (its own rule), a path nobody can tell (a finding), a mark, the core run in a process."""
        q = "%s"
        src = {
            "tests.test_rd": "import os\nROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
                             "STATIC = os.path.join(ROOT, 'lib', 'kaskad', 'zz')\n"
                             "def read(name):\n    with open(os.path.join(STATIC, name)) as f:\n"
                             "        return f.read()\n"
                             "def test():\n    for lang in ('ru', 'en'):\n        read('zz-i18n-" + q + ".js' % lang)\n"
                             "    open(os.path.join(ROOT, 'lib', 'kaskad', 'out.txt'), 'w')\n"
                             "    for d, _, fs in os.walk(STATIC):\n        for f in fs:\n"
                             "            open(os.path.join(d, f))\n",
            "tests.test_rd_unknown": "import os\nROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
                                     "class T:\n    def test(self):\n        open(os.path.join(ROOT, self.name))\n",
            "tests.test_rd_marked": "import os\n# k3-select: files=lib/kaskad/zz/*.json\n"
                                    "ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
                                    "class T:\n    def test(self):\n        open(os.path.join(ROOT, self.name))\n",
            # «files=!*»: the read is of a file of the run time (a temp root), none of the repository
            "tests.test_rd_none": "# k3-select: files=!*\nclass T:\n    def test(self):\n"
                                  "        open(self.lim.path)\n",
            "tests.test_rd_run": "import subprocess, sys\nsubprocess.run([sys.executable, '-m', 'kaskad.zz'])\n"
                                 "CODE = 'from kaskad.qq import x'\n",
            # С7: a name of the run time in a known folder — a JSON fixture, the result of the code under test
            "tests.test_rd_json": "import json, os\nROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
                                  "SH = os.path.join(ROOT, 'lib', 'kaskad', 'yy')\n"
                                  "def test():\n    with open(os.path.join(ROOT, 'tests', 'fx.json')) as f:\n"
                                  "        names = json.load(f)['files']\n"
                                  "    for name in names:\n        open(os.path.join(SH, name))\n"
                                  "    icon = catalog()[0]['icon']\n    open(os.path.join(ROOT, 'lib', 'ww', icon), 'rb')\n"
                                  "    rd('docs', 'k3', 'X.md')\n"
                                  "def rd(*parts):\n    with open(os.path.join(ROOT, *parts)) as f:\n        return f.read()\n",
        }
        files = dict((m, m.replace(".", "/") + ".py") for m in src)
        base = scan.Scan(files, dict((m, ast.parse(t)) for m, t in src.items()), src).run()
        rd = scan.Reads(base).run()
        self.assertEqual(rd.covers("tests.test_rd", "lib/kaskad/zz/zz-i18n-en.js"), "lib/kaskad/zz/zz-i18n-en.js")
        self.assertIsNone(rd.covers("tests.test_rd", "lib/kaskad/zz/zz-i18n-de.js"))   # «for lang in ('ru','en')»
        self.assertIsNone(rd.covers("tests.test_rd", "lib/kaskad/out.txt"))             # written, not read
        self.assertNotIn("tests.test_rd", rd.unresolved)                                # the walk: the folders' rule
        self.assertTrue(rd.unresolved.get("tests.test_rd_unknown"))
        self.assertEqual(rd.covers("tests.test_rd_unknown", "docs/anything.md"), "")      # nobody can tell: anything
        self.assertEqual(rd.covers("tests.test_rd_marked", "lib/kaskad/zz/a.json"), "lib/kaskad/zz/*.json")
        self.assertIsNone(rd.covers("tests.test_rd_marked", "lib/kaskad/state.py"))
        self.assertTrue(rd.unresolved.get("tests.test_rd_none"))
        self.assertIn("tests.test_rd_none", rd.marks)                                 # marked: not «any change»
        for rel in ("docs/anything.md", "lib/kaskad/state.py", "install.sh"):
            self.assertIsNone(rd.covers("tests.test_rd_none", rel), rel)
        self.assertNotIn("tests.test_rd_json", rd.unresolved)
        for rel in ("lib/kaskad/yy/a.sh", "lib/kaskad/yy/sub/b.sh", "lib/ww/apps/x.png", "tests/fx.json"):
            self.assertTrue(rd.covers("tests.test_rd_json", rel), rel)
        self.assertIsNone(rd.covers("tests.test_rd_json", "lib/kaskad/zz/a.sh"))
        self.assertEqual(rd.covers("tests.test_rd_json", "docs/k3/X.md"), "docs/k3/X.md")   # «*parts»: one path
        self.assertIsNone(rd.covers("tests.test_rd_json", "docs/other.md"))
        runs = rd.core_runs()
        self.assertTrue({"kaskad.zz", "kaskad.zz.__main__", "kaskad.qq", "kaskad.qq.x"} <= runs["tests.test_rd_run"])
        # a net in the text of any source: a quoted name with a placeholder and a word of its own
        tt = scan.text_templates('read("zz-i18n-' + q + '.js" % lang); `zz-${lang}.mjs`; "*.json"; "/etc/' + q
                                 + '.conf"; f"zz-{x}.sh"; "apps/' + q + '.png"; "' + q + '/' + q + '.png"')
        names = sorted(tt.values())
        self.assertEqual(names, ["*/apps/*.png", "*/zz-*.mjs", "*/zz-*.sh", "*/zz-i18n-*.js"])   # a folder: С7
        self.assertTrue(any(re.search(rx, "lib/kaskad/panel/static/zz-i18n-ru.js") for rx in tt))

    def test_core_run_in_its_own_process(self):
        """З5 (REV-speed-2): test_mcp runs «python -m kaskad.mcp» — the server there calls util, which its own
        process (the map) never does; a change of util picks it by the imports of kaskad.mcp."""
        mods = par.discover()
        self.put_map(dict({m: [] for m in mods}, **{"tests.test_state": ["lib/kaskad/util.py"],
                                                    "tests.test_mcp": ["lib/kaskad/mcp/server.py"]}))
        s = self.pick(["lib/kaskad/util.py"])
        self.assertIn("tests.test_mcp", s.units)
        self.assertTrue(any("отдельным процессом" in w for w in s.units["tests.test_mcp"]), s.units["tests.test_mcp"])

    def test_helper_reading_a_file_brings_its_importers(self):
        """С3: tests/fakes_gw.py takes the egress parser out of install.sh — a change of install.sh (outside the
        generated blocks) picks every test importing the helper, test_334_tofu among them."""
        self.quiet_map()
        new = read("install.sh")
        s = self.pick(["install.sh"], old=lambda rel: new.replace("\nset -", "\n# changed\nset -", 1))
        self.assertIn("tests.test_334_tofu", s.units)
        self.assertTrue(any("tests/fakes_gw.py" in w for w in s.units["tests.test_334_tofu"]), s.units)

    def test_deleted_module_by_the_imports_of_the_base(self):
        """С2: a module gone from the tree — its importers by the graph of the base; no base to tell — everything."""
        base = {"lib/kaskad/zz_gone.py": "X = 1\n",
                "lib/kaskad/zz_user.py": "from kaskad import zz_gone\n",
                "tests/test_state.py": read("tests/test_state.py") + "\nfrom kaskad import zz_user\n"}
        s = sel.select(["lib/kaskad/zz_gone.py"], "HEAD", old_text=lambda rel: None,
                       base_graph=lambda: sel.Graph(base))
        self.assertIn("tests.test_state", s.units)
        self.assertFalse(s.everything)
        s = sel.select(["lib/kaskad/zz_gone.py"], "HEAD", old_text=lambda rel: None, base_graph=lambda: None)
        self.assertTrue(s.everything)
        self.assertEqual(set(s.units), set(par.discover()))

    def test_base_graph_from_git(self):
        if sel.git("rev-parse", "HEAD", check=False) is None:
            self.skipTest("нет git")
        g = sel.base_graph_of("HEAD")
        self.assertIn("kaskad.gw.peek", g.files)
        self.assertIn("tests.test_gw_f2", g.importers("kaskad.gw.peek"))

    def test_renamed_file_is_two_paths(self):
        """С2: a rename is the old path (deleted) and the new one — «git diff --no-renames»."""
        d = tempfile.mkdtemp(prefix="k3sel-git-")
        self.addCleanup(shutil.rmtree, d, True)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@example.com")
        for cmd in (["init", "-q"], ["add", "."], ["commit", "-qm", "a"], ["mv", "a.py", "b.py"]):
            if cmd[0] == "add":
                with open(os.path.join(d, "a.py"), "w") as f:
                    f.write("x = 1\n" * 20)
            r = subprocess.run(["git"] + cmd, cwd=d, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if r.returncode != 0:
                self.skipTest("git: %s" % r.stderr[-200:])
        with mock.patch.object(sel, "ROOT", d):
            self.assertEqual(sel.changed_files("HEAD"), ["a.py", "b.py"])


def terminal_signals():
    """INT, HUP and QUIT by default in a run started by a test, as in a terminal: the tests run in the background
    («nohup … &», a job of a script) get them ignored, and bash cannot trap a signal ignored on entry."""
    for s in (signal.SIGINT, signal.SIGHUP, signal.SIGQUIT, signal.SIGTERM):
        signal.signal(s, signal.SIG_DFL)


MINI_GOOD = "import unittest\nclass T(unittest.TestCase):\n    def test(self):\n        pass\n"
# П3-б (REV-ns-p3a З-1): what lib/kaskad/i18n does in the strict mode when the raise is swallowed by «except Exception»
MINI_SWALLOW = ("import os, unittest\nclass T(unittest.TestCase):\n    def test(self):\n"
                "        log = os.environ.get('K3_TR_STRICT_LOG')\n"
                "        if os.environ.get('K3_TR_STRICT') == '1' and log:\n"
                "            with open(log, 'a') as f:\n                f.write('mini.key: {name}\\n')\n")
# С6 (REV-speed-3): SIGINT in a module and in what it starts — by default, as in a terminal, not ignored
MINI_SIGINT = ("import signal, subprocess, sys, unittest\nclass T(unittest.TestCase):\n    def test(self):\n"
               "        self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)\n"
               "        self.assertIs(signal.getsignal(signal.SIGHUP), signal.SIG_DFL)\n"
               "        out = subprocess.check_output([sys.executable, '-c', 'import signal; "
               "print(signal.getsignal(signal.SIGINT) is signal.default_int_handler)'], universal_newlines=True)\n"
               "        self.assertEqual(out.strip(), 'True')\n")
SH_SIGINT = ("python3 -c 'import signal, sys; sys.exit(3 if signal.getsignal(signal.SIGINT) is signal.SIG_IGN else 0)'"
             " || { echo SIGINT ignored; exit 3; }")
# REV-speed-4 С8: a module and a step that start a process in a session of their own and wait — as node with headless
# Chrome «detached: true»; the stop of the run takes them too
MINI_DETACH = ("import os, subprocess, time, unittest\nclass T(unittest.TestCase):\n    def test(self):\n"
               "        p = subprocess.Popen(['sleep', '60'], start_new_session=True)\n"
               "        open(os.environ['K3_MINI_PID'] + '.mod', 'w').write(str(p.pid))\n        time.sleep(60)\n")
SH_DETACH = ("python3 -c 'import subprocess, sys; p = subprocess.Popen([\"sleep\", \"60\"], start_new_session=True); "
             "open(sys.argv[1], \"w\").write(str(p.pid)); p.wait()' \"$K3_MINI_PID.step\"")
# REV-speed-5 З8: a module and a step of a few seconds — a run under nohup reaches its summary after HUP to the group
MINI_SLOW = ("import os, time, unittest\nclass T(unittest.TestCase):\n    def test(self):\n"
             "        open(os.environ['K3_MINI_PID'] + '.mod', 'w').write(str(os.getpid()))\n        time.sleep(4)\n")
SH_SLOW = 'echo $$ > "$K3_MINI_PID.step"; sleep 4'
MINI_BAD = "import unittest\nclass T(unittest.TestCase):\n    def test(self):\n        self.fail('boom')\n"
MINI_HANG = ("import os, subprocess, time, unittest\nclass T(unittest.TestCase):\n    def test(self):\n"
             "        p = subprocess.Popen(['sleep', '60'])\n"
             "        open(os.environ['K3_MINI_PID'] + '.mod', 'w').write(str(os.getpid()))\n"
             "        open(os.environ['K3_MINI_PID'], 'w').write(str(p.pid))\n        time.sleep(60)\n")


class K3TestShTest(unittest.TestCase):
    """З3: the exit code of k3_test.sh, -j N and -j 1, when a module or a step running beside them fails; П1: the
    limits of a module and of a step (their processes go with them). A small tree: the two tools of the repository
    and tests of its own."""

    def mini(self, selftest="exit 0"):
        d = tempfile.mkdtemp(prefix="k3sh-")
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "tools"))
        os.makedirs(os.path.join(d, "tests"))
        for t in ("k3_test.sh", "k3_unit_par.py", "k3_proc.py"):
            shutil.copy(os.path.join(ROOT, "tools", t), os.path.join(d, "tools", t))
        files = {"tools/fetch_test_bins.sh": "exit 0\n", "tools/egress_parser_selftest.sh": selftest + "\n",
                 "tests/__init__.py": "", "tests/realbin.py": "xray_path = nginx_path = lambda: None\n",
                 "tests/test_qs333_layout.py": "CHROME = 'x'\nbrowser_missing = lambda: 'нет'\n",
                 "tests/test_good.py": MINI_GOOD, "tests/test_bad.py": MINI_BAD, "tests/test_hang.py": MINI_HANG,
                 "tests/test_empty.py": "import unittest\n", "tests/test_sigint.py": MINI_SIGINT,
                 "tests/test_detach.py": MINI_DETACH, "tests/test_slow.py": MINI_SLOW,
                 "tests/test_swallow.py": MINI_SWALLOW}
        for rel, text in files.items():
            with open(os.path.join(d, rel), "w", encoding="utf-8") as f:
                f.write(text)
        return d

    def sh(self, d, *args, sigint=None, **env):
        e = dict(os.environ, XDG_CACHE_HOME=d, K3_PAR_LOCKS=d, K3_LAYOUT="0", **env)
        if "K3_TR_STRICT_LOG" not in env:
            e.pop("K3_TR_STRICT_LOG", None)          # the journal of the run around — not for the mini tree
        t0 = time.time()
        # the signal for INT and HUP (a job of a script, «nohup»)
        pre = None if sigint is None else (lambda: [signal.signal(s, sigint) for s in (signal.SIGINT, signal.SIGHUP)])
        r = subprocess.run(["bash", os.path.join(d, "tools", "k3_test.sh")] + list(args), cwd=d, env=e,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120,
                           preexec_fn=pre)
        return r.returncode, r.stdout, time.time() - t0

    def test_sigint_by_default_in_modules_and_steps(self):
        """С6 (REV-speed-3): a job started with «&» by bash gets SIGINT ignored, and the tests of the Ctrl-C
        protection (test_gw_f2) passed on broken code. The runner, its modules, «-j 1» and the steps (beside the unit
        tests and in the foreground) see SIGINT by default — even when k3_test.sh itself got it ignored."""
        d = self.mini(SH_SIGINT)
        for sigint in (signal.SIG_DFL, signal.SIG_IGN):
            for j in ("2", "1"):
                rc, out, _ = self.sh(d, "-j", j, "--parts", "unit,egress_parser", "--units", "tests.test_sigint",
                                     sigint=sigint)
                self.assertEqual(rc, 0, "-j %s, %s: %s" % (j, sigint, out[-1500:]))
                self.assertIn("ALL GREEN", out)

    def test_exit_code_follows_failures(self):
        d = self.mini()
        for j in ("2", "1"):
            rc, out, _ = self.sh(d, "-j", j, "--parts", "unit", "--units", "tests.test_good tests.test_bad")
            self.assertEqual(rc, 1, out[-1500:])
            self.assertIn("[unit]: FAILURES", out)
            rc, out, _ = self.sh(d, "-j", j, "--parts", "unit", "--units", "tests.test_good tests.test_empty")
            self.assertEqual(rc, 0, out[-1500:])                     # a module without tests: green in both modes
            self.assertIn("ALL GREEN", out)
        d = self.mini("echo selftest failed; exit 3")
        for j in ("4", "1"):
            rc, out, _ = self.sh(d, "-j", j, "--parts", "unit,egress_parser", "--units", "tests.test_good")
            self.assertEqual(rc, 1, out[-1500:])
            self.assertIn("FAIL tools/egress_parser_selftest.sh", out)

    def test_tr_strict_log_fails_the_run(self):
        """П3-б (REV-ns-p3a З-1): a key of tr() left unfilled and swallowed by «except Exception» — in
        $K3_TR_STRICT_LOG; k3_test.sh fails on it (-j N and -j 1), says the key; K3_TR_STRICT=0 — green."""
        d = self.mini()
        for j in ("2", "1"):
            rc, out, _ = self.sh(d, "-j", j, "--parts", "unit", "--units", "tests.test_good tests.test_swallow",
                                 K3_TR_STRICT="1")
            self.assertEqual(rc, 1, out[-1500:])
            self.assertIn("mini.key: {name}", out)
            self.assertIn("FAIL tr(): не заполнена подстановка", out)
            rc, out, _ = self.sh(d, "-j", j, "--parts", "unit", "--units", "tests.test_good", K3_TR_STRICT="1")
            self.assertEqual(rc, 0, out[-1500:])
            rc, out, _ = self.sh(d, "-j", j, "--parts", "unit", "--units", "tests.test_swallow", K3_TR_STRICT="0")
            self.assertEqual(rc, 0, out[-1500:])
        log = os.path.join(d, "given.log")                    # given from outside (test_select) — that file
        rc, out, _ = self.sh(d, "--parts", "unit", "--units", "tests.test_swallow", K3_TR_STRICT="1",
                             K3_TR_STRICT_LOG=log)
        self.assertEqual(rc, 1, out[-1500:])
        with open(log) as f:
            self.assertEqual(f.read(), "mini.key: {name}\n")

    def test_limits_stop_a_module_and_a_step_with_their_processes(self):
        d = self.mini("sleep 60 & echo $! > \"$K3_MINI_PID\"; wait")
        pidf = os.path.join(d, "child.pid")
        rc, out, secs = self.sh(d, "-j", "2", "--parts", "egress_parser", K3_STEP_TIMEOUT="2", K3_MINI_PID=pidf)
        self.assertEqual(rc, 1, out[-1500:])
        self.assertIn("K3_STEP_TIMEOUT", out)
        self.assertLess(secs, 30)
        self.assert_gone(pidf)
        rc, out, secs = self.sh(d, "-j", "2", "--parts", "unit", "--units", "tests.test_hang", K3_UNIT_TIMEOUT="3",
                                K3_MINI_PID=pidf)
        self.assertEqual(rc, 1, out[-1500:])
        self.assertIn("tests.test_hang", out)
        self.assertLess(secs, 40)
        self.assert_gone(pidf)

    def test_term_and_int_stop_it_at_once(self):
        """З4 (REV-speed-2): TERM or INT to k3_test.sh stops it at once, not after the unit step: 143 / 130, the
        module and what it started are gone (-j 2 — the runner, -j 1 — unittest in one process)."""
        d = self.mini()
        pidf = os.path.join(d, "child.pid")
        for j, sig, code in (("2", signal.SIGTERM, 143), ("1", signal.SIGTERM, 143), ("2", signal.SIGINT, 130)):
            e = dict(os.environ, XDG_CACHE_HOME=d, K3_PAR_LOCKS=d, K3_LAYOUT="0", K3_MINI_PID=pidf,
                     K3_UNIT_TIMEOUT="120")
            p = subprocess.Popen(["bash", os.path.join(d, "tools", "k3_test.sh"), "-j", j, "--parts", "unit",
                                  "--units", "tests.test_hang"], cwd=d, env=e, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, universal_newlines=True, preexec_fn=terminal_signals)
            for _ in range(300):
                if os.path.exists(pidf) and os.path.getsize(pidf):
                    break
                time.sleep(0.1)
            else:
                p.kill()
                self.fail("модуль не стартовал: %s" % p.communicate()[0][-1500:])
            time.sleep(0.3)
            t0 = time.time()
            p.send_signal(sig)
            out = p.communicate(timeout=60)[0]
            self.assertLess(time.time() - t0, 10, out[-1500:])
            self.assertEqual(p.returncode, code, out[-1500:])
            self.assertIn("[unit]: ОСТАНОВЛЕН (%s)" % sig.name[3:], out)            # З6: a line, not silence
            self.assert_gone(pidf + ".mod")
            self.assert_gone(pidf)

    def test_select_run_cut_by_a_signal(self):
        """З6 (REV-speed-3): a run cut by a signal — a line of test_select and 128+N, not 241 without a summary; a
        signal to test_select goes to the whole group of the run and it waits for the group."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = sel.run_cmd(["bash", "-c", "kill -TERM $$"])              # quiet.sh killed by a foreign pkill
        self.assertEqual(rc, 143)
        self.assertIn("оборван (SIGTERM)", buf.getvalue())
        with redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(sel.run_cmd(["bash", "-c", "exit 130"]), 130)   # k3_test.sh stopped by its trap
        self.assertIn("оборван (SIGINT)", buf.getvalue())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(sel.run_cmd(["bash", "-c", "exit 1"]), 1)
        # П3-б (REV-ns-p3a З-1): a green run with a key in its own K3_TR_STRICT_LOG — red, the key said; the file gone
        with mock.patch.dict(os.environ, {"K3_TR_STRICT": "1"}), redirect_stdout(io.StringIO()) as buf:
            os.environ.pop("K3_TR_STRICT_LOG", None)
            self.assertEqual(sel.run_cmd(["bash", "-c", 'echo "x.key: {n}" >> "$K3_TR_STRICT_LOG"']), 1)
            self.assertNotIn("K3_TR_STRICT_LOG", os.environ)
            self.assertEqual(sel.run_cmd(["bash", "-c", "exit 0"]), 0)
        self.assertIn("x.key: {n}", buf.getvalue())
        d = tempfile.mkdtemp(prefix="k3sel-")
        self.addCleanup(shutil.rmtree, d, True)
        pidf = os.path.join(d, "child.pid")
        code = ("import sys; sys.path.insert(0, %r); import test_select as t; "
                "sys.exit(t.run_cmd(['bash', '-c', 'sleep 60 & echo $! > %s; wait']))" % (os.path.join(ROOT, "tools"),
                                                                                         pidf))
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True)
        for _ in range(100):
            if os.path.exists(pidf) and os.path.getsize(pidf):
                break
            time.sleep(0.1)
        p.send_signal(signal.SIGTERM)
        out = p.communicate(timeout=30)[0]
        self.assertEqual(p.returncode, 143, out)
        self.assertIn("оборван (SIGTERM)", out)
        self.assert_gone(pidf)

    def started(self, pidf, p, ends=(".mod", ".step")):
        for _ in range(300):
            if all(os.path.exists(pidf + x) and os.path.getsize(pidf + x) for x in ends):
                return
            time.sleep(0.1)
        p.kill()
        self.fail("модуль или шаг не стартовал: %s" % p.communicate()[0][-1500:])

    def assert_tree_gone(self, d, pidf):
        """The detached processes of the module and of the step and every process with the tree in its command."""
        for x in (".mod", ".step"):
            if os.path.exists(pidf + x):
                self.assert_gone(pidf + x)
        # errors="replace": ps режет длинную команду чужого процесса по байтам — посреди буквы кириллицы (UnicodeDecodeError)
        for _ in range(50):
            left = [ln for ln in subprocess.run(["ps", "-A", "-o", "pid=,command="], stdout=subprocess.PIPE,
                                                universal_newlines=True, errors="replace").stdout.splitlines() if d in ln]
            if not left:
                return
            time.sleep(0.1)
        for ln in left:
            try:
                os.kill(int(ln.split()[0]), 9)
            except (OSError, ValueError):
                pass
        self.fail("живы после остановки: %s" % left)

    def test_stop_takes_detached_processes(self):
        """REV-speed-4 С8, З7: Ctrl-C (INT to the group of the terminal), TERM or HUP to the group, a foreign
        «pkill -f tools/k3_test.sh» — no process of the run is left: a module and a step beside it with a process in a
        session of its own (headless Chrome «detached»), the runner; the module is «снят», not a failure."""
        d = self.mini(SH_DETACH)
        pidf = os.path.join(d, "child.pid")
        e = dict(os.environ, XDG_CACHE_HOME=d, K3_PAR_LOCKS=d, K3_LAYOUT="0", K3_MINI_PID=pidf, K3_UNIT_TIMEOUT="120")
        k3 = os.path.join(d, "tools", "k3_test.sh")
        args = ["--parts", "unit,egress_parser", "--units", "tests.test_detach"]
        sel_code = ("import sys; sys.path.insert(0, %r); import test_select as t; sys.exit(t.run_cmd(sys.argv[1:]))"
                    % os.path.join(ROOT, "tools"))
        for j, how, code in (("2", signal.SIGINT, 130), ("2", signal.SIGTERM, 143), ("2", signal.SIGHUP, 129),
                             ("2", "pkill", None), ("1", signal.SIGINT, 130), ("2", "select-INT", 130),
                             ("2", "select-HUP", 129), ("2", "ignored-INT", 130)):
            for x in (".mod", ".step"):          # what a failed case before left
                try:
                    with open(pidf + x) as f:
                        os.kill(int(f.read().strip()), signal.SIGKILL)
                except (OSError, ValueError):
                    pass
                if os.path.exists(pidf + x):
                    os.remove(pidf + x)
            with self.subTest(j=j, how=str(how)):
                cmd = ["bash", k3, "-j", j] + args
                if str(how).startswith("select"):  # test_select --run: the signal to test_select, not to the group
                    cmd = [sys.executable, "-c", sel_code] + cmd
                # «ignored-»: k3_test.sh got INT ignored (started in the background by a script) and cannot trap
                # it — the steps stop on the signal to the group, the run says ОСТАНОВЛЕН, not FAILURES (HUP ignored —
                # nohup: the run goes on, test_nohup_run_outlives_hup)
                pre = terminal_signals
                if str(how).startswith("ignored"):
                    how = getattr(signal, "SIG" + how[8:])
                    pre = (lambda: [signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGHUP)])
                p = subprocess.Popen(cmd, cwd=d, env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     universal_newlines=True, start_new_session=True, preexec_fn=pre)
                # -j 1: the step runs after the unit tests — only the module is there
                self.started(pidf, p, (".mod",) if j == "1" else (".mod", ".step"))
                time.sleep(0.3)
                if str(how).startswith("select"):
                    how = getattr(signal, "SIG" + how[7:])
                    p.send_signal(how)
                elif how == "pkill":              # what «pkill -f tools/k3_test.sh» does, on this tree only
                    for ln in subprocess.run(["ps", "-A", "-o", "pid=,command="], stdout=subprocess.PIPE,
                                             universal_newlines=True, errors="replace").stdout.splitlines():
                        if k3 in ln:
                            os.kill(int(ln.split()[0]), signal.SIGTERM)
                else:
                    os.killpg(p.pid, how)
                try:
                    out = p.communicate(timeout=60)[0]
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    out = p.communicate()[0]
                    self.assert_tree_gone(d, pidf)
                    self.fail("не остановился за 60 с: %s" % out[-1500:])
                if code is not None:
                    self.assertEqual(p.returncode, code, out[-1500:])
                    self.assertIn("ОСТАНОВЛЕН (%s)" % how.name[3:], out)
                    if cmd[0] == sys.executable:
                        self.assertIn("оборван (%s)" % how.name, out)
                self.assertNotIn("FAIL tests.test_detach", out)
                self.assert_tree_gone(d, pidf)

    def test_nohup_run_outlives_hup(self):
        """REV-speed-5 З8: started by «nohup» (HUP ignored), the run outlives a closed terminal or a dropped SSH (HUP
        to its group): the module and the step go on, the run reaches ALL GREEN — -j 2 and -j 1."""
        d = self.mini(SH_SLOW)
        pidf = os.path.join(d, "child.pid")
        e = dict(os.environ, XDG_CACHE_HOME=d, K3_PAR_LOCKS=d, K3_LAYOUT="0", K3_MINI_PID=pidf, TMPDIR=d)
        for j in ("2", "1"):
            for x in (".mod", ".step"):
                if os.path.exists(pidf + x):
                    os.remove(pidf + x)
            with self.subTest(j=j):
                p = subprocess.Popen(["bash", os.path.join(d, "tools", "k3_test.sh"), "-j", j, "--parts",
                                      "unit,egress_parser", "--units", "tests.test_slow"], cwd=d, env=e,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                                     start_new_session=True,
                                     preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))
                self.started(pidf, p, (".mod",) if j == "1" else (".mod", ".step"))
                time.sleep(0.3)
                os.killpg(p.pid, signal.SIGHUP)
                try:
                    out = p.communicate(timeout=60)[0]
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    out = p.communicate()[0]
                    self.fail("не закончился за 60 с: %s" % out[-1500:])
                self.assertEqual(p.returncode, 0, out[-1500:])
                self.assertIn("ALL GREEN", out)
                self.assertNotIn("ОСТАНОВЛЕН", out)
                self.assert_tree_gone(d, pidf)

    def wait_tree_gone(self, d, pidf, secs=20):
        """assert_tree_gone after up to secs seconds: a stop by the sentinel takes a TERM and its grace."""
        end = time.time() + secs
        while time.time() < end:
            if not [ln for ln in subprocess.run(["ps", "-A", "-o", "pid=,command="], stdout=subprocess.PIPE,
                                                universal_newlines=True, errors="replace").stdout.splitlines() if d in ln]:
                break
            time.sleep(0.2)
        self.assert_tree_gone(d, pidf)

    def test_kill9_leaves_no_step(self):
        """REV-speed-5 З9: KILL is not caught. KILL to the whole group of the run (kill -9 -PGID) or to k3_test.sh
        only — no process of the run is left: the runner, the module and the step with their processes in sessions of
        their own (the sentinel of k3_proc, the runner watching its parent, k3_proc watching $K3_TEST_PID)."""
        d = self.mini(SH_DETACH)
        pidf = os.path.join(d, "child.pid")
        e = dict(os.environ, XDG_CACHE_HOME=d, K3_PAR_LOCKS=d, K3_LAYOUT="0", K3_MINI_PID=pidf, K3_UNIT_TIMEOUT="120",
                 TMPDIR=d)
        for j, how in (("2", "group"), ("1", "group"), ("2", "k3"), ("1", "k3")):
            with self.subTest(j=j, how=how):
                with open(os.path.join(d, "out.log"), "w") as log:
                    p = subprocess.Popen(["bash", os.path.join(d, "tools", "k3_test.sh"), "-j", j, "--parts",
                                          "unit,egress_parser", "--units", "tests.test_detach"], cwd=d, env=e,
                                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                         preexec_fn=terminal_signals)
                self.started(pidf, p, (".mod",) if j == "1" else (".mod", ".step"))
                time.sleep(0.5)
                if how == "group":
                    os.killpg(p.pid, signal.SIGKILL)
                else:
                    os.kill(p.pid, signal.SIGKILL)
                p.wait(timeout=10)
                self.wait_tree_gone(d, pidf)

    def test_tstp_freezes_the_steps(self):
        """REV-speed-5: Ctrl-Z (TSTP to the group) freezes the steps in sessions of their own with what they started —
        the module and the step beside it; CONT (fg) — on; TERM then stops it all."""
        d = self.mini(SH_DETACH)
        pidf = os.path.join(d, "child.pid")
        e = dict(os.environ, XDG_CACHE_HOME=d, K3_PAR_LOCKS=d, K3_LAYOUT="0", K3_MINI_PID=pidf, K3_UNIT_TIMEOUT="120",
                 TMPDIR=d)
        p = subprocess.Popen(["bash", os.path.join(d, "tools", "k3_test.sh"), "-j", "2", "--parts",
                              "unit,egress_parser", "--units", "tests.test_detach"], cwd=d, env=e,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                             start_new_session=True, preexec_fn=terminal_signals)
        self.started(pidf, p)
        pids = []
        for x in (".mod", ".step"):
            with open(pidf + x) as f:
                pids.append(int(f.read().strip()))

        def states():
            return [subprocess.run(["ps", "-o", "stat=", "-p", str(x)], stdout=subprocess.PIPE,
                                   universal_newlines=True).stdout.strip()[:1] for x in pids]

        def wait_for(want):
            for _ in range(50):
                if all(st == want if want == "T" else st not in ("T", "") for st in states()):
                    return True
                time.sleep(0.1)
            return False

        try:
            time.sleep(0.3)
            os.killpg(p.pid, signal.SIGTSTP)
            self.assertTrue(wait_for("T"), "TSTP: процессы шагов не остановлены: %s" % states())
            os.killpg(p.pid, signal.SIGCONT)
            self.assertTrue(wait_for("run"), "CONT: процессы шагов не продолжены: %s" % states())
            os.killpg(p.pid, signal.SIGTERM)
            out = p.communicate(timeout=60)[0]
            self.assertEqual(p.returncode, 143, out[-1500:])
        finally:
            for x in pids:
                try:
                    os.kill(x, signal.SIGCONT)
                except OSError:
                    pass
            try:
                os.killpg(p.pid, signal.SIGCONT)
            except OSError:
                pass
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.communicate()
        self.assert_tree_gone(d, pidf)

    def test_runner_stops_when_its_parent_is_gone(self):
        """REV-speed-5 З9: the parent of k3_unit_par killed (KILL to k3_proc) — the runner stops its modules with
        their processes and starts no more, not an orphan running the whole queue."""
        d = self.mini()
        pidf = os.path.join(d, "child.pid")
        e = dict(os.environ, XDG_CACHE_HOME=d, K3_PAR_LOCKS=d, K3_MINI_PID=pidf, TMPDIR=d)
        with open(os.path.join(d, "out.log"), "w") as log:
            p = subprocess.Popen(["sh", "-c", 'python3 tools/k3_unit_par.py -j 1 --no-map tests.test_hang '
                                  'tests.test_good; exit 0'], cwd=d, env=e, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        for _ in range(300):
            if os.path.exists(pidf) and os.path.getsize(pidf):
                break
            time.sleep(0.1)
        else:
            os.killpg(p.pid, signal.SIGKILL)
            self.fail("модуль не стартовал")
        os.kill(p.pid, signal.SIGKILL)
        p.wait(timeout=10)
        self.assert_gone(pidf + ".mod")
        self.assert_gone(pidf)
        self.wait_tree_gone(d, pidf, secs=10)

    def test_select_run_kills_what_outlived_the_run(self):
        """REV-speed-4: the group of the run still there 15 s after it ended — killed and said so; a green run."""
        d = tempfile.mkdtemp(prefix="k3sel-")
        self.addCleanup(shutil.rmtree, d, True)
        pidf = os.path.join(d, "child.pid")
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch.object(sel.time, "sleep", lambda s: None):
            rc = sel.run_cmd(["bash", "-c", "sleep 60 & echo $! > %s" % pidf])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("не завершились за 15 с — сняты", buf.getvalue())
        self.assert_gone(pidf)

    def assert_gone(self, pidf):
        with open(pidf) as f:
            pid = int(f.read().strip())
        os.remove(pidf)
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.1)
        os.kill(pid, 9)
        self.fail("процесс %d шага пережил его срок" % pid)

    def test_modes(self):
        src = read("tools/k3_test.sh")
        self.assertIn('--quick) PARTS="compat,bashn,payload"', src)
        self.assertIn("python3 tools/k3_unit_par.py -j", src)
        self.assertIn("unittest discover -s tests -t . -q", src)        # -j 1: the old way, one process
        r = subprocess.run(["bash", os.path.join(ROOT, "tools", "k3_test.sh"), "-j", "x"], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()

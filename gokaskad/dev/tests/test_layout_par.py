"""R-speed-2 (31.10): the layout of the panel in parts beside each other, and only the groups of views a change touches.

* tools/k3_layout_check.mjs, K3_LAYOUT_JOBS=N — N processes with a Chrome each (K3_SHARD=i/N): the same views as one
  process, one summary line; a breakage in one part fails the whole; a part gone without a summary — a FAIL; every part
  without Chrome — SKIP; a wrong K3_SHARD / K3_LAYOUT_JOBS — an error, not the whole set silently.
* K3_LAYOUT_MAP_OUT — what every group of views needs (.js loaded, .css rules matching an element of a view);
  tools/k3_test.sh merges it into the map (tools/test_select.py --merge-layout-map: a union per repository).
* tools/test_select.py — a change of a .css/.js of the panel: K3_PAGES of the groups the changed rules (or the file)
  touch; a shared file, an unknown or a new one, no map — the whole layout; tools/k3_test.sh gives the groups to the
  check as K3_PAGES (K3_LAYOUT_PAGES).
"""

# k3-select: dirs=lib/kaskad/panel/static — копия статики панели с внесённой поломкой (PartsInChromeTest)
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "k3_layout_check.mjs")
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CHROMES = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium",
           "/usr/bin/chromium-browser")
CHROME = next((p for p in ((os.environ.get("CHROME") or None),) + CHROMES if p and os.path.exists(p)), None)


def load(name):
    spec = importlib.util.spec_from_file_location("k3tool_lp_" + name, os.path.join(ROOT, "tools", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sel = load("test_select")


def node_ok():
    try:
        out = subprocess.run(["node", "-e", "process.stdout.write(String(typeof WebSocket))"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=20).stdout
        return out == b"function"
    except (OSError, subprocess.SubprocessError):
        return False


NODE = node_ok()


def check(*args, **env):
    e = dict(os.environ, **env)
    for k in ("K3_PAGES", "K3_SHARD", "K3_LAYOUT_JOBS", "K3_LAYOUT_MAP_OUT", "K3_LAYOUT_STATIC", "K3_FONT"):
        if k not in env:
            e.pop(k, None)
    r = subprocess.run(["node", TOOL] + list(args), env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=600)
    return r.returncode, r.stdout


def fails(out):
    return sorted(x for x in out.splitlines() if x.startswith("FAIL"))


@unittest.skipUnless(NODE, "нет node ≥ 22 (WebSocket): части вёрстки не проверить")
class PartsNoChromeTest(unittest.TestCase):
    def test_wrong_shard_or_jobs_is_an_error(self):
        for env in ({"K3_SHARD": "3/3"}, {"K3_SHARD": "x"}, {"K3_SHARD": "0/0"}, {"K3_LAYOUT_JOBS": "0"},
                    {"K3_LAYOUT_JOBS": "two"}):
            rc, out = check("1440", "light", "ru", CHROME="/bin/true", **env)
            if out.startswith("SKIP"):
                self.skipTest(out.strip())        # no Chrome at all: the tool stops before the parts
            self.assertEqual(rc, 2, (env, out))
            self.assertTrue(out.startswith("FAIL K3_"), (env, out))

    def test_every_part_without_chrome_is_a_skip(self):
        d = tempfile.mkdtemp(prefix="k3lp-")
        self.addCleanup(shutil.rmtree, d, True)
        fake = os.path.join(d, "chrome")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\necho 'нет дисплея' >&2\nexit 1\n")
        os.chmod(fake, 0o755)
        tmp = os.path.join(d, "tmp")
        os.makedirs(tmp)
        for jobs in ("1", "3"):
            rc, out = check("1440", "light", "ru", CHROME=fake, K3_PAGES="find", K3_LAYOUT_JOBS=jobs, TMPDIR=tmp)
            self.assertEqual(rc, 77, out)
            self.assertTrue(out.strip().startswith("SKIP: Chrome завершился"), out)
            self.assertEqual(len(out.strip().splitlines()), 1, out)
            self.assertEqual(profiles(tmp), [], jobs)               # REV-speed-2b С3: a SKIP leaves no profile


def profiles(tmp):
    return sorted(x for x in os.listdir(tmp) if x.startswith("k3-layout-"))


class ProfileCleanupSourceTest(unittest.TestCase):
    def test_every_chrome_profile_is_removed(self):
        """REV-speed-2b С3: a tool making a profile of Chrome in $TMPDIR (up to 170 MB each, 6 a full run) removes it
        — 312 were left by k3_layout_check.mjs alone."""
        import re
        bad = []
        for top in (os.path.join(ROOT, "tools"), os.path.join(ROOT, "tests")):
            for d, dirs, fs in os.walk(top):
                for f in fs:
                    if not f.endswith(".mjs"):
                        continue
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        src = fh.read()
                    m = re.search(r"const (\w+) = (?:await )?mkdtemp(?:Sync)?\(", src)
                    if m and not re.search(r"\brm(?:Sync)?\(%s\b" % m.group(1), src):
                        bad.append(os.path.relpath(os.path.join(d, f), ROOT))
        self.assertEqual(bad, [])


@unittest.skipUnless(NODE and CHROME, "нет node ≥ 22 или Chrome: части вёрстки не проверить в Chrome")
class PartsInChromeTest(unittest.TestCase):
    """K3_PAGES=find на 1440, светлая, RU: три «загрузки» (пустой запрос, найдено, ничего не нашлось) — по одной на часть
    при K3_LAYOUT_JOBS=3."""

    ARGS = ("1440", "light", "ru")

    def test_parts_check_the_same_views_and_a_breakage_in_one_fails_the_whole(self):
        d = tempfile.mkdtemp(prefix="k3lp-")
        self.addCleanup(shutil.rmtree, d, True)
        rc1, out1 = check(*self.ARGS, K3_PAGES="find", K3_LAYOUT_MAP_OUT=os.path.join(d, "m1.json"))
        rc3, out3 = check(*self.ARGS, K3_PAGES="find", K3_LAYOUT_JOBS="3", K3_LAYOUT_MAP_OUT=os.path.join(d, "m3.json"))
        self.assertEqual((rc1, out1.strip()), (0, "LAYOUT OK: 3 видов"), out1)
        self.assertEqual((rc3, out3.strip()), (0, "LAYOUT OK: 3 видов"), out3)
        with open(os.path.join(d, "m1.json"), encoding="utf-8") as f:
            m1 = json.load(f)
        with open(os.path.join(d, "m3.json"), encoding="utf-8") as f:
            m3 = json.load(f)
        # the map of the parts — as of one process (a passing element, a toast, may be seen by one run only)
        for m in (m1, m3):
            self.assertFalse(m["full"])                       # K3_PAGES: not the whole set
            self.assertEqual(sorted(m["groups"]), ["find"])
            self.assertIn("find.css", m["groups"]["find"])
            self.assertIn("find", m["rules"]["find.css"][".gx-find__empty"])
            self.assertEqual(m["rules"]["pset.css"][".gx-pset"], [])
        self.assertEqual(sorted(m1["rules"]), sorted(m3["rules"]))
        self.assertEqual([x for x in os.listdir(d) if x.startswith("m3.json.")], [])     # the files of the parts — gone
        # a breakage only in «nothing found» — only the third part has it
        st = os.path.join(d, "static")
        shutil.copytree(STATIC, st)
        with open(os.path.join(st, "find.css"), "a", encoding="utf-8") as f:
            f.write("\n.gx-find__empty { display: block; width: 24px; overflow: hidden; white-space: nowrap; }\n")
        rc1, out1 = check(*self.ARGS, K3_PAGES="find", K3_LAYOUT_STATIC=st)
        rc3, out3 = check(*self.ARGS, K3_PAGES="find", K3_LAYOUT_STATIC=st, K3_LAYOUT_JOBS="3")
        self.assertEqual(rc1, 1, out1)
        self.assertEqual(rc3, 1, out3)
        self.assertTrue(fails(out1) and all("find=zzqqxx" in x for x in fails(out1)), out1)
        self.assertEqual(fails(out3), fails(out1))
        self.assertEqual(out3.strip().splitlines()[-1], out1.strip().splitlines()[-1])     # «LAYOUT: N поломок на 3 видах»
        parts = [check(*self.ARGS, K3_PAGES="find", K3_LAYOUT_STATIC=st, K3_SHARD="%d/3" % i)[0] for i in range(3)]
        self.assertEqual(parts, [0, 0, 1])                    # one part red — the whole run red

    def test_part_without_chrome_beside_the_others_fails(self):
        """REV-speed-2b С2: Chrome of one part did not start (also on its retry) while the others checked their
        views — those views are not checked: FAIL and code 1, not «SKIP» that k3_test.sh took for green."""
        d = tempfile.mkdtemp(prefix="k3lp-")
        self.addCleanup(shutil.rmtree, d, True)
        fake = os.path.join(d, "chrome")
        with open(fake, "w") as f:
            f.write('#!/bin/sh\n[ "$K3_SHARD" = 1/3 ] && { echo "нет памяти" >&2; exit 1; }\nexec "%s" "$@"\n' % CHROME)
        os.chmod(fake, 0o755)
        tmp = os.path.join(d, "tmp")
        os.makedirs(tmp)
        rc, out = check(*self.ARGS, K3_PAGES="find", K3_LAYOUT_JOBS="3", CHROME=fake, TMPDIR=tmp)
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL часть 1/3: Chrome завершился", out)
        self.assertIn("её виды не проверены", out)
        self.assertEqual(out.strip().splitlines()[-1], "LAYOUT: 1 поломок на 2 видах", out)
        self.assertEqual(profiles(tmp), [])                    # С3: no profile left by any part

    def test_profiles_removed_after_a_run_and_on_term(self):
        """REV-speed-2b С3: the profile of Chrome of every part is removed — after the summary and when the run is
        stopped by TERM in the middle (the parent stops its parts, each removes its own)."""
        import signal
        import time
        d = tempfile.mkdtemp(prefix="k3lp-")
        self.addCleanup(shutil.rmtree, d, True)
        rc, out = check(*self.ARGS, K3_PAGES="find", K3_LAYOUT_JOBS="2", TMPDIR=d)
        self.assertEqual(rc, 0, out)
        self.assertEqual(profiles(d), [])
        e = dict(os.environ, TMPDIR=d, K3_LAYOUT_JOBS="2", K3_PAGES="pset")
        for k in ("K3_SHARD", "K3_LAYOUT_MAP_OUT", "K3_LAYOUT_STATIC"):
            e.pop(k, None)
        p = subprocess.Popen(["node", TOOL] + list(self.ARGS), env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            end = time.time() + 60          # both parts have their Chrome up (DevToolsActivePort in the profile)
            while time.time() < end and sum(os.path.exists(os.path.join(d, x, "DevToolsActivePort"))
                                            for x in profiles(d)) < 2:
                time.sleep(0.2)
            self.assertEqual(len(profiles(d)), 2, "Chrome частей не поднялся за 60 с")
            time.sleep(1)
            p.send_signal(signal.SIGTERM)
            p.wait(timeout=30)
        finally:
            if p.poll() is None:
                p.kill()
            p.stdout.close()
        end = time.time() + 10
        while time.time() < end and profiles(d):
            time.sleep(0.2)
        self.assertEqual(profiles(d), [])


HEAD_STATIC = sel.static_blobs_at("HEAD")


def lmap(groups, rules, full=True, files=None):
    return {"v": 2, "repos": {sel.unit_par().repo_key(ROOT): {"full": full, "groups": groups, "rules": rules,
                                                              "files": HEAD_STATIC if files is None else files}}}


MAP = lmap({"overview": ["app.js", "pset.css", "charts.css"], "pset": ["app.js", "pset.css"],
            "settings?tab=addresses": ["app.js", "addresses.css", "pages/addresses.js"], "find": ["app.js", "find.css"]},
           {"pset.css": {".gx-pset": ["pset"], ".gx-pendhost:empty": ["overview", "pset"], ".gx-pset > .gx-unused": [],
                         '[data-device="phone"] .gx-pset__f': ["pset"]},
            "addresses.css": {".kk-adr-r": ["settings?tab=addresses"]}})
PSET = """.gx-pset { display: grid; gap: 8px; }
.gx-pendhost:empty { display: none; }
.gx-pset > .gx-unused { color: red; }
@media (max-width: 759.98px) {
  [data-device='phone'] .gx-pset__f { flex-direction: column; }
}
"""


class SelectByMapTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp(prefix="k3lp-")
        self.addCleanup(shutil.rmtree, d, True)
        self.map = os.path.join(d, "layout-map.json")
        self.write(MAP)
        p = mock.patch.dict(os.environ, {"K3_LAYOUT_MAP": self.map})
        p.start()
        self.addCleanup(p.stop)

    def write(self, doc):
        with open(self.map, "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def pick(self, rel, old, new):
        texts = {rel: (old, new)}
        s = sel.select([rel], "HEAD", old_text=lambda r: texts[r][0] if r in texts else None,
                       new_text=lambda r: texts[r][1] if r in texts else None)
        return s, s.layout_groups(s.layout_all)

    def test_changed_rule_only_its_groups(self):
        s, pages = self.pick("lib/kaskad/panel/static/pset.css", PSET, PSET.replace("gap: 8px", "gap: 12px"))
        self.assertIn("layout", s.parts)
        self.assertEqual(pages, ["pset"])
        # the phone rule inside @media, the source with other quotes and spaces than CSSOM
        s, pages = self.pick("lib/kaskad/panel/static/pset.css", PSET, PSET.replace("column", "row"))
        self.assertEqual(pages, ["pset"])
        # the condition of @media changed: the rules inside it count as changed
        s, pages = self.pick("lib/kaskad/panel/static/pset.css", PSET, PSET.replace("759.98", "899.98"))
        self.assertEqual(pages, ["pset"])
        s, pages = self.pick("lib/kaskad/panel/static/pset.css", PSET, PSET.replace("display: none", "display: block"))
        self.assertEqual(pages, ["overview", "pset"])

    def test_new_rule_or_at_block_the_whole_layout(self):
        """REV-speed-2b С1: every .css is linked from index.html and loaded on every view — a rule or a block the map
        cannot place may touch any view (a new rule in addresses.css broke «find»): the whole layout, not the groups
        of the file."""
        for new in (PSET + ".gx-new { color: red; }\n", PSET + "@keyframes k { to { opacity: 0; } }\n",
                    PSET + "@font-face { font-family: x; src: url(x.woff2); }\n", "body { width: 300px !important; }\n" + PSET,
                    PSET):                                      # the last: no diff to see (--files)
            s, pages = self.pick("lib/kaskad/panel/static/pset.css", PSET, new)
            self.assertIn("layout", s.parts, new)
            self.assertIsNone(pages, new)
        self.assertTrue(any("вся вёрстка" in r for r in s.parts["layout"]), s.parts["layout"])
        s, pages = self.pick("lib/kaskad/panel/static/addresses.css", ".kk-adr-r {}",
                             ".kk-adr-r {}\n.gx-find__empty { display: block; width: 24px; overflow: hidden; }")
        self.assertIsNone(pages)
        lines = PSET.splitlines(True)                           # the same rules in another order: the cascade may differ
        s, pages = self.pick("lib/kaskad/panel/static/pset.css", PSET, lines[1] + lines[0] + "".join(lines[2:]))
        self.assertEqual(pages, ["overview", "pset"])           # only between the rules of the file: its groups

    def test_rule_seen_on_no_view_the_whole_layout(self):
        """REV-speed-2b С8: the map is written at the checks — a rule of an open dialog, a hover, an action of
        «behave» may be seen on no view: «not known where», the whole layout (not «Chrome не нужен»)."""
        s, pages = self.pick("lib/kaskad/panel/static/pset.css", PSET, PSET.replace("color: red", "color: blue"))
        self.assertIn("layout", s.parts)
        self.assertIsNone(pages)
        self.assertFalse([n for n in s.notes if "Chrome не нужен" in n], s.notes)

    def test_map_of_another_static_the_whole_layout(self):
        """REV-speed-2b С8: the map keeps the blobs of the static of its whole run; a file outside the change that
        differs at the merge base (a merge, a pull) — the whole layout; the changed file itself may differ."""
        change = ("lib/kaskad/panel/static/pset.css", PSET, PSET.replace("gap: 8px", "gap: 12px"))
        g = MAP["repos"][sel.unit_par().repo_key(ROOT)]
        other = dict(HEAD_STATIC, **{"pset.css": "0" * 40})       # only the changed file differs: still the map
        self.write(lmap(g["groups"], g["rules"], files=other))
        self.assertEqual(self.pick(*change)[1], ["pset"])
        for files in (dict(HEAD_STATIC, **{"app.js": "0" * 40}), dict(HEAD_STATIC, **{"zz-gone.js": "0" * 40}),
                      {k: v for k, v in HEAD_STATIC.items() if k != "index.html"}, {}):
            self.write(lmap(g["groups"], g["rules"], files=files))
            s, pages = self.pick(*change)
            self.assertIsNone(pages, sorted(set(files) ^ set(HEAD_STATIC)))
            self.assertTrue(any("версии статики" in r for r in s.parts["layout"]), s.parts["layout"])
        doc = lmap(g["groups"], g["rules"])                      # a map written before R-speed-2b: no «files»
        del doc["repos"][sel.unit_par().repo_key(ROOT)]["files"]
        self.write(doc)
        self.assertIsNone(self.pick(*change)[1])

    def test_js_by_the_groups_loading_it(self):
        s, pages = self.pick("lib/kaskad/panel/static/pages/addresses.js", "a", "b")
        self.assertEqual(pages, ["settings?tab=addresses"])

    def test_the_whole_layout(self):
        for rel in ("lib/kaskad/panel/static/app.js", "lib/kaskad/panel/static/tokens.css",
                    "lib/kaskad/panel/static/components.css", "lib/kaskad/panel/static/apps/happ.png",
                    "lib/kaskad/panel/static/zz-new.css", "lib/kaskad/panel/static/setup/setup.css"):
            s, pages = self.pick(rel, "a {}", "a { color: red }")
            self.assertIn("layout", s.parts, rel)
            self.assertIsNone(pages, rel)
        s, pages = self.pick("lib/kaskad/panel/static/addresses.css", None, ".kk-adr-r {}")   # a new file
        self.assertIsNone(pages)
        # a file of every group — the whole layout too
        self.write(lmap({"pset": ["pset.css"], "overview": ["pset.css"]}, {"pset.css": {".gx-pset": ["pset", "overview"]}}))
        s, pages = self.pick("lib/kaskad/panel/static/pset.css", PSET, PSET.replace("gap: 8px", "gap: 12px"))
        self.assertIsNone(pages)

    def test_no_map_or_not_from_the_whole_set(self):
        for doc in (None, lmap(MAP["repos"][sel.unit_par().repo_key(ROOT)]["groups"], {}, full=False), {"v": 2}):
            if doc is None:
                os.unlink(self.map)
            else:
                self.write(doc)
            s, pages = self.pick("lib/kaskad/panel/static/addresses.css", ".kk-adr-r {}", ".kk-adr-r { color: red }")
            self.assertIn("layout", s.parts)
            self.assertIsNone(pages)
        with mock.patch.dict(os.environ, {"K3_LAYOUT_MAP": "0"}):
            s, pages = self.pick("lib/kaskad/panel/static/addresses.css", ".kk-adr-r {}", ".kk-adr-r { color: red }")
            self.assertIsNone(pages)

    def test_two_files_and_forced_layout(self):
        texts = {"lib/kaskad/panel/static/addresses.css": (".kk-adr-r {}", ".kk-adr-r { color: red }"),
                 "lib/kaskad/panel/static/pset.css": (PSET, PSET.replace("gap: 8px", "gap: 12px"))}
        old, new = (lambda r: texts[r][0] if r in texts else None), (lambda r: texts[r][1] if r in texts else None)
        s = sel.select(sorted(texts), "HEAD", old_text=old, new_text=new)
        self.assertEqual(s.layout_groups(s.layout_all), ["pset", "settings?tab=addresses"])
        s = sel.select(sorted(texts), "HEAD", layout_forced=True, old_text=old, new_text=new)
        self.assertIsNone(s.layout_groups(s.layout_all))

    def test_merge_is_a_union_and_full_stays(self):
        os.unlink(self.map)
        d = os.path.dirname(self.map)
        runs = [{"full": True, "groups": {"pset": ["pset.css"]}, "rules": {"pset.css": {".a": ["pset"], ".b": []}}},
                {"full": False, "groups": {"pset": ["app.js"], "find": ["find.css"]},
                 "rules": {"pset.css": {".b": ["find"]}}}]
        for i, r in enumerate(runs):
            p = os.path.join(d, "run%d.json" % i)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(r, f)
            self.assertEqual(sel.merge_layout_map(p), 0)
        m = sel.load_layout_map()
        self.assertTrue(m["full"])
        self.assertEqual(m["groups"], {"pset": ["app.js", "pset.css"], "find": ["find.css"]})
        self.assertEqual(m["rules"]["pset.css"], {".a": ["pset"], ".b": ["find"]})
        self.assertEqual(m["files"], sel.static_blobs(STATIC))    # the static the whole run checked
        self.assertEqual(sel.merge_layout_map(os.path.join(d, "nope.json")), 1)
        # REV-speed-2b С8: a whole run of the same static — a union; of another static — the map afresh
        p = os.path.join(d, "run2.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"full": True, "groups": {"tour": ["tour.css"]}, "rules": {}}, f)
        self.assertEqual(sel.merge_layout_map(p), 0)
        self.assertEqual(sorted(sel.load_layout_map()["groups"]), ["find", "pset", "tour"])
        st = os.path.join(d, "static")
        os.makedirs(st)
        with open(os.path.join(st, "app.js"), "w", encoding="utf-8") as f:
            f.write("x")
        with mock.patch.dict(os.environ, {"K3_LAYOUT_STATIC": st}):
            self.assertEqual(sel.merge_layout_map(p), 0)
        m = sel.load_layout_map()
        self.assertEqual((m["groups"], m["rules"]), ({"tour": ["tour.css"]}, {}))
        blob = subprocess.run(["git", "hash-object", os.path.join(st, "app.js")], stdout=subprocess.PIPE,
                              universal_newlines=True).stdout.strip()
        self.assertEqual(m["files"], {"app.js": blob})           # the sha of git: compared with «git ls-tree»


class BusyMachineTest(unittest.TestCase):
    """REV-speed-2b С9: test_select --run on a busy machine (load average over a third of the cores) — -j 6 and the
    layout in 3 parts; idle — -j 12; -j given or K3_LAYOUT_JOBS set — theirs."""

    def run_main(self, busy, *args, **env):
        got = {}

        def fake_run(cmd):
            got["cmd"], got["layout_jobs"] = cmd, os.environ.get("K3_LAYOUT_JOBS")
            return 0
        with mock.patch.dict(os.environ, dict({"K3_LAYOUT_MAP": "0"}, **env)), \
                mock.patch.object(sel, "busy", return_value=busy), mock.patch.object(sel, "run_cmd", fake_run), \
                mock.patch.object(sel, "quiet_sh", return_value=None), mock.patch("sys.stdout"):
            if "K3_LAYOUT_JOBS" not in env:
                os.environ.pop("K3_LAYOUT_JOBS", None)
            self.assertEqual(sel.main(list(args) + ["--run", "--files", "lib/kaskad/panel/static/addresses.css"]), 0)
        c = got["cmd"]
        return c[c.index("-j") + 1], got["layout_jobs"]

    def test_jobs_by_load(self):
        self.assertEqual(self.run_main(True), ("6", "3"))
        self.assertEqual(self.run_main(False), ("12", None))
        self.assertEqual(self.run_main(True, "-j", "4"), ("4", None))
        self.assertEqual(self.run_main(True, K3_LAYOUT_JOBS="6"), ("6", "6"))
        with mock.patch("os.getloadavg", return_value=(99.0, 0, 0)):
            self.assertTrue(sel.busy())
        with mock.patch("os.getloadavg", return_value=(0.0, 0, 0)):
            self.assertFalse(sel.busy())


class CssItemsTest(unittest.TestCase):
    def test_blocks(self):
        css = ("/* c { x } */ @import url(a.css); .a, .b > .c { color: red; }\n@media (min-width: 1px) { .d { x: 1 } "
               "@supports (gap: 1px) { .e { y: 2 } } }\n@keyframes k { from { opacity: 0 } }\n.f::before { content: '}'; }")
        self.assertEqual(sel.css_items(css), [
            ("at", (), "@import url(a.css)", ""), ("rule", (), ".a, .b > .c", "color: red;"),
            ("rule", ("@media (min-width: 1px)",), ".d", "x: 1"),
            ("rule", ("@media (min-width: 1px)", "@supports (gap: 1px)"), ".e", "y: 2"),
            ("at", (), "@keyframes k", "from { opacity: 0 }"), ("rule", (), ".f::before", "content: '}';")])
        self.assertEqual(sel.sel_key('[data-device="phone"] .x::before'), sel.sel_key("[data-device=phone]  .x:before"))


MINI_NODE = """#!/bin/sh
# a fake node: what tools/k3_test.sh gave the layout check
printf 'K3_PAGES=%s\\nK3_LAYOUT_JOBS=%s\\n' "${K3_PAGES:-}" "${K3_LAYOUT_JOBS:-}" > "$K3_FAKE_SEEN"
[ -n "${K3_FAKE_SKIP:-}" ] && { echo "SKIP: Chrome не ответил за 15 с — вёрстка панели не проверена"; exit 77; }
printf '{"full": %s, "groups": {"find": ["find.css"]}, "rules": {}}' "$( [ -n "${K3_PAGES:-}" ] && echo false || echo true )" \\
  > "$K3_LAYOUT_MAP_OUT"
echo "LAYOUT OK: 3 видов"
"""


class K3TestShLayoutTest(unittest.TestCase):
    """tools/k3_test.sh: K3_LAYOUT_JOBS (6 beside the unit tests, 1 with -j 1), K3_LAYOUT_PAGES → K3_PAGES, the map of the
    run merged into K3_LAYOUT_MAP — on a small tree with a fake node."""

    def test_layout_step_gives_jobs_pages_and_merges_the_map(self):
        d = tempfile.mkdtemp(prefix="k3lp-sh-")
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "tools"))
        os.makedirs(os.path.join(d, "bin"))
        for t in ("k3_test.sh", "k3_unit_par.py", "k3_proc.py", "test_select.py"):
            shutil.copy(os.path.join(ROOT, "tools", t), os.path.join(d, "tools", t))
        with open(os.path.join(d, "bin", "node"), "w") as f:
            f.write(MINI_NODE)
        os.chmod(os.path.join(d, "bin", "node"), 0o755)
        seen, lm = os.path.join(d, "seen"), os.path.join(d, "layout-map.json")
        base = dict(os.environ, PATH=os.path.join(d, "bin") + os.pathsep + os.environ["PATH"], XDG_CACHE_HOME=d,
                    K3_PAR_LOCKS=d, K3_FAKE_SEEN=seen, K3_LAYOUT_MAP=lm)
        for k in ("K3_LAYOUT", "K3_LAYOUT_JOBS", "K3_LAYOUT_PAGES", "K3_PAGES", "K3_TR_STRICT_LOG"):
            base.pop(k, None)

        def run(*args, **env):
            r = subprocess.run(["bash", os.path.join(d, "tools", "k3_test.sh")] + list(args), cwd=d, env=dict(base, **env),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
            with open(seen, encoding="utf-8") as f:
                return r.returncode, r.stdout, dict(x.split("=", 1) for x in f.read().splitlines())

        rc, out, got = run("-j", "2", "--parts", "layout", K3_LAYOUT_PAGES="pset,find")
        self.assertEqual(rc, 0, out)
        self.assertIn("LAYOUT OK: 3 видов", out)
        self.assertEqual(got, {"K3_PAGES": "pset,find", "K3_LAYOUT_JOBS": "6"})
        with open(lm, encoding="utf-8") as f:
            doc = json.load(f)
        repo = next(iter(doc["repos"].values()))
        self.assertEqual((repo["full"], repo["groups"]), (False, {"find": ["find.css"]}))
        rc, out, got = run("-j", "1", "--parts", "layout")
        self.assertEqual(rc, 0, out)
        self.assertEqual(got, {"K3_PAGES": "", "K3_LAYOUT_JOBS": "1"})
        with open(lm, encoding="utf-8") as f:
            self.assertTrue(next(iter(json.load(f)["repos"].values()))["full"])
        rc, out, got = run("--parts", "layout", K3_LAYOUT_JOBS="3")
        self.assertEqual(got["K3_LAYOUT_JOBS"], "3")
        self.assertIn("(-j 12, вёрстка 3, установщик 3)", out)
        # REV-speed-2b С10: the layout narrowed by K3_LAYOUT_PAGES or a K3_PAGES left in the environment — said so
        self.assertNotIn("не весь набор", out)
        for env in ({"K3_LAYOUT_PAGES": "pset,find"}, {"K3_PAGES": "find"}):
            rc, out, got = run("-j", "2", "--parts", "layout", **env)
            want = "только виды K3_PAGES=%s — не весь набор" % list(env.values())[0]
            self.assertIn("  вёрстка: " + want, out)
            self.assertIn("\nlayout: " + want, out)
            self.assertEqual(rc, 0, out)
        # REV-speed-2b С2: the layout wanted and skipped — not «ALL GREEN»; K3_LAYOUT=0 — skipped on purpose, green
        rc, out, got = run("-j", "2", "--parts", "layout", K3_FAKE_SKIP="1")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("ALL GREEN", out)
        self.assertEqual(out.strip().splitlines()[-1], "K3 OFFLINE TESTS [layout]: ЗЕЛЕНО, НО ВЁРСТКА НЕ ПРОВЕРЕНА", out)
        self.assertIn("layout: SKIP — Chrome не ответил за 15 с", out)
        rc, out, got = run("-j", "2", "--parts", "layout", K3_FAKE_SKIP="1", K3_LAYOUT="0")
        self.assertEqual(out.strip().splitlines()[-1], "K3 OFFLINE TESTS [layout]: ALL GREEN", out)
        r = subprocess.run(["bash", os.path.join(d, "tools", "k3_test.sh"), "--parts", "layout"], cwd=d,
                           env=dict(base, K3_LAYOUT_JOBS="x"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=60)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("K3_LAYOUT_JOBS ждёт число", r.stdout)


if __name__ == "__main__":
    unittest.main()

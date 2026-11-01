#!/usr/bin/env python3
"""test_select.py — which tests of Kaskad 3 a change needs, and (with --run) run just them.

Usage:
    python3 tools/test_select.py [--base REF] [--run] [-j N] [--layout] [--files F ...] [--json]
      --base REF  — what the branch is compared with (default: the first of dev, origin/dev that exists);
                    the change = ``git diff --name-only REF...HEAD`` + uncommitted and untracked files
      --files F…  — these files instead of git (paths from the repository root)
      --run       — run the selection: ``tools/k3_test.sh --parts … --units …`` through k3/stand/quiet.sh; the code is
                    its code (0 green, 1 failures); a run cut by a signal — a line «прогон оборван» and 128+N
      -j N        — processes for the unit modules (default 12, on a busy machine — load average over a third of
                    the cores — 6 and K3_LAYOUT_JOBS=3; REV-speed-2b С9)
      --layout    — the panel layout in Chrome (tools/k3_layout_check.mjs) even if nothing of the panel changed
      --json      — the selection as JSON (for tools)
      --merge-layout-map FILE — (tools/k3_test.sh) the map of a layout run into the map of this repository

Printed: every changed file, then one line per reason — what was chosen and why. Rules:
    * a module of lib/kaskad or a test helper (tests/helpers.py …) → the test modules that call its functions by the
      map of the runs (tools/k3_unit_par.py writes it: ~/.cache/kaskad-tests/test-maps.json, per repository, a union
      of every run) and the test modules importing it themselves; a module the map does not know, a test module the
      map does not know, or no map at all → every test module importing it, directly or not (static ``import``
      analysis of lib/ and tests/, ``importlib.import_module("kaskad.…")`` and ``mock.patch("kaskad.…")`` included);
    * a deleted (or renamed — the diff is taken with --no-renames) module → the tests that imported it by the import
      graph of the base (``git show``), and by the map; no base to tell → everything;
    * any file → the test modules that read it as text (its name in quotes, «dir/name», or "dir", "name"); a helper
      of tests/ that reads it (tests/fakes_gw.py takes the parser out of install.sh) → every test importing the
      helper; a script of tests/ (.mjs, .sh) that names it → whoever reads that script;
    * any file → the tests going through a folder that holds it whole (os.walk, glob, listdir, «git grep»; directly,
      through a helper of tests/ or through a function of lib given the folder) — tools/k3_select_scan.py; what the
      source cannot place is marked in the test ``# k3-select: dirs=…``;
    * any file → the tests that read it by a name put together from parts (``read("give-i18n-%s.js" % lang)``,
      ``open(CAT % lang)``, f-strings, ``+``, a helper given the name; quoted names with a placeholder in any source
      of tests/ and the 2.x self-tests) — tools/k3_select_scan.py, Reads; a read the source cannot place is marked
      ``# k3-select: files=<pattern>,…``;
    * a module of lib/kaskad → the tests running the core in a process of their own (``-m kaskad.mcp``,
      [sys.executable, ".../lib/kaskad/x.py"], the imports of «-c» code) whose entry imports it, by the static
      imports — the map sees only the test's own process;
    * a data file of lib (vendor/*.json, mcp/*.md …) → as a change of the lib modules that name it;
    * the table of shared files: install.sh → the installer test and the 2.x self-tests (only the generated blocks
      K3_PAYLOAD/K3_I18N changed — the payload check); lib/kaskad/panel/static/* → test_panel*, the Chrome layout
      (setup/, user.html, give* too) — the layout only of the groups of views the change touches (R-speed-2:
      a .css — the groups of the changed rules by the map of the layout runs, ~/.cache/kaskad-tests/layout-map.json;
      a .js — the groups loading it; a shared file, any other, a new one, one the map does not know, a new rule or an
      at-block, a rule the map saw on no view, no map or a map of another static — all of it; see layout_groups);
      the catalogs lib/kaskad/i18n/** → test_i18n*, and the tests holding a
      changed key or its old text; tools/build_payload.py → the payload check; *.py → Python 3.8 compatibility.
    * an unknown file the rules cannot place (tests/__init__.py, tools/k3_test.sh …) → everything.
"""

import ast
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARTS_ORDER = ("compat", "bashn", "payload", "unit", "layout", "installer", "rules_adopt", "egress_parser",
               "egress_xray")
PART_NAMES = {
    "compat": "совместимость с Python 3.8",
    "bashn": "bash -n install.sh",
    "payload": "payload в install.sh актуален",
    "unit": "модули unittest",
    "layout": "вёрстка панели в Chrome",
    "installer": "установщик (tools/k3_install_test.sh)",
    "rules_adopt": "самотест 2.x tools/free_rules_adopt_test.sh",
    "egress_parser": "самотест 2.x tools/egress_parser_selftest.sh",
    "egress_xray": "самотест 2.x tools/egress_xray_selftest.sh",
}
SELFTESTS = {"tools/free_rules_adopt_test.sh": "rules_adopt", "tools/egress_parser_selftest.sh": "egress_parser",
             "tools/egress_xray_selftest.sh": "egress_xray", "tools/k3_install_test.sh": "installer"}
# the tools these steps take besides their own script (a change there — the step again)
STEP_TOOLS = {"tools/k3_layout_check.mjs": ("layout",), "tools/k3_install_par.py": ("installer",),
              "tools/k3_py_compat.py": ("compat",),
              "tools/build_payload.py": ("payload",), "tools/fetch_test_bins.sh": ("unit:all",),
              "tools/gen_mock_install.py": ()}
# meta files: they change how tests run, not what they check — their own tests come by the text rule
META = {"tools/test_select.py", "tools/k3_unit_par.py", "tools/k3_test.sh", "tools/k3_select_scan.py"}
STATIC_DIR = "lib/kaskad/panel/static/"
LAYOUT_STATIC = re.compile("^" + re.escape(STATIC_DIR))
PAYLOAD_BLOCK = re.compile(r"^# >>> K3_PAYLOAD.*?^# <<< K3_PAYLOAD[^\n]*\n", re.M | re.S)
I18N_BLOCK = re.compile(r"^# >>> K3_I18N.*?^# <<< K3_I18N[^\n]*\n", re.M | re.S)


def git(*args, check=True):
    r = subprocess.run(["git"] + list(args), cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True)
    if check and r.returncode != 0:
        raise SystemExit("test_select: git %s: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout if r.returncode == 0 else None


def default_base():
    for ref in ("dev", "origin/dev"):
        if git("rev-parse", "--verify", "-q", ref + "^{commit}", check=False):
            return ref
    return "HEAD"


def changed_files(base):
    # --no-renames: a renamed file is its old path (deleted) and its new one — both need their tests (REV-speed С2)
    files = set()
    out = git("diff", "--name-only", "--no-renames", base + "...HEAD")
    files.update(x for x in out.splitlines() if x)
    files.update(x for x in git("diff", "--name-only", "--no-renames", "HEAD").splitlines() if x)
    files.update(x for x in git("ls-files", "--others", "--exclude-standard").splitlines() if x)
    return sorted(files)


def base_text(base, rel):
    """The file as it is at the merge base (None — it did not exist)."""
    mb = git("merge-base", base, "HEAD", check=False)
    ref = mb.strip() if mb else base
    return git("show", "%s:%s" % (ref, rel), check=False)


def under(rel, folder):
    return folder == "" or rel == folder or rel.startswith(folder + "/")


def read(rel):
    try:
        with open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def compat_targets():
    """What the compat step of k3_test.sh hands to k3_py_compat.py (folders and files: «lib tests
    tools/k3_text_check.py») — a Python file there is checked by that step (REV-speed-joins П-2)."""
    m = re.search(r"k3_py_compat\.py((?:[ \t]+[\w./-]+)+)", read("tools/k3_test.sh") or "")
    return tuple(x.rstrip("/") for x in m.group(1).split()) if m else ("lib", "tests")


# ── the import graph ──────────────────────────────────────────────────────────────────────────────────────────

def py_files(top):
    out = {}
    for d, dirs, files in os.walk(os.path.join(ROOT, top)):
        dirs[:] = [x for x in dirs if x != "__pycache__" and not x.startswith(".")]
        for f in files:
            if f.endswith(".py"):
                full = os.path.join(d, f)
                rel = os.path.relpath(full, ROOT)
                mod = os.path.relpath(full, os.path.join(ROOT, "lib") if top == "lib" else ROOT)[:-3]
                mod = mod.replace(os.sep, ".")
                if mod.endswith(".__init__"):
                    mod = mod[:-len(".__init__")]
                out[mod] = rel
    return out


def mod_of(rel):
    """lib/kaskad/gw/ops.py → kaskad.gw.ops; tests/site/test_names.py → tests.site.test_names."""
    m = rel[len("lib/"):-3] if rel.startswith("lib/") else rel[:-3]
    m = m.replace("/", ".")
    return m[:-len(".__init__")] if m.endswith(".__init__") else m


class Graph:
    def __init__(self, sources=None):
        """The imports of lib/ and tests/: of the working tree, or of ``sources`` ({repo path: text} — another commit)."""
        self.files = {}                      # module -> repo-relative path
        if sources is None:
            self.files.update(py_files("lib"))   # kaskad.*
            self.files.update(py_files("tests"))  # tests.*
        else:
            self.files.update((mod_of(rel), rel) for rel in sources
                              if rel.endswith(".py") and rel.startswith(("lib/", "tests/")))
        self.by_file = {v: k for k, v in self.files.items()}
        self.edges = {m: set() for m in self.files}   # module -> modules it imports
        self.src = {}
        self.trees = {}
        for m, rel in self.files.items():
            text = (read(rel) if sources is None else sources.get(rel)) or ""
            self.src[m] = text
            self.edges[m] = self.imports(m, rel, text)
        self.rev = {m: set() for m in self.files}
        for m, deps in self.edges.items():
            for d in deps:
                self.rev.setdefault(d, set()).add(m)

    def known(self, name):
        """The longest existing module that ``name`` starts with (kaskad.gw.exits.probe → kaskad.gw.exits)."""
        parts = name.split(".")
        while parts:
            cand = ".".join(parts)
            if cand in self.files:
                return cand
            parts.pop()
        return None

    def imports(self, mod, rel, text):
        deps = set()
        is_pkg = rel.endswith("__init__.py")
        pkg = mod if is_pkg else mod.rpartition(".")[0]
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return deps
        self.trees[mod] = tree

        def add(name):
            k = self.known(name)
            if k and k != mod:
                deps.add(k)
                # importing a.b.c runs a/__init__ and a/b/__init__ too
                p = k.rpartition(".")[0]
                while p:
                    if p in self.files and p != mod:
                        deps.add(p)
                    p = p.rpartition(".")[0]

        dynamic = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    add(a.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = pkg.split(".")
                    if node.level > 1:
                        base = base[:len(base) - (node.level - 1)]
                    prefix = ".".join(base + ([node.module] if node.module else []))
                else:
                    prefix = node.module or ""
                add(prefix)
                for a in node.names:
                    add(prefix + "." + a.name)       # from kaskad.gw import exits — a module, not a name
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                v = node.value
                if re.match(r"^(kaskad|tests)(\.\w+)+$", v):
                    add(v)                            # import_module("kaskad.…"), mock.patch("kaskad.….f")
            elif isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                if name in ("import_module", "__import__"):
                    for sub in ast.walk(node):
                        if isinstance(sub, ast.Name) and sub.id in ("__name__", "__package__", "modname", "name"):
                            dynamic = True
        if dynamic:
            # import_module("%s.%s" % (__name__, x)) and the like: every module of this package may be loaded
            for other in self.files_under(pkg):
                if other != mod:
                    deps.add(other)
        return deps

    def files_under(self, pkg):
        return [m for m in getattr(self, "files", {}) if m.startswith(pkg + ".")]

    def importers(self, mod):
        """Every module that imports ``mod``, directly or not: {module: the chain (as text) of the first path}."""
        seen = {mod: mod.split(".")[-1]}
        queue = [mod]
        while queue:
            cur = queue.pop(0)
            for up in sorted(self.rev.get(cur, ())):
                if up not in seen:
                    seen[up] = seen[cur] + " ← " + up.split(".")[-1]
                    queue.append(up)
        seen.pop(mod)
        return seen


_GRAPH = []
_SCAN = []
_READS = []


def graph():
    if not _GRAPH:
        _GRAPH.append(Graph())
    return _GRAPH[0]


def scans():
    """The folders the tests and their helpers go through whole (tools/k3_select_scan.py)."""
    if not _SCAN:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        try:
            import k3_select_scan
        finally:
            sys.path.pop(0)
        g = graph()
        _SCAN.append(k3_select_scan.Scan(g.files, g.trees, g.src).run())
    return _SCAN[0]


def reads():
    """The files the tests and their helpers read by a name put together from parts, and the core they run in a
    process of their own (tools/k3_select_scan.py, REV-speed-2 С5, З5)."""
    if not _READS:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        try:
            import k3_select_scan
        finally:
            sys.path.pop(0)
        r = k3_select_scan.Reads(scans()).run()
        r.runs = r.core_runs()
        r.text_templates = k3_select_scan.text_templates
        _READS.append(r)
    return _READS[0]


def tree_sources(ref, tops=("lib", "tests")):
    """{repo path: text} of the .py files of ``tops`` at commit ``ref`` (None — git cannot tell)."""
    out = git("ls-tree", "-r", "--name-only", ref, "--", *tops, check=False)
    if out is None:
        return None
    rels = [x for x in out.splitlines() if x.endswith(".py")]
    r = subprocess.run(["git", "cat-file", "--batch"], cwd=ROOT, input="".join("%s:%s\n" % (ref, x) for x in rels)
                       .encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        return None
    data, pos, src = r.stdout, 0, {}
    for rel in rels:
        nl = data.index(b"\n", pos)
        head = data[pos:nl].split()
        pos = nl + 1
        if len(head) < 3 or head[1] != b"blob":
            continue
        size = int(head[2])
        src[rel] = data[pos:pos + size].decode("utf-8", "replace")
        pos += size + 1
    return src


def base_graph_of(base):
    """The import graph at the merge base with ``base`` (None — no such commit)."""
    mb = git("merge-base", base, "HEAD", check=False)
    src = tree_sources(mb.strip() if mb else base)
    return Graph(src) if src else None


# ── the selection ─────────────────────────────────────────────────────────────────────────────────────────────

class Selection:
    def __init__(self):
        self.units = {}     # test module -> [reasons]
        self.parts = {}     # part -> [reasons]
        self.everything = []
        self.notes = []     # files that need nothing
        self.layout_pages = {}       # group of views of the layout (K3_PAGES) -> [reasons] (R-speed-2)
        self.layout_narrow = set()   # the reasons of «layout» that come with their groups; any other — the whole layout
        self.layout_all = []         # every group of the map

    def layout_groups(self, all_groups=()):
        """K3_PAGES of the layout step: the groups, or None — the whole layout (a reason without groups, or every
        group of the map anyway)."""
        if "layout" not in self.parts or set(self.parts["layout"]) - self.layout_narrow:
            return None
        got = set(self.layout_pages)
        return None if all_groups and got >= set(all_groups) else sorted(got)

    def unit(self, mod, why):
        self.units.setdefault(mod, [])
        if why not in self.units[mod]:
            self.units[mod].append(why)

    def part(self, name, why):
        self.parts.setdefault(name, [])
        if why not in self.parts[name]:
            self.parts[name].append(why)


def unit_par():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        import k3_unit_par as par          # one list of modules and one map for the selection and the runner
        return par
    finally:
        sys.path.pop(0)


def test_modules():
    return unit_par().discover()


def text_patterns(rel):
    """How a test names a file it reads: "name.ext", 'name.ext', dir/name.ext, "dir", "name.ext"."""
    base = os.path.basename(rel)
    parent = os.path.basename(os.path.dirname(rel))
    pats = [re.escape('"%s"' % base), re.escape("'%s'" % base)]
    if parent:
        pats.append(re.escape("%s/%s" % (parent, base)))
        pats.append(r"""["']%s["']\s*,\s*["']%s["']""" % (re.escape(parent), re.escape(base)))
    if base in ("__init__.py",):
        pats = pats[2:]                       # «"__init__.py"» says nothing of which one
    return re.compile("|".join(pats))


def page_assets(page):
    """The static files a page of the panel loads (src/href relative to it; -ru files with their -en pair)."""
    text = read(STATIC_DIR + page) or ""
    here = os.path.dirname(page)
    out = {page}
    for ref in re.findall(r'(?:src|href)="([^":?#]+)', text):
        p = os.path.normpath(os.path.join(here, ref)).replace(os.sep, "/")
        out.add(p)
        if "-ru." in p:
            out.add(p.replace("-ru.", "-en."))
    return out


def chrome_units(sub):
    """The unit modules that open the panel's static in Chrome and load ``sub`` (a path under static/)."""
    # tools/k3_give_layout.mjs: the panel itself (index.html loads the rest of the folder as it goes) and the wizard
    yield "tests.test_333q_layout", "окно выдачи"
    # tests/setup/layout_scan.mjs: the wizard only (setup/index.html and what it loads)
    if sub.startswith("setup/") or sub in page_assets("setup/index.html"):
        yield "tests.test_qs333_layout", "раскладка мастера"


# R-speed-2: the layout of the panel — only the groups of views a change of the static touches (K3_PAGES of
# tools/k3_layout_check.mjs). The map: what every group of views needs, written by the layout step of tools/k3_test.sh
# (K3_LAYOUT_MAP_OUT: a .js the page loaded, the .css rules matching an element of a view) into
# ~/.cache/kaskad-tests/layout-map.json — per repository, a union of every run, as the map of the unit tests.
# A .css — the groups of the rules the diff changed (the same rules in another order — every group using the file);
# a .js — every group loading it. The whole layout (REV-speed-2b С1, С8): a shared file (LAYOUT_COMMON), any other kind
# of file, a file the map does not know, a new or deleted one, a rule the map does not know or saw on no view, an
# @keyframes, @font-face, @media … head, no diff to see, no map, none from a run of the whole set, a map of another
# static (its «files» — the blobs of the static of the last whole run — differ from the merge base outside the
# change), every group anyway. K3_LAYOUT_MAP=<file> — that map, K3_LAYOUT_MAP=0 — none (the whole layout).
LAYOUT_COMMON = {"tokens.css", "components.css", "styles.css", "mobile.css", "index.html", "boot.js", "app.js", "kit.js",
                 "ux.js", "templates.js", "icons.js", "i18n-ru.js", "i18n-en.js", "kit-i18n-ru.js", "kit-i18n-en.js",
                 "ux-i18n-ru.js", "ux-i18n-en.js", "dev-mock.js", "dev-mock-en.js"}
LAYOUT_NEST = ("@media", "@supports", "@container", "@layer", "@scope", "@document")


def layout_map_file():
    return os.environ.get("K3_LAYOUT_MAP") or os.path.join(unit_par().cache_dir(), "layout-map.json")


def load_layout_map():
    """{"full", "groups": {group: [static files]}, "rules": {css: {selector: [groups]}}} of this repository, or None:
    K3_LAYOUT_MAP=0, no map, or no run of the whole set (without K3_PAGES) in it yet."""
    if os.environ.get("K3_LAYOUT_MAP") == "0":
        return None
    try:
        with open(layout_map_file(), encoding="utf-8") as f:
            doc = json.load(f)
        m = doc["repos"][unit_par().repo_key(ROOT)]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(m, dict) or not m.get("full") or not isinstance(m.get("groups"), dict) or not m["groups"]:
        return None
    return m


def static_blobs(top):
    """{path under the static: its git blob sha} of the files of a static folder on disk (dot files aside) — the
    version of the static a layout run checked."""
    import hashlib
    out = {}
    for d, dirs, fs in os.walk(top):
        dirs[:] = [x for x in dirs if not x.startswith(".") and x != "__pycache__"]
        for f in fs:
            if f.startswith("."):
                continue
            p = os.path.join(d, f)
            try:
                with open(p, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            out[os.path.relpath(p, top).replace(os.sep, "/")] = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
    return out


def static_blobs_at(ref):
    """The same of lib/kaskad/panel/static at a commit (``git ls-tree``); None — git could not tell."""
    out = git("ls-tree", "-r", "-z", ref, "--", STATIC_DIR, check=False)
    if out is None:
        return None
    got = {}
    for rec in out.split("\0"):
        meta, _, path = rec.partition("\t")
        m = meta.split()
        if len(m) != 3 or m[1] != "blob" or not path.startswith(STATIC_DIR):
            continue
        sub = path[len(STATIC_DIR):]
        if not any(x.startswith(".") or x == "__pycache__" for x in sub.split("/")):
            got[sub] = m[2]
    return got


def layout_map_stale(lmap, base, files):
    """REV-speed-2b С8: why the map is not of this static (None — it is): the selection reads which groups a rule or a
    script reached when the map was taken; a file outside the change that differs between the static of the last whole
    run («files» of the map) and the merge base (a merge, a pull) may have moved a selector to other views."""
    have = lmap.get("files")
    if not isinstance(have, dict) or not have:
        return "в карте вёрстки нет версии статики (её пишет полный прогон tools/k3_test.sh)"
    mb = git("merge-base", base, "HEAD", check=False)
    ref = static_blobs_at(mb.strip() if mb else base)
    if ref is None:
        return "статику базы %s не прочесть из git" % base
    changed = {f[len(STATIC_DIR):] for f in files if f.startswith(STATIC_DIR)}
    for sub in sorted(set(ref) | set(have)):
        if sub not in changed and ref.get(sub) != have.get(sub):
            return "карта вёрстки снята с другой версии статики (%s) — до полного прогона tools/k3_test.sh" % sub
    return None


def merge_layout_map(path):
    """--merge-layout-map FILE (tools/k3_test.sh after the layout step): the map of that run into the map of this
    repository — a union under an flock (the selection only gets wider; «full» stays once a whole set ran). A whole run
    of another static (REV-speed-2b С8) starts the map afresh: its own groups and rules, «files» — the blobs of the
    static it checked (K3_LAYOUT_STATIC or the panel's)."""
    import fcntl
    try:
        with open(path, encoding="utf-8") as f:
            new = json.load(f)
        if not isinstance(new.get("groups"), dict):
            return 1
    except (OSError, ValueError, AttributeError):
        return 1
    if os.environ.get("K3_LAYOUT_MAP") == "0":
        return 0
    out = layout_map_file()
    try:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        lockf = open(out + ".lock", "w")
    except OSError:
        return 1
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            with open(out, encoding="utf-8") as f:
                doc = json.load(f)
            if not isinstance(doc, dict) or doc.get("v") != 2:
                doc = {}
        except (OSError, ValueError):
            doc = {}
        doc["v"] = 2
        repo = doc.setdefault("repos", {}).setdefault(unit_par().repo_key(ROOT), {})
        if new.get("full"):
            files = static_blobs(os.environ.get("K3_LAYOUT_STATIC") or os.path.join(ROOT, STATIC_DIR))
            if repo.get("files") != files:
                repo.clear()
                repo["files"] = files
        repo["full"] = bool(repo.get("full")) or bool(new.get("full"))
        groups = repo.setdefault("groups", {})
        for g, fs in new["groups"].items():
            groups[g] = sorted(set(groups.get(g) or ()) | set(fs))
        rules = repo.setdefault("rules", {})
        for f, sels in (new.get("rules") or {}).items():
            t = rules.setdefault(f, {})
            for s, gs in sels.items():
                t[s] = sorted(set(t.get(s) or ()) | set(gs))
        repo.update({"root": ROOT, "updated": time.strftime("%Y-%m-%d %H:%M")})
        tmp = out + ".%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, out)
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()
    return 0


def css_items(text):
    """The blocks of a stylesheet: ("rule", context, selector, body) — a style rule, its context the @media/@supports/
    @container/@layer around it; ("at", context, prelude, body) — anything else (@keyframes, @font-face, @import …).
    Whitespace collapsed: a change of formatting only is no change."""
    t = re.sub(r"/\*.*?\*/", "", text or "", flags=re.S)
    sp = lambda s: re.sub(r"\s+", " ", s).strip()
    out = []

    def end_of(j):                               # t[j] == "{" — the index of its «}»
        depth, q = 0, None
        for x in range(j, len(t)):
            c = t[x]
            if q:
                if c == q and t[x - 1] != "\\":
                    q = None
            elif c in "\"'":
                q = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return x
        return len(t)

    def walk(a, b, ctx):
        i = a
        while i < b:
            j = t.find("{", i, b)
            head = t[i:j if j >= 0 else b]
            *stmts, head = head.split(";")         # @import …; @charset …; before the block
            for s in stmts:
                if s.strip():
                    out.append(("at", ctx, sp(s), ""))
            if j < 0:
                if head.strip():
                    out.append(("at", ctx, sp(head), ""))
                return
            e = end_of(j)
            head = sp(head)
            if head.split(" ")[0].lower() in LAYOUT_NEST:
                walk(j + 1, e, ctx + (head,))
            elif head.startswith("@"):
                out.append(("at", ctx, head, sp(t[j + 1:e])))
            else:
                out.append(("rule", ctx, head, sp(t[j + 1:e])))
            i = e + 1

    walk(0, len(t), ())
    return out


def sel_key(s):
    """A selector as the map keeps it (CSSOM: «.a > .b», [x="y"], ::before) and as the source has it — compared loosely:
    without whitespace and quotes, «::» as «:», lower case (two selectors taken for one — only a wider selection)."""
    return re.sub(r"\s+|[\"']", "", s).replace("::", ":").lower()


def layout_groups(rel, texts, lmap):
    """The groups of views a change of the static file ``rel`` (under static/) touches — a set (empty: no view uses what
    changed), or None — the whole layout; the second value — why, for the reason line. ``texts()`` — the file before
    and after the change (None — it is not there), asked only for a .css or .js of the map."""
    if lmap is None:
        return None, "карты вёрстки нет (её пишет полный прогон tools/k3_test.sh)"
    if rel in LAYOUT_COMMON:
        return None, "общий файл панели"
    if not rel.endswith((".css", ".js")):
        return None, "не .css и не .js"
    old, new = texts()
    if old is None or new is None:
        return None, "новый или удалённый файл"
    groups = lmap["groups"]
    star = {g for g, fs in groups.items() if "*" in fs}        # a group the map could not learn — any change
    using = {g for g, fs in groups.items() if rel in fs} | star
    if not using - star:
        return None, "карта вёрстки не знает, где он нужен"
    if rel.endswith(".js"):
        return using, "загружен видами"
    rules = (lmap.get("rules") or {}).get(rel)
    a, b = css_items(old), css_items(new)
    # REV-speed-2b С1: every .css of the panel is linked from index.html and loaded on every view — what the map cannot
    # place (no rules of the file, no diff to see, a new rule, an at-block) may touch any view: the whole layout
    if rules is None:
        return None, "правил файла в карте нет"
    if a == b:
        return None, "разницы в правилах не видно"
    keys = {}
    for s, gs in rules.items():
        keys.setdefault(sel_key(s), set()).update(gs)
    from collections import Counter
    ca, cb = Counter(a), Counter(b)
    if ca == cb:        # the same rules in another order — the cascade between them may differ: the file whole
        return using, "файл целиком (другой порядок правил)"
    got, whole = set(star), []
    for kind, ctx, head, body in list((ca - cb).elements()) + list((cb - ca).elements()):
        k = sel_key(head)
        # a rule the map saw on no view (REV-speed-2b С8: the map is written at the checks only — an open dialog, a
        # hover, an action of «behave» may use it) — not «no view needs it», but «not known where»
        if kind == "rule" and keys.get(k):
            got |= keys[k]
        else:
            whole.append(head)
    if whole:
        return None, "%s «%s» — карта не знает, где %s" % (
            "блок" if whole[0].startswith("@") else "новое или не встреченное на видах правило", whole[0][:60],
            "он нужен" if whole[0].startswith("@") else "оно нужно")
    return got, "правила %s" % ", ".join(sorted({h[:40] for _, _, h, _ in list((ca - cb).elements()) +
                                                list((cb - ca).elements())})[:3])


def catalog_changes(old_text, rel):
    """The keys of a catalog (lib/kaskad/i18n/catalog/<lang>/*.json or i18n/sh/*.sh) whose text changed, and the old
    and new texts of them."""
    old, new = old_text(rel) or "", read(rel) or ""

    def load(text):
        if rel.endswith(".json"):
            try:
                d = json.loads(text) if text.strip() else {}
                return {k: v for k, v in d.items() if isinstance(v, str)} if isinstance(d, dict) else {}
            except ValueError:
                return {}
        out = {}
        for m in re.finditer(r"^M_[a-z]+_([A-Z0-9_]+)='((?:[^']|'\\'')*)'", text, re.M):
            out[m.group(1)] = m.group(2)
        return out

    a, b = load(old), load(new)
    keys = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    texts = []
    for k in keys:
        for v in (a.get(k), b.get(k)):
            if v:
                texts.append(v)
    return keys, texts


def text_chunks(v):
    """Pieces of a text a test would quote (the text split at its placeholders, ≥ 12 characters)."""
    out = []
    for piece in re.split(r"%[-0-9.]*[sdif]|\{[^}]*\}|\\n|\n", v):
        piece = piece.strip(" .,:;«»\"'—-")
        if len(piece) >= 12:
            out.append(piece)
    return out


def select(files, base, layout_forced=False, old_text=None, base_graph=None, new_text=None):
    """The selection for ``files`` (repo-relative); ``old_text(rel)`` — a file as it was before the change (default:
    at the merge base with ``base``); ``base_graph()`` — the import graph there (for deleted modules); ``new_text(rel)``
    — a static file of the panel as it is now (the groups of the layout; default: the file on disk)."""
    new_text = new_text or read
    old_text = old_text or (lambda rel: base_text(base, rel))
    base_graph = base_graph or (lambda: base_graph_of(base))
    g = graph()
    tests = test_modules()
    tset = set(tests)
    tsrc = {m: g.src.get(m, "") for m in tests}
    sel = Selection()
    code_changed = {}    # lib module -> why
    tmap = load_map()
    lmap = load_layout_map()
    no_map = None
    if lmap is not None and any(LAYOUT_STATIC.match(f) for f in files):
        no_map = layout_map_stale(lmap, base, files)
        if no_map:
            lmap = None
    sel.layout_all = sorted((lmap or {}).get("groups") or ())
    # the helpers of the tests (tests/*.py that are not test modules) and the other files of tests/ (.mjs, .sh …)
    helpers = sorted(m for m in g.files if m.startswith("tests") and m not in tset)
    side = {}
    for d, dirs, fs in os.walk(os.path.join(ROOT, "tests")):
        dirs[:] = [x for x in dirs if x not in ("__pycache__", "fixtures") and not x.startswith(".")]
        for f in fs:
            if f.endswith((".mjs", ".js", ".cjs", ".sh")):
                r = os.path.relpath(os.path.join(d, f), ROOT).replace(os.sep, "/")
                side[r] = read(r) or ""

    shell_tests = {rel: read(rel) or "" for rel in SELFTESTS}

    def importers_in_tests(mod, graph_=None):
        return sorted(u for u in (graph_ or g).importers(mod) if u in tset)

    rd = reads()
    templates = {}

    def built(key, text, rel, mod=None):
        """How ``text`` names ``rel`` by a name put together from parts (REV-speed-2 С5): «по шаблону …», else None.
        A module of Python — as Reads followed its reads; any source — its quoted names with a placeholder."""
        if mod is not None:
            hit = rd.covers(mod, rel)
            if hit == "":
                return "читает файл, путь которого разбор не понял (строка %d; нужна пометка «# k3-select: files=…»)" \
                       % rd.unresolved[mod][0]
            if hit is not None:
                # a name that came out whole (a loop over a list of names) is that file, not a pattern (REV-speed-3)
                return ("читает по шаблону %s" if "*" in hit else "читает %s (имя собрано из частей)") % hit
        if key not in templates:
            templates[key] = [(re.compile(rx), t) for rx, t in rd.text_templates(text).items()]
        for rx, t in templates[key]:
            if rx.search(rel):
                return "называет по шаблону %s" % t
        return None

    def readers(rel, why_prefix, depth=0):
        pat = text_patterns(rel)
        for m in tests:
            if pat.search(tsrc[m]):
                sel.unit(m, "%s %s читает как текст" % (why_prefix, m.split(".")[-1]))
                continue
            how = built(m, tsrc[m], rel, m)
            if how:
                sel.unit(m, "%s %s %s" % (why_prefix, m.split(".")[-1], how))
        # a helper naming it (tests/fakes_gw.py takes the parser out of install.sh): every test importing the helper
        for h in helpers:
            how = "его читает" if pat.search(g.src.get(h, "")) else built(h, g.src.get(h, ""), rel, h)
            if how:
                for up in importers_in_tests(h):
                    sel.unit(up, "%s %s помощник %s, тест его импортирует" % (why_prefix, how, g.files[h]))
        # a script of tests/ naming it (tests/setup/layout_scan.mjs): whoever reads that script
        if depth < 2:
            for f, text in sorted(side.items()):
                if f != rel and (pat.search(text) or built(f, text, rel)):
                    readers(f, "%s %s ←" % (why_prefix, f), depth + 1)
        for sh, part in SELFTESTS.items():
            if sh != rel and (pat.search(shell_tests[sh]) or built(sh, shell_tests[sh], rel)):
                sel.part(part, "%s читает %s" % (os.path.basename(sh), rel))

    def scanners(rel):
        """The tests going through a folder that holds ``rel`` (os.walk, glob, listdir …), REV-speed С1."""
        sc = scans()
        for m in sorted(set(sc.dirs) | set(sc.unresolved) | set(sc.marks)):
            hit = sc.covers(m, rel)
            if hit is None:
                continue
            if hit:
                kind = "папку" if os.path.isdir(os.path.join(ROOT, hit)) else "файл"     # install.sh — файл (П-3)
                where = "проходит %s %s целиком" % (kind, hit)
            elif m in sc.marks or not sc.unresolved.get(m):
                where = "проходит весь репозиторий"
            else:
                where = "проходит папку, которую разбор не понял (строка %d; нужна пометка «# k3-select: dirs=…»)" \
                        % sc.unresolved[m][0]
            who = [m] if m in tset else importers_in_tests(m)
            for up in who:
                sel.unit(up, "%s: %s %s" % (rel, g.files.get(m, m), where)
                         + ("" if up == m else ", тест его импортирует"))

    for rel in files:
        exists = os.path.exists(os.path.join(ROOT, rel))
        ext = os.path.splitext(rel)[1]
        handled = False
        scanners(rel)
        if rel.endswith(".py") and exists and any(under(rel, x) for x in compat_targets()):
            sel.part("compat", "%s — код Python" % rel)
        if rel == "tests/__init__.py":
            sel.everything.append("tests/__init__.py — пакет всех тестов")
            continue
        if rel in META:
            sel.notes.append("%s — обвязка тестов (её проверки — по тексту ниже)" % rel)
            readers(rel, rel + ":")
            continue
        if rel in SELFTESTS:
            sel.part(SELFTESTS[rel], "%s изменён" % rel)
            readers(rel, rel + ":")
            continue
        if rel in STEP_TOOLS:
            for p in STEP_TOOLS[rel]:
                if p == "unit:all":
                    sel.everything.append("%s — средство всех модульных тестов" % rel)
                else:
                    sel.part(p, "%s изменён" % rel)
            readers(rel, rel + ":")
            continue
        if rel == "install.sh":
            old, new = old_text(rel) or "", read(rel) or ""

            def strip(t):
                return I18N_BLOCK.sub("", PAYLOAD_BLOCK.sub("", t))

            def i18n(t):
                m = I18N_BLOCK.search(t)
                return m.group(0) if m else ""
            sel.part("payload", "install.sh изменён")
            if strip(old) != strip(new) or not old:
                sel.part("bashn", "install.sh изменён (не только встроенные блоки)")
                sel.part("installer", "install.sh изменён вне встроенных блоков")
                for p in ("rules_adopt", "egress_parser", "egress_xray"):
                    sel.part(p, "самотест 2.x берёт код из install.sh")
                readers(rel, "install.sh:")
            elif i18n(old) != i18n(new):
                sel.part("installer", "install.sh: тексты меню K3_I18N изменились")
            else:
                sel.notes.append("install.sh: изменён только блок K3_PAYLOAD (код ядра — по своим правилам)")
            continue
        if rel.startswith("lib/kaskad/"):
            sel.part("payload", "%s едет в payload install.sh" % rel)
        if rel.endswith(".py") and rel in g.by_file:
            mod = g.by_file[rel]
            if mod in tset:
                sel.unit(mod, "%s — сам тест изменён" % rel)
            else:
                code_changed[mod] = rel            # a module of the core or a helper of the tests: the map, the imports
            readers(rel, rel + ":")
            handled = True
        elif rel.endswith(".py") and rel.startswith(("lib/", "tests/")) and not exists:
            # a deleted (or renamed) module: whoever named it, and whoever imported it — by the imports of the base,
            # the tree here has no such module any more (REV-speed С2); no base — everything
            readers(rel, rel + " (удалён):")
            mod = mod_of(rel)
            bg = base_graph()
            if bg is None or mod not in bg.files:
                sel.everything.append("%s удалён, а кто его импортировал, по базе не узнать" % rel)
            else:
                for up, chain in sorted(bg.importers(mod).items()):
                    if up in tset:
                        sel.unit(up, "%s (удалён): в базе %s" % (rel, chain))
                for up, fs in tmap.items():
                    if up in tset and rel in fs:
                        sel.unit(up, "%s (удалён): по карте тест вызывал его функции" % rel)
            handled = True
        if LAYOUT_STATIC.match(rel):
            sub = rel[len(STATIC_DIR):]
            gs, why = layout_groups(sub, lambda: (old_text(rel), new_text(rel)), lmap) if lmap or not no_map else \
                (None, no_map)
            if gs is None:
                sel.part("layout", "%s — статика панели: вся вёрстка — %s" % (rel, why))
            elif gs:
                r = "%s — статика панели: %s → группы %s" % (rel, why, ", ".join(sorted(gs)))
                sel.part("layout", r)
                sel.layout_narrow.add(r)
                for grp in gs:
                    sel.layout_pages.setdefault(grp, []).append(rel)
            else:
                sel.notes.append("%s: %s — ни к одному виду вёрстки (карта), Chrome не нужен" % (rel, why))
            for m in tests:
                if m.split(".")[-1].startswith("test_panel"):
                    sel.unit(m, "%s — статика панели → test_panel*" % rel)
            if sub.startswith("setup/"):
                for m in tests:
                    if "setup" in m.split(".")[-1] or m.startswith("tests.setup.") or "wizard" in m:
                        sel.unit(m, "%s — мастер → тесты мастера" % rel)
            # the Chrome checks among the unit tests open pages, not a file by name
            for m, what in chrome_units(sub):
                if m in tset:
                    sel.unit(m, "%s — %s в Chrome" % (rel, what))
            readers(rel, rel + ":")
            handled = True
        if rel.startswith("lib/kaskad/i18n/catalog/") or rel.startswith("lib/kaskad/i18n/sh/"):
            for m in tests:
                if m.split(".")[-1].startswith("test_i18n"):
                    sel.unit(m, "%s — каталог текстов → test_i18n*" % rel)
            if rel.startswith("lib/kaskad/i18n/sh/"):
                sel.part("installer", "%s — тексты меню и установщика" % rel)
            keys, texts = catalog_changes(old_text, rel)
            chunks = [c for t in texts for c in text_chunks(t)]
            for m in tests:
                src = tsrc[m]
                hit = next((k for k in keys if k in src), None)
                if hit:
                    sel.unit(m, "%s: ключ %s изменён, тест его называет" % (rel, hit))
                    continue
                c = next((c for c in chunks if c in src), None)
                if c:
                    sel.unit(m, "%s: тест держит текст «%s»" % (rel, c[:40]))
            readers(rel, rel + ":")
            handled = True
        if rel.startswith("lib/") and not rel.endswith(".py") and not handled:
            # a data file of the core: the modules that name it are what changed
            pat = text_patterns(rel)
            base_name = os.path.basename(rel)
            for mod, path in g.files.items():
                if mod.startswith("kaskad") and (pat.search(g.src[mod]) or base_name in g.src[mod]):
                    code_changed.setdefault(mod, "%s (читает %s)" % (path, rel))
            readers(rel, rel + ":")
            handled = True
        if rel.startswith("tests/") and not handled:
            readers(rel, rel + ":")
            handled = True
        if rel.startswith("tools/") and not handled:
            readers(rel, rel + ":")
            handled = True
        if not handled:
            if rel.startswith(("docs/", "assets/")) or ext in (".md", ".txt", ".png", ".jpg", ".svg", ".gif") \
                    or "/" not in rel:
                readers(rel, rel + ":")
                if not any(rel in " ".join(v) for v in sel.units.values()):
                    sel.notes.append("%s — тесты его не читают" % rel)
            else:
                sel.everything.append("%s — правило не знает этот файл" % rel)

    mapped = set(tmap)
    if code_changed and not tmap:
        sel.notes.append("карты тестов нет (%s) — выбор по импортам, шире нужного; её пишет tools/k3_unit_par.py "
                         "(полный k3_test.sh)" % map_path())
    for mod, rel in sorted(code_changed.items()):
        path = g.files[mod]
        everyone = g.importers(mod)
        if tmap:
            # the map: the tests that call the functions of this file; the tests naming the module themselves;
            # the tests the map does not know yet (new ones) — by the imports
            for up, files in tmap.items():
                if up in tset and path in files:
                    sel.unit(up, "%s: по карте тест вызывает его функции" % rel)
            # what a test calls (itself or through a helper) is in the map; a test importing the module itself may
            # read its constants — those are not calls
            for up in sorted(g.rev.get(mod, ())):
                if up in tset:
                    sel.unit(up, "%s: тест импортирует модуль сам" % rel)
            in_map = any(path in files for files in tmap.values())
            for up, chain in everyone.items():
                if up in tset and (up not in mapped or not in_map):
                    sel.unit(up, "%s: %s — %s" % (rel, chain, "модуля нет в карте" if not in_map else
                                                  "теста нет в карте"))
        else:
            for up, chain in everyone.items():
                if up in tset:
                    sel.unit(up, "%s: %s" % (rel, chain))
        # a test running the core in a process of its own (-m kaskad.mcp): what runs there is not in its map
        # (REV-speed-2 З5) — by the static imports of what it runs
        for t, ents in sorted(rd.runs.items()):
            hit = next((e for e in sorted(ents) if e in g.files and (e == mod or e in everyone)), None)
            if hit is None:
                continue
            for up in ([t] if t in tset else importers_in_tests(t)):
                sel.unit(up, "%s: %s запускает %s отдельным процессом%s" % (
                    rel, g.files.get(t, t) if up != t else up.split(".")[-1], hit,
                    "" if hit == mod else ", а он импортирует %s (%s)" % (mod.split(".")[-1], everyone[hit])))
        # the installer test runs the real core for «summary», «fwd list» and the payload of kaskad-mcp
        for entry in ("kaskad.summary", "kaskad.cli_fwd", "kaskad.forwards", "kaskad.mcp"):
            if mod == entry or mod.startswith(entry + "."):
                sel.part("installer", "%s — его зовёт настоящим ядром tools/k3_install_test.sh" % rel)

    if layout_forced:
        sel.part("layout", "--layout")
    if sel.everything:
        for m in tests:
            sel.unit(m, "всё: " + sel.everything[0])
        for p in PARTS_ORDER:
            if p != "unit":
                sel.part(p, "всё: " + sel.everything[0])
    if sel.units:
        sel.part("unit", "%d модулей" % len(sel.units))
    return sel


def map_path():
    return unit_par().map_file()


def load_map():
    """{test module: [repo paths of lib files whose functions it called]} of this repository — written by
    tools/k3_unit_par.py (per repository, a union of every run). K3_TEST_MAP=<file> — that file (a map of the old
    form {"modules": …} too), K3_TEST_MAP=0 — none."""
    if os.environ.get("K3_TEST_MAP") == "0":
        return {}
    par = unit_par()
    explicit = os.environ.get("K3_TEST_MAP")
    doc = par.load_map_doc(explicit or map_path())
    try:
        if "repos" in doc:
            mods = (doc["repos"].get(par.repo_key(ROOT)) or {}).get("modules") or {}
        else:
            mods = (doc.get("modules") or {}) if explicit else {}    # v1 at the default place: not trusted
        return {m: set(v) for m, v in mods.items()}
    except (AttributeError, TypeError):
        return {}


def quiet_sh():
    d = ROOT
    for _ in range(4):
        cand = os.path.join(os.path.dirname(d), "stand", "quiet.sh")
        if os.path.isfile(cand):
            return cand
        d = os.path.dirname(d)
    home = os.path.join(os.path.expanduser("~"), ".claude", "hooks", "quiet.sh")
    return home if os.path.isfile(home) else None


def busy():
    """REV-speed-2b С9: the machine is busy (other agents' runs) — the load average of a minute over a third of the
    cores: the selection runs at -j 6 and the layout in 3 parts, not 12 + 6 Chrome (the tests bound by the wall clock
    and a Chrome starting in 15 s get closer to their edge)."""
    try:
        return os.getloadavg()[0] > (os.cpu_count() or 4) / 3.0
    except (OSError, AttributeError):
        return False


def main(argv):
    base, run, jobs, layout, files, as_json = None, False, None, False, None, False
    it = iter(argv)
    for a in it:
        if a == "--base":
            base = next(it)
        elif a == "--run":
            run = True
        elif a in ("-j", "--jobs"):
            jobs = int(next(it))
        elif a.startswith("-j") and a[2:].isdigit():
            jobs = int(a[2:])
        elif a == "--layout":
            layout = True
        elif a == "--json":
            as_json = True
        elif a == "--merge-layout-map":
            return merge_layout_map(next(it))
        elif a == "--files":
            files = []
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        elif files is not None:
            files.append(os.path.relpath(os.path.abspath(a), ROOT) if os.path.isabs(a) else a)
        else:
            raise SystemExit("test_select: непонятный аргумент %s (--help)" % a)
    base = base or default_base()
    if files is None:
        files = changed_files(base)
    sel = select(files, base, layout)
    pages = sel.layout_groups(sel.layout_all)
    if as_json:
        print(json.dumps({"files": files, "units": sel.units, "parts": sel.parts, "everything": sel.everything,
                          "notes": sel.notes, "layout_pages": pages}, ensure_ascii=False, indent=1))
        if not run:
            return 0
    else:
        print("изменено относительно %s: %d файлов" % (base, len(files)))
        for f in files:
            print("  " + f)
        for n in sel.notes:
            print("  · " + n)
        if sel.everything:
            print("ВСЁ: " + "; ".join(sel.everything))
        for p in PARTS_ORDER:
            if p in sel.parts and p != "unit":
                print("%s ← %s" % (PART_NAMES[p], sel.parts[p][0]) +
                      (" (и ещё %d)" % (len(sel.parts[p]) - 1) if len(sel.parts[p]) > 1 else ""))
                if p == "layout":
                    print("  виды: " + ("все" if pages is None else "K3_PAGES=" + ",".join(pages)))
        for m in sorted(sel.units):
            r = sel.units[m]
            print("%s ← %s" % (m, r[0]) + (" (и ещё %d)" % (len(r) - 1) if len(r) > 1 else ""))
        if not sel.units and not sel.parts:
            print("тестам нечего проверять")
    if not run:
        return 0
    if not sel.units and not sel.parts:
        return 0
    if jobs is None:
        jobs = 12
        if busy():
            jobs = 6
            os.environ.setdefault("K3_LAYOUT_JOBS", "3")
            print("машина занята (нагрузка %.1f на %d ядер): -j 6, вёрстка — 3 части" %
                  (os.getloadavg()[0], os.cpu_count() or 0), flush=True)
    cmd = ["bash", os.path.join(ROOT, "tools", "k3_test.sh"), "-j", str(jobs)]
    parts = [p for p in PARTS_ORDER if p in sel.parts]
    cmd += ["--parts", ",".join(parts)]
    if sel.units and not sel.everything:
        cmd += ["--units", " ".join(sorted(sel.units))]
    if "layout" in parts:
        # R-speed-2: only the groups of views the change touches (tools/k3_test.sh gives them to the layout as K3_PAGES)
        if pages is None:
            os.environ.pop("K3_LAYOUT_PAGES", None)
        else:
            os.environ["K3_LAYOUT_PAGES"] = ",".join(pages)
    q = quiet_sh()
    if q:
        cmd = [q, "test-select"] + cmd
    print("запуск: " + " ".join(cmd[:6]) + (" …" if len(cmd) > 6 else ""), flush=True)
    return run_cmd(cmd)


STOPPED = {129: "HUP", 130: "INT", 143: "TERM"}       # k3_test.sh stopped by a signal (its trap)


def run_cmd(cmd):
    """The run in a group of its own: Ctrl-C, TERM, HUP to test_select go to the whole group (k3_test.sh stops its
    steps by its trap) and test_select waits for the group to go. A run cut by a signal — its own line and 128+N,
    not a negative code (-15 & 0xff = 241) and no summary (REV-speed-3 З6). The group still there after 15 s — killed
    and said so; a signal that came after the run had ended does not cut a green run (REV-speed-4)."""
    os.environ.setdefault("K3_TR_STRICT", "1")             # П3-а: an unfilled «{name}» of tr() fails (as k3_test.sh)
    own_log = None
    if os.environ["K3_TR_STRICT"] == "1" and not os.environ.get("K3_TR_STRICT_LOG"):
        # П3-б (REV-ns-p3a З-1): the keys a tr() under «except Exception» left unfilled — k3_test.sh fails on them too
        fd, own_log = tempfile.mkstemp(prefix="k3-tr-strict-", suffix=".log")
        os.close(fd)
        os.environ["K3_TR_STRICT_LOG"] = own_log
    try:
        rc = run_group(cmd)
        if rc == 0 and own_log and os.path.getsize(own_log) > 0:      # a file given from outside — its owner reads it
            with open(own_log, encoding="utf-8", errors="replace") as f:
                keys = sorted(set(f.read().splitlines()))
            print("test_select: tr() — не заполнена подстановка «{…}» (вызов под except): " + "; ".join(keys[:20]),
                  flush=True)
            rc = 1
        return rc
    finally:
        if own_log:
            os.environ.pop("K3_TR_STRICT_LOG", None)
            try:
                os.unlink(own_log)
            except OSError:
                pass


def run_group(cmd):
    p = subprocess.Popen(cmd, cwd=ROOT, start_new_session=True)
    got = []

    def forward(signum, frame):
        got.append(signum)
        try:
            os.killpg(p.pid, signum)
        except OSError:
            pass

    sigs = [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]
    old = dict((s, signal.signal(s, forward)) for s in sigs)
    try:
        rc = p.wait()
        cut = list(got)                      # the signals that came while the run was going
        for _ in range(150):                 # what k3_test.sh still stops after quiet.sh is gone (its stop_all ≤ 5 s)
            try:
                os.killpg(p.pid, 0)
            except OSError:
                break
            time.sleep(0.1)
        else:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import k3_proc
            left = k3_proc.kill_group(p.pid)
            print("test_select: процессы прогона не завершились за 15 с — сняты (%d, SIGKILL)" % len(left), flush=True)
    finally:
        for s, h in old.items():
            signal.signal(s, h)
    got = cut
    sig = -rc if rc < 0 else (got[0] if got else None)
    if rc < 0 or rc in STOPPED or got:
        name = signal.Signals(sig).name if sig else "SIG" + STOPPED[rc]
        code = 128 + int(sig) if sig else rc
        print("test_select: прогон оборван (%s) — итога нет: это не зелёное и не красное, перезапустите" % name,
              flush=True)
        return code
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

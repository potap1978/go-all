"""PLAN-multi-ip Ш1–Ш2, the migration and the rollback (the owner's rule 24.10: «старые данные → новая» и откат в каждом
выпуске; PLAN §4.1). Without a write: there is no new key until a person adds an address by hand.

The node is applied from scratch by the RELEASED 3.3.7.12 (the tree of 8582b624, ``git archive``) and by this version,
each in a process of its own running ``tests/mip_old_side.py`` (the same file in both trees), on the same fake root:

| Data of the node | 3.3.7.12 → this version | rollback to 3.3.7.12 |
|---|---|---|
| state.json, the pool, the keys of 3.3.7.12 | the nft scripts and tables, xray.json, the files of the legs and the addresses of the open mode — byte for byte | — |
| ``gateway.addresses.manual`` and /var/lib/kaskad/gw/addresses.json (an address added by hand here) | — | 3.3.7.12 renders all of it as without them (an unknown key is not read), and a copy it exports keeps the key |

The legs are configured by install.sh (bash): its functions of the legs and of the xray exits are the same text as in
3.3.7.12 (``LegFunctionsTest``) — Ш1–Ш2 do not touch them. No git or no commit (a tarball) — skipped."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REV = "8582b624"                                   # 3.3.7.12 = dev = test
SIDE = "tests/mip_old_side.py"
LEG_FUNCS = ("vless_outbound", "_other_outbound", "awg_setconf", "awg_outbound", "egress_awg_route_leg")
# Ш4 Л2: what the leg routing got — the refusal on a foreign table with the mark closed (REV-mip-s34 С-4), and that
# closing rule taken off once the table is ours again; the rest of the function as in 3.3.7.12 (``hex`` — the mark
# written once)
L2_OPEN = ("""    hex=$(printf '0x%x' "$mark")
    if egress_leg_table_refuse "$mark" "$table"; then          # Л2 (PLAN-multi-ip): чужую таблицу не трогаем
        grep -qE "[[:space:]]fwmark ${hex}[[:space:]]+unreachable([[:space:]]|\\$)" <<< "$(ip -4 rule show 2>/dev/null)" \\
            || ip rule add fwmark "$mark" unreachable 2>/dev/null
        return 1
    fi
""")
L2_CLOSE = ("""    if [ "$rc" -eq 0 ] && grep -qE "[[:space:]]fwmark ${hex}[[:space:]]+unreachable([[:space:]]|\\$)" <<< "$rules"; then
        while ip rule del fwmark "$mark" unreachable 2>/dev/null; do :; done     # С-4: таблица снова своя
    fi
""")
_TMP = {}


def tearDownModule():
    if _TMP.get("dir"):
        shutil.rmtree(_TMP["dir"], ignore_errors=True)


def _tmp():
    if not _TMP.get("dir"):
        _TMP["dir"] = tempfile.mkdtemp(prefix="k3-mip-mig-")
    return _TMP["dir"]


def tree():
    """lib/, tests/ and install.sh of 3.3.7.12 with the side copied in. SkipTest without git or the commit."""
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
    arc = subprocess.run(["git", "-C", REPO, "archive", REV, "lib", "tests", "install.sh"], capture_output=True,
                         timeout=120)
    if arc.returncode != 0:
        raise unittest.SkipTest("git archive %s: %s" % (REV, arc.stderr[-300:]))
    subprocess.run(["tar", "-x", "-C", d + ".part"], input=arc.stdout, check=True, timeout=120)
    shutil.copyfile(os.path.join(REPO, SIDE), os.path.join(d + ".part", SIDE))
    os.rename(d + ".part", d)
    return d


def side(where, mode, out, root="", extra=False):
    """The side in a tree (``where``: the old one or REPO) -> the folder of the exchange."""
    io = os.path.join(_tmp(), "io")
    os.makedirs(io, exist_ok=True)
    env = dict(os.environ)
    env.pop("KASKAD_ROOT", None)
    if where != REPO:
        env.pop("K3_TR_STRICT_LOG", None)          # the strictness of the old texts is not under test here
    env.update(PYTHONPATH=os.pathsep.join([os.path.join(where, "lib"), where]), MIP_MODE=mode, MIP_IO=io,
               MIP_ROOT=root, MIP_OUT=out, KASKAD_LANG="ru",
               MIP_EXTRA=extra if isinstance(extra, str) else ("1" if extra else ""))
    p = subprocess.run([sys.executable, "-m", "unittest", "tests.mip_old_side"], cwd=where, env=env,
                       capture_output=True, text=True, timeout=900)
    ran = re.search(r"^Ran (\d+) test", p.stderr, re.M)
    if p.returncode != 0 or not ran or "skipped" in p.stderr.splitlines()[-1]:
        raise AssertionError("side %s %s in %s:\n%s" % (mode, out, where, p.stderr[-6000:]))
    return io


def got(io, out):
    with open(os.path.join(io, out + ".json"), encoding="utf-8") as f:
        return f.read()


class MigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = tree()
        io = side(cls.old, "render", "made")                     # 3.3.7.12 makes the node
        cls.root = os.path.join(io, "made-root")
        cls.io = side(cls.old, "render", "old", cls.root)        # and applies it again from its files

    def test_this_version_applies_a_node_of_3_3_7_12_byte_for_byte(self):
        side(REPO, "render", "new", self.root)
        old, new = got(self.io, "old"), got(self.io, "new")
        doc = json.loads(new)
        self.assertTrue(doc["nft_applied"] and doc["xray"])                    # something was really rendered
        self.assertEqual(doc["listen"], ["203.0.113.10", "203.0.113.77"])      # the panel: no private, no Docker
        self.assertEqual(new, old)

    def test_r2_without_a_choice_is_3_3_7_12_byte_for_byte(self):
        """REV-mip-s34 Б-1: Р2 of the lab — eth0 holds four addresses, the default route goes from the last one (not the
        first: masquerade takes the first), the cache of «Адреса сервера» is written, nothing is chosen. The table, xray
        and the legs of this version are those of 3.3.7.12 byte for byte: no SNAT of the clients, no sendThrough."""
        side(self.old, "render", "r2-old", self.root, extra="r2")
        side(REPO, "render", "r2-new", self.root, extra="r2")
        old, new = json.loads(got(self.io, "r2-old")), json.loads(got(self.io, "r2-new"))
        self.assertEqual((old["r2"], new["r2"]), ({"cache": False, "src": "85.143.0.30"},
                                                  {"cache": True, "src": "85.143.0.30"}))   # Р2 really, both sides
        self.assertTrue(new["nft_applied"] and new["xray"])
        for k in ("nft_applied", "nft_tables", "xray", "legs"):
            self.assertEqual(new[k], old[k], k)
        self.assertNotIn("snat to", json.dumps(new["nft_applied"]))
        self.assertNotIn("sendThrough", new["xray"])

    def test_the_rollback_ignores_the_new_keys(self):
        side(REPO, "mark", "marked", self.root)
        marked = os.path.join(self.io, "marked-root")
        with open(os.path.join(marked, "etc", "kaskad", "state.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["gateway"]["addresses"], {"manual": [{"ip": "93.184.216.34"}]})
        self.assertTrue(os.path.exists(os.path.join(marked, "var", "lib", "kaskad", "gw", "addresses.json")))
        side(self.old, "render", "back", marked)
        self.assertEqual(got(self.io, "back"), got(self.io, "old"))             # 3.3.7.12: the same node
        side(REPO, "render", "new-marked", marked)
        self.assertEqual(got(self.io, "new-marked"), got(self.io, "old"))       # an address by hand changes no rule
        side(self.old, "export", "copy", marked)
        self.assertEqual(json.loads(got(self.io, "copy"))["gateway"]["addresses"],
                         {"manual": [{"ip": "93.184.216.34"}]})                 # its copy keeps the key


    def test_the_rollback_ignores_the_keys_of_s34(self):
        """Ш3/Ш4 (PLAN §4.1): the node with ``addresses.in`` and ``exits[].src`` applied by this version — 3.3.7.12
        applies it as its own node (the rules of the addresses are gone with its full table), its copy keeps the keys."""
        side(REPO, "mark34", "marked34", self.root, extra=True)
        marked = os.path.join(self.io, "marked34-root")
        with open(os.path.join(marked, "etc", "kaskad", "state.json"), encoding="utf-8") as f:
            g = json.load(f)["gateway"]
        self.assertEqual(g["addresses"]["in"], {"*": ["85.143.0.20"]})
        self.assertEqual(sorted(r["src"] for r in g["exits"].values() if "src" in r), ["85.143.0.30"] * 2)
        with open(os.path.join(marked, "etc", "kaskad", "net", "kaskad.nft"), encoding="utf-8") as f:
            boot = f.read()
        self.assertIn("fib daddr type local meta l4proto . th dport", boot)    # the files hold the new rules
        self.assertIn("snat to 85.143.0.30", boot)
        clean = marked + "-clean"                       # the same node without the new keys (ops._change of this
        shutil.copytree(marked, clean, symlinks=True)   # version also wrote gateway.mtu — 3.3.7.12 reads that one)
        sp = os.path.join(clean, "etc", "kaskad", "state.json")
        with open(sp, encoding="utf-8") as f:
            st = json.load(f)
        st["gateway"].pop("addresses")
        for rec in st["gateway"]["exits"].values():
            rec.pop("src", None)
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        side(self.old, "render", "back34", marked, extra=True)                 # the addresses are on the machine
        side(self.old, "render", "clean34", clean, extra=True)
        self.assertEqual(got(self.io, "back34"), got(self.io, "clean34"))       # 3.3.7.12 does not see the keys
        self.assertNotIn("snat to 85.143.0.30", got(self.io, "back34"))
        self.assertNotIn('"sendThrough": "85.143.0.30"', json.loads(got(self.io, "back34"))["xray"])
        new34 = json.loads(side(REPO, "render", "new34", marked, extra=True) and got(self.io, "new34"))
        self.assertIn('"sendThrough": "85.143.0.30"', new34["xray"])           # this version on the same machine: yes
        self.assertTrue(any("snat to 85.143.0.30" in x for x in new34["nft_applied"]))
        side(self.old, "export", "copy34", marked)
        g = json.loads(got(self.io, "copy34"))["gateway"]
        self.assertEqual(g["addresses"]["in"], {"*": ["85.143.0.20"]})         # its copy keeps the keys


class LegFunctionsTest(unittest.TestCase):
    def test_the_functions_of_the_legs_and_xray_exits_are_those_of_3_3_7_12(self):
        old = os.path.join(tree(), "install.sh")

        def fn(path, name):
            """A bash function (``name() {`` … ``}``) or a Python one of the parser inside install.sh (``def name(`` …
            up to the next line at the left edge)."""
            with open(path, encoding="utf-8") as f:
                src = f.read()
            m = re.search(r"^%s\(\) \{.*?^\}\n" % re.escape(name), src, re.S | re.M) or \
                re.search(r"^def %s\(.*?(?=^\S)" % re.escape(name), src, re.S | re.M)
            self.assertIsNotNone(m, name)
            return m.group(0)
        for name in LEG_FUNCS:
            new = fn(os.path.join(REPO, "install.sh"), name)
            if name == "egress_awg_route_leg":
                self.assertEqual((new.count(L2_OPEN), new.count(L2_CLOSE)), (1, 1))
                new = new.replace(L2_OPEN, "").replace(L2_CLOSE, "").replace(" pref=() hex\n", " pref=()\n")
                new = new.replace("${hex}", "$(printf '0x%x' \"$mark\")")
            self.assertEqual(new, fn(old, name), name)


if __name__ == "__main__":
    unittest.main()

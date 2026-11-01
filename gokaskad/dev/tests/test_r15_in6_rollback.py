"""3.3.7.15 F11, the rollback (the owner's rule 24.10; SPEC-R1-3315 §3 F11 «Данные», «Тесты»): ``addresses.in6``
on the trees of the released 3.3.7.13 (bc541e29) and 3.3.7.14 (fc5ef683) — each in a process of its own running
``tests/r15_old_side.py`` (the same file in every tree):

| | 3.3.7.13 / 3.3.7.14 with ``in6`` in state.json | this version |
|---|---|---|
| ``model.validate``, ``gw apply``, the subscription | no error, the subscription as without ``in6`` | the same |
| ``ip kaskad`` | no IPv6 in it | no IPv6 in it |
| a live gw_pre6 after an apply (``update_lines``) | stays (fixed here: the old one refills its chains in place) | drawn |
| gw_pre6 in kaskad.nft, after ``apply_ip6`` (the watchdog) | none (the table is made anew) | drawn |
| ``in6`` in state.json | kept (an unknown key is not read and not erased) | kept |
| ``in6`` not set | — | the ip6 texts byte for byte as 3.3.7.14 |

No git or no commit (a tarball) — skipped."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REV13, REV14 = "bc541e29", "fc5ef683"
SIDE = "tests/r15_old_side.py"
X6 = "2001:db8:10::5"
_TMP = {}


def tearDownModule():
    if _TMP.get("dir"):
        shutil.rmtree(_TMP["dir"], ignore_errors=True)


def _tmp():
    if not _TMP.get("dir"):
        _TMP["dir"] = tempfile.mkdtemp(prefix="k3-r15-in6-")
    return _TMP["dir"]


def tree(rev):
    """lib/, tests/ and install.sh of ``rev`` with the side copied in. SkipTest without git or the commit."""
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
    shutil.copyfile(os.path.join(REPO, SIDE), os.path.join(d + ".part", SIDE))
    os.rename(d + ".part", d)
    return d


def side(where, out, rule=""):
    io = os.path.join(_tmp(), "io")
    os.makedirs(io, exist_ok=True)
    env = dict(os.environ)
    env.pop("KASKAD_ROOT", None)
    if where != REPO:
        env.pop("K3_TR_STRICT_LOG", None)          # the strictness of the old texts is not under test here
    env.update(PYTHONPATH=os.pathsep.join([os.path.join(where, "lib"), where]), R15_IO=io, R15_OUT=out, R15_RULE=rule,
               KASKAD_LANG="ru")
    p = subprocess.run([sys.executable, "-m", "unittest", "tests.r15_old_side"], cwd=where, env=env,
                       capture_output=True, text=True, timeout=900)
    ran = re.search(r"^Ran (\d+) test", p.stderr, re.M)
    if p.returncode != 0 or not ran or "skipped" in p.stderr.splitlines()[-1]:
        raise AssertionError("side %s in %s:\n%s" % (out, where, p.stderr[-6000:]))
    with open(os.path.join(io, out + ".json"), encoding="utf-8") as f:
        return json.load(f)


class RollbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.new = side(REPO, "new")
        cls.old = {rev: side(tree(rev), rev, cls.new["rule"]) for rev in (REV13, REV14)}

    def test_this_version(self):
        d = self.new
        self.assertTrue(d["rule"].endswith("ip6 daddr != { %s } drop" % X6), d["rule"])
        self.assertEqual((d["apply0"], d["apply1"], d["apply2"], d["validate"]), (0, 0, 0, ""))
        self.assertEqual((d["sub1"], isinstance(d["sub0"], list)), (d["sub0"], True))      # build_sub: no error
        self.assertFalse(d["ip4_v6"])
        for k in ("pre6_live", "pre6_after_update", "pre6_after_apply_ip6"):
            self.assertEqual(d[k], [d["rule"]], k)
        self.assertTrue(d["pre6_in_file"])
        self.assertEqual(d["ip6_problems"], 0)
        self.assertEqual(d["in6_kept"], {"*": [X6]})

    def test_in6_null_is_3_3_7_14_byte_for_byte(self):
        """С-1: ``in6`` not set — the boot file, the repair of the watchdog and an apply make ip6 kaskad6 as 3.3.7.14."""
        new, old = self.new["null"], self.old[REV14]["null"]
        self.assertTrue(new["boot6"] and new["apply6"] and new["upd6"] and new["file6"])     # really rendered
        for k in ("boot6", "apply6", "upd6", "file6"):
            self.assertEqual(new[k], old[k], k)
        self.assertNotIn("gw_pre6", json.dumps(new))

    def test_the_old_versions_with_in6(self):
        for rev, d in self.old.items():
            with self.subTest(rev):
                self.assertEqual(d["rule"], "")                                # really the old code
                self.assertEqual((d["apply0"], d["apply1"], d["apply2"], d["validate"]), (0, 0, 0, ""))
                self.assertEqual((d["sub1"], isinstance(d["sub0"], list)), (d["sub0"], True))
                self.assertFalse(d["ip4_v6"])
                self.assertEqual(d["pre6_live"], [self.new["rule"]])           # as this version left it
                self.assertEqual(d["pre6_after_update"], [self.new["rule"]])   # fixed: an apply keeps it
                self.assertFalse(d["pre6_in_file"])                            # a reboot takes it off
                self.assertIsNone(d["pre6_after_apply_ip6"])                   # so does the repair of the watchdog
                self.assertEqual(d["ip6_problems"], 0)
                self.assertEqual(d["in6_kept"], {"*": [X6]})                   # not read, not erased


if __name__ == "__main__":
    unittest.main()

"""3.3.7.15 F12а and F13, the rollback (the owner's rule 24.10; SPEC-R1-3315 §3 F12 and F13 «Данные», «Тесты»):
``addresses.give`` (an IPv6), ``out`` and ``direct`` on the trees of the released 3.3.7.13 (bc541e29) and 3.3.7.14
(fc5ef683) — each in a process of its own running ``tests/r15_roles_side.py`` (the same file in every tree):

| | 3.3.7.13 / 3.3.7.14 with the keys in state.json | this version |
|---|---|---|
| ``model.validate``, ``gw apply``, the subscription | no error, no IPv6 line | the IPv6 lines after the IPv4 ones |
| gw_post | no SNAT «напрямую» (the old one draws none) | the SNAT to B before masquerade |
| «direct» of xray | no ``sendThrough`` | ``sendThrough`` B |
| the keys in state.json | kept (not read, not erased) | kept |
| none of the keys | — | gw_post and «direct» as 3.3.7.14 byte for byte |

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
SIDE = "tests/r15_roles_side.py"
B_IP = "85.143.0.10"
DIRECT = 'ip saddr @gw_nets oifname "eth0" ip daddr != @gw_nets snat to %s' % B_IP
_TMP = {}


def tearDownModule():
    if _TMP.get("dir"):
        shutil.rmtree(_TMP["dir"], ignore_errors=True)


def _tmp():
    if not _TMP.get("dir"):
        _TMP["dir"] = tempfile.mkdtemp(prefix="k3-r15-roles-")
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


def side(where, out):
    io = os.path.join(_tmp(), "io")
    os.makedirs(io, exist_ok=True)
    env = dict(os.environ)
    env.pop("KASKAD_ROOT", None)
    if where != REPO:
        env.pop("K3_TR_STRICT_LOG", None)          # the strictness of the old texts is not under test here
    env.update(PYTHONPATH=os.pathsep.join([os.path.join(where, "lib"), where]), R15R_IO=io, R15R_OUT=out,
               KASKAD_LANG="ru")
    p = subprocess.run([sys.executable, "-m", "unittest", "tests.r15_roles_side"], cwd=where, env=env,
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
        cls.old = {rev: side(tree(rev), rev) for rev in (REV13, REV14)}

    def test_this_version(self):
        d = self.new
        self.assertEqual((d["host"], d["apply0"], d["apply1"], d["validate"]), (0, 0, 0, ""))
        self.assertEqual(d["ipv6_lines"], 2)                                   # wg and awg2 on [IPv6] after IPv4
        self.assertEqual(d["sub1"][:len(d["null"]["sub"])], d["null"]["sub"])
        self.assertTrue(d["null"]["sub"])
        self.assertIn(DIRECT, d["post1"])
        self.assertEqual(d["direct1"].get("sendThrough"), B_IP)
        self.assertEqual(sorted(d["kept"]), ["direct", "give", "out"])

    def test_none_of_the_keys_is_3_3_7_14_byte_for_byte(self):
        new, old = self.new["null"], self.old[REV14]["null"]
        self.assertTrue(new["post"])
        self.assertEqual((new["post"], new["direct"]), (old["post"], old["direct"]))
        self.assertEqual(len(new["sub"]), len(old["sub"]))

    def test_the_old_versions_with_the_keys(self):
        for rev, d in self.old.items():
            with self.subTest(rev):
                self.assertEqual((d["host"], d["apply0"], d["apply1"], d["validate"]), (0, 0, 0, ""))
                self.assertEqual(d["ipv6_lines"], 0)                           # no IPv6 line
                self.assertEqual(d["sub1"], d["null"]["sub"])                  # the subscription as without the keys
                self.assertNotIn(DIRECT, d["post1"])                           # no SNAT «напрямую»
                self.assertEqual(d["post1"], d["null"]["post"])
                self.assertNotIn("sendThrough", d["direct1"])
                self.assertEqual(d["kept"], self.new["kept"])                  # not read, not erased


if __name__ == "__main__":
    unittest.main()

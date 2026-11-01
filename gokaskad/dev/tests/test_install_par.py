"""R-speed-2 (31.10): tools/k3_install_par.py — the installer test (tools/k3_install_test.sh) in parts beside each
other. A part = the common start + sections in a row + the summary, it begins only under «# k3-install-par: cut»,
replays the earlier sections' definitions (functions, plain assignments, «use_root», marked blocks) but not their
checks; every section in one part; a red section or a part without its summary — red; the sum of the checks — one
line «ВСЁ ЗЕЛЕНО (N)»."""

# k3-select: files=tools/k3_install_test.sh — ip.TEST, его и делит tools/k3_install_par.py
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    spec = importlib.util.spec_from_file_location("k3tool_install_par", os.path.join(ROOT, "tools", "k3_install_par.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ip = load()

MINI = r'''#!/usr/bin/env bash
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
FAILED=0; PASSED=0
ok(){  printf '  PASS %s\n' "$1"; PASSED=$((PASSED+1)); }
bad(){ printf '  FAIL %s\n' "$1"; FAILED=1; }
use_root() { export KASKAD_ROOT="$W/root-$1"; mkdir -p "$KASKAD_ROOT"; }
echo "── 1. первый ──"
use_root one; ROOTV="$KASKAD_ROOT/x"
helper() { echo "h-$1"; }
two() {
    echo "two"   # }
}
KEY='multi
line'
cat > "$W/note" <<'EOF'
fake() { echo NOT-A-DEFINITION; }
EOF
[ "$(helper a)" = h-a ] && ok "helper" || bad "helper"
# k3-install-par: replay — общая подделка
printf 'shared\n' > "$W/shared"
# k3-install-par: end
# k3-install-par: cut — дальше можно частью
echo "── 2. второй ──"
[ "$(helper b)" = h-b ] && ok "helper из раздела 1" || bad "helper из раздела 1"
[ "$(two)" = two ] && ok "многострочная функция" || bad "многострочная функция"
[ "$KEY" = "$(printf 'multi\nline')" ] && ok "многострочное значение" || bad "многострочное значение"
[ "$ROOTV" = "$W/root-one/x" ] && ok "значение из use_root" || bad "значение из use_root"
[ "$(cat "$W/shared" 2>/dev/null)" = shared ] && ok "общая подделка" || bad "общая подделка"
type fake >/dev/null 2>&1 && bad "строка here-документа стала функцией" || ok "here-документ — не определение"
[ -n "${K3_MINI_BAD:-}" ] && bad "внесённая поломка"
echo "── 3. третий ──"
ok "третий"

echo ""
if [ "$FAILED" = "0" ]; then echo "  ВСЁ ЗЕЛЕНО ($PASSED)"; exit 0; fi
echo "  ЕСТЬ ПРОБЛЕМЫ"; exit 1
'''


class RealScriptTest(unittest.TestCase):
    def test_every_section_once_parts_only_at_marks(self):
        with open(ip.TEST, encoding="utf-8") as f:
            text = f.read()
        start, secs, summary = ip.split(text)
        self.assertEqual(len(secs), len(re.findall(r'^echo "── ', text, re.M)))
        self.assertEqual("".join([start] + [s for _, s in secs] + [summary]), text)      # nothing lost, nothing twice
        marks = ip.cuts_of(secs)
        self.assertGreaterEqual(len(marks), 3, marks)
        w = ip.weights(secs)
        for n in range(1, 6):
            blocks = [list(range(a, b)) for a, b in zip(marks, marks[1:] + [len(secs)])]
            parts = [[i for b in grp for i in blocks[b]] for grp in ip.cut([sum(w[i] for i in b) for b in blocks], n)]
            self.assertEqual([i for p in parts for i in p], list(range(len(secs))))
            self.assertTrue(all(p[0] in marks for p in parts), (n, [p[0] for p in parts]))

    def test_replay_runs_no_check(self):
        with open(ip.TEST, encoding="utf-8") as f:
            start, secs, _ = ip.split(f.read())
        replay = "".join(ip.definitions(s) for _, s in secs)
        self.assertTrue(ip.bash_ok(replay))
        for line in replay.splitlines():
            self.assertFalse(re.match(r"^(?:(?:ok|bad) \"|lib |o=\$\()", line), line)


class PiecesTest(unittest.TestCase):
    def test_statements_and_assignments(self):
        self.assertEqual(ip.statements('use_root x; U="$K/a b"; C=1 && ok "y"'), ["use_root x", 'U="$K/a b"', "C=1"])
        self.assertEqual(ip.statements("A='x;y' # c"), ["A='x;y'"])
        self.assertEqual(ip.statements("A='open"), [])
        self.assertTrue(ip.plain_assignment('A=1 B="$W/x y" C=(a b) D=\'q\''))
        for line in ("A=$(date)", "A=`date`", "A=1 cmd", "A=1; cmd", "W=/tmp/x", "PATH=/bin", "echo A=1"):
            self.assertFalse(ip.plain_assignment(line), line)

    def test_cut_is_even_and_in_order(self):
        self.assertEqual(ip.cut([1, 1, 1, 1], 2), [[0, 1], [2, 3]])
        self.assertEqual(ip.cut([5, 1, 1, 1, 1, 1], 2), [[0], [1, 2, 3, 4, 5]])
        self.assertEqual(ip.cut([1, 2], 5), [[0], [1]])


class MiniRunTest(unittest.TestCase):
    def mini(self):
        d = tempfile.mkdtemp(prefix="k3ip-")
        self.addCleanup(shutil.rmtree, d, True)
        os.makedirs(os.path.join(d, "tools"))
        shutil.copy(os.path.join(ROOT, "tools", "k3_install_par.py"), os.path.join(d, "tools"))
        with open(os.path.join(d, "tools", "k3_install_test.sh"), "w", encoding="utf-8") as f:
            f.write(MINI)
        return d

    def run_par(self, d, *args, **env):
        e = dict(os.environ, XDG_CACHE_HOME=d, **env)
        r = subprocess.run([sys.executable, os.path.join(d, "tools", "k3_install_par.py")] + list(args), cwd=d, env=e,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        return r.returncode, r.stdout

    def test_parts_green_as_one_and_red_when_a_section_is(self):
        d = self.mini()
        rc1, out1 = self.run_par(d, "-j", "1")
        rc2, out2 = self.run_par(d, "-j", "2")
        self.assertEqual(rc1, 0, out1)
        self.assertEqual(rc2, 0, out2)
        self.assertIn("ВСЁ ЗЕЛЕНО (8)", out1)
        self.assertIn("ВСЁ ЗЕЛЕНО (8) — 2 частей рядом", out2)
        self.assertEqual(sorted(x for x in out1.splitlines() if x.startswith("  PASS")),
                         sorted(x for x in out2.splitlines() if x.startswith("  PASS")))
        self.assertEqual(out2.count("── часть "), 2)
        rc, out = self.run_par(d, "-j", "2", K3_MINI_BAD="1")
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL внесённая поломка", out)
        self.assertIn("FAIL часть 2/2", out)
        self.assertTrue(out.rstrip().endswith("ЕСТЬ ПРОБЛЕМЫ"), out)

    def test_part_without_summary_is_red(self):
        d = self.mini()
        p = os.path.join(d, "tools", "k3_install_test.sh")
        with open(p, encoding="utf-8") as f:
            s = f.read()
        with open(p, "w", encoding="utf-8") as f:
            f.write(s.replace('ok "третий"', 'exit 0'))
        rc, out = self.run_par(d, "-j", "2")
        self.assertEqual(rc, 1, out)
        self.assertIn("без итога «ВСЁ ЗЕЛЕНО»", out)

    def test_sum_of_parts_matches_the_whole_run(self):
        """REV-speed-2b С7: a check of a part that silently did not run (an «if» without «else» on a state a section
        before the cut made by a command — not replayed) — the sum of the parts below the number of the script run
        as it is on the same code: red. No run of this code as it is — green, said so."""
        d = self.mini()
        p = os.path.join(d, "tools", "k3_install_test.sh")
        with open(p, encoding="utf-8") as f:
            s = f.read()
        s = s.replace('[ "$(helper a)" = h-a ]', 'touch "$W/made"\n[ "$(helper a)" = h-a ]', 1)
        s = s.replace('echo "── 3. третий ──"', '[ -f "$W/made" ] && ok "файл раздела 1"\necho "── 3. третий ──"', 1)
        with open(p, "w", encoding="utf-8") as f:
            f.write(s)
        rc, out = self.run_par(d, "-j", "2")
        self.assertEqual(rc, 0, out)                             # nothing to compare with yet
        self.assertIn("ВСЁ ЗЕЛЕНО (8) — 2 частей рядом, сверки с прогоном одним скриптом нет", out)
        rc, out = self.run_par(d, "-j", "1")
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS файл раздела 1", out)
        self.assertIn("ВСЁ ЗЕЛЕНО (9)", out)
        rc, out = self.run_par(d, "-j", "2")
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL сумма частей 8 ≠ 9", out)
        self.assertTrue(out.rstrip().endswith("ЕСТЬ ПРОБЛЕМЫ"), out)
        rc, out = self.run_par(d, "-j", "2", K3_OLD_INSTALL=p)   # other code (the old install.sh) — no number for it
        self.assertEqual(rc, 0, out)
        with open(p, "w", encoding="utf-8") as f:                # the check made whole again: the same in both
            f.write(s.replace('[ -f "$W/made" ] && ok', '[ -f "$W/made" ] || touch "$W/made"; ok'))
        self.assertEqual(self.run_par(d, "-j", "1")[0], 0)
        rc, out = self.run_par(d, "-j", "2")
        self.assertEqual(rc, 0, out)
        self.assertIn("ВСЁ ЗЕЛЕНО (9) — 2 частей рядом, как одним скриптом", out)

    def test_bad_jobs(self):
        d = self.mini()
        for a in (["-j", "0"], ["-j", "x"], ["--zz"]):
            rc, out = self.run_par(d, *a)
            self.assertEqual(rc, 2, (a, out))


if __name__ == "__main__":
    unittest.main()

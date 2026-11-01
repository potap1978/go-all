"""T8 (этап 1): одновременные записи панели на макете в настоящем Chrome — tools/k3_writes_check.mjs (SE §7 «Версия
политики», таблица «Одновременные записи — сценарии», D-095). Без Chrome или Node с WebSocket — пропуск."""
import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def scenario_count():
    """Число сценариев — из самого скрипта (ключи SCENARIOS), а не литералом: новый сценарий не ломает обёртку."""
    with open(os.path.join(ROOT, "tools", "k3_writes_check.mjs"), encoding="utf-8") as f:
        src = f.read()
    block = src.split("const SCENARIOS = {", 1)[1].split("\n};", 1)[0]
    return len(re.findall(r'^  async (?:"[a-z0-9-]+"|[a-z][a-z0-9]*)\(\)', block, flags=re.M))


@unittest.skipUnless(shutil.which("node"), "нет node")
class WritesCheckTest(unittest.TestCase):
    def test_writes_scenarios(self):
        r = subprocess.run(["node", os.path.join(ROOT, "tools", "k3_writes_check.mjs")], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=600)
        out = r.stdout.decode("utf-8", "replace")
        if r.returncode == 77:
            self.skipTest(out.strip()[:200])
        self.assertEqual(r.returncode, 0, out[-3000:])
        self.assertIn("WRITES OK: %d сценариев" % scenario_count(), out)


if __name__ == "__main__":
    unittest.main()

"""Не допускать имён аргументов, которые jq 1.6 считает ключевыми словами.

Локальный jq 1.7+ их принимает и скрывает ошибки установки на Ubuntu 22.04.
Сквозные наборы проверяют сами операции; эта проверка работает с любой версией jq.
"""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
KEYWORDS = {
    "and", "as", "break", "catch", "def", "elif", "else", "end", "foreach",
    "if", "import", "include", "label", "module", "or", "reduce", "then", "try",
}
ARGUMENT = re.compile(r"--arg(?:json)?\s+([A-Za-z_][A-Za-z_0-9]*)\b")


class JqCompatibility(unittest.TestCase):
    def test_product_arguments_are_compatible_with_jq_16(self):
        paths = [ROOT / "install.sh", ROOT / "bootstrap.sh"]
        for folder in ("lib", "admin-web", "goamnezia-bot"):
            paths.extend((ROOT / folder).rglob("*.sh"))
        errors = []
        for path in sorted(paths):
            source = path.read_text(encoding="utf-8")
            for match in ARGUMENT.finditer(source):
                if match[1] in KEYWORDS:
                    line = source.count("\n", 0, match.start()) + 1
                    errors.append(f"{path.relative_to(ROOT)}:{line}: {match[0]}")
        self.assertFalse(errors, "Ключевые слова jq 1.6 в аргументах:\n" + "\n".join(errors))

    @unittest.skipUnless(shutil.which("jq"), "нужен jq")
    def test_first_edge_alert_recovers_missing_or_empty_state(self):
        bash = os.environ.get("BASH5") or shutil.which("bash") or "bash"
        for initial in (None, "", " \n\t", "{broken", "[]", "{}"):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory(prefix="ga-jq-compat-") as tmp:
                state = Path(tmp) / "edge_alerts.json"
                if initial is not None:
                    state.write_text(initial, encoding="utf-8")
                p = subprocess.run([bash, "-c", r'''
set -uo pipefail
source "$1/lib/edgeips.sh"
export GOAMNEZIA_STATE_DIR="$2"
_ga_edge_alert_set 203.0.113.10 dark 100 90 true
''', "jq-compat", str(ROOT), tmp], capture_output=True, text=True, timeout=10)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(json.loads(state.read_text()), {
                    "203.0.113.10": {"state": "dark", "notified": 100, "since": 90, "announced": True},
                })


if __name__ == "__main__":
    unittest.main()

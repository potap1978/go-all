"""REV-s1-rel-3 П-2 (ветка выпуска 3.3.7): в плитке «Сервер» проценты одним видом — слитно, как велит правило текстов
3.3.6 (tools/k3_text_check.py, pct) и как пишет сервер в находках светофора (каталог gw_host: «{pct}%»). Строки ЦП и
«соседи по серверу» берут fmtPct панели — настоящая функция из app.js в node, RU и EN; находки макета — тем же видом.
Node-less: the formatter part is skipped."""

import json
import os
import re
import shutil
import subprocess
import unittest

from tests import jsparts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
SPACED = re.compile(r"\d[   ]%")


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


def fmt(lang, locale, values):
    js = read(STATIC, "app.js")
    src = "const LANG = %s, LOCALE = %s, DASH = \"—\";\n%s\n%s\n%s\nconsole.log(JSON.stringify(%s.map(fmtPct)));" % (
        json.dumps(lang), json.dumps(locale), jsparts.const(js, "known"), jsparts.const(js, "NF1"),
        jsparts.const(js, "fmtPct"), json.dumps(values))
    res = subprocess.run(["node", "-e", src], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if res.returncode != 0:
        raise AssertionError(res.stderr.decode("utf-8", "replace")[-1500:])
    return json.loads(res.stdout.decode("utf-8"))


class PercentTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "нет node")
    def test_fmt_pct_joined_in_both_languages(self):
        self.assertEqual(fmt("ru", "ru-RU", [12, 0.3, 97, None]), ["12,0%", "0,3%", "97,0%", "—"])
        self.assertEqual(fmt("en", "en-US", [12, 0.3]), ["12.0%", "0.3%"])

    def test_server_findings_are_joined(self):
        """Находки светофора с сервера (каталог gw_host RU/EN) — «{pct}%» слитно: тот же вид, что у fmtPct."""
        for lang in ("ru", "en"):
            doc = json.loads(read(ROOT, "lib", "kaskad", "i18n", "catalog", lang, "gw_host.json"))
            bad = [k for k, v in doc.items() if isinstance(v, str) and re.search(r"\}[   ]%", v)]
            self.assertEqual(bad, [], lang)

    def test_mock_findings_are_joined(self):
        """Макет (?mock=1) показывает находки, как сервер: без «98 %»."""
        for name in ("dev-mock.js", "dev-mock-en.js"):
            lines = [x for x in read(STATIC, name).splitlines() if 'unit: "pct"' in x or "занято" in x or "теряет" in x]
            self.assertTrue(lines, name)
            self.assertEqual([x.strip()[:80] for x in lines if SPACED.search(x)], [], name)

    def test_panel_code_joins_the_percent(self):
        """REV-337-final П-1: код панели не дописывает « %» с пробелом к числу (деталь выхода T8: ось потерь, строка
        «Потери») — весь static/*.js и pages/*.js, кроме словарей (тексты проверяет tools/k3_text_check.py)."""
        bad = []
        for sub in ("", "pages"):
            d = os.path.join(STATIC, sub)
            for name in sorted(os.listdir(d)):
                if not name.endswith(".js") or "i18n" in name:
                    continue
                for i, line in enumerate(read(d, name).splitlines(), 1):
                    if re.search(r"""\+\s*["'`][\s\u00a0\u202f]+%""", line):
                        bad.append("%s:%d" % (os.path.join(sub, name), i))
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()

"""Kaskad 3.3.6 — the release: one version everywhere it is shown or compared, and «Что нового» for people.

- install.sh ``KASKAD_VERSION`` (goInstaller and the update of the menu read it) = ``kaskad.__version__`` (kaskadctl,
  the panel, the bot, MCP, the backup); tools/k3_install_test.sh checks the same on an installed node (kaskadctl version).
- README: the heading of Kaskad 3 names the version; the entry of 3.3.6 is in Russian and in English, in plain words —
  no codes of the tasks (REV-…, З-1, П-2, С4, F0, М12, T11, R2, D-012, КТ1…); it says the security fix 3.3.5.1 is in
  it, and the fix itself is (the checked address of a key in the parser, its tests, the entry of 3.3.5.1): a node on
  3.3.5.1 is offered 3.3.6 (3.3.5.1 < 3.3.6) and must not lose the fix.
"""
import os
import re
import unittest

import kaskad

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def text(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def entry(readme, version):
    """The entry «**<version> — …**» of «Что нового» up to the next version's entry."""
    i = readme.index("**%s — " % version)
    m = re.compile(r"(?m)^\*\*\d+(?:\.\d+){2,3} — ").search(readme, i + 1)   # 3.3.5.1 — four numbers
    return readme[i:m.start() if m else len(readme)]


class ReleaseVersionTest(unittest.TestCase):
    def test_install_sh_and_the_core_say_one_version(self):
        m = re.search(r'(?m)^KASKAD_VERSION="([^"]+)"', text("install.sh")[:4096])
        self.assertIsNotNone(m, "KASKAD_VERSION is not near the top of install.sh (goInstaller reads it there)")
        self.assertEqual(m.group(1), kaskad.__version__)

    def test_readme_names_the_version(self):
        readme = text("README.md")
        head = re.search(r"(?m)^## Kaskad 3 \(версии ([^)]*)\)", readme)
        self.assertIsNotNone(head)
        self.assertTrue(head.group(1).endswith(" и " + kaskad.__version__), head.group(1))
        self.assertIn("**%s — " % kaskad.__version__, readme)


class WhatsNew336Test(unittest.TestCase):
    def setUp(self):
        self.e = entry(text("README.md"), "3.3.6")

    def test_russian_and_english(self):
        ru, sep, en = self.e.partition("*In English.*")
        self.assertTrue(sep, "no English part of 3.3.6")
        for words in ("мобильная версия", "Поиск", "⌘K", "Ctrl+K", "Внешний вид", "goAmnezia Pro", "Мелкие исправления"):
            self.assertIn(words, ru)
        for words in ("mobile version", "Search", "⌘K", "Ctrl+K", "Appearance", "goAmnezia Pro", "Small fixes"):
            self.assertIn(words, en)
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", re.sub(r"<!--.*?-->", "", en, flags=re.S)), "Cyrillic in the English part")

    def test_no_codes_of_the_tasks(self):
        said = re.sub(r"<!--.*?-->", "", self.e, flags=re.S)
        for code in (r"\bREV-", r"\b[ЗПСНТМВ]-?\d+\b", r"\b[TRD]-?\d+\b", r"\bКТ\d", r"\bF\d\b", r"\bBACKLOG", r"\bR-33"):
            self.assertIsNone(re.search(code, said), code)

    def test_the_security_fix_3351_is_said(self):
        ru, _sep, en = self.e.partition("*In English.*")
        self.assertIn("исправление безопасности 3.3.5.1", ru)
        self.assertIn("security fix of 3.3.5.1", en)
        self.assertNotIn("<!--", self.e)                      # no place kept for it any more: it is in
        readme = text("README.md")
        fix = entry(readme, "3.3.5.1")
        self.assertIn("исправление безопасности", fix)
        self.assertIn("reboot", fix)
        self.assertLess(readme.index("**3.3.6 — "), readme.index("**3.3.5.1 — "))
        self.assertLess(readme.index("**3.3.5.1 — "), readme.index("**3.3.5 — "))
        self.assertRegex(readme, r", 3\.3\.5\.1(?:,| и) 3\.3\.6[,)\s]")   # 3.3.7 and later come after it


class SecurityFix3351InTest(unittest.TestCase):
    """The fix of 3.3.5.1 came in with the merge of dev (not only its words): its tests and the parser that refuses an
    address with the shell in it."""

    def test_its_tests_are_here(self):
        src = text(os.path.join("tests", "test_3351_cmdinj.py"))
        for name in ("test_bad_hosts_are_bad_link", "test_bad_ports_are_bad_link", "test_key_ping_runs_nothing",
                     "test_menu_chain_runs_nothing", "test_list_has_no_control_bytes", "test_leg_address_without_prefix_route",
                     "test_no_terminal_sequences_from_a_key"):
            self.assertIn("def %s(" % name, src)

    def test_the_parser_refuses_a_bad_address(self):
        src = text("install.sh")
        self.assertIn("# PARSER_VER=28 ", src)              # 3.3.6: v25 (селекторы), v26 (точка в конце), v27 (REV-336-dot-sec); 28 — с этапом 1
        from tests.test_334_xray import parser               # egress_parse.py exactly as install.sh writes it
        ep = parser()
        self.assertGreaterEqual(ep.PARSER_VERSION, 26)
        for link in ("vless://u@$(touch%20/tmp/x):443?type=tcp#a", "vless://u@a.example:99999#a",
                     "vless://u@[::1]x:443#a", "vless://u@a%20b.example:443#a", "vless://u@`id`.example:443#a"):
            self.assertEqual(ep.vless_problem(link), "bad_link", link)
        self.assertNotEqual(ep.vless_problem("vless://u@203.0.113.1:443?type=tcp&security=none#a"), "bad_link")
        self.assertEqual(ep.awg_problem({"peer": {"endpoint": "$(id):51820"}}), "bad_link")
        self.assertEqual(ep.awg_problem({"peer": {"endpoint": "203.0.113.1:51820"}, "address": "0.0.0.0/0"}), "bad_addr")
        self.assertEqual(ep._term("a\x1b]0;x\x07\x9bb"), "a ]0;x b")

if __name__ == "__main__":
    unittest.main()

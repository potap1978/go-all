# -*- coding: utf-8 -*-
"""Модуль ядра AmneziaWG, этап Б: закреплённое — то, что сказано в коде, документах и лежит в репозитории.

lib/kmod.sh собирает модуль ядра от root: качает архив тега, накладывает патчи и грузит модуль. Всё, что он
считает «своим», закреплено константами, и любое расхождение должно краснеть здесь, а не на сервере:
  • sha256 патчей в lib/upstream/kmod/ = константам lib/kmod.sh = таблице PATCHES.md (= навыку Kaskad);
  • sha256 архива тега и сам тег — одни в коде, PATCHES.md и docs/KMOD_PLAN.md;
  • метка набора: последний ханк awg-kmod-31.patch дописывает ровно GA_KMOD_LABEL;
  • патчи — GPL-2.0 (сказано в PATCHES.md и в шапках патчей);
  • строка подъёма интерфейса, которую заменяет блок движка, есть в start.sh приложения и в нашей копии;
  • блок движка и блок MSS не содержат строк, перед которыми вставляют свои строки goWARP (tail -f, exit) и
    изоляция (iptables, tail с начала строки), и строки AUTOINSTALL в dkms.conf продукт не пишет (KMOD-09).

Запуск: python3 -m unittest tests.test_kmod_assets
"""
import hashlib
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
KMOD = ROOT / "lib" / "kmod.sh"
AMNEZIA = ROOT / "lib" / "amnezia.sh"
PATCH_DIR = ROOT / "lib" / "upstream" / "kmod"
PATCHES_MD = PATCH_DIR / "PATCHES.md"
PLAN = ROOT / "docs" / "KMOD_PLAN.md"
SKILL_PATCHES = {
    "awg-kmod-218.patch": "edcb1dfbb8bdd8e960b91f03e302fe3d4330f176e6a25c55e43d026fd5ed907d",
    "awg-kmod-31.patch": "5e7ed57d114b5462ee5bf9d38b6666a41ce4ff90a39efb3980b36fcff9b7b603",
}
TAG_SHA = "e32fa46f1b6f9e319c5261f0b0765c3a9261e40cb7c4616fd73500da961a2932"


def const(name):
    m = re.search(r"""^%s=(?:"([^"]*)"|'([^']*)')""" % re.escape(name), KMOD.read_text(encoding="utf-8"), re.M)
    if not m:
        raise AssertionError("нет константы %s в lib/kmod.sh" % name)
    return m.group(1) if m.group(1) is not None else m.group(2)


def heredoc(text, fn):
    """Текст heredoc <<'EOF' внутри функции fn (lib/*.sh)."""
    m = re.search(r"^%s\(\) \{.*?<<'EOF'\n(.*?)\nEOF" % re.escape(fn), text, re.S | re.M)
    if not m:
        raise AssertionError("нет heredoc в %s" % fn)
    return m.group(1)


class KmodPinnedAssets(unittest.TestCase):

    def test_patch_files_match_the_pinned_hashes(self):
        consts = {"awg-kmod-218.patch": const("GA_KMOD_PATCH_218_SHA"), "awg-kmod-31.patch": const("GA_KMOD_PATCH_31_SHA")}
        table = PATCHES_MD.read_text(encoding="utf-8")
        for name, want in SKILL_PATCHES.items():
            with self.subTest(patch=name):
                got = hashlib.sha256((PATCH_DIR / name).read_bytes()).hexdigest()
                self.assertEqual(got, want, "файл патча изменён — это уже не набор Kaskad")
                self.assertEqual(consts[name], want, "константа lib/kmod.sh разошлась с файлом")
                self.assertRegex(table, r"\| `%s` \| `%s` \|" % (re.escape(name), want))

    def test_tag_and_archive_hash_are_the_same_everywhere(self):
        self.assertEqual(const("GA_KMOD_SHA256_PIN"), TAG_SHA)
        self.assertEqual(const("GA_KMOD_VER"), "3.1.20260906")
        for doc in (PATCHES_MD, PLAN):
            with self.subTest(doc=doc.name):
                self.assertIn(TAG_SHA[:8], doc.read_text(encoding="utf-8"))

    def test_label_is_what_the_set_writes(self):
        label = "3.1.20260906+kaskad-k31.1"
        self.assertEqual(const("GA_KMOD_SET"), "kaskad-k31.1")
        last = (PATCH_DIR / "awg-kmod-31.patch").read_text(encoding="utf-8").split("diff --git a/src/version.h")[-1]
        self.assertIn('+#define WIREGUARD_VERSION "%s"' % label, last)

    def test_patches_are_gpl2(self):
        self.assertIn("GPL-2.0", PATCHES_MD.read_text(encoding="utf-8"))
        for name in SKILL_PATCHES:
            with self.subTest(patch=name):
                self.assertIn("GPL-2.0", (PATCH_DIR / name).read_text(encoding="utf-8").split("\ndiff --git")[0])

    def test_the_up_line_replaced_by_the_engine_block_exists(self):
        up = const("GA_KMOD_UP_LINE")
        for f in ("start.sh", "start.sh.upstream"):
            with self.subTest(file=f):
                lines = [l.strip() for l in (ROOT / "lib" / "upstream" / "awg" / f).read_text(encoding="utf-8").splitlines()]
                self.assertIn(up, lines)

    def test_blocks_leave_room_for_gowarp_and_isolation(self):
        engine = heredoc(KMOD.read_text(encoding="utf-8"), "ga_kmod_engine_block")
        mss = heredoc(AMNEZIA.read_text(encoding="utf-8"), "ga_amn_mss_cmd")
        for name, block in (("engine", engine), ("mss", mss)):
            for line in block.splitlines():
                with self.subTest(block=name, line=line):
                    self.assertNotRegex(line, r"^[ \t]*exit([ \t]|$)", "goWARP вставил бы свой блок перед этой строкой")
                    self.assertNotIn("tail -f /dev/null", line)
                    self.assertNotRegex(line, r"^(iptables|tail)[ \t]", "строка изоляции встала бы перед ней")
                    self.assertNotRegex(line, r"<\(|(^|\s)\[\[\s|\bfunction\b|\blocal\b", "башизм в start.sh (goWARP: sh -n)")

    def test_dkms_conf_never_gets_autoinstall(self):
        text = KMOD.read_text(encoding="utf-8")
        self.assertNotRegex(text, r'AUTOINSTALL="?(yes|no)')
        self.assertIn("(AUTOINSTALL|REMAKE_INITRD)", text)


if __name__ == "__main__":
    unittest.main()

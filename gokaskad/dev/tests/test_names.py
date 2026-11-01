"""Stage 1, N1: the one rule of a name (docs/k3/SPEC-stage1-rename.md §2, §7): 1-40 code points, NFC, spaces
trimmed and collapsed, control / invisible / bidi characters and «|» refused (ZWJ and ZWNJ allowed), HTML and shell
characters allowed; duplicates by NFKC + casefold; the refusal in RU and EN with code USAGE."""

import os
import re
import unittest

from kaskad import errors, names
from kaskad.i18n import check as icheck
from kaskad.i18n import use_lang

CYR = re.compile("[А-Яа-яЁё]")
FAMILY = "\U0001F468‍\U0001F469‍\U0001F467"          # man ZWJ woman ZWJ girl: 5 code points
CODER = "\U0001F469‍\U0001F4BB"                            # woman technologist
ENGLAND = "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F"


class CheckTest(unittest.TestCase):

    def refused(self, value, reason, what="name"):
        with self.assertRaises(errors.KaskadError) as cm:
            names.check(value, what)
        self.assertEqual(cm.exception.code, errors.USAGE)
        self.assertEqual(cm.exception.data["reason"], reason, repr(value))
        return cm.exception

    def test_length(self):
        self.assertEqual(names.check("a"), "a")
        self.assertEqual(names.check("я" * 40), "я" * 40)
        self.refused("", "empty")
        self.refused("   ", "empty")
        self.refused(None, "empty")
        e = self.refused("я" * 41, "long")
        self.assertEqual((e.data["n"], e.data["max"]), (41, 40))
        self.assertEqual(names.MAX, 40)

    def test_length_in_code_points_after_nfc(self):
        self.assertEqual(names.check("é"), "é")          # NFC: one code point
        self.assertEqual(names.check("é" * 40), "é" * 40)
        self.assertEqual(names.check(FAMILY * 8), FAMILY * 8)       # 40 code points
        self.refused(FAMILY * 8 + "a", "long")

    def test_spaces(self):
        self.assertEqual(names.check("  Вена   2  "), "Вена 2")
        self.assertEqual(names.check("a  b"), "a b")       # no-break spaces collapse too
        self.refused(" 　", "empty")

    def test_refused_characters(self):
        for ch in ("\n", "\r", "\t", "\x00", "\x1b", "\x7f", "\x85", "\x9f", "‮", "‪", "​",
                   "‎", "‏", "⁦", "⁩", "⁠", "﻿", "؜", "­", " ",
                   " ", "|", "\ud800"):
            with self.subTest(ch="U+%04X" % ord(ch)):
                e = self.refused("ab" + ch + "cd", "char")
                self.assertEqual(e.data["char"], "U+%04X" % ord(ch))
                self.assertNotIn(ch, str(e))                           # the bad character is never printed
        self.refused("Вена\n", "char")                                  # a trailing newline is not trimmed away
        # tag characters only inside a flag sequence (REV-s1-w0 П4): «Вена» + a tag looks like «Вена»
        for v in ("Вена\U000E0041", "\U000E0067\U000E007F", "\U0001F3F4\U000E0067x", "\U0001F3F4\U000E0067",
                  ENGLAND + "\U000E0041", "\U000E007F"):
            with self.subTest(v=repr(v)):
                self.refused(v, "char")

    def test_wholly_invisible(self):
        for v in ("\u3164", "\u2800", "\ufe0f", "\u115f\u1160", "\u200d", "\u0301", "\u3164 \u2800"):
            with self.subTest(v=repr(v)):
                self.refused(v, "invisible")
        self.assertEqual(names.check("a\u3164"), "a\u3164")
        self.assertEqual(names.check("\u2764\ufe0f"), "\u2764\ufe0f")

    def test_unassigned_private_control(self):
        """REV-s1-w0-2 B6: unassigned (Cn), private use (Co), surrogates (Cs) and control (Cc) — a refusal with its
        own reason in RU and EN, the character never printed."""
        for v, reason, cp in (("a\u2065", "unassigned", "U+2065"), ("a\uffff", "unassigned", "U+FFFF"),
                              ("\U0010FFFE", "unassigned", "U+10FFFE"), ("a\ue000", "private", "U+E000"),
                              ("\U000F0000", "private", "U+F0000"), ("Вена\U00100000", "private", "U+100000"),
                              ("\uf8ff Apple", "private", "U+F8FF"), ("a\ud800", "char", "U+D800"),
                              ("a\x07", "char", "U+0007")):
            with self.subTest(v=repr(v)):
                e = self.refused(v, reason)
                self.assertEqual(e.data["char"], cp)
                self.assertNotIn(v[-1], str(e))
        with use_lang("ru"):
            ru = str(self.refused("a\u2065", "unassigned", "device"))
            ru_p = str(self.refused("a\ue000", "private", "exit"))
        self.assertTrue(ru.startswith("Имя устройства: знак U+2065") and "Юникод" in ru, ru)
        self.assertTrue(ru_p.startswith("Подпись выхода: знак U+E000") and "частного" in ru_p, ru_p)
        with use_lang("en"):
            en = str(self.refused("a\u2065", "unassigned", "device"))
        self.assertTrue(en.startswith("Device name: the character U+2065"), en)

    def test_new_emoji_on_any_python(self):
        """REV-s1-w0-3 Н4: a code point of the emoji blocks (U+1F000-U+1F0FF, U+1F300-U+1FAFF) unknown to this
        Python is an emoji, not «unassigned»: 🫨 🛜 (Unicode 15), 🫩 🫆 🪉 (16) and the free U+1FAFF, U+1F8FF pass on
        3.9 (Unicode 13) as on 3.14 (16); outside the blocks a Cn is still refused (RN:431 «emoji — yes»)."""
        for cp in (0x1FAE8, 0x1F6DC, 0x1FAE9, 0x1FAC6, 0x1FA89, 0x1FAFF, 0x1F8FF, 0x1F0FF, 0x1F300, 0x1FA00):
            with self.subTest(cp="U+%X" % cp):
                v = "Вена " + chr(cp)
                self.assertEqual(names.check(v), v)
                self.assertEqual(names.check(chr(cp)), chr(cp))           # visible on its own
                self.assertTrue(names.same(v, "вена " + chr(cp)))
                self.assertFalse(names.same(v, "Вена"))
        for v, cp in (("a⁥", "U+2065"), ("a￿", "U+FFFF"), ("a\U00050000", "U+50000"),
                      ("a\U000E0080", "U+E0080"), ("a\U0001FB00\U0010FFFE", "U+10FFFE")):
            with self.subTest(v=repr(v)):
                self.assertEqual(self.refused(v, "unassigned").data["char"], cp)

    def test_allowed(self):
        for v in ("<script>alert(1)</script>"[:40], "Tom & Jerry", '"q" \'s\'', "a\\nb", "-x", "--help", "🙂",
                  FAMILY, CODER, ENGLAND, "می‌خواهم", "Вена · AWG", "100%", "$(id)", "`id`"):
            with self.subTest(v=v):
                self.assertEqual(names.check(v), v)

    def test_type(self):
        self.refused(5, "type")
        self.refused(b"bytes", "type")
        self.refused(["a"], "type")

    def test_texts_ru_en(self):
        with use_lang("ru"):
            ru = str(self.refused("x" * 41, "long", "user"))
            ru_char = str(self.refused("a|b", "char", "device"))
        self.assertTrue(ru.startswith("Имя пользователя"), ru)
        self.assertIn("40", ru)
        self.assertTrue(ru_char.startswith("Имя устройства") and "U+007C" in ru_char, ru_char)
        self.assertIn("Нельзя использовать", ru_char)
        with use_lang("ru"):
            self.assertEqual(str(self.refused("", "empty", "exit")), "Подпись выхода: пусто. Нужно от 1 до 40 знаков.")
        with use_lang("en"):
            for what in names.WHAT + ("unknown",):
                for value, reason in (("", "empty"), ("x" * 41, "long"), ("a\nb", "char"), (1, "type"),
                                      ("\u3164", "invisible"), ("a\u2065", "unassigned"),
                                      ("a\ue000", "private")):
                    msg = str(self.refused(value, reason, what))
                    self.assertIsNone(CYR.search(msg), msg)
                    self.assertNotIn("{", msg)
        with use_lang("en"):
            self.assertTrue(str(self.refused("", "empty", "exit")).startswith("Exit name"))
        e = self.refused("", "empty", "unknown")
        self.assertEqual(e.data["what"], "name")

    def test_catalog(self):
        self.assertEqual(icheck.catalog_problems(["names"]), [])
        icheck.assert_no_cyrillic(self, os.path.join(os.path.dirname(names.__file__), "names.py"))


class SameTest(unittest.TestCase):

    def test_same(self):
        self.assertTrue(names.same("Вена", "вена"))
        self.assertTrue(names.same("Ｖｅｎａ", "Vena"))               # NFKC: full-width letters
        self.assertTrue(names.same("  Мой   телефон ", "мой телефон"))
        self.assertTrue(names.same("é", "É"))
        self.assertFalse(names.same("Вена", "Вена 2"))
        self.assertFalse(names.same("Vena", "Wena"))
        # invisible characters do not make another name (REV-s1-w0 П4)
        self.assertTrue(names.same("Вена", "Вена\u200d"))
        self.assertTrue(names.same("Вена", "Ве\u200cна"))
        self.assertTrue(names.same("Вена", "Вена\U000E0041"))
        self.assertTrue(names.same("Вена", "Вена\ufe0f"))
        self.assertTrue(names.same("Вена", "Вена\u3164"))
        self.assertEqual(names.duplicates([(1, "Вена")], "Вена\u200d"), [1])

    def test_blank_fillers_are_a_space(self):
        """REV-s1-w0-2 B5: a filler or a space of any kind between words is a space — «Вена⠀Вена» is «Вена Вена»;
        the zero-width characters are still dropped (ZWJ inside a word: the same word)."""
        for blank in ("\u3164", "\u2800", "\u115f", "\u1160", "\uffa0", "\u3000", "\u00a0", "\u2009", "\u202f",
                      "\u3164\u3164", " \u2800 "):
            with self.subTest(blank=ascii(blank)):
                self.assertTrue(names.same("Вена" + blank + "Вена", "Вена Вена"))
                self.assertFalse(names.same("Вена" + blank + "Вена", "ВенаВена"))
                self.assertEqual(names.duplicates([(1, "Вена Вена")], "вена" + blank + "вена"), [1])
        self.assertTrue(names.same("Вена\u3164", "Вена"))                    # at the end: trimmed
        self.assertTrue(names.same("Ве\u200dна", "Вена"))                    # zero-width: dropped
        self.assertFalse(names.same("Ве\u200dна", "Ве на"))

    def test_duplicates(self):
        items = [{"id": "u-1", "name": "Вена"}, {"id": "u-2", "name": "Рига"}, {"id": "u-3", "name": "ВЕНА"},
                 {"id": "u-4"}]
        self.assertEqual(names.duplicates(items, "вена"), ["u-1", "u-3"])
        self.assertEqual(names.duplicates(items, "вена", "u-1"), ["u-3"])      # not a duplicate of itself
        self.assertEqual(names.duplicates(items, "Париж"), [])
        self.assertEqual(names.duplicates([("tcp:443", "Сайт"), ("udp:53", "сайт")], "САЙТ", "tcp:443"),
                         ["udp:53"])
        self.assertEqual(names.duplicates(None, "x"), [])


if __name__ == "__main__":
    unittest.main()

"""Придирки стенда КТ3 (3.3.7.1): слова после переименования (П-1, П-2), подсказка Hysteria2 в SSH (П-4)."""

import os
import unittest

from kaskad.gw import rename

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RenameWordsTest(unittest.TestCase):
    def test_a_forward_and_an_inbound_have_no_key_words(self):
        self.assertEqual(rename.note("forward", lang="ru"), "Правила проброса не меняются.")
        self.assertIn("Настройки протокола", rename.note("inbound", lang="ru"))
        self.assertEqual(rename.note("exit", lang="ru"), "Сам ключ и настройки не меняются.")     # an exit has a key
        for what in ("forward", "inbound"):
            self.assertNotIn("ключ и настройки", rename.note(what, lang="ru"))
            self.assertNotIn("The key and", rename.note(what, lang="en"))

    def test_several_kinds_each_hint_says_whose(self):
        d = {"access": ["wg", "vless", "socks5", "hy2"]}
        text = rename.note("device", obj=d, lang="ru")
        self.assertIn("WireGuard: В приложении имя останется прежним", text)
        self.assertRegex(text, r"VLESS[^:]*Hysteria2: Имя обновится")
        self.assertIn("SOCKS5: Подключение работает как раньше", text)
        one = rename.note("device", obj={"access": ["vless"]}, lang="ru")
        self.assertEqual(one, "Имя обновится в приложении при следующем обновлении подписки.")    # one kind: as before


class Hy2HintTest(unittest.TestCase):
    def test_the_ssh_hint_names_real_commands(self):
        for lang in ("ru", "en"):
            with open(os.path.join(ROOT, "lib", "kaskad", "i18n", "sh", "gwhy2.%s.sh" % lang), encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn("gw inbound hy2", text)
            self.assertIn("gw inbound port | option hy2", text)

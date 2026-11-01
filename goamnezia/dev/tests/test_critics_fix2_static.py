# -*- coding: utf-8 -*-
"""SSH, бот, словари: правки второго круга критиков 16.10 (CRITICS-FIX-2), которые проверяются по тексту кода.
  A2-12  стрелки трафика — с точки зрения пользователя везде: ↓ скачано (tx), ↑ отдано (rx);
Запуск: python3 -m unittest tests/test_critics_fix2_static.py
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rd(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class TrafficArrows(unittest.TestCase):
    def test_ssh_arrows_are_from_the_user_side(self):
        src = rd("install.sh")
        self.assertIn('"$(_pad "↓ $(_human_bytes "$tx")" 11)" "$(_pad "↑ $(_human_bytes "$rx")" 11)"', src)
        self.assertNotIn('"↓ $(_human_bytes "$rx")"', src)
        for period in ("today", "month"):
            self.assertIn("↓ $(_human_bytes \"$(printf '%%s' \"$js\" | jq -r '.%s.tx // 0')\")" % period, src)
        self.assertIn('"$(_pad "$(t col_down)" 11)" "$(_pad "$(t col_up)" 11)"', src)
        ru = rd("lib", "lang", "ru.d", "menu.sh")
        self.assertIn('I18N[col_down]="Скачано"', ru)
        self.assertIn("↓ скачано пользователями · ↑ отдано", ru)

    def test_bot_arrows_are_from_the_user_side(self):
        src = rd("goamnezia-bot", "bot.py")
        i = src.index('"st_traffic_today_fmt",')
        self.assertRegex(src[i:i + 400], r'\(today or \{\}\)\.get\("tx"\).*\(today or \{\}\)\.get\("rx"\)')
        # Строки пиров в том же сообщении — тоже ↓ = tx (R-3; поведение — LongScreensTests в test_bot_features)
        j = src.index('"  %s <code>%s</code> ↓ %s ↑ %s" % (')
        self.assertRegex(src[j:j + 300], r'human_bytes\(peer\.get\("tx"\), user_id\), human_bytes\(peer\.get\("rx"\), user_id\)')


class RunstaticNits(unittest.TestCase):
    """RUNSTATIC-NIT: подсказка 2.0 мастера с «и/too» и на английском; кавычки «…» в en menu.sh; ловушка .conf до
    записи файла (T-1); локаль в test_kmodb_dryrun.sh."""

    def test_wizard_20_note_ru_and_en(self):
        ru, en = rd("lib", "lang", "ru.d", "menu.sh"), rd("lib", "lang", "en.d", "menu.sh")
        self.assertIn("его понимают и прошлые версии приложений Amnezia, и роутеры Keenetic (3.1 — пока только в dev-прошивке)", ru)
        self.assertIn("older Amnezia app versions and Keenetic routers understand it too (3.1 only in dev firmware so far)", en)
        self.assertIn('I18N[wz_awg1_for]="older clients and MikroTik, OpenWrt routers; Keenetic routers are fine with 2.0"', en)

    def test_en_menu_one_quote_style(self):
        en = rd("lib", "lang", "en.d", "menu.sh")
        self.assertEqual([ln for ln in en.splitlines() if ln.startswith("I18N[") and '\\"' in ln], [])

    def test_t1_conf_trap_before_the_file(self):
        src = rd("install.sh")
        i = src.index("trap '_key_conf_signal INT' INT")
        j = src.index('( umask 077; printf \'%s\\n\' "$conf" > "$tmp" )', i)
        self.assertLess(i, j)
        self.assertLess(src.index("trap '_key_conf_signal HUP' HUP", i), j)

    def test_kmodb_sets_the_locale_itself(self):
        src = rd("tests", "test_kmodb_dryrun.sh")
        self.assertRegex(src, r'\[ "\$\(uname -s 2>/dev/null\)" = Darwin \]; then\n    export LANG=C\.UTF-8')


class ReviewNits(unittest.TestCase):
    """REVIEW-CRITICS-FIX-2 R-7: в предупреждении о чужих A-записях речь о посетителях домена (страницы, подписки,
    проверка LE), а не о пользователях VPN."""

    def test_r7_dns_partial_says_visitors(self):
        src = rd("admin-web", "static", "app.js")
        for key, want in (("ru", "часть посетителей могут попасть"), ("en", "some visitors may land")):
            self.assertIn(want, src, key)
        self.assertNotIn("часть пользователей могут попасть", src)


if __name__ == "__main__":
    unittest.main()

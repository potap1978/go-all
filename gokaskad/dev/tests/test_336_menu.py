"""Kaskad 3.3.6, the SSH menu «Прокси для API Telegram» (8 → 7), С4 of the stand 3.3.5: a key is named in the list as
the gateway names it (``gw exits``: its own label «Мой выход», the panel and menu 17 → 3 say the same), not by the name
the subscription wrote into the line («3GB left»). No core, no name of its own — the name of the line, as before."""
import os
import stat
import unittest

from tests import test_egress_i18n as egress_i18n

VLESS = egress_i18n.VLESS

TWO = VLESS.replace("#Riga", "#3GB%20left").replace("198.51.100.1", "198.51.100.2")
EXITS = ('{"ok": true, "exits": [{"id": "e-1111111111", "index": 1, "name": "Riga", "named": true},'
         ' {"id": "e-2222222222", "index": 2, "name": "Мой выход", "named": true}]}')


class EgressNamesTest(egress_i18n.ScreenTest):
    # the screens of ScreenTest are tested there: here only what С4 changed
    test_english_screen_has_no_russian = test_russian_screen_as_before = test_marks_only_for_the_panel = None

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.dir, "root", "etc", "kaskad", "egress", "upstream.txt"), "w") as f:
            f.write(VLESS + "\n" + TWO + "\n")

    def ctl(self, answer):
        d = os.path.join(self.dir, "root", "opt", "kaskad", "bin")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "kaskadctl")
        with open(os.path.join(self.dir, "exits.json"), "w", encoding="utf-8") as f:
            f.write(answer)
        with open(p, "w") as f:
            f.write('#!/bin/sh\necho "$*" >> "%s/ctl.log"\n[ "$1 $2" = "gw exits" ] && cat "%s/exits.json"\nexit 0\n'
                    % (self.dir, self.dir))
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)

    SCREEN = ('egress_ensure_parser\n'
              'EGR_ST=on; EGR_OPING=120; EGR_ACTIVE=1; EGR_L30="0.5%"; EGR_WARN=""\n'
              '_egr_names_refresh; _egr_paint 1 1; _egr_names_refresh; _egr_paint 2 1\n')

    def calls(self):
        p = os.path.join(self.dir, "ctl.log")
        if not os.path.exists(p):
            return 0
        with open(p) as f:
            return f.read().count("gw exits")

    def test_the_label_of_the_gateway(self):
        self.ctl(EXITS)
        rc, out = self.run_sh("ru", self.SCREEN)
        self.assertEqual(rc, 0, out)
        self.assertIn(" 2) Мой выход ", out)
        self.assertNotIn("3GB left", out)
        self.assertIn(" 1) Riga ", out)
        self.assertEqual(self.calls(), 1)                     # the pool did not change: asked once

    def test_without_a_name_of_its_own_or_a_core_the_line(self):
        self.ctl(EXITS.replace('"Мой выход", "named": true', '"AmneziaWG · 198.51.100.2", "named": false'))
        rc, out = self.run_sh("ru", self.SCREEN)
        self.assertIn(" 2) 3GB left ", out)
        os.remove(os.path.join(self.dir, "root", "opt", "kaskad", "bin", "kaskadctl"))
        rc, out = self.run_sh("ru", self.SCREEN)
        self.assertEqual(rc, 0, out)
        self.assertIn(" 2) 3GB left ", out)
        self.ctl("not json")
        rc, out = self.run_sh("ru", self.SCREEN)
        self.assertIn(" 2) 3GB left ", out)

    def test_no_terminal_bytes_from_a_name(self):
        # 3.3.5.1 at the merge into 3.3.6: the label of the gateway goes to the terminal as the lines of the parser do —
        # no ESC/OSC, BEL, C1 (CSI U+009B) or «\\» (echo -e)
        self.ctl(EXITS.replace('"Мой выход"', '"Мой\\u202e\\u001b]0;pwn\\u0007\\u009b2J\\\\e\\u2066"'))
        rc, out = self.run_sh("ru", self.SCREEN)
        self.assertEqual(rc, 0, out)
        for bad in ("\x1b", "\x07", "\x9b", "\\", "\u202e", "\u2066"):   # 3.3.6: and format chars
            self.assertNotIn(bad, out)
        self.assertIn(" 2) Мой ]0;pwn 2J e", out)

    def test_handover_words_have_no_terminal_bytes(self):
        # egress_handover (3.3.6: the cause of the core on another failure) prints the words of the core with echo -e
        d = os.path.join(self.dir, "root", "opt", "kaskad", "bin")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "kaskadctl")
        for rc, body in ((1, '{"ok": false, "error": "state.json \\u200f\\u001b]0;x\\u0007\\\\033[2J broken"}'),
                         (0, '{"ok": true, "routes": 2, "text": "Moved to \\u2066\\u009b2J\\\\e[1m twin"}')):
            with open(os.path.join(self.dir, "ho.json"), "w") as f:
                f.write(body)
            with open(p, "w") as f:
                f.write('#!/bin/sh\ncat "%s/ho.json"\nexit %d\n' % (self.dir, rc))
            os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
            code, out = self.run_sh("en", "egress_handover 2; echo rc=$?\n")
            for bad in ("\x1b", "\x07", "\x9b", "\\", "\u200f", "\u2066"):
                self.assertNotIn(bad, out)
            self.assertIn("state.json ]0;x 033[2J broken" if rc else "Moved to 2J e[1m twin", out)
            self.assertIn("rc=1" if rc else "rc=0", out)

    def test_refreshed_after_a_change(self):
        self.assertTrue('_egr_warn_refresh() { EGR_WARN=$(egress_awg_warnings); EGR_NAMES_SUM=""; }'
                        in egress_i18n.source())


if __name__ == "__main__":
    unittest.main()

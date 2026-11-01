"""Stage 1, B2 (REV-s1-t4 З6): ``group:<gid>`` alike everywhere — the CLI says its fail policy, the copy (.kbk) names
the group, not its id."""

import unittest

from kaskad import backup, i18n
from kaskad.gw import cli, policy as P


class GroupRouteWordsTest(unittest.TestCase):
    def setUp(self):
        cm = i18n.use_lang("ru")
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)

    def test_cli_shows_the_fail_policy_of_a_group_route(self):
        self.assertTrue(cli.fail_shown({"via": "group:g-123456", "fail": "direct"}))
        self.assertTrue(cli.fail_shown({"via": "pool"}))
        self.assertTrue(cli.fail_shown({"via": "exit:e-0000000001"}))
        self.assertFalse(cli.fail_shown({"via": "direct", "fail": "direct"}))
        self.assertFalse(cli.fail_shown({"via": "block", "fail": "direct"}))
        self.assertIn("при отказе — напрямую", cli._route_text({"via": "group:g-123456", "fail": "direct"}))
        with i18n.use_lang("en"):
            w = backup._route_words({"via": "group:g-123456", "fail": "block"}, {}, {"g-123456": "Europe"})
        self.assertEqual(w, "via group “Europe”, on failure — block")

    def test_the_copy_names_the_group(self):
        g = {"exit_policy": {"groups": [P.default_group("main", "abroad", "compat"),
                                        P.default_group("g-123456", "abroad", "compat", name="Европа")]}}
        names = backup._group_names(g)
        self.assertEqual(names["g-123456"], "Европа")
        self.assertEqual(names["main"], P.label({"id": "main"}))
        w = backup._route_words({"via": "group:g-123456", "fail": "direct"}, {}, names)
        self.assertEqual(w, "через группу «Европа», при отказе — напрямую")
        w = backup._route_words({"via": "group:g-654321", "fail": "block"}, {}, names)   # a group the copy lacks
        self.assertEqual(w, "через группу «g-654321», при отказе — блок")
        self.assertEqual(backup._group_names({"exit_policy": {"groups": "broken"}}), {})
        self.assertEqual(backup._group_names(None), {})


if __name__ == "__main__":
    unittest.main()

"""PLAN-order Ш3: the way of choosing of a tier in ``gw policy`` / ``gw groups`` and in the JSON of MCP
``exit_groups_list`` — «По порядку (вручную)» next to the method kept, «по соединениям» where neither acts, RU/EN, the
count of exits and keys for n = 1, 2, 5, 21."""

import unittest

from kaskad.gw import cli_policy
from kaskad.i18n import use_lang


def group(n, method="best_rtt", order=None, role="abroad", gid="main", per_connection=False):
    t = {"id": "t1", "exits": ["e-%010d" % i for i in range(n)], "method": method}
    if order:
        t["order"] = order
    return {"id": gid, "name": "" if gid == "main" else "Дом", "role": role, "preset": "custom", "fail": "block",
            "tiers": [t], "per_connection": per_connection}


class PolicyLinesTest(unittest.TestCase):
    def tier_line(self, g):
        return [x for x in cli_policy.policy_lines({"groups": [g]}) if x.startswith("  Уровень 1") or
                x.startswith("  Level 1")]

    def test_counts_and_the_method(self):
        for n, ru, en in ((1, "1 выход", "1 exit"), (2, "2 выхода", "2 exits"), (5, "5 выходов", "5 exits"),
                          (21, "21 выход", "21 exits")):
            line = self.tier_line(group(n))[0]
            self.assertTrue(line.startswith("  Уровень 1 · Лучший отклик · %s: " % ru), line)
            with use_lang("en"):
                self.assertTrue(self.tier_line(group(n))[0].startswith("  Level 1 · Best response · %s: " % en))
        for n, ru in ((1, "1 ключ"), (2, "2 ключа"), (5, "5 ключей"), (21, "21 ключ")):
            g = group(n, method="even", role="ru", gid="g-1a2b3c")
            self.assertIn("· Поровну · %s: " % ru, self.tier_line(g)[0])

    def test_manual_order_is_said_with_the_method_kept(self):
        lines = cli_policy.policy_lines({"groups": [group(4, order="manual")]})
        i = next(i for i, x in enumerate(lines) if x.startswith("  Уровень 1"))
        self.assertIn(" · По порядку (вручную) · 4 выхода: ", lines[i])
        self.assertEqual(lines[i + 1], "    Автовыбор выключен: новые устройства идут на первый рабочий выход сверху. "
                                       "Способ «Лучший отклик» сохранён.")
        with use_lang("en"):
            lines = cli_policy.policy_lines({"groups": [group(4, order="manual", method="weights")]})
        i = next(i for i, x in enumerate(lines) if x.startswith("  Level 1"))
        self.assertIn(" · In order (manual) · 4 exits: ", lines[i])
        self.assertEqual(lines[i + 1], "    Automatic choice is off: new devices go to the first working exit from the "
                                       "top. “By weight” is kept.")
        lines = cli_policy.policy_lines({"groups": [group(3, order="manual", per_connection=True)]})
        self.assertIn(" · По соединениям — порядок не действует · 3 выхода: ", self.tier_line(group(3, order="manual",
                                                                                                per_connection=True))[0])
        self.assertFalse([x for x in lines if "Автовыбор" in x])             # the flag does not act there: not said


class GroupsLinesTest(unittest.TestCase):
    def doc(self, *groups):
        out = []
        for g in groups:
            out.append(dict(g, label="Основная" if g["id"] == "main" else g["name"], exits=g["tiers"][0]["exits"],
                            tiers=[dict(t, label="Уровень 1") for t in g["tiers"]]))
        return {"groups": out, "no_exit": 0}

    def test_manual_tiers_under_the_table(self):
        lines = cli_policy.groups_lines(self.doc(group(2, order="manual"), group(1, gid="g-0a0b0c")))
        self.assertIn("«Основная», Уровень 1: автовыбор выключен — новые устройства идут на первый рабочий выход сверху "
                      "(способ «Лучший отклик» сохранён).", lines)
        self.assertEqual(len([x for x in lines if "автовыбор" in x]), 1)
        with use_lang("en"):
            lines = cli_policy.groups_lines(self.doc(group(2, order="manual", method="even")))
        self.assertIn("“Основная”, Уровень 1: automatic choice is off — new devices go to the first working exit "
                      "from the top (“Evenly” is kept).", lines)
        lines = cli_policy.groups_lines(self.doc(group(2, order="manual", per_connection=True), group(1, gid="g-0a0b0c")))
        self.assertFalse([x for x in lines if "автовыбор" in x])
        self.assertNotIn("", lines[:3])                                       # no manual tier: the table as before


if __name__ == "__main__":
    unittest.main()

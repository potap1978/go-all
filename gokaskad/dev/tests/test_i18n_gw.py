"""3.3.2, E1: the whole gateway (lib/kaskad/gw/**) speaks through the catalogs — no Russian left in its code a person
could see (except the marked «# i18n: skip» lines: files on disk, patterns of an old install.sh), every gw* area
complete in ru and en."""

import os
import unittest

from kaskad import i18n
from kaskad.i18n import check

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GW = os.path.join(ROOT, "lib", "kaskad", "gw")


class GatewayI18nTest(unittest.TestCase):
    def test_no_cyrillic_in_gw_code(self):
        check.assert_no_cyrillic(self, GW)

    def test_gw_areas_complete(self):
        areas = sorted(n[:-5] for n in os.listdir(os.path.join(i18n.CATALOG_DIR, "ru"))
                       if n.startswith("gw") and n.endswith(".json"))
        self.assertGreaterEqual(len(areas), 30)
        check.assert_catalog_complete(self, areas)

    def test_skip_marks_say_why(self):
        bad = []
        for dirpath, _d, files in os.walk(GW):
            for fn in files:
                if fn.endswith(".py"):
                    with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                        for i, line in enumerate(f, 1):
                            if check.SKIP_MARK.search(line) and not line.split("i18n:", 1)[1].replace("skip", "", 1).strip(" —-:\n"):
                                bad.append("%s:%d" % (fn, i))
        self.assertEqual(bad, [], "«# i18n: skip» без причины")


if __name__ == "__main__":
    unittest.main()

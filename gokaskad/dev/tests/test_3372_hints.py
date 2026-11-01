"""3.3.7.2 (REV-3371 «было до правки»): подсказка «17 → 3 → 3» (добавить ключ выхода) врала при маршруте «напрямую» —
пункта «Если выходы не работают» тогда нет, и «Добавить ключ выхода» — пункт 2. Номер в справке (GUIDE_P11) теперь
берётся по маршруту — тем же правилом, что у меню и у ядра (exits.menu_add_item: панель, бот). Тексты GWX_NOEXIT и
GWX_STEP_NONET2 показываются только при маршруте «пул» (там пункт 3 — верно)."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from kaskad.gw import exits
from tests.fakes_gw import INSTALL_SH
from tests.test_3351_cmdinj import function

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def text(*name):
    with open(os.path.join(ROOT, *name), encoding="utf-8") as f:
        return f.read()


class AddItemTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="k3-3372-hint-")
        self.addCleanup(shutil.rmtree, self.d, True)

    def bash_item(self, route):
        f = os.path.join(self.d, "state.json")
        if route is None and os.path.exists(f):
            os.unlink(f)
        if route is not None:
            with open(os.path.join(self.d, "state.json"), "w") as f:
                json.dump({"gateway": {"routes": {"default": route}}}, f)
        r = subprocess.run(["bash", "-c", function("k3_gw_add_item") + "\nk3_gw_add_item"],
                           env=dict(os.environ, KASKAD_DIR=self.d), capture_output=True, text=True, timeout=30)
        return int(r.stdout.strip())

    def test_the_number_of_the_menu_by_the_route(self):
        for via in ("pool", "exit:e-1111111111", "group:g-abcdef", "direct", "block"):
            route = {"via": via, "fail": "block"}
            st = {"gateway": {"routes": {"default": route}}}
            self.assertEqual(self.bash_item(route), exits.menu_add_item(st), via)
        self.assertEqual(self.bash_item({"via": "direct"}), 2)
        self.assertEqual(self.bash_item(None), 3)                 # no gateway yet: the default route is the pool

    def test_the_menu_puts_add_after_the_failure_item_only_through_the_exits(self):
        src = function("k3_gw_exits_menu")
        self.assertIn('viaexit=""; case "$via" in pool|exit:*|group:*) viaexit=1 ;; esac', src)
        self.assertIn('acts=(route); [ -n "$viaexit" ] && acts+=(fail); acts+=(add)', src)

    def test_the_guide_takes_the_number(self):
        with open(INSTALL_SH, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('tf GUIDE_P11 "$WHITE" "$NC" "$WHITE" "$(k3_gw_add_item)" "$NC"', src)
        for lang in ("ru", "en"):
            t = text("lib", "kaskad", "i18n", "sh", "guide.%s.sh" % lang)
            line = re.search(r"^M_%s_GUIDE_P11='(.*)'$" % lang, t, re.M).group(1)
            self.assertNotIn("17 → 3 → 3", line)
            self.assertIn("%s17 → 3 → %s%s", line)
            self.assertEqual(line.count("%s"), 13, lang)
            self.assertIn("Hysteria2", line)                      # not «VLESS and AmneziaWG» only
            self.assertEqual(len(line.split("\\n")), 13, lang)       # the page fits 80×24 (19b of k3_install_test)
            for ln in line.split("\\n"):
                self.assertLessEqual(len(ln.replace("%s", "")), 78, ln)

    def test_noexit_hints_only_with_the_pool(self):
        src = function("k3_gw_noexit_note")
        self.assertIn('[ "$K3_GW_ROUTE" = pool ] || return 0', src)
        self.assertIn("3) K3_GW_ROUTE=pool; return 0 ;;", text("install.sh"))   # «Пока без интернета» — пул

    def test_readme_names_the_item(self):
        r = text("README.md")
        self.assertNotIn("**17 → 3 → 3**", r)
        # REV-3372 П-5: пункт 2 бывает и при «блок» (k3_gw_add_item, exits.menu_add_item)
        self.assertIn("«Добавить ключ выхода» (пункт 3; при маршруте «напрямую» или «блок» — 2)", r)


if __name__ == "__main__":
    unittest.main()

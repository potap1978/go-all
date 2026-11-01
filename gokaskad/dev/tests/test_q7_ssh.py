"""Q7 (SPEC-stage1-exit-groups §8.6): SSH-меню — в карточке пользователя и устройства (``k3_rules_target``) строка
просмотра «Российские сайты: через «Дом» (меняется в панели)». Управления российскими группами в SSH нет. Узел без
российских групп — экран как раньше (правило миграции 24.10). Имя группы — текст владельца: управляющие символы не
доходят до терминала, «\\» печатается текстом."""

import json
import os
import re
import shutil
import subprocess
import unittest

from tests.test_3351_cmdinj import INSTALL_SH, function

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUBS = r"""
t() { printf '%s' "$1"; }
tf() { local k=$1; shift; printf '%s' "$k:$*"; }
k3ctl() { printf '%s' "$RJS"; }
k3_rules_profile_text() { printf '%s' "P:$1"; }
k3_gw_head() { :; }
k3_gw_fold() { printf '%s\n' "$2"; }
k3_bad_choice() { :; }
C_CHOICE=""
WHITE="" NC="" YELLOW="" GREEN="" RED=""
"""


def run(doc, what="device", ref="d-00000001"):
    src = function("k3_rules_target") + STUBS + "k3_rules_target %s %s Name <<< 0\n" % (what, ref)
    r = subprocess.run(["bash", "-c", src], capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "LC_ALL": "C.UTF-8",
                            "RJS": json.dumps(doc)})
    return r.stdout + r.stderr


def status(via="g-0a0b0c", groups=True, label="Дом", user_via=None):
    return {"devices": [{"id": "d-00000001", "applied": "ru_direct", "from": "user",
                         "ru": {"via": via, "from": "device"}}],
            "users": [{"id": "u-00000001", "rules": "ru_direct", "ru": {"via": user_via or via, "from": "user"}}],
            "ru_groups": [{"id": "g-0a0b0c", "label": label}] if groups else []}


@unittest.skipUnless(shutil.which("jq"), "нет jq")
class RuLineTest(unittest.TestCase):
    def test_through_the_group(self):
        out = run(status())
        self.assertIn("GWR_T_RU:GWR_T_RU_GROUP:Дом", out)
        self.assertIn("GWR_T_1_DEVICE", out)                  # the menu itself as before

    def test_directly(self):
        self.assertIn("GWR_T_RU:GWR_T_RU_DIRECT", run(status(via="direct")))

    def test_a_user(self):
        self.assertIn("GWR_T_RU:GWR_T_RU_GROUP:Дом", run(status(user_via="g-0a0b0c"), "user", "u-00000001"))

    def test_no_russian_groups_no_line(self):
        out = run(status(via="direct", groups=False))
        self.assertNotIn("GWR_T_RU", out)
        self.assertIn("GWR_T_NOW", out)

    def test_a_label_of_the_owner_is_text(self):
        out = run(status(label="a\u001b[31mb\\nc d"))
        self.assertNotIn("\u001b", out)
        self.assertNotIn(" ", out)
        self.assertIn("a [31mb\\nc d", out)                    # «\n» of the name stays two characters

    def test_a_group_named_like_a_marker(self):
        self.assertIn("GWR_T_RU:GWR_T_RU_GROUP:d", run(status(label="d")))


class TextsTest(unittest.TestCase):
    def read(self, lang):
        with open(os.path.join(ROOT, "lib", "kaskad", "i18n", "sh", "gwrules.%s.sh" % lang), encoding="utf-8") as f:
            return f.read()

    def test_both_languages(self):
        ru, en = self.read("ru"), self.read("en")
        for key in ("GWR_T_RU", "GWR_T_RU_DIRECT", "GWR_T_RU_GROUP"):
            self.assertIn("M_ru_%s=" % key, ru)
            self.assertIn("M_en_%s=" % key, en)
        self.assertIn("меняется в панели", ru)
        en_lines = [l for l in en.splitlines() if l.startswith("M_en_GWR_T_RU")]
        self.assertEqual(len(en_lines), 3)
        self.assertFalse(any(re.search("[А-Яа-яЁё]", l) for l in en_lines))

    def test_no_management_of_russian_groups_in_ssh(self):
        with open(INSTALL_SH, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("rules ru-via", src)                  # the choice is in the panel, the CLI and MCP only

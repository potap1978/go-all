"""3.3.7.2 (REV-3371-2 З-1): шесть jq-фильтров имён для терминала, расширенные в 3.3.7.1 с C0 до C0 + C1 + U+2028/2029,
ничем не охранялись — откат к «[\\u0000-\\u001f\\u007f]» проходил незамеченным. Имя шаблона сайта приходит из внешнего
каталога (чужой ввод), организации ⚠РФ и поля здоровья — из ядра. Здесь каждое место: функция install.sh с U+009B
(CSI), U+009D (OSC), ESC, BEL и U+2028 во входе — на выходе ни одного сырого знака (пробел вместо него)."""

import json
import re
import subprocess
import unittest

from tests.fakes_gw import INSTALL_SH
from tests.test_3351_cmdinj import function

BAD = "\u009b2J\u009d52;c;SGk=\u009c\u001b[2J\u0007 X"
RAW = re.compile("[\x00-\x09\x0b-\x1e\x7f-\x9f  ]")     # \x1f — разделитель полей самих функций
FULL = "[\\u0000-\\u001f\\u007f-\\u009f\\u2028\\u2029"           # начало класса в тексте jq-программы
SITES = ("k3_site_load", "k3_site_txt", "k3_rules_menu", "k3_rules_ru", "k3_rules_env_consent", "k3_head_parse")


def bash(src, env=None):
    r = subprocess.run(["bash", "-c", src], capture_output=True, text=True, timeout=60,
                       env=dict({"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "LC_ALL": "C.UTF-8"},
                                **(env or {})))
    return r.stdout


class C1FiltersTest(unittest.TestCase):
    def test_every_filter_keeps_c1_and_line_separators(self):
        """Сторож текста: в каждой из шести функций каждый gsub имён — с C1 и U+2028/2029 (откат к C0 — красный)."""
        for name in SITES:
            src = function(name)
            classes = re.findall(r'gsub\("(\[[^"]*\])"', src)
            self.assertTrue(classes, name)
            for c in classes:
                self.assertTrue(c.startswith(FULL), (name, c))

    def test_the_class_itself_turns_c1_into_spaces(self):
        """Тот же класс в jq (как его видит jq из текста install.sh): C1, ESC, BEL, U+2028 — пробел."""
        for name in SITES:
            for c in set(re.findall(r'gsub\("(\[[^"]*\])"', function(name))):
                out = subprocess.run(["jq", "-r", '.x | gsub("%s"; " ")' % c], input=json.dumps({"x": "a" + BAD}),
                                     capture_output=True, text=True, timeout=30).stdout
                self.assertFalse(RAW.search(out), (name, c, out))
                self.assertIn("2J", out)

    def test_site_template_name_from_the_catalog(self):
        """Сайт-маскировка: имя шаблона (внешний каталог GitHub) и поля сайта — k3_site_load."""
        doc = {"domain": "example.com", "site": {"template": {"name": "Шаблон" + BAD, "id": "t1"}, "root": "/var/www"}}
        src = (function("k3_jq") + function("k3_site_load")
               + "k3_site() { K3_GW_OUT=$DOC; return 0; }\nk3_date() { printf '%s' \"$1\"; }\n"
               + 'k3_site_load; printf "[%s][%s]" "$K3_SITE_TEMPLATE" "$K3_SITE_ROOT"')
        out = bash(src, {"DOC": json.dumps(doc)})
        self.assertFalse(RAW.search(out), out)
        self.assertIn("[Шаблон 2J 52;c;SGk=", out)
        self.assertIn("[/var/www]", out)

    def test_site_txt(self):
        src = function("k3_jq") + function("k3_site_txt") + 'K3_GW_OUT=$DOC; k3_site_txt ".site.template.name"'
        out = bash(src, {"DOC": json.dumps({"site": {"template": {"name": "Т" + BAD + "\\d"}}})})
        self.assertFalse(RAW.search(out), out)
        self.assertTrue(out.startswith("Т 2J"), out)
        self.assertNotIn("\\", out)

    def test_health_fields(self):
        """Поля здоровья и шапки меню — k3_head_parse (def s)."""
        doc = {"ok": True, "gateway": {"host": "vpn" + BAD, "enabled": True}, "site": {"name": "Сайт" + BAD}}
        src = function("k3_head_parse") + 'K3_HEAD_JSON=$DOC; k3_head_parse; printf "[%s][%s]" "$K3_H_HOST" "$K3_H_DNAME"'
        out = bash(src, {"DOC": json.dumps(doc)})
        self.assertFalse(RAW.search(out), out)
        self.assertIn("[vpn 2J", out)
        self.assertIn("[Сайт 2J", out)


if __name__ == "__main__":
    unittest.main()

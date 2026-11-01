"""Форма условия группы (static/pages/exits-groups.js, ruleForm) — владелец 29.10 на 3.3.7.12: «тут поработать с блоками
и выравниванием». Эталон — экран Г «Условие фразой» (design/stage1-ui/PROMPT-stage1-ui.md §Г).

1. Полей чисел не было: порог и время не изменить. Причина — один класс на два узла: ``kk-eg-num`` — у полей чисел форм
   условия и квоты и у обёртки «отклик · устройства» строки выхода (``display: contents``, РФ-группы Ш1); у элемента
   управления ``display: contents`` — то же, что ``none``. Обёртка теперь ``kk-eg-nums``.
2. Колонка подписей: «Если», «держится», «то», «Вернуть, когда», «Для выходов» — одной колонкой, поля, проверки и
   «+ условие» — с одного x; «Только вручную» — в строке «Вернуть, когда» справа, «Сообщить в бот» — в строке «Для
   выходов» справа, у правого края формы (не обрываются на 720 px из 874).
3. Пересказ «≈ … до ухода, ≈ … до возврата» — один раз (под формой; строка условия над ней — только фраза).
4. «×» — у каждой проверки (у единственной — неактивен: ядро требует от 1 до 5).

В настоящем Chrome (tools/k3_rules_check.mjs, ?mock=1): 720 (самая узкая ширина компьютера — на телефоне «Групп» нет,
T8; форма уже 400 px — строки фразой), 768 и 1024 (форма 414 и 482 px — уже колонкой, REV-3313 З-2) и 1440, тёмная и
светлая, RU и EN. Нет Chrome или node — пропуск со словами; часть 1 проверяется и без них."""

import functools
import json
import os
import re
import subprocess
import threading
import unittest

from tests.test_332f_give import HAS_NODE
from tests.test_333q_layout import ChromeServer, _Static, chrome_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CHECK = os.path.join(ROOT, "tools", "k3_rules_check.mjs")
CSS = ["styles.css", "components.css", "ux.css"]
JS = ["pages/exits-groups.js", "app.js"]


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


class ContentsClassTest(unittest.TestCase):
    def test_no_field_gets_a_display_contents_class(self):
        """Класс с ``.X { display: contents }`` не даётся полю: поле пропадает (было — kk-eg-num, 29.10)."""
        contents = set()
        for name in CSS:
            contents |= set(re.findall(r"(?m)^\.([a-z0-9_-]+) \{ display: contents", read(name)))
        self.assertIn("kk-eg-nums", contents)
        bad = []
        for name in JS:
            src = read(name)
            for cls in re.findall(r'classList\.add\("([a-z0-9_-]+)"\)', src):
                if cls in contents:
                    bad.append((name, cls))
            for tag, cls in re.findall(r'el\("(input|select|textarea|button)", \{ class: "([^"]+)"', src):
                bad += [(name, c) for c in cls.split() if c in contents]
        self.assertEqual(bad, [])

    def test_the_exit_row_wrapper_is_its_own_class(self):
        eg, css = read("pages/exits-groups.js"), read("styles.css")
        self.assertIn('el("span", { class: "kk-eg-nums" }', eg)
        self.assertNotRegex(css, r"\.kk-eg-num \{ display: contents")
        self.assertEqual(len(re.findall(r'classList\.add\("kk-eg-num"\)', eg)), 3)      # порог, «держится», квота


@unittest.skipUnless(HAS_NODE and chrome_path(), "нет node или Chrome: форма условия в браузере не проверена (K3_CHROME=…)")
class ChromeTest(unittest.TestCase):
    rows = None

    @classmethod
    def setUpClass(cls):
        srv = ChromeServer(("127.0.0.1", 0), functools.partial(_Static, directory=STATIC))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            p = subprocess.run(["node", CHECK, "http://127.0.0.1:%d" % srv.server_address[1], chrome_path()],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=280)
        finally:
            srv.shutdown()
            srv.server_close()
        if p.returncode != 0:
            raise AssertionError("k3_rules_check: %s" % p.stderr.decode("utf-8", "replace")[-800:])
        cls.rows = json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])

    def views(self):
        for r in self.rows:
            yield (r["w"], r["theme"], r["lang"]), r

    def test_every_view_opens(self):
        self.assertEqual(len(self.rows), 16)
        self.assertEqual([k for k, r in self.views() if not r["ok"]], [])

    def test_the_numbers_are_on_screen(self):
        """№1: порог и «держится» ухода и возврата — 4 поля; и поля квоты выхода (тот же класс)."""
        for k, r in self.views():
            self.assertEqual(len(r["nums"]), 4, k)
            self.assertTrue(all(w >= 40 and h >= 24 for w, h in r["nums"]), (k, r["nums"]))
            self.assertTrue(r["qnums"] and all(w >= 40 and h >= 24 for w, h in r["qnums"]), (k, r["qnums"]))

    def test_one_column_of_labels(self):
        """№2: с окна 768 (форма от 400 px) подписи — колонкой: первое поле каждой строки, проверки и «+ условие» — с
        одного x. На 720 (форма 366 px) — строки фразой."""
        for k, r in self.views():
            if r["w"] < 768:
                continue
            xs = set(r["labels"].values()) | {c[0] for c in r["conds"]} | set(r["adds"])
            self.assertEqual(len(r["labels"]), 5, k)
            self.assertEqual(len(xs), 1, (k, r["labels"], r["conds"], r["adds"]))
            self.assertEqual(r["h"], [32], k)                                    # малые поля (--h-sm), как в макете

    def test_switches_stand_at_the_right_edge(self):
        """№2: «Только вручную» — в строке «Вернуть, когда», «Сообщить в бот» — в строке «Для выходов», у правого края."""
        for k, r in self.views():
            self.assertEqual(len(r["sw"]), 2, k)
            for name, s in r["sw"].items():
                self.assertTrue(-16 <= s["right"] <= 0, (k, name, s))
                if r["w"] >= 1000:
                    self.assertTrue(s["line"], (k, name))

    def test_the_times_are_said_once(self):
        """№3: «≈ … до ухода» — один раз в карточке группы."""
        for k, r in self.views():
            self.assertEqual(r["times"], 1, k)

    def test_every_check_has_its_cross(self):
        """№4: «×» у каждой проверки."""
        for k, r in self.views():
            self.assertTrue(r["conds"] and all(c[1] for c in r["conds"]), (k, r["conds"]))

    def test_nothing_sticks_out(self):
        for k, r in self.views():
            self.assertEqual(r["over"], [], k)

    def test_after_edits(self):
        """Как на снимке владельца — «потери» (единица подписью) и «выбрать…» выходы; затем «только уведомить»: колонка
        та же, «×» на месте, ничего не торчит, сроки — один раз. Порог 30 — фраза над формой меняется сразу, фокус в поле."""
        for k, r in self.views():
            v = r["vars"]
            for name in ("lossPick", "push"):
                p = v.get(name)
                self.assertTrue(p, (k, name))
                self.assertEqual(p["over"], [], (k, name))
                self.assertEqual(p["times"], 1, (k, name))
                self.assertTrue(all(c[1] for c in p["conds"]), (k, name))
                if r["w"] >= 768:
                    xs = set(p["labels"].values()) | {c[0] for c in p["conds"]} | set(p["adds"])
                    self.assertEqual(len(xs), 1, (k, name, p["labels"]))
            self.assertEqual(len(v["push"]["sw"]), 1, k)                        # без возврата — только «Сообщить в бот»
            self.assertEqual(v["live"][0], True, k)
            self.assertIn("30", v["live"][1], k)


if __name__ == "__main__":
    unittest.main()

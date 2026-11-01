"""Круг 1 правок по видео владельца (27.10), шаг Т2 — перенос макета Claude Design it1 Д3 «Выходы и Настройки» в панель.

Поведение — в настоящем Chrome на заглушке панели (tools/k3_t2_check.mjs; нет node или Chrome — пропуск со словами),
правила текстов и общего узора — без браузера:

* №25 (решение 12): сворачиваемые столбцы — общий узор xt.js; в «Все выходы» сворачиваются «Тип», «Отклик», «Группа»,
  «Активность», «Выход» и «Состояние» — нет; свёрнутый — полоска 24 px со значком; значение — в подсказке ячейки и в
  раскрытии строки; выбор помнится в браузере; у плотности своя сетка.
* №28–31 и решения 10, 11, 13: «Группы» без российских групп (строка-ссылка), «Удалить группу…» в «⋯» шапки, сценарии,
  подсказка способа выбора, пояснение «Разложить по протоколу»; «Российские группы» — схема на всю ширину, ключи
  таблицей, редактор здесь же с набором «Дом».
* Решение 8а: последняя российская группа удаляется вместе с ключами одним действием, окно перечисляет ключи."""

import functools
import json
import os
import subprocess
import threading
import unittest

from tests.test_332f_give import HAS_NODE, read
from tests.test_333q_layout import ChromeServer, _Static, chrome_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CHECK = os.path.join(ROOT, "tools", "k3_t2_check.mjs")


def i18n(name):
    """Словарь панели {ключ: текст} из i18n-*.js (строки вида  "ключ": "текст",)."""
    out = {}
    for line in read(name).splitlines():
        s = line.strip()
        if s.startswith('"') and '": ' in s:
            k, v = s.split('": ', 1)
            try:
                out[k[1:]] = json.loads(v.rstrip(","))
            except ValueError:
                pass
    return out


class CollapseStaticTest(unittest.TestCase):
    """№25 без браузера: узор — в xt.js (без готовых фраз), тексты — в словаре панели RU и EN."""

    def test_block_stays_portable(self):
        xt = read("xt.js")
        for name in ("ccGet", "ccSet", "ccHead", "ccToggle", "ccApply", "ccFit", "tracks"):
            self.assertIn(name + ": function", xt)
        self.assertFalse('"Свернуть' in xt or "'Свернуть" in xt)        # тексты приходят параметрами
        self.assertIn("lsSet(key, v)", xt)                             # хранилище — через try/catch (lsSet)

    def test_texts_ru_en(self):
        ru, en = i18n("i18n-ru.js"), i18n("i18n-en.js")
        for k in ("xt.colCollapse", "xt.colExpand", "xt.colTipC", "xt.colTipE", "ex.xt.cActDay"):
            self.assertIn(k, ru)
            self.assertIn(k, en)
        self.assertEqual(ru["xt.colTipE"], "«{l}» свёрнут — нажмите, чтобы развернуть")

    def test_exits_marks_columns(self):
        app = read("app.js")
        self.assertIn('const EX_CC = "kaskad-cols-exits";', app)
        for k in ('k: "type", cc: true', 'k: "rtt", cc: true', 'k: "grp", cc: true', 'k: "act", cc: true'):
            self.assertIn(k, app)


@unittest.skipUnless(HAS_NODE and chrome_path(), "нет node или Chrome: шаг Т2 в браузере не проверен (K3_CHROME=…)")
class ChromeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        srv = ChromeServer(("127.0.0.1", 0), functools.partial(_Static, directory=STATIC))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = subprocess.run(["node", CHECK, "http://127.0.0.1:%d" % srv.server_address[1], chrome_path()],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=400)
        finally:
            srv.shutdown()
            srv.server_close()
        if r.returncode:
            raise AssertionError(r.stderr.decode("utf-8", "replace")[-3000:])
        cls.r = json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])

    def test_cc_which_columns(self):
        self.assertEqual(self.r["cc"]["heads"], ["", "Выход", "Состояние", "B:type", "B:rtt", "B:grp", "B:act", ""])

    def test_cc_collapse_and_back(self):
        c = self.r["cc"]
        s0, s1 = c["s0"], c["s1"]
        self.assertEqual((s0["cc"], s0["exp"], s0["aria"]), (False, "true", "Свернуть столбец «Тип»"))
        self.assertEqual((s1["cc"], s1["exp"], s1["aria"]), (True, "false", "Развернуть столбец «Тип»"))
        self.assertEqual((s1["w"], s1["cw"], s1["cellCc"], s1["kidsShown"]), (24, 24, True, False))
        self.assertGreater(s1["nameW"], s0["nameW"] + 60)              # освободившееся — имени
        self.assertTrue(s1["title"].startswith("Тип: "))
        self.assertEqual(s1["store"], '["type"]')
        self.assertTrue(c["subRow"]["type"].startswith("Тип"))         # в раскрытии строки — значение свёрнутого
        self.assertTrue(c["subRow"]["grpHidden"])                       # несвёрнутого там нет
        self.assertEqual((c["back"]["cc"], c["back"]["w"], c["back"]["store"], c["backCols"]), (False, s0["w"], "[]", ""))

    def test_cc_memory_density_keyboard(self):
        c = self.r["cc"]
        self.assertEqual((c["reload"]["cc"], c["reload"]["w"]), (True, 24))
        self.assertEqual((c["lg"]["cc"], c["lg"]["w"], c["lg"]["cw"]), (True, 24, 24))   # у «Крупно» своя сетка
        self.assertEqual((c["kbd"]["cc"], c["kbd"]["store"]), (True, '["type","grp"]'))
        self.assertEqual(c["tipFocus"], "«Группа» свёрнут — нажмите, чтобы развернуть")


    def test_groups_menu_and_no_bottom_delete(self):
        g = self.r["grp"]
        self.assertEqual(g["bottomDel"], 0)                                 # №29: внизу карточки удаления нет
        self.assertEqual(g["mainItems"], ["Переименовать"])                 # «Основную» не удалить
        self.assertEqual(g["streamItems"], ["Переименовать", "Удалить группу…"])
        self.assertEqual(g["menus"][:2], [True, True])

    def test_groups_without_russian(self):
        g = self.r["grp"]
        self.assertNotIn("Дом", g["names"])                                 # решение 10: российская — только в своей части
        self.assertNotIn("Офис", g["names"])
        self.assertIn("Внутренние группы", g["ruLink"])
        self.assertEqual(g["ruHash"], "#exits?tab=ru")

    def test_groups_texts(self):
        g = self.r["grp"]
        self.assertEqual((g["scn"]["t"], g["scn"]["rows"]), ("Что будет при «Как раньше»", 0))   # не «Стандарт» — одной строкой
        self.assertEqual((g["pop"]["open"], g["pop"]["exp"], g["pop"]["closed"]), (True, "false", True))   # нажатие открыло, Esc закрыл
        self.assertTrue(g["bp"].startswith("AmneziaWG и WireGuard — на уровень 1"))

    def test_russian_part(self):
        v = self.r["ru"]
        self.assertEqual(v["order"], ["btns", "scheme", "card", "card"])    # кнопки, схема, карточки
        self.assertEqual(v["schemeW"], v["cardW"])                          # на всю ширину части
        self.assertEqual(v["keys"], ["Роутер", "Дача"])                      # общая карточка групп: ключи уровнями (РФ-группы Ш1)
        self.assertTrue(v["ed"] and v["ruCls"])                             # «Если все выходы недоступны» — выбором вверху
        self.assertFalse(v["compat"] or v["edHead"])                        # без «Как раньше» и без второй шапки
        self.assertEqual([x[:3] for x in v["presets"]], ["Дом", "Сво"])     # «Свой» — только отметка после правок (неактивна)

    def test_last_russian_group_drop(self):
        d = self.r["drop"]
        self.assertEqual(d["keys"], ["Ключ «Роутер»", "Ключ «Дача»"])
        self.assertIn("с сервера напрямую", d["al"])
        self.assertEqual((d["btn"], d["dis"]), ("Удалить группу и ключи", False))
        self.assertEqual((d["groupsRu"], d["left"], d["empty"]), (0, 0, True))
        self.assertIn("и её ключи удалены", d["toast"])


if __name__ == "__main__":
    unittest.main()

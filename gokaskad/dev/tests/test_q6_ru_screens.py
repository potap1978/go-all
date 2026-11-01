"""T15 Q6: экраны российских групп в панели (Claude Design «RU Groups Q6 r1», экраны 1–4).

Статика: часть «Российские группы» в подменю «Выходов» (после «Группы», «Прокси API Telegram» не переносится — решение
координатора 24.10), живые поля live.groups / live.policy / no_exit_ru доходят до частей, PUT /api/rules/ru-via есть в
API панели. Поведение — в настоящем Chrome на заглушке (tools/k3_rug_check.mjs, ?mock=1&rug=…): карточки и «сейчас: …»,
пустые состояния (узел без российских групп — как 3.3.7.9 после обновления), «Все выходы» без российских, «Назначение»
окна ключа, перенос между ролями с confirm_role, строка «Российские сайты» и окно «Выбрать пользователей…».
"""
import functools
import json
import os
import re
import subprocess
import threading
import unittest

from tests.test_332f_give import HAS_NODE, read
from tests.test_333q_layout import ChromeServer, _Static, chrome_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CHECK = os.path.join(ROOT, "tools", "k3_rug_check.mjs")


class StaticTest(unittest.TestCase):
    def test_part_in_subnav_after_groups(self):
        js = read("app.js")
        n2 = js[js.index("  const N2 = {"):js.index("  const N2_ARG")]
        ex = n2[n2.index("exits: ["):n2.index("rules: [")]
        self.assertLess(ex.index('["groups", "ex.tabGroups"'), ex.index('["ru", "n2.exRu", "router"]'))
        self.assertNotIn('"n2.exTg"', ex)                                    # прокси API Telegram — в «Настройках»
        self.assertNotIn('["ru", "n2.exRu", "layers", REL10]', ex)           # больше не «Скоро»
        self.assertIn('{ id: "ru", label: "n2.exRu" }', js)                  # #exits?tab=ru — известная часть

    def test_live_fields_reach_parts(self):
        js = read("app.js")
        body = js[js.index("  function normLive("):js.index("  function mergePatch(")]
        for k in ("groups", "no_exit_ru", "policy"):
            self.assertIn("if (isObj(src.%s)) out.%s = src.%s;" % (k, k, k), body)

    def test_api_and_exports(self):
        js = read("app.js")
        self.assertIn('ruVia: (b) => request("PUT", "/api/rules/ru-via", b)', js)
        self.assertIn("addKey: (o) => addExitDialog(o || {}), ruPick: (gid) => ruPickDialog(gid)", js)
        # окно ключа: группа «Назначения» уходит в теле POST /api/gw/exits
        self.assertIn("API.exits.add(group ? { key, group } : { key })", js)

    def test_cross_role_move_sends_confirm_role(self):
        eg = read("pages/exits-groups.js")
        mv = eg[eg.index("  function moveDialog(id)"):eg.index("  // ── карточка «Тест скорости")]
        self.assertIn("toRole() !== mine ? { group: st.to, confirm_role: true } : { group: st.to }", mv)
        self.assertIn("K.API.exits.move(id, body(), true)", mv)                # прогон тоже с confirm_role (иначе 409)


@unittest.skipUnless(HAS_NODE and chrome_path(), "нет node или Chrome: «Российские группы» не проверены (K3_CHROME=…)")
class ChromeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        srv = ChromeServer(("127.0.0.1", 0), functools.partial(_Static, directory=STATIC))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            r = subprocess.run(["node", CHECK, "http://127.0.0.1:%d" % srv.server_address[1], chrome_path()],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        finally:
            srv.shutdown()
            srv.server_close()
        if r.returncode:
            raise AssertionError(r.stderr.decode("utf-8", "replace")[-3000:])
        cls.r = json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])

    def test_last_step(self):
        v = self.r["last"]
        self.assertEqual(v["title"], "Внутренние группы")
        ids = v["sub"]["ids"]
        self.assertEqual(ids[ids.index("groups") + 1], "ru")
        self.assertEqual((v["sub"]["n"], v["sub"]["warn"], v["sub"]["tg"], v["sub"]["cur"]), ("2", True, False, "page"))
        home, office = v["cards"]
        self.assertEqual((home["name"], office["name"]), ("Дом", "Офис"))
        self.assertRegex(home["now"], r"^сейчас: с сервера напрямую — выходы недоступны с (\d\d\.\d\d, )?\d\d:\d\d$")   # после полуночи — с датой
        self.assertEqual(home["tone"], "warning")
        self.assertEqual(home["down"], ["Роутер", "Дача"])                 # общая карточка групп: красное «не работает»
        self.assertEqual((office["now"], office["tone"], office["down"]), ("сейчас: через «Офисный роутер»", "success", []))
        self.assertEqual(v["rows"], ["Роутер", "Дача", "Офисный роутер"])
        self.assertEqual((v["exp0"], v["exp1"], v["focus"]), ("false", "true", True))
        # «Если все выходы недоступны» — строкой выбора вверху общей карточки (failRow), в фактах не повторяется
        self.assertEqual(v["facts"], ["Кто использует", "Проверки", "Имена (DNS)", "Страна"])

    def test_ok_and_waiting(self):
        ok = self.r["ok"]
        self.assertFalse(ok["sub"]["warn"])
        self.assertEqual((ok["cards"][0]["now"], ok["cards"][0]["tone"]), ("сейчас: через «Роутер»", "success"))
        self.assertEqual(self.r["rudown"]["cards"][0]["now"], "сейчас: сервер не достаёт до российских адресов — ждём связи")

    def test_node_without_groups(self):
        """Узел после обновления с 3.3.7.9: групп нет — пустое состояние, «Все выходы» как раньше, в правилах — у всех
        напрямую с сервера."""
        v = self.r["none"]
        self.assertTrue(v["empty"])
        self.assertEqual(v["text"], "Внутренних групп пока нет")
        self.assertEqual(v["btns"], ["Добавить внутренний ключ", "Группа"])
        self.assertIsNone(v["sub"]["n"])
        self.assertFalse(v["link"])
        self.assertEqual(v["rules"], {"main": "Напрямую с сервера — у всех.", "btn": "Открыть «Внутренние группы»"})

    def test_group_without_keys(self):
        v = self.r["emptyg"]
        self.assertEqual(v["cards"][0]["now"], "сейчас: с сервера напрямую")
        self.assertIn("В группе нет ключей", v["cards"][0]["empty"])
        self.assertEqual(v["btn"], "Добавить ключ в «Дом»")

    def test_all_exits_without_russian(self):
        v = self.r["all"]
        self.assertTrue(v["names"])
        for n in ("Роутер", "Дача", "Офисный роутер"):
            self.assertFalse(any(n in x for x in v["names"]), n)
        self.assertEqual(v["link"], "Внутренние выходы: 3 — во«Внутренних группах»")
        self.assertEqual(v["href"], "#exits?tab=ru")

    def test_key_into_new_russian_group(self):
        v = self.r["key"]
        self.assertEqual(v["radios"], [["abroad", "false"], ["ru", "true"]])
        self.assertEqual(v["picks"][1], "Новая «Дом»")
        self.assertFalse(v["open"])                                          # добавлен — окно закрылось
        self.assertEqual(v["groups"], ["Дом"])
        self.assertEqual((v["ruExits"], v["placed"]), (1, 1))

    def test_key_window_with_group(self):
        v = self.r["keyg"]
        self.assertEqual(v["radios"], [["abroad", "false"], ["ru", "true"]])
        self.assertEqual(v["picks"], ["Основная", "Офис"])
        self.assertEqual(v["after"], [["abroad", "true"], ["ru", "false"]])
        # подзаголовок окна — по «Назначению»: российский ключ — не «VPN за рубежом»
        self.assertTrue(v["sub0"].startswith("Ваш роутер или сервер в России"), v["sub0"])
        self.assertTrue(v["sub1"].startswith("Ваш VPN за рубежом"), v["sub1"])

    def test_country_changed_russian_exit(self):
        v = self.r["country"]
        self.assertNotIn("err", v)
        self.assertIn("RU", v["text"])
        self.assertIn("FI", v["text"])
        self.assertIn("не снимается", v["text"])
        self.assertIn("Добавить условие «Страна не та»", v["btns"])

    def test_move_between_roles(self):
        v = self.r["move"]
        self.assertEqual(v["heads"], ["Внутренние", "Зарубежные"])
        self.assertEqual(v["hereDis"], "true")                               # своя группа — «сейчас здесь»
        self.assertIn("Роль выхода сменится: внутренний → зарубежный.", v["warn"])
        self.assertEqual(v["err"], "")                                       # прогон с confirm_role — без 409
        self.assertTrue(v["yes"])
        self.assertEqual((v["role"], v["inStream"]), ("abroad", True))

    def test_rules_row(self):
        v = self.r["rules"]
        self.assertTrue(v["main"].startswith("Через внутреннюю группу:"))
        self.assertEqual(v["chips"], ["«Дом» — 2 пользователя", "«Офис» — 1 устройство"])
        self.assertEqual(v["btn"], "Выбрать пользователей…")

    def test_rules_row_groups_not_chosen(self):
        v = self.r["free"]
        self.assertTrue(v["main"].startswith("Напрямую с сервера"), v["main"])
        self.assertIn("«Дом»", v["sub"])
        self.assertIn("«Офис»", v["sub"])
        self.assertEqual(v["btn"], "Выбрать пользователей…")

    def test_pick_users_one_save(self):
        v = self.r["pick"]
        self.assertEqual(v["tabs"], [["Дом", "true"], ["Офис", "false"]])
        self.assertEqual(v["st0"], ["true", "true", "false"])
        self.assertTrue(v["save0"])                                          # без правок «Сохранить» недоступна
        self.assertEqual(sorted(v["chips"]), ["Гости", "Мама"])
        self.assertIn("Изменится:", v["sum"])
        self.assertIn("Мама — «Дом» → напрямую", v["sum"])
        self.assertIn("Гости — напрямую → «Дом»", v["sum"])
        self.assertFalse(v["save1"])
        self.assertTrue(v["closed"])
        self.assertEqual((v["mama"], v["guests"], v["papa"]), ("inherit", "g-d0d0d0", "g-d0d0d0"))

    def test_pick_changed_elsewhere(self):
        """REV-s1fin П-3: макет сверяет ver строки, как сервер — 409 и 400 видны без живого узла."""
        v = self.r["pick409"]
        self.assertIn("успели изменить в другом окне", v["toast"])
        self.assertEqual((v["again"], v["st1"]), (True, "false"))            # окно заново с текущим выбором
        self.assertEqual((v["guests"], v["papa"]), ("direct", "g-d0d0d0"))   # ничего не записано
        self.assertEqual(v["old"], [400, {"code": "ver_required"}])          # вкладка до обновления

    def test_widths(self):
        """768: факты — подпись над значением (значение не столбиком в слово), «сейчас» — под именем; 1440 — «сейчас»
        в строке имени, как на кадре 1.1; в шапке уровня нет пустой ячейки (лишний зазор)."""
        g = self.r["geo"]
        self.assertGreaterEqual(g["768"]["kv"], 200)
        self.assertFalse(g["768"]["nowBeside"])
        self.assertTrue(g["1440"]["nowBeside"])
        self.assertGreaterEqual(g["1440"]["kv"], 300)
        self.assertEqual((g["768"]["empty"], g["1440"]["empty"]), (0, 0))

    def test_english(self):
        v = self.r["en"]
        self.assertEqual(v["part"], "Domestic groups")
        self.assertTrue(v["lead"].startswith("Why: Russian sites see a Russian address"))   # схема «Зачем» вместо вводной (№31а)
        self.assertEqual(v["rules"], "Russian sites | Choose users…")
        self.assertFalse(re.search("[А-Яа-яЁё]", v["lead"] + v["rules"]))


if __name__ == "__main__":
    unittest.main()

"""PLAN-order Ш4–Ш5 в настоящем Chrome на заглушке панели (tools/k3_order_check.mjs, ?mock=1&ord=…|rug=ok): строки
уровня по рядам сервера, перетаскивание мышью и пальцем (768; на 390 — внутренняя группа: «Группы» на телефоне не
показываются), клавиатура и отмена Esc, «по соединениям» — ручки неактивны, во внутренней группе ↑ меняет шаги местами.
REV-order: бросок вне уровней — «сюда нельзя» и порядок прежний (С-1); бросок на ключ другого шага — «в один шаг с …» и
строка в «Что изменится» (С-2); ручной уровень с нерабочими после «Сохранить» — очередь без «Не участвуют» и ложного
«было» (С-3); aria-pressed у поднятой ручки (П-2). Модель хода и тексты — tests/test_panel_order.py (node)."""

import functools
import json
import os
import subprocess
import threading
import unittest

from tests.test_332f_give import HAS_NODE
from tests.test_333q_layout import ChromeServer, _Static, chrome_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CHECK = os.path.join(ROOT, "tools", "k3_order_check.mjs")
SERVER_ORDER = ["Ключ 1", "Ключ 3", "Ключ 4", "Ключ 2"]


@unittest.skipUnless(HAS_NODE and chrome_path(), "нет node или Chrome: порядок в группах не проверен (K3_CHROME=…)")
class ChromeOrderTest(unittest.TestCase):
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

    def test_no_page_errors(self):
        self.assertEqual(self.r["errors"], [])

    def test_rows_follow_the_server(self):
        t = self.r["rows"]
        self.assertEqual(t["rows"], SERVER_ORDER)
        self.assertEqual(t["tags"], ["Р1", "Р1", "Р1", "Р2"])
        self.assertEqual(t["ms"], ["11 мс", "12 мс", "12 мс", "95 мс"])
        self.assertEqual(t["grips"], ["on"] * 4)
        self.assertEqual(t["arrows"], 8)                                   # ↑ и ↓ у каждой строки
        self.assertEqual(len(t["arrowsOn"]), 7)                            # «Выше» у первой — некуда
        self.assertNotIn("Выше: Ключ 1", t["arrowsOn"])
        self.assertEqual(t["method"], "best_rtt")
        # Ш6 (макет П1): причина у каждого ряда, полоска — лучший / свой (11 / 11, 11 / 12, 11 / 12, 11 / 95)
        self.assertEqual(t["why"], ["равны: до +30 мс от лучшего", "очередь: если Р1 выпадет"])
        self.assertEqual(t["bars"], [100, 92, 92, 12])
        self.assertEqual(t["note"], "")
        self.assertEqual(t["man"], "")

    def test_mouse_drag_makes_the_order_manual_and_saves_it(self):
        m = self.r["mouse"]
        self.assertTrue(m["line"] and m["line"]["shown"] and m["line"]["drag"], m["line"])
        self.assertEqual(m["tier"]["rows"], ["Ключ 2", "Ключ 1", "Ключ 3", "Ключ 4"])
        self.assertEqual(m["tier"]["method"], "manual")
        self.assertIn("Автовыбор выключен", m["tier"]["man"])
        self.assertIn("Вернуть автовыбор", m["tier"]["man"])
        self.assertEqual(m["tier"]["tags"], ["1", "2", "3", "4"])           # строгая очередь: каждый — свой ряд
        self.assertEqual(m["tier"]["dirty"], 4)                             # макет П3: «было: Р2 / Р1» у всех четырёх
        self.assertEqual(m["tier"]["why"], [])                              # в ручном рядов нет — номера
        self.assertTrue(m["dirty"])
        s = m["saved"]
        self.assertTrue(s["clicked"])
        self.assertEqual((s["order"], s["method"]), ("manual", "best_rtt"))  # способ сохранён рядом с флагом
        self.assertEqual(s["exits"], ["e-0d0d0d0d02", "e-0d0d0d0d01", "e-0d0d0d0d03", "e-0d0d0d0d04"])

    def test_drop_into_an_auto_tier_frames_the_tier(self):
        z = self.r["zone"]
        self.assertTrue(z["line"]["shown"] and z["line"]["zone"], z["line"])  # линии нет — подпись у уровня
        self.assertIn("встанет по силе", z["line"]["text"])
        self.assertEqual(len(z["line"]["drop"]), 1)                          # рамка — у одного уровня, куда встанет
        self.assertTrue(z["line"]["lifted"])
        self.assertIn("Ключ 2", z["tiers"][1]["rows"])
        self.assertNotIn("Ключ 2", z["tiers"][0]["rows"])
        self.assertEqual(z["tiers"][0]["method"], "best_rtt")                # перенос в другой уровень ручной порядок не включает
        self.assertEqual(z["dropLeft"], 0)                                   # отпустили — рамки нет

    def test_rows_move_smoothly_unless_reduced_motion(self):
        f = self.r["flip"]
        self.assertGreater(f["anim"], 0)
        self.assertEqual(f["rows"][:2], ["Ключ 3", "Ключ 1"])
        self.assertEqual(f["reduced"], 0)

    def test_keyboard_lift_move_cancel_and_drop(self):
        k = self.r["key"]
        self.assertTrue(k["lifted"])
        self.assertIn("Ключ 3", k["live"])
        self.assertEqual(k["cancel"], SERVER_ORDER)                         # Esc — как было
        self.assertEqual(k["drop"]["rows"][0], "Ключ 3")
        self.assertEqual(k["drop"]["method"], "manual")
        self.assertTrue(k["focus"])                                         # фокус остался на ручке
        # REV-order П-2: поднята — aria-pressed «true» (и после хода: ручку перерисовали), положили или Esc — «false»
        self.assertEqual((k["pressed"], k["pressedEsc"], k["pressedDrop"]), ("true", "false", "false"))

    def test_touch_at_768(self):
        t = self.r["touch"]
        self.assertEqual(t["tier"]["rows"][0], "Ключ 4")
        self.assertEqual(t["tier"]["method"], "manual")
        self.assertTrue(t["scrolled"])                                      # вне ручки — прокрутка страницы
        self.assertEqual(t["after"], t["tier"]["rows"])

    def test_touch_at_390_domestic_group(self):
        t = self.r["touch390"]                                              # «Группы» на телефоне не показываются
        self.assertEqual(t["before"][:2], [["Роутер"], ["Дача"]])
        self.assertEqual(t["after"][:2], [["Дача"], ["Роутер"]])           # шаги местами, а не два ключа в одном
        self.assertTrue(t["scrolled"])

    def test_per_connection_grips_are_off(self):
        c = self.r["conn"]
        self.assertEqual(c["tier"]["grips"], ["off"] * 4)
        self.assertEqual(c["tier"]["arrowsOn"], ["Ниже: Ключ 4"])          # только в соседний уровень
        self.assertNotIn("manual", c["tier"]["method"])
        self.assertEqual(c["after"], c["tier"]["rows"])
        self.assertEqual(c["tier"]["man"], "")

    def test_drop_outside_the_tiers_is_refused(self):
        """С-1: под «+ Уровень», выше первого уровня, правее блока (мышь) и под «+ Уровень» пальцем на 768 — строка и
        подпись «сюда нельзя»; отпустили — порядок прежний (до правки «Ключ 2» уезжал в конец «Запасного»)."""
        o = self.r["outside"]
        for k in ("below", "above", "right", "touch"):
            line = o[k]["line"]
            self.assertTrue(line["shown"] and line["no"] and line["rowNo"], (k, line))
            self.assertEqual((line["text"], line["zone"], line["drop"]), ("Сюда нельзя", False, 0), k)
        self.assertEqual(o["below"]["tiers"], [{"rows": SERVER_ORDER, "method": "best_rtt"}, {"rows": ["Вена", "Хельсинки", "Токио"], "method": "even"}])
        self.assertEqual(o["below"]["dirty"], "")
        self.assertEqual(o["below"]["said"], "Сюда нельзя")                  # диктору — тоже
        for k in ("above", "right", "touch"):
            self.assertEqual(o[k]["tiers"][0], SERVER_ORDER, k)
            self.assertEqual(o[k]["tiers"][1], ["Вена", "Хельсинки", "Токио"], k)

    def test_domestic_drop_on_a_key_is_signed(self):
        """С-2 (макет В2): «Дача» на строку «Роутер» — подпись «в один шаг с «Роутер»», шаг подсвечен (is-target), без
        «встанет по силе»; после — один шаг из двух ключей и строка о слиянии в «Что изменится». RU и EN."""
        for lang, text, join in (("ru", "в один шаг с «Роутер»", "«Дом»: «Дача» теперь в одном шаге с «Роутер»"),
                                 ("en", "one step with “Router”", "“Home”: “Country house” is now in one step with “Router”")):
            m = self.r["merge"][lang]
            self.assertTrue(m["line"]["target"] and not m["line"]["no"], (lang, m["line"]))
            self.assertEqual((m["line"]["text"], m["line"]["tgt"], m["line"]["drop"]), (text, ["t1"], 0), lang)
            self.assertEqual(len(m["before"]), 2, lang)
            self.assertEqual([len(x) for x in m["after"]], [2], lang)
            self.assertEqual(m["tgtLeft"], 0, lang)
            self.assertIn(join, m["impact"], lang)

    def test_manual_tier_with_exits_out_after_save(self):
        """С-3: «Запасной» по порядку (вручную), ↓ у «Вены», «Сохранить»: номера 1–3, причина — фишкой, ни «Не
        участвуют» посреди очереди, ни «было», ни подложки правки при пустой полосе сохранения."""
        mo = self.r["manualOut"]
        self.assertEqual(mo["saved"], {"order": "manual", "n": 3})
        self.assertTrue(mo["fresh"])                                       # пришли ряды «вручную»
        a = mo["after"]
        self.assertEqual(a["rows"], ["Хельсинки", "Вена", "Токио"])
        self.assertEqual(a["tags"], ["1", "2", "3"])
        self.assertEqual((a["outh"], a["was"], a["dirty"], a["chips"]), (0, [], 0, 2))
        self.assertEqual(mo["dirtyBar"], "")

    def test_domestic_group_steps_swap(self):
        st = self.r["steps"]
        self.assertEqual(st["before"][:2], [["Роутер"], ["Дача"]])
        self.assertEqual(st["after"][:2], [["Дача"], ["Роутер"]])

    def test_domestic_move_inside_a_step_says_what_it_was(self):
        """Н-2 стенда 3.3.7.13: во внутренней группе ход внутри шага с несколькими ключами (ряды Р1/Р2) — как в обычной
        группе: шаг «По порядку (вручную)», номера мест, у ключей «было: Р1» и подложка правки (макет П3)."""
        sw = self.r["stepWas"]
        self.assertEqual((sw["before"]["rows"], sw["before"]["tags"], sw["before"]["was"]), (["Роутер", "Дача"], ["Р1", "Р1"], []))
        self.assertTrue(sw["clicked"])
        a = sw["after"]
        self.assertEqual((a["rows"], a["tags"], a["was"], a["dirty"]), (["Дача", "Роутер"], ["1", "2"], ["было: Р1", "было: Р1"], 2))

    def test_phone_save_bar_lines_above_the_buttons(self):
        """М-2 стенда 3.3.7.13: на 390 полоса правок — «Изменений: 1 · Что изменится» строкой, под ней что изменится,
        кнопки последними и поровну; между счётчиком и кнопками нет пустой полосы (было ≈ 28 px)."""
        b = self.r["bar390"]
        self.assertTrue(b["clicked"])
        self.assertEqual(len(b["lines"]), 2, b)
        for k in ("closed", "open"):
            x = b[k]
            self.assertFalse(x["over"], k)
            self.assertLess(x["wTop"], x["nBot"], k)                         # счётчик и «Что изменится» — одна строка
            self.assertAlmostEqual(x["cTop"], x["sTop"], delta=1, msg=k)      # кнопки — одна строка
            self.assertAlmostEqual(x["cW"], x["sW"], delta=2, msg=k)          # поровну
        c, o = b["closed"], b["open"]
        self.assertLessEqual(c["cTop"] - max(c["nBot"], c["wBot"]), 12)       # без пустой полосы
        self.assertIsNotNone(o["lTop"])
        self.assertGreaterEqual(o["lTop"], max(o["nBot"], o["wBot"]) - 1)     # что изменится — под счётчиком
        self.assertLessEqual(o["lBot"], o["cTop"])                            # и над кнопками
        self.assertLessEqual(o["cTop"] - o["lBot"], 12)


if __name__ == "__main__":
    unittest.main()

"""UI-5 (шаблон T13 «Страница с подменю», Claude Design 20.10): «Настройки» с подменю второго уровня.

Компьютер и планшет — подменю слева (группы Панель · Сервер · Связь · Данные), одна часть на всю оставшуюся ширину;
телефон — список частей с подписями групп. Адрес части — #settings?tab=<часть> (синоним #settings/<часть>), без части и с
неизвестной частью — «Вид»; переход по подменю — новая запись истории, «Назад» и «Вперёд» работают; правки в части —
окно ухода; каждая запись поиска ведёт в свою часть и к своему полю.

Без Chrome — только проверки исходника; поведение — в настоящем Chrome (tools/k3_settings_nav.mjs, ?mock=1)."""
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
CHECK = os.path.join(ROOT, "tools", "k3_settings_nav.mjs")
APP = read("app.js")
# UI-A1 (эталон 23.10): в «Сервере» — ещё «Время», «Хранение данных», «Проверка сервера»
GROUPS = [("Панель", ["look", "access", "about"]), ("Сервер", ["gateway", "addresses", "site", "time", "storage", "health"]), ("Связь", ["bot", "notify"]),
          ("Данные", ["backup", "github"])]
PARTS = [p for _, ids in GROUPS for p in ids]


def settings_items():
    """Записи описи поиска «Настроек»: [id, at, need]."""
    a = APP.index('{ id: "settings", label: "nav.settings"')
    b = APP.index("// «Ещё» — страница телефона", a)
    out = []
    for m in re.finditer(r'\{ id: "(settings\.\w+)".*?(?:need: "(\w+)", )?at: \{([^}]*)\}', APP[a:b]):
        out.append([m.group(1), dict(re.findall(r'(\w+): "([^"]+)"', m.group(3))), m.group(2) or ""])
    return out


class SourceTest(unittest.TestCase):
    def test_groups_cover_every_part_once(self):
        m = re.search(r"const ST_GROUPS = \[(.*?)\];\n", APP, re.S)
        got = re.findall(r'\["(\w+)", "([\w.]+)", \[([^\]]*)\]\]', m.group(1))
        ids = [x for _, _, l in got for x in re.findall(r'"(\w+)"', l)]
        self.assertEqual(ids, PARTS)
        parts = re.search(r"const ST_PARTS = \{(.*?)\n  \};", APP, re.S).group(1)
        self.assertEqual(sorted(re.findall(r"^    (\w+)(?:\(\)|:)", parts, re.M)), sorted(PARTS))
        for f in ("i18n-ru.js", "i18n-en.js"):
            src = read(f)
            for _, key, _ in got:
                self.assertIn('"%s":' % key, src, f)
            for key in ("st.accessShort", "st.ghShort", "st.subnavAria", "st.attDirty", "st.attDirtyT", "st.attGwOff", "st.attGwOffT",
                        "st.time", "st.storage", "st.health", "st.attCert", "st.attCertT", "st.attBot", "st.attBotT"):
                self.assertIn('"%s":' % key, src, f)

    def test_every_search_record_has_its_part(self):
        items = settings_items()
        self.assertGreaterEqual(len(items), 30)
        for id_, at, _ in items:
            self.assertIn(at.get("tab"), PARTS, "запись %s без части: %s" % (id_, at))

    def test_one_breakpoint_and_tokens(self):
        css, tok = read("styles.css"), read("tokens.css")
        for t in ("--side-w: 248px;", "--subnav-w: 184px;", "--subnav-w-md: 164px;", "--subnav-w-sm: 152px;", "--subnav-row: 40px;", "--sub-gap: 24px;", "--part-max: none;"):
            self.assertIn(t, tok)
        # правило владельца 20.10: второй уровень заметно уже первого (≤ 0,8 бокового меню) на всех ширинах
        for w in (184, 164, 152):
            self.assertLessEqual(w, 248 * 0.8)
        self.assertEqual(css.count("@media (max-width: 1279.98px)"), 1)
        # 1280–1439 — подменю 164 (таблица ширин r4); правило раньше полос ≤ 1279 и < 900, иначе перебьёт их 152
        r4 = "@media (max-width: 1439.98px) { .kk-sub { --subnav-w: var(--subnav-w-md); } }"
        self.assertEqual(css.count(r4), 1)
        self.assertLess(css.index(r4), css.index("@media (max-width: 1279.98px)"))
        self.assertLess(css.index(r4), css.index(".kk-sub { --subnav-w: var(--subnav-w-sm)"))
        self.assertIn(".kk-sub { --subnav-w: var(--subnav-w-md); --sub-gap: 20px; }", css)
        self.assertIn(".kk-sub { --subnav-w: var(--subnav-w-sm); --sub-gap: 20px; }", css)
        self.assertNotIn('.gx-page[data-page="settings"] { max-width: 960px; }', read("components.css"))


@unittest.skipUnless(HAS_NODE and chrome_path(), "нет node или Chrome: подменю «Настроек» не проверено в браузере (K3_CHROME=…)")
class ChromeTest(unittest.TestCase):
    out = None

    @classmethod
    def setUpClass(cls):
        srv = ChromeServer(("127.0.0.1", 0), functools.partial(_Static, directory=STATIC))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        try:
            p = subprocess.run(["node", CHECK, base, chrome_path(), json.dumps(settings_items())], capture_output=True, timeout=300)
        finally:
            srv.shutdown()
            srv.server_close()
        if p.returncode != 0:
            raise AssertionError("k3_settings_nav (код %s): %s" % (p.returncode, p.stderr.decode("utf-8", "replace")[-2500:]))
        cls.out = json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])

    @staticmethod
    def crumb(st):
        return [x for x in st["crumb"] if x]

    def test_addresses(self):
        a = self.out["addr"]
        want = {"syn": "gateway", "synFocus": "site", "unknown": "look", "none": "look", "openHost": "gateway", "oldFocus": "bot", "oldItem": "access"}
        for k, part in want.items():
            self.assertEqual((a[k]["hash"], a[k]["part"], a[k]["cur"]), ("#settings?tab=" + part, part, part), k)
        self.assertEqual((a["syn"]["h1"], self.crumb(a["syn"])), ("Шлюз", ["Настройки", "Сервер"]))

    def test_subnav(self):
        n = self.out["nav"]
        self.assertEqual(n["label"], "Разделы настроек")
        self.assertEqual([g["t"] for g in n["groups"]], [g for g, _ in GROUPS])
        for g in n["groups"]:
            self.assertEqual(g["ul"], g["id"])
        items = [i for g in n["groups"] for i in g["items"]]
        self.assertEqual([i["part"] for i in items], PARTS)
        self.assertEqual([i["t"] for i in items], ["Вид", "Доступ", "О панели", "Шлюз", "Адреса", "Домен и сайт", "Время", "Хранение данных", "Проверка сервера",
                                                   "Бот и ИИ-агент", "Уведомления", "Копия настроек", "GitHub"])
        self.assertEqual([i["cur"] for i in items].count("page"), 1)
        self.assertTrue(all(i["href"] == "#settings?tab=" + i["part"] for i in items))
        self.assertEqual(n["width"], 184)
        self.assertLess(n["width"], n["sideW"] * 0.8 + 0.5, "второй уровень не уже первого")
        self.assertEqual(n["over"], 0, "пункт шире колонки или подпись с многоточием")
        self.assertEqual(n["split"], {}, "слово подписи подменю разорвано по букве (страница и ширина: слова)")
        self.assertEqual(n["partW"], n["secW"] - 184 - 24, "часть не на всю оставшуюся ширину")
        # таблица ширин r4: 1280–1439 — 164 (как ≤ 1279), 1440–1999 — 184, ≥ 2000 — 200
        self.assertEqual(n["bands"], {"1100": 164, "1280": 164, "1366": 164, "1439": 164, "1440": 184, "1999": 184, "2000": 200})

    def test_notify_compact(self):
        """«Уведомления» по кадру Н8 (nav2 r3): часть в одном экране 1440×900, полоса «В бот @имя · Все», группы событий
        карточками в 3 колонки, строки 32 с переключателем справа, «все» у группы, серые будущие группы."""
        n = self.out["nt"]
        a = n["a"]
        self.assertTrue(a["fit"], "часть «Уведомления» не помещается в 1440×900: лишние %s px" % a["over"])
        # П-1 REV-3315-view: подменю «Настроек» на 1440×900 — в области целиком (Д1 ужал его по решению к вопросу V2; детектор
        # k3_layout_check, правило subfit); лишний пункт (+40 px) — падение
        self.assertLessEqual(a["sub"], 0, "подменю «Настроек» ниже области на %s px" % a["sub"])
        self.assertEqual(sorted(a["groups"]), sorted(["exits", "groups", "server", "host", "bench", "how", "rules", "people"]))
        self.assertEqual(sorted(a["soon"]), ["people", "rules"])
        self.assertTrue(a["soonDis"], "переключатели будущих групп нажимаются")
        self.assertEqual(a["cut"], [], "подпись группы обрезана без полного текста в title")
        self.assertEqual(a["cols"], 3, "на 1440 группы — в 3 колонки")
        # 3374 (B7, B11): + «итог проверки» и «пропуски проверок» — 26 видов; 3379 (Q5): + node_ru_offline — 27
        self.assertEqual(a["sw"], 29, "все виды сервера (27) + «склеивать» + «нестабилен» — ни один не потерян")
        # строка 32; длинная подпись («Устройства переехали на другой выход») — в две строки, как в кадре Н8
        self.assertTrue(set(a["rowH"]) <= {32, 50} and 32 in a["rowH"], a["rowH"])
        self.assertTrue(a["swRight"], "переключатель строки — не справа")
        self.assertEqual(a["all"][0], "mixed")             # в заглушке «Устройства переехали» выключено
        h = a["nhead"]
        self.assertEqual(h["t"].strip(), "В бот@kaskad_k3_bot")
        self.assertRegex(h["s"], r"^Включено 25 из 27 · ")
        self.assertEqual((h["chip"], h["all"], h["info"]), ("бот подключён", "mixed", True))
        self.assertFalse(a["bar"])
        self.assertEqual((n["on"]["all"], set(n["on"]["sw"]), n["on"]["n"]), ("true", {"true"}, "9 из 9"))
        self.assertEqual((n["off"]["all"], set(n["off"]["sw"]), n["off"]["bar"]), ("false", {"false"}, "Изменений: 8"))   # было 8 из 9 вкл
        self.assertEqual(n["off"]["head"], "mixed")
        self.assertEqual(n["mixed"], "mixed", "подпись строки не переключает")
        self.assertTrue(n["headOn"], "«Все» в полосе бота не включил события")
        self.assertEqual((n["info"]["was"], n["info"]["now"], n["info"]["exp"], n["info"]["desc"]), (True, False, "true", 1))
        self.assertTrue(n["info"]["text"])
        self.assertEqual(n["cancel"]["all"], a["all"], "«Отменить» вернул переключатели")
        self.assertFalse(n["cancel"]["bar"])
        # UI-A2: 3 колонки на 1100–1999 (было 2 до 1439 — на 1100×900 не помещалось на 234 px), 4 от 2000, 1 на планшете
        self.assertEqual(n["cols"], {"1100": 3, "1280": 3, "1439": 3, "2000": 4, "800": 1})
        # часть — в одном экране и на узком компьютере; подпись группы не режется (в узкой колонке — переносом)
        for w in ("1100", "1280", "1439"):
            self.assertEqual(n["fit"][w], {"over": 0, "cut": []}, "«Уведомления» на %s×900: %s" % (w, n["fit"][w]))
        # EN 1440: длинная подпись группы обрезается — полный текст в title; без него — находка ELLIPSIS детектора
        self.assertEqual((n["en"]["n"], n["en"]["cut"]), (8, []), n["en"])

    def test_notify_phone(self):
        p = self.out["phone"]["nt"]
        self.assertEqual((p["n"], p["cols"]), (8, 1))
        self.assertEqual((p["was"], p["now"], p["exp"]), (True, False, "true"), "группа на телефоне раскрывается")
        self.assertGreaterEqual(p["allH"], 44)
        self.assertGreaterEqual(p["rowH"], 44)
        self.assertFalse(p["hscroll"])

    def test_time(self):
        """«Время» (кадр Н5): пояс узла выбором; правка — метка «правки» в подменю и полоса; «Сохранить» — PUT с ver,
        пояс на узле; пояс сменили в другой вкладке — 409, свежие данные и «Применить снова»."""
        tm = self.out["time"]
        a, b, c, d = tm["a"], tm["b"], tm["c"], tm["d"]
        self.assertEqual((a["zone"], a["dirty"], a["bar"], a["att"]), ("Europe/Moscow", False, None, None))
        self.assertFalse(a["os"], "«Определить по серверу» недоступна при известном поясе системы")
        self.assertLessEqual(a["w"], 880, "карточка-форма шире --form-max")
        self.assertTrue(a["desc"].startswith("Часовой пояс узла"))
        self.assertEqual((b["zone"], b["dirty"], b["bar"], b["att"]), ("Europe/Berlin", True, "Изменений: 1", "правки"))
        self.assertEqual((c["zone"], c["dirty"], c["bar"], c["att"], tm["server"]), ("Europe/Berlin", False, None, None, "Europe/Berlin"))
        self.assertEqual(tm["other"], 200)
        self.assertEqual(d["zone"], "Europe/Riga", "после 409 — свежий пояс узла")
        self.assertEqual(d["list"], ["Ваша правка: пояс Asia/TbilisiПрименить снова"])

    def test_attention_marks(self):
        a = self.out["att"]
        self.assertEqual(a["a"].get("site"), "gx-att--warn|истекает|Сертификат домена истекает через 12 дней")
        self.assertEqual(a["a"].get("bot"), "gx-att--none|не настроен|Бот не настроен: уведомления не приходят")
        self.assertNotIn("bot", a["b"], "бот настроен (статус прочитан) — метки нет")
        self.assertNotIn("site", a["b"])

    def test_click_pushes_and_focuses_part(self):
        c = self.out["nav"]["click"]
        self.assertEqual((c["hash"], c["part"], c["cur"], c["h1"]), ("#settings?tab=access", "access", "access", "Доступ к панели"))
        self.assertEqual(c["pushed"], 1)
        self.assertTrue(c["active"], "фокус не на части")
        self.assertEqual(self.crumb(c), ["Настройки", "Панель"])

    def test_back_and_forward(self):
        h = self.out["hist"]
        self.assertEqual([h[k]["part"] for k in ("back", "back2", "fwd")], ["access", "look", "access"])
        self.assertEqual([h[k]["cur"] for k in ("back", "back2", "fwd")], ["access", "look", "access"])

    def test_keys(self):
        self.assertEqual(self.out["keys"], ["github", "look", "access", "github", "look", "access", "about", "gateway"])

    def test_dirty_part(self):
        d = self.out["dirty"]
        self.assertEqual(d["att"], "gx-att gx-att--dirty|правки")
        self.assertTrue(d["ask"], "уход с правками без окна")
        self.assertEqual((d["stay"]["hash"], d["stay"]["part"]), ("#settings?tab=notify", "notify"))
        self.assertEqual(d["stayAtt"], d["att"])
        self.assertEqual((d["discard"]["hash"], d["discard"]["part"]), ("#settings?tab=access", "access"))
        # пункт главного меню из части с правками: часть и правки на месте; «Отменить» снимает метку
        self.assertEqual((d["menu"]["hash"], d["menu"]["part"], d["menuAtt"]), ("#settings?tab=notify", "notify", d["att"]))
        self.assertIsNone(d["cancelAtt"])

    def test_search_records(self):
        rows = {r["id"]: r for r in self.out["find"]}
        self.assertEqual(set(rows), {i[0] for i in settings_items()})
        for id_, at, need in settings_items():
            r = rows[id_]
            if at.get("open") == "tour":
                continue                                     # знакомство уводит по разделам — часть видна до него
            self.assertEqual(r["part"], at["tab"], r)
            self.assertEqual(r["hash"], "#settings?tab=" + at["tab"], r)
            if need == "gwOff":
                continue                                     # на заглушке шлюз включён — кнопки «Включить» нет
            if at.get("focus"):
                self.assertTrue(r["found"] and r["focused"], r)
            else:
                self.assertTrue(r["dialog"], r)

    def test_phone(self):
        p = self.out["phone"]
        self.assertEqual(p["groups"], [g for g, _ in GROUPS])
        self.assertEqual((p["rows"], p["part"], p["nav"]), (13, False, False))
        self.assertEqual(p["row"], {"hash": "#settings?tab=gateway", "title": "Шлюз"})
        self.assertEqual(p["back"], {"hash": "#settings", "rows": 13})

    def test_en(self):
        e = self.out["en"]
        self.assertEqual((e["label"], e["groups"], self.crumb(e), e["h1"]), ("Settings sections", ["Panel", "Server", "Messaging", "Data"], ["Settings", "Server"], "Gateway"))


if __name__ == "__main__":
    unittest.main()

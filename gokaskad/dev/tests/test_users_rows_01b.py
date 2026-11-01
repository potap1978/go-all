"""«Пользователи и устройства» — строки по финалу 01b Claude Design «Мягкая линия, скруглённый последний узел» (решение
владельца 24.10; эталон — Claude Design/kaskad-users-rows/export-2026-10-24-final, HANDOFF в «Users Rows final.dc.html»).

Линия дерева 2 px от центра аватара пользователя вниз через устройства без разрыва, отвод к значку устройства, последний
узел скруглён; значок устройства — под началом имени пользователя (не в колонке аватара); имя устройства мельче и тише,
платформа — строкой под ним; подложки у раскрытой группы нет; у пользователя — метка с числом устройств. Линия активности
одной ширины на размер (96 / 120 / 48) на одной X у пользователя и устройств; объём рядом — с таблицы 1500. 2560: таблица
не шире 2000, свободное — «Протоколам» и «Маршруту». «Строка»: короткие метки «Состояния». Телефон 390: устройства
раскрываются под пользователем. Наведение на устройство — его ветка цветом --text-3 (идея дизайна, принята).

Числа замера — те же, что у кадров эталона в Chrome (k3/stand/logs/R-users-rows.md, «Сверка с эталоном»)."""
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
CHECK = os.path.join(ROOT, "tools", "k3_users_rows_check.mjs")
CYR = re.compile(r"[А-Яа-яЁё]")


class StaticTest(unittest.TestCase):
    def test_grids_are_the_handoff(self):
        """Д2 вариант Б r3 (владелец 28.10) вместо сеток 01b: имя постоянной ширины, линия — свободная ширина."""
        css = read("people.css")
        self.assertIn(".gx-xt--k1 { --xt-gap: 10px; --pnw: 13rem; --cc-min: 120; }", css)
        self.assertIn(".gx-xt--k1.kk-pp-mk { --pnw: 15.5rem; }", css)
        self.assertIn(".gx-xt--k1 > * { --cols: 32px minmax(7.5rem, var(--pnw)) minmax(var(--actm, 0px), 1fr) 4.5rem var(--ppw, 6.5rem) var(--prw, 7.625rem) 92px; }", css)
        self.assertIn(".kk-dn--lg .gx-xt--k1 > * { --cols: 40px minmax(8.75rem, var(--pnw)) minmax(var(--actm, 0px), 1fr) 4.5rem var(--ppw, 6.5rem) var(--prw, 5.25rem) 104px; }", css)
        self.assertIn(".kk-dn--row .gx-xt--k1 > * { --cols: 32px minmax(6.875rem, var(--pnw)) minmax(var(--actm, 0px), 1fr) 4.375rem 50px; }", css)
        self.assertNotIn("--act-vw: 72px", css)                             # объёма рядом с линией нет (№21)
        self.assertIn("#part-people { max-width: 2000px; }", css)

    def test_tree_block(self):
        xt, css, app = read("xt.css"), read("people.css"), read("app.js")
        self.assertIn(".gx-xt__tr { display: none; position: absolute; left: calc(var(--tx) - var(--tl) / 2);", xt)
        self.assertIn(".gx-xt__tr--u { top: calc(50% + var(--av) + 3px); }", xt)        # на 3 px ниже аватара
        self.assertIn("border-bottom-left-radius: var(--trad); }", xt)                  # последний узел скруглён
        self.assertIn(".gx-xt:not(.gx-xt--tree) .gx-xt__g.is-open { background:", xt)   # подложка — только у списков без дерева
        self.assertIn(".gx-xt--tree .gx-xt__sub > .gx-xt__r:not(.is-flash) { box-shadow: none; }", xt)
        self.assertIn(".gx-xt--tree .gx-xt__g:has(.gx-xt__r--sub:hover) > .gx-xt__r .gx-xt__tr--u", xt)   # ветка под курсором
        # X — центр аватара, отступ устройства — под началом имени (HANDOFF «Линия дерева», «Отступ устройства»)
        self.assertIn(".gx-xt--k1.gx-xt--tree { --tx: 83px; --av: 11px; --trad: 8px; --tw: 16px; --xt-ind: 52px; }", css)
        self.assertIn(".kk-dn--lg .gx-xt--k1.gx-xt--tree { --tx: 102px; --av: 18px; --trad: 10px; --tw: 27px; --xt-ind: 74px; }", css)
        self.assertIn(".kk-dn--row .gx-xt--k1.gx-xt--tree { --tx: 74px; --av: 10px; --trad: 6px; --tw: 13px; --xt-ind: 46px; }", css)
        # кружок скрыт (is-cc-tight): линия — по левому краю имени (где был кружок: X кружка минус его половина, плюс --tl / 2),
        # на 3 px ниже блока имени; конец отвода — там же, где без сжатия (--tx + --tw: 99 / 129 / 87)
        tight = re.findall(r"^(?:\.kk-dn--(?:lg|row) )?\.gx-xt--k1\.gx-xt--tree\.is-cc-tight \{ --tx: (\d+)px; --av: [^;]*rem[^;]*; --tw: (\d+)px; \}$", css, re.M)
        self.assertEqual([(int(x), int(w)) for x, w in tight], [(73, 26), (85, 44), (65, 22)])
        self.assertEqual([x + w for x, w in ((73, 26), (85, 44), (65, 22))], [83 + 16, 102 + 27, 74 + 13])
        self.assertIn('XT().table({ cls: "gx-xt--k1 gx-xt--tree"', app)
        self.assertIn("deviceXRow(d, full, v.q, A, i === u.devices.length - 1)", app)
        self.assertIn('el("i", { class: "gx-xt__tr gx-xt__tr--d" + (last ? " gx-xt__tr--end" : ""), "aria-hidden": "true" })', app)

    def test_device_line_and_platform(self):
        app = read("app.js")
        # платформа — своей строкой во всех плотностях (не в «Крупно»-подписи «объём · система»)
        self.assertIn('el("span", { class: "gx-xt__os", text: platText(d.platform), title: platText(d.platform) })', app)
        self.assertNotIn('class: "gx-xt__os kk-nlg"', app)
        self.assertNotIn("actLine(", app)                                  # Д2 Б: линия — колонкой «Активность», не под именем
        self.assertIn('class: "kk-ucp", title: tn("pp.devCount", nDev), text: String(nDev)', app)

    def test_row_short_labels(self):
        app, ru, en = read("app.js"), read("i18n-ru.js"), read("i18n-en.js")
        self.assertIn("function stChip(label, rowS, tone, tip)", app)
        self.assertIn('c.setAttribute("data-row", rowS)', app)
        self.assertIn(".gx-xt--k1 .gx-chip[data-row]::after { content: attr(data-row); }", read("people.css"))   # Д2 Б: во всех плотностях
        for k, v in (("pp.k1.directR", "напрямую"), ("pp.k1.goneR", "удалён"), ("pp.k1.allOffR", "выключены"),
                     ("pp.k1.devsOf", "Устройства пользователя «{name}»")):
            self.assertIn('"%s": "%s",' % (k, v), ru)
        for k in ("pp.k1.directR", "pp.k1.goneR", "pp.k1.allOffR", "pp.k1.devsOf"):
            m = re.search(r'"%s": "([^"]*)",' % re.escape(k), en)
            self.assertTrue(m, k)
            self.assertNotRegex(m.group(1), CYR, k)


@unittest.skipUnless(HAS_NODE and chrome_path(), "нет node или Chrome: строки 01b не проверены (K3_CHROME=…)")
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

    def common(self, g, k):
        # линия — из центра аватара; кружок скрыт (is-cc-tight, REV-it1-pol П-1) — от левого края имени (там, где был кружок),
        # а не из пустого места посреди имени; начало — на 3 px ниже кружка или блока имени
        self.assertEqual(g["avOn"], not g["tight"], k)
        self.assertTrue(g["tr"][0] == g["tr"][1] == g["tr"][2] == g["anc"], (k, g["tr"], g["anc"]))
        self.assertEqual(g["trTop"], 3, k)
        self.assertEqual(g["trTone"], g["border"], k)                       # цвет --border
        self.assertEqual(g["end"], [False, True], k)
        self.assertEqual([x - e for x, e in zip(g["devX"], g["elb"])], [2, 2], k)   # отвод кончается за 2 px до значка
        if not g["tight"]:
            self.assertEqual(g["devX"], [g["nameX"], g["nameX"]], k)         # значок устройства — под началом имени
        self.assertEqual(g["gapNext"], 0, k)                                 # строки устройств вплотную: линия без разрыва
        self.assertEqual(g["gbg"], "rgba(0, 0, 0, 0)", k)                    # подложки у раскрытой группы нет
        self.assertEqual(g["subShadow"], ["none", "none"], k)                # разделителей между устройствами нет
        if not k.startswith("row"):                                          # Д2 Б: в «Строке» платформы нет
            self.assertTrue(g["osF"][1] and g["osF"][2], k)                  # платформа видна, --text-3
            self.assertIn(g["osBelow"], (0, 1, 2), k)                        # строкой под именем (метка у имени — строка 20)
        self.assertEqual(g["ucp"][:2], ["2", "2 устройства"], k)
        sp = [x for x in g["sp"] if x]
        self.assertTrue(all(x == sp[0] for x in sp), (k, g["sp"]))          # линия — одна X и ширина у всех строк
        self.assertIn(len(sp), (0, 3), (k, g["sp"]))                         # у всех строк или ни у одной (узкая «Средне»)

    def test_medium_1440_is_the_frame(self):
        g = self.r["geo"]["md-1440"]
        self.common(g, "md-1440")
        self.assertEqual(g["tbw"], 916)                                      # ширина таблицы прежняя
        self.assertEqual(g["cols"], "32px 248px 164px 72px 104px 122px 92px")   # с метками у имени (kk-pp-mk)
        self.assertEqual(g["gap"], "10px")
        self.assertEqual(g["h"], [32, 40, 40])
        self.assertEqual((g["avc"], g["nameX"]), (83, 102))
        self.assertEqual(g["sp"][0], [308, 164])                             # линия — вся колонка «Активность»
        self.assertEqual(g["vol"], [None, None, None])                       # №21: объёма рядом нет
        self.assertEqual((g["nF"], g["dF"], g["osF"][0]), (["500", "16px", "20px"], ["400", "14px", "17px"], "12.5px"))   # №16: +1 px
        self.assertEqual((g["endRad"], g["ucp"][2], g["devIco"]), ("8px", 18, 14))

    def test_large_1440_is_the_frame(self):
        g = self.r["geo"]["lg-1440"]
        self.common(g, "lg-1440")
        self.assertEqual(g["cols"], "40px 270px 160px 72px 104px 84px 104px")
        self.assertEqual(g["h"], [68, 64, 64])
        self.assertEqual((g["avc"], g["nameX"]), (102, 132))
        self.assertEqual(g["sp"][0], [338, 160])
        self.assertEqual(g["vol"], [None, None, None])
        self.assertEqual((g["nF"], g["dF"], g["osF"][0]), (["500", "17px", "22px"], ["400", "15px", "20px"], "13.5px"))
        self.assertEqual((g["endRad"], g["ucp"][2], g["devIco"]), ("10px", 22, 16))

    def test_row_1440_is_the_frame(self):
        g = self.r["geo"]["row-1440"]
        self.common(g, "row-1440")
        self.assertEqual(g["cols"], "32px 171px 98px 70px 50px")             # r3: линия ≈ 98
        self.assertEqual(g["gap"], "6px")
        self.assertEqual(g["h"], [28, 32, 32])
        self.assertEqual((g["avc"], g["nameX"]), (74, 90))
        self.assertEqual(g["sp"][0], [221, 98])
        self.assertEqual((g["nF"], g["dF"], g["osF"][0]), (["500", "15px", "18px"], ["400", "13.5px", "16px"], "12.5px"))
        self.assertFalse(g["osF"][2])                                        # платформы в «Строке» нет
        self.assertEqual(g["endRad"], "6px")

    def test_wide(self):
        g = self.r["geo"]["md-2560"]
        self.common(g, "md-2560")
        self.assertEqual(g["tbw"], 2000)                                     # таблица не шире 2000
        self.assertTrue(g["cols"].startswith("32px 280px "), g["cols"])
        self.assertTrue(g["cols"].endswith(" 92px"), g["cols"])
        w = self.r["wide"]
        self.assertEqual((w["more"], w["cutRoute"]), (0, []))               # «Протоколы» без «+N», «Маршрут» целиком
        self.assertGreater(w["partLeft"], w["subLeft"])                      # часть прижата к подменю
        for k in ("md-2000", "md-1100", "lg-1100", "lg-2560"):
            self.common(self.r["geo"][k], k)
        # окно 1100 (таблица 600): «Протоколы» и «Маршрут» свернулись сами, линия — не уже 120
        for k in ("md-1100", "lg-1100"):
            c = self.r["geo"][k]["cols"].split()
            self.assertEqual((c[2], c[4], c[5]), ("120px", "24px", "24px"), k)
        # там же тесно (is-cc-tight): кружка нет, имя — на его месте, линия — по левому краю имени, значки устройств на месте
        for k, want in (("md-1100", (None, 72, 73, [102, 102])), ("lg-1100", (None, 84, 85, [132, 132]))):
            g = self.r["geo"][k]
            self.assertTrue(g["tight"], k)
            self.assertEqual((g["avc"], g["nameX"], g["anc"], g["devX"]), want, k)
        for k, g in self.r["geo"].items():                                     # «доступа нет» не обрезано, метки не за краем
            self.assertEqual(g["mut"][1], [], k)
            self.assertEqual(g["stCut"], [], k)
            if k != "lg-1100":                                               # «Крупно» на 600: метка устройства — под обрезом
                self.assertEqual(g["stOut"], [], k)

    def test_hover_branch(self):
        h = self.r["hover"]
        self.assertEqual((h["u"], h["end"], h["prev"]), (h["t3"],) * 3)      # от аватара до своего узла — --text-3
        self.assertNotEqual(h["prevAfter"], h["t3"])                         # отвод соседнего устройства — прежний
        self.assertNotEqual(h["other"], h["t3"])                             # чужая группа — без подсветки
        self.assertNotEqual(h["after"], h["t3"])                             # мышь ушла — как было
        self.assertEqual(h["bg"], "rgba(0, 0, 0, 0)")

    def test_state_labels(self):
        # Д2 Б: метка — у имени во всех плотностях, короче словом («напрямую»), полный текст и причина — в подсказке
        for lang in ("ru", "en"):
            c = self.r["chips_" + lang]
            self.assertTrue(all(x["title"] for x in c["md"] if x["cut"]), (lang, c))   # с многоточием — только с подсказкой
            self.assertEqual([x["text"] for x in c["md"]], [x["text"] for x in c["row"]], lang)
            for x in c["md"] + c["row"]:
                self.assertGreater(len(x["title"]), len(x["full"]), (lang, x))   # подсказка — полный текст и причина
        self.assertIn("напрямую", [x["text"] for x in self.r["chips_ru"]["md"]])
        self.assertTrue(all(x["title"].startswith("выходы недоступны") for x in self.r["chips_ru"]["row"] if x["text"] == "напрямую"))
        self.assertTrue(all(not CYR.search(x["text"] + x["title"]) for x in self.r["chips_en"]["row"]))

    def test_phone(self):
        p = self.r["phone"]
        self.assertEqual((p["before"]["exp"], p["before"]["hidden"], p["before"]["ctl"], p["before"]["ucp"], p["before"]["more"]), ("false", True, True, "2", True))   # Д2 Б Т1 без «⋯», но действия только в нём — оставлен (R-it1-t3)
        self.assertTrue(p["before"]["label"].startswith("Устройства пользователя «Мама»"))
        self.assertEqual(p["noDevs"], {"ucp": False, "on": False})             # без устройств — ни метки, ни стрелки, «нет устройств» словами
        o = p["open"]
        self.assertEqual((o["exp"], o["shown"], o["devs"]), ("true", True, 2))
        self.assertTrue(all(x >= 56 for x in o["h"]), o["h"])               # строка устройства 56, цель касания ≥ 44
        self.assertEqual((o["rowH"], o["avc"], o["tu"]), (61, 34, 34))       # линия из центра аватара (x 34); вторая строка — линия активности
        self.assertEqual(o["end"], [False, True])
        self.assertEqual(o["ic"], [66, 66])                                  # плитка значка с x 66
        self.assertEqual(o["os"], ["Keenetic", "iPhone / iPad"])
        self.assertEqual(p["devSheet"], p["devName"])                         # нажатие на устройство — его лист
        self.assertTrue(p["rerendered"])                                     # список перестроен живыми данными…
        self.assertEqual(p["afterLive"], [True, "true", False])              # …а раскрытие не сброшено
        self.assertEqual(p["afterReload"], [True, "true", False])
        self.assertEqual((p["userSheet"], p["stillOpen"]), ("Мама", True))   # нажатие на имя — лист пользователя
        self.assertTrue(self.r["en"])
        self.assertTrue(all(not CYR.search(x) for x in self.r["en"]), self.r["en"])


if __name__ == "__main__":
    unittest.main()

"""UI-4 (владелец 20.10, Claude Design People r1 — концепция К1 «Таблица»): «Люди и устройства» на компьютере — таблица.

Человек — строка (переключатель · имя · «в сети» · протоколы · маршрут · активность · «Выдать настройки» и «⋯»),
устройства — строками под ним по нажатию. Плотность «Крупно · Средне · Строка» помнится в браузере. Общий блок «строка
списка с раскрытием» gx-xt — xt.js / xt.css (потом «Выходы» и «Пробросы»). Поведение — в настоящем Chrome на заглушке
панели (tools/k3_people_check.mjs); сколько людей в первом экране — детектор k3/stand/ui_audit_run.mjs
(people-first-screen). Здесь же — что ни одно прежнее действие человека и устройства не потеряно: каждый пункт «⋯»
нажат и сверено, какое окно он открыл (круг 2, REV-ui4 З-8).

Круг 2 (BRIEF-UI-4-r2 и замечание владельца 20.10): протоколы — один порядок (как в «Протоколах», от порядка выдачи
доступа не зависит) и постоянные места: одинаковый протокол во всех строках на одной координате X, плашки не
обрезаются — лишнее в «+N»; стрелки по людям и раскрытым устройствам; «Строка» сеткой (колонка человека не меняется);
фильтр на планшете и телефоне; подписи для диктора с именем; одно слово для выключения."""
import functools
import json
import os
import re
import subprocess
import threading
import unittest

from tests import jsparts

from tests.test_332f_give import HAS_NODE, read
from tests.test_333q_layout import ChromeServer, _Static, chrome_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CHECK = os.path.join(ROOT, "tools", "k3_people_check.mjs")

# «⋯» — все прежние действия карточки человека и устройства (было: карандаш, метка id, «Поделиться ▾», «ссылка для
# приложений», «Возобновить», «⋯» карточки; у устройства — «Настроить», «На схеме», карандаш, id и логин)
PERSON_MENU = ["Поделиться", "Переименовать", "Маршрут", "Ссылка для приложений", "На схеме", "График активности", "Добавить устройство", "Поставить на паузу", "Удалить…", "id"]
DEVICE_MENU = ["Настроить", "На схеме", "График активности", "Переименовать", "Маршрут", "Выключить", "Удалить…", "id", "Логин"]
# что делает каждый пункт (заглушка: «Мама» и устройство «l2tp-g3» человека «Тест G3») — те же окна, что у прежних карточек
PERSON_ACTS = ["Поделиться → окно: Страница: Мама", "Переименовать → окно: Переименовать «Мама»", "Маршрут → окно: Маршрут: Мама",
               "Ссылка для приложений → окно: Ссылка для приложений (подписка): Мама", "На схеме → переход: #scheme",
               "График активности → окно: График активности: Мама",
               "Добавить устройство → окно: Добавить устройство", "Поставить на паузу → окно: Поставить «Мама» на паузу?",
               "Удалить… → окно: Удалить «Мама»?", "id → скопировано: u-1a2b3c4d"]
DEVICE_ACTS = ["Настроить → окно: Устройство", "На схеме → переход: #scheme", "График активности → окно: График активности: l2tp-g3",
               "Переименовать → окно: Переименовать «l2tp-g3»",
               "Маршрут → окно: Устройство", "Выключить → окно: Выключить «l2tp-g3»?", "Удалить… → окно: Удалить устройство «l2tp-g3»?",
               "id → скопировано: d-3c3c0002", "Логин → скопировано: l2tp-g3-22bb"]


class StaticTest(unittest.TestCase):
    def test_block_is_portable(self):
        """xt.js — без app.js и без готовых фраз: тексты приходят параметрами; хранилище — в try/catch."""
        xt = read("xt.js")
        self.assertIn("g.KaskadXT = XT;", xt)
        self.assertNotRegex(xt, r"innerHTML|insertAdjacentHTML|\beval\(|new Function")
        code = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", xt, flags=re.S))
        self.assertNotRegex(code, r"[А-Яа-яЁё]")                      # готовых фраз нет — только в комментариях
        self.assertIn("try { return g.localStorage.getItem(k); } catch (e)", xt)
        self.assertNotRegex(code, r"kaskad-|kk-pp")                      # ключ хранилища и классы продукта — параметрами
        self.assertIn("var cur = XT.density(o.key);", xt)
        self.assertIn('NAV: "button.gx-xt__x, .gx-xt__r--sub[tabindex]"', xt)   # стрелки — и по строкам устройств
        css = read("xt.css")
        for cls in (".gx-xt__hw", ".gx-xt__r", ".gx-xt__x", ".kk-dn--lg", ".kk-dn--md", ".kk-dn--row", ".gx-track--sm", ".ga-btn--xs"):
            self.assertIn(cls, css)
        self.assertIn("--xt-row: 26px", css)
        self.assertIn("--xt-row: 68px", css)
        self.assertIn("--xt-row: 28px", css)
        # «Строка» — две колонки сверху вниз (эталон 23.10, А3): половины групп — в своих колонках (XT.cols), не
        # column-count — с ним при раскрытии люди перескакивали между колонками (REV-ui4 З-2)
        self.assertNotIn("column-count:", css)
        self.assertIn(".gx-xt__cols, .gx-xt__col { display: contents; }", css)
        self.assertIn(".kk-dn--row .gx-xt__cols { position: relative; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }", css)
        self.assertIn("var all = (groups || []).filter(Boolean), h = Math.ceil(all.length / 2)", xt)
        self.assertIn(".gx-xt__slots > .is-cut { display: none; }", css)
        self.assertIn(".gx-xt__st > .gx-chip { flex: none; }", css)       # «Состояние» — метка целиком
        self.assertIn(".gx-xt__nl > .gx-chip { flex: none; }", css)      # метка проблемы не сжимается
        self.assertNotIn(".gx-xt--k1", css)                              # сетка «Людей» — в people.css
        self.assertNotIn(".kk-pp__", css)
        pcss = read("people.css")
        for cls in (".gx-xt--k1", ".kk-pp__bar", ".kk-pp__dn", ".gx-menu__k", ".kk-dn--row .gx-xt__c.is-dot > .gx-xt__t"):
            self.assertIn(cls, pcss)

    def test_page_uses_it(self):
        app, idx = read("app.js"), read("index.html")
        self.assertLess(idx.index('src="find.js'), idx.index('src="xt.js'))
        self.assertLess(idx.index('src="xt.js'), idx.index('src="app.js'))
        self.assertIn('href="xt.css', idx)
        self.assertLess(idx.index('href="xt.css'), idx.index('href="people.css'))
        self.assertIn("return peopleTable(v);", app)
        self.assertIn('XT().densitySeg({ key: "kaskad-density-people", cls: "kk-pp__dn"', app)
        self.assertNotIn("function userCard(", app)                     # карточек на компьютере больше нет
        self.assertIn('xtSlots(protoSort(d.access), (k) => protoChip(d, k), "proto")', app)   # плашка устройства — выдача с ним
        self.assertIn('role: "img", text: inboundShort(k),', app)        # подписи полные короткие: WireGuard, AWG 2.0 …
        # один порядок протоколов: установленные на узле, иначе канонический
        self.assertIn('const PROTO_CANON = ["wg", "awg2", "awg3", "vless", "hy2", "socks5", "l2tp", "ikev2"', app)
        self.assertIn("return protoSort(s); };", app)                      # ppProtos
        # обучение, шаг «Люди»: таблица или «Добавить первого человека» (З-5); прежнего .kk-user нет
        self.assertIn('tb.setAttribute("data-tour", "people");', app)
        self.assertIn('b.setAttribute("data-tour", "people");', app)
        self.assertIn('$(".gx-card, .gx-xt, .gx-kpis", sec)', app)
        self.assertNotIn('.kk-user, .gx-kpis', app)

    def test_texts_ru_en(self):
        ru, en = read("i18n-ru.js"), read("i18n-en.js")
        keys = sorted(set(re.findall(r'"(pp\.k1\.[A-Za-z]+)":', ru)))
        self.assertGreater(len(keys), 25)
        self.assertEqual(keys, sorted(set(re.findall(r'"(pp\.k1\.[A-Za-z]+)":', en))))
        for k in re.findall(r't\("(pp\.k1\.[A-Za-z]+)"', read("app.js")):
            self.assertIn('"%s":' % k, ru, k)
        # подписи протоколов — полные короткие (координатор 20.10): WireGuard, AWG 2.0, AWG 3.1, VLESS, Hysteria2, SOCKS5, L2TP
        for kind, short in (("wg", "WireGuard"), ("awg2", "AWG 2.0"), ("awg3", "AWG 3.1"), ("vless", "VLESS"), ("hy2", "Hysteria2"), ("socks5", "SOCKS5"), ("l2tp", "L2TP")):
            self.assertIn('"ibs.%s": "%s",' % (kind, short), ru)
            self.assertIn('"ibs.%s": "%s",' % (kind, short), en)
        self.assertNotIn('"ibx.', ru)

    def test_one_word_for_disable(self):
        """П-1, поправка Д2 вариант Б (владелец 28.10, решение 8): пользователь — «пауза» (метка «пауза», фильтр «На паузе»,
        «Поставить на паузу» / «Снять с паузы», «Поставить «…» на паузу?»), устройство — «Выключить» / «выключено»;
        «Доступ приостановлен» — только цитатой того, что покажет страница пользователя."""
        ru, en = read("i18n-ru.js"), read("i18n-en.js")
        for k, v in (("pp.pause", "Выключить"), ("pp.k1.enable", "Включить"), ("pp.pauseTitle", "Поставить «{name}» на паузу?"),
                     ("pp.pauseDevTitle", "Выключить «{name}»?"), ("pp.k1.disable", "Выключить"), ("pp.k1.paused", "пауза"),
                     ("pp.k1.pause", "Поставить на паузу"), ("pp.k1.unpause", "Снять с паузы"), ("m.fOff", "На паузе")):
            self.assertIn('"%s": "%s",' % (k, v), ru)
        for k, v in (("pp.pause", "Disable"), ("pp.k1.enable", "Enable"), ("pp.k1.disable", "Disable"), ("pp.k1.paused", "paused"),
                     ("pp.k1.pause", "Pause"), ("pp.k1.unpause", "Unpause"), ("m.fOff", "Paused")):
            self.assertIn('"%s": "%s",' % (k, v), en)
        vals = lambda s: "\n".join(re.findall(r'^\s*"[^"]+": "(.*)",?$', s, re.M))     # только тексты, не ключи
        self.assertEqual(re.findall(r"[Пп]риостанов\w*", vals(ru)), ["приостановлен"] * 2)   # pauseText и pg.paused — цитата
        self.assertNotRegex(vals(ru), r"[Вв]озобнов")
        self.assertNotRegex(vals(en), r"\b[Rr]esumed?\b")


@unittest.skipUnless(HAS_NODE, "нет node")
class ShortDayTest(unittest.TestCase):
    """«Активность» — «с 19.10» / «since Oct 19»: год — только если не этот (EN не обрезается, REV-ui4 С-1)."""
    def day(self, lang, locale):
        code = jsparts.dates(read("boot.js"), "D", lang, locale, "2027-10-11T12:00:00Z") + """
console.log(JSON.stringify([D.day(new Date(Date.UTC(2027, 9, 9, 10))), D.day(new Date(Date.UTC(2026, 9, 9, 10))), D.day(null)]));
"""
        r = subprocess.run(["node", "-e", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, env=dict(os.environ, TZ="UTC"))
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace")[-1500:])
        return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])

    def test_day(self):
        self.assertEqual(self.day("en", "en-US"), ["Oct 9", "Oct 9, 2026", ""])
        self.assertEqual(self.day("ru", "ru-RU"), ["09.10", "09.10.2026", ""])
        self.assertIn("DATES.time(d) : DATES.day(d) })", read("app.js"))


@unittest.skipUnless(HAS_NODE and chrome_path(), "нет node или Chrome: «Пользователи» таблицей не проверены (K3_CHROME=…)")
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

    def test_toggle_and_keys(self):
        t = self.r["toggle"]
        self.assertEqual(t["n"], 4)
        self.assertEqual((t["s0"]["exp"], t["s0"]["hidden"], t["s0"]["ctl"]), ("false", True, True))
        self.assertEqual((t["s1"]["exp"], t["s1"]["open"], t["s1"]["shown"]), ("true", True, True))
        self.assertEqual((t["s2"]["exp"], t["s2"]["shown"]), ("false", False))
        self.assertTrue(t["rowClick"]["open"])                           # по свободному месту строки
        self.assertFalse(t["menuNoToggle"]["open"])                      # «⋯» строку не раскрывает
        self.assertEqual((t["enter"]["exp"], t["space"]["exp"]), ("true", "false"))
        self.assertEqual(t["down"], "Папа")                              # стрелка вниз — к следующей строке
        # З-1: от раскрытого человека — по его устройствам, дальше к следующим людям и обратно, без застревания
        a = self.r["arrows"]
        self.assertEqual(len(a["devs"]), 2)
        self.assertEqual(a["down"], a["devs"] + ["Папа", "Тест G3"])
        self.assertEqual(a["up"], ["Папа"] + a["devs"][::-1] + ["Мама"])

    def test_four_devices_and_memory(self):
        f = self.r["four"]
        # эталон 23.10 (People Activity r1, «Средне»): у человека с устройствами строки «+ Добавить устройство» нет —
        # пункт «Добавить устройство» в «⋯»; строка остаётся только у человека без устройств
        self.assertEqual((f["devs"], f["add"], f["shown"]), (4, 0, True))
        self.assertEqual(self.r["noDevAdd"], 1)
        m = self.r["memory"]
        self.assertTrue(m["afterLive"]["open"])
        self.assertTrue(m["afterReload"]["open"])
        self.assertFalse(m["othersOpen"])

    def test_nothing_lost_in_menus(self):
        strip = lambda items: [x.split("=")[0] for x in items]
        self.assertEqual(strip(self.r["menus"]["person"]), PERSON_MENU)
        self.assertEqual(strip(self.r["menus"]["device"]), DEVICE_MENU)
        self.assertIn("id=u-1a2b3c4d", self.r["menus"]["person"])
        self.assertIn("Маршрут=по умолчанию", self.r["menus"]["person"])
        # З-8: каждый пункт нажат — то же окно (переход, копирование), что у прежней карточки; подмена действия видна
        self.assertEqual(self.r["actions"]["person"], PERSON_ACTS)
        self.assertEqual(self.r["actions"]["device"], DEVICE_ACTS)

    def test_give_in_one_press(self):
        g = self.r["give"]
        self.assertTrue(g["pick"])
        # Т3: одно окно — «Подписка» открыта, устройство плашками; адреса нет до «QR-код» / «Скопировать»
        self.assertEqual((g["s0"]["tabs"], g["s0"]["devs"], g["s0"]["hidden"], g["s0"]["url"], g["s0"]["steps"]),
                         ("sub:true static:false", "Роутер на даче|Телефон Мамы*", True, False, 3), g)   # Д-2: телефон, не роутер
        self.assertEqual((g["s1"]["tabs"], g["s1"]["devs"], g["s1"]["give"]), ("sub:false static:true", "Роутер на даче|Телефон Мамы*", True), g)
        self.assertEqual((g["s2"]["devs"], g["s2"]["give"]), ("Роутер на даче*|Телефон Мамы", True), g)
        self.assertEqual((g["s3"]["tabs"], g["s3"]["devs"], g["s3"]["url"]), ("sub:true static:false", "Роутер на даче*|Телефон Мамы", False), g)   # вкладка не сбросила устройство
        # «Скопировать» — подписка включена: адрес на экране, а без доступа из интернета — почему его нет и куда идти
        self.assertFalse(g["s4"]["hidden"], g)
        self.assertTrue(g["s4"]["url"] or g["s4"]["al"], g)
        # REV-it1-b С-1: подписка включена, адрес известен — при открытии окна адреса на экране нет; «Скопировать» и
        # «QR-код» его показывают; новое открытие окна — снова скрыт
        self.assertEqual(g["pub"], "200 200", g)                       # открытый режим заглушки и вход — для адреса подписки
        for k in ("s5", "s7"):
            self.assertEqual((g[k]["hidden"], g[k]["url"], g[k]["al"]), (True, False, ""), (k, g))
        for k in ("s6", "s8"):
            self.assertEqual((g[k]["hidden"], g[k]["url"]), (False, True), (k, g))
        self.assertTrue(g["s8"]["qr"], g)
        # доводка круга 1, п.3: окно «Ссылка для приложений» — приложения блоком каталога, без треугольников и без
        # прежнего списка словами; адрес с токеном — только после «QR-код» (С-1), новое открытие — снова скрыт
        sb0, sb1, sb2 = g["sb0"], g["sb1"], g["sb2"]
        self.assertTrue(sb0["title"].startswith("Ссылка для приложений"), sb0)
        self.assertEqual(sb0["plat"], "iPhone и iPad", sb0)
        self.assertGreaterEqual(sb0["apps"], 5, sb0)
        self.assertEqual((sb0["tri"], sb0["oldList"]), (0, False), sb0)
        self.assertEqual((sb0["hidden"], sb0["token"], sb0["qr"]), (True, False, False), sb0)
        for b in ("Новая ссылка", "Отозвать ссылку"):
            self.assertTrue(any(b in x for x in sb0["btns"]), (b, sb0))         # свои функции окна на месте
        self.assertEqual((sb1["hidden"], sb1["token"], sb1["qr"]), (False, True, True), sb1)
        self.assertEqual((sb2["hidden"], sb2["token"]), (True, False), sb2)
        # REV-it1-pol З-3: пометки — не больше двух строк, у выбранного приложения — целиком; нажатие на строку с
        # обрезанной пометкой (и на телефоне) — она целиком
        self.assertTrue(sb0["notes"], sb0)
        for n in sb0["notes"]:
            self.assertLessEqual(n["lines"], 2 if not n["on"] else 9, n)
            if n["on"]:
                self.assertFalse(n["cut"], n)
        if g["sbNote"]["id"]:
            self.assertEqual((g["sbNote"]["after"]["on"], g["sbNote"]["after"]["cut"]), (True, False), g["sbNote"])
        # REV-it1-pol З-1: после «Скопировать» — адрес и кнопка «QR-код», её нажатие рисует код
        sb3, sb3q = g["sb3"], g["sb3q"]
        self.assertEqual((sb3["hidden"], sb3["token"], sb3["qr"], g["sb3"]["clip"]), (False, True, False, True), sb3)
        self.assertTrue(any("QR" in x for x in sb3["btns"]), sb3)
        self.assertEqual((sb3q["token"], sb3q["qr"]), (True, True), sb3q)
        # REV-it1-pol З-2: показан → «Отозвать ссылку» → «Создать ссылку…» — новый адрес снова скрыт (С-1)
        self.assertEqual(g["sb4off"]["token"], False, g["sb4off"])
        self.assertEqual((g["sb4"]["hidden"], g["sb4"]["token"], g["sb4"]["qr"]), (True, False, False), g["sb4"])
        # О5 / t4: шапка за устройством, «N устройств» — формы словаря, протоколы устройства
        self.assertEqual((g["s0"]["title"], g["s0"]["sub"]), ("Как подключить «Телефон Мамы»?", "Мама · 2 устройства · WireGuard, AmneziaWG 2.0, AmneziaWG 3.1, VLESS"), g)
        self.assertEqual((g["s1"]["title"], g["s2"]["title"]), ("Как подключить «Телефон Мамы»?", "Как подключить «Роутер на даче»?"), g)
        self.assertEqual(g["s2"]["sub"], "Мама · 2 устройства · AmneziaWG 2.0, L2TP/IPsec", g)
        # ряд «Платформа»: роутера среди платформ подписки нет — «Другое»; телефон (ios) — «iPhone и iPad»
        plats = ["iPhone и iPad", "Android", "Android TV", "Windows", "macOS", "Linux", "Другое"]
        mark = lambda on: "|".join(x + ("*" if x == on else "") for x in plats)
        self.assertEqual(g["s0"]["plats"], mark("iPhone и iPad"), g)
        self.assertEqual(g["s3"]["plats"], mark("Другое"), g)
        # колонка 1 — приложения подписки этой платформы из каталога, тем же блоком give.js: «рекомендуем» у первого
        from kaskad.gw import install_apps
        from kaskad.i18n import use_lang
        with use_lang("ru"):
            want = lambda p: "|".join(a["name"] + ("+rec" if i == 0 else "") for i, a in enumerate(install_apps.sub_block(p)["apps"]))   # п.4: без «ссылка подписки»
            self.assertEqual(g["s0"]["apps"], want("ios"), g)
            self.assertEqual(g["s3"]["apps"], want("other"), g)
            self.assertEqual((g["sAnd"]["plats"], g["sAnd"]["apps"]), (mark("Android"), want("android")), g)
        self.assertEqual((g["sBack"]["tabs"], g["sBack"]["plats"]), ("sub:true static:false", mark("Android")), g)   # вкладка выбор не сбросила
        self.assertEqual(g["sArrow"], [mark("Android TV"), "androidtv"], g)                    # стрелка — к соседней, фокус на ней
        self.assertEqual(g["s0"]["steps"], 3)
        # T-it1b п.4–5 (1440×900): строки приложений в одну линию, окно не прокручивается, все строки и шаг 3 видны —
        # iPhone (6 приложений) и Android; «есть подделки» — одна строка над списком
        for k in ("s0", "sAnd"):
            self.assertLessEqual(g[k]["rowH"], 56, (k, g[k]))
            self.assertEqual((g[k]["scroll"], g[k]["appsIn"]), (0, True), (k, g[k]))
        self.assertEqual(g["s0"]["fakes"], "В магазинах есть подделки с похожими названиями — ставьте приложения только по ссылкам отсюда", g)
        # подвал: слева пометка с замком, «Закрыть» справа (у ключа — своя пометка)
        self.assertEqual((g["s0"]["foot"], g["s0"]["lock"], g["s0"]["closeRight"]), ("Ссылка подписки — показывайте только самому пользователю.", True, True), g)
        self.assertEqual(g["s1"]["foot"], "Здесь ключ — показывайте только самому пользователю. Панель его не хранит.", g)
        e = self.r["giveEn"]
        self.assertEqual((e["title"], e["sub"]), ("How to connect “Mom’s phone”?", "Mom · 2 devices · WireGuard, AmneziaWG 2.0, AmneziaWG 3.1, VLESS"), e)
        self.assertEqual(e["plats"], "iPhone & iPad*|Android|Android TV|Windows|macOS|Linux|Other", e)
        self.assertTrue(e["apps"].startswith("Karing+rec|"), e)
        self.assertEqual((e["rowH"] <= 56, e["scroll"], e["appsIn"]), (True, 0, True), e)
        self.assertEqual(e["fakes"], "The stores have fakes with similar names — install the apps only via the links here", e)
        self.assertEqual(e["foot"], "This is a subscription link — show it only to the user.", e)
        self.assertEqual(e["proto"], "all protocols of “Mom” in one link: VLESS, AmneziaWG 3.1, AmneziaWG 2.0, WireGuard", e)
        self.assertIsNone(re.search("[А-Яа-яЁё]", json.dumps(e, ensure_ascii=False)), e)
        self.assertTrue(g["offDis"])
        self.assertIn("выключен", g["offTip"])
        self.assertTrue(self.r["forty"]["single"])
        self.assertTrue(self.r["forty"]["pick"])                        # QR — сначала выбор (владелец 24.10)
        self.assertEqual(self.r["forty"]["pickOrder"], ["sub", "static"])  # подписка — первой
        self.assertTrue(self.r["forty"]["dialog"])                      # «Статический ключ», одно устройство — окно выдачи
        self.assertEqual(set(self.r["forty"]["offDevSw"]), {True})      # у выключенного человека — недоступны

    def test_search_opens(self):
        s = self.r["search"]
        self.assertEqual(s["people"], ["Тест G3"])
        self.assertTrue(s["s"]["open"])
        self.assertEqual(s["hit"], ["socks"])
        self.assertFalse(s["closed"]["open"])
        self.assertFalse(s["sameQuery"]["open"])                          # закрыт до смены запроса
        self.assertTrue(s["newQuery"]["open"])

    def test_density(self):
        d = self.r["density"]
        self.assertIn("kk-dn--md", d["def"])                             # по умолчанию «Средне»
        self.assertEqual(d["group"], "radiogroup")
        self.assertEqual(d["radios"], ["Крупно:false", "Средне:true", "Строка:false"])
        self.assertEqual(d["row"], ["kk-pp__body kk-dn--row", "row"])
        self.assertEqual(d["rowCols"], "2")                              # тело прежней ширины (окно шире на подменю, BODY)
        self.assertEqual(d["reload"], ["kk-pp__body kk-dn--row", "row"])  # запомнено в браузере
        self.assertEqual(d["arrow"], ["kk-pp__body kk-dn--md", "md"])
        self.assertEqual((d["mdRow"], d["rowH"]), (32, 28))              # Д2 Б: «Средне» — строка 32

    def test_protocol_order_and_slots(self):
        o = self.r["order"]
        # порядок узла (как в «Протоколах»: заглушка — в порядке сервера), плотно слева, без пустых мест
        # nav2 r1 (UI-A1): «w1440md» и др. — ширина тела таблицы, как до подменю «Людей»: окно шире на подменю
        # (k3_people_check.mjs, BODY); узкое тело рядом с подменю (1100–1279, эталон H6) — шаг UI-B
        self.assertGreater(o["reversed"], 2)
        self.assertEqual(o["after"], o["before"])                         # порядок выдачи доступа ничего не меняет
        self.assertIn("Мама:wg@0,awg2@1,awg3@2,l2tp@3,vless@4", o["before"])
        for k in ("w1440md", "w2000md", "w2560md", "w1440lg", "w1180md", "w1500md", "w2000lg", "w2560lg"):
            v = o[k]
            self.assertGreater(v["slots"], 0, k)
            self.assertGreater(v["chips"], 100, k)
            self.assertEqual((v["cut"], v["over"]), (0, 0), k)            # ни одна плашка не обрезана, ячейка не переполнена
            self.assertEqual(v["orderBad"], [], k)                        # порядок протоколов одинаковый во всех строках
            self.assertEqual(v["names"], 0, k)                            # имена людей (и устройств в широких колонках) не обрезаны
            # «Средне» < 1500 (колонка 176, HANDOFF): длинное имя устройства — многоточием, целиком в подсказке; модель
            # рядом — многоточием раньше имени (эталон 23.10, А1: «Телефон Мамы  i…»)
            self.assertEqual(v["devCutNoTip"], 0, k)
            # владелец 20.10: метки — колонкой «Состояние», «В сети» — сразу за ней, одна X во всех строках; имя не «резиновое»
            self.assertEqual((v["xState"], v["xOnline"]), (0, 0), k)
            # «Крупно» с окна 1500 (решение координатора 23.10): колонка имени забирает свободную ширину под линию
            # активности второй строки — она и заполняет колонку; пустоту колонок проверяет test_wide_columns_by_content
            if k not in ("w2000lg", "w2560lg"):
                self.assertLessEqual(v["nameToState"], 160, k)
            # эталон 23.10 (States Graph, «≥ 1500»): таблица во всю ширину раздела — справа без пустоты на 2000 и 2560
            self.assertLessEqual(abs(v["tableW"] - v["barW"]), 1, k)
            self.assertEqual(v["tableL"], 0, k)
        self.assertEqual(self.r["nost"], {"nost": True, "w": 0, "chips": 0})   # меток нет — колонки нет

    def test_wide_columns_by_content(self):
        """Д2 вариант Б (владелец 28.10) вместо 01b: линия — вся колонка «Активность» (не уже 120: иначе столбцы
        сворачиваются сами); на 2560 свободное — и «Протоколам» (метки без «+N»); таблица не шире 2000; viewBox — пиксели."""
        o = self.r["order"]
        for k in ("w1440md", "w1180md", "w1500md", "w2000md", "w2560md", "w1440lg", "w2000lg", "w2560lg"):
            self.assertGreaterEqual(o[k]["wide"]["sparkW"], 120, k)
            self.assertEqual(o[k]["wide"]["vbBad"], 0, k)
        self.assertEqual(o["w2560md"]["more"], 0)                         # «Протоколы» — все метки, без «+N»
        for k in ("w2560md", "w2560lg"):
            self.assertLessEqual(o[k]["tableW"], 2000, k)

    def test_row_density_grid(self):
        r = self.r["rowgrid"]
        self.assertEqual(r["tpl"], 2)
        self.assertEqual(r["moved"], [])                                  # З-2: раскрытие не переносит людей между колонками
        self.assertTrue(r["colMajor"])                                    # эталон 23.10 (А3): колонками сверху вниз
        self.assertTrue(r["subInCol"])                                    # устройства — под своим человеком, в его колонке
        self.assertEqual(r["cut"], 0)
        self.assertTrue(r["dot"])
        for title, disp in r["dot"]:                                       # З-3: точка, слово — подсказкой
            self.assertTrue(re.match(r"(в сети|не в сети|выключено)", title), title)   # Д2 Б: «в сети с …» — тоже
            self.assertEqual(disp, "none")

    def test_filter_counts_add_up(self):
        # UX-аудит 20.10: В сети + Не в сети + Выключены = Все; число у фильтра = строк в списке.
        # nav2 r1 (UI-A1): фильтры — в подменю слева, ряда над списком нет; адрес и шапка — по фильтру
        c = self.r["counts"]
        self.assertEqual(c["online"] + c["offline"] + c["off"], c["all"])
        self.assertEqual(c["rows"], {"online": c["online"], "offline": c["offline"], "off": c["off"], "all": c["all"]})
        self.assertEqual(c["seg"], 0)
        self.assertEqual(c["addr"], {"online": "#people?show=on", "offline": "#people?show=off", "off": "#people?show=disabled", "all": "#people"})
        self.assertEqual(c["head"]["online"], ["Пользователи и устройства", "В сети"])
        self.assertEqual(c["head"]["all"], ["", "Пользователи и устройства"])     # на «Все» — просто раздел

    def test_filter_on_narrow(self):
        # З-4: «Не в сети», выбранный на компьютере, виден нажатым на планшете (ряд кнопок) и на кнопке «Показать»
        # телефона (nav2 r1); «Все» из листа его снимает
        self.assertEqual(self.r["narrow"]["addr"], "#people?show=off")
        seg = self.r["narrow1024"]["seg"]
        on = [x for x in seg if x.endswith(":true")]
        self.assertEqual(len(on), 1)
        self.assertTrue(on[0].startswith("Не в сети"))
        self.assertEqual(self.r["narrow390"]["seg"], [])
        self.assertTrue(self.r["narrow390"]["pick"].startswith("Показать ·Не в сети"), self.r["narrow390"]["pick"])
        self.assertTrue(self.r["narrow"]["afterAll"].startswith("Показать ·Все"), self.r["narrow"]["afterAll"])
        self.assertEqual(self.r["narrow"]["addrAll"], "#people")
        # UI-A1 × UI-B: к человеку в сети адресом #people?focus=…&show=on на телефоне — лист человека, фильтр в адресе
        # и на кнопке «Показать» остаётся
        n = self.r["narrow"]
        self.assertTrue(n.get("focusUid") and n.get("focusSheet"), n)
        self.assertEqual(n["focusAddr"], "#people?show=on", n)
        self.assertTrue(n["focusPick"].startswith("Показать ·В сети"), n)

    def test_focus_and_filter_desktop(self):
        """REV-3377-ui №1 (компьютер): человек из адреса под фильтром, который его прячет, — без ложного «уже нет»,
        фильтр «Все», фокус на его строке; смена фильтра снимает метку focus; окно по адресу оставляет show."""
        n = self.r["navfocus"]
        self.assertTrue(n["uid"], n)
        self.assertEqual(n["hidden"]["gone"], 0, n)
        self.assertTrue(n["hidden"]["focused"], n)
        self.assertEqual(n["hidden"]["addr"], "#people", n)
        self.assertTrue(n["hidden"]["cur"].startswith("Все"), n)
        self.assertEqual(n["pick"]["addr"], "#people?show=on", n)
        self.assertTrue(n["open"]["dlg"], n)
        self.assertEqual(n["open"]["gone"], 0, n)
        self.assertEqual(n["open"]["addr"], "#people?focus=%s&show=off" % n["uid"], n)

    def test_cols_click_and_memory(self):
        """Т3 №23: свёрнутый сам столбец по нажатию разворачивается, выбор в браузере не меняется; свёрнутый нажатием —
        помнится в kaskad-cols-people и после перезагрузки, второе нажатие — обратно."""
        c = self.r["ccClick"]
        self.assertTrue(c["auto0"]["route"], c)
        self.assertIsNone(c["auto0"]["ls"], c)
        self.assertFalse(c["auto1"]["route"], c)
        self.assertIsNone(c["auto1"]["ls"], c)
        # T-it1b Н-2: и «Протоколы» развёрнуты вручную — сворачивать нечего: линия не уже --cc-min (120), строка в таблице
        tg = c["tight"]
        self.assertTrue(tg["tight"], tg)
        self.assertGreaterEqual(tg["act"], 119, tg)
        self.assertLessEqual(tg["over"], 1, tg)
        # доводка круга 1: на шагах «A A A», в «Крупно» и EN имя не уже первого слова, видимые плашки — целиком
        # (без правки: имя «Гости» 40 из 44 px, в «Крупно» «WireGuard» обрезана), сжимается «Маршрут», строка в таблице
        # REV-it1-pol П-1: в строке с доступом видна хотя бы одна плашка (не только «+N»); не влезает — многоточие
        # (без правки: «Средне m» — у Мамы и Папы «+5»/«+3», «Крупно» — у всех строк); «Строка» — колонки «Протоколы»
        # нет (протоколы в раскрытии), там проверка — у видимых ячеек плашек, если они есть
        self.assertEqual(len(c["steps"]), 8, c["steps"])
        for k, s in c["steps"].items():
            row = k.endswith("-row")
            if not row:
                self.assertTrue(s["tight"], (k, s))
            for r in s["rows"]:
                self.assertEqual(r["chipCut"], [], (k, r))
                if r["slots"] and r["access"]:
                    self.assertGreaterEqual(r["vis"], 1, (k, r))
                if row:
                    continue
                self.assertTrue(r["word1"], (k, r))
                self.assertTrue(r["slots"] or not r["access"], (k, r))
                self.assertGreaterEqual(r["act"], 119, (k, r))
                self.assertLessEqual(r["over"], 1, (k, r))
                self.assertLessEqual(r["route"], r["proto"], (k, r))
        self.assertEqual([c[k]["act"] for k in ("wide0", "wide1", "wide2", "wide3")], [False, True, True, False], c)
        self.assertEqual([c[k]["ls"] for k in ("wide1", "wide2", "wide3")], ['["act"]', '["act"]', "[]"], c)

    def test_font_step(self):
        """Т3 №17: «A A A» — радиогруппа в шапке; «крупнее» — html 18 px, имя 16 → 18, «⌘K» скрыт; выбор — kaskad-font
        в браузере, «Вид» узла не меняется; после перезагрузки тот же; стрелка влево — «обычный», фокус на нём."""
        f = self.r["font"]
        m0, l1, l2, m3 = f["m0"], f["l1"], f["l2"], f["m3"]
        self.assertEqual((m0["fs"], m0["html"], m0["name"], m0["ls"], m0["on"], m0["role"], m0["kbd"], m0["ph"]),
                         ("m", "16px", "16px", None, "s:false m:true l:false", "radiogroup", True, "Поиск по панели"), f)
        self.assertEqual((l1["fs"], l1["html"], l1["name"], l1["ls"], l1["on"], l1["kbd"]), ("l", "18px", "18px", "l", "s:false m:false l:true", False), f)
        self.assertEqual(l1["look"], m0["look"], f)                                  # «Вид» (на узел) не тронут
        self.assertEqual((l2["fs"], l2["html"], l2["on"]), ("l", "18px", "s:false m:false l:true"), f)
        self.assertEqual((m3["fs"], m3["html"], m3["ls"], f["focus"]), ("m", "16px", "m", "m"), f)

    def test_medium_on_1100(self):
        """REV-3377-ui №2 по Д2 вариант Б: линия активности не уже 120 — на узкой таблице «Маршрут», затем «Протоколы»
        сворачиваются сами (полоска 24, значение — в подсказке); заголовок «Протоколы» — целиком, когда столбец развёрнут."""
        m = self.r["narrowMd"]
        for w in ("1100", "1280", "1366"):
            x = m[w]
            self.assertIn("kk-dn--md", x["dn"], (w, x))
            self.assertFalse(x["protoCut"], (w, x))
            self.assertEqual(x["mutCut"], 0, (w, x))
            if not x["ccProto"]:
                self.assertGreater(x["rows"], 0, (w, x))
                self.assertEqual(x["noChip"], 0, (w, x))
        self.assertLess(m["1100"]["tableW"], 700, m)
        self.assertEqual([m[w]["route"] for w in ("1100", "1280", "1366")], [False, False, False], m)   # таблица < 870 с метками у имени — свёрнут сам
        self.assertEqual([m[w]["ccProto"] for w in ("1100", "1280", "1366")], [True, False, False], m)

    def test_screen_reader_and_reasons(self):
        a = self.r["aria"]
        # REV-3377-ui (было до): заглушка с still=1 — такт сборщика, где без него «в сети» переключилось бы у всех,
        # подпись не меняет
        self.assertTrue(self.r["ariaTick"])
        self.assertTrue(a["row"].startswith("Мама, в сети 2 из 2"))      # З-7: строка — имя и состояние
        self.assertTrue(a["give"].startswith("Выдать настройки — Мама"))
        self.assertEqual(a["more"], "Ещё действия — Мама")
        self.assertTrue(a["dev"].startswith("Макбук Егор, macOS"))
        self.assertIn("пользователь на паузе", a["devSw"])                    # П-3: причина недоступного переключателя
        self.assertEqual(a["darya"], ["все выключены", True])     # все устройства выключены — метка и приглушённо
        ln = self.r["longname"]
        self.assertEqual(ln["chip"], "пауза")                             # Д2 Б: метка у имени (колонки «Состояние» нет)
        self.assertTrue(ln["chipFull"])
        self.assertGreater(ln["nameW"], 100)                             # имя рядом не исчезает

    def test_states(self):
        self.assertEqual(self.r["problem"], {"chip": "пауза", "offRow": True, "sw": "false"})
        self.assertTrue(self.r["focus"]["s"]["open"])
        self.assertEqual(self.r["focus"]["active"], "d-3c3c0003")
        self.assertEqual(self.r["empty"], {"empty": True, "barHidden": True, "btn": ["Добавить первого пользователя"]})
        self.assertEqual(self.r["en"]["head"], ["User", "Activity · day", "Online", "Protocols", "Route"])   # Д2 Б: без «Status», без «Actions»
        self.assertEqual(self.r["en"]["dn"], ["Large", "Medium", "Row"])
        for s in self.r["en"]["since"]:
            self.assertNotRegex(s, r"\d{4}")                              # «since Oct 19» — без года


if __name__ == "__main__":
    unittest.main()

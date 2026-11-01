"""Круг 1, Т3 — «Выдать настройки» одним окном (Д2 Б r3, кадры О5 / О6; владелец 24.10 — подписка первой):
вкладки «Подписка» (рекомендуем, открыта сразу) и «Статический ключ» (give.js); устройство выбирается в окне и не
сбрасывается сменой вкладки; адрес подписки — секрет, на экране только после «QR-код» / «Скопировать».
О5 / t4 (R-it1-fix): заголовок «Как подключить «<устройство>»?» с подстрокой «<пользователь> · N устройств · протоколы»;
во вкладке «Подписка» — ряд «Платформа» (по умолчанию — ОС устройства), строка «Протокол», список приложений подписки
тем же блоком give.js (o.only = 'apps'; данные — userpage.admin_sub_doc «apps» ← install_apps.sub_blocks ← каталог
``subscription``), подвал с пометкой «показывайте только самому пользователю». В Chrome —
tests/test_ui4_people.py::test_give_in_one_press."""
import json
import os
import re
import unittest
from unittest import mock

from kaskad.gw import install_apps, issue_os, model
from kaskad.i18n import use_lang
from tests.test_332f_give import CYR, HAS_NODE, prelude, read, run_node

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GiveWindowTest(unittest.TestCase):
    def setUp(self):
        self.app = read("app.js")
        self.body = self.app[self.app.index("function openGive(u, devs, o = {})"):self.app.index("  function ppGiveBtns(u) {")]

    def test_one_window_tabs(self):
        self.assertNotIn("function qrPick(", self.app)                         # окна выбора перед окном больше нет
        self.assertIn('role: "tablist"', self.body)
        self.assertIn('tabBtn("sub", "arrow-rotate-right", t("qp.sub")', self.body)
        self.assertIn('tabBtn("static", "key", t("qp.static")', self.body)
        self.assertIn('if (subOk && o.tab !== "static") st.tab = "sub";', self.body)   # подписка — сразу, если ей есть что отдать
        # плашка протокола и лист устройства — то же окно на вкладке ключа
        self.assertIn('return openGive(findUser(dev.user), [dev], { dev, tab: "static", kind: kind || "" });', self.app)

    def test_device_kept_across_tabs(self):
        # выбор устройства — в окне; вкладка рисуется по st.dev, смена вкладки st.dev не трогает
        self.assertIn('cur = tab === "sub" ? subView(u, devs, d, st) : issueView(st.dev, st.kind, d);', self.body)
        # другое устройство: шапка за ним, платформа подписки — снова по его ОС
        self.assertIn("st.dev = x; st.kind = \"\"; st.subOs = \"\"; head(); chips(); show(st.tab, true);", self.body)

    def test_link_hidden_until_asked(self):
        sub = self.app[self.app.index("function subView(u, devs, d, st)"):self.app.index("  function subApps(st, onApp)")]
        # заглушка в рамке (.kk-subk), значок — как у вкладки «Подписка» (макет О5)
        self.assertIn('el("div", { class: "kk-subk" }, icon("arrow-rotate-right", 24), el("span", { text: t("qp.hidden") }))', sub)
        self.assertIn('const urlOf = () => (p && p.enabled && p.url ? String(p.url) : "");', sub)
        # адрес рисуется только из draw(), а draw показывает его после нажатия (qrOn) или при известном адресе
        self.assertIn('API.sub.act(u.id, "enable")', sub)                       # тот же POST, что «Включить подписку»
        # REV-it1-b С-1: известный адрес не идёт на экран без нажатия — флаг shown ставят только «QR-код» и «Скопировать»
        self.assertIn('} else if (url && shown) {', sub)
        self.assertEqual(sub.count("shown = true;"), 3, sub)
        self.assertNotIn('innerHTML', sub)

    def test_sub_window_link_hidden(self):
        """Доводка круга 1, п.3: окно «Ссылка для приложений» — адрес с токеном только после «QR-код» / «Скопировать»
        (С-1), приложения — общим блоком subApps (не словами sb.what)."""
        w = self.app[self.app.index("function openSub1(userId)"):self.app.index("  // ── 18. Выдача настроек")]
        self.assertIn("const A = subApps(st, () => {});", w)
        self.assertIn("if (shown) {", w)
        self.assertIn("if (r && r.ok) { shown = false; qrOn = false; }", w)          # REV-it1-pol З-2
        self.assertIn("if (qrOn) out.push(", w)
        self.assertEqual(w.count("shown = true;"), 2, w)
        self.assertNotIn('t("sb.what")', w)
        self.assertIn('t("sb.whatS")', w)

    def test_texts_and_catalog(self):
        ru, en = read("i18n-ru.js"), read("i18n-en.js")
        for k in ("qp.how", "qp.dev", "qp.qr", "qp.hidden", "qp.app", "qp.allIn", "qp.on", "qp.s1", "qp.s2", "qp.s3"):
            self.assertIn('"%s": ' % k, ru)
            self.assertIn('"%s": ' % k, en)
        self.assertIn('"qp.hidden": "Адрес подписки появится после нажатия «QR-код» или «Скопировать»",', ru)
        # приложения подписки — из каталога (subscription, 7058bc90), не из текста; своих шагов подписки у приложений
        # каталога пока нет (sub_steps) — колонка 3 говорит общие шаги без выдуманных кнопок
        with open(os.path.join(ROOT, "lib", "kaskad", "gw", "install_apps.json"), encoding="utf-8") as f:
            cat = json.load(f)
        self.assertIsInstance(cat["subscription"], dict)
        flags = sorted({k for a in cat["apps"].values() if isinstance(a, dict) for k in a if "sub" in k.lower()})
        self.assertIn(flags, ([], ["sub_steps"]))

    def test_layout_three_columns(self):
        css = read("people.css")
        self.assertIn(".kk-iss__body--sub { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, .8fr) minmax(0, .9fr);", css)
        self.assertIn('[data-device="phone"] .kk-iss__body--sub { grid-template-columns: minmax(0, 1fr); gap: 16px; }', css)


class HeadFootPlatformTest(unittest.TestCase):
    """О5 / t4: шапка окна за устройством, ряд «Платформа» и строка «Протокол», подвал вкладки «Подписка»."""

    def setUp(self):
        self.app = read("app.js")
        self.body = self.app[self.app.index("function openGive(u, devs, o = {})"):self.app.index("  function ppGiveBtns(u) {")]
        self.sub = self.app[self.app.index("function subView(u, devs, d, st)"):self.app.index("  function ppGiveBtns(u) {")]
        self.ru, self.en = read("i18n-ru.js"), read("i18n-en.js")

    def test_title_follows_the_device(self):
        self.assertIn('openDialog({ title: giveTitle(st.dev), sub: giveSub(u, st.dev), icon: "qr-code"', self.body)
        self.assertNotIn('t("pp.k1.giveOf"', self.body)                      # «Выдать настройки — …» — только у кнопки
        self.assertIn('const giveTitle = (dev) => t("qp.title", { name: dev.name });', self.app)
        g = self.app[self.app.index("function giveSub(u, dev)"):]
        g = g[:g.index("\n  }\n")]
        self.assertIn('tn("pp.devCount", full.devices.length)', g)         # формы числа — словарь (1 устройство, 2 устройства)
        self.assertIn("findUser(u.id)", g)                                   # пользователь целиком, не строка под фильтром
        self.assertIn("protoSort(arr(dev.access)).map(inboundTitle)", g)
        self.assertIn('"qp.title": "Как подключить «{name}»?",', self.ru)
        self.assertIn('"qp.title": "How to connect “{name}”?",', self.en)
        self.assertIn('"pp.devCount": "{n} устройство|{n} устройства|{n} устройств",', self.ru)
        self.assertIn('"pp.devCount": "{n} device|{n} devices",', self.en)

    def test_sub_footer(self):
        self.assertIn('if (tab === "sub") d.setFoot([el("p", { class: "gs-foot" }, icon("lock", 18), el("span", { text: t("qp.footSub") })),', self.body)
        self.assertIn('"qp.footSub": "Ссылка подписки — показывайте только самому пользователю.",', self.ru)
        self.assertIn('"qp.footSub": "This is a subscription link — show it only to the user.",', self.en)

    def test_platform_row_protocol_line_and_list(self):
        self.assertIn('el("div", { class: "kk-iss__plats", role: "radiogroup", "aria-label": t("pp.platform") })', self.sub)
        self.assertIn("subPlatOf(st.dev && st.dev.platform, have)", self.sub)   # по умолчанию — ОС устройства
        self.assertIn('el("span", { text: t("qp.proto") }), el("p", { class: "kk-iss__all", text: t("qp.allIn"', self.sub)
        # список приложений — тот же блок give.js, что «Установите приложение» ключа; данные — ответ сервера
        self.assertIn('G.mount({ viewer: "admin", lang: LANG, only: "apps", iconBase: "static/"', self.sub)
        self.assertIn('g.set({ kind: "sub", os, install: apps[os] });', self.sub)
        self.assertIn("apps = x && isObj(x.apps) ? x.apps : {};", self.sub)
        self.assertEqual(self.sub.count('t("sb.what")'), 1)                    # прежний текст — только запасной
        self.assertIn('"qp.allIn": "все протоколы «{name}» в одной ссылке: {list}",', self.ru)
        self.assertIn('"qp.allIn": "all protocols of “{name}” in one link: {list}",', self.en)
        for k in ("qp.proto", "pp.platform"):
            self.assertIn('"%s": ' % k, self.ru)
            self.assertIn('"%s": ' % k, self.en)
        # колонка 3: свои шаги приложения (каталог sub_steps), иначе общие
        self.assertIn('(own.length ? own : [t("qp.s1"), t("qp.s2"), t("qp.s3", { h })])', self.sub)
        css = read("people.css")
        self.assertIn('.kk-iss__plats > button[aria-checked="true"]', css)
        self.assertIn(".kk-iss__pick[hidden] { display: none; }", css)

    def test_platform_translation_is_that_of_the_key(self):
        m = re.search(r"const PLAT_ALIAS = \{([^}]*)\};", self.app)
        self.assertEqual(dict(re.findall(r'"?([\w-]+)"?: "(\w+)"', m.group(1))), issue_os.ALIASES)

    @unittest.skipUnless(HAS_NODE, "нет node")
    def test_platform_of_device(self):
        a = self.app
        fn = a[a.index("  const PLAT_ALIAS = "):a.index("  // Шапка окна (Д2 Б r3")]
        have = ["ios", "android", "androidtv", "windows", "macos", "linux", "other"]
        js = fn + "\nconst have = %s;\nconsole.log(JSON.stringify([subPlatOf('ios', have), subPlatOf(' iPhone ', have), " \
            "subPlatOf('android_tv', have), subPlatOf('keenetic', have), subPlatOf('', have), subPlatOf(null, have), " \
            "subPlatOf('weird', have), subPlatOf('keenetic', ['android', 'ios']), subPlatOf('ios', [])]));" % json.dumps(have)
        self.assertEqual(run_node(js), ["ios", "ios", "androidtv", "other", "other", "other", "other", "android", ""])

    def test_sub_platforms_are_those_of_the_catalog(self):
        """Д-2: SUB_PLATS (устройство окна по умолчанию) — платформы «subscription» каталога."""
        m = re.search(r"const SUB_PLATS = (\[[^\]]*\]);", self.app)
        self.assertEqual(sorted(json.loads(m.group(1))), sorted(install_apps.catalog()["subscription"]))
        self.assertIn("o.dev && devs.indexOf(o.dev) >= 0 ? o.dev : giveDevOf(devs)", self.app)

    @unittest.skipUnless(HAS_NODE, "нет node")
    def test_default_device(self):
        """Д-2: без o.dev — первое включённое устройство с платформой подписки; нет таких — первое."""
        a = self.app
        fn = a[a.index("  const PLAT_ALIAS = "):a.index("  // Шапка окна (Д2 Б r3")]
        d = lambda n, p, on=True: {"name": n, "platform": p, "enabled": on}
        cases = [[d("Роутер", "keenetic"), d("Телефон", "ios")],
                 [d("Роутер", "keenetic"), d("Выкл", "android", False), d("Ноут", "Mac")],
                 [d("Роутер", "keenetic"), d("Микротик", "routeros")],
                 [d("Роутер", "keenetic"), d("Без платформы", None), d("Другое", "other")]]
        js = fn + "\nconsole.log(JSON.stringify(%s.map((ds) => giveDevOf(ds).name)));" % json.dumps(cases, ensure_ascii=False)
        self.assertEqual(run_node(js), ["Телефон", "Ноут", "Роутер", "Другое"])


class SubAppsServerTest(unittest.TestCase):
    """install_apps.sub_block / sub_blocks — приложения подписки по платформам: те же строки, что у ключа VLESS."""

    def test_blocks_by_platform(self):
        cat = install_apps.catalog()["subscription"]
        with use_lang("ru"):
            b = install_apps.sub_blocks()
            self.assertEqual(list(b), [p for p in issue_os.PLATFORMS if p in cat])     # порядок платформ, без роутеров
            for p, blk in b.items():
                ids = [a["id"] for a in blk["apps"]]
                self.assertEqual(ids, [x for x in cat[p] if x in ids], p)             # порядок каталога
                self.assertTrue(ids, p)
                self.assertEqual([a["recommended"] for a in blk["apps"]], [True] + [False] * (len(ids) - 1), p)
                key = {a["id"]: a for a in install_apps.block("vless", p)["apps"]}
                for a in blk["apps"]:                                                  # строка — та же, что у ключа
                    same = lambda x: {k: v for k, v in x.items() if k != "recommended"}
                    self.assertEqual(same(a), same(key[a["id"]]), (p, a["id"]))
                    self.assertNotIn("steps", a)
            self.assertTrue(any(a["fakes"] for a in b["ios"]["apps"]))                # пометка о подделках доходит
        with use_lang("en"):
            self.assertIsNone(CYR.search(json.dumps(install_apps.sub_blocks(), ensure_ascii=False)))

    def test_platform_of_the_call(self):
        self.assertEqual(install_apps.sub_block("iPhone")["apps"][0]["id"], install_apps.sub_block("ios")["apps"][0]["id"])
        self.assertFalse(install_apps.sub_block("keenetic")["supported"])            # роутеры подписку не берут
        self.assertEqual([a["id"] for a in install_apps.sub_block("weird")["apps"]],
                         [a["id"] for a in install_apps.sub_block("other")["apps"]])

    def test_own_steps_only_from_the_catalog(self):
        doc = json.loads(json.dumps(install_apps.catalog()))
        doc["apps"]["hiddify"]["sub_steps"] = {"other": [{"ru": "Шаг Hiddify", "en": "Hiddify step"}, {"ru": ""}]}
        with mock.patch.dict(install_apps._cache, {"doc": doc}):
            with use_lang("ru"):
                apps = {a["id"]: a for a in install_apps.sub_block("other")["apps"]}
                self.assertEqual(apps["hiddify"]["steps"], ["Шаг Hiddify"])
                self.assertNotIn("steps", apps["v2rayn"])
                self.assertNotIn("steps", {a["id"]: a for a in install_apps.sub_block("windows")["apps"]}["hiddify"])
            with use_lang("en"):
                self.assertEqual({a["id"]: a for a in install_apps.sub_block("other")["apps"]}["hiddify"]["steps"], ["Hiddify step"])

    def test_status_and_mock_carry_them(self):
        with open(os.path.join(ROOT, "lib", "kaskad", "panel", "userpage.py"), encoding="utf-8") as f:
            up = f.read()
        self.assertIn("return dict(sub_status(ref), apps=install_apps.sub_blocks())", up)   # Д-1: только панель
        self.assertEqual(up.count("out = admin_sub_doc(uid)"), 2)                              # GET и POST /sub
        self.assertIn("apps: window.KaskadMockSubApps ? window.KaskadMockSubApps(document.documentElement.lang) : {},", read("dev-mock.js"))
        self.assertIn("g.KaskadMockSubApps = function (lang)", read("dev-mock-install.js"))


@unittest.skipUnless(HAS_NODE, "нет node")
class AppsOnlyTest(unittest.TestCase):
    """give.js o.only = 'apps' (вкладка «Подписка»): только список — строки ключа, «ссылка подписки», «рекомендуем»,
    пометки, «QR · установить»; ключа, шагов и пути нет; выбранное приложение — onRender; телефон — «Открыть в …»."""

    def run_apps(self, p, lang="ru", phone=False):
        with use_lang(lang):
            blk = install_apps.sub_block(p)
        js = prelude(lang) + r"""
const out = {}, seen = [];
PHONE = %s;
const g = G.mount({ viewer: "admin", lang: %s, only: "apps", drawLinkQr: () => document.createElement("b"),
  onRender: (vm) => seen.push(vm.app ? vm.app.id : null) });
document.body.appendChild(g.el);
g.set({ kind: "sub", os: %s, install: %s });
const root = g.el, ids = %s;
out.aria = root.querySelectorAll('[role="radio"]').map((r) => r.getAttribute("aria-label"));
out.rec = root.querySelectorAll(".gs-rec").length;
out.extra = root.querySelectorAll(".gs-qr").length + root.querySelectorAll(".gs-step__h").length + root.querySelectorAll(".gs-step3").length + root.querySelectorAll(".gs-key").length;
out.store = root.querySelectorAll('[data-gk^="store:"]').length;
out.line = root.querySelectorAll(".gs-app--line").length;
out.fmtRow = root.querySelectorAll(".gs-app__f").length;
out.fakes = root.querySelectorAll(".gs-fakes").map((x) => x.textContent);
out.open = !!root.querySelector(".gs-open");
out.tri = root.querySelectorAll(".gs-tri").length + root.querySelectorAll(".gs-tipw").length;
out.notes = root.querySelectorAll(".gs-app__nt-t").map((x) => [x.textContent, x.getAttribute("title")]);
out.noteLinks = root.querySelectorAll(".gs-app__nt-a").map((x) => [x.textContent, x.getAttribute("href"), x.getAttribute("target"), x.getAttribute("rel")]);
const tipIds = ids.filter((x) => gk(root, "tip:app-" + x));
out.tips = tipIds;
out.tipTexts = tipIds.map((x) => { fire(gk(root, "tip:app-" + x), "click"); const t = (root.querySelector(".gs-tip") || { textContent: "" }).textContent; fire(document.body, "click"); return t; });
fire(document.body, "click");
fire(gk(root, "app:" + ids[1]), "click");
out.checked = gk(root, "app:" + ids[1]).getAttribute("aria-checked");
out.seen = seen.slice(-1);
const vm = g.model();
out.vm = { key: vm.key, path: vm.path, keyName: vm.keyName, fmt: vm.apps.map((a) => a.fmtWord + "|" + a.ext) };
out.text = root.textContent;
console.log(JSON.stringify(out));
""" % ("true" if phone else "false", json.dumps(lang), json.dumps(p), json.dumps(blk), json.dumps([a["id"] for a in blk["apps"]]))
        return blk, run_node(js)

    def test_list_of_ios(self):
        blk, got = self.run_apps("ios")
        names = [a["name"] for a in blk["apps"]]
        self.assertEqual(got["aria"], ["%s%s, ссылка подписки" % (n, ", рекомендуем" if i == 0 else "") for i, n in enumerate(names)])
        self.assertEqual(got["rec"], 1)
        self.assertEqual(got["extra"], 0)                                        # ни ключа, ни шагов, ни пути
        self.assertEqual(got["store"], len(names))                               # «QR · установить» у каждой строки
        self.assertFalse(got["open"])
        self.assertEqual(got["vm"], {"key": None, "path": None, "keyName": "", "fmt": ["ссылка подписки|"] * len(names)})
        # T-it1b п.4: на ПК строка в одну линию, без подписи «ссылка подписки»
        self.assertEqual((got["line"], got["fmtRow"]), (len(names), 0), got)
        with use_lang("ru"):
            fakes = [a for a in blk["apps"] if a["fakes"]]
            self.assertTrue(fakes)
            # T-it1b п.5: «есть подделки» — одной строкой над списком, в треугольниках строк её нет; свои пометки строк
            # (платно, «нет в российском App Store») — остаются у строки
            self.assertEqual(got["fakes"], ["В магазинах есть подделки с похожими названиями — ставьте приложения только по ссылкам отсюда"], got)
            self.assertFalse(any(fakes[0]["fakes"] in x for x in got["tipTexts"]), got)
            # доводка круга 1 (решение координатора): в строках треугольника нет — справочные пометки («Бесплатно…»,
            # «Платно…», «Нет в российском App Store…», «не проверено») серой строкой, весь текст — в title
            self.assertEqual((got["tips"], got["tri"]), ([], 0), got)
            want = [" · ".join(x for x in (a["note"], a["unverified"]) if x) for a in blk["apps"]]
            self.assertEqual(got["notes"], [[x, x] for x in want if x], got)
            self.assertTrue(any(x[0].startswith("Бесплатно, открытый код") for x in got["notes"]), got)
            self.assertTrue(any(x[0].startswith("Платно") for x in got["notes"]), got)
            self.assertTrue(any(x[0].startswith("Нет в российском App Store") for x in got["notes"]), got)
            self.assertFalse(any(fakes[0]["fakes"] in x[0] for x in got["notes"]), got)   # подделки — только над списком
        self.assertEqual((got["checked"], got["seen"]), ("true", [blk["apps"][1]["id"]]))

    def test_phone_and_english(self):
        blk, got = self.run_apps("android", lang="en", phone=True)
        self.assertEqual(got["store"], 0)                                        # телефон: кнопок магазина в строках нет —
        self.assertTrue(got["open"])                                             # «Open in …» выбранного сверху
        self.assertEqual(got["vm"]["fmt"][0], "subscription link|")
        self.assertEqual(got["line"], 0)                                         # телефон: строки как были
        self.assertEqual(got["fakes"], ["The stores have fakes with similar names — install the apps only via the links here"], got)
        self.assertIsNone(CYR.search(got["text"]), got["text"])
        # без треугольников и на телефоне; вторая ссылка приложения (Hiddify: Google Play и GitHub) — словом в серой строке
        self.assertEqual((got["tips"], got["tri"]), ([], 0), got)
        more = [l for a in blk["apps"] for l in a["links"][1:]]
        self.assertTrue(more, blk)
        self.assertEqual([x[1] for x in got["noteLinks"]], [l["url"] for l in more], got)
        self.assertTrue(all(x[2] == "_blank" and x[3] == "noopener noreferrer" for x in got["noteLinks"]), got)



@unittest.skipUnless(HAS_NODE, "нет node")
class KeyAppsNoTriangleTest(unittest.TestCase):
    """Круг 1, R-it1-tri: список «Установите приложение» ключа — вкладка «Статический ключ» (viewer admin), /p/
    (person) и шаг 6 мастера (wizard) — тот же give.js appRow, что у подписки: треугольника в строке нет, пометки
    каталога серой строкой (весь текст — в title), формат приложения остался, подделки — одной строкой над списком."""

    def run_key(self, kind, p, viewer, lang="ru", phone=False):
        from tests.test_332f_give import issuance
        r = issuance(kind, p, lang=lang)
        js = prelude(lang) + r"""
const out = {};
PHONE = %s;
const g = G.mount({ viewer: %s, lang: %s, selectors: %s, device: { name: "qa" }, onPlatform: () => {}, onProto: () => {},
  drawQr: () => document.createElement("i"), drawLinkQr: () => document.createElement("b") });
document.body.appendChild(g.el);
g.set(%s);
const root = g.el, list = root.querySelector(".gs-apps");
const rows = root.querySelectorAll(".gs-app");
out.rows = rows.length;
out.rowTri = rows.map((a) => a.querySelectorAll(".gs-tri").length + a.querySelectorAll(".gs-tipw").length).reduce((x, y) => x + y, 0);
out.appTips = root.all((n) => n.getAttribute && /^tip:app-/.test(n.getAttribute("data-gk") || "")).length;
out.notes = rows.map((a) => { const n = a.querySelector(".gs-app__nt-t"); return n ? [n.textContent, n.getAttribute("title")] : null; });
out.links = root.querySelectorAll(".gs-app__nt-a").map((x) => [x.getAttribute("href"), x.getAttribute("target"), x.getAttribute("rel")]);
out.fmt = root.querySelectorAll(".gs-app__f").length;
out.fakes = root.querySelectorAll(".gs-fakes").map((x) => x.textContent);
const sib = list && list.parentNode ? list.parentNode.children : [];
out.fakesBefore = (() => { const f = root.querySelector(".gs-fakes"); return !f || (f.parentNode === list.parentNode && sib.indexOf(f) === sib.indexOf(list) - 1); })();
out.text = root.textContent;
console.log(JSON.stringify(out));
""" % ("true" if phone else "false", json.dumps(viewer), json.dumps(lang), "true" if viewer == "admin" else "false", json.dumps(r))
        return r, run_node(js)

    def check(self, kind, p, viewer, lang="ru", phone=False):
        r, got = self.run_key(kind, p, viewer, lang, phone)
        apps = r["install"]["apps"]
        self.assertEqual(got["rows"], len(apps), got)
        self.assertEqual((got["rowTri"], got["appTips"]), (0, 0), (kind, p, viewer, got))
        self.assertEqual(got["fmt"], len(apps), got)                                 # формат приложения на месте
        want = []
        for a in apps:
            parts = [x for x in (a.get("note"), a.get("unverified")) if x]
            parts += ["%s: %s" % (l.get("label", ""), l["note"]) for l in a.get("links", [])[1:] if l.get("note")]
            t = " · ".join(parts)
            want.append([t, t] if t else None)
        self.assertEqual(got["notes"], want, got)
        more = [l["url"] for a in apps for l in a.get("links", [])[1:]]
        self.assertEqual([x[0] for x in got["links"]], more, got)
        self.assertTrue(all(x[1:] == ["_blank", "noopener noreferrer"] for x in got["links"]), got)
        fk = [a["fakes"] for a in apps if a.get("fakes")]
        all_ = {"ru": "В магазинах есть подделки с похожими названиями — ставьте приложения только по ссылкам отсюда",
                "en": "The stores have fakes with similar names — install the apps only via the links here"}[lang]
        self.assertEqual(got["fakes"], [all_] if fk else [], got)
        self.assertTrue(got["fakesBefore"], got)
        for f in fk:                                                                 # у строк подделок нет
            self.assertFalse(any(n and f in n[0] for n in got["notes"]), (f, got))
        if lang == "en":
            self.assertIsNone(CYR.search(got["text"]), got["text"])
        return got

    def test_static_key_panel_ru(self):
        got = self.check("vless", "ios", "admin")
        self.assertTrue(any(n and n[0].startswith("Платно") for n in got["notes"]), got)
        got = self.check("awg3", "ios", "admin")
        self.assertTrue(got["fakes"], got)

    def test_userpage_person_ru_and_en_phone(self):
        self.check("vless", "android", "person")
        self.check("vless", "ios", "person", lang="en", phone=True)
        self.check("awg3", "android", "person", lang="en", phone=True)

    def test_wizard(self):
        self.check("awg3", "ios", "wizard")
        self.check("vless", "windows", "wizard", lang="en")
        self.check("wg", "android", "wizard", phone=True)


@unittest.skipUnless(HAS_NODE, "нет node")
class PairNoteTest(unittest.TestCase):
    """Решение координатора 29.10 по R-it1-tri (вопрос 1): у шага «Установите приложение» треугольника пометки пары
    нет. Две пометки пары iPhone, которые повторяли другие строки, сняты из каталога: VLESS — строку «подделки» над
    списком, AmneziaWG 3.1 — серую пометку «не проверено» у incy. Остальные пометки пары — серым текстом под
    заголовком (условие, пометка, «не проверено»), ссылка на инструкцию пары — там же. Строка «подделки» со значком
    остаётся (принятый образец). Треугольник шага роутера «Подготовьте роутер» — другое место, он остаётся."""

    def run_pair(self, kind, p, viewer="admin", lang="ru", phone=False):
        from tests.test_332f_give import issuance
        js = prelude(lang) + r"""
const out = {};
PHONE = %s;
const g = G.mount({ viewer: %s, lang: %s, selectors: true, device: { name: "qa" }, onPlatform: () => {}, onProto: () => {},
  drawQr: () => document.createElement("i"), drawLinkQr: () => document.createElement("b") });
document.body.appendChild(g.el);
g.set(%s);
const root = g.el, step = root.querySelector(".gs-col--l");
out.tips = root.all((n) => n.getAttribute && /^tip:/.test(n.getAttribute("data-gk") || "")).length;
out.tri = step ? step.querySelectorAll(".gs-tri").length : null;
const pr = root.querySelectorAll(".gs-pair");
out.pair = pr.map((x) => [x.querySelectorAll(".gs-pair__p").map((y) => y.textContent), x.querySelectorAll(".gs-more").map((a) => [a.getAttribute("href"),
  a.getAttribute("target"), a.getAttribute("rel"), a.textContent])]);
const kids = step ? step.children : [];
out.order = kids.map((k) => (k.className || "").split(" ")[0]);
out.fakes = root.querySelectorAll(".gs-fakes").map((x) => [x.textContent]);
console.log(JSON.stringify(out));
""" % ("true" if phone else "false", json.dumps(viewer), json.dumps(lang), json.dumps(issuance(kind, p, lang=lang)))
        return run_node(js)

    def test_the_two_iphone_notes_are_gone_from_the_catalog(self):
        cat = install_apps.catalog()["support"]["ios"]
        row = dict((x["proto"], x) for x in cat)
        self.assertNotIn("note", row["vless"])                       # без правки: «Ставьте только по ссылке ниже…»
        self.assertNotIn("note", row["awg3"])                        # без правки: «…; incy — не проверено»
        self.assertIn("guide", row["awg3"])                          # инструкция пары осталась
        for lang in ("ru", "en"):
            with use_lang(lang):
                for kind in ("vless", "awg3"):
                    hints = install_apps.support(kind, "ios")["hints"]
                    self.assertFalse(any("Happ, Incy" in h or "incy —" in h for h in hints), (kind, lang, hints))
                self.assertTrue(any("no37" in h for h in install_apps.support("awg3", "ios")["hints"]))

    def test_pair_note_does_not_repeat_the_grey_incy_mark(self):
        """Android × AWG 3.1: «…; incy — не проверено» в пометке пары повторяла серую пометку у самого incy — снята,
        как у iPhone (4d5c3546). И впредь: где incy в списке с серой «не проверено», пометка пары её не повторяет."""
        row = dict((x["proto"], x) for x in install_apps.catalog()["support"]["android"])["awg3"]
        self.assertEqual(row["note"], {"ru": "AmneziaVPN, AmneziaWG или WG Tunnel (5.6.0+)",
                                       "en": "AmneziaVPN, AmneziaWG or WG Tunnel (5.6.0+)"})
        self.assertIn("guide", row)                                  # инструкция пары осталась
        seen = 0
        for lang in ("ru", "en"):
            with use_lang(lang):
                for kind in sorted(issue_os.TITLES):
                    for p in model.PLATFORMS:
                        b = install_apps.block(kind, p)
                        if not b["supported"] or not any(a["id"] == "incy" and a["unverified"] for a in b["apps"]):
                            continue
                        seen += 1
                        hints = install_apps.support(kind, p)["hints"]
                        self.assertFalse(any(re.search(r"(?i)incy\W+(не проверено|not verified)", h) for h in hints),
                                         (kind, p, lang, hints))
        self.assertGreater(seen, 10)                                 # обход не пустой: incy с серой пометкой есть
        with use_lang("ru"):                                         # серая пометка у самого incy — на месте
            incy = [a for a in install_apps.block("awg3", "android")["apps"] if a["id"] == "incy"]
            self.assertEqual(len(incy), 1)
            self.assertTrue(incy[0]["unverified"])

    def test_iphone_vless_and_awg3(self):
        got = self.run_pair("vless", "ios")
        self.assertEqual((got["tips"], got["tri"], got["pair"]), (0, 0, []), got)
        self.assertEqual([x[0] for x in got["fakes"]],               # подделки — одна строка над списком
                         ["В магазинах есть подделки с похожими названиями — ставьте приложения только по ссылкам отсюда"])
        got = self.run_pair("awg3", "ios")
        self.assertEqual((got["tips"], got["tri"]), (0, 0), got)
        self.assertEqual(got["pair"], [[[], [["https://anten-ka.com/guides/no37/", "_blank", "noopener noreferrer",
                                             "Наша инструкция ↗"]]]], got)

    def test_a_note_still_needed_is_grey_under_the_heading(self):
        for lang, text in (("ru", "AmneziaWG из Mac App Store; DefaultVPN для Mac нет"),
                           ("en", "AmneziaWG from the Mac App Store; there is no DefaultVPN for Mac")):
            got = self.run_pair("awg3", "macos", lang=lang)
            self.assertEqual((got["tips"], got["tri"]), (0, 0), got)  # без правки: треугольник tip:pair у заголовка
            self.assertEqual([x[0] for x in got["pair"]], [[text]], got)
            self.assertEqual(got["order"][:2], ["gs-step__h", "gs-step__sub"], got)   # сразу под заголовком
            if lang == "en":
                self.assertIsNone(CYR.search(json.dumps(got["pair"], ensure_ascii=False)), got)
        got = self.run_pair("ocserv", "android", viewer="person", phone=True)     # пометка + «не проверено»
        self.assertEqual(got["pair"][0][0], ["Cisco Secure Client может не принять самоподписанный сертификат",
                                             "Не проверено на устройстве — проверьте сами"], got)
        self.assertEqual(got["tips"], 0, got)

    def test_the_router_step_keeps_its_note(self):
        got = self.run_pair("awg3", "keenetic")
        self.assertEqual(got["tips"], 1, got)                        # «Подготовьте роутер» — не шаг приложения
        self.assertEqual(got["pair"], [], got)


if __name__ == "__main__":
    unittest.main()

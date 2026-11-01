"""Круг 1, шаг Т3 — перенос Д2 вариант Б r3 «Пользователи» (владелец 28.10; Claude Design
export-2026-10-28-it1-d2r3, HANDOFF «it1 · Пользователи и Протоколы»). Без браузера: разметка и тексты; поведение в Chrome —
tests/test_users_rows_01b.py и tests/test_ui4_people.py (сетки, метки у имени, сворачивание «само» на узком окне, телефон).

№16 шрифт +1 px · №18 «Маршрут» узкий, имя группы · №19 кнопки 32 / 28 · №20 QR · добавить устройство · «⋯», пауза —
переключатель слева · №21 «8,4 КБ» нет · №22–№24 линия на всю свободную ширину · №23 сворачиваемые столбцы — общий узор
xt.js («Все выходы»), ключ kaskad-cols-people · меню «Пользователи» одной строкой · «Состояние» — меткой у имени ·
«в сети» точками · «Активность» перед «В сети» · фильтр «На паузе»."""
import re
import unittest

from tests.test_332f_give import read


def i18n(name):
    out = {}
    for m in re.finditer(r'^\s*"([^"]+)": "(.*)",?$', read(name), re.M):
        out[m.group(1)] = m.group(2)
    return out


class UsersTableTest(unittest.TestCase):
    def setUp(self):
        self.app, self.css, self.xt = read("app.js"), read("people.css"), read("xt.js")

    def test_columns_order_and_collapse(self):
        # «Активность» перед «В сети» (сначала графика), «Состояния» нет; сворачиваются act / proto / route — общий узор
        self.assertIn('{ t: t("pp.k1.cActDay"), tr: t("pp.k1.cActRow"), k: "act", cc: true }, { t: t("pp.k1.cOnline"), cls: "gx-xt__hf" }', self.app)
        self.assertIn('k: "proto", cc: true', self.app)
        self.assertIn('k: "route", cc: true', self.app)
        self.assertNotIn('t("pp.k1.cState"), cls: "gx-xt__sth"', self.app)
        self.assertIn('const PP_CC = "kaskad-cols-people", PP_ACT_MIN = 120;', self.app)
        self.assertIn('cc: { key: PP_CC, text: XT_CC_TEXT(), auto: { guard: "act", min: PP_ACT_MIN, order: ["route", "proto"] } }', self.app)
        # ячейки свёрнутых столбцов помечены (значение — в подсказке полоски)
        self.assertIn('class: "gx-xt__c kk-act kk-act1", "data-ck": "act"', self.app)
        self.assertIn('xtSlots(ppProtos(full), ppProtoChip, "proto")', self.app)
        self.assertIn('"data-ck": "route"', self.app)

    def test_auto_collapse_block(self):
        # xt.js: сворачивание «само» по пределу линии, выбор человека не меняется, развернуть нажатием — до перезагрузки
        for name in ("ccMark: function", "ccFit1: function", "ccWidth: function", "ccOn: function"):
            self.assertIn(name, self.xt)
        self.assertIn('getPropertyValue("--cc-min")', self.xt)
        self.assertIn('t.classList.add("is-cc-tight")', self.xt)
        self.assertIn("cc.keep[k] = true;", self.xt)
        self.assertNotIn('"Свернуть', self.xt)                           # тексты — параметрами
        self.assertIn(".gx-xt--k1 { --xt-gap: 10px; --pnw: 13rem; --cc-min: 120; }", self.css)
        self.assertIn(".kk-dn--row .gx-xt--k1 { --xt-gap: 6px; --pnw: 10.6875rem; --cc-min: 64; }", self.css)

    def test_row_cells(self):
        # №20: QR · «Добавить устройство» (в «Строке» нет) · «⋯»; пауза — переключатель слева; у устройства — место под «+»
        self.assertIn('U.btn("", "ghost", () => openAddDevice(u.id), { icon: "plus", tip: t("pp.addDevice")', self.app)
        self.assertIn('cls: "ga-btn--xs kk-nrow" });', self.app)
        self.assertIn('el("span", { class: "gx-xt__slot kk-nrow", "aria-hidden": "true" })', self.app)
        # №21: подписи объёма рядом с линией нет; линия — на всю ширину (kk-sp1)
        self.assertNotIn('class: "kk-act__v", text: actVol(a)', self.app[self.app.index("function actCell"):self.app.index("function xtSlots")])
        self.assertIn(".kk-act1 > .kk-sp1 { --spark-w: 100%; --spark-h: 24px; }", self.css)
        self.assertIn(".kk-dn--lg .kk-act1 > .kk-sp1 { --spark-h: 40px; }", self.css)
        self.assertIn(".kk-dn--row .kk-act1 > .kk-sp1 { --spark-h: 16px; }", self.css)
        # «в сети» точками; метка — у имени
        self.assertIn("const ON_DOTS = 3;", self.app)
        # (3.3.7.15 V1: после метки состояния — «ждёт настроек», №13 r2 П1)
        self.assertIn('el("span", { class: "gx-xt__n kk-pname", title: full.name }, ...hitText(full.name, q)), ucp, chip, uw ? psWaitChip(uw) : null)', self.app)
        # №18: «Маршрут» без значка, у устройства без своего — маршрут пользователя тише
        self.assertIn('xtRoute(d.route || owner.route, t("pp.k1.routeDef"), !d.route)', self.app)
        self.assertIn(".gx-xt__t.kk-rt--inh { color: var(--text-3); }", self.css)

    def test_fonts_plus_one(self):
        # №16: +1 px во всех плотностях, ширина таблицы прежняя (сетка — test_users_rows_01b)
        self.assertIn(".gx-xt--k1 .gx-xt__x:not(.gx-xt__x--sub) .gx-xt__n { font: 500 1rem/1.25rem var(--font); }", self.css)
        self.assertIn(".kk-dn--lg .gx-xt--k1 .gx-xt__x:not(.gx-xt__x--sub) .gx-xt__n { font-size: 1.0625rem; line-height: 1.375rem; }", self.css)
        self.assertIn(".kk-dn--row .gx-xt--k1 .gx-xt__x:not(.gx-xt__x--sub) .gx-xt__n { font-size: .9375rem; line-height: 1.125rem; }", self.css)
        self.assertIn(".gx-xt--k1 .gx-xt__x--sub .gx-xt__n { font: 400 .875rem/1.0625rem var(--font); }", self.css)
        self.assertIn(".kk-dn--md .gx-xt--k1 .ga-btn--xs, .kk-dn--md .gx-xt--k1 .gx-xt__slot { width: 28px; height: 28px; }", self.css)

    def test_menu_and_texts(self):
        ru, en = i18n("i18n-ru.js"), i18n("i18n-en.js")
        # меню — короткое имя одной строкой, полное — подсказкой
        self.assertIn('el("span", { class: "gx-nav__label", text: t(n.short || n.label) })', self.app)
        self.assertEqual((ru["nav.peopleShort"], ru["nav.people"]), ("Пользователи", "Пользователи и устройства"))
        self.assertEqual((ru["m.fOff"], en["m.fOff"]), ("На паузе", "Paused"))
        self.assertEqual((ru["pp.k1.cActDay"], ru["pp.k1.cActRow"], ru["pp.k1.paused"]), ("Активность · сутки", "За сутки", "пауза"))
        for k in ("pp.k1.cActDay", "pp.k1.cActRow", "pp.k1.paused", "pp.k1.pause", "pp.k1.unpause", "pp.k1.pausedOk", "pp.k1.unpausedOk", "pp.route.inheritOf"):
            self.assertIn(k, ru)
            self.assertIn(k, en)
            self.assertNotRegex(en[k], r"[А-Яа-яЁё]", k)
        self.assertNotRegex(" ".join(ru[k] for k in ru if k.startswith("pp.k1.")), r"[Лл]юд")   # «Пользователи», не «Люди»

    def test_phone_row(self):
        # Т1: линия · точки · «N из M» второй строкой; справа QR, раскрытие и «⋯» (переименовать, пауза, удалить,
        # «Поделиться» на телефоне есть только в нём); линии рисуются по размеру
        self.assertIn('el("span", { class: "kk-prow__sp" }', self.app)
        self.assertIn('qr.classList.add("kk-prow__qr");', self.app)
        self.assertIn('main, qr, exp, menu), devs);', self.app)
        self.assertIn("SPK().watch(box);", self.app)
        self.assertIn(".kk-prow__sp > .gx-spark.kk-sp1 { flex: 1 1 0; width: auto; min-width: 40px; height: 20px; }", self.css)


if __name__ == "__main__":
    unittest.main()

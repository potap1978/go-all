"""Круг 1 правок по видео владельца (27.10), шаг Т2, часть «Настройки» — перенос макета Claude Design it1 Д3
«Выходы и Настройки» (кадры Н1–Н6, Т3–Т4; HANDOFF «it1 · Выходы и Настройки»).

* №36 «Шлюз» простыми словами: строка под шапкой части (st.gwLead), в карточке повтора нет; пояснения у «Адреса для
  клиентов» (st.gwHostD) и у «Сети шлюза» (st.gwNetD).
* Решение 9 (П4, №39): «Адрес для клиентов» выбирается только в «Шлюзе» (gwAddr): по IP — поле, «Сохранить»,
  «Определить сам», при домене — «Подключать устройства по домену»; по домену — «Вернуть на IP». В «Домене и сайте»
  карточки нет — строка-ссылка в «Шлюз»; поиск и «Изменить» с «Обзора» ведут в «Шлюз».
* №37 «Кто пользуется сертификатом домена»: строки 1fr · 240, метки и переключатели — колонкой у правого края.
* №41 «Удалить историю активности»: строка последней в карточке, окно Сотрётся · Останется · Освободится, сухой
  прогон перед окном, «Хранить» выключено — строки нет, пусто — кнопка недоступна, 409 — словами сервера.

Поведение — в настоящем Chrome на заглушке панели (tools/k3_t2_settings_check.mjs; нет node или Chrome — пропуск со
словами), остальное — без браузера."""

import functools
import json
import os
import re
import shutil
import subprocess
import threading
import unittest

from tests import jsparts
from tests.test_332f_give import HAS_NODE, read
from tests.test_333q_layout import ChromeServer, _Static, chrome_path
from tests.test_it1_t2_panel import i18n

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CHECK = os.path.join(ROOT, "tools", "k3_t2_settings_check.mjs")


class TextsTest(unittest.TestCase):
    """Тексты макета (решение 14) — RU и EN, одинаковые подстановки."""

    KEYS = ("st.gwLead", "st.gwHostD", "st.gwNetD", "st.gwAlt", "st.gwChipAuto", "st.gwChipManual", "st.gwChipDom",
            "st.gwDomChip", "site.lrowDom", "site.lrowHost", "site.lrowS", "site.useSwAria", "sto.usedNone", "sto.wipe",
            "sto.wipeD", "sto.wipeBtn", "sto.wipeFree", "sto.wipeNone", "sto.wipeNoneT", "sto.wipeT", "sto.wipeX",
            "sto.wipeGone", "sto.wipeGone1", "sto.wipeGone2", "sto.wipeStay", "sto.wipeStay1", "sto.wipeFreeH",
            "sto.wipeOk", "sto.wipeBusy", "sto.wipeDone", "sto.wipeFreed")

    def test_ru_en(self):
        ru, en = i18n("i18n-ru.js"), i18n("i18n-en.js")
        for k in self.KEYS:
            self.assertTrue(ru.get(k), k)
            self.assertTrue(en.get(k), k)
            self.assertEqual(sorted(re.findall(r"\{\w+\}", ru[k])), sorted(re.findall(r"\{\w+\}", en[k])), k)

    def test_words_of_the_mock(self):
        ru = i18n("i18n-ru.js")
        self.assertEqual(ru["st.gwLead"], "Шлюз — это сам сервер: к нему подключаются устройства пользователей, а дальше "
                                          "их трафик идёт через выходы.")
        self.assertEqual(ru["st.gwNetD"], "Внутренние адреса устройств в туннеле. Менять обычно не нужно.")
        self.assertEqual(ru["st.gwHostD"], "Этот адрес попадает в настройки устройств: по нему они находят сервер.")
        self.assertEqual(ru["sto.wipeD"], "Сотрёт линии активности и итоги за сутки. Хранение останется включённым — "
                                          "запись начнётся заново.")
        self.assertEqual((ru["sto.wipeGone"], ru["sto.wipeStay"], ru["sto.wipeFreeH"]), ("Сотрётся", "Останется", "Освободится"))
        self.assertEqual((ru["sto.wipeFree"], ru["sto.wipeNone"]), ("освободится ≈ {n}", "нечего удалять"))

    def test_old_texts_gone(self):
        """Повтор подзаголовка в карточке и карточка «Адрес для клиентов» в «Домене и сайте» убраны вместе с текстами."""
        app = read("app.js")
        for lang in ("ru", "en"):
            d = i18n("i18n-%s.js" % lang)
            for k in ("st.gwText", "st.gwHostHint", "site.hostT", "site.hostD", "site.hostNow", "site.hostByDomain",
                      "site.hostOnChip", "site.hostOffChip", "site.hostOff"):
                self.assertNotIn(k, d, lang)
                self.assertNotIn('"%s"' % k, app)


class GatewayStaticTest(unittest.TestCase):
    """№36, решение 9: адрес для клиентов — только в «Шлюзе»."""

    def test_lead_is_the_part_line(self):
        app = read("app.js")
        self.assertIn('gateway: "st.gwLead"', app)                               # ST_DESC: строка под шапкой части
        card = jsparts.function(app, "gatewayCard")
        self.assertIn('return U.sec(t("st.gw"), "", [', card)                    # в карточке повтора нет
        self.assertIn('cls: "kk-gw2"', card)

    def test_address_lives_in_the_gateway(self):
        app = read("app.js")
        self.assertNotIn("function siteHostCard", app)
        self.assertNotIn("siteHostCard()", app)
        addr = jsparts.function(app, "gwAddr")
        self.assertIn('"data-find": "settings.host"', addr)
        for call in ("siteHostIp", "siteHost)", "API.gw.host({ host: h })", "API.gw.host({ detect: true })"):
            self.assertIn(call, addr)
        area = jsparts.function(app, "siteArea")
        self.assertEqual(area.count("siteHostRow()"), 3)                         # строка-ссылка в каждом виде «Домена и сайта»

    def test_find_and_overview_lead_to_the_gateway(self):
        app = read("app.js")
        self.assertNotIn('"settings.siteHost"', app)
        self.assertIn('{ id: "settings.host", kind: "field", group: "st.gw", label: "st.gwHost", hint: "st.gwHostD"', app)
        self.assertIn('at: { tab: "gateway", focus: "settings.host" } }', app)
        focus = jsparts.function(app, "settingsFocusHost")
        self.assertIn('const want = "gateway";', focus)
        self.assertNotIn('? "site" :', focus)                                    # с доменом — тоже «Шлюз»


class WipeStaticTest(unittest.TestCase):
    """№41: клиент API, вызов в панели, строка только при сохранённом «Хранить»."""

    def test_api_client(self):
        app = read("app.js")
        self.assertIn('wipe: (dry) => request("DELETE", "/api/gw/activity" + (dry ? qs({ dry: 1 }) : "")),', app)
        contract = open(os.path.join(ROOT, "tests", "test_panel_contract.py"), encoding="utf-8").read()
        self.assertNotIn('"gw_activity_wipe")', contract)                      # маршрут больше не «ждёт кнопки»

    def test_flow(self):
        app = read("app.js")
        flow = jsparts.function(app, "stoWipe")
        self.assertIn('oneAtATime("stowipe"', flow)                              # двойное нажатие — один заход
        dry, ask, real = flow.index("API.activity.wipe(true)"), flow.index("stoWipeAsk(bytes)"), flow.index("API.activity.wipe(false)")
        self.assertLess(dry, ask)                                                # свежее «освободится» — до окна
        self.assertLess(ask, real)
        self.assertIn('loadQuiet("activity")', flow)                             # сводка перечитана
        self.assertIn("storageLoad()", flow)                                     # настройки хранения перечитаны
        self.assertIn("if (on && stof.base.keep) kids.push(stoWipeRow(d));", app)


@unittest.skipUnless(shutil.which("node"), "нет node")
class WipeFlowTest(unittest.TestCase):
    """stoWipe без браузера: отказ в окне — настоящего удаления нет; 409 на сухом прогоне — окна нет, слова сервера."""

    def run_flow(self, ask, dry_err=None, real_err=None):
        src = "\n".join(jsparts.function(read("app.js"), n) for n in ("stoWipe",))
        code = r"""
const calls = [], toasts = [], errors = [];
const t = (k) => k;
const num = Number, known = (v) => v !== null && v !== undefined;
const fmtBytes = (n) => n + " B";
const oneAtATime = (k, fn) => fn();
const stof = { draw: () => calls.push("draw") };
const storageLoad = async () => { calls.push("reread"); };
const loadQuiet = (r) => calls.push("quiet:" + r);
const toast = (a, b, c) => toasts.push([a, b, c]);
const showError = (e) => errors.push(e.message);
const act = async (fn) => { try { return (await fn()) || {}; } catch (e) { showError(e); return null; } };
const stoWipeAsk = async (b) => { calls.push("ask:" + b); return ASK; };
const ASK = %s, DRYERR = %s, REALERR = %s;
const API = { activity: { wipe: async (dry) => { calls.push(dry ? "dry" : "wipe");
  if (dry && DRYERR) throw new Error(DRYERR); if (!dry && REALERR) throw new Error(REALERR);
  return dry ? { ok: true, dry: true, bytes: 2048 } : { ok: true, freed: 2048, keep: true, bytes: 0 }; } } };
%s
(async () => { await stoWipe(); console.log(JSON.stringify({ calls, toasts, errors })); })();
""" % (json.dumps(ask), json.dumps(dry_err), json.dumps(real_err), src)
        r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_cancel_does_not_wipe(self):
        out = self.run_flow(False)
        self.assertEqual(out["calls"], ["dry", "ask:2048"])

    def test_wipe_then_reread(self):
        out = self.run_flow(True)
        self.assertEqual(out["calls"], ["dry", "ask:2048", "wipe", "reread", "draw", "quiet:activity"])
        self.assertEqual(out["toasts"][0][0], "sto.wipeDone")

    def test_409_on_dry_no_dialog(self):
        out = self.run_flow(True, dry_err="Хранение истории активности выключено")
        self.assertEqual(out["calls"], ["dry", "reread", "draw"])
        self.assertEqual(out["errors"], ["Хранение истории активности выключено"])

    def test_409_on_wipe_reread_no_summary(self):
        out = self.run_flow(True, real_err="База занята")
        self.assertEqual(out["calls"], ["dry", "ask:2048", "wipe", "reread", "draw"])
        self.assertEqual((out["errors"], out["toasts"]), (["База занята"], []))


class CssTest(unittest.TestCase):
    def test_block_and_columns(self):
        css = read("styles.css")
        block = css[css.index("Т2 Настройки"):]
        self.assertIn(".kk-use__r { display: grid; grid-template-columns: minmax(0, 1fr) 240px;", block)
        for cls in (".kk-gw2__addr", ".kk-gw2__alt", ".kk-gw2__dom", ".kk-gw2__net", ".kk-lrow", ".kk-wipe", ".kk-dlg3__sec",
                    '[data-device="phone"] .kk-wipe__e > .ga-btn', '[data-device="phone"] .kk-gw2__dom > .ga-btn'):
            self.assertIn(cls, block)


@unittest.skipUnless(HAS_NODE and chrome_path(), "нет node или Chrome: шаг Т2 «Настройки» в браузере не проверен (K3_CHROME=…)")
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

    def test_gw_words(self):
        g = self.r["gw"]
        self.assertTrue(g["lead"].startswith("Шлюз — это сам сервер"))
        self.assertEqual((g["cardDesc"], g["headChip"]), (0, "включён"))
        self.assertEqual(g["net"]["hint"], "Внутренние адреса устройств в туннеле. Менять обычно не нужно.")
        self.assertEqual(g["net"]["vals"], ["10.77.0.0/16", "DNS 10.77.0.1"])
        self.assertTrue(self.r["en"]["lead"].startswith("The gateway is the server itself"))

    def test_gw_by_ip(self):
        ip = self.r["gw"]["ip"]
        self.assertEqual((ip["label"], ip["chip"], ip["find"]), ("Адрес для клиентов", "IP сервера · определён автоматически", "settings.host"))
        self.assertEqual((ip["value"], ip["btns"], ip["dom"]), ("203.0.113.5", ["Сохранить", "Определить сам"], False))
        self.assertTrue(ip["hint"].startswith("Этот адрес попадает в настройки устройств"))
        self.assertIn("Есть домен vpn.example.com", ip["alt"])
        self.assertTrue(ip["alt"].endswith("Подключать устройства по домену"))
        self.assertEqual(self.r["gw"]["noDom"], {"alt": False, "dom": False})

    def test_gw_to_domain_and_back(self):
        g = self.r["gw"]
        self.assertEqual(g["toDomDlg"], "Подключать устройства по домену?")
        d = g["dom"]
        self.assertEqual((d["chip"], d["dn"], d["btn"], d["input"]), ("домен", "vpn.example.com", "Вернуть на IP", False))
        self.assertTrue(d["p"].startswith("Устройства подключаются по домену vpn.example.com"))
        self.assertEqual(g["toIpDlg"], {"title": "Подключать устройства по IP?", "hasKeep": True})
        self.assertEqual(g["back"], {"chip": "IP сервера · определён автоматически", "value": "203.0.113.5", "alt": True})
        self.assertEqual(g["openHost"], {"part": "gateway", "hash": "#settings?tab=gateway"})

    def test_site_link_row(self):
        s = self.r["site"]
        self.assertFalse(s["oldCard"])
        self.assertEqual(s["lrow"], {"t": "Адрес для клиентов — по домену vpn.example.com",
                                     "s": "По IP или по домену — выбирается в «Настройки › Сервер › Шлюз»",
                                     "href": "#settings?tab=gateway&focus=settings.host", "tag": "A", "last": True})
        self.assertEqual(s["go"], {"part": "gateway", "addr": True})

    def test_site_use_column(self):
        u = self.r["site"]["use"]
        self.assertEqual((u["n"], u["cols"]), (4, "240px"))
        self.assertEqual(u["titles"], ["Панель", "Страницы пользователей", "SSTP", "OpenConnect"])
        self.assertEqual(len(set(u["rights"])), 1)                                # одна колонка у правого края
        self.assertLessEqual(abs(u["rights"][0] - u["cardRight"]), 2)
        self.assertEqual(u["chips"], ["уже на сертификате домена"] * 2)
        self.assertEqual(u["sw"], [{"aria": "SSTP на сертификате домена", "on": "true"}, {"aria": "OpenConnect на сертификате домена", "on": "false"}])
        self.assertEqual((self.r["site"]["ocDlg"], self.r["site"]["ocAfter"]), ("Перевести OpenConnect на сертификат домена?", "false"))

    def test_wipe_row(self):
        b = self.r["wipe"]["before"]
        self.assertTrue(b["last"])
        self.assertEqual((b["title"], b["btn"], b["dis"], b["keep"]), ("Удалить историю активности", "Удалить историю…", False, "true"))
        self.assertIn("ga-btn--danger-soft", b["cls"])
        self.assertEqual(b["desc"], "sto-wipe-f")
        size = b["free"].split("≈ ")[1]
        self.assertEqual(b["free"], "освободится ≈ " + size)
        self.assertIn("Сейчас занято" + size, b["kv"])                            # то же число, что «Сейчас занято»
        self.assertFalse(self.r["wipe"]["draftOff"])                              # «Хранить» выключено — строки нет
        self.assertFalse(self.r["wipe"]["off"])

    def test_wipe_dialog(self):
        w = self.r["wipe"]
        d = w["dlg"]
        self.assertEqual((d["title"], d["n"], d["kk"]), ("Удалить историю активности?", 1, True))
        self.assertEqual(d["heads"], ["Сотрётся", "Останется", "Освободится"])
        self.assertEqual(d["items"][0], ["линии активности пользователей и устройств за всё время", "итоги за сутки"])
        self.assertEqual(d["items"][1], ["сами пользователи, устройства и настройки"])
        self.assertEqual(d["items"][2], ["≈ " + w["before"]["free"].split("≈ ")[1]])
        self.assertEqual(d["btns"], ["Отмена", "Удалить историю"])
        self.assertEqual(w["wipesAsk"], ["/api/gw/activity?dry=1"])               # двойное нажатие — один сухой прогон
        self.assertEqual(w["cancel"]["wipes"], ["/api/gw/activity?dry=1"])        # «Отмена» — ничего не удалено
        self.assertEqual(w["cancel"]["row"]["free"], w["before"]["free"])

    def test_wipe_done(self):
        w = self.r["wipe"]
        self.assertEqual(w["wipes"], ["/api/gw/activity?dry=1", "/api/gw/activity?dry=1", "/api/gw/activity"])
        a = w["after"]
        self.assertEqual((a["dis"], a["free"], a["keep"]), (True, "нечего удалять", "true"))
        self.assertIn("Сейчас занято0 Б · истории пока нет", a["kv"].replace("\xa0", " "))
        self.assertIn("История активности удалена", w["toast"])

    def test_wipe_409(self):
        e = self.r["err"]
        self.assertIn("sqlite3", e["toast"])
        self.assertFalse(e["dlg"])
        self.assertEqual(e["wipes"], ["/api/gw/activity?dry=1"])

    def test_en(self):
        e = self.r["en"]
        self.assertEqual((e["row"]["title"], e["row"]["btn"]), ("Delete activity history", "Delete history…"))
        self.assertTrue(e["row"]["free"].startswith("frees ≈ "))
        self.assertEqual(e["dlg"]["heads"], ["Will be erased", "Will stay", "Will be freed"])
        self.assertEqual(e["dlg"]["btns"], ["Cancel", "Delete history"])

    def test_phone(self):
        p = self.r["phone"]
        self.assertLessEqual(abs(p["ip"]["btn"] - p["ip"]["inner"]), 1)          # «Вернуть на IP» во всю ширину (Т3)
        self.assertLessEqual(abs(p["wipe"]["btn"] - p["wipe"]["inner"]), 1)      # «Удалить историю…» во всю ширину (Т4)
        self.assertFalse(p["ip"]["over"] or p["wipe"]["over"])
        self.assertEqual(p["use"], {"sw": [40, 24], "inside": True, "over": False})   # общее правило 44×44 переключатель не раздувает


if __name__ == "__main__":
    unittest.main()

"""3.3.7.15 V1 (вид №13 r2): «Настройки протокола», безопасное применение с откатом, «Сеть шлюза» в панели.

Чистые части app.js — в node на ответах сервера: полоска пакета MTU и проверка поля, «Что изменится» из плана сервера (с
добавками 4–5 договора pending и без них — запасные слова панели), тело запроса окна, проверка сети шлюза (диапазон,
«сейчас», занятые сервером), «записано, но шаг после не прошёл» (добавка 6). Разметка: «Настройки» вместо «Порт»,
CSP (доли — через CSSOM), стили pset.css. Ядро: mtu.manual_view отдаёт концы шкалы полоски (nic, budget, overhead, s4)."""

import json
import os
import re
import subprocess
import tempfile
import unittest

from kaskad.gw import mtu as gwmtu
from tests import jsparts
from tests.helpers import RootCase

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def run_node(script):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(script)
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if r.returncode != 0:
        raise AssertionError(r.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])


# словарь — настоящий i18n-ru.js (запасные слова панели сверяются с ним же)
STUBS = r"""
const window = {};
%s
const DICT = window.KASKAD_I18N;
const fill = (s, v) => String(s).replace(/\{(\w+)\}/g, (m, k) => (v && v[k] !== undefined ? String(v[k]) : m));
const t = (k, v) => fill(DICT[k] !== undefined ? DICT[k] : k, v);
const isObj = (x) => !!x && typeof x === "object" && !Array.isArray(x);
const arr = (x) => (Array.isArray(x) ? x : []);
const known = (v) => v !== null && v !== undefined && v !== "";
const validPort = (v) => /^\d{1,5}$/.test(String(v).trim()) && Number(v) >= 1 && Number(v) <= 65535;
"""
CONSTS = ("PS_LO", "PS_QUIET", "PS_PPP", "TLS_ON", "psTlsText", "GW_A", "ip4", "ipOverlap", "gwMkX", "gwSel0", "psAfterFailed")
FUNCS = ("psMtuGeo", "psMtuCheck", "psWho", "psHit", "psChgItems", "psNetItems", "psPortVal", "psBody", "gwNetCheck",
         "psUndoWhat", "psDiff", "psIssueBody", "normPending")


class ViewLogicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        js = read("app.js")
        cls.js = js
        cls.parts = "\n".join([STUBS % read("i18n-ru.js")] + [jsparts.const(js, n) for n in CONSTS] + [jsparts.function(js, n) for n in FUNCS])

    def run_js(self, body):
        return run_node(self.parts + "\n" + body)

    def test_mtu_bar_and_field(self):
        out = self.run_js(r"""
const wg = { cur: 1320, min: 1280, limit: 1320, open: true, nic: 1500, budget: 1400, overhead: 80, s4: 0 };
const awg = { cur: 1290, min: 1280, limit: 1290, open: true, nic: 1500, budget: 1400, overhead: 80, s4: 30 };
const old = { cur: 1290, min: 1280, limit: 1290, open: true };              // ядро без nic/s4: карта 1500, S4 = бюджет − 80 − предел
const off = { cur: 1260, min: 1280, limit: 1250, open: false, why: "закрыто", nic: 1500, budget: 1400, overhead: 80, s4: 70 };
console.log(JSON.stringify({ wg: psMtuGeo(wg, 1300), awg: psMtuGeo(awg, null), old: psMtuGeo(old, null),
  chk: [psMtuCheck(wg, ""), psMtuCheck(wg, "1320"), psMtuCheck(wg, "1300"), psMtuCheck(wg, "1279"), psMtuCheck(wg, "1321"), psMtuCheck(wg, "13a0"), psMtuCheck(off, "1260")] }));
""")
        wg, awg = out["wg"], out["awg"]
        # клиент 1300 + 80 = пакет снаружи 1380; шкала 1200 … 1500 (карта): 1300 → 33,33 %, бюджет 1400 → 66,67 %
        self.assertEqual((wg["c"], wg["outer"], wg["cW"], wg["bud"], wg["s4W"]), (1300, 1380, 33.33, 66.67, 0))
        self.assertEqual(wg["lim"], 40)                                     # пунктир предела 1320
        # Н2 / Н6а: S4 30 — сегмент после «+80», пакет снаружи 1290 + 80 + 30 = 1400; пунктир предела левее «+80»
        self.assertEqual((awg["outer"], awg["s4"], awg["s4L"]), (1400, 30, 56.67))
        self.assertLess(awg["lim"], awg["s4L"])
        self.assertEqual((out["old"]["nic"], out["old"]["s4"], out["old"]["outer"]), (1500, 30, 1400))
        self.assertEqual([c["state"] for c in out["chk"]], ["cur", "cur", "ok", "bad", "bad", "bad", "off"])

    PLAN = {"kind": "wg", "title": "WireGuard", "changed": {"port": {"old": 51820, "new": 51821}},
            "restart": {"rows": [{"what": "port", "word": "syncconf", "text": "WireGuard: без перезапуска."}], "with": [], "gateway": False},
            "reissue": {"hard": [{"id": "d-1", "name": "Телефон", "user_name": "Мама"}], "soft": []},
            "warnings": [], "via_xray": False, "admin_hit": None, "need_yes": True, "front": False}

    def chg(self, plan):
        return self.run_js("console.log(JSON.stringify(psChgItems(%s)));" % json.dumps(plan, ensure_ascii=False))

    def test_what_changes_with_the_server_words_of_additions_4_and_5(self):
        hit = "Вы подключены через WireGuard: связь может оборваться."
        plan = dict(self.PLAN, admin_hit="pool", hit_text=hit, warnings=[hit],
                    reissue_text={"hard": "Сервер: выдайте заново Телефон.", "soft": ""}, with_text="")
        out = self.chg(plan)
        self.assertEqual(out["n"], 1)
        self.assertEqual([(x["ico"], x["text"], x["tone"]) for x in out["items"]],
                         [("arrow-rotate-right", "WireGuard: без перезапуска.", ""), ("key", "Сервер: выдайте заново Телефон.", "hard")])
        # добавка 6: строка плашки (hit_text) в списке не повторяется
        self.assertNotIn(hit, [x["text"] for x in out["items"]])

    def test_what_changes_without_the_additions_uses_the_panel_copies(self):
        # ядро до добавок 4–5: строки «выдайте заново» и «задевает и» — запасные слова панели (копии gw_ops), а
        # строка admin_hit в warnings — та же, что плашка (ps.hitDl) — в список не попадает
        copy = "Вы подключены через WireGuard: после применения связь может оборваться. Если за 2 мин не подтвердите — настройки вернутся сами."
        plan = dict(self.PLAN, admin_hit="pool", warnings=[copy],
                    restart={"rows": [{"what": "port", "word": "sstp", "text": "SSTP: перезапуск accel-ppp."}], "with": [{"kind": "l2tp", "title": "L2TP/IPsec"}], "gateway": False})
        out = self.chg(plan)
        texts = [x["text"] for x in out["items"]]
        self.assertEqual(texts, ["SSTP: перезапуск accel-ppp.", "Перезапуск задевает и: L2TP/IPsec.",
                                 "Устройства с прежними настройками WireGuard не подключатся — выдайте им настройки заново: Телефон (Мама)."])
        self.assertEqual(out["items"][0]["tone"], "warn")                     # перезапуск не «на ходу» — тоном внимания
        self.assertEqual(self.run_js("console.log(JSON.stringify(psHit(%s)));" % json.dumps(plan, ensure_ascii=False)), copy)

    def test_xray_line_goes_last_with_its_own_tone(self):
        x = "Если вы открыли панель через VLESS, SOCKS5 или Hysteria2 этого сервера, связь может оборваться."
        plan = dict(self.PLAN, kind="vless", title="VLESS", via_xray=True, warnings=[x, "TLS: старые клиенты."],
                    reissue={"hard": [], "soft": []}, restart={"rows": [], "with": [], "gateway": True})
        out = self.chg(plan)
        self.assertEqual([(x2["text"], x2["tone"]) for x2 in out["items"]], [("TLS: старые клиенты.", "warn"), (x, "admin")])

    def test_net_items_follow_the_reissue_kinds(self):
        plan = {"scope": "base", "title": "Сеть шлюза", "reissue": {"wg": [{}], "sstp": [{}]}, "admin_hit": "base", "hit_text": "H",
                "lines": ["WG не подключится", "MikroTik на SSTP", "Остальные переподключатся сами."], "warnings": ["192.168 — домашние", "H"]}
        out = self.run_js("console.log(JSON.stringify(psNetItems(%s)));" % json.dumps(plan, ensure_ascii=False))
        self.assertEqual([(x["ico"], x["tone"]) for x in out], [("key", "hard"), ("router", "warn"), ("arrow-rotate-right", "")])
        # предупреждения плана — строкой проверки под выбором сети (Ш3), строка плашки — плашкой: в списке ни того, ни другого
        self.assertFalse({"H", "192.168 — домашние"} & {x["text"] for x in out})

    def test_request_body_of_the_window(self):
        out = self.run_js(r"""
const mtu = { cur: 1320, min: 1280, limit: 1320, open: true };
const g = (o) => Object.assign({ key: "p", cur: 51820, proto: null, sel: "cur", own: "", chk: null, rnd: null }, o);
const tls = { cur: "1.2-1.3", choices: [] };
console.log(JSON.stringify([
  psBody({ groups: [g({ sel: "rnd", rnd: 40000 })], mtu, mtuIn: "1300", tls: null }),
  psBody({ groups: [g({ sel: "own", own: "" })], mtu, mtuIn: "1320", tls: null }),
  psBody({ groups: [g({ sel: "own", own: "22", chk: { bad: true } })], mtu: null, tls: null }),
  psBody({ groups: [g({ key: "udp", proto: "udp", cur: 1194 }), g({ key: "tcp", proto: "tcp", cur: 1443, sel: "own", own: "1444" })], mtu: null, tls: null }),
  psBody({ groups: [], mtu: null, tls, tlsSel: "1.3" }),
  psBody({ groups: [], mtu, mtuIn: "1500", tls: null }),
]));
""")
        self.assertEqual(out[0], {"body": {"port": 40000, "mtu": 1300}, "n": 2, "bad": False, "empty": False})
        self.assertEqual(out[1], {"body": {}, "n": 0, "bad": False, "empty": True})          # «Впишите свой порт»
        self.assertEqual((out[2]["bad"], out[2]["n"]), (True, 0))                            # занятый свой порт
        self.assertEqual(out[3]["body"], {"port": 1444, "proto": "tcp"})                     # OpenVPN: поле — по proto
        self.assertEqual(out[4]["body"], {"tls": "1.3"})
        self.assertEqual((out[5]["bad"], out[5]["body"]), (True, {}))                        # MTU выше предела — не шлём

    def test_gateway_network_check(self):
        out = self.run_js(r"""
const view = { base: "10.77.0.0/16", taken: [{ net: "10.78.0.0/16", why: "Docker" }, { net: "172.17.0.0/16", why: "docker0" }, { net: "10.90.3.7/32", why: "ключ «Вена»" }] };
const c = (a, x) => gwNetCheck(view, { a, x });
console.log(JSON.stringify([c("10", "77"), c("10", "78"), c("10", "256"), c("172", "15"), c("172", "17"), c("172", "20"), c("192", ""), c("10", "90"), c("10", "x"),
  gwSel0("172.27.0.0/16"), gwSel0("192.168.0.0/16"), gwSel0(""), gwMkX("10", 77), gwMkX("172", 16)]));
""")
        self.assertTrue(out[0]["now"])
        self.assertTrue(out[1]["taken"] and out[1]["bad"])
        self.assertIn("Docker 10.78.0.0/16", out[1]["text"])
        self.assertEqual([out[i]["bad"] for i in (2, 3, 8)], [True, True, True])
        self.assertIn("от 16 до 31", out[3]["text"])
        self.assertTrue(out[4]["taken"])                                   # п. 10 r2: Docker на 172.17 — занято
        self.assertEqual((out[5]["base"], out[5]["tone"]), ("172.20.0.0/16", "ok"))
        self.assertEqual(out[6]["base"], "192.168.0.0/16")                  # 192.168 — только целиком
        self.assertTrue(out[7]["taken"])                                    # адрес ключа выхода /32 внутри сети
        self.assertEqual(out[9:12], [{"a": "172", "x": "27"}, {"a": "192", "x": "168"}, {"a": "10", "x": "77"}])
        self.assertAlmostEqual(out[12], 30.273, places=2)
        self.assertAlmostEqual(out[13], 3.125, places=3)

    def test_after_failed_is_a_warning(self):
        # добавка 6, П1: признак — только ключ after_failed: true (при успехе ключа нет); слова текста не сравниваются
        out = self.run_js(r"""
console.log(JSON.stringify([psAfterFailed({ ok: true, after_failed: true, text: "x" }),
  psAfterFailed({ ok: true, text: "WireGuard: настройки применены, но последний шаг не прошёл (OSError): …" }),
  psAfterFailed({ ok: true, after_failed: "yes" }), psAfterFailed({ ok: true, text: "WireGuard: применено — порт 51820 → 51821." }), psAfterFailed(null)]));
""")
        self.assertEqual(out, [True, False, False, False, False])

    def test_undo_words_and_issue_body(self):
        out = self.run_js(r"""
const tv = { choices: [{ value: "1.2-1.3", text: "1.2 и 1.3" }, { value: "1.3", text: "только 1.3" }] };
console.log(JSON.stringify([psUndoWhat({ port: { old: 51820, new: 51821 }, mtu: { old: 1320, new: 1300 } }, null),
  psUndoWhat({ tls: { old: "1.2-1.3", new: "1.3" } }, tv), psDiff({ port: { old: 1, new: 2 }, tls: { old: "1.3", new: "1.2-1.3" } }, tv),
  psIssueBody({ kind: "openvpn", changed: { port: { old: 1443, new: 1444, protos: ["tcp"] } } }), psIssueBody({ kind: "", changed: { base: { old: "10.77.0.0/16", new: "10.79.0.0/16" } } }),
  normPending({ pending: { scope: "wg", left: 103.4, token: "0123456789abcdef", deadline: 5 } }), normPending({ pending: { scope: "wg" } })]));
""")
        self.assertEqual(out[0], "порт 51820, MTU 1320")
        self.assertEqual(out[1], "TLS 1.2 и 1.3")
        self.assertEqual(out[2], "порт 1 → 2, TLS только 1.3 → 1.2 и 1.3")
        self.assertEqual(out[3], {"port": 1444, "proto": "tcp"})
        self.assertEqual(out[4], {"base": "10.79.0.0/16"})
        self.assertEqual((out[5]["scope"], out[5]["left"]), ("wg", 103))
        self.assertIsNone(out[6])                                           # без кода ожидания — нет полосы


class ViewMarkupTest(unittest.TestCase):
    def test_settings_replaces_the_port_window(self):
        js = read("app.js")
        self.assertNotIn("function openPort", js)
        self.assertNotIn("API.inbounds.port(", js)
        acts = jsparts.function(js, "ibActs")
        self.assertIn('if (psCan(r)) more.push({ label: t("ps.settings"), icon: "sliders", on: () => psOpen(r) });', acts)
        card = jsparts.function(js, "ibUxCard")
        for need in ('portLabel: t("ps.settings")', "wait: psWaitOf(r.kind)", "onPort: () => psOpen(r)", "rowExtra: row ? [psCfgNode(r), psLastNode("):
            self.assertIn(need, card)
        ux = read("ux.js")
        self.assertIn("r.portLabel || t(lang, 'pc.changePort')", ux)
        self.assertIn("'kb2-wait kb2-wait--' + (r.wait.tone === 'hard' ? 'hard' : 'soft')", ux)
        self.assertIn("(o.rowExtra || []).filter(Boolean)", ux)

    def test_front_shows_the_rollback_button_disabled(self):
        # Е11 r2: VLESS перед сайтом (front) — «Применить с откатом через 2 мин» видна, но недоступна, акцент — «Применить»;
        # в подтверждении она не в okBtns (иначе sync() включил бы её вместе с «Применить»)
        js = read("app.js")
        self.assertEqual(js.count('U.btn(t("ps.safeBtn"), "tertiary", null, { md: true, dis: true, tip: t("ps.noSafeFront") })'), 2)   # Д1: с подсказкой
        self.assertIn("noSafe].concat(okBtns)", js)

    def test_people_menu_gets_the_wait_item_when_marks_come(self):
        # П1: «Ждут настроек · N» в подменю «Пользователей» появляется, когда пометки пришли позже списка (optional
        # ресурс): subnavSync пересобирает меню, если сменился состав пунктов (детектор: правило pend на «Пользователях»)
        sync = jsparts.function(read("app.js"), "subnavSync")
        self.assertIn("nav.replaceWith(subnav(spec))", sync)
        self.assertLess(sync.index("replaceWith"), sync.index("aria-current"))

    def test_row_extras_paint_in_place(self):
        # раскрытие строки «Протоколов» спрашивает GET …/settings (psPeek): ответ дорисовывает «Порт · MTU · TLS» и
        # «Применено …» на месте (psRowPaint), а не пересобирает список — иначе узлы под курсором менялись и фокус строки
        # терялся (детектор pcopen, сцена node3373)
        js = read("app.js")
        peek = jsparts.function(js, "psPeek")
        self.assertNotIn("scheduleRefresh", peek)
        self.assertIn("psRowPaint(kind);", jsparts.function(js, "psRemember"))
        sig = js[js.index("const psRowSig = "):js.index("\n", js.index("const psRowSig = "))]
        self.assertNotIn("PS_LAST", sig)
        paint = jsparts.function(js, "psRowPaint")
        self.assertIn("n.replaceWith(cfg)", paint)

    def test_gateway_network_change_button(self):
        # условие выпуска (PLAN «31.10 04:40»): кнопка «Сменить» у «Сети шлюза» в «Настройки › Шлюз»
        js = read("app.js")
        self.assertIn('U.btn(t("gw.change"), "tertiary", () => gwNetOpen(), { find: "settings.gwNet" })', js)
        self.assertEqual(read("i18n-ru.js").count('"gw.change": "Сменить"'), 1)

    def test_csp_shares_through_cssom_only(self):
        js = read("app.js")
        a, b = js.index("── 16а. «Настройки протокола»"), js.index("async function toggleInbound(r, on)")
        part = js[a:b]
        self.assertNotIn('style:', part)
        self.assertNotIn(".style.left", part)
        self.assertNotIn(".style.width", part)
        self.assertGreaterEqual(part.count('style.setProperty("left"'), 2)
        self.assertIn('fill.style.setProperty("width"', part)

    def test_pset_css_is_linked_and_has_the_width_table(self):
        html = read("index.html")
        self.assertLess(html.index('href="grid.css'), html.index('href="pset.css'))
        self.assertLess(html.index('href="pset.css'), html.index('href="stream.css'))
        css = read("pset.css")
        for need in (".gx-dlg--pset { --dlg-w: 1200px; }", "container: pset / inline-size", "@container pset (max-width: 987px)",
                     "@container pset (max-width: 759px)", "grid-template-columns: minmax(0, 1fr) 320px", ".gx-chg { position: sticky;",
                     '[data-device="phone"] .gx-dlg-bd--sheet .gx-dlg.gx-dlg--pset .gx-dlg__foot.gx-pset__foot { flex-direction: column;'):
            self.assertIn(need, css)
        self.assertNotIn("var(--danger-foreground", css)                  # неизвестный tokens.css токен макета заменён

    def test_fallback_copies_are_marked(self):
        # добавки 4–5: запасные копии слов сервера помечены, чтобы после натяжки не осталось двух разных текстов
        ru = read("i18n-ru.js")
        i = ru.index("// ЗАПАС: копии слов сервера")
        for k in ("ps.reHard", "ps.reSoft", "ps.with", "ps.whoMore", "ps.hitDl", "ps.hitBase", "ps.gone"):
            self.assertGreater(ru.index('"%s"' % k), i)
        # П1 влит: сравнения по словам «последний шаг не прошёл» больше нет
        for f in ("app.js", "i18n-ru.js", "i18n-en.js"):
            self.assertNotIn("до П1", read(f))
            self.assertNotIn("ps.afterMark", read(f))


class MtuViewTest(RootCase):
    def test_manual_view_gives_the_ends_of_the_bar(self):
        st = {"gateway": {"mtu": {"nic": {"dev": "eth0", "mtu": 1500}}, "inbounds": {
            "awg2": {"enabled": True, "port": 40000, "line": "2.0", "params": {"S4": "30", "MTU": "1280"}},
            "wg": {"enabled": True, "port": 51820}}}}
        v = gwmtu.manual_view(st, "awg2")
        self.assertEqual((v["nic"], v["budget"], v["overhead"], v["s4"], v["limit"]), (1500, 1400, 80, 30, 1290))
        w = gwmtu.manual_view(st, "wg")
        self.assertEqual((w["s4"], w["limit"]), (0, 1320))
        st["gateway"]["mtu"]["nic"]["mtu"] = 1380                          # узкая карта: бюджет — карта
        self.assertEqual(gwmtu.manual_view(st, "wg")["budget"], 1380)


if __name__ == "__main__":
    unittest.main()

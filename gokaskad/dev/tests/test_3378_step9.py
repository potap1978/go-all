"""3.3.7.8, правки по стенду T-3378 (шаг 9 R-3378-fix): заметное и мелочи — Н-4 (окно «Маскировка VLESS» не обещает
старые ссылки Reality при включённом сайте), Н-6 (строка «Reality — …» не повторяет надпись кнопки), Н-8 (вкладка на
домене теряет связь при выключении сайта и «Убрать домен» — не «Сайт не выключился»), Н-9 (после Ц2 — окно «Нужны новые
настройки», как после Ц4), Н-12 (бот и SSH-меню «Убрать домен»: цена Hysteria2 в вопросе, VLESS в итоге), Н-15
(«Идёт смена режима VLESS» без пустых скобок)."""

import json
import shutil
import unittest
from unittest import mock

from tests import jsparts
from tests import test_panel_vless_card as VC
from tests.test_panel_vless_card import APP_CONSTS, APP_FUNCS, STUBS, RevVless4Test, read, run_node

VTEXT = "режим VLESS — выданные раньше ссылки VLESS не подключатся. Выдайте их заново (меню 17 → 2 → 1): Телефон (papa)."
DEV = {"id": "d1", "name": "Телефон", "platform": "ios", "user_name": "papa"}

# siteSetMode / siteClear / vlSiteOn — их окружение (как RevVless4Test.test_site_off_by_the_read) и записи вызовов
SITE_PRE = r"""
const oneAtATime = (k, f) => f();
let SITE = { domain: "vpn.example.com", name: "vpn.example.com", mode: "site", use: { sstp: false, ocserv: false } };
const rd = (k) => (k === "site" ? SITE : R[k] && R[k].data);
let VIA = "port", authGate = false;
const tabVia = () => VIA;
const NOTES = [], FAILS = [], REISSUE = [], GOT = [];
const confirmDialog = async () => true;
const loadQuiet = async () => [];
const siteRun = async (k, b, fn) => { const x = await fn(); GOT.push(x); return x; };
const siteRefusal = () => false;
const siteFail = (e, title) => { FAILS.push(title); return true; };
const noticeDialog = async (o) => { NOTES.push(o); return true; };
const reissueDialog = (title, what, devs) => REISSUE.push([title, what, devs.map((d) => d.id)]);
const SITE_KIND = { sstp: "SSTP", ocserv: "OpenConnect", hy2: "Hysteria2" }, siteKinds = () => "", siteReissue = () => 0;
const SITE_RANDOM = { id: "random", name: "" }, SITE_STUB = "builtin_stub";
let siteTemplateChoice = SITE_RANDOM, sitePanelFull = "";
const net = () => Object.assign(new Error("Нет связи с панелью"), { status: 0, code: "net" });
let SITE_ANS = [];
API.site = { mode: async (b) => { CALLS.push(b); const a = SITE_ANS.shift(); if (a instanceof Error) throw a; return a; },
  clear: async (b) => { CALLS.push(b); const a = SITE_ANS.shift(); if (a instanceof Error) throw a; return a; } };
"""
SITE_FUNCS = ("siteSetMode", "siteClear", "vlSiteOn", "siteTabGone", "siteVlessQuiet", "siteVlessReissue")
SITE_CONSTS = ("vlTlsDevs", "siteModeBody", "siteVlessOf")


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class MaskKeptTest(unittest.TestCase):
    """Н-4: у сохранённой маски (VLESS сейчас на TLS) при включённом сайте Reality встанет на 8443 (443 держит сайт), а
    старые ссылки могли быть на 443 — «Старые ссылки Reality могут заработать» не обещаем (SPEC В10); сайт выключен —
    как было."""

    def test_kept_mask_with_the_site_on(self):
        run = RevVless4Test.harness(self, ("openVlessMask",), ("vlHost",), pre="const rd = (k) => R[k].data;\n")
        out = run(r"""
API.vless.masks = async () => ({ ok: true, masks: [] });
const rec = { security: "tls", port: 443, domain: "vpn.example.com", node_domain: "vpn.example.com", mask: "list", sni: "vk.com" };
const kept = (site) => { R.site = site; RADIO.splice(0); openVlessMask({ kind: "vless", devices: 2, record: rec });
  const it = RADIO.flat().find((x) => arr(x.chips).some((c) => c && c.chip === "vl.kept")); return it ? it.desc : null; };
console.log(JSON.stringify({ on: kept({ loaded: true, data: { mode: "site" } }), off: kept({ loaded: true, data: { mode: "domain" } }),
  unknown: kept({ loaded: false }) }));
""")
        self.assertEqual(out["on"], 'vl.kept.dSite{"port":8443}')      # без обещания: порт другой
        self.assertEqual(out["unknown"], out["on"])                     # TLS — перед сайтом: не знаем — не обещаем
        self.assertEqual(out["off"], "vl.kept.d")                       # сайт выключен — как было

    def test_texts(self):
        for lang, word in (("ru", "уйдёт с 443 на {port}"), ("en", "moves from 443 to {port}")):
            src = read("i18n-%s.js" % lang)
            line = [x for x in src.splitlines() if '"vl.kept.dSite"' in x]
            self.assertEqual(len(line), 1, lang)
            self.assertIn(word, line[0])
            self.assertNotIn("заработа", line[0])
            self.assertNotIn("work again", line[0])


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class FixRowTest(unittest.TestCase):
    """Н-6: «Reality — выбран вами …» с кнопкой «Перейти на TLS» — второй строкой слова ядра fix.text («Перейти на TLS»)
    не повторяем; то же у «Включите сайт на домене» рядом с «Включить сайт на домене»; у остальных (где это делается) —
    как было. Карточка и телефон — одна строка (vlTop)."""

    def test_no_second_line_that_repeats_the_button(self):
        js = read("app.js")
        parts = "\n".join([STUBS] + [jsparts.const(js, n) for n in APP_CONSTS] + [jsparts.function(js, n) for n in APP_FUNCS])
        out = run_node(parts + r"""
const base = { security: "reality", sni: "www.microsoft.com", mask: "list", node_domain: "vpn.example.net", port: 8443 };
const R0 = {
  chosen: Object.assign({}, base, { reason: "chosen", consent: { at: "2026-10-24T03:03:00Z", ok: true }, fix: { do: "mode_tls", text: "Перейти на TLS" } }),
  failed: Object.assign({}, base, { reason: "tls_failed", forced: true, tls_error: { why: "x" }, fix: { do: "mode_tls", text: "Перейти на TLS" } }),
  no_site: Object.assign({}, base, { reason: "no_site", forced: true, fix: { do: "site_on", text: "Включите сайт на домене" } }),
  no_domain: Object.assign({}, base, { node_domain: "", reason: "no_domain", forced: true, fix: { do: "domain", text: "Добавьте домен: Настройки → Домен и сайт" } }),
  consent_bad_row: Object.assign({}, base, { reason: "chosen", fix: { do: "consent", text: "Подтвердите Reality или перейдите на TLS" } }),
};
const o = {};
for (const k of Object.keys(R0)) { const n = flat(vlTop({ kind: "vless", title: "VLESS", record: R0[k], devices: 2 })); o[k] = { b: n.kids[1].kids.map((x) => x.text), btns: n.kids.length > 2 ? n.kids[2].kids.map((x) => x.btn) : [] }; }
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["chosen"]["b"], ['Reality — vl.reason.chosen{"date":"day:2026-10-24T03:03:00Z"}'])
        self.assertEqual(out["chosen"]["btns"], ["vl.toTls"])
        self.assertEqual(len(out["failed"]["b"]), 1)
        self.assertEqual(out["failed"]["btns"], ["vl.fix.retry"])
        # слова ядра («Включите сайт на домене») кнопку повторяют — их нет; вместо них подпись макета К4 — что даст кнопка
        # (DQA-3378 К4)
        self.assertEqual(out["no_site"]["b"], ["Reality — vl.reason.no_site", "vl.fix.siteOnSub"])
        self.assertEqual(out["no_site"]["btns"], ["vl.fix.siteOn"])
        # слова ядра говорят больше надписи — остаются
        self.assertEqual(out["no_domain"]["b"][1], "Добавьте домен: Настройки → Домен и сайт")
        self.assertEqual(out["consent_bad_row"]["b"][1], "Подтвердите Reality или перейдите на TLS")    # кнопки нет

    def test_phone_uses_the_same_row(self):
        # Т3 (Б r3): телефон — та же строка списка, что на компьютере; плашка — vlTop в раскрытой строке (vlessInfo)
        js = read("app.js")
        self.assertIn("vlTop(r)", jsparts.function(js, "vlessInfo"))
        self.assertIn("vlessInfo(r, !fold)", jsparts.function(js, "ibUxCard"))
        self.assertIn("PAGES.inbounds.build(root, n);", js[js.index("PAGES.inbounds.phone = function"):][:400])


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class SiteTabTest(unittest.TestCase):
    """Н-8 и Н-9 (и панель после Н-12): выключение сайта и «Убрать домен» из вкладки на домене — обрыв после запроса
    не сбой; VLESS сменил режим с сайтом или доменом — окно «Нужны новые настройки», строка ядра с путём SSH-меню из
    предупреждений уходит."""

    def run_js(self, body):
        stubs = VC.STUBS.replace(", vlSiteOn = () => 0", "")              # настоящий vlSiteOn
        self.assertNotEqual(stubs, VC.STUBS)
        with mock.patch.object(VC, "STUBS", stubs):
            run = RevVless4Test.harness(self, SITE_FUNCS, SITE_CONSTS, pre=SITE_PRE)
        return run(body)

    def test_site_off_from_the_domain_tab_is_not_a_failure(self):
        out = self.run_js(r"""
const o = {};
VIA = "proxy"; ASKS.push({ ok: true, need: null }); SITE_ANS.push(net());
await siteSetMode(false); o.proxy = { notes: NOTES.splice(0).map((n) => [n.title, n.text, n.tone]), fails: FAILS.splice(0) };
VIA = "port"; ASKS.push({ ok: true, need: null }); SITE_ANS.push(net());
await siteSetMode(false); o.port = { notes: NOTES.splice(0).length, fails: FAILS.splice(0) };
VIA = "proxy"; ASKS.push({ ok: true, need: null }); SITE_ANS.push(net());
await siteSetMode(true); o.on = { notes: NOTES.splice(0).length, fails: FAILS.splice(0) };
VIA = "proxy"; ASKS.push({ ok: true, need: null }); SITE_ANS.push(Object.assign(new Error("500"), { status: 500, code: "x" }));
await siteSetMode(false); o.http = { notes: NOTES.splice(0).length, fails: FAILS.splice(0) };
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["proxy"], {"notes": [["site.maskOffGoingT", "site.maskOffGoing", "warning"]], "fails": []})
        self.assertEqual(out["port"], {"notes": 0, "fails": ["site.maskOffFailT"]})      # туннель, порт — как было
        self.assertEqual(out["on"], {"notes": 0, "fails": ["site.maskOnFailT"]})         # включение — как было
        self.assertEqual(out["http"], {"notes": 0, "fails": ["site.maskOffFailT"]})      # ответ сервера — сбой

    def test_clear_from_the_domain_tab_is_not_a_failure(self):
        out = self.run_js(r"""
const o = {};
VIA = "proxy"; ASKS.push({ ok: true, need: null }); SITE_ANS.push(net());
let threw = "";
try { await siteClear(); } catch (e) { threw = e.message; }
o.proxy = { notes: NOTES.splice(0).map((n) => n.title), threw };
VIA = "port"; ASKS.push({ ok: true, need: null }); SITE_ANS.push(net()); threw = "";
try { await siteClear(); } catch (e) { threw = e.message; }
o.port = { notes: NOTES.splice(0).length, threw };
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["proxy"], {"notes": ["site.clearGoingT"], "threw": ""})
        self.assertEqual(out["port"], {"notes": 0, "threw": "Нет связи с панелью"})     # act покажет «Нет связи», как было

    def test_site_on_with_vless_reissue_shows_the_reissue_window(self):
        out = self.run_js(r"""
const VT = %s, DEV = %s;
const o = {};
R.inbounds = { loaded: true, data: [{ kind: "vless", title: "VLESS", installed: true, devices: 1, record: { security: "reality" } }] };
ASKS.push({ ok: true, need: null });
SITE_ANS.push({ ok: true, template: { name: "T" }, warnings: ["W1", VT], vless: { changed: true, security: "tls", reissue: [DEV], text: VT } });
await siteSetMode(true); o.on = { warnings: GOT.splice(0)[0].warnings, reissue: REISSUE.splice(0) };
// без смены режима VLESS — окна нет, предупреждения как есть
ASKS.push({ ok: true, need: null }); SITE_ANS.push({ ok: true, warnings: ["W1"], vless: { changed: false, reissue: [], text: "" } });
await siteSetMode(true); o.same = { warnings: GOT.splice(0)[0].warnings, reissue: REISSUE.splice(0) };
// выключение во вкладке через туннель — то же окно; во вкладке на домене — нет (вкладка закрывается)
VIA = "port"; ASKS.push({ ok: true, need: null });
SITE_ANS.push({ ok: true, warnings: [VT], vless: { changed: true, security: "reality", reissue: [DEV], text: VT } });
await siteSetMode(false); o.offPort = { warnings: GOT.splice(0)[0].warnings, reissue: REISSUE.splice(0) };
VIA = "proxy"; ASKS.push({ ok: true, need: null });
SITE_ANS.push({ ok: true, warnings: [VT], vless: { changed: true, security: "reality", reissue: [DEV], text: VT } });
await siteSetMode(false); o.offProxy = { reissue: REISSUE.splice(0), notes: NOTES.splice(0).map((n) => n.title) };
// «Включить сайт на домене» из карточки VLESS (vlSiteOn) — то же окно
ASKS.push({ ok: true, need: null });
SITE_ANS.push({ ok: true, warnings: [VT], vless: { changed: true, security: "tls", reissue: [DEV], text: VT } });
const res = await vlSiteOn({ kind: "vless", title: "VLESS", record: {} });
o.card = { warnings: res.warnings, reissue: REISSUE.splice(0), toasts: TOASTS.splice(0).map((x) => x[0]) };
// «Убрать домен»: VLESS ушёл на Reality (ядро после Н-12) — то же окно
VIA = "port"; ASKS.push({ ok: true, need: null });
SITE_ANS.push({ ok: true, affected: [], kept: [], warnings: [VT], vless: { changed: true, security: "reality", reissue: [DEV], text: VT } });
await siteClear(); o.clear = { warnings: GOT.splice(0)[0].warnings, reissue: REISSUE.splice(0) };
console.log(JSON.stringify(o));
""" % (json.dumps(VTEXT, ensure_ascii=False), json.dumps(DEV, ensure_ascii=False)))
        win = [["VLESS", "vl.whatMode", ["d1"]]]
        self.assertEqual(out["on"], {"warnings": ["W1"], "reissue": win})
        self.assertEqual(out["same"], {"warnings": ["W1"], "reissue": []})
        self.assertEqual(out["offPort"], {"warnings": [], "reissue": win})
        self.assertEqual(out["offProxy"], {"reissue": [], "notes": ["site.maskOffDone"]})
        self.assertEqual(out["card"]["warnings"], [])
        self.assertEqual(out["card"]["reissue"], win)
        self.assertIn("site.maskOnDone", out["card"]["toasts"])
        self.assertEqual(out["clear"], {"warnings": [], "reissue": win})

    def test_texts(self):
        for lang in ("ru", "en"):
            src = read("i18n-%s.js" % lang)
            for k in ("site.maskOffGoingT", "site.maskOffGoing", "site.clearGoingT", "site.clearGoing"):
                self.assertEqual(src.count('"%s":' % k), 1, (lang, k))
        ru = read("i18n-ru.js")
        line = [x for x in ru.splitlines() if '"site.maskOffGoing":' in x][0]
        self.assertIn("SSH-туннель", line)
        self.assertNotIn("не выключился", line)


# ── Н-12: «Убрать домен» — бот, SSH-меню, ядро ────────────────────────────────────────────────────────────────────

import re                                                                    # noqa: E402
import subprocess                                                            # noqa: E402

from kaskad import state                                                     # noqa: E402
from kaskad.site import model as smodel                                      # noqa: E402
from kaskad.site import ops as site_ops                                      # noqa: E402
from tests.fakes_gw import INSTALL_SH                                        # noqa: E402
from tests.test_3351_cmdinj import function                                  # noqa: E402
from tests.test_vless_sh3 import _Ctx                                        # noqa: E402
from tests.test_vless_want import WantCase                                   # noqa: E402

NO_LINKS = {"domain": "", "count": 0, "port": True}       # the panel is not on the domain: the port stays as it is
HY2_RU = "Hysteria2 вернётся на самоподписанный сертификат — ссылки придётся выдать заново (устройств: 2)"


class ClearVlessTest(WantCase):
    """Н-12: «Убрать домен» при VLESS на TLS этого домена с устройствами — в итоге (ядро, бот; SSH-меню и CLI печатают
    те же warnings) строка ядра «… ссылки VLESS не подключатся. Выдайте их заново …: Телефон» и «vless», как у «Сменить
    домен»; в вопросе бота — цена Hysteria2 на сертификате домена, как первая строка панели (site.clearHy2)."""

    def setUp(self):
        super().setUp()
        from kaskad.bot import site as bsite
        self.bsite = bsite
        p = mock.patch.object(site_ops, "people_links", return_value=dict(NO_LINKS))
        p.start()
        self.addCleanup(p.stop)
        site_ops.mode(self.w.runner, "site", yes=True)
        self.assertEqual(self.rec()["security"], "tls")
        self.ctx = _Ctx(self.w.runner)

    def press(self, data):
        from kaskad.bot import registry
        registry.load()
        fn, _ = registry.find_callback(data)
        self.assertIsNotNone(fn, data)
        fn(self.ctx, data)

    def test_the_core_answer_names_whom_to_give_vless_again(self):
        res = site_ops.clear(self.w.runner, way_in=True, yes=True)
        self.assertEqual(smodel.domain_of(state.load()), "")
        self.assertEqual(self.rec()["security"], "reality")
        self.assertTrue(res["vless"]["changed"])
        self.assertEqual([d["name"] for d in res["vless"]["reissue"]], ["Телефон"])
        lines = [w for w in res.get("warnings") or [] if "ссылки VLESS не подключатся" in w]
        self.assertEqual(len(lines), 1, res.get("warnings"))
        self.assertIn("Телефон", lines[0])
        self.assertEqual(lines[0], res["vless"]["text"])

    def test_the_bot_says_it_after_yes(self):
        self.bsite.cb_clear_ask(self.ctx, "sdx")
        from kaskad.gw import ops as gops
        key = self.bsite._price_key(gops._reissue_devices(state.load(), "vless"))   # REV-3378-fix придирка 4
        self.assertEqual(self.ctx.buttons(), ["sdxy:Y" + key, "sd"])
        self.press("sdxy:Y" + key)
        text = self.ctx.said[-1][0]
        self.assertIn("Домен убран", text)
        self.assertIn("ссылки VLESS не подключатся", text)
        self.assertIn("Телефон", text.split("ссылки VLESS не подключатся", 1)[1])

    def test_the_bot_question_names_the_price_of_hysteria2(self):
        from kaskad.gw import ops as gops
        got = {}
        for name, doc in (("domain", {"ok": True, "installed": True, "cert_mode": "domain", "devices": 2}),
                          ("self", {"ok": True, "installed": True, "cert_mode": "self", "devices": 2}),
                          ("none", {"ok": True, "installed": False}),
                          ("zero", {"ok": True, "installed": True, "cert_mode": "domain", "devices": 0})):
            with mock.patch.object(gops, "hy2_show", return_value=doc):
                self.bsite.cb_clear_ask(self.ctx, "sdx")
            got[name] = self.ctx.said[-1][0]
        with mock.patch.object(gops, "hy2_show", side_effect=OSError("boom")):
            self.bsite.cb_clear_ask(self.ctx, "sdx")                 # не прочиталось — без строки, вопрос задан
        got["broken"] = self.ctx.said[-1][0]
        self.assertIn(HY2_RU, got["domain"])
        self.assertLess(got["domain"].index(HY2_RU), got["domain"].index("выдать заново (устройств — 1"))   # до цены VLESS
        for k in ("self", "none", "zero", "broken"):
            self.assertNotIn("Hysteria2", got[k], k)
            self.assertIn("Убрать домен", got[k], k)

    def test_texts(self):
        import os
        cat = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "i18n", "catalog")
        for lang in ("ru", "en"):
            with open(os.path.join(cat, lang, "bot.json"), encoding="utf-8") as f:
                line = json.load(f)["bot.site.clear_hy2"]
            self.assertIn("{n}", line)
            self.assertIn("Hysteria2", line)


def bash(src, env=None):
    r = subprocess.run(["bash", "-c", src], capture_output=True, text=True, timeout=60,
                       env=dict({"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "LC_ALL": "C.UTF-8"},
                                **(env or {})))
    return r.stdout + r.stderr


@unittest.skipUnless(shutil.which("jq"), "нет jq")
class MenuClearHy2Test(unittest.TestCase):
    """Н-12, SSH-меню «19 → 5) Убрать домен»: Hysteria2 на сертификате домена с устройствами — цена в вопросе
    (kaskadctl gw hy2 show); самоподписанный, нет Hysteria2 или не прочиталось — строки нет. Итог печатает .warnings
    ядра (k3_site_warns) — там после Н-12 и строка VLESS (ClearVlessTest)."""

    STUBS = r"""
t() { printf '%s' "$1"; }
tf() { local k=$1; shift; printf '%s' "$k:$*"; }
k3_gw_head() { :; }
k3_gw_fold() { printf '%s\n' "$2"; }
k3_gw_pause() { :; }
k3_choose_yes() { echo "Q:$1"; return 1; }
k3_gw() { echo "CALL:$*" >&2; K3_GW_OUT=$HY2; return ${HY2_RC:-0}; }
K3_SITE_NAME=vpn.example.com K3_SITE_MODE=site K3_SITE_SSTP=false K3_SITE_OCS=false K3_SITE_PWARN="" K3_SITE_HOSTDOM=false
WHITE="" NC="" YELLOW="" GREEN="" RED=""
"""

    def run_menu(self, hy2, rc=0):
        src = function("k3_jq") + function("k3_site_clear") + self.STUBS + "k3_site_clear\n"
        return bash(src, {"HY2": json.dumps(hy2), "HY2_RC": str(rc)})

    def test_the_price_of_hysteria2_in_the_question(self):
        out = self.run_menu({"ok": True, "installed": True, "cert_mode": "domain", "devices": 2})
        self.assertIn("CALL:hy2 show", out)
        self.assertIn("  • SITE_CLR_HY2:2", out)
        self.assertLess(out.index("SITE_CLR_HY2"), out.index("Q:SITE_CLR_Q"))
        for doc, rc in (({"ok": True, "installed": True, "cert_mode": "self", "devices": 2}, 0),
                        ({"ok": True, "installed": False}, 0),
                        ({"ok": True, "installed": True, "cert_mode": "domain", "devices": 0}, 0),
                        ({"ok": False, "error": "x"}, 1)):
            out = self.run_menu(doc, rc)
            self.assertNotIn("SITE_CLR_HY2", out, doc)
            self.assertIn("Q:SITE_CLR_Q", out, doc)

    def test_texts(self):
        with open(INSTALL_SH, encoding="utf-8") as f:
            src = f.read()
        for lang in ("ru", "en"):
            m = re.findall(r"^M_%s_SITE_CLR_HY2='([^']*)'$" % lang, src, re.M)
            self.assertEqual(len(m), 1, lang)
            self.assertEqual(m[0].count("%s"), 1)
            self.assertIn("Hysteria2", m[0])


# ── Н-15: «Идёт смена режима VLESS» без «(начата —)» ──────────────────────────────────────────────────────────────

import threading                                                             # noqa: E402

from kaskad.errors import ConflictError                                      # noqa: E402
from kaskad.gw import ops as gops                                            # noqa: E402
from kaskad.site import front                                                # noqa: E402


class BusyTextTest(WantCase):
    """Н-15: замок держит переход без записи времени (выключение сайта, маска) — отказ без пустых скобок; время записано
    (переход R→T, mode_pending.at) — со временем, как было; data.at — то же."""

    def held(self, fn):
        inside, done = threading.Event(), threading.Event()

        def holder():
            with front.vless_guard(self.w.runner):
                inside.set()
                done.wait(10)
        t = threading.Thread(target=holder)
        t.start()
        try:
            self.assertTrue(inside.wait(10))
            return fn()
        finally:
            done.set()
            t.join(10)

    def test_no_empty_brackets_without_the_time(self):
        def go():
            with self.assertRaises(ConflictError) as cm:
                gops.vless_mode(self.w.runner, "tls", yes=True)
            return cm.exception
        e = self.held(go)
        self.assertEqual(str(e), "Идёт смена режима VLESS — повторите, когда закончится")
        self.assertEqual((e.data["need"], e.data["at"]), ("busy", None))

    def test_the_time_when_it_is_written(self):
        def go():
            self.put(mode_pending={"at": "2026-10-24T03:03:00Z", "pid": 1, "snap": {"rec": {}, "site": "site"}})
            return front._busy()
        e = self.held(go)
        self.assertEqual(str(e), "Идёт смена режима VLESS (начата 2026-10-24T03:03:00Z) — повторите, когда закончится")
        self.assertEqual(e.data["at"], "2026-10-24T03:03:00Z")


if __name__ == "__main__":
    unittest.main()

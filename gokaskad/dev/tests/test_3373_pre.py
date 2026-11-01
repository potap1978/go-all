"""3.3.7.3 перед стендом (REV-3373): плитка «Протоколы» на «Обзоре» при негодном сертификате Hysteria2 (З-2), плитка
выходов при всех выключенных (придирка 1), кнопка «Применить настройки» на карточке Hysteria2 (З-3) — модель в node."""

import os
import unittest

from tests import jsparts
from tests.test_panel_f0 import APP, HAS_NODE, read, run_node, window_with


@unittest.skipUnless(HAS_NODE, "node")
class OverviewTilesTest(unittest.TestCase):
    def run_model(self, inbounds, exits):
        parts = "\n".join([jsparts.const(APP, "ibSessions"), jsparts.function(APP, "ibState"),
                           jsparts.function(APP, "ibSummary"), jsparts.const(APP, "exAbRows"), jsparts.function(APP, "exSummary")])
        return run_node(window_with("ru", ["i18n-ru.js"]) + """
const DICT = window.KASKAD_I18N, t = (k) => (DICT[k] === undefined ? "MISSING:" + k : DICT[k]);
const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v), known = (v) => v !== null && v !== undefined;
const DASH = "—", gwEnabled = () => true, liveOff = () => false, lvInb = () => null, exitState = (r) => r.st;
const R = { inbounds: { loaded: true, data: %s }, exits: { loaded: true, data: { rows: %s } } };
%s
console.log(JSON.stringify([R.inbounds.data.map((r) => ibState(r).key), ibSummary(), exSummary()]));""" % (inbounds, exits, parts))

    def test_a_bad_certificate_is_down_on_the_overview(self):
        """З-2: служба идёт, сертификат негоден — «не работает», как карточка; счёт «Протоколы» — тот же."""
        ib = """[{ kind: "hy2", available: true, installed: true, enabled: true, running: true, fail: { why: "cert", cert: "unreadable" } },
                 { kind: "vless", available: true, installed: true, enabled: true, running: true, fail: null },
                 { kind: "wg", available: true, installed: true, enabled: true, running: true, fail: { why: "closed" } }]"""
        out = run = self.run_model(ib, "[]")
        self.assertEqual(out[0], ["down", "run", "run"])
        self.assertEqual((run[1]["running"], run[1]["down"], run[1]["enabled"]), (2, 1, 3))

    def test_all_exits_off_is_not_no_exits(self):
        """Придирка 1: все выходы выключены — плитка не зовёт «Добавить выход»."""
        out = self.run_model("[]", '[{ id: "e-1", enabled: false, st: "alive" }, { id: "e-2", enabled: false, st: "dead" }]')
        self.assertEqual((out[2]["total"], out[2]["all"]), (0, 2))
        out = self.run_model("[]", "[]")
        self.assertEqual((out[2]["total"], out[2]["all"]), (0, 0))

    def test_the_tile_says_all_off(self):
        """Все выходы выключены — не «выходов нет». Было: плитка «Выходы» старой «Сводки» (ovTiles, REV-it1 №4 — снята как
        недостижимая). Живая Сводка (часть ovb): «Выходы · час» берёт все выходы, и выключенные (exAbRows, за ними exRuRows
        с меткой РФ — решение 6; без отбора по enabled), со словом состояния «выключен»; «Выходов нет» (ovb.xnNone) — только при пустом списке.
        Поведение в node — tests/panel/test_ovb_live.SummaryLiveTest.test_exits_all_off_is_not_no_exits."""
        ctx = jsparts.const(APP, "OVB_CTX")
        self.assertIn("exits: () => exAbRows().concat(exRuRows()).map((r) => { const w = exitWord(r);", ctx)
        self.assertIn('if (!r.enabled) return { text: t("ex.offForGw"), tone: "off" };', jsparts.function(APP, "exitWord"))
        self.assertIn('if (!xs.length) return el("section", { class: "gx-card", "aria-label": t("ovb.xnT") }, head, '
                      'el("p", { class: "kb-empty", text: t("ovb.xnNone") }));', read("pages/ovb.js"))


@unittest.skipUnless(HAS_NODE, "node")
class Hy2ApplyCardTest(unittest.TestCase):
    def test_a_bad_certificate_offers_apply_not_restart(self):
        """З-3: при why=cert — «Применить настройки» и «Журнал»; иная поломка — по-прежнему «Перезапустить»."""
        from tests.test_333d_ux import prelude
        out = run_node(prelude("en") + r"""
const base = { kind: "hy2", title: "Hysteria2", engine: "xray", desc: "d", enabled: true, devices: 1, ports: ["443/udp"], fixedPort: false };
const m = (o) => X.protoModel(Object.assign({}, base, o), "ru");
console.log(JSON.stringify([m({ running: true, fail: { why: "cert", cert: "unreadable", human: "w" } }),
  m({ running: false, fail: { why: "cert", line: "x509", human: "w" } }),
  m({ running: false, fail: { why: "other", line: "weird", human: "" } })].map((x) => [x.state, x.buttons])));""")
        self.assertEqual(out, [["other", ["apply", "log"]], ["other", ["apply", "log"]], ["other", ["restart", "log"]]])

    def test_the_panel_wires_apply_with_a_confirmation(self):
        app = APP
        fn = jsparts.function(app, "hy2Apply")
        self.assertIn("confirmDialog(", fn)
        self.assertIn("API.hy2.apply()", fn)
        self.assertNotIn("rekey", fn)
        self.assertIn('onApply: r.kind === "hy2" ? () => hy2Apply(r) : null', app)
        self.assertIn('request("POST", "/api/gw/hy2/apply"', app)
        for lang, word in (("ru", "Применить настройки"), ("en", "Apply settings")):
            self.assertIn('"pc.apply": "%s"' % word, read("ux-i18n-%s.js" % lang))
            dic = read("i18n-%s.js" % lang)
            for key in ("h2.applyQ", "h2.applyTextSelf", "h2.applyTextDomain", "h2.applyDone"):
                self.assertIn('"%s"' % key, dic)

    def test_apply_says_whom_and_who_goes_offline(self):
        # REV-3373-pre 1–3: the answer's «reissue» goes to the dialog «кому выдать ключи» (not the toast); xray restarts
        # always — no «if the file had to be rewritten»; it drops VLESS, SOCKS5 and Hysteria2 too, not only the exits
        fn = jsparts.function(APP, "hy2Apply")
        self.assertIn("hy2Done(r, res,", fn)
        self.assertIn("if (devs.length) reissueDialog(", jsparts.function(APP, "hy2Done"))
        for lang, gone, words in (("ru", ("Если файл пришлось переписать", "Сертификат тот же — ключи устройств прежние"),
                                   ("VLESS, SOCKS5 и Hysteria2", "связь через выходы", "кому выдать ключи заново")),
                                  ("en", ("If the file had to be rewritten", "It is the same certificate: the devices"),
                                   ("VLESS, SOCKS5 and Hysteria2", "connections through the exits", "whom to give new keys"))):
            dic = read("i18n-%s.js" % lang)
            for key in ("h2.applyTextSelf", "h2.applyTextDomain"):
                line = [ln for ln in dic.splitlines() if ln.strip().startswith('"%s"' % key)][0]
                for w in words:
                    self.assertIn(w, line, (lang, key))
                for w in gone:
                    self.assertNotIn(w, line, (lang, key))
        readme = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.md"),
                      encoding="utf-8").read()
        self.assertNotIn("ключи устройств останутся прежними", readme)
        self.assertNotIn("the server writes the certificate again, the devices keep their keys", readme)


if __name__ == "__main__":
    unittest.main()

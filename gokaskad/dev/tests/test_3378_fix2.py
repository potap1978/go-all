"""3.3.7.8, правки по проверке REV-3378-fix.

С1: цена перехода на TLS при выключенном сайте говорит, что сайт на домене включится (слова ``site mode site``), —
в отказе ``vless_to_tls`` (CLI, SSH-меню, MCP, мастер), в рекомендации ``gw vless show`` (бот, мастер, SSH-меню) и в
строке после первого сертификата; в ответе признак ``site_on`` — по нему панель ставит строку о сайте одну."""

import json
import shutil
import unittest
from unittest import mock

from kaskad import state
from kaskad.errors import ConflictError
from kaskad.gw import model as gm
from kaskad.gw import ops as gops
from kaskad.i18n import tr, use_lang
from kaskad.site import front
from kaskad.site import model as site_model
from kaskad.site import ops as site_ops
from tests import jsparts
from tests.test_3378_fix import NO_LINKS
from tests.test_panel_vless_card import read, run_node
from tests.test_vless_want import WantCase

SITE_RU = "Сайт включится, VLESS перейдёт на TLS со своим доменом — ссылки VLESS выдать заново (устройств — 1: "
SITE_EN = "The site will go on, VLESS will switch to TLS with your own domain — give the VLESS links out again (devices — 1: "


class TlsPriceSiteTest(WantCase):
    """WantCase: VLESS Reality on 443, want tls, one device, a domain with a certificate of LE, the site never on (the
    state of T-3378 Н-17) — and the same with the site on (Reality chosen next to it, then the consent gone)."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(site_ops, "people_links", return_value=dict(NO_LINKS))
        p.start()
        self.addCleanup(p.stop)
        self.put(want="tls")

    def refusal(self, **kw):
        with self.assertRaises(ConflictError) as cm:
            gops.vless_to_tls(self.w.runner, dry=True, **kw)
        return cm.exception.data, str(cm.exception)

    def site_on_reality(self):
        """The site on, VLESS on Reality next to it (8443) and want tls: TLS is had without the site going on."""
        site_ops.mode(self.w.runner, "site", yes=True)
        self.ok("gw", "vless", "mode", "reality", "--site", "ya.ru", "--accept-risks", "--risks", "r1a", "--yes")
        st = state.load()
        gm.inbound(st, "vless")["want"] = "tls"
        gm.inbound(st, "vless").pop("reality_consent", None)
        state.save(st)
        self.assertEqual((site_model.peek(state.load())["mode"], self.rec()["security"]), ("site", "reality"))

    def test_the_price_says_the_site_goes_on(self):
        data, text = self.refusal()
        self.assertTrue(text.startswith(SITE_RU), text)
        self.assertEqual((data["need"], data["text"], data["site_on"], len(data["devices"])),
                         ("vless_change", text, False, 1))
        ask = gops.vless_ask(self.w.runner, "tls")                            # the panel reads it before the action
        self.assertEqual((ask["need"], ask["site_on"], ask["message"]), ("vless_change", False, text))
        with use_lang("en"):
            _, en = self.refusal()
        self.assertTrue(en.startswith(SITE_EN), en)
        before = json.dumps(state.load(), sort_keys=True)
        rc, out, err = self.run_cli("gw", "vless", "mode", "tls")              # CLI (MCP and the SSH menu: its text)
        self.assertEqual(rc, 3, out + err)
        self.assertIn(SITE_RU, out + err)
        self.assertEqual(json.dumps(state.load(), sort_keys=True), before)    # a price: nothing done
        self.assertEqual(site_model.peek(state.load())["mode"], "domain")

    def test_the_recommendation_says_it_too(self):
        rc, doc, err = self.run_json("gw", "vless", "show")
        self.assertEqual(rc, 0, err)
        self.assertEqual(doc["reason"], "ready")
        self.assertEqual(doc["recommend"]["text"], "Можно перейти на TLS со своим доменом. " + self.refusal()[1])
        rc, out, err = self.run_cli("gw", "vless", "show")
        self.assertIn("Можно перейти на TLS со своим доменом. " + SITE_RU, out)
        from kaskad.bot import site as bsite
        self.assertIn(SITE_RU, " ".join(bsite.vless_lines(state.load())))   # the bot
        from kaskad.setup import steps
        rec = steps.vless_now()["recommend"]["text"]                          # the wizard: «head. body» of М2
        head, body = rec.split(". ", 1)
        self.assertEqual(head, "Можно перейти на TLS со своим доменом")
        self.assertTrue(body.startswith(SITE_RU), body)
        with use_lang("en"):
            en = gops.vless_view(state.load())["recommend"]["text"]
        self.assertEqual(en.split(". ", 1)[0], "You can switch to TLS with your own domain")
        self.assertIn(SITE_EN, en)

    def test_dqa3378_the_recommendation_names_whom(self):
        """DQA-3378 М-1: рекомендация называет устройства теми же строками, что цена перехода (окно цены панели) —
        плашка показывает имена сразу, без «Людей»; без устройств — пустой список."""
        rec = gops.vless_view(state.load())["recommend"]
        data, _ = self.refusal()
        self.assertEqual(rec["reissue"], data["devices"])
        self.assertEqual((rec["devices"], len(rec["reissue"])), (1, 1))
        self.assertEqual(set(rec["reissue"][0]), {"id", "name", "platform", "user", "user_name"})   # без ключей и ссылок
        st = state.load()
        for d in gm.devices(st):
            d["access"] = [k for k in (d.get("access") or []) if k != "vless"]
        state.save(st)
        rec = gops.vless_view(state.load())["recommend"]                      # без устройств TLS ставится сам — или пусто
        self.assertTrue(rec is None or (rec["devices"], rec["reissue"]) == (0, []), rec)

    def test_dqa3378_no_price_line_without_devices(self):
        """Макет М3: устройств нет — в тексте рисков нет пункта цены, и «ссылки, выданные сейчас, не изменятся» тоже
        (Reality уже, та же маска и порт); с устройством — как было."""
        st = state.load()
        one = front.risks_data(st, devices=1)
        self.assertIn("не изменятся", one["text"])
        none = front.risks_data(st, devices=0)
        self.assertEqual(none["price"], "")
        self.assertNotIn("не изменятся", none["text"])
        self.assertIn("(ссылки — выдать заново).\n\n", none["text"])                           # список кончается точкой
        self.assertEqual((none["ver"], none["site"]), (one["ver"], one["site"]))                 # согласие — то же

    def test_the_site_on_keeps_the_old_words(self):
        self.site_on_reality()
        data, text = self.refusal()
        self.assertTrue(data["site_on"])
        self.assertTrue(text.startswith("VLESS перейдёт на TLS со своим доменом. Ссылки VLESS, выданные раньше"), text)
        self.assertNotIn("Сайт включится", text)
        rec = gops.vless_view(state.load())["recommend"]["text"]
        self.assertEqual(rec, "Можно перейти на TLS со своим доменом. Цена: ключи VLESS нужно выдать заново "
                              "(1 устройство).")

    def test_legacy_with_the_site_off_says_it(self):
        """A record of 3.3.7 (no ``want``) on Reality with the site off: ``gw vless mode tls`` switches the site on."""
        st = state.load()
        gm.inbound(st, "vless").pop("want", None)
        state.save(st)
        self.assertEqual(front.reason(state.load(), self.w.runner)["reason"], "legacy")
        self.assertIn(SITE_RU, gops.vless_view(state.load())["recommend"]["text"])
        self.assertFalse(self.refusal()[0]["site_on"])

    def test_after_the_first_certificate(self):
        """§3.3 with devices: the line after the certificate — the site always off there — says it goes on."""
        line = gops._after_cert_recommend(state.load(), gops._reissue_devices(state.load(), "vless"))["line"]
        self.assertIn("Цена: на домене включится сайт, ключи VLESS нужно выдать заново (1 устройство: ", line)
        with use_lang("en"):
            en = gops._after_cert_recommend(state.load(), gops._reissue_devices(state.load(), "vless"))["line"]
        self.assertIn("The price: a site goes on at the domain and the VLESS keys have to be given out again (1 device: ", en)

    def test_the_words_are_one_text_in_both_languages(self):
        for lang in ("ru", "en"):
            with use_lang(lang):
                self.assertIn("{price}", tr("gw_in_vless.recommend_site", price="{price}"))


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class PanelTlsPriceSiteLineTest(unittest.TestCase):
    """Окно цены «Перейти на TLS» (vlToTls): строка «включится сайт» — по site_on ядра; нет признака (старое ядро) —
    по причине и R.site, как раньше. Строка одна."""

    def lines(self, dd, reason="ready", site=None):
        src = jsparts.function(read("app.js"), "vlToTls")
        return run_node(r"""
const t = (k) => k, tn = (k) => k, arr = (x) => (Array.isArray(x) ? x : []), isObj = (x) => !!x && typeof x === "object";
const DASH = "—", vlRec = (r) => r.record, vlDomain = () => "vpn.example.com";
const SITE = %s, R = { site: { loaded: SITE !== null } }, rd = () => SITE;
const API = { vless: { mode: async () => ({ ok: true }) } };
let LINES = null;
const vlRun = async (call, o) => { LINES = o.price(%s).lines.filter(Boolean).map((x) => x[0]); return null; };
%s
(async () => { await vlToTls({ record: { reason: %s }, devices: 1 }); console.log(JSON.stringify(LINES)); })();
""" % (json.dumps(site), json.dumps(dd), src, json.dumps(reason)))

    def test_by_the_core(self):
        dev = [{"id": "d1", "name": "Телефон"}]
        off = self.lines({"devices": dev, "site_on": False}, site={"mode": "site"})     # R.site stale: the core wins
        self.assertEqual(off.count("vl.r2t.site"), 1)
        on = self.lines({"devices": dev, "site_on": True}, reason="no_site")
        self.assertNotIn("vl.r2t.site", on)
        self.assertEqual(self.lines({"devices": dev}, site={"mode": "domain"}).count("vl.r2t.site"), 1)   # old core
        self.assertNotIn("vl.r2t.site", self.lines({"devices": dev}, site=None))          # nothing known — as before


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class ClearTwoWindowsTest(unittest.TestCase):
    """Придирка 1: «Убрать домен» сменил и SSTP / OpenConnect, и VLESS — окно VLESS «Нужны новые настройки» (Н-9)
    открывается после первого окна, а не поверх него; вход показали, пока первое было открыто, — второго нет."""

    def run_js(self, body):
        from tests import test_3378_step9 as S9
        from tests import test_panel_vless_card as VC
        pre = S9.SITE_PRE.replace("siteReissue = () => 0", "siteReissue = (rows) => { FIRST.push(rows.length); "
                                  "return new Promise((res) => { RELEASE = res; }); }")
        self.assertNotEqual(pre, S9.SITE_PRE)
        pre = "let RELEASE = null; const FIRST = [];\n" + pre
        stubs = VC.STUBS.replace(", vlSiteOn = () => 0", "")
        with mock.patch.object(VC, "STUBS", stubs):
            run = S9.RevVless4Test.harness(self, S9.SITE_FUNCS, S9.SITE_CONSTS, pre=pre)
        return run(body)

    def test_one_window_at_a_time(self):
        from tests import test_3378_step9 as S9
        out = self.run_js(r"""
const VT = %s, DEV = %s;
const o = {};
const tick = async () => { for (let i = 0; i < 50 && !RELEASE; i++) await new Promise((r) => setTimeout(r, 0)); };
R.inbounds = { loaded: true, data: [{ kind: "vless", title: "VLESS", installed: true, devices: 1, record: { security: "tls" } }] };
const answer = () => ({ ok: true, affected: [{ device: "d2", name: "Ноутбук", kind: "sstp" }], kept: [], warnings: [VT],
  vless: { changed: true, security: "reality", reissue: [DEV], text: VT } });
ASKS.push({ ok: true, need: null }); SITE_ANS.push(answer());
let p = siteClear();
await tick();
o.open = { first: FIRST.splice(0), vless: REISSUE.splice(0).length };
RELEASE(); RELEASE = null; await p;
o.closed = REISSUE.splice(0).map((x) => x[1]);
ASKS.push({ ok: true, need: null }); SITE_ANS.push(answer());
p = siteClear();
await tick();
authGate = true; RELEASE(); await p;                   // the login came while the first window was open
o.login = REISSUE.splice(0).length;
console.log(JSON.stringify(o));
""" % (json.dumps(S9.VTEXT, ensure_ascii=False), json.dumps(S9.DEV, ensure_ascii=False)))
        self.assertEqual(out["open"], {"first": [1], "vless": 0})     # the first window open: no VLESS one over it
        self.assertEqual(out["closed"], ["vl.whatMode"])               # closed — the VLESS one
        self.assertEqual(out["login"], 0)

    def test_the_dialog_says_when_it_is_closed(self):
        src = jsparts.function(read("app.js"), "reissueDialog")
        self.assertIn("onClose: () => closed()", src)
        self.assertIn("return done;", src)
        self.assertIn("return list.length ? reissueDialog(", jsparts.function(read("app.js"), "siteReissue"))


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class TabGoneOfflineTest(unittest.TestCase):
    """Придирка 5 (Н-8): вкладка на домене, браузер без сети (navigator.onLine false) — запрос не ушёл: обычная ошибка
    сети, не «эта вкладка закроется»; сеть есть — обрыв после запроса, как было."""

    def test_offline_is_a_network_error(self):
        src = jsparts.function(read("app.js"), "siteTabGone")
        out = run_node(r"""
const t = (k) => k, NOTES = [], noticeDialog = (o) => NOTES.push(o.title);
let VIA = "proxy";
const tabVia = () => VIA;
const loadAll = () => 0;
const nav = (on) => Object.defineProperty(globalThis, "navigator", { value: { onLine: on }, configurable: true, writable: true });
%s
const net = { code: "net" }, timeout = { code: "timeout" };
const o = {};
nav(false); o.offline = [siteTabGone(net, "T", "X"), siteTabGone(timeout, "T", "X")];
nav(true); o.online = siteTabGone(net, "T", "X");
VIA = "port"; o.port = siteTabGone(net, "T", "X");
o.notes = NOTES.length;
console.log(JSON.stringify(o));
""" % src)
        self.assertEqual(out, {"offline": [False, True], "online": True, "port": False, "notes": 2})


class OnceTurnTest(unittest.TestCase):
    """Придирка 3: расчёты с разными ключами идут по очереди (``Once._turn``): второй ключ не входит, пока первый
    держит расчёт, и входит сразу после."""

    def test_another_key_waits_its_turn(self):
        import threading
        from kaskad.panel.once import Once
        once, release, inside, order = Once(), threading.Event(), threading.Event(), []

        def first():
            order.append("a+")
            inside.set()
            self.assertTrue(release.wait(10))
            order.append("a-")
            return "a"

        def second():
            order.append("b+")
            return "b"
        out = {}
        ta = threading.Thread(target=lambda: out.update(a=once(("tls", ""), first)))
        ta.start()
        try:
            self.assertTrue(inside.wait(10))
            tb = threading.Thread(target=lambda: out.update(b=once(("reality", "ya.ru"), second)))
            tb.start()
            for _ in range(500):                                  # the second key is in the table (or ran)
                if ("reality", "ya.ru") in once.flight or "b+" in order:
                    break
                threading.Event().wait(0.01)
            threading.Event().wait(0.2)
            self.assertEqual(order, ["a+"], "a count of another key ran side by side")
            self.assertIn(("reality", "ya.ru"), once.flight)                # it waits its turn
        finally:
            release.set()
            ta.join(10)
            tb.join(10)
        self.assertEqual((order, out), (["a+", "a-", "b+"], {"a": "a", "b": "b"}))
        self.assertEqual(once.flight, {})


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "нет jq или bash")
class SshMenuRecommendTest(unittest.TestCase):
    """С1: в рекомендации теперь имена устройств — экран «Маскировка VLESS» SSH-меню печатает её через k3_term_esc
    (``echo -e`` не исполняет «\\e[…» из имени), управляющие знаки — пробелом (одна строка для ``read``)."""

    def test_names_are_plain_text(self):
        import os
        import re
        import subprocess
        src = open(os.path.join(os.path.dirname(__file__), "..", "install.sh"), encoding="utf-8").read()
        esc = re.search(r"(?ms)^k3_term_esc\(\) \{.*?^\}", src).group(0)
        body = re.search(r"(?ms)^k3_gw_vless_mode_menu\(\) \{.*?^\}", src).group(0)
        read = re.search(r"IFS=\$'\\x1f' read -r sec dom forced reason why fix rec <<< \"\$\(k3_jq '(.*?)'\)\"", body,
                         re.S)
        show = re.search(r'\[ -n "\$rec" \] && k3_gw_fold "  " "\$\{GREEN\}(.*?)\$\{NC\}"', body).group(1)
        doc = {"security": "reality", "reason": "ready", "reason_text": "x",
               "recommend": {"text": "Можно. Сайт включится (устройств — 1: a\\e[2J\x1b[31mb\nc\x1fd)."}}
        script = esc + "\nk3_jq() { printf '%s' \"$K3_GW_OUT\" | jq -r \"$@\" 2>/dev/null; }\n" + \
            "IFS=$'\\x1f' read -r sec dom forced reason why fix rec <<< \"$(k3_jq '" + read.group(1) + "')\"\n" + \
            'echo -e "' + show + '"\n'
        r = subprocess.run(["bash", "-c", script], env=dict(os.environ, K3_GW_OUT=json.dumps(doc), LANG="C.UTF-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        out = r.stdout.decode("utf-8", "replace")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("\x1b", out)                                       # no escape reaches the terminal
        self.assertIn("a\\e[2J [31mb c d).", out)                          # one line, the rest as text


class BotSwapTest(WantCase):
    """Придирка 4 (Н-13): «Да» бота — согласие на цену с этими устройствами. Одно ушло, другое пришло (число то же) —
    старая кнопка ничего не делает, вопрос заново с новыми именами; так у «Выключить сайт» (sdmy) и «Убрать домен»
    (sdxy)."""

    def setUp(self):
        super().setUp()
        from kaskad.bot import site as bsite
        from tests.test_vless_sh3 import _Ctx
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
        fn(self.ctx, data)

    def swap(self):
        self.ok("gw", "device", "del", self.dev["id"])
        self.add_device("Ноутбук", "windows")

    def check(self, ask, old):
        from tests import test_vless_mode as VM
        before = VM.raw()
        self.press(old)
        self.assertEqual(VM.raw(), before)                                    # nothing done
        self.assertEqual((site_model.peek(state.load())["mode"], self.rec()["security"]), ("site", "tls"))
        self.assertIn("Ноутбук", self.ctx.said[-1][0])                       # the question again, the new names
        self.assertNotIn("Телефон", self.ctx.said[-1][0])
        new = self.ctx.buttons()[0]
        self.assertNotEqual(new, old)
        self.assertEqual(new.split("Y")[1][:2], "1.")                        # the same number, another print
        return new

    def test_site_off(self):
        self.bsite.cb_site_mode_ask(self.ctx, "sdm")
        old = self.ctx.buttons()[0]
        self.swap()
        self.press(self.check("sdm", old))
        self.assertEqual(site_model.peek(state.load())["mode"], "domain")

    def test_clear(self):
        self.bsite.cb_clear_ask(self.ctx, "sdx")
        old = self.ctx.buttons()[0]
        self.swap()
        self.press(self.check("sdx", old))
        self.assertEqual(site_model.domain_of(state.load()), "")

    def test_the_key(self):
        key = self.bsite._price_key
        self.assertEqual((key([]), key(None)), ("0", "0"))
        a, b, c = {"id": "d-1"}, {"id": "d-2"}, {"id": "d-3"}
        self.assertEqual(key([a, b]), key([b, a]))                            # the order does not matter
        self.assertNotEqual(key([a, b]), key([a, c]))
        self.assertRegex(key([a, b]), r"^2\.[0-9a-f]{6}$")
        self.assertLessEqual(len(self.bsite._off_data(True, "w" * 30 + ".example.com", key([a] * 10 ** 5))), 64)


if __name__ == "__main__":
    unittest.main()

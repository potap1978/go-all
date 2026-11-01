"""«Режим стрима» (handoff/briefs/PLAN-stream-mode-2026-10-29.md, вид — Claude Design kaskad-stream r1).

Флаг — только в браузере (localStorage «kaskad.stream» = «1»): сервер, API и данные не меняются. Две защиты в
static/stream.js: поле-секрет (значения нет в DOM, пока его не показали) и сетка (MutationObserver прячет известные и
узнаваемые по виду значения в тексте и атрибутах).

* Правила в коде — без браузера: порядок файлов, флаг в boot.js, одна проверка в помощниках, QR без кода до показа.
* Чистые функции — node tests/panel/test_stream.mjs (scrub, isPublicIp, реестр, флаг «старые данные → новая»).
* Страж — tools/k3_stream_check.mjs в настоящем Chrome на моке: при включённом режиме ни одного секрета мока в тексте,
  атрибутах и полях всех разделов и окон (1440 и 390, RU и EN); QR нет в DOM до «Показать QR»; показ на _ttl и второй
  щелчок; копирование без показа; обратная проверка — при выключенном режиме те же значения видны, наблюдателя нет.
* Правки по REV-stream (каждая проверка падает на 827edf5d): новый пароль точками (Б-1), «И имена» при открытии
  раздела напрямую (Б-2), поздняя запись в реестр (С-1, node), фокус окна выключения (С-2), глаз у адреса выхода
  (З-1), «Скрыть QR» в плотном виде выдачи (З-2, node), точки токена бота после перерисовки (З-3).
* Слияние в 3.3.7.13: адреса сервера из «Адресов сервера» (known, ips, in, src — только адреса) — в реестре (node), отказ
  «адреса у сервера нет» в «Шлюзе» — под маской (страж, мок mip=1).
Без node или Chrome — пропуск с причиной."""

import functools
import json
import os
import re
import subprocess
import threading
import unittest

from tests.test_332f_give import HAS_NODE, issuance, prelude, read, run_node
from tests.test_333q_layout import ChromeServer, _Static, chrome_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CHECK = os.path.join(ROOT, "tools", "k3_stream_check.mjs")
NODE_TEST = os.path.join(ROOT, "tests", "panel", "test_stream.mjs")


def read_tool(name):
    with open(os.path.join(ROOT, "tools", name), encoding="utf-8") as f:
        return f.read()


class RuleTest(unittest.TestCase):
    def test_mock_refusal_of_server_addresses_follows_the_catalog(self):
        """Мок mip=1 (страж «Адреса сервера») говорит словами ядра: gw_addresses.err.not_here_panel RU и EN."""
        mock = read("dev-mock.js")
        for lang in ("ru", "en"):
            with open(os.path.join(ROOT, "lib", "kaskad", "i18n", "catalog", lang, "gw_addresses.json"), encoding="utf-8") as f:
                text = json.load(f)["gw_addresses.err.not_here_panel"]
            for part in re.split(r"\{ip\}|\{known\}", text):
                self.assertIn(part, mock, lang)
        self.assertIn('reason: "address_not_here"', mock)

    def test_flag_lives_in_the_browser_only(self):
        boot, js = read("boot.js"), read("stream.js")
        self.assertIn('try { sm = localStorage.getItem("kaskad.stream"); } catch (e) { sm = null; }', boot)
        self.assertIn('if (sm === "1") root.setAttribute("data-stream", "on");', boot)
        self.assertIn('var KEY = "kaskad.stream", KEY_NAMES = "kaskad.stream.names";', js)
        self.assertNotIn("/api/", js)                                   # ни запросов, ни записи на узел
        self.assertNotIn("kaskad.stream", read("dev-mock.js"))

    def test_stream_js_goes_before_the_panel(self):
        html = read("index.html")
        order = re.findall(r'<script defer src="([^"?]+)', html)
        self.assertEqual(order[:2], ["icons.js", "stream.js"])
        self.assertLess(html.index('href="ovb.css'), html.index('href="stream.css'))   # стили — последними

    def test_helpers_check_once_and_mask_without_value(self):
        js = read("app.js")
        self.assertIn("const SM = () => !!(KS && KS.on);", js)
        self.assertIn("const hostName = () => (SM() ? KS.MASK : hostReal());", js)
        self.assertIn('if (KS) KS.hideRevealed();', js)                 # смена раздела прячет показанное
        self.assertIn("if (KS && data) KS.harvest(data);", js)         # реестр — из любого ответа API
        st = read("stream.js")
        self.assertIn('box.setAttribute("data-sec-shown", "");                    // сначала метка', st)
        self.assertIn('val.textContent = ""; val.hidden = true;', st)  # спрятали — значения в DOM нет

    def test_qr_is_not_drawn_until_asked(self):
        st = read("stream.js")
        self.assertIn("if (!KS.on) return o.draw();", st)
        self.assertIn('box.appendChild(qrCover(function () { open(); }, o));', st)
        give = read("give.js")
        self.assertIn("streamQr(", give)

    def test_off_means_untouched(self):
        st = read("stream.js")
        self.assertIn("if (!KS.on || !input) return null;", st)      # field(): выключен — поле как было
        self.assertIn("if (KS.on) start(); else { hideRevealed(); stop(); }", st)

    def test_observer_does_not_feed_itself(self):
        # classList.add с уже стоящим классом всё равно пишет атрибут → запись наблюдателю → вечный круг
        # (поле с токеном в «Боте» вешало вкладку; в Chrome это ловит страж: «страница не отвечает»)
        st = read("stream.js")
        self.assertIn('if (e.value && !e.classList.contains("ks-sectext") && looks(e.value)) e.classList.add("ks-sectext");', st)
        self.assertIn("if (s !== v) e.setAttribute(a, s);", st)             # атрибут — только если сетка его изменила
        self.assertIn("process.exit(4)", read_tool("k3_stream_check.mjs"))


@unittest.skipUnless(HAS_NODE, "нет node: чистые функции stream.js не проверены")
class PureTest(unittest.TestCase):
    def test_node(self):
        p = subprocess.run(["node", NODE_TEST], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace")[-800:])
        self.assertIn("STREAM OK", p.stdout.decode("utf-8"))


@unittest.skipUnless(HAS_NODE, "нет node: окно выдачи в плотном виде не проверено")
class DenseQrTest(unittest.TestCase):
    def test_page_qr_of_the_dense_view_hides_again(self):
        """REV-stream З-2: в плотном виде («QR слишком большой») QR страницы после «Показать QR» прячется своей «Скрыть QR»."""
        from tests.test_333q import real_awg_items
        r = issuance("awg3", "ios")
        r["items"] = real_awg_items("3.1", True)
        js = prelude() + r"""
window.KaskadStream = { on: true, t: (k) => k, track() {}, untrack() {}, link: (a) => a,
  secret: () => ({ el: document.createElement("span"), eye: document.createElement("button") }),
  qrCover: (onShow) => { const c = document.createElement("div"); c.className = "ks-qrc"; const b = document.createElement("button");
    b.setAttribute("data-gk", "sqrshow"); b.addEventListener("click", () => onShow()); c.appendChild(b); return c; } };
const g = G.mount({ viewer: "admin", lang: "ru", device: { name: "iPhone" }, pageUrl: "https://h.example/p/abcdefghijklmnopqrstuv",
  drawQr: () => document.createElement("i"), drawLinkQr: () => { const n = document.createElement("b"); n.className = "lq"; return n; } });
document.body.appendChild(g.el);
g.set(%s);
const root = g.el, st = () => ({ cover: has(root, "ks-qrc"), qr: has(root, "lq"), hide: !!gk(root, "sqrhide") });
fire(gk(root, "app:amneziawg"), "click");
const out = { dense: has(root, "gs-dense"), before: st() };
fire(gk(root, "sqrshow"), "click");
out.shown = st();
if (gk(root, "sqrhide")) fire(gk(root, "sqrhide"), "click");
out.again = st();
console.log(JSON.stringify(out));
""" % json.dumps(r)
        got = run_node(js)
        self.assertTrue(got["dense"], got)
        self.assertEqual(got["before"], {"cover": True, "qr": False, "hide": False})
        self.assertEqual(got["shown"], {"cover": False, "qr": True, "hide": True})
        self.assertEqual(got["again"], {"cover": True, "qr": False, "hide": False})


@unittest.skipUnless(HAS_NODE and chrome_path(), "нет node или Chrome: страж «Режима стрима» не запущен (K3_CHROME=…)")
class ChromeTest(unittest.TestCase):
    rows = []

    @classmethod
    def setUpClass(cls):
        srv = ChromeServer(("127.0.0.1", 0), functools.partial(_Static, directory=STATIC))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            p = subprocess.run(["node", CHECK, "http://127.0.0.1:%d" % srv.server_address[1], chrome_path()],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1560)
        finally:
            srv.shutdown()
            srv.server_close()
        if p.returncode != 0:
            raise AssertionError("k3_stream_check: %s" % p.stderr.decode("utf-8", "replace")[-800:])
        cls.rows = [json.loads(x) for x in p.stdout.decode("utf-8").strip().splitlines() if x.startswith("{")]

    def kind(self, k):
        return [r for r in self.rows if r.get("kind") == k]

    def test_no_leaks_anywhere(self):
        rows = self.kind("leaks")
        self.assertGreater(len(rows), 100)
        self.assertEqual([(r["where"], r["w"], r["lang"], r["hits"][:3]) for r in rows if r["hits"]], [])

    def test_every_place_opened_without_errors(self):
        rows = self.kind("leaks")
        self.assertEqual([(r["where"], r["w"], r["lang"]) for r in rows if not r["ok"]], [])
        self.assertEqual([(r["where"], r["w"], r["errors"]) for r in self.rows if r.get("errors")], [])
        self.assertTrue(all(r["active"] for r in rows))

    def test_dialogs_really_opened(self):
        rows = {(r["where"], r["w"], r["lang"]): r for r in self.kind("leaks")}
        for w in (1440, 390):
            self.assertIn("dialog:true", rows[("settings?open=link", w, "ru")]["steps"])
            self.assertIn("open:true", rows[("issue-awg", w, "ru")]["steps"])
            self.assertTrue(any(s.startswith("paste:true") for s in rows[("exits?open=add+paste", w, "ru")]["steps"]))
        # шторка QR стоит вместо кода, не поверх кнопок окна (на телефоне QR в первом экране окна нет: -1)
        self.assertIn("overlap:0", rows[("issue-awg", 1440, "ru")]["steps"])
        self.assertTrue({"overlap:0", "overlap:-1"} & set(rows[("issue-awg", 390, "ru")]["steps"]))
        self.assertIn("sub:true", rows[("issue-sub", 1440, "ru")]["steps"])

    def test_qr_absent_until_shown(self):
        # ни в одном месте QR не нарисован, пока его не попросили; «QR-код» в окне подписки — это и есть просьба
        self.assertEqual([(r["where"], r["w"], r["lang"]) for r in self.kind("leaks") if r["qrs"] and r["where"] != "issue-sub"], [])
        r = self.kind("qr")[0]
        self.assertEqual(r["before"], {"cover": True, "frame": 0})
        self.assertTrue(r["after"]["frame"] > 0 and not r["after"]["cover"])
        self.assertEqual(r["again"], {"cover": True, "frame": 0})

    def test_reveal_and_copy(self):
        rv, cp = self.kind("reveal")[0], self.kind("copy")[0]
        self.assertTrue(rv["eye"] and rv["shown"] and rv["goneAfterTtl"] and rv["secondClickHides"], rv)
        self.assertTrue(cp["clicked"] and cp["clipReal"] and cp["clipInS"] and cp["masksOnScreen"] > 0, cp)

    def test_input_field_dots_and_eye(self):
        f = self.kind("field")[0]
        self.assertEqual(f["before"], {"type": "text", "dots": True, "value": True})      # точки стилем, не type=password
        self.assertTrue(f["eye"])
        self.assertEqual(f["open"], {"type": "text", "dots": False, "value": True})
        self.assertEqual(f["closed"], {"type": "text", "dots": True, "value": True})

    def test_new_password_is_dots(self):
        """Б-1: «Придумать» и «Доступ из интернета» без пароля — точки и глаз; выключен — пароль виден (обратная)."""
        rows = {r["on"]: r for r in self.kind("password")}
        on, off = rows[True], rows[False]
        self.assertTrue(on["gen"] and on["sw"] and off["gen"] and off["sw"], rows)
        for k in ("afterGen", "publicOn"):
            self.assertEqual(on[k]["st"]["len"], 16, (k, on))
            self.assertTrue(on[k]["st"]["dots"] or on[k]["st"]["type"] == "password", (k, on))
            self.assertEqual(on[k]["st"]["eyes"], 1, (k, on))
            self.assertEqual(on[k]["hits"], 0, (k, on))
            self.assertEqual((off[k]["st"]["type"], off[k]["st"]["dots"], off[k]["st"]["len"]), ("text", False, 16), (k, off))
            self.assertGreaterEqual(off[k]["hits"], 1, (k, off))

    def test_off_dialog_focus_is_on_keep(self):
        """С-2: фокус окна выключения — «Оставить включённым»; Enter оставляет режим включённым."""
        r = self.kind("offFocus")[0]
        self.assertTrue(r["opened"], r)
        self.assertEqual(r["focus"], "Оставить включённым")
        self.assertTrue(r["closed"] and r["on"], r)
        self.assertEqual(r["attr"], "on")

    def test_names_hidden_when_a_section_opens_directly(self):
        """Б-2: «И имена» — ни одного имени мока ни в одном разделе, открытом напрямую; подпись человека везде одна."""
        rows = self.kind("names")
        self.assertGreaterEqual(len(rows), 30)
        self.assertTrue(all(r["n"] >= 4 for r in rows), [r["n"] for r in rows])
        self.assertEqual([(r["where"], r["w"]) for r in rows if not r["ok"]], [])
        self.assertEqual([(r["where"], r["w"], r["hits"][:3]) for r in rows if r["hits"]], [])
        a = self.kind("alias")[0]
        self.assertRegex(a["scheme"] or "", r"^Пользователь \d+$")
        self.assertEqual(a["scheme"], a["people"])

    def test_avatars_do_not_show_the_real_name(self):
        """Н-4 стенда 3.3.7.13: с «И имена» кружок человека — не инициалы и не оттенок настоящего имени: в разделах
        («Пользователи» на 1440 и 390, «Схема» на 1440) и в окнах («График активности»; на телефоне — лист человека и
        «Схема целиком»)."""
        rows = self.kind("names")
        self.assertEqual([(r["where"], r["w"], r["av"]["hits"][:3]) for r in rows if r["av"]["hits"]], [])
        seen = {(r["where"], r["w"]): r["av"]["n"] for r in rows}
        for place in [("people", 1440), ("people", 390), ("scheme", 1440)]:
            self.assertGreater(seen.get(place, 0), 0, place)          # кружки там есть: проверка не пустая
        w = self.kind("avWin")[0]
        for k in ("act", "sheet", "full"):
            self.assertTrue(w[k]["opened"], (k, w))
            self.assertGreater(w[k]["av"]["n"], 0, (k, w))
            self.assertEqual((w[k]["av"]["hits"], w[k]["hits"]), ([], []), k)
        s = w["search"]                                               # подсветка поиска не режет имя мимо сетки
        self.assertTrue(s["typed"] and s["q"] == 3 and s["rows"] > 0, s)
        self.assertEqual(s["hits"], [])
        self.assertEqual(w["errors"], [])

    def test_exit_address_has_an_eye(self):
        """З-1: «Адрес» в раскрытии строки выхода — маска с глазом, показ с отсчётом."""
        r = self.kind("exitAddr")[0]
        self.assertGreaterEqual(r["eyes"], 1, r)
        self.assertTrue(r["clicked"], r)
        self.assertEqual(r["shown"], {"v": True, "left": True})

    def test_bot_token_keeps_dots_after_repaint(self):
        """З-3: текст, не похожий на токен, после перерисовки поля — всё равно точками."""
        r = self.kind("botToken")[0]
        self.assertTrue(r["before"] and r["typed"], r)
        self.assertEqual(r["after"], {"dots": True, "len": 9})

    def test_server_addresses_refusal_is_masked(self):
        """3.3.7.13 «Адреса сервера» (слияние dev-mip): отказ «адреса у сервера нет» в «Шлюзе» перечисляет адреса
        сервера — в режиме они под маской (реестр берёт data.known), выключен — второй адрес виден."""
        rows = self.kind("mipRefusal")[0]["rows"]
        for k in ("on-ru", "on-en"):
            r = rows[k]
            self.assertTrue(r["typed"] and r["saved"] and r["toast"], (k, r))
            self.assertEqual(r["hits"], [], k)
            self.assertTrue(r["masked"], (k, r))
            self.assertFalse(r["nat"], (k, r))
        off = rows["off-ru"]
        self.assertTrue(off["toast"] and off["nat"] and not off["masked"], off)

    def test_reverse_sees_what_it_looks_for(self):
        s = self.kind("summary")[0]
        self.assertGreaterEqual(s["reverseFound"], 8)                  # страж видит секреты, когда режим выключен
        self.assertFalse(s["reverseActive"])                           # наблюдателя нет
        self.assertEqual(s["reverseMasks"], 0)                         # масок нет


if __name__ == "__main__":
    unittest.main()

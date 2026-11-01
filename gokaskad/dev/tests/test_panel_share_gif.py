"""«Поделиться» — кадр, PNG и GIF в браузере (C10; SPEC-node-score-schedule-share §5.3, «Сверка 24.10» п. 3–4; макет r1
check-share.js): pages/diag-share.js в node. GIF разбирается обратно своим декодером (LZW, «не стирать») — каждый кадр
совпадает с исходным по индексам; задержки — ровно 1500 сотых (15 с: движение прежнее, результаты стоят дольше, владелец 24.10) при 15 и 10 кадрах/с, в том числе после склейки
одинаковых кадров; палитра — не больше 256 цветов и одна на файл; раскадровка — по макету r1 (заставка, перекат цифр,
«в целом», полоса по дню, удержание); в кадре нет ничего, кроме строк окна."""

import json
import os
import subprocess
import tempfile
import unittest

from tests.test_panel_diag_screen import HAS_NODE
from tests.test_panel_rf_js import read

DECODER = r"""
// GIF89a -> кадры индексов на холсте W×H (композиция «не стирать»), задержки, палитра, повтор
function decodeGif(b) {
  let p = 0;
  const u8 = () => b[p++], u16 = () => { const v = b[p] | (b[p + 1] << 8); p += 2; return v; };
  const sig = String.fromCharCode(...b.slice(0, 6)); p = 6;
  const W = u16(), H = u16(), fl = u8(); u8(); u8();
  const gct = (fl & 0x80) ? 1 << ((fl & 7) + 1) : 0, pal = [];
  for (let i = 0; i < gct; i++) pal.push([u8(), u8(), u8()]);
  const canvas = new Uint8Array(W * H), frames = [];
  let delay = 0, disposal = 0, loop = null, local = 0;
  while (p < b.length) {
    const t = u8();
    if (t === 0x3b) break;
    if (t === 0x21) {
      const lab = u8();
      if (lab === 0xf9) { u8(); const f = u8(); disposal = (f >> 2) & 7; delay = u16(); u8(); u8(); continue; }
      if (lab === 0xff) { const n = u8(); const id = String.fromCharCode(...b.slice(p, p + n)); p += n; let s; while ((s = u8())) { if (id === "NETSCAPE2.0" && s === 3) { u8(); loop = u16(); } else p += s; } continue; }
      let s; while ((s = u8())) p += s; continue;
    }
    if (t !== 0x2c) throw new Error("block " + t + " at " + (p - 1));
    const x = u16(), y = u16(), w = u16(), h = u16(), f = u8();
    if (f & 0x80) local += 1;
    const min = u8(), data = [];
    let s; while ((s = u8())) { for (let i = 0; i < s; i++) data.push(b[p + i]); p += s; }
    const px = lzwDecode(data, min, w * h);
    for (let yy = 0; yy < h; yy++) for (let xx = 0; xx < w; xx++) canvas[(y + yy) * W + x + xx] = px[yy * w + xx];
    frames.push({ x, y, w, h, delay, disposal, idx: canvas.slice() });
  }
  return { sig, W, H, pal, frames, loop, local };
}
function lzwDecode(data, min, n) {
  const clear = 1 << min, eoi = clear + 1, out = new Uint8Array(n);
  let size = min + 1, dict = [], pos = 0, bit = 0, prev = null, o = 0;
  const reset = () => { dict = []; for (let i = 0; i < clear; i++) dict.push([i]); dict.push(null, null); size = min + 1; prev = null; };
  reset();
  const read = () => { let v = 0; for (let i = 0; i < size; i++) { const byte = data[(bit >> 3)]; if (byte === undefined) return eoi; v |= ((byte >> (bit & 7)) & 1) << i; bit++; } return v; };
  for (;;) {
    const c = read();
    if (c === clear) { reset(); continue; }
    if (c === eoi) break;
    let e;
    if (c < dict.length && dict[c]) e = dict[c];
    else if (c === dict.length && prev) e = prev.concat([prev[0]]);
    else throw new Error("bad code " + c);
    for (const v of e) if (o < n) out[o++] = v;
    if (prev) { if (dict.length < 4096) dict.push(prev.concat([e[0]])); }
    prev = e;
    if (dict.length === (1 << size) && size < 12) size += 1;
  }
  if (o !== n) throw new Error("short " + o + " of " + n);
  return out;
}
"""

FRAME_O = {"fmt": "sq", "title": "Мой сервер · Kaskad Pro", "cond": "проверено ночью · связь с вашими пользователями не измерена",
           "det": "", "metrics": [{"l": "Скачивание", "v": "735", "u": "Мбит/с"}, {"l": "Отдача", "v": "5", "u": "Мбит/с"},
                                  {"l": "Отклик", "v": "9,4", "u": "мс"}, {"l": "Потери", "v": "0,41", "u": "%"}],
           "total": "93", "totalL": "в целом", "totalU": "от обычного", "totalNote": "за 30 дней · 100 — как обычно у этого сервера",
           "strip": "Стабильность за 30 дней", "days": ["green"] * 27 + ["yellow", "red", "grey"], "date": "23 октября 2026",
           "brand": "Kaskad Pro · anten-ka"}


def run(script, dicts=False):
    """The script in node after pages/diag-share.js (and the dictionaries of diag, ``dicts``); the last line — JSON."""
    pre = "const window = globalThis;\n"
    if dicts:
        pre += read("pages/diag-i18n-ru.js") + "\n" + read("pages/diag-i18n-en.js") + "\n"
        pre += "const _ls = {}; globalThis.localStorage = { getItem: (k) => (k in _ls ? _ls[k] : null), setItem: (k, v) => { _ls[k] = String(v); } };\n"
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(pre + read("pages/diag-share.js") + "\nconst S = window.KaskadShare;\n" + DECODER +
                    "\n(async () => {\n" + script + "\n})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });")
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    out = r.stdout.decode("utf-8").strip().splitlines()
    if r.returncode != 0 or not out:
        raise AssertionError("node: %s" % r.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(out[-1])


@unittest.skipUnless(HAS_NODE, "no node")
class GifTest(unittest.TestCase):
    def test_round_trip_and_delays(self):
        """Синтетический ролик 120×90: полоса растёт до 2,5 с, дальше стоит — кадры совпадают с исходными по индексам,
        одинаковые склеены, сумма задержек 1500 при 15 и 10 кадрах/с, повтор бесконечно, одна палитра."""
        r = run(r"""
          const W = 120, H = 90, colors = [[20, 24, 30], [69, 208, 192], [240, 240, 240], [200, 60, 50]];
          const rgba = (t) => { const d = new Uint8ClampedArray(W * H * 4);
            for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) { const i = (y * W + x) * 4;
              const c = y > 60 && x < Math.min(2.5, t) * 40 ? colors[1] : (x + y) % 17 === 0 ? colors[2] : y < 10 ? colors[3] : colors[0];
              d[i] = c[0]; d[i + 1] = c[1]; d[i + 2] = c[2]; d[i + 3] = 255; } return d; };
          const out = {};
          for (const fps of [15, 10]) {
            const got = await S.encode(W, H, rgba, fps, null, null);
            const g = decodeGif(got.bytes);
            const pal = S.palette([rgba(0.3), rgba(S.AT.total + 0.4), rgba(S.DUR - 0.01)]), ix = S.indexer(pal);
            // кадр файла i покрывает исходные кадры от start: сравнение с исходным индексом на начале кадра
            const ds = S.delays(fps); let k = 0, ok = true;
            for (const f of g.frames) { const want = ix(rgba(k / fps), new Uint8Array(W * H));
              for (let i = 0; i < want.length; i++) if (want[i] !== f.idx[i]) { ok = false; break; }
              let left = f.delay; while (left > 0 && k < ds.length) { left -= ds[k]; k += 1; } }
            out[fps] = { sig: g.sig, frames: g.frames.length, sum: g.frames.reduce((a, f) => a + f.delay, 0), ok, loop: g.loop,
              local: g.local, disp: g.frames.every((f) => f.disposal === 1), n: ds.length, dsum: ds.reduce((a, x) => a + x, 0),
              colors: got.colors, gct: g.pal.length, last: k };
          }
          console.log(JSON.stringify(out));
        """)
        for fps, n in (("15", 225), ("10", 150)):
            x = r[fps]
            self.assertEqual((x["sig"], x["sum"], x["dsum"], x["n"], x["loop"], x["local"]), ("GIF89a", 1500, 1500, n, 0, 0), fps)
            self.assertTrue(x["ok"], fps)
            self.assertTrue(x["disp"], fps)
            self.assertLess(x["frames"], n)                    # одинаковые кадры склеены (после 2,5 с ничего не меняется)
            self.assertEqual(x["last"], n)
            self.assertLessEqual(x["colors"], 256)
            self.assertGreaterEqual(x["gct"], x["colors"])

    def test_lzw_long_and_random(self):
        """LZW: длинный кадр (сброс таблицы после 4096 кодов) и шум — разбор возвращает те же индексы."""
        r = run(r"""
          let seed = 7; const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
          const res = [];
          for (const [n, k] of [[1, 2], [300000, 256], [200000, 3], [50000, 17]]) {
            const px = new Uint8Array(n); for (let i = 0; i < n; i++) px[i] = Math.floor(rnd() * k);
            const back = lzwDecode(Array.from(S.lzw(px)), 8, n);
            let same = true; for (let i = 0; i < n; i++) if (back[i] !== px[i]) { same = false; break; }
            res.push(same);
          }
          console.log(JSON.stringify(res));
        """)
        self.assertEqual(r, [True, True, True, True])

    def test_delays(self):
        r = run("console.log(JSON.stringify([S.DUR, S.AT].concat([15, 12, 10].map((f) => [S.delays(f).length, S.delays(f).reduce((a, x) => a + x, 0), Math.min(...S.delays(f))]))));")
        self.assertEqual(r, [15, {"intro": 1.5, "total": 5.0, "days": 8.5}, [225, 1500, 6], [180, 1500, 8], [150, 1500, 10]])

    def test_storyboard_of_r1(self):
        """Раскадровка (макет r1, темп — владелец 24.10): заставка до 1,5 с без цифр; перекат цифр 1,5–2,6 с прежней
        скоростью; «в целом» 5,0–5,9; полоса по дню 8,5–9,7; с 9,8 кадр не меняется; в кадре только строки окна."""
        r = run("const o = " + json.dumps(FRAME_O, ensure_ascii=False) + r""";
          const flat = (dl) => { const out = []; const walk = (x) => { out.push(x); (x.kids || []).forEach(walk); }; dl.items.forEach(walk); return out; };
          const texts = (t) => flat(S.frame(t, o)).filter((x) => x.k === "text").map((x) => x.s);
          const lit = (t) => flat(S.frame(t, o)).filter((x) => x.k === "rect" && x.h >= 48 && x.h <= 52 && x.fill !== "var(--surface-3)").length;
          const sig = (t) => JSON.stringify(S.frame(t, o));
          console.log(JSON.stringify({ intro: texts(0.3), introSame: sig(0) === sig(1.49), at1: texts(1.9).join(" "), lit25: lit(8.4),
            lit32: lit(9.1), lit39: lit(9.8), hold: sig(9.8) === sig(14.99), post: S.frame(14.99, Object.assign({}, o, { fmt: "post" })).h,
            noTotal: texts(14.99).join("|"), all: Array.from(new Set(texts(14.99))),
            keep: sig(2.7) === sig(4.99), keep2: sig(6.0) === sig(8.49), rollSpeed: [texts(1.9).join(" ") !== texts(2.6).join(" "), texts(5.4).join(" ") !== texts(5.9).join(" ")] }));
        """)
        self.assertEqual(r["intro"], ["23 октября 2026", "Kaskad Pro · anten-ka", "K", "Мой сервер · Kaskad Pro",
                                      "проверено ночью · связь с вашими пользователями не измерена"])
        self.assertTrue(r["introSame"])
        self.assertIn("Скачивание", r["at1"])
        self.assertEqual((r["lit25"], r["lit39"]), (0, 29))           # серый день остаётся серым
        self.assertTrue(0 < r["lit32"] < 29)
        self.assertTrue(r["hold"])
        self.assertTrue(r["keep"] and r["keep2"])            # готовые цифры и «в целом» стоят, пока не начнётся следующее
        self.assertEqual(r["rollSpeed"], [True, True])           # перекат идёт с прежней скоростью (≈ 1 с), а не растянут
        self.assertEqual(r["post"], 1350)
        allowed = set(sum([[v for k, v in FRAME_O.items() if isinstance(v, str)]] +
                          [[m["l"], m["u"]] for m in FRAME_O["metrics"]], [])) | set("0123456789,") | {"K"}
        self.assertTrue(set(r["all"]) <= allowed, set(r["all"]) - allowed)


DOC = {"v": 1, "ok": True, "ready": True, "why": None, "date": "2026-10-23", "checked": {"part": "night", "people": False},
       "metrics": [{"id": "down", "v": 735, "unit": "Мбит/с"}, {"id": "up", "v": 5, "unit": "Мбит/с"},
                   {"id": "rtt", "v": 9.4, "unit": "мс"}, {"id": "loss", "v": 0.41, "unit": "%"}],
       "rel_total": 93, "rel_n": 6, "rel_need": 3, "days": ["green"] * 27 + ["yellow", "red", "grey"],
       "extra": {"tariff": {"vcpu": 2, "ram_gb": 4, "port_mbit": 1000}, "country": "FI", "cpu": "AMD EPYC 7763"},
       "dropped": []}
ALL = {"tariff": True, "country": True, "cpu": True}


@unittest.skipUnless(HAS_NODE, "no node")
class ClipTextTest(unittest.TestCase):
    """clipOf — the lines of the frame from the answer of GET /api/host/share on the language of the clip (not of the
    panel): the frame r1 word for word, the switches off — no tariff, country or CPU; a strange answer — no raw key,
    no foreign text in the frame."""

    def clip(self, doc=None, L="ru", flags=None, fmt="sq"):
        return run("console.log(JSON.stringify(S.clipOf(%s, %s, %s)));" % (
            json.dumps(DOC if doc is None else doc, ensure_ascii=False), json.dumps({"fmt": fmt, "theme": "dark", "L": L}),
            json.dumps(flags or {})), dicts=True)

    def test_the_frame_of_r1_in_russian(self):
        o = self.clip()
        want = dict(FRAME_O, theme="dark")
        self.assertEqual(o, want)
        self.assertEqual(self.clip(flags=ALL)["det"], "2 ядра · 4 ГБ · порт 1 Гбит/с · Финляндия · AMD EPYC 7763")
        self.assertEqual(self.clip(flags={"country": True})["det"], "Финляндия")

    def test_times_utc_and_moscow_next_to_the_date(self):
        """Владелец 24.10: рядом с датой — время проверки UTC и московское (UTC+3); без ``at`` — только дата."""
        at = 1792719000                                                          # 2026-10-23 01:30 UTC
        self.assertEqual(self.clip(dict(DOC, at=at))["date"], "23 октября 2026 · 01:30 UTC · 04:30 МСК")
        self.assertEqual(self.clip(dict(DOC, at=at), L="en")["date"], "October 23, 2026 · 01:30 UTC · 04:30 MSK")
        self.assertEqual(self.clip(dict(DOC, at=1792790400))["date"], "23 октября 2026 · 21:20 UTC · 00:20 МСК")
        for bad in (None, "1792719000", -5):
            self.assertEqual(self.clip(dict(DOC, at=bad))["date"], "23 октября 2026", bad)

    def test_where_the_numbers_were_measured(self):
        """Владелец 24.10, «чьи это цифры»: под скачиванием и отдачей — точка замера, под откликом и потерями — до каких
        адресов пинг; свои точки — числом; неизвестная или кривая точка — без строки; в кадре строки на своих местах."""
        wh = {"down": "cloudflare", "up": "allowed", "anchors": ["cloudflare", "google", "yandex"], "n": 3}
        o = self.clip(dict(DOC, where=wh))
        self.assertEqual([m.get("src") for m in o["metrics"]], ["Cloudflare → сервер", "сервер → ваша точка", None, None])
        self.assertEqual(o["ping"], "отклик и потери: пинг с сервера до DNS Cloudflare · Google · Яндекс")
        e = self.clip(dict(DOC, where=dict(wh, down="ertelecom-omsk", up="hostkey-msk")), L="en")
        self.assertEqual([m.get("src") for m in e["metrics"]][:2], ["ER-Telecom → server", "server → HostKey"])
        self.assertEqual(e["ping"], "latency and loss: ping from the server to the DNS of Cloudflare · Google · Yandex")
        own = self.clip(dict(DOC, where={"down": "Evil<b>", "up": "nowhere", "anchors": "own", "n": 2}))
        self.assertEqual([m.get("src") for m in own["metrics"]], [None] * 4)
        self.assertEqual(own["ping"], "отклик и потери: пинг с сервера до своих точек (2)")
        bad = self.clip(dict(DOC, where={"anchors": ["cloudflare", "192.0.2.5"], "n": 2}))
        self.assertNotIn("ping", bad)
        r = run("const o = Object.assign(" + json.dumps(FRAME_O, ensure_ascii=False) + ", " + json.dumps(
            {"ping": o["ping"], "metrics": o["metrics"]}, ensure_ascii=False) + r""");
          const out = []; const walk = (x) => { out.push(x); (x.kids || []).forEach(walk); };
          S.frame(14.99, o, S.guess).items.forEach(walk);
          console.log(JSON.stringify(out.filter((x) => x.k === "text" && /→|пинг/.test(x.s)).map((x) => [x.s, x.x, x.y])));
        """)
        self.assertEqual(r, [["Cloudflare → сервер", 72, 358], ["сервер → ваша точка", 552, 358],
                             ["отклик и потери: пинг с сервера до DNS Cloudflare · Google · Яндекс", 72, 508]])

    def test_english(self):
        o = self.clip(L="en", flags=ALL)
        self.assertEqual((o["title"], o["cond"], o["date"], o["totalL"], o["totalU"]),
                         ("My server · Kaskad Pro", "checked at night · the connection to your users is not measured",
                          "October 23, 2026", "overall", "of usual"))
        self.assertEqual(o["metrics"], [{"l": "Download", "v": "735", "u": "Mbit/s"}, {"l": "Upload", "v": "5", "u": "Mbit/s"},
                                        {"l": "Latency", "v": "9.4", "u": "ms"}, {"l": "Packet loss", "v": "0.41", "u": "%"}])
        self.assertEqual(o["det"], "2 cores · 4 GB · 1 Gbit/s port · Finland · AMD EPYC 7763")
        self.assertEqual((o["totalNote"], o["strip"]), ("over 30 days · 100 — as usual for this server", "Stability over 30 days"))
        text = json.dumps(o, ensure_ascii=False)
        self.assertFalse(any("Ѐ" <= c <= "ӿ" for c in text), text)

    def test_the_tariff_in_words(self):
        for tf, ru, en in (({"vcpu": 1, "ram_gb": 1.5, "port_mbit": 500}, "1 ядро · 1,5 ГБ · порт 500 Мбит/с", "1 core · 1.5 GB · 500 Mbit/s port"),
                           ({"vcpu": 5, "ram_gb": 32, "port_mbit": 10000}, "5 ядер · 32 ГБ · порт 10 Гбит/с", "5 cores · 32 GB · 10 Gbit/s port"),
                           ({"vcpu": 4, "ram_gb": 8, "port_mbit": 2500}, "4 ядра · 8 ГБ · порт 2,5 Гбит/с", "4 cores · 8 GB · 2.5 Gbit/s port"),
                           ({"vcpu": 2, "ram_gb": 3.9, "port_mbit": None}, "2 ядра · 3,9 ГБ", "2 cores · 3.9 GB")):
            doc = dict(DOC, extra=dict(DOC["extra"], tariff=tf))
            self.assertEqual(self.clip(doc, flags={"tariff": True})["det"], ru)
            self.assertEqual(self.clip(doc, L="en", flags={"tariff": True})["det"], en)

    def test_nothing_to_compare_people_and_no_number(self):
        doc = dict(DOC, rel_total=None, rel_n=1, checked={"part": "mixed", "people": True},
                   metrics=[dict(DOC["metrics"][0], v=None)] + DOC["metrics"][1:])
        o = self.clip(doc)
        self.assertIsNone(o["total"])
        self.assertEqual(o["totalNote"], "Сравнивать пока не с чем: звеньев с историей 1 из 3")
        self.assertEqual(o["cond"], "проверено в разное время суток · учтена связь с вашими пользователями")
        self.assertEqual(o["metrics"][0], {"l": "Скачивание", "v": "—", "u": ""})

    def test_a_strange_answer(self):
        """A foreign value never reaches the frame: the CPU not ASCII or too long, the country not ISO, a strange
        day, link, part or date — they are dropped or turned grey; no raw key of the dictionary."""
        doc = dict(DOC, date="../../x", checked={"part": "noon", "people": "yes"},
                   metrics=DOC["metrics"] + [{"id": "evil", "v": 1, "unit": "<b>"}],
                   days=["green", "purple", "<img>"] + ["grey"] * 27,
                   extra={"tariff": {"vcpu": "2", "ram_gb": -1, "port_mbit": "1e9"}, "country": "<b>", "cpu": "Xeon\u3164 192"})
        o = self.clip(doc, flags=ALL)
        self.assertEqual((o["det"], o["date"], o["cond"]), ("", "", "Связь с вашими пользователями не измерена"))
        self.assertEqual(o["days"][:3], ["green", "grey", "grey"])
        self.assertEqual([m["l"] for m in o["metrics"]], ["Скачивание", "Отдача", "Отклик", "Потери"])
        for cpu in ("A" * 49, "Xeon\n", "Xeon\u00a0E5"):
            self.assertEqual(self.clip(dict(DOC, extra=dict(DOC["extra"], cpu=cpu)), flags={"cpu": True})["det"], "", cpu)
        for c in ("fi", "XYZ", 7):
            self.assertEqual(self.clip(dict(DOC, extra=dict(DOC["extra"], country=c)), flags={"country": True})["det"], "", c)
        self.assertNotIn("dg.", json.dumps(o))

    def test_prefs_names_and_the_width(self):
        """Prefs of the window — only the format, the theme and the language (no switches); the file name has only the
        day of the check; a long line fits the frame (smaller and then «…»), every text inside 1080."""
        r = run(r"""
          const out = {};
          out.def = S.readPrefs("ru", "light");
          localStorage.setItem("kaskad.share", "{junk");
          out.junk = S.readPrefs("en", "dark");
          localStorage.setItem("kaskad.share", JSON.stringify({ fmt: "post", theme: "light", L: "en", flags: { cpu: true }, x: 1 }));
          out.set = S.readPrefs("ru", "dark");
          localStorage.setItem("kaskad.share", JSON.stringify({ fmt: "big", theme: "<b>", L: "de" }));
          out.bad = S.readPrefs("ru", "dark");
          out.names = [S.fileName({ date: "2026-10-23" }, "gif"), S.fileName({ date: "../x" }, "png"), S.fileName(null, "gif")];
          const long = "2 ядра · 4 ГБ · порт 1 Гбит/с · Соединённые Штаты Америки · Intel(R) Xeon(R) Platinum 8375C CPU @ 2.90GHz x";
          out.fit = S.fit(long, 24, 16, 936, S.guess);
          out.fitW = S.guess(out.fit.s, out.fit.fs);
          const o = Object.assign({}, """ + json.dumps(FRAME_O, ensure_ascii=False) + r""", { det: long, cond: "checked at different times of day · the connection to your users is not measured" });
          const items = []; const walk = (x) => { items.push(x); (x.kids || []).forEach(walk); };
          for (const fmt of ["sq", "post"]) for (const t of [0.3, 14.99]) S.frame(t, Object.assign({}, o, { fmt }), S.guess).items.forEach(walk);
          out.over = items.filter((x) => x.k === "text").filter((x) => {
            const w = S.guess(x.s, x.fs), l = x.a === "middle" ? x.x - w / 2 : x.a === "end" ? x.x - w : x.x;
            return l < 0 || l + w > 1080; }).map((x) => x.s);
          console.log(JSON.stringify(out));
        """, dicts=True)
        self.assertEqual(r["def"], {"fmt": "sq", "theme": "light", "L": "ru"})
        self.assertEqual(r["junk"], {"fmt": "sq", "theme": "dark", "L": "en"})
        self.assertEqual(r["set"], {"fmt": "post", "theme": "light", "L": "en"})
        self.assertEqual(r["bad"], {"fmt": "sq", "theme": "dark", "L": "ru"})
        self.assertEqual(r["names"], ["kaskad-check-2026-10-23.gif", "kaskad-check-now.png", "kaskad-check-now.gif"])
        self.assertEqual(r["fit"]["fs"], 16)
        self.assertTrue(r["fit"]["s"].endswith("…"), r["fit"])
        self.assertLessEqual(r["fitW"], 936)
        self.assertEqual(r["over"], [])


if __name__ == "__main__":
    unittest.main()

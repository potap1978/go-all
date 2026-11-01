"""3.3.3 №38 (owner): the key of AmneziaWG 3.1 was shown as ONE dense QR the camera did not take. Not «zbar read it»:
every code of the window is checked against the limit of a phone camera on a screen — its QR version and the size of
a module in CSS px in the frame of the window (kaskad.qr: FRAME_*, MIN_MODULE_PX*, MAX_VERSION, MAX_PARTS) — and
the same numbers in the browser (KaskadQRSlides.fit, KaskadQR.encode) and on /p/ (qrencode -l).

- parts of vpn:// for AmneziaVPN / DefaultVPN: two at most (owner, 3.3.3: the standard app expects 2), one when it
  fits; the frame of a computer is sized for them by the limit of a module (4 px), they join into the key;
- one code for AmneziaWG / incy / WireGuard (they read one QR, k3/handoff/FIX-333-qr.md): above MAX_VERSION the window
  shows «QR слишком большой» with the file and the page on the phone, not the code;
- the same rule of the level and the same versions in Python, in KaskadQR and on /p/."""

import base64
import json
import os
import re
import shutil
import subprocess
import unittest

from kaskad import qr
from kaskad.vendor import awgkeys
from tests.gw.tunnel_case import GwCase, device, gw_state
from tests.test_332f_give import HAS_NODE, issuance, prelude, read, run_node

HAS_QRENCODE = bool(shutil.which("qrencode"))
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def qrencode_version(text, level):
    """Version of the code qrencode makes of ``text`` in the byte mode (-8), None — does not fit."""
    p = subprocess.run(["qrencode", "-8", "-l", level, "-t", "ASCII", "-m", "0", "-o", "-"], input=text.encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if p.returncode:
        return None
    return (len(p.stdout.decode().splitlines()) - 17) // 4


class LimitTest(unittest.TestCase):
    def test_the_limit_is_the_size_of_a_module(self):
        """MAX_VERSION — the largest code with a module ≥ 4 px (owner) in the largest frame of a computer (420 inside,
        quiet zone 2); the frame of a code is sized by that limit, a phone gives its whole width."""
        self.assertEqual((qr.MIN_MODULE_PX, qr.MIN_MODULE_PX_PHONE, qr.QUIET, qr.MAX_PARTS), (4.0, 3.6, 2, 2))
        self.assertEqual((qr.FRAME_PX, qr.FRAME_MAX_PX), (296, 420))
        self.assertEqual(qr.MAX_VERSION, 21)
        self.assertGreaterEqual(qr.module_px(qr.MAX_VERSION), qr.MIN_MODULE_PX)
        self.assertLess(qr.module_px(qr.MAX_VERSION + 1), qr.MIN_MODULE_PX)
        # a computer: the frame grows with the code up to 436 outside (≈ the owner's 420–440 for two parts, v20)
        self.assertEqual([qr.frame_for(v) for v in (5, 17, 19, 20, 21, 24)], [296, 356, 388, 404, 420, 420])
        for v in range(1, qr.MAX_VERSION + 1):
            self.assertGreaterEqual(qr.module_px(v, qr.frame_for(v)), qr.MIN_MODULE_PX, v)
        # a phone: the whole width there is (give.css: min(436px, 100vw − 72px) − 2×8), the limit of a phone
        self.assertEqual([qr.frame_px(w) for w in (None, 320, 360, 375, 390, 412)], [420, 232, 272, 287, 302, 324])
        self.assertEqual([qr.frame_for(20, w) for w in (360, 390)], [272, 302])
        self.assertEqual([qr.max_version(qr.frame_px(w), qr.MIN_MODULE_PX_PHONE) for w in (320, 360, 375, 390, 412)],
                         [10, 13, 14, 15, 17])
        # the old part of 850 bytes (1144 chars of base64url) — version 24, a module of 2.45 px: what the owner saw
        self.assertEqual(qr.version_of("A" * 1144), (24, "L"))
        self.assertLess(qr.module_px(24), qr.MIN_MODULE_PX)
        self.assertFalse(qr.scannable("A" * 1144))

    @unittest.skipUnless(HAS_QRENCODE, "qrencode is not installed")
    def test_capacity_is_that_of_qrencode(self):
        for ver in (1, 9, 10, 13, 15, 17, 18, 19, 24, 40):
            for level in ("L", "M"):
                cap = qr.byte_capacity(ver, level)
                self.assertEqual(qrencode_version("a" * cap, level), ver, (ver, level))
                if ver < 40:
                    self.assertEqual(qrencode_version("a" * (cap + 1), level), ver + 1, (ver, level))

    def test_level_rule(self):
        """L for the smallest version; M only when it fits the same version."""
        for n in (10, 14, 15, 100, 300, 412, 413, 520, 600, 700, 1144, 2953):
            ver, level = qr.version_of("a" * n)
            self.assertGreaterEqual(qr.byte_capacity(ver, "L"), n)
            if ver > 1:
                self.assertLess(qr.byte_capacity(ver - 1, "L"), n)
            self.assertEqual(level == "M", qr.byte_capacity(ver, "M") >= n, n)
        self.assertEqual(qr.version_of("a" * 2954), (None, None))

    def test_parts_fit_and_are_even(self):
        """Two parts at most for any length (owner, 3.3.3); one when it fits MAX_VERSION; parts of one size; the bytes
        join back. Two parts of a real key (3.1 ≈1130–1180 bytes, 2.0 ≈960, 1.5 ≈905) fit a computer's frame."""
        for total in list(range(1, 3001, 37)) + [1133, 1178, 957, 902, 1300]:
            size = qr.part_size(total)
            raw = bytes((i * 31 + 7) % 256 for i in range(total))
            parts = awgkeys.qr_chunks_from_bytes(raw, chunk_size=size)
            self.assertIn(len(parts), (1, 2), total)
            one = qr.version_of("A" * len(awgkeys.b64url_encode(b"\0" * (8 + total))))[0]
            self.assertEqual(len(parts) == 1, bool(one) and one <= qr.MAX_VERSION, total)
            lens = [len(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4))) - 8 for p in parts]
            self.assertLessEqual(max(lens) - min(lens), 1, total)
            if total <= 1300:
                for p in parts:
                    self.assertLessEqual(qr.version_of(p)[0], qr.MAX_VERSION, (total, len(p)))
            self.assertEqual(b"".join(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4))[8:] for p in parts), raw)


class CssTest(unittest.TestCase):
    """R-333-qr №6: the frame of the limit is the frame of give.css — a change of the CSS without the numbers of
    kaskad.qr (and KaskadQRSlides.FIT, compared in BrowserTest) fails here."""

    def test_the_frame_is_that_of_give_css(self):
        css = read("give.css")
        rule = re.search(r"\n\.gs-qr__frame \{([^}]*)\}", css).group(1)
        self.assertEqual(re.search(r"(?<![-\w])width: (\d+)px", rule).group(1), str(qr.FRAME_OUTER_PX))
        self.assertEqual(re.search(r"(?<![-\w])height: (\d+)px", rule).group(1), str(qr.FRAME_OUTER_PX))
        self.assertEqual(re.search(r"(?<![-\w])padding: (\d+)px;", rule).group(1), str(qr.FRAME_PAD_PX))
        self.assertNotRegex(rule, r"box-sizing: border-box")                  # the padding is outside the width
        self.assertEqual(qr.FRAME_PX, qr.FRAME_OUTER_PX - 2 * qr.FRAME_PAD_PX)
        phone = re.search(r"@media \(max-width: (\d+)px\) \{((?:(?!@media)[\s\S])*?)\.gs-qr__frame \{([^}]*)\}", css)
        self.assertEqual(phone.group(1), "767")
        self.assertIn("(max-width: 767px)", read("give.js"))                      # PHONE_MQ — the same width
        want = "min(%dpx, calc(100vw - %dpx))" % (qr.FRAME_MAX_OUTER_PX, qr.PHONE_GUTTER_PX)
        self.assertEqual(re.findall(r"(?:width|height): ([^;]+);", phone.group(3)), [want, want])
        # no other rule sizes the frame (here or in another stylesheet of the panel and /p/)
        sizing = []
        for name in sorted(os.listdir(STATIC)):
            if name.endswith(".css"):
                for sel, body in re.findall(r"([^{}]*\.gs-qr__frame)\s*\{([^}]*)\}", read(name)):
                    if re.search(r"(?<![-\w])(width|height|padding|box-sizing|border)\s*:", body):
                        sizing.append((name, sel.strip()))
        self.assertEqual(sizing, [("give.css", ".gs-qr__frame"), ("give.css", ".gs-qr__frame")])
        # the right column holds the largest frame of a computer with the padding of .gs-key (2×20)
        col = re.search(r"\n\.gs-col--r \{ flex: 0 1 (\d+)px;", css).group(1)
        pad = re.search(r"\n\.gs-key \{[^}]*?(?<![-\w])padding: (\d+)px;", css).group(1)
        self.assertGreaterEqual(int(col), qr.FRAME_MAX_OUTER_PX + 2 * int(pad))

    def test_the_code_fills_its_frame(self):
        """R-333-qr (repeat, Г): the limit counts the code across the whole frame — nothing inside may shrink it: no
        margin, padding, max-width or transform on .kq / .kq__frame / svg in the frame, whatever the stylesheets of the
        panel and /p/ give them; and no stylesheet scales or pads the code's own nodes."""
        css = read("give.css")
        for sel in (".gs-qr__frame .kq", ".gs-qr__frame .kq__frame", ".gs-qr__frame svg"):
            body = re.search(r"\n%s \{([^}]*)\}" % re.escape(sel), css).group(1)
            for prop in ("margin: 0;", "padding: 0;", "transform: none;"):
                self.assertIn(prop, body, (sel, prop))
            if sel != ".gs-qr__frame .kq":
                self.assertIn("max-width: none;", body, sel)
        self.assertRegex(css, r"\n\.gs-qr__frame \.kq__frame, \.gs-qr__frame \.kq__part \{ width: 100%; height: 100%; \}")
        names = [n for n in os.listdir(STATIC) if n.endswith(".css")]
        names += ["setup/" + n for n in os.listdir(os.path.join(STATIC, "setup")) if n.endswith(".css")]
        for name in sorted(names):
            for sel, body in re.findall(r"([^{}]*)\{([^}]*)\}", read(name)):
                if re.search(r"\.(kq__part|up-qr-svg|gx-qrart)\b|\.kq\b(?!-)", sel) and not sel.strip().startswith("@"):
                    self.assertNotRegex(body, r"(?<![-\w])(transform|zoom|scale)\s*:(?!\s*none\b)", (name, sel.strip()))
                    if re.search(r"\.kq__part|> svg|\.gx-qrart\b(?!\s*>\s*img)", sel) and "qs-qrs" not in sel:
                        self.assertNotRegex(body, r"(?<![-\w])padding\s*:", (name, sel.strip()))

    def test_the_focus_ring_of_the_code_is_inside(self):
        """R-333-qr, пятый круг №1: «Показать QR целиком» puts the bottom of .gs-qr right at the edge of the scroll — an
        outline outside (.gs :focus-visible, offset 2px) lost its bottom edge under the dialog. The code's own ring is
        drawn inside its box and wins over the common rule."""
        css = read("give.css")
        self.assertIn("\n.gs :focus-visible { outline: 2px solid var(--accent-text); outline-offset: 2px; }", css)
        self.assertIn("\n.gs .gs-qr:focus-visible { outline-offset: -2px; }", css)

    def test_every_place_draws_the_quiet_zone_the_limit_counts(self):
        """R-333-qr (repeat, Д): /p/ drew a quiet zone of 2 modules, the limit counted 4. Now give.js hands
        KaskadQRSlides.FIT.quiet to drawQr and the panel, the wizard and /p/ draw that very zone."""
        self.assertIn("o.drawQr(key.qr, i2, k && k.FIT ? k.FIT.quiet : 2)", read("give.js"))
        self.assertIn("drawQr: (item, i, q) => giveQr(arr(item.body)[i], q),", read("app.js"))
        self.assertIn("drawQr: (it, i, q) => qr(arr(it.body)[i], q),", read("setup/setup.js"))
        self.assertIn("Q.svg(Q.encode(String(text || \"\")), \"\", quiet)", read("setup/setup.js"))
        user = read("user.js")
        self.assertIn("drawQr: function (item, i, q) { return qrNode(Array.isArray(item.qr) ? item.qr[i] : null, q); },", user)
        self.assertRegex(user, r"var quiet = typeof q === 'number' && q >= 0 && q <= 4 \? q : 2, side = n \+ 2 \* quiet;")

    def test_the_page_code_box_is_that_of_give_js(self):
        js, css = read("give.js"), read("give.css")
        m = re.search(r"var PAGE_QUIET = (\d+), PAGE_PAD = (\d+), PAGE_MIN = (\d+);", js)
        quiet, pad, least = map(int, m.groups())
        self.assertEqual(quiet, 2)
        rule = re.search(r"\n\.gs-dense__qr \{([^}]*)\}", css).group(1)
        self.assertIn("padding: %dpx;" % pad, rule)
        self.assertIn("box-sizing: border-box", rule)
        self.assertIn("width: %dpx;" % (least + 2 * pad), rule)
        # the smallest box is enough for a link of /p/ with a long host and a port (version 5) at the limit
        self.assertGreaterEqual(least / (qr.size_of(5) + 2 * quiet), qr.MIN_MODULE_PX)


class AwgIssueTest(GwCase):
    """The real adapter: what the window gets for AmneziaVPN / DefaultVPN and for AmneziaWG on every line."""

    def issue(self, kind, platform="ios"):
        port = {"awg1": 40441, "awg2": 40442, "awg3": 40443}[kind]
        st = gw_state({kind: {"enabled": True, "port": port}}, [device("d-ph", "iPhone Виталия", platform, [kind])])
        ad = self.adapter(kind)
        ad.prepare(st, self.sec)
        return ad.issue(st, self.sec, "d-ph", platform)["items"]

    def test_vpn_parts_and_conf_codes(self):
        seen = {}
        for kind in ("awg3", "awg2", "awg1"):
            items = self.issue(kind)
            vpn = [it for it in items if it["format"] == "vpn"][0]["body"]
            codes = [it for it in items if it["format"] == "qr"]
            parts, conf = codes[0]["body"], codes[1]["body"]
            self.assertTrue(all(qr.qr_for(p) == "vpnpart" for p in parts), kind)
            self.assertEqual(awgkeys.qr_chunks_join(parts), vpn, kind)                 # the app joins them back
            self.assertIn(len(parts), (1, 2), kind)                                 # owner: two at most
            for p in parts:
                ver = qr.version_of(p)[0]
                self.assertLessEqual(ver, qr.MAX_VERSION, (kind, ver))
                # in the frame a computer gives it — at the owner's 4 px
                self.assertGreaterEqual(qr.module_px(ver, qr.frame_for(ver)), qr.MIN_MODULE_PX, kind)
            self.assertEqual(len(conf), 1)
            seen[kind] = (len(parts), qr.version_of(conf[0])[0], max(qr.version_of(p)[0] for p in parts))
        # 3.1, 2.0, 1.5: two parts (a key does not fit one code a camera takes), about version 20 for 3.1
        self.assertEqual([seen[k][0] for k in ("awg3", "awg2", "awg1")], [2, 2, 2], seen)
        self.assertIn(seen["awg3"][2], (19, 20, 21), seen)
        # the .conf of 3.1 (AmneziaWG, incy read one code): v19 — a computer's frame takes it now (388 px inside)
        self.assertLessEqual(seen["awg3"][1], qr.MAX_VERSION, seen)
        self.assertTrue(qr.scannable([it for it in self.issue("awg3") if it["format"] == "qr"][1]["body"][0]))
        # a phone of 390 does not: there it is folded (BrowserTest)
        self.assertGreater(seen["awg3"][1], qr.max_version(qr.frame_px(390), qr.MIN_MODULE_PX_PHONE), seen)

    def test_the_name_has_no_platform(self):
        """Owner, 3.3.3: «goKaskad PRO · <протокол> · <имя устройства>» — the wizard names a device «<кто> · <платформа>»
        (d.devName), the platform is not a part of the key's name (vpn:// description, vless:// after #)."""
        from kaskad.gw import issue_os
        for name, want in (("Мой телефон · Android TV", "Мой телефон"), ("Мама · iPhone и iPad", "Мама"),
                           ("Mom · iPhone / iPad", "Mom"), ("Папа · Другое", "Папа"), ("iPhone Маши", "iPhone Маши"),
                           ("Android TV", "Android TV"), ("Дача · Кухня", "Дача · Кухня")):
            self.assertEqual(issue_os.app_label("AmneziaWG 3.1", name), "goKaskad PRO · AmneziaWG 3.1 · " + want)
        port = 40443
        st = gw_state({"awg3": {"enabled": True, "port": port}}, [device("d-tv", "Мой телефон · Android TV", "androidtv", ["awg3"])])
        ad = self.adapter("awg3")
        ad.prepare(st, self.sec)
        vpn = [it for it in ad.issue(st, self.sec, "d-tv", "androidtv")["items"] if it["format"] == "vpn"][0]["body"]
        self.assertEqual(awgkeys.vpn_decode(vpn)["description"], "goKaskad PRO · AmneziaWG 3.1 · Мой телефон")

    def test_the_conf_name_has_no_platform(self):
        """R-333-qr ≤2 №4: the .conf's name is the tunnel's name in WireGuard / AmneziaWG / incy — the same rule, and
        «Появится как» (give.js: the file's name without .conf) says the same."""
        from kaskad.gw import issue_os
        for name, pf, want in (("Маша · iPhone и iPad", "ios", "goKaskadPro-masha.conf"),
                               ("Маша · Windows", "windows", "goKaskadPro-masha.conf"),
                               ("Mom · iPhone / iPad", "ios", "goKaskadPro-Mom.conf"),
                               ("Мама · Android", "android", "gKP-mama.conf"),
                               ("Телефон мамы", "ios", "goKaskadPro-telefon-mamy.conf"),
                               ("Дача · Кухня", "macos", "goKaskadPro-dacha-kuhnya.conf")):
            self.assertEqual(issue_os.conf_filename("awg3", "3.1", device={"id": "d1", "name": name, "platform": pf}), want)

    def test_the_wizard_names_a_nameless_device_device(self):
        """R-333-qr ≤2 №8: no «Без имени» in a key's name — the wizard's device without a person's name is
        «Устройство · <платформа>», the key — «… · Устройство»."""
        def text(name):
            with open(os.path.join(STATIC, "setup", name), encoding="utf-8") as f:
                return f.read()
        js = text("setup.js")
        # T13 круг 2 (Л-1): devName ещё и укладывает имя в 40 знаков — заглушка та же, «кто» — w0
        self.assertRegex(js, r'const w = w0 && w0 !== t\("d\.noName"\) \? w0 : t\("d\.anyDevice"\);')
        self.assertNotIn('t("d.devName", { who, p', js)                           # every device name goes through devName()
        self.assertNotIn('who: u.devWho || t("d.noName")', js)
        for lang, word in (("ru", "Устройство"), ("en", "Device")):
            self.assertIn('"d.anyDevice": "%s"' % word, text("setup-i18n-%s.js" % lang))
        from kaskad.gw import issue_os
        self.assertEqual(issue_os.app_label("AmneziaWG 3.1", "Устройство · iPhone и iPad"), "goKaskad PRO · AmneziaWG 3.1 · Устройство")

    def test_every_platform_title_is_cut_in_give_js(self):
        """give.js keyName («Появится как …») cuts the same titles as issue_os.device_title."""
        from kaskad.gw import issue_os
        js = read("give.js")
        tail = re.search(r"var PLATFORM_TAIL = / · \(\?:(.*)\)\$/;", js).group(1).replace("\\/", "/").split("|")
        self.assertEqual(set(tail), issue_os._platform_titles())

    def test_wg_conf_fits(self):
        st = gw_state({"wg": {"enabled": True, "port": 51820}}, [device("d-ph", "iPhone Виталия", "ios", ["wg"])])
        wg = self.adapter("wg")
        wg.prepare(st, self.sec)
        code = [it for it in wg.issue(st, self.sec, "d-ph")["items"] if it["format"] == "qr"][0]["body"][0]
        self.assertTrue(qr.scannable(code), qr.version_of(code))


def real_awg_items(line="3.1", big=False):
    """Items of an AmneziaWG issuance with codes of the real size (awg.py on iOS: vpn, parts, the .conf code, conf);
    ``big`` — a .conf above even the frame of a computer (a comment of 200 bytes: the window's «QR слишком большой»)."""
    params = awgkeys.gen_params(line)
    host = "k3kaskad.example.com"
    conf = awgkeys.render_conf(line, {"params": params, "client": {"ip": "10.8.1.2", "privkey": awgkeys.genkey(),
                                                                   "psk": awgkeys.genpsk()},
                                      "server": {"pubkey": awgkeys.pubkey(awgkeys.genkey()), "endpoint_host": host,
                                                 "port": 40443}, "dns": ["10.8.1.1"], "mtu": "1280"})
    if big:
        conf += "\n# " + "x" * 200 + "\n"
    vpn = awgkeys.vpn_encode({"container": "amnezia-awg2", "line": line, "params": params, "conf_text": conf,
                              "description": "goKaskad PRO · AmneziaWG %s · iPhone" % line, "hostName": host,
                              "port": 40443, "dns": ["10.8.1.1"], "subnet": "10.8.1.0/24"})
    raw = awgkeys.b64url_decode(vpn[6:])
    parts = awgkeys.qr_chunks_from_bytes(raw, chunk_size=qr.part_size(len(raw)))
    return [{"format": "vpn", "title": "vpn", "body": vpn, "filename": "", "notes": []},
            {"format": "qr", "title": "qr app", "body": parts, "filename": "", "notes": []},
            {"format": "qr", "title": "qr conf", "body": [conf], "filename": "", "notes": []},
            {"format": "conf", "title": "conf", "body": conf, "filename": "goKaskadPro-iphone-awg31.conf", "notes": []}]


WIDTHS = (320, 360, 375, 390, 412)


@unittest.skipUnless(HAS_NODE, "нет node")
class BrowserTest(unittest.TestCase):
    def test_the_same_numbers_and_versions_in_the_browser(self):
        texts = ["a" * n for n in (1, 14, 17, 100, 271, 272, 412, 413, 520, 521, 600, 718, 719, 929, 930, 1144, 2953,
                                   2954)]
        texts += ["я" * 300, "vpn://" + "A" * 300]
        js = read("qr.js") + "\n" + read("qr-slides.js") + r"""
const K = window.KaskadQRSlides, Q = window.KaskadQR, T = %s;
const enc = (t) => { try { const c = Q.encode(t); return [c.version, c.ecc]; } catch (e) { return [null, null]; } };
const W = [undefined, 320, 360, 375, 390, 412, 767];
console.log(JSON.stringify({ fit: K.FIT, versions: T.map((t) => K.versionOf(t)), enc: T.map(enc),
  frames: W.map((w) => K.frameAt(w)), maxes: W.map((w) => [K.maxVersionFor(K.frameAt(w), K.FIT.minPx), K.maxVersionFor(K.frameAt(w), K.FIT.minPxPhone)]),
  sides: [1, 5, 17, 19, 20, 21, 24].map((v) => K.sideFor(v)), phoneSides: [5, 20].map((v) => K.sideFor(v, K.frameAt(390), K.FIT.minPxPhone, true)),
  narrow: K.fit({ body: ["a".repeat(586)] }, { frame: K.frameAt(360), minPx: K.FIT.minPxPhone, phone: true }),
  quiet2: Q.svg(Q.encode("https://h.example/p/x"), "", 2).getAttribute("viewBox"),
  byN: [21, 77, 89, 93, 97, 101, 113, 20].map((n) => K.versionOf({ n: n })),
  item: [K.fit({ qr: [{ n: 77 }, { n: 89 }] }), K.fit({ qr: [{ n: 101 }] }), K.fit({ body: ["a".repeat(1144)] }),
         K.fit({ texts: ["a".repeat(100)] })] }));
""" % json.dumps(texts)
        dom = "global.document = { createElementNS: (ns, t) => ({ a: {}, setAttribute(k, v) { this.a[k] = String(v); }, " \
              "getAttribute(k) { return this.a[k]; }, appendChild() {} }) };\n"
        got = run_node("global.window = global;\n" + dom + js.replace("(typeof window !== 'undefined' ? window : globalThis)", "(global)"))
        self.assertEqual(got["fit"], {"outer": qr.FRAME_OUTER_PX, "maxOuter": qr.FRAME_MAX_OUTER_PX, "pad": qr.FRAME_PAD_PX,
                                      "gutter": qr.PHONE_GUTTER_PX, "frame": qr.FRAME_PX, "frameMax": qr.FRAME_MAX_PX,
                                      "quiet": qr.QUIET, "minPx": qr.MIN_MODULE_PX, "minPxPhone": qr.MIN_MODULE_PX_PHONE,
                                      "maxVersion": qr.MAX_VERSION, "maxParts": qr.MAX_PARTS})
        widths = [None, 320, 360, 375, 390, 412, 767]
        self.assertEqual(got["frames"], [qr.frame_px(w) for w in widths])
        self.assertEqual(got["maxes"], [[qr.max_version(qr.frame_px(w), qr.MIN_MODULE_PX),
                                         qr.max_version(qr.frame_px(w), qr.MIN_MODULE_PX_PHONE)] for w in widths])
        self.assertEqual(got["sides"], [qr.frame_for(v) for v in (1, 5, 17, 19, 20, 21, 24)])
        self.assertEqual(got["phoneSides"], [qr.frame_for(v, 390) for v in (5, 20)])
        # v16 at 360 px of a phone: 272 / 85 = 3.2 px < 3.6 — not taken (on a computer it is)
        self.assertEqual((got["narrow"]["ver"], got["narrow"]["ok"], got["narrow"]["max"], got["narrow"]["side"]),
                         (16, False, 13, 272))
        self.assertEqual(got["quiet2"], "0 0 29 29")                                # KaskadQR.svg with a quiet zone of 2
        for t, v, e in zip(texts, got["versions"], got["enc"]):
            want = qr.version_of(t)
            self.assertEqual(v or None, want[0], len(t))
            self.assertEqual(tuple(e), want, len(t))                               # KaskadQR draws that very version
        self.assertEqual(got["byN"], [1, 15, 18, 19, 20, 21, 24, 0])
        self.assertEqual([(x["ver"], x["ok"]) for x in got["item"]], [(18, True), (21, True), (24, False), (5, True)])
        self.assertEqual([x["side"] for x in got["item"]], [qr.frame_for(18), qr.frame_for(21), qr.FRAME_MAX_PX,
                                                            qr.frame_for(5)])

    def window(self, app, viewer="admin", phone=False, page="", show=False, width=None, line="3.1", share=False,
               acts=(), big=False, client_width=None):
        """The window of an AmneziaWG issuance with codes of the real size; ``acts`` — gk of buttons to press after the
        app (and «Всё равно показать», when ``show``); ``vis`` — every QR the camera sees after each step."""
        r = issuance({"3.1": "awg3", "2.0": "awg2", "1.5": "awg1"}[line], "ios")
        r["items"] = real_awg_items(line, big)
        js = prelude() + r"""
PHONE = %s;
if (%s) global.innerWidth = %s;
if (%s) document.documentElement = { clientWidth: %s };
if (%s) { global.File = function (b, n) { this.name = n; }; navigator.canShare = () => true; navigator.share = () => ({ catch() {} }); }
const R = %s, out = {};
const g = G.mount({ viewer: %s, lang: "ru", device: { name: "iPhone" }, pageUrl: %s,
  drawQr: (it, i, q) => { const n = document.createElement("i"); n.setAttribute("data-code", String(i)); n.setAttribute("data-q", String(q)); return n; },
  drawLinkQr: (l) => { const n = document.createElement("b"); n.className = "lq"; n.setAttribute("data-url", l.url); n.setAttribute("data-q", String(l.quiet)); return n; } });
document.body.appendChild(g.el);
g.set(R);
const root = g.el;
// QR the camera sees: the frame of the key code and every link code, not under a cover (is-covered — blurred)
const up = (n, c) => { for (; n; n = n.parentNode) if (n.classList && n.classList.contains(c)) return true; return false; };
const vis = () => root.all((n) => n.classList && (n.classList.contains("gs-qr__frame") || n.classList.contains("lq")))
  .filter((n) => !up(n, "is-covered")).map((n) => n.classList.contains("lq") ? n.getAttribute("data-url") : "key");
out.vis = [];
const step = (k) => { const b = gk(root, k); out.vis.push([k, b ? vis() : null]); if (b) fire(b, "click"); };
if (%s !== "defaultvpn") fire(gk(root, "app:" + %s), "click");
out.path0 = g.model().path;
out.vis.push(["start", vis()]);
if (%s) { fire(gk(root, "denseshow"), "click"); out.vis.push(["denseshow", vis()]); }
for (const k of %s) { step(k); out.vis.push(["after " + k, vis()]); }
const fr = root.querySelector(".kq");
const kf = root.querySelector(".gs-qr__frame");
out.app = g.model().app.id;
out.path = g.model().path;
const F = g.model().key.fit;
out.key = { parts: g.model().key.parts, dense: g.model().key.dense, ver: F && F.ver, max: F && F.max, side: F && F.side, px: F && F.px };
out.qr = !!root.querySelector(".gs-qr");
out.keySide = kf ? kf.getAttribute("data-side") : null;
out.keyW = kf ? [kf.style.width, kf.style.height] : null;
const dc = root.all((n) => n.getAttribute && n.getAttribute("data-code") != null)[0];
out.keyQuiet = dc ? dc.getAttribute("data-q") : null;
out.n = fr ? fr.getAttribute("data-n") : null;
// подпись кода для чтения с экрана: все aria-label внутри .gs-qr (с ней самой), роль рамки qr-slides, рисунок части
const qn = root.querySelector(".gs-qr");
out.names = qn ? qn.all((n) => n.getAttribute && n.getAttribute("aria-label") != null).map((n) => n.getAttribute("aria-label")) : null;
out.kqRole = fr ? fr.getAttribute("role") : null;
out.codeHidden = root.all((n) => n.getAttribute && n.getAttribute("data-code") != null).map((n) => n.getAttribute("aria-hidden"));
out.dense = has(root, "gs-dense");
out.page = has(root, "gs-dense__page") ? root.querySelector(".lq").getAttribute("data-url") : null;
out.pageQuiet = has(root, "gs-dense__page") ? root.querySelector(".lq").getAttribute("data-q") : null;
out.side = has(root, "gs-dense__qr") ? Number(root.querySelector(".gs-dense__qr").getAttribute("data-side")) : null;
out.sideCss = has(root, "gs-dense__qr") ? root.querySelector(".gs-dense__qr").style.width : null;
out.covered = has(root, "gs-dense__page") ? root.querySelector(".gs-dense__page").classList.contains("is-covered") : null;
out.warn = has(root, "gs-dense__warn");
out.warnText = has(root, "gs-dense__warn") ? root.querySelector(".gs-dense__warn").textContent : "";
out.qrph = !!gk(root, "qrph");
out.dl = !!gk(root, "dl");
out.share = !!gk(root, "share");
out.text = root.querySelector(".gs-dense") ? root.querySelector(".gs-dense").textContent : "";
out.all = root.textContent;
out.pathFile = G.t("ru", "pathFile." + out.app); out.pathQr = G.t("ru", "path." + out.app);
console.log(JSON.stringify(out));
""" % (json.dumps(phone), json.dumps(width is not None), json.dumps(width), json.dumps(client_width is not None),
       json.dumps(client_width), json.dumps(share), json.dumps(r, ensure_ascii=False), json.dumps(viewer),
       json.dumps(page), json.dumps(app), json.dumps(app), json.dumps(show), json.dumps(list(acts)))
        return run_node(js)

    def test_defaultvpn_gets_two_parts_in_a_frame_by_the_limit(self):
        """Owner, 3.3.3: two parts at most; on a computer the frame grows to the limit of 4 px (≈420–436 for v20–21)."""
        for line in ("3.1", "2.0", "1.5"):
            got = self.window("defaultvpn", line=line)
            self.assertEqual(got["app"], "defaultvpn")
            self.assertEqual(got["key"]["parts"], 2, line)
            self.assertEqual(got["n"], "2")
            self.assertFalse(got["key"]["dense"], (line, got["key"]))
            self.assertLessEqual(got["key"]["ver"], qr.MAX_VERSION)
            self.assertGreaterEqual(got["key"]["px"], qr.MIN_MODULE_PX)
            outer = qr.frame_for(got["key"]["ver"]) + 2 * qr.FRAME_PAD_PX
            self.assertEqual(got["keySide"], str(outer))
            self.assertEqual(got["keyW"], ["%dpx" % outer] * 2)                    # CSSOM: the frame of that side
            self.assertEqual(got["keyQuiet"], str(qr.QUIET))                       # drawn with the quiet zone counted
            self.assertTrue(got["qr"])
            self.assertFalse(got["dense"])
        self.assertIn(self.window("defaultvpn")["keySide"], ("404", "420", "436"))

    def test_parts_on_a_phone_fold_with_the_note(self):
        """A phone: the frame takes the whole width; two parts of v18–21 are under the limit of a phone there — folded
        by default, «Всё равно показать» opens them with «QR плотный — лучше «Скопировать ключ»…»."""
        for w in WIDTHS:
            got = self.window("defaultvpn", phone=True, width=w)
            self.assertEqual(got["key"]["parts"], 2)
            self.assertEqual(got["key"]["side"], qr.frame_px(w))
            self.assertTrue(got["key"]["dense"], (w, got["key"]))
            self.assertFalse(got["qr"])
            self.assertIn("QR слишком плотный для этого экрана", got["text"])
            self.assertNotIn("по частям", got["text"])                            # DefaultVPN does read parts
            shown = self.window("defaultvpn", phone=True, width=w, show=True)
            self.assertTrue(shown["qr"])
            self.assertEqual(shown["keyW"], ["", ""])                             # a phone: the width of give.css
            self.assertEqual(shown["warnText"], "QR плотный — лучше «Скопировать ключ» или откройте на компьютере.")

    def test_the_width_is_that_of_the_layout_viewport(self):
        """R-333-qr (repeat, В): the frame of a phone follows documentElement.clientWidth (100vw of @media), not
        innerWidth — iOS changes it with a pinch."""
        got = self.window("amneziawg", line="1.5", phone=True, width=320, client_width=412)
        self.assertEqual(got["key"]["side"], qr.frame_px(412))
        self.assertFalse(got["key"]["dense"], got["key"])                         # v16 at 412: taken
        got = self.window("amneziawg", line="1.5", phone=True, width=412, client_width=320)
        self.assertTrue(got["key"]["dense"], got["key"])

    def test_the_code_is_named_once_for_a_screen_reader(self):
        """R-333-qr, пятый круг №2: a code of two parts was named three times (the group, the figure of qr-slides and
        its caption). One name now — on the group .gs-qr, the focus of «Показать QR целиком»; the figure has no role
        and no label, the drawing of a part is hidden from a screen reader. One part — the same."""
        for app, parts in (("defaultvpn", 2), ("amneziawg", 1)):
            got = self.window(app)
            self.assertEqual(got["key"]["parts"], parts, app)
            self.assertEqual(got["names"], ["QR-код ключа"], app)
            self.assertEqual(got["kqRole"], "none", app)
            self.assertTrue(got["codeHidden"], app)
            self.assertEqual(set(got["codeHidden"]), {"true"}, app)

    def test_amneziawg_31_conf_is_one_code_on_a_computer(self):
        """Recounted with the new frame: the .conf of 3.1 (v19) — one code in a frame of 404 px at 4 px a module."""
        got = self.window("amneziawg")
        self.assertEqual(got["app"], "amneziawg")
        self.assertFalse(got["key"]["dense"], got["key"])
        self.assertEqual(got["key"]["parts"], 1)
        self.assertTrue(got["qr"])
        self.assertEqual(got["keySide"], str(qr.frame_for(got["key"]["ver"]) + 2 * qr.FRAME_PAD_PX))
        self.assertGreaterEqual(got["key"]["px"], qr.MIN_MODULE_PX)
        self.assertFalse(got["dense"])

    def test_a_dense_conf_gets_the_file_not_the_code(self):
        got = self.window("amneziawg", big=True, page="https://k3kaskad.example.com/p/abcdefghijklmnopqrstuv")
        self.assertTrue(got["key"]["dense"])
        self.assertGreater(got["key"]["ver"], qr.MAX_VERSION)
        self.assertFalse(got["qr"])                                   # no unreadable code as the way to take the key
        self.assertIn("QR слишком большой для этого приложения", got["text"])
        self.assertTrue(got["dl"])                                    # «Скачать .conf» stays
        self.assertEqual(got["page"], "https://k3kaskad.example.com/p/abcdefghijklmnopqrstuv")
        self.assertIn("пусть пользователь откроет свою страницу", got["text"])   # the admin's words
        # R-333-qr (repeat, А): the admin gives a key to another — «перешлите его человеку», not «себе на телефон»
        self.assertIn("перешлите его пользователю", got["text"])
        self.assertNotIn("себе", got["text"])
        person = self.window("amneziawg", big=True, viewer="person", page="https://h.example/p/abcdefghijklmnopqrstuv")
        self.assertIn("откройте эту страницу на телефоне", person["text"])
        self.assertIn("перешлите его себе на телефон", person["text"])
        self.assertNotIn("пользователю", person["text"])

    def test_show_anyway_and_phone(self):
        got = self.window("amneziawg", big=True, show=True)
        self.assertTrue(got["qr"])
        self.assertTrue(got["warn"])
        self.assertEqual(got["page"], None)                           # no page known — no page code
        ph = self.window("amneziawg", phone=True, width=390, page="https://h.example/p/abcdefghijklmnopqrstuv")
        self.assertTrue(ph["dense"])
        self.assertFalse(ph["qrph"])                                  # no «QR для другого устройства» of a dense code
        self.assertIsNone(ph["page"])                                 # on the phone itself — no code of its own page
        self.assertTrue(ph["dl"])
        self.assertIn("перешлите его пользователю — он откроет его в AmneziaWG", ph["text"])     # the admin (А)
        me = self.window("amneziawg", viewer="person", phone=True, width=390)
        self.assertIn("Скачайте файл и откройте его в AmneziaWG", me["text"])
        self.assertTrue(self.window("incy", big=True)["dense"])
        self.assertFalse(self.window("incy")["dense"])

    def test_one_qr_on_the_screen_in_any_state(self):
        """R-333-qr №1: the card «QR · установить» over a dense key covered the key, not the code of the person's page —
        two codes on the screen. In any state of the window the camera sees one QR at most."""
        page = "https://h.example/p/" + "A" * 43
        got = self.window("amneziawg", big=True, page=page, acts=("store:amneziawg",))
        self.assertTrue(got["covered"])                                       # the page code is under the cover
        self.assertEqual(len(got["vis"][-1][1]), 1, got["vis"])                # only the code of the store
        self.assertTrue(got["vis"][-1][1][0].startswith("https://apps.apple.com/"), got["vis"])
        acts = ("store:{a}", "showkey", "store:{a}", "store:{a}", "qrph", "denseshow", "store:{a}", "qrph")
        for phone in (False, True):
            for app in ("defaultvpn", "amneziawg", "incy"):
                for show in (False, True):
                    for line, big in (("3.1", False), ("3.1", True), ("2.0", False)):
                        got = self.window(app, page=page, phone=phone, show=show, line=line, big=big,
                                          width=390 if phone else None, acts=[x.format(a=app) for x in acts])
                        for k, seen in got["vis"]:
                            if seen is not None:
                                self.assertLessEqual(len(seen), 1, (phone, app, show, line, big, k, seen))

    def test_parts_are_two_at_most_everywhere(self):
        """Owner, 3.3.3: no more than 2 parts for any line, place and width; the code shown by default is at the limit
        of its frame (a computer — 4 px, a phone — 3.6 px); the parts join into the key."""
        for line in ("3.1", "2.0", "1.5"):
            items = real_awg_items(line)
            self.assertEqual(awgkeys.qr_chunks_join(items[1]["body"]), items[0]["body"])
            for viewer in ("admin", "person", "wizard"):
                for width in (None,) + WIDTHS:
                    for app in ("defaultvpn", "amneziawg"):
                        got = self.window(app, viewer=viewer, line=line, phone=width is not None, width=width)
                        where = (line, viewer, width, app, got["key"])
                        self.assertLessEqual(got["key"]["parts"], qr.MAX_PARTS, where)
                        if not got["key"]["dense"]:                            # offered by default — at the limit
                            limit = qr.MIN_MODULE_PX if width is None else qr.MIN_MODULE_PX_PHONE
                            self.assertGreaterEqual(got["key"]["px"], limit, where)
                            # a computer shows it, a phone folds it under «QR для другого устройства» (as before)
                            self.assertEqual(got["qr"], width is None, where)
                            self.assertEqual(got["qrph"], width is not None, where)
                            self.assertEqual(len(got["vis"][0][1]), 1 if width is None else 0, where)
                        else:
                            self.assertFalse(got["qr"], where)
                            self.assertTrue(got["dense"], where)

    def test_the_page_code_is_large_enough_for_a_long_host(self):
        """R-333-qr №3: the code of the page in «QR слишком большой» is at the limit of a computer for any link — a long
        host with a port too — with a quiet zone of 2 modules."""
        for page in ("https://h.example/p/" + "A" * 43,
                     "https://" + "k" * 30 + ".very-long-subdomain.example.com:8443/p/" + "A" * 43,
                     "https://" + "k" * 63 + "." + "s" * 40 + ".example.com:65535/kaskad/p/" + "A" * 43):
            got = self.window("amneziawg", big=True, page=page)
            ver = qr.version_of(page)[0]
            self.assertEqual(got["pageQuiet"], "2")
            self.assertEqual(got["sideCss"], "%dpx" % got["side"])
            inner = got["side"] - 2 * 6
            self.assertGreaterEqual(inner / (qr.size_of(ver) + 4), qr.MIN_MODULE_PX, (len(page), ver, got["side"]))
            self.assertGreaterEqual(got["side"], 176)

    def test_the_frame_of_a_narrow_phone(self):
        """R-333-qr №4: the limit is counted on the frame of the screen (272 px at 360) with the limit of a phone."""
        desk = self.window("amneziawg", line="2.0")
        self.assertFalse(desk["key"]["dense"])                                 # .conf 2.0 (v17) — a computer takes it
        self.assertEqual(desk["key"]["max"], qr.MAX_VERSION)
        narrow = self.window("amneziawg", line="2.0", phone=True, width=360)
        self.assertTrue(narrow["key"]["dense"])
        self.assertEqual(narrow["key"]["max"], qr.max_version(qr.frame_px(360), qr.MIN_MODULE_PX_PHONE))
        self.assertIn("QR для другого устройства здесь получился бы слишком плотным", narrow["text"])

    def test_show_anyway_on_a_phone_and_the_words(self):
        """R-333-qr №8: no «version 19»; on a phone «Всё равно показать QR» opens the code at once; «Поделиться файлом»
        only with its button; step 3 after the code is shown — import from the QR."""
        ph = self.window("amneziawg", phone=True, width=390, show=True)
        self.assertTrue(ph["qr"])                                              # one press — the code is there
        self.assertTrue(ph["warn"])
        # R-333-qr ≤2 №6: under a .conf the button is «Скачать .conf» — the note names it, not «Скопировать ключ»
        self.assertEqual(ph["warnText"], "QR плотный — лучше «Скачать .conf» или откройте на компьютере.")
        self.assertNotRegex(ph["all"], r"[Вв]ерси[яи] \d")
        plain = self.window("amneziawg", phone=True, width=390)
        # №5: on a phone it is the screen, not the app (the same .conf is one code on a computer)
        self.assertIn("QR слишком плотный для этого экрана", plain["text"])
        self.assertNotIn("для этого приложения", plain["text"])
        self.assertNotIn("Поделиться файлом", plain["text"])
        self.assertFalse(plain["share"])
        sh = self.window("amneziawg", viewer="person", phone=True, width=390, share=True)
        self.assertTrue(sh["share"])
        self.assertIn("«Поделиться файлом» → AmneziaWG", sh["text"])
        desk = self.window("amneziawg", big=True, page="https://h.example/p/" + "A" * 43)
        self.assertIn("QR слишком большой для этого приложения", desk["text"])   # too dense even in the largest frame
        self.assertNotIn("Поделиться файлом", desk["text"])
        self.assertNotIn("скачайте его там", desk["text"])                    # no «download it there» without a page
        self.assertEqual(" › ".join(desk["path0"]), desk["pathFile"])           # dense: the file
        shown = self.window("amneziawg", big=True, show=True)
        self.assertIn("QR очень плотный — камера может его не взять", shown["all"])
        self.assertEqual(" › ".join(shown["path"]), shown["pathQr"])            # the code is shown: from the QR
        self.assertNotEqual(shown["pathFile"], shown["pathQr"])

    def test_resize_without_a_key_does_not_redraw(self):
        """R-333-qr (repeat, Б): on a phone the window without a key code (L2TP: fields only) is not drawn anew on every
        resize; with a code — only when the limit of the version changes."""
        def run(kind, widths):
            r = issuance(kind, "ios")
            if kind == "awg3":
                r["items"] = real_awg_items("1.5")
            js = prelude() + r"""
PHONE = true; global.innerWidth = 412;
const RS = []; global.addEventListener = (e, f) => { if (e === "resize") RS.push(f); }; global.removeEventListener = () => {};
const g = G.mount({ viewer: "admin", lang: "ru", device: { name: "iPhone" } });
document.body.appendChild(g.el);
g.set(%s);
if (gk(g.el, "app:amneziawg")) fire(gk(g.el, "app:amneziawg"), "click");
const out = [];
for (const w of %s) { const before = g.el.firstChild.firstChild; global.innerWidth = w; RS.forEach((f) => f()); out.push(g.el.firstChild.firstChild === before); }
console.log(JSON.stringify(out));
""" % (json.dumps(r, ensure_ascii=False), json.dumps(widths))
            return run_node(js)
        self.assertEqual(run("l2tp", [411, 400, 360, 320]), [True, True, True, True])
        # .conf 1.5 (v16): 412 takes it, 400 — the same limit (no redraw), 360 — no longer (redraw)
        self.assertEqual(run("awg3", [411, 360, 359]), [True, False, True])

    def test_words_in_both_languages(self):
        keys = ("denseT", "denseTScreen", "denseFile", "denseFilePhone", "denseFilePhoneShare", "denseText", "denseTextPhone",
                "denseFileAdmin", "denseFilePhoneAdmin", "denseFilePhoneShareAdmin", "denseTextAdmin", "denseWarnPhone", "denseWarnPhoneFile",
                "densePage", "densePageAdmin", "denseShow", "denseWarn", "shareFile", "theApp",
                "denseTextNarrow", "denseTextNarrowAdmin", "denseFileNarrow", "denseFileNarrowAdmin", "qrWhole")
        for name in ("give-i18n-ru.js", "give-i18n-en.js"):
            text = read(name)
            for k in keys:
                self.assertRegex(text, r"\n  %s: '" % k, (name, k))


if __name__ == "__main__":
    unittest.main()

"""3.3.2: the four small findings of the final check on .67 (k3/stand/logs/T-67-332b.md).

№1 the chip «person · device» of the issue window at 390 px: the person's name never breaks, a long device name wraps
inside the chip; №2 Esc with the pointer resting on a note's triangle closes the note, the next Esc — the window;
№3 one wording of «on the server, not for this device» in the panel and on /p/; №4 the language saved by
``kaskadctl lang set`` is seen by the panel's journal at once, even when the file keeps its size and its mtime."""

import io
import json
import os
import re
import unittest
from unittest import mock

from kaskad import i18n, paths, util
from kaskad.i18n import set_lang
from tests import jsparts
from tests.helpers import RootCase
from tests.test_332f_give import HAS_NODE, issuance, prelude, read, run_node

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def css_rule(css, selector):
    m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return dict((k.strip(), v.strip()) for k, v in (d.split(":", 1) for d in m.group(1).split(";") if ":" in d)) if m else None


class WhoChipTest(unittest.TestCase):
    """№1: the person whole, the device wraps inside, the chip grows; one line — the old 32 px."""

    def test_css(self):
        css = read("give.css")
        chip, name, dev = css_rule(css, ".gs-who"), css_rule(css, ".gs-who b"), css_rule(css, ".gs-who__dev")
        self.assertEqual((chip["min-height"], chip["height"], chip["min-width"], chip["max-width"]),
                         ("32px", "auto", "0", "100%"))
        self.assertNotIn("999px", chip["border-radius"])                    # a pill of two lines would cut the text
        self.assertEqual(css_rule(css, ".gs-who__txt")["min-width"], "0")   # the text block shrinks inside the chip
        self.assertEqual(name["white-space"], "nowrap")                      # «qa-Анна» never breaks at the hyphen
        self.assertEqual((name["max-width"], name["text-overflow"]), ("100%", "ellipsis"))   # a huge one — «…»
        self.assertEqual(dev["overflow-wrap"], "anywhere")                   # the device wraps, never sticks out
        self.assertEqual(css_rule(css, ".gs-who__ava")["flex"], "none")

    def test_chip_markup(self):
        src = read("app.js")
        # Т3: чипа gs-who в окне больше нет — кто и какое устройство: заголовок окна и плашки устройств; имя целиком —
        # в заголовке (выбранное устройство) и в подсказке плашки (плашка обрезает длинное многоточием)
        give = jsparts.function(src, "openGive")
        # О5 (R-it1-fix): «Как подключить «<устройство>»?» за выбранным устройством, пользователь — в подстроке
        self.assertIn('title: giveTitle(st.dev), sub: giveSub(u, st.dev)', give)
        self.assertIn('title: [x.name, platText(x.platform)].filter(Boolean).join(" · "),', give)
        self.assertIn("txt(x.name)", give)


@unittest.skipUnless(HAS_NODE, "нет node")
class EscUnderPointerTest(unittest.TestCase):
    """№2: the triangle is rebuilt under a resting pointer and the browser sends it mouseenter — no reopening until
    the mouse really moves; so the first Esc closes the note, the second reaches the dialog."""

    def test_esc_with_pointer_on_triangle(self):
        r = issuance("awg3", "keenetic")
        js = prelude() + r"""
const R = %s, out = {}, passed = [];
document.addEventListener("keydown", () => passed.push("dialog"));      // the dialog listens after the component
const g = G.mount({ viewer: "admin", lang: "ru", selectors: true, device: { name: "qa-iPhone" },
  onPlatform: () => {}, onProto: () => {}, drawQr: () => document.createElement("i"), drawLinkQr: () => document.createElement("b") });
document.body.appendChild(g.el);
g.set(R);
const root = g.el, K = "tip:router";           // круг 1: треугольник остался только у шага роутера (и у других протоколов)
const wrap = () => gk(root, K).parentNode;
fire(document.body, "mousemove", { clientX: 40, clientY: 50 });
fire(wrap(), "mouseenter");                                             // hover opens the note
out.hover = has(root, "gs-tip");
fire(document.body, "keydown", { key: "Escape" });                     // Esc №1
out.esc1 = { tip: has(root, "gs-tip"), passed: passed.length };
fire(wrap(), "mouseenter");                                             // the rebuilt triangle under the pointer
fire(document.body, "mousemove", { clientX: 40, clientY: 50 });        // a mousemove of the layout: same place
fire(wrap(), "mouseenter");
out.rebuilt = { tip: has(root, "gs-tip"), expanded: gk(root, K).getAttribute("aria-expanded") };
fire(document.body, "keydown", { key: "Escape" });                     // Esc №2 — to the dialog
out.esc2 = passed.slice();
fire(document.body, "mousemove", { clientX: 44, clientY: 52 });        // the mouse really moves
fire(wrap(), "mouseenter");
out.moved = has(root, "gs-tip");
fire(document.body, "keydown", { key: "Escape" });
out.esc3 = { tip: has(root, "gs-tip"), passed: passed.length };
fire(gk(root, K), "click");                                             // a click pins it whatever the pointer
out.click = has(root, "gs-tip");
g.destroy();
console.log(JSON.stringify(out));
""" % json.dumps(r)
        got = run_node(js)
        self.assertTrue(got["hover"])
        self.assertEqual(got["esc1"], {"tip": False, "passed": 0})
        self.assertEqual(got["rebuilt"], {"tip": False, "expanded": "false"})
        self.assertEqual(got["esc2"], ["dialog"])
        self.assertTrue(got["moved"])
        self.assertEqual(got["esc3"], {"tip": False, "passed": 1})
        self.assertTrue(got["click"])

    def test_listener_removed_on_destroy(self):
        src = read("give.js")
        self.assertIn("document.addEventListener('mousemove', onMove, true);", src)
        self.assertIn("document.removeEventListener('mousemove', onMove, true);", src)
        self.assertIn("if (!hoverMute && !st.pin && st.tip !== id)", src)


class DeviceOffWordsTest(unittest.TestCase):
    """№3: «this device» in both places and both languages; the admin sees «not given» beside his button, the person —
    «not for this device» and whom to ask."""

    def words(self, lang):
        src = read("give-i18n-%s.js" % lang)
        return dict(re.findall(r"^\s*(altDevice|pDevice|askAdd|addDev): '([^']*)'", src, re.M))

    def test_ru_en(self):
        ru, en = self.words("ru"), self.words("en")
        self.assertEqual(ru["altDevice"], "Включён на сервере, не выдан этому устройству")
        self.assertEqual(ru["pDevice"], "Включён на сервере, но не для этого устройства")
        self.assertEqual(ru["askAdd"], "Попросите администратора добавить {proto} этому устройству")
        self.assertEqual(en["altDevice"], "Enabled on the server, not given to this device")
        self.assertEqual(en["pDevice"], "Enabled on the server, but not for this device")
        self.assertEqual(en["askAdd"], "Ask your admin to add {proto} to this device")
        for w in (ru["altDevice"], ru["pDevice"], ru["askAdd"]):
            self.assertRegex(w, r"эт(ому|ого) устройств")
        for w in (en["altDevice"], en["pDevice"], en["askAdd"]):
            self.assertIn("this device", w)


class LangSeenAtOnceTest(RootCase):
    """№4: another process (the panel) sees the new language on its very next journal line — the file written by
    set_lang() has the same size («ru»/«en») and may share the mtime tick: the inode tells them apart."""

    def test_same_size_same_mtime(self):
        from kaskad.panel import server
        set_lang("ru")
        self.assertEqual(i18n.saved_lang(), "ru")                             # cached, as in the running panel
        p = paths.config()
        before = os.stat(p)
        text = util.read_text(p).replace('KASKAD_LANG="ru"', 'KASKAD_LANG="en"')
        util.atomic_write(p, text, 0o600)                                     # kaskadctl lang set en, another process
        os.utime(p, ns=(before.st_atime_ns, before.st_mtime_ns))             # the same tick of mtime
        self.assertEqual(os.stat(p).st_size, before.st_size)
        buf = io.StringIO()
        with mock.patch("sys.stderr", buf):
            server.log_t("journal.srv.reloaded")
        self.assertEqual(buf.getvalue(), "kaskad-panel: settings reloaded\n")


if __name__ == "__main__":
    unittest.main()

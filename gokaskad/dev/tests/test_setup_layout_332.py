"""Kaskad 3.3.2, critic-fix L — the layout of the wizard «Быстрый старт» and of the panel
(k3/stand/logs/T-332-critic.md, findings 13–17, 37–40, 42, 43):

    №13  stage 6, phone: the inputs of the device wrap to new rows — none of them hides in a sideways scroll;
    №14  stages 1 and 5, narrow phone: «Choose file» and the chip «not set up» go under the text;
    №15  stage 0: the field of the password is a frame around the input and «Show» — the button is in the flow, the hint
         in the field never goes under it (no fixed padding for one language);
    №16  the switch of the app above the QR: labels in one line, the width of a segment by its label;
    №17  the QR is 260 px at every width and in the middle; the caption is wider than the code;
    №37  the bar of the phone always has the height of two lines; the main button is never narrower than the longest
         label of a step (with the ring «busy») — «Skip» stays in place; the rail fits «Mode and protocols ✓»;
    №38  one label of the main button for every width (no «short» label), «Summary» only as the tab;
    №39  a code without parts keeps the place of the row «Part 1 of 2» — the frame does not change its height;
    №40  stage 5: every item is folded, opened by choice;
    №42  the accent chip on a chosen card has the surface under it (the text accent is computed against it);
    №43  panel: the code and engine of an inbound wrap, the previews of the presets fit their column, the card of the
         tour has the height of its longest step.
Pure checks of the sources; the screenshots are in k3/design/port-332/critic-fix/."""

import json
import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")


def read(rel):
    with open(os.path.join(STATIC, rel), encoding="utf-8") as f:
        return f.read()


def rules(css):
    """[(media, selector, {prop: value})] of a stylesheet without comments; media — the condition of the enclosing
    @media ("" at the top)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out, i, media = [], 0, [""]
    while i < len(css):
        j = css.find("{", i)
        k = css.find("}", i)
        if k != -1 and (j == -1 or k < j):        # the end of a @media block
            if len(media) > 1:
                media.pop()
            i = k + 1
            continue
        if j == -1:
            break
        head = css[i:j].strip()
        if head.startswith("@media") or head.startswith("@supports"):
            media.append(head)
            i = j + 1
            continue
        if head.startswith("@keyframes"):
            depth, p = 1, j + 1
            while depth:
                depth += {"{": 1, "}": -1}.get(css[p], 0)
                p += 1
            i = p
            continue
        end = css.index("}", j)
        body = css[j + 1:end]
        decl = {}
        for d in body.split(";"):
            if ":" in d:
                a, b = d.split(":", 1)
                decl[a.strip()] = b.strip()
        for sel in head.split(","):
            out.append((media[-1], sel.strip(), decl))
        i = end + 1
    return out


def find(css, selector, media=None):
    """The merged declarations of ``selector`` (in ``media`` when given: a substring of the condition, "" — top)."""
    got = {}
    for m, s, d in rules(css):
        if s == selector and (media is None or (media == "" and m == "") or (media and media in m)):
            got.update(d)
    return got


def js_dict(rel):
    """The keys and values of a dictionary of the wizard (window.X = { … };)."""
    src = read(rel)
    body = src[src.index("= {") + 2:src.rindex("}") + 1]
    body = re.sub(r"(?m)^\s*//.*$", "", body)                  # comments between the keys
    body = re.sub(r",(\s*\})", r"\1", body)                   # a trailing comma
    return json.loads(body)


class WizardCss(unittest.TestCase):
    def setUp(self):
        self.css = read("setup/qs.css")
        self.comp = read("components.css")

    def test_13_inputs_wrap_on_phone(self):
        self.assertEqual(find(self.css, ".qs-pills").get("flex-wrap"), "wrap")
        for m, s, d in rules(self.css):
            if ".qs-pills" in s:
                self.assertNotEqual(d.get("flex-wrap"), "nowrap", (m, s))
                self.assertNotIn(d.get("overflow-x"), ("auto", "scroll"), (m, s))

    def test_14_button_and_chip_under_the_text_on_narrow_phone(self):
        narrow = [(s, d) for m, s, d in rules(self.css) if "max-width: 480px" in m]
        sels = dict(narrow)
        self.assertIn(".qs-card__h:has(> .qs-card__ic) > .qs-card__end", sels)
        self.assertEqual(sels[".qs-card__h:has(> .qs-card__ic) > .qs-card__end"].get("flex-basis"), "100%")
        self.assertEqual(sels[".qs-x__h:has(> .gx-chip)"].get("flex-wrap"), "wrap")
        self.assertEqual(sels[".qs-x__h > .gx-chip"].get("order"), "1")
        # only cards with an icon: a card head without it («Exit keys» + «Add key») keeps its row
        self.assertNotIn(".qs-card__h:has(> .qs-card__end)", sels)

    def test_15_password_field_is_a_frame_with_the_button_in_flow(self):
        show = find(self.css, ".qs-pw__show")
        self.assertNotIn("position", show)
        self.assertEqual(show.get("flex"), "none")
        box = find(self.css, ".qs-pw__box")
        self.assertEqual(box.get("display"), "flex")
        self.assertIn("var(--field-shadow)", box.get("box-shadow", ""))
        inp = find(self.css, ".qs-pw__box > .gx-input")
        self.assertEqual(inp.get("flex"), "1")
        self.assertNotRegex(inp.get("padding-right", "0"), r"^(?:[5-9]\d|\d{3})px$")      # no room kept for one language
        # focus and error rings move to the frame
        self.assertIn("var(--accent-text)", find(self.css, ".qs-pw__box:has(> .gx-input:focus)").get("box-shadow", ""))
        self.assertIn("var(--danger)", find(self.css, '.qs-pw__box:has(> .gx-input[aria-invalid="true"])').get("box-shadow", ""))
        self.assertEqual(find(self.css, ".qs-pw__box > .gx-input:focus").get("box-shadow"), "none")

    def test_16_app_switch_labels_in_one_line(self):
        self.assertEqual(find(self.css, ".qs .kq-apps__btn").get("white-space"), "nowrap")
        self.assertEqual(find(self.css, ".qs .kq-apps__seg").get("grid-auto-columns"), "auto")

    def test_17_qr_260_centered_everywhere(self):
        # 3.3.2 (owner): every code of an issuance — one frame of one size, as in the panel and on /p/: 360 px
        q = find(self.css, ".qs-qrs", "")
        self.assertEqual(q.get("--kq-size"), "360px")
        self.assertIn("380px", q.get("width", ""))
        for m, s, d in rules(self.css):
            if s == ".qs-qrs" and m:
                self.fail("a smaller QR in %s" % m)
        self.assertEqual(find(self.css, ".qs-qrrow", "").get("justify-content"), "center")

    def test_39_place_of_the_parts_row_is_kept(self):
        """A code without parts keeps the place of the line «Часть 1 из 2» (no buttons since the parts change by
        themselves): the frame does not move when the app is switched."""
        of = re.search(r"\.kq__of \{([^}]*)\}", self.comp).group(1)
        # высота заглушки — та же величина и единица, что строка «Часть 1 из 2» (Т3: rem — с «A A A»; REV-it1-b С-3:
        # 20px против 1.25rem давали прыжок рамки на 2,5 px на «крупнее»)
        h = re.search(r"/ (\d+(?:\.\d+)?(?:px|rem))", of).group(1)
        self.assertEqual(find(self.comp, '.kq[data-n="1"] .kq__cap::after').get("height"), h)
        self.assertNotIn(".kq__btn", self.comp)

    def test_37_bar_rail_and_main_button_are_stable(self):
        bar = find(self.css, ".qs-bar__b")
        self.assertIn("2 * 1.25 * 15px", bar.get("min-height", ""))
        t = find(self.css, ".qs-bar__t")
        self.assertIn("15px/1.25", t.get("font", ""))            # the reserve is computed from these
        self.assertEqual(t.get("-webkit-line-clamp"), "2")
        prim = find(self.css, ".qs-foot .ga-btn--primary", "")
        self.assertGreaterEqual(int(prim.get("min-width", "0px")[:-2]), 192)
        phone = find(self.css, ".qs-foot .ga-btn--primary", "max-width: 767px")
        self.assertEqual(phone.get("min-width"), "0")
        self.assertEqual(find(self.css, ".qs-step__last").get("white-space"), "nowrap")
        self.assertEqual(find(self.css, ".qs-rail", "").get("width"), "256px")

    def test_42_accent_chip_on_chosen_card_has_surface(self):
        d = find(self.css, '.qs-choice[aria-pressed="true"] .gx-chip--accent')
        self.assertEqual(d.get("background"), "var(--surface)")
        d = find(self.css, '.qs-choice[aria-checked="true"] .gx-chip--accent')
        self.assertEqual(d.get("background"), "var(--surface)")


class WizardLabels(unittest.TestCase):
    REMOVED = ("imp.nextPreviewShort", "fw.createShort", "x.save", "d.nextSummary", "d.openPanelShort")

    def setUp(self):
        self.js = read("setup/setup.js")
        self.en = js_dict("setup/setup-i18n-en.js")
        self.ru = js_dict("setup/setup-i18n-ru.js")

    def test_38_one_label_for_every_width(self):
        foot = self.js[self.js.index("function footKids("):self.js.index("// ── Системные экраны")]
        self.assertNotIn("matchMedia", foot)
        self.assertNotIn(".short", foot)
        self.assertIn('const label = n.label || t("c.next");', foot)
        self.assertNotRegex(self.js, r"\bshort: t\(")
        for k in self.REMOVED:
            self.assertNotIn(k, self.en)
            self.assertNotIn(k, self.ru)
            self.assertNotIn('"%s"' % k, self.js)
        self.assertEqual(self.en["imp.nextPreview"], "Review changes")
        self.assertEqual(self.ru["imp.nextPreview"], "Что изменится")
        self.assertEqual(self.en["d.openPanel"], "Open panel")
        self.assertEqual(sorted(self.en), sorted(self.ru))
        # «Summary» is the tab only: the button of the tab «Connect» says «Next»
        done = self.js[self.js.index("VIEW.done = () => {"):]
        done = done[:done.index("\n  };")]
        self.assertIn("? { act: () => go(order[i + 1]) }", done)
        self.assertIn('{ label: t("d.openPanel"), act: finish', done)

    def test_40_extras_folded(self):
        self.assertIn('ENTER.extras = () => { const u = ui("extras"); if (u.open === undefined) u.open = ""; reload("extras-list"); step("rules", {}); };', self.js)   # R7: and the item «Россия напрямую»

    def test_no_cssom_in_the_wizard(self):
        code = re.sub(r"(?m)(^|\s)//\s.*$", r"\1", re.sub(r"/\*.*?\*/", "", self.js, flags=re.S))
        self.assertIsNone(re.search(r"\.style\.(?!setProperty\(\"--qs-hue\")", code))


class PanelLayout(unittest.TestCase):
    def setUp(self):
        self.css = read("styles.css")
        self.app = read("app.js")

    def test_43_inbound_code_wraps(self):
        d = find(self.css, ".gx-pcard__c")
        self.assertNotEqual(d.get("white-space"), "nowrap")
        self.assertNotIn("text-overflow", d)
        self.assertEqual(d.get("overflow-wrap"), "anywhere")

    def test_43_preset_fits_its_column(self):
        d = find(self.css, ".gx-preset")
        self.assertEqual(d.get("min-width"), "0")
        self.assertEqual(d.get("overflow"), "hidden")
        self.assertEqual(d.get("grid-template-columns"), "minmax(0, 1fr)")
        self.assertIn("minmax(0, 1fr)", find(self.css, ".gx-preset__pic").get("grid-template-columns", ""))

    def test_43_tour_card_keeps_its_height(self):
        self.assertIn("function tourFit()", self.app)
        fit = self.app[self.app.index("function tourFit()"):]
        fit = fit[:fit.index("\n  }\n") + 4]
        self.assertIn("TOUR.forEach", fit)
        self.assertIn("P1.text.style.minHeight", fit)
        self.assertIn("P1.title.style.minHeight", fit)
        op = self.app[self.app.index("function tourOpen("):self.app.index("function tourStep(")]
        self.assertLess(op.index("tourFit();"), op.index("tourStep(0);"))
        self.assertIn('window.addEventListener("resize", tourFit);', op)
        cl = self.app[self.app.index("function tourClose("):]
        self.assertIn('window.removeEventListener("resize", tourFit);', cl[:cl.index("\n  }\n")])


@unittest.skipUnless(shutil.which("node"), "нет node")
class MockOffers(unittest.TestCase):
    """The demo (dev mocks) speaks the words of the product: what to issue first, the notes and the steps of an
    issuance are issue_os.MATRIX / NOTES / howto() with the catalogs, Russian in the mocks and English in
    dev-mock-en.js — a text of the catalog changed, the demo follows (T-332 critic-fix, the screenshots)."""

    def run_js(self, src):
        r = subprocess.run(["node", "-e", src], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])

    def table(self, rel, name):
        src = read(rel)
        i = src.index("  var %s = {" % name)
        j = src.index("\n  };", i) + 5
        return self.run_js("var %s; %s; console.log(JSON.stringify(%s));" % (name, src[i:j].replace("var ", "", 1), name))

    def en_map(self):
        src = read("dev-mock-en.js")
        i = src.index("  var MAP = {")
        j = src.index("\n  };", i) + 5
        return self.run_js("var MAP; %s; console.log(JSON.stringify(MAP));" % src[i:j].replace("var ", "", 1))

    def test_offers_notes_and_howto_follow_the_catalogs(self):
        import sys
        sys.path.insert(0, os.path.join(ROOT, "lib"))
        from kaskad import i18n
        from kaskad.gw import issue_os
        mp = self.en_map()

        def both(fn):
            with i18n.use_lang("ru"):
                ru = fn()
            with i18n.use_lang("en"):
                en = fn()
            return ru, en
        panel, setup = self.table("dev-mock.js", "OFFERS"), self.table("setup/dev-mock-setup.js", "OFFERS")
        self.assertEqual(sorted(panel), sorted(issue_os.MATRIX))
        self.assertEqual(sorted(setup), sorted(issue_os.MATRIX))
        for p in issue_os.MATRIX:
            ru, en = both(lambda: issue_os.offers(p))
            self.assertEqual(panel[p], [[o["inbound"], o["note"]] for o in ru], p)
            self.assertEqual(setup[p], [[o["inbound"], o["format"], o["note"]] for o in ru], p)
            for r, e in zip(ru, en):
                self.assertEqual(mp.get(r["note"]), e["note"], r["note"])
        for rel in ("dev-mock.js", "setup/dev-mock-setup.js"):
            notes = self.table(rel, "OFFER_NOTES")
            for p in issue_os.MATRIX:
                ru, en = both(lambda: issue_os.NOTES[p] if p in issue_os.NOTES else [])
                self.assertEqual(notes.get(p, []), ru, (rel, p))
                for r, e in zip(ru, en):
                    self.assertEqual(mp.get(r), e, r)
            howto = self.table(rel, "HOWTO")
            self.assertTrue(howto, rel)
            words = {}                  # a Russian line -> its English ones: one Russian line may stand for two
            for key, steps in howto.items():        # (iOS «Create from QR code», Android «Scan from QR code»)
                kind, p = key.split(":")
                ru, en = both(lambda: issue_os.howto(p, kind, where="panel"))
                self.assertEqual(steps, ru, (rel, key))
                for r, e in zip(ru, en):
                    words.setdefault(r, set()).add(e)
            for r, es in words.items():
                self.assertIn(mp.get(r), es, r)

    def test_no_node_in_the_english_demo(self):
        for ru, en in self.en_map().items():
            if ru.startswith("Режим — node"):
                continue        # «node» there is the value of the API field mode, not a word for people
            self.assertNotRegex(en, r"\bnodes?\b", ru)
            self.assertNotRegex(en, r"[A-Za-z]'[A-Za-z]", ru)       # the typographic apostrophe
            self.assertNotRegex(en, r"\b[Gg]rey\b", ru)


@unittest.skipUnless(shutil.which("node"), "нет node")
class TourFitRuns(unittest.TestCase):
    def test_min_height_is_the_longest_step(self):
        app = read("app.js")
        fit = app[app.index("  function tourFit()"):]
        fit = fit[:fit.index("\n  }\n") + 4]
        src = """
const TOUR = [["a", "t1", "d1"], ["b", "t2", "d2"], ["c", "t3", "d3"]];
const L = { t1: "x", t2: "xxxx", t3: "xx", d1: "yy", d2: "y", d3: "yyyyyy" };
const t = (k) => L[k];
const el = () => ({ textContent: "", style: {}, get offsetHeight() { return this.textContent.length * 10; } });
let tour = { parts: { title: el(), text: el() } };
tour.parts.title.textContent = "x"; tour.parts.text.textContent = "yy";
%s
tourFit();
console.log(JSON.stringify({ th: tour.parts.title.style.minHeight, dh: tour.parts.text.style.minHeight,
  keep: [tour.parts.title.textContent, tour.parts.text.textContent] }));
""" % fit
        r = subprocess.run(["node", "-e", src], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        out = json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])
        self.assertEqual(out, {"th": "40px", "dh": "60px", "keep": ["x", "yy"]})


if __name__ == "__main__":
    unittest.main()

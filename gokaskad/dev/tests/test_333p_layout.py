"""3.3.3: the layout of the panel and of /p/ after the check k3/stand/logs/T-333-layout.md.

The geometry itself (no overlaps, nothing past the card or the window at 390…1440, both themes, the columns of a table
head and its rows, the scheme's state in the whole-scheme view, the tour) is checked in a real Chrome by
``tools/k3_layout_check.mjs`` — the quick set runs in ``tools/k3_test.sh`` by default, the full one with
``K3_LAYOUT=full``; these are the offline guards of each fix: the rules that make it hold (R-333: and the checker's
own rules, so that a check is not dropped silently)."""

import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


def read_tool(name):
    with open(os.path.join(ROOT, "tools", name), encoding="utf-8") as fh:
        return fh.read()


def rule(css, selector):
    """The declarations of all top-level rules with exactly this selector, the later ones winning (as in the cascade)."""
    out = None
    for m in re.finditer(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css):
        out = dict(out or {}, **dict((k.strip(), v.strip()) for k, v in (d.split(":", 1) for d in m.group(1).split(";") if ":" in d)))
    return out


def block(css, at):
    """The body of an at-rule block (one level of nesting), e.g. ``@container gxcol (max-width: 520px)``."""
    i = css.index(at)
    j = css.index("{", i) + 1
    depth = 1
    k = j
    while depth:
        depth += {"{": 1, "}": -1}.get(css[k], 0)
        k += 1
    return css[j:k - 1], i


class HeaderTest(unittest.TestCase):
    """№3: a long node address does not push «Refresh / theme / View» off the window — it ends with «…»."""

    def test_the_title_shrinks_and_the_host_gets_an_ellipsis(self):
        css = read("styles.css")
        self.assertEqual(rule(css, ".gx-head__title")["flex"], "0 1 auto")
        self.assertEqual(rule(css, ".gx-head__title")["min-width"], "0")
        self.assertEqual(rule(css, ".gx-crumb")["min-width"], "0")
        host = rule(css, ".gx-crumb > span:first-child")
        self.assertEqual((host["min-width"], host["overflow"], host["text-overflow"]), ("0", "hidden", "ellipsis"))

    def test_the_tooltip_holds_the_whole_address(self):
        js = read("app.js")
        self.assertIn('return [hostName(), ip].filter(Boolean).join(" · ");', js)


class SettingsColumnsTest(unittest.TestCase):
    """№1: «Settings» at 720–1150 px — one stack; a narrow column puts the label over the value (no letter-by-letter
    «Где задан», no 25 px field in «Gateway»); the preview keeps the status chip under the value."""

    def test_one_stack_by_the_width_of_the_area(self):
        css = read("components.css")
        self.assertEqual(rule(css, ".gx-main")["container"], "gxmain / inline-size")
        self.assertEqual(rule(css, ".gx-cols > .gx-col")["container"], "gxcol / inline-size")
        body, _ = block(css, "@container gxmain (max-width: 840px)")
        self.assertIn("flex-direction: column", body)

    def test_narrow_column_label_over_value_and_after_the_base_rules(self):
        css = read("components.css")
        body, at = block(css, "@container gxcol (max-width: 380px)")
        self.assertIn(".gx-kv { grid-template-columns: minmax(0, 1fr) auto; }", body)
        self.assertIn(".gx-kv__k { grid-column: 1 / -1;", body)
        self.assertIn(".gx-kv__v--mono .gx-kv__text { white-space: normal;", body)
        # same specificity as the base table — it has to come later, or the three columns win (the bug of the draft)
        self.assertGreater(at, css.index(".gx-kv { display: grid; grid-template-columns: var(--kv-label)"))

    def test_label_left_on_laptops(self):
        """R-333 №5: the narrowest of the two columns (~405 px at a ~1150 px window) keeps the label on the left —
        the label goes over the value only in a column that is really narrow."""
        css = read("components.css")
        self.assertNotIn("@container gxcol (max-width: 520px)", css)
        self.assertEqual(len(re.findall(r"@container gxcol \(max-width: (\d+)px\)", css)), 1)
        self.assertLessEqual(int(re.search(r"@container gxcol \(max-width: (\d+)px\)", css).group(1)), 400)

    def test_field_row_wraps_instead_of_crushing_the_field(self):
        css = read("components.css")
        self.assertEqual(rule(css, ".gx-field__row")["flex-wrap"], "wrap")
        self.assertEqual(rule(css, ".gx-field__row > .gx-input")["min-width"], "min(100%, 200px)")

    def test_preview_chip_under_the_value(self):
        body, _ = block(read("styles.css"), "@container (max-width: 760px)")
        self.assertIn(".kk-pv .gx-kv { grid-template-columns: minmax(0, 1fr); }", body)
        self.assertIn(".kk-pv .gx-kv__end:empty { display: none; }", body)


class ForwardsTableTest(unittest.TestCase):
    """№2: «Forwards» at 720–1100 px — cards (as on a phone) instead of six columns; wider, a cell never runs into the
    next one (ellipsis, the whole text in title) and «Delete» is never cut."""

    def test_cards_below_1100(self):
        js = read("app.js")
        self.assertIn('const MID_MQ = "(max-width: 1099.98px)";', js)
        self.assertIn('const cards = isPhone() || (o.cardsMq && window.matchMedia && window.matchMedia(o.cardsMq).matches);', js)
        # crossing 1100 rebuilds the page (the table is built once per layout)
        self.assertIn('+ (mq(MID_MQ) ? "m" : "");', js)

    def test_one_grid_for_head_and_rows(self):
        """R-333 №2: the head and the rows are subgrids of one grid; the actions column has a fixed width (in the head
        it is empty — max-content gave it 0 px there and «Delete» in the rows)."""
        js, css = read("app.js"), read("components.css")
        self.assertIn('minmax(140px,1fr) 132px", minW: "600px", cardsMq: MID_MQ, compact: [[0, 1, 2], [3, 4]]', js)
        self.assertNotIn('max-content", minW: "600px"', js)
        self.assertEqual(rule(css, ".gx-tbl__g")["grid-template-columns"], "var(--cols)")
        self.assertEqual(rule(css, ".gx-tbl__g")["min-width"], "var(--minw, 0)")
        hr = rule(css, ".gx-tbl__h, .gx-tbl__r")
        self.assertEqual((hr["grid-column"], hr["grid-template-columns"]), ("1 / -1", "subgrid"))
        self.assertIn("g.append(row);", js)

    def test_compact_cards_two_lines(self):
        """R-333 №6: 720–1099 px — a forward is a card of two lines («TCP 443 → where», «name · traffic»), the button
        on the right; the phone keeps «label … value» rows."""
        js, css = read("app.js"), read("components.css")
        self.assertIn("if (cards && o.compact && !isPhone()) {", js)
        self.assertEqual(rule(css, ".gx-ptbl__b")["grid-row"], "1 / span 2")
        self.assertEqual(rule(css, ".gx-ptbl__l")["display"], "flex")

    def test_compact_long_name_shrinks_traffic_whole(self):
        """R-333 repeat №1: in the second line the name (up to 64 chars, even without spaces) shrinks with «…», the
        traffic stays whole — nothing runs onto «Delete»; the mock has fw=long and the checker visits it («ptbl»)."""
        css = read("components.css")
        self.assertEqual(rule(css, ".gx-ptbl__l--2 > .gx-ptbl__i")["flex"], "none")
        self.assertEqual(rule(css, ".gx-ptbl__l--2 > .gx-ptbl__i:first-child")["flex"], "0 1 auto")
        self.assertLess(css.index(".gx-ptbl__i:last-child { flex: 0 1 auto; }"), css.index(".gx-ptbl__l--2 > .gx-ptbl__i { flex: none; }"))
        self.assertEqual(rule(css, ".gx-ptbl__i > .gx-tbl__t")["text-overflow"], "ellipsis")
        mock = read("dev-mock.js")
        self.assertIn('if (Q.get("fw") === "long") d.forwards.push(', mock)
        self.assertIn('name: "game-server-frankfurt-europe-west-primary-node-01-backup-replica"', mock)
        self.assertEqual(len("game-server-frankfurt-europe-west-primary-node-01-backup-replica"), 64)   # api.py: up to 64
        self.assertIn('"Игровой сервер друзей во Франкфурте, основной и запасной": "Friends’ game server', read("dev-mock-en.js"))
        tool = read_tool("k3_layout_check.mjs")
        self.assertIn('"fw=long": ["forwards"]', tool)
        self.assertIn("out.push(['ptbl',", tool)

    def test_long_forwards_traffic_is_fixed_in_the_mock(self):
        """R-333 round 3, nit 3: the traffic of the two long forwards is fixed and wide (millions of packets, GB), so the
        quick set (820/1024) catches a rollback of the shrinking name on every run, not on 5 runs of 6."""
        mock = read("dev-mock.js")
        self.assertIn('added: "2026-09-13T10:00:00Z", _c: { packets: 4821377, bytes: 5905580032 }, _fix: true },', mock)
        self.assertIn('added: "2026-09-14T10:00:00Z", _c: { packets: 9384216, bytes: 87960930222 }, _fix: true });', mock)
        self.assertIn("db.forwards.forEach(function (f) { if (f._fix) return; var c = f._c ||", mock)
        # _fix stays inside the mock: /api/forwards lists the fields one by one
        self.assertIn("forwards: db.forwards.map(function (f) { return { proto: f.proto, port: f.port,", mock)

    def test_cell_text_ellipsis_title_only_when_cut(self):
        """R-333 №10: the tooltip holds the whole text only where it is really cut by «…», not «443» or «TCP»."""
        js = read("app.js")
        self.assertNotIn('text: String(c.t), title: String(c.t) }));', js)
        self.assertIn('if (x.scrollWidth > x.clientWidth + 1) { if (x.title !== x.textContent) x.title = x.textContent; } else if (x.hasAttribute("title")) x.removeAttribute("title");', js)
        # the traffic is updated in place and squeezes the name: every cell is observed, not only the table
        self.assertIn('n.querySelectorAll(".gx-tbl__t").forEach((x) => ro.observe(x));', js)
        r = rule(read("components.css"), ".gx-tbl__c > .gx-tbl__t")
        self.assertEqual((r["max-width"], r["overflow"], r["text-overflow"]), ("100%", "hidden", "ellipsis"))


class SchemeTest(unittest.TestCase):
    """№4: edge labels on a free spot of the curve (two lines in a narrow gap), not under a block; labels inside blocks
    wrap instead of «…»; the whole-scheme view (30 %) shows each block's name large instead of unreadable rows."""

    def test_edge_label_free_spot(self):
        js = read("graph.js")
        self.assertIn("if (lab) { var m = labelSpot(lab, o.a, o.b, boxes, taken);", js)
        self.assertIn("lab.classList.toggle('is-2l', pass === 1);", js)
        self.assertIn("join('\\u00a0· ')", js)
        self.assertIn("if (moved) scheduleEdges();", js)
        self.assertIn("setTimeout(run, 150);", js)          # no frame in a hidden window — the labels still move
        self.assertIn(".kg-elab.is-2l { white-space: normal; width: min-content;", read("graph.css"))

    def test_node_texts_wrap(self):
        css = read("graph.css")
        for sel in (".kg-nd__name", ".kg-nd__sub", ".kg-nd__rl", ".kg-dv__n"):
            r = rule(css, sel)
            self.assertNotIn("text-overflow", r, sel)
            self.assertEqual(r.get("overflow-wrap"), "anywhere", sel)
        self.assertEqual(rule(css, ".kg-dv__chips")["flex-wrap"], "wrap")

    def test_far_view_big_names(self):
        js, css = read("graph.js"), read("graph.css")
        self.assertIn("h('span', { cls: 'kg-nd__farnm', text: nm.textContent }), h('span', { cls: 'kg-nd__farst', hidden: true })", js)
        self.assertIn("setProperty('--kg-far-fs', Math.min(40, 11.5 / V.k)", js)
        self.assertIn(".kg-stage.is-far .kg-nd > :not(.kg-nd__far) { visibility: hidden; }", css)

    def test_far_view_keeps_the_state(self):
        """R-333 №1: the whole-scheme view hides the rows, not the state: the worst mark of a block («down», «failure →
        direct», a dead exit of the pool) is shown large under the name, the frame is of its colour; refreshed with
        every live update (the geometry — k3_layout_check «farstate»)."""
        js, css = read("graph.js"), read("graph.css")
        self.assertIn("function farMarks() {", js)
        self.assertEqual(js.count("farMarks();"), 2)                       # after the build and after each live update
        self.assertIn("var lvl = bad.length || el.classList.contains('is-down') || (mem && !memOk) ? 'bad' : mem || warn.length ? 'warn' : '';", js)
        self.assertIn(".kg-stage.is-far .kg-nd.is-far-bad { border-color: var(--kg-bad);", css)
        self.assertIn(".kg-stage.is-far .kg-nd.is-far-warn { border-color: var(--kg-warn);", css)
        self.assertIn(".kg-nd__farst.is-bad {", css)
        # the far label is not hidden with the rows (it is .kg-nd__far's child)
        self.assertNotIn(".kg-nd__farst { display: none", css)
        self.assertIn("farstate", read_tool("k3_layout_check.mjs"))

    def test_pool_row_state_on_the_row(self):
        """R-333 repeat №2: «is-active» / «is-down» are set on the pool row itself (.kg-nd__mem), where graph.css colours
        it — a dead exit is a red row, not only grey text; the checker compares its background («pooldown»)."""
        js, css = read("graph.js"), read("graph.css")
        self.assertIn("var row = v.parentNode.parentNode;", js)
        self.assertNotIn("v.parentNode.classList", js)
        self.assertIn("row.classList.toggle('is-active', !!o.ok); row.classList.toggle('is-down', o.st === 'dead');", js)
        self.assertIn("el.querySelectorAll('.kg-nd__mem.is-down').length", js)
        self.assertEqual(rule(css, ".kg-nd__mem.is-down")["background"], "var(--kg-bad-soft)")
        self.assertEqual(rule(css, ".kg-nd__mem.is-active")["background"], "var(--kg-ok-soft)")
        self.assertIn("out.push(['pooldown',", read_tool("k3_layout_check.mjs"))

    def test_far_names_no_syllable_breaks(self):
        """R-333 №9: «Напрямую» is not torn «Напря-/мую»; a long single word shrinks a little instead of breaking."""
        css = read("graph.css")
        far = rule(css, ".kg-stage.is-far .kg-nd__far")
        self.assertEqual(far["hyphens"], "manual")
        self.assertEqual(rule(css, ".kg-nd__farnm")["font-size"], "min(1em, 17cqi)")


class TourTest(unittest.TestCase):
    """№5 (T-333 №8, №9): the tour card goes up when it would cover the block it explains — on a phone only (R-333 №3:
    on a computer it would cover the header); a toast during the tour stands above the card, not on «Back / Next»."""

    def test_card_moves_up_when_it_covers_the_block(self):
        js, css = read("app.js"), read("styles.css")
        # R-333 repeat №4: the block's title is never covered — a card that leaves it free wins over a smaller area
        # R-333 round 3 №1: [the section's toolbar above the block, the block's title, the area] — compared in this order
        self.assertIn('if (low[0] || low[1] || low[2]) { tour.box.classList.add("is-top"); const hi = over(); if ((hi[0] - low[0] || hi[1] - low[1] || hi[2] - low[2]) >= 0) tour.box.classList.remove("is-top"); }', js)
        self.assertIn("return [kr && mr && kr.bottom > mr.top && cut(q, kr) ? 1 : 0, w > 0 && q.top < headEnd && q.bottom > br.top ? 1 : 0, w > 0 && hh > 0 ? w * hh : 0];", js)
        self.assertIn("tourtitle", read_tool("k3_layout_check.mjs"))
        self.assertIn('if (!isPhone()) tour.box.classList.remove("is-top");', js)
        self.assertEqual(rule(css, ".kk-tour.is-top"), {"top": "24px", "bottom": "auto"})
        self.assertIn('[data-device="phone"] .kk-tour.is-top { top: calc(8px + env(safe-area-inset-top)); bottom: auto; }', css)

    def test_title_is_the_h2_and_the_toolbar_stays_free(self):
        """R-333 round 3 №1: on a short phone (390×664, 360×640, 375×548) the card covered «Exits», «Domain and site»
        and «Add person»: the block's title is its h2 alone (not the whole head with the text and buttons); a card on
        top that would cover the section's toolbar above the block («Add person») stays down."""
        js = read("app.js")
        self.assertIn('const h2 = $(".gx-card__tt .gx-h2", blk), hr = h2 && h2.getBoundingClientRect(), headEnd = hr && hr.height ? hr.bottom : br.top + 48;', js)
        self.assertNotIn('const tt = $(".gx-card__tt", blk)', js)
        self.assertIn('const k = sec && ($(".gx-toolbar .ga-btn--primary", sec) || $(".gx-toolbar", sec));', js)
        self.assertIn("(k.compareDocumentPosition(blk) & Node.DOCUMENT_POSITION_FOLLOWING) && !blk.contains(k) ? k : null;", js)
        # phone: the block's start under the low card — scroll just enough, never the toolbar under the header
        # R-333 round 4 №1: the card's top without transform (it still slides in on step 1) — the scroll does not depend
        # on the frame of the measurement, i.e. on the device's speed
        self.assertIn('const bb = parseFloat(getComputedStyle(tour.box).bottom), boxTop = isFinite(bb) ? layoutH() - bb - tour.box.offsetHeight : tour.box.getBoundingClientRect().top;', js)
        self.assertIn("const key = tourKeep(sec, blk), need = b0.top + 90 - boxTop;", js)
        self.assertNotIn("need = b0.top + 90 - tour.box.getBoundingClientRect().top", js)
        self.assertIn("const lim = key ? key.getBoundingClientRect().top - m0.top - 8 : b0.top - m0.top - 12;", js)
        self.assertIn("if (need > 0 && lim > 0) main.scrollTop += Math.min(need, lim);", js)
        # 375×548: a denser card («1 of 4» next to the title, no dots) — otherwise the toolbar and the block do not fit.
        # 3.3.7.14 (Claude Design r1, variant (a)): the same card on every phone lower than 680 px — 360×640 and 375×667
        # (steps «Rules» and «Settings»: 56 px of the section above the usual card, 119 with the dense one); 390×844 as before
        css = read("styles.css")
        self.assertNotIn("@media (max-height: 600px)", css)
        body, _ = block(css, "@media (max-height: 679.98px)")
        self.assertIn('[data-device="phone"] .kk-tour { grid-template-columns: minmax(0, 1fr) auto; column-gap: var(--sp-2); row-gap: var(--sp-1); padding: var(--sp-3); }', body)
        self.assertIn('[data-device="phone"] .kk-tour > .kk-tour__of { grid-column: 2; grid-row: 2; align-self: center; }', body)
        self.assertIn('[data-device="phone"] .kk-tour__dots { display: none; }', body)
        tool = read_tool("k3_layout_check.mjs")
        # R-333 round 4 №2: the dense card (375×548) in the quick set too, not only in full
        # 3.3.7.14: K3_PAGES=tour — the tour alone, 360×640 included
        self.assertIn("? [[390, 844], [390, 664], [375, 548], ...(FULL || TOUR_ONLY ? [[360, 640], [375, 667]] : [])]", tool)
        self.assertIn('const FULL = !process.argv[2] || process.argv[2] === "full";', tool)
        self.assertIn("for (const theme of TOUR_ONLY ? THEMES : [\"light\"]) for (const [w, h] of TOUR_VIEWS) {", tool)
        # 3.3.7.14: the dense card on a phone exactly below 680 px of height, the usual one from 680
        self.assertIn("const low = document.documentElement.clientHeight < 680;", tool)
        self.assertIn("if (dense !== low) out.push(['tourdense', step,", tool)

    def test_block_scrolled_into_view_once_per_step(self):
        js = read("app.js")
        # R-333 repeat №3: only when the block's start is off screen — a corner under the tour card does not scroll
        # «Add person» (above the block) under the header
        self.assertIn("if (b0.top < m0.top || b0.top > m0.bottom - 80) main.scrollTop += b0.top - m0.top - 12;", js)
        self.assertNotRegex(js, r"\bcovered\b")          # слово, а не часть «exit_recovered» (этап 1) и «uncovered» (⚠РФ, R4)
        self.assertIn("touradd", read_tool("k3_layout_check.mjs"))
        self.assertIn("tour.scrolled = tour.i;", js)

    def test_exits_step_rings_the_exits_card(self):
        """R-333 №4: step 2 says «exit keys» — the ring is on the card «Exits», not on «Default route» (the first card)."""
        js = read("app.js")
        self.assertIn('["exits", "tour.s2", "tour.s2D", "ex.listTitle"]', js)
        self.assertIn("""const own = $('[data-tour="' + id + '"]', sec);""", js)
        self.assertIn("tourcard", read_tool("k3_layout_check.mjs"))

    def test_settings_step_rings_domain_and_site(self):
        """R-333 repeat №5: step 4 speaks of the domain and the masking site — the ring is on «Domain and site»
        ([data-tour]), the text says the password and the appearance are in the other sections (UI-5: on a computer the step
        opens the part «Домен и сайт»); an empty marker falls back."""
        js = read("app.js")
        self.assertIn('site.dataset.tour = "settings";', js)
        self.assertIn("return (own && own.offsetHeight ? own : null) || byTitle ||", js)
        self.assertIn('"tour.s4D": "Здесь домен и сайт-маскировка, а в соседних частях — пароль, вид панели и бот.', read("i18n-ru.js"))
        self.assertIn('"tour.s4D": "Domain and masking site are here; password, panel appearance and the bot are in the other sections.', read("i18n-en.js"))
        self.assertIn('history.replaceState(null, "", "#settings?tab=site"); setPage(id, false);', js)
        self.assertIn("""sec.querySelector('[data-tour="' + sec.dataset.page + '"]')""", read_tool("k3_layout_check.mjs"))

    def test_toast_above_the_tour(self):
        js = read("app.js")
        self.assertIn('const tb = document.querySelector(".kk-tour");', js)
        self.assertIn('node.style.bottom = Math.round(window.innerHeight - q.top + 12) + "px";', js)


@unittest.skipUnless(shutil.which("node"), "no node")
class ToastTimeTest(unittest.TestCase):
    """R-333 П5″: a long toast (ak.cutAll) lives as long as its text needs (≈60 ms a character, 3.2 / 7 s at least,
    20 s at most) and stays while the cursor or the focus is on it; a toast replaced by a new one leaves its timer."""

    def run_toast(self, script):
        js = read("app.js")
        i = js.index("  function toast(title, desc, kind")      # 3.3.7.15: и (…, o) — кнопка действия
        j = js.index("\n  }\n", i) + 4
        src = r"""
let T = [], now = 0;
global.setTimeout = (fn, ms) => { T.push({ fn, at: now + ms, id: T.length + 1 }); return T.length; };
global.clearTimeout = (id) => { const x = T.find((t) => t.id === id); if (x) x.dead = true; };
const run = (ms) => { now += ms; T.filter((t) => !t.dead && !t.done && t.at <= now).forEach((t) => { t.done = true; t.fn(); }); };
const host = { kids: [], replaceChildren(...n) { this.kids.forEach((k) => { k.parentNode = null; }); this.kids = n; n.forEach((k) => { k.parentNode = host; }); } };
function mk(tag, attrs, ...kids) { const n = { tag, attrs, kids, on: {}, style: {}, parentNode: null,
  addEventListener(e, f) { (this.on[e] = this.on[e] || []).push(f); }, fire(e, x) { (this.on[e] || []).forEach((f) => f(x || {})); },
  contains(o) { return o === this; }, remove() { host.replaceChildren(...host.kids.filter((k) => k !== this)); },
  getBoundingClientRect() { return {}; } };
  Object.defineProperty(n, "isConnected", { get() { return n.parentNode === host; } }); return n; }
const el = mk, icon = () => null, t = (k) => k, $ = () => host;
global.document = { querySelector: () => null, activeElement: null };
global.window = { innerHeight: 800 };
let toastTimer = null;
""" + js[i:j] + script
        out = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.split()

    def test_long_text_lives_longer(self):
        out = self.run_toast(r"""
toast("Ключ удалён", "x".repeat(170), "warning");
run(7100); console.log(host.kids.length);           // a 7 s toast would be gone; 170+ chars — ~12 s
run(6000); console.log(host.kids.length);           // 2 s + 181 × 60 ms ≈ 12.9 s
toast("ok", "", "success"); run(3300); console.log(host.kids.length);    // a short one: as before
toast("x", "y".repeat(2000), "warning"); run(20100); console.log(host.kids.length);   // never past 20 s
""")
        self.assertEqual(out, ["1", "0", "0", "0"])

    def test_hover_holds_and_an_old_toast_leaves_the_new_timer(self):
        out = self.run_toast(r"""
toast("a", "b", "warning"); const n1 = host.kids[0];
n1.fire("mouseenter"); run(30000); console.log(host.kids.length);
n1.fire("mouseleave"); run(3100); console.log(host.kids.length);
toast("a", "b", "warning"); const n2 = host.kids[0];
toast("c", "d", "warning");                                   // n2 replaced
n2.fire("mouseenter"); run(7100); console.log(host.kids.length);   // the new toast still goes in time
""")
        self.assertEqual(out, ["1", "0", "0"])


class SmallThingsTest(unittest.TestCase):
    """№6: contrast ≥ 4.5 (the protocol chip that is off, the danger button, /p/ app buttons and step numbers), the
    hint of a chip row past the edge (390), the touch area of «!» off «More», /p/ «Open in App Store» off the card."""

    def test_off_chip_without_opacity(self):
        r = rule(read("components.css"), ".gx-chip--app.is-off")
        self.assertNotIn("opacity", r)
        self.assertEqual(r["color"], "var(--text-3)")

    def test_danger_button(self):
        self.assertEqual(read("tokens.css").count("--danger-strong: oklch(.55 .2 25);"), 2)      # dark and light
        self.assertNotIn("--danger-strong: oklch(.6 ", read("tokens.css"))
        self.assertIn("var(--danger-strong, var(--danger)) 90%, black)", read("components.css"))

    def test_p_accent_and_text3(self):
        css = read("user.css")
        self.assertIn("--accent: #006e91;", css)
        self.assertIn("--accent-text: #006e91;", css)
        self.assertIn("--text-3: #6a6a71;", css)
        self.assertNotIn("opacity", rule(read("give.css"), ".gs-open__tx span"))

    def test_p_open_button_gap(self):
        self.assertEqual(rule(read("user.css"), ".gs-sep.up-sep")["margin"], "14px 0 8px")

    def test_give_chip_row_fade_and_tri_gap(self):
        css = read("give.css")
        # R-333 №8: the right edge fades only while there is more to the right (is-more from give.js)
        self.assertIn(".gs-sel__row .gs-chips { -webkit-mask-image: linear-gradient(to right, transparent, #000 14px); ", css)
        self.assertIn(".gs-sel__row .gs-chips.is-more { -webkit-mask-image: linear-gradient(to right, transparent, #000 14px, #000 calc(100% - 28px), transparent);", css)
        js = read("give.js")
        self.assertIn("function edge(c) { c.classList.toggle('is-more', c.scrollWidth - c.clientWidth - c.scrollLeft > 2); }", js)
        self.assertIn("root.addEventListener('scroll', onEdge, true);", js)
        self.assertIn(".gs-step__t:has(.gs-tri) + .gs-step__sub { padding-top: 6px; }", css)

    def test_check_table_heads_fit(self):
        # REV-3377-final П-1: «Замеров» (12 px, 500) is 51 px wide — in 48 px it was «Замер…» on 1440 and 2000. The
        # last column is 60 px on every width; the layout check catches a cut column head even with a title (colhead)
        css = read("check.css")
        self.assertTrue(rule(css, ".gx-ct")["--cols"].endswith("var(--cl-w) 60px"))
        self.assertIn("--cols: 100px 108px 108px 76px minmax(0, 1fr) var(--cl-w) 60px;", block(css, "@container gxchk (max-width: 860px)")[0])
        tool = read_tool("k3_layout_check.mjs")
        self.assertIn("out.push(['colhead',", tool)
        pages = tool[tool.index("const PAGES"):tool.index("\n", tool.index("const PAGES"))]
        self.assertIn(",overview?tab=now,", pages)


@unittest.skipUnless(os.environ.get("K3_LAYOUT") not in (None, "", "0") and shutil.which("node"), "K3_LAYOUT=1 — проверка вёрстки в Chrome (~7,5 мин); "
                     "быстрый набор идёт в tools/k3_test.sh отдельным шагом")
class LiveLayoutTest(unittest.TestCase):
    """Every section on the mock, 390…1440 px, both themes, RU and EN, in a real Chrome: no page-wide horizontal
    scroll, no text over text, no text cut without «…», no «…» without title, no control over a control or cut by the
    edge, no letter-by-letter column, no field under 60 px (tools/k3_layout_check.mjs; on 3.3.2 it fails on №1–3)."""

    def test_layout(self):
        p = subprocess.run(["node", os.path.join(ROOT, "tools", "k3_layout_check.mjs"), "quick" if os.environ.get("K3_LAYOUT") == "quick" else "full"],
                           capture_output=True, text=True, timeout=1800)
        if p.returncode == 77:
            self.skipTest(p.stdout.strip())
        self.assertEqual(p.returncode, 0, p.stdout[-4000:] + p.stderr[-2000:])


@unittest.skipUnless(shutil.which("node"), "нет node: правило textcut детектора не проверить")
class TextcutRuleTest(unittest.TestCase):
    """Стенд it1 Н-4: заголовок «Сравнивать пока не с чем: история 1 из 3 дней» на 390 вылез вправо до 442 px и был
    срезан краем основной колонки (overflow-x hidden), а детектор молчал: ``hardClip`` снимал проверку целиком, если
    какой-то предок прокручивается по вертикали (основная колонка панели прокручивается всегда). Теперь оси раздельно:
    прокрутка вниз не оправдывает срез справа. Правило берётся из самого детектора и считается на подставном DOM."""

    def run_rule(self):
        src = read_tool("k3_layout_check.mjs")
        line = next(x for x in src.splitlines() if x.strip().startswith("const hardClip = "))
        js = r"""
const el = (rect, cs, sc, parent) => ({ parentElement: parent || null, rect, cs: Object.assign({ textOverflow: "clip", overflowX: "visible", overflowY: "visible" }, cs),
  scrollWidth: sc ? sc[0] : rect.right - rect.left, clientWidth: rect.right - rect.left, scrollHeight: sc ? sc[1] : rect.bottom - rect.top,
  clientHeight: rect.bottom - rect.top, getBoundingClientRect() { return this.rect; } });
const document = { body: {} };
const getComputedStyle = (a) => a.cs;
""" + line + r"""
const R = (l, t, r, b) => ({ left: l, top: t, right: r, bottom: b });
const out = {};
// основная колонка: overflow-x hidden, по вертикали прокручивается (содержимое выше окна)
const main = el(R(0, 0, 390, 844), { overflowX: "hidden", overflowY: "auto" }, [390, 3000]);
const span = el(R(45, 500, 444, 526), {}, null, el(R(16, 400, 374, 600), {}, null, main));
const k = hardClip(span); out.main = k && [k.left, k.right];
// ряд, который прокручивается вбок: срез справа — прокрутка, а не обрезка
const row = el(R(0, 0, 390, 100), { overflowX: "auto", overflowY: "hidden" }, [900, 100]);
const k2 = hardClip(el(R(300, 10, 700, 30), {}, null, row)); out.row = k2 && [k2.left, k2.right, k2.top, k2.bottom];
// многоточие — своё правило
const k3 = hardClip(el(R(45, 500, 444, 526), { textOverflow: "ellipsis", overflowX: "hidden" }, null, main)); out.ell = k3;
// карточка overflow hidden без прокрутки — обе оси
const card = el(R(10, 10, 200, 100), { overflowX: "hidden", overflowY: "hidden" });
const k4 = hardClip(el(R(20, 20, 300, 40), {}, null, card)); out.card = k4 && [k4.left, k4.right, k4.top, k4.bottom];
console.log(JSON.stringify(out));
"""
        p = subprocess.run(["node", "-e", js], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        return __import__("json").loads(p.stdout.strip().splitlines()[-1])

    def test_vertical_scroll_does_not_excuse_a_cut_on_the_right(self):
        out = self.run_rule()
        self.assertEqual(out["main"], [0, 390])          # было None: срез справа не проверялся вовсе
        self.assertEqual(out["row"][:2], [-1e9, 1e9])     # прокрутка вбок — не срез по x
        self.assertEqual(out["row"][2:], [0, 100])        # а по y ряд режет
        self.assertIsNone(out["ell"])
        self.assertEqual(out["card"], [10, 200, 10, 100])


if __name__ == "__main__":
    unittest.main()

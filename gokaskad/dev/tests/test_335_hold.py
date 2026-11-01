"""Kaskad 3.3.5, the stand T-335-release П2: an action opened in place survives the live redraw of the panel.

«Выходы» were drawn again about every 2 s by the live data, and the question «Удалить «…»? Отмена · Удалить» (U.askDelete)
vanished after 0.4–2 s — a copy of a key could hardly be deleted from the panel. Now a block (blk) with an open question
in place (.is-confirm of U.askDelete, .ux-confirm of ProtocolCard), an open menu «⋯» (U.menu, the menus of ux.js) or the
cursor in a field waits: it is drawn again when the action is closed (the answer of a deletion, «Отмена», Esc).

REV-335-5: a field in focus holds its card only while its input has not been sent — a successful action (act) lets the
card go at once (№1: Enter in «Имя домена» → the card shows the saved domain without a click elsewhere); an open question
or menu in a row with a key (data-hold-row: a tile of an exit, a person, a protocol) holds only that row, the other rows
keep living (№2); a question closes itself on a click or focus elsewhere, after 30 s without the human and when a second
one opens.

In a real Chrome (tools/k3_hold_check.mjs, the panel's ?mock=1 with live data changing every second, as on the stand):
the exits, the people, the protocols, the settings. No Chrome or node — skipped and said so; the rule itself is checked
without them."""

import functools
import http.server
import json
import os
import re
import subprocess
import threading
import unittest

from tests.test_332f_give import HAS_NODE, read
from tests.test_333q_layout import ChromeServer, _Static, chrome_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CHECK = os.path.join(ROOT, "tools", "k3_hold_check.mjs")


class RuleTest(unittest.TestCase):
    """The marks the hold looks for are the ones the actions put — a renamed class would lose the hold silently."""

    def test_the_marks(self):
        js, ux = read("app.js"), read("ux.js")
        sel = re.search(r"const HOLD_SEL = '([^']+)';", js).group(1)
        for mark in (".is-confirm", ".ux-confirm", '[aria-haspopup="menu"][aria-expanded="true"]'):
            self.assertIn(mark, sel)
        self.assertIn('row.classList.add("is-confirm");', js)                    # U.askDelete
        self.assertIn("h('div', { class: 'ux-confirm', role: 'alertdialog'", ux)   # ProtocolCard
        self.assertIn('b.setAttribute("aria-haspopup", "menu");', js)            # U.menu
        self.assertIn("'aria-haspopup': 'menu', 'aria-expanded': 'false'", ux)     # menus of ux.js

    def test_live_waits_a_request_of_code_does_not(self):
        js = read("app.js")
        self.assertIn("const held = !force && b.node.isConnected ? blkHold(b) : null;", js)
        self.assertIn("if (b) { b.sig = null; blkUpdate(b, true); }", js)          # blkForce
        self.assertIn("Array.from(BLOCKS).forEach((b) => blkUpdate(b));", js)     # not forEach(blkUpdate): the index ≠ force
        # the list of subscriptions in ExitsInput is drawn by a block without children of its own
        self.assertRegex(js, r"exitsUx\.set\(subRows\(v\.subs\), v\.n\); return null; \}, \{ hold: exitsUx\.el \}\);")

    def test_a_sent_field_lets_go(self):
        """REV-335-5 №1: act() marks the field it was started from as sent — after the reload, not before it."""
        js = read("app.js")
        self.assertIn("ae.matches(HOLD_TYPE) && !SENT.has(ae)) return true;", js)
        self.assertIn("if (o.reload) loadAll(o.reload).then(() => holdSent(from, typed, home));", js)
        self.assertIn("TYPED.set(ev.target, ++typedN); SENT.delete(ev.target);", js)

    def test_rows_with_a_key(self):
        """REV-335-5 №2: the lists whose rows have a question or a menu give every row its key."""
        js = read("app.js")
        self.assertIn('key: "exit:" + r.id,', js)
        self.assertIn('"data-hold-row": c.key || null', js)
        self.assertIn('"data-hold-row": "user:" + u.id', js)
        self.assertIn('"data-hold-row": "ib:" + r.kind', js)
        self.assertIn('card.setAttribute("data-hold-row", "ib:" + r.kind);', js)

    def test_a_question_does_not_hang(self):
        """REV-335-5 №2: the questions of the panel and of ux.js follow one rule (kaskad:confirm-open, 30 s)."""
        js, ux = read("app.js"), read("ux.js")
        self.assertIn("const CONFIRM_IDLE_MS = 30000;", js)
        self.assertIn("var CONFIRM_IDLE_MS = 30000, TOUCH_DOWN_MS = 700;", ux)
        for src in (js, ux):
            self.assertIn("kaskad:confirm-open", src)
        self.assertIn("life = confirmLife(note ? [row, note] : [row], (away) => back(away));", js)
        self.assertEqual(ux.count("lifeOff = confirmLife(box, back);"), 2)                # delete and restart of ProtocolCard


@unittest.skipUnless(HAS_NODE and chrome_path(), "нет node или Chrome: удержание действия на месте не проверено (K3_CHROME=…)")
class ChromeTest(unittest.TestCase):
    rows = None

    @classmethod
    def setUpClass(cls):
        srv = ChromeServer(("127.0.0.1", 0), functools.partial(_Static, directory=STATIC))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        # REV-336-int П-3: две части мест одновременно, каждая в своём Chrome (~175 с вместо ~350 подряд; почти всё в
        # них — ожидание живых кадров мока). Проверки те же: ниже — все места обеих частей
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        procs = []
        try:
            for part in ("1", "2"):
                procs.append((part, subprocess.Popen(["node", CHECK, base, chrome_path(), part],
                                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)))
            outs = []
            for part, p in procs:
                try:
                    out, err = p.communicate(timeout=420)
                except subprocess.TimeoutExpired:
                    p.kill()
                    out, err = p.communicate()
                outs.append((part, p.returncode, out, err))
        finally:
            for _, p in procs:
                if p.poll() is None:
                    p.kill()
                    p.wait()
            srv.shutdown()
            srv.server_close()
        cls.rows = {}
        for part, code, out, err in outs:
            if code != 0:
                raise AssertionError("k3_hold_check, часть %s (код %s): %s" % (part, code, err.decode("utf-8", "replace")[-2500:]))
            rows = json.loads(out.decode("utf-8").strip().splitlines()[-1])
            if not rows or set(r["where"] for r in rows) & set(cls.rows):
                raise AssertionError("k3_hold_check, часть %s: пусто или места повторяются: %s" % (part, [r["where"] for r in rows]))
            cls.rows.update({r["where"]: r for r in rows})

    def test_every_place_was_seen(self):
        self.assertEqual(set(self.rows), {"exits-confirm", "people-menu", "inbounds-confirm", "inbounds-ask", "inbounds-more", "exits-delete",
                                          "site-enter", "gw-typed", "exits-two", "toast-over", "exits-away", "exits-idle",
                                          "exits-warn-below", "gw-other-card", "inbounds-two", "inbounds-idle",
                                          "exits-touch-below", "exits-long-press", "exits-right", "exits-focus",
                                          "exits-sub-menu", "inbounds-press", "inbounds-restart",
                                          "exits-touch-hold", "exits-right-live", "flash", "flash-view", "settings-back", "share-copy", "sheet-swipe", "phone-ux", "phone-forms",
                                          "sheet-low", "live-label"})

    def test_details_survive_the_redraw(self):
        """REV-ui1 З-3, UI-3 ч.2: раскрытая карточка не в HOLD_SEL — карточку собирают заново, раскрытая остаётся раскрытой по протоколу."""
        r = self.rows["inbounds-more"]
        self.assertTrue(r["opened"], r)
        self.assertTrue(r["rebuilt"], "the card was not rebuilt — the check proves nothing: %s" % r)
        self.assertTrue(r["kept"], "the redraw closed «Подробнее»: %s" % r)
        self.assertEqual(r["others"], 1, r)
        self.assertTrue(r["again"] and r["closed"], "a closed one came back open: %s" % r)

    def test_the_open_action_stays(self):
        # Т3 (Б r3): у строки протокола «⋯» нет — держит ряд и вопрос «Перезапустить?» в строке (inbounds-ask)
        for where in ("exits-confirm", "people-menu", "inbounds-confirm", "inbounds-ask"):
            r = self.rows[where]
            self.assertGreater(r["before"], 0, "%s: the list was not redrawn — the check proves nothing" % where)
            self.assertTrue(r["opened"], where)
            self.assertTrue(r["kept"], "%s: the redraw closed the action: %s" % (where, r))
            self.assertTrue(r["closed"], where)
            self.assertGreater(r["after"], 0, "%s: the list is not redrawn after the action is closed" % where)

    def test_delete_answers_and_the_tile_goes(self):
        r = self.rows["exits-delete"]
        self.assertTrue(r["kept"], r)
        self.assertTrue(r["gone"], r)

    def test_enter_shows_the_saved_domain(self):
        """REV-335-5 №1: Enter in «Имя домена» — the card «Домен и сайт» is drawn again without a click elsewhere."""
        r = self.rows["site-enter"]
        self.assertTrue(r["focused"], r)
        self.assertFalse(r["before"], "the mock already has a domain — the check proves nothing: %s" % r)
        self.assertTrue(r["updated"], "the card still shows the state before saving: %s" % r)
        self.assertTrue(r["rebuilt"], r)

    def test_unsent_input_stays_sent_input_lets_go(self):
        r = self.rows["gw-typed"]
        self.assertTrue(r["focused"], r)
        self.assertTrue(r["changed"], "the data of the gateway did not change — the check proves nothing: %s" % r)
        self.assertTrue(r["kept"], "the redraw took the typed address away: %s" % r)
        self.assertTrue(r["sent"], "after Enter the card waits for a click elsewhere: %s" % r)

    def test_only_the_row_waits(self):
        """REV-335-5 №2: two questions — the second closes the first; the other tiles live; the second tile is deleted."""
        r = self.rows["exits-two"]
        self.assertGreaterEqual(r["n0"], 3, r)
        self.assertTrue(r["firstOpen"] and r["secondOpen"], r)
        self.assertTrue(r["firstClosed"], "the first question stayed open beside the second: %s" % r)
        self.assertTrue(r["othersLive"], "the other tiles are frozen by the question: %s" % r)
        self.assertTrue(r["secondKept"], r)
        self.assertTrue(r["gone"], r)

    def test_a_toast_over_the_bin_goes_first(self):
        """R-3377-hold: the toast of a live alert lay on the bin of the last row of «Все выходы» and took the press — the
        mouse of the check lets such a toast go (as if its time ran out) before it presses, and the click reaches the bin."""
        r = self.rows["toast-over"]
        self.assertTrue(r["covered"], "the toast did not cover the bin — the check proves nothing: %s" % r)
        self.assertTrue(r["cleared"], "the toast over the bin was left in place: %s" % r)
        self.assertTrue(r["opened"], "the press went to the toast, not to the bin: %s" % r)

    def test_a_question_closes_by_itself(self):
        r = self.rows["exits-away"]
        self.assertTrue(r["opened1"] and r["opened2"], r)
        self.assertTrue(r["clickClosed"], "a click elsewhere left the question open: %s" % r)
        self.assertTrue(r["focusClosed"], "the focus elsewhere left the question open: %s" % r)
        r = self.rows["exits-idle"]
        self.assertTrue(r["opened"] and r["kept"], r)
        self.assertTrue(r["closed"], "the question hangs without the human longer than 30 s: %s" % r)
        self.assertTrue(r["focusBack"], "closed by itself, the focus fell to body, not to the bin: %s" % r)

    def test_a_click_below_a_warned_question(self):
        """The stand П8: a question with a warning line above, a real mouse on the bin of a tile below — the second opens."""
        r = self.rows["exits-warn-below"]
        self.assertTrue(r["warned"], "no tile with a warning line above another one — the check proves nothing: %s" % r)
        self.assertTrue(r["firstClosed"], r)
        self.assertTrue(r["secondOpen"], "the click on the lower bin was lost: %s" % r)

    def test_focus_comes_back_after_the_redraw(self):
        """REV-335-6 Н3: the redraws of the tiles keep the focus on «Отмена» of the open question (rowFocus)."""
        r = self.rows["exits-two"]
        self.assertTrue(r["focusKept"], "the redraw dropped the focus of the open question: %s" % r)

    def test_a_sent_action_keeps_another_field(self):
        """REV-335-6 Н1: «Сохранить» of the domain, the focus put into the unsaved field of the gateway meanwhile — it holds."""
        r = self.rows["gw-other-card"]
        self.assertTrue(r["typed"] and r["saved"] and r["changed"], "the check proves nothing: %s" % r)
        self.assertTrue(r["kept"], "the typed address of another card was taken by the redraw: %s" % r)

    def test_protocol_questions_follow_the_rule(self):
        """REV-335-6 Н3, Н2: ProtocolCard — the second question closes the first, a click elsewhere closes it, after
        30 s it closes by itself with the focus back on «Удалить протокол» of its row (Т3; «⋯» before)."""
        r = self.rows["inbounds-two"]
        self.assertTrue(r["firstOpen"] and r["secondOpen"], r)
        self.assertTrue(r["one"], "two questions of protocols are open at once: %s" % r)
        self.assertTrue(r["clickClosed"], "a click elsewhere left the question of a protocol open: %s" % r)
        r = self.rows["inbounds-idle"]
        self.assertTrue(r["opened"] and r["closed"], r)
        self.assertTrue(r["focusBack"], "closed by itself, the focus fell to body, not to «Удалить протокол»: %s" % r)


    # ── 3.3.6: REV-335-7, REV-335-6 Н4/Н5, BACKLOG-3.3.6 п. 8 ──
    def test_a_touch_below_a_warned_question(self):
        """REV-335-7 Т1: телефон (390, касание) — pointerup раньше ухода фокуса; второе подтверждение открыто."""
        r = self.rows["exits-touch-below"]
        self.assertTrue(r["warned"], "no tile with a warning line above another one — the check proves nothing: %s" % r)
        self.assertTrue(r["firstClosed"], r)
        self.assertTrue(r["secondOpen"], "the touch on the lower bin was lost: %s" % r)

    def test_a_long_press_over_a_redraw(self):
        """REV-335-7 П2: нажатие на корзину плитки ниже держат дольше живой перерисовки — список не перерисован под
        нажатием, страница не прыгнула, отпустили — первое закрыто, второе открыто; потом список снова живёт."""
        r = self.rows["exits-long-press"]
        self.assertTrue(r["warned"], r)
        self.assertGreater(r["live"], 0, "the list was not redrawn — the check proves nothing: %s" % r)
        self.assertTrue(r["kept"], "the redraw took the pressed tile: %s" % r)
        self.assertTrue(r["still"], "the page jumped under the pressed button: %s" % r)
        self.assertTrue(r["firstClosed"] and r["secondOpen"], "the click after a long press was lost: %s" % r)
        self.assertGreater(r["after"], 0, "the list is not redrawn after the press: %s" % r)

    def test_the_right_button_does_not_press(self):
        """REV-335-7 П3: правый щелчок по чужой корзине — подтверждение закрыто сразу (фокус ушёл), не висит."""
        r = self.rows["exits-right"]
        self.assertTrue(r["opened"], r)
        self.assertTrue(r["closedOnPress"] and r["none"], r)

    def test_the_focus_after_esc_and_after_a_deleted_exit(self):
        """П6 (стенд 3.3.5): Esc — фокус на корзине своей плитки; REV-335-6 Н5: выход удалили в другом месте, пока открыто
        его подтверждение, — фокус на заголовке раздела, а не в body."""
        r = self.rows["exits-focus"]
        self.assertTrue(r["escBack"], r)
        self.assertTrue(r["del"] and r["gone"], "the exit was not deleted — the check proves nothing: %s" % r)
        self.assertTrue(r["goneFocus"], r)

    def test_enter_keeps_the_focus(self):
        """REV-335-6 Н4: после Enter карточка собрана заново — фокус в новом поле той же карточки (не body)."""
        self.assertTrue(self.rows["gw-typed"]["focusKept"], self.rows["gw-typed"])
        self.assertTrue(self.rows["site-enter"]["focusNotBody"], self.rows["site-enter"])

    def test_a_subscription_menu_holds_only_its_row(self):
        """REV-335-5 №5: открытое «⋯» подписки в ExitsInput держит свой ряд, остальные ряды собраны по новым данным."""
        r = self.rows["exits-sub-menu"]
        self.assertTrue(r["opened"] and r["menu"], r)
        self.assertTrue(r["kept"], r)
        self.assertTrue(r["othersLive"], "the open menu froze the whole list of subscriptions: %s" % r)

    def test_a_press_on_another_protocol(self):
        """REV-335-7 П4: ProtocolCard — нажатие на «Удалить протокол» другой строки подтверждение не закрывает, щелчок —
        закрывает."""
        r = self.rows["inbounds-press"]
        self.assertTrue(r["opened"], r)
        self.assertTrue(r["pressKept"], "ux.js closed the question on the press, not on the click: %s" % r)
        self.assertTrue(r["clickClosed"], r)

    def test_the_restart_question_gives_the_focus_back(self):
        """REV-335-7 П5: подтверждение перезапуска общей службы пережило перерисовки; «Отмена» и Esc — фокус на
        «Перезапустить» живой карточки."""
        r = self.rows["inbounds-restart"]
        self.assertEqual(r["kind"], "vless", r)
        self.assertTrue(r["focusNo"], r)
        self.assertTrue(r["cancelBack"], r)
        self.assertTrue(r["reopened"] and r["escClosed"], r)
        self.assertTrue(r["escBack"], r)


    # ── REV-336 З-3: порча, которую раньше не ловил ни один тест ──
    def test_a_touch_holds_the_tile_until_the_click(self):
        """pressTouch: касание — плитка ждёт click или TOUCH_DOWN_MS; кадр данных между pointerup и click её не уносит."""
        r = self.rows["exits-touch-hold"]
        self.assertTrue(r["kept"], "the tile under the finger was redrawn before the click: %s" % r)
        self.assertTrue(r["rebuilt"], "the frame did not change the tile — the check proves nothing: %s" % r)

    def test_the_right_button_does_not_hold(self):
        """pressRight: зажатая правая кнопка не держит плитку."""
        self.assertTrue(self.rows["exits-right-live"]["live"], self.rows["exits-right-live"])

    def test_the_new_field_after_enter_is_sent(self):
        """sentMove: после Enter новое поле помечено отправленным — следующие данные шлюза в нём видны."""
        self.assertTrue(self.rows["gw-typed"]["nextShown"], self.rows["gw-typed"])

    # ── REV-336 С-1 ──
    def test_the_flash_is_seen(self):
        """Подсветка is-flash видна (тень не none) в тёмной и светлой теме и у карточек border/flat."""
        cases = self.rows["flash"]["cases"]
        self.assertEqual(len(cases), 12, cases)
        themes = set()
        for c in cases:
            self.assertTrue(c["seen"], "no flash at all: %s" % c)
            self.assertNotEqual(c["sh"], "none", "the flash is invisible: %s" % c)
            m = re.search(r"0px 0px 0px ([\d.]+)px", c["sh"])
            self.assertTrue(m and float(m.group(1)) >= 2, "no ring of the flash: %s" % c)
            themes.add(c["theme"])
        self.assertEqual(themes, {"dark", "light"})

    # ── REV-336-2 З-1, П-4 ──
    def test_the_card_is_on_screen_after_the_jump(self):
        """Переход из поиска, мастера (?open=bot|mcp) и к человеку: на 390 и 1440 цель видна, когда раздел дособран."""
        cases = self.rows["flash-view"]["cases"]
        self.assertEqual(len(cases), 8, cases)
        for c in cases:
            self.assertTrue(c.get("found"), "no target: %s" % c)
            self.assertTrue(c["seen"], "the target is off the screen: %s" % c)
            if c["w"] == 1440 and c["hash"].startswith("settings"):
                self.assertLessEqual(c["top"], 140, "not at the top: %s" % c)

    # ── REV-336-mobile С-1 ──
    def test_settings_back_after_an_old_address(self):
        """Телефон: после ?open=bot|mcp|host и ?focus=settings.* адрес — ?tab=<часть>, «‹ Настройки» ведёт к списку."""
        cases = self.rows["settings-back"]["cases"]
        self.assertEqual(len(cases), 5, cases)
        for c in cases:
            self.assertEqual(c["part"], c["want"], "the address is not the part on the screen: %s" % c)
            self.assertEqual(c["after"]["hash"], "#settings", "«‹ Настройки» did not lead to the list: %s" % c)
            self.assertGreaterEqual(c["after"]["rows"], 8, "no list of parts: %s" % c)
            self.assertNotEqual(c["after"]["title"], c["title"], "still in the part: %s" % c)

    # ── REV-336-mobile С-2 ──
    def test_share_copies_in_the_tap(self):
        """«Поделиться»: адрес приходит при открытии листа, копирование — синхронно в нажатии (как требует Safari);
        буфер отказал — адрес остаётся полем, выделен; без полного адреса — «Подробнее»."""
        cases = {c["mode"]: c for c in self.rows["share-copy"]["cases"]}
        self.assertEqual(set(cases), {"ok", "deny", "closed"}, cases)
        for c in cases.values():
            self.assertTrue(all(x.endswith(":dis") for x in c["early"]), "copy before the address came: %s" % c)
        ok, deny, closed = cases["ok"], cases["deny"], cases["closed"]
        self.assertEqual(len(ok["copied"]), 1, "not copied in the tap: %s" % ok)
        self.assertTrue(ok["copied"][0].startswith("https://vpn.example.com/p/page-"), ok)
        self.assertFalse(ok["sheet"], ok)
        self.assertIsNone(ok["field"], ok)
        self.assertEqual(deny["copied"], [], deny)
        self.assertTrue(deny["sheet"], "the sheet closed and the link is lost: %s" % deny)
        f = deny["field"]
        self.assertTrue(f and f["v"].startswith("https://vpn.example.com/p/page-"), deny)
        self.assertTrue(f["focus"] and f["sel"] == len(f["v"]) and f["inside"], deny)
        self.assertGreaterEqual(f["h"], 20, deny)
        self.assertNotIn("Скопировать", closed["ready"], closed)
        self.assertEqual(closed["copied"], [], closed)
        # REV-336-mobile-2 П-12: без полной ссылки вторая строка говорит почему — до нажатия «Подробнее»
        self.assertIn("ссылки нет: панель закрыта из интернета или у узла нет адреса", closed["subs"], closed)
        self.assertNotIn("ссылки нет: панель закрыта из интернета или у узла нет адреса", ok["subs"], ok)

    # ── REV-336-mobile З-1, З-3 ──
    def test_rows_keep_the_mockup_height_and_the_sheet_swipes(self):
        r = self.rows["sheet-swipe"]
        self.assertTrue(r["more"] and all(h == 52 for _, h in r["more"]), "rows of «Ещё» are not 52: %s" % r)
        self.assertTrue(r["sheetRows"] and all(h >= 60 for h in r["sheetRows"]), "rows of the sheet are under 60: %s" % r)
        self.assertNotIn("pointercancel", r["swipe"]["ev"], "the browser took the gesture: %s" % r)
        self.assertFalse(r["swipe"]["open"], "the sheet did not close on a swipe: %s" % r)
        # З-2: значки «акцент» листа «Поделиться» — класс акцента, не «ok»
        self.assertEqual(len(r["icons"]), 3, r)
        for c in r["icons"]:
            self.assertIn("gx-mrow__ic--accent", c, r)
            self.assertNotIn("gx-mrow__ic--ok", c, r)

    # ── REV-336-mobile З-4, З-5, З-6, З-8, П-2 ──
    def test_phone_attention_people_and_one_accent(self):
        r = self.rows["phone-ux"]
        down = [x for x in r["att"] if "не работает" in x[0]]
        self.assertTrue(down, r["att"])
        for title, sub, tag, _ in down:
            self.assertTrue(sub, "no reason on the second line: %s" % r["att"])
            self.assertEqual(tag, "BUTTON", r["att"])
        # Т3: на «Протоколы» — строка неработающего протокола раскрыта (детали во весь экран больше нет)
        self.assertTrue(r["downGo"][0] and r["downGo"][1].startswith("#inbounds") and len(r["downGo"][2]) >= 1, r["downGo"])
        # UI-6 (аудит 20.10 №10): совета движка AmneziaWG в сводке нет — он в карточке протокола
        self.assertEqual([x for x in r["att"] if x[0].startswith("Движок AmneziaWG")], [], r["att"])
        # круг 1 Т1: плиток «Шлюз · Протоколы · Выходы · На связи» в Сводке варианта Б нет (замечания — строками с кнопкой)
        # строки «В сети» — часть всех (по id: объём за сутки растёт с открытым окном сборщика, 3.3.7.10), подпись — того же вида
        self.assertTrue(r["onlineIds"] and set(r["onlineIds"]) <= set(r["allIds"]), "the count follows the filter: %s / %s" % (r["allIds"], r["onlineIds"]))
        self.assertLess(len(r["onlineIds"]), len(r["allIds"]), r)
        self.assertTrue(all(x.split(" · ")[0] for x in r["online"]), r["online"])
        # UI-B 23.10: подпись строки — «Мама, в сети N из M, …» (имя, затем состояние, затем вторая строка)
        self.assertTrue(any(" # Мама, в сети " in x for x in r["all"]), "no state for the screen reader: %s" % r["all"])
        for k, acc in r["person"].items():
            self.assertEqual(len(acc), 1, "not one accent button: %s %s" % (k, acc))

    # ── REV-336-mobile З-7; Т3 (Б r3 Т2) вместо З-9 ──
    def test_phone_form_above_tabs_and_protocol_row(self):
        r = self.rows["phone-forms"]
        f = r["form"]
        self.assertGreater(f["tabsH"], 40, r)
        self.assertLessEqual(abs(f["footBottom"] - f["tabsTop"]), 1, "the bar is not above the tabs: %s" % r)
        # REV-336-mobile-2 З-11: вкладки под формой не нажимаются (inert) — и видно, что не нажимаются
        self.assertTrue(f["inert"], r)
        self.assertLess(f["tabsOpacity"], 0.6, r)
        # Т3: строки протоколов раскрываются на месте — кнопки в строке, без «⋯» и листа; касание не меньше 44 px; ничего
        # за правым краем
        row = r["row"]
        self.assertGreaterEqual(row["rows"], 5, r)
        self.assertEqual(row["open"], row["rows"], "not every row opened in place: %s" % r)
        self.assertFalse(row["sheet"], r)
        self.assertEqual(row["menus"], 0, "«⋯» is back in «Протоколы»: %s" % r)
        self.assertGreaterEqual(row["btns"], 3 * row["rows"], r)
        self.assertGreaterEqual(row["minH"], 44, "a button is shorter than a finger: %s" % r)
        self.assertEqual(row["wide"], 0, r)
        # П-11: переключатели протокола (L2TP, SOCKS5, Hysteria2) — role="switch"
        self.assertTrue(row["roles"] and all(x == "switch" for x in row["roles"]), r)

    # ── REV-336-mobile-2 З-10, П-10 ──
    def test_a_low_sheet_scrolls_instead_of_cutting(self):
        cases = self.rows["sheet-low"]["cases"]
        self.assertEqual([c["h"] for c in cases], [360, 300], cases)
        for c in cases:
            self.assertGreaterEqual(c["rows"], 3, c)
            self.assertEqual(c["clipped"], 0, "a list of the sheet is cut: %s" % c)
            self.assertTrue(c["lastSeen"], "the last item is not reachable: %s" % c)
        self.assertTrue(any(c["scrolls"] for c in cases), cases)

    def test_live_second_line_is_a_description(self):
        rows = self.rows["live-label"]["rows"]
        self.assertTrue(rows)
        for x in rows:
            self.assertTrue(x["desc"], x)
            self.assertNotIn(x["sub"], x["label"], x)                       # счётчики в подписи замёрзли бы


if __name__ == "__main__":
    unittest.main()

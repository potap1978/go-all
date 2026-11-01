"""SPEC-tg-proxy Р5: «Как бот ходит в Telegram» in the bot's card (ux.js telegramBot) — the groups with their keys and
«Напрямую», the line «Сейчас: …» from the core (st.path.text), a choice -> POST /api/bot/proxy {group, rev} and the
card read again; hidden without a token or without the core's answer; an error of the panel is said under it. RU and EN
(no Cyrillic in EN)."""

import json
import re
import unittest

from tests.test_333d_ux import HAS_NODE, prelude, run_node

CYR = re.compile("[А-Яа-яЁё]")

SCRIPT = r"""
(async () => {
  const calls = [], toasts = [];
  const groups = [{ id: "main", label: LBL, keys: 2 }, { id: "g-00a1b2", label: "Home", keys: 1 }];
  let st = { token: true, chat: true, running: true, chat_id: "123456789", choice: { group: "main", rev: 3, groups },
    path: { state: "group", text: "PATH-GROUP" } };
  let fail = false;
  const api = { get: () => Promise.resolve(Object.assign({ ok: true }, st)), check: () => Promise.resolve({}), test: () => Promise.resolve({}),
    proxy: (b) => { calls.push(b);
      if (fail) { return Promise.reject(Object.assign(new Error("SERVER-SAYS"), { status: 409 })); }
      st = Object.assign({}, st, { choice: Object.assign({}, st.choice, { group: b.group, rev: 4 }), path: { state: "direct", why: "chosen", text: "PATH-DIRECT" } });
      return Promise.resolve({ ok: true, rev: 4, group: b.group }); } };
  const b = X.telegramBot({ lang: LANG, api, open: true, toast: (t, s, kind) => toasts.push([t, kind]) });
  document.body.appendChild(b.el);
  await flush();
  const sel = b.el.all((n) => n.tagName === "SELECT")[0];
  const box = sel.parentNode.parentNode;
  const r = { opts: sel.children.map((o) => [o.value, o.textContent]), val: sel.value, hidden: box.hidden,
    line: txtOf(byCls(b.el, "ux-live")[2]), title: txtOf(box) };
  sel.value = "direct"; (sel.on.change || []).forEach((f) => f()); await flush();
  r.calls = calls.slice(); r.toasts = toasts.slice(); r.val2 = sel.value; r.line2 = txtOf(byCls(b.el, "ux-live")[2]);
  fail = true; sel.value = "main"; (sel.on.change || []).forEach((f) => f()); await flush();
  r.err = txtOf(byCls(b.el, "ux-live")[2]); r.val3 = sel.value; r.calls2 = calls.slice();
  st = { token: false, chat: false, running: false, choice: st.choice, path: null };
  await b.refresh();
  r.hiddenNoToken = box.hidden;
  st = { token: true, chat: true, running: true, choice: null, path: null };
  await b.refresh();
  r.hiddenNoCore = box.hidden;
  st = { token: true, chat: true, running: true, need_exit: true, choice: { group: "direct", rev: 5, groups },
    path: { state: "direct", why: "chosen", text: "PATH-DIRECT" } };
  await b.refresh();
  r.chosenBanner = txtOf(byCls(b.el, "ux-warnbar")[0]); r.chosenExitsBtn = byCls(b.el, "ux-warnbar")[0].all((n) => n.tagName === "BUTTON").length;
  console.log(JSON.stringify(r));
})();
"""


@unittest.skipUnless(HAS_NODE, "no node")
class BotProxyCardTest(unittest.TestCase):
    def run_card(self, lang):
        lbl = "Основная" if lang == "ru" else "Main"
        return run_node(prelude(lang) + "const LANG = %s, LBL = %s;\n" % (json.dumps(lang), json.dumps(lbl)) + SCRIPT)

    def test_choice_and_state_ru(self):
        r = self.run_card("ru")
        self.assertEqual(r["opts"], [["main", "Через выходы группы «Основная» — 2 ключа"],
                                     ["g-00a1b2", "Через выходы группы «Home» — 1 ключ"],
                                     ["direct", "Напрямую, с адреса этого сервера"]])
        self.assertEqual((r["val"], r["hidden"]), ("main", False))
        self.assertIn("Как бот ходит в Telegram", r["title"])
        self.assertEqual(r["line"], "Сейчас: PATH-GROUP")
        self.assertEqual(r["calls"][0], {"group": "direct", "rev": 3})
        self.assertEqual(r["toasts"], [["Сохранено: бот пойдёт в Telegram напрямую в течение минуты.", "success"]])
        self.assertEqual((r["val2"], r["line2"]), ("direct", "Сейчас: PATH-DIRECT"))
        self.assertEqual(r["calls2"][1:], [{"group": "main", "rev": 4}])
        self.assertEqual((r["err"], r["val3"]), ("SERVER-SAYS", "direct"))           # refused: the stored choice stays
        self.assertEqual((r["hiddenNoToken"], r["hiddenNoCore"]), (True, True))
        self.assertIn("выберите группу выходов", r["chosenBanner"])
        self.assertNotIn("вставьте ключ", r["chosenBanner"])
        self.assertEqual(r["chosenExitsBtn"], 0)

    def test_en_has_no_cyrillic(self):
        r = self.run_card("en")
        self.assertEqual(r["opts"][0], ["main", "Through the exits of «Main» — 2 keys"])
        self.assertEqual(r["opts"][1][1], "Through the exits of «Home» — 1 key")
        self.assertFalse(CYR.search(json.dumps([r["opts"], r["title"], r["line"], r["toasts"], r["chosenBanner"]],
                                                ensure_ascii=False)), r)


if __name__ == "__main__":
    unittest.main()

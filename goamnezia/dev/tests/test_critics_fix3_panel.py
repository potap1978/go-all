# -*- coding: utf-8 -*-
"""Панель: правки третьего круга критиков 17.10 (CRITICS-FIX-3) — в игрушечном DOM (tests/fixtures/panel_dom.js) под
node, как test_critics_fix2_panel.py; без node проверки пропускаются.
  A3-1  страница завела подписку — блок «Подписка» карточки под окном «Страница готова» сразу с ней.
  A3-4, A3-6  поле «Доступ» без календаря; «Страница готова» — «Страница открывается до».
  A3-8  подпись снимается пустым полем; подсказка без QR, где QR нет; дробь в своём сроке страницы; прокрутка карточки.
  A3-2  карточка подписки после перевыпуска — «перевыпущена <когда>» рядом с «с <дата>», счёт заборов нового адреса.
  A3-3  «Временная ссылка» → профиль: вкладки профилей как в карточке (перенос), а не сегмент в одну строку.
  B3-1/B3-2  «Добавить»: ошибка лимита/срока — под полем, не дублируется внизу, гаснет; предел — заранее, по-человечески.
  B3-7  «Сразу несколько»: «Сколько» не склеивает цифры — набранное остаётся, ошибка под полем.
  B3-3  окно отзыва страницы: сколько раз открывали; галочка «Перевыпустить и ключи…» (выключена) → keys=on мосту.
  B3-4  окна перевыпуска ключа — по фактам: страница с профилем, подписка с линией (до 12 ч / «Обновить»), иначе — вручную.
Запуск: python3 -m unittest tests/test_critics_fix3_panel.py
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "tests", "fixtures", "panel_dom.js")


def node_bin():
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"v(\d+)\.", out)
    return node if m and int(m.group(1)) >= 18 else None


NODE = node_bin()

JS = r"""
const { boot } = require(process.argv[2]);
const R = {};
async function scenario(name, opts, body) {
  const rec = { errors: [] };
  R[name] = rec;
  let P = null;
  try {
    P = await boot(opts);
    await body(P, rec);
  } catch (e) {
    rec.fatal = String((e && e.stack) || e);
  }
  if (P) { rec.errors = P.errors.slice(); P.close(); }
}
const txt = (n) => (n ? n.textContent.replace(/\s+/g, " ").trim() : null);
const ok = (data) => ({ status: 200, body: { ok: true, data } });
const dlgs = (P) => P.$$(".gx-dlg").map((d) => ({ title: txt(d.querySelector(".gx-dlg__title")), text: txt(d),
  checks: d.querySelectorAll('[role="checkbox"]').map((b) => [txt(b), b.getAttribute("aria-checked")]),
  foot: d.querySelectorAll(".gx-dlg__foot button").map(txt) }));
const toastText = (P) => P.$$("#toastHost .gx-toast").map(txt).join(" | ");
const PRO = {
  "/api/overview": (d) => ({ ...d, mode: "pro", domain: "vpn.example.com" }),
  "/api/settings": (d) => ({ ...d, config: { ...d.config, mode: "pro", domain: "vpn.example.com" } }),
};
const annaRow = (P) => P.$$(".gx-urow").find((n) => n.textContent.includes("Анна"));
const ANNA = { name: "anna", label: "Анна", created: "2026-09-02T10:00:00Z", enabled: true, quota_bytes: 0, expires: "", traffic: { rx: 0, tx: 0 },
  profiles: ["awg3"], state: "on", online: false, last_handshake: 0,
  peers: { awg3: { ip: "10.8.3.3", pubkey: "PUBANNA3", created: "2026-09-01T10:00:00Z", active: true, has_key: true, line: "3.1" } } };
const annaApi = (extra) => ({ "GET /api/users/anna": ok({ user: ANNA }),
  "GET /api/users/anna/key": ok({ name: "anna", line: "3.1", min_client: "5.0.1.5", conf: "[Interface]\\nPrivateKey = X\\n", vpn: "vpn://FAKE" }), ...extra });
const SUB = (extra) => ({ name: "anna", label: "Анна", created: "2026-10-03T10:00:00Z", rotated: "", generation: 1, fetches: 0,
  last_fetch: "", last_client: "", lines: [{ line: "3.1", profile: "awg3", has_key: true }], disabled: false, ...extra });
const subApi = (subs, extra) => annaApi({
  "GET /api/pages": ok({ available: true, pages: [] }),
  "POST /api/action/sub-list": ok({ status: "success", subs, count: subs.length, domain_ready: true, update_interval: 12 }),
  ...extra,
});
const subBox = (P) => txt(P.$$('[data-upages="sub"]')[0] || P.$$(".gx-upages").find((n) => /Подписка|Subscription/.test(txt(n))));

(async () => {
  for (const lang of ["ru", "en"]) {
    await scenario(`a32_${lang}`, { hash: "#users", lang, data: PRO,
      api: subApi([SUB({ rotated: "2026-10-17T08:17:35Z", generation: 2, fetches: 0 })]) }, async (P, rec) => {
      await P.tick(100);
      await P.click(annaRow(P));
      await P.tick(100);
      rec.box = subBox(P);
    });
  }
  await scenario("a32_fresh", { hash: "#users", lang: "ru", data: PRO, api: subApi([SUB({ fetches: 2, last_client: "curl" })]) }, async (P, rec) => {
    await P.tick(100);
    await P.click(annaRow(P));
    await P.tick(100);
    rec.box = subBox(P);
  });
  // B3-3: страницу открывали 3 раза — окно говорит это и предлагает перевыпустить ключи (выключено); вторая — не открывали.
  const PAGE = (id, visits, extra) => ({ id, user: "anna", created: "2026-10-16T10:00:00Z", expires: "2099-01-01T00:00:00Z", forever: false,
    visits, last_visit: "", profiles: ["awg3"], mirror: true, subscription: "created", ...extra });
  for (const lang of ["ru", "en"]) {
    const calls = [];
    await scenario(`b33_${lang}`, { hash: "#users", lang, data: PRO, api: annaApi({
      "GET /api/pages": ok({ available: true, pages: [PAGE("p-1a2b", 3), PAGE("p-3c4d", 0)] }),
      "POST /api/action/sub-list": ok({ status: "success", subs: [SUB({})], count: 1, domain_ready: true, update_interval: 12 }),
      "POST /api/action/page-revoke": (body) => { calls.push(body); return ok({ status: "success", id: body.id, user: "anna",
        sub_rotated: body.sub === "on", sub_present: body.sub === "on" ? true : null,
        keys_reissued: body.keys === "on" ? ["awg3"] : null, keys_failed: body.keys === "on" ? [] : null }); },
    }) }, async (P, rec) => {
      await P.tick(100);
      await P.click(annaRow(P));
      await P.tick(100);
      const revoke = (i) => P.$$('[data-upages="pages"] .gx-upage')[i].querySelectorAll("button").find((b) => /Отозвать|Revoke/.test(txt(b)));
      await P.click(revoke(0)); await P.tick(50);
      rec.d1 = dlgs(P).pop();
      // выключили подписку, включили ключи
      const boxes = () => P.$$('.gx-dlg [role="checkbox"]');
      await P.click(boxes()[0]); await P.tick(20);
      await P.click(boxes()[1]); await P.tick(20);
      rec.d1on = dlgs(P).pop().checks;
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(100);
      rec.toast1 = toastText(P);
      await P.click(revoke(1)); await P.tick(50);
      rec.d2 = dlgs(P).pop();
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(100);
      rec.calls = calls;
    });
  }
  // A3-1: подписки нет; page-create заводит её (subscription: created) — блок «Подписка» перечитывается сразу
  {
    let subs = [];
    await scenario("a31", { hash: "#users", lang: "ru", data: PRO, api: annaApi({
      "GET /api/pages": ok({ available: true, pages: [] }),
      "POST /api/action/sub-list": () => ok({ status: "success", subs, count: subs.length, domain_ready: true, update_interval: 12 }),
      "POST /api/action/page-create": () => { subs = [SUB({})]; return ok({ status: "success", message: "ok", id: "p-7a7a",
        url: "https://vpn.example.com/p-SECRET/", user: "anna", created: "2026-10-17T10:00:00Z", expires: "2026-10-24T10:00:00Z",
        forever: false, ttl: "7d", profiles: ["awg3"], mirror: true, imported_profiles: [], subscription: "created" }); },
    }) }, async (P, rec) => {
      await P.tick(100);
      await P.click(annaRow(P));
      await P.tick(100);
      rec.before = subBox(P);
      await P.click(P.$$('[data-upages="pages"] button').find((b) => /Создать/.test(txt(b)))); await P.tick(50);
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(200);
      rec.ready = dlgs(P).length;
      rec.readyText = dlgs(P).map((x) => x.text).join(" | ");
      rec.after = subBox(P);
    });
  }
  // A3-3: два профиля — в окне «Временная ссылка» вкладки профилей с переносом, выбор работает
  {
    const TWO = { ...ANNA, profiles: ["awg3", "awg2"], peers: { ...ANNA.peers,
      awg2: { ip: "10.8.2.3", pubkey: "PUBANNA2", created: "2026-09-01T10:00:00Z", active: true, has_key: true, line: "2.0" } } };
    await scenario("a33", { hash: "#users", lang: "ru", data: PRO, api: { ...annaApi({}), "GET /api/users/anna": ok({ user: TWO }),
      "GET /api/pages": ok({ available: true, pages: [] }),
      "POST /api/action/sub-list": ok({ status: "success", subs: [], count: 0, domain_ready: true, update_interval: 12 }) } }, async (P, rec) => {
      await P.tick(100);
      await P.click(annaRow(P));
      await P.tick(100);
      await P.click(P.$$("button").find((b) => /Временная ссылка/.test(txt(b))));
      await P.tick(100);
      const d = P.$$(".gx-dlg").pop();
      rec.title = d ? txt(d.querySelector(".gx-dlg__title")) : null;
      rec.full = d ? d.querySelectorAll(".gx-seg--full").length : -1;
      const tabs = () => P.$$(".gx-dlg").pop().querySelectorAll(".gx-ptabs > button");
      rec.tabs = tabs().map(txt);
      await P.click(tabs()[1]); await P.tick(20);
      rec.sel = tabs().map((b) => b.getAttribute("aria-selected"));
    });
  }
  // B3-1/B3-2: форма «Добавить»
  const fieldOf = (inp) => { let n = inp; while (n && !(n.classList && n.classList.contains("gx-field"))) n = n.parentNode; return n; };
  const hintOf = (inp) => { const f = fieldOf(inp); const h = f && f.querySelector(".gx-hint"); return h ? [txt(h), h.className.includes("gx-hint--bad")] : null; };
  await scenario("b32", { hash: "#users", lang: "ru", api: {
    "POST /api/action/user-add": (b) => ok({ status: "success", message: "ok", name: b.name }) } }, async (P, rec) => {
    await P.tick(100);
    await P.click(P.byText("Добавить", 'section[data-page="users"] button'));
    const ins = P.$$(".gx-sheet input");
    const idIn = ins[0], quotaIn = ins[ins.length - 2], daysIn = ins[ins.length - 1];
    await P.type(idIn, "num_test");
    await P.type(quotaIn, "99999999999999999999");
    rec.qBig = [hintOf(quotaIn), quotaIn.getAttribute("aria-invalid")];
    await P.type(daysIn, "-5");
    const create = () => P.$$(".gx-sheet button").find((b) => txt(b) === "Создать");
    const sheetText = () => txt(P.$$(".gx-sheet").pop());
    await P.click(create()); await P.tick(50);
    rec.text1 = sheetText();
    rec.calls1 = P.calls("POST", "/api/action/user-add").length;
    await P.type(quotaIn, "100");
    await P.type(daysIn, "99999");
    rec.dBig = hintOf(daysIn);
    await P.type(daysIn, "30");
    rec.text2 = sheetText();
    await P.click(create()); await P.tick(100);
    rec.adds = P.calls("POST", "/api/action/user-add").map((c) => c.body);
  });
  // B3-7: «Сразу несколько»
  await scenario("b37", { hash: "#users", lang: "ru", api: {} }, async (P, rec) => {
    await P.tick(100);
    await P.click(P.$$('section[data-page="users"] button').find((b) => /Добавить несколько/.test(txt(b))));
    await P.tick(50);
    const inp = P.$$(".gx-sheet input")[0];
    for (const v of ["1", "1,", "1,5"]) await P.type(inp, v);
    rec.v15 = [inp.value, hintOf(inp)];
    await P.type(inp, "-2");
    rec.vNeg = [inp.value, hintOf(inp)];
    await P.type(inp, "15");
    rec.v15ok = [inp.value, hintOf(inp)];
  });
  // A3-8: пустая подпись — снимается (мосту — подпись, равная идентификатору)
  await scenario("a38_rename", { hash: "#users", lang: "ru", data: PRO, api: annaApi({
    "GET /api/pages": ok({ available: true, pages: [] }),
    "POST /api/action/sub-list": ok({ status: "success", subs: [], count: 0, domain_ready: true, update_interval: 12 }),
    "POST /api/action/user-rename": (b) => ok({ status: "success", message: "ok", name: b.name, label: b.label }) }) }, async (P, rec) => {
    await P.tick(100);
    await P.click(annaRow(P));
    await P.tick(100);
    await P.click(P.$$(".gx-sheet button").find((b) => txt(b) === "Переименовать")); await P.tick(50);
    const d = P.$$(".gx-dlg").pop();
    await P.type(d.querySelector("input"), "");
    const okBtn = d.querySelectorAll(".gx-dlg__foot button").pop();
    rec.okDisabled = okBtn.disabled;
    rec.hint = txt(d);
    await P.click(okBtn); await P.tick(100);
    rec.calls = P.calls("POST", "/api/action/user-rename").map((c) => c.body);
  });
  // B3-4: перевыпуск ключа — окно и итог по фактам пользователя
  const reissueCase = (name, pages, subs) => scenario(name, { hash: "#users", lang: "ru", data: PRO, api: annaApi({
      "GET /api/pages": ok({ available: true, pages }),
      "POST /api/action/sub-list": ok({ status: "success", subs, count: subs.length, domain_ready: true, update_interval: 12 }),
      "POST /api/action/user-reissue-peer": ok({ status: "success", message: "ok", name: "anna", profile: "awg3" }),
    }) }, async (P, rec) => {
    await P.tick(100);
    await P.click(annaRow(P));
    await P.tick(100);
    await P.click(P.$$("button").find((b) => /Перевыпустить ключ/.test(txt(b))));
    await P.tick(100);
    const d = P.$$(".gx-dlg").pop();
    rec.ask = txt(d);
    await P.type(d.querySelector("input"), "anna");
    await P.click(d.querySelectorAll(".gx-dlg__foot button").pop());
    await P.tick(150);
    rec.done = dlgs(P).map((x) => x.text).join(" | ");
  });
  await reissueCase("b34_page_sub", [PAGE("p-1a2b", 0)], [SUB({})]);
  await reissueCase("b34_sub_only", [], [SUB({})]);
  await reissueCase("b34_none", [PAGE("p-9z9z", 0, { profiles: ["awg2"] })], []);
  console.log(JSON.stringify(R));
})();
"""


@unittest.skipUnless(NODE, "нужен node ≥ 18")
class CriticsFix3PanelTests(unittest.TestCase):
    R = {}

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="goamnezia-cf3-") as tmp:
            script = os.path.join(tmp, "cf3.js")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(JS)
            proc = subprocess.run([NODE, script, HARNESS], capture_output=True, text=True, timeout=240, cwd=ROOT)
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
        cls.R = json.loads(lines[-1]) if lines else {"__fatal": "no JSON: rc=%s\n%s\n%s" % (proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])}

    def scen(self, name):
        self.assertNotIn("__fatal", self.R, self.R.get("__fatal"))
        rec = self.R.get(name)
        self.assertIsNotNone(rec, name)
        self.assertNotIn("fatal", rec, rec.get("fatal"))
        self.assertEqual(rec["errors"], [], name)
        return rec

    def test_a3_2_card_says_the_subscription_was_rotated(self):
        """A3-2: после перевыпуска карточка говорит «перевыпущена 17.10.2026 …», заборов — 0 (счёт нового адреса)."""
        box = self.scen("a32_ru")["box"]
        self.assertRegex(box, r"с 03\.10\.2026 · перевыпущена 17\.10\.2026")
        self.assertRegex(box, r"Забирали: 0")
        self.assertRegex(self.scen("a32_en")["box"], r"since .* · rotated ")
        fresh = self.scen("a32_fresh")["box"]
        self.assertNotIn("перевыпущена", fresh)
        self.assertRegex(fresh, r"Забирали: 2")

    def test_b3_3_revoke_names_visits_and_offers_key_reissue(self):
        """B3-3: открывали — «открывали 3 раза», ключи у получателя продолжат работать; галочка ключей выключена, честно
        про устройства пользователя; включили — мосту keys=on, итог называет профиль; не открывали — галочки нет."""
        rec = self.scen("b33_ru")
        self.assertIn("Страницу открывали 3 раза — ключи", rec["d1"]["text"])
        self.assertIn("продолжат работать", rec["d1"]["text"])
        self.assertEqual(len(rec["d1"]["checks"]), 2, rec["d1"]["checks"])
        self.assertIn("Перевыпустить и ключи пользователя на её профилях (AmneziaWG 3.1)", rec["d1"]["checks"][1][0])
        self.assertIn("на устройствах самого пользователя", rec["d1"]["checks"][1][0])
        self.assertEqual(rec["d1"]["checks"][1][1], "false")
        self.assertEqual([c[1] for c in rec["d1on"]], ["false", "true"])
        self.assertEqual(rec["calls"][0], {"id": "p-1a2b", "sub": "off", "keys": "on"})
        self.assertIn("ключи перевыпущены (AmneziaWG 3.1)", rec["toast1"])
        self.assertNotIn("открывали", rec["d2"]["text"])
        self.assertEqual(len(rec["d2"]["checks"]), 1)
        self.assertNotIn("keys", rec["calls"][1])
        en = self.scen("b33_en")
        self.assertIn("The page was opened 3 times", en["d1"]["text"])
        self.assertIn("Reissue the user's keys on its profiles too", en["d1"]["checks"][1][0])

    def test_a3_1_card_shows_the_subscription_the_page_created(self):
        """A3-1: под окном «Страница готова» блок «Подписка» уже с подпиской, а не «Подписки нет»."""
        rec = self.scen("a31")
        self.assertIn("Подписки нет", rec["before"])
        self.assertGreaterEqual(rec["ready"], 1)
        self.assertNotIn("Подписки нет", rec["after"])
        self.assertIn("Забирали: 0", rec["after"])
        # A3-6: в окне — срок страницы («Страница открывается до …»), а не «Доступ до», как срок пользователя
        self.assertIn("Страница открывается", rec["readyText"])
        self.assertNotIn("Доступ до", rec["readyText"])

    def test_a3_4_access_preview_has_no_calendar_icon(self):
        """A3-4: поле «Доступ» в окне срока — предпросмотр, без значка календаря (не похоже на выбор даты)."""
        with open(os.path.join(ROOT, "admin-web", "static", "app.js"), encoding="utf-8") as fh:
            app = fh.read()
        i = app.index("  function openExpiry(user) {")
        fn = app[i:app.index("  function openRename(user) {", i)]
        self.assertIn('el("span", { class: "gx-ro gx-ro--font" }, until)', fn)
        self.assertNotIn('icon("calendar"', fn)

    def test_a3_3_share_profile_picker_wraps_like_card_tabs(self):
        """A3-3: выбор профиля во «Временной ссылке» — вкладки .gx-ptabs (перенос), без сегмента в одну строку."""
        rec = self.scen("a33")
        self.assertIn("Временная ссылка", rec["title"])
        self.assertEqual(rec["full"], 0)
        self.assertEqual(len(rec["tabs"]), 2, rec["tabs"])
        self.assertTrue(all(x.startswith("AmneziaWG") for x in rec["tabs"]), rec["tabs"])
        self.assertEqual(rec["sel"], ["false", "true"])

    def test_b3_1_b3_2_add_form_checks_range_once_under_the_field(self):
        """B3-2: огромный лимит — «Лимит — не больше 100000 ГБ» под полем, поле красное, мосту не уходит; срок 99999 —
        «не больше 3650 дней». B3-1: ошибка — только под полем (обе сразу), внизу не дублируется и после правки гаснет."""
        rec = self.scen("b32")
        self.assertIn("Лимит — не больше 100000 ГБ", rec["qBig"][0][0])
        self.assertTrue(rec["qBig"][0][1])
        self.assertEqual(rec["qBig"][1], "true")
        self.assertEqual(rec["calls1"], 0)
        self.assertEqual(rec["text1"].count("Лимит — не больше 100000 ГБ"), 1, rec["text1"])
        self.assertIn("Отрицательное число не подходит", rec["text1"])
        self.assertIn("Срок — не больше 3650 дней", rec["dBig"][0])
        self.assertNotIn("не больше", rec["text2"])
        self.assertNotIn("Отрицательное", rec["text2"])
        self.assertEqual(len(rec["adds"]), 1)
        self.assertEqual((rec["adds"][0]["quota_gb"], rec["adds"][0]["days"]), ("100", "30"))

    def test_b3_7_bulk_count_is_not_glued(self):
        """B3-7: «1,5» остаётся «1,5» с ошибкой под полем, «-2» — не «2»; верное число — без ошибки."""
        rec = self.scen("b37")
        self.assertEqual(rec["v15"][0], "1,5")
        self.assertTrue(rec["v15"][1][1])
        self.assertEqual(rec["vNeg"][0], "-2")
        self.assertTrue(rec["vNeg"][1][1])
        self.assertEqual(rec["v15ok"][0], "15")
        self.assertFalse(rec["v15ok"][1][1])

    def test_a3_8_nits(self):
        """A3-8: пустая подпись — кнопка активна, мосту label=name; подсказка без QR там, где QR серый; дробь в своём
        сроке страницы — «только целое число»; прокрутка карточки возвращается после загрузки страниц и подписки."""
        rec = self.scen("a38_rename")
        self.assertFalse(rec["okDisabled"])
        self.assertIn("пустое поле снимает подпись", rec["hint"])
        self.assertEqual(rec["calls"], [{"name": "anna", "label": "anna"}])
        with open(os.path.join(ROOT, "admin-web", "static", "app.js"), encoding="utf-8") as fh:
            app = fh.read()
        self.assertIn('t(["url", "deeplink", "key"].includes(it.qr) ? "subGuide2" : "subGuide2Copy")', app)
        self.assertIn('t(/\\d[.,]\\d/.test(custom) ? "udPageCustomFrac" : "udPageCustomBad")', app)
        self.assertIn("if (keep && loads.length) Promise.all(loads).then(", app)

    def test_b3_4_reissue_windows_follow_the_users_facts(self):
        """B3-4: страница с профилем и подписка — обе строки (подписка: до 12 ч, «Обновить»); только подписка — про
        страницу ни слова (страниц нет); ни того ни другого — «отправьте новый ключ из этой карточки»."""
        both = self.scen("b34_page_sub")
        self.assertIn("Страница подключения сразу отдаст новый ключ", both["ask"])
        self.assertIn("при следующем обновлении (до 12 ч)", both["ask"])
        self.assertIn("«Обновить»", both["ask"])
        self.assertIn("Страница подключения уже отдаёт новый ключ", both["done"])
        self.assertIn("заберёт его само (до 12 ч)", both["done"])
        sub = self.scen("b34_sub_only")
        self.assertNotIn("Страница подключения", sub["ask"])
        self.assertNotIn("Страница подключения", sub["done"])
        self.assertIn("Подписка уже отдаёт новый ключ", sub["done"])
        none = self.scen("b34_none")
        self.assertNotIn("Подписка", none["ask"])
        self.assertIn("подписки у пользователя нет — новый ключ нужно отправить ему из этой карточки", none["ask"])
        self.assertIn("отправьте ему новый ключ из этой карточки", none["done"])


if __name__ == "__main__":
    unittest.main()

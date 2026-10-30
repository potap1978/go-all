# -*- coding: utf-8 -*-
"""Панель: правки CRITICS-FIX-7 (17.10) — в игрушечном DOM (tests/fixtures/panel_dom.js) под node, как
test_critics_fix5_panel.py; без node проверки пропускаются.
  B4-2  после «Отозвать страницу» с перевыпуском ключей карточка не отдаёт прежний ключ из кэша вкладки.
  A4-1/B4-1  окно отзыва берёт число открытий из свежей записи страницы; блоки страниц и подписки открытой карточки
        перечитываются по таймеру — и под открытым окном, не трогая его поля.
Запуск: python3 -m unittest tests/test_critics_fix7_panel.py
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
const checks = (P) => { const d = P.$$(".gx-dlg").pop(); return d ? d.querySelectorAll('[role="checkbox"]').map((b) => [txt(b), b.getAttribute("aria-checked"), Boolean(b.disabled)]) : []; };
const PRO = {
  "/api/overview": (d) => ({ ...d, mode: "pro", domain: "vpn.example.com" }),
  "/api/settings": (d) => ({ ...d, config: { ...d.config, mode: "pro", domain: "vpn.example.com" } }),
};
const annaRow = (P) => P.$$(".gx-urow").find((n) => n.textContent.includes("Анна"));
const PEER = (ip, line) => ({ ip, pubkey: "PUB" + ip, created: "2026-09-01T10:00:00Z", active: true, has_key: true, line });
const ANNA = { name: "anna", label: "Анна", created: "2026-09-02T10:00:00Z", enabled: true, quota_bytes: 0, expires: "",
  traffic: { rx: 0, tx: 0 }, profiles: ["awg3", "awg2"], state: "on", online: false, last_handshake: 0,
  peers: { awg3: PEER("10.8.3.3", "3.1"), awg2: PEER("10.8.2.3", "2.0") } };
const SUB = (fetches) => ({ name: "anna", label: "Анна", created: "2026-10-03T10:00:00Z", rotated: "", generation: 1, fetches,
  last_fetch: "", last_client: "", lines: [{ line: "3.1", profile: "awg3", has_key: true }], disabled: false, prev_fetches: 0, prev_last_fetch: "" });
const PAGE = (visits) => ({ id: "p-1a2b", user: "anna", created: "2026-10-16T10:00:00Z", expires: "2099-01-01T00:00:00Z", forever: false,
  visits, last_visit: visits ? "2026-10-17T10:00:00Z" : "", profiles: ["awg3"], mirror: true, subscription: "exists" });
// Сервер с изменяемым состоянием: S.visits, S.fetches, S.gen (поколение ключа — меняется при перевыпуске).
const server = (S, calls) => ({
  "GET /api/users/anna": () => ok({ user: ANNA }),
  "GET /api/users/anna/key": () => ok({ name: "anna", line: "3.1", min_client: "5.0.1.5", conf: `[Interface]\nPrivateKey = KEY${S.gen}\n`, vpn: `vpn://KEY${S.gen}` }),
  "GET /api/pages": () => ok({ available: true, pages: S.page ? [PAGE(S.visits)] : [] }),
  "POST /api/action/sub-list": () => ok({ status: "success", subs: [SUB(S.fetches)], count: 1, domain_ready: true, update_interval: 12 }),
  "POST /api/action/page-revoke": (body) => { calls.push(body); if (body.keys === "on") S.gen += 1; S.page = false;
    return ok({ status: "success", id: body.id, user: "anna", sub_rotated: false, sub_present: true, keys_reissued: body.keys === "on" ? ["awg3"] : [], keys_failed: [] }); },
});
const keyGets = (P) => P.fetchLog.filter((f) => f.method === "GET" && f.path === "/api/users/anna/key").length;
const pageKv = (P) => txt(P.$$('[data-upages="pages"] .gx-upage__kv')[0]);
const subKv = (P) => txt(P.$$('[data-upages="sub"] .gx-upage__kv')[0]);
async function openCard(P) {
  await P.tick(100);
  await P.click(annaRow(P));
  await P.tick(100);
}
const revokeBtn = (P) => P.$$('[data-upages="pages"] .gx-upage')[0].querySelectorAll("button").find((b) => /Отозвать|Revoke/.test(txt(b)));

(async () => {
  // A4-1/B4-1: карточку открыли при «Открытий: 0»; страницу открыли, пока карточка открыта — окно отзыва знает об этом.
  for (const lang of ["ru", "en"]) {
    const S = { visits: 0, fetches: 0, gen: 1, page: true }, calls = [];
    await scenario(`fresh_visits_${lang}`, { hash: "#users", lang, data: PRO, api: server(S, calls) }, async (P, rec) => {
      await openCard(P);
      rec.kvBefore = pageKv(P);
      S.visits = 1;
      await P.click(revokeBtn(P)); await P.tick(50);
      rec.checks = checks(P);
      rec.text = txt(P.$$(".gx-dlg").pop());
    });
  }
  // A4-1/B4-1: живая карточка — «Открытий» и «Забирали» обновляются по таймеру и под открытым окном; поле окна цело.
  {
    const S = { visits: 0, fetches: 0, gen: 1, page: true }, calls = [];
    await scenario("live_card", { hash: "#users", lang: "ru", data: PRO, api: server(S, calls) }, async (P, rec) => {
      await openCard(P);
      rec.kv0 = [pageKv(P), subKv(P)];
      S.visits = 2; S.fetches = 5;
      await P.tick(29000);
      rec.kvEarly = [pageKv(P), subKv(P)];
      await P.tick(1500);
      rec.kv1 = [pageKv(P), subKv(P)];
      // Окно поверх карточки: «Переименовать» с набранным текстом
      const ren = P.$$(".gx-ubtns2 button").find((b) => /Переименовать/.test(txt(b)));
      await P.click(ren); await P.tick(50);
      const inp = P.$$(".gx-dlg input").pop();
      inp.value = "Новая подпись";
      S.visits = 3; S.fetches = 7;
      await P.tick(30500);
      rec.kv2 = [pageKv(P), subKv(P)];
      rec.inputKept = inp.isConnected && inp.value === "Новая подпись";
      rec.dlgOpen = P.$$(".gx-dlg").length;
    });
  }
  // B4-2: карточка показала ключ (поколение 1); отзыв страницы с ключами — карточка берёт ключ у ядра заново.
  for (const [name, withKeys] of [["keys_on", true], ["keys_off", false]]) {
    const S = { visits: 1, fetches: 0, gen: 1, page: true }, calls = [];
    await scenario(name, { hash: "#users", lang: "ru", data: PRO, api: server(S, calls) }, async (P, rec) => {
      await openCard(P);
      rec.before = keyGets(P);
      await P.click(revokeBtn(P)); await P.tick(50);
      const boxes = P.$$('.gx-dlg [role="checkbox"]');
      // единственная галочка — «и ключи» (подписка не от этой страницы: галочка подписки выключена)
      const keysBox = boxes.find((b) => /ключи пользователя/.test(txt(b)));
      if (withKeys) { await P.click(keysBox); await P.tick(20); }
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(200);
      rec.after = keyGets(P);
      rec.calls = calls;
    });
  }
  // A4-3/B4-4: окна перевыпуска ключа — срок забора подписки с приложениями, которые обновляют её реже (update_slow).
  const dlgText = (P) => P.$$(".gx-dlg").map(txt).join(" | ");
  for (const [name, lang, slow] of [["slow_ru", "ru", [{ name: "FlClash", hours: 24 }, { name: "Box", hours: 48 }, { name: "Cat", hours: 24 }]],
                                    ["slow_en", "en", [{ name: "FlClash", hours: 24 }]], ["slow_none_ru", "ru", [{ name: "Fast", hours: 6 }]],
                                    ["slow_old_ru", "ru", null]]) {
    const S = { visits: 0, fetches: 0, gen: 1, page: false }, calls = [];
    const api = server(S, calls);
    api["POST /api/action/sub-list"] = () => ok({ status: "success", subs: [SUB(0)], count: 1, domain_ready: true, update_interval: 12,
      ...(slow ? { update_slow: slow } : {}) });
    api["POST /api/action/user-reissue-peer"] = () => ok({ status: "success", message: "ok", name: "anna", profile: "awg3" });
    await scenario(name, { hash: "#users", lang, data: PRO, api }, async (P, rec) => {
      await openCard(P);
      await P.click(P.$$("button").find((b) => /Перевыпустить ключ|Reissue key/.test(txt(b))));
      await P.tick(100);
      const d = P.$$(".gx-dlg").pop();
      rec.ask = txt(d);
      await P.type(d.querySelector("input"), "anna");
      await P.click(d.querySelectorAll(".gx-dlg__foot button").pop());
      await P.tick(150);
      rec.done = dlgText(P);
    });
  }
  // B4-5/A4-5: ⌘K — по началу слова и с формами
  for (const lang of ["ru", "en"]) {
    await scenario(`cmdk_${lang}`, { hash: "#overview", lang, data: PRO }, async (P, rec) => {
      await P.tick(100);
      await P.key("k", { metaKey: true });
      rec.q = {};
      const qs = lang === "ru" ? ["пользователь", "пользователей", "Пользователи", "страница", "временная ссылка", "временн", "ссылк", "ссылка",
        "коротк", "короткая ссылка", "отозвать", "MTU", "подписк", "zzzz"] : ["user", "temporary link", "short links", "page", "revoke"];
      for (const q of qs) {
        await P.type(".gx-cmd input", q);
        rec.q[q] = P.$$(".gx-cmd__i").map((n) => txt(n).replace(/↵$/, ""));  // «↵» первой строки — клавиша, не подпись
      }
      await P.type(".gx-cmd input", lang === "ru" ? "временная ссылка" : "temporary link");
      await P.click(P.$$(".gx-cmd__i")[0]); await P.tick(100);
      rec.page = P.page();
      rec.tab = txt(P.$$('.gx-utabs [aria-selected="true"]')[0]);
    });
  }
  // A4-4: снятая подпись — «Подпись снята», «было: «Анна»»
  {
    const S = { visits: 0, fetches: 0, gen: 1, page: false }, calls = [];
    const api = server(S, calls);
    api["POST /api/action/user-rename"] = (body) => { calls.push(body); return ok({ status: "success", message: "ok" }); };
    await scenario("rename_clear", { hash: "#users", lang: "ru", data: PRO, api }, async (P, rec) => {
      await openCard(P);
      await P.click(P.$$(".gx-ubtns2 button").find((b) => /Переименовать/.test(txt(b)))); await P.tick(50);
      await P.type(P.$$(".gx-dlg input").pop(), "   ");
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(100);
      rec.toast = P.$$("#toastHost .gx-toast").map(txt).join(" | ");
      rec.calls = calls;
    });
  }
  // A4-5: «Включить» в окне «Пользователь выключен» продолжает начатое — открывает окно ссылки и окно страницы
  for (const [name, re] of [["enable_share", /^Временная ссылка$/], ["enable_page", /^Страница подключения$/]]) {
    const S = { enabled: false }, calls = [];
    const api = server({ visits: 0, fetches: 0, gen: 1, page: false }, calls);
    api["GET /api/users/anna"] = () => ok({ user: { ...ANNA, enabled: S.enabled, state: S.enabled ? "on" : "disabled" } });
    api["POST /api/action/user-enable"] = (body) => { calls.push(body); S.enabled = true; return ok({ status: "success", message: "ok" }); };
    await scenario(name, { hash: "#users", lang: "ru", data: PRO, api }, async (P, rec) => {
      await openCard(P);
      await P.click(P.$$(".gx-sheet button").find((b) => re.test(txt(b)))); await P.tick(50);
      rec.ask = txt(P.$$(".gx-dlg").pop());
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(200);
      rec.calls = calls;
      rec.dlg = P.$$(".gx-dlg").map(txt).join(" | ");
    });
  }
  // A4-5: «#/users» открывает «Пользователей», в адресе — «#users»
  await scenario("hash_slash", { hash: "#/users", lang: "ru", data: PRO }, async (P, rec) => {
    await P.tick(100);
    rec.page = P.page();
    rec.hash = P.win.location.hash;
    P.win.location.hash = "#/profiles/"; await P.tick(50);
    rec.page2 = P.page();
    rec.hash2 = P.win.location.hash;
  });
  // R7-3: включили, но свежая запись не пришла (сеть) — окно не открывается, тост «включён — создайте ссылку ещё раз»
  for (const [name, re] of [["enable_fail_share", /^Временная ссылка$/], ["enable_fail_page", /^Страница подключения$/]]) {
    const S = { enabled: false }, calls = [];
    const api = server({ visits: 0, fetches: 0, gen: 1, page: false }, calls);
    api["GET /api/users/anna"] = () => S.enabled ? { status: 503, body: { ok: false, error: "down", code: "transport" } }
      : ok({ user: { ...ANNA, enabled: false, state: "disabled" } });
    api["POST /api/action/user-enable"] = (body) => { calls.push(body); S.enabled = true; return ok({ status: "success", message: "ok" }); };
    await scenario(name, { hash: "#users", lang: "ru", data: PRO, api }, async (P, rec) => {
      await openCard(P);
      await P.click(P.$$(".gx-sheet button").find((b) => re.test(txt(b)))); await P.tick(50);
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(200);
      rec.calls = calls;
      rec.dlg = P.$$(".gx-dlg").map(txt).join(" | ");
      rec.toast = P.$$("#toastHost .gx-toast").map(txt).join(" | ");
    });
  }
  // Хвост R8-3: включение ответило «успешно», а свежая запись пришла с enabled === false — окно не открывается, тост
  // «Не удалось включить пользователя» (ru и en), а не «включён — создайте ещё раз».
  for (const [name, lang, re] of [["enable_off_share_ru", "ru", /^Временная ссылка$/], ["enable_off_page_ru", "ru", /^Страница подключения$/],
    ["enable_off_share_en", "en", /^Temporary link$/], ["enable_off_page_en", "en", /^Connection page$/]]) {
    const calls = [];
    const api = server({ visits: 0, fetches: 0, gen: 1, page: false }, calls);
    api["GET /api/users/anna"] = () => ok({ user: { ...ANNA, enabled: false, state: "disabled" } });
    api["POST /api/action/user-enable"] = (body) => { calls.push(body); return ok({ status: "success", message: "ok" }); };
    await scenario(name, { hash: "#users", lang, data: PRO, api }, async (P, rec) => {
      await openCard(P);
      await P.click(P.$$(".gx-sheet button").find((b) => re.test(txt(b)))); await P.tick(50);
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(200);
      rec.calls = calls;
      rec.dlg = P.$$(".gx-dlg").map(txt).join(" | ");
      rec.toast = P.$$("#toastHost .gx-toast").map(txt).join(" | ");
    });
  }
  // R7-2: возврат на вкладку перечитывает блоки не чаще раза в 10 с
  {
    const S = { visits: 0, fetches: 0, gen: 1, page: true }, calls = [];
    await scenario("live_visibility", { hash: "#users", lang: "ru", data: PRO, api: server(S, calls) }, async (P, rec) => {
      const reads = () => P.fetchLog.filter((f) => (f.method === "GET" && f.path === "/api/pages") || (f.method === "POST" && f.path === "/api/action/sub-list")).length;
      const back = async () => {
        P.doc.hidden = true; P.doc.visibilityState = "hidden"; P.doc.dispatchEvent(new P.win.Event("visibilitychange")); await P.tick(10);
        P.doc.hidden = false; P.doc.visibilityState = "visible"; P.doc.dispatchEvent(new P.win.Event("visibilitychange")); await P.tick(50);
        return reads();
      };
      await openCard(P);
      await P.tick(1000);
      rec.open = reads();
      rec.backSoon = await back();            // через ~1 с после открытия — блоки только что прочитаны
      await P.tick(30500);
      rec.tick = reads();                      // круг по таймеру
      rec.backAfterTick = await back();        // через ~0 с после круга — без запросов
      await P.tick(5000);
      rec.back5 = await back();                // через 5 с — тоже нет
      await P.tick(5000);
      S.visits = 4;
      rec.back10 = await back();               // через 10 с — круг
      rec.kv = pageKv(P);
      rec.back10again = await back();          // и сразу снова — нет
    });
  }
  // R7-1: ⌘K при 100 и 1000 пользователях — разделы и действия выше людей и не отрезаются лимитом; группа
  // «Пользователи» у строк людей не сравнивается по началу слова.
  for (const n of [100, 1000]) {
    for (const lang of ["ru", "en"]) {
      const many = Array.from({ length: n }, (_, i) => {
        const id = String(i + 1).padStart(4, "0");
        return { ...ANNA, name: `user${id}`, label: lang === "ru" ? `Гость ${id}` : `Guest ${id}`, peers: { awg3: PEER(`10.8.${3 + (i >> 8)}.${(i % 250) + 2}`, "3.1") } };
      });
      await scenario(`cmdk_many_${n}_${lang}`, { hash: "#users", lang, data: { ...PRO, "/api/users": (d) => ({ ...d, users: many }) } }, async (P, rec) => {
        await P.tick(100);
        await P.key("k", { metaKey: true });
        rec.q = {};
        const qs = lang === "ru" ? ["пользователь", "Пользователи", "Гость", ""] : ["user", "users", "guest", ""];
        for (const q of qs) {
          await P.type(".gx-cmd input", q);
          // [группа, подпись] каждой строки — по заголовкам групп списка
          let g = null; const rows = [];
          for (const ch of P.$(".gx-cmd__list").children) {
            if (ch.classList.contains("gx-cmd__g")) g = txt(ch);
            else if (ch.classList.contains("gx-cmd__i")) rows.push([g, txt(ch).replace(/↵$/, "")]);
          }
          rec.q[q] = rows;
        }
      });
    }
  }
  // R8-1: ⌘K по силе совпадения — имя человека целиком или его начало выше раздела, найденного по основе или подстроке
  for (const lang of ["ru", "en"]) {
    const ppl = [["nastya", "Настя"], ["alexey", "Алексей"], ["dima", "Дима"], ["router", ""]].map(([name, label], i) =>
      ({ ...ANNA, name, label, peers: { awg3: PEER(`10.8.3.${i + 10}`, "3.1") } }));
    await scenario(`cmdk_rank_${lang}`, { hash: "#users", lang, data: { ...PRO, "/api/users": (d) => ({ ...d, users: ppl }) },
      api: Object.fromEntries(ppl.map((u) => [`GET /api/users/${u.name}`, () => ok({ user: u })])) }, async (P, rec) => {
      await P.tick(100);
      await P.key("k", { metaKey: true }); await P.tick(50);
      rec.q = {};
      for (const q of (lang === "ru" ? ["настя", "ал", "ди", "наст", ""] : ["ro", "router", ""])) {
        await P.type(".gx-cmd input", q);
        rec.q[q] = P.$$(".gx-cmd__i").map(txt);
      }
      await P.type(".gx-cmd input", lang === "ru" ? "настя" : "ro");
      await P.key("Enter", {}, ".gx-cmd input"); await P.tick(150);
      rec.page = P.page();
      rec.title = P.$$(".gx-sheet__title").map(txt);
      rec.cards = P.fetchLog.filter((f) => f.method === "GET" && /^\/api\/users\/[^/]+$/.test(f.path)).map((f) => f.path);
    });
  }
  // R9-2: заголовок каждой группы — один раз, группы по лучшему пункту; R9-3: en «users» + Enter — раздел «Users».
  // Люди с подписями разделов («Настройки», «Протоколы», «Обзор», «Диагностика») перемешивали группы в списке.
  for (const lang of ["ru", "en"]) {
    const ppl = [["nastya", "Настя"], ["prohor", "Прохор"], ["u_set", "Настройки"], ["u_prot", "Протоколы"], ["u_ov", "Обзор"],
      ["u_diag", "Диагностика"], ["u_set_en", "Settings"], ["u_prot_en", "Protocols"]].map(([name, label], i) =>
      ({ ...ANNA, name, label, peers: { awg3: PEER(`10.8.3.${i + 10}`, "3.1") } }));
    await scenario(`cmdk_groups_${lang}`, { hash: "#users", lang, data: { ...PRO, "/api/users": (d) => ({ ...d, users: ppl }) },
      api: Object.fromEntries(ppl.map((u) => [`GET /api/users/${u.name}`, () => ok({ user: u })])) }, async (P, rec) => {
      await P.tick(100);
      await P.key("k", { metaKey: true }); await P.tick(50);
      rec.q = {};
      const qs = lang === "ru" ? ["наст", "про", "о", "т", "пользователи", ""] : ["set", "pro", "o", "t", "users", "user", ""];
      for (const q of qs) {
        await P.type(".gx-cmd input", q);
        const heads = []; const rows = []; let g = null;
        for (const ch of P.$(".gx-cmd__list").children) {
          if (ch.classList.contains("gx-cmd__g")) { g = txt(ch); heads.push(g); }
          else if (ch.classList.contains("gx-cmd__i")) rows.push([g, ch.querySelectorAll("span").filter((s) => !s.classList.contains("gx-cmd__h") && s.textContent).map((s) => s.textContent)[0], ch.classList.contains("is-first")]);  // подпись — без подсказки
        }
        rec.q[q] = { heads, rows };
      }
      await P.type(".gx-cmd input", lang === "ru" ? "наст" : "users");
      await P.key("Enter", {}, ".gx-cmd input"); await P.tick(150);
      rec.page = P.page();
      rec.sheets = P.$$(".gx-sheet__title").map(txt);
      rec.cards = P.fetchLog.filter((f) => f.method === "GET" && /^\/api\/users\/[^/]+$/.test(f.path)).map((f) => f.path);
    });
  }
  console.log(JSON.stringify(R));
})();
"""


@unittest.skipUnless(NODE, "нужен node 18+")
class CriticsFix7PanelTests(unittest.TestCase):
    R = {}

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="goamnezia-cf7-") as tmp:
            script = os.path.join(tmp, "cf7.js")
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

    def test_a41_revoke_window_reads_the_page_fresh(self):
        """A4-1/B4-1: в карточке «Открытий: 0», страницу открыли — окно отзыва говорит «открывали 1 раз» и даёт
        галочку перевыпуска ключей."""
        rec = self.scen("fresh_visits_ru")
        self.assertIn("Открытий: 0", rec["kvBefore"])
        self.assertIn("Страницу открывали 1 раз", rec["text"])
        self.assertTrue(any("ключи пользователя" in c[0] for c in rec["checks"]), rec["checks"])
        en = self.scen("fresh_visits_en")
        self.assertIn("The page was opened 1 time", en["text"])
        self.assertEqual(len(en["checks"]), 2, en["checks"])

    def test_a41_card_blocks_are_live(self):
        """A4-1/B4-1: «Открытий» и «Забирали» перечитываются раз в 30 с — и под открытым окном, поле окна цело."""
        rec = self.scen("live_card")
        self.assertIn("Открытий: 0", rec["kv0"][0])
        self.assertIn("Забирали: 0", rec["kv0"][1])
        self.assertIn("Открытий: 0", rec["kvEarly"][0])
        self.assertIn("Открытий: 2", rec["kv1"][0])
        self.assertIn("Забирали: 5", rec["kv1"][1])
        self.assertIn("Открытий: 3", rec["kv2"][0])
        self.assertIn("Забирали: 7", rec["kv2"][1])
        self.assertTrue(rec["inputKept"])
        self.assertEqual(rec["dlgOpen"], 1)

    def test_b42_revoke_with_keys_drops_the_cached_key(self):
        """B4-2: после отзыва с ключами карточка заново берёт ключ у ядра; без ключей — кэш прежний."""
        on = self.scen("keys_on")
        self.assertEqual(on["calls"], [{"id": "p-1a2b", "sub": "off", "keys": "on"}])
        self.assertGreater(on["after"], on["before"])
        off = self.scen("keys_off")
        self.assertEqual(off["calls"], [{"id": "p-1a2b", "sub": "off"}])
        self.assertEqual(off["after"], off["before"])

    def test_a43_reissue_windows_name_slow_apps(self):
        """A4-3/B4-4: подписку FlClash забирает раз в сутки — окна перевыпуска говорят «до 12 ч, у FlClash — до 24 ч»;
        приложения с одним сроком — вместе, сначала самые долгие; быстрее совета сервера — не называются; старое ядро
        без update_slow — как раньше."""
        rec = self.scen("slow_ru")
        self.assertIn("при следующем обновлении (до 12 ч, у Box — до 48 ч, у FlClash, Cat — до 24 ч)", rec["ask"])
        self.assertIn("заберёт его само (до 12 ч, у Box — до 48 ч, у FlClash, Cat — до 24 ч)", rec["done"])
        en = self.scen("slow_en")
        self.assertIn("on its next refresh (within 12 h, FlClash — within 24 h)", en["ask"])
        self.assertIn("picks it up by itself (within 12 h, FlClash — within 24 h)", en["done"])
        for name in ("slow_none_ru", "slow_old_ru"):
            r = self.scen(name)
            self.assertIn("при следующем обновлении (до 12 ч)", r["ask"], name)
            self.assertIn("заберёт его само (до 12 ч)", r["done"], name)

    def test_b45_command_search_by_word_start_and_forms(self):
        """B4-5/A4-5: «пользователь» находит «Пользователи» и «Добавить пользователя»; «страница», «временная ссылка»,
        «ссылк», «коротк», «отозвать» — свои пункты; прежние запросы работают; мусор — ничего."""
        q = self.scen("cmdk_ru")["q"]
        for k in ("пользователь", "пользователей", "Пользователи"):
            self.assertIn("Пользователи", q[k], k)
            self.assertTrue(any("Добавить пользователя" in x for x in q[k]), (k, q[k]))
        self.assertTrue(any("Страница подключения — в карточке" in x for x in q["страница"]), q["страница"])
        for k in ("временная ссылка", "временн", "ссылк", "ссылка"):
            self.assertTrue(any(x.startswith("Временные ссылки") for x in q[k]), (k, q[k]))
        for k in ("коротк", "короткая ссылка"):
            self.assertTrue(any(x.startswith("Короткие ссылки") for x in q[k]), (k, q[k]))
        self.assertTrue(any("Страница подключения" in x for x in q["отозвать"]), q["отозвать"])
        self.assertTrue(any("Подписка" in x for x in q["отозвать"]), q["отозвать"])
        self.assertTrue(any("MTU" in x for x in q["MTU"]), q["MTU"])
        self.assertTrue(any("Подписка" in x for x in q["подписк"]), q["подписк"])
        self.assertEqual(q["zzzz"], [])
        rec = self.scen("cmdk_ru")
        self.assertEqual(rec["page"], "users")
        self.assertIn("Временные ссылки", rec["tab"])
        en = self.scen("cmdk_en")
        self.assertIn("Users", en["q"]["user"])
        self.assertTrue(any(x.startswith("Temporary links") for x in en["q"]["temporary link"]), en["q"]["temporary link"])
        self.assertTrue(any(x.startswith("Short links") for x in en["q"]["short links"]), en["q"]["short links"])
        self.assertTrue(any("Connection page" in x for x in en["q"]["page"]), en["q"]["page"])
        self.assertTrue(any("Connection page" in x for x in en["q"]["revoke"]), en["q"]["revoke"])
        self.assertEqual(en["page"], "users")

    def test_r71_command_search_with_many_users(self):
        """R7-1: при 100 и 1000 пользователях раздел «Пользователи» и «Добавить пользователя» находятся, стоят выше
        строк людей и не отрезаются лимитом; группа «Пользователи» не делает каждого человека совпадением по началу
        слова; людей — не больше 60."""
        for n in (100, 1000):
            for lang, sect, add, qs, person_q, word_q in (
                    ("ru", "Пользователи", "Добавить пользователя", ("пользователь", "Пользователи", "Гость", ""), "Гость", "пользователь"),
                    ("en", "Users", "Add user", ("user", "users", "guest", ""), "guest", None)):
                rec = self.scen("cmdk_many_%d_%s" % (n, lang))
                for q in qs:
                    rows = rec["q"][q]
                    where = (n, lang, q)
                    people = [i for i, (g, _) in enumerate(rows) if g == sect]
                    others = [i for i, (g, _) in enumerate(rows) if g != sect]
                    # люди — одним хвостом после всех разделов и действий; пустой запрос (R8-1) — прежний порядок, люди
                    # первыми, а разделы и действия после них, не отрезанные лимитом
                    if people and others:
                        if q:
                            self.assertLess(max(others), min(people), where)
                        else:
                            self.assertLess(max(people), min(others), where)
                    self.assertLessEqual(len(people), 60, where)
                    if q != person_q:
                        self.assertIn(["Разделы" if lang == "ru" else "Sections", sect], rows, where)
                        # en «users» не основа «user» (формы — только у русских слов): «Add user» им не находится
                        if (lang, q) != ("en", "users"):
                            self.assertTrue(any(lbl.startswith(add) for g, lbl in rows if g != sect), (where, rows[:5]))
                    if q in (person_q, ""):
                        self.assertEqual(len(people), 60, where)
                # «пользователь» — не начало слова ни в одной строке человека: группу людей по началу слова не сравнивают
                if word_q:
                    self.assertEqual([r for r in rec["q"][word_q] if r[0] == sect], [], (n, lang))
                # en: «user» — начало имени user0001, люди находятся, но под разделами и действиями
                if lang == "en":
                    self.assertEqual(len([r for r in rec["q"]["user"] if r[0] == sect]), 60, n)

    def test_r81_command_search_ranks_by_match_strength(self):
        """R8-1: имя человека целиком или его начало стоит выше раздела или действия, найденного по основе слова или
        подстроке: «настя» + Enter открывает карточку Насти, «ал» — Алексей первым, «ди» — Дима первым (ближе, чем
        «Диагностика»); при равной силе разделы и действия выше людей; пустой запрос — люди первыми, как раньше."""
        ru = self.scen("cmdk_rank_ru")
        q = ru["q"]
        self.assertTrue(q["настя"][0].startswith("Настя"), q["настя"][:3])
        self.assertTrue(any(x.startswith("Вид") for x in q["настя"]), q["настя"])  # «Настройки → Вид» по основе — ниже
        self.assertTrue(q["ал"][0].startswith("Алексей"), q["ал"][:3])
        self.assertTrue(q["ди"][0].startswith("Дима"), q["ди"][:3])
        self.assertTrue(any(x.startswith("Диагностика") for x in q["ди"]), q["ди"])
        self.assertTrue(q[""][0].startswith("Настя"), q[""][:3])
        self.assertTrue(any(x.startswith("Добавить пользователя") for x in q[""]), q[""][:8])
        # «наст»: «Настройки» (раздел) и «Настя» начинаются с запроса; «Настя» ближе — на 1 букву, «Настройки» — на 5
        self.assertTrue(q["наст"][0].startswith("Настя"), q["наст"][:3])
        self.assertTrue(any(x.startswith("Настройки") for x in q["наст"]), q["наст"])
        self.assertEqual(ru["page"], "users")
        self.assertEqual(ru["cards"], ["/api/users/nastya"])
        self.assertEqual(ru["title"], ["Настя"])
        en = self.scen("cmdk_rank_en")
        self.assertTrue(en["q"]["ro"][0].startswith("router"), en["q"]["ro"][:3])
        self.assertTrue(any(x.startswith("Protocols") for x in en["q"]["ro"]), en["q"]["ro"])
        self.assertTrue(en["q"]["router"][0].startswith("router"), en["q"]["router"][:3])
        self.assertTrue(en["q"][""][0].startswith("Алексей") or en["q"][""][0].startswith("Настя"), en["q"][""][:3])
        self.assertEqual((en["page"], en["cards"]), ("users", ["/api/users/router"]))
        for r in (ru, en):
            self.assertEqual(r.get("fatal"), None)
            self.assertEqual(r["errors"], [])

    def test_r92_command_search_groups_once(self):
        """R9-2: заголовок каждой группы ⌘K — один раз, группы — сплошными блоками по лучшему пункту, внутри группы — по
        силе совпадения; первая строка и Enter прежние («наст» — Настя). R9-3: en «users» + Enter открывает раздел
        «Users», а не «Add several users at once»."""
        for lang in ("ru", "en"):
            rec = self.scen("cmdk_groups_%s" % lang)
            self.assertEqual(rec.get("fatal"), None)
            self.assertEqual(rec["errors"], [])
            for q, got in rec["q"].items():
                heads, rows = got["heads"], got["rows"]
                self.assertEqual(len(heads), len(set(heads)), (lang, q, heads))
                # строки идут блоками: группа меняется только на заголовке, и каждая группа — одним куском
                seq = [g for g, _, _ in rows]
                blocks = [g for i, g in enumerate(seq) if i == 0 or seq[i - 1] != g]
                self.assertEqual(blocks, heads, (lang, q))
                if rows:
                    self.assertTrue(rows[0][2], (lang, q))
                    self.assertEqual(sum(1 for r in rows if r[2]), 1, (lang, q))
        ru = self.scen("cmdk_groups_ru")["q"]
        users, sects = "Пользователи", "Разделы"
        # «наст»: Настя — первой (ближе на 1 букву), внутри группы людей — по силе: Настя, затем «Настройки» (человек);
        # группа «Разделы» с «Настройками» — следом, одним блоком
        rows = ru["наст"]["rows"]
        self.assertEqual(rows[0][:2], [users, "Настя"])
        people = [lbl for g, lbl, _ in rows if g == users]
        self.assertLess(people.index("Настя"), next(i for i, x in enumerate(people) if x.startswith("Настройки")))
        self.assertIn([sects, "Настройки"], [r[:2] for r in rows])
        self.assertEqual(ru["наст"]["heads"][:2], [users, sects])
        self.assertEqual(ru["про"]["rows"][0][:2], [users, "Прохор"])
        self.assertEqual(ru["пользователи"]["rows"][0][:2], [sects, "Пользователи"])
        self.assertTrue(ru[""]["rows"][0][1].startswith("Настя"))
        rec = self.scen("cmdk_groups_ru")
        self.assertEqual((rec["page"], rec["cards"]), ("users", ["/api/users/nastya"]))
        en = self.scen("cmdk_groups_en")
        # R9-3: подпись «Users» совпадает с запросом целиком — раздел выше действия «Add several users at once»
        self.assertEqual(en["q"]["users"]["rows"][0][:2], ["Sections", "Users"])
        self.assertTrue(any(lbl.startswith("Add several users") for _, lbl, _ in en["q"]["users"]["rows"]))
        self.assertTrue(en["q"]["user"]["rows"][0][1].startswith("Add user"), en["q"]["user"]["rows"][:3])
        rec = self.scen("cmdk_groups_en")
        self.assertEqual(rec["page"], "users")
        self.assertEqual(rec["sheets"], [], "Enter на «users» — раздел, а не окно «Add several users at once»")
        self.assertEqual(rec["cards"], [])

    def test_a44_cleared_label_toast(self):
        """A4-4: пустое поле — тост «Подпись снята» и прежняя подпись, а не «anna → anna»."""
        rec = self.scen("rename_clear")
        self.assertEqual(rec["calls"], [{"name": "anna", "label": "anna"}])
        self.assertIn("Подпись снята", rec["toast"])
        self.assertIn("было: «Анна»", rec["toast"])
        self.assertNotIn("anna → anna", rec["toast"])

    def test_a45_enable_continues_the_action(self):
        """A4-5: «Включить» в окне «Пользователь выключен» включает и открывает окно ссылки / страницы."""
        share = self.scen("enable_share")
        self.assertIn("Пользователь выключен", share["ask"])
        self.assertEqual(share["calls"], [{"name": "anna"}])
        self.assertIn("Создать ссылку", share["dlg"])
        page = self.scen("enable_page")
        self.assertEqual(page["calls"], [{"name": "anna"}])
        self.assertIn("Создать страницу", page["dlg"])

    def test_r73_enabled_but_not_continued_says_so(self):
        """R7-3: включение прошло, а свежая запись не пришла — окно ссылки или страницы не открывается, и тост
        говорит, что пользователь включён и ссылку (R8-3: для страницы подключения — страницу) надо создать ещё раз."""
        for name, toast in (("enable_fail_share", "Пользователь включён — создайте ссылку ещё раз"),
                            ("enable_fail_page", "Пользователь включён — создайте страницу ещё раз")):
            rec = self.scen(name)
            self.assertEqual(rec["calls"], [{"name": "anna"}], name)
            self.assertNotIn("Создать ссылку", rec["dlg"], name)
            self.assertNotIn("Создать страницу", rec["dlg"], name)
            self.assertIn(toast, rec["toast"], name)

    def test_r83_enabled_record_still_off_says_failed(self):
        """Хвост R8-3: свежая запись после включения пришла с enabled === false — окно ссылки или страницы не
        открывается, тост «Не удалось включить пользователя» (en «Could not enable the user»), без «включён»."""
        for name, toast, wrong in (("enable_off_share_ru", "Не удалось включить пользователя", "включён"),
                                   ("enable_off_page_ru", "Не удалось включить пользователя", "включён"),
                                   ("enable_off_share_en", "Could not enable the user", "is enabled"),
                                   ("enable_off_page_en", "Could not enable the user", "is enabled")):
            rec = self.scen(name)
            self.assertEqual(rec["calls"], [{"name": "anna"}], name)
            for btn in ("Создать ссылку", "Создать страницу", "Create link", "Create page"):
                self.assertNotIn(btn, rec["dlg"], name)
            self.assertIn(toast, rec["toast"], name)
            self.assertNotIn(wrong, rec["toast"], name)

    def test_r72_tab_return_rereads_at_most_every_10s(self):
        """R7-2: возврат на вкладку перечитывает блоки страниц и подписки не чаще раза в 10 с от прошлого круга."""
        rec = self.scen("live_visibility")
        self.assertEqual(rec["backSoon"], rec["open"])
        self.assertEqual(rec["tick"], rec["open"] + 2)
        self.assertEqual(rec["backAfterTick"], rec["tick"])
        self.assertEqual(rec["back5"], rec["tick"])
        self.assertEqual(rec["back10"], rec["tick"] + 2)
        self.assertIn("Открытий: 4", rec["kv"])
        self.assertEqual(rec["back10again"], rec["back10"])

    def test_a45_hash_with_slash(self):
        """A4-5: «#/users» и «#/profiles/» — те же разделы, адрес приводится к «#users»."""
        rec = self.scen("hash_slash")
        self.assertEqual((rec["page"], rec["hash"]), ("users", "#users"))
        self.assertEqual((rec["page2"], rec["hash2"]), ("profiles", "#profiles"))

    def test_a45_overview_host_label_does_not_wrap(self):
        """A4-5: подпись строки с доменом («В ключах») — одной строкой, домен — многоточием."""
        with open(os.path.join(ROOT, "admin-web", "static", "styles.css"), encoding="utf-8") as fh:
            css = fh.read()
        self.assertIn(".gx-srv > div.gx-srv__host > span:first-child { white-space: nowrap; flex: none; }", css)


if __name__ == "__main__":
    unittest.main()

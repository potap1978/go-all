# -*- coding: utf-8 -*-
"""Панель целиком (index.html + app.js) в игрушечном DOM под node: клики и переходы по разделам.

ACC5-2 (живой стенд 0.5.0): любой клик мимо кнопок разделов — по заголовку, пустому месту, «🎲», «Вид» — уводил
на «Обзор». На <html> стоит data-nav — режим меню из «Вида» (full | icons | top), а общий обработчик кликов искал
ближайший [data-nav] и находил его у любого клика; раздела «full» нет, и setPage откатывал панель на «Обзор». Заодно
не работали поиск по кнопке (окно закрывалось тем же кликом) и лист «Ещё» на телефоне.

Здесь панель грузится как в браузере — первый скрипт index.html ставит вид на <html>, потом app.js — и кликается
на компьютере (три вида меню) и в мини-аппе Telegram на телефоне. Среда — tests/fixtures/panel_dom.js; без node
(на сервере его нет) эти проверки пропускаются, как отрисовка страницы подключения в test_pages_dryrun.sh.

Запуск: python3 -m unittest tests/test_panel_dom.py
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "admin-web", "static")
HARNESS = os.path.join(ROOT, "tests", "fixtures", "panel_dom.js")


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def node_bin():
    """node не старше 18 (?? и structuredClone в среде) или None."""
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

# Сценарии кликов. Каждый шаг пишет, какой раздел открыт и что в адресе; итог — одна строка JSON.
SCENARIOS_JS = r"""
const { boot } = require(process.argv[2]);
const ROOT_OVERRIDE = process.argv[3] || undefined;
const R = {};
async function scenario(name, opts, body) {
  const rec = { steps: [], errors: [] };
  R[name] = rec;
  let P = null;
  try {
    P = await boot({ root: ROOT_OVERRIDE, ...opts });
    const step = (label, extra = {}) => rec.steps.push({ label, page: P.page(), hash: P.hash(), ...extra });
    await body(P, step, rec);
  } catch (e) {
    rec.fatal = String((e && e.stack) || e);
  }
  if (P) { rec.errors = P.errors.slice(); P.close(); }
}
const has = (P, sel) => !!P.$(sel);
// Подложить в открытый раздел то, что мог бы нарисовать будущий код: кнопку несуществующего раздела и контейнер с data-goto.
function plant(P, html) {
  const box = P.doc.createElement("div");
  box.innerHTML = html;
  P.$("section[data-page]:not([hidden])").append(box);
}

(async () => {
  // Компьютер, полное меню: клики мимо кнопок разделов раздел не меняют.
  await scenario("desktop_off_targets", { hash: "#profiles" }, async (P, step, rec) => {
    rec.mode = P.doc.documentElement.getAttribute("data-nav");
    step("start");
    await P.click("h1.gx-h1"); step("title");
    const card = P.byText("AmneziaWG 3.1", "h2");
    rec.cardTitle = card ? card.textContent.trim() : null;
    await P.click(card); step("card title");
    await P.click("#main"); step("empty main");
    await P.click('section[data-page="profiles"]'); step("empty section");
    await P.click(".gx-frame"); step("frame");
    await P.click(P.doc.body); step("body");
    await P.click(P.doc.documentElement); step("html");
    await P.click(".gx-search"); step("search", { cmd: has(P, ".gx-cmd") });
    await P.click(".gx-cmd input"); step("search input", { cmd: has(P, ".gx-cmd") });
    await P.key("Escape"); step("search closed", { cmd: has(P, ".gx-cmd") });
    await P.click(".gx-viewbtn"); step("view", { look: has(P, ".gx-sheet.gx-look") });
    await P.click(".gx-look .gx-dice"); step("dice", { look: has(P, ".gx-sheet.gx-look") });
  });

  // Переходы — как прежде: меню, «Все» на Обзоре, адрес; неизвестный раздел с клика — ни шага.
  await scenario("desktop_nav", { hash: "#overview" }, async (P, step, rec) => {
    step("start");
    rec.srvIp = P.$$(".gx-srv > div.gx-srv__ip > span:last-child").map((s) => s.textContent);
    await P.click('#side button[data-goto="users"]'); step("menu users");
    await P.click('#side button[data-goto="profiles"]'); step("menu profiles");
    await P.tick(50);
    rec.imgs = P.$$(".gx-pcard").map((c) => [c.getAttribute("data-profile"), (c.querySelector(".gx-pcard__img") || {}).textContent || ""]);
    await P.click('#side button[data-goto="overview"]'); step("menu overview");
    await P.click(".gx-allbtn"); step("overview all");
    await P.go("#backups"); step("hash backups");
    await P.go("#nope"); step("hash unknown");
    await P.click('#side button[data-goto="logs"]'); step("menu logs");
    plant(P, '<button type="button" id="ghost" data-goto="nope">?</button><div id="box" data-goto="users"><span id="inner">x</span></div>');
    await P.click("#ghost"); step("unknown target");
    await P.click("#inner"); step("container target");
    const cur = P.$('#side button[aria-current="page"]');
    rec.current = cur ? cur.getAttribute("data-goto") : null;
  });

  // Первый запуск: на Обзоре шаги «поставьте профиль → добавьте людей» ведут кнопками в разделы.
  await scenario("overview_steps", { hash: "#overview", data: { "/api/overview": (d) => ({ ...d, profiles: [], users_total: 0 }) } }, async (P, step, rec) => {
    step("start");
    rec.stepBtns = P.$$(".gx-steprow__btn[data-goto]").map((b) => b.getAttribute("data-goto"));
    await P.click(".gx-steprow__btn[data-goto]"); step("first step");
  });

  // ⌘K и Ctrl+K: открыть, выбрать раздел мышью и клавишей, закрыть повтором.
  await scenario("desktop_cmdk", { hash: "#overview" }, async (P, step) => {
    await P.key("k", { metaKey: true }); step("cmd open", { cmd: has(P, ".gx-cmd") });
    await P.click(P.byText("Пользователи", ".gx-cmd__i")); step("cmd click users", { cmd: has(P, ".gx-cmd") });
    await P.key("k", { ctrlKey: true }); step("ctrl-k open", { cmd: has(P, ".gx-cmd") });
    await P.type(".gx-cmd input", "Трафик");
    await P.key("Enter", {}, ".gx-cmd input"); step("cmd enter traffic", { cmd: has(P, ".gx-cmd") });
    await P.key("k", { metaKey: true });
    await P.key("k", { metaKey: true }); step("cmd toggle", { cmd: has(P, ".gx-cmd") });
  });

  // «🎲 Случайное имя» в шторке нового пользователя: имя пришло от ядра, раздел тот же.
  await scenario("users_dice", { hash: "#users" }, async (P, step, rec) => {
    step("start");
    await P.click(P.byText("Добавить", 'section[data-page="users"] button')); step("add sheet", { sheet: has(P, ".gx-randbtn") });
    await P.click(".gx-randbtn"); step("dice", { sheet: has(P, ".gx-randbtn") });
    rec.suggestCalls = P.calls("POST", "/api/action/name-suggest").length;
    rec.values = P.$$(".gx-sheet input").map((i) => i.value);
  });

  // «Добавить сразу несколько» (users-add-bulk, просьба владельца 03.10): шторка — число, галочки профилей, имена
  // (случайные или префикс); итог — списком в той же шторке, сбой на середине — кто добавлен и на ком встало.
  const BULK_OK = (body) => ({ status: 200, body: { ok: true, data: { status: "success", message: "ok", requested: Number(body.count),
    count: Number(body.count), partial: false, failed: null, profiles: String(body.profiles || "").split(","), prefix: body.prefix || "",
    added: Array.from({ length: Number(body.count) }, (_, i) => ({ name: `${body.prefix || "bulk"}-0${i + 1}`, label: body.prefix ? `${body.prefix}-0${i + 1}` : `Человек ${i + 1}` })) } } });
  const bulkState = (P) => {
    const sheet = P.$(".gx-sheet");
    const pf = sheet && sheet.querySelectorAll(".gx-field")[3];
    return sheet ? { title: (sheet.querySelector(".gx-sheet__title") || { textContent: "" }).textContent,
      items: sheet.querySelectorAll(".gx-blist__i").map((b) => b.textContent.trim()), alert: (sheet.querySelector(".gx-al") || { textContent: "" }).textContent,
      foot: sheet.querySelectorAll(".gx-sheet__foot button").map((b) => b.textContent.trim()), prefixHidden: pf ? !P.visible(pf) : null } : null;
  };
  const runBulk = async (P, rec, prefix) => {
    await P.click(P.byText("Добавить несколько", 'section[data-page="users"] button'));
    rec.open = bulkState(P);
    await P.type(".gx-sheet input", "0");
    await P.click(P.$$(".gx-sheet__foot button").pop());
    rec.badCount = { calls: P.calls("POST", "/api/action/users-add-bulk").length, hint: (P.$(".gx-sheet .gx-hint--bad") || { textContent: "" }).textContent };
    await P.type(".gx-sheet input", "3");
    if (prefix) {
      await P.click(P.$$(".gx-sheet .gx-radio__i")[1]);
      await P.type(P.$$(".gx-sheet input")[1], prefix);
    }
    await P.click(P.$$(".gx-sheet__foot button").pop());
    rec.calls = P.calls("POST", "/api/action/users-add-bulk").map((c) => c.body);
    rec.done = bulkState(P);
  };
  await scenario("users_bulk", { hash: "#users", api: { "POST /api/action/users-add-bulk": BULK_OK } }, async (P, step, rec) => {
    rec.toolbar = P.$$('section[data-page="users"] .gx-toolbar button').map((b) => b.textContent.trim()).slice(-2);
    await runBulk(P, rec, "team");
    step("done");
  });
  await scenario("users_bulk_partial", { hash: "#users", api: { "POST /api/action/users-add-bulk": () => ({ status: 200, body: { ok: true, data: {
    status: "success", message: "ok", requested: 3, count: 1, partial: true, profiles: ["awg3", "awg2"], prefix: "",
    added: [{ name: "foggy_london", label: "Туманный Лондон" }], failed: { name: "sunny_manila", code: "keys_failed", message: "Не удалось сгенерировать ключи." } } } }) } },
  async (P, step, rec) => { await runBulk(P, rec, ""); });
  // Мини-апп «Панель» в Telegram на телефоне — та же кнопка и та же шторка, запрос подписан initData.
  await scenario("users_bulk_miniapp", { tg: true, width: 390, api: { "POST /api/action/users-add-bulk": BULK_OK } }, async (P, step, rec) => {
    await P.click('#tabs button[data-goto="users"]');
    rec.device = P.doc.documentElement.getAttribute("data-device");
    // Одиночное добавление там же: шторка «Новый пользователь» открывается и уходит в user-add
    await P.click(P.byText("Добавить", 'section[data-page="users"] button'));
    rec.single = { title: (P.$(".gx-sheet .gx-sheet__title") || { textContent: "" }).textContent };
    await P.type(".gx-sheet input", "tgsolo");
    await P.click(P.$$(".gx-sheet__foot button").pop());
    rec.single.calls = P.calls("POST", "/api/action/user-add").map((c) => c.body);
    await P.key("Escape");
    await runBulk(P, rec, "");
    rec.signed = P.calls("POST", "/api/action/users-add-bulk").every((c) => !!c.headers["X-Telegram-Init-Data"]);
  });

  // «Удалить сразу несколько» (users-del-bulk, просьба владельца 03.10): «Ещё» → режим выбора с галочками, «все на
  // странице», счётчиком и «Удалить выбранные»; по условию — та же шторка; кого задевает выбор, считает ядро
  // (users-del-bulk-preview); подтверждение — набрать число; список изменился — новый список; итог — списком.
  const DEL_USERS = (d) => ({ ...d, users: [...d.users,
    { ...d.users[1], name: "bob", label: "Боб", enabled: false, state: "off", disabled_reason: "manual" },
    { ...d.users[1], name: "office-01", label: "" }, { ...d.users[1], name: "office-02", label: "" }] });
  const DEL_PICK = (body) => (body.names ? String(body.names).split(",") : (body.prefix ? ["office-01", "office-02"] : (body.state === "disabled" ? ["bob"] : [])));
  const DEL_PREVIEW = (body) => { const names = DEL_PICK(body); return { status: 200, body: { ok: true, data: { status: "success", message: "ok", count: names.length,
    max: 100, too_many: false, selected: names.map((n) => ({ name: n, label: n === "bob" ? "Боб" : "" })), missing: [] } } }; };
  const DEL_OK = (body) => { const names = DEL_PICK(body); return { status: 200, body: { ok: true, data: { status: "success", message: "ok", requested: names.length,
    count: names.length, deleted: names, kept: [], partial: false, failed: null } } }; };
  const selState = (P) => { const bar = P.$("#uselBar"); return bar ? { count: bar.querySelector(".gx-selbar__n").textContent, all: bar.querySelector(".gx-selall").getAttribute("aria-checked"),
    del: [bar.querySelector(".gx-seldel").textContent.trim(), bar.querySelector(".gx-seldel").disabled] } : null; };
  const delSheet = (P) => { const s = P.$(".gx-sheet"); return s ? { title: s.querySelector(".gx-sheet__title").textContent,
    radios: s.querySelectorAll(".gx-radio__i").map((b) => [b.querySelector(".gx-radio__t").textContent, b.getAttribute("aria-checked")]),
    items: s.querySelectorAll(".gx-dlist__i").map((i) => i.textContent.trim()), alerts: s.querySelectorAll(".gx-al").map((a) => a.textContent.trim()),
    foot: s.querySelectorAll(".gx-sheet__foot button").map((b) => [b.textContent.trim(), b.disabled]) } : null; };
  // typedConfirm: заголовок и неверное число — кнопка неактивна; верное — удаление
  const confirmNumber = async (P, rec, n) => {
    const d = P.$$(".gx-dlg").pop();
    const ok = d.querySelectorAll(".gx-dlg__foot button").pop();
    rec.dialog = { title: d.querySelector(".gx-dlg__title").textContent, word: d.querySelector(".gx-field__l b").textContent };
    await P.type(d.querySelector("input"), String(n + 1));
    rec.dialog.wrongDisabled = ok.disabled;
    await P.type(d.querySelector("input"), String(n));
    await P.click(ok);
  };
  await scenario("users_del_select", { hash: "#users", data: { "/api/users": DEL_USERS },
    api: { "POST /api/action/users-del-bulk-preview": DEL_PREVIEW, "POST /api/action/users-del-bulk": DEL_OK } }, async (P, step, rec) => {
    rec.toolbar = P.$$('section[data-page="users"] .gx-toolbar button').map((b) => b.textContent.trim()).filter(Boolean);
    await P.click(".gx-umore");
    rec.menu = P.$$(".gx-umenu__i .gx-blist__l").map((n) => n.textContent);
    await P.click(P.$$(".gx-umenu__i")[0]);
    rec.selToolbar = P.$$('section[data-page="users"] .gx-toolbar button').map((b) => b.textContent.trim()).filter(Boolean);
    rec.start = selState(P);
    const rows = () => P.$$(".gx-urow");
    rec.row = [rows()[2].getAttribute("role"), rows()[2].getAttribute("aria-checked")];
    await P.click(rows()[2]);
    await P.click(rows()[3]);
    rec.two = { ...selState(P), checked: rows().filter((r) => r.getAttribute("aria-checked") === "true").length, sheets: P.$$(".gx-sheet").length };
    await P.click(".gx-selall"); rec.all = selState(P);
    await P.click(".gx-selall"); rec.none = selState(P);
    await P.click(rows()[2]); await P.click(rows()[3]);
    await P.click(".gx-seldel");
    rec.sheet = delSheet(P);
    rec.preview = P.calls("POST", "/api/action/users-del-bulk-preview").map((c) => c.body);
    await P.click(P.$$(".gx-sheet__foot button").pop());
    await confirmNumber(P, rec, 2);
    rec.calls = P.calls("POST", "/api/action/users-del-bulk").map((c) => c.body);
    rec.done = delSheet(P);
    rec.after = { bar: !!P.$("#uselBar"), more: !!P.$(".gx-umore"), checkboxes: P.$$(".gx-urow[role=checkbox]").length };
    await P.key("k", { metaKey: true });
    rec.cmd = P.$$(".gx-cmd__i").map((b) => b.textContent.trim()).filter((s) => /Удалить польз|Удалить выключ|Выбрать польз/.test(s));
  });
  // По условию, а список успел измениться: ядро отказало confirm_mismatch — шторка показывает новый список и число
  let delPreviews = 0;
  await scenario("users_del_rule_changed", { hash: "#users", data: { "/api/users": DEL_USERS },
    api: { "POST /api/action/users-del-bulk-preview": (body) => { delPreviews += 1; const r = DEL_PREVIEW(body);
      if (delPreviews > 1) { r.body.data.count = 3; r.body.data.selected.push({ name: "office-03", label: "" }); } return r; },
    "POST /api/action/users-del-bulk": (body) => (body.confirm === "2" ? { status: 409, body: { ok: false, error: "Подтверждено 2, а выбрано сейчас 3", code: "confirm_mismatch",
      data: { status: "error", code: "confirm_mismatch", matched: 3 } } } : DEL_OK(body)) } }, async (P, step, rec) => {
    await P.click(".gx-umore");
    await P.click(P.$$(".gx-umenu__i")[1]);
    rec.open = delSheet(P);
    await P.type(".gx-sheet input", "bad prefix");
    await P.tick(400);
    rec.bad = { hint: P.$(".gx-sheet .gx-hint").textContent, foot: delSheet(P).foot, calls: P.calls("POST", "/api/action/users-del-bulk-preview").length };
    await P.type(".gx-sheet input", "office-");
    await P.tick(400);
    rec.ok = delSheet(P);
    await P.click(P.$$(".gx-sheet__foot button").pop());
    await confirmNumber(P, rec, 2);
    rec.changed = delSheet(P);
    rec.calls = P.calls("POST", "/api/action/users-del-bulk").map((c) => c.body);
  });
  // Сбой второй пачки (partial, kept) и больше 100 под выбор — кнопка неактивна, объяснение словами
  await scenario("users_del_partial_many", { hash: "#users", data: { "/api/users": DEL_USERS },
    api: { "POST /api/action/users-del-bulk-preview": (body) => (body.state === "quota" ? { status: 200, body: { ok: true, data: { status: "success", count: 150, max: 100,
      too_many: true, selected: [], missing: [] } } } : DEL_PREVIEW(body)),
    "POST /api/action/users-del-bulk": () => ({ status: 200, body: { ok: true, data: { status: "success", requested: 2, count: 1, deleted: ["office-01"], kept: ["office-02"],
      partial: true, failed: { name: "office-02", code: "user_del_failed", message: "контейнер не принял изменения." } } } }) } }, async (P, step, rec) => {
    await P.click(".gx-umore");
    await P.click(P.$$(".gx-umenu__i")[4]);
    rec.many = delSheet(P);
    await P.click(P.$$(".gx-sheet .gx-radio__i")[0]);
    await P.type(".gx-sheet input", "office");
    await P.tick(400);
    await P.click(P.$$(".gx-sheet__foot button").pop());
    await confirmNumber(P, rec, 2);
    rec.done = delSheet(P);
  });
  // Мини-апп «Панель» в Telegram на телефоне: «Ещё» значком рядом с поиском, галочки на карточках, запрос подписан
  await scenario("users_del_miniapp", { tg: true, width: 390, data: { "/api/users": DEL_USERS },
    api: { "POST /api/action/users-del-bulk-preview": DEL_PREVIEW, "POST /api/action/users-del-bulk": DEL_OK } }, async (P, step, rec) => {
    await P.click('#tabs button[data-goto="users"]');
    rec.device = P.doc.documentElement.getAttribute("data-device");
    const more = P.$(".gx-umore");
    rec.more = [more.textContent.trim(), more.getAttribute("aria-label")];
    await P.click(more);
    await P.click(P.$$(".gx-umenu__i")[0]);
    const cards = () => P.$$(".gx-ucard");
    rec.card = [cards()[2].getAttribute("role"), cards()[2].getAttribute("aria-checked"), !!cards()[2].querySelector(".gx-usel .gx-box")];
    await P.click(cards()[2]);
    rec.sel = selState(P);
    await P.click(".gx-seldel");
    rec.sheet = delSheet(P);
    await P.click(P.$$(".gx-sheet__foot button").pop());
    await confirmNumber(P, rec, 1);
    rec.done = delSheet(P);
    rec.calls = P.calls("POST", "/api/action/users-del-bulk").map((c) => c.body);
    rec.signed = P.calls("POST", "/api/action/users-del-bulk").every((c) => !!c.headers["X-Telegram-Init-Data"]);
  });

  // Меню иконками и сверху: на <html> другой режим, клики мимо кнопок так же безвредны.
  for (const nav of ["icons", "top"]) {
    await scenario(`nav_${nav}`, { hash: "#profiles", look: { nav } }, async (P, step, rec) => {
      rec.mode = P.doc.documentElement.getAttribute("data-nav");
      step("start");
      await P.click("#main"); step("empty main");
      await P.click(P.doc.documentElement); step("html");
      await P.click(".gx-viewbtn"); step("view", { look: has(P, ".gx-sheet.gx-look") });
      await P.key("Escape");
      await P.click('button[data-goto="users"]'); step("nav users");
    });
  }

  // Мини-апп Telegram на телефоне: тот же app.js, нижние вкладки, лист «Ещё», поиск кнопкой.
  await scenario("miniapp", { tg: true, width: 390 }, async (P, step, rec) => {
    rec.mode = P.doc.documentElement.getAttribute("data-nav");
    rec.device = P.doc.documentElement.getAttribute("data-device");
    rec.tg = P.tgEvents.map((e) => e.type);
    rec.signed = P.fetchLog.some((c) => !!c.headers["X-Telegram-Init-Data"]);
    step("start");
    await P.click('#tabs button[data-goto="profiles"]'); step("tab profiles");
    await P.click(".gx-phead__title"); step("title");
    await P.click(P.byText("AmneziaWG 3.1", "h2")); step("card title");
    await P.click("#main"); step("empty main");
    await P.click(P.$$(".gx-phead .gx-roundbtn")[1]); step("view", { look: has(P, ".gx-sheet.gx-look") });
    await P.click(".gx-look .gx-dice"); step("dice", { look: has(P, ".gx-sheet.gx-look") });
    await P.key("Escape"); step("view closed", { look: has(P, ".gx-sheet.gx-look") });
    await P.click(P.$$("#tabs button").find((b) => !b.hasAttribute("data-goto"))); step("more", { more: has(P, ".gx-more") });
    await P.click('.gx-more button[data-goto="logs"]'); step("more logs", { more: has(P, ".gx-more") });
    await P.click(P.$$(".gx-phead .gx-roundbtn")[0]); step("search", { cmd: has(P, ".gx-cmd") });
    await P.click(P.byText("Пользователи", ".gx-cmd__i")); step("search users", { cmd: has(P, ".gx-cmd") });
    await P.click('#tabs button[data-goto="overview"]'); step("tab overview");
  });

  // Ни «null», ни «undefined» текстом: append(null) браузер вставляет строкой «null» — так было в хвосте шапки.
  const stray = (P) => {
    const out = [];
    const walk = (n) => n.childNodes.forEach((k) => {
      if (k.nodeType === 3 && /\b(null|undefined)\b/.test(k.data) && !k.parentNode.closest("script")) out.push(`${k.parentNode.getAttribute("class") || k.parentNode.localName}: ${k.data.trim().slice(0, 40)}`);
      if (k.nodeType === 1) walk(k);
    });
    walk(P.doc);
    return out;
  };
  for (const who of ["tunnel", "code"]) {
    await scenario(`stray_${who}`, { hash: "#overview", data: { "/api/session": (d) => ({ ...d, who }) } }, async (P, step, rec) => {
      rec.pages = {};
      for (const page of ["overview", "users", "profiles", "traffic", "backups", "logs", "agents", "settings"]) {
        await P.go(`#${page}`);
        step(page);
        rec.pages[page] = stray(P);
      }
      rec.tail = { tunnel: has(P, ".gx-head .gx-tunchip"), me: has(P, ".gx-head .gx-me") };
      // Окно «Скачать копию приложения на сервер» (Настройки → Копии приложений), копии ещё нет.
      await P.key("k", { metaKey: true });
      await P.type(".gx-cmd input", "Скачать копию приложения");
      await P.key("Enter", {}, ".gx-cmd input");
      rec.mirrorDialog = has(P, ".gx-dlg");
      rec.dialog = stray(P);
    });
  }

  // NC-09: «Страница подключения» в карточке человека. Карточка — из /api/users/anna, окна — .gx-dlg поверх неё.
  const ANNA = { name: "anna", label: "Анна", created: "2026-09-02T10:00:00Z", enabled: true, quota_bytes: 0, expires: "",
    traffic: { rx: 524288, tx: 1048576 }, profiles: ["awg3"], state: "on", online: false, last_handshake: 0,
    peers: { awg3: { ip: "10.8.3.3", pubkey: "PUBANNA3", created: "2026-09-02T10:00:00Z", active: true, has_key: true, line: "3.1" } } };
  const userApi = { "GET /api/users/anna": { status: 200, body: { ok: true, data: { user: ANNA } } } };
  const openAnnaPage = async (P) => {
    await P.click(P.$$(".gx-urow").find((n) => n.textContent.includes("Анна")));
    await P.click(P.byText("Страница подключения", ".gx-sheet button"));
  };
  const dlgInfo = (P) => {
    const d = P.$$(".gx-dlg").pop();
    if (!d) return null;
    const title = d.querySelector(".gx-dlg__title");
    return { title: title ? title.textContent : "", text: d.textContent, ttl: !!d.querySelector(".gx-ttlgrid"),
      foot: d.querySelectorAll(".gx-dlg__foot button").map((b) => b.textContent.trim()),
      alertBtns: d.querySelectorAll(".gx-al__btns button").map((b) => b.textContent.trim()) };
  };
  // Сайта на домене нет («Только VPN» фикстуры): отказ сразу, до срока; «Временная ссылка» ведёт в её окно.
  await scenario("page_no_domain", { hash: "#users", api: userApi }, async (P, step, rec) => {
    await openAnnaPage(P);
    rec.dlg = dlgInfo(P);
    rec.creates = P.calls("POST", "/api/action/page-create").length;
    await P.click(P.byText("Временная ссылка", ".gx-dlg__foot button"));
    rec.after = P.$$(".gx-dlg").map((d) => (d.querySelector(".gx-dlg__title") || { textContent: "" }).textContent);
  });
  // Сайт на домене есть — окно срока, как прежде. Отказ ядра (сертификат самоподписанный, а «Настройки» не открывали)
  // — те же слова и «Временная ссылка» прямо в окне.
  // Раздел «Пользователи» Обзор не грузит: режим и домен панель берёт из «Настроек» (они приходят при входе).
  await scenario("page_domain_refused", { hash: "#users", data: { "/api/settings": (d) => ({ ...d, config: { ...d.config, mode: "lite_site", domain: "vpn.example.com" } }) },
    api: { ...userApi, "POST /api/action/page-create": { status: 409, body: { ok: false, error: "no", code: "pages_need_domain" } } } }, async (P, step, rec) => {
    await openAnnaPage(P);
    rec.ttl = dlgInfo(P).ttl;
    await P.click(P.byText("Создать страницу", ".gx-dlg__foot button"));
    rec.dlg = dlgInfo(P);
    rec.creates = P.calls("POST", "/api/action/page-create").length;
  });

  // Подписка (docs/SUBSCRIPTION.md): блок в карточке рядом со страницами и тем же видом — создать (окно с объяснением),
  // адреса по форматам со «Скопировать», в мини-аппе на телефоне — те же кнопки; без сайта на домене — слова вместо кнопки.
  const SUB_BASE = "https://vpn.example.com/s-FAKESUBSECRETFAKESUBSECRETFAKESUBSECRET0123/";
  const SUB_URLS = [["mihomo.yaml", "mihomo", ""], ["awg31.conf", "conf", "3.1"], ["awg31.vpn", "vpn", "3.1"], ["all.vpn", "vpn", ""]]
    .map(([s, f, l]) => ({ suffix: s, format: f, line: l, url: SUB_BASE + s }));
  const SUB_REC = { name: "anna", label: "Анна", created: "2026-10-03T10:00:00Z", rotated: "", generation: 1, fetches: 3,
    last_fetch: "2026-10-03T12:00:00Z", last_suffix: "mihomo.yaml", last_client: "clash-verge", disabled: false,
    lines: [{ line: "3.1", profile: "awg3", has_key: true, active: true, token: "awg31" }] };
  const ok = (data) => ({ status: 200, body: { ok: true, data: { status: "success", message: "ok", ...data } } });
  const subApi = (subs, ready = true) => ({ ...userApi,
    "POST /api/action/sub-list": ok({ subs, count: subs.length, domain_ready: ready, update_interval: 12 }),
    "POST /api/action/sub-create": ok({ ...SUB_REC, fetches: 0, base_url: SUB_BASE, urls: SUB_URLS, new: true, paused: false, update_interval: 12 }),
    "POST /api/action/sub-show": ok({ ...SUB_REC, base_url: SUB_BASE, urls: SUB_URLS, new: false, paused: false, update_interval: 12 }) });
  const proSettings = { "/api/settings": (d) => ({ ...d, config: { ...d.config, mode: "pro", domain: "vpn.example.com", site: { ...d.config.site, cert: "letsencrypt" } } }) };
  const subInfo = (P) => {
    const b = P.$(".gx-usub");
    return b ? { text: b.textContent, btns: b.querySelectorAll("button").map((x) => x.textContent.trim()),
      cls: b.querySelectorAll("button").map((x) => x.getAttribute("class")) } : null;
  };
  await scenario("sub_create", { hash: "#users", data: proSettings, api: subApi([]) }, async (P, step, rec) => {
    await P.click(P.$$(".gx-urow").find((n) => n.textContent.includes("Анна")));
    rec.before = subInfo(P);
    await P.click(P.byText("Создать", ".gx-usub button"));
    rec.confirm = dlgInfo(P);
    await P.click(P.byText("Создать", ".gx-dlg__foot button"));
    rec.dlg = dlgInfo(P);
    rec.codes = P.$$(".gx-dlg .gx-sharelink__url").map((n) => n.textContent);
    rec.copies = P.$$(".gx-dlg .gx-sharelink button").length;
    rec.creates = P.calls("POST", "/api/action/sub-create").map((c) => c.body);
  });
  await scenario("sub_miniapp", { tg: true, width: 375, data: proSettings, api: subApi([SUB_REC]) }, async (P, step, rec) => {
    await P.click('#tabs button[data-goto="users"]');
    rec.device = P.doc.documentElement.getAttribute("data-device");
    // На телефоне список людей — карточками (.gx-ucard), а не строками таблицы
    await P.click(P.$$(".gx-ucard").find((n) => n.textContent.includes("Анна")));
    rec.box = subInfo(P);
    await P.click(P.byText("Показать адреса", ".gx-usub button"));
    rec.dlg = dlgInfo(P);
    rec.codes = P.$$(".gx-dlg .gx-sharelink__url").length;
    rec.signed = P.calls("POST", "/api/action/sub-show").every((c) => !!c.headers["X-Telegram-Init-Data"]);
  });
  await scenario("sub_no_domain", { hash: "#users", api: subApi([], false) }, async (P, step, rec) => {
    await P.click(P.$$(".gx-urow").find((n) => n.textContent.includes("Анна")));
    rec.box = subInfo(P);
  });

  // Выбор по клиенту (поле clients ответа, фикстура tests/fixtures/sub_clients.json): слева приложения по группам (записи
  // с одним app — одно приложение), справа выбранное — «Формат» по variant, линия, «QR-код» и «Скопировать» в ряд (по
  // полям qr, qr_alt и copy), под рядом — выбранное, «Открыть в приложении» строкой ниже, как добавить. QR адреса и
  // ссылки «в приложение» дорисовывает сервер панели (здесь — руками), QR и ключ линии — путь карточки (/api/users/anna/key).
  const SUB_FIX = require(require("path").join(require("path").dirname(process.argv[2]), "sub_clients.json"));
  const PNG1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==";
  const subFix = (patch) => {
    const r = JSON.parse(JSON.stringify(SUB_FIX));
    r.clients.forEach((c) => c.items.forEach((it) => {
      if (it.qr === "url" || it.qr === "deeplink") it.qr_png_b64 = PNG1;
      if (it.qr_alt === "url") it.qr_alt_png_b64 = PNG1;
    }));
    return patch ? patch(r) : r;
  };
  const KEY_CONF = "[Interface]\nPrivateKey = FAKEPRIVKEY\nAddress = 10.8.3.3/32\n\n[Peer]\nEndpoint = vpn.example.com:443\n";
  const subClientsApi = (fx) => ({ ...subApi([SUB_REC]), "POST /api/action/sub-show": ok(fx),
    "GET /api/users/anna/key": ok({ name: "anna", line: "3.1", min_client: "4.8.12.7", conf: KEY_CONF, vpn: "vpn://FAKEKEY",
      qr_conf_png_b64: PNG1, qr_vpn_png_b64: [PNG1, PNG1] }) });
  const subcInfo = (P) => {
    const d = P.$$(".gx-dlg").pop();
    const pane = d && d.querySelector(".gx-subc__pane");
    if (!pane) return null;
    const one = (sel, attr) => { const n = pane.querySelector(sel); return n ? (attr ? n.getAttribute(attr) : n.textContent) : null; };
    const segs = pane.querySelectorAll(".gx-seg-label");
    const seg = (label) => { const s = segs.find((n) => n.firstChild && n.firstChild.textContent === label); return s ? s.querySelectorAll("button").map((b) => `${b.textContent}:${b.getAttribute("aria-pressed")}`) : []; };
    return { title: (d.querySelector(".gx-sheet__title") || { textContent: "" }).textContent,
      groups: d.querySelectorAll(".gx-subc__g").map((n) => n.textContent),
      apps: d.querySelectorAll(".gx-subc__i").map((n) => n.querySelector(".gx-subc__n").textContent),
      plats: d.querySelectorAll(".gx-subc__i").map((n) => n.querySelector(".gx-subc__p").textContent),
      pressed: d.querySelectorAll('.gx-subc__i[aria-pressed="true"]').map((n) => n.querySelector(".gx-subc__n").textContent),
      name: one(".gx-subc__t"), chips: pane.querySelectorAll(".gx-chip").map((n) => n.textContent), meta: one(".gx-subc__pane > .gx-small"),
      hint: one(".gx-subc__pane > .gx-p"), formats: seg("Формат"), lines: seg("Линия"),
      trio: pane.querySelectorAll(".gx-subtri").map((b) => [b.getAttribute("data-mode"), b.querySelector("b").textContent, b.querySelector("small").textContent,
        b.hasAttribute("disabled") ? "off" : b.getAttribute("aria-pressed")]),
      value: one(".gx-subout .gx-subval"), guide: one(".gx-subout > .gx-small"), done: one(".gx-subout .gx-al"),
      qrs: pane.querySelectorAll(".gx-subout .gx-qrbox > span").map((n) => n.textContent), keyQr: one(".gx-subout .gx-qrcard__at"),
      keyPart: one(".gx-subout .gx-qrcard__nav .gx-num"), open: one(".gx-subopen__a", "href"), openText: one(".gx-subopen"), text: d.textContent };
  };
  const pickApp = (P, name) => P.click(P.$$(".gx-subc__i").find((n) => n.querySelector(".gx-subc__n").textContent === name));
  const pickSeg = (P, text) => P.click(P.$$(".gx-subc__pane .gx-seg button").find((b) => b.textContent === text));
  const way = (P, mode) => P.click(P.$(`.gx-subc__pane .gx-subtri[data-mode="${mode}"]`));
  await scenario("sub_clients", { hash: "#users", data: proSettings,
    api: subClientsApi(subFix((r) => { r.clients.find((c) => c.id === "wgtunnel").items[1].deeplink = "javascript://%0Aalert(1)"; return r; })) },
  async (P, step, rec) => {
    await P.click(P.$$(".gx-urow").find((n) => n.textContent.includes("Анна")));
    const cardKeys = P.calls("GET", "/api/users/anna/key").length;
    await P.click(P.byText("Показать адреса", ".gx-usub button"));
    rec.shows = P.calls("POST", "/api/action/sub-show").map((c) => c.body);
    rec.start = subcInfo(P);
    rec.oldList = P.$$(".gx-dlg .gx-field__l").map((n) => n.textContent).filter((s) => /Clash Verge Rev, FlClash/.test(s));
    await way(P, "qr"); rec.vergeQr = subcInfo(P);                    // неактивная кнопка не нажимается
    await way(P, "copy"); rec.vergeCopy = subcInfo(P);
    await pickApp(P, "FlClash"); rec.flclash = subcInfo(P);
    await way(P, "qr"); rec.flclashQr = subcInfo(P);
    await way(P, "qr"); rec.flclashQrOff = subcInfo(P);
    await pickApp(P, "Clash Meta for Android"); rec.meta = subcInfo(P);
    await pickApp(P, "incy"); await way(P, "qr"); rec.incy = subcInfo(P);
    await pickApp(P, "AmneziaVPN"); rec.amnezia = subcInfo(P);
    await way(P, "qr"); rec.amneziaQr = subcInfo(P);
    await pickSeg(P, "2.0"); rec.amnezia20 = subcInfo(P);
    await way(P, "copy"); rec.amneziaCopy = subcInfo(P);
    await pickApp(P, "AmneziaWG"); await way(P, "qr"); rec.awg = subcInfo(P);
    await way(P, "copy"); rec.awgCopy = subcInfo(P);
    rec.keyQueries = P.calls("GET", "/api/users/anna/key").slice(cardKeys).map((c) => c.query);
    await pickApp(P, "Keenetic / Netcraze"); rec.keenetic = subcInfo(P);
    await way(P, "copy"); rec.keeneticCopy = subcInfo(P);
    await pickApp(P, "WG Tunnel"); rec.wg = subcInfo(P);
    await pickSeg(P, "2.0"); rec.wg20 = subcInfo(P);
    await pickApp(P, "goVLESS, Kaskad, goTelegram"); rec.go = subcInfo(P);
    // AWG Manager: три записи с одним app — одно приложение и «Формат»
    await pickApp(P, "AWG Manager"); rec.awgm = subcInfo(P);
    await way(P, "copy"); rec.awgmCopy = subcInfo(P);
    await pickSeg(P, "Ключ vpn://"); rec.awgmVpn = subcInfo(P);
    await way(P, "copy"); rec.awgmVpnCopy = subcInfo(P);
    await pickSeg(P, "Файл .conf"); await pickSeg(P, "2.0"); await way(P, "copy"); rec.awgmConfCopy = subcInfo(P);
    rec.shows2 = P.calls("POST", "/api/action/sub-show").length;
    await P.click(P.byText("Готово", ".gx-dlg__foot button"));
    rec.after = P.$$(".gx-dlg").length;
  });
  // Ни одного рекомендованного — выбран первый в списке; на английском — свои подписи.
  await scenario("sub_clients_norec_en", { hash: "#users", lang: "en", data: proSettings,
    api: subClientsApi(subFix((r) => { r.clients.forEach((c) => { c.recommended = false; }); return r; })) }, async (P, step, rec) => {
    await P.click(P.$$(".gx-urow").find((n) => n.textContent.includes("Анна")));
    await P.click(P.byText("Show the addresses", ".gx-usub button"));
    rec.start = subcInfo(P);
  });
  // Мини-апп на телефоне: окно — листом снизу, приложения и ряд способов — те же, запросы подписаны.
  await scenario("sub_clients_miniapp", { tg: true, width: 375, data: proSettings, api: subClientsApi(subFix()) }, async (P, step, rec) => {
    await P.click('#tabs button[data-goto="users"]');
    await P.click(P.$$(".gx-ucard").find((n) => n.textContent.includes("Анна")));
    await P.click(P.byText("Показать адреса", ".gx-usub button"));
    rec.sheet = !!P.$(".gx-dlg-bd--sheet .gx-subcw");
    rec.start = subcInfo(P);
    await pickApp(P, "Keenetic / Netcraze");
    await way(P, "copy");
    rec.keenetic = subcInfo(P);
    rec.signed = P.calls("POST", "/api/action/sub-show").every((c) => !!c.headers["X-Telegram-Init-Data"]);
  });

  // NC-14: «Службы» на Обзоре — состояние в роде и числе имени службы; RU и EN.
  const svcRows = (P) => P.$$(".gx-svc").map((n) => `${n.querySelector(".gx-svc__n").textContent}: ${n.querySelector(".gx-svc__s").textContent}`);
  const services = { docker: "active", "goamnezia-bot": "not_installed", "goamnezia-admin": "inactive", "goamnezia-mcp-http": "not_installed", "goamnezia-pages": "inactive" };
  for (const lang of ["ru", "en"]) {
    await scenario(`services_${lang}`, { hash: "#overview", lang, data: { "/api/overview": (d) => ({ ...d, services }) } }, async (P, step, rec) => {
      rec.rows = svcRows(P);
    });
  }
  // NC-14: карта часов копится 2 дня. Профили поставлены тогда же — «после установки»; давно (обновление) — «после
  // обновления». Даты установки — в config.profiles из «Настроек».
  const DAY = 86400000;
  const heatData = (instAgo) => ({
    "/api/traffic": (d) => ({ ...d, hours: [{ hour: new Date(Date.now() - 2 * DAY).toISOString(), active: 1, rx: 10, tx: 20 }] }),
    "/api/settings": (d) => ({ ...d, config: { ...d.config, profiles: Object.fromEntries(Object.entries(d.config.profiles).map(([k, v]) => [k, { ...v, installed_at: new Date(Date.now() - instAgo).toISOString() }])) } }),
  });
  for (const [label, ago] of [["heat_new", 2 * DAY + 3600000], ["heat_upgrade", 60 * DAY]]) {
    await scenario(label, { hash: "#overview", data: heatData(ago) }, async (P, step, rec) => {
      const al = P.$(".gx-heatcard .gx-al");
      rec.note = al ? al.textContent : null;
    });
  }
  // NC-14: вкладки телефона — те же слова, что меню компьютера и заголовок раздела.
  for (const lang of ["ru", "en"]) {
    await scenario(`tabs_${lang}`, { hash: "#users", width: 390, lang }, async (P, step, rec) => {
      rec.tabs = P.$$("#tabs button").map((b) => b.textContent.trim());
      rec.title = (P.$(".gx-phead__title") || { textContent: "" }).textContent.trim();
      await P.click(P.$$("#tabs button").find((b) => !b.hasAttribute("data-goto")));
      rec.more = P.$$(".gx-more__item").map((b) => b.textContent.trim());
    });
  }
  await scenario("side_ru", { hash: "#users" }, async (P, step, rec) => {
    rec.side = P.$$("#side button[data-goto]").map((b) => b.textContent.trim()).filter(Boolean);
  });

  // OPS-01: место на диске и откат обновления на Обзоре (status.disk, status.update_rollback через /api/overview).
  const HOUR = 3600000;
  const DISK = (level, free, pct) => ({ path: "/opt/goamnezia", total_mb: 20000, used_mb: 20000 - free, free_mb: free, used_pct: pct,
    level, low_mb: 1024, critical_mb: 512 });
  const RB = (o = {}) => ({ at: new Date(Date.now() - HOUR).toISOString().replace(/\.\d{3}Z$/, "Z"), stage: "migration", reason: "",
    version: "0.5.0", failed_version: "0.5.1", code_restored: true, data_restored: true,
    archive: "/opt/goamnezia/backups/preupgrade_0.5.1_20260928-100000.tar.gz", ...o });
  const alerts = (P) => P.$$(".gx-ovalert").map((n) => ({
    title: n.querySelector(".gx-ovalert__t").textContent, text: (n.querySelector(".gx-ovalert__d") || { textContent: "" }).textContent,
    tone: (n.querySelector(".gx-al__ic").getAttribute("class").match(/gx-al__ic--(\w+)/) || [])[1], role: n.getAttribute("role"),
    btns: n.querySelectorAll(".gx-ovalert__btns button").map((b) => b.textContent.trim()) }));
  const ops = (disk, rollback) => ({ "/api/overview": (d) => ({ ...d, disk, update_rollback: rollback }) });
  for (const [label, disk, rollback, extra] of [
    ["ops_ok", DISK("ok", 12000, 40), null, {}],
    ["ops_null", null, null, {}],
    ["ops_low", DISK("low", 900, 96), null, {}],
    ["ops_critical", DISK("critical", 300, 99), null, {}],
    ["ops_low_en", DISK("low", 900, 96), null, { lang: "en" }],
    ["ops_rb_data", null, RB({ data_restored: false }), {}],
    ["ops_rb_stuck", null, RB({ code_restored: false, data_restored: false, stage: "check" }), {}],
    ["ops_rb_old", null, RB({ at: new Date(Date.now() - 8 * 24 * HOUR).toISOString().replace(/\.\d{3}Z$/, "Z") }), {}],
    ["ops_rb_en", null, RB(), { lang: "en" }],
  ]) {
    await scenario(label, { hash: "#overview", data: ops(disk, rollback), ...extra }, async (P, step, rec) => {
      rec.alerts = alerts(P);
    });
  }
  // Кнопки «мало места»: «Логи» и «Бэкапы» ведут в свои разделы.
  await scenario("ops_low_btns", { hash: "#overview", data: ops(DISK("low", 900, 96), null) }, async (P, step, rec) => {
    await P.click(P.byText("Бэкапы", ".gx-ovalert__btns button")); step("backups");
    await P.go("#overview");
    await P.click(P.byText("Логи", ".gx-ovalert__btns button")); step("logs");
  });
  // Откат: «Понятно» прячет плашку — и при переходах, и при автообновлении, и после перезагрузки (новый boot с тем же
  // localStorage); другой откат (другое время) — снова виден.
  const rb = RB();
  await scenario("ops_rb", { hash: "#overview", data: ops(DISK("ok", 12000, 40), rb) }, async (P, step, rec) => {
    rec.before = alerts(P);
    await P.click(P.byText("Понятно", ".gx-ovalert__btns button"));
    rec.after = alerts(P);
    await P.go("#users"); await P.go("#overview");
    await P.tick(25000);
    rec.later = alerts(P);
    rec.seen = P.win.localStorage.getItem("goamnezia-rollback-seen");
  });
  await scenario("ops_rb_reload", { hash: "#overview", data: ops(null, rb), localStorage: { "goamnezia-rollback-seen": `${rb.at}|${rb.failed_version}` } },
    async (P, step, rec) => { rec.alerts = alerts(P); });
  await scenario("ops_rb_new", { hash: "#overview", data: ops(null, RB({ at: new Date(Date.now() - 60000).toISOString().replace(/\.\d{3}Z$/, "Z") })),
    localStorage: { "goamnezia-rollback-seen": `${rb.at}|${rb.failed_version}` } }, async (P, step, rec) => { rec.alerts = alerts(P); });
  // Мини-апп Telegram на телефоне — тот же Обзор: обе плашки там же.
  await scenario("ops_miniapp", { tg: true, width: 390, data: ops(DISK("critical", 300, 99), RB()) }, async (P, step, rec) => {
    step("start");
    rec.alerts = alerts(P);
  });

  // DK-02: пакет Docker обновлён, а служба старая (status.docker_stale через /api/overview) — строка на Обзоре с кнопкой
  // «Перезапустить Docker»: вопрос с предупреждением «VPN пропадёт у всех», затем мост docker-restart (confirm — имя
  // действия), итог — тостом. Та же кнопка — в «Настройках → О программе» и в «Диагностике».
  const DKS = { reason: "version", binary_version: "29.1.3", binary_build: "29.1.3-0ubuntu3~24.04.2", daemon_version: "24.0.7",
    daemon_build: "24.0.7-0ubuntu4", client_api: "1.52", daemon_api: "1.43", since: "2026-10-01T06:12:00Z", retry_after: "", last_attempt: null };
  const DK_OK = () => ({ status: 200, body: { ok: true, data: { status: "success", message: "ok", duration_s: 9, restarted: ["awg3", "awg2"],
    failed: [], daemon_before: "24.0.7", daemon_after: "29.1.3", stale: false } } });
  const dkStale = (o = {}) => ({ "/api/overview": (d) => ({ ...d, docker_stale: { ...DKS, ...o } }) });
  const dkDialog = (P) => { const d = P.$$(".gx-dlg").pop(); return d ? { title: d.querySelector(".gx-dlg__title").textContent,
    foot: d.querySelectorAll(".gx-dlg__foot button").map((b) => b.textContent.trim()), text: d.textContent } : null; };
  const toastText = (P) => (P.$("#toastHost .gx-toast") || { textContent: "" }).textContent;
  // Подтвердить или отменить вопрос и снять, что ушло мосту.
  const dkAnswer = async (P, rec, label) => {
    rec.dialog = dkDialog(P);
    rec.before = P.calls("POST", "/api/action/docker-restart").length;
    await P.click(P.byText(label, ".gx-dlg__foot button"));
    rec.calls = P.calls("POST", "/api/action/docker-restart").map((c) => c.body);
    rec.toast = toastText(P);
  };
  await scenario("dk_overview", { hash: "#overview", data: dkStale(), api: { "POST /api/action/docker-restart": DK_OK } }, async (P, step, rec) => {
    rec.alerts = alerts(P);
    await P.click(P.byText("Перезапустить Docker", ".gx-ovalert__btns button"));
    await dkAnswer(P, rec, "Перезапустить");
  });
  await scenario("dk_overview_cancel", { hash: "#overview", data: dkStale(), api: { "POST /api/action/docker-restart": DK_OK } }, async (P, step, rec) => {
    await P.click(P.byText("Перезапустить Docker", ".gx-ovalert__btns button"));
    await dkAnswer(P, rec, "Отмена");
  });
  for (const [label, extra] of [["dk_overview_fresh", { hash: "#overview" }],
    ["dk_overview_failed", { hash: "#overview", data: dkStale({ retry_after: "2026-10-04T18:00:00Z",
      last_attempt: { at: "2026-10-04T12:00:00Z", ok: false, by: "watch", code: "docker_restart_timeout" } }) }],
    ["dk_overview_en", { hash: "#overview", lang: "en", data: dkStale() }],
    ["dk_overview_miniapp", { tg: true, width: 390, data: dkStale() }]]) {
    await scenario(label, extra, async (P, step, rec) => { rec.alerts = alerts(P); });
  }
  await scenario("dk_settings", { hash: "#settings", api: { "POST /api/action/docker-restart": DK_OK } }, async (P, step, rec) => {
    await P.click(P.byText("О программе", 'section[data-page="settings"] button'));
    const sec = P.$$('section[data-page="settings"] .gx-card').find((s) => s.querySelector(".gx-h2") && s.querySelector(".gx-h2").textContent.trim() === "Docker");
    rec.sec = sec ? { text: sec.textContent, btns: sec.querySelectorAll("button").map((b) => b.textContent.trim()) } : null;
    await P.click(P.byText("Перезапустить Docker", 'section[data-page="settings"] button'));
    await dkAnswer(P, rec, "Перезапустить");
  });
  const DK_QUALITY = { status: 200, body: { ok: true, data: { quality: { docker: true, ip_forward: "1", network: true, disk_free_mb: 12345,
    docker_info: { installed: true, reachable: true, client_version: "29.1.3", client_api: "1.52", daemon_version: "24.0.7", daemon_api: "1.43",
      mismatch: true, restart_required: true, stale: true, stale_reason: "version", binary_version: "29.1.3", stale_since: "2026-10-01T06:12:00Z" } } } } };
  await scenario("dk_diag", { hash: "#logs", api: { "GET /api/quality": DK_QUALITY, "POST /api/action/docker-restart": DK_OK } }, async (P, step, rec) => {
    await P.click(P.byText("Диагностика", 'section[data-page="logs"] button'));
    const row = P.$$(".gx-stat__row").find((r) => r.textContent.includes("служба старая"));
    rec.row = row ? { text: row.textContent, btns: row.querySelectorAll("button").map((b) => b.textContent.trim()), code: !!row.querySelector("code") } : null;
    if (row) await P.click(row.querySelector("button"));
    await dkAnswer(P, rec, "Перезапустить");
  });

  // Короткие ссылки с 2FA через бота (фикстура tests/fixtures/short_links.json, договор ядра и интерфейсов): каталог ядра с
  // short-* — правкой /api/actions, ответы моста — в форме договора (итог short-decide и short-status — полем status).
  const SHORT_FX = require(require("path").join(require("path").dirname(process.argv[2]), "short_links.json"));
  const withShortCatalog = (d) => ({ ...d, actions: { ...d.actions, ...SHORT_FX.catalog.actions }, param_types: { ...d.param_types, ...SHORT_FX.catalog.param_types } });
  // Ядро без коротких ссылок (старше SHORT-01): из настоящего каталога убраны short-*.
  const withoutShortCatalog = (d) => ({ ...d, actions: Object.fromEntries(Object.entries(d.actions || {}).filter(([k]) => !k.startsWith("short-"))) });
  const soon = (sec = 300) => new Date(Date.now() + sec * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
  const okData = (data) => ({ status: 200, body: { ok: true, data } });
  const shortInfo = (P) => {
    const d = P.$$(".gx-dlg").pop();
    const out = d && d.querySelector(".gx-subout");
    if (!out) return null;
    const one = (sel) => { const n = out.querySelector(sel); return n ? n.textContent : null; };
    return { plaque: one(".gx-sub2fa"), url: one(".gx-subshort__url"), cmd: one(".gx-subshort__cmd"), how: one(".gx-subshort__how"),
      copy: (out.querySelector(".gx-subshort__row button") || { textContent: null }).textContent,
      swap: out.querySelector(".gx-subshort__swap") ? out.querySelector(".gx-subshort__swap").getAttribute("aria-label") : null,
      num: one(".gx-subswap__num"), wait: one(".gx-subswap__left"), swapText: one(".gx-subswap"), al: one(".gx-al"), text: out.textContent };
  };
  const shortSubApi = (statusSeq) => {
    let polls = 0;
    return { ...subClientsApi(subFix((r) => ({ ...r, short_available: true }))),
      "POST /api/action/short-ensure": okData(SHORT_FX.actions["short-ensure"]),
      "POST /api/action/short-replace-request": () => okData({ ...SHORT_FX.actions["short-replace-request"], expires: soon() }),
      "POST /api/action/short-status": () => { const st = statusSeq[Math.min(polls, statusSeq.length - 1)]; polls += 1;
        return okData(st === "ok" ? { status: "ok", display: "Q8RT-W2KM", url: "https://vpn.example.com/Q8RT-W2KM", expires: soon() } : { status: st, expires: soon() }); } };
  };
  await scenario("short_sub", { hash: "#users", data: { ...proSettings, "/api/actions": withShortCatalog }, api: shortSubApi(["pending", "ok"]) },
    async (P, step, rec) => {
      await P.click(P.$$(".gx-urow").find((n) => n.textContent.includes("Анна")));
      await P.click(P.byText("Показать адреса", ".gx-usub button"));
      rec.start = subcInfo(P);
      await way(P, "short");
      rec.short = { ...shortInfo(P), trio: subcInfo(P).trio };
      await way(P, "short");
      rec.ensures = P.calls("POST", "/api/action/short-ensure").map((c) => c.body);
      // Роутер: команда с короткой ссылкой вместо полной
      await pickApp(P, "Keenetic / Netcraze");
      await way(P, "short");
      rec.keenetic = shortInfo(P);
      rec.ensures2 = P.calls("POST", "/api/action/short-ensure").map((c) => c.body);
      // ↻: запрос замены — число, затем опрос short-status до итога
      await P.click(P.$(".gx-subshort__swap"));
      rec.asked = P.calls("POST", "/api/action/short-replace-request").map((c) => c.body);
      rec.waiting = shortInfo(P);
      await P.tick(3000); await P.settle();
      rec.afterPending = shortInfo(P);
      await P.tick(3000); await P.settle();
      rec.done = shortInfo(P);
      rec.polls = P.calls("POST", "/api/action/short-status").map((c) => c.body);
      await P.click(P.byText("Готово", ".gx-dlg__foot button"));
      await P.tick(20000);
      rec.pollsAfterClose = P.calls("POST", "/api/action/short-status").length;
    });
  // Замена истекла, окно закрыли посреди ожидания — опрос кончается сам.
  await scenario("short_sub_close", { hash: "#users", data: { ...proSettings, "/api/actions": withShortCatalog }, api: shortSubApi(["pending"]) },
    async (P, step, rec) => {
      await P.click(P.$$(".gx-urow").find((n) => n.textContent.includes("Анна")));
      await P.click(P.byText("Показать адреса", ".gx-usub button"));
      await way(P, "short");
      await P.click(P.$(".gx-subshort__swap"));
      await P.tick(3000); await P.settle();
      rec.before = P.calls("POST", "/api/action/short-status").length;
      await P.click(P.byText("Готово", ".gx-dlg__foot button"));
      await P.tick(30000);
      rec.after = P.calls("POST", "/api/action/short-status").length;
    });
  // Без short_available (бот не настроен) или у ядра без short-ensure — третьей кнопки нет.
  await scenario("short_sub_off", { hash: "#users", data: { ...proSettings, "/api/actions": withShortCatalog }, api: subClientsApi(subFix()) },
    async (P, step, rec) => {
      await P.click(P.$$(".gx-urow").find((n) => n.textContent.includes("Анна")));
      await P.click(P.byText("Показать адреса", ".gx-usub button"));
      rec.trio = subcInfo(P).trio.map((x) => x[0]);
    });
  await scenario("short_sub_nocore", { hash: "#users", data: { ...proSettings, "/api/actions": withoutShortCatalog }, api: subClientsApi(subFix((r) => ({ ...r, short_available: true }))) },
    async (P, step, rec) => {
      await P.click(P.$$(".gx-urow").find((n) => n.textContent.includes("Анна")));
      await P.click(P.byText("Показать адреса", ".gx-usub button"));
      rec.trio = subcInfo(P).trio.map((x) => x[0]);
      rec.tabs = P.$$(".gx-utabs button").map((b) => b.textContent);
    });
  // «Пользователи → Короткие ссылки»: «Ждут подтверждения» с теми же кнопками, что в боте, и баны со снятием.
  const PENDING = { ...SHORT_FX.actions["short-pending"], requests: SHORT_FX.actions["short-pending"].requests.map((q) => ({ ...q, expires: soon() })) };
  const shortTabApi = () => ({
    "POST /api/action/short-pending": okData(PENDING),
    "POST /api/action/short-bans": okData(SHORT_FX.actions["short-bans"]),
    "POST /api/action/short-decide": okData({ status: "ok" }),
    "POST /api/action/short-unban": okData({ status: "success", message: "ok" }) });
  const reqInfo = (P) => P.$$(".gx-screq").map((b) => ({ kind: b.getAttribute("data-kind"), req: b.getAttribute("data-req"),
    title: b.querySelector(".gx-screq__h b").textContent, where: (b.querySelector(".gx-screq__where") || { textContent: "" }).textContent,
    meta: (b.querySelector(".gx-screq__meta") || { textContent: "" }).textContent, q: b.querySelector(".gx-screq__q").textContent,
    rows: [b.querySelectorAll(".gx-screq__n").length, b.querySelectorAll(".gx-screq__tail button").length],
    keys: b.querySelectorAll(".gx-screq__n").map((n) => n.textContent), tail: b.querySelectorAll(".gx-screq__tail button").map((n) => n.textContent),
    left: b.querySelector(".gx-screq__left").textContent }));
  await scenario("short_tab", { hash: "#users", data: { "/api/actions": withShortCatalog }, api: shortTabApi() }, async (P, step, rec) => {
    rec.tabs = P.$$(".gx-utabs button").map((b) => b.textContent);
    rec.callsBefore = P.calls("POST", "/api/action/short-pending").length;
    await P.click(P.byText("Короткие ссылки", ".gx-utabs button"));
    rec.reqs = reqInfo(P);
    rec.tabsAfter = P.$$(".gx-utabs button").map((b) => b.textContent);
    rec.cards = P.$$("#usersShort .gx-h2").map((n) => n.textContent);
    rec.desc = P.$("#shortPending .gx-desc").textContent;
    rec.bans = P.$$("#shortBans .gx-tbl__r").map((r) => r.querySelectorAll(".gx-tbl__t").map((x) => x.textContent));
    rec.bansText = P.$("#shortBans").textContent;
    await P.click(P.$$('.gx-screq[data-req="rqLogin01"] .gx-screq__n').find((b) => b.textContent === "47"));
    rec.decide = P.calls("POST", "/api/action/short-decide").map((c) => c.body);
    rec.toast = toastText(P);
    await P.click(P.$$('.gx-screq[data-req="rqSwap002"] .gx-screq__no')[0]);
    rec.deny = P.calls("POST", "/api/action/short-decide").map((c) => c.body).pop();
    await P.click(P.$$("#shortBans button").pop());
    rec.unban = P.calls("POST", "/api/action/short-unban").map((c) => c.body);
    // Автообновление раздела: ждущие — раз в 10 с, пока вкладка открыта; баны — нет
    const p0 = P.calls("POST", "/api/action/short-pending").length, b0 = P.calls("POST", "/api/action/short-bans").length;
    await P.tick(10500);
    rec.autoPending = P.calls("POST", "/api/action/short-pending").length - p0;
    rec.autoBans = P.calls("POST", "/api/action/short-bans").length - b0;
    await P.click(P.$$(".gx-utabs button").find((x) => x.textContent.startsWith("Пользователи")));
    const p1 = P.calls("POST", "/api/action/short-pending").length;
    await P.tick(25000);
    rec.offTab = P.calls("POST", "/api/action/short-pending").length - p1;
  });
  // SL-UX-01 (живая проверка на test1): быстрые действия коротких ссылок — без плашки «Выполняется… до 15 минут», даже
  // когда ответ медлит.
  {
    const pend = [];
    const hold = (m, p) => (m === "POST" && /^\/api\/action\/short-(decide|unban)$/.test(p) ? new Promise((r) => pend.push(r)) : null);
    await scenario("short_tab_quick", { hash: "#users", data: { "/api/actions": withShortCatalog }, api: shortTabApi(), hold }, async (P, step, rec) => {
      await P.click(P.byText("Короткие ссылки", ".gx-utabs button"));
      await P.click(P.$$('.gx-screq[data-req="rqLogin01"] .gx-screq__n').find((b) => b.textContent === "47"));
      await P.tick(1500);
      rec.decideHeld = pend.length;
      rec.decidePlaque = !P.$("#progress").hidden;
      while (pend.length) pend.shift()();
      await P.settle(); await P.tick(0);
      await P.click(P.$$("#shortBans button").pop());
      await P.tick(1500);
      rec.unbanHeld = pend.length;
      rec.unbanPlaque = !P.$("#progress").hidden;
      while (pend.length) pend.shift()();
      await P.settle(); await P.tick(0);
    });
  }
  // SL-UX-02: без бота (tfa сессии false: нет токена или администраторов) вкладки «Короткие ссылки» нет, мост не зовём.
  await scenario("short_tab_nobot", { hash: "#users", data: { "/api/actions": withShortCatalog, "/api/session": (d) => ({ ...d, tfa: false }) },
    api: shortTabApi() }, async (P, step, rec) => {
    rec.tabs = P.$$(".gx-utabs button").map((b) => b.textContent);
    await P.tick(10500);
    rec.pending = P.calls("POST", "/api/action/short-pending").length;
  });
  await scenario("short_tab_miniapp", { tg: true, width: 375, data: { "/api/actions": withShortCatalog }, api: shortTabApi() }, async (P, step, rec) => {
    await P.click('#tabs button[data-goto="users"]');
    await P.click(P.byText("Короткие", ".gx-utabs button"));      // на телефоне подпись короче (PNL-COS-03)
    rec.reqs = reqInfo(P).map((x) => x.rows);
    rec.signed = P.calls("POST", "/api/action/short-pending").every((c) => !!c.headers["X-Telegram-Init-Data"]);
  });
  // SL-UX-03 (настоящий бот на 168): отказ short-* — словами ядра, без служебного кода; слов нет — «не выполнено» и что
  // делать. Ответ панели на отказ моста — как у send_bridge: error — слова (или код, если слов нет), code, data.
  const shortRefusal = (code, message = "", status = 409) => ({ status, body: { ok: false, error: message || code, code,
    data: { status: "error", code, message } } });
  const PENDING_WORDS = "По этой короткой ссылке уже ждут подтверждения в боте — попробуйте через 5 мин";
  await scenario("short_err", { hash: "#users", data: { ...proSettings, "/api/actions": withShortCatalog },
    api: { ...shortSubApi(["pending"]),
      "POST /api/action/short-ensure": (body) => (body && body.suffix === "awg20.conf"
        ? shortRefusal("short_failed", "Действие не выполнено (short_failed).") : okData(SHORT_FX.actions["short-ensure"])),
      "POST /api/action/short-replace-request": shortRefusal("short_pending", PENDING_WORDS) } },
    async (P, step, rec) => {
      await P.click(P.$$(".gx-urow").find((n) => n.textContent.includes("Анна")));
      await P.click(P.byText("Показать адреса", ".gx-usub button"));
      await way(P, "short");
      await P.click(P.$(".gx-subshort__swap"));
      rec.swap = shortInfo(P).al;
      await pickApp(P, "Keenetic / Netcraze");
      await way(P, "short");
      rec.ensure = shortInfo(P).al;
    });
  await scenario("short_tab_err", { hash: "#users", data: { "/api/actions": withShortCatalog },
    api: { ...shortTabApi(), "POST /api/action/short-bans": shortRefusal("write_failed"),
      "POST /api/action/short-decide": shortRefusal("short_request_not_found",
        "Запрос подтверждения не найден: его не было или он закрыт больше часа назад", 404) } },
    async (P, step, rec) => {
      await P.click(P.byText("Короткие ссылки", ".gx-utabs button"));
      rec.bans = (P.$("#shortBans .gx-al") || { textContent: null }).textContent;
      await P.click(P.$$('.gx-screq[data-req="rqLogin01"] .gx-screq__n').find((b) => b.textContent === "47"));
      rec.toast = toastText(P);
    });
  // SL-UX-06 (живая проверка на 168): «↻ Заменить ссылку» у ждущего входа — новый код окном с «Скопировать», в тост код не
  // идёт; решено раньше в другом месте (already) — «Уже решено: …», без окна.
  await scenario("short_tab_swap", { hash: "#users", data: { "/api/actions": withShortCatalog },
    api: { ...shortTabApi(), "POST /api/action/short-decide": (body) => okData(body && body.answer === "swap"
      ? { status: "success", message: "ok", decision: "swapped", display: "Q8RT-W2KM", url: "https://vpn.example.com/Q8RT-W2KM" }
      : { status: "success", message: "ok", decision: "ok", already: true, by: "bot:424242", display: "Q8RT-W2KM",
          url: "https://vpn.example.com/Q8RT-W2KM" }) } },
    async (P, step, rec) => {
      await P.click(P.byText("Короткие ссылки", ".gx-utabs button"));
      await P.click(P.$('.gx-screq[data-req="rqLogin01"] .gx-screq__swap'));
      rec.sent = P.calls("POST", "/api/action/short-decide").map((c) => c.body);
      const d = P.$$(".gx-dlg").pop();
      rec.dlg = d ? { title: d.querySelector(".gx-dlg__title").textContent,
        url: (d.querySelector(".gx-subshort__url") || { textContent: null }).textContent,
        copy: d.querySelectorAll(".gx-subshort__row button").map((b) => b.textContent.trim()),
        foot: d.querySelectorAll(".gx-dlg__foot button").map((b) => b.textContent.trim()), text: d.textContent } : null;
      rec.toast = toastText(P);
      if (d) await P.click(P.byText("Готово", ".gx-dlg__foot button"));
      rec.closed = P.$$(".gx-dlg").length;
      await P.click(P.$('.gx-screq[data-req="rqSwap002"] .gx-screq__n'));
      rec.already = toastText(P);
      rec.dlgAfter = P.$$(".gx-dlg").length;
    });
  // PNL-COS-03 (живая проверка на 168): журналы — во времени сервера, остальная панель — браузера: подпись под журналом
  // говорит, по какому поясу (смещение сервера приходит с логами); тост — один узел, живой регион для экранного чтеца;
  // на телефоне три вкладки «Пользователей» — короткими подписями в одну строку.
  const logsWith = (extra) => ({ status: 200, body: { ok: true, data: { component: "bot", lines: 100,
    log: "Oct 05 12:34:56 vps goamnezia-bot[1]: INFO запуск\nOct 05 12:35:01 vps goamnezia-bot[1]: WARNING медленно\n", ...extra } } });
  for (const [label, extra] of [["logs_tz_msk", { tz_offset: 180 }], ["logs_tz_utc", { tz_offset: 0 }], ["logs_tz_none", {}]]) {
    await scenario(label, { hash: "#logs", api: { "GET /api/logs": logsWith(extra) } }, async (P, step, rec) => {
      await P.click(P.byText("Показать", 'section[data-page="logs"] button'));
      rec.lines = P.$$("#lgBox .gx-log__l").map((l) => l.querySelector("span").textContent);
      rec.foot = (P.$("#lgFoot") || { textContent: null }).textContent;
    });
  }
  await scenario("toast_once", { hash: "#users", data: { "/api/actions": withShortCatalog }, api: shortTabApi() }, async (P, step, rec) => {
    await P.click(P.byText("Короткие ссылки", ".gx-utabs button"));
    await P.click(P.$$('.gx-screq[data-req="rqLogin01"] .gx-screq__n').find((b) => b.textContent === "47"));
    await P.click(P.$('.gx-screq[data-req="rqSwap002"] .gx-screq__no'));
    rec.toasts = P.$$(".gx-toast").length;
    rec.hosts = P.$$("#toastHost").length;
    const node = P.$(".gx-toast");
    rec.live = node ? [node.getAttribute("role"), node.getAttribute("aria-live")] : null;
  });
  await scenario("users_tabs_phone", { hash: "#users", width: 375, data: { "/api/actions": withShortCatalog }, api: shortTabApi() },
    async (P, step, rec) => {
      rec.tabs = P.$$(".gx-utabs button").map((b) => [b.firstChild ? b.firstChild.textContent : "", b.getAttribute("title")]);
    });
  await scenario("users_tabs_wide", { hash: "#users", data: { "/api/actions": withShortCatalog }, api: shortTabApi() },
    async (P, step, rec) => {
      rec.tabs = P.$$(".gx-utabs button").map((b) => [b.firstChild ? b.firstChild.textContent : "", b.getAttribute("title")]);
    });
  // PERF-02 (живая проверка на 168): ждущие — своим кругом, не чаще раза в 10 с после ответа на прошлый опрос, только при
  // открытой вкладке и видимом окне; обновление раздела их не перечитывает; своё решение — один внеочередной опрос.
  await scenario("short_tab_poll", { hash: "#users", data: { "/api/actions": withShortCatalog }, api: shortTabApi() }, async (P, step, rec) => {
    const n = () => P.calls("POST", "/api/action/short-pending").length;
    await P.click(P.byText("Короткие ссылки", ".gx-utabs button"));
    rec.open = n();
    await P.tick(3000);
    await P.click('#side button[data-goto="overview"]');
    await P.click('#side button[data-goto="users"]');
    rec.back = n();
    rec.reqsBack = P.$$(".gx-screq").length;
    await P.tick(5000);
    rec.before10 = n();
    await P.tick(3000);
    rec.after10 = n();
    P.doc.hidden = true;
    P.doc.visibilityState = "hidden";
    await P.tick(35000);
    rec.hidden = n();
    P.doc.hidden = false;
    P.doc.visibilityState = "visible";
    await P.tick(10500);
    rec.shown = n();
    await P.click(P.$$('.gx-screq[data-req="rqLogin01"] .gx-screq__n').find((b) => b.textContent === "47"));
    rec.decided = n();
    await P.tick(9000);
    rec.decided9 = n();
    await P.tick(2000);
    rec.decided11 = n();
  });

  // PERF-03: холодный Обзор — рукопожатия и учёт ещё собираются на сервере (users_online и traffic_total — null,
  // pending; /api/traffic?wait=0 — pending): плитки пишут прочерк, а не «0 на связи» и «0 Б», и Обзор переспрашивает
  // через 1,5 с, а не через 10.
  {
    let ovN = 0;
    let trN = 0;
    const data = {
      "/api/overview": (d) => (ovN++ === 0 ? { ...d, users_online: null, traffic_total: null, pending: ["traffic", "edge-status"] } : d),
      "/api/traffic": (d) => (trN++ === 0 ? { profiles: [], users: [], pending: true, error: null } : d),
    };
    await scenario("overview_pending", { hash: "#overview", data }, async (P, step, rec) => {
      const kpi = (i, sel) => { const k = P.$$(".gx-kpi")[i]; const n = k && k.querySelector(sel); return n ? n.textContent.trim() : null; };
      const snap = () => ({ online: kpi(0, ".gx-chip"), traffic: kpi(1, ".gx-kpi__v"), overview: P.calls("GET", "/api/overview").length,
                            trafficQuery: P.calls("GET", "/api/traffic").map((c) => c.query) });
      rec.first = snap();
      await P.tick(2600);
      rec.quick = snap();
      await P.tick(5000);
      rec.settled = snap();
      rec.kpiLabels = P.$$(".gx-kpi__h").map((h) => [h.children[0].textContent, h.children[0].getAttribute("title")]);
    });
  }

  // PERF-04 (168: холодный Обзор в браузере 4,2–4,5 с — рядом с первым /api/overview шли трафик, «Настройки» и медленные
  // части, и на 1 vCPU они делили ядро): пока /api/overview не ответил, ничего тяжёлого страница не просит.
  {
    let release = null;
    const gate = new Promise((resolve) => { release = resolve; });
    let held = true;
    await scenario("overview_order", { hash: "#overview", hold: (m, p) => (held && m === "GET" && p === "/api/overview" ? gate : null) },
      async (P, step, rec) => {
        const log = () => P.fetchLog.map((c) => `${c.method} ${c.path}${c.query || ""}`);
        await P.tick(1500);
        rec.before = log();
        rec.frameBefore = !!P.$(".gx-kpi");
        held = false;
        release();
        await P.settle();
        await P.tick(50);
        rec.after = log();
        rec.frame = !!P.$(".gx-kpi");
      });
  }
  // PNL-05 (168: на первом входе плашка «Выполняется: последняя копия» 0,65–2,7 с): каталог действий ещё не пришёл —
  // backup-last не зовётся и плашки нет; пришёл — зовётся как чтение, без плашки.
  {
    let releaseCatalog = null;
    let releaseBackup = null;
    const catalog = new Promise((resolve) => { releaseCatalog = resolve; });
    const backup = new Promise((resolve) => { releaseBackup = resolve; });
    // Каталог и ответ backup-last (на 168 — 2 с) держит тест: плашка «Выполняется» видна, только пока действие идёт.
    const hold = (m, p) => (m === "GET" && p === "/api/actions" ? catalog : (m === "POST" && p === "/api/action/backup-last" ? backup : null));
    await scenario("overview_catalog_late", { hash: "#overview", hold }, async (P, step, rec) => {
      let busy = 0;
      const probe = () => { const b = P.$("#progress"); if (b && !b.hidden) busy += 1; };
      const posts = () => P.fetchLog.filter((c) => c.method === "POST").map((c) => c.path);
      for (let i = 0; i < 30; i += 1) { await P.tick(100); probe(); }
      rec.before = posts();
      releaseCatalog();
      await P.settle();
      for (let i = 0; i < 30; i += 1) { await P.tick(100); probe(); }
      rec.after = posts();
      releaseBackup();
      await P.settle();
      await P.tick(50);
      rec.busy = busy;
    });
  }
  // Мини-приложение — тот же app.js: холодный Обзор в Telegram на телефоне — тот же порядок и тот же прочерк с переспросом.
  {
    let ovN = 0;
    let trN = 0;
    const data = {
      "/api/overview": (d) => (ovN++ === 0 ? { ...d, users_online: null, traffic_total: null, pending: ["traffic"] } : d),
      "/api/traffic": (d) => (trN++ === 0 ? { profiles: [], users: [], pending: true, error: null } : d),
    };
    let release = null;
    const gate = new Promise((resolve) => { release = resolve; });
    let held = true;
    await scenario("overview_miniapp", { tg: true, width: 390, data, hold: (m, p) => (held && m === "GET" && p === "/api/overview" ? gate : null) },
      async (P, step, rec) => {
        const log = () => P.fetchLog.map((c) => `${c.method} ${c.path}${c.query || ""}`);
        await P.tick(1000);
        rec.page = P.page();
        rec.before = log();
        held = false;
        release();
        await P.settle();
        await P.tick(50);
        rec.first = (P.$(".gx-kpi .gx-chip") || { textContent: "" }).textContent.trim();
        await P.tick(1600);
        rec.quick = (P.$(".gx-kpi .gx-chip") || { textContent: "" }).textContent.trim();
        rec.after = log();
      });
  }
  // PNL-05: переспрос Обзора пришёлся на паузу (скрытая вкладка, окно, идущее действие) — он повторяется через секунду, а
  // не ждёт обычного круга в 10 с.
  {
    let ovN = 0;
    const data = {
      "/api/overview": (d) => (ovN++ === 0 ? { ...d, users_online: null, traffic_total: null, pending: ["traffic"] } : d),
      "/api/traffic": (d) => (ovN <= 1 ? { profiles: [], users: [], pending: true, error: null } : d),
    };
    await scenario("overview_quick_paused", { hash: "#overview", data }, async (P, step, rec) => {
      const n = () => P.calls("GET", "/api/overview").length;
      P.doc.hidden = true;
      P.doc.visibilityState = "hidden";
      await P.tick(3000);
      rec.hidden = n();
      P.doc.hidden = false;
      P.doc.visibilityState = "visible";
      await P.tick(1100);
      rec.shown = n();
      rec.online = (P.$(".gx-kpi .gx-chip") || { textContent: "" }).textContent.trim();
    });
  }

  // Ответ сервера по просьбе теста: gate() — новое обещание, release() — ответить.
  const makeGate = () => {
    let r = null;
    const g = { open: false };
    g.p = new Promise((resolve) => { r = resolve; });
    g.release = () => { g.open = true; r(); };
    return g;
  };
  const kpiTile = (P, i) => {
    const k = P.$$(".gx-kpi")[i];
    if (!k) return null;
    const h = k.querySelector(".gx-kpi__h span");
    const v = k.querySelector(".gx-kpi__v");
    return { label: h ? h.textContent : null, value: v ? v.textContent : null, bars: k.querySelectorAll(".gx-kbars span").length };
  };
  const chipText = (P) => (P.$(".gx-kpi .gx-chip") || { textContent: "" }).textContent.trim();
  // PNL-07 (168: на первом кадре «Трафик за всё время», через 0,35 с — «за 30 дней», и счёт от нуля при известном
  // числе): плитку целиком (период, сумма, столбики) сервер отдаёт в /api/overview (traffic_kpi) — подпись, число и
  // столбики первого кадра те же, что после прихода /api/traffic.
  {
    const days = [{ date: "2026-10-03", rx: 524288, tx: 1048576 }, { date: "2026-10-04", rx: 524288, tx: 1048576 },
                  { date: "2026-10-05", rx: 524288, tx: 1048576 }];
    const data = {
      "/api/overview": (d) => ({ ...d, traffic_kpi: { period: "30d", rx: 1572864, tx: 3145728, days, at: new Date().toISOString() } }),
      "/api/traffic": (d) => ({ ...d, days }),
    };
    const g = makeGate();
    await scenario("overview_label_steady", { hash: "#overview", data, hold: (m, p) => (!g.open && m === "GET" && p === "/api/traffic" ? g.p : null) },
      async (P, step, rec) => {
        rec.first = kpiTile(P, 1);
        await P.tick(100);
        rec.firstLater = kpiTile(P, 1);
        rec.trafficAsked = P.calls("GET", "/api/traffic").length;
        g.release();
        await P.settle();
        await P.tick(50);
        rec.after = kpiTile(P, 1);
      });
  }
  // Пока учёт собирается — подпись того периода, что был в прошлый раз (а не «за всё время» до прихода учёта); пришёл
  // другой период — он и запоминается.
  {
    let ovN = 0;
    const data = { "/api/overview": (d) => (ovN++ === 0 ? { ...d, users_online: null, traffic_total: null, pending: ["traffic"] }
      : { ...d, traffic_kpi: { period: "30d", rx: 1572864, tx: 3145728, days: [], at: new Date().toISOString() } }) };
    await scenario("overview_label_pending", { hash: "#overview", data, localStorage: { "goamnezia-ov-period": "month" } }, async (P, step, rec) => {
      rec.first = kpiTile(P, 1);
      await P.tick(2600);
      rec.later = kpiTile(P, 1);
      rec.storedPeriod = P.win.localStorage.getItem("goamnezia-ov-period");
    });
  }
  // PNL-07: повторный вход на Обзор — числа, которые уже показывали, встают сразу, без счёта от нуля (ответ сервера
  // держим, чтобы видеть кадр перехода — прежнюю картину).
  {
    let g = null;
    await scenario("overview_reentry", { hash: "#overview", hold: (m, p) => (g && !g.open && m === "GET" && p === "/api/overview" ? g.p : null) },
      async (P, step, rec) => {
        const vals = () => P.$$(".gx-kpi__v").slice(0, 2).map((n) => n.textContent);
        await P.tick(2000);
        rec.before = vals();
        await P.go("#users");
        await P.settle();
        await P.tick(1000);
        g = makeGate();
        await P.go("#overview");
        rec.back = vals();
        await P.tick(48);
        rec.back48 = vals();
        g.release();
        await P.settle();
        await P.tick(50);
        rec.after = vals();
      });
  }
  // PNL-07: первое значение плитки после прочерка — со счётом от нуля; перерисовка посреди счёта (пришёл учёт) его
  // продолжает с того же места — не с нуля и не прыжком к концу.
  {
    let ovN = 0;
    let trN = 0;
    const data = {
      "/api/overview": (d) => (ovN++ === 0 ? { ...d, users_online: null, traffic_total: null, pending: ["traffic"] }
        : { ...d, traffic_kpi: { period: "all", rx: d.traffic_total.rx, tx: d.traffic_total.tx, days: [], at: new Date().toISOString() } }),
      "/api/traffic": (d) => (trN++ === 0 ? { profiles: [], users: [], pending: true, error: null } : d),
    };
    const g = makeGate();
    let trAsk = 0;
    const hold = (m, p) => { if (m === "GET" && p === "/api/traffic") { trAsk += 1; if (trAsk === 2) return g.p; } return null; };
    await scenario("overview_reveal", { hash: "#overview", data, hold }, async (P, step, rec) => {
      const val = () => (P.$$(".gx-kpi__v")[1] || { textContent: "" }).textContent;
      rec.first = val();
      for (let i = 0; i < 40 && val() === "—"; i += 1) await P.tick(50);
      rec.revealAsk = P.calls("GET", "/api/overview").length;
      await P.tick(200);
      rec.mid = val();
      g.release();
      await P.settle();
      rec.afterRedraw = val();
      await P.tick(1000);
      rec.final = val();
    });
  }
  // PNL-08 (168: «Пользователи → Обзор» после простоя — чип сразу «0 на связи» из прежнего ответа, ~2 с): прежняя
  // картина при переходе — сразу, но «на связи» и лица «в сети» из ответа старше 45 с — «—», пока не придёт новый.
  {
    let g = null;
    await scenario("overview_back_after_idle", { hash: "#overview", hold: (m, p) => (g && !g.open && m === "GET" && p === "/api/overview" ? g.p : null) },
      async (P, step, rec) => {
        const avatars = () => P.$$(".gx-avs span").length;
        await P.tick(2000);
        rec.start = [chipText(P), avatars()];
        await P.go("#users");
        await P.settle();
        await P.tick(20000);
        g = makeGate();
        await P.go("#overview");
        rec.soon = [chipText(P), avatars()];
        g.release();
        await P.settle();
        await P.tick(50);
        await P.go("#users");
        await P.settle();
        await P.tick(60000);
        g = makeGate();
        await P.go("#overview");
        rec.idle = [chipText(P), avatars()];
        g.release();
        await P.settle();
        await P.tick(50);
        rec.fresh = [chipText(P), avatars()];
      });
  }
  // PNL-08: ноль «на связи» от сервера — это ноль, а не число людей «в сети» из прежнего учёта трафика.
  await scenario("overview_online_zero", { hash: "#overview", data: { "/api/overview": (d) => ({ ...d, users_online: 0 }) } }, async (P, step, rec) => {
    await P.tick(100);
    rec.chip = chipText(P);
    rec.avatars = P.$$(".gx-avs span").length;
  });

  // AWG31-01: место профиля — не линия. Приложение Amnezia держит 3.1 и в amnezia-awg2, и тогда 2.0 встаёт в awg3
  // (LZ-02). Вкладки ключей в карточке, метки в списке, выбор временной ссылки и «кому он» при добавлении подписываются
  // фактической линией: из config.json («Настройки» приходят при входе) или из поля line у пира (сервер панели).
  const SWAP = { awg2: "3.1", awg3: "2.0" };
  const swapList = (list) => list.map((p) => ({ ...p, line: SWAP[p.profile] || p.line }));
  const swapped = {
    "/api/settings": (d) => ({ ...d, config: { ...d.config, profiles: Object.fromEntries(Object.entries(d.config.profiles)
      .map(([k, v]) => [k, { ...v, line: SWAP[k] || v.line }])) } }),
    "/api/profiles": (d) => ({ ...d, installed: swapList(d.installed) }),
    "/api/overview": (d) => ({ ...d, profiles: swapList(d.profiles) }) };
  const adminPeers = (withLine) => ({
    awg3: { ip: "10.8.3.2", pubkey: "PUBADMIN3", created: "2026-09-01T10:00:00Z", active: true, has_key: true, ...(withLine ? { line: "2.0" } : {}) },
    awg2: { ip: "10.8.2.2", pubkey: "PUBADMIN2", created: "2026-09-01T10:00:00Z", active: true, has_key: true, ...(withLine ? { line: "3.1" } : {}) } });
  const adminApi = (withLine) => ({
    "GET /api/users/admin": { status: 200, body: { ok: true, data: { user: { name: "admin", label: "", created: "2026-09-01T10:00:00Z",
      enabled: true, quota_bytes: 0, expires: "", traffic: { rx: 0, tx: 0 }, profiles: ["awg3", "awg2"], state: "on", online: false,
      last_handshake: 0, peers: adminPeers(withLine) } } } },
    "GET /api/users/admin/key": ok({ name: "admin", line: "2.0", min_client: "4.8.12.7", conf: KEY_CONF, vpn: "vpn://FAKEKEY",
      qr_conf_png_b64: PNG1, qr_vpn_png_b64: [PNG1] }) });
  const adminRow = (P) => P.$$(".gx-urow").find((n) => n.textContent.includes("admin"));
  const cardTabs = (P) => P.$$('.gx-sheet [role="tab"]').map((b) => b.textContent);
  await scenario("lines_swapped", { hash: "#users", data: swapped, api: adminApi(false) }, async (P, step, rec) => {
    await P.click(adminRow(P));
    rec.tabs = cardTabs(P);
    await P.click(P.byText("Временная ссылка", ".gx-sheet button"));
    rec.share = P.$$(".gx-dlg .gx-ptabs > button").map((b) => b.textContent);
    await P.key("Escape");
    await P.click(P.byText("Добавить", 'section[data-page="users"] button'));
    rec.add = P.$$(".gx-sheet .gx-checkcard").map((b) => [b.querySelector(".gx-radio__t").textContent, b.querySelector(".gx-radio__d").textContent]);
  });
  // Линию пира кладёт сервер панели (поле line из config.json) — список и вкладки берут её, даже если «Настройки» линий
  // не несут: метки списка рисуются по /api/users, не дожидаясь «Настроек».
  const withPeerLines = (d) => ({ ...d, users: d.users.map((u) => ({ ...u, peers: Object.fromEntries(Object.entries(u.peers || {})
    .map(([k, v]) => [k, { ...v, line: SWAP[k] || v.line }])) })) });
  await scenario("lines_peer", { hash: "#users", data: { "/api/settings": (d) => ({ ...d, config: { ...d.config, profiles: {} } }),
    "/api/users": withPeerLines }, api: adminApi(true) }, async (P, step, rec) => {
    rec.tags = adminRow(P) ? adminRow(P).querySelectorAll(".ga-tag").map((n) => n.textContent) : null;
    await P.click(adminRow(P));
    rec.tabs = cardTabs(P);
  });
  // Мини-апп Telegram на телефоне: тот же app.js, но список людей — карточками, вкладки ключей — первым экраном.
  await scenario("lines_miniapp", { tg: true, width: 390, data: { ...swapped, "/api/users": withPeerLines }, api: adminApi(true) },
    async (P, step, rec) => {
      await P.click('#tabs button[data-goto="users"]');
      rec.device = P.doc.documentElement.getAttribute("data-device");
      await P.click(P.$$(".gx-ucard").find((n) => n.textContent.includes("admin")));
      rec.tabs = cardTabs(P);
      rec.signed = P.calls("GET", "/api/users/admin").every((c) => !!c.headers["X-Telegram-Init-Data"]);
    });

  // MINIAPP-TABS-01 / MINIAPP-QR-01 (владелец 16.10, телефон): мини-апп 320 px, awg2 подхвачен у приложения. Вкладки — версия
  // первой строкой, «порт N · из приложения» — второй, ничего не обрезано; QR vpn:// из трёх частей сменяется сам по
  // кругу раз в 1,5 с, стрелки остаются, карточку закрыли — смена остановилась.
  const importedAwg2 = (list) => list.map((p) => (p.profile === "awg2" ? { ...p, imported: true } : p));
  await scenario("tabs_qr_miniapp_320", { tg: true, width: 320, data: { ...swapped, "/api/users": withPeerLines,
    "/api/profiles": (d) => ({ ...d, installed: importedAwg2(swapList(d.installed)) }),
    "/api/overview": (d) => ({ ...d, profiles: importedAwg2(swapList(d.profiles)) }) },
    api: { ...adminApi(true), "GET /api/users/admin/key": ok({ name: "admin", line: "2.0", min_client: "4.8.12.7", conf: KEY_CONF,
      vpn: "vpn://FAKEKEY", qr_conf_png_b64: PNG1, qr_vpn_png_b64: [PNG1, PNG1, PNG1] }) } },
  async (P, step, rec) => {
    await P.click('#tabs button[data-goto="users"]');
    await P.click(P.$$(".gx-ucard").find((n) => n.textContent.includes("admin")));
    const list = P.$(".gx-sheet .gx-ptabs");
    rec.tablist = list ? [list.getAttribute("role"), list.getAttribute("aria-label")] : null;
    rec.slideTabs = P.$$(".gx-sheet .gx-slide [role=\"tab\"]").length;
    rec.tabParts = P.$$('.gx-sheet [role="tab"]').map((b) => [(b.querySelector(".gx-ptab__t") || {}).textContent || null,
      (b.querySelector(".gx-ptab__s") || {}).textContent || null, b.getAttribute("aria-label"), b.getAttribute("aria-selected")]);
    const part = () => { const n = P.$(".gx-sheet .gx-qrcard__nav .gx-num"); return n ? n.textContent : null; };
    const vpnBtn = P.$$(".gx-sheet button").find((b) => b.textContent === "QR vpn:// · AmneziaVPN");
    if (vpnBtn) await P.click(vpnBtn);
    await P.settle();
    rec.parts = [part()];
    await P.tick(1500); rec.parts.push(part());
    await P.tick(1500); rec.parts.push(part());
    await P.tick(1500); rec.parts.push(part());                              // по кругу — снова первая
    await P.click(P.$('.gx-sheet .gx-qrnav[aria-label="Предыдущая часть"]')); rec.parts.push(part());
    await P.tick(1000); rec.parts.push(part());                              // после стрелки — полный шаг
    await P.tick(500); rec.parts.push(part());
    const tab2 = P.$$('.gx-sheet [role="tab"]')[1];
    if (tab2) await P.click(tab2);
    await P.settle();
    rec.afterTab = [P.$$('.gx-sheet [role="tab"]').map((b) => b.getAttribute("aria-selected")), part()];
    // карточку закрыли — смена частей остановилась: подпись снятой карточки больше не меняется (RB3-2)
    const tab1 = P.$$('.gx-sheet [role="tab"]')[0];
    if (tab1) await P.click(tab1);
    const vpnBtn1 = P.$$(".gx-sheet button").find((b) => b.textContent === "QR vpn:// · AmneziaVPN");
    if (vpnBtn1) await P.click(vpnBtn1);
    await P.settle();
    const cap = P.$(".gx-sheet .gx-qrcard__nav .gx-num");
    await P.tick(1500);
    const capBefore = cap ? cap.textContent : null;
    const closeBtn = P.$(".gx-sheet .gx-close");
    if (closeBtn) await P.click(closeBtn);
    await P.settle();
    const afterClose = [!!P.$(".gx-sheet"), cap ? cap.isConnected : null, cap ? cap.textContent : null];
    const later = [];                                     // по шагу: за три шага круг из трёх частей замкнулся бы
    for (let i = 0; i < 3; i++) { await P.tick(1500); later.push(cap ? cap.textContent : null); }
    rec.closed = { capBefore, afterClose, later };
  });

  // SUBNET-01: приложение Amnezia ставит свои контейнеры AmneziaWG на одну 10.8.1.0/24, и подхват берёт их как есть. awg1 —
  // старый amnezia-awg на wg0 (подсеть не сменить), awg2 — amnezia-awg2 приложения, у двоих ключи из приложения. Ядро
  // (subnet_overlaps в profiles-status) называет выход — перенести awg2 на свободную 10.8.2.0/24.
  const OVL_MV = { profile: "awg2", subnet: "10.8.2.0/24", keyless: 2 };
  const OVL = [
    { profile: "awg1", subnet: "10.8.1.0/24", with: ["awg2"], fixed: true, move: OVL_MV },
    { profile: "awg2", subnet: "10.8.1.0/24", with: ["awg1"], fixed: false, move: OVL_MV }];
  const ovlData = { "/api/profiles": (d) => ({ ...d, not_installed: [], subnet_overlaps: OVL, installed: [
    d.installed[0], { ...d.installed[1], subnet: "10.8.1.0/24", peers: 2, preset: "imported", subnet_fixed: false },
    { profile: "awg1", container: "amnezia-awg", state: "running", port: 51820, line: "1.0", peers: 1, image_tag: "latest", image_digest: "",
      enabled: true, subnet: "10.8.1.0/24", mtu: "", preset: "imported", iface: "wg0", installed: true, subnet_fixed: true }] }) };
  // Мост: без согласия за людей приложения — adopted_keyless (как у ядра), с ним — успех.
  const ovlApi = () => ({ "POST /api/action/profile-subnet": (body) => (body && body.adopted_confirm === "awg2"
    ? { status: 200, body: { ok: true, data: { status: "success", message: "ok", profile: "awg2", subnet: body.subnet } } }
    : { status: 409, body: { ok: false, code: "adopted_keyless", error: "Профиль awg2 подхвачен у приложения Amnezia, и у пользователей на нём (2) ключи выдало приложение." } }) });
  const ovlCard = (P, pid) => P.$(`.gx-pcard[data-profile="${pid}"]`);
  const ovlDlg = (P) => { const d = P.$$(".gx-dlg").pop(); return d ? { title: (d.querySelector(".gx-dlg__title") || { textContent: "" }).textContent, text: d.textContent,
    foot: d.querySelectorAll(".gx-dlg__foot button").map((b) => b.textContent.trim()), input: (d.querySelector("input") || { value: null }).value } : null; };
  const ovlType = async (P, word) => { const d = P.$$(".gx-dlg").pop(); const inputs = d ? d.querySelectorAll("input") : []; await P.type(inputs[inputs.length - 1], word); };
  await scenario("subnet_ovl_cards", { hash: "#profiles", data: ovlData, api: ovlApi() }, async (P, step, rec) => {
    await P.tick(100);
    rec.cards = Object.fromEntries(["awg1", "awg2", "awg3"].map((pid) => { const c = ovlCard(P, pid);
      return [pid, c ? { text: c.textContent, btns: c.querySelectorAll("button").map((b) => b.textContent.trim()) } : null]; }));
    // Окно «Подсеть» у старого awg1 — отказ сразу (без поля для подсети) и та же кнопка выхода.
    await P.click(P.byText("Подсеть", '.gx-pcard[data-profile="awg1"] button'));
    rec.fixedDlg = ovlDlg(P);
    // Кнопка выхода: окно с набором имени, отказ ядра adopted_keyless не нужен — согласие за людей приложения в том же слове.
    await P.click(P.byText("Перенести AmneziaWG 2.0 · порт 39001 на 10.8.2.0/24", ".gx-dlg__foot button"));
    rec.moveDlg = ovlDlg(P);
    await ovlType(P, "39001");
    await P.click(P.byText("Сменить подсеть", ".gx-dlg__foot button"));
    rec.calls = P.calls("POST", "/api/action/profile-subnet").map((c) => c.body);
  });
  // Окно «Подсеть» у переносимого awg2: свободная подставлена; без adopted_confirm ядро отвечает adopted_keyless — второе
  // окно со словами ядра, набранное имя — повтор с согласием.
  await scenario("subnet_ovl_dialog", { hash: "#profiles", data: ovlData, api: ovlApi() }, async (P, step, rec) => {
    await P.tick(100);
    await P.click(P.byText("Подсеть", '.gx-pcard[data-profile="awg2"] button'));
    rec.dlg = ovlDlg(P);
    await ovlType(P, "39001");
    await P.click(P.byText("Сменить подсеть", ".gx-dlg__foot button"));
    rec.keyless = ovlDlg(P);
    await ovlType(P, "39001");
    await P.click(P.byText("Сменить всё равно", ".gx-dlg__foot button"));
    rec.calls = P.calls("POST", "/api/action/profile-subnet").map((c) => c.body);
  });

  // S4-FIX (MTU-01): выданный профиль 2.0 с большим S4 — у профиля packet_risk (ядро: при стандартном MTU 1280 пакет
  // 1280 + 80 + S4 больше 1400). Карточка — что не так и «Исправить S4»; «Обзор» (и мини-апп) — строка с той же кнопкой;
  // окно — что будет; у людей приложения — набор имени (adopted_confirm); итог — слова ядра: кому что делать.
  const S4R = { packet: 1502, budget: 1400, s4: 142, safe_s4: 40, mtu: 1280, keyless: 0 };
  const S4_STEPS = [{ kind: "restart", text: "Профиль перезапущен: клиенты переподключатся сами, обычно за 15–25 секунд." },
    { kind: "subscription", text: "С подпиской (vera): обновится сама в течение 12 часов или по «Обновить» в приложении." },
    { kind: "key", text: "Выдавали ключ или файл (anna): импортировать заново — выдайте ключ vpn:// или файл .conf ещё раз." }];
  const S4_OK = (body) => ({ status: 200, body: { ok: true, data: { status: "success",
    message: "S4 профиля AmneziaWG 2.0 (awg2): 142 → 27, пакет данных 1502 → 1387 байт — укладывается в 1400.",
    profile: body.profile, s4_old: 142, s4: 27, restarted: true, steps: S4_STEPS } } });
  const s4Data = (risk) => ({
    "/api/profiles": (d) => ({ ...d, installed: d.installed.map((p) => (p.profile === "awg2" ? { ...p, packet_risk: risk, s4: "142" } : p)) }),
    "/api/overview": (d) => ({ ...d, profiles: d.profiles.map((p) => (p.profile === "awg2" ? { ...p, packet_risk: risk } : p)) }) });
  const s4Dlg = (P) => { const d = P.$$(".gx-dlg").pop(); return d ? { title: (d.querySelector(".gx-dlg__title") || { textContent: "" }).textContent,
    text: d.textContent, foot: d.querySelectorAll(".gx-dlg__foot button").map((b) => b.textContent.trim()), input: !!d.querySelector("input"),
    stat: d.querySelectorAll(".gx-stat__t").map((n) => n.textContent) } : null; };
  const s4Card = (P, pid) => { const c = P.$(`.gx-pcard[data-profile="${pid}"]`);
    return c ? { text: c.textContent, btns: c.querySelectorAll("button").map((b) => b.textContent.trim()) } : null; };
  // R8-2: срок подписки в окне — из sub-list (совет сервера и update_slow), словами окон перевыпуска
  const S4_SUBS = (body) => ({ status: 200, body: { ok: true, data: { status: "success", subs: [], count: 0, domain_ready: true, update_interval: 12,
    update_slow: [{ name: "FlClash", hours: 24 }] } } });
  await scenario("s4_card", { hash: "#profiles", data: s4Data(S4R), api: { "POST /api/action/profile-fix-s4": S4_OK, "POST /api/action/sub-list": S4_SUBS } }, async (P, step, rec) => {
    await P.tick(100);
    rec.cards = { awg2: s4Card(P, "awg2"), awg3: s4Card(P, "awg3") };
    await P.click(P.byText("Исправить S4", '.gx-pcard[data-profile="awg2"] button'));
    rec.ask = s4Dlg(P);
    await P.click(P.byText("Исправить S4", ".gx-dlg__foot button"));
    await P.tick(100);
    rec.calls = P.calls("POST", "/api/action/profile-fix-s4").map((c) => c.body);
    rec.subCalls = P.calls("POST", "/api/action/sub-list").map((c) => c.body);
    rec.done = s4Dlg(P);
  });
  // R9-4: sub-list отказал (500), ответил мусором или не дождался (тайм-аут запроса — AbortError, как после
  // timeoutSignal) — окно всё равно открывается и говорит «до 12 ч», без тостов и ошибок JS.
  const S4_SUB_BAD = {
    s4_sub_500: () => ({ status: 500, body: { ok: false, error: "boom", code: "sub_failed" } }),
    s4_sub_junk: () => ({ status: 200, body: "мусор" }),
    s4_sub_junk_fields: () => ({ status: 200, body: { ok: true, data: { status: "success", update_interval: "abc", update_slow: "FlClash" } } }),
    s4_sub_timeout: () => Promise.reject(Object.assign(new Error("aborted"), { name: "AbortError" })),
  };
  for (const [name, handler] of Object.entries(S4_SUB_BAD)) {
    await scenario(name, { hash: "#profiles", data: s4Data(S4R), api: { "POST /api/action/profile-fix-s4": S4_OK, "POST /api/action/sub-list": handler } },
      async (P, step, rec) => {
        await P.tick(100);
        await P.click(P.byText("Исправить S4", '.gx-pcard[data-profile="awg2"] button'));
        await P.tick(50);
        rec.ask = s4Dlg(P);
        rec.subCalls = P.calls("POST", "/api/action/sub-list").length;
        rec.toasts = P.$$(".gx-toast").length;
      });
  }
  await scenario("s4_card_cancel", { hash: "#profiles", data: s4Data(S4R), api: { "POST /api/action/profile-fix-s4": S4_OK } }, async (P, step, rec) => {
    await P.tick(100);
    await P.click(P.byText("Исправить S4", '.gx-pcard[data-profile="awg2"] button'));
    await P.click(P.byText("Отмена", ".gx-dlg__foot button"));
    rec.calls = P.calls("POST", "/api/action/profile-fix-s4").length;
  });
  await scenario("s4_keyless", { hash: "#profiles", data: s4Data({ ...S4R, keyless: 2 }), api: { "POST /api/action/profile-fix-s4": S4_OK } },
    async (P, step, rec) => {
      await P.tick(100);
      await P.click(P.byText("Исправить S4", '.gx-pcard[data-profile="awg2"] button'));
      rec.ask = s4Dlg(P);
      rec.before = s4Dlg(P).foot;
      const inputs = P.$$(".gx-dlg").pop().querySelectorAll("input");
      await P.type(inputs[inputs.length - 1], "39001");
      await P.click(P.byText("Исправить S4", ".gx-dlg__foot button"));
      await P.tick(100);
      rec.calls = P.calls("POST", "/api/action/profile-fix-s4").map((c) => c.body);
    });
  for (const [label, extra] of [["s4_overview", { hash: "#overview" }], ["s4_overview_miniapp", { tg: true, width: 390 }]]) {
    await scenario(label, { ...extra, data: s4Data(S4R), api: { "POST /api/action/profile-fix-s4": S4_OK } }, async (P, step, rec) => {
      await P.tick(100);
      rec.page = P.page();
      rec.alerts = alerts(P).filter((a) => a.title.includes("скачивание"));
      await P.click(P.byText("Исправить S4", ".gx-ovalert__btns button"));
      rec.ask = s4Dlg(P);
      await P.click(P.byText("Исправить S4", ".gx-dlg__foot button"));
      await P.tick(100);
      rec.calls = P.calls("POST", "/api/action/profile-fix-s4").map((c) => c.body);
      rec.done = s4Dlg(P);
    });
  }
  // PARAM-11 (проверяльщик 15.10, REVIEW-CORE-1013, находка 2): после «Изменить параметр» — слова ядра, как у «Исправить
  // S4»: итог (message = итог + summary) и кому что делать (result.steps). Прежде — тост «Действует для новых ключей», и
  // про подписки (обновятся сами) и выданные ключи (выдать заново) человек в панели не узнавал. Мини-апп — тот же app.js.
  const P11_PARAMS = { status: 200, body: { ok: true, data: { profile: "awg3", line: "3.1", base_line: "3.1", preset: "default",
    params: [{ key: "Jc", value: "4", scope: "client" }, { key: "S1", value: "12", scope: "server" }] } } };
  const P11_SUM = "Выданные ключи работают со старым значением, новое — только с новым конфигом: подписки обновятся сами, выданные ключи vpn:// и .conf — выдать заново.";
  const P11_STEPS = [{ kind: "subscription", text: "С подпиской (vera): новое значение придёт само в течение 12 часов или по «Обновить» в приложении." },
    { kind: "key", text: "Выдавали ключ или файл (anna): прежний работает со старым значением; новое — только в новом: выдайте ключ vpn:// или файл .conf ещё раз." },
    { kind: "unknown", text: "Кто на профиле, определить не удалось (список пользователей не прочитался) — это касается всех." }];
  const P11_OK = (body) => ({ status: 200, body: { ok: true, data: { status: "success",
    message: `Профиль awg3: параметр ${body.key} теперь ${body.value}. ${P11_SUM}`, profile: "awg3", key: body.key, value: body.value,
    reissue_required: false, restarted: false, clients_update: true,
    result: { profile: "awg3", key: body.key, value: body.value, scope: "client", clients_update: true, summary: P11_SUM, steps: P11_STEPS } } } });
  for (const [label, extra] of [["param_steps", { hash: "#profiles" }], ["param_steps_miniapp", { tg: true, width: 390 }]]) {
    await scenario(label, { ...extra, api: { "GET /api/profiles/awg3/params": P11_PARAMS, "POST /api/action/profile-set-param": P11_OK } },
      async (P, step, rec) => {
        await P.tick(100);
        if (extra.tg) { await P.click('#tabs button[data-goto="profiles"]'); await P.tick(100); }
        await P.click(P.byText("Параметры маскировки", '.gx-pcard[data-profile="awg3"] button'));
        await P.tick(100);
        await P.click(P.byText("Изменить параметр", '.gx-pcard[data-profile="awg3"] button'));
        await P.type(P.$$(".gx-dlg").pop().querySelector("input"), "5");
        await P.click(P.byText("Записать", ".gx-dlg__foot button"));
        await P.tick(100);
        rec.calls = P.calls("POST", "/api/action/profile-set-param").map((c) => c.body);
        rec.done = s4Dlg(P);
        rec.toast = toastText(P);
      });
  }
  // Ответ прежнего ядра (без clients_update, summary и steps) — тост, как раньше.
  await scenario("param_old_core", { hash: "#profiles", api: { "GET /api/profiles/awg3/params": P11_PARAMS,
    "POST /api/action/profile-set-param": () => ({ status: 200, body: { ok: true, data: { status: "success", message: "ok", reissue_required: false, restarted: false } } }) } },
    async (P, step, rec) => {
      await P.tick(100);
      await P.click(P.byText("Параметры маскировки", '.gx-pcard[data-profile="awg3"] button'));
      await P.tick(100);
      await P.click(P.byText("Изменить параметр", '.gx-pcard[data-profile="awg3"] button'));
      await P.type(P.$$(".gx-dlg").pop().querySelector("input"), "5");
      await P.click(P.byText("Записать", ".gx-dlg__foot button"));
      await P.tick(100);
      rec.dlgs = P.$$(".gx-dlg").length;
      rec.toast = toastText(P);
    });
  // K5-3: параметр-ключ (HeaderProtectionKey) — «задан (скрыт)» в разделе «Параметры маскировки» и в окне правки
  // («Сейчас» и подсказка при наведении), а не значение: экраны попадают в записи и скриншоты гайдов.
  const K53_HPK = "SFBLdGVzdE9ubHlOb3RBUmVhbEtleUZvclBhbmVsMDE=";
  await scenario("param_secret", { hash: "#profiles", api: { "GET /api/profiles/awg3/params": { status: 200, body: { ok: true, data: {
    profile: "awg3", line: "3.1", base_line: "3.1", preset: "default",
    params: [{ key: "Jc", value: "4", scope: "client" }, { key: "HeaderProtectionKey", value: K53_HPK, scope: "server" }] } } },
    // KEENETIC-I1-N N4: ответ ядра на новый ключ — «задан (скрыт)» и «***», как теперь отвечает мост.
    "POST /api/action/profile-set-param": () => ({ status: 200, body: { ok: true, data: { status: "success",
      message: "Профиль awg3: параметр HeaderProtectionKey теперь задан (скрыт).", value: "***", reissue_required: true, restarted: true } } }) } },
    async (P, step, rec) => {
      await P.tick(100);
      await P.click(P.byText("Параметры маскировки", '.gx-pcard[data-profile="awg3"] button'));
      await P.tick(100);
      rec.rows = P.$$('.gx-pcard[data-profile="awg3"] .gx-pacc__r').map((n) => n.textContent);
      await P.click(P.byText("Изменить параметр", '.gx-pcard[data-profile="awg3"] button'));
      const d = P.$$(".gx-dlg").pop();
      const ro = d.querySelector(".gx-ro");
      rec.nowJc = ro.textContent;
      const s = d.querySelector("select");
      s.value = "HeaderProtectionKey";
      s.dispatchEvent(new P.win.Event("change", { bubbles: true }));
      await P.settle();
      rec.now = { text: ro.textContent, title: ro.title || "" };
      rec.leak = P.doc.body.textContent.includes(K53_HPK.slice(0, 8)) || (ro.title || "").includes(K53_HPK.slice(0, 8));
      rec.hpk8 = K53_HPK.slice(0, 8);
      // KEENETIC-I1-N N4: итог записи нового ключа (заголовок тоста) печатал введённый ключ целиком.
      const K53_NEW = "TjMtdGVzdC1vbmx5LW5vdC1hLXJlYWwta2V5LTAwMDE=";
      await P.type(d.querySelector("input"), K53_NEW);
      await P.click(P.byText("Записать", ".gx-dlg__foot button"));
      await P.tick(100);
      rec.sent = P.calls("POST", "/api/action/profile-set-param").map((c) => c.body.value);
      rec.doneToast = toastText(P);
      rec.newLeak = P.doc.body.textContent.includes(K53_NEW.slice(0, 8));
      rec.new8 = K53_NEW.slice(0, 8);
    });
  // Окно MTU: «1280 — стандарт…», «Вернуть 1280», ниже 1280 — отказ словами (L-02б), предупреждение — только пакет
  // больше 1440 (L-02а; S4 профиля — 40).
  await scenario("mtu_window", { hash: "#profiles",
    data: { "/api/profiles": (d) => ({ ...d, installed: d.installed.map((p) => (p.profile === "awg2" ? { ...p, mtu: "1320", s4: "40" } : p)) }) },
    api: { "POST /api/action/profile-mtu": (b) => ({ status: 200, body: { ok: true, data: { status: "success", message: "ok", mtu: Number(b.mtu), warnings: [] } } }) } },
    async (P, step, rec) => {
      await P.tick(100);
      await P.click(P.byText("MTU", '.gx-pcard[data-profile="awg2"] button'));
      const d = () => P.$$(".gx-dlg").pop();
      const look = () => {
        const okb = d().querySelector(".gx-dlg__foot .ga-btn--primary");
        const err = d().querySelector(".gx-hint--bad");
        return { warn: d().querySelectorAll(".gx-mtuwarn .gx-al__text").map((n) => n.textContent),
          ok: okb.textContent.trim(), value: d().querySelector("input").value,
          err: err && !err.hidden ? err.textContent : "", off: !!okb.disabled };
      };
      rec.hint = d().textContent;
      rec.start = look();
      await P.type(d().querySelector("input"), "1500"); rec.v1500 = look();
      await P.click(P.byText("Вернуть 1280", ".gx-dlg button")); rec.std = look();
      await P.type(d().querySelector("input"), "1330"); rec.v1330 = look();
      await P.type(d().querySelector("input"), "1320"); rec.v1320 = look();
      await P.type(d().querySelector("input"), "1279"); rec.v1279 = look();
      await P.type(d().querySelector("input"), "1100"); rec.v1100 = look();
      await P.type(d().querySelector("input"), "1600"); rec.v1600 = look();
      await P.type(d().querySelector("input"), "1500");
      await P.click(P.byText("Сохранить всё равно", ".gx-dlg__foot button"));
      await P.tick(100);
      rec.calls = P.calls("POST", "/api/action/profile-mtu").map((c) => c.body);
    });
  // KEENETIC-I1-N (N2): пиры контейнера, которых сверка не подхватит, — причиной словами ядра (skipped[].message):
  // «совпадает» с причиной — предупреждением, у расхождения — строкой под списком, итог подхвата — в тосте.
  const N2_SK = [{ key: "AbCdEfGhIjKl", reason: "ip", message: "Пир AbCdEfGhIjKl… в конфиге контейнера пропущен: в AllowedIPs нет адреса IPv4" }];
  let n2calls = 0;
  await scenario("rec_skipped", { hash: "#profiles", api: {
    "GET /api/import": { status: 200, body: { ok: true, data: { containers: [{ container: "amnezia-awg3", managed: true, profile: "awg3",
      line: "3.1", port: 39001, subnet: "10.8.3.0/24", peers: 2 }] } } },
    "POST /api/action/import-reconcile": (b) => {
      n2calls += 1;
      const base = { status: "success", message: "ok", profile: "awg3", container: "amnezia-awg3", gone: [], skipped: N2_SK, deactivated: 0 };
      if (b.apply === "on") return { status: 200, body: { ok: true, data: { ...base, in_sync: false, new: [], applied: true, added: 1 } } };
      return { status: 200, body: { ok: true, data: n2calls === 1 ? { ...base, in_sync: true, new: [], applied: false, added: 0 }
        : { ...base, in_sync: false, new: [{ pubkey: "QkFEa2V5", ip: "10.8.3.9", name: "Phone" }], applied: false, added: 0 } } };
    } } },
    async (P, step, rec) => {
      await P.tick(100);
      await P.click(P.byText("Найти контейнеры приложения", "button"));
      await P.tick(100);
      await P.click(P.byText("Сверить с приложением", "button"));
      await P.tick(100);
      rec.sync = P.$$(".gx-al").filter((n) => n.textContent.includes("совпадают со списком")).map((n) => [n.className, n.textContent.trim()]);
      await P.click(P.byText("Сверить с приложением", "button"));
      await P.tick(100);
      rec.diff = P.$$(".gx-prec .gx-plegend").map((n) => n.textContent);
      await P.click(P.byText("Подхватить изменения", "button"));
      await P.tick(100);
      rec.toast = toastText(P);
      rec.toastWarn = !!P.$("#toastHost .gx-toast__ic--warning");
    });

  console.log(JSON.stringify(R));
})().catch((e) => { console.log(JSON.stringify({ __fatal: String((e && e.stack) || e) })); });
"""


class PanelNavNamesTests(unittest.TestCase):
    """Без node: режим меню на <html> и цель перехода — разные атрибуты (ACC5-2)."""

    @classmethod
    def setUpClass(cls):
        cls.app = read_text(os.path.join(STATIC, "app.js"))
        cls.html = read_text(os.path.join(STATIC, "index.html"))

    def test_click_target_attribute_is_not_on_the_root(self):
        root = re.search(r"<html\b[^>]*>", self.html).group(0)
        root_attrs = set(re.findall(r"\s([a-z][a-z0-9-]*)=", root))
        self.assertIn("data-nav", root_attrs)  # режим меню из «Вида» — на месте
        # Цель перехода в общем обработчике кликов — data-goto, и только на кнопке: атрибут на <html>, <body> или
        # контейнере клик мимо кнопок не ловит.
        start = self.app.index('document.addEventListener("click", (ev) => {')
        handler = self.app[start:self.app.index("\n  });\n", start)]
        self.assertIn('ev.target.closest("button[data-goto]")', handler)
        self.assertIn("setPage(go.dataset.goto)", handler)
        self.assertNotIn("data-goto", root_attrs)
        # Ни одна кнопка не несёт data-nav: закрыть обратную дорогу (слияние старой ветки вернуло бы цели с data-nav,
        # и обработчик их молча не видел бы).
        self.assertNotIn('"data-nav":', self.app)
        self.assertNotIn('closest("[data-nav]")', self.app)
        self.assertEqual(self.app.count('"data-goto":'), 7)  # меню ×3, вкладки, «Ещё», шаги и «Все» на Обзоре

    def test_unknown_section_from_click_is_ignored_and_from_address_falls_back(self):
        start = self.app.index("function setPage(page, push = true) {")
        body = self.app[start:self.app.index("\n  }\n", start)]
        self.assertIn("if (!known && push) return;", body)
        self.assertIn('const next = known ? requested : "overview";', body)
        # Адрес и история — без push: неизвестный хэш по-прежнему ведёт на «Обзор».
        self.assertIn('window.addEventListener("hashchange", () => setPage((location.hash || "#overview").slice(1), false));', self.app)


class PanelWordsStaticTests(unittest.TestCase):
    """Без node: кнопка временной ссылки зовётся так, как на экране, — «Временная ссылка» (NC-09); у раздела одна
    подпись на телефоне и компьютере (NC-14)."""

    @classmethod
    def setUpClass(cls):
        cls.app = read_text(os.path.join(STATIC, "app.js"))

    def test_the_button_name_matches_the_screen(self):
        self.assertNotIn("Скачать по ссылке", self.app)
        self.assertNotIn("Download by link", self.app)
        for key, ru, en in (("ucShare", "Временная ссылка", "Temporary link"),):
            self.assertIn('%s: "%s"' % (key, ru), self.app)
            self.assertIn('%s: "%s"' % (key, en), self.app)
        self.assertIn("«Временная ссылка» во вкладке профиля", self.app)
        self.assertIn("“Temporary link” button in the profile tab", self.app)

    def test_navigation_has_no_second_short_name(self):
        """NC-14: у разделов одна подпись — вкладки телефона берут её же (прежние «Люди»/People — короткие имена)."""
        self.assertNotIn("shNavPeople", self.app)
        self.assertNotIn("short:", self.app[self.app.index("const NAV = ["):self.app.index("];", self.app.index("const NAV = ["))])
        self.assertIn("document.createTextNode(t(n.label))", self.app)

    def test_the_refusal_is_checked_before_the_lifetime_is_asked(self):
        start = self.app.index("async function openPageCreate(user) {")
        body = self.app[start:self.app.index("// ── Новый пользователь (шторка)", start)]
        self.assertLess(body.index("if (pageNeedsDomain()) { pageDomainDialog(user); return; }"), body.index("let ttl = "))


class PanelCssStaticTests(unittest.TestCase):
    """Без node: вёрстка, которую видно только по правилам стилей (у игрушечного DOM нет раскладки); в настоящем браузере
    на 1440 и 375 то же проверено замером (передача CPU, раздел PERF-04)."""

    @classmethod
    def setUpClass(cls):
        cls.css = read_text(os.path.join(STATIC, "styles.css"))

    def rule(self, selector):
        m = re.search(r"(?m)^%s \{([^}]*)\}" % re.escape(selector), self.css)
        return m.group(1) if m else None

    def test_segment_wrapper_does_not_stick_out(self):
        """PNL-02 (168: лист «Показать адреса» — обёртка переключателя 511 px в колонке 507 px): без полей −2 px."""
        body = self.rule(".gx-segwrap")
        self.assertIsNotNone(body)
        self.assertNotRegex(body, r"margin\s*:\s*-")
        self.assertNotIn("padding", body)

    def test_server_ip_does_not_break_inside_the_number(self):
        """LH-6 (живая проверка 16.10, 1024 px: «2.56.177.9 / 6»): у строки с IPv4 значение не переносится, подпись — да."""
        self.assertIn("white-space: nowrap", self.rule(".gx-srv > div.gx-srv__ip > span:last-child"))
        self.assertIn("overflow-wrap: normal", self.rule(".gx-srv > div.gx-srv__ip > span:last-child"))
        self.assertIn("white-space: normal", self.rule(".gx-srv > div.gx-srv__ip > span:first-child"))

    def test_profile_tabs_wrap_and_never_clip(self):
        """MINIAPP-TABS-01: вкладки профилей не помещаются в ряд — ряд переносится; текст не обрезается многоточием и
        не прячется; вторая строка — своим элементом, разделитель « · » на экране скрыт."""
        box = self.rule(".gx-ptabs")
        self.assertIn("flex-wrap: wrap", box)
        btn = self.rule(".gx-ptabs > button")
        self.assertIn("white-space: normal", btn)
        self.assertIn("max-width: 100%", btn)
        self.assertIn("flex-direction: column", btn)
        for bad in ("ellipsis", "overflow: hidden", "nowrap"):
            self.assertNotIn(bad, btn)
        self.assertIn("display: none", self.rule(".gx-ptab__sep"))

    def test_kpi_label_gets_the_header_not_half_of_it(self):
        """PNL-06 (168: «Трафик за всё время» обрезан в плитке 272 px): правило на оба span шапки делило её пополам
        с значком — подписи доставалось 113 px."""
        self.assertIsNone(self.rule(".gx-kpi__h > span"))
        self.assertIn("flex: 1", self.rule(".gx-kpi__h > span:first-child"))
        self.assertIn("flex: none", self.rule(".gx-kpi__h > .gi"))


@unittest.skipUnless(NODE, "нет node 18+ — панель в игрушечном DOM не проверить")
class PanelClickNavDomTests(unittest.TestCase):
    """Клики по панели в игрушечном DOM: мимо кнопок — ни шага, по кнопкам и ⌘K — как прежде."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="goamnezia-panel-dom-") as tmp:
            script = os.path.join(tmp, "nav.js")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(SCENARIOS_JS)
            proc = subprocess.run([NODE, script, HARNESS], capture_output=True, text=True, timeout=240, cwd=ROOT)
        cls.out, cls.err = proc.stdout, proc.stderr
        lines = [ln for ln in cls.out.splitlines() if ln.startswith("{")]
        cls.R = json.loads(lines[-1]) if lines else {"__fatal": "no JSON: rc=%s\n%s\n%s" % (proc.returncode, cls.out[-2000:], cls.err[-2000:])}

    def scen(self, name):
        self.assertNotIn("__fatal", self.R, self.R.get("__fatal"))
        rec = self.R.get(name)
        self.assertIsNotNone(rec, name)
        self.assertNotIn("fatal", rec, rec.get("fatal"))
        # 0 ошибок JS: исключения в обработчиках и таймерах, console.error, необработанные отказы обещаний.
        self.assertEqual(rec["errors"], [], name)
        return rec

    def assertSteps(self, rec, expected):
        got = [(s["label"], s["page"]) for s in rec["steps"]]
        self.assertEqual(got, expected)

    def test_key_tabs_follow_the_actual_line_not_the_slot(self):
        """AWG31-01 (жалоба 05.10: «ключ 3.1 из панели в AmneziaVPN — всегда 2.0», и наоборот «из вкладки 2.0 — 3.1»).
        amnezia-awg2, подхваченный из приложения Amnezia, держит 3.1, и 2.0 встала в awg3 (LZ-02). Ключ ядро собирает по
        фактической линии профиля, а вкладки карточки панель подписывала по месту: «AmneziaWG 3.1» (awg3) отдавала ключ
        2.0, «AmneziaWG 2.0» (awg2) — ключ 3.1. Теперь вкладки, метки в списке, выбор временной ссылки и «кому он» в
        шторке нового человека — по линии из config.json («Настройки» приходят при входе)."""
        rec = self.scen("lines_swapped")
        self.assertEqual(rec["tabs"], ["AmneziaWG 2.0 · порт 443", "AmneziaWG 3.1 · порт 39001"])      # awg3, awg2 — порядок профилей прежний
        self.assertEqual(rec["share"], ["AmneziaWG 2.0 · порт 443", "AmneziaWG 3.1 · порт 39001"])
        add = {title: desc for title, desc in rec["add"]}
        self.assertIn("4.8.12.9", add["AmneziaWG 2.0 · порт 443"])                       # «кому он» — по линии 2.0
        self.assertIn("5.0.1.5", add["AmneziaWG 3.1 · порт 39001"])                        # и по линии 3.1

    def test_subnet_overlap_cards_and_the_old_profile_lead_to_the_way_out(self):
        """SUBNET-01 (жалоба 06.10: «при подхвате старого протокола и попытке добавить новый почему-то пересекаются
        подсети»). Приложение ставит свои контейнеры на одну 10.8.1.0/24, подхват берёт их как есть. Карточки обоих
        профилей говорят о пересечении и дают выход — перенести awg2 (старому awg1 подсеть не сменить) на свободную
        подсеть, которую подобрало ядро; окно «Подсеть» у старого — отказ сразу, без поля, и та же кнопка; перенос —
        набором имени, и согласие за людей приложения идёт тем же словом (adopted_confirm)."""
        rec = self.scen("subnet_ovl_cards")
        for pid in ("awg1", "awg2"):
            card = rec["cards"][pid]
            self.assertIsNotNone(card, pid)
            self.assertIn("Подсеть пересекается.", card["text"], pid)
            self.assertIn("перенесите AmneziaWG 2.0 · порт 39001 на свободную 10.8.2.0/24", card["text"], pid)
            self.assertIn("Перенести AmneziaWG 2.0 · порт 39001 на 10.8.2.0/24", card["btns"], pid)
        self.assertIn("ключи выдало приложение Amnezia", rec["cards"]["awg2"]["text"])
        self.assertNotIn("Подсеть пересекается.", rec["cards"]["awg3"]["text"])
        d = rec["fixedDlg"]
        self.assertEqual(d["title"], "Подсеть AmneziaWG 1.0 · порт 51820 не сменить")
        self.assertIn("перенесите его на свободную 10.8.2.0/24", d["text"])
        self.assertIsNone(d["input"])
        self.assertIn("Перенести AmneziaWG 2.0 · порт 39001 на 10.8.2.0/24", d["foot"])
        self.assertIn("ключи выдало приложение Amnezia", rec["moveDlg"]["text"])
        self.assertEqual(rec["calls"], [{"profile": "awg2", "subnet": "10.8.2.0/24", "adopted_confirm": "awg2", "confirm": "awg2"}])

    def test_subnet_dialog_fills_in_the_free_subnet_and_asks_for_the_app_people(self):
        """SUBNET-01: у переносимого профиля в окне «Подсеть» свободная подсеть уже подставлена — остаётся подтвердить.
        У людей приложения ключ только в приложении (ACC-2): ядро отвечает adopted_keyless, окно согласия показывает его
        слова, набранное имя — повтор с adopted_confirm."""
        rec = self.scen("subnet_ovl_dialog")
        self.assertEqual(rec["dlg"]["input"], "10.8.2.0/24")
        self.assertIn("Свободная подсеть подставлена", rec["dlg"]["text"])
        self.assertIn("та же сеть, что у AmneziaWG 1.0", rec["dlg"]["text"])
        self.assertIn("Профиль awg2 подхвачен у приложения Amnezia", rec["keyless"]["text"])
        self.assertEqual(rec["calls"], [{"profile": "awg2", "subnet": "10.8.2.0/24", "confirm": "awg2"},
                                        {"profile": "awg2", "subnet": "10.8.2.0/24", "adopted_confirm": "awg2", "confirm": "awg2"}])

    S4_RISK_TEXT = ("На части сетей (мобильный интернет, PPPoE, некоторые хостеры) скачивание не идёт: S4 = 142, пакет данных "
                    "1502 байт больше 1400.")

    def test_s4_card_warns_asks_and_says_who_does_what(self):
        """S4-FIX (MTU-01): у выданного профиля 2.0 с большим S4 (packet_risk) карточка говорит, что не так, и даёт
        «Исправить S4»; окно — то же, что в SSH: S4 12–24 (S4-MARGIN), перезапуск 15–25 с, ключи прежние, кому импортировать заново —
        после; мост — profile-fix-s4 с confirm; итог — слова ядра: что сделано и кому что делать."""
        rec = self.scen("s4_card")
        card = rec["cards"]["awg2"]
        self.assertIn("Слишком большие пакеты.", card["text"])
        self.assertIn(self.S4_RISK_TEXT, card["text"])
        self.assertIn("Исправить S4", card["btns"])
        self.assertNotIn("Исправить S4", rec["cards"]["awg3"]["btns"])
        self.assertNotIn("Слишком большие пакеты", rec["cards"]["awg3"]["text"])
        self.assertEqual(rec["ask"]["title"], "Исправить S4 · AmneziaWG 2.0 · порт 39001")
        self.assertIn("S4 станет 12–24 — при MTU 1280 пакет данных не больше 1384 байт, с запасом до 1400.", rec["ask"]["text"])
        self.assertNotIn("не больше 40", rec["ask"]["text"])
        # R8-2: о подписке — словами итога и окон перевыпуска, срок из sub-list, а не «в течение 12 часов»
        self.assertIn("подтянут новые настройки сами (до 12 ч, у FlClash — до 24 ч) или по «Обновить»", rec["ask"]["text"])
        self.assertNotIn("в течение 12 часов", rec["ask"]["text"])
        self.assertEqual(len(rec["subCalls"]), 1)
        self.assertNotIn("name", rec["subCalls"][0] or {})
        self.assertIn("клиенты переподключатся сами, обычно за 15–25 секунд. Ключи пользователей прежние.", rec["ask"]["text"])
        self.assertIn("после исправления будет список, кому.", rec["ask"]["text"])
        self.assertFalse(rec["ask"]["input"])
        self.assertEqual(rec["calls"], [{"profile": "awg2", "confirm": "awg2"}])
        self.assertEqual(rec["done"]["title"], "S4 исправлен · AmneziaWG 2.0 · порт 39001")
        self.assertIn("142 → 27, пакет данных 1502 → 1387 байт", rec["done"]["text"])
        self.assertEqual(len(rec["done"]["stat"]), 3)
        self.assertIn("обновится сама в течение 12 часов или по «Обновить» в приложении", rec["done"]["stat"][1])
        self.assertIn("(anna): импортировать заново", rec["done"]["stat"][2])
        self.assertEqual(self.scen("s4_card_cancel")["calls"], 0)

    def test_s4_window_when_sub_list_fails(self):
        """R9-4: sub-list отказал (500), ответил мусором или не дождался — окно «Исправить S4» открывается, срок
        «до 12 ч» без названий приложений, тостов и ошибок JS нет (scen проверяет ошибки JS)."""
        for name in ("s4_sub_500", "s4_sub_junk", "s4_sub_junk_fields", "s4_sub_timeout"):
            rec = self.scen(name)
            self.assertIsNotNone(rec["ask"], name)
            self.assertEqual(rec["ask"]["title"], "Исправить S4 · AmneziaWG 2.0 · порт 39001", name)
            self.assertIn("подтянут новые настройки сами (до 12 ч) или по «Обновить»", rec["ask"]["text"], name)
            self.assertNotIn("FlClash", rec["ask"]["text"], name)
            self.assertEqual(rec["subCalls"], 1, name)
            self.assertEqual(rec["toasts"], 0, name)

    def test_param_change_says_who_does_what(self):
        """PARAM-11 (проверяльщик 15.10, находка 2): после смены параметра — окно со словами ядра: итог с summary и кому
        что делать по людям (steps), как у «Исправить S4»; не тост «Действует для новых ключей». В мини-аппе — так же.
        Ответ прежнего ядра — тост, как раньше."""
        for name in ("param_steps", "param_steps_miniapp"):
            rec = self.scen(name)
            self.assertEqual(rec["calls"], [{"profile": "awg3", "key": "Jc", "value": "5", "confirm": "awg3"}], name)
            done = rec["done"]
            self.assertIsNotNone(done, name)
            self.assertEqual(done["title"], "AmneziaWG 3.1 · порт 443: Jc = 5", name)
            self.assertIn("Профиль awg3: параметр Jc теперь 5. Выданные ключи работают со старым значением", done["text"], name)
            self.assertEqual(len(done["stat"]), 3, name)
            self.assertIn("(vera): новое значение придёт само в течение 12 часов", done["stat"][0], name)
            self.assertIn("(anna): прежний работает со старым значением", done["stat"][1], name)
            self.assertIn("определить не удалось", done["stat"][2], name)
            self.assertNotIn("Действует для новых ключей", done["text"] + rec["toast"], name)
        old = self.scen("param_old_core")
        self.assertIn("Действует для новых ключей", old["toast"])

    def test_secret_param_is_hidden(self):
        """K5-3: HeaderProtectionKey в разделе «Параметры маскировки» и в окне «Изменить параметр» — «задан (скрыт)»,
        ни знака значения ни в тексте, ни в подсказке при наведении; другие значения — как есть."""
        rec = self.scen("param_secret")
        hpk = [r for r in rec["rows"] if r.startswith("HeaderProtectionKey")]
        self.assertEqual(len(hpk), 1, rec["rows"])
        self.assertIn("задан (скрыт)", hpk[0])
        self.assertNotIn(rec["hpk8"], " ".join(rec["rows"]))
        self.assertTrue(any(r.startswith("Jc4") for r in rec["rows"]), rec["rows"])
        self.assertEqual(rec["nowJc"], "4")
        self.assertEqual(rec["now"], {"text": "задан (скрыт)", "title": "задан (скрыт)"})
        self.assertFalse(rec["leak"])
        self.assertIn('prParamHidden: "set (hidden)"', read_text(os.path.join(STATIC, "app.js")))
        # KEENETIC-I1-N N4: итог записи нового ключа — «HeaderProtectionKey = задан (скрыт)», ключа нет; мосту — целиком.
        self.assertEqual(len(rec["sent"]), 1, rec["sent"])
        self.assertTrue(rec["sent"][0].startswith(rec["new8"]))
        self.assertIn("HeaderProtectionKey = задан (скрыт)", rec["doneToast"])
        self.assertNotIn(rec["new8"], rec["doneToast"])
        self.assertFalse(rec["newLeak"])

    def test_reconcile_shows_why_peers_are_skipped(self):
        """KEENETIC-I1-N (N2): пир контейнера, которого сверка не подхватит, — не расхождение, но причина видна словами
        ядра: при «совпадает» — предупреждением (не зелёным «подхватывать нечего» без слов), у расхождения — строкой под
        списком, в итоге подхвата — в тосте рядом с «Добавлено пользователей», тост — предупреждение."""
        rec = self.scen("rec_skipped")
        why = "Пир AbCdEfGhIjKl… в конфиге контейнера пропущен: в AllowedIPs нет адреса IPv4."
        self.assertEqual(len(rec["sync"]), 1, rec["sync"])
        self.assertIn("gx-al--warning", rec["sync"][0][0])
        self.assertIn(why, rec["sync"][0][1])
        self.assertEqual(rec["diff"], [why])
        self.assertIn("Добавлено пользователей: 1", rec["toast"])
        self.assertIn(why, rec["toast"])
        self.assertTrue(rec["toastWarn"])

    def test_s4_keyless_asks_for_the_profile_name(self):
        """У людей приложения Amnezia без ключа на сервере (packet_risk.keyless) — набор имени профиля, и оно же —
        согласие за них: adopted_confirm (как перенос порта, ACC-2)."""
        rec = self.scen("s4_keyless")
        self.assertTrue(rec["ask"]["input"])
        self.assertIn("У пользователей на нём (2) ключи выдало приложение Amnezia", rec["ask"]["text"])
        self.assertEqual(rec["calls"], [{"profile": "awg2", "adopted_confirm": "awg2", "confirm": "awg2"}])

    def test_s4_on_the_overview_and_in_the_miniapp(self):
        """«Обзор» и мини-апп Telegram на телефоне: строка о профиле и та же кнопка «Исправить S4»."""
        for name in ("s4_overview", "s4_overview_miniapp"):
            rec = self.scen(name)
            self.assertEqual(rec["page"], "overview", name)
            self.assertEqual(len(rec["alerts"]), 1, name)
            a = rec["alerts"][0]
            self.assertEqual(a["title"], "Профиль AmneziaWG 2.0 · порт 39001: на части сетей скачивание не идёт", name)
            self.assertIn("Слишком большие пакеты (S4 = 142): мобильный интернет, PPPoE и некоторые хостеры их не пропускают.",
                          a["text"], name)
            self.assertEqual(a["btns"], ["Исправить S4"], name)
            self.assertEqual(a["tone"], "warning", name)
            self.assertEqual(rec["ask"]["title"], "Исправить S4 · AmneziaWG 2.0 · порт 39001", name)
            self.assertEqual(rec["calls"], [{"profile": "awg2", "confirm": "awg2"}], name)
            self.assertEqual(rec["done"]["title"], "S4 исправлен · AmneziaWG 2.0 · порт 39001", name)

    def test_mtu_window_standard_and_only_sure_warnings(self):
        """MTU 1280 — стандарт и минимум (решение владельца 08.10): подсказка словами, «Вернуть 1280»; ниже 1280 — отказ
        словами, кнопка «Сохранить» не жмётся (L-02б: туннель у клиентов с ::/0 не поднимется). Предупреждение — только
        пакет MTU + 80 + S4 больше 1440 (L-02а); тогда кнопка — «Сохранить всё равно». 1320 при S4 = 40 — без слов."""
        rec = self.scen("mtu_window")
        self.assertIn("1280 — стандарт, работает везде. На проводном интернете можно поднять до 1320 — будет на несколько "
                      "процентов быстрее; если скачивание начнёт зависать, верните 1280.", rec["hint"])
        ok = {"warn": [], "ok": "Сохранить", "err": "", "off": False}
        self.assertEqual(rec["start"], dict(ok, value="1320"))
        self.assertEqual(rec["v1500"]["warn"], ["Пакет 1620 байт: на части хостеров и сетей скачивание встанет — оставьте "
                                                "1320 или меньше."])
        self.assertEqual(rec["v1500"]["ok"], "Сохранить всё равно")
        self.assertEqual(rec["std"], dict(ok, value="1280"))
        self.assertEqual(rec["v1330"]["warn"], ["Пакет 1450 байт: на части хостеров и сетей скачивание встанет — оставьте "
                                                "1320 или меньше."])
        self.assertEqual(rec["v1320"], dict(ok, value="1320"))
        low = "Ниже 1280 туннель не поднимется у клиентов с IPv6 (AllowedIPs ::/0) — оставьте 1280 или больше."
        for v in ("v1279", "v1100"):
            self.assertEqual((rec[v]["err"], rec[v]["off"], rec[v]["warn"]), (low, True, []), v)
        self.assertEqual((rec["v1600"]["err"], rec["v1600"]["off"]), ("Нужно целое число от 1280 до 1500.", True))
        self.assertEqual(rec["calls"], [{"profile": "awg2", "mtu": "1500"}])

    def test_key_tabs_take_the_line_from_the_peer(self):
        """Поле line пира (его кладёт сервер панели из config.json) — метки в списке людей и вкладки карточки по нему,
        даже когда «Настройки» линий не несут."""
        rec = self.scen("lines_peer")
        self.assertEqual(rec["tags"], ["2.0", "3.1"])                         # awg3, awg2
        self.assertEqual(rec["tabs"], ["AmneziaWG 2.0", "AmneziaWG 3.1"])

    def test_key_tabs_follow_the_line_in_the_miniapp(self):
        """Мини-апп Telegram на телефоне — тот же путь: вкладки ключей подписаны линией, запросы подписаны initData."""
        rec = self.scen("lines_miniapp")
        self.assertEqual(rec["device"], "phone")
        self.assertEqual(rec["tabs"], ["AmneziaWG 2.0 · порт 443", "AmneziaWG 3.1 · порт 39001"])
        self.assertTrue(rec["signed"])

    def test_profile_tabs_two_lines_on_a_320px_phone(self):
        """MINIAPP-TABS-01 (владелец 16.10, мини-апп на телефоне): «AmneziaWG 2.0 · порт 443» и «AmneziaWG 3.1 · порт 46567 ·
        из приложения» в одну строку вылезали за край и наезжали друг на друга. Решение владельца: ничего не обрезать,
        подробности ниже имени. Теперь версия — первой строкой, «порт N · из приложения» — второй; полное имя — в тексте
        кнопки и в подписи для экранного чтеца. Раскладку игрушечный DOM не меряет — правила стилей ниже
        (PanelCssStaticTests.test_profile_tabs_wrap_and_never_clip)."""
        rec = self.scen("tabs_qr_miniapp_320")
        self.assertEqual(rec["tablist"], ["tablist", "Профили пользователя"])
        self.assertEqual(rec["slideTabs"], 0)
        self.assertEqual(rec["tabParts"], [
            ["AmneziaWG 2.0", "порт 443", "AmneziaWG 2.0 · порт 443", "true"],
            ["AmneziaWG 3.1", "порт 39001 · из приложения", "AmneziaWG 3.1 · порт 39001 · из приложения", "false"]])

    def test_vpn_qr_parts_cycle_by_themselves(self):
        """MINIAPP-QR-01 (владелец 16.10): QR vpn:// из нескольких частей стоял на «часть 1 из 2», пока не нажать стрелку, а
        приложение Amnezia сканирует части по очереди. Теперь части сменяются сами, по кругу, раз в 1,5 с; стрелки
        остаются, после нажатия часть стоит полный шаг; другая вкладка — заново с первой части."""
        rec = self.scen("tabs_qr_miniapp_320")
        self.assertEqual(rec["parts"], ["часть 1 из 3", "часть 2 из 3", "часть 3 из 3", "часть 1 из 3",
                                        "часть 3 из 3", "часть 3 из 3", "часть 1 из 3"])
        self.assertEqual(rec["afterTab"][0], ["false", "true"])
        # карточку закрыли — смена частей остановилась (RB3-2): подпись снятой карточки через три шага та же
        closed = rec["closed"]
        self.assertTrue(closed["capBefore"] and closed["capBefore"].startswith("часть "), closed)
        self.assertEqual(closed["afterClose"][:2], [False, False], closed)
        self.assertEqual(closed["later"], [closed["afterClose"][2]] * 3, closed)

    def test_bulk_add_sheet_sends_count_profiles_and_prefix(self):
        """«Добавить сразу несколько»: кнопка рядом с «Добавить»; 0 — подсказка-ошибка и ни одного запроса; 3 с
        префиксом — один запрос (count, профили в порядке 3.1 → 2.0, prefix, язык панели); итог — список в шторке."""
        rec = self.scen("users_bulk")
        self.assertEqual(rec["toolbar"], ["Добавить несколько", "Добавить"])
        self.assertEqual(rec["open"]["title"], "Сразу несколько")
        self.assertTrue(rec["open"]["prefixHidden"])                     # поле префикса — только в режиме «С префиксом»
        self.assertEqual(rec["open"]["foot"], ["Отмена", "Добавить 10"])
        self.assertEqual(rec["badCount"], {"calls": 0, "hint": "Нужно целое число от 1 до 100."})
        self.assertEqual(rec["calls"], [{"count": "3", "profiles": "awg3,awg2", "lang": "ru", "prefix": "team"}])
        self.assertEqual(rec["done"]["items"], ["team-01", "team-02", "team-03"])
        self.assertTrue(rec["done"]["alert"].startswith("Добавлено: 3."), rec["done"])
        self.assertEqual(rec["done"]["foot"], ["Готово"])

    def test_bulk_add_partial_names_where_it_stopped(self):
        rec = self.scen("users_bulk_partial")
        self.assertEqual(rec["calls"], [{"count": "3", "profiles": "awg3,awg2", "lang": "ru"}])   # без префикса — случайные
        self.assertEqual(rec["done"]["items"], ["Туманный Лондонfoggy_london"])
        self.assertIn("Добавлено 1 из 3.", rec["done"]["alert"])
        self.assertIn("На sunny_manila остановилось: Не удалось сгенерировать ключи. Добавленные остаются.", rec["done"]["alert"])

    def test_bulk_add_in_the_miniapp_on_a_phone(self):
        """Мини-апп «Панель» в Telegram: та же кнопка и шторка на телефоне, запрос подписан initData."""
        rec = self.scen("users_bulk_miniapp")
        self.assertEqual(rec["device"], "phone")
        self.assertEqual(rec["single"]["title"], "Новый пользователь")
        self.assertEqual(rec["single"]["calls"], [{"name": "tgsolo", "profiles": "awg3,awg2", "quota_gb": "0", "days": "0"}])
        self.assertEqual(rec["calls"], [{"count": "3", "profiles": "awg3,awg2", "lang": "ru"}])
        self.assertEqual(rec["done"]["items"], ["Человек 1bulk-01", "Человек 2bulk-02", "Человек 3bulk-03"])
        self.assertTrue(rec["signed"])

    def test_bulk_delete_by_ticks_counts_confirms_with_the_number_and_lists_the_result(self):
        """«Удалить сразу несколько»: «Ещё» рядом с поиском; режим выбора — строки становятся галочками, клик отмечает
        (карточка не открывается), «Все на странице» — все и снова никого, счётчик, «Удалить: N» неактивна без
        выбора; шторка — список от ядра, подтверждение набором числа (не то — кнопка неактивна); итог — списком,
        режим выбора закрыт; в ⌘K — отметить и удалить по условию."""
        rec = self.scen("users_del_select")
        self.assertEqual(rec["toolbar"][-3:], ["Ещё", "Добавить несколько", "Добавить"])
        self.assertEqual(rec["menu"], ["Выбрать несколько", "Удалить по префиксу", "Удалить выключенных руками",
                                       "Удалить с истёкшим сроком", "Удалить с исчерпанным лимитом"])
        self.assertEqual(rec["selToolbar"], ["Все5", "На связи1", "Без доступа1"])   # отбор и поиск остаются
        self.assertEqual(rec["start"], {"count": "Выбрано: 0", "all": "false", "del": ["Удалить: 0", True]})
        self.assertEqual(rec["row"], ["checkbox", "false"])
        self.assertEqual(rec["two"], {"count": "Выбрано: 2", "all": "mixed", "del": ["Удалить: 2", False], "checked": 2, "sheets": 0})
        self.assertEqual(rec["all"], {"count": "Выбрано: 5", "all": "true", "del": ["Удалить: 5", False]})
        self.assertEqual(rec["none"], {"count": "Выбрано: 0", "all": "false", "del": ["Удалить: 0", True]})
        self.assertEqual(rec["preview"], [{"names": "bob,office-01"}])
        self.assertEqual(rec["sheet"]["title"], "Удалить сразу несколько")
        self.assertEqual(rec["sheet"]["radios"], [])                         # отмеченные — без выбора условия
        self.assertEqual(rec["sheet"]["items"], ["Бобbob", "office-01"])
        self.assertEqual(rec["sheet"]["foot"], [["Отмена", False], ["Удалить 2", False]])
        self.assertEqual(rec["dialog"], {"title": "Удалить пользователей: 2?", "word": "2", "wrongDisabled": True})
        self.assertEqual(rec["calls"], [{"confirm": "2", "names": "bob,office-01"}])
        self.assertEqual(rec["done"]["alerts"], ["Удалено: 2."])
        self.assertEqual(rec["done"]["items"], ["Бобbob", "office-01"])
        self.assertEqual(rec["done"]["foot"], [["Готово", False]])
        self.assertEqual(rec["after"], {"bar": False, "more": True, "checkboxes": 0})
        self.assertEqual(sorted(rec["cmd"]), sorted(["Выбрать пользователей для удаления", "Удалить пользователей по префиксу",
                                                     "Удалить выключенных пользователей", "Удалить пользователей с истёкшим сроком",
                                                     "Удалить пользователей с исчерпанным лимитом"]))

    def test_bulk_delete_by_prefix_shows_the_new_list_when_it_changed(self):
        """По префиксу: негодный — подсказка-ошибка, ядро не зовётся; годный — список от ядра; ядро отказало
        confirm_mismatch (список изменился) — «посмотрите заново», новый список и новое число на кнопке."""
        rec = self.scen("users_del_rule_changed")
        self.assertEqual(rec["open"]["radios"][0], ["По префиксу", "true"])
        self.assertEqual(rec["open"]["foot"], [["Отмена", False], ["Удалить", True]])
        self.assertEqual(rec["bad"], {"hint": "Только латиница, цифры, «_» и «-», до 28 знаков.", "foot": [["Отмена", False], ["Удалить", True]], "calls": 0})
        self.assertEqual(rec["ok"]["items"], ["office-01", "office-02"])
        self.assertEqual(rec["ok"]["foot"], [["Отмена", False], ["Удалить 2", False]])
        self.assertEqual(rec["calls"], [{"confirm": "2", "prefix": "office-"}])
        self.assertIn("Список успел измениться — посмотрите, кого выбор задевает теперь, и подтвердите заново.", rec["changed"]["alerts"])
        self.assertEqual(rec["changed"]["items"], ["office-01", "office-02", "office-03"])
        self.assertEqual(rec["changed"]["foot"], [["Отмена", False], ["Удалить 3", False]])

    def test_bulk_delete_too_many_and_partial(self):
        rec = self.scen("users_del_partial_many")
        self.assertEqual(rec["many"]["radios"][3], ["С исчерпанным лимитом", "true"])
        self.assertIn("Под выбор попадает 150, а за раз удаляется до 100. Удалите частями: отметьте до 100 пользователей галочками в списке.",
                      rec["many"]["alerts"])
        self.assertEqual(rec["many"]["foot"], [["Отмена", False], ["Удалить", True]])
        self.assertEqual(rec["done"]["alerts"][0], "Удалено 1 из 2. На office-02 остановилось: контейнер не принял изменения.")
        self.assertIn("Оставлены в списке выключенными (1): удаление можно повторить, когда контейнер снова примет изменения.office-02",
                      rec["done"]["alerts"])
        self.assertEqual(rec["done"]["items"], ["office-01"])

    def test_bulk_delete_in_the_miniapp_on_a_phone(self):
        """Мини-апп «Панель» в Telegram: «Ещё» — значком с подписью для чтеца, галочки на карточках, запрос подписан."""
        rec = self.scen("users_del_miniapp")
        self.assertEqual(rec["device"], "phone")
        self.assertEqual(rec["more"], ["", "Ещё действия со списком"])
        self.assertEqual(rec["card"], ["checkbox", "false", True])
        self.assertEqual(rec["sel"], {"count": "Выбрано: 1", "all": "mixed", "del": ["Удалить: 1", False]})
        self.assertEqual(rec["sheet"]["items"], ["Бобbob"])
        self.assertEqual(rec["calls"], [{"confirm": "1", "names": "bob"}])
        self.assertEqual(rec["done"]["alerts"], ["Удалено: 1."])
        self.assertTrue(rec["signed"])

    def step(self, rec, label):
        return next(s for s in rec["steps"] if s["label"] == label)

    def test_off_target_clicks_keep_the_section(self):
        """«Протоколы»: заголовок, заголовок карточки «AmneziaWG 3.1», пустое место, <body>, <html>, поиск, «Вид», «🎲»."""
        rec = self.scen("desktop_off_targets")
        self.assertEqual(rec["mode"], "full")
        self.assertEqual(rec["cardTitle"], "AmneziaWG 3.1")
        labels = ["start", "title", "card title", "empty main", "empty section", "frame", "body", "html", "search",
                  "search input", "search closed", "view", "dice"]
        self.assertSteps(rec, [(x, "profiles") for x in labels])
        self.assertTrue(all(s["hash"] == "#profiles" for s in rec["steps"]), rec["steps"])
        # Поиск кнопкой и клик в его поле окно не закрывают (раньше тот же клик и уводил, и закрывал).
        self.assertTrue(self.step(rec, "search")["cmd"])
        self.assertTrue(self.step(rec, "search input")["cmd"])
        self.assertFalse(self.step(rec, "search closed")["cmd"])
        self.assertTrue(self.step(rec, "view")["look"])
        self.assertTrue(self.step(rec, "dice")["look"])

    def test_navigation_targets_and_address_still_work(self):
        rec = self.scen("desktop_nav")
        self.assertSteps(rec, [("start", "overview"), ("menu users", "users"), ("menu profiles", "profiles"),
                               ("menu overview", "overview"), ("overview all", "profiles"), ("hash backups", "backups"),
                               ("hash unknown", "overview"), ("menu logs", "logs"),
                               # С клика неизвестный раздел — ни шага (а не «Обзор»), контейнер с data-goto — не цель.
                               ("unknown target", "logs"), ("container target", "logs")])
        self.assertEqual(self.step(rec, "menu logs")["hash"], "#logs")
        # LH-6: IPv4 в карточке «Сервер» — в строке, где значение не переносится
        self.assertIn("203.0.113.10", rec["srvIp"], rec.get("srvIp"))
        # LH-8: у профиля 2.0 на образе 3.x — «обслуживает и линию 2.0», у 3.1 — просто «образ …»
        imgs = dict(rec["imgs"])
        self.assertEqual(imgs.get("awg2"), "образ 3.1.20260828 — обслуживает и линию 2.0", rec["imgs"])
        self.assertEqual(imgs.get("awg3"), "образ 3.1.20260828", rec["imgs"])
        self.assertEqual(self.step(rec, "unknown target")["hash"], "#logs")
        self.assertEqual(self.step(rec, "container target")["hash"], "#logs")
        self.assertEqual(rec["current"], "logs")

    def test_first_run_steps_lead_to_sections(self):
        rec = self.scen("overview_steps")
        self.assertEqual(rec["stepBtns"], ["profiles", "users"])
        self.assertSteps(rec, [("start", "overview"), ("first step", "profiles")])

    def test_cmd_k_opens_and_navigates(self):
        rec = self.scen("desktop_cmdk")
        self.assertSteps(rec, [("cmd open", "overview"), ("cmd click users", "users"), ("ctrl-k open", "users"),
                               ("cmd enter traffic", "traffic"), ("cmd toggle", "traffic")])
        self.assertEqual([s["cmd"] for s in rec["steps"]], [True, False, True, False, False])

    def test_add_user_dice_keeps_users(self):
        rec = self.scen("users_dice")
        self.assertSteps(rec, [("start", "users"), ("add sheet", "users"), ("dice", "users")])
        self.assertTrue(self.step(rec, "add sheet")["sheet"])
        self.assertTrue(self.step(rec, "dice")["sheet"])
        self.assertEqual(rec["suggestCalls"], 1)
        self.assertIn("misty-london", rec["values"])

    def test_icon_and_top_menus(self):
        for nav in ("icons", "top"):
            with self.subTest(nav=nav):
                rec = self.scen("nav_" + nav)
                self.assertEqual(rec["mode"], nav)
                self.assertSteps(rec, [("start", "profiles"), ("empty main", "profiles"), ("html", "profiles"),
                                       ("view", "profiles"), ("nav users", "users")])
                self.assertTrue(self.step(rec, "view")["look"])

    def test_miniapp_on_phone(self):
        rec = self.scen("miniapp")
        self.assertEqual((rec["mode"], rec["device"]), ("full", "phone"))
        self.assertIn("web_app_ready", rec["tg"])
        self.assertTrue(rec["signed"], "запросы мини-аппа идут с подписью Telegram")
        self.assertSteps(rec, [("start", "overview"), ("tab profiles", "profiles"), ("title", "profiles"),
                               ("card title", "profiles"), ("empty main", "profiles"), ("view", "profiles"),
                               ("dice", "profiles"), ("view closed", "profiles"), ("more", "profiles"),
                               ("more logs", "logs"), ("search", "logs"), ("search users", "users"),
                               ("tab overview", "overview")])
        self.assertTrue(self.step(rec, "view")["look"])
        self.assertTrue(self.step(rec, "dice")["look"])
        self.assertFalse(self.step(rec, "view closed")["look"])
        # Лист «Ещё» открывается кнопкой и закрывается переходом в раздел.
        self.assertTrue(self.step(rec, "more")["more"])
        self.assertFalse(self.step(rec, "more logs")["more"])
        self.assertTrue(self.step(rec, "search")["cmd"])
        self.assertFalse(self.step(rec, "search users")["cmd"])

    # Команды SSH и CLI в окне человека не нужны: ни действий моста, ни установщика, ни ключей командной строки.
    CLI_RE = re.compile(r"change-mode|share-create|page-create|install\.sh|--action|goamnezia\s+-|sudo\s")

    def test_page_refusal_comes_before_the_lifetime_and_offers_the_temporary_link(self):
        """NC-09: без сайта на домене окно «Страница подключения» сразу говорит, что открыть её негде, — без выбора
        срока, без команд; кнопка называется как на экране («Временная ссылка») и открывает окно временной ссылки."""
        rec = self.scen("page_no_domain")
        dlg = rec["dlg"]
        self.assertEqual(dlg["title"], "Страницу открыть негде")
        self.assertFalse(dlg["ttl"], "до отказа срок не спрашивается")
        self.assertEqual(rec["creates"], 0)
        self.assertIn("«Временная ссылка»", dlg["text"])
        self.assertNotIn("Скачать по ссылке", dlg["text"])
        self.assertIsNone(self.CLI_RE.search(dlg["text"]), dlg["text"])
        self.assertEqual(dlg["foot"], ["Закрыть", "Временная ссылка"])
        self.assertEqual(rec["after"], ["Временная ссылка · Анна"])

    def test_subscription_block_creates_and_shows_addresses_by_format(self):
        """Подписка в карточке: «Создать» — окно с объяснением, затем адреса по форматам со «Скопировать» у каждого;
        создание шлёт только имя; команд моста на экране нет."""
        rec = self.scen("sub_create")
        # Подпись блока — «Подписка», без «(Clash, роутеры)»: в подписке теперь и приложение Amnezia, и роутер, и incy
        self.assertTrue(rec["before"]["text"].startswith("ПодпискаСоздать"), rec["before"]["text"][:60])
        self.assertNotIn("(Clash", rec["before"]["text"])
        self.assertIn("Подписки нет", rec["before"]["text"])
        self.assertEqual(rec["before"]["btns"], ["Создать"])
        self.assertEqual(rec["confirm"]["title"], "Подписка · Анна")
        self.assertIn("Clash Verge Rev", rec["confirm"]["text"])
        self.assertEqual(rec["creates"], [{"name": "anna"}])
        self.assertEqual(rec["dlg"]["title"], "Подписка готова")
        self.assertEqual([c.rsplit("/", 1)[1] for c in rec["codes"]], ["mihomo.yaml", "awg31.conf", "awg31.vpn", "all.vpn"])
        self.assertEqual(rec["copies"], 4)
        for words in ("Clash Verge Rev, FlClash", "Роутер (Keenetic, OpenWrt)", "Приложение Amnezia", "Кто знает адрес"):
            self.assertIn(words, rec["dlg"]["text"])
        self.assertIsNone(self.CLI_RE.search(rec["dlg"]["text"].replace("sub-create", "")), rec["dlg"]["text"])

    def test_subscription_in_the_miniapp_on_a_phone(self):
        rec = self.scen("sub_miniapp")
        self.assertEqual(rec["device"], "phone")
        self.assertEqual(rec["box"]["btns"], ["Показать адреса", "Перевыпустить", "Отозвать"])
        # Кнопки — обычные кнопки панели: на телефоне правило [data-device="phone"] button даёт им 44 px
        self.assertTrue(all("ga-btn" in c for c in rec["box"]["cls"]), rec["box"]["cls"])
        self.assertIn("clash-verge", rec["box"]["text"])
        self.assertIn("AmneziaWG 3.1", rec["box"]["text"])
        self.assertNotIn("FAKESUBSECRET", rec["box"]["text"], "адреса в карточке нет — только по кнопке")
        self.assertEqual(rec["dlg"]["title"], "Адреса подписки · Анна")
        self.assertEqual(rec["codes"], 4)
        self.assertTrue(rec["signed"])

    def test_subscription_without_a_domain_says_so_and_offers_no_button(self):
        rec = self.scen("sub_no_domain")
        self.assertIn("работает только на сайте с доменом", rec["box"]["text"])
        self.assertEqual(rec["box"]["btns"], [])

    SUB_SECRET = "FAKESUBSECRETFAKESUBSECRETFAKESUBSECRET0123"

    def sub_url(self, suffix):
        return "https://vpn.example.com/s-%s/%s" % (self.SUB_SECRET, suffix)

    def test_subscription_with_clients_is_a_choice_by_app(self):
        """Ответ с полем clients: окно выбора по приложению вместо списка по форматам — слева группы «Обновляется сам» и
        «Разово» с платформами, записи с одним app — одно приложение; справа выбранное; по умолчанию — рекомендованное (не
        первое в списке); адреса — один sub-show по кнопке, переключение приложений и способов моста не зовёт."""
        rec = self.scen("sub_clients")
        self.assertEqual(rec["shows"], [{"name": "anna"}])
        self.assertEqual(rec["shows2"], 1)
        s = rec["start"]
        self.assertEqual(s["title"], "Адреса подписки · Анна")
        self.assertEqual(rec["oldList"], [], "прежнего списка по форматам нет")
        self.assertEqual(s["groups"], ["Обновляется сам", "Разово"])
        self.assertEqual(s["apps"], ["FlClash", "Clash Meta for Android", "Clash Verge Rev", "Clash Party", "incy", "AWG Manager",
                                     "AmneziaVPN", "DefaultVPN", "AmneziaWG", "Keenetic / Netcraze", "WG Tunnel",
                                     "goVLESS, Kaskad, goTelegram"])
        self.assertEqual(s["plats"][0], "Android, Windows, macOS, Linux")
        self.assertEqual(s["plats"][4], "iPhone / iPad, Android, Android TV, Apple TV, macOS")
        self.assertEqual(s["pressed"], ["Clash Verge Rev"])
        self.assertEqual(s["name"], "Clash Verge Rev")
        self.assertEqual(s["chips"], ["рекомендуем"])
        self.assertEqual(s["meta"], "обновляется сам раз в 12 ч · Windows, macOS, Linux")
        self.assertTrue(s["hint"].startswith("Как добавить: «Профили»: вставьте ссылку"), s["hint"])
        self.assertEqual((s["formats"], s["lines"]), ([], []), "одна запись и одна ссылка — без «Формат» и «Линия»")
        self.assertIn("Кто знает адрес — получает ключи этого пользователя", s["text"])
        # Свои часы у FlClash; 0 часов — интервал сервера, {minutes} в подсказке — он же в минутах
        self.assertEqual(rec["flclash"]["meta"], "обновляется сам раз в 24 ч · Android, Windows, macOS, Linux")
        self.assertEqual(rec["flclash"]["pressed"], ["FlClash"])
        self.assertIn("«Автообновление (минут)» — 720", rec["meta"]["hint"])

    def test_subscription_two_ways_in_a_row_and_disabled_qr_by_client_type(self):
        """Решение владельца 04.10: справа — способы в ряд, без россыпи кнопок: «QR-код» и «Скопировать»; «Короткая ссылка»
        зарезервирована (ядро short_url не заполняет) — третьей кнопки нет. «QR-код» по полю qr: адрес и ключ — для сканера в
        приложении, ссылка «в приложение» — для камеры телефона, второй код (qr_alt) — рядом; qr "" — кнопка неактивна с
        причиной по типу приложения. Пока ничего не выбрано — адрес без секрета и подсказка, чем пользоваться."""
        rec = self.scen("sub_clients")
        s = rec["start"]
        self.assertEqual(s["trio"], [["qr", "QR-код", "на компьютере не нужен", "off"],
                                     ["copy", "Скопировать", "ссылку, чтобы вставить", "false"]])
        self.assertEqual(s["value"], "https://vpn.example.com/s-••••/all.yaml")
        self.assertNotIn(self.SUB_SECRET, s["value"])
        # QR серый («на компьютере не нужен») — и подсказка без QR (A3-8)
        self.assertEqual(s["guide"], "Нажмите «Скопировать» и вставьте в приложение.")
        # «Открыть в приложении» — не в ряду, а строкой ниже
        self.assertEqual(s["openText"], "Приложение уже стоит на этом устройстве?Открыть в Clash Verge Rev")
        self.assertTrue(s["open"].startswith("clash-verge://install-config?"), s["open"])
        self.assertEqual(rec["vergeQr"]["trio"], s["trio"], "неактивный QR не нажимается")
        self.assertEqual(rec["flclash"]["trio"][0], ["qr", "QR-код", "для сканера в приложении", "false"])
        self.assertEqual(rec["flclashQr"]["trio"][0][3], "true")
        self.assertEqual(rec["flclashQr"]["qrs"], ["QR ссылки — отсканируйте в приложении"])
        self.assertEqual(rec["flclashQrOff"]["qrs"], [], "второе нажатие прячет QR")
        # incy: qr — ссылка «в приложение» (камерой телефона), qr_alt — адрес (сканером в incy)
        self.assertEqual(rec["incy"]["trio"][0], ["qr", "QR-код", "камерой или сканером incy", "true"])
        self.assertEqual(rec["incy"]["qrs"], ["Камерой телефона: наведите обычную камеру — откроется incy",
                                              "Сканером в incy: наведите сканер приложения на этот код"])
        self.assertTrue(rec["incy"]["open"].startswith("incy://add/https://"), rec["incy"]["open"])
        self.assertEqual(rec["keenetic"]["trio"][0], ["qr", "QR-код", "роутеру не нужен", "off"])
        self.assertEqual(rec["awgm"]["trio"][0], ["qr", "QR-код", "роутеру не нужен", "off"])
        self.assertEqual(rec["wg"]["trio"][0], ["qr", "QR-код", "не для этого приложения", "off"])
        self.assertEqual(rec["go"]["trio"][0], ["qr", "QR-код", "вставляют строкой", "off"])
        for name in ("start", "flclash", "incy", "amnezia", "keenetic", "go", "awgm"):
            self.assertEqual([t[0] for t in rec[name]["trio"]], ["qr", "copy"], "%s: короткой ссылки пока нет" % name)
        self.assertNotIn("Короткая ссылка", rec["start"]["text"])
        # Ссылка «в приложение» не своей схемой (javascript:) строкой «Открыть в …» не рисуется
        self.assertTrue(rec["wg"]["open"].startswith("wg://import?url="), rec["wg"]["open"])
        self.assertIsNone(rec["wg20"]["open"])
        self.assertEqual(rec["after"], 0)

    def test_subscription_copy_follows_the_copy_field(self):
        """«Скопировать» — по полю copy пункта: url — адрес, command — готовую команду, key — ключ линии в key_format (vpn://
        или текст .conf) путём карточки; под рядом — «Скопировано: что. Теперь вставьте … в приложение» с подсказкой
        приложения и само значение (ключ — коротко)."""
        rec = self.scen("sub_clients")
        v = rec["vergeCopy"]
        self.assertEqual(v["trio"][1], ["copy", "Скопировано", "ссылку, чтобы вставить", "true"])
        self.assertEqual(v["value"], self.sub_url("all.yaml"))
        self.assertEqual(v["done"], "Скопировано: ссылка. Теперь вставьте её в Clash Verge Rev: «Профили»: вставьте ссылку в поле "
                                    "«URL профиля» → «Импортировать», затем выберите профиль.")
        a = rec["amneziaCopy"]
        self.assertEqual(a["lines"], ["3.1:false", "2.0:true"])
        self.assertEqual(a["trio"][1], ["copy", "Скопировано", "ключ vpn://, чтобы вставить", "true"])
        self.assertEqual(a["value"], "vpn://FAKEKEY")
        self.assertTrue(a["done"].startswith("Скопировано: ключ vpn://. Теперь вставьте его в AmneziaVPN: «+» и вставьте ключ"), a["done"])
        # AmneziaWG: copy url — ссылка на файл, хоть QR и ключа
        self.assertEqual(rec["awgCopy"]["trio"][1][2], "ссылку, чтобы вставить")
        self.assertEqual(rec["awgCopy"]["value"], self.sub_url("awg31.conf"))
        k = rec["keeneticCopy"]
        self.assertEqual(k["trio"][1], ["copy", "Скопировано", "команду, чтобы вставить", "true"])
        self.assertEqual(k["value"], "interface Wireguard9 wireguard import " + self.sub_url("awg20.conf"))
        self.assertTrue(k["done"].startswith("Скопировано: команда. Теперь вставьте её в Keenetic / Netcraze: KeeneticOS 5.2"), k["done"])
        # Пока не нажали — команда без секрета
        self.assertEqual(rec["keenetic"]["value"], "interface Wireguard9 wireguard import https://vpn.example.com/s-••••/awg20.conf")

    def test_subscription_one_app_with_formats(self):
        """Записи с одним app (AWG Manager: «Все линии .txt», «Ключ vpn://», «Файл .conf») — одно приложение в списке, справа
        «Формат»; у каждого формата — свои «как обновляется», линии, подсказка и что копировать (.conf — текстом ключа)."""
        rec = self.scen("sub_clients")
        self.assertEqual(rec["start"]["apps"].count("AWG Manager"), 1)
        m = rec["awgm"]
        self.assertEqual(m["formats"], ["Все линии .txt:true", "Ключ vpn://:false", "Файл .conf:false"])
        self.assertEqual(m["meta"], "обновляется сам раз в 12 ч · Роутер")
        self.assertEqual(m["lines"], [])
        self.assertEqual(rec["awgmCopy"]["value"], self.sub_url("all.txt"))
        p = rec["awgmVpn"]
        self.assertEqual(p["formats"], ["Все линии .txt:false", "Ключ vpn://:true", "Файл .conf:false"])
        self.assertEqual(p["meta"], "разово: сменится ключ или порт — добавьте заново · Роутер")
        self.assertEqual(p["lines"], ["3.1:true", "2.0:false"])
        self.assertEqual(p["hint"], "Как добавить: AWG Manager: «импортировать ссылку», вставьте ключ и нажмите «Создать».")
        self.assertEqual(rec["awgmVpnCopy"]["value"], "vpn://FAKEKEY")
        c = rec["awgmConfCopy"]
        self.assertEqual(c["formats"], ["Все линии .txt:false", "Ключ vpn://:false", "Файл .conf:true"])
        self.assertEqual(c["trio"][1], ["copy", "Скопировано", "текст .conf, чтобы вставить", "true"])
        self.assertEqual(c["value"], "[Interface] … Endpoint = vpn.example.com:443", "ключ .conf — коротко, приватный ключ не на экране")
        self.assertTrue(c["done"].startswith("Скопировано: текст .conf. Теперь вставьте его в AWG Manager:"), c["done"])

    def test_subscription_key_qr_by_line_is_the_card_path(self):
        """«Разово»: у нескольких линий — выбор линии; QR ключа линии — путём карточки (/api/users/anna/key по профилю
        линии, части листаются); выбор линии оставляет открытым QR и показывает ключ новой линии."""
        rec = self.scen("sub_clients")
        a = rec["amnezia"]
        self.assertEqual(a["meta"], "разово: сменится ключ или порт — добавьте заново · iPhone / iPad, Android, Android TV, Windows, macOS, Linux")
        self.assertEqual(a["lines"], ["3.1:true", "2.0:false"])
        self.assertEqual(a["value"], "https://vpn.example.com/s-••••/awg31.vpn")
        q = rec["amneziaQr"]
        self.assertEqual(q["qrs"], [], "QR — ключа, а не адреса")
        self.assertEqual(q["keyQr"], "QR ключа vpn:// · AmneziaWG 3.1 — отсканируйте в приложении")
        self.assertEqual(q["keyPart"], "часть 1 из 2")
        b = rec["amnezia20"]
        self.assertEqual(b["lines"], ["3.1:false", "2.0:true"])
        self.assertEqual(b["keyQr"], "QR ключа vpn:// · AmneziaWG 2.0 — отсканируйте в приложении")
        self.assertEqual(rec["awg"]["keyQr"], "QR ключа .conf · AmneziaWG 3.1 — отсканируйте в приложении")
        self.assertEqual(rec["awg"]["keyPart"], "один код")
        # awg3 карточка уже взяла при открытии (тот же кэш ключей), за awg2 — один запрос того же пути карточки
        self.assertEqual(rec["keyQueries"], ["?profile=awg2&format=all"])

    def test_subscription_choice_without_recommended_and_in_english(self):
        rec = self.scen("sub_clients_norec_en")
        s = rec["start"]
        self.assertEqual(s["pressed"], ["FlClash"], "рекомендованного нет — первое в списке")
        self.assertEqual(s["groups"], ["Updates itself", "One-time"])
        self.assertEqual(s["meta"], "updates itself every 24 h · Android, Windows, macOS, Linux")
        self.assertTrue(s["hint"].startswith("How to add: Profiles → + → URL"), s["hint"])
        self.assertEqual(s["chips"], [])
        self.assertEqual(s["trio"], [["qr", "QR code", "for the app's scanner", "false"], ["copy", "Copy", "the link, to paste", "false"]])
        self.assertEqual(s["openText"], "Is the app already on this device?Open in FlClash")

    def test_subscription_choice_in_the_miniapp_on_a_phone(self):
        rec = self.scen("sub_clients_miniapp")
        self.assertTrue(rec["sheet"], "на телефоне окно — листом снизу")
        self.assertEqual(rec["start"]["pressed"], ["Clash Verge Rev"])
        self.assertEqual(rec["keenetic"]["trio"], [["qr", "QR-код", "роутеру не нужен", "off"],
                                                   ["copy", "Скопировано", "команду, чтобы вставить", "true"]])
        self.assertEqual(rec["keenetic"]["value"], "interface Wireguard9 wireguard import " + self.sub_url("awg20.conf"))
        self.assertTrue(rec["signed"])

    # ── Короткие ссылки с 2FA через бота (фикстура tests/fixtures/short_links.json) ──
    def test_short_link_is_the_third_way_with_code_swap_and_2fa(self):
        """short_available и ядро с short-ensure: третьей кнопкой в ряду «Короткая ссылка»; нажатие — short-ensure (имя и
        суффикс пункта, один раз на окно): код крупно с адресом, круглая ↻, «Скопировать», плашка 2FA; роутеру — команда с
        короткой ссылкой."""
        rec = self.scen("short_sub")
        self.assertEqual(rec["start"]["trio"][2], ["short", "Короткая ссылка", "набрать руками, 2FA в Telegram", "false"])
        self.assertEqual([x[0] for x in rec["start"]["trio"]], ["qr", "copy", "short"])
        s = rec["short"]
        self.assertEqual(s["trio"][2][3], "true")
        self.assertEqual(s["url"], "vpn.example.com/NV43-9BKX", "код крупно — с адресом сайта")
        self.assertTrue(s["plaque"].startswith("Короткие ссылки работают с 2FA через Telegram-бота."), s["plaque"])
        self.assertEqual(s["swap"], "Заменить короткую ссылку")
        self.assertEqual(s["copy"], "Скопировать")
        self.assertIn("буква O читается как 0", s["how"])
        self.assertIsNone(s["cmd"])
        self.assertEqual(rec["ensures"], [{"name": "anna", "suffix": "all.yaml"}], "второе нажатие — без нового вызова")
        k = rec["keenetic"]
        self.assertEqual(k["cmd"], "interface Wireguard9 wireguard import https://vpn.example.com/NV43-9BKX")
        self.assertEqual(k["copy"], "Скопировать команду")
        self.assertEqual(rec["ensures2"][-1], {"name": "anna", "suffix": "awg20.conf"})
        self.assertNotIn("/s-", k["text"], "полной ссылки рядом с короткой нет")

    def test_short_link_swap_shows_the_number_and_polls_until_the_answer(self):
        """↻ — short-replace-request (who ставит сервер панели, не страница): число крупно и ожидание; опрос short-status раз
        в 3 с, итог — новый код на месте прежнего и «Ссылка заменена»; окно закрыли — опрос кончился."""
        rec = self.scen("short_sub")
        self.assertEqual(rec["asked"], [{"name": "anna", "suffix": "awg20.conf"}])
        w = rec["waiting"]
        self.assertEqual(w["num"], "47")
        self.assertTrue(w["wait"].startswith("Ждём подтверждения · 4:5"), w["wait"])
        self.assertIn("в сообщении «Замена короткой ссылки»", w["swapText"])
        self.assertEqual(rec["afterPending"]["num"], "47", "pending — ждём дальше")
        d = rec["done"]
        self.assertEqual(d["url"], "vpn.example.com/Q8RT-W2KM")
        self.assertEqual(d["cmd"], "interface Wireguard9 wireguard import https://vpn.example.com/Q8RT-W2KM")
        self.assertEqual(d["al"], "Ссылка заменена: новая — выше, прежняя больше не работает.")
        self.assertIsNone(d["num"])
        self.assertEqual(rec["polls"], [{"request": "rq7Kq2mXa"}] * 2)
        self.assertEqual(rec["pollsAfterClose"], 2)
        close = self.scen("short_sub_close")
        self.assertEqual((close["before"], close["after"]), (1, 1), "окно закрыто — опрос замены кончился")

    def test_no_short_link_without_2fa_or_without_the_core(self):
        self.assertEqual(self.scen("short_sub_off")["trio"], ["qr", "copy"], "без short_available — два способа")
        rec = self.scen("short_sub_nocore")
        self.assertEqual(rec["trio"], ["qr", "copy"], "ядро без short-ensure — кнопки нет")
        self.assertEqual(rec["tabs"], ["Пользователи2", "Временные ссылки0"], "и вкладки «Короткие ссылки» нет")

    def test_short_links_tab_awaiting_confirmation_and_bans(self):
        """«Пользователи → Короткие ссылки»: «Ждут подтверждения» — те же кнопки, что в боте (вход — 10 чисел, «Это не я» и
        «↻ Заменить ссылку», замена — без «↻»), нажатие — short-decide (запрос и ответ, who — сервер); баны — код ••••XXXX,
        «Снять» — short-unban с целью; ждущие перечитываются раз в 10 с, пока вкладка открыта, баны — нет."""
        rec = self.scen("short_tab")
        self.assertEqual(rec["tabs"], ["Пользователи2", "Временные ссылки0", "Короткие ссылки"])
        self.assertEqual(rec["callsBefore"], 0, "пока вкладку не открыли — мост не зовём")
        self.assertEqual(rec["tabsAfter"][2], "Короткие ссылки2")
        self.assertEqual(rec["cards"], ["Ждут подтверждения", "Баны и заморозки"])
        self.assertIn("Запасной путь, если Telegram недоступен", rec["desc"])
        self.assertIn("Короткие ссылки работают с 2FA через Telegram-бота", rec["desc"])
        login, swap = rec["reqs"]
        self.assertEqual((login["kind"], login["title"], login["rows"]), ("login", "Вход по короткой ссылке", [10, 2]))
        self.assertEqual(login["keys"], ["47", "12", "85", "33", "61", "90", "28", "74", "19", "56"])
        self.assertEqual(login["tail"], ["Это не я", "↻ Заменить ссылку"])
        self.assertEqual(login["where"], "Москва, Россия · iPhone · Safari")
        self.assertEqual(login["meta"], "IP 109.111.12.34 · Анна · all.yaml")
        self.assertEqual(login["q"], "Какое число на экране?")
        self.assertTrue(login["left"].startswith("осталось 4:5"), login["left"])
        self.assertEqual((swap["kind"], swap["title"], swap["rows"], swap["tail"]), ("replace", "Замена короткой ссылки", [10, 1], ["Это не я"]))
        self.assertEqual(rec["decide"], [{"request": "rqLogin01", "answer": "47"}])
        self.assertEqual(rec["toast"], "Подтверждено")
        self.assertEqual(rec["deny"], {"request": "rqSwap002", "answer": "deny"})
        # Ступень словами (PNL-COS-03): первый раз — сутки, повторно — месяц; «ступень N» больше нет
        self.assertEqual(rec["bans"], [["IP 203.0.113.7", "05.10.2026, 15:00", "подбор: три неверных кода подряд · бан на сутки (первый раз)"],
                                       ["код ••••MXPA", "04.11.2026, 15:00", "три неверных числа подряд · заморозка на месяц (повторно)"]])
        self.assertIn("Адрес закрывается сразу после «Это не я»", rec["bansText"])
        self.assertIn("ссылка замораживается после трёх неверных чисел подряд", rec["bansText"])
        self.assertNotIn("K7Q2MXPA", rec["bansText"], "код целиком на экран не идёт")
        self.assertEqual(rec["unban"], [{"target": "K7Q2MXPA"}])
        self.assertEqual((rec["autoPending"], rec["autoBans"]), (1, 0))
        self.assertEqual(rec["offTab"], 0, "ушли с вкладки — ждущие не опрашиваются")
        mini = self.scen("short_tab_miniapp")
        self.assertEqual(mini["reqs"], [[10, 2], [10, 1]])
        self.assertTrue(mini["signed"])

    def test_short_actions_are_quick_without_the_busy_plaque(self):
        """SL-UX-01: решение и снятие бана — доли секунды; плашки «Выполняется… до 15 минут» нет, даже если ответ медлит."""
        rec = self.scen("short_tab_quick")
        self.assertEqual((rec["decideHeld"], rec["unbanHeld"]), (1, 1), "ответы держит сервер — проверка честная")
        self.assertFalse(rec["decidePlaque"], "short-decide")
        self.assertFalse(rec["unbanPlaque"], "short-unban")

    def test_no_short_links_tab_without_the_bot(self):
        """SL-UX-02: без бота (нет токена или администраторов — tfa сессии) вкладки «Короткие ссылки» нет и мост не зовётся."""
        rec = self.scen("short_tab_nobot")
        self.assertEqual(rec["tabs"], ["Пользователи2", "Временные ссылки0"])
        self.assertEqual(rec["pending"], 0)

    def test_swap_from_awaiting_confirmation_shows_the_new_code(self):
        """SL-UX-06 (живая проверка на 168): «Ждут подтверждения» → «↻ Заменить ссылку» — ссылка менялась, а нового кода не
        было нигде (тост без кода). Теперь — окно «Новая короткая ссылка»: код крупно с адресом сайта, «Скопировать»,
        «Готово»; в тост код не идёт. Решено раньше в другом месте (already) — «Уже решено: …», окна нет."""
        rec = self.scen("short_tab_swap")
        self.assertEqual(rec["sent"], [{"request": "rqLogin01", "answer": "swap"}])
        d = rec["dlg"]
        self.assertIsNotNone(d, "окна с новым кодом нет")
        self.assertEqual(d["title"], "Новая короткая ссылка")
        self.assertEqual(d["url"], "vpn.example.com/Q8RT-W2KM")
        self.assertEqual(d["copy"], ["Скопировать"])
        self.assertEqual(d["foot"], ["Готово"])
        self.assertIn("Анна · all.yaml: ссылка заменена, прежняя больше не работает.", d["text"])
        self.assertNotIn("Q8RT", rec["toast"], "код — только в окне")
        self.assertEqual(rec["closed"], 0)
        self.assertEqual(rec["already"], "Уже решено: Подтверждено")
        self.assertEqual(rec["dlgAfter"], 0)

    def test_logs_say_the_server_clock_zone(self):
        """PNL-COS-03: время в строках журналов — по часам сервера (journalctl), остальная панель — по часам браузера;
        под журналом — по какому поясу (смещение приходит с логами), без смещения — просто «по часам сервера»."""
        msk = self.scen("logs_tz_msk")
        self.assertEqual(msk["lines"], ["12:35:01", "12:34:56"])
        self.assertTrue(msk["foot"].endswith("Время в строках — по часам сервера, UTC+3."), msk["foot"])
        self.assertTrue(self.scen("logs_tz_utc")["foot"].endswith("Время в строках — по часам сервера, UTC."))
        self.assertTrue(self.scen("logs_tz_none")["foot"].endswith("Время в строках — по часам сервера."))

    def test_toast_is_one_node_and_a_live_region(self):
        """PNL-COS-03: «тосты в DOM дважды» — узел тоста один (следующий заменяет прежний); второе «вхождение» — живой
        регион (role=status) для экранного чтеца, его оставляем."""
        rec = self.scen("toast_once")
        self.assertEqual((rec["toasts"], rec["hosts"]), (1, 1))
        self.assertEqual(rec["live"], ["status", "polite"])

    def test_three_users_tabs_fit_one_line_on_a_phone(self):
        """PNL-COS-03: на 375 px «Временные ссылки» и «Короткие ссылки» переносились на вторую строку — на телефоне
        подписи короче, полная — подсказкой; на широком экране — как было."""
        self.assertEqual(self.scen("users_tabs_phone")["tabs"],
                         [["Пользователи", None], ["Временные", "Временные ссылки"], ["Короткие", "Короткие ссылки"]])
        self.assertEqual(self.scen("users_tabs_wide")["tabs"],
                         [["Пользователи", None], ["Временные ссылки", None], ["Короткие ссылки", None]])
        css = read_text(os.path.join(STATIC, "styles.css"))
        rule = re.search(r"\.gx-utabs\.gx-utabs--3 > button \{([^}]*)\}", css).group(1)
        self.assertNotIn("white-space: normal", rule, "подпись не переносится на вторую строку")

    def test_short_pending_poll_is_its_own_round_not_more_often_than_10_s(self):
        """PERF-02 (живая проверка на 168: short-pending — около 1,1 с процессора на 1 vCPU, вкладка перечитывала его на
        каждом обновлении раздела): ждущие — своим кругом, через 10 с после ответа на прошлый опрос; уход из раздела и
        возврат через 3 с их не перечитывает (экран — из прошлого ответа); скрытое окно — пауза, показали — круг идёт
        дальше; своё решение — один внеочередной опрос, следующий — снова через 10 с."""
        rec = self.scen("short_tab_poll")
        self.assertEqual(rec["open"], 1, "открыли вкладку — один опрос")
        self.assertEqual(rec["back"], 1, "обновление раздела через 3 с ждущих не перечитывает")
        self.assertEqual(rec["reqsBack"], 2, "и экран не пустой — из прошлого ответа")
        self.assertEqual(rec["before10"], 1)
        self.assertEqual(rec["after10"], 2)
        self.assertEqual(rec["hidden"], 2, "окно браузера скрыто — опросов нет")
        self.assertEqual(rec["shown"], 3)
        self.assertEqual(rec["decided"], 4, "своё решение — один внеочередной опрос")
        self.assertEqual((rec["decided9"], rec["decided11"]), (4, 5), "после него круг начинается заново")

    def test_cold_overview_shows_a_dash_not_zero_online_and_asks_again_soon(self):
        """PERF-03 (живая проверка на 168: холодный Обзор 3–5 с, плитка «Пользователи» несколько секунд «0 на связи»):
        рукопожатия ещё собираются — «— на связи» и прочерк трафика; Обзор просит трафик без ожидания (wait=0) и
        переспрашивает через 1,5 с — плитки дописаны, дальше обычный круг в 10 с."""
        rec = self.scen("overview_pending")
        self.assertEqual(rec["first"]["online"], "— на связи")
        self.assertEqual(rec["first"]["traffic"], "—")
        self.assertEqual(rec["first"]["overview"], 1)
        self.assertEqual(rec["quick"]["overview"], 2, "переспросил через 1,5 с, а не через 10")
        self.assertEqual(rec["quick"]["online"], "1 на связи")
        self.assertNotEqual(rec["quick"]["traffic"], "—")
        self.assertEqual(rec["settled"]["overview"], 2, "данные дописаны — дальше обычный круг")
        self.assertTrue(rec["quick"]["trafficQuery"])
        self.assertEqual(set(rec["quick"]["trafficQuery"]), {"?wait=0"})
        # PNL-06: подпись плитки целиком и в подсказке (в узкой плитке длинный перевод сокращается многоточием)
        self.assertTrue(rec["kpiLabels"])
        for text, title in rec["kpiLabels"]:
            self.assertEqual(title, text)

    def test_first_overview_frame_does_not_share_the_server_with_other_reads(self):
        """PERF-04 (168: холодный Обзор в браузере 4,2–4,5 с вместо времени одного status — рядом с первым /api/overview
        шли /api/traffic, «Настройки» и backup-last, и на 1 vCPU они делили ядро): пока /api/overview не ответил, страница
        просит только каталог, сессию и вид; трафик и «Настройки» — после первого кадра."""
        rec = self.scen("overview_order")
        self.assertFalse(rec["frameBefore"])
        self.assertIn("GET /api/overview", rec["before"])
        for heavy in ("GET /api/traffic?wait=0", "GET /api/settings", "POST /api/action/backup-last", "GET /api/logs/status"):
            self.assertNotIn(heavy, rec["before"], heavy)
        self.assertTrue(rec["frame"])
        after = rec["after"]
        self.assertGreater(after.index("GET /api/traffic?wait=0"), after.index("GET /api/overview"))
        self.assertIn("GET /api/settings", after)

    def test_slow_bits_wait_for_the_catalog_and_show_no_busy_banner(self):
        """PNL-05 (168: на первом входе плашка «Выполняется: последняя копия» с 0,65 до 2,7 с): без каталога backup-last
        считался изменяющим действием. Теперь медленные части ждут каталог, и backup-last идёт как чтение — без плашки."""
        rec = self.scen("overview_catalog_late")
        self.assertEqual(rec["before"], [])
        self.assertIn("/api/action/backup-last", rec["after"])
        self.assertEqual(rec["busy"], 0)

    def test_miniapp_overview_follows_the_same_order(self):
        """Мини-приложение Telegram — тот же app.js: холодный Обзор на телефоне просит до первого кадра только лёгкое,
        пишет «— на связи» и через 1,5 с — число."""
        rec = self.scen("overview_miniapp")
        self.assertEqual(rec["page"], "overview")
        for heavy in ("GET /api/traffic?wait=0", "GET /api/settings", "POST /api/action/backup-last"):
            self.assertNotIn(heavy, rec["before"], heavy)
        self.assertEqual(rec["first"], "— на связи")
        self.assertEqual(rec["quick"], "1 на связи")
        self.assertIn("GET /api/settings", rec["after"])

    def test_quick_overview_retry_survives_a_pause(self):
        """PNL-05: переспрос Обзора в паузе (скрытая вкладка) повторяется через секунду — после паузы прочерк сменяется
        числом сразу, а не через 10 с."""
        rec = self.scen("overview_quick_paused")
        self.assertEqual(rec["hidden"], 1, "в паузе не переспрашивает")
        self.assertEqual(rec["shown"], 2, "после паузы — переспрос в пределах секунды")
        self.assertEqual(rec["online"], "1 на связи")

    def test_traffic_tile_label_and_number_do_not_change_when_the_tally_arrives(self):
        """PNL-07 (168: первый кадр — «Трафик за всё время» и счёт от нуля при известном числе, через 0,35 с — «за 30
        дней»): плитка — из traffic_kpi ответа Обзора; подпись, число и столбики первого кадра (учёт /api/traffic ещё не
        пришёл) — те же, что после него."""
        rec = self.scen("overview_label_steady")
        self.assertEqual(rec["trafficAsked"], 1, "учёт запрошен и держится тестом — первый кадр без него")
        want = {"label": "Трафик за 30 дней", "value": "4,5", "bars": 3}
        self.assertEqual(rec["first"], want)
        self.assertEqual(rec["firstLater"], want, "число известно — не бежит от нуля")
        self.assertEqual(rec["after"], want)
        # Пока учёт собирается — подпись прошлого периода, а не «за всё время»; пришёл другой — он и запомнен.
        pend = self.scen("overview_label_pending")
        self.assertEqual((pend["first"]["label"], pend["first"]["value"]), ("Трафик за месяц", "—"))
        self.assertEqual((pend["later"]["label"], pend["later"]["value"]), ("Трафик за 30 дней", "4,5"))
        self.assertEqual(pend["storedPeriod"], "30d")

    def test_shown_numbers_do_not_count_from_zero_again(self):
        """PNL-07: повторный вход на Обзор рисует прежнюю картину сразу — уже показанные числа встают на место, без счёта
        от нуля; первое значение после прочерка считается от нуля, а перерисовка посреди счёта его продолжает."""
        rec = self.scen("overview_reentry")
        self.assertEqual(rec["before"], ["2", "4,5"])
        self.assertEqual(rec["back"], ["2", "4,5"])
        self.assertEqual(rec["back48"], ["2", "4,5"])
        self.assertEqual(rec["after"], ["2", "4,5"])
        rev = self.scen("overview_reveal")
        num = lambda s: float(s.replace(",", "."))   # noqa: E731
        self.assertEqual(rev["first"], "—")
        self.assertEqual(rev["revealAsk"], 2, "значение пришло с переспросом Обзора")
        self.assertEqual(rev["final"], "4,5")
        self.assertTrue(0 < num(rev["mid"]) < 4.5, rev)
        self.assertTrue(num(rev["mid"]) <= num(rev["afterRedraw"]) < 4.5, f"перерисовка не продолжила счёт: {rev}")

    def test_online_chip_after_idle_is_a_dash_until_a_new_answer(self):
        """PNL-08 (168: «Пользователи → Обзор» после простоя — чип сразу «0 на связи» из прежнего ответа): ответ Обзора
        старше 45 с числом «на связи» не годится — прочерк и без лиц «в сети», пока не придёт новый; моложе — число."""
        rec = self.scen("overview_back_after_idle")
        self.assertEqual(rec["start"][0], "1 на связи")
        self.assertGreater(rec["start"][1], 0)
        self.assertEqual(rec["soon"][0], "1 на связи", "20 с — прежний ответ ещё годен")
        self.assertEqual(rec["idle"], ["— на связи", 0])
        self.assertEqual(rec["fresh"][0], "1 на связи")
        self.assertGreater(rec["fresh"][1], 0)

    def test_zero_online_from_the_server_is_zero(self):
        """PNL-08: users_online 0 — ноль, а не число «в сети» из прежнего учёта трафика (было `users_online ||
        onlineUsers.length`), и лиц «в сети» при нуле нет."""
        rec = self.scen("overview_online_zero")
        self.assertEqual(rec["chip"], "0 на связи")
        self.assertEqual(rec["avatars"], 0)

    # Служебный код в скобках — «(short_pending)», «(rc=1)»: людям его не показываем (SL-UX-03)
    SERVICE_CODE_RE = re.compile(r"\([A-Za-z0-9_.:=-]+\)")

    def test_short_refusals_are_words_without_the_service_code(self):
        """SL-UX-03 (настоящий бот на 168: «… попробуйте через 5 мин (short_pending)»): отказ short-* в панели — словами
        ядра (они говорят, что делать), без кода; слов нет или отказ незнакомый — «не выполнено» и «повторите…»."""
        rec = self.scen("short_err")
        words = "По этой короткой ссылке уже ждут подтверждения в боте — попробуйте через 5 мин"
        self.assertEqual(rec["swap"], words)
        self.assertEqual(rec["ensure"], "Действие не выполнено. Повторите через минуту; не выходит — посмотрите «Логи».")
        tab = self.scen("short_tab_err")
        self.assertEqual(tab["bans"], "Короткие ссылки: действие не выполнено. Повторите через минуту; не выходит — посмотрите «Логи».")
        self.assertEqual(tab["toast"], "Запрос подтверждения не найден: его не было или он закрыт больше часа назад")
        for text in (rec["swap"], rec["ensure"], tab["bans"], tab["toast"]):
            self.assertIsNone(self.SERVICE_CODE_RE.search(text), text)
            for code in ("short_pending", "short_failed", "write_failed", "short_request_not_found"):
                self.assertNotIn(code, text)

    def test_page_with_a_domain_asks_the_lifetime_and_the_core_refusal_speaks_the_same_words(self):
        rec = self.scen("page_domain_refused")
        self.assertTrue(rec["ttl"], "сайт на домене есть — срок спрашивается, как прежде")
        self.assertEqual(rec["creates"], 1)
        self.assertIn("Страницу открыть негде", rec["dlg"]["text"])
        self.assertIn("«Временная ссылка»", rec["dlg"]["text"])
        self.assertIsNone(self.CLI_RE.search(rec["dlg"]["text"]), rec["dlg"]["text"])
        self.assertEqual(rec["dlg"]["alertBtns"], ["Временная ссылка"])

    def test_service_states_agree_with_the_service_name(self):
        """NC-14: «Бот: не установлен», «Панель: остановлена», «Сайт: не настроен», «Агенты: не установлены» — а не
        «Бот: не установлена» у всех подряд; по-английски форма одна."""
        self.assertEqual(self.scen("services_ru")["rows"], [
            "Бот: не установлен", "Панель: остановлена", "Сайт: не настроен", "Docker: работает",
            "Агенты: не установлены", "Страницы подключения: остановлены"])
        self.assertEqual(self.scen("services_en")["rows"], [
            "Bot: not installed", "Panel: stopped", "Site: not set up", "Docker: running",
            "Agents: not installed", "Connection pages: stopped"])

    def test_heat_map_says_installation_on_a_fresh_install(self):
        """NC-14: часы копятся с установки — «через 7 дней после установки»; профили стоят давно — «после обновления»."""
        new, upgrade = self.scen("heat_new")["note"], self.scen("heat_upgrade")["note"]
        self.assertIn("через 7 дней после установки", new)
        self.assertNotIn("обновления", new)
        self.assertIn("через 7 дней после обновления", upgrade)

    def test_users_section_has_one_name_on_phone_and_desktop(self):
        """NC-14: «Люди» во вкладке телефона и «Пользователи» в меню и заголовке — теперь одно слово везде."""
        for lang, word in (("ru", "Пользователи"), ("en", "Users")):
            with self.subTest(lang=lang):
                rec = self.scen("tabs_" + lang)
                self.assertEqual(rec["tabs"][1], word)
                self.assertEqual(rec["title"], word)
                self.assertNotIn("Люди", rec["tabs"])
                self.assertNotIn("People", rec["tabs"])
        self.assertEqual(self.scen("tabs_ru")["tabs"], ["Обзор", "Пользователи", "Протоколы", "Ещё"])
        self.assertEqual(self.scen("tabs_en")["tabs"], ["Overview", "Users", "Protocols", "More"])
        self.assertTrue(self.scen("side_ru")["side"][1].startswith("Пользователи"))

    def test_disk_warning_follows_the_core_level(self):
        """OPS-01: ok и нет данных — тишина; low — жёлтая плашка, critical — красная (role=alert): сколько свободно,
        что будет и что сделать, кнопки «Логи» и «Бэкапы» ведут в свои разделы; RU/EN. Между числом и единицей —
        неразрывный пробел (fmtBytes)."""
        self.assertEqual(self.scen("ops_ok")["alerts"], [])
        self.assertEqual(self.scen("ops_null")["alerts"], [])
        low, = self.scen("ops_low")["alerts"]
        self.assertEqual((low["title"], low["tone"], low["role"]), ("Мало места на диске: свободно 900\u00a0МБ (занято 96%)", "warning", "status"))
        self.assertIn("сократите журналы в «Логах» и удалите лишние копии в «Бэкапах»", low["text"])
        self.assertIn("«Обслуживание → Диагностика»", low["text"])
        self.assertEqual(low["btns"], ["Логи", "Бэкапы"])
        crit, = self.scen("ops_critical")["alerts"]
        self.assertEqual((crit["title"], crit["tone"], crit["role"]), ("Диск почти полон: свободно 300\u00a0МБ (занято 99%)", "danger", "alert"))
        self.assertIn("Не снимутся копии", crit["text"])
        en, = self.scen("ops_low_en")["alerts"]
        self.assertEqual((en["title"], en["btns"]), ("Low disk space: 900\u00a0MB free (96% used)", ["Logs", "Backups"]))
        self.assertSteps(self.scen("ops_low_btns"), [("backups", "backups"), ("logs", "logs")])

    def test_rollback_banner_and_got_it_that_sticks(self):
        """OPS-02 на Обзоре: «Обновление до 0.5.1 не удалось — вернули 0.5.0, данные восстановлены» с «Понятно».
        Нажали — плашки нет ни сразу, ни после перехода и автообновления, ни после перезагрузки; новый откат — снова."""
        rec = self.scen("ops_rb")
        banner, = rec["before"]
        self.assertEqual(banner["title"], "Обновление до 0.5.1 не удалось — вернули 0.5.0, данные восстановлены")
        self.assertEqual((banner["tone"], banner["btns"]), ("warning", ["Понятно"]))
        self.assertIn("Причина: перенос данных на новую версию оборвался с ошибкой.", banner["text"])
        self.assertIn("«Обслуживание → Обновить goAmnezia»", banner["text"])
        self.assertEqual(rec["after"], [])
        self.assertEqual(rec["later"], [])
        self.assertTrue(rec["seen"].endswith("|0.5.1"), rec["seen"])
        self.assertEqual(self.scen("ops_rb_reload")["alerts"], [])
        self.assertEqual(len(self.scen("ops_rb_new")["alerts"]), 1)
        self.assertEqual(self.scen("ops_rb_old")["alerts"], [], "неделю от отката, как шапка SSH-меню")
        en, = self.scen("ops_rb_en")["alerts"]
        self.assertEqual((en["title"], en["btns"]), ("The update to 0.5.1 failed — 0.5.0 is back, the data is restored", ["Got it"]))

    def test_rollback_without_data_or_code_is_red_and_says_what_to_do(self):
        data, = self.scen("ops_rb_data")["alerts"]
        self.assertEqual(data["title"], "Обновление до 0.5.1 не удалось — вернули 0.5.0, но данные из копии не вернулись")
        self.assertEqual((data["tone"], data["btns"]), ("danger", ["Понятно", "К бэкапам"]))
        self.assertIn("preupgrade_0.5.1_20260928-100000.tar.gz", data["text"])
        stuck, = self.scen("ops_rb_stuck")["alerts"]
        self.assertEqual(stuck["title"], "Обновление до 0.5.1 не удалось, и прежнюю версию вернуть не вышло")
        self.assertIn("Причина: новая версия не ответила на проверку состояния.", stuck["text"])
        self.assertEqual(stuck["tone"], "danger")

    def test_miniapp_overview_shows_both(self):
        rec = self.scen("ops_miniapp")
        self.assertSteps(rec, [("start", "overview")])
        self.assertEqual([a["title"] for a in rec["alerts"]], [
            "Обновление до 0.5.1 не удалось — вернули 0.5.0, данные восстановлены",
            "Диск почти полон: свободно 300\u00a0МБ (занято 99%)"])

    def test_docker_old_service_line_and_restart_with_a_question(self):
        """DK-02: пакет Docker обновлён, а служба старая — строка на Обзоре (версии, с какого дня, «перезапустит сама»)
        с кнопкой «Перезапустить Docker»; кнопка — вопрос «VPN пропадёт на 10–30 секунд у всех», и только
        «Перезапустить» зовёт мост docker-restart (confirm — имя действия); итог — тостом. Служба свежая — строки нет."""
        rec = self.scen("dk_overview")
        line, = [a for a in rec["alerts"] if a["title"] == "Docker обновлён, а служба старая"]
        self.assertEqual((line["tone"], line["btns"]), ("warning", ["Перезапустить Docker"]))
        for want in ("до 29.1.3", "(24.0.7)", "Пакет обновлён 01.10.2026", "перезапустит её сама, когда к VPN никто не будет подключён"):
            self.assertIn(want, line["text"])
        self.assertEqual(rec["dialog"]["title"], "Перезапустить Docker?")
        self.assertIn("VPN пропадёт на 10–30 секунд у всех", rec["dialog"]["text"])
        self.assertEqual(rec["dialog"]["foot"], ["Отмена", "Перезапустить"])
        self.assertEqual(rec["before"], 0, "до ответа на вопрос мост не зовётся")
        self.assertEqual(rec["calls"], [{"confirm": "docker-restart"}])
        self.assertIn("Docker перезапущен", rec["toast"])
        self.assertIn("29.1.3", rec["toast"])
        self.assertEqual(self.scen("dk_overview_cancel")["calls"], [], "«Отмена» — Docker не трогается")
        self.assertEqual([a for a in self.scen("dk_overview_fresh")["alerts"] if "Docker" in a["title"]], [])
        failed, = [a for a in self.scen("dk_overview_failed")["alerts"] if "Docker" in a["title"]]
        self.assertIn("Прошлая попытка", failed["text"])
        en, = [a for a in self.scen("dk_overview_en")["alerts"] if "Docker" in a["title"]]
        self.assertEqual((en["title"], en["btns"]), ("Docker was updated, but the service is old", ["Restart Docker"]))
        self.assertTrue([a for a in self.scen("dk_overview_miniapp")["alerts"] if a["title"] == "Docker обновлён, а служба старая"],
                        "мини-апп — тот же Обзор")

    def test_docker_restart_in_settings_and_diagnostics(self):
        """DK-02: «Перезапустить Docker» — в «Настройках → О программе» рядом с диагностикой и у строки «служба старая»
        в «Диагностике» (вместо голой команды); оба — через тот же вопрос."""
        rec = self.scen("dk_settings")
        self.assertIsNotNone(rec["sec"])
        self.assertEqual(rec["sec"]["btns"], ["Перезапустить Docker"])
        self.assertIn("10–30 секунд у всех", rec["sec"]["text"])
        self.assertEqual(rec["dialog"]["title"], "Перезапустить Docker?")
        self.assertEqual(rec["calls"], [{"confirm": "docker-restart"}])
        rec = self.scen("dk_diag")
        self.assertIsNotNone(rec["row"])
        self.assertEqual(rec["row"]["btns"], ["Перезапустить Docker"])
        self.assertFalse(rec["row"]["code"], "кнопка вместо голой команды")
        for want in ("29.1.3", "24.0.7", "01.10.2026"):
            self.assertIn(want, rec["row"]["text"])
        self.assertEqual(rec["calls"], [{"confirm": "docker-restart"}])

    def test_no_null_text_on_screen(self):
        """append(null) браузер вставляет текстом «null»: хвост шапки (туннель или «я») и окно копии приложения."""
        pages = ["overview", "users", "profiles", "traffic", "backups", "logs", "agents", "settings"]
        for who, tail in (("tunnel", {"tunnel": True, "me": False}), ("code", {"tunnel": False, "me": True})):
            with self.subTest(who=who):
                rec = self.scen("stray_" + who)
                self.assertSteps(rec, [(p, p) for p in pages])
                self.assertEqual(rec["pages"], {p: [] for p in pages})
                self.assertEqual(rec["tail"], tail)
                self.assertTrue(rec["mirrorDialog"])
                self.assertEqual(rec["dialog"], [])


# Ускорение AmneziaWG (модуль ядра, KMOD_PLAN этап Б, CONTRACT §4.7a): карточка на «Протоколах» и в мини-аппе. Сервер панели
# подменён фазами: состояние kmod-status и действие в фоне (job) сцена переключает сама — опросов может быть сколько угодно.
KMOD_JS = r"""
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
const ok = (data) => ({ status: 200, body: { ok: true, data } });
const TS = "2026-10-08T10:00:00Z";
const LABEL = "3.1.20260906+kaskad-k31.1";
const ENG = (e, w) => ({ awg2: { engine: e, want: w }, awg3: { engine: e, want: w } });
const KS = (o = {}) => ({ loaded: false, version: "", patched: false, warn: false, state: "off", code: "", reason: "", kernel: "6.8.0-36-generic", want: "",
  installed: false, installed_version: "", owner: "", label: LABEL, profiles: ENG("program", ""), progress: null, selftest: null, failed: null, packages: [], ...o });
const OFF = KS();
const READY = KS({ loaded: true, version: LABEL, patched: true, state: "ready", installed: true, installed_version: LABEL, owner: "goamnezia", want: "program",
  profiles: ENG("program", "program") });
const ON = KS({ loaded: true, version: LABEL, patched: true, state: "on", installed: true, installed_version: LABEL, owner: "goamnezia", want: "kernel",
  profiles: ENG("kernel", "kernel"), packages: ["dkms", "gcc-13", "linux-headers-6.8.0-36-generic"] });
const FB_REASON = "нет модуля для ядра 6.8.0-146-generic — профили работают программой; пересобрать? (Обслуживание → Ускорение AmneziaWG → 3)";
const FALLBACK = KS({ state: "fallback", code: "no_module_for_kernel", kernel: "6.8.0-146-generic", want: "kernel", owner: "goamnezia",
  profiles: ENG("program", "kernel"), reason: FB_REASON });
const BUSY = (action, step, text) => KS({ state: "busy", code: action, progress: { action, step, text, at: TS, started: TS } });
const CHECK = { ok: true, code: "", reason: "", arch: "x86_64", virt: "none", secure_boot: false, disk_free_mb: 9500, kernel: "6.8.0-36-generic", kernel_ok: true,
  headers: { installed: false, candidate: "6.8.0-36.36" }, module: { loaded: false, version: "", installed: false, ko_version: "", owner: "" }, kaskad: false,
  reuse: false, label: LABEL, plan: { missing: ["dkms", "gcc-13", "linux-headers-6.8.0-36-generic"], install: [], size_mb: 214, kernel: false, gcc: "gcc-13",
    headers_meta: { name: "linux-headers-generic", installed: false, kernel: true, size_mb: 891 } } };
const LXC = { ...CHECK, ok: false, code: "container", plan: null,
  reason: "модуль ядра здесь нельзя: это контейнер LXC, модули ядра в нём не грузятся — оставляю программу amneziawg-go" };
const INSTALL_OK = { action: "kmod-install", ok: true, code: "", message: "Модуль ядра установлен", state: "ready", version: LABEL, kernel: "6.8.0-36-generic",
  owner: "goamnezia", reused: false, packages: ["dkms", "gcc-13", "linux-headers-6.8.0-36-generic"], headers_meta_skipped: "", want: "program",
  selftest: { ok: true, kernel: "6.8.0-36-generic", label: LABEL, at: TS, reason: "", cases: [
    { profile: "awg3", line: "3.1", client: "kernel", ok: true, detail: "" }, { profile: "awg3", line: "3.1", client: "program", ok: true, detail: "" }] } };
const ENGINE_OK = { action: "kmod-engine", ok: true, code: "", message: "Движок профилей сменён", engine: "kernel", want: "kernel", rolled_back: false, profiles: [
  { profile: "awg2", from: "program", to: "kernel", restarted: true, alive_before: 2, returned: 2, return_s: 17, note: "" },
  { profile: "awg3", from: "program", to: "kernel", restarted: true, alive_before: 0, returned: 0, return_s: null, note: "no_clients" }] };
const ENGINE_LOST = { action: "kmod-engine", ok: false, code: "kmod_clients_lost", engine: "kernel", want: "program", rolled_back: true,
  message: "Профиль awg2: из 3 человек на связи за 3 мин не вернулся ни один — сервер откатан на прежний движок",
  profiles: [{ profile: "awg2", from: "program", to: "kernel", restarted: true, alive_before: 3, returned: 0, return_s: null, note: "lost" }] };
const REMOVE_OK = { action: "kmod-remove", ok: true, code: "", message: "Модуль ядра удалён", removed: true, kept_for_neighbour: false,
  packages: ["dkms", "gcc-13", "linux-headers-6.8.0-36-generic"], command: "apt remove dkms gcc-13 linux-headers-6.8.0-36-generic" };
const JOB = (op, done, steps, step = "") => ({ id: "j1", op, done, step, elapsed: done ? 95 : 3, ago: done ? 1 : 0, steps });
function server(first) {
  const S = { v: first, posts: [], checks: 0, check: CHECK };
  S.api = {
    "GET /api/kmod": () => ok({ kmod: S.v.kmod, error: null, job: S.v.job }),
    "POST /api/action/kmod-check": () => { S.checks += 1; return ok({ status: "success", message: "ok", check: S.check }); },
  };
  for (const op of ["enable", "program", "rebuild", "remove"]) {
    S.api[`POST /api/kmod/${op}`] = (body) => { S.posts.push({ op, body }); return ok({ job: JOB(op, false, []) }); };
  }
  return S;
}
// Движок профилей в /api/profiles (profiles-status: engine — фактический, engine_want — выбранный), как у kmod-status.
const engData = (k) => ({ "/api/profiles": (d) => ({ ...d, installed: d.installed.map((p) => ({ ...p, engine: (k.profiles[p.profile] || {}).engine || "",
  engine_want: (k.profiles[p.profile] || {}).want || "" })) }) });
const card = (P) => {
  const c = P.$("#kmCard");
  if (!c) return null;
  return { title: (c.querySelector(".gx-h2") || { textContent: "" }).textContent, text: c.textContent,
    kv: c.querySelectorAll(".gx-kv__row").map((r) => [r.querySelector(".gx-kv__k").textContent, r.querySelector(".gx-kv__v").textContent]),
    btns: c.querySelectorAll(".gx-btns button").map((b) => b.textContent.trim()),
    alerts: c.querySelectorAll(".gx-al").map((a) => a.textContent.trim()),
    busy: c.querySelector(".gx-kmbusy") ? c.querySelector(".gx-kmbusy").textContent : null,
    stat: c.querySelectorAll(".gx-stat__t").map((n) => n.textContent), code: c.querySelectorAll(".gx-code__text").map((n) => n.textContent) };
};
const dlg = (P) => { const d = P.$$(".gx-dlg").pop(); return d ? { title: (d.querySelector(".gx-dlg__title") || { textContent: "" }).textContent, text: d.textContent,
  foot: d.querySelectorAll(".gx-dlg__foot button").map((b) => b.textContent.trim()),
  radio: d.querySelectorAll(".gx-radio__i").map((b) => [b.textContent.trim(), b.getAttribute("aria-checked")]) } : null; };
const toastText = (P) => (P.$("#toastHost .gx-toast") || { textContent: "" }).textContent;
const eng = (P) => Object.fromEntries(P.$$(".gx-pcard[data-profile]").map((c) => [c.getAttribute("data-profile"),
  { line: (c.querySelector(".gx-pcard__eng") || { textContent: "" }).textContent, note: (c.querySelector(".gx-pnote--accent") || { textContent: "" }).textContent }]));
const btn = (P, act) => P.$(`#kmCard [data-act="${act}"]`);
const alerts = (P) => P.$$(".gx-ovalert").map((n) => ({ title: n.querySelector(".gx-ovalert__t").textContent,
  text: (n.querySelector(".gx-ovalert__d") || { textContent: "" }).textContent, btns: n.querySelectorAll(".gx-ovalert__btns button").map((b) => b.textContent.trim()) }));

(async () => {
  // Состояния без действия: модуль не ставился (off), собран (ready), профили на модуле (on), выбран модуль, а модуля под
  // новое ядро нет (fallback), то же по-английски и в мини-аппе на телефоне.
  for (const [name, kmod, extra] of [["km_off", OFF, {}], ["km_ready", READY, {}], ["km_on", ON, {}], ["km_fallback", FALLBACK, {}],
    ["km_on_en", ON, { lang: "en" }], ["km_miniapp", ON, { tg: true, width: 390 }]]) {
    const S = server({ kmod, job: null });
    await scenario(name, { hash: "#profiles", api: S.api, data: engData(kmod), ...extra }, async (P, rec) => {
      if (extra.tg) await P.go("#profiles");
      await P.tick(100);
      rec.page = P.page();
      rec.device = P.doc.documentElement.getAttribute("data-device");
      rec.card = card(P);
      rec.eng = eng(P);
      rec.reads = P.calls("GET", "/api/kmod").length;
    });
  }

  // «Включить»: проверка сервера, окно согласия с пакетами, размером и метапакетом, «Да» — действие в фоне; ход словами ядра
  // опросом, итог — в карточке и тостом, карточки профилей перечитаны.
  {
    const S = server({ kmod: OFF, job: null });
    await scenario("km_enable", { hash: "#profiles", api: S.api }, async (P, rec) => {
      await P.tick(100);
      await P.click(btn(P, "km-enable"));
      await P.tick(100);
      rec.checks = S.checks;
      rec.ask = dlg(P);
      await P.click(P.byText("Поставить и его", ".gx-dlg .gx-radio__i"));
      rec.radio = dlg(P).radio;
      await P.click(P.byText("Да, включить", ".gx-dlg__foot button"));
      await P.tick(100);
      rec.posts = S.posts.slice();
      rec.dlgLeft = !!P.$(".gx-dlg");
      rec.started = card(P);
      S.v = { kmod: BUSY("install", "packages", "Ставлю пакеты для сборки: dkms gcc-13 linux-headers-6.8.0-36-generic"), job: JOB("enable", false, [], "kmod-install") };
      await P.tick(4100);
      rec.busy1 = card(P);
      S.v = { kmod: BUSY("engine", "restart:awg2", "Профиль awg2 → модуль ядра: перезапуск"), job: JOB("enable", false, [INSTALL_OK], "kmod-engine") };
      await P.tick(4100);
      rec.busy2 = card(P);
      const before = P.calls("GET", "/api/profiles").length;
      S.v = { kmod: ON, job: JOB("enable", true, [INSTALL_OK, ENGINE_OK]) };
      await P.tick(4100);
      rec.done = card(P);
      rec.toast = toastText(P);
      rec.profileReads = P.calls("GET", "/api/profiles").length - before;
      const reads = P.calls("GET", "/api/kmod").length;
      await P.tick(9000);
      rec.idleReads = P.calls("GET", "/api/kmod").length - reads;
    });
  }
  {
    const S = server({ kmod: OFF, job: null });
    await scenario("km_decline", { hash: "#profiles", api: S.api }, async (P, rec) => {
      await P.tick(100);
      await P.click(btn(P, "km-enable"));
      await P.tick(100);
      await P.click(P.byText("Нет, оставить программу", ".gx-dlg__foot button"));
      await P.tick(100);
      rec.posts = S.posts.length;
      rec.toast = toastText(P);
    });
  }
  {
    const S = server({ kmod: OFF, job: null });
    S.check = LXC;
    await scenario("km_lxc", { hash: "#profiles", api: S.api }, async (P, rec) => {
      await P.tick(100);
      await P.click(btn(P, "km-enable"));
      await P.tick(100);
      rec.notice = dlg(P);
      rec.posts = S.posts.length;
    });
  }

  // Идёт чужое действие (SSH или бот): ход словами ядра без кнопок, потом — кнопки и перечитанные профили.
  {
    const S = server({ kmod: BUSY("rebuild", "build", "Собираю модуль под ядро 6.8.0-36-generic (DKMS, 1–3 минуты)"), job: null });
    await scenario("km_busy_other", { hash: "#profiles", api: S.api }, async (P, rec) => {
      await P.tick(100);
      rec.busy = card(P);
      const before = P.calls("GET", "/api/profiles").length;
      S.v = { kmod: READY, job: null };
      await P.tick(4100);
      rec.after = card(P);
      rec.profileReads = P.calls("GET", "/api/profiles").length - before;
    });
  }

  // Перевод не удался — никто не вернулся за 3 минуты, сервер откатан: итог понятными словами и строкой по профилю.
  {
    const S = server({ kmod: BUSY("engine", "wait:awg2", "Профиль awg2: жду, что вернутся пользователи (на связи до смены было 3), до 3 минут"),
      job: JOB("enable", false, [INSTALL_OK], "kmod-engine") });
    await scenario("km_rollback", { hash: "#profiles", api: S.api }, async (P, rec) => {
      await P.tick(100);
      rec.busy = card(P);
      S.v = { kmod: READY, job: JOB("enable", true, [INSTALL_OK, ENGINE_LOST]) };
      await P.tick(4100);
      rec.done = card(P);
      rec.toast = toastText(P);
    });
  }

  // «Вернуть программу»: вопрос (отмена — ничего), затем действие в фоне с confirm.
  {
    const S = server({ kmod: ON, job: null });
    await scenario("km_program", { hash: "#profiles", api: S.api, data: engData(ON) }, async (P, rec) => {
      await P.tick(100);
      await P.click(btn(P, "km-program"));
      rec.ask = dlg(P);
      await P.click(P.byText("Отмена", ".gx-dlg__foot button"));
      rec.cancelPosts = S.posts.length;
      await P.click(btn(P, "km-program"));
      await P.click(P.byText("Вернуть программу", ".gx-dlg__foot button"));
      await P.tick(100);
      rec.posts = S.posts.slice();
      rec.busy = card(P);
    });
  }

  // Выбран модуль, а модуля под новое ядро нет: первым — «Пересобрать» с вопросом.
  {
    const S = server({ kmod: FALLBACK, job: null });
    await scenario("km_rebuild", { hash: "#profiles", api: S.api }, async (P, rec) => {
      await P.tick(100);
      await P.click(btn(P, "km-rebuild"));
      rec.ask = dlg(P);
      await P.click(P.byText("Пересобрать", ".gx-dlg__foot button"));
      await P.tick(100);
      rec.posts = S.posts.slice();
    });
  }

  // «Удалить модуль»: вопрос с предупреждением, действие в фоне, итог — и команда удаления пакетов из ответа.
  {
    const S = server({ kmod: ON, job: null });
    await scenario("km_remove", { hash: "#profiles", api: S.api }, async (P, rec) => {
      await P.tick(100);
      await P.click(btn(P, "km-remove"));
      rec.ask = dlg(P);
      await P.click(P.byText("Удалить", ".gx-dlg__foot button"));
      await P.tick(100);
      rec.posts = S.posts.slice();
      S.v = { kmod: KS({ want: "program", packages: ON.packages }), job: JOB("remove", true, [REMOVE_OK]) };
      await P.tick(4100);
      rec.done = card(P);
      rec.toast = toastText(P);
    });
  }

  // Обзор: выбран модуль, а профили на программе — строка с причиной (без пути SSH-меню) и кнопкой к карточке.
  {
    const S = server({ kmod: FALLBACK, job: null });
    const ov = { "/api/overview": (d) => ({ ...d, kmod: { loaded: false, version: "", patched: false, warn: false, state: "fallback", code: "no_module_for_kernel", reason: FB_REASON } }) };
    await scenario("km_overview", { hash: "#overview", api: S.api, data: ov }, async (P, rec) => {
      await P.tick(100);
      rec.alerts = alerts(P);
      await P.click(P.byText("Ускорение AmneziaWG", ".gx-ovalert__btns button"));
      await P.tick(200);
      rec.page = P.page();
      rec.card = !!P.$("#kmCard");
    });
  }

  // Ядро старше 1.0.13 (в каталоге нет kmod-*): карточки нет, /api/kmod не спрашивается.
  await scenario("km_old_core", { hash: "#profiles", data: { "/api/actions": (d) => ({ ...d,
    actions: Object.fromEntries(Object.entries(d.actions).filter(([k]) => !k.startsWith("kmod-"))) }) } }, async (P, rec) => {
    await P.tick(100);
    rec.card = !!P.$("#kmCard");
    rec.reads = P.calls("GET", "/api/kmod").length;
    rec.cards = P.$$(".gx-pcard").length;
  });

  console.log(JSON.stringify(R));
})().catch((e) => { console.log(JSON.stringify({ __fatal: String((e && e.stack) || e) })); });
"""


@unittest.skipUnless(NODE, "нет node 18+ — панель в игрушечном DOM не проверить")
class PanelKmodDomTests(unittest.TestCase):
    """Ускорение AmneziaWG (модуль ядра) в панели и мини-аппе: карточка на «Протоколах» по состоянию kmod-status, окно
    согласия с тем, что поставит apt, действия в фоне с ходом словами ядра, откат и удаление понятными словами, движок на
    карточках профилей, строка на Обзоре. Тексты — те же, что в SSH-меню (lib/lang/ru.d/kmod.sh)."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="goamnezia-panel-kmod-") as tmp:
            script = os.path.join(tmp, "kmod.js")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(KMOD_JS)
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

    def test_states_speak_ssh_words_and_offer_buttons_by_state(self):
        off = self.scen("km_off")
        card = off["card"]
        self.assertIsNotNone(card, "карточка на «Протоколах»")
        self.assertEqual(card["title"], "Ускорение AmneziaWG (модуль ядра)")
        self.assertIn("По умолчанию — программа amneziawg-go в Docker", card["text"])
        self.assertEqual(card["kv"][:2], [["Сейчас", "программа amneziawg-go в Docker"], ["Ядро", "6.8.0-36-generic"]])
        self.assertIn(["Профили", "AmneziaWG 3.1 — программа amneziawg-go · AmneziaWG 2.0 — программа amneziawg-go"], card["kv"])
        self.assertIn("Модуль ядра не ставился.", card["text"])
        self.assertEqual(card["btns"], ["Включить"])
        self.assertEqual(off["eng"]["awg3"]["line"], "Движок: программа amneziawg-go")
        self.assertEqual(off["reads"], 1, "одно чтение при входе, без опроса")
        ready = self.scen("km_ready")["card"]
        self.assertIn("Модуль 3.1.20260906+kaskad-k31.1 собран и загружен, а профили работают программой — «Включить» переведёт их на модуль.", ready["text"])
        self.assertIn("Выбрана программа", ready["text"])
        self.assertEqual(ready["btns"], ["Включить", "Пересобрать", "Удалить модуль"])
        on = self.scen("km_on")
        self.assertEqual(on["card"]["kv"][0], ["Сейчас", "модуль ядра 3.1.20260906+kaskad-k31.1"])
        self.assertEqual(on["card"]["btns"], ["Вернуть программу", "Пересобрать", "Удалить модуль"])
        self.assertEqual({p: e["line"] for p, e in on["eng"].items() if e["line"]}, {"awg3": "Движок: модуль ядра", "awg2": "Движок: модуль ядра"})
        self.assertEqual([e["note"] for e in on["eng"].values()], ["", "", ""], "выбран тот же движок, что работает, — без пометки")
        en = self.scen("km_on_en")["card"]
        self.assertEqual(en["title"], "AmneziaWG acceleration (kernel module)")
        self.assertEqual(en["btns"], ["Back to the program", "Rebuild", "Remove the module"])

    def test_fallback_says_why_with_the_kernel_and_offers_rebuild_first(self):
        rec = self.scen("km_fallback")
        card = rec["card"]
        self.assertEqual(card["kv"][:2], [["Сейчас", "программа amneziawg-go в Docker"], ["Ядро", "6.8.0-146-generic"]])
        self.assertIn("Нет модуля для ядра 6.8.0-146-generic — профили работают программой; пересобрать?", card["alerts"])
        self.assertNotIn("Обслуживание →", card["text"], "путь SSH-меню на панели лишний — кнопка рядом")
        self.assertEqual(card["btns"], ["Пересобрать", "Вернуть программу", "Удалить модуль"])
        # Профили на программе при выбранном модуле: пометка, что сменится при следующем перезапуске
        self.assertEqual(rec["eng"]["awg2"]["line"], "Движок: программа amneziawg-go")
        self.assertIn("Выбран «модуль ядра» — сменится при следующем перезапуске профиля.", rec["eng"]["awg2"]["note"])

    def test_enable_checks_asks_with_the_plan_runs_in_background_and_reports(self):
        rec = self.scen("km_enable")
        self.assertEqual(rec["checks"], 1, "сначала проверка сервера (kmod-check)")
        ask = rec["ask"]
        self.assertEqual(ask["title"], "Ускорить AmneziaWG модулем ядра?")
        for want in ("Поставлю пакеты: dkms, gcc-13, linux-headers-6.8.0-36-generic — около 214 МБ на диске. Сборка — 1–3 минуты.",
                     "kaskad-k31.1", "самопроверка в одноразовом контейнере",
                     "Метапакет заголовков linux-headers-generic потянет новое ядро (около 891 МБ)",
                     "пользователи переподключатся сами за 15–25 секунд"):
            self.assertIn(want, ask["text"])
        self.assertEqual(ask["foot"], ["Нет, оставить программу", "Да, включить"])
        self.assertEqual(ask["radio"], [["Не ставить (рекомендуется)", "true"], ["Поставить и его", "false"]], "по умолчанию — не ставить (KMOD-05)")
        self.assertEqual(rec["radio"], [["Не ставить (рекомендуется)", "false"], ["Поставить и его", "true"]])
        self.assertEqual(rec["posts"], [{"op": "enable", "body": {"confirm": "enable", "headers_meta": "on"}}])
        self.assertFalse(rec["dlgLeft"], "окно согласия закрыто — ход в карточке")
        self.assertIsNotNone(rec["started"]["busy"])
        self.assertIn("Включаю модуль ядра", rec["started"]["busy"])
        self.assertEqual(rec["started"]["btns"], [], "пока идёт действие — без кнопок")
        self.assertIn("Ставлю пакеты для сборки: dkms gcc-13 linux-headers-6.8.0-36-generic", rec["busy1"]["busy"])
        self.assertIn("Профиль awg2 → модуль ядра: перезапуск", rec["busy2"]["busy"])
        done = rec["done"]
        self.assertIsNone(done["busy"])
        self.assertIn("Профили работают: модуль ядра", done["alerts"][-1])
        self.assertIn("AmneziaWG 3.1 (линия 3.1) — клиент на модуле ядра", done["stat"])
        self.assertIn("AmneziaWG 2.0: модуль ядра; вернулись 2 из 2, первый — через 17 с", done["stat"])
        self.assertIn("AmneziaWG 3.1: модуль ядра; пользователей на связи не было — хватает самопроверки и поднятого интерфейса", done["stat"])
        self.assertEqual(done["btns"], ["Вернуть программу", "Пересобрать", "Удалить модуль"])
        self.assertIn("Профили работают: модуль ядра", rec["toast"])
        self.assertGreaterEqual(rec["profileReads"], 1, "движок сменился — карточки профилей перечитаны")
        self.assertEqual(rec["idleReads"], 0, "после итога опрос хода остановлен")

    def test_decline_and_refusal_install_nothing(self):
        rec = self.scen("km_decline")
        self.assertEqual(rec["posts"], 0)
        self.assertIn("Оставляю программу amneziawg-go — ничего не поставлено.", rec["toast"])
        rec = self.scen("km_lxc")
        self.assertEqual(rec["posts"], 0)
        self.assertEqual(rec["notice"]["title"], "Модуль ядра здесь нельзя")
        self.assertIn("Модуль ядра здесь нельзя: это контейнер LXC, модули ядра в нём не грузятся — оставляю программу amneziawg-go", rec["notice"]["text"])

    def test_busy_from_elsewhere_shows_progress_then_buttons(self):
        rec = self.scen("km_busy_other")
        busy = rec["busy"]
        self.assertEqual(busy["kv"][0], ["Сейчас", "идёт установка или перевод"])
        self.assertIn("Идёт установка или перевод модуля ядра", busy["busy"])
        self.assertIn("Собираю модуль под ядро 6.8.0-36-generic (DKMS, 1–3 минуты)", busy["busy"])
        self.assertEqual(busy["btns"], [])
        self.assertIsNone(rec["after"]["busy"])
        self.assertEqual(rec["after"]["btns"], ["Включить", "Пересобрать", "Удалить модуль"])
        self.assertGreaterEqual(rec["profileReads"], 1, "действие кончилось — карточки профилей перечитаны")

    def test_rollback_is_said_in_plain_words(self):
        rec = self.scen("km_rollback")
        self.assertIn("Профиль awg2: жду, что вернутся пользователи", rec["busy"]["busy"])
        done = rec["done"]
        res = done["alerts"][-1]
        self.assertIn("Перевод на «модуль ядра» отменён — профили вернулись на прежний движок", res)
        self.assertIn("из 3 человек на связи за 3 мин не вернулся ни один — сервер откатан на прежний движок", res)
        self.assertIn("AmneziaWG 2.0: за 3 минуты не вернулся ни один из 3 — откат", done["stat"])
        self.assertIn("Перевод на «модуль ядра» отменён", rec["toast"])

    def test_program_and_rebuild_ask_first_and_send_the_confirm(self):
        rec = self.scen("km_program")
        self.assertEqual(rec["ask"]["title"], "Вернуть программу amneziawg-go?")
        self.assertIn("Профили перейдут на программу amneziawg-go по одному: AmneziaWG 3.1 и AmneziaWG 2.0", rec["ask"]["text"])
        self.assertIn("15–25 секунд", rec["ask"]["text"])
        self.assertEqual(rec["cancelPosts"], 0, "«Отмена» — ничего не меняется")
        self.assertEqual(rec["posts"], [{"op": "program", "body": {"confirm": "program"}}])
        self.assertIn("Возвращаю программу amneziawg-go", rec["busy"]["busy"])
        rec = self.scen("km_rebuild")
        self.assertEqual(rec["ask"]["title"], "Пересобрать модуль ядра?")
        self.assertIn("Пересоберу модуль под ядро 6.8.0-146-generic", rec["ask"]["text"])
        self.assertEqual(rec["posts"], [{"op": "rebuild", "body": {"confirm": "rebuild"}}])

    def test_remove_asks_and_names_the_package_command(self):
        rec = self.scen("km_remove")
        self.assertEqual(rec["ask"]["title"], "Удалить модуль ядра AmneziaWG?")
        self.assertIn("Пакеты сборки останутся — назову их.", rec["ask"]["text"])
        self.assertEqual(rec["ask"]["foot"], ["Отмена", "Удалить"])
        self.assertEqual(rec["posts"], [{"op": "remove", "body": {"confirm": "remove"}}])
        done = rec["done"]
        self.assertIn("Модуль ядра удалён, профили работают программой amneziawg-go.", done["alerts"][-1])
        self.assertEqual(done["code"], ["apt remove dkms gcc-13 linux-headers-6.8.0-36-generic"])
        self.assertIn("Пакеты сборки, которые ставила goAmnezia, остались — убрать:", done["text"])
        self.assertEqual(done["btns"], ["Включить"])
        self.assertIn("apt remove dkms gcc-13 linux-headers-6.8.0-36-generic", rec["toast"])

    def test_miniapp_on_a_phone_has_the_same_card(self):
        rec = self.scen("km_miniapp")
        self.assertEqual(rec["page"], "profiles")
        self.assertEqual(rec["device"], "phone")
        self.assertEqual(rec["card"]["btns"], ["Вернуть программу", "Пересобрать", "Удалить модуль"])
        self.assertEqual(rec["card"]["kv"][0], ["Сейчас", "модуль ядра 3.1.20260906+kaskad-k31.1"])
        self.assertEqual(rec["eng"]["awg2"]["line"], "Движок: модуль ядра")

    def test_overview_line_leads_to_the_card_and_old_core_has_none(self):
        rec = self.scen("km_overview")
        line, = [a for a in rec["alerts"] if a["title"] == "Модуль ядра AmneziaWG:"]
        self.assertEqual(line["text"], "Нет модуля для ядра 6.8.0-146-generic — профили работают программой; пересобрать?")
        self.assertEqual(line["btns"], ["Ускорение AmneziaWG"])
        self.assertEqual(rec["page"], "profiles")
        self.assertTrue(rec["card"])
        old = self.scen("km_old_core")
        self.assertFalse(old["card"])
        self.assertEqual(old["reads"], 0)
        self.assertGreater(old["cards"], 0, "карточки профилей на месте")


# QR в цветах темы (app.js: qrModules, qrSvg): модули читаются из PNG qrencode и рисуются заново SVG. Здесь — тот же
# код панели под node: холст-заглушка отдаёт пиксели настоящей картинки qrencode (PNG разбирается здесь же, zlib).
# Итог — строка JSON по каждому случаю.
QR_JS = r"""
const fs = require("fs"), zlib = require("zlib"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8");
const code = src.slice(src.indexOf("  const QR_QUIET = 4;"), src.indexOf("  // Плашка с кодом."));
function decodePng(buf) {
  let pos = 8, w = 0, h = 0, depth = 0, type = 0, pal = null;
  const idat = [];
  while (pos < buf.length) {
    const len = buf.readUInt32BE(pos), kind = buf.toString("ascii", pos + 4, pos + 8), body = buf.subarray(pos + 8, pos + 8 + len);
    if (kind === "IHDR") { w = body.readUInt32BE(0); h = body.readUInt32BE(4); depth = body[8]; type = body[9]; }
    else if (kind === "PLTE") pal = body;
    else if (kind === "IDAT") idat.push(body);
    pos += 12 + len;
  }
  const chans = { 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 }[type];
  const bpp = Math.max(1, (depth * chans) >> 3), stride = (w * depth * chans + 7) >> 3;
  const raw = zlib.inflateSync(Buffer.concat(idat)), out = Buffer.alloc(stride * h);
  for (let y = 0; y < h; y += 1) {
    const f = raw[y * (stride + 1)], line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let i = 0; i < stride; i += 1) {
      const a = i >= bpp ? out[y * stride + i - bpp] : 0, b = y ? out[(y - 1) * stride + i] : 0;
      const c = i >= bpp && y ? out[(y - 1) * stride + i - bpp] : 0;
      const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
      out[y * stride + i] = (line[i] + [0, a, b, (a + b) >> 1, pa <= pb && pa <= pc ? a : (pb <= pc ? b : c)][f]) & 255;
    }
  }
  const rgba = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const k = (y * w + x) * 4;
      if (depth < 8) {
        const bit = x * depth, v = (out[y * stride + (bit >> 3)] >> (8 - depth - (bit & 7))) & ((1 << depth) - 1);
        const g = type === 3 ? null : Math.round((v * 255) / ((1 << depth) - 1));
        rgba.set(type === 3 ? [pal[v * 3], pal[v * 3 + 1], pal[v * 3 + 2]] : [g, g, g], k);
      } else if (type === 3) {
        const v = out[y * stride + x];
        rgba.set([pal[v * 3], pal[v * 3 + 1], pal[v * 3 + 2]], k);
      } else {
        const o = y * stride + x * chans;
        rgba.set(chans >= 3 ? [out[o], out[o + 1], out[o + 2]] : [out[o], out[o], out[o]], k);
      }
      rgba[k + 3] = 255;
    }
  }
  return { w, h, rgba };
}
function run(png) {
  const ctx = { fillStyle: "", fillRect() {}, drawImage() {}, getImageData: () => ({ data: png.rgba }) };
  const node = (tag) => ({ tag, attrs: {}, kids: [], setAttribute(k, v) { this.attrs[k] = String(v); }, append(...k) { this.kids.push(...k); } });
  const box = { document: { createElement: () => ({ width: 0, height: 0, getContext: () => ctx }), createElementNS: (ns, tag) => node(tag) },
    IMG: { naturalWidth: png.w, naturalHeight: png.h }, out: null };
  vm.runInNewContext(code + "\nout = (() => { const m = qrModules(IMG); return { m, svg: m && qrSvg(m, 'QR') }; })();", box);
  return box.out;
}
const dark = (png, x, y) => { const k = (y * png.w + x) * 4; return png.rgba[k] + png.rgba[k + 1] + png.rgba[k + 2] < 384 ? 1 : 0; };
// Копия картинки с перевёрнутыми пикселями [[x, y], …]
const flip = (png, pts) => {
  const p = { ...png, rgba: Uint8ClampedArray.from(png.rgba) };
  pts.forEach(([x, y]) => { const v = dark(png, x, y) ? 255 : 0; p.rgba.set([v, v, v], (y * png.w + x) * 4); });
  return p;
};
const square = (x0, y0) => { const pts = []; for (let y = y0; y < y0 + 6; y += 1) for (let x = x0; x < x0 + 6; x += 1) pts.push([x, y]); return pts; };
const R = {};
process.argv.slice(3).forEach((file, i) => {
  const png = decodePng(fs.readFileSync(file));
  const n = png.w / 6 - 4, r = run(png);
  // Модули — те же, что в картинке (qrencode -s 6 -m 2: центр модуля r,c — пиксель ((2+c)*6+3, (2+r)*6+3)).
  let diff = 0;
  for (let y = 0; y < n; y += 1) for (let x = 0; x < n; x += 1) if (!r.m || r.m.cells[y * n + x] !== dark(png, (2 + x) * 6 + 3, (2 + y) * 6 + 3)) diff += 1;
  // Контур SVG закрашивает ровно тёмные модули, со сдвигом на тихую зону в 4 модуля.
  let svgDiff = -1, viewBox = null;
  if (r.svg) {
    const path = r.svg.kids.find((k) => k.tag === "path"), cover = new Uint8Array(n * n);
    for (const m of path.attrs.d.matchAll(/M(\d+) (\d+)h(\d+)v1h-\3z/g)) for (let j = 0; j < +m[3]; j += 1) cover[(+m[2] - 4) * n + (+m[1] - 4) + j] = 1;
    svgDiff = cover.reduce((s, v, k) => s + (v !== r.m.cells[k] ? 1 : 0), 0);
    viewBox = r.svg.attrs.viewBox;
  }
  // Чужая картинка не разбирается: тёмная точка на поле, модуль не одного цвета, центр поискового узора стёрт целиком.
  R["png" + i] = { n, found: r.m ? r.m.n : null, diff, svgDiff, viewBox,
    marginDot: run(flip(png, [[3, 3]])).m === null,
    moduleSplit: run(flip(png, [[12 + 6 * 9 + 2, 12 + 6 * 9 + 4]])).m === null,
    finderGone: run(flip(png, square(12 + 6 * 3, 12 + 6 * 3))).m === null };
});
R.tiny = run(decodePng(Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==", "base64"))).m;
console.log(JSON.stringify(R));
"""


@unittest.skipUnless(NODE, "нужен node 18+ (на сервере его нет)")
@unittest.skipUnless(shutil.which("qrencode"), "нужен qrencode")
class QrModulesTests(unittest.TestCase):
    """Код в цветах темы несёт те же данные: модули из PNG qrencode переносятся в SVG один в один, а картинка не от
    qrencode (поля не светлые, модуль не ровный квадрат, нет поискового узора, крошечная заглушка) не разбирается —
    панель покажет её саму на плашке."""

    def test_modules_match_the_png(self):
        tmp = tempfile.mkdtemp(prefix="ga-qr-")
        try:
            texts = ["https://vpn.example.com/p-" + "A" * 43 + "/",
                     "[Interface]\nPrivateKey = " + "k" * 44 + "\nAddress = 10.8.3.3/32\nDNS = 1.1.1.1\nJc = 4\n\n[Peer]\n"
                     "PublicKey = " + "p" * 44 + "\nEndpoint = 203.0.113.10:39001\n" + "H1 = 1189433461-1243221730\n" * 6,
                     ("0123456789abcdef-_" * 64)[:1150]]
            files = []
            for i, text in enumerate(texts):
                path = os.path.join(tmp, "%d.png" % i)
                subprocess.run(["qrencode", "-o", path, "-l", "L", "-t", "PNG", "-s", "6", "-m", "2"], input=text.encode(), check=True)
                files.append(path)
            script = os.path.join(tmp, "qr.js")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(QR_JS)
            out = subprocess.run([NODE, script, os.path.join(STATIC, "app.js")] + files, capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr)
            res = json.loads(out.stdout.strip().splitlines()[-1])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        for i in range(len(texts)):
            r = res["png%d" % i]
            with self.subTest(png=i, n=r["n"]):
                self.assertEqual(r["found"], r["n"])
                self.assertEqual((r["diff"], r["svgDiff"]), (0, 0))
                self.assertEqual(r["viewBox"], "0 0 %d %d" % (r["n"] + 8, r["n"] + 8))
                self.assertTrue(r["marginDot"] and r["moduleSplit"] and r["finderGone"])
        self.assertGreater(res["png2"]["n"], 100)     # плотный, как часть vpn://
        self.assertIsNone(res["tiny"])



# ── DUP-LINE-01 (сообщение пользователя 15.10, goAmnezia 1.0.11) ─────────────────────────────────────────────────────
# awg2 — профиль приложения Amnezia, подхвачен: 3.1 на UDP 46567; awg3 — своя 3.1 на 6714 («Установить» ставил линию по
# месту). Две «AmneziaWG 3.1» и ни одной 2.0. Ядро (status, profiles-status): line_shared, imported, title у профилей,
# lines_check {no_20, shared, missing, reline}, free_lines; «Переставить на 2.0» — profile-reline (destructive).
# GA_PANEL_DOM_ROOT — другой корень (index.html, app.js, lib/actions.json): так те же сценарии гоняются на прежнем коде.
DUPLINE_JS = r"""
const { boot } = require(process.argv[2]);
const ROOT_OVERRIDE = process.env.GA_PANEL_DOM_ROOT || undefined;
const R = {};
async function scenario(name, opts, body) {
  const rec = { errors: [] };
  R[name] = rec;
  let P = null;
  try {
    P = await boot({ root: ROOT_OVERRIDE, ...opts });
    await body(P, rec);
  } catch (e) {
    rec.fatal = String((e && e.stack) || e);
  }
  if (P) { rec.errors = P.errors.slice(); P.close(); }
}
const txt = (n) => (n ? n.textContent.replace(/\s+/g, " ").trim() : null);
// title — подпись ядра (на его языке); панель строит свою из line, port, imported.
const T31 = { awg3: "AmneziaWG 3.1 · порт 6714", awg2: "AmneziaWG 3.1 · порт 46567 · из приложения" };
const DUP = {
  awg3: { port: 6714, line: "3.1", peers: 1, subnet: "10.8.3.0/24", line_shared: true, imported: false, title: T31.awg3 },
  awg2: { port: 46567, line: "3.1", peers: 2, subnet: "10.8.1.0/24", container: "amnezia-awg2", preset: "imported", line_shared: true, imported: true, title: T31.awg2 },
};
// steps — у каждого пользователя его действие после перестановки, до неё (ядро: ga_amn_reline_preview)
const PRE = [{ kind: "subscription", text: "С подпиской (vera): обновится сама в течение 12 часов или по «Обновить» в приложении." },
  { kind: "page", text: "Со страницей подключения (boris): открыть страницу заново и импортировать новый ключ." },
  { kind: "key", text: "Выдавали ключ или файл (admin): прежний больше не подключится — выдайте ключ vpn:// или файл .conf заново." }];
const LC = { no_20: true, shared: [{ line: "3.1", profiles: ["awg2", "awg3"] }], missing: ["2.0"],
  reline: { profile: "awg3", from: "3.1", line: "2.0", port: 6714, title: T31.awg3, steps: PRE } };
const FREE = { awg1: "1.5" };
const merge = (list, over) => list.filter((p) => over[p.profile]).map((p) => ({ ...p, ...over[p.profile] }));
const cfgOf = (d, over) => ({ ...d, config: { ...d.config, profiles: Object.fromEntries(Object.entries(d.config.profiles)
  .filter(([k]) => over[k]).map(([k, v]) => [k, { ...v, line: over[k].line, port: over[k].port }])) } });
const dupData = (o = {}) => {
  const over = o.over || DUP, lc = o.lc || LC, free = o.free || FREE;
  return {
    "/api/profiles": (d) => ({ ...d, installed: merge(d.installed, over), udp443: "", lines_check: lc, free_lines: free,
      not_installed: ["awg3", "awg2", "awg1"].filter((p) => !over[p]) }),
    "/api/overview": (d) => ({ ...d, profiles: merge(d.profiles, over), lines_check: lc, free_lines: free }),
    "/api/settings": (d) => cfgOf(d, over),
    ...(o.users ? { "/api/users": o.users } : {}),
  };
};
const STEPS = [{ kind: "users", text: "Ключи на новом профиле выданы: admin." },
  { kind: "subscription", text: "С подпиской (vera): обновится сама в течение 12 часов или по «Обновить» в приложении." },
  { kind: "key", text: "Выдавали ключ или файл (admin): прежний больше не подключится — выдайте ключ vpn:// или файл .conf заново." }];
const SUMMARY = "AmneziaWG 3.1 (awg3) переставлен на AmneziaWG 2.0: порт 6714 и подсеть 10.8.3.0/24 прежние.";
const RELINE_OK = (body) => ({ status: 200, body: { ok: true, data: { status: "success", message: SUMMARY, profile: body.profile,
  line_old: "3.1", line: "2.0", port: 6714, subnet: "10.8.3.0/24", title: "AmneziaWG 2.0 (awg3)", users: ["admin"], failed: [],
  subscription: ["vera"], page: [], key: ["admin"], app: [], update_hours: 12, summary: SUMMARY, steps: STEPS } } });
const ROLLED = "AmneziaWG 3.1 (awg3) с линией 2.0 не встал (install_failed) — вернули прежнюю линию 3.1 на тот же порт и подсеть; людям на нём — новые ключи: admin.";
const RELINE_BACK = () => ({ status: 500, body: { ok: false, error: ROLLED, code: "reline_rolled_back", data: { status: "error", code: "reline_rolled_back", message: ROLLED } } });
const dlg = (P) => { const d = P.$$(".gx-dlg").pop(); return d ? { title: txt(d.querySelector(".gx-dlg__title")), text: txt(d),
  foot: d.querySelectorAll(".gx-dlg__foot button").map((b) => `${txt(b)}${b.disabled ? " [off]" : ""}`), input: !!d.querySelector("input"),
  stat: d.querySelectorAll(".gx-stat__t").map(txt) } : null; };
const footBtn = (P, label) => P.$$(".gx-dlg__foot button").find((b) => txt(b) === label) || null;
const alerts = (P, sel) => P.$$(`${sel} .gx-ovalert`).map((n) => ({ title: txt(n.querySelector(".gx-ovalert__t")),
  text: txt(n.querySelector(".gx-ovalert__d")) || "", btns: n.querySelectorAll(".gx-ovalert__btns button").map(txt) }));
const pcard = (P, pid) => { const c = P.$(`.gx-pcard[data-profile="${pid}"]`);
  return c ? { title: txt(c.querySelector(".gx-pcard__title")), aria: c.getAttribute("aria-label"), text: txt(c),
    btns: c.querySelectorAll("button").map(txt) } : null; };
const typeName = async (P, v) => { const ins = P.$$(".gx-dlg").pop().querySelectorAll("input"); await P.type(ins[ins.length - 1], v); };
const peer = (ip, pk, has) => ({ ip, pubkey: pk, created: "2026-09-01T10:00:00Z", active: true, has_key: has });
const adminApi = (has3, has2, extra = {}) => ({
  "GET /api/users/admin": { status: 200, body: { ok: true, data: { user: { name: "admin", label: "", created: "2026-09-01T10:00:00Z",
    enabled: true, quota_bytes: 0, expires: "", traffic: { rx: 0, tx: 0 }, profiles: ["awg3", "awg2"], state: "on", online: false,
    last_handshake: 0, peers: { awg3: { ...peer("10.8.3.2", "PUBADMIN3", has3), line: "3.1" }, awg2: { ...peer("10.8.1.5", "PUBADMIN2", has2), line: "3.1" } } } } } },
  ...extra });
const adminRow = (P) => P.$$(".gx-urow").find((n) => n.textContent.includes("admin"));

(async () => {
  // «Протоколы»: подписи с портом, строка «нет 2.0», «Переставить на 2.0» в карточке своего awg3 — окно, набор имени,
  // profile-reline с confirm, итог по шагам; у профиля приложения (awg2) кнопки нет.
  await scenario("dup_profiles", { hash: "#profiles", data: dupData(), api: { "POST /api/action/profile-reline": RELINE_OK } }, async (P, rec) => {
    await P.tick(100);
    rec.cards = { awg3: pcard(P, "awg3"), awg2: pcard(P, "awg2"), awg1: pcard(P, "awg1") };
    rec.alerts = alerts(P, "#prAlerts");
    await P.click(P.byText("Переставить на 2.0", '.gx-pcard[data-profile="awg3"] button'));
    rec.ask = dlg(P);
    await typeName(P, "awg3");
    rec.byPlace = dlg(P).foot;
    await typeName(P, "6714");
    rec.armed = dlg(P).foot;
    await P.click(footBtn(P, "Переставить"));
    await P.tick(100);
    rec.calls = P.calls("POST", "/api/action/profile-reline").map((c) => c.body);
    rec.done = dlg(P);
    rec.previews = P.calls("GET", "/api/profiles/awg3/reline-preview").length;
  });
  // S-1, N-3: краткий status (Обзор, «Протоколы») шагов не несёт — окно согласия берёт их отдельным запросом
  // (сервер панели — из полного status) и показывает у каждого пользователя его действие.
  const LC_BRIEF = { ...LC, reline: { ...LC.reline, steps: undefined } };
  await scenario("dup_brief_preview", { hash: "#profiles", data: dupData({ lc: LC_BRIEF }),
    api: { "POST /api/action/profile-reline": RELINE_OK,
      "GET /api/profiles/awg3/reline-preview": { status: 200, body: { ok: true, data: { profile: "awg3", steps: PRE } } } } }, async (P, rec) => {
    await P.tick(100);
    await P.click(P.byText("Переставить на 2.0", '.gx-pcard[data-profile="awg3"] button'));
    await P.tick(50);
    rec.ask = dlg(P);
    rec.previews = P.calls("GET", "/api/profiles/awg3/reline-preview").length;
  });
  // P-7: ключа на профиле нет ни у кого — галочка «всем пользователям», а не «остальным».
  await scenario("dup_keys_none", { hash: "#profiles", data: dupData({ users: (d) => ({ ...d, users: d.users.map((u) => ({ ...u, peers: {} })) }) }),
    api: { "POST /api/action/profile-reline": RELINE_OK } }, async (P, rec) => {
    await P.tick(100);
    await P.click(P.byText("Переставить на 2.0", '.gx-pcard[data-profile="awg3"] button'));
    await P.tick(50);
    const box = P.$$(".gx-dlg").pop().querySelector('.gx-check__i[role="checkbox"]');
    rec.box = box ? { text: txt(box), on: box.getAttribute("aria-checked") } : null;
  });
  // R-2: у кого ключа на профиле нет (masha — только профиль приложения) — галочка в окне согласия, отмеченная по
  // умолчанию; после перестановки ключ на awg3 выдаётся только им. Сняли галочку — не выдаётся.
  const MASHA = { name: "masha", label: "", created: "2026-09-03T10:00:00Z", enabled: true, quota_bytes: 0, expires: "",
    traffic: { rx: 0, tx: 0 }, profiles: ["awg2"], peers: { awg2: { ip: "10.8.1.9", pubkey: "PUBMASHA2", created: "2026-09-03T10:00:00Z",
      active: true, has_key: false } }, state: "on", mark: "●", last_handshake: 0, online: false };
  const ADD_OK = (body) => ({ status: 200, body: { ok: true, data: { status: "success", message: "ok", name: body.name, profile: body.profile } } });
  // DUPLINE-KEYS-01: ключи остальным выдаёт ядро тем же действием (keys_to_others=on) — в ответе others и шаг others.
  const OTHERS_STEP = { kind: "others", text: "Ключи на AmneziaWG 2.0 выданы и остальным пользователям: masha." };
  const RELINE_KEYS = (body) => { const r = RELINE_OK(body); if (body.keys_to_others === "on") {
    r.body.data = { ...r.body.data, others: ["masha"], others_failed: [], steps: [...STEPS, OTHERS_STEP] }; } return r; };
  await scenario("dup_keys", { hash: "#profiles", data: dupData({ users: (d) => ({ ...d, users: [...d.users, MASHA] }) }),
    api: { "POST /api/action/profile-reline": RELINE_KEYS, "POST /api/action/user-add-peer": ADD_OK } }, async (P, rec) => {
    await P.tick(100);
    await P.click(P.byText("Переставить на 2.0", '.gx-pcard[data-profile="awg3"] button'));
    await P.tick(50);
    rec.ask = dlg(P);
    const box = P.$$(".gx-dlg").pop().querySelector('.gx-check__i[role="checkbox"]');
    rec.box = box ? { text: txt(box), on: box.getAttribute("aria-checked") } : null;
    await typeName(P, "6714");
    await P.click(footBtn(P, "Переставить"));
    await P.tick(100);
    rec.calls = P.calls("POST", "/api/action/profile-reline").map((c) => c.body);
    rec.done = dlg(P);
    await P.click(footBtn(P, "ОК"));
    await P.tick(100);
    rec.adds = P.calls("POST", "/api/action/user-add-peer").map((c) => c.body);
    rec.dlgs = P.$$(".gx-dlg").length;
  });
  await scenario("dup_keys_off", { hash: "#profiles", data: dupData({ users: (d) => ({ ...d, users: [...d.users, MASHA] }) }),
    api: { "POST /api/action/profile-reline": RELINE_OK, "POST /api/action/user-add-peer": ADD_OK } }, async (P, rec) => {
    await P.tick(100);
    await P.click(P.byText("Переставить на 2.0", '.gx-pcard[data-profile="awg3"] button'));
    await P.tick(50);
    await P.click(P.$$(".gx-dlg").pop().querySelector('.gx-check__i[role="checkbox"]'));
    await typeName(P, "6714");
    await P.click(footBtn(P, "Переставить"));
    await P.tick(100);
    await P.click(footBtn(P, "ОК"));
    await P.tick(100);
    rec.reline = P.calls("POST", "/api/action/profile-reline").map((c) => c.body);
    rec.adds = P.calls("POST", "/api/action/user-add-peer").length;
  });
  // R-7: список пользователей не прочитался — галочка «остальным» всё равно есть (без имён), уходит ядру; оно считает само.
  await scenario("dup_keys_unread", { hash: "#profiles", data: dupData(),
    api: { "GET /api/users": () => ({ status: 500, body: { ok: false, error: "boom" } }), "POST /api/action/profile-reline": RELINE_OK } }, async (P, rec) => {
    await P.tick(100);
    await P.click(P.byText("Переставить на 2.0", '.gx-pcard[data-profile="awg3"] button'));
    await P.tick(50);
    const box = P.$$(".gx-dlg").pop().querySelector('.gx-check__i[role="checkbox"]');
    rec.box = box ? { text: txt(box), on: box.getAttribute("aria-checked") } : null;
    await typeName(P, "6714");
    await P.click(footBtn(P, "Переставить"));
    await P.tick(100);
    rec.calls = P.calls("POST", "/api/action/profile-reline").map((c) => c.body);
  });
  await scenario("dup_cancel", { hash: "#profiles", data: dupData(), api: { "POST /api/action/profile-reline": RELINE_OK } }, async (P, rec) => {
    await P.tick(100);
    await P.click(P.byText("Переставить на 2.0", '.gx-pcard[data-profile="awg3"] button'));
    await typeName(P, "awg2");
    rec.foot = dlg(P).foot;
    await P.click(footBtn(P, "Отмена"));
    rec.calls = P.calls("POST", "/api/action/profile-reline").length;
  });
  // Откат: новая линия не встала — окно со словами ядра (кому выдали ключи заново).
  await scenario("dup_rolled_back", { hash: "#profiles", data: dupData(), api: { "POST /api/action/profile-reline": RELINE_BACK } }, async (P, rec) => {
    await P.tick(100);
    await P.click(P.byText("Переставить на 2.0", '.gx-pcard[data-profile="awg3"] button'));
    await typeName(P, "6714");
    await P.click(footBtn(P, "Переставить"));
    await P.tick(100);
    rec.dlg = dlg(P);
  });
  // «Обзор» и мини-апп: строка «нет 2.0» и кнопка «Переставить … на 2.0».
  for (const [label, extra] of [["dup_overview", { hash: "#overview" }], ["dup_overview_miniapp", { tg: true, width: 390 }]]) {
    await scenario(label, { ...extra, data: dupData(), api: { "POST /api/action/profile-reline": RELINE_OK } }, async (P, rec) => {
      await P.tick(100);
      rec.page = P.page();
      rec.alerts = alerts(P, 'section[data-page="overview"]').filter((a) => a.title.includes("AmneziaWG 2.0"));
      rec.rows = P.$$(".gx-profrow__n").map(txt);
      const b = P.$$(".gx-ovalert__btns button").find((x) => txt(x).startsWith("Переставить"));
      if (b) {
        await P.click(b);
        rec.ask = dlg(P);
        await typeName(P, "6714");
        await P.click(footBtn(P, "Переставить"));
        await P.tick(100);
      }
      rec.calls = P.calls("POST", "/api/action/profile-reline").map((c) => c.body);
      rec.done = dlg(P);
    });
  }
  // Свободное место — линией, что на него встанет (free_lines): рядом с подхваченной 3.1 в awg2 место awg3 — 2.0.
  const FREE_SLOT = { over: { awg2: { ...DUP.awg2, line_shared: false } },
    lc: { no_20: true, shared: [], missing: ["2.0"], reline: null }, free: { awg3: "2.0", awg1: "1.5" } };
  await scenario("dup_free", { hash: "#profiles", data: dupData(FREE_SLOT) }, async (P, rec) => {
    await P.tick(100);
    rec.cards = { awg3: pcard(P, "awg3"), awg2: pcard(P, "awg2") };
    rec.alerts = alerts(P, "#prAlerts");
    await P.click(P.byText("Установить", '.gx-pcard[data-profile="awg3"] button'));
    rec.install = dlg(P);
    await P.key("Escape");
    await P.go("#overview");
    await P.tick(100);
    rec.rows = P.$$(".gx-profrow__n").map(txt);
    rec.ovAlerts = alerts(P, 'section[data-page="overview"]').filter((a) => a.title.includes("AmneziaWG 2.0"));
  });
  // Мини-апп Telegram на телефоне — тот же главный сценарий пользователя: «Протоколы», свободное awg3 — 2.0.
  await scenario("dup_free_miniapp", { tg: true, width: 390, data: dupData(FREE_SLOT) }, async (P, rec) => {
    await P.tick(100);
    await P.go("#profiles");
    await P.tick(100);
    rec.page = P.page();
    rec.card = pcard(P, "awg3");
    await P.click(P.byText("Установить", '.gx-pcard[data-profile="awg3"] button'));
    rec.install = dlg(P);
  });
  // Люди: вкладки ключей, «Перевыпустить ключ», временная ссылка и выбор профилей нового человека — с портом; там же
  // честная строка «нет 2.0» (R4) — а не совет взять несуществующую 2.0.
  // Вход — как обычно, с Обзора (он приносит lines_check); подписи с портом панель знает и без него — из «Настроек».
  await scenario("dup_users", { hash: "#overview", data: dupData(), api: adminApi(true, true) }, async (P, rec) => {
    await P.tick(100);
    await P.go("#users");
    await P.tick(100);
    await P.click(adminRow(P));
    rec.tabs = P.$$('.gx-sheet [role="tab"]').map(txt);
    rec.danger = P.$$(".gx-sheet .gx-danger button").map(txt);
    await P.click(P.byText("Временная ссылка", ".gx-sheet button"));
    rec.share = P.$$(".gx-dlg .gx-ptabs > button").map(txt);
    await P.key("Escape");
    await P.key("Escape");
    await P.click(P.byText("Добавить", 'section[data-page="users"] button'));
    rec.add = P.$$(".gx-sheet .gx-checkcard .gx-radio__t").map(txt);
    rec.addAl = P.$$(".gx-sheet .gx-al").map(txt).filter((x) => x.includes("2.0"));
  });
  // KEY-APP-02: у человека все ключи только в приложении Amnezia (has_key: false) — ни QR, ни .conf, ни ссылок; строка,
  // почему, и что работает; «Создать» страницу — не окно, ведущее в no_private_key, а объяснение.
  const appOnlyUsers = (d) => ({ ...d, users: d.users.map((u) => (u.name === "admin"
    ? { ...u, peers: Object.fromEntries(Object.entries(u.peers).map(([k, v]) => [k, { ...v, has_key: false }])) } : u)) });
  await scenario("keyapp_only", { hash: "#users", data: dupData({ users: appOnlyUsers }), api: adminApi(false, false, {
    "GET /api/pages": { status: 200, body: { ok: true, data: { available: true, pages: [{ id: "pg1", forever: true, visits: 0, profiles: ["awg3"], mirror: true }] } } } }) },
  async (P, rec) => {
    await P.click(adminRow(P));
    await P.tick(50);
    const sheet = P.$$(".gx-sheet").pop();
    rec.text = txt(sheet);
    rec.btns = sheet.querySelectorAll("button").map(txt);
    const create = sheet.querySelectorAll(".gx-upages button").find((b) => txt(b) === "Создать");
    rec.hasCreate = !!create;
    if (create) await P.click(create);
    rec.dlg = dlg(P);
    rec.pageCalls = P.calls("POST", "/api/action/page-create").length;
  });
  // Сообщение после обновления (status.dupline_fix): заметный блок «Ошибка прошлых версий» с «Исправить» и «Позже»;
  // пока он виден, обычной строки lines_check о том же нет; «Позже» прячет блок до перезагрузки — строка возвращается.
  const DFX = { profile: "awg3", from: "3.1", line: "2.0", port: 6714, title: "AmneziaWG 3.1 · порт 6714", users: 3,
    text: "слова ядра", detected_at: "2026-10-15T12:00:00Z" };
  const fixData = () => { const d = dupData(); const ov = d["/api/overview"]; return { ...d, "/api/overview": (x) => ({ ...ov(x), dupline_fix: DFX }) }; };
  for (const [label, extra] of [["dup_fix", { hash: "#overview" }], ["dup_fix_miniapp", { tg: true, width: 390 }]]) {
    await scenario(label, { ...extra, data: fixData(), api: { "POST /api/action/profile-reline": RELINE_OK } }, async (P, rec) => {
      await P.tick(100);
      rec.alerts = alerts(P, 'section[data-page="overview"]').filter((a) => a.title.includes("AmneziaWG 2.0"));
      // Цвет плашки — янтарный (warning), а не danger (DUPLINE-COLOR-01)
      rec.tone = (P.$$(".gx-ovalert").map((n) => ({ t: n.querySelector(".gx-ovalert__t").textContent,
        c: (n.querySelector(".gx-al__ic").getAttribute("class").match(/gx-al__ic--(\w+)/) || [])[1] }))
        .find((x) => x.t.includes("Ошибка прошлых версий")) || {}).c || "";
      // DUPLINE-BANNER: «Подробнее» раскрывает остальной текст; раскрытое держится и после перерисовки
      const more = P.$$(".gx-ovalert__more")[0] || null;
      rec.moreAria = more ? more.getAttribute("aria-expanded") : null;
      if (more) await P.click(more);
      await P.tick(50);
      rec.opened = alerts(P, 'section[data-page="overview"]').filter((a) => a.title.includes("Ошибка прошлых версий"));
      await P.click(P.$$(".gx-ovalert__btns button").find((x) => txt(x) === "Позже"));
      await P.tick(50);
      rec.later = alerts(P, 'section[data-page="overview"]').filter((a) => a.title.includes("AmneziaWG 2.0"));
      rec.callsLater = P.calls("POST", "/api/action/profile-reline").length;
    });
  }
  await scenario("dup_fix_go", { hash: "#overview", data: fixData(), api: { "POST /api/action/profile-reline": RELINE_OK } }, async (P, rec) => {
    await P.tick(100);
    await P.click(P.$$(".gx-ovalert__btns button").find((x) => txt(x) === "Исправить"));
    rec.ask = dlg(P);
    await typeName(P, "6714");
    await P.click(footBtn(P, "Переставить"));
    await P.tick(100);
    rec.calls = P.calls("POST", "/api/action/profile-reline").map((c) => c.body);
    rec.done = dlg(P);
  });
  await scenario("dup_fix_en", { hash: "#overview", lang: "en", data: fixData() }, async (P, rec) => {
    await P.tick(100);
    rec.alerts = alerts(P, 'section[data-page="overview"]').filter((a) => a.title.includes("AmneziaWG 2.0"));
    const more = P.$$(".gx-ovalert__more")[0] || null;
    if (more) await P.click(more);
    await P.tick(50);
    rec.opened = alerts(P, 'section[data-page="overview"]').filter((a) => a.title.includes("AmneziaWG 2.0"));
  });
  // PNL-1: окно согласия собирается с задержкой (список пользователей отвечает не сразу) — «Исправить» блокируется
  // сразу, второе нажатие второго окна не открывает.
  {
    const g = { open: false }; g.p = new Promise((res) => { g.go = () => { g.open = true; res(); }; });
    await scenario("dup_fix_twice", { hash: "#overview", data: fixData(), api: { "POST /api/action/profile-reline": RELINE_OK },
      hold: (m, p) => (!g.open && m === "GET" && p === "/api/users" ? g.p : null) }, async (P, rec) => {
      await P.tick(100);
      const go = P.$$(".gx-ovalert__btns button").find((x) => txt(x) === "Исправить");
      await P.click(go);
      await P.tick(20);
      rec.disabled = !!go.disabled;
      rec.dlgsWhileWaiting = P.$$(".gx-dlg").length;
      await P.click(go);
      const again = P.$$(".gx-ovalert__btns button").find((x) => txt(x) === "Исправить");
      if (again && again !== go) await P.click(again);
      g.go();
      await P.tick(100);
      rec.dlgs = P.$$(".gx-dlg").length;
      rec.users = P.calls("GET", "/api/users").length;
      await P.key("Escape");
      await P.tick(50);
      rec.enabledAfter = !go.disabled;
    });
  }
  // PNL-3: после перестановки плашка уходит сразу, не дожидаясь снимка status; Обзор перечитывается свежим (fresh).
  {
    const st = { relined: false };
    const g = { open: false }; g.p = new Promise((res) => { g.go = () => { g.open = true; res(); }; });
    const d0 = fixData();
    const ov0 = d0["/api/overview"];
    const data = { ...d0, "/api/overview": (x) => (st.relined ? { ...ov0(x), dupline_fix: null, lines_check: { no_20: false, shared: [], missing: [], reline: null } } : ov0(x)) };
    await scenario("dup_fix_fresh", { hash: "#overview", data,
      api: { "POST /api/action/profile-reline": (body) => { st.relined = true; return RELINE_OK(body); } },
      hold: (m, p) => (st.relined && !g.open && m === "GET" && p === "/api/overview" ? g.p : null) }, async (P, rec) => {
      await P.tick(100);
      rec.before = alerts(P, 'section[data-page="overview"]').filter((a) => a.title.includes("AmneziaWG 2.0")).map((a) => a.title);
      await P.click(P.$$(".gx-ovalert__btns button").find((x) => txt(x) === "Исправить"));
      await typeName(P, "6714");
      await P.click(footBtn(P, "Переставить"));
      await P.tick(100);
      rec.whileHeld = alerts(P, 'section[data-page="overview"]').filter((a) => a.title.includes("AmneziaWG 2.0")).map((a) => a.title);
      const after = P.fetchLog.filter((c) => c.method === "GET" && c.path === "/api/overview").slice(-1)[0];
      rec.fresh = after ? after.headers["X-GoAmnezia-Fresh"] || "" : "";
      g.go();
      await P.tick(100);
      rec.after = alerts(P, 'section[data-page="overview"]').filter((a) => a.title.includes("AmneziaWG 2.0")).map((a) => a.title);
    });
  }
  // PROFILE-REMOVE-PAGES: «Удалить» называет, чьи страницы подключения перестанут работать (имена — из ядра,
  // profile-remove-preview), до набора порта; само удаление — как раньше. Страниц таких нет — строки нет.
  const RM_PREVIEW = (pages, users) => () => ({ status: 200, body: { ok: true, data: { status: "success", profile: "awg3",
    title: "AmneziaWG 3.1 · порт 6714", holders: 2, pages, page_users: users, text: "слова ядра" } } });
  const RM_OK = () => ({ status: 200, body: { ok: true, data: { status: "success", message: "ok", profile: "awg3" } } });
  for (const [label, prev, lang] of [["rm_pages", RM_PREVIEW(2, ["anna", "boris"]), "ru"], ["rm_page_one", RM_PREVIEW(1, ["boris"]), "ru"],
    ["rm_pages_none", RM_PREVIEW(0, []), "ru"], ["rm_pages_en", RM_PREVIEW(2, ["anna", "boris"]), "en"],
    ["rm_pages_many", RM_PREVIEW(23, Array.from({ length: 23 }, (_, i) => "u" + (i + 1))), "ru"]]) {
    await scenario(label, { hash: "#profiles", lang, data: dupData(),
      api: { "POST /api/action/profile-remove-preview": prev, "POST /api/action/profile-remove": RM_OK } }, async (P, rec) => {
      await P.tick(100);
      const b = P.$$('.gx-pcard[data-profile="awg3"] .gx-pdanger button')[1];
      await P.click(b);
      await P.tick(50);
      rec.ask = dlg(P);
      rec.warn = P.$$(".gx-dlg").pop().querySelectorAll(".gx-al").map(txt);
      rec.previews = P.calls("POST", "/api/action/profile-remove-preview").map((c) => c.body);
      await typeName(P, "6714");
      await P.click(P.$$(".gx-dlg__foot button").pop());
      await P.tick(100);
      rec.removes = P.calls("POST", "/api/action/profile-remove").map((c) => c.body);
    });
  }
  // Свободное место в окне нового пользователя — «AmneziaWG 2.0 — не установлена».
  await scenario("dup_free_add", { hash: "#overview", data: dupData(FREE_SLOT) }, async (P, rec) => {
    await P.tick(100);
    await P.go("#users");
    await P.tick(100);
    await P.click(P.byText("Добавить", 'section[data-page="users"] button'));
    rec.add = P.$$(".gx-sheet .gx-checkcard .gx-radio__t").map(txt);
  });
  console.log(JSON.stringify(R));
})().catch((e) => { console.log(JSON.stringify({ __fatal: String((e && e.stack) || e) })); });
"""


@unittest.skipUnless(NODE, "нет node 18+ — панель в игрушечном DOM не проверить")
class PanelDupLineDomTests(unittest.TestCase):
    """DUP-LINE-01 (решение владельца 15.10): «Установить» ставит недостающую линию; две одинаковые линии подписываются с
    портом; нет 2.0 — строка-предупреждение и «Переставить <профиль> на 2.0» (только свой профиль, профиль приложения —
    никогда) с согласием, как у разрушительных, и итогом «кому что сделать», как у «Исправить S4»."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="goamnezia-panel-dupline-") as tmp:
            script = os.path.join(tmp, "dupline.js")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(DUPLINE_JS)
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

    NO20 = "Старые версии приложений и роутеры Keenetic на обычной прошивке не подключатся ни к одному профилю (3.1 Keenetic понимает пока только в dev-прошивке)."
    OWN = "AmneziaWG 3.1 · порт 6714"
    APP = "AmneziaWG 3.1 · порт 46567 · из приложения"
    RELINE_D = ("AmneziaWG 3.1 · порт 6714 повторяет линию другого профиля — его можно переставить на AmneziaWG 2.0: "
                "порт и подсеть останутся прежними, пользователям на нём — новые ключи.")

    def test_profiles_are_named_by_line_and_port_without_the_slot(self):
        """Решение владельца 15.10: подпись для пользователей — линия и порт, без имени места; подхваченный у приложения —
        «· из приложения»; свободное место — «— не установлена». Где порт стоит рядом (карточка «Протоколов», строки
        Обзора) — короткая подпись без порта."""
        rec = self.scen("dup_profiles")
        self.assertEqual(rec["cards"]["awg3"]["title"], "AmneziaWG 3.1")
        self.assertEqual(rec["cards"]["awg2"]["title"], "AmneziaWG 3.1 · из приложения")
        self.assertEqual(rec["cards"]["awg2"]["aria"], "AmneziaWG 3.1 · из приложения")
        self.assertEqual(rec["cards"]["awg1"]["title"], "AmneziaWG 1.x")
        users = self.scen("dup_users")
        self.assertEqual(users["tabs"], [self.OWN, self.APP])
        self.assertIn("Перевыпустить ключ 3.1 · порт 6714", users["danger"])
        self.assertEqual(users["share"], [self.OWN, self.APP])
        self.assertEqual(users["add"], [self.OWN, self.APP, "AmneziaWG 1.x — не установлена"])
        for name in ("dup_overview", "dup_overview_miniapp"):
            rows = self.scen(name)["rows"]
            if rows:                                  # в мини-аппе раздел профилей Обзора бывает свёрнут
                self.assertIn("AmneziaWG 3.1 · из приложения", rows, name)
        for txt in (str(rec["cards"]), str(users)):
            self.assertNotRegex(txt, r"\(awg[123]")    # имени места в подписях нет

    def test_free_slot_is_named_by_the_line_it_gets(self):
        """Главный сценарий пользователя: подхваченная у приложения 3.1 в awg2, своего awg3 нет — свободное место это 2.0
        (free_lines ядра): карточка, «кому он», приложение, окно «Установить», строка на Обзоре, мини-апп и окно нового
        пользователя; строка «нет 2.0» ведёт к «Установить»; «2.0 занята» нигде нет — занято место, а не линия."""
        rec = self.scen("dup_free")
        c = rec["cards"]["awg3"]
        self.assertEqual(c["title"], "AmneziaWG 2.0")
        self.assertIn("4.8.12.9", c["text"])                   # «кому он» и приложение — по линии 2.0
        self.assertNotIn("5.0.1.5", c["text"])
        self.assertEqual(rec["cards"]["awg2"]["title"], "AmneziaWG 3.1 · из приложения")
        self.assertEqual(rec["install"]["title"], "Установить AmneziaWG 2.0")
        self.assertIn("4.8.12.9", rec["install"]["text"])
        self.assertIn("AmneziaWG 2.0", rec["rows"])
        way = "Получить её — «Протоколы» → AmneziaWG 2.0 → «Установить»."
        self.assertEqual(rec["alerts"], [{"title": "На сервере нет AmneziaWG 2.0", "text": self.NO20 + " " + way, "btns": []}])
        self.assertEqual(rec["ovAlerts"], [{"title": "На сервере нет AmneziaWG 2.0", "text": self.NO20 + " " + way, "btns": []}])
        tg = self.scen("dup_free_miniapp")
        self.assertEqual(tg["page"], "profiles")
        self.assertEqual(tg["card"]["title"], "AmneziaWG 2.0")
        self.assertIn("4.8.12.9", tg["card"]["text"])
        self.assertEqual(tg["install"]["title"], "Установить AmneziaWG 2.0")
        self.assertEqual(self.scen("dup_free_add")["add"], ["AmneziaWG 2.0 — не установлена", "AmneziaWG 3.1 · порт 46567 · из приложения",
                                                            "AmneziaWG 1.x — не установлена"])
        for t in (c["text"], tg["card"]["text"], rec["install"]["text"]):   # «2.0 занята приложением» — неправда
            self.assertNotRegex(t, r"(AmneziaWG )?2\.0[^.]{0,20}занят")

    def test_no_20_warning_and_reline_button_only_on_own_profile(self):
        """Протоколы: строка «нет 2.0» сверху; в карточке своего awg3 — что не так и «Переставить на 2.0»; у профиля
        приложения (awg2) — ни строки, ни кнопки."""
        rec = self.scen("dup_profiles")
        self.assertEqual(rec["alerts"], [{"title": "На сервере нет AmneziaWG 2.0", "text": self.NO20 + " " + self.RELINE_D, "btns": []}])
        self.assertIn("Переставить на 2.0", rec["cards"]["awg3"]["btns"])
        self.assertIn("Линия повторяется. Ту же линию держит AmneziaWG 3.1 · порт 46567 · из приложения, а AmneziaWG 2.0 на "
                      "сервере нет.", rec["cards"]["awg3"]["text"])
        self.assertNotIn("Переставить на 2.0", rec["cards"]["awg2"]["btns"])
        self.assertNotIn("Линия повторяется", rec["cards"]["awg2"]["text"])

    def test_reline_asks_like_destructive_calls_the_core_and_says_who_does_what(self):
        """Окно согласия — как у разрушительных (набор имени профиля): что будет, тем же языком, что SSH; мост —
        profile-reline с line и confirm = профиль; итог — summary и шаги ядра списком."""
        rec = self.scen("dup_profiles")
        ask = rec["ask"]
        self.assertEqual(ask["title"], "Переставить AmneziaWG 3.1 · порт 6714 на 2.0?")
        for part in ("AmneziaWG 3.1 · порт 6714 удалю и поставлю заново с линией 2.0 на тот же порт 6714 и ту же подсеть.",
                     "Прежние ключи пользователей на нём перестанут работать — всем, у кого он был, выдам ключи на новом профиле.",
                     "Профиль приложения Amnezia не трогаю."):
            self.assertIn(part, ask["text"])
        # у каждого пользователя его действие — до согласия, шагами ядра (подписка — только забиравшие all.*)
        self.assertEqual(ask["stat"], ["С подпиской (vera): обновится сама в течение 12 часов или по «Обновить» в приложении.",
                                       "Со страницей подключения (boris): открыть страницу заново и импортировать новый ключ.",
                                       "Выдавали ключ или файл (admin): прежний больше не подключится — выдайте ключ vpn:// или файл .conf заново."])
        self.assertTrue(ask["input"])
        self.assertEqual(ask["foot"], ["Отмена", "Переставить [off]"])
        # D-5/R-3: набирают порт профиля (6714), он же в подписи; имя места (awg3) кнопку не открывает; мосту — профиль
        self.assertIn("Для подтверждения наберите порт профиля", ask["text"])
        self.assertEqual(rec["byPlace"], ["Отмена", "Переставить [off]"])
        self.assertEqual(rec["armed"], ["Отмена", "Переставить"])
        self.assertEqual(rec["calls"], [{"profile": "awg3", "line": "2.0", "confirm": "awg3"}])
        done = rec["done"]
        self.assertEqual(done["title"], "Профиль переставлен · AmneziaWG 2.0 · порт 6714")
        self.assertIn("переставлен на AmneziaWG 2.0: порт 6714 и подсеть 10.8.3.0/24 прежние.", done["text"])
        self.assertEqual(done["stat"], ["Ключи на новом профиле выданы: admin.",
                                        "С подпиской (vera): обновится сама в течение 12 часов или по «Обновить» в приложении.",
                                        "Выдавали ключ или файл (admin): прежний больше не подключится — выдайте ключ vpn:// или файл .conf заново."])
        cancel = self.scen("dup_cancel")
        self.assertEqual(cancel["foot"], ["Отмена", "Переставить [off]"])    # чужое имя не открывает кнопку
        self.assertEqual(cancel["calls"], 0)

    def test_reline_grants_keys_to_the_others_by_a_checkbox(self):
        """R-2: в окне согласия — галочка «Выдать ключ на AmneziaWG 2.0 · порт 6714 остальным пользователям (1)», отмечена;
        после перестановки ключ получают только они. Сняли — не выдаётся. У всех ключ есть — галочки нет."""
        rec = self.scen("dup_keys")
        self.assertEqual(rec["box"]["on"], "true", rec)
        self.assertIn("Выдать ключ на AmneziaWG 2.0 · порт 6714 остальным пользователям (1)", rec["box"]["text"])
        self.assertIn("У них ключа на этом профиле нет: masha.", rec["box"]["text"])
        # DUPLINE-KEYS-01: галочка уходит в запрос перестановки, выдаёт ядро; браузер user-add-peer не зовёт
        self.assertEqual(rec["calls"], [{"profile": "awg3", "line": "2.0", "keys_to_others": "on", "confirm": "awg3"}])
        self.assertEqual(rec["adds"], [])
        self.assertIn("Ключи на AmneziaWG 2.0 выданы и остальным пользователям: masha.", rec["done"]["stat"])
        self.assertEqual(rec["dlgs"], 0, "после «ОК» окон больше нет")
        off = self.scen("dup_keys_off")
        self.assertEqual((off["reline"], off["adds"]), ([{"profile": "awg3", "line": "2.0", "confirm": "awg3"}], 0))
        self.assertNotIn("остальным пользователям", self.scen("dup_profiles")["ask"]["text"])
        # P-7: ключа на профиле нет ни у кого — «всем пользователям», «нет ни у кого»
        none = self.scen("dup_keys_none")
        self.assertIsNotNone(none["box"], none)
        self.assertRegex(none["box"]["text"], r"Выдать ключ на AmneziaWG 2\.0 · порт 6714 всем пользователям \(\d+\)")
        self.assertIn("Ключа на этом профиле нет ни у кого:", none["box"]["text"])
        self.assertNotIn("остальным", none["box"]["text"])
        # R-7: список пользователей не прочитался — выбор не пропадает молча: галочка без имён, отмечена, уходит ядру
        unread = self.scen("dup_keys_unread")
        self.assertIsNotNone(unread["box"], unread)
        self.assertEqual(unread["box"]["on"], "true")
        self.assertIn("Выдать ключ на AmneziaWG 2.0 · порт 6714 остальным пользователям, у кого его нет на этом профиле", unread["box"]["text"])
        self.assertIn("Список пользователей сейчас не прочитался", unread["box"]["text"])
        self.assertEqual(unread["calls"], [{"profile": "awg3", "line": "2.0", "keys_to_others": "on", "confirm": "awg3"}])

    def test_reline_steps_come_from_a_separate_request_when_the_brief_status_has_none(self):
        """S-1, N-3: Обзор и «Протоколы» живут кратким status без steps — окно согласия просит их у сервера панели
        (/api/profiles/awg3/reline-preview) и показывает у каждого пользователя его действие; есть в состоянии — не просит."""
        rec = self.scen("dup_brief_preview")
        self.assertEqual(rec["previews"], 1, rec)
        self.assertEqual(rec["ask"]["stat"], ["С подпиской (vera): обновится сама в течение 12 часов или по «Обновить» в приложении.",
                                              "Со страницей подключения (boris): открыть страницу заново и импортировать новый ключ.",
                                              "Выдавали ключ или файл (admin): прежний больше не подключится — выдайте ключ vpn:// или файл .conf заново."])
        self.assertEqual(self.scen("dup_profiles")["previews"], 0)

    def test_reline_rollback_is_shown_in_the_core_words(self):
        rec = self.scen("dup_rolled_back")
        self.assertEqual(rec["dlg"]["title"], "Переставить AmneziaWG 3.1 · порт 6714 не вышло")
        self.assertIn("вернули прежнюю линию 3.1 на тот же порт и подсеть; людям на нём — новые ключи: admin.", rec["dlg"]["text"])

    def test_overview_and_miniapp_warn_and_offer_the_move(self):
        for name in ("dup_overview", "dup_overview_miniapp"):
            rec = self.scen(name)
            self.assertEqual(rec["page"], "overview", name)
            self.assertEqual(rec["alerts"], [{"title": "На сервере нет AmneziaWG 2.0", "text": self.NO20 + " " + self.RELINE_D,
                                              "btns": ["Переставить AmneziaWG 3.1 · порт 6714 на 2.0"]}], name)
            self.assertEqual(rec["ask"]["title"], "Переставить AmneziaWG 3.1 · порт 6714 на 2.0?", name)
            self.assertEqual(rec["calls"], [{"profile": "awg3", "line": "2.0", "confirm": "awg3"}], name)
            self.assertEqual(rec["done"]["title"], "Профиль переставлен · AmneziaWG 2.0 · порт 6714", name)

    FIX_T = "Ошибка прошлых версий: вместо AmneziaWG 2.0 встала вторая 3.1"
    # DUPLINE-BANNER: одно предложение, остальное (текст карты сервера, DUPLINE-TEXT-01) — за «Подробнее»
    FIX_S = ("На сервере нет AmneziaWG 2.0: старые версии приложений (AmneziaVPN до 5.0.1.5, DefaultVPN) не подключатся к 3.1, "
             "а роутеры Keenetic на обычной прошивке — только к AmneziaWG 1.5 или 1.0, если она стоит (3.1 Keenetic понимает "
             "пока только в dev-прошивке).")
    FIX_D = ("Исправление переставит AmneziaWG 3.1 · порт 6714 на 2.0: порт и подсеть "
             "останутся прежними. Пользователям на нём (3) goAmnezia pro сразу выдаст новые ключи. Подписка подтянет их сама "
             "только там, где приложение само её обновляет; страницу подключения нужно открыть заново и импортировать новый "
             "ключ, а тем, кому ключ или файл отправляли вручную, — заново отсканировать QR-код или импортировать ключ: старый "
             "перестанет работать. Профиль "
             "приложения не тронем.")

    def test_update_message_block_with_fix_and_later(self):
        """Сообщение после обновления (status.dupline_fix): на Обзоре и в мини-аппе — заметный блок с «Позже» и
        «Исправить»; обычной строки lines_check о том же нет; «Позже» прячет блок — и строка lines_check возвращается."""
        for name in ("dup_fix", "dup_fix_miniapp"):
            rec = self.scen(name)
            self.assertEqual(rec["alerts"], [{"title": self.FIX_T, "text": self.FIX_S + " Подробнее", "btns": ["Позже", "Исправить"]}], name)
            self.assertEqual(rec["moreAria"], "false", name)
            self.assertEqual(rec["opened"], [{"title": self.FIX_T, "text": self.FIX_S + " " + self.FIX_D, "btns": ["Позже", "Исправить"]}], name)
            self.assertEqual(rec["tone"], "warning", name)
            self.assertEqual(rec["later"], [{"title": "На сервере нет AmneziaWG 2.0", "text": self.NO20 + " " + self.RELINE_D,
                                             "btns": ["Переставить AmneziaWG 3.1 · порт 6714 на 2.0"]}], name)
            self.assertEqual(rec["callsLater"], 0, name)
        go = self.scen("dup_fix_go")
        self.assertEqual(go["ask"]["title"], "Переставить AmneziaWG 3.1 · порт 6714 на 2.0?")
        self.assertEqual(go["calls"], [{"profile": "awg3", "line": "2.0", "confirm": "awg3"}])
        self.assertEqual(go["done"]["title"], "Профиль переставлен · AmneziaWG 2.0 · порт 6714")
        en_rec = self.scen("dup_fix_en")
        en = en_rec["alerts"]
        self.assertEqual(en[0]["title"], "A bug in earlier versions: a second 3.1 was installed instead of AmneziaWG 2.0")
        self.assertEqual(en[0]["text"], "There is no AmneziaWG 2.0 on the server: old app versions (AmneziaVPN before 5.0.1.5, "
                                        "DefaultVPN) cannot connect to 3.1, and Keenetic routers on regular firmware connect only "
                                        "to AmneziaWG 1.5 or 1.0 if it is installed (Keenetic understands 3.1 only in dev firmware "
                                        "so far). More details")
        self.assertEqual(en[0]["btns"], ["Later", "Fix"])
        opened = en_rec["opened"][0]["text"]
        self.assertIn("The fix moves AmneziaWG 3.1 · port 6714 to 2.0: the port and subnet stay.", opened)
        self.assertIn("goAmnezia pro issues new keys to the users on it (3) right away.", opened)
        self.assertNotIn("More details", opened)

    def test_fix_button_is_blocked_at_once_and_opens_one_window(self):
        """PNL-1: окно согласия собирается с задержкой — «Исправить» блокируется сразу, второе нажатие второго окна не
        открывает; закрыли окно — кнопка снова доступна."""
        rec = self.scen("dup_fix_twice")
        self.assertTrue(rec["disabled"], rec)
        self.assertEqual(rec["dlgsWhileWaiting"], 0, rec)
        self.assertEqual((rec["dlgs"], rec["users"]), (1, 1), rec)
        self.assertTrue(rec["enabledAfter"], rec)

    def test_overview_drops_the_banner_right_after_the_move(self):
        """PNL-3: после перестановки «Ошибка прошлых версий» уходит с Обзора сразу, пока свежий снимок ещё идёт; Обзор
        перечитывается свежим (X-GoAmnezia-Fresh)."""
        rec = self.scen("dup_fix_fresh")
        self.assertIn(self.FIX_T, rec["before"], rec)
        self.assertEqual(rec["whileHeld"], [], rec)
        self.assertEqual(rec["fresh"], "1", rec)
        self.assertEqual(rec["after"], [], rec)

    def test_remove_names_the_connection_pages_that_stop_working(self):
        """PROFILE-REMOVE-PAGES: до набора порта — «Страницы подключения у anna, boris перестанут работать: в них был
        только этот профиль.» (имена — из ядра, profile-remove-preview), одна — в единственном числе, нет таких — строки
        нет; само удаление прежнее: profile-remove с confirm."""
        rec = self.scen("rm_pages")
        self.assertEqual(rec["previews"], [{"profile": "awg3"}])
        self.assertEqual(rec["warn"], ["Страницы подключения у anna, boris перестанут работать: в них был только этот профиль."], rec)
        self.assertEqual(rec["removes"], [{"profile": "awg3", "confirm": "awg3"}])
        one = self.scen("rm_page_one")
        self.assertEqual(one["warn"], ["Страница подключения у boris перестанет работать: в ней был только этот профиль."])
        self.assertEqual(self.scen("rm_pages_none")["warn"], [])
        self.assertEqual(self.scen("rm_pages_en")["warn"], ["The connection pages of anna, boris stop working: they had only this profile."])
        # R-4: как бот и SSH — не больше 20 имён, остальное «и ещё N»
        many = ", ".join("u%d" % i for i in range(1, 21)) + " и ещё 3"
        self.assertEqual(self.scen("rm_pages_many")["warn"],
                         ["Страницы подключения у %s перестанут работать: в них был только этот профиль." % many])

    def test_new_person_dialog_says_there_is_no_20(self):
        """R4: выбор профилей нового пользователя на сервере без 2.0 честно говорит, что её нет и как её получить."""
        rec = self.scen("dup_users")
        self.assertEqual(rec["addAl"], ["На сервере нет AmneziaWG 2.0. " + self.NO20 + " Получить её — «Протоколы» → "
                                        "AmneziaWG 3.1 · порт 6714 → «Переставить на 2.0»."])

    def test_app_only_keys_offer_attach_and_reissue_and_no_dead_ends(self):
        """KEY-APP-02: все ключи только в приложении Amnezia — ни QR, ни .conf, ни vpn://, ни временной ссылки; строка,
        почему, и два выхода; «Создать» страницу — объяснение, а не окно, ведущее в no_private_key."""
        rec = self.scen("keyapp_only")
        self.assertIn("Привязать старый ключ", rec["btns"])
        self.assertIn("Перевыпустить ключ 3.1 · порт 6714", rec["btns"])
        for dead in ("Скопировать vpn://", "Временная ссылка", "QR vpn:// · AmneziaVPN"):
            self.assertNotIn(dead, rec["btns"])
        self.assertIn("QR, файл .conf и ссылки отсюда не выдать. Работает одно из двух: «Привязать старый ключ» или "
                      "«Перевыпустить ключ 3.1 · порт 6714» внизу карточки — тогда пользователю нужен новый ключ.", rec["text"])
        self.assertTrue(rec["hasCreate"])
        self.assertEqual(rec["dlg"]["title"], "Все ключи — в приложении Amnezia")
        self.assertIn("ни ссылку, ни страницу, ни подписку собрать не из чего", rec["dlg"]["text"])
        self.assertEqual(rec["pageCalls"], 0)

if __name__ == "__main__":
    unittest.main()

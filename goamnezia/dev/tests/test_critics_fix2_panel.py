# -*- coding: utf-8 -*-
"""Панель: правки второго круга критиков 16.10 (CRITICS-FIX-2) — в игрушечном DOM (tests/fixtures/panel_dom.js) под
node, как test_panel_dom.py; без node проверки пропускаются.
  B2-2/A2-4  «Отозвать страницу»: галочка «Перевыпустить и подписку» (включена, если подписку завела эта страница),
             мосту — sub=on/off; «Страница готова» говорит о подписке; в строке — дата, а не p-xxxx (B2-3);
  A2-5       страница на час — «ещё 55 мин», а не «ещё 1 дн».
Запуск: python3 -m unittest tests/test_critics_fix2_panel.py
"""
import json
import os
import re
import shutil
import subprocess
import importlib.util
import tempfile
import time
import unittest
from unittest import mock

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


def server_pages():
    """Ответ /api/pages из pages_payload (admin-web/server.py) на строки ядра page-list (lib/pages.sh:ga_pages_row):
    тест панели не подставляет поля в обход белого списка сервера (RR-1)."""
    iso = lambda sec: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(sec))
    now = time.time()
    rows = [
        {"id": "p-1a2b", "user": "anna", "created": "2026-10-16T10:00:00Z", "expires": iso(now + 55 * 60 + 20),
         "forever": False, "ttl": "1h", "visits": 1, "last_visit": "", "profiles": ["awg3"], "mirror": True,
         "subscription": "created"},
        {"id": "p-3c4d", "user": "anna", "created": "2026-10-15T10:00:00Z", "expires": "", "forever": True,
         "ttl": "forever", "visits": 0, "last_visit": "", "profiles": ["awg3"], "mirror": True, "subscription": "exists"},
        {"id": "p-5e6f", "user": "anna", "created": "2026-10-14T10:00:00Z", "expires": iso(now + 5 * 86400),
         "forever": False, "ttl": "7d", "visits": 0, "last_visit": "", "profiles": ["awg3"], "mirror": True,
         "subscription": None},
    ]
    with tempfile.TemporaryDirectory(prefix="goamnezia-cf2-srv-") as tmp:
        env = {"GOAMNEZIA_DIR": os.path.join(tmp, "goamnezia"), "GOAMNEZIA_ADMIN_DIR": os.path.join(tmp, "admin"),
               "GOAMNEZIA_BOT_DIR": os.path.join(tmp, "bot"), "GOAMNEZIA_AUDIT_LOG": os.path.join(tmp, "audit.log"),
               "GOAMNEZIA_ACTION_BIN": os.path.join(ROOT, "tests", "fixtures", "fake_action_admin.sh"),
               "GOAMNEZIA_ACTIONS_JSON": os.path.join(ROOT, "lib", "actions.json"), "GOAMNEZIA_ADMIN_PORT": "1985",
               "GOAMNEZIA_TRUST_TUNNEL": "1"}
        for k in ("GOAMNEZIA_DIR", "GOAMNEZIA_ADMIN_DIR", "GOAMNEZIA_BOT_DIR"):
            os.makedirs(env[k])
        with mock.patch.dict(os.environ, env):
            spec = importlib.util.spec_from_file_location("ga_admin_server_cf2", os.path.join(ROOT, "admin-web", "server.py"))
            srv = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(srv)  # type: ignore[union-attr]
            srv.run_action = lambda *a, **kw: {"status": "success", "pages": rows}
            d = srv.pages_payload("panel", "anna")
    assert d["available"] and d["count"] == 3, d
    return d["pages"]

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
const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
const NOW = Date.now();
const PRO = {
  "/api/overview": (d) => ({ ...d, mode: "pro", domain: "vpn.example.com" }),
  "/api/settings": (d) => ({ ...d, config: { ...d.config, mode: "pro", domain: "vpn.example.com" } }),
};
const annaRow = (P) => P.$$(".gx-urow").find((n) => n.textContent.includes("Анна"));
// /api/pages — ровно то, что отдаёт сервер: строки ядра прогнаны через pages_payload (RR-1, белый список полей).
const PAGES = JSON.parse(process.argv[3]);
const ANNA = { name: "anna", label: "Анна", created: "2026-09-02T10:00:00Z", enabled: true, quota_bytes: 0, expires: "", traffic: { rx: 0, tx: 0 },
  profiles: ["awg3"], state: "on", online: false, last_handshake: 0,
  peers: { awg3: { ip: "10.8.3.3", pubkey: "PUBANNA3", created: "2026-09-01T10:00:00Z", active: true, has_key: true, line: "3.1" } } };
const annaApi = (extra) => ({ "GET /api/users/anna": ok({ user: ANNA }),
  "GET /api/users/anna/key": ok({ name: "anna", line: "3.1", min_client: "5.0.1.5", conf: "[Interface]\\nPrivateKey = X\\n", vpn: "vpn://FAKE" }), ...extra });
// subs — что отвечает sub-list (подписка СЕЙЧАС, R-1); у p-5e6f к отзыву подписки уже нет (R-4: sub_present false).
const ANNA_SUB = { name: "anna", label: "Анна", created: "2026-10-03T10:00:00Z", rotated: "", generation: 1, fetches: 0, last_fetch: "",
  lines: [{ line: "3.1", profile: "awg3", has_key: true }], disabled: false };
const pagesApi = (lang, subs = [ANNA_SUB]) => annaApi({
  "GET /api/pages": ok({ available: true, pages: PAGES }),
  "POST /api/action/sub-list": ok({ status: "success", subs, count: subs.length, domain_ready: true, update_interval: 12 }),
  "POST /api/action/page-revoke": (b) => {
    const present = b.sub === "on" && subs.length > 0 && b.id !== "p-5e6f";
    return ok({ status: "success", message: "ok", id: b.id, user: "anna", sub_rotated: present, sub_present: present });
  },
});

(async () => {
  for (const lang of ["ru", "en"]) {
    await scenario(`revoke_${lang}`, { hash: "#users", lang, data: PRO, api: pagesApi(lang) }, async (P, rec) => {
      await P.tick(100);
      await P.click(annaRow(P));
      await P.tick(100);
      // Только блок страниц: блок «Подписка» рядом рисуется тем же видом (sub-list в фикстуре)
      rec.rows = P.$$('[data-upages="pages"] .gx-upage .gx-upage__h').map(txt);
      rec.rowsTitle = P.$$('[data-upages="pages"] .gx-upage .gx-upage__id').map((n) => n.getAttribute("title"));
      // 1) подписку завела страница — галочка включена; согласились как есть
      const revoke = (i) => P.$$('[data-upages="pages"] .gx-upage')[i].querySelectorAll("button").find((b) => /Отозвать|Revoke/.test(txt(b)));
      await P.click(revoke(0)); await P.tick(50);
      rec.d1 = dlgs(P).pop();
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(100);
      rec.toast1 = toastText(P);
      // 2) подписка была раньше — галочка выключена; включили и согласились
      await P.click(revoke(1)); await P.tick(50);
      rec.d2 = dlgs(P).pop();
      await P.click(P.$$('.gx-dlg [role="checkbox"]').pop()); await P.tick(20);
      rec.d2on = dlgs(P).pop().checks;
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(100);
      // 3) старая страница без поля subscription, а подписка у пользователя есть сейчас — галочка есть, выключена (R-1);
      //    включили, а подписки к отзыву уже нет — «перевыпускать нечего», не красная ошибка (R-4)
      await P.click(revoke(2)); await P.tick(50);
      rec.d3 = dlgs(P).pop();
      await P.click(P.$$('.gx-dlg [role="checkbox"]').pop()); await P.tick(20);
      await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(100);
      rec.toast3 = P.$$("#toastHost .gx-toast").map((n) => [txt(n), n.className]).pop();
      rec.calls = P.calls("POST", "/api/action/page-revoke").map((c) => c.body);
    });
  }
  // R-1: подписки у пользователя нет сейчас — галочки нет и у страницы, которая её когда-то завела
  await scenario("revoke_nosub", { hash: "#users", data: PRO, api: pagesApi("ru", []) }, async (P, rec) => {
    await P.tick(100);
    await P.click(annaRow(P));
    await P.tick(100);
    const revoke = (i) => P.$$('[data-upages="pages"] .gx-upage')[i].querySelectorAll("button").find((b) => /Отозвать/.test(txt(b)));
    await P.click(revoke(0)); await P.tick(50);
    rec.d1 = dlgs(P).pop();
    await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(100);
    rec.calls = P.calls("POST", "/api/action/page-revoke").map((c) => c.body);
    rec.toast = toastText(P);
  });
  // «Страница готова»: ответ page-create со subscription=created — строка про подписку
  await scenario("ready_sub", { hash: "#users", data: PRO, api: annaApi({
    "GET /api/pages": ok({ available: true, pages: [] }),
    "POST /api/action/page-create": (b) => ok({ status: "success", message: "ok", id: "p-7a7a", url: "https://vpn.example.com/p-SECRET/", user: "anna",
      created: iso(NOW), expires: iso(NOW + 86400000 * 7), forever: false, ttl: "7d", profiles: ["awg3"], mirror: true, imported_profiles: [], subscription: "created" }) }) },
  async (P, rec) => {
    await P.tick(100);
    await P.click(annaRow(P));
    await P.tick(100);
    const create = P.$$(".gx-upages button").find((b) => /Создать/.test(txt(b)));
    await P.click(create); await P.tick(50);
    const go = P.$$(".gx-dlg__foot button").pop();
    await P.click(go); await P.tick(200);
    rec.d = dlgs(P).pop();
  });
  // B2-6/A2-1: числа не склеиваются; A2-6: дата срока — как у сервера (сейчас + N дней)
  const typeSlow = async (P, inp, s) => { for (let i = 1; i <= s.length; i += 1) await P.type(inp, s.slice(0, i)); };
  const fieldOf = (inp) => { let n = inp; while (n && !(n.classList && n.classList.contains("gx-field"))) n = n.parentNode; return n; };
  const hintOf = (inp) => { const f = fieldOf(inp); const h = f && f.querySelector(".gx-hint"); return h ? [txt(h), h.className.includes("gx-hint--bad")] : null; };
  await scenario("numbers_add", { hash: "#users", api: {
    "POST /api/action/user-add": (b) => ok({ status: "success", message: "ok", name: b.name }) } }, async (P, rec) => {
    await P.tick(100);
    await P.click(P.byText("Добавить", 'section[data-page="users"] button'));
    const ins = P.$$(".gx-sheet input");
    const idIn = ins[0], quotaIn = ins[ins.length - 2], daysIn = ins[ins.length - 1];
    await P.type(idIn, "num_test");
    await typeSlow(P, quotaIn, "1,5");
    rec.q15 = [quotaIn.value, hintOf(quotaIn)];
    const create = () => P.$$(".gx-sheet button").find((b) => txt(b) === "Создать");
    await P.click(create()); await P.tick(50);
    rec.callsAfterBad = P.calls("POST", "/api/action/user-add").length;
    rec.msgBad = txt(P.$$(".gx-sheet").pop());
    await P.type(quotaIn, "0.5");
    rec.q05 = [quotaIn.value, hintOf(quotaIn)];
    await P.type(quotaIn, "00001");
    rec.q1 = [quotaIn.value, hintOf(quotaIn)];
    await typeSlow(P, daysIn, "-5");
    rec.dNeg = [daysIn.value, hintOf(daysIn)];
    await P.click(create()); await P.tick(50);
    rec.callsAfterNeg = P.calls("POST", "/api/action/user-add").length;
    await P.type(daysIn, "7");
    rec.d7 = hintOf(daysIn);
    await P.click(create()); await P.tick(100);
    rec.adds = P.calls("POST", "/api/action/user-add").map((c) => c.body);
  });
  await scenario("numbers_card", { hash: "#users", api: annaApi({
    "GET /api/pages": ok({ available: true, pages: [] }),
    "POST /api/action/user-quota": (b) => ok({ status: "success", message: "ok" }),
    "POST /api/action/user-expiry": (b) => ok({ status: "success", message: "ok" }) }) }, async (P, rec) => {
    await P.tick(100);
    await P.click(annaRow(P));
    await P.tick(100);
    await P.click(P.$$(".gx-sheet .gx-fieldbtn")[0]); await P.tick(50);
    let inp = P.$$(".gx-dlg input").pop();
    await P.type(inp, "0,5");
    rec.paste = [inp.value, hintOf(inp)];
    await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(50);
    rec.quotaCallsBad = P.calls("POST", "/api/action/user-quota").length;
    await P.type(inp, "00001");
    await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(100);
    rec.quota = P.calls("POST", "/api/action/user-quota").map((c) => c.body);
    rec.quotaToast = toastText(P);
    await P.click(P.$$(".gx-sheet .gx-fieldbtn")[1]); await P.tick(50);
    inp = P.$$(".gx-dlg input").pop();
    await P.type(inp, "7");
    rec.until7 = txt(P.$$(".gx-dlg .gx-fieldbtn__v").pop());
    await P.type(inp, "1");
    rec.until1 = txt(P.$$(".gx-dlg .gx-fieldbtn__v").pop());
    const day = (n) => new Date(Date.now() + n * 86400000).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
    rec.want = [day(7), day(1)];
  });
  // A2-8: вкладка «Временные ссылки» при открытии перечитывает список; A2-9/B2-9: «Порт ссылки» и честная строка под ней
  let shareN = 0;
  await scenario("shares_fresh", { hash: "#users", api: {
    "GET /api/share": () => { shareN += 1; return ok({ available: true, count: 1, error: null, shares: [{ id: "s-aaaf", name: "anna", profile: "awg3",
      format: "conf", file_name: "anna-31.conf", port: 45887, host: "203.0.113.10", via: "ip", seconds_left: 250, downloads: shareN > 1 ? 1 : 0 }] }); } } },
  async (P, rec) => {
    await P.tick(100);
    rec.before = shareN;
    const tab = P.$$('section[data-page="users"] [role="tab"]').find((b) => /Временные/.test(txt(b)));
    await P.click(tab); await P.tick(100);
    rec.after = shareN;
    rec.fresh = P.calls("GET", "/api/share").map((c) => c.headers["X-GoAmnezia-Fresh"] || "");
    rec.table = txt(P.$('section[data-page="users"] .gx-card'));
  });
  // A2-3/B2-7: новый пользователь выключен — ни отсчёта «Ждём первого подключения», ни второй плашки «Выключен»
  const NEWU = { ...ANNA, name: "newu", label: "Новый", enabled: false, state: "off", mark: "○" };
  for (const [label, on] of [["wait_off", false], ["wait_on", true]]) {
    const u = { ...NEWU, enabled: on, state: on ? "on" : "off" };
    let added = false;
    await scenario(label, { hash: "#users", data: { "/api/users": (d) => ({ ...d, users: added ? [...d.users, u] : d.users }) }, api: {
      "POST /api/action/user-add": (b) => { added = true; return ok({ status: "success", message: "ok", name: b.name }); },
      "GET /api/users/newu": ok({ user: u }),
      "GET /api/users/newu/key": ok({ name: "newu", line: "3.1", min_client: "5.0.1.5", conf: "[Interface]\\nPrivateKey = X\\n[Peer]\\nEndpoint = 203.0.113.10:6714\\n", vpn: "vpn://FAKE" }),
      "GET /api/pages": ok({ available: true, pages: [] }) } }, async (P, rec) => {
      await P.tick(100);
      await P.click(P.byText("Добавить", 'section[data-page="users"] button'));
      await P.type(P.$$(".gx-sheet input")[0], "newu");
      await P.click(P.$$(".gx-sheet button").find((b) => txt(b) === "Создать")); await P.tick(200);
      const sheet = P.$$(".gx-sheet").pop();
      rec.wait = !!sheet.querySelector(".gx-wait");
      rec.enableBtns = sheet.querySelectorAll(".gx-al button").filter((b) => txt(b) === "Включить").length;
      rec.alerts = sheet.querySelectorAll(".gx-al").map(txt);
    });
  }
  // B2-1: свой срок страницы по-русски; одна ошибка; смена выбора гасит её
  await scenario("page_ttl", { hash: "#users", data: PRO, api: annaApi({
    "GET /api/pages": ok({ available: true, pages: [] }),
    "POST /api/action/page-create": (b) => ok({ status: "success", message: "ok", id: "p-7a7a", url: "https://vpn.example.com/p-SECRET/", user: "anna",
      created: iso(NOW), expires: iso(NOW + 86400000 * 2), forever: false, ttl: b.ttl, profiles: ["awg3"], mirror: true, imported_profiles: [], subscription: "exists" }) }) },
  async (P, rec) => {
    await P.tick(100);
    await P.click(annaRow(P));
    await P.tick(100);
    await P.click(P.$$(".gx-upages button").find((b) => /Создать/.test(txt(b)))); await P.tick(50);
    const preset = (re) => P.$$(".gx-dlg .gx-ttlgrid button").find((b) => re.test(txt(b)));
    await P.click(preset(/Своё/)); await P.tick(30);
    const inp = () => P.$$(".gx-dlg input").pop();
    await P.type(inp(), "2 недели");
    await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(50);
    const d = P.$$(".gx-dlg").pop();
    rec.bad = { hints: d.querySelectorAll(".gx-hint--bad").filter((h) => !h.hidden).map(txt), errs: d.querySelectorAll('[role="alert"] .gx-al').map(txt) };
    rec.callsBad = P.calls("POST", "/api/action/page-create").length;
    await P.type(inp(), "2 дня");
    await P.click(P.$$(".gx-dlg__foot button").pop()); await P.tick(100);
    rec.creates = P.calls("POST", "/api/action/page-create").map((c) => c.body.ttl);
  });
  // B-5: русское имя в «Идентификаторе» — подсказка, куда его писать
  await scenario("id_cyr", { hash: "#users" }, async (P, rec) => {
    await P.tick(100);
    await P.click(P.byText("Добавить", 'section[data-page="users"] button'));
    const idIn = P.$$(".gx-sheet input")[0];
    await P.type(idIn, "Мама");
    rec.hint = txt(P.$$(".gx-sheet .gx-hint--bad")[0]);
  });
  // B-13: смена раздела по адресу (и «Назад» браузера) закрывает карточку
  await scenario("card_hash", { hash: "#users", api: annaApi({ "GET /api/pages": ok({ available: true, pages: [] }) }) }, async (P, rec) => {
    await P.tick(100);
    await P.click(annaRow(P)); await P.tick(100);
    rec.open = P.$$(".gx-sheet").length;
    await P.go("#profiles"); await P.tick(100);
    rec.after = P.$$(".gx-sheet").filter((s) => s.isConnected).length;
    rec.page = P.page();
  });
  // B-8: адреса машины ещё собираются — «В ключах …», а не адрес сервера
  for (const [label, patch] of [["keys_wait", { pending: ["edge-status"] }], ["keys_ready", { pending: [], edge: { count: 1, domain_endpoint: true, ips: [], dns: null } }]]) {
    await scenario(label, { hash: "#overview", data: { "/api/overview": (d) => ({ ...d, mode: "pro", domain: "vpn.example.com", ...patch }) } }, async (P, rec) => {
      await P.tick(100);
      const sec = txt(P.$('section[data-page="overview"]')) || "";
      const m = sec.match(/В ключах\s*(\S+)/);
      rec.keys = m ? m[1] : sec.slice(0, 400);
    });
  }
  // A2-10: ⌘K знает порт, MTU, подсеть, обновления, подписку и адрес в VPN
  await scenario("cmdk", { hash: "#users" }, async (P, rec) => {
    await P.tick(100);
    await P.key("k", { metaKey: true });
    rec.q = {};
    for (const q of ["MTU", "порт", "подписк", "обновлен", "10.8.3.3"]) {
      await P.type(".gx-cmd input", q);
      rec.q[q] = P.$$(".gx-cmd__i").map(txt);
    }
  });
  // Придирки: B2-5 регистр в подтверждении, A2-13 Enter в «Переименовать» и последний профиль, A2-2 счётчик подписи
  await scenario("nits_card", { hash: "#users", api: annaApi({ "GET /api/pages": ok({ available: true, pages: [] }),
    "POST /api/action/user-rename": (b) => ok({ status: "success", message: "ok" }) }) }, async (P, rec) => {
    await P.tick(100);
    await P.click(annaRow(P)); await P.tick(100);
    const btn = (re) => P.$$(".gx-sheet button").find((b) => re.test(txt(b) || ""));
    await P.click(btn(/^Убрать профиль/)); await P.tick(50);
    const d = P.$$(".gx-dlg").pop();
    rec.lastWarn = d.querySelectorAll(".gx-al").map(txt);
    const inp = d.querySelector("input");
    rec.autocap = inp.getAttribute("autocapitalize");
    await P.type(inp, "ANNA");
    rec.caseHint = d.querySelectorAll(".gx-hint--bad").filter((h) => !h.hidden).map(txt);
    await P.key("Escape"); await P.tick(50);
    await P.click(btn(/^Переименовать/)); await P.tick(50);
    const rin = P.$$(".gx-dlg input").pop();
    await P.type(rin, "Аня");
    await P.key("Enter", {}, rin); await P.tick(100);
    rec.renames = P.calls("POST", "/api/action/user-rename").map((c) => c.body);
  });
  // B2-10: нет связи с сервером — карточки «Протоколов» приглушены и не нажимаются
  await scenario("profiles_stale", { hash: "#profiles", api: { "GET /api/profiles": { status: 502, body: { ok: false, error: "bridge down", code: "bridge_failed" } } } },
  async (P, rec) => {
    await P.tick(200);
    const c = P.$("#prCards");
    rec.stale = c ? [c.classList.contains("gx-stale"), !!c.inert] : null;
  });
  console.log(JSON.stringify(R));
})();
"""


@unittest.skipUnless(NODE, "нужен node 18+")
class CriticsFix2PanelDom(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="goamnezia-cf2-") as tmp:
            script = os.path.join(tmp, "cf2.js")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(JS)
            proc = subprocess.run([NODE, script, HARNESS, json.dumps(server_pages())], capture_output=True, text=True, timeout=240, cwd=ROOT)
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
        cls.R = json.loads(lines[-1]) if lines else {"__fatal": "no JSON: rc=%s\n%s\n%s" % (proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])}

    def scen(self, name):
        self.assertNotIn("__fatal", self.R, self.R.get("__fatal"))
        rec = self.R.get(name)
        self.assertIsNotNone(rec, name)
        self.assertNotIn("fatal", rec, rec.get("fatal"))
        self.assertEqual(rec["errors"], [], name)
        return rec

    def test_revoke_page_offers_to_reissue_the_subscription(self):
        """B2-2/A2-4: галочка «Перевыпустить и подписку» — включена у страницы, которая подписку завела, выключена у той,
        что отдавала прежнюю; есть, если подписка у пользователя есть сейчас, и её нет без подписки (R-1); мосту —
        sub=on/off; итог говорит про подписку честно (R-4)."""
        rec = self.scen("revoke_ru")
        self.assertEqual(rec["d1"]["title"], "Отозвать страницу для «Анна»?")
        self.assertIn("Страница от 16.10.2026 перестанет открываться сразу", rec["d1"]["text"])
        self.assertNotIn("p-1a2b", rec["d1"]["text"])
        self.assertEqual(rec["d1"]["checks"][0][1], "true")
        self.assertIn("Перевыпустить и подписку", rec["d1"]["checks"][0][0])
        self.assertIn("Подписку завела эта страница", rec["d1"]["checks"][0][0])
        self.assertIn("Страница отозвана, подписка перевыпущена", rec["toast1"])
        self.assertEqual(rec["d2"]["checks"][0][1], "false")
        self.assertNotIn("Подписку завела эта страница", rec["d2"]["checks"][0][0])
        self.assertEqual(rec["d2on"][0][1], "true")
        # R-1: у старой страницы без поля subscription подписка есть сейчас — галочка есть, выключена
        self.assertEqual(len(rec["d3"]["checks"]), 1)
        self.assertEqual(rec["d3"]["checks"][0][1], "false")
        self.assertNotIn("Подписку завела эта страница", rec["d3"]["checks"][0][0])
        self.assertEqual(rec["calls"], [{"id": "p-1a2b", "sub": "on"}, {"id": "p-3c4d", "sub": "on"},
                                        {"id": "p-5e6f", "sub": "on"}])
        # R-4: подписки к отзыву уже не было — честно и не красным
        self.assertIn("подписки у пользователя уже нет — перевыпускать нечего", rec["toast3"][0])
        self.assertNotIn("danger", rec["toast3"][1])
        self.assertNotIn("не удалось", rec["toast3"][0])
        # R-1: подписки нет сейчас — галочки нет и у страницы с subscription: created
        nosub = self.scen("revoke_nosub")
        # (страницу открывали — рядом галочка ключей, B3-3; подписочной нет)
        self.assertEqual([c for c in nosub["d1"]["checks"] if "подписку" in c[0]], [])
        self.assertEqual(nosub["calls"], [{"id": "p-1a2b", "sub": "off"}])
        self.assertIn("Страница отозвана", nosub["toast"])
        en = self.scen("revoke_en")
        self.assertEqual(en["d1"]["title"], "Revoke the page for «Анна»?")
        self.assertIn("Reissue the subscription too", en["d1"]["checks"][0][0])

    def test_page_rows_show_date_not_service_id_and_minutes_for_short_pages(self):
        """B2-3: в строке — «Страница от 16.10.2026», номер — только в подсказке; A2-5: страница на час — «ещё 56 мин»."""
        rec = self.scen("revoke_ru")
        self.assertTrue(rec["rows"][0].startswith("Страница от 16.10.2026"), rec["rows"])
        self.assertRegex(rec["rows"][0], r"ещё 5[56] мин$")
        self.assertNotIn("дн", rec["rows"][0])
        self.assertRegex(rec["rows"][2], r"ещё [56] дн$")
        self.assertEqual(rec["rowsTitle"], ["p-1a2b", "p-3c4d", "p-5e6f"])
        self.assertRegex(self.scen("revoke_en")["rows"][0], r"5[56] min left$")

    def test_page_ready_says_a_subscription_came_with_it(self):
        """B2-2/A2-4: «Страница готова» — строка, что вместе со страницей заведена постоянная подписка."""
        rec = self.scen("ready_sub")
        self.assertIn("Вместе со страницей заведена постоянная подписка", rec["d"]["text"])

    def test_numbers_are_not_glued_and_errors_are_spoken(self):
        """B2-6/A2-1: «1,5» по буквам остаётся «1,5» с ошибкой «только целые ГБ», а не превращается в 15; вставка «0.5» не
        очищает поле; «-5» дней — ошибка; создать с ошибкой нельзя; «00001» уходит мосту единицей."""
        rec = self.scen("numbers_add")
        self.assertEqual(rec["q15"][0], "1,5")
        self.assertTrue(rec["q15"][1][1])
        self.assertIn("Только целые гигабайты", rec["q15"][1][0])
        self.assertEqual(rec["callsAfterBad"], 0)
        self.assertEqual(rec["q05"][0], "0.5")
        self.assertTrue(rec["q05"][1][1])
        self.assertEqual(rec["q1"][1], ["Пусто — без лимита", False])
        self.assertEqual(rec["dNeg"][0], "-5")
        self.assertIn("Отрицательное число не подходит", rec["dNeg"][1][0])
        self.assertEqual(rec["callsAfterNeg"], 0)
        self.assertEqual(rec["d7"], ["Пусто — бессрочно", False])
        self.assertEqual(len(rec["adds"]), 1)
        self.assertEqual((rec["adds"][0]["quota_gb"], rec["adds"][0]["days"]), ("1", "7"))

    def test_quota_paste_and_toast_and_expiry_date_like_the_server(self):
        """A2-1: вставка «0,5» в лимит карточки — в поле и с ошибкой, мост не зовётся; «00001» → quota_gb=1 и всплывашка
        «1 ГБ», без «00001»; A2-6: «7 дней» в окне срока — дата сегодня + 7, как ставит сервер."""
        rec = self.scen("numbers_card")
        self.assertEqual(rec["paste"][0], "0,5")
        self.assertTrue(rec["paste"][1][1])
        self.assertEqual(rec["quotaCallsBad"], 0)
        self.assertEqual(rec["quota"], [{"name": "anna", "quota_gb": "1"}])
        self.assertIn("1 ГБ", rec["quotaToast"])
        self.assertNotIn("00001", rec["quotaToast"])
        self.assertEqual(rec["until7"], "до " + rec["want"][0])
        self.assertEqual(rec["until1"], "до " + rec["want"][1])

    def test_shares_tab_reloads_on_open(self):
        """A2-8: открыли «Временные ссылки» — свежий запрос (X-GoAmnezia-Fresh) и «1 раз» в таблице; B2-9: «Порт ссылки»
        и файл без awg3; A2-9: без обещания «404» на закрытом порту."""
        rec = self.scen("shares_fresh")
        self.assertGreater(rec["after"], rec["before"])
        self.assertEqual(rec["fresh"][-1], "1")
        self.assertIn("1 раз", rec["table"])
        self.assertIn("Порт ссылки", rec["table"])
        self.assertIn("anna-31.conf", rec["table"])
        self.assertIn("её порт закрывается", rec["table"])

    def test_disabled_user_has_no_wait_and_one_banner(self):
        """A2-3/B2-7: выключенному — ни «Ждём первого подключения», ни совета «включите VPN»; плашка «Выключен» с одной
        кнопкой «Включить». Включённому ожидание — как было."""
        off = self.scen("wait_off")
        self.assertFalse(off["wait"])
        self.assertEqual(off["enableBtns"], 1, off["alerts"])
        self.assertFalse(any("Пользователь выключен" in a for a in off["alerts"]), off["alerts"])
        self.assertTrue(self.scen("wait_on")["wait"])

    @unittest.skipUnless(NODE, "нужен node 18+")
    def test_wait_port_from_profiles_or_the_key(self):
        """B2-8: порт для «Подключения не видно» — из данных профиля, без них — из Endpoint ключа карточки; не нашли —
        пусто (фраза без скобок)."""
        with open(os.path.join(ROOT, "admin-web", "static", "app.js"), encoding="utf-8") as fh:
            app = fh.read()
        i = app.index("  const waitPort = (name, pid) => {")
        fn = app[i:app.index("  // Ждём первого подключения", i)]
        js = ("let recs={}; const profRec=(p)=>recs[p]||null; const state={keyCache:{}};\n%s\nconst out=[];"
              "recs={awg3:{port:443}}; out.push(waitPort('u','awg3')); recs={};"
              "out.push(waitPort('u','awg3'));"
              "state.keyCache['u/awg3/']={conf:'[Interface]\\nPrivateKey = X\\n[Peer]\\nEndpoint = vpn.example.com:46567\\n'};"
              "out.push(waitPort('u','awg3')); out.push(waitPort('u','awg2'));"
              "console.log(JSON.stringify(out));" % fn)
        out = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), ["443", "", "46567", ""])

    def test_custom_page_ttl_in_russian_one_error(self):
        """B2-1: «2 недели» — одна подсказка под полем (без второй плашки и без «forever»), мост не зовётся; «2 дня» → 2d."""
        rec = self.scen("page_ttl")
        self.assertEqual(len(rec["bad"]["hints"]), 1, rec["bad"])
        self.assertEqual(rec["bad"]["errs"], [])
        self.assertNotIn("forever", rec["bad"]["hints"][0])
        self.assertEqual(rec["callsBad"], 0)
        self.assertEqual(rec["creates"], ["2d"])

    @unittest.skipUnless(NODE, "нужен node 18+")
    def test_parse_page_ttl_like_the_bot(self):
        with open(os.path.join(ROOT, "admin-web", "static", "app.js"), encoding="utf-8") as fh:
            app = fh.read()
        a = app.index("  const PAGE_TTL_RE = ")
        b = app.index("  const pageTtlLabel = (ttl) =>")
        js = "%s\nconsole.log(JSON.stringify(%s.map(parsePageTtl)));" % (app[a:b], json.dumps(
            ["12 ч", "12ч", "1 час", "2д", "2 дня", "3 дн.", "21 день", "365 дней", "навсегда", "Бессрочно", "12h", "2 days",
             "0 ч", "721 ч", "366 дн", "12", "3 нед", "", "720 часов"], ensure_ascii=False))
        out = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), ["12h", "12h", "1h", "2d", "2d", "3d", "21d", "365d", "forever", "forever", "12h",
                                                  "2d", "", "", "", "", "", "", "720h"])

    def test_b5_cyrillic_id_points_to_the_label(self):
        rec = self.scen("id_cyr")
        self.assertIn("например mama", rec["hint"])
        self.assertIn("«Подпись»", rec["hint"])

    def test_b13_page_change_closes_the_card(self):
        rec = self.scen("card_hash")
        self.assertGreaterEqual(rec["open"], 1)
        self.assertEqual(rec["after"], 0)
        self.assertEqual(rec["page"], "profiles")

    def test_b8_keys_host_waits_for_the_addresses(self):
        self.assertTrue(self.scen("keys_wait")["keys"].startswith("…"), self.scen("keys_wait")["keys"])
        self.assertTrue(self.scen("keys_ready")["keys"].startswith("vpn.example.com"), self.scen("keys_ready")["keys"])

    def test_a2_10_cmdk_finds_profile_params_updates_subscription_and_address(self):
        q = self.scen("cmdk")["q"]
        for key, want in (("MTU", "MTU ·"), ("порт", "Сменить порт ·"), ("подписк", "Подписка"), ("обновлен", "Проверить обновления"),
                          ("10.8.3.3", "Анна")):
            self.assertTrue(any(want in x for x in q[key]), (key, q[key]))

    def test_nits_case_hint_rename_enter_last_profile(self):
        rec = self.scen("nits_card")
        self.assertTrue(any("последний профиль" in a for a in rec["lastWarn"]), rec["lastWarn"])
        self.assertEqual(rec["autocap"], "off")
        self.assertEqual(rec["caseHint"], ["Буквы — как в образце: заглавные и строчные различаются."])
        self.assertEqual(rec["renames"], [{"name": "anna", "label": "Аня"}])

    def test_b2_10_stale_profiles_are_dimmed(self):
        self.assertEqual(self.scen("profiles_stale")["stale"], [True, True])


class CriticsFix2PanelText(unittest.TestCase):
    """Проверки по тексту app.js — там, где DOM не нужен."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "admin-web", "static", "app.js"), encoding="utf-8") as fh:
            cls.app = fh.read()

    def cut(self, a, b):
        i = self.app.index(a)
        return self.app[i:self.app.index(b, i)]

    def test_b2_4_reissue_result_in_a_window(self):
        """B2-4: итог перевыпуска — окно с «ОК» (noticeDialog), а не всплывашка на 3 с."""
        fn = self.cut("  async function reissuePeer(user, pid) {", "  async function removePeer(user, pid) {")
        tail = fn[fn.index('action("user-reissue-peer"'):]
        self.assertNotIn("doneMessage", fn)
        self.assertIn('title: t("reissueKeyDoneT")', tail)
        self.assertIn('text: tf("reissueKeyDone"', tail)


if __name__ == "__main__":
    unittest.main()

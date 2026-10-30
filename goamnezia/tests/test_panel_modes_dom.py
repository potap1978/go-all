# -*- coding: utf-8 -*-
"""Режимы 0.6.0 в панели и мини-аппе (docs/MODES_047.md, «Договор 0.6.0»; дизайн iteration3, F-1…F-3).

F-2 — названия режимов по id из таблицы договора, RU и EN: «Pro — сайт и VPN на 443», «Semi-Pro — VPN и сайт»,
«Lite — только VPN». В тексте — целиком; в заголовке карточки режима и на Обзоре — имя, а подпись строкой ниже, как в
макете. Прежних названий («Только VPN», «VPN и сайт», «Сайт и VPN на 443») не остаётся ни в одном состоянии.

F-1 — «Ленивый Pro» в «Настройки → Режим и сайт»: карточка первой (порядок договора), окно «домен → план (lazy-check:
pro / semi_pro / blocked с причинами) → подтверждение → выполнение (lazy-setup, долгое, как смена режима) → итог (ключи,
страницы, предупреждения)». Мост — заглушка с полями договора. По итогам ядра: no_line_for_443 (итог Semi-Pro без
«UDP 443 занят»), dns_unknown, awg2_container_busy, редкие предупреждения, отказ lazy-setup с plan и reasons, линия на
443 — только из udp443_line, длинная строка «кто держит порт».

F-3 — какая линия стоит на UDP 443 в Pro (pro-line): окно «Линия на UDP 443» (выбор → подтверждение → выполнение →
итог), «Перейти: 2.0 на 443» и «3.1 на 443» у не текущего Pro (смена режима и сразу pro-line), плашка «на UDP 443
никого»; подхваченный у приложения профиль — отдельным окном с вводом имени (adopted_keyless → adopted_confirm).

Панель грузится целиком (index.html + app.js) в игрушечном DOM под node — tests/fixtures/panel_dom.js, как в
tests/test_panel_dom.py; без node проверки пропускаются.

Запуск: python3 -m unittest tests/test_panel_modes_dom.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_panel_dom import HARNESS, NODE, ROOT, STATIC, read_text  # noqa: E402

SCENARIOS_JS = r"""
const { boot } = require(process.argv[2]);
const R = {};
async function scenario(name, opts, body) {
  const rec = { errors: [] };
  R[name] = rec;
  let P = null;
  try {
    // GA_PANEL_DOM_ROOT — другой корень панели (так сценарии гоняются на прежнем коде, test_panel_dom.py).
    P = await boot({ root: process.env.GA_PANEL_DOM_ROOT || undefined, ...opts });
    await body(P, rec);
  } catch (e) {
    rec.fatal = String((e && e.stack) || e);
  }
  if (P) { rec.errors = P.errors.slice(); P.close(); }
}
const txt = (n) => (n ? n.textContent.replace(/\s+/g, " ").trim() : null);
const all = (P, sel) => P.$$(sel).map(txt);

// ── Данные сервера под режим ──
const PF = (o = {}) => ({ available: true, error: null, lite_site_available: true, pro_available: true, hint: "", modes: {
  lite: { available: true }, lite_site: { available: true }, pro: { available: true },
  ports: { tcp80: { state: "free" }, tcp443: { state: "free" }, udp443: { state: "free" } }, warnings: [], hints: {}, ...o } });
const API = (extra = {}) => ({
  "GET /api/preflight": { status: 200, body: { ok: true, data: PF() } },
  "POST /api/action/status": { status: 200, body: { ok: true, data: { status: "success", message: "ok", profiles: [] } } },
  "POST /api/action/site-check": { status: 200, body: { ok: true, data: { status: "success", verdict: "ok", site: { cert: "letsencrypt", cert_days_left: 60 } } } },
  ...extra,
});
const DATA = (mode, extra = {}) => {
  const domain = mode === "lite" ? "" : "vpn.example.com";
  return {
    "/api/overview": (d) => ({ ...d, mode, domain }),
    "/api/settings": (d) => ({ ...d, config: { ...d.config, mode, domain } }),
    "/api/profiles": (d) => ({ ...d, mode }),
    ...extra,
  };
};
const SG_TABS = ["look", "mode", "clients", "mirror", "access", "lang", "about"];
async function sgTab(P, k) {
  const sel = P.$("#sgTabSel");
  if (sel) { sel.value = k; sel.dispatchEvent(new P.win.Event("change", { bubbles: true })); await P.settle(); await P.tick(50); return; }
  await P.click(P.$$(".gx-stabs button")[SG_TABS.indexOf(k)]);
}

// ── F-2: прежние названия режимов на экране ──
// Подпись режима («VPN и сайт») совпадает с прежним названием: в элементах подписи (.gx-kpi__cap, .gx-tc__sub) она
// законна — её проверяют отдельно, рядом с именем. Везде ещё прежнее слово допустимо только внутри полного названия.
const OLD = /Только VPN|VPN и сайт|Сайт и VPN на 443|VPN only|VPN and site|Site and VPN on 443/i;
const FULL = /Lite — только VPN|Semi-Pro — VPN и сайт|Pro — сайт и VPN на 443|Lite — VPN only|Semi-Pro — VPN and site|Pro — site and VPN on 443/g;
function oldNames(P) {
  const out = [];
  const walk = (n) => n.childNodes.forEach((k) => {
    if (k.nodeType === 1) {
      if (k.localName === "script" || k.localName === "style") return;
      if (k.classList.contains("gx-kpi__cap") || k.classList.contains("gx-tc__sub")) return;
      for (const a of ["aria-label", "title", "placeholder", "alt"]) {
        const v = k.getAttribute(a);
        if (v && OLD.test(v.replace(FULL, ""))) out.push(`@${a}: ${v}`);
      }
      walk(k);
    } else if (k.nodeType === 3 && OLD.test(k.data.replace(FULL, ""))) {
      out.push(`${k.parentNode.getAttribute("class") || k.parentNode.localName}: ${k.data.trim().slice(0, 90)}`);
    }
  });
  walk(P.doc);
  return out;
}
// Имя и подпись в карточках режима: заголовок и строка под ним.
const cards = (P) => P.$$('section[data-page="settings"] .gx-tc').map((c) => ({
  title: txt(c.querySelector(".gx-tc__title")), sub: txt(c.querySelector(".gx-tc__sub")),
  chips: c.querySelectorAll(".gx-tc__head .gx-chip").map(txt), btns: c.querySelectorAll(".gx-tc__btns button").map(txt),
  alert: txt(c.querySelector(".gx-tc__al")), desc: txt(c.querySelector(".gx-tc__desc")) }));
const kpi = (P) => ({ mode: txt(P.$(".gx-kpi__mode")), cap: txt(P.$(".gx-kpi__cap")) });
const secDesc = (P, title) => {
  const h = P.$$('section[data-page="settings"] .gx-card h2').find((n) => txt(n) === title);
  return h ? txt(h.closest(".gx-card__tt").querySelector(".gx-desc")) : null;
};
const kvRow = (P, key) => {
  const row = P.$$(".gx-kv__row").find((r) => txt(r.querySelector(".gx-kv__k")) === key);
  return row ? txt(row.querySelector(".gx-kv__v")) : null;
};
const dlg = (P) => {
  const d = P.$$(".gx-dlg").pop();
  if (!d) return null;
  const title = d.querySelector(".gx-dlg__title") || d.querySelector(".gx-sheet__title");
  return { title: txt(title), sub: txt(d.querySelector(".gx-sheet__sub")), text: txt(d),
    foot: d.querySelectorAll(".gx-dlg__foot button").map(txt) };
};

// ── Заглушка моста 0.6.0: действия и ответы — поля договора (docs/MODES_047.md, «Договор 0.6.0») ──
const NEW_ACTIONS = {
  "lazy-check": { readonly: true, destructive: false, secret: false, params: { domain: { ref: "domain", required: true } }, description: {} },
  "lazy-setup": { readonly: false, destructive: true, secret: false,
    params: { domain: { ref: "domain", required: true }, keys: { ref: "lazy_keys", required: false, default: 5 } }, description: {} },
  "pro-line": { readonly: false, destructive: true, secret: false,
    params: { profile: { ref: "profile", required: true }, adopted_confirm: { ref: "adopted_confirm", required: false } }, description: {} },
};
// Каталог ядра 0.6.0 эти действия уже знает (lib/actions.json) — его определения главнее заглушки; «старое ядро» —
// каталог без них.
const withActions = (d) => ({ ...d, actions: { ...NEW_ACTIONS, ...d.actions } });
const withoutNew = (d) => { const a = { ...d.actions }; Object.keys(NEW_ACTIONS).forEach((k) => { delete a[k]; }); return { ...d, actions: a }; };
const OK = (data) => ({ status: 200, body: { ok: true, data: { status: "success", message: "ok", ...data } } });
const ERR = (status, code, message, extra = {}) => ({ status, body: { ok: false, error: message, code, data: { status: "error", code, message, ...extra } } });
// udp443_line — какая линия встанет на UDP 443 (договор, «По итогам ядра»): на чистом сервере при плане pro — 2.0.
const CHECK = (plan, o = {}) => ({ domain: "vpn.example.com", domain_display: "vpn.example.com", points_here: true, a: ["203.0.113.10"], aaaa: [],
  server_ips: ["203.0.113.10"], cloudflare_proxied: false,
  ports: { tcp80: { free: true, by: null }, tcp443: { free: true, by: null }, udp443: { free: true, by: null } }, app_containers: [], plan, reasons: [],
  udp443_line: plan === "pro" ? "2.0" : null, ...o });
const KEYS = [["foggy_london", "Туманный Лондон"], ["sunny_manila", "Солнечная Манила"], ["snowy_ottawa", "Снежная Оттава"],
  ["misty_lisbon", "Туманный Лиссабон"], ["windy_wellington", "Ветреный Веллингтон"]]
  .map(([name, label], i) => ({ name, label, page: `https://vpn.example.com/p-FAKESECRET${i}FAKESECRETFAKESECRETFAKESECRET0/` }));
const SETUP = (o = {}) => ({ mode: "pro", domain: "vpn.example.com", udp443: { profile: "awg2", line: "2.0" },
  profiles: [{ id: "awg2", line: "2.0", port: 443 }, { id: "awg3", line: "3.1", port: 51234 }], site: { template: "h5up_hyperspace" },
  panel_on_domain: true, keys: KEYS, warnings: [], ...o });
function deferred() { let release; const p = new Promise((res) => { release = res; }); return { p, release }; }
const MSG = {
  ru: { check: "Проверить", next: "Дальше", back: "Назад", go: "Запустить Ленивый Pro", done: "Готово", recheck: "Проверить снова",
    hide: "Свернуть окно", retry: "Попробовать снова", close: "Закрыть", run: "Запустить" },
  en: { check: "Check", next: "Next", back: "Back", go: "Run Lazy Pro", done: "Done", recheck: "Check again",
    hide: "Hide the window", retry: "Try again", close: "Close", run: "Run" },
};
const lazyBtn = (P) => { const c = P.$$('section[data-page="settings"] .gx-tc')[0]; return c ? c.querySelector(".gx-tc__btns button") : null; };
const footBtn = (P, label) => P.$$(".gx-dlg__foot button").find((b) => txt(b) === label) || null;
const tone = (n, re) => ((n && n.getAttribute("class")) || "").match(re);
// Окно целиком: шаги, плашки, строки с отметкой, таблица портов, пары «ключ — значение», список ключей, подвал.
function dlgState(P) {
  const d = P.$$(".gx-dlg").pop();
  if (!d) return null;
  const body = d.querySelector(".gx-dlg__body");
  const emp = body.querySelector(".gx-empty");
  return {
    title: txt(d.querySelector(".gx-sheet__title")), sub: txt(d.querySelector(".gx-sheet__sub")),
    steps: body.querySelectorAll(".gx-step").map((s) => `${txt(s).replace(/^\d/, "")}${s.classList.contains("is-on") ? "*" : ""}${s.classList.contains("is-done") ? "+" : ""}`),
    p: body.querySelectorAll(":scope > .gx-p").map(txt),
    al: body.querySelectorAll(":scope > .gx-al").map((a) => `${(tone(a, /gx-al--(\w+)/) || [])[1]}: ${txt(a)}`),
    stat: body.querySelectorAll(".gx-stat__row").map((r) => `${(tone(r.querySelector(".gx-stat__m"), /gx-stat__m--(\w+)/) || [])[1]}: ${txt(r.querySelector(".gx-stat__t"))}${r.querySelector(".gx-stat__s") ? ` | ${txt(r.querySelector(".gx-stat__s"))}` : ""}`),
    table: body.querySelectorAll(".gx-tbl__r, .gx-ptbl__r").map(txt),
    kv: body.querySelectorAll(".gx-kv__row").map((r) => `${txt(r.querySelector(".gx-kv__k"))} = ${txt(r.querySelector(".gx-kv__val"))}`),
    kvBtns: body.querySelectorAll(".gx-kv__btns button").map(txt),
    list: body.querySelectorAll(".gx-list__i").map((i) => ({ title: txt(i.querySelector(".gx-list__title")), meta: txt(i.querySelector(".gx-list__meta")),
      btns: i.querySelectorAll(".gx-list__btns > *").map(txt), href: i.querySelector("a") ? i.querySelector("a").getAttribute("href") : null })),
    sub2: body.querySelectorAll(".gx-sub").map(txt),
    busy: txt(body.querySelector(".gx-busy__t")), clock: txt(body.querySelector(".gx-busy__c")),
    empty: emp ? { t: txt(emp.querySelector(".gx-empty__t")), x: txt(emp.querySelector(".gx-empty__x")), tone: (tone(emp.querySelector(".gx-empty__ic"), /gx-empty__ic--(\w+)/) || [])[1] || "accent" } : null,
    details: !!body.querySelector(".gx-details"),
    hint: txt(body.querySelector(".gx-hint")), hintBad: !!body.querySelector(".gx-hint--bad"),
    input: body.querySelector("input") ? body.querySelector("input").value : null,
    foot: d.querySelectorAll(".gx-dlg__foot button").map((b) => `${txt(b)}${b.disabled ? " [off]" : ""}`),
    close: !!d.querySelector(".gx-close") && !d.querySelector(".gx-close").hidden,
    sheet: P.$$(".gx-dlg-bd--sheet").length > 0,
  };
}
const LZ = (lang, api, extra = {}) => ({ hash: "#settings", lang, sessionStorage: { "goamnezia-sg-tab": "mode" },
  data: { ...DATA("lite_site"), "/api/actions": withActions }, api: API(api), ...extra });

(async () => {
  // F-2: три режима × RU/EN × компьютер и мини-апп: Обзор, карточки режимов, «Сейчас», проверка портов, «О программе»;
  // потом — прогон по всем разделам и вкладкам настроек в поисках прежних названий.
  for (const mode of ["lite", "lite_site", "pro"]) {
    for (const lang of ["ru", "en"]) {
      for (const dev of ["desk", "tg"]) {
        const opts = { hash: "#overview", lang, data: DATA(mode), api: API(), ...(dev === "tg" ? { tg: true, width: 390 } : {}) };
        await scenario(`names_${mode}_${lang}_${dev}`, opts, async (P, rec) => {
          rec.kpi = kpi(P);
          rec.old = {};
          rec.old.overview = oldNames(P);
          await P.go("#settings");
          await sgTab(P, "mode");
          rec.cards = cards(P);
          rec.modeDesc = secDesc(P, lang === "ru" ? "Режим сервера" : "Server mode");
          rec.stat = P.$$('section[data-page="settings"] .gx-stat__t').map(txt);
          rec.old.settingsMode = oldNames(P);
          for (const k of ["clients", "mirror", "access", "lang", "about"]) {
            await sgTab(P, k);
            rec.old[`settings_${k}`] = oldNames(P);
          }
          rec.about = kvRow(P, lang === "ru" ? "Режим" : "Mode");
          for (const page of ["users", "profiles", "traffic", "backups", "logs", "agents"]) {
            await P.go(`#${page}`);
            rec.old[page] = oldNames(P);
          }
        });
      }
    }
  }
  // Ядро без lazy-check, lazy-setup и pro-line (каталог без них): прежние кнопки и слова, у Ленивого Pro — ни одной кнопки.
  for (const mode of ["lite", "lite_site", "pro"]) {
    await scenario(`old_core_${mode}`, { hash: "#settings", lang: "ru", sessionStorage: { "goamnezia-sg-tab": "mode" },
      data: { ...DATA(mode), "/api/actions": withoutNew }, api: API() }, async (P, rec) => {
      rec.cards = cards(P);
    });
  }
  // Окна со словами о режимах: смена режима (каждая цель), уход в Lite, «Занять UDP/443» в Semi-Pro.
  for (const lang of ["ru", "en"]) {
    await scenario(`dialogs_${lang}`, { hash: "#settings", lang, sessionStorage: { "goamnezia-sg-tab": "mode" }, data: DATA("lite_site"), api: API() }, async (P, rec) => {
      rec.old = {};
      rec.dialogs = {};
      const cardBtn = (title) => {
        const c = P.$$('section[data-page="settings"] .gx-tc').find((n) => txt(n.querySelector(".gx-tc__title")) === title);
        return c ? c.querySelector(".gx-tc__btns button") : null;
      };
      for (const [key, title] of [["pro", "Pro"], ["lite", "Lite"]]) {
        const b = cardBtn(title);
        rec.dialogs[key] = b ? null : "no button";
        if (!b) continue;
        await P.click(b);
        rec.dialogs[key] = dlg(P);
        rec.old[key] = oldNames(P);
        await P.key("Escape");
      }
      await P.go("#profiles");
      const take = P.$$('section[data-page="profiles"] button').find((b) => /UDP\/443/.test(txt(b)));
      rec.take443 = !!take;
      if (take) {
        await P.click(take);
        rec.dialogs.take443 = dlg(P);
        rec.old.take443 = oldNames(P);
      }
    });
  }

  // ── F-1: Ленивый Pro ──
  // Весь путь: карточка → домен (https://, путь и точка отрезаются) → план pro → подтверждение (7 ключей) → выполнение
  // (плашка «Выполняется», окно не закрыть, Esc тоже) → итог. RU/EN, компьютер и мини-апп.
  for (const lang of ["ru", "en"]) {
    for (const dev of ["desk", "tg"]) {
      const hold = deferred();
      const T = MSG[lang];
      await scenario(`lazy_flow_${lang}_${dev}`, LZ(lang, { "POST /api/action/lazy-check": () => OK(CHECK("pro")),
        "POST /api/action/lazy-setup": () => hold.p.then(() => OK(SETUP())) }, dev === "tg" ? { tg: true, width: 390 } : {}), async (P, rec) => {
        // Мини-апп открывается с подписью Telegram в адресе — на Обзоре; в «Настройки» — переходом.
        if (P.page() !== "settings") await P.go("#settings");
        rec.page = P.page();
        rec.cards = cards(P);
        await P.click(lazyBtn(P));
        rec.s1 = dlgState(P);
        await P.type("#sgLzDomain", "https://VPN.Example.com/path.");
        rec.s1typed = dlgState(P);
        await P.click(footBtn(P, T.check));
        rec.checkBodies = P.calls("POST", "/api/action/lazy-check").map((c) => c.body);
        rec.s2 = dlgState(P);
        await P.click(footBtn(P, T.next));
        rec.s3 = dlgState(P);
        await P.type("#sgLzKeys", "7");
        await P.click(footBtn(P, T.go));
        await P.tick(1500);
        rec.s4 = dlgState(P);
        rec.progress = { shown: !P.$("#progress").hidden, text: txt(P.$("#progress .gx-busy__t")), busy: P.doc.documentElement.classList.contains("is-busy") };
        await P.key("Escape");
        rec.escKept = !!P.$(".gx-dlg");
        const before = P.calls("GET", "/api/settings").length;
        hold.release();
        await P.settle();
        await P.tick(50);
        rec.setupBodies = P.calls("POST", "/api/action/lazy-setup").map((c) => c.body);
        rec.s5 = dlgState(P);
        rec.reloaded = P.calls("GET", "/api/settings").length - before;
        rec.after = { shown: !P.$("#progress").hidden, busy: P.doc.documentElement.classList.contains("is-busy") };
        await P.click(footBtn(P, T.done));
        rec.closed = !P.$(".gx-dlg");
      });
    }
  }
  // Semi-Pro: UDP 443 держит контейнер приложения — план semi_pro, итог lite_site без профиля на 443, предупреждения
  // знакомые и незнакомые, панель на домене — адресом с копированием.
  for (const lang of ["ru", "en"]) {
    const T = MSG[lang];
    await scenario(`lazy_semi_${lang}`, LZ(lang, {
      "POST /api/action/lazy-check": () => OK(CHECK("semi_pro", { ports: { tcp80: { free: true, by: "" }, tcp443: { free: true, by: "" },
        udp443: { free: false, by: "amnezia-xray" } }, reasons: ["udp443_busy"], app_containers: [{ name: "amnezia-xray", line: "", port: 443 }] })),
      "POST /api/action/lazy-setup": () => OK(SETUP({ mode: "lite_site", udp443: null, site: { template: "" },
        profiles: [{ id: "awg3", line: "3.1", port: 51234 }, { id: "awg2", line: "2.0", port: 40211 }],
        panel_on_domain: "https://vpn.example.com/app-AbCdEf123456/", keys: KEYS.slice(0, 2).concat([{ name: "calm_rome", label: "Тихий Рим", page: "" }]),
        warnings: ["udp443_busy", "template_fallback", "mystery_code"] })) }), async (P, rec) => {
      await P.click(lazyBtn(P));
      await P.type("#sgLzDomain", "vpn.example.com");
      await P.click(footBtn(P, T.check));
      rec.s2 = dlgState(P);
      await P.click(footBtn(P, T.next));
      rec.s3 = dlgState(P);
      await P.click(footBtn(P, T.go));
      rec.s5 = dlgState(P);
      rec.setupBodies = P.calls("POST", "/api/action/lazy-setup").map((c) => c.body);
    });
  }
  // blocked: TCP 443 чужой, домен смотрит не сюда, незнакомый код — «Дальше» нет, есть «Проверить снова»; повтор — pro.
  for (const lang of ["ru", "en"]) {
    const T = MSG[lang];
    let n = 0;
    await scenario(`lazy_blocked_${lang}`, LZ(lang, { "POST /api/action/lazy-check": () => (n++ === 0 ? OK(CHECK("blocked", { points_here: false,
      a: ["198.51.100.50"], ports: { tcp80: { free: true, by: "" }, tcp443: { free: false, by: "nginx · goTelegram Pro" }, udp443: { free: true, by: "" } },
      reasons: ["tcp443_busy", "dns_elsewhere", "weird_code"] })) : OK(CHECK("pro"))) }), async (P, rec) => {
      await P.click(lazyBtn(P));
      await P.type("#sgLzDomain", "vpn.example.com");
      await P.click(footBtn(P, T.check));
      rec.s2 = dlgState(P);
      rec.noNext = !footBtn(P, T.next);
      await P.click(footBtn(P, T.recheck));
      rec.s2b = dlgState(P);
      rec.calls = P.calls("POST", "/api/action/lazy-check").length;
      await P.click(footBtn(P, T.back));
      rec.s1back = dlgState(P);
    });
  }
  // Домен: кириллица → punycode, мусор — отказ до моста, пусто — кнопка неактивна; прокси Cloudflare, AAAA, контейнеры приложения.
  await scenario("lazy_domain_ru", LZ("ru", { "POST /api/action/lazy-check": (b) => OK(CHECK("pro", { domain: b.domain, domain_display: "пример.рф",
    points_here: false, cloudflare_proxied: true, a: ["104.21.3.4", "172.67.1.2"], aaaa: ["2606:4700::1"],
    app_containers: [{ name: "amnezia-awg", line: "1.5", port: 51820 }] })) }), async (P, rec) => {
    await P.click(lazyBtn(P));
    rec.empty = dlgState(P);
    await P.type("#sgLzDomain", "vpn_example");
    rec.bad = dlgState(P);
    await P.click(footBtn(P, "Проверить"));
    rec.badCalls = P.calls("POST", "/api/action/lazy-check").length;
    await P.type("#sgLzDomain", "Пример.РФ");
    rec.idn = dlgState(P);
    await P.key("Enter", {}, "#sgLzDomain");
    rec.checkBodies = P.calls("POST", "/api/action/lazy-check").map((c) => c.body);
    rec.s2 = dlgState(P);
  });
  // Отказы: проверка не прошла (шаг «Домен» с причиной); сервер занят; сценарий упал — «Попробовать снова» ведёт к домену.
  for (const lang of ["ru", "en"]) {
    const T = MSG[lang];
    await scenario(`lazy_errors_${lang}`, LZ(lang, { "POST /api/action/lazy-check": (b) => (b.domain === "bad.example.com"
      ? ERR(400, "bad_arg", "параметр domain: недопустимое значение") : OK(CHECK("pro"))),
    "POST /api/action/lazy-setup": (b) => (b.keys === "3" ? ERR(409, "lock_timeout", "busy", { busy_action: "profile-install", busy_seconds: 42 })
      : ERR(409, "cert_failed", "Let’s Encrypt отказал: домен ведёт не сюда")) }), async (P, rec) => {
      await P.click(lazyBtn(P));
      await P.type("#sgLzDomain", "bad.example.com");
      await P.click(footBtn(P, T.check));
      rec.checkErr = dlgState(P);
      await P.type("#sgLzDomain", "vpn.example.com");
      await P.click(footBtn(P, T.check));
      await P.click(footBtn(P, T.next));
      for (const v of ["0", "21", "x"]) {
        await P.type("#sgLzKeys", v);
        rec[`keys_${v}`] = dlgState(P);
      }
      await P.type("#sgLzKeys", "3");
      await P.click(footBtn(P, T.go));
      rec.busy = dlgState(P);
      await P.click(footBtn(P, T.retry));
      rec.retry = dlgState(P);
      await P.click(footBtn(P, T.check));
      await P.click(footBtn(P, T.next));
      await P.type("#sgLzKeys", "4");
      await P.click(footBtn(P, T.go));
      rec.fail = dlgState(P);
      rec.toast = txt(P.$("#toastHost"));
    });
  }
  // Окно свернули на выполнении — итог приходит тостом; окна больше нет.
  {
    const hold = deferred();
    await scenario("lazy_hidden_ru", LZ("ru", { "POST /api/action/lazy-check": () => OK(CHECK("pro")),
      "POST /api/action/lazy-setup": () => hold.p.then(() => OK(SETUP())) }), async (P, rec) => {
      await P.click(lazyBtn(P));
      await P.type("#sgLzDomain", "vpn.example.com");
      await P.click(footBtn(P, "Проверить"));
      await P.click(footBtn(P, "Дальше"));
      await P.click(footBtn(P, "Запустить Ленивый Pro"));
      await P.click(footBtn(P, "Свернуть окно"));
      await P.tick(1000);
      rec.hidden = !P.$(".gx-dlg");
      // Окно свёрнуто, а плашка «Выполняется» над разделом осталась, и кнопки раздела ждут.
      rec.bar = { shown: !P.$("#progress").hidden, text: txt(P.$("#progress .gx-busy__t")), busy: P.doc.documentElement.classList.contains("is-busy") };
      hold.release();
      await P.settle();
      await P.tick(50);
      rec.toast = txt(P.$("#toastHost"));
      rec.dlgAfter = !!P.$(".gx-dlg");
    });
  }

  // ── F-1 по итогам ядра (договор, «По итогам ядра (28.09)») ──
  // План → подтверждение для одного ответа lazy-check; дальше — итог lazy-setup (или его отказ).
  const lazyTo = async (P, lang, stop) => {
    const T = MSG[lang];
    await P.click(lazyBtn(P));
    await P.type("#sgLzDomain", "vpn.example.com");
    await P.click(footBtn(P, T.check));
    const s2 = dlgState(P);
    if (stop === "plan" || !footBtn(P, T.next)) return { s2 };
    await P.click(footBtn(P, T.next));
    const s3 = dlgState(P);
    if (stop === "confirm") return { s2, s3 };
    await P.click(footBtn(P, T.go));
    return { s2, s3, s5: dlgState(P) };
  };
  // «Lite → Ленивый Pro»: обе линии уже на своих портах (no_line_for_443) — итог Semi-Pro, без слов «UDP 443 занят».
  for (const lang of ["ru", "en"]) {
    await scenario(`lazy_noline_${lang}`, LZ(lang, { "POST /api/action/lazy-check": () => OK(CHECK("semi_pro", { reasons: ["no_line_for_443"] })),
      "POST /api/action/lazy-setup": () => OK(SETUP({ mode: "lite_site", udp443: null, profiles: [{ id: "awg3", line: "3.1", port: 51234 }, { id: "awg2", line: "2.0", port: 39001 }],
        warnings: ["no_line_for_443"] })) }, { data: { ...DATA("lite"), "/api/actions": withActions } }), async (P, rec) => {
      Object.assign(rec, await lazyTo(P, lang, "result"));
    });
  }
  // LZ-05: установленный сервер — порты держим мы сами (ours) и три ключа прошлого запуска засчитываются (keys_have).
  for (const lang of ["ru", "en"]) {
    await scenario(`lazy_ours_${lang}`, LZ(lang, { "POST /api/action/lazy-check": () => OK(CHECK("pro", {
      ports: { tcp80: { free: true, by: null, ours: true }, tcp443: { free: true, by: null, ours: true }, udp443: { free: true, by: null, ours: true } },
      keys_have: 3, keys_new: 2 })) }), async (P, rec) => {
      Object.assign(rec, await lazyTo(P, lang, "confirm"));
      const inp = P.$("#sgLzKeys");
      inp.value = "2";
      inp.dispatchEvent(new P.win.Event("input", { bubbles: true }));
      await P.settle();
      rec.hint2 = dlgState(P).hint;
    });
  }
  // Линия на 443 — из udp443_line: «3.1» (2.0 уже на своём порту) и поля нет вовсе — линию не называем.
  for (const [label, extra] of [["lazy_line31_ru", { udp443_line: "3.1" }], ["lazy_lineless_ru", { udp443_line: undefined }], ["lazy_linenull_ru", { udp443_line: null }]]) {
    await scenario(label, LZ("ru", { "POST /api/action/lazy-check": () => OK(CHECK("pro", extra)) }), async (P, rec) => {
      Object.assign(rec, await lazyTo(P, "ru", "confirm"));
    });
  }
  // DNS не ответил, часть записей чужие, место 2.0 занял контейнер приложения с людьми — план blocked.
  for (const [label, o] of [
    ["lazy_dnsunk_ru", { points_here: false, a: [], reasons: ["dns_unknown"] }],
    ["lazy_dnspart_ru", { points_here: false, a: ["203.0.113.10", "198.51.100.50"], reasons: ["dns_partial"] }],
    ["lazy_awg2busy_ru", { reasons: ["awg2_container_busy"], app_containers: [{ name: "amnezia-awg2", line: "2.0", port: 51820 }] }],
    ["lazy_awg2busy_en", { reasons: ["awg2_container_busy"] }],
  ]) {
    const lang = label.endsWith("_en") ? "en" : "ru";
    await scenario(label, LZ(lang, { "POST /api/action/lazy-check": () => OK(CHECK("blocked", o)) }), async (P, rec) => {
      Object.assign(rec, await lazyTo(P, lang, "plan"));
    });
  }
  // Кто держит порт — длинной строкой предполётной проверки: целиком, в переносимой ячейке (компьютер и телефон).
  const LONG = {
    tcp80: "nginx (PID 812, юнит nginx.service, слушает 0.0.0.0:80 и [::]:80) — похоже, это goTelegram Pro: сайт-маскировка и выдача сертификатов соседнего продукта",
    tcp443: "haproxy (PID 1290, юнит haproxy.service, конфигурация /etc/haproxy/haproxy.cfg, frontend https-in) — похоже, это чужая панель 3x-ui с переадресацией на xray",
    udp443: "docker-proxy (PID 2201, юнит docker.service, контейнер amnezia-xray-with-a-very-long-name-from-the-app) — похоже, это приложение Amnezia",
  };
  for (const [label, extra] of [["lazy_longby_desk", {}], ["lazy_longby_tg", { tg: true, width: 390 }]]) {
    await scenario(label, LZ("ru", { "POST /api/action/lazy-check": () => OK(CHECK("blocked", { reasons: ["tcp80_busy", "tcp443_busy"],
      ports: { tcp80: { free: false, by: LONG.tcp80 }, tcp443: { free: false, by: LONG.tcp443 }, udp443: { free: false, by: LONG.udp443 } } })) }, extra), async (P, rec) => {
      if (P.page() !== "settings") await P.go("#settings");
      Object.assign(rec, await lazyTo(P, "ru", "plan"));
      const d = P.$$(".gx-dlg").pop();
      // В какой ячейке стоит строка «кто держит»: класс ячейки и её родителя — перенос задаёт CSS (overflow-wrap).
      rec.cells = Object.values(LONG).map((s) => {
        const n = d.querySelectorAll(".gx-tbl__t").find((x) => txt(x) === s);
        return n ? `${n.parentNode.getAttribute("class")} > ${n.getAttribute("class")}` : null;
      });
      rec.table = d.querySelector(".gx-tbl") ? d.querySelector(".gx-tbl").getAttribute("style") : "phone";
    });
  }
  // Редкие предупреждения сценария и обойдённые причины проверки — словами; незнакомый код — как есть.
  await scenario("lazy_rarewarn_ru", LZ("ru", { "POST /api/action/lazy-check": () => OK(CHECK("pro")),
    "POST /api/action/lazy-setup": () => OK(SETUP({ warnings: ["adopt_failed", "firewall_open_failed", "line_skipped", "profile_failed", "site_failed",
      "timers_failed", "aaaa_foreign", "cloudflare_proxied", "brand_new_code"] })) }), async (P, rec) => {
    Object.assign(rec, await lazyTo(P, "ru", "result"));
  });
  await scenario("lazy_rarewarn_en", LZ("en", { "POST /api/action/lazy-check": () => OK(CHECK("pro")),
    "POST /api/action/lazy-setup": () => OK(SETUP({ warnings: ["adopt_failed", "firewall_open_failed", "line_skipped", "profile_failed", "site_failed",
      "timers_failed", "no_line_for_443"] })) }), async (P, rec) => {
    Object.assign(rec, await lazyTo(P, "en", "result"));
  });
  // Отказ lazy-setup до изменений несёт plan и reasons — показаны как на шаге «План»; «Попробовать снова» — к домену.
  for (const lang of ["ru", "en"]) {
    await scenario(`lazy_refused_${lang}`, LZ(lang, { "POST /api/action/lazy-check": () => OK(CHECK("pro")),
      "POST /api/action/lazy-setup": () => ERR(409, "port_busy", lang === "ru" ? "TCP/443 занят: nginx (PID 812, юнит nginx.service) — похоже, это goTelegram Pro. Чужое не останавливаю"
        : "TCP/443 is taken: nginx (PID 812, unit nginx.service) — looks like goTelegram Pro. I do not stop other software",
      { plan: "blocked", reasons: ["tcp443_busy", "dns_elsewhere", "odd_reason"] }) }), async (P, rec) => {
      Object.assign(rec, await lazyTo(P, lang, "result"));
      await P.click(footBtn(P, MSG[lang].retry));
      rec.retry = dlgState(P);
    });
  }

  // ── F-3: линия на UDP 443 (pro-line) ──
  const PROFS = (o = {}) => [{ profile: "awg3", line: "3.1", port: 443, peers: 2, state: "running", container: "amnezia-awg3" },
    { profile: "awg2", line: "2.0", port: 39001, peers: 1, state: "running", container: "amnezia-awg2" }].map((p) => ({ ...p, ...(o[p.profile] || {}) }));
  const STATUS = (profiles, extra = {}) => ({ "POST /api/action/status": OK({ profiles, ...extra }) });
  const PL = (lang, mode, api, extra = {}) => ({ hash: "#settings", lang, sessionStorage: { "goamnezia-sg-tab": "mode" },
    data: { ...DATA(mode), "/api/actions": withActions }, api: API(api), ...extra });
  const proCard = (P) => cards(P).find((c) => c.title === "Pro");
  const proBtn = (P, label) => {
    const c = P.$$('section[data-page="settings"] .gx-tc').find((n) => txt(n.querySelector(".gx-tc__title")) === "Pro");
    return c ? c.querySelectorAll(".gx-tc__btns button").find((b) => txt(b) === label) || null : null;
  };
  const radio = (P) => P.$$(".gx-dlg .gx-radio__i").map((b) => `${b.getAttribute("aria-checked") === "true" ? "(x) " : "( ) "}${txt(b)}`);
  const pickLine = async (P, name) => P.click(P.$$(".gx-dlg .gx-radio__i").find((b) => txt(b.querySelector(".gx-radio__t")).startsWith(name)));
  // Окно с вводом имени (подхваченный профиль): заголовок, слова, поле, подвал.
  const typed = (P) => {
    const d = P.$$(".gx-dlg").pop();
    return d ? { title: txt(d.querySelector(".gx-dlg__title")), text: txt(d.querySelector(".gx-dlg__body")),
      foot: d.querySelectorAll(".gx-dlg__foot button").map((b) => `${txt(b)}${b.disabled ? " [off]" : ""}`) } : null;
  };
  const LBL = {
    ru: { line: "Линия на UDP 443", next: "Дальше", back: "Назад", go2: "Поставить AmneziaWG 2.0 на UDP 443", done: "Готово", retry: "Попробовать снова",
      adoptGo: "Перенести всё равно", cancel: "Отмена", sw20: "Перейти: 2.0 на 443", sw31: "3.1 на 443", change: "Сменить режим", hide: "Свернуть окно" },
    en: { line: "Line on UDP 443", next: "Next", back: "Back", go2: "Put AmneziaWG 2.0 on UDP 443", done: "Done", retry: "Try again",
      adoptGo: "Move anyway", cancel: "Cancel", sw20: "Switch: 2.0 on 443", sw31: "3.1 on 443", change: "Change the mode", hide: "Hide the window" },
  };
  // Режим Pro: на UDP 443 — 3.1; окно выбора → 2.0 → подтверждение → выполнение → итог. RU/EN, компьютер и мини-апп.
  for (const lang of ["ru", "en"]) {
    for (const dev of ["desk", "tg"]) {
      const hold = deferred();
      const T = LBL[lang];
      await scenario(`line_pro_${lang}_${dev}`, PL(lang, "pro", { ...STATUS(PROFS()),
        "POST /api/action/pro-line": () => hold.p.then(() => OK({ profiles: [{ id: "awg2", line: "2.0", port: 443 }, { id: "awg3", line: "3.1", port: 42817 }] })) },
      dev === "tg" ? { tg: true, width: 390 } : {}), async (P, rec) => {
        if (P.page() !== "settings") await P.go("#settings");
        rec.card = proCard(P);
        await P.click(proBtn(P, T.line));
        rec.pick = dlgState(P);
        rec.radio = radio(P);
        await pickLine(P, "AmneziaWG 3.1");
        rec.same = { radio: radio(P), foot: dlgState(P).foot };
        await pickLine(P, "AmneziaWG 2.0");
        await P.click(footBtn(P, T.next));
        rec.confirm = dlgState(P);
        await P.click(footBtn(P, T.go2));
        await P.tick(1500);
        rec.run = dlgState(P);
        rec.progress = { shown: !P.$("#progress").hidden, text: txt(P.$("#progress .gx-busy__t")), busy: P.doc.documentElement.classList.contains("is-busy") };
        await P.key("Escape");
        rec.escKept = !!P.$(".gx-dlg");
        hold.release();
        await P.settle();
        await P.tick(50);
        rec.calls = P.calls("POST", "/api/action/pro-line").map((c) => c.body);
        rec.result = dlgState(P);
        await P.click(footBtn(P, T.done));
        rec.closed = !P.$(".gx-dlg");
      });
    }
  }
  // Подхваченный у приложения профиль: ядро отказывает adopted_keyless → отдельное окно с вводом имени → тот же вызов
  // с adopted_confirm; отказ от ввода — ничего не трогаем и второго вызова нет.
  for (const [label, lang, agree] of [["line_adopted_ru", "ru", true], ["line_adopted_en", "en", true], ["line_adopted_no_ru", "ru", false]]) {
    const T = LBL[lang];
    await scenario(label, PL(lang, "pro", { ...STATUS(PROFS()), "POST /api/action/pro-line": (b) => (b.adopted_confirm === "awg2" ? OK({})
      : ERR(409, "adopted_keyless", lang === "ru" ? "У 2 человек на awg2 ключи из приложения Amnezia" : "2 people on awg2 hold keys from the Amnezia app", { profile: "awg2" })) }), async (P, rec) => {
      await P.click(proBtn(P, T.line));
      await P.click(footBtn(P, T.next));
      await P.click(footBtn(P, T.go2));
      rec.typed = typed(P);
      rec.dialogs = P.$$(".gx-dlg").length;
      if (agree) {
        await P.type(P.$$(".gx-dlg").pop().querySelector("input"), "39001");
        rec.typedReady = typed(P);
        await P.click(P.$$(".gx-dlg").pop().querySelectorAll(".gx-dlg__foot button").find((b) => txt(b) === T.adoptGo));
      } else {
        await P.click(P.$$(".gx-dlg").pop().querySelectorAll(".gx-dlg__foot button").find((b) => txt(b) === T.cancel));
      }
      rec.calls = P.calls("POST", "/api/action/pro-line").map((c) => c.body);
      rec.result = dlgState(P);
    });
  }
  // Отказ ядра (не Pro) — итог с причиной и ответом сервера; «Попробовать снова» — к выбору.
  await scenario("line_fail_ru", PL("ru", "pro", { ...STATUS(PROFS()), "POST /api/action/pro-line": () => ERR(409, "mode_not_pro", "Выбор линии — только в режиме Pro") }), async (P, rec) => {
    await P.click(proBtn(P, "Линия на UDP 443"));
    await P.click(footBtn(P, "Дальше"));
    await P.click(footBtn(P, "Поставить AmneziaWG 2.0 на UDP 443"));
    rec.result = dlgState(P);
    await P.click(footBtn(P, "Попробовать снова"));
    rec.back = { radio: radio(P), foot: dlgState(P).foot };
  });
  // Pro, а на UDP 443 никого (mode_port_mismatch): плашка сверху ведёт в тот же выбор, карточка — главной кнопкой.
  // Итог: порт сменился у одной 2.0 — её ключи раздать заново, 3.1 осталась на своём порту (LZ-08).
  for (const lang of ["ru", "en"]) {
    const T = LBL[lang];
    await scenario(`line_mismatch_${lang}`, PL(lang, "pro", { ...STATUS(PROFS({ awg3: { port: 51234 } }), { mode_port_mismatch: true }),
      "POST /api/action/pro-line": () => OK({ mode: "pro", udp443: { profile: "awg2", line: "2.0" },
        profiles: [{ id: "awg2", line: "2.0", port: 443 }, { id: "awg3", line: "3.1", port: 51234 }] }) }), async (P, rec) => {
      rec.top = P.$$('section[data-page="settings"] .gx-al--card').map((a) => ({ text: txt(a), btns: a.querySelectorAll(".gx-al__btns button").map(txt) }));
      rec.card = proCard(P);
      await P.click(P.$$('section[data-page="settings"] .gx-al--card .gx-al__btns button').find((b) => txt(b) === T.line));
      rec.pick = dlgState(P);
      rec.radio = radio(P);
      await P.click(footBtn(P, T.next));
      rec.confirm = dlgState(P);
      await P.click(footBtn(P, T.go2));
      rec.result = dlgState(P);
    });
  }
  // Из Semi-Pro в Pro с выбором линии, как в макете: «Перейти: 2.0 на 443» и «3.1 на 443».
  for (const lang of ["ru", "en"]) {
    const T = LBL[lang];
    await scenario(`line_switch_${lang}`, PL(lang, "lite_site", { ...STATUS(PROFS()),
      "POST /api/action/change-mode": () => OK({ mode: "pro", keys_host_before: "203.0.113.10", keys_host: "vpn.example.com" }),
      "POST /api/action/pro-line": () => OK({}) }), async (P, rec) => {
      rec.card = proCard(P);
      await P.click(proBtn(P, T.sw20));
      rec.what = dlgState(P);
      await P.click(footBtn(P, T.change));
      rec.modeCalls = P.calls("POST", "/api/action/change-mode").map((c) => c.body);
      rec.lineCalls = P.calls("POST", "/api/action/pro-line").map((c) => c.body);
      rec.result = dlgState(P);
    });
  }
  // На UDP 443 никого: порт сменится у одной 2.0, 3.1 остаётся на своём — её ключи работают как работали (LZ-08).
  for (const lang of ["ru", "en"]) {
    const T = LBL[lang];
    await scenario(`line_switch_one_${lang}`, PL(lang, "lite_site", { ...STATUS(PROFS({ awg3: { port: 51234 } })),
      "POST /api/action/change-mode": () => OK({ mode: "pro", keys_host: "vpn.example.com" }),
      "POST /api/action/pro-line": () => OK({ mode: "pro", udp443: { profile: "awg2", line: "2.0" },
        profiles: [{ id: "awg2", line: "2.0", port: 443 }, { id: "awg3", line: "3.1", port: 51234 }] }) }), async (P, rec) => {
      await P.click(proBtn(P, T.sw20));
      rec.what = dlgState(P);
      await P.click(footBtn(P, T.change));
      rec.lineCalls = P.calls("POST", "/api/action/pro-line").map((c) => c.body);
      rec.result = dlgState(P);
    });
  }
  // Окно смены режима свернули на выполнении — итог тостом, и в нём тоже сказано, чьи ключи раздать заново.
  {
    const hold = deferred();
    await scenario("line_switch_hidden_ru", PL("ru", "lite_site", { ...STATUS(PROFS()),
      "POST /api/action/change-mode": () => hold.p.then(() => OK({ mode: "pro", keys_host: "vpn.example.com" })),
      "POST /api/action/pro-line": () => OK({}) }), async (P, rec) => {
      await P.click(proBtn(P, "Перейти: 2.0 на 443"));
      await P.click(footBtn(P, "Сменить режим"));
      await P.click(footBtn(P, "Свернуть окно"));
      rec.hidden = !P.$(".gx-dlg");
      hold.release();
      await P.settle();
      await P.tick(50);
      rec.lineCalls = P.calls("POST", "/api/action/pro-line").map((c) => c.body);
      rec.toast = txt(P.$("#toastHost"));
    });
  }
  // Линия уже на UDP 443 — порт не меняется: выданные ключи работают как работали, так и сказано.
  await scenario("line_switch_31_ru", PL("ru", "lite_site", { ...STATUS(PROFS()), "POST /api/action/change-mode": () => OK({ mode: "pro", keys_host: "vpn.example.com" }),
    "POST /api/action/pro-line": () => OK({}) }), async (P, rec) => {
    await P.click(proBtn(P, "3.1 на 443"));
    rec.what = dlgState(P);
    await P.click(footBtn(P, "Сменить режим"));
    rec.lineCalls = P.calls("POST", "/api/action/pro-line").length;
    rec.result = dlgState(P);
  });
  await scenario("line_switch_fail_ru", PL("ru", "lite_site", { ...STATUS(PROFS()), "POST /api/action/change-mode": () => OK({ mode: "pro", keys_host: "vpn.example.com" }),
    "POST /api/action/pro-line": () => ERR(409, "port_busy", "UDP 443 занят чужим") }), async (P, rec) => {
    await P.click(proBtn(P, "Перейти: 2.0 на 443"));
    await P.click(footBtn(P, "Сменить режим"));
    rec.result = dlgState(P);
    await P.click(P.$$(".gx-dlg .gx-al__btns button").find((b) => txt(b) === "Линия на UDP 443"));
    rec.next = dlgState(P);
  });
  // Только 3.1 установлена — одна кнопка «Перейти: 3.1 на 443» и слова без «почему 2.0».
  await scenario("line_only31_ru", PL("ru", "lite", STATUS(PROFS().filter((p) => p.profile === "awg3").map((p) => ({ ...p, port: 51234 })))), async (P, rec) => {
    rec.card = proCard(P);
  });
  // Смена режима с линией, а 2.0 подхвачена у приложения и согласия нет: режим включён, линия не тронута — так и сказано.
  await scenario("line_switch_adopted_no_ru", PL("ru", "lite_site", { ...STATUS(PROFS()), "POST /api/action/change-mode": () => OK({ mode: "pro" }),
    "POST /api/action/pro-line": () => ERR(409, "adopted_keyless", "У 2 человек на awg2 ключи из приложения Amnezia", { profile: "awg2" }) }), async (P, rec) => {
    await P.click(proBtn(P, "Перейти: 2.0 на 443"));
    await P.click(footBtn(P, "Сменить режим"));
    rec.typed = typed(P);
    await P.click(P.$$(".gx-dlg").pop().querySelectorAll(".gx-dlg__foot button").find((b) => txt(b) === "Отмена"));
    rec.modeCalls = P.calls("POST", "/api/action/change-mode").length;
    rec.lineCalls = P.calls("POST", "/api/action/pro-line").map((c) => c.body);
    rec.result = dlgState(P);
  });
  // Окно линии свернули на выполнении — итог тостом; сервер занят другим действием — так и сказано, с его названием.
  {
    const hold = deferred();
    await scenario("line_hidden_ru", PL("ru", "pro", { ...STATUS(PROFS()), "POST /api/action/pro-line": () => hold.p.then(() => OK({})) }), async (P, rec) => {
      await P.click(proBtn(P, "Линия на UDP 443"));
      await P.click(footBtn(P, "Дальше"));
      await P.click(footBtn(P, "Поставить AmneziaWG 2.0 на UDP 443"));
      await P.click(footBtn(P, "Свернуть окно"));
      rec.hidden = !P.$(".gx-dlg");
      hold.release();
      await P.settle();
      await P.tick(50);
      rec.toast = txt(P.$("#toastHost"));
      rec.dlgAfter = !!P.$(".gx-dlg");
    });
  }
  await scenario("line_busy_en", PL("en", "pro", { ...STATUS(PROFS()),
    "POST /api/action/pro-line": () => ERR(409, "lock_timeout", "busy", { busy_action: "backup-create", busy_seconds: 12 }) }), async (P, rec) => {
    await P.click(proBtn(P, "Line on UDP 443"));
    await P.click(footBtn(P, "Next"));
    await P.click(footBtn(P, "Put AmneziaWG 2.0 on UDP 443"));
    rec.result = dlgState(P);
  });
  // DUP-LINE-01 / R4: подхваченная у приложения 3.1 в awg2 (UDP 46567) и своя 3.1 в awg3 (6714), 2.0 нет. Две 3.1 в выборе —
  // с местом и портом; «Рекомендуем 2.0» звал бы в несуществующее — вместо него честная строка и как получить 2.0.
  const DUPP = [{ profile: "awg3", line: "3.1", port: 6714, peers: 1, state: "running", container: "amnezia-awg3", line_shared: true },
    { profile: "awg2", line: "3.1", port: 46567, peers: 2, state: "running", container: "amnezia-awg2", line_shared: true, imported: true }];
  const DUP_LC = { no_20: true, shared: [{ line: "3.1", profiles: ["awg2", "awg3"] }], missing: ["2.0"],
    reline: { profile: "awg3", from: "3.1", line: "2.0", port: 6714, title: "AmneziaWG 3.1 · порт 6714" } };
  const dupPl = PL("ru", "pro", STATUS(DUPP));
  await scenario("line_dup_ru", { ...dupPl, data: { ...dupPl.data,
    "/api/overview": (d) => ({ ...d, mode: "pro", domain: "vpn.example.com", profiles: DUPP, lines_check: DUP_LC, free_lines: { awg1: "1.5" } }) } },
  async (P, rec) => {
    if (P.page() !== "settings") await P.go("#settings");
    await P.go("#overview");
    await P.tick(100);
    await P.go("#settings");
    await P.tick(100);
    await P.click(proBtn(P, "Линия на UDP 443"));
    rec.pick = dlgState(P);
    rec.radio = radio(P);
  });
  console.log(JSON.stringify(R));
})().catch((e) => { console.log(JSON.stringify({ __fatal: String((e && e.stack) || e) })); });
"""

# LZ-08: порт записан в каждом ключе — итог называет линии, чей порт сменился, и велит раздать их ключи заново.
MOVED2 = {
    "ru": "У AmneziaWG 2.0 и AmneziaWG 3.1 сменились порты, а порт записан в каждом ключе: выданные ключи этих линий больше не подключаются — "
          "раздайте пользователям ключи заново.",
    "en": "AmneziaWG 2.0 and AmneziaWG 3.1 have new ports, and the port is inside every key — issued keys of these lines no longer connect. "
          "Send users their keys again.",
}
MOVED1 = {
    "ru": "У AmneziaWG 2.0 сменился порт, а он записан в каждом ключе: выданные ключи этой линии больше не подключаются — раздайте пользователям ключи заново. "
          "Остальные выданные ключи работают как работали.",
    "en": "AmneziaWG 2.0 has a new port, and the port is inside every key — issued keys of this line no longer connect. Send users their keys again. "
          "Other issued keys work as before.",
}

NAMES = {
    "ru": {"lite": ("Lite", "только VPN"), "lite_site": ("Semi-Pro", "VPN и сайт"), "pro": ("Pro", "сайт и VPN на 443"),
           "lazy": ("Ленивый Pro", "только домен, остальное сделаю сам")},
    "en": {"lite": ("Lite", "VPN only"), "lite_site": ("Semi-Pro", "VPN and site"), "pro": ("Pro", "site and VPN on 443"),
           "lazy": ("Lazy Pro", "just a domain, I do the rest")},
}


def full(lang, mode):
    name, cap = NAMES[lang][mode]
    return "%s — %s" % (name, cap)


class ModeNamesStaticTests(unittest.TestCase):
    """Без node: словари RU и EN несут названия из таблицы договора, а прежних названий в словарях нет."""

    @classmethod
    def setUpClass(cls):
        cls.app = read_text(os.path.join(STATIC, "app.js"))

    def dict_body(self, lang):
        head = "    %s: {" % lang
        start = self.app.index(head) + len(head)
        return self.app[start:self.app.index("\n    },", start)]

    def test_names_come_from_the_contract_table(self):
        for lang in ("ru", "en"):
            body = self.dict_body(lang)
            for key, mode in (("modeLite", "lite"), ("modeLiteSite", "lite_site"), ("modePro", "pro"), ("modeLazy", "lazy")):
                self.assertIn('%s: "%s"' % (key, full(lang, mode)), body, lang)

    def test_old_names_are_gone_from_the_dictionaries(self):
        old = re.compile(r"«(Только VPN|VPN и сайт|Сайт и VPN на 443|VPN only|VPN and site|Site and VPN on 443)»")
        for lang in ("ru", "en"):
            self.assertEqual(old.findall(self.dict_body(lang)), [], lang)

    def test_one_function_names_the_mode_and_splits_it(self):
        self.assertIn('if (mode === "lazy") return t("modeLazy");', self.app)
        self.assertIn('const modeShort = (mode) => modeName(mode).split(" — ")[0];', self.app)
        self.assertIn('const modeCap = (mode) => modeName(mode).split(" — ").slice(1).join(" — ");', self.app)


@unittest.skipUnless(NODE, "нет node 18+ — панель в игрушечном DOM не проверить")
class ModesDomTests(unittest.TestCase):
    """F-1…F-3 в игрушечном DOM: панель и мини-апп, RU и EN, каждый режим и каждое состояние окна."""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="goamnezia-panel-modes-") as tmp:
            script = os.path.join(tmp, "modes.js")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(SCENARIOS_JS)
            proc = subprocess.run([NODE, script, HARNESS], capture_output=True, text=True, timeout=300, cwd=ROOT)
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
        cls.R = json.loads(lines[-1]) if lines else {"__fatal": "no JSON: rc=%s\n%s\n%s" % (proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])}

    def scen(self, name):
        self.assertNotIn("__fatal", self.R, self.R.get("__fatal"))
        rec = self.R.get(name)
        self.assertIsNotNone(rec, name)
        self.assertNotIn("fatal", rec, rec.get("fatal"))
        self.assertEqual(rec["errors"], [], name)
        return rec

    def each(self):
        for mode in ("lite", "lite_site", "pro"):
            for lang in ("ru", "en"):
                for dev in ("desk", "tg"):
                    yield mode, lang, dev, self.scen("names_%s_%s_%s" % (mode, lang, dev))

    def test_overview_names_the_mode_with_its_caption(self):
        for mode, lang, dev, rec in self.each():
            with self.subTest(mode=mode, lang=lang, dev=dev):
                name, cap = NAMES[lang][mode]
                self.assertEqual(rec["kpi"], {"mode": name, "cap": cap})

    def test_mode_cards_carry_name_and_caption(self):
        """Порядок договора: Ленивый Pro → Pro → Semi-Pro → Lite; у каждой карточки имя и подпись."""
        for mode, lang, dev, rec in self.each():
            with self.subTest(mode=mode, lang=lang, dev=dev):
                got = [(c["title"], c["sub"]) for c in rec["cards"]]
                self.assertEqual(got, [NAMES[lang][m] for m in ("lazy", "pro", "lite_site", "lite")])
                cur = [c for c in rec["cards"] if (c["chips"] or [""])[0] in ("сейчас", "current")]
                self.assertEqual([(c["title"], c["sub"]) for c in cur], [NAMES[lang][mode]])
                # B-14 (критик 16.10): сервер уже в Pro — у Ленивого Pro «уже сделано» и «Запустить снова»
                if mode == "pro":
                    self.assertEqual(rec["cards"][0]["btns"], ["Запустить снова" if lang == "ru" else "Run again"])
                    self.assertIn("уже сделано" if lang == "ru" else "already done", rec["cards"][0]["chips"] or [])
                else:
                    self.assertEqual(rec["cards"][0]["btns"], ["Запустить" if lang == "ru" else "Run"])

    def test_old_core_keeps_the_old_buttons(self):
        """Ядро без lazy-check, lazy-setup и pro-line: у Ленивого Pro нет кнопки — только слова, что он придёт с новым
        ядром; у Pro — прежние «Перейти» и слова про «Занять UDP/443», у текущего Pro — без кнопки."""
        for mode in ("lite", "lite_site", "pro"):
            with self.subTest(mode=mode):
                cards = self.scen("old_core_" + mode)["cards"]
                self.assertEqual(cards[0]["btns"], [])
                self.assertEqual(cards[0]["alert"], "Ленивый Pro появится с новой версией ядра.")
                pro = cards[1]
                if mode == "pro":
                    self.assertEqual((pro["btns"], pro["alert"]), ([], None))
                else:
                    self.assertEqual(pro["btns"], ["Перейти"])
                    self.assertTrue(pro["alert"].startswith("Порт VPN не переезжает сам."), pro["alert"])

    def test_now_and_about_use_the_full_name(self):
        for mode, lang, dev, rec in self.each():
            with self.subTest(mode=mode, lang=lang, dev=dev):
                quoted = ("«%s»" if lang == "ru" else "“%s”") % full(lang, mode)
                self.assertIn(quoted, rec["modeDesc"])
                self.assertEqual(rec["about"], full(lang, mode))

    def test_port_check_rows_use_the_short_name(self):
        for mode, lang, dev, rec in self.each():
            with self.subTest(mode=mode, lang=lang, dev=dev):
                word = {"ru": ("сейчас", "доступен"), "en": ("current", "available")}[lang]
                want = ["%s — %s" % (NAMES[lang][m][0], word[0] if m == mode else word[1]) for m in ("lite", "lite_site", "pro")]
                self.assertEqual(rec["stat"][:3], want)

    def test_no_old_names_on_any_screen(self):
        for mode, lang, dev, rec in self.each():
            with self.subTest(mode=mode, lang=lang, dev=dev):
                self.assertEqual({k: v for k, v in rec["old"].items() if v}, {})

    def test_dialogs_name_modes_in_full(self):
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                rec = self.scen("dialogs_" + lang)
                self.assertEqual({k: v for k, v in rec["old"].items() if v}, {})
                q = (lambda s: "«%s»" % s) if lang == "ru" else (lambda s: "“%s”" % s)
                self.assertIn(q(full(lang, "pro")), rec["dialogs"]["pro"]["title"])
                self.assertIn(q(full(lang, "lite_site")), rec["dialogs"]["pro"]["sub"])
                self.assertIn(q(full(lang, "lite")), rec["dialogs"]["lite"]["title"])
                self.assertTrue(rec["take443"])
                self.assertIn(q(full(lang, "lite_site")), rec["dialogs"]["take443"]["text"])
                self.assertIn(q(full(lang, "pro")), rec["dialogs"]["take443"]["text"])

    # ── F-1: Ленивый Pro ─────────────────────────────────────────────────────
    LZ_STEPS = {"ru": ["Домен", "План", "Подтверждение", "Выполнение", "Итог"],
                "en": ["Domain", "Plan", "Confirmation", "Running", "Result"]}

    def steps(self, lang, cur, done=None):
        done = cur - 1 if done is None else done
        return ["%s%s%s" % (s, "*" if i + 1 == cur else "", "+" if i + 1 <= done else "") for i, s in enumerate(self.LZ_STEPS[lang])]

    def flows(self):
        for lang in ("ru", "en"):
            for dev in ("desk", "tg"):
                yield lang, dev, self.scen("lazy_flow_%s_%s" % (lang, dev))

    def test_lazy_card_opens_the_scenario(self):
        for lang, dev, rec in self.flows():
            with self.subTest(lang=lang, dev=dev):
                self.assertEqual(rec["page"], "settings")
                lazy = rec["cards"][0]
                self.assertEqual((lazy["title"], lazy["sub"]), NAMES[lang]["lazy"])
                self.assertEqual(lazy["chips"], [{"ru": "сценарий", "en": "scenario"}[lang]])
                self.assertEqual(lazy["btns"], [{"ru": "Запустить", "en": "Run"}[lang]])
                self.assertIsNone(lazy["alert"])
                self.assertTrue(lazy["desc"].endswith({"ru": "Порты: TCP 80/443 · UDP 443.", "en": "Ports: TCP 80/443 · UDP 443."}[lang]), lazy["desc"])
                self.assertEqual([c["title"] for c in rec["cards"]], [NAMES[lang][m][0] for m in ("lazy", "pro", "lite_site", "lite")])

    def test_lazy_domain_step(self):
        for lang, dev, rec in self.flows():
            with self.subTest(lang=lang, dev=dev):
                s1 = rec["s1"]
                self.assertEqual(s1["title"], NAMES[lang]["lazy"][0])
                self.assertEqual(s1["sub"], {"ru": "Сейчас: «Semi-Pro — VPN и сайт»", "en": "Now: “Semi-Pro — VPN and site”"}[lang])
                self.assertEqual(s1["steps"], self.steps(lang, 1))
                self.assertEqual(s1["foot"], {"ru": ["Отмена", "Проверить [off]"], "en": ["Cancel", "Check [off]"]}[lang])
                self.assertTrue(s1["close"])
                self.assertEqual(s1["sheet"], dev == "tg", "на телефоне окно — лист снизу")
                # https://, путь, регистр и точка в конце — отрезаны; мосту уходит чистый домен.
                self.assertEqual(rec["s1typed"]["hint"], {"ru": "Проверим vpn.example.com", "en": "We will check vpn.example.com"}[lang])
                self.assertEqual(rec["s1typed"]["foot"][-1], {"ru": "Проверить", "en": "Check"}[lang])
                self.assertEqual(rec["checkBodies"], [{"domain": "vpn.example.com"}])

    def test_lazy_domain_is_cleaned_before_the_bridge(self):
        rec = self.scen("lazy_domain_ru")
        self.assertEqual(rec["empty"]["foot"], ["Отмена", "Проверить [off]"])
        self.assertTrue(rec["bad"]["hintBad"])
        self.assertEqual(rec["bad"]["hint"], "Домен вида vpn.example.ru: строчные латинские буквы, цифры, точки и дефисы.")
        self.assertEqual(rec["bad"]["foot"], ["Отмена", "Проверить [off]"])
        self.assertEqual(rec["badCalls"], 0, "мусор не доходит до моста")
        self.assertEqual(rec["idn"]["hint"], "Кириллический домен уйдёт в punycode: xn--e1afmkfd.xn--p1ai")
        self.assertEqual(rec["checkBodies"], [{"domain": "xn--e1afmkfd.xn--p1ai"}])
        # План показывает домен, как его назвало ядро: кириллицей и punycode; Cloudflare, AAAA, контейнеры приложения.
        self.assertEqual(rec["s2"]["stat"], [
            "warning: пример.рф (xn--e1afmkfd.xn--p1ai) → 104.21.3.4, 172.67.1.2 — адреса Cloudflare | Сайт откроется через Cloudflare, а ключи поведут на IP сервера.",
            "warning: AAAA-запись (IPv6) ведёт не сюда: 2606:4700::1 | Let’s Encrypt сначала пробует IPv6 — удалите запись или направьте её на IPv6 сервера.",
            "accent: Контейнеры приложения Amnezia: amnezia-awg (AmneziaWG 1.5, UDP 51820) | Подхватим без вопросов, их порты не тронем — у пользователей всё продолжит работать."])

    def test_lazy_plan_pro(self):
        for lang, dev, rec in self.flows():
            with self.subTest(lang=lang, dev=dev):
                s2 = rec["s2"]
                self.assertEqual(s2["steps"], self.steps(lang, 2))
                self.assertEqual(s2["al"], [{"ru": "success: План — «Pro — сайт и VPN на 443». Сайт встанет на TCP 443, AmneziaWG 2.0 — на UDP 443, 3.1 — на случайный порт.",
                                             "en": "success: Plan — “Pro — site and VPN on 443”. The site goes on TCP 443, AmneziaWG 2.0 on UDP 443, 3.1 on a random port."}[lang]])
                self.assertEqual(s2["stat"], [{"ru": "success: vpn.example.com → 203.0.113.10 — это этот сервер",
                                               "en": "success: vpn.example.com → 203.0.113.10 — this server"}[lang]])
                self.assertEqual(len(s2["table"]), 3)
                self.assertTrue(all(("свободен" if lang == "ru" else "free") in r for r in s2["table"]), s2["table"])
                self.assertEqual(s2["foot"], {"ru": ["Назад", "Дальше"], "en": ["Back", "Next"]}[lang])

    def test_lazy_plan_semi_pro_and_blocked(self):
        semi = self.scen("lazy_semi_ru")["s2"]
        self.assertEqual(semi["al"], ["warning: План — «Semi-Pro — VPN и сайт»: UDP 443 занят. Сайт встанет на TCP 443, профили — на случайные порты; "
                                      "чужое не останавливаем. UDP 443 держит: amnezia-xray."])
        self.assertIn("UDP 443занятamnezia-xray", semi["table"])
        self.assertNotIn("warning: UDP 443 занят — профили встанут на случайные порты.", semi["stat"], "причина уже сказана плашкой плана")
        self.assertEqual(semi["foot"], ["Назад", "Дальше"])
        rec = self.scen("lazy_blocked_ru")
        b = rec["s2"]
        self.assertEqual(b["al"], ["danger: Ленивый Pro здесь не пройдёт. Чужое мы не останавливаем. Поправьте то, что ниже, и проверьте снова — или выберите обычный режим."])
        self.assertEqual(b["stat"], [
            "danger: vpn.example.com → 198.51.100.50 — это не этот сервер | Направьте A-запись на 203.0.113.10 у регистратора домена и проверьте снова.",
            "danger: TCP 443 занят чужой программой — сайту встать некуда.",
            "danger: Ещё причина от сервера: weird_code"])
        self.assertIn("TCP 443занятnginx · goTelegram Pro", b["table"])
        self.assertTrue(rec["noNext"], "дальше blocked не пускает")
        self.assertEqual(b["foot"], ["Назад", "Проверить снова"])
        self.assertEqual(rec["calls"], 2)
        self.assertTrue(rec["s2b"]["al"][0].startswith("success: План — «Pro — сайт и VPN на 443»."))
        self.assertEqual(rec["s1back"]["steps"], self.steps("ru", 1))
        self.assertEqual(rec["s1back"]["input"], "vpn.example.com")
        en = self.scen("lazy_blocked_en")["s2"]
        self.assertEqual(en["al"], ["danger: Lazy Pro cannot run here. We never stop what is not ours. Fix what is listed below and check again — or pick a regular mode."])
        self.assertIn("danger: Another reason from the server — weird_code", en["stat"])

    def test_lazy_confirmation(self):
        for lang, dev, rec in self.flows():
            with self.subTest(lang=lang, dev=dev):
                s3 = rec["s3"]
                self.assertEqual(s3["steps"], self.steps(lang, 3))
                self.assertEqual(s3["p"], [{"ru": "Что будет на vpn.example.com:", "en": "What happens on vpn.example.com:"}[lang]])
                marks = [x.split(":", 1)[0] for x in s3["stat"]]
                self.assertEqual(marks, ["accent", "accent", "warning", "accent", "accent"])
                self.assertIn({"ru": "AmneziaWG 2.0 — на UDP 443, 3.1 — на случайном порту | Почему 2.0: её понимает больше всего клиентов, включая роутеры Keenetic.",
                               "en": "AmneziaWG 2.0 on UDP 443, 3.1 on a random port | Why 2.0 — most clients understand it, including Keenetic routers."}[lang], s3["stat"][2])
                self.assertEqual(s3["input"], "5")
                self.assertEqual(s3["foot"], {"ru": ["Назад", "Запустить Ленивый Pro"], "en": ["Back", "Run Lazy Pro"]}[lang])
        semi = self.scen("lazy_semi_ru")["s3"]
        self.assertIn("warning: AmneziaWG 2.0 и 3.1 — на случайных портах | UDP 443 занят, поэтому режим — «Semi-Pro — VPN и сайт».", semi["stat"])
        self.assertIn("accent: Контейнеры приложения подхватим, их порты не тронем | Недостающую линию поставим новым профилем.", semi["stat"])
        err = self.scen("lazy_errors_ru")
        for v in ("0", "21", "x"):
            with self.subTest(keys=v):
                self.assertTrue(err["keys_" + v]["hintBad"])
                self.assertEqual(err["keys_" + v]["hint"], "Нужно целое число от 1 до 20.")
                self.assertEqual(err["keys_" + v]["foot"], ["Назад", "Запустить Ленивый Pro [off]"])

    def test_lazy_setup_is_a_long_action(self):
        """Выполнение — как у прочих долгих: плашка «Выполняется: Ленивый Pro» над разделом, кнопки раздела ждут, окно не
        закрыть ни крестиком, ни Esc — только свернуть. Мосту — домен, число ключей и confirm = домен (разрушительное)."""
        for lang, dev, rec in self.flows():
            with self.subTest(lang=lang, dev=dev):
                s4 = rec["s4"]
                self.assertEqual(s4["steps"], self.steps(lang, 4))
                self.assertEqual(s4["busy"], {"ru": "Ставим Ленивый Pro на vpn.example.com…", "en": "Setting up Lazy Pro on vpn.example.com…"}[lang])
                self.assertTrue(s4["clock"].startswith({"ru": "идёт ", "en": "running "}[lang]), s4["clock"])
                self.assertEqual(s4["foot"], [{"ru": "Свернуть окно", "en": "Hide the window"}[lang]])
                self.assertFalse(s4["close"])
                self.assertEqual(rec["progress"], {"shown": True, "busy": True,
                                                   "text": {"ru": "Выполняется: Ленивый Pro", "en": "Running: Lazy Pro"}[lang]})
                self.assertTrue(rec["escKept"])
                self.assertEqual(rec["setupBodies"], [{"domain": "vpn.example.com", "keys": "7", "confirm": "vpn.example.com"}])
                self.assertEqual(rec["after"], {"shown": False, "busy": False})
                self.assertGreaterEqual(rec["reloaded"], 1, "после сценария «Настройки» перечитываются")
                self.assertTrue(rec["closed"])

    def test_lazy_result(self):
        for lang, dev, rec in self.flows():
            with self.subTest(lang=lang, dev=dev):
                s5 = rec["s5"]
                self.assertEqual(s5["steps"], self.steps(lang, 6, 5))
                self.assertEqual(s5["empty"]["t"], {"ru": "Готово: «Pro — сайт и VPN на 443» на vpn.example.com",
                                                    "en": "Done — “Pro — site and VPN on 443” on vpn.example.com"}[lang])
                self.assertEqual(s5["empty"]["tone"], "success")
                ru = lang == "ru"
                self.assertEqual(s5["kv"], [
                    ("Режим = " if ru else "Mode = ") + full(lang, "pro"),
                    ("Домен = " if ru else "Domain = ") + "vpn.example.com",
                    # Профили — в порядке продукта: 3.1 → 2.0 → 1.x.
                    "UDP 443 = AmneziaWG 2.0", "AmneziaWG 3.1 = UDP 51234", "AmneziaWG 2.0 = UDP 443",
                    ("Шаблон сайта = " if ru else "Site template = ") + "h5up_hyperspace",
                    "Панель на домене = включена" if ru else "Panel on the domain = on"])
                self.assertEqual(s5["kvBtns"], ["Вход в панель" if ru else "Panel sign-in"])
                self.assertTrue(s5["al"][0].startswith("warning: На UDP 443 — 2.0." if ru else "warning: UDP 443 gets 2.0."), s5["al"])
                self.assertEqual(s5["sub2"], ["Ключи и страницы подключения: 5" if ru else "Keys and connection pages — 5"])
                self.assertEqual([k["title"] for k in s5["list"]], ["Туманный Лондон", "Солнечная Манила", "Снежная Оттава", "Туманный Лиссабон", "Ветреный Веллингтон"])
                self.assertEqual(s5["list"][0]["meta"], "foggy_london")
                self.assertEqual(s5["list"][0]["btns"], ["Скопировать ссылку", "Открыть"] if ru else ["Copy the link", "Open"])
                self.assertTrue(all(k["href"].startswith("https://vpn.example.com/p-") for k in s5["list"]))
                self.assertTrue(s5["al"][-1].startswith("warning: Кто знает ссылку" if ru else "warning: Whoever knows the link"), s5["al"])
                self.assertEqual(s5["foot"], ["Готово" if ru else "Done"])

    def test_lazy_result_semi_pro_with_warnings(self):
        s5 = self.scen("lazy_semi_ru")["s5"]
        self.assertEqual(s5["empty"]["t"], "Готово: «Semi-Pro — VPN и сайт» на vpn.example.com")
        self.assertIn("UDP 443 = никто — профили на своих портах", s5["kv"])
        self.assertIn("Шаблон сайта = встроенная заглушка", s5["kv"])
        self.assertIn("Панель на домене = https://vpn.example.com/app-AbCdEf123456/", s5["kv"])
        self.assertEqual(s5["kvBtns"], ["Скопировать"])
        self.assertEqual(s5["al"][:3], [
            "warning: UDP 443 был занят — режим «Semi-Pro — VPN и сайт», профили на случайных портах.",
            "warning: Шаблон сайта не скачался за три попытки — стоит встроенная заглушка. Сменить: «Режим и сайт → Сайт-прикрытие».",
            "warning: Предупреждение сервера: mystery_code"])
        self.assertFalse(any("На UDP 443 — 2.0." in a for a in s5["al"]), "2.0 на 443 нет — и объяснять нечего")
        self.assertEqual(s5["list"][-1], {"title": "Тихий Рим", "meta": "calm_rome · страницы нет", "btns": [], "href": None})

    def test_lazy_refusals(self):
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                rec = self.scen("lazy_errors_" + lang)
                ru = lang == "ru"
                self.assertEqual(rec["checkErr"]["steps"], self.steps(lang, 1))
                self.assertEqual(rec["checkErr"]["al"], ["danger: %s параметр domain: недопустимое значение" % ("Проверить не вышло." if ru else "The check failed.")])
                self.assertEqual(rec["busy"]["empty"], {
                    "t": "Сервер занят другим действием" if ru else "The server is busy with another action",
                    "x": "Сейчас выполняется другое действие (установка профиля) — повторите через минуту" if ru
                    else "Another action is running right now (installing a profile) — retry in a minute", "tone": "warning"})
                self.assertEqual(rec["busy"]["foot"], ["Закрыть", "Попробовать снова"] if ru else ["Close", "Try again"])
                self.assertEqual(rec["retry"]["steps"], self.steps(lang, 1))
                self.assertEqual(rec["retry"]["input"], "vpn.example.com")
                self.assertEqual(rec["fail"]["empty"]["t"], "Ленивый Pro не прошёл" if ru else "Lazy Pro did not complete")
                self.assertEqual(rec["fail"]["empty"]["tone"], "danger")
                self.assertEqual(rec["fail"]["empty"]["x"], "Let’s Encrypt отказал: домен ведёт не сюда. " + (
                    "Что успело встать, останется; повторный запуск ничего не ломает." if ru else "Whatever got installed stays; running it again breaks nothing."))
                self.assertTrue(rec["fail"]["details"])

    # ── F-1 по итогам ядра: no_line_for_443, dns_unknown, awg2_container_busy, udp443_line, by, отказ, редкие предупреждения ──
    def test_ports_held_by_us_and_keys_that_count(self):
        """LZ-05: наши порты (ours) — «занят нами» с тем, чем именно, а не «свободен»; подтверждение говорит, сколько ключей
        будет новых на самом деле: прежние ключи Ленивого Pro засчитываются."""
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                rec = self.scen("lazy_ours_" + lang)
                ru = lang == "ru"
                rows = rec["s2"]["table"]
                self.assertEqual(len(rows), 3, rows)
                for row, sub in zip(rows, (("goAmnezia: сайт и продление сертификата", "goAmnezia: сайт, панель, агенты", "goAmnezia: AmneziaWG на UDP 443") if ru
                                           else ("goAmnezia: site and certificate renewal", "goAmnezia: site, panel, agents", "goAmnezia: AmneziaWG on UDP 443"))):
                    self.assertIn("занят нами" if ru else "ours", row)
                    self.assertIn(sub, row)
                    self.assertNotIn("свободен" if ru else "free", row)
                self.assertEqual(rec["s3"]["hint"], ("Новых ключей: 2. Ключи прошлого запуска (3) остаются и засчитываются. От 1 до 20; по умолчанию 5." if ru
                                                     else "New keys: 2. Keys from the previous run (3) stay and count. From 1 to 20; 5 by default."))
                self.assertEqual(rec["hint2"], ("Новых ключей: 0. Ключи прошлого запуска (3) остаются и засчитываются. От 1 до 20; по умолчанию 5." if ru
                                                else "New keys: 0. Keys from the previous run (3) stay and count. From 1 to 20; 5 by default."))
        # Ядро без keys_have (чистый сервер) — «новых» столько, сколько просят
        self.assertTrue(self.scen("lazy_line31_ru")["s3"]["hint"].startswith("Новых ключей: 5."), self.scen("lazy_line31_ru")["s3"]["hint"])

    def test_no_line_for_443_explains_semi_pro_without_a_busy_port(self):
        """«Lite → Ленивый Pro»: обе линии уже на своих портах — итог Semi-Pro, и не «UDP 443 занят» / «на случайные
        порты»: порты с людьми не двигаем; линию на 443 можно потом, но её ключи придётся обновить."""
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                rec = self.scen("lazy_noline_" + lang)
                ru = lang == "ru"
                head, = rec["s2"]["al"]
                self.assertTrue(head.startswith("warning: План — «Semi-Pro — VPN и сайт»: обе линии уже стоят на своих портах." if ru
                                                else "warning: Plan — “Semi-Pro — VPN and site”: both lines already sit on ports of their own."), head)
                self.assertIn("«Режим и сайт → Линия на UDP 443», но ключи этой линии пользователям придётся обновить." if ru
                              else "Mode and site → Line on UDP 443, but users on that line will need their keys updated.", head)
                for text in [head] + rec["s2"]["stat"] + rec["s3"]["stat"]:
                    for word in (("UDP 443 занят", "на случайные порты", "на случайных портах") if ru else ("UDP 443 is taken", "random port")):
                        self.assertNotIn(word, text)
                self.assertEqual(len(rec["s2"]["stat"]), 1, "причина сказана плашкой плана — второй строкой не повторяется")
                self.assertIn("accent: AmneziaWG 2.0 и 3.1 остаются на своих портах | Порты, к которым подключены пользователи, не двигаем — поэтому режим «Semi-Pro — VPN и сайт»."
                              if ru else "accent: AmneziaWG 2.0 and 3.1 stay on their own ports | Ports that users use are not moved — hence the “Semi-Pro — VPN and site” mode.",
                              " ".join(rec["s3"]["stat"]))
                self.assertEqual(rec["s5"]["empty"]["t"], "Готово: «Semi-Pro — VPN и сайт» на vpn.example.com" if ru else "Done — “Semi-Pro — VPN and site” on vpn.example.com")
                self.assertTrue(rec["s5"]["al"][0].startswith("warning: Обе линии AmneziaWG уже стоят на своих портах" if ru
                                                              else "warning: Both AmneziaWG lines already sit on ports of their own"), rec["s5"]["al"])

    def test_line_on_443_comes_from_the_core_only(self):
        s31 = self.scen("lazy_line31_ru")
        self.assertEqual(s31["s2"]["al"], ["success: План — «Pro — сайт и VPN на 443». Сайт встанет на TCP 443, AmneziaWG 3.1 — на UDP 443; 2.0 остаётся на своём порту."])
        self.assertIn("warning: AmneziaWG 3.1 — на UDP 443, 2.0 остаётся на своём порту | 2.0 уже стоит — её порт с пользователями не трогаем. Поменять — «Режим и сайт → Линия на UDP 443».",
                      s31["s3"]["stat"])
        for label in ("lazy_lineless_ru", "lazy_linenull_ru"):
            with self.subTest(label=label):
                rec = self.scen(label)
                self.assertEqual(rec["s2"]["al"], ["success: План — «Pro — сайт и VPN на 443». Сайт встанет на TCP 443, AmneziaWG — на UDP 443."])
                texts = " ".join(rec["s2"]["al"] + rec["s3"]["stat"])
                self.assertNotIn("2.0", texts, "линию не называем, если ядро её не назвало")
                self.assertNotIn("3.1", texts)
                self.assertIn("accent: AmneziaWG — на UDP 443 | Профили, которые уже стоят, остаются на своих портах", texts)

    def test_new_blocking_reasons(self):
        unk = self.scen("lazy_dnsunk_ru")["s2"]
        self.assertEqual(unk["stat"], ["danger: DNS не ответил про vpn.example.com | Проверить, указывает ли домен сюда, не вышло. Повторите проверку "
                                       "через минуту; если повторяется — проверьте DNS-серверы самого сервера."])
        part = self.scen("lazy_dnspart_ru")["s2"]
        self.assertEqual(part["stat"], ["danger: vpn.example.com → 203.0.113.10, 198.51.100.50 — часть записей ведёт не сюда | Оставьте в DNS только "
                                        "адреса этого сервера (203.0.113.10) и проверьте снова."])
        busy = self.scen("lazy_awg2busy_ru")["s2"]
        self.assertIn("danger: Место awg2 занимает контейнер приложения Amnezia amnezia-awg2 с пользователями, и подхватить его не вышло — ставить поверх значило бы "
                      "удалить их ключи. Подхватите его в «Протоколы → Импорт установки Amnezia».", busy["stat"])
        self.assertIn("accent: Контейнеры приложения Amnezia: amnezia-awg2 (AmneziaWG 2.0, UDP 51820) | Что подхватывается — подхватим без вопросов; порты, "
                      "к которым подключены пользователи, не тронем.", busy["stat"])
        self.assertEqual(busy["foot"], ["Назад", "Проверить снова"])
        en = self.scen("lazy_awg2busy_en")["s2"]
        self.assertIn("danger: The awg2 slot is taken by the Amnezia app container amnezia-awg2 with users, and adopting it failed — installing over it "
                      "would delete their keys. Adopt it under Protocols → Import an Amnezia installation.", en["stat"])

    def test_long_holder_text_wraps_instead_of_breaking_the_layout(self):
        """by — полная формулировка предполётной проверки: строка целиком, в ячейке, которая переносит слова (компьютер —
        третья колонка minmax(0,1fr), телефон — значение строки списка), а не режет и не раздвигает окно."""
        desk, phone = self.scen("lazy_longby_desk"), self.scen("lazy_longby_tg")
        self.assertEqual(desk["cells"], ["gx-tbl__c > gx-tbl__t"] * 3)
        self.assertEqual(desk["table"], "--cols: 90px 150px minmax(0,1fr);")
        self.assertEqual(phone["cells"], ["gx-ptbl__v > gx-tbl__t"] * 3)
        self.assertTrue(phone["s2"]["sheet"])
        css = read_text(os.path.join(STATIC, "styles.css"))
        rule = lambda sel: re.search(r"^%s \{([^}]*)\}" % re.escape(sel), css, re.M).group(1)
        self.assertIn("overflow-wrap: anywhere", rule(".gx-tbl__t"))
        self.assertIn("min-width: 0", rule(".gx-tbl__c"))
        self.assertIn("min-width: 0", rule(".gx-ptbl__v"))
        self.assertIn("overflow-wrap: anywhere", rule(".gx-al__text"))

    def test_rare_warnings_have_words(self):
        ru = self.scen("lazy_rarewarn_ru")["s5"]["al"]
        for text in ("Контейнер приложения Amnezia подхватить не вышло — он остаётся как есть.",
                     "Открыть порт сайта в локальном файрволе не вышло — откройте его вручную",
                     "Одну линию AmneziaWG не поставили: её место занимает контейнер приложения Amnezia с пользователями.",
                     "Одна из линий AmneziaWG не встала — остальное поставлено; поставьте её позже в «Протоколах».",
                     "Сайт не поднялся — сервер работает как «Lite — только VPN». Повторите позже: «Режим и сайт».",
                     "Таймеры продукта (лимиты, сторож, бэкап) не включились — проверьте их в «Логи → Диагностика».",
                     # Обойдённые причины проверки — словами той же причины.
                     "AAAA-запись (IPv6) домена ведёт не сюда — удалите её или направьте на IPv6 сервера.",
                     "Домен за прокси Cloudflare — ключи ведут на IP сервера.",
                     "Предупреждение сервера: brand_new_code"):
            with self.subTest(text=text):
                self.assertTrue(any(text in a for a in ru), text)
        en = self.scen("lazy_rarewarn_en")["s5"]["al"]
        for text in ("An Amnezia app container could not be adopted", "could not be opened in the local firewall", "One AmneziaWG line was not installed",
                     "One AmneziaWG line did not come up", "the server runs as “Lite — VPN only”", "The product timers (limits, watchdog, backup) did not start",
                     "Both AmneziaWG lines already sit on ports of their own"):
            with self.subTest(text=text):
                self.assertTrue(any(text in a for a in en), text)
        self.assertFalse(any(re.search(r"[А-Яа-яЁё]", a.replace("Туманный", "")) for a in en[:-1]), en)

    def test_setup_refusal_shows_plan_and_reasons(self):
        """Отказ lazy-setup до изменений несёт plan и reasons: как на шаге «План» — плашка плана и причины списком (DNS и
        таблицы портов в отказе нет — строки dns_* идут причинами); слова ядра и честное «ничего не менялось»."""
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                rec = self.scen("lazy_refused_" + lang)
                ru = lang == "ru"
                s5 = rec["s5"]
                self.assertEqual(s5["empty"]["t"], "Ленивый Pro не начался" if ru else "Lazy Pro did not start")
                self.assertEqual(s5["empty"]["tone"], "danger")
                self.assertTrue(s5["empty"]["x"].endswith("Проверка перед установкой не прошла — на сервере ничего не менялось." if ru
                                                          else "The pre-install check failed — nothing on the server was changed."), s5["empty"]["x"])
                self.assertIn("goTelegram Pro", s5["empty"]["x"])
                self.assertEqual(s5["al"], ["danger: Ленивый Pro здесь не пройдёт. Чужое мы не останавливаем. Поправьте то, что ниже, и проверьте снова — или выберите обычный режим."
                                            if ru else "danger: Lazy Pro cannot run here. We never stop what is not ours. Fix what is listed below and check again — or pick a regular mode."])
                self.assertEqual(s5["stat"], ["danger: TCP 443 занят чужой программой — сайту встать некуда.", "danger: Домен смотрит не на этот сервер.",
                                              "danger: Ещё причина от сервера: odd_reason"] if ru else
                                 ["danger: TCP 443 is taken by another program — the site has nowhere to go.", "danger: The domain does not point to this server.",
                                  "danger: Another reason from the server — odd_reason"])
                self.assertEqual(s5["table"], [])
                self.assertFalse(s5["details"])
                self.assertEqual(s5["foot"], ["Закрыть", "Попробовать снова"] if ru else ["Close", "Try again"])
                self.assertEqual((rec["retry"]["steps"], rec["retry"]["input"]), (self.steps(lang, 1), "vpn.example.com"))

    # ── F-3: линия на UDP 443 ────────────────────────────────────────────────
    def test_pro_card_offers_the_line_choice(self):
        for lang, dev in (("ru", "desk"), ("en", "desk"), ("ru", "tg"), ("en", "tg")):
            with self.subTest(lang=lang, dev=dev):
                card = self.scen("line_pro_%s_%s" % (lang, dev))["card"]
                self.assertEqual(card["chips"], ["сейчас" if lang == "ru" else "current"])
                self.assertEqual(card["alert"], "На UDP 443 — AmneziaWG 3.1." if lang == "ru" else "UDP 443 has AmneziaWG 3.1.")
                self.assertEqual(card["btns"], ["Линия на UDP 443" if lang == "ru" else "Line on UDP 443"])
                self.assertTrue(card["desc"].startswith("Сайт на TCP 443 и AmneziaWG на UDP 443 — снаружи это обычный сайт. Какая линия стоит на 443, выбираете вы."
                                                        if lang == "ru" else "Site on TCP 443 and AmneziaWG on UDP 443 — from outside it is an ordinary site. You choose which line sits on 443."))
        # Каталог ядра 0.6.0 (фикстура берёт настоящий lib/actions.json): и в прогоне по разделам Pro предлагает линию.
        self.assertEqual(self.scen("names_pro_ru_desk")["cards"][1]["btns"], ["Линия на UDP 443"])
        self.assertEqual(self.scen("names_lite_site_ru_desk")["cards"][1]["btns"], ["Перейти: 2.0 на 443", "3.1 на 443"])

    def test_line_dialog_pick_confirm_run_result(self):
        for lang, dev in (("ru", "desk"), ("en", "desk"), ("ru", "tg"), ("en", "tg")):
            with self.subTest(lang=lang, dev=dev):
                rec = self.scen("line_pro_%s_%s" % (lang, dev))
                ru = lang == "ru"
                pick = rec["pick"]
                self.assertEqual(pick["title"], "Pro · линия на UDP 443" if ru else "Pro · line on UDP 443")
                self.assertEqual(pick["sub"], "Сейчас на UDP 443 — AmneziaWG 3.1" if ru else "Now on UDP 443 — AmneziaWG 3.1")
                self.assertEqual(pick["sheet"], dev == "tg")
                # 2.0 первой и выбрана (рекомендуем), у 3.1 — «на 443».
                self.assertEqual(rec["radio"], ["(x) AmneziaWG 2.0рекомендуемсейчас UDP 39001", "( ) AmneziaWG 3.1на 443сейчас UDP 443"] if ru
                                 else ["(x) AmneziaWG 2.0recommendednow UDP 39001", "( ) AmneziaWG 3.1on 443now UDP 443"])
                self.assertEqual(pick["al"], ["warning: Рекомендуем 2.0: её понимает больше всего клиентов, включая роутеры Keenetic." if ru
                                              else "warning: We recommend 2.0 — most clients understand it, including Keenetic routers."])
                self.assertEqual(pick["foot"], ["Отмена", "Дальше"] if ru else ["Cancel", "Next"])
                # Та же линия, что уже на 443, — дальше не пускает.
                self.assertEqual(rec["same"]["foot"], ["Отмена", "Дальше [off]"] if ru else ["Cancel", "Next [off]"])
                conf = rec["confirm"]["stat"]
                self.assertEqual(len(conf), 3)
                self.assertEqual(conf[0], "warning: AmneziaWG 2.0 встанет на UDP 443 | Порт записан в каждом ключе: пользователям на этой линии (1 пользователь) придётся забрать ключи заново."
                                 if ru else "warning: AmneziaWG 2.0 goes on UDP 443 | The port is inside every key — users on this line (1 user) will need their keys again.")
                self.assertTrue(conf[1].startswith("warning: AmneziaWG 3.1 переедет на свободный случайный порт" if ru else "warning: AmneziaWG 3.1 moves to a free random port"), conf[1])
                self.assertIn("(2 пользователя)" if ru else "(2 users)", conf[1])
                self.assertEqual(rec["confirm"]["foot"], ["Назад", "Поставить AmneziaWG 2.0 на UDP 443"] if ru else ["Back", "Put AmneziaWG 2.0 on UDP 443"])
                run = rec["run"]
                self.assertEqual(run["busy"], "Ставим AmneziaWG 2.0 на UDP 443…" if ru else "Putting AmneziaWG 2.0 on UDP 443…")
                self.assertFalse(run["close"])
                self.assertEqual(run["foot"], ["Свернуть окно" if ru else "Hide the window"])
                self.assertEqual(rec["progress"], {"shown": True, "busy": True,
                                                   "text": "Выполняется: смена линии на UDP 443" if ru else "Running: changing the line on UDP 443"})
                self.assertTrue(rec["escKept"])
                self.assertEqual(rec["calls"], [{"profile": "awg2", "confirm": "awg2"}])
                res = rec["result"]
                self.assertEqual(res["empty"]["t"], "AmneziaWG 2.0 — на UDP 443" if ru else "AmneziaWG 2.0 is on UDP 443")
                # LZ-08: порт сменился у обеих линий — обе названы, других линий нет, «остальные работают» не к чему.
                self.assertEqual(res["empty"]["x"], MOVED2[lang])
                self.assertEqual(res["sub"], "Сейчас на UDP 443 — AmneziaWG 2.0" if ru else "Now on UDP 443 — AmneziaWG 2.0")
                self.assertEqual(res["kv"], ["AmneziaWG 3.1 = UDP 42817", "AmneziaWG 2.0 = UDP 443"])
                self.assertEqual(res["foot"], ["Готово" if ru else "Done"])
                self.assertTrue(rec["closed"])

    def test_adopted_profile_needs_its_own_confirmation(self):
        """ACC-2: подхваченный у приложения профиль — только с явным согласием: ядро отказывает adopted_keyless, панель
        показывает отдельное окно с вводом имени профиля и повторяет тот же вызов с adopted_confirm."""
        for label, lang in (("line_adopted_ru", "ru"), ("line_adopted_en", "en")):
            with self.subTest(lang=lang):
                rec = self.scen(label)
                ru = lang == "ru"
                self.assertEqual(rec["dialogs"], 2, "окно согласия — поверх окна линии")
                self.assertEqual(rec["typed"]["title"], "Пользователи на AmneziaWG 2.0 останутся без связи" if ru else "Users on AmneziaWG 2.0 will lose the connection")
                self.assertIn("У 2 человек на awg2 ключи из приложения Amnezia" if ru else "2 people on awg2 hold keys from the Amnezia app", rec["typed"]["text"])
                self.assertEqual(rec["typed"]["foot"], ["Отмена", "Перенести всё равно [off]"] if ru else ["Cancel", "Move anyway [off]"])
                # D-5/R-3: набирают порт профиля (39001), а не имя места awg2
                self.assertIn("наберите порт профиля" if ru else "type the profile's port", rec["typed"]["text"])
                self.assertEqual(rec["typedReady"]["foot"][-1], "Перенести всё равно" if ru else "Move anyway")
                self.assertEqual(rec["calls"], [{"profile": "awg2", "confirm": "awg2"},
                                                {"profile": "awg2", "adopted_confirm": "awg2", "confirm": "awg2"}])
                self.assertEqual(rec["result"]["empty"]["tone"], "success")
        no = self.scen("line_adopted_no_ru")
        self.assertEqual(no["calls"], [{"profile": "awg2", "confirm": "awg2"}], "без согласия второго вызова нет")
        self.assertEqual(no["result"]["empty"], {"t": "Линию не переставили", "x": "Перенос профиля, подхваченного у приложения, не подтверждён — ничего не тронуто.",
                                                 "tone": "default"})
        self.assertEqual(no["result"]["foot"], ["Закрыть", "Попробовать снова"])

    def test_line_dialog_without_20_names_both_31_and_says_there_is_no_20(self):
        """DUP-LINE-01 / R4: две 3.1 различимы — короткая подпись (порт — строкой рядом), у подхваченной «из приложения»; совета «Рекомендуем 2.0» на сервере без 2.0 нет — честная
        строка, что её нет, и как получить («Переставить на 2.0» своего awg3)."""
        rec = self.scen("line_dup_ru")
        self.assertEqual(rec["radio"], ["(x) AmneziaWG 3.1 · из приложениясейчас UDP 46567", "( ) AmneziaWG 3.1сейчас UDP 6714"])
        self.assertEqual(rec["pick"]["al"], ["warning: На сервере нет AmneziaWG 2.0. Старые версии приложений и роутеры Keenetic на обычной прошивке "
                                             "не подключатся ни к одному профилю (3.1 Keenetic понимает пока только в dev-прошивке). Получить её — «Протоколы» → AmneziaWG 3.1 · порт 6714 → «Переставить на 2.0»."])
        self.assertNotIn("Рекомендуем", " ".join(rec["pick"]["al"]))

    def test_line_refusal_and_retry(self):
        rec = self.scen("line_fail_ru")
        self.assertEqual(rec["result"]["empty"], {"t": "Линию не переставили", "x": "Выбор линии — только в режиме Pro.", "tone": "danger"})
        self.assertTrue(rec["result"]["details"])
        self.assertEqual(rec["back"]["foot"], ["Отмена", "Дальше"])

    def test_pro_without_a_profile_on_443(self):
        rec = self.scen("line_mismatch_ru")
        top, = rec["top"]
        self.assertTrue(top["text"].startswith("Режим «Pro — сайт и VPN на 443», а на UDP/443 нет ни одного профиля. Снаружи это обычный режим с сайтом. "
                                               "Выберите линию на UDP 443"), top["text"])
        self.assertEqual(top["btns"], ["Линия на UDP 443"])
        self.assertEqual(rec["card"]["alert"], "На UDP 443 нет ни одного профиля. Выберите, какая линия там встанет.")
        self.assertEqual(rec["pick"]["sub"], "Сейчас на UDP 443 никого")
        self.assertEqual(rec["radio"], ["(x) AmneziaWG 2.0рекомендуемсейчас UDP 39001", "( ) AmneziaWG 3.1сейчас UDP 51234"])
        self.assertEqual(len(rec["confirm"]["stat"]), 2, "с UDP 443 уезжать некому")
        # LZ-08: порт сменился только у 2.0 — её ключи раздать заново; 3.1 на своём порту, её ключи работают как работали.
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                res = self.scen("line_mismatch_" + lang)["result"]
                self.assertEqual(res["empty"], {"t": "AmneziaWG 2.0 — на UDP 443" if lang == "ru" else "AmneziaWG 2.0 is on UDP 443",
                                                "x": MOVED1[lang], "tone": "success"})
                self.assertEqual(res["kv"], ["AmneziaWG 3.1 = UDP 51234", "AmneziaWG 2.0 = UDP 443"])

    def test_switch_to_pro_with_a_line(self):
        """Макет: у Pro — «Перейти: 2.0 на 443» и «3.1 на 443». Смена режима, затем pro-line — одним окном: что переедет,
        сказано до согласия; линия, уже стоящая на 443, лишнего вызова не делает; отказ pro-line — режим включён,
        а линия — кнопкой «Линия на UDP 443»."""
        for lang in ("ru", "en"):
            with self.subTest(lang=lang):
                rec = self.scen("line_switch_" + lang)
                ru = lang == "ru"
                self.assertEqual(rec["card"]["btns"], ["Перейти: 2.0 на 443", "3.1 на 443"] if ru else ["Switch: 2.0 on 443", "3.1 on 443"])
                self.assertEqual(rec["card"]["alert"], "На UDP 443 — 2.0. Её понимает больше всего клиентов, включая роутеры Keenetic; 3.1 встанет на случайный порт. "
                                 "Поменять — «Режим и сайт → Линия на UDP 443»." if ru else
                                 "UDP 443 gets 2.0. Most clients understand it, including Keenetic routers; 3.1 goes to a random port. Change it under Mode and site → Line on UDP 443.")
                self.assertIn("warning: AmneziaWG 2.0 встанет на UDP 443 | Сразу после смены режима; AmneziaWG 3.1 переедет на свободный случайный порт. "
                              "Порт записан в каждом ключе — пользователям на обеих линиях придётся забрать ключи заново." if ru else
                              "warning: AmneziaWG 2.0 goes on UDP 443 | Right after the mode change; AmneziaWG 3.1 moves to a free random port. "
                              "The port is inside every key — users on both lines will need their keys again.", rec["what"]["stat"])
                self.assertEqual(rec["modeCalls"], [{"mode": "pro"}])
                self.assertEqual(rec["lineCalls"], [{"profile": "awg2", "confirm": "awg2"}])
                # LZ-08: не «выданные ключи работают как работали» — порт сменился у 2.0 и у 3.1, их ключи раздать заново.
                self.assertEqual(rec["result"]["empty"]["x"], (
                    "Сайт, домен и сертификат остались как были. Адрес в новых ключах — vpn.example.com (был 203.0.113.10). На UDP 443 — AmneziaWG 2.0. "
                    if ru else "The site, domain and certificate are as they were. New keys use vpn.example.com (was 203.0.113.10). UDP 443 has AmneziaWG 2.0. ")
                    + MOVED2[lang])
                self.assertEqual(rec["result"]["al"], [], "«осталось занять UDP/443» больше не нужно")
        # На UDP 443 никого не было: порт сменился у одной 2.0 — она названа, а 3.1 работает как работала.
        for lang in ("ru", "en"):
            with self.subTest(lang=lang, case="one"):
                one = self.scen("line_switch_one_" + lang)
                ru = lang == "ru"
                self.assertIn("warning: AmneziaWG 2.0 встанет на UDP 443 | Сразу после смены режима. Порт записан в каждом ключе — пользователям на этой линии придётся забрать ключи заново."
                              if ru else "warning: AmneziaWG 2.0 goes on UDP 443 | Right after the mode change. The port is inside every key — users on this line will need their keys again.",
                              one["what"]["stat"])
                self.assertEqual(one["lineCalls"], [{"profile": "awg2", "confirm": "awg2"}])
                self.assertEqual(one["result"]["empty"], {"t": "Режим «Pro — сайт и VPN на 443» включён" if ru else "Mode “Pro — site and VPN on 443” is on", "tone": "success",
                                                          "x": ("Сайт, домен и сертификат остались как были. Адрес в новых ключах — vpn.example.com. На UDP 443 — AmneziaWG 2.0. "
                                                                if ru else "The site, domain and certificate are as they were. New keys use vpn.example.com. UDP 443 has AmneziaWG 2.0. ")
                                                          + MOVED1[lang]})
        hidden = self.scen("line_switch_hidden_ru")
        self.assertTrue(hidden["hidden"])
        self.assertEqual(hidden["lineCalls"], [{"profile": "awg2", "confirm": "awg2"}])
        self.assertEqual(hidden["toast"], "Режим «Pro — сайт и VPN на 443» включёнНа UDP 443 — AmneziaWG 2.0. " + MOVED2["ru"])
        # Линия уже на UDP 443 — порт не меняется: выданные ключи и правда работают как работали.
        same = self.scen("line_switch_31_ru")
        self.assertIn("accent: AmneziaWG 3.1 останется на UDP 443 | Она уже там — порт не меняется, выданные ключи продолжат работать.", same["what"]["stat"])
        self.assertEqual(same["lineCalls"], 0)
        self.assertEqual(same["result"]["empty"]["x"], "Сайт, домен и сертификат остались как были. Адрес в новых ключах — vpn.example.com; "
                                                      "выданные ключи работают как работали. На UDP 443 — AmneziaWG 3.1.")
        fail = self.scen("line_switch_fail_ru")
        self.assertEqual(fail["result"]["empty"]["tone"], "success")
        # pro-line отказал — порты не тронуты, прежние слова о ключах верны.
        self.assertEqual(fail["result"]["empty"]["x"], "Сайт, домен и сертификат остались как были. Адрес в новых ключах — vpn.example.com; выданные ключи работают как работали.")
        self.assertEqual(fail["result"]["al"], ["warning: Режим включён, а линия на UDP 443 не встала. UDP 443 занят чужим.Линия на UDP 443"])
        self.assertEqual(fail["next"]["title"], "Pro · линия на UDP 443")
        only = self.scen("line_only31_ru")["card"]
        self.assertEqual(only["btns"], ["Перейти: 3.1 на 443"])
        self.assertEqual(only["alert"], "На UDP 443 — 3.1. Другие линии останутся на своих портах. Поменять — «Режим и сайт → Линия на UDP 443».")
        # Подхваченная 2.0 без согласия: режим включён, линия не тронута, второго вызова pro-line нет.
        no = self.scen("line_switch_adopted_no_ru")
        self.assertEqual(no["typed"]["title"], "Пользователи на AmneziaWG 2.0 останутся без связи")
        self.assertEqual((no["modeCalls"], no["lineCalls"]), (1, [{"profile": "awg2", "confirm": "awg2"}]))
        self.assertEqual(no["result"]["empty"]["tone"], "success")
        self.assertEqual(no["result"]["al"], ["default: Режим включён, линия на UDP 443 не переставлена. Перенос профиля, подхваченного у приложения, "
                                              "не подтверждён — ничего не тронуто.Линия на UDP 443"])

    def test_line_hidden_and_busy(self):
        hidden = self.scen("line_hidden_ru")
        self.assertTrue(hidden["hidden"])
        self.assertEqual(hidden["toast"], "AmneziaWG 2.0 — на UDP 443" + MOVED2["ru"])
        self.assertFalse(hidden["dlgAfter"])
        busy = self.scen("line_busy_en")["result"]
        self.assertEqual(busy["empty"], {"t": "The server is busy with another action", "tone": "warning",
                                         "x": "Another action is running right now (creating a backup) — retry in a minute"})
        self.assertEqual(busy["foot"], ["Close", "Try again"])

    def test_lazy_hidden_window_reports_by_toast(self):
        rec = self.scen("lazy_hidden_ru")
        self.assertTrue(rec["hidden"])
        self.assertEqual(rec["bar"], {"shown": True, "text": "Выполняется: Ленивый Pro", "busy": True})
        self.assertEqual(rec["toast"], "Готово: «Pro — сайт и VPN на 443» на vpn.example.comКлючи и страницы подключения — в карточках пользователей.")
        self.assertFalse(rec["dlgAfter"])


if __name__ == "__main__":
    unittest.main()

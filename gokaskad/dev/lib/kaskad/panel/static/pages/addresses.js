/* Kaskad 3.3.7.15 — «Настройки › Сервер › Адреса сервера» (SPEC-R1 §3 F10–F13, F12а; Claude Design «Адреса сервера» r1,
   export-2026-10-31-addresses-r1: HANDOFF-addresses.md, css/addresses.css → addresses.css). Часть ядра: K.loadPart
   ("addresses") грузит pages/addresses-i18n-ru.js (+ -en.js), этот файл; он регистрирует часть через K.part().

   Что рисует (сверху вниз; правило владельца «сначала графика, потом цифры»):
   - полосы над блоком: шлюз выключен (С10), префикс IPv6 сменился — событие ленты ip6_prefix_changed (С6);
   - «Адреса»: полоса ролей (колонка на адрес: приём · выдача IPv6 · выход · панель, у IPv6 — срок жизни), условные
     знаки, список с общими колонками (строка раскрывается: дорожки, «Приём» — gx-seg с замками, «Для выхода» —
     gx-sw, цифры gx-kv), низ — «Проверить заново», «Проверено …», «Сбросить выбор»;
   - «Выдача» (есть IPv6): у каких протоколов строка IPv6, переключатель, почему так;
   - «Выход» (≥ 2 адресов IPv4): схема «наши адреса → напрямую, выходы, сервер», «Сайты видят», выбор адресов.
   Один адрес IPv4 и нет IPv6 — «Адрес сервера» и подсказка (С1). Загрузка — скелет (С8), ошибка — полоса (С9).
   Телефон: полоса — в своей карточке, адреса — строки gx-mrow группами, адрес — деталь во весь экран (шапка
   панели прячется: K.rehead, phoneDetail), окна — те же gx-dlg.

   Данные: GET /api/gw/addresses (compose: addresses, addresses6, checked_at; mip — приём, выход, «напрямую», выдача;
   page — строки с кодами состояний; ver), ресурсы ядра R.exits (имена выходов) и R.gw (шлюз, домен). Записи —
   K.API.addresses.* с ver из чтения; 409 need: "confirm" — окно «было → станет», «Что изменится» (строки панели и
   последствия сервера по одному) и тот же запрос с yes: true; 409 object_changed — перечитать и сказать; другой 409 —
   окно отказа с текстом сервера. Атрибутов style в разметке нет (CSP): доли, места узлов схемы и --cols — CSSOM.
   «Разнести выходы по адресам» (F12б, POST …/spread) — в «Выходе», последним; подпись «xray перезапустится» — один раз
   на блок (REV-3315-b3 П-5, решение координатора Д2): у переключателя «Для выхода» раскрытой строки и строкой «Выхода»;
   предупреждение mip.route_out — в «Выходе» (REV-3315-b3 З-2).
   Доводка Д2 (stand/logs/D-3315-view-adr.md): «eth0 · второй адрес» (addresses[].secondary), «IPv6 выключен в ядре»
   (ipv6_off) отдельно от «IPv6 нет», без IPv6 — в полосе нет дорожек «Выдача IPv6» и «Срок IPv6»; подпись колонки
   полосы — полный адрес, пока входит в колонку (fitMap); «На этом же интерфейсе» — переносом. */
"use strict";

(() => {
  const K = window.KaskadPanel;
  if (!K) return;
  const { el, t, U } = K;
  const NS = "http://www.w3.org/2000/svg";
  const arr = (v) => (Array.isArray(v) ? v : []);
  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  const TAG = "gx-chip gx-chip--tag gx-chip--tiny", TW = "gx-chip gx-chip--warning gx-chip--tiny", TD = "gx-chip gx-chip--danger gx-chip--tiny";
  const X = (k) => "kk-adr-map__x" + (k ? " is-" + k : "");
  const PROTOS = [["VLESS Reality", 1], ["Hysteria2", 1], ["WireGuard", 1], ["AmneziaWG", 1], ["VLESS TLS", 0]];   // F13: строка IPv6 — у этих
  const SCH_ROW = 44, SCH_MAX = 6;                         // схема «Выхода»: строка справа 44 px, строк не больше 6

  // ── состояние части ─────────────────────────────────────────────────────────────────────────────────────────────
  const S = { doc: null, err: null, loading: false, seq: 0, host: null, open: "", det: "", busy: false, unsub: null, ev: null, asked: false };

  // ── IPv6: полная запись и /64 (префикс в «Цифрах», событие смены префикса) ───────────────────────────────────────
  function v6groups(ip) {
    const s = String(ip || "").toLowerCase().split("%")[0];
    if (s.indexOf(":") < 0) return null;
    const [a, b] = s.split("::");
    const ha = a ? a.split(":") : [], hb = b !== undefined && b ? b.split(":") : [];
    const fill = s.indexOf("::") >= 0 ? 8 - ha.length - hb.length : 0;
    const g = ha.concat(new Array(Math.max(0, fill)).fill("0"), hb).map((x) => parseInt(x || "0", 16));
    return g.length === 8 && g.every((x) => x >= 0 && x <= 0xffff) ? g : null;
  }
  function v6str(g) {
    let bs = -1, bl = 0;
    for (let i = 0; i < 8;) { if (g[i]) { i++; continue; } let j = i; while (j < 8 && !g[j]) j++; if (j - i > bl && j - i > 1) { bs = i; bl = j - i; } i = j; }
    const h = g.map((x) => x.toString(16));
    return bs < 0 ? h.join(":") : h.slice(0, bs).join(":") + "::" + h.slice(bs + bl).join(":");
  }
  const net64 = (ip) => { const g = v6groups(ip); return g ? v6str(g.slice(0, 4).concat([0, 0, 0, 0])) + "/64" : ""; };
  const pfxOf = (ip, n) => { const g = v6groups(ip); if (!g) return ""; const bits = Math.max(0, Math.min(128, n || 64)), out = g.slice();
    for (let i = 0; i < 8; i++) { const keep = Math.max(0, Math.min(16, bits - i * 16)); out[i] = keep >= 16 ? out[i] : keep <= 0 ? 0 : out[i] & (0xffff << (16 - keep)) & 0xffff; }
    return v6str(out) + "/" + bits; };
  const isV6 = (ip) => String(ip || "").indexOf(":") >= 0;

  // ── данные документа ─────────────────────────────────────────────────────────────────────────────────────────────
  const doc = () => S.doc;
  const mip = () => (S.doc && isObj(S.doc.mip) ? S.doc.mip : {});
  const page = () => (S.doc && isObj(S.doc.page) ? S.doc.page : { v4: [], v6: [] });
  const rows4 = () => arr(page().v4).filter((r) => isObj(r) && r.ip);
  const rows6 = () => arr(page().v6).filter((r) => isObj(r) && r.ip);
  const has = (r, c) => arr(r && r.codes).indexOf(c) >= 0;
  const inCode = (r) => arr(r && r.codes)[0] || "off";
  const a4 = (ip) => arr(S.doc && S.doc.addresses).find((x) => isObj(x) && x.ip === ip) || {};
  const a6 = (ip) => arr(S.doc && S.doc.addresses6).find((x) => isObj(x) && x.ip === ip) || {};
  const gone = (r) => has(r, "gone");
  const exRows = () => (K.R.exits && K.R.exits.loaded && K.R.exits.data ? arr(K.R.exits.data.rows) : []);
  const kindText = (r) => (r.kind === "awg" ? "AmneziaWG" : r.wg ? "WireGuard" : String(r.proto || r.kind || "").toUpperCase());
  const exTitle = (r) => r.name + (kindText(r) ? " · " + kindText(r) : "");
  const srcOf = (id) => { const x = arr(mip().exits).find((e) => isObj(e) && e.id === id); return x && x.src ? String(x.src) : "main"; };
  const primary = () => String(mip().primary || (rows4().find((r) => a4(r.ip).primary) || {}).ip || "");
  const directIp = () => { const d = mip().direct; return isObj(d) && d.ip ? String(d.ip) : ""; };
  const directOn = () => { const d = mip().direct; return isObj(d) && !!d.on; };
  const outOk = (r) => !!r && r.out !== false && !gone(r);
  const gwOff = () => !!(K.R.gw && K.R.gw.data && K.R.gw.data.enabled === false);
  // домен сайта (in_dns — среди его записей A / AAAA при последней проверке), иначе домен для клиентов
  const domainName = () => { const s = K.R.site && K.R.site.data, c = K.R.gw && K.R.gw.data && K.R.gw.data.clients;
    return s && s.domain ? s.name || s.domain : c && c.domain ? c.name || c.domain : ""; };
  const give6 = () => (isObj(mip().give6) ? mip().give6 : { ip: null, on: false, why: null });
  const shortList = (xs) => (xs.length <= 1 ? xs.join("") : t("adr.and", { a: xs.slice(0, -1).join(", "), b: xs[xs.length - 1] }));

  // что идёт с адреса IPv4: «напрямую» (своё или адрес маршрута, пока «напрямую» не действует) и выходы (свой адрес
  // или «по маршруту» — с адреса маршрута)
  function usedBy(ip, full) {
    const prim = primary(), d = directIp(), on = directOn(), out = [];
    if ((on && d === ip) || (!on && ip === prim)) out.push(t("adr.direct"));
    exRows().forEach((x) => { if (isV6(x.host)) return; const s = srcOf(x.id); if (s === ip || (s === "main" && ip === prim)) out.push(full ? exTitle(x) : x.name); });
    return out;
  }
  // короткая подпись колонки: «.10», «::5»; одинаковые хвосты — на группу длиннее («10::5» и «20::5»)
  function shortIps(list) {
    const v4 = list.filter((ip) => !isV6(ip)), v6 = list.filter(isV6), out = {};
    const pick = (ips, f) => { let n = 1; for (; n < 4; n++) { const s = ips.map((ip) => f(ip, n)); if (new Set(s).size === s.length) break; } ips.forEach((ip) => { out[ip] = f(ip, n); }); };
    pick(v4, (ip, n) => "." + ip.split(".").slice(-n).join("."));
    pick(v6, (ip, n) => { const g = v6groups(ip); if (!g) return ip; const last = g[7].toString(16); return n === 1 ? "::" + last : g.slice(4 - n + 1, 4).map((x) => x.toString(16)).join(":") + "::" + last; });
    return out;
  }

  // ── строка адреса: дорожки, огонёк, метки, коротко ───────────────────────────────────────────────────────────────
  function lanes(r) {
    const v6 = isV6(r.ip), g = gone(r), ic = inCode(r);
    const inL = g ? ["bad", t("adr.lv.gone")] : ic === "off" ? ["", t("adr.lv.off")] : ["on", t("adr.lv.accepts")];
    let giveL = ["na", t("adr.lv.v6only")];
    if (v6) {
      if (has(r, "echo_other")) giveL = ["", t("adr.lv.giveEcho")];
      else if (ic === "accept_give") giveL = [has(r, "deprecated") || has(r, "no_route6") || g ? "warn" : "on", t("adr.lv.giveOn")];
      else giveL = ["", t("adr.lv.no")];
    }
    const used = v6 ? [] : usedBy(r.ip);
    const outL = v6 ? ["na", t("adr.lv.v4only")] : used.length ? ["on", t("adr.lv.outList", { list: shortList(used) })] : ["", t("adr.lv.no")];
    const panL = has(r, "lock_panel") ? ["on", t("adr.lv.panel")] : ["", t("adr.lv.no")];
    return [["in", inL], ["give", giveL], ["out", outL], ["panel", panL]];
  }
  const dotCls = (r) => "kk-adr-r__d" + (gone(r) ? " kk-adr-r__d--bad" : has(r, "no_route6") || has(r, "deprecated") || has(r, "echo_other") ? " kk-adr-r__d--warn" : inCode(r) === "off" ? " kk-adr-r__d--off" : "");
  function tags(r) {
    const out = [];
    const add = (cls, short, full, lock) => out.push({ cls, t: short, full: full || short, lock: !!lock });
    if (has(r, "lock_host")) add(TAG, t("adr.tag.lock_host"), t("adr.full.lock_host"));
    if (has(r, "route_src")) add(TAG, t("adr.tag.route_src"), t("adr.full.route_src"));
    if (has(r, "direct")) add(TAG, t("adr.tag.direct"), t("adr.full.direct"));
    if (has(r, "lock_panel")) add(TAG, t("adr.tag.lock_panel"), t("adr.full.lock_panel"), true);
    if (has(r, "lock_aaaa")) add(TAG, t("adr.tag.lock_aaaa"), t("adr.full.lock_aaaa"));
    if (has(r, "deprecated")) { const s = typeof r.hours === "number" ? t("adr.tag.deprecatedH", { n: r.hours }) : t("adr.tag.deprecated"); add(TW, s, s); }
    else if (has(r, "dynamic")) add(TAG, t("adr.tag.dynamic"));
    if (has(r, "echo_other")) add(TW, t("adr.tag.echo_other", { ip: r.seen_as || "?" }));
    if (gone(r)) add(TD, t("adr.tag.gone"));
    return out;
  }
  const inShort = (r) => t("adr.inS." + inCode(r));
  const outShort = (r) => (isV6(r.ip) ? "—" : t(outOk(r) || (r.out !== false && !gone(r)) ? "adr.out.yes" : "adr.out.no"));
  function life(r) {
    const a = a6(r.ip);
    if (has(r, "deprecated")) { const h = typeof r.hours === "number" ? r.hours : 0; return { cls: "kk-adr-life is-dep", w: Math.max(4, Math.min(100, Math.round(h / 24 * 100))), t: t("adr.life.dep", { n: h }), ts: t("adr.life.depS", { n: h }) }; }
    if (has(r, "dynamic") || a.dynamic) return { cls: "kk-adr-life is-dyn", w: 100, t: t("adr.life.dyn"), ts: t("adr.life.dynS") };
    return { cls: "kk-adr-life", w: 100, t: t("adr.life.static"), ts: t("adr.life.staticS") };
  }
  // «подпись — значение» как в макете: значение, отметка ✓ / ✗ и ссылка — рядом в ячейке значения; выбор — вместо текста
  const kvEl = (rows) => el("div", { class: "gx-kv" }, rows.map((r) => el("div", { class: "gx-kv__row" }, el("span", { class: "gx-kv__k", text: r.k }),
    el("span", { class: "gx-kv__v" + (r.mono ? " gx-kv__v--mono" : " gx-kv__v--wrap") }, r.sel || r.node || el("span", { class: "gx-kv__text", title: r.mono ? String(r.v) : null }, String(r.v), r.s ? el("small", { text: r.s }) : null),
      r.sel && r.s ? el("span", { class: "gx-hint", text: r.s }) : null, r.mk || null, r.link || null), el("span", { class: "gx-kv__end" }))));
  const chip = (g) => el("span", { class: g.cls, title: g.full }, g.t, g.lock ? K.icon("lock", 12) : null);
  const why = (ic, text, id) => el("p", { class: "kk-adr-why", id: id || null }, K.icon(ic, 14), el("span", { text }));

  // ── полоса ролей ─────────────────────────────────────────────────────────────────────────────────────────────────
  // без IPv6 на сервере дорожек «Выдача IPv6» и «Срок IPv6» нет (у IPv4 они пустые), знака «не бывает» — тоже (С2, С3)
  function mapBlock() {
    const v4 = rows4(), v6 = rows6(), sh = shortIps(v4.concat(v6).map((r) => r.ip)), no6 = !v6.length;
    const lg = el("div", { class: "kk-adr-map__lg", "aria-hidden": "true" }, ["", t("adr.lane.in"), no6 ? null : t("adr.lane.give"), t("adr.lane.out"), t("adr.lane.panel"), t("adr.lane.addr"), no6 ? null : t("adr.lane.life")]
      .filter((x) => x !== null).map((x) => el("span", { text: x })));
    const col = (r, gh, g) => {
      const L = lanes(r).filter((p) => !(no6 && p[0] === "give")), lf = isV6(r.ip) ? life(r) : null;
      const label = r.ip + ": " + L.map((p) => t("adr.lane." + p[0]) + " — " + p[1][1]).join("; ") + (lf ? "; " + t("adr.lane.life") + " — " + lf.t : "");
      const b = el("button", { type: "button", class: "kk-adr-map__c" + (g ? " is-g" : "") + (S.open === r.ip ? " is-cur" : "") + (gone(r) ? " is-gone" : ""), "aria-label": label, "data-adr-col": r.ip },
        el("span", { class: "kk-adr-map__gh", text: gh }), L.map((p) => el("i", { class: X(p[1][0]) })),
        el("span", { class: "kk-adr-map__ip", text: r.ip, title: r.ip }), el("span", { class: "kk-adr-map__ips", text: sh[r.ip] || r.ip, title: r.ip }));
      if (lf) {
        const bar = el("i");
        bar.style.width = lf.w + "%";
        b.append(el("span", { class: lf.cls }, el("span", { class: "kk-adr-life__b" }, bar), el("span", { class: "kk-adr-life__t", text: lf.t }), el("span", { class: "kk-adr-life__ts", text: lf.ts })));
      }
      b.addEventListener("click", () => openRow(r.ip, true));
      return b;
    };
    const cols = v4.map((r, i) => col(r, i === 0 ? t("adr.gV4", { n: v4.length }) : "", false))
      .concat(v6.map((r, i) => col(r, i === 0 ? t("adr.gV6", { n: v6.length }) : "", i === 0)));
    const key = el("p", { class: "kk-adr-key" }, [["on", "adr.key.on"], ["", "adr.key.off"], ["na", "adr.key.na"], ["bad", "adr.key.gone"]].filter((k) => !(no6 && k[0] === "na"))
      .map((k) => el("span", {}, el("i", { class: X(k[0]) }), t(k[1]))));
    return el("div", { class: "kk-adr-mapw" + (K.isPhone() ? " gx-card" : "") }, el("div", { class: "kk-adr-map" + (no6 ? " is-no6" : ""), role: "group", "aria-label": t("adr.mapAria") }, lg, cols), key);
  }

  // ── раскрытая строка ─────────────────────────────────────────────────────────────────────────────────────────────
  // «Интерфейс»: «eth0 · второй адрес» (addresses[].secondary, только IPv4) — строка цифр и подзаголовок адреса на
  // телефоне берут одно значение (кадр М3)
  const ifaceText = (r) => { const v6 = isV6(r.ip), A = v6 ? a6(r.ip) : a4(r.ip);
    return (A.iface || r.iface || "—") + (!v6 && A.secondary === true ? " · " + t("adr.kv.second") : ""); };
  let whySeq = 0;
  function detBlock(r) {
    const v6 = isV6(r.ip), g = gone(r), ic = inCode(r), A = v6 ? a6(r.ip) : a4(r.ip);
    const lanesEl = el("div", { class: "kk-adr-lanes" }, lanes(r).map((p) => el("div", { class: "kk-adr-lane" + (p[1][0] === "on" ? "" : " is-off") },
      el("i", { class: X(p[1][0]) }), el("span", { class: "kk-adr-lane__k", text: t("adr.lane." + p[0]) }), el("span", { class: "kk-adr-lane__v", text: p[1][1] }))));
    // «Приём»: значения, замки с причиной (кнопка в фокусе, aria-disabled), причина строкой под выбором
    const opts = v6 ? ["accept_give", "reserve", "off"] : ["accept", "off"];
    const lock = {}, whys = [];
    const addWhy = (k, ic2, text) => { const id = "adrw" + (++whySeq); whys.push(why(ic2, text, id)); if (k) lock[k] = (lock[k] ? lock[k] + " " : "") + id; };
    if (!v6 && ic !== "off" && has(r, "lock_host")) addWhy("off", "lock", t("adr.lock.host", { ip: r.ip }));
    if (!v6 && ic !== "off" && has(r, "lock_panel")) addWhy("off", "lock", t("adr.lock.panel", { ip: r.ip }));
    if (g) opts.filter((o) => o !== "off" && o !== ic).forEach((o) => { lock[o] = lock[o] || "gone"; });
    if (g) addWhy("", "lock", t("adr.lock.gone", { ip: r.ip }));
    if (v6 && !g && ic !== "accept_give") {
      if (has(r, "echo_other")) addWhy("accept_give", "lock", t("adr.lock.echo", { ip: r.ip, seen: r.seen_as || "?" }));
      else if (has(r, "deprecated")) addWhy("accept_give", "lock", t("adr.lock.dep", { ip: r.ip }));
      else if (has(r, "no_route6")) addWhy("accept_give", "lock", t("adr.lock.noRoute", { ip: r.ip }));
    }
    if (v6 && ic === "accept_give") addWhy("", "circle-info", t("adr.giveHint"));
    const seg = el("div", { class: "gx-seg", role: "group", "aria-label": t("adr.fIn") }, opts.map((o) => {
      const dis = !!lock[o], ids = lock[o] && lock[o] !== "gone" ? lock[o] : null;
      // «в AAAA-записи»: «Не принимать» спросит подтверждение — подсказка у кнопки
      const tip = o === "off" && ic !== "off" && has(r, "lock_aaaa") ? t("adr.lock.aaaa", { ip: r.ip }) : null;
      const b = el("button", { type: "button", "aria-pressed": String(o === ic), "aria-disabled": dis ? "true" : "false", "aria-describedby": ids, "data-adr-in": o, title: tip },
        dis && lock[o] !== "gone" ? K.icon("lock", 14) : null, t("adr.seg." + o));
      // кадр Д5 (D-3315-view-adr-r3): щелчок выбор не переключает — пока открыто подтверждение, нажатым остаётся прежнее
      // значение в обычном виде gx-seg (затемнение приглушает его вместе со страницей); «Отмена» — ничего не меняется,
      // согласие — нажатым станет новое, когда ответит сервер (reload перерисует строку)
      b.addEventListener("click", () => { if (dis || o === ic || S.busy) return; setIn(r, o); });
      return b;
    }));
    const fIn = el("div", { class: "kk-adr-f" }, el("span", { class: "kk-adr-f__l", text: t("adr.fIn") }), seg, whys);
    let fOut;
    if (v6) fOut = el("div", { class: "kk-adr-f" }, el("span", { class: "kk-adr-f__l", text: t("adr.fOut") }), el("span", { class: "kk-adr-f__na", text: "—" }), why("circle-info", t("adr.v6noOut")));
    else {
      const used = usedBy(r.ip), on = r.out !== false && !g;
      const d = used.length ? t("adr.swOutNow", { list: shortList(used) }) : t("adr.swOutNone");
      const wid = g ? "adrw" + (++whySeq) : null;
      const sw = el("button", { type: "button", class: "gx-sw", role: "switch", "aria-checked": String(on), "aria-disabled": g ? "true" : "false", "aria-describedby": wid, "data-adr-out": r.ip },
        el("span", { class: "gx-sw__t" }, el("span", { text: t("adr.swOut") }), el("span", { text: d + (has(r, "route_src") ? " " + t("adr.swOutRoute") : "") + " " + t("adr.xrayD") })), el("span", { class: "gx-track" }));
      sw.addEventListener("click", () => { if (g || S.busy) return; setOut(r, !on); });
      fOut = el("div", { class: "kk-adr-f" }, el("span", { class: "kk-adr-f__l", text: t("adr.fOut") }), sw, g ? why("lock", t("adr.lock.outGone"), wid) : null);
    }
    // цифры
    const mk = (ok, text) => el("span", { class: "kk-adr-mk kk-adr-mk--" + (ok ? "ok" : "no") }, K.icon(ok ? "check" : "xmark", 14), text || "");
    const kv = [];
    const row = (k, v, o) => kv.push(Object.assign({ k, v }, o || {}));
    row(t("adr.kv.iface"), ifaceText(r));
    if (v6) row(t("adr.kv.from"), t(A.dynamic || has(r, "dynamic") ? "adr.from.slaac" : "adr.from.static"));
    else row(t("adr.kv.from"), A.origin === "nat" ? t("adr.from.nat", { ip: A.local || "?" }) : A.origin === "manual" ? t("adr.from.manual") : A.origin === "host" ? t("adr.from.host") : t("adr.from.iface"));
    if (!v6 && has(r, "lock_host")) row(t("adr.kv.host"), t("adr.kv.yes"), { link: U.btn(t("adr.hostLink"), "link", () => K.goNav("settings", { tab: "gateway" })) });
    if (v6 && typeof A.prefix === "number") row(t("adr.kv.pfx"), pfxOf(r.ip, A.prefix), { mono: true });
    if (A.in_dns === true && domainName()) row(t("adr.kv.dom"), domainName(), { mk: mk(true, v6 ? "AAAA" : "A") });
    if (v6 && has(r, "echo_other")) row(t("adr.kv.seen"), r.seen_as || "?", { mono: true, mk: mk(false, t("adr.kv.notIt")) });
    const route = v6 ? A.route_ok6 : A.route_ok;
    row(t("adr.kv.path"), route === true ? t("adr.kv.has") : route === false ? t("adr.kv.no") : t("adr.kv.unknown"), route === true || route === false ? { mk: mk(route === true, "") } : {});
    if (!v6) { const u = usedBy(r.ip, true); row(t("adr.kv.exits"), u.length ? u.join(", ") : "—"); }
    const iface = A.iface || r.iface;
    const same = iface ? rows4().concat(rows6()).filter((x) => x.ip !== r.ip && (isV6(x.ip) ? a6(x.ip).iface || x.iface : a4(x.ip).iface || x.iface) === iface).map((x) => x.ip) : [];
    // адреса того же интерфейса — переносом по адресу (обрезанное «203.0.113.11 · 2001:db…» — кадр Д1)
    if (same.length) row(t("adr.kv.same"), "", { node: el("span", { class: "gx-kv__text kk-adr-ips" }, same.map((ip, i) => el("span", { class: "kk-adr-ips__i", text: ip + (i < same.length - 1 ? " ·" : "") }))) });
    return el("div", { class: "kk-adr-det" }, lanesEl, el("div", { class: "kk-adr-ctl" }, fIn, fOut), kvEl(kv));
  }

  // ── список ───────────────────────────────────────────────────────────────────────────────────────────────────────
  function rowEl(r, solo) {
    const open = solo || S.open === r.ip;
    const n = el("div", { class: "kk-adr-r" + (gone(r) ? " is-gone" : "") + (solo ? " kk-adr-r--solo" : ""), role: "listitem" });
    if (!solo) {
      const b = el("button", { type: "button", class: "kk-adr-r__b", "aria-expanded": String(open), "data-adr-row": r.ip },
        el("span", { class: dotCls(r) }), el("span", { class: "kk-adr-r__ip", text: r.ip, title: r.ip }),
        el("span", { class: "kk-adr-r__tg" }, tags(r).map(chip)), el("span", { class: "kk-adr-r__in", text: inShort(r) }),
        el("span", { class: "kk-adr-r__out" }, el("span", { class: "kk-adr-r__ol", text: t("adr.outL") + " " }), outShort(r)),
        el("span", { class: "kk-adr-r__ch" }, K.icon("chevron-down", 16)));
      b.addEventListener("click", () => openRow(open ? "" : r.ip, false));
      n.append(b);
    }
    if (open) n.append(detBlock(r));
    return n;
  }
  // нет IPv6: «выключен в ядре» (ipv6_off ядра) или «на сервере нет» — кадры С3 и С2
  const noV6 = () => el("p", { class: "kk-adr-list__e", "data-adr-e": doc() && doc().ipv6_off === true ? "off" : "none", text: t(doc() && doc().ipv6_off === true ? "adr.v6off" : "adr.noV6") });
  function v6warn() { const v6 = rows6().filter((r) => !gone(r)); return v6.length && v6.every((r) => has(r, "no_route6")) ? t("adr.warn.noRoute6") : ""; }
  function listBlock() {
    const v4 = rows4(), v6 = rows6(), w = v6warn();
    const head = el("div", { class: "kk-adr-list__h", "aria-hidden": "true" }, ["adr.col.addr", "adr.col.tags", "adr.col.in", "adr.col.out"].map((k) => el("span", { text: t(k) })));
    const g = (h, warn) => el("div", { class: "kk-adr-list__g" }, el("span", { text: h }), warn ? el("span", { class: "kk-adr-warn", text: warn }) : null);
    return el("div", { class: "kk-adr-list", role: "list" }, head,
      g(t("adr.gV4", { n: v4.length })), v4.map((r) => rowEl(r)),
      g(v6.length ? t("adr.gV6", { n: v6.length }) : t("adr.gV6none"), w),
      v6.length ? v6.map((r) => rowEl(r)) : noV6());
  }
  // телефон: группы строк gx-mrow; нажатие — адрес во весь экран
  function phoneList() {
    const Kit = K.Kit, w = v6warn();
    const sec = (h, rows, warn, empty) => el("div", { class: "gx-msec" }, el("div", { class: "gx-msec__h" }, el("h3", { class: "gx-msec__t", text: h })),
      warn ? why("triangle-exclamation", warn) : null,
      rows.length ? Kit.mlist(rows.map((r) => Kit.mrow({ lead: el("span", { class: dotCls(r) }), title: r.ip, mono: true, aria: r.ip,
        sub: tags(r).map((x) => x.t).concat([t("adr.outL") + " " + outShort(r)]).join(" · "), end: inShort(r), find: null,
        onClick: () => showDet(r.ip) }))) : empty);
    return [sec(t("adr.gV4", { n: rows4().length }), rows4(), "", null), sec(rows6().length ? t("adr.gV6", { n: rows6().length }) : t("adr.gV6none"), rows6(), w, noV6())];
  }
  function phoneDetail(r) {
    const Kit = K.Kit, v6 = isV6(r.ip), A = v6 ? a6(r.ip) : a4(r.ip);
    const from = v6 ? t(A.dynamic || has(r, "dynamic") ? "adr.from.slaac" : "adr.from.static") : A.origin === "nat" ? t("adr.from.nat", { ip: A.local || "?" }) : A.origin === "manual" ? t("adr.from.manual") : A.origin === "host" ? t("adr.from.host") : t("adr.from.iface");
    const tone = gone(r) ? "bad" : inCode(r) === "off" ? "" : has(r, "no_route6") || has(r, "deprecated") || has(r, "echo_other") ? "warn" : "ok";
    const hero = Kit.mhero({ lead: el("span", { class: "gx-mhero__ic" + (tone ? " gx-mhero__ic--" + tone : "") }, K.icon("server", 24)), name: r.ip,
      sub: [el("span", { text: t("adr.det.sub", { iface: ifaceText(r), fam: v6 ? "IPv6" : "IPv4", from }) })] });
    const tg = tags(r);
    return [Kit.mnav({ back: t("adr.back"), onBack: () => showDet("") }), hero,
      tg.length ? el("div", { class: "gx-mchips" }, tg.map(chip)) : null,
      el("section", { class: "gx-card", id: "stAdr", "aria-label": r.ip }, el("div", { class: "kk-adr-list", role: "list" }, rowEl(r, true)))];
  }

  // ── низ блока «Адреса» ───────────────────────────────────────────────────────────────────────────────────────────
  function agoText() {
    const at = S.doc && S.doc.checked_at ? Date.parse(S.doc.checked_at) : NaN;
    if (!isFinite(at)) return t("adr.checkedNever");
    const m = Math.max(0, Math.floor((Date.now() - at) / 60000));
    return t("adr.checked", { ago: m < 1 ? t("adr.ago.now") : m < 60 ? t("adr.ago.min", { n: m }) : t("adr.ago.h", { n: Math.floor(m / 60) }) });
  }
  function footBlock() {
    const chk = U.btn(t("adr.check"), "tertiary", () => reload(true), { icon: "arrow-rotate-right" });
    chk.dataset.adrCheck = "";
    const rs = U.btn(t("adr.reset"), "link", () => resetRoles());
    rs.dataset.adrReset = "";
    return el("div", { class: "kk-adr-foot" }, chk, el("span", { class: "kk-adr-foot__t", text: agoText() }), el("span", { class: "gx-hint", text: t("adr.checkedH") }),
      el("span", { class: "kk-adr-foot__sp" }), el("span", { class: "kk-adr-foot__r" }, rs, el("span", { class: "gx-hint", text: t("adr.resetH") })));
  }

  // ── «Выдача» ─────────────────────────────────────────────────────────────────────────────────────────────────────
  // кандидат выдачи (переключатель без выбранного IPv6): «принимать и выдавать» строки, иначе IPv6, который принимает,
  // не устаревает, с путём наружу и виден снаружи собой; раньше — из AAAA-записи и статический (HANDOFF, вопрос 4)
  function giveCand() {
    const g = give6();
    if (g.ip) return String(g.ip);
    const ok = rows6().filter((r) => inCode(r) !== "off" && !gone(r) && !has(r, "deprecated") && !has(r, "no_route6") && !has(r, "echo_other"));
    ok.sort((x, y) => (a6(y.ip).in_dns === true) - (a6(x.ip).in_dns === true) || (has(x, "dynamic") - has(y, "dynamic")));
    return ok.length ? ok[0].ip : "";
  }
  function giveBlock() {
    const g = give6(), ip = giveCand(), on = !!g.ip && !!g.on;
    const pl = el("div", { class: "kk-adr-pl" }, PROTOS.map((p) => el("div", { class: "kk-adr-pl__i" + (p[1] ? "" : " is-na") }, el("i", { class: X(p[1] ? (on ? "on" : "") : "na") }),
      el("span", { class: "kk-adr-pl__n", text: p[0] }), el("span", { class: "kk-adr-pl__s", text: p[1] ? t("adr.gLine") : t("adr.gNo") }))));
    const wid = ip ? null : "adrgw";
    const sw = el("button", { type: "button", class: "gx-sw", role: "switch", "aria-checked": String(!!g.ip), "aria-disabled": ip ? "false" : "true", "aria-describedby": wid, "data-adr-give": "" },
      el("span", { class: "gx-sw__t" }, el("span", {}, t("adr.gSw"), el("b", { text: ip || "—" })), el("span", { text: t("adr.gSwD") })), el("span", { class: "gx-track" }));
    sw.addEventListener("click", () => { if (!ip || S.busy) return; setGive(g.ip ? null : ip, g.ip || ip); });
    const ws = [];
    if (!ip) ws.push(why("lock", t("adr.gNone"), wid));
    if (g.ip && !g.on) ws.push(why("triangle-exclamation", t("adr.give.idle", { why: t("adr.give.why." + (g.why || "off")) })));
    ws.push(why("circle-info", t("adr.gName")), why("arrow-rotate-right", t("adr.gXray")), why("clock", t("adr.gLater")));
    return el("section", { class: "gx-card", id: "stAdrGive", "aria-label": t("adr.bGive") }, cardHead(t("adr.bGive")),
      el("div", { class: "kk-adr-give" }, el("p", { class: "kk-adr-cap", text: t("adr.gCap") }), pl, sw, ws));
  }

  // ── «Выход» ──────────────────────────────────────────────────────────────────────────────────────────────────────
  // узлы выходов справа в схеме: все, если входят в room строк, иначе room − 1 (последняя строка — «ещё N»). В «ещё N»
  // уходят выходы с адреса маршрута prim (его линии и так есть — «Напрямую», «Сервер»), а не единственные у своего узла
  // слева — иначе узел остаётся без линии (выход по IPv6 — «маршрут IPv6», выход со своего адреса)
  function schPick(nodes, prim, room) {
    if (nodes.length <= room) return nodes.slice();
    const own = [];
    nodes.forEach((n) => { if (n.from !== prim && !own.some((o) => o.from === n.from)) own.push(n); });
    return own.concat(nodes.filter((n) => own.indexOf(n) < 0 && n.from !== prim), nodes.filter((n) => own.indexOf(n) < 0 && n.from === prim)).slice(0, room - 1);
  }
  function outBlock() {
    const prim = primary(), dIp = directIp(), dOn = directOn(), ex = exRows();
    const v4ex = ex.filter((x) => !isV6(x.host)), v6ex = ex.filter((x) => isV6(x.host));
    const route6 = (rows6().find((r) => !gone(r) && a6(r.ip).route_ok6 === true) || {}).ip || "";
    // левые узлы: адрес маршрута, адреса, с которых что-то идёт, маршрут IPv6 (есть выходы по IPv6)
    const left = [{ ip: prim, s: t("adr.sch.route") }];
    const fromIp = (ip) => { if (ip && ip !== prim && !left.some((n) => n.ip === ip)) left.push({ ip, s: t("adr.sch.out") }); };
    if (dOn) fromIp(dIp);
    v4ex.forEach((x) => { const s = srcOf(x.id); if (s !== "main") fromIp(s); });
    if (v6ex.length && route6) left.push({ ip: route6, s: t("adr.sch.route6"), v6: true });
    // правые: «Напрямую», выходы (не больше SCH_MAX − 2, остальные — «ещё N»), сервер
    const right = [{ b: t("adr.sch.direct"), s: t("adr.sch.all"), from: dOn && dIp ? dIp : prim, cur: true }];
    const exNodes = v4ex.map((x) => { const s = srcOf(x.id); return { b: exTitle(x), s: s === "main" ? t("adr.oByRoute") : t("adr.sch.from", { ip: s }), from: s === "main" ? prim : s }; })
      .concat(v6ex.map((x) => ({ b: exTitle(x), s: t("adr.sch.v6"), from: route6, v6: true })));
    const room = SCH_MAX - 2;
    schPick(exNodes, prim, room).forEach((n) => right.push(n));
    if (exNodes.length > room) { const n = exNodes.length - room + 1; right.push({ b: t("adr.sch.more", { n }), s: K.tn("adr.sch.moreS", n), from: "" }); }
    right.push({ b: t("adr.sch.srv"), s: t("adr.oOwnS"), from: prim });
    const H = SCH_ROW * right.length;
    const ly = left.map((n, j) => Math.round(H * (j + 0.5) / left.length));
    const lEl = el("div", { class: "kk-adr-sch__l" }, left.map((n, j) => { const s = el("span", { class: "kk-adr-sch__n" }, el("span", { class: "kk-adr-sch__ip", text: n.ip || "—", title: n.ip || null }), el("span", { class: "kk-adr-sch__s", text: n.s })); s.style.top = ly[j] + "px"; return s; }));
    lEl.style.height = H + "px";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "kk-adr-sch__svg"); svg.setAttribute("viewBox", "0 0 100 " + H); svg.setAttribute("preserveAspectRatio", "none"); svg.setAttribute("aria-hidden", "true");
    svg.style.height = H + "px";
    right.forEach((n, i) => {
      const j = left.findIndex((x) => x.ip === n.from);
      if (j < 0) return;
      const y1 = ly[j], y2 = SCH_ROW * i + SCH_ROW / 2, p = document.createElementNS(NS, "path");
      p.setAttribute("d", "M0 " + y1 + " C50 " + y1 + " 50 " + y2 + " 100 " + y2);
      if (n.cur) p.setAttribute("class", "is-cur"); else if (n.v6) p.setAttribute("class", "is-v6");
      svg.append(p);
    });
    const rEl = el("div", { class: "kk-adr-sch__r" }, right.map((n) => el("span", { class: "kk-adr-sch__t" + (n.cur ? " is-cur" : "") }, el("b", { text: n.b, title: n.b }), el("span", { text: n.s, title: n.s }))));
    rEl.style.gridTemplateRows = "repeat(" + right.length + ", " + SCH_ROW + "px)";
    const sch = el("div", { class: "kk-adr-sch", role: "img", "aria-label": t("adr.schAria") + ": " + right.map((n) => n.b + " — " + (n.from || "")).join("; ") }, lEl, svg, rEl);
    const sees = dOn && dIp ? dIp : prim;
    const okIps = rows4().filter(outOk).map((r) => r.ip);
    const sel = (label, cur, opts, onPick, key) => {
      const s = el("select", { class: "gx-input", "aria-label": label, "data-adr-sel": key });
      const all = opts.slice();
      if (cur && !all.some((o) => o[0] === cur)) all.push([cur, cur]);
      all.forEach((o) => s.append(el("option", { value: o[0], text: o[1], selected: o[0] === cur ? true : null })));
      s.value = cur;
      s.addEventListener("change", () => { if (S.busy) { s.value = cur; return; } onPick(s.value, () => { s.value = cur; }); });
      return s;
    };
    const kv = [{ k: t("adr.oDirect"), sel: sel(t("adr.oDirect"), dIp, [["", t("adr.oDefault")]].concat(okIps.map((ip) => [ip, ip])), (v, undo) => setDirect(v || null, undo), "direct") }];
    v4ex.forEach((x) => kv.push({ k: exTitle(x), sel: sel(exTitle(x), srcOf(x.id), [["main", t("adr.oByRoute")]].concat(okIps.map((ip) => [ip, ip])), (v, undo) => setExit(x, v, undo), "exit:" + x.id) }));
    v6ex.forEach((x) => kv.push({ k: exTitle(x), v: t("adr.oV6") }));
    kv.push({ k: t("adr.oOwn"), v: t("adr.oFromRoute"), s: t("adr.oOwnS") });
    const idle = dIp && !dOn ? why("triangle-exclamation", t("adr.oIdle", { ip: dIp, why: t("adr.oWhy." + (mip().direct.why || "route")) })) : null;
    // REV-3315-b3 З-2: адрес маршрута не «для выхода», а выходы без своего адреса подключаются с него (mip.route_out)
    const ro = isObj(mip().route_out) ? mip().route_out : null;
    const loose = ro && arr(ro.ids).length ? U.al("warning", "", t("adr.routeLoose", { ip: ro.ip || prim, names: shortList(arr(ro.ids).map((id) => (ex.find((x) => x.id === id) || {}).name || id)) })) : null;
    // F12б: выходам без своего адреса — адреса «для выхода» поровну (подтверждение — последствия сервера)
    const sp = U.btn(t("adr.oSpread"), "tertiary", () => spread(), { icon: "arrow-right-arrow-left" });
    sp.dataset.adrSpread = "";
    return el("section", { class: "gx-card kk-adr-out", id: "stAdrOut", "aria-label": t("adr.bOut") }, cardHead(t("adr.bOut")), sch,
      el("p", { class: "kk-adr-sees" }, el("span", { text: t("adr.oSees") }), el("b", { text: sees || "—" }), sees === prim ? el("span", { text: t("adr.oRoute") }) : null),
      idle, loose, kvEl(kv), why("arrow-rotate-right", t("adr.oXray")), el("div", { class: "kk-adr-acts" }, sp, el("span", { class: "gx-hint", text: t("adr.oSpreadH") })));
  }

  // ── полосы над блоком ────────────────────────────────────────────────────────────────────────────────────────────
  // смена префикса IPv6: последнее событие ленты, пока на сервере есть адрес старого /64 (он устаревает) или роль на нём
  function prefixEvent() {
    const ov = K.R.overview && K.R.overview.data, list = [].concat(S.ev ? [S.ev] : [], arr(ov && ov.liveRaw && ov.liveRaw.events));
    const e = list.find((x) => isObj(x) && x.kind === "ip6_prefix_changed");
    if (!e) return null;
    const a = isObj(e.args) ? e.args : e;
    if (!a.old || !a.suggest) return null;
    const inOld = (ip) => net64(ip) === String(a.old).toLowerCase();
    const roles = arr(mip().in6 && mip().in6.list).concat(give6().ip ? [give6().ip] : []).filter(inOld);
    const live = rows6().some((r) => inOld(r.ip) && !gone(r));
    return roles.length || live ? { old: String(a.old), new: String(a.new || net64(a.suggest)), ip: String(a.suggest), roles } : null;
  }
  function topAlerts() {
    const out = [];
    if (gwOff()) out.push(U.al("warning", t("adr.gwOffT"), t("adr.gwOff")));
    // ядро П3: warnings чтения (сбой отложенного перезапуска xray — пока xray не догонит) — одной полосой, без повторов
    const ws = warnsOf(S.doc).filter((w, i, a) => a.indexOf(w) === i);
    if (ws.length) out.push(U.al("warning", "", ws.join(" ")));
    const p = prefixEvent();
    if (p) {
      const btn = p.roles.length && rows6().some((r) => r.ip === p.ip && !gone(r)) ? U.btn(t("adr.moveRole", { ip: p.ip }), "tertiary", () => moveRole(p)) : null;
      out.push(U.al("warning", t("adr.pfxT", { old: p.old, new: p.new }), t("adr.pfx", { ip: p.ip }), { btns: [btn] }));
    }
    return out;
  }
  const cardHead = (title) => el("div", { class: "gx-card__head" }, el("div", { class: "gx-card__tt" }, el("span", { class: "gx-card__title", text: title })));

  // ── отрисовка ────────────────────────────────────────────────────────────────────────────────────────────────────
  function skeleton() {
    const sk = el("div", { class: "gx-skel" }, [1, 2, 3, 4].map(() => { const r = el("div", { class: "gx-skel__r" }, [1, 2, 3, 4, 5].map(() => el("span", { class: "kk-adr-sk" }))); r.style.setProperty("--cols", "8px 130px minmax(0, 1fr) 100px 60px"); return r; }));
    return el("section", { class: "gx-card", "aria-busy": "true", "aria-label": t("adr.bAdr") }, cardHead(t("adr.bAdr")), el("span", { class: "kk-adr-sk kk-adr-sk--map" }), sk);
  }
  function errorCard() {
    return el("section", { class: "gx-card", "aria-label": t("adr.bAdr") }, cardHead(t("adr.bAdr")),
      U.al("danger", t("adr.errT"), (S.err && S.err.message) || "", { row: true, btns: [U.btn(t("c.retry"), "tertiary", () => reload(false))] }));
  }
  const one = () => rows4().filter((r) => !gone(r)).length <= 1 && !rows6().length;
  function content() {
    if (!S.doc) return [S.err ? errorCard() : skeleton()];
    const phone = K.isPhone();
    const det = phone && S.det ? rows4().concat(rows6()).find((r) => r.ip === S.det) : null;
    if (phone && S.det && !det) S.det = "";
    if (det) return phoneDetail(det);
    const top = topAlerts();
    if (one()) {
      const ip = (rows4().find((r) => !gone(r)) || rows4()[0] || {}).ip || primary() || "—";
      return top.concat([el("section", { class: "gx-card", id: "stAdr", "aria-label": t("adr.bAdr") }, cardHead(t("adr.bAdr")),
        el("div", { class: "kk-adr-one" }, el("span", { class: "kk-adr-one__k", text: t("adr.one") }), el("span", { class: "kk-adr-one__v", text: ip })),
        why("circle-info", t("adr.oneH")))]);
    }
    const adr = phone ? el("section", { class: "kk-adr-ph", id: "stAdr", "aria-label": t("adr.bAdr") }, mapBlock(), phoneList(), footBlock())
      : el("section", { class: "gx-card", id: "stAdr", "aria-label": t("adr.bAdr") }, cardHead(t("adr.bAdr")), mapBlock(), listBlock(), footBlock());
    const side = [];
    if (rows6().length) side.push(giveBlock());
    if (rows4().filter((r) => !gone(r)).length >= 2) side.push(outBlock());
    return top.concat([el("div", { class: "kk-adr" }, adr, side.length ? el("div", { class: "kk-adr__side" }, side) : null)]);
  }
  // подпись колонки полосы: полный адрес, пока он входит в колонку, иначе короткий («.10», «::5») — по ширине самой
  // колонки, а не части (С2: четыре колонки на части 560 — полные адреса входят; шесть на 640 — уже нет). Ширина
  // колонки меняется с окном и шрифтом — ResizeObserver полосы
  let fitRO = null;
  function fitMap() {
    if (fitRO) { fitRO.disconnect(); fitRO = null; }
    const map = S.host && S.host.querySelector(".kk-adr-map");
    if (!map) return;
    const fit = () => {
      const cs = Array.from(map.querySelectorAll(".kk-adr-map__c"));
      cs.forEach((c) => c.classList.remove("is-short"));
      const cut = cs.map((c) => { const ip = c.querySelector(".kk-adr-map__ip"); return !!ip && ip.scrollWidth > ip.clientWidth + 1; });
      cs.forEach((c, i) => c.classList.toggle("is-short", cut[i]));
    };
    fit();
    if (typeof ResizeObserver === "function") { fitRO = new ResizeObserver(fit); fitRO.observe(map); }
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { if (map.isConnected) fit(); }, () => null);
  }
  // фокус переживает перерисовку: тот же элемент по data-adr-* (строка, колонка, выбор, переключатель)
  function focusKey() {
    const a = document.activeElement;
    if (!a || !S.host || !S.host.contains(a)) return null;
    for (const k of ["adrRow", "adrCol", "adrIn", "adrOut", "adrGive", "adrSel", "adrCheck", "adrReset", "adrSpread"]) if (a.dataset && a.dataset[k] !== undefined) return [k, a.dataset[k]];
    return null;
  }
  function paint() {
    if (!S.host) return;
    const fk = focusKey();
    S.host.replaceChildren(...content().filter(Boolean));
    fitMap();
    S.host.classList.toggle("is-det", !!S.det && K.isPhone());       // адрес во весь экран: без пояснения части
    if (fk) {
      const attr = "data-" + fk[0].replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
      const n = Array.from(S.host.querySelectorAll("[" + attr + "]")).find((x) => x.getAttribute(attr) === fk[1]);
      if (n) n.focus();
    }
  }
  // телефон: адрес во весь экран и назад — шапка панели прячется и возвращается (phoneHead), экран — с начала; назад —
  // фокус на строку этого адреса
  function showDet(ip) {
    const was = S.det;
    S.det = ip; paint(); K.rehead();
    const m = S.host && S.host.closest(".gx-main");
    if (m) m.scrollTop = 0;
    const f = ip ? S.host.querySelector(".gx-mnav__back") : was ? Array.from(S.host.querySelectorAll("button.gx-mrow")).find((b) => b.getAttribute("aria-label") && b.getAttribute("aria-label").indexOf(was) === 0) : null;
    if (f) f.focus();
  }
  function openRow(ip, fromMap) {
    if (K.isPhone()) { if (ip) showDet(ip); return; }
    S.open = ip;
    paint();
    if (fromMap && ip) {
      const b = Array.from(S.host.querySelectorAll("[data-adr-row]")).find((x) => x.getAttribute("data-adr-row") === ip);
      if (b) { b.focus(); try { b.scrollIntoView({ block: "nearest" }); } catch (e) { /* старый браузер */ } }
    }
  }

  // ── обрыв связи (3.3.7.15 П2) ─────────────────────────────────────────────────────────────────────────────────────
  // Запись «Напрямую», «Для выхода», «Сбросить выбор», «Выдача» при VLESS Reality перезапускает xray: панель, открытая
  // через 443, теряет ответ записи или следующего чтения (ошибка сети, пустой ответ). Это не ошибка: через ~2 с одно
  // перечитывание GET /api/gw/addresses и фактическое состояние; не вышло — обычная ошибка
  const DROP_MS = 2000;
  const empty = (d) => !isObj(d) || !Object.keys(d).length;                  // api() отдаёт {} на пустое тело
  const dropped = (e) => !!e && e.status === 0 && e.code === "net";          // ApiError «нет связи»: fetch оборвался
  const dropErr = () => Object.assign(new Error(t("err.net")), { status: 0, code: "net" });
  const later = (ms) => new Promise((ok) => setTimeout(ok, ms));
  // предупреждения чтения (ядро П3: warnings GET /api/gw/addresses) — строки
  const warnsOf = (d) => arr(d && d.warnings).filter((x) => typeof x === "string" && x);

  // ── чтение ───────────────────────────────────────────────────────────────────────────────────────────────────────
  // -> true — прочитано и нарисовано; false — нет (или ответ устарел). again — повтор после обрыва уже был; keep — при
  // отказе оставить прежние данные и сказать тостом (перечитывание после потерянного ответа записи)
  function reload(refresh, again, keep) {
    const mine = ++S.seq;
    S.loading = true;
    const deps = K.loadAll(["exits", "gw"].concat(K.R.site ? ["site"] : [])).catch(() => null);
    const got = K.API.addresses.get(refresh ? { refresh: 1 } : null).then((d) => { if (empty(d)) throw dropErr(); return d; });
    return got.then((d) => deps.then(() => {
      if (mine !== S.seq) return false;
      S.doc = isObj(d) ? d : null; S.err = null; S.loading = false;
      paint(); K.refresh();
      return true;
    }), (e) => {
      if (mine !== S.seq) return false;
      if (!again && dropped(e)) return later(DROP_MS).then(() => (mine === S.seq ? reload(refresh, true, keep) : false));
      S.loading = false;
      if (S.doc && (refresh || keep)) K.toast(t("adr.errT"), (e && e.message) || "", "danger");
      else { S.doc = null; S.err = e || new Error(t("adr.errT")); }
      paint();
      return false;
    });
  }

  // ── записи ───────────────────────────────────────────────────────────────────────────────────────────────────────
  // o: {call(body) — запрос, body, title, mv: {a, ax, as, b, bx, bs}, items — строки панели, btn — подпись действия,
  // noTitle — заголовок отказа, undo — вернуть выбор (select) при отмене или отказе}
  function write(o) {
    if (S.busy || !S.doc) return Promise.resolve(false);
    S.busy = true;
    const send = (yes) => o.call(Object.assign({}, o.body, yes ? { yes: true } : {}, { ver: S.doc.ver }));
    // ответ записи потерян (обрыв, пустой ответ): запись могла пройти — «Применить» заперта, через ~2 с одно
    // перечитывание; прочитано — фактическое состояние и пояснение, нет — обычная ошибка чтения, выбор возвращается.
    // Ответ перечитывания устарел (П7, REV-3315-p256 П-1: тем временем «Проверить» начала другое чтение) — выбор не
    // возвращаем: запись могла пройти, а новое чтение само нарисует то, что на сервере
    const lost = () => later(DROP_MS).then(() => {
      const r = reload(false, true, true), mine = S.seq;            // reload взял свой номер сразу
      return r.then((ok) => {
        S.busy = false;
        if (ok) { K.toast(t("adr.lostT"), t("adr.lostD"), "info"); return true; }
        if (mine === S.seq && o.undo) o.undo();
        return false;
      });
    });
    // итог — тостом со словами сервера; предупреждения записи (warnings, apply.warnings: xray не поднялся и т. п.) — в
    // том же тосте, тоном «внимание»: запись прошла, но не всё применилось. xray_after: true (ядро П3) — перезапуск
    // xray отложен до ответа: «Применить» заперта, чтение — через ~2 с (оборвётся — свой повтор); сбой отложенного
    // перезапуска приходит в warnings чтения — полосой страницы (topAlerts), не тостом
    const done = (res) => {
      if (empty(res)) return lost();
      const after = isObj(res) && res.xray_after === true;
      if (!after) S.busy = false;
      const warns = [].concat(arr(res && res.warnings), arr(res && res.apply && res.apply.warnings)).filter((x) => typeof x === "string" && x);
      if (res && (res.text || warns.length)) K.toast(res.text || t("c.attention"), warns.join(" "), warns.length ? "warning" : o.kindOf ? o.kindOf(res) : "success");
      if (!after) return reload(false).then(() => true);
      return later(DROP_MS).then(() => reload(false)).then(() => { S.busy = false; return true; });
    };
    const fail = (e) => {
      if (dropped(e)) return lost();
      S.busy = false;
      if (o.undo) o.undo();
      if (e && e.status === 409 && e.code === "object_changed") { K.toast(t("adr.changed"), "", "warning"); return reload(false).then(() => false); }
      if (e && e.status === 409 && e.data && e.data.need === "confirm") return ask(o, arr(e.data.consequences)).then((yes) => {
        if (!yes) return false;
        S.busy = true;
        return send(true).then(done, (e2) => { if (dropped(e2)) return lost(); S.busy = false; return refusal(o, e2); });
      });
      return refusal(o, e);
    };
    return send(false).then(done, fail);
  }
  function refusal(o, e) {
    if (e && e.status === 409 && e.code === "object_changed") { K.toast(t("adr.changed"), "", "warning"); return reload(false).then(() => false); }
    if (e && e.status === 409) {
      const d = K.openDialog({ stack: true, tone: "danger", icon: "lock", title: o.noTitle || t("adr.dlg.noAny"),
        blocks: [el("p", { class: "gx-desc", text: (e && e.message) || "" })] });
      d.setFoot([U.btn(t("adr.btn.close"), "tertiary", () => d.close(false))]);
      return reload(false).then(() => false);
    }
    K.toast((e && e.message) || t("adr.errT"), "", "danger");
    return Promise.resolve(false);
  }
  // окно «нужно подтверждение»: было → станет, «Что изменится» (строки панели, затем сервера — по одному), вопрос
  function ask(o, cons) {
    return new Promise((resolve) => {
      const blocks = [];
      if (o.mv) {
        const n = (ip, x, s) => el("span", { class: "kk-adr-mv__n" }, el("b", { text: ip }), el("i", { class: X(x) }), el("span", { text: s }));
        blocks.push(el("div", { class: "kk-adr-mv" }, n(o.mv.a, o.mv.ax, o.mv.as), el("span", { class: "kk-adr-mv__a" }, K.icon("chevron-right", 18)), n(o.mv.b, o.mv.bx, o.mv.bs)));
      }
      const items = arr(o.items).map((x) => [x, 0]).concat(cons.map((x) => [String(x), 1]));
      if (items.length) blocks.push(el("p", { class: "kk-adr-dh", text: t("adr.impact") }), el("ul", { class: "gx-impact" }, items.map((i) =>
        el("li", { class: "gx-impact__i" + (i[1] ? " gx-impact__i--warn" : "") }, el("span", { class: "gx-impact__m" }, K.icon(i[1] ? "triangle-exclamation" : "dot", 14)), el("span", { text: i[0] })))));
      if (o.q !== false) blocks.push(el("p", { class: "kk-adr-q", text: t("adr.q") }));
      let res = false;
      // ширина — как у gx-dlg--risk (600 px, кадры Д5–Д7): openDialog ставит --dlg-w на само окно, класс его не перебивает
      const d = K.openDialog({ stack: true, size: "600px", tone: o.tone || "warning", icon: o.icon || "triangle-exclamation", title: o.title, blocks, onClose: () => resolve(res) });
      d.box.classList.add("gx-dlg--risk");
      d.setFoot([U.btn(t("c.cancel"), "tertiary", () => d.close(false), { md: true }), U.btn(o.btn || t("adr.btn.go"), "primary", () => { res = true; d.close(true); }, { md: true })]);
    });
  }
  // «Приём»: список адресов того же вида после выбора (все, что есть на сервере, — {all: true})
  function setIn(r, code) {
    const v6 = isV6(r.ip), rows = v6 ? rows6() : rows4();
    const now = rows.filter((x) => inCode(x) !== "off").map((x) => x.ip);
    const want = code === "off" ? now.filter((ip) => ip !== r.ip) : now.indexOf(r.ip) < 0 ? now.concat([r.ip]) : now;
    const here = rows.filter((x) => !gone(x)).map((x) => x.ip);
    const all = here.length && here.every((ip) => want.indexOf(ip) >= 0) && want.every((ip) => here.indexOf(ip) >= 0);
    const body = all ? { all: true } : v6 && !want.length ? { none: true } : { ips: want };
    const offMv = { a: r.ip, ax: "on", as: t("adr.mv.nowIn"), b: r.ip, bx: "", bs: t("adr.mv.thenOff") };
    const inW = (then) => (inCode(r) === code ? Promise.resolve(true) : write({ call: v6 ? K.API.addresses.in6 : K.API.addresses.in, body,
      title: t(code === "off" ? "adr.dlg.off" : "adr.dlg.on", { ip: r.ip }), mv: code === "off" ? offMv : null,
      items: code === "off" ? [t("adr.dlg.ports", { ip: r.ip }), t("adr.dlg.files")] : [], btn: t(code === "off" ? "adr.btn.off" : "adr.btn.go"),
      noTitle: t(code === "off" ? "adr.dlg.no" : "adr.dlg.noAny", { ip: r.ip }) }).then((ok) => (ok && then ? then() : ok)));
    if (!v6) return inW();
    const g = give6().ip;
    // IPv6: «принимать и выдавать» — приём и выдача; «только принимать» с выдачи — снять выдачу
    if (code === "accept_give") return inCode(r) === "off" ? inW(() => setGive(r.ip, r.ip)) : setGive(r.ip, r.ip);
    if (code === "reserve" && g === r.ip) return setGive(null, r.ip);
    return inW();
  }
  function setGive(ip6, shown) {
    const on = !!ip6;
    return write({ call: K.API.addresses.give, body: { ip6: ip6 || null }, title: t(on ? "adr.dlg.give" : "adr.dlg.giveOff", { ip: shown }), tone: "accent", icon: "arrow-rotate-right",
      mv: { a: on ? "IPv4" : "IPv4 + IPv6", ax: "on", as: t(on ? "adr.mv.v4" : "adr.mv.v46now"), b: on ? "IPv4 + IPv6" : "IPv4", bx: "on", bs: t(on ? "adr.mv.v46" : "adr.mv.v46off") },
      btn: t(on ? "adr.btn.give" : "adr.btn.giveOff"), q: false });
  }
  // «Для выхода: нет» на адресе, с которого что-то идёт: сервер переносит на «напрямую» (если он «для выхода») или
  // на первый другой адрес «для выхода» с путём наружу — панель называет его заранее в «было → станет»
  function setOut(r, on) {
    const used = usedBy(r.ip), d = directIp();
    const others = rows4().filter((x) => x.ip !== r.ip && outOk(x) && a4(x.ip).route_ok !== false).map((x) => x.ip);
    const to = d && d !== r.ip && others.indexOf(d) >= 0 ? d : others[0] || "";
    return write({ call: K.API.addresses.out, body: { ip: r.ip, on }, title: t(on ? "adr.dlg.on" : "adr.dlg.outOff", { ip: r.ip }), tone: "accent", icon: "arrow-right-arrow-left",
      mv: !on && to ? { a: r.ip, ax: "on", as: t("adr.mv.now", { x: used.length ? shortList(used) : t("adr.mv.nothing") }), b: to, bx: "on", bs: t("adr.mv.then", { x: used.length ? shortList(used) : t("adr.mv.nothing") }) } : null,
      btn: t("adr.btn.move"), q: false });
  }
  const setDirect = (ip, undo) => write({ call: K.API.addresses.direct, body: { ip }, title: t("adr.oDirect"), undo });
  const setExit = (x, ip, undo) => write({ call: K.API.addresses.exit, body: { id: x.id, ip }, title: exTitle(x), undo });
  // «Сбросить выбор» (решение координатора 6): подтверждение — только если сервер его просит (выдан IPv6: xray
  // перезапустится), иначе ссылка и тост с текстом сервера
  const resetRoles = () => write({ call: K.API.addresses.reset, body: {}, title: t("adr.dlg.reset"), tone: "accent", icon: "arrow-rotate-right", btn: t("adr.btn.reset"), q: false });
  // F12б: без yes сервер отвечает 409 need: "confirm" с планом (какие выходы с какого адреса, ноги AmneziaWG, что видят
  // сайты) — окно; разносить нечего — 200 с текстом (тост); адресов «для выхода» меньше двух — отказ словами
  const spread = () => write({ call: K.API.addresses.spread, body: {}, title: t("adr.dlg.spread"), tone: "accent", icon: "arrow-right-arrow-left", btn: t("adr.btn.spread"), q: false,
    kindOf: (res) => (arr(res.moves).length ? "success" : "warning") });
  // смена префикса: роль приёма IPv6 и выдача — со старого адреса на новый (приём, затем выдача)
  function moveRole(p) {
    const s = mip().in6 || {}, inOld = (ip) => net64(ip) === p.old.toLowerCase();
    const list = arr(s.list), g = give6().ip;
    const step2 = () => (g && inOld(g) ? setGive(p.ip, p.ip) : Promise.resolve(true));
    if (!s.all && list.some(inOld)) {
      const want = list.filter((ip) => !inOld(ip)).concat(list.indexOf(p.ip) < 0 ? [p.ip] : []);
      return write({ call: K.API.addresses.in6, body: { ips: want }, title: t("adr.dlg.move", { ip: p.ip }), btn: t("adr.btn.go") }).then((ok) => (ok ? step2() : ok));
    }
    return step2();
  }

  // ── часть ────────────────────────────────────────────────────────────────────────────────────────────────────────
  // перерисовка «Настроек» (смена ширины, данные ядра) строит часть заново: раскрытая строка и адрес во весь экран
  // остаются (сбрасывает их уход из части — leave), прочитанное рисуется сразу, свежее — следом
  function build(box) {
    if (S.unsub) S.unsub();
    S.host = box;
    S.unsub = K.alertSub((e) => { if (e && e.kind === "ip6_prefix_changed") { S.ev = e; reload(false); } });
    paint();
    reload(false);
  }
  function leave() {
    if (S.unsub) { S.unsub(); S.unsub = null; }
    if (fitRO) { fitRO.disconnect(); fitRO = null; }
    S.host = null; S.det = ""; S.open = ""; S.seq++; S.busy = false;
  }
  K.part("addresses", {
    build, leave,
    phoneDetail: () => !!S.det && K.isPhone(),
    // строка телефона «4 IPv4 · 2 IPv6» — тем же счётом, что группы «Адресов» (М1 = Д1): адрес, которого на сервере нет,
    // считается; без хвоста «нет на сервере» (D-3315-view-adr-r2 М1: на узком экране обрежется первым, предупреждение —
    // у строки адреса)
    counts: () => (S.doc ? { v4: rows4().length, v6: rows6().length } : null),
    prefetch: () => { if (S.doc || S.asked) return; S.asked = true; reload(false); },
    pure: { net64, pfxOf, shortIps, v6groups, v6str, schPick },
  });
})();

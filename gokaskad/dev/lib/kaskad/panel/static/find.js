/* Kaskad 3 — поиск по панели «как в Настройках iPhone» (3.3.6, BACKLOG-3.3.6 п. 3): чистая модель, без DOM.

   Вид — окно gx-find и выдача gx-cmdk (nav2 r1, кадр Н2; UI-A2), логика — своя (k3/handoff/RESEARCH-panel-custom-search.md §5):
     KaskadFind.norm(s)            нижний регистр, «ё = е», знаки препинания и дефисы — пробелом;
     KaskadFind.model(recs, q, o)  группы найденного по запросу: [{id, best, rows: [{rec, score}], more, stuck}] и total;
     KaskadFind.hits(text, q)      где в подписи совпало ([[начало, конец]]) — для <mark>;
     KaskadFind.recentPush(list, id, max)  «Недавние»: id первым, без повторов, не больше max.
     KaskadFind.portWords(list, pre)       слова по портам («443», «порт 443», «port 443») для записей объектов.
   Запись: {id, kind, label, words: [синонимы и подпись другого языка], sub, hint, path: [..], href, group?}. kind:
   person | device | exit | proto | forward (объекты по уже загруженным данным) | action | card | field | section |
   part | soon (части разделов nav2 и будущие части — группа «Части разделов»).
   Ранжирование: точная подпись выше всего, затем запрос, равный синониму, начало подписи выше начала слова, слово —
   выше части слова; подпись выше синонимов, синонимы выше строки под подписью, подсказка ниже, путь (раздел,
   карточка) — последним. Каждое слово запроса должно где-то совпасть. Есть совпадения с начала слова — совпавшие
   только частью слова («порт» в «экспорт», «ключ» в «выключить») не показываются. Группы — по лучшему в группе, при равенстве — объекты, действия, настройки, части разделов, разделы.
   Файл грузит index.html до app.js; при загрузке только объявляет функции (тест модели — tests/test_336_find.py). */
"use strict";

(() => {
  const GROUP_OF = { person: "people", device: "people", exit: "exits", proto: "inbounds", forward: "forwards", action: "actions", card: "settings", field: "settings",
    part: "parts", soon: "parts", section: "sections" };
  const ORDER = ["people", "exits", "inbounds", "forwards", "actions", "settings", "parts", "sections"];
  const W = { label: 1, words: 0.7, sub: 0.6, hint: 0.4, path: 0.25 };
  const PER = 8;                                   // строк на группу, остальное — «ещё N»
  const MAX = 60;                                  // всего строк, даже если группу раскрыли

  const SEP = "\\s«»\"'`’‘“”„()[\\]{}.,:;!?…—–\\-_/\\\\|+*#@&=<>~^%$";       // разделители слов: одни у norm и hits
  const norm = (s) => String(s === null || s === undefined ? "" : s).toLowerCase().replace(/ё/g, "е")
    .replace(new RegExp("[" + SEP + "]+", "g"), " ").trim();
  const words = (q) => norm(q).split(" ").filter(Boolean);

  // Насколько слово запроса tk совпало с текстом x (уже norm): 100 — весь текст, 80 — начало, 60 — начало слова,
  // 30 — часть слова (одна буква — только с начала слова)
  function hit(x, tk) {
    if (!x) return 0;
    if (x === tk) return 100;
    if (x.indexOf(tk) === 0) return 80;
    if ((" " + x).indexOf(" " + tk) >= 0) return 60;
    return tk.length > 1 && x.indexOf(tk) >= 0 ? 30 : 0;
  }
  function prep(r) {
    if (r._n) return r._n;
    const list = (a) => (Array.isArray(a) ? a : a ? [a] : []).map(norm).filter(Boolean);
    r._n = { label: norm(r.label), words: list(r.words), sub: norm(r.sub), hint: norm(r.hint), path: list(r.path).join(" ") };
    return r._n;
  }
  // r.weak — какое-то слово запроса совпало только частью слова (нигде — с начала слова)
  function score(r, toks, phrase) {
    const n = prep(r);
    let sum = 0;
    r._weak = false;
    for (const tk of toks) {
      const lv = [hit(n.label, tk), hit(n.sub, tk), hit(n.hint, tk), hit(n.path, tk)].concat(n.words.map((x) => hit(x, tk)));
      let best = W.label * lv[0];
      n.words.forEach((x, i) => { best = Math.max(best, W.words * lv[4 + i]); });
      best = Math.max(best, W.sub * lv[1], W.hint * lv[2], W.path * lv[3]);
      if (!best) return 0;
      if (Math.max.apply(null, lv) < 60) r._weak = true;
      sum += best;
    }
    let s = sum / toks.length;
    // вся фраза: точная подпись — выше всего, подпись с фразы — следом, фраза внутри подписи или синонима
    if (n.label === phrase) s += 60;
    else if (n.words.indexOf(phrase) >= 0) s += 40;            // запрос — ровно синоним («пароль», «бэкап»)
    else if (n.label.indexOf(phrase) === 0) s += 25;
    else if (toks.length > 1 && (n.label.indexOf(phrase) >= 0 || n.words.some((x) => x.indexOf(phrase) >= 0))) s += 15;
    return s;
  }
  // группа записи — по виду; запись может назвать группу сама (r.group из ORDER): карточка, которая и есть часть раздела
  const groupOf = (r) => (r.group && ORDER.indexOf(r.group) >= 0 ? r.group : GROUP_OF[r.kind] || "sections");
  // o.open — {группа: true} раскрытые «ещё N»; o.per — строк на группу
  function model(recs, q, o) {
    o = o || {};
    const toks = words(q);
    const per = o.per || PER;
    if (!toks.length) return { q: "", groups: [], total: 0 };
    const phrase = toks.join(" ");
    const byG = Object.create(null);
    let total = 0;
    const found = [];
    (recs || []).forEach((r, i) => {
      if (!r || !r.label) return;
      const s = score(r, toks, phrase);
      if (s) found.push({ rec: r, score: s, i, weak: r._weak });
    });
    const strong = found.some((x) => !x.weak);           // есть совпадения с начала слова — без «части слова» (П-3)
    found.forEach((x) => {
      if (strong && x.weak) return;
      total += 1;
      const g = groupOf(x.rec);
      (byG[g] = byG[g] || []).push({ rec: x.rec, score: x.score, i: x.i });
    });
    const groups = Object.keys(byG).map((id) => {
      const rows = byG[id].sort((a, b) => b.score - a.score || a.i - b.i);
      return { id, best: rows[0].score, rows };
    }).sort((a, b) => b.best - a.best || ORDER.indexOf(a.id) - ORDER.indexOf(b.id));
    // REV-336-search З-5: у каждой группы — свои первые per строк всегда; раскрытая «ещё N» добирает из общего запаса
    // (MAX минус эти строки) и не отнимает строк у других групп. Раскрыть больше нельзя (раскрыта или запас кончился) —
    // stuck: вместо «Ещё N» — «уточните запрос»
    // (раскрытые — от меньшего остатка к большему: короткая группа раскрывается целиком)
    const alloc = (isOpen) => {
      const cap = groups.map((g) => Math.min(per, g.rows.length));
      let left = Math.max(0, MAX - cap.reduce((a, b) => a + b, 0));
      groups.map((g, i) => i).filter((i) => isOpen(groups[i])).sort((a, b) => (groups[a].rows.length - cap[a]) - (groups[b].rows.length - cap[b]))
        .forEach((i) => { const add = Math.min(groups[i].rows.length - cap[i], left); cap[i] += add; left -= add; });
      return cap;
    };
    const opened = (g) => !!(o.open && o.open[g.id]);
    const cap = alloc(opened);
    groups.forEach((g, i) => {
      g.more = g.rows.length - cap[i];
      // раскрыть нельзя: уже раскрыта или раскрытие ничего не добавит
      g.stuck = g.more > 0 && (opened(g) || alloc((x) => x === g || opened(x))[i] === cap[i]);
    });
    groups.forEach((g, i) => { g.rows = g.rows.slice(0, cap[i]).map((x) => ({ rec: x.rec, score: x.score })); });
    return { q: phrase, groups: groups.filter((g) => g.rows.length || g.more), total };
  }
  // Совпадения в исходной подписи: для каждого слова запроса — первое начало слова, иначе первое вхождение
  function hits(text, q) {
    const src = String(text || "");
    const low = src.toLowerCase().replace(/ё/g, "е");
    if (low.length !== src.length) return [];
    const out = [];
    words(q).forEach((tk) => {
      let at = -1;
      const re = new RegExp("[^" + SEP + "]+", "g");
      let m;
      while ((m = re.exec(low))) { if (low.indexOf(tk, m.index) === m.index) { at = m.index; break; } }
      if (at < 0 && tk.length > 1) at = low.indexOf(tk);
      if (at >= 0) out.push([at, at + tk.length]);
    });
    out.sort((a, b) => a[0] - b[0]);
    const merged = [];
    out.forEach((r) => { const last = merged[merged.length - 1]; if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]); else merged.push(r.slice()); });
    return merged;
  }
  function recentPush(list, id, max) {
    const out = [id].concat((Array.isArray(list) ? list : []).filter((x) => typeof x === "string" && x && x !== id));
    return out.slice(0, max || 8);
  }
  // Слова поиска по портам объекта (REV-T-336 Н-5): «443/tcp», «host:443», «[::1]:443», 443 → «443», «порт 443», «port 443»
  // (pre — слово «порт» обоих языков). IPv6 без скобок и адрес без порта портов не дают
  function portWords(list, pre) {
    const out = [];
    (Array.isArray(list) ? list : [list]).forEach((x) => {
      const v = String(x === null || x === undefined ? "" : x).trim();
      const m = /^(\d{1,5})(?:\/[a-z]+)?$/i.exec(v) || /^(?:\[[^\]]*\]|[^:\[\]]+):(\d{1,5})$/.exec(v);
      if (!m || Number(m[1]) < 1 || Number(m[1]) > 65535) return;
      const n = String(Number(m[1]));
      [n].concat((pre || []).map((w) => w + " " + n)).forEach((w) => { if (out.indexOf(w) < 0) out.push(w); });
    });
    return out;
  }
  window.KaskadFind = Object.freeze({ norm, model, hits, recentPush, groupOf, ORDER, portWords });
})();

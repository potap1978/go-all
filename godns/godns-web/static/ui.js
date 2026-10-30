// Кирпичики «зелёного терминала»: безопасная сборка разметки, форматирование,
// компоненты. Ни одной зависимости, нативный ES-модуль, работает без сборки.
//
// ГЛАВНОЕ ПРАВИЛО, которое нельзя нарушать при любой смене оформления:
// данные попадают в разметку ТОЛЬКО через h`` или esc() — тег-шаблон экранирует
// каждую подстановку сам. Если нужен уже готовый HTML (собранный тем же h``),
// его оборачивают в raw(). Так XSS невозможен по построению, а не по внимательности.
import { t } from './i18n.js';

export const esc = (s) => String(s == null ? '' : s)
  .replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

class Raw { constructor(v) { this.v = v; } toString() { return this.v; } }
/** Пометить строку как готовую разметку — её не будут экранировать. */
export const raw = (v) => new Raw(v);

/** Тег-шаблон: h`<b>${имя}</b>` — имя экранируется, raw(...) и массивы вставляются как есть. */
export function h(strings, ...vals) {
  let out = strings[0];
  for (let i = 0; i < vals.length; i++) {
    const v = vals[i];
    out += (v instanceof Raw ? v.v
      : Array.isArray(v) ? v.map((x) => (x instanceof Raw ? x.v : esc(x))).join('')
        : esc(v)) + strings[i + 1];
  }
  return raw(out);
}

// ──────────────────────────── форматирование ────────────────────────────────
export const num = (n) => (n == null ? '—' : Number(n).toLocaleString('ru-RU'));
export const short = (n) => {
  n = Number(n) || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace('.0', '') + 'M';
  if (n >= 1e4) return Math.round(n / 1e3) + t('ui_unit_thousand');
  return String(n);
};
export const pct = (v) => (v == null ? '—' : Math.round(v) + '%');
/** Время может прийти как epoch-число (админ-API) или строка ISO (/v1). */
// Параметр называется ts, а не t: `t` — импортированная функция строк, и
// параметр с тем же именем затенял её внутри функции. Вызов t('ui_unit_sec')
// оказывался вызовом… отметки времени.
export const tsec = (ts) => (ts == null || ts === '' ? null
  : typeof ts === 'number' ? ts : (Date.parse(ts) / 1000 || null));
export const ago = (ts) => {
  const s = tsec(ts); if (!s) return '—';
  const d = Date.now() / 1000 - s;
  if (d < 60) return Math.max(0, Math.round(d)) + ' ' + t('ui_unit_sec');
  if (d < 3600) return Math.round(d / 60) + ' ' + t('ui_unit_min');
  if (d < 86400) return Math.round(d / 3600) + ' ' + t('ui_unit_hour');
  return Math.round(d / 86400) + ' ' + t('ui_unit_day');
};
export const hms = (s) => {
  s = Math.max(0, Math.round(s || 0));
  const hh = Math.floor(s / 3600); const mm = Math.floor((s % 3600) / 60); const ss = s % 60;
  return hh ? `${hh}:${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`
    : `${mm}:${String(ss).padStart(2, '0')}`;
};
export const plural = (n, one, few, many) => {
  const d = n % 100; const e = n % 10;
  return d > 10 && d < 20 ? many : e === 1 ? one : e >= 2 && e <= 4 ? few : many;
};
export const dateLabel = (ts, step) => {
  const d = new Date((tsec(ts) || 0) * 1000);
  return step >= 86400
    ? d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })
    : d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
};

// ──────────────────────────── словари продукта ──────────────────────────────
// Единственное продуктовое место в файле. Набор состояний меняется от продукта
// к продукту, ЦВЕТА И ИХ ПОРЯДОК — нет: зелёный «всё хорошо» → салатовый
// «с потерями» → жёлтый «сложности» → оранжевый «почти не работает» →
// красный «не работает». Новое состояние вешается на существующую ступень,
// а не заводит шестой цвет.
export const VERDICTS = ['ok', 'lossy', 'trouble', 'mostly_down', 'banned', 'closed', 'unknown'];
export const VERDICT_COLOR = {
  ok: 'var(--ok)', lossy: 'var(--lossy)', trouble: 'var(--warn)', mostly_down: 'var(--warn2)',
  banned: 'var(--bad)', closed: 'var(--closed)', unknown: 'var(--dim)',
};
export const STATE_COLOR = {
  open: 'var(--ok)', no_ssh: 'var(--cyan)', dpi: 'var(--warn)', refused: 'var(--closed)',
  timeout: 'var(--bad)', unreach: 'var(--warn2)', error: 'var(--dim)', no_answer: 'var(--dim)',
};
/** Цвет по доле удачных: зелёный → жёлтый → оранжевый → красный. */
export const okColor = (p) => (p == null ? 'var(--dim)'
  : p >= 80 ? 'var(--ok)' : p >= 50 ? 'var(--lossy)' : p >= 30 ? 'var(--warn)' : 'var(--bad)');

// ──────────────────────────── компоненты ────────────────────────────────────
export const badge = (text, kind = '') => h`<span class="badge ${kind ? raw('b-' + esc(kind)) : ''}">${text}</span>`;

export const kpi = (items) => h`<div class="kpi">${items.filter(Boolean).map((it) => h`
  <div class="kpi-card"${it.title ? raw(` title="${esc(it.title)}"`) : ''}>
    <div class="kpi-n"${it.color ? raw(` style="color:${esc(it.color)}"`) : ''}>${it.value}</div>
    <div class="kpi-l">${it.label}</div>
    ${it.hint ? h`<div class="kpi-h">${it.hint}</div>` : ''}
  </div>`)}</div>`;

/** Таблица: cols = [{key, title, cell?(row), num?, wide?}]. На телефоне строки
 *  превращаются в карточки, поэтому каждой ячейке нужна подпись (data-l). */
export function table(cols, rows, opts = {}) {
  // класс собираем одной строкой: два отдельных атрибута class браузер
  // склеивать не станет — второй просто пропадёт
  const tdCls = (c) => [c.num ? 'num' : '', c.wide ? 'cut' : ''].filter(Boolean).join(' ');
  // rowAttr отдаёт объект {имя: значение}, а не готовую строку: атрибуты собирает
  // сама таблица через h``, и данные строки не смогут вырваться из значения атрибута
  const rowA = (r) => {
    const at = opts.rowAttr && opts.rowAttr(r);
    return at ? Object.entries(at).map(([k, v]) => h` ${k}="${v}"`) : '';
  };
  const body = rows.length ? rows.map((r) => h`<tr${rowA(r)}>${cols.map((c) => h`
      <td data-l="${c.title}"${tdCls(c) ? h` class="${tdCls(c)}"` : ''}>${c.cell ? c.cell(r) : r[c.key]}</td>`)}</tr>`)
    : h`<tr><td class="empty" colspan="${cols.length}">${opts.empty || t('ui_no_data')}</td></tr>`;
  return h`<div class="tbl"><table>
    <thead><tr>${cols.map((c) => h`<th${c.num ? raw(' class="num"') : ''}>${c.title}</th>`)}</tr></thead>
    <tbody>${body}</tbody></table></div>`;
}

/** Полоса доли: показывает и число, и величину одним взглядом. */
export const bar = (value, max, color) => h`<div class="bar"><i style="width:${
  Math.max(0, Math.min(100, max ? (100 * value) / max : 0))}%;background:${color || 'var(--ok)'}"></i></div>`;

/** Составная полоса из состояний/вердиктов: {open: 20, timeout: 5}. */
export function mixbar(mix, colors) {
  const total = Object.values(mix || {}).reduce((a, b) => a + b, 0);
  if (!total) return h`<div class="mixbar empty"></div>`;
  const parts = Object.entries(mix).sort((a, b) => b[1] - a[1]);
  return h`<div class="mixbar" title="${parts.map(([k, v]) => `${k}: ${v}`).join(', ')}">${
    parts.map(([k, v]) => h`<i style="width:${(100 * v) / total}%;background:${(colors || {})[k] || 'var(--dim)'}"></i>`)}</div>`;
}

/** Спарклайн: путь по точкам, без библиотек. values = массив чисел. */
export function spark(values, opts = {}) {
  const v = (values || []).map((x) => Number(x) || 0);
  if (v.length < 2) return h`<div class="spark empty"></div>`;
  const w = opts.width || 240; const ht = opts.height || 40;
  const max = Math.max(...v, 1); const min = Math.min(...v, 0);
  const span = max - min || 1;
  const x = (i) => (i * w) / (v.length - 1);
  const y = (n) => ht - ((n - min) / span) * (ht - 4) - 2;
  const line = v.map((n, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(n).toFixed(1)}`).join(' ');
  const area = `${line} L${w},${ht} L0,${ht} Z`;
  const c = opts.color || 'var(--ok)';
  return h`<svg class="spark" viewBox="0 0 ${w} ${ht}" preserveAspectRatio="none" aria-hidden="true">
    <path d="${raw(area)}" fill="${c}" fill-opacity=".12"/>
    <path d="${raw(line)}" fill="none" stroke="${c}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
  </svg>`;
}

/** Столбчатая диаграмма с накоплением: bars = [{label, mix:{k:v}}].
 *
 *  Подсказка со значениями — разметкой, а не атрибутом title: браузерная
 *  всплывашка появляется через секунду, рисуется системным шрифтом и не умеет
 *  показать цвет вердикта. А цифры под курсором тут и есть смысл графика.
 *  Обработчиков не вешаем совсем: раздел перерисовывается по таймеру, и
 *  слушатели копились бы с каждым тактом. opts.labels — подписи вердиктов. */
export function stackedBars(bars, colors, opts = {}) {
  const maxTotal = Math.max(1, ...bars.map((b) => Object.values(b.mix).reduce((a, x) => a + x, 0)));
  const labels = opts.labels || {};
  const order = (mix) => Object.entries(mix).sort((a, c2) => (opts.order
    ? opts.order.indexOf(a[0]) - opts.order.indexOf(c2[0]) : c2[1] - a[1]));
  return h`<div class="bars" style="--bars:${bars.length}">${bars.map((b) => {
    const total = Object.values(b.mix).reduce((a, x) => a + x, 0);
    const parts = order(b.mix);
    // подпись для голосового доступа и поиска по странице — тем же текстом
    const flat = `${b.label}: ${parts.map(([k, v]) => `${labels[k] || k} ${v}`).join(', ')}`;
    return h`<div class="bars-col" tabindex="0" aria-label="${flat}">
      <div class="bars-pop"><b class="pop-t">${b.label}</b>${parts.map(([k, v]) => h`
        <span><i style="background:${(colors || {})[k] || 'var(--dim)'}"></i>${labels[k] || k}<b>${v}</b></span>`)}
        <span class="pop-all">всего<b>${total}</b></span></div>
      <div class="bars-stack" style="height:${(100 * total) / maxTotal}%">${
  parts.map(([k, v]) => h`<i style="height:${(100 * v) / (total || 1)}%;background:${(colors || {})[k] || 'var(--dim)'}"></i>`)}</div>
      <div class="bars-x">${b.label}</div></div>`;
  })}</div>`;
}

/** Тепловая матрица: rows × cols со значением 0..100. */
export function heatmap(rowNames, colNames, get, opts = {}) {
  if (!rowNames.length || !colNames.length) return h`<div class="empty">${opts.empty || t('ui_no_data')}</div>`;
  return h`<div class="tbl heat"><table>
    <thead><tr><th></th>${colNames.map((c) => h`<th class="num">${c}</th>`)}</tr></thead>
    <tbody>${rowNames.map((r) => h`<tr><td class="cut" data-l="">${r}</td>${colNames.map((c) => {
    const cell = get(r, c);
    if (!cell) return h`<td class="num heat-none" data-l="${c}">—</td>`;
    return h`<td class="num" data-l="${c}" style="background:${okColor(cell.pct)};--a:${
      Math.min(1, (cell.probes || 1) / (opts.strong || 50))}" title="${`${r} · порт ${c}: ${cell.probes} замеров, ${cell.pct}% удачных`}">${cell.pct}%</td>`;
  })}</tr>`)}</tbody></table></div>`;
}

export const section = (title, body, note) => h`<section class="block">
  <h2>${title}${note ? h`<span class="note">${note}</span>` : ''}</h2>${body}</section>`;

/** Подписи к цветам под графиком: {ok: 120, banned: 8} → «● доступ есть · 120».
 *  Без легенды столбики и полосы — просто цветные пятна. */
export const legendOf = (mix, colors, labels) => (Object.entries(mix || {})
  .sort((a, b) => b[1] - a[1])
  .map(([k, v]) => h`<span><i style="background:${(colors || {})[k] || 'var(--dim)'}"></i>${
  (labels || {})[k] || k} · ${num(v)}</span>`));

/** Переключатель значений: [{v, label}] — общий вид для всех фильтров. */
export const seg = (name, value, items) => h`<div class="seg" data-seg="${name}">${
  items.map((it) => h`<button type="button" class="${it.v === value ? 'on' : ''}" data-v="${it.v}">${it.label}</button>`)}</div>`;

export const toast = (text, kind = '') => {
  const el = document.createElement('div');
  el.className = 'toast' + (kind ? ' ' + kind : '');
  el.textContent = text;
  document.body.appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 300); }, 3200);
};

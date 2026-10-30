// Апстримы и политика — зеркало подменю «Апстримы»: выбрать набор, политику,
// замерить задержку, отчёт по живым апстримам, свой апстрим (DoT / DoH /
// обычный DNS). Фильтрующие помечены и по умолчанию не предлагаются:
// фильтрующий апстрим молча отдаёт NXDOMAIN, и это выглядит как сломанный
// туннель, а не как фильтр.
//
// Свой апстрим идёт тем же порядком, что SSH-экран custom_upstream_add:
// адрес → проба ДО записи → «добавить всё равно?» → запись → в набор.
// Правила живут на сервере (webui_custom_probe/_add): открытый DNS к
// публичному адресу без явного подтверждения не пробуется и не пишется —
// галочка здесь лишь передаёт ответ, а не решает.
import { h, raw, section, badge, table, toast, seg } from '../ui.js';
import { t } from '../i18n.js';
import { armBlock, wireSection, showTerm, termSlot, buttonJob } from './lib.js';

// checked/disabled/hidden как «сырые» атрибуты: h`` экранирует всё, а тут
// нужна именно разметка. Строки фиксированные, данных в них нет.
const raw0 = (s) => raw(s);

// Ровно те же две политики, что в SSH-мастере (godns.sh, шаг 3).
const POLICIES = [
  { v: 'roundrobin', label: t('ui_ups_policy_rr') },
  { v: 'fastest', label: t('ui_ups_policy_fastest') },
];

// Слово подтверждения удаления своего апстрима. Сервер принимает оба
// языка (CONFIRM_WORDS), отправляем то, на котором его набрал человек.
const WORDS = { curm: t('ui_ups_word_curm') };

// Протоколы своего апстрима и порты по умолчанию — те же, что в SSH.
const PROTOS = [
  { v: 'dot', label: t('ui_ups_cu_proto_dot') },
  { v: 'doh', label: t('ui_ups_cu_proto_doh') },
  { v: 'plain', label: t('ui_ups_cu_proto_plain') },
];
const DEF_PORT = { dot: '853', doh: '443', plain: '53' };
const PROTO_LBL = { dot: t('ui_ups_cu_lbl_dot'), doh: t('ui_ups_cu_lbl_doh'), plain: t('ui_ups_cu_lbl_plain') };

// Выбранное храним в модуле между перерисовками такта опроса; сбрасывается
// при заходе в раздел (load кладёт свежее из конфига, если человек не менял).
let picked = null;
let pickedPolicy = null;
let dirty = false;

// Черновик формы «Свой апстрим» живёт между перерисовками: ctx.refresh()
// после действий перерисовывает раздел целиком, и без этого форма
// пропадала бы вместе с набранным. probe — последний ответ пробы с ключом
// полей, для которых он получен; anyway — ключ, на котором человек уже
// видел «не ответил» и нажал «добавить» первый раз.
const freshDraft = () => ({
  proto: 'dot', host: '', port: '', path: '', name: '',
  enable: true, ack: false, probe: null, anyway: '',
});
let draft = freshDraft();
// Кандидат на удаление — как restoreName в backups.js.
let rmId = null;

// Ключ полей, от которых зависит проба. Имя в него не входит: его смена
// пробу не обесценивает.
const draftKey = () => [draft.proto, draft.host, draft.port,
  draft.proto === 'doh' ? draft.path : '', draft.ack ? 'ack' : ''].join('|');

// Разбор адреса — те же правила, что в godns.sh (custom_upstream_add):
// снять https://, для doh отделить путь после первого «/», затем
// [v6]:port · [v6] · голый v6 (два двоеточия и больше) · host:port · host.
// Возвращает {host, port, path} или null, если хоста нет вовсе. Что именно
// негодно в хосте, решает сервер — здесь только раскладка по полям.
export function parseAddress(proto, rawIn) {
  let s = String(rawIn || '').replace(/\s+/g, '');
  if (!s) return null;
  s = s.replace(/^https?:\/\//i, '');
  let path = '';
  if (proto === 'doh') {
    const i = s.indexOf('/');
    if (i >= 0) { path = s.slice(i); s = s.slice(0, i); }
  }
  let host = s;
  let port = '';
  let m;
  if ((m = s.match(/^\[([^\]]+)\]:([^:]*)$/))) { host = m[1]; port = m[2]; }
  else if ((m = s.match(/^\[([^\]]+)\]$/))) { host = m[1]; }
  else if ((s.match(/:/g) || []).length >= 2) { host = s; }
  else if ((m = s.match(/^([^:]+):([^:]*)$/))) { host = m[1]; port = m[2]; }
  if (!host) return null;
  return { host, port, path };
}

const latCell = (m) => {
  if (!m) return h`<span style="color:var(--faint)">${t('ui_ups_not_measured')}</span>`;
  if (m.error) return badge(m.error, 'banned');
  const warm = m.warm != null ? t('ui_ups_ms', m.warm) : '—';
  const cold = m.cold != null ? ` ${t('ui_ups_cold_paren', m.cold)}` : '';
  const part = m.probes && m.probes_ok != null && m.probes_ok < m.probes ? ` ${m.probes_ok}/${m.probes}` : '';
  return h`<span>${(m.degraded ? '~' : '') + warm}<span style="color:var(--faint)">${cold}${part}</span></span>`;
};

// Своя запись в колонке имени: имя, бейдж «свой», под ним протокол и
// адрес. Порт показываем настоящий (из addr), а не «853» из подписи
// протокола — свой DoT на 8853 иначе читался бы как стандартный.
const nameCell = (r) => (r.custom
  ? h`<div>${r.name} ${badge(t('ui_ups_cu_badge'), 'info')}</div>
      <div style="color:var(--faint);font-size:var(--fs-xs)">${PROTO_LBL[r.proto] || r.proto || ''} · ${r.addr || ''}</div>`
  : h`${r.name}`);

// ── Форма «Свой апстрим» ─────────────────────────────────────────────────
const hostPh = (p) => (p === 'doh' ? t('ui_ups_cu_host_ph_doh') : 'dns.example.org');

// Результат последней пробы — из draft.probe, чтобы пережить перерисовку.
const probeResult = () => {
  const p = draft.probe;
  if (!p) return '';
  const rows = [];
  if (p.error) {
    rows.push(h`<span style="color:var(--bad)">${p.error}</span>`);
    return h`<div style="display:grid;gap:6px;font-size:var(--fs-sm)">${rows}</div>`;
  }
  // Открытый DNS к публичному адресу: предупреждение и чекбокс — и когда
  // проба ещё не шла (needs_ack), и когда она уже прошла с подтверждением.
  if (p.needs_ack || p.private === false) {
    rows.push(h`<div style="color:var(--warn)">${t('ui_ups_cu_plain_public')}</div>`);
    rows.push(h`<label style="flex-direction:row;align-items:center;gap:8px;color:var(--fg)">
      <input type="checkbox" id="cu-ack"${draft.ack ? raw0(' checked') : ''}> ${t('ui_ups_cu_plain_ack')}</label>`);
  }
  if (!p.needs_ack) {
    rows.push(h`<div>${p.ok
      ? badge(t('ui_ups_cu_probe_ok', p.latency || '?'), 'ok')
      : badge(t('ui_ups_cu_probe_fail', p.latency || '?'), 'banned')}</div>`);
    if (p.ip && p.ip !== draft.host) {
      rows.push(h`<span style="color:var(--dim)">${t('ui_ups_cu_resolved', draft.host, p.ip)}</span>`);
    }
    if (p.private === true) rows.push(h`<span style="color:var(--dim)">${t('ui_ups_cu_plain_private')}</span>`);
  }
  return h`<div style="display:grid;gap:6px;font-size:var(--fs-sm)">${rows}</div>`;
};

const addLabel = () => (draft.probe && !draft.probe.error && !draft.probe.needs_ack
  && !draft.probe.ok && draft.anyway === draftKey()
  ? t('ui_ups_cu_btn_add_anyway') : t('ui_ups_cu_btn_add'));

const cuFormInner = () => {
  const p = draft.proto;
  return h`
    ${seg('cuproto', p, PROTOS)}
    <div class="row" style="align-items:flex-end;margin-top:10px">
      <label style="flex:1 1 240px">${t('ui_ups_cu_host')}<input id="cu-host" value="${draft.host}" placeholder="${hostPh(p)}" autocomplete="off" spellcheck="false"></label>
      <label>${t('ui_ups_cu_port')}<input id="cu-port" inputmode="numeric" value="${draft.port}" placeholder="${DEF_PORT[p]}" style="width:110px"></label>
      ${p === 'doh' ? h`<label style="flex:1 1 180px">${t('ui_ups_cu_path')}<input id="cu-path" value="${draft.path}" placeholder="/dns-query" autocomplete="off" spellcheck="false"></label>` : ''}
      <label style="flex:1 1 180px">${t('ui_ups_cu_name')}<input id="cu-name" value="${draft.name}" maxlength="40" autocomplete="off"></label>
    </div>
    <label style="flex-direction:row;align-items:center;gap:8px;margin-top:10px;color:var(--fg)">
      <input type="checkbox" id="cu-enable"${draft.enable && !dirty ? raw0(' checked') : ''}${dirty ? raw0(' disabled') : ''}> ${t('ui_ups_cu_enable')}${dirty ? h` <span style="color:var(--warn)">${t('ui_ups_cu_enable_dirty')}</span>` : ''}
    </label>
    <div class="row" style="align-items:center;margin-top:10px">
      <button class="btn" data-act="cu-probe">${t('ui_ups_cu_btn_probe')}</button>
      <button class="btn ok" data-act="cu-add">${addLabel()}</button>
    </div>
    <div id="cu-result" style="margin-top:8px"${draft.probe ? '' : raw0(' hidden')}>${probeResult()}</div>
    <div style="margin-top:8px;font-size:var(--fs-xs);color:var(--dim)">${t('ui_ups_cu_note')}</div>`;
};

// Перерисовать только карточку формы — таблица и терминальный слот остаются.
function paintForm(el) {
  const box = el.querySelector('#cu-form');
  if (box) box.innerHTML = String(cuFormInner());
}

const cuSection = (n, max) => section(t('ui_ups_cu_title'),
  n >= max
    ? h`<div class="card">${badge(t('ui_ups_cu_limit_reached', max), 'trouble')}</div>`
    : h`<div class="card" id="cu-form">${cuFormInner()}</div>`,
  t('ui_ups_cu_limit', n, max));

// Живая проба кандидата. Возвращает данные ответа или null.
async function cuProbe(ctx, el, btn) {
  const a = parseAddress(draft.proto, draft.host);
  if (!a) { toast(t('ui_ups_cu_need_host'), 'bad'); return null; }
  // Раскладываем «https://host:8443/path» по полям — как SSH-экран.
  draft.host = a.host;
  if (a.port) draft.port = a.port;
  if (a.path) draft.path = a.path;
  const key = draftKey();
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = old + ' …';
  const box = el.querySelector('#cu-result');
  if (box) {
    box.hidden = false;
    box.innerHTML = String(h`<span style="color:var(--dim)">${t('ui_ups_cu_probing')}</span>`);
  }
  try {
    const r = await ctx.api.post('/upstreams/custom/probe', {
      proto: draft.proto, host: draft.host, port: draft.port,
      path: draft.proto === 'doh' ? draft.path : '', ack_plain: draft.ack,
    });
    draft.probe = Object.assign({ key }, r.data || {});
  } catch (err) {
    draft.probe = { key, error: err.message };
    toast(err.message, 'bad');
  } finally {
    btn.disabled = false;
    btn.textContent = old;
    paintForm(el);
  }
  return draft.probe;
}

// Добавить: пробы не было или поля менялись — сначала проба (SSH всегда
// пробует до записи); не ответил — первый клик лишь меняет подпись на
// «добавить всё равно», и только второй отправляет запись.
async function cuAdd(ctx, el, btn) {
  let p = draft.probe;
  if (!p || p.error || p.key !== draftKey()) {
    p = await cuProbe(ctx, el, btn);
    if (!p || p.error) return;
    // проба перерисовала форму — прежняя кнопка отсоединена от DOM
    btn = el.querySelector('[data-act="cu-add"]') || btn;
  }
  if (p.needs_ack) return;   // предупреждение и чекбокс уже на экране
  const key = draftKey();
  if (!p.ok && draft.anyway !== key) {
    draft.anyway = key;
    paintForm(el);
    return;
  }
  btn.disabled = true;
  try {
    const enable = draft.enable && !dirty;
    const r = await ctx.api.post('/upstreams/custom', {
      proto: draft.proto, host: draft.host, port: draft.port,
      path: draft.proto === 'doh' ? draft.path : '',
      name: draft.name.trim(), enable, ack_plain: draft.ack,
    });
    showTerm(el, 'ups-out', (r.data && r.data.output) || t('ui_ups_cu_added_toast'));
    toast(t('ui_ups_cu_added_toast'), 'ok');
    draft = freshDraft();
    // Непринятые галочки живут дальше, если новую запись не включали:
    // человек их наотмечал, и терять их из-за чужого действия нельзя.
    if (enable) dirty = false;
    ctx.refresh();
  } catch (err) {
    toast(err.message, 'bad');
    const out = err.data && err.data.data && err.data.data.output;
    if (out) showTerm(el, 'ups-out', out);
  } finally { btn.disabled = false; }
}

export default {
  title: t('ui_ups_title'),

  async load({ api }) {
    const r = await api.get('/upstreams');
    const d = r.data;
    if (!dirty) {
      picked = new Set(d.current || []);
      pickedPolicy = d.policy || 'roundrobin';
    }
    return d;
  },

  note: (d) => t('ui_ups_note_selected', (d.current || []).length),

  render(el, d, ctx) {
    const cat = d.catalog || [];
    const nCustom = cat.filter((r) => r.custom).length;
    const max = Number(d.custom_max) || 8;
    el.innerHTML = String(h`
      ${section(t('ui_ups_set_title'), h`<div class="card">
        ${table([
    { key: 'on', title: '', cell: (r) => h`<input type="checkbox" data-pick="${r.id}"${picked.has(r.id) ? raw0(' checked') : ''}>` },
    { key: 'name', title: t('ui_ups_col_resolver'), wide: true, cell: nameCell },
    { key: 'filter', title: t('ui_ups_col_filter'), cell: (r) => (r.filter === 'none' ? badge(t('ui_ups_filter_none'), 'ok') : badge(r.filter_label || r.filter, 'closed')) },
    { key: 'region', title: t('ui_ups_col_region'), cell: (r) => h`<span style="color:var(--dim)">${r.region || ''}</span>` },
    { key: 'lat', title: t('ui_ups_col_latency'), num: true, cell: (r) => latCell(d.measure && d.measure[r.id]) },
    // Без заголовка, как act в backups.js: кнопка только у своих строк.
    { key: 'act', title: '', cell: (r) => (r.custom ? h`<button class="btn no" data-cu-rm="${r.id}">${t('ui_ups_cu_btn_remove')}</button>` : '') },
  ], cat, { empty: t('ui_ups_catalog_empty') })}
        <div class="row" style="margin-top:12px;align-items:center">
          ${seg('policy', pickedPolicy, POLICIES)}
          <span class="sp"></span>
          <button class="btn" data-act="measure">${t('ui_ups_btn_measure')}</button>
          <button class="btn ok" data-act="apply">${t('ui_ups_btn_apply')}</button>
        </div>
        <div style="margin-top:8px;font-size:var(--fs-xs);color:var(--dim)">${t('ui_ups_lat_hint')}</div>
      </div>`, t('ui_ups_set_note'))}
      ${armBlock('curm', WORDS.curm, '', t('ui_ups_cu_rm_go'))}

      ${cuSection(nCustom, max)}

      ${section(t('ui_ups_report_title'), h`<div class="row">
        <button class="btn" data-act="report">${t('ui_ups_btn_report')}</button>
      </div>`, t('ui_ups_report_note'))}
      ${termSlot('ups-out')}
    `);

    wireSection(el, WORDS, async (id) => {
      if (id !== 'curm' || !rmId) return;
      try {
        const r = await ctx.api.post('/upstreams/custom/remove', { id: rmId, confirm: WORDS.curm });
        showTerm(el, 'ups-out', (r.data && r.data.output) || t('ui_ups_cu_removed_toast'));
        toast(t('ui_ups_cu_removed_toast'), 'ok');
        picked.delete(rmId);
        rmId = null;
        ctx.refresh();
      } catch (err) { toast(err.message, 'bad'); }
    }, async (e) => {
      // сегмент политики — свой клик (seg из ui.js — просто кнопки)
      const p = e.target.closest('.seg[data-seg="policy"] button[data-v]');
      if (p) {
        pickedPolicy = p.dataset.v;
        dirty = true;
        el.querySelectorAll('.seg[data-seg="policy"] button').forEach((b) => b.classList.toggle('on', b.dataset.v === pickedPolicy));
        paintForm(el);
        return;
      }
      const pr = e.target.closest('.seg[data-seg="cuproto"] button[data-v]');
      if (pr) {
        if (pr.dataset.v !== draft.proto) {
          draft.proto = pr.dataset.v;
          draft.probe = null;
          draft.anyway = '';
          paintForm(el);
        }
        return;
      }
      const rb = e.target.closest('[data-cu-rm]');
      if (rb) {
        rmId = rb.dataset.cuRm;
        const row = cat.find((r) => r.id === rmId) || {};
        const blk = el.querySelector('#arm-curm');
        if (blk) {
          blk.hidden = false;
          const hint = blk.querySelector('div');
          if (hint) hint.textContent = t('ui_ups_cu_rm_hint', row.name || rmId, PROTO_LBL[row.proto] || row.proto || '', row.addr || '');
          const go = blk.querySelector('[data-arm-go]');
          if (go) go.disabled = true;
          const i = blk.querySelector('input');
          if (i) { i.value = ''; i.focus(); }
        }
        return;
      }
      const b = e.target.closest('[data-act]');
      if (!b) return;
      if (b.dataset.act === 'apply') {
        if (!picked.size) { toast(t('ui_ups_need_one'), 'bad'); return; }
        b.disabled = true;
        try {
          const r = await ctx.api.post('/upstreams', { ids: [...picked], policy: pickedPolicy });
          showTerm(el, 'ups-out', (r.data && r.data.output) || t('ui_ups_applied'));
          dirty = false;
          toast(t('ui_ups_applied_toast'), 'ok');
          ctx.refresh();
        } catch (err) {
          toast(err.message, 'bad');
        } finally { b.disabled = false; }
      } else if (b.dataset.act === 'measure') {
        try {
          await buttonJob(ctx, b, el, 'ups-out', '/upstreams/measure', {});
          ctx.refresh();
        } catch (err) { /* текст уже в слоте */ }
      } else if (b.dataset.act === 'report') {
        b.disabled = true;
        showTerm(el, 'ups-out', t('ui_ups_report_wait'));
        try {
          const r = await ctx.api.post('/upstreams/report', {});
          showTerm(el, 'ups-out', (r.data && r.data.output) || '—', { tall: true });
        } catch (err) {
          showTerm(el, 'ups-out', t('ui_ups_error', err.message));
        } finally { b.disabled = false; }
      } else if (b.dataset.act === 'cu-probe') {
        await cuProbe(ctx, el, b);
      } else if (b.dataset.act === 'cu-add') {
        await cuAdd(ctx, el, b);
      }
    });

    // Поля формы — в черновик на каждое нажатие: перерисовка раздела
    // (такт опроса, refresh после действий) берёт значения оттуда.
    // wireSection уже занял el.oninput под слово подтверждения — оборачиваем,
    // а не вешаем второй обработчик (addEventListener копил бы их с каждой
    // перерисовкой, см. lib.js).
    const armInput = el.oninput;
    el.oninput = (e) => {
      const f = e.target;
      if (f.id === 'cu-host') draft.host = f.value;
      else if (f.id === 'cu-port') draft.port = f.value;
      else if (f.id === 'cu-path') draft.path = f.value;
      else if (f.id === 'cu-name') draft.name = f.value;
      if (f.id === 'cu-host' || f.id === 'cu-port' || f.id === 'cu-path') {
        const add = el.querySelector('[data-act="cu-add"]');
        if (add) add.textContent = addLabel();
      }
      if (armInput) armInput(e);
    };

    el.onchange = (e) => {
      const c = e.target.closest('input[data-pick]');
      if (c) {
        dirty = true;
        if (c.checked) picked.add(c.dataset.pick); else picked.delete(c.dataset.pick);
        // Набор изменился — «сразу включить» теперь врало бы: сервер
        // применил бы сохранённый набор плюс новый id, выбросив галочки.
        paintForm(el);
        return;
      }
      if (e.target.id === 'cu-enable') draft.enable = e.target.checked;
      if (e.target.id === 'cu-ack') {
        draft.ack = e.target.checked;
        draft.anyway = '';
        const add = el.querySelector('[data-act="cu-add"]');
        if (add) add.textContent = addLabel();
      }
    };
  },
};

// Общие кирпичики разделов goDNS: вывод захваченного терминала, задания
// с опросом, подтверждение вводом слова. Всё собрано из компонентов ui.js —
// новых классов CSS здесь не заводится (ui.css не правится никогда).
import { h, raw } from '../ui.js';
import { t } from '../i18n.js';

// ── Терминальный вывод ──────────────────────────────────────────────────
// Захваченный вывод bash-функций (ANSI уже снят сервером). Стиль инлайном:
// это <pre> внутри .card, своего класса в системе нет и не нужно.
export const term = (text, opts = {}) => h`<pre style="margin:0;padding:2px 2px;
  font:400 var(--fs-sm)/1.55 var(--font);color:var(--fg);white-space:pre;
  overflow-x:auto;max-height:${opts.tall ? '560px' : '380px'};overflow-y:auto">${text || '—'}</pre>`;

// Пустой слот на месте будущего вывода.
export const termSlot = (id) => h`<div class="card" id="${id}" hidden></div>`;

export function showTerm(el, id, text, opts = {}) {
  const host = el.querySelector('#' + id);
  if (!host) return;
  host.hidden = false;
  host.innerHTML = String(term(text, opts));
}

// ── Значения-состояния goDNS ────────────────────────────────────────────
// Смысловая шкала та же, что в SSH-меню, чтобы не переучиваться:
// работает — зелёный; остановлена / скоро истечёт / есть непринятые
// изменения — жёлтый; выключено или истекло — красный; не настроено — серый.
export const SERVICE_BADGE = {
  running: [t('ui_lib_svc_running'), 'ok'],
  stopped: [t('ui_lib_svc_stopped'), 'trouble'],
  not_installed: [t('ui_lib_svc_not_installed'), 'unknown'],
};


// ISO-дата → человеческая: 2027-08-15 → 15.08.2027. Пустое — прочерк.
// Дата, а не только «сколько осталось»: число дней не отвечает на вопрос
// «а когда именно», и рядом с именем домена читается как срок домена.
export const humanDate = (iso) => {
  if (!iso || typeof iso !== 'string') return '';
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : iso;
};

// Дней до ISO-даты; null — если разобрать нечего.
export const daysUntil = (iso) => {
  const human = /^\d{4}-\d{2}-\d{2}$/.test(iso || '');
  if (!human) return null;
  const then = Date.parse(iso + 'T00:00:00Z');
  if (!Number.isFinite(then)) return null;
  return Math.floor((then - Date.now()) / 86400000);
};

export const certBadge = (days) => {
  if (days === null || days === undefined || days === '') return [t('ui_lib_cert_unknown'), 'unknown'];
  const n = Number(days);
  if (!Number.isFinite(n)) return [t('ui_lib_cert_unknown'), 'unknown'];
  if (n <= 0) return [t('ui_lib_cert_expired'), 'banned'];
  if (n <= 14) return [t('ui_lib_cert_expiring', n), 'trouble'];
  return [t('ui_lib_cert_days', n), 'ok'];
};


// ── График нагрузки ─────────────────────────────────────────────────────
// Терминал рисует ряд блочными символами — там это единственный способ, и
// он хорош. В панели тот же приём выглядит осколком терминала: глифы не
// масштабируются, строка уезжает в горизонтальную прокрутку, значение под
// курсором не прочитать. Поэтому здесь настоящий SVG по тем же числам
// (lib/stats.py, series): одна разбивка на корзины на оба представления,
// расходиться им нечем.
//
// Пропуск (служба или сервер не работали) рисуется НЕ отсутствием
// столбика — пустое место читалось бы как «ноль запросов», а это разные
// вещи, — а тусклой полосой во всю высоту. Простой видно глазом.
const pad2 = (n) => String(n).padStart(2, '0');
const hhmm = (sec) => {
  const d = new Date(sec * 1000);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
};

export const chart = (ser, opts = {}) => {
  if (!ser || !Array.isArray(ser.values) || !ser.values.length) return '';
  const vals = ser.values;
  const n = vals.length;
  const H = 44;                     // единицы viewBox, не пиксели
  const SLOT = 4;                   // 3 — столбик, 1 — просвет
  const W = n * SLOT;
  const top = Math.max(...vals.filter((v) => v !== null && v !== undefined), 0);

  const parts = vals.map((v, i) => {
    const x = i * SLOT;
    const t0 = ser.end - (n - i) * ser.step;
    if (v === null || v === undefined) {
      return `<rect x="${x}" y="0" width="${SLOT}" height="${H}" fill="var(--faint)" opacity="0.13">`
        + `<title>${hhmm(t0)} — ${t('ui_lib_chart_gap')}</title></rect>`;
    }
    // Ноль — это «запросов не было», и он обязан отличаться от пропуска:
    // засечка на базовой линии, а не пустота.
    const hgt = top > 0 && v > 0 ? Math.max(1.5, (v / top) * (H - 2)) : 1;
    const y = (H - hgt).toFixed(2);
    const op = v > 0 ? 0.85 : 0.35;
    return `<rect x="${x}" y="${y}" width="3" height="${hgt.toFixed(2)}" rx="0.6" `
      + `fill="var(--cyan)" opacity="${op}"><title>${hhmm(t0)} — ${v}</title></rect>`;
  });
  parts.push(`<rect x="0" y="${H - 0.4}" width="${W}" height="0.4" fill="var(--line-2)"/>`);

  const svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" `
    + `style="width:100%;height:${opts.height || 56}px;display:block">`
    + parts.join('') + '</svg>';

  const t0 = ser.end - n * ser.step;
  const mid = ser.end - Math.floor(n / 2) * ser.step;
  return h`<div style="min-width:0">
    <div class="row" style="justify-content:space-between;align-items:baseline;margin-bottom:5px;gap:8px">
      <span style="font-size:var(--fs-xs);color:var(--dim)">${opts.title || ''}${top > 0 ? ` · ${t('ui_lib_chart_max', top)}` : ''}</span>
      ${opts.meta ? h`<span style="font-size:var(--fs-xs);color:var(--fg)">${opts.meta}</span>` : ''}
    </div>
    <div style="position:relative">
      ${raw(svg)}
    </div>
    <div class="row" style="justify-content:space-between;margin-top:3px">
      <span style="font-size:var(--fs-2xs);color:var(--faint)">${hhmm(t0)}</span>
      <span style="font-size:var(--fs-2xs);color:var(--faint)">${hhmm(mid)}</span>
      <span style="font-size:var(--fs-2xs);color:var(--faint)">${hhmm(ser.end)}</span>
    </div>
  </div>`;
};

// ── Подтверждение вводом слова ──────────────────────────────────────────
// Модальных окон в системе нет осознанно: опасное действие подтверждается
// текстом в строке. Кнопка-триггер несёт data-arm="<id>"; блок с полем
// появляется рядом, действие выполняется только при точном совпадении слова.
export const armBlock = (id, word, hint, btnText) => h`<div class="card" id="arm-${id}" hidden>
  <div style="font-size:var(--fs-sm);color:var(--warn);margin-bottom:8px">${hint}</div>
  <div class="row" style="align-items:center">
    <label style="flex:1 1 220px">${t('ui_lib_arm_type', word)}
      <input data-arm-input="${id}" autocomplete="off" spellcheck="false" placeholder="${word}"></label>
    <button class="btn no" data-arm-go="${id}" disabled>${btnText}</button>
    <button class="btn" data-arm-cancel="${id}">${t('ui_lib_arm_cancel')}</button>
  </div>
</div>`;

// Проводка раздела ОДНИМ обработчиком на контейнер, через присваивание
// свойства (el.onclick), а не addEventListener: render() зовётся на каждом
// такте опроса, и накапливающиеся слушатели — грабли №1 из SKILL.md.
// words: {id: 'СЛОВО'}; onGo(id, btn) — только при точном совпадении;
// onClick(e) — остальные нажатия раздела.
export function wireSection(el, words, onGo, onClick) {
  el.oninput = (e) => {
    const inp = e.target.closest('[data-arm-input]');
    if (!inp) return;
    const id = inp.dataset.armInput;
    const go = el.querySelector(`[data-arm-go="${CSS.escape(id)}"]`);
    if (go) go.disabled = inp.value.trim() !== words[id];
  };
  el.onclick = async (e) => {
    const armBtn = e.target.closest('[data-arm]');
    if (armBtn) {
      const b = el.querySelector('#arm-' + CSS.escape(armBtn.dataset.arm));
      if (b) { b.hidden = !b.hidden; const i = b.querySelector('input'); if (!b.hidden && i) i.focus(); }
      return;
    }
    const cancel = e.target.closest('[data-arm-cancel]');
    if (cancel) {
      const b = el.querySelector('#arm-' + CSS.escape(cancel.dataset.armCancel));
      if (b) { b.hidden = true; const i = b.querySelector('input'); if (i) i.value = ''; }
      return;
    }
    const goBtn = e.target.closest('[data-arm-go]');
    if (goBtn && !goBtn.disabled) {
      const id = goBtn.dataset.armGo;
      const inp = el.querySelector(`[data-arm-input="${CSS.escape(id)}"]`);
      if (!inp || inp.value.trim() !== words[id]) return;
      goBtn.disabled = true;
      try { await onGo(id, goBtn); } finally { goBtn.disabled = false; }
      return;
    }
    if (onClick) await onClick(e);
  };
}

// ── Задания с опросом ───────────────────────────────────────────────────
// Долгие действия (замер, полная проверка) сервер выполняет как задание:
// POST стартует, GET опрашивается. Ответ задания: {state, output, result}.
export async function runJob(api, startPath, body, onTick) {
  const started = await api.post(startPath, body || {});
  if (!started || !started.ok) throw new Error((started && started.error) || t('ui_lib_job_start_failed'));
  const jid = started.data && started.data.job;
  for (let i = 0; i < 600; i++) {
    await new Promise((res) => setTimeout(res, 1500));
    const st = await api.get(`/jobs/${encodeURIComponent(jid)}`);
    const d = (st && st.data) || {};
    if (onTick) onTick(d);
    if (d.state === 'done' || d.state === 'failed') return d;
  }
  throw new Error(t('ui_lib_job_timeout'));
}

// Kill-переключатель кнопки на время запроса + вывод в слот.
export async function buttonJob(ctx, btn, el, slotId, startPath, body) {
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = old + ' …';
  showTerm(el, slotId, t('ui_lib_job_running'));
  try {
    const d = await runJob(ctx.api, startPath, body, (tick) => {
      if (tick.output) showTerm(el, slotId, tick.output, { tall: true });
    });
    showTerm(el, slotId, d.output || (d.state === 'done' ? t('ui_lib_job_done') : t('ui_lib_job_failed')), { tall: true });
    return d;
  } catch (err) {
    showTerm(el, slotId, t('ui_lib_error', err.message));
    throw err;
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

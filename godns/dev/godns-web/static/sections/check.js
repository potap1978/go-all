// Проверка — зеркало подменю «Проверка»: быстрая, полная, аудит утечки,
// проверка снаружи. Всё выполняется на сервере теми же функциями, что и в
// SSH-меню; сюда приходит захваченный вывод. Проверка снаружи раскрывает
// host:port третьей стороне — поэтому отдельное согласие (секретный путь
// при этом никуда не уходит).
import { h, section, toast } from '../ui.js';
import { t } from '../i18n.js';
import { armBlock, wireSection, buttonJob, termSlot } from './lib.js';

const WORDS = { ext: t('ui_chk_word_ext') };

export default {
  title: t('ui_sec_check'),

  async load({ api }) {
    const r = await api.get('/check');
    return r.data;
  },

  note: (d) => (d.domain ? d.domain : ''),

  render(el, d, ctx) {
    el.innerHTML = String(h`
      ${section(t('ui_chk_inside'), h`<div class="card"><div class="row">
        <button class="btn ok" data-job="quick">${t('ui_chk_quick')}</button>
        <button class="btn" data-job="full">${t('ui_chk_full')}</button>
        <button class="btn" data-job="leak">${t('ui_chk_leak')}</button>
      </div>
      <div style="margin-top:8px;font-size:var(--fs-xs);color:var(--dim)">${t('ui_chk_full_hint')}</div>
      </div>`, t('ui_chk_inside_note'))}

      ${section(t('ui_chk_outside'), h`<div class="card">
        <div class="row" style="align-items:center">
          <button class="btn" data-arm="ext">${t('ui_chk_ext_btn')}</button>
          <span style="font-size:var(--fs-xs);color:var(--dim)">${t('ui_chk_ext_note')}</span>
        </div>
      </div>`, t('ui_chk_outside_note'))}
      ${armBlock('ext', WORDS.ext,
    t('ui_chk_ext_confirm'),
    t('ui_chk_ext_go'))}

      ${termSlot('check-out')}
    `);

    wireSection(el, WORDS, async (id, btn) => {
      if (id !== 'ext') return;
      try { await buttonJob(ctx, btn, el, 'check-out', '/check/external', { confirm: WORDS.ext }); } catch (e) { /* в слоте */ }
    }, async (e) => {
      const b = e.target.closest('[data-job]');
      if (!b) return;
      try { await buttonJob(ctx, b, el, 'check-out', '/check/' + b.dataset.job, {}); } catch (err) { toast(err.message, 'bad'); }
    });
  },
};

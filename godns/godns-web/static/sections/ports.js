// Порты и путь — зеркало подменю «Порты»: смена портов DoH/DoT, новый
// секретный путь, переключатель DoT. Опасное (новый путь отваливает всех
// клиентов разом; выключение DoT — всех Android) подтверждается вводом слова.
import { h, section, badge, toast } from '../ui.js';
import { t } from '../i18n.js';
import { armBlock, wireSection, showTerm, termSlot } from './lib.js';

// Слова подтверждения: сервер принимает и русское, и английское значение.
const WORDS = { rotate: t('ui_ports_word_rotate'), dotoff: t('ui_ports_word_dotoff') };

export default {
  title: t('ui_ports_title'),

  async load({ api }) {
    const r = await api.get('/ports');
    return r.data;
  },

  render(el, d, ctx) {
    el.innerHTML = String(h`
      ${section(t('ui_ports_sec_ports'), h`<div class="card">
        <div class="row" style="align-items:flex-end">
          <label>${t('ui_ports_doh_port')}<input id="p-doh" inputmode="numeric" value="${d.doh_port || ''}" style="width:110px"></label>
          <label>${t('ui_ports_dot_port')}<input id="p-dot" inputmode="numeric" value="${d.dot_port || 853}" style="width:110px"></label>
          <button class="btn ok" data-act="ports">${t('ui_ports_btn_apply')}</button>
        </div>
        <div style="margin-top:8px;font-size:var(--fs-xs);color:var(--dim)">${t('ui_ports_hint_busy')}</div>
      </div>`, t('ui_ports_note_ports'))}

      ${section(t('ui_ports_sec_path'), h`<div class="card">
        <div class="row" style="align-items:center">
          <code style="color:var(--hi);font-size:var(--fs-sm);word-break:break-all">/${d.doh_path || '—'}</code>
          <span class="sp"></span>
          <button class="btn no" data-arm="rotate">${t('ui_ports_btn_rotate')}</button>
        </div>
      </div>`, t('ui_ports_note_path'))}
      ${armBlock('rotate', WORDS.rotate,
    t('ui_ports_rotate_warn'),
    t('ui_ports_rotate_go'))}

      ${section('DoT', h`<div class="card">
        <div class="row" style="align-items:center">
          ${d.dot_enabled ? badge(t('ui_ports_dot_on', d.dot_port || 853), 'ok') : badge(t('ui_ports_dot_off_badge'), 'banned')}
          <span style="font-size:var(--fs-xs);color:var(--dim)">${d.dot_enabled
    ? t('ui_ports_dot_open_note')
    : t('ui_ports_dot_need_note')}</span>
          <span class="sp"></span>
          ${d.dot_enabled
    ? h`<button class="btn no" data-arm="dotoff">${t('ui_ports_btn_dotoff')}</button>`
    : h`<button class="btn" data-act="doton">${t('ui_ports_btn_doton')}</button>`}
        </div>
      </div>`)}
      ${armBlock('dotoff', WORDS.dotoff,
    t('ui_ports_dotoff_warn'),
    t('ui_ports_btn_dotoff'))}

      ${termSlot('ports-out')}
    `);

    wireSection(el, WORDS, async (id) => {
      try {
        if (id === 'rotate') {
          const r = await ctx.api.post('/path/rotate', { confirm: WORDS.rotate });
          showTerm(el, 'ports-out', (r.data && r.data.output) || t('ui_ports_done'));
          toast(t('ui_ports_rotated_toast'), 'ok');
        } else if (id === 'dotoff') {
          const r = await ctx.api.post('/ports/dot', { enabled: false, confirm: WORDS.dotoff });
          showTerm(el, 'ports-out', (r.data && r.data.output) || t('ui_ports_done'));
          toast(t('ui_ports_dotoff_toast'), 'ok');
        }
        ctx.refresh();
      } catch (err) { toast(err.message, 'bad'); }
    }, async (e) => {
      const b = e.target.closest('[data-act]');
      if (!b) return;
      b.disabled = true;
      try {
        if (b.dataset.act === 'ports') {
          const doh = el.querySelector('#p-doh').value.trim();
          const dot = el.querySelector('#p-dot').value.trim();
          const r = await ctx.api.post('/ports', { doh_port: doh, dot_port: dot });
          showTerm(el, 'ports-out', (r.data && r.data.output) || t('ui_ports_applied'));
          toast(t('ui_ports_applied_toast'), 'ok');
        } else if (b.dataset.act === 'doton') {
          const r = await ctx.api.post('/ports/dot', { enabled: true });
          showTerm(el, 'ports-out', (r.data && r.data.output) || t('ui_ports_done'));
          toast(t('ui_ports_doton_toast'), 'ok');
        }
        ctx.refresh();
      } catch (err) {
        toast(err.message, 'bad');
      } finally { b.disabled = false; }
    });
  },
};

// Копии — зеркало подменю «Копии»: создать, список, восстановить, прибрать,
// расписание. Восстановление — разрушительное: подтверждается вводом слова.
// Сертификаты в копии не кладутся (они чужие), исполняемое из копии не
// восстанавливается — это устройство lib/backup.sh, панель его наследует.
import { h, section, badge, table, toast, seg } from '../ui.js';
import { t } from '../i18n.js';
import { armBlock, wireSection, showTerm, termSlot } from './lib.js';

// Слова подтверждения. Сервер принимает оба языка (CONFIRM_WORDS),
// поэтому отправляем слово того языка, на котором его набрал человек.
const WORDS = { restore: t('ui_bk_word_restore'), prune: t('ui_bk_word_prune') };

// Кандидат на восстановление живёт между перерисовками такта.
let restoreName = null;

export default {
  title: t('ui_bk_title'),

  async load({ api }) {
    const r = await api.get('/backups');
    return r.data;
  },

  note: (d) => t('ui_bk_note_count', (d.items || []).length),

  render(el, d, ctx) {
    const items = d.items || [];
    el.innerHTML = String(h`
      ${section(t('ui_bk_create_title'), h`<div class="card"><div class="row" style="align-items:flex-end">
        <label style="flex:1 1 200px">${t('ui_bk_label_field')}<input id="bk-label" placeholder="before-change" autocomplete="off"></label>
        <button class="btn ok" data-act="create">${t('ui_bk_btn_create')}</button>
      </div>
      <div style="margin-top:8px;font-size:var(--fs-xs);color:var(--dim)">${t('ui_bk_create_hint')}</div>
      </div>`)}

      ${section(t('ui_bk_list_title'), items.length ? table([
    { key: 'name', title: t('ui_bk_col_file'), wide: true, cell: (r) => h`${r.name}${r.encrypted ? h` <span class="badge b-closed">${t('ui_bk_badge_encrypted')}</span>` : ''}` },
    { key: 'version', title: t('ui_bk_col_version'), cell: (r) => h`v${r.version || '?'}` },
    { key: 'created', title: t('ui_bk_col_created'), cell: (r) => h`${r.created || '—'}` },
    { key: 'size', title: t('ui_bk_col_size'), num: true, cell: (r) => h`${r.size || '—'}` },
    // У зашифрованной копии кнопки быть не должно: мост принципиально не
    // принимает пароль, stdin у него закрыт, и `read -rs password` в
    // lib/backup.sh получает EOF мгновенно. Пользователь честно вводил
    // слово ВОССТАНОВИТЬ и получал «код 1». Вместо кнопки — где это
    // делается.
    { key: 'act', title: '', cell: (r) => (r.encrypted
        ? h`<span class="badge b-dim">${t('ui_bk_enc_ssh_only')}</span>`
        : h`<button class="btn no" data-restore="${r.name}">${t('ui_bk_btn_restore')}</button>`) },
  ], items) : h`<div class="empty">${t('ui_bk_empty')}</div>`,
  d.dir ? t('ui_bk_dir', d.dir) : '')}
      ${armBlock('restore', WORDS.restore,
    t('ui_bk_restore_hint'),
    t('ui_bk_btn_restore'))}

      ${section(t('ui_bk_sched_section'), h`<div class="card" style="display:grid;gap:12px">
        <div class="row" style="align-items:center">
          <span style="color:var(--dim);font-size:var(--fs-sm)">${t('ui_bk_sched_label')}</span>
          ${seg('sched', d.schedule || 'off', [
    { v: 'daily', label: t('ui_bk_sched_daily') }, { v: 'weekly', label: t('ui_bk_sched_weekly') },
    { v: 'monthly', label: t('ui_bk_sched_monthly') }, { v: 'off', label: t('ui_bk_sched_off') }])}
        </div>
        <div class="row" style="align-items:center">
          <button class="btn no" data-arm="prune">${t('ui_bk_btn_prune')}</button>
          <span style="font-size:var(--fs-xs);color:var(--dim)">${t('ui_bk_prune_note', d.keep || 5)}</span>
        </div>
      </div>`)}
      ${armBlock('prune', WORDS.prune, t('ui_bk_prune_hint'), t('ui_bk_btn_prune_go'))}

      ${termSlot('bk-out')}
    `);

    wireSection(el, WORDS, async (id) => {
      try {
        if (id === 'restore') {
          if (!restoreName) { toast(t('ui_bk_pick_first'), 'bad'); return; }
          showTerm(el, 'bk-out', t('ui_bk_restoring', restoreName));
          const r = await ctx.api.post('/backups/restore', { name: restoreName, confirm: WORDS.restore });
          showTerm(el, 'bk-out', (r.data && r.data.output) || t('ui_bk_done'), { tall: true });
          toast(t('ui_bk_restored'), 'ok');
        } else if (id === 'prune') {
          const r = await ctx.api.post('/backups/prune', { confirm: WORDS.prune });
          showTerm(el, 'bk-out', (r.data && r.data.output) || t('ui_bk_done'));
          toast(t('ui_bk_pruned'), 'ok');
        }
        restoreName = null;
        ctx.refresh();
      } catch (err) { toast(err.message, 'bad'); }
    }, async (e) => {
      const rb = e.target.closest('[data-restore]');
      if (rb) {
        restoreName = rb.dataset.restore;
        const blk = el.querySelector('#arm-restore');
        if (blk) {
          blk.hidden = false;
          const hint = blk.querySelector('div');
          if (hint) hint.textContent = t('ui_bk_restore_q', restoreName);
          const i = blk.querySelector('input'); if (i) i.focus();
        }
        return;
      }
      const sg = e.target.closest('.seg[data-seg="sched"] button[data-v]');
      if (sg) {
        try {
          await ctx.api.post('/backups/schedule', { freq: sg.dataset.v });
          toast(t('ui_bk_sched_set', sg.textContent), 'ok');
          ctx.refresh();
        } catch (err) { toast(err.message, 'bad'); }
        return;
      }
      const b = e.target.closest('[data-act]');
      if (!b) return;
      if (b.dataset.act === 'create') {
        b.disabled = true;
        showTerm(el, 'bk-out', t('ui_bk_creating'));
        try {
          const label = el.querySelector('#bk-label').value.trim();
          const r = await ctx.api.post('/backups/create', { label });
          showTerm(el, 'bk-out', (r.data && r.data.output) || t('ui_bk_done'));
          toast(t('ui_bk_created'), 'ok');
          ctx.refresh();
        } catch (err) {
          toast(err.message, 'bad');
          showTerm(el, 'bk-out', t('ui_bk_error', err.message));
        } finally { b.disabled = false; }
      }
    });
  },
};

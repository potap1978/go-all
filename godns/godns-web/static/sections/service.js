// Служба — зеркало подменю «Служба»: состояние, перезапуск (перечитать
// конфиг и применить), перечитать сертификат, показать конфиг, журнал.
// Удаление goDNS в панель не выносится: это SSH-операция с обязательной
// копией и чтением отчёта — из браузера ей делать нечего.
import { h, section, badge, table, toast } from '../ui.js';
import { t } from '../i18n.js';
import { SERVICE_BADGE, showTerm, termSlot } from './lib.js';

export default {
  title: t('ui_svc_title'),
  interval: 15000,

  async load({ api }) {
    const r = await api.get('/service');
    return r.data;
  },

  note: (d) => (d.engine || ''),

  render(el, d, ctx) {
    const svc = SERVICE_BADGE[d.service] || SERVICE_BADGE.not_installed;
    el.innerHTML = String(h`
      ${section(t('ui_svc_state_title'), h`<div class="card">
        ${table([
    { key: 'k', title: '', cell: (r) => h`<span style="color:var(--dim)">${r.k}</span>` },
    { key: 'v', title: '', wide: true, cell: (r) => (r.render ? r.render() : h`${r.v}`) },
  ], [
    { k: t('ui_svc_row_service'), render: () => badge(svc[0], svc[1]) },
    { k: t('ui_svc_row_engine'), v: d.engine_version || d.engine || '—' },
    { k: t('ui_svc_row_since'), v: d.since || '—' },
    { k: 'PID', v: d.main_pid || '—' },
    { k: t('ui_svc_row_listen'), v: (d.listen || []).join('  ') || '—' },
    ...(d.drift ? [{ k: t('ui_svc_row_attn'), render: () => badge(t('ui_svc_drift'), 'pending') }] : []),
  ])}
        <div class="row" style="margin-top:12px">
          <button class="btn ok" data-act="restart">${t('ui_svc_btn_restart')}</button>
          <button class="btn" data-act="reload-certs">${t('ui_svc_btn_reload_certs')}</button>
          <button class="btn" data-act="config">${t('ui_svc_btn_config')}</button>
        </div>
        <div style="margin-top:8px;font-size:var(--fs-xs);color:var(--dim)">${t('ui_svc_restart_hint')}</div>
      </div>`)}

      ${section(t('ui_svc_logs_title'), h`<div class="card"><div class="row" style="align-items:center">
        <button class="btn" data-act="logs" data-n="60">${t('ui_svc_btn_logs_last', 60)}</button>
        <button class="btn" data-act="logs" data-n="200">${t('ui_svc_btn_logs_n', 200)}</button>
        <span style="font-size:var(--fs-xs);color:var(--dim)">${t('ui_svc_logs_hint')}</span>
      </div></div>`)}

      ${termSlot('svc-out')}
    `);

    el.onclick = async (e) => {
      const b = e.target.closest('[data-act]');
      if (!b) return;
      b.disabled = true;
      try {
        if (b.dataset.act === 'restart') {
          showTerm(el, 'svc-out', t('ui_svc_applying'));
          const r = await ctx.api.post('/service/restart', {});
          showTerm(el, 'svc-out', (r.data && r.data.output) || t('ui_svc_done'), { tall: true });
          toast(t('ui_svc_applied'), 'ok');
          ctx.refresh();
        } else if (b.dataset.act === 'reload-certs') {
          const r = await ctx.api.post('/cert/reload', {});
          showTerm(el, 'svc-out', (r.data && r.data.output) || t('ui_svc_done'));
          toast(t('ui_svc_cert_reloaded'), 'ok');
        } else if (b.dataset.act === 'config') {
          const r = await ctx.api.get('/service/config');
          showTerm(el, 'svc-out', (r.data && r.data.text) || '—', { tall: true });
        } else if (b.dataset.act === 'logs') {
          const r = await ctx.api.get('/service/logs?lines=' + encodeURIComponent(b.dataset.n || '60'));
          showTerm(el, 'svc-out', (r.data && r.data.text) || '—', { tall: true });
        }
      } catch (err) {
        toast(err.message, 'bad');
      } finally { b.disabled = false; }
    };
  },
};

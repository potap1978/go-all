// Сертификат — зеркало подменю «Сертификат» SSH-меню: показать текущий,
// перечитать в службе без разрыва соединений, переустановить реакцию на
// перевыпуск. Выпуск нового и смена домена остаются в SSH-мастере: это
// многошаговые операции с DNS-проверками, вести их из панели рано.
import { h, section, badge, table, toast } from '../ui.js';
import { t } from '../i18n.js';
import { certBadge, showTerm, termSlot, humanDate } from './lib.js';

export default {
  title: t('ui_cert_title'),
  interval: 30000,

  async load({ api }) {
    const r = await api.get('/cert');
    return r.data;
  },

  note: (d) => (d.domain || ''),

  render(el, d, ctx) {
    const cb = certBadge(d.days);
    el.innerHTML = String(h`
      ${section(t('ui_cert_sec_current'), d.domain ? h`<div class="card">
        ${table([
    { key: 'k', title: '', cell: (r) => h`<span style="color:var(--dim)">${r.k}</span>` },
    { key: 'v', title: '', wide: true, cell: (r) => r.render ? r.render() : h`${r.v}` },
  ], [
    { k: t('ui_cert_row_domain'), v: d.domain },
    { k: t('ui_cert_row_until'), render: () => (d.until
      ? h`<span style="color:var(--hi)">${humanDate(d.until)}</span> ${badge(cb[0], cb[1])}`
      : badge(cb[0], cb[1])) },
    { k: t('ui_cert_row_file'), v: d.cert_path || '—' },
    { k: 'SAN', v: d.san || '—' },
    { k: t('ui_cert_row_issuer'), v: d.issuer || '—' },
  ])}
      </div>` : h`<div class="empty">${t('ui_cert_no_domain')}</div>`)}

      ${section(t('ui_cert_sec_renew'), h`<div class="card" style="display:grid;gap:10px">
        <div class="row" style="align-items:center">
          ${d.renew_hook === 'certbot' ? badge(t('ui_cert_hook_certbot'), 'ok')
    : d.renew_hook === 'acme_path_unit' ? badge(t('ui_cert_hook_acme'), 'ok')
      : badge(t('ui_cert_hook_none'), 'trouble')}
          <span style="font-size:var(--fs-xs);color:var(--dim)">${t('ui_cert_renew_hint')}</span>
        </div>
        <div class="row">
          <button class="btn" data-act="rehook">${t('ui_cert_btn_rehook')}</button>
          <button class="btn" data-act="reload">${t('ui_cert_btn_reload')}</button>
        </div>
      </div>`, t('ui_cert_sec_renew_note'))}

      ${termSlot('cert-out')}
    `);

    el.onclick = async (e) => {
      const b = e.target.closest('[data-act]');
      if (!b) return;
      b.disabled = true;
      try {
        if (b.dataset.act === 'rehook') {
          const r = await ctx.api.post('/cert/rehook', {});
          showTerm(el, 'cert-out', (r.data && r.data.output) || t('ui_cert_done'));
          toast(t('ui_cert_rehook_ok'), 'ok');
        } else if (b.dataset.act === 'reload') {
          const r = await ctx.api.post('/cert/reload', {});
          showTerm(el, 'cert-out', (r.data && r.data.output) || t('ui_cert_done'));
          toast(t('ui_cert_reload_ok'), 'ok');
        }
        ctx.refresh();
      } catch (err) {
        toast(err.message, 'bad');
      } finally {
        b.disabled = false;
      }
    };
  },
};

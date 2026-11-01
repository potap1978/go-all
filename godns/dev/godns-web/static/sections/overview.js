// Состояние — зеркало главной панели SSH-меню (show_dashboard) плюс счётчик
// запросов из lib/stats.sh: окна 1м/15м/1ч/1д/7д и спарклайны за час и сутки.
// Смыслы цветов ровно те же, что в терминале: работает — зелёный, DoT
// выключен — красный, сертификат ≤14 дней — жёлтый, истёк — красный,
// «есть непринятые изменения» — жёлтый.
import { h, kpi, section, badge, num, ago } from '../ui.js';
import { t } from '../i18n.js';
import { SERVICE_BADGE, certBadge, humanDate, daysUntil, chart } from './lib.js';

// «≈» — окно неполное (разрыв внутри или данных меньше глубины окна),
// «—» — данных нет вовсе. Ноль вместо прочерка не рисуем никогда: ноль
// значит «запросов не было», а это враньё. Тот же контракт, что в терминале.
const winValue = (w) => {
  if (!w || w.mark === 'none') return '—';
  return (w.mark === 'approx' ? '≈' : '') + w.human;
};

export default {
  title: t('ui_ov_title'),
  interval: 10000,

  async load({ api }) {
    const r = await api.get('/overview');
    return r.data;
  },

  note: (d) => (d.domain ? d.domain : t('ui_ov_no_domain')),

  render(el, d) {
    const svc = SERVICE_BADGE[d.service] || SERVICE_BADGE.not_installed;
    const cert = certBadge(d.cert_days);

    // Сигналы — только когда есть повод: пустая полоса приучает не смотреть.
    const signals = [
      d.service === 'stopped' ? { text: t('ui_ov_sig_stopped'), go: 'service', kind: 'b-trouble' } : null,
      d.drift ? { text: t('ui_ov_sig_drift'), go: 'service', kind: 'b-pending' } : null,
      d.cert_days !== null && d.cert_days !== undefined && Number(d.cert_days) <= 0 && d.domain
        ? { text: t('ui_ov_sig_cert_expired'), go: 'cert', kind: 'b-banned' } : null,
      d.cert_days !== null && Number(d.cert_days) > 0 && Number(d.cert_days) <= 14
        ? { text: t('ui_ov_sig_cert_expiring', d.cert_days), go: 'cert', kind: 'b-trouble' } : null,
    ].filter(Boolean);

    const wins = d.stats && d.stats.windows ? d.stats.windows : [];
    const winByName = Object.fromEntries(wins.map((w) => [w.name, w]));
    const w1d = winByName['1d'];

    el.innerHTML = String(h`
      ${signals.length ? h`<div class="row">${signals.map((g) => h`
        <a class="badge ${g.kind}" href="#/${g.go}" style="padding:6px 11px">${g.text}</a>`)}</div>` : ''}

      ${kpi([
    { value: svc[0], label: t('ui_ov_kpi_service', d.engine || ''), color: svc[1] === 'ok' ? 'var(--ok)' : svc[1] === 'trouble' ? 'var(--warn)' : 'var(--dim)' },
    d.domain
      ? { value: cert[0], label: t('ui_ov_cert'), color: cert[1] === 'ok' ? 'var(--ok)' : cert[1] === 'trouble' ? 'var(--warn)' : cert[1] === 'banned' ? 'var(--bad)' : 'var(--dim)', hint: d.cert_until ? t('ui_ov_until', humanDate(d.cert_until)) : d.domain }
      : { value: '—', label: t('ui_ov_cert'), hint: t('ui_ov_no_domain') },
    { value: winValue(w1d), label: t('ui_ov_kpi_day'), hint: d.stats && d.stats.last_ago != null ? t('ui_ov_slice_ago', ago(Date.now() / 1000 - d.stats.last_ago)) : t('ui_ov_slices_pending') },
    { value: d.upstreams ? String(d.upstreams.length) : '—', label: t('ui_ov_kpi_upstreams'), hint: d.policy_label || '' },
  ])}

      ${d.domain ? section(t('ui_ov_sec_dates'), h`<div class="card" style="display:grid;gap:10px">
        <div class="row" style="align-items:center">
          <span style="color:var(--dim);font-size:var(--fs-xs);min-width:12ch">${t('ui_ov_row_domain')}</span>
          <code style="color:var(--hi);font-size:var(--fs-sm)">${d.domain}</code>
          ${d.domain_paid_status === 'ok' && d.domain_paid_until
    ? (() => {
      const left = daysUntil(d.domain_paid_until);
      const kind = left !== null && left <= 0 ? 'b-banned' : left !== null && left <= 30 ? 'b-trouble' : 'b-ok';
      const text = left !== null && left <= 0
        ? t('ui_ov_reg_expired', humanDate(d.domain_paid_until))
        : t('ui_ov_reg_paid', humanDate(d.domain_paid_until));
      return h`<span class="badge ${kind}">${text}</span>`;
    })()
    : d.domain_paid_status === 'pending'
      ? h`<span style="color:var(--faint);font-size:var(--fs-xs)">${t('ui_ov_reg_pending')}</span>`
      : h`<span style="color:var(--faint);font-size:var(--fs-xs)">${t('ui_ov_reg_unknown')}</span>`}
        </div>
        <div class="row" style="align-items:center">
          <span style="color:var(--dim);font-size:var(--fs-xs);min-width:12ch">${t('ui_ov_cert')}</span>
          ${d.cert_until
    ? h`<code style="color:var(--hi);font-size:var(--fs-sm)">${t('ui_ov_until', humanDate(d.cert_until))}</code>
        <span class="badge ${cert[1] === 'ok' ? 'b-ok' : cert[1] === 'trouble' ? 'b-trouble' : 'b-banned'}">${cert[0]}</span>`
    : h`<span class="badge b-unknown">${t('ui_ov_until_unknown')}</span>`}
        </div>
      </div>`, t('ui_ov_sec_dates_note')) : ''}

      ${section(t('ui_ov_sec_addr'), h`<div class="card" style="display:grid;gap:10px">
        <div class="row" style="align-items:center">
          <span style="color:var(--dim);font-size:var(--fs-xs);min-width:6ch">DoH</span>
          ${d.doh_url
    ? h`<code style="color:var(--hi);font-size:var(--fs-sm);word-break:break-all">${d.doh_url}</code>
        <button class="btn" data-copy="${d.doh_url}">${t('ui_ov_copy')}</button>`
    : h`<span class="badge b-unknown">${t('ui_ov_not_configured')}</span>`}
        </div>
        <div class="row" style="align-items:center">
          <span style="color:var(--dim);font-size:var(--fs-xs);min-width:6ch">DoT</span>
          ${d.dot_enabled && d.dot_addr
    ? h`<code style="color:var(--hi);font-size:var(--fs-sm)">${d.dot_addr}</code>
        <button class="btn" data-copy="${d.dot_addr}">${t('ui_ov_copy')}</button>`
    : h`<span class="badge b-banned">${t('ui_ov_dot_off')}</span>
        <span style="color:var(--faint);font-size:var(--fs-xs)">${t('ui_ov_dot_off_hint')}</span>`}
        </div>
      </div>`, t('ui_ov_sec_addr_note'))}

      ${section(t('ui_ov_sec_queries'), h`<div class="card" style="display:grid;gap:14px">
        ${wins.length ? h`<div class="kpi" style="margin:0">${wins.map((w) => h`
          <div class="kpi-card">
            <div class="kpi-n" style="font-size:20px${w.mark === 'none' ? ';color:var(--dim)' : ''}">${winValue(w)}</div>
            <div class="kpi-l">${w.label}</div>
            ${w.cache != null ? h`<div class="kpi-h">${t('ui_ov_from_cache', w.cache)}${w.fails ? ` · SERVFAIL ${w.fails}` : ''}</div>` : ''}
          </div>`)}</div>` : h`<div class="empty">${t('ui_ov_slices_empty')}</div>`}
        ${(() => {
    // Блочные спарклайны из терминала здесь не годятся: глифы не тянутся
    // по ширине, строка уезжает в горизонтальную прокрутку, а значение
    // под курсором прочитать нечем. Рисуем по тем же числам (series),
    // которыми терминал рисует свои блоки, — расходиться им нечем.
    const ser = (d.stats && d.stats.series) || {};
    const gaps = ['1h', '24h'].some((k) => ser[k] && ser[k].values.some((v) => v === null));
    return h`${ser['1h'] ? chart(ser['1h'], {
      title: t('ui_ov_chart_1h'),
      meta: d.stats.peak_1h ? t('ui_ov_peak', num(d.stats.peak_1h)) : '',
      height: 56,
    }) : ''}
    ${ser['24h'] ? chart(ser['24h'], {
      title: t('ui_ov_chart_24h'),
      meta: d.stats.peak_24h ? t('ui_ov_peak', num(d.stats.peak_24h)) : '',
      height: 56,
    }) : ''}
    ${gaps ? h`<div class="row" style="gap:6px;align-items:center;font-size:var(--fs-2xs);color:var(--faint)">
      <span style="display:inline-block;width:14px;height:10px;background:var(--faint);opacity:0.13;border-radius:2px"></span>
      ${t('ui_ov_gap_legend')}
    </div>` : ''}`;
  })()}
      </div>`, t('ui_ov_sec_queries_note'))}

      ${section(t('ui_ov_sec_upstreams'), h`<div class="card">
        ${(d.upstreams || []).length ? h`<div class="row">${d.upstreams.map((u) => badge(u.name, u.filter === 'none' ? 'ok' : 'closed'))}</div>
          <div style="margin-top:10px;font-size:var(--fs-xs);color:var(--dim)">${t('ui_ov_policy', d.policy_label || d.policy || '—')}</div>`
    : h`<div class="empty">${t('ui_ov_no_upstreams')}</div>`}
      </div>`)}
    `);

    el.onclick = async (e) => {
      const b = e.target.closest('[data-copy]');
      if (!b) return;
      try {
        await navigator.clipboard.writeText(b.dataset.copy);
        b.textContent = t('ui_ov_copied');
        setTimeout(() => { b.textContent = t('ui_ov_copy'); }, 1500);
      } catch (err) { /* нет clipboard API — например, http:// через туннель */ }
    };
  },
};

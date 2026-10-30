// Подключение — зеркало «клиентских сниппетов» SSH-меню: адреса и готовые
// инструкции для роутеров, ОС и браузеров. Текст приходит с сервера тем же
// кодом, что печатает их в терминале (lib/selftest.sh), — источник один.
// Адрес DoT показывается ТОЛЬКО при включённом DoT: это инвариант проекта.
import { h, section, badge, seg } from '../ui.js';
import { t } from '../i18n.js';
import { showTerm, termSlot } from './lib.js';

let tab = 'routers';

export default {
  title: t('ui_sec_connect'),

  async load({ api }) {
    const r = await api.get('/connect');
    return r.data;
  },

  note: (d) => (d.domain || ''),

  render(el, d, ctx) {
    el.innerHTML = String(h`
      ${section(t('ui_cn_addresses'), h`<div class="card" style="display:grid;gap:10px">
        <div class="row" style="align-items:center">
          <span style="color:var(--dim);font-size:var(--fs-xs);min-width:6ch">DoH</span>
          ${d.doh_url ? h`<code style="color:var(--hi);font-size:var(--fs-sm);word-break:break-all">${d.doh_url}</code>
            <button class="btn" data-copy="${d.doh_url}">${t('ui_cn_copy')}</button>`
    : h`<span class="badge b-unknown">${t('ui_cn_not_configured')}</span>`}
        </div>
        <div class="row" style="align-items:center">
          <span style="color:var(--dim);font-size:var(--fs-xs);min-width:6ch">DoT</span>
          ${d.dot_enabled && d.dot_addr ? h`<code style="color:var(--hi);font-size:var(--fs-sm)">${d.dot_addr}</code>
            <button class="btn" data-copy="${d.dot_addr}">${t('ui_cn_copy')}</button>`
    : h`<span class="badge b-banned">${t('ui_cn_disabled')}</span>
        <span style="color:var(--faint);font-size:var(--fs-xs)">${t('ui_cn_dot_hint')}</span>`}
        </div>
      </div>`, t('ui_cn_addr_note'))}

      ${section(t('ui_cn_instructions'), h`<div class="card">
        <div class="row">${seg('snip', tab, [
    { v: 'routers', label: t('ui_cn_tab_routers') },
    { v: 'os', label: t('ui_cn_tab_os') },
    { v: 'browsers', label: t('ui_cn_tab_browsers') },
    { v: 'phones', label: t('ui_cn_tab_phones') },
  ])}</div>
      </div>`, t('ui_cn_instr_note'))}
      ${termSlot('conn-out')}
      <div id="conn-qr"></div>
    `);

    const qrBox = el.querySelector('#conn-qr');
    const termBox = () => el.querySelector('[data-term="conn-out"]');

    const loadQr = async () => {
      qrBox.innerHTML = String(h`<div class="card"><span style="color:var(--dim)">${t('ui_loading')}</span></div>`);
      let d;
      try {
        d = (await ctx.api.get('/connect/qr')).data;
      } catch (err) {
        qrBox.innerHTML = String(h`<div class="card"><span class="badge b-banned">${t('ui_lib_error', err.message)}</span></div>`);
        return;
      }
      // SVG приходит с доверенного сервера (qrencode), но всё равно
      // вставляем ТОЛЬКО распознанный <svg …>…</svg>, а не любой ответ.
      const svg = (raw) => {
        const s = String(raw || '');
        return (s.startsWith('<svg') && s.includes('</svg>')) ? s : '';
      };
      const a = d.android || {};
      const ios = d.ios || {};
      const androidBlock = a.state === 'ok'
        ? h`<div style="text-align:center">
              <div style="color:var(--dim);font-size:var(--fs-sm);margin-bottom:6px">${t('ui_cn_qr_android_scan')}</div>
              <code style="color:var(--hi)">${a.host}</code>
              <div class="qr">${svg(a.svg) ? h`<div data-svg="${svg(a.svg)}"></div>` : ''}</div>
              <div style="color:var(--faint);font-size:var(--fs-xs)">${t('ui_cn_qr_android_warn')}</div>
            </div>`
        : (a.state === 'badport'
            ? h`<span class="badge b-unknown">${t('ui_cn_qr_android_badport', a.port)}</span>`
            : h`<span class="badge b-unknown">${t('ui_cn_qr_android_nodot')}</span>`);
      const iosBlock = ios.error
        ? h`<span class="badge b-banned">${ios.error}</span>`
        : h`<div style="text-align:center">
              <div style="color:var(--dim);font-size:var(--fs-sm);margin-bottom:6px">${t('ui_cn_qr_ios_scan')}</div>
              <div class="qr">${svg(ios.svg) ? h`<div data-svg="${svg(ios.svg)}"></div>` : ''}</div>
              <div style="color:var(--faint);font-size:var(--fs-xs)">${t('ui_cn_qr_ios_after')}</div>
            </div>`;
      qrBox.innerHTML = String(h`
        <div class="card" style="display:grid;grid-template-columns:1fr 1fr;gap:18px">
          <div><div style="font-weight:600;margin-bottom:8px">Android</div>${androidBlock}</div>
          <div><div style="font-weight:600;margin-bottom:8px">iPhone / iPad</div>${iosBlock}</div>
        </div>
        <div style="color:var(--faint);font-size:var(--fs-xs);margin-top:6px">${t('ui_cn_qr_note')}</div>`);
      // SVG вставляем через textContent-безопасный путь: h`` экранирует
      // атрибут, поэтому кладём разметку из data-svg в innerHTML отдельно.
      qrBox.querySelectorAll('[data-svg]').forEach((n) => { n.innerHTML = n.getAttribute('data-svg'); });
    };

    const loadSnip = async (which) => {
      const term = termBox();
      if (which === 'phones') {
        if (term) term.style.display = 'none';
        await loadQr();
        return;
      }
      if (term) term.style.display = '';
      qrBox.innerHTML = '';
      showTerm(el, 'conn-out', t('ui_loading'));
      try {
        const r = await ctx.api.get('/connect/snippets?which=' + encodeURIComponent(which));
        showTerm(el, 'conn-out', (r.data && r.data.text) || '—', { tall: true });
      } catch (err) {
        showTerm(el, 'conn-out', t('ui_lib_error', err.message));
      }
    };
    loadSnip(tab);

    el.onclick = async (e) => {
      const sg = e.target.closest('.seg[data-seg="snip"] button[data-v]');
      if (sg) {
        tab = sg.dataset.v;
        el.querySelectorAll('.seg[data-seg="snip"] button').forEach((b) => b.classList.toggle('on', b.dataset.v === tab));
        await loadSnip(tab);
        return;
      }
      const b = e.target.closest('[data-copy]');
      if (!b) return;
      try {
        await navigator.clipboard.writeText(b.dataset.copy);
        b.textContent = t('ui_cn_copied');
        setTimeout(() => { b.textContent = t('ui_cn_copy'); }, 1500);
      } catch (err) { /* http://localhost без clipboard API */ }
    };
  },
};

// Точка входа — единственный файл, который правится под продукт.
// Здесь только список разделов. Разделы — зеркало SSH-меню godns.sh:
// человек, привыкший к терминалу, находит всё на тех же местах.
import { mountShell } from './shell.js';
import * as api from './api.js';
import { initTelegram } from './tg-webapp.js';
import { t } from './i18n.js';

// Значки — символы Unicode, а не иконочный пакет: внешних ресурсов в системе
// нет, а моноширинный шрифт рисует их ровно по сетке.
const SECTIONS = [
  { id: 'overview',  title: t('ui_sec_overview'),   icon: '◉', short: t('ui_short_overview'),  load: () => import('./sections/overview.js') },
  { id: 'cert',      title: t('ui_sec_cert'),  icon: '◈', short: t('ui_short_cert'),   load: () => import('./sections/cert.js') },
  { id: 'upstreams', title: t('ui_sec_upstreams'),    icon: '⇄', short: t('ui_short_upstreams'),  load: () => import('./sections/upstreams.js') },
  { id: 'ports',     title: t('ui_sec_ports'), icon: '⌸', short: t('ui_short_ports'),  load: () => import('./sections/ports.js') },
  { id: 'check',     title: t('ui_sec_check'),    icon: '⧗', short: t('ui_short_check'),   load: () => import('./sections/check.js') },
  { id: 'service',   title: t('ui_sec_service'),      icon: '⚙', short: t('ui_short_service'),  load: () => import('./sections/service.js') },
  { id: 'backups',   title: t('ui_sec_backups'),       icon: '▣', short: t('ui_short_backups'),   load: () => import('./sections/backups.js') },
  { id: 'connect',   title: t('ui_sec_connect'), icon: '⌁', short: t('ui_short_connect'),  load: () => import('./sections/connect.js') },
];

// Внутри Telegram — родная «назад» вместо второй стрелки в интерфейсе.
const tg = initTelegram({ onBack: () => { location.hash = '#/' + SECTIONS[0].id; } });

const shell = mountShell({
  sections: SECTIONS,
  api,
  storageKey: 'godns',
  titleSuffix: 'goDNS Pro',
  onRoute: (id, nested) => tg.back && tg.back(nested),
});

export { shell, tg };

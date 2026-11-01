// Точка входа — единственный файл, который правится под продукт.
// Здесь только список разделов, значки меню и период. Всё остальное — в
// shell.js (маршрут и опрос) и ui.js (компоненты).
import { mountShell } from './shell.js';
import * as api from './api.js';
import { initTelegram } from './tg-webapp.js';

// Значки — символы Unicode, а не иконочный пакет: внешних ресурсов в системе
// нет, а моноширинный шрифт рисует их ровно по сетке.
const SECTIONS = [
  { id: 'overview', title: 'Обзор', icon: '◉', short: 'обзор', load: () => import('./sections/example.js') },
  { id: 'network', title: 'Сеть', icon: '◈', short: 'сеть', load: () => import('./sections/example.js') },
  { id: 'log', title: 'Журнал', icon: '☰', short: 'журнал', load: () => import('./sections/example.js') },
  // hot: true — значок счётчика красный: раздел требует действия
  { id: 'moderation', title: 'Модерация', icon: '⚑', short: 'модер.', hot: true, load: () => import('./sections/example.js') },
];

const PERIODS = [{ v: 1, label: '24ч' }, { v: 7, label: '7д' }, { v: 30, label: '30д' }, { v: 90, label: '90д' }];

// Внутри Telegram — родная «назад» вместо второй стрелки в интерфейсе.
const tg = initTelegram({ onBack: () => { location.hash = '#/' + SECTIONS[0].id; } });

const shell = mountShell({
  sections: SECTIONS,
  periods: PERIODS,
  api,
  storageKey: 'adm',
  titleSuffix: 'панель',
  onRoute: (id, nested) => tg.back && tg.back(nested),
  // счётчики у пунктов меню: обновляются раз в 30 с независимо от раздела
  badges: async (a) => {
    const p = await a.get('/pending').catch(() => null);
    return p ? { moderation: p.count || 0 } : null;
  },
});

export { shell, tg };

// Оболочка панели: маршрутизация по вкладкам, общий период, ленивый опрос.
// Продуктового здесь нет ничего — всё приходит через mountShell(cfg).
//
// Три решения, ради которых оболочка вообще существует:
//   1. Раздел = свой адрес (#/network). На вкладку можно дать ссылку, работают
//      «назад» и «вперёд», при перезагрузке остаёшься там же.
//   2. Опрашивается ТОЛЬКО открытый раздел. Иначе пять запросов каждые 20 с
//      уходят независимо от того, на что человек смотрит.
//   3. Когда вкладка браузера скрыта, опрос останавливается совсем — на
//      телефоне это заметная разница по батарее и трафику.
import * as ui from './ui.js';

const $ = (s) => document.querySelector(s);

export const state = {
  route: null,
  days: null,
  section: null,   // загруженный модуль текущего раздела
  data: null,
  timer: null,
  loading: false,
  error: null,
  badges: {},      // счётчики у пунктов меню: {moderation: 3, queue: 12}
};

/**
 * mountShell({
 *   sections: [{ id, title, icon, short, hot, load: () => import('./sections/x.js') }],
 *   periods:  [{ v: 1, label: '24ч' }, …],        // необязательно
 *   api,                                          // передаётся разделам как ctx.api
 *   badges: async (api) => ({ moderation: 3 }),   // необязательно, раз в 30 с
 *   storageKey: 'adm',                            // префикс в localStorage
 *   titleSuffix: 'панель',                        // для document.title
 * })
 *
 * Модуль раздела: { title, interval?, note?(data), async load(ctx), render(el, data, ctx) }
 */
export function mountShell(cfg) {
  const sections = cfg.sections;
  const periods = cfg.periods || null;
  const key = (cfg.storageKey || 'app') + '.days';
  const DEFAULT = sections[0].id;
  const meta = Object.fromEntries(sections.map((s) => [s.id, s]));

  if (periods) {
    state.days = periods[Math.min(1, periods.length - 1)].v;
    // значение из localStorage могли испортить руками или оставить от старой
    // версии: чужое число молча ушло бы в API, а ни одна кнопка периода не
    // подсветилась бы
    const saved = Number(localStorage.getItem(key));
    if (periods.some((p) => p.v === saved)) state.days = saved;
  }

  function parseHash() {
    const m = (location.hash || '').replace(/^#\/?/, '').split('?');
    const id = (m[0] || DEFAULT).trim();
    const q = new URLSearchParams(m[1] || '');
    return { id: meta[id] ? id : DEFAULT, q };
  }

  function go(id, params) {
    const s = new URLSearchParams(params || {}).toString();
    location.hash = '#/' + id + (s ? '?' + s : '');
  }

  /** Контекст, который получает каждый раздел. */
  function ctx() {
    return {
      days: state.days,
      api: cfg.api,
      ui,
      go,
      query: parseHash().q,
      setQuery(params) {
        const q = parseHash().q;
        Object.entries(params).forEach(([k, v]) => (v == null || v === '' ? q.delete(k) : q.set(k, v)));
        go(state.route, Object.fromEntries(q));
      },
      /** Перерисовать текущий раздел, не дожидаясь такта опроса. */
      refresh: () => load(true),
      setBadge(id, n) { state.badges[id] = n; renderNav(); },
    };
  }

  // Запросы нумеруем. Пока идёт медленный, успевают переключить раздел или
  // фильтр — и ответ на устаревший запрос ни рисовать, ни класть в state
  // нельзя: он придёт последним и перетрёт свежий, а чужой раздел ещё и
  // упадёт на чужих данных.
  let seq = 0;

  async function load(force) {
    const sec = state.section;
    if (!sec) return;
    if (state.loading && !force) return;
    const my = ++seq;
    const route = state.route;
    state.loading = true;
    let data = null; let err = null;
    try { data = await sec.load(ctx()); } catch (e) { err = e.message; }
    if (my !== seq || route !== state.route || sec !== state.section) return;
    state.loading = false;
    if (err) {
      // данные на экране уже есть — значит это сбой одного такта: показываем
      // полосу «могло устареть», но экран и наполовину набранную форму не рвём
      state.error = err;
    } else {
      state.data = data;
      state.error = null;
    }
    paint();
  }

  function paint() {
    const host = $('#view');
    const stale = $('#stale');
    if (!state.data) {
      host.innerHTML = state.error
        ? String(ui.h`<div class="err">${state.error}</div>`)
        : '<div class="loading">загружаю…</div>';
      stale.hidden = true;
      return;
    }
    try {
      state.section.render(host, state.data, ctx());
    } catch (e) {
      host.innerHTML = String(ui.h`<div class="err">не удалось отрисовать раздел: ${e.message}</div>`);
    }
    const note = $('#head-note');
    if (note) note.textContent = state.section.note ? state.section.note(state.data) : '';
    stale.hidden = !state.error;
    if (state.error) stale.innerHTML = String(ui.h`данные могли устареть: ${state.error}`);
  }

  function schedule() {
    clearInterval(state.timer);
    const every = state.section && state.section.interval;
    if (!every) return;
    state.timer = setInterval(() => {
      if (document.hidden) return;   // скрытая вкладка сервер не дёргает
      load();
    }, every);
  }

  function renderNav() {
    const items = sections.map(({ id, title, icon, short, hot }) => {
      const n = state.badges[id];
      return ui.h`<a href="#/${id}" class="${id === state.route ? 'on' : ''}" data-nav="${id}" title="${title}">
        <i class="ic">${icon || '·'}</i><span class="full">${title}</span><span class="short">${short || title}</span>${
  n ? ui.h`<i class="cnt ${n > 0 && hot ? 'hot' : ''}">${n}</i>` : ''}</a>`;
    });
    $('#nav').innerHTML = String(ui.h`${items}`);
    const per = $('#period');
    if (per && periods) per.innerHTML = String(ui.seg('days', state.days, periods));
  }

  async function mount() {
    const { id } = parseHash();
    const changed = id !== state.route || !state.section;
    state.route = id;
    if (changed) {
      state.data = null;
      state.error = null;
      $('#view').innerHTML = '<div class="loading">загружаю…</div>';
      state.section = (await meta[id].load()).default;
      const t = state.section.title || meta[id].title;
      document.title = cfg.titleSuffix ? `${t} · ${cfg.titleSuffix}` : t;
      const head = $('#head-title');
      if (head) head.textContent = t;
      if (cfg.onRoute) cfg.onRoute(id, id !== DEFAULT);
    }
    renderNav();
    await load(true);
    schedule();
  }

  // общий период — один на все разделы, живёт в адресе и в localStorage
  const per = $('#period');
  if (per && periods) {
    per.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-v]');
      if (!b) return;
      state.days = Number(b.dataset.v);
      localStorage.setItem(key, state.days);
      renderNav();
      load(true);
    });
  }
  window.addEventListener('hashchange', mount);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) load(true); });
  const burger = $('#burger');
  if (burger) burger.addEventListener('click', () => document.body.classList.toggle('nav-open'));
  $('#nav').addEventListener('click', (e) => {
    if (e.target.closest('a')) document.body.classList.remove('nav-open');
  });

  mount();

  // Значки «требует внимания» обновляем независимо от открытого раздела:
  // человек должен видеть заявку, даже когда смотрит журнал.
  if (cfg.badges) {
    const poll = async () => {
      try {
        const b = await cfg.badges(cfg.api);
        if (b) { Object.assign(state.badges, b); renderNav(); }
      } catch (e) { /* значки не критичны */ }
    };
    poll();
    setInterval(() => { if (!document.hidden) poll(); }, 30000);
  }

  return { go, refresh: () => load(true), state };
}

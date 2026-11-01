// Образец раздела: показывает контракт целиком. Скопировать, переименовать,
// заменить данные.
//
// Контракт: { title, interval?, note?(data), async load(ctx), render(el, data, ctx) }
//   load    — только запросы, никакой разметки. Кинутая ошибка попадёт в полосу
//             «данные могли устареть», уже нарисованный экран не пострадает.
//   render  — только разметка, никаких запросов. Вызывается на каждом такте
//             опроса, поэтому обработчики НЕ навешиваются поэлементно: они
//             копились бы с каждой перерисовкой. Слушатель — один, на контейнер.
//   note    — примечание в шапке рядом с заголовком.
import { h, kpi, section, table, badge, seg, mixbar, bar, spark, stackedBars,
  legendOf, num, pct, ago, okColor, toast, VERDICT_COLOR } from '../ui.js';

export default {
  title: 'Обзор',
  interval: 20000,          // такт опроса; убрать — раздел статичный

  async load({ api, days }) {
    // Параллельно, а не последовательно: раздел ждёт самый медленный запрос,
    // а не их сумму. Второстепенное гасим .catch(() => null) и потом честно
    // говорим, чего не узнали, — пропавшая плитка читается как «всё спокойно».
    const [main, extra] = await Promise.all([
      api.get(`/overview?days=${days}`),
      api.get('/queue').catch(() => null),
    ]);
    return { main, extra, days };
  },

  note: (d) => `за ${d.days} дн.`,

  render(el, d, ctx) {
    const t = d.main.totals || {};

    // Сигналы — то, что требует действия. Показываем только когда есть повод:
    // пустая полоса сигналов приучает не смотреть на неё.
    const signals = [
      t.pending ? { text: `${t.pending} ждут решения`, go: 'moderation', kind: 'b-pending' } : null,
      d.extra && d.extra.paused ? { text: 'очередь на паузе', go: 'log', kind: 'b-trouble' } : null,
    ].filter(Boolean);

    el.innerHTML = String(h`
      ${signals.length ? h`<div class="row">${signals.map((g) => h`
        <a class="badge ${g.kind}" href="#/${g.go}" style="padding:6px 11px">${g.text} →</a>`)}</div>` : ''}

      ${kpi([
    { value: `${t.online}/${t.total}`, label: 'нод онлайн', color: okColor(t.total ? (100 * t.online) / t.total : 0),
      hint: `ёмкость ${t.capacity} слотов` },
    { value: num(t.checks), label: `проверок за ${d.days} дн.` },
    { value: pct(t.bad_pct), label: 'проблемных целей', color: okColor(100 - (t.bad_pct || 0)) },
    // «0» при недоступном источнике — не ноль, а незнание: прочерк и подпись
    d.extra ? { value: num(d.extra.waiting), label: 'ждёт в очереди' }
      : { value: '—', label: 'ждёт в очереди', hint: 'не удалось получить' },
  ])}

      ${section('Проверки по дням', h`<div class="card">
        ${(d.main.bars || []).length
    ? stackedBars(d.main.bars, VERDICT_COLOR, { order: Object.keys(VERDICT_COLOR), labels: d.main.labels })
    : h`<div class="empty">нет проверок за период</div>`}
        <div class="legend" style="margin-top:12px">${legendOf(d.main.by_verdict, VERDICT_COLOR, d.main.labels)}</div>
      </div>`, `${num(t.checks)} проверок`)}

      ${section('Последние', table([
    { key: 'created_at', title: 'когда', cell: (r) => h`${ago(r.created_at)} назад` },
    { key: 'target', title: 'цель', wide: true },
    { key: 'port', title: 'порт', num: true },
    { key: 'verdict', title: 'вердикт', cell: (r) => badge((d.main.labels || {})[r.verdict] || r.verdict, r.verdict) },
    { key: 'pct', title: '%', num: true, cell: (r) => h`${r.pct == null ? '—' : r.pct + '%'}` },
    // опасное действие — .btn.no: красный призрак, наливается под курсором
    { key: 'act', title: '', cell: (r) => h`<button class="btn no" data-act="drop" data-id="${r.id}">убрать</button>` },
  ], d.main.recent || [], { empty: 'проверок за период не было' }))}
    `);

    // Один слушатель на контейнер, а не на каждую кнопку: раздел
    // перерисовывается по таймеру, поэлементные обработчики копились бы.
    el.onclick = async (e) => {
      const b = e.target.closest('button[data-act="drop"]');
      if (!b) return;
      b.disabled = true;                       // защита от второго нажатия
      try {
        await ctx.api.post('/drop', { id: b.dataset.id });
        toast('убрано', 'ok');
        ctx.refresh();                         // не дожидаясь такта опроса
      } catch (err) {
        toast(err.message, 'bad');
        b.disabled = false;
      }
    };
  },
};

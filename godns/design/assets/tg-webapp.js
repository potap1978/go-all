// Мини-приложение Telegram. Вызывать ДО первой отрисовки, один раз.
//
// Принцип: вид у нас один на все площадки, поэтому тему клиента мы не
// подхватываем — светлый Telegram не должен выбеливать тёмную панель. Из
// клиента берём только то, чего вёрстка знать не может: реальную высоту окна,
// безопасную зону, родные кнопки и вибрацию. Наружу отдаём заглушки, поэтому
// один и тот же код работает и в браузере, и внутри Telegram.
//
// В <head> страницы:
//   <script src="https://telegram.org/js/telegram-web-app.js"></script>
// Это единственный внешний скрипт, который тут допустим.

const tok = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const NOOP = { show() {}, hide() {}, set() {}, progress() {} };

export function initTelegram(opts = {}) {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (!tg || !tg.initData === undefined) { /* не в Telegram — работаем как обычная страница */ }
  if (!tg) return { tg: null, inside: false, main: NOOP, haptic() {}, close() {} };

  document.documentElement.dataset.tg = tg.platform || '1';
  tg.ready();
  tg.expand();

  // Высота: viewportStableHeight не скачет при открытой клавиатуре, в отличие
  // от 100vh и innerHeight. Без этого нижнее меню то уезжает под край, то
  // прыгает на каждый ввод символа.
  const setVh = () => {
    const px = tg.viewportStableHeight || tg.viewportHeight || window.innerHeight;
    document.documentElement.style.setProperty('--tg-vh', px + 'px');
  };
  setVh();
  tg.onEvent('viewportChanged', setVh);
  window.addEventListener('resize', setVh);

  // Вертикальный свайп закрывает мини-апп — посреди заполненной формы это
  // потеря данных. Метод появился в 7.7, на старых клиентах молча пропускаем.
  if (tg.disableVerticalSwipes) tg.disableVerticalSwipes();

  // Шапку и фон красим ИЗ ТОКЕНОВ, а не константой: сменится --bg — сменятся
  // и они, без правки этого файла.
  try {
    tg.setHeaderColor(tok('--bg') || '#080b0a');
    tg.setBackgroundColor(tok('--bg') || '#080b0a');
  } catch (e) { /* старый клиент: только hex из фиксированного набора */ }

  // Родная кнопка «назад» вместо своей: в мини-аппе она уже есть в шапке,
  // вторая стрелка в интерфейсе сбивает.
  if (tg.BackButton) {
    tg.BackButton.onClick(() => (opts.onBack ? opts.onBack() : history.back()));
  }

  /** Главное действие экрана. Дублировать его в вёрстке нельзя — ui.css
   *  прячет элементы [data-tg-main] внутри Telegram. */
  const main = {
    set({ text, onClick }) {
      const b = tg.MainButton;
      if (!b) return;
      b.setParams({
        text,
        color: tok('--accent') || '#3fb950',
        text_color: tok('--accent-ink') || '#05130a',
      });
      if (onClick) { b.offClick(main._h); main._h = onClick; b.onClick(onClick); }
      b.show();
    },
    show() { tg.MainButton && tg.MainButton.show(); },
    hide() { tg.MainButton && tg.MainButton.hide(); },
    progress(on) {
      const b = tg.MainButton; if (!b) return;
      on ? b.showProgress(true) : b.hideProgress();
    },
    _h: null,
  };

  /** Вибрация: 'tap' — обычное нажатие, 'ok' / 'bad' — итог операции.
   *  Без неё мини-апп ощущается веб-страницей, а не приложением. */
  const haptic = (kind = 'tap') => {
    const H = tg.HapticFeedback; if (!H) return;
    if (kind === 'ok') H.notificationOccurred('success');
    else if (kind === 'bad') H.notificationOccurred('error');
    else if (kind === 'warn') H.notificationOccurred('warning');
    else H.impactOccurred(kind === 'heavy' ? 'medium' : 'light');
  };

  return {
    tg,
    inside: true,
    main,
    haptic,
    /** Показывать/прятать родную «назад» на вложенных экранах. */
    back(on) {
      if (!tg.BackButton) return;
      on ? tg.BackButton.show() : tg.BackButton.hide();
    },
    /** Спросить подтверждение перед закрытием, если в форме есть несохранённое. */
    guardClose(on) { tg.enableClosingConfirmation && (on ? tg.enableClosingConfirmation() : tg.disableClosingConfirmation()); },
    close() { tg.close(); },
  };
}

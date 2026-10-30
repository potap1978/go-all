import { t } from './i18n.js';
// Обращения к серверу. Одна точка входа, чтобы ошибки выглядели одинаково
// во всех разделах: раздел получает либо данные, либо Error с человеческой
// строкой — её оболочка покажет в полосе «данные могли устареть».
//
// BASE — ОТ ТЕКУЩЕЙ СТРАНИЦЫ, не от корня. Панель живёт под секретным путём
// (https://домен:порт/AbC…/), и абсолютный '/api/…' уходил бы мимо префикса —
// на 404 для чужих глаз. Тот же приём, что на странице входа.
const BASE = location.pathname.replace(/\/[^/]*$/, '').replace(/\/$/, '') + '/api';

// Аварийный вход по SSH-туннелю: панель открывают как
// http://127.0.0.1:ПОРТ/?token=… — токен из /etc/godns/webui/token. Токен
// снимается из адреса в sessionStorage (не localStorage: пусть живёт не
// дольше вкладки) и дальше ходит заголовком. Тот же приём, что у goTelegram.
const TOKEN_KEY = 'godns-web-token';
let tunnelToken = '';
try {
  const fromUrl = (new URLSearchParams(location.search).get('token') || '').trim();
  if (fromUrl) {
    sessionStorage.setItem(TOKEN_KEY, fromUrl);
    // подчистим адресную строку: токену не место в истории браузера
    const u = new URL(location.href);
    u.searchParams.delete('token');
    history.replaceState(null, '', u.pathname + (u.search || '') + (u.hash || ''));
  }
  tunnelToken = sessionStorage.getItem(TOKEN_KEY) || '';
} catch (e) { /* приватное окно без storage — остаются cookie и мини-апп */ }

async function req(method, path, body) {
  let r;
  try {
    r = await fetch(BASE + path, {
      method,
      // X-GoDNS-Admin — страж записи: без него сервер не принимает POST.
      // Простейшая защита от CSRF: чужая форма такой заголовок не поставит.
      headers: {
        ...(body ? { 'content-type': 'application/json' } : {}),
        ...(method !== 'GET' ? { 'X-GoDNS-Admin': '1' } : {}),
        ...(tunnelToken ? { 'X-GoDNS-Token': tunnelToken } : {}),
        // Внутри Telegram у каждого запроса есть подпись initData: если
        // браузер фрейма выбросил стороннюю cookie, панель всё равно живёт.
        ...(window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData
          ? { 'X-Telegram-Init-Data': window.Telegram.WebApp.initData } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'same-origin',
    });
  } catch (e) {
    // сеть отвалилась — это не «сервер вернул ошибку», и текст должен быть свой
    throw new Error(t('ui_err_offline'));
  }
  if (r.status === 401) {
    // сессия кончилась — честнее перезагрузить: сервер отдаст страницу входа
    setTimeout(() => location.reload(), 800);
    throw new Error(t('ui_err_relogin'));
  }
  if (r.status === 403) throw new Error(t('ui_err_forbidden'));
  const txt = await r.text();
  let data = null;
  try { data = txt ? JSON.parse(txt) : null; } catch (e) { /* не JSON — ниже */ }
  if (!r.ok) {
    // Причина провала лежит в data.data.output — там log_error вроде «Порт
    // 853 уже занят (nginx)». Раньше она выбрасывалась, и пользователь
    // видел только «действие завершилось с кодом 1»: сервер честно писал,
    // ЧТО не так, а панель это стирала. Прикрепляем тело к ошибке, чтобы
    // раздел мог показать вывод в терминальном слоте.
    let msg = (data && (data.detail || data.error)) || t('ui_err_http', r.status);
    // Каждый раздел ловит только err.message и показывает его в toast.
    // Поэтому причину подмешиваем в САМО сообщение, а не рядом: иначе
    // пришлось бы править все разделы, и следующий забыл бы.
    const out = data && data.data && typeof data.data.output === 'string' ? data.data.output : '';
    const why = out.split('\n').map((s) => s.trim()).filter(Boolean).pop();
    if (why && !msg.includes(why)) msg = `${msg} — ${why}`;
    const err = new Error(msg);
    if (data) err.data = data;
    throw err;
  }
  if (data === null && txt) throw new Error(t('ui_err_notjson'));
  return data;
}

export const get = (p) => req('GET', p);
export const post = (p, b) => req('POST', p, b);
export const del = (p) => req('DELETE', p);

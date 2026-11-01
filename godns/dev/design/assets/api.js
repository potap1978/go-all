// Обращения к серверу. Одна точка входа, чтобы ошибки выглядели одинаково
// во всех разделах: раздел получает либо данные, либо Error с человеческой
// строкой — её оболочка покажет в полосе «данные могли устареть».
const BASE = '/api';

async function req(method, path, body) {
  let r;
  try {
    r = await fetch(BASE + path, {
      method,
      headers: body ? { 'content-type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'same-origin',
    });
  } catch (e) {
    // сеть отвалилась — это не «сервер вернул ошибку», и текст должен быть свой
    throw new Error('нет связи с сервером');
  }
  if (r.status === 401 || r.status === 403) throw new Error('нужен вход заново');
  const txt = await r.text();
  let data = null;
  try { data = txt ? JSON.parse(txt) : null; } catch (e) { /* не JSON — ниже */ }
  if (!r.ok) throw new Error((data && (data.detail || data.error)) || `сервер ответил ${r.status}`);
  if (data === null && txt) throw new Error('сервер ответил не JSON');
  return data;
}

export const get = (p) => req('GET', p);
export const post = (p, b) => req('POST', p, b);
export const del = (p) => req('DELETE', p);

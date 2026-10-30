// Строки панели. Источник истины — .i18n-keys/webui-ui*.tsv, где русский и
// английский стоят в ОДНОЙ строке; `python3 tools/build-lang.py` собирает
// из них godns-web/static/lang/{ru,en}.json. Руками json не править.
//
// Словарь приходит НЕ загрузкой файла, а впечатанным в страницу
// (`window.__GODNS_I18N__`): иначе между отрисовкой и ответом на fetch
// человек успевает увидеть непереведённый интерфейс, а на секретном пути
// ещё и лишний запрос в журнале доступа.
const DICT = (typeof window !== 'undefined' && window.__GODNS_I18N__) || {};
export const LANG = (typeof window !== 'undefined' && window.__GODNS_LANG__) || 'ru';

// t('key') → строка. t('key', a, b) подставляет {0}, {1}.
// Нет ключа — возвращаем сам ключ: так пропущенный перевод видно сразу,
// а не «пусто вместо фразы». Тот же приём, что в оболочке.
export function t(key, ...args) {
  let s = Object.prototype.hasOwnProperty.call(DICT, key) ? DICT[key] : key;
  for (let i = 0; i < args.length; i += 1) {
    s = s.split('{' + i + '}').join(String(args[i]));
  }
  return s;
}

// Список: t('key') для каждого, удобно для наборов подписей.
export const tt = (...keys) => keys.map((k) => t(k));

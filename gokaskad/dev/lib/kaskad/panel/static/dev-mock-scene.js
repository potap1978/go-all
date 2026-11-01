/* Kaskad — dev-mock-scene.js: сцена заглушки «как на узле» (?mock=1&scene=<имя>) поверх dev-mock.js и dev-mock-ux.js.
   Данные сцены — dev-mock-scene-<имя>.js (window.KASKAD_MOCK_SCENES[имя] = {at, get: {"/api/…": тело}, live}): записанные
   ответы GET живого узла с вычищенными адресами, доменами и ключами. Здесь: GET записанного адреса отвечает записанным телом,
   поток событий шлёт записанную полную картину live один раз; всё прочее (изменения, незаписанные адреса) — обычная
   заглушка. Время в данных (секунды и мс эпохи) сдвигается на «сейчас − at»: «в сети с», события и свежесть — как в момент
   записи. Только для проверки вида (ui_audit_run.mjs, tools/k3_layout_check.mjs); в index.html не подключается. */
(function (g) {
  "use strict";
  var M = g.KASKAD_MOCK;
  var NAME = new URLSearchParams(location.search).get("scene");
  var S = M && NAME && g.KASKAD_MOCK_SCENES ? g.KASKAD_MOCK_SCENES[NAME] : null;
  if (!S) { return; }
  var SHIFT = Math.max(0, Math.floor(Date.now() / 1000) - (S.at || 0));
  var COUNTERS = { rx: 1, tx: 1, bytes: 1, packets: 1, mem: 1, total: 1, used: 1, free: 1, size: 1, avail: 1 };
  function shifted(x, key) {
    if (Array.isArray(x)) { return x.map(function (v) { return shifted(v, key); }); }
    if (x && typeof x === "object") { var o = {}; Object.keys(x).forEach(function (k) { o[k] = shifted(x[k], k); }); return o; }
    if (typeof x === "number" && !COUNTERS[key]) {
      if (x > 1.6e9 && x < 2.2e9) { return x + SHIFT; }
      if (x > 1.6e12 && x < 2.2e12) { return x + SHIFT * 1000; }
    }
    return x;
  }
  // EN (?lang=en): записанные ответы — по-русски, как сервер до перевода; переводим тем же словарём dev-mock-en.js, что и
  // обычную заглушку (иначе на EN строки узла, например «Движок AmneziaWG: модуль ядра», оставались русскими — REV-ui3 П3)
  function tr(x) { return g.KaskadMockEn && typeof g.KaskadMockEn.tx === "function" ? g.KaskadMockEn.tx(x) : x; }
  var base = M.fetch;
  M.fetch = function (url, init) {
    var method = ((init && init.method) || "GET").toUpperCase();
    var u = new URL(url, location.href), path = u.pathname.replace(/^.*?(\/api\/)/, "/api/").replace(/\/+$/, "");
    var key = Object.prototype.hasOwnProperty.call(S.get, path + u.search) ? path + u.search : path;
    if (method !== "GET" || !Object.prototype.hasOwnProperty.call(S.get, key)) { return base(url, init); }
    var body = JSON.stringify(tr(shifted(S.get[key])));
    return new Promise(function (ok) {
      setTimeout(function () { ok(new Response(body, { status: 200, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } })); }, 40);
    });
  };
  if (S.live && M.EventSource) {
    M.EventSource = function (url) {
      var self = this, handlers = {};
      this.url = url; this.readyState = 0; this.onopen = null; this.onerror = null;
      this.addEventListener = function (t, fn) { (handlers[t] = handlers[t] || []).push(fn); };
      this.close = function () { self.readyState = 2; };
      setTimeout(function () {
        if (self.readyState === 2) { return; }
        self.readyState = 1;
        if (self.onopen) { self.onopen({}); }
        var d = JSON.stringify(Object.assign({ full: true }, tr(shifted(S.live))));
        (handlers.live || []).forEach(function (fn) { fn({ data: d }); });
      }, 60);
    };
  }
})(window);

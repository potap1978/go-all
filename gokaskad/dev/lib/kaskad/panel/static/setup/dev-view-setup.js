/* Kaskad — панель просмотра мастера для разработки (как «Просмотр» прототипа QuickStart.dc.html): любой этап и его
   состояния на данных dev-mock-setup.js. Подключается только из setup.js и только при ?mock=1 вне секретного пути
   сервера — в продукте не работает. ?bar=0 — без полосы (снимки экранов); window.KaskadDev.run("<id>") — сцена по имени.

       python3 -m http.server 8765 --directory lib/kaskad/panel/static
       http://127.0.0.1:8765/setup/index.html?mock=1                      этапы и состояния — кнопками сверху
       http://127.0.0.1:8765/setup/index.html?mock=1&step=exits&theme=light   этап и тема адресом */
(function () {
  "use strict";
  const M = window.KASKAD_SETUP_MOCK;
  if (!M) return;
  const Q = new URLSearchParams(location.search);
  const A = () => window.KaskadSetup;                    // крючки setup.js (есть только при ?mock=1)
  const wait = (ms) => new Promise((ok) => setTimeout(ok, ms));
  const after = (fn) => Promise.resolve().then(fn).then(() => wait(50)).then(() => A().render());

  async function goStage(kind) {
    const st = M.state;
    // этап другого режима (сцена «Выходы» после «классического»): режим — тот, в котором этот этап есть
    if (M.stages[st.mode].indexOf(kind) < 0) st.mode = Object.keys(M.stages).find((m) => M.stages[m].indexOf(kind) >= 0) || st.mode;
    const kinds = M.stages[st.mode];
    st.auth = st.auth || "password"; st.reason = null; st.pw = kind !== "welcome" || st.pw;
    st.stages = {};
    const i = kinds.indexOf(kind);
    kinds.forEach((k, n) => { if (n < i) st.stages[k] = "done"; });
    if (i >= kinds.indexOf("exits") && kinds.indexOf("exits") >= 0) ["awg2", "awg3", "wg", "l2tp"].forEach((k) => { if (M.installed.indexOf(k) < 0) M.installed.push(k); });
    if (st.mode === "classic" && kind === "done") st.answers.forward = { proto: "tcp", port: 8443, to: "192.0.2.4", to_port: 443 };
    st.current = kind;
    const S = A().S;
    S.sys = null; S.noConn = false; A().setOffline(false);
    await A().loadState();
  }
  const flag = (k, v) => { if (v === null) delete M.flags[k]; else M.flags[k] = v; };
  const ui = (k) => A().ui(k);
  const res = (k, v) => { A().S.res[k] = v; };

  // копия настроек: файл выбран → открыта (предпросмотр) → перенос
  async function bakFile() {
    await A().reload("scan");
    const u = ui("import");
    u.view = "found"; u.bak = null; u.bakGo = false; u.bakPw = "";
    u.file = { kind: "backup", name: "kaskad-192.0.2.4-20261001.kbk", size: 28725, text: "KASKAD-BACKUP demo" };
  }
  async function bakOpen() {
    await bakFile();
    const r = await A().step("backup-open", { file: "KASKAD-BACKUP demo", password: "demo-pass-1" });
    const u = ui("import");
    u.file.text = ""; u.bak = r.data; u.bakOk = false; u.bakGo = false; u.view = "backup"; u.last = "backup-open";
    await wait(100);
    A().render();
  }
  async function bakGo() {
    const u = ui("import");
    u.bakOk = true; u.bakGo = true; u.last = "import-file";
    delete A().S.res["import-file"];
    await A().step("import-file", { kind: "backup", ticket: u.bak.ticket, confirm: true });
  }

  // домен этапа 4 (3.3.3: поддомен и домен — два поля, проверка DNS сама)
  const K3 = { sub: "k3", dom: "example.com", name: "k3.example.com", checked: "k3.example.com", checking: "" };
  // сцены: [id, этап, группа, подпись, действие]
  const SC = [
    ["welcome", "welcome", "", "Вход", () => goStage("welcome")],
    ["welcome.empty", "welcome", "Пароль", "пустой", () => { ui("welcome").pw = ""; }],
    ["welcome.short", "welcome", "Пароль", "короче 10", () => { ui("welcome").pw = "kz7Qm4"; }],
    ["welcome.set", "welcome", "Пароль", "задан", () => { ui("welcome").pw = "kz7Qm4Trx9Lp2wNa"; }],
    ["welcome.weak", "welcome", "Пароль", "отказ сервера", () => { ui("welcome").pw = "aaaaaaaaaa "; ui("welcome").err = document.documentElement.lang === "en" ? "This password won’t do: password: 10 to 200 characters." : "Пароль не подходит: пароль: от 10 до 200 символов."; }],
    ["welcome.fallback", "welcome", "Сертификат", "свой (не выпустился)", async () => { flag("cert", "fallback"); A().S.ipcert = null; A().enter("x"); A().enter("welcome"); await wait(200); }],

    ["import", "import", "", "Импорт", () => goStage("import")],
    ["import.found", "import", "Импорт", "найдено", () => { const u = ui("import"); u.view = "found"; u.last = null; }],
    ["import.preview", "import", "Импорт", "что изменится", () => { const u = ui("import"); u.view = "preview"; u.last = null; }],
    ["import.progress", "import", "Импорт", "перенос", async () => { const u = ui("import"); u.view = "found"; u.last = "import"; await A().step("import", {}); await wait(1300); }],
    ["import.done", "import", "Импорт", "итог", async () => { const u = ui("import"); u.view = "found"; u.last = "import"; await A().step("import", {}); await wait(4300); }],
    ["import.clean", "import", "Импорт", "чистый сервер", async () => { flag("clean", "1"); ui("import").last = null; await A().reload("scan"); }],
    ["import.file", "import", "Файл", "выбран", () => { const u = ui("import"); u.last = null; u.file = { kind: "awg", name: "kaskad-awg-export.json", size: 2480, doc: { inbounds: [] }, text: "{}" }; }],
    ["import.backup", "import", "Файл", "копия настроек", () => { const u = ui("import"); u.last = null; u.bakPw = ""; u.file = { kind: "backup", name: "kaskad-203.0.113.67-2026-10-01.kbk", size: 2480, text: "KASKAD-BACKUP demo" }; }],
    ["import.here", "import", "Импорт", "узел уже настроен", async () => { flag("clean", null); flag("here", "1"); ui("import").last = null; ui("import").view = "found"; await A().reload("scan"); }],
    ["import.bakWrong", "import", "Копия", "неверный пароль", async () => { await bakFile(); ui("import").last = "backup-open"; await A().step("backup-open", { file: "KASKAD-BACKUP demo", password: "wrong-wrong" }); }],
    ["import.bakEmpty", "import", "Копия", "предпросмотр · пустой узел", async () => { flag("here", null); flag("clean", "1"); await bakOpen(); }],
    ["import.bakLoss", "import", "Копия", "предпросмотр · потери", async () => { flag("clean", null); flag("here", "1"); flag("bak", null); await bakOpen(); }],
    ["import.bakOk", "import", "Копия", "потери подтверждены", async () => { flag("clean", null); flag("here", "1"); await bakOpen(); ui("import").bakOk = true; }],
    ["import.bakDone", "import", "Копия", "перенесено", async () => { flag("clean", null); flag("here", "1"); flag("bak", null); await bakOpen(); await bakGo(); await wait(1600); }],
    ["import.bakChanged", "import", "Копия", "сервер изменился", async () => { flag("clean", null); flag("here", "1"); flag("bak", "changed"); await bakOpen(); await bakGo(); await wait(2600); }],
    ["import.filebad", "import", "Файл", "не тот файл", () => { const u = ui("import"); u.last = null; u.file = { kind: "awg", name: "notes.txt", size: 912, err: "Это не файл экспорта AmneziaWG: внутри не JSON. Возьмите файл из gokaskad → 17 → экспорт.", text: "" }; }],

    ["protocols", "protocols", "", "Режим и протоколы", () => goStage("protocols")],
    ["protocols.pick", "protocols", "Шаг", "выбор", () => { ui("protocols").view = "pick"; }],
    ["protocols.install", "protocols", "Шаг", "установка", async () => { const u = ui("protocols"); u.sel = { awg2: true, awg3: true, wg: true, l2tp: true }; await A().step("protocols", { kinds: ["awg2", "awg3", "wg", "l2tp"] }); u.view = "live"; await wait(2100); }],
    ["protocols.partial", "protocols", "Шаг", "частично", async () => { const u = ui("protocols"); await A().step("protocols", { kinds: ["awg2", "wg", "pptp"] }); u.view = "live"; await wait(10500); }],
    ["protocols.ghnone", "protocols", "Ключ GitHub", "нет ключа", async () => { flag("github", "none"); ui("protocols").view = "pick"; await A().reload("protocols-plan"); }],
    ["protocols.ghbad", "protocols", "Ключ GitHub", "не подошёл", async () => { flag("github", "bad"); ui("protocols").view = "pick"; await A().reload("protocols-plan"); }],
    ["protocols.ghok", "protocols", "Ключ GitHub", "найден", async () => { flag("github", null); await A().reload("protocols-plan"); }],
    ["protocols.warn", "protocols", "Жёлтые строки", "ядро с GitHub", async () => { flag("warn", "1"); A().S.ui.protocols = { view: "pick" }; await A().reload("protocols-plan"); flag("warn", null); }],
    ["protocols.nomodule", "protocols", "Модуль ядра", "нельзя", async () => { flag("module", "no"); await A().reload("protocols-plan"); }],
    ["protocols.classic", "protocols", "Режим", "классический", async () => { await A().step("mode", { mode: "classic" }); await A().loadState(); }],

    ["forward", "forward", "", "Пробросы (классический)", async () => { M.state.mode = "classic"; await goStage("forward"); }],
    ["forward.ok", "forward", "Проброс", "адрес отвечает", async () => { Object.assign(ui("forward"), { proto: "tcp", port: "8443", to: "192.0.2.4", toPort: "443" }); ui("forward").check = await A().step("forward-check", { to: "192.0.2.4", to_port: "443", proto: "tcp" }); }],
    ["forward.443", "forward", "Проброс", "порт 443", () => { Object.assign(ui("forward"), { proto: "tcp", port: "443", to: "192.0.2.4", toPort: "443", check: null }); }],
    ["forward.bad", "forward", "Проброс", "не отвечает", async () => { Object.assign(ui("forward"), { proto: "tcp", port: "8443", to: "10.0.0.9", toPort: "443" }); ui("forward").check = await A().step("forward-check", { to: "10.0.0.9", to_port: "443", proto: "tcp" }); }],
    ["forward.made", "forward", "Проброс", "создан", async () => { Object.assign(ui("forward"), { proto: "tcp", port: "8443", to: "192.0.2.4", toPort: "443" }); await A().step("forward", { proto: "tcp", port: "8443", to: "192.0.2.4", to_port: "443" }); }],

    ["exits", "exits", "", "Выходы", () => goStage("exits")],
    ["exits.direct", "exits", "Выбор", "напрямую", () => { ui("exits").choice = "direct"; }],
    ["exits.exits", "exits", "Выбор", "через выходы", () => { ui("exits").choice = "exits"; }],
    ["exits.directhave", "exits", "Выбор", "без выхода, а выходы есть", async () => { M.demoExits(); ui("exits").choice = "direct"; await A().reload("exits"); }],
    ["exits.empty", "exits", "Ключи", "пусто", async () => { M.state.exits = []; ui("exits").choice = "exits"; await A().reload("exits"); }],
    ["exits.keys", "exits", "Ключи", "2 из 3", async () => { M.demoExits(); ui("exits").choice = "exits"; await A().reload("exits"); }],
    ["exits.adding", "exits", "Ключи", "сборка модуля", async () => { M.state.exits = []; ui("exits").choice = "exits"; await A().step("exits-add", { text: "vpn://AAAD-demo" }); await wait(1300); }],
    ["exits.mvless", "exits", "Поле ключа", "VLESS", async () => { ui("exits").choice = "exits"; await typeKey("vless://3f1c-demo@198.51.100.45:443?security=reality"); }],
    ["exits.mawg", "exits", "Поле ключа", "AmneziaWG", async () => { M.state.exits = []; ui("exits").choice = "exits"; await A().reload("exits"); await typeKey("vpn://AAAD3nicbVPLbtswEPwVgeeCpkS-demo"); }],
    ["exits.msub", "exits", "Поле ключа", "подписка", async () => { ui("exits").choice = "exits"; await typeKey("https://sub.example.net/u/7Hq2Zk3Q"); }],
    ["exits.bad", "exits", "Поле ключа", "не узнали", async () => { ui("exits").choice = "exits"; await typeKey("просто текст"); }],
    ["exits.subs", "exits", "Ключи", "подписка и ключи", async () => { M.demoExits(); ui("exits").choice = "exits"; await A().step("exits-add", { text: "https://sub.example.net/u/7Hq2Zk3Q" }); await wait(2600); await A().reload("exits"); }],
    // × подписки: вопрос панели «с ключами / оставить ключи / отмена» (R-333i З1) и строка во время удаления
    ["exits.subask", "exits", "Ключи", "× подписки: вопрос", async () => { M.demoExits(); ui("exits").choice = "exits"; await A().step("exits-add", { text: "https://sub.example.net/u/7Hq2Zk3Q" }); await wait(2600); await A().reload("exits"); A().render(); await wait(100);
      const x = document.querySelector('.ux-ei__item[data-id^="s-"] .ux-x'); if (x) x.click(); }],
    ["exits.subdeling", "exits", "Ключи", "× подписки: удаляю", async () => { M.demoExits(); ui("exits").choice = "exits"; await A().step("exits-add", { text: "https://sub.example.net/u/7Hq2Zk3Q" }); await wait(2600); await A().reload("exits");
      const sub = M.state.subs[0]; if (sub) { ui("exits").deleting = sub.id; ui("exits").uxSig = ""; } }],

    ["domain", "domain", "", "Домен и сайт", () => goStage("domain")],
    ["domain.empty", "domain", "Домен", "не вписан", () => { A().stop("dns"); Object.assign(ui("domain"), { sub: "", dom: "", name: "", checked: "", dns: null, checking: "" }); res("dns", null); res("cert", null); }],
    ["domain.nosub", "domain", "Домен", "без поддомена (A-запись @)", () => { A().stop("dns"); Object.assign(ui("domain"), { sub: "", dom: "example.com", name: "example.com", checked: "", dns: null, checking: "" }); res("dns", null); res("cert", null); }],
    ["domain.bad", "domain", "Домен", "не того вида", () => { A().stop("dns"); Object.assign(ui("domain"), { sub: "vpn.x", dom: "my-domain", name: "", checked: "", dns: null }); }],
    ["domain.wait", "domain", "DNS", "ждём", async () => { flag("dns", "wait"); Object.assign(ui("domain"), K3); await A().step("dns", { name: "k3.example.com" }).then((r) => { ui("domain").dns = r.data; ui("domain").dnsSince = Date.now() / 1000 - 134; }); }],
    ["domain.hints", "domain", "DNS", "ждём 15 мин", async () => { flag("dns", "wait"); Object.assign(ui("domain"), K3); await A().step("dns", { name: "k3.example.com" }).then((r) => { ui("domain").dns = r.data; ui("domain").dnsSince = Date.now() / 1000 - 960; }); }],
    ["domain.cf", "domain", "DNS", "Cloudflare", async () => { flag("dns", "cloudflare"); Object.assign(ui("domain"), K3); ui("domain").dns = (await A().step("dns", { name: "k3.example.com" })).data; }],
    ["domain.wrong", "domain", "DNS", "не туда", async () => { flag("dns", "elsewhere"); Object.assign(ui("domain"), K3); ui("domain").dns = (await A().step("dns", { name: "k3.example.com" })).data; }],
    ["domain.ok", "domain", "DNS", "готов + сертификат", async () => { flag("dns", "ok"); flag("fail", null); Object.assign(ui("domain"), K3, { certAuto: false }); res("cert", null); const r = await A().step("dns", { name: "k3.example.com" }); ui("domain").dns = r.data; await A().step("cert", {}); await wait(2200); }],
    // R-333 З1/С3: DNS готов — сертификат по кнопке; во время выпуска поля имени закрыты, ход выпуска виден
    ["domain.ready", "domain", "DNS", "готов, сертификат по кнопке", async () => { flag("dns", "ok"); flag("fail", null); Object.assign(ui("domain"), K3, { certAuto: false }); res("cert", null); ui("domain").dns = (await A().step("dns", { name: "k3.example.com" })).data; }],
    ["domain.locked", "domain", "Сертификат", "выпуск идёт, имя закрыто", async () => { flag("dns", "ok"); flag("fail", null); Object.assign(ui("domain"), K3, { certAuto: false }); res("cert", null); ui("domain").dns = (await A().step("dns", { name: "k3.example.com" })).data; await A().step("cert", {}); }],
    ["domain.certerr", "domain", "Сертификат", "ошибка", async () => { flag("dns", "ok"); flag("fail", "cert"); Object.assign(ui("domain"), K3, { certAuto: true }); ui("domain").dns = (await A().step("dns", { name: "k3.example.com" })).data; await A().step("cert", {}); await wait(2200); flag("fail", null); }],
    ["domain.site", "domain", "Сайт", "вручную", async () => { await certOk(); const u = ui("domain"); u.site = "manual"; u.tplErr = null; u.tplLoading = ""; u.tpls = (await A().step("templates", {})).data; u.tpl = "cafe-zerno"; }],
    ["domain.tplerr", "domain", "Сайт", "каталог: ошибка", async () => { await certOk(); const u = ui("domain"); Object.assign(u, { site: "manual", tpls: null, tplLoading: "" }); flag("fail", "templates"); u.tplErr = await A().step("templates", {}); flag("fail", null); }],
    ["domain.tplkey", "domain", "Сайт", "каталог: нет ключа GitHub", async () => { await certOk(); const u = ui("domain"); Object.assign(u, { site: "manual", tpls: null, tplLoading: "" }); flag("tpl", "nokey"); u.tplErr = await A().step("templates", {}); flag("tpl", null); }],
    ["domain.move", "domain", "Сертификат", "переезд на домен", async () => { A().S.moveStay = false; await certOk(); if (!A().S.move) await A().prepMove(); }],
    ["domain.ipgone", "domain", "Сертификат", "IP не открывается", async () => { await certOk(); if (!A().S.move) await A().prepMove(); await wait(300); A().S.noConn = true; A().render(); const sc = document.getElementById("qs-scroll"); if (sc) sc.scrollTop = sc.scrollHeight; }],
    ["domain.sitedone", "domain", "Сайт", "поставлен", async () => { await certOk(); await A().step("site", { template: "cafe-zerno" }); await wait(2200); }],
    // VLESS Ш4 (HANDOFF-vless §5): М1 — сайт включится сам, М3 — «Не включать сайт» → согласие, М2 — копия с 9 устройствами
    // VLESS → рекомендация; Ц4 — «Перейти на TLS» → цена; выбор Reality сделан. Последняя сцена убирает VLESS из макета
    ["domain.vlm1", "domain", "VLESS", "М1: сайт включится сам", () => vlScene("m1")],
    ["domain.vlm3", "domain", "VLESS", "М3: согласие на Reality", () => vlScene("m1", "site-decline")],
    ["domain.vlm2", "domain", "VLESS", "М2: копия с устройствами", () => vlScene("m2")],
    ["domain.vlprice", "domain", "VLESS", "Ц4: цена перехода на TLS", () => vlScene("m2", "vl-tls")],
    ["domain.vlchosen", "domain", "VLESS", "Reality выбран", async () => { await vlScene("chosen"); M.vless(null); }],

    ["extras", "extras", "", "Дополнительно", () => goStage("extras")],
    ["extras.bot", "extras", "Открыт", "бот", () => { ui("extras").open = "bot"; }],
    ["extras.mcp", "extras", "Открыт", "ключ агента", async () => { ui("extras").open = "mcp"; }],
    ["extras.backup", "extras", "Открыт", "копия", () => { ui("extras").open = "backup"; }],
    ["extras.bottoken", "extras", "Бот", "токен принят", async () => { ui("extras").open = "bot"; A().render(); await typeIn('[data-ux="bot"] .ux-in--token', "7012345678:AAHq2Zk3Qx9LmT4vNbDemoDemoDemoDemo0"); await wait(900); }],
    ["extras.botbad", "extras", "Бот", "неверный токен", async () => { ui("extras").open = "bot"; A().render(); await typeIn('[data-ux="bot"] .ux-in--token', "7012345678:AAbadq2Zk3Qx9LmT4vNbDemoDemoDemoDem"); await wait(900); }],
    ["extras.mcpadd", "extras", "Ключ агента", "добавлен", async () => { ui("extras").open = "mcp"; A().render(); await wait(300);
      await typeIn('[data-ux="agent"] textarea', "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK3Qz7pLmXv2 laptop"); const b = document.querySelector('[data-ux="agent"] [data-akf="add"]'); if (b) b.click(); await wait(900); }],

    ["done", "done", "", "Готово", () => goStage("done")],
    ["done.connect", "done", "Вкладка", "подключение", () => { ui("done").tab = "connect"; }],
    ["done.ios", "done", "Платформа", "iPhone", () => issue("ios", "Мама")],
    ["done.windows", "done", "Платформа", "Windows", () => issue("windows", "Папа")],
    ["done.keenetic", "done", "Платформа", "Keenetic", () => issue("keenetic", "Дача")],
    ["done.xiaomi", "done", "Платформа", "Xiaomi", () => issue("xiaomi", "Дача")],
    ["done.traffic", "done", "Подключение", "трафик идёт", async () => { flag("conn", null); await issue("ios", "Мама"); await wait(10500); }],
    ["done.stuck", "done", "Подключение", "нет 2 мин", async () => { flag("conn", "stuck"); await issue("ios", "Мама"); await wait(600); }],
    ["done.android", "done", "Платформа", "Android, 3.1", () => issue("android", "Сын", "awg3")],
    ["done.summary", "done", "Вкладка", "сводка", async () => { ui("done").tab = "summary"; await A().step("summary"); }],
    ["done.summaryDomain", "done", "Вкладка", "сводка с доменом", async () => { M.state.domain = "k3.example.com"; ui("done").tab = "summary"; await A().step("summary"); }],
    ["done.classic", "done", "Режим", "классический: проверка", async () => { M.state.mode = "classic"; ui("done").tab = "check"; await goStage("done"); }],

    ["sys.ok", "*", "Система", "норма", () => { const S = A().S; S.sys = null; S.noConn = false; A().setOffline(false); }],
    ["sys.offline", "*", "Система", "нет сети", () => { flag("offline", "1"); A().setOffline(true); }],
    ["sys.noconn", "*", "Система", "нет связи", () => { A().S.noConn = true; }],
    ["sys.expired", "*", "Система", "сессия истекла", () => { A().showSys("expired"); }],
    ["sys.link", "*", "Система", "вход по ссылке", () => { A().showSys("link"); }],
    ["sys.login", "*", "Система", "вход паролем", () => { A().showSys("login"); }],
    ["sys.used", "*", "Система", "ссылка открыта", () => { A().showSys("used"); }],
    ["sys.closed", "*", "Система", "мастер закрыт", () => { A().showSys("closed"); }]
  ];
  async function vlScene(kind, press) {
    M.vless(kind); res("site", null); res("vless-mode", null);
    await certOk(); await A().loadState();
    // с начала страницы: прокрутка прошлой сцены (domain.ipgone листает вниз) не в счёт
    A().render(); await wait(100);
    const sc = document.getElementById("qs-scroll");
    if (sc) sc.scrollTop = 0;
    if (!press) return;
    const b = document.querySelector('[data-k="' + press + '"]');
    if (b) b.click();
    await wait(2600);
  }
  async function certOk() {
    const u = ui("domain");
    if (A().S.res.cert && A().S.res.cert.ok && u.dns && u.dns.dns === "ok") return;
    flag("dns", "ok"); flag("fail", null);
    Object.assign(u, K3, { certAuto: true });
    u.dns = (await A().step("dns", { name: "k3.example.com" })).data;
    await A().step("cert", {});
    await wait(2200);
  }
  // поле общего компонента (ux.js): текст — как от человека (событие input), ответ разбора — через 350 мс
  async function typeIn(sel, text) {
    A().render();
    await wait(120);
    const el = document.querySelector(sel);
    if (!el) return;
    el.value = text;
    el.dispatchEvent(new Event("input"));
    await wait(700);
  }
  const typeKey = (text) => typeIn(".ux-ei textarea", text);
  async function issue(pf, who, inp) {
    const u = ui("done");
    u.tab = "connect"; u.pf = pf; u.who = who; u.inp = inp || "";
    A().render();
    await wait(300);
    const b = document.querySelector('[data-k="issue"]');
    if (b) b.click();
    await wait(500);
  }
  // o.keep — проход этапов подряд (layout_scan --walk): компоненты и S.ui не сбрасываются, как у человека, который
  // идёт по мастеру (R-333i Б1: хвост этапа 3 ронял отрисовку дальше, а сцены с чистого листа его прятали)
  async function run(id, o) {
    const sc = SC.find((x) => x[0] === id);
    if (!sc) throw new Error("нет сцены " + id);
    const stage = sc[1];
    if (stage !== "*" && sc[2] && A().S.rail && A().S.rail.current !== stage) await goStage(stage);
    // общие компоненты (ux.js) держат поле между отрисовками — у каждой сцены своё, с чистого листа
    if (!(o && o.keep)) {
      ["exits", "extras"].forEach((k) => { const u = A().S.ui[k]; if (u && u.ux) { Object.keys(u.ux).forEach((n) => { const c = u.ux[n]; if (c && c.destroy) c.destroy(); }); u.ux = {}; u.uxLang = {}; u.uxSig = ""; } });
      if (A().S.ui.exits) A().S.ui.exits.deleting = "";            // хвост сцены «× подписки: удаляю»
      A().S.modal = null;
    }
    await after(sc[4]);
    draw();
    return id;
  }

  // ── полоса просмотра ──
  function el(tag, a, ...kids) {
    const e = document.createElement(tag);
    for (const k of Object.keys(a || {})) { if (k === "on") e.addEventListener("click", a.on); else if (k === "text") e.textContent = a.text; else e.setAttribute(k, a[k]); }
    kids.forEach((k) => e.append(k));
    return e;
  }
  let bar = null;
  function draw() {
    if (Q.get("bar") === "0") return;
    if (!bar) {
      document.documentElement.setAttribute("data-qs-dev", "");
      const css = el("link", { rel: "stylesheet", href: "dev-view-setup.css" });
      document.head.append(css);
      bar = el("div", { class: "qsdev", role: "toolbar", "aria-label": "Просмотр" });
      document.body.prepend(bar);
    }
    const cur = (A() && A().S.rail && A().S.rail.current) || "welcome";
    const row1 = el("div", { class: "qsdev__row" }, el("b", { text: "Просмотр" }));
    SC.filter((x) => !x[2]).forEach((x) => row1.append(el("button", { type: "button", "aria-pressed": String(x[1] === cur), on: () => run(x[0]) }, x[3])));
    const th = document.documentElement.getAttribute("data-theme");
    row1.append(el("span", { class: "qsdev__sp" }),
      el("button", { type: "button", on: () => { const u = new URL(location.href); u.searchParams.set("theme", th === "dark" ? "light" : "dark"); u.searchParams.set("step", cur); location.assign(u.toString()); } }, th === "dark" ? "Светлая тема" : "Тёмная тема"),
      el("button", { type: "button", on: () => { bar.hidden = true; document.documentElement.removeAttribute("data-qs-dev"); } }, "Скрыть"));
    const row2 = el("div", { class: "qsdev__row" });
    const groups = {};
    SC.filter((x) => x[2] && (x[1] === cur || x[1] === "*")).forEach((x) => { (groups[x[2]] = groups[x[2]] || []).push(x); });
    Object.keys(groups).forEach((g) => { row2.append(el("span", { class: "qsdev__g", text: g })); groups[g].forEach((x) => row2.append(el("button", { type: "button", "data-id": x[0], on: () => run(x[0]) }, x[3]))); });
    bar.replaceChildren(row1, row2);
  }
  window.KaskadDev = { run, scenes: () => SC.map((x) => x[0]), goStage };
  let last = "";
  const wd = setInterval(() => {
    if (!A() || !A().S.rail) return;
    const c = A().S.rail.current + "|" + (A().S.st && A().S.st.mode);
    if (c !== last) { last = c; draw(); }
  }, 300);
})();

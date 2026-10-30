// goVLESS — Copyright (c) 2025-2026 potap1978. All rights reserved.
// Licensed under the goVLESS Source-Available License (see the LICENSE file).
// Redistribution, mirroring, or republishing in any form — whole or partial,
// modified or not — is prohibited without prior written permission.

// goVLESS Mini App — production React build (v1.4.0: + cover-site template picker).
// Single-file app that mounts every screen from phase-a/webapp/dist/design/
// in a routed shell. React+Babel from cdnjs. Hash-based routing.
//
// Three screens have FULL production wiring (real RPC + actions):
//   #/                       Home (custom React, design-spec layout)
//   #/clients                ClientsList (custom React, real RPC)
//   #/clients/<uuid>         ClientCard (custom React, real RPC + actions)
//
// The other ~30 screens from the canvas are mounted as visual-only
// previews (Wizard, Inbounds, Admins, Backup, System detail, Settings,
// Partners, Intro, Disclaimer, Theme picker). They read from the same
// real client.list / system.status feed, but their internal buttons are
// not yet wired — Batch 3 work.

const { useState, useEffect, useRef } = React;

/* ─────────── Real-data feed: mutate window.CONTENT before mount ─────────── */
window.CONTENT = window.CONTENT || {
  appName: "goVLESS",
  status: "Под защитой", statusEn: "Protected",
  mode: "lite", modeFull: "Reality · Cloudflare tunnel",
  server: "—", uptime: "—",
  clientsActive: 0, clientsTotal: 0,
  trafficMonth: "0 GB", trafficCap: "1 TB", trafficPct: 0,
  clients: [],
};

const TG = window.Telegram && window.Telegram.WebApp;
if (TG) { TG.ready(); TG.expand(); TG.enableClosingConfirmation(); }
// Best-effort light haptic on primary actions; no-op when TG/Haptics absent.
function haptic() {
  try { if (TG && TG.HapticFeedback && TG.HapticFeedback.impactOccurred) TG.HapticFeedback.impactOccurred('light'); } catch(e){}
}
const INIT_DATA = (TG && TG.initData) || "";
const USER_ID = (TG && TG.initDataUnsafe && TG.initDataUnsafe.user && TG.initDataUnsafe.user.id) || 0;
const TG_LANG = (() => {
  try {
    const ls = localStorage.getItem("gov_lang");
    if (ls === "ru" || ls === "en") return ls;
  } catch(e){}
  return (TG && TG.initDataUnsafe && TG.initDataUnsafe.user && TG.initDataUnsafe.user.language_code === "en") ? "en" : "ru";
})();

let rpcSeq = 0;
async function rpc(method, params) {
  rpcSeq += 1;
  const body = { jsonrpc:"2.0", id:rpcSeq, method, params:params||{}, caller:"webapp", initData:INIT_DATA };
  // Abort a hung request after ~15s so callers reject instead of spinning forever.
  const ctrl = new AbortController();
  const timer = setTimeout(() => { try { ctrl.abort(); } catch(e){} }, 15000);
  try {
    const resp = await fetch("/api/rpc", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body), signal:ctrl.signal });
    const j = await resp.json().catch(() => ({}));
    if (j.error) {
      const friendly = (j.error.code===503) ? ((STR[TG_LANG]&&STR[TG_LANG].server_down) || j.error.message) : (j.error.message || ("RPC "+j.error.code));
      const e = new Error(friendly); e.code=j.error.code; e.detail=j.error.data; throw e;
    }
    return j.result;
  } catch (e) {
    if (e && (e.name==="AbortError" || e.code===20)) {
      const te = new Error((STR[TG_LANG]&&STR[TG_LANG].server_down) || "Server is not responding"); te.code=504; throw te;
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

const T_GET = (theme) => (window.TOKENS && window.TOKENS[theme]) || (window.getTokens && window.getTokens(theme));
const STR = {
  ru: { site_changed_sub:"Шаблон сайта-маскировки заменён", creds_changed_sub:"Выданы новые логин и пароль панели", pw_unavailable:"Пароля нет на сервере — в базе панели хранится только его хеш. Посмотрите пароль в SSH: cat /root/.govless_credentials, или нажмите «Ротировать креды» ниже, чтобы задать новый.", site_template:"Сайт-маскировка", site_pro_only:"Сайт-маскировка есть только в Pro (свой домен + TLS). Переключение режима — в SSH: govless → 1 → 4.", site_current:"Текущий шаблон", site_stub:"стандартная заглушка", site_custom:"свой из Git", site_unknown:"неизвестен (положен вручную)", site_none:"сайта нет", site_catalog_fmt:"{n} проверенных шаблонов · {c} категорий", site_open:"Открыть сайт", site_random:"🎲 Случайный", site_random_label:"случайный шаблон", site_stub_btn:"⏭ Заглушка", site_backups:"↩ Бэкапы", site_search_ph:"Поиск по названию…", site_search:"Найти", site_categories:"Категории", site_results_fmt:"Найдено: {n}", site_prev:"« Назад", site_next:"Дальше »", site_apply:"Применить", site_apply_q:"Установить «{n}»? Текущий сайт будет сохранён в бэкап.", site_started:"Запущено — скачиваю и проверяю шаблон", site_applying_fmt:"Применяю: {n} · {s}с", site_done:"Шаблон установлен", site_failed:"Не удалось: ", site_prev_saved:"прежний сайт сохранён", site_preview:"Превью", site_repo:"Репозиторий", site_license:"Лицензия", site_custom_title:"Свой шаблон из Git", site_custom_hint:"Публичный https-репозиторий со статическим сайтом (index.html в корне, dist/, public/ или build/), до 100 МБ.", site_restore:"Вернуть", site_restore_q:"Вернуть сайт из {n}?", site_no_backups:"Бэкапов пока нет — они создаются при каждой смене шаблона", site_empty_list:"Ничего не найдено", site_hint:"Шаблон меняется и в боте (/site), и в SSH-меню (govless → 1 → 5).", site_pick_hint:"Выберите категорию, найдите по названию или возьмите случайный.", site_http_fmt:"Проверка HTTP: {c}", qr:"QR",
        vless:"VLESS-ссылка",
        subscr:"Subscription URL",
        uuid_label:"UUID",
        online:"Online",
        appName:"goVLESS",
        rpc_unavailable:"RPC недоступен",
        copy:"Копировать",
        rotate:"Ротировать",
        actions:"Действия",
        search:"Поиск",
        add:"+ Добавить",
        copied:"Скопировано",
        cancelled:"Отменено",
        rotated:"Новый URL",
        deleted:"Удалено",
        err:"Ошибка: ",
        cancel:"Отмена",
        ok:"Подтвердить",
        btn_disable:"Отключить",
        btn_enable:"Включить",
        btn_limit:"Лимит трафика",
        btn_expiry:"Срок",
        btn_reset:"Сброс трафика",
        btn_rename:"Переименовать",
        btn_delete:"Удалить",
        confirm_title:"Подтвердите действие",
        confirm_type:"Введите точную строку:",
        loading:"Загрузка…",
        not_found:"Клиент не найден",
        rpc_unavail_hint:"Открой из Telegram-бота",
        empty:"Никого пока нет.",
        traffic:"Трафик",
        expires:"Истекает",
        never:"никогда",
        status:"Статус",
        shield_on:"Защита активна",
        theme_label:"Тема",
        state_on:"включён",
        state_off:"выключен",
        clients_online:"клиентов онлайн",
        clients:"Клиенты",
        menu:"Меню",
        system:"Система",
        panel_access:"Доступ к панели",
        repair:"Восстановление",
        backups:"Резервные копии",
        notifications:"Уведомления",
        admins:"Админы",
        settings:"Настройки",
        about:"О приложении",
        disclaimer:"Дисклеймер",
        empty_hint:"Добавь первого клиента — он получит свой VLESS-ключ и подписку.",
        no_results:"Ничего не найдено.",
        disabled:"Отключено",
        enabled:"Включено",
        confirm_q:"Подтвердить?",
        done:"Готово",
        reset_done:"Сброшено",
        is_on:"Включён",
        is_off:"Отключён",
        copy_key:"Копировать ключ",
        copy_sub:"Копировать подписку",
        applied:"Сохранено",
        theme_and_lang:"Тема и язык",
        lang_ru:"Русский",
        apply_hint:"Применится после нажатия «Применить».",
        theme_system:"Системная",
        theme_sys_hint:"следует за Telegram",
        theme_light:"Светлая",
        theme_dark:"Тёмная",
        btn_apply:"Применить",
        services:"Сервисы",
        mode:"Режим",
        transport:"Транспорт",
        version:"Версия",
        tunnel:"Туннель",
        not_active:"Не активен",
        restart_2s:"Перезапуск через 2 сек",
        restart_q:"Перезапустить?",
        recent_actions:"Последние действия",
        audit_empty:"Пока тихо.",
        bad_tg_id:"Неверный TG ID",
        invited:"Приглашён",
        current_admins:"Текущие админы",
        nobody:"Никого",
        invite:"Пригласить",
        inviting:"Приглашаю…",
        port:"Порт",
        protocol:"Протокол",
        more:"Подробнее",
        restored:"Восстановлено",
        backup_done_q:"Бэкап восстановлен. Перезагрузить Mini App?",
        creating_backup:"Создаю бэкап…",
        no_backups:"Резервных копий ещё нет.",
        restore_done:"Восстановление выполнено",
        what_will_happen:"Что будет сделано",
        repair_ip:"Заново определить публичный IP сервера",
        restoring:"Восстановление…",
        btn_run:"Запустить",
        result:"Результат",
        success:"Успешно",
        with_warnings:"С предупреждениями",
        build:"Сборка",
        license:"Лицензия",
        src_github:"Исходники на GitHub",
        read_disc:"Прочитать дисклеймер",
        accepted:"Принято",
        tos:"Условия использования",
        read_first:"Прочитайте перед использованием",
        legal_warn:"Правовое предупреждение",
        already_acc:"Уже принято",
        btn_decline:"Не согласен",
        btn_accept:"Согласен · продолжить",
        name_rules32:"Имя: латиница/цифры/-_., до 32",
        client_created:"Клиент создан",
        new_client:"Новый клиент",
        name:"Имя",
        name_ph:"например, iphone-anna",
        limit_gb:"Лимит трафика (ГБ, 0 = ∞)",
        expiry_opt:"Срок действия (опц.)",
        creating:"Создаю…",
        btn_create:"Создать",
        client_name:"имя клиента",
        limit_gb2:"Лимит трафика (ГБ, 0 или пусто = ∞)",
        expiry_empty:"Срок действия (пусто = ∞)",
        name_rules:"Имя: латиница/цифры/-_.",
        confirm_big:"Подтвердите крупное изменение",
        expiry:"Срок действия",
        saving:"Сохраняю…",
        btn_save:"Сохранить",
        params:"Параметры",
        hide:"скрыть",
        show:"показать",
        mask_domain:"Маскирующий домен",
        server_down:"Сервер не отвечает",
        retry_now:"Повторить сейчас",
        what_to_chat:"Что приходит в чат",
        client_added:"Добавлен клиент",
        new_key_made:"новый VLESS-ключ создан",
        limit_hit:"Достигнут лимит трафика",
        quota_used:"клиент израсходовал свою квоту",
        mode_switched:"Переключён режим",
        daemon_restart:"Перезапуск демона",
        creds_updated:"Креды обновлены",
        rotate_creds_q:"Ротировать креды панели?",
        rotate_wip:"Ротация кредов: в разработке",
        login:"Логин",
        password:"Пароль",
        public:"Публичный",
        rotating:"Ротация…",
        keys_tile:"Ключи",
        lang_section:"Язык интерфейса",
        clients_count:"клиентов",
        vpn_off:"VPN выключен",
        files_count:"файлов",
        all_n:"Все",
        vpn_word:"VPN",
        cf_tunnel:"Cloudflare tunnel",
        nginx_le:"nginx + LE cert",
        qr_sub:"QR подписки",
        qr_vless:"QR VLESS",
        qr_unavail:"QR недоступен",
        restore_btn:"Восстановить",
        rotate_creds_btn:"Ротировать креды",
        inbound_not_found:"Inbound не найден",
        time_locale:"ru-RU",
        repair_warn:"Запускаю восстановление…",
        screen_in_dev:"Экран «{name}» в разработке",
        design_note2:"Дизайн готов в /design/, RPC-wiring запланирован в Batch 3.",
        retry_in:"Повторная попытка через",
        tg_id_hint:"Чтобы получить TG ID, попроси пользователя написать /start боту — он увидит свой id в первом ответе.",
        decline_close:"Если вы не согласны с этими условиями или намерены использовать материалы в целях, противоречащих законодательству, — закройте приложение.",
        disc_h1:"01 · Назначение материалов",
        disc_h2:"02 · Законность и ответственность",
        disc_h3:"03 · Категорически запрещено",
        disc_h4:"04 · Что важно понимать",
        disc_p1:"Скрипт и ключи предоставляются исключительно в образовательных целях и для законной защиты приватности и безопасности вашего собственного трафика.",
        disc_p2:"Вы используете инструмент на свой страх и риск и несёте полную ответственность за соблюдение законодательства вашей страны.",
        disc_dont_1:"для обхода блокировок доступа к экстремистским и иным запрещённым ресурсам;",
        disc_dont_2:"для анонимного доступа к деструктивному, пиратскому или вредоносному контенту;",
        disc_dont_3:"для любой противоправной деятельности и распространения запрещённой информации.",
        disc_p3:"Программа поставляется «как есть», без каких-либо гарантий. Авторы не контролируют и не несут ответственности за способ её использования.",
        theme_sys_lc:"системную", theme_light_lc:"светлую", theme_dark_lc:"тёмную", theme_prefix:"Тема: ",
        repair_sub:"Восстановить сервер подписок", repair_links:"Перегенерировать ссылки и QR клиентов",
        no_inbounds:"Inbound'ов нет.", btn_create_backup:"+ Создать бэкап", panel_url:"URL панели",
        backup_empty_sub:"Нажми «Создать бэкап» — текущая x-ui.db, state.db и config.json соберутся в .tgz архив на сервере.",
        tunnel_changed:"Туннель изменился",
        cf_new_addr:"Новый адрес Cloudflare",
        lite_pro_done:"Переключение Lite/Pro завершено",
        daemons_restarted:"Демоны перезапущены",
        search_no_match:"По запросу «{q}» нет совпадений",
        confirm_rotate_sub_fmt:"Ротировать subscription URL для «{n}»?",
        confirm_reset_fmt:"Сбросить трафик для «{n}»?",
        confirm_delete_fmt:"Удалить «{n}»? Необратимо.",
        confirm_restore_fmt:"Восстановить из «{n}»?",
        created_fmt:"Создан: {n} ({s})",
        expiry_until:"до",
        count_active:"активных",
        disable_inbound_fmt:"Отключить inbound {id}? Активные сессии разорвутся." },
  en: { site_changed_sub:"The cover-site template was replaced", creds_changed_sub:"New panel login and password issued", pw_unavailable:"The password is not stored on the server — the panel database keeps only its hash. Read it over SSH: cat /root/.govless_credentials, or press “Rotate credentials” below to set a new one.", site_template:"Cover site", site_pro_only:"The cover (masquerade) site exists only in Pro (own domain + TLS). Switch modes in SSH: govless → 1 → 4.", site_current:"Current template", site_stub:"built-in stub page", site_custom:"custom from Git", site_unknown:"unknown (placed manually)", site_none:"no site", site_catalog_fmt:"{n} verified templates · {c} categories", site_open:"Open site", site_random:"🎲 Random", site_random_label:"random template", site_stub_btn:"⏭ Stub page", site_backups:"↩ Backups", site_search_ph:"Search by name…", site_search:"Search", site_categories:"Categories", site_results_fmt:"Found: {n}", site_prev:"« Prev", site_next:"Next »", site_apply:"Apply", site_apply_q:"Deploy “{n}”? The current site is backed up first.", site_started:"Started — downloading and verifying the template", site_applying_fmt:"Applying: {n} · {s}s", site_done:"Template deployed", site_failed:"Failed: ", site_prev_saved:"previous site saved", site_preview:"Preview", site_repo:"Repository", site_license:"License", site_custom_title:"Custom template from Git", site_custom_hint:"A public https repository with a static site (index.html in the root, dist/, public/ or build/), up to 100 MB.", site_restore:"Restore", site_restore_q:"Bring the site back from {n}?", site_no_backups:"No backups yet — one is created on every template change", site_empty_list:"Nothing found", site_hint:"The template can also be changed in the bot (/site) and the SSH menu (govless → 1 → 5).", site_pick_hint:"Pick a category, search by name, or take a random one.", site_http_fmt:"HTTP check: {c}", clients_count:"clients", vpn_off:"VPN off", qr_unavail:"QR unavailable", restore_btn:"Restore", rotate_creds_btn:"Rotate credentials", inbound_not_found:"Inbound not found", time_locale:"en-US", repair_warn:"Running repair…", tunnel_changed:"Tunnel changed", cf_new_addr:"New Cloudflare address", lite_pro_done:"Lite/Pro switch complete", daemons_restarted:"Daemons restarted", qr:"QR",
        vless:"VLESS link",
        subscr:"Subscription URL",
        uuid_label:"UUID",
        online:"Online",
        appName:"goVLESS",
        rpc_unavailable:"RPC unavailable",
        copy:"Copy",
        rotate:"Rotate",
        actions:"Actions",
        search:"Search",
        add:"+ Add",
        copied:"Copied",
        cancelled:"Cancelled",
        rotated:"New URL",
        deleted:"Deleted",
        err:"Error: ",
        cancel:"Cancel",
        ok:"Confirm",
        btn_disable:"Disable",
        btn_enable:"Enable",
        btn_limit:"Traffic limit",
        btn_expiry:"Expiry",
        btn_reset:"Reset traffic",
        btn_rename:"Rename",
        btn_delete:"Delete",
        confirm_title:"Confirm action",
        confirm_type:"Type the exact string:",
        loading:"Loading…",
        not_found:"Client not found",
        rpc_unavail_hint:"Open from Telegram bot",
        empty:"Nobody yet.",
        traffic:"Traffic",
        expires:"Expires",
        never:"never",
        status:"Status",
        shield_on:"Shield active",
        theme_label:"Theme",
        state_on:"on",
        state_off:"off",
        clients_online:"clients online",
        clients:"Clients",
        menu:"Menu",
        system:"System",
        panel_access:"Panel access",
        repair:"Repair",
        backups:"Backups",
        notifications:"Notifications",
        admins:"Admins",
        settings:"Settings",
        about:"About",
        disclaimer:"Disclaimer",
        empty_hint:"Add the first client — it will get its own VLESS key and subscription.",
        no_results:"No results.",
        disabled:"Disabled",
        enabled:"Enabled",
        confirm_q:"Confirm?",
        done:"Done",
        reset_done:"Reset done",
        is_on:"On",
        is_off:"Off",
        copy_key:"Copy key",
        copy_sub:"Copy subscription",
        applied:"Saved",
        theme_and_lang:"Theme and language",
        lang_ru:"Russian",
        apply_hint:"Will apply after you press Apply.",
        theme_system:"System",
        theme_sys_hint:"follows Telegram",
        theme_light:"Light",
        theme_dark:"Dark",
        btn_apply:"Apply",
        services:"Services",
        mode:"Mode",
        transport:"Transport",
        version:"Version",
        tunnel:"Tunnel",
        not_active:"Not active",
        restart_2s:"Restarting in 2s",
        restart_q:"Restart?",
        recent_actions:"Recent actions",
        audit_empty:"Nothing yet.",
        bad_tg_id:"Invalid TG ID",
        invited:"Invited",
        current_admins:"Current admins",
        nobody:"Nobody",
        invite:"Invite",
        inviting:"Inviting…",
        port:"Port",
        protocol:"Protocol",
        more:"More",
        restored:"Restored",
        backup_done_q:"Backup restored. Reload Mini App?",
        creating_backup:"Creating backup…",
        no_backups:"No backups yet.",
        restore_done:"Restore complete",
        what_will_happen:"What will happen",
        repair_ip:"Re-detect server public IP",
        disc_h1:"01 · Purpose of the materials", disc_h2:"02 · Legality and responsibility",
        disc_h3:"03 · Strictly prohibited", disc_h4:"04 · What to keep in mind",
        disc_p1:"The script and keys are provided solely for educational purposes and for lawfully protecting the privacy and security of your own traffic.",
        disc_p2:"You use this tool at your own risk and are fully responsible for complying with the laws of your country.",
        disc_dont_1:"bypassing access blocks to extremist or otherwise prohibited resources;",
        disc_dont_2:"anonymous access to destructive, pirated or malicious content;",
        disc_dont_3:"any unlawful activity or distribution of prohibited information.",
        disc_p3:"The software is provided “as is”, without any warranty. The authors do not control and are not responsible for how it is used.",
        theme_sys_lc:"system", theme_light_lc:"light", theme_dark_lc:"dark", theme_prefix:"Theme: ",
        repair_sub:"Restore the subscription server", repair_links:"Regenerate client links & QR",
        all_n:"All", count_active:"active", files_count:"files", expiry_until:"until", vpn_word:"VPN",
        cf_tunnel:"Cloudflare tunnel", nginx_le:"nginx + LE cert", qr_sub:"Subscription QR", qr_vless:"VLESS QR", retry_in:"Retry in",
        confirm_delete_fmt:"Delete “{n}”? Irreversible.",
        confirm_reset_fmt:"Reset traffic for “{n}”?",
        confirm_rotate_sub_fmt:"Rotate the subscription URL for “{n}”?",
        confirm_restore_fmt:"Restore from “{n}”?",
        created_fmt:"Created: {n} ({s})",
        search_no_match:"No matches for “{q}”",
        decline_close:"If you do not agree with these terms or intend to use the materials unlawfully, close the app.",
        disable_inbound_fmt:"Disable inbound {id}? Active sessions will drop.",
        screen_in_dev:"Screen “{name}” is under development",
        design_note2:"Design ready in /design/, RPC wiring planned for Batch 3.",
        tg_id_hint:"To get a TG ID, ask the user to send /start to the bot — they'll see their id in the first reply.",
        no_inbounds:"No inbounds.", btn_create_backup:"+ Create backup", panel_url:"Panel URL",
        backup_empty_sub:"Tap “Create backup” — the current x-ui.db, state.db and config.json are packed into a .tgz archive on the server.",
        restoring:"Repairing…",
        btn_run:"Run",
        result:"Result",
        success:"Success",
        with_warnings:"With warnings",
        build:"Build",
        license:"License",
        src_github:"Sources on GitHub",
        read_disc:"Read disclaimer",
        accepted:"Accepted",
        tos:"Terms of use",
        read_first:"Please read before using",
        legal_warn:"Legal notice",
        already_acc:"Already accepted",
        btn_decline:"Decline",
        btn_accept:"Accept · continue",
        name_rules32:"Name: latin/digits/-_., up to 32",
        client_created:"Client created",
        new_client:"New client",
        name:"Name",
        name_ph:"e.g., iphone-anna",
        limit_gb:"Traffic limit (GB, 0 = inf)",
        expiry_opt:"Expiry (optional)",
        creating:"Creating…",
        btn_create:"Create",
        client_name:"client name",
        limit_gb2:"Traffic limit (GB, 0 or empty = inf)",
        expiry_empty:"Expiry (empty = inf)",
        name_rules:"Name: latin/digits/-_.",
        confirm_big:"Confirm large change",
        expiry:"Expiry",
        saving:"Saving…",
        btn_save:"Save",
        params:"Parameters",
        hide:"hide",
        show:"show",
        mask_domain:"Masking domain",
        server_down:"Server is not responding",
        retry_now:"Retry now",
        what_to_chat:"What goes to chat",
        client_added:"Client added",
        new_key_made:"new VLESS key created",
        limit_hit:"Traffic limit reached",
        quota_used:"client used up its quota",
        mode_switched:"Mode switched",
        daemon_restart:"Daemon restart",
        creds_updated:"Credentials updated",
        rotate_creds_q:"Rotate panel credentials?",
        rotate_wip:"Credential rotation: in development",
        login:"Login",
        password:"Password",
        public:"Public",
        rotating:"Rotating…",
        keys_tile:"Keys",
        lang_section:"Interface language" },
};

/* ─────────── theme hook ─────────── */
function useTheme() {
  const lsKey = "gov_" + USER_ID + "_theme";
  const tgScheme = (TG && TG.colorScheme) || "dark";
  const [pref, setPref] = useState(() => { try { return localStorage.getItem(lsKey)||"system"; } catch(e){return "system";} });
  const resolved = pref==="system" ? (tgScheme==="light"?"L5":"D2") : pref==="light"?"L5":"D2";
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", pref);
    try { localStorage.setItem(lsKey, pref); } catch(e){}
  }, [pref]);
  const cycle = () => setPref(p => p==="system"?"light":p==="light"?"dark":"system");
  return { pref, resolved, cycle, setPref };
}

/* ─────────── router hook ─────────── */
function useRoute() {
  const [hash, setHash] = useState(() => window.location.hash || "#/");
  useEffect(() => {
    const cb = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", cb);
    return () => window.removeEventListener("hashchange", cb);
  }, []);
  return { hash, navigate: (h) => { window.location.hash = h; } };
}

/* ─────────── toast bus ─────────── */
const ToastBus = { listeners:new Set() };
const toast = (msg, kind) => ToastBus.listeners.forEach(cb => cb(msg, kind));
function ToastHost() {
  const [list, setList] = useState([]);
  useEffect(() => {
    const cb = (msg, kind) => {
      const id = Math.random();
      setList(l => [...l, {id, msg, kind}]);
      setTimeout(() => setList(l => l.filter(x => x.id !== id)), 2400);
    };
    ToastBus.listeners.add(cb);
    return () => ToastBus.listeners.delete(cb);
  }, []);
  return (
    <div style={{ position:"fixed", top:64, left:0, right:0, zIndex:300, display:"flex", flexDirection:"column", alignItems:"center", gap:6, pointerEvents:"none" }}>
      {list.map(t => (
        <div key={t.id} style={{
          background: t.kind==="err"?"#b3322f":t.kind==="ok"?"#2e7d32":"rgba(0,0,0,0.88)",
          color:"#fff", padding:"10px 16px", borderRadius:999, fontSize:13, pointerEvents:"auto",
          animation:"toastin 200ms cubic-bezier(0.32,0.72,0,1)",
        }}>{t.msg}</div>
      ))}
    </div>
  );
}

/* ─────────── modal bus ─────────── */
const ModalBus = { set: null };
function ModalHost() {
  const [node, setNode] = useState(null);
  useEffect(() => { ModalBus.set = setNode; return () => { ModalBus.set = null; }; }, []);
  if (!node) return null;
  return (
    <div onClick={(e) => { if (e.target === e.currentTarget) setNode(null); }} style={{
      position:"fixed", inset:0, zIndex:200,
      background:"rgba(0,0,0,0.78)", backdropFilter:"blur(6px)", WebkitBackdropFilter:"blur(6px)",
      display:"flex", alignItems:"flex-end", justifyContent:"center", padding:12,
      animation:"fadein 180ms ease-out",
    }}>{node}</div>
  );
}
const openModal = (n) => ModalBus.set && ModalBus.set(n);
const closeModal = () => ModalBus.set && ModalBus.set(null);

/* ─────────── helpers ─────────── */
function formatBytes(n) {
  if (!n) return "0 B";
  // 1024-based units are KiB/MiB/GiB — подписывать их как KB/GB значит
  // расходиться с числом на 2,4 % на гигабайте и на 10 % на терабайте (QA BUG-35).
  const u=["B","KiB","MiB","GiB","TiB"]; let v=n,i=0;
  while(v>=1024&&i<4){v/=1024;i++;}
  return v.toFixed(v<10?2:1)+" "+u[i];
}
function formatTs(ts) { return ts? new Date(ts*1000).toISOString().slice(0,10) : null; }
function hueFor(s) { let h=0; for(const ch of s||"") h=(h*31+ch.charCodeAt(0))%360; return h; }

function Header({ T, L, title, sub, onBack, themePref, onThemeClick, onSettings }) {
  return (
    <div style={{
      display:"flex", alignItems:"center", height:54, padding:"0 14px", gap:8,
      background:T.chromeBg, borderBottom:`1px solid ${T.hair}`, position:"sticky", top:0, zIndex:10,
    }}>
      <button onClick={onBack || (() => window.history.back())} style={{
        width:36,height:36,background:"transparent",border:0,color:T.ink3,cursor:"pointer",
        display:"flex",alignItems:"center",justifyContent:"center",borderRadius:8,
      }}>
        {onBack ? <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M11.5 3.5L6 9l5.5 5.5"/></svg>
                : <span style={{fontSize:20,lineHeight:1,fontWeight:300}}>✕</span>}
      </button>
      <div style={{flex:1,textAlign:"center",fontSize:14.5,fontWeight:600,lineHeight:1.15,color:T.ink}}>
        {title}
        {sub && <small style={{display:"block",fontSize:11,fontWeight:400,color:T.ink3,marginTop:1}}>{sub}</small>}
      </div>
      <button onClick={onThemeClick} title={L.theme_label} style={{
        width:32,height:36,background:"transparent",border:0,color:T.ink3,cursor:"pointer",fontSize:16,borderRadius:8,
      }}>{themePref==="light"?"☀":themePref==="dark"?"☾":"◐"}</button>
      <button onClick={onSettings || (()=>{window.location.hash="#/settings"})} title="⋯" style={{
        width:32,height:36,background:"transparent",border:0,color:T.ink3,cursor:"pointer",fontSize:18,letterSpacing:1,borderRadius:8,
      }}>⋯</button>
    </div>
  );
}
function Pill({ T, kind, children }) {
  const p = kind==="warn"?{bg:T.warnSoft,fg:T.warn,dot:T.warn}
          : kind==="bad"||kind==="err"?{bg:T.dangerSoft,fg:T.danger,dot:T.danger}
          : {bg:T.accentSoft,fg:T.accent2||T.accent,dot:T.accent};
  return <span style={{display:"inline-flex",alignItems:"center",gap:6,padding:"3px 10px",borderRadius:999,
    background:p.bg,color:p.fg,fontSize:11,fontWeight:700,letterSpacing:"0.02em",
    border:T.isDark&&kind!=="warn"&&kind!=="bad"?"1px solid rgba(110,224,166,0.18)":"1px solid transparent",
  }}><span style={{width:6,height:6,borderRadius:"50%",background:p.dot}}/>{children}</span>;
}
function Avatar({ T, name, hue, size=36 }) {
  const ini = (name||"?").split(/\s+|-|_/).map(w=>w[0]).join("").slice(0,2).toUpperCase();
  return <div style={{width:size,height:size,borderRadius:T.avatarRadius,
    background:`oklch(0.58 0.10 ${hue||145})`,display:"flex",alignItems:"center",justifyContent:"center",
    color:"#fff",fontSize:size>40?16:12,fontWeight:700,flexShrink:0}}>{ini}</div>;
}
function linkBlock(T) { return {fontFamily:T.mono,fontSize:11,lineHeight:1.45,wordBreak:"break-all",
  whiteSpace:"normal",color:T.ink,background:T.card2,border:`1px solid ${T.border}`,
  padding:"10px 12px",borderRadius:10,marginTop:6,userSelect:"all",cursor:"copy"}; }
function btn(T, kind) {
  const b = {minHeight:44,padding:"0 14px",borderRadius:10,border:"none",fontFamily:T.font,fontSize:13.5,fontWeight:600,cursor:"pointer"};
  if (kind==="secondary") return {...b,background:T.card2,color:T.ink,border:`1px solid ${T.border}`};
  if (kind==="warn") return {...b,background:T.warn,color:T.isDark?"#0c1322":"#fff"};
  if (kind==="danger") return {...b,background:T.danger,color:"#fff"};
  if (kind==="ok") return {...b,background:T.accent,color:T.primaryInk};
  return {...b,background:T.accent,color:T.primaryInk};
}

/* ─────────── Typed-confirm modal ─────────── */
function TypedConfirm({ T, L, expected, description, onResolve }) {
  const [val, setVal] = useState("");
  const [ttl, setTtl] = useState(60);
  useEffect(() => {
    const id = setInterval(() => setTtl(x => Math.max(0, x-1)), 1000);
    return () => clearInterval(id);
  }, []);
  const ok = val===expected && ttl>0;
  return (
    <div style={{width:"100%",maxWidth:480,background:T.card,color:T.ink,
      border:`1px solid ${T.border}`,borderRadius:16,padding:18,
      boxShadow:"0 -12px 40px rgba(0,0,0,0.45)",
      animation:"slidein 220ms cubic-bezier(0.32,0.72,0,1)"}}>
      <h3 style={{fontSize:16,margin:"0 0 8px",color:T.ink}}>⚠️ {L.confirm_title}</h3>
      <p style={{fontSize:13,color:T.ink2,margin:"6px 0",lineHeight:1.5}}>{description}</p>
      <p style={{fontSize:12,color:T.ink3,margin:"10px 0 4px"}}>{L.confirm_type} <code style={{
        fontFamily:T.mono,background:T.card2,padding:"2px 6px",borderRadius:4,color:T.ink,fontSize:12.5}}>{expected}</code>
        <span style={{marginLeft:6,fontFamily:T.mono,color:ttl<10?T.danger:T.ink3}}>{ttl}s</span></p>
      <input value={val} onChange={e=>setVal(e.target.value)} autoFocus style={{
        width:"100%",background:T.card2,color:T.ink,border:`1px solid ${T.border}`,
        padding:"10px 12px",borderRadius:8,fontFamily:T.mono,fontSize:13,minHeight:44}}/>
      <div style={{display:"flex",gap:8,marginTop:14}}>
        <button onClick={()=>{closeModal(); onResolve(false);}} style={{...btn(T,"secondary"),flex:"0 0 auto",padding:"0 18px"}}>{L.cancel}</button>
        <button disabled={!ok} onClick={()=>{closeModal(); onResolve(true);}} style={{...btn(T,"danger"),flex:1,opacity:ok?1:0.6,cursor:ok?"pointer":"not-allowed"}}>{L.ok}</button>
      </div>
    </div>
  );
}
function typedConfirm(T, L, expected, description) {
  return new Promise(res => openModal(<TypedConfirm T={T} L={L} expected={expected} description={description} onResolve={res}/>));
}

/* ─────────── HOME ─────────── */
function HomeScreen({ T, L, themePref, onThemeClick, navigate }) {
  const [data, setData] = useState({status:null, clients:null, audit:[], err:null, loaded:false});
  useEffect(() => {
    (async () => {
      let s = null, c = [], a = [];
      let firstErr = null;
      try { s = await rpc("system.status", {}); }
      catch(e) { if (!firstErr) firstErr = e; }
      try { c = await rpc("client.list", {}); }
      catch(e) { if (!firstErr) firstErr = e; }
      try { a = await rpc("audit.tail", { limit:5 }); }
      catch(e) { /* not critical */ }
      window.CONTENT.clients = c;
      window.CONTENT.clientsActive = c.filter(x=>x.enabled&&x.online).length;
      window.CONTENT.clientsTotal = c.length;
      window.CONTENT.mode = s ? s.mode : "lite";
      // If both system.status AND client.list failed with the same 401, treat as auth error
      if (!s && (c==null || c.length===0) && firstErr && firstErr.code===401) {
        setData(d => ({...d, err: firstErr, loaded:true}));
      } else {
        setData({status:s, clients:c, audit:a, err:null, loaded:true});
      }
    })();
  }, []);
  if (data.err) return <ErrorPane T={T} L={L} error={data.err} themePref={themePref} onThemeClick={onThemeClick}/>;
  if (!data.loaded) return <LoadingPane T={T} L={L} themePref={themePref} onThemeClick={onThemeClick}/>;
  // Fallback: status may be null (RPC error) — show degraded home with what we have
  const s = data.status || { mode: window.CONTENT.mode || "—", transport: "—", xui_active: false, xray_active: false, version: "—" };
  const cs = data.clients || [];
  const isOn = s.xui_active && s.xray_active;
  // «Онлайн» — это c.online (из 3X-UI), а не c.enabled: экран показывал
  // «3 / 3 клиентов онлайн» при нуле подключённых (QA BUG-24).
  const enabled = cs.filter(c=>c.enabled).length;
  const online = cs.filter(c=>c.enabled && c.online).length;

  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title={L.appName||"goVLESS"} sub={`${s.mode||"lite"} · ${s.transport||"reality"}`}
        themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,overflowY:"auto",padding:"6px 16px 24px",display:"flex",flexDirection:"column",gap:12}}>
        {/* Hero card — matches L5_PastelMint / D2_MidnightSlate */}
        <div style={{
          background: T.isDark?"linear-gradient(160deg,#1b2030,#171a26)":T.card,
          borderRadius:22, padding:"20px 18px",
          boxShadow:T.shadow, border:T.isDark?`1px solid ${T.border}`:"none",
        }}>
          <Pill T={T} kind={isOn?"ok":"warn"}>{isOn?L.shield_on:L.vpn_off}</Pill>
          <h1 style={{fontSize:22,fontWeight:700,margin:"12px 0 6px",letterSpacing:"-0.4px",color:T.ink,lineHeight:1.15}}>
            VPN <span style={{color:T.accent}}>{isOn?L.state_on:L.state_off}</span>.
          </h1>
          <div style={{fontSize:12.5,color:T.ink3}}>
            {s.mode==="lite"?L.cf_tunnel:L.nginx_le} · {enabled} {L.clients_count}
          </div>
          <div style={{display:"flex",gap:18,marginTop:18,paddingTop:16,borderTop:`1px solid ${T.isDark?T.hair:"#e6efea"}`}}>
            <div style={{flex:1}}>
              <div style={{fontSize:20,fontWeight:700,letterSpacing:"-0.3px",fontVariantNumeric:"tabular-nums",color:T.ink}}>
                {online} / {enabled}
              </div>
              <div style={{fontSize:11,color:T.ink3,marginTop:2}}>{L.clients_online}</div>
            </div>
            <div style={{flex:1}}>
              <div style={{fontSize:20,fontWeight:700,letterSpacing:"-0.3px",fontVariantNumeric:"tabular-nums",color:T.ink}}>
                v{s.version||"—"}
              </div>
              <div style={{fontSize:11,color:T.ink3,marginTop:2}}>goVLESS</div>
            </div>
          </div>
        </div>
        {/* QA tiles */}
        <div style={{display:"grid",gridTemplateColumns:"1fr 2fr",gap:10}}>
          <button onClick={()=>navigate("#/clients?add=1")} style={{
            display:"flex",flexDirection:"column",alignItems:"center",gap:8,padding:"14px 6px",
            background:T.accent,color:T.primaryInk,border:"none",borderRadius:14,
            fontFamily:T.font,fontSize:12.5,fontWeight:600,cursor:"pointer",boxShadow:T.shadow,
          }}>
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke={T.primaryInk} strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>
            {L.add}
          </button>
          <button onClick={()=>navigate("#/clients")} style={{
            display:"flex",flexDirection:"column",alignItems:"center",gap:8,padding:"14px 6px",
            background:T.card,color:T.ink,border:`1px solid ${T.border}`,borderRadius:14,
            fontFamily:T.font,fontSize:12.5,fontWeight:600,cursor:"pointer",
          }}>
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke={T.accent} strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="8" cy="15" r="4"/><path d="M10.85 12.15L19 4M15 8l3 3M14 9l3 3"/>
            </svg>
            {L.keys_tile}
          </button>
        </div>
        {/* Clients section */}
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"baseline",padding:"4px 4px 0"}}>
          <h2 style={{margin:0,fontSize:13,fontWeight:600,color:T.ink3,textTransform:"uppercase",letterSpacing:"0.06em"}}>{L.clients}</h2>
          <a onClick={()=>navigate("#/clients")} style={{fontSize:13,color:T.accent2,fontWeight:600,cursor:"pointer"}}>{L.all_n} ({cs.length})</a>
        </div>
        {cs.length===0 ? (
          <div style={{textAlign:"center",color:T.ink3,padding:"32px 12px",fontSize:13}}>{L.empty}</div>
        ) : (
          <div style={{background:T.card,borderRadius:22,padding:"6px 0",boxShadow:T.shadow,border:T.isDark?`1px solid ${T.border}`:"none"}}>
            {cs.slice(0,5).map((c,i)=>(
              <div key={c.uuid} onClick={()=>navigate("#/clients/"+c.uuid)} style={{
                display:"flex",alignItems:"center",gap:12,padding:"11px 18px",cursor:"pointer",
                borderTop:i>0?`1px solid ${T.hair}`:"none",
              }}>
                <Avatar T={T} name={c.name} hue={hueFor(c.name)}/>
                <div style={{flex:1,minWidth:0}}>
                  <div style={{fontSize:14,fontWeight:600,color:T.ink,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{c.name}</div>
                  <div style={{fontSize:11.5,color:T.ink3,marginTop:2,fontFamily:T.mono}}>
                    {formatBytes(c.traffic_used)} · {c.expiry_ts?L.expiry_until+" "+formatTs(c.expiry_ts):"∞"}
                  </div>
                </div>
                <div style={{width:8,height:8,borderRadius:"50%",
                  background:c.enabled&&c.online?T.accent:c.enabled?T.switchOff:T.danger,
                  boxShadow:c.enabled&&c.online?`0 0 6px ${T.accent}`:"none"}}/>
              </div>
            ))}
          </div>
        )}
        {/* Bottom nav stubs to other design screens (visual-only previews) */}
        <div style={{marginTop:24}}>
          <h2 style={{margin:"0 4px 8px",fontSize:13,fontWeight:600,color:T.ink3,textTransform:"uppercase",letterSpacing:"0.06em"}}>{L.menu}</h2>
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:10}}>
            {[
              ["Inbounds", "#/inbounds"],
              [L.site_template, "#/site"],
              [L.system, "#/system"],
              [L.panel_access, "#/panel"],
              [L.repair, "#/repair"],
              [L.backups, "#/backup"],
              [L.notifications, "#/notifications"],
              [L.admins, "#/admins"],
              [L.settings, "#/settings"],
              [L.about, "#/about"],
              [L.disclaimer, "#/disclaimer"],
            ].map(([label, h]) => (
              <button key={h} onClick={()=>navigate(h)} style={{
                display:"flex",alignItems:"center",justifyContent:"center",minHeight:56,padding:"12px 8px",
                background:T.card,color:T.ink,border:`1px solid ${T.border}`,borderRadius:14,
                fontFamily:T.font,fontSize:13,fontWeight:600,cursor:"pointer",
                boxShadow:T.shadow,
              }}>{label}</button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─────────── CLIENTS LIST ─────────── */
function ClientsListScreen({ T, L, themePref, onThemeClick, navigate }) {
  const [cs, setCs] = useState(null);
  const [filter, setFilter] = useState("");
  useEffect(() => {
    rpc("client.list", {}).then(setCs).catch(e => { toast(L.err+e.message, "err"); setCs([]); });
  }, []);
  useEffect(() => {
    if (window.location.hash.indexOf("add=1") !== -1) openAddClient(T, L, navigate);
  }, []);
  const f = (cs||[]).filter(c => !filter || c.name.toLowerCase().includes(filter.toLowerCase()));
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title={L.clients} sub={cs?`${cs.length} ${L.count_active}`:""} onBack={()=>navigate("#/")}
        themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,overflowY:"auto",padding:"6px 16px 80px",display:"flex",flexDirection:"column",gap:10}}>
        <input value={filter} onChange={e=>setFilter(e.target.value)} placeholder={L.search}
          style={{width:"100%",background:T.card,color:T.ink,border:`1px solid ${T.border}`,
            padding:"10px 14px",borderRadius:14,fontSize:14,minHeight:44,fontFamily:T.font}}/>
        {cs===null ? <ClientsSkeleton T={T}/> :
         f.length===0 ? (cs.length===0
           ? <EmptyState T={T} icon="👥" title={L.empty} sub={L.empty_hint}
               actionLabel={L.add} onAction={()=>openAddClient(T, L, navigate)}/>
           : <EmptyState T={T} icon="🔍" title={L.no_results} sub={`${L.search_no_match.replace("{q}", filter)}`}/>) :
         <div style={{background:T.card,borderRadius:22,padding:"6px 0",boxShadow:T.shadow,border:T.isDark?`1px solid ${T.border}`:"none"}}>
           {f.map((c,i)=>(
             <div key={c.uuid} onClick={()=>navigate("#/clients/"+c.uuid)} style={{
               display:"flex",alignItems:"center",gap:12,padding:"11px 18px",cursor:"pointer",
               borderTop:i>0?`1px solid ${T.hair}`:"none",
             }}>
               <Avatar T={T} name={c.name} hue={hueFor(c.name)}/>
               <div style={{flex:1,minWidth:0}}>
                 <div style={{fontSize:14,fontWeight:600,color:T.ink}}>{c.name}</div>
                 <div style={{fontSize:11.5,color:T.ink3,marginTop:2,fontFamily:T.mono}}>
                   {formatBytes(c.traffic_used)} · {c.expiry_ts?L.expiry_until+" "+formatTs(c.expiry_ts):"∞"}
                 </div>
               </div>
               <div style={{width:8,height:8,borderRadius:"50%",
                 background:c.enabled&&c.online?T.accent:c.enabled?T.switchOff:T.danger,
                 boxShadow:c.enabled&&c.online?`0 0 6px ${T.accent}`:"none"}}/>
             </div>
           ))}
         </div>}
      </div>
      <button onClick={()=>openAddClient(T, L, navigate)} style={{
        position:"fixed",bottom:"calc(16px + env(safe-area-inset-bottom))",right:16,
        minHeight:48,padding:"0 20px",borderRadius:999,background:T.accent,color:T.primaryInk,
        border:0,fontFamily:T.font,fontSize:14,fontWeight:600,cursor:"pointer",
        boxShadow:T.isDark?"0 8px 24px rgba(110,224,166,0.20)":"0 8px 24px rgba(62,140,94,0.25)",zIndex:50,
      }}>{L.add}</button>
    </div>
  );
}

/* ─────────── CLIENT CARD ─────────── */
function ClientCardScreen({ T, L, uuid, themePref, onThemeClick, navigate }) {
  const [cl, setCl] = useState(null);
  const [err, setErr] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => {
    rpc("client.list", {})
      .then(list => {
        const c = list.find(x=>x.uuid===uuid);
        if (!c) setErr(new Error(L.not_found)); else setCl(c);
      })
      .catch(setErr);
  }, [uuid, reloadKey]);
  const reload = () => setReloadKey(k=>k+1);
  const onCopy = async (txt) => {
    haptic();
    try { await navigator.clipboard.writeText(txt); toast(L.copied, "ok"); }
    catch(e) { toast(L.err+e.message, "err"); }
  };
  const onShowQR = async (preferSub) => {
    haptic();
    try {
      const r = await rpc("client.qr", {uuid, prefer_subscription:preferSub});
      const showSubCopy = r.kind !== "subscription" && r.sub_url;
      openModal(<QRModal T={T} L={L} qr={r} onCopyLink={()=>onCopy(r.link)} onCopySub={showSubCopy?()=>onCopy(r.sub_url):null}/>);
    } catch(e) { toast(L.err+e.message, "err"); }
  };
  const runIssuedConfirm = async (method, description, onOk) => {
    try { await rpc(method, {uuid}); }
    catch(e) {
      if (!(e.code===412 && e.detail && e.detail.confirm)) { toast(L.err+e.message, "err"); return; }
      const ok = await typedConfirm(T, L, e.detail.confirm, description);
      if (!ok) { toast(L.cancelled, "warn"); return; }
      try { await rpc(method, {uuid, confirm:e.detail.confirm, confirm_token:e.detail.confirm_token}); onOk(); }
      catch(ee) { toast(L.err+ee.message, "err"); }
    }
  };
  const onToggle = async () => {
    const method = cl.enabled?"client.disable":"client.enable";
    try { await rpc(method, {uuid}); toast(cl.enabled?L.disabled:L.enabled, "ok"); reload(); }
    catch(e) {
      if (e.code===412 && e.detail && e.detail.confirm) {
        const ok = await typedConfirm(T, L, e.detail.confirm, e.detail.description||L.confirm_q);
        if (!ok) { toast(L.cancelled, "warn"); return; }
        try { await rpc(method, {uuid, confirm:e.detail.confirm, confirm_token:e.detail.confirm_token}); toast(L.done, "ok"); reload(); }
        catch(ee) { toast(L.err+ee.message, "err"); }
      } else toast(L.err+e.message, "err");
    }
  };
  const onDelete = async () => {
    await runIssuedConfirm("client.delete", L.confirm_delete_fmt.replace("{n}", cl.name), () => {
      toast(L.deleted,"ok");
      navigate("#/clients");
    });
  };
  const onReset = async () => {
    await runIssuedConfirm("client.reset_traffic", L.confirm_reset_fmt.replace("{n}", cl.name), () => {
      toast(L.reset_done,"ok");
      reload();
    });
  };
  const onRotateSub = async () => {
    await runIssuedConfirm("subscription.rotate", L.confirm_rotate_sub_fmt.replace("{n}", cl.name), () => {
      toast(L.rotated,"ok");
      reload();
    });
  };

  if (err) return <ErrorPane T={T} L={L} error={err} onBack={()=>navigate("#/clients")} themePref={themePref} onThemeClick={onThemeClick}/>;
  if (!cl) return <LoadingPane T={T} L={L} onBack={()=>navigate("#/clients")} themePref={themePref} onThemeClick={onThemeClick}/>;
  const card = (children) => (
    <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:18,padding:14,marginBottom:12,boxShadow:T.shadow}}>
      {children}
    </div>
  );
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title={cl.name} sub={cl.uuid.slice(0,8)+"…"} onBack={()=>navigate("#/clients")}
        themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,overflowY:"auto",padding:"14px 14px 32px"}}>
        {card(<>
          <div style={{display:"flex",alignItems:"center",gap:14,marginBottom:14}}>
            <Avatar T={T} name={cl.name} hue={hueFor(cl.name)} size={56}/>
            <div style={{flex:1,minWidth:0}}>
              <div style={{fontSize:16,fontWeight:700,color:T.ink,letterSpacing:"-0.2px"}}>{cl.name}</div>
              <div style={{marginTop:6}}>
                <Pill T={T} kind={cl.enabled?"ok":"warn"}>
                  {cl.enabled?L.is_on:L.is_off}{cl.online?" · online":""}
                </Pill>
              </div>
            </div>
          </div>
          <div style={{display:"grid",gridTemplateColumns:"max-content 1fr",gap:"8px 14px",fontSize:13}}>
            <div style={{color:T.ink3}}>{L.traffic}</div>
            <div style={{color:T.ink,fontFamily:T.mono,fontSize:12}}>
              {formatBytes(cl.traffic_used)} {cl.traffic_limit?"/ "+formatBytes(cl.traffic_limit):"/ ∞"}
            </div>
            <div style={{color:T.ink3}}>{L.expires}</div>
            <div style={{color:T.ink,fontFamily:T.mono,fontSize:12}}>{cl.expiry_ts?formatTs(cl.expiry_ts):L.never}</div>
            <div style={{color:T.ink3}}>{L.uuid_label}</div>
            <div style={{color:T.ink,fontFamily:T.mono,fontSize:11,wordBreak:"break-all"}}>{cl.uuid}</div>
          </div>
        </>)}
        {cl.sub_url && card(<>
          <h3 style={{fontSize:14,margin:"0 0 8px",color:T.ink}}>{L.subscr}</h3>
          <div onClick={()=>onCopy(cl.sub_url)} style={linkBlock(T)}>{cl.sub_url}</div>
          <div style={{display:"flex",gap:8,marginTop:8}}>
            <button onClick={()=>onCopy(cl.sub_url)} style={btn(T,"secondary")}>{L.copy}</button>
            <button onClick={()=>onShowQR(true)} style={btn(T,"secondary")}>{L.qr}</button>
            <button onClick={onRotateSub} style={btn(T,"warn")}>{L.rotate}</button>
          </div>
        </>)}
        {cl.link && card(<>
          <h3 style={{fontSize:14,margin:"0 0 8px",color:T.ink}}>{L.vless}</h3>
          <div onClick={()=>onCopy(cl.link)} style={linkBlock(T)}>{cl.link}</div>
          <div style={{display:"flex",gap:8,marginTop:8}}>
            <button onClick={()=>onCopy(cl.link)} style={btn(T,"secondary")}>{L.copy}</button>
            <button onClick={()=>onShowQR(false)} style={btn(T,"secondary")}>{L.qr}</button>
          </div>
        </>)}
        {card(<>
          <h3 style={{fontSize:14,margin:"0 0 10px",color:T.ink}}>{L.actions}</h3>
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8}}>
            <button onClick={onToggle} style={btn(T, cl.enabled?"warn":"ok")}>
              {cl.enabled?L.btn_disable:L.btn_enable}
            </button>
            <button onClick={()=>openModal(<EditFieldModal T={T} L={L} kind="limit" client={cl} onSave={reload}/>)} style={btn(T,"secondary")}>{L.btn_limit}</button>
            <button onClick={()=>openModal(<EditFieldModal T={T} L={L} kind="expiry" client={cl} onSave={reload}/>)} style={btn(T,"secondary")}>{L.btn_expiry}</button>
            <button onClick={onReset} style={btn(T,"warn")}>{L.btn_reset}</button>
            <button onClick={()=>openModal(<EditFieldModal T={T} L={L} kind="name" client={cl} onSave={reload}/>)} style={btn(T,"secondary")}>{L.btn_rename}</button>
            <button onClick={onDelete} style={btn(T,"danger")}>{L.btn_delete}</button>
          </div>
        </>)}
      </div>
    </div>
  );
}

function QRModal({ T, L, qr, onCopyLink, onCopySub }) {
  return (
    <div style={{width:"100%",maxWidth:480,background:T.card,color:T.ink,
      border:`1px solid ${T.border}`,borderRadius:16,padding:18,
      boxShadow:"0 -12px 40px rgba(0,0,0,0.45)",
      animation:"slidein 220ms cubic-bezier(0.32,0.72,0,1)"}}>
      <h3 style={{fontSize:16,margin:"0 0 10px",color:T.ink}}>
        {qr.kind==="subscription"?L.qr_sub:L.qr_vless}
      </h3>
      {qr.qr_svg ? (
        <div style={{width:"min(78vw, 320px)",maxWidth:"100%",margin:"6px auto",
          background:"#fff",borderRadius:10,padding:12,boxShadow:"0 2px 10px rgba(0,0,0,0.2)"}}
          dangerouslySetInnerHTML={{__html:qr.qr_svg}}/>
      ) : (
        <pre style={{fontFamily:T.mono,fontSize:6,lineHeight:"6px",whiteSpace:"pre",
          background:"#fff",color:"#000",padding:12,borderRadius:10,
          margin:"6px auto",width:"max-content",maxWidth:"100%",overflowX:"auto"}}>
          {qr.qr_text||L.qr_unavail}
        </pre>
      )}
      <div style={{display:"flex",gap:8,marginTop:14}}>
        <button onClick={onCopyLink} style={btn(T,"secondary")}>
          {qr.kind==="subscription"?L.copy_sub:L.copy_key}
        </button>
        {qr.kind!=="subscription" && onCopySub && <button onClick={onCopySub} style={btn(T,"secondary")}>{L.copy_sub}</button>}
        <button onClick={closeModal} style={{...btn(T,"secondary"),flex:"0 0 auto",padding:"0 18px"}}>{L.cancel}</button>
      </div>
    </div>
  );
}

/* ─────────── Generic wrapped design canvas screen ─────────── */
/* Mounts an existing design component (visual-only preview) */
function DesignFrame({ T, L, name, title, component, themePref, onThemeClick, onBack, lang="ru", extraProps }) {
  const Comp = window[component];
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title={title||name} onBack={onBack} themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1, overflow:"hidden", position:"relative"}}>
        {Comp ? <Comp theme={T.id} lang={lang} {...(extraProps||{})}/> : (
          <div style={{padding:24,textAlign:"center",color:T.ink3}}>
            <div style={{fontSize:32,marginBottom:8}}>🚧</div>
            <div>Экран «{name}» в разработке</div>
            <div style={{fontSize:12,marginTop:6}}>Дизайн готов в /design/, RPC-wiring запланирован в Batch 3.</div>
          </div>
        )}
      </div>
    </div>
  );
}

function LoadingPane({ T, L, onBack, themePref, onThemeClick, embedded }) {
  const spinner = <div style={{width:28,height:28,borderRadius:"50%",
    border:`3px solid ${T.bgSoft}`,borderTopColor:T.accent,
    animation:"spin 800ms linear infinite"}}/>;
  if (embedded) return <div style={{display:"flex",justifyContent:"center",padding:40}}>{spinner}</div>;
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      {(onBack!==undefined||themePref) && <Header T={T} L={L} title="goVLESS" onBack={onBack} themePref={themePref} onThemeClick={onThemeClick}/>}
      <div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center"}}>{spinner}</div>
    </div>
  );
}
function ErrorPane({ T, L, error, onBack, themePref, onThemeClick }) {
  const is401 = error && error.code===401;
  // Fatal auth dead-end (401/HMAC): don't strand the user in a Mini App with
  // no way out — close it after a short delay so they return to Telegram.
  useEffect(() => {
    if (!is401 || !(TG && TG.close)) return;
    const t = setTimeout(() => { try { TG.close(); } catch(e){} }, 2500);
    return () => clearTimeout(t);
  }, [is401]);
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title="goVLESS" onBack={onBack} themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center",padding:24,textAlign:"center"}}>
        <div>
          <div style={{fontSize:32,marginBottom:8}}>{is401?"🔒":"⚠"}</div>
          <div style={{fontSize:15,fontWeight:600,color:T.ink,marginBottom:6}}>
            {is401?L.rpc_unavailable:L.err.trim()}
          </div>
          <div style={{fontSize:13,color:T.ink3,lineHeight:1.45,maxWidth:280}}>
            {is401?L.rpc_unavail_hint:(error?.message||String(error))}
          </div>
        </div>
      </div>
    </div>
  );
}



/* ─────────── SETTINGS (theme + language) ─────────── */
function SettingsScreen({ T, L, themePref, onThemeClick, onSetTheme, navigate, langPref, onLangChange }) {
  const [lang, setLang] = useState(langPref);
  const [pref, setPref] = useState(themePref);
  useEffect(() => { setPref(themePref); }, [themePref]);
  useEffect(() => { setLang(langPref); }, [langPref]);
  const apply = () => {
    const langChanged = lang !== langPref;
    if (pref !== themePref && typeof onSetTheme === "function") onSetTheme(pref);
    toast(L.applied || L.applied, "ok");
    if (langChanged) {
      // language change requires reload to rebuild STR[TG_LANG] lookup table
      setTimeout(() => onLangChange(lang), 400);
    } else {
      setTimeout(() => navigate("#/"), 400);
    }
  };
  const radio = (val, currentVal, onPick, labelText, sub) => (
    <button onClick={() => onPick(val)} style={{
      display:"flex", alignItems:"center", gap:10, padding:"12px 14px",
      background: currentVal === val ? (T.isDark ? "rgba(110,224,166,0.10)" : "#f3f0e6") : "transparent",
      border: 0, width: "100%", textAlign: "left",
      borderRadius: 9, color: T.ink, fontFamily: T.font, cursor: "pointer",
    }}>
      <div style={{
        width: 22, height: 22, borderRadius: 11,
        border: `1.5px solid ${currentVal === val ? T.accent : T.border}`,
        background: currentVal === val ? T.accent : "transparent",
        display: "flex", alignItems: "center", justifyContent: "center",
        flexShrink: 0,
      }}>
        {currentVal === val && (
          <svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke={T.primaryInk} strokeWidth="2.2" strokeLinecap="round">
            <path d="M2.5 6.5L5 9l4.5-5.5"/>
          </svg>
        )}
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: T.ink }}>{labelText}</div>
        {sub && <div style={{ fontSize: 11.5, color: T.ink3, marginTop: 2 }}>{sub}</div>}
      </div>
    </button>
  );
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title={L.settings} sub={L.theme_and_lang} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1, overflowY:"auto", padding:"14px 16px 24px", display:"flex", flexDirection:"column", gap:18}}>
        <div>
          <h2 style={{margin:"0 4px 8px",fontSize:11,fontWeight:600,color:T.ink3,textTransform:"uppercase",letterSpacing:"0.06em"}}>{L.lang_section}</h2>
          <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:6,display:"flex",flexDirection:"column",gap:2}}>
            {radio("ru", lang, setLang, L.lang_ru, "RU")}
            {radio("en", lang, setLang, "English", "EN")}
          </div>
          <div style={{fontSize:11.5,color:T.ink3,padding:"6px 4px 0"}}>{L.apply_hint}</div>
        </div>
        <div>
          <h2 style={{margin:"0 4px 8px",fontSize:11,fontWeight:600,color:T.ink3,textTransform:"uppercase",letterSpacing:"0.06em"}}>{L.theme_label}</h2>
          <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:6,display:"flex",flexDirection:"column",gap:2}}>
            {radio("system", pref, setPref, L.theme_system, L.theme_sys_hint)}
            {radio("light", pref, setPref, L.theme_light, "L5 Pastel Mint")}
            {radio("dark", pref, setPref, L.theme_dark, "D2 Midnight Slate")}
          </div>
        </div>
      </div>
      <div style={{flex:"0 0 auto",padding:"10px 16px calc(14px + env(safe-area-inset-bottom))",borderTop:`1px solid ${T.hair}`,background:T.chromeBg,display:"flex",gap:8}}>
        <button onClick={()=>navigate("#/")} style={{...btn(T,"secondary"),flex:"0 0 auto",padding:"0 18px"}}>{L.cancel}</button>
        <button onClick={apply} style={{...btn(T,"ok"),flex:1}}>{L.btn_apply}</button>
      </div>
    </div>
  );
}

/* ─────────── SYSTEM (real RPC) ─────────── */
function SystemScreenReal({ T, L, themePref, onThemeClick, navigate }) {
  const [status, setStatus] = useState(null);
  const [tunnel, setTunnel] = useState(null);
  const [audit, setAudit] = useState([]);
  const [err, setErr] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const load = async () => {
    let firstErr = null;
    try { setStatus(await rpc("system.status", {})); } catch(e){ firstErr=e; }
    try { setTunnel(await rpc("tunnel.url_get", {})); } catch(e){}
    try { setAudit(await rpc("audit.tail", {limit:10})); } catch(e){}
    if (firstErr && firstErr.code===401) setErr(firstErr);
    setLoaded(true);
  };
  useEffect(() => { load(); }, []);
  if (err) return <ErrorPane T={T} L={L} error={err} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>;
  if (!loaded) return <LoadingPane T={T} L={L} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>;
  const s = status || {};
  const row = (k, v) => (
    <div style={{display:"grid",gridTemplateColumns:"max-content 1fr",gap:"8px 14px",padding:"6px 0",fontSize:13,borderBottom:`1px solid ${T.hair}`}}>
      <div style={{color:T.ink3}}>{k}</div><div style={{color:T.ink,textAlign:"right",fontFamily:T.mono,fontSize:12}}>{v}</div>
    </div>
  );
  const dot = (on) => <span style={{display:"inline-block",width:8,height:8,borderRadius:"50%",background: on ? T.accent : T.danger, boxShadow: on?`0 0 6px ${T.accent}`:"none",marginRight:6}}/>;
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title={L.system} sub={`v${s.version||"—"}`} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,overflowY:"auto",padding:"14px 14px 32px"}}>
        <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:14,marginBottom:12,boxShadow:T.shadow}}>
          <h3 style={{fontSize:14,margin:"0 0 10px",color:T.ink}}>{L.services}</h3>
          {row(<>{dot(s.xui_active)}x-ui</>, s.xui_active?"active":"inactive")}
          {row(<>{dot(s.xray_active)}xray</>, s.xray_active?"active":"inactive")}
          {row(<>{dot(s.nginx_active)}nginx</>, s.nginx_active?"active":s.mode==="lite"?"inactive (Lite)":"inactive")}
          {row(L.mode, s.mode || "—")}
          {row(L.transport, s.transport || "—")}
        </div>
        <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:14,marginBottom:12,boxShadow:T.shadow}}>
          <h3 style={{fontSize:14,margin:"0 0 10px",color:T.ink}}>{L.version}</h3>
          {row("goVLESS", "v"+(s.version||"—"))}
          {row("3X-UI / Xray", (s.xui_version||"—") + " / " + (s.xray_version||"—"))}
        </div>
        {tunnel && (
          <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:14,marginBottom:12,boxShadow:T.shadow}}>
            <h3 style={{fontSize:14,margin:"0 0 10px",color:T.ink}}>{L.tunnel}</h3>
            <div style={{marginBottom:8}}>
              <Pill T={T} kind={tunnel.url?"ok":"warn"}>
                {tunnel.source==="pro"?"Pro · nginx":tunnel.source==="quick"?"Lite · Cloudflare Quick":tunnel.source==="named"?"Lite · Named":L.not_active}
              </Pill>
            </div>
            {tunnel.url && <div style={{fontSize:11,color:T.ink3,fontFamily:T.mono,wordBreak:"break-all"}}>
              {/* Было "fp: sha256:<base64 от URL>" — подпись врала о содержимом
                  (никакого sha256 там нет). Показываем сам адрес: он всё равно
                  открыт пользователю, который сейчас внутри этого приложения. */}
              {(() => { try { return new URL(tunnel.url).host; } catch(e) { return tunnel.url; } })()}
            </div>}
          </div>
        )}
        <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:14,marginBottom:12,boxShadow:T.shadow}}>
          <h3 style={{fontSize:14,margin:"0 0 10px",color:T.ink}}>{L.actions}</h3>
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8,marginBottom:6}}>
            <button onClick={()=>navigate("#/repair")} style={btn(T,"secondary")}>↻ {L.repair}</button>
            <button onClick={async ()=>{
              try { await rpc("system.restart", {}); toast(L.restart_2s,"ok"); setTimeout(()=>{window.location.reload();}, 8000); }
              catch(e) {
                if (e.code===412 && e.detail && e.detail.confirm) {
                  const ok = await typedConfirm(T, L, e.detail.confirm, e.detail.description||L.restart_q);
                  if (!ok) { toast(L.cancelled,"warn"); return; }
                  try { await rpc("system.restart", {confirm:e.detail.confirm, confirm_token:e.detail.confirm_token});
                        toast(L.restart_2s,"ok"); setTimeout(()=>{window.location.reload();}, 8000); }
                  catch(ee) { toast(L.err+ee.message,"err"); }
                } else toast(L.err+e.message,"err");
              }
            }} style={btn(T,"warn")}>⟲ Restart all</button>
          </div>
        </div>
        <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:14,marginBottom:12,boxShadow:T.shadow}}>
          <h3 style={{fontSize:14,margin:"0 0 10px",color:T.ink}}>{L.recent_actions}</h3>
          {audit.length===0 ? <div style={{color:T.ink3,fontSize:13,padding:"8px 0"}}>{L.audit_empty}</div> :
           audit.map((e,i)=>(
             <div key={i} style={{display:"flex",gap:10,padding:"6px 0",fontSize:12,borderTop:i>0?`1px solid ${T.hair}`:"none"}}>
               <span style={{color:T.ink3,fontFamily:T.mono,width:80}}>{formatTs(e.ts)||"—"}</span>
               <span style={{color:T.accent2,flex:1}}>{e.action||"—"}</span>
               <span style={{color:T.ink3,maxWidth:"50%",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{e.target||""}</span>
             </div>
           ))
          }
        </div>
      </div>
    </div>
  );
}

/* ─────────── ADMINS (real RPC) ─────────── */
function AdminsScreenReal({ T, L, themePref, onThemeClick, navigate }) {
  const [ids, setIds] = useState(null);
  const [err, setErr] = useState(null);
  const [val, setVal] = useState("");
  const [busy, setBusy] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => {
    rpc("admin.list", {}).then(setIds).catch(e => { if (e.code===401) setErr(e); else { setIds([]); toast(L.err+e.message,"err"); } });
  }, [reloadKey]);
  const onInvite = async () => {
    const tgid = val.trim();
    if (!/^\d{4,12}$/.test(tgid)) { toast(L.bad_tg_id,"err"); return; }
    setBusy(true);
    try { await rpc("admin.invite", {tg_id: tgid}); toast(L.invited,"ok"); setVal(""); setReloadKey(k=>k+1); }
    catch(e) { toast(L.err+e.message,"err"); }
    setBusy(false);
  };
  if (err) return <ErrorPane T={T} L={L} error={err} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>;
  if (!ids) return <LoadingPane T={T} L={L} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>;
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title={L.admins} sub={`${ids.length} ${L.count_active}`} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,overflowY:"auto",padding:"14px 14px 32px"}}>
        <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:14,marginBottom:12,boxShadow:T.shadow}}>
          <h3 style={{fontSize:14,margin:"0 0 10px",color:T.ink}}>{L.current_admins}</h3>
          {ids.length===0 ? <div style={{color:T.ink3,fontSize:13,padding:"12px 0",textAlign:"center"}}>{L.nobody}</div> :
           <ul style={{listStyle:"none",padding:0,margin:0,fontSize:13,fontFamily:T.mono}}>
             {ids.map((id,i)=>(
               <li key={id} style={{padding:"8px 0",borderBottom:i<ids.length-1?`1px solid ${T.hair}`:"none",color:T.ink}}>
                 {id}
               </li>
             ))}
           </ul>
          }
        </div>
        <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:14,marginBottom:12,boxShadow:T.shadow}}>
          <h3 style={{fontSize:14,margin:"0 0 10px",color:T.ink}}>{L.invite}</h3>
          <input value={val} onChange={e=>setVal(e.target.value)} placeholder="Telegram user id"
            style={{width:"100%",background:T.card2,color:T.ink,border:`1px solid ${T.border}`,padding:"10px 12px",borderRadius:8,fontFamily:T.font,fontSize:14,minHeight:44,marginBottom:8}}/>
          <button onClick={onInvite} disabled={busy} style={{...btn(T,"ok"),width:"100%",opacity:busy?0.6:1}}>
            {busy?L.inviting:L.invite}
          </button>
          <div style={{fontSize:11.5,color:T.ink3,padding:"8px 4px 0"}}>
            {L.tg_id_hint}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─────────── INBOUNDS (real RPC) ─────────── */
function InboundsScreenReal({ T, L, themePref, onThemeClick, navigate }) {
  const [list, setList] = useState(null);
  const [err, setErr] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => {
    rpc("inbound.list", {}).then(setList).catch(e => { if (e.code===401) setErr(e); else { setList([]); toast(L.err+e.message,"err"); } });
  }, [reloadKey]);
  const onToggle = async (ib) => {
    if (ib.enable) {
      // Disable needs a server-issued DISABLE-INBOUND confirm token:
      // first call returns 412 with the token + expected string, then retry with both.
      try { await rpc("inbound.toggle", {inbound_id:ib.id, enable:false}); toast(L.disabled,"ok"); setReloadKey(k=>k+1); return; }
      catch(e) {
        if (!(e.code===412 && e.detail && e.detail.confirm)) { toast(L.err+e.message,"err"); return; }
        const ok = await typedConfirm(T, L, e.detail.confirm, e.detail.description || L.disable_inbound_fmt.replace("{id}", ib.id));
        if (!ok) { toast(L.cancelled,"warn"); return; }
        try { await rpc("inbound.toggle", {inbound_id:ib.id, enable:false, confirm:e.detail.confirm, confirm_token:e.detail.confirm_token}); toast(L.disabled,"ok"); setReloadKey(k=>k+1); }
        catch(ee) { toast(L.err+ee.message,"err"); }
      }
    } else {
      try { await rpc("inbound.toggle", {inbound_id:ib.id, enable:true}); toast(L.enabled,"ok"); setReloadKey(k=>k+1); }
      catch(e) { toast(L.err+e.message,"err"); }
    }
  };
  if (err) return <ErrorPane T={T} L={L} error={err} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>;
  if (!list) return <LoadingPane T={T} L={L} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>;
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title="Inbounds" sub={`${list.length} ${L.count_active}`} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,overflowY:"auto",padding:"14px 14px 32px"}}>
        {list.length===0 ? <div style={{textAlign:"center",color:T.ink3,padding:"32px 12px",fontSize:13}}>{L.no_inbounds}</div> :
         list.map(ib => (
           <div key={ib.id} style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:14,marginBottom:12,boxShadow:T.shadow}}>
             <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:10}}>
               <h3 style={{fontSize:14,margin:0,color:T.ink}}>{ib.remark || "Inbound #"+ib.id}</h3>
               <Pill T={T} kind={ib.enable?"ok":"warn"}>{ib.enable?L.is_on:L.is_off}</Pill>
             </div>
             <div style={{display:"grid",gridTemplateColumns:"max-content 1fr",gap:"6px 14px",fontSize:13,marginBottom:10}}>
               <div style={{color:T.ink3}}>{L.port}</div><div style={{color:T.ink,fontFamily:T.mono,fontSize:12,textAlign:"right"}}>{ib.port}</div>
               <div style={{color:T.ink3}}>{L.protocol}</div><div style={{color:T.ink,textAlign:"right"}}>{ib.protocol}</div>
               <div style={{color:T.ink3}}>Network</div><div style={{color:T.ink,textAlign:"right"}}>{ib.network || "—"}</div>
               <div style={{color:T.ink3}}>Security</div><div style={{color:T.ink,textAlign:"right"}}>{ib.security || "—"}</div>
               <div style={{color:T.ink3}}>{L.clients}</div><div style={{color:T.ink,textAlign:"right"}}>{ib.client_count}</div>
             </div>
             <div style={{display:"flex",gap:8}}>
               <button onClick={()=>navigate("#/inbounds/"+ib.id)} style={{...btn(T,"secondary"),flex:1}}>{L.more}</button>
               <button onClick={()=>onToggle(ib)} style={{...btn(T, ib.enable?"warn":"ok"),flex:1}}>
                 {ib.enable?L.btn_disable:L.btn_enable}
               </button>
             </div>
           </div>
         ))
        }
      </div>
    </div>
  );
}

/* ─────────── BACKUP (info-only — no backup RPC yet) ─────────── */
function BackupScreenReal({ T, L, themePref, onThemeClick, navigate }) {
  const [list, setList] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  useEffect(() => {
    rpc("backup.list", {})
      .then(r => setList(r.backups || []))
      .catch(e => { if (e.code===401) setErr(e); else { setList([]); toast(L.err+e.message,"err"); } });
  }, [reloadKey]);
  const onCreate = async () => {
    setBusy(true);
    try {
      const r = await rpc("backup.create", {});
      toast(`${L.created_fmt.replace("{n}", r.name).replace("{s}", formatBytes(r.size))}`, "ok");
      setReloadKey(k=>k+1);
    } catch(e) { toast(L.err+e.message,"err"); }
    setBusy(false);
  };
  const onRestore = async (bk) => {
    try { await rpc("backup.restore", {path: bk.path}); toast(L.restored,"ok"); }
    catch(e) {
      if (e.code===412 && e.detail && e.detail.confirm) {
        const ok = await typedConfirm(T, L, e.detail.confirm, e.detail.description || `${L.confirm_restore_fmt.replace("{n}", bk.name)}`);
        if (!ok) { toast(L.cancelled,"warn"); return; }
        try { const r = await rpc("backup.restore", {path: bk.path, confirm: e.detail.confirm, confirm_token: e.detail.confirm_token});
              toast(L.restored,"ok");
              // After restore — recommend reload
              setTimeout(()=>{ if(confirm(L.backup_done_q)) window.location.reload(); }, 800); }
        catch(ee) { toast(L.err+ee.message,"err"); }
      } else toast(L.err+e.message,"err");
    }
  };
  if (err) return <ErrorPane T={T} L={L} error={err} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>;
  if (list===null) return <LoadingPane T={T} L={L} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>;
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title={L.backups} sub={`${list.length} ${L.files_count}`} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,overflowY:"auto",padding:"14px 14px 32px"}}>
        <button onClick={onCreate} disabled={busy} style={{...btn(T,"ok"),width:"100%",marginBottom:12,opacity:busy?0.6:1}}>
          {busy?L.creating_backup:L.btn_create_backup}
        </button>
        {list.length===0 ? (
          <EmptyState T={T} icon="📦" title={L.no_backups}
            sub={L.backup_empty_sub}/>
        ) : (
          <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:"6px 0",boxShadow:T.shadow}}>
            {list.map((bk,i)=>(
              <div key={bk.path} style={{padding:"12px 14px",borderTop:i>0?`1px solid ${T.hair}`:"none"}}>
                <div style={{display:"flex",justifyContent:"space-between",alignItems:"baseline",marginBottom:6}}>
                  <div style={{fontSize:13,fontWeight:600,color:T.ink,fontFamily:T.mono}}>{bk.name}</div>
                  <div style={{fontSize:11,color:T.ink3,fontFamily:T.mono}}>{formatBytes(bk.size)}</div>
                </div>
                <div style={{fontSize:11,color:T.ink3,fontFamily:T.mono,marginBottom:8}}>
                  {new Date(bk.ts*1000).toLocaleString(L.time_locale)}
                </div>
                <button onClick={()=>onRestore(bk)} style={{...btn(T,"warn"),width:"100%",fontSize:13}}>
                  ↻ {L.restore_btn}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}



/* ─────────── REPAIR (Etap 2) ─────────── */
function RepairScreen({ T, L, themePref, onThemeClick, navigate }) {
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState(null);
  const onRun = async () => {
    setBusy(true);
    try {
      const r = await rpc("repair.run", {});
      setLastResult(r);
      if (r.ok) toast(L.restore_done, "ok");
      else toast(L.repair_warn, "warn");
    } catch(e) { toast(L.err+e.message,"err"); }
    setBusy(false);
  };
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title={L.repair} onBack={()=>navigate("#/system")} themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,overflowY:"auto",padding:"14px 14px 32px"}}>
        <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:14,marginBottom:12,boxShadow:T.shadow}}>
          <h3 style={{fontSize:14,margin:"0 0 10px",color:T.ink}}>{L.what_will_happen}</h3>
          <ul style={{margin:"0 0 12px",paddingLeft:20,color:T.ink2,fontSize:13,lineHeight:1.6}}>
            <li>{L.repair_ip}</li>
            <li>{L.repair_sub}</li>
            <li>{L.repair_links}</li>
          </ul>
          <button onClick={onRun} disabled={busy} style={{...btn(T,"ok"),width:"100%",opacity:busy?0.6:1}}>
            {busy?L.restoring:L.btn_run}
          </button>
        </div>
        {lastResult && (
          <div style={{background:T.card2,border:`1px solid ${T.border}`,borderRadius:14,padding:14,marginBottom:12,boxShadow:T.shadow}}>
            <h3 style={{fontSize:14,margin:"0 0 10px",color:T.ink}}>{L.result}</h3>
            <div style={{marginBottom:8}}>
              <Pill T={T} kind={lastResult.ok?"ok":"warn"}>
                {lastResult.ok?L.success:L.with_warnings}
              </Pill>
            </div>
            {lastResult.stdout && <pre style={{fontFamily:T.mono,fontSize:10,color:T.ink2,background:T.bg,padding:8,borderRadius:6,overflow:"auto",maxHeight:200,margin:0,whiteSpace:"pre-wrap"}}>{lastResult.stdout}</pre>}
            {lastResult.stderr && <pre style={{fontFamily:T.mono,fontSize:10,color:T.danger,background:T.bg,padding:8,borderRadius:6,overflow:"auto",maxHeight:120,margin:"8px 0 0",whiteSpace:"pre-wrap"}}>{lastResult.stderr}</pre>}
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────── ABOUT ─────────── */
function AboutScreenReal({ T, L, themePref, onThemeClick, navigate }) {
  const [s, setS] = useState(null);
  useEffect(() => { rpc("system.status",{}).then(setS).catch(()=>setS({version:"—"})); }, []);
  const row = (k, v) => (
    <div style={{display:"flex",justifyContent:"space-between",padding:"8px 0",fontSize:13,borderBottom:`1px solid ${T.hair}`}}>
      <span style={{color:T.ink3}}>{k}</span>
      <span style={{color:T.ink,fontFamily:T.mono,fontSize:12}}>{v}</span>
    </div>
  );
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title={L.about} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,overflowY:"auto",padding:"14px 14px 32px"}}>
        <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:18,padding:18,marginBottom:12,boxShadow:T.shadow,textAlign:"center"}}>
          <div style={{width:64,height:64,borderRadius:14,background:T.accent,margin:"0 auto 12px",
            display:"flex",alignItems:"center",justifyContent:"center",color:T.primaryInk,fontSize:24,fontWeight:800}}>gV</div>
          <div style={{fontSize:20,fontWeight:700,color:T.ink}}>goVLESS</div>
          <div style={{fontSize:13,color:T.ink3,fontFamily:T.mono,marginTop:6}}>v{s?.version || "—"}</div>
        </div>
        <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:"4px 14px 14px",marginBottom:12,boxShadow:T.shadow}}>
          {row(L.build, "goVLESS v"+(s?.version||"—"))}
          {row("3X-UI / Xray", (s?.xui_version||"—") + " / " + (s?.xray_version||"—"))}
          {row(L.license, "Source-Available")}
          {row(L.mode, s?.mode || "—")}
        </div>
        <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:14,marginBottom:12,boxShadow:T.shadow,display:"flex",flexDirection:"column",gap:8}}>
          <a href="https://github.com/potap1978/go-all" target="_blank" rel="noopener" onClick={(e)=>{ if (TG && TG.openLink) { e.preventDefault(); TG.openLink("https://github.com/potap1978/go-all"); } }} style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"10px 0",color:T.ink,textDecoration:"none",borderBottom:`1px solid ${T.hair}`}}>
            <span style={{fontSize:14,fontWeight:500}}>{L.src_github}</span>
            <span style={{color:T.ink3}}>→</span>
          </a>
          <button onClick={()=>navigate("#/disclaimer")} style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"10px 0",background:"transparent",border:0,color:T.ink,cursor:"pointer",fontFamily:T.font,fontSize:14,fontWeight:500,borderBottom:`1px solid ${T.hair}`,textAlign:"left"}}>
            <span>{L.read_disc}</span>
            <span style={{color:T.ink3}}>→</span>
          </button>
          <button onClick={()=>navigate("#/settings")} style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"10px 0",background:"transparent",border:0,color:T.ink,cursor:"pointer",fontFamily:T.font,fontSize:14,fontWeight:500,textAlign:"left"}}>
            <span>{L.settings}</span>
            <span style={{color:T.ink3}}>→</span>
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─────────── DISCLAIMER ─────────── */
function DisclaimerScreenReal({ T, L, themePref, onThemeClick, navigate }) {
  const lsKey = "gov_" + USER_ID + "_disclaimer_accepted_v1";
  const [accepted, setAccepted] = useState(() => { try { return localStorage.getItem(lsKey)==="1"; } catch(e){return false;} });
  const onAccept = () => {
    try { localStorage.setItem(lsKey, "1"); } catch(e){}
    setAccepted(true);
    toast(L.accepted, "ok");
    setTimeout(()=>navigate("#/"), 400);
  };
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title={L.tos} sub={L.read_first} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,overflowY:"auto",padding:"14px 14px 24px",fontSize:13,color:T.ink2,lineHeight:1.55}}>
        <div style={{background:T.warnSoft,border:`1px solid ${T.warn}`,borderRadius:12,padding:"12px 14px",marginBottom:14,color:T.ink}}>
          <div style={{color:T.warn,fontWeight:700,fontSize:11.5,textTransform:"uppercase",letterSpacing:"0.06em",marginBottom:4}}>{L.legal_warn}</div>
          {L.decline_close}
        </div>
        <h3 style={{fontSize:15,fontWeight:700,color:T.ink,margin:"16px 0 8px"}}>{L.disc_h1}</h3>
        <p style={{margin:"0 0 10px"}}>{L.disc_p1}</p>
        <h3 style={{fontSize:15,fontWeight:700,color:T.ink,margin:"16px 0 8px"}}>{L.disc_h2}</h3>
        <p style={{margin:"0 0 10px"}}>{L.disc_p2}</p>
        <h3 style={{fontSize:15,fontWeight:700,color:T.danger,margin:"16px 0 8px"}}>{L.disc_h3}</h3>
        <ul style={{margin:"0 0 10px",paddingLeft:18}}>
          <li>{L.disc_dont_1}</li>
          <li>{L.disc_dont_2}</li>
          <li>{L.disc_dont_3}</li>
        </ul>
        <h3 style={{fontSize:15,fontWeight:700,color:T.ink,margin:"16px 0 8px"}}>{L.disc_h4}</h3>
        <p style={{margin:"0 0 10px"}}>{L.disc_p3}</p>
        <div style={{fontFamily:T.mono,fontSize:11,color:T.ink3,textAlign:"center",padding:"12px",border:`1px dashed ${T.border}`,borderRadius:10,marginTop:14}}>
          as is · Source-Available · 2025-11
        </div>
      </div>
      <div style={{flex:"0 0 auto",padding:"10px 14px calc(14px + env(safe-area-inset-bottom))",borderTop:`1px solid ${T.hair}`,background:T.chromeBg,display:"flex",gap:8}}>
        {accepted ? (
          <button onClick={()=>navigate("#/")} style={{...btn(T,"secondary"),flex:1}}>{L.already_acc}</button>
        ) : (
          <>
            <button onClick={()=>navigate("#/")} style={{...btn(T,"secondary"),flex:"0 0 auto",padding:"0 18px"}}>{L.btn_decline}</button>
            <button onClick={onAccept} style={{...btn(T,"ok"),flex:1}}>{L.btn_accept}</button>
          </>
        )}
      </div>
    </div>
  );
}




/* ─────────── AddClient sheet (Etap 1) ─────────── */
function AddClientSheet({ T, L, onDone }) {
  const [name, setName] = useState("");
  const [limitGB, setLimitGB] = useState("");
  const [expiry, setExpiry] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const nm = name.trim();
    if (!nm || !/^[A-Za-z0-9_.\-]{1,32}$/.test(nm)) { toast(L.name_rules32, "err"); return; }
    setBusy(true);
    try {
      const params = { name: nm };
      const gb = parseFloat(limitGB.replace(",","."));
      if (!isNaN(gb) && gb > 0) params.traffic_limit = Math.floor(gb * 1024 * 1024 * 1024);
      if (expiry) params.expiry_ts = Math.floor(new Date(expiry).getTime()/1000);
      const r = await rpc("client.add", params);
      closeModal();
      toast(L.client_created, "ok");
      if (r && r.uuid) onDone(r.uuid);
    } catch(e) { toast(L.err + e.message, "err"); }
    setBusy(false);
  };
  return (
    <div style={{width:"100%",maxWidth:480,background:T.card,color:T.ink,
      border:`1px solid ${T.border}`,borderRadius:16,padding:18,
      boxShadow:"0 -12px 40px rgba(0,0,0,0.45)",
      animation:"slidein 220ms cubic-bezier(0.32,0.72,0,1)"}}>
      <h3 style={{fontSize:16,margin:"0 0 12px",color:T.ink,fontFamily:T.font}}>{L.new_client}</h3>
      <label style={{display:"block",fontSize:12,color:T.ink3,margin:"8px 0 4px"}}>{L.name}</label>
      <input value={name} onChange={e=>setName(e.target.value)} autoFocus placeholder={L.name_ph}
        style={{width:"100%",background:T.card2,color:T.ink,border:`1px solid ${T.border}`,
          padding:"10px 12px",borderRadius:8,fontFamily:T.mono,fontSize:13,minHeight:44}}/>
      <label style={{display:"block",fontSize:12,color:T.ink3,margin:"12px 0 4px"}}>{L.limit_gb}</label>
      <input value={limitGB} onChange={e=>setLimitGB(e.target.value)} type="number" min="0" step="0.1" placeholder="0"
        style={{width:"100%",background:T.card2,color:T.ink,border:`1px solid ${T.border}`,
          padding:"10px 12px",borderRadius:8,fontFamily:T.mono,fontSize:13,minHeight:44}}/>
      <label style={{display:"block",fontSize:12,color:T.ink3,margin:"12px 0 4px"}}>{L.expiry_opt}</label>
      <input value={expiry} onChange={e=>setExpiry(e.target.value)} type="date"
        style={{width:"100%",background:T.card2,color:T.ink,border:`1px solid ${T.border}`,
          padding:"10px 12px",borderRadius:8,fontFamily:T.font,fontSize:13,minHeight:44}}/>
      <div style={{display:"flex",gap:8,marginTop:14}}>
        <button onClick={closeModal} disabled={busy}
          style={{...btn(T,"secondary"),flex:"0 0 auto",padding:"0 18px"}}>{L.cancel}</button>
        <button onClick={submit} disabled={busy} style={{...btn(T,"ok"),flex:1,opacity:busy?0.6:1}}>
          {busy?L.creating:L.btn_create}
        </button>
      </div>
    </div>
  );
}
function openAddClient(T, L, navigate) {
  haptic();
  openModal(<AddClientSheet T={T} L={L} onDone={(uuid) => {
    if (uuid) navigate("#/clients/" + uuid);
    else navigate("#/clients");
  }}/>);
}

/* ─────────── EditField modal (rename/limit/expiry) (Etap 1) ─────────── */
function EditFieldModal({ T, L, kind, client, onSave }) {
  let initial = "";
  let label = "";
  let inputType = "text";
  let placeholder = "";
  if (kind === "name") {
    initial = client.name; label = L.name; placeholder = L.client_name;
  } else if (kind === "limit") {
    initial = client.traffic_limit ? (client.traffic_limit / 1024 / 1024 / 1024).toFixed(2).replace(".00","") : "";
    label = L.limit_gb2; inputType = "number"; placeholder = "0";
  } else if (kind === "expiry") {
    initial = client.expiry_ts ? new Date(client.expiry_ts*1000).toISOString().slice(0,10) : "";
    label = L.expiry_empty; inputType = "date";
  }
  const [val, setVal] = useState(initial);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      const fields = {};
      if (kind === "name") {
        if (!val.trim() || !/^[A-Za-z0-9_.\-]{1,32}$/.test(val.trim())) { toast(L.name_rules,"err"); setBusy(false); return; }
        fields.name = val.trim();
      } else if (kind === "limit") {
        const gb = parseFloat((val||"").toString().replace(",","."));
        fields.traffic_limit = (!isNaN(gb) && gb > 0) ? Math.floor(gb * 1024 * 1024 * 1024) : 0;
      } else if (kind === "expiry") {
        fields.expiry_ts = val ? Math.floor(new Date(val).getTime()/1000) : 0;
      }
      await rpc("client.update", {uuid: client.uuid, fields});
      closeModal();
      toast(L.applied, "ok");
      onSave();
    } catch(e) {
      if (e.code === 412 && e.detail && e.detail.confirm) {
        const ok = await typedConfirm(T, L, e.detail.confirm, e.detail.description || L.confirm_big);
        if (!ok) { toast(L.cancelled, "warn"); setBusy(false); return; }
        try {
          await rpc("client.update", {uuid: client.uuid, fields: kind==="name"?{name:val.trim()}:kind==="limit"?{traffic_limit:val?Math.floor(parseFloat(val)*1024*1024*1024):0}:{expiry_ts:val?Math.floor(new Date(val).getTime()/1000):0},
            confirm: e.detail.confirm, confirm_token: e.detail.confirm_token});
          closeModal();
          toast(L.applied, "ok");
          onSave();
        } catch(ee) { toast(L.err + ee.message, "err"); }
      } else toast(L.err + e.message, "err");
    }
    setBusy(false);
  };
  return (
    <div style={{width:"100%",maxWidth:480,background:T.card,color:T.ink,
      border:`1px solid ${T.border}`,borderRadius:16,padding:18,
      boxShadow:"0 -12px 40px rgba(0,0,0,0.45)",
      animation:"slidein 220ms cubic-bezier(0.32,0.72,0,1)"}}>
      <h3 style={{fontSize:16,margin:"0 0 8px",color:T.ink}}>
        {kind==="name"?L.btn_rename:kind==="limit"?L.btn_limit:L.expiry} · {client.name}
      </h3>
      <label style={{display:"block",fontSize:12,color:T.ink3,margin:"8px 0 4px"}}>{label}</label>
      <input value={val} onChange={e=>setVal(e.target.value)} autoFocus type={inputType} placeholder={placeholder}
        style={{width:"100%",background:T.card2,color:T.ink,border:`1px solid ${T.border}`,
          padding:"10px 12px",borderRadius:8,fontFamily:inputType==="number"||inputType==="date"?T.font:T.mono,
          fontSize:13,minHeight:44}}/>
      <div style={{display:"flex",gap:8,marginTop:14}}>
        <button onClick={closeModal} disabled={busy}
          style={{...btn(T,"secondary"),flex:"0 0 auto",padding:"0 18px"}}>{L.cancel}</button>
        <button onClick={submit} disabled={busy} style={{...btn(T,"ok"),flex:1,opacity:busy?0.6:1}}>
          {busy?L.saving:L.btn_save}
        </button>
      </div>
    </div>
  );
}

/* ─────────── InboundDetail (Etap 1) ─────────── */
function InboundDetailScreen({ T, L, id, themePref, onThemeClick, navigate }) {
  const [ib, setIb] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    rpc("inbound.list", {})
      .then(list => {
        const found = list.find(x => String(x.id) === String(id));
        if (!found) setErr(new Error(L.inbound_not_found));
        else setIb(found);
      })
      .catch(setErr);
  }, [id]);
  if (err) return <ErrorPane T={T} L={L} error={err} onBack={()=>navigate("#/inbounds")} themePref={themePref} onThemeClick={onThemeClick}/>;
  if (!ib) return <LoadingPane T={T} L={L} onBack={()=>navigate("#/inbounds")} themePref={themePref} onThemeClick={onThemeClick}/>;
  const reality = ib.stream && ib.stream.realitySettings;
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title={`Inbound #${ib.id}`} sub={ib.remark||""} onBack={()=>navigate("#/inbounds")}
        themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,overflowY:"auto",padding:"14px 14px 32px"}}>
        <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:14,marginBottom:12,boxShadow:T.shadow}}>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:10}}>
            <h3 style={{fontSize:14,margin:0,color:T.ink}}>{L.params}</h3>
            <Pill T={T} kind={ib.enable?"ok":"warn"}>{ib.enable?L.is_on:L.is_off}</Pill>
          </div>
          <div style={{display:"grid",gridTemplateColumns:"max-content 1fr",gap:"6px 14px",fontSize:13}}>
            <div style={{color:T.ink3}}>{L.port}</div><div style={{color:T.ink,fontFamily:T.mono,fontSize:12,textAlign:"right"}}>{ib.port}</div>
            <div style={{color:T.ink3}}>{L.protocol}</div><div style={{color:T.ink,textAlign:"right"}}>{ib.protocol}</div>
            <div style={{color:T.ink3}}>Network</div><div style={{color:T.ink,textAlign:"right"}}>{ib.network || "—"}</div>
            <div style={{color:T.ink3}}>Security</div><div style={{color:T.ink,textAlign:"right"}}>{ib.security || "—"}</div>
            <div style={{color:T.ink3}}>{L.clients}</div><div style={{color:T.ink,textAlign:"right"}}>{ib.client_count}</div>
          </div>
        </div>
        {reality && (
          <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:14,marginBottom:12,boxShadow:T.shadow}}>
            <h3 style={{fontSize:14,margin:"0 0 10px",color:T.ink}}>Reality</h3>
            <div style={{fontSize:12,color:T.ink3,marginBottom:4}}>Public key</div>
            <div onClick={()=>{navigator.clipboard.writeText(reality.publicKey||"").then(()=>toast(L.copied,"ok"))}}
              style={linkBlock(T)}>{reality.publicKey||"—"}</div>
            {/* Приватный ключ Reality здесь больше не показывается: он нужен
                только самому xray, а утечка позволяет выдать себя за сервер. */}
            <div style={{fontSize:12,color:T.ink3,margin:"12px 0 4px"}}>SNI · dest</div>
            <div style={linkBlock(T)}>{(reality.serverNames||[]).join(", ")||"—"} · {reality.dest||"—"}</div>
            {reality.shortIds && reality.shortIds.length>0 && <>
              <div style={{fontSize:12,color:T.ink3,margin:"12px 0 4px"}}>Short IDs</div>
              {reality.shortIds.map((s,i)=>(
                <div key={i} style={{...linkBlock(T),margin:"4px 0"}}>{s||"(empty)"}</div>
              ))}
            </>}
            {reality.dest && <>
              <div style={{fontSize:12,color:T.ink3,margin:"12px 0 4px"}}>{L.mask_domain}</div>
              <div style={linkBlock(T)}>{reality.dest}</div>
            </>}
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────── ErrorTimeout pane (Etap 1) ─────────── */
function ErrorTimeoutPane({ T, L, onRetry, onBack, themePref, onThemeClick }) {
  const [secs, setSecs] = useState(5);
  useEffect(() => {
    const id = setInterval(() => {
      setSecs(s => {
        if (s <= 1) { onRetry && onRetry(); return 5; }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title="goVLESS" onBack={onBack} themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,display:"flex",alignItems:"center",justifyContent:"center",padding:24,textAlign:"center"}}>
        <div>
          <div style={{fontSize:32,marginBottom:8}}>⏱</div>
          <div style={{fontSize:15,fontWeight:600,color:T.ink,marginBottom:6}}>{L.server_down}</div>
          <div style={{fontSize:13,color:T.ink3,lineHeight:1.45,marginBottom:18,maxWidth:280}}>
            Повторная попытка через {secs}s
          </div>
          <button onClick={onRetry} style={{...btn(T,"ok"),padding:"0 24px"}}>{L.retry_now}</button>
        </div>
      </div>
    </div>
  );
}

/* ─────────── Better Skeleton (Etap 1) ─────────── */
function ClientsSkeleton({ T }) {
  const lines = [60, 75, 50, 80, 65];
  return (
    <div style={{background:T.card,borderRadius:22,padding:"6px 0",boxShadow:T.shadow,border:T.isDark?`1px solid ${T.border}`:"none"}}>
      {lines.map((w,i)=>(
        <div key={i} style={{display:"flex",alignItems:"center",gap:12,padding:"11px 18px",
          borderTop:i>0?`1px solid ${T.hair}`:"none"}}>
          <div style={{width:36,height:36,borderRadius:T.avatarRadius,background:T.bgSoft,animation:"shimmer 1.4s ease-in-out infinite"}}/>
          <div style={{flex:1,minWidth:0}}>
            <div style={{width:`${w}%`,height:14,background:T.bgSoft,borderRadius:4,marginBottom:6,animation:"shimmer 1.4s ease-in-out infinite"}}/>
            <div style={{width:`${w*0.7}%`,height:11,background:T.bgSoft,borderRadius:4,animation:"shimmer 1.4s ease-in-out infinite"}}/>
          </div>
          <div style={{width:8,height:8,borderRadius:"50%",background:T.bgSoft,animation:"shimmer 1.4s ease-in-out infinite"}}/>
        </div>
      ))}
    </div>
  );
}

/* ─────────── Empty state with icon (Etap 1) ─────────── */
function EmptyState({ T, icon = "👥", title, sub, actionLabel, onAction }) {
  return (
    <div style={{display:"flex",flexDirection:"column",alignItems:"center",padding:"48px 16px",textAlign:"center"}}>
      <div style={{fontSize:48,marginBottom:8,opacity:0.6}}>{icon}</div>
      <div style={{fontSize:15,fontWeight:600,color:T.ink,marginBottom:6}}>{title}</div>
      {sub && <div style={{fontSize:13,color:T.ink3,marginBottom:18,maxWidth:240,lineHeight:1.45}}>{sub}</div>}
      {actionLabel && onAction && (
        <button onClick={onAction} style={{...btn(T,"ok"),padding:"0 22px"}}>{actionLabel}</button>
      )}
    </div>
  );
}



/* ─────────── NOTIFICATIONS (Etap 3) ─────────── */
function NotificationsScreen({ T, L, themePref, onThemeClick, navigate }) {
  const [prefs, setPrefs] = useState(null);
  // Список событий приходит от демона: раньше экран показывал тумблеры, которых
  // сервер не знает (traffic_limit_reached), и они молча ничего не делали.
  const [events, setEvents] = useState([]);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    rpc("notifications.get", {}).then(r => { setPrefs(r.prefs || {}); setEvents(r.events || Object.keys(r.prefs || {})); })
      .catch(e => { if (e.code===401) setErr(e); else { setPrefs({}); setEvents([]); toast(L.err+e.message,"err"); } });
  }, []);
  const toggle = async (k) => {
    if (!prefs) return;
    const next = {...prefs, [k]: !prefs[k]};
    setPrefs(next);
    setBusy(true);
    try { await rpc("notifications.set", {prefs: next}); toast(L.applied,"ok"); }
    catch(e) { toast(L.err+e.message,"err"); setPrefs(prefs); /* rollback */ }
    setBusy(false);
  };
  const row = (k, label, sub) => (
    <div onClick={()=>toggle(k)} style={{display:"flex",alignItems:"center",gap:12,padding:"12px 14px",cursor:"pointer",borderBottom:`1px solid ${T.hair}`}}>
      <div style={{flex:1}}>
        <div style={{fontSize:14,fontWeight:500,color:T.ink}}>{label}</div>
        <div style={{fontSize:11.5,color:T.ink3,marginTop:2}}>{sub}</div>
      </div>
      <div style={{
        width:34,height:20,borderRadius:10,position:"relative",flexShrink:0,
        background: prefs && prefs[k] ? T.accent : T.switchOff,
        transition:"background 240ms cubic-bezier(0.32,0.72,0,1)",
      }}>
        <div style={{
          position:"absolute",top:2,
          left: prefs && prefs[k] ? 16 : 2,
          width:16,height:16,borderRadius:8,
          background:"#fff",
          transition:"left 240ms cubic-bezier(0.32,0.72,0,1)",
        }}/>
      </div>
    </div>
  );
  if (err) return <ErrorPane T={T} L={L} error={err} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>;
  if (!prefs) return <LoadingPane T={T} L={L} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>;
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title={L.notifications} sub={L.what_to_chat} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,overflowY:"auto",padding:"14px 14px 32px"}}>
        <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,boxShadow:T.shadow,overflow:"hidden"}}>
          {(events.length ? events : Object.keys(prefs)).map(k => {
            const meta = {
              tunnel_url_changed:   [L.tunnel_changed, L.cf_new_addr],
              site_template_changed:[L.site_template,  L.site_changed_sub],
              client_added:         [L.client_added,   L.new_key_made],
              panel_creds_rotated:  [L.panel_access,   L.creds_changed_sub],
              mode_switched:        [L.mode_switch,    L.lite_pro_done],
              daemon_restart:       [L.daemons,        L.daemons_restarted],
            }[k] || [k, ""];
            return <div key={k}>{row(k, meta[0], meta[1])}</div>;
          })}
        </div>
      </div>
    </div>
  );
}

/* ─────────── PANEL ACCESS (Etap 3) ─────────── */
function PanelAccessScreen({ T, L, themePref, onThemeClick, navigate }) {
  const [info, setInfo] = useState(null);
  const [err, setErr] = useState(null);
  const [showPw, setShowPw] = useState(false);
  const [busy, setBusy] = useState(false);
  const load = () => rpc("panel.access_get", {})
    .then(setInfo)
    .catch(e => { if (e.code===401) setErr(e); else { setInfo({}); toast(L.err+e.message,"err"); } });
  useEffect(() => { load(); }, []);
  const onRotate = async () => {
    setBusy(true);
    // Use dedicated RPC; gracefully degrade if daemon doesn't yet ship it.
    try { await rpc("panel.creds_rotate", {}); toast(L.creds_updated,"ok"); load(); }
    catch(e) {
      if (e.code===412 && e.detail && e.detail.confirm) {
        const ok = await typedConfirm(T, L, e.detail.confirm, e.detail.description||L.rotate_creds_q);
        if (!ok) { toast(L.cancelled,"warn"); setBusy(false); return; }
        try { await rpc("panel.creds_rotate", {confirm:e.detail.confirm, confirm_token:e.detail.confirm_token});
              toast(L.creds_updated,"ok"); load(); }
        catch(ee) { toast(L.err+ee.message,"err"); }
      } else if (e.code === -32601 || /method not found/i.test(e.message||"")) {
        toast(L.rotate_wip,"warn");
      } else toast(L.err+e.message,"err");
    }
    setBusy(false);
  };
  if (err) return <ErrorPane T={T} L={L} error={err} onBack={()=>navigate("#/system")} themePref={themePref} onThemeClick={onThemeClick}/>;
  if (!info) return <LoadingPane T={T} L={L} onBack={()=>navigate("#/system")} themePref={themePref} onThemeClick={onThemeClick}/>;
  const onCopy = async (txt) => { try { await navigator.clipboard.writeText(txt); toast(L.copied,"ok"); } catch(e){} };
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title={L.panel_access} onBack={()=>navigate("#/system")} themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,overflowY:"auto",padding:"14px 14px 32px"}}>
        <div style={{background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:14,marginBottom:12,boxShadow:T.shadow}}>
          <h3 style={{fontSize:14,margin:"0 0 10px",color:T.ink}}>3X-UI</h3>
          {info.url && <>
            <div style={{fontSize:12,color:T.ink3,marginBottom:4}}>{L.panel_url}</div>
            <div onClick={()=>onCopy(info.url)} style={linkBlock(T)}>{info.url}</div>
          </>}
          {info.username && <>
            <div style={{fontSize:12,color:T.ink3,margin:"10px 0 4px"}}>{L.login}</div>
            <div onClick={()=>onCopy(info.username)} style={linkBlock(T)}>{info.username}</div>
          </>}
          {!info.password && info.username && (
            <>
              <div style={{fontSize:12,color:T.ink3,margin:"10px 0 4px"}}>{L.password}</div>
              <div style={{fontSize:12.5,color:T.ink2,lineHeight:1.45,background:T.card2,border:`1px solid ${T.border}`,padding:"10px 12px",borderRadius:10}}>
                {L.pw_unavailable}
              </div>
            </>
          )}
          {info.password && <>
            <div style={{fontSize:12,color:T.ink3,margin:"10px 0 4px",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
              <span>{L.password}</span>
              <button onClick={()=>setShowPw(!showPw)} style={{background:"transparent",border:0,color:T.accent2,cursor:"pointer",fontSize:12}}>
                {showPw?L.hide:L.show}
              </button>
            </div>
            <div onClick={()=>{if(showPw)onCopy(info.password)}} style={{...linkBlock(T),filter:showPw?"none":"blur(5px)",cursor:showPw?"copy":"default"}}>
              {info.password}
            </div>
          </>}
          {info.mode && <div style={{marginTop:10}}><Pill T={T} kind={info.mode==="ssh-only"?"ok":"warn"}>{info.mode==="ssh-only"?"SSH-only":L.public}</Pill></div>}
          <button onClick={onRotate} disabled={busy} style={{...btn(T,"warn"),width:"100%",marginTop:14,opacity:busy?0.6:1}}>
            {busy?L.rotating:L.rotate_creds_btn}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─────────── App ─────────── */

/* ─────────── COVER SITE / TEMPLATE (Pro, real RPC) ─────────── */
// Same backend as the SSH menu (govless → 1 → 5) and the bot (/site):
// site.template_status / categories / list / get / random / apply / job.
// Applying is a daemon-side background job (a clone can take 30-180 s), so the
// screen polls site.template_job every 2.5 s and shows progress inline.
const SITE_PER_PAGE = 24;
function SiteTemplateScreen({ T, L, themePref, onThemeClick, navigate }) {
  const [st, setSt] = useState(null);
  const [err, setErr] = useState(null);
  const [cats, setCats] = useState([]);
  const [cat, setCat] = useState("");
  const [q, setQ] = useState("");
  const [activeQ, setActiveQ] = useState("");
  const [list, setList] = useState(null);
  const [listBusy, setListBusy] = useState(false);
  const [job, setJob] = useState(null);
  const [now, setNow] = useState(Date.now());
  const [url, setUrl] = useState("");
  const [showBackups, setShowBackups] = useState(false);
  const pollRef = useRef(null);
  const ask = (msg) => new Promise(res => { try { if (TG && TG.showConfirm) { TG.showConfirm(msg, res); return; } } catch(e){} res(window.confirm(msg)); });
  const openLink = (u) => { try { if (TG && TG.openLink) { TG.openLink(u); return; } } catch(e){} window.open(u, "_blank"); };
  const stopPoll = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  const loadStatus = async () => {
    try {
      const s = await rpc("site.template_status", {});
      setSt(s); setJob(s.job || null);
      if (s.job && s.job.state === "running") startPoll();
    } catch(e) {
      if (e.code === 401) setErr(e); else setSt({ available:false, error:e.message });
    }
  };
  const startPoll = () => {
    stopPoll();
    pollRef.current = setInterval(async () => {
      setNow(Date.now());
      try {
        const r = await rpc("site.template_job", {});
        const j = r.job || null; setJob(j);
        if (!j || j.state !== "running") {
          stopPoll();
          if (j && j.ok) toast(L.site_done, "ok");
          else if (j) toast(L.site_failed + (j.error || j.error_code || ""), "err");
          loadStatus();
        }
      } catch(e) {}
    }, 2500);
  };
  useEffect(() => {
    loadStatus();
    rpc("site.template_categories", {}).then(c => setCats(c || [])).catch(() => {});
    return () => stopPoll();
  }, []);
  const loadList = async (c, page, query) => {
    setListBusy(true);
    try {
      const params = query ? { q:query, page, per:SITE_PER_PAGE } : { category:c, page, per:SITE_PER_PAGE };
      setList(await rpc("site.template_list", params));
    } catch(e) { toast(L.err + e.message, "err"); }
    setListBusy(false);
  };
  const pickCat = (c) => { setCat(c); setActiveQ(""); setQ(""); loadList(c, 0, ""); };
  const doSearch = () => { const w = q.trim(); if (!w) return; setActiveQ(w); setCat(""); loadList("", 0, w); };
  const apply = async (params, label) => {
    if (!(await ask(L.site_apply_q.replace("{n}", label)))) return;
    haptic();
    try {
      const r = await rpc("site.template_apply", {...params, lang: TG_LANG});
      setJob(r.job || null); closeModal(); startPoll();
      toast(L.site_started, "ok");
    } catch(e) { toast(L.err + e.message, "err"); }
  };
  const openCard = (t) => openModal(
    <div style={{width:"100%",maxWidth:480,background:T.card,color:T.ink,border:`1px solid ${T.border}`,borderRadius:16,padding:18,
      boxShadow:"0 -12px 40px rgba(0,0,0,0.45)",animation:"slidein 220ms cubic-bezier(0.32,0.72,0,1)",maxHeight:"80vh",overflowY:"auto"}}>
      <h3 style={{fontSize:16,margin:"0 0 6px",color:T.ink}}>{t.name}</h3>
      <div style={{fontSize:12,color:T.ink3,marginBottom:8,fontFamily:T.mono}}>
        {t.source} · {TG_LANG==="en" ? (t.category_en||t.category) : t.category}{t.license ? ` · ${L.site_license}: ${t.license}` : ""}{t.stars ? ` · ★ ${t.stars}` : ""}
      </div>
      {t.description && <p style={{fontSize:13,color:T.ink2,margin:"6px 0 10px",lineHeight:1.5}}>{t.description}</p>}
      <div style={{fontSize:11,color:T.ink3,fontFamily:T.mono,wordBreak:"break-all",marginBottom:12}}>id: {t.id}</div>
      <div style={{display:"flex",gap:8,flexWrap:"wrap",marginBottom:12}}>
        {t.preview_url && <button onClick={()=>openLink(t.preview_url)} style={{...btn(T,"secondary"),flex:1}}>👁 {L.site_preview}</button>}
        {t.repo_url && <button onClick={()=>openLink(t.repo_url)} style={{...btn(T,"secondary"),flex:1}}>📦 {L.site_repo}</button>}
      </div>
      <div style={{display:"flex",gap:8}}>
        <button onClick={closeModal} style={{...btn(T,"secondary"),flex:"0 0 auto",padding:"0 18px"}}>{L.cancel}</button>
        <button onClick={()=>apply({template_id:t.id}, t.name)} style={{...btn(T,"ok"),flex:1}}>✅ {L.site_apply}</button>
      </div>
    </div>
  );
  if (err) return <ErrorPane T={T} L={L} error={err} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>;
  if (!st) return <LoadingPane T={T} L={L} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>;
  const card = {background:T.card,border:`1px solid ${T.border}`,borderRadius:14,padding:14,marginBottom:12,boxShadow:T.shadow};
  const cur = st.current || null;
  const curLabel = !cur ? L.site_none
    : cur.kind==="stub" ? L.site_stub
    : cur.kind==="custom" ? `${L.site_custom}: ${cur.source_url||cur.id}`
    : cur.kind==="catalog" ? (cur.template ? `${cur.template.name}  [${cur.template.source}]` : cur.id)
    : `${L.site_unknown}`;
  const jobRunning = !!(job && job.state==="running");
  const elapsed = jobRunning ? Math.max(0, Math.floor(now/1000 - (job.started_ts||now/1000))) : 0;
  const backups = st.backups || [];
  const urlOk = /^https:\/\/\S+$/.test(url.trim());
  return (
    <div style={{display:"flex",flexDirection:"column",height:"100%",background:T.bg,color:T.ink,fontFamily:T.font}}>
      <Header T={T} L={L} title={L.site_template} sub={st.domain || (st.mode||"lite")} onBack={()=>navigate("#/")} themePref={themePref} onThemeClick={onThemeClick}/>
      <div style={{flex:1,overflowY:"auto",padding:"14px 14px 32px"}}>
        {!st.available ? (
          <div style={card}>
            <div style={{fontSize:13,color:T.ink2,lineHeight:1.5}}>{L.site_pro_only}</div>
            {st.error && <div style={{fontSize:12,color:T.danger,marginTop:8}}>{st.error}</div>}
          </div>
        ) : (<>
          <div style={card}>
            <h3 style={{fontSize:14,margin:"0 0 8px",color:T.ink}}>{L.site_current}</h3>
            <div style={{fontSize:14,fontWeight:600,color:T.ink,wordBreak:"break-word"}}>{curLabel}</div>
            {cur && cur.template && cur.template.id && <div style={{fontSize:11,color:T.ink3,fontFamily:T.mono,marginTop:4,wordBreak:"break-all"}}>{cur.template.id}</div>}
            {st.catalog && st.catalog.ok && <div style={{fontSize:12,color:T.ink3,marginTop:8}}>{L.site_catalog_fmt.replace("{n}", st.catalog.total).replace("{c}", st.catalog.categories)}</div>}
            {st.catalog && !st.catalog.ok && <div style={{fontSize:12,color:T.danger,marginTop:8}}>{st.catalog.error}</div>}
            <div style={{display:"flex",gap:8,marginTop:12,flexWrap:"wrap"}}>
              {st.site_url && <button onClick={()=>openLink(st.site_url)} style={{...btn(T,"secondary"),flex:1}}>🌐 {L.site_open}</button>}
              <button disabled={jobRunning} onClick={()=>apply({random:true}, L.site_random_label)} style={{...btn(T,"ok"),flex:1,opacity:jobRunning?0.6:1}}>{L.site_random}</button>
              <button disabled={jobRunning} onClick={()=>apply({stub:true}, L.site_stub)} style={{...btn(T,"secondary"),flex:1,opacity:jobRunning?0.6:1}}>{L.site_stub_btn}</button>
            </div>
            <div style={{fontSize:11,color:T.ink3,marginTop:10}}>{L.site_hint}</div>
          </div>
          {job && (
            <div style={{...card, borderColor: job.state==="failed"?T.danger:job.state==="done"?T.accent:T.border}}>
              {jobRunning ? (
                <div style={{display:"flex",alignItems:"center",gap:10,fontSize:13}}>
                  <div style={{width:18,height:18,border:`3px solid ${T.hair}`,borderTopColor:T.accent,borderRadius:"50%",animation:"spin 800ms linear infinite",flexShrink:0}}/>
                  <div>{L.site_applying_fmt.replace("{n}", job.name||job.target||"").replace("{s}", elapsed)}</div>
                </div>
              ) : job.ok ? (
                <div style={{fontSize:13}}>
                  <div style={{fontWeight:600,color:T.accent}}>✅ {L.site_done}: {job.name||job.template_id}</div>
                  <div style={{fontSize:12,color:T.ink3,marginTop:4}}>{L.site_http_fmt.replace("{c}", job.http_code||"?")}{job.backup ? ` · ${L.site_prev_saved}` : ""}</div>
                </div>
              ) : (
                <div style={{fontSize:13}}>
                  <div style={{fontWeight:600,color:T.danger}}>❌ {L.site_failed}{job.error_code||""}</div>
                  <div style={{fontSize:12,color:T.ink2,marginTop:4,lineHeight:1.45}}>{job.error||""}</div>
                </div>
              )}
            </div>
          )}
          <div style={card}>
            <div style={{display:"flex",gap:8,marginBottom:10}}>
              <input value={q} onChange={e=>setQ(e.target.value)} onKeyDown={e=>{ if(e.key==="Enter") doSearch(); }} placeholder={L.site_search_ph} style={{
                flex:1,minWidth:0,background:T.card2,color:T.ink,border:`1px solid ${T.border}`,padding:"10px 12px",borderRadius:10,fontFamily:T.font,fontSize:13,minHeight:44}}/>
              <button onClick={doSearch} style={{...btn(T,"secondary"),flex:"0 0 auto"}}>🔍 {L.site_search}</button>
            </div>
            <div style={{fontSize:11,color:T.ink3,textTransform:"uppercase",letterSpacing:"0.06em",marginBottom:6}}>{L.site_categories}</div>
            {/* Разделы каталога — сетка на всю ширину, без горизонтальной прокрутки:
                18 категорий в ленте со свайпом прятали половину списка и мешали
                вертикальному скроллу экрана. */}
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:6}}>
              {cats.map(c => (
                <button key={c.id} onClick={()=>pickCat(c.id)} style={{
                  display:"flex",alignItems:"center",justifyContent:"space-between",gap:6,
                  padding:"9px 11px",borderRadius:10,fontSize:12,fontWeight:600,cursor:"pointer",
                  fontFamily:T.font,textAlign:"left",minHeight:40,
                  background: cat===c.id ? T.accent : T.card2, color: cat===c.id ? T.primaryInk : T.ink,
                  border:`1px solid ${cat===c.id?T.accent:T.border}`}}>
                  <span style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                    {TG_LANG==="en" ? (c.name_en||c.name) : c.name}
                  </span>
                  <span style={{opacity:0.7,flexShrink:0,fontVariantNumeric:"tabular-nums"}}>{c.count}</span>
                </button>
              ))}
            </div>
            {!list && !listBusy && <div style={{fontSize:12,color:T.ink3,marginTop:8}}>{L.site_pick_hint}</div>}
            {listBusy && <div style={{display:"flex",justifyContent:"center",padding:16}}><div style={{width:22,height:22,border:`3px solid ${T.hair}`,borderTopColor:T.accent,borderRadius:"50%",animation:"spin 800ms linear infinite"}}/></div>}
            {list && !listBusy && (
              <div style={{marginTop:8}}>
                <div style={{fontSize:12,color:T.ink3,marginBottom:6}}>
                  {activeQ ? L.site_results_fmt.replace("{n}", list.total) : `${list.total}`} · {list.page+1}/{list.pages}
                </div>
                {list.items.length===0 ? <div style={{fontSize:13,color:T.ink3,padding:"10px 0"}}>{L.site_empty_list}</div> : (
                  <div style={{background:T.card2,border:`1px solid ${T.border}`,borderRadius:12,padding:"2px 0"}}>
                    {list.items.map((t,i)=>(
                      <div key={t.id} onClick={()=>openCard(t)} style={{display:"flex",alignItems:"center",gap:10,padding:"10px 12px",cursor:"pointer",borderTop:i>0?`1px solid ${T.hair}`:"none"}}>
                        <div style={{flex:1,minWidth:0}}>
                          <div style={{fontSize:13.5,fontWeight:600,color:T.ink,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{t.name}</div>
                          <div style={{fontSize:11,color:T.ink3,fontFamily:T.mono,marginTop:2}}>{t.source}{t.license?` · ${t.license}`:""}{t.stars?` · ★ ${t.stars}`:""}</div>
                        </div>
                        {(t.preview_url || t.repo_url) && (
                          <button onClick={(e)=>{ e.stopPropagation(); openLink(t.preview_url || t.repo_url); }}
                            style={{...btn(T,"secondary"),minHeight:36,padding:"0 10px",fontSize:12,flex:"0 0 auto",whiteSpace:"nowrap"}}>
                            {t.preview_url ? `👁 ${L.site_preview}` : `📦 ${L.site_repo}`}
                          </button>
                        )}
                        <span style={{color:T.accent2||T.accent,fontSize:12,fontWeight:700}}>{L.site_apply} ›</span>
                      </div>
                    ))}
                  </div>
                )}
                {list.pages>1 && (
                  <div style={{display:"flex",gap:8,marginTop:10}}>
                    <button disabled={list.page<=0} onClick={()=>loadList(cat, list.page-1, activeQ)} style={{...btn(T,"secondary"),flex:1,opacity:list.page<=0?0.5:1}}>{L.site_prev}</button>
                    <button disabled={list.page>=list.pages-1} onClick={()=>loadList(cat, list.page+1, activeQ)} style={{...btn(T,"secondary"),flex:1,opacity:list.page>=list.pages-1?0.5:1}}>{L.site_next}</button>
                  </div>
                )}
              </div>
            )}
          </div>
          <div style={card}>
            <h3 style={{fontSize:14,margin:"0 0 6px",color:T.ink}}>🔗 {L.site_custom_title}</h3>
            <div style={{fontSize:12,color:T.ink3,marginBottom:8,lineHeight:1.45}}>{L.site_custom_hint}</div>
            <div style={{display:"flex",gap:8}}>
              <input value={url} onChange={e=>setUrl(e.target.value)} placeholder="https://github.com/user/repo" inputMode="url" style={{
                flex:1,minWidth:0,background:T.card2,color:T.ink,border:`1px solid ${T.border}`,padding:"10px 12px",borderRadius:10,fontFamily:T.mono,fontSize:12,minHeight:44}}/>
              <button disabled={jobRunning || !urlOk} onClick={()=>apply({custom_url:url.trim()}, url.trim())} style={{...btn(T,"ok"),flex:"0 0 auto",opacity:(jobRunning || !urlOk)?0.5:1}}>{L.site_apply}</button>
            </div>
          </div>
          <div style={card}>
            <div onClick={()=>setShowBackups(v=>!v)} style={{display:"flex",justifyContent:"space-between",alignItems:"center",cursor:"pointer"}}>
              <h3 style={{fontSize:14,margin:0,color:T.ink}}>{L.site_backups} ({backups.length})</h3>
              <span style={{color:T.ink3}}>{showBackups?"▾":"▸"}</span>
            </div>
            {showBackups && (backups.length===0 ? <div style={{fontSize:12,color:T.ink3,marginTop:8}}>{L.site_no_backups}</div> : (
              <div style={{marginTop:8}}>
                {backups.map((b,i)=>(
                  <div key={b.path} style={{display:"flex",alignItems:"center",gap:10,padding:"8px 0",borderTop:i>0?`1px solid ${T.hair}`:"none"}}>
                    <div style={{flex:1,minWidth:0}}>
                      <div style={{fontSize:12,fontFamily:T.mono,color:T.ink,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis"}}>{b.name}</div>
                      <div style={{fontSize:11,color:T.ink3,fontFamily:T.mono}}>{formatBytes(b.size)} · {new Date(b.ts*1000).toLocaleString(L.time_locale)}</div>
                    </div>
                    <button disabled={jobRunning} onClick={async()=>{ if (await ask(L.site_restore_q.replace("{n}", b.name))) { try { const r = await rpc("site.template_apply", {restore:b.path, lang:TG_LANG}); setJob(r.job||null); startPoll(); toast(L.site_started,"ok"); } catch(e){ toast(L.err+e.message,"err"); } } }} style={{...btn(T,"warn"),fontSize:12,minHeight:36,padding:"0 10px",opacity:jobRunning?0.6:1}}>↻ {L.site_restore}</button>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </>)}
      </div>
    </div>
  );
}

function App() {
  const { pref, resolved, cycle, setPref } = useTheme();
  const T = TOKENS[resolved];
  const L = STR[TG_LANG];
  const { hash, navigate } = useRoute();
  const onThemeClick = () => {
    cycle();
    const next = pref==="system"?"light":pref==="light"?"dark":"system";
    const names = {system:L.theme_sys_lc, light:L.theme_light_lc, dark:L.theme_dark_lc};
    toast(L.theme_prefix+names[next], "ok");
  };

  let screen;
  if (hash === "#/" || hash === "" || hash === "#") {
    screen = <HomeScreen T={T} L={L} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}/>;
  } else if (hash.startsWith("#/clients/")) {
    const uuid = hash.slice(10).split("?")[0];
    screen = <ClientCardScreen T={T} L={L} uuid={uuid} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}/>;
  } else if (hash.startsWith("#/clients")) {
    screen = <ClientsListScreen T={T} L={L} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}/>;
  } else if (hash.startsWith("#/system")) {
    screen = <SystemScreenReal T={T} L={L} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}/>;
  } else if (hash.startsWith("#/admins")) {
    screen = <AdminsScreenReal T={T} L={L} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}/>;
  } else if (hash.startsWith("#/inbounds/")) {
    const id = hash.slice(11).split("?")[0];
    screen = <InboundDetailScreen T={T} L={L} id={id} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}/>;
  } else if (hash.startsWith("#/inbounds")) {
    screen = <InboundsScreenReal T={T} L={L} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}/>;
  } else if (hash.startsWith("#/backup")) {
    screen = <BackupScreenReal T={T} L={L} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}/>;
  } else if (hash.startsWith("#/repair")) {
    screen = <RepairScreen T={T} L={L} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}/>;
  } else if (hash.startsWith("#/notifications")) {
    screen = <NotificationsScreen T={T} L={L} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}/>;
  } else if (hash.startsWith("#/panel")) {
    screen = <PanelAccessScreen T={T} L={L} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}/>;
  } else if (hash.startsWith("#/site")) {
    screen = <SiteTemplateScreen T={T} L={L} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}/>;
  } else if (hash.startsWith("#/settings")) {
    screen = <SettingsScreen T={T} L={L} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}
      onSetTheme={setPref}
      langPref={TG_LANG} onLangChange={(lng)=>{try{localStorage.setItem("gov_lang", lng);}catch(e){} window.location.reload();}}/>;
  } else if (hash.startsWith("#/about")) {
    screen = <AboutScreenReal T={T} L={L} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}/>;
  } else if (hash.startsWith("#/disclaimer")) {
    screen = <DisclaimerScreenReal T={T} L={L} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}/>;
  } else {
    screen = <HomeScreen T={T} L={L} themePref={pref} onThemeClick={onThemeClick} navigate={navigate}/>;
  }
  useEffect(() => {
    document.body.style.background = T.bg;
    document.body.style.color = T.ink;
    document.body.style.fontFamily = T.font;
  }, [T]);

  // T3714/T3744: sync TG.BackButton with hash routing
  useEffect(() => {
    if (!TG || !TG.BackButton) return;
    const isHome = hash === "#/" || hash === "" || hash === "#";
    if (isHome) {
      TG.BackButton.hide();
    } else {
      TG.BackButton.show();
    }
    const onBack = () => {
      try {
        if (window.history.length > 1) window.history.back();
        else navigate("#/");
      } catch(e) { navigate("#/"); }
    };
    TG.BackButton.onClick(onBack);
    return () => { try { TG.BackButton.offClick(onBack); } catch(e){} };
  }, [hash]);

  // T2634/T2664: listen for OS-level themeChanged so system-theme reacts live
  useEffect(() => {
    if (!TG || !TG.onEvent) return;
    const onThemeChanged = () => {
      if (pref === "system") {
        // Force re-render — useTheme's resolved value depends on TG.colorScheme which just changed.
        // Triggers via state nudge: call setPref(pref) which re-evaluates derived.
        try { setPref(p => p); } catch(e){}
      }
    };
    TG.onEvent("themeChanged", onThemeChanged);
    return () => { try { TG.offEvent("themeChanged", onThemeChanged); } catch(e){} };
  }, [pref, setPref]);

  return (
    <>
      {screen}
      <ToastHost/>
      <ModalHost/>
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes fadein { from { opacity: 0; } to { opacity: 1; } }
        @keyframes slidein { from { transform: translateY(24px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
        @keyframes toastin { from { opacity: 0; transform: translateY(-12px) scale(0.96); } to { opacity: 1; transform: translateY(0) scale(1); } }
        html, body { margin: 0; padding: 0; overscroll-behavior-y: contain; -webkit-tap-highlight-color: transparent; }
        * { box-sizing: border-box; }
        button { font-family: inherit; }
        button:active:not(:disabled) { transform: scale(0.97); opacity: 0.88; }
        @keyframes shimmer { 0%,100% { opacity: 1; } 50% { opacity: 0.55; } }
      `}</style>
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);

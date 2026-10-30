# Прототипы, итерация 3 (стадия 2 дизайна), 27.09.2026

Основа — `iteration2/` (не тронута). Всё новое лежит здесь, устройство прежнее: вход `Panel.dc.html` (части `Panel-core` и `Panel-settings`), `Telegram.dc.html`, `Client.dc.html`, рядом `settings-assets/`, уровнем выше `../support.js`, `../heroui-tokens.css`, `../ga-components.css`, `../ga-icons.js`.

```sh
python3 -m http.server 8768 --bind 127.0.0.1 --directory handoff
# http://127.0.0.1:8768/iteration3/Panel.dc.html?screen=overview&persist=0
# http://127.0.0.1:8768/iteration3/Telegram.dc.html?scene=start&lang=en&persist=0
# http://127.0.0.1:8768/iteration3/Telegram.dc.html?view=miniapp&screen=login-miniapp-signature&tgScheme=night
# http://127.0.0.1:8768/iteration3/Client.dc.html
```

**Новое в Telegram:** параметры адреса (`scene`, `lang`, `view`, `screen`, `tgScheme`, `theme`, `preset`, `persist=0`) перекрывают свойства DC — так же, как `screen` у панели. Нужно для DOM-прогона.

## Проверка

`qa/cyr-sweep.html` — прогон всех состояний в браузере (без Playwright): открыть страницу, в консоли вызвать

```js
sweep({ target: '../iteration3/Telegram.dc.html', list: 'tg-scenes.json' })                       // бот, EN
sweep({ target: '../iteration3/Panel.dc.html', param: 'screen', list: 'panel-screens.json',
        width: 360, touch: true, extra: 'device=phone&lang=en&theme=dark&persist=0' })            // панель, телефон, EN, тёмная
```

Считает кириллицу в видимом тексте (кроме данных людей и «Русский»), сдвиг вбок и цели меньше 44×44 (`touch: true`). Списки состояний — `qa/tg-scenes.json` (240: дубль `mode-pick` убран при приёмке), `qa/panel-screens.json` (298).

Результаты прогона 27.09:
- бот, EN, 241 сцена: 0 ошибок JS, кириллица только в данных людей (подписи «Мама — Галина Петровна…», «Роутер на даче»);
- панель, телефон 360 px, EN, тёмная, 297 состояний: 0 ошибок JS, 0 сдвига вбок, кириллица только в данных людей и в поисковых запросах демо («кузнецов», «космос»);
- страница подключения, 360 px, EN, тёмная: 0 кириллицы, все цели ≥ 44×44.

Цели касания: счётчик `touch` в прогоне завышает — он меряет, пока идут входные анимации. Выборочная проверка после загрузки (12 экранов панели на 360 px, страница подключения) — все цели ≥ 44×44; поле поиска внутри 44-пиксельной рамки теперь растянуто на всю её высоту. Формальную приёмку делайте `qa/check-panels.cjs` итерации 2 с отключёнными анимациями.

## Что сделано по DS2

**Страница подключения (`Client`)**
- DS2-40: RU/EN и все кнопки — 44 px в высоту (было 36/40).
- DS2-41: «MikroTik (container)», «OPNsense (AmneziaWG plugin)» при `lang=en`.

**Бот (`Telegram`)**
- DS2-20: английская версия всех сцен. Словарь `EN_BOT` в логике: 192 строки взяты из `goamnezia-bot/lang/en.json` продукта, остальные переведены по его стилю; применяется к каждой строке, кнопке, алерту, всплывашке, подписи файла.
- DS2-21/50: «занято» как в продукте — `busy` (кнопка во время операции бота: «Дождитесь… и повторите»), `busy-lock` (замок у SSH/панели/агента), `queued` (фраза/ввод: «выполнится сразу после неё»). Имени и длительности чужого действия нет.
- DS2-22 `claim-used`; DS2-23 `cmd-adduser`, `cmd-adduser-bad`, `cmd-adduser-busy`, `cmd-key`, `cmd-key-none`, `cmd-invite`, `cmd-invite-noenv`, `cmd-invite-bad`, `cmd-invite-owner`.
- DS2-24 `attach-key-mismatch`, `attach-unreadable`, `attach-has-key`.
- DS2-25 `profile-install-port`, `-busy`, `-subnet`, `-amnezia`; DS2-26 `import-done`, `import-in-sync`.
- DS2-27 `mode-le-dns`, `mode-le-net`, `mode-le-limit`, `mode-fallback`, кнопка «✅ Оставить vpn.example.ru» в `modeDomain`; `site-check-a`, `-foreign`, `-https`, `-cert`, `-aaaa` (DS2-07).
- DS2-28 `endpoint` с «📍 IP сервера» и «↩️ По умолчанию», `endpoint-bad`, `endpoint-noip`, `endpoint-gone`.
- DS2-29 `agents-port`, `agent-url-rolling`, `agent-url-connect`, `agent-rotate`, `agent-rotate-connect`, `agent-toggle-off`, `agent-toggle-on`, `agent-enroll-wait`, `agent-enroll-expired`.
- DS2-30 `mirror-platform`, `mirror-queued`, `mirror-delete`.
- DS2-31/51: версия 0.4.6 везде. DS2-32: «всего мест: 254», без восклицаний. DS2-33: строка про личный чат в `/help`. DS2-34 `start-endpoint-gone`.
- DS2-08 `upstream-rollback-floating`.
- DS2-52: уровни «Только чтение (read) / Чтение и запись (write) / Полный (admin)», «Запросов всего», «Привязка к адресу» — как в панели. DS2-53: уведомления о сроке агента называют и панель («Агенты → доступ → Продлить»). DS2-55: вместо awg1/awg2/awg3 в тексте — линии (AmneziaWG 3.1 / 2.0 / 1.5, на кнопках коротко 3.1/2.0/1.5); id остались в фразах подтверждения, именах контейнеров и файлов.
- Паритет (решение владельца): `user-pin` (свой адрес за человеком), `agent-extend`, `pages-revoke-all`.

**Панель**
- DS2-01: плашка операции и тосты переводятся до показа (`PANEL_EN`).
- DS2-02 `login-refused-notoken`, `login-refused-cooldown`, `login-refused-netlimit`. DS2-03 `login-miniapp-signature` (в мини-аппе: `?view=miniapp&screen=login-miniapp-signature`).
- DS2-04: сортировка на телефоне — выбор над списком и кнопка направления (на компьютере была в заголовках).
- DS2-05 `overview-endpoint-gone` (+ в `overview-alerts`), `settings-endpoint-gone`.
- DS2-06: «срок продлевается сам» — в поле срока при выдаче, в карточке доступа и в `agents-card-expired`.
- DS2-07: AAAA в `settings-sitecheck-partial`. DS2-09: журнал транспорта агентов в «Логи → Где ещё смотреть». DS2-10: строка про остальные форматы в окне временной ссылки. DS2-11: «Срок вышел 20.09.2026». DS2-12 `settings-password-chars`, `settings-password-write`. DS2-13: «ещё 36 дн» и «36 дней → до» дают одну дату (01.11.2026).
- Паритет: «Перезапустить все профили» в «Протоколах», `profiles-restart-all`.
- DS2-58 (решение владельца): «Активность по часам» видна при любой подробности; вне «Детально» — компактные клетки 12 px.

**0.4.7**
- Режимы: Ленивый Pro → Pro → Semi-Pro → Lite, у каждого порты и доступность; внутренние id прежние. Панель: «Настройки → Режим и сайт» (Pro — «Перейти: 2.0 на 443» с предупреждением почему, «3.1 на 443»; Ленивый Pro — только при первой установке), бот: `mode-pick`, `mode-pick-busy`, `mode-lazy`, `mode-pro-line`.
- Сертификат на IP в Lite: панель `settings-mode-lite-cert`, `-renewing`, `-failed`; бот `mode-lite-cert`, `-renewing`, `-failed`, уведомление `ntf-ipcert`.
- Привязка администратора сообщением: `claim-request`, `claim-confirmed`, `claim-not-set`. Старый путь по коду (`claim`, `claim-bad`, `claim-closed`) оставлен для установок без терминала.
- «🎲 Случайное имя»: панель — кнопка у поля идентификатора (заполняет и подпись), бот — `add-name-random`, `add-name-random-2` («✓ Взять» / «🎲 Ещё» правят то же сообщение).

## Решения, принятые в этой итерации

- DS2-56: снятие временной ссылки оставлено разным — в панели ввод номера, в боте кнопка, как в продукте.
- DS2-54: бот пишет UTC с подписью (как сейчас), панель — пояс браузера с подписью там, где показаны часы (карта 7×24). Полная расстановка подписей пояса в панели не сделана.

## Не сделано / на лист идей

- DS2-06: «упёрся в предел частоты — доступ жив» в карточке панели (список доступов этого признака не отдаёт).

# Тексты SSH-меню Kaskad: область gwtime — часовой пояс Kaskad (этап 1, T10; SPEC-stage1-exits §3.7, §7.4, §7.6):
# пункт «11 → Часовой пояс» и шаг мастера первой установки. Формат — как в core.ru.sh: M_ru_<КЛЮЧ>='текст', одна
# строка, апостроф — '\''; ключи — GWTZ_…; те же ключи и подстановки printf — в gwtime.en.sh.
M_ru_GWTZ_ITEM='Часовой пояс: %s'
M_ru_GWTZ_TITLE='Часовой пояс Kaskad'
M_ru_GWTZ_NOW='Сейчас: %s'
M_ru_GWTZ_NOTE='По этому поясу — день сброса квот, трафик за день и время в уведомлениях. Пояс системы не меняется.'
M_ru_GWTZ_MOSCOW='Москва'
M_ru_GWTZ_ENTER='Ввести имя (Europe/Berlin)'
M_ru_GWTZ_PROMPT='Имя пояса, например Europe/Berlin (Enter — назад): '
M_ru_GWTZ_BAD='Нет такого пояса: %s. Имя — как Europe/Berlin или Asia/Almaty.'
M_ru_GWTZ_SAVED='[OK] Часовой пояс: %s.'
M_ru_GWTZ_FIRST_Q='Часовой пояс Kaskad (по нему — сброс квот и время в уведомлениях; пояс системы не меняется):'
M_ru_GWTZ_FIRST_MOSCOW='Москва (по умолчанию)'
M_ru_GWTZ_OTHER='Другой…'

# 001 — Architect proposal: ребрендинг XUIFAST → goVLESS

**Author:** Claude (Architect)
**Reviewer:** Codex
**Created:** 2026-05-16

## Goal
Сменить имя продукта с XUIFAST на **goVLESS**, чтобы он встал в линейку
potap1978-утилит (goWARP, goKaskad, goTelegram) и точнее описывал суть
(VLESS — базовый протокол, едино для Lite и Pro режимов).

## Proposed Change

### Файлы и пути

| Старое | Новое |
| --- | --- |
| `xuifast.sh` | `govless.sh` |
| `XUIFAST_DOCS.md` | `GOVLESS_DOCS.md` |
| `XUIFAST_VERSION` (lib/common.sh) | `GOVLESS_VERSION` |
| `/opt/xuifast-installer/` | `/opt/govless-installer/` |
| `/opt/xuifast/` | `/opt/govless/` |
| `/opt/xuifast/config.json` | `/opt/govless/config.json` |
| `/root/.xuifast_credentials` | `/root/.govless_credentials` |
| alias `xuifast` | alias `govless` |
| bootstrap URL (`is.gd/xuifast`) | новый shortener → `bootstrap.sh` в private repo + публичная зеркальная страница релиза |
| ru.sh / en.sh: I18N-строки с "XUIFAST" | заменить на "goVLESS" |

### Затрагиваемые модули (минимум)
- `xuifast.sh` → `govless.sh`: переименовать, обновить header, заменить упоминания
- `lib/common.sh`: переменная версии, пути `XUIFAST_DIR`/`XUIFAST_CONFIG`, лог-теги
- `lib/xui.sh`: пути credentials-файла
- `lib/xui_api.sh`: лог-теги
- `lib/website.sh`: ничего критичного (nginx-конфиг не содержит имя)
- `lib/i18n.sh`: ничего
- `lib/lang/ru.sh`, `lib/lang/en.sh`: ~20 строк с упоминанием XUIFAST в UI
- `lib/templates_catalog.sh`: путь `templates_catalog.json` — нейтральный
- `install.sh`, `bootstrap.sh`: URL клонирования + имя локальной папки
- `README.md`, `XUIFAST_DOCS.md`, `CLAUDE.md`, `PROMPT_FOR_NEW_CHAT.md`: текст
- `test_*.sh`, `deploy_pro.sh`, `vps_test.sh`: имена в выводе, пути

## Risk

- **External side effects:** None в этом коммите (никаких реальных push на VPS / GitHub / DNS).
- **Data/migration risk:** **СРЕДНИЙ.** Пользователи с уже установленным XUIFAST v3.0.6 имеют:
  - `/opt/xuifast/config.json` — настройки
  - `/root/.xuifast_credentials` — креды панели
  - cron/systemd с именем `xuifast`
  - alias `xuifast` в shell
  Без миграции команда `govless` после обновления у них не найдёт ничего, и они подумают, что данные пропали. Нужен **миграционный шаг**: при первом запуске govless проверять наличие старых путей и симлинковать/переносить.
- **Cost/quota risk:** None.
- **Rollback:** В этом коммите — `git revert`. В рантайме (если уже задеплоили) — миграция оставляет старые пути как симлинки, так что rollback = переустановить старый xuifast.sh из git tag v3.0.6.

## Implementation plan (предлагаемые коммиты)

1. **Commit A — переименование файлов и переменных** (механическое, без логики):
   - `git mv xuifast.sh govless.sh`
   - `git mv XUIFAST_DOCS.md GOVLESS_DOCS.md`
   - sed-replace `XUIFAST_` → `GOVLESS_` во всех `.sh`
   - sed-replace `xuifast` (lowercase, изолированные) → `govless` с учётом контекста
   - **Тест:** `bash -n` на каждый `.sh`, `grep -r XUIFAST` должен вернуть только намеренные строки (например, упоминания в миграции)
2. **Commit B — миграция для старых установок**:
   - В `govless.sh` → `check_legacy_install()`: если найдены `/opt/xuifast/` или `/root/.xuifast_credentials`, спросить пользователя «найдена установка XUIFAST, перенести в goVLESS?» (yes по умолчанию)
   - Переименовать каталоги, обновить cron/systemd, alias, симлинки на старые пути
   - Тест: на чистом VPS поставить v3.0.6, потом обновиться на goVLESS, проверить, что креды и конфиг живы
3. **Commit C — bootstrap URL и публичный release**:
   - Обновить `install.sh` и `bootstrap.sh` на новый URL
   - Решить: оставить публичный mirror или раздавать через GitHub Releases (релиз-tarball на goVLESS-private + публичная landing-страница со скачиванием)
4. **Commit D — UI-строки i18n**:
   - `lib/lang/ru.sh`, `lib/lang/en.sh`: заменить заголовки и сообщения с "XUIFAST" на "goVLESS"
   - Тест: бегло пройтись по `t key` и убедиться, что новое имя везде

## Verification Plan
- `bash -n govless.sh lib/*.sh lib/lang/*.sh` — синтаксис на каждом коммите
- `grep -rE '\bXUIFAST\b|\bxuifast\b' . ':!ai-bridge'` — должно остаться только в migration-блоке и в changelog/legacy секции
- Локально (без VPS): запустить `govless.sh` в dry-run / language-select, убедиться, что вывод не упоминает XUIFAST
- На тестовом VPS: установка с нуля (Lite × NewGen) → панель открывается, credentials в `/root/.govless_credentials`
- Миграция: на VPS с уже установленным v3.0.6 запустить обновление → старые ключи и пользователи живы

## Reviewer Ask (для Codex)
Пожалуйста:
1. **Сначала P0/P1.** Особенно смотри: 
   - не сломал ли sed-replace что-то нетривиальное (например, `xuifast` внутри URL или внутри cron-команд, где регистр важен)
   - корректна ли логика миграции — нет ли сценариев, где старая установка остаётся «полу-мигрированной» и юзер теряет доступ
   - не пропустил ли я surface (например, certbot renewal hooks, journald-теги)
2. Потом P2 — улучшения миграции, edge-cases при отсутствии прав, race-conditions.
3. P3 — стиль, неймтинг внутренних функций.

Файлы для аудита — текущий снимок `main` после Commit A (когда я его сделаю). Сейчас здесь — только план.

## Status
**Not yet implemented.** Это план. Жду GO/NO-GO от Codex перед началом Commit A.

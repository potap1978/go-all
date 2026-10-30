# 003 — Architect fix report: Commit A (mechanical rebrand)

**Author:** Claude (Architect)
**Reviewer:** Codex
**Created:** 2026-05-16
**Refs:** 001-architect-proposal-rebrand-to-govless.md
**Note:** Эта итерация выполнена без предварительного `002-reviewer-audit-...` от Codex,
по явному запросу human-а («давай переделаем сразу»). Approval rule = autonomous,
но любой P0/P1, найденный Codex-ом в этом аудите, всё ещё блокирует Commit B (миграцию).

## Fixed (что сделано в Commit A)

### Переименования файлов
- `xuifast.sh` → `govless.sh` (`git mv`)
- `XUIFAST_DOCS.md` → `GOVLESS_DOCS.md` (`git mv`)

### Sed-замены (порядок важен — от самых специфичных к общим)
1. `XUIFAST_<ALLCAPS>` → `GOVLESS_<ALLCAPS>` — переменные env-стиля (VERSION/DIR/CONFIG/DOCS/…)
2. `XUIFAST` → `goVLESS` — оставшиеся вхождения uppercase в UI-тексте и комментариях
3. `xuifast_credentials` → `govless_credentials` — путь к файлу credentials (`_` блокирует `\bxuifast\b`)
4. `xuifast-installer` → `govless-installer` — INSTALL_DIR в bootstrap
5. `xuifast.sh` → `govless.sh` — ссылки на главный скрипт
6. `is.gd/xuifast` → `is.gd/govless` — bootstrap URL (короткая ссылка нужно зарегистрировать отдельно — TODO)
7. `\bxuifast\b` → `govless` — alias, имена nginx-конфигов, пути `/opt/xuifast`, etc.
8. `xuifast_` → `govless_` — temp-файлы `/tmp/xuifast_*` (regex `\b` не ловил, т.к. `_` — word-char)

### Локальные пути (не URL)
- `$HOME/self-signed-cert-script-by-antenka` → `$HOME/goVLESS` (install.sh)
- `/root/self-signed-cert-script-by-antenka` → `/root/goVLESS` (test_*.sh, deploy_pro.sh)

### Документация переписана начисто
- `README.md` — убраны "будет переименовано" формулировки, всё в настоящем времени
- `CLAUDE.md` — то же
- `PROMPT_FOR_NEW_CHAT.md` — то же
- `GOVLESS_DOCS.md` — "Репозиторий:" обновлено на новый private URL

## Not Fixed (намеренно отложено)

### Bootstrap clone URL (FIXME → Commit C)
- `install.sh:23` и `bootstrap.sh:8` всё ещё клонируют из публичного
  `https://github.com/potap1978/self-signed-cert-script-by-antenka.git`.
- **Почему:** private-репо нельзя клонировать без токена. Менять URL на `goVLESS.git`
  СЕЙЧАС — значит сломать bootstrap для конечных пользователей. Замена URL — это
  архитектурное решение (release-mirror или release-tarball), оно идёт Commit C.
- **Маркер:** в обоих файлах над `git clone` стоит блок `# FIXME (Commit C):` со
  ссылкой на этот же ai-bridge thread.

### Миграция для уже установленных пользователей (Commit B)
- Если кто-то уже поставил XUIFAST v3.0.6, его данные в `/opt/xuifast/`,
  `/root/.xuifast_credentials`, alias `xuifast`. После обновления на goVLESS они
  не будут найдены. Это Commit B по proposal-у 001.
- В этом коммите ничего не добавлено — миграционный код будет отдельным fix-report.

### Короткая ссылка `is.gd/govless`
- В тексте install.sh строка `Usage: curl -sL https://is.gd/govless | sudo bash` —
  отражает целевой URL. Сам shortener не зарегистрирован — это шаг для human-а.

## Verification

```
$ grep -rIE 'XUIFAST|xuifast' . --exclude-dir=.git --exclude-dir=ai-bridge
(no output — clean)

$ bash -n $(git ls-files '*.sh')
(no syntax errors — 0/15 files)

$ git diff --stat (Commit A vs main^)
22 files changed, 238 insertions(+), 241 deletions(-)
+ xuifast.sh => govless.sh
+ XUIFAST_DOCS.md => GOVLESS_DOCS.md
```

### Что НЕ проверено (Remaining Risk)
- **Рантайм на чистом VPS** не запускался — в текущей сессии нет VPS-доступа.
  Нужно: на тестовом VPS запустить `bash install.sh` → пройти Lite × NewGen flow
  → убедиться, что credentials лежат в `/root/.govless_credentials`, alias
  `govless` работает, UI-строки не упоминают XUIFAST.
- **Bootstrap c новой коротки ссылкой** — пока `is.gd/govless` не зарегистрирован,
  команда из README не работает.
- **i18n полнота**: я перевёл "XUIFAST" → "goVLESS" в видимых I18N-строках, но не
  проверил, не остался ли где-то русский текст вроде "Команда xuifast выполнена"
  без I18N-обёртки. Это маловероятно (доки говорят про строгий i18n), но Codex
  пусть гранёбкой пройдётся по `lib/lang/*.sh` и проверит парность ключей.

## Release Read
**Не release-ready.** Это intermediate commit. Готово к ревью Codex-ом, но не к
деплою. Перед релизом нужны Commit B (миграция) и Commit C (bootstrap URL).

## Reviewer Ask (для Codex)

Аудитируй с приоритетом P0/P1:

1. **P0/P1**: проверить, что sed-замены не сломали ничего семантически:
   - Не появились ли коллизии, например `GOVLESS_GOVLESS_VERSION` где-то.
   - Не пропустил ли я surface — например, journald-теги, certbot renewal hooks,
     cron-задачи, systemd-юниты (если они создаются по имени `xuifast`).
   - Не сломал ли `/tmp/xuifast_*` → `/tmp/govless_*` где-то, где это имя
     хардкоднуто как regex pattern (например, в EXIT-trap `rm -f /tmp/xuifast_*`).
2. **P1**: проверить парность i18n-ключей en/ru — не разъехались ли после правки.
3. **P2**: nginx-конфиг — site name `xuifast` → `govless`. Проверь, что если у
   юзера уже стоит nginx с symlink `sites-enabled/xuifast`, он отвалится тихо или
   с понятной ошибкой (это уже про Commit B миграцию, но если есть быстрый фикс
   в Commit A — отметь).
4. **P3**: стиль/нейминг внутренних функций (если ты вообще будешь смотреть на
   функции — здесь только rename).

Файлы для аудита — текущий `main` после этого коммита.

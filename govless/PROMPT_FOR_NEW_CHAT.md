# Промт для нового чата — копируй целиком

Ты подключаешься к проекту **goVLESS** — bash-скрипт автоматической установки VPN
(3X-UI + Xray + VLESS Reality/TLS) для нетехнических пользователей.

## Репозиторий и доступ
- **Репо:** https://github.com/potap1978/go-all (private)
- **Токен:** локальный `gh auth login` или env `GH_TOKEN` — **никогда не в файлах репо**
- **Ветка:** `main`

## Роли (по skill `coordinating-ai-duos`)
- Architect: Claude
- Reviewer: Codex
- Human: Vitalii (potap1978)

## Первым делом
```bash
gh repo clone potap1978/go-all
cd goVLESS
cat GOVLESS_DOCS.md
cat ai-bridge/README.md
```

## Кратко о проекте
- 2 режима: Lite (Reality, без домена) и Pro (TLS, свой домен)
- 2 версии 3X-UI: New Generation (3.x) и Legacy (2.x)
- 3 транспорта: TCP, XHTTP, gRPC
- i18n: русский + английский

## Ключевые требования
1. UX foolproof, рамки только `═══`, без боксов `╔═╗║╚╝` в runtime-выводе
2. Панель 3X-UI всегда доступна, даже если автонастройка не удалась
3. `set -euo pipefail`, `grep` в пайплайнах через `|| true`
4. i18n: любая новая строка → в `lib/lang/ru.sh` И `lib/lang/en.sh`
5. `bash -n` на все `.sh` перед коммитом
6. 4 тест-комбинации перед релизом: Lite×NewGen, Lite×Legacy, Pro×NewGen, Pro×Legacy

## VPS для тестов
- IP, домен и доступ — у пользователя локально, **в репо не лежат**
- Доступ только по SSH-ключу

## Текущая версия
`GOVLESS_VERSION` в `lib/common.sh`

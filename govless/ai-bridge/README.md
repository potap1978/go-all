# AI Bridge

Эта папка — durable handoff-канал между AI-агентами по протоколу
`coordinating-ai-duos`.

## Структура
```
ai-bridge/
└── claude-codex/         # Architect=Claude, Reviewer=Codex
    ├── 001-<topic>.md    # Architect proposal или Reviewer audit
    ├── 002-<topic>.md
    └── ...
```

Нумерация монотонная. Имя файла включает роль автора и тему.

## Типы сообщений
- `NNN-architect-proposal-<topic>.md` — Architect предлагает изменение
- `NNN-reviewer-audit-<topic>.md` — Reviewer аудитит (GO / NO-GO / GO after fixes)
- `NNN-architect-fix-report-<topic>.md` — Architect отчитывается о фиксах
- `NNN-reviewer-release-readiness-<topic>.md` — финальный вердикт перед релизом

## Severity
- **P0** — data loss / security leak / prod outage. Релиз заблокирован.
- **P1** — видимая поломка / прожиг квоты / миграция. Блок без явного human accept.
- **P2** — баг с воркэраундом. Track или дешёвый фикс.
- **P3** — полиш, документация, мелочи.

## Правила
1. Никогда не класть сюда секреты (токены, пароли, ключи).
2. Каждое утверждение — со ссылкой на код, лог или явной пометкой "inference".
3. Каждый P0/P1 Architect должен явно ответить: fix / reject + rationale / follow-up ticket.
4. "Done" без верификации = не done.

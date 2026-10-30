# Архив документов

Устаревшие документы перенесены сюда 27.09.2026 через `git mv`, история сохранена (`git log --follow <файл>`). Пути
внутри файлов — прежние, до переноса; поправлены только ссылки-переходы между ними. Актуально — [MAP.md](../MAP.md) и
[FINDINGS_TRACKER.md](../FINDINGS_TRACKER.md). Прототипы дизайна итерации 1 — в `handoff/archive/`.

| Файл | Что это | Чем заменено |
|---|---|---|
| [AUDIT.md](AUDIT.md) | «актуальное состояние» ветки `dev` на версии 0.1.2, был в корне | [MAP.md](../MAP.md), трекер |
| [AUDIT_TASK.md](AUDIT_TASK.md) | задание внешнему аудитору на 0.1.0, был в корне | трекер (находки и статусы) |
| [STAND.md](STAND.md) | план приёмки на стенде для 0.1.2, был в `docs/` | `tests/live/acceptance.py`, `tests/live/README.md` |
| [MENU_REVIEW.md](MENU_REVIEW.md) | ручной обход экранов SSH, бота и панели до 0.5.0, был в `tests/` | `handoff/qa/panel-screens.json`, `tg-scenes.json`; `tests/live/` |
| [DESIGN_COVERAGE.md](DESIGN_COVERAGE.md) | покрытие продукта дизайном итерации 1 и задание на итерацию 2 (26.09) | `docs/DESIGN_COVERAGE_2.md`, `docs/DESIGN_ITERATION3_ACCEPTANCE.md` |
| [DESIGN_COVERAGE_MATRIX.md](DESIGN_COVERAGE_MATRIX.md) | матрица «функции × состояния» к итерации 1 (25.09) | то же |
| [CLAUDE_DESIGN_ITERATION_2.md](CLAUDE_DESIGN_ITERATION_2.md) | задание Claude Design на итерацию 2 (выполнено) | `handoff/iteration2/`, затем `docs/DESIGN_STAGE2_BRIEF.md` и `handoff/iteration3/` |
| [wave1-panel.md](wave1-panel.md) | итог исполнителя волны 1 по панели (0.4.6), был в `docs/reports/` | трекер; панель 0.5.0 — [MAP.md](../MAP.md) §6 |
| [ai-bridge/codex-opus/001…](ai-bridge/codex-opus/001-codex-to-opus-proposal-release-046.md) | Codex → Opus: продолжение 0.4.6, был в `ai-bridge/` | трекер (итоги Codex: HM-1, BOT-R1, SEC-R1…R3, BOOT-R1, UP-R1) |
| [ai-bridge/codex-opus/002…](ai-bridge/codex-opus/002-codex-to-opus-fix-report-integration.md) | Codex → Opus: слияния и найденные стыки | то же |
| [ai-bridge/codex-opus/003…](ai-bridge/codex-opus/003-codex-to-opus-fix-report-validation.md) | Codex → Opus: дизайн, безопасность и ход приёмки | то же |
| [ai-bridge/codex-opus/004…](ai-bridge/codex-opus/004-codex-to-opus-fix-report-handoff.md) | Codex → Opus: передача кандидата 0.4.6 (выпущена 27.09) | то же |

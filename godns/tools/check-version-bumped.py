#!/usr/bin/env python3
"""Ругается, если код изменился, а GODNS_VERSION осталась прежней.

Зачем: обновление поверх сравнивает версии. При равных версиях установщик
считает происходящее переустановкой, а не обновлением — не снимает копию,
не зовёт нормализацию состояния и НЕ ИСПОЛНЯЕТ ступени миграции схемы.
Код на сервере обновится, состояние останется прежним, и разъезд всплывёт
позже и не там, где его сделали.

Полагаться тут на память нельзя: забыть поднять версию легко, а заметить
последствие — трудно.
"""
import re
import subprocess
import sys

VER_RE = re.compile(r'^GODNS_VERSION="([^"]+)"', re.M)
# Файлы, изменение которых требует новой версии. Документация и служебное
# сюда не входят: правка README не меняет поведения на сервере.
CODE = re.compile(r'^(godns\.sh|install\.sh|lib/|tools/|test_)')


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout


def version_at(ref):
    src = sh("git", "show", f"{ref}:lib/common.sh")
    m = VER_RE.search(src)
    return m.group(1) if m else None


def _vkey(v):
    """Числовые разряды версии для сравнения. Хвост вида «-rc1» отбрасываем:
    он не участвует в порядке, который проверяет godns_version_lt."""
    core = re.split(r'[^0-9.]', v, 1)[0]
    out = []
    for part in core.split("."):
        out.append(int(part) if part.isdigit() else 0)
    return out


def _newer(was, now):
    a, b = _vkey(was), _vkey(now)
    n = max(len(a), len(b))
    a += [0] * (n - len(a))
    b += [0] * (n - len(b))
    return b > a


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "HEAD"

    # Вне git-дерева (распакованный архив на сервере) git падает, sh()
    # отдаёт пустую строку, список выходит пустым — и проверка бодро
    # печатала «код не менялся», не осмотрев НИЧЕГО. Ровно тот класс, про
    # который в CLAUDE.md сказано: страж обязан считать осмотренное.
    if sh("git", "rev-parse", "--is-inside-work-tree").strip() != "true":
        print("✗ не git-дерево: сравнивать не с чем, проверка не состоялась",
              file=sys.stderr)
        return 1

    changed = [l for l in sh("git", "diff", "--name-only", base).splitlines() if l]
    changed += [l for l in sh("git", "diff", "--cached", "--name-only").splitlines() if l]
    changed = sorted({c for c in changed if CODE.match(c)})

    if not changed:
        print("✓ код не менялся — версию поднимать не за что")
        return 0

    try:
        with open("lib/common.sh", encoding="utf-8") as fh:
            now = VER_RE.search(fh.read())
        now = now.group(1) if now else None
    except OSError:
        now = None
    was = version_at(base)

    if not now or not was:
        print("⚠ не удалось прочитать GODNS_VERSION — проверьте вручную")
        return 0

    if now != was:
        # «Отличается» — не то же самое, что «поднята». Понижение
        # 0.25.0 → 0.1.0 проходило как успех, а на сервере это худший
        # исход из возможных: godns_update_state вернёт «older», и
        # установщик решит, что его откатывают назад. Сравниваем ростом,
        # тем же порядком, что и сам продукт (sort -V).
        if not _newer(was, now):
            print(f"✗ версия не поднята, а понижена: {was} → {now}")
            print()
            print("  Обновление поверх сравнивает версии по порядку, а не на")
            print("  неравенство. Понижение — не релиз, а откат состояния.")
            return 1
        print(f"✓ версия поднята: {was} → {now} ({len(changed)} файл(ов) кода изменено)")
        return 0

    print(f"✗ код изменён ({len(changed)} файл(ов)), а GODNS_VERSION осталась {now}")
    for c in changed[:8]:
        print(f"    {c}")
    if len(changed) > 8:
        print(f"    … и ещё {len(changed) - 8}")
    print()
    print("  Поднимите GODNS_VERSION в lib/common.sh. При равных версиях")
    print("  обновление поверх считает себя переустановкой: не снимает копию,")
    print("  не зовёт нормализацию и не исполняет миграции схемы.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

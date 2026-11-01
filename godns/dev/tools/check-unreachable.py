#!/usr/bin/env python3
"""Ловит функции, которые определены, переведены — и никем не вызываются.

Зачем отдельно от check-callgraph: та проверяет «вызов → определение»,
то есть ловит опечатку в имени. Обратное направление не сторожил никто, и
в продукте накопилось тринадцать готовых, отлаженных и переведённых
функций, не подключённых ни к одному пункту меню: «проверить снаружи»,
слежение за сертификатом acme.sh, карточка провайдера, выбор языка.

Это не «мёртвый код» в обычном смысле — это оплаченная работа, до которой
пользователь не может дотянуться. Стоит дешевле всего исправить и дороже
всего не заметить.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Вызываются диспетчером по имени, а не текстом: «<движок>_<действие>» и
# ступени миграции «godns_migrate_step_<n>» (lib/update.sh собирает имя из
# номера схемы). Их отсутствие в явных вызовах нормально.
DISPATCHED = re.compile(r'^(dnsdist)_|^godns_migrate_step_')

# Точки входа и обработчики, у которых вызывающего нет по определению.
ENTRY = {"main", "main_menu"}

# Аксессоры и служебные помощники: их отсутствие среди вызовов — не потеря
# функции, а просто ещё не понадобившаяся часть API модуля. Держим список
# явным, чтобы новая осиротевшая ВОЗМОЖНОСТЬ сразу бросалась в глаза.
LIBRARY_API = {
    "certs_cert_path", "certs_key_path", "certs_domain", "certs_usable",
    "certs_count", "certs_covers_name", "certs_select",
    "prov_doh_hostname", "prov_bootstrap_first", "prov_ids_by_region",
    "prov_ids_unfiltered", "prov_geo_remember", "prov_selftest",
    "bench_summary", "bench_sorted_ids",
    "engine_loaded", "engine_render_config", "engine_reload",
    # Доказательство арифметики бюджета логов. Меню его не зовёт намеренно:
    # это утверждение о константах, и его место — в test_common.sh.
    "godns_logs_budget_ok",
    # Осознанный API модуля статистики: окно голым числом и спарклайн
    # произвольной ширины. Панель и экран ходят через godns_stats_line /
    # _stats_report (ОДИН вызов python3 на все окна и оба спарклайна
    # разом — панель обновляется раз в 10 секунд, и три запуска на кадр
    # были бы тратой). Поштучные функции проверяются в test_common.sh.
    "godns_stats_window", "godns_stats_sparkline",
    # Точка входа для install.sh: он зовёт её из свежераспакованного дерева
    # ДО подмены программы. Сам install.sh этот инструмент не сканирует
    # (только godns.sh и lib/), поэтому вызывающего здесь не видно.
    "godns_update_prepare",
}


def main() -> int:
    files = [ROOT / "godns.sh"]
    files += sorted(p for p in (ROOT / "lib").rglob("*.sh") if p.parent.name != "lang")
    # Мосты бота и панели — тоже наш код и тоже зовут lib/*.sh. Без них
    # проверка объявляла бы недостижимыми девять функций webui_* и весь
    # фасад бота: их вызывающий лежит в godns-bot/actions.sh и
    # godns-web/bridge.sh, куда сканер раньше не заглядывал. Заглушить
    # предупреждение списком исключений было бы хуже — тогда настоящая
    # осиротевшая функция в этих файлах прошла бы незамеченной.
    files += sorted(ROOT.glob("godns-bot/*.sh"))
    files += sorted(ROOT.glob("godns-web/*.sh"))

    defined: dict[str, str] = {}
    bodies: list[str] = []
    for f in files:
        rel = f.relative_to(ROOT)
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\)', line)
            if m:
                defined.setdefault(m.group(1), f"{rel}:{n}")
        # Комментарии выбрасываем: упоминание в комментарии — не вызов.
        bodies.append("\n".join(
            l.split(" #")[0] for l in f.read_text(encoding="utf-8").splitlines()
            if not l.lstrip().startswith("#")
        ))
    blob = "\n".join(bodies)

    dead = []
    for name, where in sorted(defined.items()):
        if name in ENTRY or name in LIBRARY_API or DISPATCHED.match(name):
            continue
        uses = len(re.findall(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'(?![A-Za-z0-9_])', blob))
        # Одно вхождение — это само определение.
        if uses <= 1:
            dead.append((name, where))

    strict = "--strict" in sys.argv
    if dead:
        mark = "✗" if strict else "⚠"
        print(f"{mark} {len(dead)} function(s) defined but never called:")
        for name, where in dead:
            print(f"    {name:<30} {where}")
        print("\n  Either wire it into a menu, or delete it. A finished feature the")
        print("  operator cannot reach is worse than no feature: it looks done.")
        print("  If it is deliberate library API, add it to LIBRARY_API here.")
        return 1 if strict else 0

    print(f"✓ no unreachable functions — {len(defined)} defined, {len(files)} file(s) scanned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

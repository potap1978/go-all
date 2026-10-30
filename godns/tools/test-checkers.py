#!/usr/bin/env python3
"""Отрицательные тесты для самих проверок.

Зачем отдельный файл. check-callgraph ловит разъезд имён между модулями —
самый вероятный способ уронить меню в этом проекте. Но проверка, которая
молча пропускает ошибку, хуже отсутствующей: на зелёный прогон полагаются.
Две первые версии callgraph пропускали опечатку в `if fn; then`, а третья
приняла метку `case` за вызов и выдала пять ложных срабатываний на
диспетчерах бота и веб-моста.

Поэтому здесь проверяется не код проекта, а СПОСОБНОСТЬ проверки отличать
вызов от не-вызова — в обе стороны.
"""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CASES = [
    # (описание, строка кода, имя, ожидается ли имя среди вызовов)
    ("метка case — не вызов",       '    cert_info) bot_act_cert_info "$@" ;;', "cert_info",         False),
    ("команда после метки — вызов", '    cert_info) bot_act_cert_info "$@" ;;', "bot_act_cert_info", True),
    ("метка со звёздочкой",         "    *) log_warning ;;",                    "log_warning",       True),
    ("метка ''|\\?)",               "    ''|\\?) days_txt=x ;;",                "days_txt",          False),
    ("$(имя) — вызов",              "x=$(prov_list_ids); do",                   "prov_list_ids",     True),
    ("if имя; then — вызов",        "if engine_is_running; then",               "engine_is_running", True),
    ("опечатка в if ловится",       "if engien_is_running; then",               "engien_is_running", True),
    ("определение — не вызов",      "certs_discover() {",                       "certs_discover",    False),
    ("скобка в кавычках",           'echo "готово)" ; certs_validate',          "certs_validate",    True),
    ("подоболочка",                 "( cd /tmp && certs_validate )",            "certs_validate",    True),
    ("однострочная обёртка",        "bot_act_x() { engine_upstream_report; }",  "engine_upstream_report", True),
    ("подстановка ${x} — не вызов", 'echo "${st_good}"',                        "st_good",           False),
    ("${x:-y} — не вызов",          'v="${st_bad:-—}"',                         "st_bad",            False),
]


def main() -> int:
    cg = load("check-callgraph")
    bad = 0
    for label, code, name, want in CASES:
        got = name in set(cg.call_names(code))
        if got != want:
            bad += 1
            print(f"  ✗ {label}: ожидалось {want}, вышло {got}  ({code!r})")
    if bad:
        print(f"✗ {bad} of {len(CASES)} checker self-test(s) failed")
        return 1
    print(f"✓ checker self-tests pass — {len(CASES)} case(s)")
    return 0




# ── Отрицательные тесты для остальных проверяльщиков ────────────────────
# У call_names они были, у прочих — нет, и именно поэтому его слепота к
# аргументам trap и транзитивная слепота check_source_boundaries прожили
# незамеченными до мутационного аудита. Метод один: берём ЖИВОЕ дерево,
# вносим в его КОПИЮ одну поломку и требуем, чтобы проверка покраснела.
def _mutate_and_run(tool: str, edits, argv=None):
    """Копирует дерево, применяет правки, зовёт проверку. Возвращает её rc."""
    import shutil, subprocess, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        dst = pathlib.Path(tmp) / "tree"
        shutil.copytree(ROOT, dst, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "*.tar.gz*"))
        for rel, old, new in edits:
            f = dst / rel
            txt = f.read_text(encoding="utf-8")
            if old not in txt:
                return ("НЕ НАЙДЕНО", rel, old[:60])
            f.write_text(txt.replace(old, new, 1), encoding="utf-8")
        cmd = [sys.executable, str(dst / "tools" / f"{tool}.py")] + (argv or [])
        r = subprocess.run(cmd, cwd=dst, capture_output=True, text=True)
        return r.returncode


MUTATIONS = [
    # (описание, инструмент, правки, аргументы)
    ("callgraph видит опечатку в аргументе trap", "check-callgraph",
     [("godns.sh", "trap cleanup_temp_files EXIT", "trap cleanup_tmp_files EXIT")], None),
    ("source-граница видит вызов из библиотеки", "check-callgraph",
     [("lib/engine.sh", "engine_upstream_report() {",
       "engine_upstream_report() {\n    firewall_open_ports")], None),
    ("str-usage видит несуществующий ключ меню", "check-str-usage",
     [("lib/logs.sh", "$(t logs_cut_hint)", "$(t logs_cut_hintt)")], None),
    ("str-usage видит опечатку в ключе бота", "check-str-usage",
     [("godns-bot/bot.py", '_t(user_id, "btn_back")', '_t(user_id, "btn_backk")')], None),
    ("parity видит ключ только в одном языке", "check-str-parity",
     [("lib/lang/ru.sh", 'I18N[back]=', 'I18N[back_ru_only]=')], None),
    ("unreachable видит функцию без вызовов", "check-unreachable",
     [("lib/logs.sh", "godns_logs_file_mb() {",
       "godns_never_called_anywhere() { return 0; }\ngodns_logs_file_mb() {")], ["--strict"]),
]

# check-version-bumped мутацией не проверить: копия дерева идёт БЕЗ .git,
# и проверка честно падает «не git-дерево» независимо от правки — то есть
# тест зеленел бы, ничего не доказав. Сравнение версий проверяем напрямую.
VERSION_ORDER = [
    ("0.9.0",  "0.10.0", True),   # не строковое сравнение
    ("0.10.0", "0.9.0",  False),  # понижение — не подъём
    ("0.25.1", "0.25.2", True),
    ("0.23.0", "0.1.0",  False),  # ровно та мутация из аудита
    ("1.0.0",  "1.0.0",  False),  # равенство — не подъём
    ("0.9.9",  "1.0.0",  True),
]


def check_mutations() -> int:
    bad = 0
    for label, tool, edits, argv in MUTATIONS:
        rc = _mutate_and_run(tool, edits, argv)
        if isinstance(rc, tuple):
            bad += 1
            print(f"  ✗ {label}: не нашёл, что ломать — {rc[1]}: {rc[2]!r}")
        elif rc == 0:
            bad += 1
            print(f"  ✗ {label}: проверка осталась зелёной")
    cv = load("check-version-bumped")
    for was, now, want in VERSION_ORDER:
        if cv._newer(was, now) != want:
            bad += 1
            print(f"  ✗ порядок версий {was} → {now}: ожидалось {want}")
    total = len(MUTATIONS) + len(VERSION_ORDER)
    if bad:
        print(f"✗ {bad} из {total} проверок не заметили поломку")
        return 1
    print(f"✓ проверяльщики кусаются — {len(MUTATIONS)} мутаций(я) поймано, "
          f"{len(VERSION_ORDER)} сравнений(я) версий")
    return 0


if __name__ == "__main__":
    rc = main()
    rc |= check_mutations()
    sys.exit(rc)

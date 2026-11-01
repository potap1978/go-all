#!/usr/bin/env bash
# Быстрый прогон разделов tools/k3_install_test.sh (подделки — из его начала, помощники теста — gw_st, gw_list,
# gw_calls, GW_PROBE_OK… — из всех разделов: определения функций и однострочные присваивания верхнего уровня):
#   bash tools/i18n_h/run_section.sh 49H        — разделы, чей заголовок «── 49H. …» (одинаковый номер — все)
#   bash tools/i18n_h/run_section.sh 18 22 49H  — несколько
# Полная проверка — только bash tools/k3_test.sh.
set -u
D="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
T="$D/tools/k3_install_test.sh"
tmp=$(mktemp "${TMPDIR:-/tmp}/k3sec.XXXXXX") || exit 2
trap 'rm -f "$tmp"' EXIT
python3 - "$T" "$D" "$@" > "$tmp" <<'PY' || exit 2
import re, subprocess, sys
path, root, want = sys.argv[1], sys.argv[2], sys.argv[3:]
L = open(path, encoding="utf-8").read().split("\n")
first = next(i for i, l in enumerate(L) if l.startswith('echo "── 1. '))
fin = max(i for i, l in enumerate(L) if l == 'echo ""')
pre = [('ROOT_DIR="%s"' % root) if l.startswith("ROOT_DIR=") else l for l in L[:first]]
heads = [i for i in range(first, fin) if L[i].startswith('echo "── ')]
defs, i = [], first
FN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\(\)\s*\{")
VAR = re.compile(r"^([A-Z_][A-Z0-9_]*)=")
REF = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
# Переменные, известные к началу разделов: присвоенные в начале теста (подделки, CUR, NEWER…) и взятые ниже.
known = {m.group(1) for m in (re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", l) for l in pre) if m}


def parsed_end(i):
    """Конец конструкции с строки i — первая строка, на которой она разбирается целиком (bash -n)."""
    j = i
    while j < fin and subprocess.run(["bash", "-n"], input="\n".join(L[i:j + 1]), text=True,
                                     stderr=subprocess.DEVNULL).returncode != 0:
        j += 1
    return j


while i < fin:
    l = L[i]
    if FN.match(l):
        j = parsed_end(i)
        defs += L[i:j + 1]; i = j + 1; continue
    m = VAR.match(l)
    if m:
        # Присваивание верхнего уровня, в том числе многострочное (HEAD_BASE='{…}' в разделе 44): берётся, если
        # не запускает команд ($(…), `…`) и ссылается только на уже известные переменные — иначе под set -u
        # предзагрузка упала бы «unbound variable» до начала нужного раздела.
        j = parsed_end(i)
        chunk = "\n".join(L[i:j + 1])
        if "$(" not in chunk and "`" not in chunk and set(REF.findall(chunk)) <= known:
            defs += L[i:j + 1]; known.add(m.group(1)); i = j + 1; continue
    i += 1
body = []
for k, h in enumerate(heads):
    m = re.match(r'echo "── ([0-9A-Za-z]+)\. ', L[h])
    if m and m.group(1) in want:
        end = heads[k + 1] if k + 1 < len(heads) else fin
        body += L[h:end]
print("\n".join(pre + defs + body))
print('echo ""; if [ "$FAILED" = "0" ]; then echo "  ВСЁ ЗЕЛЕНО ($PASSED)"; exit 0; fi; echo "  ЕСТЬ ПРОБЛЕМЫ"; exit 1')
PY
bash "$tmp"

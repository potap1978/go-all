#!/usr/bin/env bash
# k3_install_test.sh — install.sh Kaskad 3 (SPEC-wave1 §5.7) без root и без сети.
#
# Поддельные iptables, systemctl, nft, curl, ufw, netfilter-persistent лежат первыми в PATH,
# поддельный kaskadctl — в $K3_OPT/bin (через него install.sh зовёт ядро). Все пути сервера —
# во временном каталоге: KASKAD_ROOT, K3_OPT, KASKAD_DIR. install.sh подключается режимом --lib
# в подоболочке; служебные режимы (--k3-post-update) запускаются отдельным процессом.
# Нужны bash 4+, python3, jq.
#
# Usage: bash tools/k3_install_test.sh
set -u
# Homebrew bash 5.3 на macOS без LANG и LC_ALL: подоболочка, которая возвращает локаль после «local LC_ALL=C»
# (k3_pg_fold, k3_pg_height), изредка падает с SIGSEGV — CoreFoundation не переживает fork (libintl_setlocale →
# CFLocaleCopyPreferredLanguages). На Linux этого нет. Как в awg_core_selftest.sh: только Darwin и только без локали.
if [ "$(uname -s)" = Darwin ] && [ -z "${LANG:-}" ] && [ -z "${LC_ALL:-}" ]; then export LANG=en_US.UTF-8; fi
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$ROOT_DIR/install.sh"
# the version of this install.sh and one above it (self_update tests): no hard-coded release numbers
CUR=$(sed -n 's/^KASKAD_VERSION="\([^"]*\)".*/\1/p' "$S" | head -1)
NEWER=$(printf '%s\n' "$CUR" | awk -F. '{printf "%d.%d.%d", $1, $2, $3 + 1}')
export KASKAD_TEST_CUR="$CUR"          # the fake kaskadctl answers «version» with it
# Временный каталог обязан быть: иначе W пустой, и подделки легли бы в /bin (на сервере — /usr/bin).
W="$(mktemp -d 2>/dev/null)"
case "$W" in /|/bin|/usr|/usr/bin|"") echo "mktemp -d не сработал — тест не запускаю" >&2; exit 2 ;; esac
[ -d "$W" ] && [ -z "$(ls -A "$W")" ] || { echo "mktemp -d не сработал — тест не запускаю" >&2; exit 2; }
trap 'rm -rf "$W"' EXIT
# Пути Kaskad выводятся из KASKAD_ROOT каждого раздела: унаследованные переменные не должны увести
# тест в настоящие /usr/local/bin, /etc/systemd/system, /var/backups.
unset K3_CMD K3_UNIT_DIR K3_BACKUPS K3_REPO K3_LOCK K3_LOCK_WAIT K3_VARLIB K3_RUNDIR K3_LOGROTATE EGRESS_POOL_LOCK EGRESS_POOL_WAIT EGRESS_NO_TEST
# Язык меню (KASKAD_LANG): тесты — по-русски, кроме раздела 48; язык из окружения разработчика их не уводит.
unset KASKAD_LANG
unset KASKAD_CONFIRM_NOT_RF           # ⚠РФ (раздел 54): согласие из окружения разработчика не попадает в первую установку
FAILED=0; PASSED=0
ok(){  printf '  PASS %s\n' "$1"; PASSED=$((PASSED+1)); }
bad(){ printf '  FAIL %s\n' "$1"; [ -n "${2:-}" ] && printf '       %s\n' "$2"; FAILED=1; }
strip(){ sed 's/\x1b\[[0-9;]*m//g'; }
# Строки, перенесённые по словам (меню 17 — в ширину окна), — снова в одну: фразу можно искать целиком.
flat(){ tr '\n' ' ' | tr -s ' '; }

export CALLS="$W/calls" FAKE_DIR="$W/fake" IPT_DIR="$W/ipt" PYTHONDONTWRITEBYTECODE=1
mkdir -p "$W/bin" "$CALLS" "$FAKE_DIR/gh" "$IPT_DIR"

# ── поддельные программы ────────────────────────────────────────────────────
# iptables: -t, -S/-A/-I/-D/-C по цепочке, правила — строки файла (как в free_rules_adopt_test).
cat > "$W/bin/iptables" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALLS/iptables.log"
[ "${1:-}" = "-V" ] && { echo "iptables v1.8.10 (nf_tables)"; exit 0; }
t=filter; a=()
while [ $# -gt 0 ]; do case "$1" in -t) t="$2"; shift 2 ;; *) a+=("$1"); shift ;; esac; done
op="${a[0]}"; ch="${a[1]:-}"; spec="${a[*]:2}"; spec="${spec//\"/}"
f="$IPT_DIR/$t.$ch"; touch "$f"
case "$op" in
  -S) while IFS= read -r l; do [ -n "$l" ] && echo "-A $ch $l"; done < "$f" ;;
  -A) printf '%s\n' "$spec" >> "$f" ;;
  -I) { printf '%s\n' "$spec"; cat "$f"; } > "$f.t" && mv "$f.t" "$f" ;;
  -C) grep -qxF -- "$spec" "$f" ;;
  -D) n=$(grep -nxF -- "$spec" "$f" | head -1 | cut -d: -f1); [ -n "$n" ] || exit 1
      awk -v n="$n" 'NR!=n' "$f" > "$f.t" && mv "$f.t" "$f" ;;
  *) exit 0 ;;
esac
MOCK
# systemctl: журнал вызовов; is-active — по списку $FAKE_DIR/active; restart поднимает службу,
# а при флаге dies_once.<имя> — «роняет» её один раз (проверка отката self_update).
cat > "$W/bin/systemctl" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALLS/systemctl.log"
act="$FAKE_DIR/active"; touch "$act"
# Ноги AmneziaWG — юниты kaskad-leg@kawgN (3.2): запуск делает то, что делает юнит — amneziawg-go из шаблона
# (ExecStart=… -f %i) и awg setconf, а с drop-in kernel.conf — интерфейс модуля ядра (kmod_fail — не создаётся);
# остановка — его ExecStopPost (ip link delete). Нет шаблона — запуск не удаётся.
leg_i() { local i="${1#kaskad-leg@}"; echo "${i%.service}"; }
leg_start() {
    local u="$1" i tpl go tool
    i=$(leg_i "$u"); tpl="$KASKAD_ROOT/etc/systemd/system/kaskad-leg@.service"
    [ -f "$tpl" ] || return 1
    go=$(sed -n 's/^ExecStart=\(.*\) -f %i$/\1/p' "$tpl"); tool=$(sed -n 's/^ExecStartPost=\(.*\) setconf %i .*/\1/p' "$tpl")
    ip link delete dev "$i" >/dev/null 2>&1
    if [ -f "$KASKAD_ROOT/etc/systemd/system/$u.d/kernel.conf" ]; then
        [ -f "$FAKE_DIR/kmod_fail" ] && return 1
        ip link add "$i" type amneziawg || return 1
        echo "kernel $i" >> "$CALLS/core.log"
    else
        "$go" -f "$i" || return 1
    fi
    "$tool" setconf "$i" "$KASKAD_DIR/egress/awg/.$i.conf"
}
leg_off() { grep -vxF "${1%.service}" "$act" > "$act.t"; mv -f "$act.t" "$act"; }
# Как в systemd: stop останавливает загруженный юнит и без файла («not-found», но active); остановка юнита, чей
# файл уже стёрт, — в stop_nofile.log (полное удаление обязано останавливать раньше, чем стирать).
# disable — только по файлам: нет файла хоть одного юнита (у экземпляра — шаблона) — отказ всей команды, и
# «--now» никого не останавливает (так 3.2.19 оставлял kaskad-egress: «disable --now … kaskad-egress-probe.timer»).
unit_file() { local u="$1"; case "$u" in *@?*.service) u="${u%%@*}@.service" ;; *.service|*.timer|*.slice|*.socket|*.path) ;; *) u="$u.service" ;; esac; echo "$KASKAD_ROOT/etc/systemd/system/$u"; }
unit_stop() {
    local u="$1" n="${1%.service}"
    case "$u" in kaskad-leg@*) leg_off "$u"; ip link delete dev "$(leg_i "$u")" >/dev/null 2>&1; return 0 ;; esac
    if grep -qxF "$n" "$act"; then
        case "$u" in *.slice) ;; *) [ -e "$(unit_file "$u")" ] || echo "$n" >> "$CALLS/stop_nofile.log" ;; esac
        leg_off "$u"
    fi
}
case "${1:-} ${2:-}" in
  "restart kaskad-leg@"*|"start kaskad-leg@"*)
    if leg_start "$2"; then grep -qx "${2%.service}" "$act" || echo "${2%.service}" >> "$act"; exit 0; fi
    leg_off "$2"; ip link delete dev "$(leg_i "$2")" >/dev/null 2>&1; exit 1 ;;
esac
case "${1:-}" in
  stop)
    shift; for u in "$@"; do case "$u" in -*) ;; *) unit_stop "$u" ;; esac; done; exit 0 ;;
  disable)
    shift; now=""; us=()
    for u in "$@"; do case "$u" in --now) now=1 ;; -*) ;; *) us+=("$u") ;; esac; done
    for u in "${us[@]}"; do
        [ -e "$(unit_file "$u")" ] || [ -L "$KASKAD_ROOT/etc/systemd/system/$u" ] \
            || { echo "Failed to disable unit: Unit file $u does not exist." >&2; exit 1; }
    done
    [ -n "$now" ] && for u in "${us[@]}"; do unit_stop "$u"; done
    exit 0 ;;
  list-units)
    while IFS= read -r n; do
        case "$n" in kaskad-*|system-kaskad*) ;; *) continue ;; esac
        case "$n" in *.service|*.timer|*.slice) u="$n" ;; *) u="$n.service" ;; esac
        if [ -e "$(unit_file "$u")" ]; then printf '  %s loaded active running x\n' "$u"
        else printf '\xe2\x97\x8f %s not-found active running %s\n' "$u" "$u"; fi
    done < "$act"
    exit 0 ;;
esac
case "${1:-}" in
  list-jobs)                                   # задания systemd (при загрузке): юниты из $FAKE_DIR/jobs
    shift; for u in "$@"; do case "$u" in -*) ;; *) grep -qx "${u%.service}" "$FAKE_DIR/jobs" 2>/dev/null \
        && printf '7 %s start waiting\n' "$u" ;; esac; done; exit 0 ;;
  is-active)
    shift; [ "${1:-}" = "--quiet" ] && shift
    grep -qx "${1%.service}" "$act" && { echo active; exit 0; }
    echo inactive; exit 3 ;;
  restart)
    u="${2%.service}"
    if [ -f "$FAKE_DIR/dies_once.$u" ]; then
        rm -f "$FAKE_DIR/dies_once.$u"; grep -vx "$u" "$act" > "$act.t"; mv -f "$act.t" "$act"
    else
        grep -qx "$u" "$act" || echo "$u" >> "$act"
    fi ;;
esac
exit 0
MOCK
# curl: install.sh веток и тегов — из $FAKE_DIR/gh/<ref>.sh; Telegram — журнал тела запроса.
# «-K -» / «--config -» — настройки из stdin (адрес с токеном бота, прокси): строки url и proxy читаются; argv
# каждого вызова — в $CALLS/curl_argv.log (проверка: токен бота в argv не попадает).
cat > "$W/bin/curl" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALLS/curl_argv.log"
out="" url="" data="" cfg=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -d|--data) data="$2"; shift 2 ;;
    --config|-K) cfg="$2"; shift 2 ;;
    -w|-X|-H|--max-time|--connect-timeout|--proxy|--retry|--socks5-hostname|-m) shift 2 ;;
    http://*|https://*) url="$1"; shift ;;
    *) shift ;;
  esac
done
if [ "$cfg" = "-" ]; then
  while IFS= read -r l; do
    case "$l" in
      'url = "'*) url="${l#url = \"}"; url="${url%\"}" ;;
      'proxy = "'*) p="${l#proxy = \"}"; printf '%s\n' "${p%\"}" >> "$CALLS/curl_proxy.log" ;;
    esac
  done
fi
printf '%s%s\n' "$url" "${cfg:+ (key)}" >> "$CALLS/curl.log"
[ -f "$FAKE_DIR/offline" ] && exit 6          # сети нет: адрес не находится
case "$url" in
  https://raw.githubusercontent.com/*/kaskad-pro/*/install.sh)
    ref="${url#https://raw.githubusercontent.com/*/kaskad-pro/}"; ref="${ref%/install.sh}"
    [ -f "$FAKE_DIR/gh/$ref.sh" ] || exit 22
    if [ -n "$out" ]; then cp "$FAKE_DIR/gh/$ref.sh" "$out"; else cat "$FAKE_DIR/gh/$ref.sh"; fi ;;
  https://api.telegram.org/*)
    printf '%s\n' "$data" >> "$CALLS/tg.log"
    echo '{"ok":true,"result":{"message_id":7}}' ;;
  https://api.github.com/repos/XTLS/Xray-core/releases/latest)
    [ -f "$FAKE_DIR/xray_latest" ] || exit 22
    cat "$FAKE_DIR/xray_latest" ;;
  https://github.com/XTLS/Xray-core/releases/download/*) exit 22 ;;   # сам архив не нужен: важен адрес
  *) exit 6 ;;
esac
MOCK
for p in nft netfilter-persistent; do
    printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> "$CALLS/%s.log"\nexit 0\n' "$p" > "$W/bin/$p"
done
cat > "$W/bin/ufw" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALLS/ufw.log"
if [ "$1" = status ] && [ "${2:-}" = numbered ]; then
    cat "$FAKE_DIR/ufw_numbered" 2>/dev/null
elif [ "$1" = --force ] && [ "${2:-}" = delete ]; then
    rm -f "$FAKE_DIR/ufw_numbered"
elif [ -f "$FAKE_DIR/ufw_active" ]; then
    echo "Status: active"
else
    echo "Status: inactive"
fi
MOCK
# apt-get: ничего не ставит; -s печатает $FAKE_DIR/apt_sim (строки «Remv …» — apt что-то удалил бы),
# install выходит с кодом из $FAKE_DIR/apt_rc. dpkg-query: пакет «стоит», если есть $FAKE_DIR/pkg.<имя>.
cat > "$W/bin/apt-get" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALLS/apt.log"
case " $* " in
  *" -s "*) cat "$FAKE_DIR/apt_sim" 2>/dev/null; exit 0 ;;
  *" install "*) exit "$(cat "$FAKE_DIR/apt_rc" 2>/dev/null || echo 0)" ;;
esac
exit 0
MOCK
cat > "$W/bin/dpkg-query" <<'MOCK'
#!/usr/bin/env bash
p="${*: -1}"
[ -f "$FAKE_DIR/pkg.$p" ] && { printf 'install ok installed'; exit 0; }
exit 1
MOCK
# kaskadctl: журнал аргументов; ответы — из файлов $FAKE_DIR (как условились в SPEC §3.7, §4.10).
cat > "$W/kaskadctl.fake" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALLS/kaskadctl.log"
st="$KASKAD_ROOT/etc/kaskad/state.json"
case "${1:-} ${2:-}" in
  "version "*) echo "Kaskad $KASKAD_TEST_CUR" ;;
  "fwd list")
    if [ "${3:-}" = "--traffic" ]; then cat "$FAKE_DIR/fwd_list_traffic" 2>/dev/null; else cat "$FAKE_DIR/fwd_list" 2>/dev/null; fi ;;
  "fwd add")
    case " $* " in *" --force "*) ;; *) [ -s "$FAKE_DIR/conflict.json" ] && { cat "$FAKE_DIR/conflict.json"; exit 3; } ;; esac
    echo '{"ok": true}' ;;
  "fwd del") exit "$(cat "$FAKE_DIR/del_rc" 2>/dev/null || echo 0)" ;;
  "fwd rename"|"server rename")   # T13: ответ — $FAKE_DIR/gw/<fwd|server>_rename.{json,rc}; без них — {"ok": true}
    f="$FAKE_DIR/gw/${1}_rename"
    # имя сервера — в aliases, как настоящее ядро (bot/aliases.rename): заметка, страна и провайдер прежние
    if [ "$1" = server ] && [ ! -f "$f.rc" ] && [ ! -f "$f.once.rc" ]; then
        al="$KASKAD_ROOT/etc/kaskad/aliases"; ip="$3"; nm=""
        for x in "$@"; do case "$x" in --name=*) nm="${x#--name=}" ;; esac; done
        old=$(grep "^${ip//./\\.}=" "$al" 2>/dev/null | head -1 | cut -d= -f2-)
        IFS='|' read -r _ o2 o3 o4 <<< "$old"
        { grep -v "^${ip//./\\.}=" "$al" 2>/dev/null; printf '%s=%s|%s|%s|%s\n' "$ip" "$nm" "$o2" "$o3" "$o4"; } > "$al.t"; mv -f "$al.t" "$al"
    fi
    if [ -f "$f.once.json" ]; then cat "$f.once.json"; rc=$(cat "$f.once.rc" 2>/dev/null || echo 0); rm -f "$f.once.json" "$f.once.rc"; exit "$rc"; fi
    if [ -f "$f.json" ]; then cat "$f.json"; else echo '{"ok": true}'; fi
    exit "$(cat "$f.rc" 2>/dev/null || echo 0)" ;;
  "fwd flush") echo "Удалено правил: 1." ;;
  "migrate scan") [ -s "$FAKE_DIR/scan.json" ] || exit 1; cat "$FAKE_DIR/scan.json" ;;
  "migrate plan") cat "$FAKE_DIR/plan_txt" 2>/dev/null || echo "ПЛАН: перенесу 1 проброс" ;;
  "migrate apply")     # apply_rc — код; apply_sets_nft — движок nft и при ошибке (автооткат не вернул всё)
    rc=$(cat "$FAKE_DIR/apply_rc" 2>/dev/null || echo 0)
    if [ "$rc" = 0 ] || [ -f "$FAKE_DIR/apply_sets_nft" ]; then
        if [ -f "$FAKE_DIR/apply.state" ]; then cp "$FAKE_DIR/apply.state" "$st"; else printf '{\n  "engine": "nft"\n}\n' > "$st"; fi
    fi
    exit "$rc" ;;
  "migrate rollback")  # rollback_rc — код; rollback_keeps_nft — откат не удался, движок прежний
    [ -f "$FAKE_DIR/rollback_keeps_nft" ] || printf '{\n  "engine": "legacy"\n}\n' > "$st"
    cat "$FAKE_DIR/rollback_out" 2>/dev/null
    exit "$(cat "$FAKE_DIR/rollback_rc" 2>/dev/null || echo 0)" ;;
  "migrate backups")
    if [ "${3:-}" = "--json" ]; then cat "$FAKE_DIR/backups.json" 2>/dev/null || echo '{"ok": true, "backups": []}'
    else cat "$FAKE_DIR/backups_txt" 2>/dev/null || echo "20261004T120000Z-pro-2.13"; fi ;;
  "net status")        # net_status — текст, net_status.json — ответ --json
    if [ "${3:-}" = "--json" ]; then cat "$FAKE_DIR/net_status.json" 2>/dev/null || echo '{"ok": true}'
    else cat "$FAKE_DIR/net_status" 2>/dev/null || echo "engine: nft"; fi ;;
  "net teardown") : ;;
  "net apply") exit "$(cat "$FAKE_DIR/net_apply_rc" 2>/dev/null || echo 0)" ;;
  "net install-units") echo "Службы движка установлены." ;;
  "panel unit") : ;;
  "tz "*)              # часовой пояс (T10): tz get | tz set <имя> — ответ ядра; tz.rc — код
    if [ "$2" = set ]; then printf '{"ok": true, "tz": "%s"}\n' "$3"; else echo '{"ok": true, "tz": "Europe/Moscow"}'; fi
    exit "$(cat "$FAKE_DIR/tz.rc" 2>/dev/null || echo 0)" ;;
  "gw "*)
    # Шлюз: ответ — $FAKE_DIR/gw/<ключ>.json, код — <ключ>.rc, stderr — <ключ>.err, state.json после
    # команды — <ключ>.state. Ключ — подкоманда («inbound_install»); с первым аргументом
    # («inbound_install.wg») — точнее. <ключ>.once.{json,rc} — ответ только на первый вызов.
    k="$2"; a="${3:-}"
    case "$2" in inbound|user|device|exit-sub) k="$2_$3"; a="${4:-}" ;; esac
    # адрес подписки — только на вход «exit-sub add» (остальные команды вход не читают: меню продолжает читать своё)
    [ "$2 ${3:-}" = "exit-sub add" ] && [ ! -t 0 ] && { cat; echo; } >> "$CALLS/stdin.log"
    f="$FAKE_DIR/gw/$k"
    if [ -n "$a" ] && { [ -f "$f.$a.json" ] || [ -f "$f.$a.rc" ] || [ -f "$f.$a.state" ] || [ -f "$f.$a.once.rc" ]; }; then f="$f.$a"; fi
    if [ -f "$f.once.rc" ] || [ -f "$f.once.json" ]; then
        [ -f "$f.once.json" ] && cat "$f.once.json"
        rc=$(cat "$f.once.rc" 2>/dev/null || echo 0); rm -f "$f.once.json" "$f.once.rc"; exit "$rc"
    fi
    [ -f "$f.err" ] && cat "$f.err" >&2
    [ -f "$f.sleep" ] && sleep "$(cat "$f.sleep")"          # ядро долго работает после строки в stderr
    [ -f "$f.state" ] && cp "$f.state" "$st"
    if [ -f "$f.json" ]; then cat "$f.json"
    elif [ "$2" = "status" ]; then echo "шлюз: включён"
    else echo '{"ok": true}'; fi
    exit "$(cat "$f.rc" 2>/dev/null || echo 0)" ;;
  "site "*)
    # Домен (пункт 19): ключ — аргументы без «site» и --json через «_», ведущие «-» сняты («cert_issue»,
    # «use_sstp_on_dry-run», «dns_wait_300»); нет файла — ключ короче (без последнего слова). <ключ>.json — ответ,
    # .rc — код, .err — stderr, .sleep — пауза, .status — после ответа он же становится ответом «status»;
    # <ключ>.once.{json,rc} — только на первый вызов. Без файлов: status — домена нет, остальное — {"ok": true}.
    shift; parts=()
    for x in "$@"; do [ "$x" = "--json" ] && continue; while [ "${x#-}" != "$x" ]; do x="${x#-}"; done; parts+=("$x"); done
    n=${#parts[@]}; f=""
    while [ "$n" -gt 0 ]; do
        k=$(IFS=_; echo "${parts[*]:0:$n}"); g="$FAKE_DIR/site/$k"
        if [ -f "$g.json" ] || [ -f "$g.rc" ] || [ -f "$g.once.json" ]; then f="$g"; break; fi
        n=$((n - 1))
    done
    if [ -n "$f" ] && { [ -f "$f.once.json" ] || [ -f "$f.once.rc" ]; }; then
        [ -f "$f.once.json" ] && cat "$f.once.json"
        rc=$(cat "$f.once.rc" 2>/dev/null || echo 0); rm -f "$f.once.json" "$f.once.rc"; exit "$rc"
    fi
    [ -n "$f" ] && [ -f "$f.err" ] && cat "$f.err" >&2
    [ -n "$f" ] && [ -f "$f.sleep" ] && sleep "$(cat "$f.sleep")"
    if [ -n "$f" ] && [ -f "$f.json" ]; then cat "$f.json"
    elif [ "${parts[0]:-}" = status ]; then echo '{"ok": true, "domain": null, "cert": null, "dns": null, "use": {}, "use_impact": {"sstp": 0, "ocserv": 0}, "port80": {"free": true}, "email": ""}'
    else echo '{"ok": true}'; fi
    [ -n "$f" ] && [ -f "$f.status" ] && cp "$f.status" "$FAKE_DIR/site/status.json"
    [ -n "$f" ] && exit "$(cat "$f.rc" 2>/dev/null || echo 0)"
    exit 0 ;;
  "summary "*)
    # Шапка главного меню и «Что не так»: $FAKE_DIR/summary.json (с --check — summary_check.json, если есть), иначе —
    # настоящее ядро (lib/kaskad) над тем же KASKAD_ROOT: шапка и снимок ядра проверяются вместе. --check без
    # подделки не зовётся: настоящая проверка запускала бы nft и sysctl этой машины.
    if [ "${2:-}" = "--check" ]; then cat "$FAKE_DIR/summary_check.json" 2>/dev/null || cat "$FAKE_DIR/summary.json"
    elif [ -f "$FAKE_DIR/summary.json" ]; then cat "$FAKE_DIR/summary.json"
    else PYTHONPATH="$K3_TEST_LIB" exec python3 -m kaskad "$@"; fi ;;
  "backup "*)
    # Копия настроек: ответ — $FAKE_DIR/backup/<подкоманда>[.yes].{json,rc}; вход (пароль) — в $CALLS/stdin.log
    k="$2"; for x in "$@"; do [ "$x" = --yes ] && k="$k.yes"; done
    case "$2" in export|import) [ -t 0 ] || { cat; echo; } >> "$CALLS/stdin.log" ;; esac   # пароль — только у них
    f="$FAKE_DIR/backup/$k"
    if [ -f "$f.json" ]; then cat "$f.json"; else echo '{"ok": true}'; fi
    exit "$(cat "$f.rc" 2>/dev/null || echo 0)" ;;
  "setup "*)
    # Быстрая настройка: ответ — $FAKE_DIR/setup/<подкоманда>.json (link с --bind-ip — link.bind.json), код — .rc.
    f="$FAKE_DIR/setup/$2"
    case " $* " in *" --bind-ip "*) [ -f "$f.bind.json" ] && f="$f.bind" ;; esac
    if [ -f "$f.json" ]; then cat "$f.json"
    elif [ "$2" = status ]; then echo '{"ok": true, "status": "off", "expires": 0, "password_set": false}'
    else echo '{"ok": true}'; fi
    exit "$(cat "$f.rc" 2>/dev/null || echo 0)" ;;
  "host "*)
    # Здоровье сервера (пункт 22, A8): $FAKE_DIR/host/<подкоманда>.{txt,rc} — ответ и код; без файла — настоящее ядро
    # (lib/kaskad) над тем же KASKAD_ROOT: host set пишет state.json, host health и probes-hint читают его
    f="$FAKE_DIR/host/$2"
    if [ -f "$f.txt" ]; then cat "$f.txt"; exit "$(cat "$f.rc" 2>/dev/null || echo 0)"; fi
    PYTHONPATH="$K3_TEST_LIB" exec python3 -m kaskad "$@" ;;
  *) echo "kaskadctl (поддельный): нет ответа на $*" >&2; exit 2 ;;
esac
MOCK
printf '#!/usr/bin/env bash\nexit 0\n' > "$W/bin/ip"      # egress_awg_teardown: настоящих kawg* не трогать
cp "$W/bin/ip" "$W/ip-noop"
# modprobe: журнал; amneziawg «грузится», только если есть $FAKE_DIR/kmod_available (появляется /sys/module/amneziawg).
cat > "$W/bin/modprobe" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALLS/modprobe.log"
[ "${1:-}" = amneziawg ] && [ -f "$FAKE_DIR/kmod_available" ] && { mkdir -p "$KASKAD_ROOT/sys/module/amneziawg"; exit 0; }
exit 1
MOCK
chmod +x "$W/bin/modprobe"
# pgrep: «голые» процессы ног прошлых версий — интерфейсы из $FAKE_DIR/naked (pgrep -f "amneziawg-go kawgN$").
cat > "$W/bin/pgrep" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALLS/pgrep.log"
p="${*: -1}"; p="${p#amneziawg-go }"; p="${p%\$}"
grep -qx -- "$p" "$FAKE_DIR/naked" 2>/dev/null && { echo 4242; exit 0; }
exit 1
MOCK
chmod +x "$W/bin/pgrep"
# pkill: только журнал — на стенде могут работать настоящие ноги kawg*, тест их не трогает.
printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> "$CALLS/pkill.log"\nexit 1\n' > "$W/bin/pkill"
# ip с памятью (раздел 16): интерфейсы, ip rule, таблицы маршрутов — в $FAKE_DIR/ip.json; журнал вызовов —
# $CALLS/ip.log. Понимает ровно то, что зовут ноги AmneziaWG и удаление Kaskad.
cat > "$W/ip-stateful" <<'MOCK'
#!/usr/bin/env python3
import json, os, sys
st_path = os.path.join(os.environ["FAKE_DIR"], "ip.json")
with open(os.path.join(os.environ["CALLS"], "ip.log"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
try:
    st = json.load(open(st_path))
except (OSError, ValueError):
    st = {"links": [], "rules": [[0, "lookup local"], [32766, "lookup main"], [32767, "lookup default"]], "routes": {}}
a = [x for x in sys.argv[1:] if x not in ("-4", "-o", "-d")]
detail = "-d" in sys.argv[1:]              # ip -d: the kind of the link (amneziawg = the kernel module, else tun)
def kind(name):
    t = st.setdefault("types", {}).get(name, "")
    return "    amneziawg addrgenmode none" if t == "amneziawg" else "    tun type tun pi off vnet_hdr off"
def line(i, n):
    return "%d: %s: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1200" % (i, n) + ((" \\" + kind(n)) if detail else "")
def name_at(args, i):                       # «link add|del NAME» and «link add|delete dev NAME»
    return args[i + 1] if len(args) > i + 1 and args[i] == "dev" else args[i]
def save():
    json.dump(st, open(st_path, "w"))
def kv(args):
    return dict(zip(args[::2], args[1::2]))
def out(lines):
    sys.stdout.write("".join(l + "\n" for l in lines))
def route_key(text):          # what «ip route replace» replaces: the same destination and metric
    tok = text.split()
    dst = tok[1] if tok[0] in ("unreachable", "blackhole", "prohibit", "local", "throw") else tok[0]
    return dst, (tok[tok.index("metric") + 1] if "metric" in tok else "0")
if a[:2] in (["link", "show"], ["link", "list"]):
    if len(a) >= 4 and a[2] == "dev":
        if a[3] not in st["links"]:
            sys.stderr.write('Device "%s" does not exist.\n' % a[3]); sys.exit(1)
        out([line(st["links"].index(a[3]) + 10, a[3])])
    else:
        out([line(i + 10, n) for i, n in enumerate(st["links"])])
elif a[:2] == ["link", "add"]:
    n = name_at(a, 2)
    if n in st["links"]:
        sys.stderr.write("RTNETLINK answers: File exists\n"); sys.exit(2)
    st["links"].append(n)
    st.setdefault("types", {})[n] = a[a.index("type") + 1] if "type" in a else ""
    save()
elif a[:2] in (["link", "del"], ["link", "delete"]):
    n = name_at(a, 2)
    if n not in st["links"]:
        sys.stderr.write('Cannot find device "%s"\n' % n); sys.exit(1)
    st["links"].remove(n)
    st.setdefault("types", {}).pop(n, None)
    for t in st["routes"]:
        st["routes"][t] = [r for r in st["routes"][t] if " dev %s" % n not in " " + r]
    save()
elif a[:2] == ["rule", "show"] or a[:1] == ["rule"] and len(a) == 1:
    out(["%d:\tfrom all %s" % (p, t) for p, t in sorted(st["rules"])])
elif a[:2] in (["rule", "add"], ["rule", "del"]):
    m = kv(a[2:])
    text = "fwmark 0x%x lookup %s" % (int(m["fwmark"], 0), m.get("table") or m.get("lookup"))
    if a[1] == "add":
        st["rules"].append([min(p for p, _ in st["rules"] if p > 0) - 1, text])
    else:
        hit = [r for r in sorted(st["rules"]) if r[1] == text]
        if not hit:
            sys.stderr.write("RTNETLINK answers: No such file or directory\n"); sys.exit(2)
        st["rules"].remove(hit[0])
    save()
elif a[:2] == ["route", "replace"]:
    spec = a[2:]
    t = spec[spec.index("table") + 1]
    body = [x for i, x in enumerate(spec) if x != "table" and not (i > 0 and spec[i - 1] == "table")]
    if "dev" in body and body[body.index("dev") + 1] not in st["links"]:
        sys.stderr.write('Cannot find device "%s"\n' % body[body.index("dev") + 1]); sys.exit(1)
    text = " ".join(body)
    rs = st["routes"].setdefault(t, [])
    rs[:] = [r for r in rs if route_key(r) != route_key(text)] + [text]
    save()
elif a[:3] == ["route", "show", "table"]:
    out(st["routes"].get(a[3], []))
elif a[:3] == ["route", "flush", "table"]:
    st["routes"][a[3]] = []; save()
elif a[:2] in (["addr", "add"], ["link", "set"]):
    pass
else:
    sys.stderr.write("fake ip: %s\n" % " ".join(sys.argv[1:])); sys.exit(1)
MOCK
chmod +x "$W/ip-stateful"
# flock (util-linux): «flock -w СЕК FD» — блокировка на унаследованном дескрипторе, как у настоящего
# (держится и после выхода flock: открытый файл — общий с оболочкой). На macOS flock нет — поэтому
# своя, на python; журнал вызовов — для проверки, что запись старого движка шла под блокировкой.
cat > "$W/bin/flock" <<'MOCK'
#!/usr/bin/env python3
import fcntl, os, sys, time
args = sys.argv[1:]
with open(os.path.join(os.environ["CALLS"], "flock.log"), "a") as f:
    f.write(" ".join(args) + "\n")
wait = 0.0
if args[:1] == ["-w"]:
    wait, args = float(args[1]), args[2:]
fd, deadline = int(args[0]), time.time() + wait
while True:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sys.exit(0)
    except OSError:
        if time.time() >= deadline:
            sys.exit(1)
        time.sleep(0.05)
MOCK
# qrencode для выдачи шлюза: журнал аргументов и того, что пришло на stdin (секрет — только через stdin).
# -t ASCII — строка шириной 2×QR_FAKE_W знаков (по умолчанию 30 модулей; QR_FAKE_W=len — столько модулей,
# сколько знаков пришло на stdin); -t PNG -o файл — «картинка».
cat > "$W/qrencode.fake" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALLS/qrencode.log"
in=$(cat); printf '%s\n' "$in" >> "$CALLS/qr_in.log"
w="${QR_FAKE_W:-30}"; [ "$w" = len ] && w=${#in}
case " $* " in
  *" -t ASCII "*) printf '%*s\n' "$(( w * 2 ))" "" ;;
  *" -t PNG "*) o=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && o="$2"; shift; done; printf 'PNG' > "$o" ;;
  *) echo "[QR]" ;;
esac
MOCK
chmod +x "$W/bin/"* "$W/kaskadctl.fake" "$W/qrencode.fake"
unset K3_ISSUE_DIR SSH_CONNECTION
export K3_QRENCODE="$W/qrencode.fake"
# Окно терминала для выдачи шлюза (QR и «Enter — дальше»): большое — без пауз; раздел 22 проверяет 80×24.
export K3_TERM_COLS=100 K3_TERM_ROWS=100
export K3_TEST_LIB="$ROOT_DIR/lib"          # поддельный kaskadctl отдаёт «summary» настоящим ядром
export K3_PAUSE_SEC=0                     # «нет такого пункта» и прочие короткие паузы меню — без ожидания
# Тест можно запускать и на стенде от root: в PATH — только подделки и /usr/bin:/bin, без /sbin и
# /usr/sbin, где лежат настоящие iptables, nft, ufw, netfilter-persistent, ip. Нужные настоящие
# программы (bash 4+, python3, jq) — ссылками.
for b in bash python3 jq; do
    p=$(command -v "$b") || { echo "нет $b" >&2; exit 2; }
    ln -sf "$p" "$W/bin/$b"
done
export PATH="$W/bin:/usr/bin:/bin"

# Свежий корень сервера для раздела: KASKAD_ROOT, K3_OPT, KASKAD_DIR, пустые журналы и подделки.
use_root() {
    export KASKAD_ROOT="$W/root-$1"
    export K3_OPT="$KASKAD_ROOT/opt/kaskad" KASKAD_DIR="$KASKAD_ROOT/etc/kaskad"
    rm -rf "$KASKAD_ROOT" "$IPT_DIR" "$CALLS" "$FAKE_DIR"
    mkdir -p "$KASKAD_DIR" "$KASKAD_ROOT/var/log" "$KASKAD_ROOT/usr/local/bin" \
             "$KASKAD_ROOT/etc/systemd/system" "$IPT_DIR" "$CALLS" "$FAKE_DIR/gh"
    : > "$CALLS/kaskadctl.log"; : > "$CALLS/systemctl.log"; : > "$CALLS/curl.log"; : > "$CALLS/tg.log"; : > "$CALLS/apt.log"
    os_release debian 12
}
# Система для выбора пакетов (k3_os_supported): os_release <ID> <VERSION_ID>.
export K3_OS_RELEASE="$W/os-release"
os_release() { printf 'ID=%s\nVERSION_ID="%s"\n' "$1" "$2" > "$K3_OS_RELEASE"; }
fake_ctl() { mkdir -p "$K3_OPT/bin"; cp "$W/kaskadctl.fake" "$K3_OPT/bin/kaskadctl"; chmod +x "$K3_OPT/bin/kaskadctl"; }
engine_nft() { printf '{\n  "engine": "nft",\n  "schema": 1\n}\n' > "$KASKAD_DIR/state.json"; }
# Движок nft с пробросами tcp 443 → 2.2.2.2:443 и udp 51820 → 5.5.5.5:51820 (в state.json — не по порядку).
engine_nft_fwds() {
    printf '{\n  "engine": "nft",\n  "forwards": [\n    {"proto": "udp", "port": 51820, "to": "5.5.5.5", "to_port": 51820},\n    {"proto": "tcp", "port": 443, "to": "2.2.2.2", "to_port": 443}\n  ],\n  "schema": 1\n}\n' > "$KASKAD_DIR/state.json"
}
ctl_log() { cat "$CALLS/kaskadctl.log" 2>/dev/null; }
# Ответ поддельного kaskadctl gw: gwf <ключ>.{json,rc,err,state} '<текст>' (подробно — у раздела «шлюз (WP7)»).
gwf() { mkdir -p "$FAKE_DIR/gw"; printf '%s\n' "$2" > "$FAKE_DIR/gw/$1"; }
# lib <функция теста> [аргументы] — install.sh (--lib) в подоболочке и вызов функции теста.
lib() {
    ( set +u
      unset GH_TOKEN GITHUB_TOKEN GITHUB_PAT
      [ -n "${K3_KEEP_NO_COLOR:-}" ] || unset NO_COLOR    # цвета проверяются; без цвета — раздел 44
      export GOINST_TOKEN_FILE="$W/no-goinstaller-token"
      source "$S" --lib >/dev/null 2>&1 || exit 97
      KASKAD_LOG="$KASKAD_ROOT/var/log/kaskad.log"; IFACE=eth0; MY_IP=203.0.113.10
      BOT_PID_FILE="$KASKAD_ROOT/run/kaskad_bot.pid"; MONITOR_PID_FILE="$KASKAD_ROOT/run/kaskad_monitor.pid"
      "$@" )
}
# Копия install.sh с другой версией (подделка ветки на GitHub).
mk_version() { sed "s/^KASKAD_VERSION=\"$CUR\"/KASKAD_VERSION=\"$1\"/" "$S" > "$2"; }
ver_of() { sed -n 's/^KASKAD_VERSION="\([^"]*\)".*/\1/p' "$1" 2>/dev/null | head -1; }

SHA=$(sed -n 's/^K3_LIB_SHA="\([0-9a-f]*\)"$/\1/p' "$S")
SHA12="${SHA:0:12}"

echo ""
echo "  Kaskad 3 install.sh test"
echo "── 1. файл install.sh: шапка, версия, хвост, блок кода ──"
head -1 "$S" | grep -qx '#!/bin/bash' && ok "первая строка #!/bin/bash (self_update 2.x)" || bad "первая строка не #!/bin/bash"
tail -3 "$S" | grep -q '^esac' && ok "esac в последних трёх строках (self_update 2.x)" || bad "нет esac в хвосте"
head -20 "$S" | grep -qx "KASKAD_VERSION=\"$CUR\"" && ok "KASKAD_VERSION=\"$CUR\" отдельной строкой в начале (goInstaller)" || bad "строка версии"
[ "$(grep -E '^[[:space:]]*[A-Z][A-Z0-9_]*VERSION="?[0-9]' "$S" | head -1)" = "KASKAD_VERSION=\"$CUR\"" ] \
  && ok "первая строка вида *VERSION= — KASKAD_VERSION $CUR (её читает goInstaller)" || bad "goInstaller прочтёт не ту версию"
bash -n "$S" && ok "bash -n ($(bash -c 'echo $BASH_VERSION'))" || bad "bash -n"
if [ -x /bin/bash ] && [ "$(/bin/bash -c 'echo ${BASH_VERSINFO[0]}')" -lt 4 ]; then
    /bin/bash -n "$S" && ok "bash -n под /bin/bash 3.2 (macOS)" || bad "bash -n под bash 3.2"
fi
grep -q '^    --lib) : ;;' "$S" && ok "режим --lib на месте" || bad "нет режима --lib"
python3 "$ROOT_DIR/tools/build_payload.py" --check >/dev/null && ok "блок K3_PAYLOAD актуален (build_payload.py --check)" || bad "блок K3_PAYLOAD устарел"
b=$(grep -nxF '# >>> K3_PAYLOAD (generated by tools/build_payload.py, do not edit)' "$S" | cut -d: -f1)
e=$(grep -nxF '# <<< K3_PAYLOAD' "$S" | cut -d: -f1); total=$(wc -l < "$S")
[ -n "$b" ] && [ -n "$e" ] && [ "$e" -gt "$b" ] && [ $((total - e)) -gt 200 ] && ok "блок кода в середине файла (стр. ${b}–${e} из ${total})" || bad "блок кода не там" "b=$b e=$e total=$total"
k3sec=$(sed -n '/^#  KASKAD 3 — ядро на Python/,/^# >>> K3_PAYLOAD/p' "$S")
printf '%s\n' "$k3sec" | grep -v '^[[:space:]]*#' | grep -qw eval && bad "eval в новом коде Kaskad 3" || ok "в новом коде Kaskad 3 нет eval"
# kaskadctl как команда (в начале команды, после ; & | ( { или $( ) — только через k3ctl.
# И путь к обёртке с аргументами ("$K3_OPT/bin/kaskadctl" fwd …) — только в самой k3ctl.
direct=$(grep -v '^[[:space:]]*#' "$S" | grep -nE '(^|[;&|({]|\$\()[[:space:]]*kaskadctl[[:space:]]|kaskadctl"?[[:space:]]+(version|fwd|net|migrate)([[:space:]]|$)')
[ -z "$direct" ] && [ "$(grep -c 'bin/kaskadctl" "\$@"' "$S")" = "1" ] \
  && ok "kaskadctl зовётся только через k3ctl (\$K3_OPT/bin/kaskadctl)" || bad "kaskadctl в обход k3ctl" "$direct"
for u in k3_bot_unit_text start_monitoring_silent egress_write_service; do
    sed -n "/^${u}() {/,/^}/p" "$S" | grep -q 'cmd=$(k3_unit_cmd)' && ok "$u пишет юнит через k3_unit_cmd (копия в /opt)" || bad "$u: путь программы не через k3_unit_cmd"
done
# 3.2: xray бота ноги AmneziaWG не поднимает и не снимает (у ног свои юниты kaskad-leg@) — перед стартом только
# правила ног (--egress-awg-routes).
sed -n '/^egress_write_service() {/,/^}/p' "$S" | grep -qE -- '--egress-awg-(up|down)' \
  && bad "egress_write_service: служба xray бота снова управляет ногами" \
  || { sed -n '/^egress_write_service() {/,/^}/p' "$S" | grep -qx 'ExecStartPre=-${cmd} --egress-awg-routes' \
       && ok "egress_write_service: служба xray бота ноги не поднимает и не снимает — перед стартом только их правила"; } \
  || bad "egress_write_service: нет ExecStartPre --egress-awg-routes"
! grep -nE '^Exec[A-Za-z]*=-?/usr/local/bin/gokaskad' "$S" >/dev/null && ok "ни один юнит не запускает /usr/local/bin/gokaskad напрямую" || bad "юнит с /usr/local/bin/gokaskad"

echo "── 2. k3_ensure_lib: код в /opt, повторный вызов, порча .ok и архива ──"
use_root lib
t_ensure() { k3_ensure_lib; echo "RC=$? ERR=[$K3_LIB_ERR]"; }
o=$(lib t_ensure)
printf '%s' "$o" | grep -q 'RC=0 ERR=\[\]' && ok "k3_ensure_lib → 0" || bad "k3_ensure_lib" "$o"
[ "$(cat "$K3_OPT/lib/$SHA12/.ok" 2>/dev/null)" = "$SHA" ] && ok "lib/$SHA12/.ok = sha256 архива" || bad ".ok"
[ "$(readlink "$K3_OPT/lib/current")" = "$SHA12" ] && ok "current → $SHA12" || bad "current → $(readlink "$K3_OPT/lib/current")"
[ -f "$K3_OPT/lib/$SHA12/kaskad/cli.py" ] && [ -f "$K3_OPT/lib/$SHA12/kaskad/migrate/__init__.py" ] && ok "файлы lib/kaskad распакованы" || bad "нет файлов кода"
v=$("$K3_OPT/bin/kaskadctl" version 2>&1); [ "$v" = "Kaskad $CUR" ] && ok "обёртка kaskadctl запускает код: «${v}»" || bad "kaskadctl version" "$v"
# T-N5 3.2.19, №10: подсказки «kaskadctl gw …» — команда в PATH: ссылка /usr/local/bin/kaskadctl на обёртку
[ "$(readlink "$KASKAD_ROOT/usr/local/bin/kaskadctl")" = "$K3_OPT/bin/kaskadctl" ] \
  && [ "$("$KASKAD_ROOT/usr/local/bin/kaskadctl" version 2>&1)" = "Kaskad $CUR" ] \
  && ok "k3_ensure_lib ставит ссылку /usr/local/bin/kaskadctl → обёртка (подсказки меню, панели и бота работают)" || bad "нет ссылки kaskadctl в PATH"
rm -f "$KASKAD_ROOT/usr/local/bin/kaskadctl"; printf '#!/bin/sh\necho mine\n' > "$KASKAD_ROOT/usr/local/bin/kaskadctl"
lib t_ensure >/dev/null; [ "$(cat "$KASKAD_ROOT/usr/local/bin/kaskadctl")" = "$(printf '#!/bin/sh\necho mine')" ] \
  && ok "чужой файл /usr/local/bin/kaskadctl не трогается" || bad "чужой kaskadctl перезаписан"
rm -f "$KASKAD_ROOT/usr/local/bin/kaskadctl"; lib t_ensure >/dev/null
h=$("$K3_OPT/bin/kaskadctl" --help 2>&1)
printf '%s' "$h" | grep -q 'fwd' && printf '%s' "$h" | grep -q 'net' && printf '%s' "$h" | grep -q 'migrate' \
  && ok "во встроенном коде есть группы fwd, net, migrate" || bad "встроенный код без групп команд" "$h"
"$K3_OPT/bin/kaskadctl" fwd list >/dev/null 2>&1; rc=$?
[ "$rc" = "5" ] && ok "настоящий kaskadctl: fwd list на старом движке — код 5 (как в контракте)" || bad "fwd list на legacy: код $rc"
grep -q "exec python3 -I -c 'import runpy, sys; sys.path.insert(0, \"$K3_OPT/lib/current\")" "$K3_OPT/bin/kaskadctl" && ok "обёртка: python3 -I, путь к коду — …/lib/current" || bad "текст обёртки" "$(cat "$K3_OPT/bin/kaskadctl")"
# root, запустивший меню из каталога с чужим kaskad/ или json.py, не должен их подхватить.
mkdir -p "$W/evil/kaskad"; echo 'print("EVIL")' > "$W/evil/kaskad/__main__.py"; : > "$W/evil/kaskad/__init__.py"; echo 'print("EVIL")' > "$W/evil/json.py"
v=$(cd "$W/evil" && "$K3_OPT/bin/kaskadctl" version 2>&1); [ "$v" = "Kaskad $CUR" ] && ok "kaskadctl из каталога с чужими kaskad/ и json.py — свой код" || bad "kaskadctl подхватил код из текущего каталога" "$v"
cmp -s "$S" "$K3_OPT/bin/gokaskad" && [ -x "$K3_OPT/bin/gokaskad" ] && ok "копия программы в \$K3_OPT/bin/gokaskad (0755)" || bad "копии программы нет"
# MCP-сервер для ИИ-агентов (SPEC-mcp): обёртка kaskad-mcp тем же способом, что kaskadctl, и ссылка в PATH
grep -q "exec python3 -I -c 'import runpy, sys; sys.path.insert(0, \"$K3_OPT/lib/current\"); runpy.run_module(\"kaskad.mcp\"" "$K3_OPT/bin/kaskad-mcp" \
  && [ -x "$K3_OPT/bin/kaskad-mcp" ] && [ "$(readlink "$KASKAD_ROOT/usr/local/bin/kaskad-mcp")" = "$K3_OPT/bin/kaskad-mcp" ] \
  && ok "k3_ensure_lib: обёртка kaskad-mcp (python3 -I, …/lib/current) и ссылка /usr/local/bin/kaskad-mcp" || bad "нет обёртки или ссылки kaskad-mcp" "$(cat "$K3_OPT/bin/kaskad-mcp" 2>&1)"
o=$("$K3_OPT/bin/kaskad-mcp" --check 2>&1)
printf '%s\n' "$o" | tail -1 | grep -qx OK && ok "kaskad-mcp --check из встроенного кода: каталог сходится с kaskadctl, гайд на месте" || bad "kaskad-mcp --check" "$o"
o=$(printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"version","arguments":{}}}' \
    | KASKAD_MCP_AUDIT="$W/mcp-audit.log" "$K3_OPT/bin/kaskad-mcp" 2>/dev/null)
printf '%s' "$o" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); sys.exit(0 if d["result"]["structuredContent"]["version"] == sys.argv[1] and not d["result"]["isError"] else 1)' "$CUR" \
  && grep -q '"tool": "version"' "$W/mcp-audit.log" && ok "kaskad-mcp по stdio: version → $CUR через обёртку kaskadctl, вызов в аудите" || bad "kaskad-mcp tools/call version" "$o"
# меню 18 → «Ключи ИИ-агентов» (настоящий kaskadctl из встроенного кода): добавить, список, команда подключения, удалить
AGK="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHYQVexUVwrWE3ClSz+u3fw9zsSdIB/hQ6bgD7OtwSt8 me@laptop"
AK="$KASKAD_ROOT/root/.ssh/authorized_keys"; mkdir -p "${AK%/*}"; printf 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOwnerKEYownerKEYownerKEYownerKEYownerKEYownerK owner\n' > "$AK"
t_agkeys() { k3_mcp_keys_menu; }
o=$(printf '1\n%s\nclaude-laptop\n\n3\n\n0\n' "$AGK" | lib t_agkeys 2>&1 | strip)
line=$(grep 'kaskad-mcp-agent' "$AK")
[ "$(head -1 "$AK")" = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOwnerKEYownerKEYownerKEYownerKEYownerKEYownerK owner" ] \
  && printf '%s' "$line" | grep -q '^restrict,command="/usr/local/bin/kaskad-mcp",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-user-rc ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHYQVexUVwrWE3ClSz+u3fw9zsSdIB/hQ6bgD7OtwSt8 kaskad-mcp-agent:claude-laptop$' \
  && printf '%s' "$o" | grep -q 'claude mcp add kaskad -- ssh -i ~/.ssh/kaskad_agent -o IdentitiesOnly=yes -o BatchMode=yes root@203.0.113.10 kaskad-mcp' \
  && printf '%s' "$o" | grep -q '1. SHA256:' \
  && ok "18 → ключи ИИ-агентов: ключ дописан с принудительной командой и запретами, строка владельца цела, команда для Claude Code" \
  || bad "меню ключей агентов: добавление" "$o | $(cat "$AK")"
o=$(printf '1\nssh-ed25519 AAAA мусор\n\n1\ncommand="/bin/sh" %s\n\n1\n-----BEGIN OPENSSH PRIVATE KEY-----\n\n0\n' "$AGK" | lib t_agkeys 2>&1 | strip)
[ "$(grep -c kaskad-mcp-agent "$AK")" = 1 ] && printf '%s' "$o" | grep -q 'Ключ повреждён\|Это не публичный ключ' \
  && printf '%s' "$o" | grep -q 'свои опции' && printf '%s' "$o" | grep -q 'Это ЗАКРЫТЫЙ ключ' && ! printf '%s' "$o" | grep -q 'Подпись ключа' \
  && ok "мусор, ключ со своими опциями (command=), закрытый ключ — отказ сразу, до вопроса о подписи; файл не тронут" \
  || bad "меню ключей агентов: отказы" "$o"
o=$(printf '2\n1\n1\n\n0\n' | lib t_agkeys 2>&1 | strip)
! grep -q kaskad-mcp-agent "$AK" && grep -q ' owner$' "$AK" && ok "удаление ключа агента — цифрами; строка владельца осталась" \
  || bad "меню ключей агентов: удаление" "$o | $(cat "$AK")"
touch "$W/marker"; sleep 1
o=$(lib t_ensure)
[ -z "$(find "$K3_OPT" -newer "$W/marker" 2>/dev/null)" ] && printf '%s' "$o" | grep -q 'RC=0' && ok "повторный вызов ничего не пишет" || bad "повторный вызов что-то изменил" "$(find "$K3_OPT" -newer "$W/marker")"
echo "испорчено" > "$K3_OPT/lib/$SHA12/.ok"; lib t_ensure >/dev/null
[ "$(cat "$K3_OPT/lib/$SHA12/.ok")" = "$SHA" ] && [ -f "$K3_OPT/lib/$SHA12/kaskad/cli.py" ] && ok "испорченный .ok — код распакован заново" || bad "испорченный .ok не пойман"
rm -f "$K3_OPT/lib/$SHA12/.ok" "$K3_OPT/lib/$SHA12/kaskad/cli.py"; lib t_ensure >/dev/null
[ "$(cat "$K3_OPT/lib/$SHA12/.ok" 2>/dev/null)" = "$SHA" ] && [ -f "$K3_OPT/lib/$SHA12/kaskad/cli.py" ] && ok "нет .ok (оборванная распаковка) — распакован заново" || bad "нет .ok — не восстановлено"
[ -z "$(ls -A "$K3_OPT/lib" | grep -E '^\.(new|payload|current)')" ] && ok "временных файлов в lib/ не осталось" || bad "остались временные файлы: $(ls -A "$K3_OPT/lib")"
# Порча встроенного архива: одна буква base64 в середине блока.
python3 - "$S" "$W/bad.sh" <<'PY'
import sys
lines = open(sys.argv[1], encoding="utf-8").read().split("\n")
i = lines.index("    cat <<'K3PAYLOAD'") + 20
lines[i] = ("B" if lines[i][0] != "B" else "C") + lines[i][1:]
open(sys.argv[2], "w", encoding="utf-8").write("\n".join(lines))
PY
use_root badlib
o=$( ( set +u; source "$W/bad.sh" --lib >/dev/null 2>&1; k3_ensure_lib; echo "RC=$? ERR=[$K3_LIB_ERR]" ) )
printf '%s' "$o" | grep -q 'RC=1' && printf '%s' "$o" | grep -q 'sha256 не совпал' && ok "порча архива поймана: «$(printf '%s' "$o" | sed -n 's/.*ERR=\[\(.*\)\]/\1/p')»" || bad "порча архива не поймана" "$o"
[ ! -e "$K3_OPT/lib/$SHA12" ] && [ ! -e "$K3_OPT/lib/current" ] && ok "битый код не распакован, current не создан" || bad "битый код попал в lib"
# Старые каталоги кода: остаются текущий и предыдущий.
use_root clean
mkdir -p "$K3_OPT/lib/aaaaaaaaaaaa" "$K3_OPT/lib/bbbbbbbbbbbb" "$K3_OPT/lib/not-a-sha"
ln -s aaaaaaaaaaaa "$K3_OPT/lib/current"
lib t_ensure >/dev/null
[ "$(readlink "$K3_OPT/lib/current")" = "$SHA12" ] && [ "$(cat "$K3_OPT/lib/.previous")" = "aaaaaaaaaaaa" ] \
  && [ -d "$K3_OPT/lib/aaaaaaaaaaaa" ] && [ ! -e "$K3_OPT/lib/bbbbbbbbbbbb" ] && [ -d "$K3_OPT/lib/not-a-sha" ] \
  && ok "смена версии: current → новый, предыдущий оставлен, старше — удалён, чужое не тронуто" || bad "чистка lib" "$(ls -A "$K3_OPT/lib")"
# Прежняя программа 2.x (gokaskad.bak после self_update 2.x) — в /opt/kaskad/prev.
use_root prev
mk_version 2.13 "$KASKAD_ROOT/usr/local/bin/gokaskad.bak"
lib t_ensure >/dev/null
cmp -s "$KASKAD_ROOT/usr/local/bin/gokaskad.bak" "$K3_OPT/prev/gokaskad-2.13" && ok "gokaskad.bak 2.13 сохранён в \$K3_OPT/prev/gokaskad-2.13" || bad "2.x из .bak не сохранён"
printf '#!/bin/bash\n# бесплатный Kaskad: без строки версии\nconfigure_custom_rule() { :; }\nshow_promo() { :; }\n' > "$KASKAD_ROOT/usr/local/bin/gokaskad"
t_keep() { k3_keep_prev_program; }
lib t_keep; [ -f "$K3_OPT/prev/gokaskad-free" ] && ok "бесплатный скрипт (без KASKAD_VERSION) — gokaskad-free" || bad "бесплатный не сохранён"
cmp -s "$KASKAD_ROOT/usr/local/bin/gokaskad" "$KASKAD_ROOT/usr/local/bin/gokaskad.bak" \
  && ok "перед заменой команды прежняя программа — в gokaskad.bak (по нему migrate scan узнаёт, откуда переезд)" || bad "gokaskad.bak не записан"
# 2.12 → (self_update 2.x: .bak = 2.12) → поверх бесплатный скрипт → 3.x: 2.12 не должна потеряться.
rm -rf "$K3_OPT/prev"; mk_version 2.12 "$KASKAD_ROOT/usr/local/bin/gokaskad.bak"; lib t_keep
[ "$(ver_of "$K3_OPT/prev/gokaskad-2.12")" = "2.12" ] && [ -f "$K3_OPT/prev/gokaskad-free" ] \
  && ok "прежний gokaskad.bak (2.12) сначала уходит в prev, потом .bak = бесплатный скрипт" || bad "2.12 из .bak потерялась" "$(ls "$K3_OPT/prev" 2>&1)"
cp "$S" "$KASKAD_ROOT/usr/local/bin/gokaskad"; rm -rf "$K3_OPT/prev" "$KASKAD_ROOT/usr/local/bin/gokaskad.bak"; lib t_keep
[ ! -e "$K3_OPT/prev" ] && [ ! -e "$KASKAD_ROOT/usr/local/bin/gokaskad.bak" ] && ok "3.x не копируется ни в prev, ни в gokaskad.bak" || bad "3.x попал в prev/.bak"

echo "── 3. движок legacy: правила как в 2.13, kaskadctl не зовётся ──"
use_root legacy; fake_ctl
t_apply() { apply_iptables_rules tcp 443 443 2.2.2.2 VLESS; echo "RC=$?"; }
o=$(lib t_apply 2>&1)
grep -qx -- '-p tcp --dport 443 -m comment --comment kaskad:443:tcp -j DNAT --to-destination 2.2.2.2:443' "$IPT_DIR/nat.PREROUTING" \
  && ok "DNAT с меткой kaskad:443:tcp (2.13)" || bad "legacy DNAT" "$(cat "$IPT_DIR/nat.PREROUTING" 2>/dev/null)"
[ "$(grep -c 'kaskad:443:tcp' "$IPT_DIR/filter.FORWARD")" = "2" ] && grep -q 'kaskad:masq' "$IPT_DIR/nat.POSTROUTING" && ok "две FORWARD и kaskad:masq (2.13)" || bad "legacy FORWARD/masq"
grep -q 'save' "$CALLS/netfilter-persistent.log" 2>/dev/null && ok "netfilter-persistent save (2.13)" || bad "save_iptables в legacy не сохранил"
t_list() { get_rules_list; }
[ "$(lib t_list)" = "tcp|443|2.2.2.2:443" ] && ok "get_rules_list из iptables: tcp|443|2.2.2.2:443" || bad "legacy get_rules_list: $(lib t_list)"
[ -z "$(ctl_log)" ] && ok "kaskadctl не вызывался" || bad "legacy позвал kaskadctl" "$(ctl_log)"

echo "── 4. движок nft: те же функции зовут kaskadctl ──"
use_root nft; fake_ctl; engine_nft_fwds
printf 'tcp|443|2.2.2.2:443\nudp|51820|5.5.5.5:51820\n' > "$FAKE_DIR/fwd_list"
printf 'tcp|443|2.2.2.2:443|12|1536\nudp|51820|5.5.5.5:51820|3|3145728\n' > "$FAKE_DIR/fwd_list_traffic"
o=$(lib t_apply 2>&1)
[ "$(ctl_log)" = "fwd add --proto tcp --port 443 --to 2.2.2.2 --to-port 443 --name VLESS --json" ] && ok "apply_iptables_rules → fwd add --proto tcp --port 443 --to 2.2.2.2 --to-port 443 --name VLESS --json" || bad "fwd add: аргументы" "$(ctl_log)"
printf '%s' "$o" | grep -q 'RC=0' && printf '%s' "$o" | grep -q 'tcp: 203.0.113.10:443 -> 2.2.2.2:443' && ok "успех — как в 2.13: «tcp: IP:443 -> 2.2.2.2:443»" || bad "вывод fwd add" "$o"
[ -z "$(ls -A "$IPT_DIR")" ] && [ ! -s "$CALLS/netfilter-persistent.log" ] && ok "iptables и netfilter-persistent не тронуты" || bad "nft-путь тронул iptables" "$(ls "$IPT_DIR")"
# Пункты 1–4 для новичка: чей адрес спрашиваем, «0»/Enter — назад, Enter на «Применить?» — отмена словами, итог — что
# указать на телефоне; сервер молчит на ping — простой вопрос, совет для специалиста — после него.
t_rule() { smart_ping() { return 1; }; geoip_lookup() { echo '{}'; }; sleep() { :; }; configure_rule udp AmneziaWG; echo "RC=$?"; }
: > "$CALLS/kaskadctl.log"; o=$(printf '0\n' | lib t_rule 2>&1 | strip)
printf '%s' "$o" | grep -q 'IP адрес зарубежного сервера (куда пересылать), 0 — назад:' && printf '%s' "$o" | grep -q 'RC=0' && [ -z "$(ctl_log)" ] \
  && ok "п. 1: «IP адрес зарубежного сервера (куда пересылать), 0 — назад»; «0» — назад в меню" || bad "п. 1: 0 — назад" "$o"
o=$(printf '\n' | lib t_rule 2>&1 | strip); o2=$(printf 'abc\n198.51.100.7\n\n' | lib t_rule 2>&1 | strip)
printf '%s' "$o" | grep -q 'RC=0' && ! printf '%s' "$o" | grep -q '━━━ Проверка сервера' \
  && printf '%s' "$o2" | grep -q 'Это не IP-адрес: нужны четыре числа через точку' && printf '%s' "$o2" | grep -q 'RC=0' && [ -z "$(ctl_log)" ] \
  && ok "п. 1: Enter — назад; слово вместо адреса — что нужно ввести; Enter на порте — тоже назад" || bad "п. 1: Enter — назад" "$o | $o2"
o=$(printf '198.51.100.7\n51820\n1\n\n\n\n\n' | lib t_rule 2>&1 | strip)
q=$(printf '%s\n' "$o" | grep -n 'Так бывает: многие серверы не отвечают на ping' | cut -d: -f1)
s=$(printf '%s\n' "$o" | grep -n 'Совет: чтобы сервер отвечал на ping' | cut -d: -f1); n=$(printf '%s\n' "$o" | grep -n 'Введите имя сервера' | cut -d: -f1)
[ -n "$q" ] && [ -n "$s" ] && [ -n "$n" ] && [ "$q" -lt "$s" ] && [ "$s" -lt "$n" ] && ! printf '%s' "$o" | grep -q 'iptables -A INPUT' \
  && printf '%s' "$o" | grep -q 'Отменено, правило не создано' && [ -z "$(ctl_log)" ] \
  && ok "п. 1: сервер молчит на ping — простой вопрос до имени, совет одной строкой после; Enter на «Применить?» — «Отменено, правило не создано»" \
  || bad "п. 1: ping и отмена" "$o"
o=$(printf '198.51.100.7\n51820\n2\n\n' | lib t_rule 2>&1 | strip)
printf '%s' "$o" | grep -q 'Отменено, правило не создано' && ! printf '%s' "$o" | grep -q 'Введите имя сервера' && [ -z "$(ctl_log)" ] \
  && ok "п. 1: «2» на «Продолжить?» — тоже «Отменено, правило не создано»" || bad "п. 1: отказ после ping" "$o"
o=$(printf '198.51.100.7\n51820\n1\n\n\n1\n\n' | lib t_rule 2>&1 | strip)
ctl_log | grep -qx 'fwd add --proto udp --port 51820 --to 198.51.100.7 --to-port 51820 --name AmneziaWG --json' \
  && printf '%s' "$o" | grep -q 'в приложении на телефоне укажите адрес 203.0.113.10 и порт 51820' \
  && ok "п. 1: «1» — правило создано; итог: «в приложении на телефоне укажите адрес 203.0.113.10 и порт 51820»" || bad "п. 1: итог" "$o | $(ctl_log)"
t_custom() { smart_ping() { echo "ICMP|12.5"; }; geoip_lookup() { echo '{}'; }; sleep() { :; }; configure_custom_rule; echo "RC=$?"; }
: > "$CALLS/kaskadctl.log"; o=$(printf 'TCP\n198.51.100.7\n0\n' | lib t_custom 2>&1 | strip)
printf '%s' "$o" | grep -q 'RC=0' && [ -z "$(ctl_log)" ] && o=$(printf '0\n' | lib t_custom 2>&1 | strip) && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "п. 4: «0» на протоколе и на порте — назад" || bad "п. 4: назад" "$o"
o=$(printf 'tcp\n198.51.100.7\n2222\n22\n\n\n1\n\n' | lib t_custom 2>&1 | strip)
ctl_log | grep -qx 'fwd add --proto tcp --port 2222 --to 198.51.100.7 --to-port 22 --name Custom Rule --json' \
  && printf '%s' "$o" | grep -q 'укажите адрес 203.0.113.10 и порт 2222' && ok "п. 4: итог — входящий порт этого сервера (2222)" || bad "п. 4: итог" "$o | $(ctl_log)"
: > "$CALLS/kaskadctl.log"
real_list=$(PYTHONPATH="$ROOT_DIR/lib" python3 -m kaskad fwd list 2>&1)
[ "$(lib t_list)" = "$(cat "$FAKE_DIR/fwd_list")" ] && [ "$(lib t_list)" = "$real_list" ] && ! ctl_log | grep -q 'fwd list' \
  && ok "get_rules_list на nft — из state.json через jq: те же строки и порядок, что у kaskadctl fwd list, без python" \
  || bad "nft get_rules_list" "$(lib t_list) | kaskadctl: $real_list | $(ctl_log)"
t_ports() { get_target_ips | tr '\n' ' '; get_port_for_ip 5.5.5.5; }
[ "$(lib t_ports)" = "2.2.2.2 5.5.5.5 51820" ] && ok "get_target_ips / get_port_for_ip поверх fwd list" || bad "производные get_rules_list: $(lib t_ports)"
: > "$CALLS/kaskadctl.log"
t_misc() { remove_rules_for_port udp 51820; flush_all_kaskad_rules; save_iptables; remove_masquerade_if_unused; echo "RC=$?"; }
o=$(lib t_misc)
[ "$(ctl_log | tr '\n' ';')" = "fwd del --proto udp --port 51820 --json;fwd flush;" ] && ok "remove_rules_for_port → fwd del, flush_all_kaskad_rules → fwd flush" || bad "del/flush" "$(ctl_log)"
[ -z "$(ls -A "$IPT_DIR")" ] && [ ! -s "$CALLS/netfilter-persistent.log" ] && printf '%s' "$o" | grep -q 'RC=0' && ok "save_iptables и remove_masquerade_if_unused ничего не делают" || bad "save/masq в nft"
: > "$CALLS/kaskadctl.log"
t_listui() { list_active_rules </dev/null; }
o=$(lib t_listui | strip)
ctl_log | grep -qx 'fwd list --traffic' && printf '%s' "$o" | grep -q '2.2.2.2:443.*трафик: 1.5 KB' && printf '%s' "$o" | grep -q '5.5.5.5:51820.*трафик: 3.0 MB' \
  && ok "«Правила»: fwd list --traffic, столбец трафика" || bad "список с трафиком" "$o"
: > "$CALLS/kaskadctl.log"
t_delui() { delete_single_rule <<< "2"; }
o=$(lib t_delui | strip)
ctl_log | grep -qx 'fwd del --proto udp --port 51820 --json' && printf '%s' "$o" | grep -q 'Правило удалено' && ok "«Удалить правило» → fwd del выбранного" || bad "удаление в меню" "$(ctl_log)"
: > "$CALLS/kaskadctl.log"
t_delbad() { delete_single_rule < <(printf 'первый\n1+1\n08\n7\n\n'); echo "RC=$?"; }
o=$(lib t_delbad 2>&1 | strip)
[ -z "$(ctl_log)" ] && [ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта: номер от 1 до 2, 0 — назад.')" = 4 ] \
  && printf '%s' "$o" | grep -q 'Отменено, ничего не удалено' && ! printf '%s' "$o" | grep -qiE 'syntax error|operand|value too great' \
  && printf '%s' "$o" | grep -q 'RC=' && ok "п. 9: слово («первый»), «1+1», «08», чужой номер — «Нет такого пункта: номер от 1 до 2, 0 — назад» и вопрос снова, меню не падает; Enter — отмена" \
  || bad "п. 9: ввод не числа" "$o"
engine_nft
t_delnone() { delete_single_rule < <(printf '\n'); }
o=$(lib t_delnone 2>&1 | strip)
printf '%s' "$o" | grep -q 'Нет активных правил' && ! printf '%s' "$o" | grep -q '\[1\]' && ok "п. 9 без правил — «Нет активных правил», а не пустая строка «[1]»" || bad "п. 9 без правил" "$o"
engine_nft_fwds
: > "$CALLS/kaskadctl.log"
t_flushui() { flush_rules <<< "1"; }
o=$(lib t_flushui | strip)
ctl_log | grep -qx 'fwd flush' && printf '%s' "$o" | grep -q 'Очищено' && ! grep -q 'delete' "$CALLS/ufw.log" 2>/dev/null \
  && ok "«Сбросить всё» → fwd flush; ufw выключен — его правила не трогаются" || bad "сброс в меню" "$(ctl_log)"
# critic #15c: ufw включён — удалённый проброс забирает и свой ufw allow (как 2.x: пробросы из 2.x его принесли)
touch "$FAKE_DIR/ufw_active"; : > "$CALLS/ufw.log"
lib t_delui >/dev/null 2>&1
grep -qx 'delete allow 51820/udp' "$CALLS/ufw.log" && [ "$(grep -c '^delete' "$CALLS/ufw.log")" = "1" ] \
  && ok "ufw включён: «Удалить правило» на nft → ufw delete allow 51820/udp" || bad "ufw при удалении" "$(cat "$CALLS/ufw.log")"
: > "$CALLS/ufw.log"; lib t_flushui >/dev/null 2>&1
grep -qx 'delete allow 443/tcp' "$CALLS/ufw.log" && grep -qx 'delete allow 51820/udp' "$CALLS/ufw.log" \
  && ok "ufw включён: «Сбросить всё» на nft → ufw delete allow каждого входного порта" || bad "ufw при сбросе" "$(cat "$CALLS/ufw.log")"
: > "$CALLS/ufw.log"
t_botdel_nft() { K3_BOT=1; BOT_TOKEN="1:x"; bot_handle_callback 42 7 cb "dry_1"; }
lib t_botdel_nft >/dev/null 2>&1
grep -qx 'delete allow 443/tcp' "$CALLS/ufw.log" && ctl_log | grep -qx 'fwd del --proto tcp --port 443 --json' \
  && ok "ufw включён: бот, «Удалить» на nft → ufw delete allow 443/tcp" || bad "ufw: бот" "$(cat "$CALLS/ufw.log") | $(ctl_log)"
: > "$CALLS/ufw.log"
t_botflush_nft() { K3_BOT=1; BOT_TOKEN="1:x"; bot_handle_callback 42 7 cb "fa_y"; }
lib t_botflush_nft >/dev/null 2>&1
grep -qx 'delete allow 443/tcp' "$CALLS/ufw.log" && grep -qx 'delete allow 51820/udp' "$CALLS/ufw.log" \
  && ok "ufw включён: бот, «Сбросить всё» на nft → ufw delete allow каждого входного порта" || bad "ufw: бот, сброс" "$(cat "$CALLS/ufw.log")"
rm -f "$FAKE_DIR/ufw_active"
t_header() { k3_engine_label; }
[ "$(lib t_header)" = "nftables" ] && ok "в шапке меню: движок nftables" || bad "метка движка"

echo "── 5. конфликт порта (код 3): вопрос и --force в меню, отказ в боте ──"
use_root conflict; fake_ctl; engine_nft
echo '{"ok": false, "error": "порт 443 слушает nginx", "code": 3, "data": {"kind": "listener"}}' > "$FAKE_DIR/conflict.json"
t_ask() { K3_TEST_TTY=1 apply_iptables_rules tcp 443 443 2.2.2.2 VLESS; echo "RC=$?"; }
o=$(printf '1\n' | lib t_ask 2>&1 | strip)
[ "$(ctl_log | wc -l | tr -d ' ')" = "2" ] && ctl_log | tail -1 | grep -q -- '--force' && printf '%s' "$o" | grep -q 'Порт занят: порт 443 слушает nginx' \
  && printf '%s' "$o" | grep -q 'RC=0' && ok "«Порт занят: … Всё равно?» → 1 → повтор с --force" || bad "вопрос о конфликте" "$o | $(ctl_log)"
printf '%s' "$o" | grep -q '^Всё равно?' && printf '%s' "$o" | grep -q '1) Добавить проброс всё равно' && printf '%s' "$o" | grep -q '2) Отмена' \
  && printf '%s' "$o" | grep -q 'Выбор \[2\]:' && ok "вопрос «Всё равно?» — пункты цифрами, Enter — «2) Отмена»" || bad "нет вопроса «Всё равно?» цифрами" "$o"
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n' | lib t_ask 2>&1 | strip)
[ "$(ctl_log | wc -l | tr -d ' ')" = "1" ] && printf '%s' "$o" | grep -q 'RC=3' && ok "n → без повтора, код 3" || bad "отказ от --force" "$o"
# T-G1 F-3: «2» (отмена) — выбор человека, а не ошибка: без красного [ERROR]; подсказка ядра про ключ --force (он для командной
# строки) — ни в меню, ни в ответе бота; и без «Порт занят: Порт …»
echo '{"ok": false, "error": "Порт 443/tcp занят: его занимает вход шлюза WireGuard (wg) — проброс заберёт его клиентов. Добавить всё равно — с --force.", "code": 3, "data": {"kind": "gw", "conflicts": [{"kind": "gw", "detail": "его занимает вход шлюза WireGuard (wg) — проброс заберёт его клиентов"}]}}' > "$FAKE_DIR/conflict.json"
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n' | lib t_ask 2>&1 | strip)
[ "$(ctl_log | wc -l | tr -d ' ')" = "1" ] && printf '%s' "$o" | grep -q 'RC=3' && printf '%s' "$o" | grep -q 'Отменено, правило не создано\.' \
  && ! printf '%s' "$o" | grep -q 'ERROR' && ! printf '%s' "$o" | grep -q -- '--force' && ! printf '%s' "$o" | grep -q 'Порт занят: Порт' \
  && printf '%s' "$o" | grep -q '^Порт 443/tcp занят: его занимает вход шлюза WireGuard' \
  && ok "«2» на «Всё равно?» (F-3): «Отменено, правило не создано» без [ERROR], без «--force» и без «Порт занят: Порт …»" || bad "отказ от проброса" "$o"
t_botf() { K3_BOT=1; apply_iptables_rules tcp 443 443 2.2.2.2 VLESS >/dev/null; k3_bot_refusal tcp 443 2.2.2.2 443; }
o=$(lib t_botf </dev/null 2>&1 | strip)
printf '%s' "$o" | grep -q 'Порт 443/tcp занят: его занимает' && ! printf '%s' "$o" | grep -q -- '--force' && ! printf '%s' "$o" | grep -q 'Порт занят: Порт' \
  && ok "бот: отказ из-за занятого порта — без подсказки про ключ --force и без «Порт занят: Порт …»" || bad "бот: --force в ответе" "$o"
echo '{"ok": false, "error": "22/tcp — порт SSH этого сервера", "code": 3, "data": {"kind": "ssh"}}' > "$FAKE_DIR/conflict.json"
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n' | lib t_ask 2>&1 | strip)
[ "$(ctl_log | wc -l | tr -d ' ')" = "1" ] && printf '%s' "$o" | grep -q 'ПОДТВЕРЖДАЮ' && ok "порт SSH: «1» не принимается, нужно слово" || bad "ssh: 1 прошло" "$o"
: > "$CALLS/kaskadctl.log"
o=$(printf 'ПОДТВЕРЖДАЮ\n' | lib t_ask 2>&1 | strip)
[ "$(ctl_log | wc -l | tr -d ' ')" = "2" ] && ctl_log | tail -1 | grep -q -- '--force' && printf '%s' "$o" | grep -q 'RC=0' && ok "порт SSH: слово ПОДТВЕРЖДАЮ → --force" || bad "ssh: слово не сработало" "$o"
: > "$CALLS/kaskadctl.log"
t_bot() { K3_BOT=1; apply_iptables_rules tcp 22 22 2.2.2.2 Custom; echo "RC=$? ERR=[$K3_FWD_ERR] KIND=[$K3_FWD_KIND]"; k3_bot_refusal tcp 22 2.2.2.2 22; }
o=$(lib t_bot </dev/null 2>&1 | strip)
[ "$(ctl_log | wc -l | tr -d ' ')" = "1" ] && printf '%s' "$o" | grep -q 'RC=3 ERR=\[22/tcp — порт SSH этого сервера\] KIND=\[ssh\]' \
  && printf '%s' "$o" | grep -q 'Проброс не добавлен' && ok "бот: без вопроса, код 3 и причина для ответа" || bad "бот и конфликт" "$o"
: > "$CALLS/kaskadctl.log"
t_botflow() {
    K3_BOT=1; BOT_TOKEN="1:x"; mkdir -p "$BOT_STATE_DIR"
    bot_set_state 42 "STATE=awaiting_note" "PROTO=tcp" "NAME=VLESS" "CUSTOM=0" "TARGET_IP=2.2.2.2" "PORT=22"
    bot_handle_message 42 "-"
}
lib t_botflow >/dev/null 2>&1
grep -q 'Проброс не добавлен' "$CALLS/tg.log" && grep -q 'Порт занят: 22/tcp' "$CALLS/tg.log" && ! grep -q '✅' "$CALLS/tg.log" \
  && ok "бот (поток добавления): «⛔ Проброс не добавлен … Порт занят: …», без «✅»" || bad "бот отправил" "$(cat "$CALLS/tg.log")"
rm -f "$FAKE_DIR/conflict.json"; : > "$CALLS/tg.log"
lib t_botflow >/dev/null 2>&1
grep -q '✅' "$CALLS/tg.log" && ok "бот без конфликта: «✅ …» как в 2.13" || bad "бот без конфликта" "$(cat "$CALLS/tg.log")"

echo "── 6. перенос правил бесплатной версии (legacy): два правила на порт → остаётся первое ──"
use_root adopt
cat > "$IPT_DIR/nat.PREROUTING" <<'R'
-p tcp -m tcp --dport 18080 -j DNAT --to-destination 1.1.1.1:80
-p tcp -m tcp --dport 18080 -j DNAT --to-destination 3.3.3.3:80
-p tcp -m tcp --dport 443 -m comment --comment kaskad:443:tcp -j DNAT --to-destination 2.2.2.2:443
-p tcp -m tcp --dport 443 -j DNAT --to-destination 4.4.4.4:443
R
cat > "$IPT_DIR/filter.FORWARD" <<'R'
-d 1.1.1.1/32 -p tcp -m tcp --dport 80 -m state --state NEW,RELATED,ESTABLISHED -j ACCEPT
-s 1.1.1.1/32 -p tcp -m tcp --sport 80 -m state --state RELATED,ESTABLISHED -j ACCEPT
-d 3.3.3.3/32 -p tcp -m tcp --dport 80 -m state --state NEW,RELATED,ESTABLISHED -j ACCEPT
-s 3.3.3.3/32 -p tcp -m tcp --sport 80 -m state --state RELATED,ESTABLISHED -j ACCEPT
-d 2.2.2.2/32 -p tcp -m tcp --dport 443 -m state --state NEW,RELATED,ESTABLISHED -m comment --comment kaskad:443:tcp -j ACCEPT
-d 4.4.4.4/32 -p tcp -m tcp --dport 443 -m state --state NEW,RELATED,ESTABLISHED -j ACCEPT
-s 4.4.4.4/32 -p tcp -m tcp --sport 443 -m state --state RELATED,ESTABLISHED -j ACCEPT
R
t_adopt() { adopt_free_kaskad_rules; }
o=$(lib t_adopt 2>&1 | strip)
[ "$(grep -c -- '--dport 18080' "$IPT_DIR/nat.PREROUTING")" = "1" ] \
  && grep -q -- '--dport 18080 -m comment --comment kaskad:18080:tcp -j DNAT --to-destination 1.1.1.1:80' "$IPT_DIR/nat.PREROUTING" \
  && ok "tcp 18080: осталось первое (→ 1.1.1.1:80), с меткой pro" || bad "18080" "$(cat "$IPT_DIR/nat.PREROUTING")"
! grep -q '3\.3\.3\.3' "$IPT_DIR/nat.PREROUTING" "$IPT_DIR/filter.FORWARD" && ok "второе (→ 3.3.3.3) снято вместе с FORWARD" || bad "3.3.3.3 остался"
[ "$(grep -c -- '--dport 443' "$IPT_DIR/nat.PREROUTING")" = "1" ] && grep -q 'kaskad:443:tcp -j DNAT --to-destination 2.2.2.2:443' "$IPT_DIR/nat.PREROUTING" \
  && ! grep -q '4\.4\.4\.4' "$IPT_DIR/nat.PREROUTING" "$IPT_DIR/filter.FORWARD" && ok "tcp 443: правило pro стояло первым — оно и осталось, бесплатное за ним снято" || bad "443" "$(cat "$IPT_DIR/nat.PREROUTING")"
grep -q 'ADOPT free kaskad rules: 1 of 3' "$KASKAD_ROOT/var/log/kaskad.log" && ok "журнал: перенесено 1 из 3" || bad "журнал" "$(cat "$KASKAD_ROOT/var/log/kaskad.log" 2>/dev/null)"

echo "── 7. --k3-post-update: пути юнитов → копия в /opt, чужие юниты не тронуты ──"
use_root units
U="$KASKAD_ROOT/etc/systemd/system"; C="$KASKAD_ROOT/usr/local/bin/gokaskad"
printf '[Service]\nExecStart=%s --bot-daemon\n' "$C" > "$U/kaskad-bot.service"
printf '[Service]\nExecStart=%s --monitor-daemon\n' "$C" > "$U/kaskad-monitor.service"
printf '[Service]\nExecStartPre=-%s --egress-awg-up\nExecStart=/etc/kaskad/egress/xray run -c /etc/kaskad/egress/config.json\nExecStopPost=-%s --egress-awg-down\n' "$C" "$C" > "$U/kaskad-egress.service"
printf '[Service]\nType=oneshot\nExecStart=%s --egress-probe\n' "$C" > "$U/kaskad-egress-probe.service"
printf '[Timer]\nOnUnitActiveSec=10\n' > "$U/kaskad-egress-probe.timer"
printf '[Unit]\nDescription=my helper for %s\n[Service]\nExecStart=%s --bot-daemon\n' "$C" "$C" > "$U/my-helper.service"
cp "$U/my-helper.service" "$W/my-helper.orig"; cp "$U/kaskad-egress-probe.timer" "$W/timer.orig"
bash "$S" --k3-post-update </dev/null >/dev/null 2>&1; rc=$?
O="$K3_OPT/bin/gokaskad"
[ "$rc" = "0" ] && grep -qx "ExecStart=$O --bot-daemon" "$U/kaskad-bot.service" && grep -qx "ExecStart=$O --monitor-daemon" "$U/kaskad-monitor.service" \
  && ok "bot, monitor → $O" || bad "юниты не переписаны (rc=$rc)" "$(cat "$U/kaskad-bot.service")"
# 3.2 (сисадмин №13): проба потерь — не таймер, раз в 10 с разбиравший всю программу, а служба-цикл маленького скрипта
grep -qx "ExecStart=/bin/bash $KASKAD_DIR/egress/probe.sh" "$U/kaskad-egress-probe.service" && grep -qx 'Restart=always' "$U/kaskad-egress-probe.service" \
  && [ ! -e "$U/kaskad-egress-probe.timer" ] && grep -qx 'disable --no-reload --now kaskad-egress-probe.timer' "$CALLS/systemctl.log" \
  && grep -qxF 'n=0; while :; do egress_probe_sample; n=$((n+1)); if [ $((n % 360)) -eq 0 ] && egress_tofu_waiting; then "$K3_KICK" --egress-tofu-kick >/dev/null 2>&1; fi; if [ $((n % 6)) -eq 0 ] && egress_awg_nodns_waiting; then "$K3_KICK" --egress-awg-revive >/dev/null 2>&1; fi; sleep 10; done' "$KASKAD_DIR/egress/probe.sh" \
  && grep -q '^egress_tofu_waiting ()' "$KASKAD_DIR/egress/probe.sh" && grep -q '^K3_KICK=.*/bin/gokaskad$' "$KASKAD_DIR/egress/probe.sh" \
  && grep -q '^egress_awg_nodns_waiting ()' "$KASKAD_DIR/egress/probe.sh" && grep -q '^AWG_DIR=.*/egress/awg$' "$KASKAD_DIR/egress/probe.sh" \
  && [ "$(wc -c < "$KASKAD_DIR/egress/probe.sh")" -lt 20000 ] && bash -n "$KASKAD_DIR/egress/probe.sh" \
  && ok "проба потерь: таймер прошлых версий → служба-цикл kaskad-egress-probe маленького скрипта ($(wc -c < "$KASKAD_DIR/egress/probe.sh" | tr -d ' ') Б вместо всей программы)" \
  || bad "проба потерь после обновления" "$(cat "$U/kaskad-egress-probe.service" 2>&1) | $(cat "$CALLS/systemctl.log")"
! grep -qE -- '--egress-awg-(up|down)' "$U/kaskad-egress.service" && grep -qx "ExecStartPre=-$O --egress-awg-routes" "$U/kaskad-egress.service" \
  && grep -qx 'ExecStart=/etc/kaskad/egress/xray run -c /etc/kaskad/egress/config.json' "$U/kaskad-egress.service" \
  && grep -q 'больше не поднимает и не снимает ноги AmneziaWG' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "egress: служба xray бота ноги больше не поднимает и не снимает (--egress-awg-up → --egress-awg-routes, down убран), строка xray цела" || bad "egress" "$(cat "$U/kaskad-egress.service")"
cmp -s "$U/my-helper.service" "$W/my-helper.orig" && ok "чужой юнит не тронут" || bad "чужой юнит изменён"
[ "$(stat -c %a "$U/kaskad-bot.service" 2>/dev/null || stat -f %Lp "$U/kaskad-bot.service")" = "644" ] && ok "права юнита 0644" || bad "права юнита"
grep -qx 'daemon-reload' "$CALLS/systemctl.log" && ! grep -qE '^(restart|start|stop)' "$CALLS/systemctl.log" && ok "daemon-reload, без перезапуска" || bad "systemctl" "$(cat "$CALLS/systemctl.log")"
cmp -s "$S" "$O" && [ -f "$K3_OPT/lib/$SHA12/.ok" ] && ok "заодно: копия программы и код в /opt" || bad "post-update не подготовил /opt"
[ ! -s "$CALLS/kaskadctl.log" ] && ok "движок legacy — службы движка nftables не трогаются" || bad "post-update на legacy позвал kaskadctl" "$(cat "$CALLS/kaskadctl.log")"
: > "$CALLS/systemctl.log"; cp "$U/kaskad-bot.service" "$W/bot.1"
bash "$S" --k3-post-update </dev/null >/dev/null 2>&1
cmp -s "$U/kaskad-bot.service" "$W/bot.1" && ! grep -q 'daemon-reload' "$CALLS/systemctl.log" && ok "повторный запуск: ничего не меняет, без daemon-reload" || bad "повтор post-update" "$(cat "$CALLS/systemctl.log")"
# 4.0-C: обновление не оставляет у установленной панели старый Type=simple. Ядро переписывает только её
# существующий юнит и перечитывает systemd, без enable/start/restart; при смене кода это до try-restart панели.
printf '[Service]\nType=simple\nExecStart=/old/panel\n' > "$U/kaskad-panel.service"; ln -sfn "oldlib000000" "$K3_OPT/lib/current"
: > "$CALLS/systemctl.log"; : > "$CALLS/kaskadctl.log"
bash "$S" --k3-post-update </dev/null >/dev/null 2>&1; rc=$?
post_body=$(sed -n '/^k3_post_update() {/,/^}/p' "$S")
refresh_line=$(printf '%s\n' "$post_body" | grep -n 'k3ctl panel unit --refresh --quiet' | cut -d: -f1)
restart_line=$(printf '%s\n' "$post_body" | grep -n 'try-restart --no-block "\$K3_PANEL_UNIT"' | cut -d: -f1)
[ "$rc" = 0 ] && grep -qx 'Type=notify' "$U/kaskad-panel.service" \
  && grep -qx 'daemon-reload' "$CALLS/systemctl.log" \
  && grep -qx 'try-restart --no-block kaskad-panel.service' "$CALLS/systemctl.log" \
  && [ -n "$refresh_line" ] && [ -n "$restart_line" ] && [ "$refresh_line" -lt "$restart_line" ] \
  && ok "панель: --k3-post-update → panel unit --refresh до try-restart, без смены настроек" \
  || bad "post-update панели" "rc=$rc $(cat "$U/kaskad-panel.service") | $(cat "$CALLS/systemctl.log")"
rm -f "$U/kaskad-panel.service"
# critic #11: на движке nft --k3-post-update обновляет и службы движка (тексты юнитов новой версии):
# настоящий kaskadctl net install-units — юниты в $U, enable через поддельный systemctl.
engine_nft; : > "$CALLS/systemctl.log"
bash "$S" --k3-post-update </dev/null >/dev/null 2>&1; rc=$?
rm -f "$KASKAD_DIR/state.json"
[ "$rc" = "0" ] && grep -q "net check --repair" "$U/kaskad-net.service" 2>/dev/null && [ -f "$U/kaskad-net-watch.timer" ] \
  && grep -qx 'enable --no-reload kaskad-net.service' "$CALLS/systemctl.log" && grep -qx 'enable --no-reload --now kaskad-net-watch.timer' "$CALLS/systemctl.log" \
  && ok "движок nft: --k3-post-update → kaskadctl net install-units (юниты движка этой версии, включены)" \
  || bad "post-update на nft" "rc=$rc $(cat "$CALLS/systemctl.log")"
rm -f "$U/kaskad-net.service" "$U/kaskad-net-watch.service" "$U/kaskad-net-watch.timer"
# Шлюз включён и код сменился (обновление): сборщик kaskad-gw-collect перезапускается, затем gw apply;
# код тот же (старт демонов), шлюз выключен — ничего. Код меняет подделка k3_ensure_lib ($NEWLIB).
t_post_gw() {
    k3_ensure_lib() { mkdir -p "$K3_OPT/lib" && ln -sfn "$NEWLIB" "$K3_OPT/lib/current"; }
    k3_lib_ok() { return 0; }
    k3_units_rewrite() { return 1; }
    k3ctl() { printf '%s\n' "$*" >> "$CALLS/kaskadctl.log"; [ "$1 $2" != "gw apply" ] || return "${GW_APPLY_RC:-0}"; }
    k3_post_update
}
gw_state() { printf '{\n  "engine": "nft",\n  "gateway": {\n    "enabled": %s\n  },\n  "schema": 1\n}\n' "$1" > "$KASKAD_DIR/state.json"; }
gw_state true; ln -sfn "oldlib000000" "$K3_OPT/lib/current"; : > "$CALLS/systemctl.log"; : > "$CALLS/kaskadctl.log"
NEWLIB=newlib000000 lib t_post_gw >/dev/null 2>&1
grep -qx 'try-restart kaskad-gw-collect.service' "$CALLS/systemctl.log" && grep -qx 'gw apply' "$CALLS/kaskadctl.log" \
  && [ "$(grep -n 'gw apply' "$CALLS/kaskadctl.log" | cut -d: -f1)" -gt "$(grep -n 'net install-units' "$CALLS/kaskadctl.log" | cut -d: -f1)" ] \
  && ok "шлюз включён, код сменился: перезапуск kaskad-gw-collect и kaskadctl gw apply" \
  || bad "post-update шлюза" "$(cat "$CALLS/systemctl.log") | $(ctl_log)"
: > "$CALLS/systemctl.log"; : > "$CALLS/kaskadctl.log"; touch "$U/kaskad-gw-rules.timer"
NEWLIB=newlib000000 lib t_post_gw >/dev/null 2>&1
! grep -q 'kaskad-gw-collect' "$CALLS/systemctl.log" && ! grep -q 'gw apply' "$CALLS/kaskadctl.log" \
  && ok "код тот же (старт бота или мониторинга): сборщик и шлюз не трогаются" || bad "post-update без смены кода" "$(ctl_log)"
rm -f "$U/kaskad-gw-rules.timer"; : > "$CALLS/systemctl.log"; : > "$CALLS/kaskadctl.log"
NEWLIB=newlib000000 lib t_post_gw >/dev/null 2>&1
! grep -q 'kaskad-gw-collect' "$CALLS/systemctl.log" && grep -qx 'gw apply' "$CALLS/kaskadctl.log" \
  && ok "3.2: шлюз включён, а таймера правил нет (3.1.0, прошлый apply не прошёл) — gw apply его ставит, сборщик не трогается" \
  || bad "post-update: таймер правил" "$(cat "$CALLS/systemctl.log") | $(ctl_log)"
gw_state false; ln -sfn "oldlib000000" "$K3_OPT/lib/current"; : > "$CALLS/systemctl.log"; : > "$CALLS/kaskadctl.log"
NEWLIB=newlib000000 lib t_post_gw >/dev/null 2>&1
! grep -q 'kaskad-gw-collect' "$CALLS/systemctl.log" && ! grep -q 'gw apply' "$CALLS/kaskadctl.log" \
  && ok "шлюз выключен: gw apply не зовётся" || bad "post-update при выключенном шлюзе" "$(ctl_log)"
gw_state true; ln -sfn "oldlib000000" "$K3_OPT/lib/current"; : > "$KASKAD_ROOT/var/log/kaskad.log"
GW_APPLY_RC=1 NEWLIB=newlib000000 lib t_post_gw >/dev/null 2>&1
grep -q 'шлюз после обновления не применён' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "gw apply не удался — строка в журнале" || bad "нет строки о неудачном gw apply" "$(cat "$KASKAD_ROOT/var/log/kaskad.log")"
# Пул «Прокси для API Telegram» сменился (egress_rebuild, egress_disable): шлюз включён — сразу gw apply
# (выходы шлюза привязаны к номерам строк пула); выключен, движок iptables, ядра нет — ничего.
t_pool() {
    k3_lib_ok() { return 0; }
    k3ctl() { printf '%s\n' "$*" >> "$CALLS/kaskadctl.log"; [ "$1 $2" != "gw apply" ] || return "${GW_APPLY_RC:-0}"; }
    k3_gw_pool_changed
}
: > "$CALLS/kaskadctl.log"; : > "$KASKAD_ROOT/var/log/kaskad.log"
printf '{\n  "engine": "nft",\n  "gateway": {"enabled": true},\n  "schema": 1\n}\n' > "$KASKAD_DIR/state.json"
o=$(lib t_pool 2>&1 | strip)
grep -qx 'gw apply' "$CALLS/kaskadctl.log" && printf '%s' "$o" | grep -q 'выходы пула сменились' \
  && ok "пул сменился, шлюз включён → сразу kaskadctl gw apply (номера строк пула — выходы шлюза)" || bad "gw apply после смены пула" "$o | $(ctl_log)"
: > "$CALLS/kaskadctl.log"; GW_APPLY_RC=1 lib t_pool >/dev/null 2>&1
grep -q 'шлюз после смены пула не применён' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "gw apply после смены пула не удался — строка в журнале" || bad "нет строки в журнале" "$(cat "$KASKAD_ROOT/var/log/kaskad.log")"
printf '{\n  "engine": "nft",\n  "gateway": {"enabled": false},\n  "schema": 1\n}\n' > "$KASKAD_DIR/state.json"
: > "$CALLS/kaskadctl.log"; lib t_pool >/dev/null 2>&1
printf '{\n  "engine": "legacy",\n  "schema": 1\n}\n' > "$KASKAD_DIR/state.json"; lib t_pool >/dev/null 2>&1
[ ! -s "$CALLS/kaskadctl.log" ] && ok "шлюз выключен или движок iptables — gw apply не зовётся" || bad "лишний gw apply" "$(ctl_log)"
body=$(sed -n '/^egress_rebuild() {/,/^}/p' "$S"); dis=$(sed -n '/^egress_disable() {/,/^}/p' "$S")
printf '%s\n' "$body" | tail -3 | grep -q '^    k3_gw_pool_changed$' && printf '%s\n' "$dis" | grep -q '^    k3_gw_pool_changed$' \
  && [ "$(printf '%s\n' "$body" | grep -n 'k3_gw_pool_changed' | cut -d: -f1)" -gt "$(printf '%s\n' "$body" | grep -n 'egress_write_service' | cut -d: -f1)" ] \
  && ok "egress_rebuild (после перезапуска ног) и egress_disable зовут k3_gw_pool_changed" || bad "k3_gw_pool_changed не на месте"
rm -f "$KASKAD_DIR/state.json" "$K3_OPT/lib/current"; ln -sfn "$SHA12" "$K3_OPT/lib/current"
t_unitcmd() { k3_unit_cmd; }
[ "$(lib t_unitcmd)" = "$O" ] && ok "новые юниты пишутся с $O" || bad "k3_unit_cmd: $(lib t_unitcmd)"
rm -f "$O"; [ "$(lib t_unitcmd)" = "$C" ] && ok "копии в /opt нет — юниты пишутся с командой, как 2.x" || bad "k3_unit_cmd без копии"

echo "── 8. self_update: ветка, запрет понижения, целостность ──"
su_setup() {   # $1 — main, $2 — test (версии; "" — ветки нет)
    use_root "su-$3"; C="$KASKAD_ROOT/usr/local/bin/gokaskad"; U="$KASKAD_ROOT/etc/systemd/system"
    cp "$S" "$C"; chmod 755 "$C"
    printf 'BOT_TOKEN=""\nBOT_CHAT_ID=""\nMENU_STYLE="inline"\nGITHUB_PAT=""\nBOT_PROXY=""\n' > "$KASKAD_DIR/config"
    [ -n "$1" ] && mk_version "$1" "$FAKE_DIR/gh/main.sh"
    [ -n "$2" ] && mk_version "$2" "$FAKE_DIR/gh/test.sh"
    [ -n "${4:-}" ] && mk_version "$4" "$FAKE_DIR/gh/dev.sh"
    printf '[Service]\nExecStart=%s --bot-daemon\n' "$C" > "$U/kaskad-bot.service"
    echo kaskad-bot > "$FAKE_DIR/active"
}
t_su() { self_update </dev/null; }
su_setup 2.13 $NEWER auto
o=$(lib t_su 2>&1 | strip)
[ "$(ver_of "$C")" = "$NEWER" ] && printf '%s' "$o" | grep -q "На main версия 2.13 — старше вашей $CUR" && printf '%s' "$o" | grep -q "Обновлено до v$NEWER (ветка test)" \
  && ok "ветка не задана, на main 2.13 < $CUR → взята test, стоит $NEWER" || bad "выбор ветки" "$o"
[ "$(grep -c 'kaskad-pro/main/install.sh' "$CALLS/curl.log")" = "1" ] && [ "$(grep -c 'kaskad-pro/test/install.sh' "$CALLS/curl.log")" = "1" ] && ok "скачано: main, затем test" || bad "загрузки" "$(cat "$CALLS/curl.log")"
[ "$(ver_of "$K3_OPT/bin/gokaskad")" = "$NEWER" ] && grep -qx "ExecStart=$K3_OPT/bin/gokaskad --bot-daemon" "$U/kaskad-bot.service" \
  && ok "после замены: --k3-post-update нового файла — копия в /opt $NEWER, юнит на копию" || bad "post-update после обновления"
dr=$(grep -n '^daemon-reload' "$CALLS/systemctl.log" | head -1 | cut -d: -f1); rs=$(grep -n '^restart kaskad-bot' "$CALLS/systemctl.log" | head -1 | cut -d: -f1)
[ -n "$dr" ] && [ -n "$rs" ] && [ "$dr" -lt "$rs" ] && ok "сначала daemon-reload, потом перезапуск бота" || bad "порядок" "$(cat "$CALLS/systemctl.log")"
[ "$(ver_of "$C.bak")" = "$CUR" ] && ok "бэкап прошлой версии: gokaskad.bak ($CUR)" || bad "бэкап"
grep -q "Самообновление -> v$NEWER (test, перезапущены: bot)" "$KASKAD_ROOT/var/log/kaskad.log" && ! grep -q 'Self-update' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "журнал русского сервера: «Самообновление -> v$NEWER (test, перезапущены: bot)» (T-M6-332 №22)" || bad "журнал обновления" "$(tail -3 "$KASKAD_ROOT/var/log/kaskad.log")"
# 3.3.5 №5 (T-335 §6): новый файл в --k3-post-update сменил ветку (мост k3 → dev) — журнал и экран говорят «test → dev»,
# а шапка этого (прежнего) процесса меню — «обновлено до …, откройте заново», не «есть …: 11»
su_setup "" $NEWER br-moved; echo 'KASKAD_BRANCH="test"' >> "$KASKAD_DIR/config"
python3 - "$FAKE_DIR/gh/test.sh" <<'PY'
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
a = "k3_post_update() {\n"
s = s.replace(a, a + "    sed -i.bak 's/^KASKAD_BRANCH=.*/KASKAD_BRANCH=\"dev\"/' \"$KASKAD_CONF\"; rm -f \"$KASKAD_CONF.bak\"\n", 1)
open(p, "w", encoding="utf-8").write(s)
PY
t_su_head() { self_update </dev/null; K3_H_LATEST="$NEWER"; k3_head_lines 300; printf 'HEAD=%s\n' "${K3_HL[1]}"; }
o=$(lib t_su_head 2>&1 | strip)
[ "$(ver_of "$C")" = "$NEWER" ] && grep -qx 'KASKAD_BRANCH="dev"' "$KASKAD_DIR/config" && printf '%s' "$o" | grep -q "Обновлено до v$NEWER (ветка test → dev)" \
  && grep -q "Самообновление -> v$NEWER (test → dev, перезапущены: bot)" "$KASKAD_ROOT/var/log/kaskad.log" \
  && printf '%s' "$o" | grep -q "^HEAD=.*обновлено до $NEWER — откройте меню заново: gokaskad" && ! printf '%s' "$o" | grep -q "^HEAD=.*есть $NEWER: 11" \
  && ok "post-update нового файла сменил ветку: журнал «test → dev», шапка прежнего меню — «обновлено до $NEWER, откройте заново»" \
  || bad "ветка после post-update / шапка" "$o | $(tail -2 "$KASKAD_ROOT/var/log/kaskad.log")"
su_setup 2.13 $NEWER explicit; echo 'KASKAD_BRANCH="main"' >> "$KASKAD_DIR/config"
o=$(lib t_su 2>&1 | strip)
cmp -s "$S" "$C" && printf '%s' "$o" | grep -q "На ветке main версия 2.13 — старше вашей $CUR. Обновлять нечего." && ! grep -q test "$CALLS/curl.log" \
  && ok "KASKAD_BRANCH=main с 2.13: «На ветке main версия 2.13 — старше вашей $CUR. Обновлять нечего.»" || bad "понижение не запрещено" "$o"
su_setup 2.13 $NEWER tail; printf '# хвост\n# без\n# esac\n' >> "$FAKE_DIR/gh/test.sh"     # синтаксис цел, а esac не в хвосте
o=$(lib t_su 2>&1 | strip)
cmp -s "$S" "$C" && printf '%s' "$o" | grep -q 'Файл неполный' && ok "нет esac в хвосте нового файла → «Файл неполный», ничего не заменено" || bad "проверка хвоста" "$o"
su_setup "" "" same; echo 'KASKAD_BRANCH="test"' >> "$KASKAD_DIR/config"; cp "$S" "$FAKE_DIR/gh/test.sh"
o=$(lib t_su 2>&1 | strip)
printf '%s' "$o" | grep -q "У вас уже последняя версия ветки test: v$CUR" && [ ! -e "$C.bak" ] && ok "та же версия и тот же файл → «уже последняя», без замены" || bad "та же версия" "$o"
su_setup "" $NEWER rollback; echo 'KASKAD_BRANCH="test"' >> "$KASKAD_DIR/config"; touch "$FAKE_DIR/dies_once.kaskad-bot"
lib t_su >/dev/null 2>&1
cmp -s "$S" "$C" && cmp -s "$S" "$K3_OPT/bin/gokaskad" && grep -q 'Самообновление ОТКАЧЕНО (бот не запустился)' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "бот не поднялся на $NEWER → откат: команда и копия в /opt снова $CUR" || bad "откат self_update" "$(ver_of "$C") / $(ver_of "$K3_OPT/bin/gokaskad")"
# --update-check (kaskadctl update check, MCP): те же правила ветки, ничего не меняется, одна строка JSON
t_uc() { k3_update_check; echo "RC=$?"; }
su_setup 2.13 $NEWER uc
o=$(lib t_uc 2>/dev/null)
printf '%s\n' "$o" | head -1 | python3 -c 'import json,sys; d=json.loads(sys.stdin.readline()); sys.exit(0 if (d["ok"], d["available"], d["latest"], d["branch"], d["current"]) == (True, True, sys.argv[1], "test", sys.argv[2]) else 1)' "$NEWER" "$CUR" \
  && printf '%s' "$o" | grep -q 'RC=0' && cmp -s "$S" "$C" && [ ! -e "$C.bak" ] \
  && ok "--update-check: на main 2.13 → test, есть $NEWER; файл не тронут" || bad "--update-check" "$o"
su_setup "" "" ucsame; echo 'KASKAD_BRANCH="test"' >> "$KASKAD_DIR/config"; cp "$S" "$FAKE_DIR/gh/test.sh"
o=$(lib t_uc 2>/dev/null)
printf '%s' "$o" | grep -q '"available":false,"same_file":true' && ok "--update-check: та же версия и тот же файл → available false" || bad "--update-check без новой версии" "$o"
su_setup "" "" ucnone; echo 'KASKAD_BRANCH="test"' >> "$KASKAD_DIR/config"
o=$(lib t_uc 2>/dev/null)
printf '%s' "$o" | grep -q '"ok":false,"reason":"fetch"' && printf '%s' "$o" | grep -q 'RC=1' && ok "--update-check: ветки нет → ok false, reason fetch, код 1" || bad "--update-check без ветки" "$o"
# Стенд T-334: при KASKAD_LANG=en отказ был по-русски — теперь текст на языке запуска, JSON цельный
o=$(KASKAD_LANG=en lib t_uc 2>/dev/null)
printf '%s\n' "$o" | head -1 | python3 -c 'import json,sys; d=json.loads(sys.stdin.readline()); sys.exit(0 if d["reason"] == "fetch" and d["text"].startswith("Couldn’t download install.sh of the test branch: no network") else 1)' \
  && ok "--update-check по-английски: отказ «Couldn’t download install.sh of the test branch…» (стенд T-334)" || bad "--update-check: язык отказа" "$o"
grep -q '^    --update-check) init_config >/dev/null 2>&1; k3_update_check; exit \$? ;;' "$S" \
  && grep -q '^    --self-update) init_config; self_update </dev/null ;;' "$S" \
  && ok "режимы --update-check и --self-update (stdin пуст: ключ не спрашивается)" || bad "нет режимов обновления для kaskadctl update"

echo "── 8b. ветка обновлений: запись при установке, цепочка main → test → dev (3.2.23) ──"
# Версии ниже этой сборки (V): так выглядит GitHub для сборки из dev — main 2.13, test 3.1.0.
OLDER=$(printf '%s\n' "$CUR" | awk -F. '{ if ($3 > 0) printf "%d.%d.%d", $1, $2, $3 - 1; else printf "%d.%d.99", $1, $2 - 1 }')
cfg_branch() { sed -n 's/^KASKAD_BRANCH="\(.*\)"$/\1/p' "$KASKAD_DIR/config"; }
t_rb() { init_config >/dev/null 2>&1; k3_remember_branch "$@"; echo "RC=$?"; printf 'NOTE=%s\n' "$K3_BRANCH_NOTE"; }
use_root br-env; mk_version 2.13 "$FAKE_DIR/gh/main.sh"; mk_version 3.1.0 "$FAKE_DIR/gh/test.sh"
o=$(KASKAD_BRANCH="dev/3.x_b-1" lib t_rb auto 2>&1)
[ "$(cfg_branch)" = "dev/3.x_b-1" ] && printf '%s' "$o" | grep -q 'NOTE=Ветка обновлений: dev/3.x_b-1 — записана из команды установки' \
  && [ ! -s "$CALLS/curl.log" ] && ok "KASKAD_BRANCH из команды установки записана в настройки, GitHub не спрашивали" || bad "ветка из окружения" "$o | $(cat "$KASKAD_DIR/config")"
for junk in '../main' 'a..b' 'dev;id' 'dev x' '$(id)'; do
    use_root br-junk; mk_version "$CUR" "$FAKE_DIR/gh/test.sh"; mk_version 2.13 "$FAKE_DIR/gh/main.sh"
    o=$(KASKAD_BRANCH="$junk" lib t_rb auto 2>&1)
    if [ -z "$(cfg_branch)" ] && ! grep -q 'KASKAD_BRANCH' "$KASKAD_DIR/config" && printf '%s' "$o" | grep -q 'NOTE=\[!\] KASKAD_BRANCH из команды установки не принята' \
       && ! printf '%s' "$o" | grep -qF "$junk"; then ok "мусор в KASKAD_BRANCH («${junk}») отклонён, не записан и не показан"
    else bad "мусор в KASKAD_BRANCH: ${junk}" "$o | $(cat "$KASKAD_DIR/config")"; fi
done
use_root br-own; mk_version 2.13 "$FAKE_DIR/gh/main.sh"; mk_version 3.1.0 "$FAKE_DIR/gh/test.sh"
printf 'BOT_TOKEN=""\nKASKAD_BRANCH="test"\n' > "$KASKAD_DIR/config"
o=$(unset KASKAD_BRANCH; lib t_rb auto 2>&1)
[ "$(cfg_branch)" = "test" ] && [ "$(grep -c '^KASKAD_BRANCH=' "$KASKAD_DIR/config")" = 1 ] && [ ! -s "$CALLS/curl.log" ] && printf '%s' "$o" | grep -qx 'NOTE=' \
  && ok "без KASKAD_BRANCH в команде своя строка KASKAD_BRANCH=\"test\" в настройках не тронута (и автоопределением тоже)" || bad "своя ветка перезаписана" "$o | $(cat "$KASKAD_DIR/config")"
# M6 332c №1: обновление поверх однострочником с явной веткой — она перебивает записанную, строка на экране одна
o=$(KASKAD_BRANCH="dev-next" lib t_rb auto 2>&1)
[ "$(cfg_branch)" = "dev-next" ] && [ "$(grep -c '^KASKAD_BRANCH=' "$KASKAD_DIR/config")" = 1 ] && [ ! -s "$CALLS/curl.log" ] \
  && [ "$(printf '%s\n' "$o" | sed -n 's/^NOTE=//p' | grep -c .)" = 1 ] && printf '%s' "$o" | grep -qx 'NOTE=Ветка обновлений: dev-next — записана из команды установки' \
  && grep -q 'Update branch saved: dev-next (environment, was test)' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "явная KASKAD_BRANCH=dev-next поверх записанной test — перезаписана, одна строка «Ветка обновлений: dev-next…»" || bad "явная ветка не перебила записанную" "$o | $(cat "$KASKAD_DIR/config")"
o=$(KASKAD_BRANCH='dev;id' lib t_rb auto 2>&1)
[ "$(cfg_branch)" = "dev-next" ] && printf '%s' "$o" | grep -q 'NOTE=\[!\] KASKAD_BRANCH из команды установки не принята' \
  && ok "негодная KASKAD_BRANCH поверх записанной — записанная не тронута, предупреждение" || bad "негодная ветка поверх записанной" "$o | $(cat "$KASKAD_DIR/config")"
o=$(KASKAD_LANG=en KASKAD_BRANCH="dev" lib t_rb auto 2>&1)
[ "$(cfg_branch)" = "dev" ] && printf '%s' "$o" | grep -qx 'NOTE=Update branch: dev — saved from the install command' \
  && ok "EN: «Update branch: dev — saved from the install command»" || bad "EN строка ветки" "$o"
use_root br-auto; mk_version 2.13 "$FAKE_DIR/gh/main.sh"; mk_version 3.1.0 "$FAKE_DIR/gh/test.sh"; : > "$CALLS/curl_argv.log"
t_rbk() { init_config >/dev/null 2>&1; save_config_val GITHUB_PAT "ghp_BRANCHKEY0123456789abcdefABCDEF"; t_rb auto; }
o=$(lib t_rbk 2>&1)
[ "$(cfg_branch)" = "dev" ] && printf '%s' "$o" | grep -q 'NOTE=Ветка обновлений: dev (тестовые сборки) — записана автоматически' \
  && [ "$(grep -c 'kaskad-pro/main/install.sh (key)$' "$CALLS/curl.log")" = 1 ] && [ "$(grep -c 'kaskad-pro/test/install.sh (key)$' "$CALLS/curl.log")" = 1 ] \
  && [ "$(wc -l < "$CALLS/curl.log" | tr -d ' ')" = 2 ] && grep -q 'Update branch saved: dev (auto)' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "версия $CUR новее main (2.13) и test (3.1.0) → KASKAD_BRANCH=\"dev\" и строка «записана автоматически»" || bad "автоопределение dev" "$o | $(cat "$CALLS/curl.log")"
grep -q -- '-r 0-16383' "$CALLS/curl_argv.log" && grep -q -- '--max-time 10' "$CALLS/curl_argv.log" && ! grep -q -- '--retry' "$CALLS/curl_argv.log" \
  && ok "версии веток — по началу файла (-r 0-16383), таймаут 10 с, без повторов" || bad "лёгкий запрос версии" "$(cat "$CALLS/curl_argv.log")"
! grep -q 'BRANCHKEY' "$CALLS/curl_argv.log" && grep -q -- '--config' "$CALLS/curl_argv.log" && ! ls -A "$KASKAD_DIR" | grep -q '^\.hdr' \
  && ok "ключ GitHub при определении ветки — через --config, не в argv; временный файл убран" || bad "ключ в argv" "$(cat "$CALLS/curl_argv.log")"
use_root br-eq; mk_version 2.13 "$FAKE_DIR/gh/main.sh"; mk_version "$CUR" "$FAKE_DIR/gh/test.sh"
o=$(lib t_rb auto 2>&1)
! grep -q KASKAD_BRANCH "$KASKAD_DIR/config" && printf '%s' "$o" | grep -qx 'NOTE=' && ok "версия равна test → ничего не записано (человек ставил test)" || bad "V = test" "$o"
use_root br-main; mk_version "$NEWER" "$FAKE_DIR/gh/main.sh"; mk_version "$OLDER" "$FAKE_DIR/gh/test.sh"
o=$(lib t_rb auto 2>&1)
! grep -q KASKAD_BRANCH "$KASKAD_DIR/config" && ok "на main новее ($NEWER) → в dev не попадает" || bad "V < main" "$o"
use_root br-off; mk_version 2.13 "$FAKE_DIR/gh/main.sh"; mk_version 3.1.0 "$FAKE_DIR/gh/test.sh"; touch "$FAKE_DIR/offline"
t_rbo() { init_config >/dev/null 2>&1; GH_TOKEN="ghp_OFFLINEKEY0123456789abcdefABCDEF" k3_remember_branch auto; echo "RC=$?"; }
o=$(lib t_rbo 2>&1)
! grep -q KASKAD_BRANCH "$KASKAD_DIR/config" && printf '%s' "$o" | grep -q 'RC=0' && [ "$(wc -l < "$CALLS/curl.log" | tr -d ' ')" = 1 ] \
  && ok "сети нет → ничего не записано, код 0 (установка идёт дальше), один запрос без повтора" || bad "без сети" "$o | $(cat "$CALLS/curl.log")"
use_root br-404; mk_version 2.13 "$FAKE_DIR/gh/main.sh"
o=$(lib t_rb auto 2>&1)
! grep -q KASKAD_BRANCH "$KASKAD_DIR/config" && ok "test не ответил (404) → ничего не записано" || bad "test 404" "$o"
use_root br-noauto; mk_version 2.13 "$FAKE_DIR/gh/main.sh"; mk_version 3.1.0 "$FAKE_DIR/gh/test.sh"
o=$(lib t_rb 2>&1)
! grep -q KASKAD_BRANCH "$KASKAD_DIR/config" && [ ! -s "$CALLS/curl.log" ] && ok "запуск командой gokaskad (без auto) — GitHub не спрашивается" || bad "без auto" "$o"
body=$(sed -n '/^run_startup() {/,/^}/p' "$S")
printf '%s\n' "$body" | grep -q 'then k3_remember_branch auto; else k3_remember_branch; fi' \
  && [ "$(printf '%s\n' "$body" | grep -n 'k3_remember_gh_token' | head -1 | cut -d: -f1)" -lt "$(printf '%s\n' "$body" | grep -n 'k3_remember_branch auto' | cut -d: -f1)" ] \
  && ok "run_startup: ветка запоминается сразу после ключа GitHub; автоопределение — только файлом установки" || bad "вызов k3_remember_branch"

# цепочка меню 11 без записанной ветки: main 2.13 → test 3.1.0 → dev
su_setup 2.13 3.1.0 chain "$NEWER"
o=$(lib t_su 2>&1 | strip)
[ "$(ver_of "$C")" = "$NEWER" ] && [ "$(cfg_branch)" = dev ] && printf '%s' "$o" | grep -q 'Ветка обновлений: main — по умолчанию' \
  && printf '%s' "$o" | grep -q 'На test версия 3.1.0 — тоже старше вашей. Тестовые сборки Kaskad 3 — в ветке dev, беру её.' \
  && printf '%s' "$o" | grep -q "Обновлено до v$NEWER (ветка dev)" && printf '%s' "$o" | grep -q 'Ветка обновлений: dev (тестовые сборки) — записана автоматически' \
  && [ "$(grep -o 'kaskad-pro/[a-z0-9]*/install.sh' "$CALLS/curl.log" | cut -d/ -f2 | tr '\n' ' ')" = "main test dev " ] \
  && ok "меню 11 без ветки: main 2.13 → test 3.1.0 → dev $NEWER, обновлено, KASKAD_BRANCH=\"dev\" записана" || bad "цепочка self_update" "$o | $(cat "$CALLS/curl.log")"
o=$(lib t_su 2>&1 | strip)
printf '%s' "$o" | grep -q "Ветка обновлений: dev — записана в $KASKAD_DIR/config" && [ "$(grep -c 'kaskad-pro/dev/install.sh' "$CALLS/curl.log")" = 2 ] \
  && [ "$(grep -c 'kaskad-pro/main/install.sh' "$CALLS/curl.log")" = 1 ] \
  && ok "следующий раз меню 11 идёт сразу в dev и пишет, что ветка записана в настройках" || bad "второй запуск после записи" "$o"
su_setup 2.13 3.1.0 chain-down "$OLDER"
o=$(lib t_su 2>&1 | strip)
cmp -s "$S" "$C" && [ ! -e "$C.bak" ] && ! grep -q KASKAD_BRANCH "$KASKAD_DIR/config" && printf '%s' "$o" | grep -q "На ветке dev версия $OLDER — старше вашей $CUR. Обновлять нечего." \
  && ok "на dev старше ($OLDER) → понижения нет, ветка не записана" || bad "понижение по цепочке" "$o"
su_setup 2.13 3.1.0 chain-fail
o=$(lib t_su 2>&1 | strip)
cmp -s "$S" "$C" && ! grep -q KASKAD_BRANCH "$KASKAD_DIR/config" && printf '%s' "$o" | grep -q 'Не удалось скачать ветку dev' \
  && ok "dev не скачалась → ошибка, ничего не заменено и не записано" || bad "ошибка dev" "$o"
su_setup 2.13 3.1.0 chain-same; cp "$S" "$FAKE_DIR/gh/dev.sh"
o=$(lib t_su 2>&1 | strip)
printf '%s' "$o" | grep -q "У вас уже последняя версия ветки dev: v$CUR" && [ "$(cfg_branch)" = dev ] && [ ! -e "$C.bak" ] \
  && ok "та же версия на dev → «уже последняя», ветка dev записана (чинится сервер без строки)" || bad "та же версия на dev" "$o"
su_setup 2.13 3.1.0 uc-chain "$NEWER"
o=$(lib t_uc 2>/dev/null)
printf '%s\n' "$o" | head -1 | python3 -c 'import json,sys; d=json.loads(sys.stdin.readline()); sys.exit(0 if (d["ok"], d["branch"], d["auto"], d["remembered"], d["available"], d["latest"]) == (True, "dev", True, True, True, sys.argv[1]) else 1)' "$NEWER" \
  && [ "$(cfg_branch)" = dev ] && cmp -s "$S" "$C" \
  && ok "--update-check без ветки: main → test → dev, branch dev, auto и remembered true, ветка записана, программа не тронута" || bad "--update-check цепочка" "$o"
o=$(lib t_uc 2>/dev/null)
printf '%s' "$o" | grep -q '"branch":"dev","auto":false,"remembered":false' && ok "--update-check с записанной веткой: auto false" || bad "--update-check auto false" "$o"
su_setup 2.13 3.1.0 uc-down "$OLDER"
o=$(lib t_uc 2>/dev/null)
printf '%s' "$o" | grep -q '"branch":"dev","auto":true,"remembered":false,"available":false' && ! grep -q KASKAD_BRANCH "$KASKAD_DIR/config" \
  && ok "--update-check: на dev старше → available false, ветка не записана" || bad "--update-check dev старше" "$o"
su_setup 2.13 3.1.0 uc-fail
o=$(lib t_uc 2>/dev/null)
printf '%s' "$o" | grep -q '"ok":false,"reason":"fetch","text":"Не удалось скачать install.sh ветки dev."' && ! grep -q KASKAD_BRANCH "$KASKAD_DIR/config" \
  && ok "--update-check: dev не ответила → ok false, ветка не записана" || bad "--update-check ошибка dev" "$o"

echo "── 8c. 3.3.4: записанная k3 (заморожена на 3.3.3) — один раз на dev; цепочка main → test → dev ──"
t_peek() { k3_update_peek; echo "RC=$?"; }
UJ() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["latest"], d["branch"])' "$KASKAD_DIR/update.json" 2>/dev/null; }
cfg_mark() { sed -n 's/^KASKAD_BRANCH_K3_DEV="\(.*\)"$/\1/p' "$KASKAD_DIR/config"; }
t_next() { printf '%s|%s|%s\n' "$(_k3_su_next main)" "$(_k3_su_next test)" "$(_k3_su_next dev)"; }
[ "$(lib t_next)" = "test|dev|" ] && ok "цепочка без записанной ветки: main → test → dev, после dev — ничего (k3 в цепочке нет)" || bad "цепочка веток" "$(lib t_next)"
t_mv() { init_config >/dev/null 2>&1; k3_branch_to_dev "$@"; printf 'LINE=%s\nBR=%s\n' "$K3_BRANCH_DEV_LINE" "$KASKAD_BRANCH"; }
use_root mv-ru; printf 'BOT_TOKEN=""\nKASKAD_LANG="ru"\nKASKAD_BRANCH="k3"\n' > "$KASKAD_DIR/config"
o=$(lib t_mv 2>&1)
[ "$(cfg_branch)" = dev ] && [ "$(cfg_mark)" = 2 ] && [ "$(grep -c '^KASKAD_BRANCH=' "$KASKAD_DIR/config")" = 1 ] \
  && printf '%s' "$o" | grep -qx 'LINE=Ветка обновлений: k3 → dev — сборки Kaskad 3 теперь выходят в dev, записано в настройки' \
  && printf '%s' "$o" | grep -qx 'BR=dev' && grep -q 'Update branch moved: k3 -> dev' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "KASKAD_BRANCH=\"k3\" → \"dev\" в настройках, отметка 2, одна строка RU, запись в журнале" || bad "переход k3 → dev" "$o | $(cat "$KASKAD_DIR/config")"
o=$(lib t_mv 2>&1)
[ "$(cfg_branch)" = dev ] && printf '%s' "$o" | grep -qx 'LINE=' && [ "$(grep -c 'Update branch moved' "$KASKAD_ROOT/var/log/kaskad.log")" = 1 ] \
  && ok "второй запуск — ни строки, ни записи (переход один раз)" || bad "повтор перехода" "$o"
printf 'BOT_TOKEN=""\nKASKAD_LANG="ru"\nKASKAD_BRANCH="k3"\nKASKAD_BRANCH_K3_DEV="2"\n' > "$KASKAD_DIR/config"
o=$(lib t_mv 2>&1)
[ "$(cfg_branch)" = k3 ] && printf '%s' "$o" | grep -qx 'LINE=' \
  && ok "человек сам вернул k3 после перехода (отметка есть) — не трогаем" || bad "k3 по выбору человека" "$o | $(cat "$KASKAD_DIR/config")"
mv_bad=""
for b in test main dev k3-next ""; do
    if [ -n "$b" ]; then printf 'KASKAD_LANG="ru"\nKASKAD_BRANCH="%s"\n' "$b" > "$KASKAD_DIR/config"; else printf 'KASKAD_LANG="ru"\n' > "$KASKAD_DIR/config"; fi
    o=$(lib t_mv 2>&1)
    [ "$(cfg_branch)" = "$b" ] && [ -z "$(cfg_mark)" ] && printf '%s' "$o" | grep -qx 'LINE=' || mv_bad+="«$b»: $o | $(cat "$KASKAD_DIR/config"); "
done
[ -z "$mv_bad" ] && ok "другие ветки (test, main, dev, k3-next, не записана) — без изменений и без отметки" || bad "ветка не должна меняться" "$mv_bad"
use_root mv-quiet; printf 'KASKAD_LANG="en"\nKASKAD_BRANCH="k3"\n' > "$KASKAD_DIR/config"
o=$(lib t_mv quiet 2>&1)
[ "$(cfg_branch)" = dev ] && [ "$(cfg_mark)" = 1 ] && printf '%s' "$o" | grep -qx 'LINE=' \
  && ok "quiet (--k3-post-update, --update-check): переход сделан, отметка 1, строки нет" || bad "тихий переход" "$o | $(cat "$KASKAD_DIR/config")"
o=$(lib t_mv 2>&1)
[ "$(cfg_mark)" = 2 ] && printf '%s' "$o" | grep -qx 'LINE=Update branch: k3 → dev — Kaskad 3 builds now come out in dev; saved in the settings' \
  && ok "следующий запуск меню показывает отложенную строку (EN) один раз, отметка 2" || bad "отложенная строка" "$o | $(cat "$KASKAD_DIR/config")"
o=$(lib t_mv 2>&1)
printf '%s' "$o" | grep -qx 'LINE=' && ok "и больше не показывает" || bad "строка показана второй раз" "$o"
# REV-334 R4: тихий переход (отметка 1), а до запуска меню человек сам вписал k3 — строки «k3 → dev» нет, отметка 2
use_root mv-r4; printf 'KASKAD_LANG="ru"\nKASKAD_BRANCH="k3"\nKASKAD_BRANCH_K3_DEV="1"\n' > "$KASKAD_DIR/config"
o=$(lib t_mv 2>&1)
[ "$(cfg_branch)" = k3 ] && [ "$(cfg_mark)" = 2 ] && printf '%s' "$o" | grep -qx 'LINE=' \
  && ok "отметка 1, ветку сам вернули на k3 — ложной строки «k3 → dev» нет, k3 не тронута, отметка 2" || bad "ложная строка перехода" "$o | $(cat "$KASKAD_DIR/config")"
# REV-334 №1: k3 из команды установки (старый README: KASKAD_BRANCH=k3 ./install.sh) — записывается dev, одна строка
# «k3 → dev»; и поверх сервера, который уже перешёл (отметка 2), и на свежей установке, и поверх ручной k3
K3DEV_RU='Ветка обновлений: k3 → dev — сборки Kaskad 3 теперь выходят в dev, записано в настройки'
t_env() { t_rb "$@"; k3_branch_to_dev; printf 'LINE=%s\n' "$K3_BRANCH_DEV_LINE"; }
use_root env-k3; mk_version 2.13 "$FAKE_DIR/gh/main.sh"; mk_version 3.1.0 "$FAKE_DIR/gh/test.sh"
printf 'KASKAD_LANG="ru"\nKASKAD_BRANCH="dev"\nKASKAD_BRANCH_K3_DEV="2"\n' > "$KASKAD_DIR/config"
o=$(KASKAD_BRANCH=k3 lib t_env auto 2>&1)
[ "$(cfg_branch)" = dev ] && [ "$(cfg_mark)" = 2 ] && [ "$(grep -c '^KASKAD_BRANCH=' "$KASKAD_DIR/config")" = 1 ] && [ ! -s "$CALLS/curl.log" ] \
  && [ "$(printf '%s\n' "$o" | sed -n 's/^NOTE=//p' | grep -c .)" = 1 ] && printf '%s' "$o" | grep -qxF "NOTE=$K3DEV_RU" && printf '%s' "$o" | grep -qx 'LINE=' \
  && grep -q 'Update branch saved: dev (environment, was dev, k3 -> dev: k3 is frozen at 3.3.3)' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "KASKAD_BRANCH=k3 поверх перешедшего сервера (dev, отметка 2) — остаётся dev, одна строка «k3 → dev»" || bad "k3 из окружения поверх dev" "$o | $(cat "$KASKAD_DIR/config")"
o=$(KASKAD_BRANCH=k3 lib t_env auto 2>&1)
[ "$(cfg_branch)" = dev ] && [ "$(cfg_mark)" = 2 ] && printf '%s' "$o" | grep -qxF "NOTE=$K3DEV_RU" \
  && ok "и повтор той же команды — снова dev (k3 не закрепляется)" || bad "повтор k3 из окружения" "$o | $(cat "$KASKAD_DIR/config")"
use_root env-k3-fresh; rm -f "$KASKAD_DIR/config"
o=$(KASKAD_BRANCH=k3 lib t_env auto 2>&1)
[ "$(cfg_branch)" = dev ] && [ "$(cfg_mark)" = 2 ] && [ "$(printf '%s\n' "$o" | grep -c 'Ветка обновлений')" = 1 ] && printf '%s' "$o" | grep -qxF "NOTE=$K3DEV_RU" \
  && ! printf '%s' "$o" | grep -q 'записана из команды установки' \
  && ok "свежая установка с KASKAD_BRANCH=k3 — dev и одна строка (без спорящей «k3 — записана из команды»)" || bad "свежая установка с k3" "$o | $(cat "$KASKAD_DIR/config" 2>&1)"
use_root env-k3-own; printf 'KASKAD_LANG="en"\nKASKAD_BRANCH="k3"\nKASKAD_BRANCH_K3_DEV="1"\n' > "$KASKAD_DIR/config"
o=$(KASKAD_BRANCH=k3 lib t_env auto 2>&1)
[ "$(cfg_branch)" = dev ] && [ "$(cfg_mark)" = 2 ] && printf '%s' "$o" | grep -qx 'NOTE=Update branch: k3 → dev — Kaskad 3 builds now come out in dev; saved in the settings' \
  && printf '%s' "$o" | grep -qx 'LINE=' \
  && ok "EN: KASKAD_BRANCH=k3 поверх k3 с отметкой 1 — dev, одна строка (отложенная не повторяется)" || bad "EN k3 из окружения" "$o | $(cat "$KASKAD_DIR/config")"
# меню 11 с записанной k3: строка, затем сразу dev (k3 не скачивается), обновление
su_setup "" "" mv-su "$NEWER"; echo 'KASKAD_BRANCH="k3"' >> "$KASKAD_DIR/config"; mk_version "$CUR" "$FAKE_DIR/gh/k3.sh"
o=$(lib t_su 2>&1 | strip)
[ "$(ver_of "$C")" = "$NEWER" ] && [ "$(cfg_branch)" = dev ] && [ "$(cfg_mark)" = 2 ] \
  && [ "$(printf '%s\n' "$o" | grep -c 'Ветка обновлений: k3 → dev')" = 1 ] && printf '%s' "$o" | grep -q 'записано в настройки\.$' && printf '%s' "$o" | grep -q "Обновлено до v$NEWER (ветка dev)" \
  && [ "$(grep -o 'kaskad-pro/[a-z0-9]*/install.sh' "$CALLS/curl.log" | cut -d/ -f2 | tr '\n' ' ')" = "dev " ] \
  && ok "меню 11 с KASKAD_BRANCH=\"k3\": одна строка «k3 → dev», скачана только dev, обновлено до $NEWER" || bad "меню 11 и переход" "$o | $(cat "$CALLS/curl.log")"
su_setup "" "" mv-marks "$NEWER"; echo 'KASKAD_BRANCH="k3"' >> "$KASKAD_DIR/config"
t_sum() { K3_MARKS=1 self_update </dev/null; }
o=$(lib t_sum 2>&1 | strip)
[ "$(cfg_branch)" = dev ] && [ "$(cfg_mark)" = 1 ] && ! printf '%s' "$o" | grep -q 'k3 → dev' && printf '%s' "$o" | grep -q "@K3:update_done:$NEWER:dev" \
  && ok "kaskadctl update run (K3_MARKS): переход тихо, отметка 1 — строку покажет меню" || bad "переход при K3_MARKS" "$o"
su_setup "" "" mv-uc "$NEWER"; echo 'KASKAD_BRANCH="k3"' >> "$KASKAD_DIR/config"
o=$(lib t_uc 2>/dev/null)
printf '%s' "$o" | grep -q '"branch":"dev","auto":false' && [ "$(printf '%s\n' "$o" | head -1 | cut -c1)" = '{' ] && [ "$(cfg_branch)" = dev ] && [ "$(cfg_mark)" = 1 ] \
  && ok "--update-check с KASKAD_BRANCH=\"k3\": ответ про dev, JSON первой строкой (без строки перехода), отметка 1" || bad "--update-check и переход" "$o"
use_root mv-peek; printf 'GITHUB_PAT=""\nKASKAD_BRANCH="k3"\n' > "$KASKAD_DIR/config"; cp "$KASKAD_DIR/config" "$W/cfg.before"
mk_version "$NEWER" "$FAKE_DIR/gh/dev.sh"; mk_version "$CUR" "$FAKE_DIR/gh/k3.sh"
o=$(lib t_peek 2>&1 | strip)
[ "$(UJ)" = "$NEWER dev" ] && cmp -s "$KASKAD_DIR/config" "$W/cfg.before" \
  && [ "$(grep -o 'kaskad-pro/[a-z0-9]*/install.sh' "$CALLS/curl.log" | cut -d/ -f2 | tr '\n' ' ')" = "dev " ] \
  && ok "--update-check-bg с k3 без отметки: смотрит dev, настройки не меняет" || bad "фоновая проверка и k3" "$o | $(UJ) | $(cat "$CALLS/curl.log")"
body=$(sed -n '/^k3_post_update() {/,/^}/p' "$S")
printf '%s\n' "$body" | sed -n '2,4p' | grep -q 'k3_branch_to_dev quiet' && ok "--k3-post-update: переход k3 → dev первым делом, тихо" || bad "post-update без перехода"
body=$(sed -n '/^run_startup() {/,/^}/p' "$S")
[ "$(printf '%s\n' "$body" | grep -n 'k3_remember_branch auto' | cut -d: -f1)" -lt "$(printf '%s\n' "$body" | grep -n '^    k3_branch_to_dev ' | cut -d: -f1)" ] \
  && printf '%s\n' "$body" | grep -q 'K3_BRANCH_NOTE=.*K3_BRANCH_NOTE' \
  && ok "run_startup: переход после записи ветки, строка — вместе с остальными строками о ветке" || bad "run_startup без перехода"
# Старый код (3.3.3, KASKAD_BRANCH="k3") обновляется на этот файл, выложенный в k3: меню 11 старого кода его
# принимает, а --k3-post-update нового файла переводит ветку на dev. Старый install.sh — K3_OLD_INSTALL (git show 6a6c410).
if [ -n "${K3_OLD_INSTALL:-}" ] && [ -f "$K3_OLD_INSTALL" ]; then
    su_setup "" "" mv-old; cp "$K3_OLD_INSTALL" "$C"; echo 'KASKAD_BRANCH="k3"' >> "$KASKAD_DIR/config"; cp "$S" "$FAKE_DIR/gh/k3.sh"
    o=$( (S="$K3_OLD_INSTALL"; lib t_su) 2>&1 | strip)
    [ "$(ver_of "$C")" = "$CUR" ] && [ "$(cfg_branch)" = dev ] && [ "$(cfg_mark)" = 1 ] && printf '%s' "$o" | grep -q "Обновлено до v$CUR (ветка k3)" \
      && ok "старый $(ver_of "$K3_OLD_INSTALL") с k3: меню 11 ставит $CUR из k3, post-update нового файла — ветка dev, отметка 1" \
      || bad "старый код → новый" "$o | $(cat "$KASKAD_DIR/config")"
    # REV-334 R1: старый 3.3.3 без записанной ветки — его цепочка main → test → k3 ставит новый файл из k3 и пишет k3
    # (после --k3-post-update нового файла); следующий запуск меню переводит на dev одной строкой
    su_setup 2.13 3.1.0 mv-old-chain; cp "$K3_OLD_INSTALL" "$C"; cp "$S" "$FAKE_DIR/gh/k3.sh"
    o=$( (S="$K3_OLD_INSTALL"; lib t_su) 2>&1 | strip)
    t_start_br() { init_config >/dev/null 2>&1; k3_remember_branch; k3_branch_to_dev; printf 'NOTE=%s\nLINE=%s\n' "$K3_BRANCH_NOTE" "$K3_BRANCH_DEV_LINE"; }
    o2=$(lib t_start_br 2>&1)
    [ "$(ver_of "$C")" = "$CUR" ] && printf '%s' "$o" | grep -q "Обновлено до v$CUR (ветка k3)" \
      && [ "$(cfg_branch)" = dev ] && [ "$(cfg_mark)" = 2 ] && printf '%s' "$o2" | grep -qx 'NOTE=' \
      && printf '%s' "$o2" | grep -qx 'LINE=Ветка обновлений: k3 → dev — сборки Kaskad 3 теперь выходят в dev, записано в настройки' \
      && ok "старый $(ver_of "$K3_OLD_INSTALL") без ветки: цепочка до k3 ставит $CUR, следующий запуск меню — dev, одна строка" \
      || bad "старый код без ветки → новый" "$o | $o2 | $(cat "$KASKAD_DIR/config")"
    # 3.3.4, миграция allowInsecure: у старого 3.3.3 в пуле ключ allowInsecure=1 без pcs к серверу с самоподписанным
    # сертификатом (здесь — TLS на 127.0.0.1). Обновление (меню 11 старого кода → --k3-post-update нового файла) снимает
    # отпечаток сертификата один раз: строка пула прежняя, настройки нового разборщика — с pinnedPeerCertSha256, выход жив.
    # Второй ключ — сервер не отвечает: остаётся в пуле с предупреждением, остальной пул собирается.
    if command -v openssl >/dev/null 2>&1; then
        openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=test.local -keyout "$W/tofu.key" -out "$W/tofu.crt" >/dev/null 2>&1
        tsha=$(openssl x509 -in "$W/tofu.crt" -outform DER 2>/dev/null | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')
        cat > "$W/tofu_srv.py" <<'PY'
import socket, ssl, sys
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(sys.argv[1], sys.argv[2])
s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(8)
d = socket.socket(); d.bind(("127.0.0.1", 0))                     # занят, но не слушает: «сервер не отвечает»
open(sys.argv[3], "w").write("%d %d\n" % (s.getsockname()[1], d.getsockname()[1]))
s.settimeout(120)
while True:
    try: c, _ = s.accept()
    except OSError: break
    try:
        c.settimeout(5)
        with ctx.wrap_socket(c, server_side=True) as t: t.recv(1)
    except (OSError, ssl.SSLError): pass
    finally: c.close()
PY
        rm -f "$W/tofu.port"; python3 "$W/tofu_srv.py" "$W/tofu.crt" "$W/tofu.key" "$W/tofu.port" & tpid=$!
        for _ in $(seq 1 50); do [ -s "$W/tofu.port" ] && [ "$(wc -w < "$W/tofu.port")" = 2 ] && break; python3 -c 'import time; time.sleep(0.1)'; done
        read -r tport tdead < "$W/tofu.port"
        su_setup "" "" mv-old-tofu; cp "$K3_OLD_INSTALL" "$C"; echo 'KASKAD_BRANCH="k3"' >> "$KASKAD_DIR/config"; cp "$S" "$FAKE_DIR/gh/k3.sh"
        mkdir -p "$KASKAD_DIR/egress"; chmod 700 "$KASKAD_DIR/egress"
        TK1="vless://aaaaaaaa-1111-2222-3333-444444444444@127.0.0.1:$tport?type=tcp&security=tls&sni=test.local&allowInsecure=1#Marzban"
        TK2="vless://bbbbbbbb-1111-2222-3333-444444444444@127.0.0.1:$tdead?type=tcp&security=tls&sni=test.local&allowInsecure=1#Dead"
        TKR='vless://22222222-3333-4444-5555-666666666666@198.51.100.21:443?type=tcp&security=reality&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com&fp=chrome#Helsinki'
        printf '%s\n%s\n%s\n' "$TK1" "$TKR" "$TK2" > "$KASKAD_DIR/egress/upstream.txt"; chmod 600 "$KASKAD_DIR/egress/upstream.txt"
        cp "$KASKAD_DIR/egress/upstream.txt" "$W/tofu.pool"
        # 3.3.6 круг 3 (SEC-336-awgaddr-2 Б-3): сервер VLESS на 127.0.0.1 разборщик отвергает (self_ep). TLS-сервер этой
        # проверки — на петле, поэтому python3 блока зовёт разборщик через tests/parser_loopback.py (пропуск IP
        # петли 127.x); остальной python3 — как есть. В установщике пропуска нет.
        PYL="$W/pyloop"; RPY=$(command -v python3); mkdir -p "$PYL"
        printf '#!/usr/bin/env bash\ncase "${1:-}" in */egress_parse.py) exec "%s" "%s" "$@" ;; esac\nexec "%s" "$@"\n' \
            "$RPY" "$ROOT_DIR/tests/parser_loopback.py" "$RPY" > "$PYL/python3"; chmod 755 "$PYL/python3"
        PATH0="$PATH"; export PATH="$PYL:$PATH"                     # до конца этой проверки (ниже — снова PATH0)
        o=$( (S="$K3_OLD_INSTALL"; lib t_su) 2>&1 | strip)
        kill "$tpid" 2>/dev/null; wait "$tpid" 2>/dev/null
        EP="$KASKAD_DIR/egress/egress_parse.py"; PINS="$KASKAD_DIR/egress/tofu-pins.json"
        tcfg=$(python3 "$EP" config < "$KASKAD_DIR/egress/upstream.txt" 2>/dev/null)
        tprob=$(python3 "$EP" problems < "$KASKAD_DIR/egress/upstream.txt" 2>/dev/null)
        [ "$(ver_of "$C")" = "$CUR" ] && cmp -s "$KASKAD_DIR/egress/upstream.txt" "$W/tofu.pool" && [ -n "$tsha" ] \
          && printf '%s' "$tcfg" | grep -q "\"pinnedPeerCertSha256\": \"$tsha\"" && ! printf '%s' "$tcfg" | grep -q allowInsecure \
          && [ "$(printf '%s' "$tcfg" | grep -c '"protocol": "vless"')" = 2 ] && [ "$tprob" = "$(printf '3\ttofu_wait')" ] \
          && [ -e "$KASKAD_DIR/egress/.tofu-done" ] && [ "$(python3 -c 'import os,sys; print("%o" % (os.stat(sys.argv[1]).st_mode & 0o777))' "$PINS")" = 600 ] \
          && ok "старый $(ver_of "$K3_OLD_INSTALL") с ключом allowInsecure → $CUR: отпечаток снят при обновлении, строка пула прежняя, выход жив (pinnedPeerCertSha256)" \
          || bad "миграция allowInsecure" "$o | cfg: $(printf '%s' "$tcfg" | grep -c vless) | prob: $tprob | $(cat "$PINS" 2>&1 | tr -d '\n' | cut -c1-200)"
        tlog=$(cat "$KASKAD_ROOT/var/log/kaskad.log" 2>/dev/null)
        printf '%s' "$tlog" | grep -q "Ключ без проверки сертификата (127.0.0.1:$tport): отпечаток сертификата снят и закреплён автоматически" \
          && printf '%s' "$tlog" | grep -q "Ключ без проверки сертификата (127.0.0.1:$tdead): сервер не ответил — отпечаток не снят, выход пока не работает. Kaskad повторит сам" \
          && ! printf '%s' "$tlog" | grep -q 'aaaaaaaa-1111' && ! printf '%s' "$tlog" | grep -q "$tsha" \
          && python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); s=d["servers"]["127.0.0.1:%s|test.local" % sys.argv[2]]; sys.exit(0 if s["auto"] and "127.0.0.1:%s|test.local" % sys.argv[3] in d["pending"] else 1)' "$PINS" "$tport" "$tdead" \
          && ok "миграция allowInsecure: в журнале — сервер (без ключа и отпечатка); снят — «автоматически», не ответил — отложен с предупреждением" \
          || bad "миграция allowInsecure: журнал и отметки" "$tlog | $(cat "$PINS" 2>&1 | tr -d '\n' | cut -c1-300)"
        t_tofu_warn() { egress_awg_warnings; }
        o=$(lib t_tofu_warn 2>&1 | strip)
        printf '%s' "$o" | grep -q 'Ключ #3: VLESS без проверки сертификата (allowInsecure) — сервер не ответил при обновлении, выход пока не работает. Kaskad повторит сам, не чаще раза в час' \
          && ! printf '%s' "$o" | grep -q 'Ключ #1' \
          && ok "миграция allowInsecure: предупреждение меню — только про ключ, чей сервер не ответил" || bad "миграция allowInsecure: предупреждение" "$o"
        # новый ключ allowInsecure после первого прохода не закрепляется (их отклоняет разборщик), отложенный — не чаще раза в час
        t_tofu_again() { egress_tofu_snap; echo "NEW=${EGRESS_TOFU_NEW:-}"; }
        cp "$PINS" "$W/tofu.pins.before"
        printf '%s\n' "${TK1/aaaaaaaa/cccccccc}" | sed "s/127.0.0.1:$tport/127.0.0.2:$tport/" >> "$KASKAD_DIR/egress/upstream.txt"
        o=$(lib t_tofu_again 2>&1 | strip)
        cmp -s "$PINS" "$W/tofu.pins.before" && printf '%s' "$o" | grep -qx 'NEW=' \
          && ok "миграция allowInsecure: новый ключ после первого прохода не закрепляется, отложенный в течение часа не повторяется" \
          || bad "миграция allowInsecure: повтор" "$o"
        # час прошёл: отложенный повторяется (сервер так и не отвечает), новый ключ — нет; попытка сдвигает отметку
        python3 - "$PINS" <<'PY'
import json, os, sys, time
d = json.load(open(sys.argv[1]))
for r in d["pending"].values():
    r["try"] -= 7200; r["since"] -= 7200
json.dump(d, open(sys.argv[1], "w"))
t = time.time() - 7200; os.utime(sys.argv[1], (t, t))
PY
        t_tofu_due() { egress_tofu_due && echo DUE || echo NODUE; egress_tofu_snap; echo "NEW=${EGRESS_TOFU_NEW:-}"; egress_tofu_due && echo DUE2 || echo NODUE2; }
        o=$(lib t_tofu_due 2>&1 | strip)
        printf '%s' "$o" | grep -qx DUE && printf '%s' "$o" | grep -qx NODUE2 && printf '%s' "$o" | grep -qx 'NEW=' \
          && python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); p=d["pending"]; sys.exit(0 if list(p)==["127.0.0.1:%s|test.local" % sys.argv[2]] and p[list(p)[0]]["n"]==2 and "127.0.0.2:%s|test.local" % sys.argv[3] not in d["servers"] else 1)' "$PINS" "$tdead" "$tport" \
          && ok "миграция allowInsecure: через час отложенный повторён (n=2), новый ключ не тронут, следующий — через час" \
          || bad "миграция allowInsecure: повтор через час" "$o | $(cat "$PINS" 2>&1 | tr -d '\n' | cut -c1-300)"
        # первый проход сделан, отложенных нет, пришёл новый небезопасный ключ — ни python, ни сети, ничего не закреплено
        python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d.pop("pending",None); json.dump(d,open(sys.argv[1],"w"))' "$PINS"
        python3 -c 'import os,sys,time; t=time.time()-7200; os.utime(sys.argv[1], (t, t))' "$PINS"
        cp "$PINS" "$W/tofu.pins.before"
        printf '%s\n' "${TK1/aaaaaaaa/dddddddd}" | sed "s/127.0.0.1:$tport/127.0.0.3:$tport/" >> "$KASKAD_DIR/egress/upstream.txt"
        t_tofu_new() { egress_tofu_due && echo DUE || echo NODUE; egress_tofu_snap; echo "NEW=${EGRESS_TOFU_NEW:-}"; }
        o=$(lib t_tofu_new 2>&1 | strip)
        cmp -s "$PINS" "$W/tofu.pins.before" && printf '%s' "$o" | grep -qx NODUE && printf '%s' "$o" | grep -qx 'NEW=' \
          && [ "$(python3 "$EP" problems < "$KASKAD_DIR/egress/upstream.txt" 2>/dev/null | tail -n 2 | cut -f2 | tr '\n' ' ')" = "insecure insecure " ] \
          && ok "миграция allowInsecure: первый проход сделан, отложенных нет — новый ключ allowInsecure не закрепляется и снятия не вызывает" \
          || bad "миграция allowInsecure: новый ключ без отложенных" "$o | $(python3 "$EP" problems < "$KASKAD_DIR/egress/upstream.txt" 2>&1 | tr '\n' ' ')"
        export PATH="$PATH0"
    else
        printf '  SKIP миграция allowInsecure: нет openssl\n'
    fi
else
    printf '  SKIP старый код → новый: задайте K3_OLD_INSTALL=<install.sh 3.3.3>\n'
fi

# 3.3.4 (R-334-merge §5в №4) — весь путь повтора отложенных отпечатков (TOFU), без старого кода и без сети.
# --egress-tofu-kick (его зовут проба потерь и сборщик шлюза раз в час): отложенный ключ, последняя попытка больше
# часа назад — переход своим временным юнитом (systemd-run … --egress-xray-upgrade); попытка свежая — ничего;
# ключа того сервера в пуле нет — «pending» убирается, перехода нет.
su_setup "" "" tofu-kick
mkdir -p "$KASKAD_DIR/egress" "$K3_OPT/bin" "$W/kickbin"; chmod 700 "$KASKAD_DIR/egress"
printf '#!/bin/sh\nexit 0\n' > "$K3_OPT/bin/gokaskad"; chmod 755 "$K3_OPT/bin/gokaskad"
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s"\n' "$CALLS/systemd-run.log" > "$W/kickbin/systemd-run"; chmod 755 "$W/kickbin/systemd-run"
KW='vless://aaaaaaaa-1111-2222-3333-444444444444@198.51.100.21:443?type=tcp&security=tls&sni=a.example.com&allowInsecure=1#Wait'
KR='vless://22222222-3333-4444-5555-666666666666@198.51.100.22:443?type=tcp&security=reality&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com&fp=chrome#Reality'
PINS="$KASKAD_DIR/egress/tofu-pins.json"
tofu_pending() {   # $1 — секунд назад последняя попытка
    printf '{"v": 1, "servers": {}, "pending": {"198.51.100.21:443|a.example.com": {"try": 1, "n": 1, "since": 1}}}\n' > "$PINS"
    chmod 600 "$PINS"; python3 -c 'import os,sys,time; t=time.time()-int(sys.argv[2]); os.utime(sys.argv[1], (t, t))' "$PINS" "$1"
}
printf '%s\n%s\n' "$KW" "$KR" > "$KASKAD_DIR/egress/upstream.txt"; chmod 600 "$KASKAD_DIR/egress/upstream.txt"
: > "$KASKAD_DIR/egress/.tofu-done"; tofu_pending 7200; : > "$CALLS/systemd-run.log"
PATH="$W/kickbin:$PATH" bash "$S" --egress-tofu-kick </dev/null >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && grep -q -- "--unit=kaskad-xray-pin .*$K3_OPT/bin/gokaskad --egress-xray-upgrade" "$CALLS/systemd-run.log" \
  && ok "--egress-tofu-kick: отложенный ключ, попытка больше часа назад — переход своим юнитом (--egress-xray-upgrade)" \
  || bad "--egress-tofu-kick: перехода нет" "rc=$rc $(cat "$CALLS/systemd-run.log")"
tofu_pending 600; : > "$CALLS/systemd-run.log"
PATH="$W/kickbin:$PATH" bash "$S" --egress-tofu-kick </dev/null >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && [ ! -s "$CALLS/systemd-run.log" ] && grep -q '"pending"' "$PINS" \
  && ok "--egress-tofu-kick: попытка меньше часа назад — ничего не запускается" \
  || bad "--egress-tofu-kick: лишний переход" "rc=$rc $(cat "$CALLS/systemd-run.log")"
printf '%s\n' "$KR" > "$KASKAD_DIR/egress/upstream.txt"; tofu_pending 7200; : > "$CALLS/systemd-run.log"
PATH="$W/kickbin:$PATH" bash "$S" --egress-tofu-kick </dev/null >/dev/null 2>&1; rc=$?
[ "$rc" = 0 ] && [ ! -s "$CALLS/systemd-run.log" ] && ! grep -q '"pending"' "$PINS" \
  && [ -n "$(find "$PINS" -mmin +60 2>/dev/null)" ] \
  && ok "--egress-tofu-kick: ключ удалён из пула — его «pending» убран, перехода нет, время файла прежнее" \
  || bad "--egress-tofu-kick: удалённый отложенный ключ" "rc=$rc $(cat "$PINS") | $(cat "$CALLS/systemd-run.log")"
# Переход (--egress-xray-upgrade) снял отложенный отпечаток, xray уже тот: служба бота есть — пересборка пула (в ней
# gw apply); только шлюз, службы бота нет — gw apply (выходы шлюза разборщик соберёт уже с отпечатком).
t_up_tofu() {
    egress_ensure_parser() { :; }; egress_tofu_snap() { EGRESS_TOFU_NEW=${T_NEW:-}; }; egress_ensure_xray() { :; }
    egress_xray_follow() { EGRESS_XRAY_REBUILT=""; }; egress_xray_gw_stale() { return 1; }
    egress_rebuild() { echo REBUILD >> "$CALLS/egress.log"; }      # вывод пересборки переход глушит
    k3_engine() { echo nft; }; k3_gw_enabled() { return 0; }; k3_lib_ok() { return 0; }
    k3ctl() { printf '%s\n' "$*" >> "$CALLS/kaskadctl.log"; }
    [ -z "${T_UNIT:-}" ] || : > "$K3_UNIT_DIR/${EGRESS_SERVICE}.service"
    : > "$CALLS/egress.log"; egress_xray_upgrade; echo "RB=${EGRESS_XRAY_REBUILT:-}"; cat "$CALLS/egress.log"
}
: > "$CALLS/kaskadctl.log"; rm -f "$U/kaskad-egress.service"
o=$(T_NEW=1 lib t_up_tofu 2>&1 | strip)
[ "$(ctl_log)" = "gw apply" ] && ! printf '%s' "$o" | grep -q REBUILD && printf '%s' "$o" | grep -qx 'RB=1' \
  && ok "переход: отпечаток снят, службы бота нет (только шлюз) — gw apply, без пересборки пула" \
  || bad "переход: ветка gw apply" "$o | $(ctl_log)"
: > "$CALLS/kaskadctl.log"
o=$(T_NEW=1 T_UNIT=1 lib t_up_tofu 2>&1 | strip)
[ ! -s "$CALLS/kaskadctl.log" ] && printf '%s' "$o" | grep -qx REBUILD && printf '%s' "$o" | grep -qx 'RB=1' \
  && ok "переход: отпечаток снят, служба бота есть — пересборка пула (gw apply в ней)" \
  || bad "переход: ветка пересборки" "$o | $(ctl_log)"
rm -f "$U/kaskad-egress.service"; : > "$CALLS/kaskadctl.log"
o=$(lib t_up_tofu 2>&1 | strip)
[ ! -s "$CALLS/kaskadctl.log" ] && ! printf '%s' "$o" | grep -q REBUILD \
  && ok "переход: ничего не снято — ни пересборки, ни gw apply" || bad "переход: лишнее без снятия" "$o | $(ctl_log)"
# Пропущенный ключ, чей сервер не ответил при обновлении (код tofu_wait): меню называет причину, панели — insecure.
t_skip_wait() {
    printf 'K3SKIP\ttofu_wait\tWait\nK3SKIP\tinsecure\tNew\n' > "$EGRESS_DIR/.extract.skip"
    EGRESS_MARKS=1 egress_skip_report; echo "N=$EGRESS_SKIPPED CODE=$EGRESS_SKIP_CODE WHY=$EGRESS_SKIP_WHY"
}
o=$(K3_MARKS=1 lib t_skip_wait 2>&1 | strip); o1=$(printf '%s' "$o" | tr '\n' ' ' | tr -s ' ')
printf '%s' "$o1" | grep -q 'Пропущен ключ «Wait»: в нём выключена проверка сертификата (allowInsecure), а его сервер не ответил при обновлении' \
  && printf '%s' "$o1" | grep -q 'Пропущен ключ «New»: в нём выключена проверка сертификата (allowInsecure), а отпечатка' \
  && printf '%s' "$o" | grep -qx 'N=2 CODE=insecure WHY=без проверки сертификата и без отпечатка pcs; без проверки сертификата, сервер не ответил при обновлении' \
  && printf '%s' "$o" | grep -qx '@K3:egress_warn:skipped:insecure:2' && ! printf '%s' "$o" | grep -q 'tofu_wait' \
  && ok "пропуск ключа с отложенным сервером: меню — «сервер не ответил при обновлении», панели — insecure" \
  || bad "пропуск ключа tofu_wait" "$o"

echo "── 9. запуск меню: решение о движке (migrate scan) ──"
t_start() {
    migrate_dnat_comments() { echo "LEGACY-STARTUP"; }
    adopt_free_kaskad_rules() { :; }
    k3_legacy_deps() { echo "LEGACY-DEPS ${1:-min}"; }
    init_config >/dev/null 2>&1
    k3_startup_engine
}
# scan <supported> <reason> <kaskad_rules> <has_work> [persisted_lines] [warnings] [import] [blocked] — отчёт
# migrate scan --json (форма как у WP2: engine, support{supported,reason,code}, summary{kaskad_rules,
# import, persisted_lines, warnings, has_work, blocked, …}).
scan() {
    printf '{"engine": "%s", "edition": "pro", "version": "2.13", "support": {"supported": %s, "reason": "%s", "code": 0}, "summary": {"mode": "switch", "kaskad_rules": %s, "import": %s, "persisted_lines": %s, "warnings": %s, "has_work": %s, "blocked": %s}}\n' \
        "$(grep -q '"engine": "nft"' "$KASKAD_DIR/state.json" 2>/dev/null && echo nft || echo legacy)" "$1" "$2" "$3" "${7:-$3}" \
        "${5:-0}" "${6:-0}" "$4" "${8:-false}" > "$FAKE_DIR/scan.json"
}
prog_ready() { cp "$S" "$KASKAD_ROOT/usr/local/bin/gokaskad"; cp "$S" "$K3_OPT/bin/gokaskad"; }
use_root start1; fake_ctl; prog_ready; scan true "" 0 true
o=$(lib t_start </dev/null 2>&1 | strip)
ctl_log | grep -qx 'migrate apply --yes --json' && grep -q '"engine": "nft"' "$KASKAD_DIR/state.json" && ! printf '%s' "$o" | grep -q LEGACY \
  && ok "правил Kaskad нет (summary.kaskad_rules = 0), nft есть → migrate apply молча (свежая установка сразу на nftables)" || bad "тихий переезд" "$o | $(ctl_log)"
# Правил Kaskad в iptables нет, но есть что показать — не молча, а предпросмотр (critic #2: правила,
# которые остались только в rules.v4, тихий переезд стёр бы, а 2.x вернул бы их после перезагрузки).
for c in "12 0 3:правила Kaskad только в rules.v4" "0 1 0:есть предупреждение" "0 0 2:есть что восстановить" \
         "0 0 0 true:переезд заблокирован"; do
    set -- ${c%%:*}
    use_root start1s; fake_ctl; prog_ready; scan true "" 0 true "$1" "$2" "$3" "${4:-false}"
    o=$(printf '2\n' | lib t_start 2>&1 | strip)
    ctl_log | grep -qx 'migrate plan' && ! ctl_log | grep -q 'migrate apply' && printf '%s' "$o" | grep -q '1) Перевести сейчас' \
      && ok "правил Kaskad в iptables нет, но ${c#*:} → предпросмотр и вопрос, не молча" || bad "тихий переезд: ${c#*:}" "$o | $(ctl_log)"
done
use_root start1b; fake_ctl; prog_ready; scan true "" 0 true; echo 5 > "$FAKE_DIR/apply_rc"
printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> "$CALLS/kaskadctl.log"\ncase "$1 $2" in\n "version "*) echo ok ;;\n "migrate scan") cat "$FAKE_DIR/scan.json" ;;\n "migrate apply") echo "{\\"ok\\": false, \\"error\\": \\"нет nft\\", \\"code\\": 5}"; exit 5 ;;\nesac\n' > "$K3_OPT/bin/kaskadctl"
o=$(lib t_start </dev/null 2>&1 | strip)
printf '%s' "$o" | grep -q 'Новый движок не включился: нет nft — пробросы работают по-старому' && printf '%s' "$o" | grep -q LEGACY-STARTUP \
  && ok "тихий переезд не прошёл → причина одной строкой, всё как 2.13" || bad "сбой тихого переезда" "$o"
use_root start1c; fake_ctl; scan true "" 0 true; cp "$S" "$KASKAD_ROOT/usr/local/bin/gokaskad"     # копии в /opt нет
o=$(lib t_start </dev/null 2>&1 | strip)
! ctl_log | grep -q 'migrate apply' && printf '%s' "$o" | grep -q 'установлен не целиком: нет копии программы для служб' && printf '%s' "$o" | grep -q LEGACY-STARTUP \
  && ok "программа не из файла (нет копии в /opt) → переезд отложен, всё как 2.13" || bad "переезд без программы 3.x" "$o | $(ctl_log)"
use_root start2; fake_ctl; prog_ready; scan true "" 1 true
o=$(printf '2\n' | lib t_start 2>&1 | strip)
ctl_log | grep -qx 'migrate plan' && ! ctl_log | grep -q 'migrate apply' && printf '%s' "$o" | grep -q 'ПЛАН: перенесу 1 проброс' \
  && printf '%s' "$o" | grep -q '1) Перевести сейчас  2) Позже  3) Не спрашивать больше' && printf '%s' "$o" | grep -q LEGACY-STARTUP \
  && ok "есть что переносить → предпросмотр и вопрос; «Позже» — всё как 2.13" || bad "предпросмотр/позже" "$o"
printf '%s' "$o" | grep -q 'LEGACY-DEPS full' && bad "на поддерживаемой системе ставится iptables-persistent" || ok "на поддерживаемой системе iptables-persistent не ставится (он удалил бы ufw)"
use_root start3; fake_ctl; prog_ready; scan true "" 1 true
o=$(printf '3\n' | lib t_start 2>&1 | strip)
grep -q '^K3_MIGRATE_ASK="0"' "$KASKAD_DIR/config" && printf '%s' "$o" | grep -q LEGACY-STARTUP && ok "«Не спрашивать больше» → K3_MIGRATE_ASK=\"0\" в config" || bad "не спрашивать" "$(cat "$KASKAD_DIR/config")"
: > "$CALLS/kaskadctl.log"; o=$(lib t_start </dev/null 2>&1 | strip)
! ctl_log | grep -qE 'migrate (plan|apply)' && printf '%s' "$o" | grep -q LEGACY-STARTUP && ok "K3_MIGRATE_ASK=0 — больше не спрашивает и сам не переводит" || bad "K3_MIGRATE_ASK=0" "$(ctl_log)"
# «Не спрашивать», но пробросы прошлой настройки пропали (вернули iptables, перезагрузка без
# netfilter-persistent): одна строка с подсказкой, без вопроса и без переезда (стенд, 2026-10-04).
use_root start3r; fake_ctl; prog_ready; echo 'K3_MIGRATE_ASK="0"' >> "$KASKAD_DIR/config"
printf '{"engine": "legacy", "edition": "pro", "version": "3.0.1", "support": {"supported": true, "reason": "", "code": 0}, "summary": {"mode": "switch", "kaskad_rules": 0, "import": 3, "state_restore": 3, "persisted_lines": 0, "warnings": 0, "has_work": true, "blocked": false}}\n' > "$FAKE_DIR/scan.json"
o=$(lib t_start </dev/null 2>&1 | strip)
printf '%s' "$o" | grep -q 'Пробросов из прошлой настройки нет в iptables: 3' && printf '%s' "$o" | grep -q 'Вернуть: меню 16 → 1' \
  && ! ctl_log | grep -qE 'migrate (plan|apply)' && printf '%s' "$o" | grep -q LEGACY-STARTUP \
  && ok "K3_MIGRATE_ASK=0, пробросы прошлой настройки пропали → одна строка «меню 16 → 1», без вопроса" || bad "пропавшие пробросы при K3_MIGRATE_ASK=0" "$o | $(ctl_log)"
use_root start4; fake_ctl; prog_ready; scan true "" 1 true; echo kaskad-bot > "$FAKE_DIR/active"
o=$(printf '1\n\n' | lib t_start 2>&1 | strip)
ctl_log | grep -qx 'migrate apply --yes' && grep -q '"engine": "nft"' "$KASKAD_DIR/state.json" && ! printf '%s' "$o" | grep -q LEGACY-STARTUP \
  && grep -qx 'restart kaskad-bot' "$CALLS/systemctl.log" && ok "«Перевести сейчас» → migrate apply --yes, бот перезапущен на новом движке" || bad "перевести сейчас" "$o"
use_root start4b; fake_ctl; prog_ready; scan true "" 1 true; echo 3 > "$FAKE_DIR/apply_rc"
o=$(printf '1\n1\n\n' | K3_TEST_TTY=1 lib t_start 2>&1 | strip)
ctl_log | grep -qx 'migrate apply --yes' && ctl_log | grep -qx 'migrate apply --yes --force' && ok "конфликт при переезде (код 3) → «Всё равно перевести?» → 1 → --force" || bad "переезд с --force" "$(ctl_log)"
printf '%s' "$o" | grep -q '1) Перевести всё равно' && printf '%s' "$o" | grep -q '2) Отмена' \
  && ok "текст вопроса: «Всё равно перевести?», пункты цифрами" || bad "нет вопроса «Всё равно перевести?»" "$o"
for c in 4:'Проверка не прошла — вернул как было' 5:'Новый движок здесь недоступен' 6:'Kaskad уже что-то меняет, повторите через минуту'; do
    use_root "start4c${c%%:*}"; fake_ctl; prog_ready; scan true "" 1 true; echo "${c%%:*}" > "$FAKE_DIR/apply_rc"
    o=$(printf '1\n\n' | lib t_start 2>&1 | strip)
    printf '%s' "$o" | grep -q "${c#*:}" && printf '%s' "$o" | grep -q LEGACY-STARTUP && ok "переезд, код ${c%%:*} → «${c#*:}», дальше как 2.13" || bad "код ${c%%:*}" "$o"
done
# Код 1, а движок уже nft (автооткат не вернул всё): запуск 2.13 (перенос правил в iptables) нельзя.
use_root start4d; fake_ctl; prog_ready; scan true "" 1 true; echo 1 > "$FAKE_DIR/apply_rc"; touch "$FAKE_DIR/apply_sets_nft"
o=$(printf '1\n\n' | lib t_start 2>&1 | strip)
printf '%s' "$o" | grep -q 'но пробросы уже работают через nftables' && ! printf '%s' "$o" | grep -q LEGACY-STARTUP \
  && ok "переезд с кодом 1, но движок уже nft → честно, и без запуска 2.13" || bad "код 1 при движке nft" "$o"
use_root start4e; fake_ctl; prog_ready; scan true "" 0 true; echo 1 > "$FAKE_DIR/apply_rc"; touch "$FAKE_DIR/apply_sets_nft"
o=$(lib t_start </dev/null 2>&1 | strip)
printf '%s' "$o" | grep -q 'Переезд завершился с ошибкой' && printf '%s' "$o" | grep -q 'через nftables' && ! printf '%s' "$o" | grep -q LEGACY-STARTUP \
  && ok "тихий переезд: код 1, движок nft → без запуска 2.13" || bad "тихий переезд, код 1 при nft" "$o"
use_root start5; fake_ctl; prog_ready; scan false "Ubuntu 22.04: новый движок — с Ubuntu 24.04" 1 true
o=$(lib t_start </dev/null 2>&1 | strip)
printf '%s' "$o" | grep -q 'Новый движок здесь недоступен: Ubuntu 22.04: новый движок — с Ubuntu 24.04. Пробросы работают по-старому.' \
  && printf '%s' "$o" | grep -q 'LEGACY-DEPS full' && printf '%s' "$o" | grep -q LEGACY-STARTUP && ! ctl_log | grep -q 'migrate apply' \
  && ok "nft не поддерживается → одна строка с причиной, пакеты и запуск как 2.13" || bad "неподдерживаемая система" "$o"
t_line() { k3_unsupported_line "$1" "${2:-}"; }
l=$(lib t_line "Новый движок пробросов — на Debian 12+ и Ubuntu 24.04+; здесь неизвестная система. Пробросы работают по-старому." os)
[ "$l" = "Новый движок пробросов — на Debian 12+ и Ubuntu 24.04+; здесь неизвестная система. Пробросы работают по-старому." ] \
  && ok "причина-фраза от kaskadctl (код os) — как есть, без повторов; по словам не судим" || bad "строка недоступности" "$l"
l=$(lib t_line "нет nft"); [ "$l" = "Новый движок здесь недоступен: нет nft. Пробросы работают по-старому." ] && ok "короткая причина — «Новый движок здесь недоступен: нет nft. …»" || bad "короткая причина" "$l"
use_root start6; fake_ctl; prog_ready; engine_nft; scan true "" 0 false
o=$(lib t_start </dev/null 2>&1 | strip)
! ctl_log | grep -qE 'migrate (plan|apply)' && [ -z "$o" ] && ok "движок nft, дозабирать нечего (has_work false) → молча" || bad "nft без работы" "$o | $(ctl_log)"
scan true "" 1 true
o=$(printf '2\n' | lib t_start 2>&1 | strip)
printf '%s' "$o" | grep -q 'В iptables появились правила Kaskad: 1' && printf '%s' "$o" | grep -q '1) Забрать сейчас  2) Позже  3) Не спрашивать больше' && ! ctl_log | grep -q 'migrate apply' \
  && ! printf '%s' "$o" | grep -q LEGACY && ok "движок nft, в iptables новые правила Kaskad → предложение дозабрать (с «Не спрашивать больше»)" || bad "дозабрать" "$o"
o=$(printf '1\n\n' | lib t_start 2>&1 | strip)
ctl_log | grep -qx 'migrate apply --yes' && ok "«Забрать сейчас» → тот же migrate apply" || bad "дозабрать: apply" "$(ctl_log)"
# critic #15a: заблокированный план не предлагается; «Не спрашивать больше» — свой ключ в config
scan true "" 1 true 0 0 1 true; : > "$CALLS/kaskadctl.log"
o=$(printf '1\n\n' | lib t_start 2>&1 | strip)
! ctl_log | grep -qE 'migrate (plan|apply)' && [ -z "$o" ] && ok "дозабрать нельзя (план заблокирован) → не спрашивает" || bad "вопрос при блокере" "$o | $(ctl_log)"
scan true "" 1 true; : > "$CALLS/kaskadctl.log"
o=$(printf '3\n' | lib t_start 2>&1 | strip)
grep -q '^K3_ADOPT_ASK="0"' "$KASKAD_DIR/config" && ! grep -q '^K3_MIGRATE_ASK=' "$KASKAD_DIR/config" && ! ctl_log | grep -q 'migrate apply' \
  && printf '%s' "$o" | grep -q 'Больше не спрошу' && ok "«Не спрашивать больше» → K3_ADOPT_ASK=\"0\" (свой ключ, не K3_MIGRATE_ASK)" || bad "не спрашивать (дозабрать)" "$(cat "$KASKAD_DIR/config")"
: > "$CALLS/kaskadctl.log"; o=$(lib t_start </dev/null 2>&1 | strip)
! ctl_log | grep -qE 'migrate (plan|apply)' && [ -z "$o" ] && ok "K3_ADOPT_ASK=0 — больше не спрашивает" || bad "K3_ADOPT_ASK=0" "$o | $(ctl_log)"
# critic #1c: движок nft, а таблицы нет / службы не включены — починка при запуске меню, одной строкой
use_root start6h; fake_ctl; prog_ready; engine_nft
printf '%s\n' '{"engine": "nft", "support": {"supported": true, "reason": "", "code": ""}, "engine_health": ["таблица nftables «kaskad» не загружена", "служба kaskad-net.service не включена"], "summary": {"mode": "adopt", "kaskad_rules": 0, "import": 0, "has_work": false, "blocked": false}}' > "$FAKE_DIR/scan.json"
o=$(lib t_start </dev/null 2>&1 | strip)
[ "$(ctl_log | grep -E '^net (apply|install-units)$' | tr '\n' ';')" = "net apply;net install-units;" ] \
  && printf '%s' "$o" | flat | grep -q 'Движок nftables был не в порядке (таблица nftables «kaskad» не загружена; служба kaskad-net.service не включена) — таблица загружена, службы движка включены.' \
  && ! ctl_log | grep -q 'migrate apply' && grep -q 'K3 heal: таблица' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "движок nft, таблицы нет и служба не включена → net apply + net install-units и одна строка" || bad "починка при запуске" "$o | $(ctl_log)"
: > "$CALLS/kaskadctl.log"; echo 1 > "$FAKE_DIR/net_apply_rc"
o=$(lib t_start </dev/null 2>&1 | strip)
printf '%s' "$o" | grep -q 'и починить не вышло — меню 16' && ! ctl_log | grep -q 'net install-units' \
  && ok "починить не вышло → честная строка, без установки служб" || bad "неудачная починка" "$o | $(ctl_log)"
use_root start6i; fake_ctl; prog_ready; engine_nft; scan true "" 0 false
o=$(lib t_start </dev/null 2>&1 | strip)
! ctl_log | grep -qE '^net (apply|install-units)' && [ -z "$o" ] && ok "движок nft в порядке → ничего не чинит" || bad "лишняя починка" "$o | $(ctl_log)"
use_root start7; fake_ctl
o=$(lib t_start </dev/null 2>&1 | strip)
printf '%s' "$o" | grep -q 'проверка сервера не удалась' && printf '%s' "$o" | grep -q LEGACY-STARTUP && ok "scan не ответил → строка-предупреждение, всё как 2.13" || bad "сбой scan" "$o"
use_root start8
o=$(lib t_start </dev/null 2>&1 | strip)
printf '%s' "$o" | grep -q LEGACY-STARTUP && [ ! -s "$CALLS/kaskadctl.log" ] && ok "ядра нет (kaskadctl не запускается) → молча как 2.13" || bad "без ядра" "$o"

echo "── 10. меню 16 для новичка и «Вернуться на Kaskad 2.13» ──"
use_root m16; fake_ctl; engine_nft; prog_ready
t_m16() { clear() { :; }; k3_engine_menu; }
o=$(printf 'abc\n0\n' | lib t_m16 2>&1 | strip)
[ "$(printf '%s\n' "$o" | sed -n 2p)" = "Здесь ничего менять не нужно — 0 назад. Пункты ниже — для особых случаев." ] \
  && printf '%s' "$o" | grep -q '1) Перевести на nftables — уже сделано' && printf '%s' "$o" | grep -q '2) Вернуть iptables — пробросы снова как в Kaskad 2.x' \
  && printf '%s' "$o" | grep -q '4) Вернуться на Kaskad 2.13 — программа станет старой версией, шлюз (17) удаляется' \
  && printf '%s' "$o" | grep -q 'Нет такого пункта' && ! ctl_log | grep -q 'migrate\|rollback' \
  && ok "меню 16: первая строка — «Здесь ничего менять не нужно — 0 назад», у пунктов — последствия; неверный ввод — со словами" || bad "меню 16" "$o"
use_root m16free; fake_ctl; prog_ready
printf '{\n  "engine": "nft",\n  "forwards": [\n    {"proto": "tcp", "port": 443, "to": "2.2.2.2", "to_port": 443, "origin": "free"}\n  ],\n  "schema": 1\n}\n' > "$KASKAD_DIR/state.json"
o=$(printf '0\n' | lib t_m16 2>&1 | strip)
printf '%s' "$o" | grep -q '2) Вернуть iptables — пробросы снова в iptables, как в бесплатной версии' \
  && printf '%s' "$o" | grep -q '4) Поставить Kaskad PRO 2.13 — прежняя версия PRO вместо этой (не бесплатная)' && ! printf '%s' "$o" | grep -q 'Вернуться на Kaskad 2.13' \
  && ok "меню 16 после бесплатной версии: «Вернуть iptables, как в бесплатной версии», пункт 4 — «Поставить Kaskad PRO 2.13», а не «Вернуться»" || bad "меню 16: бесплатная" "$o"
ret_setup() {
    use_root "ret-$1"; fake_ctl; engine_nft
    C="$KASKAD_ROOT/usr/local/bin/gokaskad"; U="$KASKAD_ROOT/etc/systemd/system"
    cp "$S" "$C"; cp "$S" "$K3_OPT/bin/gokaskad"
    printf '[Service]\nExecStart=%s --bot-daemon\n' "$K3_OPT/bin/gokaskad" > "$U/kaskad-bot.service"
    printf '[Service]\nExecStartPre=-%s --egress-awg-up\n' "$K3_OPT/bin/gokaskad" > "$U/kaskad-egress.service"
    echo kaskad-bot > "$FAKE_DIR/active"
}
t_ret() { k3_return_to_2x; echo "RET=$?"; }
ret_setup snap
# Снимки как у WP2: <id>-<издание>-<версия>/files/usr/local/bin/gokaskad (+ .bak); список — migrate backups --json.
# Порядок в списке нарочно не совпадает с порядком имён каталогов: так видно, что берётся список.
B="$KASKAD_ROOT/var/backups/kaskad"; SN="$B/20261004T120000Z-pro-2.13"
mkdir -p "$SN/files/usr/local/bin" "$B/20261005T090000Z-pro-2.11/files/usr/local/bin" "$B/20261006T090000Z-k3-3.0.1/files/usr/local/bin"
mk_version 2.13 "$SN/files/usr/local/bin/gokaskad"
mk_version 2.11 "$B/20261005T090000Z-pro-2.11/files/usr/local/bin/gokaskad"
cp "$S" "$B/20261006T090000Z-k3-3.0.1/files/usr/local/bin/gokaskad"
printf '{"ok": true, "backups": [%s, %s, %s]}\n' \
  '{"path": "/var/backups/kaskad/20261006T090000Z-k3-3.0.1", "command_kind": "k3", "command_version": "3.0.1"}' \
  '{"path": "/var/backups/kaskad/20261004T120000Z-pro-2.13", "command_kind": "pro", "command_version": "2.13"}' \
  '{"path": "/var/backups/kaskad/20261005T090000Z-pro-2.11", "command_kind": "pro", "command_version": "2.11"}' > "$FAKE_DIR/backups.json"
o=$(printf '1\n\n' | lib t_ret 2>&1 | strip)
ctl_log | grep -qx 'migrate backups --json' && ctl_log | grep -qx 'migrate rollback --to-legacy --yes' && [ "$(ver_of "$C")" = "2.13" ] \
  && ok "migrate rollback --to-legacy, команда — 2.x из первого по списку migrate backups --json снимка с 2.x" || bad "возврат: программа" "$o | $(ctl_log)"
# Реальный снимок переезда: команда в нём уже 3.x, а 2.13 — в gokaskad.bak.
ret_setup snapbak; B="$KASKAD_ROOT/var/backups/kaskad"; mkdir -p "$B/20261005T090000Z-pro-2.13/files/usr/local/bin" "$B/20261006T090000Z-pro-2.11/files/usr/local/bin"
cp "$S" "$B/20261005T090000Z-pro-2.13/files/usr/local/bin/gokaskad"; mk_version 2.13 "$B/20261005T090000Z-pro-2.13/files/usr/local/bin/gokaskad.bak"
mk_version 2.11 "$B/20261006T090000Z-pro-2.11/files/usr/local/bin/gokaskad"       # в список не входит
printf '{"ok": true, "backups": [{"path": "/var/backups/kaskad/20261005T090000Z-pro-2.13", "command_kind": "k3", "command_version": "3.0.1"}]}\n' > "$FAKE_DIR/backups.json"
printf '1\n\n' | lib t_ret >/dev/null 2>&1
[ "$(ver_of "$C")" = "2.13" ] && ok "снимок переезда (команда 3.x, 2.13 в gokaskad.bak) — найден по списку" || bad "возврат из .bak снимка: $(ver_of "$C")"
ret_setup snap2; SN="$KASKAD_ROOT/var/backups/kaskad/20261004T120000Z-pro-2.13"; mkdir -p "$SN/files/usr/local/bin"
mk_version 2.13 "$SN/files/usr/local/bin/gokaskad"
o=$(printf '1\n\n' | lib t_ret 2>&1 | strip)
[ "$(ver_of "$C")" = "2.13" ] && ok "kaskadctl не дал списка — снимок найден по каталогам" || bad "возврат по каталогам: $(ver_of "$C")"
grep -qx "ExecStart=$C --bot-daemon" "$U/kaskad-bot.service" && grep -qx "ExecStartPre=-$C --egress-awg-up" "$U/kaskad-egress.service" \
  && ok "юниты снова на $C" || bad "юниты после возврата" "$(cat "$U/kaskad-bot.service")"
grep -qx 'daemon-reload' "$CALLS/systemctl.log" && grep -qx 'restart kaskad-bot' "$CALLS/systemctl.log" && ok "daemon-reload и перезапуск бота" || bad "systemctl после возврата"
[ -f "$K3_OPT/bin/gokaskad" ] && [ -d "$SN" ] && ! printf '%s' "$o" | grep -q 'RET=' && ok "/opt/kaskad и снимки на месте, меню 3.x завершилось" || bad "после возврата"
ret_setup prev; mkdir -p "$K3_OPT/prev"; mk_version 2.11 "$K3_OPT/prev/gokaskad-2.11"; mk_version 2.13 "$K3_OPT/prev/gokaskad-2.13"
printf '1\n\n' | lib t_ret >/dev/null 2>&1
[ "$(ver_of "$C")" = "2.13" ] && ok "снимка нет — новейшая 2.x из \$K3_OPT/prev" || bad "возврат из prev: $(ver_of "$C")"
ret_setup net; mk_version 2.13 "$FAKE_DIR/gh/v2.13.sh"
printf '1\n\n' | lib t_ret >/dev/null 2>&1
[ "$(ver_of "$C")" = "2.13" ] && grep -q 'kaskad-pro/v2.13/install.sh' "$CALLS/curl.log" && ok "ничего нет на диске — v2.13 с GitHub" || bad "возврат по сети" "$(cat "$CALLS/curl.log")"
ret_setup nonet
o=$(printf '1\n\n' | lib t_ret 2>&1 | strip)
cmp -s "$S" "$C" && ! ctl_log | grep -q rollback && grep -q '"engine": "nft"' "$KASKAD_DIR/state.json" && printf '%s' "$o" | grep -q 'ничего не менял' \
  && ok "2.x не найти и не скачать → ничего не менял (пробросы на nft, команда 3.x)" || bad "возврат без программы" "$o"
ret_setup no
printf '2\n' | lib t_ret >/dev/null 2>&1
cmp -s "$S" "$C" && ! ctl_log | grep -q rollback && grep -q '"engine": "nft"' "$KASKAD_DIR/state.json" && ok "ответ «2» — ничего не меняется" || bad "отказ от возврата"

echo "── 11. xray для «Прокси для API Telegram»: только закреплённая версия (3.3.4) ──"
# До 3.3.4 качался releases/latest (а не вышло — любой xray сервера); теперь — только выпуск EGRESS_XVER, архив и файл
# сверены по SHA-256 (подробно — tools/egress_xray_selftest.sh). Здесь — что спрашивается у GitHub и честный отказ.
t_xray() { egress_ensure_xray; echo "RC=$?"; t_pin=$(egress_xray_pin) && echo "ZIP=${t_pin%% *}"; }
use_root xray
printf '%s' '{"tag_name":"v26.9.10"}' > "$FAKE_DIR/xray_latest"
o=$(lib t_xray </dev/null 2>&1 | strip)
zip=$(printf '%s\n' "$o" | sed -n 's/^ZIP=//p')
dl=$(grep -o 'releases/download/[^ ]*' "$CALLS/curl.log" | tr '\n' ' ')
[ -n "$zip" ] && [ "$dl" = "releases/download/v26.3.27/$zip " ] && ok "одна загрузка: releases/download/v26.3.27/$zip" || bad "адрес загрузки xray" "$dl | $o"
! grep -q 'api.github.com/repos/XTLS' "$CALLS/curl.log" && ok "releases/latest не спрашивается (свежий тег v26.9.10 не берётся)" || bad "спрошен API GitHub" "$(cat "$CALLS/curl.log")"
printf '%s' "$o" | grep -q 'RC=1' && [ ! -e "$KASKAD_DIR/egress/xray" ] && ok "архив не скачался — честный отказ, xray нет" || bad "отказ без архива" "$o"
left=$(grep -v '^[[:space:]]*#' "$S" | grep -nE 'grep[^|]*tag_name|tag_name[^|]*\| *cut|Xray-core/releases/latest')
[ -z "$left" ] && ok "в install.sh нет ни releases/latest, ни разбора tag_name" || bad "остался releases/latest" "$left"

echo "── 11b. sysctl: свой файл в /etc/sysctl.d (Debian 13 / Ubuntu 26.04 не читают sysctl.conf) ──"
use_root sysctl1
lib k3_sysctl_tuning >/dev/null 2>&1
F="$KASKAD_ROOT/etc/sysctl.d/90-kaskad-tuning.conf"
grep -qx 'net.ipv4.ip_forward=1' "$F" && grep -qx 'net.core.default_qdisc=fq' "$F" && grep -qx 'net.ipv4.tcp_congestion_control=bbr' "$F" \
  && ok "90-kaskad-tuning.conf: ip_forward, fq, bbr" || bad "файл sysctl.d" "$(cat "$F" 2>&1)"
[ ! -e "$KASKAD_ROOT/etc/sysctl.conf" ] && ok "sysctl.conf нет — не создаётся" || bad "создан sysctl.conf"
o=$(lib k3_sysctl_tuning 2>&1); [ -z "$o" ] && ok "без sysctl.conf — ни строчки на экране (на Debian 13 был grep: No such file)" || bad "вывод" "$o"
m1=$(stat -f %m "$F" 2>/dev/null || stat -c %Y "$F"); sleep 1; lib k3_sysctl_tuning >/dev/null 2>&1
m2=$(stat -f %m "$F" 2>/dev/null || stat -c %Y "$F"); [ "$m1" = "$m2" ] && ok "повторный вызов файл не переписывает" || bad "файл переписан"
use_root sysctl2
printf '%s\n' '# old' '#net.ipv4.ip_forward=1' 'vm.swappiness=10' > "$KASKAD_ROOT/etc/sysctl.conf"
lib k3_sysctl_tuning >/dev/null 2>&1
[ "$(cat "$KASKAD_ROOT/etc/sysctl.conf")" = "$(printf '%s\n' '# old' 'net.ipv4.ip_forward=1' 'vm.swappiness=10')" ] \
  && ok "в sysctl.conf исправлена только строка ip_forward, новых строк нет" || bad "sysctl.conf" "$(cat "$KASKAD_ROOT/etc/sysctl.conf")"

echo "── 12. старый движок: пакеты, сохранение правил, «Вернуть iptables» ──"
t_deps() { k3_legacy_deps "$@"; echo "RC=$?"; }
use_root deps1; os_release ubuntu 22.04; touch "$FAKE_DIR/pkg.ufw"
o=$(lib t_deps full 2>&1 | strip)
grep -q 'install -y --no-remove iptables-persistent netfilter-persistent' "$CALLS/apt.log" && grep -q 'DPkg::Lock::Timeout=600' "$CALLS/apt.log" \
  && ok "Ubuntu 22.04 (без нового движка): iptables-persistent ставится, как 2.13 — с --no-remove и ожиданием блокировки apt" || bad "22.04 full" "$(cat "$CALLS/apt.log")"
use_root deps2; os_release ubuntu 24.04
o=$(lib t_deps full 2>&1 | strip)
! grep -q 'iptables-persistent' "$CALLS/apt.log" && ok "Ubuntu 24.04: iptables-persistent не ставится даже при «full» (решение владельца: он удаляет ufw)" || bad "24.04 full" "$(cat "$CALLS/apt.log")"
use_root deps3; os_release debian 11; printf 'Remv ufw [0.36-7.1]\n' > "$FAKE_DIR/apt_sim"
o=$(lib t_deps full 2>&1 | strip)
! grep -q 'install -y --no-remove iptables-persistent' "$CALLS/apt.log" && [ -f "$KASKAD_DIR/.legacy-persist-refused" ] && printf '%s' "$o" | grep -q 'удалил бы ufw' \
  && ok "apt удалил бы ufw (симуляция: Remv) → не ставим, запоминаем, честное предупреждение" || bad "Remv" "$o | $(cat "$CALLS/apt.log")"
: > "$CALLS/apt.log"; lib t_deps full >/dev/null 2>&1
[ ! -s "$CALLS/apt.log" ] && ok "после отказа apt больше не дёргается на каждом запуске" || bad "повтор после отказа" "$(cat "$CALLS/apt.log")"
use_root deps4; os_release debian 11; echo 100 > "$FAKE_DIR/apt_rc"
o=$(lib t_deps full 2>&1 | strip)
[ ! -f "$KASKAD_DIR/.legacy-persist-refused" ] && printf '%s' "$o" | grep -q 'попробую при следующем запуске' \
  && ok "apt занят или нет сети → отказ не запоминается, попробуем снова" || bad "временный сбой apt" "$o"
use_root deps5; os_release debian 12
o=$(lib t_deps 2>&1 | strip)
[ ! -s "$CALLS/apt.log" ] && ok "iptables есть, «min» — apt не нужен" || bad "лишний apt" "$(cat "$CALLS/apt.log")"
t_rb() { init_config >/dev/null 2>&1; k3_rollback_interactive; echo "RC=$?"; }
use_root rb1; fake_ctl; engine_nft; echo kaskad-bot > "$FAKE_DIR/active"; rm -f "$W/bin/netfilter-persistent.off"
o=$(printf '1\n' | lib t_rb 2>&1 | strip)
printf '%s' "$o" | grep -q 'После перезагрузки пробросы пропадут' && bad "есть netfilter-persistent, а предупреждение о перезагрузке показано" "$o" \
  || ok "есть netfilter-persistent → без предупреждения о перезагрузке"
ctl_log | grep -qx 'migrate rollback --to-legacy --yes' && grep -q '^K3_MIGRATE_ASK="0"' "$KASKAD_DIR/config" && grep -qx 'restart kaskad-bot' "$CALLS/systemctl.log" \
  && grep -qx 'save' "$CALLS/netfilter-persistent.log" && ok "16 → 2: rollback --to-legacy, больше не спрашивать, бот перезапущен, правила сохранены" || bad "вернуть iptables" "$o | $(ctl_log)"
# Без netfilter-persistent: на Debian 12+ / Ubuntu 24.04+ — только честное предупреждение (решение
# владельца: iptables-persistent там не ставим); на других системах — как 2.13.
mv "$W/bin/netfilter-persistent" "$W/bin/netfilter-persistent.off"
use_root rb2; fake_ctl; engine_nft
o=$(printf '1\n' | K3_TEST_TTY=1 lib t_rb 2>&1 | strip)
printf '%s' "$o" | grep -q 'iptables-persistent на этой системе Kaskad не ставит' && ! grep -q 'iptables-persistent' "$CALLS/apt.log" \
  && ok "Debian 12, нет netfilter-persistent → предупреждение, iptables-persistent не ставим" || bad "rollback на Debian 12" "$o | $(cat "$CALLS/apt.log")"
# critic #3a: предупреждение — ДО вопроса «Вернуть iptables?», а не после действия: порядок строк на экране
# (k3_choose печатает вопрос и без терминала) и в самой функции; ответ «2» — ничего не сделано.
use_root rb2b; fake_ctl; engine_nft
o=$(printf '2\n' | lib t_rb 2>&1 | strip)
body=$(sed -n '/^k3_rollback_interactive() {/,/^}/p' "$S")
w=$(printf '%s\n' "$body" | grep -n 'k3_legacy_unsaved' | head -1 | cut -d: -f1)
q=$(printf '%s\n' "$body" | grep -n 'k3_choose_yes "$(t MIG_RB_Q)"' | head -1 | cut -d: -f1)
ow=$(printf '%s\n' "$o" | grep -n 'После перезагрузки пробросы пропадут' | head -1 | cut -d: -f1)
oq=$(printf '%s\n' "$o" | grep -n '1) Вернуть iptables' | head -1 | cut -d: -f1)
printf '%s' "$o" | grep -q 'После перезагрузки пробросы пропадут' && printf '%s' "$o" | grep -q 'оставайтесь на nftables' \
  && ! ctl_log | grep -q rollback && [ -n "$w" ] && [ -n "$q" ] && [ "$w" -lt "$q" ] && [ -n "$ow" ] && [ -n "$oq" ] && [ "$ow" -lt "$oq" ] \
  && ok "нет netfilter-persistent (Debian 12+): «После перезагрузки пробросы пропадут … оставайтесь на nftables» — до вопроса; «2» — ничего не тронуто" \
  || bad "предупреждение до вопроса" "$o"
use_root rb3; fake_ctl; engine_nft; os_release ubuntu 22.04
o=$(printf '1\n' | lib t_rb 2>&1 | strip)
grep -q 'install -y --no-remove iptables-persistent netfilter-persistent' "$CALLS/apt.log" && ok "Ubuntu 22.04, нет netfilter-persistent → ставим, как 2.13 (с --no-remove)" || bad "rollback на 22.04" "$o | $(cat "$CALLS/apt.log")"
mv "$W/bin/netfilter-persistent.off" "$W/bin/netfilter-persistent"
# Код 1 при частичном успехе (правила уже в iptables): продолжаем; откат не удался — честный отказ.
use_root rb4; fake_ctl; engine_nft; echo 1 > "$FAKE_DIR/rollback_rc"
o=$(printf '1\n' | lib t_rb 2>&1 | strip)
printf '%s' "$o" | grep -q 'не всё прошло гладко' && grep -q '^K3_MIGRATE_ASK="0"' "$KASKAD_DIR/config" && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "rollback с кодом 1, но движок уже legacy → предупреждение и дальше как при успехе" || bad "частичный rollback" "$o"
use_root rb5; fake_ctl; engine_nft; echo 1 > "$FAKE_DIR/rollback_rc"; touch "$FAKE_DIR/rollback_keeps_nft"
o=$(printf '1\n' | lib t_rb 2>&1 | strip)
printf '%s' "$o" | grep -q 'Вернуть iptables не удалось (код 1) — пробросы работают через nftables' && ! grep -q 'K3_MIGRATE_ASK' "$KASKAD_DIR/config" \
  && ok "rollback не удался (движок nft) → честный отказ, настройки не тронуты" || bad "неудачный rollback" "$o"

echo "── 13. режимы демонов без state.json (так их запускает self_update 2.x) ──"
use_root daemons; U="$KASKAD_ROOT/etc/systemd/system"; C="$KASKAD_ROOT/usr/local/bin/gokaskad"
cp "$S" "$C"; mk_version 2.13 "$C.bak"
printf '[Service]\nExecStart=%s --bot-daemon\n' "$C" > "$U/kaskad-bot.service"
printf 'BOT_TOKEN=""\nBOT_CHAT_ID=""\n' > "$KASKAD_DIR/config"
( cd "$KASKAD_ROOT" && bash "$C" --bot-daemon </dev/null >/dev/null 2>&1 ); rc=$?
[ "$rc" = "1" ] && grep -q 'Bot daemon started' "$KASKAD_ROOT/var/log/kaskad.log" && grep -q 'BOT ERROR: no token' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "--bot-daemon без state.json: старт как в 2.13 (без токена — выход 1 с записью в журнал)" || bad "--bot-daemon" "rc=$rc $(cat "$KASKAD_ROOT/var/log/kaskad.log" 2>/dev/null)"
[ -f "$K3_OPT/lib/$SHA12/.ok" ] && cmp -s "$C" "$K3_OPT/bin/gokaskad" && grep -qx "ExecStart=$K3_OPT/bin/gokaskad --bot-daemon" "$U/kaskad-bot.service" \
  && cmp -s "$C.bak" "$K3_OPT/prev/gokaskad-2.13" && ok "демон заодно: код и копия в /opt, юнит — на копию, прежняя 2.13 из .bak — в prev" || bad "post-update в демоне" "$(cat "$U/kaskad-bot.service")"
bash "$C" --monitor-daemon </dev/null >/dev/null 2>&1 & mp=$!
sleep 2; kill -0 "$mp" 2>/dev/null && alive=1 || alive=0; kill "$mp" 2>/dev/null; wait "$mp" 2>/dev/null
[ "$alive" = "1" ] && grep -q 'Monitor daemon started' "$KASKAD_ROOT/var/log/kaskad.log" && ok "--monitor-daemon без state.json работает (не падает)" || bad "--monitor-daemon упал"

# critic #10: движок legacy, а команду вернули на 2.x (self_update 2.x / его откат) — служба идёт за
# командой: юниты обратно на /usr/local/bin/gokaskad, daemon-reload, процесс становится программой 2.x.
fake_2x() {   # «Kaskad PRO 2.13», который только записывает, как его запустили
    printf '#!/bin/bash\nKASKAD_VERSION="2.13"\necho "2.x $*" >> "$CALLS/twox.log"\ncase "${1:-}" in\n  *) : ;;\nesac\n' > "$1"
    chmod 755 "$1"
}
use_root follow; U="$KASKAD_ROOT/etc/systemd/system"; C="$KASKAD_ROOT/usr/local/bin/gokaskad"
lib k3_ensure_lib >/dev/null 2>&1; cp "$S" "$K3_OPT/bin/gokaskad"; fake_2x "$C"
printf '[Service]\nExecStart=%s --bot-daemon\n' "$K3_OPT/bin/gokaskad" > "$U/kaskad-bot.service"
printf '[Service]\nExecStart=%s --monitor-daemon\n' "$K3_OPT/bin/gokaskad" > "$U/kaskad-monitor.service"
printf 'BOT_TOKEN=""\nBOT_CHAT_ID=""\n' > "$KASKAD_DIR/config"
bash "$K3_OPT/bin/gokaskad" --bot-daemon </dev/null >/dev/null 2>&1; rc=$?
[ "$rc" = "0" ] && grep -qx "ExecStart=$C --bot-daemon" "$U/kaskad-bot.service" && grep -qx "ExecStart=$C --monitor-daemon" "$U/kaskad-monitor.service" \
  && grep -qx 'daemon-reload' "$CALLS/systemctl.log" && grep -qx '2.x --bot-daemon' "$CALLS/twox.log" \
  && ! grep -q 'Bot daemon started' "$KASKAD_ROOT/var/log/kaskad.log" && grep -q 'K3: команда gokaskad — Kaskad PRO 2.13' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "движок legacy, команда снова 2.13 → юниты на команду, daemon-reload, процесс стал 2.13 (тот же PID)" \
  || bad "демон 3.x за командой 2.x" "rc=$rc $(cat "$U/kaskad-bot.service") | $(cat "$CALLS/twox.log" 2>&1)"
: > "$CALLS/twox.log"; : > "$CALLS/systemctl.log"
bash "$K3_OPT/bin/gokaskad" --monitor-daemon </dev/null >/dev/null 2>&1
grep -qx '2.x --monitor-daemon' "$CALLS/twox.log" && ! grep -q 'daemon-reload' "$CALLS/systemctl.log" \
  && ok "юниты уже на команде — без daemon-reload, сразу программа 2.x" || bad "второй демон" "$(cat "$CALLS/twox.log" 2>&1)"
use_root follow-nft; U="$KASKAD_ROOT/etc/systemd/system"; C="$KASKAD_ROOT/usr/local/bin/gokaskad"
lib k3_ensure_lib >/dev/null 2>&1; cp "$S" "$K3_OPT/bin/gokaskad"; fake_2x "$C"; engine_nft
printf '[Service]\nExecStart=%s --bot-daemon\n' "$K3_OPT/bin/gokaskad" > "$U/kaskad-bot.service"
printf 'BOT_TOKEN=""\nBOT_CHAT_ID=""\n' > "$KASKAD_DIR/config"
bash "$K3_OPT/bin/gokaskad" --bot-daemon </dev/null >/dev/null 2>&1
grep -qx "ExecStart=$K3_OPT/bin/gokaskad --bot-daemon" "$U/kaskad-bot.service" && [ ! -s "$CALLS/twox.log" ] \
  && grep -q 'Bot daemon started' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "движок nft, команда 2.x → как раньше: служба на копии 3.x (команду вернёт сторож)" || bad "nft: демон ушёл за 2.x" "$(cat "$U/kaskad-bot.service")"
use_root follow-free; U="$KASKAD_ROOT/etc/systemd/system"; C="$KASKAD_ROOT/usr/local/bin/gokaskad"
lib k3_ensure_lib >/dev/null 2>&1; cp "$S" "$K3_OPT/bin/gokaskad"
printf '#!/bin/bash\n# бесплатный Kaskad\nconfigure_custom_rule() { :; }\nshow_promo() { :; }\n' > "$C"
printf '[Service]\nExecStart=%s --bot-daemon\n' "$K3_OPT/bin/gokaskad" > "$U/kaskad-bot.service"
printf 'BOT_TOKEN=""\nBOT_CHAT_ID=""\n' > "$KASKAD_DIR/config"
bash "$K3_OPT/bin/gokaskad" --bot-daemon </dev/null >/dev/null 2>&1
grep -qx "ExecStart=$K3_OPT/bin/gokaskad --bot-daemon" "$U/kaskad-bot.service" && grep -q 'Bot daemon started' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "команда — бесплатный скрипт (у него нет бота) → служба остаётся на 3.x" || bad "бесплатный скрипт" "$(cat "$U/kaskad-bot.service")"

echo "── 14. удаление на движке nft ──"
use_root uninst; fake_ctl; engine_nft
# как на узле 3.2: xray бота и проба (служба, таймера нет) работают
printf '[Service]\nExecStart=/x\n' > "$KASKAD_ROOT/etc/systemd/system/kaskad-egress.service"
printf '[Service]\nExecStart=/bin/bash /x\n' > "$KASKAD_ROOT/etc/systemd/system/kaskad-egress-probe.service"
printf 'kaskad-egress\nkaskad-egress-probe\n' >> "$FAKE_DIR/active"
t_uninst() {   # слово подтверждения выбирается через $RANDOM: то же зерно — то же слово
    local words=("УДАЛИТЬ" "СТЕРЕТЬ" "СНЕСТИ" "УНИЧТОЖИТЬ" "ПРОЩАЙ") w
    RANDOM=7; w="${words[$((RANDOM % ${#words[@]}))]}"; RANDOM=7
    full_uninstall < <(printf '1\n%s\n\n' "$w")
}
engine_nft_fwds; touch "$FAKE_DIR/ufw_active"
mkdir -p "$KASKAD_ROOT/etc/systemd/networkd.conf.d"; echo '[Network]' > "$KASKAD_ROOT/etc/systemd/networkd.conf.d/90-kaskad.conf"
: > "$KASKAD_DIR/gw-offer.pending"           # 3.2.21: отметка «предложить шлюз» уходит вместе с /etc/kaskad
lib t_uninst >/dev/null 2>&1
ctl_log | grep -qx 'net teardown' && [ ! -e "$K3_OPT" ] && [ ! -e "$KASKAD_DIR" ] && ok "kaskadctl net teardown, затем удалены /opt/kaskad и /etc/kaskad (state.json, отметка gw-offer.pending)" || bad "удаление" "$(ctl_log) | $(ls "$KASKAD_ROOT/opt" 2>&1)"
[ ! -e "$KASKAD_ROOT/etc/systemd/networkd.conf.d/90-kaskad.conf" ] && [ -d "$KASKAD_ROOT/etc/systemd/networkd.conf.d" ] \
  && ok "файл systemd-networkd 90-kaskad.conf удалён (ног и правил Kaskad не осталось), каталог на месте" || bad "90-kaskad.conf после удаления"
grep -qx 'delete allow 443/tcp' "$CALLS/ufw.log" && grep -qx 'delete allow 51820/udp' "$CALLS/ufw.log" \
  && ok "ufw включён: удаление Kaskad на nft снимает ufw allow входных портов пробросов (как 2.x)" || bad "ufw при удалении Kaskad" "$(cat "$CALLS/ufw.log")"
rm -f "$FAKE_DIR/ufw_active"
[ ! -e "$KASKAD_ROOT/etc/systemd/system/kaskad-egress.service" ] && [ ! -e "$KASKAD_ROOT/etc/systemd/system/kaskad-egress-probe.service" ] \
  && ! grep -qE '^kaskad-' "$FAKE_DIR/active" && [ ! -s "$CALLS/stop_nofile.log" ] \
  && ok "служба «Прокси для API Telegram» и проба остановлены, потом удалены (3.2.19 оставлял их работать без юнита)" \
  || bad "egress после удаления" "active: $(cat "$FAKE_DIR/active") | $(cat "$CALLS/systemctl.log")"

echo "── 15. старый движок пишет под блокировкой переезда (flock /run/kaskad/state.lock) ──"
# Держатель блокировки — как kaskadctl migrate: flock на том же файле. $1 — сколько секунд держать;
# $2 = nft — перед отпуском «переезд» успевает записать движок nft.
hold_lock() {
    rm -f "$W/held"
    python3 - "$KASKAD_ROOT/run/kaskad/state.lock" "$1" "${2:-}" "$KASKAD_DIR/state.json" "$W/held" <<'PY' &
import fcntl, os, sys, time
lock, secs, nft, st, marker = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
os.makedirs(os.path.dirname(lock), exist_ok=True)
fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
open(marker, "w").close()
time.sleep(secs)
if nft:
    with open(st, "w") as f:
        f.write('{\n  "engine": "nft",\n  "schema": 1\n}\n')
os.close(fd)
PY
    HOLDER=$!
    while [ ! -f "$W/held" ]; do sleep 0.05; done
}
use_root lock1; fake_ctl
o=$(lib t_apply 2>&1 | strip)
[ "$(wc -l < "$CALLS/flock.log" | tr -d ' ')" = "1" ] && grep -qE '^-w 15 [0-9]+$' "$CALLS/flock.log" \
  && grep -q 'kaskad:443:tcp -j DNAT --to-destination 2.2.2.2:443' "$IPT_DIR/nat.PREROUTING" && grep -q save "$CALLS/netfilter-persistent.log" \
  && ok "legacy apply_iptables_rules: правила и netfilter-persistent save — под одной блокировкой (flock -w 15, вложенные не берут её снова)" \
  || bad "блокировка legacy apply" "$(cat "$CALLS/flock.log" 2>&1) | $o"
[ -e "$KASKAD_ROOT/run/kaskad/state.lock" ] && ok "файл блокировки тот же, что у kaskadctl: \$KASKAD_ROOT/run/kaskad/state.lock" || bad "нет state.lock"
use_root lock2; fake_ctl; hold_lock 3
: > "$CALLS/netfilter-persistent.log"
o=$(K3_LOCK_WAIT=1 lib t_apply 2>&1 | strip); wait "$HOLDER"
printf '%s' "$o" | grep -q 'RC=6' && printf '%s' "$o" | grep -q 'Kaskad сейчас что-то меняет' && [ ! -s "$IPT_DIR/nat.PREROUTING" ] \
  && [ ! -s "$CALLS/netfilter-persistent.log" ] && ok "переезд держит блокировку дольше K3_LOCK_WAIT → код 6 и честная строка, iptables и rules.v4 не тронуты" \
  || bad "занятая блокировка" "$o | $(cat "$IPT_DIR/nat.PREROUTING" 2>/dev/null)"
use_root lock3; fake_ctl; hold_lock 1 nft
o=$(K3_LOCK_WAIT=10 lib t_apply 2>&1 | strip); wait "$HOLDER"
printf '%s' "$o" | grep -q 'RC=7' && printf '%s' "$o" | grep -q 'только что перешли на nftables' && [ ! -s "$IPT_DIR/nat.PREROUTING" ] && [ -z "$(ctl_log)" ] \
  && ok "пока ждали, переезд закончился (движок nft) → код 7, правило в iptables не пишется" || bad "движок сменился во время ожидания" "$o | $(ctl_log)"
use_root lock4; fake_ctl
cat > "$IPT_DIR/nat.PREROUTING" <<'R'
-p tcp -m tcp --dport 443 -m comment --comment kaskad:443:tcp -j DNAT --to-destination 2.2.2.2:443
R
printf '%s\n' '-p tcp -m tcp --dport 443 -m comment --comment kaskad:443:tcp -j ACCEPT' > "$IPT_DIR/filter.INPUT"
t_botdel() { K3_BOT=1; BOT_TOKEN="1:x"; bot_handle_callback 42 7 cb "dry_1"; }
lib t_botdel >/dev/null 2>&1
[ ! -s "$IPT_DIR/nat.PREROUTING" ] && [ ! -s "$IPT_DIR/filter.INPUT" ] && [ "$(wc -l < "$CALLS/flock.log" | tr -d ' ')" = "1" ] \
  && grep -q 'удалено' "$CALLS/tg.log" && grep -q 'BOT DELETE: tcp :443' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "бот, «Удалить» на старом движке: правила, masq, сохранение и журнал — одним куском под блокировкой" || bad "бот: удаление legacy" "$(cat "$CALLS/tg.log") | $(cat "$CALLS/flock.log")"
cat > "$IPT_DIR/nat.PREROUTING" <<'R'
-p tcp -m tcp --dport 443 -m comment --comment kaskad:443:tcp -j DNAT --to-destination 2.2.2.2:443
R
: > "$CALLS/tg.log"; hold_lock 3
K3_LOCK_WAIT=1 lib t_botdel >/dev/null 2>&1; wait "$HOLDER"
grep -q 'kaskad:443:tcp' "$IPT_DIR/nat.PREROUTING" && grep -q 'Не удалось удалить: Kaskad сейчас что-то меняет' "$CALLS/tg.log" \
  && ok "бот: блокировка занята → «❌ Не удалось удалить: Kaskad сейчас что-то меняет…», правило на месте" || bad "бот: занято" "$(cat "$CALLS/tg.log")"
: > "$CALLS/tg.log"; hold_lock 3
t_botadd() {
    K3_BOT=1; BOT_TOKEN="1:x"; mkdir -p "$BOT_STATE_DIR"
    bot_set_state 42 "STATE=awaiting_note" "PROTO=tcp" "NAME=VLESS" "CUSTOM=0" "TARGET_IP=2.2.2.2" "PORT=8443"
    bot_handle_message 42 "-"
}
K3_LOCK_WAIT=1 lib t_botadd >/dev/null 2>&1; wait "$HOLDER"
grep -q 'Проброс не добавлен' "$CALLS/tg.log" && grep -q 'Kaskad сейчас что-то меняет' "$CALLS/tg.log" && ! grep -q '✅' "$CALLS/tg.log" \
  && ok "бот: добавить на старом движке во время переезда → отказ с причиной, а не «✅»" || bad "бот: добавление при занятой блокировке" "$(cat "$CALLS/tg.log")"
use_root lock5; fake_ctl
t_flushui_legacy() { flush_rules <<< "1"; }
cat > "$IPT_DIR/nat.PREROUTING" <<'R'
-p tcp -m tcp --dport 443 -m comment --comment kaskad:443:tcp -j DNAT --to-destination 2.2.2.2:443
R
lib t_flushui_legacy >/dev/null 2>&1
[ ! -s "$IPT_DIR/nat.PREROUTING" ] && [ "$(wc -l < "$CALLS/flock.log" | tr -d ' ')" = "1" ] && grep -q 'FLUSH all kaskad rules' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "«Сбросить всё» на старом движке — под одной блокировкой" || bad "сброс legacy" "$(cat "$CALLS/flock.log")"
mv "$W/bin/flock" "$W/bin/flock.off"
use_root lock6; fake_ctl
o=$(lib t_apply 2>&1 | strip)
mv "$W/bin/flock.off" "$W/bin/flock"
printf '%s' "$o" | grep -q 'RC=0' && grep -q 'kaskad:443:tcp -j DNAT' "$IPT_DIR/nat.PREROUTING" && [ ! -e "$CALLS/flock.log" ] \
  && ok "flock нет — правила пишутся как в 2.13, без блокировки" || bad "без flock" "$o"

echo "── 16. ноги AmneziaWG: перезапуск systemd-networkd, --egress-awg-heal, файл 90-kaskad.conf ──"
# Стенд, Ubuntu 26.04: перезапуск systemd-networkd (каждое обновление пакета systemd) стирает ip rule ног
# (fwmark 256+N) и их запасной unreachable; маршрут через устройство остаётся. Нога мертва до
# --egress-awg-up, который пересоздаёт ВСЕ ноги. Теперь: файл networkd не даёт стирать, а
# --egress-awg-heal чинит на месте тем же кодом, что и подъём ног.
cp "$W/ip-stateful" "$W/bin/ip"
VLESS_KEY='vless://11111111-2222-3333-4444-555555555555@198.51.100.20:443?type=tcp&security=reality&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com&fp=chrome&flow=xtls-rprx-vision#Vienna'
# Строка пула awg://… — тем же разборщиком, что пишет install.sh (как при импорте ключа).
mk_awg() {
    python3 - "$KASKAD_DIR/egress/egress_parse.py" "$1" <<'PY'
import base64, importlib.util, json, sys
spec = importlib.util.spec_from_file_location("egress_parse", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
n = int(sys.argv[2])
key = lambda s: base64.b64encode(bytes((s * 7 + i) % 256 for i in range(32))).decode()
conf = ("[Interface]\nAddress = 10.8.%d.5/32\nPrivateKey = %s\nJc = 4\nJmin = 40\nJmax = 70\nS1 = 116\nS2 = 61\n"
        "H1 = 1234567801\nH2 = 1234567802\nH3 = 1234567803\nH4 = 1234567804\n\n[Peer]\nPublicKey = %s\n"
        "AllowedIPs = 0.0.0.0/0\nEndpoint = 198.51.100.%d:51820\nPersistentKeepalive = 25\n") % (n, key(n), key(100 + n), 30 + n)
print("awg://" + m._b64e(json.dumps(m.awg_from_conf(conf, "Leg%d" % n))))
PY
}
# Пул: VLESS (строка 1) и два ключа AmneziaWG — строки 2 и 3: kawg2 (0x102, таблица 258), kawg3 (0x103, 259).
# Ядро AmneziaWG — поддельное, в $AWG_DIR (его install.sh берёт первым, настоящее из /usr/local/bin не
# запустится): amneziawg-go («amneziawg-go -f kawgN» — так его запускает юнит ноги kaskad-leg@, поддельный
# systemctl) «создаёт» интерфейс в ip с памятью, awg пишет журнал.
heal_root() {
    use_root "$1"; rm -f "$FAKE_DIR/ip.json"; : > "$CALLS/ip.log"; : > "$CALLS/pkill.log"; : > "$CALLS/core.log"
    lib egress_ensure_parser >/dev/null 2>&1
    { echo "$VLESS_KEY"; mk_awg 2; mk_awg 3; } > "$KASKAD_DIR/egress/upstream.txt"
    [ "${2:-}" = "no-networkd" ] || { mkdir -p "$KASKAD_ROOT/lib/systemd"; : > "$KASKAD_ROOT/lib/systemd/systemd-networkd"; }
    local d="$KASKAD_DIR/egress/awg"; mkdir -p "$d"
    printf '#!/usr/bin/env bash\necho "go $*" >> "$CALLS/core.log"\n[ "$1" = -f ] && shift\nip link add "$1" type tun\n' > "$d/amneziawg-go"
    printf '#!/usr/bin/env bash\necho "awg $1 $2" >> "$CALLS/core.log"\n' > "$d/awg"
    chmod 755 "$d/amneziawg-go" "$d/awg"
}
# Что делает перезапуск systemd-networkd без 90-kaskad.conf: чужие ip rule и маршруты без устройства — прочь.
networkd_restart() {
    python3 - "$FAKE_DIR/ip.json" <<'PY'
import json, sys
st = json.load(open(sys.argv[1]))
st["rules"] = [r for r in st["rules"] if r[1] in ("lookup local", "lookup main", "lookup default")]
st["routes"] = dict((t, [r for r in rs if " dev " in " %s " % r]) for t, rs in st["routes"].items())
json.dump(st, open(sys.argv[1], "w"))
PY
}
nrule() { ip -4 rule show | grep -cE "fwmark $1 lookup $2\$"; }
perm() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1"; }
DROPIN_REL=/etc/systemd/networkd.conf.d/90-kaskad.conf
python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from kaskad.gw import sys as g; sys.stdout.write(g.NETWORKD_DROPIN)' \
    "$ROOT_DIR/lib" > "$W/dropin.want"
heal_root heal1
ip link add kawg2 type tun; ip link add kawg3 type tun                      # ноги стояли под своими юнитами…
printf 'kaskad-leg@kawg2\nkaskad-leg@kawg3\n' >> "$FAKE_DIR/active"
ip route replace default dev kawg2 table 258; ip route replace default dev kawg3 table 259
ip rule add fwmark 259 table 259; ip rule add fwmark 259 table 259           # …у kawg3 правило задвоено
: > "$CALLS/ip.log"
o=$( (umask 077; lib egress_awg_heal; echo "RC=$?") 2>&1)
[ "$(nrule 0x102 258)" = 1 ] && [ "$(nrule 0x103 259)" = 1 ] && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "heal: правило ноги вернулось (kawg2), задвоенное — одно (kawg3)" || bad "heal: правила" "$o | $(ip -4 rule show)"
[ "$(ip -4 route show table 258 | sort | tr '\n' '|')" = "default dev kawg2|unreachable default metric 1000|" ] \
  && [ "$(ip -4 route show table 259 | sort | tr '\n' '|')" = "default dev kawg3|unreachable default metric 1000|" ] \
  && ok "heal: запасной unreachable metric 1000 на месте, маршрут через ногу — тот же" || bad "heal: маршруты" "$(ip -4 route show table 258) / $(ip -4 route show table 259)"
! grep -qE '^(link (add|del|set)|addr)' "$CALLS/ip.log" && ! grep -q 'rule del fwmark 258' "$CALLS/ip.log" \
  && [ "$(grep -c 'rule del fwmark 259 table 259' "$CALLS/ip.log")" = 1 ] \
  && ok "heal: интерфейсы не тронуты, живое правило не снималось ни на миг (снята только лишняя копия)" || bad "heal: лишние вызовы ip" "$(cat "$CALLS/ip.log")"
F="$KASKAD_ROOT$DROPIN_REL"
cmp -s "$F" "$W/dropin.want" && [ "$(perm "$F")" = 644 ] && [ "$(perm "${F%/*}")" = 755 ] \
  && ok "файл systemd-networkd: тот же текст, что пишет шлюз (lib/kaskad), 0644, каталог 0755 и при umask 077" || bad "90-kaskad.conf" "$(perm "$F") $(perm "${F%/*}") $(cat "$F" 2>&1)"
grep -qx '\[Network\]' "$F" && grep -qx 'ManageForeignRoutingPolicyRules=no' "$F" && grep -qx 'ManageForeignRoutes=no' "$F" \
  && ! grep -q networkd "$CALLS/systemctl.log" && ok "в файле [Network] ManageForeignRoutingPolicyRules=no, ManageForeignRoutes=no; networkd не перезапускался" || bad "текст 90-kaskad.conf / systemctl" "$(cat "$CALLS/systemctl.log")"
ino=$(ls -i "$F" | awk '{print $1}'); : > "$CALLS/ip.log"
o=$(lib egress_awg_heal; echo "RC=$?")
! grep -qE '^rule (add|del)' "$CALLS/ip.log" && [ "$(ls -i "$F" | awk '{print $1}')" = "$ino" ] && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "повторный heal: ни одного ip rule add/del, файл не переписан" || bad "повторный heal" "$(cat "$CALLS/ip.log")"
ip link del kawg3; networkd_restart; : > "$CALLS/ip.log"; : > "$CALLS/pkill.log"; : > "$CALLS/systemctl.log"   # пропал интерфейс ноги kawg3
o=$(lib egress_awg_heal; echo "RC=$?")
[ "$(nrule 0x102 258)" = 1 ] && [ "$(nrule 0x103 259)" = 1 ] && ip link show dev kawg3 >/dev/null 2>&1 \
  && [ "$(ip -4 route show table 259 | sort | tr '\n' '|')" = "default dev kawg3|unreachable default metric 1000|" ] \
  && printf '%s' "$o" | grep -q 'RC=0' && ok "нога без интерфейса поднята одна (amneziawg-go, awg setconf, маршрут), живая — починена на месте" \
  || bad "heal: подъём ноги" "$o | $(cat "$CALLS/ip.log")"
! grep -qE '^link (add|del|set) kawg2|kawg2 up' "$CALLS/ip.log" && [ "$(cat "$CALLS/pkill.log")" = '-f amneziawg-go kawg3$' ] \
  && grep -qx 'go -f kawg3' "$CALLS/core.log" && ! grep -q 'kawg2' "$CALLS/core.log" \
  && grep -qx 'restart kaskad-leg@kawg3.service' "$CALLS/systemctl.log" && ! grep -qE '^(restart|start|stop|disable|reset-failed).*kaskad-leg@kawg2' "$CALLS/systemctl.log" \
  && ok "kawg2 и её юнит не тронуты; kawg3 — systemctl restart kaskad-leg@kawg3; «голый» процесс ищется по имени целиком (amneziawg-go kawg3\$, не kawg30)" \
  || bad "heal задел другие ноги" "$(cat "$CALLS/ip.log") | $(cat "$CALLS/pkill.log") | $(cat "$CALLS/core.log")"
# T-M6-qs330b №8: после загрузки юнит ноги ждёт в очереди systemd (сеть, модуль ядра) — heal его не трогает: ногу
# поднимет systemd; restart поверх пересоздал бы ногу, которую systemd только что поднял.
ip link del kawg3; leg_off_fake() { grep -vx "$1" "$FAKE_DIR/active" > "$FAKE_DIR/active.t"; mv -f "$FAKE_DIR/active.t" "$FAKE_DIR/active"; }
leg_off_fake kaskad-leg@kawg3; echo kaskad-leg@kawg3 > "$FAKE_DIR/jobs"; : > "$CALLS/systemctl.log"; : > "$CALLS/ip.log"
o=$(lib egress_awg_heal; echo "RC=$?")
printf '%s' "$o" | grep -q 'RC=1' && ! grep -qE '^(restart|start|stop|reset-failed).*kaskad-leg@kawg3' "$CALLS/systemctl.log" \
  && grep -qx 'list-jobs --no-legend kaskad-leg@kawg3.service' "$CALLS/systemctl.log" && [ "$(nrule 0x103 259)" = 1 ] \
  && ip -4 route show table 259 | grep -qx 'unreachable default metric 1000' \
  && ok "heal после загрузки: у юнита ноги задание systemd — не перезапускаю (поднимет systemd), нога закрыта до подъёма" \
  || bad "heal поверх задания systemd" "$o | $(cat "$CALLS/systemctl.log")"
rm -f "$FAKE_DIR/jobs"
# Пока heal решал режим ноги (kaskadctl gw accel check — секунды), systemd поднял её сам: только маршруты, без restart.
: > "$CALLS/systemctl.log"
t_race() {
    egress_awg_leg_mode() {
        ip link add kawg3 type tun >/dev/null 2>&1
        grep -qx kaskad-leg@kawg3 "$FAKE_DIR/active" || echo kaskad-leg@kawg3 >> "$FAKE_DIR/active"
        echo userspace
    }
    egress_awg_heal; echo "RC=$?"
}
o=$(lib t_race)
printf '%s' "$o" | grep -q 'RC=0' && ! grep -qE '^restart kaskad-leg@kawg3' "$CALLS/systemctl.log" \
  && [ "$(ip -4 route show table 259 | sort | tr '\n' '|')" = "default dev kawg3|unreachable default metric 1000|" ] \
  && ok "heal: ногу поднял systemd, пока решался режим — не пересоздана (lazy), маршрут через неё на месте" \
  || bad "heal пересоздал только что поднятую ногу" "$o | $(cat "$CALLS/systemctl.log")"
leg_off_fake kaskad-leg@kawg3
ip link del kawg3
t_nocore() { awg_go_bin() { return 1; }; awg_tool_bin() { return 1; }; egress_awg_heal; echo "RC=$?"; }
o=$(lib t_nocore)
printf '%s' "$o" | grep -q 'RC=1' && ! ip link show dev kawg3 >/dev/null 2>&1 && [ "$(nrule 0x103 259)" = 1 ] \
  && [ "$(ip -4 route show table 259)" = "unreachable default metric 1000" ] \
  && ok "ядра AmneziaWG нет — нога остаётся закрытой (правило + unreachable, без утечки), код 1" || bad "heal без ядра" "$o"
lib egress_awg_heal >/dev/null 2>&1
networkd_restart
( cd "$KASKAD_ROOT" && bash "$S" --egress-awg-heal </dev/null >/dev/null 2>&1 ); rc=$?
[ "$rc" = 0 ] && [ "$(nrule 0x102 258)" = 1 ] && ip -4 route show table 258 | grep -qx 'unreachable default metric 1000' \
  && ok "режим gokaskad --egress-awg-heal (его зовёт сторож шлюза)" || bad "--egress-awg-heal" "rc=$rc $(ip -4 rule show)"
heal_root heal2 no-networkd
ip link add kawg2 type none
lib egress_awg_heal >/dev/null 2>&1
[ ! -e "$KASKAD_ROOT/etc/systemd/networkd.conf.d" ] && [ "$(nrule 0x102 258)" = 1 ] \
  && ok "нет systemd-networkd — файл не пишется, нога чинится как обычно" || bad "без networkd" "$(ls -R "$KASKAD_ROOT/etc/systemd" 2>&1)"
# Подъём ног — тот же код: правило ставится один раз, а не «снять и поставить» (было окно без правила);
# одна нога поднимается одной функцией и при подъёме всех, и в heal.
body=$(sed -n '/^egress_awg_sync() {/,/^}/p' "$S"); one=$(sed -n '/^egress_awg_leg_up() {/,/^}/p' "$S")
hb=$(sed -n '/^egress_awg_heal() {/,/^}/p' "$S"); rl=$(sed -n '/^egress_awg_route_leg() {/,/^}/p' "$S")
printf '%s\n' "$body" | grep -q 'egress_awg_route_leg "$mark" "$table"$' && printf '%s\n' "$body" | grep -q '^ *egress_awg_leg_up ' \
  && printf '%s\n' "$one" | grep -q 'egress_awg_route_leg "$mark" "$table" "$iface"' \
  && printf '%s\n' "$hb" | grep -q 'egress_awg_leg_up ' && printf '%s\n' "$hb" | grep -q 'egress_awg_route_leg "$mark" "$table" "$iface"' \
  && [ "$(grep -v '^[[:space:]]*#' "$S" | grep -c 'ip rule add')" = 2 ] \
  && [ "$(printf '%s\n' "$rl" | grep -v '^[[:space:]]*#' | grep -c 'ip rule add')" = 2 ] \
  && ok "подъём ног и heal — одни функции: маршрутизация ноги, подъём одной ноги (ip rule add — только в egress_awg_route_leg: «fwmark → таблица» и закрытие метки при отказе Л2, REV-mip-s34 С-4)" || bad "общая функция" "$body"
heal_root sync1
t_sync() { awg_go_bin() { return 1; }; awg_tool_bin() { return 1; }; egress_awg_sync; egress_awg_sync; }   # без ядра
lib t_sync >/dev/null 2>&1
[ "$(nrule 0x102 258)" = 1 ] && [ "$(nrule 0x103 259)" = 1 ] && [ "$(ip -4 route show table 258)" = "unreachable default metric 1000" ] \
  && ! grep -q 'rule del' "$CALLS/ip.log" && [ -f "$KASKAD_ROOT$DROPIN_REL" ] \
  && ok "подъём ног без ядра: правило и unreachable (нога закрыта), дважды — без копий и без снятия; файл networkd" || bad "egress_awg_sync" "$(cat "$CALLS/ip.log")"
# Удаление: файл уходит, только когда ни ног, ни их правил, ни правила шлюза не осталось.
heal_root rel1
t_rel() { k3_networkd_keep_foreign; k3_networkd_release; [ -e "$K3_NETWORKD_DROPIN" ] && echo KEPT || echo GONE; }
ip link add kawg2 type none
a1=$(lib t_rel 2>&1); ip link del kawg2; ip rule add fwmark 258 table 258
a2=$(lib t_rel 2>&1); ip rule del fwmark 258 table 258; ip rule add fwmark 0x200 table 200
a3=$(lib t_rel 2>&1); ip rule del fwmark 0x200 table 200
a4=$(lib t_rel 2>&1)
[ "$a1 $a2 $a3 $a4" = "KEPT KEPT KEPT GONE" ] && ok "k3_networkd_release: нога kawg2, правило ноги, правило шлюза — файл остаётся; ничего — удалён" || bad "release" "$a1 $a2 $a3 $a4"
# Обновление с версии без файла: ноги уже стоят — файл сразу (--k3-post-update), а не при перезапуске egress.
heal_root post1
lib k3_post_update >/dev/null 2>&1
heal_root post2; echo "$VLESS_KEY" > "$KASKAD_DIR/egress/upstream.txt"
lib k3_post_update >/dev/null 2>&1
[ -f "$W/root-post1$DROPIN_REL" ] && [ ! -e "$W/root-post2$DROPIN_REL" ] \
  && ok "--k3-post-update: в пуле ключи AmneziaWG — файл networkd пишется сразу; только VLESS — нет" || bad "post-update и networkd"
# 3.3.4, стенд T-334 (54b3d65): парсер пула 3.3.3 (v14) на узле без ключей allowInsecure — снятие отпечатков его не
# переписывает (снимать нечего), и раньше он оставался до правки пула: ключ с pcs шёл в xray без закрепления, выход
# мёртв. --k3-post-update пишет разборщик этой версии первым делом. Старый — из K3_OLD_INSTALL (git show 6a6c410), иначе
# подделка: номер 14 и без pinnedPeerCertSha256 (pcs 3.3.3 не знал).
use_root ppars; mkdir -p "$KASKAD_DIR/egress"; chmod 700 "$KASKAD_DIR/egress"
EP="$KASKAD_DIR/egress/egress_parse.py"
if [ -n "${K3_OLD_INSTALL:-}" ] && [ -f "$K3_OLD_INSTALL" ]; then
    (S="$K3_OLD_INSTALL"; lib egress_ensure_parser) >/dev/null 2>&1
else
    lib egress_ensure_parser >/dev/null 2>&1
    python3 - "$EP" <<'PY'
import re, sys
p = sys.argv[1]; t = open(p).read()
t = re.sub(r"^PARSER_VERSION = \d+", "PARSER_VERSION = 14", t, count=1, flags=re.M)
t = t.replace('\nif __name__=="__main__":', '\n_vo_new = vless_outbound\ndef vless_outbound(*a, **kw):\n'
              '    ob = _vo_new(*a, **kw)\n    (ob.get("streamSettings") or {}).get("tlsSettings", {}).pop("pinnedPeerCertSha256", None)\n'
              '    return ob\n\nif __name__=="__main__":', 1)
open(p, "w").write(t)
PY
fi
PCSK='vless://55555555-6666-7777-8888-999999999999@198.51.100.24:8443?type=tcp&security=tls&sni=a.example.com&pcs=6fbc15ba46dfed152ad6c8d2129dd774707dd667a9ab4965476fa0f79ba82670#Pcs'
printf '%s\n' "$PCSK" > "$KASKAD_DIR/egress/upstream.txt"; chmod 600 "$KASKAD_DIR/egress/upstream.txt"
pv_old=$(sed -n 's/^PARSER_VERSION = \([0-9]*\).*/\1/p' "$EP")
c_old=$(python3 "$EP" config < "$KASKAD_DIR/egress/upstream.txt" 2>/dev/null)
lib k3_post_update >/dev/null 2>&1
pv_new=$(sed -n 's/^PARSER_VERSION = \([0-9]*\).*/\1/p' "$EP")
c_new=$(python3 "$EP" config < "$KASKAD_DIR/egress/upstream.txt" 2>/dev/null)
pv_cur=$(sed -n 's/^PARSER_VERSION = \([0-9]*\).*/\1/p' "$S" | head -1)
[ "$pv_old" = 14 ] && ! printf '%s' "$c_old" | grep -q pinnedPeerCertSha256 && [ "$pv_new" = "$pv_cur" ] \
  && printf '%s' "$c_new" | grep -q '"pinnedPeerCertSha256": "6fbc15ba46dfed152ad6c8d2129dd774707dd667a9ab4965476fa0f79ba82670"' \
  && [ ! -e "$KASKAD_DIR/egress/tofu-pins.json" ] && [ "$(perm "$EP")" = 600 ] \
  && ok "--k3-post-update: парсер пула 3.3.3 (v14) без ключей allowInsecure → v$pv_cur, ключ с pcs — с pinnedPeerCertSha256" \
  || bad "post-update: парсер пула остался старым" "old=$pv_old new=$pv_new cur=$pv_cur"
post_body=$(sed -n '/^k3_post_update() {/,/^}/p' "$S")
pl() { printf '%s\n' "$post_body" | grep -n "$1" | head -1 | cut -d: -f1; }
[ -n "$(pl 'egress_ensure_parser')" ] && [ "$(pl 'egress_ensure_parser')" -lt "$(pl 'egress_tofu_post_update')" ] \
  && [ "$(pl 'egress_ensure_parser')" -lt "$(pl 'k3ctl gw apply')" ] && [ "$(pl 'egress_ensure_parser')" -lt "$(pl 'egress_legs_mtu_refresh')" ] \
  && ok "k3_post_update: разборщик пула — до снятия отпечатков, gw apply и MTU ног" || bad "k3_post_update: порядок разборщика"
# Узел без «Прокси для API Telegram» (нет /etc/kaskad/egress) — каталог не создаётся.
use_root ppars2; lib k3_post_update >/dev/null 2>&1
[ ! -e "$KASKAD_DIR/egress" ] && ok "--k3-post-update без «Прокси для API Telegram»: каталог egress не создаётся" || bad "post-update создал egress"

echo "── 16b. ноги AmneziaWG: юниты kaskad-leg@, линия 2.0 на модуле ядра (ускорение), «голые» ноги, миграция ──"
# Находка критика (2026-10-05): ноги жили в cgroup того, кто их поднял (xray бота, сборщик шлюза, сторож-oneshot) —
# их перезапуск рвал выходы шлюза. Теперь у ноги свой юнит kaskad-leg@kawgN; ускорение (kaskadctl gw accel) ставит
# ноги линии 2.0 на модуль ядра — drop-in kernel.conf того же юнита. Ключи разных линий — тем же разборщиком:
# 2.0 (S3/S4, H-диапазоны, I1), 3.1 (HeaderProtectionKey), 1.0; «bad» — 2.0 с Jmin > Jmax (у модуля паника, #254).
mk_awg_line() {
    python3 - "$KASKAD_DIR/egress/egress_parse.py" "$1" "$2" <<'PY'
import base64, importlib.util, json, sys
spec = importlib.util.spec_from_file_location("egress_parse", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
n, line = int(sys.argv[2]), sys.argv[3]
key = lambda s: base64.b64encode(bytes((s * 7 + i) % 256 for i in range(32))).decode()
i1 = "<r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>"
h20 = "H1 = 1827480060-1827483990\nH2 = 447982126-447984206\nH3 = 717615779-717619267\nH4 = 398175212-398180108\n"
p = {"1.0": "Jc = 4\nJmin = 40\nJmax = 70\nS1 = 116\nS2 = 61\nH1 = 1234567801\nH2 = 1234567802\nH3 = 1234567803\n"
            "H4 = 1234567804\n",
     "2.0": "Jc = 4\nJmin = 10\nJmax = 50\nS1 = 106\nS2 = 123\nS3 = 18\nS4 = 117\n" + h20 + "I1 = %s\n" % i1,
     "bad": "Jc = 4\nJmin = 80\nJmax = 50\nS1 = 106\nS2 = 123\nS3 = 18\nS4 = 117\n" + h20,
     "3.1": "Jc = 5\nJmin = 10\nJmax = 50\nS1 = 12\nS2 = 12\nS3 = 12\nS4 = 12\nH1 = 1\nH2 = 2\nH3 = 3\nH4 = 4\n"
            "HeaderProtectionKey = %s\nRandomTrailers = on\nDisableCookies = on\n" % key(77)}[line]
conf = ("[Interface]\nAddress = 10.8.%d.5/32\nPrivateKey = %s\n%s\n[Peer]\nPublicKey = %s\nAllowedIPs = 0.0.0.0/0\n"
        "Endpoint = 198.51.100.%d:51820\nPersistentKeepalive = 25\n") % (n, key(n), p, key(100 + n), 30 + n)
print("awg://" + m._b64e(json.dumps(m.awg_from_conf(conf, "Leg%d" % n))))
PY
}
# Пул: VLESS, kawg2 — линия $2 (2.0 или bad), kawg3 — 3.1, kawg4 — 1.0. kaskadctl — настоящая проверка конфига
# ноги (python3 -m kaskad gw accel check), остальное он не умеет.
kmod_root() {
    use_root "$1"; rm -f "$FAKE_DIR/ip.json"
    : > "$CALLS/ip.log"; : > "$CALLS/pkill.log"; : > "$CALLS/core.log"; : > "$CALLS/systemctl.log"; : > "$CALLS/kaskadctl.log"
    lib egress_ensure_parser >/dev/null 2>&1
    { echo "$VLESS_KEY"; mk_awg_line 2 "${2:-2.0}"; mk_awg_line 3 3.1; mk_awg_line 4 1.0; } > "$KASKAD_DIR/egress/upstream.txt"
    local d="$KASKAD_DIR/egress/awg"; mkdir -p "$d" "$K3_OPT/bin"
    printf '#!/usr/bin/env bash\necho "go $*" >> "$CALLS/core.log"\n[ "$1" = -f ] && shift\nip link add "$1" type tun\n' > "$d/amneziawg-go"
    printf '#!/usr/bin/env bash\necho "awg $1 $2" >> "$CALLS/core.log"\n' > "$d/awg"
    cat > "$K3_OPT/bin/kaskadctl" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "\$CALLS/kaskadctl.log"
[ "\${1:-} \${2:-} \${3:-}" = "gw accel check" ] || exit 2
PYTHONPATH="$ROOT_DIR/lib" exec python3 -m kaskad gw accel check "\$4"
EOF
    chmod 755 "$d/amneziawg-go" "$d/awg" "$K3_OPT/bin/kaskadctl"
    U="$KASKAD_ROOT/etc/systemd/system"; T="$U/kaskad-leg@.service"; A="$KASKAD_DIR/egress/awg"
}
accel_state() { mkdir -p "$KASKAD_DIR/gw"; printf '{\n  "enabled": %s,\n  "tag": "v3.1.20260906"\n}\n' "$1" > "$KASKAD_DIR/gw/awg-kmod.json"; }
live() {
    if ip -d -o link show dev "$1" 2>/dev/null | grep -qw amneziawg; then echo kernel
    elif ip link show dev "$1" >/dev/null 2>&1; then echo userspace
    else echo none; fi
}
modes() { echo "$(live kawg2) $(live kawg3) $(live kawg4)"; }
kmod_root kmod1
lib egress_awg_sync >/dev/null 2>&1
[ "$(modes)" = "userspace userspace userspace" ] && grep -qx 'go -f kawg2' "$CALLS/core.log" && grep -qx 'go -f kawg4' "$CALLS/core.log" \
  && [ "$(grep -c '^restart kaskad-leg@kawg[234].service$' "$CALLS/systemctl.log")" = 3 ] && [ ! -e "$U/kaskad-leg@kawg2.service.d" ] \
  && ! grep -q accel "$CALLS/kaskadctl.log" && ! grep -q amneziawg "$CALLS/modprobe.log" 2>/dev/null \
  && ok "ускорение выключено: все ноги — amneziawg-go под своими юнитами kaskad-leg@, модуль и проверка не спрашиваются" \
  || bad "ноги без ускорения" "$(modes) | $(cat "$CALLS/systemctl.log") | $(cat "$CALLS/core.log")"
grep -qx "ExecStart=$A/amneziawg-go -f %i" "$T" && grep -qx 'Restart=on-failure' "$T" && grep -qx 'Before=kaskad-egress.service' "$T" \
  && grep -qE '^ExecStartPre=-[^ ]+/gokaskad --egress-awg-routes %i$' "$T" \
  && [ "$(grep -A1 -- '--egress-awg-routes %i$' "$T" | tail -1)" = "ExecStartPre=$(sed -n 's/^ExecStartPre=-\([^ ]*\) --egress-awg-routes %i$/\1/p' "$T") --egress-awg-conf %i" ] \
  && grep -qE '^ExecStopPost=-[^ ]+ link delete dev %i$' "$T" && grep -qx "EnvironmentFile=$A/.%i.env" "$T" \
  && grep -qx "ExecStartPost=$A/awg setconf %i $A/.%i.conf" "$T" && grep -qE '^ExecStartPost=[^ ]+ route replace default dev %i table \$\{TABLE\}$' "$T" \
  && grep -qx 'WantedBy=multi-user.target' "$T" && grep -qx 'enable --no-reload kaskad-leg@kawg2.service' "$CALLS/systemctl.log" \
  && ok "шаблон kaskad-leg@.service: amneziawg-go -f %i, Restart=on-failure, конфиг заново при старте (--egress-awg-conf), ExecStopPost убирает только свою ногу, до xray бота, включён" \
  || bad "шаблон юнита ног" "$(cat "$T" 2>&1)"
[ "$(cat "$A/.kawg2.env")" = "$(printf 'ADDR=10.8.2.5/32\nTABLE=258\nMTU=1280')" ] && [ "$(perm "$A/.kawg2.env")" = 600 ] && [ "$(perm "$A/.kawg2.conf")" = 600 ] \
  && grep -q '^S3 = 18$' "$A/.kawg2.conf" && ok "файлы ноги: .kawg2.env (ADDR, TABLE, MTU 1280 — карта 1500, S4 = 117) и .kawg2.conf, 0600" || bad "файлы ноги" "$(cat "$A/.kawg2.env")"
# §6.1: MTU ноги — по карте сервера (маршрут по умолчанию) и S4 ключа; юнит берёт его из env, 1200 больше не вписан
mkdir -p "$KASKAD_ROOT/proc/net" "$KASKAD_ROOT/sys/class/net/ens3"
printf 'Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\nens3\t00000000\t0102A8C0\t0003\t0\t0\t0\t00000000\n' > "$KASKAD_ROOT/proc/net/route"
legmtu() { echo "$1" > "$KASKAD_ROOT/sys/class/net/ens3/mtu"; lib egress_awg_leg_files 2 kawg2 10.8.2.5/32 258 >/dev/null 2>&1; sed -n 's/^MTU=//p' "$A/.kawg2.env"; }
m1=$(legmtu 1450); m2=$(legmtu 1380); m3=$(legmtu 9000)
rm -f "$KASKAD_ROOT/proc/net/route"; m4=$(legmtu 1500); rm -rf "$KASKAD_ROOT/sys/class/net/ens3"
[ "$m1 $m2 $m3 $m4" = "1248 1232 1280 1280" ] && grep -qE "^ExecStartPost=/bin/sh -c 'exec [^ ]+ link set dev %i mtu [$][$][{]MTU:-1280[}] up'$" "$T" \
  && ! grep -q 'mtu 1200' "$T" \
  && ok "MTU ноги по карте (1450 → 1248, 1380 → 1232, 9000 → 1280, без карты → 1280); юнит — \${MTU} из env" \
  || bad "MTU ноги по карте" "$m1 $m2 $m3 $m4 | $(grep mtu "$T")"
lib egress_awg_leg_files 2 kawg2 10.8.2.5/32 258 >/dev/null 2>&1
: > "$CALLS/systemctl.log"; lib egress_awg_sync >/dev/null 2>&1
! grep -qE '^(restart|stop|disable)' "$CALLS/systemctl.log" && [ "$(modes)" = "userspace userspace userspace" ] \
  && ok "повторный подъём ног (смена ключей VLESS): ничего не изменилось — ни одна нога не перезапущена" || bad "лишний перезапуск ног" "$(cat "$CALLS/systemctl.log")"
# включили ускорение (kaskadctl gw accel on): EGRESS_AWG_REMODE=1 --egress-awg-heal — пересоздаётся только нога 2.0
accel_state true; touch "$FAKE_DIR/kmod_available"; : > "$CALLS/systemctl.log"; : > "$CALLS/core.log"
EGRESS_AWG_REMODE=1 lib egress_awg_heal >/dev/null 2>&1
D="$U/kaskad-leg@kawg2.service.d/kernel.conf"
[ "$(modes)" = "kernel userspace userspace" ] && grep -qx 'kernel kawg2' "$CALLS/core.log" && grep -qx 'awg setconf kawg2' "$CALLS/core.log" \
  && grep -qx 'restart kaskad-leg@kawg2.service' "$CALLS/systemctl.log" && ! grep -qE '^restart kaskad-leg@kawg[34]' "$CALLS/systemctl.log" \
  && grep -q "gw accel check $A/.kawg2.conf" "$CALLS/kaskadctl.log" && grep -q "gw accel check $A/.kawg4.conf" "$CALLS/kaskadctl.log" \
  && grep -q "gw accel check $A/.kawg3.conf" "$CALLS/kaskadctl.log" \
  && ok "ускорение включено, модуль без набора: нога 2.0 (kawg2) пересоздана на модуле ядра; 3.1 и 1.0 (проверка отказала: нет набора) не тронуты" \
  || bad "ноги и ускорение" "$(modes) | $(cat "$CALLS/systemctl.log") | $(cat "$CALLS/kaskadctl.log")"
grep -qx 'Type=oneshot' "$D" && grep -qx 'RemainAfterExit=yes' "$D" && grep -qx 'ExecStart=' "$D" \
  && grep -qE '^ExecStart=[^ ]+ link add dev %i type amneziawg$' "$D" && grep -qx "ExecStartPost=$A/awg setconf %i $A/.%i.conf" "$D" \
  && ok "нога на модуле: drop-in kernel.conf того же юнита — oneshot, RemainAfterExit, ip link add … type amneziawg, awg setconf" \
  || bad "drop-in ноги на модуле" "$(cat "$D" 2>&1)"
: > "$CALLS/systemctl.log"; lib egress_awg_sync >/dev/null 2>&1
! grep -q '^restart' "$CALLS/systemctl.log" && [ "$(modes)" = "kernel userspace userspace" ] \
  && ok "--egress-awg-up при включённом ускорении: нога уже на модуле — не перезапускается" || bad "подъём ног на модуле" "$(cat "$CALLS/systemctl.log")"
accel_state false; : > "$CALLS/systemctl.log"
lib egress_awg_heal >/dev/null 2>&1
[ "$(live kawg2)" = kernel ] && ! grep -q '^restart' "$CALLS/systemctl.log" \
  && ok "ускорение выключено, обычный heal: работающая нога не трогается (смена режима — только по EGRESS_AWG_REMODE)" || bad "heal тронул ногу" "$(cat "$CALLS/systemctl.log")"
EGRESS_AWG_REMODE=1 lib egress_awg_heal >/dev/null 2>&1
[ "$(modes)" = "userspace userspace userspace" ] && [ ! -e "$D" ] && grep -qx 'restart kaskad-leg@kawg2.service' "$CALLS/systemctl.log" \
  && ! grep -qE '^restart kaskad-leg@kawg[34]' "$CALLS/systemctl.log" \
  && ok "kaskadctl gw accel off (REMODE): нога 2.0 обратно на amneziawg-go, drop-in убран, другие ноги не тронуты" \
  || bad "выключение ускорения для ног" "$(modes) | $(cat "$CALLS/systemctl.log")"
accel_state true; rm -f "$FAKE_DIR/kmod_available"; rm -rf "$KASKAD_ROOT/sys/module"; : > "$CALLS/systemctl.log"
EGRESS_AWG_REMODE=1 lib egress_awg_heal >/dev/null 2>&1
[ "$(live kawg2)" = userspace ] && ! grep -q '^restart' "$CALLS/systemctl.log" && grep -qx amneziawg "$CALLS/modprobe.log" \
  && ok "ускорение включено, но модуля под это ядро нет (modprobe не смог): нога 2.0 остаётся на amneziawg-go" || bad "откат ноги без модуля" "$(live kawg2)"
kmod_root kmod2 bad; accel_state true; touch "$FAKE_DIR/kmod_available"
lib egress_awg_sync >/dev/null 2>&1
[ "$(live kawg2)" = userspace ] && [ ! -e "$U/kaskad-leg@kawg2.service.d/kernel.conf" ] && grep -q 'gw accel check' "$CALLS/kaskadctl.log" \
  && ok "ключ 2.0 с Jmin > Jmax (паника модуля, #254): kaskadctl gw accel check отказал — нога на amneziawg-go" || bad "опасный ключ в ядро" "$(live kawg2)"
kmod_root kmod3; accel_state true; touch "$FAKE_DIR/kmod_available" "$FAKE_DIR/kmod_fail"
lib egress_awg_sync >/dev/null 2>&1
[ "$(live kawg2)" = userspace ] && [ ! -e "$U/kaskad-leg@kawg2.service.d/kernel.conf" ] && grep -qx 'go -f kawg2' "$CALLS/core.log" \
  && grep -q 'kawg2 (AmneziaWG 2.0) — модуль ядра не поднял ногу, она на amneziawg-go' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "модуль не поднял ногу: сразу amneziawg-go под тем же юнитом, строка в журнале" || bad "откат ноги" "$(live kawg2) | $(cat "$CALLS/systemctl.log")"
kmod_root kmod31; accel_state true; touch "$FAKE_DIR/kmod_available"
mkdir -p "$KASKAD_ROOT/sys/module/amneziawg"
printf '3.1.20260812+%s\n' "$(sed -n 's/^EGRESS_AWG_KMOD_SET="\([^"]*\)".*$/\1/p' "$S")" > "$KASKAD_ROOT/sys/module/amneziawg/version"
lib egress_awg_sync >/dev/null 2>&1
[ "$(modes)" = "kernel kernel kernel" ] && [ -e "$U/kaskad-leg@kawg3.service.d/kernel.conf" ] \
  && [ -e "$U/kaskad-leg@kawg4.service.d/kernel.conf" ] && grep -q "gw accel check $A/.kawg3.conf" "$CALLS/kaskadctl.log" \
  && ok "модуль с набором (метка в версии): ноги 2.0, 3.1 и 1.0 — все на модуле ядра" \
  || bad "нога 3.1 на модуле с набором" "$(modes) | $(cat "$CALLS/kaskadctl.log")"
rm -rf "$KASKAD_ROOT/sys/module"
kmod_root kmod4
ip link add kawg2 type tun                                 # «голая» нога прошлой версии: интерфейс есть, юнита нет
lib egress_awg_heal >/dev/null 2>&1
grep -qx 'restart kaskad-leg@kawg2.service' "$CALLS/systemctl.log" && grep -qxF -- '-f amneziawg-go kawg2$' "$CALLS/pkill.log" \
  && grep -q 'kawg2 — нога без юнита, перевожу под kaskad-leg@kawg2.service' "$KASKAD_ROOT/var/log/kaskad.log" \
  && [ "$(modes)" = "userspace userspace userspace" ] \
  && ok "heal: «голая» нога (без юнита) один раз переведена под kaskad-leg@ — её процесс ищется по имени целиком" \
  || bad "«голая» нога" "$(cat "$CALLS/systemctl.log") | $(cat "$CALLS/pkill.log")"
: > "$CALLS/systemctl.log"; lib egress_awg_heal >/dev/null 2>&1
! grep -q '^restart' "$CALLS/systemctl.log" && ok "второй heal: ноги под юнитами — ничего не перезапускается" || bad "второй heal" "$(cat "$CALLS/systemctl.log")"
: > "$CALLS/systemctl.log"; lib egress_awg_teardown >/dev/null 2>&1
[ "$(modes)" = "none none none" ] && [ ! -e "$T" ] && [ -z "$(ls -A "$A" 2>/dev/null | grep -E '^\.kawg')" ] \
  && grep -qx 'disable --no-reload --now kaskad-leg@kawg2.service' "$CALLS/systemctl.log" && [ -z "$(ip -4 rule show | grep fwmark)" ] \
  && ok "снятие ног (пул опустел, удаление Kaskad): юниты выключены, шаблон, файлы ног, правила и интерфейсы убраны" \
  || bad "снятие ног" "$(modes) | $(ls -A "$A") | $(cat "$CALLS/systemctl.log")"
# Обновление 3.1.0 → 3.2: служба xray бота поднимала ноги (ExecStartPre/ExecStopPost), ноги — «голые» процессы.
kmod_root kmod5
C="$K3_OPT/bin/gokaskad"
printf '[Service]\nExecStartPre=-%s --egress-awg-up\nExecStart=/etc/kaskad/egress/xray run -c /etc/kaskad/egress/config.json\nExecStopPost=-%s --egress-awg-down\n' "$C" "$C" > "$U/kaskad-egress.service"
ip link add kawg2 type tun; ip link add kawg3 type tun; ip link add kawg4 type tun
printf 'kawg2\nkawg3\nkawg4\n' > "$FAKE_DIR/naked"
bash "$S" --k3-post-update </dev/null >/dev/null 2>&1
: > "$FAKE_DIR/naked"
! grep -qE -- '--egress-awg-(up|down)' "$U/kaskad-egress.service" && grep -q '^ExecStart=/etc/kaskad/egress/xray' "$U/kaskad-egress.service" \
  && grep -qx "ExecStartPre=-$C --egress-awg-routes" "$U/kaskad-egress.service" \
  && [ "$(grep -c '^restart kaskad-leg@kawg[234].service$' "$CALLS/systemctl.log")" = 3 ] && [ "$(wc -l < "$CALLS/pkill.log")" -ge 3 ] \
  && ! grep -qE '^(restart|stop) kaskad-egress' "$CALLS/systemctl.log" \
  && ok "--k3-post-update: строки ног убраны из службы xray бота, «голые» ноги переведены под kaskad-leg@ (xray не перезапускался)" \
  || bad "миграция ног" "$(cat "$U/kaskad-egress.service") | $(cat "$CALLS/systemctl.log")"
: > "$CALLS/systemctl.log"; bash "$S" --k3-post-update </dev/null >/dev/null 2>&1
! grep -qE '^restart kaskad-leg' "$CALLS/systemctl.log" && ok "повторный --k3-post-update (и каждый запуск бота): «голых» ног нет — ноги не трогаются" || bad "повторная миграция" "$(cat "$CALLS/systemctl.log")"
# --egress-awg-routes: ExecStartPre ноги и xray бота — только правило и unreachable, интерфейсы не трогаются
kmod_root kmod6; : > "$CALLS/ip.log"
lib egress_awg_routes kawg3 >/dev/null 2>&1
[ "$(nrule 0x103 259)" = 1 ] && [ "$(nrule 0x102 258)" = 0 ] && [ "$(ip -4 route show table 259)" = "unreachable default metric 1000" ] \
  && ! grep -qE '^link (add|del|set)' "$CALLS/ip.log" && : > "$CALLS/systemctl.log" \
  && ok "--egress-awg-routes kawg3: правило и unreachable только своей ноги, интерфейсы и юниты не тронуты" || bad "--egress-awg-routes ноги" "$(ip -4 rule show)"
( cd "$KASKAD_ROOT" && bash "$S" --egress-awg-routes </dev/null >/dev/null 2>&1 ); rc=$?
[ "$rc" = 0 ] && [ "$(nrule 0x102 258)" = 1 ] && [ "$(nrule 0x103 259)" = 1 ] && [ "$(nrule 0x104 260)" = 1 ] \
  && ! grep -qE '^(restart|start|stop)' "$CALLS/systemctl.log" \
  && ok "режим gokaskad --egress-awg-routes: правила всех ног пула, ничего не перезапускается" || bad "--egress-awg-routes" "rc=$rc $(ip -4 rule show)"
sed -n '/^full_uninstall() {/,/^}/p' "$S" | grep -qx '    egress_awg_teardown' \
  && sed -n '/^egress_disable() {/,/^}/p' "$S" | grep -qx '    if k3_gw_enabled 2>/dev/null; then egress_awg_sync; else egress_awg_teardown; fi' \
  && ok "удаление Kaskad (меню 15) и опустевший пул снимают ноги и их юниты — и без службы xray бота (T15 Q4: при включённом шлюзе — только ноги строк, которых нет ни в одном файле)" || bad "юниты ног при удалении"
pl=$(PYTHONPATH="$ROOT_DIR/lib" python3 -c 'from kaskad.gw import awgkmod; print(" ".join(awgkmod.LEG_LINES))')
[ "$pl" = "$(sed -n 's/^EGRESS_AWG_KMOD_LINES="\(.*\)"$/\1/p' "$S")" ] && ok "линии ног на модуле в install.sh и в awgkmod.LEG_LINES одни ($pl)" || bad "линии ног разошлись" "$pl"
sid=$(PYTHONPATH="$ROOT_DIR/lib" python3 -c 'from kaskad.gw import awgkmod; print(awgkmod.SET_ID)')
[ "$sid" = "$(sed -n 's/^EGRESS_AWG_KMOD_SET="\([^"]*\)".*$/\1/p' "$S")" ] \
  && grep -qF "+$sid\"" "$ROOT_DIR/lib/kaskad/vendor/awg-kmod-31.patch" \
  && ok "метка набора 3.1 одна в install.sh, awgkmod.SET_ID и в патче ($sid)" || bad "метка набора 3.1 разошлась" "$sid"
mkdir -p "$KASKAD_ROOT/sys/module/amneziawg"; printf '3.1.20260812\n' > "$KASKAD_ROOT/sys/module/amneziawg/version"
r1=$(KASKAD_ROOT="$KASKAD_ROOT" bash -c "source <(sed -n '/^EGRESS_AWG_KMOD_SET=/p;/^egress_awg_kmod_has31()/p' '$S'); egress_awg_kmod_has31 && echo yes || echo no")
printf '3.1.20260812+%s\n' "$sid" > "$KASKAD_ROOT/sys/module/amneziawg/version"
r2=$(KASKAD_ROOT="$KASKAD_ROOT" bash -c "source <(sed -n '/^EGRESS_AWG_KMOD_SET=/p;/^egress_awg_kmod_has31()/p' '$S'); egress_awg_kmod_has31 && echo yes || echo no")
rm -rf "$KASKAD_ROOT/sys/module/amneziawg"
[ "$r1/$r2" = "no/yes" ] && ok "egress_awg_kmod_has31: модуль без метки — нет, с меткой — да" || bad "egress_awg_kmod_has31" "$r1/$r2"
sed -n '/^k3_post_update() {/,/^}/p' "$S" | grep -q 'systemd-run --unit=kaskad-awgkmod-upgrade' \
  && sed -n '/^k3_post_update() {/,/^}/p' "$S" | grep -q 'gw accel upgrade --quiet' \
  && ok "k3_post_update пересобирает модуль без набора 3.1 во временном юните (gw accel upgrade)" || bad "k3_post_update без upgrade"
cp "$W/ip-noop" "$W/bin/ip"

# (новую пометку cut — только после сверки строк PASS частями и одним скриптом: tools/k3_install_par.py -j 3 и -j 1)
# k3-install-par: cut — tools/k3_install_par.py может начать часть здесь (разделам ниже хватает функций и значений выше)
echo "── 16c. веб-панель: ключ выхода (--egress-add из stdin, --egress-del с отпечатком), блокировка пула ──"
# 3.2.22 (O, 2026-10-08): блокировки пула и старого движка не закрывают /run/kaskad — через него nginx (www-data) идёт к
# сокету панели (3.2.21 делал его 0700 при каждом изменении пула — панель на домене отвечала 502); закрытому — проход
mode_of() { python3 -c 'import os, sys; print("%04o" % (os.stat(sys.argv[1]).st_mode & 0o7777))' "$1"; }
t_rundir() {
    local d="$KASKAD_ROOT/run/kaskad"
    if [ "$1" = new ]; then rm -rf "$d"; else mkdir -p "$d"; chmod "$1" "$d"; fi
    egress_pool_locked true; k3_legacy_locked true >/dev/null 2>&1
    mode_of "$d"
}
use_root rundir
r1=$(lib t_rundir 755); r2=$(lib t_rundir 700); r3=$(lib t_rundir new)
[ "$r1" = 0755 ] && [ "$r2" = 0711 ] && [ "$r3" = 0711 ] \
  && ok "блокировки пула и старого движка: /run/kaskad не закрывается (0755 остаётся, 0700 → 0711, новый — 0711)" \
  || bad "/run/kaskad после блокировок" "0755→$r1 0700→$r2 новый→$r3"
# Панель (kaskad.gw.ops.exit_add / exit_del) добавляет и удаляет ключ тем же путём, что SSH-меню: egress_add_key /
# egress_remove_key, разборщик egress_parse.py, пересборка (здесь — подделка: журнал), без проверки связи.
# Ключи — заведомо поддельные (документационные адреса 198.51.100.0/24).
awg_conf() {   # текст файла AmneziaWG .conf номер $1 (как из приложения Amnezia)
    python3 - "$1" <<'PY'
import base64, sys
n = int(sys.argv[1])
key = lambda s: base64.b64encode(bytes((s * 7 + i) % 256 for i in range(32))).decode()
print(("[Interface]\nAddress = 10.8.%d.5/32\nPrivateKey = %s\nJc = 4\nJmin = 40\nJmax = 70\nS1 = 116\nS2 = 61\n"
       "H1 = 1234567801\nH2 = 1234567802\nH3 = 1234567803\nH4 = 1234567804\n\n[Peer]\nPublicKey = %s\n"
       "AllowedIPs = 0.0.0.0/0\nEndpoint = 198.51.100.%d:51820\nPersistentKeepalive = 25") % (n, key(n), key(100 + n), 30 + n))
PY
}
VLESS_B='vless://22222222-3333-4444-5555-666666666666@198.51.100.21:443?type=tcp&security=reality&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com&fp=chrome&flow=xtls-rprx-vision#Helsinki'
hex_of() { printf '%s' "$1" | python3 -c 'import hashlib, sys; print(hashlib.sha1(sys.stdin.read().strip().encode()).hexdigest()[:10])'; }
UPF() { echo "$KASKAD_DIR/egress/upstream.txt"; }
# подделки пересборки, первой ноги, отключения и проверки связи — журнал $CALLS/egress.log
egress_fakes() {
    egress_rebuild() {
        echo "REBUILD $(grep -c . "$EGRESS_UP")" >> "$CALLS/egress.log"
        [ -z "${REBUILD_FAIL:-}" ] || { echo -e "${RED}Конфиг xray невалиден:${NC}"; return 1; }
        echo -e "${YELLOW}⚠ Ключ AmneziaWG 3.1: поддельное предупреждение${NC}"
    }
    k3_awg_first_leg() { echo "FIRSTLEG" >> "$CALLS/egress.log"; }
    egress_disable() { echo "DISABLE" >> "$CALLS/egress.log"; }
    egress_test() { echo "TEST" >> "$CALLS/egress.log"; }
}
t_padd() { egress_fakes; egress_panel_add; echo "RC=$?"; }
t_pdel() { egress_fakes; egress_panel_del "$@"; echo "RC=$?"; }
t_prm() { egress_fakes; egress_remove_key "$@"; echo "RC=$?"; }
t_pimp() { egress_fakes; sleep() { :; }; egress_import "$@"; echo "RC=$?"; }
use_root padd1; : > "$CALLS/egress.log"
o=$(printf '%s\n' "$VLESS_KEY" | lib t_padd 2>&1 | strip)
[ "$(cat "$(UPF)")" = "$VLESS_KEY" ] && printf '%s' "$o" | grep -q 'RC=0' && grep -qx 'REBUILD 1' "$CALLS/egress.log" \
  && ! grep -q 'TEST' "$CALLS/egress.log" && [ "$(perm "$(UPF)")" = 600 ] && ! printf '%s' "$o" | grep -q '11111111-2222' \
  && ok "--egress-add: ключ из stdin — в пул (0600), пересборка, без проверки связи (до 75 с), ключ не в выводе, код 0" \
  || bad "--egress-add: VLESS" "$o | $(cat "$CALLS/egress.log")"
printf '%s' "$o" | grep -q '⚠ Ключ AmneziaWG 3.1' && ok "--egress-add: предупреждения пересборки (⚠ …) — в выводе для панели" || bad "--egress-add: предупреждения" "$o"
: > "$CALLS/egress.log"
o=$(printf '  %s  \n' "$VLESS_KEY" | lib t_padd 2>&1 | strip)
[ "$(grep -c . "$(UPF)")" = 1 ] && printf '%s' "$o" | grep -q 'RC=4' && [ ! -s "$CALLS/egress.log" ] \
  && ok "--egress-add: тот же ключ ещё раз — код 4 (уже в пуле), пул и службы не тронуты" || bad "--egress-add: повтор" "$o | $(cat "$CALLS/egress.log")"
o=$(printf '%s\n' "$KASKAD_DIR/egress/egress_parse.py" | lib t_padd 2>&1 | strip)
o2=$(printf '  etc/kaskad/egress/egress_parse.py \n' | (cd "$KASKAD_ROOT" && lib t_padd 2>&1) | strip)
[ "$(grep -c . "$(UPF)")" = 1 ] && printf '%s' "$o" | grep -q 'RC=1' && printf '%s' "$o" | grep -q 'Это путь к файлу, а не ключ' \
  && printf '%s' "$o2" | grep -q 'RC=1' && printf '%s' "$o2" | grep -q 'Это путь к файлу' && [ ! -s "$CALLS/egress.log" ] \
  && ok "--egress-add: путь к существующему файлу (и относительный, с пробелами) — отказ, файл не читается" || bad "--egress-add: путь" "$o | $o2"
o=$(printf ' \n\n' | lib t_padd 2>&1 | strip); o2=$(printf 'hello world\n' | lib t_padd 2>&1 | strip)
printf '%s' "$o" | grep -q 'RC=1' && printf '%s' "$o" | grep -q 'Пусто' && printf '%s' "$o2" | grep -q 'RC=1' \
  && printf '%s' "$o2" | grep -q 'Не удалось разобрать ключи' && [ "$(grep -c . "$(UPF)")" = 1 ] \
  && ok "--egress-add: пусто — отказ; не ключ — «Не удалось разобрать ключи» (тот же разборщик), код 1" || bad "--egress-add: мусор" "$o | $o2"
: > "$CALLS/egress.log"
o=$(awg_conf 2 | lib t_padd 2>&1 | strip)
[ "$(grep -c . "$(UPF)")" = 2 ] && sed -n 2p "$(UPF)" | grep -q '^awg://' && printf '%s' "$o" | grep -q 'RC=0' \
  && grep -qx 'FIRSTLEG' "$CALLS/egress.log" && ! printf '%s' "$o" | grep -q 'PrivateKey' \
  && ok "--egress-add: многострочный .conf AmneziaWG — строка awg:// в пул; первая нога — выбор движка (k3_awg_first_leg)" \
  || bad "--egress-add: .conf" "$o | $(cat "$CALLS/egress.log")"
# тот же сервер другой строкой (vpn:// и .conf одного подключения дают разные строки: здесь — другой Address того же
# пира): ключ сервера, адрес и порт совпали — не добавлен, код 4 «уже в пуле» (T-X6: оба были в пуле)
: > "$CALLS/egress.log"
o=$(awg_conf 2 | sed 's|^Address = .*|Address = 10.8.99.9/32|' | lib t_padd 2>&1 | strip)
[ "$(grep -c . "$(UPF)")" = 2 ] && printf '%s' "$o" | grep -q 'RC=4' && printf '%s' "$o" | grep -q 'Не добавлено ключей: 1 — этот ключ уже есть как ключ № 2, по другому адресу или записан иначе' \
  && [ ! -s "$CALLS/egress.log" ] \
  && ok "--egress-add: тот же пир другой строкой (ключ сервера + ключ клиента) — не добавлен, код 4 «уже в пуле», № ключа" \
  || bad "--egress-add: тот же пир" "$o | $(cat "$(UPF)" | cut -c1-20)"
# 3.3.5 №4: тот же клиент по домену вместо IP — тот же ключ (две ноги с одним ключом теряют 25–100 %): не добавлен
: > "$CALLS/egress.log"
o=$(awg_conf 2 | sed 's|^Endpoint = .*|Endpoint = vpn.example.com:51820|' | lib t_padd 2>&1 | strip)
[ "$(grep -c . "$(UPF)")" = 2 ] && printf '%s' "$o" | grep -q 'RC=4' && printf '%s' "$o" | grep -q 'по другому адресу или записан иначе' \
  && [ ! -s "$CALLS/egress.log" ] \
  && ok "--egress-add: тот же клиент AmneziaWG по домену (а был по IP) — не добавлен, код 4 «уже в пуле»" \
  || bad "--egress-add: тот же клиент по домену" "$o | $(cat "$(UPF)" | cut -c1-20)"
# другой человек того же сервера (свой закрытый ключ, тот же ключ сервера и адрес) — отдельный выход
o=$(awg_conf 2 | sed "s|^PrivateKey = .*|PrivateKey = $(python3 -c 'import base64; print(base64.b64encode(bytes(range(40, 72))).decode())')|" | lib t_padd 2>&1 | strip)
[ "$(grep -c . "$(UPF)")" = 3 ] && printf '%s' "$o" | grep -q 'RC=0' && ! printf '%s' "$o" | grep -q 'Не добавлено ключей' \
  && ok "--egress-add: другой клиент того же сервера AmneziaWG — добавлен отдельным выходом" || bad "--egress-add: другой клиент" "$o"
sed -i.bak '3d' "$(UPF)"; rm -f "$(UPF).bak"; chmod 600 "$(UPF)"
# тот же аккаунт VLESS (uuid@host:port) с другими параметрами и именем — тоже «уже в пуле» (T-M6-qs330 №6)
: > "$CALLS/egress.log"
o=$(printf '%s\n' "${VLESS_KEY%%#*}&x=1#Другое%20имя" | lib t_padd 2>&1 | strip)
[ "$(grep -c . "$(UPF)")" = 2 ] && printf '%s' "$o" | grep -q 'RC=4' && printf '%s' "$o" | grep -q 'Не добавлено ключей: 1 — этот ключ уже есть как ключ № 1' \
  && [ ! -s "$CALLS/egress.log" ] \
  && ok "--egress-add: тот же аккаунт VLESS (uuid@адрес:порт) с другим именем — не добавлен, код 4 «уже в пуле»" \
  || bad "--egress-add: тот же VLESS" "$o | $(cat "$(UPF)" | cut -c1-20)"
o=$(awg_conf 3 | lib t_padd 2>&1 | strip)
[ "$(grep -c . "$(UPF)")" = 3 ] && printf '%s' "$o" | grep -q 'RC=0' && ! printf '%s' "$o" | grep -q 'Не добавлено ключей' \
  && ok "--egress-add: другой пир AmneziaWG — добавлен" || bad "--egress-add: другой пир" "$o"
sed -i.bak '3d' "$(UPF)"; rm -f "$(UPF).bak"; chmod 600 "$(UPF)"
: > "$CALLS/egress.log"; cp "$(UPF)" "$W/pool.before"
o=$(printf '%s\n' "$VLESS_B" | REBUILD_FAIL=1 lib t_padd 2>&1 | strip)
cmp -s "$(UPF)" "$W/pool.before" && printf '%s' "$o" | grep -q 'RC=1' && printf '%s' "$o" | grep -q 'Ключ не добавлен — пул оставлен прежним' \
  && [ "$(perm "$(UPF)")" = 600 ] && ok "пересборка не прошла (xray не принял) — пул прежний, код 1: на этом ключе не падает каждая следующая" \
  || bad "--egress-add: откат" "$o | $(cat "$(UPF)")"
# 3.3.4: ключ без проверки сертификата (allowInsecure) и без отпечатка pcs xray не примет — пропускается ОН ОДИН, остальные
# ключи порции добавляются (раньше откатывалась вся порция); итог «добавлено N, пропущено M: причина», метка для панели
BADK='vless://33333333-4444-5555-6666-777777777777@198.51.100.22:8443?type=ws&security=tls&allowInsecure=1&sni=a.example.com#Bad'
PINK='vless://44444444-5555-6666-7777-888888888888@198.51.100.23:8443?type=tcp&security=tls&allowInsecure=1&pcs=6fbc15ba46dfed152ad6c8d2129dd774707dd667a9ab4965476fa0f79ba82670#Pinned'
: > "$CALLS/egress.log"
o=$(printf '%s\n%s\n%s\n' "$BADK" "$VLESS_B" "$PINK" | K3_MARKS=1 lib t_padd 2>&1 | strip)
o1=$(printf '%s' "$o" | tr '\n' ' ' | tr -s ' ')
[ "$(grep -c . "$(UPF)")" = 4 ] && grep -qxF "$VLESS_B" "$(UPF)" && grep -qxF "$PINK" "$(UPF)" && ! grep -q '33333333-4444' "$(UPF)" \
  && printf '%s' "$o" | grep -q 'RC=0' && grep -qx 'REBUILD 4' "$CALLS/egress.log" \
  && ok "--egress-add: ключ allowInsecure без pcs пропущен один, два других (и с pcs) добавлены, одна пересборка" \
  || bad "--egress-add: пропуск одного ключа" "$o | $(cut -c1-30 "$(UPF)")"
printf '%s' "$o1" | grep -q 'Пропущен ключ «Bad»: в нём выключена проверка сертификата (allowInsecure), а отпечатка сертификата (pcs) нет' \
  && printf '%s' "$o1" | grep -q 'Добавлено ключей: 2, пропущено: 1 — без проверки сертификата и без отпечатка pcs' \
  && printf '%s' "$o" | grep -qx '@K3:egress_warn:skipped:insecure:1' && ! printf '%s' "$o" | grep -q '33333333-4444' \
  && ok "--egress-add: причина по-русски с именем ключа, итог «добавлено 2, пропущено 1», метка skipped для панели, ключа в выводе нет" \
  || bad "--egress-add: слова пропуска" "$o"
[ ! -e "$KASKAD_DIR/egress/.extract.skip" ] && ok "--egress-add: служебный файл пропусков убран" || bad "остался .extract.skip"
cp "$W/pool.before" "$(UPF)"; chmod 600 "$(UPF)"; : > "$CALLS/egress.log"
o=$(printf '%s\n' "$BADK" | K3_MARKS=1 lib t_padd 2>&1 | strip)
cmp -s "$(UPF)" "$W/pool.before" && printf '%s' "$o" | grep -q 'RC=1' && [ ! -s "$CALLS/egress.log" ] \
  && printf '%s' "$o" | tr '\n' ' ' | tr -s ' ' | grep -q 'Добавлено ключей: 0, пропущено: 1' && printf '%s' "$o" | grep -qx '@K3:egress_fail:skipped:insecure' \
  && ok "--egress-add: единственный ключ — такой: код 1, причина, пул и службы не тронуты" || bad "--egress-add: только плохой ключ" "$o"
o=$(printf '%s\n' "$BADK" | KASKAD_LANG=en lib t_padd 2>&1 | strip)
printf '%s' "$o" | tr '\n' ' ' | tr -s ' ' | grep -q 'Skipped key “Bad”: its certificate check is off (allowInsecure)' \
  && ok "--egress-add: причина по-английски (KASKAD_LANG=en)" || bad "--egress-add: en" "$o"
cp "$W/pool.before" "$(UPF)"; chmod 600 "$(UPF)"; : > "$CALLS/egress.log"
# --egress-del: N-я НЕПУСТАЯ строка и её отпечаток (id выхода e-<hex>)
HV=$(hex_of "$VLESS_KEY"); HA=$(hex_of "$(sed -n 2p "$(UPF)")")
: > "$CALLS/egress.log"
o=$(lib t_pdel 1 "$HA" 2>&1 | strip)
cmp -s "$(UPF)" "$W/pool.before" && printf '%s' "$o" | grep -q 'RC=3' && printf '%s' "$o" | grep -q 'Пул изменился — обновите страницу' \
  && [ ! -s "$CALLS/egress.log" ] && ok "--egress-del: отпечаток не той строки — код 3 «Пул изменился», ничего не удалено" || bad "--egress-del: чужой отпечаток" "$o"
o=$(lib t_pdel 1 "$HV" 2>&1 | strip)
[ "$(grep -c . "$(UPF)")" = 1 ] && sed -n 1p "$(UPF)" | grep -q '^awg://' && printf '%s' "$o" | grep -q 'RC=0' \
  && grep -qx 'REBUILD 1' "$CALLS/egress.log" && [ "$(perm "$(UPF)")" = 600 ] \
  && ok "--egress-del: отпечаток совпал — строка удалена, пересборка (gw apply — в ней), права 0600" || bad "--egress-del" "$o | $(cat "$(UPF)")"
: > "$CALLS/egress.log"
o=$(lib t_pdel 1 "$HA" 2>&1 | strip)
[ ! -s "$(UPF)" ] && printf '%s' "$o" | grep -q 'RC=0' && grep -qx 'DISABLE' "$CALLS/egress.log" && ! grep -q REBUILD "$CALLS/egress.log" \
  && ok "--egress-del: последний ключ — egress_disable (бот в Telegram напрямую), пул пуст" || bad "--egress-del: последний" "$o | $(cat "$CALLS/egress.log")"
# повтор ключа (e-<hex>-<N>) и пустые строки: номер — среди непустых, как у шлюза
{ echo "$VLESS_KEY"; echo ""; echo "   "; echo "$VLESS_B"; echo "$VLESS_KEY"; } > "$(UPF)"; chmod 600 "$(UPF)"
o=$(lib t_pdel 3 "$HV" 2>&1 | strip)
[ "$(cat "$(UPF)")" = "$(printf '%s\n\n   \n%s' "$VLESS_KEY" "$VLESS_B")" ] && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "--egress-del: строка-повтор (третья непустая) — удалена она, исходная и пустые строки на месте" || bad "--egress-del: повтор" "$o | $(cat "$(UPF)")"
o=$(lib t_prm 2 2>&1 | strip)
[ "$(cat "$(UPF)")" = "$(printf '%s\n\n   ' "$VLESS_KEY")" ] && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "меню: «удалить ключ 2» — вторая непустая строка, а не вторая строка файла (там пусто)" || bad "egress_remove_key: номер" "$o | $(cat "$(UPF)")"
# REV-335-2: тот же ключ в пуле ещё раз — маршруты удаляемой копии ядро переводит на оставшуюся до удаления строки
# (gw exit-handover N [hex] --json); меню печатает слова ядра, режим панели — метку egress_handover:<сколько> (итог
# передачи ядро читает ПОСЛЕ install.sh, REV-335-3); ядро без команды (код 2) — удаляется как раньше
fake_ctl; mkdir -p "$FAKE_DIR/gw"
echo '{"id": "e-1", "routes": 3, "text": "Маршруты перешли на «Аня IP» — тот же ключ."}' > "$FAKE_DIR/gw/exit-handover.json"
{ echo "$VLESS_KEY"; echo "$VLESS_B"; } > "$(UPF)"; chmod 600 "$(UPF)"; : > "$CALLS/kaskadctl.log"
o=$(lib t_prm 2 2>&1 | strip)
{ echo "$VLESS_KEY"; echo "$VLESS_B"; } > "$(UPF)"; chmod 600 "$(UPF)"
o2=$(K3_MARKS=1 lib t_pdel 2 "$(hex_of "$VLESS_B")" 2>&1 | strip)
[ "$(cat "$(UPF)")" = "$VLESS_KEY" ] && printf '%s' "$o" | grep -q 'RC=0' && printf '%s' "$o" | grep -q 'Маршруты перешли на «Аня IP»' \
  && ! printf '%s' "$o" | grep -q '@K3:' \
  && printf '%s' "$o2" | grep -q 'RC=0' && ! printf '%s' "$o2" | grep -q 'Маршруты перешли' \
  && printf '%s' "$o2" | grep -qx '@K3:egress_handover:3' \
  && [ "$(grep -c '^gw exit-handover' "$CALLS/kaskadctl.log")" = 2 ] && grep -qx 'gw exit-handover 2 --json' "$CALLS/kaskadctl.log" \
  && grep -qx "gw exit-handover 2 $(hex_of "$VLESS_B") --json" "$CALLS/kaskadctl.log" \
  && ok "удаление ключа (меню и панель): сначала ядро переводит маршруты копии на тот же ключ (gw exit-handover), меню говорит куда, панели — метка с числом" \
  || bad "удаление: передача маршрутов" "$o | $o2 | $(cat "$CALLS/kaskadctl.log")"
# маршрутов у копии нет — ни слов, ни метки
echo '{"id": "e-1", "routes": 0, "text": ""}' > "$FAKE_DIR/gw/exit-handover.json"
{ echo "$VLESS_KEY"; echo "$VLESS_B"; } > "$(UPF)"; chmod 600 "$(UPF)"
o2=$(K3_MARKS=1 lib t_pdel 2 "$(hex_of "$VLESS_B")" 2>&1 | strip)
[ "$(cat "$(UPF)")" = "$VLESS_KEY" ] && printf '%s' "$o2" | grep -q 'RC=0' && ! printf '%s' "$o2" | grep -q 'egress_handover' \
  && ok "удаление ключа: у копии нет маршрутов — ничего не говорится" || bad "удаление: без маршрутов" "$o2"
# REV-335-3: передача не удалась (занято — 6) или копия не может их взять (3) — строка остаётся, код 7, слова и метка
for c in 6 3; do
    echo "$c" > "$FAKE_DIR/gw/exit-handover.rc"
    { echo "$VLESS_KEY"; echo "$VLESS_B"; } > "$(UPF)"; chmod 600 "$(UPF)"; : > "$CALLS/egress.log"
    o=$(lib t_prm 2 2>&1 | strip)
    o2=$(K3_MARKS=1 lib t_pdel 2 "$(hex_of "$VLESS_B")" 2>&1 | strip)
    m=handover; w='Маршруты не перешли — удаление отменено, повторите.'
    [ "$c" = 3 ] && { m=handover_unfit; w='Маршруты не перешли: вторая копия ключа выключена для шлюза'; }
    [ "$(cat "$(UPF)")" = "$(printf '%s\n%s' "$VLESS_KEY" "$VLESS_B")" ] && printf '%s' "$o" | grep -q 'RC=7' \
      && printf '%s' "$o" | flat | grep -qF "$w" && ! printf '%s' "$o" | grep -q '@K3:' \
      && printf '%s' "$o2" | grep -q 'RC=7' && printf '%s' "$o2" | grep -qx "@K3:egress_fail:$m" \
      && ! grep -q REBUILD "$CALLS/egress.log" \
      && ok "удаление ключа: маршруты не перешли (ядро — код ${c}) — строка на месте, код 7, «${w}»" \
      || bad "удаление: передача не удалась ($c)" "$o | $o2"
done
# REV-335-4 №2 (3.3.6): ядро не смогло перевести маршруты не из-за блокировки (state.json не читается — код 1 и причина
# в JSON; ядро упало — код 1 без JSON) — без «повторите»: причина ядра или «ядро шлюза не ответило», метка handover_broken
echo '{"ok": false, "error": "Маршруты не перешли: state.json испорчен — удаление отменено. Повтор не поможет: сначала проверьте шлюз — kaskadctl gw check.", "code": 1}' > "$FAKE_DIR/gw/exit-handover.json"
echo 1 > "$FAKE_DIR/gw/exit-handover.rc"
{ echo "$VLESS_KEY"; echo "$VLESS_B"; } > "$(UPF)"; chmod 600 "$(UPF)"
o=$(lib t_prm 2 2>&1 | strip)
o2=$(K3_MARKS=1 lib t_pdel 2 "$(hex_of "$VLESS_B")" 2>&1 | strip)
: > "$FAKE_DIR/gw/exit-handover.json"
o3=$(lib t_prm 2 2>&1 | strip)
[ "$(cat "$(UPF)")" = "$(printf '%s\n%s' "$VLESS_KEY" "$VLESS_B")" ] && printf '%s' "$o" | grep -q 'RC=7' \
  && printf '%s' "$o" | flat | grep -qF 'state.json испорчен — удаление отменено. Повтор не поможет' && ! printf '%s' "$o$o3" | grep -q 'повторите' \
  && printf '%s' "$o2" | grep -q 'RC=7' && printf '%s' "$o2" | grep -qx '@K3:egress_fail:handover_broken' \
  && printf '%s' "$o3" | grep -q 'RC=7' && printf '%s' "$o3" | flat | grep -qF 'ядро шлюза не ответило (код 1) — удаление отменено' \
  && ok "удаление ключа: ядро не смогло перевести маршруты (не блокировка) — причина ядра, без «повторите», метка handover_broken" \
  || bad "удаление: сбой ядра при передаче" "$o | $o2 | $o3"
# С3 стенда 3.3.5: удаление в SSH-меню пишет в журнал «GW exit del: <id>» — id ядра (как у панели), без ядра — по тексту
# строки; режим панели строку не пишет (её пишет ops.exit_del после итога)
echo '{"id": "e-0123456789", "routes": 0, "text": ""}' > "$FAKE_DIR/gw/exit-handover.json"; rm -f "$FAKE_DIR/gw/exit-handover.rc"
: > "$KASKAD_ROOT/var/log/kaskad.log"
o=$(lib t_prm 2 2>&1 | strip)
l1=$(grep -c 'GW exit del: e-0123456789$' "$KASKAD_ROOT/var/log/kaskad.log")
{ echo "$VLESS_KEY"; echo "$VLESS_B"; } > "$(UPF)"; chmod 600 "$(UPF)"; : > "$KASKAD_ROOT/var/log/kaskad.log"
o2=$(K3_MARKS=1 lib t_pdel 2 "$(hex_of "$VLESS_B")" 2>&1 | strip)
l2=$(grep -c 'GW exit del' "$KASKAD_ROOT/var/log/kaskad.log")
echo 2 > "$FAKE_DIR/gw/exit-handover.rc"
{ echo "$VLESS_KEY"; echo "$VLESS_B"; } > "$(UPF)"; chmod 600 "$(UPF)"; : > "$KASKAD_ROOT/var/log/kaskad.log"
o3=$(lib t_prm 2 2>&1 | strip)
l3=$(grep -c "GW exit del: e-$(hex_of "$VLESS_B")\$" "$KASKAD_ROOT/var/log/kaskad.log")
[ "$l1" = 1 ] && [ "$l2" = 0 ] && [ "$l3" = 1 ] && printf '%s' "$o$o2$o3" | grep -c 'RC=0' | grep -qx 3 \
  && ! printf '%s' "$o3" | grep -q 'e-[0-9a-f]\{10\}' \
  && ok "удаление ключа в SSH-меню: строка журнала «GW exit del: <id>» (id ядра; без ядра — по тексту строки), панель — без неё" \
  || bad "удаление: строка журнала меню" "$l1/$l2/$l3 | $o | $o2 | $o3"
echo 2 > "$FAKE_DIR/gw/exit-handover.rc"
{ echo "$VLESS_KEY"; echo "$VLESS_B"; } > "$(UPF)"; chmod 600 "$(UPF)"
o=$(lib t_prm 2 2>&1 | strip)
[ "$(cat "$(UPF)")" = "$VLESS_KEY" ] && printf '%s' "$o" | grep -q 'RC=0' && ! printf '%s' "$o" | grep -q 'Маршруты' \
  && ok "удаление ключа: ядро без gw exit-handover (старше 3.3.5, код 2) — удаляется как раньше" || bad "удаление: старое ядро" "$o"
rm -f "$K3_OPT/bin/kaskadctl" "$FAKE_DIR/gw/exit-handover.json" "$FAKE_DIR/gw/exit-handover.rc"
# импорт с заменой пула: тот же ключ в списке ещё раз (тот же аккаунт VLESS с другим именем) — пишется первая строка
: > "$CALLS/egress.log"
o=$(lib t_pimp "$(printf '%s\n%s\n%s\n' "$VLESS_KEY" "${VLESS_KEY%%#*}#Другое%20имя" "$VLESS_B")" 2>&1 | strip)
[ "$(cat "$(UPF)")" = "$(printf '%s\n%s' "$VLESS_KEY" "$VLESS_B")" ] && printf '%s' "$o" | grep -q 'RC=0' \
  && printf '%s' "$o" | tr '\n' ' ' | tr -s ' ' | grep -q 'Не добавлено строк VLESS: 1 — та же учётка того же сервера в списке ещё раз' \
  && ! printf '%s' "$o" | flat | grep -q 'одно подключение' \
  && ok "импорт с заменой пула: та же учётка VLESS дважды — пишется первая строка, вторая — «не добавлено» (без «одно подключение»)" \
  || bad "импорт: тот же ключ" "$o | $(cut -c1-30 "$(UPF)")"
# ключ удалён руками, подписки есть (subs.json): его id — в subs-excluded.pending (ядро пометит «исключён», подписка
# его не вернёт); подписок нет — файла нет. Только id, ключа в файле нет.
PEND="$KASKAD_DIR/egress/subs-excluded.pending"; rm -f "$PEND"
{ echo "$VLESS_KEY"; echo "$VLESS_B"; } > "$(UPF)"; chmod 600 "$(UPF)"
o=$(lib t_prm 2 2>&1 | strip)
[ ! -e "$PEND" ] && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "удаление ключа без подписок — файла исключённых нет" || bad "исключённые: без подписок" "$o | $(cat "$PEND" 2>/dev/null)"
echo '{"schema": 1, "next": 2, "subs": []}' > "$KASKAD_DIR/egress/subs.json"
{ echo "$VLESS_KEY"; echo "$VLESS_B"; } > "$(UPF)"; chmod 600 "$(UPF)"
o=$(lib t_pdel 2 "$(hex_of "$VLESS_B")" 2>&1 | strip)
[ "$(cat "$PEND" 2>/dev/null)" = "e-$(hex_of "$VLESS_B")" ] && [ "$(perm "$PEND")" = 600 ] && printf '%s' "$o" | grep -q 'RC=0' \
  && ! grep -q 'vless://' "$PEND" \
  && ok "удаление ключа руками при подписках — id в subs-excluded.pending (0600, без ключа): обновление подписки его не вернёт" \
  || bad "исключённые: id в pending" "$o | $(cat "$PEND" 2>/dev/null)"
rm -f "$PEND" "$KASKAD_DIR/egress/subs.json"
{ echo "$VLESS_KEY"; echo ""; echo "   "; } > "$(UPF)"; chmod 600 "$(UPF)"
o=$(lib t_pdel 7 "$HV" 2>&1 | strip); o2=$(lib t_pdel 1 nothex 2>&1 | strip)
printf '%s' "$o" | grep -q 'RC=1' && printf '%s' "$o2" | grep -q 'RC=1' && printf '%s' "$o2" | grep -q 'Неверный номер или отпечаток' \
  && ok "--egress-del: нет такой строки / не отпечаток — код 1" || bad "--egress-del: ошибки" "$o | $o2"
# блокировка пула: свой файл, не state.lock; занято дольше EGRESS_POOL_WAIT — код 6 и строка словами
hold_pool() {
    rm -f "$W/held"
    python3 - "$KASKAD_ROOT/run/kaskad/egress-pool.lock" "$1" "$W/held" <<'PY' &
import fcntl, os, sys, time
os.makedirs(os.path.dirname(sys.argv[1]), exist_ok=True)
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
open(sys.argv[3], "w").close()
time.sleep(float(sys.argv[2]))
os.close(fd)
PY
    HOLDER=$!
    while [ ! -f "$W/held" ]; do sleep 0.05; done
}
use_root plock1; : > "$CALLS/egress.log"; rm -f "$CALLS/flock.log"
o=$(printf '%s\n' "$VLESS_KEY" | lib t_padd 2>&1 | strip)
printf '%s' "$o" | grep -q 'RC=0' && [ "$(wc -l < "$CALLS/flock.log" | tr -d ' ')" = 1 ] && grep -qE '^-w 30 [0-9]+$' "$CALLS/flock.log" \
  && [ -e "$KASKAD_ROOT/run/kaskad/egress-pool.lock" ] && [ ! -e "$KASKAD_ROOT/run/kaskad/state.lock" ] \
  && ok "пул меняется под своей блокировкой (flock -w 30 на /run/kaskad/egress-pool.lock, не state.lock — его берёт gw apply)" \
  || bad "блокировка пула" "$o | $(cat "$CALLS/flock.log" 2>&1)"
hold_pool 3; : > "$CALLS/egress.log"
o=$(printf '%s\n' "$VLESS_B" | EGRESS_POOL_WAIT=1 lib t_padd 2>&1 | strip)
o2=$(EGRESS_POOL_WAIT=1 lib t_prm 1 2>&1 | strip); wait "$HOLDER"
[ "$(cat "$(UPF)")" = "$VLESS_KEY" ] && printf '%s' "$o" | grep -q 'RC=6' && printf '%s' "$o" | grep -q 'Пул ключей сейчас меняет другое окно' \
  && printf '%s' "$o2" | grep -q 'RC=6' && [ ! -s "$CALLS/egress.log" ] \
  && ok "пул занят другим окном (панель, меню) — добавление и удаление ждут EGRESS_POOL_WAIT, потом код 6 и строка; пул цел" \
  || bad "занятый пул" "$o | $o2 | $(cat "$(UPF)")"
lk=1
for f in egress_add_key egress_remove_key egress_import; do
    sed -n "/^$f() {/p" "$S" | grep -q "egress_pool_locked ${f}_now" || lk=""
done
for f in egress_awg_up egress_awg_heal egress_awg_routes egress_awg_sync egress_rebuild; do
    sed -n "/^$f() *{/,/^}/p" "$S" | grep -q 'egress_pool_locked\|flock' && lk=""
done
[ -n "$lk" ] && ok "блокировку берут добавление, удаление и импорт; ноги (up, heal, routes) и пересборка — нет (их зовёт ядро под state.lock)" \
  || bad "кто берёт блокировку пула"
# ключ ни разу не уходит аргументом во внешнюю программу (argv виден всем через /proc/<pid>/cmdline): подделки-обёртки
# записывают свои аргументы и запускают настоящую программу
mkdir -p "$W/argvbin"; : > "$CALLS/argv.log"
for b in grep sed awk python3 tr cat cut head tail sort uniq wc cmp curl mktemp chmod mkdir touch rm mv cp flock base64 xargs date; do
    rp=$(command -v "$b") || continue
    printf '#!/usr/bin/env bash\nprintf "%%s %%s\\n" "%s" "$*" >> "$CALLS/argv.log"\nexec "%s" "$@"\n' "$b" "$rp" > "$W/argvbin/$b"
    chmod +x "$W/argvbin/$b"
done
use_root pargv; : > "$CALLS/egress.log"; : > "$CALLS/argv.log"
o=$( { printf '%s\n' "$VLESS_KEY"; awg_conf 7; } | PATH="$W/argvbin:$PATH" lib t_padd 2>&1 | strip)
o2=$(printf '%s\n' "$VLESS_KEY" | PATH="$W/argvbin:$PATH" lib t_padd 2>&1 | strip)            # повтор: сравнение строк пула
o3=$(PATH="$W/argvbin:$PATH" lib t_pdel 1 "$(hex_of "$(sed -n 1p "$(UPF)")")" 2>&1 | strip)
PRIV7=$(awg_conf 7 | sed -n 's/^PrivateKey = //p')
printf '%s' "$o" | grep -q 'RC=0' && printf '%s' "$o2" | grep -q 'RC=4' && printf '%s' "$o3" | grep -q 'RC=0' \
  && [ "$(grep -c . "$CALLS/argv.log")" -gt 3 ] && ! grep -qF '11111111-2222' "$CALLS/argv.log" \
  && ! grep -qF "$PRIV7" "$CALLS/argv.log" && ! grep -qE 'awg://[A-Za-z0-9_-]{8}|vless://[0-9a-f]|PrivateKey' "$CALLS/argv.log" \
  && ok "добавление, повтор и удаление ключа: строка ключа не уходит аргументом ни в одну внешнюю программу (только stdin и файлы)" \
  || bad "ключ в argv внешней программы" "$o | $o2 | $o3 | $(grep -E '11111111|awg://[A-Za-z0-9_-]{8}|vless://[0-9a-f]|PrivateKey' "$CALLS/argv.log" | cut -c1-120)"
! grep -v '^[[:space:]]*#' <(sed -n '/^egress_add_key_now() {/,/^}/p' "$S") | grep -qE '(grep|sed|awk)[^|]* "\$l"' \
  && ok "egress_add_key_now: повторы ищутся в самой оболочке, без grep -xF \"\$l\"" || bad "grep с ключом в egress_add_key_now"
# сами режимы: отдельный процесс, ключ — только из stdin
use_root padd2; lib egress_ensure_parser >/dev/null 2>&1
printf '%s\n' "$VLESS_KEY" > "$(UPF)"; chmod 600 "$(UPF)"
o=$(printf '%s\n' "$VLESS_KEY" | (cd "$KASKAD_ROOT" && bash "$S" --egress-add 2>&1); echo "RC=$?")
o2=$(printf '%s\n' "$KASKAD_DIR/egress/egress_parse.py" | (cd "$KASKAD_ROOT" && bash "$S" --egress-add 2>&1); echo "RC=$?")
o3=$( (cd "$KASKAD_ROOT" && bash "$S" --egress-add </dev/null 2>&1); echo "RC=$?")
o4=$( (cd "$KASKAD_ROOT" && bash "$S" --egress-del 1 0000000000 </dev/null 2>&1); echo "RC=$?")
printf '%s' "$o" | grep -q 'RC=4' && printf '%s' "$o2" | strip | grep -q 'Это путь к файлу' && printf '%s' "$o2" | grep -q 'RC=1' \
  && printf '%s' "$o3" | grep -q 'RC=1' && printf '%s' "$o4" | grep -q 'RC=3' && [ "$(cat "$(UPF)")" = "$VLESS_KEY" ] \
  && ok "gokaskad --egress-add (stdin): повтор — 4, путь — 1, пусто — 1; --egress-del с чужим отпечатком — 3" \
  || bad "режимы --egress-add/--egress-del" "$o | $o2 | $o3 | $o4"
grep -qE '^    --egress-add\) init_config; egress_panel_add; exit \$\? ;;' "$S" \
  && grep -qE '^    --egress-del\) init_config; egress_panel_del "\$\{2:-\}" "\$\{3:-\}"; exit \$\? ;;' "$S" \
  && ok "режимы панели — в case рядом с --egress-ensure-parser, с init_config (настройки как у меню)" || bad "case: режимы панели"

echo "── 16d. --egress-replace: весь пул из stdin одной пересборкой (подписки выходов, перенос настроек) ──"
# Ядро (kaskad/gw/exitsubs.py, kaskad/backup.py) считает новый пул само и отдаёт его целиком с отпечатком того пула,
# который видело: пул с тех пор изменился — код 3, ничего не тронуто.
phash() { printf '%s' "$1" | python3 -c 'import hashlib, sys; t = sys.stdin.read(); l = [x.strip() for x in t.splitlines() if x.strip()]; print(hashlib.sha1("\n".join(l).encode()).hexdigest()[:16])'; }
t_prep() { egress_fakes; egress_panel_replace "$@"; echo "RC=$?"; }
use_root prep1; lib egress_ensure_parser >/dev/null 2>&1; : > "$CALLS/egress.log"
printf '%s\n' "$VLESS_KEY" > "$(UPF)"; chmod 600 "$(UPF)"
H1=$(phash "$VLESS_KEY")
o=$(printf '%s\n' "$VLESS_B" | lib t_prep 0123456789abcdef 2>&1 | strip)
[ "$(cat "$(UPF)")" = "$VLESS_KEY" ] && printf '%s' "$o" | grep -q 'RC=3' && [ ! -s "$CALLS/egress.log" ] \
  && ok "--egress-replace: отпечаток не того пула — код 3, пул не тронут" || bad "--egress-replace: чужой отпечаток" "$o"
o=$(printf '  %s\n\n%s\n' "$VLESS_KEY" "$VLESS_B" | lib t_prep "$H1" 2>&1 | strip)
[ "$(cat "$(UPF)")" = "$(printf '%s\n%s' "$VLESS_KEY" "$VLESS_B")" ] && printf '%s' "$o" | grep -q 'RC=0' \
  && grep -qx 'REBUILD 2' "$CALLS/egress.log" && [ "$(perm "$(UPF)")" = 600 ] && ! grep -q TEST "$CALLS/egress.log" \
  && ok "--egress-replace: новый пул записан (пробелы и пустые строки сняты, 0600), одна пересборка, без проверки связи" \
  || bad "--egress-replace: замена" "$o | $(cat "$CALLS/egress.log")"
H2=$(phash "$(cat "$(UPF)")"); : > "$CALLS/egress.log"
o=$(printf '%s\n%s\n' "$VLESS_KEY" "$VLESS_B" | lib t_prep "$H2" 2>&1 | strip)
printf '%s' "$o" | grep -q 'RC=0' && printf '%s' "$o" | grep -q 'Пул не изменился' && [ ! -s "$CALLS/egress.log" ] \
  && ok "--egress-replace: тот же пул — код 0 без пересборки" || bad "--egress-replace: тот же пул" "$o"
o=$(printf '%s\nhttps://evil.example/sub\n' "$VLESS_KEY" | lib t_prep "$H2" 2>&1 | strip)
o2=$(printf '%s\n%s\n%s\n' "$VLESS_KEY" "$VLESS_B" "${VLESS_B/Helsinki/Oslo}" | EGRESS_MAX=2 lib t_prep "$H2" 2>&1 | strip)
printf '%s' "$o" | grep -q 'RC=1' && printf '%s' "$o2" | grep -q 'RC=5' && [ "$(grep -c . "$(UPF)")" = 2 ] && [ ! -s "$CALLS/egress.log" ] \
  && ok "--egress-replace: строка не ключа — код 1, больше EGRESS_MAX — код 5; пул не тронут" || bad "--egress-replace: отказы" "$o | $o2"
cp "$(UPF)" "$W/pool.before"
o=$(printf '%s\n' "$VLESS_KEY" | REBUILD_FAIL=1 lib t_prep "$H2" 2>&1 | strip)
cmp -s "$(UPF)" "$W/pool.before" && printf '%s' "$o" | grep -q 'RC=1' && printf '%s' "$o" | grep -q 'оставлен прежним' \
  && ok "--egress-replace: пересборка не прошла — пул прежний, код 1" || bad "--egress-replace: откат" "$o | $(cat "$(UPF)")"
: > "$CALLS/egress.log"
o=$(printf '' | lib t_prep "$H2" 2>&1 | strip)
[ ! -s "$(UPF)" ] && printf '%s' "$o" | grep -q 'RC=0' && grep -qx 'DISABLE' "$CALLS/egress.log" \
  && ok "--egress-replace: пустой пул — «Прокси для API Telegram» выключается (как удаление последнего ключа)" \
  || bad "--egress-replace: пусто" "$o | $(cat "$CALLS/egress.log")"
: > "$CALLS/egress.log"; : > "$CALLS/argv.log"
AWGL=$(awg_conf 4 | python3 "$KASKAD_DIR/egress/egress_parse.py" extract 2>/dev/null)
o=$(printf '%s\n%s\n' "$VLESS_KEY" "$AWGL" | PATH="$W/argvbin:$PATH" lib t_prep "$(phash "")" 2>&1 | strip)
printf '%s' "$o" | grep -q 'RC=0' && grep -qx 'FIRSTLEG' "$CALLS/egress.log" && [ "$(grep -c . "$CALLS/argv.log")" -gt 1 ] \
  && ! grep -qE '11111111-2222|awg://[A-Za-z0-9_-]{8}|vless://[0-9a-f]' "$CALLS/argv.log" \
  && ok "--egress-replace: первая нога AmneziaWG — выбор движка; ключи не уходят аргументом ни в одну программу" \
  || bad "--egress-replace: первая нога / argv" "$o | $(grep -E '11111111|awg://|vless://' "$CALLS/argv.log" | cut -c1-120)"
grep -qE '^    --egress-replace\) init_config; egress_panel_replace "\$\{2:-\}"; exit \$\? ;;' "$S" \
  && sed -n '/^egress_panel_replace() {/,/^}/p' "$S" | grep -q 'egress_pool_locked egress_replace_now' \
  && ok "режим --egress-replace — в case, под блокировкой пула" || bad "case: --egress-replace"

echo "── 16e. меню: подписки выходов (17 → 3) и копия настроек (16 → 6): секреты — только на вход ядра ──"
bkf() { mkdir -p "$FAKE_DIR/backup"; printf '%s\n' "$2" > "$FAKE_DIR/backup/$1"; }
SUBURL='https://sub.example.org/s/TOPSECRETPATH'
use_root subs1; fake_ctl; : > "$CALLS/stdin.log"
gwf exit-sub_list.json '{"ok": true, "subs": [{"id": "s-abcdef", "n": 1, "name": "sub.example.org", "domain": "sub.example.org", "auto": true, "stale": true, "text": "не обновлялась 30 ч: Сервер подписки ответил 404.", "error": "x"}]}'
gwf exit-sub_add.json '{"ok": true, "text": "Подписка добавлена: sub.example.org. Добавлено 2."}'
t_subs() { K3_TEST_TTY=1 k3_exit_subs_menu; }
o=$(printf '1\n%s\n2\n\n0\n' "$SUBURL" | lib t_subs 2>&1 | strip | flat)
printf '%s' "$o" | grep -q 'sub.example.org — не обновлялась 30 ч' && printf '%s' "$o" | grep -q 'Подписка добавлена' \
  && ctl_log | grep -qx 'gw exit-sub add --manual --json' && ! ctl_log | grep -q TOPSECRET && grep -q TOPSECRETPATH "$CALLS/stdin.log" \
  && ok "подписки: список с состоянием, «Добавить» (2 — вручную → --manual), адрес — только на вход ядра, не в argv" \
  || bad "подписки: добавление" "$o | $(ctl_log)"
: > "$CALLS/kaskadctl.log"
o=$(printf '5\n2\n\n0\n' | lib t_subs 2>&1 | strip | flat)
ctl_log | grep -qx 'gw exit-sub del s-abcdef --keep-keys --json' \
  && ok "подписки: «Удалить» — «2) оставить ключи» даёт --keep-keys" || bad "подписки: удаление" "$o | $(ctl_log)"
: > "$CALLS/kaskadctl.log"
o=$(printf '5\n0\n0\n' | lib t_subs 2>&1 | strip | flat)
! ctl_log | grep -q 'exit-sub del' && ok "подписки: «Удалить» → 0 — ничего не удалено" || bad "подписки: отмена удаления" "$(ctl_log)"
use_root bk1; fake_ctl; : > "$CALLS/stdin.log"
bkf summary.json '{"ok": true, "summary": {"gateway": {"present": true, "users": 2, "devices": 3, "inbounds": ["wg"]}, "exits": {"keys": 4, "subs": 1}, "forwards": {"forwards": 1, "names": 0}, "site": {"domain": ""}, "bot": {"token": true}, "mcp": {"keys": 0}}}'
bkf export.json '{"ok": true, "file": "/root/kaskad-x.kbk", "size": 4096}'
t_bksave() { K3_TEST_TTY=1 k3_backup_save; echo "RC=$?"; }
o=$(printf '2\nSecretPass9\nSecretPass9\n' | lib t_bksave 2>&1 | strip | flat)
printf '%s' "$o" | grep -q 'RC=0' && printf '%s' "$o" | grep -q 'пользователей 2, устройств 3' && printf '%s' "$o" | grep -q 'scp root@' \
  && ctl_log | grep -qx 'backup export --without bot,mcp --json' && ! ctl_log | grep -q SecretPass && grep -qx 'SecretPass9' "$CALLS/stdin.log" \
  && ! grep -q SecretPass "$KASKAD_ROOT/var/log/kaskad.log" 2>/dev/null \
  && ok "16 → 6 → 1: что войдёт, без бота и ключей агентов (--without bot,mcp), пароль — только на вход ядра" || bad "копия: сохранить" "$o | $(ctl_log)"
: > "$CALLS/kaskadctl.log"
o=$(printf '1\nSecretPass9\nДругой123\n' | lib t_bksave 2>&1 | strip | flat)
o2=$(printf '1\nshort\n' | lib t_bksave 2>&1 | strip | flat)
printf '%s' "$o" | grep -q 'Пароли не совпали' && printf '%s' "$o2" | grep -q 'короче 8' && ! ctl_log | grep -q 'backup export' \
  && ok "16 → 6 → 1: пароли не совпали / короче 8 — копия не делается" || bad "копия: пароль" "$o | $o2"
mkdir -p "$KASKAD_ROOT/root"; echo x > "$KASKAD_ROOT/root/old.kbk"
bkf import.json '{"ok": true, "applied": false, "preview": {"created": "2026-10-09", "version": "3.2.23", "text": ["• шлюз — перенесётся: пользователей 2"]}}'
bkf import.yes.json '{"ok": true, "applied": true, "text": ["Перенесено: шлюз."]}'
t_bkload() { K3_TEST_TTY=1 k3_backup_load; echo "RC=$?"; }
: > "$CALLS/kaskadctl.log"
o=$(printf '\nSecretPass9\n\n' | lib t_bkload 2>&1 | strip | flat)
printf '%s' "$o" | grep -q 'шлюз — перенесётся' && ! ctl_log | grep -q -- '--yes' && ctl_log | grep -q "backup import $KASKAD_ROOT/root/old.kbk --json" \
  && ok "16 → 6 → 2: самый новый .kbk по умолчанию, предпросмотр; Enter — «Отмена», ничего не применено" || bad "копия: отмена загрузки" "$o | $(ctl_log)"
: > "$CALLS/kaskadctl.log"
o=$(printf '\nSecretPass9\n1\n' | lib t_bkload 2>&1 | strip | flat)
printf '%s' "$o" | grep -q 'Перенесено: шлюз' && ctl_log | grep -q -- 'backup import .*--yes --json' && ! ctl_log | grep -q SecretPass \
  && ok "16 → 6 → 2: «1) Перенести» — import --yes, пароль не в аргументах" || bad "копия: перенос" "$o | $(ctl_log)"
bkf import.rc 2; bkf import.json '{"ok": false, "error": "Неверный пароль копии."}'
o=$(printf '\nwrongpass1\n' | lib t_bkload 2>&1 | strip | flat)
printf '%s' "$o" | grep -q 'Неверный пароль копии' && printf '%s' "$o" | grep -q 'RC=1' \
  && ok "16 → 6 → 2: неверный пароль — словами ядра" || bad "копия: неверный пароль" "$o"
# R-333 П3‴: «Вернуть как было» удалит ключи агентов, которых не было до загрузки, — сказать до вопроса; нет таких — молчать
t_bkundo() { K3_TEST_TTY=1 k3_backup_undo; echo "RC=$?"; }
t_bkundoen() { k3_lang_set en; K3_TEST_TTY=1 k3_backup_undo; echo "RC=$?"; }
bkf undo.json '{"ok": true, "applied": false, "from": "/var/backups/kaskad/backup-import-1", "created": "2026-10-09T12:00:00Z", "parts": ["mcp"], "agent_keys_gone": 2}'
: > "$CALLS/kaskadctl.log"
o=$(printf '\n' | lib t_bkundo 2>&1 | strip | flat)
o2=$(printf '\n' | lib t_bkundoen 2>&1 | strip | flat)
bkf undo.json '{"ok": true, "applied": false, "from": "/var/backups/kaskad/backup-import-1", "created": "2026-10-09T12:00:00Z", "parts": ["mcp"], "agent_keys_gone": 0}'
o3=$(printf '\n' | lib t_bkundo 2>&1 | strip | flat)
printf '%s' "$o" | grep -q 'Удалятся ключи агентов, которых не было до загрузки копии: 2. Агенты с этими ключами сразу отключатся.' \
  && printf '%s' "$o2" | grep -q 'Agent keys that weren’t here before the load will be deleted: 2.' && ! printf '%s' "$o2" | LC_ALL=en_US.UTF-8 grep -q '[А-Яа-яЁё]' \
  && ! printf '%s' "$o3" | grep -q 'Удалятся ключи агентов' && ! ctl_log | grep -q -- 'undo --yes' \
  && ok "16 → 6 → 3: ключи агентов, которых не было до загрузки, — предупреждение до вопроса (RU/EN); нет таких — без строки" \
  || bad "копия: вопрос «Вернуть как было» и ключи агентов" "$o | $o2 | $o3 | $(ctl_log)"
o=$(lib bash -c 'source "$1" --lib >/dev/null 2>&1; declare -f k3_engine_menu' _ "$S")
printf '%s' "$o" | flat | grep -q '6) k3_backup_menu' && grep -q 'Движок и перенос' "$S" \
  && ok "меню 16: пункт 6 «Копия настроек и перенос»; в главном меню — «Движок и перенос»" || bad "меню 16: пункт 6"

echo "── 17. apt занят (свежий сервер, unattended-upgrades): ждём до 10 минут, честная строка ──"
# Держатель блокировки dpkg — как unattended-upgrades: fcntl-блокировка на lock-frontend; его имя в
# $KASKAD_ROOT/proc/<pid>/cmdline (тест идёт и на macOS, где /proc нет).
hold_apt() {
    rm -f "$W/apt_held"; mkdir -p "$KASKAD_ROOT/var/lib/dpkg"
    python3 - "$KASKAD_ROOT/var/lib/dpkg/lock-frontend" "$1" "$W/apt_held" <<'PY' &
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o640)
fcntl.lockf(fd, fcntl.LOCK_EX)
open(sys.argv[3], "w").close()
time.sleep(float(sys.argv[2]))
PY
    APT_HOLDER=$!
    while [ ! -f "$W/apt_held" ]; do sleep 0.05; done
    mkdir -p "$KASKAD_ROOT/proc/$APT_HOLDER"
    printf '/usr/bin/python3\0/usr/bin/unattended-upgrade\0' > "$KASKAD_ROOT/proc/$APT_HOLDER/cmdline"
}
WAITLINE='Система ставит обновления (apt занят) — жду до 10 минут…'
t_aptget() { k3_apt_get install -y --no-remove nftables; echo "RC=$?"; }
use_root apt1
o=$(lib t_aptget 2>&1 | strip)
! printf '%s' "$o" | grep -q 'apt занят' && grep -qx -- '-o DPkg::Lock::Timeout=600 install -y --no-remove nftables' "$CALLS/apt.log" \
  && ok "apt свободен — без ожидания и без строки; apt-get с DPkg::Lock::Timeout=600" || bad "свободный apt" "$o | $(cat "$CALLS/apt.log")"
use_root apt2; hold_apt 1.5
o=$(K3_APT_POLL=0.2 lib t_aptget 2>&1 | strip); wait "$APT_HOLDER" 2>/dev/null
[ "$(printf '%s\n' "$o" | grep -cxF "$WAITLINE")" = 1 ] && printf '%s' "$o" | grep -q 'RC=0' && grep -q 'install -y --no-remove nftables' "$CALLS/apt.log" \
  && grep -q 'APT: ждал' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "apt занят — одна строка «${WAITLINE}», дождались — apt-get пошёл" || bad "ожидание apt" "$o | $(cat "$CALLS/apt.log")"
use_root apt3; hold_apt 8
t_aptget2() { k3_apt_get update -y; echo "RC1=$?"; k3_apt_get install -y nftables; echo "RC2=$?"; }
o=$(K3_APT_WAIT=1 K3_APT_POLL=0.2 lib t_aptget2 2>&1 | strip); kill "$APT_HOLDER" 2>/dev/null; wait "$APT_HOLDER" 2>/dev/null
printf '%s' "$o" | grep -q "apt занят дольше 1 с: блокировку держит unattended-upgrade (pid $APT_HOLDER) — система ставит обновления" \
  && printf '%s' "$o" | grep -q 'RC1=100' && printf '%s' "$o" | grep -q 'RC2=100' && [ ! -s "$CALLS/apt.log" ] \
  && [ "$(printf '%s\n' "$o" | grep -cF "$WAITLINE")" = 1 ] \
  && ok "не дождались — честная строка с именем процесса (unattended-upgrade, pid), apt-get не запускался, второй вызов — сразу отказ" \
  || bad "apt занят дольше срока" "$o | $(cat "$CALLS/apt.log")"
left=$(grep -v '^[[:space:]]*#' "$S" | grep -n 'apt-get' | grep -v 'apt-get -s install' | grep -vE 'command -v apt-get|apt-get -o "DPkg::Lock::Timeout=\$\{K3_APT_WAIT\}"|runner|"apt-get"|apt-get нет')
[ -z "$left" ] && ok "все apt-get install.sh — через k3_apt_get (ожидание блокировки 600 с)" || bad "apt-get в обход k3_apt_get" "$left"
# ── шлюз (WP7): ответы поддельного kaskadctl — файлы $FAKE_DIR/gw/<ключ>.{json,rc,err,state} ──
# state.json со шлюзом: gw_st <включён> '<входы JSON>' <устройств> <пользователей> [файл]
gw_st() {
    local devs="" users="" i out="${5:-$KASKAD_DIR/state.json}"
    for ((i=1; i<=${3:-0}; i++)); do devs+="${devs:+, }{\"id\": \"d-0000000$i\"}"; done
    for ((i=1; i<=${4:-0}; i++)); do users+="${users:+, }{\"id\": \"u-0000000$i\"}"; done
    mkdir -p "${out%/*}"
    printf '{\n  "engine": "nft",\n  "gateway": {"enabled": %s, "host": "203.0.113.10", "inbounds": %s, "devices": [%s], "users": [%s], "routes": {"default": {"via": "pool", "fail": "block"}}},\n  "schema": 1\n}\n' \
        "$1" "$2" "$devs" "$users" > "$out"
}
gw_calls() { grep '^gw ' "$CALLS/kaskadctl.log" 2>/dev/null; }
gw_seq() { gw_calls | tr '\n' ';'; }
perm() { stat -c %a "$1" 2>/dev/null || stat -f %Lp "$1" 2>/dev/null; }
GW_PROBE_OK='{"ok": true, "supported": true, "code": "", "reason": "", "engine": "nft"}'
# Строка inbound list: gw_row <вход> <название> <стоит> <включён> <работает> '<порты JSON>' <устройств>
gw_row() { printf '{"kind": "%s", "title": "%s", "engine": "", "available": true, "installed": %s, "enabled": %s, "running": %s, "ports": %s, "devices": %s, "sessions": 0}' "$@"; }
gw_list() {   # входы через пробел, которые «стоят» (порт 51820/udp у wg, остальные без портов)
    local rows="" k t on
    for k in wg awg1 awg2 awg3 l2tp pptp sstp ikev2 openvpn ocserv socks5; do
        case "$k" in wg) t=WireGuard ;; awg1) t="AmneziaWG 1.5" ;; awg2) t="AmneziaWG 2.0" ;; awg3) t="AmneziaWG 3.1" ;;
            l2tp) t="L2TP/IPsec" ;; pptp) t=PPTP ;; sstp) t=SSTP ;; ikev2) t=IKEv2 ;; openvpn) t=OpenVPN ;; ocserv) t=OpenConnect ;; socks5) t=SOCKS5 ;; esac
        on=false; [[ " $1 " == *" $k "* ]] && on=true
        if [ "$k" = wg ] && [ "$on" = true ]; then rows+="${rows:+, }$(gw_row "$k" "$t" true true true '["51820/udp"]' 1)"
        else rows+="${rows:+, }$(gw_row "$k" "$t" "$on" "$on" "$on" '[]' 0)"; fi
    done
    printf '{"ok": true, "inbounds": [%s]}' "$rows"
}

echo "── 16f. шлюз: пункт 17 главного меню, его состояние, без ядра ──"
use_root gw-menu; fake_ctl; engine_nft
t_menu() { show_menu; }
o=$(lib t_menu <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qx 'Шлюз: не настроен — телефоны и роутеры через этот сервер: 17' && printf '%s' "$o" | grep -q ' 17) Шлюз: телефоны и роутеры$' \
  && ok "шапка «Шлюз: не настроен — …: 17», пункт «17) Шлюз: телефоны и роутеры»" || bad "пункт 17" "$o"
nums=$(printf '%s\n' "$o" | sed -n '/^─/,$p' | grep -oE '(^| )[0-9]{1,2}\) ' | tr -d ' )' | tr '\n' ' ')
[ "$nums" = "1 12 2 13 3 14 4 15 5 16 6 17 7 18 8 19 9 20 10 21 11 22 0 " ] \
  && ok "пункты в два столбца: 1–11 слева, 12–22 и 0 справа — номера 1–19 прежние, 20–22 новые" || bad "номера пунктов" "$nums"
[ -z "$(gw_calls)" ] && ctl_log | grep -qx 'summary --json' && ok "главное меню ядро шлюза не зовёт: один снимок kaskadctl summary --json" || bad "меню зовёт gw" "$(ctl_log)"
gw_st true '{"wg": {"enabled": true}, "awg2": {}, "l2tp": {"enabled": false}}' 2 1
o=$(lib t_menu <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qx 'Шлюз: ? WireGuard  ? AWG 2.0  ○ L2TP · устройств: 2' \
  && printf '%s' "$o" | grep -qx '  ● работает  ○ выключен  ? нет свежих данных' \
  && ok "шлюз включён, сборщик молчит: «? WireGuard  ? AWG 2.0  ○ L2TP · устройств: 2» и легенда только встреченных значков" \
  || bad "строка включённого шлюза" "$(printf '%s' "$o" | grep -A1 '^Шлюз')"
printf '%s' "$o" | grep -q '^Выходы: нет — устройства без интернета — 17 → 3' && printf '%s' "$o" | grep -q '^✗ Устройства шлюза сейчас без интернета' \
  && ok "пул пуст, при отказе — блок: «Выходы: нет — устройства без интернета», первая проблема — она же (правило ядра)" \
  || bad "строка 1/1" "$(printf '%s' "$o" | grep -A2 '^Выходы')"
gw_st false '{"wg": {"enabled": true}}' 0 0
o=$(lib t_menu <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qx 'Шлюз: ○ выключен · протоколов: 1 · устройств: 0 — включить: 17 → 6' && ! printf '%s' "$o" | grep -q '^Выходы' \
  && ok "входы есть, шлюз выключен: «○ выключен · входов: 1 …», строки выходов нет" || bad "строка выключенного шлюза" "$(printf '%s' "$o" | grep '^Шлюз')"
t_plural() { local n; for n in 0 1 2 4 5 11 14 21 22 25 101 111; do printf '%s ' "$n$(k3_plural "$n" вход входа входов)"; done; }
[ "$(lib t_plural)" = "0входов 1вход 2входа 4входа 5входов 11входов 14входов 21вход 22входа 25входов 101вход 111входов " ] \
  && ok "склонение: 1 вход, 2 входа, 5 входов, 11 входов, 21 вход" || bad "склонение" "$(lib t_plural)"
use_root gw-nocore; engine_nft
t_gw17() { k3_gw_menu; }
o=$(lib t_gw17 <<< "" 2>&1 | strip)
printf '%s' "$o" | grep -q 'Ядро Kaskad 3 недоступно' && ok "пункт 17 без ядра: одна строка — и назад" || bad "пункт 17 без ядра" "$o"
use_root gw-setup; fake_ctl; gw_st false '{"wg": {}}' 0 0
t_gw17b() { k3_gw_wizard() { echo "WIZARD"; }; k3_gw_menu; }
o=$(lib t_gw17b <<< "0" 2>&1 | strip)
! printf '%s' "$o" | grep -q WIZARD && printf '%s' "$o" | grep -q '1) Протоколы' && printf '%s' "$o" | grep -q '6) Включить шлюз' \
  && printf '%s' "$o" | grep -q '7) Удалить шлюз' && ok "входы стоят → подменю шлюза (мастер не нужен), выключенный — «Включить шлюз»" || bad "подменю" "$o"
! printf '%s' "$o" | grep -q 'была прервана' && printf '%s' "$o" | grep -q 'Протоколов: 1 · пользователей: 0' && ! printf '%s' "$o" | grep -qE 'входов:|пула|переводит протоколы' \
  && ok "шапка 17 без жаргона: «протоколов: 1», без «входов» и «пула»; выключен сам — не «прервана»" || bad "шапка 17" "$o"
: > "$KASKAD_DIR/gw-wizard.unfinished"
o=$(lib t_gw17b <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -q 'Установка шлюза была прервана — нажмите 6, чтобы закончить' && printf '%s' "$o" | grep -q '6) Включить шлюз' \
  && ok "мастер прервали после установки протоколов (шлюз не включился) → «Установка шлюза была прервана — нажмите 6, чтобы закончить»" || bad "прерванная установка" "$o"
gw_st true '{"wg": {}}' 0 0; o=$(lib t_gw17b <<< "0" 2>&1 | strip)
! printf '%s' "$o" | grep -q 'была прервана' && [ ! -e "$KASKAD_DIR/gw-wizard.unfinished" ] \
  && ok "шлюз включён — отметка прерванной установки снята" || bad "отметка прерванной установки" "$o"
gw_st false '{}' 2 1
o=$(lib t_gw17b <<< "" 2>&1 | strip)
printf '%s' "$o" | grep -q WIZARD && ok "входов нет (остались только пользователи) → мастер" || bad "мастер не открылся" "$o"

echo "── 17b. мастер шлюза: проверка сервера, движок iptables ──"
t_wiz() { K3_TEST_TTY=1; k3_gw_wizard; echo "WIZ=$?"; }
use_root gw-pre1; fake_ctl; engine_nft
gwf probe.json '{"ok": false, "supported": false, "code": "os", "reason": "Новый движок пробросов — на Debian 12+ и Ubuntu 24.04+; здесь Ubuntu 22.04.5 LTS. Пробросы работают по-старому.", "engine": "nft"}'
gwf probe.rc 5
o=$(printf '\n\n' | lib t_wiz 2>&1 | strip)
printf '%s' "$o" | flat | grep -q 'Шлюз здесь недоступен: Новый движок пробросов — на Debian 12+ и Ubuntu 24.04+; здесь Ubuntu 22.04.5 LTS\.' \
  && ! printf '%s' "$o" | grep -q 'Пробросы работают по-старому' && printf '%s' "$o" | grep -q 'WIZ=1' && ! gw_calls | grep -q 'inbound' \
  && ok "система не подходит → причина от ядра одной строкой, мастер остановлен, ничего не ставится" || bad "неподходящая система" "$o | $(gw_seq)"
use_root gw-pre2; fake_ctl; prog_ready
gwf probe.json '{"ok": true, "supported": true, "code": "", "reason": "", "engine": "legacy"}'
o=$(printf '\n2\n\n' | lib t_wiz 2>&1 | strip)
printf '%s' "$o" | grep -q 'Шлюзу нужен новый движок пробросов' && printf '%s' "$o" | grep -q 'ПЛАН: перенесу 1 проброс' \
  && printf '%s' "$o" | grep -q 'без nftables шлюза нет' && ! ctl_log | grep -q 'migrate apply' && ! gw_calls | grep -q inbound \
  && printf '%s' "$o" | grep -q 'WIZ=1' && ok "движок iptables → объяснение, план переезда (как 16 → 1) и вопрос; «2» — мастер остановлен" || bad "отказ от переезда" "$o"
use_root gw-pre3; fake_ctl; prog_ready
gwf probe.json '{"ok": true, "supported": true, "code": "", "reason": "", "engine": "legacy"}'
o=$(printf '\n1\n0\n0\n' | lib t_wiz 2>&1 | strip)
ctl_log | grep -qx 'migrate apply --yes' && grep -q '"engine": "nft"' "$KASKAD_DIR/state.json" && [ "$(gw_calls | grep -c '^gw probe --json')" = "2" ] \
  && printf '%s' "$o" | grep -q 'Отмечены три основных' && ok "«1» → переезд, проверка заново, дальше — выбор протоколов" || bad "переезд из мастера" "$o | $(ctl_log)"

echo "── 18. мастер шлюза целиком: протоколы → порты → выходы → установка → устройство → выдача ──"
use_root gw-wiz; fake_ctl; engine_nft
gwf probe.json "$GW_PROBE_OK"
gwf inbound_plan.json '{"ok": true, "inbounds": [
 {"kind": "wg", "title": "WireGuard", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 51820, "default": 51820, "conflicts": [{"kind": "forward", "detail": "его занимает проброс на 198.51.100.7:51820"}], "free": 38211}, "fixed": []},
 {"kind": "awg1", "title": "AmneziaWG 1.5", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 31001, "default": 31001, "conflicts": [], "free": null}, "fixed": []},
 {"kind": "awg2", "title": "AmneziaWG 2.0", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 41002, "default": 41002, "conflicts": [], "free": null}, "fixed": []},
 {"kind": "awg3", "title": "AmneziaWG 3.1", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 51003, "default": 51003, "conflicts": [], "free": null}, "fixed": []},
 {"kind": "l2tp", "title": "L2TP/IPsec", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "build": "accel-ppp 1.14.0 из исходников (около минуты)", "port": null, "fixed": [{"proto": "udp", "port": 1701, "conflicts": []}, {"proto": "udp", "port": 500, "conflicts": []}, {"proto": "udp", "port": 4500, "conflicts": []}]}]}'
gwf exits.json '{"ok": true, "exits": []}'
gwf inbound_install.wg.json '{"ok": true, "kind": "wg", "record": {"enabled": true, "port": 38211}, "notes": []}'
gwf inbound_install.awg1.json '{"ok": true, "kind": "awg1", "record": {"enabled": true, "port": 31001}, "notes": []}'
gwf inbound_install.awg2.json '{"ok": false, "error": "Нет ядра AmneziaWG 3.1 для amd64: нужны bin/amneziawg-go-linux-amd64 и bin/awg-linux-amd64", "code": 1, "data": {}}'
gwf inbound_install.awg2.rc 1
gwf inbound_install.awg3.json '{"ok": true, "kind": "awg3", "record": {"enabled": true, "port": 51003}, "notes": []}'
gwf inbound_install.l2tp.json '{"ok": true, "kind": "l2tp", "record": {"enabled": true, "ipsec": true, "plain": false}, "notes": []}'
gwf enable.json '{"ok": true, "base": "10.77.0.0/16", "host": "203.0.113.10", "warnings": []}'
gwf enable.err '@K3:warn L2TP/IPsec: модуль ppp_mppe не загрузился'
gw_st true '{"wg": {}, "awg1": {}, "awg3": {}, "l2tp": {}}' 0 0 "$FAKE_DIR/gw/enable.state"
gwf inbound_list.json "$(gw_list "wg awg1 awg3 l2tp")"
gwf user_add.json '{"ok": true, "user": {"id": "u-00000001", "name": "Мама"}}'
gwf offers.json '{"ok": true, "platform": "android", "notes": [], "offers": [
 {"inbound": "awg3", "format": "vpn", "note": "AmneziaVPN или AmneziaWG: ссылка vpn://", "title": "AmneziaWG 3.1", "installed": true, "enabled": true},
 {"inbound": "awg2", "format": "vpn", "note": "AmneziaVPN или AmneziaWG: ссылка vpn://", "title": "AmneziaWG 2.0", "installed": false, "enabled": false},
 {"inbound": "wg", "format": "qr", "note": "приложение WireGuard: QR", "title": "WireGuard", "installed": true, "enabled": true}]}'
gwf device_add.json '{"ok": true, "device": {"id": "d-00000001", "login": "telefon-1a2b", "name": "Телефон", "access": ["awg3"]}}'
GW_ISSUE='{"ok": true, "device": "d-00000001", "name": "Телефон", "kind": "awg3", "os": "android", "available": ["awg3"], "items": [
 {"format": "vpn", "title": "AmneziaWG 3.1 — ссылка vpn://", "body": "vpn://SECRETVPN", "filename": "", "notes": ["AmneziaVPN: «+» → «Вставить из буфера»."]},
 {"format": "qr", "title": "AmneziaWG 3.1 — QR для AmneziaVPN", "body": ["vpn://SECRETVPN"], "filename": "", "notes": []},
 {"format": "conf", "title": "AmneziaWG 3.1 — файл .conf", "body": "[Interface]\nPrivateKey = SECRETKEY\n", "filename": "kaskad-awg3.conf", "notes": []},
 {"format": "fields", "title": "Данные для подключения", "body": [["Логин", "telefon-1a2b"], ["Пароль", "SECRETPASS"]], "filename": "", "notes": []}]}'
gwf issue.json "$GW_ISSUE"
o=$(printf '\n\n\n2\n\n\nМама\n2\n\n\n1\n\n0\n' | lib t_wiz 2>&1 | strip)
exp="gw probe --json;gw inbound plan wg awg1 awg2 awg3 l2tp --json;gw exits --json;gw route direct --json;gw host --detect --json;gw inbound install wg --port 38211 --json;gw inbound install awg1 --port 31001 --awg-engine kernel --json;gw inbound install awg2 --port 41002 --json;gw inbound install awg3 --port 51003 --json;gw inbound install l2tp --json;gw enable --json;gw inbound list --json;gw user add Мама --json;gw offers --platform android --json;gw inbound list --json;gw device add u-00000001 Телефон --kind phone --platform android --access awg3 --json;gw issue d-00000001 --json;"
[ "$(gw_seq)" = "$exp" ] && printf '%s' "$o" | grep -q 'WIZ=0' \
  && printf '%s' "$o" | grep -q '1) Да, ускорить модулем ядра (рекомендуется)' \
  && ok "по умолчанию — WireGuard, три линии AmneziaWG и L2TP/IPsec; порты из плана ядра (--port), вопрос о модуле ядра (Enter — да: --awg-engine kernel у первого входа AmneziaWG), маршрут «напрямую», включение, пользователь, устройство (Android: AmneziaWG 3.1 отмечен), выдача" \
  || bad "порядок вызовов мастера" "$(gw_seq)"
printf '%s' "$o" | grep -q 'WireGuard *51820/udp *занят → возьму 38211$' \
  && printf '%s' "$o" | grep -q '^       51820/udp: его занимает проброс на 198.51.100.7:51820$' \
  && printf '%s' "$o" | grep -q 'L2TP/IPsec *1701, 500, 4500/udp *✓ свободен' && ok "сводка портов: занятый порт — свободный взамен, чем занят — строкой ниже; постоянные порты L2TP проверены" || bad "сводка портов" "$o"
printf '%s' "$o" | grep -q '\[3/5\] AmneziaWG 2.0 … ✗' && printf '%s' "$o" | grep -q 'Нет ядра AmneziaWG 3.1 для amd64' \
  && printf '%s' "$o" | grep -q '\[4/5\] AmneziaWG 3.1 … ✓ порт 51003' && printf '%s' "$o" | grep -q '\[5/5\] L2TP/IPsec (accel-ppp собирается из исходников — до двух минут) … ✓' \
  && ok "строка на протокол; сбой AmneziaWG 2.0 (честная причина ядра) не мешает остальным" || bad "установка по протоколам" "$o"
printf '%s' "$o" | grep -q 'Шлюз включён.*сеть 10.77.0.0/16, адрес для клиентов 203.0.113.10' && printf '%s' "$o" | grep -q '! L2TP/IPsec: модуль ppp_mppe не загрузился' \
  && printf '%s' "$o" | grep -q 'AmneziaWG 3.1 *- *работает' && ok "шлюз включён, предупреждения ядра показаны, итоговая таблица установленных" || bad "включение и итог" "$o"
printf '%s' "$o" | grep -q 'PrivateKey = SECRETKEY' && printf '%s' "$o" | grep -q 'Пароль: SECRETPASS' && printf '%s' "$o" | grep -q '\[QR\]' \
  && grep -q 'vpn://SECRETVPN' "$CALLS/qr_in.log" && ! grep -q SECRET "$CALLS/qrencode.log" \
  && ok "выдача: текст между разделителями, поля, QR (секрет — в qrencode через stdin, не аргументом)" || bad "экран выдачи" "$o"
D="$KASKAD_ROOT/root/kaskad-issue/telefon-1a2b"
[ "$(perm "$D")" = "700" ] && [ "$(perm "$D/kaskad-awg3.conf")" = "600" ] && [ "$(perm "$D/kaskad-awg3.txt")" = "600" ] \
  && [ "$(cat "$D/kaskad-awg3.conf")" = "$(printf '[Interface]\nPrivateKey = SECRETKEY')" ] && grep -q 'vpn://SECRETVPN' "$D/kaskad-awg3.txt" \
  && grep -q 'Пароль: SECRETPASS' "$D/kaskad-awg3.txt" && printf '%s' "$o" | grep -q "$D/kaskad-awg3.conf" \
  && ok "файлы в /root/kaskad-issue/<логин>/: каталог 0700, файлы 0600, пути показаны" || bad "сохранение файлов" "$(ls -la "$D" 2>&1)"
! grep -q SECRET "$KASKAD_ROOT/var/log/kaskad.log" && grep -q 'GW: настройки устройства telefon-1a2b сохранены' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "в журнале — ни одного секрета (только «сохранены в …»)" || bad "секреты в журнале" "$(cat "$KASKAD_ROOT/var/log/kaskad.log")"

echo "── 19. мастер: выбор протоколов, правка портов, порт занят при установке ──"
use_root gw-pick; fake_ctl; engine_nft
gwf probe.json "$GW_PROBE_OK"
gwf inbound_plan.json '{"ok": true, "inbounds": []}'
o=$(printf '\n3\n4\n10\n1\n\n0\n0\n0\n' | lib t_wiz 2>&1 | strip)          # PPTP — 10 (9 — VLESS Reality)
gw_calls | grep -qx 'gw inbound plan wg awg1 awg2 awg3 ikev2 pptp --json' && printf '%s' "$o" | grep -q 'PPTP шифрует слабо' \
  && ! gw_calls | grep -q 'inbound install' && printf '%s' "$o" | grep -q 'WIZ=1' \
  && ok "номера снимают и ставят галочки (PPTP — с предупреждением); «0» ведёт назад, ничего не поставлено" || bad "выбор протоколов" "$(gw_seq)"
use_root gw-ports; fake_ctl; engine_nft
gwf inbound_plan.json '{"ok": true, "inbounds": [
 {"kind": "wg", "title": "WireGuard", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 51820, "default": 51820, "conflicts": [{"kind": "listener", "detail": "его слушает wg-quick"}], "free": 38211}, "fixed": []},
 {"kind": "awg1", "title": "AmneziaWG 1.5", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 31001, "default": 31001, "conflicts": [], "free": null}, "fixed": []},
 {"kind": "awg2", "title": "AmneziaWG 2.0", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 41002, "default": 41002, "conflicts": [], "free": null}, "fixed": []},
 {"kind": "awg3", "title": "AmneziaWG 3.1", "available": true, "installed": false, "supported": false, "why": "нет ядра AmneziaWG 3.1", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 51003, "default": 51003, "conflicts": [], "free": null}, "fixed": []}]}'
t_ports() { K3_GW_PICK="wg awg"; k3_gw_ports_step; echo "RC=$? PORTS=${K3_GW_SELPORT[*]} SKIP=${K3_GW_SELSKIP[*]} MAN=${K3_GW_SELMAN[*]}"; }
o=$(printf '1\n2\n5000\n2\n3\n\n' | lib t_ports 2>&1 | strip)
printf '%s' "$o" | grep -q 'RC=0 PORTS=5000 31001 41002 51003 SKIP=0 1 0 2 MAN=1 0 0 0' \
  && printf '%s' "$o" | grep -q 'AmneziaWG 3.1 *51003/udp *✗ пропущу: здесь не работает$' \
  && printf '%s' "$o" | grep -q '^AmneziaWG 3.1 здесь не работает: нет ядра AmneziaWG 3.1$' \
  && ok "строка порта: свой порт (5000), «пропустить»; неработающий здесь — пропущен с причиной" || bad "правка портов" "$o"
o=$(printf '1\n2\n31001\n\n\n' | lib t_ports 2>&1 | strip)
printf '%s' "$o" | grep -q 'Порт 31001 уже у AmneziaWG 1.5' && printf '%s' "$o" | grep -q 'RC=0 PORTS=38211 31001' \
  && ok "свой порт, уже отданный другому выбранному протоколу (UDP), не принимается" || bad "повтор порта" "$o"
o=$(printf '0\n' | lib t_ports 2>&1 | strip)
printf '%s' "$o" | grep -q 'RC=1' && ok "«0» на экране портов — назад" || bad "назад с портов" "$o"
t_ports2() { K3_GW_PICK="wg awg"; k3_gw_ports_step; k3_gw_ports_step; echo "PORTS=${K3_GW_SELPORT[*]} SKIP=${K3_GW_SELSKIP[*]}"; }
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n2\n5000\n\n\n' | lib t_ports2 2>&1 | strip)
[ "$(gw_calls | grep -c 'inbound plan')" = "1" ] && printf '%s' "$o" | grep -q 'PORTS=5000 31001 41002 51003 SKIP=0 0 0 2' \
  && ok "назад к портам при том же выборе — прежний план и правки (порты не меняются)" || bad "повторный экран портов" "$o | $(gw_seq)"
use_root gw-ports2; fake_ctl; engine_nft
gwf inbound_plan.json '{"ok": true, "inbounds": [
 {"kind": "wg", "title": "WireGuard", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 51820, "default": 51820, "conflicts": [], "free": null}, "fixed": []},
 {"kind": "l2tp", "title": "L2TP/IPsec", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": null, "fixed": [{"proto": "udp", "port": 1701, "conflicts": []}, {"proto": "udp", "port": 500, "conflicts": []}, {"proto": "udp", "port": 4500, "conflicts": [{"kind": "listener", "detail": "его слушает charon"}]}]}]}'
t_ports3() { K3_GW_PICK="wg l2tp"; k3_gw_ports_step; echo "RC=$? PORTS=${K3_GW_SELPORT[*]} SKIP=${K3_GW_SELSKIP[*]}"; }
o=$(printf '2\n3\n1\n2\n4500\n\n\n' | lib t_ports3 2>&1 | strip)
printf '%s' "$o" | grep -q 'L2TP/IPsec.*✗ пропущу: порт занят$' && printf '%s' "$o" | grep -q '^       4500/udp — его слушает charon$' \
  && printf '%s' "$o" | grep -q 'Порт 4500 — постоянный порт L2TP/IPsec' \
  && printf '%s' "$o" | grep -q 'RC=0 PORTS=51820  SKIP=0 0' \
  && ok "постоянный порт занят — строка пропущена (можно вернуть); свой порт на постоянном порту другого входа не принимается" || bad "постоянные порты" "$o"
use_root gw-conflict; fake_ctl; engine_nft
gwf inbound_install.wg.once.json '{"ok": false, "error": "Порт 51820/udp занят: его слушает nginx. Взять всё равно — с --force.", "code": 3, "data": {"kind": "listener", "conflicts": []}}'
gwf inbound_install.wg.once.rc 3
gwf inbound_install.wg.json '{"ok": true, "kind": "wg", "record": {"enabled": true, "port": 34567}, "notes": ["порт 51820/udp занят (его слушает nginx) — взял 34567"]}'
gwf inbound_install.wg.err '@K3:warn порт 51820/udp занят (его слушает nginx) — взял 34567'    # как пишет ядро: и в stderr
t_inst() { K3_TEST_TTY=1; k3_gw_install_one wg 51820 1 1; echo "RC=$?"; }
o=$(printf '1\n' | lib t_inst 2>&1 | strip)
[ "$(gw_seq)" = "gw inbound install wg --port 51820 --json;gw inbound install wg --json;" ] && printf '%s' "$o" | grep -q 'Порт 51820/udp занят: его слушает nginx\.' \
  && ! printf '%s' "$o" | grep -q -- '--force' && printf '%s' "$o" | grep -q '✓ порт 34567' && [ "$(printf '%s\n' "$o" | grep -c 'взял 34567')" = "1" ] \
  && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "порт занят при установке (код 3) → «1» — порт подбирает ядро (повтор без --port), его заметка — один раз" || bad "конфликт при установке" "$o | $(gw_seq)"
use_root gw-conflict2; fake_ctl; engine_nft
gwf inbound_install.wg.once.json '{"ok": false, "error": "Порт 51820/udp занят: его слушает nginx. Взять всё равно — с --force.", "code": 3, "data": {}}'
gwf inbound_install.wg.once.rc 3
o=$(printf '2\n6000\n' | lib t_inst 2>&1 | strip)
gw_calls | tail -1 | grep -qx 'gw inbound install wg --port 6000 --json' && printf '%s' "$o" | grep -q 'RC=0' && ok "«2» — свой порт: повтор с --port 6000" || bad "свой порт при конфликте" "$(gw_seq)"
gwf inbound_install.wg.once.json '{"ok": false, "error": "Порт 51820/udp занят: его слушает nginx.", "code": 3, "data": {}}'
gwf inbound_install.wg.once.rc 3
: > "$CALLS/kaskadctl.log"
o=$(printf '3\n' | lib t_inst 2>&1 | strip)
[ "$(gw_calls | wc -l | tr -d ' ')" = "1" ] && printf '%s' "$o" | grep -q 'WireGuard пропущен' && printf '%s' "$o" | grep -q 'RC=1' \
  && ok "«3» — протокол пропущен, остальные ставятся дальше" || bad "пропуск при конфликте" "$o"

echo "── 20. «Протоколы»: выключить, порт, удалить, поставить, PPTP, L2TP без шифрования ──"
t_proto() { K3_TEST_TTY=1; k3_gw_protocols; }
use_root gw-proto; fake_ctl
gw_st true '{"wg": {"enabled": true, "port": 51820}, "l2tp": {"enabled": true, "plain": false}}' 1 1
gwf inbound_list.json "$(gw_list "wg l2tp")"
o=$(printf '0\n' | lib t_proto 2>&1 | strip)
printf '%s' "$o" | grep -q ' 1) WireGuard *51820/udp *работает · устройств 1, сессий 0' && printf '%s' "$o" | grep -q ' 7) SSTP *- *не установлен' \
  && ok "список протоколов: состояние, порт, устройства и сессии — из gw inbound list" || bad "список протоколов" "$o"
gwf inbound_list.json "$(gw_list "wg l2tp" | jq -c '(.inbounds[] | select(.kind == "l2tp") | .ports) = ["1701/udp", "500/udp", "4500/udp"]')"
o=$(printf '0\n' | lib t_proto 2>&1 | strip)
cols=$(printf '%s\n' "$o" | grep -E '^ +(1\) WireGuard|5\) L2TP/IPsec)' | python3 -c 'import sys; print(" ".join(str(l.index("работает")) for l in sys.stdin.read().splitlines()))')
printf '%s' "$o" | grep -q ' 5) L2TP/IPsec *1701, 500, 4500/udp *работает' && [ "$(printf '%s' "$cols" | tr ' ' '\n' | sort -u | wc -l | tr -d ' ')" = 1 ] \
  && ok "L2TP: порты «1701, 500, 4500/udp» — столбец не съезжает (состояние с той же позиции, что у WireGuard)" || bad "столбец портов L2TP" "$cols | $o"
gwf inbound_list.json "$(gw_list "wg l2tp")"
o=$(printf '1\n1\n1\n\n0\n0\n' | lib t_proto 2>&1 | strip)
gw_calls | grep -qx 'gw inbound disable wg --json' && ok "карточка → «Выключить» (с вопросом) → gw inbound disable" || bad "выключить вход" "$(gw_seq)"
# 3.3.7.15 F9: «Сменить порт» — через «Настройки протокола»: gw inbound set … --dry → «Применить» → --yes
gwf inbound_set.wg.once.json '{"ok": false, "error": "Порт 5000/udp занят: его слушает nginx. Взять всё равно — с --force.", "code": 3, "data": {}}'
gwf inbound_set.wg.once.rc 3
gwf inbound_set.wg.json '{"ok": true, "kind": "wg", "port": 5000, "changed": {"port": {"old": 51820, "new": 5000}}, "via_xray": false, "lines": ["WireGuard: что изменится — порт 51820 → 5000"], "reissue": [{"id": "d-00000001", "name": "Телефон", "user_name": "Мама"}], "text": "Устройства с прежними настройками WireGuard не подключатся — выдайте им настройки заново: Телефон (Мама).", "pending": null}'
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n2\n5000\n1\n1\n\n0\n0\n' | lib t_proto 2>&1 | strip)
gw_calls | grep -qx 'gw inbound set wg --port 5000 --dry --json' && gw_calls | grep -qx 'gw inbound set wg --port 5000 --dry --force --json' \
  && gw_calls | grep -qx 'gw inbound set wg --port 5000 --yes --force --json' && ! gw_calls | grep -q 'gw inbound port' \
  && printf '%s' "$o" | grep -q 'Порт 5000/udp занят: его слушает nginx\.' && printf '%s' "$o" | grep -q '\[OK\] WireGuard: порт 5000\.$' \
  && printf '%s' "$o" | flat | grep -q 'не подключатся — выдайте им настройки заново: Телефон (Мама)' \
  && ok "«Сменить порт»: занят → причина и «всё равно?» → --dry --force → «Применить» → --yes --force; кому выдать заново — словами ядра" \
  || bad "смена порта" "$(gw_seq) | $o"
rm -f "$FAKE_DIR/gw/inbound_set.wg.json"
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n3\n1\n\n0\n' | lib t_proto 2>&1 | strip)
gw_calls | grep -qx 'gw inbound uninstall wg --json' && printf '%s' "$o" | flat | grep -q 'Пользователи и их другие протоколы останутся' \
  && ok "«Удалить протокол» (с вопросом) → gw inbound uninstall; пользователи и другие входы остаются" || bad "удаление входа" "$(gw_seq)"
gwf inbound_plan.json '{"ok": true, "inbounds": [{"kind": "sstp", "title": "SSTP", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": {"field": "port", "protos": ["tcp"], "port": 4433, "default": 4433, "conflicts": [], "free": null}, "fixed": []}]}'
: > "$CALLS/kaskadctl.log"
o=$(printf '7\n1\n\n\n0\n0\n' | lib t_proto 2>&1 | strip)
gw_calls | grep -qx 'gw inbound plan sstp --json' && gw_calls | grep -qx 'gw inbound install sstp --port 4433 --json' \
  && ok "не установленный → «Установить»: порты проверяет ядро, Enter — предложенный порт" || bad "установка из «Протоколы»" "$(gw_seq)"
: > "$CALLS/kaskadctl.log"
o=$(printf '6\n1\n2\n\n0\n0\n' | lib t_proto 2>&1 | strip)
printf '%s' "$o" | grep -q 'PPTP шифрует слабо' && ! gw_calls | grep -q 'pptp' && ok "PPTP — с предупреждением о слабом шифровании; «2» — не ставится" || bad "PPTP" "$o"
: > "$CALLS/kaskadctl.log"
o=$(printf '5\n2\n1\n\n0\n0\n' | lib t_proto 2>&1 | strip)
gw_calls | grep -qx 'gw inbound option l2tp plain on --json' && printf '%s' "$o" | grep -q 'L2TP без IPsec не шифрует трафик' \
  && printf '%s' "$o" | grep -q '^ 1) Выключить$' && printf '%s' "$o" | grep -q '^ 2) L2TP без шифрования (для роутеров без IPsec)' \
  && printf '%s' "$o" | grep -q '^ 3) Удалить протокол$' \
  && ok "L2TP: пункты карточки подряд (1, 2, 3); «L2TP без шифрования» — с предупреждением → gw inbound option l2tp plain on" || bad "L2TP без шифрования" "$(gw_seq) | $o"
o=$(printf '1\nабв\n7\n0\n0\n' | lib t_proto 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта: номер от 1 до 4, 0 — назад')" = 2 ] && ! gw_calls | grep -q 'inbound disable\|uninstall' \
  && ok "карточка: слово и чужой номер — «нет такого пункта» со словами, ничего не делается" || bad "карточка: неверный выбор" "$o"
o=$(printf 'abc\n99\n0\n' | lib t_proto 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта: номер от 1 до 12, 0 — назад')" = 2 ] \
  && ok "список протоколов: неверный номер — «Нет такого пункта», как везде" || bad "протоколы: неверный выбор" "$o"
o=$(printf 'y\nу\nu\n0\n' | lib t_proto 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта: номер от 1 до 12, 0 — назад')" = 3 ] && ! printf '%s' "$o" | grep -q 'Шлюз: движок' \
  && ok "T-N5 №6: буквы (y, у, u) на странице протоколов — «Нет такого пункта», движок — только номером" || bad "протоколы: буквы" "$o"
o=$(printf '08\n0\n09\n0\n0\n' | lib t_proto 2>&1 | strip)
printf '%s' "$o" | grep -q 'Шлюз: IKEv2' && printf '%s' "$o" | grep -q 'Шлюз: OpenVPN' && ! printf '%s' "$o" | grep -qi 'value too great' \
  && ok "номер с ведущим нулём (08, 09) — десятичный, меню не падает" || bad "ввод 08" "$o"
gwf inbound_list.json "$(gw_list "wg l2tp ikev2" | jq -c '(.inbounds[] | select(.kind == "ikev2") | .warnings) = ["центр сертификации IKEv2 старого образца — сменить: kaskadctl gw pki rotate ikev2"]')"
gwf pki.rotate.json '{"ok": true, "kind": "ikev2", "created": ["центр сертификации IKEv2 для 203.0.113.10"], "devices": ["d-00000001"], "host": "203.0.113.10", "apply": null}'
: > "$CALLS/kaskadctl.log"
o=$(printf '8\n3\n2\n8\n3\n1\n\n0\n0\n' | lib t_proto 2>&1 | strip)
[ "$(gw_calls | grep -c '^gw pki')" = "1" ] && gw_calls | grep -qx 'gw pki rotate ikev2 --yes --json' \
  && printf '%s' "$o" | grep -q '! центр сертификации IKEv2 старого образца' && printf '%s' "$o" | grep -q '3) Сменить центр сертификации IKEv2' \
  && printf '%s' "$o" | grep -q 'подтверждает только 203.0.113.10' && printf '%s' "$o" | flat | grep -q 'со старыми они больше' \
  && ok "IKEv2: предупреждение ядра в карточке; «Сменить центр сертификации» — с вопросом («2» — ничего), «1» → gw pki rotate --yes" \
  || bad "смена центра сертификации IKEv2" "$(gw_seq) | $o"

echo "── 20а. «Сменить порт» с «Что изменится», ожидание подтверждения, «Вернуть прежние» (3.3.7.15 F9, С-4) ──"
use_root gw-port15; fake_ctl
gw_st true '{"wg": {"enabled": true, "port": 51820}, "vless": {"enabled": true, "port": 8443}}' 1 1
gwf inbound_list.json "$(gw_list "wg")"
P15_DRY='{"ok": true, "dry": true, "kind": "wg", "changed": {"port": {"old": 51820, "new": 5000}}, "via_xray": false, "need_yes": true, "lines": ["WireGuard: что изменится — порт 51820 → 5000", "  WireGuard: без перезапуска — новый порт подхватывается на ходу.", "  Устройства с прежними настройками WireGuard не подключатся — выдайте им настройки заново: Телефон (Мама)."]}'
P15_NOTE='Подтвердите в панели или командой kaskadctl gw pending confirm за 2 мин — иначе настройки вернутся.'
P15_TEXT='Устройства с прежними настройками WireGuard не подключатся — выдайте им настройки заново: Телефон (Мама).'
p15_set() {   # $1 — pending JSON или null: ответ «Что изменится» (первый вызов) и применения (следующие)
    gwf inbound_set.wg.once.json "$P15_DRY"
    gwf inbound_set.wg.json "{\"ok\": true, \"kind\": \"wg\", \"port\": 5000, \"changed\": {\"port\": {\"old\": 51820, \"new\": 5000}}, \"reissue\": [{\"id\": \"d-00000001\", \"name\": \"Телефон\", \"user_name\": \"Мама\"}], \"pending\": $1, $([ "$1" = null ] && printf '"text": "%s"' "$P15_TEXT" || printf '"note": "%s", "text": "%s %s"' "$P15_NOTE" "$P15_TEXT" "$P15_NOTE")}"
    : > "$CALLS/kaskadctl.log"
}
P15_PEND='{"token": "0123456789abcdef", "deadline": 1900000000, "scope": "wg", "left": 120}'
t_p15() { K3_TEST_TTY=1; k3_gw_proto_port wg WireGuard; echo "RC=$?"; }
p15_set null
o=$(printf '5000\n1\n\n' | lib t_p15 2>&1 | strip)
[ "$(gw_seq)" = "gw inbound set wg --port 5000 --dry --json;gw inbound set wg --port 5000 --yes --json;" ] \
  && printf '%s' "$o" | grep -q 'WireGuard: что изменится — порт 51820 → 5000' && printf '%s' "$o" | grep -q 'без перезапуска — новый порт' \
  && printf '%s' "$o" | grep -q '^ *1) Применить$' && printf '%s' "$o" | grep -q '^ *2) Отмена$' && printf '%s' "$o" | grep -q 'Выбор \[1\]' \
  && printf '%s' "$o" | grep -q '\[OK\] WireGuard: порт 5000\.$' && printf '%s' "$o" | flat | grep -q 'выдайте им настройки заново: Телефон (Мама)' \
  && ! printf '%s' "$o" | grep -q 'Связь может оборваться' && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "«Сменить порт»: --dry → «Что изменится» → «1) Применить 2) Отмена Выбор [1]» → --yes; кому выдать заново" \
  || bad "F9: смена порта с предпросмотром" "$(gw_seq) | $o"
p15_set null
o=$(printf '5000\n2\n' | lib t_p15 2>&1 | strip)
[ "$(gw_seq)" = "gw inbound set wg --port 5000 --dry --json;" ] && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "«2) Отмена» после «Что изменится» — ничего не применено" || bad "F9: отмена" "$(gw_seq) | $o"
gwf inbound_set.wg.once.json '{"ok": true, "dry": true, "kind": "wg", "changed": {}, "lines": ["WireGuard: менять нечего — эти настройки уже стоят."]}'
: > "$CALLS/kaskadctl.log"
o=$(printf '51820\n\n' | lib t_p15 2>&1 | strip)
[ "$(gw_seq)" = "gw inbound set wg --port 51820 --dry --json;" ] && printf '%s' "$o" | grep -q 'менять нечего' && ! printf '%s' "$o" | grep -q 'Применить' \
  && ok "тот же порт — «менять нечего», без вопроса" || bad "F9: тот же порт" "$(gw_seq) | $o"
# ожидание F7: «1» — подтвердить (с токеном), «2» — вернуть; без выбора по умолчанию
p15_set "$P15_PEND"
o=$(printf '5000\n1\n1\n\n' | lib t_p15 2>&1 | strip)
gw_calls | grep -qx 'gw pending confirm --token 0123456789abcdef --json' && ! gw_calls | grep -q 'gw inbound undo' \
  && printf '%s' "$o" | grep -q 'Связь может оборваться' && printf '%s' "$o" | grep -q '^ *1) Оставить новые настройки$' \
  && printf '%s' "$o" | grep -q '^ *2) Вернуть прежние сейчас$' && printf '%s' "$o" | grep -q 'Выбор (осталось [0-9]:[0-9][0-9]): ' \
  && [ "$(printf '%s\n' "$o" | grep -c 'Выбор \[')" = 1 ] \
  && printf '%s' "$o" | flat | grep -q 'Не ответите — настройки вернутся сами через 2:00\. Подтвердить можно и из новой сессии SSH: kaskadctl gw pending confirm' \
  && ! printf '%s' "$o" | grep -q 'Подтвердите в панели' && printf '%s' "$o" | flat | grep -q 'выдайте им настройки заново: Телефон (Мама)' \
  && printf '%s' "$o" | grep -q '\[OK\] Новые настройки WireGuard оставлены\.' \
  && ok "ожидание: «1» → gw pending confirm --token; вопрос без «[1]», с отсчётом; строка «подтвердите» ядра не дублируется" \
  || bad "F9: подтверждение ожидания" "$(gw_seq) | $o"
p15_set "$P15_PEND"
gwf inbound_undo.wg.json '{"ok": true, "kind": "wg", "undone": {"port": {"old": 5000, "new": 51820}}, "text": "WireGuard: прежние настройки возвращены — порт 5000 → 51820."}'
o=$(printf '5000\n1\n2\n\n' | lib t_p15 2>&1 | strip)
gw_calls | grep -qx 'gw inbound undo wg --json' && ! gw_calls | grep -q 'gw pending confirm' \
  && printf '%s' "$o" | grep -q '\[OK\] WireGuard: прежние настройки возвращены — порт 5000 → 51820\.' \
  && ok "ожидание: «2» → gw inbound undo (прежние сейчас)" || bad "F9: вернуть при ожидании" "$(gw_seq) | $o"
p15_set "$P15_PEND"
o=$(printf '5000\n1\n\nабв\n' | lib t_p15 2>&1 | strip)
! gw_calls | grep -q 'gw pending confirm\|gw inbound undo' && [ "$(printf '%s\n' "$o" | grep -c '1) Оставить новые настройки')" = 3 ] \
  && [ "$(printf '%s\n' "$o" | grep -c 'Введите 1 или 2 — выбора по умолчанию здесь нет')" = 2 ] \
  && printf '%s' "$o" | grep -q 'Ответа нет — настройки вернутся сами' && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "ожидание: Enter и слово — вопрос снова (не подтверждает и не откатывает), конец ввода — ничего не вызвано" \
  || bad "F9: пустой ввод при ожидании" "$(gw_seq) | $o"
p15_set "$P15_PEND"
o=$(lib t_p15 < <(printf '5000\n1\n1') 2>&1 | strip)
! gw_calls | grep -q 'gw pending confirm\|gw inbound undo' && printf '%s' "$o" | grep -q 'Ответа нет — настройки вернутся сами' \
  && ok "ожидание: «1» без Enter и конец ввода (оборванная сессия) — не подтверждает (С-4)" || bad "F9: обрыв при ожидании" "$(gw_seq) | $o"
p15_set '{"token": "0123456789abcdef", "deadline": 1900000000, "scope": "wg", "left": 2}'
o=$({ printf '5000\n1\n'; sleep 3; } | lib t_p15 2>&1 | strip)    # ввод открыт дольше остатка 2 с
! gw_calls | grep -q 'gw pending confirm\|gw inbound undo' && printf '%s' "$o" | grep -q 'Выбор (осталось 0:0[12]): ' \
  && printf '%s' "$o" | flat | grep -q 'Ответа нет — настройки вернутся сами, когда выйдет время. Подтвердить можно из новой сессии SSH: kaskadctl gw pending confirm' \
  && ok "ожидание: время вышло (read -t на остатке 2 с) — ничего не вызвано, настройки вернёт сервер (С-4)" || bad "F9: таймаут ожидания" "$(gw_seq) | $o"
p15_set '{"token": "XYZ", "deadline": 1900000000, "scope": "wg", "left": 120}'
o=$(printf '5000\n1\n1\n\n' | lib t_p15 2>&1 | strip)
! gw_calls | grep -q 'gw pending confirm\|gw inbound undo' && printf '%s' "$o" | flat | grep -q 'Это ожидание меню не опознало — подтвердите его в панели или командой kaskadctl gw pending confirm' \
  && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "ожидание с неопознанным токеном: «1» не подтверждает чужое ожидание — подсказка про панель (REV-3315-a2 П-2)" \
  || bad "F9: токен ожидания не опознан" "$(gw_seq) | $o"
# VLESS и SOCKS5: адрес администратора не узнать (З-6) — «Применить с откатом через 2 мин» → --safe
t_p15v() { K3_TEST_TTY=1; k3_gw_proto_port vless VLESS; echo "RC=$?"; }
gwf inbound_set.vless.once.json '{"ok": true, "dry": true, "kind": "vless", "changed": {"port": {"old": 8443, "new": 9443}}, "via_xray": true, "need_yes": true, "lines": ["VLESS: что изменится — порт 8443 → 9443", "  Если вы открыли панель через VLESS, SOCKS5 или Hysteria2 этого сервера, связь может оборваться."]}'
gwf inbound_set.vless.json '{"ok": true, "kind": "vless", "port": 9443, "changed": {"port": {"old": 8443, "new": 9443}}, "reissue": [], "pending": null, "text": "VLESS: применено — порт 8443 → 9443."}'
: > "$CALLS/kaskadctl.log"
o=$(printf '9443\n2\n\n' | lib t_p15v 2>&1 | strip)
gw_calls | grep -qx 'gw inbound set vless --port 9443 --yes --safe --json' && printf '%s' "$o" | grep -q '^ *2) Применить с откатом через 2 мин$' \
  && printf '%s' "$o" | grep -q '^ *3) Отмена$' && printf '%s' "$o" | grep -q 'связь может оборваться' \
  && ok "VLESS: предупреждение via_xray и «2) Применить с откатом через 2 мин» → --yes --safe" || bad "F9: VLESS --safe" "$(gw_seq) | $o"
gwf inbound_set.vless.once.json '{"ok": true, "dry": true, "kind": "vless", "changed": {"port": {"old": 8443, "new": 9443}}, "via_xray": true, "lines": ["VLESS: что изменится — порт 8443 → 9443"]}'
: > "$CALLS/kaskadctl.log"
o=$(printf '9443\n3\n' | lib t_p15v 2>&1 | strip)
[ "$(gw_seq)" = "gw inbound set vless --port 9443 --dry --json;" ] && ok "VLESS: «3) Отмена» — ничего не применено" || bad "F9: VLESS отмена" "$(gw_seq)"
# карточка: «Ждут новых настроек» и «Вернуть прежние настройки» — пока gw inbound show отдаёт last (последним пунктом)
t_p15c() { K3_TEST_TTY=1; k3_gw_proto_card wg; }
gwf inbound_show.wg.json '{"ok": true, "kind": "wg", "title": "WireGuard", "reissue": {"title": "WireGuard", "hard": [{"id": "d-00000001", "name": "Телефон", "what": ["port"]}], "soft": []}, "last": {"at": 1, "id": "a-12345678", "changed": {"port": {"old": 51820, "new": 5000}}, "text": "порт 51820 → 5000"}}'
: > "$CALLS/kaskadctl.log"
# П6 (3.3.7.15): сначала «Что изменится» (undo --dry: что перезапустится), вопрос — после него, «Вернуть» — --yes
gwf inbound_undo.wg.once.json '{"ok": true, "dry": true, "kind": "wg", "need_yes": false, "what": "порт 5000 → 51820", "lines": ["Порт: перезапуск без обрыва других протоколов (проверка)."]}'
o=$(printf '5\n1\n\n0\n' | lib t_p15c 2>&1 | strip)
gw_calls | grep -qx 'gw inbound show wg --json' \
  && [ "$(gw_calls | grep '^gw inbound undo' | tr '\n' ';')" = "gw inbound undo wg --dry --json;gw inbound undo wg --yes --json;" ] \
  && printf '%s' "$o" | grep -q 'Ждут новых настроек: Телефон' && printf '%s' "$o" | grep -q '^ 4) Понятная подпись протокола' \
  && printf '%s' "$o" | grep -q '^ 5) Вернуть прежние настройки$' \
  && printf '%s' "$o" | flat | grep -q 'перезапуск без обрыва других протоколов (проверка)\..*Вернуть прежние настройки WireGuard? Последнее применение: порт 51820 → 5000\.' \
  && printf '%s' "$o" | grep -q '\[OK\] WireGuard: прежние настройки возвращены' \
  && ok "карточка: «Ждут новых настроек», «5) Вернуть прежние настройки» (прежние номера на месте) → --dry, план до вопроса → gw inbound undo --yes" \
  || bad "F9: карточка — вернуть прежние" "$(gw_seq) | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '5\n2\n\n0\n' | lib t_p15c 2>&1 | strip)
[ "$(gw_calls | grep '^gw inbound undo' | tr '\n' ';')" = "gw inbound undo wg --dry --json;" ] \
  && ok "карточка: «Вернуть прежние» → «2) Отмена» — только план, ничего не возвращено (П6)" \
  || bad "F9: карточка — отмена возврата" "$(gw_seq) | $o"
gwf inbound_show.wg.json '{"ok": true, "kind": "wg", "reissue": {"title": "WireGuard", "hard": [], "soft": []}, "last": null}'
: > "$CALLS/kaskadctl.log"
o=$(printf '5\n0\n' | lib t_p15c 2>&1 | strip)
! printf '%s' "$o" | grep -q 'Вернуть прежние\|Ждут новых' && printf '%s' "$o" | grep -q 'Нет такого пункта: номер от 1 до 4' \
  && ok "карточка: без последнего применения — пункта «Вернуть прежние» и строки «ждут» нет" || bad "F9: карточка без last" "$o"
# EN: тексты меню без русского
t_p15en() { k3_lang_set en; K3_TEST_TTY=1; k3_gw_proto_port wg WireGuard; echo "RC=$?"; }
gwf inbound_set.wg.once.json '{"ok": true, "dry": true, "kind": "wg", "changed": {"port": {"old": 51820, "new": 5000}}, "via_xray": false, "lines": ["WireGuard: what changes — port 51820 → 5000"]}'
gwf inbound_set.wg.json "{\"ok\": true, \"kind\": \"wg\", \"port\": 5000, \"reissue\": [], \"pending\": $P15_PEND, \"note\": \"Confirm.\", \"text\": \"WireGuard: applied. Confirm.\"}"
: > "$CALLS/kaskadctl.log"
o=$(printf '5000\n1\n\n' | lib t_p15en 2>&1 | strip)
! printf '%s' "$o" | LC_ALL=en_US.UTF-8 grep -q '[А-Яа-яЁё]' && printf '%s' "$o" | grep -q '1) Apply' && printf '%s' "$o" | grep -q '1) Keep the new settings' \
  && printf '%s' "$o" | grep -q 'Choice (2:00 left): \|Choice (1:59 left): ' && printf '%s' "$o" | grep -q 'No answer — the settings return by themselves' \
  && ok "EN: «Сменить порт» и ожидание — без русского" || bad "F9: EN" "$o"

echo "── 21. «Пользователи и устройства» ──"
t_people() { K3_TEST_TTY=1; k3_gw_people; }
use_root gw-people; fake_ctl
gw_st true '{"wg": {}, "awg3": {}}' 3 2
gwf user_list.json '{"ok": true, "users": [{"id": "u-00000001", "name": "Мама", "route": null, "enabled": true, "devices": 2}, {"id": "u-00000002", "name": "Дача", "route": {"via": "direct", "fail": "block"}, "enabled": true, "devices": 1}]}'
gwf device_list.json '{"ok": true, "devices": [
 {"id": "d-00000001", "user": "u-00000001", "name": "Телефон", "kind": "phone", "platform": "android", "login": "telefon-0001", "access": ["awg3"], "route": null, "enabled": true},
 {"id": "d-00000003", "user": "u-00000002", "name": "Роутер", "kind": "router", "platform": "keenetic", "login": "router-0003", "access": ["wg"], "route": {"via": "exit:e-3fa9c1d2b4", "fail": "direct"}, "enabled": false},
 {"id": "d-00000002", "user": "u-00000001", "name": "Ноутбук", "kind": "pc", "platform": "linux", "login": "noutbuk-0002", "access": ["wg", "awg3"], "route": null, "enabled": true}]}'
gwf exits.json '{"ok": true, "exits": [{"id": "e-3fa9c1d2b4", "index": 1, "name": "Вена", "kind": "vless", "host": "198.51.100.31", "usable": true, "enabled": true}]}'
gwf inbound_list.json "$(gw_list "wg awg3")"
gwf issue.json "$GW_ISSUE"
o=$(printf '0\n' | lib t_people 2>&1 | strip)
printf '%s' "$o" | grep -q '👤 Мама · маршрут: как по умолчанию' \
  && printf '%s' "$o" | grep -q '   1) Телефон — Android · протоколы: AmneziaWG 3.1 · маршрут: как у пользователя' \
  && printf '%s' "$o" | grep -q '   2) Ноутбук — Linux · протоколы: WireGuard, AmneziaWG 3.1 · маршрут: как у пользователя' \
  && printf '%s' "$o" | grep -q '👤 Дача · маршрут: напрямую' \
  && printf '%s' "$o" | flat | grep -q ' 3) Роутер — Роутер Keenetic · протоколы: WireGuard · маршрут: выход Вена, при отказе — напрямую · выключено' \
  && ok "список: пользователи, их устройства (номера по порядку), входы, маршруты, выключенные" || bad "список пользователей" "$o"
# M6 332c №10: у человека без своего маршрута — действующий («как по умолчанию — сейчас: …», по route_effective ядра);
# устройство с именем, равным платформе («iPhone / iPad»), — без повтора платформы
cp "$FAKE_DIR/gw/user_list.json" "$W/ul.keep"; cp "$FAKE_DIR/gw/device_list.json" "$W/dl.keep"
gwf user_list.json '{"ok": true, "users": [{"id": "u-00000001", "name": "Мама", "route": null, "route_effective": {"via": "pool", "fail": "direct", "from": "default"}, "enabled": true, "devices": 1}]}'
gwf device_list.json '{"ok": true, "devices": [
 {"id": "d-00000001", "user": "u-00000001", "name": "iPhone / iPad", "kind": "phone", "platform": "ios", "login": "iphone-0001", "access": ["awg3"], "route": null, "route_effective": {"via": "pool", "fail": "direct", "from": "default"}, "enabled": true}]}'
o=$(printf '0\n' | lib t_people 2>&1 | strip)
printf '%s' "$o" | grep -q '👤 Мама · маршрут: как по умолчанию — сейчас: пул выходов, при отказе — напрямую$' \
  && ok "17 → 2: у пользователя без своего маршрута — «как по умолчанию — сейчас: пул выходов, при отказе — напрямую»" || bad "17 → 2: маршрут пользователя" "$o"
t_people_en() { K3_LANG=en; t_people; }
o=$(printf '0\n' | lib t_people_en 2>&1 | strip)
printf '%s' "$o" | grep -q '👤 Мама · route: as default — now: exit pool, on failure — direct$' \
  && printf '%s' "$o" | grep -q '   1) iPhone / iPad · protocols: AmneziaWG 3.1 · route: ' && ! printf '%s' "$o" | grep -q 'iPhone / iPad — iPhone / iPad' \
  && ok "17 → 2 EN: «route: as default — now: exit pool…», «iPhone / iPad» без повтора платформы" || bad "17 → 2 EN" "$o"
cp "$W/ul.keep" "$FAKE_DIR/gw/user_list.json"; cp "$W/dl.keep" "$FAKE_DIR/gw/device_list.json"
: > "$CALLS/kaskadctl.log"; o=$(printf '1\n2\n0\n0\n' | lib t_people 2>&1 | strip)
gw_calls | grep -qx 'gw issue d-00000002 --json' && ok "«Выдать настройки» → номер устройства → gw issue" || bad "выдача из списка" "$(gw_seq)"
: > "$CALLS/kaskadctl.log"; o=$(printf '4\n1\n1\n2\n0\n0\n' | lib t_people 2>&1 | strip)
gw_calls | grep -qx 'gw device grant d-00000001 wg --json' && gw_calls | grep -qx 'gw device revoke d-00000001 awg3 --json' \
  && ok "«Входы устройства»: номер не выданного — grant, выданного — revoke" || bad "входы устройства" "$(gw_seq)"
: > "$CALLS/kaskadctl.log"; o=$(printf '6\n1\n4\n\n6\n1\n2\n2\n\n6\n2\n3\n\n\n7\n2\n1\n\n0\n' | lib t_people 2>&1 | strip)
gw_calls | grep -qx 'gw device route d-00000001 direct --json' && gw_calls | grep -qx 'gw device route d-00000001 pool --fail direct --json' \
  && gw_calls | grep -qx 'gw device route d-00000002 exit:e-3fa9c1d2b4 --json' && gw_calls | grep -qx 'gw user route u-00000002 inherit --json' \
  && ok "маршруты: напрямую; пул с «при отказе — напрямую»; один выход (Enter — политика как сейчас: без --fail, ядро её сохраняет); пользователь — «как по умолчанию»" || bad "маршруты" "$(gw_seq)"
: > "$CALLS/kaskadctl.log"; o=$(printf '8\n3\n1\n\n9\n1\n1\n\n3\nПапа\n\n0\n' | lib t_people 2>&1 | strip)
gw_calls | grep -qx 'gw device del d-00000003 --json' && gw_calls | grep -qx 'gw user del u-00000001 --force --json' && gw_calls | grep -qx 'gw user add Папа --json' \
  && ok "удалить устройство; удалить пользователя с устройствами (--force после вопроса); добавить пользователя" || bad "удаление и добавление" "$(gw_seq)"
gwf device_reset.json '{"ok": true, "device": {"id": "d-00000002", "name": "Ноутбук"}, "kinds": ["wg", "awg3"], "also": [], "apply": null}'
: > "$CALLS/kaskadctl.log"; o=$(printf '5\n2\n1\n1\n0\n0\n' | lib t_people 2>&1 | strip)
[ "$(gw_seq)" = "gw user list --json;gw device list --json;gw exits --json;gw device reset d-00000002 --json;gw user list --json;gw device list --json;gw exits --json;" ] \
  && printf '%s' "$o" | grep -q '5) Сменить ключи и пароли устройства' && printf '%s' "$o" | grep -q '1) Все протоколы (WireGuard, AmneziaWG 3.1)' \
  && printf '%s' "$o" | grep -q 'подключения прерваны' && printf '%s' "$o" | flat | grep -q 'Другие устройства не затронуты' \
  && ok "«Сменить ключи и пароли»: все входы — с вопросом → gw device reset; «0» — настройки выдать позже" || bad "смена ключей: все входы" "$(gw_seq) | $o"
gwf device_reset.json '{"ok": true, "device": {"id": "d-00000002", "name": "Ноутбук"}, "kinds": ["awg3"], "also": ["ikev2"], "apply": null}'
: > "$CALLS/kaskadctl.log"; o=$(printf '5\n2\n2\n2\n1\n\n0\n0\n' | lib t_people 2>&1 | strip)
gw_calls | grep -qx 'gw device reset d-00000002 --inbound awg3 --json' && gw_calls | grep -qx 'gw issue d-00000002 --json' \
  && printf '%s' "$o" | grep -q 'Общий пароль сменился и у: IKEv2' \
  && ok "один вход → --inbound; общий пароль у других входов — названы; Enter — сразу выдача новых настроек" || bad "смена ключей: один вход" "$(gw_seq) | $o"
: > "$CALLS/kaskadctl.log"; o=$(printf '5\n1\n2\n0\n' | lib t_people 2>&1 | strip)
! gw_calls | grep -q 'device reset' && ok "устройство с одним входом: без выбора входа, «2» — ничего не меняется" || bad "смена ключей: отказ" "$(gw_seq)"
gwf offers.json '{"ok": true, "platform": "keenetic", "notes": ["AmneziaWG 3.1 KeeneticOS пока не умеет; на прошивках до 5.1.1 — линия 1.0 или WireGuard."], "offers": [
 {"inbound": "awg2", "format": "conf", "note": "KeeneticOS 5.1.1 и новее", "title": "AmneziaWG 2.0", "installed": false, "enabled": false},
 {"inbound": "wg", "format": "conf", "note": "файл .conf: «Загрузить из файла»", "title": "WireGuard", "installed": true, "enabled": true},
 {"inbound": "l2tp", "format": "guide", "note": "L2TP/IPsec", "title": "L2TP/IPsec", "installed": false, "enabled": false}]}'
gwf device_add.json '{"ok": true, "device": {"id": "d-00000004", "login": "router-0004", "name": "Роутер", "access": ["wg"]}}'
: > "$CALLS/kaskadctl.log"; o=$(printf '2\n2\n7\n\n\n0\n0\n' | lib t_people 2>&1 | strip)
gw_calls | grep -qx 'gw offers --platform keenetic --json' && gw_calls | grep -qx 'gw device add u-00000002 Роутер --kind router --platform keenetic --access wg --json' \
  && gw_calls | grep -qx 'gw issue d-00000004 --json' && printf '%s' "$o" | grep -q '\[x\] 1) WireGuard — файл .conf: «Загрузить из файла»' \
  && printf '%s' "$o" | grep -q 'KeeneticOS пока не умеет' \
  && ok "новое устройство: платформа, имя по умолчанию по виду, входы по рекомендации ядра (первый рабочий отмечен), сразу выдача" || bad "новое устройство" "$(gw_seq) | $o"

echo "── 22. выдача: по частям в окне 80×24, QR только целиком, scp, qrencode по запросу, другой вход ──"
t_issue() { K3_TEST_TTY=1; k3_gw_issue_screen d-00000001 telefon-1a2b; echo "RC=$?"; }
use_root gw-issue; fake_ctl; gw_st true '{"awg3": {}}' 1 1
gwf issue.json "$GW_ISSUE"
o=$(printf '2\n0\n' | K3_QRENCODE="$W/no-qrencode" lib t_issue 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c '^vpn://SECRETVPN$')" = "1" ] && printf '%s' "$o" | grep -q 'QR-код не показан: нет программы qrencode' \
  && ! grep -q 'qrencode' "$CALLS/apt.log" && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "qrencode нет, ставить отказались → вместо кода строка «не показан: нет qrencode», ссылка — текстом; apt не трогали" || bad "без qrencode" "$o"
o=$(printf '1\n0\n' | K3_QRENCODE="$W/no-qrencode" lib t_issue 2>&1 | strip)
grep -q 'install -y --no-remove qrencode' "$CALLS/apt.log" && printf '%s' "$o" | grep -q 'qrencode не поставился — QR-коды не покажу' \
  && ok "согласие → apt-get install qrencode (с --no-remove); не встал — сказано" || bad "установка qrencode" "$(cat "$CALLS/apt.log")"
gwf issue.json "$(printf '%s' "$GW_ISSUE" | jq -c '.available = ["awg3", "wg"]')"
: > "$CALLS/kaskadctl.log"; o=$(printf '3\n2\n0\n' | lib t_issue 2>&1 | strip)
[ "$(gw_seq)" = "gw issue d-00000001 --json;gw issue d-00000001 --inbound wg --json;" ] \
  && printf '%s\n' "$o" | grep -qx ' 2) Показать настройки ещё раз' && printf '%s\n' "$o" | grep -qx ' 3) Другой протокол (AmneziaWG 3.1, WireGuard)' \
  && ok "«Другой вход» (входов два) — пункт 3 после «Показать ещё раз» → gw issue --inbound" || bad "другой вход" "$(gw_seq) | $o"
gwf issue.json "$GW_ISSUE"
: > "$CALLS/kaskadctl.log"; o=$(printf 'x\n3\n2\n\n' | lib t_issue 2>&1 | strip)
[ "$(gw_seq)" = "gw issue d-00000001 --json;" ] && [ "$(printf '%s' "$o" | grep -c 'Нет такого пункта')" = "2" ] && [ "$(printf '%s\n' "$o" | grep -c '^vpn://SECRETVPN$')" = "2" ] \
  && printf '%s' "$o" | grep -q 'RC=0' && ok "неверный выбор («x», «3») — со словами; «2» — показать ещё раз (без нового вызова ядра); Enter — готово" || bad "действия выдачи" "$o"
[ "$(printf '%s\n' "$o" | grep -E '^ [0-9]\) ' | sort -u | tr '\n' '|')" = " 0) Готово| 1) Сохранить в файлы — чтобы скачать на компьютер (QR-коды — картинками)| 2) Показать настройки ещё раз|" ] \
  && ! printf '%s' "$o" | grep -q 'Другой вход' && ok "один вход: действия 1, 2, 0 — номера подряд, «Другой вход» не показан (T-G1 C1-4)" || bad "номера действий" "$o"
: > "$CALLS/qrencode.log"; o=$(printf '0\n' | K3_TERM_COLS=80 QR_FAKE_W=117 lib t_issue 2>&1 | strip)
! printf '%s' "$o" | grep -q '\[QR\]' && printf '%s\n' "$o" | grep -qx '  QR-код не влезает в окно (нужно 117×61, сейчас 80×100) — возьмите ссылку.' \
  && printf '%s' "$o" | grep -q 'QR-коды можно сохранить картинками' \
  && ! grep -q 'ansiutf8' "$CALLS/qrencode.log" && ok "QR шире окна (117 > 80): код не печатается, сказано, какое окно нужно, и про картинки" \
  || bad "широкий QR" "$o | $(cat "$CALLS/qrencode.log")"
o=$(printf '0\n' | K3_TERM_COLS=140 K3_TERM_ROWS=40 QR_FAKE_W=117 lib t_issue 2>&1 | strip)
! printf '%s' "$o" | grep -q '\[QR\]' && printf '%s' "$o" | grep -q 'нужно 117×61, сейчас 140×40' \
  && ok "QR выше окна (61 > 40 строк): тоже не печатается — проверяется и высота" || bad "высокий QR" "$o"
o=$(printf '\n\n0\n' | K3_TERM_COLS=140 K3_TERM_ROWS=70 QR_FAKE_W=117 lib t_issue 2>&1 | strip)
printf '%s' "$o" | grep -q 'QR-код — наведите камеру' && printf '%s' "$o" | grep -q '\[QR\]' && ! printf '%s' "$o" | grep -q 'не влезает' \
  && [ "$(printf '%s\n' "$o" | grep -n 'наведите камеру' | cut -d: -f1)" -lt "$(printf '%s\n' "$o" | grep -n '\[QR\]' | cut -d: -f1)" ] \
  && ok "окно больше кода (140×70 ≥ 117×61) — QR печатается, подпись над ним" || bad "QR в большом окне" "$o"
D="$KASKAD_ROOT/root/kaskad-issue/telefon-1a2b"
: > "$CALLS/qr_in.log"; o=$(printf '1\n\n0\n' | K3_TERM_COLS=80 QR_FAKE_W=117 lib t_issue 2>&1 | strip)
[ -s "$D/kaskad-awg3-qr.png" ] && [ "$(perm "$D/kaskad-awg3-qr.png")" = "600" ] && grep -q -- '-t PNG' "$CALLS/qrencode.log" \
  && ! grep -q SECRET "$CALLS/qrencode.log" && grep -q 'vpn://SECRETVPN' "$CALLS/qr_in.log" && printf '%s' "$o" | grep -q "$D/kaskad-awg3-qr.png" \
  && ok "«Сохранить в файлы»: QR — картинкой PNG (0600; секрет — через stdin, не аргументом)" || bad "PNG QR" "$(ls -la "$D" 2>&1) | $o"
printf '%s' "$o" | grep -qF "scp -r root@203.0.113.10:$D ." && printf '%s' "$o" | grep -q 'WinSCP' && printf '%s' "$o" | grep -q 'Termius' \
  && printf '%s' "$o" | grep -qF "rm -r $D" && ! printf '%s' "$o" | grep -q 'перенесёте' \
  && ok "после сохранения — готовая команда scp для своего компьютера, WinSCP/Termius и как удалить файлы" || bad "команда scp" "$o"
o=$(printf '1\n\n0\n' | SSH_CONNECTION="198.51.100.9 50000 203.0.113.10 2222" lib t_issue 2>&1 | strip)
printf '%s' "$o" | grep -qF "scp -r -P 2222 root@203.0.113.10:$D ." && ok "SSH не на 22-м порту — в команде scp -P 2222" || bad "scp -P" "$o"
# Окно 80×24, как у критика: шаги для новичка → ссылка vpn:// (1542 знака — 20 строк) → два QR больше окна, QR файла
# .conf (56×30: выше окна) → .conf.
LONGVPN="vpn://$(printf 'A%.0s' $(seq 1 1536))"
CH1=$(printf 'B%.0s' $(seq 1 115)); CH2=$(printf 'C%.0s' $(seq 1 71))
GW_ISSUE2=$(jq -cn --arg v "$LONGVPN" --arg c1 "$CH1" --arg c2 "$CH2" '{"ok": true, "device": "d-00000001", "name": "Телефон",
  "kind": "awg3", "os": "android", "available": ["awg3"],
  "howto": ["Поставьте приложение AmneziaVPN 5.0.1.5 или новее из Google Play (нет Google Play — файл APK: github.com/amnezia-vpn/amnezia-client/releases).",
            "Скопируйте ссылку ниже — всю, от vpn:// до конца: выделите её мышью (в PuTTY выделенное копируется само, в Терминале macOS — Cmd+C).",
            "Отправьте её себе на телефон — например, в «Избранное» Telegram — и там скопируйте.",
            "В AmneziaVPN: «+» → «Вставить» (из буфера) → «Подключить»."],
  "items": [{"format": "vpn", "title": "AmneziaWG 3.1 — ссылка vpn://", "body": $v, "filename": "",
             "notes": ["Приложения: AmneziaVPN 5.0.1.5+ (vpn://, QR)", "AmneziaVPN: «+» → «Вставить из буфера»."]},
            {"format": "qr", "title": "AmneziaWG 3.1 — QR для AmneziaVPN", "body": [$c1, $c2], "filename": "",
             "notes": ["Кодов: 2, сканируйте в любом порядке."]},
            {"format": "qr", "title": "AmneziaWG 3.1 — QR файла .conf", "body": ["[Interface]\nPrivateKey = SECRETKEY\nAddress = 10.77.3.10/32"],
             "filename": "", "notes": ["Для AmneziaWG и других приложений, которые читают .conf."]},
            {"format": "conf", "title": "AmneziaWG 3.1 — файл .conf", "body": "[Interface]\nPrivateKey = SECRETKEY\nAddress = 10.77.3.10/32",
             "filename": "kaskad-awg3.conf", "notes": []}]}')
gwf issue.json "$GW_ISSUE2"
t_issue80() { K3_TEST_TTY=1; clear() { printf '\f'; }; k3_gw_issue_screen d-00000001 telefon-1a2b; echo "RC=$?"; }
# Текст для человека на экране выдачи (T-G1 C1-2): строки с русскими буквами не шире 78 знаков (приглашение паузы без
# терминала склеивается со следующей строкой — его не считаем), ни одна строка не начинается с «.», «,» или «)».
k3_text_check() {
    python3 -c '
import re, sys
bad = []
for l in sys.stdin.read().replace("\f", "\n").split("\n"):
    l = l.replace("Enter — дальше, 0 — к выбору действий: ", "")
    if (re.search("[А-Яа-яЁё]", l) and len(l) > 78) or re.match(r"\s*[.,)]", l):
        bad.append(l)
print("ok" if not bad else "%d: %s" % (len(bad), bad[:3]))'
}
o=$(printf '\n\n\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 QR_FAKE_W=len lib t_issue80 2>&1 | strip)
# куски между паузами «Enter — дальше»: каждый с приглашением — не больше 24 строк по 80 столбцов
res=$(printf '%s' "$o" | python3 -c '
import sys
text = sys.stdin.read().split("\f", 1)[-1]
pages = text.split("Enter — дальше, 0 — к выбору действий: ")
rows = []
for p in pages:
    lines = p.split("\n")
    rows.append(sum(max(1, -(-len(l) // 80)) for l in lines))
link = [i for i, p in enumerate(pages) if "\nvpn://AAAA" in p and "▸ AmneziaWG 3.1 — ссылка vpn://" in p]
first = [i for i, p in enumerate(pages) if "Как подключить (Android):" in p]
print("pauses=%d max=%d first=%s link=%s" % (len(pages) - 1, max(rows), first, link))
')
[ "$res" = "pauses=2 max=24 first=[0] link=[1]" ] && printf '%s' "$o" | grep -q ' 4\. В AmneziaVPN: «+» → «Вставить» (из буфера) → «Подключить»\.' \
  && ok "80×24: сначала четыре шага, по паузе — ссылка целиком с заголовком, каждый кусок с приглашением влезает в окно" || bad "выдача в 80×24" "$res | $o"
# T-G1 C1-2: текст для человека — по словам, строки не шире 78, продолжение с отступом; ссылка и настройки — как есть
res=$(printf '%s' "$o" | k3_text_check)
[ "$res" = "ok" ] && printf '%s\n' "$o" | grep -qx ' 3\. Отправьте её себе на телефон — например, в «Избранное» Telegram — и там' \
  && printf '%s\n' "$o" | grep -qx '    скопируйте\.' && printf '%s\n' "$o" | grep -qx 'Здесь ключ устройства: не показывайте экран чужим, пересылайте только себе\.' \
  && printf '%s\n' "$o" | grep -q "^$LONGVPN\$" \
  && ok "80×24 (C1-2): шаги и пометки — по словам с отступом, строки ≤ 78, без «.» в начале строки; ссылка не рвётся" || bad "переносы в 80×24" "$res | $o"
# T-G1 C1-3: коды не влезают — одна строка на запись (по самому большому коду) без «Кодов: 2…»; у QR файла .conf —
# «возьмите файл .conf», а не «ссылку»
[ "$(printf '%s\n' "$o" | grep -cx '  QR-коды не влезают в окно (нужно 115×60, сейчас 80×24) — возьмите ссылку\.')" = 1 ] \
  && printf '%s\n' "$o" | grep -qx '  QR-код не влезает в окно (нужно 58×31, сейчас 80×24) — возьмите файл \.conf\.' \
  && ! printf '%s' "$o" | grep -q '71×38' && ! printf '%s' "$o" | grep -q 'Кодов: 2' && ! printf '%s' "$o" | grep -q 'Для AmneziaWG и других' \
  && ! printf '%s' "$o" | grep -q 'используйте ссылку' && ! printf '%s' "$o" | grep -q '\[QR\]' \
  && ok "QR больше окна (C1-3): одна строка на запись; у кодов AmneziaVPN — «ссылку», у QR .conf — «файл .conf»; пометок к невидимым кодам нет" \
  || bad "QR в 80×24" "$o"
o=$(printf '\n\n\n\n0\n' | K3_TERM_COLS=120 K3_TERM_ROWS=62 QR_FAKE_W=len lib t_issue80 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c '\[QR\]')" = 3 ] && printf '%s' "$o" | grep -q 'QR-код 1 из 2 — наведите камеру' \
  && printf '%s' "$o" | grep -q 'QR-код 2 из 2 — наведите камеру' && ! printf '%s' "$o" | grep -q 'не влезает' \
  && printf '%s' "$o" | grep -q 'Кодов: 2, сканируйте в любом порядке\.' \
  && ok "окно 120×62: все коды печатаются, каждый со своей подписью над ним; пометки к ним — на месте" || bad "QR в большом окне (2 кода)" "$o"
o=$(printf '0\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 QR_FAKE_W=len lib t_issue80 2>&1 | strip)
! printf '%s' "$o" | grep -q 'vpn://AAAA' && printf '%s' "$o" | grep -q '1) Сохранить в файлы' && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "«0» на паузе — дальше не показывать, сразу действия" || bad "0 на паузе" "$o"
# T-G1 C1-1: Windows IKEv2 в 80×24 — сначала «Как подключить (Windows)» (шаги — настоящие, из ядра), потом данные для
# подключения, скрипт PowerShell — в конце строкой без текста; текст — пунктом «Показать текст файлов».
WIN_HOWTO=$(PYTHONPATH="$ROOT_DIR/lib" python3 -c 'import json; from kaskad.gw import issue_os; print(json.dumps(issue_os.howto("windows", "ikev2")))')
PS1=$( echo "# Kaskad: IKEv2 for Windows 10/11 (built-in client)"; echo "\$pem = @'"; echo "-----BEGIN CERTIFICATE-----"
       for i in $(seq 1 40); do echo "MIIDdzCCAl+gAwIBAgIUcertline$i"; done
       echo "-----END CERTIFICATE-----"; echo "'@"
       echo "Add-VpnConnection -AllUserConnection -Name \$name -ServerAddress \$server -TunnelType Ikev2 -AuthenticationMethod Eap" )
GW_ISSUE_WIN=$(jq -cn --arg ps "$PS1" --argjson how "$WIN_HOWTO" '{"ok": true, "device": "d-00000002", "name": "Ноутбук", "kind": "ikev2",
  "os": "windows", "available": ["ikev2", "sstp", "openvpn"], "howto": $how,
  "items": [{"format": "ps1", "title": "IKEv2 — скрипт PowerShell", "body": $ps, "filename": "kaskad-ikev2.ps1",
             "notes": ["Сохраните файл и запустите PowerShell от имени администратора: powershell -ExecutionPolicy Bypass -File .\\kaskad-ikev2.ps1. Логин и пароль Windows спросит при первом подключении.",
                       "Скрипт кладёт сертификат центра Kaskad в доверенные корневые сертификаты компьютера."]},
            {"format": "fields", "title": "IKEv2 — данные для подключения", "body": [["Сервер", "203.0.113.10"],
             ["Удалённый идентификатор", "203.0.113.10"], ["Логин (EAP)", "noutbuk-3c4d"], ["Пароль", "SECRETPASS"],
             ["Проверка сервера", "сертификат центра Kaskad, файл kaskad-ikev2-ca.crt"]], "filename": "", "notes": []}]}')
gwf issue.json "$GW_ISSUE_WIN"
t_issuewin() { K3_TEST_TTY=1; clear() { printf '\f'; }; k3_gw_issue_screen d-00000002 noutbuk-3c4d; echo "RC=$?"; }
o=$(printf '\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_issuewin 2>&1 | strip)
res=$(printf '%s' "$o" | python3 -c '
import sys
text = sys.stdin.read().split("\f", 1)[-1]
pages = text.split("Enter — дальше, 0 — к выбору действий: ")
rows = [sum(max(1, -(-len(l) // 80)) for l in p.split("\n")) for p in pages]
first = pages[0]
ok = ("Как подключить (Windows):" in first and " 1. Сохраните скрипт kaskad-ikev2.ps1" in first
      and "Add-VpnConnection" not in text and "BEGIN CERTIFICATE" not in text
      and text.index("▸ IKEv2 — данные для подключения") < text.index("▸ IKEv2 — скрипт PowerShell"))
print("fit=%s order=%s" % ("yes" if max(rows) <= 24 else "no %s" % rows, "ok" if ok else "bad"))
')
[ "$res" = "fit=yes order=ok" ] && [ "$(printf '%s' "$o" | k3_text_check)" = ok ] \
  && printf '%s\n' "$o" | grep -qx ' 3\. Выполните: powershell -ExecutionPolicy Bypass -File \.\\kaskad-ikev2\.ps1' \
  && printf '%s\n' "$o" | grep -q '«Kaskad IKEv2» → «Подключиться»' \
  && printf '%s\n' "$o" | grep -qx '  Его не переписывают с экрана — сохраните файл: «Сохранить в файлы» ниже\.' \
  && printf '%s\n' "$o" | grep -qx '  Пароль: SECRETPASS' \
  && ok "Windows IKEv2 в 80×24 (C1-1): сначала шаги, потом данные для подключения, скрипт — в конце без стены кода" || bad "Windows IKEv2: порядок" "$res | $o"
[ "$(printf '%s\n' "$o" | grep -E '^ [0-9]\) ' | tr '\n' '|')" = " 1) Сохранить в файлы — чтобы скачать на компьютер (QR-коды — картинками)| 2) Показать настройки ещё раз| 3) Показать текст файлов (kaskad-ikev2.ps1)| 4) Другой протокол (IKEv2, SSTP, OpenVPN)| 0) Готово|" ] \
  && ok "действия: 1 сохранить, 2 показать ещё раз, 3 текст файлов, 4 другой вход — подряд" || bad "Windows IKEv2: действия" "$o"
o=$(printf '\n3\n\n\n\n\n\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_issuewin 2>&1 | strip)
printf '%s' "$o" | grep -q '━━━ Текст файлов: Ноутбук — IKEv2 ━━━' && printf '%s\n' "$o" | grep -qx 'Add-VpnConnection .*-TunnelType Ikev2 -AuthenticationMethod Eap' \
  && printf '%s\n' "$o" | grep -qx 'MIIDdzCCAl+gAwIBAgIUcertline40' && [ "$(printf '%s' "$o" | grep -c 'Как подключить (Windows)')" = 1 ] \
  && [ "$(printf '%s' "$o" | sed -n '/━━━ Текст файлов/,/▸ IKEv2 — скрипт PowerShell/p' | grep -c 'Enter — дальше')" = 0 ] \
  && ok "«3) Показать текст файлов» — текст скрипта целиком, по частям, без шагов и записей" || bad "Windows IKEv2: текст файлов" "$o"
D="$KASKAD_ROOT/root/kaskad-issue/noutbuk-3c4d"
o=$(printf '\n1\n\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_issuewin 2>&1 | strip)
[ "$(cat "$D/kaskad-ikev2.ps1" 2>/dev/null)" = "$PS1" ] && [ "$(perm "$D/kaskad-ikev2.ps1")" = "600" ] \
  && printf '%s' "$o" | tr '\n' ' ' | grep -q 'файлы лягут в папку *noutbuk-3c4d:' && [ "$(printf '%s' "$o" | k3_text_check)" = ok ] \
  && ok "«Сохранить в файлы»: скрипт — файлом kaskad-ikev2.ps1 (0600), сказано, в какую папку он ляжет" || bad "Windows IKEv2: сохранение" "$o"
# T-G1 C1-2: инструкция TP-Link — по словам («общи/й ключ» больше не рвётся): слова те же и в том же порядке
TPL=$(PYTHONPATH="$ROOT_DIR/lib" python3 -c 'from kaskad.gw import issue_os; print("\n".join(issue_os.steps("tplink", "l2tp")))')
gwf issue.json "$(jq -cn --arg g "$TPL" '{"ok": true, "device": "d-00000003", "name": "Роутер", "kind": "l2tp", "os": "tplink",
  "available": ["l2tp", "pptp"], "howto": [], "items": [{"format": "guide", "title": "L2TP — как подключить", "body": $g, "filename": "", "notes": []},
  {"format": "fields", "title": "L2TP — данные для подключения", "body": [["Сервер", "203.0.113.10"], ["Общий ключ IPsec (PSK)", "SECRETPSK"],
   ["Логин", "router-0003"], ["Пароль", "SECRETPASS"]], "filename": "", "notes": []}]}')"
t_issuetpl() { K3_TEST_TTY=1; clear() { printf '\f'; }; k3_gw_issue_screen d-00000003 router-0003; echo "RC=$?"; }
o=$(printf '0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_issuetpl 2>&1 | strip)
res=$(printf '%s' "$o" | TPL="$TPL" python3 -c '
import os, sys
text = sys.stdin.read()
body = text.split("────────────── начало ──────────────\n", 1)[1].split("  ──────────────", 1)[0]
print("same" if body.split() == os.environ["TPL"].split() else "differ: %r" % body)
')
[ "$res" = same ] && [ "$(printf '%s' "$o" | k3_text_check)" = ok ] && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "TP-Link в 80×24 (C1-2): инструкция переносится по словам — слова целые и в том же порядке, строки ≤ 78" || bad "TP-Link: переносы" "$res | $o"
gwf issue.json "$GW_ISSUE"

echo "── 23. выходы, адрес для клиентов, включение, состояние ──"
use_root gw-exits; fake_ctl; gw_st true '{"wg": {}}' 1 1
gwf exits.json '{"ok": true, "exits": [{"id": "e-1111111111", "index": 1, "name": "Вена", "kind": "vless", "host": "198.51.100.31", "usable": true, "enabled": true, "state": "alive"},
 {"id": "e-2222222222", "index": 2, "name": "Амстердам", "kind": "awg", "version": "2.0", "host": "198.51.100.32", "usable": false, "enabled": true, "detail": "рукопожатия нет", "state": "dead"}]}'
# ключ выхода добавляется в пул «Прокси для API Telegram» (egress_add_key; здесь — подделка с его строками)
t_exits() { K3_TEST_TTY=1; egress_add_key() { echo "ADD-KEY:$1"; echo "Добавлено ключей: 1."; echo "@K3:egress_test:ok"; echo -e "${GREEN}✓ Telegram доступен через выход (HTTP 302).${NC}"; }
            k3_gw_exits_menu; }
o=$(printf '1\n2\n2\n\n2\n1\n\n3\nvless://NEWKEY\n.\n\n4\n1\n\n0\n' | lib t_exits 2>&1 | strip)
printf '%s' "$o" | grep -q '^ *1) Вена (VLESS, 198.51.100.31) — работает$' \
  && printf '%s' "$o" | grep -q '^ *2) Амстердам (AmneziaWG 2.0, 198.51.100.32) — не работает (рукопожатия нет)$' && ! printf '%s' "$o" | grep -q 'e-1111111111' \
  && printf '%s' "$o" | grep -q 'Маршрут по умолчанию: пул выходов; если выходы недоступны — как у группы (блок)' \
  && ok "таблица выходов: имя, вид и адрес без служебного id (работает / не работает с причиной), маршрут по умолчанию" || bad "экран выходов" "$o"
gw_calls | grep -qx 'gw route exit:e-1111111111 --fail direct --json' && gw_calls | grep -qx 'gw route pool --fail direct --json' \
  && gw_calls | grep -qx 'gw exit e-1111111111 --disable --json' && printf '%s' "$o" | grep -q 'ADD-KEY:vless://NEWKEY' \
  && printf '%s' "$o" | grep -q 'Выход — ваш зарубежный VPN. Его ключ (vless, hy2, ss, trojan, vmess, vpn://):' \
  && printf '%s' "$o" | grep -q '✓ Выход работает: через него открывается интернет' && ! printf '%s' "$o" | grep -q 'Telegram доступен' \
  && ok "маршрут по умолчанию → один выход; «при отказе» → напрямую; ключ VPN → «✓ выход работает» (не про Telegram); выход выключен для шлюза" \
  || bad "действия с выходами" "$(gw_seq) | $o"
jq '.gateway.routes.default = {"via": "direct", "fail": "block"}' "$KASKAD_DIR/state.json" > "$W/st.tmp" && mv "$W/st.tmp" "$KASKAD_DIR/state.json"
: > "$CALLS/kaskadctl.log"; o=$(printf '0\n' | lib t_exits 2>&1 | strip)
printf '%s' "$o" | grep -q 'Маршрут по умолчанию: напрямую\.' && ! printf '%s' "$o" | grep -q 'Если выходы не работают' && ! gw_calls | grep -q '^gw route' \
  && [ "$(printf '%s\n' "$o" | grep -E '^ [0-9]\) ' | tr '\n' '|')" = " 1) Маршрут по умолчанию| 2) Добавить ключ выхода| 3) Выход: выключить или включить для шлюза| 4) Подписки выходов — ключи по ссылке, обновление раз в сутки| 5) Состояние и группы — только просмотр| 6) Переименовать выход (понятное имя)| 0) Назад|" ] \
  && ok "маршрут «напрямую» — пункта «если выходы не работают» нет (он ничего не значит), номера подряд (T-G2b, N-5)" || bad "пункт отказа при direct" "$o"
# T-G2b N-5: выходов нет и маршрут «напрямую» — пункты 1, 2 без пропуска, «выключить выход» не предлагается
gwf exits.json '{"ok": true, "exits": []}'
o=$(printf '6\n5\n0\n' | lib t_exits 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -E '^ [0-9]\) ' | sort -u | tr '\n' '|')" = " 0) Назад| 1) Маршрут по умолчанию| 2) Добавить ключ выхода| 3) Подписки выходов — ключи по ссылке, обновление раз в сутки| 4) Состояние и группы — только просмотр|" ] \
  && printf '%s' "$o" | grep -q 'В пуле нет выходов: добавьте ключ (пункт 2)\.' \
  && [ "$(printf '%s' "$o" | grep -c 'Нет такого пункта: номер от 1 до 4, 0 — назад')" = 2 ] \
  && ok "17 → 3 без выходов: пункты 1, 2, 3 (подписки), 4 (состояние и группы) подряд, подсказка «пункт 2»; 5 и 6 — «нет такого пункта»" || bad "17 → 3 без выходов" "$o"
gwf exits.json '{"ok": true, "exits": [{"id": "e-3333333333", "index": 1, "name": "Рига", "kind": "awg", "version": "3.1", "host": "198.51.100.33", "usable": false, "enabled": true, "alive": false, "detail": "ещё не проверялся", "text": "ещё не проверялся", "state": "unknown"},
 {"id": "e-4444444444", "index": 2, "name": "Вена", "kind": "vless", "host": "198.51.100.34", "usable": false, "enabled": true, "alive": false, "detail": "xray шлюза не отвечает", "text": "ещё не проверялся (xray шлюза не отвечает)", "state": "unknown"}]}'
t_exstep() { K3_TEST_TTY=1; egress_awg_runs() { return 0; }; k3_gw_exits_step; echo "RC=$? ROUTE=$K3_GW_ROUTE"; }   # ядро AWG есть
o=$(printf '1\n' | lib t_exstep 2>&1 | strip)
printf '%s' "$o" | grep -q 'Рига (AmneziaWG 3.1, 198.51.100.33) — ещё не проверялся$' && printf '%s' "$o" | grep -q 'Вена (VLESS, 198.51.100.34) — ещё не проверялся (xray шлюза не отвечает)' \
  && ! printf '%s' "$o" | grep -q 'не работает' && printf '%s' "$o" | grep -q 'RC=0 ROUTE=pool' \
  && ok "мастер, шаг «выходы»: до первой проверки — «ещё не проверялся», а не «не работает»" || bad "выходы до первой проверки" "$o"
t_exstep_nocore() { K3_TEST_TTY=1; egress_awg_runs() { return 1; }; k3_gw_exits_step; echo "RC=$?"; }
o=$(printf '1\n' | lib t_exstep_nocore 2>&1 | strip)
printf '%s' "$o" | grep -q 'Рига (AmneziaWG 3.1, 198.51.100.33) — не заработает: нет ядра AmneziaWG (нужен ключ GitHub' \
  && printf '%s' "$o" | grep -q 'Вена (VLESS, 198.51.100.34) — ещё не проверялся' && ! printf '%s' "$o" | grep -q 'связь через ключи проверена' \
  && ok "мастер, шаг «выходы»: ключ AmneziaWG без ядра — «не заработает: нет ядра» с подсказкой, а не «проверено»" || bad "выход AWG без ядра" "$o"
o=$(printf 'x\n\n' | lib t_exstep 2>&1 | strip)
printf '%s' "$o" | grep -q 'Нет такого пункта: введите 1, 2, 3 или 0 (Enter — через VPN)' && printf '%s' "$o" | grep -q 'RC=0 ROUTE=pool' \
  && printf '%s' "$o" | grep -q '2) Без VPN, с адреса этого сервера (заблокированное в России не откроется)' \
  && ! printf '%s' "$o" | grep -qE 'пул|Telegram' && ok "ключи есть: 1 через VPN (Enter), 2 без VPN (что не откроется), 3 ещё ключ; неверный ввод — со словами; без «пула» и Telegram" \
  || bad "шаг «выходы» с ключами" "$o"
t_pickr() { K3_TEST_TTY=1; k3_gw_pick_route "" <<< "0"; }
o=$(lib t_pickr 2>&1 | strip)
printf '%s' "$o" | grep -q 'Только выход «Рига» (awg, ещё не проверялся)' && ok "выбор маршрута: выход до проверки — «ещё не проверялся»" || bad "выбор маршрута" "$o"
# у ключа без своего имени имя — «вид · адрес» (стенд T-G1, B1-4), состояние — текстом ядра: рукопожатие, «нет ответа
# 35 с — клиенты переходят на другой выход» (C3-1)
gwf exits.json '{"ok": true, "exits": [{"id": "e-6666666666", "index": 1, "name": "AmneziaWG 2.0 · 198.51.100.36", "where": "AmneziaWG 2.0 · 198.51.100.36", "named": false, "kind": "awg", "version": "2.0", "host": "198.51.100.36:51820", "usable": true, "enabled": true, "state": "alive", "text": "работает, рукопожатие 40 с назад"},
 {"id": "e-7777777777", "index": 2, "name": "AmneziaWG 3.1 · 198.51.100.36", "where": "AmneziaWG 3.1 · 198.51.100.36", "named": false, "kind": "awg", "version": "3.1", "host": "198.51.100.36:51831", "usable": false, "enabled": true, "state": "dead", "detail": "нет ответа", "text": "не работает: нет ответа 35 с — клиенты переходят на другой выход"},
 {"id": "e-8888888888", "index": 3, "name": "Вена", "where": "VLESS · 198.51.100.37", "named": true, "kind": "vless", "host": "198.51.100.37:443", "usable": true, "enabled": true, "state": "alive", "fails": 1, "text": "работает, но нет ответа 12 с (проверка 1 из 2)"}]}'
t_extab() { K3_TEST_TTY=1; k3_gw exits && k3_gw_exits_table; k3_gw_pick_route "" <<< "0"; }
o=$(lib t_extab 2>&1 | strip)
printf '%s' "$o" | grep -q '^ *1) AmneziaWG 2.0 · 198.51.100.36 — работает, рукопожатие 40 с назад$' \
  && printf '%s' "$o" | grep -q '^ *2) AmneziaWG 3.1 · 198.51.100.36 — не работает: нет ответа 35 с — клиенты переходят на другой выход$' \
  && printf '%s' "$o" | grep -q '^ *3) Вена (VLESS · 198.51.100.37) — работает, но нет ответа 12 с (проверка 1 из 2)$' \
  && printf '%s' "$o" | grep -q 'Только выход «AmneziaWG 3.1 · 198.51.100.36» (не работает)' \
  && printf '%s' "$o" | grep -q 'Только выход «Вена» (VLESS · 198.51.100.37, работает)' && ! printf '%s' "$o" | grep -qiE 'нога|ноги|e-[0-9]{10}' \
  && ok "выходы: «AmneziaWG 2.0 · адрес» вместо одинаковых «amneziawg», рукопожатие и «нет ответа 35 с — клиенты переходят…»; маршрут — теми же именами" \
  || bad "имена и состояние выходов" "$o"
gwf exits.json '{"ok": true, "exits": []}'
o=$(printf '3\n' | lib t_exstep 2>&1 | strip)
printf '%s' "$o" | grep -q 'Ключа зарубежного VPN пока нет' && printf '%s' "$o" | grep -q '3) Пока без интернета' && printf '%s' "$o" | grep -q 'RC=0 ROUTE=pool' \
  && printf '%s' "$o" | grep -q 'Выход — ваш зарубежный VPN' && ! printf '%s' "$o" | grep -qE 'пул|Telegram' \
  && ok "мастер: ключа нет, «3 — пока без интернета» → маршрут «пул» (интернет появится с ключом); без «пула» и Telegram" || bad "пустой пул" "$o"
printf '%s' "$o" | grep -q '3) Пока без интернета: устройства подключатся к шлюзу, но в интернет не выйдут' \
  && printf '%s' "$o" | grep -q '(и на сайты России тоже), пока не добавите ключ VPN (17 → 3 → 3)' \
  && ok "T-N5 №12: «3 — пока без интернета» честно: подключатся к шлюзу, но в интернет не выйдут, и на сайты России тоже" \
  || bad "мастер: текст «пока без интернета»" "$o"
t_noexit() { K3_TERM_COLS=80; K3_GW_ROUTE=pool; k3_gw_noexit_note; echo "|"; K3_GW_ROUTE=direct; k3_gw_noexit_note; }
printf '{"engine": "nft", "gateway": {"enabled": true, "net": {"base": "10.88.0.0/16"}}}\n' > "$KASKAD_DIR/state.json"
o=$(lib t_noexit 2>&1 | strip | flat)
printf '%s' "$o" | grep -q 'Ключа VPN пока нет: устройства подключатся к шлюзу (его адрес 10.88.0.1 отвечает на ping и DNS), но в интернет не выйдут — и на сайты России тоже, — пока не добавите ключ: 17 → 3 → 3\. | *$' \
  && ok "итог мастера без ключей: устройства подключатся и видят шлюз (ping и DNS адреса .1 базы), интернета нет до первого ключа" \
  || bad "итог мастера без ключей" "$o"
o=$(printf '\nabc\n2\n' | lib t_exstep 2>&1 | strip)
printf '%s' "$o" | grep -q 'Выберите вариант: 1 — вставить ключ VPN, 2 — без VPN, 3 — пока без интернета, 0 — назад' \
  && printf '%s' "$o" | grep -q 'Нет такого пункта: введите 1, 2, 3 или 0\.' && printf '%s' "$o" | grep -q 'RC=0 ROUTE=direct' \
  && [ "$(printf '%s\n' "$o" | grep -c 'шаг 4 из 6')" = 1 ] \
  && ok "мастер: Enter и неверный ввод — со словами и без перерисовки экрана; «2» — без VPN (direct)" || bad "напрямую" "$o"
# ключ добавлен в мастере: проверка связи → «✓ выход работает», и в списке он уже не «ещё не проверялся»
gwf exits.once.json '{"ok": true, "exits": []}'
gwf exits.json '{"ok": true, "exits": [{"id": "e-5555555555", "index": 1, "name": "Хельсинки", "kind": "vless", "host": "198.51.100.35", "usable": false, "enabled": true, "alive": false, "detail": "ещё не проверялся", "text": "ещё не проверялся", "state": "unknown"}]}'
t_exadd() { K3_TEST_TTY=1; egress_add_key() { mkdir -p "${EGRESS_UP%/*}"; printf '%s\n' "$1" >> "$EGRESS_UP"; echo "Добавлено ключей: 1."
                                               echo "@K3:egress_test:ok"; echo -e "${GREEN}✓ Telegram доступен через выход (HTTP 302).${NC}"; }
            k3_gw_exits_step; echo "RC=$? ROUTE=$K3_GW_ROUTE"; }
o=$(printf '1\nvless://KEY1\n.\n\n\n' | lib t_exadd 2>&1 | strip)
printf '%s' "$o" | grep -q '✓ Выход работает: через него открывается интернет' && ! printf '%s' "$o" | grep -q 'Telegram доступен' \
  && printf '%s' "$o" | grep -q 'Хельсинки (VLESS, 198.51.100.35) — работает (проверен при добавлении)' && ! printf '%s' "$o" | grep -q 'ещё не проверялся' \
  && printf '%s' "$o" | grep -q 'RC=0 ROUTE=pool' && grep -qx 'vless://KEY1' "$KASKAD_DIR/egress/upstream.txt" \
  && ok "мастер: вставлен ключ → «✓ выход работает» (не про Telegram), в списке — «работает (проверен при добавлении)»; Enter — через VPN" \
  || bad "ключ в мастере" "$o"
# второй ключ к уже рабочему: бот проверяет связь через весь пул — «выход работает» про новый ключ не говорим (B1-4)
gwf exits.once.json '{"ok": true, "exits": []}'
t_exadd3() { K3_TEST_TTY=1; k3_gw_enabled() { return 1; }
             egress_add_key() { printf '%s\n' "$1" >> "$EGRESS_UP"; echo "Добавлено ключей: 1."
                                echo "@K3:egress_test:ok"; echo -e "${GREEN}✓ Telegram доступен через выход (HTTP 302).${NC}"; }
             k3_gw_exits_step; echo "RC=$? ROUTE=$K3_GW_ROUTE"; }
o=$(printf '1\nvless://KEY2\n.\n\n\n' | lib t_exadd3 2>&1 | strip)
printf '%s' "$o" | grep -q '✓ Связь есть. Новый ключ проверится после включения шлюза' && ! printf '%s' "$o" | grep -q 'Выход работает' \
  && ! printf '%s' "$o" | grep -q 'проверен при добавлении' && grep -qx 'vless://KEY2' "$KASKAD_DIR/egress/upstream.txt" \
  && ok "второй ключ: проверка бота шла через весь пул — «связь есть, ключ проверится после включения», не «выход работает»" \
  || bad "второй ключ в мастере" "$o"
t_words() { k3_gw_enabled() { return 0; }; printf '%s\n' "@K3:egress_test:ok" "✓ Telegram доступен через выход (HTTP 302)." | k3_gw_egr_words "" 1; }
o=$(lib t_words 2>&1 | strip)
printf '%s' "$o" | grep -q '✓ Связь через выходы есть. Новый ключ шлюз проверит сам за полминуты' && ! printf '%s' "$o" | grep -q '@K3:' \
  && ok "шлюз включён: «новый ключ шлюз проверит сам за полминуты — смотрите список выходов»" || bad "слова проверки при включённом шлюзе" "$o"
rm -f "$KASKAD_DIR/egress/upstream.txt"
gwf exits.once.json '{"ok": true, "exits": []}'
t_exadd2() { K3_TEST_TTY=1; egress_add_key() { echo "@K3:egress_test:fail"; echo -e "${RED}✗ Через выход Telegram недоступен (journalctl -u kaskad-egress).${NC}"; }
             k3_gw_exits_step; echo "RC=$? ROUTE=$K3_GW_ROUTE"; }
o=$(printf '1\nvless://BAD\n.\n\n\n' | lib t_exadd2 2>&1 | strip)
printf '%s' "$o" | grep -q '✗ Выход не ответил: проверьте ключ' && ! printf '%s' "$o" | grep -q 'проверен при добавлении' && ! printf '%s' "$o" | grep -q 'Telegram' \
  && ok "ключ не ответил — «✗ выход не ответил: проверьте ключ», список его «работающим» не называет" || bad "плохой ключ в мастере" "$o"
gwf exits.json '{"ok": true, "exits": []}'
o=$(printf '1\n\n' | lib t_exstep 2>&1 | strip)          # вставки нет (после пустой строки ввод кончился, как у человека)
printf '%s' "$o" | grep -q 'Ничего не вставлено — ключ не добавлен' && [ "$(printf '%s\n' "$o" | grep -c 'шаг 4 из 6')" = 2 ] \
  && ok "пустая вставка — «ключ не добавлен», назад к выбору" || bad "пустая вставка" "$o"
gwf host.json '{"ok": true, "host": "203.0.113.10", "auto": true, "apply": null}'
t_host() { K3_TEST_TTY=1; k3_gw_host_menu; }
: > "$CALLS/kaskadctl.log"; o=$(printf '1\nvpn.example.org\n\n2\n\n0\n' | lib t_host 2>&1 | strip)
[ "$(gw_seq)" = "gw host --json;gw addresses --json;gw host vpn.example.org --json;gw host --json;gw addresses --json;gw host --detect --json;gw host --json;gw addresses --json;" ] \
  && printf '%s' "$o" | grep -q 'Сейчас: 203.0.113.10 (определён сам)' && printf '%s' "$o" | grep -q 'выдайте устройствам настройки заново' \
  && ok "«Адрес для клиентов»: показать, задать имя, определить заново" || bad "адрес для клиентов" "$(gw_seq)"
t_toggle() { K3_TEST_TTY=1; k3_gw_toggle; }
: > "$CALLS/kaskadctl.log"; o=$(printf '1\n\n' | lib t_toggle 2>&1 | strip)
[ "$(gw_seq)" = "gw disable --json;" ] && printf '%s' "$o" | grep -q 'Шлюз выключен' && ok "включённый шлюз → «Выключить» с вопросом → gw disable" || bad "выключение" "$(gw_seq)"
gw_st false '{"wg": {}}' 1 1; gwf probe.json "$GW_PROBE_OK"; gwf enable.json '{"ok": true, "base": "10.77.0.0/16", "host": "203.0.113.10", "warnings": []}'
: > "$CALLS/kaskadctl.log"; o=$(printf '\n' | lib t_toggle 2>&1 | strip)
[ "$(gw_seq)" = "gw probe --json;gw enable --json;" ] && printf '%s' "$o" | grep -q 'Шлюз включён' && ok "выключенный → проверка сервера и gw enable" || bad "включение" "$(gw_seq)"
t_gwst() { k3_gw_status_show; }
gwf status.json '{"ok": true, "enabled": true, "host": "203.0.113.10", "inbounds": [], "exits": [], "dns": {"active": true}, "problems": []}'
: > "$CALLS/kaskadctl.log"; o=$(printf '0\n' | lib t_gwst 2>&1 | strip)
[ "$(gw_seq)" = "gw status --json;" ] && printf '%s' "$o" | grep -q 'Шлюз включён · адрес для устройств: 203.0.113.10' \
  && ok "«Состояние» — из gw status --json: итог (подробно — раздел 37)" || bad "состояние" "$(gw_seq) | $o"

echo "── 23b. этап 1 (T10): «Состояние и группы» сразу за «Подписками» в 17 → 3 (T13 «Переименовать» — последним), «если выходы недоступны» — как у группы, группа по имени ──"
use_root gw-groups; fake_ctl; gw_st true '{"wg": {}}' 1 1
gw_route_st() { jq --arg v "$1" --arg f "$2" '.gateway.routes.default = {"via": $v, "fail": $f}
    | .gateway.exit_policy = {"groups": [{"id": "main", "name": ""}, {"id": "g-1a2b3c", "name": "Стриминг"}]}' \
    "$KASKAD_DIR/state.json" > "$W/st.tmp" && mv "$W/st.tmp" "$KASKAD_DIR/state.json"; }
gw_route_st group:g-1a2b3c block
gwf exits.json '{"ok": true, "exits": [{"id": "e-1111111111", "index": 1, "name": "Вена", "kind": "vless", "host": "198.51.100.31", "usable": true, "enabled": true, "state": "alive", "group": "g-1a2b3c"},
 {"id": "e-2222222222", "index": 2, "name": "Рига", "kind": "awg", "version": "2.0", "host": "198.51.100.32", "usable": true, "enabled": true, "state": "alive", "group": "main"}],
 "groups": [{"id": "main", "label": "Основная", "role": "abroad", "fail": "block"}, {"id": "g-1a2b3c", "label": "Стриминг", "role": "abroad", "fail": "direct"}]}'
gwf groups.json '{"ok": true, "rev": 3, "groups": [], "no_exit": 2, "lines": ["Группа          Роль        Выходы  Сейчас                Последний шаг",
 "Основная        зарубежная  1       Уровень 1             блок", "Стриминг        зарубежная  1       последний шаг с 12:03 с сервера напрямую", "",
 "  Вена — Стриминг · Уровень 1 · не работает: нет ответа · устройств: 2", "", "Нет выхода в интернет: 2 устройства.", "",
 "Управление выходами и группами — в панели или kaskadctl gw …"]}'
t_groups() { K3_TEST_TTY=1; k3_gw_exits_menu; }
: > "$CALLS/kaskadctl.log"
o=$(printf '6\n\n0\n' | lib t_groups 2>&1 | strip)
printf '%s' "$o" | grep -q 'Маршрут по умолчанию: группа «Стриминг»; если выходы недоступны — как у группы (с сервера напрямую)\.' \
  && printf '%s' "$o" | grep -q '^ 2) Если выходы недоступны: как у группы (с сервера напрямую) → сделать «напрямую»$' \
  && [ "$(printf '%s\n' "$o" | grep -E '^ [0-9]\) ' | sort -u | tr '\n' '|')" = " 0) Назад| 1) Маршрут по умолчанию| 2) Если выходы недоступны: как у группы (с сервера напрямую) → сделать «напрямую»| 3) Добавить ключ выхода| 4) Выход: выключить или включить для шлюза| 5) Подписки выходов — ключи по ссылке, обновление раз в сутки| 6) Состояние и группы — только просмотр| 7) Переименовать выход (понятное имя)|" ] \
  && ! printf '%s' "$o" | grep -q 'g-1a2b3c' \
  && ok "17 → 3, маршрут через группу: имя группы, а не id; «если выходы недоступны — как у группы (…)»; «Состояние и группы» — шестым, сразу за «Подписками», «Переименовать выход» — последним" \
  || bad "17 → 3: маршрут через группу" "$o"
printf '%s' "$o" | grep -q '^ *Группа  *Роль  *Выходы  *Сейчас  *Последний шаг$' \
  && printf '%s' "$o" | grep -q '^ *Стриминг  *зарубежная  *1  *последний шаг с 12:03 с сервера напрямую$' \
  && printf '%s' "$o" | grep -q 'Вена — Стриминг · Уровень 1 · не работает: нет ответа · устройств: 2' \
  && printf '%s' "$o" | grep -q 'Нет выхода в интернет: 2 устройства\.' \
  && printf '%s' "$o" | grep -q 'Управление выходами и группами — в панели или kaskadctl gw …' \
  && gw_calls | grep -qx 'gw groups --json' && ! gw_calls | grep -qvE '^gw (exits|groups) --json$' \
  && ! printf '%s' "$o" | grep -qiE '\(y/n\)|\[y/n\]|в запас|Сбросить|Включить «Стандарт»' \
  && ok "«Состояние и группы»: таблица ядра (gw groups --json), выходы, «нет выхода в интернет», подсказка — только просмотр, без аварийных пунктов" \
  || bad "«Состояние и группы»" "$(gw_seq) | $o"
# выход: «сначала — другие выходы группы», «если выходы недоступны» → «напрямую» — только после «1) Да» цифрами
gw_route_st exit:e-2222222222 block
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n1\n\n0\n' | lib t_groups 2>&1 | strip)
printf '%s' "$o" | grep -q 'Маршрут по умолчанию: выход «Рига»; если выходы недоступны — как у группы (блок)\.' \
  && printf '%s' "$o" | grep -q 'Выход недоступен — сначала другие выходы группы «Основная»\.' \
  && printf '%s' "$o" | grep -q '2) Нет, оставить «как у группы»' && printf '%s' "$o" | grep -q 'Выбор \[2\]' \
  && gw_calls | grep -qx 'gw route exit:e-2222222222 --fail direct --json' \
  && printf '%s' "$o" | grep -q '\[OK\] Сохранено: при отказе — напрямую\.' \
  && ok "маршрут через выход: «сначала — другие выходы группы «Основная»», переключение на «напрямую» — цифрами (Выбор [2])" \
  || bad "маршрут через выход: подписи fail" "$(gw_seq) | $o"
gw_route_st exit:e-2222222222 direct
o=$(printf '0\n' | lib t_groups 2>&1 | strip)
printf '%s' "$o" | grep -q '^ 2) Если выходы недоступны: напрямую → сделать «как у группы (блок)»$' \
  && printf '%s' "$o" | grep -q 'если выходы недоступны — напрямую\.' \
  && ok "своё «напрямую» маршрута: «напрямую → сделать «как у группы (блок)»»" || bad "подпись fail при «напрямую»" "$o"
# выбор маршрута: «1) Как у группы (…) 2) Напрямую»; у выхода — его группа
t_pickg() { K3_TEST_TTY=1; k3_gw_pick_route ""; echo "VIA=$K3_GW_VIA FAIL=$K3_GW_FAIL"; }
o=$(printf '2\n\n' | lib t_pickg 2>&1 | strip)
printf '%s' "$o" | grep -q 'Если выходы этого маршрута недоступны:' \
  && printf '%s' "$o" | grep -q '1) Как у группы (с сервера напрямую) — по умолчанию' \
  && printf '%s' "$o" | grep -q '2) Напрямую — с адреса этого сервера, мимо выхода' \
  && printf '%s' "$o" | grep -q 'Выход недоступен — сначала другие выходы группы «Стриминг»\.' \
  && printf '%s' "$o" | grep -q 'VIA=exit:e-1111111111 FAIL=$' \
  && ok "выбор маршрута, выход «Вена»: «1) Как у группы (с сервера напрямую) 2) Напрямую», Enter — как было" || bad "выбор маршрута: подписи fail" "$o"
o=$(printf '1\n1\n' | lib t_pickg 2>&1 | strip)
printf '%s' "$o" | grep -q '1) Как у группы (блок) — по умолчанию' && printf '%s' "$o" | grep -q 'VIA=pool FAIL=block' \
  && ! printf '%s' "$o" | grep -q 'сначала другие выходы' \
  && ok "выбор маршрута, пул: «Как у группы (блок)» (группа «Основная»), 1 — block" || bad "выбор маршрута: пул" "$o"
# группы не прочитаны — строка с причиной, без таблицы
gwf groups.json '{"ok": false, "error": "ядро недоступно"}'; gwf groups.rc 1
o=$(printf '6\n\n0\n' | lib t_groups 2>&1 | strip)
printf '%s' "$o" | grep -q 'Группы не прочитаны: ядро недоступно' && ! printf '%s' "$o" | grep -q 'Последний шаг' \
  && ok "«Состояние и группы» без ответа ядра: «Группы не прочитаны: …»" || bad "группы не прочитаны" "$o"
rm -f "$FAKE_DIR/gw/groups.rc"
gw_route_st pool block
o=$(printf '6\n\n0\n' | KASKAD_LANG=en lib t_groups 2>&1 | strip)
printf '%s' "$o" | grep -q '6) State and groups — view only' && printf '%s' "$o" | grep -q 'if the exits are down — as its group (block)' \
  && ok "EN: «State and groups — view only», «as its group (block)»" || bad "EN: группы" "$o"

# T10, круг 2 (REV-s1-t10 №5): «Состояние и группы» — сразу за «Подписками»; пункт, который T13 добавит после
# «Подписок» («Переименовать», только при выходах), встаёт за ним — номер «Состояние и группы» постоянный
t_acts() { local -a acts=(route fail add toggle subs rename); k3_gw_acts_after subs groups; echo "${acts[*]}"; }
o=$(lib t_acts 2>&1)
[ "$o" = "route fail add toggle subs groups rename" ] \
  && ok "17 → 3: «Состояние и группы» — сразу за «Подписками», пункты после «Подписок» (T13) — за ним" || bad "порядок пунктов 17 → 3" "$o"
# группа и так при отказе идёт с сервера напрямую: «как у группы (с сервера напрямую) → сделать «напрямую»» — без
# тревожной строки и вопроса (путь не меняется; придирка 4)
gw_route_st group:g-1a2b3c block
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n\n0\n' | lib t_groups 2>&1 | strip)
gw_calls | grep -qx 'gw route group:g-1a2b3c --fail direct --json' && ! printf '%s' "$o" | grep -q 'с адреса этого сервера' \
  && ! printf '%s' "$o" | grep -q 'Переключить «если выходы недоступны»' && printf '%s' "$o" | grep -q '\[OK\] Сохранено: при отказе — напрямую\.' \
  && ok "группа и так «с сервера напрямую»: «→ напрямую» — без тревоги и вопроса" || bad "fail без тревоги" "$(gw_seq) | $o"
gw_route_st exit:e-2222222222 block
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n2\n\n0\n' | lib t_groups 2>&1 | strip)
printf '%s' "$o" | grep -q 'Переключить «если выходы недоступны» на «напрямую»?' && ! gw_calls | grep -q -- '--fail direct' \
  && ok "группа «Основная» — блок: «→ напрямую» по-прежнему с вопросом, «2» — ничего" || bad "fail с вопросом" "$(gw_seq) | $o"
# «Состояние» (17 → 5): шаг группы маршрута — из gw status (default_route, groups), как в 17 → 3 (REV-s1-t10 №3)
gwf status.json '{"ok": true, "enabled": true, "host": "203.0.113.10", "inbounds": [], "dns": {"active": true}, "problems": [],
 "exits": [{"id": "e-1111111111", "name": "Вена", "kind": "vless", "host": "198.51.100.31", "usable": true, "enabled": true, "state": "alive"}],
 "default_route": {"via": "exit:e-1111111111", "fail": "block", "fail_eff": "direct", "group": "g-1a2b3c", "group_name": "Стриминг", "group_fail": "direct"},
 "groups": [{"id": "main", "label": "Основная", "role": "abroad", "fail": "block"}, {"id": "g-1a2b3c", "label": "Стриминг", "role": "abroad", "fail": "direct"}]}'
t_stx() { k3_gw_status_exits "$(cat "$FAKE_DIR/gw/status.json")"; }
gw_route_st exit:e-1111111111 block
o=$(lib t_stx 2>&1 | strip)
printf '%s' "$o" | flat | grep -q 'Маршрут по умолчанию: выход «Вена»; если выходы недоступны — как у группы (с сервера напрямую)\.' \
  && ! printf '%s' "$o" | grep -q '— блок\.' \
  && ok "«Состояние»: маршрут через выход группы «напрямую при отказе» — «как у группы (с сервера напрямую)», не «блок»" || bad "«Состояние»: шаг группы" "$o"
gw_route_st group:g-1a2b3c block
o=$(lib t_stx 2>&1 | strip)
printf '%s' "$o" | flat | grep -q 'Маршрут по умолчанию: группа «Стриминг»; если выходы недоступны — как у группы (с сервера напрямую)\.' \
  && ok "«Состояние»: маршрут через группу — по имени и её шагу" || bad "«Состояние»: группа" "$o"
gwf status.json '{"ok": true, "enabled": true, "host": "203.0.113.10", "inbounds": [], "exits": [], "dns": {"active": true}, "problems": []}'
gw_route_st pool block
o=$(lib t_stx 2>&1 | strip)
printf '%s' "$o" | flat | grep -q 'Маршрут по умолчанию: пул выходов; если выходы недоступны — как у группы (блок)\.' \
  && ok "«Состояние» старого ядра (без default_route): «как у группы (блок)» — из state.json" || bad "«Состояние»: старое ядро" "$o"
# «Пользователи и устройства» (17 → 2): группа — по имени (без «\» и управляющих), «при отказе — напрямую» — по fail_eff ядра
t_people2() { K3_TEST_TTY=1; k3_gw_people; }
gwf user_list.json '{"ok": true, "users": [{"id": "u-00000001", "name": "Мама", "route": {"via": "group:g-1a2b3c", "fail": "block", "fail_eff": "direct", "group_name": "Стр\\e[31mим\u001b"}, "enabled": true, "devices": 0},
 {"id": "u-00000002", "name": "Папа", "route": null, "route_effective": {"via": "pool", "fail": "block", "fail_eff": "direct"}, "enabled": true, "devices": 0},
 {"id": "u-00000003", "name": "Дача", "route": {"via": "pool", "fail": "block", "fail_eff": "block"}, "enabled": true, "devices": 0}]}'
gwf device_list.json '{"ok": true, "devices": []}'
gwf inbound_list.json "$(gw_list "wg")"
raw=$(printf '0\n' | lib t_people2 2>&1); o=$(printf '%s' "$raw" | strip)
printf '%s' "$o" | grep -q '👤 Мама · маршрут: группа «Стрe\[31mим», при отказе — напрямую$' && ! printf '%s' "$raw" | grep -q $'\x1b\\[31m' \
  && printf '%s' "$o" | grep -q '👤 Папа · маршрут: как по умолчанию — сейчас: пул выходов, при отказе — напрямую$' \
  && printf '%s' "$o" | grep -q '👤 Дача · маршрут: пул выходов$' && ! printf '%s' "$o" | grep -q 'g-1a2b3c' \
  && ok "17 → 2: группа по имени (без «\\» и управляющих), «при отказе — напрямую» — когда так делает группа" || bad "17 → 2: группы" "$o"
# REV-s1-t10-2 З-3: выбор маршрута человека — «Как по умолчанию — сейчас: …» (k3_gw_route_words): маршрут «пул, блок»,
# а последний шаг «Основной» — «напрямую» → «при отказе — напрямую»; у «Основной» «блок» — без этих слов
t_inh() { k3_gw_inherit_label user u-00000002; }
gw_main_fail() { jq --arg f "$1" '.gateway.routes.default = {"via": "pool", "fail": "block"}
    | .gateway.exit_policy = {"groups": [{"id": "main", "name": "", "fail": $f}]}' \
    "$KASKAD_DIR/state.json" > "$W/st.tmp" && mv "$W/st.tmp" "$KASKAD_DIR/state.json"; }
gw_main_fail direct; o=$(lib t_inh 2>&1 | strip)
gw_main_fail block; o2=$(lib t_inh 2>&1 | strip)
[ "$o" = 'Как по умолчанию — сейчас: пул выходов, при отказе — напрямую' ] && [ "$o2" = 'Как по умолчанию — сейчас: пул выходов' ] \
  && ok "выбор маршрута: «как по умолчанию — сейчас: пул выходов, при отказе — напрямую» — по шагу «Основной»" \
  || bad "выбор маршрута: шаг группы" "$o | $o2"

echo "── 23c. этап 1 (T10): часовой пояс — «11 → Часовой пояс» и шаг мастера первой установки ──"
ZI="$W/zoneinfo"; mkdir -p "$ZI/Europe" "$ZI/Asia"; for z in Europe/Moscow Europe/Berlin UTC; do printf 'TZif2' > "$ZI/$z"; done
printf '# leap seconds\n' > "$ZI/leapseconds"; printf '2026a\n' > "$ZI/+VERSION"      # файлы zoneinfo, но не пояса
t_tz11() { K3_TEST_TTY=1; K3_ZONEINFO="$ZI"; clear() { :; }; sleep() { :; }; init_config >/dev/null 2>&1; k3_update_menu; echo "TZ=$(k3_tz_conf)"; }
use_root tz1; rm -f "$K3_OPT/bin/kaskadctl"
o=$(printf '3\n3\nMars/Olympus\nEurope/Berlin\n' | lib t_tz11 2>&1 | strip)
printf '%s' "$o" | grep -q '3) Часовой пояс: Europe/Moscow' && printf '%s' "$o" | grep -q '1) Москва' && printf '%s' "$o" | grep -q '2) UTC' \
  && printf '%s' "$o" | grep -q '3) Ввести имя (Europe/Berlin)' && printf '%s' "$o" | grep -q '0) Назад' \
  && printf '%s' "$o" | grep -q 'Нет такого пояса: Mars/Olympus' && printf '%s' "$o" | grep -q '\[OK\] Часовой пояс: Europe/Berlin\.' \
  && printf '%s' "$o" | grep -qx 'TZ=Europe/Berlin' && grep -qx 'KASKAD_TZ="Europe/Berlin"' "$KASKAD_DIR/config" \
  && ok "11 → 3: «1) Москва 2) UTC 3) Ввести имя 0) Назад»; неверное имя — снова; без ядра — KASKAD_TZ в настройки" || bad "11 → пояс" "$o"
use_root tz2; fake_ctl
: > "$CALLS/kaskadctl.log"; o=$(printf '3\n2\n' | lib t_tz11 2>&1 | strip)
grep -qx 'tz set UTC --json' "$CALLS/kaskadctl.log" && printf '%s' "$o" | grep -q '\[OK\] Часовой пояс: UTC\.' \
  && ok "11 → 3 → 2: с ядром — kaskadctl tz set UTC (та же запись, что у панели)" || bad "пояс через ядро" "$o | $(ctl_log)"
: > "$CALLS/kaskadctl.log"; o=$(printf '3\n0\n' | lib t_tz11 2>&1 | strip)
[ ! -s "$CALLS/kaskadctl.log" ] && ok "11 → 3 → 0: назад, ничего не записано" || bad "пояс: назад" "$(ctl_log)"
t_tzfirst() { K3_ZONEINFO="$ZI"; k3_tz_first; echo "SAVE=$K3_TZ_SAVE"; init_config >/dev/null 2>&1; k3_tz_remember; echo "TZ=$(k3_tz_conf)"; }
use_root tz3
o=$(printf '\n' | K3_TEST_TTY=1 lib t_tzfirst 2>&1 | strip)
printf '%s' "$o" | grep -q 'Часовой пояс Kaskad' && printf '%s' "$o" | grep -q '1) Москва (по умолчанию)' && printf '%s' "$o" | grep -q '2) Другой…' \
  && printf '%s' "$o" | grep -q '3) UTC' && printf '%s' "$o" | grep -qF 'Выбор [1]' && printf '%s' "$o" | grep -qx 'TZ=Europe/Moscow' \
  && ok "мастер: «1) Москва (по умолчанию) 2) Другой… 3) UTC», Enter — Москва в настройки" || bad "мастер: пояс" "$o"
use_root tz4
o=$(printf '2\nAsia/Nowhere\nleapseconds\n+VERSION\nEurope/Berlin\n' | K3_TEST_TTY=1 lib t_tzfirst 2>&1 | strip)
printf '%s' "$o" | grep -q 'Нет такого пояса: Asia/Nowhere' && printf '%s' "$o" | grep -q 'Нет такого пояса: leapseconds' \
  && printf '%s' "$o" | grep -q 'Нет такого пояса: +VERSION' && printf '%s' "$o" | grep -qx 'TZ=Europe/Berlin' \
  && ok "мастер: «2) Другой…» — имя с проверкой (файл TZif: «leapseconds», «+VERSION» — не пояса)" || bad "мастер: другой пояс" "$o"
use_root tz5; printf 'BOT_TOKEN=""\n' > "$KASKAD_DIR/config"
o=$(printf '3\n' | K3_TEST_TTY=1 lib t_tzfirst 2>&1 | strip)
! printf '%s' "$o" | grep -q 'Часовой пояс' && printf '%s' "$o" | grep -qx 'SAVE=' && ! grep -q KASKAD_TZ "$KASKAD_DIR/config" \
  && ok "обновление (настройки есть): пояс не спрашивается и не пишется" || bad "мастер: обновление" "$o"
use_root tz6
o=$(KASKAD_LANG=en lib eval 'K3_LANG=en; t_tzfirst' < /dev/null 2>&1 | strip)
printf '%s' "$o" | grep -qx 'SAVE=' && ! printf '%s' "$o" | grep -q 'zone' && ok "без терминала: без вопроса (ядро — Москва по умолчанию)" || bad "мастер без терминала" "$o"
use_root tz7
o=$(printf '3\n' | K3_TEST_TTY=1 KASKAD_LANG=en lib eval 'K3_LANG=en; t_tzfirst' 2>&1 | strip)
printf '%s' "$o" | grep -q 'Kaskad time zone' && printf '%s' "$o" | grep -q '1) Moscow (default)' && printf '%s' "$o" | grep -q '2) Other…' \
  && printf '%s' "$o" | grep -qx 'TZ=UTC' && [ -z "$(printf '%s' "$o" | grep '[А-Яа-яЁё]')" ] \
  && ok "EN: «Kaskad time zone … 1) Moscow (default) 2) Other… 3) UTC», без кириллицы" || bad "EN: пояс" "$o"

echo "── 24. «Удалить шлюз» ──"
t_rm() { K3_TEST_TTY=1; k3_gw_remove_interactive; echo "RM=$?"; }
use_root gw-rm; fake_ctl; gw_st true '{"wg": {}}' 2 1
o=$(printf '1\n\n\n' | lib t_rm 2>&1 | strip)
[ "$(gw_seq)" = "gw purge --yes --json;" ] && printf '%s' "$o" | grep -q 'RM=0' && printf '%s' "$o" | grep -q 'Пользователи и устройства сохранены' \
  && ok "«1» и Enter на втором вопросе → gw purge --yes: протоколы удалены, пользователи сохранены" || bad "удаление шлюза" "$(gw_seq) | $o"
: > "$CALLS/kaskadctl.log"; o=$(printf '1\nУДАЛИТЬ\n\n' | lib t_rm 2>&1 | strip)
[ "$(gw_seq)" = "gw purge --users --yes --json;" ] && ok "пользователи и устройства — только по слову УДАЛИТЬ → --users" || bad "удаление с пользователями" "$(gw_seq)"
: > "$CALLS/kaskadctl.log"; o=$(printf '1\n1\n\n' | lib t_rm 2>&1 | strip)
[ "$(gw_seq)" = "gw purge --yes --json;" ] && ok "«1» вместо слова — пользователи остаются" || bad "1 вместо слова" "$(gw_seq)"
: > "$CALLS/kaskadctl.log"; o=$(printf '2\n\n' | lib t_rm 2>&1 | strip)
[ -z "$(gw_calls)" ] && printf '%s' "$o" | grep -q 'RM=1' && ok "«2» — ничего не удалено" || bad "отказ от удаления" "$(gw_seq)"
gwf purge.json '{"ok": false, "error": "занято", "code": 6, "data": {}}'; gwf purge.rc 6
o=$(printf '1\n\n\n' | lib t_rm 2>&1 | strip)
printf '%s' "$o" | grep -q 'Шлюз не удалён: занято' && printf '%s' "$o" | grep -q 'RM=1' && ok "ядро отказало → причина, код 1" || bad "сбой удаления" "$o"

echo "── 25. полное удаление (15) со шлюзом ──"
use_root gw-uninst; fake_ctl; gw_st true '{"wg": {}}' 1 1
printf '[Service]\n' > "$KASKAD_ROOT/etc/systemd/system/kaskad-gw-wg.service"
o=$(lib t_uninst 2>&1 | strip)
p=$(ctl_log | grep -nx 'gw purge --users --yes' | cut -d: -f1); t=$(ctl_log | grep -nx 'net teardown' | cut -d: -f1)
[ -n "$p" ] && [ -n "$t" ] && [ "$p" -lt "$t" ] && printf '%s' "$o" | grep -q 'Шлюз удалён' && [ ! -e "$KASKAD_DIR" ] \
  && ok "15: gw purge --users --yes — до снятия движка (службы и входы шлюза, маски пакетов), потом /etc/kaskad" || bad "полное удаление со шлюзом" "$(ctl_log)"
use_root gw-uninst2; fake_ctl; gw_st true '{"wg": {}}' 1 1; gwf purge.rc 1
U="$KASKAD_ROOT/etc/systemd/system"
for u in kaskad-gw-wg kaskad-gw-ipsec kaskad-gw-collect kaskad-dns; do printf '[Service]\n' > "$U/$u.service"; done
mkdir -p "$KASKAD_DIR/gw" "$KASKAD_ROOT/etc/wireguard" "$KASKAD_ROOT/etc/swanctl/conf.d"
printf '{"openvpn": {"by_kaskad": true, "masked": ["openvpn.service"]}}\n' > "$KASKAD_DIR/gw/packages.json"
printf '[Interface]\n' > "$KASKAD_ROOT/etc/wireguard/kgw-wg.conf"; printf '[Interface]\n' > "$KASKAD_ROOT/etc/wireguard/wg0.conf"
printf 'conn\n' > "$KASKAD_ROOT/etc/swanctl/conf.d/kaskad.conf"
mkdir -p "$KASKAD_ROOT/etc/openvpn/kaskad/ccd-udp" "$KASKAD_ROOT/etc/openvpn/server"
mkdir -p "$KASKAD_DIR/gw/wg" "$KASKAD_DIR/gw/pptp"; touch "$FAKE_DIR/ufw_active"
printf '[[51820, "udp"]]\n' > "$KASKAD_DIR/gw/wg/ufw.json"; printf '[[1723, "tcp"], [null, "gre"]]\n' > "$KASKAD_DIR/gw/pptp/ufw.json"
lib t_uninst >/dev/null 2>&1
rm -f "$FAKE_DIR/ufw_active"
[ -z "$(ls "$U" | grep -E '^kaskad-(gw-|dns)')" ] && grep -qx 'disable kaskad-gw-ipsec.service' "$CALLS/systemctl.log" \
  && grep -qx 'unmask openvpn.service' "$CALLS/systemctl.log" && [ ! -e "$KASKAD_ROOT/etc/wireguard/kgw-wg.conf" ] \
  && [ -e "$KASKAD_ROOT/etc/wireguard/wg0.conf" ] && [ ! -e "$KASKAD_ROOT/etc/swanctl/conf.d/kaskad.conf" ] \
  && [ ! -e "$KASKAD_ROOT/etc/openvpn/kaskad" ] && [ -d "$KASKAD_ROOT/etc/openvpn/server" ] \
  && ok "ядро не удалило шлюз → по файлам: юниты kaskad-gw-*/kaskad-dns, маски пакетов (до /etc/kaskad), kgw-*.conf, swanctl, /etc/openvpn/kaskad" || bad "запасное удаление шлюза" "$(ls "$U") | $(cat "$CALLS/systemctl.log")"
grep -qx 'delete allow 51820/udp' "$CALLS/ufw.log" && grep -qx 'delete allow 1723/tcp' "$CALLS/ufw.log" \
  && grep -qx 'delete allow proto gre from any to any' "$CALLS/ufw.log" \
  && ok "…и правила UFW входов по их ufw.json (порты и GRE)" || bad "UFW при запасном удалении" "$(cat "$CALLS/ufw.log")"

echo "── 26. «Вернуться на 2.13» и «Вернуть iptables» со включённым шлюзом ──"
gw_ret_setup() { ret_setup "$1"; gw_st true '{"wg": {}}' 1 1; mkdir -p "$K3_OPT/prev"; mk_version 2.13 "$K3_OPT/prev/gokaskad-2.13"
                 gw_st false '{}' 1 1 "$FAKE_DIR/gw/purge.state"; }
gw_ret_setup gw1
o=$(printf '2\n' | lib t_ret 2>&1 | strip)
printf '%s' "$o" | grep -q 'шлюз будет удалён' && [ -z "$(gw_calls)" ] && ! ctl_log | grep -q rollback && cmp -s "$S" "$C" \
  && ok "в «Что будет» — шлюз будет удалён; «2» — ничего не тронуто" || bad "возврат: отказ" "$o"
ln_of() { ctl_log | grep -nx -- "$1" | head -1 | cut -d: -f1; }
gw_ret_setup gw2; gw_st false '{"wg": {}}' 1 1 "$FAKE_DIR/gw/disable.state"
o=$(printf '1\n\n\n' | lib t_ret 2>&1 | strip)
d=$(ln_of 'gw disable --json'); r=$(ln_of 'migrate rollback --to-legacy --yes'); p=$(ln_of 'gw purge --yes --json')
[ -n "$d" ] && [ -n "$r" ] && [ -n "$p" ] && [ "$d" -lt "$r" ] && [ "$r" -lt "$p" ] && [ "$(ver_of "$C")" = "2.13" ] \
  && ok "«1» → шлюз выключен, пробросы в iptables, затем шлюз удалён (пользователи — по выбору, Enter — оставить), команда — 2.13" || bad "возврат со шлюзом" "$(ctl_log)"
gw_ret_setup gw2b; gw_st false '{"wg": {}}' 1 1 "$FAKE_DIR/gw/disable.state"; echo 1 > "$FAKE_DIR/rollback_rc"; touch "$FAKE_DIR/rollback_keeps_nft"
o=$(printf '1\n\n' | lib t_ret 2>&1 | strip)
gw_calls | grep -qx 'gw enable --json' && ! gw_calls | grep -q purge && cmp -s "$S" "$C" && printf '%s' "$o" | grep -q 'Шлюз снова включён' \
  && ok "пробросы вернуть не вышло → шлюз включён обратно, не удалён, программа прежняя" || bad "возврат: сбой отката" "$(ctl_log) | $o"
gw_ret_setup gw3; gw_st false '{"wg": {}}' 1 1 "$FAKE_DIR/gw/disable.state"; gwf purge.rc 1; gwf purge.json '{"ok": false, "error": "занято", "code": 6, "data": {}}'
o=$(printf '1\n\n\n' | lib t_ret 2>&1 | strip)
printf '%s' "$o" | grep -q "Шлюз не удалён — программа осталась Kaskad $CUR, пробросы уже работают через iptables" && cmp -s "$S" "$C" \
  && ok "шлюз не удалился → программа осталась 3.x (пробросы уже в iptables): повторить возврат или удалить шлюз в 17" || bad "возврат: сбой удаления" "$o"
gw_ret_setup gw4; gw_st false '{"wg": {}}' 0 0
o=$(printf '1\n\n' | lib t_ret 2>&1 | strip)
r=$(ln_of 'migrate rollback --to-legacy --yes'); p=$(ln_of 'gw purge --yes --json')
[ -n "$p" ] && [ -n "$r" ] && [ "$r" -lt "$p" ] && ! gw_calls | grep -q disable && printf '%s' "$o" | grep -q 'шлюз будет удалён' && [ "$(ver_of "$C")" = "2.13" ] \
  && ok "выключенный, но установленный шлюз тоже удаляется: его файлы 2.13 не увидит и не удалит" || bad "возврат с выключенным шлюзом" "$(ctl_log)"
gw_ret_setup gw4b; gw_st false '{}' 0 0; printf '[Service]\n' > "$U/kaskad-dns.service"
o=$(printf '1\n\n' | lib t_ret 2>&1 | strip)
gw_calls | grep -qx 'gw purge --yes --json' && ok "входов нет, а службы шлюза на диске остались → тоже удаляются" || bad "возврат: остатки на диске" "$(ctl_log)"
gw_ret_setup gw5; gw_st false '{}' 2 1
o=$(printf '1\n\n' | lib t_ret 2>&1 | strip)
[ -z "$(gw_calls)" ] && ! printf '%s' "$o" | grep -q 'шлюз будет удалён' && [ "$(ver_of "$C")" = "2.13" ] \
  && ok "от шлюза остались только пользователи (протоколов нет) — возврат без удаления" || bad "возврат: только пользователи" "$(ctl_log)"
use_root gw-rb1; fake_ctl; gw_st true '{"wg": {}}' 1 1
o=$(printf '1\n' | lib t_rb 2>&1 | strip)
d=$(ln_of 'gw disable --json'); r=$(ln_of 'migrate rollback --to-legacy --yes')
[ -n "$d" ] && [ -n "$r" ] && [ "$d" -lt "$r" ] && ! gw_calls | grep -q purge && printf '%s' "$o" | grep -q 'работает он только с nftables' \
  && ok "16 → 2, шлюз включён: «1» — шлюз выключен (пользователи и протоколы сохранены), потом iptables" || bad "вернуть iptables со шлюзом" "$(ctl_log)"
use_root gw-rb2; fake_ctl; gw_st true '{"wg": {}}' 1 1; gw_st false '{}' 1 1 "$FAKE_DIR/gw/purge.state"
o=$(printf '2\n\n' | lib t_rb 2>&1 | strip)
d=$(ln_of 'gw disable --json'); r=$(ln_of 'migrate rollback --to-legacy --yes'); p=$(ln_of 'gw purge --yes --json')
[ -n "$d" ] && [ -n "$r" ] && [ -n "$p" ] && [ "$d" -lt "$r" ] && [ "$r" -lt "$p" ] \
  && ok "«2» — выключить, вернуть iptables, потом удалить шлюз" || bad "удалить шлюз и вернуть iptables" "$(ctl_log)"
use_root gw-rb2b; fake_ctl; gw_st true '{"wg": {}}' 1 1; echo 1 > "$FAKE_DIR/rollback_rc"; touch "$FAKE_DIR/rollback_keeps_nft"
o=$(printf '2\n' | lib t_rb 2>&1 | strip)
gw_calls | grep -qx 'gw enable --json' && ! gw_calls | grep -q purge && printf '%s' "$o" | grep -q 'RC=1' \
  && ok "iptables вернуть не вышло → шлюз включён обратно и не удалён" || bad "16 → 2: сбой отката" "$(ctl_log)"
use_root gw-rb3; fake_ctl; gw_st true '{"wg": {}}' 1 1
o=$(printf '0\n' | lib t_rb 2>&1 | strip)
[ -z "$(gw_calls)" ] && ! ctl_log | grep -q rollback && ok "«0» — отмена, ничего не тронуто" || bad "отмена 16 → 2" "$(ctl_log)"
use_root gw-rb4; fake_ctl; gw_st true '{"wg": {}}' 1 1; gwf disable.rc 1; gwf disable.json '{"ok": false, "error": "nft занят", "code": 1, "data": {}}'
o=$(printf '1\n' | lib t_rb 2>&1 | strip)
printf '%s' "$o" | grep -q 'Шлюз не выключился (nft занят) — iptables не возвращаю' && ! ctl_log | grep -q rollback && printf '%s' "$o" | grep -q 'RC=1' \
  && ok "шлюз не выключился → iptables не возвращаются" || bad "сбой выключения" "$o"
t_mig() { init_config >/dev/null 2>&1; k3_migrate_interactive; echo "RC=$?"; }
use_root gw-rb6; fake_ctl; prog_ready; gw_st true '{"wg": {}}' 1 1; mkdir -p "$KASKAD_DIR/gw"
o=$(printf '1\n' | lib t_rb 2>&1 | strip)
[ -f "$KASKAD_DIR/gw/off-for-iptables" ] && ok "16 → 2 → «1»: шлюз выключен ради iptables — отметка, чтобы потом предложить включить" || bad "отметка выключения" "$o"
gw_st false '{"wg": {}}' 1 1 "$FAKE_DIR/apply.state"; : > "$CALLS/kaskadctl.log"
o=$(printf '1\n\n' | lib t_mig 2>&1 | strip)
gw_calls | grep -qx 'gw enable --json' && printf '%s' "$o" | grep -q 'Шлюз снова включён' && [ ! -f "$KASKAD_DIR/gw/off-for-iptables" ] \
  && ok "16 → 1 после такого возврата: «Включить его снова?» (1 по умолчанию), Enter — шлюз включён, отметка снята" || bad "включить шлюз после 16 → 1" "$o | $(ctl_log)"
use_root gw-rb7; fake_ctl; prog_ready; gw_st false '{"wg": {}}' 1 1; mkdir -p "$KASKAD_DIR/gw"
printf '{\n  "engine": "legacy"\n}\n' > "$KASKAD_DIR/state.json"; : > "$KASKAD_DIR/gw/off-for-iptables"
gw_st false '{"wg": {}}' 1 1 "$FAKE_DIR/apply.state"
o=$(printf '1\n2\n' | lib t_mig 2>&1 | strip)
! gw_calls | grep -q 'gw enable' && printf '%s' "$o" | grep -q 'Включить потом: меню 17 → 6' && [ ! -f "$KASKAD_DIR/gw/off-for-iptables" ] \
  && ok "«2» — шлюз не трогаю, подсказка «меню 17 → 6», больше не спрашиваю" || bad "отказ включать шлюз" "$o | $(ctl_log)"

echo "── 27. первая установка: мастер шлюза предлагается один раз ──"
t_fresh() { k3_fresh_check; echo "FRESH=$K3_FRESH"; }
use_root gw-fresh1
[ "$(lib t_fresh)" = "FRESH=1" ] && ok "ни настроек, ни команды, ни state.json → первая установка" || bad "первая установка" "$(lib t_fresh)"
printf 'BOT_TOKEN=""\n' > "$KASKAD_DIR/config"
[ "$(lib t_fresh)" = "FRESH=0" ] && ok "есть /etc/kaskad/config (2.x, бесплатный с настройками) → обновление" || bad "обновление по config" "$(lib t_fresh)"
rm -f "$KASKAD_DIR/config"; cp "$S" "$KASKAD_ROOT/usr/local/bin/gokaskad"
[ "$(lib t_fresh)" = "FRESH=0" ] && ok "есть команда gokaskad (бесплатный v1.1, прежняя установка) → обновление" || bad "обновление по команде" "$(lib t_fresh)"
t_offer() { K3_TEST_TTY=1; K3_FRESH=1; K3_SILENT_SWITCH="${1-1}"; k3_gw_menu() { echo "GW-MENU"; }; k3_gw_offer_fresh; echo "DONE"; }
use_root gw-fresh2; fake_ctl; engine_nft
o=$(printf '\n' | lib t_offer 2>&1 | strip)
# 3.3.2 (владелец): экран установщика — вопрос и цифры, без пояснений
printf '%s' "$o" | grep -q 'телефоны и роутеры ━━━' && ! printf '%s' "$o" | grep -q 'подключатся к этому серверу и выйдут' \
  && ! printf '%s' "$o" | grep -qiE 'переводчик|пул' && printf '%s' "$o" | grep -q 'GW-MENU' \
  && ok "первая установка → «Хотите подключить к этому серверу телефоны и роутеры?» без лишних пояснений; Enter — да, мастер" || bad "предложение мастера" "$o"
printf '%s' "$o" | grep -q '^Хотите подключить к этому серверу телефоны и роутеры?' && printf '%s' "$o" | grep -q '1) Да, настроить шлюз' \
  && printf '%s' "$o" | grep -q '2) Нет, потом' && printf '%s' "$o" | grep -q 'Выбор \[1\]:' \
  && ok "вопрос — словами задачи, пункты цифрами, Enter — 1" || bad "текст вопроса" "$o"
o=$(printf '1\n' | lib t_offer 2>&1 | strip)
printf '%s' "$o" | grep -q 'GW-MENU' && ok "«1» — мастер" || bad "1 — мастер" "$o"
o=$(printf 'abc\n2\n' | K3_PAUSE_SEC=0 lib t_offer 2>&1 | strip)
! printf '%s' "$o" | grep -q 'GW-MENU' && printf '%s' "$o" | grep -q 'Нет такого пункта: введите номер от 1 до 2 (Enter — 1)' \
  && printf '%s' "$o" | grep -q 'Подключить можно потом: пункт 17 главного меню' && printf '%s' "$o" | grep -q DONE \
  && ok "непонятный ответ — «нет такого пункта» и вопрос снова; «2» — где найти потом (пункт 17), и главное меню" || bad "отказ от мастера" "$o"
o=$(printf '1\n' | lib t_offer "" 2>&1 | strip)
! printf '%s' "$o" | grep -q 'GW-MENU' && ! printf '%s' "$o" | grep -q 'пункт 17' && ok "при запуске был переезд (не молча) → не предлагается" || bad "предложение после переезда" "$o"
# 3.2.21 (T-N5 3.2.20, Н-6): отметка на диске — оборванный вопрос возвращается при следующем запуске
OM="$KASKAD_DIR/gw-offer.pending"
o=$(lib t_offer </dev/null 2>&1 | strip)
[ -e "$OM" ] && ! printf '%s' "$o" | grep -q 'GW-MENU\|пункт 17' \
  && ok "первая установка, ответа нет (SSH оборвался на вопросе) → отметка $OM остаётся" || bad "отметка при обрыве" "$o"
t_offer2() { K3_TEST_TTY=1; k3_fresh_check; K3_SILENT_SWITCH=""; k3_gw_menu() { echo "GW-MENU"; }; k3_gw_offer_fresh; echo "DONE"; }
printf 'BOT_TOKEN=""\n' > "$KASKAD_DIR/config"
o=$(printf '2\n' | lib t_offer2 2>&1 | strip)
printf '%s' "$o" | grep -q '^Хотите подключить к этому серверу телефоны и роутеры?' && printf '%s' "$o" | grep -q 'Подключить можно потом: пункт 17' \
  && [ ! -e "$OM" ] && ok "повторный запуск (уже не первая установка) с отметкой — вопрос снова; ответ 2 — отметка снята" || bad "повтор вопроса" "$o"
o=$(printf '1\n' | lib t_offer2 2>&1 | strip)
! printf '%s' "$o" | grep -q 'телефоны и роутеры\|GW-MENU' && ok "отметки нет (ответили; или обновление) — не спрашивает" || bad "вопрос без отметки" "$o"
: > "$OM"; o=$(printf '1\n' | lib t_offer2 2>&1 | strip)
printf '%s' "$o" | grep -q 'GW-MENU' && [ ! -e "$OM" ] && ok "с отметкой, ответ 1 — мастер, отметка снята" || bad "ответ 1 с отметкой" "$o"
rm -f "$KASKAD_DIR/config"
gw_st true '{"wg": {}}' 0 0
: > "$OM"; o=$(printf '1\n' | lib t_offer 2>&1 | strip)
! printf '%s' "$o" | grep -q 'GW-MENU' && [ ! -e "$OM" ] && ok "шлюз уже настроен → не предлагается, отметка снята" || bad "предложение при настроенном" "$o"
body=$(sed -n '/^run_startup() {/,/^}/p' "$S")
f=$(printf '%s\n' "$body" | grep -n 'k3_fresh_check' | head -1 | cut -d: -f1); i=$(printf '%s\n' "$body" | grep -n '^    init_config' | head -1 | cut -d: -f1)
pr=$(printf '%s\n' "$body" | grep -n '^    show_promo' | cut -d: -f1); of=$(printf '%s\n' "$body" | grep -n 'k3_gw_offer_fresh' | cut -d: -f1); m=$(printf '%s\n' "$body" | grep -n '^    show_menu' | cut -d: -f1)
[ -n "$f" ] && [ -n "$i" ] && [ "$f" -lt "$i" ] && [ -n "$of" ] && [ "$pr" -lt "$of" ] && [ "$of" -lt "$m" ] \
  && ok "run_startup: первая ли установка — до init_config; предложение — после промо, до меню" || bad "run_startup" "f=$f i=$i pr=$pr of=$of m=$m"
# 3.2.21 (критик, М-1): отметка «предложить шлюз» — сразу после init_config первой установки (обрыв до nftables)
mk=$(printf '%s\n' "$body" | grep -n 'K3_FRESH" = 1 \] && k3_gw_offer_mark' | head -1 | cut -d: -f1)
[ -n "$mk" ] && [ "$mk" -gt "$i" ] && [ "$mk" -lt "$pr" ] \
  && ok "run_startup: первая установка — отметка «предложить шлюз» сразу после init_config, до апта и переезда" || bad "отметка до переезда" "mk=$mk i=$i pr=$pr"
t_silent() {
    migrate_dnat_comments() { :; }; adopt_free_kaskad_rules() { :; }; k3_legacy_deps() { :; }
    init_config >/dev/null 2>&1; k3_startup_engine >/dev/null 2>&1; echo "SILENT=${K3_SILENT_SWITCH:-}"
}
use_root gw-fresh3; fake_ctl; prog_ready; scan true "" 0 true
[ "$(lib t_silent </dev/null)" = "SILENT=1" ] && ok "молчаливый переезд (переносить нечего) отмечается для предложения" || bad "отметка молчаливого переезда" "$(lib t_silent </dev/null)"
use_root gw-fresh4; fake_ctl; prog_ready; scan true "" 1 true
[ "$(printf '2\n' | lib t_silent)" = "SILENT=" ] && ok "было что переносить → не отмечается" || bad "отметка при переезде с правилами" "$(printf '2\n' | lib t_silent)"
# отметка «предложить шлюз» на диске — сразу при молчаливом переезде первой установки (до промо: SSH может оборваться
# и там), при обновлении — нет
t_silent2() { K3_FRESH="$1"; t_silent; }    # первая ли установка, run_startup решает раньше (k3_fresh_check, раздел выше)
use_root gw-fresh5; fake_ctl; prog_ready; scan true "" 0 true
[ "$(lib t_silent2 1 </dev/null)" = "SILENT=1" ] && [ -e "$KASKAD_DIR/gw-offer.pending" ] \
  && ok "первая установка, молчаливый переезд → отметка gw-offer.pending на диске" || bad "отметка первой установки" "$(ls -A "$KASKAD_DIR")"
# переносить было что (предпросмотр и вопрос) — отметку первой установки снимает k3_startup_engine
t_silent3() { mkdir -p "$KASKAD_DIR"; : > "$KASKAD_DIR/gw-offer.pending"; t_silent; }
use_root gw-fresh7; fake_ctl; prog_ready; scan true "" 1 true
[ "$(printf '2\n' | lib t_silent3)" = "SILENT=" ] && [ ! -e "$KASKAD_DIR/gw-offer.pending" ] \
  && ok "было что переносить → отметка «предложить шлюз» снята" || bad "отметка при переезде" "$(ls -A "$KASKAD_DIR")"
use_root gw-fresh6; fake_ctl; prog_ready; scan true "" 0 true
[ "$(lib t_silent2 0 </dev/null)" = "SILENT=1" ] && [ ! -e "$KASKAD_DIR/gw-offer.pending" ] \
  && ok "обновление, молчаливый переезд → отметки нет" || bad "отметка при обновлении" "$(ls -A "$KASKAD_DIR")"

echo "── 28. код меню шлюза: только через k3ctl, без секретов в журнале ──"
gwsec=$(sed -n '/^#  KASKAD 3 — шлюз (меню 17)/,/^# >>> K3_PAYLOAD/p' "$S")
[ -n "$gwsec" ] && ! printf '%s\n' "$gwsec" | grep -v '^[[:space:]]*#' | grep -qE '>[[:space:]]*"?[^ ]*(state|secrets)\.json|secrets\.json' \
  && ok "меню шлюза state.json только читает, secrets.json не касается" || bad "меню правит state.json"
la=$(printf '%s\n' "$gwsec" | grep -v '^[[:space:]]*#' | grep 'log_action')
[ -n "$la" ] && ! printf '%s\n' "$la" | grep -qE '\$\{?(K3_GW_OUT|js|body|payload|offers|list)\b' \
  && ok "log_action в меню шлюза не получает ответов ядра (в них секреты выдачи)" || bad "log_action с ответом ядра" "$la"

echo "── 29. мастер и «Протоколы»: apt занят системой — сказать заранее, ждать до 30 минут, поставить позже ──"
# Держатель блокировки dpkg — hold_apt (раздел 17): как unattended-upgrades после первого запуска сервера.
t_inst() {
    K3_TEST_TTY=1; K3_GW_SEL=(wg l2tp); K3_GW_SELPORT=(51820 ""); K3_GW_SELSKIP=(0 0); K3_GW_ROUTE=pool
    K3_GW_PLAN='{"inbounds": [{"kind": "wg", "packages": []}, {"kind": "l2tp", "packages": ["cmake", "strongswan-swanctl"]}]}'
    K3_GW_PROBE='{"supported": true, "packages": []}'
    k3_gw_install_step; echo "RC=$?"
}
apt_setup() {
    use_root "$1"; fake_ctl; gw_st false '{}' 0 0
    gwf enable.json '{"ok": true, "base": "10.77.0.0/16", "host": "203.0.113.10", "warnings": []}'
    gwf inbound_list.json "$(gw_list "wg")"
}
apt_setup gw-apt1; hold_apt 30
o=$(printf '2\n' | K3_APT_POLL=0.2 lib t_inst 2>&1 | strip); kill "$APT_HOLDER" 2>/dev/null; wait "$APT_HOLDER" 2>/dev/null
printf '%s' "$o" | flat | grep -q "Система ставит обновления (unattended-upgrade (pid $APT_HOLDER)) — после первого запуска сервера это обычно 5–15 минут" \
  && printf '%s' "$o" | grep -q 'Пакеты нужны: L2TP/IPsec\.' && printf '%s' "$o" | grep -q '1) Подождать (до 30 минут; Enter или Ctrl+C — перестать ждать)' \
  && gw_calls | grep -qx 'gw inbound install wg --port 51820 --json' && ! gw_calls | grep -q 'inbound install l2tp' \
  && printf '%s' "$o" | grep -q '\[2/2\] L2TP/IPsec … позже: нужны пакеты, а система ставит обновления' \
  && printf '%s' "$o" | grep -q 'Поставить позже: «Шлюз → Протоколы → L2TP/IPsec → Установить» (пункт 17)' \
  && gw_calls | grep -qx 'gw enable --json' && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "apt занят: заранее — кто держит и сколько обычно ждать; «2» — WireGuard ставится, L2TP/IPsec — позже с точной подсказкой, шлюз включается" \
  || bad "apt занят: поставить без пакетов" "$(gw_seq) | $o"
apt_setup gw-apt2; hold_apt 30
o=$(printf '0\n' | K3_APT_POLL=0.2 lib t_inst 2>&1 | strip); kill "$APT_HOLDER" 2>/dev/null; wait "$APT_HOLDER" 2>/dev/null
printf '%s' "$o" | grep -q 'RC=2' && ! gw_calls | grep -q 'inbound install' && ok "«0» — назад к выходам, ничего не поставлено" || bad "apt занят: назад" "$(gw_seq) | $o"
apt_setup gw-apt3; hold_apt 1.2
o=$(printf '1\n' | K3_GW_APT_WAIT=20 K3_APT_POLL=0.2 lib t_inst 2>&1 | strip); wait "$APT_HOLDER" 2>/dev/null
printf '%s' "$o" | grep -q 'Система ставит обновления (unattended-upgrade' && printf '%s' "$o" | grep -q 'жду: 0 мин из 0' \
  && printf '%s' "$o" | grep -q 'Система закончила с обновлениями — ставлю' && gw_calls | grep -qx 'gw inbound install l2tp --json' \
  && gw_calls | grep -qx 'gw inbound install wg --port 51820 --json' \
  && ok "«1» — ждём со строкой и временем вместо точек; система закончила — ставится всё" || bad "apt занят: дождались" "$(gw_seq) | $o"
apt_setup gw-apt4; hold_apt 30
o=$(printf '1\n\n' | K3_GW_APT_WAIT=600 K3_APT_POLL=0.2 lib t_inst 2>&1 | strip); kill "$APT_HOLDER" 2>/dev/null; wait "$APT_HOLDER" 2>/dev/null
printf '%s' "$o" | grep -q 'Не ждём: ставлю то, чему пакеты не нужны' && ! gw_calls | grep -q 'inbound install l2tp' \
  && gw_calls | grep -qx 'gw inbound install wg --port 51820 --json' && grep -q 'APT: мастер шлюза не дождался' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "Enter во время ожидания — не ждём: ставится то, чему пакеты не нужны" || bad "apt занят: Enter" "$(gw_seq) | $o"
apt_setup gw-apt5; hold_apt 30
o=$(printf '1\n' | K3_GW_APT_WAIT=1 K3_APT_POLL=0.2 lib t_inst 2>&1 | strip); kill "$APT_HOLDER" 2>/dev/null; wait "$APT_HOLDER" 2>/dev/null
! gw_calls | grep -q 'inbound install l2tp' && printf '%s' "$o" | grep -q 'Поставить позже: «Шлюз → Протоколы → L2TP/IPsec' \
  && ok "не дождались за срок мастера — то же «поставить позже»" || bad "apt занят: срок вышел" "$(gw_seq) | $o"
apt_setup gw-apt6; hold_apt 30
t_inst_gw() { K3_TEST_TTY=1; K3_GW_SEL=(wg); K3_GW_SELPORT=(51820); K3_GW_SELSKIP=(0); K3_GW_ROUTE=pool
              K3_GW_PLAN='{"inbounds": [{"kind": "wg", "packages": []}]}'; K3_GW_PROBE='{"packages": ["dnsmasq-base"]}'
              k3_gw_install_step; echo "RC=$?"; }
o=$(printf '2\n' | K3_APT_POLL=0.2 lib t_inst_gw 2>&1 | strip); kill "$APT_HOLDER" 2>/dev/null; wait "$APT_HOLDER" 2>/dev/null
printf '%s' "$o" | grep -q 'Пакеты нужны: самому шлюзу (dnsmasq-base)' && gw_calls | grep -qx 'gw inbound install wg --port 51820 --json' \
  && ! gw_calls | grep -q 'gw enable' && printf '%s' "$o" | grep -q 'Включите его, когда система закончит обновления' && printf '%s' "$o" | grep -q 'RC=1' \
  && ok "самому шлюзу нужны пакеты (dnsmasq) — протоколы стоят, включение — позже, с подсказкой «Шлюз → Включить шлюз»" || bad "apt занят: dnsmasq" "$(gw_seq) | $o"
apt_setup gw-apt7
gwf inbound_install.l2tp.err '@K3:wait Система ставит обновления (apt занят) — жду до 10 минут…'
gwf inbound_install.l2tp.json '{"ok": true, "kind": "l2tp", "record": {"enabled": true}, "notes": []}'
t_one() { K3_TEST_TTY=1; K3_GW_BUILD_KINDS=" l2tp "; k3_gw_install_one l2tp "" 1 1; echo "RC=$?"; }
o=$(lib t_one 2>&1 | strip)
printf '%s' "$o" | grep -q 'L2TP/IPsec (accel-ppp собирается из исходников — до двух минут)' \
  && [ "$(printf '%s\n' "$o" | grep -c 'Система ставит обновления (apt занят) — жду до 10 минут')" = 1 ] \
  && ok "ядро ждёт apt во время установки — его строка на экране (а не одни точки), один раз" || bad "строка ожидания ядра" "$o"
gwf inbound_install.l2tp.sleep 2.5
o=$(lib t_one 2>&1 | strip)
rm -f "$FAKE_DIR/gw/inbound_install.l2tp.sleep"
[ "$(printf '%s\n' "$o" | grep -c 'Система ставит обновления (apt занят) — жду до 10 минут')" = 1 ] && printf '%s' "$o" | grep -q '✓' \
  && ok "…и пока ядро ещё ждёт — строка выходит сразу (сторож stderr), без повтора после" || bad "живая строка ожидания" "$o"
hold_apt 30
o=$(lib t_one 2>&1 | strip); kill "$APT_HOLDER" 2>/dev/null; wait "$APT_HOLDER" 2>/dev/null
printf '%s' "$o" | grep -q 'L2TP/IPsec (сначала система закончит обновления)' && ! printf '%s' "$o" | grep -q 'до двух минут' \
  && ok "apt занят в начале установки — «до двух минут» не обещается" || bad "обещание при занятом apt" "$o"
rm -f "$FAKE_DIR/gw/inbound_install.l2tp.err"
gwf inbound_install.l2tp.json '{"ok": false, "error": "Пакеты cmake не установлены: apt занят дольше 10 минут: блокировку держит unattended-upgrade (pid 812) — система ставит обновления. Повторите, когда закончит.", "code": 1, "data": {"kind": "apt_busy"}}'
gwf inbound_install.l2tp.rc 1
o=$(lib t_one 2>&1 | strip)
printf '%s' "$o" | grep -q 'apt занят дольше 10 минут' && printf '%s' "$o" | grep -q 'Поставить позже: «Шлюз → Протоколы → L2TP/IPsec → Установить»' \
  && printf '%s' "$o" | grep -q 'RC=1' && ok "ядро не дождалось apt — причина и точная подсказка, как поставить позже" || bad "отказ ядра: apt занят" "$o"
apt_setup gw-apt8; hold_apt 30
gwf inbound_plan.json '{"ok": true, "inbounds": [{"kind": "l2tp", "title": "L2TP/IPsec", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "packages": ["cmake"], "port": null, "fixed": [{"proto": "udp", "port": 1701, "conflicts": []}]}]}'
t_pinst() { K3_TEST_TTY=1; k3_gw_proto_install l2tp; echo "RC=$?"; }
o=$(printf '2\n' | K3_APT_POLL=0.2 lib t_pinst 2>&1 | strip); kill "$APT_HOLDER" 2>/dev/null; wait "$APT_HOLDER" 2>/dev/null
printf '%s' "$o" | grep -q 'Пакеты нужны: L2TP/IPsec' && ! gw_calls | grep -q 'inbound install' \
  && printf '%s' "$o" | grep -q 'Поставить позже: «Шлюз → Протоколы → L2TP/IPsec → Установить»' \
  && ok "«Протоколы → Установить» при занятом apt — тот же вопрос; «позже» — ничего не ставится" || bad "Протоколы: apt занят" "$(gw_seq) | $o"

echo "── 19b. инструкция (пункт 13): 12 страниц, переход по номеру, каждая страница влезает в окно 80×24 ──"
use_root instr
t_instr() { K3_OPT=/opt/kaskad; clear() { printf '\f'; }; show_instructions; }   # K3_OPT — как на сервере: путь к kaskadctl идёт на страницу
feed=""; for _ in 1 2 3 4 5 6 7 8 9 10 11; do feed+=$'n\n'; done; feed+=$'0\n'
o=$(printf '%s' "$feed" | lib t_instr 2>&1 | strip)
# экран = всё между двумя clear; строка приглашения «  > » — ещё одна строка; ширина — в столбцах терминала (эмодзи — два)
res=$(printf '%s' "$o" | python3 -c '
import sys, unicodedata
screens = sys.stdin.read().split("\f")[1:]
def width(s):
    return sum(0 if unicodedata.combining(c) else (2 if unicodedata.east_asian_width(c) in "WF" else 1) for c in s)
big, skew = [], []
for i, scr in enumerate(screens, 1):
    lines = scr.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) + 1 > 24 or max(width(l) for l in lines) > 78:
        big.append(i)
    if width(lines[1]) != width(lines[0]):
        skew.append(i)
print(len(screens), "big=" + ",".join(map(str, big)), "skew=" + ",".join(map(str, skew)))
')
[ "$res" = "12 big= skew=" ] && ok "12 страниц; на каждой не больше 24 строк (с приглашением) и 78 столбцов, правая граница рамки на месте" \
  || bad "раскладка страниц инструкции" "$res"
miss=""
for t in "ДВИЖОК И ПЕРЕНОС (16)" "ШЛЮЗ (17): ЧТО ЭТО" "ПРОТОКОЛЫ И УСТРОЙСТВА" "ВЫХОДЫ И МАРШРУТЫ" "ЕСЛИ ЧТО-ТО НЕ ТАК"; do
    printf '%s' "$o" | grep -qF "═══ $t ═══" || miss+="$t; "
done
[ -z "$miss" ] && printf '%s' "$o" | grep -qF "KASKAD PRO v$CUR  (12/12)" && printf '%s' "$o" | grep -qF '[1-12]' \
  && ok "новые страницы 8–12 на месте, версия $CUR в рамке, подсказка «[1-12] К странице»" || bad "страницы 8–12" "$miss"
pages() { printf '%s' "$1" | lib t_instr 2>&1 | strip | grep -o '([0-9]*/12)' | tr '\n' ' '; }
[ "$(pages $'n\nn\np\n0\n')" = "(1/12) (2/12) (3/12) (2/12) " ] && ok "N и P листают страницы" || bad "N/P" "$(pages $'n\nn\np\n0\n')"
[ "$(pages $'10\n13\n99\n08\n0\n')" = "(1/12) (10/12) (10/12) (10/12) (10/12) " ] \
  && ok "номер — переход на страницу (10), лишнее (13, 99, 08) его не сдвигает" || bad "переход по номеру" "$(pages $'10\n13\n99\n08\n0\n')"
[ "$(pages $'12\nn\np\n0\n')" = "(1/12) (12/12) (12/12) (11/12) " ] && ok "на последней странице «далее» никуда не уводит" || bad "последняя страница" "$(pages $'12\nn\np\n0\n')"
# рамки «Загрузка» (run_startup) и «ХОСТИНГ СО СКИДКОЙ» (промо): правая граница — на месте, при любой длине версии
t_frames() {
    clear() { :; }; sleep() { :; }
    show_promo </dev/null 2>/dev/null | sed -n '2,4p'
    local lpad rpad v
    for v in 3.1.0 3.10.12; do
        KASKAD_VERSION="$v"
        eval "$(sed -n '/^run_startup() {/,/^}/p' "$S" | grep -E '^    local lpad=|^    \[ "\$rpad" -ge 1 \]|^    printf "\$\{MAGENTA\}║%\*s')"
    done
}
res=$(lib t_frames | strip | python3 -c '
import sys, unicodedata
def width(s):
    return sum(0 if unicodedata.combining(c) else (2 if unicodedata.east_asian_width(c) in "WF" else 1) for c in s)
w = sorted(set(width(l) for l in sys.stdin.read().split("\n") if l.strip()))
print(" ".join(map(str, w)))
')
[ "$res" = "64" ] && ok "рамки «Загрузка» (версии 3.1.0 и 3.10.12) и «ХОСТИНГ СО СКИДКОЙ»: все строки одной ширины, правая граница на месте" || bad "рамки" "$res"

# ══ WP-I (3.2): волна 3 в install.sh ══════════════════════════════════════════════════════════════
# Ответы поддельного kaskadctl для групп panel, rules, page, bot: $FAKE_DIR/<группа>/<подкоманда>.{json,rc,err}.
# k3-install-par: replay — общая подделка kaskadctl: части после неё (tools/k3_install_par.py) делают её тоже
cat > "$W/kaskadctl.wave3" <<'MOCK'
  "panel "*|"rules "*|"page "*|"bot "*)
    f="$FAKE_DIR/$1/$2"
    [ -f "$f.err" ] && cat "$f.err" >&2
    if [ -f "$f.json" ]; then cat "$f.json"; else echo '{"ok": true}'; fi
    exit "$(cat "$f.rc" 2>/dev/null || echo 0)" ;;
MOCK
python3 - "$W/kaskadctl.fake" "$W/kaskadctl.wave3" <<'PY'
import sys
fake, add = sys.argv[1], open(sys.argv[2]).read()
s = open(fake).read()
mark = '  "gw "*)\n'
assert mark in s
open(fake, "w").write(s.replace(mark, add + mark, 1))
PY
# k3-install-par: end
U3() { echo "$KASKAD_ROOT/etc/systemd/system"; }

echo "── 30. полное удаление (15) в 3.2: панель, бот на Python, правила, ускорение, ноги, sysctl — без хвостов ──"
# Слово подтверждения — то же зерно $RANDOM, что в t_uninst; $1 — ответ на вопрос о средствах сборки.
t_uninst3() {
    local words=("УДАЛИТЬ" "СТЕРЕТЬ" "СНЕСТИ" "УНИЧТОЖИТЬ" "ПРОЩАЙ") w
    RANDOM=7; w="${words[$((RANDOM % ${#words[@]}))]}"; RANDOM=7
    full_uninstall < <(printf '1\n%s\n%s\n\n' "$w" "${1:-}")
}
# Всё, что приносит 3.2: юниты, drop-in, хуки ядра, исходники модуля, списки правил, sysctl, ротация журнала.
uninst_fixture() {
    local u; u=$(U3)
    mkdir -p "$u/multi-user.target.wants" "$u/kaskad-leg@kawg1.service.d" "$u/kaskad-gw-wg.service.d" \
             "$KASKAD_DIR/panel" "$KASKAD_DIR/gw" "$KASKAD_ROOT/etc/kernel/postinst.d" "$KASKAD_ROOT/etc/kernel/header_postinst.d" \
             "$KASKAD_ROOT/usr/src/amneziawg-3.1.20260906" "$KASKAD_ROOT/var/lib/kaskad/rules" "$KASKAD_ROOT/etc/sysctl.d" \
             "$KASKAD_ROOT/etc/logrotate.d" "$KASKAD_ROOT/run/kaskad/panel" "$KASKAD_ROOT/sys/module/amneziawg"
    printf '[Service]\nExecStart=%s panel serve\n' "$K3_OPT/bin/kaskadctl" > "$u/kaskad-panel.service"
    printf '[Service]\nExecStart=%s bot run\nRestart=always\n' "$K3_OPT/bin/kaskadctl" > "$u/kaskad-bot.service"
    for x in kaskad-gw-rules.timer kaskad-gw-rules.service kaskad-gw-awgkmod.service kaskad-gw-wg.service kaskad-leg@.service; do
        printf '[Unit]\n' > "$u/$x"
    done
    printf '[Service]\nType=oneshot\n' > "$u/kaskad-leg@kawg1.service.d/kernel.conf"
    ln -sf ../kaskad-leg@.service "$u/multi-user.target.wants/kaskad-leg@kawg1.service"
    printf '[Service]\n' > "$u/kaskad-gw-wg.service.d/kaskad-gate.conf"
    printf '{"public": {"on": true, "port": 41234}}\n' > "$KASKAD_DIR/panel/config.json"
    printf '{\n  "enabled": true\n}\n' > "$KASKAD_DIR/gw/awg-kmod.json"
    printf '{"build-essential": {"by_kaskad": true}, "cmake": {"by_kaskad": true}, "libssl-dev": {"by_kaskad": false}, "openvpn": {"by_kaskad": true}}\n' \
        > "$KASKAD_DIR/gw/packages.json"
    touch "$FAKE_DIR/pkg.build-essential" "$FAKE_DIR/pkg.cmake" "$FAKE_DIR/pkg.libssl-dev" "$FAKE_DIR/pkg.openvpn"
    for x in postinst.d header_postinst.d; do printf '#!/bin/sh\n' > "$KASKAD_ROOT/etc/kernel/$x/zz-kaskad-amneziawg"; done
    printf 'PACKAGE_VERSION="3.1.20260906"\n' > "$KASKAD_ROOT/usr/src/amneziawg-3.1.20260906/dkms.conf"
    printf 'x\n' > "$KASKAD_ROOT/var/lib/kaskad/rules/current"
    for x in 90-kaskad.conf 90-kaskad-gw.conf 90-kaskad-tuning.conf 99-mine.conf; do printf 'x=1\n' > "$KASKAD_ROOT/etc/sysctl.d/$x"; done
    printf '/var/log/kaskad.log {\n}\n' > "$KASKAD_ROOT/etc/logrotate.d/kaskad"
    printf '{}\n' > "$KASKAD_ROOT/run/kaskad/live.json"
    ln -sfn "$K3_OPT/bin/kaskadctl" "$KASKAD_ROOT/usr/local/bin/kaskadctl"
    ln -sfn "$K3_OPT/bin/kaskad-mcp" "$KASKAD_ROOT/usr/local/bin/kaskad-mcp"
    printf '{}\n' > "$KASKAD_ROOT/var/log/kaskad-mcp.log"
    mkdir -p "$KASKAD_ROOT/root/.ssh"
    printf '%s\n' "ssh-ed25519 AAAAOWNER owner@pc" \
        'restrict,command="/usr/local/bin/kaskad-mcp",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-user-rc ssh-ed25519 AAAAAGENT kaskad-mcp-agent:laptop' \
        'command="/usr/local/bin/kaskad-mcp" ssh-ed25519 AAAAHAND kaskad-mcp-agent' > "$KASKAD_ROOT/root/.ssh/authorized_keys"
}
uninst_left() {   # что осталось из списка 3.2 (пусто — ничего)
    local u; u=$(U3)
    ls "$u" "$u/multi-user.target.wants" 2>/dev/null | grep -E '^kaskad-' | tr '\n' ' '
    for p in "$KASKAD_ROOT/etc/kernel/postinst.d/zz-kaskad-amneziawg" "$KASKAD_ROOT/etc/kernel/header_postinst.d/zz-kaskad-amneziawg" \
             "$KASKAD_ROOT/usr/src/amneziawg-3.1.20260906" "$KASKAD_ROOT/var/lib/kaskad" "$KASKAD_ROOT/etc/sysctl.d/90-kaskad.conf" \
             "$KASKAD_ROOT/etc/sysctl.d/90-kaskad-gw.conf" "$KASKAD_ROOT/etc/sysctl.d/90-kaskad-tuning.conf" \
             "$KASKAD_ROOT/etc/logrotate.d/kaskad" "$KASKAD_ROOT/run/kaskad" "$KASKAD_DIR" "$K3_OPT" \
             "$KASKAD_ROOT/usr/local/bin/kaskadctl" "$KASKAD_ROOT/usr/local/bin/kaskad-mcp" "$KASKAD_ROOT/var/log/kaskad-mcp.log"; do
        { [ -e "$p" ] || [ -L "$p" ]; } && printf '%s ' "${p#"$KASKAD_ROOT"}"
    done
}
use_root uninst32; fake_ctl; gw_st true '{"wg": {}}' 1 1; uninst_fixture
o=$(lib t_uninst3 1 2>&1 | strip)
for c in 'panel remove --purge --json' 'gw purge --users --yes' 'gw accel off --json' 'net teardown'; do
    ctl_log | grep -qxF -- "$c" || bad "15: нет вызова kaskadctl $c" "$(ctl_log)"
done
p=$(ctl_log | grep -nx 'gw purge --users --yes' | cut -d: -f1); a=$(ctl_log | grep -nx 'gw accel off --json' | cut -d: -f1)
[ -n "$p" ] && [ -n "$a" ] && [ "$p" -lt "$a" ] && ok "15: kaskadctl panel remove --purge, gw purge, затем gw accel off (переводить на amneziawg-go уже нечего), net teardown" \
  || bad "15: порядок удаления" "$(ctl_log)"
left=$(uninst_left)
[ "$(cat "$KASKAD_ROOT/root/.ssh/authorized_keys")" = "$(printf '%s\n' "ssh-ed25519 AAAAOWNER owner@pc" 'command="/usr/local/bin/kaskad-mcp" ssh-ed25519 AAAAHAND kaskad-mcp-agent')" ] \
  && printf '%s' "$o" | grep -q 'Ключи ИИ-агентов сняты из /root/.ssh/authorized_keys: 1' && printf '%s' "$o" | grep -q 'ключи ИИ-агентов, ядро' \
  && ok "15: ключи ИИ-агентов сняты (только наши строки, строка владельца и ручная строка целы), об этом сказано" \
  || bad "15: ключи агентов" "$(cat "$KASKAD_ROOT/root/.ssh/authorized_keys") | $o"
printf '%s' "$o" | flat | grep -q '! *В /root/.ssh/authorized_keys осталась 1 строка для kaskad-mcp, добавленная вручную — проверьте её' \
  && ok "15: о ручной строке для kaskad-mcp сказано в итоге (сама строка не тронута)" || bad "15: ручная строка kaskad-mcp не названа" "$o"
t_mman() { printf '%s\n' 'ssh-ed25519 AAAAOWNER owner@pc' '# command="/usr/local/bin/kaskad-mcp" старая' \
    'command="/usr/local/bin/kaskad-mcp" ssh-ed25519 AAAA1 a' 'command="/usr/local/bin/kaskad-mcp" ssh-ed25519 AAAA2 b' > "$K3_AUTH_KEYS"; k3_mcp_keys_manual; }
[ "$(lib t_mman 2>/dev/null)" = 2 ] && ok "15: ручные строки kaskad-mcp считаются без закомментированных и чужих" || bad "15: счёт ручных строк" "$(lib t_mman 2>&1)"
[ -z "$left" ] && ok "15: не осталось ни юнитов kaskad-* (панель, бот на Python, таймер правил, awgkmod, ноги kaskad-leg@ и их *.wants, drop-in), ни ссылок /usr/local/bin/kaskadctl и kaskad-mcp, ни аудита MCP, ни хуков ядра, ни /usr/src/amneziawg-*, ни /var/lib/kaskad, ни sysctl и logrotate Kaskad, ни /run/kaskad" \
  || bad "15: хвосты после удаления" "$left"
[ -e "$KASKAD_ROOT/etc/sysctl.d/99-mine.conf" ] && ok "15: чужой файл sysctl.d остался" || bad "15: удалён чужой sysctl"
grep -qE '^stop .*kaskad-leg@kawg1\.service' "$CALLS/systemctl.log" && grep -qx 'disable kaskad-leg@kawg1.service' "$CALLS/systemctl.log" \
  && grep -qx 'disable --now kaskad-bot' "$CALLS/systemctl.log" \
  && ok "15: экземпляр ноги из *.wants и бот на Python выключены через systemctl, не только стёрты" || bad "15: systemctl при удалении" "$(cat "$CALLS/systemctl.log")"
grep -q 'remove -y --auto-remove build-essential cmake$' "$CALLS/apt.log" && ! grep -q 'openvpn\|libssl-dev' "$CALLS/apt.log" \
  && printf '%s' "$o" | grep -q 'build-essential cmake' && ok "15: средства сборки accel-ppp, поставленные Kaskad (build-essential cmake), — отдельным вопросом; «1» — apt remove --auto-remove; чужие и не сборочные не трогаются" \
  || bad "15: средства сборки" "$(cat "$CALLS/apt.log") | $o"
use_root uninst32n; fake_ctl; gw_st true '{"wg": {}}' 1 1; uninst_fixture
lib t_uninst3 "" >/dev/null 2>&1
! grep -q 'remove' "$CALLS/apt.log" && ok "15: Enter на вопросе о средствах сборки — они остаются" || bad "15: средства сборки удалены без согласия" "$(cat "$CALLS/apt.log")"
use_root uninst32p; fake_ctl; engine_nft
o=$(lib t_uninst3 2>&1 | strip)
! printf '%s' "$o" | grep -q 'средства сборки' && ! ctl_log | grep -qE 'panel|accel' \
  && ok "15: без панели, ускорения и сборки accel-ppp — ни вопроса, ни лишних вызовов ядра" || bad "15: лишнее без волны 3" "$o | $(ctl_log)"

# T-N5 3.2.19, находка 1: после «полностью удалён» работали kaskad-egress (xray на 127.0.0.1:10808) и проба —
# юнит «not-found», но active. Порядок: сначала stop всех наших юнитов (и загруженных без файла), потом disable и
# файлы, потом daemon-reload; уцелевшие процессы из наших каталогов — TERM; чужие (редактор с /etc/kaskad) — нет.
use_root uninst32k; fake_ctl; gw_st true '{"wg": {}}' 1 1; uninst_fixture
printf '[Service]\nExecStart=%s/egress/xray\n' "$KASKAD_DIR" > "$(U3)/kaskad-egress.service"
printf '[Service]\nExecStart=/bin/bash %s/egress/probe.sh\n' "$KASKAD_DIR" > "$(U3)/kaskad-egress-probe.service"
printf '%s\n' kaskad-egress kaskad-egress-probe kaskad-leg@kawg1 kaskad-gw-wg kaskad-panel kaskad-bot kaskad-gw-rules.timer \
    'system-kaskad\x2dleg.slice' kaskad-ghost >> "$FAKE_DIR/active"          # kaskad-ghost — уже без файла (прошлое удаление)
fproc() {   # fproc <pid> <exe> <argv…>: процесс в $KASKAD_ROOT/proc
    local d="$KASKAD_ROOT/proc/$1"; mkdir -p "$d"; ln -s "$2" "$d/exe"; shift 2; printf '%s\0' "$@" > "$d/cmdline"
}
fproc 90001 "$KASKAD_DIR/egress/xray (deleted)" "$KASKAD_DIR/egress/xray" run -c "$KASKAD_DIR/egress/config.json"
fproc 90002 /usr/bin/bash /bin/bash "$KASKAD_DIR/egress/probe.sh"
fproc 90003 /usr/bin/vim vim "$KASKAD_DIR/config"
fproc 90004 /usr/bin/python3 python3 /srv/other.py "$KASKAD_DIR/x"
fproc 90005 /usr/bin/python3 /usr/bin/python3 -u "$K3_OPT/bin/kaskadctl" bot run
t_uninst3k() { kill() { echo "kill $*" >> "$CALLS/kill.log"; [ "$1" = -0 ] && return 1; return 0; }; t_uninst3 2; }
o=$(lib t_uninst3k 2>&1 | strip)
! grep -qE '^(kaskad-|system-kaskad)' "$FAKE_DIR/active" && [ "$(cat "$CALLS/stop_nofile.log" 2>/dev/null)" = kaskad-ghost ] \
  && ok "15: все службы Kaskad остановлены — и загруженная без файла (kaskad-ghost), и срез ног; ни одна не остановлена после стирания своего юнита" \
  || bad "15: службы пережили удаление" "active: $(tr '\n' ' ' < "$FAKE_DIR/active") | stop без файла: $(cat "$CALLS/stop_nofile.log" 2>/dev/null)"
st=$(grep -nE '^stop .*kaskad-egress\.service' "$CALLS/systemctl.log" | head -1 | cut -d: -f1)
di=$(grep -nE '^disable' "$CALLS/systemctl.log" | head -1 | cut -d: -f1)
tm=$(grep -nE '^stop .*kaskad-gw-rules\.timer' "$CALLS/systemctl.log" | head -1 | cut -d: -f1)
[ -n "$st" ] && [ -n "$di" ] && [ -n "$tm" ] && [ "$tm" -le "$st" ] && [ "$st" -lt "$di" ] \
  && grep -qE '^stop .*kaskad-ghost\.service' "$CALLS/systemctl.log" && grep -qE "^stop system-kaskad.x2dleg\.slice" "$CALLS/systemctl.log" \
  && ok "15: сначала stop (таймеры, потом службы, потом срез), только потом disable и файлы" || bad "15: порядок stop/disable" "$(cat "$CALLS/systemctl.log")"
[ "$(cat "$CALLS/kill.log" 2>/dev/null | head -1)" = "kill -TERM 90001 90002 90005" ] && printf '%s' "$o" | grep -q 'Остановлены процессы Kaskad без службы: 3' \
  && ok "15: уцелевшие процессы из наших каталогов (xray бота «(deleted)», probe.sh, kaskadctl) — TERM; vim с /etc/kaskad/config и чужой python — нет" \
  || bad "15: добивание процессов" "$(cat "$CALLS/kill.log" 2>/dev/null) | $o"
use_root uninst32k2; fake_ctl; engine_nft
o=$(lib t_uninst3k 2>&1 | strip)
[ ! -s "$CALLS/kill.log" ] && ! printf '%s' "$o" | grep -q 'процессы Kaskad без службы' && ok "15: процессов Kaskad без службы нет — ни kill, ни строки" || bad "15: лишний kill" "$(cat "$CALLS/kill.log")"
# запасной путь (ядро не отвечает) тоже останавливает раньше, чем стирает: k3_units_off
for fn in k3_gw_teardown_fallback k3_teardown_fallback egress_probe_remove egress_disable; do
    sed -n "/^${fn}() {/,/^}/p" "$S" | grep -q 'disable --now' && bad "$fn: disable --now вместо k3_units_off (несуществующий юнит — и никто не остановлен)"
done
ok "запасное удаление шлюза, движка, пробы и выхода — через k3_units_off (stop, затем disable по одному)"

# T-N5 3.2.19, №3: обновление тихо перезапускало вход awg2 (новый MTU) — в итоге обновления одна строка с причиной
use_root awgrs; mkdir -p "$KASKAD_DIR/gw"
printf '{"awg2": {"at": 2000, "why": "unit", "mtu_was": 1280, "mtu_now": 1216}, "awg1": {"at": 1000, "why": "unit"}, "wg": {"at": 2000, "why": "addr"}}\n' \
    > "$KASKAD_DIR/gw/restarts.json"
t_awgrs() { k3_awg_restarts_since 1500; echo "|"; k3_awg_restarts_since 3000; echo "|"; k3_awg_restarts_since 500; }
o=$(lib t_awgrs 2>&1)
[ "$o" = "Протоколы AmneziaWG перезапущены (новый MTU) — клиенты переподключатся сами за 15–25 с.
|
|
Протоколы AmneziaWG перезапущены (новая версия службы, новый MTU) — клиенты переподключатся сами за 15–25 с." ] \
  && sed -n '/^self_update() {/,/^}/p' "$S" | grep -q 'awgr=$(k3_awg_restarts_since "$t0")' \
  && ok "итог обновления: «Входы AmneziaWG перезапущены (новый MTU) — клиенты переподключатся сами за 15–25 с»; не было перезапуска — ни строки; WireGuard не в счёт" \
  || bad "строка о перезапуске входов при обновлении" "$o"

echo "── 31. удаление без ядра (kaskadctl не запускается): правила маршрутизации шлюза, таблицы 199/200, drop-in ──"
# ip: «ip -4 rule show» и таблица 200 — из файлов, всё остальное — в журнал.
cat > "$W/bin/ip" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CALLS/ip.log"
case "$*" in
  "-4 rule show") cat "$FAKE_DIR/ip_rules" 2>/dev/null ;;
  "-4 route show table 200") cat "$FAKE_DIR/ip_t200" 2>/dev/null ;;
esac
exit 0
MOCK
chmod +x "$W/bin/ip"
use_root uninst32f; gw_st true '{"wg": {}}' 1 1; uninst_fixture; : > "$CALLS/ip.log"
printf '%s\n' '0:	from all lookup local' '31995:	from all fwmark 0x10000/0x10000 lookup main' \
    '31998:	from 10.77.0.0/16 to 10.77.0.0/16 lookup main' '31999:	from 10.77.0.0/16 fwmark 0/0xffff lookup 199' \
    '31999:	from 192.168.5.0/24 lookup 777' '31998:	from 10.1.0.0/16 to 10.2.0.0/16 lookup main' \
    '32766:	from all lookup main' > "$FAKE_DIR/ip_rules"
printf '%s\n' 'local default dev lo scope host' 'throw 10.77.0.0/16' 'default via 192.0.2.1 dev eth1' > "$FAKE_DIR/ip_t200"
lib t_uninst3 >/dev/null 2>&1
miss=""
for c in 'rule del pref 31995 fwmark 0x10000/0x10000 lookup main' 'rule del pref 31998 from 10.77.0.0/16 to 10.77.0.0/16 lookup main' \
         'rule del pref 31999 from 10.77.0.0/16 fwmark 0x0/0xffff lookup 199' 'rule del fwmark 0x300/0xff00 lookup 200' \
         'route del unreachable default table 199' 'route del throw 10.77.0.0/16 table 200' 'rule del fwmark 0x200 lookup 200'; do
    grep -qxF -- "$c" "$CALLS/ip.log" || miss+="$c; "
done
[ -z "$miss" ] && ok "без ядра: ip rule 31995/31998/31999, fwmark 0x300/0xff00 и 0x200, unreachable в 199, throw в 200 — убраны" \
  || bad "без ядра: правила маршрутизации остались" "$miss"
! grep -qE '192\.168\.5\.0|10\.1\.0\.0|via 192\.0\.2\.1' "$CALLS/ip.log" && ok "без ядра: чужие правила с теми же номерами и чужой маршрут таблицы 200 не тронуты" \
  || bad "без ядра: задеты чужие правила" "$(cat "$CALLS/ip.log")"
left=$(uninst_left)
[ -z "$left" ] && grep -q '^-r amneziawg' "$CALLS/modprobe.log" && ! ctl_log | grep -q . \
  && ok "без ядра: панель, ускорение (хуки, исходники, modprobe -r), drop-in kaskad-gw-*.d, таймер правил и остальное — по файлам" \
  || bad "без ядра: хвосты" "$left | $(cat "$CALLS/modprobe.log" 2>/dev/null)"
cp "$W/ip-noop" "$W/bin/ip"

echo "── 32. sysctl.conf: ip_forward правится ровно по ключу (ip_forward_use_pmtu не трогается) ──"
use_root sysctl32
printf '%s\n' '#net.ipv4.ip_forward=1' 'net.ipv4.ip_forward_use_pmtu = 1' '# net.ipv4.ip_forward_update_priority=0' > "$KASKAD_ROOT/etc/sysctl.conf"
lib k3_sysctl_tuning >/dev/null 2>&1
[ "$(cat "$KASKAD_ROOT/etc/sysctl.conf")" = "$(printf '%s\n' 'net.ipv4.ip_forward=1' 'net.ipv4.ip_forward_use_pmtu = 1' '# net.ipv4.ip_forward_update_priority=0')" ] \
  && ok "sysctl.conf: исправлена только net.ipv4.ip_forward, ip_forward_use_pmtu и прочие с тем же началом — как были" \
  || bad "sysctl.conf: задеты чужие ключи" "$(cat "$KASKAD_ROOT/etc/sysctl.conf")"
t_k3const() { echo "$K3_AWG_KMOD_VER $K3_AWG_KMOD_HOOK $K3_BUILD_PKGS"; }
py=$(PYTHONPATH="$ROOT_DIR/lib" python3 -c 'from kaskad.gw import awgkmod; from kaskad.gw.inbounds import accel
print(awgkmod.VERSION, awgkmod.HOOK_NAME, " ".join(p for p in accel.BUILD_DEPS if p not in ("curl", "ca-certificates")))' 2>&1)
[ "$(lib t_k3const)" = "$py" ] && ok "версия модуля, имя хуков ядра и средства сборки в install.sh — те же, что в коде ядра" \
  || bad "константы install.sh разошлись с ядром" "$(lib t_k3const) ≠ $py"

echo "── 33. бот на Python и обновление 3.1.0 → 3.2: BOT_IMPL, юнит, мониторинг, панель ──"
BOTU() { echo "$KASKAD_ROOT/etc/systemd/system/kaskad-bot.service"; }
bash_unit() { printf '[Unit]\nDescription=Kaskad Telegram Bot\n[Service]\nType=simple\nExecStart=%s --bot-daemon\nRestart=always\n' "$K3_OPT/bin/gokaskad" > "$(BOTU)"; }
cfg_impl() { sed -n 's/^BOT_IMPL="\(.*\)"$/\1/p' "$KASKAD_DIR/config" | tail -1; }
use_root bot1
lib init_config >/dev/null 2>&1
[ "$(cfg_impl)" = python ] && ok "новая установка: в config BOT_IMPL=\"python\"" || bad "init_config: BOT_IMPL" "$(cat "$KASKAD_DIR/config")"
# обновление с бота на bash: --k3-post-update заполняет BOT_IMPL=bash, юнит бота не трогает и не перезапускает
use_root bot2; printf 'BOT_TOKEN="1:x"\nBOT_CHAT_ID="5"\n' > "$KASKAD_DIR/config"; bash_unit; cp "$(BOTU)" "$W/bot2.orig"
echo kaskad-bot > "$FAKE_DIR/active"
bash "$S" --k3-post-update </dev/null >/dev/null 2>&1
[ "$(cfg_impl)" = bash ] && cmp -s "$(BOTU)" "$W/bot2.orig" && ! grep -qE '(^| )(restart|try-restart|stop) kaskad-bot' "$CALLS/systemctl.log" \
  && grep -qx 'try-restart --no-block kaskad-panel.service' "$CALLS/systemctl.log" \
  && ok "обновление: был бот на bash — BOT_IMPL=bash, юнит и работающий бот не тронуты; код сменился — try-restart kaskad-panel" \
  || bad "обновление с ботом на bash" "$(cat "$KASKAD_DIR/config") | $(cat "$CALLS/systemctl.log")"
: > "$CALLS/systemctl.log"; bash "$S" --k3-post-update </dev/null >/dev/null 2>&1
! grep -q 'kaskad-panel' "$CALLS/systemctl.log" && ok "повтор (код тот же): панель не перезапускается" || bad "лишний перезапуск панели" "$(cat "$CALLS/systemctl.log")"
use_root bot3; printf 'BOT_TOKEN=""\n' > "$KASKAD_DIR/config"
bash "$S" --k3-post-update </dev/null >/dev/null 2>&1
[ "$(cfg_impl)" = python ] && ok "обновление без бота: BOT_IMPL=python (бот, если его заведут, будет на Python)" || bad "BOT_IMPL без бота" "$(cat "$KASKAD_DIR/config")"
sed -i.bak 's/^BOT_IMPL=.*/BOT_IMPL="bash"/' "$KASKAD_DIR/config"; cp "$KASKAD_DIR/config" "$W/bot3.cfg"
bash "$S" --k3-post-update </dev/null >/dev/null 2>&1
cmp -s "$KASKAD_DIR/config" "$W/bot3.cfg" && ok "заданный BOT_IMPL не переписывается" || bad "BOT_IMPL переписан" "$(cat "$KASKAD_DIR/config")"
# бот на Python и смена кода: перезапуск только его (try-restart), юнит не переписывается
use_root bot4; printf 'BOT_IMPL="python"\n' > "$KASKAD_DIR/config"
printf '[Service]\nExecStart=%s bot run\n' "$K3_OPT/bin/kaskadctl" > "$(BOTU)"; cp "$(BOTU)" "$W/bot4.orig"
bash "$S" --k3-post-update </dev/null >/dev/null 2>&1
grep -qx 'try-restart --no-block kaskad-bot.service' "$CALLS/systemctl.log" && cmp -s "$(BOTU)" "$W/bot4.orig" \
  && ok "код сменился, бот на Python: try-restart kaskad-bot (новый код), юнит прежний" || bad "бот на Python после обновления" "$(cat "$CALLS/systemctl.log")"
# start_bot: юнит по BOT_IMPL
t_start() { source "$KASKAD_CONF"; k3_lib_ok() { return 0; }; start_bot; }
use_root bot5; fake_ctl; printf 'BOT_TOKEN="1:x"\nBOT_CHAT_ID="5"\nBOT_IMPL="python"\n' > "$KASKAD_DIR/config"
o=$(lib t_start 2>&1 | strip)
grep -qx "ExecStart=$K3_OPT/bin/kaskadctl bot run" "$(BOTU)" && grep -qx 'Restart=always' "$(BOTU)" && grep -qx 'enable kaskad-bot' "$CALLS/systemctl.log" \
  && ok "новый бот: kaskad-bot.service → ExecStart=/opt/kaskad/bin/kaskadctl bot run, Restart=always" || bad "юнит бота на Python" "$(cat "$(BOTU)" 2>&1) | $o"
sed -i.bak 's/^BOT_IMPL=.*/BOT_IMPL="bash"/' "$KASKAD_DIR/config"; rm -f "$(BOTU)"
o=$(lib t_start 2>&1 | strip)
grep -qx "ExecStart=$K3_OPT/bin/gokaskad --bot-daemon" "$(BOTU)" || grep -qx "ExecStart=$KASKAD_ROOT/usr/local/bin/gokaskad --bot-daemon" "$(BOTU)" \
  && ok "BOT_IMPL=bash: прежний юнит --bot-daemon" || bad "юнит бота на bash" "$(cat "$(BOTU)" 2>&1)"
# переключатель (меню 8 → 8) и мониторинг
t_switch() { k3_lib_ok() { return 0; }; k3_bot_switch "$1"; }
use_root bot6; fake_ctl; printf 'BOT_TOKEN="1:x"\nBOT_IMPL="bash"\n' > "$KASKAD_DIR/config"; bash_unit; echo kaskad-bot > "$FAKE_DIR/active"
mkdir -p "$KASKAD_DIR/monitors"; printf 'MON_IP="1.2.3.4"\nMON_INTERVAL=60\nMON_THRESHOLD=100\n' > "$KASKAD_DIR/monitors/1.2.3.4.conf"
echo kaskad-monitor >> "$FAKE_DIR/active"
o=$(lib t_switch python 2>&1 | strip)
[ "$(cfg_impl)" = python ] && grep -q 'kaskadctl bot run' "$(BOTU)" && grep -qx 'restart kaskad-bot' "$CALLS/systemctl.log" \
  && grep -qx 'stop kaskad-monitor' "$CALLS/systemctl.log" \
  && ok "на Python: BOT_IMPL, юнит, перезапуск работавшего бота; kaskad-monitor выключен (мониторы — в боте)" || bad "переключение на Python" "$o | $(cat "$CALLS/systemctl.log")"
: > "$CALLS/systemctl.log"; echo kaskad-bot > "$FAKE_DIR/active"
o=$(lib t_switch bash 2>&1 | strip)
[ "$(cfg_impl)" = bash ] && grep -q -- '--bot-daemon' "$(BOTU)" && grep -qx 'restart kaskad-bot' "$CALLS/systemctl.log" \
  && grep -qx 'start kaskad-monitor' "$CALLS/systemctl.log" && [ -f "$KASKAD_ROOT/etc/systemd/system/kaskad-monitor.service" ] \
  && ok "обратно на bash: юнит --bot-daemon, перезапуск; мониторы есть — kaskad-monitor включён снова" || bad "возврат на bash" "$o | $(cat "$CALLS/systemctl.log")"
t_sync() { sync_monitoring_service; }
use_root bot7; printf '[Service]\nExecStart=%s bot run\n' "$K3_OPT/bin/kaskadctl" > "$(BOTU)"; echo kaskad-bot > "$FAKE_DIR/active"
mkdir -p "$KASKAD_DIR/monitors"; printf 'MON_IP="1.2.3.4"\n' > "$KASKAD_DIR/monitors/1.2.3.4.conf"
lib t_sync >/dev/null 2>&1
! grep -qE '^(start|enable) kaskad-monitor' "$CALLS/systemctl.log" && [ ! -e "$KASKAD_ROOT/etc/systemd/system/kaskad-monitor.service" ] \
  && ok "sync_monitoring_service: работает бот на Python — kaskad-monitor не включается" || bad "мониторинг при боте на Python" "$(cat "$CALLS/systemctl.log")"
: > "$FAKE_DIR/active"; lib t_sync >/dev/null 2>&1
grep -qx 'start kaskad-monitor' "$CALLS/systemctl.log" && ok "бот на Python остановлен — мониторы снова у kaskad-monitor" || bad "мониторинг без бота" "$(cat "$CALLS/systemctl.log")"
# возврат на 2.13: бот на Python — обратно на bash, панель остановлена (настройки остаются)
t_ret3() { k3_lib_ok() { return 0; }; k3_return_wave3; }
use_root bot8; fake_ctl; printf 'BOT_IMPL="python"\n' > "$KASKAD_DIR/config"
printf '[Service]\nExecStart=%s bot run\n' "$K3_OPT/bin/kaskadctl" > "$(BOTU)"; printf '[Service]\n' > "$KASKAD_ROOT/etc/systemd/system/kaskad-panel.service"
o=$(lib t_ret3 2>&1 | strip)
[ "$(cfg_impl)" = bash ] && grep -q -- '--bot-daemon' "$(BOTU)" && ctl_log | grep -qx 'panel remove' && printf '%s' "$o" | grep -q 'Веб-панель остановлена' \
  && ok "16 → 4: бот на Python → на bash (2.13 знает только его), BOT_IMPL=bash; kaskadctl panel remove (без --purge)" || bad "возврат на 2.13: волна 3" "$o | $(ctl_log)"

echo "── 34. меню 3.2: новые пункты на месте, каждый экран влезает в окно 80×24 ──"
# Экран — всё между двумя clear (подмена печатает \f); строка приглашения read -p (её не видно без терминала) —
# ещё одна строка; ширина — в столбцах терминала (эмодзи W/F — два). $1 — строка, с которой экран кончается (не
# включая её: дальше — то, что выводится уже после ответа).
fit() {
    python3 -c '
import os, sys, unicodedata
stop = sys.argv[1] if len(sys.argv) > 1 else ""
def width(s):
    return sum(0 if unicodedata.combining(c) else (2 if unicodedata.east_asian_width(c) in "WF" else 1) for c in s)
bad = []
scr = sys.stdin.read().split("\f")[1:]
for i, s in enumerate(scr, 1):
    lines = s.split("\n")
    if stop:
        for j, l in enumerate(lines):
            if stop in l:
                lines = lines[:j]
                break
    if lines and lines[-1] == "":
        lines.pop()
    h, w = len(lines) + 1, max([width(l) for l in lines] or [0])
    if h > int(os.environ.get("FIT_H") or 24) or w > int(os.environ.get("FIT_W") or 80):
        bad.append("%d:%dx%d" % (i, w, h))
print("%d %s" % (len(scr), ",".join(bad) or "ok"))' "$@"
}
scr() { clear() { printf '\f'; }; K3_TEST_TTY=1; "$@"; }
t_menu3() { scr show_menu; }
use_root menu3; fake_ctl; gw_st false '{}' 0 0
o=$(lib t_menu3 <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -q ' 18) Веб-панель$' && ! printf '%s' "$o" | grep -q '^Панель:' && ok "18) Веб-панель; панели нет — строки «Панель:» в шапке нет" || bad "пункт 18" "$o"
[ "$(printf '%s' "$o" | fit)" = "1 ok" ] && ok "главное меню: 80×24 (не настроено)" || bad "главное меню не влезает" "$(printf '%s' "$o" | fit) $o"
# меню 16 в 80×24 (тестер T-O: слова рвались посреди): пришёл с бесплатной версии — самые длинные пункты
t_m16s() { scr k3_engine_menu; }
use_root m16w; fake_ctl; prog_ready
printf '{\n  "engine": "nft",\n  "forwards": [\n    {"proto": "tcp", "port": 443, "to": "2.2.2.2", "to_port": 443, "origin": "free"}\n  ],\n  "schema": 1\n}\n' > "$KASKAD_DIR/state.json"
o=$(printf '0\n' | K3_TERM_COLS=80 lib t_m16s 2>&1 | strip)
r=$(printf '%s' "$o" | fit)
[ "$r" = "1 ok" ] && printf '%s' "$o" | tr '\n' ' ' | tr -s ' ' | grep -q '4) Поставить Kaskad PRO 2.13 — прежняя версия PRO вместо этой (не бесплатная); шлюз (17) удаляется' \
  && ok "меню 16 в 80×24 (тестер T-O): длинные пункты — по словам, с отступом, ничего не шире окна" || bad "меню 16: 80×24" "$r $o"
use_root menu3b; engine_nft
o=$(lib t_menu3 <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -q 'Ядро Kaskad 3 недоступно' && [ "$(printf '%s' "$o" | fit)" = "1 ok" ] \
  && ok "главное меню со строкой «Ядро Kaskad 3 недоступно» — всё ещё 80×24" || bad "главное меню без ядра" "$(printf '%s' "$o" | fit)"
# много входов и устройств, а живых выходов нет; ядро не ответило на summary — шапка из state.json и live.json через jq
# (k3_gw_dead, то же правило, что красная строка меню 17): красная «нет интернета у устройств», ширина ≤ 80
use_root menu3c; fake_ctl; echo '{"ok": false, "error": "сломано"}' > "$FAKE_DIR/summary.json"
gw_st true '{"wg": {}, "awg1": {}, "awg2": {}, "awg3": {}, "l2tp": {}, "ikev2": {}, "sstp": {}, "openvpn": {}, "ocserv": {}, "socks5": {}, "pptp": {}}' 120 30
mkdir -p "$KASKAD_ROOT/run/kaskad"
live3() { printf '{"v": 1, "ts": %s, "gateway": {"enabled": true}, "exits": {"e-1": {"alive": %s, "kind": "vless", "enabled": true}, "e-2": {"alive": false, "kind": "awg", "enabled": true}}}\n' "$1" "$2" > "$KASKAD_ROOT/run/kaskad/live.json"; }
live3 "$(date +%s)" false
o=$(lib t_menu3 <<< "0" 2>&1)
printf '%s' "$o" | strip | grep -qx 'Шлюз: нет интернета у устройств — 17' \
  && printf '%s' "$o" | grep 'Шлюз:' | grep -qF "$(printf '\033[0;31mнет интернета')" \
  && printf '%s' "$o" | strip | grep -q '^✗ Ядро Kaskad 3 недоступно' \
  && ok "ядро не ответило, живых выходов нет (live.json свежий, пул, при отказе — блок) — «Шлюз: нет интернета у устройств» красным" \
  || bad "пометка «нет интернета»" "$(printf '%s' "$o" | strip | grep '^Шлюз')"
live3 "$(date +%s)" true; o=$(lib t_menu3 <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qx 'Шлюз: 11 протоколов, 120 устройств' && [ "$(printf '%s' "$o" | fit)" = "1 ok" ] \
  && ok "выход жив — «Шлюз: 11 входов, 120 устройств», меню в 80×24" || bad "строка 17 с живым выходом" "$(printf '%s' "$o" | grep '^Шлюз') $(printf '%s' "$o" | fit)"
live3 "$(( $(date +%s) - 300 ))" false; o=$(lib t_menu3 <<< "0" 2>&1 | strip)
! printf '%s' "$o" | grep -q 'нет интернета' && ok "live.json старше минуты (сборщик не пишет) — неизвестно, пометки нет" || bad "старый live.json" "$(printf '%s' "$o" | grep '^Шлюз')"
live3 "$(date +%s)" false
sed -i.bak 's/"fail": "block"/"fail": "direct"/' "$KASKAD_DIR/state.json"; o=$(lib t_menu3 <<< "0" 2>&1 | strip)
! printf '%s' "$o" | grep -q 'нет интернета' && ok "при отказе — напрямую: интернет у устройств есть, пометки нет" || bad "fail=direct" "$(printf '%s' "$o" | grep '^Шлюз')"
sed -i.bak 's/"fail": "direct"/"fail": "block"/' "$KASKAD_DIR/state.json"
t_gw3() { scr k3_gw_menu; }
o=$(lib t_gw3 <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -q 'Нет интернета у устройств: ни один выход не работает' && printf '%s' "$o" | grep -q '^ 8) Правила «Россия напрямую»$' \
  && printf '%s' "$o" | grep -q '^ 9) Куда пойдёт трафик? — проверить домен или IP$' && [ "$(printf '%s' "$o" | fit)" = "1 ok" ] \
  && ok "17: красная строка «Нет интернета у устройств», пункт «8) Правила «Россия напрямую»»; экран 80×24 (и шапка — B1-3)" \
  || bad "меню шлюза" "$(printf '%s' "$o" | fit) $o"
[ -z "$(gw_calls | grep -v '^gw ')" ] && ok "главное меню и шапка 17 о выходах ядро не спрашивают (только live.json и state.json)" || bad "лишние вызовы" "$(gw_calls)"
# правила «Россия напрямую»
mkdir -p "$FAKE_DIR/rules"
printf '%s\n' '{"ok": true, "default": "ru_direct", "default_applied": "ru_direct", "block_ads": true, "block_torrent": false,
  "lists": {"label": "geoip 202610040942, geosite 202604152235"}, "ru_direct": 2, "devices": [{"id": "d-00000001", "name": "Телефон",
  "applied": "ru_direct", "from": "default"}, {"id": "d-00000002", "name": "Ноутбук", "applied": "all_exit", "from": "device"}],
  "users": [{"id": "u-00000001", "name": "Мама", "rules": "inherit"}]}' > "$FAKE_DIR/rules/status.json"
t_rules3() { scr k3_rules_menu; }
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n\n2\n\n4\n\n0\n' | lib t_rules3 2>&1 | strip)
ctl_log | grep -qx 'rules default all_exit' && ctl_log | grep -qx 'rules block ads off' && ctl_log | grep -qx 'rules update' \
  && printf '%s' "$o" | grep -q 'Списки: geoip 202610040942' && [ "$(printf '%s' "$o" | fit)" = "4 ok" ] \
  && ok "правила: профиль узла, реклама, «обновить сейчас» — kaskadctl rules default|block|update; экраны 80×24" || bad "меню правил" "$(ctl_log) | $(printf '%s' "$o" | fit)"
# люди: правила устройства и пользователя, страница
gwf user_list.json '{"ok": true, "users": [{"id": "u-00000001", "name": "Мама", "route": null, "enabled": true, "devices": 2}]}'
gwf device_list.json '{"ok": true, "devices": [
 {"id": "d-00000001", "user": "u-00000001", "name": "Телефон", "kind": "phone", "platform": "android", "login": "t-1", "access": ["awg3"], "route": null, "enabled": true},
 {"id": "d-00000002", "user": "u-00000001", "name": "Ноутбук", "kind": "pc", "platform": "linux", "login": "n-2", "access": ["wg"], "route": null, "enabled": true}]}'
mkdir -p "$FAKE_DIR/page"; printf '{"ok": true, "pages": []}\n' > "$FAKE_DIR/page/list.json"
printf 'Страница для «Мама» включена.\nСсылка: https://203.0.113.10:41234/p/SECRETLINK\n' > "$FAKE_DIR/page/on.json"
t_people3() { scr k3_gw_people; }
: > "$CALLS/kaskadctl.log"
o=$(printf '10\n1\n2\n\n11\n3\n\n12\n1\n\n0\n0\n' | lib t_people3 2>&1 | strip)
ctl_log | grep -qx 'rules profile device d-00000001 ru_direct' && ctl_log | grep -qx 'rules profile user u-00000001 all_exit' \
  && ctl_log | grep -qx 'page on u-00000001' && printf '%s' "$o" | grep -q '^10) Правила «Россия напрямую» устройства$' \
  && printf '%s' "$o" | grep -q '^12) Страница пользователя' && printf '%s' "$o" | grep -q 'SECRETLINK' \
  && ok "пользователи: 10 — профиль устройства, 11 — пользователя (kaskadctl rules profile), 12 — страница (kaskadctl page on, ссылка на экране)" \
  || bad "пользователи: правила и страница" "$(ctl_log) | $o"
! grep -q 'SECRETLINK' "$KASKAD_ROOT/var/log/kaskad.log" 2>/dev/null && ok "ссылка страницы не попала в журнал" || bad "ссылка страницы в журнале"
r=$(printf '%s' "$o" | fit); [ "${r#* }" = ok ] && ok "экраны «Пользователи и устройства» и страницы — 80×24 ($r)" || bad "пользователи: экраны не влезают" "$r"
# «Куда пойдёт трафик?» (17 → 9): устройство по номеру или Enter — новое по умолчанию; домены по одному; ответ ядра
# (kaskadctl gw trace) — по словам в ширину окна, итог цветом; ошибка ядра — одной строкой
printf '%s\n' 'Итог: Через выход «AmneziaWG 2.0 · 203.0.113.141»' \
  '1. Устройство «Телефон» (Мама), профиль «Россия напрямую» (от пользователя)' '   вход AmneziaWG 3.1' \
  '2. www.youtube.com (как youtube.com) — список «всегда через выход» (категория youtube)' \
  '   значит, по маршруту устройства, даже если адрес в сетях России' \
  '3. Маршрут: пул выходов (по умолчанию)' '   при отказе выхода — блок' \
  '4. Сейчас выход «AmneziaWG 2.0 · 203.0.113.141» — работает, рукопожатие 40 с назад' \
  '! Блок по домену работает, когда устройство спрашивает адреса у DNS шлюза (так в выданных настройках); свой DNS в браузере (DoH) его обходит.' \
  > "$FAKE_DIR/gw/trace.json"
t_trace3() { scr k3_gw_trace_menu; }
: > "$CALLS/kaskadctl.log"
o=$(printf '1\nhttps://www.youtube.com/\n -x.ru\n\n' | K3_TERM_COLS=80 lib t_trace3 2>&1)
r=$(printf '%s' "$o" | strip | fit)
ctl_log | grep -qx 'gw trace d-00000001 https://www.youtube.com/' && ctl_log | grep -qx 'gw trace d-00000001 x.ru' \
  && printf '%s' "$o" | grep -qF "$(printf '\033[0;36mИтог: Через выход')" && printf '%s' "$o" | strip | grep -q '^ *1) Телефон — Мама$' \
  && printf '%s' "$o" | strip | tr '\n' ' ' | tr -s ' ' | grep -qF 'свой DNS в браузере (DoH) его обходит.' && [ "$r" = "3 ok" ] \
  && ok "17 → 9: устройство по номеру, домены по одному (kaskadctl gw trace <устройство> <домен>, ведущий «-» снят), итог цветом; 80×24" \
  || bad "куда пойдёт трафик: меню" "$r | $(ctl_log) | $o"
mkdir -p "$FAKE_DIR/gw"; printf '2\n' > "$FAKE_DIR/gw/trace.-.rc"; : > "$FAKE_DIR/gw/trace.-.json"
printf '%s\n' '@K3:error «notadomain» — не домен и не адрес IPv4. Пример: youtube.com, ya.ru, 8.8.8.8.' > "$FAKE_DIR/gw/trace.-.err"
: > "$CALLS/kaskadctl.log"
o=$(printf '\nnot a domain\n\n' | K3_TERM_COLS=80 lib t_trace3 2>&1 | strip)
ctl_log | grep -qx 'gw trace - notadomain' && printf '%s' "$o" | grep -q '^\[!\] «notadomain» — не домен и не адрес IPv4' \
  && [ "$(printf '%s' "$o" | fit)" = "2 ok" ] \
  && ok "17 → 9: Enter — новое устройство по умолчанию («-»); отказ ядра — одной строкой «[!] …»" || bad "куда пойдёт трафик: новое и ошибка" "$(ctl_log) | $o"
# 17 → 9 у устройства с несколькими входами: путь идёт через ОДИН — меню спрашивает, через какой (Enter — первый),
# и передаёт его ядру (kaskadctl gw trace … --inbound K); у устройства с одним входом не спрашивает (выше)
cp "$FAKE_DIR/gw/device_list.json" "$FAKE_DIR/gw/device_list.json.keep"
gwf device_list.json '{"ok": true, "devices": [
 {"id": "d-00000001", "user": "u-00000001", "name": "l2tp-g3", "kind": "pc", "platform": "windows", "login": "l-1", "access": ["l2tp", "socks5", "ikev2"], "route": null, "enabled": true}]}'
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n2\nya.ru\n\n' | K3_TERM_COLS=80 lib t_trace3 2>&1 | strip)
ctl_log | grep -qx 'gw trace d-00000001 ya.ru --inbound socks5' \
  && printf '%s' "$o" | grep -q '^ *2) SOCKS5$' && printf '%s' "$o" | grep -q 'l2tp-g3 через SOCKS5' \
  && [ "$(printf '%s' "$o" | fit)" = "2 ok" ] \
  && ok "17 → 9: у устройства с тремя входами — «через какой вход» (SOCKS5 решает xray), ядру — --inbound socks5" \
  || bad "куда пойдёт трафик: выбор входа" "$(ctl_log) | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n\nya.ru\n\n' | K3_TERM_COLS=80 lib t_trace3 2>&1 | strip)
ctl_log | grep -qx 'gw trace d-00000001 ya.ru --inbound l2tp' \
  && ok "17 → 9: Enter у выбора входа — первый выданный (L2TP/IPsec)" || bad "куда пойдёт трафик: вход по умолчанию" "$(ctl_log) | $o"
mv "$FAKE_DIR/gw/device_list.json.keep" "$FAKE_DIR/gw/device_list.json"
rm -f "$FAKE_DIR/gw/trace.-.rc" "$FAKE_DIR/gw/trace.-.err" "$FAKE_DIR/gw/trace.-.json" "$FAKE_DIR/gw/trace.json"
# T-G1 D2-1: панель закрыта для интернета — у ссылки нет адреса, человек её не откроет. Не тупик: полная ссылка через
# SSH-туннель и вопрос, открыть ли панель для интернета (как 18 → 3): «1» — пароль (его не было), доступ, ссылка заново.
printf '%s\n' 'Страница для «Мама» включена.' 'Ссылка: /p/SECRETLINK' '@K3:warn Панель сейчас закрыта для интернета (только через SSH-туннель): пользователь не откроет эту ссылку. Включите «Панель → Доступ из интернета» — ссылка станет полной.' > "$FAKE_DIR/page/on.json"
printf '{"ok": true, "enabled": true, "path": "/p/SECRETLINK", "url": null, "public": false}\n' > "$FAKE_DIR/page/url.json"
mkdir -p "$FAKE_DIR/panel"
printf '{"ok": true, "installed": true, "active": true, "public": {"on": false}, "password": false, "local_url": "http://127.0.0.1:1980/", "tunnel": "ssh -L 1980:127.0.0.1:1980 root@203.0.113.10"}\n' > "$FAKE_DIR/panel/status.json"
t_page3() { scr k3_gw_page_menu u-00000001 Мама; }
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n2\n\n0\n' | lib t_page3 2>&1 | strip)
printf '%s\n' "$o" | grep -qx '  http://127.0.0.1:1980/p/SECRETLINK' && printf '%s\n' "$o" | grep -qx '  ssh -L 1980:127.0.0.1:1980 root@203.0.113.10' \
  && printf '%s\n' "$o" | grep -qx '/p/SECRETLINK' && ctl_log | grep -qx 'page url u-00000001 --json' && ! ctl_log | grep -q 'panel public' \
  && [ "$(printf '%s' "$o" | k3_text_check)" = ok ] \
  && ok "страница при закрытой панели (D2-1): полная ссылка через SSH-туннель и вопрос; «2» — доступ не трогаем" || bad "страница: закрытая панель" "$(ctl_log) | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n1\n\n0\n' | lib t_page3 2>&1 | strip)
p=$(ctl_log | grep -nx 'panel password --generate' | cut -d: -f1); q=$(ctl_log | grep -nx 'panel public on' | cut -d: -f1)
u=$(ctl_log | grep -nx 'page url u-00000001' | cut -d: -f1)
[ -n "$p" ] && [ -n "$q" ] && [ -n "$u" ] && [ "$p" -lt "$q" ] && [ "$q" -lt "$u" ] \
  && ok "«1» — как 18 → 3: пароль (его не было), доступ из интернета, затем ссылка заново" || bad "страница: открыть панель" "$(ctl_log)"
printf '{"ok": true, "installed": false}\n' > "$FAKE_DIR/panel/status.json"
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n\n0\n' | lib t_page3 2>&1 | strip)
printf '%s' "$o" | grep -q 'веб-панель, а она не установлена: главное меню → 18' && ! ctl_log | grep -q 'panel public' \
  && ok "панели нет — сказано, где её поставить (меню 18), без вопроса о доступе" || bad "страница: панели нет" "$(ctl_log) | $o"
! grep -q 'SECRETLINK' "$KASKAD_ROOT/var/log/kaskad.log" 2>/dev/null && ok "ссылка страницы не попала в журнал и здесь" || bad "ссылка страницы в журнале"
# подписка для приложений (12 → 2, у включённой страницы — 4): своя ссылка, kaskadctl page sub …; смена и отзыв —
# с подтверждением цифрой (1 — да, 2 — нет, Enter — 2), ссылка — на экран, не в журнал. Поддельный ядро отвечает файлом
# page/sub.json на всё «page sub …»: текст — подписки «нет» (jq не разберёт), JSON со строкой человека — «включена».
printf '%s\n' 'Подписка для «Мама» включена.' 'Ссылка: https://203.0.113.10:41234/p/SUBLINK/sub' > "$FAKE_DIR/page/sub.json"
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n1\n\n0\n0\n' | lib t_page3 2>&1 | strip)
ctl_log | grep -qx 'page sub list --json' && ctl_log | grep -qx 'page sub on u-00000001' \
  && printf '%s' "$o" | grep -q '^ 2) Подписка для приложений (v2RayTun, Hiddify, incy…)$' && printf '%s\n' "$o" | grep -qx 'https://203.0.113.10:41234/p/SUBLINK/sub' \
  && [ "$(printf '%s' "$o" | k3_text_check)" = ok ] && r=$(printf '%s' "$o" | fit) && [ "${r#* }" = ok ] \
  && ok "12 → 2 «Подписка»: включить — kaskadctl page sub on, ссылка своей строкой; 80×24" || bad "подписка: включить" "$(ctl_log) | $(printf '%s' "$o" | fit) | $o"
printf '{"ok": true, "subs": [{"user": "u-00000001", "name": "Мама", "enabled": true, "devices": 1}]}\n' > "$FAKE_DIR/page/sub.json"
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n3\n\n2\n1\n\n0\n0\n' | lib t_page3 2>&1 | strip)
ctl_log | grep -qx 'page sub rotate u-00000001' && ! ctl_log | grep -q 'page sub off' \
  && printf '%s' "$o" | grep -q 'Выбор \[2\]' && printf '%s' "$o" | grep -q '^ 3) Отозвать подписку' \
  && ok "подписка: «3» и Enter — отзыв не сделан (по умолчанию «Нет»), «2» и «1» — новая ссылка (kaskadctl page sub rotate)" \
  || bad "подписка: смена и отзыв" "$(ctl_log) | $o"
! grep -q 'SUBLINK' "$KASKAD_ROOT/var/log/kaskad.log" 2>/dev/null && grep -q 'подписка пользователя u-00000001 — rotate' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "ссылка подписки не попала в журнал (в нём — действие и id)" || bad "подписка: журнал"
rm -f "$FAKE_DIR/page/sub.json"
# протоколы: строка движка AmneziaWG — номер после протоколов (SPEC-awg-engine §4; T-N5 №6 — цифрой, не «у»)
gwf inbound_list.json "$(gw_list "wg awg1 awg2 awg3 l2tp")"
gwf awg.status.json "$(printf '%s\n' 'Движок AmneziaWG: программа — модуль ядра ещё не включали (рекомендуется)')"
gwf awg.check.json "$(printf '%s\n' 'Модуль ядра здесь можно включить.' '  ядро 6.8.0-50-generic, процессоров 1, ОЗУ 1,9 ГБ, на диске свободно 4,9 ГБ' '  Поставится: компилятор и заголовки ядра (dkms, gcc, make, patch, linux-headers-6.8.0-50-generic, ≈210 МБ); сборка модуля 1–3 мин, затем самопроверка. Клиенты AmneziaWG переподключатся сами, обычно за 15–25 секунд; WireGuard не затрагивается.')"
t_proto3() { K3_TERM_COLS=80; scr k3_gw_protocols; }
: > "$CALLS/kaskadctl.log"
o=$(printf '12\n1\n\n\n0\n0\n' | lib t_proto3 2>&1 | strip)
printf '%s' "$o" | grep -q '^ 12) Движок AmneziaWG (модуль ядра / программа): программа' && gw_calls | grep -qx 'gw awg status' \
  && gw_calls | grep -qx 'gw awg check' && gw_calls | grep -qx 'gw awg engine kernel --why person:menu' \
  && printf '%s' "$o" | grep -q '≈200 МБ\|≈210 МБ' && [ "$(printf '%s' "$o" | fit)" = "4 ok" ] \
  && printf '%s' "$o" | flat | grep -q 'Клиенты AmneziaWG переподключатся сами, обычно за 15–25 секунд; WireGuard не затрагивается' \
  && ! printf '%s' "$o" | grep -q '≈2 с' \
  && ok "«Протоколы»: «12) Движок AmneziaWG» (номер после 11 протоколов) → gw awg status, «1» → gw awg check и Enter → gw awg engine kernel; «15–25 секунд», не «≈2 с»; 80×24" \
  || bad "движок в «Протоколах»" "$(gw_seq) | $(printf '%s' "$o" | fit)"
# T-N5 3.2.19, №2: «≈2 с» нигде (меню, мастер, ядро, бот, панель) — на стенде клиенты возвращались за 14–23 с
left=$(grep -rln '≈2 с\|один раз (≈' "$S" "$ROOT_DIR/lib/kaskad" 2>/dev/null)
k3r=$(sed -n 's/^K3_AWG_RECONNECT="\(.*\)"$/\1/p' "$S")
pyr=$(PYTHONPATH="$ROOT_DIR/lib" python3 -c 'from kaskad.gw import awgengine; print(awgengine.RECONNECT)')
[ -z "$left" ] && [ -n "$k3r" ] && [ "$k3r" = "$pyr" ] \
  && grep -q 'переподключатся сами, обычно за 15–25 секунд; WireGuard не затрагивается' "$ROOT_DIR/lib/kaskad/panel/static/i18n-ru.js" \
  && ok "«≈2 с» больше нигде; одна фраза «…переподключатся сами, обычно за 15–25 секунд; WireGuard не затрагивается» в меню, ядре и панели" \
  || bad "текст о переподключении" "$left | $k3r | $pyr"
mkdir -p "$KASKAD_DIR/gw"; printf '{\n  "enabled": true\n}\n' > "$KASKAD_DIR/gw/awg-kmod.json"; : > "$CALLS/kaskadctl.log"
o=$(printf '12\n2\n1\n\n0\n0\n' | lib t_proto3 2>&1 | strip)
printf '%s' "$o" | grep -q '^ 12) Движок AmneziaWG (модуль ядра / программа): модуль ядра' && gw_calls | grep -qx 'gw awg engine program --why person:menu' \
  && ok "на модуле — «2» и «1) Да» переводят на программу (gw awg engine program)" || bad "движок: программа" "$(gw_seq)"
rm -f "$KASKAD_DIR/gw/awg-kmod.json"
# смешивание (линия 3.1): ядро отказывает кодом 3 — меню спрашивает отдельно, «y» — --force
gwf awg.engine.once.rc 3
gwf awg.engine.once.json '{"ok": false, "error": "Смешивать программу и модуль нельзя: Вход AmneziaWG 3.1 …", "code": 3}'
: > "$CALLS/kaskadctl.log"
o=$(printf '12\n1\n\n1\n\n0\n0\n' | lib t_proto3 2>&1 | strip)
gw_calls | grep -qx 'gw awg engine kernel --why person:menu' && gw_calls | grep -qx 'gw awg engine kernel --force --why person:menu' \
  && ok "линия 3.1: отказ ядра (код 3) → вопрос о смешанном режиме цифрами → «1» — --force" || bad "движок: смешивание" "$(gw_seq) | $o"
# экспорт: предупреждение о секретах, путь по умолчанию
: > "$CALLS/kaskadctl.log"
o=$(printf '12\n3\n/root/x.json\n\n0\n0\n' | K3_TERM_COLS=80 lib t_proto3 2>&1 | strip)
gw_calls | grep -qx 'gw awg export --file /root/x.json' && printf '%s' "$o" | flat | grep -q 'храните его как пароль' \
  && [ "$(printf '%s' "$o" | fit)" = "4 ok" ] && ok "«Движок AmneziaWG» → 3: экспорт в файл с предупреждением о секретах (gw awg export --file); 80×24" || bad "экспорт AWG" "$(gw_seq)"
# импорт (T-N5 3.2.19, №8): сначала проверка файла (gw awg import --check), ошибка — словами и без вопроса
gwf awg.import.rc 3; gwf awg.import.err '@K3:error Нет файла /nonexistent.json — проверьте путь.'
: > "$CALLS/kaskadctl.log"
o=$(printf '12\n4\n/nonexistent.json\n\n0\n0\n' | K3_TERM_COLS=80 lib t_proto3 2>&1 | strip)
[ "$(gw_calls | grep -c 'gw awg import')" = 1 ] && gw_calls | grep -qx 'gw awg import --file /nonexistent.json --check' \
  && printf '%s' "$o" | grep -q 'Нет файла /nonexistent.json — проверьте путь' && ! printf '%s' "$o" | grep -q 'Да, импортировать' \
  && ok "импорт: нет файла — сразу «Нет файла … — проверьте путь», вопроса «импортировать?» нет" || bad "импорт: проверка до вопроса" "$(gw_seq) | $o"
rm -f "$FAKE_DIR/gw/awg.import.rc" "$FAKE_DIR/gw/awg.import.err"; : > "$CALLS/kaskadctl.log"
gwf awg.import.json 'Файл экспорта AmneziaWG: входы awg2, пользователей 1, устройств 2.'
o=$(printf '12\n4\n/root/x.json\n2\n\n0\n0\n' | K3_TERM_COLS=80 lib t_proto3 2>&1 | strip)
gw_calls | grep -qx 'gw awg import --file /root/x.json --check' && ! gw_calls | grep -qx 'gw awg import --file /root/x.json' \
  && printf '%s' "$o" | grep -q 'Файл экспорта AmneziaWG: входы awg2' && printf '%s' "$o" | grep -q 'Да, импортировать' \
  && ok "импорт: файл проверен и описан — потом вопрос цифрами; «2» — ничего не импортируется" || bad "импорт: вопрос после проверки" "$(gw_seq) | $o"
rm -f "$FAKE_DIR/gw/awg.import.json"
# мастер: первый вход AmneziaWG — вопрос «Модуль ядра? [Y/n]»; «n» — --awg-engine program, Enter — kernel
t_inst_awg() { K3_TEST_TTY=1; k3_gw_install_one awg2 "" 1 1; echo "RC=$?"; }
gw_st true '{}' 0 0; : > "$CALLS/kaskadctl.log"
o=$(printf '2\n' | lib t_inst_awg 2>&1 | strip)
gw_calls | grep -qx 'gw inbound install awg2 --awg-engine program --json' && printf '%s' "$o" | grep -q '1) Да, ускорить модулем ядра (рекомендуется)' \
  && printf '%s' "$o" | grep -q '2) Нет, оставить программу amneziawg-go' && printf '%s' "$o" | grep -q 'Выбор \[1\]:' \
  && printf '%s' "$o" | grep -q 'RC=0' && ok "первый вход AmneziaWG: вопрос цифрами (1 — модуль ядра, по умолчанию; 2 — программа), «2» → --awg-engine program" || bad "мастер: вопрос движка" "$(gw_seq) | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '\n' | lib t_inst_awg 2>&1 | strip)
gw_calls | grep -qx 'gw inbound install awg2 --awg-engine kernel --json' && printf '%s' "$o" | grep -q 'модуль ядра собирается' \
  && ok "Enter — модуль ядра (по умолчанию «да»): --awg-engine kernel, «модуль ядра собирается — 1–3 мин»" || bad "мастер: движок по умолчанию" "$(gw_seq) | $o"
gw_st true '{"awg1": {}}' 0 0; : > "$CALLS/kaskadctl.log"
o=$(lib t_inst_awg < /dev/null 2>&1 | strip)
gw_calls | grep -qx 'gw inbound install awg2 --json' && ! printf '%s' "$o" | grep -q 'Выбор \[1\]' \
  && ok "второй вход AmneziaWG — без вопроса (движок узла уже есть)" || bad "мастер: второй вход" "$(gw_seq) | $o"
gw_st true '{}' 0 0; : > "$CALLS/kaskadctl.log"
o=$(K3_BOT=1 lib t_inst_awg < /dev/null 2>&1 | strip)
gw_calls | grep -qx 'gw inbound install awg2 --json' && ok "без человека — без вопроса: ядро само берёт модуль ядра" || bad "мастер без человека" "$(gw_seq)"
# k3_choose: выбор цифрами (правило владельца — не y/n): Enter — пункт по умолчанию, неверный ввод — вопрос снова
t_choose() { n=$(k3_choose "Вопрос?" 2 "Первый" "Второй" "Третий"); echo "N=$n RC=$?"; }
o=$(printf 'x\n5\n3\n' | lib t_choose 2>&1 | strip)
printf '%s' "$o" | grep -q 'N=3 RC=0' && [ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта: введите номер от 1 до 3 (Enter — 2)')" = 2 ] \
  && printf '%s' "$o" | grep -q '^ *2) Второй$' && printf '%s' "$o" | grep -q 'Выбор \[2\]:' \
  && ok "k3_choose: пункты цифрами, «Выбор [2]:», неверный ввод — снова; номер — в stdout" || bad "k3_choose" "$o"
o=$(printf '\n' | lib t_choose 2>&1 | strip); o2=$(lib t_choose < /dev/null 2>&1 | strip)
printf '%s' "$o" | grep -q 'N=2 RC=0' && printf '%s' "$o2" | grep -q 'N= RC=1' && ok "k3_choose: Enter — пункт по умолчанию; конец ввода — код 1" || bad "k3_choose: по умолчанию" "$o | $o2"
# k3_choose_yes: два пункта, 0 — только пункт 1; пункт 2, Enter при «2» по умолчанию и конец ввода — 1
t_cy() { k3_choose_yes "Удалить?" 2 "Удалить" "Отмена"; echo "RC=$?"; }
r1=$(printf '1\n' | lib t_cy 2>/dev/null); r2=$(printf '2\n' | lib t_cy 2>/dev/null); r3=$(printf '\n' | lib t_cy 2>/dev/null)
r4=$(lib t_cy < /dev/null 2>/dev/null); r5=$(printf 'y\n\n' | lib t_cy 2>&1 | strip)
[ "$r1 $r2 $r3 $r4" = "RC=0 RC=1 RC=1 RC=1" ] && printf '%s' "$r5" | grep -q 'Нет такого пункта' && printf '%s' "$r5" | grep -q 'RC=1' \
  && ok "k3_choose_yes: 1 — да; 2, Enter (по умолчанию 2), конец ввода — нет; «y» — не ответ, вопрос снова" || bad "k3_choose_yes" "$r1 $r2 $r3 $r4 | $r5"
# Правило владельца: в SSH-меню — выбор цифрами. Ни подсказок (y/n) / [Y/n], ни разбора ответа «y», ни k3_yes.
yn=$(grep -nE '\((y/n|y/N|Y/n|Y/N)|\[(y/n|y/N|Y/n|Y/N)\]|k3_yes|= *"[yYдД]"|"" *\| *y *\||(^|[ (])y *\| *Y *\||Ответьте y' "$S")
[ -z "$yn" ] && ok "в install.sh не осталось вопросов y/n: ни (y/n), (Y/n), [Y/n], ни сравнений с «y», ни k3_yes" \
  || bad "в install.sh остались вопросы y/n" "$yn"
# 17: совет после обновления без человека (pending) — строка и вопрос при входе (по умолчанию «да»)
t_gw17() { scr k3_gw_menu; }
gw_st true '{"awg2": {}}' 1 1
python3 - "$KASKAD_DIR/state.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); d["gateway"]["awg_engine"] = {"pending": True}; json.dump(d, open(sys.argv[1], "w"))
PY
gwf awg.status.json '{"ok": true, "offer": {"show": true, "blocked": [], "pending": true}}'
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n\n0\n' | K3_TERM_COLS=80 lib t_gw17 2>&1 | strip)
gw_calls | grep -qx 'gw awg engine program --why person:menu-update' && printf '%s' "$o" | grep -q 'После обновления: AmneziaWG работает программой' \
  && printf '%s' "$o" | grep -q 'Совет: перевести AmneziaWG на модуль ядра' && [ "$(printf '%s' "$o" | fit)" = "2 ok" ] \
  && ok "17 после обновления: вопрос цифрами (2 — программа), строка «Совет: …» в меню; 80×24" || bad "17: совет pending" "$(gw_seq) | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '\n\n0\n' | lib t_gw17 2>&1 | strip)
gw_calls | grep -qx 'gw awg engine kernel --why person:menu-update' && ok "Enter — модуль ядра (gw awg engine kernel)" || bad "17: pending → модуль" "$(gw_seq)"
gwf awg.status.json '{"ok": true, "offer": {"show": true, "blocked": ["Вход AmneziaWG 3.1 …"], "pending": true}}'
: > "$CALLS/kaskadctl.log"
o=$(printf '0\n' | lib t_gw17 2>&1 | strip)
! gw_calls | grep -q 'awg engine' && ! printf '%s' "$o" | grep -q 'Выбор \[1\]' && ok "совет с линией 3.1 — без вопроса (объяснение — в «у»)" || bad "17: pending с 3.1" "$(gw_seq)"
rm -f "$FAKE_DIR/gw/awg.status.json"
# 17: безопасный MTU (§6.1) — строка в меню и вопрос цифрами при входе. Узкая карта: 1 — новые настройки с меньшим
# MTU (gw mtu reissue), Enter — оставить (gw mtu keep). S4 > 88 (сервер ниже 1232): 1 — перечень устройств (gw mtu s4),
# затем подтверждение (gw mtu s4 --yes); Enter — оставить. Решено — не спрашиваем
mtu_st() {   # $1 — offer (mtu|s4|null)
    gw_st true '{"awg2": {}}' 1 1
    python3 - "$KASKAD_DIR/state.json" "$1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
offer = None if sys.argv[2] == "null" else sys.argv[2]
d["gateway"]["mtu"] = {"nic": {"dev": "eth0", "mtu": 1400}, "budget": 1400,
                       "inbounds": {"awg2": {"server": 1248, "client": 1280, "s4": 60, "outer": 1388, "narrow": True,
                                             "low": False, "offer": offer, "new_client": 1248}}}
json.dump(d, open(sys.argv[1], "w"))
PY
}
mtu_st mtu
gwf mtu.json '{"ok": true, "nic": 1400, "budget": 1400, "ask": ["awg2"], "inbounds": [{"kind": "awg2", "server": 1248, "client": 1280, "narrow": true, "offer": "mtu", "text": "AmneziaWG 2.0: карта сервера 1400 уже, чем нужно клиентам с MTU 1280 при S4 = 60 (1420 байт). Выдайте новые настройки с MTU 1248 (перечень устройств покажу) или оставьте как есть."}], "legs": []}'
gwf mtu.reissue.json 'AmneziaWG 2.0: MTU клиентов 1280 → 1248. Выданные раньше настройки работают, но с прежним MTU — выдайте их заново (меню 17 → 2 → 1 или панель → «Пользователи»): Телефон (Мама).'
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n\n0\n' | K3_TERM_COLS=80 lib t_gw17 2>&1 | strip)
gw_calls | grep -qx 'gw mtu --json' && gw_calls | grep -qx 'gw mtu reissue awg2' && ! gw_calls | grep -q 'gw mtu keep' \
  && printf '%s' "$o" | flat | grep -q 'карта сервера 1400 уже, чем нужно клиентам с MTU 1280' \
  && printf '%s' "$o" | grep -q '1) Выдать новые настройки с меньшим MTU' && printf '%s' "$o" | grep -q '2) Оставить как есть' \
  && printf '%s' "$o" | flat | grep -q 'выдайте их заново (меню 17 → 2 → 1' \
  && printf '%s' "$o" | flat | grep -q 'MTU: протоколу AmneziaWG 2.0 нужно решение' \
  && [ "$(printf '%s' "$o" | k3_text_check)" = ok ] \
  && ok "17, узкая карта (§6.1): предупреждение, выбор цифрами, «1» → gw mtu reissue awg2 с перечнем устройств; строка в меню" \
  || bad "17: узкая карта — новые настройки" "$(gw_seq) | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '\n\n0\n' | lib t_gw17 2>&1 | strip)
gw_calls | grep -qx 'gw mtu keep awg2' && ! gw_calls | grep -q 'gw mtu reissue' \
  && ok "Enter — «Оставить как есть» (gw mtu keep awg2): клиентам ничего не перевыдаётся" || bad "17: узкая карта — оставить" "$(gw_seq)"
# S4 > 88: сервер ниже 1232 — новые параметры с меньшим S4 только после перечня устройств и подтверждения
mtu_st s4
gwf mtu.json '{"ok": true, "nic": 1500, "budget": 1400, "ask": ["awg2"], "inbounds": [{"kind": "awg2", "server": 1200, "client": 1280, "narrow": false, "low": true, "offer": "s4", "new_s4_max": 40, "text": "AmneziaWG 2.0: S4 = 120 — чтобы мобильные сети не тормозили, сервер уменьшил MTU до 1200; HTTP/3 через этот вход перейдёт на TCP. Полное лечение — новые настройки с S4 ≤ 40."}], "legs": []}'
mtu_dry() { printf '%s\n' 'AmneziaWG 2.0: новые параметры с S4 ≤ 40 вместо 120. Вход перезапустится, выданные раньше настройки перестанут подключаться — выдать заново нужно всем устройствам этого входа: Телефон (Мама).' '@K3:confirm kaskadctl gw mtu s4 awg2 --yes' > "$FAKE_DIR/gw/mtu.s4.once.json"; }   # K3_MARKS=1: подсказка — меткой
gwf mtu.s4.json 'AmneziaWG 2.0: S4 120 → 33, MTU сервера 1280. Выданные раньше настройки этого входа больше не подключатся — выдайте их заново (меню 17 → 2 → 1 или панель → «Пользователи»): Телефон (Мама).'
mtu_dry; : > "$CALLS/kaskadctl.log"
o=$(printf '1\n1\n\n0\n' | K3_TERM_COLS=80 lib t_gw17 2>&1 | strip)
gw_calls | grep -qx 'gw mtu s4 awg2' && gw_calls | grep -qx 'gw mtu s4 awg2 --yes' \
  && [ "$(gw_calls | grep -nx 'gw mtu s4 awg2' | cut -d: -f1)" -lt "$(gw_calls | grep -nx 'gw mtu s4 awg2 --yes' | cut -d: -f1)" ] \
  && printf '%s' "$o" | flat | grep -q 'сервер уменьшил MTU до 1200; HTTP/3 через этот вход перейдёт на TCP' \
  && printf '%s' "$o" | flat | grep -q '1) Выдать новые настройки с меньшим S4 (покажу, кому — всем устройствам этого протокола)' \
  && printf '%s' "$o" | flat | grep -q 'всем устройствам этого входа: Телефон (Мама)' && ! printf '%s' "$o" | grep -q 'Подтвердить: kaskadctl' \
  && printf '%s' "$o" | flat | grep -q 'S4 120 → 33' \
  && [ "$(printf '%s' "$o" | k3_text_check)" = ok ] \
  && ok "17, S4 > 88 (сервер ниже 1232): предупреждение, «1» → перечень устройств (gw mtu s4), подтверждение «1» → gw mtu s4 --yes" \
  || bad "17: новый S4" "$(gw_seq) | $o"
: > "$CALLS/kaskadctl.log"
mtu_dry; o=$(printf '1\n\n\n0\n' | lib t_gw17 2>&1 | strip)
gw_calls | grep -qx 'gw mtu s4 awg2' && ! gw_calls | grep -q -- '--yes' && ! gw_calls | grep -q 'gw mtu keep' \
  && ok "перечень показан, подтверждения нет (Enter — «Нет, пока не менять») — параметры не меняются" || bad "17: новый S4 без подтверждения" "$(gw_seq)"
: > "$CALLS/kaskadctl.log"
o=$(printf '\n\n0\n' | lib t_gw17 2>&1 | strip)
gw_calls | grep -qx 'gw mtu keep awg2' && ! gw_calls | grep -q 'gw mtu s4' \
  && ok "S4: Enter — «Оставить как есть» (gw mtu keep awg2)" || bad "17: S4 — оставить" "$(gw_seq)"
mtu_st null
: > "$CALLS/kaskadctl.log"
o=$(printf '0\n' | lib t_gw17 2>&1 | strip)
! gw_calls | grep -q 'gw mtu' && ! printf '%s' "$o" | flat | grep -q 'нужно решение' \
  && ok "решено «оставить как есть» — ни вопроса, ни строки" || bad "17: MTU решено" "$(gw_seq) | $o"
rm -f "$FAKE_DIR/gw/mtu.json" "$FAKE_DIR/gw/mtu.reissue.json" "$FAKE_DIR/gw/mtu.s4.json" "$FAKE_DIR/gw/mtu.s4.once.json"
# веб-панель
t_panel3() { scr k3_panel_menu; }
mkdir -p "$FAKE_DIR/panel"; printf '{"ok": true, "installed": false}\n' > "$FAKE_DIR/panel/status.json"; : > "$CALLS/kaskadctl.log"
o=$(printf '1\n\n0\n' | lib t_panel3 2>&1 | strip)
ctl_log | grep -qx 'panel install' && [ "$(printf '%s' "$o" | fit)" = "2 ok" ] && ok "18, панели нет: «1) Установить и запустить» → kaskadctl panel install; 80×24" || bad "панель: установка" "$(ctl_log) | $o"
printf '%s\n' '{"ok": true, "installed": true, "active": true, "public": {"on": true, "port": 41234}, "require_password": false,
  "password": false, "url": "https://203.0.113.10:41234/", "fingerprint": "AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89",
  "tunnel": "ssh -L 1980:127.0.0.1:1980 root@203.0.113.10"}' > "$FAKE_DIR/panel/status.json"; : > "$CALLS/kaskadctl.log"
o=$(printf '1\n\n3\n1\n\n4\n1\n\n0\n' | K3_TERM_COLS=80 lib t_panel3 2>&1 | strip)
ctl_log | grep -qx 'panel link' && ctl_log | grep -qx 'panel public off' && ctl_log | grep -qx 'panel require-password on' \
  && printf '%s' "$o" | grep -q 'ssh -L 1980:127.0.0.1:1980 root@203.0.113.10' && [ "$(printf '%s' "$o" | fit)" = "6 ok" ] \
  && printf '%s' "$o" | flat | grep -q 'Панель перестанет открываться из интернета (https://203.0.113.10:41234/) — только через SSH-туннель' \
  && printf '%s' "$o" | grep -q '^ *1) Да, выключить$' && printf '%s' "$o" | grep -q '^ *2) Нет, оставить включённым$' \
  && printf '%s' "$o" | grep -q '^ *1) Да, спрашивать пароль$' && printf '%s' "$o" | grep -q '^ *Доступ из интернета: включён\.$' \
  && printf '%s' "$o" | flat | grep -q 'Пароль в SSH-туннеле: не нужен, но пока открыт доступ из интернета — пароль спрашивается везде\.' \
  && printf '%s' "$o" | grep -q '^Пароль: не задан · нужен и в туннеле, пока открыт доступ из интернета$' \
  && ok "18: ссылка входа; доступ из интернета выкл и пароль в туннеле — после «1) Да», затем состояние (kaskadctl panel status); 80×24" \
  || bad "панель: меню" "$(ctl_log) | $(printf '%s' "$o" | fit) | $o"
# 3.3.2 T-M6-332b №13: Enter на вопросе (по умолчанию «2) Нет») — доступ и пароль не тронуты, «Не изменено. …»
: > "$CALLS/kaskadctl.log"; o=$(printf '3\n\n\n4\n\n\n0\n' | lib t_panel3 2>&1 | strip)
! ctl_log | grep -q 'panel public\|panel require-password' \
  && printf '%s' "$o" | grep -q '^ *Не изменено\. Доступ из интернета: включён\.$' \
  && printf '%s' "$o" | flat | grep -q 'Не изменено\. Пароль в SSH-туннеле: не нужен, но пока открыт доступ из интернета — пароль спрашивается везде\.' \
  && ok "18 → 3 / 4: Enter — «Нет»: доступ из интернета и пароль в туннеле не меняются, сказано, что осталось" \
  || bad "18: подтверждение выключения доступа" "$(ctl_log) | $o"
t_panel3en() { K3_LANG=en; scr k3_panel_menu; }
o=$(printf '3\n\n\n4\n\n\n0\n' | lib t_panel3en 2>&1 | strip)
printf '%s' "$o" | grep -q '^ *Not changed\. Access from the internet: on\.$' && printf '%s' "$o" | grep -q '^ *1) Yes, turn it off$' \
  && printf '%s' "$o" | flat | grep -q 'Not changed\. Password in the SSH tunnel: not required, but while access from the internet is on, it is asked everywhere\.' \
  && ok "18 → 3 / 4 по-английски: «1) Yes, turn it off», «Not changed. Access from the internet: on.»" || bad "18: EN подтверждение" "$o"
# M6 332c №2–3: 18 → 4 при включённом «пароль в туннеле» — вопрос цифрами (по умолчанию «Нет»); строка состояния одна
# (ответ kaskadctl — только при ошибке; здесь он нарочно расходится с состоянием панели, чтобы был виден дубль)
printf '{"ok": true, "installed": true, "active": true, "public": {"on": false}, "require_password": true, "password": true, "tunnel": "ssh -L 1980:127.0.0.1:1980 root@x"}\n' > "$FAKE_DIR/panel/status.json"
printf 'Пароль в SSH-туннеле: не нужен.\n' > "$FAKE_DIR/panel/require-password.json"
: > "$CALLS/kaskadctl.log"; o=$(printf '4\n\n\n0\n' | lib t_panel3 2>&1 | strip)
! ctl_log | grep -q 'panel require-password' && printf '%s' "$o" | grep -q '^ *1) Да, не спрашивать$' && printf '%s' "$o" | grep -q '^ *2) Нет, спрашивать пароль$' \
  && printf '%s' "$o" | flat | grep -q 'в панель войдёт любой, у кого есть SSH-доступ к серверу' \
  && printf '%s' "$o" | grep -q '^ *Не изменено\. Пароль в SSH-туннеле: нужен\.$' \
  && ok "18 → 4 при включённом: «Не спрашивать пароль?» 1) Да 2) Нет, Enter — «Не изменено. Пароль в SSH-туннеле: нужен.»" \
  || bad "18 → 4: снятие пароля без вопроса" "$(ctl_log) | $o"
: > "$CALLS/kaskadctl.log"; o=$(printf '4\n1\n\n0\n' | lib t_panel3 2>&1 | strip)
ctl_log | grep -qx 'panel require-password off' && [ "$(printf '%s\n' "$o" | grep -c '^ *Пароль в SSH-туннеле: [^→]*\.$')" = 1 ] \
  && ! printf '%s' "$o" | grep -q 'Пароль в SSH-туннеле: не нужен\.$' \
  && ok "18 → 4 → 1: kaskadctl panel require-password off, строка «Пароль в SSH-туннеле: …» одна (по состоянию панели)" \
  || bad "18 → 4: строка состояния дважды" "$(ctl_log) | $o"
printf '1\n' > "$FAKE_DIR/panel/require-password.rc"; printf 'Ошибка записи настроек панели.\n' > "$FAKE_DIR/panel/require-password.err"
: > "$CALLS/kaskadctl.log"; o=$(printf '4\n1\n\n0\n' | lib t_panel3 2>&1 | strip)
printf '%s' "$o" | grep -q '^  Ошибка записи настроек панели\.$' && ok "18 → 4: ошибка kaskadctl показана" || bad "18 → 4: ошибка скрыта" "$o"
rm -f "$FAKE_DIR/panel/require-password."*
o=$(printf '4\n\n\n0\n' | lib t_panel3en 2>&1 | strip)
printf '%s' "$o" | grep -q '^ *1) Yes, stop asking$' && printf '%s' "$o" | grep -q '^ *Not changed\. Password in the SSH tunnel: required\.$' \
  && ! printf '%s' "$o" | grep -q '[А-Яа-яЁё]' \
  && ok "18 → 4 по-английски: «1) Yes, stop asking», «Not changed. Password in the SSH tunnel: required.»" || bad "18 → 4 EN" "$o"
printf '{"ok": true, "installed": true, "active": true, "public": {"on": false}, "password": false, "tunnel": "ssh -L 1980:127.0.0.1:1980 root@x"}\n' > "$FAKE_DIR/panel/status.json"
: > "$CALLS/kaskadctl.log"; o=$(printf '3\n1\n\n0\n' | lib t_panel3 2>&1 | strip)
p=$(ctl_log | grep -nx 'panel password --generate' | cut -d: -f1); q=$(ctl_log | grep -nx 'panel public on' | cut -d: -f1)
[ -n "$p" ] && [ -n "$q" ] && [ "$p" -lt "$q" ] && ok "18: доступ из интернета без пароля — сначала пароль (--generate), потом public on" || bad "панель: public on" "$(ctl_log)"
# M6 332c №4: 18 → 3 при выключенном доступе, «Отмена» (Enter или «2») — «Не изменено. Доступ из интернета: выключен.»
for k in '' 2; do
    : > "$CALLS/kaskadctl.log"; o=$(printf '3\n%s\n\n0\n' "$k" | lib t_panel3 2>&1 | strip)
    ! ctl_log | grep -q 'panel public\|panel password' && printf '%s' "$o" | grep -q '^ *Не изменено\. Доступ из интернета: выключен\.$' \
      && ok "18 → 3 при выключенном доступе, ответ «${k:-Enter}» — «Не изменено. Доступ из интернета: выключен.»" || bad "18 → 3: отмена молча (${k:-Enter})" "$(ctl_log) | $o"
done
o=$(printf '3\n\n\n0\n' | lib t_panel3en 2>&1 | strip)
printf '%s' "$o" | grep -q '^ *Not changed\. Access from the internet: off\.$' && ok "18 → 3 EN: «Not changed. Access from the internet: off.»" || bad "18 → 3 EN отмена" "$o"
# 3.2.21 (T-N5 3.2.20, Н-3): в вопросе «Открыть доступ из интернета» есть 0 — назад, ничего не меняется
: > "$CALLS/kaskadctl.log"; o=$(printf '3\ny\n0\n\n0\n' | lib t_panel3 2>&1 | strip)
! ctl_log | grep -q 'panel public\|panel password' && printf '%s' "$o" | grep -q '^ 0) Назад$' \
  && printf '%s' "$o" | grep -q 'Нет такого пункта: введите номер от 1 до 2, 0 — назад (Enter — 2).' \
  && ok "18 → 3: «0) Назад», y — «Нет такого пункта … 0 — назад (Enter — 2)», 0 — доступ и пароль не тронуты" || bad "18 → 3: 0 — назад" "$(ctl_log) | $o"
: > "$CALLS/kaskadctl.log"; o=$(printf '5\n1\nУДАЛИТЬ\n\n' | lib t_panel3 2>&1 | strip)
ctl_log | grep -qx 'panel remove --purge' && ok "18 → 5: удаление панели, слово УДАЛИТЬ — и пароль с настройками (--purge)" || bad "панель: удаление" "$(ctl_log)"
# бот, мониторинг, удаление
t_bot3() { scr bot_menu; }
use_root menu3d; fake_ctl; printf 'BOT_TOKEN="123:abcdefSECRET"\nBOT_CHAT_ID="5"\nBOT_IMPL="python"\n' > "$KASKAD_DIR/config"
o=$(lib t_bot3 <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -q '^8) Вернуть прежнего бота (bash)$' && printf '%s' "$o" | grep -q 'Бот: на Python' && ! printf '%s' "$o" | grep -q 'abcdefSECRET' \
  && [ "$(printf '%s' "$o" | fit)" = "1 ok" ] && ok "8: «Бот: на Python», «8) Вернуть прежнего бота (bash)», токен скрыт; 80×24" || bad "меню бота" "$(printf '%s' "$o" | fit) $o"
t_mon3() { scr monitoring_menu; }
o=$(lib t_mon3 <<< "0" 2>&1 | strip); [ "$(printf '%s' "$o" | fit)" = "1 ok" ] && ok "7: мониторинг — 80×24" || bad "мониторинг" "$(printf '%s' "$o" | fit)"
t_un3() { scr full_uninstall; }
use_root menu3e; fake_ctl; gw_st true '{"wg": {}}' 1 1; uninst_fixture; mkdir -p "$KASKAD_ROOT/root/kaskad-issue"
o=$(printf '2\n\n' | K3_ISSUE_DIR="$KASKAD_ROOT/root/kaskad-issue" lib t_un3 2>&1 | strip | sed "s#$KASKAD_ROOT##g")
r=$(printf '%s' "$o" | fit 'Отменено.')
printf '%s' "$o" | grep -q 'веб-панель' && printf '%s' "$o" | grep -q 'Ускорение AmneziaWG' && [ "$r" = "1 ok" ] \
  && ok "15: экран удаления (панель, ускорение, все строки) — 80×24" || bad "экран удаления" "$r $o"
w=$(printf '%s' "$o" | tr '\n' ' ' | tr -s ' ')
printf '%s' "$w" | grep -q 'Пакеты, которые ставил Kaskad (если не нужны: apt remove openvpn)' \
  && ! printf '%s' "$w" | grep -o 'ставил Kaskad: [^•]*' | grep -q 'cmake\|build-essential\|libssl-dev' \
  && ok "15: «НЕ будет затронуто» называет пакеты протоколов, которые ставил шлюз (без средств сборки — о них отдельный вопрос)" || bad "пакеты шлюза" "$o"

echo "── 34b. подписки выходов (17 → 3): метка ключа, строка о старой подписке, обновление, ссылка в «Добавить ключ», 80×24 ──"
SUBURL2='https://sub2.example.org/s/SECRETTOKEN42'
use_root subs2; fake_ctl; gw_st true '{"wg": {}}' 1 1; : > "$CALLS/stdin.log"
gwf exits.json '{"ok": true, "exits": [{"id": "e-1111111111", "index": 1, "name": "Вена", "kind": "vless", "host": "198.51.100.31", "usable": true, "enabled": true, "state": "alive", "source": {"sub": "s-aaaaaa", "name": "подписка 1"}},
 {"id": "e-2222222222", "index": 2, "name": "Рига", "kind": "vless", "host": "198.51.100.32", "usable": true, "enabled": true, "state": "alive", "source": null}],
 "subs": [{"id": "s-aaaaaa", "n": 1, "name": "подписка 1", "auto": true, "stale": true, "error": "Сервер подписки ответил 404.", "text": "не обновлялась 30 ч: Сервер подписки ответил 404."},
          {"id": "s-bbbbbb", "n": 2, "name": "vpn.example.net", "auto": false, "stale": false, "error": "", "text": "обновлена 3 ч назад: ключей 4"}]}'
gwf exit-sub_list.json '{"ok": true, "subs": [{"id": "s-aaaaaa", "n": 1, "name": "подписка 1", "auto": true, "stale": true, "text": "не обновлялась 30 ч: Сервер подписки ответил 404."},
 {"id": "s-bbbbbb", "n": 2, "name": "vpn.example.net", "auto": false, "stale": false, "text": "обновлена 3 ч назад: ключей 4"}]}'
t_exits2() { K3_TEST_TTY=1; k3_gw_exits_menu; }
o=$(printf '0\n' | lib t_exits2 2>&1 | strip)
printf '%s' "$o" | grep -q '^ *1) Вена (VLESS, 198.51.100.31) \[подписка 1\] — работает$' \
  && printf '%s' "$o" | grep -q '^ *2) Рига (VLESS, 198.51.100.32) — работает$' \
  && printf '%s' "$o" | grep -q '! Подписка 1: не обновлялась 30 ч: Сервер подписки ответил 404\.' \
  && ! printf '%s' "$o" | grep -q 'Подписка vpn.example.net' \
  && printf '%s' "$o" | grep -q '^ 5) Подписки выходов' \
  && ok "17 → 3: ключ подписки помечен «[подписка 1]», ручной — без метки; не обновляется — строка под таблицей; пункт «Подписки выходов» последним" \
  || bad "17 → 3: подписки в таблице выходов" "$o"
t_subs2() { K3_TEST_TTY=1; k3_exit_subs_menu; }
o=$(printf '0\n' | lib t_subs2 2>&1 | strip)
printf '%s' "$o" | grep -q '^ *1) подписка 1 — не обновлялась 30 ч: Сервер подписки ответил 404\. (раз в сутки)$' \
  && printf '%s' "$o" | grep -q '^ *2) vpn.example.net — обновлена 3 ч назад: ключей 4 (вручную)$' \
  && printf '%s' "$o" | grep -q '^ 5) Удалить подписку$' \
  && ok "экран подписок: номер, имя, состояние, «раз в сутки» / «вручную»; пункты 1–5" || bad "экран подписок: список" "$o"
# обновить все: одна обновилась, другая нет (код 1) — обе строками; обновить одну — по номеру
gwf exit-sub_refresh.json '{"ok": false, "results": [{"id": "s-aaaaaa", "name": "подписка 1", "ok": false, "error": "404", "text": "Подписка 1 не обновилась: 404."},
 {"id": "s-bbbbbb", "name": "vpn.example.net", "ok": true, "added": 1, "removed": 0, "count": 4, "full": 0, "warnings": ["пул пересобран"], "text": "vpn.example.net: добавлено 1, убрано 0 (ключей в подписке: 4)."}], "text": "…"}'
gwf exit-sub_refresh.rc 1
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n\n3\n2\n\n0\n' | lib t_subs2 2>&1 | strip)
gw_calls | grep -qx 'gw exit-sub refresh --json' && gw_calls | grep -qx 'gw exit-sub refresh s-bbbbbb --json' \
  && printf '%s' "$o" | grep -q '✗ Подписка 1 не обновилась: 404\.' && printf '%s' "$o" | grep -q '✓ vpn.example.net: добавлено 1, убрано 0' \
  && printf '%s' "$o" | grep -q '! пул пересобран' && printf '%s' "$o" | grep -q 'Скачиваю подписки' \
  && ok "«Обновить все сейчас» и «Обновить одну» (номер 2 → s-bbbbbb): каждая строкой ✓/✗, предупреждения" || bad "подписки: обновление" "$(gw_seq) | $o"
# раз в сутки ⇄ вручную: у второй (вручную) — on, у первой (раз в сутки) — off; неверный номер — вопрос снова
: > "$CALLS/kaskadctl.log"
o=$(printf '4\n2\n\n4\n9\n1\n\n0\n' | lib t_subs2 2>&1 | strip)
gw_calls | grep -qx 'gw exit-sub auto s-bbbbbb on --json' && gw_calls | grep -qx 'gw exit-sub auto s-aaaaaa off --json' \
  && printf '%s' "$o" | grep -q 'Нет такого пункта: номер от 1 до 2' && printf '%s' "$o" | grep -q 'vpn.example.net: обновляется сама раз в сутки' \
  && ok "«Раз в сутки / вручную»: переключает выбранную по номеру (on/off), неверный номер — словами" || bad "подписки: раз в сутки" "$(gw_seq) | $o"
# удалить вместе с ключами: без --keep-keys; Enter — отмена (по умолчанию 0)
: > "$CALLS/kaskadctl.log"
o=$(printf '5\n1\n1\n\n5\n2\n\n0\n' | lib t_subs2 2>&1 | strip)
gw_calls | grep -qx 'gw exit-sub del s-aaaaaa --json' && [ "$(gw_calls | grep -c 'exit-sub del')" = 1 ] \
  && printf '%s' "$o" | grep -q '1) Удалить вместе с её ключами' && printf '%s' "$o" | grep -q '0) Отмена' && printf '%s' "$o" | grep -q 'Выбор \[0\]' \
  && ok "«Удалить подписку»: «1) вместе с ключами» — без --keep-keys; Enter — отмена (Выбор [0])" || bad "подписки: удаление с ключами" "$(gw_seq) | $o"
# добавить «раз в сутки» (Enter): без --manual; ошибка ядра — словами; адрес — только на вход ядра
gwf exit-sub_add.json '{"ok": false, "error": "Сервер подписки ответил 404."}'; gwf exit-sub_add.rc 2
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n%s\n\n\n0\n' "$SUBURL2" | lib t_subs2 2>&1 | strip)
gw_calls | grep -qx 'gw exit-sub add --json' && printf '%s' "$o" | grep -q 'Подписка не добавлена: Сервер подписки ответил 404\.' \
  && printf '%s' "$o" | flat | grep -q 'ваши ручные ключи не трогает; подписка goAmnezia — адрес all.vpn' \
  && printf '%s' "$o" | grep -q '1) Обновлять раз в сутки' && printf '%s' "$o" | grep -q '2) Только вручную' \
  && ! ctl_log | grep -q SECRETTOKEN42 && grep -q SECRETTOKEN42 "$CALLS/stdin.log" \
  && ok "«Добавить подписку»: Enter — раз в сутки (без --manual), отказ ядра словами, адрес не в argv" || bad "подписки: добавление раз в сутки" "$(gw_seq) | $o"
rm -f "$FAKE_DIR/gw/exit-sub_add.rc"; gwf exit-sub_add.json '{"ok": true, "text": "Подписка добавлена: sub2.example.org. sub2.example.org: добавлено 2, убрано 0 (ключей в подписке: 2)."}'
# «Добавить ключ выхода» со ссылкой https://: 1 — подпиской (exit-sub add), 2 — ключи один раз (как раньше)
t_addx() { K3_TEST_TTY=1; egress_add_key() { echo "ADD-KEY:$1"; }; k3_gw_add_exit; }
: > "$CALLS/kaskadctl.log"; : > "$CALLS/stdin.log"
o=$(printf '%s\n.\n\n\n' "$SUBURL2" | lib t_addx 2>&1 | strip)
gw_calls | grep -qx 'gw exit-sub add --json' && grep -q SECRETTOKEN42 "$CALLS/stdin.log" && ! ctl_log | grep -q SECRETTOKEN42 \
  && printf '%s' "$o" | grep -q '1) Добавить как подписку (обновлять раз в сутки)' && printf '%s' "$o" | grep -q 'Подписка добавлена: sub2.example.org' \
  && ! printf '%s' "$o" | grep -q 'ADD-KEY' \
  && ok "«Добавить ключ выхода»: ссылка https:// — вопрос; Enter — подпиской (exit-sub add, адрес на вход ядра)" || bad "ключ выхода: ссылка подпиской" "$(gw_seq) | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '%s\n.\n2\n\n' "$SUBURL2" | lib t_addx 2>&1 | strip)
printf '%s' "$o" | grep -q "ADD-KEY:$SUBURL2" && ! gw_calls | grep -q 'exit-sub' \
  && ok "«Добавить ключ выхода»: ссылка, «2) ключи один раз» — как раньше, в пул (без подписки)" || bad "ключ выхода: ссылка один раз" "$(gw_seq) | $o"
o=$(printf 'vless://KEY1\n.\n\n' | lib t_addx 2>&1 | strip)
printf '%s' "$o" | grep -q 'ADD-KEY:vless://KEY1' && ! printf '%s' "$o" | grep -q 'Это ссылка подписки' \
  && ok "«Добавить ключ выхода»: обычный ключ — без вопроса о подписке" || bad "ключ выхода: vless" "$o"
grep -q SECRETTOKEN42 "$KASKAD_ROOT/var/log/kaskad.log" 2>/dev/null && bad "адрес подписки в журнале kaskad.log" \
  || ok "адрес подписки не попал в журнал kaskad.log"
# экран подписок в 80×24: 20 подписок (предел ядра) с длинными доменами — строки в ширину, высота — сколько есть
python3 -c '
import json
subs = [{"id": "s-%06d" % i, "n": i, "name": "subscription-%02d.very-long-provider-domain.example.org" % i, "auto": i % 2 == 0,
         "stale": i == 3, "text": "не обновлялась 30 ч: Сервер подписки ответил 404." if i == 3 else "обновлена 3 ч назад: ключей 12"}
        for i in range(1, 4)]
print(json.dumps({"ok": True, "subs": subs}, ensure_ascii=False))' > "$FAKE_DIR/gw/exit-sub_list.json"
t_subs2s() { scr k3_exit_subs_menu; }
o=$(printf '0\n' | K3_TERM_COLS=80 lib t_subs2s 2>&1 | strip)
[ "$(printf '%s' "$o" | fit)" = "1 ok" ] && ok "экран подписок (3 длинных домена) — в 80×24" || bad "экран подписок: 80×24" "$(printf '%s' "$o" | fit) $o"

echo "── 35. токен бота не в argv curl (сисадмин №11): адрес с токеном и прокси — через stdin (curl -K -) ──"
use_root tok1; : > "$CALLS/curl_argv.log"; : > "$CALLS/curl_proxy.log"
t_tok() {
    BOT_TOKEN="123456:SECRETTOKEN"; BOT_PROXY="socks5h://user:PROXYPASS@127.0.0.1:1080"
    tg_send 5 "привет" >/dev/null; tg_answer_cb 77 "ok" >/dev/null
    BOT_PROXY=""; tg_api getMe '{}' >/dev/null
}
lib t_tok >/dev/null 2>&1
! grep -q 'SECRETTOKEN\|PROXYPASS' "$CALLS/curl_argv.log" && grep -q '^https://api.telegram.org/bot123456:SECRETTOKEN/sendMessage (key)$' "$CALLS/curl.log" \
  && grep -q '/answerCallbackQuery (key)$' "$CALLS/curl.log" && grep -q '/getMe (key)$' "$CALLS/curl.log" \
  && [ "$(cat "$CALLS/curl_proxy.log")" = "$(printf '%s\n%s' 'socks5h://user:PROXYPASS@127.0.0.1:1080' 'socks5h://user:PROXYPASS@127.0.0.1:1080')" ] \
  && grep -q 'привет' "$CALLS/tg.log" \
  && ok "tg_send, tg_answer_cb, tg_api: токен и прокси (с паролем) — только в stdin curl, в argv их нет; без прокси — строки proxy нет" \
  || bad "токен бота в argv" "$(cat "$CALLS/curl_argv.log") | $(cat "$CALLS/curl.log") | $(cat "$CALLS/curl_proxy.log")"
n=$(grep -v '^[[:space:]]*#' "$S" | grep -c 'telegram\.org/bot')
[ "$n" = 1 ] && grep -q "printf 'url = \"https://api.telegram.org/bot%s/%s\"" "$S" \
  && ok "адрес с токеном бота собирается только в tg_curl (бот на bash, его getUpdates, меню 8 → 2, проверка связи)" \
  || bad "адрес с токеном в обход tg_curl" "$(grep -n 'telegram\.org/bot' "$S")"

echo "── 36. журнал и проба потерь (сисадмин №13): logrotate, проба — маленький скрипт без разбора всей программы ──"
use_root lr1; mkdir -p "$KASKAD_ROOT/etc/logrotate.d"
bash "$S" --k3-post-update </dev/null >/dev/null 2>&1
F="$KASKAD_ROOT/etc/logrotate.d/kaskad"
grep -qx '/var/log/kaskad.log {' "$F" && grep -qx '    copytruncate' "$F" && grep -qx '    maxsize 5M' "$F" && grep -qx '    su root root' "$F" \
  && [ "$(perm "$F")" = 644 ] && ok "--k3-post-update: /etc/logrotate.d/kaskad (неделя или 5 МБ, 4 архива, copytruncate, su root root), 0644" \
  || bad "logrotate" "$(cat "$F" 2>&1)"
m1=$(cksum < "$F"); : > "$KASKAD_ROOT/var/log/kaskad.log"; bash "$S" --k3-post-update </dev/null >/dev/null 2>&1
[ "$(cksum < "$F")" = "$m1" ] && ! grep -q LOGROTATE "$KASKAD_ROOT/var/log/kaskad.log" && ok "повтор — файл ротации не переписывается" || bad "logrotate переписан"
use_root lr2; bash "$S" --k3-post-update </dev/null >/dev/null 2>&1
[ ! -e "$KASKAD_ROOT/etc/logrotate.d" ] && ok "нет /etc/logrotate.d (logrotate не стоит) — ничего не создаётся" || bad "создан logrotate.d"
# Ежедневная фоновая проверка обновления (3.3.0): таймер kaskad-update-check — его ставит --k3-post-update; служба
# скачивает только начало install.sh ветки (-r 0-…), ключ GitHub — в файле --config, итог — update.json; настройки и
# программа не меняются
use_root upd1
bash "$S" --k3-post-update </dev/null >/dev/null 2>&1
UU="$KASKAD_ROOT/etc/systemd/system/kaskad-update-check"
grep -qx "ExecStart=$K3_OPT/bin/gokaskad --update-check-bg" "$UU.service" && grep -qx 'Type=oneshot' "$UU.service" \
  && grep -qx 'OnCalendar=daily' "$UU.timer" && grep -qx 'Persistent=true' "$UU.timer" && grep -qx 'WantedBy=timers.target' "$UU.timer" \
  && grep -q 'enable --no-reload --now --no-block kaskad-update-check.timer' "$CALLS/systemctl.log" \
  && [ "$(perm "$UU.timer")" = 644 ] \
  && ok "--k3-post-update: таймер kaskad-update-check (раз в сутки, Persistent) и служба на копии в /opt — включён" \
  || bad "таймер проверки обновления" "$(cat "$UU.service" "$UU.timer" 2>&1) | $(cat "$CALLS/systemctl.log")"
: > "$CALLS/systemctl.log"; m1=$(cksum < "$UU.timer"); bash "$S" --k3-post-update </dev/null >/dev/null 2>&1
[ "$(cksum < "$UU.timer")" = "$m1" ] && ! grep -q 'daemon-reload' "$CALLS/systemctl.log" \
  && ok "повтор --k3-post-update — юниты таймера не переписываются, daemon-reload нет" || bad "таймер переписан" "$(cat "$CALLS/systemctl.log")"
grep -q '^    --update-check-bg) init_config >/dev/null 2>&1; k3_update_peek; exit 0 ;;' "$S" \
  && ok "режим --update-check-bg (служба таймера) — в case" || bad "нет режима --update-check-bg"
t_peek() { k3_update_peek; echo "RC=$?"; }
UJ() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["latest"], d["branch"])' "$KASKAD_DIR/update.json" 2>/dev/null; }
use_root upd2; C="$KASKAD_ROOT/usr/local/bin/gokaskad"; cp "$S" "$C"
printf 'GITHUB_PAT="ghp_PEEKSECRET123"\nKASKAD_BRANCH="test"\n' > "$KASKAD_DIR/config"; cp "$KASKAD_DIR/config" "$W/cfg.before"
mk_version "$NEWER" "$FAKE_DIR/gh/test.sh"; : > "$CALLS/curl_argv.log"
o=$(lib t_peek 2>&1 | strip)
[ "$(UJ)" = "$NEWER test" ] && printf '%s' "$o" | grep -q 'RC=0' && grep -q -- '-r 0-16383' "$CALLS/curl_argv.log" \
  && ! grep -q PEEKSECRET "$CALLS/curl_argv.log" && grep -q 'kaskad-pro/test/install.sh (key)' "$CALLS/curl.log" \
  && cmp -s "$S" "$C" && cmp -s "$KASKAD_DIR/config" "$W/cfg.before" && [ "$(perm "$KASKAD_DIR/update.json")" = 600 ] \
  && ok "--update-check-bg: начало install.sh ветки test (-r 0-16383), ключ — в --config (не в argv), update.json: $NEWER; программа и настройки не тронуты" \
  || bad "фоновая проверка обновления" "$o | $(UJ) | $(cat "$CALLS/curl_argv.log")"
use_root upd3; mk_version 2.13 "$FAKE_DIR/gh/main.sh"; mk_version 3.1.0 "$FAKE_DIR/gh/test.sh"; mk_version "$NEWER" "$FAKE_DIR/gh/dev.sh"
printf 'GITHUB_PAT=""\n' > "$KASKAD_DIR/config"
o=$(lib t_peek 2>&1 | strip)
[ "$(UJ)" = "$NEWER dev" ] && ! grep -q KASKAD_BRANCH "$KASKAD_DIR/config" \
  && [ "$(grep -o 'kaskad-pro/[a-z0-9]*/install.sh' "$CALLS/curl.log" | cut -d/ -f2 | tr '\n' ' ')" = "main test dev " ] \
  && ok "--update-check-bg без ветки: main 2.13 → test 3.1.0 → dev $NEWER в update.json, ветка в настройки не пишется" \
  || bad "фоновая проверка: цепочка" "$o | $(UJ) | $(cat "$CALLS/curl.log")"
use_root upd4; touch "$FAKE_DIR/offline"; printf 'GITHUB_PAT=""\nKASKAD_BRANCH="test"\n' > "$KASKAD_DIR/config"
o=$(lib t_peek 2>&1 | strip)
[ ! -e "$KASKAD_DIR/update.json" ] && printf '%s' "$o" | grep -q 'RC=0' && [ -z "$(printf '%s' "$o" | grep -v '^RC=0$')" ] \
  && ok "--update-check-bg без сети: тихо, код 0, update.json не пишется" || bad "фоновая проверка без сети" "$o"
# проба: один замер сгенерированным скриптом (поддельный ss: одно соединение к узлу ключа)
use_root probe1
printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> "$CALLS/ss.log"\necho "ESTAB 0 0 10.0.0.2:43210 198.51.100.7:443 bytes_sent:5000 bytes_retrans:50"\n' > "$W/bin/ss"
chmod +x "$W/bin/ss"
mkdir -p "$KASKAD_DIR/egress"; printf 'vless://0e2a@198.51.100.7:443?security=reality#x\n' > "$KASKAD_DIR/egress/upstream.txt"
t_probe() { egress_probe_install >/dev/null 2>&1; }
lib t_probe
P="$KASKAD_DIR/egress/probe.sh"
sed '$d' "$P" > "$W/probe1.sh"; echo 'egress_probe_sample' >> "$W/probe1.sh"
bash "$W/probe1.sh" >/dev/null 2>&1; bash "$W/probe1.sh" >/dev/null 2>&1
grep -qx -- '-tinO dst 198.51.100.7' "$CALLS/ss.log" && [ "$(wc -l < "$KASKAD_DIR/egress/loss.log" | tr -d ' ')" = 2 ] \
  && ! grep -q 'K3_PAYLOAD\|show_menu' "$P" && [ "$(grep -qx 'disable --no-reload --now kaskad-egress-probe.timer' "$CALLS/systemctl.log"; echo $?)" = 1 ] \
  && grep -qx 'enable --no-reload --now --no-block kaskad-egress-probe.service' "$CALLS/systemctl.log" \
  && ok "проба: скрипт $(wc -c < "$P" | tr -d ' ') Б (без программы и её кода), замер тот же (ss -tinO dst <узел ключа> → loss.log), служба включена" \
  || bad "проба потерь" "$(cat "$CALLS/ss.log" 2>&1) | $(cat "$KASKAD_DIR/egress/loss.log" 2>&1) | $(cat "$CALLS/systemctl.log")"
rm -f "$W/bin/ss"
t_probe_rm() { egress_probe_remove; }
lib t_probe_rm >/dev/null 2>&1
[ ! -e "$P" ] && [ ! -e "$KASKAD_ROOT/etc/systemd/system/kaskad-egress-probe.service" ] \
  && grep -qx 'stop kaskad-egress-probe.timer kaskad-egress-probe.service' "$CALLS/systemctl.log" && grep -qx 'disable kaskad-egress-probe.service' "$CALLS/systemctl.log" \
  && ok "пул опустел: служба пробы, её скрипт и журнал потерь удалены" || bad "удаление пробы" "$(cat "$CALLS/systemctl.log")"

# ── Ядро AmneziaWG: в плане needs_core — мастер сам скачивает ядро (egress_ensure_awg) и проверяет порты заново ──
CORE_PLAN='{"ok": true, "inbounds": [
 {"kind": "awg1", "title": "AmneziaWG 1.5", "available": true, "installed": false, "supported": false, "why": "нет ядра AmneziaWG 3.1", "warning": "", "needs_core": true, "port": {"field": "port", "protos": ["udp"], "port": 31001, "default": 31001, "conflicts": [], "free": null}, "fixed": []},
 {"kind": "awg2", "title": "AmneziaWG 2.0", "available": true, "installed": false, "supported": false, "why": "нет ядра AmneziaWG 3.1", "warning": "", "needs_core": true, "port": {"field": "port", "protos": ["udp"], "port": 41002, "default": 41002, "conflicts": [], "free": null}, "fixed": []},
 {"kind": "awg3", "title": "AmneziaWG 3.1", "available": true, "installed": false, "supported": false, "why": "нет ядра AmneziaWG 3.1", "warning": "", "needs_core": true, "port": {"field": "port", "protos": ["udp"], "port": 51003, "default": 51003, "conflicts": [], "free": null}, "fixed": []}]}'
use_root gw-core; fake_ctl; engine_nft; gwf inbound_plan.json "$CORE_PLAN"
t_core() { K3_GW_PICK="awg"; k3_gw_awg_core_ensure() { echo "CORE-FETCH"; return 0; }; k3_gw_ports_step; echo "RC=$?"; }
o=$(printf '0\n' | lib t_core 2>&1 | strip)
[ "$(gw_calls | grep -c '^gw inbound plan awg1 awg2 awg3 --json$')" = 2 ] && printf '%s' "$o" | grep -q 'CORE-FETCH' \
  && ok "мастер: AmneziaWG без ядра — ядро скачивается, порты проверяются заново" || bad "ядро AmneziaWG в мастере" "$o | $(gw_seq)"
use_root gw-core2; fake_ctl; engine_nft; gwf inbound_plan.json "$CORE_PLAN"
t_core_fail() { K3_GW_PICK="awg"; k3_gw_awg_core_ensure() { echo "CORE-FAIL"; return 1; }; k3_gw_ports_step; echo "RC=$?"; }
o=$(printf '0\n' | lib t_core_fail 2>&1 | strip)
[ "$(gw_calls | grep -c '^gw inbound plan awg1 awg2 awg3 --json$')" = 1 ] && printf '%s' "$o" | grep -q 'CORE-FAIL' \
  && printf '%s' "$o" | grep -q 'пропущу: здесь не работает$' \
  && [ "$(printf '%s\n' "$o" | flat | grep -o 'AmneziaWG 1.5, AmneziaWG 2.0, AmneziaWG 3.1 здесь не работают: нет ядра AmneziaWG 3.1' | wc -l | tr -d ' ')" = 1 ] \
  && ok "мастер: ядро не скачалось — AmneziaWG пропущен с причиной, второй проверки нет" || bad "ядро AmneziaWG: отказ" "$o | $(gw_seq)"
use_root gw-core3; fake_ctl; engine_nft; gwf inbound_plan.json "$CORE_PLAN"
t_core_card() { k3_gw_awg_core_ensure() { echo "CORE-FETCH"; return 0; }; k3_gw_proto_install awg2; echo "RC=$?"; }
o=$(printf '0\n' | lib t_core_card 2>&1 | strip)
[ "$(gw_calls | grep -c '^gw inbound plan awg2 --json$')" = 2 ] && printf '%s' "$o" | grep -q 'CORE-FETCH' \
  && ok "«Протоколы → установить» AmneziaWG без ядра — ядро скачивается, проверка повторяется один раз" || bad "ядро AmneziaWG в карточке" "$o | $(gw_seq)"

# ── ключ GitHub из README-команды (GH_TOKEN) запоминается для меню 11 и ядра AmneziaWG; свой GITHUB_PAT не трогается ──
use_root gh-token
t_tok() { init_config >/dev/null 2>&1; GH_TOKEN="ghp_TESTONLY0123456789abcdefABCDEF" k3_remember_gh_token; grep -c '^GITHUB_PAT="ghp_TESTONLY0123456789abcdefABCDEF"$' "$KASKAD_CONF"; }
o=$(lib t_tok 2>&1 | tail -1)
t_tok2() { init_config >/dev/null 2>&1; save_config_val GITHUB_PAT "ghp_OWNKEY00000000000000000000000"; GITHUB_PAT="ghp_OWNKEY00000000000000000000000"; GH_TOKEN="ghp_TESTONLY0123456789abcdefABCDEF" k3_remember_gh_token; grep -c '^GITHUB_PAT="ghp_OWNKEY00000000000000000000000"$' "$KASKAD_CONF"; }
o2=$(lib t_tok2 2>&1 | tail -1)
[ "$o" = 1 ] && [ "$o2" = 1 ] && ok "ключ из GH_TOKEN запомнен в настройках; свой GITHUB_PAT не перезаписан" || bad "запоминание ключа GitHub" "$o / $o2"

echo "── 37. меню 17 (тестер T-G1): строки в окно, слова не рвутся (B1-3); «Состояние» по разделам (B1-5); протокол снова — прежние порт и ключи (F-2) ──"
# Окна 80×24 и 100×36: каждая строка не шире окна (терминал не рвёт слово), экран не выше окна (fit: FIT_W, FIT_H).
# Причины, подписи и предупреждения — длинные, как на стенде G1.
use_root wide; fake_ctl; engine_nft; gw_st false '{}' 0 0
gwf inbound_plan.json '{"ok": true, "inbounds": [
 {"kind": "wg", "title": "WireGuard", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 51820, "default": 51820, "conflicts": [{"kind": "forward", "detail": "его занимает проброс на 198.51.100.7:51820 (Keenetic на даче у мамы)"}], "free": 38211}, "fixed": []},
 {"kind": "awg1", "title": "AmneziaWG 1.5", "available": true, "installed": false, "supported": false, "why": "нет ядра AmneziaWG и ключа GitHub, чтобы его скачать (меню 8 → 6)", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 40205, "default": 40205, "conflicts": [], "free": null}, "fixed": []},
 {"kind": "awg2", "title": "AmneziaWG 2.0", "available": true, "installed": false, "supported": false, "why": "нет ядра AmneziaWG и ключа GitHub, чтобы его скачать (меню 8 → 6)", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 52913, "default": 52913, "conflicts": [], "free": null}, "fixed": []},
 {"kind": "awg3", "title": "AmneziaWG 3.1", "available": true, "installed": false, "supported": false, "why": "нет ядра AmneziaWG и ключа GitHub, чтобы его скачать (меню 8 → 6)", "warning": "", "port": {"field": "port", "protos": ["udp"], "port": 57726, "default": 57726, "conflicts": [], "free": null}, "fixed": []},
 {"kind": "l2tp", "title": "L2TP/IPsec", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": null, "fixed": [{"proto": "udp", "port": 1701, "conflicts": []}, {"proto": "udp", "port": 500, "conflicts": []}, {"proto": "udp", "port": 4500, "conflicts": [{"kind": "listener", "detail": "его слушает charon-systemd (strongSwan, поставленный другим установщиком)"}]}]},
 {"kind": "sstp", "title": "SSTP", "available": true, "installed": false, "supported": true, "why": "", "warning": "SSTP и L2TP/IPsec работают в одной службе accel-ppp: установка перезапустит её, и подключения L2TP оборвутся на несколько секунд", "kept": true, "port": {"field": "port", "protos": ["tcp"], "port": 4433, "default": 4433, "conflicts": [], "free": null}, "fixed": []},
 {"kind": "pptp", "title": "PPTP", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "port": null, "fixed": [{"proto": "tcp", "port": 1723, "conflicts": []}]}]}'
gwf exits.json '{"ok": true, "exits": [
 {"id": "e-1111111111", "index": 1, "name": "k3-exit-vless", "kind": "vless", "host": "203.0.113.141", "usable": false, "enabled": true, "alive": false, "detail": "ещё не проверялся", "text": "ещё не проверялся", "state": "unknown"},
 {"id": "e-2222222222", "index": 2, "name": "amneziawg", "kind": "awg", "version": "2.0", "host": "203.0.113.141", "usable": false, "enabled": true, "detail": "нет интерфейса", "state": "unknown"},
 {"id": "e-3333333333", "index": 3, "name": "amneziawg", "kind": "awg", "version": "3.1", "host": "203.0.113.141", "usable": false, "enabled": true, "detail": "нет интерфейса", "state": "unknown"}]}'
t_w_intro() { scr k3_gw_wiz_intro; }
t_w_pick() { K3_GW_CHECKED="Сервер подходит, движок пробросов — nftables."; K3_GW_PICK="wg awg l2tp ikev2 sstp openvpn ocserv socks5 vless pptp"; scr k3_gw_pick_protocols; }
t_w_ports() { K3_GW_PICK="wg awg l2tp sstp pptp"; k3_gw_awg_core_ensure() { return 1; }; scr k3_gw_ports_step; }
t_w_exits() { egress_awg_runs() { return 1; }; scr k3_gw_exits_step; }
for win in 80x24 100x36; do
    c="${win%x*}"; r="${win#*x}"
    res=""
    for t in t_w_intro t_w_pick t_w_ports t_w_exits; do
        o=$(printf '0\n0\n0\n' | K3_TERM_COLS="$c" K3_TERM_ROWS="$r" lib "$t" 2>&1 | strip)
        f=$(printf '%s' "$o" | FIT_W="$c" FIT_H="$r" fit)
        [ "${f#* }" = ok ] || res+=" $t:$f"
    done
    [ -z "$res" ] && ok "мастер $win: шаги 1–4 — ни одна строка не шире окна, экран не выше окна" || bad "мастер $win не влезает" "$res"
done
# 3.2.21 (критик, С-1): «Нет такого пункта» встаёт на место пустой строки — шаг 2 в 80×24 не выше 24 строк и после ошибки
o=$(printf 'y\nzz\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_w_pick 2>&1 | strip)
f=$(printf '%s' "$o" | FIT_W=80 FIT_H=24 fit)
[ "$f" = "3 ok" ] && [ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта: номер от 1 до 10, Enter — дальше, 0 — назад.')" = 2 ] \
  && ok "шаг 2, 80×24: после неверного ввода сообщение на месте пустой строки — экран не выше 24 строк" || bad "шаг 2: экран после ошибки" "$f | $o"
o=$(printf '0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_w_pick 2>&1 | strip)
printf '%s' "$o" | grep -q 'до 5\.1\.0$' && printf '%s' "$o" | grep -q 'его шифрование давно взломано$' \
  && ! printf '%s' "$o" | grep -qE 'Keene$|взломан$' && ok "шаг 2, 80×24: подписи протоколов — целыми словами («Keenetic», «взломано» не рвутся)" || bad "шаг 2: подписи" "$o"
o=$(printf '0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_w_ports 2>&1 | strip)
printf '%s' "$o" | grep -q 'AmneziaWG 1.5 *40205/udp *✗ пропущу: здесь не работает$' \
  && [ "$(printf '%s\n' "$o" | flat | grep -o 'здесь не работают: нет ядра AmneziaWG и ключа GitHub, чтобы его скачать (меню 8 → 6)' | wc -l | tr -d ' ')" = 1 ] \
  && printf '%s' "$o" | flat | grep -q 'AmneziaWG 1.5, AmneziaWG 2.0, AmneziaWG 3.1 здесь не работают' \
  && printf '%s' "$o" | grep -q 'SSTP *4433/tcp *✓ свободен — прежний порт$' && printf '%s' "$o" | grep -q 'L2TP/IPsec .*✗ пропущу: порт занят$' \
  && ok "шаг 3: причина «нет ядра» — одна на три AmneziaWG, под таблицей; строки протоколов короткие; удалённый — «прежний порт»" || bad "шаг 3: причины" "$o"
o=$(printf '0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_w_exits 2>&1 | strip)
printf '%s' "$o" | flat | grep -q 'не заработает: нет ядра AmneziaWG (нужен ключ GitHub — меню 8 → 6, затем снова этот шаг)' \
  && ! printf '%s' "$o" | grep -qE 'G$|Git$' && ok "шаг 4, 80×24: подписи ключей переносятся по словам («ключ GitHub» целиком)" || bad "шаг 4: подписи ключей" "$o"
# 17 → 1: удалённый протокол — «настройки сохранены»; его карточка; удаление — честный текст (F-2)
gwf inbound_list.json "$(gw_list "wg l2tp" | jq -c '(.inbounds[] | select(.kind == "awg1") | .kept) = {"ports": ["40850/udp"]}
    | (.inbounds[] | select(.kind == "awg1") | .devices) = 2 | (.inbounds[] | select(.kind == "pptp") | .kept) = {"ports": []}')"
t_w_proto() { scr k3_gw_protocols; }
o=$(printf '2\n0\n6\n0\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_w_proto 2>&1 | strip)
r=$(printf '%s' "$o" | fit)
printf '%s' "$o" | grep -q ' 2) AmneziaWG 1.5 *- *не установлен (настройки сохранены)$' && printf '%s' "$o" | grep -q ' 3) AmneziaWG 2.0 *- *не установлен$' \
  && printf '%s' "$o" | flat | grep -q 'Порт 40850/udp и ключи сервера сохранились с прошлой установки: поставите снова — выданные устройствам (2) настройки заработают без новой выдачи' \
  && printf '%s' "$o" | flat | grep -q 'Ключи сервера сохранились с прошлой установки' && [ "${r#* }" = ok ] \
  && ok "17 → 1: удалённый протокол — «не установлен (настройки сохранены)», в карточке — порт и ключи ждут новой установки; 80×24" || bad "протоколы: сохранённые" "$r | $o"
t_w_rm() { K3_TEST_TTY=1; k3_gw_proto_remove awg1 "AmneziaWG 1.5" 2; echo "RC=$?"; }
: > "$CALLS/kaskadctl.log"; o=$(printf '2\n' | K3_TERM_COLS=80 lib t_w_rm 2>&1 | strip)
printf '%s' "$o" | flat | grep -q 'Ключи сервера и порт сохранятся: поставите AmneziaWG 1.5 снова — выданные устройствам настройки заработают без новой выдачи' \
  && printf '%s' "$o" | flat | grep -q 'устройства на этом протоколе (2) не подключатся, пока протокол не поставят снова' \
  && ! printf '%s' "$o" | grep -q 'ключи сервера уйдут' && [ -z "$(gw_calls)" ] && printf '%s' "$o" | grep -q 'RC=1' \
  && [ "$(printf '%s' "$o" | python3 -c 'import sys; print(max(len(l) for l in sys.stdin.read().splitlines()))')" -le 80 ] \
  && ok "удалить протокол: окно говорит правду — порт и ключи сохранятся, выданное заработает после новой установки; «2» — ничего" || bad "удаление протокола: текст" "$o"
# установка удалённого: прежний порт свободен — предлагается он, ядро вернуло прежнее; занят — свободный и список устройств
gwf inbound_plan.json '{"ok": true, "inbounds": [{"kind": "awg1", "title": "AmneziaWG 1.5", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "kept": true, "packages": [], "port": {"field": "port", "protos": ["udp"], "port": 40850, "default": 40850, "conflicts": [], "free": null}, "fixed": []}]}'
gwf inbound_install.awg1.json '{"ok": true, "kind": "awg1", "record": {"enabled": true, "port": 40850}, "notes": [], "previous": {"restored": true, "changed": [], "devices": [{"id": "d-00000001", "name": "Телефон", "user_name": "Мама"}], "text": ""}}'
t_w_inst() { K3_TEST_TTY=1; k3_gw_proto_install awg1; echo "RC=$? NEW=${K3_GW_NEWKEYS// /}"; }
: > "$CALLS/kaskadctl.log"; o=$(printf '\n' | lib t_w_inst 2>&1 | strip)
gw_calls | grep -qx 'gw inbound install awg1 --port 40850 --json' && printf '%s' "$o" | grep -q 'Прежний порт 40850: с ним выданные устройствам настройки заработают снова' \
  && printf '%s' "$o" | grep -q 'Прежние порт и ключи вернулись — выданные настройки снова работают' && printf '%s' "$o" | grep -q 'RC=0 NEW=$' \
  && ok "поставить удалённый: прежний порт предлагается сам (Enter), ядро вернуло прежние — «выданные настройки снова работают»" || bad "установка удалённого" "$(gw_seq) | $o"
gwf inbound_plan.json '{"ok": true, "inbounds": [{"kind": "awg1", "title": "AmneziaWG 1.5", "available": true, "installed": false, "supported": true, "why": "", "warning": "", "kept": true, "packages": [], "port": {"field": "port", "protos": ["udp"], "port": 40850, "default": 40850, "conflicts": [{"kind": "forward", "detail": "его занимает проброс на 198.51.100.7:40850"}], "free": 32941}, "fixed": []}]}'
gwf inbound_install.awg1.json '{"ok": true, "kind": "awg1", "record": {"enabled": true, "port": 32941}, "notes": [], "previous": {"restored": false, "changed": ["порт 40850 → 32941"], "devices": [{"id": "d-00000001", "name": "Телефон", "user_name": "Мама"}], "text": "x"}}'
gwf inbound_install.awg1.err '@K3:warn AmneziaWG 1.5: изменились настройки сервера (порт 40850 → 32941) — выданные раньше настройки не подключатся. Выдайте их заново (меню 17 → 2 → 1 или панель → «Пользователи»): Телефон (Мама).'
: > "$CALLS/kaskadctl.log"; o=$(printf '\n' | K3_TERM_COLS=80 lib t_w_inst 2>&1 | strip)
gw_calls | grep -qx 'gw inbound install awg1 --port 32941 --json' && printf '%s' "$o" | grep -q 'Это прежний порт: с новым настройки устройств придётся выдать заново' \
  && printf '%s' "$o" | flat | grep -q '! AmneziaWG 1.5: изменились настройки сервера (порт 40850 → 32941) — выданные раньше настройки не подключатся. Выдайте их заново (меню 17 → 2 → 1 или панель → «Пользователи»): Телефон (Мама).' \
  && ! printf '%s' "$o" | grep -q 'снова работают' && printf '%s' "$o" | grep -q 'RC=0 NEW=awg1' \
  && [ "$(printf '%s' "$o" | python3 -c 'import sys; print(max(len(l) for l in sys.stdin.read().splitlines()))')" -le 80 ] \
  && ok "прежний порт заняли: свободный взамен, сказано заранее; после установки — каким устройствам выдать заново (по словам, 80 столбцов)" || bad "установка удалённого: порт занят" "$(gw_seq) | $o"
rm -f "$FAKE_DIR/gw/inbound_install.awg1.err"
# мастер, шаг 6: люди остались — выданное работает, если все протоколы вернулись с прежними портом и ключами
printf '{\n  "engine": "nft",\n  "gateway": {"enabled": true, "inbounds": {"wg": {}, "awg1": {}}, "users": [{"id": "u-00000001", "name": "Мама"}], "devices": [{"id": "d-00000001", "user": "u-00000001", "access": ["awg1"]}]}\n}\n' > "$KASKAD_DIR/state.json"
t_w_first() { K3_GW_NEWKEYS="$1"; k3_gw_first_device; }
o=$(printf '\n' | lib t_w_first "" 2>&1 | strip); o2=$(printf '\n' | lib t_w_first " awg1" 2>&1 | strip); o3=$(printf '\n' | lib t_w_first " wg" 2>&1 | strip)
printf '%s' "$o" | grep -q 'Прежние порты и ключи протоколов вернулись — выданные раньше настройки снова' \
  && printf '%s' "$o2" | grep -q 'Выданные раньше настройки (устройств: 1) больше не работают — выдайте новые:' \
  && printf '%s' "$o3" | grep -q 'снова' && ok "шаг 6: выданное «не работает» — только у устройств с протоколом, поставленным с новыми ключами" || bad "шаг 6 мастера" "$o | $o2 | $o3"
# «Состояние»: итог в две строки и проблемы; разделы — каждый в окно 80×24; длинный раздел — частями
gwf status.json '{"ok": true, "enabled": true, "engine": "nft", "net": {"base": "10.77.0.0/16", "dns": "10.77.0.1"}, "host": "203.0.113.241", "users": 3, "devices": 9,
 "inbounds": [
  {"kind": "wg", "title": "WireGuard", "available": true, "installed": true, "enabled": true, "ports": ["51820/udp"], "running": true, "sessions": 1, "devices": 3, "warnings": []},
  {"kind": "awg1", "title": "AmneziaWG 1.5", "available": true, "installed": true, "enabled": true, "ports": ["40850/udp"], "running": true, "sessions": 0, "devices": 2, "warnings": [], "core": "kernel"},
  {"kind": "awg2", "title": "AmneziaWG 2.0", "available": true, "installed": true, "enabled": true, "ports": ["44274/udp"], "running": true, "sessions": 0, "devices": 1, "warnings": [], "core": "kernel"},
  {"kind": "awg3", "title": "AmneziaWG 3.1", "available": true, "installed": true, "enabled": true, "ports": ["49842/udp"], "running": true, "sessions": 2, "devices": 4, "warnings": [], "core": "userspace"},
  {"kind": "l2tp", "title": "L2TP/IPsec", "available": true, "installed": true, "enabled": true, "ports": ["1701/udp", "500/udp", "4500/udp"], "running": true, "sessions": 0, "devices": 2, "warnings": ["L2TP без IPsec включён: трафик роутеров без IPsec идёт без шифрования — выключите, когда они не нужны"]},
  {"kind": "pptp", "title": "PPTP", "available": true, "installed": true, "enabled": true, "ports": ["1723/tcp"], "running": true, "sessions": 0, "devices": 1, "warnings": []},
  {"kind": "sstp", "title": "SSTP", "available": true, "installed": true, "enabled": true, "ports": ["4433/tcp"], "running": true, "sessions": 0, "devices": 1, "warnings": []},
  {"kind": "ikev2", "title": "IKEv2", "available": true, "installed": true, "enabled": true, "ports": ["500/udp", "4500/udp"], "running": true, "sessions": 0, "devices": 1, "warnings": ["центр сертификации IKEv2 старого образца — без ограничения адресом узла (доверие к нему распространяется на любые сайты): сменить — kaskadctl gw pki rotate ikev2, затем выдать устройствам IKEv2 настройки заново"]},
  {"kind": "openvpn", "title": "OpenVPN", "available": true, "installed": true, "enabled": true, "ports": ["1194/udp"], "running": true, "sessions": 0, "devices": 1, "warnings": []},
  {"kind": "ocserv", "title": "OpenConnect", "available": true, "installed": true, "enabled": true, "ports": ["4443/tcp", "4443/udp"], "running": true, "sessions": 0, "devices": 1, "warnings": []},
  {"kind": "socks5", "title": "SOCKS5", "available": true, "installed": true, "enabled": true, "ports": ["1080/tcp", "1080/udp"], "running": false, "sessions": 0, "devices": 1, "warnings": []}],
 "exits": [
  {"id": "e-1111111111", "name": "k3-exit-vless", "kind": "vless", "host": "203.0.113.141", "usable": true, "enabled": true, "state": "alive"},
  {"id": "e-2222222222", "name": "amneziawg", "kind": "awg", "version": "2.0", "host": "203.0.113.141", "usable": true, "enabled": true, "state": "alive"},
  {"id": "e-3333333333", "name": "amneziawg", "kind": "awg", "version": "3.1", "host": "203.0.113.141", "usable": false, "enabled": true, "state": "dead", "detail": "сервер выхода не отвечает"}],
 "dns": {"active": true, "filter_aaaa": true}, "xray": {"active": true}, "collect": {"active": true},
 "rules": {"ok": true, "lists": {"label": "geoip 202610061012", "fallback": false}, "line": "списки geoip 202610061012, geosite 202610061012; сетей России 35609 (диапазонов 12000); в таблице: загружены; доменов: напрямую 1200, через выход 300, блок 50000; nftset: есть; по умолчанию: Россия напрямую"},
 "problems": [{"kind": "gw_inbound", "detail": "вход SOCKS5 не работает", "repaired": false}],
 "accel": {"enabled": true, "loaded": true}}'
gw_st true '{"wg": {}}' 9 3
t_w_status() { scr k3_gw_status_show; }
: > "$CALLS/kaskadctl.log"; o=$(printf '\n\n\n\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_w_status 2>&1 | strip)
r=$(printf '%s' "$o" | fit)
[ "$(gw_seq)" = "gw status --json;" ] && [ "$r" = "5 ok" ] \
  && printf '%s' "$o" | grep -q '^Протоколы: 10 из 11 работают · выходы: 2 из 3 · DNS и правила — в порядке$' \
  && printf '%s' "$o" | grep -q '^Проблемы (2):$' && printf '%s' "$o" | grep -q '^  • вход SOCKS5 не работает$' \
  && printf '%s' "$o" | grep -q '^  • выход «amneziawg» не работает: сервер выхода не отвечает$' \
  && printf '%s' "$o" | grep -q 'состояние — протоколы (1 из 3)' && printf '%s' "$o" | grep -q '^AmneziaWG 1.5 *40850/udp *работает, ядро · устройств 2, сессий 0$' \
  && printf '%s' "$o" | grep -q '^L2TP/IPsec *1701, 500, 4500/udp *работает' && printf '%s' "$o" | grep -q '^AmneziaWG 3.1 *49842/udp *работает, программа · устройств 4, сессий 2$' && printf '%s' "$o" | grep -q 'Ускорение AmneziaWG: включено, модуль ядра загружен' \
  && printf '%s' "$o" | grep -q 'состояние — выходы (2 из 3)' && printf '%s' "$o" | flat | grep -q 'Маршрут по умолчанию: пул выходов; если выходы недоступны — как у группы (блок)' \
  && printf '%s' "$o" | grep -q 'amneziawg (AmneziaWG 3.1, 203.0.113.141)' \
  && printf '%s' "$o" | grep -q 'состояние — DNS и правила (3 из 3)' && printf '%s' "$o" | grep -q 'DNS шлюза: работает, ответы AAAA убираются' \
  && printf '%s' "$o" | grep -q '^  • сетей России 35609 (диапазонов 12000)$' \
  && ok "«Состояние», 80×24: итог «Протоколы: 10 из 11 работают · выходы: 2 из 3 · DNS и правила — в порядке», проблемы под ним; три раздела, каждый в окно" \
  || bad "«Состояние»: экраны" "$r | $(gw_seq) | $o"
o=$(printf '0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_w_status 2>&1 | strip)
[ "$(printf '%s' "$o" | fit)" = "2 ok" ] && ! printf '%s' "$o" | grep -q '1 из 3' && ok "«Состояние»: «0» на итоге — назад, разделы не показываются" || bad "«Состояние»: 0" "$o"
gwf status.json "$(jq -c '.problems += [{"detail": "DNS шлюза (kaskad-dns.service) не работает"}, {"detail": "UFW закрыл порты входов: 51820/udp (wg)"}, {"detail": "сети России не загружены в таблицу"}]' "$FAKE_DIR/gw/status.json")"
o=$(printf '\n\n\n\n\n\n\n\n' | K3_TERM_COLS=80 K3_TERM_ROWS=12 lib t_w_status 2>&1 | strip)
r=$(printf '%s' "$o" | FIT_H=12 fit)
[ "${r#* }" = ok ] && [ "${r%% *}" -gt 5 ] && [ "$(printf '%s\n' "$o" | grep -c 'состояние — протоколы (1 из 3)')" -ge 2 ] \
  && printf '%s' "$o" | grep -q '… дальше — в разделе «DNS и правила» (Enter)' \
  && ok "«Состояние» в окне 80×12: длинный раздел — частями («Enter — дальше»), проблемы на итоге урезаны со ссылкой на раздел ($r)" || bad "«Состояние»: окно 12 строк" "$r | $o"
gwf status.json '{"ok": true, "enabled": false, "inbounds": [{"kind": "wg", "installed": true}], "exits": [], "problems": []}'
o=$(printf '0\n' | lib t_w_status 2>&1 | strip)
printf '%s' "$o" | grep -q 'Шлюз выключен: устройства не подключаются. Включить — 17 → 6' && printf '%s' "$o" | grep -q 'Протоколов: 1 — заработают, когда шлюз включат' \
  && ok "«Состояние» выключенного шлюза — словами, без «не работает»" || bad "«Состояние»: шлюз выключен" "$o"
# помощник: строка шире окна — по словам, с отступом продолжения; влезает — как есть; тире и стрелка строку не начинают
t_w_fold() { K3_TERM_COLS=30 k3_gw_fold "  " "${RED}раз два три четыре пять шесть семь — восемь → девять десять${NC}"; K3_TERM_COLS=30 k3_gw_fold "" "короткая"; }
o=$(lib t_w_fold 2>&1 | strip)
[ "$o" = "$(printf 'раз два три четыре пять шесть\n  семь — восемь → девять\n  десять\nкороткая')" ] && ok "k3_gw_fold: по словам в ширину окна − 1, продолжение с отступом, «—» и «→» не в начале строки" || bad "k3_gw_fold" "$o"

echo "── 38. мелочи тестера T-G2b: меню 16 в 80×24, ядро AmneziaWG без ключа, отпечаток, сборка accel-ppp ──"
# самая длинная строка (столбцы терминала: эмодзи — два)
maxw() { python3 -c '
import sys, unicodedata
w = lambda s: sum(0 if unicodedata.combining(c) else (2 if unicodedata.east_asian_width(c) in "WF" else 1) for c in s)
print(max([w(l) for l in sys.stdin.read().replace("\f", "\n").split("\n")] or [0]))'; }
# N-2: меню 16 — «для специалиста» отдельным пунктом 5, первый экран влезает в 80×24; неполадка — одной строкой
NET_STATUS='движок: nftables
поддержка nftables: да
система: Debian GNU/Linux 12 (bookworm)
nft: 1.0.6
ядро: 6.1.0-26-amd64
iptables: nf_tables
таблица kaskad: есть, пробросов 0, совпадает с настройками
правило совместимости: есть
kaskad-net.service: включена, работает
kaskad-net-watch.service: по таймеру
kaskad-net-watch.timer: включена, работает'
NET_JSON='{"ok": true, "engine": "nft", "table": {"present": true, "forwards": 0, "in_sync": true}, "services": {"kaskad-net.service": {"enabled": true, "active": true}, "kaskad-net-watch.service": {"enabled": true, "active": false}, "kaskad-net-watch.timer": {"enabled": true, "active": true}}}'
use_root g2b-m16; fake_ctl; prog_ready; engine_nft
printf '%s\n' "$NET_STATUS" > "$FAKE_DIR/net_status"; printf '%s\n' "$NET_JSON" > "$FAKE_DIR/net_status.json"
o=$(printf '5\n\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_m16s 2>&1 | strip)
r=$(printf '%s' "$o" | fit); s1=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==2')
[ "$r" = "3 ok" ] && printf '%s' "$s1" | grep -q '^5) Для специалиста — состояние движка подробно' \
  && ! printf '%s' "$s1" | grep -q 'kaskad-net\|Не в порядке' && printf '%s' "$o" | grep -q '^kaskad-net-watch.timer: включена, работает$' \
  && printf '%s' "$s1" | grep -q '^Здесь ничего менять не нужно — 0 назад' \
  && ok "меню 16 (N-2): первый экран без 11 строк «для специалиста» — в 80×24 и с шапкой; подробности — пункт 5, тоже в окно" || bad "меню 16: пункт 5" "$r | $o"
printf '%s\n' "$NET_JSON" | jq -c '.table.in_sync = false | .services["kaskad-net.service"].enabled = false' > "$FAKE_DIR/net_status.json"
o=$(printf '0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_m16s 2>&1 | strip)
printf '%s' "$o" | flat | grep -q '! Не в порядке: таблица kaskad не совпадает с настройками; служба kaskad-net.service не включена — подробности в пункте 5\.' \
  && [ "$(printf '%s' "$o" | fit)" = "1 ok" ] && ok "меню 16: движок не в порядке — одна строка на первом экране со ссылкой на пункт 5" || bad "меню 16: неполадка" "$o"
# 16 → 2 со шлюзом, без netfilter-persistent: всё по словам; текст kaskadctl — тоже
use_root g2b-rb; fake_ctl; gw_st true '{"wg": {}}' 1 1
printf '%s\n' 'Внимание: Сохранённые правила (/etc/iptables/rules.v4) не обновлены: нет netfilter-persistent. После перезагрузки пробросов может не быть.' > "$FAKE_DIR/rollback_out"
mv "$W/bin/netfilter-persistent" "$W/bin/netfilter-persistent.off"
o=$(printf '1\n' | K3_TERM_COLS=80 lib t_rb 2>&1 | strip)
mv "$W/bin/netfilter-persistent.off" "$W/bin/netfilter-persistent"
[ "$(printf '%s' "$o" | maxw)" -le 79 ] && printf '%s' "$o" | grep -q 'RC=0' \
  && printf '%s' "$o" | flat | grep -q '(нет netfilter-persistent, а iptables-persistent на Debian 12+ и Ubuntu 24.04+ удаляет ufw — Kaskad его не ставит)' \
  && printf '%s' "$o" | flat | grep -q 'Перед возвратом iptables его нужно выключить или удалить' \
  && printf '%s' "$o" | flat | grep -q '1) Выключить шлюз и вернуть iptables — протоколы, пользователи и устройства сохранятся' \
  && printf '%s' "$o" | flat | grep -q 'не обновлены: нет netfilter-persistent\. После' \
  && printf '%s' "$o" | flat | grep -q 'После перезагрузки пробросы пропадут — надёжнее движок nftables: меню 16 → 1' \
  && ok "16 → 2 и итог возврата (N-2): строки по словам в 80 столбцов — свои и kaskadctl" || bad "16 → 2: переносы" "$(printf '%s' "$o" | maxw) $o"
# 16 → 3 и 16 → 1: список копий и план переезда — по словам
use_root g2b-bk; fake_ctl; engine_nft
printf '%s\n' '/var/backups/kaskad/20261007T024204Z-k3-3.2.13  перед переездом: Kaskad 3 3.2.13 → 3.2.13' > "$FAKE_DIR/backups_txt"
t_bk() { k3_backups_show; }
o=$(K3_TERM_COLS=80 lib t_bk 2>&1 | strip)
[ "$(printf '%s\n' "$o" | tail -n +2 | maxw)" -le 80 ] && printf '%s' "$o" | flat | grep -q 'перед переездом: Kaskad 3 3.2.13 → 3.2.13' \
  && ! printf '%s' "$o" | grep -q '^ *→' && ok "16 → 3: строки копий — по словам, «→» не начинает строку" || bad "16 → 3" "$o"
use_root g2b-mg; fake_ctl; prog_ready
printf '%s\n' 'Останется как было: правила UFW, правила Docker и всё, что Kaskad не ставил (их не трогаю, и они не мешают переезду).' > "$FAKE_DIR/plan_txt"
t_mg() { k3_migrate_interactive; }
o=$(printf '2\n' | K3_TERM_COLS=80 lib t_mg 2>&1 | strip)
[ "$(printf '%s' "$o" | maxw)" -le 79 ] && printf '%s' "$o" | flat | grep -q 'и они не мешают переезду' && ! ctl_log | grep -q 'migrate apply' \
  && ok "16 → 1: план переезда — по словам; «2» — ничего не делает" || bad "16 → 1" "$o"

# N-3: «Установить» AmneziaWG без ядра и ключа — коротко, без путей на экране (они — в журнале); в карточке — заранее
NOKEY_PLAN="$(printf '%s' "$CORE_PLAN" | jq -c '.inbounds |= map(.why = "нет ядра AmneziaWG и ключа GitHub, чтобы его скачать (меню 8 → 6)" | .needs_key = true)')"
use_root g2b-awg; fake_ctl; engine_nft; gwf inbound_plan.json "$NOKEY_PLAN"
t_awg_nokey() { egress_awg_pair_ok() { return 1; }; egress_awg_runs() { return 1; }; awg_go_bin() { return 1; }
                KASKAD_AWG_ROOTS=""; k3_gw_proto_install awg1; echo "RC=$?"; }
o=$(K3_TERM_COLS=80 lib t_awg_nokey </dev/null 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -v 'Подробности — в журнале' | maxw)" -le 79 ] && printf '%s' "$o" | grep -q 'Подробности — в журнале .*kaskad.log' && printf '%s' "$o" | grep -q 'RC=1' \
  && printf '%s' "$o" | flat | grep -q 'Нет ядра AmneziaWG и ключа GitHub, чтобы его скачать\. Задайте ключ: меню 8 → 6 или панель «Настройки → Ключ GitHub»\.' \
  && ! printf '%s' "$o" | grep -q 'bin/\|/etc/kaskad\|здесь не работает\|пропущу' \
  && grep -q 'AWG core: не удалось подготовить ядро для .*bin/amneziawg-go-linux-.*egress/awg/amneziawg-go' "$KASKAD_ROOT/var/log/kaskad.log" \
  && [ "$(gw_calls | grep -c '^gw inbound plan awg1')" = 1 ] \
  && ok "N-3: без ядра и ключа — одна причина и что сделать (меню 8 → 6 или панель), пути файлов — в журнале, не на экране" || bad "N-3: установка AmneziaWG без ключа" "$o"
gwf inbound_list.json "$(gw_list "wg")"
t_awg_card() { K3_TEST_TTY=1; egress_awg_pair_ok() { return 1; }; scr k3_gw_proto_card awg1; }
o=$(printf '0\n' | K3_TERM_COLS=80 lib t_awg_card 2>&1 | strip)
printf '%s' "$o" | flat | grep -q 'Сейчас установка не выйдет: на сервере нет ядра AmneziaWG, а скачать его без ключа GitHub нельзя\. Задайте ключ — меню 8 → 6 или панель «Настройки → Ключ GitHub»' \
  && [ "$(printf '%s' "$o" | fit)" = "1 ok" ] && printf '%s' "$o" | grep -q '^ 1) Установить$' \
  && ok "N-3: карточка AmneziaWG без ядра и ключа — до «Установить» сказано, что не выйдет и почему" || bad "N-3: карточка" "$o"
gwf inbound_plan.json "$CORE_PLAN"; gwf inbound_list.json "$(gw_list "wg")"
o=$(printf '0\n' | K3_TERM_COLS=80 lib t_awg_card 2>&1 | strip)
! printf '%s' "$o" | grep -q 'установка не выйдет' && ok "N-3: ключ GitHub есть (needs_key нет) — карточка не пугает" || bad "N-3: карточка с ключом" "$o"

# N-4: отпечаток сертификата — двумя строками по 16 байт, перенос по двоеточию
FP="EC:EF:EC:24:7B:3A:91:47:7A:A2:2B:26:D5:85:E5:21:CC:04:78:53:40:05:23:8E:F9:7A:64:19:08:58:F4:4C"
t_fp() { k3_pg_start; k3_gw_issue_item "$1" 0; }
o=$(K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_fp "{\"items\": [{\"format\": \"fields\", \"title\": \"SSTP — данные для подключения\", \"body\": [[\"Сервер\", \"203.0.113.10:4433\"], [\"Отпечаток сертификата (SHA-256)\", \"$FP\"]], \"notes\": []}]}" 2>&1 | strip)
printf '%s\n' "$o" | grep -qx '  Отпечаток сертификата (SHA-256):' && printf '%s\n' "$o" | grep -qx '    EC:EF:EC:24:7B:3A:91:47:7A:A2:2B:26:D5:85:E5:21:' \
  && printf '%s\n' "$o" | grep -qx '    CC:04:78:53:40:05:23:8E:F9:7A:64:19:08:58:F4:4C' && printf '%s\n' "$o" | grep -qx '  Сервер: 203.0.113.10:4433' \
  && [ "$(printf '%s' "$o" | maxw)" -le 80 ] && ok "N-4: отпечаток SHA-256 — две строки по 16 байт, перенос по двоеточию, байт не рвётся" || bad "N-4: отпечаток" "$o"

# N-6: «собирается из исходников — до двух минут» — только когда план ядра говорит, что сборка будет; одна на accel-ppp
use_root g2b-build; fake_ctl; engine_nft
for k in l2tp sstp pptp; do gwf "inbound_install.$k.json" "{\"ok\": true, \"kind\": \"$k\", \"record\": {\"enabled\": true}, \"notes\": []}"; done
t_build() { K3_TEST_TTY=1; k3_gw_build_kinds "$1"; k3_gw_install_one l2tp "" 1 3; k3_gw_install_one sstp "" 2 3; k3_gw_install_one pptp "" 3 3; }
o=$(lib t_build '{"inbounds": [{"kind": "l2tp", "build": "accel-ppp 1.14.0 из исходников"}, {"kind": "sstp", "build": "accel-ppp 1.14.0 из исходников"}, {"kind": "pptp", "build": "x"}]}' 2>&1 | strip)
o2=$(lib t_build '{"inbounds": [{"kind": "l2tp", "build": ""}, {"kind": "sstp", "build": ""}, {"kind": "pptp"}]}' 2>&1 | strip)
[ "$(printf '%s' "$o" | grep -c 'до двух минут')" = 1 ] && printf '%s' "$o" | grep -q '\[1/3\] L2TP/IPsec (accel-ppp собирается из исходников — до двух минут) … ✓' \
  && printf '%s' "$o" | grep -q '\[2/3\] SSTP … ✓' && ! printf '%s' "$o2" | grep -q 'до двух минут' && printf '%s' "$o2" | grep -q '\[1/3\] L2TP/IPsec … ✓' \
  && ok "N-6: «до двух минут» — только когда сборка будет, и один раз (accel-ppp общий у L2TP, SSTP, PPTP); сборка есть — без обещания" || bad "N-6: сборка accel-ppp" "$o | $o2"

echo "── 39. пункт 19 «Домен и сайт» (4.0-B): главное меню 80×24, сквозной путь, отказы, SSTP, удаление, меню 15 и 18 ──"
# Ответы поддельного kaskadctl site — $FAKE_DIR/site/<ключ>.* (см. подделку). STATUS и DNS — по контракту §4.1.
sitef() { mkdir -p "$FAKE_DIR/site"; printf '%s\n' "$2" > "$FAKE_DIR/site/$1"; }
DNS_OK='{"state": "ok", "a": ["203.0.113.10"], "aaaa": [], "own": ["203.0.113.10"], "cloudflare": false, "aaaa_foreign": false, "ready": true, "checked_at": "2026-10-07T12:00:00Z", "advice": null}'
DNS_NONE='{"state": "none", "a": [], "aaaa": [], "own": ["203.0.113.10"], "cloudflare": false, "aaaa_foreign": false, "ready": false, "checked_at": "2026-10-07T12:00:00Z", "advice": "У домена vpn.example.com нет A-записи — Let'"'"'s Encrypt проверку не пройдёт. В настройках DNS домена добавьте A-запись со значением 203.0.113.10 (адрес этого сервера). Запись начинает работать через несколько минут, иногда через час."}'
DNS_FAR='{"state": "elsewhere", "a": ["198.51.100.7"], "aaaa": [], "own": ["203.0.113.10"], "cloudflare": false, "aaaa_foreign": false, "ready": false, "checked_at": "2026-10-07T12:00:00Z", "advice": "Домен vpn.example.com указывает на 198.51.100.7, а адреса этого сервера — 203.0.113.10: Let'"'"'s Encrypt проверку не пройдёт. Поправьте A-запись в настройках DNS домена."}'
CERT_LE='{"kind": "letsencrypt", "names": ["vpn.example.com"], "not_after": "2026-12-30T10:00:00Z", "days_left": 83, "staging": false, "issuer": "R11"}'
CERT_SELF='{"kind": "selfsigned", "names": ["203.0.113.10"], "not_after": "2036-10-01T00:00:00Z", "days_left": 3646, "staging": false, "issuer": "Kaskad"}'
# site_st <CERT> <DNS> <use.sstp> <host_is_domain> [use.panel] [use.ocserv] [unique devices] [ocserv devices]
site_st() {
    printf '{"ok": true, "domain": "vpn.example.com", "domain_unicode": "vpn.example.com", "mode": "domain", "cert": %s, "dns": %s, "port80": {"free": true, "who": null}, "use": {"panel": %s, "pages": %s, "sstp": %s, "ocserv": %s}, "use_impact": {"sstp": 3, "ocserv": %s}, "use_impact_total": %s, "host_is_domain": %s, "renew": {"by": null}, "deps": {"certbot": false}, "email": ""}' \
        "$1" "$2" "${5:-false}" "${5:-false}" "$3" "${6:-false}" "${8:-0}" "${7:-3}" "$4"
}
NO_SITE='{"ok": true, "domain": null, "domain_unicode": null, "mode": "off", "cert": null, "dns": null, "port80": {"free": true, "who": null}, "use": {"panel": false, "pages": false, "sstp": false, "ocserv": false}, "use_impact": {"sstp": 0, "ocserv": 0}, "host_is_domain": false, "email": ""}'
AFF='[{"device": "d-00000001", "name": "Телефон", "kind": "sstp", "user_name": "Мама"}, {"device": "d-00000002", "name": "Ноутбук", "kind": "sstp", "user_name": "Мама"}, {"device": "d-00000003", "name": "Планшет", "kind": "sstp", "user_name": "Мама"}]'
F2_ON='SSTP: адрес сервера в настройках меняется на vpn.example.com, сертификат — на Let'"'"'s Encrypt — выданные раньше настройки не подключатся. Выдайте их заново (меню 17 → 2 → 1 или панель → «Пользователи»): Телефон (Мама), Ноутбук (Мама), Планшет (Мама).'
F2_OFF='SSTP: вход вернулся на самоподписанный сертификат и адрес узла — выданные раньше настройки не подключатся. Выдайте их заново (меню 17 → 2 → 1 или панель → «Пользователи»): Телефон (Мама), Ноутбук (Мама), Планшет (Мама).'
t_site() { scr k3_site_menu; }
# экраны с бегущей строкой ожидания: \r возвращает каретку — на экране остаётся то, что после последнего \r
cr() { sed 's/.*\r//'; }
site_state() {   # state.json: движок nft, домен $1 (пусто — нет), сертификат Let's Encrypt $2 (true|false)
    if [ -n "$1" ]; then
        printf '{\n  "engine": "nft",\n  "site": {"domain": "%s", "mode": "domain", "cert": "%s"},\n  "schema": 1\n}\n' "$1" "$([ "${2:-false}" = true ] && echo letsencrypt)" > "$KASKAD_DIR/state.json"
    else engine_nft; fi
}
le_copy() {   # копия сертификата для потребителей: $1 дней до конца, $2 — имя (по умолчанию vpn.example.com)
    rm -rf "$KASKAD_DIR/site/tls/.pair-menu"; mkdir -p "$KASKAD_DIR/site/tls/.pair-menu"
    openssl req -x509 -newkey rsa:2048 -nodes -keyout "$KASKAD_DIR/site/tls/.pair-menu/privkey.pem" -out "$KASKAD_DIR/site/tls/.pair-menu/fullchain.pem" \
        -days "$1" -subj "/CN=${2:-vpn.example.com}" >/dev/null 2>&1
    rm -f "$KASKAD_DIR/site/tls/current"; ln -s .pair-menu "$KASKAD_DIR/site/tls/current"
}
day_after() { python3 -c 'import datetime, sys; print((datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=int(sys.argv[1]))).strftime("%d.%m"))' "$1"; }

# главное меню: строка «Домен:» шапки — из снимка ядра (kaskadctl summary: state.json и копия сертификата, свой разбор)
use_root site-m1; fake_ctl; site_state ""
o=$(lib t_menu3 <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -q ' 19) Домен и сайт$' && ! printf '%s' "$o" | grep -q '^Домен:' \
  && printf '%s' "$o" | grep -q '^──────' && [ "$(printf '%s' "$o" | fit)" = "1 ok" ] \
  && ok "главное меню: домена нет — строки «Домен:» нет, пункт «19) Домен и сайт» прежний; 80×24" || bad "строка 19" "$(printf '%s' "$o" | fit) $o"
use_root site-m2; engine_nft            # ядра нет: красная строка проблемы, черта на месте
o=$(lib t_menu3 <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -q '^✗ Ядро Kaskad 3 недоступно' && printf '%s' "$o" | grep -q '^──────' && printf '%s' "$o" | grep -q ' 19) Домен и сайт$' \
  && [ "$(printf '%s' "$o" | fit)" = "1 ok" ] \
  && ok "главное меню без ядра: красная строка «✗ Ядро Kaskad 3 недоступно» и совет — всё ещё 80×24" || bad "главное меню без ядра" "$(printf '%s' "$o" | fit) $o"
use_root site-m3; fake_ctl; site_state vpn.example.com true; le_copy 60
o=$(lib t_menu3 <<< "0" 2>&1); exp=$(day_after 60)
printf '%s' "$o" | strip | grep -qx "Домен: vpn.example.com · сертификат до $exp" && [ -z "$(ctl_log | grep -vx 'summary --json')" ] \
  && [ "$(printf '%s' "$o" | strip | fit)" = "1 ok" ] \
  && ok "домен с сертификатом: «Домен: vpn.example.com · сертификат до ${exp}» — из одного снимка ядра (копия /etc/kaskad/site/tls)" \
  || bad "строка 19 с сертификатом" "$(printf '%s' "$o" | strip | grep '^Домен') | $(ctl_log)"
le_copy 10; o=$(lib t_menu3 <<< "0" 2>&1)
printf '%s' "$o" | grep '^.*Домен:' | grep -qF "$(printf '\033[1;33mсертификат до %s' "$(day_after 10)")" \
  && printf '%s' "$o" | strip | grep -q "^✗ Сертификат домена vpn.example.com истекает" \
  && ok "до конца меньше 21 дня — срок жёлтым и проблема «✗ Сертификат домена … истекает» (Let's Encrypt не продлился)" \
  || bad "строка 19: скоро истекает" "$(printf '%s' "$o" | strip | grep -A3 'Домен')"
rm -rf "$KASKAD_DIR/site"; o=$(lib t_menu3 <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qx 'Домен: vpn.example.com · сертификата нет' && ok "домен без сертификата — «сертификата нет»" || bad "строка 19 без сертификата" "$(printf '%s' "$o" | grep '^Домен')"
idn=$(python3 -c 'print("впн.пример.рф".encode("idna").decode())'); site_state "$idn"
o=$(lib t_menu3 <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qx 'Домен: впн.пример.рф · сертификата нет' && ok "домен кириллицей ($idn) — как его вводили" || bad "строка 19: IDN" "$(printf '%s' "$o" | grep '^Домен')"
site_state "a-very-long-subdomain-name-for-the-test.example.com"; le_copy 60 a-very-long-subdomain-name-for-the-test.example.com
o=$(lib t_menu3 <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -q '^Домен: a-very-long-subdomain-name-for-th… · сертификат до' && [ "$(printf '%s' "$o" | fit)" = "1 ok" ] \
  && ok "длинный домен — с «…», строка в 80 столбцов" || bad "строка 19: длинный домен" "$(printf '%s' "$o" | fit) $(printf '%s' "$o" | grep '^Домен')"

# сквозной путь: домен → DNS не готов → ждать (site dns --wait 300, «прошло N с») → выпуск → SSTP (3 устройства) → адрес
use_root site-w; fake_ctl; gw_st true '{"sstp": {}}' 3 1
sitef domain.json "{\"ok\": true, \"site\": $(site_st "$CERT_SELF" "$DNS_NONE" false false), \"dns\": $DNS_NONE, \"affected\": []}"
sitef domain.status "$(site_st "$CERT_SELF" "$DNS_NONE" false false)"
sitef dns_wait.json "$DNS_OK"; sitef dns_wait.sleep 1; sitef dns_wait.status "$(site_st "$CERT_SELF" "$DNS_OK" false false)"
sitef cert_issue.json '{"ok": true, "issued": true, "kept": false, "cert": '"$CERT_LE"', "warnings": ["Почта для Let'"'"'s Encrypt не задана — письма о сроке сертификата приходить не будут (задать: kaskadctl site email <почта>)."], "consumers": [{"name": "panel", "action": "reload"}, {"name": "pages", "action": "reload"}], "text": "Сертификат Let'"'"'s Encrypt для vpn.example.com выпущен."}'
sitef cert_issue.status "$(site_st "$CERT_LE" "$DNS_OK" false false true)"
sitef use_sstp_on_dry-run.json "{\"ok\": true, \"use\": {\"sstp\": true, \"ocserv\": false}, \"affected\": $AFF, \"text\": \"$F2_ON\", \"warnings\": [], \"dry_run\": true}"
sitef use_sstp_on.json "{\"ok\": true, \"use\": {\"sstp\": true, \"ocserv\": false}, \"affected\": $AFF, \"text\": \"$F2_ON\", \"warnings\": []}"
sitef use_sstp_on.status "$(site_st "$CERT_LE" "$DNS_OK" true false true)"
gwf host.json '{"ok": true, "host": "vpn.example.com", "auto": false, "apply": null}'
raw=$(printf 'vpn.example.com\n\n\n\n1\n\n1\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip)
o=$(printf '%s' "$raw" | cr); w=$(printf '%s' "$o" | flat)
n() { ctl_log | grep -nxF -- "$1" | head -1 | cut -d: -f1; }
a1=$(n 'site domain vpn.example.com --json'); a2=$(n 'site dns --wait 300 --json'); a3=$(n 'site cert issue --json')
a4=$(n 'site use sstp on --dry-run --json'); a5=$(n 'site use sstp on --json'); a6=$(n 'gw host vpn.example.com --json')
[ -n "$a1" ] && [ -n "$a6" ] && [ "$a1" -lt "$a2" ] && [ "$a2" -lt "$a3" ] && [ "$a3" -lt "$a4" ] && [ "$a4" -lt "$a5" ] && [ "$a5" -lt "$a6" ] \
  && ok "сквозной путь: site domain → site dns --wait 300 → site cert issue → site use sstp on --dry-run → site use sstp on → gw host <домен>" \
  || bad "сквозной путь: порядок" "$(ctl_log)"
printf '%s' "$raw" | grep -q 'Жду DNS: прошло 0 с из 300' && printf '%s' "$w" | grep -q '! DNS: A-записи нет (A: нет; адреса сервера: 203.0.113.10)' \
  && printf '%s' "$w" | grep -q 'В настройках DNS домена добавьте A-запись со значением 203.0.113.10' \
  && printf '%s' "$w" | grep -q '✓ DNS готов: все A-записи ведут на этот сервер (203.0.113.10)' \
  && ok "DNS не готов: совет ядра на экране, ожидание до 5 минут со строкой «прошло N с», затем «✓ DNS готов»" || bad "сквозной путь: DNS" "$o"
printf '%s' "$w" | grep -q '✓ Сертификат Let.s Encrypt для vpn.example.com выпущен\.' \
  && printf '%s' "$w" | grep -q 'Действует до 30.12.2026 (осталось 83 дн.), продлевается сам\.' \
  && printf '%s' "$w" | grep -q 'Веб-панель и страницы пользователей уже на нём' && printf '%s' "$w" | grep -q '! Почта для Let.s Encrypt не задана' \
  && ok "выпуск: итог, срок ДД.ММ.ГГГГ, панель и страницы уже на сертификате, предупреждение ядра" || bad "сквозной путь: выпуск" "$o"
printf '%s' "$w" | grep -q 'Новые настройки понадобятся устройствам: 3\.' && printf '%s' "$w" | grep -q 'Телефон (Мама), Ноутбук (Мама), Планшет (Мама)' \
  && printf '%s' "$w" | grep -q '✓ SSTP: сертификат домена\.' && printf '%s' "$w" | grep -q '✓ Адрес для клиентов: vpn.example.com\.' \
  && ok "после выпуска: SSTP на домене — число и имена устройств, подтверждение; «Адрес для клиентов — домен» (gw host)" || bad "сквозной путь: SSTP и адрес" "$o"
r=$(printf '%s' "$o" | fit); [ "$r" = "6 ok" ] && ok "сквозной путь: шесть экранов (домен, DNS, сертификат, SSTP, адрес, подменю) — каждый 80×24" || bad "сквозной путь: 80×24" "$r"
[ "$(printf '%s' "$o" | maxw)" -le 80 ] && ok "строки пути — не шире 80 столбцов, по словам" || bad "ширина строк пути" "$(printf '%s' "$o" | maxw)"

# DNS указывает не сюда, ждать не стали — без выпуска; неверное имя — ошибка ядра и вопрос снова
use_root site-w2; fake_ctl; engine_nft
sitef domain.once.json '{"ok": false, "error": "«bad_name» — не домен: в имени «bad_name» недопустимые знаки. Домен — имя вида vpn.example.com (можно кириллицей: впн.пример.рф).", "code": 2, "data": {}}'
sitef domain.once.rc 2
sitef domain.json "{\"ok\": true, \"site\": $(site_st "$CERT_SELF" "$DNS_FAR" false false), \"dns\": $DNS_FAR, \"affected\": []}"
sitef domain.status "$(site_st "$CERT_SELF" "$DNS_FAR" false false)"
o=$(printf 'bad_name\nvpn.example.com\n2\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$w" | grep -q '\[!\] «bad_name» — не домен' && printf '%s' "$w" | grep -q 'Домен vpn.example.com указывает на 198.51.100.7' \
  && printf '%s' "$w" | grep -q 'Сертификат пока не выпустить: Let.s Encrypt проверяет домен по этой записи' \
  && ! ctl_log | grep -q 'cert issue' && ! ctl_log | grep -q 'dns --wait' && [ "$(printf '%s' "$o" | fit)" = "3 ok" ] \
  && ok "неверное имя — ошибка ядра и вопрос снова; A-запись не сюда и ждать не стали — сертификат не выпускается, подсказка 19 → 1/2" \
  || bad "путь: DNS не сюда" "$(ctl_log) | $(printf '%s' "$o" | fit) | $o"

use_root site-w3; fake_ctl; engine_nft
sitef domain.json "{\"ok\": true, \"site\": $(site_st "$CERT_SELF" "$DNS_NONE" false false), \"dns\": $DNS_NONE, \"affected\": []}"
sitef domain.status "$(site_st "$CERT_SELF" "$DNS_NONE" false false)"
sitef dns_wait.json '{"ok": false, "error": "Домен не задан: kaskadctl site domain <имя> (меню 19 → домен).", "code": 2, "data": {}}'; sitef dns_wait.rc 2
o=$(printf 'vpn.example.com\n\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip | cr)
printf '%s' "$o" | grep -q '^\[!\] Домен не задан' && ! ctl_log | grep -q 'cert issue' && [ "$(printf '%s' "$o" | fit)" = "3 ok" ] \
  && ok "ожидание DNS не удалось — причина ядра, к выпуску путь не идёт" || bad "путь: сбой ожидания DNS" "$(ctl_log) | $o"

# выпуск из подменю (2): каждый отказ — причина словами и текст ядра
use_root site-ref; fake_ctl; engine_nft
sitef status.json "$(site_st "$CERT_SELF" "$DNS_OK" false false)"
refusal() {   # <reason> <rc> <текст ядра> <ожидаемая причина>
    sitef cert_issue.json "{\"ok\": false, \"reason\": \"$1\", \"text\": \"$3\", \"error\": \"$3\", \"code\": $2$([ "$1" = limit ] && echo ', "retry_after": "2026-10-08T12:00:00Z"')}"
    sitef cert_issue.rc "$2"; : > "$CALLS/kaskadctl.log"
    o=$(printf '2\n\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
    printf '%s' "$w" | grep -qF "✗ Сертификат не выпущен: $4." && printf '%s' "$w" | grep -qF "$3" \
      && { [ "$1" = limit ] || printf '%s' "$w" | grep -qF 'Когда исправите — выпустить снова: 19 → 2.'; } \
      && ctl_log | grep -qx 'site cert issue --json' && ! ctl_log | grep -q 'site use' && [ "$(printf '%s' "$o" | fit)" = "3 ok" ]
}
refusal port80 3 "Порт 80/tcp занят: nginx (pid 812). Let's Encrypt проверяет домен через порт 80 — освободите его (уберите проброс tcp/80 или остановите эту службу) и повторите. Чужое Kaskad не останавливает." "порт 80 занят" \
  && ok "отказ port80 (код 3): «порт 80 занят» и кто его держит" || bad "отказ port80" "$o"
refusal dns 1 "Домен vpn.example.com указывает на 198.51.100.7, а адреса этого сервера — 203.0.113.10: Let's Encrypt проверку не пройдёт. Поправьте A-запись в настройках DNS домена." "Let's Encrypt не достучался до сервера по домену" \
  && ok "отказ dns: A-запись не сюда — словами ядра" || bad "отказ dns" "$o"
refusal limit 1 "Let's Encrypt временно не выдаёт сертификаты домену vpn.example.com (лимит). Повторить можно 2026-10-08 12:00 UTC (через 24 ч)." "лимит Let's Encrypt" \
  && ok "отказ limit: когда повторить" || bad "отказ limit" "$o"
refusal net 1 "Сервер не достучался до Let's Encrypt: исходящее соединение оборвалось. Домен и порт 80 тут ни при чём — это сеть сервера или хостинга." "нет связи с Let's Encrypt" \
  && ok "отказ net: сеть сервера" || bad "отказ net" "$o"
refusal apt 1 "certbot не установлен: apt-get install certbot завершился с ошибкой" "не удалось поставить certbot" \
  && printf '%s' "$w" | grep -q 'повторите через несколько минут' && ok "отказ apt: certbot не поставился, совет подождать" || bad "отказ apt" "$o"

# подменю: экран 80×24, DNS (1) без ожидания, почта (4), SSTP обратно (3), убрать домен (5)
use_root site-sub; fake_ctl; gw_st true '{"sstp": {}}' 3 1
sitef status.json "$(site_st "$CERT_LE" "$DNS_OK" true true true)"
sitef dns.json "$DNS_FAR"
sitef email.json '{"ok": true, "email": "admin@example.com", "warnings": []}'
o=$(printf '1\n2\n\n4\nadmin@example.com\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$o" | grep -qx 'Сертификат: Let.s Encrypt, до 30.12.2026 (осталось 83 дн.)' && printf '%s' "$o" | grep -qx 'Им пользуются: веб-панель, страницы пользователей, SSTP' \
  && printf '%s' "$o" | grep -q '^ 2) Сертификат: проверить или продлить$' && printf '%s' "$o" | grep -q '^ 5) Убрать домен$' \
  && ctl_log | grep -qx 'site dns --json' && ! ctl_log | grep -q 'dns --wait' && ctl_log | grep -qx 'site email admin@example.com --json' \
  && printf '%s' "$w" | grep -q '✓ Почта для Let.s Encrypt: admin@example.com\.' && [ "$(printf '%s' "$o" | fit)" = "5 ok" ] \
  && ok "подменю: статус (сертификат, кто им пользуется), 1 — DNS без ожидания по «2», 4 — почта (site email); экраны 80×24" \
  || bad "подменю" "$(ctl_log) | $(printf '%s' "$o" | fit) | $o"
sitef use_sstp_off_dry-run.json "{\"ok\": true, \"use\": {\"sstp\": false, \"ocserv\": false}, \"affected\": $AFF, \"text\": \"$F2_OFF\", \"warnings\": [], \"dry_run\": true}"
sitef use_sstp_off.json "{\"ok\": true, \"use\": {\"sstp\": false, \"ocserv\": false}, \"affected\": $AFF, \"text\": \"$F2_OFF\", \"warnings\": []}"
: > "$CALLS/kaskadctl.log"
o=$(printf '3\n1\n1\n\n0\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$o" | grep -q '^ 1) SSTP: на домене → вернуть самоподписанный (устройств: 3)$' && printf '%s' "$o" | grep -q '^ 2) OpenConnect: самоподписанный → перевести на домен (устройств: 0)$' \
  && ctl_log | grep -qx 'site use sstp off --dry-run --json' && ctl_log | grep -qx 'site use sstp off --json' \
  && printf '%s' "$w" | grep -q 'Новые настройки понадобятся устройствам: 3\.' && printf '%s' "$w" | grep -q '✓ SSTP: самоподписанный сертификат\.' \
  && [ "$(printf '%s' "$o" | fit)" = "4 ok" ] \
  && ok "3: SSTP и OpenConnect — состояние и число устройств; вернуть SSTP — --dry-run, вопрос, site use sstp off; 80×24" || bad "подменю: SSTP" "$(ctl_log) | $o"
sitef status.json "$(site_st "$CERT_LE" "$DNS_OK" true true true true 3 3)"
sitef domain_clear.json "{\"ok\": true, \"site\": $NO_SITE, \"affected\": $AFF, \"kept\": [\"/etc/letsencrypt/live/vpn.example.com\"], \"text\": \"$F2_OFF\"}"
sitef domain_clear.status "$NO_SITE"
# 3.3.7.5 круг 3: строки ядра про ссылки людей (people_warn.clear) — в окне
jq -c '. + {"people_warn": {"clear": ["ссылки пользователей на домене vpn.example.com перестанут открываться (пользователей со ссылками: 2)"], "change": []}}' \
    "$FAKE_DIR/site/status.json" > "$FAKE_DIR/site/status.json.n" && mv "$FAKE_DIR/site/status.json.n" "$FAKE_DIR/site/status.json"
: > "$CALLS/kaskadctl.log"
o=$(printf '5\n1\n\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$w" | grep -q 'панель на домене перестанет открываться — останется вход по порту' \
  && printf '%s' "$w" | grep -q 'перестанут открываться (пользователей со ссылками: 2)' \
  && printf '%s' "$w" | grep -q 'SSTP и OpenConnect вернутся на самоподписанный сертификат и адрес узла — настройки придётся выдать заново (устройств: 3; SSTP: 3; OpenConnect: 3);' \
  && printf '%s' "$w" | grep -q 'сертификат Let.s Encrypt останется на диске' && printf '%s' "$w" | grep -q 'адрес для клиентов останется доменом' \
  && ctl_log | grep -qx 'site domain --clear --json' && printf '%s' "$w" | grep -q '✓ Домен убран' \
  && printf '%s' "$w" | grep -q 'Сертификат Let.s Encrypt остался: /etc/letsencrypt/live/vpn.example.com (не продлевается)' \
  && [ "$(printf '%s' "$o" | fit)" = "2 ok" ] \
  && ok "5: убрать домен — уникальные устройства и разбивка SSTP/OpenConnect, сертификат остаётся, site domain --clear; 80×24" \
  || bad "подменю: убрать домен" "$(ctl_log) | $(printf '%s' "$o" | fit) | $o"
# REV-vless4 «было до» 2 (§3.6): VLESS на TLS этого домена с устройствами — ядро отказало ценой (ничего не меняя);
# меню печатает её и спрашивает цифрами (Enter — отмена, --yes не передан), «1» — то же с --yes; сайт Reality не
# сохранён (need_pick) — меню подбирает его с узла, называет и передаёт --vless-site
CLR_PRICE='{"ok": false, "error": "Сайт на домене выключится, VLESS перейдёт на запасной Reality под dl.google.com. КОРОТКИЕ-РИСКИ. Ссылки VLESS — выдать заново (устройств — 1: Телефон).", "code": 3, "data": {"need": "vless_change", "kind": "site", "text": "Сайт на домене выключится, VLESS перейдёт на запасной Reality под dl.google.com. КОРОТКИЕ-РИСКИ. Ссылки VLESS — выдать заново (устройств — 1: Телефон).", "site": "dl.google.com", "need_pick": false}}'
sitef status.json "$(site_st "$CERT_LE" "$DNS_OK" false false)"
sitef domain_clear.once.json "$CLR_PRICE"; printf '3\n' > "$FAKE_DIR/site/domain_clear.once.rc"
: > "$CALLS/kaskadctl.log"
o=$(printf '5\n1\n\n\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$w" | grep -q 'КОРОТКИЕ-РИСКИ. Ссылки VLESS — выдать заново (устройств — 1: Телефон)\.' \
  && printf '%s' "$w" | grep -q '1) Убрать домен и выдать ключи VLESS заново' && printf '%s' "$w" | grep -q 'Отменено\.' \
  && [ "$(ctl_log | grep -c '^site domain --clear')" = 1 ] && ! ctl_log | grep -q -- '--yes' && ! printf '%s' "$w" | grep -q '✓ Домен убран' \
  && ok "5: убрать домен при устройствах VLESS на TLS — цена ядра и вопрос цифрами, Enter — отмена, --yes не передан" \
  || bad "убрать домен: цена VLESS, отмена" "$(ctl_log | tr '\n' ';') | $w"
sitef status.json "$(site_st "$CERT_LE" "$DNS_OK" false false)"
sitef domain_clear.once.json "$CLR_PRICE"; printf '3\n' > "$FAKE_DIR/site/domain_clear.once.rc"
: > "$CALLS/kaskadctl.log"
o=$(printf '5\n1\n1\n\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
ctl_log | grep -qx 'site domain --clear --yes --json' && [ "$(ctl_log | grep -c '^site domain --clear')" = 2 ] \
  && printf '%s' "$w" | grep -q '✓ Домен убран' \
  && ok "5: убрать домен при устройствах VLESS — «1» — site domain --clear --yes, итог" || bad "убрать домен: цена VLESS, да" "$(ctl_log | tr '\n' ';') | $w"
gwf vless.masks.json '{"ok": true, "masks": [{"sni": "vpn.example.com", "mask": "own", "check": {"ok": true}}, {"sni": "www.microsoft.com", "mask": "list", "check": {"ok": false}}, {"sni": "dl.google.com", "mask": "list", "check": {"ok": true}}]}'
sitef status.json "$(site_st "$CERT_LE" "$DNS_OK" false false)"
sitef domain_clear.once.json "$(printf '%s' "$CLR_PRICE" | jq -c '.data.need_pick = true | .data.site = null')"; printf '3\n' > "$FAKE_DIR/site/domain_clear.once.rc"
sitef domain_clear_vless-site_dl.google.com.once.json "$CLR_PRICE"; printf '3\n' > "$FAKE_DIR/site/domain_clear_vless-site_dl.google.com.once.rc"
: > "$CALLS/kaskadctl.log"
o=$(printf '5\n1\n1\n\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
gw_calls | grep -qx 'gw vless masks --check --json' && ctl_log | grep -qx 'site domain --clear --vless-site dl.google.com --json' \
  && ctl_log | grep -qx 'site domain --clear --vless-site dl.google.com --yes --json' && printf '%s' "$w" | grep -q 'dl.google.com' \
  && printf '%s' "$w" | grep -q '✓ Домен убран' \
  && ok "5: убрать домен, сайт Reality не сохранён — меню подобрало сайт (не свой), назвало, --vless-site и --yes" \
  || bad "убрать домен: подбор сайта Reality" "$(ctl_log | tr '\n' ';') | $w"
rm -f "$FAKE_DIR/gw/vless.masks.json"

# меню 18: панель на сертификате Let's Encrypt домена — вместо отпечатка самоподписанного
use_root site-18; fake_ctl; engine_nft; mkdir -p "$FAKE_DIR/panel"
printf '%s\n' '{"ok": true, "installed": true, "active": true, "public": {"on": true, "port": 41234}, "require_password": false,
  "password": true, "url": "https://vpn.example.com:41234/", "fingerprint": "AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89",
  "tunnel": "ssh -L 1980:127.0.0.1:1980 root@203.0.113.10"}' > "$FAKE_DIR/panel/status.json"
sitef status.json "$(site_st "$CERT_LE" "$DNS_OK" false false true)"
o=$(printf '0\n' | lib t_panel3 2>&1 | strip)
printf '%s' "$o" | grep -qx 'Из интернета: https://vpn.example.com:41234/' && printf '%s' "$o" | grep -qx "  сертификат Let's Encrypt для vpn.example.com, до 30.12.2026" \
  && ! printf '%s' "$o" | grep -q 'отпечаток' && [ "$(printf '%s' "$o" | fit)" = "1 ok" ] \
  && ok "18: на сертификате Let's Encrypt домена — «сертификат Let's Encrypt для <домен>, до ДД.ММ.ГГГГ» вместо отпечатка, адрес с доменом" \
  || bad "18: сертификат домена" "$o"
sitef status.json "$(site_st "$CERT_SELF" "$DNS_OK" false false false)"
o=$(printf '0\n' | lib t_panel3 2>&1 | strip)
printf '%s' "$o" | grep -q 'отпечаток сертификата SHA-256' && ok "18: самоподписанный — отпечаток как раньше" || bad "18: отпечаток" "$o"

# меню 15: site remove --json до шлюза; пакеты Kaskad (шлюза и домена) — одной строкой apt remove; сертификат LE остаётся
use_root site-15; fake_ctl; gw_st true '{"wg": {}}' 1 1
jq '.site = {"domain": "vpn.example.com", "mode": "domain", "cert": "letsencrypt"}' "$KASKAD_DIR/state.json" > "$W/st.json" && cp "$W/st.json" "$KASKAD_DIR/state.json"
mkdir -p "$KASKAD_DIR/site" "$KASKAD_DIR/gw" "$KASKAD_ROOT/etc/letsencrypt/live/vpn.example.com"
printf 'certbot\n' > "$KASKAD_DIR/site/pkgs"; touch "$FAKE_DIR/pkg.certbot" "$FAKE_DIR/pkg.openvpn"
printf '{"openvpn": {"by_kaskad": true}}\n' > "$KASKAD_DIR/gw/packages.json"
sitef remove.json '{"ok": true, "kept": ["/etc/letsencrypt/live/vpn.example.com"], "packages": ["certbot"], "affected": []}'
o=$(lib t_uninst 2>&1 | strip); w=$(printf '%s' "$o" | flat)
p1=$(n 'site remove --json'); p2=$(n 'gw purge --users --yes')
[ -n "$p1" ] && [ -n "$p2" ] && [ "$p1" -lt "$p2" ] \
  && printf '%s' "$w" | grep -q 'Пакеты, которые ставил Kaskad (если не нужны: apt remove openvpn certbot)' \
  && printf '%s' "$w" | grep -q "Сертификат Let's Encrypt домена в /etc/letsencrypt (не продлевается)" \
  && printf '%s' "$w" | grep -q 'Домен: продление сертификата, его таймеры и правило UFW порта 80' \
  && printf '%s' "$w" | grep -q 'Домен снят: сертификат не продлевается' \
  && printf '%s' "$w" | grep -q 'Пакеты, которые ставил Kaskad, остались: openvpn, certbot\. Если они больше не нужны: apt remove openvpn certbot' \
  && ok "15: kaskadctl site remove --json — до шлюза; certbot вместе с пакетами шлюза в строке apt remove; сертификат LE остаётся — одной строкой" \
  || bad "15: домен при удалении" "$(ctl_log) | $o"
# Ядро уже не отвечает: по state.json берём только его домен, выключаем autorenew и убираем лишь наш hook.
use_root site-15-fallback; fake_ctl; engine_nft
jq '.site = {"domain": "vpn.example.com", "mode": "domain", "cert": "letsencrypt"}' "$KASKAD_DIR/state.json" > "$W/st.json" && cp "$W/st.json" "$KASKAD_DIR/state.json"
mkdir -p "$KASKAD_DIR/site" "$KASKAD_ROOT/etc/letsencrypt/renewal"
cat > "$KASKAD_ROOT/etc/letsencrypt/renewal/vpn.example.com.conf" <<'CONF'
version = 2.9.0
autorenew = True
[renewalparams]
renew_hook = /opt/kaskad/bin/kaskadctl site cert-installed --quiet
deploy_hook = systemctl reload nginx
CONF
cat > "$KASKAD_ROOT/etc/letsencrypt/renewal/foreign.example.org.conf" <<'CONF'
autorenew = True
[renewalparams]
renew_hook = /opt/kaskad/bin/kaskadctl site cert-installed --quiet
CONF
sitef remove.rc 1; touch "$FAKE_DIR/ufw_active"
printf '[ 7] 80/tcp ALLOW IN    Anywhere                   # kaskad-acme\n' > "$FAKE_DIR/ufw_numbered"
o=$(lib t_uninst 2>&1 | strip)
own="$KASKAD_ROOT/etc/letsencrypt/renewal/vpn.example.com.conf"
foreign="$KASKAD_ROOT/etc/letsencrypt/renewal/foreign.example.org.conf"
! grep -q 'site cert-installed' "$own" && grep -qx 'autorenew = False' "$own" \
  && grep -qx 'deploy_hook = systemctl reload nginx' "$own" \
  && grep -qx 'autorenew = True' "$foreign" && grep -q 'site cert-installed' "$foreign" \
  && grep -qx -- '--force delete 7' "$CALLS/ufw.log" \
  && printf '%s' "$o" | grep -q 'хук и продление сняты по файлам' \
  && ok "15 без ядра: только наш renewal hook снят, autorenew выключен лишь для домена Kaskad, чужой конфиг и kaskad-acme убраны бережно" \
  || bad "15: запасное удаление домена" "$(cat "$own" 2>&1) | $(cat "$foreign" 2>&1) | $(cat "$CALLS/ufw.log") | $o"
use_root site-15b; fake_ctl; gw_st true '{"wg": {}}' 1 1; uninst_fixture; mkdir -p "$KASKAD_ROOT/root/kaskad-issue"
jq '.site = {"domain": "vpn.example.com", "mode": "domain", "cert": "letsencrypt"}' "$KASKAD_DIR/state.json" > "$W/st.json" && cp "$W/st.json" "$KASKAD_DIR/state.json"
mkdir -p "$KASKAD_DIR/site" "$KASKAD_ROOT/etc/letsencrypt/live/vpn.example.com"; printf 'certbot\n' > "$KASKAD_DIR/site/pkgs"; touch "$FAKE_DIR/pkg.certbot"
o=$(printf '2\n\n' | K3_ISSUE_DIR="$KASKAD_ROOT/root/kaskad-issue" lib t_un3 2>&1 | strip | sed "s#$KASKAD_ROOT##g")
r=$(printf '%s' "$o" | fit 'Отменено.')
[ "$r" = "1 ok" ] && printf '%s' "$o" | grep -q 'Домен: продление' && ok "15: экран удаления со всем сразу (шлюз, панель, ускорение, домен, пакеты, сертификат LE) — 80×24" || bad "15: экран с доменом" "$r $o"

# k3_post_update: сертификат домена — потребителям (site cert-installed --quiet), только когда он выпущен
t_post_site() {
    k3_ensure_lib() { return 0; }
    k3_lib_ok() { return 0; }
    k3_units_rewrite() { return 1; }
    k3ctl() { printf '%s\n' "$*" >> "$CALLS/kaskadctl.log"; }
    k3_post_update
}
use_root site-post; site_state vpn.example.com true
lib t_post_site >/dev/null 2>&1
ctl_log | grep -qx 'site cert-installed --quiet' && ok "k3_post_update: домен с сертификатом — kaskadctl site cert-installed --quiet" || bad "post-update: cert-installed" "$(ctl_log)"
# BC2a: vhost сайта — по шаблону этой версии (site vhost --refresh --quiet), после того как код обновлён (k3_ensure_lib)
ctl_log | grep -qx 'site vhost --refresh --quiet' \
  && sed -n '/^k3_post_update() {/,/^}/p' "$S" | awk '/k3_ensure_lib \|\|/ { e = NR } /site vhost --refresh --quiet/ { v = NR } END { exit !(e && v && e < v) }' \
  && ok "k3_post_update: домен — kaskadctl site vhost --refresh --quiet, после обновления кода" || bad "post-update: vhost --refresh" "$(ctl_log)"
: > "$CALLS/kaskadctl.log"; site_state vpn.example.com false; lib t_post_site >/dev/null 2>&1
! ctl_log | grep -q '^site ' && { : > "$CALLS/kaskadctl.log"; engine_nft; lib t_post_site >/dev/null 2>&1; ! ctl_log | grep -q '^site '; } \
  && ok "k3_post_update: без домена или без выпуска — site не зовётся (без шума)" || bad "post-update без домена" "$(ctl_log)"

# (новую пометку cut — только после сверки строк PASS частями и одним скриптом: tools/k3_install_par.py -j 3 и -j 1)
# k3-install-par: cut — tools/k3_install_par.py может начать часть здесь (разделам ниже хватает функций и значений выше)
echo "── 40. пункт 19 «Сайт-маскировка» (4.0-C): включить и выключить, каталог по 25 с поиском, шаблон, адрес панели, проверка, 80×24 ──"
# STATUS с сайтом (контракт §7.1): mode site, шаблон, адрес панели маской. site_on [domain] [CERT] [DNS]
MASK='https://vpn.example.com/app-AbCdEfGh••••WxYz/'
FULL='https://vpn.example.com/app-AbCdEfGhSECRETxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxWxYz/'
site_on() {
    site_st "${2:-$CERT_LE}" "${3:-$DNS_OK}" false false true | jq -c --arg d "${1:-vpn.example.com}" --arg m "$MASK" \
        '.domain = $d | .domain_unicode = $d | .mode = "site" | .site = {"template": {"id": "gh_good", "name": "Good Business",
         "source": "https://github.com/fx/good", "deployed_at": "2026-10-07T12:00:00Z"}, "root": "/var/www/kaskad-site",
         "panel_url_masked": $m, "nginx": {"installed": true, "active": true, "version": "1.24.0", "ours": true},
         "port443": {"free": true, "who": null}, "check": null}'
}
site_off() {   # режим domain с боевым сертификатом; $1 — порт 443 занят (кем)
    site_st "$CERT_LE" "$DNS_OK" false false true | jq -c --arg w "${1:-}" '.mode = "domain" | .site = {"template": null,
        "root": "/var/www/kaskad-site", "panel_url_masked": null, "nginx": {"installed": false, "active": false,
        "version": null, "ours": false}, "port443": {"free": ($w == ""), "who": (if $w == "" then null else $w end)}, "check": null}'
}
# Каталог: категории и страницы (25 шаблонов, как отдаёт ядро). tpl_page <категория> <страница> <страниц> <шаблонов> <всего>
TPL_CATS='[{"id": "business", "title": "Бизнес и корпоративные", "icon": "briefcase", "count": 1174},
  {"id": "landing", "title": "Лендинги", "icon": "rocket", "count": 6654}, {"id": "restaurant", "title": "Рестораны и еда", "icon": "utensils", "count": 1684}]'
tpl_page() {
    python3 - "$@" <<'PY'
import json, sys
cat, page, pages, n, total = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
items = []
for i in range(1, n + 1):
    k = (page - 1) * 25 + i
    items.append({"id": "gh_%s_%d" % (cat, k), "name": "%s Template Number %d With A Very Long Name" % (cat.title(), k),
                  "category": cat, "source": "github", "license": "MIT", "preview": "https://example.org/%s/%d/" % (cat, k),
                  "description": "Корпоративный сайт с минималистичным дизайном и слайдером", "repo": "https://github.com/fx/t%d" % k,
                  "author": "fx"})
print(json.dumps({"ok": True, "items": items, "page": page, "pages": pages, "per_page": 25, "total": total,
                  "category": cat, "query": None}, ensure_ascii=False))
PY
}
CAFE='{"ok": true, "items": [{"id": "gh_cafe", "name": "Кафе Ромашка", "category": "business", "source": "github", "license": "CC-BY-4.0",
  "preview": "https://example.org/cafe/", "description": "Сайт кафе с меню и бронированием", "repo": "https://github.com/fx/cafe", "author": "fx"}],
  "page": 1, "pages": 1, "per_page": 25, "total": 1, "category": "business", "query": "кафе"}'
tpl_files() {   # ответы site templates: категории, business — 2 страницы, поиск «кафе», landing — 1 страница
    sitef templates.json "{\"ok\": true, \"categories\": $TPL_CATS, \"catalog_total\": 33467, \"items\": [], \"page\": 1, \"pages\": 1, \"total\": 33467}"
    sitef templates_category=business_page_1.json "$(tpl_page business 1 2 25 32)"
    sitef templates_category=business_page_2.json "$(tpl_page business 2 2 7 32)"
    sitef templates_category=business_query=кафе_page_1.json "$CAFE"
    sitef templates_category=landing_page_1.json "$(tpl_page landing 1 1 3 3)"
}
MODE_OK='{"ok": true, "site": '"$(site_on)"', "template": {"id": "gh_good", "name": "Good Business", "source": "https://github.com/fx/good", "deployed_at": "2026-10-07T12:00:00Z"}}'
t_menu_exp() { k3_site_cert_within() { return 0; }; scr show_menu; }   # сертификат истёк — самая длинная строка 19

# главное меню: сайт включён — «Домен: … · сайт · сертификат до ДД.ММ»; длинный домен и истёкший сертификат — в 80 столбцов
use_root site40-m; fake_ctl
printf '{\n  "engine": "nft",\n  "site": {"domain": "vpn.example.com", "mode": "site", "cert": "letsencrypt"},\n  "schema": 1\n}\n' > "$KASKAD_DIR/state.json"
le_copy 60; o=$(lib t_menu3 <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qx "Домен: vpn.example.com · сайт · сертификат до $(day_after 60)" && [ "$(printf '%s' "$o" | fit)" = "1 ok" ] \
  && [ -z "$(ctl_log | grep -vx 'summary --json')" ] \
  && ok "главное меню: «Домен: vpn.example.com · сайт · сертификат до ДД.ММ» — один снимок ядра; 80×24" \
  || bad "строка 19 с сайтом" "$(printf '%s' "$o" | grep '^Домен') $(printf '%s' "$o" | fit)"
jq '.site.domain = "a-very-long-subdomain-name-for-the-test.example.com"' "$KASKAD_DIR/state.json" > "$W/st.json" && cp "$W/st.json" "$KASKAD_DIR/state.json"
le_copy 60 a-very-long-subdomain-name-for-the-test.example.com
# истёкший сертификат: снимок настоящего ядра, срок в нём — прошедший
PYTHONPATH="$K3_TEST_LIB" python3 -m kaskad summary --json | jq -c '.site.cert.days_left = -3 | .site.cert.end = "08.12"' > "$FAKE_DIR/summary.json"
o=$(lib t_menu3 <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qx 'Домен: a-very-long-subdomain-name-for-th… · сайт · сертификат истёк 08.12' && [ "$(printf '%s' "$o" | fit)" = "1 ok" ] \
  && [ "$(printf '%s' "$o" | maxw)" -le 79 ] \
  && ok "длинный домен с сайтом и истёкшим сертификатом — имя с «…», строка не шире 79 столбцов, меню 80×24" \
  || bad "строка 19: длинный домен с сайтом" "$(printf '%s' "$o" | maxw) $(printf '%s' "$o" | grep '^Домен')"
rm -f "$K3_OPT/bin/kaskadctl"; o=$(lib t_menu_exp <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -q '^✗ Ядро Kaskad 3 недоступно' && printf '%s' "$o" | grep -q '^Домен: .* · сайт · сертификат истёк' && [ "$(printf '%s' "$o" | fit)" = "1 ok" ] \
  && ok "без ядра: красная строка и «Домен: … · сайт · сертификат истёк» (jq и openssl, как раньше) — меню 80×24" || bad "главное меню без ядра с сайтом" "$(printf '%s' "$o" | fit) $o"

# подменю с сайтом: шапка (режим, шаблон, адрес маской), пункты 6–9; длинный домен и DNS с советом — короткая шапка
use_root site40-sub; fake_ctl; engine_nft
sitef status.json "$(site_on)"
o=$(printf '0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip)
printf '%s' "$o" | grep -qx 'Сайт-маскировка: включена, шаблон «Good Business»' && printf '%s' "$o" | grep -qxF "Панель на домене: $MASK" \
  && printf '%s' "$o" | grep -qx ' 6) Сайт-маскировка: выключить' && printf '%s' "$o" | grep -qx ' 7) Сменить шаблон сайта' \
  && printf '%s' "$o" | grep -qx ' 8) Адрес панели на домене' && printf '%s' "$o" | grep -qx ' 9) Проверить сайт' \
  && printf '%s' "$o" | grep -q '^Им пользуются' && [ "$(printf '%s' "$o" | fit)" = "1 ok" ] && ! printf '%s' "$o" | grep -q 'SECRET' \
  && ok "подменю с сайтом: «Сайт-маскировка: включена, шаблон «…»», адрес панели маской, пункты 6–9; 80×24" || bad "подменю с сайтом" "$(printf '%s' "$o" | fit) $o"
long=a-very-long-subdomain-name-for-the-masquerade-site-test.example-hosting.com
gw_st true '{"sstp": {}}' 3 1
sitef status.json "$(site_on "$long" "$CERT_LE" "$DNS_FAR" | jq -c '.port80 = {"free": false, "who": "проброс tcp/80 → 10.0.0.5:80 (правило 3) и ещё какая-то служба на этом порту"}')"
o=$(printf '0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip)
[ "$(printf '%s' "$o" | fit)" = "1 ok" ] && [ "$(printf '%s' "$o" | maxw)" -le 80 ] && ! printf '%s' "$o" | grep -q '^Им пользуются' \
  && printf '%s' "$o" | grep -q '^ 9) Проверить сайт' \
  && ok "длинный домен, DNS не сюда, порт 80 занят, адрес не домен — шапка короче (без совета и «Им пользуются»), экран 80×24" \
  || bad "подменю с сайтом: длинная шапка" "$(printf '%s' "$o" | fit) $(printf '%s' "$o" | maxw) $o"
gw_st false '{}' 0 0
sitef status.json "$(site_off "nginx (pid 900) — чужой сайт")"
o=$(printf '7\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$o" | grep -qx 'Сайт-маскировка: выключена — сертификат без сайта' && printf '%s' "$o" | grep -qx ' 6) Сайт-маскировка: включить' \
  && ! printf '%s' "$o" | grep -q '^ 7)' && printf '%s' "$w" | grep -q 'Порт 443/tcp занят: nginx (pid 900) — чужой сайт — сайт-маскировку не включить' \
  && printf '%s' "$w" | grep -q 'Сначала включите сайт-маскировку: 6\.' && [ "$(printf '%s' "$o" | fit)" = "2 ok" ] \
  && ok "без сайта: «выключена — сертификат без сайта», только пункт 6, порт 443 занят — заранее; 7 — «сначала включите: 6»" \
  || bad "подменю без сайта" "$(printf '%s' "$o" | fit) $o"

# включить: Enter — прежний шаблон, если сайт уже был, иначе случайный (site mode site без ключа: T-M6-qs330c №5),
# долгий шаг и итог с адресом панели маской
use_root site40-on; fake_ctl; engine_nft
sitef status.json "$(site_off)"; sitef mode_site.json "$MODE_OK"; sitef mode_site.status "$(site_on)"
o=$(printf '6\n\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
ctl_log | grep -qx 'site mode site --json' && printf '%s' "$w" | grep -q 'Kaskad поставит nginx (если его нет), займёт порты 80 и 443/tcp' \
  && printf '%s' "$w" | grep -q '\[\*\] Включаю сайт: nginx, шаблон с GitHub, проверка настроек — обычно 1–3 минуты' \
  && printf '%s' "$w" | grep -q '✓ Сайт-маскировка включена: https://vpn.example.com/ — шаблон «Good Business»\.' \
  && printf '%s' "$w" | grep -qF "Веб-панель на домене: $MASK — полный адрес: 19 → 8." \
  && printf '%s' "$o" | grep -qx 'Сайт-маскировка: включена, шаблон «Good Business»' && [ "$(printf '%s' "$o" | fit)" = "3 ok" ] \
  && printf '%s' "$o" | grep -qx ' Enter) Прежний шаблон, если сайт уже включали; иначе случайный' \
  && printf '%s' "$o" | grep -qx '     2) Новый случайный шаблон' \
  && ok "6 → Enter: site mode site (прежний шаблон или случайный), «[*] Включаю сайт…», итог: шаблон и адрес панели маской; меню — уже с сайтом; 80×24" \
  || bad "включить: Enter" "$(ctl_log) | $(printf '%s' "$o" | fit) | $o"
# «2» — новый случайный шаблон: site mode site --random
use_root site40-rnd; fake_ctl; engine_nft
sitef status.json "$(site_off)"; sitef mode_site_random.json "$MODE_OK"; sitef mode_site_random.status "$(site_on)"
o=$(printf '6
2

0
' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
ctl_log | grep -qx 'site mode site --random --json' && ! ctl_log | grep -qx 'site mode site --json' \
  && printf '%s' "$w" | grep -q '✓ Сайт-маскировка включена: https://vpn.example.com/ — шаблон «Good Business»\.' \
  && ok "6 → 2: site mode site --random — новый случайный шаблон" || bad "включить: новый случайный" "$(ctl_log) | $o"
# заглушка вместо шаблона — сказать и куда идти
sitef status.json "$(site_off)"; sitef mode_site.json "$(printf '%s' "$MODE_OK" | jq -c '.fallback = true | .warnings = ["Шаблоны не скачались: нет связи с GitHub."]')"
o=$(printf '6\n\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$w" | grep -q 'Шаблоны с GitHub не скачались — поставлена страница-заглушка. Шаблон можно сменить: 19 → 7\.' \
  && printf '%s' "$w" | grep -q '! Шаблоны не скачались: нет связи с GitHub\.' \
  && ok "включить: fallback: true — «простая страница-заглушка», сменить 19 → 7, предупреждения ядра" || bad "включить: заглушка" "$o"

# включить из каталога: категории → страница 1 (25 в два столбца) → N → P → поиск «кафе» → карточка с превью → Y
use_root site40-cat; fake_ctl; engine_nft; tpl_files
sitef status.json "$(site_off)"; sitef mode_site.json "$MODE_OK"; sitef mode_site.status "$(site_on)"
o=$(printf '6\n1\n1\nN\nP\n/кафе\n1\n\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
seq=$(ctl_log | grep -v '^site status' | grep -v '^version' | tr '\n' ';')
[ "$seq" = 'site templates --json;site templates --category=business --page 1 --check --json;site templates --category=business --page 2 --check --json;site templates --category=business --page 1 --check --json;site templates --category=business --query=кафе --page 1 --check --json;site mode site --template=gh_cafe --json;' ] \
  && ok "каталог: site templates → --category=business --page 1 → --page 2 (N) → --page 1 (P) → --query=кафе (страницы — с --check) → site mode site --template=gh_cafe" \
  || bad "каталог: вызовы ядра" "$seq"
printf '%s' "$o" | grep -q '^ 1) Бизнес и корпоративные (1 174)' && printf '%s' "$w" | grep -q 'Шаблонов: 33467' \
  && printf '%s' "$o" | grep -q '^ 1) Business Template Number 1 With A…  14) Business Template Number 14 With A…$' \
  && printf '%s' "$o" | grep -qx '13) Business Template Number 13 With A…' \
  && printf '%s' "$w" | grep -q 'Бизнес и корпоративные — найдено 32, страница 1 из 2' && printf '%s' "$w" | grep -q 'Номер — выбрать · N — далее · / — поиск · 0 — категории' \
  && printf '%s' "$w" | grep -q 'страница 2 из 2' && printf '%s' "$w" | grep -q 'Номер — выбрать · P — назад · / — поиск' \
  && printf '%s' "$w" | grep -q 'поиск «кафе» — найдено 1, страница 1 из 1' && printf '%s' "$o" | grep -qx ' 1) Кафе Ромашка · Сайт кафе с меню и…' \
  && ok "каталог: категории с числом шаблонов; 25 на странице в два столбца (1–13 и 14–25), «…» у длинного; N/P/поиск — подсказкой" \
  || bad "каталог: экраны" "$o"
printf '%s' "$w" | grep -q 'Кафе Ромашка — Бизнес и корпоративные' && printf '%s' "$w" | grep -q 'Сайт кафе с меню и бронированием' \
  && printf '%s' "$w" | grep -q 'Лицензия: CC-BY-4.0; авторы: fx' && printf '%s' "$o" | grep -qx '  https://example.org/cafe/' \
  && printf '%s' "$w" | grep -q '✓ Сайт-маскировка включена' \
  && ok "карточка шаблона: название, категория, описание, лицензия и авторы, превью-ссылка; «Включить сайт с этим шаблоном?» (1 — да, по умолчанию)" \
  || bad "каталог: карточка" "$o"
r=$(printf '%s' "$o" | fit); m=$(printf '%s' "$o" | maxw)
[ "$r" = "9 ok" ] && [ "$m" -le 80 ] && ok "каталог: каждый экран (меню, включение, категории, 3 страницы, карточка) — 80×24, строки до 80 столбцов" \
  || bad "каталог: 80×24" "$r $m"
# «2» на карточке — назад к списку; 0 — к категориям; Enter — назад; ничего не меняется
: > "$CALLS/kaskadctl.log"; sitef status.json "$(site_off)"
o=$(printf '6\n1\n2\n9\n3\n2\n0\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip)
! ctl_log | grep -q '^site mode' && [ "$(ctl_log | grep -cx 'site templates --category=landing --page 1 --check --json')" = 1 ] \
  && printf '%s' "$o" | grep -q 'Landing Template Number 3 With A Very Long Name — Лендинги' \
  && printf '%s' "$o" | grep -q 'Нет такого пункта' \
  && ok "каталог: неверный номер — «нет такого пункта», «2» на карточке — к списку (без нового вызова ядра), 0 и Enter — назад; сайт не включается" \
  || bad "каталог: отказ от выбора" "$(ctl_log) | $o"
# «жив ли шаблон» (3.2.21): перед страницей — «[*] Проверяю шаблоны...» (site templates … --check); удалённый автором —
# тусклая строка «· недоступен», его номер не принимается; без образца — «· без образца», в карточке нет ссылки превью;
# не проверен — как обычно
use_root site40-health; fake_ctl; engine_nft; tpl_files
sitef status.json "$(site_off)"
sitef templates_category=landing_page_1.json "$(tpl_page landing 1 1 3 3 | jq -c '.items[0].health = {"state": "gone", "text": "Шаблон удалён автором — выбрать нельзя"}
  | .items[1].health = {"state": "no_preview", "text": "Образец снят автором, сам шаблон ставится"} | .items[2].health = {"state": "unknown", "text": "Не удалось проверить"}')"
o=$(printf '6\n1\n2\n1\n2\n2\n3\n2\n0\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1); s=$(printf '%s' "$o" | strip); w=$(printf '%s' "$s" | flat)
[ "$(ctl_log | grep -cx 'site templates --category=landing --page 1 --check --json')" = 1 ] && ! ctl_log | grep -q '^site mode' \
  && printf '%s' "$w" | grep -q '\[\*\] Проверяю шаблоны' \
  && printf '%s' "$o" | grep -q $'\033\\[2m 1) Landing Template Numb… · недоступен\033\\[0m' \
  && printf '%s' "$s" | grep -q '^ 1) Landing Template Numb… · недоступен *3) Landing Template Number 3 With A V…$' \
  && printf '%s' "$s" | grep -qx ' 2) Landing Template Num… · без образца' \
  && printf '%s' "$w" | grep -q 'Тусклые — шаблон удалён автором, выбрать нельзя\.' \
  && printf '%s' "$w" | grep -q 'Шаблон «Landing Template Number 1 With A Very Long Name» удалён автором — выберите другой\.' \
  && ! printf '%s' "$w" | grep -q 'Landing Template Number 1 With A Very Long Name — Лендинги' \
  && printf '%s' "$w" | grep -q 'Landing Template Number 2 With A Very Long Name — Лендинги' \
  && printf '%s' "$w" | grep -q 'Образец снят автором, сам шаблон ставится\.' && ! printf '%s' "$w" | grep -q 'https://example.org/landing/2/' \
  && printf '%s' "$w" | grep -q 'https://example.org/landing/3/' \
  && printf '%s' "$s" | fit | grep -q ' ok$' && [ "$(printf '%s' "$s" | maxw)" -le 80 ] \
  && ok "жив ли шаблон: «[*] Проверяю шаблоны...» и --check; удалённый — тусклый «· недоступен», номер не принят («удалён автором — выберите другой»); без образца — без ссылки превью; не проверен — как обычно; 80×24" \
  || bad "каталог: жив ли шаблон" "$(ctl_log) | $(printf '%s' "$s" | fit) | $s"

# каждый отказ ядра — причина словами, текст ядра и что сделать
use_root site40-ref; fake_ctl; engine_nft
sitef status.json "$(site_off)"
site_refusal() {   # <reason> <текст ядра> <ожидаемая причина> <ожидаемый совет> [template_reason]
    sitef mode_site.json "{\"ok\": false, \"reason\": \"$1\", \"text\": \"$2\", \"error\": \"$2\", \"code\": 3$([ -n "${5:-}" ] && echo ", \"template_reason\": \"$5\", \"template_id\": null")}"
    sitef mode_site.rc 3; : > "$CALLS/kaskadctl.log"
    o=$(printf '6\n\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
    printf '%s' "$w" | grep -qF "✗ Сайт не включён: $3" && printf '%s' "$w" | grep -qF "$2" && printf '%s' "$w" | grep -qF "$4" \
      && ctl_log | grep -qx 'site mode site --json' && [ "$(printf '%s' "$o" | fit)" = "3 ok" ]
}
site_refusal no_domain "Для сайта сначала задайте домен: kaskadctl site domain <имя>." "домен не задан." "Задайте домен: 19." \
  && ok "отказ no_domain: «домен не задан», задать — 19" || bad "отказ no_domain" "$o"
site_refusal no_cert "Для сайта нужен действующий боевой сертификат Let's Encrypt для vpn.example.com." "нет боевого сертификата Let's Encrypt" "Выпустите его: 19 → 2" \
  && ok "отказ no_cert: выпустить — 19 → 2" || bad "отказ no_cert" "$o"
site_refusal port443 "Порт 443/tcp занят: проброс tcp/443 → 10.0.0.5:443. Сайт на домене не сможет открыться." "порт 443/tcp занят" "режим «сертификат без сайта»" \
  && ok "отказ port443: кто держит 443 (текст ядра), без сайта всё работает" || bad "отказ port443" "$o"
site_refusal port80 "Порт 80/tcp занят: nginx (pid 812). Сайту он нужен для перехода с http на https." "порт 80/tcp занят" "режим «сертификат без сайта»" \
  && ok "отказ port80 (свой reason, без разбора текста) — «порт 80/tcp занят»" || bad "отказ port80" "$o"
site_refusal port443 "Порт 80 указан в тексте, но reason — port443." "порт 443/tcp занят" "сертификат без сайта" \
  && ok "port443 — по reason, текст ядра не разбирается" || bad "отказ port443 по reason" "$o"
site_refusal neighbour "На 443 уже настроен nginx соседа (goVLESS). Оставьте режим «домен»." "на сервере уже есть сайт другой программы (nginx на 443)" "сертификат без сайта" \
  && ok "отказ neighbour: сосед назван ядром" || bad "отказ neighbour" "$o"
site_refusal apt "Не удалось поставить nginx для сайта: apt-get install nginx завершился с ошибкой" "не удалось поставить nginx или git" "повторите через несколько минут" \
  && ok "отказ apt: подождать и повторить" || bad "отказ apt" "$o"
site_refusal template "Шаблон сайта не развёрнут: Каталог шаблонов сайта лежит в закрытом репозитории Kaskad. Сайт не включён." "шаблон не поставлен — нет ключа GitHub для каталога шаблонов" "Задайте ключ GitHub: главное меню → 8 → 6" nokey \
  && printf '%s' "$w" | grep -q 'Всё осталось как было: nginx и продление не тронуты' \
  && ok "отказ template/nokey — путь 8 → 6, всё осталось как было" || bad "отказ template nokey" "$o"
site_refusal template "Шаблон сайта не развёрнут: нет связи с GitHub. Сайт не включён." "шаблон не поставлен — нет связи с GitHub" "Проверьте сеть сервера (доступ к github.com)" net \
  && ok "отказ template/net — проверить сеть" || bad "отказ template net" "$o"
for tr in download_failed no_index app_shell broken_assets timeout too_big bad_repo; do
    site_refusal template "Шаблон сайта не развёрнут: x. Сайт не включён." "шаблон не поставлен —" "Выберите другой шаблон (19 → 6 → 1)" "$tr" \
      || { bad "отказ template/$tr" "$o"; continue; }
    case "$tr" in no_index) printf '%s' "$w" | grep -q 'нет готовой страницы index.html' ;; too_big) printf '%s' "$w" | grep -q 'слишком большой' ;; *) true ;; esac \
      && ok "отказ template/$tr — словами, «выберите другой шаблон»" || bad "отказ template/$tr: слова" "$o"
done
site_refusal nginx_test "nginx не принял настройки сайта: unknown directive. Прежний файл возвращён; проверьте конфигурацию nginx соседа." "nginx не принял настройки" "Прежние настройки nginx возвращены" \
  && ok "отказ nginx_test: прежние настройки возвращены" || bad "отказ nginx_test" "$o"
sitef mode_site.json '{"ok": false, "error": "Шаблон сайта не развёрнут: нет связи с GitHub", "code": 1, "data": {}}'; sitef mode_site.rc 1
o=$(printf '6\n\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip)
printf '%s' "$o" | grep -q '^\[!\] Шаблон сайта не развёрнут: нет связи с GitHub' && ok "ошибка ядра без reason — её текст" || bad "включить: ошибка ядра" "$o"

# выключить: последствия, по умолчанию «Отмена»; «2» — ничего; «1» — site mode domain
use_root site40-off; fake_ctl; engine_nft
sitef status.json "$(site_on)"; sitef mode_domain.json "{\"ok\": true, \"site\": $(site_off)}"; sitef mode_domain.status "$(site_off)"
o=$(printf '6\n\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip)
! ctl_log | grep -q '^site mode' && printf '%s' "$o" | grep -q '^Отменено\.' && ok "выключить: Enter — «Отменено.», ядро не зовётся" || bad "выключить: отмена" "$(ctl_log) | $o"
o=$(printf '6\n1\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$w" | grep -q 'Выключить сайт-маскировку на vpn.example.com? Что будет:' && printf '%s' "$w" | grep -q '• сайт на домене перестанет открываться;' \
  && printf '%s' "$w" | grep -q 'веб-панель и страницы пользователей по адресам на домене перестанут открываться; прежний вход в панель (18) останется;' \
  && printf '%s' "$w" | grep -q 'файлы сайта останутся в /var/www/kaskad-site — включить снова: 19 → 6;' \
  && ctl_log | grep -qx 'site mode domain --json' && printf '%s' "$w" | grep -q '✓ Сайт-маскировка выключена: домен и сертификат на месте, файлы сайта остались\.' \
  && printf '%s' "$o" | grep -qx 'Сайт-маскировка: выключена — сертификат без сайта' && [ "$(printf '%s' "$o" | fit)" = "3 ok" ] \
  && ok "выключить: последствия (сайт и панель на домене перестанут открываться, файлы останутся), 1 — site mode domain; 80×24" \
  || bad "выключить" "$(ctl_log) | $(printf '%s' "$o" | fit) | $o"
sitef status.json "$(site_on)"; rm -f "$FAKE_DIR/site/mode_domain.status"
sitef mode_domain.json '{"ok": false, "reason": "nginx_test", "text": "nginx не принял конфигурацию без сайта: bad. Файл сайта возвращён, ничего не выключено.", "error": "x", "code": 3}'; sitef mode_domain.rc 3
o=$(printf '6\n1\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$w" | grep -q '✗ Сайт не выключен: nginx не принял настройки\.' && printf '%s' "$w" | grep -q 'ничего не выключено' \
  && ok "выключить: отказ nginx_test — словами" || bad "выключить: отказ" "$o"
# убрать домен при включённом сайте — сначала о сайте
o=$(printf '5\n2\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$w" | grep -q 'сайт-маскировка выключится: сайт и панель на домене перестанут открываться, файлы сайта останутся' \
  && ok "5 «Убрать домен» при сайте: сайт выключится — в последствиях" || bad "убрать домен с сайтом" "$o"

# сменить шаблон: Enter — случайный сразу; каталог — карточка с предупреждением; отказ шаблона; нет ключа GitHub
use_root site40-tpl; fake_ctl; engine_nft; tpl_files
sitef status.json "$(site_on)"
sitef template_set_random.json '{"ok": true, "id": "gh_dist", "name": "Dist Layout", "source": "https://github.com/fx/dist", "fallback": false, "backup": "/var/backups/kaskad/site/previous", "tries": 1, "errors": [], "warnings": [], "root": "/var/www/kaskad-site"}'
o=$(printf '7\n\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
ctl_log | grep -qx 'site template set --random --json' && printf '%s' "$w" | grep -q 'Сейчас на сайте: «Good Business»\.' \
  && printf '%s' "$w" | grep -q '! Текущий сайт заменится этим шаблоном; прежний сохранится в резервной копии\.' \
  && printf '%s' "$w" | grep -q '\[\*\] Ставлю шаблон' && printf '%s' "$w" | grep -q '✓ Шаблон «Dist Layout» на сайте\.' \
  && printf '%s' "$w" | grep -q 'Прежний сайт сохранён: /var/backups/kaskad/site/previous\.' && [ "$(printf '%s' "$o" | fit)" = "3 ok" ] \
  && ok "7 → Enter: случайный одной клавишей (site template set --random), предупреждение о замене, резервная копия; 80×24" \
  || bad "сменить шаблон: случайный" "$(ctl_log) | $o"
sitef template_set_gh_landing_2.json '{"ok": true, "id": "gh_landing_2", "name": "Landing Two", "source": "x", "fallback": false, "backup": null, "tries": 1, "errors": [], "warnings": [], "root": "/var/www/kaskad-site"}'
: > "$CALLS/kaskadctl.log"
o=$(printf '7\n1\n2\n2\n1\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
ctl_log | grep -qx 'site templates --category=landing --page 1 --check --json' && printf '%s' "$w" | grep -q 'https://example.org/landing/2/' \
  && printf '%s' "$w" | grep -q 'Landing Template Number 2 With A Very Long Name — Лендинги' \
  && printf '%s' "$w" | grep -q '! Текущий сайт заменится этим шаблоном; прежний сохранится в резервной копии\.' \
  && ctl_log | grep -qx 'site template set gh_landing_2 --json' \
  && printf '%s' "$w" | grep -q '✓ Шаблон «Landing Two» на сайте\.' && [ "$(printf '%s' "$o" | fit)" = "6 ok" ] \
  && ok "7 → 1: категория → номер → карточка с превью и предупреждением → «Поставить?» (1 — да) → site template set <id>" \
  || bad "сменить шаблон: каталог" "$(ctl_log) | $(printf '%s' "$o" | fit) | $o"
sitef template_set_random.json '{"ok": false, "id": null, "reason": "download_failed", "text": "Шаблон «gh_x» не скачался: репозиторий недоступен или нет связи с GitHub.", "fallback": false, "backup": null, "tries": 3, "errors": [], "warnings": [], "root": "/var/www/kaskad-site"}'
sitef template_set_random.rc 1
o=$(printf '7\n\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$w" | grep -q '✗ Шаблон не поставлен: Шаблон «gh_x» не скачался: репозиторий недоступен или нет связи с GitHub\. Прежний сайт не тронут\.' \
  && ok "сменить шаблон: не скачался — текст ядра, «прежний сайт не тронут»" || bad "сменить шаблон: отказ" "$o"
sitef templates.json '{"ok": false, "error": "Каталог шаблонов сайта лежит в закрытом репозитории Kaskad, и без ключа GitHub его не скачать.", "code": 1, "data": {"reason": "nokey", "needs_key": true}}'
sitef templates.rc 1; : > "$CALLS/kaskadctl.log"
o=$(printf '7\n1\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$w" | grep -q '✗ Каталог шаблонов не скачать: нет ключа GitHub\.' && printf '%s' "$w" | grep -q 'Задайте ключ: главное меню → 8 → 6' \
  && ! ctl_log | grep -q 'template set gh' \
  && ok "нет ключа GitHub (nokey) — «Задайте ключ: главное меню → 8 → 6»" || bad "сменить шаблон: nokey" "$o"

# адрес панели: маска; показать — после предупреждения (n — не показывать); сменить — после «старый перестанет открываться сразу»
use_root site40-url; fake_ctl; engine_nft
sitef status.json "$(site_on)"
sitef panel-url_show.json "{\"ok\": true, \"url\": \"$FULL\", \"masked\": \"$MASK\"}"
NEWMASK='https://vpn.example.com/app-NeWpAtH0••••QwEr/'
sitef panel-path_rotate.json "{\"ok\": true, \"url\": \"$NEWMASK\", \"masked\": \"$NEWMASK\"}"
sitef panel-path_rotate.status "$(site_on | jq -c --arg m "$NEWMASK" '.site.panel_url_masked = $m')"
o=$(printf '8\n1\n2\n\n0\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip)
! ctl_log | grep -q 'panel-url' && ! printf '%s' "$o" | grep -q SECRET && printf '%s' "$o" | grep -qF "Веб-панель на домене: $MASK" \
  && ok "8: адрес маской; «показать» без «1» — ядро не спрашивает полный адрес" || bad "адрес панели: отказ показать" "$(ctl_log) | $o"
o=$(printf '8\n1\n1\n\n2\n1\n\n0\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$w" | grep -q '! Адрес — секрет: не пересылайте и не публикуйте его' && ctl_log | grep -qx 'site panel-url --show --json' \
  && printf '%s' "$o" | grep -qxF "$FULL" \
  && printf '%s' "$w" | grep -q 'старый перестанет открываться сразу — закладки и сохранённые ссылки придётся обновить' \
  && ctl_log | grep -qx 'site panel-path rotate --json' && printf '%s' "$w" | grep -qF "✓ Новый адрес панели: $NEWMASK. Полностью: 19 → 8 → 1." \
  && printf '%s' "$o" | grep -qF "Веб-панель на домене: $NEWMASK" && [ "$(printf '%s' "$o" | grep -vxF "$FULL" | FIT_H=23 fit)" = "5 ok" ] \
  && ok "8 → 1: предупреждение «адрес — секрет», 1 — полный адрес отдельной строкой (копируется целиком); 8 → 2: «старый перестанет открываться сразу», 1 — rotate, новая маска; 80×24" \
  || bad "адрес панели" "$(ctl_log) | $(printf '%s' "$o" | fit) | $o"
sitef panel-path_rotate.json '{"ok": false, "reason": "nginx_test", "text": "nginx не принял новый путь панели: bad. Старый путь оставлен.", "error": "x", "code": 3}'
sitef panel-path_rotate.rc 3; rm -f "$FAKE_DIR/site/panel-path_rotate.status"
o=$(printf '8\n2\n1\n\n0\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$w" | grep -q '✗ Адрес не сменён: nginx не принял настройки\.' && printf '%s' "$w" | grep -q 'Старый путь оставлен' \
  && ok "8 → 2: отказ nginx_test — старый адрес остался" || bad "адрес панели: отказ" "$o"

# проверить сайт: каждый вердикт словами
use_root site40-chk; fake_ctl; engine_nft
sitef status.json "$(site_on)"
site_verdict() {   # <JSON ответа> <код> <ожидаемая фраза>
    sitef check.json "$1"; sitef check.rc "$2"
    o=$(printf '9\n\n0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip); w=$(printf '%s' "$o" | flat)
    printf '%s' "$w" | grep -qF "$3" && [ "$(printf '%s' "$o" | fit)" = "3 ok" ]
}
site_verdict '{"ok": true, "verdict": "ok", "checked_at": "x"}' 0 "✓ Сайт в порядке: главная страница открывается, панель отвечает по секретному адресу, http ведёт на https, сертификат действует." \
  && ctl_log | grep -qx 'site check --json' && ok "9: ok — сайт, панель, перенаправление, сертификат" || bad "проверка: ok" "$o"
site_verdict '{"ok": false, "verdict": "dns_mismatch"}' 1 "✗ DNS домена не ведёт на этот сервер — сайт откроется не у всех. Проверьте A-запись: 19 → 1." \
  && ok "9: dns_mismatch" || bad "проверка: dns_mismatch" "$o"
site_verdict '{"ok": false, "verdict": "no_cert"}' 1 "✗ Нет действующего сертификата Let's Encrypt — браузеры не откроют сайт. Выпустите: 19 → 2." \
  && ok "9: no_cert" || bad "проверка: no_cert" "$o"
site_verdict '{"ok": false, "verdict": "http_error", "http": {"root": 502, "redirect": 200}}' 1 "✗ Сайт отвечает не так, как должен (главная — код 502, http — код 200)." \
  && ok "9: http_error — коды ответа" || bad "проверка: http_error" "$o"
site_verdict '{"ok": false, "verdict": "no_site"}' 1 "Сайт-маскировка выключена — проверять нечего. Включить: 19 → 6." \
  && ok "9: no_site" || bad "проверка: no_site" "$o"
site_verdict '{"ok": false, "verdict": "cert_expiring", "days_left": 9}' 1 "! Сайт отвечает, но сертификат скоро истекает (осталось 9 дн.). Продлить: 19 → 2." \
  && ok "9: cert_expiring — сколько дней" || bad "проверка: cert_expiring" "$o"
site_verdict '{"ok": false, "verdict": "panel_unreachable"}' 1 "✗ Сайт открывается, а панель по секретному адресу не отвечает. Проверьте веб-панель: 18." \
  && ok "9: panel_unreachable" || bad "проверка: panel_unreachable" "$o"

# весь раздел: секрет пути только там, где его попросили показать
grep -n 'panel-url --show' "$S" | grep -v '^[0-9]*:\s*#' | grep -q 'k3_site panel-url --show' && ! grep -q 'k3_log.*panel-url' "$S" \
  && ok "полный адрес панели берётся только в «показать полностью» и в журнал не пишется" || bad "секрет пути"

echo "── 41. T-N5 3.2.19-r2: номер — только цифры из списка, «Нет такого пункта» и вопрос снова (меню 6, 7, 8, 13, 14, 19…) ──"
use_root num; fake_ctl; engine_nft
two_ips() { get_target_ips() { printf '%s\n' 203.0.113.5 198.51.100.7; }; ping_live() { echo "LIVE-PING $1"; }; }
# 6 «Ping»: «y» раньше становился индексом −1 — пинг последнего сервера
t_ping() { two_ips; ping_menu; echo "RC=$?"; }
o=$(printf 'y\nу\n3\n-1\n2\n' | lib t_ping 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта: номер от 1 до 2, 0 — назад')" = 4 ] && printf '%s' "$o" | grep -qx 'LIVE-PING 198.51.100.7' \
  && [ "$(printf '%s\n' "$o" | grep -c 'LIVE-PING')" = 1 ] \
  && ok "6: y, у, 3, -1 — «Нет такого пункта» и вопрос снова; 2 — второй сервер" || bad "6: ping" "$o"
o=$(printf 'y\n0\n' | lib t_ping 2>&1 | strip)
! printf '%s' "$o" | grep -q 'LIVE-PING' && printf '%s' "$o" | grep -q 'Нет такого пункта' && ok "6: y, затем 0 — назад, пинга нет" || bad "6: y → 0" "$o"
o=$(printf '\n' | lib t_ping 2>&1 | strip)
! printf '%s' "$o" | grep -q 'LIVE-PING\|Нет такого' && ok "6: Enter — назад, как раньше" || bad "6: Enter" "$o"
# 7 «Мониторинг»: меню, «Сервер:», интервал, порог, уведомления, удаление
t_mon() { two_ips; systemctl() { :; }; k3_bot_py_active() { return 1; }; monitoring_menu; for f in "$MONITOR_DIR"/*.conf; do [ -f "$f" ] && { echo "CONF ${f##*/}"; cat "$f"; }; done; }
mkdir -p "$KASKAD_DIR/monitors"
o=$(printf 'y\n9\n1\ny\n2\nx\n3\nabc\n150\n9\n\n\n0\n' | lib t_mon 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта: номер от 1 до 2, 0 — назад')" = 3 ] \
  && printf '%s' "$o" | grep -q 'Нет такого пункта: введите номер от 1 до 3, 0 — назад (Enter — 2)' \
  && printf '%s' "$o" | grep -q 'Нет такого пункта: введите номер от 1 до 4, 0 — назад (Enter — 3)' \
  && printf '%s' "$o" | grep -qx 'Порог — число миллисекунд от 1 до 10000, например 150 (0 — назад).' \
  && ! printf '%s' "$o" | grep -q 'порт' \
  && printf '%s' "$o" | grep -qx 'CONF 198.51.100.7.conf' && printf '%s' "$o" | grep -qx 'MON_INTERVAL=300' \
  && printf '%s' "$o" | grep -qx 'MON_THRESHOLD=150' && printf '%s' "$o" | grep -qx 'MON_COOLDOWN=300' \
  && [ "$(printf '%s\n' "$o" | grep -c '^CONF ')" = 1 ] \
  && ok "7: y, 9 в меню и y в «Сервер:» — «Нет такого пункта»; монитор на выбранный сервер (2), не на последний; интервал и уведомления — цифрами; порог — миллисекунды, не «порт»" \
  || bad "7: мониторинг" "$o"
o=$(printf '1\n0\n2\ny\n5\n1\n\n0\n' | lib t_mon 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта: номер 1, 0 — назад')" = 2 ] && ! printf '%s' "$o" | grep -q '^CONF ' \
  && ! printf '%s' "$o" | grep -q 'от 1 до 1' \
  && ok "7 → 1: 0 в «Сервер:» — назад; 7 → 2: y, 5 — «Нет такого пункта: номер 1» (не «от 1 до 1»), 1 — удалён" || bad "7: удаление" "$o"
# 3.2.21 (T-N5 3.2.20, Н-2, Н-3): в интервале, пороге и уведомлениях 0 — назад, монитор не создаётся; порог — 1…10000
for in7 in '1\n2\n0\n' '1\n2\n2\n0\n' '1\n2\n2\ny\n10001\n0\n' '1\n2\n2\n150\n0\n' '1\n2\n2\n\n'; do
    o=$(printf "${in7}0\n" | lib t_mon 2>&1 | strip)
    ! printf '%s' "$o" | grep -q '^CONF ' && printf '%s' "$o" | grep -q '^ 0) Назад$' \
      && ok "7 → 1: «$(printf '%s' "$in7" | sed 's/\\n/ /g')» — назад, монитор не создан" || bad "7 → 1: назад ($in7)" "$o"
done
o=$(printf '1\n2\n2\ny\n10001\n0150\n1\n\n0\n' | lib t_mon 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c 'Порог — число миллисекунд от 1 до 10000, например 150 (0 — назад).')" = 2 ] \
  && printf '%s' "$o" | grep -qx 'MON_THRESHOLD=150' && printf '%s' "$o" | grep -qx 'MON_INTERVAL=60' && printf '%s' "$o" | grep -qx 'MON_COOLDOWN=10' \
  && ok "7 → 1: порог y и 10001 — «число миллисекунд от 1 до 10000, например 150», 0150 — 150" || bad "7 → 1: порог" "$o"
rm -f "$KASKAD_DIR/monitors"/*.conf
# бот на bash: порог — те же 1…10000 мс и тот же текст ошибки
t_botth() {
    K3_BOT=1; BOT_TOKEN="1:x"; mkdir -p "$BOT_STATE_DIR"
    for x in y 0 10001 " 150 "; do
        bot_set_state 42 "STATE=awaiting_threshold" "MON_IP=198.51.100.7" "MON_INTERVAL=60"; bot_handle_message 42 "$x"
    done
}
: > "$CALLS/tg.log"; lib t_botth >/dev/null 2>&1
[ "$(grep -c 'Порог — число миллисекунд от 1 до 10000, например 150.' "$CALLS/tg.log")" = 3 ] && grep -q '60с | 150мс' "$CALLS/tg.log" \
  && grep -q 'mc:198.51.100.7:60:150:' "$CALLS/tg.log" && ok "бот на bash: порог y, 0, 10001 — «число миллисекунд от 1 до 10000»; « 150 » — 150" \
  || bad "бот на bash: порог" "$(cat "$CALLS/tg.log")"
# 3.2.21 (критик, М-7): «0 — назад» в подсказке k3_choose — готовой строкой: ${back,,} в локали C кириллицу не понижает
t_back() { K3_CHOOSE_BACK="Назад" k3_choose "Вопрос" 2 "А" "Б" "В"; echo "RC=$?"; }
o=$(printf 'y\n0\n' | lib t_back 2>&1 | strip)
! grep -q 'back,,' "$S" && printf '%s' "$o" | grep -q 'введите номер от 1 до 3, 0 — назад (Enter — 2)' && printf '%s' "$o" | grep -q 'RC=2' \
  && ok "k3_choose с пунктом 0: «0 — назад» строкой, без \${back,,}; 0 — код 2" || bad "k3_choose: 0 — назад" "$o"
# Н-1: «Нет такого пункта» в меню, которое перерисовывает экран, — после перерисовки, над «Выбор:», до следующего ввода
t_mon1() { two_ips; systemctl() { :; }; k3_bot_py_active() { return 1; }; scr monitoring_menu; }
o=$(printf 'y\n\n0\n' | lib t_mon1 2>&1 | strip)
f2=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==3' | sed '/^$/d' | tail -n 2 | tr '\n' '|')
f3=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==4')
[ "$f2" = "0) Назад|Нет такого пункта: номер от 1 до 2, 0 — назад.|" ] && ! printf '%s' "$f3" | grep -q 'Нет такого' \
  && [ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта')" = 1 ] \
  && ok "7: «Нет такого пункта» — на перерисованном экране последней строкой (над «Выбор:»), после следующего ввода снято" \
  || bad "7: сообщение пропадает" "[$f2] $o"
# так же меню 8 и главное меню; в главном — на строке «Выбор:» (экран 80×24 занят целиком, лишней строки нет)
t_bot1() { systemctl() { return 3; }; scr bot_menu; }
o=$(printf 'y\n0\n' | lib t_bot1 2>&1 | strip)
[ "$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==3' | sed '/^$/d' | tail -n 1)" = "Нет такого пункта: номер от 1 до 9, 0 — назад." ] \
  && [ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта')" = 1 ] && ok "8: «Нет такого пункта» — после перерисовки, над «Выбор:»" || bad "8: сообщение" "$o"
t_main1() { scr show_menu; }
use_root num-main; fake_ctl; gw_st false '{}' 0 0
o=$(printf 'y\n0\n' | K3_TERM_COLS=80 lib t_main1 2>&1 | strip)
fr=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==3')
[ "$(printf '%s\n' "$fr" | sed '/^$/d' | tail -n 1)" = "Нет такого пункта: номер от 0 до 22. " ] && [ "$(printf '%s\n' "$fr" | sed '/^$/d' | wc -l | tr -d ' ')" -le 24 ] \
  && [ "$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==2' | sed '/^$/d' | wc -l | tr -d ' ')" -le 23 ] \
  && ok "главное меню: «Нет такого пункта» — на строке «Выбор:», экран по-прежнему 24 строки" || bad "главное меню: сообщение" "$fr"
use_root num; fake_ctl; engine_nft
# 8 «Telegram Bot»: неверный ввод — словами
t_bot8() { systemctl() { return 3; }; bot_menu; }
o=$(printf 'y\n10\n\n0\n' | lib t_bot8 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта: номер от 1 до 9, 0 — назад')" = 2 ] && ok "8: y, 10 — «Нет такого пункта», Enter — перерисовать" || bad "8: бот" "$o"
# 14 «Имена серверов»
t_alias() { two_ips; set_alias() { echo "ALIAS $1=$2"; }; set_alias_note() { :; }; manage_aliases_menu; }
: > "$CALLS/kaskadctl.log"; o=$(printf 'y\n2\nОфис\n\n\n0\n' | lib t_alias 2>&1 | strip)
printf '%s' "$o" | grep -q 'Нет такого пункта: номер от 1 до 2, 0 — назад' && ctl_log | grep -qx 'server rename 198.51.100.7 --name=Офис --via=ssh --json' \
  && ok "14: y — «Нет такого пункта», 2 — имя второму серверу" || bad "14: имена" "$o"
# 13 «Инструкция»: страница — номер из списка
t_help() { clear() { :; }; show_instructions; }
o=$(printf 'y\n99\n0\n' | lib t_help 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта: номер страницы от 1 до')" = 2 ] && ok "13: y, 99 — «Нет такого пункта»" || bad "13: инструкция" "$o"
# 19 «Домен»: 0 — назад, как везде (раньше ядро отвечало «"0" — не домен»)
use_root num19; fake_ctl; site_state ""
o=$(printf '0\n' | K3_TERM_COLS=80 lib t_site 2>&1 | strip)
! ctl_log | grep -q '^site domain' && ! printf '%s' "$o" | grep -q 'не домен' && ok "19: в вопросе «Домен» 0 — назад, ядро не зовётся" || bad "19: 0 — назад" "$(ctl_log) | $o"
# меню и вопросы шлюза, панели, правил: нет «тихой» ветки
t_pw() { k3ctl() { echo "CTL $*"; }; k3_panel_password; }
o=$(printf 'y\n3\n\n' | lib t_pw 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта: введите 1 или 2 (Enter — отмена)')" = 2 ] && ! printf '%s' "$o" | grep -q '^CTL' \
  && ok "18 → 2 «Пароль»: y, 3 — «Нет такого пункта», Enter — отмена" || bad "пароль панели" "$o"
t_rt() { k3ctl() { echo '{}'; }; k3_rules_do() { echo "RULES $*"; }; k3_gw_head() { :; }; k3_rules_target device d-1 Телефон; }
o=$(printf 'y\n4\n2\n' | lib t_rt 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта: номер от 1 до 3, 0 — назад')" = 2 ] && printf '%s' "$o" | grep -qx 'RULES profile device d-1 ru_direct' \
  && ok "профиль правил устройства: y, 4 — «Нет такого пункта», 2 — «Россия напрямую»" || bad "профиль правил" "$o"
# по всему install.sh: номер не превращается в арифметику ($((choice-1)) и т. п. без проверки цифр)
! grep -nE '\$\(\((choice|sc|dc|ch|nav|ic|cc|ans) *- *1\)\)' "$S" | grep -q . \
  && ok "нигде нет \$((ввод - 1)): номер — только цифры из списка (k3_read_num или проверка), арифметика — 10#" \
  || bad "арифметика по вводу" "$(grep -nE '\$\(\((choice|sc|dc|ch|nav|ic|cc|ans) *- *1\)\)' "$S")"

echo "── 42. T-N5 3.2.19-r2, Н-4: число и единица не разрываются переносом (k3_gw_fold, k3_say, k3_wrap) ──"
NU_LINE="ядро 6.8.0-146-generic, процессоров 1, ОЗУ 1,9 ГБ, на диске свободно 25,8 ГБ; сборка 1–3 мин, клиенты вернутся за 15–25 с, нагрузка ≈25 % против ≈50 %, компилятор ≈210 МБ."
t_nu() {
    local c pad
    for c in $(seq 24 100); do K3_TERM_COLS=$c k3_gw_fold "  " "$NU_LINE"; K3_PG_WRAP=$c k3_say "$NU_LINE"; done
    for pad in $(seq 0 70); do printf '%s %s\n' "$(printf '%*s' "$pad" "" | tr ' ' 'я')" "$NU_LINE"; done | k3_wrap
}
o=$(lib t_nu 2>&1 | strip)
torn=$(printf '%s\n' "$o" | grep -nE '^ *(ГБ|МБ|с|мин|%)([ .,;)]|$)' | head -3)
[ -n "$o" ] && [ -z "$torn" ] && [ "$(printf '%s\n' "$o" | grep -c '')" -gt 200 ] \
  && ok "«25,8 ГБ», «1–3 мин», «15–25 с», «≈25 %», «≈210 МБ» — ни при какой ширине (24–100) единица не начинает строку" \
  || bad "число оторвано от единицы" "$torn"
t_nu1() { K3_TERM_COLS=80 k3_gw_fold "  " "короткая строка 25,8 ГБ"; printf '%s\n' "строка 25,8 ГБ" | k3_wrap; }
o=$(lib t_nu1 2>&1 | strip)
[ "$o" = "$(printf 'короткая строка 25,8 ГБ\n  строка 25,8 ГБ')" ] && ! printf '%s' "$o" | grep -q $'\xc2\xa0' \
  && ok "в выводе обычный пробел (неразрывный — только на время переноса), короткая строка как есть" || bad "неразрывный пробел в выводе" "$(printf '%s' "$o" | od -c | head -5)"
# 3.2.21 (T-N5 3.2.20, Н-5): k3_wrap — по ширине окна (окно − 1, не шире 78, не уже 40), а не всегда 78
WR_LINE="Модуль ядра быстрее программы amneziawg-go: перейти на модуль можно сейчас, клиенты вернутся сами за 15–25 с, ничего выдавать заново не нужно."
t_wr() { local c; for c in 30 40 50 60 61 70 76 79 80 100; do printf '%s\n' "== $c"; printf '%s\n' "$WR_LINE" | K3_TERM_COLS=$c k3_wrap; done; }
o=$(lib t_wr 2>&1)
wr=$(printf '%s\n' "$o" | python3 -c '
import sys
cur, bad, widths = None, [], {}
for l in sys.stdin.read().split("\n"):
    if l.startswith("== "):
        cur = l[3:]; widths[cur] = 0; continue
    if cur is not None and l:
        widths[cur] = max(widths[cur], len(l))
want = {"30": 40, "40": 40, "50": 49, "60": 59, "61": 60, "70": 69, "76": 75, "79": 78, "80": 78, "100": 78}
for c, lim in want.items():
    if not (lim - 12 <= widths.get(c, -1) <= lim):
        bad.append("%s:%s>%d" % (c or "?", widths.get(c), lim))
print(",".join(bad) or "ok")')
[ "$wr" = ok ] && ok "k3_wrap: строки не шире окна − 1 (60 → 59, 70 → 69, 76 → 75), не шире 78 и не уже 40" \
  || bad "k3_wrap: ширина" "$wr $o"

echo "── 43. меню 15: резервные копии /var/backups/kaskad (в них секреты) — вопрос цифрами; худший экран в 80×24 (Н-7) ──"
# Худший набор: шлюз, панель, модуль ядра, домен с сертификатом Let's Encrypt, длинная строка пакетов (шлюз + модуль +
# домен), папка выдачи, восемь копий в /var/backups/kaskad.
un_worst() {
    use_root "$1"; fake_ctl; gw_st true '{"wg": {}, "awg2": {}}' 1 1; uninst_fixture
    jq '.site = {"domain": "vpn.example.com", "mode": "site", "cert": "letsencrypt"}' "$KASKAD_DIR/state.json" > "$W/st.json" \
        && mv "$W/st.json" "$KASKAD_DIR/state.json"
    mkdir -p "$KASKAD_DIR/site" "$KASKAD_ROOT/etc/letsencrypt/live/vpn.example.com" "$KASKAD_ROOT/root/kaskad-issue"
    printf 'certbot\ngit\n' > "$KASKAD_DIR/site/pkgs"
    local p j="{"
    for p in dkms gcc gcc-13 linux-headers-6.8.0-146-generic linux-headers-generic make patch wireguard-tools strongswan \
             strongswan-pki libcharon-extra-plugins ocserv openvpn certbot git; do
        j+="\"$p\": {\"by_kaskad\": true}, "; touch "$FAKE_DIR/pkg.$p"
    done
    printf '%s"cmake": {"by_kaskad": true}}\n' "$j" > "$KASKAD_DIR/gw/packages.json"; touch "$FAKE_DIR/pkg.cmake"
    for p in 20261001T010000Z-k3-3.2.13 awg-engine-20261008T011600Z awg-engine-20261008T012000Z site 1 2 3 4; do
        mkdir -p "$KASKAD_ROOT/var/backups/kaskad/$p"; printf 'PrivateKey = SECRET\n' > "$KASKAD_ROOT/var/backups/kaskad/$p/wg0.conf"
    done
    chmod 700 "$KASKAD_ROOT/var/backups/kaskad"
}
un_worst un43a
o=$(printf '2\n\n' | K3_ISSUE_DIR="$KASKAD_ROOT/root/kaskad-issue" lib t_un3 2>&1 | strip | sed "s#$KASKAD_ROOT##g")
r=$(printf '%s' "$o" | fit 'Отменено.'); w=$(printf '%s' "$o" | flat)
printf '%s' "$w" | grep -q 'Сертификат Let.s Encrypt домена' && printf '%s' "$w" | grep -q 'Ускорение AmneziaWG' && printf '%s' "$w" | grep -q 'веб-панель' \
  && printf '%s' "$w" | grep -q 'Домен: продление' && printf '%s' "$w" | grep -q '/root/kaskad-issue' \
  && printf '%s' "$w" | grep -q 'apt remove dkms gcc gcc-13 linux-headers-6.8.0-146-generic linux-headers-generic make patch wireguard-tools strongswan strongswan-pki libcharon-extra-plugins ocserv openvpn certbot git' \
  && [ "$r" = "1 ok" ] && [ -d "$KASKAD_ROOT/var/backups/kaskad/site" ] \
  && ok "15, худший случай (шлюз, панель, модуль, домен, 15 пакетов, папка выдачи, копии): экран подтверждения — 80×24" \
  || bad "15: худший экран" "$r | $o"
# пакетов ещё больше — список не влезает в три строки: число, а команда — в конце удаления
jq '. + {"amneziawg-dkms": {"by_kaskad": true}, "xl2tpd": {"by_kaskad": true}, "ppp": {"by_kaskad": true}, "libreswan-tools": {"by_kaskad": true}, "accel-ppp-extra-long-package-name": {"by_kaskad": true}, "nginx": {"by_kaskad": true}, "python3-certbot-nginx": {"by_kaskad": true}}' \
    "$KASKAD_DIR/gw/packages.json" > "$W/p.json" && mv "$W/p.json" "$KASKAD_DIR/gw/packages.json"
for p in amneziawg-dkms xl2tpd ppp libreswan-tools accel-ppp-extra-long-package-name nginx python3-certbot-nginx; do touch "$FAKE_DIR/pkg.$p"; done
o=$(printf '2\n\n' | K3_ISSUE_DIR="$KASKAD_ROOT/root/kaskad-issue" lib t_un3 2>&1 | strip | sed "s#$KASKAD_ROOT##g")
r=$(printf '%s' "$o" | fit 'Отменено.')
[ "$r" = "1 ok" ] && printf '%s' "$o" | grep -qx '  • Пакеты, которые ставил Kaskad (22): команда apt remove — в конце удаления' \
  && ok "15: 22 пакета — одной строкой «(22): команда apt remove — в конце удаления», экран 80×24" || bad "15: очень длинный список пакетов" "$r | $o"
! printf '%s' "$w" | grep -q 'удалите сами).*backups\|Копии /var/backups/kaskad (в них токен' \
  && ok "15: копии /var/backups/kaskad больше не в «НЕ будет затронуто» — о них вопрос после слова" || bad "15: строка про копии" "$o"
# слово → средства сборки → копии: Enter — 1 (удалить: в копиях ключи и пароли)
t_un43() {
    local words=("УДАЛИТЬ" "СТЕРЕТЬ" "СНЕСТИ" "УНИЧТОЖИТЬ" "ПРОЩАЙ") w
    RANDOM=7; w="${words[$((RANDOM % ${#words[@]}))]}"; RANDOM=7
    K3_ISSUE_DIR="$KASKAD_ROOT/root/kaskad-issue" full_uninstall < <(printf '1\n%s\n2\n%s\n\n' "$w" "$1")
}
o=$(lib t_un43 "" 2>&1 | strip | sed "s#$KASKAD_ROOT##g"); w=$(printf '%s' "$o" | flat)
printf '%s' "$w" | grep -q 'В /var/backups/kaskad 8 резервных копий Kaskad' \
  && printf '%s' "$w" | grep -q '1) Удалить и резервные копии Kaskad (в них ключи и пароли)' \
  && printf '%s' "$w" | grep -q '2) Оставить копии в /var/backups/kaskad' && printf '%s' "$w" | grep -q 'Выбор \[1\]' \
  && [ ! -e "$KASKAD_ROOT/var/backups/kaskad" ] && [ -d "$KASKAD_ROOT/var/backups" ] \
  && printf '%s' "$w" | grep -q '✓ Резервные копии /var/backups/kaskad удалены' \
  && [ -e "$FAKE_DIR/pkg.cmake" ] \
  && ok "15: «1) Удалить и резервные копии … / 2) Оставить копии …», Enter — 1: /var/backups/kaskad удалён целиком (сам /var/backups — нет)" \
  || bad "15: копии — удалить" "$(ls -la "$KASKAD_ROOT/var/backups" 2>&1) | $o"
un_worst un43b
o=$(lib t_un43 2 2>&1 | strip | sed "s#$KASKAD_ROOT##g"); w=$(printf '%s' "$o" | flat)
[ "$(ls "$KASKAD_ROOT/var/backups/kaskad" | wc -l | tr -d ' ')" = 8 ] && [ ! -e "$KASKAD_DIR" ] \
  && printf '%s' "$w" | grep -q 'Резервные копии остались в /var/backups/kaskad (в них ключи и пароли)' \
  && ok "15: 2 — копии остались (восемь каталогов), в итоге сказано, где они и что в них" || bad "15: копии — оставить" "$o"
un_worst un43c
o=$(printf '1\nНЕТ\n' | K3_ISSUE_DIR="$KASKAD_ROOT/root/kaskad-issue" lib t_un3 2>&1 | strip)
[ "$(ls "$KASKAD_ROOT/var/backups/kaskad" | wc -l | tr -d ' ')" = 8 ] && [ -e "$KASKAD_DIR" ] && ! printf '%s' "$o" | grep -q 'резервных копий' \
  && ok "15: неверное слово — ничего не удалено, о копиях не спрашивали" || bad "15: отмена словом" "$o"
use_root un43d; fake_ctl; engine_nft
o=$(lib t_un43 "" 2>&1 | strip)
! printf '%s' "$o" | grep -q 'резервных копий\|Резервные копии' && ok "15: копий нет — вопроса нет" || bad "15: без копий" "$o"
t_bkok() { local d; for d in / "" /var/backups /etc/kaskad "$KASKAD_ROOT/var/backups/kaskad" /var/backups/kaskad/; do K3_BACKUPS="$d" k3_backups_rm_ok && echo "YES $d"; done; }
[ "$(lib t_bkok 2>&1)" = "$(printf 'YES %s\nYES /var/backups/kaskad/' "$KASKAD_ROOT/var/backups/kaskad")" ] \
  && ok "15: каталог копий удаляется, только если это …/backups/kaskad (не «/», не /var/backups, не чужой путь)" || bad "15: проверка пути копий" "$(lib t_bkok 2>&1)"

echo "── 44. главное меню: шапка «что с сервером» (SPEC-ssh-menu), 80×24 и 76 столбцов, F1, 20 и 21, неверный номер ──"
# Снимок ядра — подделкой $FAKE_DIR/summary.json (сам снимок проверяют tests/test_summary.py и разделы 16, 34, 39, 40 —
# настоящим ядром). head_js <jq-правка> — снимок «шлюз, домен, панель, две проблемы, есть обновление» с правкой.
HEAD_BASE='{"ok": true, "version": "'"$CUR"'", "engine": "nft", "forwards": 4,
 "gateway": {"setup": true, "enabled": true, "host": "vpn.example.com", "users": 3, "devices": 12, "online": 5,
   "inbounds": [{"kind": "wg", "title": "WireGuard", "short": "WireGuard", "state": "run"},
                {"kind": "awg3", "title": "AmneziaWG 3.1", "short": "AWG 3.1", "state": "run"},
                {"kind": "l2tp", "title": "L2TP/IPsec", "short": "L2TP", "state": "down"},
                {"kind": "sstp", "title": "SSTP", "short": "SSTP", "state": "off"}]},
 "exits": {"total": 5, "alive": 3, "via": "pool", "fail": "block", "error": null},
 "awg": {"has": true, "active": "kernel", "offer": false},
 "site": {"domain": "vpn.example.com", "name": "vpn.example.com", "mode": "site",
          "cert": {"kind": "letsencrypt", "end": "30.12", "end_long": "30.12.2026", "days_left": 82}},
 "panel": {"installed": true, "public_url": null, "masked": "https://vpn.example.com/app-AbCdEfGh••••WxYz/"},
 "update": {"latest": "'"$NEWER"'", "branch": "k3", "at": 1790000000},
 "live": {"age": 3, "fresh": true}, "watch": {"age": 20}, "checked": false,
 "problems": [{"kind": "gw_inbound", "detail": "Вход L2TP/IPsec не работает", "item": 17, "source": "live",
               "advice": "Причина — в 17 → 5 «Состояние». Сторож перезапускает вход сам раз в минуту."},
              {"kind": "site_cert", "detail": "Сертификат домена vpn.example.com истекает 30.12.2026 (через 5 дн.): продление не сработало.",
               "item": 19, "advice": "Проверьте и продлите сертификат: 19 → 2.", "source": "rule"}]}'
head_js() { printf '%s' "$HEAD_BASE" | jq -c "${1:-.}" > "$FAKE_DIR/summary.json"; }
PANEL_UNIT_T() { printf '%s' "$KASKAD_ROOT/etc/systemd/system/kaskad-panel.service"; }
head_root() { use_root "$1"; fake_ctl; engine_nft; : > "$(PANEL_UNIT_T)"; echo kaskad-panel > "$FAKE_DIR/active"; }
t_head() { scr show_menu; }
# экран в окне шириной FIT_W с переносом длинных строк (адрес панели целиком): строк экрана с приглашением ≤ 24
wfit() { python3 -c '
import os, sys, unicodedata
c = int(os.environ.get("FIT_W") or 80)
def width(s):
    return sum(0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in "WF" else 1) for ch in s)
out = []
for s in sys.stdin.read().split("\f")[1:]:
    ls = s.split("\n")
    if ls and ls[-1] == "":
        ls.pop()
    h = sum(max(1, -(-width(l) // c)) for l in ls) + 1
    out.append("ok" if h <= 24 else str(h))
print(" ".join(out))'; }
# окна 80×24 и 76×24: всё на одном экране, ничего не шире окна
head_root head1; head_js
res=""
for c in 80 76; do
    o=$(K3_TERM_COLS=$c lib t_head <<< "0" 2>&1 | strip)
    f=$(printf '%s' "$o" | FIT_W=$c FIT_H=24 fit); [ "$f" = "1 ok" ] || res+=" ${c}:$f"
done
[ -z "$res" ] && ok "шапка со шлюзом, доменом, панелью и двумя проблемами: 80×24 и 76×24 — один экран, ни одна строка не шире окна" \
  || bad "шапка: окно" "$res $o"
o=$(K3_TERM_COLS=76 lib t_head <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qx "IP: 203.0.113.10 · пробросов: 4 (nftables) · есть ${NEWER}: 11" \
  && printf '%s' "$o" | grep -qx 'Домен: vpn.example.com · сайт · сертификат до 30.12' \
  && printf '%s' "$o" | grep -qx 'Панель: https://vpn.example.com/app-AbCdEfGh••••WxYz/ F1 или «п» — показать' \
  && printf '%s' "$o" | grep -qx 'Шлюз: ● WireGuard  ● AWG 3.1  ✗ L2TP  ○ SSTP · устройств: 12, в сети 5' \
  && printf '%s' "$o" | grep -qx '  ● работает  ✗ не работает  ○ выключен' \
  && printf '%s' "$o" | grep -qx 'Выходы: живы 3 из 5 · AmneziaWG: модуль ядра' \
  && printf '%s' "$o" | grep -qx 'Адрес для устройств: vpn.example.com' \
  && printf '%s' "$o" | grep -qx '✗ Вход L2TP/IPsec не работает' \
  && printf '%s' "$o" | grep -q '^  Причина — в 17 → 5 «Состояние».* · ещё 1 — 21$' \
  && printf '%s' "$o" | grep -q '21) Поиск неполадок (2)$' \
  && ok "шапка: IP и пробросы, «есть ${NEWER}: 11», домен, панель маской, входы значками и легенда, «живы 3 из 5», первая проблема с советом и «ещё 1 — 21»" \
  || bad "шапка: строки" "$o"
o=$(K3_TERM_COLS=76 lib t_head <<< "0" 2>&1)
printf '%s' "$o" | grep '21) ' | grep -qF "$(printf '\033[0;31mПоиск неполадок (2)')" && printf '%s' "$o" | grep '^.*✗ Вход' | grep -qF "$(printf '\033[0;31m✗ Вход')" \
  && ok "проблемы — красным: строка «✗ …» и пункт «21) Поиск неполадок (2)»" || bad "шапка: цвет проблем" "$(printf '%s' "$o" | grep -a '21)')"
o=$(K3_KEEP_NO_COLOR=1 NO_COLOR=1 K3_TERM_COLS=76 lib t_head <<< "0" 2>&1)
! printf '%s' "$o" | grep -q "$(printf '\033')\[" && printf '%s' "$o" | grep -q '✗ L2TP' \
  && ok "NO_COLOR: ни одного кода цвета — состояние значками ● ✗ ○ и словами" || bad "NO_COLOR" "$(printf '%s' "$o" | head -5 | od -c | head -5)"
# шлюза нет, всё в порядке; ветка обновлений записана
head_root head2; head_js '.gateway = {"setup": false, "enabled": false, "host": "", "users": 0, "devices": 0, "online": null, "inbounds": []} | .exits = null | .site = null | .panel.installed = false | .panel.masked = null | .problems = [] | .update = null'
rm -f "$(PANEL_UNIT_T)"; printf 'KASKAD_BRANCH="k3"\n' > "$KASKAD_DIR/config"
o=$(K3_TERM_COLS=76 lib t_head <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qx 'IP: 203.0.113.10 · пробросов: 4 (nftables) · ветка k3' \
  && printf '%s' "$o" | grep -qx 'Шлюз: не настроен — телефоны и роутеры через этот сервер: 17' \
  && printf '%s' "$o" | grep -qx '✓ Неполадок не видно · подробная проверка — 21' \
  && ! printf '%s' "$o" | grep -q '^Домен:\|^Панель:\|^Выходы:' && printf '%s' "$o" | grep -q '21) Поиск неполадок$' \
  && [ "$(printf '%s' "$o" | FIT_W=76 fit)" = "1 ok" ] \
  && ok "без шлюза: «Шлюз: не настроен — …: 17», «✓ Неполадок не видно», ветка k3 в строке IP; лишних строк нет" || bad "шапка без шлюза" "$o"
# К2-3 (T-KT2): проблем нет, а здоровье сервера (пункт 22) с замечаниями — не «✓ Неполадок не видно»
head_root head2w; head_js '.gateway = {"setup": false, "enabled": false, "host": "", "users": 0, "devices": 0, "online": null, "inbounds": []} | .exits = null | .site = null | .panel.installed = false | .panel.masked = null | .problems = [] | .update = null | .host = {"level": "warn"}'
rm -f "$(PANEL_UNIT_T)"
o=$(K3_TERM_COLS=76 lib t_head <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qx '! Здоровье сервера: есть предупреждения — 22' && ! printf '%s' "$o" | grep -q 'Неполадок не видно' \
  && ok "шапка: здоровье сервера с предупреждениями — «! Здоровье сервера: есть предупреждения — 22», без «Неполадок не видно»" || bad "шапка: здоровье сервера" "$o"
# Владелец 28.10 «статусы одними словами везде»: строка без проблем говорит словами вердикта Сводки панели (ovb.v.*):
# ok — «Работает нормально», off — «Работает, проверки здоровья выключены», wait — «Ждём данных сервера», crit —
# «требует внимания» (ovb.hh.crit); шлюз выключен или свет никто не судил — прежнее «Неполадок не видно»
for lv in "ok|✓ Работает нормально · подробная проверка — 21" "off|○ Работает, проверки здоровья выключены · подробная проверка — 21" \
          "wait|? Ждём данных сервера · подробная проверка — 21" "crit|✗ Здоровье сервера: требует внимания — 22"; do
    head_root "head2-${lv%%|*}"; head_js '.gateway = {"setup": false, "enabled": false, "host": "", "users": 0, "devices": 0, "online": null, "inbounds": []} | .exits = null | .site = null | .panel.installed = false | .panel.masked = null | .problems = [] | .update = null | .host = {"level": "'"${lv%%|*}"'"}'
    rm -f "$(PANEL_UNIT_T)"
    o=$(K3_TERM_COLS=76 lib t_head <<< "0" 2>&1 | strip)
    printf '%s' "$o" | grep -qx "${lv#*|}" && ! printf '%s' "$o" | grep -q 'Неполадок не видно' \
      && ok "шапка: свет здоровья ${lv%%|*} — «${lv#*|}»" || bad "шапка: свет здоровья ${lv%%|*}" "$o"
done
head_root head2-gwoff; head_js '.gateway.enabled = false | .exits = null | .problems = [] | .host = {"level": "ok"}'
o=$(K3_TERM_COLS=76 lib t_head <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -q '^Шлюз: ○ выключен' && printf '%s' "$o" | grep -qx '✓ Неполадок не видно · подробная проверка — 21' \
  && ! printf '%s' "$o" | grep -q 'Работает нормально' \
  && ok "шапка: шлюз выключен, здоровье ok — не «Работает нормально», а «✓ Неполадок не видно»" || bad "шапка: шлюз выключен, здоровье ok" "$o"
# нет выходов: устройства без интернета — красным, проблема ядра первой
head_root head3; head_js '.exits = {"total": 0, "alive": 0, "via": "pool", "fail": "block", "error": null} | .problems = [{"kind": "no_exits", "item": 17, "detail": "Нет интернета у устройств: ни один выход не работает, а при отказе выходов стоит «блокировать». Добавьте рабочий ключ выхода или смените маршрут по умолчанию.", "advice": "Добавьте рабочий ключ выхода (17 → 3) или смените маршрут по умолчанию (17 → 3 → 1).", "source": "rule"}]'
o=$(K3_TERM_COLS=76 lib t_head <<< "0" 2>&1); so=$(printf '%s' "$o" | strip)
printf '%s' "$so" | grep -qx 'Выходы: нет — устройства без интернета — 17 → 3 · AmneziaWG: модуль ядра' \
  && printf '%s' "$o" | grep '^.*Выходы:' | grep -qF "$(printf '\033[0;31mнет — устройства без интернета')" \
  && printf '%s' "$so" | grep -q '^✗ Нет интернета у устройств: ни один выход не работает, а при отказе выход…$' \
  && printf '%s' "$so" | grep -q '^  Добавьте рабочий ключ выхода (17 → 3) .* · подробно — 21$' \
  && [ "$(printf '%s' "$so" | FIT_W=76 fit)" = "1 ok" ] \
  && ok "без выходов: «Выходы: нет — устройства без интернета» красным, проблема ядра обрезана «…» в строку, совет и «подробно — 21»" \
  || bad "шапка без выходов" "$so"
head_js '.exits = {"total": 0, "alive": 0, "via": "direct", "fail": null, "error": null} | .problems = []'
o=$(K3_TERM_COLS=76 lib t_head <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -q '^Выходы: нет — устройства выходят в интернет с адреса сервера' && ok "маршрут «напрямую»: выходов нет — и не нужно, без красного" || bad "выходы: напрямую" "$(printf '%s' "$o" | grep '^Выходы')"
# много входов: не влезают — работающие числом; и так не влезают — числа по значкам
ALLIN='[{"short":"WireGuard"},{"short":"AWG 1.5"},{"short":"AWG 2.0"},{"short":"AWG 3.1"},{"short":"L2TP"},{"short":"IKEv2"},{"short":"SSTP"},{"short":"OpenVPN"},{"short":"OpenConnect"},{"short":"SOCKS5"},{"short":"PPTP"}]'
head_js ".gateway.inbounds = ($ALLIN | to_entries | map(.value + {state: (if .key == 4 then \"down\" elif .key == 9 then \"unknown\" else \"run\" end)}))"
o=$(K3_TERM_COLS=76 lib t_head <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qx 'Шлюз: ● работают 9  ✗ L2TP  ? SOCKS5 · устройств: 12, в сети 5' && [ "$(printf '%s' "$o" | FIT_W=76 fit)" = "1 ok" ] \
  && ok "11 входов, 2 не работают: «● работают 9  ✗ L2TP  ? SOCKS5» — в 76 столбцов" || bad "много входов" "$(printf '%s' "$o" | grep '^Шлюз')"
head_js ".gateway.inbounds = ($ALLIN | to_entries | map(.value + {state: ([\"down\", \"off\", \"unknown\"][.key % 3])}))"
o=$(K3_TERM_COLS=76 lib t_head <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qx 'Шлюз: протоколов 11: ● 0  ✗ 4  ○ 4  ? 3 · устройств: 12, в сети 5' \
  && printf '%s' "$o" | grep -qx '  ● работает  ✗ не работает  ○ выключен  ? нет свежих данных' \
  && ok "11 входов, ни один не работает: числа по значкам и полная легенда" || bad "много входов: числа" "$(printf '%s' "$o" | grep -A1 '^Шлюз')"
# снимок берётся один раз на 5 с: неверный номер и Enter — без нового вызова ядра; после пункта — заново
head_root head4; head_js
o=$(printf '99\n\n13\n0\n0\n' | K3_TERM_COLS=80 lib t_head 2>&1 | strip)
fr2=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==3'); fr3=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==4')
[ "$(printf '%s\n' "$fr2" | sed '/^$/d' | tail -n 1)" = "Нет такого пункта: номер от 0 до 22. " ] \
  && [ "$(printf '%s\n' "$fr3" | sed '/^$/d' | tail -n 1)" = "Нет такого пункта: номер от 0 до 22. " ] \
  && [ "$(ctl_log | grep -cx 'summary --json')" = 2 ] \
  && ok "неверный номер: «Нет такого пункта: номер от 0 до 22.» держится и после Enter (до следующего ввода); снимок ядра — 1 раз до пункта 13 и 1 после" \
  || bad "неверный номер / кэш снимка" "$(ctl_log | tr '\n' ' ') | $fr3"
# F1 и «п»: адрес панели целиком на 5 минут, предупреждение; в журнал — нет
head_root head5; head_js; sitef panel-url_show.json "{\"ok\": true, \"url\": \"$FULL\", \"masked\": \"$MASK\"}"
for key in 'п' $'\eOP' $'\e[11~'; do
    # клавиша приходит без Enter (терминал шлёт последовательность целиком); «п» — с Enter
    if [ "$key" = п ]; then in="п"$'\n'; else in="$key"; fi
    o=$(printf '%s0\n' "$in" | K3_TERM_COLS=76 lib t_head 2>&1 | strip)
    s2=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==3')
    if printf '%s' "$s2" | grep -qx "Панель: $FULL" && printf '%s' "$s2" | grep -qx 'Адрес — секрет, как пароль: не показывайте экран. Скрою через 5 мин.' \
       && [ "$(printf '%s' "$o" | FIT_W=76 wfit)" = "ok ok" ] && ctl_log | grep -qx 'site panel-url --show --json'; then :; else res="$key"; break; fi
done
[ -z "${res:-}" ] && ! grep -q SECRET "$KASKAD_ROOT/var/log/kaskad.log" 2>/dev/null \
  && ok "F1 (\\eOP, \\e[11~) и «п»: адрес панели целиком в шапке на 5 минут с предупреждением; экран 76×24; в журнал не попал" \
  || bad "F1: адрес панели" "$(printf '%q' "${res:-}") $s2"
head_js '.panel.masked = null'; : > "$CALLS/kaskadctl.log"
o=$(printf 'п\n0\n' | K3_TERM_COLS=76 lib t_head 2>&1 | strip)
printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==3' | grep -q 'Панель не открыта из интернета — показывать нечего (пункты 18, 19). $' \
  && ! ctl_log | grep -q 'panel-url' && ok "«п» без адреса панели из интернета — словами, ядро не спрашивается" || bad "F1 без панели" "$o"
# панель стоит, но не работает — красная строка и проблема (пункт 18)
: > "$FAKE_DIR/active"; head_js '.problems = []'
o=$(K3_TERM_COLS=76 lib t_head <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qx 'Панель: ✗ не работает — 18' && printf '%s' "$o" | grep -qx '✗ Веб-панель не работает (служба kaskad-panel.service).' \
  && ok "панель не работает: «Панель: ✗ не работает — 18» и проблема с пунктом 18" || bad "панель не работает" "$o"
# 20: быстрая настройка в браузере — k3_setup_menu (ветка k3-setup, раздел 46); экран — в 80×24
head_root head6; head_js
o=$(printf '20\n0\n0\n' | K3_TERM_COLS=80 lib t_head 2>&1 | strip)
printf '%s' "$o" | grep -q 'Быстрая настройка (в браузере)' && printf '%s' "$o" | grep -q '━━━ 🚀 Быстрая настройка в браузере ━━━' \
  && printf '%s' "$o" | grep -q 'Сейчас: не запущена' && ctl_log | grep -qx 'setup status --json' \
  && [ "$(printf '%s' "$o" | fit)" = "3 ok" ] && ok "20 → k3_setup_menu: экран быстрой настройки (setup status), 80×24" || bad "20: быстрая настройка" "$(printf '%s' "$o" | fit) $o"
t_head20() { k3_setup_menu() { echo "SETUP-MENU"; }; scr show_menu; }
o=$(printf '20\n0\n' | lib t_head20 2>&1 | strip)
printf '%s' "$o" | grep -qx 'SETUP-MENU' && ok "20: зовётся k3_setup_menu" || bad "20: k3_setup_menu" "$o"
# 21: поиск неполадок — summary --check, все проблемы с советом и пунктом, переход цифрой
head_js '.checked = true' && cp "$FAKE_DIR/summary.json" "$FAKE_DIR/summary_check.json"; head_js '.problems = []'
t_head21() { k3_site_menu() { echo "SITE-MENU"; }; scr show_menu; }
o=$(printf '21\n99\n19\n0\n' | K3_TERM_COLS=76 lib t_head21 2>&1 | strip); w=$(printf '%s' "$o" | flat)
printf '%s' "$o" | grep -q '━━━ Поиск неполадок ━━━' && printf '%s' "$o" | grep -qx 'Что не так: 2' \
  && printf '%s' "$o" | grep -qx '✗ Вход L2TP/IPsec не работает' \
  && printf '%s' "$w" | grep -q 'Пункт 17: Причина — в 17 → 5 «Состояние». Сторож перезапускает вход сам раз в минуту.' \
  && printf '%s' "$w" | grep -q 'Пункт 19: Проверьте и продлите сертификат: 19 → 2.' \
  && printf '%s' "$w" | grep -q "Есть новая версия ${NEWER} — пункт 11." \
  && printf '%s' "$o" | grep -qx 'Нет такого пункта: номер от 1 до 22, Enter — в меню.' \
  && printf '%s' "$o" | grep -qx 'SITE-MENU' && ctl_log | grep -qx 'summary --check --json' \
  && [ "$(printf '%s' "$o" | FIT_W=76 fit 'SITE-MENU')" = "3 ok" ] \
  && ok "21: «Поиск неполадок» — проверка сейчас (summary --check), обе проблемы с «Пункт N: совет», обновление; 99 — отказ, 19 — переход; 76×24" \
  || bad "21: проверка сервера" "$(ctl_log | tr '\n' ' ') | $o"
head_js '.problems = []'; cp "$FAKE_DIR/summary.json" "$FAKE_DIR/summary_check.json"
o=$(printf '21\n\n0\n' | lib t_head 2>&1 | strip)
printf '%s' "$o" | grep -qx '✓ Неполадок не найдено.' && ok "21 без неполадок: «✓ Неполадок не найдено.»" || bad "21: без неполадок" "$o"
# ядро не отвечает: шапка из state.json, красная строка, номера прежние; старый движок — пробросы через iptables
head_root head7; rm -f "$K3_OPT/bin/kaskadctl"
printf '{\n  "engine": "legacy",\n  "schema": 1\n}\n' > "$KASKAD_DIR/state.json"
printf '%s\n' '-p tcp -m tcp --dport 8443 -m comment --comment kaskad:r1 -j DNAT --to-destination 198.51.100.7:443' > "$IPT_DIR/nat.PREROUTING"
o=$(K3_TERM_COLS=76 lib t_head <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qx 'IP: 203.0.113.10 · пробросов: 1 (iptables)' && ! printf '%s' "$o" | grep -q 'Ядро Kaskad 3' \
  && [ "$(printf '%s' "$o" | FIT_W=76 fit)" = "1 ok" ] \
  && ok "старый движок без ядра (как 2.x): «пробросов: 1 (iptables)» — из iptables, без красной строки" || bad "шапка: legacy" "$o"
echo "── 45. VLESS Reality в меню 17: мастер, «Протоколы», карточка (сайт маскировки, проверка, ключи узла), 17 → 9 ──"
# мастер: VLESS — девятым пунктом, не отмечен; двузначные номера (10 — PPTP); порядок входов — после SOCKS5
t_vpick() { K3_TEST_TTY=1; K3_GW_PICK=""; k3_gw_pick_protocols; echo "RC=$?"; k3_gw_expand; echo "SEL=${K3_GW_SEL[*]}"; }
use_root vless-pick; fake_ctl; engine_nft
o=$(printf '11\n9\n\n' | K3_TERM_COLS=80 lib t_vpick 2>&1 | strip)
printf '%s' "$o" | grep -q '^  \[ \]  9) VLESS$' && printf '%s' "$o" | flat | grep -q 'телефоны и компьютеры: v2RayTun, Hiddify, Streisand; с сайтом узла — TLS на 443, без — Reality' \
  && printf '%s' "$o" | grep -q '^  \[ \] 10) PPTP — слабое шифрование$' && printf '%s' "$o" | grep -q 'Нет такого пункта: номер от 1 до 10' \
  && printf '%s' "$o" | grep -q '^SEL=wg awg1 awg2 awg3 l2tp vless$' \
  && ok "мастер: «9) VLESS» не отмечен, с подписью; 11 — «нет такого пункта», 9 — отметить; VLESS — после SOCKS5" || bad "мастер: VLESS" "$o"
o=$(printf '10\n1\n\n' | K3_TERM_COLS=80 lib t_vpick 2>&1 | strip)
printf '%s' "$o" | grep -q 'PPTP шифрует слабо' && printf '%s' "$o" | grep -q '^SEL=wg awg1 awg2 awg3 l2tp pptp$' \
  && ok "мастер: двузначный номер 10 — PPTP (с предупреждением)" || bad "мастер: номер 10" "$o"

# «Протоколы» и карточка VLESS: порт, сайт маскировки и его проверка, устройства; пункты подряд
use_root vless-card; fake_ctl
gw_st true '{"wg": {"enabled": true, "port": 51820}, "vless": {"enabled": true, "port": 443}}' 2 1
gwf inbound_list.json "$(gw_list "wg" | jq -c '.inbounds += [{"kind": "vless", "title": "VLESS", "engine": "", "available": true, "installed": true, "enabled": true, "running": true, "ports": ["443/tcp"], "devices": 2, "sessions": 1}]')"
gwf vless.show.json '{"ok": true, "kind": "vless", "enabled": true, "port": 443, "security": "reality", "mask_list": "int", "sni": "www.microsoft.com", "dest": "www.microsoft.com:443", "mask": "list", "mask_check": {"ok": true, "tls": "TLSv1.3", "alpn": "h2", "h2": true, "why": "", "at": "2026-10-09T10:00:00Z"}, "pub": "PUBKEY", "short_id": "ab12", "fp": "chrome", "flow": "xtls-rprx-vision", "own": "vpn.example.com", "devices": 2, "host": "203.0.113.10", "node_domain": "vpn.example.com", "reason": "no_site", "forced": true, "reason_text": "сайт на домене не включён"}'
VMASKS='{"ok": true, "current": "www.microsoft.com", "checked": false, "masks": [
 {"sni": "vpn.example.com", "title": "свой сайт узла", "mask": "own", "current": false, "check": null},
 {"sni": "www.microsoft.com", "title": "Microsoft", "mask": "list", "current": true, "check": null},
 {"sni": "dl.google.com", "title": "Google (загрузки)", "mask": "list", "current": false, "check": null}]}'
gwf vless.masks.json "$VMASKS"
gwf vless.mask.json '{"ok": true, "kind": "vless", "old": "www.microsoft.com", "sni": "dl.google.com", "dest": "dl.google.com:443", "mask": "list", "mask_check": {"ok": true, "tls": "TLSv1.3", "alpn": "h2", "h2": true, "why": "", "at": "x"}, "changed": true, "reissue": [{"id": "d-00000001", "name": "Телефон", "platform": "android", "user": "u-00000001", "user_name": "Мама"}], "text": "Сайт маскировки VLESS сменился (www.microsoft.com → dl.google.com) — выданные раньше ссылки VLESS не подключатся. Выдайте их заново (меню 17 → 2 → 1 или панель → «Пользователи»): Телефон (Мама).", "apply": null}'
t_vproto() { K3_TEST_TTY=1; k3_gw_protocols; }
t_vcard() { K3_TEST_TTY=1; k3_gw_proto_card vless; }
o=$(printf '12\n0\n0\n' | K3_TERM_COLS=80 lib t_vproto 2>&1 | strip)
printf '%s' "$o" | grep -q '^ *12) VLESS *443/tcp *работает · устройств 2, сессий 1' && printf '%s' "$o" | grep -q 'Шлюз: VLESS' \
  && printf '%s' "$o" | grep -q 'Состояние: работает · порт: 443/tcp · устройств: 2 · сессий: 1' \
  && printf '%s' "$o" | grep -q '^Режим: Reality — маскировка под чужой сайт (зарубежные сайты)\.$' \
  && printf '%s' "$o" | grep -q '^Сайт маскировки: www.microsoft.com (из списка) · проверка: ✓ TLS 1.3, HTTP/2$' \
  && printf '%s' "$o" | grep -q '^ 2) Сменить порт$' && printf '%s' "$o" | grep -q '^ 3) Маскировка VLESS$' \
  && printf '%s' "$o" | grep -q '^ 4) Новые ключи узла$' && printf '%s' "$o" | grep -q '^ 5) Удалить протокол$' \
  && ! printf '%s' "$o" | grep -q 'Режим по сайту узла\|Сменить сайт маскировки' \
  && gw_calls | grep -qx 'gw vless show --json' \
  && ok "«Протоколы» → VLESS: режим Reality, порт, устройства, сайт маскировки с проверкой (gw vless show); «Маскировка VLESS» вместо двух пунктов, номера подряд" || bad "карточка VLESS" "$(gw_seq) | $o"
# смена сайта: номер из списка → gw vless mask <сайт>; итог — кому выдать ссылки заново
: > "$CALLS/kaskadctl.log"
o=$(printf '3\n2\n3\n\n0\n0\n' | K3_TERM_COLS=80 lib t_vcard 2>&1 | strip)
printf '%s' "$o" | grep -q '^  1) vpn.example.com    свой сайт узла$' && printf '%s' "$o" | grep -q '^  2) www.microsoft.com  Microsoft$' \
  && printf '%s' "$o" | grep -q '^Сейчас: www.microsoft.com$' \
  && printf '%s' "$o" | grep -q '^  4) Проверить все сайты с узла$' && printf '%s' "$o" | grep -q '^  5) Ввести свой сайт$' \
  && printf '%s' "$o" | grep -q '^  6) Подобрать автоматически$' \
  && gw_calls | grep -qx 'gw vless masks --json' && gw_calls | grep -qx 'gw vless mask dl.google.com --json' \
  && printf '%s' "$o" | grep -q '\[OK\] Сайт маскировки: dl.google.com — ✓ TLS 1.3, HTTP/2\.' \
  && printf '%s' "$o" | flat | grep -q 'Выдайте их заново (меню 17 → 2 → 1 или панель → «Пользователи»): Телефон (Мама)\.' \
  && ok "«Маскировка VLESS» → 2 (вынужденный Reality — сразу список): список (свой сайт узла — первым), номер → gw vless mask dl.google.com; кому выдать заново" \
  || bad "смена сайта маскировки" "$(gw_seq) | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '3\n2\n1\n\n0\n0\n' | lib t_vcard 2>&1 | strip)
gw_calls | grep -qx 'gw vless mask own --json' && ok "свой сайт узла → gw vless mask own" || bad "маскировка: свой сайт" "$(gw_seq)"
: > "$CALLS/kaskadctl.log"
o=$(printf '3\n2\n6\n\n0\n0\n' | lib t_vcard 2>&1 | strip)
gw_calls | grep -qx 'gw vless mask auto --json' && ok "«Подобрать автоматически» → gw vless mask auto" || bad "маскировка: авто" "$(gw_seq)"
# отказ проверки (код 2) → причина, «1) Выбрать другой 2) Всё равно взять (без проверки) 0) Назад»; 2 → --no-check
gwf vless.mask.once.json '{"ok": false, "error": "badsite.example не подходит для маскировки: сайт ответил TLSv1.2, а нужен TLS 1.3.", "code": 2, "data": {"kind": "mask_check"}}'
gwf vless.mask.once.rc 2
: > "$CALLS/kaskadctl.log"
o=$(printf '3\n2\n5\nbadsite.example\n2\n\n0\n0\n' | K3_TERM_COLS=80 lib t_vcard 2>&1 | strip)
gw_calls | grep -qx 'gw vless mask badsite.example --json' && gw_calls | grep -qx 'gw vless mask badsite.example --no-check --json' \
  && printf '%s' "$o" | flat | grep -q 'badsite.example не подходит для маскировки: сайт ответил TLSv1.2, а нужен TLS 1.3.' \
  && printf '%s' "$o" | grep -q '1) Выбрать другой$' && printf '%s' "$o" | grep -q '2) Всё равно взять (без проверки)$' \
  && printf '%s' "$o" | grep -q '0) Назад$' && printf '%s' "$o" | grep -q '\[OK\] Сайт маскировки' \
  && ok "сайт не прошёл проверку → причина и выбор цифрами; «2» → gw vless mask … --no-check" || bad "маскировка: без проверки" "$(gw_seq) | $o"
gwf vless.mask.once.json '{"ok": false, "error": "badsite.example не подходит для маскировки: сайт не ответил за 5 с.", "code": 2, "data": {"kind": "mask_check"}}'
gwf vless.mask.once.rc 2
: > "$CALLS/kaskadctl.log"
o=$(printf '3\n2\n5\nbadsite.example\n1\n0\n0\n0\n' | lib t_vcard 2>&1 | strip)
[ "$(gw_calls | grep -c '^gw vless mask ')" = 1 ] && [ "$(printf '%s\n' "$o" | grep -c 'Шлюз: VLESS — сайт маскировки (Reality)')" = 2 ] \
  && ok "«1) Выбрать другой» — снова список, без --no-check" || bad "маскировка: выбрать другой" "$(gw_seq) | $o"
# «Проверить все сайты с узла» → gw vless masks --check, результат — у каждого сайта
gwf vless.masks.once.json "$VMASKS"
gwf vless.masks.json "$(printf '%s' "$VMASKS" | jq -c '.checked = true | .masks[0].check = {"ok": true, "tls": "TLSv1.3", "alpn": "http/1.1", "h2": false, "why": ""}
    | .masks[1].check = {"ok": true, "tls": "TLSv1.3", "alpn": "h2", "h2": true, "why": ""}
    | .masks[2].check = {"ok": false, "tls": "", "alpn": "", "h2": false, "why": "сайт не ответил за 5 с"}')"
: > "$CALLS/kaskadctl.log"
o=$(printf '3\n2\n4\n0\n0\n0\n' | K3_TERM_COLS=80 lib t_vcard 2>&1 | strip)
gw_calls | grep -qx 'gw vless masks --check --json' \
  && printf '%s' "$o" | grep -q '^  1) vpn.example.com    свой сайт узла · ✓ TLS 1.3 (без HTTP/2)$' \
  && printf '%s' "$o" | grep -q '^  3) dl.google.com      ✗ сайт не ответил за 5 с$' \
  && ! gw_calls | grep -q 'gw vless mask ' \
  && ok "«Проверить все сайты с узла» → gw vless masks --check; проверка — у каждого сайта" || bad "маскировка: проверить все" "$(gw_seq) | $o"
# список маскировки в 80×24: свой сайт и 8 сайтов, все с длинной причиной отказа
gwf vless.masks.json "$(jq -cn '{"ok": true, "current": "www.microsoft.com", "checked": true, "masks": ([{"sni": "vpn.example.com", "title": "свой сайт узла", "mask": "own"}]
    + ([["www.microsoft.com","Microsoft"],["dl.google.com","Google (загрузки)"],["www.apple.com","Apple"],["www.samsung.com","Samsung"],["www.nvidia.com","NVIDIA"],["addons.mozilla.org","Mozilla"],["ya.ru","Яндекс"],["vk.com","ВКонтакте"]]
      | map({"sni": .[0], "title": .[1], "mask": "list"}))
    | map(. + {"current": (.sni == "www.microsoft.com"), "check": {"ok": false, "tls": "", "alpn": "", "h2": false, "why": "сайт недоступен с узла (Connection refused)"}}))}')"
t_vmasks() { scr k3_gw_vless_masks; }
o=$(printf '0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_vmasks 2>&1 | strip)
f=$(printf '%s' "$o" | FIT_W=80 FIT_H=24 fit)
[ "$f" = "1 ok" ] && printf '%s' "$o" | grep -q '^  9) vk.com  *✗ сайт недоступен с узла (Connection refused)$' && printf '%s' "$o" | grep -q '^ 12) Подобрать автоматически$' \
  && ok "сайт маскировки, 80×24: девять сайтов с причинами отказа — экран не выше и не шире окна" || bad "маскировка: 80×24" "$f | $o"
# «Новые ключи узла»: только после подтверждения цифрой (Enter и 2 — отмена) → gw vless rekey --yes
gwf vless.rekey.json '{"ok": true, "kind": "vless", "created": ["ключ узла VLESS"], "pub": "NEWPUB", "short_id": "cd34", "reissue": [{"id": "d-00000001", "name": "Телефон", "platform": "android", "user": "u-00000001", "user_name": "Мама"}], "text": "Ключи узла VLESS сменились — выданные раньше ссылки VLESS не подключатся. Выдайте их заново (меню 17 → 2 → 1 или панель → «Пользователи»): Телефон (Мама).", "apply": null}'
: > "$CALLS/kaskadctl.log"
o=$(printf '4\n\n4\n2\n0\n' | lib t_vcard 2>&1 | strip)
! gw_calls | grep -q 'gw vless rekey' && printf '%s' "$o" | flat | grep -q 'все выданные ссылки VLESS перестанут подключаться' \
  && printf '%s' "$o" | grep -q '1) Сменить ключи$' && printf '%s' "$o" | grep -q 'Выбор \[2\]' \
  && ok "«Новые ключи узла»: Enter и «2» — отмена, ядро не зовётся" || bad "rekey: отмена" "$(gw_seq) | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '4\n1\n\n0\n' | lib t_vcard 2>&1 | strip)
gw_calls | grep -qx 'gw vless rekey --yes --json' && printf '%s' "$o" | grep -q '\[OK\] Новые ключи узла VLESS\.' \
  && printf '%s' "$o" | flat | grep -q 'Ключи узла VLESS сменились — выданные раньше ссылки VLESS не подключатся' \
  && ok "«Новые ключи узла»: «1» → gw vless rekey --yes; кому выдать ссылки заново" || bad "rekey" "$(gw_seq) | $o"
# смена порта VLESS — как у других входов (3.3.7.15 F9: --dry → «Применить» → --yes; у VLESS — и «с откатом»)
gwf inbound_set.vless.json '{"ok": true, "kind": "vless", "port": 8443, "changed": {"port": {"old": 443, "new": 8443}}, "via_xray": true, "lines": ["VLESS: что изменится — порт 443 → 8443"], "reissue": [], "pending": null}'
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n8443\n1\n\n0\n' | lib t_vcard 2>&1 | strip)
gw_calls | grep -qx 'gw inbound set vless --port 8443 --dry --json' && gw_calls | grep -qx 'gw inbound set vless --port 8443 --yes --json' \
  && printf '%s' "$o" | grep -q 'VLESS: порт 8443' \
  && ok "VLESS: «Сменить порт» → gw inbound set vless --port 8443 (--dry, затем --yes)" || bad "VLESS: порт" "$(gw_seq) | $o"
rm -f "$FAKE_DIR/gw/inbound_set.vless.json"
# режим TLS со своим доменом: строка режима; нет порта, сайта маскировки и ключей Reality; «Маскировка VLESS» → 1
# (текущий режим) ничего не меняет и ядро не зовёт
gwf vless.show.json '{"ok": true, "kind": "vless", "enabled": true, "port": 443, "security": "tls", "domain": "vpn.example.com", "sni": "www.microsoft.com", "mask": "list", "mask_check": null, "pub": "PUBKEY", "short_id": "ab12", "fp": "chrome", "flow": "xtls-rprx-vision", "own": "vpn.example.com", "devices": 2, "host": "203.0.113.10", "node_domain": "vpn.example.com", "reason": "", "forced": false}'
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n1\n\n0\n0\n' | K3_TERM_COLS=80 lib t_vcard 2>&1 | strip)
printf '%s' "$o" | flat | grep -q 'Режим: TLS со своим доменом vpn.example.com — порт 443; всё постороннее открывает сайт узла\.' \
  && ! printf '%s' "$o" | grep -q 'Сменить порт\|Сменить сайт маскировки\|Новые ключи узла\|^Сайт маскировки' \
  && printf '%s' "$o" | grep -q '^ 2) Маскировка VLESS$' && printf '%s' "$o" | grep -q '^ 3) Удалить протокол$' \
  && printf '%s' "$o" | grep -q 'Выбор \[1\]' && printf '%s' "$o" | grep -q 'VLESS уже на TLS со своим доменом — ничего не меняется\.' \
  && ! gw_calls | grep -q 'gw vless sync\|gw vless mode' \
  && ok "VLESS в режиме TLS: строка режима, без порта и Reality; «Маскировка VLESS» → 1 (по умолчанию) — ничего не меняется" || bad "VLESS: TLS" "$(gw_seq) | $o"

# 17 → 9: VLESS, как SOCKS5, — вход xray шлюза; выбранный вход уходит ядру (--inbound vless)
use_root vless-trace; fake_ctl
gw_st true '{"wg": {}, "vless": {}}' 1 1
gwf user_list.json '{"ok": true, "users": [{"id": "u-00000001", "name": "Мама", "route": null, "enabled": true, "devices": 1}]}'
gwf device_list.json '{"ok": true, "devices": [
 {"id": "d-00000001", "user": "u-00000001", "name": "Телефон", "kind": "phone", "platform": "android", "login": "t-1", "access": ["wg", "vless"], "route": null, "enabled": true}]}'
o=$(printf '1\n2\nya.ru\n\n' | K3_TERM_COLS=80 lib t_trace3 2>&1 | strip)
ctl_log | grep -qx 'gw trace d-00000001 ya.ru --inbound vless' && printf '%s' "$o" | grep -q '^ *2) VLESS$' \
  && printf '%s' "$o" | flat | grep -q 'SOCKS5 и VLESS решает xray' && printf '%s' "$o" | grep -q 'Телефон через VLESS' \
  && ok "17 → 9: «через какой вход» — VLESS (как SOCKS5, решает xray), ядру — --inbound vless" || bad "17 → 9: VLESS" "$(ctl_log) | $o"
echo "── 45h. Hysteria2 в «Протоколы»: только строка с состоянием (T12, D-050/D-051) ──"
use_root hy2-line; fake_ctl
gw_st true '{"wg": {"enabled": true, "port": 51820}, "hy2": {"enabled": true, "port": 443}}' 1 0
gwf inbound_list.json "$(gw_list "wg" | jq -c '.inbounds += [{"kind": "hy2", "title": "Hysteria2", "engine": "xray", "available": true, "installed": true, "enabled": true, "running": true, "ports": ["443/udp", "20000–20099/udp"], "devices": 1, "sessions": 0}]')"
gwf hy2.show.json '{"ok": true, "kind": "hy2", "installed": true, "enabled": true, "port": 443, "hop": "20000-20099", "obfs": true, "cert": "domain", "cert_mode": "self", "pin": "ab12", "domain": "", "devices": 1, "ports_view": [{"proto": "udp", "port": "443"}, {"proto": "udp", "port": "20000–20099"}], "warnings": []}'
t_h2proto() { K3_TEST_TTY=1; k3_gw_protocols; }
t_h2card() { K3_TEST_TTY=1; k3_gw_hy2_card; }
n_h2=$(jq '.inbounds | length' "$FAKE_DIR/gw/inbound_list.json")
o=$(printf '%s\n7\n0\n0\n' "$n_h2" | K3_TERM_COLS=80 lib t_h2proto 2>&1 | strip)
printf '%s' "$o" | flat | grep -q 'Hysteria2 — вкл., порты 443/udp, 20000–20099/udp (смена портов: вкл.), Salamander: вкл., сертификат: самоподписанный ⚠' \
  && printf '%s' "$o" | flat | grep -q 'UDP/QUIC: блокировки в мобильных сетях РФ не проверены' \
  && printf '%s' "$o" | flat | grep -q 'Настройка — в панели («Протоколы» → Hysteria2) или kaskadctl gw hy2 show' \
  && printf '%s' "$o" | grep -q 'Здесь только 0 — назад\.' \
  && ! printf '%s' "$o" | sed -n '/Шлюз: Hysteria2/,$p' | grep -q '^ [1-9]) ' \
  && gw_calls | grep -qx 'gw hy2 show --json' && ! gw_calls | grep -q 'gw inbound \(install\|uninstall\|enable\|disable\|port\|option\) hy2' \
  && ok "«Протоколы» → Hysteria2: строка состояния (порты, смена портов, Salamander, ⚠ самоподписанный, UDP не проверен), пунктов настройки нет" \
  || bad "строка Hysteria2" "$(gw_seq) | $o"
o=$(printf '0\n' | KASKAD_LANG=en K3_TERM_COLS=80 lib t_h2card 2>&1 | strip)
printf '%s' "$o" | flat | grep -q 'Hysteria2 — on, ports 443/udp, 20000–20099/udp (port hopping: on), Salamander: on, certificate: self-signed ⚠' \
  && printf '%s' "$o" | flat | grep -q 'UDP/QUIC: blocking in Russian mobile networks has not been checked' \
  && ok "Hysteria2 по-английски (KASKAD_LANG=en)" || bad "Hysteria2: en" "$o"
gwf hy2.show.json '{"ok": true, "kind": "hy2", "installed": true, "enabled": false, "port": 8443, "hop": null, "obfs": false, "cert": "domain", "cert_mode": "domain", "pin": "", "domain": "example.com", "devices": 0, "ports_view": [{"proto": "udp", "port": "8443"}], "warnings": []}'
o=$(printf '0\n' | K3_TERM_COLS=80 lib t_h2card 2>&1 | strip)
printf '%s' "$o" | flat | grep -q 'Hysteria2 — выкл., порты 8443/udp (смена портов: выкл.), Salamander: выкл., сертификат: домен' \
  && ! printf '%s' "$o" | grep -q '⚠' && ok "Hysteria2 с доменом: «сертификат: домен», без ⚠" || bad "Hysteria2: домен" "$o"

echo "── 46. быстрая настройка в браузере: выбор после установки, ссылка, QR, меню ──"
SETUP_OKTEXT="Сертификат выпущен"
SETUP_URL="https://203.0.113.67:41234/setup-AbCdEfGhIjKlMnOpQrStUvWxYz012345/?t=TOKENtokenTOKENtokenTOKENtoken12345"
setup_fake() {
    mkdir -p "$FAKE_DIR/setup"
    printf '{"ok": true, "url": "%s", "expires": 1791200000, "password": false, "cert": {"state": "%s", "text": "%s"}, "fingerprint": "%s", "port": 41234}\n' \
        "$SETUP_URL" "${1:-ok}" "${2:-$SETUP_OKTEXT}" "${3:-}" > "$FAKE_DIR/setup/start.json"
    printf '{"ok": true, "url": "%s", "expires": 1791200000, "password": false, "bind": false}\n' "$SETUP_URL" > "$FAKE_DIR/setup/link.json"
    printf '{"ok": true, "url": "%s", "expires": 1791200000, "password": false, "bind": true}\n' "$SETUP_URL" > "$FAKE_DIR/setup/link.bind.json"
}
t_qs() { K3_TEST_TTY=1; k3_gw_menu() { echo "GW-MENU"; }; k3_setup_offer_fresh; echo "DONE"; }
use_root qs1; fake_ctl; engine_nft; setup_fake; : > "$KASKAD_DIR/gw-offer.pending"; : > "$CALLS/qr_in.log"
o=$(printf '\n\n' | lib t_qs 2>&1 | strip)
printf '%s' "$o" | grep -q '^ *Как настроить Kaskad?' && printf '%s' "$o" | grep -q '1) Квик-старт в браузере — пошагово, с видео (рекомендуется)' \
  && printf '%s' "$o" | grep -q '2) Классически, здесь в SSH' && printf '%s' "$o" | grep -q 'Выбор \[1\]:' \
  && ok "первая установка: «Как настроить Kaskad? 1) Квик-старт в браузере … 2) Классически, здесь в SSH», Выбор [1]" || bad "вопрос квик-старта" "$o"
ctl_log | grep -qx 'setup start --json' && printf '%s' "$o" | grep -qF "$SETUP_URL" && printf '%s' "$o" | grep -q '\[QR\]' \
  && printf '%s' "$o" | grep -q 'Действует до .* · порт 41234/tcp должен быть открыт у хостера' && ! printf '%s' "$o" | grep -q 'Сертификат' \
  && ! printf '%s' "$o" | grep -q 'В браузере — мышкой' && [ ! -e "$KASKAD_DIR/gw-offer.pending" ] \
  && ok "Enter — 1: setup start, ссылка целиком, QR, одна строка «срок · порт» (сертификат выпущен — ни слова); отметка «предложить шлюз» снята" || bad "квик-старт: ссылка" "$(ctl_log) | $o"
grep -qF "$SETUP_URL" "$CALLS/qr_in.log" && ! grep -qF "TOKENtoken" "$CALLS/qrencode.log" \
  && ok "QR: ссылка с токеном уходит в qrencode через stdin, не в argv" || bad "QR через stdin" "$(cat "$CALLS/qrencode.log")"
! grep -q 'TOKENtoken' "$KASKAD_ROOT/var/log/kaskad.log" 2>/dev/null && ok "токен ссылки не попадает в журнал" || bad "токен в журнале"
use_root qs2; fake_ctl; engine_nft; setup_fake; : > "$KASKAD_DIR/gw-offer.pending"
o=$(printf 'abc\n2\n' | lib t_qs 2>&1 | strip)
! ctl_log | grep -q '^setup ' && [ -e "$KASKAD_DIR/gw-offer.pending" ] && printf '%s' "$o" | grep -q 'Нет такого пункта: введите номер от 1 до 2 (Enter — 1)' \
  && ok "«abc» — снова вопрос; «2» — классически: мастер в браузере не запускается, вопрос про шлюз останется" || bad "квик-старт: 2" "$(ctl_log) | $o"
use_root qs3; fake_ctl; engine_nft; setup_fake fallback "Порт 80/tcp занят: nginx. Работаем на своём сертификате." "AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89"
: > "$KASKAD_DIR/gw-offer.pending"
o=$(printf '1\n\n' | lib t_qs 2>&1 | strip | flat)
printf '%s' "$o" | grep -q 'Сертификат на IP не выпущен: Порт 80/tcp занят: nginx' && printf '%s' "$o" | grep -q 'Отпечаток SHA-256: AB:CD' \
  && printf '%s' "$o" | grep -qF "$SETUP_URL" && ok "сертификат не вышел — честная причина и отпечаток, ссылка всё равно есть" || bad "квик-старт без сертификата" "$o"
# T-M6-qs330 №14: ссылка уже на домен (меню 20 → 1 после этапа «Домен») — строка про сертификат по факту, не «на IP»
SETUP_URL_IP=$SETUP_URL; SETUP_URL="https://m6.example.com:41234/setup-AbCdEfGhIjKlMnOpQrStUvWxYz012345/"
use_root qs3d; fake_ctl; engine_nft
setup_fake domain "Ссылка на домен m6.example.com: сертификат Let's Encrypt домена — браузер не будет ругаться."
t_qsd() { k3_setup_show start; }
o=$(lib t_qsd 2>&1 | strip | flat)
! printf '%s' "$o" | grep -q 'на IP' && printf '%s' "$o" | grep -qF "$SETUP_URL" \
  && ok "ссылка на домен: ссылка и срок, ни слова про сертификат на IP" || bad "квик-старт: ссылка на домен" "$o"
SETUP_URL=$SETUP_URL_IP
use_root qs4; fake_ctl; engine_nft; setup_fake
o=$(printf '1\n' | lib t_qs 2>&1 | strip)
! printf '%s' "$o" | grep -q 'Как настроить' && ! ctl_log | grep -q '^setup ' && ok "не первая установка (нет отметки) — не спрашивает" || bad "квик-старт при обновлении" "$o"
use_root qs5; fake_ctl; engine_nft; setup_fake; : > "$KASKAD_DIR/gw-offer.pending"; echo 1 > "$FAKE_DIR/setup/start.rc"; echo '{"ok": false}' > "$FAKE_DIR/setup/start.json"
o=$(printf '1\n\n' | lib t_qs 2>&1 | strip)
printf '%s' "$o" | grep -q 'Ссылка не получена (код 1)' && printf '%s' "$o" | grep -q 'DONE' && ok "ядро не дало ссылку — понятная строка, установка идёт дальше" || bad "квик-старт: отказ ядра" "$o"
# первая установка подряд, как run_startup (K3_FRESH=1, переносить было нечего): выбрали браузер и ссылка есть — вопроса
# про шлюз в SSH нет ни сейчас, ни при следующем запуске; ссылки нет — SSH спрашивает про шлюз, как раньше
t_qs2() { K3_TEST_TTY=1; K3_FRESH=1; K3_SILENT_SWITCH=1; k3_gw_is_setup() { return 1; }; k3_gw_menu() { echo "GW-MENU"; }
          k3_setup_offer_fresh; k3_gw_offer_fresh; echo "DONE"; }
use_root qs7; fake_ctl; engine_nft; setup_fake; : > "$KASKAD_DIR/gw-offer.pending"
o=$(printf '1\n\n1\n' | lib t_qs2 2>&1 | strip)
printf '%s' "$o" | grep -qF "$SETUP_URL" && ! printf '%s' "$o" | grep -q 'телефоны и роутеры?' && ! printf '%s' "$o" | grep -q 'GW-MENU' \
  && [ ! -e "$KASKAD_DIR/gw-offer.pending" ] && printf '%s' "$o" | grep -q 'DONE' \
  && ok "браузер выбран, ссылка есть: вопроса про шлюз в SSH нет, отметка снята" || bad "квик-старт и вопрос про шлюз" "$o"
use_root qs8; fake_ctl; engine_nft; setup_fake; : > "$KASKAD_DIR/gw-offer.pending"; echo 1 > "$FAKE_DIR/setup/start.rc"; echo '{"ok": false}' > "$FAKE_DIR/setup/start.json"
o=$(printf '1\n\n1\n' | lib t_qs2 2>&1 | strip)
printf '%s' "$o" | grep -q 'Ссылка не получена' && printf '%s' "$o" | grep -q 'Хотите подключить к этому серверу телефоны и роутеры?' \
  && printf '%s' "$o" | grep -q 'GW-MENU' && ok "ссылки нет: дальше SSH спрашивает про шлюз (1 — мастер шлюза)" || bad "квик-старт без ссылки и шлюз" "$o"
body=$(sed -n '/^run_startup() {/,/^}/p' "$S")
pr=$(printf '%s\n' "$body" | grep -n '^    show_promo' | cut -d: -f1); qs=$(printf '%s\n' "$body" | grep -n '^    k3_setup_offer_fresh' | cut -d: -f1)
of=$(printf '%s\n' "$body" | grep -n '^    k3_gw_offer_fresh' | cut -d: -f1)
[ -n "$qs" ] && [ "$pr" -lt "$qs" ] && [ "$qs" -lt "$of" ] && ok "run_startup: выбор «браузер или SSH» — после промо, до вопроса про шлюз" || bad "порядок run_startup" "pr=$pr qs=$qs of=$of"
t_qsm() { scr k3_setup_menu; }
use_root qs6; fake_ctl; engine_nft; setup_fake
o=$(printf '1\n\n2\n\n0\n' | lib t_qsm 2>&1 | strip)
printf '%s' "$o" | grep -q 'Быстрая настройка в браузере' && printf '%s' "$o" | grep -q 'Сейчас: не запущена' \
  && printf '%s' "$o" | grep -q ' 1) Показать ссылку и QR' && printf '%s' "$o" | grep -q ' 2) Ссылка только для моего IP' \
  && ctl_log | grep -qx 'setup start --json' && ctl_log | grep -qx 'setup link --bind-ip --json' \
  && ok "k3_setup_menu: не запущена — 1 запускает (start), 2 — ссылка только для моего IP; выбор цифрами" || bad "меню быстрой настройки" "$(ctl_log) | $o"
printf '{"ok": true, "status": "active", "expires": 1791200000, "password_set": true}\n' > "$FAKE_DIR/setup/status.json"
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n\n3\n1\n\n0\n' | lib t_qsm 2>&1 | strip)
printf '%s' "$o" | grep -q 'Сейчас: идёт · вход по паролю панели' && ctl_log | grep -qx 'setup link --json' && ! ctl_log | grep -q 'setup start' \
  && ctl_log | grep -qx 'setup off' && ok "идёт — 1 даёт новую ссылку (link), 3 → 1 останавливает мастер" || bad "меню: идёт" "$(ctl_log) | $o"
o=$(printf '9\n0\n' | lib t_qsm 2>&1 | strip)
printf '%s' "$o" | grep -q 'Нет такого пункта: номер от 1 до 3, 0 — назад' && ok "меню: неверный номер — словами" || bad "меню: неверный ввод" "$o"
o=$(lib t_qsm </dev/null 2>&1 | strip)
[ "$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==2' | sed '/^$/d' | wc -l | tr -d ' ')" -le 23 ] && ok "меню быстрой настройки — в окне 80×24" || bad "меню 80×24" "$o"
grep -q '^    --bot-start) init_config; start_bot </dev/null; exit 0 ;;' "$S" && ok "режим --bot-start: бот запускается без вопросов (шаг «Бот» мастера)" || bad "нет --bot-start"

echo "── 47. T-M6 №1: однострочник bash <(curl …) и curl … | bash — копия себя байт в байт, перезапуск с файла ──"
# Без root и сети: вместо curl — cat/head. Оба bash: системный (на macOS 3.2) и Homebrew 5.x (с LANG — иначе SIGSEGV).
# На bash 3.2 сама программа (declare -g) не работает, но загрузка K3_G обязана: её читает любой bash.
BOOT_BASHES=$(for b in /bin/bash /opt/homebrew/bin/bash /usr/local/bin/bash "$(command -v bash)"; do [ -x "$b" ] && readlink -f "$b"; done | awk '!seen[$0]++')
bn=0
for BB in $BOOT_BASHES; do
  bn=$((bn+1)); bv=$(LANG=en_US.UTF-8 "$BB" -c 'echo $BASH_VERSION'); BT="$W/boot$bn"; mkdir -p "$BT"
  o=$(TMPDIR="$BT" K3_BOOT_KEEP=1 LANG=en_US.UTF-8 "$BB" <(cat "$S") --help 2>&1); rc=$?
  cp_f=$(ls -d "$BT"/kaskad-install.*/install.sh 2>/dev/null | head -1)
  [ "$rc" = 0 ] && printf '%s' "$o" | grep -q '^Usage: gokaskad' && [ -n "$cp_f" ] && cmp -s "$cp_f" "$S" \
    && [ "$(stat -f %Lp "${cp_f%/*}" 2>/dev/null || stat -c %a "${cp_f%/*}")" = 700 ] \
    && ok "bash $bv: bash <(cat install.sh) — копия во временном каталоге 0700 равна install.sh байт в байт, программа идёт с неё" \
    || bad "bash $bv: bash <(…)" "rc=$rc $o | $(ls -la "$BT"/* 2>&1)"
  rm -rf "$BT"/*
  o=$(cat "$S" | TMPDIR="$BT" K3_BOOT_KEEP=1 K3_BOOT_TTY=/dev/null LANG=en_US.UTF-8 "$BB" -s -- --help 2>&1); rc=$?
  cp_f=$(ls -d "$BT"/kaskad-install.*/install.sh 2>/dev/null | head -1)
  [ "$rc" = 0 ] && printf '%s' "$o" | grep -q '^Usage: gokaskad' && [ -n "$cp_f" ] && cmp -s "$cp_f" "$S" \
    && ok "bash $bv: cat install.sh | bash — копия равна install.sh, продолжение с терминалом" || bad "bash $bv: … | bash" "rc=$rc $o"
  rm -rf "$BT"/*
  # без K3_BOOT_KEEP копия удаляется при выходе (ловушка EXIT)
  o=$(TMPDIR="$BT" LANG=en_US.UTF-8 "$BB" <(cat "$S") --help 2>&1); rc=$?
  [ "$rc" = 0 ] && [ -z "$(ls -A "$BT")" ] && ok "bash $bv: временная копия удалена при выходе" || bad "bash $bv: копия осталась" "$(ls -la "$BT")"
  # обрезанная загрузка: ни программы, ни копии
  for cut in 1500000 $(( $(wc -c < "$S") - 1 )); do
    o=$(TMPDIR="$BT" LANG=en_US.UTF-8 "$BB" <(head -c "$cut" "$S") 2>&1); rc=$?
    o2=$(head -c "$cut" "$S" | TMPDIR="$BT" K3_BOOT_TTY=/dev/null LANG=en_US.UTF-8 "$BB" 2>&1); rc2=$?
    [ "$rc" = 1 ] && [ "$rc2" = 1 ] && printf '%s' "$o" | grep -q 'скачан не целиком' && printf '%s' "$o2" | grep -q 'скачан не целиком' \
      && printf '%s' "$o" | grep -q 'downloaded incompletely — nothing changed, run the install command again' \
      && ! printf '%s%s' "$o" "$o2" | grep -q 'KASKAD PRO' && [ -z "$(ls -A "$BT")" ] \
      && ok "bash $bv: обрезанный файл ($cut байт) → «скачан не целиком», код 1, ничего не запущено, копии нет" \
      || bad "bash $bv: обрезанный файл $cut" "rc=$rc/$rc2 $o | $o2 | $(ls -A "$BT")"
  done
  # curl … | bash без терминала и без аргументов: правильная команда и выход, ничего не менялось
  o=$(cat "$S" | TMPDIR="$BT" K3_BOOT_TTY="$W/no-tty" LANG=en_US.UTF-8 "$BB" 2>&1); rc=$?
  [ "$rc" = 1 ] && printf '%s' "$o" | grep -qF 'bash <(curl -fsSL https://raw.githubusercontent.com/anten-ka/kaskad-pro/' \
    && printf '%s' "$o" | grep -q 'нет терминала для ответов' && printf '%s' "$o" | grep -q 'has no terminal for answers' && [ -z "$(ls -A "$BT")" ] \
    && ok "bash $bv: curl | bash без терминала → подсказка «bash <(curl -fsSL …)», код 1, копии нет" || bad "bash $bv: | bash без tty" "rc=$rc $o"
done
[ "$bn" -ge 1 ] || bad "нет bash для проверки загрузки"
# запуск файлом и source --lib: загрузка не срабатывает (копии нет), чужой K3_BOOT_TMP не удаляется
BT="$W/boot-file"; mkdir -p "$BT/foreign"; : > "$BT/foreign/keep"
o=$(TMPDIR="$BT" K3_BOOT_TMP="$BT/foreign" bash "$S" --help 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$(ls -A "$BT")" = foreign ] && [ -f "$BT/foreign/keep" ] && ok "запуск файлом: временной копии нет, чужой K3_BOOT_TMP не тронут" || bad "запуск файлом" "$o | $(ls -A "$BT")"
o=$(TMPDIR="$BT" bash -c 'source "$1" --lib; echo "SELF=$K3_SELF DIR=[$K3_BOOT_DIR]"' _ "$S" 2>&1)
[ "$o" = "SELF=$S DIR=[]" ] && [ "$(ls -A "$BT")" = foreign ] && ok "source install.sh --lib: загрузка молчит, K3_SELF — сам файл" || bad "source --lib" "$o"
[ "$(head -2 "$S" | tail -1 | cut -c1-6)" = "K3_G='" ] && [ "$(tail -n 1 "$S")" = "# K3_EOF: конец install.sh (по этой строке установщик проверяет, что файл скачан целиком)" ] \
  && [ "$(head -c 16384 "$S" | grep -c '^KASKAD_VERSION="')" = 1 ] \
  && ok "install.sh: загрузка — вторая строка, маркер конца — последняя, версия в первых 16 КБ (k3_branch_version, goInstaller)" || bad "начало или конец install.sh"

# После перезапуска: шаг 3 и копия в /opt — с временного файла, пути в юнитах и настройках — постоянные.
t_boot() {
    K3_BOOT_DIR="$BOOTD"; K3_SELF="$BOOTD/install.sh"
    k3_install_cmd; echo "CMD=$? ERR=[$K3_CMD_ERR]"
    k3_ensure_lib >/dev/null 2>&1; echo "LIB=$?"
    k3_boot_done; echo "SELF=$K3_SELF DIR=[$K3_BOOT_DIR]"
}
use_root boot; export BOOTD="$W/boot-run/kaskad-install.TEST"; mkdir -p "$BOOTD"; cp "$S" "$BOOTD/install.sh"
printf '#!/bin/bash\nKASKAD_VERSION="2.13"\necho old\nesac\n' > "$KASKAD_ROOT/usr/local/bin/gokaskad"
o=$(lib t_boot 2>&1)
printf '%s' "$o" | grep -q 'CMD=0 ERR=\[\]' && printf '%s' "$o" | grep -q 'LIB=0' && printf '%s' "$o" | grep -qF "SELF=$K3_OPT/bin/gokaskad DIR=[]" \
  && cmp -s "$S" "$KASKAD_ROOT/usr/local/bin/gokaskad" && cmp -s "$S" "$K3_OPT/bin/gokaskad" && [ -x "$KASKAD_ROOT/usr/local/bin/gokaskad" ] \
  && [ ! -e "$BOOTD" ] && [ -f "$K3_OPT/prev/gokaskad-2.13" ] \
  && ! grep -rqs "boot-run" "$KASKAD_ROOT/etc" "$K3_OPT/bin" \
  && ok "с временной копии: команда и копия в /opt — install.sh, прежняя 2.13 в prev, временный каталог удалён, его пути нигде нет" \
  || bad "запуск с временной копии" "$o | $(ls -la "$KASKAD_ROOT/usr/local/bin" "$W/boot-run" 2>&1)"
t_cmdbad() { K3_SELF="$W/half.sh"; k3_install_cmd; echo "CMD=$? ERR=[$K3_CMD_ERR]"; }
use_root boot2; head -c 100000 "$S" > "$W/half.sh"
o=$(lib t_cmdbad 2>&1)
printf '%s' "$o" | grep -q 'CMD=1 ERR=\[программа запущена не из целого файла Kaskad 3' && [ ! -e "$KASKAD_ROOT/usr/local/bin/gokaskad" ] \
  && ok "k3_install_cmd: не целая программа → код 1 и причина, команда не записана" || bad "k3_install_cmd с обрезанной программой" "$o"
body=$(sed -n '/^run_startup() {/,/^}/p' "$S")
printf '%s\n' "$body" | grep -q '^    if k3_install_cmd; then' && printf '%s\n' "$body" | grep -qF 'tf I_S3_BAD "$K3_CMD_ERR"' \
  && [ "$(printf '%s\n' "$body" | grep -c '✓.*I_S3_OK')" = 1 ] && ! printf '%s\n' "$body" | grep -q 'не из файла' \
  && printf '%s\n' "$body" | grep -q '^    k3_boot_done' \
  && ok "run_startup: «✓ Команда gokaskad» — только после k3_install_cmd, иначе ✗ с причиной; копия удаляется после ядра" || bad "шаг 3 run_startup"

echo "── 48. язык: каталоги lib/kaskad/i18n/sh (ru ↔ en), t/tf, выбор при первой установке, меню 11 ──"
# Полнота: в каждой области ключи ru и en одни и те же, подстановки printf совпадают (это же проверяет build_payload).
o=$(cd "$ROOT_DIR" && python3 -c 'import sys; sys.path.insert(0, "tools"); import build_payload as b
e = b.i18n_entries("lib"); n = sum(len(l["ru"]) for _a, l in e)
print("AREAS=%d KEYS=%d" % (len(e), n))' 2>&1)
case "$o" in AREAS=[1-9]*" KEYS="[1-9]*) ok "каталоги sh: ru и en — одни ключи, одни подстановки ($o)" ;; *) bad "каталоги sh" "$o" ;; esac
# Неполный каталог (нет ключа в en, другая подстановка, лишний ключ) — --check и сборка отказывают с причиной.
mkdir -p "$W/i18n-bad/kaskad/i18n/sh"
printf "M_ru_A='а %%s'\nM_ru_B='б'\n" > "$W/i18n-bad/kaskad/i18n/sh/x.ru.sh"
printf "M_en_A='a %%d'\nM_en_C='c'\n" > "$W/i18n-bad/kaskad/i18n/sh/x.en.sh"
o=$(cd "$ROOT_DIR" && python3 -c 'import sys; sys.path.insert(0, "tools"); import build_payload as b; b.i18n_entries(sys.argv[1])' "$W/i18n-bad" 2>&1)
printf '%s' "$o" | grep -q 'нет ключа B' && printf '%s' "$o" | grep -q 'лишний ключ C' && printf '%s' "$o" | grep -q 'ключ A: подстановки printf разные' \
  && ok "неполный каталог: нет ключа, лишний ключ, другая подстановка — отказ с причиной" || bad "проверка каталога" "$o"
# Апостроф в тексте: '\'' — каталог принимают сборка и bash; «60% off» — текст, не подстановка printf
mkdir -p "$W/i18n-q/kaskad/i18n/sh"
printf "%s\n" "M_ru_Q='не может: «%s»'" "M_ru_P='скидка 60%'" > "$W/i18n-q/kaskad/i18n/sh/q.ru.sh"
printf "%s\n" "M_en_Q='can'\\''t: the server'\\''s \"%s\"'" "M_en_P='60% off'" > "$W/i18n-q/kaskad/i18n/sh/q.en.sh"
o=$(cd "$ROOT_DIR" && python3 -c 'import sys; sys.path.insert(0, "tools"); import build_payload as b; print(len(b.render_i18n(sys.argv[1])))' "$W/i18n-q" 2>&1)
o2=$(bash -c 'source "$1"; source "$2"; printf "$M_en_Q" X; printf "|%s" "$M_en_P"' _ "$W/i18n-q/kaskad/i18n/sh/q.ru.sh" "$W/i18n-q/kaskad/i18n/sh/q.en.sh" 2>&1)
[ "$o" = 6 ] && [ "$o2" = "can't: the server's \"X\"|60% off" ] && ok "апостроф '\\'' и «60% off» в каталоге: сборка принимает, bash читает can't" || bad "апостроф в каталоге" "$o | $o2"
# t/tf: перевод → русский → ключ; и под /bin/bash 3.2 (косвенная подстановка, без declare -A)
t_tr() { M_ru_ZZ_ONLY_RU='только русский'; k3_lang_set en; echo "[$(t C_ENTER)][$(tf C_CHOICE_DEF 2)][$(t ZZ_ONLY_RU)][$(t ZZ_NO_KEY)]"
         k3_lang_set ru; echo "[$(t C_ENTER)][$(tf C_NUM_RANGE 5)]"; }
o=$(lib t_tr 2>&1)
[ "$o" = "[Press Enter to continue][Choice [2]: ][только русский][ZZ_NO_KEY]
[Нажмите Enter][номер от 1 до 5]" ] && ok "t/tf: английский, нет перевода — русский, нет ключа — сам ключ" || bad "t/tf" "$o"
blk=$(sed -n '/^# >>> K3_I18N/,/^# <<< K3_I18N/p' "$S"; sed -n '/^K3_LANG=ru$/,/^k3_lang_load$/p' "$S")
if [ -x /bin/bash ] && [ "$(/bin/bash -c 'echo ${BASH_VERSINFO[0]}')" -lt 4 ]; then
    o=$(KASKAD_LANG=en KASKAD_CONF=/nonexistent K3_ENV_LANG=en /bin/bash -c "$blk"'
echo "$K3_LANG|$(t C_ENTER)|$(tf C_CHOICE_DEF 1)"' 2>&1)
    [ "$o" = "en|Press Enter to continue|Choice [1]: " ] && ok "t/tf под /bin/bash 3.2 (macOS)" || bad "t/tf под bash 3.2" "$o"
fi
# Слово к числу — один механизм: tn КЛЮЧ n (формы в каталоге через «|»: ru — три, en — две)
t_tn() { for n in 1 2 5 11 21; do printf '%s ' "$(tn W_INB $n)"; done; k3_lang_set en; for n in 1 2 21; do printf '%s ' "$(tn W_DEV $n)"; done; }
o=$(lib t_tn 2>&1)
[ "$o" = "протокол протокола протоколов протоколов протокол device devices devices " ] && ok "tn: ru 1/2/5/11/21 — вход/входа/входов/входов/вход; en 1/2/21 — device/devices/devices" || bad "tn" "$o"
# Язык при запуске: окружение → настройки → ru. Окружение — без вопроса, записывается после init_config.
t_lang() { k3_lang_first; echo "LANG1=$K3_LANG SAVE=$K3_LANG_SAVE"; init_config >/dev/null 2>&1; k3_lang_remember
           echo "CONF=$(k3_lang_conf) NOW=$K3_LANG"; }
use_root lang1
o=$(lib eval 'K3_ENV_LANG=en; t_lang' 2>&1)
printf '%s' "$o" | grep -q 'LANG1=en SAVE=en' && printf '%s' "$o" | grep -q 'CONF=en NOW=en' && grep -qx 'KASKAD_LANG="en"' "$KASKAD_DIR/config" \
  && ok "KASKAD_LANG=en из окружения: без вопроса, записан в настройки" || bad "язык из окружения" "$o"
# Записанный язык — при следующем запуске без окружения; загрузка файла читает его сразу (k3_lang_load)
o=$(lib eval 'echo "$K3_LANG $(t C_ENTER)"' 2>&1)
[ "$o" = "en Press Enter to continue" ] && ok "язык из настроек — с загрузки программы" || bad "язык из настроек" "$o"
# Первая установка, терминал: вопрос на двух языках цифрами; Enter — по локали; «2» — English; мусор — снова
use_root lang2
o=$(printf 'x\n2\n' | K3_TEST_TTY=1 lib eval 'clear() { :; }; t_lang' 2>&1)
printf '%s' "$o" | grep -q '1) Русский' && printf '%s' "$o" | grep -q '2) English' && printf '%s' "$o" | grep -qF 'Выбор / Choice [' \
  && printf '%s' "$o" | grep -q '1 — Русский, 2 — English' && printf '%s' "$o" | grep -q 'CONF=en NOW=en' \
  && ok "первая установка: вопрос «1) Русский 2) English», неверный ввод — снова, 2 — English в настройки" || bad "вопрос языка" "$o"
use_root lang3
o=$(printf '\n' | LC_ALL= LC_MESSAGES= LANG=ru_RU.UTF-8 K3_TEST_TTY=1 lib eval 'clear() { :; }; t_lang' 2>&1)
printf '%s' "$o" | grep -qF 'Choice [1]' && printf '%s' "$o" | grep -q 'CONF=ru NOW=ru' && ok "Enter при LANG=ru_RU — русский (1)" || bad "язык по локали ru" "$o"
use_root lang4
o=$(printf '\n' | LC_ALL=C.UTF-8 K3_TEST_TTY=1 lib eval 'clear() { :; }; t_lang' 2>&1)
printf '%s' "$o" | grep -qF 'Choice [2]' && printf '%s' "$o" | grep -q 'CONF=en NOW=en' && ok "Enter при LC_ALL=C.UTF-8 — English (2)" || bad "язык по локали en" "$o"
# Обновление: настройки есть, строки KASKAD_LANG нет — русский, без вопроса и без записи; неверный KASKAD_LANG — предупреждение
use_root lang5; printf 'BOT_TOKEN=""\n' > "$KASKAD_DIR/config"
o=$(printf '2\n' | K3_TEST_TTY=1 lib eval 'clear() { :; }; K3_ENV_LANG=de; t_lang' 2>&1)
! printf '%s' "$o" | grep -q 'Language' && printf '%s' "$o" | grep -q 'KASKAD_LANG=de не принят' && printf '%s' "$o" | grep -qx 'LANG1=ru SAVE=' \
  && ! grep -q KASKAD_LANG "$KASKAD_DIR/config" && ok "обновление без строки языка — русский без вопроса; KASKAD_LANG=de — предупреждение" || bad "обновление и язык" "$o"
# Меню смены языка: цифрами, сразу в настройки
use_root lang6
o=$(printf '2\n' | K3_TEST_TTY=1 lib eval 'clear() { :; }; sleep() { :; }; init_config >/dev/null 2>&1; k3_lang_menu; echo "NOW=$K3_LANG"; t C_ENTER' 2>&1)
printf '%s' "$o" | grep -q '0) Назад' && printf '%s' "$o" | grep -q 'Language changed: English' && printf '%s' "$o" | grep -q 'NOW=en' \
  && grep -qx 'KASKAD_LANG="en"' "$KASKAD_DIR/config" && printf '%s' "$o" | tail -1 | grep -qx 'Press Enter to continue' \
  && ok "смена языка: 2 → English, KASKAD_LANG=\"en\" в настройках" || bad "меню языка" "$o"
# С ядром: смена — через kaskadctl lang set (один язык у SSH, панели и бота); язык процесса — из настроек
use_root lang7; fake_ctl
o=$(printf '1\n' | K3_TEST_TTY=1 lib eval 'clear() { :; }; sleep() { :; }; init_config >/dev/null 2>&1; save_config_val KASKAD_LANG en; k3_lang_set en
    k3ctl() { printf "%s\n" "$*" >> "$CALLS/kaskadctl.log"; [ "$1 $2" = "lang set" ] && save_config_val KASKAD_LANG "$3"; }
    k3_lang_menu; echo "NOW=$K3_LANG"' 2>&1)
grep -qx 'lang set ru' "$CALLS/kaskadctl.log" && printf '%s' "$o" | grep -q 'Язык изменён: Русский' && printf '%s' "$o" | grep -q 'NOW=ru' \
  && printf '%s' "$o" | grep -q '0) Back' && ok "смена языка с ядром: kaskadctl lang set ru, меню сразу по-русски" || bad "меню языка с ядром" "$o | $(ctl_log)"

echo "── 49. KASKAD_LANG=en: путь первой установки, главное меню, 11, 20, 21 — без кириллицы ──"
# Строки с кириллицей из вывода (коды цвета сняты); пусто — всё по-английски. Тексты ядра в подделках — английские:
# их переводит само ядро (E1/E2), здесь проверяется то, что печатает install.sh.
cyr() { strip | python3 -c 'import re, sys
print("\n".join(l for l in sys.stdin.read().split("\n") if re.search("[А-Яа-яЁё]", l)))'; }
EN_HEAD=$(printf '%s' "$HEAD_BASE" | jq -c '.problems = [{"kind": "gw_inbound", "detail": "Inbound L2TP/IPsec is down", "item": 17, "source": "live",
   "advice": "The reason is in 17 → 5 “Status”. The watchdog restarts the inbound every minute."},
  {"kind": "site_cert", "detail": "The domain certificate vpn.example.com expires 30.12.2026 (in 5 d): renewal did not work.", "item": 19,
   "advice": "Check and renew the certificate: 19 → 2.", "source": "rule"}]')
en_root() { use_root "$1"; fake_ctl; prog_ready; scan true "" 0 true; printf '%s\n' "$EN_HEAD" > "$FAKE_DIR/summary.json"
            : > "$(PANEL_UNIT_T)"; echo kaskad-panel > "$FAKE_DIR/active"; }
# run_startup целиком: шаги 1–8 (root, apt, сеть — подделки), переезд на nftables, промо, «Как настроить», шлюз, меню
t_en_start() {
    check_root() { :; }; k3_sysctl_tuning() { :; }; k3_post_update() { return 0; }; k3_apt_wait() { :; }; k3_apt_get() { :; }
    detect_interface() { IFACE=eth0; }; get_my_ip() { MY_IP=203.0.113.10; }; k3_remember_branch() { K3_BRANCH_NOTE=""; }
    k3_fresh_check() { K3_FRESH=1; }; migrate_dnat_comments() { :; }; adopt_free_kaskad_rules() { :; }; k3_legacy_deps() { :; }
    k3_gw_menu() { echo "GW-MENU"; }; sleep() { :; }; clear() { printf '\f'; }; K3_TEST_TTY=1
    run_startup
}
en_root en1
o=$(printf '\n2\n2\n0\n' | KASKAD_LANG=en lib t_en_start 2>&1)
c=$(printf '%s' "$o" | cyr)
so=$(printf '%s' "$o" | strip)
[ -z "$c" ] && printf '%s' "$so" | grep -q 'KASKAD PRO v.* — Loading' && printf '%s' "$so" | grep -q 'Root rights confirmed' \
  && printf '%s' "$so" | grep -q 'Interface: eth0' && printf '%s' "$so" | grep -q 'HOSTING UP TO 60% OFF' \
  && printf '%s' "$so" | grep -q 'How do you want to set up Kaskad?' && printf '%s' "$so" | grep -q 'Connect phones and routers to this server?' \
  && printf '%s' "$so" | grep -q 'You can connect them later: item 17' && printf '%s' "$so" | grep -q '11) Update · Language' \
  && grep -qx 'KASKAD_LANG="en"' "$KASKAD_DIR/config" \
  && ok "en: установка (шаги 1–8, промо, «How do you want…», шлюз «2 — later») и главное меню — без кириллицы; язык в настройках" \
  || bad "en: путь установки" "КИРИЛЛИЦА: $c | $(printf '%s' "$so" | tail -40)"
# квик-старт в браузере: ссылка, QR, одна строка «срок · порт»; сертификат не вышел — причина и отпечаток
en_root en2; setup_fake fallback "Port 80/tcp is in use: nginx." "AB:CD:EF:01:23:45:67:89:AB:CD:EF:01:23:45:67:89"
o=$(printf '\n1\n\n0\n' | KASKAD_LANG=en lib t_en_start 2>&1); c=$(printf '%s' "$o" | cyr); so=$(printf '%s' "$o" | strip | flat)
[ -z "$c" ] && printf '%s' "$so" | grep -qF "$SETUP_URL" && printf '%s' "$so" | grep -q 'No certificate for the IP: Port 80/tcp is in use' \
  && printf '%s' "$so" | grep -q 'SHA-256 fingerprint: AB:CD' && printf '%s' "$so" | grep -q 'Valid until .* · port 41234/tcp must be open' \
  && ! printf '%s' "$so" | grep -q 'Connect phones and routers' \
  && ok "en: квик-старт — ссылка, отпечаток, «Valid until … · port …», без вопроса про шлюз; без кириллицы" || bad "en: квик-старт" "КИРИЛЛИЦА: $c | $so"
# шапка и меню: 80×24, проблемы, 21 — экран проверки, 20, 11, неверный номер
t_en_menu() { scr show_menu; }
en_root en3; printf 'KASKAD_LANG="en"\n' > "$KASKAD_DIR/config"
o=$(printf '99\n21\n\n20\n0\n11\n0\n0\n' | K3_TERM_COLS=80 lib t_en_menu 2>&1); c=$(printf '%s' "$o" | cyr); so=$(printf '%s' "$o" | strip)
[ -z "$c" ] && printf '%s' "$so" | grep -q "IP: 203.0.113.10 · forwards: 4 (nftables) · ${NEWER} available: 11" \
  && printf '%s' "$so" | grep -q 'Domain: vpn.example.com · site · certificate until 30.12' \
  && printf '%s' "$so" | grep -q 'Panel: https://vpn.example.com/app-AbCdEfGh••••WxYz/ F1 or p — show' \
  && printf '%s' "$so" | grep -q 'Exits: alive 3 of 5 · AmneziaWG: kernel module' \
  && printf '%s' "$so" | grep -q '21) Troubleshooting (2)' && printf '%s' "$so" | grep -q 'No such item: a number from 0 to 22' \
  && printf '%s' "$so" | grep -q 'Problems: 2' && printf '%s' "$so" | grep -q 'Item 17:' \
  && printf '%s' "$so" | grep -q 'Quick Start in the browser' && printf '%s' "$so" | grep -q 'Version .* · branch' \
  && printf '%s' "$so" | grep -q '2) Language: English' \
  && ok "en: шапка, пункты, 21 «Troubleshooting», 20, 11 «Update / Language», неверный номер — без кириллицы" \
  || bad "en: главное меню" "КИРИЛЛИЦА: $c | $so"
res=""
for cw in 80 76; do
    so=$(K3_TERM_COLS=$cw lib t_en_menu <<< "0" 2>&1 | strip); f=$(printf '%s' "$so" | FIT_W=$cw FIT_H=24 fit); [ "$f" = "1 ok" ] || res+=" ${cw}:$f"
done
[ -z "$res" ] && ok "en: главное меню с шапкой и двумя проблемами — 80×24 и 76×24, ни одна строка не шире окна" || bad "en: меню в окне" "$res $so"
# 11 → 1: обновление по-английски; для ядра — метки @K3 (K3_MARKS=1), без них — ни одной
t_su_en() { K3_MARKS="${1:-}" self_update </dev/null; }
su_setup 2.13 $NEWER en; printf 'KASKAD_LANG="en"\n' >> "$KASKAD_DIR/config"
o=$(lib t_su_en 1 2>&1); c=$(printf '%s' "$o" | cyr)
[ -z "$c" ] && [ "$(ver_of "$C")" = "$NEWER" ] && printf '%s' "$o" | strip | grep -q "Updated to v$NEWER (branch test)" \
  && printf '%s' "$o" | grep -qx "@K3:update_done:$NEWER:test" && printf '%s' "$o" | grep -qx '@K3:update_restarted:bot' \
  && ok "en: 11 → обновление по-английски; метки @K3:update_done:<версия>:<ветка>, @K3:update_restarted:bot для kaskadctl update run" || bad "en: обновление" "КИРИЛЛИЦА: $c | $o"
su_setup 2.13 $NEWER en2
o=$(lib t_su_en 2>&1); ! printf '%s' "$o" | grep -q '@K3:' && printf '%s' "$o" | grep -q "Обновлено до v$NEWER" \
  && ok "без K3_MARKS (человек в меню) — ни одной метки @K3; русский по умолчанию" || bad "метки без K3_MARKS" "$o"
# шапка без ядра: строка «Domain:» — по полю state.json (домен задан), а не по словам состояния; нет домена — строки нет
t_en_nocore() { k3_head_load() { K3_HEAD_OK=""; K3_HEAD_JSON=""; k3_head_parse; }; K3_HL=(); k3_head_lines 79; printf '%b\n' "${K3_HL[@]}"; }
use_root en5; printf '{"engine": "nft", "schema": 1}\n' > "$KASKAD_DIR/state.json"
o=$(KASKAD_LANG=en lib t_en_nocore 2>&1 | strip)
use_root en6; printf '{"engine": "nft", "schema": 1, "site": {"domain": "vpn.example.com", "mode": "domain"}}\n' > "$KASKAD_DIR/state.json"
o2=$(KASKAD_LANG=en lib t_en_nocore 2>&1 | strip)
! printf '%s' "$o" | grep -q 'Domain' && printf '%s' "$o2" | grep -q '^Domain: vpn.example.com' && [ -z "$(printf '%s%s' "$o" "$o2" | cyr)" ] \
  && ok "en, ядро не отвечает: «Domain: …» только если домен задан (поле state.json), без кириллицы" || bad "шапка без ядра: домен" "$o | $o2"
# обмен с ядром без сопоставления слов: предупреждения и ожидание — метки @K3 и приставки на обоих языках
t_coretext() { printf '%s\n' '@K3:warn a' '@K3:error d' 'plain e' '@K3:wait f' '@K3:x y' | k3_core_text
    k3_no_force_hint "Port 51820/udp is in use: nginx. Take it anyway — with --force."; echo
    printf '%s\n' '@K3:wait The system is installing updates' 'other' > "$W/wl"; k3_gw_wait_lines "$W/wl" | strip; }
o=$(lib t_coretext 2>&1)
[ "$o" = "! a
d
plain e
f
Port 51820/udp is in use: nginx.

     The system is installing updates
     " ] && ok "k3_core_text / k3_no_force_hint / k3_gw_wait_lines: только метки @K3 (слова-приставки ядра не разбираются)" || bad "обмен с ядром" "$o"

# ── 49–51. английский язык подменю SSH 1–7, 9, 10, 12–16 (исполнитель G, 3.3.2) ──
# Кириллица в выводе на английском: строки с ней (цвета сняты). Имена и данные теста — латиницей, так что любая
# найденная строка — непереведённый текст. $1 — что проверяем (в сообщение), stdin — вывод меню.
en_cyr() { strip | LC_ALL=en_US.UTF-8 grep -n '[А-Яа-яЁё]' | head -5; }
# Кириллица в коде функций (declare -f — без комментариев): имена функций — аргументы. Журнал (log_action) не
# переводится; намеренно русские строки (данные 2.x, разбор старых файлов) — шаблон ERE в CYR_OK.
code_cyr() { local f; for f in "$@"; do lib declare -f "$f" 2>/dev/null; done | grep -v 'log_action' \
             | { if [ -n "${CYR_OK:-}" ]; then grep -Ev -- "$CYR_OK"; else cat; fi; } \
             | LC_ALL=en_US.UTF-8 grep -n '[А-Яа-яЁё]' | head -5; }

# >>> G-A (49): правила и пробросы 1–5, 9, 10, имена серверов 14 — раздел вставлять сразу под этой строкой
echo "── 49g. английский: пункты 1–4 (новый проброс, конфликт порта), 5, 9, 10, 14 — экраны, вопросы, ошибки (G-A) ──"
o=$(code_cyr read_validated_ip read_validated_port probe_server_cli k3_fwd_server_name smart_ping tcp_ping get_rules_list apply_iptables_rules \
    configure_rule configure_custom_rule k3_rule_cancelled k3_rule_next_step list_active_rules delete_single_rule \
    k3_legacy_delete_rule flush_all_kaskad_rules remove_masquerade_if_unused migrate_dnat_comments adopt_free_kaskad_rules \
    flush_rules k3_legacy_flush_all manage_aliases_menu k3_legacy_locked k3_add_refused k3_confirm_conflict k3_fwd_add \
    k3_fwd_del k3_list_active_rules k3_ufw_forget k3_delete_rule_nft k3_flush_rules_nft remove_rules_for_port)
[ -z "$o" ] && ok "п. 1–5, 9, 10, 14: в коде функций нет кириллицы (тексты — в каталогах fwd, rules, reset, aliases)" || bad "кириллица в коде G-A" "$o"
# Конфликт порта — без сравнения текста ядра: ни «Порт »*, ни среза хвоста про --force
! sed -n '/^k3_confirm_conflict() {/,/^}/p;/^k3_fwd_add() {/,/^}/p' "$S" | grep -v '^[[:space:]]*#' | grep -qE '== "Port |% Add|--force\.' \
  && sed -n '/^k3_fwd_add() {/,/^}/p' "$S" | grep -q '\.data\.conflicts' \
  && ok "конфликт порта: сообщение из data.conflicts (машинные поля), текст ядра не сравнивается и не режется" || bad "конфликт порта: разбор текста ядра"
# Прогон под английским и под русским: $1 — en|ru, $2 — пинг отвечает (alive) или молчит (silent), дальше — команда.
ga_run() { local l="$1" p="$2" sp; shift 2
    if [ "$p" = alive ]; then sp='smart_ping() { echo "ICMP|12.5"; }'; else sp='smart_ping() { return 1; }'; fi
    lib eval "k3_lang_set $l; clear() { :; }; sleep() { :; }; geoip_lookup() { echo '{}'; }; $sp
        K3_TEST_TTY=1; $*"'; echo "RC=$? ERR=[$K3_FWD_ERR] BUSY=[$K3_FWD_BUSY]"' 2>&1 | strip; }
# приглашения read -p без терминала не печатаются — их текст проверяет code_cyr выше; длинные строки k3_say
# переносит по ширине — hasf ищет во выводе, собранном в одну строку
has() { printf '%s' "$1" | grep -qF -- "$2"; }
hasf() { printf '%s' "$1" | flat | grep -qF -- "$2"; }
use_root ga49; fake_ctl; engine_nft_fwds
printf 'tcp|443|2.2.2.2:443|12|1536\nudp|51820|5.5.5.5:51820|3|3145728\n' > "$FAKE_DIR/fwd_list_traffic"
# п. 1: неверный IP и порт, сервер молчит на ping, имя, «Применить?» → 1
o=$(printf 'abc\n198.51.100.7\nx\n51820\n1\nedge-nl\n\n1\n\n' | ga_run en silent configure_rule udp AmneziaWG)
c=$(printf '%s\n' "$o" | en_cyr)
[ -z "$c" ] && has "$o" '--- Set up AmneziaWG (udp) ---' && has "$o" 'IP address of the destination server abroad (where to forward), 0 — back:' \
  && has "$o" 'Not an IP address: four numbers separated by dots' && has "$o" 'Port (the same on both servers), 0 — back:' \
  && has "$o" 'Error: the port must be a number from 1 to 65535.' && has "$o" '━━━ Checking server 198.51.100.7 ━━━' \
  && has "$o" 'GeoIP: lookup failed' && has "$o" 'No reply from the server' && has "$o" 'Server 198.51.100.7 did not answer the check (ping).' \
  && has "$o" 'Continue adding?' && has "$o" '1) Continue adding the forward' && has "$o" 'Tip: to make the server answer ping' \
  && has "$o" 'Enter a server name (or Enter to skip):' && has "$o" 'Enter a note (or Enter to skip):' && has "$o" 'Forward to be created:' \
  && has "$o" 'Apply?' && has "$o" '1) Create forward' && has "$o" '[*] Applying forwards…' && has "$o" '[SUCCESS] AmneziaWG is set up.' \
  && has "$(printf '%s' "$o" | flat)" 'Next: in the app on your phone, set the address 203.0.113.10 and port 51820 instead of the server abroad.' && ctl_log | grep -qx 'fwd add --proto udp --port 51820 --to 198.51.100.7 --to-port 51820 --name AmneziaWG --json' \
  && ok "en п. 1: ввод IP/порта, проверка сервера, «Apply?», итог — по-английски, без кириллицы" || bad "en п. 1" "$c | $o"
o=$(printf '198.51.100.7\n51820\n\n' | ga_run en alive configure_rule udp AmneziaWG)
[ -z "$(printf '%s\n' "$o" | en_cyr)" ] && has "$o" 'Average: 12.50 ms  Lost: 0/3' && has "$o" '2) Cancel' && has "$o" 'Canceled, no forward created.' \
  && ok "en п. 1: сервер отвечает — «Average … Lost 0/3»; Enter на «Apply?» — «Canceled, no forward created.»" || bad "en п. 1: отмена" "$o"
# п. 1 с конфликтом порта (код 3, data.conflicts — detail уже на языке сервера): сообщение собрано из полей, без --force
: > "$CALLS/kaskadctl.log"
echo '{"ok": false, "error": "Port 443/tcp is in use: nginx listens on it. Add anyway — with --force.", "code": 3, "data": {"kind": "listener", "conflicts": [{"kind": "listener", "detail": "nginx listens on it"}, {"kind": "docker", "detail": "a Docker container publishes it (→ 172.17.0.2:443)"}]}}' > "$FAKE_DIR/conflict.json"
o=$(printf '198.51.100.7\n443\n\n\n1\n2\n\n' | ga_run en alive configure_rule tcp VLESS)
[ -z "$(printf '%s\n' "$o" | en_cyr)" ] && hasf "$o" 'Port 443/tcp is in use: nginx listens on it; a Docker container publishes it (→ 172.17.0.2:443).' \
  && ! has "$o" '--force' && ! has "$o" 'Port in use: Port' && has "$o" 'Add anyway?' && has "$o" '1) Add the forward anyway' \
  && has "$o" 'Canceled, no forward created.' && ! has "$o" 'ERROR' && [ "$(ctl_log | wc -l | tr -d ' ')" = 1 ] \
  && ok "en п. 1, порт занят: «Port 443/tcp is in use: …» из data.conflicts, «Add anyway?» → 2 — «Canceled», без --force" || bad "en конфликт" "$o"
: > "$CALLS/kaskadctl.log"
o=$(printf '198.51.100.7\n443\n\n\n1\n1\n\n' | ga_run en alive configure_rule tcp VLESS)
[ "$(ctl_log | wc -l | tr -d ' ')" = 2 ] && ctl_log | tail -1 | grep -q -- '--force' && has "$o" '[SUCCESS] VLESS is set up.' \
  && ok "en п. 1, порт занят: «1» — повтор с --force, правило создано" || bad "en конфликт → 1" "$o | $(ctl_log)"
# порт SSH: слово на языке вопроса — CONFIRM; русское ПОДТВЕРЖДАЮ на английском не проходит
echo '{"ok": false, "error": "Port 22/tcp is in use: sshd listens on it — the forward cuts off access to this server. Add anyway — with --force.", "code": 3, "data": {"kind": "ssh", "conflicts": [{"kind": "ssh", "detail": "sshd listens on it — the forward cuts off access to this server"}]}}' > "$FAKE_DIR/conflict.json"
: > "$CALLS/kaskadctl.log"
o=$(printf '198.51.100.7\n22\n\n\n1\nПОДТВЕРЖДАЮ\n\n' | ga_run en alive configure_rule tcp VLESS)
o2=$(printf '198.51.100.7\n22\n\n\n1\nCONFIRM\n\n' | ga_run en alive configure_rule tcp VLESS)
[ -z "$(printf '%s\n' "$o2" | en_cyr)" ] && hasf "$o2" 'Port 22/tcp is in use: sshd listens on it — the forward cuts off access to this server.' \
  && has "$o2" 'Forwarding this port cuts off SSH to this server' && has "$o2" 'If that is what you want, type CONFIRM (Enter — cancel):' \
  && has "$o2" '[SUCCESS] VLESS is set up.' && has "$o" 'Canceled, no forward created.' && [ "$(grep -c -- '--force' "$CALLS/kaskadctl.log")" = 1 ] \
  && ok "en порт SSH: слово CONFIRM → --force; ПОДТВЕРЖДАЮ на английском — отмена" || bad "en порт SSH" "$o | $o2 | $(ctl_log)"
# без data.conflicts (старое ядро, «уже есть») — причина ядра как есть, с приставкой «Port in use:»
echo '{"ok": false, "error": "forward tcp/443 already exists", "code": 3, "data": {"kind": "listener"}}' > "$FAKE_DIR/conflict.json"
o=$(printf '2\n' | ga_run en alive apply_iptables_rules tcp 443 443 2.2.2.2 VLESS)
has "$o" 'Port in use: forward tcp/443 already exists' && has "$o" 'BUSY=[]' && ok "en без data.conflicts: «Port in use: <причина ядра>»" || bad "en без conflicts" "$o"
# бот (K3_FWD_ERR): «Port …» собран из data.conflicts — K3_FWD_BUSY=1, по нему k3_bot_refusal не ставит подпись
echo '{"ok": false, "error": "x", "code": 3, "data": {"kind": "listener", "conflicts": [{"kind": "listener", "detail": "nginx listens on it"}]}}' > "$FAKE_DIR/conflict.json"
o=$(ga_run en alive 'K3_BOT=1; apply_iptables_rules tcp 443 443 2.2.2.2 VLESS </dev/null')
has "$o" 'ERR=[Port 443/tcp is in use: nginx listens on it.] BUSY=[1]' && has "$o" '[ERROR] Forward not added: Port 443/tcp' \
  && ok "en бот: K3_FWD_ERR «Port 443/tcp is in use: …», меню — «[ERROR] Forward not added»" || bad "en бот: K3_FWD_ERR" "$o"
echo '{"ok": false, "error": "Порт 443/tcp занят: его слушает nginx. Добавить всё равно — с --force.", "code": 3, "data": {"kind": "listener", "conflicts": [{"kind": "listener", "detail": "его слушает nginx"}]}}' > "$FAKE_DIR/conflict.json"
o=$(ga_run ru alive 'K3_BOT=1; apply_iptables_rules tcp 443 443 2.2.2.2 VLESS </dev/null')
has "$o" 'ERR=[Порт 443/tcp занят: его слушает nginx.] BUSY=[1]' && has "$o" '[ERROR] Проброс не добавлен: Порт 443/tcp занят' \
  && ok "ru бот: K3_FWD_ERR — как текст ядра без хвоста про --force, байт в байт" || bad "ru бот: K3_FWD_ERR" "$o"
echo '{"ok": false, "error": "Порт 22/tcp занят: его слушает sshd — проброс отрежет вход на этот сервер. Добавить всё равно — с --force.", "code": 3, "data": {"kind": "ssh", "conflicts": [{"kind": "ssh", "detail": "его слушает sshd — проброс отрежет вход на этот сервер"}]}}' > "$FAKE_DIR/conflict.json"
: > "$CALLS/kaskadctl.log"
o=$(printf 'CONFIRM\n' | ga_run ru alive apply_iptables_rules tcp 22 22 2.2.2.2 VLESS)
o2=$(printf 'ПОДТВЕРЖДАЮ\n' | ga_run ru alive apply_iptables_rules tcp 22 22 2.2.2.2 VLESS)
hasf "$o" 'Порт 22/tcp занят: его слушает sshd — проброс отрежет вход на этот сервер.' && has "$o" 'введите слово ПОДТВЕРЖДАЮ (Enter — отмена):' \
  && has "$o" 'Отменено, правило не создано.' && has "$o2" 'RC=0' && [ "$(grep -c -- '--force' "$CALLS/kaskadctl.log")" = 1 ] \
  && ok "ru порт SSH: прежний текст, слово ПОДТВЕРЖДАЮ → --force; CONFIRM по-русски — отмена" || bad "ru порт SSH" "$o | $o2"
rm -f "$FAKE_DIR/conflict.json"
# п. 4: протокол, входящий и исходящий порт
: > "$CALLS/kaskadctl.log"
o=$(printf 'sctp\nudp\n198.51.100.7\n40000\n51820\n\n\n1\n\n' | ga_run en alive configure_custom_rule)
[ -z "$(printf '%s\n' "$o" | en_cyr)" ] && has "$o" '--- Custom forward ---' && has "$o" 'Choose the protocol (tcp or udp), 0 — back:' \
  && has "$o" 'Error: enter tcp or udp (0 — back).' && has "$o" 'Enter the INCOMING port (on this server), 0 — back:' \
  && has "$o" 'Enter the OUTGOING port (on the server abroad), 0 — back:' && has "$o" 'udp: 203.0.113.10:40000 -> 198.51.100.7:51820' \
  && has "$o" 'set the address 203.0.113.10 and port 40000' && has "$o" '[SUCCESS] Custom forward is set up.' \
  && grep -qx -- 'fwd add --proto udp --port 40000 --to 198.51.100.7 --to-port 51820 --name Custom forward --json' "$CALLS/kaskadctl.log" \
  && ok "en п. 4: протокол, входящий/исходящий порт — по-английски; «Custom forward is set up» (T-M6-332 №5)" || bad "en п. 4" "$o | $(cat "$CALLS/kaskadctl.log")"
o=$(printf 'tcp\n198.51.100.7\n2222\n22\n\n\n1\n\n' | ga_run ru alive configure_custom_rule)
has "$o" '--- Универсальное кастомное правило ---' && has "$o" 'Введите ВХОДЯЩИЙ Порт (на этом сервере), 0 — назад:' \
  && has "$o" 'Выберите протокол (tcp или udp), 0 — назад:' && has "$o" 'Среднее: 12.50ms  Потеряно: 0/3' && has "$o" '[SUCCESS] Custom Rule настроен!' && ok "ru п. 4: прежний текст" || bad "ru п. 4" "$o"
# п. 5: список с трафиком; старый движок без правил
o=$(ga_run en alive 'list_active_rules </dev/null')
o2=$(engine_nft_fwds; rm -f "$KASKAD_DIR/state.json"; ga_run en alive 'list_active_rules </dev/null'); engine_nft_fwds
[ -z "$(printf '%s\n%s\n' "$o" "$o2" | en_cyr)" ] && has "$o" '━━━ Active forwards ━━━' && has "$o" 'Kaskad server: 203.0.113.10  Forwarding engine: nftables' \
  && has "$o" '2.2.2.2:443' && has "$o" 'traffic: 1.5 KB' && has "$o2" 'No active forwards.' \
  && ok "en п. 5: «Active forwards», движок, трафик; без пробросов — «No active forwards.»" || bad "en п. 5" "$o | $o2"
o=$(ga_run ru alive 'list_active_rules </dev/null')
has "$o" '━━━ Активные переадресации ━━━' && has "$o" 'Сервер каскада: 203.0.113.10  Движок: nftables' && has "$o" 'трафик: 3.0 MB' && ok "ru п. 5: прежний текст" || bad "ru п. 5" "$o"
# п. 9: неверный номер, удаление, отмена, отказ ядра
: > "$CALLS/kaskadctl.log"
o=$(printf 'x\n7\n2\n\n' | ga_run en alive delete_single_rule)
o2=$(printf '\n' | ga_run en alive delete_single_rule)
echo 1 > "$FAKE_DIR/del_rc"; o3=$(printf '1\n\n' | ga_run en alive delete_single_rule); rm -f "$FAKE_DIR/del_rc"
[ -z "$(printf '%s\n%s\n%s\n' "$o" "$o2" "$o3" | en_cyr)" ] && has "$o" '--- Delete forward ---' \
  && [ "$(printf '%s\n' "$o" | grep -c 'No such item: enter a number from 1 to 2, 0 — back.')" = 2 ] && has "$o" '[OK] Forward deleted.' \
  && ctl_log | grep -qx 'fwd del --proto udp --port 51820 --json' && has "$o2" 'Canceled, nothing deleted.' \
  && has "$o3" '[ERROR] Could not delete: kaskadctl exited with code 1' \
  && ok "en п. 9: номер, «No such item», «Forward deleted», отмена, отказ ядра — по-английски" || bad "en п. 9" "$o | $o2 | $o3"
o=$(printf 'x\n2\n\n' | ga_run ru alive delete_single_rule)
has "$o" '--- Удаление правила ---' && has "$o" 'Нет такого пункта: номер от 1 до 2, 0 — назад.' \
  && has "$o" '[OK] Правило удалено.' && ok "ru п. 9: прежний текст" || bad "ru п. 9" "$o"
# п. 10
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n\n' | ga_run en alive flush_rules); o2=$(printf '\n\n' | ga_run en alive flush_rules)
[ -z "$(printf '%s\n%s\n' "$o" "$o2" | en_cyr)" ] && has "$o" '!!! WARNING !!!' && has "$o" 'Only Kaskad forwards will be deleted.' && has "$o" 'Are you sure?' \
  && has "$o" '1) Delete all Kaskad forwards' && has "$o" '2) Cancel' && has "$o" '[OK] Cleared.' \
  && [ "$(ctl_log | grep -c 'fwd flush')" = 1 ] && ! has "$o2" 'Cleared' \
  && ok "en п. 10: предупреждение, вопрос цифрами, «Cleared»; Enter — отмена" || bad "en п. 10" "$o | $o2"
o=$(printf '1\n\n' | ga_run ru alive flush_rules)
has "$o" '!!! ВНИМАНИЕ !!!' && has "$o" 'Будут удалены только правила Kaskad.' && has "$o" 'Уверены?' && has "$o" '1) Удалить все правила Kaskad' \
  && has "$o" '[OK] Очищено.' && ok "ru п. 10: прежний текст" || bad "ru п. 10" "$o"
# п. 14: имя и примечание; без серверов
o=$(printf '1\nedge-nl\nhome router\n0\n' | ga_run en alive manage_aliases_menu)
[ -z "$(printf '%s\n' "$o" | en_cyr)" ] && has "$o" '━━━ Server names ━━━' && has "$o" '[0] Back' \
  && has "$o" 'New name for 2.2.2.2 (Enter — keep):' && has "$o" 'New note (Enter — keep):' && has "$o" 'Note: home router' \
  && has "$o" 'edge-nl (2.2.2.2)' && ok "en п. 14: имя и примечание — по-английски" || bad "en п. 14" "$o"
o=$(printf '1\n\n\n0\n' | ga_run ru alive manage_aliases_menu)
has "$o" '━━━ Имена серверов ━━━' && has "$o" '[0] Назад' && has "$o" 'Новое имя для 2.2.2.2 (Enter — оставить):' && has "$o" 'Примечание: home router' \
  && ok "ru п. 14: прежний текст" || bad "ru п. 14" "$o"
o=$(rm -f "$KASKAD_DIR/state.json"; printf '\n' | ga_run en alive manage_aliases_menu)
has "$o" 'No servers.' && [ -z "$(printf '%s\n' "$o" | en_cyr)" ] && ok "en п. 14 без серверов — «No servers.»" || bad "en п. 14 пусто" "$o"
# перенос правил бесплатной версии (старый движок) — по-английски
use_root ga49adopt
printf '%s\n' '-p tcp -m tcp --dport 18080 -j DNAT --to-destination 1.1.1.1:80' '-p tcp -m tcp --dport 18080 -j DNAT --to-destination 3.3.3.3:80' > "$IPT_DIR/nat.PREROUTING"
printf '%s\n' '-d 1.1.1.1/32 -p tcp -m tcp --dport 80 -m state --state NEW,RELATED,ESTABLISHED -j ACCEPT' \
    '-d 3.3.3.3/32 -p tcp -m tcp --dport 80 -m state --state NEW,RELATED,ESTABLISHED -j ACCEPT' > "$IPT_DIR/filter.FORWARD"
o=$(ga_run en alive adopt_free_kaskad_rules)
[ -z "$(printf '%s\n' "$o" | en_cyr)" ] && has "$o" '[*] Forwards of the free Kaskad: 2 — taking them over' \
  && has "$o" 'tcp :18080 → 3.3.3.3:80 did not work (another forward comes first on this port) — removed.' \
  && has "$o" '[SUCCESS] Forward from the free version is set up.' && ok "en перенос правил бесплатной версии — по-английски" || bad "en adopt" "$o"

# <<< G-A

# >>> G-B (50): ping 6, мониторинг 7, удаление 15, PROMO 12, инструкция 13 — раздел вставлять сразу под этой строкой
echo "── 50. английский язык подменю: 6 Ping, 7 мониторинг, 13 инструкция, 15 удаление (G-B, 3.3.2) ──"
# (а) кириллицы в коде нет. Намеренно: [тТ] и [зЗ] в show_instructions — N и P в русской раскладке (листать, не
# переключая язык клавиатуры).
o=$(CYR_OK='\[тТ\]|\[зЗ\]' code_cyr make_ping_bar ping_live ping_menu add_monitor remove_monitor has_monitors \
    sync_monitoring_service start_monitoring_silent stop_monitoring_silent list_monitors monitor_daemon k3_threshold_ok \
    k3_read_threshold monitoring_menu k3_backups_rm_ok k3_backups_count full_uninstall k3_uninstall_iptables \
    k3_logrotate_install k3_host_files_remove k3_ufw_sweep k3_leg_routes_remove k3_units_ours k3_units_stop_all \
    k3_units_off k3_units_sweep k3_our_path k3_kill_ours k3_panel_present k3_panel_uninstall k3_site_uninstall_fallback \
    k3_accel_hooks k3_accel_present k3_accel_uninstall k3_gw_pkgs_ours k3_build_pkgs_ours k3_gw_fallback_extra show_instructions)
[ -z "$o" ] && ok "6, 7, 13, 15: в коде функций нет русского текста (кроме журнала и N/P русской раскладки)" || bad "6, 7, 13, 15: русский в коде" "$o"
# (б) 6 «Ping (live)»: одна проба (ответ и тишина), выход из цикла — первым sleep
t_ping50() { k3_lang_set "$1"; get_target_ips() { printf '%s\n' 203.0.113.5 198.51.100.7; }; get_port_for_ip() { echo 443; }
             clear() { :; }; sleep() { running=0; }; smart_ping() { [ -n "${PING_OK:-}" ] && echo "ICMP|12.34"; }; ping_menu; }
use_root g50; fake_ctl; engine_nft
o=$(printf '2\n\n' | PING_OK=1 lib t_ping50 en 2>&1); o2=$(printf '1\n\n' | lib t_ping50 en 2>&1)
c=$(printf '%s\n%s' "$o" "$o2" | en_cyr); o=$(printf '%s\n%s' "$o" "$o2" | strip)
[ -z "$c" ] && printf '%s' "$o" | grep -q -- '--- Ping servers ---' && printf '%s' "$o" | grep -qx '  \[0\] Cancel' \
  && printf '%s' "$o" | grep -qF '[Ctrl+C — stop]' && printf '%s' "$o" | grep -qF 'Min: 12.34ms │ Max: 12.34ms │ Avg: 12.34ms' \
  && printf '%s' "$o" | grep -qx '  Lost: 1 / 1' && printf '%s' "$o" | grep -qF 'The server never replied' \
  && printf '%s' "$o" | grep -qx 'ICMP is blocked and TCP:443 failed.' && printf '%s' "$o" | grep -qx 'To enable ping on the remote server:' \
  && ok "6 по-английски: список серверов, проба, Min/Max/Avg, Lost, сервер не ответил — без кириллицы" || bad "6 по-английски" "$c | $o"
o=$(printf '1\n\n' | lib t_ping50 ru 2>&1 | strip)
printf '%s' "$o" | grep -q -- '--- Ping серверов ---' && printf '%s' "$o" | grep -qx 'ICMP заблокирован и TCP:443 не удался.' \
  && printf '%s' "$o" | grep -qF '[Ctrl+C — стоп]' && printf '%s' "$o" | grep -qx '  Потеряно: 1 / 1' \
  && ok "6 по-русски — прежний текст" || bad "6 по-русски" "$o"
# 7 «Monitoring»: неверный пункт, добавить (порог с ошибкой), список, удалить
t_mon50() { k3_lang_set "$1"; get_target_ips() { printf '%s\n' 203.0.113.5 198.51.100.7; }; systemctl() { :; }
            k3_bot_py_active() { return 1; }; clear() { :; }; monitoring_menu; ls "$MONITOR_DIR"; }
mkdir -p "$KASKAD_DIR/monitors"
o=$(printf '9\n1\n2\n2\ny\n150\n3\n\n\n2\n1\n\n0\n' | lib t_mon50 en 2>&1); c=$(printf '%s' "$o" | en_cyr); o=$(printf '%s' "$o" | strip)
[ -z "$c" ] && printf '%s' "$o" | grep -qF '━━━ Monitoring (auto) ━━━' && printf '%s' "$o" | grep -qx 'Status: Running' \
  && printf '%s' "$o" | grep -qx '1) Add' && printf '%s' "$o" | grep -qx '2) Remove' && printf '%s' "$o" | grep -qx '0) Back' \
  && printf '%s' "$o" | grep -qx 'No such item: a number from 1 to 2, 0 — back.' && printf '%s' "$o" | grep -qx 'Check interval:' \
  && printf '%s' "$o" | grep -qx ' 2) 1 min' && printf '%s' "$o" | grep -qx 'Threshold: milliseconds from 1 to 10000, e.g. 150 (0 — back).' \
  && printf '%s' "$o" | grep -qx 'Alert at most once every:' && printf '%s' "$o" | grep -qx ' 4) 15 min' \
  && printf '%s' "$o" | grep -qx '  198.51.100.7  every: 60s  threshold: 150 ms  alerts: 300s' \
  && [ "$(printf '%s\n' "$o" | grep -c '^  No monitors.$')" = 3 ] && ! printf '%s' "$o" | grep -q '\.conf$' \
  && ok "7 по-английски: меню, неверный пункт, интервал, порог, уведомления, список, удаление — без кириллицы" || bad "7 по-английски" "$c | $o"
o=$(printf '1\n2\n2\n150\n3\n0\n' | lib t_mon50 ru 2>&1 | strip)
printf '%s' "$o" | grep -qx '  198.51.100.7  инт: 60s  порог: 150ms  уведомл: 300s' && printf '%s' "$o" | grep -qx 'Уведомлять не чаще, чем раз в:' \
  && printf '%s' "$o" | grep -qx '198.51.100.7.conf' && ok "7 по-русски — прежний текст, монитор добавлен" || bad "7 по-русски" "$o"
rm -f "$KASKAD_DIR/monitors"/*.conf
# 13 «Guide»: все 12 страниц, каждая в 80×24 (как раздел 19), неверный номер (14 экранов: 12 страниц, x, P)
t_ins50() { k3_lang_set en; K3_OPT=/opt/kaskad; clear() { printf '\f'; }; show_instructions; }
feed=""; for _ in 1 2 3 4 5 6 7 8 9 10 11; do feed+=$'n\n'; done; feed+=$'x\np\n0\n'
o=$(printf '%s' "$feed" | lib t_ins50 2>&1); c=$(printf '%s' "$o" | en_cyr); o=$(printf '%s' "$o" | strip)
res=$(printf '%s' "$o" | python3 -c '
import sys, unicodedata
def width(s):
    return sum(0 if unicodedata.combining(c) else (2 if unicodedata.east_asian_width(c) in "WF" else 1) for c in s)
big, skew = [], []
screens = sys.stdin.read().split("\f")[1:]
for i, scr in enumerate(screens, 1):
    lines = scr.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) + 1 > 24 or max(width(l) for l in lines) > 78:
        big.append(i)
    if width(lines[1]) != width(lines[0]):
        skew.append(i)
print(len(screens), "big=" + ",".join(map(str, big)), "skew=" + ",".join(map(str, skew)))
')
miss=""
for x in "═══ WHAT IS A CASCADE ═══" "═══ ENGINE AND MIGRATION (16) ═══" "═══ IF SOMETHING IS WRONG ═══" "/opt/kaskad/bin/kaskadctl gw status" \
         "[N] Next  [1-12] Go to page  [0] Quit" "[P] Back  [N] Next  [1-12] Go to page  [0] Quit" "[P] Back  [1-12] Go to page  [0] Quit" \
         "No such item: a page number from 1 to 12, N — next, P — back, 0 — quit."; do
    printf '%s' "$o" | grep -qF -- "$x" || miss+="$x; "
done
[ -z "$c" ] && [ "$res" = "14 big= skew=" ] && [ -z "$miss" ] \
  && ok "13 по-английски: 12 страниц и подсказки без кириллицы, каждая страница в 80×24, рамка ровная" || bad "13 по-английски" "$c | $res | $miss"
# 15 «Uninstall Kaskad PRO»: худший набор (шлюз, панель, модуль, домен, пакеты, копии); слово — на языке вопроса
t_un50() {   # $1 — язык, $2 — ввод (printf, %s — слово), $3 — язык слова (по умолчанию тот же)
    local -a ws; k3_lang_set "${3:-$1}"; read -r -a ws <<< "$(t UNINST_WORDS)"; k3_lang_set "$1"
    RANDOM=7; local w="${ws[$((RANDOM % ${#ws[@]}))]}"; RANDOM=7
    K3_ISSUE_DIR="$KASKAD_ROOT/root/kaskad-issue" scr full_uninstall < <(printf "$2" "$w")
}
un_worst g50a
o=$(lib t_un50 en '2\n\n' 2>&1 | sed "s#$KASKAD_ROOT##g"); c=$(printf '%s' "$o" | en_cyr); o=$(printf '%s' "$o" | strip)
r=$(printf '%s' "$o" | fit 'Canceled.')
[ -z "$c" ] && [ "$r" = "1 ok" ] && printf '%s' "$o" | grep -qF '⚠  UNINSTALL KASKAD PRO  ⚠' && printf '%s' "$o" | grep -qx 'Will be removed:' \
  && printf '%s' "$o" | grep -qx 'Will NOT be touched:' && printf '%s' "$o" | grep -qx '  • Telegram bot, monitoring, Telegram API proxy, web panel' \
  && printf '%s' "$o" | grep -qx ' 1) Uninstall Kaskad PRO completely — then you type a word to confirm' && printf '%s' "$o" | grep -qx 'Canceled.' \
  && [ -d "$KASKAD_DIR" ] && ok "15 по-английски: экран подтверждения (худший набор) — 80×24, без кириллицы; 2 — отменено" || bad "15 по-английски: экран" "$c | $r | $o"
o=$(lib t_un50 en '1\n%s\n\n' ru 2>&1 | strip)
[ -d "$KASKAD_DIR" ] && printf '%s' "$o" | grep -qx 'Wrong word. Uninstall canceled.' \
  && ok "15 по-английски: русское слово на английский вопрос — неверное слово, ничего не удалено" || bad "15: слово не того языка" "$o"
o=$(lib t_un50 en '1\n%s\n2\n2\n\n' 2>&1 | sed "s#$KASKAD_ROOT##g"); c=$(printf '%s' "$o" | en_cyr); o=$(printf '%s' "$o" | strip); w=$(printf '%s' "$o" | flat)
[ -z "$c" ] && [ ! -e "$KASKAD_DIR" ] && printf '%s' "$o" | grep -qE '^Last chance\. Type the word (DELETE|ERASE|REMOVE|DESTROY|GOODBYE) to confirm:$' \
  && printf '%s' "$w" | grep -qF 'In /var/backups/kaskad: 8 Kaskad backups.' && printf '%s' "$w" | grep -qF '2) Keep the backups in /var/backups/kaskad' \
  && printf '%s' "$o" | grep -qx '  ✓  Gateway removed' && printf '%s' "$o" | grep -qx '  ✓  Logs removed' \
  && printf '%s' "$w" | grep -qF 'The backups are left in /var/backups/kaskad (they contain keys and passwords)' \
  && printf '%s' "$o" | grep -qx '  Kaskad PRO is completely uninstalled.' \
  && ok "15 по-английски: английское слово, средства сборки, копии (2 — оставить), шаги удаления, итог — без кириллицы" || bad "15 по-английски: удаление" "$c | $o"

# <<< G-B

# >>> G (51): движок и перенос 16, копии — раздел вставлять сразу под этой строкой
echo "── 51. английский: меню 16 «Движок и перенос», копия настроек (16 → 6); tn ──"
# Код пункта 16 без кириллицы (комментарии declare -f не печатает; журнал — по-русски, его не переводим).
o=$(code_cyr k3_legacy_persist_offer k3_migrate_apply k3_migrate_interactive k3_gw_offer_reenable k3_rollback_interactive \
    k3_ctl_folded k3_gw_reenable k3_backups_show k3_find_2x_program k3_download_213 k3_return_to_2x k3_teardown_fallback \
    k3_came_from_free k3_engine_trouble k3_engine_details k3_backup_cmd k3_backup_password k3_backup_save k3_backup_load \
    k3_backup_undo k3_backup_menu k3_engine_menu)
[ -z "$o" ] && ok "пункт 16 и копия настроек: в коде нет русского текста — только t/tf" || bad "русский в коде пункта 16" "$o"
# tn: три формы — по-русски, две — по-английски, нет английского ключа — русские формы
o=$(lib eval 'M_ru_ZZ_W="копия|копии|копий"; M_en_ZZ_W="backup|backups"; M_ru_ZZ_R="строка|строки|строк"
    for n in 1 3 5 21; do printf "%s " "$n $(tn ZZ_W $n)"; done; k3_lang_set en
    for n in 1 3; do printf "%s " "$n $(tn ZZ_W $n)"; done; printf "%s" "$(tn ZZ_R 2)"' 2>&1)
[ "$o" = "1 копия 3 копии 5 копий 21 копия 1 backup 3 backups строки" ] && ok "tn: ru — три формы, en — one|other, нет перевода — русские формы" || bad "tn" "$o"
# Меню 16 на английском: обычное (nft) и после бесплатной версии; неверный ввод; 80×24
t_m16en() { k3_lang_set en; clear() { printf '\f'; }; sleep() { :; }; K3_TEST_TTY=1; k3_engine_menu; }
use_root en16a; fake_ctl; engine_nft; prog_ready
o=$(printf 'abc\n0\n' | K3_TERM_COLS=80 lib t_m16en 2>&1 | strip)
c=$(printf '%s\n' "$o" | en_cyr)
[ -z "$c" ] && printf '%s' "$o" | grep -q '━━━ Forwarding engine and migration ━━━' && printf '%s' "$o" | grep -q 'Nothing needs changing here — 0 to go back.' \
  && printf '%s' "$o" | grep -q '1) Switch to nftables — already done' && printf '%s' "$o" | grep -q '4) Go back to Kaskad 2.13' \
  && printf '%s' "$o" | grep -q '6) Settings backup — move to another server' && printf '%s' "$o" | grep -q '0) Back' \
  && printf '%s' "$o" | grep -q 'No such item: a number from 1 to 6, 0 — back.' \
  && ok "меню 16 по-английски: без кириллицы, пункты, «No such item»" || bad "меню 16 en" "$c | $o"
use_root en16b; fake_ctl; prog_ready
printf '{\n  "engine": "nft",\n  "forwards": [\n    {"proto": "tcp", "port": 443, "to": "2.2.2.2", "to_port": 443, "origin": "free"}\n  ],\n  "schema": 1\n}\n' > "$KASKAD_DIR/state.json"
o=$(printf '0\n' | K3_TERM_COLS=80 lib t_m16en 2>&1 | strip)
[ -z "$(printf '%s\n' "$o" | en_cyr)" ] && printf '%s' "$o" | flat | grep -q '4) Install Kaskad PRO 2.13 — the previous PRO version instead of this one (not the free one); the gateway (17) is removed' \
  && [ "$(printf '%s' "$o" | fit)" = "1 ok" ] && ok "меню 16 после бесплатной версии по-английски: «Install Kaskad PRO 2.13», 80×24" || bad "меню 16 en: бесплатная" "$(printf '%s' "$o" | fit) $o"
# Неполадка движка (jq: слова из каталога, поля net status — машинные)
use_root en16c; fake_ctl; engine_nft; prog_ready
o=$(lib eval 'k3_lang_set en; k3ctl() { echo "{\"engine\": \"nft\", \"table\": {\"present\": true, \"in_sync\": false}, \"services\": {\"kaskad-net.service\": {\"enabled\": false}}}"; }
    echo "[$(k3_engine_trouble)]"; k3_lang_set ru; echo "[$(k3_engine_trouble)]"' 2>&1)
[ "$o" = "[the kaskad table does not match the settings; service kaskad-net.service is not enabled]
[таблица kaskad не совпадает с настройками; служба kaskad-net.service не включена]" ] && ok "неполадка движка: en и ru — из каталога, имя службы на месте" || bad "k3_engine_trouble" "$o"
# Итог переезда по коду kaskadctl migrate apply — английские строки (ядро не печатает: k3_ctl_folded подменён)
printf '{\n  "engine": "legacy"\n}\n' > "$KASKAD_DIR/state.json"
o=$(lib eval 'k3_lang_set en; k3_restart_daemons() { :; }; k3_can_ask() { return 1; }
    for rc in 0 3 4 5 6 1; do k3_ctl_folded() { return $RC; }; RC=$rc k3_migrate_apply; done' 2>&1 | strip)
[ -z "$(printf '%s\n' "$o" | en_cyr)" ] && printf '%s' "$o" | grep -q 'Moving forwards to nftables: backup, switch, check' \
  && printf '%s' "$o" | grep -q 'The move is canceled — forwarding works the old way.' && printf '%s' "$o" | grep -q 'The check failed' \
  && printf '%s' "$o" | grep -q 'Kaskad is already changing something' && printf '%s' "$o" | grep -q 'The move failed (code 1)' \
  && ok "16 → 1: итог переезда по коду ядра — по-английски" || bad "k3_migrate_apply en" "$o"
# 16 → 2 и 16 → 4 по-английски — экран и вопрос, ответ «отмена»: ничего не тронуто
use_root en16d; fake_ctl; engine_nft
t_rben() { k3_lang_set en; init_config >/dev/null 2>&1; k3_rollback_interactive; echo "RC=$?"; }
o=$(printf '2\n' | lib t_rben 2>&1 | strip)
[ -z "$(printf '%s\n' "$o" | en_cyr)" ] && printf '%s' "$o" | flat | grep -q 'Forwards will go back to iptables in the Kaskad 2.13 format' \
  && printf '%s' "$o" | grep -q '1) Bring back iptables' && printf '%s' "$o" | grep -q '2) Cancel' && ! ctl_log | grep -q rollback \
  && ok "16 → 2 по-английски: что будет, «1) Bring back iptables / 2) Cancel», отмена — ничего не тронуто" || bad "16 → 2 en" "$o"
ret_setup en; t_reten() { k3_lang_set en; k3_return_to_2x; echo "RET=$?"; }
o=$(printf '2\n' | lib t_reten 2>&1 | strip)
[ -z "$(printf '%s\n' "$o" | en_cyr)" ] && printf '%s' "$o" | grep -q '━━━ Go back to Kaskad 2.13 ━━━' && printf '%s' "$o" | grep -q 'What will happen:' \
  && printf '%s' "$o" | grep -q 'the gokaskad command will become Kaskad PRO 2.13' && printf '%s' "$o" | grep -q 'Go back to Kaskad 2.13?' \
  && ! ctl_log | grep -q rollback && ok "16 → 4 по-английски: «What will happen», вопрос цифрами, отмена" || bad "16 → 4 en" "$o"
# Копия настроек (16 → 6) по-английски: меню, что войдёт (jq — слова каталога), пароль, сохранение, загрузка с отменой
use_root en16e; fake_ctl; : > "$CALLS/stdin.log"
t_bkmen() { k3_lang_set en; clear() { :; }; sleep() { :; }; K3_TEST_TTY=1; k3_backup_menu; }
o=$(printf '7\n0\n' | lib t_bkmen 2>&1 | strip)
[ -z "$(printf '%s\n' "$o" | en_cyr)" ] && printf '%s' "$o" | grep -q '━━━ Settings backup ━━━' && printf '%s' "$o" | grep -q ' 3) Undo the last backup load' \
  && printf '%s' "$o" | grep -q 'No such item: a number from 1 to 3, 0 — back.' && ok "16 → 6 по-английски: меню, неверный ввод" || bad "16 → 6 en" "$o"
bkf summary.json '{"ok": true, "summary": {"gateway": {"present": true, "users": 2, "devices": 3, "inbounds": []}, "exits": {"keys": 4, "subs": 1}, "forwards": {"forwards": 1, "names": 0}, "site": {"domain": "vpn.example.com"}, "bot": {"token": true}, "mcp": {"keys": 2}}}'
bkf export.json '{"ok": true, "file": "/root/kaskad-x.kbk", "size": 4096}'
t_bksaveen() { k3_lang_set en; K3_TEST_TTY=1 k3_backup_save; echo "RC=$?"; }
o=$(printf '1\nSecretPass9\nSecretPass9\n' | lib t_bksaveen 2>&1 | strip | flat)
o2=$(printf '1\nSecretPass9\nOther1234\n' | lib t_bksaveen 2>&1 | strip | flat)
[ -z "$(printf '%s\n' "$o" "$o2" | en_cyr)" ] && printf '%s' "$o" | grep -q 'The backup will include: • gateway: users 2, devices 3, protocols: none • exits: keys 4, subscriptions 1 • forwards: 1, server names: 0 • domain vpn.example.com • Telegram bot • AI agent keys: 2 • panel settings (without the password)' \
  && printf '%s' "$o" | grep -q 'Include the Telegram bot and AI agent keys in the backup? 1) Yes 2) No' \
  && printf '%s' "$o" | grep -q '\[OK\] Backup saved: /root/kaskad-x.kbk (4 KB).' && printf '%s' "$o" | grep -q 'scp root@203.0.113.10:/root/kaskad-x.kbk .' \
  && printf '%s' "$o2" | grep -q 'The passwords do not match — no backup made.' && ctl_log | grep -qx 'backup export --json' \
  && ok "16 → 6 → 1 по-английски: «The backup will include» (jq — слова каталога), «Backup saved … (4 KB)», пароли не совпали" || bad "копия en: сохранить" "$o | $o2"
o=$(lib eval 'k3_backup_cmd() { K3_GW_OUT="{\"summary\": {\"gateway\": {\"present\": true, \"users\": 2, \"devices\": 3, \"inbounds\": [\"wg\", \"awg2\"]}, \"exits\": {\"keys\": 4, \"subs\": 1}, \"forwards\": {\"forwards\": 1, \"names\": 0}, \"site\": {\"domain\": \"\"}, \"bot\": {\"token\": false}, \"mcp\": {\"keys\": 0}}, \"inbound_titles\": [\"WireGuard\", \"AmneziaWG 2.0\"]}"; }
    k3_choose() { return 1; }; k3_backup_save' 2>&1 | strip | flat)
[ "$o" = " В копию войдёт: • шлюз: пользователей 2, устройств 3, протоколы: WireGuard, AmneziaWG 2.0 • выходы: ключей 4, подписок 1 • пробросы: 1, имён серверов: 0 • настройки панели (без пароля) " ] \
  && ok "16 → 6 → 1 по-русски: «В копию войдёт» — протоколы названиями (inbound_titles ядра), не кодами wg, awg2" || bad "копия ru: что войдёт" "[$o]"
mkdir -p "$KASKAD_ROOT/root"; echo x > "$KASKAD_ROOT/root/old.kbk"
bkf import.json '{"ok": true, "applied": false, "preview": {"created": "2026-10-09", "version": "3.3.2", "text": ["• gateway — will be moved: 2 users"]}}'
t_bkloaden() { k3_lang_set en; K3_TEST_TTY=1 k3_backup_load; echo "RC=$?"; }
o=$(printf '\nSecretPass9\n\n' | lib t_bkloaden 2>&1 | strip | flat)
[ -z "$(printf '%s\n' "$o" | en_cyr)" ] && printf '%s' "$o" | grep -q "scp file.kbk root@203.0.113.10:/root/) and enter its path" \
  && printf '%s' "$o" | grep -q 'Backup from Oct 9, 2026 (Kaskad 3.3.2). What will happen:' && printf '%s' "$o" | grep -q '1) Load 0) Cancel' \
  && printf '%s' "$o" | grep -q 'Canceled — nothing was changed.' && ! ctl_log | grep -q -- '--yes' \
  && ok "16 → 6 → 2 по-английски: куда положить файл, предпросмотр, «1) Load 0) Cancel», Enter — отмена" || bad "копия en: загрузка" "$o"

# <<< G

echo "── 49H. английский: меню 17 «Шлюз», 18 «Веб-панель», 19 «Домен и сайт» (KASKAD_LANG=en) ──"
# Код функций этих меню — без кириллицы (тексты — из каталогов lib/kaskad/i18n/sh), кроме строк с пометкой
# «# TODO i18n-exchange» (меню сопоставляет текст ядра) и «# i18n-keep: причина». i18n_code <подпись> <regex функций>…
i18n_code() {
    local label="$1" o; shift
    o=$(python3 "$ROOT_DIR/tools/i18n_h/nocyr.py" "$S" "$@" 2>&1)
    if [ $? -eq 0 ]; then ok "$label: в коде нет русского текста ($(printf '%s' "$o" | tail -n 1))"
    else bad "$label: русский текст в коде" "$(printf '%s\n' "$o" | head -n 12)"; fi
}
# Вывод экрана на английском — без кириллицы: en_clean <подпись> "<вывод>" [regex разрешённого — данные: имена…]
en_clean() {
    local label="$1" out="$2" allow="${3:-}" hit
    hit=$(printf '%s\n' "$out" | strip | ALLOW="$allow" python3 -c '
import os, re, sys
cyr = re.compile("[\u0400-\u04ff]"); allow = os.environ.get("ALLOW") or ""
for n, l in enumerate(sys.stdin.read().split("\n"), 1):
    t = re.sub(allow, "", l) if allow else l
    if cyr.search(t):
        print("%d: %s" % (n, l[:140]))' | head -n 8)
    if [ -n "$out" ] && [ -z "$hit" ]; then ok "$label: по-английски, без кириллицы"
    else bad "$label: кириллица на английском экране" "${hit:-пустой вывод}"; fi
}
# Функция теста на английском: en <функция> [аргументы] (как lib, язык — en)
_en_run() { k3_lang_set en; k3_log_lang() { echo en; }; "$@"; }   # сервер на английском: экран и журнал (k3_log_lang — язык из настроек)
en() { lib _en_run "$@"; }
t_tn() { local n; for n in 1 2 5 11 21; do printf '%s %s; ' "$n" "$(tn ZZ_PL "$n")"; done; echo
         M_en_ZZ_PL='inbound|inbounds'; k3_lang_set en; for n in 1 2 5; do printf '%s %s; ' "$n" "$(tn ZZ_PL "$n")"; done; echo
         M_en_ZZ_PL=''; printf '%s\n' "$(tn ZZ_PL 3)"; M_ru_ZZ_ONE='штук'; printf '%s\n' "$(tn ZZ_ONE 3)"; }
o=$(lib eval "M_ru_ZZ_PL='вход|входа|входов'; t_tn" 2>&1)
[ "$o" = "1 вход; 2 входа; 5 входов; 11 входов; 21 вход; 
1 inbound; 2 inbounds; 5 inbounds; 
входа
штук" ] && ok "tn: формы по каталогу — три (ru), две (en), нет перевода — русские по русскому правилу" || bad "tn" "$o"
for f in "$ROOT_DIR"/tools/i18n_h/*_test.sh; do [ -f "$f" ] && . "$f"; done

echo "── 52. журнал — на сохранённом языке сервера (сведение 3.3.2): не окружения, не меню ──"
# ядро зовёт gokaskad с KASKAD_LANG запроса (панель/бот): экран — на нём, запись журнала — на языке из настроек
use_root jlang; printf 'KASKAD_LANG="en"\n' > "$KASKAD_DIR/config"
t_jlang() { log_action "$(K3_LANG=$(k3_log_lang) tf LOG_ROTATE /var/log/kaskad.log)"; t LOG_PANEL_INSTALLED; echo; k3_log_lang; }
o=$(KASKAD_LANG=ru lib t_jlang 2>&1)
[ "$o" = "PANEL: установлена из меню
en" ] && grep -q 'LOGROTATE: /var/log/kaskad.log — Kaskad log rotation$' "$KASKAD_ROOT/var/log/kaskad.log" \
  && ok "журнал: KASKAD_LANG=ru в окружении, в настройках en — запись по-английски, экран по-русски" \
  || bad "журнал на языке сервера" "$o | $(cat "$KASKAD_ROOT/var/log/kaskad.log" 2>/dev/null)"
rm -f "$KASKAD_DIR/config"; o=$(KASKAD_LANG=en lib k3_log_lang 2>&1)
[ "$o" = ru ] && ok "журнал: язык не сохранён — ru (как по умолчанию), а не язык окружения" || bad "журнал без настроек" "$o"
# каждая запись log_action с текстом каталога — на языке журнала (k3_log_lang), а не на языке экрана
o=$(grep -n 'log_action "' "$S" | sed 's/^\([0-9]*:\).*log_action "/\1/' | grep -E '\$\((t|tf) ' | grep -v 'read -p' | head -5)
[ -z "$o" ] && ok "install.sh: все log_action с t/tf — через K3_LANG=\$(k3_log_lang)" || bad "log_action на языке экрана" "$o"

echo "── 53. живая проверка 3.3.2 (№9–12): ширина 80, язык меню из настроек, рамка удаления ──"
# Строки (stdin) не шире 80 столбцов на экране — «ok» или первые широкие (ширина знака — как у терминала)
w80() {
    python3 -c '
import sys, unicodedata
def width(s):
    return sum(0 if unicodedata.combining(c) else (2 if unicodedata.east_asian_width(c) in "WF" else 1) for c in s)
bad = [l for l in sys.stdin.read().split("\n") if width(l) > 80]
print("ok" if not bad else " | ".join("%d: %s" % (width(l), l) for l in bad[:4]))'
}
# №9: экраны, где в окне 80×24 строки рвались посреди слова, — по словам в ширину окна, текст целиком
ret_setup w80r; gw_st true '{"wg": {}}' 1 1
o=$(printf '2\n' | K3_TERM_COLS=80 lib t_reten 2>&1 | sed "s#$KASKAD_ROOT##g" | strip); r=$(printf '%s' "$o" | w80)
[ "$r" = ok ] && printf '%s' "$o" | flat | grep -qF 'the gateway will be removed: Kaskad 2.13 has no gateway, and its protocols and services would be left unattended' \
  && printf '%s' "$o" | flat | grep -qF 'the gokaskad command will become Kaskad PRO 2.13; the bot and monitoring will restart on it;' \
  && ok "16 → 4 по-английски в окне 80: строки по словам, текст целиком" || bad "16 → 4 в 80 столбцов" "$r | $o"
use_root w80x; fake_ctl; gw_st true '{"wg": {}}' 1 1; gwf exits.json "$H2_EXITS"
o=$(printf '0\n' | K3_TERM_COLS=80 en h2_exits 2>&1 | strip); r=$(printf '%s' "$o" | w80)
[ "$r" = ok ] && printf '%s' "$o" | flat | grep -qF '3) Oslo (AmneziaWG 3.1, 198.51.100.33) — won’t work: no AmneziaWG core (needs a GitHub key — menu 8 → 6, then this step again)' \
  && printf '%s' "$o" | grep -q 'menu 8 → 6' \
  && ok "17 → 3 по-английски в окне 80: строка выхода без ядра — по словам, путь «8 → 6» не рвётся" || bad "17 → 3 в 80 столбцов" "$r | $o"
o=$(printf 'vless://H2KEY\n.\n\n' | K3_TERM_COLS=80 en h2_add_bad 2>&1 | strip); r=$(printf '%s' "$o" | w80)
[ "$r" = ok ] && printf '%s' "$o" | flat | grep -qF '✗ The exit didn’t respond: check the key (is it still valid, is the VPN server on) — or add another one.' \
  && ok "17 → 3 → 3 по-английски в окне 80: «The exit didn’t respond…» по словам" || bad "17 → 3 → 3 в 80 столбцов" "$r | $o"
use_root w80p; fake_ctl; gw_st true '{"wg": {}, "awg3": {}}' 3 2
gwf user_list.json '{"ok": true, "users": [{"id": "u-00000001", "name": "Anna", "route": null, "enabled": true}]}'
gwf device_list.json '{"ok": true, "devices": [{"id": "d-00000001", "user": "u-00000001", "name": "Phone", "kind": "phone", "platform": "android", "login": "phone-0001", "access": ["awg3"], "route": null, "enabled": true}]}'
gwf exits.json "$H2_EXITS"; gwf inbound_list.json "$(gw_list "wg awg3")"
o=$(printf '6\n1\n2\n1\n\n0\n' | K3_TERM_COLS=80 en h2_people 2>&1 | strip); r=$(printf '%s' "$o" | w80)
[ "$r" = ok ] && printf '%s' "$o" | flat | grep -qF 'As its group (block) — the default' \
  && ok "17 → 2 → 6 по-английски в окне 80: маршруты и «при отказе» — по словам" || bad "17 → 2 → 6 в 80 столбцов" "$r | $o"
use_root w80m; engine_nft
o=$(printf '1\nssh-ed25519 AAAATEST me\nlaptop\n\n3\n\n0\n' | K3_TERM_COLS=80 en t_h4_keys 2>&1 | strip); r=$(printf '%s' "$o" | w80)
o2=$(printf '1\n\n0\n' | K3_TERM_COLS=80 lib t_h4_keys 2>&1 | strip)
[ "$r" = ok ] && printf '%s' "$o" | grep -qx 'Create a key on your computer:' && printf '%s' "$o" | grep -qx '  ssh-keygen -t ed25519 -f ~/.ssh/kaskad_agent -N ""' \
  && printf '%s' "$o" | flat | grep -qF 'AI agents (Claude Code, Claude Desktop, Cursor) manage the server via kaskad-mcp over SSH.' \
  && printf '%s' "$o" | flat | grep -qF 'Output in root’s ~/.bashrc (greeting, echo) breaks the agent link — remove it or wrap it in if [ -t 1 ].' \
  && printf '%s' "$o" | grep -qx '  claude mcp add kaskad -- ssh root@203.0.113.10 kaskad-mcp' \
  && printf '%s' "$o2" | grep -qx 'Сделайте ключ на своём компьютере:' && printf '%s' "$o2" | grep -qx '  ssh-keygen -t ed25519 -f ~/.ssh/kaskad_agent -N ""' \
  && ok "18 → 6 (MCP) в окне 80: пояснения по словам, команда ssh-keygen — отдельной строкой целиком (en, ru)" || bad "18 → 6 в 80 столбцов" "$r | $o"
# №11: рамка «Uninstall Kaskad PRO» — три строки одной ширины (64) на обоих языках; ширина надписи — по знакам на экране
un_worst w80u
for l in en ru; do
    o=$(lib t_un50 "$l" '2\n\n' 2>&1 | strip)
    r=$(printf '%s\n' "$o" | python3 -c '
import sys, unicodedata
def width(s):
    return sum(0 if unicodedata.combining(c) else (2 if unicodedata.east_asian_width(c) in "WF" else 1) for c in s)
import re
f = [m.group(0) for m in (re.search("[╔║╚].*", l) for l in sys.stdin.read().split("\n")) if m]   # до рамки — clear и пр.
m = f[1][1:-1] if len(f) == 3 else ""
lp, rp = len(m) - len(m.lstrip(" ")), len(m) - len(m.rstrip(" "))
print(len(f), sorted(set(width(l) for l in f)), "center" if m and abs(lp - rp) <= 1 else "skew")')
    [ "$r" = "3 [64] center" ] \
      && ok "15 ($l): рамка ⚠ … ⚠ — три строки по 64 столбца, надпись по центру" || bad "15 ($l): рамка" "$r | $(printf '%s' "$o" | head -n 4)"
done
# №10: открытое главное меню берёт язык из настроек при каждой перерисовке (сменили в панели или боте — без перезапуска);
# KASKAD_LANG из окружения запуска главнее настроек только до первой смены строки в настройках
t_lsync() {   # перерисовки главного меню: язык и пункт «0»; SW — что записать в настройки на перерисовках 1, 2, …
    local -a sw; read -r -a sw <<< "${SW:-}"; DRAWN=0
    k3_menu_draw() { echo "DRAW $K3_LANG $(t MM_I0) $KASKAD_LANG"; [ -n "${sw[$DRAWN]:-}" ] && [ "${sw[$DRAWN]}" != - ] && save_config_val KASKAD_LANG "${sw[$DRAWN]}"; DRAWN=$((DRAWN + 1)); }
    clear() { :; }; show_menu
}
use_root lsync; printf 'KASKAD_LANG="ru"\n' > "$KASKAD_DIR/config"
o=$(printf '\n\n' | SW="en" K3_MENU_REFRESH=5 lib t_lsync 2>&1)
[ "$o" = "DRAW ru Выход ru
DRAW en Quit en
DRAW en Quit en" ] && ok "меню: язык сменили в настройках (панель, бот) — следующая перерисовка на новом, kaskadctl — тоже (KASKAD_LANG)" || bad "язык меню из настроек" "$o"
printf 'KASKAD_LANG="ru"\n' > "$KASKAD_DIR/config"
o=$(printf '\n\n\n' | KASKAD_LANG=en SW="- en ru" K3_MENU_REFRESH=5 lib t_lsync 2>&1)
[ "$o" = "DRAW en Quit en
DRAW en Quit en
DRAW en Quit en
DRAW ru Выход ru" ] && ok "меню: KASKAD_LANG=en при запуске главнее настроек (ru), пока их не сменили; сменили — берётся настройка" || bad "язык из окружения и настроек" "$o"

echo "── 54. R6 ⚠РФ: галочка в меню правил и у пользователя/устройства, шапка, KASKAD_CONFIRM_NOT_RF (rf-mark §5.2, §5.3) ──"
use_root rf6
RF_ST="$W/rf6-status.json"; RF_LOG="$W/rf6-calls"
# rules status --json: $1 mode, $2 required, $3 uncovered (JSON), $4 default, $5 default_applied, $6 default_why, $7 имя второй организации
rf_st() {
    printf '{"ok": true, "default": "%s", "default_applied": "%s", "default_why": "%s", "block_ads": true, "block_torrent": false,
 "lists": {"label": "geoip 202610040942"}, "ru_direct": 0, "devices": [], "users": [],
 "rf": {"required": %s, "mode": "%s", "orgs": [{"id": "meta", "name": "Meta"}, {"id": "fbk", "name": "%s"}], "uncovered": %s}}\n' \
        "${4:-all_exit}" "${5:-${4:-all_exit}}" "${6:-}" "$2" "$1" "${7:-ФБК}" "$3" > "$RF_ST"
}
# ядро — подделкой функции k3ctl: вызовы в $RF_LOG, rules status — из $RF_ST; пауза и шапка экрана — без ожидания
_rf_run() {
    k3ctl() { printf '%s\n' "$*" >> "$RF_LOG"; case "$1 $2" in "rules status") cat "$RF_ST" ;; *) echo "готово" ;; esac; }
    k3_gw_pause() { :; }; clear() { printf '\f'; }; K3_TEST_TTY=1; K3_TERM_COLS=80; "$@"
}
rf_run() { : > "$RF_LOG"; lib _rf_run "$@"; }
rf_en() { : > "$RF_LOG"; lib _en_run _rf_run "$@"; }
rf_log() { grep -v '^rules status' "$RF_LOG" 2>/dev/null; }

rf_st none true '[]'
o=$(printf '1\n\n0\n' | rf_run k3_rules_menu 2>&1 | strip)
[ -z "$(rf_log)" ] && printf '%s' "$o" | flat | grep -qF '⚠ Данная организация признана экстремистской на территории Российской Федерации (Meta, ФБК)' \
  && printf '%s' "$o" | flat | grep -qF 'Подтвердите: скрипт не используется на территории РФ.' \
  && printf '%s' "$o" | grep -q '^ *1) Подтверждаю — скрипт не используется на территории РФ$' && printf '%s' "$o" | grep -q '^ *2) Отмена$' \
  && printf '%s' "$o" | grep -q 'Выбор \[2\]: ' && printf '%s' "$o" | grep -q 'Отменено — ничего не изменилось.' \
  && ok "17 → 8 → 1 без согласия: блок владельца (Meta, ФБК), «1) Подтверждаю … 2) Отмена», Enter — «Отмена», ядро не зовётся" \
  || bad "галочка: Enter — отмена" "$(rf_log) | $o"
o=$(printf '1\n' | rf_run k3_rules_menu 2>&1 | strip)
[ -z "$(rf_log)" ] && ok "конец ввода на вопросе ⚠РФ — отмена, не согласие" || bad "EOF на галочке" "$(rf_log)"
o=$(printf '1\n3\n1\n0\n' | rf_run k3_rules_menu 2>&1 | strip)
[ "$(rf_log)" = "rules default ru_direct --confirm-not-rf --via ssh --orgs meta,fbk" ] && printf '%s' "$o" | grep -q 'Нет такого пункта: введите номер от 1 до 2' \
  && ok "1 → «Подтверждаю»: kaskadctl rules default ru_direct --confirm-not-rf --via ssh --orgs meta,fbk (показанные); 3 — «Нет такого пункта»" || bad "галочка: 1" "$(rf_log) | $o"
[ "$(grep -c '^rules status' "$RF_LOG")" = 2 ] && ok "меню правил: rules status — один раз на экран, вопрос ⚠РФ берёт прочитанное (REV-s1-r67 П-1)" \
  || bad "лишний rules status" "$(grep -c '^rules status' "$RF_LOG")"
o=$(printf '1\n0\n' | rf_run k3_rules_menu 2>&1 | strip)
[ -z "$(rf_log)" ] || bad "галочка: 0 — не согласие" "$(rf_log)"
rf_st full false '[]'
o=$(printf '1\n0\n' | rf_run k3_rules_menu 2>&1 | strip)
[ "$(rf_log)" = "rules default ru_direct" ] && ! printf '%s' "$o" | grep -q 'Подтверждаю' && ! printf '%s' "$o" | grep -q '^ *6)' \
  && ok "согласие уже есть (full): «Россия напрямую» без вопроса, пункта 6 нет" || bad "full: без вопроса" "$(rf_log) | $o"
rf_st full false '[]' ru_direct
o=$(printf '1\n0\n' | rf_run k3_rules_menu 2>&1 | strip)
[ "$(rf_log)" = "rules default all_exit" ] && ok "«всё через выход» — без галочки" || bad "all_exit без галочки" "$(rf_log)"
rf_st none true '[]'
o=$(printf '6\n0\n' | rf_run k3_rules_menu 2>&1 | strip)
[ -z "$(rf_log)" ] && printf '%s' "$o" | grep -q 'Нет такого пункта: номер от 1 до 5, 0 — назад.' \
  && ok "режим none: пункта «Подтвердить ⚠РФ» нет, 6 — «Нет такого пункта» (1–5)" || bad "none: пункт 6" "$(rf_log) | $o"
rf_st legacy true '[]' ru_direct
o=$(printf '6\n\n6\n1\n0\n' | rf_run k3_rules_menu 2>&1 | strip)
[ "$(rf_log)" = "rules consent --confirm-not-rf --via ssh --orgs meta,fbk" ] \
  && printf '%s' "$o" | flat | grep -qF '⚠РФ: Россия напрямую работает как раньше. Во встроенном списке «всегда через выход» есть ресурсы ⚠РФ (Meta, ФБК).' \
  && printf '%s' "$o" | grep -q '^ 6) Подтвердить ⚠РФ: скрипт не используется на территории РФ$' \
  && [ "$(printf '%s' "$o" | fit 'Данная организация')" = "3 ok" ] \
  && ok "legacy: жёлтая строка в шапке правил, «6) Подтвердить ⚠РФ» — Enter отмена, 1 — rules consent --confirm-not-rf --via ssh --orgs; 80×24" \
  || bad "legacy: подтвердить" "$(rf_log) | $(printf '%s' "$o" | fit) | $o"
rf_st need true '[]' ru_direct all_exit no_consent
o=$(printf '0\n' | rf_run k3_rules_menu 2>&1 | strip)
printf '%s' "$o" | flat | grep -qF 'По умолчанию: Россия напрямую (не включена: нет подтверждения ⚠РФ — всё через выход)' \
  && printf '%s' "$o" | flat | grep -qF '⚠РФ: Россия напрямую выбрана, но не включена: нет подтверждения — пункт 6.' && printf '%s' "$o" | grep -q '^ 6) ' \
  && ok "need: «По умолчанию: Россия напрямую (не включена: нет подтверждения ⚠РФ …)», жёлтая строка, пункт 6" || bad "need" "$o"
rf_st full false '["fbk"]' ru_direct
o=$(printf '6\n1\n0\n' | rf_run k3_rules_menu 2>&1 | strip)
[ "$(rf_log)" = "rules consent --confirm-not-rf --via ssh --orgs meta,fbk" ] \
  && printf '%s' "$o" | flat | grep -qF 'в списке «всегда через выход» появилась организация ⚠РФ (ФБК). Подтвердите — пункт 6.' \
  && ok "full + новая организация: строка «появилась организация ⚠РФ (ФБК)», пункт 6 спрашивает и при согласии (ask)" || bad "uncovered" "$(rf_log) | $o"
# человек и устройство («Пользователи и устройства» → 10, 11)
rf_st none true '[]'
o=$(printf '2\n\n' | rf_run k3_rules_target device d-1 Телефон 2>&1 | strip)
[ -z "$(rf_log | grep -v '^rules status')" ] && printf '%s' "$o" | flat | grep -qF 'Российской Федерации (Meta, ФБК)' \
  && ok "устройство → «Россия напрямую» без согласия: блок, Enter — отмена" || bad "устройство: отмена" "$(rf_log) | $o"
o=$(printf '2\n1\n' | rf_run k3_rules_target user u-1 Мама 2>&1 | strip)
[ "$(rf_log)" = "rules profile user u-1 ru_direct --confirm-not-rf --via ssh --orgs meta,fbk" ] && ok "пользователь → «Россия напрямую» с галочкой: --confirm-not-rf --via ssh --orgs meta,fbk" || bad "пользователь: галочка" "$(rf_log)"
o=$(printf '3\n' | rf_run k3_rules_target device d-1 Телефон 2>&1 | strip)
[ "$(rf_log)" = "rules profile device d-1 all_exit" ] && ! printf '%s' "$o" | grep -q 'Подтверждаю' && ok "устройство → «всё через выход» — без вопроса" || bad "устройство: all_exit" "$(rf_log) | $o"
o=$(printf '1\n' | rf_run k3_rules_target device d-1 Телефон 2>&1 | strip)
[ "$(rf_log)" = "rules profile device d-1 inherit" ] || bad "устройство: inherit" "$(rf_log)"
# REV-338-sec: сломанный реестр (.rf.error, вместо имён — коды) — меню галочку не спрашивает, строка отказа ядра
# (rules.rf.unread), ядро не зовётся: меню правил (1 и 6), человек и устройство; согласие уже есть — как раньше
RF_UNREAD='Не прочитал список организаций ⚠РФ (реестр повреждён) — согласие не записано. Обновите Kaskad.'
rf_broken() { jq -c '.rf.orgs = [{"id": "meta", "name": "meta"}, {"id": "fbk", "name": "fbk"}] | .rf.error = "Реестр ⚠РФ повреждён — пометка ⚠РФ не работает. Обновите Kaskad."' "$RF_ST" > "$RF_ST.b" && mv "$RF_ST.b" "$RF_ST"; }
rfu=0
rf_st none true '[]'; rf_broken
o=$(printf '1\n1\n0\n' | rf_run k3_rules_menu 2>&1 | strip)
[ -z "$(rf_log)" ] && ! printf '%s' "$o" | grep -q 'Подтверждаю\|экстремистской' && printf '%s' "$o" | flat | grep -qF "$RF_UNREAD" \
  || { rfu=1; bad "сломанный реестр: меню правил 1" "$(rf_log) | $o"; }
rf_st legacy true '[]' ru_direct; rf_broken
o=$(printf '6\n0\n' | rf_run k3_rules_menu 2>&1 | strip)
[ -z "$(rf_log)" ] && ! printf '%s' "$o" | grep -q 'Подтверждаю' && printf '%s' "$o" | flat | grep -qF "$RF_UNREAD" \
  || { rfu=1; bad "сломанный реестр: пункт 6" "$(rf_log) | $o"; }
rf_st none true '[]'; rf_broken
o=$(printf '2\n1\n' | rf_run k3_rules_target user u-1 Мама 2>&1 | strip)
[ -z "$(rf_log)" ] && ! printf '%s' "$o" | grep -q 'Подтверждаю' && printf '%s' "$o" | flat | grep -qF "$RF_UNREAD" \
  || { rfu=1; bad "сломанный реестр: пользователь" "$(rf_log) | $o"; }
rf_st full false '[]'; rf_broken
o=$(printf '1\n0\n' | rf_run k3_rules_menu 2>&1 | strip)
[ "$(rf_log)" = "rules default ru_direct" ] && ! printf '%s' "$o" | grep -q 'Подтверждаю' || { rfu=1; bad "сломанный реестр, согласие есть" "$(rf_log) | $o"; }
rf_st none true '[]' all_exit all_exit "" FBK; rf_broken
o=$(printf '1\n0\n' | rf_en k3_rules_menu 2>&1 | strip)
[ -z "$(rf_log)" ] && printf '%s' "$o" | flat | grep -qF 'Could not read the list of ⚠RU organizations (the registry is broken) — no consent written. Update Kaskad.' \
  && ! printf '%s' "$o" | grep -q 'I confirm' || { rfu=1; bad "сломанный реестр: en" "$(rf_log) | $o"; }
[ "$rfu" = 0 ] && ok "сломанный реестр ⚠РФ в меню (правила 1 и 6, пользователь, en): галочки нет, строка отказа ядра, ядро не зовётся; согласие есть — без вопроса" || true
# английский: блок и галочка — дословно как rules.rf.block / rules.rf.consent ядра, без кириллицы
rf_st none true '[]' all_exit all_exit "" FBK
o=$(printf '1\n\n0\n' | rf_en k3_rules_menu 2>&1 | strip)
[ -z "$(rf_log)" ] && printf '%s' "$o" | flat | grep -qF '⚠ This organization is recognized as extremist in the Russian Federation (Meta, FBK)' \
  && printf '%s' "$o" | grep -q '1) I confirm — the script is not used in the Russian Federation$' && printf '%s' "$o" | grep -q 'Choice \[2\]: ' \
  && ! printf '%s' "$o" | grep -q '[А-Яа-яЁё]' && ok "по-английски: блок, «I confirm …», «Choice [2]», без кириллицы" || bad "en: галочка" "$(printf '%s' "$o" | grep '[А-Яа-яЁё]') | $o"
rf_st legacy true '[]' ru_direct all_exit "" FBK
o=$(printf '0\n' | rf_en k3_rules_menu 2>&1 | strip)
printf '%s' "$o" | flat | grep -qF '⚠RU: Russian sites direct works as before.' && ! printf '%s' "$o" | grep -q '[А-Яа-яЁё]' \
  && [ "$(printf '%s' "$o" | K3_TERM_COLS=80 w80)" = ok ] && ok "по-английски: legacy — «⚠RU: … works as before», в 80 столбцов" || bad "en: legacy" "$o"
# шапка главного меню: одна жёлтая строка у настроенного шлюза (summary --json → .rf)
_rf_head() { K3_HEAD_OK=1; K3_HEAD_JSON="$2"; k3_head_parse; K3_H_GWSET="$1"; jq() { echo JQ >&2; command jq "$@"; }
            echo -e "$(k3_rules_head_line)|"; }   # k3_hl: цвет — как у строк шапки; поля — из k3_head_parse, без своего jq
o=$(lib _rf_head true '{"ok": true, "rf": {"mode": "legacy", "uncovered_names": ""}}' 2>&1 | strip)
[ "$o" = "⚠РФ: Россия напрямую работает как раньше — подтвердите: 17 → 8|" ] && ok "шапка: legacy — «⚠РФ: … работает как раньше — подтвердите: 17 → 8»" || bad "шапка legacy" "$o"
o=$(lib _rf_head true '{"ok": true, "rf": {"mode": "need", "uncovered_names": ""}}' 2>&1 | strip)
[ "$o" = "⚠РФ: Россия напрямую не включена, нет подтверждения — 17 → 8|" ] || bad "шапка need" "$o"
o=$(lib _rf_head true '{"ok": true, "rf": {"mode": "full", "uncovered_names": "ФБК"}}' 2>&1 | strip)
[ "$o" = "⚠РФ: новая организация в списке (ФБК) — подтвердите: 17 → 8|" ] || bad "шапка uncovered" "$o"
o=$(lib _rf_head true '{"ok": true, "rf": {"mode": "full", "uncovered_names": ""}}' 2>&1; lib _rf_head true '{"ok": true, "rf": {"mode": "none"}}' 2>&1
    lib _rf_head false '{"ok": true, "rf": {"mode": "legacy"}}' 2>&1; lib _rf_head true '{"ok": true, "rf": null}' 2>&1)
[ "$o" = "|
|
|
|" ] && ok "шапка: full без новых, none, шлюз не настроен, нет .rf — ничего" || bad "шапка: пусто" "$o"
grep -q 'line=$(k3_rules_head_line); \[ -n "$line" \] && k3_hl 1 "$line"' "$S" && ok "k3_head_lines: строка ⚠РФ — через k3_rules_head_line" || bad "шапка: вызов"
# установка без мастера: KASKAD_CONFIRM_NOT_RF — только «1» и только первая установка
_rf_env2() { k3_gw_pause() { echo "[ПАУЗА]"; }; k3_rules_env_consent; }
_rf_env() { k3_lib_ok() { return 0; }; K3_FRESH="$1"; _rf_run _rf_env2; echo "RC=$?"; }
o=$(: > "$RF_LOG"; KASKAD_CONFIRM_NOT_RF=1 lib _rf_env 1 2>&1 | strip)
[ "$(rf_log)" = "rules default ru_direct --confirm-not-rf --via env --orgs meta,fbk" ] && printf '%s' "$o" | flat | grep -qF 'Россия напрямую включена (KASKAD_CONFIRM_NOT_RF=1' \
  && printf '%s' "$o" | grep -q 'RC=0' && ok "KASKAD_CONFIRM_NOT_RF=1, первая установка: rules default ru_direct --confirm-not-rf --via env --orgs <показанные>" || bad "env=1" "$(rf_log) | $o"
# К2-9 (T-KT2): перед согласием — блок ⚠РФ с организациями, согласие — на показанные (--orgs), блок выше строки «включена»
nb=$(printf '%s' "$o" | flat | grep -bo '⚠ Данная организация признана экстремистской на территории Российской Федерации (Meta, [^)]*)' | head -1 | cut -d: -f1)
nk=$(printf '%s' "$o" | flat | grep -bo 'Россия напрямую включена' | head -1 | cut -d: -f1)
[ -n "$nb" ] && [ -n "$nk" ] && [ "$nb" -lt "$nk" ] \
  && ok "KASKAD_CONFIRM_NOT_RF=1: блок ⚠РФ (Meta, …) напечатан до записи согласия, согласие — --orgs meta,fbk" || bad "env=1: блок ⚠РФ" "$o"
_rf_nost() { k3ctl() { printf '%s\n' "$*" >> "$RF_LOG"; case "$1 $2" in "rules status") echo "не JSON" ;; *) echo "готово" ;; esac; }; k3_gw_pause() { echo "[ПАУЗА]"; }; k3_rules_env_consent; }
_rf_env_nost() { k3_lib_ok() { return 0; }; K3_FRESH=1; _rf_run _rf_nost; echo "RC=$?"; }
o=$(: > "$RF_LOG"; KASKAD_CONFIRM_NOT_RF=1 lib _rf_env_nost 2>&1 | strip)
[ -z "$(rf_log)" ] && printf '%s' "$o" | flat | grep -qF 'не прочитал список организаций ⚠РФ — согласие не записано' && printf '%s' "$o" | grep -q 'RC=1' \
  && printf '%s' "$o" | grep -q '^\[ПАУЗА\]$' && ok "KASKAD_CONFIRM_NOT_RF=1, список организаций не прочитался — согласие не записано, строка и пауза" || bad "env: нет списка" "$(rf_log) | $o"
# К2-9 (REV-s1-kt2-final З-1): сломанный реестр — тот же отказ, что «не прочитался»: пустой список, .rf.error (коды
# вместо имён), коды без имён, имена без кодов — ядро не зовётся, согласия нет, строка и пауза, отметка остаётся
cp "$RF_ST" "$RF_ST.keep"; RF_MARK="$KASKAD_DIR/rf-env.pending"; rfbad=0
for f in '.rf.orgs = []' '.rf.orgs = [] | .rf.error = "Реестр ⚠РФ повреждён"' \
         '.rf.orgs = [{"id": "meta", "name": "meta"}] | .rf.error = "Реестр ⚠РФ повреждён"' \
         '.rf.orgs = [{"id": "meta"}, {"id": "fbk", "name": "ФБК"}]' '.rf.orgs = [{"id": "meta", "name": ""}]' \
         '.rf.orgs = [{"id": "", "name": "Meta"}]' '.rf.orgs = [{"name": "Meta"}]' '.rf.orgs = null' '.rf.orgs = "meta"'; do
    jq -c "$f" "$RF_ST.keep" > "$RF_ST"; : > "$RF_MARK"
    o=$(: > "$RF_LOG"; KASKAD_CONFIRM_NOT_RF=1 lib _rf_env 1 2>&1 | strip)
    [ -z "$(rf_log)" ] && ! printf '%s' "$o" | grep -q 'экстремистской' && printf '%s' "$o" | grep -q 'RC=1' \
      && printf '%s' "$o" | flat | grep -qF 'не прочитал список организаций ⚠РФ — согласие не записано' && printf '%s' "$o" | grep -q '^\[ПАУЗА\]$' \
      && [ -e "$RF_MARK" ] || { rfbad=1; bad "env: сломанный реестр ($f)" "$(rf_log) | $o"; }
done
[ "$rfbad" = 0 ] && ok "сломанный реестр ⚠РФ (пустой список, .rf.error, коды без имён) — согласие не записано, строка и пауза" || true
mv "$RF_ST.keep" "$RF_ST"; rm -f "$RF_MARK"
for v in yes 0 "" " 1" TRUE; do
    o=$(: > "$RF_LOG"; KASKAD_CONFIRM_NOT_RF="$v" lib _rf_env 1 2>&1 | strip)
    [ -z "$(rf_log)" ] && printf '%s' "$o" | flat | grep -qF "KASKAD_CONFIRM_NOT_RF=\"$v\" — не подтверждение (нужно ровно 1)" \
      || bad "env=«$v» — не согласие" "$(rf_log) | $o"
done
[ -z "$(rf_log)" ] && ok "KASKAD_CONFIRM_NOT_RF=yes, 0, пусто, « 1», TRUE — не согласие: ядро не зовётся, строка в выводе"
o=$(: > "$RF_LOG"; KASKAD_CONFIRM_NOT_RF=yes lib _rf_env 1 2>&1 | strip)
printf '%s' "$o" | grep -q '^\[ПАУЗА\]$' && ok "«не подтверждение» — пауза: строку видно до экрана меню (REV-s1-r67 З-3)" || bad "env: пауза" "$o"
o=$(: > "$RF_LOG"; KASKAD_CONFIRM_NOT_RF='\e[2J\e[H' lib _rf_env 1 2>&1)
! printf '%s' "$o" | grep -q $'\033\\[2J' && printf '%s' "$o" | grep -qF 'KASKAD_CONFIRM_NOT_RF="\e[2J\e[H"' \
  && ok "значение переменной печатается как есть: \\e[2J не становится кодом терминала (SEC-s1-r67 П-3)" || bad "env: echo -e значения" "$(printf '%s' "$o" | od -c | head -5)"
o=$(: > "$RF_LOG"; KASKAD_CONFIRM_NOT_RF=1 lib _rf_env 0 2>&1 | strip)
[ -z "$(rf_log)" ] && printf '%s' "$o" | flat | grep -qF 'учитывается только при первой установке' \
  && ok "KASKAD_CONFIRM_NOT_RF=1 при обновлении — не применяется, строка «только при первой установке»" || bad "env при обновлении" "$(rf_log) | $o"
o=$(: > "$RF_LOG"; unset KASKAD_CONFIRM_NOT_RF; lib _rf_env 1 2>&1 | strip)
[ -z "$(rf_log)" ] && [ "$o" = "RC=0" ] && ok "без переменной — ни вызова, ни строки (свежая установка — «всё через выход»)" || bad "без переменной" "$(rf_log) | $o"
# отметка на диске (REV-s1-r67 З-1): первая установка оборвалась до шага согласия — следующий запуск ещё первый
RF_MARK="$KASKAD_DIR/rf-env.pending"
_rf_fail() { k3ctl() { printf '%s\n' "$*" >> "$RF_LOG"; [ "$1 $2" = "rules status" ] && { cat "$RF_ST"; return 0; }; echo "ядро не ответило" >&2; return 1; }; k3_gw_pause() { echo "[ПАУЗА]"; }; k3_rules_env_consent; }
_rf_env_fail() { k3_lib_ok() { return 0; }; K3_FRESH="$1"; _rf_run _rf_fail; echo "RC=$?"; }
: > "$RF_MARK"
o=$(: > "$RF_LOG"; KASKAD_CONFIRM_NOT_RF=1 lib _rf_env_fail 0 2>&1 | strip)
[ "$(rf_log)" = "rules default ru_direct --confirm-not-rf --via env --orgs meta,fbk" ] && [ -e "$RF_MARK" ] && printf '%s' "$o" | grep -q 'RC=1' \
  && printf '%s' "$o" | grep -q '^\[ПАУЗА\]$' && printf '%s' "$o" | flat | grep -qF 'ядро не ответило' \
  && ok "отметка есть, ядро не ответило: вызов был, отметка осталась, строка и пауза" || bad "env: сбой при отметке" "$(rf_log) | $o"
o=$(: > "$RF_LOG"; KASKAD_CONFIRM_NOT_RF=1 lib _rf_env 0 2>&1 | strip)
[ "$(rf_log)" = "rules default ru_direct --confirm-not-rf --via env --orgs meta,fbk" ] && [ ! -e "$RF_MARK" ] && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "повторный запуск после обрыва (K3_FRESH=0, отметка есть): согласие via=env, отметка снята" || bad "env: повтор после обрыва" "$(rf_log) | $o"
o=$(: > "$RF_LOG"; KASKAD_CONFIRM_NOT_RF=1 lib _rf_env 0 2>&1 | strip)
[ -z "$(rf_log)" ] && printf '%s' "$o" | flat | grep -qF 'учитывается только при первой установке' && ok "отметки нет — снова «только при первой установке»" || bad "env: после снятия" "$(rf_log) | $o"
: > "$RF_MARK"; o=$(: > "$RF_LOG"; KASKAD_CONFIRM_NOT_RF=yes lib _rf_env 0 2>&1 | strip)
[ -z "$(rf_log)" ] && [ ! -e "$RF_MARK" ] || bad "env: «не подтверждение» снимает отметку" "$(rf_log) | $o"
: > "$RF_MARK"; o=$(: > "$RF_LOG"; unset KASKAD_CONFIRM_NOT_RF; lib _rf_env 0 2>&1 | strip)
[ -z "$(rf_log)" ] && [ ! -e "$RF_MARK" ] && [ "$o" = "RC=0" ] && ok "«не подтверждение» и запуск без переменной снимают отметку — дальше только обновления" || bad "env: без переменной снимает отметку" "$(rf_log) | $o"
# REV-s1-rf-2 П-3: ответ ядра и ошибка библиотеки печатаются как есть (\e[2J — текст); ядро не готово — пауза
_rf_esc() { k3ctl() { printf '%s\n' "$*" >> "$RF_LOG"; [ "$1 $2" = "rules status" ] && { cat "$RF_ST"; return 0; }; printf 'x\n\\e[2J\\e[H сбой\n' >&2; return 1; }; k3_gw_pause() { echo "[ПАУЗА]"; }; k3_rules_env_consent; }
_rf_env_esc() { k3_lib_ok() { return 0; }; K3_FRESH=1; _rf_run _rf_esc; echo "RC=$?"; }
o=$(: > "$RF_LOG"; KASKAD_CONFIRM_NOT_RF=1 lib _rf_env_esc 2>&1)
[ "$(rf_log)" = "rules default ru_direct --confirm-not-rf --via env --orgs meta,fbk" ] && ! printf '%s' "$o" | grep -q $'\033\\[2J' \
  && printf '%s' "$o" | strip | flat | grep -qF '\e[2J\e[H сбой' && printf '%s' "$o" | grep -q 'RC=1' \
  && ok "ответ ядра печатается как есть: \\e[2J из k3ctl не становится кодом терминала (REV-s1-rf-2 П-3)" || bad "env: echo -e ответа ядра" "$(printf '%s' "$o" | od -c | head -8)"
_rf_nolib() { k3_lib_ok() { K3_LIB_ERR='нет библиотеки \e[2J'; return 1; }; k3ctl() { printf '%s\n' "$*" >> "$RF_LOG"; }; k3_gw_pause() { echo "[ПАУЗА]"; }; k3_rules_env_consent; }
_rf_env_nolib() { K3_FRESH=1; _rf_run _rf_nolib; echo "RC=$?"; }
: > "$RF_MARK"; o=$(: > "$RF_LOG"; KASKAD_CONFIRM_NOT_RF=1 lib _rf_env_nolib 2>&1)
[ -z "$(rf_log)" ] && [ -e "$RF_MARK" ] && printf '%s' "$o" | grep -q 'RC=1' && printf '%s' "$o" | strip | grep -q '^\[ПАУЗА\]$' \
  && ! printf '%s' "$o" | grep -q $'\033\\[2J' && printf '%s' "$o" | strip | flat | grep -qF 'нет библиотеки \e[2J' \
  && ok "программа не готова (k3_lib_ok ложно): ядро не зовётся, строка с ошибкой как есть, пауза, отметка остаётся" || bad "env: k3_lib_ok ложно" "$(rf_log) | $(printf '%s' "$o" | od -c | head -8)"
rm -f "$RF_MARK"
o=$(awk '/^run_startup\(\)/,/^}/' "$S" | grep -nE '^ *k3_fresh_check|&& k3_rules_env_mark|^ *k3_rules_env_consent' | cut -d: -f1 | tr '\n' ' ')
set -- $o; [ "$#" = 3 ] && [ "$2" -gt "$1" ] && [ "$3" -gt "$2" ] \
  && ok "run_startup: отметка ставится на шаге 2 (после k3_fresh_check, как отметка мастера шлюза), до шага согласия" || bad "run_startup: отметка" "$o"
cp "$RF_ST" "$RF_ST.keep"; jq -c '.rf.orgs[1].name = "FBK"' "$RF_ST.keep" > "$RF_ST"      # ядро отдаёт имена на языке узла
o=$(: > "$RF_LOG"; KASKAD_CONFIRM_NOT_RF=1 lib _en_run _rf_env 1 2>&1 | strip); mv "$RF_ST.keep" "$RF_ST"
printf '%s' "$o" | flat | grep -qF 'Russian sites direct is on (KASKAD_CONFIRM_NOT_RF=1' && ! printf '%s' "$o" | grep -q '[А-Яа-яЁё]' \
  && ok "KASKAD_CONFIRM_NOT_RF по-английски — без кириллицы" || bad "env en" "$o"
o=$(awk '/^run_startup\(\)/,/^}/' "$S" | grep -nE 'k3_startup_engine$|k3_rules_env_consent' | cut -d: -f1 | tr '\n' ' ')
set -- $o; [ "$#" = 2 ] && [ "$2" -gt "$1" ] && ok "run_startup: k3_rules_env_consent — после движка (migrate apply) и до меню" || bad "run_startup: место вызова" "$o"
echo "── 55. пункт 22 «Нагрузка и сеть сервера» (A8): главное меню, экран цифрами, пробы, «Не напоминать», 21 → 22, установка, обновление ──"
# главное меню 80×24: 22 — в правом столбце после 21 (на строке с 11), «0» — последней строкой правого столбца; номера
# правого столбца — с одного места экрана (без съезда), левого — тоже
head_root hm1; head_js
o=$(K3_TERM_COLS=80 lib t_head <<< "0" 2>&1 | strip)
r=$(printf '%s\n' "$o" | sed -n '/^─/,$p' | python3 -c '
import re, sys
cols = {}
for l in sys.stdin.read().split("\n"):
    for m in re.finditer(r"(?:^| )(\d{1,2})\) ", l):
        cols[m.group(1)] = l.index(")", m.start(1))
left = sorted(set(cols.get(str(i)) for i in range(1, 12)))
right = sorted(set([cols.get(str(i)) for i in range(12, 23)] + [cols.get("0")]), key=str)
print("ok" if left == [2] and right == [40] else "left=%s right=%s" % (left, right))')
[ "$r" = ok ] && printf '%s' "$o" | grep -qE '^11\) .* 22\) Нагрузка и сеть сервера$' && printf '%s' "$o" | grep -qE '^ {38} 0\) Выход$' \
  && [ "$(printf '%s' "$o" | FIT_W=80 fit)" = "1 ok" ] \
  && ok "главное меню 80×24: «22) Нагрузка и сеть сервера» справа после 21 (строка с 11), «0) Выход» под ним; столбцы не съезжают" \
  || bad "пункт 22 в главном меню" "$r | $o"
o=$(K3_TERM_COLS=76 lib t_head <<< "0" 2>&1 | strip)
[ "$(printf '%s' "$o" | FIT_W=76 fit)" = "1 ok" ] && printf '%s' "$o" | grep -qE ' 22\) Нагрузка и сеть сервера$' \
  && ok "главное меню 76×24: один экран, пункт 22 на месте" || bad "главное меню 76 столбцов" "$o"
o=$(K3_TERM_COLS=60 lib t_head <<< "0" 2>&1 | strip)
nums=$(printf '%s\n' "$o" | sed -n '/^─/,$p' | grep -oE '^ ?[0-9]{1,2}\) ' | tr -d ' )' | tr '\n' ' ')
wide=$(printf '%s\n' "$o" | sed -n '/^─/,$p' | python3 -c 'import sys; print("\n".join(l for l in sys.stdin.read().split("\n") if len(l) > 60))')
[ "$nums" = "1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 0 " ] && [ -z "$wide" ] \
  && ok "узкое окно (60): пункты одним столбцом 1–22 и 0, каждый — в окно" || bad "узкое окно" "$nums | $wide"
o=$(K3_TERM_COLS=80 en t_head <<< "0" 2>&1 | strip)
printf '%s' "$o" | grep -qE ' 22\) Server load & network$' && [ "$(printf '%s' "$o" | FIT_W=80 fit)" = "1 ok" ] \
  && ok "главное меню по-английски: «22) Server load & network», 80×24" || bad "пункт 22 по-английски" "$o"
# экран пункта 22: вывод kaskadctl host health как есть, ниже — цифрами; ключа probes нет — ещё «6) Не напоминать»
# (B14: пункты 4 и 5 добавили две строки — без предела окна экран в 26 строк; в окне 24 строки он идёт постранично,
# пункты внизу — проверка «на узле с тревогами в 80×24» ниже)
t_hm() { scr k3_host_menu; }
use_root hm2; fake_ctl; engine_nft
o=$(printf '0\n' | K3_TERM_COLS=80 lib t_hm 2>&1 | strip)
printf '%s' "$o" | grep -q '━━━ Нагрузка и сеть сервера ━━━$' && printf '%s' "$o" | grep -q '^Состояние: ' \
  && printf '%s' "$o" | grep -qx ' 1) Обновить' && printf '%s' "$o" | grep -qx ' 2) Лёгкие пробы: включить' \
  && printf '%s' "$o" | grep -qx ' 6) Не напоминать о пробах' && printf '%s' "$o" | grep -qx ' 0) Назад' \
  && printf '%s' "$o" | grep -qx ' 3) Проверить сервер' && printf '%s\n' "$o" | grep -qx 'Сравнивать пока не с чем: сервер ни разу не проверялся' \
  && printf '%s\n' "$o" | grep -qx '  Проверить — пункт 3 на этом экране' && ctl_log | grep -qx 'host score --screen' \
  && printf '%s' "$o" | grep -q '^Выбор \[1\]: ' && ! printf '%s' "$o" | grep -qiE '\[[yд]/[nн]\]|\by/n\b' \
  && ! printf '%s' "$o" | grep -qE 'Оценк|балл' && ctl_log | grep -qx 'host health --screen' && [ "$(printf '%s' "$o" | FIT_W=80 FIT_H=26 fit)" = "1 ok" ] \
  && printf '%s\n' "$o" | grep -qx 'Лёгкие пробы сети: выключены. Включить — 2 на этом экране' && ! printf '%s' "$o" | grep -q 'kaskadctl' \
  && ok "22: заголовок, вывод host health --screen («Включить — 2 на этом экране», без команды kaskadctl), сравнение со своей историей без балла (host score --screen: «пункт 3 на этом экране»), «1) Обновить 2) Лёгкие пробы: включить 3) Проверить сервер 6) Не напоминать 0) Назад», «Выбор [1]:», без y/n, 80×24" \
  || bad "экран пункта 22" "$(ctl_log | tr '\n' ' ') | $o"
o=$(printf '6\n0\n' | K3_TERM_COLS=80 lib t_hm 2>&1 | strip); s2=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==3')
ctl_log | grep -qx 'host set --probes off' && [ "$(jq -r '.gateway.hostmon.probes' "$KASKAD_DIR/state.json")" = false ] \
  && printf '%s' "$s2" | grep -q 'Больше не напомню: пробы выключены' && ! printf '%s' "$s2" | grep -q ' 6) ' \
  && [ -z "$(PYTHONPATH="$K3_TEST_LIB" python3 -m kaskad host probes-hint)" ] \
  && ok "22 → 6 «Не напоминать»: probes false явно, подсказки больше нет, пункта 6 на экране больше нет" \
  || bad "22 → 6" "$(ctl_log | tr '\n' ' ') | $s2"
o=$(printf '2\n0\n' | K3_TERM_COLS=80 lib t_hm 2>&1 | strip); s2=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==3')
ctl_log | grep -qx 'host set --probes on' && [ "$(jq -r '.gateway.hostmon.probes' "$KASKAD_DIR/state.json")" = true ] \
  && printf '%s' "$s2" | grep -q 'Лёгкие пробы включены' && printf '%s' "$s2" | grep -qx ' 2) Лёгкие пробы: выключить' \
  && ok "22 → 2: пробы включены (ответ ядра над приглашением), пункт 2 — «выключить»" || bad "22 → 2 включить" "$s2"
o=$(printf '2\n0\n' | K3_TERM_COLS=80 lib t_hm 2>&1 | strip); s2=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==3')
[ "$(jq -r '.gateway.hostmon.probes' "$KASKAD_DIR/state.json")" = false ] && printf '%s' "$s2" | flat | grep -q 'остановятся в течение 10 с' \
  && ok "22 → 2: пробы выключены — «остановятся в течение 10 с»" || bad "22 → 2 выключить" "$s2"
: > "$CALLS/kaskadctl.log"
o=$(printf '6\n7\n\n0\n' | K3_TERM_COLS=80 K3_PAUSE_SEC=0 lib t_hm 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта: введите номер из списка, 0 — назад.')" -ge 2 ] && ! ctl_log | grep -q '^host set' \
  && [ "$(ctl_log | grep -cx 'host health --screen')" = 4 ] \
  && ok "22: ключ есть — 6 и 7 «Нет такого пункта», ничего не пишется; Enter — обновить (host health заново)" \
  || bad "22: неверный выбор" "$(ctl_log | tr '\n' ' ') | $o"
o=$(printf '0\n' | K3_TERM_COLS=80 en t_hm 2>&1 | strip)
printf '%s' "$o" | grep -q '━━━ Server load & network ━━━$' && printf '%s' "$o" | grep -q '^Status: ' && printf '%s' "$o" | grep -qx ' 1) Refresh' \
  && printf '%s' "$o" | grep -qx ' 2) Light probes: turn on' && printf '%s' "$o" | grep -q '^Choice \[1\]: ' \
  && ok "22 по-английски: заголовок, вывод ядра на английском, пункты цифрами" || bad "22 по-английски" "$o"
# B7: 22 → 3 «Проверить сервер» — цена словами ядра (host bench --dry), выбор цифрами «1) Быстрая 2) Полная 0) Назад»,
# вопрос ядра о sysbench и fio (если их нет), запуск host bench --yes --by-ssh; всё от ядра — через k3_term_esc (ESC
# из ответа — текстом, не кодом терминала)
HB_JS="$W/hb.json"
_hb() { k3ctl() { printf '%s\n' "$*" >> "$CALLS/kaskadctl.log"
          case "$*" in
            "host bench --dry") printf 'Цена одной проверки сервера:\n  Быстрая: ≈ 250 МБ\n  \033[2J сбой\n' ;;
            "host bench --dry --"*" --json") cat "$HB_JS" ;;
            "host bench --"*" --yes --by-ssh"*) printf '  Идёт проверка: шаг 1 из 9\nПроверка закончена\n\033]52;c;eA==\a\n' ;;
            *) return 2 ;;
          esac; }
        k3_gw_pause() { echo "[ПАУЗА]"; }; k3_host_bench; echo "RC=$?"; }
use_root hb; engine_nft; : > "$CALLS/kaskadctl.log"
printf '{"ok": true, "pkgs": {"missing": [], "question": null}}\n' > "$HB_JS"
o=$(printf '\n' | K3_TERM_COLS=80 lib _hb 2>&1)
[ "$(ctl_log | tr '\n' '|')" = "host bench --dry|host bench --dry --quick --json|host bench --quick --yes --by-ssh|" ] \
  && printf '%s' "$o" | strip | grep -q '━━━ Проверка сервера ━━━' && printf '%s' "$o" | strip | grep -qx ' 1) Быстрая проверка' \
  && printf '%s' "$o" | strip | grep -qx ' 2) Полная проверка' && printf '%s' "$o" | strip | grep -qx ' 0) Назад' \
  && printf '%s' "$o" | strip | grep -q '^Выбор \[1\]: ' && printf '%s' "$o" | strip | grep -qx 'Проверка закончена' \
  && ! printf '%s' "$o" | grep -q $'\033\\[2J' && ! printf '%s' "$o" | grep -q $'\033\\]52' && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "22 → 3: цена ядра, «1) Быстрая 2) Полная 0) Назад», Enter — быстрая: host bench --quick --yes --by-ssh; ESC из ответа ядра — текстом (k3_term_esc)" \
  || bad "22 → 3 быстрая" "$(ctl_log | tr '\n' '|') | $(printf '%s' "$o" | od -c | head -20)"
: > "$CALLS/kaskadctl.log"
printf '{"ok": true, "pkgs": {"missing": ["sysbench", "fio"], "question": {"text": "Для тестов нужны программы sysbench fio. Установить их?", "choices": [[1, "Установить sysbench fio и проверить"], [2, "Отмена"]], "default": 1}}}\n' > "$HB_JS"
o=$(printf '2\n2\n' | K3_TERM_COLS=80 lib _hb 2>&1 | strip)
[ "$(ctl_log | tr '\n' '|')" = "host bench --dry|host bench --dry --full --json|" ] && printf '%s\n' "$o" | grep -qx ' 1) Установить sysbench fio и проверить' \
  && printf '%s\n' "$o" | grep -qx ' 2) Отмена' && printf '%s' "$o" | grep -q 'Установить их?' && ! printf '%s' "$o" | grep -q 'Проверка закончена' \
  && ok "22 → 3 → 2: вопрос ядра о пакетах цифрами; «2) Отмена» — проверка не запускается" || bad "22 → 3 пакеты, отмена" "$(ctl_log | tr '\n' '|') | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '7\n2\n\n' | K3_TERM_COLS=80 K3_PAUSE_SEC=0 lib _hb 2>&1 | strip)
[ "$(ctl_log | tr '\n' '|')" = "host bench --dry|host bench --dry --full --json|host bench --full --yes --by-ssh|" ] \
  && printf '%s\n' "$o" | grep -q 'Нет такого пункта' && printf '%s\n' "$o" | grep -qx 'Проверка закончена' \
  && ok "22 → 3: неверный номер — «Нет такого пункта» и вопрос снова; 2 и Enter (поставить пакеты) — полная, host bench --full --yes" \
  || bad "22 → 3 полная с пакетами" "$(ctl_log | tr '\n' '|') | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '0\n' | K3_TERM_COLS=80 lib _hb 2>&1 | strip)
[ "$(ctl_log | tr '\n' '|')" = "host bench --dry|" ] && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "22 → 3 → 0: назад, ничего не запускается" || bad "22 → 3 → 0" "$(ctl_log | tr '\n' '|') | $o"
o=$(printf '0\n' | K3_TERM_COLS=80 en _hb 2>&1 | strip)
printf '%s' "$o" | grep -q '━━━ Server check ━━━' && printf '%s\n' "$o" | grep -qx ' 1) Quick check' && printf '%s\n' "$o" | grep -qx ' 2) Full check' \
  && printf '%s\n' "$o" | grep -qx ' 0) Back' && ok "22 → 3 по-английски: «Server check», «1) Quick check 2) Full check 0) Back»" || bad "22 → 3 en" "$o"
# D-144, С2 REV-score-rename: у полной проверки сервера, который может быть в России, — сначала вопрос ядра об отдаче,
# потом вопрос о программах по ответу: iperf3 — только при замере отдачи (.upload_ru.pkgs), «2» — без него
printf '%s\n' '{"ok": true, "pkgs": {"missing": [], "question": null}, "upload_ru": {"text": "Точка увидит адрес сервера.", "choices": [[1, "Замерить отдачу"], [2, "Не замерять отдачу"]], "default": 1, "pkgs": {"missing": ["iperf3"], "question": {"text": "Для замера отдачи нужна программа iperf3. Установить её?", "choices": [[1, "Установить iperf3 и проверить"], [2, "Отмена"]], "default": 1}}}}' > "$HB_JS"
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n2\n' | K3_TERM_COLS=80 K3_PAUSE_SEC=0 lib _hb 2>&1 | strip)
[ "$(ctl_log | tr '\n' '|')" = "host bench --dry|host bench --dry --full --json|host bench --full --yes --by-ssh --no-upload-ru|" ] \
  && printf '%s\n' "$o" | grep -qx ' 2) Не замерять отдачу' && ! printf '%s' "$o" | grep -q 'iperf3' \
  && ok "22 → 3 → 2: вопрос об отдаче; «2» — --no-upload-ru и без вопроса об iperf3" || bad "22 → 3 отдача выключена" "$(ctl_log | tr '\n' '|') | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n\n2\n' | K3_TERM_COLS=80 lib _hb 2>&1 | strip)
q1=$(printf '%s\n' "$o" | grep -n 'Точка увидит адрес' | head -1 | cut -d: -f1)
q2=$(printf '%s\n' "$o" | grep -n 'нужна программа iperf3' | head -1 | cut -d: -f1)
[ "$(ctl_log | tr '\n' '|')" = "host bench --dry|host bench --dry --full --json|" ] && [ -n "$q1" ] && [ -n "$q2" ] && [ "$q1" -lt "$q2" ] \
  && printf '%s\n' "$o" | grep -qx ' 1) Установить iperf3 и проверить' \
  && ok "22 → 3 → 2: сначала отдача, потом iperf3 (Enter — замерять); «2) Отмена» — не запускается" || bad "22 → 3 отдача и iperf3" "$(ctl_log | tr '\n' '|') | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n' | K3_TERM_COLS=80 K3_PAUSE_SEC=0 lib _hb 2>&1 | strip)
[ "$(ctl_log | tr '\n' '|')" = "host bench --dry|host bench --dry --quick --json|host bench --quick --yes --by-ssh|" ] && ! printf '%s' "$o" | grep -q 'iperf3' \
  && ok "22 → 3 → 1: быстрая — без вопроса об отдаче и iperf3" || bad "22 → 3 быстрая без iperf3" "$(ctl_log | tr '\n' '|') | $o"
_hb_fail() { k3ctl() { printf 'Ошибка: \033[2J нет ядра\n' >&2; return 1; }; k3_gw_pause() { echo "[ПАУЗА]"; }; k3_host_bench; echo "RC=$?"; }
o=$(printf '\n' | K3_TERM_COLS=80 lib _hb_fail 2>&1)
printf '%s' "$o" | strip | grep -q '^\[ПАУЗА\]$' && ! printf '%s' "$o" | grep -q $'\033\\[2J' && printf '%s' "$o" | strip | grep -q 'нет ядра' \
  && ! printf '%s' "$o" | strip | grep -q ' 1) Быстрая' && ok "22 → 3: ядро не ответило — его слова (через k3_term_esc) и пауза, без выбора" || bad "22 → 3 сбой ядра" "$o"
# круг 2 B7, З-3: ESC и OSC 52 из ответа ядра — текстом и в строках сравнения на экране 22 (host score --screen), и в
# вопросе ядра о пакетах (question.text и choices из --dry --json)
use_root hmesc; fake_ctl; engine_nft; mkdir -p "$FAKE_DIR/host"
printf 'В целом: 96 от обычного (100 — как обычно у этого сервера)\n  \033[2J чисто \033]52;c;eA==\a\n' > "$FAKE_DIR/host/score.txt"
o=$(printf '0\n' | K3_TERM_COLS=80 lib t_hm 2>&1)
rm -f "$FAKE_DIR/host/score.txt"
printf '%s' "$o" | strip | grep -qx 'В целом: 96 от обычного (100 — как обычно у этого сервера)' && printf '%s' "$o" | grep -q 'чисто' \
  && ! printf '%s' "$o" | grep -q $'\033\\[2J' && ! printf '%s' "$o" | grep -q $'\033\\]52' \
  && ok "22: строки сравнения (host score --screen) — через k3_term_esc: ESC и OSC 52 из ответа ядра текстом" \
  || bad "22: ESC в строках сравнения" "$(printf '%s' "$o" | od -c | grep -m3 '033')"
: > "$CALLS/kaskadctl.log"
printf '{"ok": true, "pkgs": {"missing": ["sysbench"], "question": {"text": "Нужен sysbench \\u001b[2J?", "choices": [[1, "Установить \\u001b]52;c;eA==\\u0007"], [2, "Отмена"]], "default": 1}}}\n' > "$HB_JS"
o=$(printf '\n2\n' | K3_TERM_COLS=80 lib _hb 2>&1)
printf '%s' "$o" | strip | grep -q 'Нужен sysbench' && printf '%s' "$o" | strip | grep -q '^ 1) Установить' \
  && ! printf '%s' "$o" | grep -q $'\033\\[2J' && ! printf '%s' "$o" | grep -q $'\033\\]52' && ! ctl_log | grep -q -- '--yes' \
  && ok "22 → 3: вопрос ядра о пакетах (question.text, choices) — через k3_term_esc: ESC и OSC 52 текстом" \
  || bad "22 → 3: ESC в вопросе о пакетах" "$(ctl_log | tr '\n' '|') | $(printf '%s' "$o" | od -c | grep -m3 '033')"
# B14: 22 → 4 «Расписание проверок» и 22 → 5 «История проверок» — экраны печатает ядро (kaskadctl host bench schedule
# list, history --days 14) через k3_term_esc, выбор цифрами «… 0) Назад», «Выбор [1]:»; добавление по шагам (время,
# вид, дни, «только когда свободен»), итог с ценой ядра (--dry) и «1) Сохранить 2) Отмена»; сохраняется ровно
# показанное (--code из --dry --json) с --yes; номер проверки истории -> id через --json. Круг 2 (REV-ns-b14): экраны
# ядра — с --screen (без флагов и кода, З-5), постранично в окне 80×24 (З-4), «5) Обновить таймер» — когда таймер не
# совпадает с расписанием (П-3), выключить — без --yes (С-1).
_hs() { k3ctl() { printf '%s\n' "$*" >> "$CALLS/kaskadctl.log"
          case "$*" in
            "host bench schedule list --screen")
                if [ -n "${HS_LONG:-}" ]; then
                    printf 'Расписание проверок (часовой пояс Europe/Moscow)\n'
                    for i in $(seq 1 12); do printf '%2d) %02d:00 · быстрая · каждый день · включена\n' "$i" "$i"; done
                    printf '\nСледующая: 20.10 01:00 (быстрая)\nЦена: ≈ 3 ГБ в сутки\n  sysbench и fio не установлены\n  Если звено резко упало — до 2 повторов в сутки\nЕсли звено резко упало — проверить ещё раз через 1–2 ч: включено.\nПОСЛЕДНЯЯ СТРОКА\n'
                else printf 'Расписание проверок (часовой пояс Europe/Moscow)\n 1) 03:45 · быстрая · каждый день · включена \033[2J\nЦена: ≈ 250 МБ в сутки\n'; fi ;;
            "host bench schedule list --json") printf '{"schedule": [{"id": "abcdef", "on": %s}], "armed": true, "timer": {"applied": %s}}\n' "${HS_ON:-true}" "${HS_TM:-true}" ;;
            "host bench schedule add "*" --dry --screen"|"host bench schedule rm 1 --dry --screen"|"host bench schedule on 1 --dry --screen")
                printf 'Так будет (ничего не сохранено):\n 1) 12:00 \033]52;c;eA==\a\nЦена: ≈ 500 МБ в сутки\n' ;;
            *" --dry --json") printf '{"ok": true, "dry": true, "code": "0123456789"}\n' ;;
            "host bench schedule add "*" --code=0123456789 --yes --screen") printf 'Проверка добавлена.\n' ;;
            "host bench schedule rm 1 --code=0123456789 --yes --screen") printf 'Проверка удалена.\n' ;;
            "host bench schedule on 1 --code=0123456789 --yes --screen") printf 'Проверка включена.\n' ;;
            "host bench schedule off 1 --screen") printf 'Проверка выключена.\n' ;;
            "host bench schedule cost") printf 'Цена: ≈ 250 МБ в сутки, ≈ 7,5 ГБ в месяц\n' ;;
            "host bench schedule apply") ;;
            *) return 2 ;;
          esac; }
        clear() { printf '\f'; }; k3_gw_pause() { echo "[ПАУЗА]"; }; k3_host_sched; echo "RC=$?"; }
use_root hs; engine_nft; : > "$CALLS/kaskadctl.log"
o=$(printf '1\n25:00\n3:45\n2\n4\n1,3\n2\n1\n0\n' | K3_TERM_COLS=80 lib _hs 2>&1)
s=$(printf '%s' "$o" | strip)
[ "$(ctl_log | tr '\n' '|')" = "host bench schedule list --screen|host bench schedule list --json|host bench schedule add --time=03:45 --mode=full --days=1,3 --no-quiet --dry --screen|host bench schedule add --time=03:45 --mode=full --days=1,3 --no-quiet --dry --json|host bench schedule add --time=03:45 --mode=full --days=1,3 --no-quiet --code=0123456789 --yes --screen|host bench schedule list --screen|host bench schedule list --json|" ] \
  && printf '%s' "$s" | grep -q '━━━ Расписание проверок ━━━' && printf '%s\n' "$s" | grep -qx ' 1) Добавить' \
  && printf '%s\n' "$s" | grep -qx ' 2) Включить или выключить' && printf '%s\n' "$s" | grep -qx ' 3) Удалить' \
  && printf '%s\n' "$s" | grep -qx ' 4) Подробно о цене' && printf '%s\n' "$s" | grep -qx ' 0) Назад' \
  && printf '%s' "$s" | grep -q '^Выбор \[1\]: ' && printf '%s' "$s" | grep -q 'Время — ЧЧ:ММ от 00:00 до 23:59' \
  && printf '%s\n' "$s" | grep -qx ' 4) Выбрать дни' && printf '%s' "$s" | grep -q 'Проверять только когда сервер свободен?' \
  && printf '%s\n' "$s" | grep -qx ' 1) Сохранить' && printf '%s\n' "$s" | grep -qx ' 2) Отмена' \
  && printf '%s' "$s" | grep -q 'Проверка добавлена.' && printf '%s' "$s" | grep -q 'Цена: ≈ 500 МБ в сутки' \
  && ! printf '%s' "$o" | grep -q $'\033\\[2J' && ! printf '%s' "$o" | grep -q $'\033\\]52' \
  && ! printf '%s' "$s" | grep -qiE '\[[yд]/[nн]\]|\by/n\b' && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "22 → 4 → 1: время (неверное — подсказка, 3:45 → 03:45), вид, дни (4 — свои), «только когда свободен» цифрами; цена ядра (--dry) и «1) Сохранить 2) Отмена»; сохраняется с --code из --dry --json и --yes; ESC и OSC 52 ядра — текстом" \
  || bad "22 → 4 добавить" "$(ctl_log | tr '\n' '|') | $s"
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n1\n2\n1\n0\n' | K3_TERM_COLS=80 HS_ON=true lib _hs 2>&1 | strip)
[ "$(ctl_log | tr '\n' '|')" = "host bench schedule list --screen|host bench schedule list --json|host bench schedule list --json|host bench schedule off 1 --screen|host bench schedule list --screen|host bench schedule list --json|host bench schedule list --json|host bench schedule off 1 --screen|host bench schedule list --screen|host bench schedule list --json|" ] \
  && printf '%s' "$o" | grep -q 'Проверка выключена.' \
  && ok "22 → 4 → 2: включённая запись выключается сразу (трафик не растёт), ответ ядра над приглашением" \
  || bad "22 → 4 → 2 выключить" "$(ctl_log | tr '\n' '|') | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '2\n1\n2\n0\n' | K3_TERM_COLS=80 HS_ON=false lib _hs 2>&1 | strip)
[ "$(ctl_log | tr '\n' '|')" = "host bench schedule list --screen|host bench schedule list --json|host bench schedule list --json|host bench schedule on 1 --dry --screen|host bench schedule on 1 --dry --json|host bench schedule list --screen|host bench schedule list --json|" ] \
  && printf '%s\n' "$o" | grep -qx ' 1) Включить' \
  && ok "22 → 4 → 2: выключенная — включение с ценой ядра и согласием; «2) Отмена» — ничего не меняется" \
  || bad "22 → 4 → 2 включить, отмена" "$(ctl_log | tr '\n' '|') | $o"
: > "$CALLS/kaskadctl.log"
o=$(printf '3\nx\n3\n1\n1\n4\n9\n0\n' | K3_TERM_COLS=80 K3_PAUSE_SEC=0 lib _hs 2>&1 | strip)
[ "$(ctl_log | tr '\n' '|')" = "host bench schedule list --screen|host bench schedule list --json|host bench schedule list --screen|host bench schedule list --json|host bench schedule rm 1 --dry --screen|host bench schedule rm 1 --dry --json|host bench schedule rm 1 --code=0123456789 --yes --screen|host bench schedule list --screen|host bench schedule list --json|host bench schedule cost|host bench schedule list --screen|host bench schedule list --json|host bench schedule list --screen|host bench schedule list --json|" ] \
  && printf '%s' "$o" | grep -q 'Проверка удалена.' && [ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта')" -ge 2 ] \
  && printf '%s' "$o" | grep -q '7,5 ГБ в месяц' \
  && ok "22 → 4 → 3: удалить с показом и согласием (--code, --yes); 4 — цена ядра; не номер и 9 — «Нет такого пункта»" \
  || bad "22 → 4 → 3 удалить" "$(ctl_log | tr '\n' '|') | $o"
o=$(printf '0\n' | K3_TERM_COLS=80 en _hs 2>&1 | strip)
printf '%s' "$o" | grep -q '━━━ Check schedule ━━━' && printf '%s\n' "$o" | grep -qx ' 1) Add' && printf '%s\n' "$o" | grep -qx ' 4) The price in detail' \
  && printf '%s\n' "$o" | grep -qx ' 0) Back' && printf '%s' "$o" | grep -q '^Choice \[1\]: ' \
  && ok "22 → 4 по-английски: «Check schedule», «1) Add … 0) Back», «Choice [1]:»" || bad "22 → 4 en" "$o"
nlines() { printf '%s' "$1" | awk 'END { print NR }'; }      # строк экрана у куска (без последнего перевода строки)
# REV-ns-b14 З-4: 12 записей и цена — длиннее окна 80×24: экран листается («Enter — дальше»), ни один кусок не выше 24
# строк, пункты и «Выбор» — сразу под концом списка
o=$(printf '\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 HS_LONG=1 lib _hs 2>&1 | strip)
s1=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==2'); p1="${s1%%Enter — дальше*}"; p2="${s1#*к выбору действий: }"; p2="${p2%%Выбор*}"
[ "$s1" != "$p1" ] && [ "$(nlines "$p1")" -le 23 ] && [ "$(nlines "$p2")" -le 23 ] \
  && printf '%s\n' "$p2" | grep -qx ' 1) Добавить' \
  && [ "$(printf '%s\n' "$s1" | grep -n 'ПОСЛЕДНЯЯ СТРОКА' | cut -d: -f1)" -lt "$(printf '%s\n' "$s1" | grep -nx ' 1) Добавить' | cut -d: -f1)" ] \
  && ok "22 → 4 в окне 80×24: длинный список листается, куски ≤ 24 строк, пункты сразу под концом списка" \
  || bad "22 → 4: 80×24" "$(nlines "$p1") / $(nlines "$p2") | $s1"
# REV-ns-b14 П-3: таймер не совпадает с расписанием — пункт «5) Обновить таймер» (а не команда kaskadctl); 5 — apply
: > "$CALLS/kaskadctl.log"
o=$(printf '5\n0\n' | K3_TERM_COLS=80 HS_TM=false lib _hs 2>&1 | strip)
s1=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==2'); s2=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==3')
printf '%s\n' "$s1" | grep -qx ' 5) Обновить таймер' && ctl_log | grep -qx 'host bench schedule apply' \
  && printf '%s' "$s2" | grep -q 'Таймер расписания обновлён.' \
  && ok "22 → 4: «5) Обновить таймер», когда таймер не совпадает с расписанием; 5 — schedule apply" \
  || bad "22 → 4 → 5 таймер" "$(ctl_log | tr '\n' '|') | $s1 | $s2"
o=$(printf '5\n0\n' | K3_TERM_COLS=80 K3_PAUSE_SEC=0 lib _hs 2>&1 | strip)
! printf '%s' "$o" | grep -q ' 5) Обновить таймер' && printf '%s' "$o" | grep -q 'Нет такого пункта' \
  && ok "22 → 4: таймер совпадает — пункта 5 нет, ввод 5 — «Нет такого пункта»" || bad "22 → 4: лишний пункт 5" "$o"
_hh() { k3ctl() { printf '%s\n' "$*" >> "$CALLS/kaskadctl.log"
          case "$*" in
            "host bench history --days 14 --screen")
                if [ -n "${HH_LONG:-}" ]; then
                    for i in $(seq 1 "${HH_N:-31}"); do printf '%2d) 19.10 03:47 · быстрая · по расписанию · готово\n' "$i"; done
                else printf ' 1) 19.10 03:47 · быстрая · повтор после резкого падения · готово \033[2J\n 2) 18.10 03:45 · полная · по расписанию · готово\n'; fi ;;
            "host bench history --days 14 --json") printf '{"runs": [{"id": "00000000aa"}, {"id": "00000000bb"}]}\n' ;;
            "host bench show 00000000aa")
                if [ -n "${HH_TALL:-}" ]; then for i in $(seq -w 1 40); do printf '  подробно %s\n' "$i"; done
                else printf '19.10 03:47 · быстрая\n  Скачивание: 900 Мбит/с \033]52;c;eA==\a\n'; fi ;;
            "host bench compare 00000000aa 00000000bb") printf 'A: 19.10\nB: 18.10\n' ;;
            "host bench history --days 14 --skipped --screen")
                if [ -n "${HH_TALL:-}" ]; then for i in $(seq -w 1 40); do printf '  пропуск %s\n' "$i"; done
                else printf 'Пропусков за это время нет.\n'; fi ;;
            *) return 2 ;;
          esac; }
        clear() { printf '\f'; }; k3_gw_pause() { echo "[ПАУЗА]"; }; k3_host_hist; echo "RC=$?"; }
: > "$CALLS/kaskadctl.log"
o=$(printf '1\n1\n2\n1\n2\n3\n1\n5\n9\n0\n' | K3_TERM_COLS=80 K3_PAUSE_SEC=0 lib _hh 2>&1)
s=$(printf '%s' "$o" | strip)
[ "$(ctl_log | tr '\n' '|')" = "host bench history --days 14 --screen|host bench history --days 14 --json|host bench show 00000000aa|host bench history --days 14 --screen|host bench history --days 14 --json|host bench history --days 14 --json|host bench compare 00000000aa 00000000bb|host bench history --days 14 --screen|host bench history --days 14 --skipped --screen|host bench history --days 14 --screen|host bench history --days 14 --json|host bench history --days 14 --screen|host bench history --days 14 --screen|" ] \
  && printf '%s' "$s" | grep -q '━━━ История проверок ━━━' && printf '%s\n' "$s" | grep -qx ' 1) Подробно о проверке' \
  && printf '%s\n' "$s" | grep -qx ' 2) Сравнить две' && printf '%s\n' "$s" | grep -qx ' 3) Показать пропуски' \
  && printf '%s\n' "$s" | grep -qx ' 0) Назад' && printf '%s' "$s" | grep -q '^Выбор \[1\]: ' \
  && printf '%s' "$s" | grep -q 'повтор после резкого падения' && printf '%s' "$s" | grep -q 'Пропусков за это время нет' \
  && ! printf '%s' "$o" | grep -q $'\033\\[2J' && ! printf '%s' "$o" | grep -q $'\033\\]52' && printf '%s' "$o" | grep -q 'RC=0' \
  && ok "22 → 5: история за 14 дней от ядра (подпись auto), 1 — подробно (номер → id через --json), 2 — сравнить две, 3 — пропуски; номер не из списка — ядро не зовётся; ESC и OSC 52 — текстом" \
  || bad "22 → 5 история" "$(ctl_log | tr '\n' '|') | $s"
o=$(printf '0\n' | K3_TERM_COLS=80 en _hh 2>&1 | strip)
printf '%s' "$o" | grep -q '━━━ Check history ━━━' && printf '%s\n' "$o" | grep -qx ' 3) Show the skipped ones' \
  && ok "22 → 5 по-английски: «Check history», «3) Show the skipped ones»" || bad "22 → 5 en" "$o"
# REV-ns-b14 З-4: 31 проверка за 14 дней — листается в окне 80×24, пункты сразу под концом списка
o=$(printf '\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 HH_LONG=1 lib _hh 2>&1 | strip)
s1=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==2'); p1="${s1%%Enter — дальше*}"; p2="${s1#*к выбору действий: }"; p2="${p2%%Выбор*}"
[ "$s1" != "$p1" ] && [ "$(nlines "$p1")" -le 23 ] && [ "$(nlines "$p2")" -le 23 ] \
  && printf '%s\n' "$p2" | grep -q '^31) ' && printf '%s\n' "$p2" | grep -qx ' 1) Подробно о проверке' \
  && ok "22 → 5 в окне 80×24: 31 проверка листается, куски ≤ 24 строк, пункты под концом списка" \
  || bad "22 → 5: 80×24" "$s1"
# REV-ns-b14-2 П-2: 18 проверок — граница у самих пунктов: заголовок и список влезают, а с пунктами (6 строк) — уже нет;
# пункты уходят на следующий кусок, весь экран не выше окна (пункты «короче», чем есть, — экран в 25 строк)
o=$(printf '\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 HH_LONG=1 HH_N=18 lib _hh 2>&1 | strip)
s1=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==2'); p1="${s1%%Enter — дальше*}"; p2="${s1#*к выбору действий: }"; p2="${p2%%Выбор*}"
[ "$s1" != "$p1" ] && [ "$(nlines "$p1")" -le 23 ] && printf '%s\n' "$p1" | grep -q '^18) ' \
  && ! printf '%s' "$p1" | grep -q ' 1) Подробно' && printf '%s\n' "$p2" | grep -qx ' 1) Подробно о проверке' \
  && ok "22 → 5 в окне 80×24: 18 проверок — пункты не влезают под список, уходят на следующий кусок" \
  || bad "22 → 5: 80×24, граница у пунктов" "$(nlines "$p1") | $s1"
# REV-ns-b14-2 П-2: «Подробно» и «Пропуски» длиннее окна (40 строк) — листаются кусками ≤ 23 строк, затем пауза
o=$(printf '1\n1\n\n3\n\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 K3_PAUSE_SEC=0 HH_TALL=1 lib _hh 2>&1 | strip)
r=""
for nr in 3 5; do
    s=$(printf '%s' "$o" | awk -v n="$nr" 'BEGIN{RS="\f"} NR==n'); p1="${s%%Enter — дальше*}"; p2="${s#*к выбору действий: }"
    p2="${p2%%\[ПАУЗА\]*}"
    if [ "$s" != "$p1" ] && [ "$(nlines "$p1")" -le 23 ] && [ "$(nlines "$p2")" -le 23 ] \
       && printf '%s' "$p1" | grep -q ' 01$' && printf '%s' "$p2" | grep -q ' 40$' && printf '%s' "$s" | grep -q '\[ПАУЗА\]'
    then r+="ok "; else r+="bad($nr: $(nlines "$p1")/$(nlines "$p2")) "; fi
done
[ "$r" = "ok ok " ] && ok "22 → 5 → 1 и 3 в окне 80×24: длинные «Подробно» и «Пропуски» листаются, куски ≤ 23 строк, потом пауза" \
  || bad "22 → 5: «Подробно»/«Пропуски» в 80×24" "$r | $o"
use_root hm45; fake_ctl; engine_nft
o=$(printf '0\n' | K3_TERM_COLS=80 lib t_hm 2>&1 | strip)
printf '%s\n' "$o" | grep -qx ' 4) Расписание проверок' && printf '%s\n' "$o" | grep -qx ' 5) История проверок' \
  && [ "$(printf '%s\n' "$o" | grep -nE '^ [0-9]\) ' | cut -d: -f2 | cut -c2 | tr -d '\n')" = "1234560" ] \
  && ok "22: «4) Расписание проверок», «5) История проверок» после «3) Проверить сервер», затем 6 и 0 — номера A не сдвинулись" \
  || bad "22: пункты 4 и 5" "$o"
# REV-ns-a8 З-2: проверка здоровья выключена при работающем шлюзе — на экране «Включить — 2 на этом экране», пункт 2 —
# «Проверку здоровья сервера: включить» (пробы без неё не идут), пункта 6 нет; 2 включает саму проверку
use_root hm-off; fake_ctl
printf '{"engine": "nft", "gateway": {"enabled": true, "hostmon": {"enabled": false}}, "schema": 1}\n' > "$KASKAD_DIR/state.json"
o=$(printf '2\n0\n' | K3_TERM_COLS=80 lib t_hm 2>&1 | strip); s1=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==2'); s2=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==3')
printf '%s\n' "$s1" | grep -qx '  Включить — 2 на этом экране' && printf '%s\n' "$s1" | grep -qx ' 2) Проверку здоровья сервера: включить' \
  && ! printf '%s' "$s1" | grep -q ' 6) ' && ! printf '%s' "$s1" | grep -q 'Лёгкие пробы: включить' \
  && ctl_log | grep -qx 'host set --on' && ! ctl_log | grep -q 'host set --probes' \
  && [ "$(jq -r '.gateway.hostmon.enabled' "$KASKAD_DIR/state.json")" = true ] \
  && printf '%s' "$s2" | grep -q 'Проверка здоровья сервера включена' && printf '%s\n' "$s2" | grep -qx ' 2) Лёгкие пробы: включить' \
  && printf '%s\n' "$s2" | grep -qx ' 6) Не напоминать о пробах' \
  && ok "22, проверка выключена: «Включить — 2 на этом экране», 2 — «Проверку здоровья сервера: включить» → host set --on, дальше — пробы" \
  || bad "22: проверка здоровья выключена" "$(ctl_log | tr '\n' ' ') | $s1 | $s2"
# REV-ns-a-2 П-1: проверка выключена, ключа probes нет — пункта 6 на экране нет, и ввод «6» — «Нет такого пункта»,
# ничего не пишется (без проверки «$en != on» скрытый номер записал бы probes: false)
printf '{"engine": "nft", "gateway": {"enabled": true, "hostmon": {"enabled": false}}, "schema": 1}\n' > "$KASKAD_DIR/state.json"
: > "$CALLS/kaskadctl.log"
o=$(printf '6\n0\n' | K3_TERM_COLS=80 K3_PAUSE_SEC=0 lib t_hm 2>&1 | strip)
printf '%s\n' "$o" | grep -q 'Нет такого пункта: введите номер из списка, 0 — назад.' && ! ctl_log | grep -q '^host set' \
  && [ "$(jq -r '.gateway.hostmon | has("probes")' "$KASKAD_DIR/state.json")" = false ] \
  && ok "22, проверка выключена: 6 — «Нет такого пункта», host set не зовётся, probes не записан" \
  || bad "22: 6 при выключенной проверке" "$(ctl_log | tr '\n' ' ') | $o"
# REV-ns-a8 З-3: узел с тревогами (вывод длиннее окна) — постранично, как пункт 21: первый кусок с заголовком и
# светофором, каждый кусок с приглашением — не больше 24 строк по 80 столбцов, пункты и «Выбор» — вместе внизу
use_root hm-long; fake_ctl; engine_nft
mkdir -p "$FAKE_DIR/host"
{ echo "Состояние: Требует внимания"
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13; do
      echo "✗ Проверка номер $i: показатель вышел за предел, сервер с ним работает медленнее обычного"
      echo "  Совет $i: посмотрите, что занимает ресурс, и освободите его; если не помогает — возьмите тариф побольше"
  done
  echo "Лёгкие пробы сети: включены, точки: 1.1.1.1, 8.8.8.8, 77.88.8.8"; } > "$FAKE_DIR/host/health.txt"
pages22() { python3 -c '
import sys
text = sys.stdin.read().split("\f", 1)[-1].split("\f")[0]
pages = text.split("Enter — дальше, 0 — к выбору действий: ")
rows = [sum(max(1, -(-len(l) // 80)) for l in p.split("\n")) for p in pages]
head = "━━━ Нагрузка и сеть сервера ━━━" in pages[0] and "Состояние: Требует внимания" in pages[0]
menu = [i for i, p in enumerate(pages) if " 1) Обновить" in p and " 0) Назад" in p and "Выбор [1]: " in p]
print("pauses=%d max=%d head=%s menu=%s" % (len(pages) - 1, max(rows), head, menu))'; }
o=$(printf '\n\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_hm 2>&1 | strip); r=$(printf '%s' "$o" | pages22)
o0=$(printf '0\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_hm 2>&1 | strip); r0=$(printf '%s' "$o0" | pages22)
[ "$r" = "pauses=2 max=24 head=True menu=[2]" ] && printf '%s' "$o" | grep -q 'Совет 13:' && [ "$r0" = "pauses=1 max=24 head=True menu=[1]" ] \
  && ! printf '%s' "$o0" | grep -q 'Совет 13:' && [ "$(printf '%s' "$o" | k3_text_check)" = ok ] \
  && ok "22 на узле с тревогами в 80×24: постранично, заголовок и светофор в первом куске, пункты внизу; «0» на паузе — сразу к пунктам" \
  || bad "22: длинный вывод в 80×24" "$r | $r0 | $o"
# текст кончается у низа окна: пункты не влезают — пауза перед ними, а не меню за краем (k3_pg_room)
{ echo "Состояние: Есть предупреждения"; for i in $(seq 1 20); do echo "! Проверка $i: выше обычного"; done; } > "$FAKE_DIR/host/health.txt"
: > "$FAKE_DIR/host/score.txt"                  # B7: без строк сравнения — текст здоровья ровно до низа окна
o=$(printf '\n0\n' | K3_TERM_COLS=80 K3_TERM_ROWS=24 lib t_hm 2>&1 | strip); r=$(printf '%s' "$o" | pages22)
rm -f "$FAKE_DIR/host/score.txt"
[ "$r" = "pauses=1 max=23 head=False menu=[1]" ] && printf '%s' "$o" | awk 'BEGIN{RS="Enter — дальше"} NR==1' | grep -q 'Проверка 20:' \
  && ok "22: текст до низа окна — пауза, пункты и «Выбор» на следующем куске, ни один кусок не длиннее 24 строк" || bad "22: пункты у низа окна" "$r | $o"
# П-5: ошибка ядра — его текст как есть («Ошибка: …»), без второй рамки «Не получилось: Ошибка: …»; пусто — с кодом
printf 'Ошибка: Не удалось прочитать здоровье сервера: битый файл\n' > "$FAKE_DIR/host/health.txt"; echo 1 > "$FAKE_DIR/host/health.rc"
o=$(printf '0\n' | K3_TERM_COLS=80 lib t_hm 2>&1 | strip)
: > "$FAKE_DIR/host/health.txt"
o2=$(printf '0\n' | K3_TERM_COLS=80 lib t_hm 2>&1 | strip)
printf '%s\n' "$o" | grep -qx 'Ошибка: Не удалось прочитать здоровье сервера: битый файл' && ! printf '%s' "$o" | grep -q 'Не получилось' \
  && printf '%s\n' "$o2" | grep -qx 'Не получилось (код 1).' && printf '%s\n' "$o" | grep -qx ' 0) Назад' \
  && ok "22: ошибка ядра — его текст без второй рамки; пустой ответ — «Не получилось (код 1).»; пункты на месте" \
  || bad "22: ошибка ядра" "$o | $o2"
rm -f "$FAKE_DIR/host/health.txt" "$FAKE_DIR/host/health.rc"
printf 'Ошибка: Kaskad уже что-то меняет, повторите через минуту.\n' > "$FAKE_DIR/host/set.txt"; echo 6 > "$FAKE_DIR/host/set.rc"
o=$(printf '2\n0\n' | K3_TERM_COLS=80 lib t_hm 2>&1 | strip); s2=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==3')
: > "$FAKE_DIR/host/set.txt"
o=$(printf '2\n0\n' | K3_TERM_COLS=80 lib t_hm 2>&1 | strip); s3=$(printf '%s' "$o" | awk 'BEGIN{RS="\f"} NR==3')
printf '%s\n' "$s2" | grep -qx 'Ошибка: Kaskad уже что-то меняет, повторите через минуту.' && ! printf '%s' "$s2" | grep -q 'Не получилось' \
  && printf '%s\n' "$s3" | grep -qx 'Не получилось (код 6).' \
  && ok "22 → 2, ядро отказало: его текст над приглашением без второй рамки; пустой ответ — «Не получилось (код 6).»" \
  || bad "22 → 2: отказ ядра" "$s2 | $s3"
rm -rf "$FAKE_DIR/host"
use_root hm3; engine_nft
o=$(printf '\n' | lib t_hm 2>&1 | strip)
printf '%s' "$o" | grep -q 'Ядро Kaskad 3 недоступно' && ok "22 без ядра: «Ядро Kaskad 3 недоступно …», в меню" || bad "22 без ядра" "$o"
# «Поиск неполадок» (21): тревога host_* — «Пункт 22: …», номер 22 — переход на экран пункта 22
head_root hm4; head_js '.problems = [{"kind": "host_disk", "detail": "На диске / осталось 0,4 ГБ (99 % занято)", "item": 22, "source": "live", "advice": "Освободите место на диске; что занимает и совет — 22 «Нагрузка и сеть сервера»."}]'
t_ck22() { scr k3_check_screen; }
o=$(printf '22\n0\n' | K3_TERM_COLS=80 lib t_ck22 2>&1 | strip)
printf '%s' "$o" | grep -q '^  Пункт 22: Освободите место' \
  && printf '%s' "$o" | grep -q '━━━ Нагрузка и сеть сервера ━━━$' \
  && ok "21: тревога диска — «Пункт 22: …», ввод 22 — экран «Нагрузка и сеть сервера»" || bad "21 → 22" "$o"
# новая установка пишет probes: true явно; обновление и узел на старом движке — нет
# отметка на диске (REV-ns-a8 З-4, как gw-offer.pending): шаг 2 первой установки ставит её, k3_hostmon_fresh снимает,
# когда ключ записан
t_hmfresh() { K3_FRESH="$1"; [ "$1" = 1 ] && k3_hostmon_mark; k3_hostmon_fresh; echo "RC=$?"; }
HMM=hostmon-probes.pending
use_root hm-fresh; fake_ctl; engine_nft
[ "$(lib t_hmfresh 1)" = "RC=0" ] && [ "$(jq -r '.gateway.hostmon.probes' "$KASKAD_DIR/state.json")" = true ] \
  && ctl_log | grep -qx 'host set --probes on --json' && [ ! -e "$KASKAD_DIR/$HMM" ] \
  && ok "новая установка (K3_FRESH=1, движок nft): в state.json gateway.hostmon.probes: true, отметка снята" || bad "новая установка: probes" "$(cat "$KASKAD_DIR/state.json")"
# оборванная первая установка: отметка осталась, следующий запуск уже K3_FRESH=0 — ключ всё равно пишется
use_root hm-cut; fake_ctl; engine_nft; : > "$KASKAD_DIR/$HMM"
[ "$(lib t_hmfresh 0)" = "RC=0" ] && [ "$(jq -r '.gateway.hostmon.probes' "$KASKAD_DIR/state.json")" = true ] && [ ! -e "$KASKAD_DIR/$HMM" ] \
  && ok "оборванная первая установка (отметка на диске, K3_FRESH=0): probes: true, отметка снята" || bad "оборванная установка: probes" "$(ctl_log) | $(ls -A "$KASKAD_DIR")"
# ядро отказало или его нет — отметка ждёт следующего запуска; ключ уже есть (false — «Не напоминать») — не трогаем
use_root hm-fail; fake_ctl; engine_nft; : > "$KASKAD_DIR/$HMM"; mkdir -p "$FAKE_DIR/host"; echo x > "$FAKE_DIR/host/set.txt"; echo 6 > "$FAKE_DIR/host/set.rc"
r1=$(lib t_hmfresh 0); e1=$([ -e "$KASKAD_DIR/$HMM" ] && echo kept); rm -rf "$FAKE_DIR/host" "$K3_OPT/bin/kaskadctl"
r2=$(lib t_hmfresh 0); e2=$([ -e "$KASKAD_DIR/$HMM" ] && echo kept)
fake_ctl; printf '{"engine": "nft", "gateway": {"hostmon": {"probes": false}}, "schema": 1}\n' > "$KASKAD_DIR/state.json"; : > "$CALLS/kaskadctl.log"
r3=$(lib t_hmfresh 0)
[ "$r1 $e1 $r2 $e2 $r3" = "RC=0 kept RC=0 kept RC=0" ] && [ ! -e "$KASKAD_DIR/$HMM" ] && ! ctl_log | grep -q '^host' \
  && [ "$(jq -r '.gateway.hostmon.probes' "$KASKAD_DIR/state.json")" = false ] \
  && ok "отметка: ядро отказало или его нет — ждёт; ключ уже есть — снята без вызова ядра, false не тронут" || bad "отметка проб" "$r1 $e1 $r2 $e2 $r3 | $(ctl_log)"
use_root hm-upd; fake_ctl; engine_nft
[ "$(lib t_hmfresh 0)" = "RC=0" ] && [ "$(jq -r '.gateway.hostmon.probes | type' "$KASKAD_DIR/state.json")" = null ] && ! ctl_log | grep -q '^host' \
  && ok "обновление (K3_FRESH=0): ключ probes не появляется" || bad "обновление: probes" "$(ctl_log)"
use_root hm-legacy; fake_ctl
[ "$(lib t_hmfresh 1)" = "RC=0" ] && [ ! -e "$KASKAD_DIR/state.json" ] && ! ctl_log | grep -q '^host' && [ ! -e "$KASKAD_DIR/$HMM" ] \
  && ok "новая установка на старом движке (state.json нет) — без ключа и без вызова ядра, отметка снята" || bad "старый движок: probes" "$(ctl_log)"
body=$(sed -n '/^run_startup() {/,/^}/p' "$S")
e=$(printf '%s\n' "$body" | grep -n '^    k3_startup_engine$' | cut -d: -f1); h=$(printf '%s\n' "$body" | grep -n '^    k3_hostmon_fresh ' | cut -d: -f1)
[ -n "$e" ] && [ -n "$h" ] && [ "$h" = $((e + 1)) ] && ok "run_startup: k3_hostmon_fresh сразу после k3_startup_engine (state.json уже есть)" || bad "run_startup: k3_hostmon_fresh" "e=$e h=$h"
i=$(printf '%s\n' "$body" | grep -n '^    init_config' | head -1 | cut -d: -f1); mk=$(printf '%s\n' "$body" | grep -n 'K3_FRESH" = 1 \] && k3_hostmon_mark' | head -1 | cut -d: -f1)
[ -n "$i" ] && [ -n "$mk" ] && [ "$mk" -gt "$i" ] && [ "$mk" -lt "$e" ] \
  && ok "run_startup: отметка «включить пробы» — на шаге 2 первой установки, до apt и переезда (REV-ns-a8 З-4)" || bad "отметка проб в run_startup" "i=$i mk=$mk e=$e"
# итог обновления: одна строка «можно включить» (ядро нового файла); k3_post_update её не печатает (его зовут и службы)
[ "$(sed -n '/^self_update() {/,/^}/p' "$S" | grep -c 'k3ctl host probes-hint')" = 1 ] && ! sed -n '/^k3_post_update() {/,/^}/p' "$S" | grep -q 'probes-hint' \
  && ok "host probes-hint — в итоге self_update, не в k3_post_update" || bad "место подсказки о пробах"
su_setup "" $NEWER hm-hint; echo 'KASKAD_BRANCH="test"' >> "$KASKAD_DIR/config"
printf '{"engine": "nft", "gateway": {"enabled": true}, "schema": 1}\n' > "$KASKAD_DIR/state.json"
o=$(lib t_su 2>&1 | strip)
printf '%s' "$o" | grep -q "Обновлено до v$NEWER" && [ "$(printf '%s\n' "$o" | grep -c 'Можно включить лёгкие пробы сети')" = 1 ] \
  && printf '%s' "$o" | flat | grep -q 'пункт 22 SSH-меню или kaskadctl host set --probes on' \
  && ok "обновление узла со шлюзом без ключа probes: в итоге одна строка «Можно включить лёгкие пробы сети … пункт 22 …»" \
  || bad "подсказка в итоге обновления" "$o"
su_setup "" $NEWER hm-hint2; echo 'KASKAD_BRANCH="test"' >> "$KASKAD_DIR/config"
printf '{"engine": "nft", "gateway": {"enabled": true, "hostmon": {"probes": false}}, "schema": 1}\n' > "$KASKAD_DIR/state.json"
o=$(lib t_su 2>&1 | strip)
printf '%s' "$o" | grep -q "Обновлено до v$NEWER" && ! printf '%s' "$o" | grep -q 'лёгкие пробы' \
  && ok "обновление, probes: false («Не напоминать») — строки нет" || bad "подсказка при probes false" "$o"

echo "── 56. T13: «Переименовать» в SSH-меню — пользователи, устройства, выходы, протоколы, подписки, пробросы, серверы ──"
use_root rn; fake_ctl
gw_st true '{"wg": {}, "awg3": {}}' 2 1
gwf user_list.json '{"ok": true, "users": [{"id": "u-00000001", "name": "Мама\\nОля\\t\\e[31m", "route": null, "enabled": true, "devices": 2}]}'
gwf device_list.json '{"ok": true, "devices": [
 {"id": "d-00000001", "user": "u-00000001", "name": "Телефон\\c", "kind": "phone", "platform": "android", "login": "telefon-0001", "access": ["wg"], "route": null, "enabled": true},
 {"id": "d-00000002", "user": "u-00000001", "name": "Ноутбук", "kind": "pc", "platform": "linux", "login": "noutbuk-0002", "access": ["wg"], "route": null, "enabled": true}]}'
gwf exits.json '{"ok": true, "exits": [{"id": "e-3fa9c1d2b4", "index": 1, "name": "Вена", "kind": "vless", "host": "198.51.100.31", "usable": true, "enabled": true}]}'
gwf inbound_list.json "$(gw_list "wg awg3")"
t_people() { K3_TEST_TTY=1; k3_gw_people; }
o=$(printf '0\n' | lib t_people 2>&1 | strip)
printf '%s' "$o" | grep -qF '👤 Мама\nОля\t\e[31m · маршрут' && printf '%s' "$o" | grep -qF '1) Телефон\c — Android' \
  && printf '%s' "$o" | grep -q '13) Переименовать устройство' && printf '%s' "$o" | grep -q '14) Переименовать пользователя' \
  && ok "имена с «\\n», «\\t», «\\e[…», «\\c» — печатаются текстом (echo -e их не выполняет); пункты 13 и 14" || bad "имена с «\\» в меню пользователей" "$o"
gwf device_rename.json '{"ok": true, "what": "device", "id": "d-00000002", "old": "Ноутбук", "name": "Ноут Оли", "shown": "Ноут Оли", "duplicates": [], "note": "В приложении имя останется прежним, пока вы не выдадите настройки заново."}'
: > "$CALLS/kaskadctl.log"; o=$(printf '13\n2\nНоут Оли\n\n0\n' | lib t_people 2>&1 | strip)
gw_calls | grep -qx 'gw device rename d-00000002 --name=Ноут Оли --via=ssh --json' && printf '%s' "$o" | grep -q 'Сейчас: «Ноутбук»' \
  && printf '%s' "$o" | grep -q 'Сохранено: «Ноут Оли»\.' \
  && printf '%s' "$o" | grep -q 'пока вы не выдадите настройки заново' \
  && ok "13 → устройство → имя → gw device rename <id> --name=… --via=ssh; «Сохранено», подсказка ядра" || bad "переименование устройства" "$(gw_seq) | $o"
# дубль: вопрос цифрами (по умолчанию «Ввести другое имя»), «1» — повтор с --allow-duplicate
gwf device_rename.once.json '{"ok": false, "error": "Имя «Телефон» уже есть. Сохранить всё равно?", "code": 3, "data": {"need": "confirm_duplicate", "duplicates": ["d-00000001"]}}'
gwf device_rename.once.rc 3
: > "$CALLS/kaskadctl.log"; o=$(printf '13\n2\nТелефон\n1\n\n0\n' | lib t_people 2>&1 | strip)
gw_calls | grep -qx 'gw device rename d-00000002 --name=Телефон --via=ssh --json' \
  && gw_calls | grep -qx 'gw device rename d-00000002 --name=Телефон --via=ssh --allow-duplicate --json' \
  && printf '%s' "$o" | grep -q 'Уже есть «Телефон» (d-00000001)\.' && printf '%s' "$o" | grep -q '1) Всё равно сохранить' \
  && printf '%s' "$o" | grep -q '2) Ввести другое имя' && printf '%s' "$o" | grep -q '3) Отмена' && printf '%s' "$o" | grep -q 'Выбор \[2\]' \
  && ok "дубль: «Уже есть …», 1) Всё равно сохранить 2) Ввести другое имя 3) Отмена, Выбор [2]; «1» — --allow-duplicate" || bad "дубль имени" "$(gw_seq) | $o"
# отказ проверки (код 2) — словами ядра и вопрос снова; Enter — отмена, ядро второй раз не зовётся
gwf user_rename.once.json '{"ok": false, "error": "Имя пользователя: длиннее 40 знаков (41).", "code": 2, "data": {"reason": "long"}}'
gwf user_rename.once.rc 2
: > "$CALLS/kaskadctl.log"; o=$(printf '14\nxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n\n\n0\n' | lib t_people 2>&1 | strip)
[ "$(gw_calls | grep -c 'gw user rename')" = 1 ] && printf '%s' "$o" | grep -q 'Имя пользователя: длиннее 40 знаков (41)\.' \
  && printf '%s' "$o" | grep -q 'Отменено, имя прежнее\.' \
  && ok "отказ проверки имени — словами ядра, вопрос снова; Enter — «Отменено, имя прежнее»" || bad "отказ проверки имени" "$(gw_seq) | $o"
# имя с ведущим «-» и кавычками — одним аргументом --name=… (не через eval)
gwf user_rename.json '{"ok": true, "what": "user", "id": "u-00000001", "name": "-$(id) \"Оля\"", "shown": "-$(id) \"Оля\"", "note": ""}'
: > "$CALLS/kaskadctl.log"; o=$(printf '14\n-$(id) "Оля"\n\n0\n' | lib t_people 2>&1 | strip)
gw_calls | grep -qxF 'gw user rename u-00000001 --name=-$(id) "Оля" --via=ssh --json' \
  && ok "имя с «-», «\$(…)» и кавычками — аргументом --name=…, ничего не выполняется" || bad "имя-аргумент" "$(gw_seq)"
# выход: пункт после «Подписки», «-» — снова имя из ключа (--name= пусто)
t_exits() { K3_TEST_TTY=1; egress_awg_runs() { return 0; }; k3_gw_exits_menu; }
gwf exit.json '{"ok": true, "what": "exit", "id": "e-3fa9c1d2b4", "name": "", "shown": "VLESS · 198.51.100.31", "note": "Сам ключ и настройки не меняются."}'
: > "$CALLS/kaskadctl.log"; o=$(printf '7\n1\n-\n\n0\n' | lib t_exits 2>&1 | strip)
printf '%s' "$o" | grep -q '5) Подписки выходов' && printf '%s' "$o" | grep -q '6) Состояние и группы' && printf '%s' "$o" | grep -q '7) Переименовать выход (понятное имя)' \
  && gw_calls | grep -qx 'gw exit e-3fa9c1d2b4 --name= --via=ssh --json' && printf '%s' "$o" | grep -q 'Сейчас: «Вена»' \
  && printf '%s' "$o" | grep -q 'Своё имя убрано, показывается: «VLESS · 198.51.100.31»' \
  && ok "17 → 3 → 7 «Переименовать выход»: «-» — сброс (--name= пусто), номера прежних пунктов на месте" || bad "переименование выхода" "$(gw_seq) | $o"
# протокол: «Подпись протокола» — последним пунктом карточки; подпись видна в шапке и в таблице
gwf inbound_list.json "$(gw_list "wg awg3" | jq -c '(.inbounds[] | select(.kind == "wg") | .label) = "для роутеров"')"
t_proto() { K3_TEST_TTY=1; k3_gw_protocols; }
gwf inbound_label.json '{"ok": true, "what": "inbound", "id": "wg", "name": "дом", "shown": "WireGuard — дом", "note": "Сам ключ и настройки не меняются."}'
: > "$CALLS/kaskadctl.log"; o=$(printf '1\n4\nдом\n\n0\n0\n' | lib t_proto 2>&1 | strip)
gw_calls | grep -qx 'gw inbound label wg --name=дом --via=ssh --json' && printf '%s' "$o" | grep -q 'WireGuard — для роутеров' \
  && printf '%s' "$o" | grep -q '4) Понятная подпись протокола' && printf '%s' "$o" | grep -q '3) Удалить протокол' \
  && printf '%s' "$o" | grep -q '· для роутеров$' \
  && ok "карточка протокола: подпись в шапке и в таблице, «4) Подпись протокола» последней (Удалить — 3, как было)" || bad "подпись протокола" "$(gw_seq) | $o"
# подписка: 6 — переименовать, 7 — вернуть исключённые
gwf exit-sub_list.json '{"ok": true, "subs": [{"id": "s-aaaaaa", "n": 1, "name": "Моя", "label": "Моя", "domain": "sub.example.org", "auto": true, "stale": false, "text": "обновлена 3 ч назад", "excluded": 2, "last_ok": 1}]}'
t_subs() { K3_TEST_TTY=1 k3_exit_subs_menu; }
gwf exit-sub_rename.json '{"ok": true, "what": "exitsub", "id": "s-aaaaaa", "name": "", "shown": "sub.example.org", "note": ""}'
: > "$CALLS/kaskadctl.log"; o=$(printf '6\n-\n\n0\n' | lib t_subs 2>&1 | strip)
gw_calls | grep -qx 'gw exit-sub rename s-aaaaaa --name= --via=ssh --json' && printf '%s' "$o" | grep -q '6) Переименовать подписку' \
  && printf '%s' "$o" | grep -q '7) Вернуть исключённые ключи (2)' && printf '%s' "$o" | grep -q 'Сейчас: «Моя»' \
  && ok "подписки выходов: 6 — переименовать («-» — снова домен), 7 — вернуть исключённые" || bad "переименование подписки" "$(gw_seq) | $o"
# пробросы (меню 5, nft): имя из state.json рядом со строкой, «1) Переименовать проброс» → fwd rename tcp 443
engine_nft_fwds
jq '.forwards |= map(if .port == 443 then . + {"name": "Сайт"} else . end)' "$KASKAD_DIR/state.json" > "$W/st.t" && mv "$W/st.t" "$KASKAD_DIR/state.json"
printf 'tcp|443|2.2.2.2:443|0|0\nudp|51820|5.5.5.5:51820|0|0\n' > "$FAKE_DIR/fwd_list_traffic"
t_fwdlist() { list_active_rules; }
gwf fwd_rename.json '{"ok": true, "what": "forward", "id": "tcp:443", "name": "Сайт 2", "shown": "Сайт 2", "note": ""}'
: > "$CALLS/kaskadctl.log"; o=$(printf '1\n1\nСайт 2\n\n' | lib t_fwdlist 2>&1 | strip)
ctl_log | grep -qx 'fwd rename tcp 443 --name=Сайт 2 --via=ssh --json' && printf '%s' "$o" | grep -q '\[1\] .*:443 (tcp).* · Сайт$' \
  && printf '%s' "$o" | grep -q '1) Переименовать проброс' && printf '%s' "$o" | grep -q 'Сохранено: «Сайт 2»' \
  && ok "меню 5: имя проброса в строке; «1) Переименовать проброс» → fwd rename tcp 443 --name=…" || bad "переименование проброса" "$(ctl_log) | $o"
o=$(lib t_fwdlist </dev/null 2>&1 | strip); [ -z "$(ctl_log | grep rename | sed -n 2p)" ] && ok "меню 5 без ввода — выходит, ничего не переименовано" || bad "меню 5 без ввода" "$o"
# неверный ввод — «Нет такого пункта» и вопрос снова (без перерисовки); после переименования — список снова; 0 — назад
: > "$CALLS/kaskadctl.log"; o=$(printf 'x\n1\n1\nСайт 3\n\n0\n' | lib t_fwdlist 2>&1 | strip)
[ "$(printf '%s\n' "$o" | grep -c 'Нет такого пункта: введите номер из списка, 0 — назад\.')" = 1 ] \
  && [ "$(printf '%s\n' "$o" | grep -c '1) Переименовать проброс')" = 2 ] && printf '%s' "$o" | grep -q ' 0) Назад' \
  && [ "$(ctl_log | grep -c 'fwd rename tcp 443 --name=Сайт 3')" = 1 ] \
  && ok "меню 5: « 0) Назад», неверный ввод — вопрос снова, после переименования список рисуется снова" || bad "меню 5: цикл" "$(ctl_log) | $o"
# меню 14: имя сервера — через ядро (server rename), заметка — как раньше
printf '198.51.100.7=Старое|заметка||\n' > "$KASKAD_DIR/aliases"
t_al() { ALIASES_FILE="$KASKAD_DIR/aliases"; get_target_ips() { echo 198.51.100.7; }; manage_aliases_menu; }
: > "$CALLS/kaskadctl.log"; o=$(printf '1\nВена\n\n\n0\n' | lib t_al 2>&1 | strip)
ctl_log | grep -qx 'server rename 198.51.100.7 --name=Вена --via=ssh --json' && grep -qx '198.51.100.7=Вена|заметка||' "$KASKAD_DIR/aliases" \
  && ok "меню 14: имя сервера — kaskadctl server rename (проверка имени и журнал — в ядре), заметка прежняя" || bad "меню 14: имя сервера" "$(ctl_log) | $(cat "$KASKAD_DIR/aliases") | $o"
# отказ ядра (код 2) — словами, имя прежнее; дубль — вопрос цифрами, «1» — --allow-duplicate
gwf server_rename.once.json '{"ok": false, "error": "Имя сервера: недопустимый знак U+005C.", "code": 2, "data": {"reason": "char"}}'; gwf server_rename.once.rc 2
: > "$CALLS/kaskadctl.log"; o=$(printf '1
а\\б


0
' | lib t_al 2>&1 | strip)
printf '%s' "$o" | grep -q 'Имя сервера: недопустимый знак U+005C\.' && grep -qx '198.51.100.7=Вена|заметка||' "$KASKAD_DIR/aliases" \
  && ok "меню 14: отказ проверки имени — словами ядра, имя прежнее" || bad "меню 14: отказ" "$o"
gwf server_rename.once.json '{"ok": false, "error": "dup", "code": 3, "data": {"need": "confirm_duplicate", "duplicates": ["203.0.113.5"]}}'; gwf server_rename.once.rc 3
: > "$CALLS/kaskadctl.log"; o=$(printf '1
Рига
1


0
' | lib t_al 2>&1 | strip)
printf '%s' "$o" | grep -q 'Уже есть «Рига» (203.0.113.5)\.' && ctl_log | grep -qx 'server rename 198.51.100.7 --name=Рига --via=ssh --allow-duplicate --json' \
  && ok "меню 14: дубль имени — вопрос, «1» — --allow-duplicate" || bad "меню 14: дубль" "$(ctl_log) | $o"
# мастер проброса: такое же имя у другого сервера — тоже предупреждение и вопрос (REV-s1-t13, придирка 1);
# «2) Отмена» — имя прежнее, мастер идёт дальше; «1» — --allow-duplicate
t_fwdname() { k3_fwd_server_name 198.51.100.7; }
gwf server_rename.once.json '{"ok": false, "error": "dup", "code": 3, "data": {"need": "confirm_duplicate", "duplicates": ["203.0.113.5"]}}'; gwf server_rename.once.rc 3
: > "$CALLS/kaskadctl.log"; o=$(printf 'Рига\n2\n' | lib t_fwdname 2>&1 | strip)
printf '%s' "$o" | grep -q 'Уже есть «Рига» (203.0.113.5)\.' && printf '%s' "$o" | grep -q '2) Отмена' \
  && [ "$(ctl_log | grep -c 'server rename')" = 1 ] && grep -qx '198.51.100.7=Рига|заметка||' "$KASKAD_DIR/aliases" \
  && ok "мастер проброса: дубль имени сервера — вопрос, «Отмена» — имя прежнее" || bad "мастер проброса: дубль" "$(ctl_log) | $o"
gwf server_rename.once.json '{"ok": false, "error": "dup", "code": 3, "data": {"need": "confirm_duplicate", "duplicates": ["203.0.113.5"]}}'; gwf server_rename.once.rc 3
: > "$CALLS/kaskadctl.log"; o=$(printf 'Рига 2\n1\n' | lib t_fwdname 2>&1 | strip)
ctl_log | grep -qx 'server rename 198.51.100.7 --name=Рига 2 --via=ssh --allow-duplicate --json' \
  && ok "мастер проброса: «1) Всё равно сохранить» — --allow-duplicate" || bad "мастер проброса: дубль 1" "$(ctl_log) | $o"
# английский: тот же диалог
t_people_en() { K3_LANG=en; t_people; }
gwf device_rename.json '{"ok": true, "what": "device", "id": "d-00000002", "name": "Laptop", "shown": "Laptop", "note": ""}'
o=$(printf '13\n2\nLaptop\n\n0\n' | lib t_people_en 2>&1 | strip)
printf '%s' "$o" | grep -q '13) Rename a device' && printf '%s' "$o" | grep -q 'Now: “Ноутбук”' \
  && printf '%s' "$o" | grep -q 'Saved: “Laptop”\.' && ! printf '%s' "$o" | grep -q 'Сохранено\|Сейчас' \
  && ok "EN: «Rename a device», «Now: …», «Saved: …»" || bad "переименование EN" "$o"

echo "── 57. T13: имена с «\\c», «\\e]52;…», «%s», «\\n» — на каждом экране SSH-меню текстом (REV-s1-t13, С-1) ──"
# Имя доходит до echo -e (k3_gw_fold, k3_choose, k3_gw_head, k3_pg_msg) только с удвоенным «\»: «\c» не обрезает вопрос,
# «\e]52;…\a» не пишет в буфер обмена, «\e[2J» не стирает экран. Проверка: имя видно целиком, вопрос после него — тоже,
# в выводе нет ни ESC, кроме цветов (strip), ни BEL. Выходы — как в их таблице: без «\» (k3_term_filter).
use_root rn57; fake_ctl
gw_st true '{"wg": {}, "awg3": {}}' 2 2
N_U='Мама\cU1%s'; N_D='Тел\e]52;c;SGk=\aD1'; N_P='Папа\nP2\e[2J'; N_S='Моя\cQQ\e]52;c;SGk=\a'
gwf user_list.json '{"ok": true, "users": [{"id": "u-00000001", "name": "Мама\\cU1%s", "route": null, "enabled": true, "devices": 1},
 {"id": "u-00000002", "name": "Папа\\nP2\\e[2J", "route": null, "enabled": true, "devices": 0}]}'
gwf device_list.json '{"ok": true, "devices": [
 {"id": "d-00000001", "user": "u-00000001", "name": "Тел\\e]52;c;SGk=\\aD1", "kind": "phone", "platform": "android", "login": "tel-0001", "access": ["wg", "awg3"], "route": null, "enabled": true}]}'
gwf exits.json '{"ok": true, "exits": [{"id": "e-3fa9c1d2b4", "index": 1, "name": "Вена\\cZZ", "kind": "vless", "host": "198.51.100.31", "usable": true, "enabled": true}]}'
gwf inbound_list.json "$(gw_list "wg awg3")"
jq '.gateway.users = [{"id": "u-00000001", "name": "Мама\\cU1%s"}, {"id": "u-00000002", "name": "Папа\\nP2\\e[2J"}]
    | .gateway.devices = [{"id": "d-00000001", "user": "u-00000001", "name": "Тел\\e]52;c;SGk=\\aD1"}]' "$KASKAD_DIR/state.json" > "$W/st57" \
  && mv "$W/st57" "$KASKAD_DIR/state.json"
# чисто: ни OSC/ESC вне цветов, ни BEL (strip снял только цвета)
t57_clean() { ! printf '%s' "$1" | grep -q $'\x1b' && ! printf '%s' "$1" | grep -q $'\a'; }
t57_has() { printf '%s' "$1" | flat | grep -qF -- "$2"; }
t_people() { K3_TEST_TTY=1 K3_TERM_COLS=250 TERM=dumb; k3_gw_people; }
o=$(printf '0\n' | lib t_people 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "👤 ${N_U} · маршрут" && t57_has "$o" "1) ${N_D} — Android" && t57_has "$o" "👤 ${N_P} · маршрут" \
  && ok "список пользователей и устройств: имена текстом целиком" || bad "57: список пользователей" "$o"
o=$(printf '8\n1\n2\n\n0\n' | lib t_people 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "Ключи и пароль устройства «${N_D}» пропадут." && t57_has "$o" "Удалить устройство?" \
  && ok "удаление устройства: заметка и вопрос целиком" || bad "57: удаление устройства" "$o"
o=$(printf '9\n1\n2\n\n0\n' | lib t_people 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "Вместе с пользователем «${N_U}» уйдут его устройства (1)" \
  && ok "удаление пользователя с устройствами: вопрос целиком (%s в имени — текстом)" || bad "57: удаление пользователя" "$o"
o=$(printf '9\n2\n2\n\n0\n' | lib t_people 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "Удалить пользователя «${N_P}»?" \
  && ok "удаление пользователя без устройств: «\\n», «\\e[2J» в имени — текстом" || bad "57: удаление пользователя 2" "$o"
o=$(printf '6\n1\n0\n\n0\n' | lib t_people 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "Маршрут для «${N_D}»:" && t57_has "$o" "Как у пользователя «${N_U}»" && t57_has "$o" "Только выход «ВенаcZZ»" \
  && ok "маршрут устройства: имя, «как у пользователя …», выход — без «\\»" || bad "57: маршрут устройства" "$o"
o=$(printf '7\n1\n0\n\n0\n' | lib t_people 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "Маршрут для «${N_U}»:" && ok "маршрут пользователя: имя текстом" || bad "57: маршрут пользователя" "$o"
o=$(printf '10\n1\n0\n0\n' | lib t_people 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "Шлюз: правила «Россия напрямую» — ${N_D}" && t57_has "$o" "Правила для «${N_D}»" \
  && ok "правила устройства: шапка и «Правила для …»" || bad "57: правила устройства" "$o"
o=$(printf '12\n1\n0\n0\n' | lib t_people 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "Шлюз: страница «${N_U}»" && ok "страница пользователя: шапка" || bad "57: страница пользователя" "$o"
o=$(printf '5\n1\n0\n\n0\n' | lib t_people 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "Новые ключи, пароли и сертификаты устройства «${N_D}»: его подключения прервутся" \
  && ok "новые ключи устройства: вводная целиком" || bad "57: новые ключи" "$o"
o=$(printf '4\n1\n0\n\n0\n' | lib t_people 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "Протоколы устройства «${N_D}» (номер — выдать или снять):" \
  && ok "протоколы устройства: шапка целиком" || bad "57: протоколы устройства" "$o"
o=$(printf '13\n1\n\n\n0\n' | lib t_people 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "Сейчас: «${N_D}»" && ok "переименование: «Сейчас: …» — текстом" || bad "57: Сейчас" "$o"
# отказ ядра с именем (K3_GW_ERR) и предупреждение (K3_GW_WARN) — тоже текстом
gwf user_add.json '{"ok": false, "error": "Пользователь «Мама\\cU1%s» уже есть.", "code": 3}'; gwf user_add.rc 3
o=$(printf '3\nмама\\cU1%%s\n\n0\n' | lib t_people 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "[ERROR] Пользователь «${N_U}» уже есть." && ok "отказ ядра с именем — текстом (K3_GW_ERR)" || bad "57: K3_GW_ERR" "$o"
rm -f "$FAKE_DIR/gw/user_add.json" "$FAKE_DIR/gw/user_add.rc"
gwf device_route.json '{"ok": true}'; gwf device_route.err "@K3:warn Устройство «Тел\\cW» ещё не подключалось."
o=$(printf '6\n1\n2\n\n0\n' | lib t_people 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "! Устройство «Тел\\cW» ещё не подключалось." && ok "предупреждение ядра с именем — текстом (K3_GW_WARN)" || bad "57: K3_GW_WARN" "$o"
rm -f "$FAKE_DIR/gw/device_route.err"
# «Куда пойдёт трафик?»: список устройств, «Чей трафик», ответ ядра с именами
t_trace() { K3_TEST_TTY=1 K3_TERM_COLS=250 TERM=dumb; k3_gw_trace_menu; }
gwf trace.json "$(printf '%s\n' '@K3:verdict exit' 'Устройство «Тел\e]52;c;SGk=\aD1» → выход «Вена\cZZ»' 'DNS: ответ «a\cb»')"
o=$(printf '1\n1\nexample.com\n0\n' | lib t_trace 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "1) ${N_D} — ${N_U}" && t57_has "$o" "Чей трафик: ${N_D} через WireGuard" \
  && t57_has "$o" "Устройство «${N_D}» → выход «Вена\\cZZ»" && t57_has "$o" 'DNS: ответ «a\cb»' \
  && ok "«Куда пойдёт трафик?»: список, «Чей трафик» и ответ ядра — имена текстом" || bad "57: трассировка" "$o"
# выходы: выключить — «[OK] Выход «ВенаcZZ»: …» целиком
gwf exit.json '{"ok": true}'
t_exits() { K3_TEST_TTY=1 K3_TERM_COLS=250 TERM=dumb; egress_awg_runs() { return 0; }; k3_gw_exits_menu; }
o=$(printf '4\n1\n\n0\n' | lib t_exits 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "[OK] Выход «ВенаcZZ»: выключен для шлюза." && ok "выход: «\\c» в имени не обрезает итог" || bad "57: выход" "$o"
# подписки: список, давно не обновлялась, раз в сутки ⇄ вручную, обновить, удалить, вернуть исключённые, переименовать
gwf exit-sub_list.json '{"ok": true, "subs": [{"id": "s-aaaaaa", "n": 1, "name": "Моя\\cQQ\\e]52;c;SGk=\\a", "label": "Моя\\cQQ\\e]52;c;SGk=\\a",
 "domain": "sub.example.org", "auto": true, "stale": false, "text": "обновлена", "excluded": 0, "last_ok": 1}]}'
gwf exit-sub_refresh.json '{"ok": true, "results": [{"ok": true, "text": "Моя\\cQQ\\e]52;c;SGk=\\a: без изменений", "warnings": ["Моя\\cWW"]}]}'
gwf exit-sub_auto.json '{"ok": true}'
gwf exit-sub_del.json '{"ok": true, "text": "Подписка «Моя\\cQQ\\e]52;c;SGk=\\a» удалена."}'
t_subs() { K3_TEST_TTY=1 K3_TERM_COLS=250 TERM=dumb; k3_exit_subs_menu; }
o=$(printf '0\n' | lib t_subs 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "1) ${N_S} — обновлена" && ok "подписки: подпись в списке текстом" || bad "57: список подписок" "$o"
o=$(printf '4\n\n0\n' | lib t_subs 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "[OK] ${N_S}: обновляется только вручную." && ok "подписка: «раз в сутки ⇄ вручную» — итог целиком" || bad "57: подписка auto" "$o"
o=$(printf '3\n\n0\n' | lib t_subs 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "✓ ${N_S}: без изменений" && t57_has "$o" '! Моя\cWW' && ok "подписка: «Обновить» — текст ядра и предупреждение текстом" || bad "57: подписка refresh" "$o"
o=$(printf '5\n1\n\n0\n' | lib t_subs 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "Удалить подписку «${N_S}»?" && t57_has "$o" "[OK] Подписка «${N_S}» удалена." \
  && ok "подписка: вопрос удаления и итог — целиком" || bad "57: подписка del" "$o"
o=$(printf '6\n\n\n0\n' | lib t_subs 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "Сейчас: «${N_S}»" && ok "подписка: «Сейчас: …» при переименовании — текстом" || bad "57: подписка rename" "$o"
t_restore() { K3_TERM_COLS=250; k3_exit_subs_load >/dev/null; k3_exit_sub_restore 0; }
o=$(lib t_restore 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "У подписки «${N_S}» исключённых ключей нет." && ok "подписка: «исключённых нет» — текстом" || bad "57: подписка restore" "$o"
gwf exits.json '{"ok": true, "exits": [{"id": "e-3fa9c1d2b4", "index": 1, "name": "Вена", "kind": "vless", "host": "198.51.100.31", "usable": true, "enabled": true}],
 "subs": [{"id": "s-aaaaaa", "n": 1, "domain": "sub\\cX.example.org", "stale": true, "text": "давно\\c не обновлялась", "error": ""}]}'
o=$(printf '0\n' | lib t_exits 2>&1 | strip)
t57_clean "$o" && t57_has "$o" '! Подписка sub\cX.example.org: давно\c не обновлялась' && ok "выходы: строка «подписка давно не обновлялась» — текстом" || bad "57: подписка stale" "$o"
# выдача настроек: шапка «Настройки: <устройство> — <протокол>» (k3_pg_msg — %b)
t_issue() { K3_TERM_COLS=250 TERM=dumb; k3_gw_issue_show '{"name": "Тел\\e]52;c;SGk=\\aD1", "kind": "wg", "os": "android", "items": []}' "" 6; }
o=$(lib t_issue </dev/null 2>&1 | strip)
t57_clean "$o" && t57_has "$o" "Настройки: ${N_D} — WireGuard" && ok "выдача: имя устройства в шапке — текстом" || bad "57: выдача" "$o"
# протокол: «Сейчас: …» с подписью; проброс (меню 5): «Сейчас: …»
gwf inbound_list.json "$(gw_list "wg awg3" | jq -c '(.inbounds[] | select(.kind == "wg") | .label) = "дом\\cL\\e]52;c;SGk=\\a"')"
t_proto() { K3_TEST_TTY=1 K3_TERM_COLS=250 TERM=dumb; k3_gw_protocols; }
o=$(printf '1\n4\n\n\n0\n0\n' | lib t_proto 2>&1 | strip)
t57_clean "$o" && t57_has "$o" 'WireGuard — дом\cL\e]52;c;SGk=\a' && t57_has "$o" 'Сейчас: «дом\cL\e]52;c;SGk=\a»' \
  && ok "протокол: подпись в шапке и «Сейчас: …» — текстом" || bad "57: подпись протокола" "$o"
engine_nft_fwds
jq '.forwards |= map(if .port == 443 then . + {"name": "Сайт\\cF%s"} else . end)' "$KASKAD_DIR/state.json" > "$W/st.t" && mv "$W/st.t" "$KASKAD_DIR/state.json"
printf 'tcp|443|2.2.2.2:443|0|0\n' > "$FAKE_DIR/fwd_list_traffic"
t_fwdlist() { list_active_rules; }
o=$(printf '1\n1\n\n0\n' | lib t_fwdlist 2>&1 | strip)
t57_clean "$o" && t57_has "$o" ':443 (tcp)' && t57_has "$o" '· Сайт\cF%s' && t57_has "$o" 'Сейчас: «Сайт\cF%s»' \
  && ok "меню 5: имя проброса в строке и «Сейчас: …» — текстом" || bad "57: проброс" "$o"

echo "── 57R. 3.3.7.1: старые имена с сырыми управляющими знаками (ESC, CSI U+009B, BEL, U+2028) — на экранах пробелом (REV-s1-t13-2 С-1а) ──"
# 3.3.5–3.3.7 и копии .kbk пропускали в имена C1 и ESC (clean_name снимал только C0 при записи); ядро 3.3.7.1 новые такие
# не примет, но старые остаются. Сторож класса: ни один экран 17 → 2/3, подписки, «Куда пойдёт трафик?», отказ ядра,
# группы — не отдаёт их в терминал как есть (k3_e, k3_term_esc, k3_term_filter). Проверка — по сырому выводу, до strip.
use_root rn57r; fake_ctl
gw_st true '{"wg": {}, "awg3": {}}' 2 2
R_U='Мама\u009b2J\u009b31mU1'; R_P='Папа\u001b[2JP2 X'; R_D='Тел\u001b]52;c;SGk=\u0007D1'; R_G='Стрим\u009b2J\u001b[2J'
gwf user_list.json '{"ok": true, "users": [{"id": "u-00000001", "name": "'"$R_U"'", "route": {"via": "group:g-1a2b3c", "fail": "block", "fail_eff": "block", "group_name": "'"$R_G"'"}, "enabled": true, "devices": 1},
 {"id": "u-00000002", "name": "'"$R_P"'", "route": null, "enabled": true, "devices": 0}]}'
gwf device_list.json '{"ok": true, "devices": [
 {"id": "d-00000001", "user": "u-00000001", "name": "'"$R_D"'", "kind": "phone", "platform": "android", "login": "tel-0001", "access": ["wg", "awg3"], "route": null, "enabled": true}]}'
gwf exits.json '{"ok": true, "exits": [{"id": "e-3fa9c1d2b4", "index": 1, "name": "Вена\u009b2J ZZ", "kind": "vless", "host": "198.51.100.31", "usable": true, "enabled": true, "group": "g-1a2b3c"}],
 "groups": [{"id": "main", "label": "Основная", "role": "abroad", "fail": "block"}, {"id": "g-1a2b3c", "label": "'"$R_G"'", "role": "abroad", "fail": "direct"}]}'
gwf groups.json '{"ok": true, "rev": 3, "groups": [], "no_exit": 0, "lines": ["Группа  Роль", "'"$R_G"'  зарубежная"]}'
gwf inbound_list.json "$(gw_list "wg awg3")"
jq '.gateway.users = [{"id": "u-00000001", "name": "Мама\u009b2J\u009b31mU1"}, {"id": "u-00000002", "name": "Папа\u001b[2JP2 X"}]
    | .gateway.devices = [{"id": "d-00000001", "user": "u-00000001", "name": "Тел\u001b]52;c;SGk=\u0007D1"}]
    | .gateway.exit_policy = {"groups": [{"id": "main", "name": ""}, {"id": "g-1a2b3c", "name": "Стрим\u009b2J\u001b[2J", "fail": "direct"}]}' \
    "$KASKAD_DIR/state.json" > "$W/st57r" && mv "$W/st57r" "$KASKAD_DIR/state.json"
# сырые: ESC[2J, ESC]52, CSI U+009B (C2 9B), OSC U+009D (C2 9D), BEL, U+2028 (E2 80 A8)
t57r_raw() { printf '%s' "$1" | LC_ALL=C grep -c $'\x1b\\[2J\\|\x1b\\]52\\|\xc2\x9b\\|\xc2\x9d\\|\a\\|\xe2\x80\xa8'; }
t57r() {   # $1 — что, $2 — вывод (сырой), $3 — что видно текстом (после strip)
    if [ "$(t57r_raw "$2")" = 0 ] && printf '%s' "$2" | strip | flat | grep -qF -- "$3"; then ok "57R $1: управляющие знаки имён — пробелом"
    else bad "57R $1" "raw=$(t57r_raw "$2") | $(printf '%s' "$2" | LC_ALL=C cat -v | head -c 1500)"; fi
}
t_people() { K3_TEST_TTY=1 K3_TERM_COLS=250 TERM=dumb; k3_gw_people; }
t57r "список пользователей" "$(printf '0\n' | lib t_people 2>&1)" "Мама 2J 31mU1"
t57r "удаление устройства" "$(printf '8\n1\n2\n\n0\n' | lib t_people 2>&1)" "Тел ]52;c;SGk= D1"
t57r "удаление пользователя" "$(printf '9\n2\n2\n\n0\n' | lib t_people 2>&1)" "Папа [2JP2 X"
t57r "маршрут устройства" "$(printf '6\n1\n0\n\n0\n' | lib t_people 2>&1)" "Тел ]52;c;SGk= D1"
t57r "маршрут пользователя" "$(printf '7\n1\n0\n\n0\n' | lib t_people 2>&1)" "Мама 2J 31mU1"
t57r "правила устройства" "$(printf '10\n1\n0\n0\n' | lib t_people 2>&1)" "Тел ]52;c;SGk= D1"
t57r "страница пользователя" "$(printf '12\n1\n0\n\n0\n' | lib t_people 2>&1)" "Мама 2J 31mU1"
t57r "протоколы устройства" "$(printf '4\n1\n0\n\n0\n' | lib t_people 2>&1)" "Тел ]52;c;SGk= D1"
t57r "переименование" "$(printf '13\n1\n\n\n0\n' | lib t_people 2>&1)" "Тел ]52;c;SGk= D1"
gwf user_add.json '{"ok": false, "error": "Пользователь «Мама\u009b2J\u001b[2J» уже есть.", "code": 3}'; gwf user_add.rc 3
t57r "отказ ядра (K3_GW_ERR)" "$(printf '3\nмама\n\n0\n' | lib t_people 2>&1)" "уже есть"
rm -f "$FAKE_DIR/gw/user_add.json" "$FAKE_DIR/gw/user_add.rc"
gwf device_route.json '{"ok": true}'; gwf device_route.err "@K3:warn Устройство «Тел$(printf '\033')[2J» ещё не подключалось."
t57r "предупреждение ядра (K3_GW_WARN)" "$(printf '6\n1\n2\n\n0\n' | lib t_people 2>&1)" "ещё не подключалось"
rm -f "$FAKE_DIR/gw/device_route.err"
t_trace() { K3_TEST_TTY=1 K3_TERM_COLS=250 TERM=dumb; k3_gw_trace_menu; }
gwf trace.json "$(printf '%s\n' '@K3:verdict exit' "Устройство «Тел$(printf '\033')]52;c;SGk=$(printf '\a')D1» → выход «Вена»")"
t57r "«Куда пойдёт трафик?»" "$(printf '1\n1\nexample.com\n0\n' | lib t_trace 2>&1)" "Тел ]52;c;SGk= D1"
t_exits() { K3_TEST_TTY=1 K3_TERM_COLS=250 TERM=dumb; egress_awg_runs() { return 0; }; k3_gw_exits_menu; }
t57r "таблица выходов" "$(printf '0\n' | lib t_exits 2>&1)" "Вена 2J ZZ"
t57r "группы выходов (17 → 3 → 6)" "$(printf '6\n\n0\n' | lib t_exits 2>&1)" "Стрим 2J [2J"
gwf exit.json '{"ok": true, "what": "exit", "id": "e-3fa9c1d2b4", "name": "", "shown": "VLESS\u009b2J\u001b[2J · 198.51.100.31", "note": ""}'
t57r "итог переименования (shown ядра)" "$(printf '7\n1\n-\n\n0\n' | lib t_exits 2>&1)" "VLESS 2J [2J · 198.51.100.31"
gwf exit-sub_list.json '{"ok": true, "subs": [{"id": "s-aaaaaa", "n": 1, "name": "Моя\u001b]52;c;SGk=\u0007", "label": "Моя\u001b]52;c;SGk=\u0007",
 "domain": "sub.example.org", "auto": true, "stale": false, "text": "обновлена", "excluded": 0, "last_ok": 1}]}'
t_subs() { K3_TEST_TTY=1 K3_TERM_COLS=250 TERM=dumb; k3_exit_subs_menu; }
t57r "подписки" "$(printf '0\n' | lib t_subs 2>&1)" "Моя ]52;c;SGk="
# SEC-3371 З-1: ещё три экрана со старыми или привезёнными из .kbk именами — подпись входа (таблица протоколов, ESC/BEL),
# маршрут человека «выход …» (карта имён выходов K3_GW_XNAME: C1 и U+2028), имя проброса (меню 5: C1); «\» — как есть
gwf inbound_list.json "$(gw_list "wg awg3" | jq -c '(.inbounds[] | select(.kind == "wg") | .label) = "Роутеры\u001b]52;c;SGk=\u0007\u009b2J\\d"')"
t_proto() { K3_TEST_TTY=1 K3_TERM_COLS=250 TERM=dumb; k3_gw_protocols; }
t57r "подпись входа (таблица протоколов)" "$(printf '0\n' | lib t_proto 2>&1)" '· Роутеры ]52;c;SGk= 2J\d'
gwf user_list.json '{"ok": true, "users": [{"id": "u-00000001", "name": "Мама", "route": {"via": "exit:e-3fa9c1d2b4", "fail": "block", "fail_eff": "block"}, "enabled": true, "devices": 0}]}'
gwf device_list.json '{"ok": true, "devices": [{"id": "d-00000001", "user": "u-00000001", "name": "Тел", "kind": "phone", "platform": "android", "login": "tel-0001", "access": ["wg"], "route": null, "enabled": true}]}'
gwf exits.json '{"ok": true, "exits": [{"id": "e-3fa9c1d2b4", "index": 1, "name": "Вена\u009d52;c;SGk=\u009c\u2028ZZ\u009b2J", "kind": "vless", "host": "198.51.100.31", "usable": true, "enabled": true}],
 "groups": [{"id": "main", "label": "Основная", "role": "abroad", "fail": "block"}]}'
t57r "маршрут устройства «как у пользователя: выход …» (имя выхода)" "$(printf '6\n1\n0\n\n0\n' | lib t_people 2>&1)" "Вена 52;c;SGk= ZZ 2J"
t57r "маршрут пользователя «выход …» (имя выхода)" "$(printf '7\n1\n0\n\n0\n' | lib t_people 2>&1)" "Вена 52;c;SGk= ZZ 2J"
# сама карта K3_GW_XNAME из k3_gw_people_load (экраны выше перечитывают имена выходов через k3_term_filter в выборе
# маршрута): «как у человека — сейчас: выход «…»» сразу после загрузки списка людей; маршрут человека — из state.json
jq '.gateway.users = [{"id": "u-00000001", "name": "Мама", "route": {"via": "exit:e-3fa9c1d2b4", "fail": "block"}}]
    | .gateway.devices = [{"id": "d-00000001", "user": "u-00000001", "name": "Тел"}]' "$KASKAD_DIR/state.json" > "$W/st57r" \
  && mv "$W/st57r" "$KASKAD_DIR/state.json"
t_xname() { K3_TEST_TTY=1 K3_TERM_COLS=250 TERM=dumb; k3_gw_people_load >/dev/null 2>&1; k3_gw_inherit_label device d-00000001; }
t57r "карта имён выходов k3_gw_people_load → «как у пользователя»" "$(lib t_xname 2>&1)" "сейчас: выход «Вена 52;c;SGk= ZZ 2J»"
engine_nft_fwds
jq '.forwards |= map(if .port == 443 then . + {"name": "Офис\u009b2J\u009d52;c;SGk=\u009c\u2028X\\n"} else . end)' "$KASKAD_DIR/state.json" > "$W/st57r" \
  && mv "$W/st57r" "$KASKAD_DIR/state.json"
printf 'tcp|443|2.2.2.2:443|0|0\n' > "$FAKE_DIR/fwd_list_traffic"
t57r "имя проброса (меню 5)" "$(printf '0\n' | lib t_fwdlist 2>&1)" '· Офис 2J 52;c;SGk= X\n'

echo ""
if [ "$FAILED" = "0" ]; then echo "  ВСЁ ЗЕЛЕНО ($PASSED)"; exit 0; fi
echo "  ЕСТЬ ПРОБЛЕМЫ"; exit 1

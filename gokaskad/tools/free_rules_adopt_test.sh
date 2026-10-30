#!/usr/bin/env bash
# free_rules_adopt_test.sh — pro берёт себе правила бесплатного Kaskad.
#
# Бесплатный Kaskad (github.com/potap1978/kaskad) ставит каскад без меток
# «kaskad:», и pro, поставленный поверх, их не видел: «Нет активных правил»,
# а правило pro на том же порту вставало после старого DNAT и не работало.
# adopt_free_kaskad_rules забирает такие наборы (DNAT + FORWARD к той же цели)
# и не трогает чужие DNAT. Проверяется на поддельном iptables: правила лежат в
# файлах в том виде, как их печатает `iptables -S`. Нужен только bash 4+.
#
# Usage: bash tools/free_rules_adopt_test.sh
set -u
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
mkdir -p "$W/bin" "$W/ipt"
FAILED=0
ok(){  printf '  PASS %s\n' "$1"; }
bad(){ printf '  FAIL %s\n' "$1"; FAILED=1; }

# Поддельный iptables: -t, -S/-A/-I/-D/-C по цепочке, правила — строки файла.
cat > "$W/bin/iptables" <<'MOCK'
#!/usr/bin/env bash
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
chmod +x "$W/bin/iptables"
export IPT_DIR="$W/ipt" PATH="$W/bin:$PATH"

seed() {
    rm -f "$W/ipt/"*
    # бесплатный Kaskad: tcp 18080 → 1.1.1.1:80 и udp 51820 → 5.5.5.5:51820
    cat > "$W/ipt/nat.PREROUTING" <<'R'
-m addrtype --dst-type LOCAL -j DOCKER
-p tcp -m tcp --dport 18080 -j DNAT --to-destination 1.1.1.1:80
-p udp -m udp --dport 51820 -j DNAT --to-destination 5.5.5.5:51820
-p tcp -m tcp --dport 2222 -j DNAT --to-destination 10.0.0.5:22
-p tcp -m tcp --dport 443 -m comment --comment kaskad:443:tcp -j DNAT --to-destination 2.2.2.2:443
R
    cat > "$W/ipt/filter.FORWARD" <<'R'
-d 1.1.1.1/32 -p tcp -m tcp --dport 80 -m state --state NEW,RELATED,ESTABLISHED -j ACCEPT
-s 1.1.1.1/32 -p tcp -m tcp --sport 80 -m state --state RELATED,ESTABLISHED -j ACCEPT
-d 5.5.5.5/32 -p udp -m udp --dport 51820 -m state --state NEW,RELATED,ESTABLISHED -j ACCEPT
-s 5.5.5.5/32 -p udp -m udp --sport 51820 -m state --state RELATED,ESTABLISHED -j ACCEPT
-d 2.2.2.2/32 -p tcp -m tcp --dport 443 -m state --state NEW,RELATED,ESTABLISHED -m comment --comment kaskad:443:tcp -j ACCEPT
R
    cat > "$W/ipt/filter.INPUT" <<'R'
-p tcp -m tcp --dport 22 -j ACCEPT
-p tcp -m tcp --dport 18080 -j ACCEPT
-p udp -m udp --dport 51820 -j ACCEPT
R
    printf '%s\n' '-o eth0 -j MASQUERADE' > "$W/ipt/nat.POSTROUTING"
}
run() {  # грузим только определения и зовём перенос, как это делает запуск pro
    ( source "$ROOT_DIR/install.sh" --lib >/dev/null 2>&1
      KASKAD_LOG="$W/kaskad.log"; IFACE=eth0; MY_IP=203.0.113.10
      adopt_free_kaskad_rules ) > "$W/out" 2>&1
}
nat() { cat "$W/ipt/nat.PREROUTING"; }
fwd() { cat "$W/ipt/filter.FORWARD"; }
inp() { cat "$W/ipt/filter.INPUT"; }

echo ""
echo "  free Kaskad rules adoption test"
seed; run
grep -q 'Правила бесплатного Kaskad: 2' "$W/out" && ok "найдено два набора бесплатной версии" || bad "не найдено: $(tail -3 "$W/out")"
nat | grep -q -- '--dport 18080 -m comment --comment kaskad:18080:tcp -j DNAT --to-destination 1.1.1.1:80' \
    && ok "tcp 18080 → 1.1.1.1:80 теперь с меткой pro" || bad "tcp 18080 не помечен: $(nat)"
nat | grep -q -- '--dport 51820 -m comment --comment kaskad:51820:udp -j DNAT --to-destination 5.5.5.5:51820' \
    && ok "udp 51820 → 5.5.5.5:51820 теперь с меткой pro" || bad "udp 51820 не помечен"
[ "$(nat | grep -v kaskad: | grep -c -- '-j DNAT')" = "1" ] && ok "без метки остался один DNAT" || bad "без метки: $(nat | grep -v kaskad:)"
nat | grep -v kaskad: | grep -q -- '--dport 2222 -j DNAT --to-destination 10.0.0.5:22' \
    && ok "чужой DNAT без пары FORWARD не тронут" || bad "чужой DNAT пропал"
nat | grep -q -- '-j DOCKER' && ok "переход в DOCKER не тронут" || bad "DOCKER пропал"
[ "$(nat | grep -c 'kaskad:443:tcp')" = "1" ] && ok "правило pro на 443 одно и прежнее" || bad "правило pro на 443 задето"
[ -z "$(fwd | grep -v kaskad: | grep -E '1\.1\.1\.1|5\.5\.5\.5')" ] && ok "FORWARD без меток к целям бесплатной версии снят" || bad "FORWARD без меток остался"
[ "$(fwd | grep -c 'kaskad:18080:tcp')" = "2" ] && [ "$(fwd | grep -c 'kaskad:51820:udp')" = "2" ] \
    && ok "пары FORWARD pro на месте" || bad "пары FORWARD pro: $(fwd)"
inp | grep -qx -- '-p tcp -m tcp --dport 22 -j ACCEPT' && ok "INPUT на 22 не тронут" || bad "INPUT 22 пропал"
[ -z "$(inp | grep -v kaskad: | grep -E -- '--dport (18080|51820) ')" ] && inp | grep -q 'kaskad:18080:tcp' \
    && ok "INPUT входных портов — с меткой pro" || bad "INPUT: $(inp)"
grep -q 'ADOPT free kaskad rules: 2 of 2' "$W/kaskad.log" && ok "записано в журнал" || bad "нет записи в журнале"
# Второй запуск — ничего нового: всё уже помечено.
before="$(nat; fwd; inp)"; run
[ "$(nat; fwd; inp)" = "$before" ] && ! grep -q 'Правила бесплатного Kaskad' "$W/out" \
    && ok "повторный запуск ничего не меняет" || bad "повторный запуск что-то сделал"
# Правило, вокруг которого нет пары FORWARD, — не наше.
seed; : > "$W/ipt/filter.FORWARD"; run
[ "$(nat | grep -c kaskad:)" = "1" ] && ok "без FORWARD ничего не берётся" || bad "взято без FORWARD: $(nat)"

echo ""
if [ "$FAILED" = "0" ]; then echo "  ВСЁ ЗЕЛЕНО"; exit 0; fi
echo "  ЕСТЬ ПРОБЛЕМЫ"; exit 1

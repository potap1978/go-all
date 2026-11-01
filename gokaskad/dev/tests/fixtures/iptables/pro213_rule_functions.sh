remove_rules_for_port() {
    local proto="$1" in_port="$2"
    iptables -t nat -S PREROUTING 2>/dev/null | grep "DNAT" | grep -- "kaskad:${in_port}:${proto}" | while read -r rule; do
        eval "iptables -t nat -D ${rule#-A }" 2>/dev/null
    done
    iptables -S INPUT 2>/dev/null | grep -- "kaskad:${in_port}:${proto}" | while read -r rule; do
        eval "iptables -D ${rule#-A }" 2>/dev/null
    done
    iptables -S FORWARD 2>/dev/null | grep -- "kaskad:${in_port}:${proto}" | while read -r rule; do
        eval "iptables -D ${rule#-A }" 2>/dev/null
    done
}
apply_iptables_rules() {
    local proto="$1" in_port="$2" out_port="$3" target_ip="$4" name="$5"
    echo -e "${YELLOW}[*] Применение правил...${NC}"
    log_action "ADD rule: $proto :$in_port -> $target_ip:$out_port ($name)"
    remove_rules_for_port "$proto" "$in_port"
    local rc=0
    iptables -I INPUT -p "$proto" --dport "$in_port" -m comment --comment "kaskad:${in_port}:${proto}" -j ACCEPT || rc=1
    iptables -t nat -A PREROUTING -p "$proto" --dport "$in_port" -m comment --comment "kaskad:${in_port}:${proto}" -j DNAT --to-destination "$target_ip:$out_port" || rc=1
    if ! iptables -t nat -C POSTROUTING -o "$IFACE" -m comment --comment "kaskad:masq" -j MASQUERADE 2>/dev/null; then
        iptables -t nat -A POSTROUTING -o "$IFACE" -m comment --comment "kaskad:masq" -j MASQUERADE || rc=1
    fi
    iptables -I FORWARD -p "$proto" -d "$target_ip" --dport "$out_port" -m state --state NEW,ESTABLISHED,RELATED -m comment --comment "kaskad:${in_port}:${proto}" -j ACCEPT || rc=1
    iptables -I FORWARD -p "$proto" -s "$target_ip" --sport "$out_port" -m state --state ESTABLISHED,RELATED -m comment --comment "kaskad:${in_port}:${proto}" -j ACCEPT || rc=1
    if [ "$rc" -ne 0 ]; then
        echo -e "${RED}[ERROR] iptables отклонил правило — откат.${NC}"
        remove_rules_for_port "$proto" "$in_port"
        remove_masquerade_if_unused
        log_action "ADD rule FAILED: $proto :$in_port -> $target_ip:$out_port"
        return 1
    fi
    if command -v ufw &>/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw allow "$in_port/$proto" > /dev/null 2>&1
    fi
    save_iptables
    echo -e "${GREEN}[SUCCESS] $name настроен!${NC}"
    echo -e "$proto: ${MY_IP:-*}:$in_port -> $target_ip:$out_port"
}

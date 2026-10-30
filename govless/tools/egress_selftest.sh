#!/usr/bin/env bash
# egress_selftest.sh — parser-level regression tests for the Telegram-API egress parser.
# Usage:  bash egress_selftest.sh [path/to/egress_parse.py]
# Generates SYNTHETIC fixtures (no real secrets), runs the parser, asserts expected output.
# Exit 0 = all pass. Safe to run anywhere with python3 (xray optional for the -test step).
set -u
P="${1:-egress_parse.py}"; [ -f "$P" ] || P="reference/egress_parse.py"
[ -f "$P" ] || { echo "egress_parse.py not found (pass its path as \$1)"; exit 2; }
PY(){ python3 "$P" "$@"; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0
ok(){ printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
no(){ printf '  \033[31mFAIL\033[0m %s\n     expected: %s\n     got:      %s\n' "$1" "$2" "$3"; fail=$((fail+1)); }
has(){ case "$2" in *"$1"*) return 0;; *) return 1;; esac; }

# ---------- generate fixtures ----------
python3 - "$TMP" <<'GEN'
import base64,json,zlib,struct,sys,os
T=sys.argv[1]
k=lambda s: base64.b64encode(bytes((s+i)%256 for i in range(32))).decode()
c15=f"""[Interface]
Address = 10.13.13.2/32
DNS = 1.1.1.1, 1.0.0.1
PrivateKey = {k(1)}
Jc = 4
Jmin = 40
Jmax = 70
S1 = 116
S2 = 61
H1 = 1234567801
H2 = 1234567802
H3 = 1234567803
H4 = 1234567804
[Peer]
PublicKey = {k(100)}
PresharedKey = {k(200)}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 198.51.100.10:51820
PersistentKeepalive = 25
"""
c20=f"""[Interface]
Address = 10.13.13.3/32
DNS = 1.1.1.1, 1.0.0.1
PrivateKey = {k(2)}
Jc = 6
Jmin = 50
Jmax = 1000
S1 = 44
S2 = 60
S3 = 37
S4 = 4
H1 = 1000000001-1500000001
H2 = 1500000002-2000000002
H3 = 2000000003-2500000003
H4 = 2500000004-3000000004
I1 = <b 0xf1c2...>
[Peer]
PublicKey = {k(101)}
PresharedKey = {k(201)}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 198.51.100.12:34472
PersistentKeepalive = 25
"""
def vpn(conf,name,host,params):
    last={"config":conf}; last.update(params)
    awg=dict(params); awg["last_config"]=json.dumps(last); awg["transport_proto"]="udp"
    j={"containers":[{"awg":awg,"container":"amnezia-awg"}],"defaultContainer":"amnezia-awg",
       "description":name,"dns1":"1.1.1.1","dns2":"1.0.0.1","hostName":host.split(":")[0]}
    raw=json.dumps(j).encode(); pay=struct.pack(">I",len(raw))+zlib.compress(raw)
    return "vpn://"+base64.urlsafe_b64encode(pay).decode().rstrip("=")
p15={"Jc":"4","Jmin":"40","Jmax":"70","S1":"116","S2":"61","H1":"1234567801","H2":"1234567802","H3":"1234567803","H4":"1234567804"}
p20={"Jc":"6","Jmin":"50","Jmax":"1000","S1":"44","S2":"60","S3":"37","S4":"4","H1":"1000000001-1500000001","H2":"1500000002-2000000002","H3":"2000000003-2500000003","H4":"2500000004-3000000004","I1":"<b 0xf1c2...>"}
w=lambda n,s: open(os.path.join(T,n),"w").write(s)
w("awg_v15.conf",c15); w("awg_v20.conf",c20)
w("awg_v15.vpn",vpn(c15,"test-awg-v15","198.51.100.11:41181",p15)+"\n")
w("awg_v20.vpn",vpn(c20,"test-awg-v20","198.51.100.12:34472",p20)+"\n")
vr="vless://00000000-0000-4000-8000-000000000001@192.0.2.10:443?type=tcp&security=reality&pbk=AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIIIJJJJKKK&fp=chrome&sni=gateway.icloud.com&sid=0123456789abcdef&flow=xtls-rprx-vision&spx=%2F#test-reality-tcp"
vw="vless://00000000-0000-4000-8000-000000000002@203.0.113.20:8443?type=ws&security=tls&host=cdn.example.com&path=%2Fws&sni=cdn.example.com#test-tls-ws"
w("vless_keys.txt",vr+"\n"+vw+"\n"); w("sub_b64.txt",base64.b64encode((vr+"\n"+vw+"\n").encode()).decode()+"\n")
w("awg_mangled.conf",c15.replace("\n"," ").replace("[Interface]","Interface]"))
w("junk.txt","hello\nnot a key\nvless//broken\n"); w("mixed.txt",vr+"\n"+open(os.path.join(T,"awg_v15.vpn")).read())
GEN

echo "── parser tests (synthetic fixtures) ──"
o=$(PY extract < "$TMP/awg_v20.vpn" | PY list); has "amnezia-2.0	udp" "$o" && ok "T2 vpn:// v2.0 → amnezia-2.0/udp" || no "T2" "amnezia-2.0/udp" "$o"
o=$(PY extract < "$TMP/awg_v15.conf" | PY list); has "amnezia-1.5	udp" "$o" && ok "T3 .conf v1.5 → amnezia-1.5/udp" || no "T3" "amnezia-1.5/udp" "$o"
o=$(PY extract < "$TMP/awg_mangled.conf" | PY list); has "amnezia-1.5" "$o" && ok "T4 mangled .conf still parses" || no "T4" "amnezia-1.5" "$o"
o=$(PY extract < "$TMP/vless_keys.txt" | PY list); { has "reality	tcp" "$o" && has "tls	ws" "$o"; } && ok "T5 VLESS reality+ws" || no "T5" "reality/tcp + tls/ws" "$o"
o=$(PY extract < "$TMP/sub_b64.txt" | PY list | grep -c .); [ "$o" = 2 ] && ok "T6 subscription(base64) → 2 keys" || no "T6" "2" "$o"
o=$(PY extract < "$TMP/junk.txt" | grep -c .); [ "$o" = 0 ] && ok "T7 junk → 0 keys" || no "T7" "0" "$o"
o=$(PY extract < "$TMP/mixed.txt" | PY list | grep -c .); [ "$o" = 2 ] && ok "T8 mixed vless+vpn paste → 2 keys" || no "T8" "2" "$o"
o=$(PY extract < "$TMP/awg_v20.vpn" | PY awgconf 1); { has "I1 = <b 0xf1c2...>" "$o" && has "S3 = 37" "$o"; } && ok "T9 awgconf v2.0 (I-packet + S3 preserved)" || no "T9" "I1/S3 present" "$o"
o=$(PY extract < "$TMP/awg_v20.vpn" | PY awglist); has "1	kawg1	257	257" "$o" && ok "T10 awglist mark/table=256+idx" || no "T10" "1 kawg1 257 257" "$o"

echo "── config generation tests ──"
o=$(head -1 "$TMP/vless_keys.txt" | PY config --mss 1000 2>/dev/null | python3 -c 'import json,sys;c=json.load(sys.stdin);print("svc",c["api"]["services"]);print("bal","balancers" in c["routing"]);print("mss",c["outbounds"][0]["streamSettings"]["sockopt"].get("tcpMaxSeg"))')
{ has "svc ['StatsService']" "$o" && has "bal False" "$o" && has "mss 1000" "$o"; } && ok "T11 single key → direct, StatsService, tcpMaxSeg=1000" || no "T11" "Stats/direct/1000" "$o"
cat <(head -1 "$TMP/vless_keys.txt") "$TMP/awg_v20.vpn" | PY extract > "$TMP/mix.txt"
o=$(PY config --mss 1000 < "$TMP/mix.txt" 2>/dev/null | python3 -c 'import json,sys;c=json.load(sys.stdin);b=c["routing"]["balancers"][0];print("obs",c["observatory"]["probeURL"]);print("strat",b["strategy"]["type"]);awg=[o for o in c["outbounds"] if o["tag"].startswith("out_") and o["protocol"]=="freedom"][0];print("mark",awg["streamSettings"]["sockopt"]["mark"],awg["streamSettings"]["sockopt"]["tcpMaxSeg"])')
{ has "obs http://" "$o" && has "strat leastPing" "$o" && has "mark 257 1160" "$o"; } && ok "T12 mixed → balancer+observatory, AWG freedom+mark 257/1160" || no "T12" "balancer/obs/mark" "$o"

if command -v xray >/dev/null 2>&1; then
  PY config --mss 1000 < "$TMP/mix.txt" > "$TMP/mix.json" 2>/dev/null
  xray -test -c "$TMP/mix.json" >/dev/null 2>&1 && ok "T13 xray -test mixed config" || no "T13" "xray -test ok" "see: xray -test -c $TMP/mix.json"
else
  echo "  SKIP T13 (xray not installed) — run on target: xray -test -c config.json"
fi

echo "────────────────────────────"
echo "  PASS=$pass  FAIL=$fail"
[ "$fail" = 0 ]

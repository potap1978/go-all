#!/usr/bin/env bash
# goVLESS — Telegram-API egress library: unified VLESS + AmneziaWG pool.
# Real working implementation: config vars, embedded python parser (gated by
# PARSER_VER), dedicated-xray bootstrap, AmneziaWG plumbing (kawg<i>+fwmark),
# egress_rebuild, import, manual/auto selection, loss sampler, htop-style TUI.
# App helpers used: log_*, gv_fetch, gv_step, t(), config helpers, bot.env
# (BOT_PROXY), GH_TOKEN, govless-bot restart hook.
# Entry modes (main): --egress-probe / --egress-awg-up / --egress-awg-down.

#  TELEGRAM API EGRESS — VLESS via dedicated xray ("Прокси для API Telegram")
# ═══════════════════════════════════════════════════════════════
EGRESS_DIR="${EGRESS_DIR:-/etc/govless/egress}"
EGRESS_XRAY="$EGRESS_DIR/xray"
EGRESS_UP="$EGRESS_DIR/upstream.txt"
EGRESS_CFG="$EGRESS_DIR/config.json"
EGRESS_PARSE_PY="$EGRESS_DIR/egress_parse.py"
EGRESS_SERVICE="govless-egress"
BOT_ENV="${BOT_ENV:-/etc/govless/bot.env}"
EGRESS_SOCKS_PORT="${EGRESS_SOCKS_PORT:-10808}"
EGRESS_API_PORT="${EGRESS_API_PORT:-10851}"
EGRESS_MAX="${EGRESS_MAX:-100}"
EGRESS_MSS="${EGRESS_MSS:-1000}"  # clamp egress MSS (tcpMaxSeg): fixes low-MTU paths where large Reality/Vision segments are dropped. 0 = no clamp.
EGRESS_LOSS="$EGRESS_DIR/loss.log"
EGRESS_PROBE_SVC="govless-egress-probe"
AWG_DIR="$EGRESS_DIR/awg"
AWG_GO="$AWG_DIR/amneziawg-go"
AWG_TOOL="$AWG_DIR/awg"
AWG_REPO="${AWG_REPO:-potap1978/go-all}"
# Observatory health-probe MUST be plain HTTP: xray's internal HTTPS probe can
# fail with "tls: CurvePreferences includes unsupported curve" on go1.26+ builds,
# which would mark every (working) upstream dead. A plain-HTTP 204 avoids it.
EGRESS_OBSPROBE="${EGRESS_OBSPROBE:-http://cp.cloudflare.com/generate_204}"
EGRESS_TESTURL="${EGRESS_TESTURL:-https://api.telegram.org/}"
# Dedicated egress xray (independent of any panel binary). If GitHub "latest"
# (stable, no pre-releases) can't be queried, fall back to a known-good STABLE
# build — some pre-releases break OUTBOUND TLS (the curve bug noted above).
EGRESS_XVER_FALLBACK="${EGRESS_XVER_FALLBACK:-v26.3.27}"
BOLD='\033[1m'; DIM='\033[2m'

# Count non-blank keys as ONE clean integer (grep -c prints "0" AND exits 1 on
# an empty file, so a bare `|| echo 0` yields "0\n0" and breaks integer tests).
_egr_count() { local n; n=$(grep -c . "$EGRESS_UP" 2>/dev/null); echo "${n:-0}"; }

# ── Write the embedded VLESS->xray parser to disk (single-file app) ──────────
egress_ensure_parser() {
    mkdir -p "$EGRESS_DIR"; chmod 700 "$EGRESS_DIR" 2>/dev/null
    [ -f "$EGRESS_PARSE_PY" ] && grep -q 'PARSER_VER=6' "$EGRESS_PARSE_PY" 2>/dev/null && return 0
    cat > "$EGRESS_PARSE_PY" <<'PYEOF'
#!/usr/bin/env python3
# PARSER_VER=6 — goVLESS unified egress parser: VLESS + AmneziaWG in one pool.
# Inputs: vless:// , vless subscription (base64), AWG vpn:// (Amnezia), AWG .conf (v1.5/v2.0).
# Pool storage: one line per key — "vless://..." OR "awg://<base64(json record)>".
import sys, json, base64, argparse, urllib.parse, re

MAX_KEYS = 100
AWG_IFACE_KEYS = ["Jc","Jmin","Jmax","S1","S2","S3","S4","H1","H2","H3","H4","I1","I2","I3","I4","I5"]
AWG_MARK_BASE = 256   # xray sockopt mark / route table = AWG_MARK_BASE + index

# ───────────────────────── helpers ─────────────────────────
def _b64e(s): return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")
def _b64d(s): return base64.urlsafe_b64decode(s + "="*(-len(s) % 4)).decode("utf-8","replace")

def _maybe_b64_vless(text):
    t = "".join(text.split())
    if "vless://" in text: return text
    for dec in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            out = dec(t + "="*(-len(t)%4)).decode("utf-8","ignore")
            if "vless://" in out or "vpn://" in out or "[Interface]" in out: return out
        except Exception: pass
    return text

def _q1(qs, key, default=""):
    v = qs.get(key); return v[0] if v else default

# ───────────────────────── VLESS ─────────────────────────
def vless_outbound(link, tag, mss=0):
    try:
        u = urllib.parse.urlparse(link)
        if u.scheme != "vless": return None
        uuid = urllib.parse.unquote(u.username or ""); host = u.hostname or ""
        port = 443 if u.port is None else u.port
        if not uuid or not host or port<1 or port>65535: return None
        qs = urllib.parse.parse_qs(u.query)
        net = _q1(qs,"type","tcp") or "tcp";  net = "tcp" if net=="raw" else net
        sec = _q1(qs,"security","none") or "none"
        if net in ("http","h2"): return None
        if sec=="reality" and net not in ("tcp","grpc","xhttp"): return None
        sni=_q1(qs,"sni") or _q1(qs,"host") or host; fp=_q1(qs,"fp","chrome") or "chrome"
        flow=_q1(qs,"flow",""); alpn=_q1(qs,"alpn","")
        user={"id":uuid,"encryption":"none"}
        if flow and net=="tcp": user["flow"]=flow
        stream={"network":net,"security":sec}
        if sec=="reality":
            stream["realitySettings"]={"serverName":sni,"fingerprint":fp,"publicKey":_q1(qs,"pbk"),"shortId":_q1(qs,"sid"),"spiderX":_q1(qs,"spx","/") or "/"}
        elif sec=="tls":
            tls={"serverName":sni,"fingerprint":fp}
            if _q1(qs,"allowInsecure","0") in ("1","true"): tls["allowInsecure"]=True
            if alpn: tls["alpn"]=[a for a in alpn.replace("%2C",",").split(",") if a]
            stream["tlsSettings"]=tls
        if net=="tcp": stream["tcpSettings"]={"header":{"type":"none"}}
        elif net=="grpc": stream["grpcSettings"]={"serviceName":_q1(qs,"serviceName"),"multiMode":_q1(qs,"mode")=="multi"}
        elif net=="ws": stream["wsSettings"]={"path":_q1(qs,"path","/") or "/","headers":{"Host":_q1(qs,"host") or sni}}
        elif net in ("xhttp","splithttp"):
            stream["network"]="xhttp"; stream["xhttpSettings"]={"path":_q1(qs,"path","/") or "/","host":_q1(qs,"host") or sni,"mode":_q1(qs,"mode","auto") or "auto"}
        stream["sockopt"]={"domainStrategy":"ForceIPv4","tcpKeepAliveIdle":100,"tcpKeepAliveInterval":15,"tcpNoDelay":True}
        if mss and mss>0: stream["sockopt"]["tcpMaxSeg"]=mss
        return {"tag":tag,"protocol":"vless","settings":{"vnext":[{"address":host,"port":port,"users":[user]}]},"streamSettings":stream}
    except Exception as e:
        sys.stderr.write("skip vless (%s)\n"%e); return None

def vless_label(link):
    try:
        u=urllib.parse.urlparse(link); host=u.hostname or "?"; port=u.port or 443
        qs=urllib.parse.parse_qs(u.query); name=urllib.parse.unquote(u.fragment) if u.fragment else ""
        return {"name":name or ("%s:%s"%(host,port)),"host":"%s:%s"%(host,port),"sec":_q1(qs,"security","none") or "none","net":_q1(qs,"type","tcp") or "tcp"}
    except Exception: return {"name":"?","host":"?","sec":"?","net":"?"}

# ───────────────────────── AmneziaWG ─────────────────────────
def awg_from_vpn(blob):
    s = blob[len("vpn://"):] if blob.startswith("vpn://") else blob
    raw = base64.urlsafe_b64decode(s + "="*(-len(s)%4))
    data=None
    for skip in (4,0):
        try: data=__import__("zlib").decompress(raw[skip:]); break
        except Exception: pass
    if data is None: raise ValueError("vpn decompress")
    j=json.loads(data.decode("utf-8","replace")); name=j.get("description") or "amnezia"
    awg=None
    for c in j.get("containers",[]):
        if isinstance(c,dict) and "awg" in c: awg=c["awg"]; break
    if not awg: raise ValueError("no awg container")
    lc=awg.get("last_config"); conf=""; params={}
    if lc:
        lj=json.loads(lc); conf=lj.get("config","")
        for k in AWG_IFACE_KEYS:
            if lj.get(k): params[k]=lj[k]
        conf=conf.replace("$PRIMARY_DNS",j.get("dns1","1.1.1.1")).replace("$SECONDARY_DNS",j.get("dns2","1.0.0.1"))
    rec=awg_from_conf(conf,name)
    for k,v in params.items(): rec["params"][k]=v
    return rec

def awg_from_conf(text,name=None):
    # Tolerate a paste whose newlines collapsed to spaces and/or lost the section
    # brackets (common over SSH/terminal): re-insert breaks before headers & keys.
    t=re.sub(r"\[?\s*(Interface|Peer)\s*\]", lambda m: "\n["+m.group(1).capitalize()+"]\n", text, flags=re.I)
    _wgk="Address|DNS|PrivateKey|PublicKey|PresharedKey|AllowedIPs|Endpoint|PersistentKeepalive|ListenPort|MTU|Jc|Jmin|Jmax|S1|S2|S3|S4|H1|H2|H3|H4|I1|I2|I3|I4|I5"
    t=re.sub(r"[ \t]+(?=(?:"+_wgk+r")[ \t]*=)", "\n", t)
    rec={"t":"awg","name":name or "amneziawg","address":"","private_key":"","mtu":"1376","params":{},
         "peer":{"public_key":"","preshared_key":"","allowed_ips":"0.0.0.0/0","endpoint":"","keepalive":"25"}}
    sec=None
    for line in t.splitlines():
        line=line.strip()
        if not line or line.startswith("#"): continue
        ll=line.lower()
        if ll.startswith("[interface]"): sec="i"; continue
        if ll.startswith("[peer]"): sec="p"; continue
        if "=" not in line: continue
        k,_,v=line.partition("="); k=k.strip(); v=v.strip(); kl=k.lower()
        if sec=="i":
            if kl=="address": rec["address"]=v.split(",")[0].strip()
            elif kl=="privatekey": rec["private_key"]=v
            elif kl=="mtu": rec["mtu"]=v
            elif k in AWG_IFACE_KEYS and v: rec["params"][k]=v
        elif sec=="p":
            if kl=="publickey": rec["peer"]["public_key"]=v
            elif kl=="presharedkey": rec["peer"]["preshared_key"]=v
            elif kl=="allowedips": rec["peer"]["allowed_ips"]=v
            elif kl=="endpoint": rec["peer"]["endpoint"]=v
            elif kl=="persistentkeepalive": rec["peer"]["keepalive"]=v
    if not rec["private_key"] or not rec["peer"]["endpoint"]: raise ValueError("awg conf missing key/endpoint")
    return rec

def awg_version(rec):
    p=rec["params"]
    return "2.0" if (p.get("S3") or p.get("S4") or any(p.get("I%d"%i) for i in range(1,6))) else "1.5"

def awg_setconf(rec):
    p=rec["params"]; out=["[Interface]","PrivateKey = %s"%rec["private_key"]]
    for k in AWG_IFACE_KEYS:
        if p.get(k): out.append("%s = %s"%(k,p[k]))
    pe=rec["peer"]; allowed=pe["allowed_ips"].split(",")[0].strip() or "0.0.0.0/0"
    out+=["","[Peer]","PublicKey = %s"%pe["public_key"]]
    if pe["preshared_key"]: out.append("PresharedKey = %s"%pe["preshared_key"])
    out+=["AllowedIPs = %s"%allowed,"Endpoint = %s"%pe["endpoint"],"PersistentKeepalive = %s"%(pe["keepalive"] or "25")]
    return "\n".join(out)

def awg_outbound(rec, tag, idx):
    return {"tag":tag,"protocol":"freedom","settings":{"domainStrategy":"UseIPv4"},
            "streamSettings":{"sockopt":{"mark":AWG_MARK_BASE+idx,"tcpMaxSeg":1160,"tcpNoDelay":True}}}

# ───────────────────────── extract (any input -> pool lines) ─────────────────────────
def extract(raw):
    raw=_maybe_b64_vless(raw); seen=[]; out=[]
    def add(x):
        if x and x not in seen: seen.append(x); out.append(x)
    # AWG .conf (newline- or space-collapsed; tolerate mangled brackets)
    if re.search(r'PrivateKey\s*=', raw, re.I) and re.search(r'Endpoint\s*=', raw, re.I):
        parts=re.split(r'(?=\[?\s*Interface\s*\])', raw, flags=re.I)
        parts=[x for x in parts if re.search(r'PrivateKey\s*=', x, re.I) and re.search(r'Endpoint\s*=', x, re.I)] or [raw]
        for b in parts:
            try: add("awg://"+_b64e(json.dumps(awg_from_conf(b))))
            except Exception as e: sys.stderr.write("skip conf (%s)\n"%e)
    # AWG vpn:// tokens
    for tok in re.findall(r'vpn://[A-Za-z0-9_\-+/=]+', raw):
        try: add("awg://"+_b64e(json.dumps(awg_from_vpn(tok))))
        except Exception as e: sys.stderr.write("skip vpn (%s)\n"%e)
    # VLESS lines
    for line in raw.replace("\r","\n").split("\n"):
        line=line.strip()
        if line.startswith("vless://"): add(line)
    return out[:MAX_KEYS]

def pool_label(line):
    if line.startswith("awg://"):
        try:
            r=json.loads(_b64d(line[6:]))
            return {"name":r.get("name","awg"),"host":r["peer"]["endpoint"],"sec":"amnezia-"+awg_version(r),"net":"udp"}
        except Exception:
            return {"name":"awg(bad)","host":"?","sec":"amnezia","net":"udp"}
    return vless_label(line)

# ───────────────────────── config (mixed xray) ─────────────────────────
def build_config(lines, socks_port, probe_url, strategy, probe_interval, max_keys, api_port, mss):
    outbounds=[]; tags=[]
    for i,line in enumerate(lines[:max_keys], start=1):
        tag="out_%d"%i
        if line.startswith("awg://"):
            try: ob=awg_outbound(json.loads(_b64d(line[6:])),tag,i)
            except Exception as e: sys.stderr.write("skip awg (%s)\n"%e); ob=None
        else:
            ob=vless_outbound(line,tag,mss)
        if ob: outbounds.append(ob); tags.append(tag)
    if not outbounds: sys.stderr.write("no valid keys\n"); sys.exit(2)
    outbounds.append({"tag":"direct","protocol":"freedom","settings":{"domainStrategy":"UseIPv4"}})
    outbounds.append({"tag":"block","protocol":"blackhole"})
    api_services=["RoutingService","ObservatoryService","StatsService"] if len(tags)>1 else ["StatsService"]
    cfg={"log":{"loglevel":"warning"},"stats":{},"api":{"tag":"api","services":api_services},
         "inbounds":[{"tag":"socks-in","listen":"127.0.0.1","port":socks_port,"protocol":"socks","settings":{"udp":True,"auth":"noauth"},"sniffing":{"enabled":False}},
                     {"tag":"api-in","listen":"127.0.0.1","port":api_port,"protocol":"dokodemo-door","settings":{"address":"127.0.0.1"}}],
         "outbounds":outbounds,
         "policy":{"levels":{"0":{"connIdle":300,"handshake":4,"uplinkOnly":2,"downlinkOnly":5}},"system":{"statsInboundUplink":True,"statsInboundDownlink":True}}}
    routing={"domainStrategy":"AsIs","rules":[{"type":"field","inboundTag":["api-in"],"outboundTag":"api"}]}
    if len(tags)<=1:
        routing["rules"].append({"type":"field","inboundTag":["socks-in"],"outboundTag":tags[0]})
    else:
        cfg["observatory"]={"subjectSelector":["out_"],"probeURL":probe_url,"probeInterval":probe_interval}
        routing["balancers"]=[{"tag":"bot-bal","selector":["out_"],"strategy":{"type":strategy},"fallbackTag":tags[0]}]
        routing["rules"].append({"type":"field","inboundTag":["socks-in"],"balancerTag":"bot-bal"})
    cfg["routing"]=routing
    return cfg, len(tags)

# ───────────────────────── main ─────────────────────────
def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest="cmd",required=True)
    sub.add_parser("extract"); sub.add_parser("list")
    al=sub.add_parser("awglist")
    ac=sub.add_parser("awgconf"); ac.add_argument("idx",type=int)
    c=sub.add_parser("config")
    c.add_argument("--socks-port",type=int,default=10808); c.add_argument("--api-port",type=int,default=10851)
    c.add_argument("--probe",default="http://cp.cloudflare.com/generate_204"); c.add_argument("--strategy",default="leastPing")
    c.add_argument("--probe-interval",default="10s"); c.add_argument("--max",type=int,default=MAX_KEYS); c.add_argument("--mss",type=int,default=0)
    a=ap.parse_args(); raw=sys.stdin.read()
    if a.cmd=="extract":
        for l in extract(raw): print(l)
    elif a.cmd=="list":
        for l in [x for x in raw.splitlines() if x.strip()]:
            d=pool_label(l.strip()); print("%s\t%s\t%s\t%s"%(d["name"],d["host"],d["sec"],d["net"]))
    elif a.cmd=="awglist":
        i=0
        for l in [x for x in raw.splitlines() if x.strip()]:
            i+=1; l=l.strip()
            if l.startswith("awg://"):
                try: r=json.loads(_b64d(l[6:]))
                except Exception: continue
                host=r["peer"]["endpoint"].rsplit(":",1)[0].strip("[]")
                print("%d\tkawg%d\t%d\t%d\t%s\t%s"%(i,i,AWG_MARK_BASE+i,AWG_MARK_BASE+i,r["address"] or "10.8.0.2/32",host))
    elif a.cmd=="awgconf":
        i=0
        for l in [x for x in raw.splitlines() if x.strip()]:
            i+=1
            if i==a.idx and l.strip().startswith("awg://"):
                try: print(awg_setconf(json.loads(_b64d(l.strip()[6:])))); return
                except Exception as e: sys.stderr.write("awgconf bad (%s)\n"%e); sys.exit(3)
        sys.exit(3)
    else:
        lines=[x.strip() for x in raw.splitlines() if x.strip()]  # config consumes normalized pool lines verbatim
        cfg,n=build_config(lines,a.socks_port,a.probe,a.strategy,a.probe_interval,a.max,a.api_port,a.mss)
        sys.stderr.write("parsed %d key(s)\n"%n); print(json.dumps(cfg,indent=2))

if __name__=="__main__": main()
PYEOF
    chmod 600 "$EGRESS_PARSE_PY" 2>/dev/null
}

# ── Locate an xray binary (prefer our dedicated one) ────────────────────────
egress_xray_bin() {
    [ -x "$EGRESS_XRAY" ] && { echo "$EGRESS_XRAY"; return 0; }
    local c
    for c in /usr/local/bin/xray /usr/bin/xray $(ls /usr/local/x-ui/bin/xray-* 2>/dev/null); do
        [ -x "$c" ] && { echo "$c"; return 0; }
    done
    return 1
}

# ── Ensure a known-good dedicated egress xray (independent of any panel) ─────
egress_ensure_xray() {
    [ -x "$EGRESS_XRAY" ] && return 0
    mkdir -p "$EGRESS_DIR"; chmod 700 "$EGRESS_DIR" 2>/dev/null
    local z
    case "$(uname -m)" in
        x86_64|amd64)  z="Xray-linux-64.zip" ;;
        aarch64|arm64) z="Xray-linux-arm64-v8a.zip" ;;
        armv7l)        z="Xray-linux-arm32-v7a.zip" ;;
        *)             z="Xray-linux-64.zip" ;;
    esac
    # GitHub API отдаёт JSON и с переносами строк, и одной строкой. На одной строке grep -m1 | cut -f4
    # возвращал значение первого ключа ("url"): версия непустая, запасная не включалась, xray не скачивался.
    # Поэтому tag_name берём разбором JSON (python3; нет его или не вышло — sed) и верим только vX.Y.Z.
    local ver js re='^v[0-9]+\.[0-9]+\.[0-9]+$'
    js=$(curl -fsSL --connect-timeout 15 --max-time 30 \
        https://api.github.com/repos/XTLS/Xray-core/releases/latest 2>/dev/null) || js=""
    ver=$(printf '%s' "$js" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])' 2>/dev/null) || ver=""
    [ -n "$ver" ] || ver=$(printf '%s' "$js" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1) || ver=""
    [[ "$ver" =~ $re ]] || ver="$EGRESS_XVER_FALLBACK"
    echo -e "${YELLOW}[*] $(tf egr_xray_dl "$ver")${NC}"
    local zf="$EGRESS_DIR/.xray.zip"
    curl -fsSL --retry 3 --connect-timeout 15 --max-time 120 \
        -o "$zf" "https://github.com/XTLS/Xray-core/releases/download/${ver}/${z}" \
        || { echo -e "${RED}$(t egr_xray_dl_fail)${NC}"; rm -f "$zf"; return 1; }
    # Extract to a temp dir, then mv — overwriting a *running* binary in place
    # fails with "Text file busy".
    rm -rf "$EGRESS_DIR/.x"; mkdir -p "$EGRESS_DIR/.x"
    if command -v unzip >/dev/null 2>&1; then
        unzip -oq "$zf" xray -d "$EGRESS_DIR/.x" 2>/dev/null
    else
        python3 -c "import zipfile; zipfile.ZipFile('$zf').extract('xray','$EGRESS_DIR/.x')" 2>/dev/null
    fi
    mv -f "$EGRESS_DIR/.x/xray" "$EGRESS_XRAY" 2>/dev/null
    chmod +x "$EGRESS_XRAY" 2>/dev/null
    rm -rf "$EGRESS_DIR/.x" "$zf"
    [ -x "$EGRESS_XRAY" ]
}

# ── Which upstream the balancer is currently using (1-based index) ──────────
egress_active_index() {
    local xray; xray=$(egress_xray_bin) || return 1
    systemctl is-active --quiet "$EGRESS_SERVICE" 2>/dev/null || return 1
    [ "$(_egr_count)" -le 1 ] && { echo 1; return 0; }   # single key -> no balancer, it is #1
    "$xray" api bi --server="127.0.0.1:${EGRESS_API_PORT}" -timeout 3 bot-bal 2>/dev/null \
        | awk '/Selecting Override:/{getline; if(match($0,/out_[0-9]+/)){s=substr($0,RSTART,RLENGTH); gsub(/[^0-9]/,"",s); if(s!=""){print s; exit}} next}
               /Selects:/{getline; if(match($0,/out_[0-9]+/)){s=substr($0,RSTART,RLENGTH); gsub(/[^0-9]/,"",s); print s; exit}}'
}

# ── BOT_PROXY lives in /etc/govless/bot.env (govless-bot reads it via aiohttp-socks) ──
egress_set_bot_proxy() {
    local val="$1"; touch "$BOT_ENV" 2>/dev/null
    if grep -qE '^BOT_PROXY=' "$BOT_ENV" 2>/dev/null; then sed -i "s#^BOT_PROXY=.*#BOT_PROXY=${val}#" "$BOT_ENV"; else echo "BOT_PROXY=${val}" >> "$BOT_ENV"; fi
}

# ── Write + enable the egress systemd service ───────────────────────────────
egress_write_service() {
    local xray; xray=$(egress_xray_bin) || { echo -e "${RED}$(t egr_no_xray)${NC}"; return 1; }
    cat > "/etc/systemd/system/${EGRESS_SERVICE}.service" <<UNIT
[Unit]
Description=goVLESS Telegram API egress (xray client, failover SOCKS5)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0
[Service]
Type=simple
ExecStart=${xray} run -c ${EGRESS_CFG}
Restart=always
RestartSec=5
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable "${EGRESS_SERVICE}" >/dev/null 2>&1 || true
    systemctl restart "${EGRESS_SERVICE}"
    egress_probe_install
}

# ── AmneziaWG support: per-key amneziawg-go interface + fwmark route ─────────
awg_go_bin()  { [ -x "$AWG_GO" ] && { echo "$AWG_GO"; return 0; }; [ -x /usr/local/bin/amneziawg-go ] && { echo /usr/local/bin/amneziawg-go; return 0; }; return 1; }
awg_tool_bin(){ [ -x "$AWG_TOOL" ] && { echo "$AWG_TOOL"; return 0; }; [ -x /usr/local/bin/awg ] && { echo /usr/local/bin/awg; return 0; }; return 1; }
egress_pool_has_awg() { grep -q '^awg://' "$EGRESS_UP" 2>/dev/null; }

# Бинарники ядра AmneziaWG лежат в bin/ того же клона goVLESS, из которого
# запущен скрипт. Ищем их рядом со скриптом, затем в штатных местах установки.
egress_awg_copy_from_checkout() {
    local arch="$1" root copied=0
    # AWG_CHECKOUT_ROOTS переопределяет список мест — нужно только тестам, чтобы
    # на живом сервере сымитировать «ядра нет нигде» (иначе его найдёт /opt).
    local -a roots
    if [ -n "${AWG_CHECKOUT_ROOTS+x}" ]; then read -r -a roots <<<"$AWG_CHECKOUT_ROOTS"
    else roots=("${SCRIPT_DIR:-}" /opt/govless-installer "${HOME:-/root}/goVLESS"); fi
    for root in "${roots[@]}"; do
        [ -n "$root" ] && [ -d "$root/bin" ] || continue
        if ! awg_go_bin >/dev/null && [ -s "$root/bin/amneziawg-go-linux-$arch" ]; then
            cp -f "$root/bin/amneziawg-go-linux-$arch" "$AWG_GO" 2>/dev/null \
                && chmod +x "$AWG_GO" 2>/dev/null && copied=1
        fi
        if ! awg_tool_bin >/dev/null && [ -s "$root/bin/awg-linux-$arch" ]; then
            cp -f "$root/bin/awg-linux-$arch" "$AWG_TOOL" 2>/dev/null \
                && chmod +x "$AWG_TOOL" 2>/dev/null && copied=1
        fi
    done
    [ "$copied" = "1" ]
}

egress_ensure_awg() {
    awg_go_bin >/dev/null && awg_tool_bin >/dev/null && return 0
    mkdir -p "$AWG_DIR"; chmod 700 "$AWG_DIR" 2>/dev/null
    local arch tok ref base; local hdr=()
    # AWG_ARCH — только для тестов на машине разработчика (macOS arm64 без arm64-сборки в bin/).
    case "${AWG_ARCH:-$(uname -m)}" in x86_64|amd64) arch=amd64;; aarch64|arm64) arch=arm64;; *) arch=amd64;; esac
    echo -e "${YELLOW}[*] $(t egr_awg_dl)${NC}"

    # СНАЧАЛА локальная копия. Оба бинарника уже лежат в bin/ этого же клона —
    # по сети за ними ходить незачем. Раньше единственным путём был curl на
    # raw.githubusercontent за ПРИВАТНЫМ репозиторием: без GH_TOKEN в окружении
    # он отдаёт 404, а в окружении его нет ни у кого, кто открыл меню ярлыком
    # govless (токен живёт только внутри запуска bootstrap.sh). Отсюда и
    # «Не удалось загрузить ядро AmneziaWG» у всех подряд.
    if egress_awg_copy_from_checkout "$arch" && awg_go_bin >/dev/null && awg_tool_bin >/dev/null; then
        echo -e "  ${GREEN}✓${NC} $(t egr_awg_local)"
        return 0
    fi

    # Запасной путь — сеть. Нужен, только если в чекауте нет bin/ (частичная
    # выкладка, ручная сборка). Приватный репозиторий, поэтому без токена мимо.
    tok="${GH_TOKEN:-}"
    [ -n "$tok" ] && hdr=(-H "Authorization: token $tok")
    for ref in dev main test; do
        base="https://raw.githubusercontent.com/${AWG_REPO}/${ref}/bin"
        if curl -fsSL "${hdr[@]}" --max-time 90 -o "$AWG_GO" "$base/amneziawg-go-linux-$arch" 2>/dev/null && [ -s "$AWG_GO" ]; then
            curl -fsSL "${hdr[@]}" --max-time 60 -o "$AWG_TOOL" "$base/awg-linux-$arch" 2>/dev/null
            break
        fi
    done
    chmod +x "$AWG_GO" "$AWG_TOOL" 2>/dev/null
    awg_go_bin >/dev/null && awg_tool_bin >/dev/null && return 0

    # Честно про то, что произошло. Прежний текст звал «задать GitHub PAT в
    # меню 8 → 6» — такого пункта нет ни в одной ветке, и ни одна строка кода
    # не читает сохранённый PAT, так что совет был тупиковым.
    echo -e "${RED}$(t egr_awg_dl_fail)${NC}"
    echo -e "${RED}$(tf egr_awg_fail_files "$arch" "$arch")${NC}"
    echo -e "${RED}$(tf egr_awg_fail_dir "${SCRIPT_DIR:-/opt/govless-installer}")${NC}"
    echo -e "${RED}$(t egr_awg_fail_hint)${NC}"
    return 1
}

# Bring up kawg<i> for each awg key (amneziawg-go + awg setconf + fwmark route table);
# tear down kawg interfaces no longer in the pool. Idempotent.
egress_install_awg_unit() {
    local go tool; go=$(awg_go_bin); tool=$(awg_tool_bin); [ -n "$go" ] || return 0
    mkdir -p "$AWG_DIR" 2>/dev/null
    cat > "$AWG_DIR/awg-cfg.sh" <<CFG
#!/bin/bash
iface="\$1"; [ -n "\$iface" ] || exit 0; n="\${iface#kawg}"; T=\$((256+n)); D="$AWG_DIR"
for _ in \$(seq 1 16); do ip link show "\$iface" >/dev/null 2>&1 && break; sleep 0.25; done
[ -f "\$D/.\$iface.conf" ] && "$tool" setconf "\$iface" "\$D/.\$iface.conf" 2>/dev/null
addr=\$(cat "\$D/.\$iface.addr" 2>/dev/null)
[ -n "\$addr" ] && ip addr add "\$addr" dev "\$iface" 2>/dev/null
ip link set "\$iface" up mtu 1200 2>/dev/null
# Строгий rp_filter (CIS-образы) отбрасывает ответы на kawg — источник «снаружи» (QA S-21)
sysctl -qw "net.ipv4.conf.\$iface.rp_filter=2" 2>/dev/null; sysctl -qw net.ipv4.conf.all.rp_filter=2 2>/dev/null
while ip rule del fwmark "\$T" table "\$T" 2>/dev/null; do :; done
ip rule add fwmark "\$T" table "\$T" 2>/dev/null
ip route replace default dev "\$iface" table "\$T" 2>/dev/null
exit 0
CFG
    cat > "$AWG_DIR/awg-decfg.sh" <<DCFG
#!/bin/bash
iface="\$1"; [ -n "\$iface" ] || exit 0; n="\${iface#kawg}"; T=\$((256+n))
while ip rule del fwmark "\$T" table "\$T" 2>/dev/null; do :; done
ip route flush table "\$T" 2>/dev/null
exit 0
DCFG
    chmod +x "$AWG_DIR/awg-cfg.sh" "$AWG_DIR/awg-decfg.sh"
    cat > /etc/systemd/system/govless-awg@.service <<UNIT
[Unit]
Description=goVLESS AmneziaWG egress %i
After=network-online.target
[Service]
Type=forking
ExecStart=$go %i
ExecStartPost=$AWG_DIR/awg-cfg.sh %i
ExecStopPost=$AWG_DIR/awg-decfg.sh %i
Restart=on-failure
RestartSec=2
[Install]
WantedBy=multi-user.target
UNIT
    cat > /etc/systemd/system/govless-awg-reconcile.service <<RSVC
[Unit]
Description=goVLESS AWG egress reconcile (bring up kawg legs that missed boot)
After=govless-egress.service network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/bin/govless --egress-awg-up
RSVC
    cat > /etc/systemd/system/govless-awg-reconcile.timer <<RTMR
[Unit]
Description=goVLESS AWG egress reconcile shortly after boot
[Timer]
OnBootSec=30
[Install]
WantedBy=timers.target
RTMR
    systemctl daemon-reload 2>/dev/null
    systemctl enable govless-awg-reconcile.timer >/dev/null 2>&1
}

egress_awg_sync() {
    mkdir -p "$AWG_DIR" 2>/dev/null
    egress_install_awg_unit
    local want="" idx iface mark table addr host go tool
    go=$(awg_go_bin); tool=$(awg_tool_bin)
    [ -n "$go" ] && [ -n "$tool" ] || return 0
    while IFS=$'\t' read -r idx iface mark table addr host; do
        [ -z "$iface" ] && continue
        want="$want $iface"
        python3 "$EGRESS_PARSE_PY" awgconf "$idx" < "$EGRESS_UP" > "$AWG_DIR/.$iface.conf" 2>/dev/null; chmod 600 "$AWG_DIR/.$iface.conf"
        printf '%s\n' "$addr" > "$AWG_DIR/.$iface.addr"
        systemctl enable "govless-awg@${iface}" >/dev/null 2>&1
        systemctl restart "govless-awg@${iface}" 2>/dev/null
    done < <(python3 "$EGRESS_PARSE_PY" awglist < "$EGRESS_UP" 2>/dev/null)
    local i
    for i in $(ip -o link show 2>/dev/null | grep -oE 'kawg[0-9]+' | sort -u); do
        case " $want " in *" $i "*) : ;; *) systemctl disable --now "govless-awg@$i" 2>/dev/null; ip link del "$i" 2>/dev/null;; esac
    done
}

egress_awg_up()   { egress_awg_sync; }
egress_awg_teardown() {
    local i
    for i in $(ip -o link show 2>/dev/null | grep -oE 'kawg[0-9]+' | sort -u); do
        systemctl disable --now "govless-awg@$i" 2>/dev/null; ip link del "$i" 2>/dev/null; pkill -f "amneziawg-go $i" 2>/dev/null
    done
}

# ── Collect vless:// links from input (file / URL / inline string) ──────────
egress_collect_links() {
    local input="$1" raw=""
    egress_ensure_parser
    if [ -f "$input" ]; then
        raw="$(cat "$input")"
    elif printf '%s' "$input" | grep -qiE '^https?://'; then
        echo -e "${YELLOW}[*] $(t egr_fetching)${NC}" >&2
        raw="$(curl -fsSL --max-time 25 "$input" 2>/dev/null || true)"
        [ -z "$raw" ] && { echo -e "${RED}$(t egr_fetch_fail)${NC}" >&2; return 1; }
    else
        raw="$input"
    fi
    printf '%s' "$raw" | python3 "$EGRESS_PARSE_PY" extract
}

# ── Rebuild xray config from upstream.txt and (re)start everything ──────────
egress_rebuild() {
    [ -s "$EGRESS_UP" ] || { echo -e "${RED}$(t egr_pool_empty)${NC}"; return 1; }
    egress_ensure_parser
    egress_ensure_xray || echo -e "${YELLOW}$(t egr_xray_fallback)${NC}"
    # xray detects config format by file EXTENSION → temp file must end in .json
    local tmpcfg="$EGRESS_DIR/.config.new.json"
    local poolfile="$EGRESS_UP"
    if egress_pool_has_awg && ! egress_ensure_awg; then
        echo -e "${YELLOW}$(t egr_awg_skip)${NC}"
        poolfile="$EGRESS_DIR/.pool.vlessonly"; grep -v '^awg://' "$EGRESS_UP" > "$poolfile" 2>/dev/null
        [ -s "$poolfile" ] || { echo -e "${RED}$(t egr_awg_only)${NC}"; return 1; }
    fi
    if ! python3 "$EGRESS_PARSE_PY" config \
            --socks-port "$EGRESS_SOCKS_PORT" --api-port "$EGRESS_API_PORT" \
            --probe "$EGRESS_OBSPROBE" --strategy leastPing --probe-interval 10s \
            --max "$EGRESS_MAX" --mss "$EGRESS_MSS" < "$poolfile" \
            > "$tmpcfg" 2> "$EGRESS_DIR/parse.log"; then
        echo -e "${RED}$(t egr_parse_fail_detail)${NC}"
        sed 's/^/    /' "$EGRESS_DIR/parse.log" >&2; rm -f "$tmpcfg"; return 1
    fi
    local xray; xray=$(egress_xray_bin)
    if [ -n "$xray" ] && ! "$xray" -test -c "$tmpcfg" > "$EGRESS_DIR/test.log" 2>&1; then
        echo -e "${RED}$(t egr_cfg_invalid)${NC}"; tail -3 "$EGRESS_DIR/test.log"; rm -f "$tmpcfg"; return 1
    fi
    mv -f "$tmpcfg" "$EGRESS_CFG"; chmod 600 "$EGRESS_CFG"
    egress_pool_has_awg && egress_awg_sync
    egress_write_service || return 1
    egress_set_bot_proxy "socks5://127.0.0.1:${EGRESS_SOCKS_PORT}"
    systemctl restart govless-bot 2>/dev/null || true
    rm -f "$EGRESS_DIR/.override" 2>/dev/null   # a fresh xray restart clears any bo override
    log_info "$(tf egr_log_rebuilt "$(_egr_count)")"
    return 0
}

# ── Reachability test through the egress SOCKS5 (warms the observatory) ─────
egress_test() {
    # The observatory needs several probe cycles + xray startup to elect a live
    # outbound after a (re)build — up to ~60s cold. Keep trying within a 75s
    # window with a spinner instead of a false early failure. HTTP >=100 = OK.
    local code t0=$SECONDS spin='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' f=0 j
    while [ $((SECONDS - t0)) -lt 75 ]; do
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 6 \
            --socks5-hostname "127.0.0.1:${EGRESS_SOCKS_PORT}" "$EGRESS_TESTURL" 2>/dev/null)
        code="${code//[^0-9]/}"
        if [ -n "$code" ] && [ "$code" -ge 100 ] 2>/dev/null; then
            [ -t 2 ] && printf "\r\033[K" >&2
            echo -e "${GREEN}✓ $(tf egr_test_ok "$code")${NC}"; return 0
        fi
        for j in 1 2 3 4 5 6 7; do
            [ -t 2 ] && printf "\r\033[K  \033[0;36m%s\033[0m %s \033[2m(%s%s)\033[0m" \
                "${spin:f++%10:1}" "$(t egr_checking)" "$((SECONDS-t0))" "$(t egr_unit_s)" >&2
            sleep 0.3
        done
    done
    [ -t 2 ] && printf "\r\033[K" >&2
    echo -e "${RED}✗ $(tf egr_test_fail "$EGRESS_SERVICE")${NC}"; return 1
}

# ── Import = REPLACE the whole pool (arg: key / list / subscription) ─────────
egress_import() {
    mkdir -p "$EGRESS_DIR"; chmod 700 "$EGRESS_DIR" 2>/dev/null
    local links; links="$(egress_collect_links "$1")" || return 1
    [ -z "$links" ] && { echo -e "${RED}$(t egr_parse_fail)${NC}"; return 1; }
    printf '%s\n' "$links" | grep . | head -n "$EGRESS_MAX" > "$EGRESS_UP"; chmod 600 "$EGRESS_UP"
    egress_rebuild || return 1
    echo -e "${GREEN}$(tf egr_imported "$(_egr_count)")${NC}"
    echo -e "${YELLOW}$(t egr_warming)${NC}"
    sleep 2; egress_test || echo -e "${YELLOW}$(t egr_test_hint)${NC}"
}

# ── Add one (or more) key(s) to the existing pool ───────────────────────────
egress_add_key() {
    mkdir -p "$EGRESS_DIR"; chmod 700 "$EGRESS_DIR" 2>/dev/null
    local links; links="$(egress_collect_links "$1")" || return 1
    [ -z "$links" ] && { echo -e "${RED}$(t egr_parse_fail)${NC}"; return 1; }
    touch "$EGRESS_UP"
    local added=0 l
    while IFS= read -r l; do
        [ -z "$l" ] && continue
        grep -qxF "$l" "$EGRESS_UP" && continue
        if [ "$(grep -c . "$EGRESS_UP")" -ge "$EGRESS_MAX" ]; then echo -e "${YELLOW}$(tf egr_full "$EGRESS_MAX")${NC}"; break; fi
        printf '%s\n' "$l" >> "$EGRESS_UP"; added=$((added+1))
    done <<< "$links"
    chmod 600 "$EGRESS_UP"
    [ "$added" -eq 0 ] && { echo -e "${YELLOW}$(t egr_add_none)${NC}"; return 0; }
    egress_rebuild || return 1
    echo -e "${GREEN}$(tf egr_added "$added")${NC}"; sleep 2; egress_test || true
}

# ── Remove a key by its list number ─────────────────────────────────────────
egress_remove_key() {
    local idx="$1" total
    [ -s "$EGRESS_UP" ] || { echo -e "${RED}$(t egr_no_keys_yet)${NC}"; return 1; }
    total=$(grep -c . "$EGRESS_UP")
    { [[ "$idx" =~ ^[0-9]+$ ]] && [ "$idx" -ge 1 ] && [ "$idx" -le "$total" ]; } \
        || { echo -e "${RED}$(t egr_bad_index)${NC}"; return 1; }
    sed -i "${idx}d" "$EGRESS_UP"
    if [ ! -s "$EGRESS_UP" ]; then echo -e "${YELLOW}$(t egr_now_empty)${NC}"; egress_disable; return 0; fi
    egress_rebuild && echo -e "${GREEN}$(t egr_removed)${NC}"
}

# ── List the pool, marking the active key ───────────────────────────────────
egress_list() {
    egress_ensure_parser
    [ -s "$EGRESS_UP" ] || { echo -e "${YELLOW}$(t egr_no_keys)${NC}"; return 0; }
    local active; active="$(egress_active_index)"
    echo ""; echo -e "  ${BOLD}$(t egr_list_title)${NC}"
    local i=0 name host sec net mark
    while IFS=$'\t' read -r name host sec net; do
        i=$((i+1))
        if [ "$i" = "$active" ]; then mark="${GREEN}★${NC}"; else mark=" "; fi
        printf "  %b %2d) %s  %-21s %s/%s\n" "$mark" "$i" "$(_egr_pad "$name" 18)" "$host" "$sec" "$net"
    done < <(python3 "$EGRESS_PARSE_PY" list < "$EGRESS_UP")
    echo ""
}

# ── Turn the egress off (back to direct) ────────────────────────────────────
egress_disable() {
    systemctl disable --now "${EGRESS_SERVICE}" 2>/dev/null || true
    rm -f "/etc/systemd/system/${EGRESS_SERVICE}.service"
    systemctl daemon-reload 2>/dev/null || true
    egress_set_bot_proxy ""
    egress_probe_remove
    egress_awg_teardown
    # Бот перечитает bot.env без прокси; упавший из-за прокси — поднимется.
    # Выключенного бота не трогаем: restart запускал его, а через Requires= и
    # govlessctl, даже когда бот не настроен.
    if systemctl is-enabled --quiet govless-bot 2>/dev/null; then
        systemctl restart govless-bot 2>/dev/null || true
    fi
    echo -e "${GREEN}$(t egr_disabled)${NC}"
    log_info "$(t egr_log_disabled)"
}

# ── Text status (CLI) ────────────────────────────────────────────────────────
egress_status() {
    source "$BOT_ENV" 2>/dev/null
    local active nkeys act
    active=$(systemctl is-active "${EGRESS_SERVICE}" 2>/dev/null || echo "inactive")
    nkeys=$(_egr_count); act="$(egress_active_index)"
    echo -e "  $(t egr_st_service): ${YELLOW}${active}${NC}   $(t egr_st_keys): ${YELLOW}${nkeys}${NC}   SOCKS5: 127.0.0.1:${EGRESS_SOCKS_PORT}"
    [ -n "$act" ] && echo -e "  $(t egr_st_active): ${GREEN}#${act}${NC}"
    echo -e "  BOT_PROXY: ${YELLOW}${BOT_PROXY:-$(t egr_st_none)}${NC}"
    if [ "$active" = "active" ]; then
        local ip; ip=$(curl -s --max-time 8 --socks5-hostname 127.0.0.1:${EGRESS_SOCKS_PORT} https://api.ipify.org 2>/dev/null)
        echo -e "  $(t egr_st_extip): ${GREEN}${ip:-—}${NC}"
    fi
}

# ── Dashboard helpers ───────────────────────────────────────────────────────
egress_human() {
    awk -v b="${1:-0}" 'BEGIN{u[0]="B";u[1]="KB";u[2]="MB";u[3]="GB";u[4]="TB";i=0;
        while(b>=1024 && i<4){b/=1024;i++}
        if(i==0) printf "%d %s", b, u[i]; else printf "%.1f %s", b, u[i]}'
}

egress_ping_ms() {
    local r code tapp ttot t
    r=$(curl -s -o /dev/null -w '%{http_code} %{time_appconnect} %{time_total}' --max-time 2 \
        --socks5-hostname "127.0.0.1:${EGRESS_SOCKS_PORT}" "$EGRESS_TESTURL" 2>/dev/null) || true
    set -- $r; code="${1:-000}"; tapp="${2:-0}"; ttot="${3:-0}"
    t="$tapp"; awk -v x="$tapp" 'BEGIN{exit !(x>0)}' || t="$ttot"
    if awk -v x="$tapp" 'BEGIN{exit !(x>0)}' || { [ -n "$code" ] && [ "$code" != "000" ]; }; then
        awk -v t="$t" 'BEGIN{printf "%.0f", (t>0?t:0)*1000}'
    else
        echo "—"
    fi
}

egress_traffic() {
    local xray; xray=$(egress_xray_bin) || { echo "0 0"; return; }
    systemctl is-active --quiet "$EGRESS_SERVICE" 2>/dev/null || { echo "0 0"; return; }
    "$xray" api statsquery --server="127.0.0.1:${EGRESS_API_PORT}" -timeout 3 \
        -pattern "inbound>>>socks-in" 2>/dev/null | python3 -c '
import sys, json
up = dn = 0
try:
    d = json.load(sys.stdin)
    for s in d.get("stat", []):
        n = s.get("name", "")
        if n.endswith("uplink"): up = s.get("value", 0)
        elif n.endswith("downlink"): dn = s.get("value", 0)
except Exception:
    pass
print(up, dn)' 2>/dev/null || echo "0 0"
}

# Per-key latency: 5 TCP connects, ignore the first 2 (warm-up), average last 3.
egress_key_ping() {
    local host="$1" port="$2" i rc t0 t1 sum=0 cnt=0 fails=0
    for i in 1 2 3 4 5; do
        t0=$(date +%s%N)
        timeout 1.5 bash -c 'exec 3<>/dev/tcp/"$1"/"$2"' _ "$host" "$port" 2>/dev/null; rc=$?
        t1=$(date +%s%N)
        [ "$rc" -ne 0 ] && fails=$((fails + 1))
        [ "$i" -eq 2 ] && [ "$fails" -eq 2 ] && { echo "—"; return; }
        if [ "$i" -ge 3 ]; then
            [ "$rc" -ne 0 ] && { echo "—"; return; }
            sum=$(( sum + (t1 - t0) / 1000000 )); cnt=$((cnt + 1))
        fi
    done
    [ "$cnt" -gt 0 ] && echo "$((sum / cnt))" || echo "—"
}

# Alternate-screen + cursor hide (flicker-free, htop-like)
egress_alt_enter() { tput smcup 2>/dev/null || printf '\033[?1049h'; }
egress_alt_leave() { tput rmcup 2>/dev/null || printf '\033[?1049l'; }
egress_cur_hide()  { tput civis 2>/dev/null || printf '\033[?25l'; }
egress_cur_show()  { tput cnorm 2>/dev/null || printf '\033[?25h'; }

# Manually pin / unpin which key is active (balancer override via `xray api bo`)
egress_set_active() {
    local idx="$1" xray; xray=$(egress_xray_bin) || return 1
    "$xray" api bo --server="127.0.0.1:${EGRESS_API_PORT}" -b bot-bal "out_${idx}" >/dev/null 2>&1 \
        && echo "$idx" > "${EGRESS_DIR}/.override"
}
egress_set_auto() {
    local xray; xray=$(egress_xray_bin) || return 1
    "$xray" api bo --server="127.0.0.1:${EGRESS_API_PORT}" -b bot-bal -r >/dev/null 2>&1
    rm -f "${EGRESS_DIR}/.override"
}

# ── Measure all dynamic values into globals (so arrow nav stays instant) ─────
declare -gA EGR_PING
EGR_OPING=""; EGR_UP=0; EGR_DN=0; EGR_ACTIVE=""; EGR_ST="off"; EGR_L30="—"; EGR_L5="—"; EGR_L15="—"; EGR_L60="—"
_egr_measure() {
    case "$(systemctl is-active "$EGRESS_SERVICE" 2>/dev/null)" in
        active) EGR_ST="on" ;;
        activating|deactivating|reloading) EGR_ST="warm" ;;
        *) EGR_ST="off" ;;
    esac
    EGR_ACTIVE=$(egress_active_index)
    [ -z "$EGR_ACTIVE" ] && [ "$(_egr_count)" -ge 1 ] && EGR_ACTIVE=1
    EGR_OPING=$(egress_ping_ms)
    read -r EGR_UP EGR_DN < <(egress_traffic)
    unset EGR_PING; declare -gA EGR_PING
    local i=0 name host sec net hp h p
    while IFS=$'\t' read -r name host sec net; do
        i=$((i+1)); hp="$host"; p="${hp##*:}"; h="${hp%:*}"
        if [[ "$sec" == amnezia* ]]; then EGR_PING[$i]=$(awg_ping_ms "$h"); else EGR_PING[$i]=$(egress_key_ping "$h" "$p"); fi
    done < <(python3 "$EGRESS_PARSE_PY" list < "$EGRESS_UP")
    EGR_L30=$(egress_loss_pct 30); EGR_L5=$(egress_loss_pct 300); EGR_L15=$(egress_loss_pct 900); EGR_L60=$(egress_loss_pct 3600)
}

# Left-pad a string to a DISPLAY width (UTF-8 aware: Cyrillic counts as 1, not 2
# bytes) so the columns line up regardless of name script. Needs a UTF-8 locale,
# which egress_menu sets.
_egr_pad() { local s="$1" w="$2" l=${#1}; [ "$l" -gt "$w" ] && { s="${1:0:$w}"; l="$w"; }; printf '%s%*s' "$s" "$((w-l))" ''; }

# ── Paint from cached values (home + overwrite; selected row highlighted) ────
_egr_paint() {
    local sel="$1" live="$2"
    printf '\033[H'
    echo -e "  ${BOLD}${GREEN}$(t egr_title)${NC}\033[K"
    local stcol mode
    case "$EGR_ST" in
        on)   stcol="${GREEN}● $(t egr_dash_on)${NC}" ;;
        warm) stcol="${CYAN}◐ $(t egr_dash_warm)${NC}" ;;
        *)    stcol="${YELLOW}○ $(t egr_dash_off)${NC}" ;;
    esac
    if [ -s "${EGRESS_DIR}/.override" ]; then mode="${YELLOW}$(t egr_mode_manual)${NC}"; else mode="${DIM}$(t egr_mode_auto)${NC}"; fi
    echo -e "  $(t egr_dash_status): ${stcol}   Telegram API: ${EGR_OPING} ms   ↑ $(egress_human "$EGR_UP")  ↓ $(egress_human "$EGR_DN")   ${mode}\033[K"
    local lc="${GREEN}" l3n="${EGR_L30%\%}"
    if [ "$EGR_L30" = "—" ]; then lc="${DIM}"
    elif awk -v x="$l3n" 'BEGIN{exit !(x>=5)}' 2>/dev/null; then lc="${RED}"
    elif awk -v x="$l3n" 'BEGIN{exit !(x>=1)}' 2>/dev/null; then lc="${YELLOW}"; fi
    echo -e "  ${DIM}$(t egr_loss)${NC} ${lc}30$(t egr_unit_s) ${EGR_L30}${NC} · 5$(t egr_unit_m) ${EGR_L5} · 15$(t egr_unit_m) ${EGR_L15} · 60$(t egr_unit_m) ${EGR_L60}\033[K"
    echo -e "  ${DIM}──────────────────────────────────────────────${NC}\033[K"
    echo -e "  ${BOLD}$(t egr_list_title)${NC} ${DIM}($(t egr_ping_col))${NC}\033[K"
    local i=0 name host sec net kp mark m
    while IFS=$'\t' read -r name host sec net; do
        i=$((i+1))
        kp="${EGR_PING[$i]:-—}"; [ "$kp" != "—" ] && kp="${kp} ms"
        if [ "$i" = "$sel" ]; then
            if [ "$i" = "$EGR_ACTIVE" ]; then m="★"; else m=" "; fi
            printf " \033[7m %s %2d) %s  %-21s %-16s %s \033[0m\033[K\n" "$m" "$i" "$(_egr_pad "$name" 18)" "$host" "$sec/$net" "$kp"
        else
            if [ "$i" = "$EGR_ACTIVE" ]; then mark="${GREEN}★${NC}"; else mark=" "; fi
            printf "  %b %2d) %s  %-21s %-16s %s\033[K\n" "$mark" "$i" "$(_egr_pad "$name" 18)" "$host" "$sec/$net" "$kp"
        fi
    done < <(python3 "$EGRESS_PARSE_PY" list < "$EGRESS_UP")
    echo -e "  ${DIM}──────────────────────────────────────────────${NC}\033[K"
    _egr_bar "$live"
    printf '\033[J'
}

# htop-style function-key action bar
_egr_bar() {
    local live="$1" p
    [ "$live" = "1" ] && p="$(t egr_pause)" || p="$(t egr_resume)"
    echo -e "  \033[7m F2 \033[0m $(t egr_add)  \033[7m F3 \033[0m $(t egr_remove)  \033[7m F6 \033[0m $(t egr_setactive)  \033[7m F7 \033[0m $(t egr_auto)  \033[7m F10 \033[0m $(t egr_quit)\033[K"
    echo -e "  ${DIM}$(t egr_navhint): ↑/↓ · F4 ${p} · F5 $(t egr_refresh)${NC}\033[K"
    echo -e "  ${DIM}$(t egr_enterhint)${NC}\033[K"
}

# Read one keypress: F1-F12, UP/DOWN/LEFT/RIGHT, a literal char, ESC, __timeout__.
egress_readkey() {
    local to="$1" k k2 k3 seq c
    IFS= read -rsn1 -t "$to" k || { echo "__timeout__"; return; }
    if [ -z "$k" ]; then echo "ENTER"; return; fi
    if [ "$k" != $'\033' ]; then echo "$k"; return; fi
    IFS= read -rsn1 -t 0.06 k2 || { echo "ESC"; return; }
    case "$k2" in
        O)  IFS= read -rsn1 -t 0.06 k3
            case "$k3" in P) echo F1;; Q) echo F2;; R) echo F3;; S) echo F4;; *) echo ESC;; esac ;;
        "[") seq=""
            while IFS= read -rsn1 -t 0.06 c; do seq+="$c"; [[ "$c" =~ [A-Z~] ]] && break; done
            case "$seq" in
                A) echo UP;; B) echo DOWN;; C) echo RIGHT;; D) echo LEFT;;
                "11~"|P) echo F1;; "12~"|Q) echo F2;; "13~"|R) echo F3;; "14~"|S) echo F4;;
                "15~") echo F5;; "17~") echo F6;; "18~") echo F7;; "19~") echo F8;;
                "20~") echo F9;; "21~") echo F10;; "23~") echo F11;; "24~") echo F12;;
                *) echo ESC;; esac ;;
        *) echo ESC ;;
    esac
}

# ── Active-key REAL traffic-loss probe (kernel retrans bytes, bg timer) ─────────────
# host:port of the upstream the balancer/route is actually using.
egress_active_endpoint() {
    local idx key hp
    idx=$(egress_active_index 2>/dev/null); [ -z "$idx" ] && idx=1
    key=$(sed -n "${idx}p" "$EGRESS_UP" 2>/dev/null); [ -z "$key" ] && key=$(sed -n '1p' "$EGRESS_UP" 2>/dev/null)
    case "$key" in awg://*) return 1 ;; esac   # AWG endpoint is UDP; loss-probe (TCP retrans) does not apply
    hp="${key#*@}"; hp="${hp%%\?*}"; [ -z "$hp" ] && return 1
    echo "${hp%%:*} ${hp##*:}"
}

# One sample: ping the active node; append "epoch sent recv" to the ring log.
# If the node blocks ICMP entirely but its TCP port is reachable, record it as
# no-loss (we can't measure ICMP loss, so don't report a false 100%).
egress_probe_sample() {
    [ -s "$EGRESS_UP" ] || return 0
    # Pick what to measure by the ACTIVE key's type:
    #  VLESS -> TCP retrans to the node (dst node-ip);
    #  AWG   -> the tunnelled TCP to Telegram egresses via the kawg interface, so
    #           its source IP is the kawg address -> measure retrans on (src kawg-ip).
    local idx key filter
    idx=$(egress_active_index 2>/dev/null); [[ "$idx" =~ ^[0-9]+$ ]] || idx=1
    key=$(sed -n "${idx}p" "$EGRESS_UP" 2>/dev/null)
    if [ "${key:0:6}" = "awg://" ]; then
        local sip; sip=$(python3 "$EGRESS_PARSE_PY" awglist < "$EGRESS_UP" 2>/dev/null | awk -v i="$idx" '$1==i{split($5,a,"/");print a[1];exit}')
        [ -z "$sip" ] && return 0
        filter="src $sip"
    else
        local hp; hp="${key#*@}"; hp="${hp%%\?*}"; [ -z "$hp" ] && return 0
        filter="dst ${hp%%:*}"
    fi
    local snap="$EGRESS_DIR/.lossnap" now; now=$(date +%s)
    # Real traffic loss = retrans/sent over the interval, measured passively.
    ss -tinO "$filter" 2>/dev/null | awk -v snap="$snap" -v now="$now" -v lf="$EGRESS_LOSS" '
      BEGIN{ while((getline l < snap)>0){ split(l,a," "); ps[a[1]]=a[2]; pr[a[1]]=a[3] } close(snap) }
      {
        laddr=$4; np=split(laddr,b,":"); port=b[np]
        s=0; r=0
        if(match($0,/bytes_sent:[0-9]+/))    s=substr($0,RSTART+11,RLENGTH-11)
        if(match($0,/bytes_retrans:[0-9]+/)) r=substr($0,RSTART+14,RLENGTH-14)
        if(port ~ /^[0-9]+$/){ cs[port]=s; cr[port]=r }
      }
      END{
        ds=0; dr=0
        for(pp in cs){ if(pp in ps){ x=cs[pp]-ps[pp]; y=cr[pp]-pr[pp]; if(x>0)ds+=x; if(y>0)dr+=y } }
        out=""; for(pp in cs){ out=out pp" "cs[pp]" "cr[pp]"\n" }
        printf "%s", out > snap
        printf "%s %d %d\n", now, ds, dr >> lf
      }'
    tail -n 400 "$EGRESS_LOSS" > "$EGRESS_LOSS.tmp" 2>/dev/null && mv -f "$EGRESS_LOSS.tmp" "$EGRESS_LOSS"
}

# Loss % over the last <window> seconds → "X.X%" or "—" if no samples yet.
egress_loss_pct() {
    local win="$1" cutoff; cutoff=$(( $(date +%s) - win ))
    [ -s "$EGRESS_LOSS" ] || { echo "—"; return; }
    awk -v c="$cutoff" '$1>=c{s+=$2; r+=$3} END{ if(s>0) printf "%.1f%%", r/s*100; else printf "—" }' "$EGRESS_LOSS" 2>/dev/null
}

# Install / remove the systemd timer that samples loss every 15s.
egress_probe_install() {
    cat > "/etc/systemd/system/${EGRESS_PROBE_SVC}.service" <<U
[Unit]
Description=goVLESS egress active-key loss probe
[Service]
Type=oneshot
ExecStart=/usr/local/bin/govless --egress-probe
U
    cat > "/etc/systemd/system/${EGRESS_PROBE_SVC}.timer" <<U
[Unit]
Description=goVLESS egress loss probe (every 15s)
[Timer]
OnBootSec=20
OnUnitActiveSec=10
AccuracySec=2s
[Install]
WantedBy=timers.target
U
    systemctl daemon-reload
    systemctl enable --now "${EGRESS_PROBE_SVC}.timer" >/dev/null 2>&1 || true
}
egress_probe_remove() {
    systemctl disable --now "${EGRESS_PROBE_SVC}.timer" 2>/dev/null || true
    rm -f "/etc/systemd/system/${EGRESS_PROBE_SVC}.service" "/etc/systemd/system/${EGRESS_PROBE_SVC}.timer" "$EGRESS_LOSS" "$EGRESS_DIR/.lossnap"
    systemctl daemon-reload 2>/dev/null || true
}

# ── Live htop-style dashboard: ↑/↓ select a key, F-keys act on it ────────────
# ICMP latency to an AWG endpoint host (ms) or "—"
awg_ping_ms() { local h="$1" r; r=$(ping -4 -c1 -W1 -- "$h" 2>/dev/null | grep -oE 'time=[0-9.]+' | head -1 | cut -d= -f2); [ -n "$r" ] && printf '%.0f\n' "$r" || echo "—"; }

# Mini-instruction: which formats the import accepts and how to paste.
egress_paste_prompt() {
    echo -e "  ${BOLD}$(t egr_fmt_title)${NC}"
    echo -e "    ${CYAN}$(_egr_pad "vless://…" 19)${NC}— $(t egr_fmt_vless)"
    echo -e "    ${CYAN}$(_egr_pad "$(t egr_fmt_sub_label)" 19)${NC}— $(t egr_fmt_sub)"
    echo -e "    ${CYAN}$(_egr_pad "AmneziaWG vpn://…" 19)${NC}— $(t egr_fmt_awg_vpn)"
    echo -e "    ${CYAN}$(_egr_pad "AmneziaWG .conf" 19)${NC}— $(t egr_fmt_awg_conf)"
    echo -e "  ${DIM}$(t egr_paste_pre) ${NC}${WHITE}Enter${NC}${DIM}$(t egr_paste_post)${NC}"
}
# Read a multi-line paste from the terminal until a lone "." or EOF.
egress_read_paste() {
    # Submit on Enter. The first line blocks for input; any extra lines of a
    # multi-line paste (AmneziaWG .conf) are captured because they arrive within
    # ~0.5s — then it auto-submits. No "." / Ctrl-D needed (but "." still ends it).
    local line buf=""
    IFS= read -r line || return
    buf="$line"$'\n'
    while IFS= read -r -t 0.5 line; do [ "$line" = "." ] && break; buf+="$line"$'\n'; done
    printf '%s' "$buf"
}
egress_add_interactive() {
    egress_paste_prompt
    local s; s="$(egress_read_paste)"
    [ -n "$(printf '%s' "$s" | tr -d '[:space:]')" ] && egress_add_key "$s" || echo -e "  ${DIM}$(t egr_paste_empty)${NC}"
}

egress_menu() {
    [ "${LC_ALL:-}" = *UTF-8* ] 2>/dev/null || { _l=$(locale -a 2>/dev/null | grep -im1 -E "^(C|en_US)\.utf-?8$"); [ -n "$_l" ] && export LC_ALL="$_l"; }
    egress_ensure_parser
    # Self-heal upgraded installs: if a pool is configured but predates the fixed
    # config (sockopt under streamSettings / keepalive interval), rebuild once.
    if [ -s "$EGRESS_UP" ] && { [ ! -x "$EGRESS_XRAY" ] || ! grep -q 'tcpKeepAliveInterval' "$EGRESS_CFG" 2>/dev/null || ! grep -q 'tcpMaxSeg' "$EGRESS_CFG" 2>/dev/null || { [ "$(_egr_count)" -le 1 ] && grep -q '"bot-bal"' "$EGRESS_CFG" 2>/dev/null; }; }; then
        echo -e "${YELLOW}[*] $(t egr_selfheal)${NC}"
        egress_rebuild >/dev/null 2>&1 || true
    fi
    local interval="${EGRESS_REFRESH:-2}" N s key live=1 sel=1
    egress_alt_enter; egress_cur_hide
    trap 'egress_cur_show; egress_alt_leave' RETURN

    # empty-pool screen — only "add" until there's at least one key
    while [ "$(_egr_count)" -lt 1 ]; do
        egress_cur_hide; printf '\033[H'
        echo -e "  ${BOLD}${GREEN}$(t egr_title)${NC}\033[K"
        echo -e "  ${DIM}$(t egr_no_keys)${NC}\033[K"; echo -e "\033[K"
        echo -e "  \033[7m F2 \033[0m $(t egr_add)   \033[7m F10 \033[0m $(t egr_quit)\033[K"
        echo -e "  ${DIM}$(t egr_keyhint_empty)${NC}\033[K"; printf '\033[J'
        key=$(egress_readkey 3600)
        case "$key" in
            F2|2|a|A) egress_alt_leave; egress_cur_show
                      egress_add_interactive; echo -ne "  $(t egr_enter_back) "; read -r; egress_alt_enter ;;
            F10|0|q|Q) return ;;
        esac
    done

    # Instant first paint: status + loss windows are cheap (local), so the menu
    # appears immediately; the slower probes (ping/traffic) fill in on the next tick.
    systemctl is-active --quiet "$EGRESS_SERVICE" 2>/dev/null && EGR_ST="on" || EGR_ST="off"
    EGR_L30=$(egress_loss_pct 30); EGR_L5=$(egress_loss_pct 300); EGR_L15=$(egress_loss_pct 900); EGR_L60=$(egress_loss_pct 3600)
    EGR_ACTIVE=$(egress_active_index); [ -z "$EGR_ACTIVE" ] && [ "$(_egr_count)" -ge 1 ] && EGR_ACTIVE=1
    [ -n "$EGR_ACTIVE" ] && sel="$EGR_ACTIVE"
    egress_cur_hide; _egr_paint "$sel" "$live"
    _egr_measure
    while true; do
        N=$(_egr_count)
        [ "${N:-0}" -lt 1 ] && return
        if [ "$sel" -gt "$N" ]; then sel="$N"; fi
        if [ "$sel" -lt 1 ]; then sel=1; fi
        egress_cur_hide
        _egr_paint "$sel" "$live"
        if [ "$live" = "1" ]; then key=$(egress_readkey "$interval"); else key=$(egress_readkey 3600); fi
        case "$key" in
            __timeout__) _egr_measure ;;
            UP|k|K)   sel=$((sel-1)); [ "$sel" -lt 1 ] && sel=1 ;;
            DOWN|j|J) sel=$((sel+1)); [ "$sel" -gt "$N" ] && sel="$N" ;;
            F2|2|a|A) egress_alt_leave; egress_cur_show
                      egress_add_interactive; echo -ne "  $(t egr_enter_back) "; read -r; egress_alt_enter; _egr_measure ;;
            F3|3|d|D) egress_alt_leave; egress_cur_show
                      printf "  %s " "$(tf egr_del_confirm "$sel")"; read -r s
                      if [ "$s" = "1" ]; then egress_remove_key "$sel"; echo -ne "  $(t egr_enter_back) "; read -r; fi
                      egress_alt_enter; _egr_measure ;;
            F6|6|ENTER) egress_set_active "$sel"; EGR_ACTIVE="$sel" ;;
            F7|7) egress_set_auto; EGR_ACTIVE=$(egress_active_index) ;;
            F4|4|p|P) [ "$live" = "1" ] && live=0 || live=1 ;;
            F5|5|r|R) _egr_measure ;;
            F10|0|q|Q) return ;;
            *) : ;;
        esac
    done
}

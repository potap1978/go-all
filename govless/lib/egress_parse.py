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

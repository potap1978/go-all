#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Заглушка lib/awgkeys.py для tests/test_core_dryrun.sh.

Используется ТОЛЬКО когда настоящего lib/awgkeys.py ещё нет (его пишет другой
агент параллельно). Повторяет интерфейс контракта §4.2 — подкоманды и форматы
stdin/stdout — в объёме, нужном ядру: gen-params, validate, genkey, pubkey,
genpsk, conf, server-conf, vpn-encode, vpn-decode, qr-chunks, detect-line.
Криптография — X25519 на чистом Python (RFC 7748), кодек vpn:// — qCompress
(4 байта BE длины + zlib) и base64url без «=». Точность генераторов параметров
здесь не проверяется (это делают тесты настоящего модуля) — важна согласованность
форматов, чтобы сквозной прогон ядра был проверяемым без Docker.
"""
import base64
import json
import os
import random
import struct
import sys
import zlib

PARAM_KEYS = ["Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4",
              "I1", "I2", "I3", "I4", "I5", "HeaderProtectionKey", "ContentPaddingAddition",
              "RekeyAfterTime", "RekeyTimeout", "RejectAfterTime", "KeepaliveTimeout",
              "MaxHandshakeAttempts", "RandomTrailers", "DisableCookies",
              "PersistentKeepalive", "MTU"]
V3_KEYS = ["HeaderProtectionKey", "ContentPaddingAddition", "RekeyAfterTime", "RekeyTimeout",
           "RejectAfterTime", "KeepaliveTimeout", "MaxHandshakeAttempts", "RandomTrailers",
           "DisableCookies"]
I_KEYS = ["I1", "I2", "I3", "I4", "I5"]
# Универсальный I1 (KEENETIC-I1): только тег <b>, как у lib/awgkeys.py default_i1().
DEFAULT_I1 = "<b 0x1a2b858000010001000000000000>"
QUIC_I1 = "<b 0xc30000000108><r 16>"

# ── X25519 ────────────────────────────────────────────────────────────────────
P = 2 ** 255 - 19


def _clamp(k):
    k = bytearray(k)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    return bytes(k)


def _x25519(k, u):
    k = int.from_bytes(_clamp(k), "little")
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in range(254, -1, -1):
        kt = (k >> t) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt
        a = (x2 + z2) % P
        aa = a * a % P
        b = (x2 - z2) % P
        bb = b * b % P
        e = (aa - bb) % P
        c = (x3 + z3) % P
        d = (x3 - z3) % P
        da = d * a % P
        cb = c * b % P
        x3 = pow((da + cb) % P, 2, P)
        z3 = x1 * pow((da - cb) % P, 2, P) % P
        x2 = aa * bb % P
        z2 = e * ((aa + 121665 * e) % P) % P
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return (x2 * pow(z2, P - 2, P) % P).to_bytes(32, "little")


def _b64(b):
    return base64.b64encode(b).decode()


def genkey():
    return _b64(_clamp(os.urandom(32)))


def pubkey(priv_b64):
    priv = base64.b64decode(priv_b64)
    if len(priv) != 32:
        raise SystemExit("bad private key")
    return _b64(_x25519(priv, b"\x09" + b"\x00" * 31))


def genpsk():
    return _b64(os.urandom(32))


# ── Параметры ─────────────────────────────────────────────────────────────────
def _empty_params():
    return {k: "" for k in PARAM_KEYS}


def gen_params(line, preset):
    p = _empty_params()
    p["Jc"] = str(random.randint(4, 6))
    p["Jmin"] = "10"
    p["Jmax"] = "50"
    p["PersistentKeepalive"] = "25"
    p["MTU"] = "1280"
    if line in ("1.0", "1.5", "2.0"):
        while True:
            s1, s2 = random.randint(15, 150), random.randint(15, 150)
            if s1 + 56 != s2:
                break
        p["S1"], p["S2"] = str(s1), str(s2)
        if line == "2.0":
            while True:
                s3, s4 = random.randint(15, 64), random.randint(15, 150)
                if len({148 + s1, 92 + s2, 64 + s3, 32 + s4}) == 4:
                    break
            p["S3"], p["S4"] = str(s3), str(s4)
            start = 5
            for h in ("H1", "H2", "H3", "H4"):
                width = random.randint(1000, 5000)
                lo = random.randint(start, start + 100000)
                p[h] = "%d-%d" % (lo, lo + width)
                start = lo + width + 1
        else:
            hs = set()
            while len(hs) < 4:
                hs.add(random.randint(5, 2 ** 31 - 1))
            for h, v in zip(("H1", "H2", "H3", "H4"), sorted(hs)):
                p[h] = str(v)
        if line in ("1.5", "2.0") and preset != "router":
            p["I1"] = QUIC_I1 if preset == "quic" else DEFAULT_I1
    elif line == "3.1":
        for s in ("S1", "S2", "S3", "S4"):
            p[s] = "12"
        for h, v in zip(("H1", "H2", "H3", "H4"), ("1", "2", "3", "4")):
            p[h] = v
        p["HeaderProtectionKey"] = pubkey(genkey())
        p["RekeyAfterTime"] = "100-120"
        p["RekeyTimeout"] = "3-7"
        p["RejectAfterTime"] = "150-180"
        p["KeepaliveTimeout"] = "5-15"
        p["MaxHandshakeAttempts"] = "15-20"
        # 1.0.13 (PARAM-04, PARAM-07), как у lib/awgkeys.py: добавка 10-100 и keepalive 25.
        p["ContentPaddingAddition"] = "10-100"
        p["PersistentKeepalive"] = "25"
        p["RandomTrailers"] = "on"
        p["DisableCookies"] = "on"
        p["I1"] = QUIC_I1 if preset == "quic" else DEFAULT_I1
    else:
        raise SystemExit("unknown line: %s" % line)
    return p


def detect_line(params):
    if any(params.get(k) for k in V3_KEYS):
        return "3.1"
    if params.get("S3") or params.get("S4") or any("-" in str(params.get(h, "")) for h in ("H1", "H2", "H3", "H4")):
        return "2.0"
    if any(params.get(k) for k in I_KEYS):
        return "1.5"
    return "1.0"


def _conf_to_params(text):
    p = _empty_params()
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            line = line.lstrip("# ").strip()
        if "=" not in line:
            continue
        k, v = [x.strip() for x in line.split("=", 1)]
        if k in p:
            p[k] = v
    return p


def validate(line, params):
    errs = []
    try:
        jc, jmin, jmax = int(params["Jc"]), int(params["Jmin"]), int(params["Jmax"])
    except (KeyError, ValueError):
        return ["Jc/Jmin/Jmax must be integers"]
    if not 1 <= jc <= 128:
        errs.append("Jc out of range")
    if not jmin < jmax <= 1280:
        errs.append("Jmin < Jmax <= 1280 violated")
    hs = [params.get(h, "") for h in ("H1", "H2", "H3", "H4")]
    if len(set(hs)) != 4:
        errs.append("H1-H4 must differ")
    if line in ("1.0", "1.5", "2.0"):
        s1, s2 = int(params.get("S1") or 0), int(params.get("S2") or 0)
        if s1 > 1132 or s2 > 1188 or s1 + 56 == s2:
            errs.append("S1/S2 constraints violated")
    if line == "1.0" and (any(params.get(k) for k in I_KEYS) or params.get("S3") or params.get("S4")):
        errs.append("line 1.0 forbids I* and S3/S4")
    if line in ("1.0", "1.5", "2.0") and any(params.get(k) for k in V3_KEYS):
        errs.append("3.x fields forbidden for line %s" % line)
    return errs


# ── Тексты конфигов ───────────────────────────────────────────────────────────
def _param_lines(line, params, comment_i=False):
    out = []
    order = ["Jc", "Jmin", "Jmax", "S1", "S2"]
    if line in ("2.0", "3.1"):
        order += ["S3", "S4"]
    order += ["H1", "H2", "H3", "H4"]
    for k in order:
        if params.get(k):
            out.append("%s = %s" % (k, params[k]))
    if line != "1.0":
        for k in I_KEYS:
            if params.get(k):
                out.append(("# %s = %s" if comment_i else "%s = %s") % (k, params[k]))
    if line == "3.1":
        for k in V3_KEYS:
            if params.get(k):
                out.append("%s = %s" % (k, params[k]))
    return out


def conf(line, data):
    p = data["params"]
    c, s = data["client"], data["server"]
    lines = ["[Interface]", "Address = %s/32" % c["ip"]]
    dns = [d for d in data.get("dns", []) if d]
    if dns:
        lines.append("DNS = %s" % ", ".join(dns))
    lines.append("PrivateKey = %s" % c["privkey"])
    if data.get("mtu"):
        lines.append("MTU = %s" % data["mtu"])
    lines += _param_lines(line, p)
    lines += ["", "[Peer]", "PublicKey = %s" % s["pubkey"]]
    if c.get("psk"):
        lines.append("PresharedKey = %s" % c["psk"])
    lines += ["AllowedIPs = 0.0.0.0/0, ::/0",
              "Endpoint = %s:%s" % (s["endpoint_host"], s["port"]),
              "PersistentKeepalive = %s" % (p.get("PersistentKeepalive") or "25")]
    return "\n".join(lines) + "\n"


def server_conf(line, data):
    p, s = data["params"], data["server"]
    lines = ["[Interface]", "PrivateKey = %s" % s["privkey"], "Address = %s" % s["subnet"],
             "ListenPort = %s" % s["port"]]
    lines += _param_lines(line, p, comment_i=True)
    for peer in data.get("peers", []):
        lines += ["", "[Peer]", "PublicKey = %s" % peer["pubkey"]]
        if peer.get("psk"):
            lines.append("PresharedKey = %s" % peer["psk"])
        lines.append("AllowedIPs = %s/32" % peer["ip"])
    return "\n".join(lines) + "\n"


# ── vpn:// ────────────────────────────────────────────────────────────────────
def _qcompress(raw):
    return struct.pack(">I", len(raw)) + zlib.compress(raw, 8)


def _b64url(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64url_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def vpn_payload(data):
    awg = {k: v for k, v in data["params"].items() if v}
    awg["last_config"] = json.dumps({"config": data["conf_text"], "transport_proto": "udp"})
    awg["transport_proto"] = "udp"
    awg["port"] = str(data["port"])
    awg["subnet_address"] = data.get("subnet", "")
    dns = data.get("dns") or []
    return {
        "containers": [{"awg": awg, "container": data["container"]}],
        "defaultContainer": data["container"],
        "description": data.get("description", ""),
        "dns1": dns[0] if len(dns) > 0 else "",
        "dns2": dns[1] if len(dns) > 1 else "",
        "hostName": data["hostName"],
    }


def vpn_encode(data):
    raw = json.dumps(vpn_payload(data), ensure_ascii=False).encode("utf-8")
    return "vpn://" + _b64url(_qcompress(raw))


def vpn_decode(link):
    """Нормализованный вид, как у настоящего awgkeys.py: не сырой JSON приложения,
    а {container, line, hostName, port, description, dns, subnet, params,
    conf_text, client}."""
    body = link[len("vpn://"):] if link.startswith("vpn://") else link
    blob = _b64url_decode(body)
    top = json.loads(zlib.decompress(blob[4:]).decode("utf-8"))
    cont = next((c for c in top.get("containers", []) if "awg" in c), None)
    if cont is None:
        raise SystemExit("no awg container in vpn payload")
    awg = cont["awg"]
    try:
        last = json.loads(awg.get("last_config") or "{}")
    except ValueError:
        last = {}
    conf_text = last.get("config") or ""
    params = _empty_params()
    conf_params = _conf_to_params(conf_text)
    for k in PARAM_KEYS:
        params[k] = str(awg.get(k) or conf_params.get(k) or "")
    iface = {}
    peer = {}
    section = None
    for raw in conf_text.splitlines():
        line = raw.strip()
        if line in ("[Interface]", "[Peer]"):
            section = line
            continue
        if "=" in line and section:
            k, v = [x.strip() for x in line.split("=", 1)]
            (iface if section == "[Interface]" else peer)[k] = v
    ep = peer.get("Endpoint", "")
    host, _, port = ep.rpartition(":")
    try:
        port = int(awg.get("port") or port or 0)
    except ValueError:
        port = 0
    return {
        "container": cont.get("container", ""),
        "line": detect_line(params),
        "hostName": top.get("hostName") or host,
        "port": port,
        "description": top.get("description", ""),
        "dns": [d for d in (top.get("dns1", ""), top.get("dns2", "")) if d],
        "subnet": awg.get("subnet_address", ""),
        "params": params,
        "conf_text": conf_text,
        "client": {
            "ip": iface.get("Address", "").split("/")[0],
            "privkey": iface.get("PrivateKey", ""),
            "psk": peer.get("PresharedKey", ""),
        },
    }


def qr_chunks(link):
    body = link[len("vpn://"):] if link.startswith("vpn://") else link
    blob = _b64url_decode(body)
    parts = [blob[i:i + 850] for i in range(0, len(blob), 850)]
    out = []
    for idx, part in enumerate(parts):
        hdr = struct.pack(">hBBI", 1984, len(parts), idx, len(part))
        out.append(_b64url(hdr + part))
    return out


# ── CLI ───────────────────────────────────────────────────────────────────────
def _arg(args, name, default=None):
    if name in args:
        return args[args.index(name) + 1]
    return default


def main(argv):
    if not argv:
        raise SystemExit("usage: awgkeys.py <subcommand> ...")
    cmd, args = argv[0], argv[1:]
    if cmd == "genkey":
        print(genkey())
    elif cmd == "pubkey":
        # «-» или отсутствие аргумента — приватник со stdin, как у настоящего модуля.
        print(pubkey(args[0] if args and args[0] != "-" else sys.stdin.read().strip()))
    elif cmd == "genpsk":
        print(genpsk())
    elif cmd == "gen-params":
        print(json.dumps(gen_params(_arg(args, "--line", "2.0"), _arg(args, "--preset", "default"))))
    elif cmd == "validate":
        params = json.loads(sys.stdin.read())
        errs = validate(_arg(args, "--line", detect_line(params)), params)
        if errs:
            sys.stderr.write("\n".join(errs) + "\n")
            sys.exit(2)
    elif cmd == "detect-line":
        text = sys.stdin.read()
        try:
            params = json.loads(text)
        except ValueError:
            params = _conf_to_params(text)
        print(detect_line(params))
    elif cmd == "conf":
        sys.stdout.write(conf(_arg(args, "--line", "2.0"), json.loads(sys.stdin.read())))
    elif cmd == "server-conf":
        sys.stdout.write(server_conf(_arg(args, "--line", "2.0"), json.loads(sys.stdin.read())))
    elif cmd == "vpn-encode":
        print(vpn_encode(json.loads(sys.stdin.read())))
    elif cmd == "vpn-decode":
        print(json.dumps(vpn_decode(args[0] if args else sys.stdin.read().strip()), ensure_ascii=False))
    elif cmd == "qr-chunks":
        print(json.dumps(qr_chunks(args[0] if args else sys.stdin.read().strip())))
    else:
        raise SystemExit("unknown subcommand: %s" % cmd)


if __name__ == "__main__":
    main(sys.argv[1:])

#!/usr/bin/env python3
"""adopted_check.py — are the people of containers adopted from the Amnezia app intact? (runs on the server, root)

For every profile with `imported: true` in config.json: the newest pre-adoption snapshot
/opt/goamnezia/import-snapshots/<container>-*/ is compared with the live container and users.json —
server key files (snapshot = live = state copy), and per snapshot peer: live peer present, same AllowedIPs, same PSK,
user present with the same pubkey/IP/PSK, clientName kept. creationDate/userData changes are counted separately
(known ACC-1, 27.09.2026). Prints ONE JSON line, no secrets (hashes only). Exit 0 — intact, 1 — broken, 2 — no
adopted profiles. Optional argv[1]: a baseline file "<container> <sha256> <path>" (e.g. taken before installation).
"""
import glob
import hashlib
import json
import os
import subprocess
import sys

GA = os.environ.get("GOAMNEZIA_DIR", "/opt/goamnezia")


def h(b):
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode()).hexdigest()


def sh(*a):
    return subprocess.run(a, capture_output=True).stdout


def peers_of(conf):
    peers, cur = [], None
    for ln in conf.splitlines():
        s = ln.strip()
        if s == "[Peer]":
            cur = {}
            peers.append(cur)
        elif cur is not None and "=" in s and not s.startswith("#"):
            k, v = s.split("=", 1)
            cur[k.strip()] = v.strip()
    return peers


base = {}
if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
    for ln in open(sys.argv[1]):
        parts = ln.split()
        if len(parts) == 3:
            base[(parts[0], parts[2].rsplit("/", 1)[-1])] = parts[1]

cfg = json.load(open(GA + "/config.json"))
users = json.load(open(GA + "/users.json"))["users"]
out = {"profiles": {}, "people": 0, "broken": [], "meta_changed": 0}
for prof, p in sorted((cfg.get("profiles") or {}).items()):
    if not p.get("imported"):
        continue
    c = p.get("container")
    iface = p.get("iface") or "awg0"
    tool = "wg" if iface == "wg0" else "awg"
    snaps = sorted(glob.glob("%s/import-snapshots/%s-2*/" % (GA, c)))
    if not snaps:
        out["broken"].append("%s: no import snapshot" % c)
        continue
    snap = snaps[-1]
    r = {"container": c, "snapshot": os.path.basename(snap.rstrip("/")), "keys_ok": True, "people": 0, "ok": 0}
    for f in ("wireguard_server_private_key.key", "wireguard_psk.key", "wireguard_server_public_key.key"):
        live = h(sh("docker", "exec", c, "cat", "/opt/amnezia/awg/" + f))
        s = h(open(snap + f, "rb").read())
        st_path = "%s/state/%s/%s" % (GA, c, f)
        st = h(open(st_path, "rb").read()) if os.path.exists(st_path) else None
        same = live == s == st and base.get((c, f), s) == s
        if not same:
            r["keys_ok"] = False
            out["broken"].append("%s: %s differs" % (c, f))
    conf_name = "wg0.conf" if iface == "wg0" and os.path.exists(snap + "wg0.conf") else "awg0.conf"
    if not os.path.exists(snap + conf_name):
        conf_name = [n for n in ("wg0.conf", "awg0.conf") if os.path.exists(snap + n)][0]
    live_peers = {}
    for ln in sh("docker", "exec", c, tool, "show", iface, "dump").decode().splitlines()[1:]:
        f = ln.split("\t")
        if len(f) >= 4:
            live_peers[f[0]] = (f[1], f[3])
    ct_snap = {e["clientId"]: e.get("userData", {}) for e in json.load(open(snap + "clientsTable"))}
    try:
        ct_live = {e["clientId"]: e.get("userData", {})
                   for e in json.loads(sh("docker", "exec", c, "cat", "/opt/amnezia/awg/clientsTable") or b"[]")}
    except ValueError:
        ct_live = {}
    for pe in peers_of(open(snap + conf_name).read()):
        pub, allowed, psk = pe.get("PublicKey"), pe.get("AllowedIPs"), pe.get("PresharedKey", "")
        r["people"] += 1
        lv = live_peers.get(pub)
        u = [x for x in users if ((x.get("peers") or {}).get(prof) or {}).get("pubkey") == pub]
        up = u[0]["peers"][prof] if u else {}
        ok = (bool(lv) and lv[1] == allowed and h(lv[0]) == h(psk) and bool(u)
              and h(up.get("psk", "")) == h(psk) and (up.get("ip", "") + "/32") == allowed
              and ct_live.get(pub, {}).get("clientName") == ct_snap.get(pub, {}).get("clientName"))
        if ok:
            r["ok"] += 1
        else:
            out["broken"].append("%s: peer %s… %s" % (c, (pub or "?")[:8], allowed))
        if ct_live.get(pub, {}) != ct_snap.get(pub, {}):
            out["meta_changed"] += 1
    out["people"] += r["people"]
    out["profiles"][prof] = r
if not out["profiles"]:
    print(json.dumps({"adopted": False}))
    sys.exit(2)
out["intact"] = not out["broken"]
print(json.dumps(out, ensure_ascii=False))
sys.exit(0 if out["intact"] else 1)

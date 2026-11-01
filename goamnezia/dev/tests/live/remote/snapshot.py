#!/usr/bin/env python3
"""snapshot.py <stage> [--no-keys] — secret-free snapshot of a goAmnezia server for the upgrade-path test.

Runs ON the server as root (C2: /root/c2-upgrade/snapshot.py) and prints one JSON document to stdout:
    python3 /root/c2-upgrade/snapshot.py before > /root/c2-upgrade/before.json
    python3 /root/c2-upgrade/snapshot.py after  > /root/c2-upgrade/after.json
    python3 /root/c2-upgrade/compare.py /root/c2-upgrade/before.json /root/c2-upgrade/after.json

Secrets never appear in the output: client/server private keys, PSKs, HeaderProtectionKey and the other
profile params, the panel path, page/share secrets, panel token and password hash are reduced to sha256.
Public keys, IPs, ports, labels and file names are kept as they are.
Only read-only bridge actions are called (user-list, user-key, page-list, share-list, edge-status):
they never trigger state migrations. user-key is called with GOAMNEZIA_WHO=c2-snapshot (audit trail).
Written 2026-09-27 for the goAmnezia pro 0.4.5 → 0.4.6 upgrade-path stand (C2).
"""
import glob
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time

GA = os.environ.get("GOAMNEZIA_DIR", "/opt/goamnezia")
ENV = dict(os.environ, GOAMNEZIA_WHO="c2-snapshot")
STAGE = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
NO_KEYS = "--no-keys" in sys.argv


def run(cmd, timeout=180, input=None):
    try:
        p = subprocess.run(cmd, input=input, capture_output=True, timeout=timeout, env=ENV,
                           shell=isinstance(cmd, str))
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 999, "", "%s: %s" % (type(e).__name__, e)


def h(data):
    if data is None:
        return None
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def hfile(path):
    try:
        with open(path, "rb") as f:
            return h(f.read())
    except FileNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001
        return "error:%s" % type(e).__name__


def jload(path, default=None):
    try:
        with open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return default


def canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def bridge(action, *args):
    rc, out, err = run(["bash", GA + "/install.sh", "--action=" + action] + list(args) + ["--json"], timeout=240)
    lines = [ln for ln in out.strip().splitlines() if ln.strip()]
    try:
        return rc, json.loads(lines[-1]) if lines else None
    except Exception:  # noqa: BLE001
        return rc, None


snap = {"stage": STAGE, "taken_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host": socket.gethostname(), "notes": []}

# ── version / install source ────────────────────────────────────────────────────────────────────────
cfg = jload(GA + "/config.json", {}) or {}
rc, out, _ = run(["bash", GA + "/install.sh", "--version"])
m = re.search(r'GOAMNEZIA_VERSION="([^"]+)"', open(GA + "/lib/common.sh", encoding="utf-8").read()) \
    if os.path.exists(GA + "/lib/common.sh") else None
install_source = {}
try:
    for ln in open(GA + "/.install-source", encoding="utf-8"):
        if "=" in ln:
            k, v = ln.rstrip("\n").split("=", 1)
            install_source[k] = v
except FileNotFoundError:
    pass
snap["version"] = {
    "config_version": cfg.get("version"),
    "code_version": m.group(1) if m else None,
    "cli_version": out.strip(),
    "install_source": install_source,
    "markers": sorted(os.path.basename(p) for p in glob.glob(GA + "/.migrated_*") + glob.glob(GA + "/.preupgrade_*")
                      + glob.glob(GA + "/.changelog_pending")),
    "goamnezia_symlink": os.path.realpath("/usr/local/bin/goamnezia") if os.path.exists("/usr/local/bin/goamnezia") else None,
}

# ── config.json (no secrets: params, panel path → sha256) ───────────────────────────────────────────
PUBLIC_PARAMS = ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "MTU", "PersistentKeepalive")
profiles = {}
for pid, p in sorted((cfg.get("profiles") or {}).items()):
    params = p.get("params") or {}
    profiles[pid] = {
        "line": p.get("line"), "container": p.get("container"), "port": p.get("port"), "subnet": p.get("subnet"),
        "iface": p.get("iface"), "image": p.get("image"), "image_tag": p.get("image_tag"),
        "image_digest": p.get("image_digest"), "preset": p.get("preset"), "installed_at": p.get("installed_at"),
        "enabled": p.get("enabled"), "server_pubkey": p.get("server_pubkey"),
        "params_sha256": h(canon(params)), "params_keys": sorted(params.keys()),
        "params_public": {k: params.get(k) for k in PUBLIC_PARAMS if k in params},
        "other_keys": sorted(k for k in p.keys() if k not in (
            "line", "container", "port", "subnet", "iface", "image", "image_tag", "image_digest", "preset",
            "installed_at", "enabled", "server_pubkey", "params")),
    }
site = dict(cfg.get("site") or {})
panel = dict(cfg.get("panel") or {})
for d, key in ((site, "panel_path"), (panel, "miniapp_path")):
    if key in d:
        d[key + "_sha256"] = h(d.pop(key) or "")
snap["config"] = {
    "schema": cfg.get("schema"), "installed": cfg.get("installed"), "mode": cfg.get("mode"), "lang": cfg.get("lang"),
    "domain": cfg.get("domain"), "server_ip": cfg.get("server_ip"), "endpoint_host": cfg.get("endpoint_host"),
    "client_dns": cfg.get("client_dns"), "site": site, "panel": panel, "options": cfg.get("options"),
    "mcp": cfg.get("mcp"), "pages": cfg.get("pages"), "mirror": cfg.get("mirror"),
    "top_level_keys": sorted(cfg.keys()), "profiles": profiles,
}

# ── docker: containers, images, network ─────────────────────────────────────────────────────────────
# Рассогласование версий Docker (пакет обновлён автообновлением, демон не перезапущен: CLI просит API новее, чем понимает
# демон) — каждая команда docker падала, и снимок писал «контейнера нет» (ложный FAIL на 168, 04.10.2026). Как продукт
# (lib/docker.sh docker_api_pin): версию API берём из ответа самого демона и закрепляем на весь снимок.
if "DOCKER_API_VERSION" not in ENV:
    _rc, _out, _err = run(["docker", "version", "--format", "{{.Server.APIVersion}}"])
    _m = re.search(r"Maximum supported API version is ([0-9]+\.[0-9]+)", _err or "")
    if _rc != 0 and _m:
        ENV["DOCKER_API_VERSION"] = _m.group(1)
containers = {}
for pid, p in sorted(profiles.items()):
    c = p.get("container")
    rc, out, err = run(["docker", "inspect", c])
    if rc != 0:
        containers[c] = {"error": err.strip()[:200]}
        continue
    d = json.loads(out)[0]
    img_rc, img_out, _ = run(["docker", "image", "inspect", d.get("Image", "")])
    img = json.loads(img_out)[0] if img_rc == 0 else {}
    containers[c] = {
        "profile": pid, "id": d.get("Id"), "created": d.get("Created"),
        "started_at": (d.get("State") or {}).get("StartedAt"), "status": (d.get("State") or {}).get("Status"),
        "restart_count": d.get("RestartCount"), "image_id": d.get("Image"), "config_image": (d.get("Config") or {}).get("Image"),
        "image_repo_digests": img.get("RepoDigests"), "image_repo_tags": img.get("RepoTags"),
        "restart_policy": ((d.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name"),
        "privileged": (d.get("HostConfig") or {}).get("Privileged"),
        "log_driver": ((d.get("HostConfig") or {}).get("LogConfig") or {}).get("Type"),
        "port_bindings": (d.get("HostConfig") or {}).get("PortBindings"),
        "networks": {n: v.get("IPAddress") for n, v in ((d.get("NetworkSettings") or {}).get("Networks") or {}).items()},
        "mounts": sorted("%s:%s" % (mm.get("Source"), mm.get("Destination")) for mm in d.get("Mounts") or []),
    }
    # server key files: state copy on the host and the live copy in the container — sha256 only
    state_dir = os.path.join(GA, "state", c)
    names = ("wireguard_server_private_key.key", "wireguard_psk.key", "wireguard_server_public_key.key",
             "awg0.conf", "clientsTable", "start.sh")
    containers[c]["state_files_sha256"] = {n: hfile(os.path.join(state_dir, n)) for n in names}
    rc, out, _ = run(["docker", "exec", c, "sh", "-c",
                      "cd /opt/amnezia/awg && sha256sum wireguard_server_private_key.key wireguard_psk.key "
                      "wireguard_server_public_key.key awg0.conf clientsTable 2>/dev/null; "
                      "sha256sum /opt/amnezia/start.sh 2>/dev/null"])
    live = {}
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) == 2:
            live[os.path.basename(parts[1])] = parts[0]
    containers[c]["container_files_sha256"] = live
    # runtime: public key, port and peers (public keys + allowed IPs only)
    iface = p.get("iface") or "awg0"
    tool = "wg" if iface == "wg0" else "awg"
    rc, pub, _ = run(["docker", "exec", c, tool, "show", iface, "public-key"])
    rc2, port, _ = run(["docker", "exec", c, tool, "show", iface, "listen-port"])
    rc3, allowed, _ = run(["docker", "exec", c, tool, "show", iface, "allowed-ips"])
    peers = sorted(ln.split("\t")[0] + " " + (ln.split("\t")[1] if "\t" in ln else "")
                   for ln in allowed.splitlines() if ln.strip())
    containers[c]["runtime"] = {"public_key": pub.strip(), "listen_port": port.strip(),
                                "public_key_matches_config": pub.strip() == (p.get("server_pubkey") or ""),
                                "peer_count": len(peers), "peers": peers}
    ct = jload(os.path.join(state_dir, "clientsTable"), []) or []
    containers[c]["clients_table"] = sorted(
        "%s %s" % (e.get("clientId"), (e.get("userData") or {}).get("clientName")) for e in ct if isinstance(e, dict))
snap["docker"] = {"containers": containers}
rc, out, _ = run(["docker", "network", "inspect", "amnezia-dns-net"])
if rc == 0:
    n = json.loads(out)[0]
    snap["docker"]["network"] = {"name": n.get("Name"), "id": n.get("Id"), "created": n.get("Created"),
                                 "subnets": [c.get("Subnet") for c in ((n.get("IPAM") or {}).get("Config") or [])]}
rc, out, _ = run(["docker", "--version"])
snap["docker"]["version"] = out.strip()

# ── users (users.json directly) + per-profile key hashes ────────────────────────────────────────────
uj = jload(GA + "/users.json", {}) or {}
users = []
for u in uj.get("users") or []:
    peers = {}
    for prof, pr in sorted((u.get("peers") or {}).items()):
        priv, psk = pr.get("privkey") or "", pr.get("psk") or ""
        peers[prof] = {
            "ip": pr.get("ip"), "pubkey": pr.get("pubkey"), "active": pr.get("active"), "created": pr.get("created"),
            "imported": pr.get("imported"), "has_privkey": bool(priv),
            "privkey_sha256": h(priv) if priv else None, "psk_sha256": h(psk) if psk else None,
            "material_sha256": h("%s|%s|%s|%s" % (pr.get("ip"), priv, pr.get("pubkey"), psk)),
            "other_keys": sorted(k for k in pr.keys() if k not in (
                "ip", "privkey", "pubkey", "psk", "created", "active", "imported", "last_handshake", "rx", "tx")),
        }
    users.append({
        "name": u.get("name"), "label": u.get("label"), "created": u.get("created"), "enabled": u.get("enabled"),
        "quota_bytes": u.get("quota_bytes"), "quota_gb": (u.get("quota_bytes") or 0) / 1073741824,
        "expires": u.get("expires"), "endpoint_ip": u.get("endpoint_ip"), "imported": u.get("imported"),
        "profiles": sorted((u.get("peers") or {}).keys()),
        "traffic_volatile": {k: (u.get("traffic") or {}).get(k) for k in ("rx", "tx")},
        "record_keys": sorted(u.keys()), "peers": peers,
    })
snap["users"] = users
snap["users_json_schema"] = uj.get("schema")

KEY_LINES = ("Address", "PrivateKey", "PublicKey", "PresharedKey", "Endpoint", "AllowedIPs")
keys = {}
if not NO_KEYS:
    for u in users:
        for prof in u["profiles"]:
            rc, js = bridge("user-key", "--name=" + u["name"], "--profile=" + prof, "--format=all")
            entry = {"rc": rc, "status": (js or {}).get("status"), "code": (js or {}).get("code")}
            conf = (js or {}).get("conf")
            vpn = (js or {}).get("vpn")
            if conf:
                pairs = [ln.split("=", 1) for ln in conf.splitlines() if "=" in ln and not ln.lstrip().startswith("#")]
                entry["conf_sha256"] = h(conf)
                entry["conf_core_sha256"] = h("\n".join("%s=%s" % (k.strip(), v.strip()) for k, v in pairs
                                                        if k.strip() in KEY_LINES))
                entry["conf_line_keys"] = [k.strip() for k, _ in pairs]
                ep = [v.strip() for k, v in pairs if k.strip() == "Endpoint"]
                entry["endpoint"] = ep[0] if ep else None
                addr = [v.strip() for k, v in pairs if k.strip() == "Address"]
                entry["address"] = addr[0] if addr else None
            if vpn:
                entry["vpn_sha256"] = h(vpn)
            for k in ("line", "min_client", "disabled", "key_host", "key_endpoint"):
                if js and k in js:
                    entry[k] = js[k]
            keys["%s/%s" % (u["name"], prof)] = entry
    # determinism probe: the same key twice must hash the same
    if users and users[0]["profiles"]:
        u0, p0 = users[0]["name"], users[0]["profiles"][0]
        rc, js = bridge("user-key", "--name=" + u0, "--profile=" + p0, "--format=all")
        k0 = keys.get("%s/%s" % (u0, p0), {})
        snap["notes"].append("determinism %s/%s: conf %s, vpn %s" % (
            u0, p0, "same" if h((js or {}).get("conf")) == k0.get("conf_sha256") else "DIFFERS",
            "same" if h((js or {}).get("vpn")) == k0.get("vpn_sha256") else "DIFFERS"))
snap["user_keys"] = keys

# ── pages, shares, edge ─────────────────────────────────────────────────────────────────────────────
rc, js = bridge("page-list")
pages = []
for pg in ((js or {}).get("pages") or []):
    pages.append({k: v for k, v in pg.items() if not re.search("url|secret", k, re.I)})
snap["pages"] = {"count": len(pages), "items": pages, "dirs": {}}
for d in sorted(glob.glob(GA + "/pages/*")):
    if os.path.isdir(d):
        snap["pages"]["dirs"][os.path.basename(d)] = {os.path.basename(f): hfile(f) for f in sorted(glob.glob(d + "/*"))}
rc, js = bridge("share-list")
shares = [{k: v for k, v in s.items() if not re.search("url|secret|path|token", k, re.I)}
          for s in ((js or {}).get("shares") or (js or {}).get("items") or [])]
snap["shares"] = {"count": len(shares), "items": shares, "bridge_keys": sorted((js or {}).keys()),
                  "note": "a share lives 5 minutes (one-shot listener); count 0 later is expected"}
rc, js = bridge("edge-status")
snap["edge"] = {"count": (js or {}).get("count"), "ips": [i.get("ip") for i in ((js or {}).get("ips") or [])],
                "keys_host": (js or {}).get("keys_host")}

# ── systemd ─────────────────────────────────────────────────────────────────────────────────────────
rc, out, _ = run("systemctl list-units --all --plain --no-legend --no-pager 'goamnezia*'")
units = {}
for ln in out.splitlines():
    parts = ln.split(None, 4)
    if len(parts) >= 4:
        units[parts[0]] = {"load": parts[1], "active": parts[2], "sub": parts[3]}
rc, out, _ = run("systemctl list-unit-files --plain --no-legend --no-pager 'goamnezia*'")
for ln in out.splitlines():
    parts = ln.split()
    if len(parts) >= 2:
        units.setdefault(parts[0], {})["file_state"] = parts[1]
for f in sorted(glob.glob("/etc/systemd/system/goamnezia*")):
    if os.path.isfile(f):
        units.setdefault(os.path.basename(f), {})["unit_file_sha256"] = hfile(f)
snap["systemd"] = units

# ── nginx, certificate, site, panel ─────────────────────────────────────────────────────────────────
domain = cfg.get("domain") or ""
rc, out, err = run(["nginx", "-t"])
snap["nginx"] = {
    "vhost_sha256": hfile("/etc/nginx/sites-available/goamnezia"),
    "sites_enabled": sorted("%s -> %s" % (os.path.basename(p), os.path.realpath(p))
                            for p in glob.glob("/etc/nginx/sites-enabled/*")),
    "nginx_t_rc": rc, "active": run(["systemctl", "is-active", "nginx"])[1].strip(),
}
cert = "/etc/letsencrypt/live/%s/cert.pem" % domain
rc, out, _ = run(["openssl", "x509", "-in", cert, "-noout", "-serial", "-enddate", "-issuer", "-fingerprint", "-sha256"])
snap["certificate"] = {"path": cert, "exists": os.path.exists(cert), "info": out.strip().splitlines(),
                       "renewal_conf_sha256": hfile("/etc/letsencrypt/renewal/%s.conf" % domain)}
site_dir = "/var/www/goamnezia-site"
lines = []
for root, _, files in os.walk(site_dir):
    for fn in files:
        fp = os.path.join(root, fn)
        lines.append("%s  %s" % (hfile(fp), os.path.relpath(fp, site_dir)))
snap["site"] = {"dir": site_dir, "files": len(lines), "tree_sha256": h("\n".join(sorted(lines)))}


def http_code(url, resolve=None):
    cmd = ["curl", "-s", "-o", "/dev/null", "-m", "15", "-w", "%{http_code}"]
    if resolve:
        cmd += ["--resolve", resolve]
    rc, out, _ = run(cmd + [url])
    return out.strip() or ("rc%d" % rc)


mini = (cfg.get("panel") or {}).get("miniapp_path") or ""
snap["http"] = {
    "site_root": http_code("https://%s/" % domain) if domain else None,
    "http_redirect": http_code("http://%s/" % domain) if domain else None,
    "panel_domain": http_code("https://%s/%s/" % (domain, mini.strip("/"))) if domain and mini else None,
    "panel_local": http_code("http://127.0.0.1:%s/" % ((cfg.get("panel") or {}).get("port") or 1985)),
    "note": "codes only; the panel path never leaves the server",
}
snap["panel"] = {
    "admin_dir_files": sorted(os.path.basename(p) for p in glob.glob("/opt/goamnezia-admin/*")),
    "password_json_sha256": hfile("/opt/goamnezia-admin/password.json"),
    "token_sha256": hfile("/opt/goamnezia-admin/token"),
    # SEC50-08 (0.6.0): аварийный ключ — часовой, рядом token.meta со сроком; ключ без meta — прежний постоянный
    "token_meta": os.path.exists("/opt/goamnezia-admin/token.meta"),
    "server_py_sha256": hfile("/opt/goamnezia-admin/server.py"),
}
snap["mcp"] = {"server_py_sha256": hfile("/opt/goamnezia-mcp/server.py"),
               "agents_json_sha256": hfile(GA + "/agents.json")}
snap["bot"] = {"installed": os.path.exists("/opt/goamnezia-bot/bot.py"),
               "env_sha256": hfile("/opt/goamnezia-bot/.env")}

# ── other state files ───────────────────────────────────────────────────────────────────────────────
snap["state_files"] = {os.path.basename(p): hfile(p) for p in (
    GA + "/config.json", GA + "/users.json", GA + "/agents.json", GA + "/stats.json", GA + "/backup_schedule.json",
    GA + "/.language", GA + "/.install-source")}
snap["backup_schedule"] = jload(GA + "/backup_schedule.json")
snap["backups"] = sorted("%s %d" % (os.path.basename(p), os.path.getsize(p)) for p in glob.glob(GA + "/backups/*"))
code = []
for root, dirs, files in os.walk(GA):
    rel = os.path.relpath(root, GA)
    if rel.split(os.sep)[0] in ("state", "backups", "pages", "mirror", "tls", "upstream"):
        continue
    for fn in files:
        fp = os.path.join(root, fn)
        r = os.path.relpath(fp, GA)
        if r in ("config.json", "users.json", "agents.json", "stats.json", "backup_schedule.json") or fn.startswith("."):
            continue
        code.append("%s  %s" % (hfile(fp), r))
snap["code"] = {"files": len(code), "tree_sha256": h("\n".join(sorted(code)))}
snap["logs"] = {os.path.basename(p): sum(1 for _ in open(p, "rb")) for p in glob.glob("/var/log/goamnezia*.log")}

json.dump(snap, sys.stdout, ensure_ascii=False, indent=1, sort_keys=False)
sys.stdout.write("\n")

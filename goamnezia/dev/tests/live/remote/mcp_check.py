#!/usr/bin/env python3
"""mcp_check.py — live check of the MCP HTTP transport ON THE SERVER (root), through the product's own bridge.

Turns the transport on (if it was off), issues two temporary grants (read; write + pin_ip), calls MCP over HTTPS the
way an agent does — through nginx and the real certificate, but resolved to 127.0.0.1, so grant URLs and tokens never
leave this machine (cards live in a 0700 temp dir, the token goes to curl on stdin) — and checks: scopes, token,
pinned IP (a second local source address is refused), idempotent repeated mutations, revoke. Then removes everything
it created and turns the transport off again if it was off. Prints JSON lines {"check","ok","evidence"}; exit 1 on
any failure. Nothing secret is printed.
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

GA = os.environ.get("GOAMNEZIA_DIR", "/opt/goamnezia")
WHO = os.environ.get("GOAMNEZIA_WHO", "live-acceptance")
results = []


def report(check, ok, evidence):
    results.append(ok)
    print(json.dumps({"check": check, "ok": bool(ok), "evidence": re.sub(r"[A-Za-z0-9_+/-]{40,}={0,2}", "<redacted>", evidence)},
                     ensure_ascii=False), flush=True)


def bridge(action, *args, save=None):
    p = subprocess.run(["bash", GA + "/install.sh", "--action=" + action] + list(args) + ["--json"],
                       capture_output=True, env=dict(os.environ, GOAMNEZIA_WHO=WHO), timeout=300)
    line = (p.stdout.decode("utf-8", "replace").strip().splitlines() or [""])[-1]
    if save:
        fd = os.open(save, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(line)
    try:
        return json.loads(line)
    except ValueError:
        return {}


def fh(path):
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
    except OSError:
        return "-"


def probe(card, method, params=None, src=None, token="ok"):
    """HTTP code + secret-free summary of one JSON-RPC call."""
    c = json.load(open(card))
    url, tok = c.get("url") or "", c.get("token") or ""
    m = re.match(r"https://([^/:]+)(?::(\d+))?", url)
    host, port = m.group(1), m.group(2) or "443"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    hdr = "Content-Type: application/json\nAccept: application/json, text/event-stream\nMCP-Protocol-Version: 2025-06-18\n"
    if token == "ok":
        hdr += "Authorization: Bearer %s\n" % tok
    elif token == "wrong":
        hdr += "Authorization: Bearer %s\n" % ("x" * 43)
    cmd = ["curl", "-s", "-m", "60", "-o", "-", "-w", "\n%{http_code}", "--resolve", "%s:%s:127.0.0.1" % (host, port),
           "-H", "@-", "--data-binary", body, url]
    if src:
        cmd[1:1] = ["--interface", src]
    out = subprocess.run(cmd, input=hdr.encode(), capture_output=True, timeout=90).stdout.decode("utf-8", "replace")
    text, code = out.rsplit("\n", 1) if "\n" in out else ("", out)
    try:
        j = json.loads(text)
    except ValueError:
        return code, {"raw404": "404 Not Found" in text}
    return code, j


def tool(card, name, args=None, src=None):
    code, j = probe(card, "tools/call", {"name": name, "arguments": args or {}}, src=src)
    if "error" in j:
        return code, "rpc_error", j["error"].get("code")
    r = j.get("result") or {}
    t = " ".join(x.get("text", "") for x in r.get("content", []) if isinstance(x, dict))
    try:
        tj = json.loads(t)
    except ValueError:
        tj = {}
    return code, ("error" if r.get("isError") else "ok"), tj.get("code")


def main():
    st = bridge("mcp-http-status")
    was_on = bool(st.get("enabled"))
    vhost = "/etc/nginx/sites-available/goamnezia"
    vhost_before = fh(vhost)
    work = tempfile.mkdtemp(prefix="ga-mcp-", dir="/root")
    os.chmod(work, 0o700)
    tmp_user = "live-mcp-" + secrets.token_hex(3)
    ids = []
    try:
        if not was_on:
            r = bridge("mcp-http-on")
            report("mcp-http-on", r.get("status") == "success" and r.get("unit_state") == "active",
                   "status=%s unit=%s nginx_location=%s" % (r.get("status"), r.get("unit_state"), r.get("nginx_location")))
        A, B = os.path.join(work, "a.json"), os.path.join(work, "b.json")
        ra = bridge("agent-add", "--label=live-acc-read", "--scope=read", "--days=1", save=A)
        rb = bridge("agent-add", "--label=live-acc-write", "--scope=write", "--days=1", "--pin_ip=on", save=B)
        ids = [x.get("id") for x in (ra, rb) if x.get("id")]
        if len(ids) != 2:
            report("agent-add", False, "grants not issued: %s / %s" % (ra.get("code"), rb.get("code")))
            return
        # scope
        c1, j1 = probe(A, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "live-acceptance", "version": "1"}})
        c2, j2 = probe(A, "tools/list")
        names = sorted(t["name"] for t in ((j2.get("result") or {}).get("tools") or []))
        c3, s3, _ = tool(A, "server_status")
        c4, s4, e4 = tool(A, "user_add", {"name": tmp_user + "-ro", "profiles": "awg2"})
        exists_ro = any(u.get("name") == tmp_user + "-ro" for u in json.load(open(GA + "/users.json"))["users"])
        report("scope read", c1 == "200" and c2 == "200" and "user_add" not in names and "user_get_key" not in names
               and s3 == "ok" and s4 == "rpc_error" and e4 == -32003 and not exists_ro,
               "initialize %s; tools %d (user_add %s, user_get_key %s); server_status %s; user_add → %s %s; created %s"
               % (c1, len(names), "user_add" in names, "user_get_key" in names, s3, s4, e4, exists_ro))
        cn, _ = probe(A, "tools/list", token="none")
        cw, _ = probe(A, "tools/list", token="wrong")
        report("token required", cn == "404" and cw == "404", "no token → %s, wrong token → %s" % (cn, cw))
        # pinned IP
        cfg = json.load(open(GA + "/config.json"))
        other = cfg.get("server_ip") or ""
        k1, s1, _ = tool(B, "server_status")
        card = bridge("agent-show", "--id=" + ids[1])
        pinned = card.get("pinned_ip") or (card.get("agent") or {}).get("pinned_ip")
        k2, _ = probe(B, "tools/list", src=other) if other else ("skip", {})
        k3, j3 = probe(B, "tools/list")
        n3 = len((j3.get("result") or {}).get("tools") or [])
        report("pin_ip", k1 == "200" and pinned == "127.0.0.1" and k2 == "404" and k3 == "200",
               "first call %s → pinned %s; from %s → %s; from 127.0.0.1 again → %s (%d tools)"
               % (k1, pinned, other or "-", k2, k3, n3))
        # idempotent mutations
        uj = GA + "/users.json"
        a1 = tool(B, "user_add", {"name": tmp_user, "label": "live acceptance", "profiles": "awg2"})
        h1 = fh(uj)
        a2 = tool(B, "user_add", {"name": tmp_user, "label": "live acceptance", "profiles": "awg2"})
        h2 = fh(uj)
        q1 = tool(B, "user_set_quota", {"name": tmp_user, "quota_gb": 5})
        h3 = fh(uj)
        q2 = tool(B, "user_set_quota", {"name": tmp_user, "quota_gb": 5})
        h4 = fh(uj)
        n = sum(1 for u in json.load(open(uj))["users"] if u.get("name") == tmp_user)
        report("idempotent mutation", a1[1] == "ok" and a2[1] == "error" and a2[2] == "user_exists" and h1 == h2
               and q1[1] == "ok" and q2[1] == "ok" and h3 == h4 and n == 1,
               "user_add %s, again %s/%s (users.json %s); set_quota %s, again %s (users.json %s); copies of user: %d"
               % (a1[1], a2[1], a2[2], "same" if h1 == h2 else "CHANGED", q1[1], q2[1],
                  "same" if h3 == h4 else "CHANGED", n))
        # revoke
        bridge("agent-disable", "--id=" + ids[0])
        d1, _ = probe(A, "tools/list")
        bridge("agent-enable", "--id=" + ids[0])
        d2, _ = probe(A, "tools/list")
        bridge("agent-del", "--id=" + ids[1])
        d3, _ = probe(B, "tools/list")
        bridge("agent-del", "--id=" + ids[0])
        d4, _ = probe(A, "tools/list")
        ids = []
        report("revoke", (d1, d2, d3, d4) == ("404", "200", "404", "404"),
               "disabled → %s, enabled → %s, deleted write → %s, deleted read → %s" % (d1, d2, d3, d4))
    finally:
        for i in ids:
            bridge("agent-del", "--id=" + i)
        if any(u.get("name") == tmp_user for u in json.load(open(GA + "/users.json"))["users"]):
            bridge("user-del", "--name=" + tmp_user)
        shutil.rmtree(work, ignore_errors=True)
        if not was_on:
            r = bridge("mcp-http-off")
            s = bridge("mcp-http-status")
            report("mcp-http-off (restored)", r.get("status") == "success" and not s.get("enabled")
                   and fh(vhost) == vhost_before,
                   "enabled=%s unit=%s vhost %s" % (s.get("enabled"), s.get("unit_state"),
                                                   "as before" if fh(vhost) == vhost_before else "CHANGED"))


main()
sys.exit(0 if results and all(results) else 1)

#!/usr/bin/env python3
"""backup_check.py [probe-user] — real backup → change → restore ON THE SERVER (root), through the bridge.

snapshot.py before → backup-create → harmless change (a temporary user; +1 GB quota for probe-user if given) →
backup-restore of that very archive → snapshot.py after → compare.py. Restore re-creates the containers from the
saved state by design, so new container ids/created are WARN, not FAIL. Prints JSON lines {"check","ok","evidence"}
without secrets; exit 1 on failure. The snapshots stay in a 0700 temp dir that is removed at the end.
"""
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile

GA = os.environ.get("GOAMNEZIA_DIR", "/opt/goamnezia")
HERE = os.path.dirname(os.path.abspath(__file__))
ENV = dict(os.environ, GOAMNEZIA_WHO=os.environ.get("GOAMNEZIA_WHO", "live-acceptance"))
ok_all = []


def report(check, ok, evidence):
    ok_all.append(ok)
    print(json.dumps({"check": check, "ok": bool(ok), "evidence": evidence}, ensure_ascii=False), flush=True)


def bridge(action, *args):
    p = subprocess.run(["bash", GA + "/install.sh", "--action=" + action] + list(args) + ["--json"],
                       capture_output=True, env=ENV, timeout=600)
    try:
        return json.loads((p.stdout.decode("utf-8", "replace").strip().splitlines() or ["{}"])[-1])
    except ValueError:
        return {}


def snap(path, stage):
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        subprocess.run(["python3", os.path.join(HERE, "snapshot.py"), stage], stdout=f, env=ENV, timeout=900)


def users():
    return {u["name"]: u for u in json.load(open(GA + "/users.json"))["users"]}


probe = sys.argv[1] if len(sys.argv) > 1 else ""
work = tempfile.mkdtemp(prefix="ga-bk-", dir="/root")
os.chmod(work, 0o700)
tmp_user = "live-bk-" + secrets.token_hex(3)
try:
    cfg = json.load(open(GA + "/config.json"))
    prof = sorted((cfg.get("profiles") or {}).keys())[0]
    pre, post = os.path.join(work, "pre.json"), os.path.join(work, "post.json")
    snap(pre, "pre-restore")
    b = bridge("backup-create")
    name = b.get("file") or ""
    report("backup-create", b.get("status") == "success" and bool(name), "archive %s" % (name or b.get("code")))
    if not name:
        sys.exit(1)
    q_before = (users().get(probe) or {}).get("quota_bytes")
    a = bridge("user-add", "--name=" + tmp_user, "--label=live backup probe", "--profiles=" + prof)
    q = bridge("user-quota", "--name=" + probe, "--quota_gb=%d" % ((q_before or 0) // 1073741824 + 1)) if probe in users() else {}
    changed = tmp_user in users() and (not probe or users()[probe].get("quota_bytes") != q_before)
    report("change before restore", a.get("status") == "success" and changed,
           "user %s added on %s%s" % (tmp_user, prof, ("; %s quota +1 GB (%s)" % (probe, q.get("status"))) if probe else ""))
    r = bridge("backup-restore", "--file=" + name)
    report("backup-restore", r.get("status") == "success",
           "status %s, kept_current=%s, server_ip_changed=%s" % (r.get("status") or r.get("code"),
                                                                 r.get("kept_current"), r.get("server_ip_changed")))
    snap(post, "post-restore")
    cmp = subprocess.run(["python3", os.path.join(HERE, "compare.py"), pre, post], capture_output=True, text=True)
    lines = cmp.stdout.splitlines()
    fails = [ln for ln in lines if ln.startswith("FAIL")]
    warns = [ln.split("—")[0].strip() for ln in lines if ln.startswith("WARN")]
    unexpected = [w for w in warns if not any(k in w for k in ("id (not re-created)", "created", "backup schedule"))]
    report("state after restore", not fails and not unexpected and tmp_user not in users()
           and (not probe or users()[probe].get("quota_bytes") == q_before),
           "%s; temp user gone=%s%s; FAIL: %s; unexpected WARN: %s" % (
               (lines[-1] if lines else "no compare output"), tmp_user not in users(),
               ("; %s quota back=%s" % (probe, users()[probe].get("quota_bytes") == q_before)) if probe else "",
               "; ".join(f[:100] for f in fails[:3]) or "-", "; ".join(unexpected[:3]) or "-"))
    units = ["nginx"] + [u for u in ("goamnezia-admin", "goamnezia-pages", "goamnezia-bot")
                         if subprocess.run(["systemctl", "cat", u], capture_output=True).returncode == 0]
    act = {u: subprocess.run(["systemctl", "is-active", u], capture_output=True, text=True).stdout.strip() for u in units}
    failed = subprocess.run("systemctl --failed --no-legend --plain | grep -c goamnezia", shell=True,
                            capture_output=True, text=True).stdout.strip()
    running = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
    want = [p.get("container") for p in (cfg.get("profiles") or {}).values()]
    report("services after restore", all(v == "active" for v in act.values()) and failed in ("0", "")
           and all(c in running for c in want),
           "%s; failed goamnezia units %s; containers up %d/%d" % (
               ", ".join("%s %s" % kv for kv in act.items()), failed or "0",
               sum(1 for c in want if c in running), len(want)))
finally:
    if tmp_user in users():
        bridge("user-del", "--name=" + tmp_user)
    shutil.rmtree(work, ignore_errors=True)
sys.exit(0 if ok_all and all(ok_all) else 1)

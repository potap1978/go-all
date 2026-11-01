#!/usr/bin/env python3
"""compare.py <before.json> <after.json> — upgrade-path verdict for the C2 stand (goAmnezia 0.4.5 → 0.4.6).

Both files come from snapshot.py (secret-free: hashes only). Prints one line per check:
  PASS  — unchanged where it must be unchanged
  FAIL  — something the upgrade must never change did change or disappear (keys, people, pages, panel path…)
  WARN  — changed, may be intended by the new release (vhost re-rendered, conf format, image tag…) — read it
  INFO  — expected to move (version, code, volatile counters, the 5-minute share)
Exit code 1 if any FAIL. Usage (on C2 or anywhere with both files):
  python3 compare.py before.json after.json [--expect-version 0.4.6]
"""
import json
import sys

args = [a for a in sys.argv[1:] if not a.startswith("--")]
expect_version = None
if "--expect-version" in sys.argv:
    expect_version = sys.argv[sys.argv.index("--expect-version") + 1]
    args = [a for a in args if a != expect_version]
B = json.load(open(args[0], encoding="utf-8"))
A = json.load(open(args[1], encoding="utf-8"))
counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "INFO": 0}


def say(level, check, detail=""):
    counts[level] += 1
    print("%-4s  %s%s" % (level, check, ("  — " + detail) if detail else ""))


def get(d, path, default=None):
    for p in path.split("."):
        if isinstance(d, dict) and p in d:
            d = d[p]
        else:
            return default
    return d


def same(check, path, level_if_diff="FAIL"):
    b, a = get(B, path), get(A, path)
    if b == a:
        say("PASS", check)
    else:
        say(level_if_diff, check, "before=%s after=%s" % (json.dumps(b, ensure_ascii=False)[:160],
                                                         json.dumps(a, ensure_ascii=False)[:160]))


# ── version ──────────────────────────────────────────────────────────────────────────────────────────
v_b, v_a = get(B, "version.config_version"), get(A, "version.config_version")
code_a = get(A, "version.code_version")
say("INFO", "version", "%s → config %s / code %s / cli '%s'" % (v_b, v_a, code_a, get(A, "version.cli_version")))
if v_a != code_a:
    say("FAIL", "config.version follows the code", "config %s, code %s (migration did not run? do the menu pass)" % (v_a, code_a))
if expect_version and code_a != expect_version:
    say("FAIL", "code version", "expected %s, got %s" % (expect_version, code_a))
say("INFO", "install source", "before %s → after %s" % (get(B, "version.install_source"), get(A, "version.install_source")))
say("INFO", "markers", "after %s" % get(A, "version.markers"))

# ── config ───────────────────────────────────────────────────────────────────────────────────────────
for k in ("installed", "mode", "domain", "server_ip", "endpoint_host", "client_dns", "lang"):
    same("config.%s" % k, "config.%s" % k)
for k in ("cert", "template_id", "template_source", "listen", "mcp_location", "panel_path_sha256"):
    if get(B, "config.site.%s" % k) is not None or get(A, "config.site.%s" % k) is not None:
        same("config.site.%s" % k, "config.site.%s" % k)
for k in ("port", "miniapp_path_sha256", "miniapp_auto", "trust_tunnel"):
    same("config.panel.%s" % k, "config.panel.%s" % k)
same("config.options", "config.options", "WARN")
same("config.mcp", "config.mcp", "WARN")
added = sorted(set(get(A, "config.top_level_keys") or []) - set(get(B, "config.top_level_keys") or []))
removed = sorted(set(get(B, "config.top_level_keys") or []) - set(get(A, "config.top_level_keys") or []))
if removed:
    say("FAIL", "config top-level keys removed", ", ".join(removed))
if added:
    say("INFO", "config top-level keys added", ", ".join(added))

# ── profiles ─────────────────────────────────────────────────────────────────────────────────────────
pb, pa = get(B, "config.profiles") or {}, get(A, "config.profiles") or {}
if sorted(pb) != sorted(pa):
    say("FAIL", "profile set", "before %s after %s" % (sorted(pb), sorted(pa)))
for pid in sorted(pb):
    if pid not in pa:
        continue
    for k in ("line", "container", "port", "subnet", "iface", "preset", "installed_at", "enabled", "server_pubkey",
              "params_sha256"):
        same("profile %s.%s" % (pid, k), "config.profiles.%s.%s" % (pid, k))
    for k in ("image", "image_tag", "image_digest"):
        same("profile %s.%s" % (pid, k), "config.profiles.%s.%s" % (pid, k), "WARN")

# ── containers: not re-created, server keys intact ──────────────────────────────────────────────────
cb, ca = get(B, "docker.containers") or {}, get(A, "docker.containers") or {}
for c in sorted(cb):
    if c not in ca or "error" in ca[c]:
        say("FAIL", "container %s present" % c, str(ca.get(c)))
        continue
    same("container %s id (not re-created)" % c, "docker.containers.%s.id" % c, "WARN")
    same("container %s created" % c, "docker.containers.%s.created" % c, "WARN")
    same("container %s started_at (not restarted)" % c, "docker.containers.%s.started_at" % c, "INFO")
    same("container %s status" % c, "docker.containers.%s.status" % c)
    same("container %s image" % c, "docker.containers.%s.image_id" % c, "WARN")
    same("container %s port bindings" % c, "docker.containers.%s.port_bindings" % c)
    # file names contain dots, so these are looked up directly, not through get()
    for where in ("state_files_sha256", "container_files_sha256"):
        for f in ("wireguard_server_private_key.key", "wireguard_psk.key", "wireguard_server_public_key.key",
                  "awg0.conf", "clientsTable"):
            b = (cb[c].get(where) or {}).get(f)
            a = (ca[c].get(where) or {}).get(f)
            lvl = "FAIL" if f.endswith(".key") else "WARN"
            if b == a:
                say("PASS", "container %s %s %s" % (c, where.split("_")[0], f))
            else:
                say(lvl, "container %s %s %s" % (c, where.split("_")[0], f), "hash changed")
    rb, ra = cb[c].get("runtime") or {}, ca[c].get("runtime") or {}
    if rb.get("public_key") == ra.get("public_key") and ra.get("public_key_matches_config"):
        say("PASS", "container %s live public key = config" % c)
    else:
        say("FAIL", "container %s live public key" % c, "before %s after %s match_config=%s" % (
            rb.get("public_key"), ra.get("public_key"), ra.get("public_key_matches_config")))
    if sorted(rb.get("peers") or []) == sorted(ra.get("peers") or []):
        say("PASS", "container %s live peers (%d)" % (c, len(ra.get("peers") or [])))
    else:
        say("FAIL", "container %s live peers" % c, "before %d after %d" % (len(rb.get("peers") or []), len(ra.get("peers") or [])))
    if sorted(cb[c].get("clients_table") or []) == sorted(ca[c].get("clients_table") or []):
        say("PASS", "container %s clientsTable entries" % c)
    else:
        say("WARN", "container %s clientsTable entries" % c, "differ")

# ── users ────────────────────────────────────────────────────────────────────────────────────────────
ub = {u["name"]: u for u in B.get("users") or []}
ua = {u["name"]: u for u in A.get("users") or []}
if sorted(ub) != sorted(ua):
    say("FAIL", "user set", "missing %s, new %s" % (sorted(set(ub) - set(ua)), sorted(set(ua) - set(ub))))
for n in sorted(ub):
    if n not in ua:
        continue
    for k in ("label", "enabled", "quota_bytes", "expires", "endpoint_ip", "imported", "profiles", "created"):
        if ub[n].get(k) == ua[n].get(k):
            say("PASS", "user %s.%s" % (n, k))
        else:
            say("FAIL", "user %s.%s" % (n, k), "before %r after %r" % (ub[n].get(k), ua[n].get(k)))
    for prof, pr in sorted((ub[n].get("peers") or {}).items()):
        pa_ = (ua[n].get("peers") or {}).get(prof)
        if not pa_:
            say("FAIL", "user %s peer %s" % (n, prof), "missing after")
            continue
        for k in ("ip", "pubkey", "privkey_sha256", "psk_sha256", "material_sha256", "active", "has_privkey"):
            if pr.get(k) == pa_.get(k):
                say("PASS", "user %s/%s.%s" % (n, prof, k))
            else:
                say("FAIL", "user %s/%s.%s" % (n, prof, k), "changed")

# ── issued keys (.conf / vpn://) ─────────────────────────────────────────────────────────────────────
kb, ka = B.get("user_keys") or {}, A.get("user_keys") or {}
for k in sorted(kb):
    b, a = kb[k], ka.get(k)
    if b.get("status") != "success":
        # Ключ и раньше не выдавался (у подхваченного у приложения человека приватного ключа на сервере нет —
        # no_private_key): сравнивать нечего, важно лишь, что ответ тот же.
        same_refusal = bool(a) and (a.get("status"), a.get("code")) == (b.get("status"), b.get("code"))
        say("PASS" if same_refusal else "WARN", "key %s not issuable before (%s)" % (k, b.get("code")),
            "" if same_refusal else "now %s/%s" % ((a or {}).get("status"), (a or {}).get("code")))
        continue
    if not a or a.get("status") != "success":
        say("FAIL", "key %s issued after upgrade" % k, str(a)[:160])
        continue
    if b.get("conf_sha256") == a.get("conf_sha256"):
        say("PASS", "key %s .conf identical" % k)
    elif b.get("conf_core_sha256") == a.get("conf_core_sha256"):
        say("WARN", "key %s .conf changed, key material identical" % k,
            "line keys before %s after %s" % (b.get("conf_line_keys"), a.get("conf_line_keys")))
    else:
        say("FAIL", "key %s .conf key material changed" % k)
    if b.get("vpn_sha256") == a.get("vpn_sha256"):
        say("PASS", "key %s vpn:// identical" % k)
    else:
        say("WARN", "key %s vpn:// changed" % k, "the app key string differs (format change?)")
    if b.get("endpoint") != a.get("endpoint"):
        say("FAIL", "key %s endpoint" % k, "%s → %s" % (b.get("endpoint"), a.get("endpoint")))

# ── pages, shares ────────────────────────────────────────────────────────────────────────────────────
pgb = {p["id"]: p for p in get(B, "pages.items") or []}
pga = {p["id"]: p for p in get(A, "pages.items") or []}
for pid, p in sorted(pgb.items()):
    q = pga.get(pid)
    if not q:
        say("FAIL", "page %s survives" % pid, "gone after upgrade")
        continue
    for k in ("user", "ttl", "forever", "expires", "profiles", "mirror", "created"):
        if p.get(k) == q.get(k):
            say("PASS", "page %s.%s" % (pid, k))
        else:
            say("FAIL", "page %s.%s" % (pid, k), "%r → %r" % (p.get(k), q.get(k)))
    say("INFO", "page %s visits" % pid, "%s → %s" % (p.get("visits"), q.get("visits")))
    if (get(B, "pages.dirs") or {}).get(pid) == (get(A, "pages.dirs") or {}).get(pid):
        say("PASS", "page %s files (secret, meta) unchanged" % pid)
    else:
        say("WARN", "page %s files" % pid, "hashes differ (state.json/visits counters are expected to move)")
say("INFO", "shares", "before %s, after %s (a share lives 5 minutes; 0 after is expected)" % (
    get(B, "shares.count"), get(A, "shares.count")))

# ── services, nginx, certificate, site, panel ────────────────────────────────────────────────────────
sb, sa = B.get("systemd") or {}, A.get("systemd") or {}
for u in sorted(sb):
    if u not in sa:
        say("FAIL", "unit %s present" % u, "gone after upgrade")
        continue
    if u.endswith(".timer") or u in ("goamnezia-admin.service", "goamnezia-pages.service",
                                     "goamnezia-bot.service", "goamnezia-mcp-http.service"):
        if sb[u].get("active") == sa[u].get("active") and sb[u].get("file_state") == sa[u].get("file_state"):
            say("PASS", "unit %s %s/%s" % (u, sa[u].get("active"), sa[u].get("file_state")))
        else:
            say("FAIL", "unit %s" % u, "%s/%s → %s/%s" % (sb[u].get("active"), sb[u].get("file_state"),
                                                          sa[u].get("active"), sa[u].get("file_state")))
    if sb[u].get("unit_file_sha256") != sa[u].get("unit_file_sha256"):
        say("INFO", "unit file %s" % u, "rewritten by the new version")
for u in sorted(set(sa) - set(sb)):
    say("INFO", "new unit %s" % u, "%s/%s" % (sa[u].get("active"), sa[u].get("file_state")))
same("nginx vhost", "nginx.vhost_sha256", "WARN")
if get(A, "nginx.nginx_t_rc") == 0 and get(A, "nginx.active") == "active":
    say("PASS", "nginx -t and active")
else:
    say("FAIL", "nginx", "nginx -t rc %s, %s" % (get(A, "nginx.nginx_t_rc"), get(A, "nginx.active")))
same("certificate (not re-issued)", "certificate.info", "WARN")
same("site template files", "site.tree_sha256", "WARN")
for k, want in (("site_root", "200"), ("http_redirect", "301"), ("panel_domain", "200"), ("panel_local", "200")):
    if get(A, "http.%s" % k) == want:
        say("PASS", "http %s %s" % (k, want))
    elif get(A, "http.%s" % k) is None and get(B, "http.%s" % k) is None:
        # Lite без домена: сайта и панели на домене нет ни до, ни после — сравнивать нечего (снимок пишет null).
        say("PASS", "http %s" % k, "not applicable: no domain before and after")
    else:
        say("FAIL", "http %s" % k, "want %s, got %s (before %s)" % (want, get(A, "http.%s" % k), get(B, "http.%s" % k)))
same("panel password hash kept", "panel.password_json_sha256")
# SEC50-08 (0.6.0, решение владельца 28.09): постоянного аварийного ключа больше нет. Старый ключ без token.meta установка
# 0.6.0 удаляет (панель его и так не примет): после обновления на 0.6.0+ его на диске быть НЕ должно. Ключ с token.meta —
# часовой из SSH-меню и сгорает при входе: пропал или сменился — не потеря. До 0.6.0 — как раньше, ключ обязан остаться.
def _vt(v):
    try:
        return tuple(int(x) for x in str(v or "").split("-")[0].split(".")[:3])
    except ValueError:
        return ()


tok_b, tok_a = get(B, "panel.token_sha256"), get(A, "panel.token_sha256")
if _vt(code_a) >= (0, 6, 0):
    if tok_b and not get(B, "panel.token_meta"):
        say("PASS" if not tok_a else "FAIL", "panel legacy emergency token removed (SEC50-08)",
            "removed by the 0.6.0 install" if not tok_a else "a permanent key (no token.meta) is still on disk")
    elif tok_a and not get(A, "panel.token_meta"):
        say("FAIL", "panel: no permanent emergency token (SEC50-08)", "a key without token.meta is on disk")
    elif tok_b != tok_a:
        say("INFO", "panel emergency token", "one-hour key (token.meta) gone or replaced — it burns on login")
    else:
        say("PASS", "panel emergency token", "no permanent key; one-hour key %s" % ("present" if tok_a else "absent"))
else:
    same("panel emergency token kept", "panel.token_sha256")
same("backup schedule", "backup_schedule", "WARN")
say("INFO", "backups", "before %s → after %s" % (get(B, "backups"), get(A, "backups")))
say("INFO", "code tree", "%s files → %s files, hash %s" % (
    get(B, "code.files"), get(A, "code.files"),
    "unchanged (upgrade NOT applied?)" if get(B, "code.tree_sha256") == get(A, "code.tree_sha256") else "changed"))
print("\nsummary: " + ", ".join("%s %d" % (k, v) for k, v in counts.items()))
sys.exit(1 if counts["FAIL"] else 0)

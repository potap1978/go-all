#!/usr/bin/env python3
"""Живая приёмка goAmnezia pro одной командой (запуск с машины разработчика, не с сервера).

Сервер (--server) — установленный goAmnezia; клиент (--client) — другая машина с Docker, откуда подключается
настоящий клиент AmneziaWG в одноразовом --privileged контейнере (своя сетевая namespace, маршруты хоста не
трогаются). SSH только по ключу, одним общим каналом на прогон и с повтором при обрыве (ssh_link.py, TOOL-01):
обрыв до начала команды повторяется всегда, посреди — только у чтения, проверок и заливки файлов. Секреты не
печатаются: ключи идут трубой сервер → клиент и не сохраняются, адреса страниц читаются из локальных файлов 0600
вне git, токены MCP не покидают сервер. Итог — короткая таблица «проверка → OK/FAIL/SKIP → доказательство»; код
выхода 1, если есть FAIL. Подробности — tests/live/README.md.

    python3 tests/live/acceptance.py --server root@203.0.113.20 --client root@198.51.100.10 \\
        --user upg-1 --disabled-user upg-4 --page-user upg-1 --page-url-file <0600-файл> \\
        --checks conn,disabled,page,newkey,adopted,backup,mcp,snapshot --snapshot-compare before

Эксплуатационные проверки (вместо критика-сисадмина) — только по имени, опасные ещё и с флагом:

    python3 tests/live/acceptance.py --server root@203.0.113.20 --client root@198.51.100.10 --user upg-1 \
        --checks reboot,disk-full,logrotate,cert-renew,update-rollback --allow-disk-full --allow-update-rollback

Модуль ядра AmneziaWG (этап Б): программа → kmod-install → модуль → программа → модуль → как было, на настоящих
клиентах временного человека; ключи те же, клиенты возвращаются сами, журнал ядра чист — тоже только по имени:

    python3 tests/live/acceptance.py --server root@203.0.113.20 --client root@198.51.100.10 --checks kmod

Ключ SSH у сервера и клиента разный — --key для сервера, --client-key для клиента (без него клиент идёт с --key):

    python3 tests/live/acceptance.py --server root@203.0.113.20 --key ~/.ssh/stand_key \\
        --client root@198.51.100.10 --client-key ~/.ssh/client_key --checks kmod
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ssh_link  # noqa: E402 — рядом с этим файлом, не пакет

REMOTE_DIR = "/root/ga-live"
ACTIONS_JSON = os.path.join(HERE, "..", "..", "lib", "actions.json")
# Режимы ops_check.py, которые только смотрят: их можно повторить и после обрыва посреди вызова.
OPS_READ = ("health", "pre-reboot", "wait-boot", "selfupdate-view")
SERVER_FILES = ("snapshot.py", "compare.py", "adopted_check.py", "mcp_check.py", "backup_check.py", "ops_check.py",
                "kmod_server.py")
CLIENT_FILES = ("awg_client_test.sh", "iso_pair_test.sh", "kmod_client.sh")
SECRET_ACTIONS = {"user-key", "page-create", "page-url", "share-create", "agent-add", "agent-rotate", "agent-harden"}
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
      "Version/18.0 Mobile/15E148 Safari/604.1")
ALL_CHECKS = "conn,disabled,page,newkey,adopted,isolation,backup,mcp,bridge,share,snapshot"
# Эксплуатационные проверки (вместо критика-сисадмина, docs/REDESIGN_050.md п. 6) — только по имени в --checks;
# disk-full и update-rollback — ещё и с явным флагом --allow-….
OPS_CHECKS = "reboot,disk-full,logrotate,cert-renew,update-rollback,upgrade,selfupdate"
# Модуль ядра AmneziaWG (этап Б) ставит на стенд пакеты и модуль и перезапускает профили — тоже только по имени.
NAMED_CHECKS = OPS_CHECKS + ",kmod"
CYR = re.compile("[А-Яа-яЁё]")
ROWS = []


def clean(text):
    text = re.sub(r"(https?://[^/\s]+)/\S*", r"\1/<path>", str(text))
    text = re.sub(r"vpn://\S+", "vpn://<redacted>", text)
    return re.sub(r"[A-Za-z0-9+/_-]{24,}={0,2}",
                  lambda m: m.group(0) if re.fullmatch(r"goamnezia_backup_\d{8}_\d{6}", m.group(0)) else "<redacted>", text)


def row(item, result, evidence=""):
    ROWS.append((item, result, clean(evidence)))
    print("  %-4s %s — %s" % (result, item, clean(evidence)), flush=True)


class Host(ssh_link.Host):
    """Машина прогона: ssh через общий канал с повтором (ssh_link.Host — run, pipe, argv) и заливка проверок.
    run(…, safe=True) — для чтения, проверок и заливки: их можно повторить и после обрыва посреди команды."""

    def upload(self, names):
        for n in names:
            with open(os.path.join(HERE, "remote", n), "rb") as f:
                rc, _, err = self.run("umask 077; mkdir -p %s/snapshots && cat > %s/%s && chmod 700 %s/%s"
                                      % (REMOTE_DIR, REMOTE_DIR, n, REMOTE_DIR, n), input=f.read(), safe=True)
            if rc != 0:
                raise SystemExit("upload %s to %s failed: %s" % (n, self.target, err.strip()[:200]))


_READONLY = None


def readonly(action):
    """Только ли читает действие моста (поле readonly в lib/actions.json): такое повторяется и после обрыва посреди
    вызова; изменяющее — только если до команды не дошло."""
    global _READONLY
    if _READONLY is None:
        try:
            with open(ACTIONS_JSON, encoding="utf-8") as f:
                _READONLY = {k for k, v in json.load(f)["actions"].items() if v.get("readonly")}
        except (OSError, ValueError, KeyError):
            _READONLY = set()
    return action in _READONLY


def bridge(server, action, *args, env="", timeout=900):
    if action in SECRET_ACTIONS:
        raise ValueError("secret action %s must not be read by the harness" % action)
    cmd = "%sGOAMNEZIA_WHO=live-acceptance bash /opt/goamnezia/install.sh --action=%s %s --json 2>/dev/null | tail -1" % (
        env + " " if env else "", action, " ".join("'%s'" % a.replace("'", "'\\''") for a in args))
    rc, out, _ = server.run(cmd, timeout=timeout, safe=readonly(action))
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}


def conf_cmd(user, profile):
    return ("GOAMNEZIA_WHO=live-acceptance bash /opt/goamnezia/install.sh --action=user-key --name='%s' --profile=%s "
            "--format=conf --json 2>/dev/null | tail -1 | jq -r '.conf // empty'" % (user, profile))


def client_test(ctx, tag, source=None, data=None, expect_exit=True, hs_timeout=30):
    """Настоящее подключение: конфиг командой source на сервере (трубой сервер → клиент) или из data идёт в
    клиентский тест. Проверка повторяемая (свой одноразовый контейнер на каждый запуск) — повтор и после обрыва."""
    env = "GA_CLIENT_IMG=%s HS_TIMEOUT=%d %s" % (ctx.image, hs_timeout,
                                                ("EXPECT_EXIT=%s" % ctx.server_ip) if expect_exit else "")
    cmd = "%s bash %s/awg_client_test.sh - %s" % (env, REMOTE_DIR, tag)
    if source:
        _, out, _ = ctx.server.pipe(source, ctx.client, cmd, timeout=300, safe=True)
    else:
        _, out, _ = ctx.client.run(cmd, input=data, timeout=300, safe=True)
    line = (out.strip().splitlines() or ["FAIL no output"])[-1]
    return line.startswith("OK "), " ".join(w for w in line.split() if w.startswith(("handshake=", "ping=", "exit=", "Endpoint=")))


def user_profiles(server, user):
    u = bridge(server, "user-show", "--name=" + user)
    u = u.get("user") or u
    return u, sorted((u.get("peers") or {}).keys())


def check_file_secret(path):
    st = os.stat(path)
    if st.st_mode & 0o077:
        raise SystemExit("%s must be 0600 (secret)" % path)
    rp = os.path.realpath(path)
    top = subprocess.run(["git", "-C", os.path.dirname(rp), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode == 0:
        tracked = subprocess.run(["git", "-C", top.stdout.strip(), "ls-files", "--error-unmatch", rp], capture_output=True)
        ignored = subprocess.run(["git", "-C", top.stdout.strip(), "check-ignore", "-q", rp])
        if tracked.returncode == 0 or ignored.returncode != 0:
            raise SystemExit("%s is (or could be) in git — keep secret files outside git or ignored" % path)


# ── проверки ────────────────────────────────────────────────────────────────────────────────────────────
def c_conn(ctx):
    """Ключ --user на каждом его профиле; вдобавок — локальные 0600-конфы --conn-conf-file (люди приложения Amnezia,
    чьих ключей на сервере нет: после подхвата они обязаны подключаться как раньше)."""
    if ctx.a.user:
        _, profs = user_profiles(ctx.server, ctx.a.user)
        if not profs:
            row("conn %s" % ctx.a.user, "FAIL", "user not found or has no profiles")
        only = [x for x in (ctx.a.conn_profiles or "").split(",") if x]
        for p in profs:
            if only and p not in only:
                row("conn %s/%s" % (ctx.a.user, p), "SKIP", "not in --conn-profiles (port %s)" % ctx.cfg["profiles"].get(p, {}).get("port"))
                continue
            ok, ev = client_test(ctx, "live-%s-%s" % (ctx.a.user, p), conf_cmd(ctx.a.user, p))
            row("conn %s/%s" % (ctx.a.user, p), "OK" if ok else "FAIL", ev)
    for f in ctx.a.conn_conf_file or []:
        check_file_secret(f)
        with open(f, "rb") as fh:
            data = fh.read()
        ok, ev = client_test(ctx, "live-conf-%s" % re.sub(r"[^A-Za-z0-9-]", "-", os.path.basename(f))[:30], data=data)
        row("conn %s (local conf)" % os.path.basename(f), "OK" if ok else "FAIL", ev)


def c_disabled(ctx):
    u, profs = user_profiles(ctx.server, ctx.a.disabled_user)
    if not profs:
        return row("disabled %s" % ctx.a.disabled_user, "FAIL", "user not found")
    if u.get("enabled") is not False:
        return row("disabled %s" % ctx.a.disabled_user, "FAIL", "user is not disabled (enabled=%s)" % u.get("enabled"))
    cfg = ctx.cfg
    bad, ev = [], []
    for p in profs:
        ok, e = client_test(ctx, "live-disabled-%s" % p, conf_cmd(ctx.a.disabled_user, p),
                            expect_exit=False, hs_timeout=20)
        c = cfg["profiles"][p]["container"]
        tool = "wg" if cfg["profiles"][p].get("iface") == "wg0" else "awg"
        ip = (u.get("peers") or {}).get(p, {}).get("ip", "?")
        _, out, _ = ctx.server.run("docker exec %s %s show %s allowed-ips | grep -c '%s/32'"
                                   % (c, tool, cfg["profiles"][p].get("iface") or "awg0", ip), safe=True)
        live = out.strip() not in ("", "0")
        if ok or live:
            bad.append(p)
        ev.append("%s: %s, live peer %s" % (p, "CONNECTED" if ok else "no handshake", "yes" if live else "no"))
    row("disabled %s cannot connect" % ctx.a.disabled_user, "FAIL" if bad else "OK", "; ".join(ev))


def c_page(ctx):
    if not (ctx.a.page_url_file and ctx.a.page_user):
        return row("page", "SKIP", "no --page-url-file/--page-user")
    check_file_secret(ctx.a.page_url_file)
    url = open(ctx.a.page_url_file).read().strip()
    if not url.endswith("/"):
        url += "/"

    def get(u, method="GET", data=None):
        hdr = {"User-Agent": UA}
        if data is not None:
            hdr["Content-Type"] = "application/json"
        req = urllib.request.Request(u, headers=hdr, method=method, data=data)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.headers.get("Content-Disposition") or "", r.read()
        except urllib.error.HTTPError as e:
            return e.code, "", b""

    def norm(text):
        return hashlib.sha256((text.rstrip("\n") + "\n").encode()).hexdigest()
    code, _, html = get(url)
    _, profs = user_profiles(ctx.server, ctx.a.page_user)
    want = {}
    for p in profs:
        _, srv, _ = ctx.server.run(conf_cmd(ctx.a.page_user, p) + " | python3 -c \"import sys,hashlib; "
                                   "t=sys.stdin.read(); print(hashlib.sha256((t.rstrip(chr(10))+chr(10)).encode()).hexdigest())\"",
                                   safe=True)
        want[p] = srv.strip()

    # Ключи страницы 0.5.0 (SEC50-05): при отрисовке — одноразовое значение reveal в данных страницы, ключи — только
    # POST keys.json с ним (так шлёт app.js после действия человека); GET — 405, повтор значения — 409. Вместе с ключами
    # ответ несёт токен на файл и QR ключа (SEC50-10): key/<профиль>.conf и qr/key/… — только с ?g=<токен>, без него —
    # 404. Ни значение, ни токен, ни ключи не печатаются: сверяются хеши .conf с мостом.
    m = re.search(r'<script type="application/json" id="ga-data">(.*?)</script>', html.decode("utf-8", "replace"), re.S)
    try:
        nonce = (json.loads(m.group(1)) if m else {}).get("reveal")
    except ValueError:
        nonce = None
    grant = None
    if not nonce:
        row("page keys.json (SEC50-05)", "SKIP" if code == 200 else "FAIL",
            "no one-time value in the page data (a page before 0.5.0?)" if code == 200 else "page %s" % code)
    else:
        g, _, _ = get(url + "keys.json")
        body = json.dumps({"nonce": nonce}).encode()
        c1, _, raw = get(url + "keys.json", "POST", body)
        try:
            answer = json.loads(raw.decode("utf-8"))
        except ValueError:
            answer = {}
        keys = answer.get("keys") or {}
        grant = answer.get("grant") if isinstance(answer.get("grant"), str) else None
        match = [p for p in profs if isinstance(keys.get(p), dict) and norm(str(keys[p].get("conf") or "")) == want.get(p)]
        vpn = sum(1 for p in profs if str((keys.get(p) or {}).get("vpn") or "").startswith("vpn://"))
        c2, _, _ = get(url + "keys.json", "POST", body)
        ok = g == 405 and c1 == 200 and len(match) == len(profs) and vpn == len(profs) and c2 == 409
        row("page keys.json (SEC50-05)", "OK" if ok else "FAIL",
            "GET %s; POST with the page's one-time value %s: conf = bridge %d/%d, vpn:// %d/%d; same value again %s" % (
                g, c1, len(match), len(profs), vpn, len(profs), c2))

    # Файл .conf каждого профиля: с токеном из ответа keys.json (0.5.0 с SEC50-10) или, у страницы без токена, как раньше
    q = "?g=" + urllib.parse.quote(grant, safe="") if grant else ""
    ev, ok_all, first = ["page %s" % code], code == 200, None
    for p in profs:
        c, disp, body = get(url + "key/%s.conf%s" % (p, q))
        same = c == 200 and "attachment" in disp and norm(body.decode("utf-8", "replace")) == want[p]
        ok_all &= same
        ev.append("%s.conf %s%s" % (p, c, " = bridge" if same else " MISMATCH"))
        if same and first is None:
            first = (p, body)
    if first:
        ok, e = client_test(ctx, "live-page-%s" % first[0], data=first[1])
        ok_all &= ok
        ev.append("downloaded %s.conf connects: %s" % (first[0], e if ok else "FAIL " + e))
    row("page + .conf download (%s)" % ctx.a.page_user, "OK" if ok_all else "FAIL", "; ".join(ev))

    # SEC50-10: без токена (и с чужим) файл и QR ключа не отдаются — сканер с адресом страницы их не заберёт
    if not grant:
        return row("page key files need the token (SEC50-10)", "SKIP",
                   "no token in the keys.json answer (a page before SEC50-10?)" if nonce else "no keys.json protocol")
    codes = []
    for p in profs:
        for path in ("key/%s.conf" % p, "qr/key/%s/conf.png" % p, "qr/key/%s/vpn.png" % p):
            for query in ("", "?g=" + "A" * 32):
                codes.append(get(url + path + query)[0])
    bad = [c for c in codes if c != 404]
    row("page key files need the token (SEC50-10)", "FAIL" if bad else "OK",
        "%d requests without the token or with a wrong one: %s" % (len(codes), "all 404" if not bad else
                                                                  "not 404: %s" % sorted(set(bad))))


def c_newkey(ctx):
    profs = ctx.a.newkey_profiles.split(",") if ctx.a.newkey_profiles else sorted(ctx.cfg["profiles"])
    name = "live-nk-" + secrets.token_hex(3)
    r = bridge(ctx.server, "user-add", "--name=" + name, "--label=live acceptance", "--profiles=" + ",".join(profs))
    try:
        if r.get("status") != "success":
            return row("new key", "FAIL", "user-add: %s" % (r.get("code") or r.get("message")))
        for p in profs:
            tag = " (adopted app container)" if ctx.cfg["profiles"][p].get("imported") else ""
            ok, e = client_test(ctx, "live-newkey-%s" % p, conf_cmd(name, p))
            row("new key %s%s" % (p, tag), "OK" if ok else "FAIL", e)
    finally:
        bridge(ctx.server, "user-del", "--name=" + name)


def c_adopted(ctx):
    base = " '%s'" % ctx.a.adopted_baseline if ctx.a.adopted_baseline else ""
    rc, out, _ = ctx.server.run("python3 %s/adopted_check.py%s" % (REMOTE_DIR, base), safe=True)
    try:
        j = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return row("adopted people intact", "FAIL", "no answer")
    if rc == 2:
        return row("adopted people intact", "SKIP", "no adopted (imported) profiles")
    ev = "; ".join("%s: %d/%d people ok, server keys %s" % (p, v["ok"], v["people"], "same" if v["keys_ok"] else "CHANGED")
                   for p, v in j["profiles"].items())
    ev += "; clientsTable metadata changed for %d (ACC-1)" % j.get("meta_changed", 0) if j.get("meta_changed") else ""
    row("adopted people intact", "OK" if rc == 0 else "FAIL", ev + ("; " + "; ".join(j["broken"][:3]) if j["broken"] else ""))


def remote_rows(ctx, prefix, cmd):
    rc, out, _ = ctx.server.run(cmd, timeout=1800)
    seen = False
    for ln in out.splitlines():
        try:
            j = json.loads(ln)
        except ValueError:
            continue
        seen = True
        row("%s: %s" % (prefix, j["check"]), "OK" if j["ok"] else "FAIL", j.get("evidence", ""))
    if not seen:
        row(prefix, "FAIL", "remote check produced no result (rc %s)" % rc)


def c_backup(ctx):
    remote_rows(ctx, "backup", "python3 %s/backup_check.py '%s'" % (REMOTE_DIR, ctx.a.backup_probe_user or ctx.a.user or ""))
    if ctx.a.user:
        _, profs = user_profiles(ctx.server, ctx.a.user)
        if profs:
            ok, e = client_test(ctx, "live-after-restore", conf_cmd(ctx.a.user, profs[0]))
            row("backup: %s/%s connects after restore" % (ctx.a.user, profs[0]), "OK" if ok else "FAIL", e)


def c_mcp(ctx):
    remote_rows(ctx, "mcp", "python3 %s/mcp_check.py" % REMOTE_DIR)


def site_code(ctx):
    d = ctx.cfg.get("domain")
    if not d:
        return "-"
    try:
        with urllib.request.urlopen(urllib.request.Request("https://%s/" % d, headers={"User-Agent": UA}), timeout=20) as r:
            return str(r.status)
    except Exception as e:  # noqa: BLE001
        return getattr(e, "code", "error")


def c_mode(ctx):
    p = ctx.a.mode_switch
    if ctx.cfg.get("mode") != "lite_site":
        return row("mode switch", "SKIP", "server is in %s, the check starts from lite_site" % ctx.cfg.get("mode"))
    if p not in ctx.cfg["profiles"]:
        return row("mode switch", "FAIL", "no profile %s" % p)
    old_port = ctx.cfg["profiles"][p]["port"]
    _, uprofs = user_profiles(ctx.server, ctx.a.user)
    if p not in uprofs:
        return row("mode switch", "FAIL", "--user %s has no key on %s" % (ctx.a.user, p))
    other = [q for q in uprofs if q != p]
    state_cmd = ("sha256sum < /etc/nginx/sites-available/goamnezia | cut -c1-16; openssl x509 -noout -serial -in "
                 "/etc/letsencrypt/live/%s/cert.pem 2>/dev/null; " % ctx.cfg.get("domain")) + "; ".join(
        conf_cmd(ctx.a.user, q) + " | sha256sum | cut -c1-16" for q in uprofs)
    _, pre, _ = ctx.server.run(state_cmd, safe=True)
    try:
        r1 = bridge(ctx.server, "change-mode", "--mode=pro")
        r2 = bridge(ctx.server, "profile-port", "--profile=" + p, "--port=443")
        s = site_code(ctx)
        ev, ok_old = [], True
        for q in other[:1]:
            ok, e = client_test(ctx, "live-pro-old-%s" % q, conf_cmd(ctx.a.user, q))
            ok_old &= ok
            ev.append("old key %s %s" % (q, e if ok else "FAIL " + e))
        row("mode pro: site + other keys", "OK" if r1.get("status") == "success" and r2.get("status") == "success"
            and s == "200" and ok_old else "FAIL",
            "change-mode %s, %s→443 %s, site %s; %s" % (r1.get("status"), p, r2.get("status"), s, "; ".join(ev)))
        ok, e = client_test(ctx, "live-pro-443-%s" % p, conf_cmd(ctx.a.user, p))
        row("mode pro: %s key on UDP/443" % p, "OK" if ok else "FAIL",
            e if ok else e + " — if other UDP ports reach the server, UDP/443 is filtered on the path (see README)")
    finally:
        r3 = bridge(ctx.server, "profile-port", "--profile=" + p, "--port=%s" % old_port)
        r4 = bridge(ctx.server, "change-mode", "--mode=lite_site")
        _, post, _ = ctx.server.run(state_cmd, safe=True)
        ok, e = client_test(ctx, "live-back-%s" % p, conf_cmd(ctx.a.user, p))
        row("mode back to lite_site", "OK" if r3.get("status") == "success" and r4.get("status") == "success"
            and pre == post and ok and site_code(ctx) == "200" else "FAIL",
            "port %s back %s, mode %s, vhost/cert/keys %s, %s key %s" % (
                old_port, r3.get("status"), r4.get("mode"), "as before" if pre == post else "CHANGED", p, e))


def c_snapshot(ctx):
    a = ctx.a
    if not (a.snapshot_save or a.snapshot_compare):
        return row("snapshot", "SKIP", "no --snapshot-save/--snapshot-compare")
    if a.snapshot_save:
        rc, _, _ = ctx.server.run("umask 077; python3 %s/snapshot.py %s > %s/snapshots/%s.json"
                                  % (REMOTE_DIR, a.snapshot_save, REMOTE_DIR, a.snapshot_save), safe=True)
        _, out, _ = ctx.server.run("jq -c '{v: .version.code_version, users: (.users|length), keys: (.user_keys|length), "
                                   "pages: .pages.count}' %s/snapshots/%s.json" % (REMOTE_DIR, a.snapshot_save), safe=True)
        row("snapshot saved '%s'" % a.snapshot_save, "OK" if rc == 0 else "FAIL", out.strip())
    if a.snapshot_compare:
        before = a.snapshot_compare if a.snapshot_compare.startswith("/") else "%s/snapshots/%s.json" % (REMOTE_DIR, a.snapshot_compare)
        exp = " --expect-version %s" % a.expect_version if a.expect_version else ""
        rc, out, _ = ctx.server.run("umask 077; python3 %s/snapshot.py now > %s/snapshots/now.json && python3 %s/compare.py "
                                    "'%s' %s/snapshots/now.json%s" % (REMOTE_DIR, REMOTE_DIR, REMOTE_DIR, before, REMOTE_DIR, exp),
                                    safe=True)
        lines = out.splitlines()
        bad = [ln.split("—")[0].strip() for ln in lines if ln.startswith(("FAIL", "WARN"))]
        row("snapshot compare vs '%s'" % a.snapshot_compare, "OK" if rc == 0 else "FAIL",
            (lines[-1] if lines else "no output") + ("; " + "; ".join(bad[:6]) if bad else ""))


def iso_pair(ctx, tag, conf_a, conf_b):
    """Два клиента одного профиля одновременно (remote/iso_pair_test.sh). conf: ("bridge", user, profile) — ключ
    трубой с сервера, или ("file", путь) — локальный 0600-файл (ключ человека приложения, которого нет на сервере)."""
    d = "%s/iso-%s" % (REMOTE_DIR, secrets.token_hex(3))
    ctx.client.run("umask 077; mkdir -p -m 700 %s" % d, safe=True)
    for x, src in (("a", conf_a), ("b", conf_b)):
        dest = "umask 077; cat > %s/%s.conf" % (d, x)
        if src[0] == "bridge":
            ctx.server.pipe(conf_cmd(src[1], src[2]), ctx.client, dest, timeout=120, safe=True)
        else:
            with open(src[1], "rb") as f:
                ctx.client.run(dest, input=f.read(), timeout=120, safe=True)
    # Проверка сама стирает конфиги по ходу: после обрыва посреди её нечем повторить — только обрыв до начала.
    _, out, _ = ctx.client.run("GA_CLIENT_IMG=%s bash %s/iso_pair_test.sh %s %s" % (ctx.image, REMOTE_DIR, d, tag), timeout=400)
    line = ([ln for ln in out.splitlines() if ln.startswith("ISO ")] or ["ISO %s FAIL no output" % tag])[-1]
    m = re.search(r"hs=(\S+)/(\S+) exit=(\S+)/(\S+) a->b ping=(\S+) tcp=(\S+) b->a ping=(\S+) tcp=(\S+)", line)
    if not m:
        return None, line
    k = ("hs_a", "hs_b", "exit_a", "exit_b", "ab_ping", "ab_tcp", "ba_ping", "ba_tcp")
    return dict(zip(k, m.groups())), line.split(" ", 2)[2]


def isolation_set(ctx, value):
    """client_isolation в config.json (переключателя в интерфейсах нет, CONTRACT): под замком продукта, 0600;
    None — убрать поле (умолчание true). Применение — тиком сторожа (watch-tick), как у продукта."""
    expr = "del(.client_isolation)" if value is None else ".client_isolation=%s" % ("true" if value else "false")
    ctx.server.run("cd /opt/goamnezia && flock -w 60 /run/goamnezia/config.lock sh -c \"jq '%s' config.json > "
                   "config.json.iso && chmod 600 config.json.iso && mv config.json.iso config.json\"" % expr)
    bridge(ctx.server, "watch-tick")


def c_isolation(ctx):
    a = ctx.a
    _, uprofs = user_profiles(ctx.server, a.user) if a.user else ({}, [])
    p = a.isolation_profile or ("awg2" if "awg2" in (uprofs or ctx.cfg["profiles"]) else sorted(uprofs or ctx.cfg["profiles"])[0])
    pc = ctx.cfg["profiles"][p]
    iface = pc.get("iface") or "awg0"
    rule_cmd = "docker exec %s iptables -S FORWARD | grep -c -- '-i %s -o %s -j DROP'" % (pc["container"], iface, iface)
    st = bridge(ctx.server, "status")
    _, rules, _ = ctx.server.run(rule_cmd, safe=True)
    _, orig, _ = ctx.server.run("jq -c '.client_isolation' /opt/goamnezia/config.json", safe=True)
    if a.isolation_conf_file:
        check_file_secret(a.isolation_conf_file)
        ca, who_a = ("file", a.isolation_conf_file), "local conf"
    else:
        ca, who_a = ("bridge", a.user, p), a.user
    tmp = None
    if a.isolation_peer:
        cb, who_b = ("bridge", a.isolation_peer, p), a.isolation_peer
    else:
        tmp = "live-iso-" + secrets.token_hex(3)
        r = bridge(ctx.server, "user-add", "--name=" + tmp, "--label=live isolation", "--profiles=" + p)
        if r.get("status") != "success":
            return row("isolation %s" % p, "FAIL", "user-add: %s" % (r.get("code") or r.get("message")))
        cb, who_b = ("bridge", tmp, p), "new user"
    tag = "%s (%s)%s" % (p, pc.get("line"), " adopted" if pc.get("imported") else "")
    try:
        res, ev = iso_pair(ctx, "iso-%s" % p, ca, cb)
        exits_ok = bool(res) and res["exit_a"] == ctx.server_ip and res["exit_b"] == ctx.server_ip
        blocked = bool(res) and all(res[k] == "blocked" for k in ("ab_ping", "ab_tcp", "ba_ping", "ba_tcp"))
        row("isolation %s: %s ↔ %s" % (tag, who_a, who_b),
            "OK" if st.get("client_isolation") is True and rules.strip() == "1" and exits_ok and blocked else "FAIL",
            "status client_isolation=%s, DROP rule ×%s; %s" % (st.get("client_isolation"), rules.strip(), ev))
        if a.isolation_toggle:
            isolation_set(ctx, False)
            _, r_off, _ = ctx.server.run(rule_cmd, safe=True)
            res2, ev2 = iso_pair(ctx, "iso-off-%s" % p, ca, cb)
            open_ok = bool(res2) and all(res2[k] == "ok" for k in ("ab_ping", "ab_tcp", "ba_ping", "ba_tcp"))
            row("isolation %s off → reachable" % p, "OK" if r_off.strip() == "0" and open_ok else "FAIL",
                "client_isolation=false + watch-tick, DROP rule ×%s; %s" % (r_off.strip(), ev2))
            isolation_set(ctx, None if orig.strip() in ("null", "") else orig.strip() == "true")
            _, r_on, _ = ctx.server.run(rule_cmd, safe=True)
            st3 = bridge(ctx.server, "status")
            res3, ev3 = iso_pair(ctx, "iso-on-%s" % p, ca, cb)
            blocked3 = bool(res3) and all(res3[k] == "blocked" for k in ("ab_ping", "ab_tcp", "ba_ping", "ba_tcp"))
            row("isolation %s back on → blocked" % p,
                "OK" if st3.get("client_isolation") is True and r_on.strip() == "1" and blocked3 else "FAIL",
                "config restored (%s), status %s, DROP rule ×%s; %s" % (orig.strip(), st3.get("client_isolation"),
                                                                        r_on.strip(), ev3))
    finally:
        if a.isolation_toggle:
            _, cur, _ = ctx.server.run("jq -c '.client_isolation' /opt/goamnezia/config.json", safe=True)
            if cur.strip() == "false" and orig.strip() != "false":
                isolation_set(ctx, None if orig.strip() in ("null", "") else True)
        if tmp:
            bridge(ctx.server, "user-del", "--name=" + tmp)


def c_bridge(ctx):
    """Мост 0.5.0 (только чтение): «🎲» name-suggest на двух языках, язык message по GOAMNEZIA_CALL_LANG,
    schedule у backup-last, новые поля status (L-01 endpoint_host_gone, L-06 server_ip_change, L-07 upstream)."""
    ev, ok = [], True
    for lang in ("ru", "en"):
        got = [bridge(ctx.server, "name-suggest", "--lang=" + lang) for _ in range(2)]
        ids = [g.get("id") or "" for g in got]
        good = all(g.get("status") == "success" and re.fullmatch(r"[a-z][a-z0-9_]{0,31}", i) for g, i in zip(got, ids))
        labels_ru = [bool(CYR.search(g.get("label") or "")) for g in got]
        lang_ok = all(labels_ru) if lang == "ru" else not any(labels_ru)
        ok &= good and lang_ok and ids[0] != ids[1]
        ev.append("name-suggest %s: %s, %s" % (lang, " / ".join(ids), "labels %s" % lang if lang_ok else "LABEL LANGUAGE WRONG"))
    probe = "--name=live-no-such-" + secrets.token_hex(2)
    msg = {lg: bridge(ctx.server, "user-show", probe, env="GOAMNEZIA_CALL_LANG=" + lg) for lg in ("ru", "en")}
    lang_ok = (msg["ru"].get("code") == msg["en"].get("code") == "user_not_found"
               and bool(CYR.search(msg["ru"].get("message") or "")) and not CYR.search(msg["en"].get("message") or ""))
    ok &= lang_ok
    ev.append("CALL_LANG: message %s" % ("ru→en" if lang_ok else "NOT SWITCHED (%s / %s)" % (msg["ru"].get("code"), msg["en"].get("code"))))
    bl = bridge(ctx.server, "backup-last")
    sched = bl.get("status") == "success" and isinstance(bl.get("schedule"), str) and bl.get("schedule") != ""
    ok &= sched
    ev.append("backup-last schedule=%s" % bl.get("schedule"))
    st = bridge(ctx.server, "status")
    missing = [k for k in ("endpoint_host_gone", "server_ip_change", "upstream") if k not in st]
    ok &= not missing and isinstance(st.get("endpoint_host_gone"), bool)
    ev.append("status: %s" % ("missing " + ",".join(missing) if missing else "endpoint_host_gone=%s server_ip_change=%s upstream=%s" % (
        st.get("endpoint_host_gone"), "set" if st.get("server_ip_change") else "null", "set" if st.get("upstream") else "null")))
    row("bridge 0.5.0 fields", "OK" if ok else "FAIL", "; ".join(ev))


# Клиентская половина проверки временной ссылки: адрес приходит по stdin (трубой с сервера) и не печатается.
# Тело — файл с приватным ключом: только во временном каталоге 0700, наружу — одни хеши.
SHARE_CLIENT = r"""
umask 077; d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
IFS= read -r u; [ -n "$u" ] || { echo "SHARE no-url"; exit 1; }
k=""; [ "${SELFSIGNED:-0}" = 1 ] && k="-k"
hp=$(printf '%s' "$u" | sed -E 's#^(https://[^/]+)/.*#\1#')
get() { c=$(curl -s $k -o "$d/b" -D "$d/h" -w '%{http_code}' --max-time 20 "$u"); a=$(grep -ci '^content-disposition: *attachment' "$d/h");
  s=$(python3 -c 'import sys,hashlib;t=open(sys.argv[1],"rb").read().decode("utf-8","replace");print(hashlib.sha256((t.rstrip(chr(10))+chr(10)).encode()).hexdigest()[:16])' "$d/b" 2>/dev/null)
  printf ' %s=%s/att%s/%s' "$1" "$c" "$a" "$s"; }
r=""; for i in 1 2 3; do r="$r$(get get$i)"; done
r="$r wrong=$(curl -s $k -o /dev/null -w '%{http_code}' --max-time 20 "$hp/d-nope/x.conf")"
if [ "${WAIT_END:-0}" -gt 0 ]; then
  sleep "$WAIT_END"; r="$r$(get near_end)"
  sleep 45; c=$(curl -s $k -o /dev/null -w '%{http_code}' --max-time 10 "$u"); r="$r expired=$c/exit$?"
fi
echo "SHARE$r"
"""


def c_share(ctx):
    """Временная ссылка (решение В1, docs/SHARE.md): файл сразу по адресу, многоразовая в пределах срока, чужой путь →
    404; после снятия (или с --share-expiry — после 5 минут) процесс гасится и порт закрыт. Адрес — секрет: из ответа
    сервера сразу клиенту в stdin, harness его не печатает."""
    _, profs = user_profiles(ctx.server, ctx.a.user)
    if not profs:
        return row("share", "FAIL", "--user has no profiles")
    p = profs[0]
    srv = ("out=$(GOAMNEZIA_WHO=live-acceptance bash /opt/goamnezia/install.sh --action=share-create --name='%s' --profile=%s "
           "--format=conf --json 2>/dev/null | tail -1); printf '%%s' \"$out\" | jq -r '.url // empty'; "
           "printf '%%s' \"$out\" | jq -c '{status, code, id, port, ttl, expires, selfsigned, via}' >&2" % (ctx.a.user, p))
    # share-create заводит ссылку: повтор — только если ssh не дошёл до команды (ssh_link). Адрес — первая строка
    # ответа; он не печатается и уходит клиенту в stdin.
    _, out, err = ctx.server.run(srv)
    url = (out.splitlines() or [""])[0].strip()
    meta_raw = err.strip().splitlines()
    out = ""
    try:
        meta = json.loads(meta_raw[-1])
    except (ValueError, IndexError):
        meta = {}
    if meta.get("status") != "success" or not url:
        return row("share %s/%s" % (ctx.a.user, p), "FAIL", "share-create: %s" % (meta.get("code") or "no answer"))
    wait_end = max(0, int(meta.get("ttl") or 300) - 25) if ctx.a.share_expiry else 0
    env = "SELFSIGNED=%d WAIT_END=%d" % (1 if meta.get("selfsigned") else 0, wait_end)
    try:
        # Клиент считает скачивания (downloads = 3): посреди не повторяется.
        _, got, _ = ctx.client.run("%s bash -c %s" % (env, shlex.quote(SHARE_CLIENT)), input=(url + "\n").encode(),
                                   timeout=wait_end + 240)
        line = ([ln for ln in got.splitlines() if ln.startswith("SHARE")] or ["SHARE no output"])[-1]
        _, want, _ = ctx.server.run(conf_cmd(ctx.a.user, p) + " | python3 -c \"import sys,hashlib; t=sys.stdin.read(); "
                                    "print(hashlib.sha256((t.rstrip(chr(10))+chr(10)).encode()).hexdigest()[:16])\"",
                                    safe=True)
        gets = re.findall(r"get\d=(\d+)/att(\d+)/(\w*)", line)
        multi = len(gets) == 3 and all(c == "200" and a == "1" and h == want.strip() for c, a, h in gets)
        wrong = re.search(r"wrong=(\d+)", line)
        lst = bridge(ctx.server, "share-list")
        dl = next((x.get("downloads") for x in lst.get("shares") or [] if x.get("id") == meta.get("id")), None)
        ok = multi and wrong and wrong.group(1) == "404" and (ctx.a.share_expiry or dl == 3)
        ne = re.search(r"near_end=(\d+)", line)
        ex = re.search(r"expired=(\d+)/exit(\d+)", line)
        if ctx.a.share_expiry:
            ok = ok and ne and ne.group(1) == "200" and ex and (ex.group(1) == "404" or ex.group(2) == "7")
        if ctx.a.share_expiry:
            tail = "; GET %ss before the end %s, after the end %s" % (
                25, ne.group(1) if ne else "-", ("%s/curl exit %s" % ex.groups()) if ex else "-")
        else:
            tail = "; downloads counted %s" % dl
        row("share %s/%s: file at once, reusable, wrong path 404" % (ctx.a.user, p), "OK" if ok else "FAIL",
            "%s, ttl %ss, via %s; 3 GETs %s (= bridge conf: %s); wrong path %s%s" % (
                meta.get("id"), meta.get("ttl"), meta.get("via"), ",".join(c for c, _, _ in gets) or "-",
                "yes" if multi else "NO", wrong.group(1) if wrong else "-", tail))
    finally:
        if not ctx.a.share_expiry:
            bridge(ctx.server, "share-revoke", "--id=%s" % meta.get("id"))
        port = meta.get("port")
        _, listen, _ = ctx.server.run("ss -ltn | grep -c ':%s '" % port, safe=True)
        host = ctx.cfg.get("domain") or ctx.server_ip
        _, probe, _ = ctx.client.run("curl -s -k -o /dev/null -w '%%{http_code}' --max-time 10 https://%s:%s/; echo \" exit=$?\""
                                     % (host, port), safe=True)
        left = [x for x in bridge(ctx.server, "share-list").get("shares") or [] if x.get("id") == meta.get("id")]
        closed = listen.strip() == "0" and not left and (probe.strip().startswith("000") or probe.strip().startswith("404"))
        row("share %s: after %s — port closed" % (meta.get("id"), "expiry" if ctx.a.share_expiry else "revoke"),
            "OK" if closed else "FAIL", "listening %s, in share-list %s, https://<host>:%s/ → %s" % (
                listen.strip(), "yes" if left else "no", port, probe.strip()))


# ── эксплуатационные проверки (tests/live/remote/ops_check.py) ──────────────────────────────────────────────
def ops_rows(ctx, prefix, mode, timeout=1800):
    """Режим ops_check.py на сервере → строки таблицы (skip → SKIP)."""
    try:
        rc, out, err = ctx.server.run("python3 %s/ops_check.py %s" % (REMOTE_DIR, mode), timeout=timeout,
                                      safe=mode.split()[0] in OPS_READ)
    except subprocess.TimeoutExpired:
        return row(prefix, "FAIL", "ops_check.py %s: no answer within %ds" % (mode.split()[0], timeout))
    seen = False
    for ln in out.splitlines():
        try:
            j = json.loads(ln)
        except ValueError:
            continue
        seen = True
        row("%s: %s" % (prefix, j["check"]), "SKIP" if j.get("skip") else ("OK" if j["ok"] else "FAIL"),
            j.get("evidence", ""))
    if not seen:
        row(prefix, "FAIL", "ops_check.py %s produced no result (rc %s): %s" % (mode.split()[0], rc, err.strip()[-160:]))


def snap_save(ctx, prefix, name):
    rc, _, err = ctx.server.run("umask 077; mkdir -p %s/snapshots && python3 %s/snapshot.py %s > %s/snapshots/%s.json"
                                % (REMOTE_DIR, REMOTE_DIR, name, REMOTE_DIR, name), timeout=900, safe=True)
    if rc != 0:
        row("%s: snapshot before" % prefix, "FAIL", "snapshot.py rc %s: %s" % (rc, err.strip()[-160:]))
    return rc == 0


def snap_compare(ctx, prefix, name, expect_version=None):
    """Снимок сейчас против снимка до проверки: FAIL 0 — ничего не потеряно (WARN читать, их видно в строке).
    expect_version — код обязан быть этой версии (после обновления)."""
    exp = " --expect-version %s" % shlex.quote(expect_version) if expect_version else ""
    rc, out, _ = ctx.server.run("umask 077; python3 %s/snapshot.py now > %s/snapshots/now.json && python3 %s/compare.py "
                                "%s/snapshots/%s.json %s/snapshots/now.json%s" % (REMOTE_DIR, REMOTE_DIR, REMOTE_DIR,
                                                                                  REMOTE_DIR, name, REMOTE_DIR, exp),
                                timeout=900, safe=True)
    lines = out.splitlines()
    bad = [ln.split("—")[0].strip() for ln in lines if ln.startswith(("FAIL", "WARN"))]
    row("%s: snapshot before/after" % prefix, "OK" if rc == 0 else "FAIL",
        (lines[-1] if lines else "no output") + ("; " + "; ".join(bad[:8]) if bad else ""))


def page_code(ctx):
    """Код ответа страницы подключения из --page-url-file (адрес не печатается); None — файла нет."""
    if not ctx.a.page_url_file:
        return None
    check_file_secret(ctx.a.page_url_file)
    with open(ctx.a.page_url_file, encoding="utf-8") as f:
        url = f.read().strip()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20) as r:
            return str(r.status)
    except Exception as e:  # noqa: BLE001
        return str(getattr(e, "code", "error"))


def web_rows(ctx, prefix):
    d = ctx.cfg.get("domain")
    if d:
        code = site_code(ctx)
        row("%s: site https://%s/ from outside" % (prefix, d), "OK" if str(code) == "200" else "FAIL", "GET → %s" % code)
    pc = page_code(ctx)
    if pc is not None:
        row("%s: connection page (--page-url-file)" % prefix, "OK" if pc == "200" else "FAIL", "GET → %s" % pc)


def conn_rows(ctx, prefix):
    """Ключи подключаются: --user на всех своих профилях + локальные 0600-конфы --conn-conf-file (люди приложения)."""
    if ctx.a.user:
        _, profs = user_profiles(ctx.server, ctx.a.user)
        if not profs:
            row("%s: conn %s" % (prefix, ctx.a.user), "FAIL", "user not found or has no profiles")
        for p in profs:
            ok, ev = client_test(ctx, "ops-%s-%s" % (ctx.a.user, p), conf_cmd(ctx.a.user, p))
            row("%s: conn %s/%s" % (prefix, ctx.a.user, p), "OK" if ok else "FAIL", ev)
    for f in ctx.a.conn_conf_file or []:
        check_file_secret(f)
        with open(f, "rb") as fh:
            data = fh.read()
        ok, ev = client_test(ctx, "ops-conf-%s" % re.sub(r"[^A-Za-z0-9-]", "-", os.path.basename(f))[:30], data=data)
        row("%s: conn %s (local conf)" % (prefix, os.path.basename(f)), "OK" if ok else "FAIL", ev)


def c_reboot(ctx):
    a, s, P = ctx.a, ctx.server, "reboot"
    ops_rows(ctx, P, "pre-reboot")
    if not snap_save(ctx, P, "ops-reboot-before"):
        return
    _, boot0, _ = s.run("cat /proc/sys/kernel/random/boot_id", safe=True)
    t0 = time.time()
    # Через systemd-run: команда отвечает сразу, SSH не рвётся посреди ответа.
    rc, _, err = s.run("systemd-run --on-active=3 --timer-property=AccuracySec=1s /bin/systemctl reboot", timeout=60)
    if rc != 0:
        return row(P + ": reboot command", "FAIL", err.strip()[-160:])
    back = None
    while time.time() - t0 < a.reboot_timeout:
        time.sleep(10)
        try:
            rc, out, _ = s.run("cat /proc/sys/kernel/random/boot_id", timeout=30, tries=1)
        except subprocess.TimeoutExpired:
            continue
        if rc == 0 and out.strip() and out.strip() != boot0.strip():
            back = int(time.time() - t0)
            break
    if back is None:
        return row(P + ": server back over SSH", "FAIL", "no SSH with a new boot_id within %ds" % a.reboot_timeout)
    row(P + ": server back over SSH", "OK", "%ds after the reboot command, new boot_id" % back)
    ops_rows(ctx, P, "wait-boot 420", timeout=900)
    ops_rows(ctx, P, "health after-reboot", timeout=900)
    web_rows(ctx, P)
    conn_rows(ctx, P)
    snap_compare(ctx, P, "ops-reboot-before")


def c_disk_full(ctx):
    a, P = ctx.a, "disk-full"
    if not a.allow_disk_full:
        return row(P, "SKIP", "fills / to 99 % for a few minutes: run only with --allow-disk-full")
    if not snap_save(ctx, P, "ops-diskfull-before"):
        return
    ops_rows(ctx, P, "diskfull '%s'" % (a.probe_user or a.backup_probe_user or a.user or ""), timeout=2400)
    snap_compare(ctx, P, "ops-diskfull-before")


def c_logrotate(ctx):
    ops_rows(ctx, "logrotate", "logrotate", timeout=900)


def c_cert_renew(ctx):
    ops_rows(ctx, "cert-renew", "certrenew", timeout=1200)
    web_rows(ctx, "cert-renew")


def c_update_rollback(ctx):
    a, P = ctx.a, "update-rollback"
    if not a.allow_update_rollback:
        return row(P, "SKIP", "installs candidates with a failing and a hanging migration on the stand: run only with "
                              "--allow-update-rollback")
    if not snap_save(ctx, P, "ops-rb-before"):
        return
    # Кандидата ставит загрузчик из этого репозитория, как «Обновить goAmnezia»: он кладёт прежний код рядом
    # (.prev_code), и откатывать продукту есть чем (OPS-02). ops_check.py берёт его рядом с собой.
    with open(os.path.join(HERE, "..", "..", "bootstrap.sh"), "rb") as f:
        rc, _, err = ctx.server.run("umask 077; mkdir -p %s && cat > %s/bootstrap.sh && chmod 700 %s/bootstrap.sh"
                                    % (REMOTE_DIR, REMOTE_DIR, REMOTE_DIR), input=f.read(), safe=True)
    if rc != 0:
        return row(P, "FAIL", "upload bootstrap.sh: %s" % err.strip()[:160])
    # Снимок «до» отдаётся режиму: он сравнивает сразу после автоматического отката, до второй части проверки.
    ops_rows(ctx, P, "rollback ops-rb-before", timeout=4800)
    snap_compare(ctx, P, "ops-rb-before")
    ops_rows(ctx, P + " (after)", "health", timeout=900)
    conn_rows(ctx, P)


def tree_version(tree, ref="HEAD"):
    """Версия в lib/common.sh закоммиченного дерева (git show <ref>:…) — ровно то, что уедет на сервер."""
    p = subprocess.run(["git", "-C", tree, "show", "%s:lib/common.sh" % ref], capture_output=True, text=True)
    m = re.search(r'^GOAMNEZIA_VERSION="([0-9][0-9A-Za-z.+-]*)"', p.stdout, re.M)
    return m.group(1) if m else ""


def c_upgrade(ctx):
    """Обновление настоящим путём загрузчика: дерево HEAD этого репозитория (git archive) → GOAMNEZIA_LOCAL_TREE →
    bootstrap.sh → exec меню → миграция. Снимок до и после (с --expect-version), «Что нового» один раз, .prev_code."""
    a, P = ctx.a, "upgrade"
    if not a.allow_upgrade:
        return row(P, "SKIP", "replaces the product code on the stand: run only with --allow-upgrade")
    tree = os.path.abspath(a.upgrade_tree or os.path.join(HERE, "..", ".."))
    want = a.upgrade_ref or "HEAD"
    ver = tree_version(tree, want)
    head = subprocess.run(["git", "-C", tree, "rev-parse", "--short=12", want], capture_output=True, text=True).stdout.strip()
    ref = subprocess.run(["git", "-C", tree, "rev-parse", "--abbrev-ref", want], capture_output=True, text=True).stdout.strip() or want
    if not ver or not head:
        return row(P, "FAIL", "no git tree with lib/common.sh at %s" % tree)
    if not snap_save(ctx, P, "ops-upgrade-before"):
        return
    remote_tree = "%s/tree" % REMOTE_DIR
    arch = subprocess.run(["git", "-C", tree, "archive", "--format=tar", want], capture_output=True)
    rc, _, err = ctx.server.run("umask 077; rm -rf %s && mkdir -p %s && tar -x -C %s" % (remote_tree, remote_tree, remote_tree),
                                input=arch.stdout, timeout=300, safe=True)
    if arch.returncode != 0 or rc != 0:
        return row(P, "FAIL", "upload of the tree failed: %s" % (err.strip() or arch.stderr.decode()[:160])[:160])
    row(P + ": tree uploaded", "OK", "%s@%s (version %s), %d KB via git archive" % (ref, head, ver, len(arch.stdout) // 1024))
    ops_rows(ctx, P, "upgrade %s '%s' %s" % (remote_tree, a.upgrade_branch or "", ver), timeout=2400)
    snap_compare(ctx, P, "ops-upgrade-before", expect_version=ver)
    ctx.server.run("rm -rf %s" % remote_tree, safe=True)


def c_selfupdate(ctx):
    """«Обновить goAmnezia» — только просмотр: выбор ветки, какую ветку спросили у GitHub, ничего не сменилось. Есть ли
    ветка на GitHub, harness спрашивает сам через gh (ключ на сервер не передаётся)."""
    P = "selfupdate"
    ops_rows(ctx, P, "selfupdate-view", timeout=900)
    _, src, _ = ctx.server.run("cat /opt/goamnezia/.install-source 2>/dev/null", safe=True)
    repo = (re.search(r"^repo=([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)$", src, re.M) or [None, ""])[1]
    if not repo or subprocess.run(["sh", "-c", "command -v gh"], capture_output=True).returncode != 0:
        return row(P + ": branches on GitHub", "SKIP", "no gh CLI or no repo in .install-source")
    have = {}
    for b in ("main", "dev"):
        p = subprocess.run(["gh", "api", "-i", "repos/%s/commits/%s" % (repo, b), "-H", "Accept: application/vnd.github.sha"],
                           capture_output=True, text=True)
        m = re.search(r"^HTTP/\S+ (\d{3})", p.stdout, re.M)
        have[b] = m.group(1) if m else "?"
    row(P + ": branches on GitHub (gh)", "OK" if have.get("dev") == "200" else "FAIL",
        "commits/main → %s%s; commits/dev → %s" % (have.get("main"), " (no branch: with a real key the screen answers "
                                                    "«Ветки main в репозитории нет.»)" if have.get("main") == "422" else "",
                                                    have.get("dev")))


# ── Ленивый Pro (0.6.0, docs/MODES_047.md) ──────────────────────────────────────────────────────────────
NAMES = os.path.join(HERE, "..", "..", "lib", "names_capitals.json")
STUB_TEMPLATE = "builtin_stub"


def c_lazy(ctx):
    """Итог Ленивого Pro (раздел «Ленивый Pro» docs/MODES_047.md): режим и линия на UDP 443 (2.0 на 443, 3.1 на
    случайном порту; подхваченные у приложения — на своих портах, ACC-2), сертификат Let's Encrypt снаружи, случайный
    шаблон сайта, панель на домене, --lazy-keys ключей «прилагательное + столица» с обеими линиями и страницей на 30 дней.
    Только чтение. Путь панели и адреса страниц не покидают сервер: их открывает curl на самом сервере (--resolve на
    127.0.0.1), наружу — только коды ответа."""
    a, P = ctx.a, "lazy"
    _, out, _ = ctx.server.run("jq -c '{mode, domain, lazy: {domain: .lazy.domain, keys: (.lazy.keys // []), "
                               "pages: ((.lazy.pages // {}) | keys)}, cert: .site.cert, template: .site.template_id, "
                               "profiles: (.profiles | map_values({port, line, imported, iface, container}))}' "
                               "/opt/goamnezia/config.json", safe=True)
    try:
        c = json.loads(out)
    except ValueError:
        return row(P, "FAIL", "config.json not readable")
    profs, expect = c.get("profiles") or {}, a.lazy_expect
    on443 = [p for p, v in profs.items() if v.get("port") == 443]
    shown = ", ".join("%s %s :%s%s" % (p, v.get("line"), v.get("port"), " adopted" if v.get("imported") else "")
                      for p, v in sorted(profs.items()))
    imported = {p: v for p, v in profs.items() if v.get("imported")}
    if expect == "semi_pro":
        ok = c.get("mode") == "lite_site" and not on443
    else:
        # Линии — по версии, а не по месту профиля (LZ-02): приложение Amnezia держит 3.1 и в amnezia-awg2. На сервере
        # обязаны быть обе линии, 2.0 и 3.1; на UDP 443 — 2.0, а если 2.0 уже стоит на своём порту (подхвачена, её
        # людей не двигаем) — 3.1; подхваченный профиль на 443 не переезжает (ACC-2).
        l443 = profs[on443[0]].get("line") if len(on443) == 1 else None
        others = [v.get("line") for v in profs.values() if v.get("port") != 443]
        want443 = "3.1" if "2.0" in others else "2.0"
        ok = (c.get("mode") == "pro" and len(on443) == 1 and on443[0] not in imported and l443 == want443
              and {"2.0", "3.1"} <= {v.get("line") for v in profs.values()})
    row("%s: mode %s, UDP 443" % (P, expect), "OK" if ok else "FAIL", "mode %s; %s" % (c.get("mode"), shown))
    # ACC-2: у подхваченного профиля порт тот же, что в снимке до подхвата, и тот же у контейнера
    for p, v in sorted(imported.items()):
        cmd = ("s=$(ls -d /opt/goamnezia/import-snapshots/%s-2*/ 2>/dev/null | tail -1); f=$s/%s.conf; "
               "grep -m1 -iE '^ListenPort' \"$f\" 2>/dev/null | tr -dc 0-9; echo; docker port %s 2>/dev/null | head -1"
               % (v.get("container"), v.get("iface") or "awg0", v.get("container")))
        _, o, _ = ctx.server.run(cmd, safe=True)
        lines = (o.splitlines() + ["", ""])[:2]
        snap_port, pub = lines[0].strip(), lines[1].strip()
        same = snap_port == str(v.get("port")) and pub.startswith("%s/udp" % v.get("port"))
        row("%s: adopted %s keeps its port (ACC-2)" % (P, p), "OK" if same else "FAIL",
            "config :%s, before adoption :%s, docker %s" % (v.get("port"), snap_port or "?", pub or "-"))
    # Сертификат и сайт — снаружи, с проверкой цепочки и имени
    d = c.get("domain") or ""
    issuer, code = "?", "-"
    try:
        with socket.create_connection((d, 443), timeout=20) as sock:
            with ssl.create_default_context().wrap_socket(sock, server_hostname=d) as t:
                cert = t.getpeercert()
        issuer = dict(x[0] for x in cert.get("issuer", ())).get("organizationName", "?")
        issuer += ", until " + cert.get("notAfter", "?")
    except Exception as e:  # noqa: BLE001
        issuer = "TLS failed: %s" % e
    code = site_code(ctx)
    row("%s: site https://%s/ on Let's Encrypt" % (P, d), "OK" if issuer.startswith("Let's Encrypt") and str(code) == "200"
        and c.get("cert") == "letsencrypt" else "FAIL", "issuer %s; GET / → %s; site.cert %s" % (issuer, code, c.get("cert")))
    row("%s: random site template" % P, "OK" if c.get("template") and c.get("template") != STUB_TEMPLATE else "FAIL",
        "template %s" % (c.get("template") or "none"))
    # Панель на домене: путь читает сам сервер
    _, o, _ = ctx.server.run(
        "d=$(jq -r '.domain // empty' /opt/goamnezia/config.json); p=$(jq -r '.panel.miniapp_path // empty' "
        "/opt/goamnezia/config.json); [ -n \"$d\" ] && [ -n \"$p\" ] || { echo none none; exit 0; }; "
        "a=$(curl -s -o /dev/null -w '%%{http_code}' --max-time 15 --resolve \"$d:443:127.0.0.1\" \"https://$d/$p/\"); "
        "b=$(curl -s -o /dev/null -w '%%{http_code}' --max-time 15 --resolve \"$d:443:127.0.0.1\" \"https://$d/app-%s/\"); "
        "echo $a $b" % secrets.token_hex(8), safe=True)
    pc = (o.split() + ["?", "?"])[:2]
    row("%s: panel on the domain" % P, "OK" if pc[0] == "200" and pc[1] == "404" else "FAIL",
        "panel path → %s, a wrong path → %s (codes from the server, the path stays there)" % (pc[0], pc[1]))
    # Ключи: число, обе линии, имена «прилагательное + столица»
    keys = (c.get("lazy") or {}).get("keys") or []
    lines = sorted(p for p, v in profs.items() if v.get("iface") != "wg0")
    with open(NAMES, encoding="utf-8") as f:
        names = {e["id"]: (e["ru"], e["en"]) for e in json.load(f)}
    bad = []
    for k in keys:
        u, up = user_profiles(ctx.server, k)
        if not up:
            bad.append("%s: no user" % k)
            continue
        if not set(lines) <= set(up):
            bad.append("%s: profiles %s" % (k, ",".join(up)))
        if k not in names or u.get("label") not in names[k]:
            bad.append("%s: «%s» not in names_capitals.json" % (k, u.get("label")))
    ok = len(keys) == a.lazy_keys and len(set(keys)) == len(keys) and not bad
    row("%s: %d keys, both lines, capital names" % (P, a.lazy_keys), "OK" if ok else "FAIL",
        "%d keys (%s) on %s%s" % (len(keys), ", ".join(keys), "+".join(lines), "; " + "; ".join(bad[:4]) if bad else ""))
    # Страницы: одна на ключ, 30 дней, обе линии; каждая открывается (на сервере, адрес не выходит)
    pl = bridge(ctx.server, "page-list").get("pages") or []
    mine = {k: [x for x in pl if x.get("user") == k] for k in keys}
    ids = [x[0]["id"] for x in mine.values() if x]
    _, o, _ = ctx.server.run(
        "d=$(jq -r '.domain // empty' /opt/goamnezia/config.json); for id in %s; do u=$(GOAMNEZIA_WHO=live-acceptance "
        "bash /opt/goamnezia/install.sh --action=page-url --id=$id --json 2>/dev/null | tail -1 | jq -r '.url // empty'); "
        "c=$(curl -s -o /dev/null -w '%%{http_code}' --max-time 20 -A %s --resolve \"$d:443:127.0.0.1\" \"$u\"); "
        "echo \"$id $c\"; done" % (" ".join(shlex.quote(i) for i in ids), shlex.quote(UA)))
    codes = dict(ln.split()[:2] for ln in o.splitlines() if len(ln.split()) >= 2)
    bad = []
    for k, x in mine.items():
        if len(x) != 1:
            bad.append("%s: %d pages" % (k, len(x)))
            continue
        pg = x[0]
        if pg.get("ttl") != "30d" or pg.get("forever") or not set(lines) <= set(pg.get("profiles") or []):
            bad.append("%s: ttl %s, profiles %s" % (k, pg.get("ttl"), ",".join(pg.get("profiles") or [])))
        if codes.get(pg["id"]) != "200":
            bad.append("%s: page → %s" % (k, codes.get(pg["id"], "?")))
    row("%s: a connection page per key (30 days)" % P, "OK" if keys and not bad else "FAIL",
        "%d/%d pages, ttl 30d, open → 200: %d%s" % (sum(1 for x in mine.values() if x), len(keys),
                                                   sum(1 for v in codes.values() if v == "200"),
                                                   "; " + "; ".join(bad[:4]) if bad else ""))


# ── Модуль ядра AmneziaWG по выбору (этап Б, docs/KMOD_PLAN.md) ───────────────────────────────────────────
# Программа (как есть) → kmod-install → модуль → программа → модуль → как было. Через все шаги — одни и те же клиенты с
# одними и теми же ключами (remote/kmod_client.sh); серверная сторона — remote/kmod_server.py (только смотрит, кроме
# одноразового iperf3 рядом с профилем). Долгие изменяющие шаги — службой systemd на сервере (bridge_long).
KMOD_LEGS = ("kernel", "program", "kernel")


def bridge_long(ctx, action, *args, timeout=1800):
    """Долгое изменяющее действие моста (kmod-install, kmod-engine, kmod-remove) — отдельной службой systemd на
    сервере: обрыв SSH посреди его не рвёт, а опрос (только чтение) повторяется. Ход — строка kmod-status.progress,
    когда она меняется; ответ — последняя строка JSON из файла 0600. Не дождались — службу не останавливаем (перевод
    посреди остановки оставил бы профили на разных движках): FAIL с именем службы."""
    unit = "ga-live-kmod-%s" % secrets.token_hex(3)
    res = "%s/%s.json" % (REMOTE_DIR, unit)
    inner = ("GOAMNEZIA_WHO=live-acceptance bash /opt/goamnezia/install.sh --action=%s %s --json 2>/dev/null | tail -1 "
             "> %s.part; mv %s.part %s" % (action, " ".join(shlex.quote(x) for x in args), res, res, res))
    rc, _, err = ctx.server.run("umask 077; mkdir -p %s && systemd-run --quiet --collect --unit=%s /bin/sh -c %s"
                                % (REMOTE_DIR, unit, shlex.quote(inner)), timeout=120)
    if rc != 0:
        # Обрыв SSH уже после запуска службы — не повод запускать второй раз: служба идёт (или уже ответила) — ждём её.
        _, st, _ = ctx.server.run("systemctl is-active %s 2>/dev/null; [ -e %s ] && echo answered; true" % (unit, res),
                                  timeout=60, safe=True)
        if not re.search(r"^(active|activating|answered)$", st, re.M):
            return {"status": "error", "code": "systemd_run", "message": err.strip()[-160:]}
    t0, polled, last = time.time(), 0.0, ""
    while True:
        time.sleep(5)
        try:
            _, st, _ = ctx.server.run("systemctl is-active %s 2>/dev/null; true" % unit, timeout=60, safe=True)
        except subprocess.TimeoutExpired:
            st = "active"
        if st.strip() not in ("active", "activating", "deactivating", "reloading"):
            break
        if time.time() - t0 > timeout:
            return {"status": "error", "code": "timeout",
                    "message": "%s still running after %d s (unit %s left running)" % (action, timeout, unit)}
        if time.time() - polled >= 15:
            polled = time.time()
            text = ((bridge(ctx.server, "kmod-status").get("kmod") or {}).get("progress") or {}).get("text") or ""
            if text and text != last:
                print("       … %s" % clean(text), flush=True)
                last = text
    _, out, _ = ctx.server.run("cat %s 2>/dev/null; true" % res, safe=True)
    ctx.server.run("rm -f %s %s.part" % (res, res), safe=True)
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"status": "error", "code": "no_answer", "message": "%s left no JSON (journalctl -u %s)" % (action, unit)}


def kmod_client(ctx, sub, tag, *args, source=None, timeout=420):
    """remote/kmod_client.sh на машине-клиенте → (строка итога, её поля key=value). Все подкоманды повторяемы:
    up пересоздаёт контейнер с тем же ключом, остальные только смотрят или убирают."""
    cmd = "GA_CLIENT_IMG=%s bash %s/kmod_client.sh %s %s %s" % (ctx.image, REMOTE_DIR, sub, tag, " ".join(str(x) for x in args))
    if source:
        _, out, _ = ctx.server.pipe(source, ctx.client, cmd, timeout=timeout, safe=True)
    else:
        _, out, _ = ctx.client.run(cmd, timeout=timeout, safe=True)
    heads = {"up": ("UP ",), "probe": ("PROBE ",), "wait": ("BACK ", "LOST "), "iperf": ("IPERF ",), "down": ("DOWN ",)}[sub]
    lines = [ln for ln in out.splitlines() if ln.startswith(heads)] or (out.strip().splitlines() or ["FAIL no output"])
    line = lines[-1]
    return line, dict(w.split("=", 1) for w in line.split() if "=" in w)


def kmod_facts(ctx, user, profs, since=0):
    _, out, _ = ctx.server.run("python3 %s/kmod_server.py facts %s %s %d" % (REMOTE_DIR, shlex.quote(user), ",".join(profs),
                                                                             since), timeout=300, safe=True)
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}


def kmod_engines(ctx):
    """{профиль: (engine, engine_want)} живым опросом — так их видят панель и бот (profiles-status)."""
    return {x.get("profile"): (x.get("engine"), x.get("engine_want"))
            for x in bridge(ctx.server, "profiles-status").get("profiles") or []}


def kmod_now(host):
    _, out, _ = host.run("date +%s", safe=True)
    return int(out.strip() or 0)


def kmod_iperf(ctx, tag, p):
    """iperf3 по туннелю: сервер — одноразовый контейнер в сетевом пространстве профиля, клиент — тот же постоянный
    клиент. → «up/down Мбит/с» или причина пропуска."""
    if ctx.a.kmod_iperf <= 0:
        return None, "iperf off"
    _, out, _ = ctx.server.run("python3 %s/kmod_server.py iperf-up %s" % (REMOTE_DIR, p), timeout=300, safe=True)
    try:
        up = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        up = {"ok": False, "why": "no answer"}
    try:
        if not up.get("ok"):
            return None, "iperf skipped: %s" % up.get("why")
        line, kv = kmod_client(ctx, "iperf", tag, ctx.a.kmod_iperf, timeout=ctx.a.kmod_iperf * 2 + 180)
        if "up" not in kv:
            return None, "iperf skipped: %s" % " ".join(line.split()[2:])[:80]
        return (kv["up"], kv["down"]), "iperf %s/%s Mbit/s" % (kv["up"], kv["down"])
    finally:
        ctx.server.run("python3 %s/kmod_server.py iperf-down %s" % (REMOTE_DIR, p), timeout=120, safe=True)


def kmod_clients_rows(ctx, P, label, target, user, profs, tags, base, since, switched, speeds):
    """После шага: каждый клиент вернулся сам (рукопожатие не раньше начала шага — у перезапущенных), ping, ключи те
    же (на клиенте = в users.json и awg show сервера = как в начале), движок профиля — target по profiles-status, по
    ip -d link в контейнере и по файлу engine; iperf3. → все ли OK."""
    eng = kmod_engines(ctx)
    facts = kmod_facts(ctx, user, profs)
    all_ok = True
    for p in profs:
        f = (facts.get("profiles") or {}).get(p) or {}
        ev, ok = [], True
        if p in switched:
            line, kv = kmod_client(ctx, "wait", tags[p], since, ctx.a.kmod_wait, timeout=ctx.a.kmod_wait + 120)
            back = line.startswith("BACK ")
            ok &= back
            ev.append("back by itself after %ss" % kv.get("after") if back else "NOT back within %ds" % ctx.a.kmod_wait)
        else:
            ev.append("not restarted")
        _, pr = kmod_client(ctx, "probe", tags[p])
        ping = pr.get("ping", "fail")
        ok &= ping.startswith("ok")
        keys = (pr.get("pub") == base[p]["pub"] == f.get("user_fp") and pr.get("srv") == base[p]["srv"] == f.get("server_fp")
                and bool(base[p]["pub"]) and bool(base[p]["srv"]))
        ok &= keys
        e_now = (eng.get(p) or (None, None))[0]
        # Файл engine пишет перевод (и установка — тем, у кого его нет); до установки его может не быть вовсе.
        file_ok = f.get("engine_file") == target or (label == "baseline" and f.get("engine_file") in ("", "program"))
        ok &= e_now == target and f.get("engine") == target and file_ok
        ev.append("ping %s" % ping)
        ev.append("keys same (client %s, server %s)" % (pr.get("pub"), pr.get("srv")) if keys else
                  "KEYS CHANGED: client %s→%s (users.json %s), server %s→%s (awg show %s)" % (
                      base[p]["pub"], pr.get("pub"), f.get("user_fp"), base[p]["srv"], pr.get("srv"), f.get("server_fp")))
        ev.append("engine %s (profiles-status %s, file %s)" % (f.get("engine"), e_now, f.get("engine_file") or "-"))
        sp, sev = kmod_iperf(ctx, tags[p], p)
        if sp:
            speeds.setdefault(p, {}).setdefault(target, []).append(sp)
        ev.append(sev)
        row("%s: %s %s client" % (P, label, p), "OK" if ok else "FAIL", "; ".join(ev))
        all_ok &= ok
    return all_ok


def kmod_switch(ctx, P, label, target):
    """kmod-engine → (ответ, перезапущенные профили, без отката ли). Строка: по профилю «откуда→куда, вернулись N из
    M, через S с» — это ответ самого продукта: он ждёт, пока вернётся хоть один из тех, кто был на связи."""
    r = bridge_long(ctx, "kmod-engine", "--engine=" + target, timeout=ctx.a.kmod_engine_timeout)
    rows = r.get("profiles") or []
    ok = r.get("status") == "success" and not r.get("rolled_back")
    ev = "; ".join("%s %s→%s, back %s/%s%s%s" % (
        x.get("profile"), x.get("from") or "-", x.get("to"), x.get("returned"), x.get("alive_before"),
        " in %ss" % x.get("return_s") if x.get("return_s") is not None else "", " (%s)" % x["note"] if x.get("note") else "")
        for x in rows)
    if not ok:
        ev = "%s: %s%s%s" % (r.get("code") or r.get("status"), r.get("message") or "", "; rolled back" if r.get("rolled_back")
                              else "", "; " + ev if ev else "")
    row("%s: %s → %s" % (P, label, target), "OK" if ok else "FAIL", ev)
    return r, [x.get("profile") for x in rows if x.get("restarted")], ok


def c_kmod(ctx):
    """Модуль ядра AmneziaWG по выбору (этап Б, docs/KMOD_PLAN.md): переход с программы amneziawg-go в Docker на модуль
    ядра и обратно — в два плеча, на настоящих клиентах. Временный человек на профилях (--kmod-profiles, по умолчанию
    все awg0) и по постоянному клиенту на профиль: один ключ на всю проверку, ping раз в 2 с — «на связи», поэтому
    продукт ждёт их возвращения. Шаги: программа (как есть) → kmod-install → модуль → программа → модуль → как было.
    После каждого шага: ответ перевода (кто вернулся и за сколько, откат), движок профиля, клиенты вернулись сами, ping,
    ключи те же (отпечатки), iperf3 в обе стороны. В конце: правила MSS ровно по одному после всех перезапусков, блок
    goWARP цел и start.sh проходит sh -n, журнал ядра без BUG и soft lockup, модуль убран (или оставлен соседу),
    снимок сервера до/после — ключи и люди те же."""
    a, P, s = ctx.a, "kmod", ctx.server
    chk = bridge(s, "kmod-check").get("check") or {}
    if not chk:
        return row(P + ": host check", "FAIL", "kmod-check gave no answer (the server runs code without lib/kmod.sh?)")
    if not chk.get("ok"):
        return row(P + ": host check", "SKIP", "no module here: %s — %s" % (chk.get("code"), chk.get("reason")))
    plan, mod0 = chk.get("plan") or {}, chk.get("module") or {}
    row(P + ": host check", "OK", "kernel %s, %s%s" % (
        chk.get("kernel"), "reuse %s" % mod0.get("ko_version") if chk.get("reuse") else "apt: %s (%s MB)" % (
            " ".join(plan.get("install") or []) or "nothing", plan.get("size_mb")),
        ", Kaskad neighbour present" if chk.get("kaskad") else ""))
    profs = [p for p in (a.kmod_profiles.split(",") if a.kmod_profiles else sorted(ctx.cfg["profiles"])) if p]
    profs = [p for p in profs if p in ctx.cfg["profiles"] and (ctx.cfg["profiles"][p].get("iface") or "awg0") == "awg0"]
    if not profs:
        return row(P, "FAIL", "no awg0 profiles to test (--kmod-profiles)")
    st0, eng0 = bridge(s, "kmod-status").get("kmod") or {}, kmod_engines(ctx)
    had_module = bool(mod0.get("installed") or st0.get("installed"))
    restore_to = "kernel" if st0.get("want") == "kernel" else "program"
    if not snap_save(ctx, P, "kmod-before"):
        return
    t_start = kmod_now(s)
    name = "live-km-" + secrets.token_hex(3)
    tags = {p: "%s-%s" % (name, p) for p in profs}
    base, speeds, up = {}, {}, []
    r = bridge(s, "user-add", "--name=" + name, "--label=live kmod", "--profiles=" + ",".join(profs))
    try:
        if r.get("status") != "success":
            return row(P + ": temporary user", "FAIL", "user-add: %s" % (r.get("code") or r.get("message")))
        for p in profs:
            line, kv = kmod_client(ctx, "up", tags[p], source=conf_cmd(name, p))
            up.append(p)
            base[p] = {"pub": kv.get("pub", ""), "srv": kv.get("srv", "")}
            if not line.startswith("UP ") or kv.get("hs") in (None, "none"):
                return row(P + ": clients up", "FAIL", "%s: %s" % (p, " ".join(line.split()[:6])))
        f0 = kmod_facts(ctx, name, profs)
        bad = [p for p in profs if base[p]["pub"] != ((f0.get("profiles") or {}).get(p) or {}).get("user_fp")
               or base[p]["srv"] != ((f0.get("profiles") or {}).get(p) or {}).get("server_fp")]
        row(P + ": clients up, one key each for the whole check", "FAIL" if bad else "OK",
            "%s; client key = users.json, server key = awg show%s" % (
                ", ".join("%s %s" % (p, base[p]["pub"]) for p in profs), "; MISMATCH on " + ",".join(bad) if bad else ""))
        # Исходное — программа. Профиль уже на модуле (модуль соседа подхватил его сам или прежний выбор) — сначала
        # перевод на программу: это и есть первое плечо «модуль → программа» для этого сервера.
        if any((eng0.get(p) or ("program",))[0] != "program" for p in profs):
            since = kmod_now(ctx.client)
            _, sw, ok = kmod_switch(ctx, P, "baseline", "program")
            if not ok:
                return
            kmod_clients_rows(ctx, P, "baseline", "program", name, profs, tags, base, since, sw, speeds)
        else:
            kmod_clients_rows(ctx, P, "baseline", "program", name, profs, tags, base, 0, [], speeds)
        # Установка: согласие человека — сам вызов (мост не спрашивает); пакеты, сборка, самопроверка в netns.
        ri = bridge_long(ctx, "kmod-install", timeout=a.kmod_install_timeout)
        stt = ri.get("selftest") or {}
        cases = stt.get("cases") or []
        ok = ri.get("status") == "success" and stt.get("ok") is not False
        row(P + ": kmod-install", "OK" if ok else "FAIL",
            ("version %s on %s, %s, #218 %s, packages %s, selftest %d/%d ok, %ss" % (
                ri.get("version"), ri.get("kernel"), "reused" if ri.get("reused") else "built", ri.get("p218"),
                " ".join(ri.get("packages") or []) or "-", sum(1 for c in cases if c.get("ok")), len(cases),
                ri.get("duration_s"))) if ok else "%s: %s" % (ri.get("code") or ri.get("status"), ri.get("message")))
        if not ok:
            return
        for i, target in enumerate(KMOD_LEGS, 1):
            label = "leg %d" % i
            since = kmod_now(ctx.client)
            _, sw, ok = kmod_switch(ctx, P, label, target)
            kmod_clients_rows(ctx, P, label, target if ok else ("program" if target == "kernel" else "kernel"), name,
                              profs, tags, base, since, sw, speeds)
            if not ok:
                break
        f1 = kmod_facts(ctx, name, profs)
        mss = {p: ((f1.get("profiles") or {}).get(p) or {}).get("mss") or {} for p in profs}
        ok = all(m.get("v4") == 2 and m.get("v6") in (None, 2) for m in mss.values())
        row(P + ": TCP MSS rules once after every restart", "OK" if ok else "FAIL",
            "; ".join("%s v4 %s, v6 %s" % (p, m.get("v4"), "-" if m.get("v6") is None else m.get("v6")) for p, m in mss.items()))
        w0 = {p: ((f0.get("profiles") or {}).get(p) or {}).get("warp") for p in profs}
        w1 = {p: ((f1.get("profiles") or {}).get(p) or {}) for p in profs}
        ok = all(w1[p].get("warp") == w0[p] and w1[p].get("sh_n") and w1[p].get("engine_block") for p in profs)
        row(P + ": start.sh — goWARP block intact, engine block, sh -n", "OK" if ok else "FAIL",
            "; ".join("%s goWARP %s%s, engine block %s, sh -n %s" % (
                p, w1[p].get("warp"), "" if w1[p].get("warp") == w0[p] else " (was %s)" % w0[p], w1[p].get("engine_block"),
                "ok" if w1[p].get("sh_n") else "FAIL") for p in profs))
    finally:
        try:
            kmod_restore(ctx, P, name, profs, tags, base, eng0, restore_to, had_module, up)
        finally:
            for p in up:
                kmod_client(ctx, "down", tags[p])
            bridge(s, "user-del", "--name=" + name)
        fk = kmod_facts(ctx, name, profs, since=t_start)
        kl = fk.get("klog") or {}
        row(P + ": kernel log clean (BUG, soft lockup, Oops)", "OK" if kl and not kl.get("count") else "FAIL",
            "%s since the start: %d bad lines%s" % (kl.get("source", "?"), kl.get("count", -1),
                                                   "; " + " | ".join(kl.get("bad")[:3]) if kl.get("bad") else ""))
        if speeds:
            row(P + ": iperf3 program vs kernel (Mbit/s up/down, for the record)", "OK", "; ".join(
                "%s %s" % (p, ", ".join("%s %s" % (e, " ".join("%s/%s" % v for v in vs)) for e, vs in sorted(d.items())))
                for p, d in sorted(speeds.items())))
        snap_compare(ctx, P, "kmod-before")


def kmod_restore(ctx, P, name, profs, tags, base, eng0, restore_to, had_module, up):
    """Как было: движок (выбор до проверки: модуль — модулем, иначе программа), клиенты вернулись; модуль, которого
    до проверки не было, — прочь (--kmod-keep-module оставляет; сосед Kaskad им пользуется — продукт оставит сам)."""
    a, s = ctx.a, ctx.server
    eng = kmod_engines(ctx)
    if any((eng.get(p) or (None,))[0] != restore_to for p in profs):
        since = kmod_now(ctx.client)
        _, sw, ok = kmod_switch(ctx, P, "restore", restore_to)
        if up:
            kmod_clients_rows(ctx, P, "restore", restore_to if ok else ("kernel" if restore_to == "program" else "program"),
                              name, [p for p in profs if p in up], tags, base, since, sw, {})
    if had_module or a.kmod_keep_module:
        row(P + ": module left installed", "OK", "it was there before the check" if had_module else "--kmod-keep-module")
    else:
        r = bridge_long(ctx, "kmod-remove", timeout=900)
        f = kmod_facts(ctx, name, profs).get("module") or {}
        kept = bool(r.get("kept_for_neighbour"))
        gone = not f.get("hooks") and (kept or (not f.get("loaded") and not f.get("dkms") and not f.get("modules_load")))
        row(P + ": kmod-remove", "OK" if r.get("status") == "success" and gone else "FAIL",
            "%s; loaded %s, dkms %s, hooks %s, modules-load %s; packages stay (%s)" % (
                "kept for the Kaskad neighbour" if kept else "removed" if r.get("removed") else
                "%s: %s" % (r.get("code") or r.get("status"), r.get("message")), f.get("loaded"), f.get("dkms") or "-",
                f.get("hooks") or "-", f.get("modules_load"), r.get("command") or "-"))
    eng = kmod_engines(ctx)
    row(P + ": engines as before the check", "OK" if all((eng.get(p) or (None,))[0] == restore_to for p in profs) else "FAIL",
        "; ".join("%s %s (was %s)" % (p, (eng.get(p) or ("?",))[0], (eng0.get(p) or ("?",))[0]) for p in profs))


CHECKS = {"conn": c_conn, "disabled": c_disabled, "page": c_page, "newkey": c_newkey, "adopted": c_adopted,
          "backup": c_backup, "mode": c_mode, "mcp": c_mcp, "snapshot": c_snapshot, "isolation": c_isolation,
          "bridge": c_bridge, "share": c_share, "reboot": c_reboot, "disk-full": c_disk_full, "logrotate": c_logrotate,
          "cert-renew": c_cert_renew, "update-rollback": c_update_rollback, "upgrade": c_upgrade,
          "selfupdate": c_selfupdate, "lazy": c_lazy, "kmod": c_kmod}


class Ctx:
    pass


def hosts(a):
    """Сервер и клиент прогона: у каждого свой ключ SSH (KMODB-04) — --key у сервера, --client-key у клиента (не задан —
    тот же --key). Ключ идёт сервер → клиент трубой через эту машину (ssh_link.Host.pipe), каждая сторона — своим ключом:
    ни ssh-agent, ни ключ сервера на клиенте не нужны."""
    return Host(a.server, a.key), Host(a.client, a.client_key or a.key)


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--server", required=True, help="root@host with goAmnezia installed")
    ap.add_argument("--client", required=True, help="root@host with Docker for the real client")
    ap.add_argument("--key", default=os.path.expanduser("~/.ssh/id_ed25519"),
                    help="SSH key of the server (and of the client, unless --client-key)")
    ap.add_argument("--client-key", help="SSH key of the client when it differs from the server's (default: --key); "
                    "configs still go server → client through this machine, neither host needs the other's key")
    ap.add_argument("--checks", default=ALL_CHECKS, help="comma list: " + ",".join(CHECKS) + " (default: %s; the ops "
                    "checks and kmod — %s — run only when named)" % (ALL_CHECKS, NAMED_CHECKS))
    ap.add_argument("--user", help="existing user whose keys must connect (conn, backup, mode)")
    ap.add_argument("--conn-profiles", help="conn: only these profiles of --user (comma list, default: all) — e.g. without "
                    "the UDP/443 one when the road to the server cuts UDP 443")
    ap.add_argument("--disabled-user", help="a user disabled by hand: must NOT connect")
    ap.add_argument("--page-user", help="user of the connection page")
    ap.add_argument("--page-url-file", help="local 0600 file (outside git) with the page URL")
    ap.add_argument("--newkey-profiles", help="profiles for the temporary new user (default: all installed)")
    ap.add_argument("--adopted-baseline", help="server path of a '<container> <sha256> <path>' baseline (optional)")
    ap.add_argument("--backup-probe-user", help="existing user whose quota is changed and must come back")
    ap.add_argument("--mode-switch", metavar="PROFILE", help="also run lite_site → pro (PROFILE on UDP/443) → lite_site")
    ap.add_argument("--isolation-profile", help="profile for the isolation check (default: awg2 if the user has it)")
    ap.add_argument("--isolation-peer", help="existing user as the second client (default: a temporary new user)")
    ap.add_argument("--isolation-conf-file", help="local 0600 conf used as the first client instead of --user "
                    "(e.g. an Amnezia-app key that the server does not hold)")
    ap.add_argument("--isolation-toggle", action="store_true",
                    help="also set client_isolation=false (reachable), then restore it (blocked again)")
    ap.add_argument("--share-expiry", action="store_true",
                    help="share: wait out the link's 5 minutes (a GET near the end, then the port must be closed) instead of revoking it")
    ap.add_argument("--snapshot-save", metavar="NAME", help="save a snapshot on the server (e.g. before an upgrade)")
    ap.add_argument("--snapshot-compare", metavar="NAME", help="compare a saved snapshot (NAME or server path) with now")
    ap.add_argument("--expect-version", help="with --snapshot-compare: the version the code must have now")
    ap.add_argument("--allow-disk-full", action="store_true", help="disk-full: allow filling / to 99 %% (and 100 %% "
                    "for root for a few seconds); the fill is removed at the end, a failsafe timer removes it after 15 min")
    ap.add_argument("--allow-update-rollback", action="store_true", help="update-rollback: allow installing a candidate "
                    "with a failing migration on the stand; the previous code and state are put back at the end")
    ap.add_argument("--allow-upgrade", action="store_true", help="upgrade: allow replacing the product code on the "
                    "stand with HEAD of --upgrade-tree through bootstrap.sh (GOAMNEZIA_LOCAL_TREE), then the menu")
    ap.add_argument("--upgrade-tree", help="upgrade: local git tree to install (default: this repository); its HEAD "
                    "is sent with git archive")
    ap.add_argument("--upgrade-ref", help="upgrade: commit of --upgrade-tree to send instead of HEAD (a candidate that must "
                    "not move while other agents merge into the branch)")
    ap.add_argument("--upgrade-branch", help="upgrade: branch written to .install-source (default: the server's current)")
    ap.add_argument("--probe-user", help="disk-full: existing user whose quota is changed and put back "
                    "(default --backup-probe-user, --user)")
    ap.add_argument("--conn-conf-file", action="append", help="conn/reboot/update-rollback: a local 0600 client conf that must "
                    "connect too (e.g. a person of the Amnezia app whose key the server does not hold); repeatable")
    ap.add_argument("--reboot-timeout", type=int, default=900, help="reboot: seconds to wait for SSH with a new boot_id")
    ap.add_argument("--lazy-expect", choices=("pro", "semi_pro"), default="pro",
                    help="lazy: the outcome Lazy Pro must have given (semi_pro: UDP 443 busy or both lines already on "
                         "their ports)")
    ap.add_argument("--lazy-keys", type=int, default=5, help="lazy: how many keys Lazy Pro was asked for (default 5)")
    ap.add_argument("--kmod-profiles", help="kmod: profiles for the temporary user (comma list, default: every awg0 profile)")
    ap.add_argument("--kmod-keep-module", action="store_true", help="kmod: leave the module installed at the end "
                    "(default: kmod-remove when there was no module before the check)")
    ap.add_argument("--kmod-iperf", type=int, default=10, help="kmod: iperf3 seconds per direction after every step "
                    "(0 = no iperf3)")
    ap.add_argument("--kmod-wait", type=int, default=180, help="kmod: seconds a client may take to come back by itself")
    ap.add_argument("--kmod-install-timeout", type=int, default=2400, help="kmod: seconds for kmod-install (apt wait, "
                    "build, selftest)")
    ap.add_argument("--kmod-engine-timeout", type=int, default=1200, help="kmod: seconds for one kmod-engine switch")
    ap.add_argument("--client-image", default="auto")
    ap.add_argument("--report", help="also write the table as Markdown to this local file")
    return ap


def main():
    ap = build_parser()
    a = ap.parse_args()
    checks = [c for c in a.checks.split(",") if c]
    if a.mode_switch and "mode" not in checks:
        checks.append("mode")
    unknown = [c for c in checks if c not in CHECKS]
    if unknown:
        ap.error("unknown checks: %s" % ",".join(unknown))
    # Общий канал SSH прогона (ssh_link): одно рукопожатие на машину. Закрывается в конце — и при исключении,
    # Ctrl-C, SIGTERM и SIGHUP (сигнал становится выходом, и finally срабатывает).
    chan = ssh_link.default_channel()
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda signum, _frame: sys.exit(128 + signum))
    try:
        ctx = Ctx()
        ctx.a = a
        ctx.server, ctx.client = hosts(a)
        ctx.server.upload(SERVER_FILES)
        ctx.client.upload(CLIENT_FILES)
        rc, out, _ = ctx.server.run("jq -c '{version, mode, domain, server_ip, profiles: (.profiles | map_values({port, "
                                    "container, iface, line, imported}))}' /opt/goamnezia/config.json", safe=True)
        ctx.cfg = json.loads(out)
        ctx.server_ip = ctx.cfg.get("server_ip")
        if a.client_image == "auto":
            _, img, _ = ctx.client.run("for i in ga-test-client:latest amnezia-awg3:latest amnezia-awg2:latest "
                                       "amnezia-awg1:latest; do docker image inspect $i >/dev/null 2>&1 && echo $i && break; done",
                                       safe=True)
            ctx.image = img.strip() or "amnezia-awg2:latest"
        else:
            ctx.image = a.client_image
        print("goAmnezia %s on %s (%s, %s), client %s, image %s" % (
            ctx.cfg.get("version"), a.server, ctx.cfg.get("mode"), ctx.cfg.get("domain") or "no domain", a.client, ctx.image))
        for c in checks:
            need = {"conn": a.user or a.conn_conf_file, "backup": True, "mode": a.user, "disabled": a.disabled_user,
                    "isolation": a.user or a.isolation_conf_file, "share": a.user}.get(c, True)
            if not need:
                row(c, "SKIP", "missing --user/--disabled-user")
                continue
            try:
                CHECKS[c](ctx)
            except Exception as e:  # noqa: BLE001 — one broken check must not hide the others
                row(c, "FAIL", "harness error: %s" % e)
        fails = sum(1 for r in ROWS if r[1] == "FAIL")
        table = ["| # | check | result | evidence |", "|---|---|---|---|"] + [
            "| %d | %s | %s | %s |" % (i + 1, r[0], r[1], r[2].replace("|", "/")) for i, r in enumerate(ROWS)]
        print("\n" + "\n".join(table))
        print("\nИТОГ: OK %d, FAIL %d, SKIP %d" % (sum(1 for r in ROWS if r[1] == "OK"), fails, sum(1 for r in ROWS if r[1] == "SKIP")))
        if a.report:
            with open(a.report, "w", encoding="utf-8") as f:
                f.write("\n".join(table) + "\n")
        sys.exit(1 if fails else 0)
    finally:
        chan.close()


if __name__ == "__main__":
    main()

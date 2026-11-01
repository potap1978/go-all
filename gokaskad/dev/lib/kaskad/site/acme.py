"""certbot for the domain of the node (docs/k3/SPEC-wave4.md §4.1 п. 4–6): the package, the issue on TCP/80
(``--standalone``), the renewal, its timers and the reasons of a refusal in words people understand.

    ensure_certbot(runner)        apt install certbot (only from ``site cert issue`` — the owner's rule about
                                  dependencies), recorded in /etc/kaskad/site/pkgs -> [installed packages]
    issue_argv(domain, …)         the command line of certbot certonly (see ISSUE_FLAGS)
    run_certbot(runner, argv)     -> (Result, text of the run: stdout, stderr and what letsencrypt.log got meanwhile)
    cause(text)                   "limit" | "net" | "port80" | "dns" — why Let's Encrypt refused (as goAmnezia;
                                  "port80": ``--standalone`` could not bind TCP/80)
    retry_after(text)             "2026-10-07T12:00:00Z" from «retry after …» or None
    limit_kind(text)              which limit: "dup" (5 certificates of the same names a week) | "failed" (failed
                                  validations an hour) | "other" (the registered domain, orders, accounts)
    when_text(retry)              «2026-10-07 15:00 MSK (через 3 ч)» — the clock of this server, None when past
    refusal_text(domain, reason, retry, kind)   the text of a refusal (goAmnezia's words, Kaskad's names)
    renewal: hook_state / add_hook / drop_hook / set_autorenew   — /etc/letsencrypt/renewal/<domain>.conf
    units: ensure_renewal(runner), ensure_watch(runner), remove_units(runner)

The deploy hook is ours, per certificate: ``--deploy-hook "<kaskadctl> site cert-installed --quiet"`` (certbot keeps
it as ``renew_hook`` in the renewal file; the hook itself compares RENEWED_LINEAGE with our domain). A renewal file
whose hook is someone else's (the same name issued by another program) keeps it: the daily watchdog
(``site tick``) notices a new certificate by itself.

Renewal: the system ``certbot.timer`` when the package has it (enabled), else our ``kaskad-cert-renew.timer``
(``certbot renew --quiet`` daily at 03:17 + up to an hour). The watchdog ``kaskad-site.timer`` runs ``kaskadctl site
tick --quiet`` daily at 04:40 (+ up to 30 min).
"""

import calendar
import os
import re
import time

from .. import paths, util
from ..gw import sys as gsys
from ..i18n import fmt_datetime, tr
from . import model

CERTBOT_TIMEOUT = 600
RENEW_UNIT = "kaskad-cert-renew.service"
RENEW_TIMER = "kaskad-cert-renew.timer"
WATCH_UNIT = "kaskad-site.service"
WATCH_TIMER = "kaskad-site.timer"
SYSTEM_TIMER = "certbot.timer"
UNITS = (RENEW_TIMER, RENEW_UNIT, WATCH_TIMER, WATCH_UNIT)
HOOK_MARK = "site cert-installed"

_now = time.time

_LIMIT = re.compile(r"too many (certificates|failed authorizations|new orders|registrations)|rateLimited|/rate-limits",
                    re.I)
_NET = re.compile(r"HTTPSConnectionPool\(host='acme|Requesting acme-|acme(-staging)?-v02\.api\.letsencrypt\.org.*"
                  r"(timed out|No route to host|Connection (refused|reset|aborted)|Name or service not known|"
                  r"Temporary failure in name resolution|Network is unreachable|NewConnectionError|SSLError|"
                  r"Max retries)")
_PORT80 = re.compile(r"Could not bind TCP port 80|Problem binding to port 80|bind.{0,40}port 80.{0,60}already in use",
                     re.I)
_DUP = re.compile(r"exact set of (identifiers|domains|hostnames)|duplicate certificate", re.I)
_FAILED = re.compile(r"too many failed authorizations", re.I)
_RETRY = re.compile(r"retry after\s+(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::(\d{2}))?", re.I)


def hook_command():
    return "%s site cert-installed --quiet" % paths.kaskadctl()


# ── the package ────────────────────────────────────────────────────────────

def have_certbot(runner):
    return bool(runner.which("certbot"))


def ensure_certbot(runner):
    """certbot from apt (waiting while the system holds apt; one install of Kaskad at a time). -> installed names.
    KaskadError when apt fails."""
    if have_certbot(runner):
        return []
    got = gsys.apt_install(runner, ["certbot"])
    if "certbot" in got:                    # only what Kaskad itself installed goes to the removal line
        model.add_pkgs(["certbot"])
        util.log_tr("site.acme.log_certbot")
    return got


# ── the run ────────────────────────────────────────────────────────────────

def issue_argv(domain, email="", staging=False, force_renewal=False, webroot=None):
    """certbot argv for standalone (domain mode) or our ACME webroot (site mode)."""
    if webroot:
        challenge = ["--webroot", "-w", webroot]
    else:
        challenge = ["--standalone", "--preferred-challenges", "http"]
    argv = ["certbot", "certonly"] + challenge + ["--non-interactive", "--agree-tos", "--keep-until-expiring",
                                                     "--cert-name", domain, "-d", domain]
    argv += ["--email", email] if email else ["--register-unsafely-without-email"]
    if staging:
        argv.append("--staging")
    if force_renewal:
        argv.append("--force-renewal")
    argv += ["--deploy-hook", hook_command()]
    return argv


def renew_argv(domain):
    return ["certbot", "renew", "--cert-name", domain, "--non-interactive", "--quiet"]


def _log_size():
    try:
        return os.path.getsize(model.le_log())
    except OSError:
        return 0


def _log_since(offset):
    try:
        with open(model.le_log(), "rb") as f:
            f.seek(offset)
            return f.read(1 << 20).decode("utf-8", "replace")
    except OSError:
        return ""


def run_certbot(runner, argv):
    """(Result, text): the run and everything it said — stdout, stderr and the lines letsencrypt.log got during it
    (the reason of a refusal is often only there)."""
    start = _log_size()
    res = runner.run(argv, timeout=CERTBOT_TIMEOUT)
    text = "\n".join(x for x in (res.out, res.err, _log_since(start)) if x)
    return res, text


def cause(text):
    """limit | net | port80 | dns (goAmnezia ``_ga_site_cert_cause``: «check» of goAmnezia is «dns» of the contract;
    port80 — certbot ``--standalone`` found TCP/80 taken, Let's Encrypt was never asked)."""
    if _LIMIT.search(text or ""):
        return "limit"
    if _NET.search(text or ""):
        return "net"
    if _PORT80.search(text or ""):
        return "port80"
    return "dns"


def retry_after(text):
    found = _RETRY.findall(text or "")
    if not found:
        return None
    day, hm, sec = found[-1]
    return "%sT%s:%sZ" % (day, hm, sec or "00")


def limit_kind(text):
    """Which limit of Let's Encrypt refused (its words in certbot's output): "dup" — five certificates of exactly
    these names a week (another name has its own limit), "failed" — failed validations an hour, "other" — the
    registered domain, new orders, accounts (another subdomain does not help)."""
    if _DUP.search(text or ""):
        return "dup"
    if _FAILED.search(text or ""):
        return "failed"
    return "other"


def _when(iso):
    """«2026-10-07 15:00 MSK (через 3 ч)»: Let's Encrypt names UTC, the text says the clock of this server (its zone
    by name — UTC stays UTC) and the hours from now (T-M6-qs330c №2)."""
    try:
        ts = calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, OverflowError):
        return None
    left = ts - _now()
    if left <= 0:
        return None
    hours = int(left // 3600)
    rel = tr("site.acme.in_lt_hour") if hours < 1 else (tr("site.acme.in_hours", n=hours) if hours < 48
                                                         else tr("site.acme.in_days", n=hours // 24))
    lt = time.localtime(ts)
    return "%s %s (%s)" % (fmt_datetime(ts, now=_now()), time.strftime("%Z", lt) or "UTC", rel)


def when_text(retry):
    """The part of a refusal that says when to repeat (the page swaps it for the clock of the browser)."""
    return _when(retry) if retry else None


def _other_name(name):
    return tr("site.acme.other_name", name=name)


def refusal_text(domain, reason, retry=None, kind=None):
    """``kind`` — limit_kind of certbot's output (None: not known)."""
    name = model.unicode(domain)
    if reason == "limit":
        when = _when(retry) if retry else None
        if kind == "dup":
            return " ".join(x for x in (tr("site.acme.limit_dup", name=name),
                                        tr("site.acme.retry_at", when=when) if when else "", _other_name(name)) if x)
        if kind == "failed":
            return tr("site.acme.limit_failed", name=name, when=when or tr("site.acme.about_hour"))
        if when:
            return tr("site.acme.limit_when", name=name, when=when)
        return tr("site.acme.limit_generic", name=name)
    if reason == "port80":
        return tr("site.acme.port80")
    if reason == "net":
        return tr("site.acme.net")
    return tr("site.acme.failed", name=name)


# ── the renewal file ───────────────────────────────────────────────────────

def _read_conf(domain):
    return util.read_text(model.renewal_conf(domain))


def _write_conf(domain, text):
    p = model.renewal_conf(domain)
    try:
        mode = os.stat(p).st_mode & 0o777
    except OSError:
        mode = 0o644
    util.atomic_write(p, text, mode)


def hook_state(domain):
    """"ours" | "foreign" | "none" | "missing" (no renewal file)."""
    text = _read_conf(domain)
    if text is None:
        return "missing"
    for ln in text.splitlines():
        k, sep, v = ln.partition("=")
        if sep and k.strip() in ("renew_hook", "deploy_hook"):
            return "ours" if HOOK_MARK in v else "foreign"
    return "none"


def add_hook(domain):
    """Our deploy hook into [renewalparams] when the file has none. True if written."""
    text = _read_conf(domain)
    if text is None or hook_state(domain) != "none":
        return False
    lines = text.splitlines()
    try:
        i = next(n for n, ln in enumerate(lines) if ln.strip() == "[renewalparams]")
    except StopIteration:
        lines += ["", "[renewalparams]"]
        i = len(lines) - 1
    lines.insert(i + 1, "renew_hook = %s" % hook_command())
    _write_conf(domain, "\n".join(lines) + "\n")
    return True


def drop_hook(domain):
    """Our deploy hook out of the renewal file (someone else's stays). True if removed."""
    text = _read_conf(domain)
    if text is None:
        return False
    lines = [ln for ln in text.splitlines()
             if not (ln.partition("=")[0].strip() in ("renew_hook", "deploy_hook") and HOOK_MARK in ln)]
    if len(lines) == len(text.splitlines()):
        return False
    _write_conf(domain, "\n".join(lines) + "\n")
    return True


def set_autorenew(domain, on):
    """``autorenew = True|False`` at the top of the renewal file (before the first section). True if changed."""
    text = _read_conf(domain)
    if text is None:
        return False
    want = "autorenew = %s" % ("True" if on else "False")
    lines = text.splitlines()
    first = next((n for n, ln in enumerate(lines) if ln.strip().startswith("[")), len(lines))
    for n in range(first):
        if lines[n].partition("=")[0].strip() == "autorenew":
            if lines[n].strip() == want:
                return False
            lines[n] = want
            break
    else:
        lines.insert(first, want)
    _write_conf(domain, "\n".join(lines) + "\n")
    return True


def autorenew_off(domain):
    text = _read_conf(domain) or ""
    return any(ln.replace(" ", "").lower() == "autorenew=false" for ln in text.splitlines())


# ── standalone <-> webroot (4.0-C) ───────────────────────────────────────

def renewal_mode(domain):
    """``standalone`` | ``webroot`` | None from certbot's saved renewal file."""
    text = _read_conf(domain)
    if text is None:
        return None
    for ln in text.splitlines():
        k, sep, v = ln.partition("=")
        if sep and k.strip() == "authenticator":
            v = v.strip().lower()
            return v if v in ("standalone", "webroot") else None
    return None


def _renewal_text(domain, text, mode, webroot=None):
    """Change only certbot's authentication lines, preserving accounts and hooks."""
    lines = (text or "").splitlines()
    out, in_params, skip_map = [], False, False
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("[[") and stripped.endswith("]]"):
            skip_map = stripped == "[[webroot_map]]"
            if skip_map:
                continue
        elif skip_map:
            # The map ends at the next section.  Its ``name = root`` rows are ours to replace.
            if stripped.startswith("[") and stripped.endswith("]"):
                skip_map = False
            else:
                continue
        if stripped == "[renewalparams]":
            in_params = True
            out.append(ln)
            continue
        if in_params and stripped.startswith("[") and stripped.endswith("]"):
            in_params = False
        key = ln.partition("=")[0].strip()
        if in_params and key in ("authenticator", "webroot_path"):
            continue
        out.append(ln)
    try:
        at = next(i for i, ln in enumerate(out) if ln.strip() == "[renewalparams]") + 1
    except StopIteration:
        out += ["", "[renewalparams]"]
        at = len(out)
    out.insert(at, "authenticator = %s" % mode)
    if mode == "webroot":
        out += ["", "[[webroot_map]]", "%s = %s" % (domain, webroot)]
    return "\n".join(out).rstrip() + "\n"


def set_renewal_mode(runner, domain, mode, webroot=None):
    """Switch a certificate's next HTTP-01 attempt, returning its previous text.

    Certbot 2.x knows ``reconfigure``.  Older releases did not, so the exact
    renewal file is updated as a conservative fallback.  The caller keeps the
    returned text and restores it if the surrounding nginx transition fails.
    """
    if mode not in ("standalone", "webroot"):
        raise ValueError("renewal mode")
    old = _read_conf(domain)
    if old is None:
        return None
    if mode == "webroot" and not webroot:
        raise ValueError("webroot")
    if renewal_mode(domain) == mode and (mode != "webroot" or _renewal_text(domain, old, mode, webroot) == old):
        return old                      # already so: no ``certbot reconfigure`` (it makes a test run over the network)
    if have_certbot(runner):
        argv = ["certbot", "reconfigure", "--cert-name", domain]
        argv += ["--webroot", "-w", webroot] if mode == "webroot" else ["--standalone"]
        # Failure is expected with an older certbot; the file fallback below is
        # deliberately independent of its localized diagnostic.
        runner.run(argv, timeout=120)
    _write_conf(domain, _renewal_text(domain, _read_conf(domain) or old, mode, webroot))
    return old


def restore_renewal(domain, old):
    """Restore exactly the file saved by ``set_renewal_mode`` (None means absent)."""
    p = model.renewal_conf(domain)
    if old is None:
        try:
            os.unlink(p)
        except OSError:
            pass
    else:
        _write_conf(domain, old)


# ── units ──────────────────────────────────────────────────────────────────

def _renew_service():
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3: renewal of the Let's Encrypt certificates (certbot renew)",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        "ExecStart=/usr/bin/certbot renew --quiet",
        "TimeoutStartSec=15min",
        "",
    ])


def _renew_timer():
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3: renewal of the Let's Encrypt certificates once a day",  # one text for every language
        "",
        "[Timer]",
        "OnCalendar=*-*-* 03:17:00",
        "RandomizedDelaySec=1h",
        "Persistent=true",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])


def _watch_service():
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3: watchdog of the domain certificate expiry",  # one text for every language
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        "ExecStart=%s site tick --quiet" % paths.kaskadctl(),
        "TimeoutStartSec=15min",
        "",
    ])


def _watch_timer():
    return "\n".join([
        "[Unit]",
        "Description=Kaskad 3: watchdog of the domain certificate expiry once a day",  # one text for every language
        "",
        "[Timer]",
        "OnCalendar=*-*-* 04:40:00",
        "RandomizedDelaySec=30min",
        "Persistent=true",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])


def system_timer_present(runner):
    res = runner.run(["systemctl", "list-unit-files", "--no-legend", SYSTEM_TIMER])
    return res.ok and SYSTEM_TIMER in res.out


def ensure_renewal(runner):
    """certbot.timer (enabled now) or our kaskad-cert-renew.timer. -> the name of the timer that renews."""
    if system_timer_present(runner):
        gsys.enable(runner, SYSTEM_TIMER, check=False)
        gsys.systemd(runner, "start", SYSTEM_TIMER, check=False)
        for u in (RENEW_TIMER, RENEW_UNIT):                     # a doubled renewal of an earlier choice goes
            gsys.remove_unit(runner, u)
        return SYSTEM_TIMER
    written = gsys.write_unit(RENEW_UNIT, _renew_service())
    r = gsys.ensure_unit(runner, RENEW_TIMER, _renew_timer())
    if written and not r["written"]:
        gsys.systemd(runner, "daemon-reload")
    return RENEW_TIMER


def ensure_watch(runner):
    written = gsys.write_unit(WATCH_UNIT, _watch_service())
    r = gsys.ensure_unit(runner, WATCH_TIMER, _watch_timer())
    if written and not r["written"]:
        gsys.systemd(runner, "daemon-reload")
    return WATCH_TIMER


def remove_units(runner):
    """Our timers and services away (certbot.timer of the system stays: it is the package's). -> removed names."""
    return [u for u in UNITS if gsys.remove_unit(runner, u)]


def renew_by(runner):
    """What renews now: "kaskad-cert-renew.timer" (ours, by its file), "certbot.timer", or None."""
    if os.path.exists(gsys.unit_path(RENEW_TIMER)):
        return RENEW_TIMER
    if have_certbot(runner) and system_timer_present(runner):
        return SYSTEM_TIMER
    return None

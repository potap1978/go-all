"""A protocol (inbound) for the panel: its journal, why it does not work, a restart (3.3.3 UX).

    units(kind)                         the units whose journal tells about the inbound (AmneziaWG on the module —
                                        with the unit that loads the module at boot)
    journal(runner, kind, n)            {"lines": [{"t", "err"}], "units", "cmd"} — redacted, ≤ 400 chars a line
    fail_info(runner, kind, row, st)    None | {"why": busy|closed|cert|other, "port", "proto", "line", "human"}
    cert_bad(kind, st)                  Hysteria2 running with a pair of the certificate that is no good: why, else ""
    restart(runner, kind)               systemctl restart, up to 4 s for «active»; {"running", "at", "fail"}
    mark_issued(kind), note_sessions(sessions) the file of «выдали настройки — подключился ли кто-нибудь»

«closed»: the settings of a working protocol were given out more than CLOSED_AFTER s ago and no session of it came
since — the port is most likely closed at the hoster. /var/lib/kaskad/gw/inbound-seen.json {kind: {"issued": ts,
"seen": ts}}: ops.issue writes «issued», the collector writes «seen» once after an issue (not every round).
"""

import json
import os
import re
import ssl
import time

from .. import paths, util
from ..errors import ConflictError, KaskadError
from ..i18n import tr
from . import model, registry

CLOSED_AFTER = 600           # s after an issue without a session -> «closed»
NO_SESSIONS = ("socks5",)    # the adapter never reports sessions (xray SOCKS5): «nobody connected» would always be true
CERT_KINDS = ("hy2",)        # xray runs with a bad pair of these and only warns: the card looks at the files itself
LINES = 30
LINE_MAX = 400
WAIT_ACTIVE = 4.0            # s a restart waits for «active»
XRAY_UNIT = "kaskad-gw-xray.service"

# + «unrecognized», «no such device»: AmneziaWG/WireGuard say a bad line of the config and a missing interface so
ERR_RE = re.compile(r"error|fail|fatal|cannot|can't|denied|refused|in use|not found|invalid|panic|unrecognized|"
                    r"no such device", re.I)
BUSY_RE = re.compile(r"address already in use|address in use|EADDRINUSE", re.I)
_PREFIX = re.compile(r"^\d{4}-\d\d-\d\dT\S+\s+\S+\s+[^\s:]+:\s*")      # short-iso: «2026-10-12T14:00:00+0300 host u[1]: »

# КТ3-10: the gateway xray is one journal for VLESS, SOCKS5 and Hysteria2 (and the rest of the gateway). The reason of
# one of them is only ITS line: its tag, or its port in a «listen» line; a level below Error never; the noise of
# scanners and of other inbounds («malformed HTTP request», «failed to read», «connection ends») never
XRAY_TAGS = {"vless": "gw-vless", "socks5": "gw-socks5", "hy2": "gw-hy2"}
_XRAY_LEVEL = re.compile(r"\[(Debug|Info|Warning|Error)\]")
_XRAY_NOISE = re.compile(r"malformed HTTP request|failed to read|connection ends|mstshash", re.I)

# the frequent errors in words: (pattern, catalog key); the first that matches one of the error lines wins
HUMAN = (
    (re.compile(r"unknown device type|module \S* ?not found|modprobe: fatal|operation not supported", re.I), "kmod"),
    # the certificate before «config»: xray says «failed to load config files > … > failed to parse certificate > open
    # …/fullchain.pem: no such file or directory» — a new certificate is the fix, not a reinstall (stand 3.3.7.2 Н-8.3)
    (re.compile(r"certificate|x509|\btls:|ssl_ctx|private key", re.I), "cert"),
    (re.compile(r"line unrecognized|invalid config|configuration (?:file )?(?:error|parse)|parse error|"
                r"unknown option|invalid argument in config|failed to (?:parse|load) config", re.I), "config"),
    (re.compile(r"permission denied|operation not permitted", re.I), "denied"),
    (re.compile(r"cannot find device|no such device", re.I), "device"),
    (re.compile(r"cannot assign requested address", re.I), "addr"),
    (re.compile(r"no such file or directory|not found", re.I), "file"),
)

_now = time.time
_mono = time.monotonic
_sleep = time.sleep


def seen_path():
    return paths.P("/var/lib/kaskad/gw/inbound-seen.json")


def _ad(kind):
    return registry.get(kind)


def _is_awg(kind):
    return kind.startswith("awg")


def restart_units(kind):
    """The units a restart of the inbound restarts (the gateway xray for VLESS and SOCKS5): those whose unit file
    is on the disk (OpenVPN without its TCP instance), all of them when none is."""
    if kind in model.XRAY_KINDS:
        return [XRAY_UNIT]
    ad = _ad(kind)
    if ad is None:
        return []
    try:
        us = [str(u) for u in (ad.units() or [])]
    except Exception:                               # noqa: BLE001 — an adapter of another work package
        return []
    from .sys import unit_path
    return [u for u in us if os.path.exists(unit_path(u))] or us


def shared(kind, kinds):
    """What else a restart of ``kind`` breaks for a few seconds: {"gateway": True when the unit is the gateway xray
    (TPROXY to the exits and the DNS of the gateway go through it), "with": the other kinds of ``kinds`` on a unit of
    it (accel-ppp: L2TP, PPTP, SSTP; strongSwan: L2TP and IKEv2; xray: VLESS and SOCKS5)}."""
    mine = set(restart_units(kind))
    others = [k for k in kinds if k != kind and mine & set(restart_units(k))]
    return {"gateway": XRAY_UNIT in mine, "with": others}


def units(kind):
    out = list(restart_units(kind))
    if _is_awg(kind):
        ad = _ad(kind)
        try:
            kernel = callable(getattr(ad, "unit_core", None)) and ad.unit_core() == "kernel"
        except Exception:                           # noqa: BLE001
            kernel = False
        if kernel:
            from .awgkmod import BOOT_UNIT
            out.append(BOOT_UNIT)
    return out


def _clean(line):
    return util.redact(line)[:LINE_MAX]


def journal(runner, kind, n=LINES):
    us = units(kind)
    argv = ["journalctl", "-q", "--no-pager", "-o", "short-iso", "-n", str(int(n))]
    for u in us:
        argv += ["-u", u]
    lines = []
    if us and runner.which("journalctl"):
        r = runner.run(argv, timeout=15)
        for raw in (r.out or "").splitlines()[-int(n):]:
            if raw.strip():
                t = _clean(raw.rstrip())
                lines.append({"t": t, "err": bool(ERR_RE.search(t))})
    cmd = "journalctl %s -n %d" % (" ".join("-u %s" % u for u in us), int(n)) if us else ""
    return {"lines": lines, "units": us, "cmd": cmd.replace("  ", " ")}


_STARTING = re.compile(r"systemd\[\d+\]: (?:Starting|Started) ")


def _since_start(lines):
    """The lines of the last start (from the last «systemd: Starting/Started …»): an old «Address already in use» of an
    earlier start says nothing about why the service fails now."""
    at = 0
    for i, l in enumerate(lines):
        if _STARTING.search(l["t"]):
            at = i
    return lines[at:]


def _busy_port(kind, st, line):
    """The port of the protocol named in the «in use» line (L2TP/IPsec: 500 of 1701, 500, 4500), else the first one."""
    ad = _ad(kind)
    try:
        ports = list(ad.ports(st) or []) if ad is not None else []
    except Exception:                               # noqa: BLE001
        ports = []
    named, link = _listen_link(line)                # «listen UDP on 443»: the port of that protocol (З-2, Н-8.1)
    for n in re.findall(r"(?<![\d.])(\d{2,5})(?!\d)", link or line):     # no «listen» (L2TP/IPsec): the whole line
        for proto, port in ports:
            if int(port) == int(n) and (not named or str(proto).lower() in named):
                return int(port), str(proto)
    return _first_port(kind, st)


def _first_port(kind, st):
    ad = _ad(kind)
    try:
        ports = list(ad.ports(st) or []) if ad is not None else []
    except Exception:                               # noqa: BLE001
        ports = []
    if ports:
        proto, port = ports[0]
        return int(port), str(proto)
    return None, None


def _own_xray(kind, st, text):
    """A line of the shared xray journal that tells about inbound ``kind`` itself (КТ3-10)."""
    if _XRAY_NOISE.search(text):
        return False
    lvl = _XRAY_LEVEL.search(text)
    if lvl and lvl.group(1) != "Error":
        return False
    tag = XRAY_TAGS.get(kind)
    if tag and re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(tag), text):
        return True
    protos, link = _listen_link(text)
    if not protos:                                  # no «listen TCP/UDP» — not known whose: never (REV-3371-3 З-2)
        return False
    ad = _ad(kind)
    try:
        ports = [(str(proto).lower(), int(p)) for proto, p in (ad.ports(st) or [])] if ad is not None else []
    except Exception:                               # noqa: BLE001
        ports = []
    return any(proto in protos and re.search(r"(?<![\d.])%d(?!\d)" % p, link) for proto, p in ports)


_LISTEN_PROTO = re.compile(r"\blisten\s+(tcp|udp)[46]?\b", re.I)


def _listen_link(text):
    """({protocol}, link) of the «listen» in a line: xray «failed to listen TCP on 443», Go «listen udp 0.0.0.0:443» —
    {"tcp"}, {"udp"}. VLESS on 443/tcp and Hysteria2 on 443/udp are a usual pair: the port alone does not tell whose
    the line is. The protocol and the port are those of the LAST link of the xray chain «a > b > c» that names one (the
    cause itself, the words of Go). Stand 3.3.7.2 Н-8.1: «failed to listen TCP on 443 > … > listen udp 0.0.0.0:443:
    bind: address already in use» — xray says «TCP» for its Hysteria2 (QUIC) inbound too, the busy one is udp. No
    «listen» — (set(), "")."""
    links = [l for l in (text or "").split(" > ") if _LISTEN_PROTO.search(l)]
    if not links:
        return set(), ""
    return set(m.lower() for m in _LISTEN_PROTO.findall(links[-1])), links[-1]


def _human_key(kind, line):
    for rx, key in HUMAN:
        if rx.search(line or ""):
            if key == "kmod":
                key = "kmod_awg" if _is_awg(kind) else "kmod"
            return key
    return ""


def human(kind, line):
    """The words of the dictionary for an error line, or ""."""
    key = _human_key(kind, line)
    return tr("gw_inlog.human." + key) if key else ""


def _cert_human(kind, st):
    """REV-3373 З-3: Hysteria2 — what to press by the mode of its certificate («Применить настройки» rewrites the copy
    xray reads; the domain's own certificate — Настройки → Сайт). Never «issue it again»: ``gw hy2 rekey`` changes the
    pin, every link given out stops connecting."""
    if kind == "hy2":
        try:
            mode = _ad(kind).cert_mode(st)
        except Exception:                           # noqa: BLE001
            mode = "self"
        return tr("gw_inlog.human.cert_domain" if mode == "domain" else "gw_inlog.human.cert_self")
    return tr("gw_inlog.human.cert")


def _read_seen():
    try:
        with open(seen_path(), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _write_seen(doc):
    p = seen_path()
    util.ensure_dir(os.path.dirname(p), 0o755)
    util.atomic_write(p, json.dumps(doc, sort_keys=True) + "\n", mode=0o644)


def mark_issued(kind, now=None):
    """The settings of ``kind`` were given out now."""
    doc = _read_seen()
    rec = doc.get(kind) if isinstance(doc.get(kind), dict) else {}
    rec["issued"] = int(now if now is not None else _now())
    doc[kind] = rec
    _write_seen(doc)


def note_sessions(sessions, now=None):
    """The collector saw ``sessions`` [{"kind", "since", "last"?, "device", "ip"}]: a kind given out after its last
    session is «seen» only by a session that proves a connection after the issue (``reissue.fresh``: a handshake after
    it, a growth of xray's counters, a round 30 s after it — REV-3315-a1 З-1: an old session must not put out «порт
    закрыт?» after a change of the port). A bare kind (no session) proves nothing. The file changes only then."""
    from . import reissue
    items = [s if isinstance(s, dict) else {"kind": s} for s in sessions or [] if s]
    if not items:
        return False
    now = int(now if now is not None else _now())
    doc = _read_seen()
    changed = False
    for x in items:
        k = x.get("kind")
        rec = doc.get(k)
        if isinstance(rec, dict) and isinstance(rec.get("issued"), int) and (rec.get("seen") or 0) < rec["issued"] \
                and reissue.fresh(k, x, rec["issued"], now):
            rec["seen"] = now
            changed = True
    if changed:
        _write_seen(doc)
    return changed


def closed(kind, now=None):
    """True when the settings were given out more than CLOSED_AFTER s ago and no session of the kind came since."""
    if kind in NO_SESSIONS:
        return False
    rec = _read_seen().get(kind)
    if not isinstance(rec, dict) or not isinstance(rec.get("issued"), int):
        return False
    now = now if now is not None else _now()
    return now - rec["issued"] > CLOSED_AFTER and (rec.get("seen") or 0) < rec["issued"]


def cert_bad(kind, st):
    """Stand 3.3.7.2 Н-8.2: the pair of the certificate of a running ``kind`` (CERT_KINDS) is no good — "" when it is,
    else why: "missing" (no file or it cannot be read), "unreadable" (no certificate in it), "expired", "key" (the key
    is not of this certificate). xray 26.3.27 starts with a broken PEM of Hysteria2 and only warns «ignoring invalid
    X509 key pair» (no tag of the inbound) — no client connects, the card said «работает»."""
    if kind not in CERT_KINDS:
        return ""
    fn = getattr(_ad(kind), "cert_paths", None)
    if not callable(fn):
        return ""
    from ..site import certinfo
    chain, key = fn(st)
    try:
        with open(chain, encoding="ascii", errors="replace") as f:
            pem = f.read()
        if not os.access(key, os.R_OK):
            return "missing"
    except OSError:
        return "missing"
    try:
        info = certinfo.parse(pem)
    except ValueError:
        return "unreadable"
    if certinfo.expired(info):
        return "expired"
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(chain, key)
    except (OSError, ssl.SSLError, ValueError):
        return "key"
    return ""


def fail_info(runner, kind, row, st, log=None):
    """Why the inbound of ``row`` ({"installed", "enabled", "running"}) does not work — None when it works or it is
    not known. ``log`` — journal() already taken."""
    if not row or not row.get("installed") or row.get("enabled") is False:
        return None
    running = row.get("running")
    if running is True:
        bad = cert_bad(kind, st)
        if bad:                                     # Н-8.2: running, but no client gets through its TLS
            port, proto = _first_port(kind, st)
            return {"why": "cert", "port": port, "proto": proto, "line": "", "cert": bad,
                    "human": _cert_human(kind, st)}
        if not closed(kind):
            return None
        port, proto = _first_port(kind, st)
        return {"why": "closed", "port": port, "proto": proto, "line": "",
                "human": tr("gw_inlog.human.closed", minutes=CLOSED_AFTER // 60)}
    if running is not False:
        return None
    lines = _since_start((log or journal(runner, kind))["lines"])
    if kind in model.XRAY_KINDS:                    # КТ3-10: only its own lines of the shared xray journal
        lines = [l for l in lines if _own_xray(kind, st, _PREFIX.sub("", l["t"]))]
        lines = [dict(l, err=l["err"] or "[Error]" in l["t"]) for l in lines]
    errs = [_PREFIX.sub("", l["t"]) for l in lines if l["err"]]
    if any(BUSY_RE.search(l["t"]) for l in lines):
        line = next((e for e in errs if BUSY_RE.search(e)), "")
        port, proto = _busy_port(kind, st, line)
        return {"why": "busy", "port": port, "proto": proto, "line": line,
                "human": tr("gw_inlog.human.busy", port=port, proto=proto) if port else tr("gw_inlog.human.busy_any")}
    line, words = (errs[0] if errs else ""), ""
    for e in errs:
        words = human(kind, e)
        if words:
            line = e
            break
    if kind == "hy2" and line and _human_key(kind, line) == "cert":
        # xray stopped on the certificate of Hysteria2 (no copy, a broken one): the same words and action as Н-8.2
        port, proto = _first_port(kind, st)
        return {"why": "cert", "port": port, "proto": proto, "line": line, "cert": "", "human": _cert_human(kind, st)}
    return {"why": "other", "port": None, "proto": None, "line": line, "human": words}


def _active(runner, unit):
    return runner.run(["systemctl", "is-active", unit], timeout=10).out.strip() == "active"


def running_now(runner, kind):
    us = restart_units(kind)
    return bool(us) and all(_active(runner, u) for u in us)


def row_now(runner, st, kind):
    """{"installed", "enabled", "running"} of the inbound by systemd now (not by the collector's last round)."""
    rec = model.inbound(st, kind)
    installed = rec is not None
    enabled = installed and model.enabled(st) and kind in model.enabled_kinds(st)
    return {"installed": installed, "enabled": enabled, "running": running_now(runner, kind) if enabled else False}


def restart(runner, kind):
    from .. import state
    st = state.load()
    row = row_now(runner, st, kind)
    title = getattr(_ad(kind), "title", "") or kind
    if not row["installed"] or not row["enabled"]:
        raise ConflictError(tr("gw_inlog.err.not_on", title=title), data={"reason": "not_on"})
    us = restart_units(kind)
    if not us:
        raise KaskadError(tr("gw_inlog.err.no_units", title=title), data={"reason": "no_units"})
    for u in us:
        runner.run(["systemctl", "reset-failed", u], timeout=10)
        runner.run(["systemctl", "restart", u], timeout=60)
    end = _mono() + WAIT_ACTIVE
    while True:
        running = all(_active(runner, u) for u in us)
        if running or _mono() >= end:
            break
        _sleep(0.5)
    row["running"] = running
    return {"ok": True, "running": running, "at": int(_now()), "fail": fail_info(runner, kind, row, st)}

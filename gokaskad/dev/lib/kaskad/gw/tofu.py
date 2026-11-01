"""TOFU of a NEW key with consent (SPEC-stage1-exits §9.1; plan B9, finding Б2).

3.3.4 pins the certificates of the OLD keys of the pool with allowInsecure on the update (egress_parse.py ``tofu_snap``)
in ``/etc/kaskad/egress/tofu-pins.json``, by server («host:port|sni»). Stage 1 uses the same file and the same
``_tls_check`` — no second mechanism — for a key being ADDED over TLS (vless, trojan, vmess) without a check of the
certificate and without a fingerprint of its own (``pcs``/``pinSHA256``/``vcn``):

    check(parser, line)              -> None (nothing to ask: not such a key, or its server has a record) |
                                        {"status": "verified", "server"}   the chain and the name checked out: written
                                                                           «verified» without a question (a renewal of
                                                                           Let's Encrypt keeps the exit working);
                                        {"status": "ask", "server", "sha256"}  self-signed: the person agrees
                                                                           («1) Закрепить 2) Отказаться»), the panel —
                                                                           a button, CLI --pin-tofu, MCP pin_tofu;
                                        {"status": "ask", "server", "sha256", "name"}  the chain checks out, but for
                                                                           another name (REV-s1-t11 С4): the same
                                                                           question with the name (words — gw_tofu.
                                                                           warn_name); the consent trusts that name;
                                        {"status": "failed", "server", "why"}  no answer (a block on the way): an honest
                                                                           refusal, the key is not added;
                                        {"status": "refused", "code": "insecure"}  Hysteria2 — QUIC, no TOFU (stdlib has
                                                                           no QUIC): without pinSHA256 it is refused.
    consent(parser, line, sha256, by)  the fingerprint shown is taken again and compared: another one — KaskadError
                                        ``bad_pin`` (the certificate changed between the look and the consent); the same —
                                        the record {"pin", "at", "auto": false, "by": "panel"|"cli"|"mcp"} (another name:
                                        {"verified": true, "name", …} — xray checks the chain by that name, a renewal
                                        keeps working); then the line goes into the pool AS IT IS (its id, routes and
                                        subscriptions keep).

The record is written under the lock of the pool (``/run/kaskad/egress-pool.lock`` — the one of egress_pool_locked, so
tofu_snap never writes at the same time) and atomically (the parser's ``tofu_put``); the readers (``_tofu_load`` by
mtime) see it at once; ``backup.F_TOFU`` carries it in a copy .kbk. A renamed key (3.3.5, §3.8) has the same server —
nothing to move. Only global addresses that are not the node's own are probed (§5.4 п. 2; ``runner`` — to read them):
the name is resolved once, every address checked, and the handshake goes to the checked address (no second lookup —
REV-s1-t11 З2). Neither the key nor the fingerprint goes to the journal.
Owners of the buttons: T7/T8 (panel), T10 (CLI/MCP) — they call these two functions.
"""

import fcntl
import ipaddress
import os
import re
import socket
import time

from .. import paths, util
from ..errors import KaskadError
from ..i18n import tr

TIMEOUT = 6                         # s: one handshake (as tofu_snap)
LOCK_WAIT = 30                      # s: the lock of the pool (EGRESS_POOL_WAIT of install.sh)
BY = ("panel", "cli", "mcp")
_HEX = re.compile(r"^[0-9a-f]{64}$")

_now = time.time                    # tests patch these
_resolve = socket.getaddrinfo


def lock_path():
    return paths.P("/run/kaskad/egress-pool.lock")


def _addresses(host):
    """The addresses of ``host`` (one lookup), IPv4 first — xray dials with ForceIPv4, as _cert_probe did. [] — none."""
    try:
        infos = _resolve(host.strip("[]"), None)
    except (OSError, UnicodeError):
        return []
    out = []
    for i in infos:
        a = str(i[4][0]).split("%", 1)[0]
        if a not in out:
            out.append(a)
    return sorted(out, key=lambda a: ":" in a)


def _allowed(a, node=()):
    """A global address that is not one of the node (``node`` — ip_network-s): what may be probed."""
    try:
        ip = ipaddress.ip_address(a)
    except ValueError:
        return False
    return ip.is_global and not any(ip.version == n.version and ip in n for n in node)


def _node(runner):
    """The node's own addresses (net.node_addresses; no ``runner`` — the one of the system) as networks; () — they
    cannot be read (the fixed rules hold anyway)."""
    try:
        from . import net
        if runner is None:
            from .. import sysrun
            runner = sysrun.default_runner()
        out = []
        for a in net.node_addresses(runner):
            try:
                out.append(ipaddress.ip_network(str(a), strict=False))
            except ValueError:
                pass
        return tuple(out)
    except Exception:                                   # noqa: BLE001 — the fixed rules hold anyway
        return ()


def _global(host, node=()):
    """Every address of ``host`` is a global one and none is the node's (no private, loopback, link-local address)."""
    addrs = _addresses(host)
    return bool(addrs) and all(_allowed(a, node) for a in addrs)


def _target(parser, line):
    fn = getattr(parser, "tofu_target", None)
    if not callable(fn):
        raise KaskadError(tr("gw_tofu.old_parser"))
    try:
        t = fn(line)
        if not t:
            return None
        host = parser._host(t[1])                       # the checked host — what was checked is what is probed
        if not host or parser._server_problem(host):    # (as tofu_snap); this node, its services: never probed
            return None                                 # (the second line of _probe; REV-s1-rel-3 П-1)
        return (t[0], host) + tuple(t[2:])
    except Exception:                                   # noqa: BLE001 — a foreign file
        return None


def _probe(parser, t, node=()):
    """What the server shows now: "verified" | "verified:<name>|<sha256>" | 64 hex; raises OSError & co — no answer or
    an address that may not be probed. The name is resolved once and the handshake goes to the address checked."""
    srv, host, port, sni, alpn = t
    addrs = _addresses(host)
    if not addrs or not all(_allowed(a, node) for a in addrs):
        raise OSError("not a global address")
    return parser._cert_probe(addrs[0], port, sni or host.strip("[]"), TIMEOUT, alpn or None, sha=True)


class _Locked(object):
    def __enter__(self):
        p = lock_path()
        os.makedirs(os.path.dirname(p), mode=0o711, exist_ok=True)
        self.fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        end = _now() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if _now() >= end:
                    os.close(self.fd)
                    raise KaskadError(tr("gw_tofu.busy"))
                time.sleep(0.2)

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def _put(parser, server, rec):
    with _Locked():
        parser.tofu_put(server, rec)


def _split(got):
    """("verified" | "name" | "pin", name, sha256) of what _probe gave; (None, "", "") — nothing usable."""
    if got == "verified":
        return "verified", "", ""
    if got.startswith("verified:"):
        name, _, sha = got[9:].partition("|")
        return ("name", name, sha) if name and _HEX.match(sha) else (None, "", "")
    return ("pin", "", got) if _HEX.match(got) else (None, "", "")


def check(parser, line, by="panel", runner=None):
    """What to ask about the certificate of a key being added (above). Writes «verified» at once — only when the chain
    and the name both checked out (§9.1)."""
    line = str(line or "").strip()
    proto = getattr(parser, "link_proto", lambda _l: "")(line)
    if proto == "hysteria2":
        return {"status": "refused", "code": "insecure"} if parser.link_problem(line) == "insecure" else None
    t = _target(parser, line)
    if t is None or parser._tofu_rec(t[0]) is not None:
        return None
    server = t[0].split("|", 1)[0]
    try:
        got = str(_probe(parser, t, _node(runner)) or "")
    except Exception as e:                              # noqa: BLE001 — ssl.SSLError, OSError, ValueError
        return {"status": "failed", "server": server, "why": type(e).__name__}
    kind, name, sha = _split(got)
    if kind == "verified":
        _put(parser, t[0], {"verified": True, "at": int(_now()), "auto": False, "by": by if by in BY else "panel"})
        util.log_tr("gw_tofu.log.verified", server=server)
        return {"status": "verified", "server": server}
    if kind == "name":                                  # С4: a certificate for another name — never trusted silently
        return {"status": "ask", "server": server, "sha256": sha, "name": name}
    if kind is None:
        return {"status": "failed", "server": server, "why": "no certificate"}
    return {"status": "ask", "server": server, "sha256": sha}


def consent(parser, line, sha256, by, runner=None):
    """The person agreed to pin ``sha256`` (the fingerprint ``check`` showed): taken again and compared, written — a
    certificate for another name: that name (the chain checked by it), else the fingerprint."""
    line = str(line or "").strip()
    want = str(sha256 or "").strip().lower().replace(":", "")
    if not _HEX.match(want):
        raise KaskadError(tr("gw_tofu.bad_pin"), data={"code": "bad_pin"})
    t = _target(parser, line)
    if t is None:
        raise KaskadError(tr("gw_tofu.not_needed"), data={"code": "not_needed"})
    try:
        got = str(_probe(parser, t, _node(runner)) or "")
    except Exception as e:                              # noqa: BLE001
        raise KaskadError(tr("gw_tofu.failed", why=type(e).__name__), data={"code": "failed"})
    kind, name, sha = _split(got)
    if kind not in ("name", "pin") or sha != want:      # changed between the look and the consent (or now verified)
        raise KaskadError(tr("gw_tofu.changed"), data={"code": "bad_pin"})
    rec = {"at": int(_now()), "auto": False, "by": by if by in BY else "panel"}
    rec.update({"verified": True, "name": name} if kind == "name" else {"pin": want})
    _put(parser, t[0], rec)
    util.log_tr("gw_tofu.log.pinned", server=t[0].split("|", 1)[0], by=by)
    return {"status": "pinned", "server": t[0].split("|", 1)[0]}

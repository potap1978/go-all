"""Exits of the gateway (docs/k3/SPEC-wave2-gateway.md §1.3): the pool of «Прокси для API Telegram».

The pool is ``/etc/kaskad/egress/upstream.txt`` (one key per line: ``vless://...`` or ``awg://<base64>``),
parsed by the SAME ``egress_parse.py`` the egress uses (imported by path with importlib; missing ->
``gokaskad --egress-ensure-parser``). Nothing here edits the pool.

    id      ``e-`` + the first 10 hex of sha1(line): the line number changes when a key is removed, the id not
    AWG     line N -> interface kawgN, mark and route table 256+N (each leg is its own systemd unit
            kaskad-leg@kawgN, brought up by «Прокси для API Telegram» — ``gokaskad --egress-awg-up``; on the
            AmneziaWG kernel module when acceleration is on, gw/awgkmod.py; the table has ``unreachable default
            metric 1000``). The gateway keeps them whole with ``gokaskad --egress-awg-heal`` (``ensure_legs``):
            a missing leg is brought up alone, a leg whose rule or routes are gone is repaired in place — the
            working legs are never touched
    VLESS   line N -> mark 0x300+N: TPROXY to its own port 127.0.0.1:12400+N, an xray inbound
            ``tproxy-<id>`` routed straight to the outbound ``gx-<id>`` (the parser's vless_outbound).
            Mark 0x200 is the pool through xray: the balancer over the live VLESS legs.

Liveness (``probe``): AWG — the interface exists and a TCP connection through the leg itself answers every
round (``connect_through``: the leg's mark 256+N, so its own ip rule and table — the path of its clients — to
1.1.1.1, 8.8.8.8 and 9.9.9.9 port 443 at once, any one answering; in this process, no command, all legs in
one ≤4 s wait). The handshake is shown, not believed: WireGuard re-handshakes only every 2 minutes, the last
handshake of a dead leg stayed «fresh» for up to 3 minutes and its clients sat without internet that long (stand
T-G1, C3-1). Nothing passive is believed either: packets keep coming in through a leg whose sending is cut
(the exit retransmits to its clients for minutes) — only an answer to our own connection counts. While a leg
fails, the collector checks again in 5 s instead of 10 (``failing``). VLESS — the observatory of the gateway's xray
(``/debug/vars`` of its metrics listener: alive per outbound tag). A probe answers alive, dead or UNKNOWN:
xray does not answer, has not probed the exit yet, or was (re)started less than 30 s ago and says "dead".
``merge`` keeps the previous state on UNKNOWN; UNKNOWN for UNKNOWN_MAX s (xray of the node silent) makes the exit
dead at once, counted from the start of the silence. The rule «упал» (the owner, 15.10, 3.3.5) — the same for VLESS and
AmneziaWG: at least DEAD_SHARE (half) of the checks of the last WINDOW s (2 min) failed, MIN_TRIES checks at the least,
decided on a failed one; alive again after RISE good checks in a row; the window starts anew on a change of the state
except between ok, degraded and standby (the exit is probed all along: Н12, health._events). A check is one probe per CHECK_PERIOD (10 s, CHECK_SLACK s of slack): AmneziaWG — a connection of its own each
round, a recheck 5 s later replaces the result of the last check; VLESS — one probe of xray's observatory, told by its
``last_try_time`` (read twice it counts once; the same probe for UNKNOWN_MAX s — xray stopped probing — is the silence
above). A whole failure is told in about a minute for AmneziaWG, in 1–1.5 for VLESS (stand T-335-release); one lost probe or a short run of them never moves clients (stand: «мигает в Событиях», 15.10). Right after a reboot (``boot.pending``) a leg that systemd is still
bringing up is UNKNOWN too, and so is any leg brought up less than LEG_GRACE s ago and silent: the devices are not
moved away and back while the legs start (T-M6-qs330b №8). The snapshot of the probes is
``/etc/kaskad/gw/exits.json`` (written only when the state of an exit changes; ``fail_since`` — the first
failed probe of the current run); renderers read it.

Names (``label``): the one a person gave (``gw exit --name``), else the key's own (#fragment of vless://, the
description of vpn://), else ``where`` — «AmneziaWG 2.0 · 203.0.113.141», «VLESS · 203.0.113.141» (stand T-G1,
B1-4: two keys «amneziawg» were told apart only by the version).

The mark of a device (``target``) — route ``device -> user -> default``:
    block  -> 0x2ff            direct -> 0x201
    exit:E -> E alive and enabled: its mark (256+N or 0x300+N); else the fail policy (block 0x2ff / direct 0x201)
    pool   -> live enabled exits; the device's leg by rendezvous hash (HRW: only the clients of a dead leg
              move, the others stay): an AmneziaWG leg -> 256+N, a VLESS leg -> 0x200 (the xray balancer
              over the live VLESS legs, leastPing, fallback block); none alive -> the fail policy
A route change of a device is a change of its element in gw_route only: xray's config does not depend on
the routes of TPROXY clients (only on the pool and the SOCKS5 accounts).

The marks follow the LINE of an exit, not the exit: a key removed from the pool (or the pool replaced) shifts
the lines after it — kawg3 becomes kawg2, mark 0x103 now leads to another exit. gw_route follows at once
(install.sh runs ``gw apply`` after every change of the pool; else the collector within 10 s), but live
connections keep their ct mark and would stay on the wrong exit: ``renumbered`` (old pool ids vs new) names the
lines whose exit changed and the core flushes the connections of their marks (``net.flush_marks``).
"""

import base64
import collections
import contextlib
import copy
import errno
import fcntl
import hashlib
import importlib.util
import io
import ipaddress
import json
import os
import re
import selectors
import socket
import struct
import threading
import time
import urllib.parse

from .. import boot, paths, util
from ..errors import USAGE, KaskadError
from ..i18n import LANGS, has, lookup, tr
from . import model

AWG_MARK_BASE = 256
MARK_XRAY = 0x200                   # the pool through xray: balancer over the live VLESS legs
MARK_DIRECT = 0x201
MARK_NODE = 0x202                   # set by gw_pre on packets to the node itself (its services, wave-1 forwards)
MARK_BLOCK = 0x2ff
VLESS_MARK_BASE = 0x300             # VLESS exit of line N: 0x300+N (its own TPROXY port and xray inbound)
MAX_AWG_INDEX = 255                 # 256+N must stay below 0x200
MAX_VLESS_INDEX = 255               # 0x300+N must stay below 0x400
RU_INDEX_BASE = 200                 # line N of upstream-ru.txt is exit 200+N (T15, SPEC-groups §2.2): legs kawg201…, marks
RU_MAX = 32                         # 456+N / 0x3c8+N, TPROXY 12600+N — at most 32 Russian keys (201–232)
VLESS_PORT_BASE = 12400             # TPROXY of the VLESS exit of line N: 127.0.0.1:12400+N (a foreign one)
RU_PORT_FIRST = VLESS_PORT_BASE + RU_INDEX_BASE + 1   # 12601…12698: TPROXY of a Russian xray exit — by its ID, not its
RU_PORT_LAST = 12698                                  # line (``ru_ports``, SEC-s1-q2 С-2); 12699 — pool-socks of xray
XRAY_UNIT = "kaskad-gw-xray.service"
FAILS_TO_DIE = 2                    # (before 3.3.5) failed probes in a row before a VLESS exit was dead
AWG_FAILS_TO_DIE = 3                # (before 3.3.5) the same of AmneziaWG — now the window below for both
WINDOW = 120                        # s: «упал» — at least DEAD_SHARE of the checks of the last WINDOW s failed (owner 15.10)
DEAD_SHARE = 0.5
MIN_TRIES = 4                       # checks in the window at the least (1 failure of 2 right after a start is not «half»)
RISE = 2                            # good checks in a row that bring a dead exit back (the window starts anew)
CHECK_PERIOD = 10                   # s: one check of the window per period (collect.INTERVAL) — a recheck 5 s later
                                    # (a failing AmneziaWG leg) replaces the result of the last one, never adds one
CHECK_SLACK = 2                     # s: a check less than CHECK_PERIOD - CHECK_SLACK after the last one replaces it (the
                                    # coordinator 15.10, as in stage 1: P − 2 s) — a failing leg waits up to PROBE_TIMEOUT
                                    # and RECHECK: its rounds come 9–10 s apart, and each of them is a check of its own.
                                    # Not symmetric (REV-335-3 №4): a GOOD check replaces a failed one up to CHECK_PERIOD
                                    # (as before P − 2 s) — on a slow node the good recheck of one lost probe comes 8–10 s
                                    # later and must still cancel it; a failed one replaces only within P − 2 s
OBS_ERROR_MAX = 200                 # characters of xray's last_error_reason in the journal
XRAY_GRACE = 30                     # s after an xray (re)start: "dead" from its observatory is not believed yet
LEG_GRACE = 30                      # s after a leg's unit came up (the boot, a heal): «no answer» is not believed yet —
                                    # its first handshake (T-M6-qs330b №8: exits flapped at boot)
UNKNOWN_MAX = 60                    # s of unknown answers after which they count as failed probes
PROBE_URL = "http://cp.cloudflare.com/generate_204"   # plain HTTP, as the egress observatory (TLS curve bug)
PROBE_TIMEOUT = 4                   # s: one wait for the connections of every leg (SYN is resent at 1 s and 3 s)
PROBE_TARGETS = (("1.1.1.1", 443), ("8.8.8.8", 443), ("9.9.9.9", 443))   # Cloudflare, Google, Quad9: any one
PROBE_STAGGER = 1.0                 # s: the next target of a leg only when the earlier ones have not answered (Н3)
SO_MARK = getattr(socket, "SO_MARK", 36)          # Linux: the route mark of a socket (root)
OUTBOUND_MSS = 1000                 # tcpMaxSeg of VLESS outbounds, the egress default EGRESS_MSS
METRICS_ADDR = "127.0.0.1:10852"    # metrics listener of kaskad-gw-xray (observatory in /debug/vars)
HEAL_RETRY = 60                     # ``--egress-awg-heal`` that did not help: again in a minute (one that did — at once)
BACKSTOP_METRIC = 1000              # ``unreachable default metric 1000`` in table 256+N (egress_awg_route_leg)
REQUIRED = ("vless_outbound", "awg_outbound", "pool_label", "awg_version", "xray_outbound", "link_proto", "peer_key")
PARSER_MIN = 28                     # egress_parse.py (PARSER_VERSION) this code needs: 9 — amneziawg://, 15 — pcs (the
#                                     pinned certificate) and no allowInsecure, 16 — pins taken on the update, 17 — "wait"
#                                     of a key whose server is tried again, 18 — verified by the name of the certificate,
#                                     19 — peers of AmneziaWG by the client (3.3.5), 20 — hy2/ss/trojan/vmess (stage 1, B9),
#                                     the address of a key checked (bad_link, bad_addr: 3.3.5.1); 21–25 of 3.3.6 — the
#                                     Address of AmneziaWG private, the Endpoint not the node, resolved and checked
#                                     (self_ep), no DNS in the lists, gw_net, the server of VLESS not the node; 26 — stage 1
#                                     and 3.3.6 in one parser (v20 of stage 1 and v25 of 3.3.6 are both too old here),
#                                     self_ep for the server of every xray scheme; 28 — the same with 26–27 of 3.3.6: the
#                                     host of a key without one dot at the end, no «..», names of the node and of the
#                                     hoster without DNS, TOFU never probes the node (27 is the number of 3.3.6 alone,
#                                     without the xray keys of stage 1 — too old here: REV-336-dot З-1)

_now = time.time                    # tests patch this
_mono = time.monotonic              # the clock of systemd's *TimestampMonotonic (CLOCK_MONOTONIC)
_cache = {}
_refreshed = set()                  # (path, mtime, size) of an old parser already sent to --egress-ensure-parser


# sys.stderr is one for the whole process: redirect_stderr of two threads at once (two previews of the panel, a preview
# and a read of the pool) would leave it on a StringIO for good — the journal of the panel would go silent. One lock
# for every redirection here (reentrant: pool() under preview() and so on).
_STDERR = threading.RLock()


@contextlib.contextmanager
def _quiet():
    """The parser's own complaints (stderr) away — one thread at a time."""
    with _STDERR, contextlib.redirect_stderr(io.StringIO()):
        yield


def _s4(rec):
    """S4 of an AmneziaWG key (the leg's MTU depends on it, gw/mtu.py): 0 for 1.x."""
    v = str(((rec or {}).get("params") or {}).get("S4") or "").strip()
    return int(v) if v.isdigit() else 0


def _loss226(rec):
    """A key of 3.1 in the zone of losses of #226 (awgkmod.trailers_loss_risk): a warning, the key works."""
    from . import awgkmod
    try:
        return awgkmod.trailers_loss_risk((rec or {}).get("params") or {})
    except Exception:                               # noqa: BLE001 — a key we cannot judge is not a warning
        return False


# ── words: a code and its arguments, the text in the language of the moment ──
# The state of an exit is kept on disk (exits.json) and shown elsewhere — the panel, the bot, the SSH menu, the journal —
# in the language of THEIR moment: a record keeps ``detail_code`` (a key of the catalog after «gw_exits.»: «detail.
# no_answer», «problem.too_far») and ``detail_args``, its ``detail`` is only the words of the writer (read by an
# earlier version). ``detail_wait`` — seconds of unknown answers that counted as a failed probe, ``detail_window`` —
# [failed checks, checks] of the window while an exit still works (3.3.5); ``detail_fail`` — the same of a record
# before 3.3.5: [failed probes in a row, of how many] — told in its own words «(сбой 1 из 3)». A record of an earlier
# version has only ``detail`` (Russian):
# ``parse_detail`` finds its code by the templates of the catalog.

def word(code, **args):
    """The text of a code of a detail or a problem («detail.no_iface», iface="kawg2") in the language of the moment."""
    return tr("gw_exits." + code, **args) if args else tr("gw_exits." + code)


def quoted(name):
    """«Вена» / “Vienna” — a name in the quotes of the language."""
    return tr("gw_exits.quoted", name=name)


def _problem(e, code, **args):
    """The problem of a key of the pool: its words (``problem``) and its code (``problem_code``, ``problem_args``)."""
    e["problem_code"] = "problem." + code
    if args:
        e["problem_args"] = args
    e["problem"] = word(e["problem_code"], **args)


DETAIL_CODES = ("detail.answers", "detail.no_root", "detail.no_answer", "detail.leg_booting", "detail.no_iface",
                "detail.leg_young", "detail.not_checked", "detail.xray_silent", "detail.xray_unchecked",
                "detail.server_answers", "detail.xray_young", "detail.server_silent", "detail.xray_stale", "problem.dup", "problem.too_far", "problem.ru_too_far",
                "problem.awg3_lost", "problem.awg_bad_addr", "problem.awg_self_ep", "problem.awg_gw_net", "problem.vless_self_ep", "problem.vless_unparsed", "problem.vless_insecure", "problem.vless_bad_pin",
                "problem.vless_tofu_wait",
                "problem.unknown_format", "problem.unparsed", "problem.no_parser", "problem.unsupported_scheme") \
    + tuple("problem.key_" + c for c in ("insecure", "bad_pin", "tofu_wait", "no_tls", "weak_cipher", "bad_link",
                                         "unsupported_scheme", "dup_endpoint", "unparsed", "unsupported", "insecure_quic",
                                         "xray_refused", "self_ep"))
VLESS_PROBLEMS = ("insecure", "bad_pin", "tofu_wait", "self_ep")    # egress_parse.vless_problem (3.3.4): the key alone is unusable;
#                                                           tofu_wait — its server did not answer on the update, tried again;
#                                                           self_ep — the server is this node (3.3.6, round 3; any xray scheme
#                                                           of stage 1: «problem.key_self_ep»)
# «(не прошло проверок: 2 из 5 за 2 мин)» (3.3.5: ``detail_window``) and «(сбой 1 из 3)» of a record written before it
# (``detail_fail``) — each told again in its own words: their numbers mean different things (REV-335 №5)
FAIL_FIELDS = (("gw_exits.detail.fail_of", "detail_window"), ("gw_exits.detail.fail_of_334", "detail_fail"))
FAIL_KEYS = tuple(k for k, _ in FAIL_FIELDS)


def _template_rx(text):
    """A regex of a text of the catalog: its ``{name}`` — any text (``n``, ``of``, ``s``: digits)."""
    out, names, pos = [], [], 0
    for m in re.finditer(r"\{(\w+)\}", text):
        out.append(re.escape(text[pos:m.start()]))
        name = m.group(1)
        names.append(name)
        out.append(r"(\d+)" if name in ("n", "of", "s", "max") else r"(.+?)")
        pos = m.end()
    out.append(re.escape(text[pos:]))
    return re.compile("^" + "".join(out) + "$"), names


def _match(key, lang, text):
    """{name: value} of ``text`` made by the template ``key`` of ``lang``, or None."""
    tpl = lookup(key, lang)
    if not tpl:
        return None
    rx, names = _template_rx(tpl)
    m = rx.match(text)
    if not m:
        return None
    return dict((n, int(v) if n in ("n", "of", "s", "max") else v) for n, v in zip(names, m.groups()))


def parse_detail(text):
    """{"detail_code", "detail_args"?, "detail_wait"?, "detail_window"? | "detail_fail"?} of the words of a detail written
    in any language (a record of an earlier version: Russian only), or None when they are not ours."""
    text = str(text or "").strip()
    if not text:
        return None
    out = {}
    for lang in LANGS:                                  # the suffixes «(сбой 1 из 3)», «(65 с)» — of either language
        for key, field in FAIL_FIELDS:
            m = _match(key, lang, text)
            if m and "detail_window" not in out and "detail_fail" not in out:
                out[field] = [m["n"], m["of"]]
                text = m["detail"]
        m = _match("gw_exits.detail.waited", lang, text)
        if m and "detail_wait" not in out:
            out["detail_wait"] = m["s"]
            text = m["detail"]
    for lang in LANGS:
        for code in DETAIL_CODES:
            args = _match("gw_exits." + code, lang, text)
            if args is not None:
                out["detail_code"] = code
                if args:
                    out["detail_args"] = args
                return out
    return None


def _strip_fail(text):
    """The words of a detail without its suffix «(сбой 1 из 3)» of either language."""
    for lang in LANGS:
        for key in FAIL_KEYS:
            m = _match(key, lang, text)
            if m:
                return m["detail"]
    return text


def _code_of(rec):
    """(code, args) of a record or a row: its own code, else the code of its words (a record of an earlier version)."""
    code = rec.get("detail_code")
    if isinstance(code, str) and has("gw_exits." + code):
        args = rec.get("detail_args")
        return code, dict(args) if isinstance(args, dict) else {}
    got = parse_detail(rec.get("detail")) if "detail_code" not in rec else None
    if got:
        return got["detail_code"], dict(got.get("detail_args") or {})
    return None, {}


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def detail_text(rec, fail=True):
    """The words of the detail of a snapshot record (or a probe answer) in the language of the moment: its code; a record
    without one — its words as they were written (with their suffixes). ``fail`` — with «(сбой 1 из 3)»."""
    rec = rec if isinstance(rec, dict) else {}
    code, args = _code_of(rec)
    if not code:
        return str(rec.get("detail") or "")
    old = parse_detail(rec.get("detail")) if not rec.get("detail_code") else None
    src = old if old is not None else rec
    text = word(code, **args)
    w = _int(src.get("detail_wait"))
    if w is not None:
        text = tr("gw_exits.detail.waited", detail=text, s=w)
    for key, field in FAIL_FIELDS if fail else ():
        f = src.get(field)
        if isinstance(f, (list, tuple)) and len(f) == 2 and all(_int(x) is not None for x in f):
            return tr(key, detail=text, n=f[0], of=f[1])
    return text


# ── the kind, the protocol and the role of an exit (stage 1, T1: SPEC-stage1-exits §9.3, §3.1) ──
# An exit through xray is of the kind "xray" with its protocol in ``proto`` (vless, hysteria2, shadowsocks, trojan,
# vmess); before stage 1 it was the kind "vless" — files on the disk and rows of an older Kaskad are read as "xray"
# (``is_xray``). The kind of an INBOUND "vless" (model.XRAY_KINDS, inbounds/vless.py) is another thing and stays.
XRAY = "xray"
OLD_XRAY = "vless"                  # the kind of an xray exit in 3.3.5 and before
KINDS = ("awg", XRAY)               # the kinds of a parsed exit; "bad" — a key that is not
ROLES = ("abroad", "ru")            # the role of an exit by its file: upstream.txt — abroad, upstream-ru.txt — ru (T15)
_TAG_PREFIX = {"abroad": "gx-", "ru": "gr-"}    # the xray outbound of an exit; gw-all/observatory select "gx-" only
ABROAD_PREFIX = _TAG_PREFIX["abroad"]           # the selector of gw-all and the observatory: foreign exits only
RU_PREFIX = _TAG_PREFIX["ru"]                   # a Russian exit: for D-136 its lists rules are «Россия напрямую» (SEC-s1-q2 С-1)
PROTO_TITLES = {"vless": "VLESS", "hysteria2": "Hysteria2", "shadowsocks": "Shadowsocks", "trojan": "Trojan",
                "vmess": "VMess"}
_SCHEMES = {"vless": "vless", "hysteria2": "hysteria2", "hy2": "hysteria2", "ss": "shadowsocks", "trojan": "trojan",
            "vmess": "vmess"}


def is_xray(e):
    """An exit through xray (a row of ``pool``, ``public``, the snapshot or the API): the kind "xray", or "vless" of
    3.3.5 and before."""
    return isinstance(e, dict) and e.get("kind") in (XRAY, OLD_XRAY)


def parsed(e):
    """An exit of a known kind (AmneziaWG or xray) — what may carry traffic; not a key that is "bad"."""
    return isinstance(e, dict) and (e.get("kind") == "awg" or is_xray(e))


def link_proto(line):
    """The protocol of a link by its scheme — "vless", "hysteria2", "shadowsocks", "trojan", "vmess" — or ""."""
    scheme = str(line or "").strip().split("://", 1)[0].lower() if "://" in str(line or "") else ""
    return _SCHEMES.get(scheme, "")


def proto_title(proto):
    """«VLESS», «Hysteria2», … of a protocol ("" when unknown)."""
    return PROTO_TITLES.get(str(proto or ""), "")


def proto_of(e):
    """The protocol of an xray exit: its ``proto``, else by the scheme of its line, else "vless" (a row of 3.3.5)."""
    return str(e.get("proto") or "") or link_proto(e.get("line")) or OLD_XRAY


def role_of(e):
    """"abroad" | "ru" of an exit (a row without the field — of 3.3.5 — is abroad)."""
    r = e.get("role") if isinstance(e, dict) else None
    return r if r in ROLES else "abroad"


def tag(eid, role="abroad"):
    """The xray outbound of an exit: «gx-<id>» abroad, «gr-<id>» of a Russian exit (T15). The one place that makes it —
    never build «gx-» + id elsewhere (SPEC-stage1-exits §3.1)."""
    return _TAG_PREFIX.get(role, _TAG_PREFIX["abroad"]) + eid


def outbound_server(ob):
    """(host, port) of the server of an xray outbound: ``settings.vnext[0]`` (vless, vmess), ``settings.servers[0]``
    (trojan, shadowsocks), ``settings.address``/``port`` (hysteria) — (None, None) when there is none."""
    s = (ob or {}).get("settings") if isinstance(ob, dict) else None
    if not isinstance(s, dict):
        return None, None
    for key in ("vnext", "servers"):
        lst = s.get(key)
        if isinstance(lst, list) and lst and isinstance(lst[0], dict) and lst[0].get("address"):
            return str(lst[0]["address"]), _int(lst[0].get("port"))
    if s.get("address"):
        return str(s["address"]), _int(s.get("port"))
    return None, None


def tproxy_tag(eid):
    """Tag of the xray inbound of one VLESS exit (its own TPROXY port)."""
    return "tproxy-" + eid


def tproxy_port(e):
    """TPROXY port of an xray exit: 12400 + its line number (a foreign one); a Russian one — its own port by its id
    (``ru_ports``: a line removed above it never gives its port to somebody else's key, SEC-s1-q2 С-2)."""
    if role_of(e) == "ru":
        if not e.get("port"):
            ru_ports([e], full=False)
        return e.get("port")
    return VLESS_PORT_BASE + int(e["index"])


def _ru_ports_path():
    return os.path.join(paths.egress_dir(), "ru-ports.json")


@contextlib.contextmanager
def _ru_ports_lock():
    """flock of ``.ru-ports.lock`` next to the file (the readers of the pool in other processes allocate too); no
    lock possible (a read-only disk, a test without the directory) — without it: nothing is written then either."""
    f = None
    try:
        f = open(os.path.join(paths.egress_dir(), ".ru-ports.lock"), "a")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    except OSError:
        if f is not None:
            f.close()
        f = None
    try:
        yield f is not None
    finally:
        if f is not None:
            f.close()


def _xray_tproxy_ports():
    """{port: {tags}} of the TPROXY inbounds of the xray config on the disk and of the one a closing set aside (what may
    still run): a port there is never given to another exit."""
    from . import xray
    out = {}
    for p in (xray.config_path(), xray.closed_path()):
        cfg = xray._read_config(p)
        for ib in (cfg or {}).get("inbounds") or []:
            if isinstance(ib, dict) and str(ib.get("tag") or "").startswith("tproxy-") and \
                    isinstance(ib.get("port"), int) and not isinstance(ib.get("port"), bool):
                out.setdefault(ib["port"], set()).add(ib["tag"])
    return out


def _ru_port_ok(p):
    return isinstance(p, int) and not isinstance(p, bool) and RU_PORT_FIRST <= p <= RU_PORT_LAST


def ru_ports(rows, full=True):
    """SEC-s1-q2 С-2: the TPROXY port of each Russian xray exit of ``rows`` (``e["port"]``) — bound to its ID for good,
    not to its line. The kernel (gw_rx -> mark -> «tproxy to port») is applied before xray (gw apply, the collector):
    with ports by the line a key removed above another gave its port to the next one while xray still ran the old
    config — the neighbour's device went to the inbound of somebody else's home router. Now a port names one exit:
    the old xray sends it where the new table means, or nowhere (no inbound there yet — closed).

    ``ru-ports.json`` {"ports": {id: port}, "next": port} in the egress directory, under its flock. A new id gets the port
    the xray config on the disk already has for it (a lost file), else the next free one round the range (a freed port
    comes back only after the whole range — never at once), never one the xray config (or the closed one) gives to
    another exit. ``full`` — ``rows`` are ALL the Russian lines: the ids that left go from the file. A port not found
    (the range full, unreachable with RU_MAX keys) — the exit is not usable (``ru_too_far``)."""
    want = [e for e in rows if role_of(e) == "ru" and is_xray(e) and e.get("mark") and not e.get("dup_of")]
    if not want and not (full and os.path.exists(_ru_ports_path())):
        return rows
    span = RU_PORT_LAST - RU_PORT_FIRST + 1
    with _ru_ports_lock() as locked:
        raw = util.read_text(_ru_ports_path())
        try:
            doc = json.loads(raw) if raw else {}
        except ValueError:
            doc = {}
        doc = doc if isinstance(doc, dict) else {}
        had = doc.get("ports") if isinstance(doc.get("ports"), dict) else {}
        ports, used = {}, set()
        for i, p in sorted(had.items(), key=lambda kv: str(kv[0])):
            if _ID_RX.match(str(i)) and _ru_port_ok(p) and p not in used:
                ports[i] = p
                used.add(p)
        nxt = doc.get("next") if _ru_port_ok(doc.get("next")) else None
        running = None
        if nxt is None:                                 # REV-s1-q2-2 П-1: the file lost — never 12601 again: past the
            running = _xray_tproxy_ports()              # highest port the xray configs (on the disk, the closed one) and
            top = max([p for p in running if _ru_port_ok(p)] + list(used) or [None], key=lambda x: x or 0)
            # the file know, and past RU_MAX more: the keys a write just removed (the old xray may still run them) sat
            # below the lost cursor, at most RU_MAX of them above the highest port still known
            nxt = RU_PORT_FIRST if top is None else RU_PORT_FIRST + (top - RU_PORT_FIRST + 1 + RU_MAX) % span
        for e in want:
            i = e["id"]
            if i in ports:
                continue
            if running is None:
                running = _xray_tproxy_ports()
            own = tproxy_tag(i)
            seed = sorted(p for p, tags in running.items() if tags == {own} and _ru_port_ok(p) and p not in used)
            got = seed[0] if seed else None
            for k in range(span) if got is None else ():
                p = RU_PORT_FIRST + (nxt - RU_PORT_FIRST + k) % span
                if p not in used and running.get(p, {own}) == {own}:
                    got = p
                    break
            if got is None:
                continue
            ports[i] = got
            used.add(got)
            if not seed:
                nxt = RU_PORT_FIRST + (got - RU_PORT_FIRST + 1) % span
        keep = dict((e["id"], ports[e["id"]]) for e in want if e["id"] in ports) if full else ports
        new = {"ports": dict(sorted(keep.items())), "next": nxt}
        if locked and new != doc:
            try:
                util.atomic_write(_ru_ports_path(), util.json_text(new), 0o600)
            except OSError as err:
                util.log_tr("gw_exits.log.ru_ports", err=err)
    for e in want:
        p = ports.get(e["id"])
        if p:
            e["port"] = p
        else:                                  # no port: never a TPROXY of another — the exit is not usable
            e.update(kind="bad", mark=None, outbound=None, port=None)
            _problem(e, "ru_too_far", max=RU_MAX)
    return rows


def exit_id(line):
    """The id of a line by its text (sha1) — what install.sh computes too (``--egress-del``, subs-excluded.pending).
    The id of the exit in the pool is ``pool_id``: the same, unless a subscription renamed the key."""
    return "e-" + hashlib.sha1(line.strip().encode("utf-8")).hexdigest()[:10]


_IDS_CACHE = {}


def kept_ids():
    """3.3.5: {id of a line: id of its exit} — keys a subscription gives under another line now (another name after
    «#», other parameters; the same server and credentials — ``identity``) keep the id of the exit they had: the routes
    of devices and people, the name and the switch of the exit point to it (exitsubs ``plan``, ``_apply``). Kept in
    subs.json («ids»: it goes with the copy .kbk); no file or a broken one — {} (every id by its text, as 3.3.4;
    ``kept_ids_ok`` tells the broken one apart)."""
    return _kept()[0]


def kept_ids_ok():
    """False when subs.json is there but cannot be read or parsed (REV-3371-3 З-1): ``kept_ids`` is {} then, so the ids
    by text are not the ids of the exits — nothing may be taken out of the groups on them (``policy_ops.known_ids``)."""
    return _kept()[1]


def _kept():
    """(``kept_ids``, the file was read whole) — cached by the inode, mtime and size of subs.json; a failed reading is
    not cached (REV-3371-pre П-3: the rights mended without a write — read whole at the next call)."""
    p = os.path.join(paths.egress_dir(), "subs.json")
    try:
        st = os.stat(p)
    except OSError:
        return {}, not os.path.lexists(p)
    key = (p, st.st_ino, st.st_mtime_ns, st.st_size)
    hit = _IDS_CACHE.get("ids")
    if hit and hit[0] == key:
        return hit[1], hit[2]
    text = util.read_text(p)
    ok = text is not None
    try:
        doc = json.loads(text or "{}")
    except ValueError:
        doc, ok = {}, False
    if not isinstance(doc, dict):
        doc, ok = {}, False
    raw = doc.get("ids")
    if raw is not None and not isinstance(raw, dict):
        ok = False
    out = dict((k, v) for k, v in (raw if isinstance(raw, dict) else {}).items()
               if _ID_RX.match(str(k)) and _ID_RX.match(str(v)))
    if ok:
        _IDS_CACHE["ids"] = (key, out, ok)
    else:
        _IDS_CACHE.pop("ids", None)
    return out, ok


_ID_RX = re.compile(r"^e-[0-9a-f]{10}$")


def pool_id(line, ids=None):
    """The id of the exit of a pool line: ``exit_id``, or the one a subscription kept for it (``kept_ids``)."""
    n = exit_id(line)
    return (kept_ids() if ids is None else ids).get(n, n)


def peer_key(line, parser=None):
    """Who the key IS — the one notion of «the same key» (3.3.5 №4: the check of repeats, ``identity`` of a renamed key of
    a subscription, the marks of ``pool`` are all this): an AmneziaWG key — «awg|<public key of the server>|<public key
    of the client>» (the client's, from its private key): the same client of the same server, at any address (by IP and
    by domain — one client: two legs with it lose 25–100 %, T-335 №4) and in any form (``vpn://`` and ``.conf`` — T-X6);
    two clients of one server — two keys. A VLESS key — «vless|<uuid>|<host:port>»: one account on one server, whatever
    the parameters and the name after «#» (T-M6-qs330 №6). Hysteria2, Shadowsocks, Trojan, VMess — their credentials
    at host:port (``new_peer``, stage 1). Any other line — its exit id (only the same text is the same). The same in
    egress_parse.py ``peer_key`` of install.sh (``peers``). ``parser`` may be None (base64 of the record)."""
    line = str(line or "").strip()
    if line.startswith("vless://"):
        k = vless_peer(line)
        if k:
            return k
    if link_proto(line) not in ("", "vless"):
        k = new_peer(line)
        if k:
            return k
    if line.startswith("awg://"):
        try:
            rec = _awg_record(parser, line[6:])
            pub = "".join(str((rec.get("peer") or {}).get("public_key") or "").split())
            me = client_key(rec.get("private_key"))
            if pub and me:
                return "awg|%s|%s" % (pub, me)
        except Exception:                           # noqa: BLE001 — a broken record is told by its text alone
            pass
    return exit_id(line)


_CLIENT_KEYS = {}


def client_key(priv):
    """The public key of the client (base64) from its private key — X25519, as ``wg pubkey``; a private key that is not
    32 bytes of base64 — «k:<sha1 of it, 16 hex>» (the same text is the same client); "" — none. The same in
    egress_parse.py ``client_key``."""
    priv = "".join(str(priv or "").split())
    if not priv:
        return ""
    hit = _CLIENT_KEYS.get(priv)
    if hit is None:
        from ..vendor import awgkeys
        try:
            hit = awgkeys.pubkey(priv)
        except Exception:                           # noqa: BLE001 — awgkeys.AwgError: not a key of 32 bytes
            hit = "k:" + hashlib.sha1(priv.encode("utf-8", "surrogateescape")).hexdigest()[:16]
        if len(_CLIENT_KEYS) > 4096:
            _CLIENT_KEYS.clear()
        _CLIENT_KEYS[priv] = hit
    return hit


def key_address(line, parser=None):
    """«host:port» the key connects to (lower case), "" when it does not parse — only to tell «at another address» of
    one key (``same_how``), never who the key is."""
    line = str(line or "").strip()
    if line.startswith("vless://"):
        k = vless_peer(line)
        return k.rsplit("|", 1)[-1] if k else ""
    if link_proto(line):
        k = new_peer(line)
        return k.rsplit("|", 1)[-1] if k else ""
    if line.startswith("awg://"):
        try:
            return str((_awg_record(parser, line[6:]).get("peer") or {}).get("endpoint") or "").strip().lower()
        except Exception:                           # noqa: BLE001
            return ""
    return ""


def _ip_host(addr):
    """True — «host:port» names its host by an IP (v4, or v6 in brackets), not by a domain."""
    host = addr.rsplit(":", 1)[0].strip("[]")
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


SAME_AT = ("ip_domain", "address")             # ``same_how`` of the same key at two addresses


def same_how(line, other, parser=None):
    """How one key (``peer_key``) is written twice: "ip_domain" — at two addresses, one by IP and one by domain;
    "address" — at two other addresses (two IPs, two domains); "text" — at one address, written differently
    (``vpn://`` and ``.conf``, another name or parameters)."""
    a, b = key_address(line, parser), key_address(other, parser)
    if not (a and b and a != b):
        return "text"
    return "ip_domain" if _ip_host(a) != _ip_host(b) else "address"


def same_key_text(eid, how, name=""):
    """«этот ключ уже есть как e-… («Вена»), по другому адресу» — why a key is not added again (RU+EN). At two IPs or
    two domains (not by IP and by domain — that is one server) — with the hint of a server restored from a copy of
    this one: it has the same keys (REV-335-2, a known limit)."""
    who = eid + (" (%s)" % quoted(name) if name and name != eid else "")
    return tr("gw_exits.same_key", id=who, how=tr("gw_exits.same_how." + ("address" if how in SAME_AT else "text"))) \
        + (tr("gw_exits.same_key_restored") if how == "address" else "")


def vless_peer(line):
    """«vless|<uuid>|<host:port>» of a vless:// link (the same in egress_parse.py peer_key of install.sh), "" when it
    does not parse. The host in lower case, an IPv6 one in brackets, the port 443 when it is not given."""
    try:
        u = urllib.parse.urlsplit(str(line or "").strip())
        uid = urllib.parse.unquote(u.username or "").strip().lower()
        host = (u.hostname or "").strip().lower()
        port = _netloc_port(u.netloc)                  # not u.port: the same on 3.9 and 3.12+ (REV-s1-hotfix-merge П-1)
    except ValueError:
        return ""
    if not uid or not host or not port:
        return ""
    return "vless|%s|%s:%d" % (uid, "[%s]" % host if ":" in host else host, port)


# ── who a key of the other xray schemes is (stage 1, B9; SPEC-stage1-exits §3.8) — stdlib, no parser: exitsubs.plan of
# 3.3.5 calls peer_key/identity without it, and a renamed key (another name after «#») must stay the same exit. The same
# strings as egress_parse.py ``_peer_new`` (install.sh; tests/gw/test_s1_idcarry.py compares them on every scheme).

def _b64any(s):
    s = "".join(str(s or "").split()).replace("-", "+").replace("_", "/")
    return base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8")


def _netloc_port(netloc, default=443):
    """The port of «user@host:port» (IPv6 in brackets) as the parser's ``_netloc_port`` takes it: ASCII digits 1..65535,
    spaces around stripped, none — ``default``; None — not a port (never ``urlsplit().port``: 3.9 and 3.12+ differ)."""
    hp = str(netloc or "").rpartition("@")[2]
    if hp.startswith("["):
        tail = hp.partition("]")[2]
        if tail and not tail.startswith(":"):
            return None
        ptext = tail[1:]
    else:
        ptext = hp.partition(":")[2]
    if ptext == "":                    # none — the default; a port of spaces alone is no port (as ``_port1``, REV-s1-b4 П-4)
        return default
    ptext = ptext.strip()
    if not re.match(r"^[0-9]{1,5}\Z", ptext):
        return None
    n = int(ptext)
    return n if 0 < n < 65536 else None


def _hp(host, port):
    host = str(host or "").strip().lower().strip("[]")
    return "%s:%d" % ("[%s]" % host if ":" in host else host, port)


def _link_parts(link):
    """(userinfo, host, port text) of «scheme://userinfo@host:port/?query#name» by hand (a port range of Hysteria2
    breaks urllib; base64 of Shadowsocks may hold «/»); None — no host."""
    rest = str(link or "").strip().split("://", 1)[1]
    rest = rest.partition("#")[0].partition("?")[0]
    user, at, hp = rest.rpartition("@")
    if not at:
        user, hp = "", rest
    hp = hp.split("/", 1)[0]
    if hp.startswith("["):
        host, _, p = hp[1:].partition("]")
        p = p[1:] if p.startswith(":") else p
    elif hp.count(":") == 1:
        host, _, p = hp.partition(":")
    else:
        host, p = hp, ""
    host = _host(host)
    return (user, host, p.strip()) if host else None


_HOST = re.compile(r"^[a-z0-9_](?:[a-z0-9_.-]{0,251}[a-z0-9_.])?$")


def _host(h):
    """The host of a key as the parser takes it (_host there): an IP address, or a domain in ASCII (IDN — in punycode);
    None — anything else (spaces, «$(…)», quotes, an IPv6 zone «fe80::1%eth0»: 3.3.5.1). An IP address — as ``str`` of
    ip_address writes it («2001:0DB8::1» → «2001:db8::1»), as the parser does. One dot at the end (after IDNA: «。»,
    «．» too) is dropped, as the parser 3.3.6 does: «srv.example.com.» is the same key as «srv.example.com» for the
    menu, the panel and the core alike (one leg, the same id; REV-s1-rel-3 З-1); an empty label («a..b», «.») — None."""
    h = str(h or "").strip().lower()
    if "%" in h:                                    # an IPv6 zone: ip_address takes any tail after «%» (3.9+)
        return None
    try:
        return str(ipaddress.ip_address(h))
    except ValueError:
        pass
    try:
        h = h.encode("idna").decode("ascii")        # IDNA: «。» «．» «｡» are dots, an empty label inside — an error
    except (UnicodeError, ValueError):
        return None
    if h.endswith("."):                             # «127.0.0.1.» — IPv4; IPv6 has no such form («::1.» — None)
        h = h[:-1]
        try:
            return str(ipaddress.IPv4Address(h))
        except ValueError:
            pass
    return h if _HOST.match(h) and not h.endswith(".") and ".." not in h else None


_HOP = re.compile(r"^[0-9]{1,5}(-[0-9]{1,5})?(,[0-9]{1,5}(-[0-9]{1,5})?)*\Z")   # ASCII digits only («４４３» — not a port, П-2)


def _first_port(p):
    if not p:
        return 443
    m = re.match(r"^([0-9]{1,5})(?:\Z|[-,])", p)              # «443abc» — not a port (as the parser, П1)
    n = int(m.group(1)) if m else 0
    return n if 0 < n < 65536 else None


def _new_parts(line):
    """(protocol, credentials, host, port) of a hy2/ss/trojan/vmess key, None — it does not parse."""
    proto = link_proto(line)
    try:
        if proto == "vmess":
            j = json.loads(_b64any(str(line).strip().split("://", 1)[1].split("#", 1)[0]))
            host, uid = _host(j.get("add")) or "", str(j.get("id") or "").strip().lower()
            port = int(str(j.get("port") or "443").strip())
            return ("vmess", uid, host, port) if host and uid and 0 < port < 65536 else None
        if proto == "shadowsocks":
            body = str(line).strip().split("://", 1)[1].partition("#")[0]
            if "@" not in body.partition("?")[0]:
                body = _b64any(body.partition("?")[0].rstrip("/"))
            r = _link_parts("ss://" + body)
            if not r:
                return None
            user, host, p = r
            user = urllib.parse.unquote(user)
            if ":" not in user:
                user = _b64any(user)
            method, _, pw = user.partition(":")
            port = _first_port(p) if p else None
            if not method or not pw or not port or "-" in p or "," in p:
                return None
            return ("ss", "%s:%s" % (method.strip().lower(), pw), host, port)
        if proto in ("hysteria2", "trojan"):
            r = _link_parts(line)
            if not r:
                return None
            user, host, p = r
            cred, port = urllib.parse.unquote(user), _first_port(p)
            if proto == "trojan" and ("-" in p or "," in p):
                return None
            if proto == "hysteria2":                # a range of ports to hop — or «mport» — as the parser checks it
                q = urllib.parse.parse_qs(str(line).strip().split("://", 1)[1].partition("#")[0].partition("?")[2])
                hop = (q.get("mport") or [""])[0] or (p if ("-" in p or "," in p) else "")
                if hop and not _HOP.match(hop):
                    return None
            return ("hy2" if proto == "hysteria2" else "trojan", cred, host, port) if cred and port else None
    except (ValueError, TypeError, UnicodeError, AttributeError, IndexError):
        return None
    return None


def new_peer(line):
    """«hy2|<auth>|<host:port>», «ss|<method>:<password>|<host:port>», «trojan|<password>|<host:port>»,
    «vmess|<id>|<host:port>» — who a Hysteria2, Shadowsocks, Trojan or VMess key is (§3.8): the host in lower case, IPv6
    in brackets, the port 443 when none (the first one of a range); no name after «#», no parameters (sni, obfs, fp,
    alpn). SIP002 of Shadowsocks (base64 or plain «method:password») and its old «all in base64» are one key. ""
    when it does not parse."""
    r = _new_parts(line)
    return "%s|%s|%s" % (r[0], r[1], _hp(r[2], r[3])) if r else ""


def identity(line, parser=None):
    """Who the key IS (3.3.5) — ``peer_key``, the same notion (no second key): a subscription that gives the same key
    under another line (another name after «#» — «10GB left» → «9GB left» —, other parameters, another address of the
    same AmneziaWG server and client) gives the same exit: its routes, name and switch stay (``exitsubs.plan``)."""
    return peer_key(line, parser)


def identity_hash(line, parser=None):
    """``identity`` as 16 hex (what subs.json keeps: never a uuid or a key in clear)."""
    return hashlib.sha1(identity(line, parser).encode("utf-8", "surrogateescape")).hexdigest()[:16]


def place_hash(line, parser=None):
    """``identity`` and the address together as 16 hex — one copy of a key the pool may hold twice (by IP and by domain,
    a pool of 3.3.4): what a subscription excludes when a person removed one copy and the other stays (REV-335-2)."""
    text = "%s|%s" % (identity(line, parser), key_address(line, parser))
    return hashlib.sha1(text.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def same_peers(lines, parser=None):
    """[[index, …]] (1-based lines of the pool) of keys that lead to one server — groups of two and more."""
    groups = collections.OrderedDict()
    for idx, line in enumerate(lines, 1):
        groups.setdefault(peer_key(line, parser), []).append(idx)
    return [g for g in groups.values() if len(g) > 1]


# ── the parser of the egress ───────────────────────────────────────────────

def _script(runner):
    for p in (paths.script_copy(), paths.command()):
        if os.path.isfile(p):
            return p
    return paths.script_copy()


def load_parser(runner=None):
    """egress_parse.py as a module (cached by mtime/size). A missing file -> ``gokaskad
    --egress-ensure-parser`` once (with a runner). Raises KaskadError if it cannot be loaded.
    3.3.4 (stand T-334, 54b3d65): a file older than PARSER_MIN (a node updated from 3.3.3 where nothing rewrote it) —
    the same, once per file (with a runner): gw apply built the exits of the pool with the parser of 3.3.3, and a key
    with pcs went to xray without its pinned certificate. A file that stays old after that is used as it is.
    Stage 1 (SPEC-stage1-exits §9.2, З1): a file without a name of REQUIRED (``gw_exits.parser.old`` — a parser of
    3.3.4/3.3.5 on the disk has no ``xray_outbound``/``link_proto``) is rewritten the same way, once per file (its path,
    mtime and size), and loaded again: the first call of the collector, ``gw apply`` or a subscription brings the new
    parser without ``k3_post_update``. ``parser.old`` goes out only when the names are missing after that too, or
    without a runner (the readers of the panel then fall back to ``pool_fallback``)."""
    try:
        mod = _load_parser(runner)
    except KaskadError as e:
        if runner is None or not _is_old(e) or not _refresh(runner):
            raise
        return _load_parser(runner)
    if runner is not None and getattr(mod, "PARSER_VERSION", 0) < PARSER_MIN and _refresh(runner):
        mod = _load_parser(runner)
    return mod


def _is_old(err):
    """The KaskadError of ``_load_parser`` is «names of REQUIRED are missing» (``gw_exits.parser.old``)."""
    return getattr(err, "data", None) == {"parser": "old"}


def _refresh(runner):
    """``gokaskad --egress-ensure-parser`` once per file on the disk (its path, mtime and size: ``_refreshed``) —
    True when it was called now."""
    path = paths.egress_parser()
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return False
    if key in _refreshed:
        return False
    _refreshed.add(key)
    runner.run([_script(runner), "--egress-ensure-parser"], timeout=60)
    return True


def _load_parser(runner=None):
    path = paths.egress_parser()
    if not os.path.isfile(path) and runner is not None:
        runner.run([_script(runner), "--egress-ensure-parser"], timeout=60)
    try:
        st = os.stat(path)
    except OSError:
        raise KaskadError(tr("gw_exits.parser.missing", path=path))
    key = (path, st.st_mtime_ns, st.st_size)
    mod = _cache.get(key)
    if mod is None:
        spec = importlib.util.spec_from_file_location("kaskad_egress_parse", path)
        mod = importlib.util.module_from_spec(spec)
        try:
            with _quiet():
                spec.loader.exec_module(mod)
        except Exception as e:                      # noqa: BLE001 — any failure of a foreign file
            raise KaskadError(tr("gw_exits.parser.broken", path=path, err=type(e).__name__))
        missing = [n for n in REQUIRED if not callable(getattr(mod, n, None))]
        if missing:
            raise KaskadError(tr("gw_exits.parser.old", path=path, names=", ".join(missing)), data={"parser": "old"})
        _cache.clear()
        _cache[key] = mod
    return mod


def pool_path(role="abroad"):
    """The file of the keys of a role: upstream.txt (abroad) or upstream-ru.txt (ru, T15; SPEC-groups §2.2)."""
    return paths.egress_up_ru() if role == "ru" else paths.egress_up()


def index_base(role="abroad"):
    """The number of line 0 of a file: 0 for upstream.txt, RU_INDEX_BASE for upstream-ru.txt (exit 201 = its line 1)."""
    return RU_INDEX_BASE if role == "ru" else 0


def pool_lines(role="abroad"):
    """Non-blank lines of the file of ``role`` (upstream.txt by default) — numbered exactly as the egress numbers them
    (awglist, config); a line of upstream-ru.txt is number ``RU_INDEX_BASE`` + its place."""
    raw = util.read_text(pool_path(role))
    return [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]


def _max_index(role, top):
    """The last number of a line of ``role`` that may carry a leg or a mark: a foreign line past RU_INDEX_BASE would
    take the marks of a Russian one (T15)."""
    return min(top, RU_INDEX_BASE) if role == "abroad" else top


def role_of_index(n):
    """"ru" for a number of upstream-ru.txt (201…), else "abroad"."""
    return "ru" if int(n) > RU_INDEX_BASE else "abroad"


AWG_KEEPALIVE_IDLE = 600            # s: TCP keepalive of the AmneziaWG outbounds of the gateway's xray (Н3) — past connIdle


def quiet_keepalive(ob):
    """The AmneziaWG outbound (``freedom`` with the mark of its leg) of the GATEWAY's xray without TCP keepalives of its
    own (Н3, T-KT1): xray 26 sends one every 45 s on every idle socket by default, and its observatory leaves the
    connection of each probe open (no DisableKeepAlives) until ``connIdle`` (300 s) — ≈ 30 idle connections per exit,
    ≈ 1.3 packets a second through the tunnel for nothing. An idle socket lives ``connIdle`` at most anyway (xray closes
    it), so a keepalive after AWG_KEEPALIVE_IDLE s never fires. The parser's own outbound (the bot's proxy) is not
    changed — a copy. Not a dict — as it is."""
    if not isinstance(ob, dict):
        return ob
    ob = copy.deepcopy(ob)
    ss = ob.setdefault("streamSettings", {})
    so = ss.setdefault("sockopt", {}) if isinstance(ss, dict) else None
    if isinstance(so, dict):
        so.setdefault("tcpKeepAliveIdle", AWG_KEEPALIVE_IDLE)
    return ob


def _awg_record(parser, payload):
    if callable(getattr(parser, "_b64d", None)):
        text = parser._b64d(payload)
    else:
        import base64
        text = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8", "replace")
    return json.loads(text)


def pool(runner=None, parser=None, roles=("abroad",)):
    """Exits of the pool: [{"id", "index", "tag", "kind": "awg"|"xray"|"bad", "proto", "role", "mark", "iface",
    "table", "version", "name", "host", "outbound", "line", "problem"?}]. ``line`` and ``outbound`` hold secrets —
    never print them (``public()``). An empty or missing pool needs no parser. ``roles`` — the exits of these roles
    only: by default the foreign ones ("abroad"), so a reader that does not ask never sees a Russian exit (T15; the
    places that need both ask ``roles=ROLES``, SPEC-stage1-exits §3.1). ``proto`` — the protocol of an xray exit."""
    files = [(r, pool_lines(r)) for r in ROLES if r in roles]
    if not any(lines for _r, lines in files):
        return []
    parser = parser or load_parser(runner)
    out = []
    seen = set()
    ids = kept_ids()
    for role, idx, line in ((r, index_base(r) + n, ln) for r, lines in files for n, ln in enumerate(lines, 1)):
        eid = pool_id(line, ids)
        e = {"id": eid, "index": idx, "tag": tag(eid, role), "kind": "bad", "proto": "", "role": role, "mark": None,
             "iface": None, "table": None, "version": "", "name": "", "host": "", "outbound": None, "line": line}
        if eid in seen:                    # the same key twice: one exit (xray refuses a repeated tag)
            e["id"] = "%s-%d" % (eid, idx)
            e["tag"] = tag(e["id"], e["role"])
            _problem(e, "dup")
            e["dup_of"] = eid
            out.append(e)
            continue
        seen.add(eid)
        with _quiet():
            try:
                lab = parser.pool_label(line) or {}
                e["name"] = term_text(lab.get("name"))
                e["host"] = term_text(lab.get("host"))
            except Exception:                       # noqa: BLE001
                pass
            try:
                if line.startswith("awg://"):
                    rec = _awg_record(parser, line[6:])
                    e["version"] = parser.awg_version(rec)
                    e["wg"] = plain_wg(rec)                     # a plain WireGuard key: «WireGuard», not «AmneziaWG 1.0»
                    lost = getattr(parser, "awg_lost3x", None)
                    why = _awg_problem(parser, rec)
                    if role == "ru" and idx > RU_INDEX_BASE + RU_MAX:
                        _problem(e, "ru_too_far", max=RU_MAX)
                    elif idx > _max_index(role, MAX_AWG_INDEX):
                        _problem(e, "too_far", max=_max_index(role, MAX_AWG_INDEX))
                    elif why:                                   # 3.3.5.1: no usable Endpoint/Address — no leg
                        _problem(e, AWG_PROBLEMS.get(why, "unparsed"), **_gw_net_args(parser, rec, why))
                    elif callable(lost) and lost(rec):
                        _problem(e, "awg3_lost")
                    else:
                        e.update(kind="awg", mark=AWG_MARK_BASE + idx, iface="kawg%d" % idx,
                                 table=AWG_MARK_BASE + idx, outbound=quiet_keepalive(parser.awg_outbound(rec, e["tag"], idx)),
                                 s4=_s4(rec), loss226=_loss226(rec))
                elif link_proto(line):                  # an xray key: vless://, hy2://, ss://, trojan://, vmess://
                    proto = link_proto(line)
                    ob = parser.xray_outbound(line, e["tag"], OUTBOUND_MSS)
                    if role == "ru" and idx > RU_INDEX_BASE + RU_MAX:
                        _problem(e, "ru_too_far", max=RU_MAX)
                    elif idx > _max_index(role, MAX_VLESS_INDEX):
                        _problem(e, "too_far", max=_max_index(role, MAX_VLESS_INDEX))
                    elif ob:
                        e.update(kind=XRAY, proto=proto, mark=VLESS_MARK_BASE + idx, outbound=ob)
                    else:
                        _key_problem(e, parser, line, "unparsed")
                else:
                    _problem(e, "unknown_format")
            except Exception:                       # noqa: BLE001
                _problem(e, "unparsed")
        out.append(e)
    set_where(out)
    by_peer = {}
    for e in out:       # the same key by another line (vpn:// and .conf of one peer, by IP and by domain): marked — a pool
        k = peer_key(e["line"], parser)     # of 3.3.4 may have both legs of one client: told, never removed (3.3.5 №4)
        if k in by_peer and not e.get("dup_of"):
            e["same_peer"] = by_peer[k][0]
            e["same_how"] = same_how(e["line"], by_peer[k][1], parser)
            if by_peer[k][2]["role"] == e["role"]:      # T15: a twin only in its own file — the routes of a foreign
                e["twin"] = by_peer[k][0]               # copy never go to a Russian one (the same key in both files is
                by_peer[k][2].setdefault("twin", e["id"])   # told, ``same_peer_problems``); the first copy: its
        by_peer.setdefault(k, (e["id"], e["line"], e))  # routes go here when it goes (REV-335-2)
    first = dict((e["id"], e) for e in out if not e.get("dup_of"))
    for e in out:                           # a repeated key is named as the key above it, not «e-41a19b9cbe-6»
        src = first.get(e.get("dup_of"))
        if src:
            e.update((k, src.get(k)) for k in ("name", "host", "version", "where", "wg"))
    if "ru" in roles:
        ru_ports(out)
    return [e for e in out if e["role"] in roles]


def heal(runner, xray):
    """REV-s1-t11-2 С2-2: the xray of the gateway refused its config — the lines of the pool asked of it (the parser's
    xray_heal: halves, a line with the parameters 3.3.5 did not pass — again in the form of 3.3.5, refused in that form
    too — out of every config, told as «xray_refused»). -> its result ({"refused", "new", "legacy", "unchecked"}) or
    None (a parser without it, no answer). Each change goes to the log. The lines of BOTH files (T15 Q4, SEC-s1-q4
    З-3: a Russian line xray refuses no longer poisons the config; the forms of its lines are not dropped either); a
    line of upstream-ru.txt is numbered as the pool numbers it — 200 + its place."""
    try:
        parser = load_parser(runner)
    except KaskadError:
        return None
    fn = getattr(parser, "xray_heal", None)
    if not callable(fn):
        return None

    def run(cfg):
        rc = runner.run([xray, "-test", "-c", cfg], timeout=30).rc
        return None if rc in (124, 127) else rc == 0      # 124 — no answer in time (sysrun), 127 — did not run

    ab = pool_lines("abroad")
    lines = ab + pool_lines("ru")                       # SEC-s1-q4 З-3: the same xray takes both files
    with _quiet():
        r = fn(lines, xray, 30, run)
    if not isinstance(r, dict):
        return None
    num = lambda n: n if not isinstance(n, int) or n <= len(ab) else RU_INDEX_BASE + n - len(ab)   # noqa: E731
    r = dict((k, [num(n) for n in v] if isinstance(v, list) else v) for k, v in r.items())
    for key, which in (("gw_exits.log.xray_refused", "new"), ("gw_exits.log.xray_legacy", "legacy"),
                       ("gw_exits.log.xray_ext", "ext")):
        for n in r.get(which) or ():
            util.log_tr(key, n=n)
    return r


def line_ids(lines=None, role="abroad", taken=()):
    """[(id, index)] of the lines of the pool — the ids of ``pool`` (a repeated key: ``e-<hex>-<line>``) without the
    parser. The panel removes a key by them (``ops.exit_del`` -> ``gokaskad --egress-del <line> <hex>``). ``role`` —
    the file (``lines`` None) and the numbers: a line of upstream-ru.txt is ``RU_INDEX_BASE`` + its place (T15).
    ``taken`` — the ids the lines of upstream.txt hold already (``all_line_ids``): the same key in both files is one
    exit, its Russian copy «e-…-20N», as ``pool`` names it."""
    out, seen = [], set(taken)
    ids = kept_ids()
    for idx, line in enumerate(pool_lines(role) if lines is None else lines, index_base(role) + 1):
        eid = pool_id(line, ids)
        out.append(("%s-%d" % (eid, idx) if eid in seen else eid, idx))
        seen.add(eid)
    return out


def all_line_ids():
    """[(id, index, role)] of the lines of BOTH files, the ids exactly as ``pool(roles=ROLES)`` gives them (no parser)."""
    ab = pool_lines("abroad")
    ids = kept_ids()
    out = [(i, n, "abroad") for i, n in line_ids(ab, "abroad")]
    taken = set(pool_id(l, ids) for l in ab)
    return out + [(i, n, "ru") for i, n in line_ids(pool_lines("ru"), "ru", taken)]


def twin_of(lines, n, parser=None):
    """(id of line ``n`` (1-based) of the pool, id of the copy that stays) — the first other line with the same key
    (``peer_key``: by IP and by domain, ``vpn://`` and ``.conf``; a pool of 3.3.4) — or (id, None): no other copy. The
    routes of the copy that goes go to the one that stays (``ops.hand_over``, REV-335-2). Ids as ``line_ids``; a
    repeated text («e-…-6») is no copy of its own (routes name the key above it)."""
    rows = line_ids(lines)
    if not 1 <= n <= len(rows):
        return None, None
    me = rows[n - 1][0]
    if not _ID_RX.match(me):
        return me, None
    k = peer_key(lines[n - 1], parser)
    for (eid, idx), line in zip(rows, lines):
        if idx != n and eid != me and _ID_RX.match(eid) and peer_key(line, parser) == k:
            return me, eid
    return me, None


def _is_vless(e):
    """A VLESS exit (a row of ``pool`` or of ``public``): one account of a VLESS server (REV-335-3 №3)."""
    if is_xray(e):
        return proto_of(e) == "vless"
    return str(e.get("line") or "").startswith("vless://")


def same_peer_problems(st, pool_exits):
    """[detail] — one per key the pool holds by more than one line (``pool`` rows): «Ключ «Аня IP» добавлен дважды (и
    как «Аня домен»): по IP и по домену. Работать будет только один — …». For «Проверка сервера» (summary), ``gw
    exits`` and ``gw check``; the panel says the same by ``same_how`` (ex.samePeer)."""
    groups = collections.OrderedDict()
    for e in pool_exits or []:
        groups.setdefault(e.get("same_peer") or e.get("dup_of") or e["id"], []).append(e)
    out = []
    for g in groups.values():
        if len(g) > 1:
            names = []
            for e in g:
                n = quoted(label(st, e))
                if n not in names:
                    names.append(n)
            how = "repeat" if g[1].get("dup_of") else (g[1].get("same_how") or "text")
            also = tr("gw_exits.same_peer_also", names=", ".join(names[1:])) if len(names) > 1 else ""
            # «работать будет только один» — AmneziaWG (one private key, one connection) and a repeated line (xray
            # refuses a repeated tag); two lines of one VLESS account both work (REV-335-3 №3)
            key = "gw_exits.same_peer_vless" if how != "repeat" and _is_vless(g[0]) else "gw_exits.same_peer"
            out.append(tr(key if len(g) == 2 else key + "_n", name=names[0], n=len(g), also=also,
                          how=tr("gw_exits.twice." + how)))
    return out


# ── what a pasted text or a file holds, before it is written (the panel's preview) ──
# The SAME parser as the SSH menu and ``gokaskad --egress-add`` (egress_parse.py: extract, pool_label, awg_version), in
# this process: nothing is written, nothing runs (but ``--egress-ensure-parser`` once on a node that has no parser yet).
# A plain WireGuard .conf (no AmneziaWG parameters) is stored as an AmneziaWG record of line 1.0 with empty parameters:
# the leg (amneziawg-go or the kernel module) without junk, padding and magic headers speaks plain WireGuard — so it is
# shown as «WireGuard».

# SUBSCRIPTION_TEXT, NO_KEYS_TEXT, NO_AWG_IN_VPN (and the other words below) — read through the module's __getattr__:
# a tr() in the language of the moment
_WORDS = {"SUBSCRIPTION_TEXT": "gw_exits.preview.subscription", "NO_KEYS_TEXT": "gw_exits.preview.no_keys",
          "NO_AWG_IN_VPN": "gw_exits.preview.no_awg_in_vpn", "ADD_PANEL": "gw_exits.add.panel",
          "NO_EXITS_OFFLINE": "gw_exits.offline", "NOT_CHECKED": "gw_exits.detail.not_checked",
          "GW_OFF_TEXT": "gw_exits.state.gw_off", "NO_DATA": "gw_exits.state.no_data"}


def __getattr__(name):
    if name in _WORDS:
        return tr(_WORDS[name])
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


_SUBSCRIPTION = re.compile(r"(?im)^https?://")       # egress_collect_links: such a line — the input is a subscription
_VPN = re.compile(r"vpn://[A-Za-z0-9_\-+/=]+")         # the tokens extract() looks for


_WS_RUN = re.compile(r"\s{16,}")


def squeeze(text):
    """Long runs of blanks (16 and more) -> one line break (a run with a break in it) or one space. The parser splits a
    .conf with lookaheads over blanks: on 64 KB of spaces that is minutes of the CPU (and of the GIL of the panel).
    No key holds such a run, so the keys stay the same."""
    return _WS_RUN.sub(lambda m: "\n" if ("\n" in m.group(0) or "\r" in m.group(0)) else " ", str(text or ""))


def is_subscription(text):
    return bool(_SUBSCRIPTION.search(str(text or "")))


def plain_wg(rec):
    """An AmneziaWG record without a single AmneziaWG parameter: a plain WireGuard key."""
    return not any(str(v or "").strip() for v in ((rec or {}).get("params") or {}).values())


LINK_PROBLEMS = VLESS_PROBLEMS + ("no_tls", "weak_cipher", "bad_link", "unsupported_scheme", "dup_endpoint",
                                  "xray_refused")      # REV-s1-t11-2: the real xray refused the line (egress_parse)


def _link_problem(parser, line):
    """Why the parser refuses one key being added (``link_problem`` of egress_parse ≥ 20), "" — nothing it knows."""
    fn = getattr(parser, "link_problem", None)
    try:
        code = fn(line) if callable(fn) else ""
    except Exception:                                   # noqa: BLE001 — a foreign file
        code = ""
    return code if code in LINK_PROBLEMS else ""


def _problem_code(proto, code, wait=True):
    """(code, args) of a problem of an xray key: «problem.vless_<code>» of VLESS (the words of 3.3.4), else
    «problem.key_<code>» with the name of the protocol (``proto``). ``wait`` False — tofu_wait is insecure (a key being
    added: only a key of the pool is tried again)."""
    if code == "tofu_wait" and not wait:
        code = "insecure"
    if proto in ("", OLD_XRAY) and code in VLESS_PROBLEMS + ("unparsed", "unsupported"):
        return "problem.vless_" + code, {}
    if proto == "hysteria2" and code == "insecure":     # QUIC: no TOFU — «add it again to pin» is no advice (REV З6)
        return "problem.key_insecure_quic", {"proto": proto_title(proto)}
    return "problem.key_" + (code if code in LINK_PROBLEMS + ("unparsed", "unsupported") else "unparsed"), \
        {"proto": proto_title(proto or OLD_XRAY)}


def _key_problem(e, parser, line, default):
    """The problem of an xray key of the pool the parser did not build: VLESS — ``vless_problem`` (a VLESS without TLS
    keeps working: the pool is not the place to refuse it), the others — ``link_problem``."""
    proto = link_proto(line)
    fn = getattr(parser, "pool_problem", None) or getattr(parser, "link_problem", None)
    if proto == OLD_XRAY:
        try:
            refused = callable(fn) and fn(line) == "xray_refused"        # REV-s1-t11-2: in its own words, not 3.3.4's
        except Exception:                               # noqa: BLE001
            refused = False
        if not refused:
            _problem(e, "vless_" + _vless_problem(parser, line, default))
            return
    try:
        code = fn(line) if callable(fn) else ""
    except Exception:                                   # noqa: BLE001
        code = ""
    key, args = _problem_code(proto, code if code in LINK_PROBLEMS else default)
    _problem(e, key[len("problem."):], **args)


def _vless_problem(parser, line, default, wait=True):
    """Why the parser refuses a vless:// line (``vless_problem`` of egress_parse ≥ 15: insecure | bad_pin | tofu_wait),
    else ``default``. ``wait`` False — a key being added: tofu_wait is said as insecure (only a key of the pool is tried
    again)."""
    fn = getattr(parser, "vless_problem", None)
    try:
        code = fn(line) if callable(fn) else ""
    except Exception:                                   # noqa: BLE001 — a foreign file
        code = ""
    if code == "tofu_wait" and not wait:
        code = "insecure"
    if code == "bad_link":                              # 3.3.5.1: no usable address in the link — «не разобран»
        return "unparsed"
    return code if code in VLESS_PROBLEMS else default


# awg_problem (3.3.6) → the problem of the exit in its own words: the Address of the client, the Endpoint is this server,
# the Address in the network of the gateway of this node (round 3: «10.77.0.10 … 10.77.0.0/16»)
AWG_PROBLEMS = {"bad_addr": "awg_bad_addr", "self_ep": "awg_self_ep", "gw_net": "awg_gw_net"}


def _gw_net_args(parser, rec, why):
    """{"addr", "net"} of a gw_net problem (``awg_gw_net`` of egress_parse ≥ 23); {} for any other."""
    if why != "gw_net":
        return {}
    fn = getattr(parser, "awg_gw_net", None)
    try:
        got = fn(rec) if callable(fn) else None
    except Exception:                                   # noqa: BLE001 — a foreign file
        got = None
    addr, net = got if got else ("?", gw_base_text())
    return {"addr": addr, "net": net}


def gw_base_text():
    """The network of the devices of the gateway of this node (state.json gateway.net.base) for the words of gw_net;
    the default base when none is saved."""
    try:
        from .. import state as kstate
        b = model.gw(kstate.load())["net"].get("base")
        if b:
            return str(ipaddress.ip_network(str(b), strict=False))
    except Exception:                                   # noqa: BLE001 — words only
        pass
    from . import addr
    return addr.DEFAULT_BASE


def _awg_problem(parser, rec):
    """``awg_problem`` of egress_parse ≥ 20 (3.3.5.1): "bad_link" — the record has no usable Endpoint or Address;
    ≥ 21 — "bad_addr" (the Address of the client), ≥ 22 — "self_ep" (the Endpoint is this server)."""
    fn = getattr(parser, "awg_problem", None)
    try:
        return fn(rec) if callable(fn) else ""
    except Exception:                                   # noqa: BLE001 — a foreign file
        return "bad_link"


def key_title(kind, version="", wg=False, proto=""):
    """«AmneziaWG 2.0», «WireGuard», «VLESS», «Hysteria2» … — the kind of a key for people (an xray exit: its protocol)."""
    if kind == "awg":
        return "WireGuard" if wg else ("AmneziaWG %s" % version if version else "AmneziaWG")
    if kind in (XRAY, OLD_XRAY):
        return proto_title(proto or OLD_XRAY)
    return ""


def preview(parser, text, pool_ids=(), pool_peers=None, tofu_by=None):
    """[{"id", "kind", "title", "where", "name", "in_pool", "same_peer"?, "same_how"?, "same_text"?, "problem"?}] of the
    keys in ``text`` — no key, no secret (``id`` is the id the exit will have). ``pool_peers`` — {``peer_key``: (id of
    the exit, its line)} of the pool (``ops._pool_peers``): a key the pool has by another line is ``in_pool`` too —
    ``same_peer`` (the id of that exit), ``same_how`` ("address" | "text") and ``same_text`` («этот ключ уже есть как
    e-…, по другому адресу»). Raises KaskadError with the reason when there is none.
    ``tofu_by`` ("panel" | "cli" | "mcp"; stage 1, §9.1) — a key over TLS without a check of the certificate and without
    a fingerprint gets ``tofu`` (gw/tofu.check: «verified» is written at once, «ask» carries the fingerprint for the
    consent «1) Закрепить 2) Отказаться»); it goes to the network — only the add of a key asks for it (T7/T8, T10)."""
    text = squeeze(text)
    report = getattr(parser, "extract_report", None)
    said = {}                               # 3.3.6 round 3: line -> why extract_report left it out (it resolved names)
    with _quiet():
        if callable(report):                # 3.3.4: the keys left out one by one are shown with their reason
            lines, skipped = report(text)
            said = {x["line"]: x for x in skipped or [] if isinstance(x, dict) and x.get("line")}
            lines = list(lines or []) + [x["line"] for x in skipped or []]
        else:
            lines = parser.extract(text) or []
    out = []
    ids = kept_ids()
    for line in lines:
        eid = pool_id(line, ids)
        row = {"id": eid, "kind": "bad", "title": "", "where": "", "name": "", "in_pool": eid in pool_ids}
        had = (pool_peers or {}).get(peer_key(line, parser)) if not row["in_pool"] else None
        if had:
            how = same_how(line, had[1], parser)
            row.update(in_pool=True, same_peer=had[0], same_how=how, same_text=same_key_text(had[0], how, had[2]))
        with _quiet():
            try:
                lab = parser.pool_label(line) or {}
            except Exception:                               # noqa: BLE001
                lab = {}
            host = term_text(lab.get("host"))
            try:
                if line.startswith("awg://"):
                    rec = _awg_record(parser, line[6:])
                    wg = plain_wg(rec)
                    row.update(kind="awg", title=key_title("awg", parser.awg_version(rec), wg), wg=wg)
                    # the reason of extract_report first: it alone resolved the name of the Endpoint (self_ep)
                    left = said.get(line) or {}
                    why = left.get("code") or _awg_problem(parser, rec)
                    if why:
                        args = ({"addr": left.get("addr") or "?", "net": left.get("net") or gw_base_text()}
                                if why == "gw_net" and left else _gw_net_args(parser, rec, why))
                        row["problem"] = word("problem." + AWG_PROBLEMS.get(why, "unparsed"), **args)
                        if why == "gw_net":
                            row["gw_net"] = True        # F6 Е3: ops.exit_preview adds the free network to move to
                elif link_proto(line):
                    proto = link_proto(line)
                    row.update(kind=XRAY, proto=proto, title=key_title(XRAY, proto=proto))
                    # 3.3.7.14: extract_report resolved the name of its server — it is this node (self_ep, «dns»)
                    code = _link_problem(parser, line) or ("self_ep" if (said.get(line) or {}).get("code") == "self_ep" else "")
                    if code == "insecure" and tofu_by:
                        from . import tofu
                        row["tofu"] = tofu.check(parser, line, tofu_by)
                        if (row["tofu"] or {}).get("status") == "verified":
                            code = _link_problem(parser, line)
                    if code or not parser.xray_outbound(line, "preview", OUTBOUND_MSS):
                        key, args = _problem_code(proto, code or "unsupported", wait=False)
                        row["problem"] = word(key, **args)
                elif not line.startswith("awg://"):         # tuic://, anytls://, … — the pool does not take them
                    row.update(title=str(getattr(parser, "scheme_title", lambda _l: "")(line) or ""),
                               problem=word("problem.unsupported_scheme"))
            except Exception:                               # noqa: BLE001
                row["problem"] = word("problem.unparsed")
        row["where"] = host if host != "?" else ""
        row["name"] = own_name({"name": lab.get("name"), "host": host})
        out.append(row)
    if not out:
        bad_vpn = 0
        for tok in _VPN.findall(str(text or "")):
            try:
                with _quiet():
                    parser.awg_from_vpn(tok)
            except Exception:                               # noqa: BLE001
                bad_vpn += 1
        raise KaskadError(tr("gw_exits.preview.no_awg_in_vpn" if bad_vpn else "gw_exits.preview.no_keys"), code=USAGE)
    return out


# ── an empty pool: where to add a key, and when the devices are without internet ──

def via_exits(route):
    """The route goes through the exits: the pool, a group (``group:<gid>``, stage 1) or one exit (``exit:<id>``) — not
    direct, not block (REV-s1-fix1: one path for every form of the route)."""
    via = str((route or {}).get("via") or "")
    return via == "pool" or via.startswith("exit:") or via.startswith(model.GROUP_VIA)


def default_route(st):
    return model.route_of(st or {}, {"user": None})


def menu_add_item(st):
    """The number of «Добавить ключ выхода» in gokaskad → 17 → 3 (``k3_gw_exits_menu`` of install.sh): 1 is «Маршрут
    по умолчанию», «Если выходы не работают» stands before the key only while the default route goes through the
    exits — then 3, else (direct, block) 2. The panel and the bot take the number from here: the hint must not lie."""
    return 3 if via_exits(default_route(st)) else 2


def add_hint(st):
    """Where a person adds a key: {"panel", "menu", "item", "what", "text"}. Menu 17 → 3 only on a gateway that is set
    up (``k3_gw_is_setup``: on, or an inbound installed) — else 17 opens the wizard, and the key goes in through
    «Прокси для API Telegram» (8 → 7, «2 — Добавить ключ»): the same pool."""
    st = st or {}
    if model.enabled(st) or model.installed_kinds(st):
        item = menu_add_item(st)
        menu, what = "gokaskad → 17 → 3 → %d" % item, tr("gw_exits.add.menu_gw")
    else:
        item, menu, what = None, "gokaskad → 8 → 7 → 2", tr("gw_exits.add.menu_egress")
    panel = tr("gw_exits.add.panel")
    return {"panel": panel, "menu": menu, "item": item, "what": what,
            "text": tr("gw_exits.add.text", panel=panel, menu=menu, what=what)}


def offline_empty(st, count=None):
    """The devices of the default route have no internet now: the pool is empty (``count`` — its keys, else
    ``pool_lines``), the gateway is on and its default route goes through the exits with the fail policy «block»."""
    st = st or {}
    if not model.enabled(st):
        return False
    r = default_route(st)
    n = len(pool_lines()) if count is None else count
    return bool(not n and via_exits(r) and r.get("fail") != "direct")


# ── names ──────────────────────────────────────────────────────────────────

GENERIC_NAMES = ("amneziawg", "amnezia", "awg", "awg(bad)", "?")   # the parser's names of keys that carry none


def kind_title(e):
    """«AmneziaWG 2.0», «VLESS» — the kind of an exit for people ("" when the key is not even of a known kind)."""
    line = str(e.get("line") or "")
    if e.get("kind") == "awg" or (e.get("kind") == "bad" and line.startswith("awg://")):
        return key_title("awg", e.get("version"), e.get("wg"))
    if is_xray(e):
        return key_title(XRAY, proto=proto_of(e))
    if e.get("kind") == "bad" and link_proto(line):
        return key_title(XRAY, proto=link_proto(line))
    return ""


def host_only(host):
    """«203.0.113.141» of «203.0.113.141:51820» (an IPv6 address without its brackets)."""
    h = str(host or "").strip()
    if h.startswith("["):
        return h[1:h.index("]")] if "]" in h else h
    return h.split(":", 1)[0] if h.count(":") == 1 else h


def set_where(exits):
    """Every exit of a pool gets ``where`` — «AmneziaWG 2.0 · 203.0.113.141:51820»: the name of a key that has none of
    its own. Always with the port of its server (T-X6: a leg by domain had «…:8635», a leg by IP had none — one rule for
    every row, the same as the address under the name in the panel). Returns the list."""
    for e in exits:
        host = str(e.get("host") or "")
        e["where"] = " · ".join(x for x in (kind_title(e), host) if x and x != "?")
    return exits


_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f\\]")   # control bytes (C0, DEL, C1) and «\»: a terminal would run ESC/OSC
_UNSAFE = re.compile("[\x00-\x1f\x7f-\x9f\xad\u061c\u180e\u200b\u200e\u200f\u2028-\u202e\u2060-\u2064\u2066-\u206f\ufeff\ufff9-\ufffb\ud800-\udfff]")


# 3.3.6 round 3 (REV-336-awgaddr-2 П-2, SEC-336-awgaddr-2 П-2): tags (U+E0000–E007F) only in a flag of a subdivision
# that is an emoji: England, Scotland, Wales (U+1F3F4, «gbeng» / «gbsct» / «gbwls» in tags, U+E007F) — elsewhere they
# hide text (REV-336-awgaddr-3 П-2: any other tags after U+1F3F4 — a black flag with hidden letters); egress_parse
# _TERM_TAGS, kaskad-mcp
# Variation selectors (U+FE00–FE0F, U+E0100–E01EF) are invisible and carry hidden bytes the same way (REV-336-heal-sec
# З-2): only one U+FE0E/U+FE0F right after a visible char stays (❤️, 1️⃣, 🏳️‍🌈 need it), the rest go. Not after ZWJ, ZWNJ
# or another invisible char (REV-336-heal-2 П-2: in RGI emoji U+FE0F never follows ZWJ); CGJ U+034F, the Mongolian
# selectors U+180B–180D, 180F, the Hangul fillers, U+17B4/17B5, U+1D173–1D17A go themselves.
_TAGS = re.compile("(\U0001f3f4\U000e0067\U000e0062(?:\U000e0065\U000e006e\U000e0067|\U000e0073\U000e0063\U000e0074|\U000e0077\U000e006c\U000e0073)\U000e007f)|[\U000e0000-\U000e007f\ufe00-\ufe0d\U000e0100-\U000e01ef\u034f\u115f\u1160\u17b4\u17b5\u180b-\u180d\u180f\u3164\uffa0\U0001d173-\U0001d17a]|(?<![^\x00-\x20\x7f-\xa0\ufe00-\ufe0f\U000e0000-\U000e01ef\u034f\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u2060-\u206f\u3164\ufeff\uffa0\U0001d173-\U0001d17a])[\ufe0e\ufe0f]")


def no_tags(s):
    """``s`` without the tags that are not a flag (_TAGS)."""
    return _TAGS.sub(lambda m: m.group(1) or "", s)


def term_text(s):
    """Text of a key (its name, its address — from someone else's link or a subscription) for any screen: no control
    bytes, no «\» (echo -e of the SSH menu), no tags out of a flag, spaces squeezed."""
    s = no_tags(str(s if s is not None else ""))
    # 3.3.6: and the format chars that turn a line around or hide text (U+202A–202E, U+2066–2069, U+200E/200F…) — the
    # list of egress_parse _TERM_UNSAFE; U+200D (an emoji of a family) and the chars Python does not know yet (U+1FAE0 on
    # Python 3.9) stay
    return " ".join(_UNSAFE.sub(" ", _CTRL.sub(" ", s)).split())


def own_name(e):
    """The name the key itself carries (#fragment of vless://, the description of vpn://); "" when the parser only
    put a placeholder there («amneziawg», «host:port» of a vless:// without a fragment)."""
    n = term_text(e.get("name"))                    # from someone else's link: no ESC, BEL, «\»… (SSH menu)
    h = term_text(e.get("host"))
    if not n or n.lower() in GENERIC_NAMES or n in (h, host_only(h)):
        return ""
    return n


def label(st, e):
    """The name of an exit for people: the one given in Kaskad, the key's own, else «AmneziaWG 2.0 · 203.0.113.141»."""
    return model.exit_pref(st or {}, e["id"])["name"] or own_name(e) or e.get("where") or e["id"]


# NOT_CHECKED «ещё не проверялся», GW_OFF_TEXT «включите шлюз, чтобы проверять выходы» (the collector checks the exits
# only while the gateway is on) — module __getattr__ (_WORDS)


def state_of(rec):
    """"alive" | "dead" | "unknown" of a snapshot record: no record yet, or only unknown answers so far (a new
    exit before its first real probe) — unknown, not dead."""
    if not isinstance(rec, dict):
        return "unknown"
    if rec.get("alive"):
        return "alive"
    if rec.get("new"):
        return "unknown"                   # a new exit not up yet (its leg, xray): «проверяется» (T-M6-qs330d №4)
    return "dead" if int(rec.get("fails") or 0) > 0 else "unknown"


def dies_after(e):
    """(Before 3.3.5: failed probes in a row that made the exit dead.) The least number of checks in the window before
    it can tell «упал» — the same for every kind (3.3.5); a row of ``public`` says the checks of its window."""
    return MIN_TRIES


def public(e, st=None, snap=None):
    """An exit without secrets, for people and JSON. With the snapshot: its state, and while probes fail in a row
    ``fails``, ``silent_since`` (the first failed one) and ``silent`` (seconds since)."""
    out = dict((k, e.get(k)) for k in ("id", "index", "tag", "kind", "mark", "iface", "version", "host"))
    # stage 1 (T1, SPEC-stage1-exits §7.8): an xray exit is the kind "xray" with its protocol in ``proto`` ("vless"
    # in 3.3.5 and before); ``role`` — abroad | ru
    if is_xray(e):
        out["kind"] = XRAY
    out["proto"] = proto_of(e) if is_xray(e) else ""
    out["role"] = role_of(e)
    pref = model.exit_pref(st or {}, e["id"])
    out["where"] = e.get("where") or ""
    out["name"] = label(st, e)
    out["named"] = bool(out["where"]) and out["name"] != out["where"]
    out["enabled"] = pref["enabled"]
    if e.get("problem"):
        out["problem"] = e["problem"]
        if e.get("problem_code"):
            out["problem_code"] = e["problem_code"]         # what the words say: menus and pages compare this
    if e.get("wg"):
        out["wg"] = True                                  # a plain WireGuard key (no AmneziaWG parameters)
    if e.get("same_peer"):
        out["same_peer"] = e["same_peer"]       # the same key as that exit (another line of one client: 3.3.5 №4)
        out["same_how"] = e.get("same_how") or "text"
    if e.get("twin"):
        out["twin"] = e["twin"]                 # the other copy of the same key: the routes go there when this one goes
    if snap is not None:
        rec = (snap.get("exits") or {}).get(e["id"])
        out["alive"] = bool((rec or {}).get("alive"))
        code, _ = _code_of(rec) if rec else ("detail.not_checked", {})
        out["detail"] = detail_text(rec) if rec else word("detail.not_checked")
        out["detail_code"] = code              # the words above are for people; menus and pages compare the code
        out["since"] = (rec or {}).get("since")
        out["state"] = state_of(rec)
        out["fails"] = int((rec or {}).get("fails") or 0)
        # 3.3.5: «не прошло проверок: fails из dies_after» — the checks of the window (2 min) while it fails
        checks = _int((rec or {}).get("checks"))
        # Н2 (T-KT1): never fewer checks than failed ones — a record without ``checks`` (dead: «fails» counted on) said
        # «не прошло проверок: 5 из 4»
        out["dies_after"] = max(checks or dies_after(e), out["fails"])
        fs = (rec or {}).get("fail_since") if out["fails"] else None
        out["silent_since"] = fs if isinstance(fs, (int, float)) and not isinstance(fs, bool) else None
        out["silent"] = max(0, int(_now() - fs)) if out["silent_since"] is not None else None
    return out


NO_DATA_STATE = "nodata"                # NO_DATA — the words: module __getattr__ (_WORDS)


def no_data(row):
    """A row of ``public`` (with ``usable`` and ``text``) while nobody checks the exits: the collector is silent on a
    gateway that is on (live.json older than ``live.STALE``). The snapshot is the last check, not the present — the
    state is «нет данных», not «работает» (stand T-G1b, N2). A problem of the key stays: no probe tells it."""
    if row.get("problem"):
        return row
    out = dict(row)
    out.update(state=NO_DATA_STATE, alive=None, usable=None, text=tr("gw_exits.state.no_data"), since=None, fails=0,
               silent_since=None, silent=None)
    return out


def ago_text(s):
    """«35 с», «2 мин», «1 ч 05 мин» — how long, for people."""
    s = max(0, int(s))
    if s < 100:
        return tr("gw_exits.ago.s", n=s)
    if s < 3600:
        return tr("gw_exits.ago.min", n=(s + 30) // 60)
    return tr("gw_exits.ago.h_min", h=s // 3600, m="%02d" % ((s % 3600) // 60))


def why_text(detail, silent=None):
    """«нет ответа 35 с» / «сервер выхода не отвечает (уже 2 мин)» — why an exit fails and for how long (``silent``:
    seconds since its first failed probe in a row). ``detail`` — a snapshot record (its code and arguments) or the
    words of a detail in either language (a row of ``public``, a probe answer, a record of an earlier version)."""
    rec = detail if isinstance(detail, dict) else {"detail": str(detail or "")}
    code, args = _code_of(rec)
    if not code:                                        # words that are not ours: as they are, without «(сбой 1 из 3)»
        d = _strip_fail(str(rec.get("detail") or ""))
        if silent is None:
            return d
        return tr("gw_exits.why.still", detail=d, ago=ago_text(silent)) if d else \
            tr("gw_exits.why.no_answer_for", ago=ago_text(silent))
    if silent is None:
        return detail_text(rec, fail=False)
    if code != "detail.no_answer":
        return tr("gw_exits.why.still", detail=detail_text(rec, fail=False), ago=ago_text(silent))
    text = tr("gw_exits.why.no_answer_for", ago=ago_text(silent))
    src = rec if rec.get("detail_code") else (parse_detail(rec.get("detail")) or {})
    w = _int(src.get("detail_wait"))
    return tr("gw_exits.detail.waited", detail=text, s=w) if w is not None else text


def state_text(row):
    """What to say about an exit (a ``public`` row with ``usable``): «работает» (and the age of its handshake —
    ``handshake``, seconds, AmneziaWG), «работает, но нет ответа 15 с (проверка 2 из 3)», the problem of its key,
    «ещё не проверялся» (and why, if the probe said something; ``gw_off`` — the gateway is off, nobody checks: «включите
    шлюз, чтобы проверять выходы»), «не работает: нет ответа 35 с» — and with ``others``
    (how many other exits work) what happens to its clients."""
    detail = detail_text({"detail": str(row.get("detail") or "")})     # our words of either language: in this one
    if row.get("problem"):
        return row["problem"]
    if row.get("enabled") is False:
        # К2-7 (T-KT2): off for the gateway — not checked (xray does not probe it): its last probe is no state now
        return tr("gw_exits.state.off_for_gw")
    silent = row.get("silent")
    hs = row.get("handshake")
    hs_text = tr("gw_exits.state.handshake", ago=ago_text(hs)) if isinstance(hs, (int, float)) \
        and not isinstance(hs, bool) else ""
    fails = int(row.get("fails") or 0)
    if row.get("usable") or row.get("state") == "alive":
        if silent is not None and fails:
            of = int(row.get("dies_after") or fails)
            # fewer than MIN_TRIES checks and half of them failed: not «работает» — the rule has not decided yet
            # (REV-335 №4: «работает … не прошло 2 из 3» read as a contradiction)
            few = of < MIN_TRIES and 2 * fails >= of
            return tr("gw_exits.state.alive_few" if few else "gw_exits.state.alive_failing", why=why_text(detail, silent),
                      n=fails, of=of, min=MIN_TRIES) + (("; " + hs_text) if hs_text else "")
        return tr("gw_exits.state.alive") + ((", " + hs_text) if hs_text else "")
    if row.get("state") == "unknown":
        if row.get("gw_off"):                       # T-X6: not «ещё не проверялся» — nobody checks them now
            return tr("gw_exits.state.gw_off")
        checked = detail and _code_of(row)[0] != "detail.not_checked"
        return word("detail.not_checked") + ((" (%s)" % detail) if checked else "")
    why = why_text(detail, silent)
    text = tr("gw_exits.state.dead_why", why=why) if why else tr("gw_exits.state.dead")
    text += (", " + hs_text) if hs_text else ""
    others = row.get("others")
    if row.get("enabled", True) and isinstance(others, int) and not isinstance(others, bool):
        text += " — " + tr("gw_exits.state.clients_move" if others > 0 else "gw_exits.state.no_others")
    return text


# ── liveness ───────────────────────────────────────────────────────────────

def links(runner):
    """Names of the network interfaces (``ip -o link show``)."""
    res = runner.run(["ip", "-o", "link", "show"])
    names = set()
    for line in (res.out if res.ok else "").splitlines():
        parts = line.split(":", 2)
        if len(parts) >= 2:
            names.add(parts[1].strip().split("@", 1)[0])
    return names


def awg_tool(runner):
    p = paths.egress_awg_tool()
    if os.path.isfile(p) and os.access(p, os.X_OK):
        return p
    return runner.which("awg")


def handshake_age(runner, tool, iface, now):
    """Seconds since the latest handshake of the leg, or None (never / unreadable)."""
    res = runner.run([tool, "show", iface, "latest-handshakes"])
    if not res.ok:
        return None
    best = 0
    for line in res.out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].isdigit():
            best = max(best, int(parts[-1]))
    if best <= 0:
        return None
    return max(0, int(now - best))


def _mark(s, mark):
    s.setsockopt(socket.SOL_SOCKET, SO_MARK, mark)


def _drop(s):
    """Close at once with a reset (SO_LINGER 0): no FIN exchange, no TIME_WAIT, nothing sent through the leg later."""
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    except OSError:
        pass
    try:
        s.close()
    except OSError:
        pass


def connect_through(marks, targets=PROBE_TARGETS, timeout=PROBE_TIMEOUT, stagger=None, took=None):
    """{mark: True | False | None}: a TCP connection opened with the route mark (SO_MARK — the leg's ip rule and
    table, the path of its clients' packets; a leg whose route is gone hits its ``unreachable`` backstop or gw_out,
    never the main table) to any of ``targets`` within ``timeout`` s. Every mark at once (non-blocking sockets, one
    selector): a round costs one wait, no process. Only a completed connection counts; it is reset at once. None —
    the socket could not be marked (not root): nothing is known.

    The targets of one mark go ONE AFTER ANOTHER (Н3, T-KT1): the next one only when the earlier ones did not answer
    within ``stagger`` s (PROBE_STAGGER) or refused at once — a working leg costs one connection (SYN, SYN-ACK, ACK,
    RST: 4 packets through the tunnel), not one per target (12 every 10 s on every AmneziaWG exit); a leg that does not
    answer still gets every target within ``timeout``. ``took`` — a dict: {mark: ms} of the connection that answered
    (from its own connect() to its completion — the response of a Russian AmneziaWG exit, SPEC-groups §6.1)."""
    stagger = PROBE_STAGGER if stagger is None else stagger
    out = {}
    pending = {}                                    # socket -> mark
    began = {}                                      # socket -> monotonic of its connect() (``took``)
    left_of = {}                                    # mark -> the targets not tried yet
    next_at = {}                                    # mark -> when its next target starts (it has one and is undecided)
    sel = selectors.DefaultSelector()

    def forget(m):                                  # the other connections of a leg that has answered (or can't)
        for o in [o for o, mm in pending.items() if mm == m]:
            del pending[o]
            sel.unregister(o)
            _drop(o)
        left_of[m] = []
        next_at.pop(m, None)

    def start(m):                                   # the next target of ``m``: pending, decided or none left
        next_at.pop(m, None)
        while left_of.get(m):
            host, port = left_of[m].pop(0)
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            except OSError:
                out[m] = None
                forget(m)
                return
            try:
                if m:                               # 0 — no mark: the node's own route (``direct_probe``)
                    _mark(s, m)
                s.setblocking(False)
                rc = s.connect_ex((host, port))
            except OSError:
                _drop(s)
                out[m] = None
                forget(m)
                return
            if rc == 0:
                _drop(s)
                out[m] = True
                if took is not None:
                    took[m] = 0
                forget(m)
                return
            if rc in (errno.EINPROGRESS, errno.EAGAIN, errno.EWOULDBLOCK, errno.EALREADY):
                pending[s] = m
                began[s] = time.monotonic()
                sel.register(s, selectors.EVENT_WRITE)
                if left_of[m]:
                    next_at[m] = time.monotonic() + stagger
                return
            _drop(s)                                # ENETUNREACH: the leg's backstop — its route is gone; the next one

    try:
        for m in marks:
            out[m] = False
            left_of[m] = list(targets)
            start(m)
        deadline = time.monotonic() + timeout
        while pending or next_at:
            now = time.monotonic()
            left = deadline - now
            if left <= 0:
                break
            wait = min([left] + [max(0.0, t - now) for t in next_at.values()])
            for key, _ in (sel.select(wait) if pending else (time.sleep(wait) or [])):
                s = key.fileobj
                if s not in pending:
                    continue                        # dropped a moment ago: its leg has answered
                m = pending.pop(s)
                sel.unregister(s)
                try:
                    ok = s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0
                except OSError:
                    ok = False
                _drop(s)
                if ok:
                    out[m] = True
                    if took is not None and s in began:
                        took[m] = int(round((time.monotonic() - began[s]) * 1000))
                    forget(m)
                elif out[m] is False and not any(mm == m for mm in pending.values()):
                    start(m)                        # refused: the next target at once, not after the stagger
            now = time.monotonic()
            for m in [m for m, t in next_at.items() if t <= now]:
                if out[m] is False:
                    start(m)
                else:
                    next_at.pop(m, None)
    finally:
        for s in list(pending):
            _drop(s)
        sel.close()
    return out


def direct_probe(targets, timeout=PROBE_TIMEOUT):
    """The node's own network to these targets, without a mark (§4.6): True — one of them took a TCP connection."""
    return connect_through([0], targets, timeout).get(0) is True


def observatory(runner):
    """{outbound tag: alive} from the gateway xray's metrics (``/debug/vars`` -> "observatory"), or None
    when xray does not answer. A dead outbound has no "alive" key (protobuf omitempty)."""
    obs = observatory_detail(runner)
    return None if obs is None else dict((t, v["alive"]) for t, v in obs.items())


def debug_vars(runner):
    """The metrics of the gateway xray (``/debug/vars``: the observatory and the counters ``stats.outbound``) — one
    request a round for both (§5.1, §5.6); None when xray does not answer."""
    res = runner.run(["curl", "-s", "--max-time", "3", "http://%s/debug/vars" % METRICS_ADDR], timeout=6)
    if not res.ok or not res.out.strip():
        return None
    try:
        doc = json.loads(res.out)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else {}


def observatory_detail(runner, doc=None):
    """{outbound tag: {"alive", "try_at" (last_try_time: one probe of xray — 3.3.5 counts it once), "delay" (ms; 99999999
    — a failure), "error" (last_error_reason, redacted, at most OBS_ERROR_MAX characters)}}, or None when xray does not
    answer. ``doc`` — ``debug_vars`` read already this round."""
    doc = debug_vars(runner) if doc is None else doc
    if doc is None:
        return None
    obs = doc.get("observatory") if isinstance(doc, dict) else None
    if not isinstance(obs, dict):
        return {}
    out = {}
    for t, v in obs.items():
        if not isinstance(v, dict):
            continue
        err = " ".join(str(v.get("last_error_reason") or "").split())
        err = util.redact(err)
        out[t] = {"alive": bool(v.get("alive")), "try_at": _int(v.get("last_try_time")),
                  "delay": _int(v.get("delay")),
                  "error": err[:OBS_ERROR_MAX - 1] + "…" if len(err) > OBS_ERROR_MAX else err}
    return out


DELAY_FAIL = 99999999                  # the delay of a failed probe of the observatory (§5.1, С4)


def xray_counters(doc, tags):
    """{tag: rx + tx bytes} of the outbounds ``tags`` from ``debug_vars`` (``stats.outbound.<tag>.uplink/downlink``,
    with ``stats {}`` and ``statsOutbound*`` in the config — §5.6); a tag without counters is not there."""
    out = {}
    so = (((doc or {}).get("stats") or {}).get("outbound") or {}) if isinstance(doc, dict) else {}
    for t in tags:
        v = so.get(t) if isinstance(so, dict) else None
        if not isinstance(v, dict):
            continue
        up, down = _int(v.get("uplink")), _int(v.get("downlink"))
        if up is None and down is None:
            continue
        out[t] = (down or 0, up or 0)
    return out


def iface_counters(iface):
    """(rx, tx) bytes of the interface of a leg (/sys/class/net/<iface>/statistics), None when it is not there."""
    if not isinstance(iface, str) or not re.match(r"^[A-Za-z0-9_.-]{1,15}$", iface):
        return None
    base = os.path.join(paths.P("/sys/class/net"), iface, "statistics")
    try:
        rx = int(util.read_text(os.path.join(base, "rx_bytes")).strip())
        tx = int(util.read_text(os.path.join(base, "tx_bytes")).strip())
    except (TypeError, ValueError, AttributeError, OSError):
        return None
    return rx, tx


_TARGETS = {}


def probe_targets(role="abroad"):
    """The targets of the probes of the exits of a group by ``checks.targets`` (data: ``probe_targets.json``, §5.1):
    {"tcp": [(host, port)], "http": url | None, "direct": [(host, port)], "dns": [(ip, port)], "dns_name"}; an unknown
    role — those of «abroad». ``http`` None (``"http": null`` in the file) — no response probe over HTTP: a Russian
    exit's response is the time of its TCP connection (SPEC-groups §6.1, Б3: no HTTP to Yandex every 30 s from a home
    IP); a role without the key — PROBE_URL, as before."""
    if not _TARGETS:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_targets.json"),
                      encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            doc = {}
        for r in ROLES:
            got = doc.get(r) if isinstance(doc, dict) else None
            got = got if isinstance(got, dict) else {}
            tcp = [tuple(x) for x in got.get("tcp") or [] if isinstance(x, list) and len(x) == 2] or \
                list(PROBE_TARGETS)
            direct = [tuple(x) for x in got.get("direct") or [] if isinstance(x, list) and len(x) == 2] or tcp[:1]
            dns = [tuple(x) for x in got.get("dns") or [] if isinstance(x, list) and len(x) == 2] or \
                [(h, 53) for h, _ in tcp]
            http = got.get("http", PROBE_URL)
            _TARGETS[r] = {"tcp": tcp, "http": str(http) if http else None, "direct": direct, "dns": dns,
                           "dns_name": str(got.get("dns_name") or "ya.ru")}
    return _TARGETS.get(role) or _TARGETS["abroad"]


def http_probe(marks, url=PROBE_URL, timeout=PROBE_TIMEOUT):
    """{mark: rtt ms | None}: ``GET url`` through each route mark (SO_MARK, like ``connect_through``), every mark at once
    (non-blocking sockets, one selector) — the time from ``connect()`` to the first line of the answer (≈ 2 RTT, §5.1).
    None — no answer within ``timeout`` s (or the socket could not be marked). Plain HTTP only (no TLS: the curve bug
    of the egress observatory)."""
    u = urllib.parse.urlsplit(url)
    host, port = u.hostname or "", u.port or 80
    path = (u.path or "/") + ("?" + u.query if u.query else "")
    req = ("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: kaskad-probe\r\nConnection: close\r\n\r\n"
           % (path, host)).encode("ascii", "replace")
    out = dict((m, None) for m in marks)
    try:
        addr = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4]
    except (OSError, IndexError, UnicodeError):
        return out
    sel = selectors.DefaultSelector()
    socks = {}
    try:
        for m in marks:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            except OSError:
                continue
            try:
                _mark(s, m)
                s.setblocking(False)
                rc = s.connect_ex(addr)
            except OSError:
                _drop(s)
                continue
            if rc not in (0, errno.EINPROGRESS, errno.EAGAIN, errno.EWOULDBLOCK, errno.EALREADY):
                _drop(s)
                continue
            socks[s] = {"mark": m, "t0": time.monotonic(), "sent": False, "buf": b""}
            sel.register(s, selectors.EVENT_WRITE)
        deadline = time.monotonic() + timeout
        while socks:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            for key, _ in sel.select(left):
                s = key.fileobj
                st = socks.get(s)
                if st is None:
                    continue
                try:
                    if not st["sent"]:
                        if s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) != 0:
                            raise OSError("connect")
                        s.send(req)
                        st["sent"] = True
                        sel.modify(s, selectors.EVENT_READ)
                        continue
                    got = s.recv(256)
                    if not got:
                        raise OSError("closed")
                    st["buf"] += got
                    if b"\n" in st["buf"] or len(st["buf"]) >= 64:
                        if st["buf"].startswith(b"HTTP/"):
                            out[st["mark"]] = int((time.monotonic() - st["t0"]) * 1000 + 0.5)
                        raise OSError("done")
                except OSError:
                    sel.unregister(s)
                    del socks[s]
                    _drop(s)
    finally:
        for s in list(socks):
            _drop(s)
        sel.close()
    return out


SOCKS_HOST = "127.0.0.1"            # probe-<id> of the gateway xray (xray.probe_port, §5.2)


def _recv_n(s, n):
    got = b""
    while len(got) < n:
        part = s.recv(n - len(got))
        if not part:
            raise OSError("closed")
        got += part
    return got


def _socks_reply(s):
    """Read the reply of a SOCKS5 CONNECT: -> its REP (0 — granted). xray grants at once, before it dials (SE §5.3):
    the grant proves nothing of the far side — the DNS answer does (``_socks_one``)."""
    head = _recv_n(s, 4)
    n = {1: 4, 4: 16}.get(head[3])
    if n is None:
        n = _recv_n(s, 1)[0]
    _recv_n(s, n + 2)
    return head[1]


def dns_query(name, qid):
    """A DNS query «A ``name``» over TCP (the length first), recursion desired."""
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    for part in name.strip(".").split("."):
        b = part.encode("idna")
        q += bytes([len(b)]) + b
    q += b"\x00" + struct.pack(">HH", 1, 1)
    return struct.pack(">H", len(q)) + q


def _socks_one(port, ip, dport, name, timeout):
    """One target through the SOCKS5 of ``probe-<id>`` (SPEC-groups §6.1): CONNECT ATYP=IPv4 ``ip:dport`` and a DNS
    query «A ``name``» over TCP. -> (ok, rtt ms | None): ok True — a DNS answer of that query came back (any RCODE: the
    far side answered), rtt — from the query to the answer; None — 127.0.0.1:``port`` refused (the inbound is not
    there: xray down, the exit not drawn)."""
    try:
        s = socket.create_connection((SOCKS_HOST, port), timeout=timeout)
    except ConnectionRefusedError:
        return None, None
    except OSError:
        return False, None
    try:
        s.settimeout(timeout)
        s.sendall(b"\x05\x01\x00")
        if _recv_n(s, 2) != b"\x05\x00":
            return False, None
        s.sendall(b"\x05\x01\x00\x01" + socket.inet_aton(ip) + struct.pack(">H", int(dport)))
        if _socks_reply(s) != 0:
            return False, None
        qid = int.from_bytes(os.urandom(2), "big")
        t0 = time.monotonic()
        s.sendall(dns_query(name, qid))
        n = struct.unpack(">H", _recv_n(s, 2))[0]
        ans = _recv_n(s, n) if n >= 12 else b""
        if len(ans) < 12 or struct.unpack(">H", ans[:2])[0] != qid or not ans[2] & 0x80:
            return False, None
        return True, int((time.monotonic() - t0) * 1000 + 0.5)
    except (OSError, ValueError, IndexError, UnicodeError, struct.error):
        return False, None              # xray closed it (its outbound did not reach the target), a timeout
    finally:
        _drop(s)


def socks_probe(items, timeout=PROBE_TIMEOUT):
    """T-q круг 3 (З-2; SPEC-groups §6.1): the probe of a RUSSIAN xray exit — through its own ``probe-<id>`` (SOCKS5
    127.0.0.1:port -> gr-<id>) to the DNS targets of its group (``probe_targets(role)["dns"]``, «A dns_name»), never
    the observatory (a foreign target, every 10 s, every Russian AmneziaWG leg too). ``items`` — {key: (port, targets,
    name)}. Every exit at once (a thread each), its targets one after another within ``timeout`` s.
    -> {key: (alive True|False|None, rtt ms|None)}: None — the inbound refused (unknown)."""
    out = dict((k, (None, None)) for k in items)

    def one(k, port, targets, name):
        deadline = time.monotonic() + timeout
        res = (False, None)
        for i, (ip, dport) in enumerate(targets):
            left = deadline - time.monotonic()
            if left <= 0.05:
                break
            last = i == len(targets) - 1
            ok, rtt = _socks_one(port, ip, dport, name, left if last else max(min(left, 2.0), left / (len(targets) - i)))
            if ok is None:
                res = (None, None)
                break
            if ok:
                res = (True, rtt)
                break
        out[k] = res

    threads = [threading.Thread(target=one, args=(k, p, list(t or ()), n), daemon=True)
               for k, (p, t, n) in items.items()]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout + 1)
    return out


def xray_age(runner):
    """Seconds since kaskad-gw-xray.service became active (systemd's ActiveEnterTimestampMonotonic, the
    clock of ``time.monotonic``), or None when unknown."""
    res = runner.run(["systemctl", "show", "-p", "ActiveEnterTimestampMonotonic", "--value", XRAY_UNIT])
    v = (res.out or "").strip() if res.ok else ""
    if not v.isdigit() or int(v) <= 0:
        return None
    return max(0.0, _mono() - int(v) / 1e6)


def leg_age(runner, iface):
    """Seconds since the unit of the leg (kaskad-leg@<iface>) became active, or None when unknown."""
    res = runner.run(["systemctl", "show", "-p", "ActiveEnterTimestampMonotonic", "--value",
                      "kaskad-leg@%s.service" % iface])
    v = (res.out or "").strip() if res.ok else ""
    if not v.isdigit() or int(v) <= 0:
        return None
    return max(0.0, _mono() - int(v) / 1e6)


def _probe_awg(runner, e, tool, ok, now):
    """(alive, detail (code, args), handshake age) of one AmneziaWG leg whose interface is up; ``ok`` — its connection
    (``connect_through``; None: the socket could not be marked — unknown)."""
    hs = handshake_age(runner, tool, e["iface"], now) if tool else None
    if ok:
        return True, ("detail.answers", {}), hs
    if ok is None:
        return None, ("detail.no_root", {}), hs
    return False, ("detail.no_answer", {}), hs


def probe(runner, exits, active=True, only=None, doc=None, targets=None, measure=False, vouched=(), own=None):
    """{id: {"alive": True|False|None, "detail": str, ...}} for every exit of the pool (``detail`` — the words in the
    language of the moment; ``merge`` keeps their code, ``parse_detail``). None is UNKNOWN: xray
    does not answer, has not probed the exit yet, or says "dead" less than XRAY_GRACE s after its start —
    ``merge`` keeps the previous state then. AmneziaWG legs also give ``handshake`` (seconds since the last one,
    None: never), ``dies_after`` and, when they did not answer, ``waited`` (seconds the connections waited: the
    silence began that long before the round); without ``active`` they are not connected through — unknown.

    Stage 1 (§3.2, §5.1): ``only`` — the ids probed this round (the period of their group, «Лениво», the quota): the
    others are left out of the answer (no answer is no unknown — ``merge`` keeps their record as it is); ``doc`` —
    ``debug_vars`` read already this round; ``targets`` — {id: [(host, port)]} of the probe «alive» of an AmneziaWG exit
    by ``checks.targets`` of its group (default PROBE_TARGETS); ``measure`` — an xray exit also gets ``rtt`` (the delay
    of a good probe of the observatory) and ``try_age`` (s since its last probe: the freshness, §4.6); ``vouched`` — the
    ids of AmneziaWG exits a fresh GOOD probe of the gateway's observatory through their own outbound answers for this
    round (Н3, ``collect.probe_round``): no connection of ours — the same answer «отвечает»; ``own`` — {id: (alive,
    rtt)} of the Russian xray exits probed by ``socks_probe`` this round (T-q круг 3: never the observatory), or a
    callable giving it (they ran while the legs were connected through: one wait for both)."""
    now = _now()
    if only is not None:
        exits = [e for e in exits if e["id"] in only]
    names = links(runner) if any(e["kind"] == "awg" for e in exits) else set()
    tool = awg_tool(runner) if names else None
    # Right after a reboot (boot.pending) the legs come up under systemd while the snapshot still holds their state
    # before the reboot: a leg not up yet is UNKNOWN — merge keeps its state (at most UNKNOWN_MAX s), so the devices
    # are not moved away and back while the legs start (T-M6-qs330b №8); so is a leg up less than LEG_GRACE s and
    # silent (the boot, a heal). Fail-closed all the same: a device on a leg without its interface hits the leg's
    # unreachable backstop.
    booting = any(e["kind"] == "awg" for e in exits) and boot.pending()
    up = [e["mark"] for e in exits if e["kind"] == "awg" and e["iface"] in names]
    t0 = time.monotonic()
    answered = {}
    took = {}                                           # mark -> ms of its connection (``conn_ms``, measure only)
    if active and up:
        by_t = {}
        for e in exits:
            if e["kind"] == "awg" and e["mark"] in up and e["id"] in vouched:
                answered[e["mark"]] = True              # the observatory got through this leg just now (Н3)
            elif e["kind"] == "awg" and e["mark"] in up:
                by_t.setdefault(tuple((targets or {}).get(e["id"]) or PROBE_TARGETS), []).append(e["mark"])
        for tg, ms in sorted(by_t.items()):
            answered.update(connect_through(ms, list(tg), took=took if measure else None))
    waited = int(time.monotonic() - t0 + 0.5)          # a leg that did not answer was silent that long already
    if callable(own):
        own = own()                                     # the probes of the Russian xray exits, run meanwhile
    obs, age, asked = None, None, False
    out = {}
    extra = {}
    for e in exits:
        if e["kind"] == "awg":
            if e["iface"] not in names:
                out[e["id"]] = (None, ("detail.leg_booting", {"iface": e["iface"]})) if booting \
                    else (False, ("detail.no_iface", {"iface": e["iface"]}))
                extra[e["id"]] = {"handshake": None, "dies_after": AWG_FAILS_TO_DIE}
                continue
            if active:
                a, d, hs = _probe_awg(runner, e, tool, answered.get(e["mark"]), now)
                if a is False:
                    la = leg_age(runner, e["iface"])
                    if la is not None and la < LEG_GRACE:
                        a, d = None, ("detail.leg_young", {"iface": e["iface"], "s": int(la)})
            else:
                a, d, hs = None, ("detail.not_checked", {}), handshake_age(runner, tool, e["iface"], now) if tool else None
            out[e["id"]] = (a, d)
            extra[e["id"]] = {"handshake": hs, "dies_after": AWG_FAILS_TO_DIE}
            if a and took.get(e["mark"]) is not None:
                extra[e["id"]]["conn_ms"] = took[e["mark"]]      # the collector takes it where no HTTP probe is
            if a is False:
                extra[e["id"]]["waited"] = waited
        elif is_xray(e) and e["id"] in (own or {}):
            a, rtt = own[e["id"]]
            if a is None:
                out[e["id"]] = (None, ("detail.xray_silent", {}))
            elif a:
                out[e["id"]] = (True, ("detail.server_answers", {}))
                if measure and rtt is not None:
                    extra[e["id"]] = {"rtt": rtt}
            else:
                xa = xray_age(runner)
                out[e["id"]] = (None, ("detail.xray_young", {"s": int(xa)})) if xa is not None and xa < XRAY_GRACE \
                    else (False, ("detail.server_silent", {}))
        elif is_xray(e):
            if not asked:
                asked = True
                obs = observatory_detail(runner, doc)
                age = xray_age(runner) if obs is not None else None
            o = (obs or {}).get(e["tag"])
            if obs is None:
                out[e["id"]] = (None, ("detail.xray_silent", {}))
            elif o is None:
                out[e["id"]] = (None, ("detail.xray_unchecked", {}))
            elif o["alive"]:
                out[e["id"]] = (True, ("detail.server_answers", {}))
            elif age is not None and age < XRAY_GRACE:
                out[e["id"]] = (None, ("detail.xray_young", {"s": int(age)}))
            else:
                out[e["id"]] = (False, ("detail.server_silent", {}))
                # 3.3.5: why xray says so — the journal line of «не работает» (collect), never a key
                extra[e["id"]] = dict(("obs_" + k, o[k]) for k in ("delay", "error") if o.get(k) not in (None, ""))
            if o is not None and o.get("try_at") and out[e["id"]][0] is not None:
                extra.setdefault(e["id"], {})["try_at"] = o["try_at"]   # one probe of xray: one check (3.3.5)
            if measure and o is not None and o.get("try_at"):
                extra.setdefault(e["id"], {})["try_age"] = max(0, int(now - o["try_at"]))
            if measure and o is not None and o["alive"] and o.get("delay") is not None and 0 <= o["delay"] < DELAY_FAIL:
                extra.setdefault(e["id"], {})["rtt"] = o["delay"]
        else:
            out[e["id"]] = (False, _problem_detail(e))
    return dict((k, dict(_answer(a, d), **extra.get(k, {}))) for k, (a, d) in out.items())


def _problem_detail(e):
    """(code, args) of the problem of a key that cannot be used; (None, its words) when they have no code."""
    if e.get("problem_code"):
        return e["problem_code"], dict(e.get("problem_args") or {})
    if e.get("problem"):
        got = parse_detail(e["problem"])
        return (got["detail_code"], dict(got.get("detail_args") or {})) if got else (None, str(e["problem"]))
    return "problem.unparsed", {}


def _answer(alive, detail):
    """A probe answer: alive and the words of its detail (the language of the moment)."""
    code, args = detail
    return {"alive": alive, "detail": args if code is None else word(code, **args)}


# ── the snapshot ───────────────────────────────────────────────────────────

def snapshot_path():
    return os.path.join(paths.gw_dir(), "exits.json")


def window_path():
    """The checks of the last WINDOW s of every exit (3.3.5): /run — every round writes them, the disk only the state."""
    return os.path.join(paths.run_dir(), "exits-window.json")


WINDOW_KEYS = ("tries", "rise", "last_try", "try_seen", "try_mono", "h_win")   # in window_path(), not on the disk
# ``h_win`` — the window of health (its checks of «упал», good ones in a row, the last probe of xray, the samples): in
# /run as the window of 3.3.5 (§3.2, §3.4) — a restarted collector goes on with it, a reboot starts it anew


def load_snapshot():
    """The snapshot (/etc/kaskad/gw/exits.json) with the window of each exit (``window_path``; none after a reboot —
    every window starts anew)."""
    raw = util.read_text(snapshot_path())
    try:
        doc = json.loads(raw) if raw else {}
    except ValueError:
        doc = {}
    if not isinstance(doc, dict) or not isinstance(doc.get("exits"), dict):
        doc = {"at": 0, "pool": [], "exits": {}}
    doc.setdefault("pool", [])
    try:
        win = json.loads(util.read_text(window_path()) or "{}")
    except ValueError:
        win = {}
    for eid, w in (win.items() if isinstance(win, dict) else ()):
        rec = doc["exits"].get(eid)
        if isinstance(rec, dict) and isinstance(w, dict):
            rec.update((k, w[k]) for k in WINDOW_KEYS if k in w)
    return doc


def save_snapshot(snap, state=True):
    """The state to the disk (``state`` — it changed: ``merge`` dirty) and the windows to /run (every round)."""
    if state:
        disk = dict(snap, exits=dict((eid, dict((k, v) for k, v in rec.items() if k not in WINDOW_KEYS))
                                     if isinstance(rec, dict) else rec for eid, rec in (snap.get("exits") or {}).items()))
        util.atomic_write(snapshot_path(), util.json_text(disk), 0o600)
    win = dict((eid, dict((k, rec[k]) for k in WINDOW_KEYS if k in rec))
               for eid, rec in (snap.get("exits") or {}).items() if isinstance(rec, dict))
    win = dict((eid, w) for eid, w in win.items() if w)
    try:
        util.atomic_write(window_path(), util.json_text(win), 0o600)
    except OSError:
        pass                                # no /run (a test, a chroot): the window starts anew next round


def _state(rec):
    """What of a record is the state of the exit (worth writing to disk); "detail" and the window are not."""
    rec = rec or {}
    return (bool(rec.get("alive")), int(rec.get("fails") or 0), rec.get("unknown_since"))


def _window(o, now):
    """[[ts, 1|0], …] — the checks of ``o`` younger than WINDOW s."""
    out = []
    for x in (o or {}).get("tries") or []:
        if isinstance(x, list) and len(x) == 2 and isinstance(x[0], (int, float)) and not isinstance(x[0], bool) \
                and 0 <= now - x[0] < WINDOW:
            out.append([x[0], 1 if x[1] else 0])
    return out


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _step(eid, o, p, now, mono=None):
    """One exit of ``merge``: (new record, change (id, was, alive) or None). ``fail_since`` — the first failed check
    of the run that goes on (kept while the exit stays dead). The rule «упал» (owner 15.10, 3.3.5): at least DEAD_SHARE
    of the checks of the last WINDOW s failed, MIN_TRIES checks at the least, told on a failed one; back after RISE good
    checks in a row; the window (``tries``) starts anew on a change, not between ok/degraded/standby (Н12). ``fails`` — failed checks of the window while
    the last one failed (0 after a good one), counted on while dead; ``checks`` — the checks of the window then.
    VLESS: ``try_at`` (last_try_time of xray) the record has seen already — the same probe read again: no check; read
    again for UNKNOWN_MAX s since it was first seen (``try_seen``; counted by ``try_mono`` — the monotonic clock, ``mono``:
    a step of the wall clock does not make every probe «old», REV-335-3 №5) — xray stopped probing the exit: the silence
    below (REV-335 №3: a frozen probe must not keep an exit «работает» for good).
    UNKNOWN for UNKNOWN_MAX s — dead at once, since the start of the silence (``fail_since`` = ``unknown_since``).

    A NEW exit (``new``: never alive since it was added) is «проверяется», not dead, until the rule tells «упал»: the
    first probes run before its leg or xray is up — no «не работает — нет интерфейса kawg5» for a key that works a
    second later; alive on its first good check (T-M6-qs330d №3, №4)."""
    a = p.get("alive")
    dd = _detail_of(p)
    fresh = o is None or bool(o.get("new"))
    was = None if fresh else bool(o.get("alive"))
    t = _int(p.get("try_at"))
    if a is not None and t and o is not None and o.get("last_try") == t:
        seen = _num(o.get("try_seen"))
        seen_m = _num(o.get("try_mono"))
        age = mono - seen_m if seen_m is not None and mono is not None and mono >= seen_m else \
            (now - seen if seen is not None else None)
        if age is not None and age >= UNKNOWN_MAX:         # xray has not probed it for a minute: the silence
            rec, change = _silent(eid, o, was, seen if seen is not None else now - age, now,
                                  _detail_of({"detail_code": "detail.xray_stale"}, wait=int(age)))
            rec.update(last_try=t, try_seen=seen)              # the same probe stays «seen»: no check out of it
            if seen_m is not None:
                rec["try_mono"] = seen_m
            return rec, change
        rec = dict(o)                      # the same probe of xray read again (5 s rounds): not one more check
        rec.pop("unknown_since", None)
        return rec, None
    base = {"last_try": t, "try_seen": now} if t else {}
    if t and mono is not None:
        base["try_mono"] = mono
    if a is None:
        if o is None:                      # a new exit nobody has checked yet: not usable, nothing to tell
            return dict({"alive": False, "since": now, "fails": 0, "unknown_since": now, "new": True}, **dd), None
        us = _num(o.get("unknown_since"))
        if us is None:
            rec = dict(o)
            rec["unknown_since"] = now
            return rec, None
        if now - us < UNKNOWN_MAX:
            return dict(o), None
        return _silent(eid, o, was, us, now, _detail_of(p, wait=int(now - us)))
    tries = _window(o, now)
    gap = now - tries[-1][0] if tries else None
    if gap is not None and (gap < CHECK_PERIOD - CHECK_SLACK or (a and not tries[-1][1] and gap < CHECK_PERIOD)):
        tries[-1] = [tries[-1][0], 1 if a else 0]   # a recheck within the period: its result replaces the last one
    else:
        tries.append([now, 1 if a else 0])
    if a:
        if was:
            return dict({"alive": True, "since": o.get("since", now), "fails": 0, "tries": tries}, **dict(dd, **base)), None
        if fresh:                          # a new exit answers: in use at once (it was never told dead)
            return dict({"alive": True, "since": now, "fails": 0, "tries": tries}, **dict(dd, **base)), (eid, None, True)
        rise = int(o.get("rise") or 0) + 1
        if rise < RISE:                    # dead: one good check is not back yet — its «why» stays
            rec = dict(o, rise=rise, **base)
            rec.pop("unknown_since", None)
            return rec, None
        return dict({"alive": True, "since": now, "fails": 0}, **dict(dd, **base)), (eid, False, True)
    n = len(tries)
    f = n - sum(x[1] for x in tries)
    before = int((o or {}).get("fails") or 0)
    fail_since = _num((o or {}).get("fail_since")) if before or was is False else None
    if fail_since is None:
        w = p.get("waited")                # the failed probe waited that long: the silence began before it
        fail_since = now - (w if isinstance(w, int) and not isinstance(w, bool) and 0 < w < 60 else 0)
    if was is False:                       # dead stays dead: no window until it is back (the disk is not rewritten)
        return dict({"alive": False, "since": o.get("since", now), "fails": max(before, 1),
                     "fail_since": fail_since}, **dict(dd, **base)), None
    if n >= MIN_TRIES and f >= DEAD_SHARE * n:
        return dict({"alive": False, "since": now, "fails": f, "fail_since": fail_since}, **dict(dd, **base)), \
            (eid, was, False)
    if fresh:                              # a new exit still coming up: not usable, nothing to tell yet
        rec = dict({"alive": False, "since": (o or {}).get("since", now), "fails": f, "checks": n,
                    "fail_since": fail_since, "new": True, "tries": tries},
                   **dict(_detail_of(p, wait=dd.get("detail_wait"), fail=[f, n]), **base))
        return rec, None
    rec = dict((k, v) for k, v in o.items() if k not in _DETAIL_KEYS and k != "unknown_since")
    rec.update(fails=f, checks=n, fail_since=fail_since, tries=tries, **base)    # alive: the rule did not tell «упал»
    rec.update(_detail_of(p, wait=dd.get("detail_wait"), fail=[f, n]))
    return rec, None


def _silent(eid, o, was, us, now, dd):
    """Silent for too long (xray of the node, a leg that does not come up, a probe of xray that does not change): down
    since the silence began (``us``) — no window."""
    rec = dict({"alive": False, "since": o.get("since", now) if was is False else now,
                "fails": max(1, int(o.get("fails") or 0)), "unknown_since": us,
                "fail_since": _num(o.get("fail_since")) if was is False and _num(o.get("fail_since")) else us}, **dd)
    return rec, (None if was is False else (eid, was, False))


_DETAIL_KEYS = ("detail", "detail_code", "detail_args", "detail_wait", "detail_window", "detail_fail")
DETAIL_KEYS = _DETAIL_KEYS              # live.py: the detail of an exit event, told again in the reader's language


def _detail_of(p, wait=None, fail=None):
    """The detail fields of a snapshot record from a probe answer: ``detail_code``/``detail_args`` (the answer's own, else
    found in its words — written a moment ago in the language of the moment, ``parse_detail``), ``detail_wait``,
    ``detail_window`` and ``detail`` — the words of all that in the language of the moment (for an earlier reader)."""
    words = str(p.get("detail") or "")
    out = {}
    code = p.get("detail_code")
    if isinstance(code, str) and code:
        out["detail_code"] = code
        if isinstance(p.get("detail_args"), dict) and p["detail_args"]:
            out["detail_args"] = dict(p["detail_args"])
    else:
        got = parse_detail(words)
        if got and not got.get("detail_wait") and not got.get("detail_window") and not got.get("detail_fail"):
            out["detail_code"] = got["detail_code"]
            if got.get("detail_args"):
                out["detail_args"] = got["detail_args"]
    if out.get("detail_code"):
        if wait is not None:
            out["detail_wait"] = int(wait)
        if fail is not None:
            out["detail_window"] = list(fail)
        out["detail"] = detail_text(out)
    else:                                   # words that are not ours: kept as they are, the suffixes in words too
        if wait is not None:
            words = tr("gw_exits.detail.waited", detail=words, s=int(wait))
        if fail is not None:
            words = tr("gw_exits.detail.fail_of", detail=words, n=fail[0], of=fail[1])
        out["detail"] = words
    return out


def merge(snap, probed, pool_ids, now=None, mono=None, off=(), ru=0):
    """New snapshot from the probe. Returns (snapshot, changes [(id, was_alive|None, alive)], dirty).

    Stage 1 (§4.2): ``alive`` here is what the probes say (the reading of the answers: unknown, the silence of xray,
    the words of the journal) — the state that decides the traffic is ``health``'s (``alive_ids``, ``health_of``); its
    fields (``HEALTH_KEYS``) a record keeps as they were, the collector writes them after ``health.evaluate``.

    UNKNOWN (alive None) keeps the previous state; UNKNOWN for UNKNOWN_MAX s — dead (since the silence began). Else
    the rule «упал» of ``_step`` (half of the checks of 2 min, 4 at the least; back after 2 good ones). ``changes``
    are the transitions of "alive" only (new exits included). ``dirty`` — the state of an exit (alive, failures,
    unknown since) or the pool changed: the snapshot on the disk; the windows go to /run every round
    (``save_snapshot(new, state=dirty)``). ``mono`` — the monotonic clock of the moment (``now`` given without it — none:
    the wall clock only).

    ``off`` — ids of the exits switched off for the gateway (К2-7, T-KT2): xray takes them out of its observatory, the
    probe that is read stays the last one — not a silence of xray. Their records stay as they are (no check, no «упал»);
    the clock of the silence starts anew (``try_seen`` — now), so the exit switched on again is not «xray давно не
    проверял» before xray has had time to probe it.

    ``ru`` — how many of the last ``pool_ids`` are lines of upstream-ru.txt (``ru_count``; T15): kept as ``pool_ru``,
    so ``renumbered`` counts each file by itself."""
    if now is None:
        now, mono = _now(), (_mono() if mono is None else mono)
    old = snap.get("exits") or {}
    exits = {}
    changes = []
    moved = False
    for eid in pool_ids:
        p = probed.get(eid)
        o = old.get(eid)
        if eid in off and o is not None:
            exits[eid] = _frozen(o, now, mono)
            continue
        if p is None:
            if o is not None:
                exits[eid] = o
            continue
        rec, change = _step(eid, o, p, now, mono)
        for k in HEALTH_KEYS:                  # the state of health stays (§3.4): merge writes only its own fields
            if o is not None and k in o and k not in rec:
                rec[k] = o[k]
        exits[eid] = rec
        if change:
            changes.append(change)
        if o is None or _state(o) != _state(rec):
            moved = True
    new = {"at": now, "pool": list(pool_ids), "exits": exits}
    if ru:
        new["pool_ru"] = int(ru)
    dirty = moved or bool(changes) or list(snap.get("pool") or []) != list(pool_ids) or set(old) != set(exits) or \
        int(snap.get("pool_ru") or 0) != int(ru or 0)
    return new, changes, dirty


def _frozen(o, now, mono):
    """The record of an exit off for the gateway (``merge``, ``off``): as it was, the silence not counted."""
    rec = dict(o)
    rec.pop("unknown_since", None)
    if rec.get("detail_code") == "detail.xray_stale":     # a silence told by mistake after the switch-off (3.3.7 and before)
        for k in _DETAIL_KEYS:
            rec.pop(k, None)
    if "last_try" in rec:
        rec["try_seen"] = now
        if mono is not None:
            rec["try_mono"] = mono
        else:
            rec.pop("try_mono", None)
    return rec


def renumbered(old_ids, new_ids, old_ru=0, new_ru=0):
    """Lines of the old pool that now hold another exit or none: a key removed shifts every line after it, a pool
    replaced changes them all. Lines the old pool did not have carried no connection. Each file by itself (T15): the
    last ``old_ru`` / ``new_ru`` ids of the lists are the lines of upstream-ru.txt (numbers ``RU_INDEX_BASE`` + place) —
    a Russian line removed shifts only the Russian ones, and a foreign key added never «renumbers» them. A snapshot
    written before Q4 has no Russian part (``old_ru`` 0)."""
    old_ids, new_ids = list(old_ids or []), list(new_ids or [])
    old_ru = max(0, min(int(old_ru or 0), len(old_ids)))
    new_ru = max(0, min(int(new_ru or 0), len(new_ids)))

    def diff(old, new, base):
        return [base + i for i, o in enumerate(old, 1) if (new[i - 1] if i <= len(new) else None) != o]
    return diff(old_ids[:len(old_ids) - old_ru], new_ids[:len(new_ids) - new_ru], 0) + \
        diff(old_ids[len(old_ids) - old_ru:], new_ids[len(new_ids) - new_ru:], RU_INDEX_BASE)


def ru_count(rows):
    """How many rows of a pool (``pool`` with both roles) are Russian — the part of the snapshot ``renumbered`` needs."""
    return sum(1 for e in rows or [] if role_of(e) == "ru")


def line_marks(lines):
    """Route marks of these pool lines: the AmneziaWG leg (256+N) and the VLESS exit (0x300+N) of each."""
    out = []
    for i in lines:
        if 1 <= i <= MAX_AWG_INDEX:
            out.append(AWG_MARK_BASE + i)
        if 1 <= i <= MAX_VLESS_INDEX:
            out.append(VLESS_MARK_BASE + i)
    return out


# ── route marks ────────────────────────────────────────────────────────────

def hrw(key, cands):
    """Rendezvous (highest random weight) choice: stable per key; when a candidate goes away only its
    keys move."""
    best = None
    for c in cands:
        h = int(hashlib.sha1(("%s|%s" % (key, c["id"])).encode("utf-8")).hexdigest()[:16], 16)
        if best is None or h > best[0] or (h == best[0] and c["id"] < best[1]["id"]):
            best = (h, c)
    return best[1] if best else None


def usable(st, exits, alive):
    """Exits the gateway may use now: parsed, enabled for the gateway, alive."""
    return [e for e in exits if parsed(e) and role_of(e) == "abroad" and e["id"] in alive
            and model.exit_pref(st, e["id"])["enabled"]]


def xray_exits(st, exits):
    """Xray exits with their own TPROXY port and xray inbound (enabled for the gateway, alive or not) —
    the same list for gw_pre and for xray's config."""
    return [e for e in exits if is_xray(e) and e.get("mark") and model.exit_pref(st, e["id"])["enabled"]]


def xray_vless(st, exits):
    """The name before stage 1 (T1) — ``xray_exits``."""
    return xray_exits(st, exits)


def fail_mark(route):
    return MARK_DIRECT if route.get("fail") == "direct" else MARK_BLOCK


# ── the decision of the tick (stage 1, SPEC-stage1-exits §2.2, §4.2, §4.4) ──
# ``Decision`` — what the collector decides in its tick (``assign.assign`` + the fields below) and what every other
# process reads back with ``decision(st)`` (pins.json + the snapshot of the states, no network, no command):
#
#   {"version": 1,
#    "groups": {gid: {"pins": {device: {"exit", "tier", "since", "why"}}, "active_tier", "tier_good_since",
#                     "last_step": None|"direct"|"block", "steps"?: {device: "exit"|"last_direct"|"last_block"}}},
#    "states": {exit id: "ok"|"degraded"|"standby"|"off"|"unknown"} — every exit of the pool (a missing id: not in it),
#    "gw_all": "gx-<id>"|"direct"|"block"|None, "quota_off": [ids off by their quota with action «remove»: not drawn
#    in xray, not probed — §2.7, §5.6; T5], "moves"?, "events"?, "dns_via"?,
#    "policy": the effective policy (memory only), "rows": {id: {"mark", "tag", "kind", "role"}} (memory only)}
#
# ``steps`` is only in the decision of the collector itself (its tick): a reader derives the step from the pins, the
# last step of the group and ``assign.pick_new`` — the same deterministic rule the collector writes next tick.

USABLE = ("ok", "degraded")                 # «годен» (§4.2): new devices go there; standby — only as a reserve
HEALTH_STATES = ("ok", "degraded", "standby", "off", "unknown")
MARK_SPREAD = 0x2fe                         # «по соединениям» (§4.5): numgen of gw_pre spreads it (net.MARK_SPREAD, T5)
FAIL_BLOCK = "fail-block"                   # the fallback block of a group in xray (§2.2; xray.FAIL_BLOCK, T5)
VLESS_BALANCER = "gw-vless"                 # «по соединениям»: the balancer of the xray exits of «Основная» (§4.5)
STEPS = ("exit", "last_direct", "last_block", "chosen")
# the fields of health (gw/health.py SNAPSHOT_KEYS + state_since) a record of the snapshot keeps when ``merge`` writes
# it anew: ``ops._apply`` merges a probe too, and must not wipe the state of the collector (§3.4)
HEALTH_KEYS = ("state", "reasons", "state_since", "off_until", "falls", "unstable_until", "unstable", "recovering",
               "norm_rtt", "geo", "shown", "rules", "quota", "last_ok")


def health_of(rec):
    """(state, shown) of a record of the snapshot: the state of ``health`` (§4.2); a record of 3.3.5 (no ``state``) —
    alive «ok», dead «off», else «unknown»; ``shown`` — what an «unknown» exit was before (kept for its devices)."""
    if not isinstance(rec, dict):
        return "unknown", None
    s = rec.get("state")
    if s in HEALTH_STATES:
        shown = rec.get("shown") if s == "unknown" and rec.get("shown") in HEALTH_STATES else None
        return s, shown
    was = state_of(rec)
    return ("ok" if was == "alive" else "off" if was == "dead" else "unknown"), None


def usable_rec(rec):
    """The exit of this record of the snapshot may carry traffic now («годен»; «unknown» — as it was shown)."""
    s, shown = health_of(rec)
    return (shown if s == "unknown" else s) in USABLE


def alive_ids(snap):
    """Ids of the exits that may carry traffic now — the ONE source «годен/мёртв» is ``health`` (§4.2): the state of
    the snapshot (a record of 3.3.5 — its ``alive``). ``alive`` of a record is only what its probes said last."""
    return set(eid for eid, r in (snap.get("exits") or {}).items() if isinstance(r, dict) and usable_rec(r))


def quota_off(snap, ids=None):
    """Ids of the exits off by their quota with ``action = remove`` (§5.6, О8): not drawn in xray, not probed."""
    out = []
    for eid, r in sorted((snap.get("exits") or {}).items()):
        if ids is not None and eid not in ids or not isinstance(r, dict):
            continue
        if r.get("state") == "off" and "quota" in (r.get("reasons") or []) and \
                (r.get("quota") or {}).get("action") == "remove":
            out.append(eid)
    return out


def _row(e):
    return {"mark": e.get("mark"), "tag": e.get("tag") or tag(e["id"], role_of(e)), "kind": e.get("kind"),
            "role": role_of(e), "parsed": parsed(e)}


def decision(st, snap=None, pool_exits=None, policy=None, pins_doc=None, alive=None):
    """The decision as the collector left it (§2.2): ``pins.json`` + the states of the snapshot ``exits.json``. Cheap
    and without side effects — files only, no network, no command, no parser (``pool_exits`` None: the lines of the
    pool as ``pool_fallback`` reads them — ids and marks). ``alive`` — a set of ids (the form of the callers before
    stage 1): those exits «ok», the others «off». An exit disabled for the gateway is «off» whatever the snapshot
    says (the collector says so on its next tick). -> Decision (see above)."""
    from . import pins as PN, policy as P
    if pool_exits is None:
        pool_exits = pool_fallback()
    snap = load_snapshot() if snap is None and alive is None else (snap or {})
    recs = snap.get("exits") or {}
    rows, states = {}, {}
    for e in pool_exits or []:
        if not isinstance(e, dict) or not e.get("id") or e["id"] in rows:
            continue
        rows[e["id"]] = _row(e)
        if alive is not None:
            s = "ok" if e["id"] in alive else "off"
        else:
            s, shown = health_of(recs.get(e["id"]))
        if not parsed(e) or not model.exit_pref(st, e["id"])["enabled"]:
            s = "off"
        states[e["id"]] = s
    pol = policy if policy is not None else P.load(st, list(pool_exits or []))
    doc = PN.read() if pins_doc is None else pins_doc
    groups = {}
    for gid, g in ((doc or {}).get("groups") or {}).items():
        if isinstance(g, dict):
            groups[gid] = dict(g)
    return {"version": 1, "groups": groups, "states": states, "gw_all": (doc or {}).get("gw_all"),
            "quota_off": quota_off(snap, set(rows)) if alive is None else [], "policy": pol, "rows": rows}


def _as_decision(st, arg, alive):
    """``target``/``now_of``/``marks`` take a Decision, or the pool and a set of alive ids (the form before stage 1)."""
    if isinstance(arg, dict) and "states" in arg:
        return arg
    return decision(st, pool_exits=arg if isinstance(arg, list) else None, alive=alive)


def route_group(st, route, dec):
    """(gid, group, missing) — where the route of a device resolves (§2.2): ``pool`` (and the default) — «main»,
    ``group:<gid>`` — that foreign group, ``exit:E`` — the group of E (E out of the pool: the group where it is still
    listed, else «main»; ``missing`` True). None for ``direct``/``block``."""
    from . import policy as P
    via = route.get("via") or ""
    if via in ("direct", "block"):
        return None, None, False
    pol = dec.get("policy") or P.load(st)
    eid = model.exit_of(route)
    missing = False
    if eid:
        grp = P.group_of(pol, eid)
        missing = eid not in (dec.get("states") or {})
        if grp is None or grp.get("role", "abroad") != "abroad":
            grp = P.group(pol, P.MAIN)
    else:
        grp = P.group(pol, model.group_of_route(route) or P.MAIN)
        if grp is None or grp.get("role", "abroad") != "abroad":
            grp = P.group(pol, P.MAIN)
    return (grp or {}).get("id", P.MAIN), grp, missing


def _foreign_exit(dec, eid):
    """The exit ``eid`` is foreign (True), Russian (False: its row or its group says so — the role guard, §3.1), or not
    known (None)."""
    from . import policy as P
    row = (dec.get("rows") or {}).get(eid)
    if row is not None and row.get("role") == "ru":
        return False
    grp = P.group_of(dec.get("policy") or {}, eid)
    if grp is not None:
        return grp.get("role", "abroad") == "abroad"
    return True if row is not None else None


def _last(grp, route):
    return "last_direct" if (grp or {}).get("fail") == "direct" or route.get("fail") == "direct" else "last_block"


def local_policy(st, pool=None):
    """The policy of ``st`` with the unplaced exits placed as the collector places them (``policy.load`` with a pool):
    ``pool`` — the ids (or rows) of the pool; None — the pool on this disk by ``line_ids`` (no parser) and the hints
    of subs.json. What a reader without a decision (the CLI, the bot, the panel, MCP) takes (REV-s1-b4 П-1): a new
    exit with the hint «group Дом» is in «Дом» there, as in the decision of the collector."""
    from . import policy as P
    subs = None
    if pool is None:
        try:
            pool = [eid for eid, _ in line_ids()]
        except (OSError, ValueError):
            pool = []
        try:
            from . import exitsubs
            subs = exitsubs.load()
        except Exception:                            # noqa: BLE001 — the hint of a subscription is only a hint
            subs = None
    return P.load(st, list(pool), subs)


def fail_of(st, route, dec=None, pool=None):
    """«direct» | «block» — where a route through the exits goes when they all fail, as the decision takes it
    (``_last``: the route's own policy **or** the one of its group — «main» for the pool, the group of E for exit:E,
    that group for group:<gid>; §2.2, REV-s1-b2 З1). None — a route not through the exits (direct, block, none).
    ``dec`` — a Decision (its ``policy``) or None: ``local_policy(st, pool)`` — the policy with the exits of the pool
    placed, as the collector's decision has them (REV-s1-b4 П-1). One answer for the CLI, the copy .kbk, trace, the
    scheme, the panel, MCP and the bot."""
    if not isinstance(route, dict) or not via_exits(route):
        return None
    pol = dec.get("policy") if isinstance(dec, dict) else None
    if pol is None:
        pol = local_policy(st, pool)
    _gid, grp, _missing = route_group(st, route, {"policy": pol, "states": {}})
    return "direct" if _last(grp, route) == "last_direct" else "block"


def route_view(st, route, pol):
    """A route for a reader (the panel, MCP): a copy of ``route`` with ``fail_eff`` — where it goes when the exits
    fail, as it ACTS now (``fail_of`` with the policy ``pol``: its own policy or its group's; None — not through the
    exits; REV-s1-b4 З-1) — and, for ``group:<gid>``, ``group_name`` (the name of the group for people, not its id).
    Not a dict — returned as is."""
    if not isinstance(route, dict):
        return route
    from . import policy as P
    out = dict(route)
    out["fail_eff"] = fail_of(st, route, {"policy": pol})
    gid = model.group_of_route(route)
    if gid:
        out["group_name"] = P.label(P.group(pol, gid) or {"id": gid})
    return out


def via_fails(st, pol, pool_ids=()):
    """{via: "direct"} of the targets whose GROUP sends directly when its exits fail (the pool — «main», exit:E — the
    group of E, group:<gid>): the panel says «при отказе — напрямую» for a route it is about to set (REV-s1-b4 З-1)."""
    from . import policy as P
    vias = ["pool"] + ["exit:%s" % e for e in pool_ids] + \
        ["%s%s" % (model.GROUP_VIA, g.get("id")) for g in pol.get("groups") or []
         if isinstance(g, dict) and g.get("id") and g.get("id") != P.MAIN and g.get("role", "abroad") == "abroad"]
    out = {}
    for v in vias:
        if fail_of(st, {"via": v, "fail": "block"}, {"policy": pol}) == "direct":
            out[v] = "direct"
    return out


def _step_of(st, device, route, dec, gid, grp, eid, missing):
    """(step, exit id | None, why) of a device whose route goes to the group ``gid`` (§2.2, §4.4)."""
    from . import assign
    states = dec.get("states") or {}
    g = (dec.get("groups") or {}).get(gid) or {}
    did = device.get("id")
    step = (g.get("steps") or {}).get(did) if did else None
    if step in ("last_direct", "last_block"):
        return step, None, "last"
    pin = (g.get("pins") or {}).get(did) if did else None
    pe = pin.get("exit") if isinstance(pin, dict) else None
    if pe and (assign._tier_index(grp, pe) is None or
               ((dec.get("rows") or {}).get(pe) or {}).get("role", (grp or {}).get("role", "abroad")) !=
               (grp or {}).get("role", "abroad")):
        # SEC-s1-q2 С-3, Б-1: a pin counts only in its own group and role — an exit moved to another group (a Russian
        # one: somebody else's home router) or whose line went to the other file is never taken from a stale
        # pins.json (gw apply, the hook, the render of xray read it before the collector's next tick)
        pe = None
    if pe and states.get(pe) is not None and (step == "exit" or states.get(pe) != "off"):
        return "exit", pe, (pin.get("why") or "new")
    if eid and not missing and states.get(eid) in USABLE:
        return "exit", eid, "exit"
    if g.get("last_step") in ("direct", "block") and not g.get("pins") or not grp:
        return _last(grp, route), None, "last"
    pick = assign.pick_new(did or "", gid, states, dec.get("policy"), prefer=eid if eid and not missing else None)
    if pick:
        return "exit", pick, ("evict" if eid and pick != eid else "new")
    return _last(grp, route), None, "last"


def _pick_role(dec, device, gid, role):
    """The exit a device of the group ``gid`` gets among the fit exits of ``role`` only (``assign.pick_new``) — None:
    none. The role of an exit — its row (no row — not fit)."""
    from . import assign
    rows = dec.get("rows") or {}
    states = dict((e, s) for e, s in (dec.get("states") or {}).items() if e in rows and role_of(rows[e]) == role)
    return assign.pick_new(device.get("id") or "", gid, states, dec.get("policy"))


def _spread_down(dec, gid, grp, device):
    """«по соединениям» of «Основная» with nothing usable (S26, КТ1): the group is on its last step — the device goes
    there (``fail``: block | direct) like a pinned one, not to the spread of dead legs (the kernel: numgen over them or
    the block mark; xray: ``gw-vless`` falling back to block whatever ``fail`` says). The step of the collector's tick
    (``steps``), else — a reader of pins.json — the last step it wrote with no pins, else the states themselves (the
    rule of ``assign._group``: no tier with an exit «ok» / «degraded» / «в запасе», «unknown» as it was)."""
    from . import assign
    g = (dec.get("groups") or {}).get(gid) or {}
    step = (g.get("steps") or {}).get(device.get("id")) if device.get("id") else None
    if step in ("last_direct", "last_block"):
        return True
    if step is not None:
        return False
    if g.get("last_step") in ("direct", "block") and not g.get("pins"):
        return True
    return assign._klass(dec.get("states") or {}, grp, seen=True)[0] is None


def target(st, device, decision=None, alive=None):
    """Where the traffic of ``device`` goes by the decision (§2.2):

        {"mark", "exit" (id | None), "route", "group" (gid | None), "step" (exit | last_direct | last_block | chosen),
         "why" (words), "xray": {"outboundTag": "gx-<id>" | "direct" | "block" | "fail-block"}
                                (or {"balancerTag": "gw-vless"} — «по соединениям»), "missing" (exit:X not in the pool)}

    ``decision`` None — ``decision(st)`` (pins.json + the snapshot). The form before stage 1 ``(st, device, pool,
    alive)`` still works: a decision from pins.json with those exits alive. ``block`` by choice — the mark of the
    block and the xray ``block`` (a restart: the open connections are cut on purpose); the last step of a group —
    ``fail-block`` (no restart) or ``direct``."""
    route = model.route_of(st, device)
    via = route["via"]
    out = {"route": route, "exit": None, "group": None, "missing": False}
    if via == "block":
        out.update(mark=MARK_BLOCK, step="chosen", xray={"outboundTag": "block"},
                   why=tr("gw_exits.target.route_block"))
        return out
    if via == "direct":
        out.update(mark=MARK_DIRECT, step="chosen", xray={"outboundTag": "direct"},
                   why=tr("gw_exits.target.route_direct"))
        return out
    dec = _as_decision(st, decision, alive)
    gid, grp, missing = route_group(st, route, dec)
    eid = model.exit_of(route)
    out.update(group=gid, missing=missing)
    from . import policy as P
    if gid == P.MAIN and (grp or {}).get("per_connection") and not eid and not _spread_down(dec, gid, grp, device):
        out.update(mark=MARK_SPREAD, step="exit", xray={"balancerTag": VLESS_BALANCER},
                   why=tr("gw_exits.target.spread"))
        return out
    if eid and _foreign_exit(dec, eid) is False:
        step, ex, why = _last(grp, route), None, "last"      # a Russian exit on a foreign route: never used (§3.1)
    else:
        step, ex, why = _step_of(st, device, route, dec, gid, grp, eid, missing)
    if step == "exit" and ex and _foreign_exit(dec, ex) is False:
        # SEC-s1-q2 Б-1: a Russian exit (its line went to upstream-ru.txt, the policy not yet changed) is never the
        # target of a foreign group — the fit foreign ones instead, else its last step
        ex = _pick_role(dec, device, gid, "abroad")
        step, why = ("exit", "new") if ex else (_last(grp, route), "last")
    row = (dec.get("rows") or {}).get(ex) if ex else None
    if step == "exit" and row and row.get("mark"):
        out.update(mark=row["mark"], exit=ex, step="exit", xray={"outboundTag": row.get("tag") or tag(ex)})
        if eid and ex == eid:
            out["why"] = tr("gw_exits.target.exit", id=ex)
        elif eid:
            out["why"] = tr("gw_exits.target.evict", id=eid, group=P.label(grp), to=ex)
        else:
            out["why"] = tr("gw_exits.target.pool_exit", id=ex)
        return out
    step = step if step in ("last_direct", "last_block") else _last(grp, route)
    fall = tr("gw_exits.target.fall_direct" if step == "last_direct" else "gw_exits.target.fall_block")
    out.update(step=step, mark=MARK_DIRECT if step == "last_direct" else MARK_BLOCK,
               xray={"outboundTag": "direct" if step == "last_direct" else FAIL_BLOCK})
    out["why"] = tr("gw_exits.target.exit_down", id=eid, fall=fall) if eid else \
        tr("gw_exits.target.none_alive", fall=fall)
    return out



def ru_target(st, device, decision=None, is_ready=None):
    """Where the RUSSIAN traffic of ``device`` goes (SPEC-groups §2.8, Q2) — the ONE answer for the kernel (gw_rd /
    gw_rx), xray (the rules «напрямую» of the lists), the trace and the scheme:

        None — «Россия напрямую» does not act for it (``rules.apply.device_rd``: the lists, the consent ⚠РФ, the
               profile, active, not «block» by choice): no Russian rule at all;
        {"step": "direct", "mark": 0x201, "xray": {"outboundTag": "direct"}, "group": None} — ``ru_via`` direct;
        {"step": "exit", "mark": 256+N | 0x300+N, "xray": {"outboundTag": "gr-<id>"}, "group": R, "exit", "tier"};
        {"step": "last_direct" | "last_block", "mark": 0x201 | 0x2ff, "xray": direct | fail-block, "group": R}.

    The exit — the pin of the device in its Russian group R, else the fit ones by their order (§4.4); «снят» never.
    The fallback block of the FOREIGN group of the device changes nothing here (Б1). ``decision`` None —
    ``decision(st)`` with the exits of both files."""
    from ..rules import apply as rules_apply
    from . import policy as P
    if not rules_apply.device_rd(st, device, is_ready=is_ready):
        return None
    direct = {"step": "direct", "mark": MARK_DIRECT, "xray": {"outboundTag": "direct"}, "group": None}
    via = model.ru_via_of(st, device)["via"]
    if via == model.RU_VIA_DIRECT:
        return direct
    dec = _as_decision(st, decision if isinstance(decision, dict) else pool_fallback(roles=ROLES), None)
    grp = P.group(dec.get("policy") or P.load(st), via)
    if grp is None or grp.get("role") != "ru":          # gone meanwhile: «как сейчас» (ru_via_of, §2.5)
        return direct
    route = {"via": "group:" + via}
    step, ex, _why = _step_of(st, device, route, dec, via, grp, None, False)
    rows = dec.get("rows") or {}
    row = rows.get(ex) if ex else None
    if step == "exit" and not (row and row.get("role") == "ru"):
        # REV-s1-q2 П-4: an exit of another role in the group (a hand-edited policy) — the fit Russian ones instead
        ex = _pick_role(dec, device, via, "ru")
        row = rows.get(ex) if ex else None
        step = "exit" if ex else _last(grp, {})
    if step == "exit" and row and row.get("mark") and row.get("role") == "ru":
        pin = (((dec.get("groups") or {}).get(via) or {}).get("pins") or {}).get(device.get("id")) or {}
        tier = pin.get("tier") if pin.get("exit") == ex else None
        if tier is None:
            tier = next((t.get("id") for t in grp.get("tiers") or [] if ex in (t.get("exits") or [])), None)
        return {"step": "exit", "mark": row["mark"], "xray": {"outboundTag": row.get("tag") or tag(ex, "ru")},
                "group": via, "exit": ex, "tier": tier}
    step = step if step in ("last_direct", "last_block") else _last(grp, {})
    if step == "last_direct":
        return {"step": step, "mark": MARK_DIRECT, "xray": {"outboundTag": "direct"}, "group": via}
    return {"step": step, "mark": MARK_BLOCK, "xray": {"outboundTag": FAIL_BLOCK}, "group": via}

def _fall(route):
    return tr("gw_exits.target.fall_direct" if route["fail"] == "direct" else "gw_exits.target.fall_block")


def now_of(st, device, exits, alive=None, socks=False, decision=None):
    """Where the traffic of ``device`` goes NOW — the ONE answer for «Куда пойдёт трафик?» (kaskad.gw.trace), the scheme
    (its «сейчас», «закреплено», the list) and the counters (stand T-scheme-3.2.16, Н-1):

        {"target": "exit:<id>" | "pool" | "direct" | "block", "exit": id | None, "balancer": False | "xray" | "any",
         "exits": [ids], "dead": [ids of the route's unfit exits], "pool_live": [ids], "route": route,
         "group": gid | None, "step": exit | last_direct | last_block | chosen, "pinned": bool, "missing": bool}

    Stage 1 (§2.2): the kernel and xray (SOCKS5, VLESS: ``socks``) take the SAME exit — the pin of the device in the
    group of its route (``target``); «по соединениям» of «Основная» — the balancer of the good exits of its active tier.
    ``decision`` — the decision of the caller (one per request), else ``alive`` given — the form before stage 1, else
    ``decision(st)``. A device without an id (a new one) is pinned later: ``pool_live`` — the candidates of its tier."""
    from . import assign, policy as P
    dec = decision if decision is not None else _as_decision(st, exits if alive is not None else None, alive)
    route = model.route_of(st, device)
    via = route["via"]
    out = {"target": via, "exit": None, "balancer": False, "exits": [], "dead": [], "pool_live": [], "route": route,
           "group": None, "step": "chosen", "pinned": False, "missing": False}
    if via in ("block", "direct"):
        return out
    t = target(st, device, dec)
    out.update(group=t["group"], step=t["step"], missing=t["missing"])
    eid = model.exit_of(route)
    if eid and t["exit"] != eid:
        out["dead"] = [eid]                                    # the chosen exit is unfit (or gone): another one / last
    if t["mark"] == MARK_SPREAD:
        grp = P.group(dec.get("policy") or P.load(st), t["group"])
        cands = assign.spread_cands(dec.get("states") or {}, grp)   # the kernel's and xray's own (REV-s1-kt1a-2 П-1)
        out.update(target="pool", balancer="any", exits=list(cands))
        return out
    if t["step"] != "exit":
        out["target"] = "direct" if t["step"] == "last_direct" else "block"
        return out
    if not device.get("id") and not (eid and t["exit"] == eid):
        grp = P.group(dec.get("policy") or P.load(st), t["group"])
        cands = assign.spread_cands(dec.get("states") or {}, grp)   # the best class of the first tier with it
        out.update(target="pool", pool_live=list(cands), exit=cands[0] if len(cands) == 1 else None)
        if out["exit"]:
            out.update(target="exit:" + out["exit"], exits=[out["exit"]])
        return out
    g = ((dec.get("groups") or {}).get(t["group"]) or {})
    out["pinned"] = bool(((g.get("pins") or {}).get(device["id"]) or {}).get("exit") == t["exit"])
    out.update(target="exit:" + t["exit"], exit=t["exit"], exits=[t["exit"]])
    return out


def marks(st, decision=None, alive=None, leases=()):
    """{client ip: mark} for gw_route: static addresses of active devices on enabled inbounds
    (``model.live_ips``) + dynamic leases [(ip, device)] (IKEv2), each by ``target`` of the decision. Unknown
    addresses are not in the map: gw_pre blocks them. The form before stage 1 ``(st, pool, alive)`` still works."""
    return dict((ip, t["mark"]) for ip, t in targets(st, decision, alive, leases).items())


def targets(st, decision=None, alive=None, leases=()):
    """{client ip: target} of every address ``marks`` puts in gw_route (one ``target`` per device)."""
    dec = _as_decision(st, decision, alive)
    out = {}
    by_dev = {}
    kinds = model.enabled_kinds(st)
    for d in model.devices(st):
        if not model.device_active(st, d):
            continue
        t = target(st, d, dec)
        by_dev[d["id"]] = t
        for ip in model.live_ips(st, d, kinds):
            out[ip] = t
    for ip, d in leases:
        if not model.device_active(st, d):
            continue
        out[ip] = by_dev.get(d["id"]) or target(st, d, dec)
    return out


def direct_needed(st, policy=None):
    """Some active device of an inbound that uses the DNS of the gateway (everything but SOCKS5 and VLESS) may go out
    directly — only then dnsmasq gets a direct upstream (§2.6, С4): its route is «direct», or (not «block») its fail
    policy is «direct», or the group its route resolves to (``pool`` — «main», ``group:G`` — G, ``exit:E`` — the group
    of E) has ``fail = direct``. Static — by the settings, not by the state of the exits: dnsmasq is not touched when an
    exit falls. ``policy`` — the effective one (None: ``policy.load(st)``)."""
    from . import policy as P
    kinds = [k for k in model.enabled_kinds(st) if k not in model.XRAY_KINDS]
    pol = None
    for d in model.devices(st):
        if not model.device_active(st, d) or not any(k in kinds for k in d.get("access") or []):
            continue
        r = model.route_of(st, d)
        if r["via"] == "direct" or (r["via"] != "block" and r.get("fail") == "direct"):
            return True
        if r["via"] == "block":
            continue
        if pol is None:
            pol = policy if policy is not None else P.load(st)
        eid = model.exit_of(r)
        grp = P.group_of(pol, eid) if eid else P.group(pol, model.group_of_route(r) or P.MAIN)
        if grp is None or grp.get("role", "abroad") != "abroad":
            grp = P.group(pol, P.MAIN)
        if (grp or {}).get("fail") == "direct":
            return True
    return False


def pool_fallback(roles=("abroad",)):
    """The pool when the parser cannot be loaded (half an update of gokaskad, an old parser): ids, kinds
    by prefix and marks from the line numbers — what gw_route needs; no outbounds (xray keeps its
    config, nothing is probed). With the last snapshot the clients stay on their legs instead of all
    being blocked; a leg that died meanwhile still drops (table 256+N ends in ``unreachable``). ``roles`` — as
    ``pool``: the foreign exits by default, the Russian ones (upstream-ru.txt, numbers 201…) only when asked."""
    out, seen = [], set()
    ids = kept_ids()
    for role in (r for r in ROLES if r in roles):
        base = index_base(role)
        for idx, line in enumerate(pool_lines(role), base + 1):
            eid = pool_id(line, ids)
            e = {"id": eid, "index": idx, "tag": tag(eid, role), "kind": "bad", "proto": "", "role": role,
                 "mark": None, "iface": None, "table": None, "version": "", "name": "", "host": "", "outbound": None,
                 "line": line}
            _problem(e, "no_parser")
            if eid in seen:
                e["id"] = "%s-%d" % (eid, idx)          # as ``pool``: the same key twice — one exit
                e["tag"] = tag(e["id"], role)
                e["dup_of"] = eid
            else:
                seen.add(eid)
                top = RU_INDEX_BASE + RU_MAX if role == "ru" else RU_INDEX_BASE
                if line.startswith("awg://") and idx <= min(top, MAX_AWG_INDEX):
                    e.update(kind="awg", mark=AWG_MARK_BASE + idx, iface="kawg%d" % idx, table=AWG_MARK_BASE + idx)
                elif link_proto(line) and idx <= min(top, MAX_VLESS_INDEX):   # every xray scheme: «not ready»
                    e.update(kind=XRAY, proto=link_proto(line), mark=VLESS_MARK_BASE + idx)
            out.append(e)
    if "ru" in roles:
        ru_ports(out)
    return set_where(out)


def current(st, runner=None):
    """(exits, snapshot) for renderers (gw_route): never raises — an unreadable pool gives
    ``pool_fallback()`` with the last snapshot (a missing upstream.txt is an empty pool: every pool route
    falls to its fail policy, block by default). Both files (T15 Q4 — the «load_pool» of SPEC-groups §12.1): the
    xray exits of upstream-ru.txt have their TPROXY and probe ports in the plan and in gw_xports, as the config the
    collector draws has them; the targets never take a Russian exit for the pool (``usable``, the role guard)."""
    try:
        exits = pool(runner, roles=ROLES)
    except KaskadError:
        exits = pool_fallback(roles=ROLES)
    return exits, load_snapshot()


# ── the legs ───────────────────────────────────────────────────────────────

def _legs_lock():
    return os.path.join(paths.gw_run_dir(), "legs.lock")


def _heal_stamp():
    return os.path.join(paths.gw_run_dir(), "legs-heal.stamp")


def _ru_hold_path():
    return os.path.join(paths.egress_dir(), "awg", ".ru-hold")      # install.sh egress_ru_hold


def lines_hash(lines):
    """The fingerprint of the lines of a file of the pool — as install.sh egress_pool_hash takes it."""
    return hashlib.sha1("\n".join(lines).encode("utf-8", "surrogateescape")).hexdigest()[:16]


def ru_hold():
    """REV-s1-q4-2 С-1 (Д-1 when the apply fails): the mark install.sh writes BEFORE upstream-ru.txt gives a leg's place
    to another key — {"hash": the fingerprint of the file the legs wait for, "legs": ["kawgN", …], "lines": [N, …]};
    None — no mark. While it is there these legs never come up (install.sh egress_awg_leg_up, the start of their unit):
    the marks of the devices (gw_route) and the xray of the gateway may still lead the people of the old owner there."""
    raw = util.read_text(_ru_hold_path())
    if raw is None:
        return None
    rows = [r.strip() for r in raw.split("\n")]
    legs = [r for r in rows[1:] if re.fullmatch(r"kawg[0-9]+", r)]
    return {"hash": rows[0], "legs": legs, "lines": [int(r[4:]) for r in legs]}


LEGS_LOCK_WAIT = 5.0               # s: ru_hold_release waits this long for legs.lock, then tries again later
HOLD_ORPHAN_S = 120                # s: a mark of ANOTHER file goes only this old (SEC-s1-q2 П-2: a writer without flock
                                   # holds no lock of the pool — its live write is never taken for a cut-off one)
LEGS_LOCK_FD = "KASKAD_LEGS_LOCK_FD"    # install.sh egress_ru_apply_now: gw apply runs under ITS legs.lock


def _inherited_lock(path):
    """The descriptor of ``path`` the caller handed down (LEGS_LOCK_FD): install.sh holds legs.lock and runs gw apply
    under it — waiting for it here would only wait for ourselves. Taken only when it is the same file (device and
    inode) and the lock on it is ours (the same open file: flock succeeds at once). Never closed or unlocked here —
    the lock is the caller's. -> the descriptor or None."""
    raw = os.environ.get(LEGS_LOCK_FD, "")
    if not raw.isdigit():
        return None
    fd = int(raw)
    try:
        a, b = os.fstat(fd), os.stat(path)
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            return None
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return fd


@contextlib.contextmanager
def _legs_locked(wait):
    """legs.lock for a short read-check-write (REV-s1-q4-3 З-2): install.sh writes the mark under it. -> True — held
    (by us or by the caller that ran us), False — busy for ``wait`` seconds."""
    lock = _legs_lock()
    if _inherited_lock(lock) is not None:
        yield True
        return
    util.ensure_dir(os.path.dirname(lock), 0o700)      # /run/kaskad above it stays passable (nginx → the panel)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        end, held = time.monotonic() + wait, False
        while not held:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
            except OSError:
                if time.monotonic() >= end:
                    break
                time.sleep(0.1)
        yield held
    finally:
        os.close(fd)                                    # closing the descriptor drops the lock


def _hold_old(now=None):
    """The mark was written at least HOLD_ORPHAN_S ago (by its mtime; one «from the future» — a clock set back — is
    young)."""
    try:
        age = (time.time() if now is None else now) - os.stat(_ru_hold_path()).st_mtime
    except OSError:
        return False
    return age >= HOLD_ORPHAN_S


def _pool_lock_free():
    """The lock of the pool (install.sh egress_pool_locked, tofu.lock_path) taken WITHOUT waiting: nobody is writing
    the files of the pool. -> the descriptor (close it to let go) or None — busy."""
    p = paths.P("/run/kaskad/egress-pool.lock")
    try:
        os.makedirs(os.path.dirname(p), mode=0o711, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def ru_hold_release(pool_exits, why, wait=LEGS_LOCK_WAIT):
    """The mark goes when the table and the xray of the gateway were just applied by ``pool_exits`` (the caller says
    so) and its Russian lines are the file now — and the file the legs wait for (its fingerprint: install.sh writes
    the mark before the file, so a mark of the NEXT file never goes by the old one). All of it under legs.lock
    (REV-s1-q4-3 З-2): install.sh writes the mark under it, so a mark written meanwhile is never the one unlinked.
    Another fingerprint and the lock of the pool free (REV-s1-q4-3 З-1): the writer of that file was cut off between
    the mark and the file (Ctrl+C, SSH gone, a timeout, a reboot) — the file never came; the legs wait for nothing,
    the mark goes by the file there is. -> True — released (the legs may come up)."""
    if ru_hold() is None:
        return False
    with _legs_locked(wait) as held:
        if not held:
            return False
        hold = ru_hold()
        if hold is None:
            return False
        used = [e.get("line") for e in pool_exits or () if e.get("role") == "ru"]
        if used != pool_lines("ru"):
            return False
        pfd, orphan = None, False
        try:
            if lines_hash(pool_lines("ru")) != hold["hash"]:
                if not _hold_old():                             # written a moment ago: its file may still come
                    return False
                pfd = _pool_lock_free()
                if pfd is None or used != pool_lines("ru"):     # a writer is inside: the file may still come
                    return False
                orphan = True
            try:
                os.unlink(_ru_hold_path())
            except FileNotFoundError:
                pass
            except OSError:
                return False
        finally:
            if pfd is not None:
                os.close(pfd)
    if orphan:
        util.log_action("GW: the legs %s may come up — the change of the domestic keys they waited for was cut off "
                        "before its file was written; the gateway applied the file there is (%s)"
                        % (" ".join(hold["legs"]), why))
    else:
        util.log_action("GW: the legs %s may come up — the gateway applied their new numbers (%s)"
                        % (" ".join(hold["legs"]), why))
    return True


EGRESS_UNIT = "kaskad-egress.service"
HEAL_TIMEOUT = 180                  # a missing leg costs about a second to bring up; a pool has up to 100 keys


def _after(tok, key):
    """The word after ``key`` in a split line, or None."""
    try:
        return tok[tok.index(key) + 1]
    except (ValueError, IndexError):
        return None


def leg_problems(runner, legs):
    """{iface: [missing parts]} of AWG legs whose interface is up but whose policy routing is not whole.
    Parts: "rule" (``fwmark 256+N lookup 256+N``), "dev" (``default dev kawgN`` in table 256+N), "backstop"
    (``unreachable default metric 1000`` there). A restart of systemd-networkd (every upgrade of the systemd
    package, unless its 90-kaskad.conf is in place) removes the rule and the backstop and leaves the device
    route. What ``ip`` does not answer is not reported — nothing is repaired on a guess."""
    out = {}
    if not legs:
        return out
    res = runner.run(["ip", "-4", "rule", "show"])
    rules = res.out.splitlines() if res.ok else None
    for e in legs:
        miss = []
        if rules is not None:
            rx = re.compile(r"\bfwmark 0x%x\b.*\blookup %d\b" % (e["mark"], e["table"]))
            if not any(rx.search(ln) for ln in rules):
                miss.append("rule")
        rt = runner.run(["ip", "-4", "route", "show", "table", str(e["table"])])
        if rt.ok or "does not exist" in (rt.err or ""):     # an empty table: «FIB table does not exist»
            dev = back = False
            for ln in (rt.out if rt.ok else "").splitlines():
                tok = ln.split()
                if tok[:1] == ["default"] and _after(tok, "dev") == e["iface"]:
                    dev = True
                elif tok[:2] == ["unreachable", "default"] and _after(tok, "metric") == str(BACKSTOP_METRIC):
                    back = True
            miss += [part for part, ok in (("dev", dev), ("backstop", back)) if not ok]
        if miss:
            out[e["iface"]] = miss
    return out


def _parts_text(e, parts):
    t = e["table"]
    text = {"rule": "ip rule fwmark 0x%x lookup %d" % (e["mark"], t),
            "dev": "default dev %s table %d" % (e["iface"], t),
            "backstop": "unreachable default table %d metric %d" % (t, BACKSTOP_METRIC)}
    return ", ".join(text[p] for p in parts)


def _what(awg, missing, broken):
    """«kawg2 — не было интерфейса; kawg3 — нет ip rule …» for the log, in the order of the pool (the language of the
    moment: the caller is in ``util.journal_lang``)."""
    out = []
    for e in awg:
        if e["iface"] in missing:
            out.append(tr("gw_exits.legs.no_iface", iface=e["iface"]))
        elif e["iface"] in broken:
            out.append(tr("gw_exits.legs.missing", iface=e["iface"], parts=_parts_text(e, broken[e["iface"]])))
    return "; ".join(out)


def _age(path):
    """Age of ``path`` in seconds; no file — None. A file «from the future» (written while the clock ran ahead, then NTP
    set it back) — None too, as in egress_file_age of install.sh: otherwise the heal waits until the clock catches up with
    it (REV-336-heal-2 Б-1)."""
    try:
        age = _now() - os.stat(path).st_mtime
    except OSError:
        return None
    return age if age >= 0 else None


def _stamp(path, text):
    try:
        util.atomic_write(path, text, 0o600)
        os.utime(path, (_now(), _now()))
    except OSError:
        pass


def _untold(path):
    """The stamp of a heal that failed while the node was booting: its failure is not in the log yet."""
    return "boot" in (util.read_text(path) or "").split()


def ensure_legs(runner, exits, force=False):
    """Keep the AWG legs of the pool whole WITHOUT touching the working ones (the collector every 10 s; gw apply
    with ``force`` — no retry wait). A leg without its interface (amneziawg-go died) and a leg whose rule or
    routes are gone (a restart of systemd-networkd) -> ``gokaskad --egress-awg-heal``: it brings up just the
    missing legs, one by one, and puts back the routing of the broken ones in place. No other leg is recreated,
    the traffic of its clients does not drop (``--egress-awg-up`` recreates every leg — that is the start of
    «Прокси для API Telegram» as a whole, never the gateway's business). A heal that did not help is retried
    after HEAL_RETRY (unless ``force``); the first failure in a row is logged, a success always — right after a
    reboot (``boot.pending``) the legs are still starting under systemd: a failure then waits for the first retry
    after the boot to be logged, a success is a calm line. Nothing while another caller holds the legs' lock.
    All legs whole -> nothing runs, ``force`` or not. Since 3.2 every leg
    is its own unit kaskad-leg@kawgN (install.sh): the heal only runs ``systemctl restart`` of the units — no
    leg is a child of the collector, of ``gw apply`` or of the watchdog oneshot, and the xray of the bot
    (kaskad-egress.service) no longer brings the legs up or down, so a starting or crash-looping egress is no
    reason to wait. Returns {"missing", "broken", "healed", "ok"}: ``healed`` — the heal ran, ``ok`` — the legs
    are whole afterwards (as far as ``ip`` tells)."""
    out = {"missing": [], "broken": [], "healed": False, "ok": True}
    held = set((ru_hold() or {}).get("legs") or ())         # REV-s1-q4-2 С-1: closed on purpose until the apply
    awg = [e for e in exits if e["kind"] == "awg" and e["iface"] not in held]
    if not awg:
        return out
    names = links(runner)
    present = [e for e in awg if e["iface"] in names]
    out["missing"] = [e["iface"] for e in awg if e["iface"] not in names]
    broken = leg_problems(runner, present)
    out["broken"] = [e["iface"] for e in present if e["iface"] in broken]
    if not out["missing"] and not broken:
        return out
    out["ok"] = False
    lock = _legs_lock()
    util.ensure_dir(os.path.dirname(lock), 0o700)      # /run/kaskad above it stays passable (nginx → the panel)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:                                # one heal at a time (collector vs. gw apply)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return out
        stamp = _heal_stamp()
        age = _age(stamp)
        if not force and age is not None and age < HEAL_RETRY:
            return out
        res = runner.run([_script(runner), "--egress-awg-heal"], timeout=HEAL_TIMEOUT)
        out["healed"] = True
        with util.journal_lang():                       # the words of the journal: the language of the server
            what = _what(awg, out["missing"], broken)
        now_up = links(runner)
        still = [e["iface"] for e in awg if e["iface"] not in now_up]
        still += list(leg_problems(runner, [e for e in awg if e["iface"] in now_up]))
        booting = boot.pending()
        if still:
            untold = age is None or _untold(stamp)
            if untold and not booting:
                util.log_tr("gw_exits.log.heal_failed", rc=res.rc, what=what, still=", ".join(still))
            _stamp(stamp, "%d\n%s" % (res.rc, "boot\n" if untold and booting else ""))
        else:
            if age is not None:
                try:
                    os.unlink(stamp)
                except OSError:
                    pass
            if booting:
                util.log_tr("gw_exits.log.boot_up", legs=", ".join(out["missing"] + out["broken"]))
            else:
                util.log_tr("gw_exits.log.healed", what=what)
        out["ok"] = not still
        return out
    finally:
        os.close(fd)                        # closing the descriptor drops the lock

"""Traffic engine: state.json -> nftables table ``ip kaskad`` (work package 1 implements this file).

CONTRACT — other modules (migration, CLI) call exactly these functions; keep the signatures.
Full design: docs/k3/SPEC-wave1.md, «Движок трафика».

Table layout (family ``ip``, IPv4 only)::

    table ip kaskad {
        counter fwd_tcp_443 { packets 0 bytes 0 }          # one named counter per forward (traffic)
        chain fwd_pre   { type nat hook prerouting priority -101; policy accept;
                          fib daddr type local iifname != "lo" jump fwd_dnat }
        chain fwd_dnat  { tcp dport 443 ct mark set ct mark or 0x4b000000 dnat to 203.0.113.5:443
                          comment "kaskad:443:tcp" }
        chain fwd_count { type filter hook forward priority -5; policy accept;
                          meta l4proto tcp ct original proto-dst 443 ct status dnat
                              ct mark set ct mark or 0x4b000000 counter name "fwd_tcp_443" return }
        chain fwd_post  { type nat hook postrouting priority 101; policy accept;
                          ct status dnat ct mark and 0xff000000 == 0x4b000000 masquerade }
    }

Why these details:
  * ``fib daddr type local`` — DNAT only for packets addressed to this server. 2.x DNAT had no such
    match and hijacked transit traffic (Docker containers, gateway clients in wave 2) to the port.
  * priority -101 — one below iptables ``nat PREROUTING`` (-100): a Kaskad forward deterministically
    wins over a foreign iptables DNAT on the same port (equal priorities run in registration order).
  * a NAT chain sees only the first packet of a connection, so traffic is counted in ``fwd_count``
    (forward hook, every packet, both directions) by the ORIGINAL destination port of a DNATed
    connection (``ct status dnat``: gateway clients of wave 2 carry the K mark too, but are not
    DNATed — without it their HTTPS would land in the counter of a :443 forward).
  * the count rule also SETS the K mark: connections opened before the switch from iptables went
    through the old DNAT and have no mark; under a FORWARD DROP without a general ESTABLISHED accept
    (Docker) the compat rule would cut them once the old FORWARD pairs are gone (found by WP2).
    It marks ANY DNATed connection whose original port is a forward's port — a foreign DNAT on the
    same proto/port would be marked (and counted) too, but that is a port conflict anyway (§3.6).
  * fwd_post at priority 101 (srcnat + 1): iptables nat POSTROUTING (100) runs first, so a foreign
    SNAT there (to a pinned source address) keeps winning, as with 2.x's kaskad:masq that was the
    last rule of POSTROUTING. NAT is set up once per connection: the first chain that does it wins.
  * ct mark top byte 0x4b ("K") marks Kaskad's connections: masquerade only them, and the single
    iptables compat rule (compat.py) accepts only them.

Chains/sets/maps whose names start with ``gw_`` belong to the gateway (wave 2). The gateway renders
them through a SECTION (``kaskad.gw.net.SECTION``, imported lazily — it imports this module): when it
is active (gateway enabled in state) render_full / render_update / verify / apply / restore carry its
part too, so one table, one transaction and one boot file hold both. When it is not active, foreign
``gw_*`` objects in the live table are left alone, as before.

Implementation notes:
  * Every nft construct is spelled in ONE place — the "nft spelling" block below (HOOKS, the
    ``_rule_*`` helpers, STDIN, OR/AND). probe_support() checks exactly these constructs with
    ``nft -c``; if a real system disagrees, that block is the only thing to change. The render_full
    and render_update texts are byte for byte the ones verified on Debian 12 (6.1, nft 1.0.6) and
    Ubuntu 24.04 (6.8, nft 1.0.9): tests/fixtures/nft/real-full.nft, real-update.nft.
  * read_live parses the JSON those systems print (tests/fixtures/nft/real-*-list.json); it only
    relies on rule comments, dnat addr/port, the jump target, named counter references and the
    masquerade statement (nft prints the masked ct mark matches as prefixes — not used here).
  * Forwards are validated (state.normalize_forward) before they reach a script: only IPv4
    addresses, ports 1-65535 and tcp/udp ever get into nft text — no user string (names) does.
  * apply() keeps the live table (counters survive). Only if nft refuses the incremental script
    (someone changed a base chain by hand) AND the table holds nothing of the gateway (``gw_*``)
    it recreates the table from render_full() with the live counter values.
  * Nothing here takes the state lock: callers (forwards, netcheck, CLI, migration) hold it.
"""

import importlib
import json
import os
import re
import sys

from . import __version__, paths, state, util
from .errors import KaskadError
from .i18n import tr

TABLE_FAMILY = "ip"
TABLE_NAME = "kaskad"
TABLE = TABLE_FAMILY + " " + TABLE_NAME
CHAINS = ("fwd_pre", "fwd_dnat", "fwd_count", "fwd_post")
GATEWAY_PREFIX = "gw_"
# Other parts of table ip kaskad, each a module with ``SECTION`` (active, full_lines, full_extra,
# update_lines, verify, extra_tables). Missing modules are skipped (the gateway may not be shipped).
SECTION_MODULES = ("kaskad.gw.net",)

PROBE_TABLE = "kaskad_probe"          # probe_support checks a twin table under this name (nft -c only)
MIN_NFT = (0, 9, 3)
MIN_KERNEL = (4, 18)                  # iptables nat and nftables nat coexist since 4.18
SYSCTL_FILE = "/etc/sysctl.d/90-kaskad.conf"
SYSCTL_TEXT = "# Kaskad 3: port forwards need IPv4 forwarding\nnet.ipv4.ip_forward=1\n"
OS_RELEASE = "/etc/os-release"

# ── nft spelling: every construct of the table lives here ──────────────────
NFT = "nft"
STDIN = "-"                           # ``nft -f -`` reads the script from stdin ("/dev/stdin" also works)
OR = "or"                             # bit operators as words; ``nft list`` prints them as | and &
AND = "and"
MARK = "0x%08x" % state.MARK          # 0x4b000000
MARK_MASK = "0x%08x" % state.MARK_MASK  # 0xff000000
HOOKS = {
    "fwd_pre": "type nat hook prerouting priority -101; policy accept;",
    "fwd_count": "type filter hook forward priority -5; policy accept;",
    "fwd_post": "type nat hook postrouting priority 101; policy accept;",
}


def _rule_jump():
    return 'fib daddr type local iifname != "lo" jump fwd_dnat'


def _rule_dnat(f):
    return ('%s dport %d ct mark set ct mark %s %s dnat to %s:%d comment "kaskad:%d:%s"'
            % (f["proto"], f["port"], OR, MARK, f["to"], f["to_port"], f["port"], f["proto"]))


def _rule_count(f):
    return ('meta l4proto %s ct original proto-dst %d ct status dnat ct mark set ct mark %s %s '
            'counter name "%s" return'
            % (f["proto"], f["port"], OR, MARK, counter_name(f["proto"], f["port"])))


def _rule_masq():
    return "ct status dnat ct mark %s %s == %s masquerade" % (AND, MARK_MASK, MARK)


def _chain_rules(fwds):
    return (("fwd_pre", [_rule_jump()]),
            ("fwd_dnat", [_rule_dnat(f) for f in fwds]),
            ("fwd_count", [_rule_count(f) for f in fwds]),
            ("fwd_post", [_rule_masq()]))


# Twin forwards of the probe table: one tcp, one udp, so every rule form is checked.
PROBE_FWDS = (
    {"proto": "tcp", "port": 1, "to": "192.0.2.1", "to_port": 1},
    {"proto": "udp", "port": 2, "to": "192.0.2.1", "to_port": 2},
)

_FWD_COUNTER = re.compile(r"^fwd_(tcp|udp)_(\d+)$")
_COMMENT = re.compile(r"^kaskad:(\d+):(tcp|udp)$")


# ── forwards of a state ────────────────────────────────────────────────────

def counter_name(proto, port):
    """Named counter of a forward: ``fwd_<proto>_<port>``."""
    return "fwd_%s_%d" % (proto, int(port))


def _forwards(st):
    """Validated, sorted forwards of ``st``. Validation is what keeps nft scripts injection-free."""
    seen = set()
    out = []
    for f in (st or {}).get("forwards") or []:
        nf = state.normalize_forward(f)
        k = (nf["proto"], nf["port"])
        if k in seen:
            raise KaskadError(tr("nft.fwd_twice", proto=k[0], port=k[1]))
        seen.add(k)
        out.append(nf)
    return state.sort_forwards(out)


def _desired(fwds):
    return dict(((f["proto"], f["port"]), (f["to"], f["to_port"])) for f in fwds)


def _kkey(k):
    return (k[1], k[0])


def _kstr(k):
    return "%s:%d" % k


def _count(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return n if n >= 0 else 0


def _counter_sort(name):
    m = _FWD_COUNTER.match(name)
    return (int(m.group(2)), m.group(1)) if m else (1 << 20, name)


# ── sections (the gateway part of the same table) ──────────────────────────

def sections():
    """Section objects of the other parts of the table (lazy import: they import this module)."""
    out = []
    for modname in SECTION_MODULES:
        try:
            mod = importlib.import_module(modname)
        except ModuleNotFoundError as e:
            parts = modname.split(".")
            if e.name not in [".".join(parts[:i]) for i in range(1, len(parts) + 1)]:
                raise
            continue
        sec = getattr(mod, "SECTION", None)
        if sec is not None:
            out.append(sec)
    return out


def _active(st):
    return [s for s in sections() if s.active(st)]


# ── rendering ──────────────────────────────────────────────────────────────

def _table_text(name, fwds, counters, inner=()):
    # byte for byte the form verified on Debian 12 (nft 1.0.6) and Ubuntu 24.04 (nft 1.0.9):
    # tests/fixtures/nft/real-full.nft (``inner`` — lines of active sections, after the forwards)
    t = "%s %s" % (TABLE_FAMILY, name)
    out = ["table " + t, "delete table " + t, "table %s {" % t]
    counters = counters or {}
    for f in fwds:
        cn = counter_name(f["proto"], f["port"])
        c = counters.get(cn) or {}
        out += ["\tcounter %s {" % cn,
                "\t\tpackets %d bytes %d" % (_count(c.get("packets")), _count(c.get("bytes"))),
                "\t}"]
    for chain, rules in _chain_rules(fwds):
        out.append("\tchain %s {" % chain)
        if chain in HOOKS:
            out.append("\t\t" + HOOKS[chain])
        out.extend("\t\t" + r for r in rules)
        out.append("\t}")
    out.extend(inner)
    out.append("}")
    return "\n".join(out) + "\n"


def _full_text(st, fwds, counters, ctx=None):
    """The table with the active sections inside, then the sections' own tables (ip6 kaskad6)."""
    secs = _active(st)
    inner = []
    for s in secs:
        inner.extend(s.full_lines(st, ctx=ctx))
    text = _table_text(TABLE_NAME, fwds, counters, inner)
    for s in secs:
        text += s.full_extra(st, ctx=ctx)
    return text


def render_full(st, counters=None):
    """Complete, deterministic table text for /etc/kaskad/net/kaskad.nft (applied at boot with
    ``nft -f``). Starts with ``table ip kaskad`` + ``delete table ip kaskad`` so applying it always
    recreates the table atomically. ``counters`` = {counter_name: {"packets": int, "bytes": int}}
    restores saved values into the counter declarations. Active sections (the gateway) are inside the
    same table; their extra tables follow, each with the same recreate prologue."""
    return _full_text(st, _forwards(st), counters)


def render_update(st, live, ctx=None):
    """Transaction script that brings the LIVE table (``read_live`` result, or None if absent) to
    ``st`` WITHOUT resetting counters of forwards that stay: create missing table/chains, flush the
    four fwd_* chains, add missing counters, re-add rules, delete counters of removed forwards
    (after the flush, in the same transaction). Counters that are not ``fwd_<proto>_<port>``
    (for example ``gw_*``) are never deleted. Active sections append their lines (same transaction);
    ``ctx`` — a dict one apply shares between script, verification and file, so a section computes its
    run-time content (the gateway's route elements) once."""
    fwds = _forwards(st)
    have = set(((live or {}).get("counters") or {}).keys())
    want = [counter_name(f["proto"], f["port"]) for f in fwds]
    out = ["add table " + TABLE]
    for chain in CHAINS:
        if chain in HOOKS:
            out.append("add chain %s %s { %s }" % (TABLE, chain, HOOKS[chain]))
        else:
            out.append("add chain %s %s" % (TABLE, chain))
    for chain in CHAINS:
        out.append("flush chain %s %s" % (TABLE, chain))
    for cn in want:
        if cn not in have:
            out.append("add counter %s %s" % (TABLE, cn))
    for chain, rules in _chain_rules(fwds):
        for r in rules:
            out.append("add rule %s %s %s" % (TABLE, chain, r))
    for cn in sorted(have, key=_counter_sort):
        if _FWD_COUNTER.match(cn) and cn not in want:
            out.append("delete counter %s %s" % (TABLE, cn))
    for s in _active(st):
        out.extend(s.update_lines(st, live, ctx=ctx))
    return "\n".join(out) + "\n"


# ── reading the live table ─────────────────────────────────────────────────

def _err_text(res):
    if res.rc == 127:
        return tr("nft.no_command", cmd=res.argv[0] if res.argv else "?")
    return util.redact((res.err or res.out or "").strip())


def _short(text, limit=400):
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    s = " | ".join(lines[:4])
    return s if len(s) <= limit else s[:limit - 1] + "…"


def _absent(res):
    return res.rc not in (0, 127) and "No such file or directory" in ((res.err or "") + (res.out or ""))


def read_live(runner):
    """Parse ``nft -j list table ip kaskad``. Returns None if the table does not exist, else
    ``{"forwards": {("tcp", 443): ("203.0.113.5", 443), ...},
       "counters": {"fwd_tcp_443": {"packets": int, "bytes": int}, ...},
       "chains": ["fwd_pre", ...], "has_masquerade": bool, "has_jump": bool}``.
    Forwards are read from the fwd_dnat rules (comment ``kaskad:<port>:<proto>`` + dnat addr/port).
    Extra keys: ``sets`` (set/map names), ``counted`` (counter names referenced in fwd_count),
    ``anomalies`` (fwd_dnat rules that are not Kaskad's or contradict their comment), and for the
    sections: ``chain_info`` {chain: {type, hook, prio, policy}}, ``set_info`` {name: {kind: set|map,
    type, map, flags, elem}}, ``rules`` {chain: [expr lists]} of the chains that are not fwd_*.
    Raises KaskadError if nft is missing or fails for another reason."""
    res = runner.run([NFT, "-j", "list", "table", TABLE_FAMILY, TABLE_NAME])
    if not res.ok:
        if _absent(res):
            return None
        raise KaskadError(tr("nft.read_failed", error=_short(_err_text(res))))
    try:
        doc = json.loads(res.out)
    except ValueError as e:
        raise KaskadError(tr("nft.not_json", error=e))
    return parse_live(doc)


def _port_value(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if 1 <= v <= 65535 else None
    if isinstance(v, str) and v.isdigit():
        n = int(v)
        return n if 1 <= n <= 65535 else None
    return None


def _counter_ref(stmt):
    """Name of a named counter in a rule statement: ``{"counter": "fwd_tcp_443"}``."""
    if not isinstance(stmt, dict) or "counter" not in stmt:
        return None
    v = stmt["counter"]
    if isinstance(v, str):
        return v
    if isinstance(v, dict) and isinstance(v.get("name"), str):
        return v["name"]
    return None


def _parse_dnat_rule(rule, res):
    exprs = rule.get("expr") or []
    comment = rule.get("comment") or ""
    m = _COMMENT.match(comment) if isinstance(comment, str) else None
    key_c = (m.group(2), int(m.group(1))) if m else None
    key_m = None
    target = None
    for e in exprs:
        if not isinstance(e, dict):
            continue
        mt = e.get("match")
        if isinstance(mt, dict):
            left = mt.get("left")
            pl = left.get("payload") if isinstance(left, dict) else None
            if isinstance(pl, dict) and pl.get("field") == "dport" and pl.get("protocol") in ("tcp", "udp") \
                    and mt.get("op", "==") == "==":
                port = _port_value(mt.get("right"))
                if port:
                    key_m = (pl["protocol"], port)
        d = e.get("dnat")
        if isinstance(d, dict):
            addr = d.get("addr")
            port = _port_value(d.get("port"))
            if isinstance(addr, str) and port:
                target = (addr, port)
    where = tr("nft.rule_where", handle=rule.get("handle", "?"))
    key = key_c or key_m
    if key is None or target is None:
        res["anomalies"].append(tr("nft.not_kaskad", where=where))
    elif key_c and key_m and key_c != key_m:
        res["anomalies"].append(tr("nft.label_mismatch", where=where, label=_kstr(key_c), rule=_kstr(key_m)))
    elif key in res["forwards"]:
        res["anomalies"].append(tr("nft.twice_in_table", fwd=_kstr(key)))
    else:
        res["forwards"][key] = target


def parse_live(doc):
    """``nft -j list table ip kaskad`` document -> the dict described in read_live."""
    items = doc.get("nftables") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise KaskadError(tr("nft.bad_format"))
    res = {"forwards": {}, "counters": {}, "chains": [], "sets": [], "has_masquerade": False,
           "has_jump": False, "counted": [], "anomalies": [], "chain_info": {}, "set_info": {}, "rules": {}}
    counted = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        c = it.get("counter")
        if isinstance(c, dict):
            if c.get("table", TABLE_NAME) == TABLE_NAME and isinstance(c.get("name"), str):
                res["counters"][c["name"]] = {"packets": _count(c.get("packets")), "bytes": _count(c.get("bytes"))}
            continue
        ch = it.get("chain")
        if isinstance(ch, dict):
            if isinstance(ch.get("name"), str):
                res["chains"].append(ch["name"])
                res["chain_info"][ch["name"]] = dict((k, ch.get(k)) for k in ("type", "hook", "prio", "policy"))
            continue
        for kind in ("set", "map"):
            s = it.get(kind)
            if isinstance(s, dict) and isinstance(s.get("name"), str):
                res["sets"].append(s["name"])
                elem = s.get("elem")
                res["set_info"][s["name"]] = {"kind": kind, "type": s.get("type"), "map": s.get("map"),
                                              "flags": s.get("flags"), "elem": elem if isinstance(elem, list) else []}
        r = it.get("rule")
        if not isinstance(r, dict):
            continue
        chain = r.get("chain")
        exprs = [e for e in (r.get("expr") or []) if isinstance(e, dict)]
        if chain == "fwd_pre":
            for e in exprs:
                j = e.get("jump")
                if isinstance(j, dict) and j.get("target") == "fwd_dnat":
                    res["has_jump"] = True
        elif chain == "fwd_dnat":
            _parse_dnat_rule(r, res)
        elif chain == "fwd_count":
            for e in exprs:
                name = _counter_ref(e)
                if name:
                    counted.add(name)
        elif chain == "fwd_post":
            if any("masquerade" in e for e in exprs):
                res["has_masquerade"] = True
        elif isinstance(chain, str):
            res["rules"].setdefault(chain, []).append(exprs)
    res["counted"] = sorted(counted, key=_counter_sort)
    return res


def verify(st, live, ctx=None):
    """Differences between the live table and ``st`` (empty list = in sync): the forwards set,
    jump, masquerade, a counter per forward, plus anomalies found by parse_live, plus what the active
    sections find in their part (the gateway)."""
    if live is None:
        return [tr("nft.no_table")]
    want = _desired(_forwards(st))
    got = live.get("forwards") or {}
    probs = []
    for k in sorted(want, key=_kkey):
        if k not in got:
            probs.append(tr("nft.missing_fwd", fwd=_kstr(k)))
        elif tuple(got[k]) != want[k]:
            probs.append(tr("nft.wrong_target", fwd=_kstr(k), ip=got[k][0], port=got[k][1], want_ip=want[k][0],
                             want_port=want[k][1]))
    for k in sorted(got, key=_kkey):
        if k not in want:
            probs.append(tr("nft.extra_fwd", fwd=_kstr(k), ip=got[k][0], port=got[k][1]))
    if not live.get("has_jump"):
        probs.append(tr("nft.no_jump"))
    if not live.get("has_masquerade"):
        probs.append(tr("nft.no_masquerade"))
    counters = live.get("counters") or {}
    for k in sorted(want, key=_kkey):
        cn = counter_name(*k)
        if cn not in counters:
            probs.append(tr("nft.no_counter", name=cn))
    probs.extend(live.get("anomalies") or [])
    for s in _active(st):
        probs.extend(s.verify(st, live, ctx=ctx))
    return probs


def _has_gateway(live):
    names = list(live.get("chains") or []) + list(live.get("sets") or []) + list((live.get("counters") or {}).keys())
    return any(n.startswith(GATEWAY_PREFIX) for n in names)


# ── changing the table ─────────────────────────────────────────────────────

def check(script, runner):
    """``nft -c -f -`` with the script on stdin. Returns (ok: bool, error_text: str)."""
    res = runner.run([NFT, "-c", "-f", STDIN], input=script)
    return res.ok, ("" if res.ok else _err_text(res))


def ensure_ip_forward(runner):
    """``net.ipv4.ip_forward=1``: if it is 0 — ``sysctl -w`` and /etc/sysctl.d/90-kaskad.conf.
    Returns (changed: bool, warning: str) — never raises: the table is already in place."""
    r = runner.run(["sysctl", "-n", "net.ipv4.ip_forward"])
    if r.ok and r.out.strip() == "1":
        return False, ""
    w = runner.run(["sysctl", "-w", "net.ipv4.ip_forward=1"])
    warn = "" if w.ok else tr("nft.ip_forward_failed", error=_short(_err_text(w)))
    try:
        util.atomic_write(paths.P(SYSCTL_FILE), SYSCTL_TEXT, 0o644)
    except OSError as e:
        warn = warn or tr("nft.write_failed", path=SYSCTL_FILE, error=e)
    return w.ok, warn


def _write_files(st, fwds, counters, ctx=None):
    """kaskad.nft (render_full + counters, with the active sections) and counters.json, both atomic 0600."""
    keep = {}
    for f in fwds:
        cn = counter_name(f["proto"], f["port"])
        c = (counters or {}).get(cn) or {}
        keep[cn] = {"packets": _count(c.get("packets")), "bytes": _count(c.get("bytes"))}
    try:
        util.atomic_write(paths.nft_file(), _full_text(st, fwds, keep, ctx), 0o600)
        util.atomic_write(paths.counters_file(),
                          util.json_text({"saved": util.now_iso(), "counters": keep}), 0o600)
    except OSError as e:
        raise KaskadError(tr("nft.write_failed", path=paths.nft_file(), error=e))
    return keep


# Which version of Kaskad applied the live table last (/etc/kaskad/net/applied.json). The watchdog tells a drift right
# after an update (the new code wants rules the old one did not put — planned) from a breakdown (T-N5 3.2.19-r2, Н-6:
# «NET repair: в цепочке gw_pre правил 24, а нужно 25 … — применил настройки заново» read like a failure).
def applied_file():
    return os.path.join(paths.net_dir(), "applied.json")


def applied_version():
    """The version that applied the table last; None — none recorded (a version before 3.2.20 applied it)."""
    try:
        doc = json.loads(util.read_text(applied_file()) or "null")
    except ValueError:
        return None
    v = doc.get("version") if isinstance(doc, dict) else None
    return v if isinstance(v, str) and v else None


def _note_applied():
    try:
        if applied_version() != __version__:
            util.atomic_write(applied_file(), util.json_text({"version": __version__, "at": util.now_iso()}), 0o600)
    except OSError:
        pass


def saved_counters():
    """Counters stored by the last apply/save_counters (counters.json), {} if none."""
    raw = util.read_text(paths.counters_file())
    if raw is None:
        return {}
    try:
        doc = json.loads(raw)
    except ValueError:
        return {}
    c = doc.get("counters") if isinstance(doc, dict) else None
    return c if isinstance(c, dict) else {}


def apply(st, runner, write_file=True):
    """Make the live table match ``st["forwards"]``: render_update -> check -> ``nft -f -`` ->
    read_live -> verify the live forwards equal the state. On success (and write_file) rewrite
    /etc/kaskad/net/kaskad.nft with render_full + current counters. Returns
    ``{"ok": True, "added": [...], "removed": [...], "changed": [...]}`` (lists of "proto:port").
    Raises KaskadError (code ERROR) if nft rejects the script or the verification fails; the live
    table is then left as nft left it (the transaction is atomic: either all or nothing).

    Extra result keys: ``recreated`` (the table was rebuilt, see the module notes) and
    ``warnings`` (ip_forward could not be switched on). ``KaskadError.data["stage"]`` is
    "check", "apply" or "verify"."""
    fwds = _forwards(st)
    want = _desired(fwds)
    live = read_live(runner)
    ctx = {"runner": runner}            # one set of gateway elements for script, check and file (the runner: the
                                        # sections may look at their own tables — the gateway at gw_pre6, F11)
    script = render_update(st, live, ctx)
    ok, err = check(script, runner)
    recreated = False
    # recreate only if nothing foreign would be lost: no gw_* objects, or the gateway section is active
    # (then render_full carries the whole gateway part)
    if not ok and live is not None and (not _has_gateway(live) or _active(st)):
        # «live» — the table it replaces: a section keeps what may not shrink yet (gw_xports, SEC-s1-b1 П-2)
        full = _full_text(st, fwds, live.get("counters"), dict(ctx, live=live))
        if check(full, runner)[0]:
            script, ok, recreated = full, True, True
    if not ok:
        raise KaskadError(tr("nft.check_rejected", error=_short(err)),
                          data={"stage": "check", "detail": _short(err)})
    res = runner.run([NFT, "-f", STDIN], input=script)
    if not res.ok:
        err = _err_text(res)
        raise KaskadError(tr("nft.apply_failed", error=_short(err)), data={"stage": "apply", "detail": _short(err)})
    after = read_live(runner)
    problems = verify(st, after, ctx)
    if problems:
        raise KaskadError(tr("nft.verify_failed", problems="; ".join(problems)),
                          data={"stage": "verify", "mismatch": problems})
    if write_file:
        _write_files(st, fwds, after.get("counters"), ctx)
    _note_applied()
    _, warn = ensure_ip_forward(runner)
    before = (live or {}).get("forwards") or {}
    return {
        "ok": True,
        "added": [_kstr(k) for k in sorted(want, key=_kkey) if k not in before],
        "removed": [_kstr(k) for k in sorted(before, key=_kkey) if k not in want],
        "changed": [_kstr(k) for k in sorted(want, key=_kkey) if k in before and tuple(before[k]) != want[k]],
        "recreated": recreated,
        "warnings": [warn] if warn else [],
    }


def restore(runner):
    """Boot/repair path: ``nft -f /etc/kaskad/net/kaskad.nft``; if the file is missing, render it
    from state first. Returns True on success.

    A file nft refuses (damaged by hand) is re-rendered from state (with the counters of
    counters.json) and loaded once more. The result may still differ from state if the file was
    stale: netcheck/apply settle that."""
    path = paths.nft_file()
    if not os.path.exists(path):
        st = state.load()
        _write_files(st, _forwards(st), saved_counters())
    if runner.run([NFT, "-f", path]).ok:
        return True
    st = state.load()
    text = _full_text(st, _forwards(st), saved_counters())
    if util.read_text(path) == text:
        return False
    try:
        util.atomic_write(path, text, 0o600)
    except OSError:
        return False
    return runner.run([NFT, "-f", path]).ok


def delete_table(runner):
    """Remove ``table ip kaskad`` if it exists (rollback/uninstall). Never fails if absent.
    Returns True if a table was deleted. The sections' own tables (``ip6 kaskad6`` of the gateway)
    go too, silently if absent."""
    res = runner.run([NFT, "delete", "table", TABLE_FAMILY, TABLE_NAME])
    if res.ok:
        deleted = True
    elif res.rc == 127 or _absent(res):
        deleted = False
    else:
        raise KaskadError(tr("nft.delete_failed", error=_short(_err_text(res))))
    if res.rc != 127:
        for s in sections():
            for family, name in s.extra_tables():
                runner.run([NFT, "delete", "table", family, name])
    return deleted


def save_counters(runner):
    """Read live counters and store them into the counter declarations of kaskad.nft (and
    counters.json). Returns the counters dict. No-op returning {} if the table is absent."""
    live = read_live(runner)
    if live is None:
        return {}
    st = state.load()
    return _write_files(st, _forwards(st), live.get("counters"))


# ── can engine nft work here? ──────────────────────────────────────────────

def _vtuple(s):
    out = []
    for part in str(s or "").split("."):
        m = re.match(r"\d+", part)
        if not m:
            break
        out.append(int(m.group(0)))
    return tuple(out)


def iptables_backend(runner):
    """"nf_tables", "legacy" or "none" (no iptables binary) — from ``iptables -V``."""
    if not runner.which("iptables"):
        return "none"
    r = runner.run(["iptables", "-V"])
    if not r.ok:
        return "none"
    m = re.search(r"\((nf_tables|legacy)\)", r.out or "")
    return m.group(1) if m else "legacy"


def read_os_release():
    """{"id", "version_id", "pretty"} from /etc/os-release (empty strings if missing)."""
    raw = util.read_text(paths.P(OS_RELEASE))
    vals = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        vals[k.strip()] = v
    return {"id": vals.get("ID", "").lower(), "version_id": vals.get("VERSION_ID", ""),
            "pretty": vals.get("PRETTY_NAME", "")}


def os_supported(osr):
    """Owner's decision (2026-10-04): engine nft only on Debian 12+ and Ubuntu 24.04+."""
    v = _vtuple(osr.get("version_id"))
    if not v:
        return False
    if osr.get("id") == "debian":
        return v[0] >= 12
    if osr.get("id") == "ubuntu":
        return v[:2] >= (24, 4)
    return False


def probe_support(runner):
    """Can engine nft work here? Returns ``{"supported": bool, "reason": str, "nft": "1.0.6",
    "kernel": "6.1.0-...", "backend": "nf_tables|legacy|none", "firewalld": bool}``. Supported
    needs: nft binary, a successful ``nft -c`` of a probe table using every construct of
    render_full (fib, named counter, ct mark bit ops, dnat to ip:port, masquerade, comments), no
    active firewalld, python >= 3.8. Read-only.

    Also required: Debian 12+ or Ubuntu 24.04+ (/etc/os-release), nft >= 0.9.3, kernel >= 4.18,
    JSON output of nft (``nft -j list tables``). Extra keys: ``os`` {"id", "version_id", "pretty"},
    ``python`` and ``code`` — why it is unsupported, for programs: "os", "python", "no_nft"
    (the nftables package is missing — installing it may fix this), "nft_broken", "old_nft",
    "old_kernel", "firewalld", "no_json", "check_failed"; "" when supported."""
    osr = read_os_release()
    info = {"supported": False, "reason": "", "code": "", "nft": "", "kernel": "", "backend": "none",
            "firewalld": False, "python": "%d.%d.%d" % tuple(sys.version_info[:3]), "os": osr}
    r = runner.run(["uname", "-r"])
    if r.ok:
        info["kernel"] = r.out.strip()
    info["backend"] = iptables_backend(runner)
    info["firewalld"] = runner.run(["systemctl", "is-active", "firewalld"]).out.strip() == "active"
    has_nft = bool(runner.which(NFT))
    if has_nft:
        r = runner.run([NFT, "--version"])
        m = re.search(r"v(\d+(?:\.\d+)+)", r.out or "")
        if r.ok and m:
            info["nft"] = m.group(1)

    def no(code, reason):
        info["code"] = code
        info["reason"] = reason
        return info

    if not os_supported(osr):
        return no("os", tr("nft.cap.os", os=osr["pretty"] or tr("nft.cap.unknown_os")))
    if sys.version_info < (3, 8):
        return no("python", tr("nft.cap.python", version=info["python"]))
    if not has_nft:
        return no("no_nft", tr("nft.cap.no_nft"))
    if not info["nft"]:
        return no("nft_broken", tr("nft.cap.nft_broken"))
    if _vtuple(info["nft"]) < MIN_NFT:
        return no("old_nft", tr("nft.cap.old_nft", version=info["nft"]))
    kv = _vtuple(info["kernel"].split("-", 1)[0])
    if kv and kv < MIN_KERNEL:
        return no("old_kernel", tr("nft.cap.old_kernel", version=info["kernel"]))
    if info["firewalld"]:
        return no("firewalld", tr("nft.cap.firewalld"))
    r = runner.run([NFT, "-j", "list", "tables"])
    try:
        doc = json.loads(r.out) if r.ok else None
    except ValueError:
        doc = None
    if not (isinstance(doc, dict) and isinstance(doc.get("nftables"), list)):
        return no("no_json", tr("nft.cap.no_json", error=_short(_err_text(r) or tr("nft.cap.empty"))))
    ok, err = check(_table_text(PROBE_TABLE, list(PROBE_FWDS), {}), runner)
    if not ok:
        return no("check_failed", tr("nft.cap.check_failed", error=_short(err)))
    info["supported"] = True
    return info

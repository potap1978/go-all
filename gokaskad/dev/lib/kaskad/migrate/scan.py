"""``kaskadctl migrate scan`` — read-only inventory of the server (SPEC §4.2) and the
classification of DNAT rules with an effective first-match analysis (SPEC §4.3).

Nothing here changes the system: only reading commands (``iptables -S``, ``systemctl is-*``,
``ss``, ``ufw status``, ``iptables -V``) and reading files. Secrets never enter the report: of
``/etc/kaskad/config`` only "which keys are set" is kept, of the egress pool only the number of
keys.

Classification of a DNAT in nat PREROUTING (key = proto + destination port):

    pro          comment ``kaskad:N:P`` and N/P equal the rule's --dport/-p
    pro-legacy   no comment, simple form, and a FORWARD rule labeled ``kaskad:N:P`` to the same
                 target ``-d IP/32 --dport OUT`` (Kaskad PRO 2.0–2.4)
    free         no comment, simple form, and an unlabeled FORWARD
                 ``-d IP/32 -p P --dport OUT [state] -j ACCEPT`` to the same target
    (foreign)    anything else — never touched

Which rule works: walk PREROUTING top-down, jumps into user chains expanded (depth <= 3, a DNAT
inside counts at the position of the jump), first rule that can catch the key wins. Statuses of
Kaskad rules: ``import`` (it works), ``duplicate`` (an earlier Kaskad rule — or, on engine nft,
Kaskad 3 itself — has the key: dead, removed), ``shadowed`` (an earlier foreign/Docker rule
catches the key: left exactly as is), ``skipped`` (left as is because importing would change
behaviour: a manually narrowed rule sharing its port with other rules, a target Kaskad 3
cannot forward to, or a foreign rule with conditions before it — then the Kaskad rule works for
part of the traffic: reason ``partial_before``, never called dead).
"""

import ipaddress
import os
import re

from .. import __version__, ipt, nft, paths, state, units, util
from ..errors import StateError
from ..i18n import tr
from ..ipt import FULL, NONE, PARTIAL

SERVICES = ("kaskad-bot.service", "kaskad-monitor.service", "kaskad-egress.service",
            "kaskad-egress-probe.timer", "kaskad-net.service", "kaskad-net-watch.timer",
            "ufw.service", "docker.service", "firewalld.service", "nftables.service")
WATCHED_SERVICES = ("kaskad-bot.service", "kaskad-monitor.service", "kaskad-egress.service")
CLASSES = ("pro", "pro-legacy", "free")

# targets that end NAT processing for a packet in nat PREROUTING
_TERMINAL = ("DNAT", "REDIRECT", "NETMAP", "ACCEPT", "DROP", "REJECT", "RETURN", "QUEUE", "NFQUEUE")
_NON_TERMINAL = ("LOG", "NFLOG", "ULOG", "MARK", "CONNMARK", "TRACE", "CT", "NOTRACK", "TCPMSS",
                 "TOS", "DSCP", "TTL", "CLASSIFY", "SET", "AUDIT", "CHECKSUM", "SNAT", "MASQUERADE")
_NAT_TARGETS = ("DNAT", "REDIRECT", "NETMAP")
_VERSION_RE = re.compile(r'^KASKAD_VERSION="([^"]*)"', re.M)
_MAX_DEPTH = 3
_LOOPBACK = ipaddress.ip_network("127.0.0.0/8")


def display(path):
    """Production path for reports (without the test KASKAD_ROOT prefix)."""
    r = paths.root()
    return path[len(r):] if r and path.startswith(r) else path


def command_bak():
    return paths.P("/usr/local/bin/gokaskad.bak")      # 2.4+ self_update keeps the old program here


def proc_legacy_tables():
    return paths.P("/proc/net/ip_tables_names")


# ── small readers ──────────────────────────────────────────────────────────

def command_info(path):
    """What program is at ``path``: kind pro / free / k3 / unknown, KASKAD_VERSION."""
    info = {"path": display(path), "exists": False, "kind": None, "version": ""}
    text = util.read_text(path)
    if text is None:
        return info
    info["exists"] = True
    m = _VERSION_RE.search(text)
    if m:
        info["version"] = m.group(1)
        info["kind"] = "k3" if m.group(1).startswith("3.") else "pro"
    elif "show_promo" in text and "configure_rule" in text:
        info["kind"] = "free"
        info["version"] = "1.1" if "configure_custom_rule" in text else "1.0"
    else:
        info["kind"] = "unknown"
    return info


def read_aliases():
    """``IP=name|note|country|isp`` (2.2+) or ``IP=name`` (2.1) -> {ip: {...}}."""
    out = {}
    text = util.read_text(paths.aliases()) or ""
    for line in text.splitlines():
        if "=" not in line:
            continue
        ip, _, val = line.partition("=")
        ip = ip.strip()
        if not ip or ip in out:
            continue                       # 2.x reads the first line of an IP (head -1)
        parts = (val.split("|") + ["", "", "", ""])[:4]
        out[ip] = {"name": state.clean_name(parts[0]), "note": state.clean_name(parts[1]),
                   "country": state.clean_name(parts[2]), "isp": state.clean_name(parts[3])}
    return out


def read_config_keys():
    """Which keys of /etc/kaskad/config are set (True) or empty (False). Values never kept."""
    text = util.read_text(paths.config())
    if text is None:
        return {"exists": False, "keys": {}}
    keys = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not m:
            continue
        v = m.group(2).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        keys[m.group(1)] = bool(v)
    return {"exists": True, "keys": keys}


def read_monitors():
    d = paths.monitors()
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    return [n[:-5] for n in names if n.endswith(".conf") and not n.startswith(".")]


def egress_keys():
    text = util.read_text(paths.egress_up()) or ""
    return sum(1 for line in text.splitlines() if line.strip())


def parse_ss(text):
    out = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[4]
        addr, _, port = local.rpartition(":")
        if not port.isdigit():
            continue
        m = re.search(r'users:\(\("([^"]+)"', line)
        out.append({"proto": parts[0], "address": addr, "port": int(port),
                    "process": m.group(1) if m else ""})
    return out


def services(runner):
    out = {}
    have = runner.which("systemctl")
    for unit in SERVICES:
        if not have:
            out[unit] = {"enabled": "unknown", "active": "unknown"}
            continue
        en = runner.run(["systemctl", "is-enabled", unit])
        ac = runner.run(["systemctl", "is-active", unit])
        enabled = (en.out.strip().splitlines() or [""])[0] or ("not-found" if not en.ok else "unknown")
        active = (ac.out.strip().splitlines() or [""])[0] or "unknown"
        out[unit] = {"enabled": enabled, "active": active}
    return out


def ufw_info(runner):
    info = {"installed": bool(runner.which("ufw")), "active": False, "forward_policy": ""}
    text = util.read_text(paths.ufw_default()) or ""
    m = re.search(r'^\s*DEFAULT_FORWARD_POLICY="?([A-Za-z]+)"?', text, re.M)
    if m:
        info["forward_policy"] = m.group(1).upper()
    if info["installed"]:
        r = runner.run(["ufw", "status"])
        info["active"] = r.ok and "Status: active" in r.out
    return info


def default_iface(runner):
    """Interface of the default route, as 2.x detect_interface: ``ip route get 8.8.8.8``."""
    r = runner.run(["ip", "route", "get", "8.8.8.8"])
    m = re.search(r"\bdev\s+(\S+)", r.out or "")
    return m.group(1) if (r.ok and m) else ""


# ── coverage: can a rule catch packets of a key? ───────────────────────────

def loopback(addr):
    """Is an address or network inside 127.0.0.0/8 (as conflicts._loopback_dst)?"""
    try:
        return ipaddress.ip_network(addr, strict=False).subnet_of(_LOOPBACK)
    except (ValueError, TypeError):
        return False


def coverage(r, proto, port):
    """NONE: never matches (proto, port) traffic addressed to this server; FULL: matches all of
    it; PARTIAL: some of it (extra conditions we cannot evaluate). A plain ``-d`` inside
    127.0.0.0/8 (Docker publishing ``-p 127.0.0.1:8080:80``) never sees traffic from outside:
    NONE; ``! -d 127.0.0.0/8`` restricts nothing."""
    if r.error:
        return PARTIAL
    level = FULL
    if r.proto is not None:
        m = r.proto == proto
        if r.proto_neg:
            m = not m
        if not m:
            return NONE
    if r.dport is not None:
        m = ipt.port_matches(r.dport, port)
        if m is None:
            level = PARTIAL
        else:
            if r.dport_neg:
                m = not m
            if not m:
                return NONE
    if r.sport is not None or r.src is not None or r.frag:
        level = PARTIAL
    if r.dst is not None:
        if not loopback(r.dst):
            level = PARTIAL
        elif not r.dst_neg:
            return NONE
    if r.iif is not None or r.oif is not None or r.states is not None:
        level = PARTIAL
    for mod, neg, opt, vals in r.opts:
        val = vals[0] if vals else ""
        if mod in ipt._PORT_MODULES + ("state", "conntrack", "comment") and \
                opt in ("--dport", "--destination-port", "--sport", "--source-port", "--state",
                        "--ctstate", "--comment"):
            continue
        if mod == "multiport" and opt in ("--dports", "--destination-ports", "--ports"):
            m = ipt.port_list_matches(val, port)
            if m is None:
                level = PARTIAL
                continue
            if neg:
                m = not m
            if not m:
                return NONE
            continue
        if mod == "addrtype" and opt == "--dst-type":
            m = "LOCAL" in val.upper().split(",")
            if neg:
                m = not m
            if not m:
                return NONE
            continue
        level = PARTIAL
    return level


class Entry(object):
    """A rule as nat PREROUTING meets it: ``path`` are the jumps that led to it, ``frames``
    the chain invocations (for RETURN)."""

    __slots__ = ("rule", "path", "frames", "kind")

    def __init__(self, rule, path, frames, kind="rule"):
        self.rule, self.path, self.frames, self.kind = rule, path, frames, kind

    @property
    def docker(self):
        if any(f[0].startswith("DOCKER") for f in self.frames[1:]):
            return True
        return self.kind != "rule" and (self.rule.target or "").startswith("DOCKER")

    def cover(self, proto, port):
        lvl = FULL
        for r in self.path + (self.rule,):
            lvl = min(lvl, coverage(r, proto, port))
            if lvl == NONE:
                break
        return lvl


def expand(nat, start="PREROUTING"):
    """Rules of ``start`` in evaluation order with user-chain jumps expanded."""
    out = []
    counter = [0]

    def walk(chain, path, frames, depth):
        for r in nat.get(chain, []):
            tgt = r.target or ""
            if tgt in nat and tgt not in ipt.BUILTIN_CHAINS["nat"]:
                if depth >= _MAX_DEPTH or any(f[0] == tgt for f in frames):
                    out.append(Entry(r, path, frames, "opaque"))
                    continue
                counter[0] += 1
                walk(tgt, path + (r,), frames + ((tgt, counter[0]),), depth + 1)
                if r.goto:
                    out.append(Entry(r, path, frames, "goto_end"))
                continue
            out.append(Entry(r, path, frames))

    walk(start, (), ((start, 0),), 0)
    return out


def _captures(r):
    t = r.target
    if not t:
        return False
    if t in _TERMINAL:
        return True
    return t not in _NON_TERMINAL           # unknown extension target: assume it may catch


def entries_for_key(entries, proto, port):
    """[(Entry, coverage)] that may catch (proto, port), in order."""
    res = []
    returned = set()
    for e in entries:
        if any(f in returned for f in e.frames[1:]):
            continue
        cov = e.cover(proto, port)
        if cov == NONE:
            continue
        r = e.rule
        if e.kind == "rule":
            if r.target == "RETURN" and len(e.frames) > 1:
                if cov == FULL:
                    returned.add(e.frames[-1])
                continue
            if not _captures(r):
                continue
        res.append((e, cov))
    return res


# ── Kaskad rules ───────────────────────────────────────────────────────────

class Candidate(object):
    """A DNAT in nat PREROUTING that Kaskad (free / PRO) created."""

    def __init__(self, rule, cls, proto, port, dest):
        self.rule = rule
        self.cls = cls
        self.proto = proto
        self.port = port
        self.to, self.to_port = dest if dest else (None, None)
        self.status = None
        self.reason = ""
        self.winner = None             # text of the rule (or Kaskad 3 forward) that works instead
        self.qualifiers = rule.qualifiers()
        extra_targs = [a for a in rule.target_args if a != "--to-destination" and a != rule.to_destination]
        if extra_targs:
            self.qualifiers.append(" ".join(extra_targs))
        self.coverage = FULL
        self.importable = dest is not None
        self.bad_target = "" if dest else tr("migrate.scan.bad_target", target=repr(rule.to_destination or ""))
        self.assoc = []                # [(Rule, role)] INPUT / FORWARD rules that belong to it
        self.name = ""
        self.warnings = []
        self.before = ""               # conditions of a foreign rule before it that catches part of the key
        self.source = "live"           # "file": found only in the persisted rules file (rules.v4)

    @property
    def key(self):
        return (self.proto, self.port)

    @property
    def key_text(self):
        return "%s:%d" % (self.proto, self.port)

    def forward(self):
        return {"proto": self.proto, "port": self.port, "to": self.to, "to_port": self.to_port,
                "name": self.name, "origin": self.cls}

    def to_dict(self):
        d = {"key": self.key_text, "proto": self.proto, "port": self.port, "to": self.to,
             "to_port": self.to_port, "class": self.cls, "status": self.status,
             "reason": self.reason, "name": self.name, "qualifiers": list(self.qualifiers),
             "source": self.source, "rules": [dict(self.rule.to_dict(), role="dnat")]}
        d["rules"] += [dict(r.to_dict(), role=role) for r, role in self.assoc]
        if self.winner:
            d["works_instead"] = self.winner
        if self.before:
            d["condition_before"] = self.before
        if self.bad_target:
            d["bad_target"] = self.bad_target
        return d


def _is_free_fwd(r, proto, ip, port, side):
    if r.chain != "FORWARD" or r.comment is not None or r.target != "ACCEPT" or r.goto or r.error:
        return False
    if r.iif is not None or r.oif is not None or r.frag or r.extra or r.target_args:
        return False
    if r.proto != proto or r.proto_neg or (r.states is not None and r.states_neg):
        return False
    if side == "d":
        return (r.dst == ip and not r.dst_neg and r.src is None and r.sport is None
                and r.dport == str(port) and not r.dport_neg)
    return (r.src == ip and not r.src_neg and r.dst is None and r.dport is None
            and r.sport == str(port) and not r.sport_neg)


def _is_free_input(r, proto, port):
    return (r.chain == "INPUT" and r.comment is None and r.target == "ACCEPT" and not r.goto
            and r.proto == proto and not r.proto_neg and r.dport == str(port) and r.simple
            and not r.target_args)


def _labeled_fwd_to(filt, label, proto, ip, port):
    for r in filt.get("FORWARD", []):
        if (r.comment == label and r.dst == ip and not r.dst_neg and r.proto == proto
                and r.dport == str(port) and not r.dport_neg):
            return True
    return False


def classify_dnat(r, filt):
    """Candidate for a Kaskad-made DNAT in nat PREROUTING, or None (foreign)."""
    if r.chain != "PREROUTING" or r.target != "DNAT" or r.goto or r.error:
        return None
    lab = r.label
    if lab is not None:
        port, proto = lab
        if r.proto != proto or r.proto_neg or r.port != port or r.dport_neg:
            return None
        return Candidate(r, "pro", proto, port, ipt.parse_to_destination(r.to_destination, port))
    if r.comment is not None:
        return None
    if r.proto not in ("tcp", "udp") or r.proto_neg or r.port is None or not r.simple:
        return None
    if r.target_args != ["--to-destination", r.to_destination]:
        return None
    dest = ipt.parse_to_destination(r.to_destination)
    if dest is None:
        return None
    label = "kaskad:%d:%s" % (r.port, r.proto)
    if _labeled_fwd_to(filt, label, r.proto, dest[0], dest[1]):
        return Candidate(r, "pro-legacy", r.proto, r.port, dest)
    if any(_is_free_fwd(f, r.proto, dest[0], dest[1], "d") for f in filt.get("FORWARD", [])):
        return Candidate(r, "free", r.proto, r.port, dest)
    return None


def associate(c, filt):
    """INPUT / FORWARD rules that belong to candidate ``c`` (SPEC §4.4)."""
    c.assoc = []
    if c.cls in ("pro", "pro-legacy"):
        label = "kaskad:%d:%s" % (c.port, c.proto)
        for chain in ("INPUT", "FORWARD"):
            for r in filt.get(chain, []):
                if r.comment == label:
                    role = "input" if chain == "INPUT" else ("forward_reply" if r.src and not r.dst else "forward")
                    c.assoc.append((r, role))
        return
    if c.to is None:
        return
    for r in filt.get("INPUT", []):
        if _is_free_input(r, c.proto, c.port):
            c.assoc.append((r, "input"))
    for r in filt.get("FORWARD", []):
        if _is_free_fwd(r, c.proto, c.to, c.to_port, "d"):
            c.assoc.append((r, "forward"))
        elif _is_free_fwd(r, c.proto, c.to, c.to_port, "s"):
            c.assoc.append((r, "forward_reply"))


def assign(cands, nat, k3=None):
    """Statuses of all candidates (in place). Returns the list of conflicts."""
    k3 = k3 or {}
    conflicts = []
    entries = expand(nat)
    by_rule = dict((id(c.rule), c) for c in cands)
    keys = []
    for c in cands:
        if c.key not in keys:
            keys.append(c.key)
    published = {}
    for chain, rules in nat.items():
        if not chain.startswith("DOCKER"):
            continue
        for r in rules:
            if r.target in _NAT_TARGETS:
                for k in keys:
                    if coverage(r, k[0], k[1]) != NONE:
                        published.setdefault(k, r)

    for key in keys:
        same = [c for c in cands if c.key == key]
        if key in k3:
            to, to_port = k3[key]
            for c in same:
                c.status, c.reason, c.winner = "duplicate", "k3", "Kaskad 3 → %s:%d" % (to, to_port)
            continue
        elist = entries_for_key(entries, key[0], key[1])
        listed = set(id(e.rule) for e, _cv in elist)
        for c in same:
            if id(c.rule) not in listed:
                # never catches traffic addressed to this server (e.g. narrowed to transit by hand):
                # not a forward Kaskad 3 can take over, and not provably dead either — leave it
                c.status, c.reason = "skipped", "unreachable"
        same = [c for c in same if c.status is None]
        if not same:
            continue
        first, cov = elist[0]
        fc = by_rule.get(id(first.rule)) if first.kind == "rule" else None
        if fc is None or fc.key != key:
            _foreign_first(key, same, elist, by_rule)
            continue
        fc.coverage = cov
        if not fc.importable:
            fc.status, fc.reason = "skipped", "target"
            for c in same:
                if c is not fc:
                    c.status, c.reason, c.winner = "skipped", "key_kept", fc.rule.text
            continue
        others = elist[1:]
        if cov != FULL and others:
            for c in same:
                c.status, c.reason = "skipped", "partial"
            continue
        fc.status = "import"
        if fc.qualifiers:
            fc.warnings.append("manual")
        for c in same:
            if c is not fc:
                c.status, c.reason, c.winner = "duplicate", "second", fc.rule.text
        docker_seen = False
        for e, _cv in others:
            oc = by_rule.get(id(e.rule))
            if oc is not None and oc.key == key:
                continue
            where = "%s: %s" % (e.rule.chain, e.rule.spec())
            if e.docker and e.rule.target in _NAT_TARGETS:
                docker_seen = True
                conflicts.append({"key": fc.key_text, "kind": "docker_port", "blocking": True,
                                  "detail": tr("migrate.scan.docker_after", where=where)})
            elif e.rule.target in _NAT_TARGETS:
                conflicts.append({"key": fc.key_text, "kind": "foreign_after", "blocking": False,
                                  "detail": tr("migrate.scan.foreign_after", where=where)})
        if key in published and not docker_seen:
            pub = published[key]
            conflicts.append({"key": fc.key_text, "kind": "docker_port", "blocking": True,
                              "detail": tr("migrate.scan.docker_published", chain=pub.chain, spec=pub.spec())})
    return conflicts


def _partial_conditions(e, proto, port):
    """What narrows a rule (and the jumps leading to it) to part of the key's traffic."""
    q = []
    for r in e.path + (e.rule,):
        if coverage(r, proto, port) == PARTIAL:
            q += [x for x in r.qualifiers() if x not in q]
    return "; ".join(q) or e.rule.spec()


def _foreign_first(key, same, elist, by_rule):
    """A foreign rule (or Docker) is the first to catch the key. If a rule catching ALL of the
    key's traffic (or Docker's published port) comes before the first Kaskad rule, the Kaskad rules
    never worked: shadowed. If only rules with conditions come before it (``-s 10.0.0.0/8``...),
    the first Kaskad rule works for the rest of the traffic: left exactly as is, but not called
    dead (status skipped, reason partial_before)."""
    blocker, partial, kaskad = None, [], None
    for e, cv in elist:
        oc = by_rule.get(id(e.rule)) if e.kind == "rule" else None
        if oc is not None and oc.key == key:
            kaskad = oc
            break
        if cv == FULL or e.docker:
            blocker = e
            break
        partial.append(e)
    if blocker is None and (kaskad is None or not partial):
        blocker = elist[0][0]
    if blocker is not None:
        reason = "docker" if blocker.docker else "foreign"
        for c in same:
            c.status, c.reason, c.winner = "shadowed", reason, blocker.rule.text
        return
    for c in same:
        if c is kaskad:
            c.status, c.reason, c.winner = "skipped", "partial_before", partial[0].rule.text
            c.before = _partial_conditions(partial[0], key[0], key[1])
        else:
            c.status, c.reason, c.winner = "skipped", "key_kept", kaskad.rule.text


def check_target(c):
    """Kaskad 3 refuses some targets (loopback, 0.0.0.0, multicast...): such a rule stays as is."""
    if not c.importable:
        return
    try:
        state.normalize_forward(dict(c.forward(), origin=c.cls))
    except StateError as e:
        c.importable = False
        c.bad_target = str(e)


# ── persisted rules file ───────────────────────────────────────────────────

class Persisted(object):
    def __init__(self, path, raw, ss):
        self.path = path
        self.raw = raw               # bytes, to restore or rewrite byte-exact
        self.ss = ss
        self.mode = 0o640

    def rules(self, tables=("nat", "filter")):
        out = []
        for t in tables:
            if t in self.ss:
                out += list(self.ss[t].all_rules())
        return out


def read_persisted():
    for p in (paths.rules_v4(), paths.rules_sysconfig()):
        try:
            with open(p, "rb") as f:
                raw = f.read()
            st_mode = os.stat(p).st_mode & 0o7777
        except (OSError, IOError):
            continue
        ps = Persisted(p, raw, ipt.parse_save(raw.decode("utf-8", "surrogateescape")))
        ps.mode = st_mode
        return ps
    return None


def _diff(a_rules, b_rules):
    """Rules of a whose (table, norm) is missing from b, with multiplicity."""
    want = ipt.multiset(b_rules)
    out = []
    for r in a_rules:
        k = (r.table, r.norm())
        if want[k] > 0:
            want[k] -= 1
        else:
            out.append("%s: %s" % (r.table, r.text))
    return out


# ── the inventory ──────────────────────────────────────────────────────────

class Inventory(object):
    """Everything scan found. ``report()`` is the JSON-able view (no secrets)."""

    def __init__(self):
        self.engine = "legacy"
        self.state = None
        self.state_raw = None
        self.state_error = ""
        self.k3 = {}
        self.backend = {"backend": "none", "version": ""}
        self.mixed = False
        self.nat = ipt.Chains("nat")
        self.filt = ipt.Chains("filter")
        self.ipt_error = ""
        self.cands = []
        self.conflicts = []
        self.persisted = None
        self.support = {}
        self.nft_binary = False
        self.live_table = None
        self.live_error = ""
        self.command = {}
        self.command_bak = {}
        self.edition = "none"
        self.version = ""
        self.version_source = ""
        self.aliases = {}
        self.config = {}
        self.monitors = []
        self.egress = 0
        self.services = {}
        self.ufw = {}
        self.listeners = []
        self.warnings = []
        self.apt = False
        self.forward_established = True

    # convenience
    def by_status(self, status):
        return [c for c in self.cands if c.status == status]

    @property
    def masq_kaskad(self):
        return [r for r in self.nat.get("POSTROUTING", []) if r.target == "MASQUERADE" and r.comment == ipt.MASQ_LABEL]

    @property
    def masq_unlabeled(self):
        return [r for r in self.nat.get("POSTROUTING", []) if r.target == "MASQUERADE" and r.comment is None]

    def foreign_dnat(self):
        mine = set(id(c.rule) for c in self.cands)
        out = []
        for chain, rules in self.nat.items():
            for r in rules:
                if r.target in _NAT_TARGETS and id(r) not in mine:
                    out.append(r)
        return out

    def report(self):
        fwds = [c.to_dict() for c in self.cands]
        fd = [{"chain": r.chain, "rule": r.text, "docker": r.chain.startswith("DOCKER")} for r in self.foreign_dnat()]
        pers = {"file": "", "exists": False, "only_live": [], "only_persisted": [], "problems": 0}
        if self.persisted is not None:
            live = list(self.nat.all_rules()) + list(self.filt.all_rules())
            prules = self.persisted.rules()
            pers = {"file": display(self.persisted.path), "exists": True,
                    "only_live": _diff(live, prules), "only_persisted": _diff(prules, live),
                    "problems": len(self.persisted.ss.problems)}
        else:
            pers["file"] = display(paths.rules_v4())
        return {
            "edition": self.edition,
            "version": self.version,
            "version_source": self.version_source,
            "engine": self.engine,
            "kaskad3": __version__,
            "state": {"exists": self.state_raw is not None, "error": self.state_error,
                      "forwards": len(self.state["forwards"]) if self.state else 0},
            "command": dict(self.command, backup=self.command_bak),
            "support": self.support,
            "iptables": {"backend": self.backend.get("backend"), "version": self.backend.get("version"),
                         "mixed": self.mixed, "error": self.ipt_error},
            "forwards": fwds,
            "foreign_dnat": fd,
            "masquerade": {"kaskad": [r.text for r in self.masq_kaskad],
                           "unlabeled": [r.text for r in self.masq_unlabeled]},
            "conflicts": list(self.conflicts),
            "persisted": pers,
            "firewall": {"ufw_active": bool(self.ufw.get("active")),
                         "ufw_forward_policy": self.ufw.get("forward_policy", ""),
                         "forward_policy": self.filt.policies.get("FORWARD", ""),
                         "forward_keeps_established": self.forward_established,
                         "docker": any(c.startswith("DOCKER") for c in self.nat) or
                         self.services.get("docker.service", {}).get("active") == "active",
                         "firewalld": self.services.get("firewalld.service", {}).get("active") == "active"},
            "services": self.services,
            "egress": {"keys": self.egress},
            "config": self.config,
            "aliases": self.aliases,
            "monitors": self.monitors,
            "listeners": self.listeners,
            "nft_table": {"exists": self.live_table is not None,
                          "forwards": len((self.live_table or {}).get("forwards") or {}),
                          "error": self.live_error},
            "engine_health": engine_health(self),
            "warnings": list(self.warnings),
        }


_NOT_ENABLED = ("disabled", "not-found")


def engine_health(inv):
    """Engine nft whose forwards are not live now or would not come back at boot: the table is not
    loaded, or kaskad-net.service / the watchdog timer is not enabled — a migration cut off by a
    power loss, units removed by hand. ``migrate apply`` and the menu repair it (nft.apply +
    units.install). Short Russian problems; [] when all is well or the engine is not nft."""
    if inv.engine != "nft" or inv.state is None:
        return []
    out = []
    if inv.nft_binary and not inv.live_error and inv.live_table is None:
        out.append(tr("migrate.scan.table_not_loaded"))
    if (inv.services.get(units.NET_UNIT) or {}).get("enabled") in _NOT_ENABLED:
        out.append(tr("migrate.unit_disabled", unit=units.NET_UNIT))
    if (inv.services.get(units.WATCH_TIMER) or {}).get("enabled") in _NOT_ENABLED:
        out.append(tr("migrate.scan.timer_disabled", unit=units.WATCH_TIMER))
    return out


def name_for(inv, c):
    """Name of a forward: the one state.json gave it (same port and target), else the alias of
    the target (2.x aliases)."""
    for f in (inv.state or {}).get("forwards") or []:
        if (f["proto"], f["port"], f["to"], f["to_port"]) == (c.proto, c.port, c.to, c.to_port) and f.get("name"):
            return f["name"]
    return (inv.aliases.get(c.to) or {}).get("name", "")


def file_candidates(ps):
    """Kaskad DNATs of the persisted file, classified within the file — what 2.x would bring back
    at boot (first match, as for the live rules). [] without a readable file."""
    if ps is None or ps.ss.problems:
        return []
    nat = ps.ss.get("nat") or ipt.Chains("nat")
    filt = ps.ss.get("filter") or ipt.Chains("filter")
    out = []
    for r in nat.get("PREROUTING", []):
        c = classify_dnat(r, filt)
        if c is None:
            continue
        associate(c, filt)
        check_target(c)
        c.source = "file"
        out.append(c)
    if out:
        assign(out, nat)
    return out


def _edition(inv):
    """Which installation the rules come from (for the snapshot name and the migration record)."""
    cmd, bak = inv.command, inv.command_bak
    if inv.engine == "nft":
        inv.edition, inv.version = "k3", cmd.get("version") if cmd.get("kind") == "k3" else __version__
        inv.version_source = "state"
        return
    for info, src in ((cmd, "command"), (bak, "command.bak")):
        if info.get("kind") in ("pro", "free"):
            inv.edition, inv.version, inv.version_source = info["kind"], info.get("version", ""), src
            return
    # the program itself is gone or already 3.x (goInstaller copies 3.x over it): judge by traces
    classes = set(c.cls for c in inv.cands)
    labeled_dnat = any(r.label and r.target == "DNAT" for r in inv.nat.get("PREROUTING", []))
    labeled = labeled_dnat or any(r.label or r.comment == ipt.MASQ_LABEL
                                  for r in list(inv.nat.all_rules()) + list(inv.filt.all_rules()))
    version = ""
    if "pro" in classes or "pro-legacy" in classes or labeled:
        inv.edition = "pro"
        # 2.0–2.4 labeled INPUT/FORWARD only; 2.5+ labeled the DNAT too
        version = "2.0-2.4" if ("pro-legacy" in classes and not labeled_dnat) else "2.x"
    elif "free" in classes:
        inv.edition, version = "free", "1.x"
    elif cmd.get("kind") == "k3" or inv.state_raw is not None:
        inv.edition, inv.version = "k3", cmd.get("version") or __version__
        inv.version_source = "command"
        return
    elif inv.config.get("exists"):
        inv.edition, version = "pro", "2.x"
    else:
        inv.edition = "none"
    inv.version, inv.version_source = version, "rules"


def collect(runner):
    """Read everything (SPEC §4.2) and classify (§4.3). Read-only."""
    inv = Inventory()

    # state.json
    inv.state_raw = util.read_text(paths.state())
    try:
        inv.state = state.load()
        inv.engine = inv.state["engine"]
    except StateError as e:
        inv.state_error = str(e)
        inv.state = None
    if inv.state and inv.engine == "nft":
        for f in inv.state["forwards"]:
            inv.k3[(f["proto"], f["port"])] = (f["to"], f["to_port"])

    # iptables
    inv.backend = ipt.backend(runner)
    be = inv.backend["backend"]
    if be != "none":
        try:
            inv.nat = ipt.list_table(runner, "nat", be)
            inv.filt = ipt.list_table(runner, "filter", be)
        except Exception as e:                       # noqa: BLE001 — reported as a blocker
            inv.ipt_error = util.redact(str(e))
        notes = inv.nat.notes + inv.filt.notes
        if any("legacy tables present" in n for n in notes):
            inv.mixed = True
        other = "iptables-legacy-save" if be == "nf_tables" else "iptables-nft-save"
        names = util.read_text(proc_legacy_tables()) if be == "nf_tables" else ""
        if not inv.mixed and runner.which(other) and (be != "nf_tables" or (names or "").strip()):
            r = runner.run([other])
            if r.ok and any(l.startswith("-A ") for l in r.out.splitlines()):
                inv.mixed = True
        if inv.mixed:
            inv.warnings.append(tr("migrate.scan.mixed", other=other.replace("-save", ""), backend=be))

    # Kaskad rules
    for r in inv.nat.get("PREROUTING", []):
        c = classify_dnat(r, inv.filt)
        if c is not None:
            associate(c, inv.filt)
            check_target(c)
            inv.cands.append(c)
    inv.conflicts = assign(inv.cands, inv.nat, inv.k3)

    # files
    inv.command = command_info(paths.command())
    inv.command_bak = command_info(command_bak())
    inv.aliases = read_aliases()
    for c in inv.cands:
        c.name = name_for(inv, c)
    inv.config = read_config_keys()
    inv.monitors = read_monitors()
    inv.egress = egress_keys()
    inv.persisted = read_persisted()
    if inv.persisted is not None and inv.persisted.ss.problems:
        inv.warnings.append(tr("migrate.scan.file_bad", file=display(inv.persisted.path)))

    # system
    inv.services = services(runner)
    inv.ufw = ufw_info(runner)
    if runner.which("ss"):
        r = runner.run(["ss", "-Hltnup"])
        inv.listeners = parse_ss(r.out if r.ok else "")
    inv.nft_binary = bool(runner.which("nft"))
    inv.apt = bool(runner.which("apt-get"))
    try:
        inv.support = nft.probe_support(runner)
    except Exception as e:                           # noqa: BLE001
        inv.support = {"supported": False, "reason": tr("migrate.scan.nft_probe_failed", error=util.redact(e))}
    if inv.nft_binary:
        try:
            inv.live_table = nft.read_live(runner)
        except Exception as e:                       # noqa: BLE001
            inv.live_error = util.redact(e)
            inv.warnings.append(tr("migrate.scan.table_unreadable", error=inv.live_error))
    if inv.engine == "legacy" and inv.live_table and inv.live_table.get("forwards"):
        inv.conflicts.append({"key": "", "kind": "stale_table", "blocking": True,
                              "detail": tr("migrate.scan.stale_table", n=len(inv.live_table["forwards"]))})
    _edition(inv)
    inv.forward_established = forward_keeps_established(inv.filt)
    _forward_bypass_warning(inv)
    return inv


def forward_keeps_established(filt):
    """Does the FORWARD chain let packets of already established connections through without
    Kaskad's own rules? True with policy ACCEPT, or when a generic ``--ctstate ESTABLISHED``
    ACCEPT (UFW's ufw-before-forward) comes before any unconditional DROP. Docker's FORWARD
    (policy DROP, ESTABLISHED only for ``-o docker0``) gives False."""
    if filt.policies.get("FORWARD", "ACCEPT") == "ACCEPT":
        return True

    def ours(r):
        return r.label is not None or r.comment == ipt.COMPAT_LABEL

    def walk(chain, depth):
        for r in filt.get(chain, []):
            if ours(r):
                continue
            plain = (r.src is None and r.dst is None and r.iif is None and r.oif is None
                     and r.proto is None and not r.frag)
            tgt = r.target or ""
            if tgt in filt and tgt not in ipt.BUILTIN_CHAINS["filter"]:
                if plain and not r.extra and r.states is None and depth < _MAX_DEPTH:
                    res = walk(tgt, depth + 1)
                    if res is not None:
                        return res
                continue
            if plain and tgt == "ACCEPT" and r.states is not None and not r.states_neg \
                    and "ESTABLISHED" in r.states and not r.extra:
                return True
            if plain and r.states is None and not r.extra:
                if tgt in ("DROP", "REJECT"):
                    return False
                if tgt == "RETURN":
                    return None
        return None

    return bool(walk("FORWARD", 0))


def _forward_bypass_warning(inv):
    """After the switch Kaskad's connections are accepted by kaskad:compat at FORWARD position 1.
    Own DROP/REJECT rules there would no longer apply to them — say so."""
    own = []
    for r in inv.filt.get("FORWARD", []):
        if r.target in ("DROP", "REJECT") and r.comment is None:
            own.append(r.spec())
    for r in inv.filt.get("DOCKER-USER", []):
        if r.target != "RETURN":
            own.append(r.spec())
    for r in inv.filt.get("ufw-user-forward", []):
        own.append(r.spec())
    if own and any(c.status == "import" for c in inv.cands):
        inv.warnings.append(tr("migrate.scan.forward_bypass", rules="; ".join(own[:3])))

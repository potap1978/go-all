"""iptables rules as data: parsing of ``iptables -S`` / ``iptables-save`` output (work package 2).

Used by the migration (``kaskad.migrate``) to read what Kaskad free / PRO 2.x left in iptables,
and to take exactly those rules out again.

Splitting. A line is cut with ``shlex`` set up like iptables-restore's own tokenizer: whitespace
separates, only double quotes group, ``\\"`` and ``\\\\`` inside them are escapes. Nothing is ever
given to a shell or to ``eval`` (2.x did ``eval "iptables -D ${rule#-A }"``).

Rules. Every ``-A`` line becomes a :class:`Rule` with the fields the migration reasons about
(``proto``, ``dport``, ``src``/``dst`` without ``/32``, interfaces, negations, ``comment``,
``target``, ``to_destination``, ...). Whatever is not understood lands in ``extra``; ``simple``
means "no extra and no qualifiers".

Sameness. :meth:`Rule.norm` ignores how iptables happened to print a rule: comment quoted or
bare, ``-m tcp`` written or implied, ``/32`` on a host address, ``--state`` flag order,
``-m state --state`` vs ``-m conntrack --ctstate``, and where the comment stands (iptables-nft
prints it after all other matches, iptables-legacy where it was given). Two rules with equal
``norm()`` are the same rule for the kernel.

Deleting. :func:`delete_line` turns a printed ``-A`` line into a ``-D`` line for
``iptables-restore --noflush``. The original text is reused as is, so the quoting iptables chose
when printing is exactly what it parses back.
"""

import collections
import ipaddress
import re
import shlex

from .errors import KaskadError
from .i18n import tr

BUILTIN_CHAINS = {
    "filter": ("INPUT", "FORWARD", "OUTPUT"),
    "nat": ("PREROUTING", "INPUT", "OUTPUT", "POSTROUTING"),
    "mangle": ("PREROUTING", "INPUT", "FORWARD", "OUTPUT", "POSTROUTING"),
    "raw": ("PREROUTING", "OUTPUT"),
    "security": ("INPUT", "FORWARD", "OUTPUT"),
}

# conntrack/state flags in the order iptables prints them
STATE_ORDER = ("INVALID", "NEW", "RELATED", "ESTABLISHED", "UNTRACKED", "SNAT", "DNAT")

LABEL_RE = re.compile(r"^kaskad:(\d{1,5}):(tcp|udp)$")
MASQ_LABEL = "kaskad:masq"
COMPAT_LABEL = "kaskad:compat"

_COUNTERS_RE = re.compile(r"^\[(\d+):(\d+)\]\s+")
_PROTO_NUM = {"6": "tcp", "17": "udp", "1": "icmp", "132": "sctp", "33": "dccp", "136": "udplite"}
_PORT_MODULES = ("tcp", "udp", "sctp", "dccp", "udplite")
_DPORT_OPTS = ("--dport", "--destination-port")
_SPORT_OPTS = ("--sport", "--source-port")
_ADDR_OPTS = {"-s": "src", "--source": "src", "--src": "src",
              "-d": "dst", "--destination": "dst", "--dst": "dst"}
_IFACE_OPTS = {"-i": "iif", "--in-interface": "iif", "-o": "oif", "--out-interface": "oif"}

# Options whose value count is known (values may start with "-", e.g. a comment "-x").
# Unknown options take the following tokens up to the next one that starts with "-" or is "!".
_ARITY = {
    "--dport": 1, "--destination-port": 1, "--sport": 1, "--source-port": 1,
    "--state": 1, "--ctstate": 1, "--ctstatus": 1, "--ctproto": 1, "--ctdir": 1,
    "--ctorigsrc": 1, "--ctorigdst": 1, "--ctreplsrc": 1, "--ctrepldst": 1,
    "--ctorigsrcport": 1, "--ctorigdstport": 1, "--ctreplsrcport": 1, "--ctrepldstport": 1,
    "--comment": 1, "--dports": 1, "--sports": 1, "--ports": 1,
    "--destination-ports": 1, "--source-ports": 1,
    "--dst-type": 1, "--src-type": 1, "--limit-iface-in": 0, "--limit-iface-out": 0,
    "--mark": 1, "--tcp-flags": 2, "--syn": 0, "--icmp-type": 1, "--match-set": 2,
    "--limit": 1, "--limit-burst": 1, "--uid-owner": 1, "--gid-owner": 1,
    "--to-destination": 1, "--to-source": 1, "--to-ports": 1, "--to": 1,
    "--random": 0, "--random-fully": 0, "--persistent": 0, "--reject-with": 1,
    "--log-prefix": 1, "--log-level": 1, "--set-mark": 1, "--set-xmark": 1,
    "--save-mark": 0, "--restore-mark": 0, "--nfmask": 1, "--ctmask": 1,
    "--clamp-mss-to-pmtu": 0, "--set-mss": 1, "--queue-num": 1,
}

NONE, PARTIAL, FULL = 0, 1, 2


def split_line(text):
    """Tokens of one rule line (see the module docstring). Raises ValueError on a bad quote."""
    lex = shlex.shlex(text, posix=True)
    lex.whitespace_split = True
    lex.commenters = ""
    lex.quotes = '"'
    lex.escapedquotes = '"'
    return list(lex)


def norm_addr(value):
    """``1.2.3.4/32`` -> ``1.2.3.4``; ``0.0.0.0/0`` -> None; anything else unchanged."""
    if value is None:
        return None
    v = value.strip()
    if v.endswith("/32"):
        v = v[:-3]
    if v in ("0.0.0.0/0", "0/0", "0.0.0.0/0.0.0.0"):
        return None
    return v


def norm_proto(value):
    if value is None:
        return None
    p = value.strip().lower()
    p = _PROTO_NUM.get(p, p)
    return None if p in ("all", "0") else p


def norm_port(value):
    """``"0443"`` -> ``"443"``, ``"1000:2000"`` stays a range, service names stay as written."""
    v = (value or "").strip()
    if v.isdigit():
        return str(int(v))
    m = re.match(r"^(\d*):(\d*)$", v)
    if m:
        lo = str(int(m.group(1))) if m.group(1) else "0"
        hi = str(int(m.group(2))) if m.group(2) else "65535"
        return lo + ":" + hi
    return v


def port_matches(spec, port):
    """Does a port spec (``"443"``, ``"1000:2000"``) include ``port``? None if unknown (a name)."""
    s = norm_port(spec)
    if s.isdigit():
        return int(s) == port
    m = re.match(r"^(\d+):(\d+)$", s)
    if m:
        return int(m.group(1)) <= port <= int(m.group(2))
    return None


def port_list_matches(spec, port):
    """multiport list ``"80,443,1000:2000"``; None if any element is unknown."""
    unknown = False
    for part in (spec or "").split(","):
        m = port_matches(part, port)
        if m:
            return True
        if m is None:
            unknown = True
    return None if unknown else False


def parse_to_destination(value, default_port=None):
    """``"203.0.113.5:443"`` -> ("203.0.113.5", 443). Without a port the original port is kept,
    so ``default_port`` is returned. Ranges (address or port) and anything else -> None."""
    if not value:
        return None
    v = value.strip()
    ip_s, sep, port_s = v.rpartition(":")
    if not sep:
        ip_s, port_s = v, ""
    try:
        ip = str(ipaddress.IPv4Address(ip_s))
    except ValueError:
        return None
    if port_s == "":
        if default_port is None:
            return None
        return ip, int(default_port)
    if not port_s.isdigit():
        return None
    port = int(port_s)
    if not 1 <= port <= 65535:
        return None
    return ip, port


def _states(value):
    return frozenset(s.strip().upper() for s in (value or "").split(",") if s.strip())


def states_text(states):
    """Canonical order, as iptables prints: NEW,RELATED,ESTABLISHED."""
    known = [s for s in STATE_ORDER if s in states]
    rest = sorted(s for s in states if s not in STATE_ORDER)
    return ",".join(known + rest)


class Rule(object):
    """One ``-A CHAIN ...`` line. ``index`` is the 1-based position in its chain (as
    ``iptables -S CHAIN`` counts), ``lineno`` the 0-based line in a saved file."""

    def __init__(self, line, table=None, index=0, lineno=None):
        self.line = line.rstrip("\r\n")
        self.table = table
        self.index = index
        self.lineno = lineno
        self.counters = None
        self.error = None
        text = self.line.strip()
        m = _COUNTERS_RE.match(text)
        if m:
            self.counters = (int(m.group(1)), int(m.group(2)))
            text = text[m.end():]
        self.text = text
        try:
            self.tokens = split_line(text)
        except ValueError as e:
            self.tokens = text.split()
            self.error = tr("ipt.unclosed_quotes", error=e)
        self.op = self.tokens[0] if self.tokens else ""
        self.chain = self.tokens[1] if len(self.tokens) > 1 else ""
        self.proto = None
        self.proto_neg = False
        self.src = None
        self.src_neg = False
        self.dst = None
        self.dst_neg = False
        self.iif = None
        self.iif_neg = False
        self.oif = None
        self.oif_neg = False
        self.frag = False
        self.frag_neg = False
        self.sport = None
        self.sport_neg = False
        self.dport = None
        self.dport_neg = False
        self.states = None
        self.states_neg = False
        self.comment = None
        self.target = None
        self.goto = False
        self.to_destination = None
        self.target_args = []
        self.matches = []            # module names in the order given (implicit tcp/udp included)
        self.extra = []              # tokens of everything not understood, as -m groups
        self.opts = []               # (module, negated, option, [values]) of every module option
        self._extra_groups = collections.OrderedDict()
        self._parse()
        for mod, toks in self._extra_groups.items():
            self.extra += ["-m", mod] + toks

    # ── parsing ────────────────────────────────────────────────────────────

    def _parse(self):
        toks = self.tokens
        n = len(toks)
        i = 2
        neg = False
        ctx = None                   # module receiving --options, or "<target>"
        while i < n:
            t = toks[i]
            if t == "!":
                neg = not neg
                i += 1
                continue
            if t in _ADDR_OPTS or t in _IFACE_OPTS or t in ("-p", "--protocol"):
                j = i + 1
                if j < n and toks[j] == "!":          # old syntax: -s ! 1.2.3.4
                    neg = not neg
                    j += 1
                val = toks[j] if j < n else None
                if val is None:
                    self.error = self.error or tr("ipt.no_value", opt=t)
                    break
                if t in _ADDR_OPTS:
                    name = _ADDR_OPTS[t]
                    setattr(self, name, norm_addr(val))
                    setattr(self, name + "_neg", neg)
                elif t in _IFACE_OPTS:
                    name = _IFACE_OPTS[t]
                    setattr(self, name, val)
                    setattr(self, name + "_neg", neg)
                else:
                    self.proto = norm_proto(val)
                    self.proto_neg = neg
                neg = False
                i = j + 1
                continue
            if t in ("-f", "--fragment"):
                self.frag, self.frag_neg, neg = True, neg, False
                i += 1
                continue
            if t in ("-m", "--match"):
                mod = toks[i + 1] if i + 1 < n else ""
                self.matches.append(mod)
                ctx = mod
                if mod not in _PORT_MODULES + ("state", "conntrack", "comment"):
                    self._extra_groups.setdefault(mod, [])
                i += 2
                continue
            if t in ("-j", "--jump", "-g", "--goto"):
                self.target = toks[i + 1] if i + 1 < n else ""
                self.goto = t in ("-g", "--goto")
                ctx = "<target>"
                i += 2
                continue
            if t in ("-c", "--set-counters"):
                i += 3
                continue
            if t.startswith("--"):
                arity = _ARITY.get(t)
                j = i + 1
                vals = []
                if arity is None:
                    while j < n and not toks[j].startswith("-") and toks[j] != "!":
                        vals.append(toks[j])
                        j += 1
                else:
                    vals = toks[j:j + arity]
                    j += arity
                self._option(ctx, neg, t, vals)
                neg = False
                i = j
                continue
            # a bare word nobody asked for
            self._extra_groups.setdefault("?", []).append(t)
            i += 1

    def _option(self, ctx, neg, opt, vals):
        val = vals[0] if vals else ""
        if ctx == "<target>":
            self.target_args += (["!"] if neg else []) + [opt] + list(vals)
            if opt == "--to-destination":
                self.to_destination = val
            return
        if opt in _DPORT_OPTS + _SPORT_OPTS and ctx not in _PORT_MODULES:
            # iptables loads the protocol match implicitly for --dport without -m tcp
            ctx = self.proto or "tcp"
            if ctx not in self.matches:
                self.matches.append(ctx)
        self.opts.append((ctx, neg, opt, list(vals)))
        if ctx in _PORT_MODULES and opt in _DPORT_OPTS:
            self.dport, self.dport_neg = norm_port(val), neg
            return
        if ctx in _PORT_MODULES and opt in _SPORT_OPTS:
            self.sport, self.sport_neg = norm_port(val), neg
            return
        if (ctx == "state" and opt == "--state") or (ctx == "conntrack" and opt == "--ctstate"):
            self.states, self.states_neg = _states(val), neg
            return
        if ctx == "comment" and opt == "--comment":
            self.comment = val
            return
        group = self._extra_groups.setdefault(ctx or "?", [])
        group += (["!"] if neg else []) + [opt] + list(vals)

    # ── views ──────────────────────────────────────────────────────────────

    @property
    def port(self):
        """Destination port as int when it is a single port, else None."""
        if self.dport and self.dport.isdigit():
            return int(self.dport)
        return None

    @property
    def label(self):
        """(port, proto) from a ``kaskad:N:P`` comment, or None."""
        m = LABEL_RE.match(self.comment or "")
        if not m:
            return None
        return int(m.group(1)), m.group(2)

    def qualifiers(self):
        """Conditions beyond ``-p P --dport N``, as short printable strings."""
        q = []

        def neg(flag):
            return "! " if flag else ""
        if self.src is not None:
            q.append("%s-s %s" % (neg(self.src_neg), self.src))
        if self.dst is not None:
            q.append("%s-d %s" % (neg(self.dst_neg), self.dst))
        if self.iif is not None:
            q.append("%s-i %s" % (neg(self.iif_neg), self.iif))
        if self.oif is not None:
            q.append("%s-o %s" % (neg(self.oif_neg), self.oif))
        if self.proto_neg:
            q.append("! -p %s" % self.proto)
        if self.frag:
            q.append("%s-f" % neg(self.frag_neg))
        if self.sport is not None:
            q.append("%s--sport %s" % (neg(self.sport_neg), self.sport))
        if self.dport is not None and (self.dport_neg or not self.dport.isdigit()):
            q.append("%s--dport %s" % (neg(self.dport_neg), self.dport))
        if self.states is not None:
            q.append("%s--state %s" % (neg(self.states_neg), states_text(self.states)))
        if self.extra:
            q.append(" ".join(self.extra))
        if self.error:
            q.append(tr("ipt.unparsed", error=self.error))
        return q

    @property
    def simple(self):
        return not self.qualifiers()

    def norm(self):
        """Comparison key: equal for the same rule however iptables printed it."""
        return (
            self.chain,
            ("s", self.src_neg, self.src), ("d", self.dst_neg, self.dst),
            ("i", self.iif_neg, self.iif), ("o", self.oif_neg, self.oif),
            ("p", self.proto_neg, self.proto), ("f", self.frag, self.frag_neg),
            ("sport", self.sport_neg, self.sport), ("dport", self.dport_neg, self.dport),
            ("state", self.states_neg, states_text(self.states) if self.states is not None else None),
            ("comment", self.comment),
            ("extra", tuple(self.extra)),
            ("target", self.goto, self.target, tuple(self.target_args)),
            ("error", self.error and self.text),
        )

    def spec(self):
        """The rule as printed, without ``-A CHAIN``."""
        parts = self.text.split(None, 2)
        if len(parts) == 3 and parts[0] == "-A":
            return parts[2]
        return self.text

    def to_dict(self):
        return {"table": self.table, "chain": self.chain, "index": self.index, "rule": self.text}

    def __repr__(self):
        return "Rule(%s %r)" % (self.table, self.text)


class Chains(collections.OrderedDict):
    """``{chain: [Rule]}`` of one table in the order iptables printed it. ``policies`` holds the
    built-in chain policies; ``notes`` the ``#`` lines iptables added (e.g. "iptables-legacy tables
    present")."""

    def __init__(self, table="filter"):
        super(Chains, self).__init__()
        self.table = table
        self.policies = {}
        self.notes = []

    def all_rules(self):
        for rules in self.values():
            for r in rules:
                yield r

    def add(self, rule):
        lst = self.setdefault(rule.chain, [])
        rule.index = len(lst) + 1
        rule.table = self.table
        lst.append(rule)
        return rule


def parse_S(text, table="filter"):
    """Output of ``iptables [-t T] -S [CHAIN]`` -> :class:`Chains`."""
    ch = Chains(table)
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            ch.notes.append(line)
            continue
        parts = line.split()
        if parts[0] == "-P" and len(parts) >= 3:
            ch.setdefault(parts[1], [])
            ch.policies[parts[1]] = parts[2]
        elif parts[0] == "-N" and len(parts) >= 2:
            ch.setdefault(parts[1], [])
        elif parts[0] == "-A" and len(parts) >= 2:
            ch.add(Rule(line, table))
        else:
            ch.notes.append(line)
    return ch


class SaveSet(dict):
    """``{table: Chains}`` from iptables-save text. ``lines`` keeps the raw lines (with their line
    endings) so a file can be rewritten with only some lines left out; ``problems`` lists the
    0-based numbers of lines that are not iptables-save syntax."""

    def __init__(self):
        super(SaveSet, self).__init__()
        self.lines = []
        self.problems = []
        self.notes = []

    @property
    def policies(self):
        return dict((t, dict(c.policies)) for t, c in self.items())

    def all_rules(self):
        for t in self:
            for r in self[t].all_rules():
                yield r


def split_keepends(text):
    """Lines with their "\\n" (only "\\n" ends a line, as for iptables-restore)."""
    out = []
    start = 0
    while start < len(text):
        i = text.find("\n", start)
        if i < 0:
            out.append(text[start:])
            break
        out.append(text[start:i + 1])
        start = i + 1
    return out


def parse_save(text):
    """iptables-save text (also /etc/iptables/rules.v4) -> :class:`SaveSet`."""
    ss = SaveSet()
    table = None
    for i, raw in enumerate(split_keepends(text or "")):
        ss.lines.append(raw)
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            ss.notes.append(line)
            continue
        if line.startswith("*"):
            table = line[1:].strip()
            ss.setdefault(table, Chains(table))
            continue
        if line == "COMMIT":
            table = None
            continue
        if table is None:
            ss.problems.append(i)
            continue
        if line.startswith(":"):
            parts = line[1:].split()
            if parts:
                ss[table].setdefault(parts[0], [])
                if len(parts) > 1 and parts[1] != "-":
                    ss[table].policies[parts[0]] = parts[1]
            continue
        body = _COUNTERS_RE.sub("", line)
        if body.startswith("-A "):
            ss[table].add(Rule(line, table, lineno=i))
        else:
            ss.problems.append(i)
    return ss


def multiset(rules):
    """Counter of (table, norm) over rules."""
    c = collections.Counter()
    for r in rules:
        c[(r.table, r.norm())] += 1
    return c


# ── commands ───────────────────────────────────────────────────────────────

def delete_line(rule):
    """The ``-D`` line for ``iptables-restore --noflush`` that removes exactly this rule."""
    if not rule.text.startswith("-A "):
        raise ValueError("not an -A line: %r" % rule.text)
    return "-D " + rule.text[3:]


def restore_input(table, lines):
    """iptables-restore input for one table: ``*table``, the lines, ``COMMIT``. No chain
    declarations: with --noflush a ``:CHAIN`` line would flush an existing user chain."""
    return "*%s\n%s\nCOMMIT\n" % (table, "\n".join(lines))


def backend(runner):
    """{"backend": "nf_tables"|"legacy"|"none"|"unknown", "version": "1.8.9", "text": ...}."""
    if not runner.which("iptables"):
        return {"backend": "none", "version": "", "text": ""}
    r = runner.run(["iptables", "-V"])
    text = (r.out or r.err or "").strip()
    m = re.search(r"v(\d+(?:\.\d+)*)", text)
    if "nf_tables" in text:
        be = "nf_tables"
    elif "legacy" in text or (r.ok and m):
        be = "legacy"
    else:
        be = "unknown"
    return {"backend": be, "version": m.group(1) if m else "", "text": text}


def _wait(be):
    # iptables-legacy takes the xtables lock; iptables-nft does not need -w
    return ["-w", "5"] if be == "legacy" else []


def list_table(runner, table, be="nf_tables"):
    """``iptables -t T -S`` -> Chains. Raises KaskadError if iptables refuses."""
    r = runner.run(["iptables"] + _wait(be) + ["-t", table, "-S"])
    if not r.ok:
        raise KaskadError(tr("ipt.list_failed", table=table, error=(r.err or r.out).strip()[:300]))
    return parse_S(r.out, table)


def save_all(runner):
    """Full ``iptables-save`` text (raises KaskadError on failure)."""
    r = runner.run(["iptables-save"])
    if not r.ok:
        raise KaskadError(tr("ipt.save_failed", error=(r.err or r.out).strip()[:300]))
    return r.out


def restore_noflush(runner, table, lines, be="nf_tables"):
    """One transaction: ``iptables-restore --noflush`` with ``lines`` for ``table``. Returns the
    Result (the caller decides what a failure means)."""
    argv = ["iptables-restore"] + (["-w", "10"] if be == "legacy" else []) + ["--noflush"]
    return runner.run(argv, input=restore_input(table, lines), timeout=120)


def restore_table(runner, table, save_text, be="nf_tables"):
    """Replace ``table`` completely with its part of ``save_text`` (``iptables-restore -T``)."""
    argv = ["iptables-restore"] + (["-w", "10"] if be == "legacy" else []) + ["-T", table]
    return runner.run(argv, input=save_text, timeout=120)

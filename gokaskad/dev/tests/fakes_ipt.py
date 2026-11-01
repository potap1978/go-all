"""A stateful iptables emulator for FakeRunner (tests only, work package 2).

Deliberately independent from ``kaskad.ipt`` (its own tokenizer, parser and printer), so a bug in
the production parser cannot hide behind the same bug here.

What it models (iptables 1.8, nf_tables backend by default, ``legacy`` on request):

* tables filter / nat / mangle with built-in chains, policies and user chains;
* ``iptables [-w [N]] [-t T] -S [CHAIN] | -A | -I CHAIN [N] | -D CHAIN (spec|N) | -C | -N | -X |
  -F | -P | -V``;
* ``iptables-save`` and ``iptables-restore [-w [N]] [--noflush|-n] [-T T] [--test]`` with the input
  on stdin, one atomic transaction per table (an error leaves that table untouched, tables
  committed before stay, like the real tool);
* output in the canonical form iptables prints: ``-s/-d/-i/-o/-p`` first, ``-m tcp`` added for
  ``--dport``, host addresses with ``/32``, ``--state`` flags in kernel order
  (NEW,RELATED,ESTABLISHED), comments quoted the way ``xtables_save_string`` does it (bare only if
  every character is ``[A-Za-z0-9_-]``) and — on nf_tables — the comment after all other matches;
* DNAT/MASQUERADE only in the nat table, jumps only to existing chains, ``-D``/``-C`` compare the
  canonical rule (so quoting/order on the command line does not matter, as in the kernel).

Every call is appended to ``self.log``; mutating calls also to ``events`` (a list shared with the
other fakes, to check the order of steps). ``inject(kind, fn)`` lets a test make a call fail.
"""

import copy
import ipaddress

BUILTIN = {
    "filter": ("INPUT", "FORWARD", "OUTPUT"),
    "nat": ("PREROUTING", "INPUT", "OUTPUT", "POSTROUTING"),
    "mangle": ("PREROUTING", "INPUT", "FORWARD", "OUTPUT", "POSTROUTING"),
    "raw": ("PREROUTING", "OUTPUT"),
}
TABLE_ORDER = ("filter", "nat", "mangle", "raw")
STD_TARGETS = ("ACCEPT", "DROP", "RETURN", "QUEUE")
NAT_TARGETS = {"DNAT": ("PREROUTING", "OUTPUT"), "REDIRECT": ("PREROUTING", "OUTPUT"),
               "SNAT": ("POSTROUTING", "INPUT"), "MASQUERADE": ("POSTROUTING",),
               "NETMAP": ("PREROUTING", "OUTPUT", "POSTROUTING", "INPUT")}
EXT_TARGETS = ("LOG", "REJECT", "MARK", "CONNMARK", "TCPMSS", "NFLOG", "CT", "TRACE", "NFQUEUE",
               "CHECKSUM", "NOTRACK")

MATCH_OPTS = {
    "tcp": ("--sport", "--source-port", "--dport", "--destination-port", "--tcp-flags", "--syn",
            "--tcp-option"),
    "udp": ("--sport", "--source-port", "--dport", "--destination-port"),
    "state": ("--state",),
    "conntrack": ("--ctstate", "--ctproto", "--ctorigsrc", "--ctorigdst", "--ctreplsrc",
                  "--ctrepldst", "--ctorigsrcport", "--ctorigdstport", "--ctreplsrcport",
                  "--ctrepldstport", "--ctstatus", "--ctexpire", "--ctdir"),
    "comment": ("--comment",),
    "multiport": ("--sports", "--source-ports", "--dports", "--destination-ports", "--ports"),
    "addrtype": ("--src-type", "--dst-type", "--limit-iface-in", "--limit-iface-out"),
    "connmark": ("--mark",),
    "mark": ("--mark",),
    "limit": ("--limit", "--limit-burst"),
    "icmp": ("--icmp-type",),
    "physdev": ("--physdev-in", "--physdev-out", "--physdev-is-in", "--physdev-is-out",
                "--physdev-is-bridged"),
    "pkttype": ("--pkt-type",),
    "recent": ("--name", "--set", "--update", "--rcheck", "--remove", "--seconds", "--hitcount",
               "--rsource", "--rdest", "--mask"),
}
TARGET_OPTS = {
    "DNAT": ("--to-destination", "--random", "--persistent"),
    "SNAT": ("--to-source", "--random", "--random-fully", "--persistent"),
    "MASQUERADE": ("--to-ports", "--random", "--random-fully"),
    "REDIRECT": ("--to-ports", "--random"),
    "NETMAP": ("--to",),
    "REJECT": ("--reject-with",),
    "LOG": ("--log-prefix", "--log-level", "--log-tcp-sequence", "--log-tcp-options",
            "--log-ip-options", "--log-uid"),
    "MARK": ("--set-mark", "--set-xmark", "--and-mark", "--or-mark", "--xor-mark"),
    "CONNMARK": ("--set-mark", "--set-xmark", "--save-mark", "--restore-mark", "--nfmask",
                 "--ctmask", "--mask"),
    "TCPMSS": ("--set-mss", "--clamp-mss-to-pmtu"),
}
ARITY = {"--tcp-flags": 2, "--syn": 0, "--random": 0, "--random-fully": 0, "--persistent": 0,
         "--save-mark": 0, "--restore-mark": 0, "--clamp-mss-to-pmtu": 0, "--set": 0,
         "--update": 0, "--rcheck": 0, "--remove": 0, "--rsource": 0, "--rdest": 0,
         "--physdev-is-in": 0, "--physdev-is-out": 0, "--physdev-is-bridged": 0,
         "--limit-iface-in": 0, "--limit-iface-out": 0, "--log-tcp-sequence": 0,
         "--log-tcp-options": 0, "--log-ip-options": 0, "--log-uid": 0}
STATE_ORDER = ("INVALID", "NEW", "RELATED", "ESTABLISHED", "UNTRACKED", "SNAT", "DNAT")
NOQUOTE = set("_-0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
PROTO_NUM = {"6": "tcp", "17": "udp", "1": "icmp"}


class IptFail(Exception):
    def __init__(self, msg, rc=1):
        super(IptFail, self).__init__(msg)
        self.rc = rc


def ipt_tokenize(line):
    """Replica of iptables' add_param_to_argv (how iptables-restore splits a line)."""
    args, buf = [], []
    quote = esc = False
    for ch in line:
        if quote:
            if esc:
                buf.append(ch)
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch != '"':
                buf.append(ch)
                continue
            quote = False                      # closing quote ends the parameter
            args.append("".join(buf))
            buf = []
            continue
        if ch == '"':
            quote = True
            continue
        if ch in " \t\n\r":
            if buf:
                args.append("".join(buf))
                buf = []
            continue
        buf.append(ch)
    if buf:
        args.append("".join(buf))
    return args


def save_string(value):
    """Replica of xtables_save_string: bare when safe, else double-quoted with \\ escapes."""
    if value and all(c in NOQUOTE for c in value):
        return value
    out = ['"']
    for c in value:
        if c in '"\\\'':
            out.append("\\")
        out.append(c)
    out.append('"')
    return "".join(out)


def _addr(value):
    try:
        net = ipaddress.IPv4Network(value, strict=False)
    except ValueError:
        raise IptFail("host/network `%s' not found" % value, 2)
    return None if net.prefixlen == 0 else str(net)


def _port(value):
    v = value.strip()
    if v.isdigit():
        return str(int(v))
    if ":" in v:
        lo, hi = v.split(":", 1)
        return "%s:%s" % (int(lo or 0), int(hi or 65535))
    raise IptFail("invalid port/service `%s' specified" % value, 2)


def _states(value):
    parts = [p.strip().upper() for p in value.split(",") if p.strip()]
    for p in parts:
        if p not in STATE_ORDER:
            raise IptFail("Bad ctstate \"%s\"" % p, 2)
    return ",".join(s for s in STATE_ORDER if s in parts)


class Spec(object):
    """A parsed rule (everything after ``-A CHAIN``)."""

    def __init__(self):
        self.src = self.dst = self.iif = self.oif = self.proto = None
        self.neg = {}                     # field -> True
        self.matches = []                 # [name, [tokens]]
        self.target = None
        self.goto = False
        self.topts = []

    def _match(self, name):
        for m in self.matches:
            if m[0] == name:
                return m
        m = [name, []]
        self.matches.append(m)
        return m

    def text(self, nft=True):
        out = []
        for f, opt in (("src", "-s"), ("dst", "-d"), ("iif", "-i"), ("oif", "-o"), ("proto", "-p")):
            v = getattr(self, f)
            if v is None:
                continue
            if f in ("src", "dst") and "/" not in v:
                v += "/32"
            out += (["!"] if self.neg.get(f) else []) + [opt, v]
        comment = []
        for name, toks in self.matches:
            if name == "comment" and nft:
                comment = ["-m", "comment"] + toks
                continue
            out += ["-m", name] + toks
        out += comment
        if self.target:
            out += ["-g" if self.goto else "-j", self.target] + self.topts
        return " ".join(out)


def parse_spec(toks, table, chain, tables):
    """iptables command-line semantics for a rule spec (list of tokens)."""
    sp = Spec()
    i, n, neg = 0, len(toks), False
    raw_matches = []                       # [name, [(neg, opt, vals)]]
    implicit = None

    def need(k):
        if k >= n:
            raise IptFail("option \"%s\" requires an argument" % toks[k - 1], 2)
        return toks[k]

    while i < n:
        t = toks[i]
        if t == "!":
            neg = True
            i += 1
            continue
        if t in ("-s", "--source", "-d", "--destination", "-i", "--in-interface", "-o",
                 "--out-interface", "-p", "--protocol"):
            v = need(i + 1)
            f = {"-s": "src", "--source": "src", "-d": "dst", "--destination": "dst", "-i": "iif",
                 "--in-interface": "iif", "-o": "oif", "--out-interface": "oif", "-p": "proto",
                 "--protocol": "proto"}[t]
            if f in ("src", "dst"):
                v = _addr(v)
                if v is not None and v.endswith("/32"):
                    v = v[:-3]
            elif f == "proto":
                v = PROTO_NUM.get(v.lower(), v.lower())
                if v == "all":
                    v = None
            setattr(sp, f, v)
            if neg:
                sp.neg[f] = True
            neg = False
            i += 2
            continue
        if t in ("-m", "--match"):
            name = need(i + 1)
            if name not in MATCH_OPTS:
                raise IptFail("Couldn’t load match `%s':No such file or directory" % name, 2)
            raw_matches.append([name, []])
            i += 2
            continue
        if t in ("-j", "--jump", "-g", "--goto"):
            sp.target = need(i + 1)
            sp.goto = t in ("-g", "--goto")
            i += 2
            continue
        if t.startswith("--"):
            arity = ARITY.get(t, 1)
            vals = toks[i + 1:i + 1 + arity]
            if len(vals) < arity:
                raise IptFail("option \"%s\" requires an argument" % t, 2)
            owner = None
            if sp.target and t in TARGET_OPTS.get(sp.target, ()):
                owner = "<target>"
            else:
                for m in reversed(raw_matches):
                    if t in MATCH_OPTS[m[0]]:
                        owner = m
                        break
                if owner is None and sp.proto in ("tcp", "udp") and t in MATCH_OPTS[sp.proto]:
                    if implicit is None:
                        implicit = [sp.proto, []]
                        raw_matches.append(implicit)
                    owner = implicit
            if owner is None:
                raise IptFail("unknown option \"%s\"" % t, 2)
            if owner == "<target>":
                if t == "--log-prefix":
                    vals = [save_string(vals[0])]
                sp.topts += [t] + vals
            else:
                owner[1].append((neg, t, vals))
            neg = False
            i += 1 + arity
            continue
        raise IptFail("Bad argument `%s'" % t, 2)

    # canonical printing of every match
    for name, opts in raw_matches:
        toks_out = []
        if name in ("tcp", "udp"):
            order = {"--sport": 0, "--source-port": 0, "--dport": 1, "--destination-port": 1}
            for ng, opt, vals in sorted(opts, key=lambda o: order.get(o[1], 2)):
                if opt in ("--sport", "--source-port"):
                    toks_out += (["!"] if ng else []) + ["--sport", _port(vals[0])]
                elif opt in ("--dport", "--destination-port"):
                    toks_out += (["!"] if ng else []) + ["--dport", _port(vals[0])]
                else:
                    toks_out += (["!"] if ng else []) + [opt] + vals
        else:
            for ng, opt, vals in opts:
                if opt in ("--state", "--ctstate"):
                    vals = [_states(vals[0])]
                elif opt == "--comment":
                    vals = [save_string(vals[0])]
                toks_out += (["!"] if ng else []) + [opt] + vals
        sp.matches.append([name, toks_out])

    if sp.target == "REJECT" and "--reject-with" not in sp.topts:
        sp.topts += ["--reject-with", "icmp-port-unreachable"]
    _check_target(sp, table, chain, tables)
    return sp


def _check_target(sp, table, chain, tables):
    tg = sp.target
    if tg is None or tg in STD_TARGETS or tg in EXT_TARGETS:
        return
    if tg in NAT_TARGETS:
        if table != "nat":
            raise IptFail("RULE_APPEND failed (Invalid argument): %s target only valid in nat table" % tg, 4)
        if chain in BUILTIN["nat"] and chain not in NAT_TARGETS[tg]:
            raise IptFail("RULE_APPEND failed (Invalid argument): %s not valid in %s" % (tg, chain), 4)
        if tg == "DNAT" and "--to-destination" not in sp.topts:
            raise IptFail("DNAT: you must specify --to-destination", 2)
        return
    if tg not in tables.get(table, {}):
        raise IptFail("Chain '%s' does not exist" % tg, 2)


class FakeIptables(object):
    def __init__(self, backend="nf_tables", version="1.8.9", events=None):
        self.backend = backend
        self.version = version
        self.events = events if events is not None else []
        self.log = []
        self.inject_fns = []
        self.tables = {}
        for t in ("filter", "nat"):
            self._ensure_table(t)

    # ── state helpers ──────────────────────────────────────────────────────

    @property
    def nft(self):
        return self.backend == "nf_tables"

    def _ensure_table(self, t, tables=None):
        tables = self.tables if tables is None else tables
        if t not in tables:
            if t not in BUILTIN:
                raise IptFail("can’t initialize iptables table `%s': Table does not exist" % t, 3)
            tables[t] = {"chains": dict((c, {"policy": "ACCEPT", "rules": []}) for c in BUILTIN[t])}
        return tables[t]

    def _chains(self, t, tables=None):
        tb = self._ensure_table(t, tables)
        builtin = [c for c in BUILTIN[t] if c in tb["chains"]]
        user = sorted(c for c in tb["chains"] if c not in BUILTIN[t])
        return tb, builtin + user

    def state(self):
        """Deep copy of all tables, for 'nothing changed' comparisons."""
        return copy.deepcopy(dict((t, [(c, ch["policy"], [r.text(self.nft) for r in ch["rules"]])
                                       for c, ch in sorted(self.tables[t]["chains"].items())])
                                  for t in self.tables))

    def rules(self, table, chain):
        return ["-A %s %s" % (chain, r.text(self.nft)) for r in self.tables[table]["chains"][chain]["rules"]]

    def inject(self, fn):
        """fn(argv, input) -> None or (rc, out, err): a non-None result replaces the real answer."""
        self.inject_fns.append(fn)

    # ── output ────────────────────────────────────────────────────────────

    def list_S(self, table, chain=None, tables=None):
        tb, order = self._chains(table, tables)
        out = []
        if chain is not None:
            if chain not in tb["chains"]:
                raise IptFail("iptables: No chain/target/match by that name.", 1)
            order = [chain]
        for c in order:
            pol = tb["chains"][c]["policy"]
            out.append("-P %s %s" % (c, pol) if c in BUILTIN[table] else "-N %s" % c)
        for c in order:
            for r in tb["chains"][c]["rules"]:
                out.append("-A %s %s" % (c, r.text(self.nft)))
        return "".join(l + "\n" for l in out)

    def save_text(self, only=None):
        stamp = "Sat Oct  4 12:00:00 2026"
        head = "iptables-save v%s%s" % (self.version, " (nf_tables)" if self.nft else "")
        out = []
        for t in TABLE_ORDER:
            if t not in self.tables or (only and t != only):
                continue
            tb, order = self._chains(t)
            out.append("# Generated by %s on %s" % (head, stamp))
            out.append("*%s" % t)
            for c in order:
                pol = tb["chains"][c]["policy"] if c in BUILTIN[t] else "-"
                out.append(":%s %s [0:0]" % (c, pol))
            for c in order:
                for r in tb["chains"][c]["rules"]:
                    out.append("-A %s %s" % (c, r.text(self.nft)))
            out.append("COMMIT")
            out.append("# Completed on %s" % stamp)
        return "".join(l + "\n" for l in out)

    # ── commands on a table dict ──────────────────────────────────────────

    def _exec(self, tables, table, toks):
        """Run one command (tokens after the table selection). Raises IptFail."""
        if not toks:
            raise IptFail("no command specified", 2)
        cmd = toks[0]
        tb = self._ensure_table(table, tables)
        chains = tb["chains"]

        def chain_arg(k=1):
            if len(toks) <= k:
                raise IptFail("option \"%s\" requires an argument" % cmd, 2)
            c = toks[k]
            if c not in chains:
                raise IptFail("iptables: No chain/target/match by that name.", 1)
            return c

        tabs = dict((t, tables[t]["chains"]) for t in tables)
        if cmd in ("-A", "--append"):
            c = chain_arg()
            chains[c]["rules"].append(parse_spec(toks[2:], table, c, tabs))
            return ""
        if cmd in ("-I", "--insert"):
            c = chain_arg()
            rest = toks[2:]
            pos = 1
            if rest and rest[0].isdigit():
                pos = int(rest[0])
                rest = rest[1:]
            sp = parse_spec(rest, table, c, tabs)
            lst = chains[c]["rules"]
            if pos < 1 or pos > len(lst) + 1:
                raise IptFail("iptables: Index of insertion too big.", 1)
            lst.insert(pos - 1, sp)
            return ""
        if cmd in ("-D", "--delete", "-C", "--check"):
            c = chain_arg()
            rest = toks[2:]
            lst = chains[c]["rules"]
            if cmd in ("-D", "--delete") and len(rest) == 1 and rest[0].isdigit():
                k = int(rest[0])
                if not 1 <= k <= len(lst):
                    raise IptFail("iptables: Index of deletion too big.", 1)
                del lst[k - 1]
                return ""
            want = parse_spec(rest, table, c, tabs).text(self.nft)
            for k, r in enumerate(lst):
                if r.text(self.nft) == want:
                    if cmd in ("-D", "--delete"):
                        del lst[k]
                    return ""
            raise IptFail("iptables: Bad rule (does a matching rule exist in that chain?).", 1)
        if cmd in ("-N", "--new-chain"):
            if len(toks) < 2:
                raise IptFail("-N requires a chain name", 2)
            if toks[1] in chains:
                raise IptFail("iptables: Chain already exists.", 1)
            chains[toks[1]] = {"policy": None, "rules": []}
            return ""
        if cmd in ("-X", "--delete-chain"):
            names = [toks[1]] if len(toks) > 1 else [c for c in chains if c not in BUILTIN[table]]
            for name in names:
                if name not in chains or name in BUILTIN[table]:
                    raise IptFail("iptables: No chain/target/match by that name.", 1)
                for other in chains.values():
                    if any(r.target == name for r in other["rules"]):
                        raise IptFail("iptables: Too many links.", 1)
                if chains[name]["rules"]:
                    raise IptFail("iptables: Directory not empty.", 1)
                del chains[name]
            return ""
        if cmd in ("-F", "--flush"):
            names = [chain_arg()] if len(toks) > 1 else list(chains)
            for name in names:
                chains[name]["rules"] = []
            return ""
        if cmd in ("-P", "--policy"):
            c = chain_arg()
            if c not in BUILTIN[table] or len(toks) < 3 or toks[2] not in ("ACCEPT", "DROP"):
                raise IptFail("iptables: Bad built-in chain name.", 1)
            chains[c]["policy"] = toks[2]
            return ""
        if cmd in ("-S", "--list-rules"):
            return self.list_S(table, toks[1] if len(toks) > 1 else None, tables)
        if cmd in ("-Z", "--zero"):
            return ""
        raise IptFail("unknown command %s" % cmd, 2)

    # ── FakeRunner handlers ───────────────────────────────────────────────

    def install(self, runner):
        runner.on(["iptables-save"], self.h_save)
        runner.on(["iptables-restore"], self.h_restore)
        runner.on(["iptables"], self.h_iptables)
        runner.binaries.update(["iptables", "iptables-save", "iptables-restore"])
        return self

    def _injected(self, argv, inp):
        for fn in self.inject_fns:
            res = fn(argv, inp)
            if res is not None:
                return res
        return None

    def h_iptables(self, argv, inp):
        self.log.append(list(argv))
        res = self._injected(argv, inp)
        if res is not None:
            return res
        toks = argv[1:]
        table = "filter"
        rest = []
        i = 0
        while i < len(toks):
            t = toks[i]
            if t in ("-w", "--wait"):
                i += 2 if i + 1 < len(toks) and toks[i + 1].isdigit() else 1
                continue
            if t in ("-t", "--table"):
                table = toks[i + 1]
                i += 2
                continue
            rest = toks[i:]
            break
        if rest[:1] in (["-V"], ["--version"]):
            return 0, "iptables v%s%s\n" % (self.version, " (nf_tables)" if self.nft else " (legacy)"), ""
        try:
            work = copy.deepcopy(self.tables)
            out = self._exec(work, table, rest)
        except IptFail as e:
            return e.rc, "", "iptables v%s: %s\n" % (self.version, e)
        if rest and rest[0] not in ("-S", "--list-rules", "-C", "--check"):
            self.tables = work
            self.events.append(("iptables", table, " ".join(rest)))
        return 0, out, ""

    def h_save(self, argv, inp):
        self.log.append(list(argv))
        res = self._injected(argv, inp)
        if res is not None:
            return res
        only = argv[argv.index("-t") + 1] if "-t" in argv else None
        self.events.append(("iptables-save",))
        return 0, self.save_text(only), ""

    def h_restore(self, argv, inp):
        self.log.append(list(argv))
        res = self._injected(argv, inp)
        if res is not None:
            return res
        noflush = test = False
        only = None
        i = 1
        while i < len(argv):
            a = argv[i]
            if a in ("-n", "--noflush"):
                noflush = True
            elif a in ("-t", "--test"):
                test = True
            elif a in ("-T", "--table"):
                only = argv[i + 1]
                i += 1
            elif a in ("-w", "--wait"):
                if i + 1 < len(argv) and argv[i + 1].isdigit():
                    i += 1
            elif a in ("-c", "--counters", "-v", "--verbose"):
                pass
            else:
                return 2, "", "iptables-restore: unknown option %s\n" % a
            i += 1
        rc, err = self.restore(inp or "", noflush=noflush, only=only, test=test)
        return rc, "", err

    def restore(self, text, noflush=False, only=None, test=False):
        """Process restore input. Returns (rc, err)."""
        table = None
        work = None
        skip = False
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if table is None:
                if not line.startswith("*"):
                    return 1, "iptables-restore: line %d failed\n" % lineno
                table = line[1:].strip()
                skip = only is not None and table != only
                if skip:
                    continue
                work = copy.deepcopy(self.tables)
                try:
                    self._ensure_table(table, work)
                except IptFail as e:
                    return 1, "iptables-restore: line %d failed: %s\n" % (lineno, e)
                if not noflush:
                    work[table] = {"chains": dict((c, {"policy": "ACCEPT", "rules": []})
                                                  for c in BUILTIN[table])}
                continue
            if line == "COMMIT":
                if not skip and not test:
                    self.tables = work
                    self.events.append(("iptables-restore", table, "noflush" if noflush else "full"))
                table = None
                skip = False
                continue
            if skip:
                continue
            try:
                if line.startswith(":"):
                    parts = line[1:].split()
                    name, pol = parts[0], parts[1] if len(parts) > 1 else "-"
                    chains = work[table]["chains"]
                    if name in BUILTIN[table]:
                        if pol != "-":
                            chains[name]["policy"] = pol
                    elif name in chains:
                        chains[name]["rules"] = []      # noflush: declaring flushes the user chain
                    else:
                        chains[name] = {"policy": None, "rules": []}
                    continue
                self._exec(work, table, ipt_tokenize(line))
            except IptFail as e:
                return 1, "iptables-restore v%s: %s\nError occurred at line: %d\n" % (self.version, e, lineno)
        if table is not None and not skip:
            return 1, "iptables-restore: COMMIT expected at line %d\n" % (lineno + 1)
        return 0, ""

    def load(self, save_text):
        """Initialise from an iptables-save text (fixture). Raises on any error."""
        self.tables = {}
        for t in ("filter", "nat"):
            self._ensure_table(t)
        rc, err = self.restore(save_text)
        if rc:
            raise AssertionError("fixture does not load: %s" % err)
        return self

    def cmd(self, *argv_tail):
        """Run ``iptables <argv_tail>`` directly (test setup); raises on failure."""
        rc, out, err = self.h_iptables(["iptables"] + list(argv_tail), None)
        if rc:
            raise AssertionError("iptables %s: %s" % (" ".join(argv_tail), err))
        return out

    def run_bash_line(self, line):
        """Execute one 2.x-style shell line ``iptables -t nat -A ... "quoted"``."""
        toks = ipt_tokenize(line)
        assert toks and toks[0] == "iptables", line
        return self.cmd(*toks[1:])

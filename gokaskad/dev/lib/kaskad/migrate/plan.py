"""``kaskadctl migrate plan`` — what ``migrate apply`` would do (SPEC §4.4–4.5), blockers, and the
Russian preview text (§4.11). Pure computation over a scan ``Inventory``; changes nothing.

What is taken out of iptables — only rules of keys with status ``import`` / ``duplicate``:

* their DNAT (any class);
* pro / pro-legacy: INPUT and FORWARD rules labeled ``kaskad:N:P``;
* free: unlabeled INPUT ``-p P [-m P] --dport N -j ACCEPT`` and the unlabeled FORWARD pair to
  ``IP:OUT``;
* a FORWARD rule (labeled or not) stays if a DNAT that remains (foreign, shadowed, skipped)
  points to the same target — that DNAT may rely on it under a FORWARD DROP policy.

``kaskad:masq`` always stays (in iptables and in the persisted file): it masquerades everything
that leaves the interface, and another VPN on the server may rely on it. A return to 2.13 finds
it and does not add a second one.

The persisted file (rules.v4 / sysconfig) loses exactly the removed rules (token comparison, with
multiplicity) plus Kaskad rules that exist only in the file (they would come back at boot);
every other byte stays.

Kaskad forwards that exist ONLY in the persisted file (the live rules were flushed, or not restored
after a reboot) are the user's configured cascades — 2.x brings them back at boot. On a switch they
are imported (nftables brings them up at once; the preview says they do not work right now) and
their lines leave the file. One whose port a live rule catches now, or that would not work as it
is (narrowed by hand, a target Kaskad 3 refuses...), stays in the file untouched; dead ones (a
second rule on the port, a port taken by an earlier rule of the file) go as stale lines. Nothing of
this happens silently: the menu switches without a preview only when there is nothing to show.

After «Вернуть iptables» state.json keeps the forwards (engine legacy). If a later switch finds
no Kaskad rule at all — neither in iptables nor in the persisted file (no netfilter-persistent on
Debian 12+/Ubuntu 24.04+: a reboot wiped them) — those forwards are offered back (preview: «из
прошлой настройки Kaskad 3») instead of being overwritten with an empty list. One whose port a
live rule catches now is not taken back.
"""

import collections
import ipaddress

from .. import errors, ipt, state, util
from ..i18n import tr, trn
from . import scan as scan_mod

# the words are looked up when used (the language is known only then): tr("migrate.origin.<class>")
ORIGIN_CLASSES = ("free", "pro", "pro-legacy", "k3")


def masq_reason():
    return tr("migrate.masq_reason")


def state_words():
    return tr("migrate.state_words")


def origin_word(cls):
    """«из бесплатной версии»… of a class of rules; an unknown class as it is."""
    return tr("migrate.origin." + cls) if cls in ORIGIN_CLASSES else cls


def forwards_word(n):
    return trn("migrate.word.forwards", n)


def rules_word(n):
    return trn("migrate.word.rules", n)


# ── references: which FORWARD rules a remaining DNAT may rely on ──────────

def _ip_int(s):
    try:
        return int(ipaddress.IPv4Address(s))
    except ValueError:
        return None


def dest_ref(r):
    """(proto|None, ip_lo, ip_hi, port_lo, port_hi) of where a DNAT sends packets; None ends = any."""
    if r.target != "DNAT":
        return None
    v = (r.to_destination or "").strip()
    if not v:
        return None
    ip_part, sep, port_part = v.partition(":")
    lo_s, _, hi_s = ip_part.partition("-")
    lo, hi = _ip_int(lo_s), _ip_int(hi_s or lo_s)
    if lo is None or hi is None:
        lo = hi = None
    plo = phi = None
    if sep and port_part:
        a, _, b = port_part.partition("-")
        if a.isdigit():
            plo = int(a)
            phi = int(b) if b.isdigit() else plo
    elif r.port is not None:
        plo = phi = r.port
    return (r.proto, lo, hi, plo, phi)


def referenced(fr, refs):
    """Is FORWARD rule ``fr`` (towards or back from a target) needed by one of ``refs``?"""
    ip = fr.dst if fr.dst is not None else fr.src
    port_s = fr.dport if fr.dport is not None else fr.sport
    ipn = _ip_int(ip or "")
    port = int(port_s) if port_s and port_s.isdigit() else None
    for proto, lo, hi, plo, phi in refs:
        if proto is not None and fr.proto is not None and proto != fr.proto:
            continue
        if lo is not None and (ipn is None or not lo <= ipn <= hi):
            continue
        if plo is not None and (port is None or not plo <= port <= phi):
            continue
        return True
    return False


class Plan(object):
    def __init__(self, inv):
        self.inv = inv
        self.mode = "adopt" if inv.engine == "nft" else "switch"
        self.imports = []
        self.forwards = []             # the state forwards after apply
        self.remove = {"nat": [], "filter": []}
        self.kept_ref = []             # (Rule, referencing DNAT text)
        self.masq_kept = []
        self.persisted_lines = []      # 0-based line numbers to drop from the persisted file
        self.persisted_stale = []      # Rules that exist only in the file
        self.file_imports = []         # Candidates found only in the persisted file, imported (switch)
        self.file_kept = []            # Candidates found only in the persisted file, left in it
        self.state_restores = []       # Restored: forwards of state.json offered back (switch)
        self.state_kept = []           # (forward, text of the live rule that catches its port now)
        self.persisted_removed = collections.Counter()   # (table, norm) -> count, for verification
        self.blockers = []
        self.warnings = []
        self.install_nft = False

    # ── views ──────────────────────────────────────────────────────────────

    @property
    def removed_rules(self):
        return self.remove["nat"] + self.remove["filter"]

    @property
    def nothing_to_do(self):
        """Engine nft and nothing new in iptables: no snapshot, exit 0."""
        return (self.mode == "adopt" and not self.imports and not self.removed_rules
                and not self.persisted_lines)

    @property
    def blocked(self):
        return bool(self.blockers)

    def blocker_code(self):
        codes = [b["code"] for b in self.blockers]
        for c in (errors.UNSUPPORTED, errors.CONFLICT, errors.ERROR):
            if c in codes:
                return c
        return errors.ERROR

    def removal_lines(self, table):
        return [ipt.delete_line(r) for r in self.remove[table]]

    def summary(self):
        inv = self.inv
        by = collections.Counter(c.status for c in inv.cands)
        return {
            "mode": self.mode,
            "kaskad_rules": len(inv.cands),
            "import": len(self.imports),
            "duplicate": by.get("duplicate", 0),
            "shadowed": by.get("shadowed", 0),
            "skipped": by.get("skipped", 0),
            "remove_rules": len(self.removed_rules),
            "persisted_lines": len(self.persisted_lines),
            "persisted_import": len(self.file_imports),
            "persisted_kept": len(self.file_kept),
            "state_restore": len(self.state_restores),
            "warnings": len(self.warnings),
            "has_work": self.mode == "switch" or not self.nothing_to_do,
            "blocked": self.blocked,
        }

    def to_dict(self):
        return {
            "ok": not self.blocked,
            "mode": self.mode,
            "nothing_to_do": self.nothing_to_do,
            "summary": self.summary(),
            "blockers": list(self.blockers),
            "install_nft": self.install_nft,
            "import": [c.to_dict() for c in self.imports],
            "forwards": self.forwards,
            "remove": {"nat": [r.text for r in self.remove["nat"]],
                       "filter": [r.text for r in self.remove["filter"]]},
            "kept": [{"rule": r.text, "needed_by": ref} for r, ref in self.kept_ref] +
                    [{"rule": r.text, "needed_by": masq_reason()} for r in self.masq_kept],
            "persisted": {"file": scan_mod.display(self.inv.persisted.path) if self.inv.persisted else "",
                          "remove_lines": [n + 1 for n in self.persisted_lines],
                          "stale": [r.text for r in self.persisted_stale],
                          "kept": [c.to_dict() for c in self.file_kept]},
            "warnings": list(self.warnings),
            "actions": self.actions(),
            "preview": self.preview(),
        }

    def actions(self):
        acts = []
        if self.nothing_to_do or self.blocked:
            return acts
        acts.append({"step": "snapshot"})
        if self.install_nft:
            acts.append({"step": "install_nft", "command": "apt-get install -y nftables"})
        acts.append({"step": "state", "engine": "nft", "forwards": len(self.forwards)})
        acts.append({"step": "nft_apply"})
        acts.append({"step": "compat"})
        acts.append({"step": "units"})        # before the old rules go: they come back at boot either way
        for t in ("nat", "filter"):
            if self.remove[t]:
                acts.append({"step": "iptables_remove", "table": t, "rules": len(self.remove[t])})
        if self.persisted_lines:
            acts.append({"step": "persisted_edit", "file": scan_mod.display(self.inv.persisted.path),
                         "lines": len(self.persisted_lines)})
        acts.append({"step": "verify"})
        return acts

    # ── preview (SPEC §4.11) ───────────────────────────────────────────────

    def preview(self, backup=None):
        inv = self.inv
        L = []
        if self.mode == "switch":
            L.append(tr("migrate.preview.title_switch"))
        else:
            L.append(tr("migrate.preview.title_adopt"))
        L.append("")
        if self.blocked:
            L.append(tr("migrate.preview.blocked"))
            for b in self.blockers:
                L.append("  • " + b["message"])
            L.append("")
        if self.nothing_to_do:
            L.append(tr("migrate.preview.nothing_new"))
            return "\n".join(L)

        rows = []
        for c in self.imports:
            rows.append(("%s :%d" % (c.proto, c.port), "→ %s:%d" % (c.to, c.to_port), c.name,
                         state_words() if c.source == "state" else origin_word(c.cls)))
        if rows:
            if self.state_restores and len(self.state_restores) == len(rows):
                L.append(tr("migrate.preview.restore", forwards=forwards_word(len(rows)), source=state_words()))
            else:
                L.append(tr("migrate.preview.move" if self.mode == "switch" else "migrate.preview.add",
                            forwards=forwards_word(len(rows))))
            w0 = max(len(r[0]) for r in rows)
            w1 = max(len(r[1]) for r in rows)
            w2 = max(len(r[2]) for r in rows)
            for r in rows:
                line = "  %s   %s   " % (r[0].ljust(w0), r[1].ljust(w1))
                if w2:
                    line += r[2].ljust(w2) + "   "
                L.append((line + r[3]).rstrip())
        elif self.mode == "switch":
            if inv.cands:
                L.append(tr("migrate.preview.none_worked"))
            else:
                L.append(tr("migrate.preview.no_rules"))
        if self.file_imports:
            L.append(tr("migrate.preview.file_imports",
                        items=", ".join("%s :%d" % (c.proto, c.port) for c in self.file_imports), file=self._file()))

        skipped = [c for c in inv.cands if c.status != "import"]
        if skipped or self.file_kept or self.state_kept:
            L.append(tr("migrate.preview.skip_head"))
            for c in skipped:
                L.append("  " + self._skip_line(c))
            for c in self.file_kept:
                L.append("  " + self._file_kept_line(c))
            for f, winner in self.state_kept:
                L.append("  " + tr("migrate.preview.state_kept", proto=f["proto"], port=f["port"], to=f["to"],
                                   to_port=f["to_port"], source=state_words(), winner=_short(winner)))

        stay = []
        simple_masq = [r for r in inv.masq_unlabeled if _simple_masq(r)]
        if simple_masq:
            stay.append(tr("migrate.stay.masq_one" if len(simple_masq) == 1 else "migrate.stay.masq_many",
                           ifaces=", ".join(r.oif for r in simple_masq)))
        other_masq = len(inv.masq_unlabeled) - len(simple_masq)
        foreign = inv.foreign_dnat()
        docker = [r for r in foreign if r.chain.startswith("DOCKER")]
        own = [r for r in foreign if not r.chain.startswith("DOCKER")]
        if own:
            stay.append(trn("migrate.word.foreign_dnat", len(own)))
        if docker or (other_masq and any(c.startswith("DOCKER") for c in inv.nat)):
            stay.append(tr("migrate.stay.docker"))
        if inv.ufw.get("active"):
            stay.append(tr("migrate.stay.ufw"))
        if self.masq_kept:
            stay.append(tr("migrate.stay.masq_kaskad",
                           ifaces=", ".join(tr("migrate.stay.via", iface=r.oif) if r.oif else "(%s)" % r.spec()
                                            for r in self.masq_kept),
                           reason=masq_reason()))
        if self.kept_ref:
            stay.append(trn("migrate.stay.kept_ref", len(self.kept_ref)))
        if stay:
            L.append(tr("migrate.preview.stay", items=", ".join(stay)))

        if self.warnings:
            L.append("")
            L.append(tr("migrate.preview.attention"))
            for w in self.warnings:
                L.append("  • " + w)

        L.append("")
        if self.mode == "switch":
            L.append(tr("migrate.preview.changes"))
            L.append("  • " + tr("migrate.preview.change_local"))
            L.append("  • " + tr("migrate.preview.change_counter"))
            L.append("  • " + tr("migrate.preview.change_table"))
        if self.install_nft:
            L.append("  • " + tr("migrate.preview.change_install"))
        if self.removed_rules:
            L.append(tr("migrate.preview.remove", rules=rules_word(len(self.removed_rules))))
        if self.persisted_lines:
            L.append(tr("migrate.preview.persisted", file=scan_mod.display(inv.persisted.path),
                        n=len(self.persisted_lines)))
        if backup:
            L.append(tr("migrate.backup_at", path=scan_mod.display(backup)))
        else:
            L.append(tr("migrate.preview.backup_to", dir=scan_mod.display(_backups_dir())))
        L.append(tr("migrate.preview.undo"))
        return "\n".join(L)

    def _file(self):
        return scan_mod.display(self.inv.persisted.path) if self.inv.persisted else tr("migrate.preview.rules_file")

    def _file_kept_line(self, c):
        head = "%s :%d → %s" % (c.proto, c.port, "%s:%d" % (c.to, c.to_port) if c.to else c.rule.to_destination)
        if c.reason == "file_taken":
            why = tr("migrate.file_kept.file_taken", winner=_short(c.winner))
        elif c.reason == "target":
            why = tr("migrate.file_kept.target", target=c.bad_target)
        elif c.reason == "partial":
            why = tr("migrate.file_kept.partial", conditions="; ".join(c.qualifiers))
        elif c.reason == "unreachable":
            why = tr("migrate.file_kept.unreachable", conditions="; ".join(c.qualifiers))
        elif c.reason == "partial_before":
            why = tr("migrate.file_kept.partial_before", before=c.before)
        else:
            why = tr("migrate.file_kept.other")
        return tr("migrate.file_kept.line", head=head, file=self._file(), why=why)

    def _skip_line(self, c):
        head = "%s :%d → %s" % (c.proto, c.port, "%s:%d" % (c.to, c.to_port) if c.to else c.rule.to_destination)
        if c.status == "duplicate" and c.reason == "k3":
            return tr("migrate.skip.dup_k3", head=head, winner=c.winner.split("→ ")[-1])
        if c.status == "duplicate":
            return tr("migrate.skip.dup", head=head)
        if c.status == "shadowed" and c.reason == "docker":
            return tr("migrate.skip.shadowed_docker", head=head)
        if c.status == "shadowed":
            return tr("migrate.skip.shadowed", head=head, winner=_short(c.winner))
        if c.reason == "partial_before":
            return tr("migrate.skip.partial_before", head=head, before=c.before)
        if c.reason == "partial":
            return tr("migrate.skip.partial", head=head, conditions="; ".join(c.qualifiers))
        if c.reason == "target":
            return tr("migrate.skip.target", head=head, target=c.bad_target)
        if c.reason == "key_kept":
            return tr("migrate.skip.key_kept", head=head)
        if c.reason == "unreachable":
            return tr("migrate.skip.unreachable", head=head,
                      conditions="; ".join(c.qualifiers) or tr("migrate.skip.conditions"))
        return tr("migrate.skip.other", head=head)


def _short(text):
    t = text or ""
    if t.startswith("-A "):
        t = t.split(" ", 2)[2] if t.count(" ") >= 2 else t
    return t if len(t) <= 90 else t[:87] + "..."


def _simple_masq(r):
    return (r.target == "MASQUERADE" and r.comment is None and r.oif is not None and not r.oif_neg
            and r.src is None and r.dst is None and r.iif is None and r.proto is None
            and not r.extra and not r.target_args)


def _backups_dir():
    from .. import paths
    return paths.backups()


# ── building the plan ──────────────────────────────────────────────────────

def build(inv, force=False):
    p = Plan(inv)
    _blockers(p, force)
    _imports(p)
    _removals(p)
    _persisted(p)
    _warnings(p, force)
    return p


def _file_imports(p):
    """Switch mode: Kaskad forwards of the persisted file whose port has no Kaskad rule live (see the
    module docstring). Fills p.file_imports / p.file_kept."""
    inv = p.inv
    if p.mode != "switch":
        return
    fcands = scan_mod.file_candidates(inv.persisted)
    if not fcands:
        return
    live_keys = set(c.key for c in inv.cands)
    live = scan_mod.expand(inv.nat)
    taken = set(c.key for c in p.imports)
    for c in fcands:
        if c.key in live_keys or c.key in taken:
            continue                       # the live rules of this port decide (stale lines go)
        if c.status in ("duplicate", "shadowed"):
            continue                       # would not work at boot either: a stale line
        if c.status == "import":
            hits = scan_mod.entries_for_key(live, c.proto, c.port)
            if hits:                       # importing would take a port that works now away
                c.status, c.reason, c.winner = "skipped", "file_taken", hits[0][0].rule.text
            else:
                c.name = scan_mod.name_for(inv, c)
                p.file_imports.append(c)
                taken.add(c.key)
                continue
        p.file_kept.append(c)


def _blockers(p, force):
    inv = p.inv
    if inv.ipt_error:
        p.blockers.append({"code": errors.ERROR, "kind": "iptables", "message": inv.ipt_error})
    if inv.state_error:
        p.blockers.append({"code": errors.UNSUPPORTED, "kind": "state",
                           "message": tr("migrate.state_unreadable", error=inv.state_error)})
    sup = inv.support or {}
    if not sup.get("supported"):
        # only a missing nftables package is fixed by installing it (probe code "no_nft");
        # an unsupported OS / kernel / firewalld is not
        code = sup.get("code")
        missing_nft = code == "no_nft" or (not code and not inv.nft_binary)
        if missing_nft and getattr(inv, "apt", False):
            p.install_nft = True
        elif p.mode == "switch" or inv.cands:
            p.blockers.append({"code": errors.UNSUPPORTED, "kind": "unsupported",
                               "message": tr("migrate.unsupported",
                                             reason=sup.get("reason") or tr("migrate.nft_unsupported"))})
    hint = {"docker_port": "migrate.hint.docker_port", "stale_table": "migrate.hint.stale_table"}
    for cf in inv.conflicts:
        if cf.get("blocking") and not force:
            p.blockers.append({"code": errors.CONFLICT, "kind": cf["kind"],
                               "message": "%s%s — %s"
                                          % ((cf["key"].replace(":", " :") + ": ") if cf["key"] else "",
                                             cf["detail"], tr(hint.get(cf["kind"], "migrate.hint.force")))})


class Restored(object):
    """A forward of state.json offered back on a switch (see the module docstring): looks like a
    Candidate to the rest of the plan, but has no iptables rules."""

    source = "state"
    status = "import"
    reason = ""
    winner = None
    rule = None

    def __init__(self, f):
        self.f = dict(f)
        self.proto, self.port, self.to, self.to_port = f["proto"], f["port"], f["to"], f["to_port"]
        self.cls = f.get("origin") or "k3"
        self.name = f.get("name") or ""
        self.qualifiers = []
        self.warnings = []
        self.assoc = []

    @property
    def key(self):
        return (self.proto, self.port)

    @property
    def key_text(self):
        return "%s:%d" % (self.proto, self.port)

    def forward(self):
        return dict(self.f)

    def to_dict(self):
        return {"key": self.key_text, "proto": self.proto, "port": self.port, "to": self.to,
                "to_port": self.to_port, "class": self.cls, "status": self.status, "reason": "",
                "name": self.name, "qualifiers": [], "source": self.source, "rules": []}


def _state_restores(p):
    """Switch mode, engine legacy with forwards in state.json (a «Вернуть iptables» earlier), and no
    Kaskad rule left anywhere: offer those forwards back."""
    inv = p.inv
    if p.mode != "switch" or not inv.state or not inv.state.get("forwards"):
        return
    if inv.cands or p.file_imports or p.file_kept:
        return                             # working Kaskad rules in iptables / rules.v4 are the newer truth
    live = scan_mod.expand(inv.nat)
    for f in inv.state["forwards"]:
        hits = scan_mod.entries_for_key(live, f["proto"], f["port"])
        if hits:
            p.state_kept.append((dict(f), hits[0][0].rule.text))
        else:
            p.state_restores.append(Restored(f))


def _imports(p):
    inv = p.inv
    p.imports = [c for c in inv.cands if c.status == "import"]
    _file_imports(p)
    p.imports += p.file_imports
    _state_restores(p)
    p.imports += p.state_restores
    by_key = {}
    if p.mode == "adopt" and inv.state:
        for f in inv.state["forwards"]:
            by_key[(f["proto"], f["port"])] = dict(f)
    old = {}
    if inv.state:
        for f in inv.state["forwards"]:
            old[(f["proto"], f["port"], f["to"], f["to_port"])] = f
    for c in p.imports:
        f = state.normalize_forward(dict(c.forward(), origin=c.cls), stamp=True)
        prev = old.get((c.proto, c.port, c.to, c.to_port))
        if prev:
            f["added"] = prev.get("added") or f["added"]
        by_key[c.key] = f
    p.forwards = state.sort_forwards(list(by_key.values()))


def _first_ref(fr, dnats):
    for r in dnats:
        ref = dest_ref(r)
        if ref and referenced(fr, [ref]):
            return r.text
    return ""


def _all_nat_dnat(inv):
    out = []
    for rules in inv.nat.values():
        out += [r for r in rules if r.target in ("DNAT", "REDIRECT", "NETMAP")]
    return out


def _removals(p):
    inv = p.inv
    gone = [c for c in inv.cands if c.status in ("import", "duplicate")]
    gone_ids = set(id(c.rule) for c in gone)
    remaining = [r for r in _all_nat_dnat(inv) if id(r) not in gone_ids]
    refs = [x for x in (dest_ref(r) for r in remaining) if x]
    seen = set()
    p.remove["nat"] = [c.rule for c in gone]
    for c in gone:
        for r, role in c.assoc:
            if id(r) in seen:
                continue
            seen.add(id(r))
            if role != "input" and referenced(r, refs):
                p.kept_ref.append((r, _first_ref(r, remaining)))
                continue
            p.remove["filter"].append(r)
    # forwards found only in the persisted file (switch): their INPUT/FORWARD companions may still be live
    # (only nat PREROUTING was flushed; stand, Debian 12) — they go too, as for a live forward, instead
    # of staying in iptables until a reboot
    live_by_key = collections.defaultdict(list)
    for r in inv.filt.all_rules():
        if id(r) not in seen:
            live_by_key[(r.chain, r.norm())].append(r)
    for c in p.file_imports:
        for fr, role in c.assoc:
            hits = live_by_key.get((fr.chain, fr.norm()))
            if not hits:
                continue
            r = hits.pop(0)
            seen.add(id(r))
            if role != "input" and referenced(r, refs):
                p.kept_ref.append((r, _first_ref(r, remaining)))
                continue
            p.remove["filter"].append(r)
    # kaskad:masq stays: it masquerades everything leaving the interface (another VPN may rely on it)
    p.masq_kept = list(inv.masq_kaskad)
    # keep iptables order inside each table (cosmetic; -D lines are independent)
    for t in ("nat", "filter"):
        p.remove[t].sort(key=lambda r: (r.chain, r.index))


def _file_kaskad_items(ps):
    """Kaskad rules found in the persisted file itself (DNAT + their INPUT/FORWARD). kaskad:masq is
    not one of them: it stays in the file as it stays in iptables."""
    nat = ps.ss.get("nat") or ipt.Chains("nat")
    filt = ps.ss.get("filter") or ipt.Chains("filter")
    items = []
    for r in nat.get("PREROUTING", []):
        c = scan_mod.classify_dnat(r, filt)
        if c is None:
            continue
        scan_mod.associate(c, filt)
        items.append(c.rule)
        items += [x for x, _role in c.assoc]
    uniq, seen = [], set()
    for r in items:
        if id(r) not in seen:
            seen.add(id(r))
            uniq.append(r)
    return sorted(uniq, key=lambda r: r.lineno)


def _persisted(p):
    inv = p.inv
    ps = inv.persisted
    if ps is None or ps.ss.problems:
        return
    removed = ipt.multiset(p.removed_rules)
    want = collections.Counter(removed)
    lines = set()
    file_rules = ps.rules()
    for fr in file_rules:
        k = (fr.table, fr.norm())
        if want[k] > 0:
            want[k] -= 1
            lines.add(fr.lineno)
            p.persisted_removed[k] += 1
    items = _file_kaskad_items(ps)
    remaining_dnat = [r for r in file_rules if r.table == "nat" and r.target == "DNAT"
                      and r.lineno not in lines and r not in items]
    refs = [x for x in (dest_ref(r) for r in remaining_dnat) if x]
    # forwards found only in the file: kept ones stay with their INPUT/FORWARD lines, imported
    # ones leave the file (a FORWARD line a foreign DNAT of the file points to stays)
    keep = set()
    for c in p.file_kept:
        keep.update([c.rule.lineno] + [x.lineno for x, _role in c.assoc])
    for c in p.file_imports:
        for fr in [c.rule] + [x for x, _role in c.assoc]:
            if fr.lineno in lines or fr.lineno in keep or (fr.chain == "FORWARD" and referenced(fr, refs)):
                continue
            lines.add(fr.lineno)
            p.persisted_removed[(fr.table, fr.norm())] += 1
    live_after = ipt.multiset(list(inv.nat.all_rules()) + list(inv.filt.all_rules()))
    live_after.subtract(removed)
    file_after = ipt.multiset([r for r in file_rules if r.lineno not in lines])
    for fr in items:
        if fr.lineno in lines or fr.lineno in keep:
            continue
        k = (fr.table, fr.norm())
        if file_after[k] <= max(live_after[k], 0):
            continue
        if fr.chain == "FORWARD" and referenced(fr, refs):
            continue
        lines.add(fr.lineno)
        file_after[k] -= 1
        p.persisted_stale.append(fr)
        p.persisted_removed[k] += 1
    p.persisted_lines = sorted(lines)


def _warnings(p, force):
    inv = p.inv
    for c in p.imports:
        if "manual" in c.warnings:
            p.warnings.append(tr("migrate.warn.manual", proto=c.proto, port=c.port,
                                 conditions="; ".join(c.qualifiers)))
    for cf in inv.conflicts:
        if cf["kind"] == "foreign_after":
            p.warnings.append(tr("migrate.warn.foreign_after", what=cf["key"].replace(":", " :"),
                                 detail=cf["detail"]))
        elif cf.get("blocking") and force:
            p.warnings.append(tr("migrate.warn.forced", what=cf["key"].replace(":", " :") if cf["key"]
                                 else tr("migrate.warn.table"), detail=cf["detail"]))
    if p.persisted_stale:
        p.warnings.append(tr("migrate.warn.stale", file=scan_mod.display(inv.persisted.path),
                             n=len(p.persisted_stale)))
    # Connections opened before the switch went through the old iptables DNAT and carry no Kaskad
    # ct mark; the fwd_count rule of the engine adopts them (ct status dnat + original port → mark K),
    # so kaskad:compat keeps them alive even under a Docker-style FORWARD DROP — verified on Debian 12
    # and Ubuntu 24.04 (k3/stand/nftcheck2.sh). No warning needed any more.
    p.warnings += list(inv.warnings)
    p.warnings = [util.redact(w) for w in p.warnings]

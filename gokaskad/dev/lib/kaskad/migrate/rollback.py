"""``kaskadctl migrate rollback --to-legacy --yes`` — back from the nft engine to iptables rules in
exactly the Kaskad PRO 2.13 format (SPEC §4.9). Used by «Вернуть iptables» and by the way back to
2.13 (install.sh brings the 2.13 program back itself).

For every forward of state.json, the rules ``apply_iptables_rules`` of 2.13 creates, including its
``remove_rules_for_port`` first::

    nat     -A PREROUTING -p P --dport N -m comment --comment kaskad:N:P -j DNAT --to-destination IP:OUT
    nat     -A POSTROUTING -o IFACE -m comment --comment kaskad:masq -j MASQUERADE      (if missing)
    filter  -I INPUT -p P --dport N -m comment --comment kaskad:N:P -j ACCEPT
    filter  -I FORWARD -p P -d IP --dport OUT -m state --state NEW,ESTABLISHED,RELATED -m comment --comment kaskad:N:P -j ACCEPT
    filter  -I FORWARD -p P -s IP --sport OUT -m state --state ESTABLISHED,RELATED -m comment --comment kaskad:N:P -j ACCEPT

one ``iptables-restore --noflush`` transaction per table, IFACE from ``ip route get 8.8.8.8`` (as
2.x ``detect_interface``). Only then the nft table, the compat rule and the units go (no
downtime), ``netfilter-persistent save`` runs if present (as 2.x did), engine -> legacy.

SIGHUP/SIGTERM while the iptables rules are written raise ``interrupt.Interrupted``: the tables
touched so far go back to the snapshot and the engine stays nft. Once they are written, the rest
(nft table away, state.json) is finished whatever signal comes — stopping there would leave
neither engine in charge.
"""

from .. import __version__, compat, errors, ipt, nft, paths, state, units, util
from ..errors import KaskadError
from ..i18n import tr
from . import interrupt
from . import scan as scan_mod
from . import snapshot


def legacy_lines(forwards, iface, nat, filt):
    """(nat lines, filter lines) for iptables-restore --noflush, 2.13 format."""
    nat_lines, filt_lines = [], []
    # 2.13: iptables -t nat -C POSTROUTING -o IFACE -m comment --comment kaskad:masq -j MASQUERADE
    masq_present = any(r.target == "MASQUERADE" and r.comment == ipt.MASQ_LABEL and r.oif == iface
                       and not r.oif_neg and r.src is None and r.dst is None and r.iif is None
                       and r.proto is None and not r.extra and not r.target_args
                       for r in nat.get("POSTROUTING", []))
    for f in state.sort_forwards(list(forwards)):
        proto, port, to, to_port = f["proto"], int(f["port"]), f["to"], int(f["to_port"])
        label = "kaskad:%d:%s" % (port, proto)
        # remove_rules_for_port (2.13): labeled DNAT in PREROUTING, labeled INPUT / FORWARD
        for r in nat.get("PREROUTING", []):
            if r.target == "DNAT" and r.comment == label:
                nat_lines.append(ipt.delete_line(r))
        for chain in ("INPUT", "FORWARD"):
            for r in filt.get(chain, []):
                if r.comment == label:
                    filt_lines.append(ipt.delete_line(r))
        filt_lines.append("-I INPUT -p %s --dport %d -m comment --comment %s -j ACCEPT" % (proto, port, label))
        nat_lines.append("-A PREROUTING -p %s --dport %d -m comment --comment %s -j DNAT --to-destination %s:%d"
                         % (proto, port, label, to, to_port))
        if not masq_present:
            nat_lines.append("-A POSTROUTING -o %s -m comment --comment %s -j MASQUERADE" % (iface, ipt.MASQ_LABEL))
            masq_present = True
        filt_lines.append("-I FORWARD -p %s -d %s --dport %d -m state --state NEW,ESTABLISHED,RELATED "
                          "-m comment --comment %s -j ACCEPT" % (proto, to, to_port, label))
        filt_lines.append("-I FORWARD -p %s -s %s --sport %d -m state --state ESTABLISHED,RELATED "
                          "-m comment --comment %s -j ACCEPT" % (proto, to, to_port, label))
    return nat_lines, filt_lines


def _earlier_dnat_warnings(forwards, nat):
    """A rule already in nat PREROUTING that catches a forward's port would win after the return
    (our DNAT is appended, as 2.13 does) — say so. Jumps are followed as the migration does
    (scan.expand): a DNAT inside DOCKER or another user chain counts at the position of the jump.
    The forward's own labeled DNAT does not count — legacy_lines replaces it."""
    out = []
    entries = scan_mod.expand(nat)
    for f in forwards:
        label = "kaskad:%d:%s" % (f["port"], f["proto"])
        for e, _cov in scan_mod.entries_for_key(entries, f["proto"], int(f["port"])):
            r = e.rule
            if e.kind == "rule" and r.comment == label and r.target == "DNAT":
                continue
            where = r.spec() if r.chain == "PREROUTING" else "%s: %s" % (r.chain, r.spec())
            out.append(tr("migrate.back.earlier_dnat", proto=f["proto"], port=int(f["port"]), where=where))
            break
    return out


def _missing_dnat(nat, forwards):
    """Forwards without their 2.13 DNAT in PREROUTING."""
    have = set()
    for r in nat.get("PREROUTING", []):
        lab = r.label
        if r.target == "DNAT" and lab is not None:
            have.add((lab[1], lab[0], r.to_destination))
    return [f for f in forwards if (f["proto"], int(f["port"]), "%s:%d" % (f["to"], int(f["to_port"]))) not in have]


def run(runner):
    with interrupt.guard():
        with state.locked():
            return _run(runner)


def _run(runner):
    st = state.load()
    if st["engine"] != "nft":
        return {"ok": True, "result": "nothing", "engine": st["engine"],
                "message": tr("migrate.back.nothing")}
    inv = scan_mod.collect(runner)
    be = inv.backend.get("backend", "none")
    if be == "none":
        raise KaskadError(tr("migrate.back.no_iptables"), code=errors.UNSUPPORTED)
    if inv.ipt_error:
        raise KaskadError(inv.ipt_error)
    iface = scan_mod.default_iface(runner)
    if not iface:
        raise KaskadError(tr("migrate.back.no_iface"))
    forwards = list(st["forwards"])
    warnings = _earlier_dnat_warnings(forwards, inv.nat)
    snap = snapshot.create(runner, inv, None, reason="rollback")
    nat_lines, filt_lines = legacy_lines(forwards, iface, inv.nat, inv.filt)

    changed = []
    try:
        for table, lines in (("nat", nat_lines), ("filter", filt_lines)):
            if not lines:
                continue
            changed.append(table)             # marked first: a signal inside the call -> restored
            r = ipt.restore_noflush(runner, table, lines, be)
            if not r.ok and r.rc != 124:      # refused: iptables-restore changed nothing
                changed.remove(table)
            if not r.ok:
                raise KaskadError(tr("migrate.restore_refused", table=table,
                                     error=util.redact((r.err or r.out).strip()[:300])))
        missing = _missing_dnat(ipt.list_table(runner, "nat", be), forwards)
        if missing:
            raise KaskadError(tr("migrate.back.rules_missing",
                                 items=", ".join("%s:%d" % (f["proto"], f["port"]) for f in missing)))
    except (KaskadError, KeyboardInterrupt) as e:
        with interrupt.deferred():            # a second signal must not cut the undo
            why = util.redact(str(e) or e.__class__.__name__)
            undo = []
            if changed:
                try:
                    text = snapshot.read_text(snap, "iptables-save.txt")
                except KaskadError as se:
                    text = None
                    undo.append(str(se))
                for table in (changed if text is not None else []):
                    rr = ipt.restore_table(runner, table, text, be)
                    if not rr.ok:
                        undo.append("iptables-restore -T %s: %s" % (table, (rr.err or rr.out).strip()[:200]))
            util.log_action("ROLLBACK to iptables FAILED: %s" % why)
            msg = tr("migrate.back.failed", why=why)
            if undo:
                msg += " " + tr("migrate.back.undo_failed", errors="; ".join(undo), backup=scan_mod.display(snap))
        raise KaskadError(msg, data={"backup": scan_mod.display(snap)})

    with interrupt.deferred():                # the rules are in iptables: finish, whatever comes
        return _finish(runner, forwards, snap, warnings)


def _finish(runner, forwards, snap, warnings):
    errs = []
    for label, fn in (("table", nft.delete_table), ("compat", compat.remove), ("units", units.remove)):
        try:
            fn(runner)
        except Exception as e:                                   # noqa: BLE001
            errs.append(tr("migrate.not_removed." + label, error=util.redact(e)))
    saved = ""
    ps = scan_mod.read_persisted()
    if runner.which("netfilter-persistent"):
        r = runner.run(["netfilter-persistent", "save"], timeout=120)
        saved = "netfilter-persistent" if r.ok else ""
        if not r.ok:
            errs.append("netfilter-persistent save: %s" % util.redact((r.err or r.out).strip()[:200]))
    elif ps is not None and ps.path == paths.rules_sysconfig() and runner.which("service"):
        r = runner.run(["service", "iptables", "save"], timeout=120)       # 2.x save_iptables on RHEL
        saved = "service iptables save" if r.ok else ""
    elif ps is not None:
        warnings.append(tr("migrate.back.not_saved_file", file=scan_mod.display(ps.path)))
    else:
        warnings.append(tr("migrate.back.not_saved"))
    st = state.load()
    st["engine"] = "legacy"
    state.add_migration(st, {"id": snap.rstrip("/").rsplit("/", 1)[-1], "at": util.now_iso(), "kind": "rollback",
                             "from": {"edition": "k3", "version": __version__, "engine": "nft"},
                             "to": "legacy", "backup": scan_mod.display(snap),
                             "result": "ok" if not errs else "partial", "forwards": len(forwards),
                             "problems": errs})
    state.save(st)
    util.log_action("ROLLBACK to iptables: %d forwards, backup %s%s"
                    % (len(forwards), scan_mod.display(snap), (" (" + "; ".join(errs) + ")") if errs else ""))
    return {"ok": not errs, "result": "ok" if not errs else "partial", "engine": "legacy",
            "forwards": len(forwards), "backup": scan_mod.display(snap), "saved_by": saved,
            "problems": errs, "warnings": warnings}

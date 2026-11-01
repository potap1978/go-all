"""``kaskadctl migrate apply --yes`` — switch the forwards to the nft engine (SPEC §4.5–4.8).

Order (no downtime — the nft table takes over before the old rules go):

    1. scan + plan; blockers -> code 3/5, nothing changed
    2. snapshot
    3. no ``nft`` -> ``apt-get install -y nftables`` (the service is not enabled)
    4. state.json: engine nft, imported forwards, a migration record "pending"
    5. nft.apply            — forwards now work through nft (priority -101 wins over iptables)
    6. compat.ensure        — one FORWARD accept for Kaskad's connections (UFW/Docker DROP)
    7. units.install        — BEFORE anything of the old engine goes: if the process dies after
                              the next step (kill, SIGHUP, power cut), kaskad-net.service still
                              brings the forwards back at boot
    8. iptables-restore --noflush with -D lines, one transaction per table (nat, then filter);
       the persisted rules file loses exactly those lines
    9. verify; any failure from step 4 on -> automatic rollback, exit code 4
   10. log + record "ok"

SIGHUP/SIGTERM in steps 2–9 raise ``interrupt.Interrupted`` in the running step: it fails like
any other error and the automatic rollback runs (with further signals ignored).

Engine already nft: only Kaskad rules that appeared in iptables since then are taken over (the
free script run on top, 2.x restored...). Nothing new -> no snapshot, exit 0, "Переносить нечего".
Before that the engine itself is checked (scan.engine_health): table not loaded, or
kaskad-net.service / the watchdog timer not enabled (a switch cut off by a power loss) -> the
table is loaded from state.json and the units installed (started after the lock), one line
``healed`` in the result.

Automatic rollback restores iptables first (tables that were changed, from the snapshot taken
seconds ago), then removes the nft table / compat rule / units — so traffic always has a path.
If iptables cannot be restored, the nft table is kept (forwards keep working through it) and the
command fails with code 1 pointing to the snapshot.
"""

import collections
import copy
import os
import time

from .. import __version__, aptlock, compat, errors, ipt, nft, paths, state, units, util
from ..errors import KaskadError, UnsupportedError
from ..i18n import tr
from . import interrupt
from . import plan as plan_mod
from . import scan as scan_mod
from . import snapshot


class VerifyError(Exception):
    def __init__(self, problems):
        super(VerifyError, self).__init__("; ".join(problems))
        self.problems = list(problems)


class _Done(object):
    """What has been changed so far (drives the rollback)."""

    def __init__(self):
        self.state_written = False
        self.nft_applied = False
        self.compat = False
        self.tables = []
        self.persisted = None          # (old bytes, new bytes, mode) of the persisted file edit
        self.units = False


def run(runner, force=False):
    """Returns a result dict; raises KaskadError with the right exit code otherwise."""
    ctx = {}
    with interrupt.guard():
        try:
            with state.locked():
                res = _run(runner, force, ctx)
        except KaskadError:
            if ctx.get("healed"):                 # the engine was repaired, then something else failed
                _activate(runner, {})
            raise
        # kaskad-net.service is started only now, outside the lock (its ExecStartPost takes it): a
        # unit that was only enabled is not stopped at shutdown, and the counters would not be saved
        # on the first reboot (found on the stand, 2026-10-04).
        if res.get("result") == "ok" or res.get("healed"):
            _activate(runner, res)
    return res


def _activate(runner, res):
    with interrupt.deferred():
        try:
            units.activate(runner)
        except KaskadError as e:
            res.setdefault("warnings", []).append(tr("migrate.apply.net_not_started", error=e))


def heal(runner, inv, problems):
    """Engine nft, but its table is not loaded or its units are not enabled (scan.engine_health):
    load the table from state.json, install and enable the units (the caller starts
    kaskad-net.service after the lock). Returns (one-line message, [warnings]); KaskadError if the
    table or the units cannot be put in place. Called under the state lock."""
    warnings = []
    nft.apply(inv.state, runner)
    units.install(runner)
    try:
        compat.ensure(runner)
    except KaskadError as e:
        warnings.append(str(e))
    util.log_tr("migrate.apply.heal_log", problems="; ".join(problems))
    return tr("migrate.apply.healed", problems="; ".join(problems)), warnings


def _run(runner, force, ctx):
    inv = scan_mod.collect(runner)
    healed, heal_warnings = "", []
    problems = scan_mod.engine_health(inv)
    if problems:
        try:
            healed, heal_warnings = heal(runner, inv, problems)
        except KaskadError as e:
            raise KaskadError(tr("migrate.apply.heal_failed", problems="; ".join(problems), error=e),
                              code=errors.ERROR)
        ctx["healed"] = healed
    pl = plan_mod.build(inv, force=force)
    if pl.blocked:
        raise KaskadError(((healed + " ") if healed else "")
                          + tr("migrate.apply.blocked", why="; ".join(b["message"] for b in pl.blockers)),
                          code=pl.blocker_code(),
                          data={"blockers": pl.blockers, "summary": pl.summary(), "healed": healed})
    if pl.nothing_to_do:
        res = {"ok": True, "result": "nothing", "engine": inv.engine,
               "message": tr("migrate.nothing"), "summary": pl.summary()}
        if healed:
            res.update(healed=healed, warnings=heal_warnings)
        return res
    pl.warnings += heal_warnings

    snap = snapshot.create(runner, inv, pl, reason="migrate")
    snap_id = snap.rstrip("/").rsplit("/", 1)[-1]
    if pl.install_nft:
        _install_nft(runner)

    prev_engine = inv.engine
    prev_state = copy.deepcopy(inv.state) if inv.state is not None else None
    be = inv.backend.get("backend", "none")
    done = _Done()
    record = {"id": snap_id, "at": util.now_iso(), "kind": "migrate" if pl.mode == "switch" else "adopt",
              "from": {"edition": inv.edition, "version": inv.version, "engine": prev_engine},
              "to": __version__, "backup": scan_mod.display(snap), "result": "pending",
              "imported": len(pl.imports), "skipped": len([c for c in inv.cands if c.status != "import"]),
              "removed": len(pl.removed_rules)}
    st = None
    try:
        st = copy.deepcopy(prev_state) if prev_state is not None else state.empty()
        st["engine"] = "nft"
        st["forwards"] = [dict(f) for f in pl.forwards]
        state.add_migration(st, dict(record))
        done.state_written = True
        state.save(st)

        done.nft_applied = True
        nft.apply(st, runner)

        done.compat = True
        compat.ensure(runner)

        # the units come before anything of the old engine goes (see the module docstring)
        done.units = True
        units.install(runner)

        for table in ("nat", "filter"):
            lines = pl.removal_lines(table)
            if not lines:
                continue
            # marked first: a signal inside the call leaves the outcome unknown -> restored
            done.tables.append(table)
            res = ipt.restore_noflush(runner, table, lines, be)
            if not res.ok and res.rc != 124:  # refused: iptables-restore changed nothing
                done.tables.remove(table)     # (124 — timed out: unknown, stays marked)
            if not res.ok:
                raise KaskadError(tr("migrate.restore_refused", table=table,
                                     error=util.redact((res.err or res.out).strip()[:300])))

        if pl.persisted_lines:
            # from the file as it is NOW; refused if the planned lines are gone (-> rollback)
            done.persisted = persisted_edit(inv.persisted, pl.persisted_lines)
            _write_edit(inv.persisted.path, done.persisted)

        problems = verify(runner, inv, pl, st, done.persisted)
        if problems:
            raise VerifyError(problems)
    except (Exception, KeyboardInterrupt) as e:                 # noqa: BLE001
        problems = e.problems if isinstance(e, VerifyError) else [util.redact(str(e) or e.__class__.__name__)]
        with interrupt.deferred():                               # a second signal must not cut the rollback
            ok, errs = auto_rollback(runner, inv, pl, done, snap, prev_state, record, problems)
        what = tr("migrate.apply.verify_failed" if isinstance(e, VerifyError) else "migrate.apply.interrupted")
        data = {"problems": problems, "rollback_errors": errs, "backup": scan_mod.display(snap)}
        if ok:
            raise KaskadError(tr("migrate.apply.rolled_back", what=what, problems="; ".join(problems),
                                 backup=scan_mod.display(snap)),
                              code=errors.ROLLED_BACK, data=data)
        raise KaskadError(tr("migrate.apply.rollback_failed", what=what, problems="; ".join(problems),
                             errors="; ".join(errs), backup=scan_mod.display(snap)), code=errors.ERROR, data=data)

    with interrupt.deferred():           # switched and verified: finishing is safer than stopping
        st["migrations"][-1]["result"] = "ok"
        try:
            state.save(st)
        except (OSError, IOError) as e:  # the switch itself is done and verified; only the mark is late
            pl.warnings.append(tr("migrate.apply.record_not_saved", error=e))
        util.log_action("MIGRATE %s: imported %d, removed %d iptables rules, backup %s"
                        % ("to nft" if pl.mode == "switch" else "adopt", len(pl.imports), len(pl.removed_rules),
                           scan_mod.display(snap)))
        res = {"ok": True, "result": "ok", "engine": "nft", "mode": pl.mode,
               "imported": [c.to_dict() for c in pl.imports], "removed": len(pl.removed_rules),
               "persisted_lines": len(pl.persisted_lines), "backup": scan_mod.display(snap),
               "summary": pl.summary(), "warnings": pl.warnings}
        if healed:
            res["healed"] = healed
        return res


# apt-get without questions: no debconf dialogs, needrestart restarts services by itself instead of
# asking (Ubuntu 22.04+), and stdin is closed — a CLI run must not hang on a prompt nobody sees.
APT_ENV = ["env", "DEBIAN_FRONTEND=noninteractive", "NEEDRESTART_MODE=a"]


def _apt(runner, args):
    """apt-get ``args`` once apt is free: aptlock.wait() first (fresh servers — unattended-upgrades holds
    the lock for minutes; one line on stderr while waiting, KaskadError after 10 minutes naming the
    holder), apt's own lock wait as a second line of defence, a runner timeout longer than both."""
    try:
        aptlock.wait()
    except interrupt.Interrupted:
        raise
    except KaskadError as e:
        raise KaskadError(tr("migrate.apply.nft_missing", why=e))
    return runner.run(APT_ENV + ["apt-get"] + aptlock.OPTS + list(args), input="", timeout=aptlock.RUN_TIMEOUT)


def _install_nft(runner):
    if not runner.which("apt-get"):
        raise UnsupportedError(tr("migrate.apply.no_apt"))
    r = _apt(runner, ["install", "-y", "nftables"])
    if not r.ok:
        if aptlock.failure_text(r) is None:
            _apt(runner, ["update"])
        r = _apt(runner, ["install", "-y", "nftables"])
    busy = None if r.ok else aptlock.failure_text(r)
    if busy:                                     # temporary, not "unsupported here": code 1, try again later
        raise KaskadError(tr("migrate.apply.nft_missing", why=busy))
    if not r.ok or not runner.which("nft"):
        raise UnsupportedError(tr("migrate.apply.nft_install_failed",
                                  error=util.redact((r.err or r.out).strip()[-300:])))
    sup = nft.probe_support(runner)
    if not sup.get("supported"):
        raise UnsupportedError(tr("migrate.unsupported", reason=sup.get("reason", "")))
    util.log_action("MIGRATE: installed nftables")


def _read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except (OSError, IOError):
        return None


def persisted_edit(ps, line_numbers):
    """``(old, new, mode)``: the persisted rules file as it is NOW and without the planned lines
    (``line_numbers`` of the scanned content ``ps``). Every other byte stays.

    The file is read again here: 2.x, the bot or ``netfilter-persistent save`` may have written it
    since the scan (the menu and the bot take the state lock for their writes, other tools do not).
    Unchanged -> the planned line numbers are dropped. Changed -> the planned rules are looked up in
    the current content by tokens (with multiplicity, first occurrences in file order) and only
    they are dropped. If one of them is not there any more, KaskadError — the caller rolls back
    and the file stays as the other writer left it."""
    shown = scan_mod.display(ps.path)
    cur = _read_bytes(ps.path)
    if cur is None:
        raise KaskadError(tr("migrate.apply.file_gone", file=shown))
    try:
        mode = os.stat(ps.path).st_mode & 0o7777
    except OSError:
        mode = ps.mode
    drop = set(line_numbers)
    if cur == ps.raw:
        text = "".join(l for i, l in enumerate(ps.ss.lines) if i not in drop)
        return cur, text.encode("utf-8", "surrogateescape"), mode
    now = ipt.parse_save(cur.decode("utf-8", "surrogateescape"))
    if now.problems:
        raise KaskadError(tr("migrate.apply.file_changed_bad", file=shown))
    want = collections.Counter((r.table, r.norm()) for r in ps.rules() if r.lineno in drop)
    drop_now = set()
    for r in sorted((r for t in ("nat", "filter") if t in now for r in now[t].all_rules()),
                    key=lambda r: r.lineno):
        k = (r.table, r.norm())
        if want[k] > 0:
            want[k] -= 1
            drop_now.add(r.lineno)
    gone = sum(n for n in want.values() if n > 0)
    if gone:
        raise KaskadError(tr("migrate.apply.file_changed_lines", file=shown, n=gone))
    text = "".join(l for i, l in enumerate(now.lines) if i not in drop_now)
    return cur, text.encode("utf-8", "surrogateescape"), mode


def _write_edit(path, edit):
    _old, new, mode = edit
    util.atomic_write(path, new, mode)


def write_persisted(ps, line_numbers):
    """Rewrite the persisted rules file without the planned lines (see persisted_edit).
    Returns the edit ``(old, new, mode)``."""
    edit = persisted_edit(ps, line_numbers)
    _write_edit(ps.path, edit)
    return edit


# ── verification (SPEC §4.6) ───────────────────────────────────────────────

def _live_rules(runner, be):
    nat = ipt.list_table(runner, "nat", be)
    filt = ipt.list_table(runner, "filter", be)
    return list(nat.all_rules()) + list(filt.all_rules())


def verify(runner, inv, pl, st, persisted_edit_done=None):
    problems = []
    expected = dict(((f["proto"], int(f["port"])), (f["to"], int(f["to_port"]))) for f in st["forwards"])

    # 1. the live table
    try:
        live = nft.read_live(runner)
    except Exception as e:                                       # noqa: BLE001
        live = None
        problems.append(tr("migrate.verify.nft_unreadable", error=util.redact(e)))
    if live is None:
        if not problems:
            problems.append(tr("migrate.verify.no_table"))
    else:
        got = dict(((p, int(n)), (t, int(tp))) for (p, n), (t, tp) in (live.get("forwards") or {}).items())
        if got != expected:
            miss = sorted("%s:%d" % k for k in set(expected) - set(got))
            extra = sorted("%s:%d" % k for k in set(got) - set(expected))
            diff = sorted("%s:%d" % k for k in set(got) & set(expected) if got[k] != expected[k])
            parts = []
            if miss:
                parts.append(tr("migrate.verify.missing", items=", ".join(miss)))
            if extra:
                parts.append(tr("migrate.verify.extra", items=", ".join(extra)))
            if diff:
                parts.append(tr("migrate.verify.other_target", items=", ".join(diff)))
            problems.append(tr("migrate.verify.mismatch", parts="; ".join(parts)))
        if not live.get("has_jump"):
            problems.append(tr("migrate.verify.no_jump"))
        if not live.get("has_masquerade"):
            problems.append(tr("migrate.verify.no_masq"))
        counters = live.get("counters") or {}
        nocnt = sorted("%s:%d" % k for k in expected if nft.counter_name(k[0], k[1]) not in counters)
        if nocnt:
            problems.append(tr("migrate.verify.no_counters", items=", ".join(nocnt)))

    # 2. iptables: removed rules are gone, the compat rule is there
    be = inv.backend.get("backend", "none")
    if be != "none":
        if pl.removed_rules:
            try:
                after = ipt.multiset(_live_rules(runner, be))
                before = ipt.multiset(list(inv.nat.all_rules()) + list(inv.filt.all_rules()))
                removed = ipt.multiset(pl.removed_rules)
                reported = set()
                for r in pl.removed_rules:
                    k = (r.table, r.norm())
                    if k not in reported and after[k] > before[k] - removed[k]:
                        problems.append(tr("migrate.verify.rule_left", rule=r.text))
                        reported.add(k)
            except KaskadError as e:
                problems.append(str(e))
        try:
            if not compat.present(runner):
                problems.append(tr("migrate.verify.no_compat"))
        except Exception as e:                                   # noqa: BLE001
            problems.append(tr("migrate.verify.compat_unknown", error=util.redact(e)))

    # 3. persisted file (compared with its content right before our edit, not at the scan)
    if pl.persisted_lines and inv.persisted is not None:
        now = scan_mod.read_persisted()
        if now is None or now.path != inv.persisted.path:
            problems.append(tr("migrate.verify.file_gone", file=scan_mod.display(inv.persisted.path)))
        else:
            cnt_now = ipt.multiset(now.rules())
            before = inv.persisted
            if persisted_edit_done is not None and persisted_edit_done[0] != inv.persisted.raw:
                old = persisted_edit_done[0]
                before = scan_mod.Persisted(before.path, old,
                                            ipt.parse_save(old.decode("utf-8", "surrogateescape")))
            cnt_before = ipt.multiset(before.rules())
            for k, n in pl.persisted_removed.items():
                if cnt_now[k] > cnt_before[k] - n:
                    problems.append(tr("migrate.verify.line_left", file=scan_mod.display(now.path)))
                    break

    # 4. units
    try:
        us = units.status(runner) or {}
        for u in (units.NET_UNIT, units.WATCH_TIMER):
            en = (us.get(u) or {}).get("enabled")
            if not (en is True or en in ("enabled", "static")):
                problems.append(tr("migrate.unit_disabled", unit=u))
    except Exception as e:                                       # noqa: BLE001
        problems.append(tr("migrate.verify.units_unknown", error=util.redact(e)))

    # 5. state.json
    try:
        st2 = state.load()
        if st2["engine"] != "nft":
            problems.append(tr("migrate.verify.state_engine"))
        got = dict(((f["proto"], f["port"]), (f["to"], f["to_port"])) for f in st2["forwards"])
        if got != expected:
            problems.append(tr("migrate.verify.state_forwards"))
    except Exception as e:                                       # noqa: BLE001
        problems.append(tr("migrate.state_unreadable", error=util.redact(e)))

    # 6. Kaskad services are as they were. A service in the middle of a restart (Restart=always after
    #    a crash, "activating (auto-restart)") is not a mismatch: "activating" counts as running, and
    #    anything else is asked once more after a short pause before it fails the switch.
    if runner.which("systemctl"):
        was_running = [u for u in scan_mod.WATCHED_SERVICES
                       if (inv.services.get(u) or {}).get("active") == "active"]
        now = dict((u, _unit_active(runner, u)) for u in was_running)
        if any(s not in RUNNING_STATES for s in now.values()):
            _sleep(RECHECK_DELAY)
            for u in was_running:
                if now[u] not in RUNNING_STATES:
                    now[u] = _unit_active(runner, u)
        for u in was_running:
            if now[u] not in RUNNING_STATES:
                problems.append(tr("migrate.verify.unit_stopped", unit=u,
                                   state=now[u] or tr("migrate.verify.no_answer")))
    return problems


RUNNING_STATES = ("active", "activating", "reloading")
RECHECK_DELAY = 3.0
_sleep = time.sleep          # tests patch this


def _unit_active(runner, unit):
    r = runner.run(["systemctl", "is-active", unit])
    return (r.out.strip().splitlines() or [""])[0]


# ── automatic rollback (SPEC §4.8) ─────────────────────────────────────────

def auto_rollback(runner, inv, pl, done, snap, prev_state, record, problems):
    """Undo whatever ``done`` says was changed. Returns (ok, [errors])."""
    errs = []
    be = inv.backend.get("backend", "none")
    if done.tables:
        try:
            text = snapshot.read_text(snap, "iptables-save.txt")
        except KaskadError as e:
            text = None
            errs.append(str(e))
        if text is not None:
            for table in ("nat", "filter"):
                if table not in done.tables:
                    continue
                r = ipt.restore_table(runner, table, text, be)
                if not r.ok:
                    errs.append("iptables-restore -T %s: %s" % (table, util.redact((r.err or r.out).strip()[:200])))
    if done.persisted is not None and inv.persisted is not None:
        # put back only our own edit: a file someone else rewrote since is not ours to overwrite
        old, new, mode = done.persisted
        path = inv.persisted.path
        cur = _read_bytes(path)
        if cur == new and new != old:
            try:
                util.atomic_write(path, old, mode)
            except (OSError, IOError) as e:
                errs.append(tr("migrate.rollback.file_not_restored", file=scan_mod.display(path), error=e))
        elif cur != old:
            errs.append(tr("migrate.rollback.file_changed", file=scan_mod.display(path)))
    iptables_back = not errs
    prev_engine = (prev_state or {}).get("engine", "legacy")
    if iptables_back:
        if prev_engine == "nft":
            try:
                if done.nft_applied:
                    nft.apply(prev_state, runner)
                if done.compat:
                    compat.ensure(runner)
            except Exception as e:                               # noqa: BLE001
                errs.append(tr("migrate.rollback.table_not_restored", error=util.redact(e)))
        else:
            for label, fn in (("table", nft.delete_table), ("compat", compat.remove), ("units", units.remove)):
                try:
                    fn(runner)
                except Exception as e:                           # noqa: BLE001
                    errs.append(tr("migrate.not_removed." + label, error=util.redact(e)))
    rec = dict(record, reason="; ".join(problems))
    try:
        if iptables_back:
            st = copy.deepcopy(prev_state) if prev_state is not None else state.empty()
            rec["result"] = "rolled_back"
        else:
            st = state.load()
            rec["result"] = "failed"
            if st.get("migrations") and st["migrations"][-1].get("id") == record["id"]:
                st["migrations"].pop()
        state.add_migration(st, rec)
        state.save(st)
    except Exception as e:                                       # noqa: BLE001
        errs.append(tr("migrate.rollback.state_not_saved", error=util.redact(e)))
    util.log_action("MIGRATE %s: %s" % ("ROLLED BACK" if not errs else "FAILED", "; ".join(problems)[:300]))
    return (not errs), errs

"""Forwards on engine nft: the logic behind ``kaskadctl fwd ...`` (later also the bash menu and the bot).

Every change follows one order (docs/k3/SPEC-wave1.md §3.6–3.7):

    under state.locked(): load -> refuse unless engine nft (UnsupportedError, code 5)
    -> conflict checks (unless --force, or the same proto:port is being replaced)
    -> new state COPY -> nft.apply(copy) FIRST -> state.save(copy) -> util.log_action (2.x text)

so a failed apply leaves state.json and /var/log/kaskad.log untouched. ``add`` also makes sure
the iptables compat rule is there (no iptables: nothing to do; refused: a warning in
``result["apply"]["warnings"]``), so the forward works at once under UFW/Docker.

Log lines are the 2.x ones: ``ADD rule: tcp :443 -> 203.0.113.5:443 (name)`` (without the
parentheses when there is no name), ``DELETE rule: tcp :443 -> 203.0.113.5:443``,
``FLUSH all kaskad rules`` — in the language of the server (catalog journal.fwd.*: RU as in 2.x, EN «ADD forward:»…).
"""

import copy

from . import compat, conflicts, names, nft, snapshot, state, sysrun, util
from .errors import ERROR, USAGE, ConflictError, KaskadError, StateError, UnsupportedError
from .i18n import tr


def require_nft(st):
    if st.get("engine") != "nft":
        raise UnsupportedError(tr("forwards.legacy_engine"))


def describe(f):
    """``tcp: :443 -> 203.0.113.5:443`` — the line 2.x printed after adding a rule."""
    return "%s: :%d -> %s:%d" % (f["proto"], int(f["port"]), f["to"], int(f["to_port"]))


def legacy_line(f, traffic=False):
    """``tcp|443|203.0.113.5:443`` (+ ``|packets|bytes``) — the format of 2.x get_rules_list."""
    s = "%s|%d|%s:%d" % (f["proto"], int(f["port"]), f["to"], int(f["to_port"]))
    if traffic:
        s += "|%d|%d" % (int(f.get("packets", 0)), int(f.get("bytes", 0)))
    return s


def _log_add(f):
    kw = {"proto": f["proto"], "port": f["port"], "to": f["to"], "to_port": f["to_port"]}
    if f.get("name"):
        util.log_tr("journal.fwd.add_named", name=f["name"], **kw)
    else:
        util.log_tr("journal.fwd.add", **kw)


def _key(proto, port):
    try:
        return state.parse_proto(proto), state.parse_port(port)
    except StateError as e:
        raise KaskadError(str(e), code=USAGE)


def list_forwards(st, runner, traffic=False):
    """Forwards of ``st`` (copies, sorted by port/proto). With ``traffic`` each gets ``packets``
    and ``bytes`` from the live counters (0 when the table or the counter is missing) and
    ``live`` = the table could be read. Without ``traffic`` no command is run."""
    require_nft(st)
    rows = [dict(f) for f in state.sort_forwards(list(st.get("forwards") or []))]
    if traffic:
        try:
            live = nft.read_live(runner)
        except KaskadError:
            live = None
        counters = (live or {}).get("counters") or {}
        for r in rows:
            c = counters.get(nft.counter_name(r["proto"], r["port"])) or {}
            r["packets"] = int(c.get("packets", 0))
            r["bytes"] = int(c.get("bytes", 0))
            r["live"] = live is not None
    return rows


def add_forward(proto, port, to, to_port, name="", force=False, runner=None, new_only=False):
    """Add or replace the forward proto/port -> to:to_port. Returns {"ok", "forward", "replaced"
    (old record or None), "apply" (nft.apply result)}. Raises KaskadError: code 2 bad arguments,
    3 conflict (``data`` = {"kind", "conflicts"}; ``new_only`` and the forward exists — {"exists": record}),
    5 engine legacy, 1 nft failed."""
    runner = runner or sysrun.default_runner()
    if name not in (None, ""):
        name = names.check(name, "forward")          # T13: a new name — a refusal, never a silent cut (USAGE)
    try:
        rec = state.normalize_forward({"proto": proto, "port": port, "to": to, "to_port": to_port,
                                       "name": name, "origin": "k3"}, stamp=True)
    except StateError as e:
        raise KaskadError(str(e), code=USAGE)
    with state.locked():
        st = state.load()
        require_nft(st)
        old = state.find_forward(st, rec["proto"], rec["port"])
        if old is not None and new_only:
            raise ConflictError(tr("forwards.exists", proto=rec["proto"], port=rec["port"], to=old["to"],
                                   to_port=old["to_port"]), data={"exists": old})
        if old is None and not force:
            found = conflicts.check(rec["proto"], rec["port"], runner)
            if found:
                raise ConflictError(conflicts.message(rec["proto"], rec["port"], found),
                                    data={"kind": found[0]["kind"], "conflicts": found})
        if old is not None:
            if not rec["name"]:
                rec["name"] = old["name"]
            if (old["to"], old["to_port"]) == (rec["to"], rec["to_port"]):
                rec["origin"] = old["origin"]
                rec["added"] = old["added"]
        new = copy.deepcopy(st)
        state.upsert_forward(new, rec)
        result = nft.apply(new, runner)
        state.save(new)
        _log_add(rec)
        _ensure_compat(runner, result)
    return {"ok": True, "forward": rec, "replaced": old, "apply": result}


def _ensure_compat(runner, result):
    """The compat rule is always there on engine nft (spec §3.3); a new forward must work at once
    under UFW/Docker, not a minute later when the watchdog would put a dropped rule back."""
    try:
        compat.ensure(runner)
    except KaskadError as e:
        result.setdefault("warnings", []).append(str(e))


def delete_forward(proto, port, runner=None):
    """Delete the forward proto/port. Returns {"ok", "removed" (record), "apply"}. Raises
    KaskadError code 1 if there is no such forward."""
    runner = runner or sysrun.default_runner()
    p, n = _key(proto, port)
    with state.locked():
        st = state.load()
        require_nft(st)
        old = state.find_forward(st, p, n)
        if old is None:
            raise KaskadError(tr("forwards.missing", proto=p, port=n), code=ERROR)
        new = copy.deepcopy(st)
        state.remove_forward(new, p, n)
        result = nft.apply(new, runner)
        state.save(new)
        util.log_tr("journal.fwd.delete", proto=p, port=n, to=old["to"], to_port=old["to_port"])
    return {"ok": True, "removed": old, "apply": result}


def flush_forwards(runner=None):
    """Delete every forward. Returns {"ok", "removed" (count), "forwards" (removed records), "apply"}."""
    runner = runner or sysrun.default_runner()
    with state.locked():
        st = state.load()
        require_nft(st)
        removed = list(st.get("forwards") or [])
        new = copy.deepcopy(st)
        new["forwards"] = []
        result = nft.apply(new, runner)
        state.save(new)
        util.log_tr("journal.fwd.flush")
    return {"ok": True, "removed": len(removed), "forwards": removed, "apply": result}


def rename(proto, port, name, allow_duplicate=False, via="cli"):
    """The name of the forward proto/port (T13, SPEC-stage1-rename §3.2): ``names.check`` (a refusal — USAGE), ""
    — no name («tcp :8443» is shown); the same name of another forward — ConflictError with ``data.need =
    "confirm_duplicate"`` and ``data.duplicates`` (["tcp:8443", …]) unless ``allow_duplicate``. The name is not in nft:
    state.json alone, no apply. -> {"ok", "what": "forward", "id": "tcp:8443", "old", "name", "shown",
    "duplicates", "note"}."""
    p, n = _key(proto, port)
    clean = ""
    if not (name is None or isinstance(name, str) and not name.strip(" ")):
        clean = names.check(name, "forward")
    with state.locked():
        st = state.load()
        require_nft(st)
        f = state.find_forward(st, p, n)
        if f is None:
            raise KaskadError(tr("forwards.missing", proto=p, port=n), code=ERROR, data={"gone": True})
        me = state.fwd_key(p, n)
        dup = names.duplicates([(state.fwd_key(x["proto"], x["port"]), x.get("name") or None)
                                for x in st["forwards"]], clean, me) if clean else []
        if dup and not allow_duplicate:
            raise ConflictError(tr("forwards.rename_dup", name=clean, ids=", ".join(dup)),
                                data={"need": "confirm_duplicate", "duplicates": dup})
        old = f.get("name") or ""
        new = copy.deepcopy(st)
        state.find_forward(new, p, n)["name"] = clean
        state.save(new)
    snapshot.bump()
    if old != clean:
        util.log_action("RENAME forward %s: «%s» -> «%s» (via=%s)" % (me, old, clean, via))
    return {"ok": True, "what": "forward", "id": me, "old": old, "name": clean,
            "shown": clean or "%s :%d" % (p, n), "duplicates": dup, "note": tr("gw_rename.note.forward")}

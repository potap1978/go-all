"""F7 (SPEC-R1 3.3.7.15 §3 F7; Е1, В9): the safe apply — a change that may cut the administrator off (the port, TLS or
the network of the protocol he is connected through; the network of the gateway) waits for a confirmation for
``WAIT_S`` s, else it is reverted by a timer of systemd. One module for F3 (the settings of a protocol, ``scope`` — its
kind) and F6 (the network of the gateway, ``scope`` «base»).

The contract the callers use (the stub of F3 has the same names; its ``guard`` just returns ``apply()``):

    who_ssh(env=None) -> {"via": "ssh", "ip"} | None       the client of SSH_CONNECTION (CLI, the menu, the old MCP)
    admin_hit(st_old, st_new, scope, who) -> "pool" | "base" | None
    via_xray(scope, changed) -> bool                       VLESS/SOCKS5/Hysteria2: no guess by the address (З-6)
    cuts(scope, changed) -> bool                           a change that may cut the link (MTU alone never does)
    guard(runner, st_old, plan, scope, who=None, safe=False, apply=fn) -> apply()'s result; a dict gets "pending"
          plan = {"changed": {field: {"old", "new"}}, "id": "a-<8 hex>" (REQUIRED to arm: the id F3 writes to
                  settings-last[scope]; F6 writes settings-last["base"] the same way),
                  "st_new": state after (optional, for a change of the network)}
    confirm(runner, token=None) · disarm(runner) · expire(runner, token, now=None) · expire_due(runner, now=None)
    view(now=None) -> {"scope", "left", "deadline", "token", "via"} | None
    busy(rec, now=None) -> the 409 ``pending_busy`` of ``guard`` for the wait ``rec`` (a change outside ``guard``)
    lock() — the one flock of all of the above (re-entrant in one thread); order: ``lock`` -> the state lock, never back

The wait — ``paths.gw_pending_apply()`` (0600): {"token": 16 hex, "scope", "id", "deadline", "via", "at", "unit", "n"};
one per server. Its timer — ``systemd-run --unit=kaskad-gw-pending-<token>[-<n>] --collect --on-active=…`` calling
``kaskadctl gw pending expire --token <token>``; a re-arm takes the next ``n`` (the unit that runs ``expire`` still
holds its own name). The revert — ``ops.inbound_settings_undo(runner, kind, pending=rec)`` (F3) or
``ops.net_base_undo(runner, pending=rec)`` (F6), only while ``settings-last[scope].id`` is the id of the wait.

Events for the feed (the bot pushes them by the feed): ``settings_reverted {scope, why: "no_confirm"}``,
``settings_revert_failed {scope, why: "changed" | "port_busy" | "net_busy" | "error", port?}`` — written by any process into the
spool ``paths.gw_pending_events()`` (one file each), the collector takes them into its round (``drain``). No secret
anywhere: ports, ids, the token (confirming needs the session of the panel or root)."""

import contextlib
import errno
import fcntl
import ipaddress
import json
import os
import re
import secrets as _secrets
import threading
import time

from .. import paths, state, util
from ..errors import CONFLICT, ERROR, USAGE, KaskadError, LockedError, NestedLockError
from ..i18n import tr

WAIT_S = 120                 # s: the window of the confirmation (Е1)
LOCK_WAIT = 120.0            # s: the lock is held only by the applies of F3/F6 (≤ ≈ 25 s at AmneziaWG)
RETRY_S = 30                 # s: ``expire`` that did not get the lock — its timer again in so many seconds
EARLY_S = 5                  # s: the timer (monotonic) before the deadline (wall clock) by more — the rest, no revert
UNIT = "kaskad-gw-pending-"
TOKEN_RE = re.compile(r"^[0-9a-f]{16}$")
SCOPE_RE = re.compile(r"^[a-z][a-z0-9_]{1,15}$")
ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")
BASE = "base"
CUT_FIELDS = ("port", "proto", "tls", "net")     # what may cut the link of a protocol (MTU alone does not)
XRAY_KINDS = ("vless", "socks5", "hy2")
EVENTS_MAX = 50              # files in the spool at most (a collector that stands: the oldest go)
RETRY_MAX = 600              # s after the deadline: a revert refused by a busy lock is tried again until then (С-1)
OVERDUE_S = 60               # s after the deadline with the wait still there: the round of the collector reverts it (З-2)
STALE_S = 24 * 3600          # s after the deadline: a wait this old is not reverted (a rollback of the version and an
                             # update later — its «old» values are of days ago, П-2): the event «changed» only
DUE_WAIT = 5.0               # s: the round of the collector waits for the lock this long at most (then the next round)


class Refused(KaskadError):
    """A refusal with its word for the API (``key``) and its HTTP status — as ``policy_ops.Refused`` (core_error)."""

    def __init__(self, message, key, status=409, code=CONFLICT, data=None):
        KaskadError.__init__(self, message, code=code, data=data)
        self.key = key
        self.status = status


# ── the lock (С-2) ─────────────────────────────────────────────────────────

_HELD = threading.local()


def held():
    return getattr(_HELD, "depth", 0) > 0 and getattr(_HELD, "pid", None) == os.getpid()


@contextlib.contextmanager
def lock(timeout=LOCK_WAIT):
    """The flock of ``paths.gw_pending_lock()``: re-entrant in the thread that holds it (``expire`` -> ``undo`` of F3
    -> ``disarm``). Taken while the state lock is held — NestedLockError: the order is always this lock first."""
    if held():
        _HELD.depth += 1
        try:
            yield
        finally:
            _HELD.depth -= 1
        return
    if state.held():
        raise NestedLockError(tr("gw_pending.err.order"))
    lp = paths.gw_pending_lock()
    util.ensure_run_dir(os.path.dirname(os.path.dirname(lp)))
    os.makedirs(os.path.dirname(lp), 0o700, exist_ok=True)
    fd = os.open(lp, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.time() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.time() >= deadline:
                    raise LockedError(tr("gw_pending.err.locked"))
                time.sleep(0.1)
        _HELD.pid, _HELD.depth = os.getpid(), 1
        try:
            yield
        finally:
            _HELD.pid, _HELD.depth = None, 0
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ── the file of the wait ───────────────────────────────────────────────────

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def read():
    """The live wait, or None (no file, a broken or foreign one — the same: nothing waits)."""
    try:
        with open(paths.gw_pending_apply(), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not TOKEN_RE.match(str(doc.get("token") or "")) or \
            not SCOPE_RE.match(str(doc.get("scope") or "")) or not _num(doc.get("deadline")):
        return None
    if not isinstance(doc.get("unit"), str) or not doc["unit"].startswith(UNIT + doc["token"]):
        doc["unit"] = UNIT + doc["token"]
    return doc


def _write(doc):
    util.atomic_write(paths.gw_pending_apply(), json.dumps(doc, sort_keys=True) + "\n", 0o600)


def _drop():
    try:
        os.unlink(paths.gw_pending_apply())
    except OSError:
        pass


def view(now=None):
    """For the panel and F3 (``inbound_settings_view``): {"scope", "left", "deadline", "token", "via"} | None. The
    token is no secret: confirming needs the session of the panel or root."""
    rec = read()
    if rec is None:
        return None
    now = time.time() if now is None else now
    return {"scope": rec["scope"], "left": max(0, int(round(rec["deadline"] - now))), "deadline": rec["deadline"],
            "token": rec["token"], "via": rec.get("via")}


# ── who and whether it hits him ────────────────────────────────────────────

def who_ssh(env=None):
    """The administrator of the CLI, the SSH menu and the old MCP ``inbound_port``: the first field of SSH_CONNECTION
    (the client's address); none — None (the local console, a timer)."""
    raw = (os.environ if env is None else env).get("SSH_CONNECTION") or ""
    ip = raw.split()[0] if raw.split() else ""
    return {"via": "ssh", "ip": ip} if ip else None


def _ip_of(who):
    """The IPv4 address of ``who`` or None (none, loopback, unspecified, not an address; an IPv4-mapped IPv6 — its
    IPv4: the pools are IPv4)."""
    raw = (who or {}).get("ip") if isinstance(who, dict) else None
    try:
        ip = ipaddress.ip_address(str(raw or "").strip().split("%")[0])
    except ValueError:
        return None
    if ip.version == 6:
        ip = ip.ipv4_mapped
        if ip is None:
            return None
    if ip.is_loopback or ip.is_unspecified:
        return None
    return ip


def _nets(st, scope):
    from . import addr
    if st is None:
        return []
    try:
        if scope == BASE:
            return [addr.base(st)]
        pools = addr.KIND_POOLS.get(scope)
        if pools is None:
            return []
        if not pools and scope in addr.POOL_OCTET:
            pools = (scope,)                             # IKEv2: its dynamic pool (addr.subnet("ikev2"))
        return [addr._pool_net(p, st) for p in pools]
    except (KaskadError, ValueError, TypeError):
        return []


def admin_hit(st_old, st_new, scope, who, changed=None):
    """Is the administrator ``who`` cut off by this change? A code only when the change ``cuts`` the link
    (``changed`` = ``plan["changed"]``; one change of MTU never does — CONTRACT-pending «Добавка координатора 2»):
    «pool» — his address is in a pool of the kind ``scope`` before or after the change; «base» — in the network of the
    gateway before or after (F6); None — no (loopback, empty, another address; VLESS, SOCKS5 and Hysteria2 have no
    pool — never, З-6). The words for a person — ``hit_text``."""
    if not cuts(scope, changed):
        return None
    ip = _ip_of(who)
    if ip is None:
        return None
    for st in (st_old, st_new):
        for net in _nets(st, scope):
            if ip in net:
                return BASE if scope == BASE else "pool"
    return None


def hit_text(code, kind, lang=None):
    """The words of ``admin_hit`` for the plan (SPEC F7): «вы подключены через WireGuard: … если за 2 мин не
    подтвердите — настройки вернутся сами»; no code — "" (nothing to say)."""
    if code == BASE:
        return tr("gw_pending.hit.base", lang)
    if code == "pool":
        return tr("gw_pending.hit.pool", lang, what=_title(kind, lang))
    return ""


def cuts(scope, changed):
    """A change that may cut the link: the network of the gateway, or the port, TLS or network of a protocol (one
    change of MTU never does — no timer)."""
    return scope == BASE or any(k in CUT_FIELDS for k in (changed or {}))


def via_xray(scope, changed):
    """VLESS, SOCKS5, Hysteria2 with a change of the port or TLS: the panel may go through them (an exit of another
    server, a client of this one) and their clients' addresses tell nothing — the warning ``via_xray`` and
    ``need_yes``; the timer only by choice (``safe``, З-6)."""
    return scope in XRAY_KINDS and cuts(scope, changed)


# ── the timer ──────────────────────────────────────────────────────────────

def _unit_of(token, n):
    return UNIT + token + ("-%d" % n if n else "")


def _timer(runner, token, unit, secs):
    """``systemd-run`` of the timer of the wait; -> True when it is set."""
    argv = ["systemd-run", "--unit=" + unit, "--collect", "--quiet", "--on-active=%ds" % max(1, int(secs)),
            "--timer-property=AccuracySec=1s", paths.kaskadctl(), "gw", "pending", "expire", "--token", token]
    try:
        return runner.run(argv, timeout=30).ok
    except Exception as e:                           # noqa: BLE001 — no timer is an answer, not a crash
        util.log_action("GW pending: systemd-run %s" % type(e).__name__)
        return False


def _timer_active(runner, rec):
    try:
        return runner.run(["systemctl", "is-active", rec["unit"] + ".timer"], timeout=10).out.strip() == "active"
    except Exception:                                # noqa: BLE001 — unknown: as not active (expire_due re-arms)
        return False


def _timer_stop(runner, rec):
    try:
        runner.run(["systemctl", "stop", rec["unit"] + ".timer"], timeout=15)
    except Exception:                                # noqa: BLE001 — a timer left fires ``expire``: no file, nothing
        pass


def _rearm(runner, rec, secs):
    """The timer again for ``secs`` under a new unit name (the running one holds its own); -> True when set."""
    n = int(rec.get("n") or 0) + 1
    unit = _unit_of(rec["token"], n)
    if not _timer(runner, rec["token"], unit, secs):
        return False
    rec = dict(rec, unit=unit, n=n)
    _write(rec)
    return True


# ── the steps ──────────────────────────────────────────────────────────────

def _busy(rec, now):
    left = max(0, int(round(rec["deadline"] - now)))
    text = tr("gw_pending.err.busy", what=_title(rec["scope"]), left=left) if left else \
        tr("gw_pending.err.busy_now", what=_title(rec["scope"]))
    return Refused(text, "pending_busy", data={"pending": {"scope": rec["scope"], "left": left}})


def busy(rec, now=None):
    """The refusal of ``guard`` (409 ``pending_busy``) for the wait ``rec`` of ``read`` — for a change made outside
    ``guard`` under ``lock``: F6 «Вернуть прежнюю сеть» by hand while another kind waits (REV-3315-g2 Н-1)."""
    return _busy(rec, time.time() if now is None else now)


def arm(runner, scope, apply_id, who=None, now=None):
    """The wait: its file, then its timer; no timer — the file goes and a refusal (nothing was changed yet). The id
    of the apply is required: the revert acts only while ``settings-last[scope].id`` is it (С-3)."""
    if not SCOPE_RE.match(str(scope or "")) or not (isinstance(apply_id, str) and ID_RE.match(apply_id)):
        raise TypeError("pending.arm: scope or the id of the apply")
    now = time.time() if now is None else now
    token = _secrets.token_hex(8)
    rec = {"token": token, "scope": scope, "id": apply_id, "deadline": int(now) + WAIT_S, "at": int(now), "unit": _unit_of(token, 0), "n": 0,
           "via": (who or {}).get("via") if (who or {}).get("via") in ("panel", "ssh") else None}
    _write(rec)
    if not _timer(runner, token, rec["unit"], WAIT_S):
        _drop()
        raise Refused(tr("gw_pending.err.timer"), "pending_timer", status=500, code=ERROR)
    util.log_action("GW pending: %s armed for %d s (%s)" % (scope, WAIT_S, rec["via"] or "-"))
    return rec


def guard(runner, st_old, plan, scope, who=None, safe=False, apply=None, now=None):
    """The whole apply of F3/F6 under the lock (С-2, С-3). A live wait and an apply of the same kind (any change —
    MTU too: it would overwrite ``settings-last[kind]``), a change of the network of the gateway while any wait, or a
    new wait while another kind waits -> 409 ``pending_busy``. The administrator hit (``admin_hit`` on a change that
    ``cuts``) or ``safe`` -> the wait armed BEFORE ``apply`` (no timer — refused, nothing changed); ``apply`` failed —
    the wait goes. No id of the apply (``plan["id"] is None``: VLESS on 443 in front of the site, settings-last can't be
    written — nothing to revert): the administrator hit or ``safe`` -> 409 ``pending_no_undo``, nothing applied (what
    cuts him off is never applied without its revert — CONTRACT-pending «Добавка координатора 3»); else applied, no
    wait.
    -> ``apply()``; a dict answer gets ``pending`` ({token, deadline, scope, left} | None)."""
    if not callable(apply):
        raise TypeError("pending.guard: apply")
    plan = plan if isinstance(plan, dict) else {}
    changed = plan.get("changed") if isinstance(plan.get("changed"), dict) else {}
    with lock():
        now = time.time() if now is None else now
        cur = read()
        pid = plan.get("id")
        hit = admin_hit(st_old, plan.get("st_new"), scope, who, changed)
        want = bool(hit or safe)
        if cur is not None and (cur["scope"] == scope or scope == BASE or want):
            raise _busy(cur, now)
        if want and pid is None:                    # no record to revert: never applied without its revert
            if hit:
                raise Refused(tr("gw_pending.err.no_undo_hit", what=_title(scope)), "pending_no_undo",
                              data={"why": "hit", "hit": hit})
            raise Refused(tr("gw_pending.err.no_undo", what=_title(scope)), "pending_no_undo", data={"why": "safe"})
        rec = arm(runner, scope, pid, who, now) if want else None
        try:
            res = apply()
        except BaseException:
            if rec is not None:
                disarm(runner)
            raise
        if rec is not None:
            util.log_action("GW pending: %s applied, waits for a confirmation (%s)" % (scope, hit or "safe"))
        if isinstance(res, dict):                   # З-1: the deadline was set before the apply (≈ 25 s at AWG)
            res["pending"] = {"token": rec["token"], "deadline": rec["deadline"], "scope": scope,
                              "left": max(0, int(round(rec["deadline"] - time.time())))} if rec is not None else None
        return res


def confirm(runner, token=None):
    """«Подтвердить»: the wait (with this token, when given) is over — its timer stopped, its file gone. None waits
    (reverted already, or another one) -> 409 ``pending_gone``: the administrator must know the old settings work."""
    if token is not None and not TOKEN_RE.match(str(token)):
        raise Refused(tr("gw_pending.err.token"), "field", status=400, code=USAGE, data={"field": "token"})
    with lock():
        rec = read()
        if rec is None or (token is not None and rec["token"] != token):
            raise Refused(tr("gw_pending.err.gone"), "pending_gone")
        _timer_stop(runner, rec)
        _drop()
    util.log_action("GW pending: %s confirmed" % rec["scope"])
    return {"ok": True, "confirmed": True, "scope": rec["scope"], "text": tr("gw_pending.confirmed",
                                                                           what=_title(rec["scope"]))}


def disarm(runner):
    """The wait off without a token: an apply that failed, a manual «Вернуть прежние» of the same kind, F6
    ``net_base_undo`` (С-3: after a manual revert no wait hangs). -> the record that waited, or None."""
    with lock():
        rec = read()
        if rec is None:
            return None
        _timer_stop(runner, rec)
        _drop()
    util.log_action("GW pending: %s disarmed" % rec["scope"])
    return rec


def expire(runner, token, now=None):
    """The timer of the wait (``kaskadctl gw pending expire --token``): no file or another token — nothing (confirmed
    or reverted already); more than EARLY_S s before the deadline — the timer for the rest (no revert); else the
    revert. The lock not got in LOCK_WAIT s — the timer again in RETRY_S s with the same token. -> what was done."""
    if not TOKEN_RE.match(str(token or "")):
        raise Refused(tr("gw_pending.err.token"), "field", status=400, code=USAGE, data={"field": "token"})
    try:
        with lock():
            rec = read()
            if rec is None or rec["token"] != token:
                return {"ok": True, "done": "nothing"}
            now = time.time() if now is None else now
            if now < rec["deadline"] - EARLY_S:
                if _rearm(runner, rec, rec["deadline"] - now):
                    return {"ok": True, "done": "later", "left": int(rec["deadline"] - now)}
            return _revert(runner, rec, now)
    except LockedError:
        # the file is not touched without the lock: a timer of its own name; a second one that finds no file (or
        # another token) does nothing, so a stale ``unit`` in the file is harmless
        if _timer(runner, token, UNIT + token + "-r%d" % int(time.time()), RETRY_S):
            return {"ok": True, "done": "retry", "left": RETRY_S}
        raise


def expire_due(runner, now=None, wait=LOCK_WAIT):
    """Before the first round of the collector (a reboot, an update of Kaskad, a crash — С-2): no wait — nothing; its
    timer alive — nothing (it fires itself: no «unit already exists», no double revert); no timer and the deadline
    passed — the revert now; not yet — the timer for the rest; ``systemd-run`` refused — the revert now (safer than a
    wait without a timer). ``wait`` — the lock at most so long (the round of the collector: DUE_WAIT, the next round
    tries again). Never raises: the loop of the collector goes on. -> what was done."""
    try:
        with lock(wait):
            rec = read()
            if rec is None:
                return None
            if _timer_active(runner, rec):
                return "alive"
            now = time.time() if now is None else now
            if now < rec["deadline"] and _rearm(runner, rec, rec["deadline"] - now):
                return "rearmed"
            return _revert(runner, rec, now)["done"]
    except LockedError as e:
        if isinstance(e, NestedLockError):           # a bug of the order of the locks: said
            util.log_action("GW pending: expire_due %s" % type(e).__name__)
            return "error"
        return "busy"                                # an apply holds the lock: the next round tries again (quietly)
    except Exception as e:                           # noqa: BLE001 — the collector must start whatever happens here
        util.log_action("GW pending: expire_due %s" % type(e).__name__)
        return "error"


def overdue(runner, now=None):
    """З-2: every round of the collector (not under the state lock) — a wait OVERDUE_S s past its deadline is still
    there (its timer lost: ``daemon-reexec``, ``reset-failed``, a retry refused) -> ``expire_due`` with a short wait for
    the lock. A cheap read of one file otherwise; no command while nothing is overdue."""
    rec = read()
    now = time.time() if now is None else now
    if rec is None or now <= rec["deadline"] + OVERDUE_S:
        return None
    return expire_due(runner, now, wait=DUE_WAIT)


# ── the revert ─────────────────────────────────────────────────────────────

def settings_last_path():
    """F3's ``settings-last.json`` (``paths.gw_var_dir()``): {scope: {"id", …}} — the last apply of each kind."""
    return os.path.join(paths.gw_var_dir(), "settings-last.json")


def _last_id(scope):
    try:
        with open(settings_last_path(), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    rec = doc.get(scope) if isinstance(doc, dict) else None
    return rec.get("id") if isinstance(rec, dict) else None


def _undo(runner, rec):
    """The revert of the whole apply: F3 ``ops.inbound_settings_undo(kind, pending=rec)`` / F6
    ``ops.net_base_undo(pending=rec)`` — under this lock (the callers know it is held: no ``disarm`` of their own)."""
    from . import ops
    if rec["scope"] == BASE:
        fn = getattr(ops, "net_base_undo", None)
        if fn is None:
            raise KaskadError("net_base_undo")
        return fn(runner, pending=rec)
    fn = getattr(ops, "inbound_settings_undo", None)
    if fn is None:
        raise KaskadError("inbound_settings_undo")
    return fn(runner, rec["scope"], pending=rec)


def _busy_port(e):
    """The port of a refusal «прежний порт занят» of ``undo`` (П-2): ``data.why == "port_busy"`` with ``data.port``, or
    the conflicts of ``port_conflicts`` (as ``_inbound_port`` answers); else None."""
    data = getattr(e, "data", None)
    if not isinstance(data, dict):
        return None
    if data.get("why") == "port_busy" and _num(data.get("port")):
        return int(data["port"])
    if isinstance(data.get("conflicts"), list):
        ports = [c.get("port") for c in data["conflicts"] if isinstance(c, dict) and _num(c.get("port"))]
        return int(ports[0]) if ports else (int(data["port"]) if _num(data.get("port")) else 0)
    return None


def _final_why(e, scope=None):
    """The «why» of the event for a final refusal of ``undo``: changed since (F3 ``why`` «moved» — the port or MTU
    changed by another action, «changed» — another apply) -> «changed» (nothing to revert); the old port taken ->
    «port_busy»; the old network of the gateway taken by something of the node (F6 ``why`` «busy») -> «net_busy»
    (REV-3315-g2 Н-5); else «error»."""
    data = getattr(e, "data", None)
    if isinstance(data, dict) and data.get("why") in ("moved", "changed"):
        return "changed"
    if scope == BASE and isinstance(data, dict) and data.get("why") == "busy":
        return "net_busy"
    return "port_busy" if _busy_port(e) is not None else "error"


def _transient(e):
    """A refusal of ``undo`` that passes by itself (С-1): the state lock busy (``LockedError``, not the nested one — a
    bug), a transition of VLESS under way (``ConflictError`` with ``data.need == "busy"``)."""
    if isinstance(e, LockedError) and not isinstance(e, NestedLockError):
        return True
    data = getattr(e, "data", None)
    return isinstance(data, dict) and data.get("need") == "busy"


def _revert(runner, rec, now=None):
    """Not confirmed in time: the apply of this wait back, only while ``settings-last[scope].id`` is its id (another
    apply or a manual revert meanwhile — nothing back, ``changed``; a wait STALE_S s past its deadline — a rollback of
    the version and an update later — nothing back either, П-2). A busy lock inside the revert (С-1) — the wait stays,
    its timer again in RETRY_S s, until RETRY_MAX s past the deadline (then «error»; no timer — the round of the
    collector tries, ``overdue``). Any final outcome: the wait goes and one event for the feed (the push of the bot); no
    text of an exception with data in it."""
    scope = rec["scope"]
    now = time.time() if now is None else now
    ev = {"kind": "settings_revert_failed", "scope": scope, "why": "error"}
    final = True
    try:
        if rec.get("id") is None or _last_id(scope) != rec.get("id") or now > rec["deadline"] + STALE_S:
            ev["why"] = "changed"
        else:
            try:
                _undo(runner, rec)
                ev = {"kind": "settings_reverted", "scope": scope, "why": "no_confirm"}
            except KaskadError as e:
                if _transient(e) and now <= rec["deadline"] + RETRY_MAX:
                    final = False
                    _rearm(runner, rec, RETRY_S)
                    util.log_action("GW pending: %s revert waits for a busy lock, again in %d s" % (scope, RETRY_S))
                    return {"ok": True, "done": "retry", "left": RETRY_S}
                ev["why"] = _final_why(e, scope)
                if ev["why"] == "port_busy":
                    ev["port"] = _busy_port(e)
                util.log_action("GW pending: %s revert refused (%s)" % (scope, ev["why"]))
            except Exception as e:                   # noqa: BLE001 — an event says it, the file goes
                util.log_action("GW pending: %s revert failed: %s" % (scope, type(e).__name__))
    finally:
        if final:
            _drop()
            emit(ev)
    util.log_action("GW pending: %s %s (%s)" % (scope, "reverted" if ev["kind"] == "settings_reverted" else
                                                 "not reverted", ev["why"]))
    return {"ok": ev["kind"] == "settings_reverted", "done": "reverted" if ev["kind"] == "settings_reverted"
            else "failed", "event": dict(ev)}


# ── the events for the feed ────────────────────────────────────────────────

EVENT_KINDS = ("settings_reverted", "settings_revert_failed")


def emit(ev):
    """An event for the feed of live.json from any process: one file in the spool (atomic), the collector takes it
    (``drain``). Never raises."""
    try:
        d = paths.gw_pending_events()
        name = "%016d-%s.json" % (int(time.time() * 1000), _secrets.token_hex(4))
        util.atomic_write(os.path.join(d, name), json.dumps(dict(ev, ts=int(time.time())), sort_keys=True), 0o600)
    except Exception as e:                           # noqa: BLE001 — the revert is done; the push is the lesser part
        util.log_action("GW pending: event not written: %s" % type(e).__name__)


def peek():
    """The events of the spool for the round of the collector (oldest first; only the known kinds with a valid
    scope; more than EVENTS_MAX — the oldest dropped) and the files to remove once the feed has them (``done``, З-3:
    a round that fails before the feed loses none). -> (events, files)."""
    d = paths.gw_pending_events()
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".json") and not n.startswith("."))
    except OSError:
        return [], []
    out, files = [], []
    for i, n in enumerate(names):
        p = os.path.join(d, n)
        files.append(p)
        try:
            if i >= len(names) - EVENTS_MAX:
                with open(p, encoding="utf-8") as f:
                    ev = json.load(f)
                if isinstance(ev, dict) and ev.get("kind") in EVENT_KINDS and SCOPE_RE.match(str(ev.get("scope"))):
                    one = {"kind": ev["kind"], "scope": ev["scope"], "why": str(ev.get("why") or "error")[:16]}
                    if _num(ev.get("port")):
                        one["port"] = int(ev["port"])
                    out.append(one)
        except (OSError, ValueError):
            pass
    return out, files


def done(files):
    """The files of ``peek`` removed (the feed has their events). Never raises."""
    for p in files or ():
        try:
            os.unlink(p)
        except OSError:
            pass


def drain():
    """``peek`` and ``done`` at once: the events, their files removed."""
    out, files = peek()
    done(files)
    return out


# ── words ──────────────────────────────────────────────────────────────────

def done_text(done, lang=None):
    """The words of what ``expire`` did (the line of ``gw pending expire`` in the journal of the timer, П-3)."""
    if done == "nothing":
        return tr("gw_pending.done.nothing", lang)
    if done == "later":
        return tr("gw_pending.done.later", lang)
    if done == "retry":
        return tr("gw_pending.done.retry", lang)
    if done == "reverted":
        return tr("gw_pending.done.reverted", lang)
    return tr("gw_pending.done.failed", lang)


def _title(scope, lang=None):
    if scope == BASE:
        return tr("gw_pending.base", lang)
    from . import registry
    try:
        return registry.title(scope) or scope
    except Exception:                                # noqa: BLE001 — a kind this version does not know: its id
        return scope


def event_text(kind, a, lang=None):
    """The words of ``settings_reverted`` / ``settings_revert_failed`` in ``lang`` (the feed and the push)."""
    scope = str((a or {}).get("scope") or "")
    what = _title(scope, lang)
    base = scope == BASE
    if kind == "settings_reverted":
        if base:
            return tr("gw_pending.event.reverted_base", lang)
        return tr("gw_pending.event.reverted", lang, what=what)
    why = (a or {}).get("why")
    if why == "port_busy" and _num((a or {}).get("port")) and a["port"]:
        return tr("gw_pending.event.failed_port", lang, what=what, port=int(a["port"]))
    if why == "changed":
        if base:
            return tr("gw_pending.event.failed_changed_base", lang)
        return tr("gw_pending.event.failed_changed", lang, what=what)
    if base and why == "net_busy":
        return tr("gw_pending.event.failed_base_busy", lang)
    if base:
        return tr("gw_pending.event.failed_base", lang)
    return tr("gw_pending.event.failed", lang, what=what)

"""The schedule is bound to the machine (SPEC-node-score-schedule-share §2.1, NS-S4, §11.1 п. 9, 19, 28).

``armed`` = the first manual check (``first_manual_at``) AND the consent to sysbench/fio (``pkgs == "yes"``), both
got on THIS machine: ``mid`` = HMAC-SHA256(key = /etc/machine-id, message = "kaskad-bench"), the first 32 hex — the
raw id never goes to state.json or a .kbk (machine-id(5): applications get a keyed hash). A copy restored on another
server (``backup.py`` takes ``gateway`` whole) or a reinstalled OS -> the marks count as None, nothing runs until the
first manual check and the consent here.

    machine_id(path=None) -> the 32 hex of /etc/machine-id or None (no file, empty, «uninitialized», not 32 hex)
    mid(path=None)        -> the HMAC of this machine or None
    status(cfg, here)     -> {"first_manual_at", "pkgs", "armed_at", "armed", "armed_why"} of ``gateway.hostmon.bench``
    mark(st, field, value, now, here=…)  writes a mark (inside the caller's transaction): a ``mid`` other than this
                          machine's resets ALL the marks first (§2.1 «Правило записи при смене машины»)
"""

import hashlib
import hmac
import re

from .. import paths

MACHINE_ID = "/etc/machine-id"
MSG = b"kaskad-bench"
MARKS = ("first_manual_at", "pkgs", "armed_at")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_MAX = 4096
_UNSET = object()


def machine_id(path=None):
    """machine-id(5): one line with a newline at the end — the norm; exactly one «\\n» is taken off, the rest must be
    ``fullmatch`` 32 lowercase hex (``<32 hex>\\n\\n``, 31 hex, «uninitialized» — None)."""
    p = path or paths.P(MACHINE_ID)
    try:
        with open(p, "rb") as f:
            raw = f.read(_MAX + 1)
    except (OSError, IOError):
        return None
    if len(raw) > _MAX:
        return None
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    if text.endswith("\n"):
        text = text[:-1]
    return text if _HEX32.fullmatch(text) else None


def mid_of(mid_raw):
    return hmac.new(mid_raw.encode("ascii"), MSG, hashlib.sha256).hexdigest()[:32]


def mid(path=None):
    """The keyed hash of this machine (32 hex) or None."""
    raw = machine_id(path)
    return mid_of(raw) if raw else None


def same(stored, here):
    """``stored`` (from a state or a copy — anything) is the ``mid`` ``here``; a non-ASCII text is not (compare_digest
    raises TypeError on it: a .kbk must never break a restore)."""
    return (isinstance(stored, str) and isinstance(here, str) and len(stored) == len(here) and stored.isascii()
            and here.isascii() and hmac.compare_digest(stored, here))


def _ts(v):
    return v if type(v) in (int, float) and v > 0 else None


def status(cfg, here):
    """The marks of ``cfg`` (``gateway.hostmon.bench``) as this machine sees them and whether the schedule is armed.
    ``armed_why``: None | "no_machine_id" | "other_machine" | "no_manual" | "no_pkgs"."""
    cfg = cfg if isinstance(cfg, dict) else {}
    marks = {"first_manual_at": _ts(cfg.get("first_manual_at")),
             "pkgs": cfg.get("pkgs") if cfg.get("pkgs") in ("yes", "no") else None,
             "armed_at": _ts(cfg.get("armed_at"))}
    why = None
    if not here:
        why = "no_machine_id"
    elif not same(cfg.get("mid"), here):
        why = "other_machine" if any(v is not None for v in marks.values()) else None
        marks = dict.fromkeys(MARKS)
    if why is None:
        if marks["first_manual_at"] is None:
            why = "no_manual"
        elif marks["pkgs"] != "yes":
            why = "no_pkgs"
    if why == "no_machine_id":
        marks = dict.fromkeys(MARKS)
    armed = why is None
    return dict(marks, armed=armed, armed_why=why)


def _cfg(st):
    """``st.gateway.hostmon.bench`` as a dict of its own (created when missing; other keys untouched)."""
    g = st.get("gateway")
    if not isinstance(g, dict):
        g = st["gateway"] = {}
    hm = g.get("hostmon")
    if not isinstance(hm, dict):
        hm = g["hostmon"] = {}
    b = hm.get("bench")
    if not isinstance(b, dict):
        b = hm["bench"] = {}
    return b


def mark(st, field, value, now, here=_UNSET):
    """Write the mark ``field`` (``first_manual_at`` | ``pkgs``) in ``st`` (the caller holds the lock and saves).
    A ``mid`` other than this machine's (or none) — all the marks are reset and the new ``mid`` set first, so the
    ``pkgs: "yes"`` of another machine never comes back to life. ``armed_at`` is set when the schedule becomes armed.
    -> None, or "no_machine_id" (nothing written: no mark without a machine)."""
    if field not in ("first_manual_at", "pkgs"):
        raise ValueError("mark: %r" % (field,))
    if field == "pkgs" and value not in ("yes", "no", None):
        raise ValueError("mark: pkgs %r" % (value,))
    here = mid() if here is _UNSET else here
    if not here:
        return "no_machine_id"
    b = _cfg(st)
    if not same(b.get("mid"), here):
        for k in MARKS:
            b[k] = None
        b["mid"] = here
    b[field] = int(value) if field == "first_manual_at" else value
    if status(b, here)["armed"]:
        if _ts(b.get("armed_at")) is None:
            b["armed_at"] = int(now)
    else:
        b["armed_at"] = None
    return None

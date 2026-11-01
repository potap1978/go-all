"""/etc/kaskad/aliases of 2.x: ``IP=name|note|country|isp`` (2.1: ``IP=name``); the first line of an IP wins
(2.x reads ``head -1``). Fields are sanitized as ``sanitize_field`` does: ``|`` -> space, no ``\\``, no control
characters. Written atomically (0600) with every line of the IP replaced by one.
"""

import os
import re

from .. import paths, util
from .data import valid_ip

FIELDS = ("name", "note", "country", "isp")
# C0, DEL, C1 (CSI, OSC), line separators (SEC-3371 Б-1); lone surrogates — a file of UTF-8 cannot hold them
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]")
MAX = 64            # a field given now (a name, a note — the menu and the bot of 2.x)
KEEP = 255          # a field already written (a copy, a file of 2.x — its length was never limited): cleaned, not cut


def clean(value, limit=KEEP):
    s = str(value or "").replace("|", " ").replace("\\", "")
    return _CTRL.sub("", s).strip()[:limit]


def sanitize(value):
    return clean(value, MAX)


def read_all():
    out = {}
    for line in (util.read_text(paths.aliases()) or "").splitlines():
        if "=" not in line:
            continue
        ip, _, val = line.partition("=")
        ip = ip.strip()
        if not ip or ip in out:
            continue
        parts = (val.split("|") + ["", "", "", ""])[:4]
        out[ip] = dict(zip(FIELDS, (_CTRL.sub("", p).strip() for p in parts)))
    return out


def clean_existing():
    """The file already on disk, after an update (REV-3371-2 Б-2, 3.3.7.2): a file a copy brought in 3.3.5–3.3.7
    (before ``clean_file``) — cleaned the same way. True if it changed (journalled)."""
    p = paths.aliases()
    if not os.path.isfile(p) or os.path.islink(p):
        return False
    try:
        with open(p, "rb") as f:
            data = f.read()
    except OSError:
        return False
    try:
        raw, broken = data.decode("utf-8"), False
    except UnicodeDecodeError:                   # a lone byte 0x9B (8-bit CSI) — bash prints the bytes as they are
        raw, broken = data.decode("utf-8", "replace"), True
    body = raw[:-1] if raw.endswith("\n") else raw
    lines = body.split("\n") if body else []
    if not (broken or "\\" in body or _CTRL.search(body.replace("\n", "")) or any(
            "=" not in l or not valid_ip(l.partition("=")[0].strip())
            or any(len(f) > KEEP for f in l.partition("=")[2].split("|")) for l in lines)):
        return False                             # a clean file (2.1 «IP=name» too) is not rewritten
    text = clean_file(raw)
    util.atomic_write(p, text, 0o600)
    util.log_action("ALIASES CLEAN: %d line(s) -> %d" % (len([l for l in raw.split("\n") if l.strip()]),
                                                          len([l for l in text.split("\n") if l.strip()])))
    return True


def clean_file(text):
    """The file of a copy (.kbk) before it is written (SEC-3371 Б-1): bash of 2.x prints the fields with ``echo -e``
    (``fmt_ip``), so a raw «\\e]52;…\\a» there would run. Every line ``IP=name|note|country|isp`` with its fields
    ``clean``ed (up to KEEP: a long note of 2.x comes whole — REV-3371-2 П-1); a line without «=» or with no address —
    dropped (2.x reads ``^IP=`` only); the order kept.
    Lines — by «\\n» only, as bash reads them (``splitlines`` would cut a field at U+2028, 0x1c–0x1e, U+0085)."""
    out = []
    for line in str(text or "").split("\n"):
        if "=" not in line:
            continue
        ip, _, val = line.partition("=")
        ip = ip.strip()
        if not valid_ip(ip):
            continue
        parts = (val.split("|") + ["", "", "", ""])[:4]
        out.append("%s=%s" % (ip, "|".join(clean(p) for p in parts)))
    return "\n".join(out) + "\n" if out else ""


def get(ip, table=None):
    t = read_all() if table is None else table
    return t.get(ip) or dict((f, "") for f in FIELDS)


def update(ip, **fields):
    """Set some fields of ``ip`` (None — keep), keep the others. Returns the new record."""
    if not valid_ip(ip):
        raise ValueError("address %r" % (ip,))
    p = paths.aliases()
    text = util.read_text(p) or ""
    rec = get(ip, read_all())
    for f in FIELDS:
        if fields.get(f) is not None:
            rec[f] = sanitize(fields[f])
    prefix = ip + "="
    lines = [l for l in text.splitlines() if not l.startswith(prefix)]
    lines.append("%s=%s" % (ip, "|".join(clean(rec[f]) for f in FIELDS)))     # the fields kept — not cut to MAX
    util.atomic_write(p, "\n".join(lines) + "\n", 0o600)
    return rec


def check_name(name, lang=None):
    """A NEW name of a server (T13, SPEC-stage1-rename §2): ``names.check`` and no «\\» — the file is read by bash of
    2.x, which drops it (``sanitize``): refused, never cut silently. "" / spaces — "" (no name: the IP is shown)."""
    from .. import names
    from ..errors import USAGE, KaskadError
    from ..i18n import tr
    if name is None or (isinstance(name, str) and not name.strip(" ")):
        return ""
    clean = names.check(name, "server", lang)
    if "\\" in clean:
        raise KaskadError(tr("names.err.char", lang, what=tr("names.what.server", lang), char="U+005C"), code=USAGE,
                          data={"reason": "char", "what": "server", "char": "U+005C", "max": names.MAX})
    return clean


def rename(ip, name, allow_duplicate=False, via="cli"):
    """The name of a monitored server (and of the targets of its forwards — one name per IP) without its note,
    country and provider changing. A duplicate of another server's name — ConflictError ``data.need =
    "confirm_duplicate"`` unless ``allow_duplicate``. -> {"ok", "what": "server", "id": ip, "old", "name", "shown",
    "duplicates", "note"}."""
    from .. import names, snapshot
    from ..errors import USAGE, ConflictError, KaskadError
    from ..i18n import tr
    ip = str(ip or "").strip()
    if not valid_ip(ip):
        raise KaskadError(tr("aliases.bad_ip", ip=ip), code=USAGE)
    clean = check_name(name)
    table = read_all()
    dup = names.duplicates([(k, v.get("name") or None) for k, v in table.items()], clean, ip) if clean else []
    if dup and not allow_duplicate:
        raise ConflictError(tr("aliases.rename_dup", name=clean, ids=", ".join(dup)),
                            data={"need": "confirm_duplicate", "duplicates": dup})
    old = get(ip, table).get("name") or ""
    update(ip, name=clean)
    snapshot.bump()
    if old != clean:
        util.log_action("RENAME server %s: «%s» -> «%s» (via=%s)" % (ip, old, clean, via))
    return {"ok": True, "what": "server", "id": ip, "old": old, "name": clean, "shown": clean or ip,
            "duplicates": dup, "note": tr("gw_rename.note.same")}

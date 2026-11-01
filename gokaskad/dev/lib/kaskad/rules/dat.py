"""Decoder of the .dat lists of Xray/v2fly — protobuf, stdlib only (docs/k3/research/rules.md):

    GeoIPList    1 repeated GeoIP                  GeoSiteList  1 repeated GeoSite
    GeoIP        1 string code                     GeoSite      1 string code
                 2 repeated CIDR                                2 repeated Domain
                 3 bool reverse_match              Domain       1 enum type: 0 Plain (keyword:), 1 Regex,
    CIDR         1 bytes ip (4 or 16 bytes)                                  2 RootDomain (domain:), 3 Full
                 2 uint32 prefix                                2 string value
                                                                3 repeated Attribute (skipped)

proto3 does not write a field equal to its default: no ``prefix`` is /0, no ``type`` is Plain (keyword). Unknown
fields are skipped by their wire type (0 varint, 1 — 8 bytes, 2 — a length, 5 — 4 bytes); groups (3, 4), a
varint longer than 10 bytes and a length past the end are errors (``DatError``). Codes are compared without case
(Xray uppercases them): the results are keyed by the upper-case code.

Streaming by length: ``entries(src)`` reads one top-level entry at a time — the tag, the length, then exactly
that many bytes — so the file is never held whole (the largest entry, DIRECT of RoscomVPN, is ~400 KB); the
functions below decode only the entries they are asked for. ``src`` is bytes, a path or a binary file object.
"""

import io

from .. import util
from ..i18n import tr

WT_VARINT, WT_I64, WT_LEN, WT_I32 = 0, 1, 2, 5
MAX_ENTRY = 64 << 20                 # one GeoIP / GeoSite: far above anything real, below an absurd allocation
PLAIN, REGEX, DOMAIN, FULL = 0, 1, 2, 3
TYPE_NAMES = {PLAIN: "keyword", REGEX: "regexp", DOMAIN: "domain", FULL: "full"}


class DatError(ValueError):
    """The file is not a list of Xray (or it is cut short)."""


def _varint(buf, pos, end):
    result = 0
    shift = 0
    while True:
        if pos >= end:
            raise util.err(DatError, "rules.dat.cut_number")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if b < 0x80:
            return result, pos
        shift += 7
        if shift >= 70:
            raise util.err(DatError, "rules.dat.varint")


def fields(buf):
    """(field, wire type, value) of one message in memory: an int (varint), a memoryview (length-delimited) or
    bytes (fixed 8 or 4 bytes)."""
    mv = buf if isinstance(buf, memoryview) else memoryview(buf)
    pos, end = 0, len(mv)
    while pos < end:
        key, pos = _varint(mv, pos, end)
        field, wt = key >> 3, key & 7
        if field == 0:
            raise util.err(DatError, "rules.dat.field0")
        if wt == WT_VARINT:
            val, pos = _varint(mv, pos, end)
        elif wt == WT_LEN:
            ln, pos = _varint(mv, pos, end)
            if ln > end - pos:
                raise util.err(DatError, "rules.dat.past_end", field=field)
            val = mv[pos:pos + ln]
            pos += ln
        elif wt in (WT_I64, WT_I32):
            n = 8 if wt == WT_I64 else 4
            if end - pos < n:
                raise util.err(DatError, "rules.dat.cut_field_n", field=field)
            val = bytes(mv[pos:pos + n])
            pos += n
        else:
            raise util.err(DatError, "rules.dat.wire_type", field=field, wt=wt)
        yield field, wt, val


# ── streaming of the top level ─────────────────────────────────────────────

def _stream_varint(f):
    """A varint from a stream; None at a clean end of the stream."""
    result = 0
    shift = 0
    n = 0
    while True:
        b = f.read(1)
        if not b:
            if n == 0:
                return None
            raise util.err(DatError, "rules.dat.cut_number")
        n += 1
        c = b[0]
        result |= (c & 0x7F) << shift
        if c < 0x80:
            return result
        shift += 7
        if shift >= 70:
            raise util.err(DatError, "rules.dat.varint")


def _read_exact(f, n):
    out = bytearray()
    while len(out) < n:
        chunk = f.read(min(1 << 20, n - len(out)))
        if not chunk:
            raise util.err(DatError, "rules.dat.cut_need", size=util.msg_n("util.bytes", n - len(out)))
        out += chunk
    return bytes(out)


def _skip(f, n):
    left = n
    while left > 0:
        chunk = f.read(min(1 << 20, left))
        if not chunk:
            raise util.err(DatError, "rules.dat.cut_field")
        left -= len(chunk)


def _open(src):
    """(file object, close it?) for bytes, a path or a binary file object."""
    if isinstance(src, (bytes, bytearray, memoryview)):
        return io.BytesIO(bytes(src)), True
    if isinstance(src, str):
        return open(src, "rb"), True
    return src, False


def entries(src, max_entry=MAX_ENTRY):
    """The top-level entries (field 1, length-delimited) one at a time, as bytes; other top-level fields are
    skipped."""
    f, close = _open(src)
    try:
        while True:
            key = _stream_varint(f)
            if key is None:
                return
            field, wt = key >> 3, key & 7
            if field == 0:
                raise util.err(DatError, "rules.dat.field0")
            if wt == WT_LEN:
                ln = _stream_varint(f)
                if ln is None:
                    raise util.err(DatError, "rules.dat.cut_header")
                if ln > max_entry:
                    raise util.err(DatError, "rules.dat.too_long", size=util.msg_n("util.bytes", ln))
                if field == 1:
                    yield _read_exact(f, ln)
                else:
                    _skip(f, ln)
            elif wt == WT_VARINT:
                if _stream_varint(f) is None:
                    raise util.err(DatError, "rules.dat.cut_header")
            elif wt == WT_I64:
                _skip(f, 8)
            elif wt == WT_I32:
                _skip(f, 4)
            else:
                raise util.err(DatError, "rules.dat.wire_type", field=field, wt=wt)
    finally:
        if close:
            f.close()


def _text(view):
    return bytes(view).decode("utf-8", "replace")


def code_of(entry):
    """The code of a GeoIP/GeoSite entry, upper-case; None when it has none."""
    for field, wt, val in fields(entry):
        if field == 1 and wt == WT_LEN:
            return _text(val).strip().upper()
    return None


def codes(src):
    """Codes of a list in file order (upper-case)."""
    out = []
    for entry in entries(src):
        c = code_of(entry)
        if c:
            out.append(c)
    return out


def _wanted(codes_):
    return None if codes_ is None else set(str(c).strip().upper() for c in codes_)


# ── GeoIP ──────────────────────────────────────────────────────────────────

def _cidr(view):
    """(version, network as int, prefix) or None for a broken CIDR (an ip of another length, a prefix too big).
    Host bits are cleared."""
    ip = b""
    prefix = 0
    for field, wt, val in fields(view):
        if field == 1 and wt == WT_LEN:
            ip = bytes(val)
        elif field == 2 and wt == WT_VARINT:
            prefix = val
    if len(ip) == 4:
        bits = 32
    elif len(ip) == 16:
        bits = 128
    else:
        return None
    if prefix > bits:
        return None
    n = int.from_bytes(ip, "big")
    mask = ((1 << bits) - 1) ^ ((1 << (bits - prefix)) - 1)
    return (4 if bits == 32 else 6), n & mask, prefix


def geoip(src, codes_=None):
    """{CODE: {"v4": [(network int, prefix)], "v6": [(network int, prefix)], "bad": n, "reverse": bool}} of the
    entries asked for (all when ``codes_`` is None). Entries with the same code are merged."""
    want = _wanted(codes_)
    out = {}
    for entry in entries(src):
        code = code_of(entry)
        if not code or (want is not None and code not in want):
            continue
        rec = out.setdefault(code, {"v4": [], "v6": [], "bad": 0, "reverse": False})
        for field, wt, val in fields(entry):
            if field == 2 and wt == WT_LEN:
                c = _cidr(val)
                if c is None:
                    rec["bad"] += 1
                else:
                    rec["v4" if c[0] == 4 else "v6"].append((c[1], c[2]))
            elif field == 3 and wt == WT_VARINT:
                rec["reverse"] = bool(val)
    return out


# ── GeoSite ────────────────────────────────────────────────────────────────

def _domain(view):
    typ = PLAIN
    value = ""
    for field, wt, val in fields(view):
        if field == 1 and wt == WT_VARINT:
            typ = val
        elif field == 2 and wt == WT_LEN:
            value = _text(val)
        # 3: attributes (@cn, @ads ...) — not used by the rules
    return typ, value


def geosite(src, codes_=None):
    """{CODE: [(type, value)]} of the entries asked for (all when ``codes_`` is None); ``type`` — PLAIN, REGEX,
    DOMAIN, FULL (or another number of a newer format: the caller drops it)."""
    want = _wanted(codes_)
    out = {}
    for entry in entries(src):
        code = code_of(entry)
        if not code or (want is not None and code not in want):
            continue
        doms = out.setdefault(code, [])
        for field, wt, val in fields(entry):
            if field == 2 and wt == WT_LEN:
                doms.append(_domain(val))
    return out

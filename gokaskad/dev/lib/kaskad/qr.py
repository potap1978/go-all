"""QR codes as PNG for ``kaskadctl gw issue --png`` (the MCP server hands them to the agent as pictures).

``qrencode -t PNG -o -`` with the payload on stdin: a key never sits in argv (/proc/<pid>/cmdline is open to every
user of the node). The same way as the bot (kaskad.bot.gateway.qr_png), apart from it so that the gateway does not
import the bot. No qrencode — QrMissing: the caller says so in words, the text of the settings stays.
"""

import base64
import math
import os
import re
import shutil
import subprocess

from . import util
from .errors import KaskadError
from .i18n import tr

QRENCODE = "qrencode"
TIMEOUT = 20
_LAZY = {"MISSING_TEXT": "qr.missing"}


def __getattr__(name):
    """MISSING_TEXT in the language of the moment (PEP 562): a text of the module is not fixed at import."""
    if name in _LAZY:
        return tr(_LAZY[name])
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


# For which app a code is (the person's page names it under the code, 3.3.2 №8): by its text — the same rule as
# KaskadQRSlides.kind in static/qr-slides.js (tests/test_qs332_a.py runs both on the same texts).
QR_MAGIC = 1984                                   # vendor/awgkeys.QR_MAGIC: a part of vpn:// (">hBBI" header)
_AWG_FIELD = re.compile(r"^\s*(Jc|Jmin|Jmax|S[1-4]|H[1-4]|I[1-5])\s*=", re.M)
_B64URL = re.compile(r"^[A-Za-z0-9_-]+=*$")


def qr_part(text):
    """``{"n", "i"}`` of a part of an AmneziaVPN key (magic, count, index of its header), None for anything else."""
    s = str(text or "").strip()
    if len(s) < 12 or not _B64URL.match(s):
        return None
    try:
        head = base64.urlsafe_b64decode(s[:8])
    except (ValueError, TypeError):
        return None
    if len(head) < 4 or (head[0] << 8 | head[1]) != QR_MAGIC or not head[2] or head[3] >= head[2]:
        return None
    return {"n": head[2], "i": head[3]}


def qr_for(text):
    """"vpnpart" | "vpn" | "vless" | "hy2" | "awgconf" | "wgconf" | "url" | "text"."""
    s = str(text or "").strip()
    low = s.lower()
    if low.startswith("vpn://"):
        return "vpn"
    if low.startswith("vless://"):
        return "vless"
    if low.startswith("hysteria2://") or low.startswith("hy2://"):
        return "hy2"
    if re.search(r"^\[Interface\]", s, re.M):
        return "awgconf" if _AWG_FIELD.search(s) else "wgconf"
    if low.startswith("http://") or low.startswith("https://"):
        return "url"
    if qr_part(s):
        return "vpnpart"
    return "text"


# ── Can a phone camera take a code from the screen (3.3.3, owner's №38) ─────────────────────────────────────────
# The window «Выдать настройки» draws a key code in one white frame (give.css .gs-qr__frame: FRAME_PAD_PX of white on
# each side) with a quiet zone of QUIET modules — with the pad that is ≥ 4 modules of white at the limit. The frame is
# sized by the limit of a module, not fixed (owner, 3.3.3: «не более 2 частей», the standard AmneziaVPN app expects 2):
# - on a computer — the side inside is ⌈MIN_MODULE_PX × modules⌉, no less than FRAME_PX (312 outside) and no more
#   than FRAME_MAX_PX (FRAME_MAX_OUTER_PX outside; the right column of the window holds it, give.css .gs-col--r);
# - on a phone — the whole width there is: min(FRAME_MAX_OUTER_PX, 100vw − PHONE_GUTTER_PX) without the pad.
# The limit is the physical size of a module: the scanning camera does not focus closer than ~15–20 cm, and a module
# of ≈0.6–0.8 mm is ≈4–5 pixels of its preview — a margin against blur and moiré (k3/handoff/FIX-333-qr.md). On a
# computer MIN_MODULE_PX = 4 (the owner's rule; a laptop CSS px ≈0.2 mm), on a phone MIN_MODULE_PX_PHONE = 3.6
# (≈0.16 mm per CSS px). A code above the limit of its frame is not shown as the way to take a key: on a computer —
# «QR слишком большой», on a phone — folded with «QR плотный…». An AmneziaVPN key is cut into MAX_PARTS = 2 parts at
# most (one when it fits MAX_VERSION of a computer). The same numbers and the same rule of the level —
# KaskadQRSlides.fit in static/qr-slides.js and KaskadQR.encode in static/qr.js (tests/test_333q.py runs them side
# by side; tests read give.css too).
FRAME_OUTER_PX = 312
FRAME_MAX_OUTER_PX = 436
FRAME_PAD_PX = 8
PHONE_GUTTER_PX = 72
PHONE_MIN_VIEWPORT = 360
FRAME_PX = FRAME_OUTER_PX - 2 * FRAME_PAD_PX
FRAME_MAX_PX = FRAME_MAX_OUTER_PX - 2 * FRAME_PAD_PX
QUIET = 2
MIN_MODULE_PX = 4.0
MIN_MODULE_PX_PHONE = 3.6
MAX_PARTS = 2
_ECC_PER_BLOCK = {   # ISO/IEC 18004 table 9, versions 1..40 (the same as static/qr.js)
    "L": [7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30,
          30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    "M": [10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28,
          28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
}
_BLOCKS = {
    "L": [1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18,
          19, 19, 20, 21, 22, 24, 25],
    "M": [1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31,
          33, 35, 37, 38, 40, 43, 45, 47, 49],
}


def _raw_modules(ver):
    r = (16 * ver + 128) * ver + 64
    if ver >= 2:
        n = ver // 7 + 2
        r -= (25 * n - 10) * n - 55
        if ver >= 7:
            r -= 36
    return r


def byte_capacity(ver, level):
    """How many bytes a code of version ``ver`` and level "L" | "M" holds in the byte mode."""
    words = _raw_modules(ver) // 8 - _ECC_PER_BLOCK[level][ver - 1] * _BLOCKS[level][ver - 1]
    return (words * 8 - 4 - (8 if ver <= 9 else 16)) // 8


def version_of(text):
    """``(version, level)`` of the code of ``text`` as every page draws it, (None, None) — not into version 40. The
    level: the smallest version at "L", then "M" when it fits the same version (a screen does not get dirty — the
    size of a module matters, not the error correction). KaskadQR.encode and userpage.qr_matrix pick it so too."""
    n = len(str(text).encode("utf-8"))
    for ver in range(1, 41):
        if byte_capacity(ver, "L") >= n:
            return ver, ("M" if byte_capacity(ver, "M") >= n else "L")
    return None, None


def size_of(ver):
    return 4 * ver + 17


def module_px(ver, frame=FRAME_MAX_PX):
    """CSS px of one module of a code of version ``ver`` in a frame of ``frame`` CSS px inside (with QUIET)."""
    return float(frame) / (size_of(ver) + 2 * QUIET)


def frame_px(viewport=None):
    """The largest CSS px inside the frame of a key code: on a computer (``viewport`` None) — FRAME_MAX_PX, on a phone
    of ``viewport`` CSS px wide — min(FRAME_MAX_OUTER_PX, viewport − PHONE_GUTTER_PX) without the pad (give.css,
    @media ≤ 767px)."""
    if viewport is None:
        return FRAME_MAX_PX
    return max(0, min(FRAME_MAX_OUTER_PX, int(viewport) - PHONE_GUTTER_PX) - 2 * FRAME_PAD_PX)


def frame_for(ver, viewport=None):
    """CSS px inside the frame the window gives a code of version ``ver``: a phone — its whole width; a computer — by
    the limit (⌈MIN_MODULE_PX × modules⌉) between FRAME_PX and FRAME_MAX_PX."""
    if viewport is not None:
        return frame_px(viewport)
    need = math.ceil(MIN_MODULE_PX * (size_of(ver) + 2 * QUIET) - 1e-9) if ver else FRAME_PX
    return max(FRAME_PX, min(FRAME_MAX_PX, need))


def max_version(frame=FRAME_MAX_PX, min_px=MIN_MODULE_PX):
    """The largest version whose module in ``frame`` CSS px is ≥ ``min_px``; 0 — none."""
    return max([v for v in range(1, 41) if module_px(v, frame) >= min_px] or [0])


MAX_VERSION = max_version()


def scannable(text):
    """A phone camera takes the code of ``text`` from the frame of a computer (version ≤ MAX_VERSION)."""
    ver = version_of(text)[0]
    return bool(ver) and ver <= MAX_VERSION


def part_size(total, header=8, version=MAX_VERSION):
    """Bytes of one part of an AmneziaVPN key of ``total`` compressed bytes: one part when it — a header of ``header``
    bytes plus the piece in base64url — fits ``version``, else MAX_PARTS (2) parts of one size, whatever their version
    (owner, 3.3.3: the standard app expects 2; a frame of the window is sized for them). The app takes any size (its
    own export cuts by 850: 2 parts of version 24 and a short one)."""
    room = byte_capacity(version, "L") * 3 // 4 - header          # base64url without "=": 4 chars per 3 bytes
    count = 1 if int(total) <= room else MAX_PARTS
    return max(1, -(-int(total) // count))


class QrMissing(Exception):
    pass


def qrencode_path():
    return shutil.which(QRENCODE, path=os.pathsep.join([os.environ.get("PATH", ""), "/usr/bin", "/usr/local/bin"]))


def png(text):
    """PNG bytes of a QR code of ``text``. QrMissing — no qrencode; KaskadError — it failed (the reason redacted)."""
    exe = qrencode_path()
    if not exe:
        raise QrMissing()
    try:
        p = subprocess.run([exe, "-t", "PNG", "-s", "6", "-m", "2", "-o", "-"], input=str(text).encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise KaskadError(tr("qr.failed", error=type(e).__name__))
    if p.returncode != 0 or not p.stdout.startswith(b"\x89PNG"):
        why = util.redact((p.stderr or b"").decode("utf-8", "replace").strip()[:200]) or tr("qr.rc", rc=p.returncode)
        raise KaskadError(tr("qr.not_drawn", why=why))
    return p.stdout


def png_b64(text):
    return base64.b64encode(png(text)).decode("ascii")


def add_pngs(res):
    """``gw issue`` with pictures: every record of format ``qr`` (one code or the parts of one) and every ``link`` gets
    ``png_b64`` — a list of PNG in base64, one per code, in the order of ``body``. No qrencode or a payload too long
    for a code — ``png_error`` in words; the record and its text stay as they were. Returns ``res``."""
    for it in res.get("items") or []:
        fmt = it.get("format")
        if fmt not in ("qr", "link"):
            continue
        body = it.get("body")
        payloads = [str(x) for x in (body if isinstance(body, list) else [body]) if x]
        if not payloads:
            continue
        try:
            it["png_b64"] = [png_b64(p) for p in payloads]
        except QrMissing:
            it["png_error"] = tr("qr.missing")
        except KaskadError as e:
            it["png_error"] = str(e)
    return res

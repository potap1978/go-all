"""The one rule of a name for every entity — people, devices, exits, protocols (inbounds), subscriptions of exits,
forwards, monitored servers, groups and levels of exits (docs/k3/SPEC-stage1-rename.md §2). A name is a label: the
technical id never follows it. stdlib only.

    check(value, what="name", lang=None) -> str   the clean name or KaskadError(code=USAGE) with a RU/EN text and
                                                  data {"reason": empty|long|char|unassigned|
                                                  private|invisible|type, "what", "max", ...}: a
                                                  refusal, never a silent replacement (on create and on rename)
    same(a, b) -> bool                            names equal for duplicates: NFKC + casefold + collapsed spaces
                                                  («Вена» = «вена», «Ｖｅｎａ» = «Vena»)
    duplicates(items, name, self_id=None) -> [id] ids of ``items`` with the same name, except ``self_id``;
                                                  ``items`` — dicts with "id" and "name" or pairs (id, name)
    fit(head, tail="", limit=MAX) -> str          ``head`` + ``tail`` in ``limit`` code points: a long ``head`` is cut
                                                  with «…», the ``tail`` stays whole — only for names Kaskad makes
                                                  itself («{кто} · {платформа}» of the wizard), never for a typed one

The clean name: NFC; spaces at the ends dropped, runs of spaces inside collapsed into one; 1..MAX code points.
Refused anywhere in the value (before any trimming — a trailing «\\n» is a refusal, not a trim):
  * control characters (Cc: \\x00-\\x1f, \\x7f, \\x80-\\x9f), line and paragraph separators (Zl, Zp), lone
    surrogates (Cs) — reason «char»;
  * unassigned code points (Cn: unknown to the Unicode of this Python — U+2065, U+FFFF; a newer Python may know
    them, so the name is refused rather than compared by a guess) — reason «unassigned». EXCEPT the emoji blocks
    EMOJI_BLOCKS (U+1F000-U+1F0FF, U+1F300-U+1FAFF: only So/Sk there, no decompositions in any Unicode): a code
    point of them unknown to this Python is a new emoji (🫩 of Unicode 16 on Python 3.9-3.13) and passes as So —
    the same name passes or not on every Python of a node;
  * private use (Co: U+E000-U+F8FF, planes 15-16 — every font draws them its own way or not at all) — «private»;
  * format characters (Cf) — bidi controls (U+200E, U+200F, U+202A-U+202E, U+2066-U+2069, U+061C) and invisible
    ones (U+200B, U+2060-U+2064, U+FEFF, U+00AD ...) — EXCEPT U+200C/U+200D (ZWNJ, ZWJ: emoji families and
    professions, some scripts) and the tag characters U+E0020-U+E007F ONLY inside a flag sequence (U+1F3F4, tags,
    U+E007F: the flags of England, Scotland, Wales);
  * «|» — the separator of /etc/kaskad/aliases and of state.clean_name;
  * a name with no visible character at all (fillers U+3164, U+2800, U+115F, variation selectors, combining
    marks only) — reason «invisible».
Duplicates compare without the zero-width characters (Cf incl. ZWJ/ZWNJ and tags, variation selectors, U+034F, the
Mongolian selectors): «Вена» and «Вена» + ZWJ are the same name; the blank fillers (U+3164, U+FFA0, U+2800, U+115F,
U+1160) and every space (Zs) are a space: «Вена⠀Вена» and «Вена Вена» are the same name.
Allowed: «<», «>», «&», quotes, «\\», a leading «-», emoji — HTML and shell safety is the job of the output
(textContent, html.escape, telegram.h, printf '%s'), not of the storage.
Old names (longer than MAX, made before stage 1) are read and shown as they are: check() is only for a new value.
"""

import unicodedata

from . import errors
from .i18n import tr

MAX = 40
WHAT = ("name", "user", "device", "exit", "inbound", "sub", "forward", "server", "group", "level")
_ALLOWED_CF = frozenset((0x200C, 0x200D))
_BAD_CATEGORIES = frozenset(("Cc", "Cs", "Zl", "Zp"))
_REASON_OF = {"Cn": "unassigned", "Co": "private"}
_FLAG, _TAG_END = 0x1F3F4, 0xE007F
_BLANKS = frozenset((0x115F, 0x1160, 0x2800, 0x3164, 0xFFA0))      # look like a space: a space when compared
_FILLERS = _BLANKS | frozenset((0x034F, 0x17B4, 0x17B5, 0x180B, 0x180C, 0x180D, 0x180F))
_NOT_SEEN = frozenset(("Cf", "Mn", "Me", "Co", "Cc", "Zs", "Zl", "Zp", "Cs"))
EMOJI_BLOCKS = ((0x1F000, 0x1F0FF), (0x1F300, 0x1FAFF))    # Mahjong-Playing Cards; Misc Symbols and Pictographs-
#                                                           Symbols and Pictographs Extended-A (REV-s1-w0-3 Н4)


def _category(ch):
    """unicodedata.category, but a code point of EMOJI_BLOCKS unknown to this Python is «So» (a newer emoji)."""
    cat = unicodedata.category(ch)
    if cat == "Cn" and any(lo <= ord(ch) <= hi for lo, hi in EMOJI_BLOCKS):
        return "So"
    return cat


def _tag(cp):
    return 0xE0020 <= cp <= 0xE007F


def _ignorable(ch):
    """Dropped before comparing names: format characters (ZWJ, ZWNJ, tags), variation selectors, fillers."""
    cp = ord(ch)
    return (cp in _FILLERS or 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF
            or unicodedata.category(ch) == "Cf")


def _invisible(ch):
    """Not seen on its own: format characters, variation selectors, fillers, combining marks, spaces, private use."""
    cp = ord(ch)
    return (cp in _FILLERS or 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF
            or _category(ch) in _NOT_SEEN)


def _bad_char(ch):
    """None or the reason of a refused character: «char», «unassigned», «private»."""
    if ch == "|":
        return "char"
    cat = _category(ch)
    if cat in _BAD_CATEGORIES:
        return "char"
    if cat in _REASON_OF:
        return _REASON_OF[cat]
    if cat == "Cf" and ord(ch) not in _ALLOWED_CF:  # tags: only in a flag sequence, see _bad_at
        return "char"
    return None


def _bad_at(s):
    """(the first refused character of ``s``, reason) or None. Tag characters pass only as 🏴 + tags + U+E007F."""
    i, n = 0, len(s)
    while i < n:
        cp = ord(s[i])
        if cp == _FLAG and i + 1 < n and _tag(ord(s[i + 1])):
            j = i + 1
            while j < n and _tag(ord(s[j])) and ord(s[j]) != _TAG_END:
                j += 1
            if j < n and ord(s[j]) == _TAG_END:
                i = j + 1                              # a whole flag
                continue
            return s[i + 1], "char"                    # tags that do not end the flag
        why = _bad_char(s[i])
        if why:
            return s[i], why
        i += 1
    return None


def _fail(reason, what, lang, **kw):
    w = what if what in WHAT else "name"
    label = tr("names.what." + w, lang)
    kw.setdefault("max", MAX)
    data = dict(kw, reason=reason, what=w)
    return errors.KaskadError(tr("names.err." + reason, lang, what=label, **kw), code=errors.USAGE, data=data)


def check(value, what="name", lang=None):
    """The clean name of ``value`` (see the module) or KaskadError(code=USAGE). ``what`` — one of WHAT: the words of
    the refusal («Имя человека …»)."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise _fail("type", what, lang)
    s = unicodedata.normalize("NFC", value)
    bad = _bad_at(s)
    if bad is not None:
        raise _fail(bad[1], what, lang, char="U+%04X" % ord(bad[0]))
    s = " ".join(s.split())
    if not s:
        raise _fail("empty", what, lang)
    if all(_invisible(ch) for ch in s):
        raise _fail("invisible", what, lang)
    if len(s) > MAX:
        raise _fail("long", what, lang, n=len(s))
    return s


def fit(head, tail="", limit=MAX):
    """``head`` + ``tail`` (NFC, runs of spaces collapsed — counted as check() counts) in ``limit`` code points: when
    longer, ``head`` is cut and ends with «…», ``tail`` stays whole («Бабушка Валентина Пе… · iPhone и iPad»). The cut
    does not end on a joiner, a variation selector, a combining mark or a space (they would hang on «…»). A ``tail``
    with no room for the head — the whole string cut the same way. For a name Kaskad composes itself (the device of
    the quick setup, REV-s1-t13 Л-1): a refusal there would refuse words the person never typed."""
    def clean(v):
        return " ".join(unicodedata.normalize("NFC", str(v if v is not None else "")).split())

    h, t = clean(head), unicodedata.normalize("NFC", str(tail if tail is not None else ""))
    whole = clean(h + t)
    if len(whole) <= limit:
        return whole
    room = limit - (len(clean("x" + t)) - 1) - 1         # «…» and the tail as they stand after a head (its spaces)
    if room < 1:
        h, t, room = whole, "", limit - 1
    cut = h[:max(room, 0)]
    while cut and _invisible(cut[-1]):
        cut = cut[:-1]
    return clean(cut + "\u2026" + t)


def _key(name):
    s = unicodedata.normalize("NFKC", str(name if name is not None else ""))
    s = unicodedata.normalize("NFKC", s.casefold())
    s = "".join(" " if ord(ch) in _BLANKS or unicodedata.category(ch) == "Zs" else ch
                for ch in s if ord(ch) in _BLANKS or not _ignorable(ch))
    return " ".join(s.split())


def same(a, b):
    """True when two names are the same for duplicates (NFKC, casefold, zero-width characters dropped, blank
    fillers and spaces of any kind made one space, spaces collapsed)."""
    return _key(a) == _key(b)


def duplicates(items, name, self_id=None):
    """[id] of ``items`` (dicts {"id", "name"} or pairs (id, name)) whose name is the same as ``name``, in their
    order; the item ``self_id`` is never its own duplicate; an item without a name is no duplicate."""
    want = _key(name)
    out = []
    for it in items or ():
        if isinstance(it, dict):
            iid, nm = it.get("id"), it.get("name")
        else:
            iid, nm = it
        if nm is None or (self_id is not None and iid == self_id):
            continue
        if _key(nm) == want:
            out.append(iid)
    return out

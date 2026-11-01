"""Texts for people in two languages (3.3.2, k3/handoff/I18N-3.3.2.md): Russian and English.

    tr(key, lang=None, /, **kw)    the text of ``key`` in ``lang`` (None: get_lang()); missing in that language ->
                                   the Russian text -> the key itself. ``str.format(**kw)`` ONLY when kw are given
                                   (without kw the text goes out as is); a placeholder without a value stays as
                                   ``{name}`` (never an exception in front of a person).
    trs(key, lang=None, /, **kw)   tr() that remembers its key: retr(text, lang) says it again in another language
                                   (a reply's words that also go to the journal in the server's language).
    trn(key, n, lang=None, /, **kw)  a text with a number, the replacement of util.plural(): the catalog value holds
                                   the forms through «|» — ru «одна|несколько|много» («{n} выход|{n} выхода|{n}
                                   выходов»), en «one|other» («{n} exit|{n} exits»); the form by ``n``, then
                                   ``.format(n=n, **kw)``.
    get_lang(explicit=None)        "ru" | "en": explicit -> use_lang() of this context -> env KASKAD_LANG ->
                                   KASKAD_LANG in /etc/kaskad/config -> "ru".
    use_lang(lang)                 a context manager (contextvars): the language of one request / update / job.
                                   Python threads start with an EMPTY context — a thread that must keep the
                                   language is started through bind_lang(fn) (or sets use_lang itself).
    bind_lang(fn)                  fn that runs in the language of the caller (for threading.Thread, executors).
    lang_env(env=None)             a copy of ``env`` (os.environ by default) with KASKAD_LANG = get_lang(): the
                                   environment of a child process; setenv_args() — the same as systemd-run options.
    fmt_date(x, lang=None, /, short=False)  a date the same way everywhere (bot, CLI, SSH menu, panel, wizard): ru
                                   «09.01.2027», en «Jan 9, 2027»; short — no year, only for a tight line (the main
                                   menu): ru «09.01», en «Jan 9». x — date/datetime, ISO text («2026-12-30»,
                                   «…T10:00:00Z», «20261230T100000Z») or epoch seconds; an aware time — on the clock
                                   of the server (utc=True: in UTC, for a text that says «UTC» after it).
    fmt_datetime(x, lang=None, /, seconds=False, year=None, utc=False, now=None)  ru «12.10, 02:40», en «Oct 12,
                                   02:40» — within the current year (of the same clock); another year: ru
                                   «09.01.2027, 02:40», en «Jan 9, 2027, 02:40» (24 h). year=True — always with the
                                   year. The same rule: SSH k3_date (install.sh), web KaskadLang.date/dateTime (boot.js).
    fmt_num(n, lang=None, /, digits=0)            ru «33 467», «1 234,5» (a narrow no-break space U+202F
                                                  between thousands); en «33,467», «1,234.5».
    fmt_bytes(n, lang=None, /)     ru «512 Б», «4,9 КБ» … «ТБ»; en «512 B», «4.9 KB» … «TB» (1024; bytes whole, the
                                   rest with one decimal) — replaces util.human_bytes.
    lower_first(text)              a sentence of the catalog put inside a line: its first letter small, unless the
                                   first word is a name («AmneziaWG engine: …» stays, «Движок …» -> «движок …»).
    normalize(code, default)       "ru-RU", "ru_RU.UTF-8", "RU" -> "ru"; "en-GB" -> "en"; anything else -> default.
    server_lang()                  the language of the server: env KASKAD_LANG -> /etc/kaskad/config -> "ru" (no
                                   use_lang: the journal speaks it); saved_lang() — only the file.
    set_lang(lang)                 change the language of the server (one for the whole node: installer, SSH menu,
                                   kaskadctl, wizard, panel, bot): KASKAD_LANG="ru|en" written atomically into
                                   /etc/kaskad/config (other lines kept). -> "ru" | "en"; ValueError (text via tr)
                                   for anything else. ``kaskadctl lang get|set`` (i18n/cli.py) calls the same.
                                   Long-running processes see the change at once (get_lang() rereads the file when
                                   its mtime/size changes) unless KASKAD_LANG is in their environment.

Catalogs: ``catalog/<lang>/<area>.json`` next to this file, one flat JSON object per area, every key begins with
``<area>.`` (``gw.exit.down``). Each area belongs to one author; no shared area (3.3.2: an unused common.json went).
Loaded lazily, once per language (reload() drops the cache: tests). Texts of the catalogs have no literal braces
(a text given kw would need ``{{ }}``) and «|» only as the separator of number forms (trn keys). The key, the
number and the language are positional-only: every placeholder name is free (``{key}``, ``{lang}`` too); the
language goes second — ``tr("k", "en")`` (``lang="en"`` is taken as the language only when the text has no
``{lang}``); trn fills ``{n}`` itself.

Texts at import time (module constants) would freeze one language: keep the KEYS in constants, call tr() where the
text goes out. kaskad.i18n.check — the completeness checks the tests use.
"""

import contextlib
import contextvars
import datetime
import functools
import json
import os
import re
import string
import threading
import time

LANGS = ("ru", "en")
DEFAULT = "ru"
ENV = "KASKAD_LANG"
PLURAL_FORMS = {"ru": 3, "en": 2}     # number of «|» forms of a trn() text

CATALOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog")   # tests patch it, then reload()

_ctx = contextvars.ContextVar("kaskad_lang", default=None)
_lock = threading.Lock()
_catalogs = {}                 # lang -> {key: text}
_conf_cache = [None, None]     # [(path, inode, mtime_ns, size), lang or None]
_CONF_LINE = re.compile(r"^\s*(?:export\s+)?KASKAD_LANG\s*=\s*(.*?)\s*$")


def normalize(code, default=None):
    """The language of a code: "ru*" -> "ru", "en*" -> "en", else ``default``. Takes the first tag of a list
    ("ru-RU,ru;q=0.9,en;q=0.8" -> "ru")."""
    if not code:
        return default
    s = str(code).strip().lower()
    s = re.split(r"[,;\s]", s, maxsplit=1)[0]
    for lang in LANGS:
        if s == lang or s.startswith(lang + "-") or s.startswith(lang + "_") or s.startswith(lang + "."):
            return lang
    return default


def _config_lang():
    from .. import paths                      # here: kaskad.paths resolves KASKAD_ROOT at call time
    p = paths.config()
    try:
        st = os.stat(p)
        # the inode too (T-67-332b №4): set_lang() writes a new file (atomic_write: a new inode) of the same size —
        # «ru» and «en» — and another process (the panel) must not take it for the old one within one tick of mtime
        sig = (p, st.st_ino, st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    if _conf_cache[0] == sig:
        return _conf_cache[1]
    lang = None
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _CONF_LINE.match(line)
                if m:
                    v = m.group(1)
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                        v = v[1:-1]
                    lang = normalize(v)        # the last assignment wins, as in bash `source`
    except (OSError, IOError):
        return None
    _conf_cache[0], _conf_cache[1] = sig, lang
    return lang


def server_lang():
    """"ru" | "en" of the server as this process knows it: env KASKAD_LANG -> /etc/kaskad/config -> "ru" (use_lang()
    not looked at: the journal, the bot's command menu speak it whatever the language of a request)."""
    return normalize(os.environ.get(ENV)) or _config_lang() or DEFAULT


def saved_lang():
    """"ru" | "en" written in /etc/kaskad/config ("ru" when none)."""
    return _config_lang() or DEFAULT


def set_lang(lang):
    """The language of the server -> "ru" | "en". Exact codes only ("ru", "en", any case, around spaces): a typo
    must not switch the node to English silently."""
    from .. import paths, util
    v = str(lang or "").strip().lower()
    if v not in LANGS:
        raise ValueError(tr("lang.bad", value=str(lang if lang is not None else "")))
    p = paths.config()
    with util.config_locked():                 # one writer of the file at a time (util.config_locked)
        keep = [l for l in util.config_lines(util.read_text(p)) if not _CONF_LINE.match(l)]
        util.ensure_dir(paths.etc(), 0o700)
        util.atomic_write(p, "\n".join(keep + ['%s="%s"' % (ENV, v)]) + "\n", 0o600)
    _conf_cache[0] = _conf_cache[1] = None
    if os.environ.get(ENV):
        os.environ[ENV] = v                    # this process speaks the new language too
    return v


def get_lang(explicit=None):
    """"ru" | "en". ``explicit`` (a language asked for: a parameter, a header, Telegram language_code): ru* -> ru,
    anything else -> en. Then the language of this context (use_lang), env KASKAD_LANG, /etc/kaskad/config, ru."""
    if explicit:
        return normalize(explicit, "en")
    lang = _ctx.get()
    if lang:
        return lang
    lang = normalize(os.environ.get(ENV))
    if lang:
        return lang
    return _config_lang() or DEFAULT


@contextlib.contextmanager
def use_lang(lang):
    """``with use_lang("en"): ...`` — tr() without ``lang`` speaks English inside (this thread / this task).
    None or "" — keeps the language around it."""
    if not lang:
        yield get_lang()
        return
    token = _ctx.set(normalize(lang, "en"))
    try:
        yield _ctx.get()
    finally:
        _ctx.reset(token)


def current_lang():
    """The language set by use_lang() in this context, or None."""
    return _ctx.get()


def bind_lang(fn, lang=None):
    """``fn`` that runs in ``lang`` (None: the language of the caller now) — for threading.Thread(target=...),
    executors, callbacks: a new thread does not see the context of the one that started it."""
    lang = normalize(lang, "en") if lang else get_lang()

    @functools.wraps(fn)
    def run(*a, **kw):
        with use_lang(lang):
            return fn(*a, **kw)
    return run


def lang_env(env=None, lang=None):
    """A copy of ``env`` (os.environ when None) with KASKAD_LANG of the caller: a child process (kaskadctl, a job)
    speaks the same language."""
    out = dict(os.environ if env is None else env)
    out[ENV] = normalize(lang, "en") if lang else get_lang()
    return out


def setenv_args(lang=None):
    """Options of ``systemd-run`` that hand the language to the unit: ["--setenv=KASKAD_LANG=en"]."""
    return ["--setenv=%s=%s" % (ENV, normalize(lang, "en") if lang else get_lang())]


def _load(lang):
    out = {}
    d = os.path.join(CATALOG_DIR, lang)
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".json") and not n.startswith("."))
    except OSError:
        names = []
    for n in names:
        try:
            with open(os.path.join(d, n), "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, IOError, ValueError):
            continue                           # a broken file must not take the program down (check.py reports it)
        if isinstance(doc, dict):
            out.update((k, v) for k, v in doc.items() if isinstance(k, str) and isinstance(v, str))
    return out


def catalog(lang):
    """{key: text} of a language (loaded once)."""
    cat = _catalogs.get(lang)
    if cat is None:
        with _lock:
            cat = _catalogs.get(lang)
            if cat is None:
                cat = _load(lang)
                _catalogs[lang] = cat
    return cat


def reload():
    """Forget the loaded catalogs and the cached config (tests; a hot update)."""
    with _lock:
        _catalogs.clear()
    _conf_cache[0] = _conf_cache[1] = None


class _Keep(dict):
    def __missing__(self, key):
        return "{" + key + "}"


_fmt = string.Formatter()


def _format(text, kw):
    try:
        return _fmt.vformat(text, (), _Keep(kw))
    except (ValueError, IndexError, KeyError, AttributeError, TypeError):
        return text


def lookup(key, lang=None):
    """The raw text of ``key`` (lang -> ru), or None."""
    lang = normalize(lang, "en") if lang else get_lang()
    text = catalog(lang).get(key)
    if text is None and lang != DEFAULT:
        text = catalog(DEFAULT).get(key)
    return text


def has(key, lang=None):
    return lookup(key, lang) is not None


def _placeholders(text):
    try:
        return set(f for _l, f, _s, _c in _fmt.parse(text or "") if f is not None)
    except ValueError:
        return set()


def _lang_from_kw(key, lang, kw):
    """``tr(key, lang="en")`` written by habit: when the Russian text has no {lang}, it is the language, not a
    placeholder (a silent «wrong language» otherwise)."""
    if lang is None and "lang" in kw and "lang" not in _placeholders(catalog(DEFAULT).get(key)):
        return kw.pop("lang")
    return lang


def tr(_key, _lang=None, /, **kw):
    """The text of ``_key`` in ``_lang`` with ``{name}`` filled from ``kw`` (only when kw are given); the key itself
    when no catalog has it. Positional-only: any placeholder name works, ``{key}`` and ``{lang}`` too."""
    kw = dict(kw)
    _lang = _lang_from_kw(_key, _lang, kw)
    text = lookup(_key, _lang)
    if text is None:
        return _key
    _check_filled(_key, text, kw)
    out = _format(text, kw) if kw else text
    return Said(out, _key, kw) if _KEEP[0] else out


# П3-а (B11 «{cpu}», REV-ns-b14-3 «{mb}»): a placeholder left unfilled is a raw «{name}» on a screen. In the tests
# (K3_TR_STRICT=1 — tools/k3_test.sh, tools/test_select.py) tr()/trn() raise; in production — silent, as before, and one
# line per key in the system journal (syslog -> journald).
STRICT_ENV = "K3_TR_STRICT"
# П3-б (REV-ns-p3a З-1): Unfilled is an Exception, and «try … except Exception» around a tr() swallows it — the test stays
# green. So in the strict mode the key is also written to this file (one line «key: {name},…», no argument values);
# tools/k3_test.sh and tools/test_select.py fail when it is not empty.
STRICT_LOG_ENV = "K3_TR_STRICT_LOG"
_unfilled_said = set()


def strict_env():
    """The strict mode for a child with an environment of its own (``env={...}``): the two variables, if set."""
    return dict((k, os.environ[k]) for k in (STRICT_ENV, STRICT_LOG_ENV) if k in os.environ)


class Unfilled(AssertionError):
    """K3_TR_STRICT=1: tr()/trn() of a text with a «{name}» no argument fills."""


def _unfilled(text, kw):
    try:
        names = [f for _l, f, _s, _c in _fmt.parse(text or "") if f is not None]
    except ValueError:
        return []
    return sorted(set(n for n in (re.split(r"[.[]", f, maxsplit=1)[0] for f in names) if n not in kw))


def _check_filled(key, text, kw):
    miss = _unfilled(text, kw) if "{" in (text or "") else []
    if not miss:
        return
    if os.environ.get(STRICT_ENV) == "1":
        _strict_note(key, miss)
        raise Unfilled("tr(%r): no %s" % (key, ", ".join("{%s}" % n for n in miss)))
    if key in _unfilled_said:
        return
    _unfilled_said.add(key)
    try:
        import syslog
        syslog.openlog("kaskad", 0, syslog.LOG_USER)
        syslog.syslog(syslog.LOG_WARNING, "i18n: %s: unfilled %s" % (key, ",".join(miss)))
    except Exception:                                    # no syslog (not Linux/macOS): silent, as before
        pass


def _strict_note(key, miss):
    """The strict mode: «key: {a},{b}» appended to $K3_TR_STRICT_LOG (O_APPEND — the unit modules run in parallel)."""
    path = os.environ.get(STRICT_LOG_ENV)
    if not path:
        return
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, ("%s: %s\n" % (key, ",".join("{%s}" % n for n in miss))).encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        pass


class Said(str):
    """A text of tr() that remembers what it said (``key``, ``kw``): a plain str for everyone, and ``retr()`` can say
    it again in another language — words made for a reply that must also go to the journal (server's language).
    3.3.3: ``plural`` — said by trn() (the number is kw["n"]); ``parts`` — a text glued from pieces (``said + " · " +
    said``, join()): each piece is said again on its own."""

    def __new__(cls, text, key=None, kw=None, plural=False, parts=None):  # copy/pickle: the text only, then __dict__
        obj = str.__new__(cls, text)
        obj.key, obj.kw, obj.plural, obj.parts = key, dict(kw or {}), plural, (list(parts) if parts else None)
        return obj

    def _pieces(self):
        return self.parts if getattr(self, "parts", None) else [self]

    def __add__(self, other):
        if not isinstance(other, str):
            return NotImplemented
        return Said(str.__add__(self, other), parts=self._pieces() + (other._pieces() if isinstance(other, Said)
                                                                       else [str(other)]))

    def __radd__(self, other):
        if not isinstance(other, str):
            return NotImplemented
        return Said(str(other) + str(self), parts=[str(other)] + self._pieces())


# 3.3.3: while keeping() is on (the process of a job of the wizard), tr()/trn() return Said — every row of the
# progress keeps its key and arguments, and the page gets it in ITS language (said_doc / said_text below), even when
# the language was switched while the job ran. Process-wide on purpose: the steps run threads of their own.
_KEEP = [0]


@contextlib.contextmanager
def keeping():
    _KEEP[0] += 1
    try:
        yield
    finally:
        _KEEP[0] -= 1


def join(sep, items):
    """``sep.join(items)`` that keeps the Said pieces (a plain str.join() forgets them)."""
    items = list(items)
    out = []
    for i, x in enumerate(items):
        if i:
            out.append(str(sep))
        out.extend(x._pieces() if isinstance(x, Said) else [str(x)])
    text = str(sep).join(str(x) for x in items)
    return Said(text, parts=out) if any(isinstance(x, Said) for x in items) else text


def said_doc(text, clean=None):
    """A Said as JSON — {"k": key, "a": {arguments}, "n": 1 when plural} or {"cat": [pieces]}; None for a plain text
    (nothing to say again). ``clean`` — applied to every plain string inside (util.redact for secrets)."""
    if not isinstance(text, Said):
        return None
    clean = clean or (lambda x: x)

    def arg(v):
        if isinstance(v, Said) and (v.key or getattr(v, "parts", None)):
            return said_doc(v, clean)
        if isinstance(v, str):
            return clean(str(v))
        if v is None or isinstance(v, (bool, int, float)):
            return v
        return clean(str(v))
    if getattr(text, "parts", None):
        return {"cat": [arg(p) for p in text.parts]}
    if not text.key:
        return None
    out = {"k": text.key, "a": dict((str(k), arg(v)) for k, v in text.kw.items() if k != "n" or not text.plural)}
    if text.plural:
        out["n"] = text.kw.get("n")
    return out


def said_known(doc):
    """True when every key of a said_doc() is in the catalog — a job.json of another version (a key renamed) or a
    broken catalog file: say the words it was written with, never the bare key (3.3.3)."""
    if isinstance(doc, list):
        return all(said_known(x) for x in doc)
    if not isinstance(doc, dict):
        return True
    if "cat" in doc:
        return all(said_known(x) for x in doc.get("cat") or [])
    return bool(doc.get("k")) and has(doc["k"]) and all(said_known(v) for v in (doc.get("a") or {}).values())


def said_text(doc, lang=None):
    """The words of said_doc() in ``lang`` (None: get_lang())."""
    if isinstance(doc, str) or doc is None or isinstance(doc, (bool, int, float)):
        return doc
    if isinstance(doc, list):
        return [said_text(x, lang) for x in doc]
    if not isinstance(doc, dict):
        return str(doc)
    if "cat" in doc:
        return "".join(str(said_text(x, lang)) for x in doc.get("cat") or [])
    kw = dict((k, said_text(v, lang) if isinstance(v, dict) else v) for k, v in (doc.get("a") or {}).items())
    if "n" in doc:
        return trn(doc.get("k") or "", doc.get("n"), lang, **kw)
    return tr(doc.get("k") or "", lang, **kw)


def trs(_key, _lang=None, /, **kw):
    """tr() that returns a Said: ``done.append(trs("gw_in_ipsec.made.ca", host=h))``."""
    return Said(tr(_key, _lang, **kw), _key, dict(kw))


def retr(text, _lang=None, /):
    """A Said (or a list of them) said again in ``_lang`` (None: the language of the caller); any other text as is.
    Arguments that are Said are said again too."""
    if isinstance(text, (list, tuple)):
        return [retr(t, _lang) for t in text]
    if isinstance(text, Said) and getattr(text, "parts", None):
        return "".join(str(retr(p, _lang)) for p in text.parts)
    if not isinstance(text, Said) or not text.key:
        return text
    kw = dict((k, retr(v, _lang)) for k, v in text.kw.items())
    if getattr(text, "plural", False):
        n = kw.pop("n", None)
        return trn(text.key, n, _lang, **kw)
    return tr(text.key, _lang, **kw)


def plural_index(n, lang):
    """The index of the «|» form: ru 0 (1, 21, 101), 1 (2–4, 22–24), 2 (5–20, 11–14, 25…); en 0 (1), 1 (other)."""
    try:
        n = abs(int(n))
    except (TypeError, ValueError):
        n = 0
    if lang == "en":
        return 0 if n == 1 else 1
    if n % 10 == 1 and n % 100 != 11:
        return 0
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return 1
    return 2


def pick_form(text, n, lang):
    """The form for ``n`` out of «a|b|c» (fewer forms than the rule wants: the last one)."""
    forms = text.split("|")
    return forms[min(plural_index(n, lang), len(forms) - 1)]


# Names that start a sentence of the catalog and stay capitalized inside a line (a word with a capital or a digit
# after its first letter — AmneziaWG, WireGuard, IKEv2, VLESS — is a name by itself and needs no entry here).
NAMES = frozenset(("Kaskad", "Telegram", "Debian", "Ubuntu", "Linux", "Windows", "Android", "Xray", "Cloudflare",
                   "Google", "Apple", "Amnezia", "Keenetic", "Docker", "Let"))
_FIRST_WORD = re.compile(r"[^\W\d_]+")


def lower_first(text):
    """A sentence of the catalog put inside a line (after a colon, «…, {text}»): its first letter small — «Движок
    AmneziaWG: …» -> «движок AmneziaWG: …», «The …» -> «the …». A first word that is a name stays as is: one with a
    capital or a digit after its first letter (AmneziaWG engine, VLESS, IKEv2) or one of NAMES. Never
    ``text[0].lower()`` by hand: in English a line often starts with a name (3.3.2: «amneziaWG engine»)."""
    if not text:
        return text
    m = _FIRST_WORD.match(text)
    if m is None:
        return text
    word = m.group(0)
    end = m.end()
    tail = text[end:end + 1]
    if any(c.isupper() for c in word[1:]) or (tail and (tail.isdigit() or tail.isupper())) or word in NAMES:
        return text
    return text[0].lower() + text[1:]


def trn(_key, _n, _lang=None, /, **kw):
    """The text of ``_key`` for the number ``_n``: ``trn("gw.exits.count", 3)`` -> "3 выхода" / "3 exits"."""
    kw = dict(kw)
    _lang = _lang_from_kw(_key, _lang, kw)
    lang = normalize(_lang, "en") if _lang else get_lang()
    text = catalog(lang).get(_key)
    used = lang
    if text is None and lang != DEFAULT:
        text, used = catalog(DEFAULT).get(_key), DEFAULT
    if text is None:
        return _key
    kw["n"] = _n
    form = pick_form(text, _n, used)
    _check_filled(_key, form, kw)
    out = _format(form, kw)
    return Said(out, _key, kw, plural=True) if _KEEP[0] else out


# ── numbers, sizes, dates on the language ─────────────────────────────────────

_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_UNITS = {"ru": ("Б", "КБ", "МБ", "ГБ", "ТБ"), "en": ("B", "KB", "MB", "GB", "TB")}


def _lang(lang):
    return normalize(lang, "en") if lang else get_lang()


THIN = "\u202f"                 # narrow no-break space: the thousands of Russian numbers (as Intl ru-RU)


def fmt_num(n, lang=None, /, digits=0):
    """33467 -> ru «33 467» (U+202F), en «33,467»; digits=1: 1234.5 -> «1 234,5» / «1,234.5». Not a number: str(n)."""
    try:
        v = float(n) if digits else int(round(float(n)))
    except (TypeError, ValueError):
        return "" if n is None else str(n)
    text = ("{:,.%df}" % digits).format(v) if digits else "{:,}".format(v)
    if _lang(lang) == "ru":
        text = text.replace(",", THIN).replace(".", ",")
    return text


def fmt_bytes(n, lang=None, /):
    """5017 -> ru «4,9 КБ», en «4.9 KB»; 512 -> «512 Б» / «512 B»."""
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        v = 0.0
    ru = _lang(lang) == "ru"
    units = _UNITS["ru" if ru else "en"]
    for i, unit in enumerate(units):
        if abs(v) < 1024 or i == len(units) - 1:
            num = ("%d" % v) if i == 0 else ("%.1f" % v)
            return "%s %s" % (num.replace(".", ",") if ru else num, unit)
        v /= 1024.0
    return ""


_FRACTION = re.compile(r"(\d{2}:\d{2}:\d{2})\.\d+")


def _when(x, utc=False):
    """datetime (naive: as is; aware: on the clock of the server, utc=True — in UTC) or date, or None."""
    def clock(d):
        return d.astimezone(datetime.timezone.utc) if utc else d.astimezone()
    if x is None or x == "":
        return None
    if isinstance(x, datetime.datetime):
        return clock(x) if x.tzinfo is not None else x
    if isinstance(x, datetime.date):
        return x
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        try:
            return clock(datetime.datetime.fromtimestamp(float(x), datetime.timezone.utc))
        except (OverflowError, OSError, ValueError):
            return None
    t = str(x).strip()
    if re.match(r"^\d{8}T\d{6}Z$", t):
        try:
            return clock(datetime.datetime.strptime(t, "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc))
        except ValueError:
            return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", t):
        try:
            return datetime.date(int(t[:4]), int(t[5:7]), int(t[8:10]))
        except ValueError:
            return None
    t = _FRACTION.sub(r"\1", t.replace(" ", "T", 1))
    if t.endswith(("Z", "z")):
        t = t[:-1] + "+00:00"
    try:
        d = datetime.datetime.fromisoformat(t)
    except ValueError:
        return None
    return clock(d) if d.tzinfo is not None else d


def _day(d, lang, year=True):
    if lang == "ru":
        return ("%02d.%02d.%04d" % (d.day, d.month, d.year)) if year else ("%02d.%02d" % (d.day, d.month))
    return ("%s %d, %04d" % (_MONTHS_EN[d.month - 1], d.day, d.year)) if year else \
        ("%s %d" % (_MONTHS_EN[d.month - 1], d.day))


def fmt_date(x, lang=None, /, short=False, utc=False):
    """ru «09.01.2027», en «Jan 9, 2027»; short=True (a tight line): ru «09.01», en «Jan 9». Not a date: str(x)
    ("" for None)."""
    d = _when(x, utc)
    if d is None:
        return "" if x is None else str(x)
    return _day(d, _lang(lang), not short)


def fmt_datetime(x, lang=None, /, seconds=False, year=None, utc=False, now=None):
    """ru «12.10, 02:40» / «09.01.2027, 02:40», en «Oct 12, 02:40» / «Jan 9, 2027, 02:40»: the year only when it is
    not the current one (``year=True`` — always; ``now`` — the moment to compare with, for tests). A bare date —
    fmt_date()."""
    d = _when(x, utc)
    if d is None:
        return "" if x is None else str(x)
    lang = _lang(lang)
    if not isinstance(d, datetime.datetime):
        return _day(d, lang)
    if year is None:
        n = _when(time.time() if now is None else now, utc)
        year = not isinstance(n, datetime.date) or n.year != d.year
    clock = ("%02d:%02d:%02d" % (d.hour, d.minute, d.second)) if seconds else ("%02d:%02d" % (d.hour, d.minute))
    return "%s, %s" % (_day(d, lang, bool(year)), clock)

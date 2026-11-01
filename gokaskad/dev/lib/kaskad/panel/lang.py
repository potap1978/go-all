"""The language of the web (3.3.2, k3/handoff/I18N-3.3.2.md): one language per server (``KASKAD_LANG``, chosen at
install, changed in any interface) — the panel and the quick setup follow it; the person's page (/p/) starts in it and
the visitor may pick their own (the cookie ``kaskad_lang`` of that page).

Where the language comes from:
  * a page (index.html of the panel or the wizard, user.html) — ``server_lang()`` at every answer: the server puts it in
    ``<html lang>`` (and the ETag), so the first frame is already in it;
  * an API request — the header ``X-Kaskad-Lang: ru|en`` the page sends (the language it was loaded in);
    ``request_lang()``; the panel server wraps the request in ``use()`` with it, so every ``tr()`` deep down (setup
    steps, gateway, site) answers in it.

The core — ``tr``, ``get_lang``, ``use_lang``, ``set_lang``, ``server_lang`` — is ``kaskad.i18n`` (E1)."""

import contextlib

from .. import i18n
from ..i18n import tr

LANGS = ("ru", "en")
HEADER = "x-kaskad-lang"
COOKIE = "kaskad_lang"


def norm(value):
    """``"ru"``/``"en"`` from a header, cookie or body value (``en-US`` → ``en``); None for anything else."""
    v = str(value or "").strip().lower()[:2]
    return v if v in LANGS else None


def server_lang():
    """The language of the server (``KASKAD_LANG`` of /etc/kaskad/config): what the pages of the panel and the wizard
    are rendered in."""
    try:
        return norm(i18n.server_lang()) or "ru"
    except Exception:                     # noqa: BLE001 — a broken config never breaks a page
        return "ru"


def request_lang(headers):
    """The language of an API request: the header ``X-Kaskad-Lang`` (lower-case header names), else None."""
    return norm((headers or {}).get(HEADER))


def cookie_lang(headers):
    """The visitor's own language on the person's page: the cookie ``kaskad_lang``, else None."""
    raw = (headers or {}).get("cookie") or ""
    for part in raw.split(";"):
        k, sep, v = part.strip().partition("=")
        if sep and k.strip() == COOKIE:
            return norm(v.strip().strip('"'))
    return None


@contextlib.contextmanager
def use(lang):
    """Run the block in ``lang`` (every ``tr()`` without an explicit language); without a language — as is."""
    if lang is None:
        yield
        return
    with i18n.use_lang(lang):
        yield


def current():
    """The language ``tr()`` answers in right now (the request's one inside ``use``)."""
    return norm(i18n.get_lang()) or "ru"


def set_server_lang(lang):
    """Change the language of the server (``KASKAD_LANG``) — the very function of ``kaskadctl lang set``. ValueError for
    an unknown language."""
    if norm(lang) != lang:
        raise ValueError(lang)
    return i18n.set_lang(lang)


def change(body):
    """``POST /api/lang`` (the panel, admin) and ``POST /api/setup/lang`` (the wizard, its session): ``{lang: ru|en}`` —
    the language of the server. -> {"ok", "lang"}; HttpError 400 for an unknown language, 500 when not saved."""
    from .routes import HttpError
    raw = body.get("lang") if isinstance(body, dict) else None
    want = norm(raw)
    if want is None or str(raw).strip().lower() != want:
        raise HttpError(400, tr("panel.lang.bad"), data={"field": "lang"})
    try:
        got = set_server_lang(want)
    except ValueError:
        raise HttpError(400, tr("panel.lang.bad"), data={"field": "lang"})
    except OSError as e:
        raise HttpError(500, tr("panel.lang.notSaved", why=str(e)))
    return {"ok": True, "lang": norm(got) or want}

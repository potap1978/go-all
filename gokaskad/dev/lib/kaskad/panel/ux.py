"""Panel routes of 3.3.3 UX: the Telegram bot, the keys of the AI agent, the journal and the restart of a protocol.

    GET  /api/bot                              the bot: token, chat, running, last error, «нужен выход»
    POST /api/bot/check {token}                getMe of the token (nothing saved)
    POST /api/bot/test {token?, chat_id}       save, start, a test message (kaskad.bot.onboard.apply)
    POST /api/bot/proxy {group, rev}           «Как бот ходит в Telegram»: a foreign group or "direct" (SPEC-tg-proxy
                                               Р5; gw.policy_ops.tg_proxy_set) -> {rev, group, path}
    GET    /api/mcp                            the agent's keys with «seen» (kaskad.mcp.seen), the command to connect
    POST   /api/mcp/keys {pubkey, label?}      add a public key (a private one — 400 private_key)
    DELETE /api/mcp/keys/<SHA256:…>            remove a key (the fingerprint URL-encoded)
    GET  /api/hosting                          the partner hosting for «Нет сервера за рубежом?» (product.HOSTING)
    GET  /api/gw/inbounds/<kind>/log           the last 30 lines of the protocol's journal and why it does not work
    POST /api/gw/inbounds/<kind>/restart       systemctl restart of its units, up to 4 s for «active»

The secrets (the bot token, a public key line) come in the body only and never go back: answers carry names,
fingerprints and states.

The wizard (kaskad.setup.web, /api/setup/ux/…) calls these same handlers behind its own session — one logic for the
bot and the agent's keys in the panel and in the wizard.
"""

import re
import urllib.parse

from .. import util
from ..bot import onboard
from ..errors import KaskadError
from ..i18n import tr
from .api import _KIND, _answer, _changed, _need_rev, _pcall, _st, ok, runner
from .routes import HttpError, core_error, route

_BOT_STATUS = {"bad_format": 400, "bad_token": 400, "bad_chat": 400, "no_token": 400, "net": 502, "net_proxy": 502, "telegram": 502}


def _bot_error(e):
    data = e.data if isinstance(getattr(e, "data", None), dict) else {}
    reason = data.get("reason") or "telegram"
    return HttpError(_BOT_STATUS.get(reason, 400), util.redact(str(e)), data={"reason": reason})


def _token(b):
    v = b.get("token")
    if v is None:
        return ""
    if not isinstance(v, str) or len(v) > 200:
        raise HttpError(400, tr("bot_onboard.bad_format"), data={"reason": "bad_format"})
    return v.strip()


# ── the Telegram bot (kaskad.bot.onboard) ──────────────────────────────────

@route("GET", r"/api/bot", runs=True)                 # systemctl is-active of the bot
def bot_get(req):
    return ok(**onboard.status(runner()))


@route("POST", r"/api/bot/check")
def bot_check(req):
    try:
        res = onboard.check_token(_token(req.json()))
    except KaskadError as e:
        raise _bot_error(e)
    return ok(username=res["username"])


@route("POST", r"/api/bot/test")
def bot_test(req, journal="journal.panel.bot"):
    b = req.json()
    token = _token(b)
    chat = b.get("chat_id")
    if isinstance(chat, bool) or not isinstance(chat, (str, int)):
        chat = ""
    try:
        res = onboard.apply(runner(), token, str(chat).strip())
    except KaskadError as e:
        raise _bot_error(e)
    finally:
        token = None
    if journal == "journal.panel.bot":
        util.log_tr(journal, user=res["username"] or "?", ip=req.client_ip)
    else:
        util.log_tr(journal, user=res["username"] or "?")
    _changed()
    return ok(**dict((k, res[k]) for k in ("username", "chat_id", "started", "sent", "sent_why")))


@route("POST", r"/api/bot/proxy")
def bot_proxy(req):
    from ..gw import policy_ops as pops
    b = req.json()
    gid = b.get("group")
    if not isinstance(gid, str) or len(gid) > 32:
        raise HttpError(400, tr("bot_proxy.bad_group"), data={"field": "group"})
    res = _pcall(pops.tg_proxy_set, runner(), gid, want_rev=_need_rev(req, b))
    util.log_action("Panel: bot proxy -> %s (%s)" % (gid, req.client_ip))
    res["path"] = onboard._path(onboard._cfg(), None)
    return _answer(res)


# ── the keys of the AI agent (kaskad.mcp.keys, kaskad.mcp.seen) ───────────

def _mcp():
    from ..mcp import keys as mkeys
    return mkeys


def _label(v):
    """A label people typed -> one LABEL_RE takes: Cyrillic transliterated, anything else not allowed -> «-», at
    most 32; nothing left -> None (a key without a label)."""
    if not isinstance(v, str):
        return None
    from ..gw.model import _TRANSLIT
    v = v.strip()
    if not v:
        return None
    mkeys = _mcp()
    if mkeys.LABEL_RE.match(v):
        return v
    out = []
    for ch in v:
        low = ch.lower()
        if low in _TRANSLIT:
            t = _TRANSLIT[low]
            out.append(t.capitalize() if ch != low else t)
        elif re.match(r"[A-Za-z0-9._@-]", ch):
            out.append(ch)
        else:
            out.append("-")
    v = re.sub(r"-{2,}", "-", "".join(out)).strip("-")[:32].strip("-")
    return v if v and mkeys.LABEL_RE.match(v) else None


@route("GET", r"/api/mcp")
def mcp_get(req):
    mkeys = _mcp()
    from ..mcp import seen
    try:
        doc = mkeys.listing()
    except KaskadError as e:
        raise core_error(e)
    keys = doc.get("keys") or []
    try:
        s = seen.for_keys([k["fingerprint"] for k in keys], runner())
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):  # «подключено» never breaks the card
        s = {}
    rows = [{"fingerprint": k["fingerprint"], "type": k["type"], "label": k.get("label") or "",
             "seen": s.get(k["fingerprint"])} for k in keys]
    connect = doc.get("connect") or mkeys.connect()
    return ok(keys=rows, connect=connect, host=connect.get("host"))


@route("POST", r"/api/mcp/keys")
def mcp_key_add(req):
    mkeys = _mcp()
    b = req.json()
    text = b.get("pubkey")
    if not isinstance(text, str) or not text.strip():
        raise HttpError(400, tr("panel_ux.mcp_paste"), data={"reason": "empty"})
    if len(text) > mkeys.KEY_MAX:
        raise HttpError(400, tr("mcp.err.too_long"), data={"reason": "bad_key"})
    if "PRIVATE KEY" in text:
        raise HttpError(400, tr("panel_ux.mcp_private"), data={"reason": "private_key"})
    try:
        r = mkeys.add(text, label=_label(b.get("label")))
    except KaskadError as e:
        err = core_error(e)
        data = e.data if isinstance(getattr(e, "data", None), dict) else {}
        err.data = {"reason": data.get("reason") or ("exists" if err.status == 409 else "bad_key")}
        raise err
    finally:
        text = None
    _changed()
    return ok(fingerprint=r["fingerprint"], type=r["type"], label=r.get("label") or "", connect=r["connect"])


@route("DELETE", r"/api/mcp/keys/(?P<fp>[A-Za-z0-9%:+/=._-]{8,200})")
def mcp_key_del(req):
    mkeys = _mcp()
    fp = urllib.parse.unquote(req.match.group("fp"))
    if not mkeys.FP_RE.match(fp):
        raise HttpError(400, tr("mcp.err.fp"), data={"reason": "bad_fp"})
    try:
        r = mkeys.delete(fp, runner())
    except KaskadError as e:
        err = core_error(e)
        if (e.data or {}).get("gone"):
            err.status, err.data = 404, {"reason": "gone"}
        raise err
    _changed()
    return ok(removed=r["removed"], fingerprint=fp, ended=r.get("ended", 0), unsure=bool(r.get("unsure")))


# ── the partner hosting (OWNER-FEEDBACK №45) ───────────────────────────────

@route("GET", r"/api/hosting")
def hosting(req):
    """Where to rent a server for an exit: kaskad.setup.product.HOSTING in words of the language — the one place of the
    addresses (the wizard reads them in its product object; GET /api/setup/product needs the wizard's session, the
    panel after the installation has none). Public partner addresses, no secret."""
    from ..setup import product
    return ok(hosting=product.hosting(), guide=product.LINKS.get("exit_guide"))


# ── a protocol: its journal and a restart (kaskad.gw.inlog) ────────────────

def _inbound_kind(req):
    from ..gw import registry
    kind = req.match.group("kind")
    if registry.get(kind) is None:
        raise HttpError(404, tr("panel_ux.no_kind", kind=kind), data={"reason": "no_kind"})
    return kind


@route("GET", r"/api/gw/inbounds/%s/log" % _KIND, runs=True)      # journalctl
def inbound_log(req):
    from ..gw import inlog
    kind = _inbound_kind(req)
    r = runner()
    log = inlog.journal(r, kind)
    st = _st()
    try:
        fail = inlog.fail_info(r, kind, inlog.row_now(r, st, kind), st, log=log)
    except (KaskadError, OSError, ValueError):
        fail = None
    return ok(kind=kind, lines=log["lines"], units=log["units"], cmd=log["cmd"], fail=fail)


@route("POST", r"/api/gw/inbounds/%s/restart" % _KIND)
def inbound_restart(req):
    from ..gw import inlog
    kind = _inbound_kind(req)
    try:
        res = inlog.restart(runner(), kind)
    except KaskadError as e:
        err = core_error(e)
        data = e.data if isinstance(getattr(e, "data", None), dict) else {}
        err.data = {"reason": data.get("reason") or "error"}
        raise err
    finally:
        _changed()
    from ..gw import registry
    title = getattr(registry.get(kind), "title", "") or kind
    with util.journal_lang():
        util.log_action(tr("journal.panel.inbound_restart", title=title, ip=req.client_ip,
                           state=tr("journal.panel.inbound_running" if res["running"] else "journal.panel.inbound_down")))
    return ok(kind=kind, running=res["running"], at=res["at"], fail=res["fail"])

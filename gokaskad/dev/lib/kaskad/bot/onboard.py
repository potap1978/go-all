"""Connecting the Telegram bot — one logic for the quick setup (``setup.steps.s_bot``) and the panel (``/api/bot``).

    check_token(token)               getMe through BOT_PROXY; nothing saved. {"ok", "username"} | KaskadError
    apply(runner, token, chat, test) check, save BOT_TOKEN/BOT_CHAT_ID, ``--bot-start``, a test message
    status(runner)                   what the panel shows: token, chat, running, last error, «нужен выход», in Russia,
                                     «path» (bot/proxy.path) and «choice» (the groups for «Как бот ходит в Telegram»)

Errors are KaskadError with ``data["reason"]``: bad_format, bad_token (Telegram 401/404), net (no answer from
Telegram), telegram (anything else Telegram said), bad_chat. The token never goes to a text, a journal or an answer:
``Api.clean`` cuts it out of Telegram's words, the callers log the bot's name only.
"""

import os
import re
import socket
import threading
import time

from .. import paths, util
from ..errors import KaskadError
from ..i18n import tr

TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")
CHAT_RE = re.compile(r"^-?\d{1,20}$")
UNIT = "kaskad-bot.service"
TG_HOST = ("api.telegram.org", 443)
REACH_TTL = 600.0              # s: the direct reach of api.telegram.org is remembered this long (per process)
REACH_TIMEOUT = 4.0

_now = time.time
_reach = {"at": 0.0, "value": None}


def _fail(reason, text, error=None):
    """``error`` — Telegram's own words without the token (the wizard says them its way); never an answer field."""
    data = {"reason": reason}
    if error is not None:
        data["error"] = error
    return KaskadError(text, data=data)


def _cfg():
    from . import config as bcfg
    return bcfg.load()


def _proxy(cfg):
    from ..net import socks
    try:
        return socks.parse_proxy(cfg.proxy)
    except ValueError:
        return None


def _api(token, cfg):
    from . import telegram                       # the module attribute at call time: tests replace telegram.Api
    return telegram.Api(token, proxy=_proxy(cfg), base=os.environ.get("KASKAD_TG_API") or None)


def connect(token, cfg=None):
    """(api, me) of a token Telegram accepted; KaskadError bad_format / bad_token / net / telegram otherwise."""
    from .telegram import ApiError, NetworkError
    token = (token or "").strip()
    if not TOKEN_RE.match(token):
        raise _fail("bad_format", tr("bot_onboard.bad_format"))
    cfg = cfg if cfg is not None else _cfg()
    proxied = bool((cfg.proxy or "").strip())
    tries = proxy_tries() if proxied else 1
    for n in range(tries):
        api = _api(token, cfg)                       # a new connection each time: bot-bal gives it the next key
        try:
            me = api.get_me() or {}
        except ApiError as e:
            if getattr(e, "code", None) in (401, 404):
                raise _fail("bad_token", tr("bot_onboard.bad_token"))
            why = api.clean(e)
            raise _fail("telegram", tr("bot_onboard.telegram", error=why), why)
        except NetworkError as e:
            why = api.clean(e)
            if not proxied:
                raise _fail("net", tr("bot_onboard.net_no_exit"), why)
            if n + 1 < tries:                        # SPEC-stage1-exits §2.9: one dead key of the group does not stop it
                continue
            # a proxy (an exit) is set but does not carry: not «add an exit», the server's words with the reason
            raise _fail("net_proxy", tr("bot_onboard.net", error=why), why)
        return api, (me if isinstance(me, dict) else {})


def tg_rebuild(runner):
    """The bot got its first token: its proxy now has its own probes when the gateway is off (SPEC-stage1-exits §2.9) —
    ``gokaskad --egress-tg-rebuild`` in the background (``systemd-run --no-block``: it restarts the bot and its xray, not
    under this request). Nothing when there is no pool or no systemd-run; a failure is only a line in the journal."""
    from ..gw import exits as gexits
    if not os.path.isfile(paths.egress_up()):
        return False
    try:
        r = runner.run(["systemd-run", "--no-block", "--collect", "--quiet", gexits._script(runner),
                        "--egress-tg-rebuild"], timeout=15)
        return bool(r.ok)
    except Exception as e:                                   # noqa: BLE001 — the bot works anyway, with 3.3.3's probes
        util.log_action("bot: --egress-tg-rebuild: %s" % type(e).__name__)
        return False


PROXY_TRIES_MAX = 5


def proxy_tries():
    """How many times ``connect`` tries through the bot's proxy (SPEC-stage1-exits §2.9): bot-bal without the gateway
    goes round the keys of its group — each new connection through the next key, so a dead key does not stop the setup:
    min(the keys of the group, 5) — the numbers install.sh wrote last (``.tg_indexes``), else the lines of the pool."""
    raw = util.read_text(os.path.join(paths.egress_dir(), ".tg_indexes"))
    if raw is not None:
        n = len(re.findall(r"\d+", raw))
    else:
        raw = util.read_text(paths.egress_up())
        n = len([ln for ln in (raw or "").splitlines() if ln.strip()])
    return max(1, min(n, PROXY_TRIES_MAX))


def check_token(token):
    """getMe of the token (through BOT_PROXY as the bot itself goes); nothing is saved."""
    _, me = connect(token)
    return {"ok": True, "username": me.get("username") or ""}


def detect_chat(api):
    """The chat of the last message the bot got (the owner pressed /start), or None. The quick setup's «auto»."""
    from .telegram import ApiError, NetworkError
    try:
        ups = api.get_updates(-1, timeout=0, limit=1) or []
    except (ApiError, NetworkError):
        ups = []
    msg = (ups[-1].get("message") if ups and isinstance(ups[-1], dict) else None) or {}
    return str((msg.get("chat") or {}).get("id") or "") or None


def save_token(token):
    from . import config as bcfg
    bcfg.save_value("BOT_TOKEN", token)


def _start(runner):
    """``install.sh --bot-start`` with K3_MARKS=1: @K3:bot_started | @K3:bot_running | @K3:bot_failed:<why>
    (never translated); [OK] — the older script. A running bot re-reads the config."""
    from ..gw import exits as gexits
    r = runner.run([gexits._script(runner), "--bot-start"], timeout=120, env={"K3_MARKS": "1"})
    out = r.out or ""
    return bool(r.ok and ("@K3:bot_running" in out or "@K3:bot_started" in out or "[OK]" in out)
                and "@K3:bot_failed" not in out)


def _test_message(api, chat, text):
    """(sent, why): why None | "nostart" (the owner never pressed /start: 403, or 400 «chat not found») | "net" |
    "busy" (Telegram answered 429 or 5xx: try again later, the ID is not the cause) | "telegram"."""
    from .telegram import ApiError, NetworkError
    try:
        api.send_message(chat, text)
        return True, None
    except ApiError as e:
        desc = (getattr(e, "description", "") or "").lower()
        if e.code == 403 or (e.code == 400 and "chat not found" in desc):
            return False, "nostart"
        if e.code == 429 or (isinstance(e.code, int) and e.code >= 500):
            return False, "busy"
        return False, "telegram"
    except NetworkError:
        return False, "net"


def finish(runner, api, me, token, chat, test=True, message=None):
    """Save the token and the chat, start the bot, send the test message (``message`` — its words; the panel's
    by default). ``api``/``me`` — of ``connect``."""
    from . import config as bcfg
    had = bool((_cfg().token or "").strip())
    bcfg.save_value("BOT_TOKEN", token)
    bcfg.save_value("BOT_CHAT_ID", chat)
    started = _start(runner)
    sent, why = _test_message(api, chat, message or tr("bot_onboard.test_message")) if test else (False, None)
    _reach.update(at=0.0, value=None)                        # the next status looks again
    if not had:
        tg_rebuild(runner)
    return {"ok": True, "username": me.get("username") or "", "chat_id": chat, "started": started, "sent": sent,
            "sent_why": why}


def apply(runner, token, chat, test=True):
    """The whole connection from the panel: ``token`` empty — the saved one; ``chat`` — the owner's chat id."""
    chat = str(chat or "").strip()
    if not CHAT_RE.match(chat):
        raise _fail("bad_chat", tr("bot_onboard.bad_chat"))
    cfg = _cfg()
    token = (token or "").strip() or cfg.token
    if not token:
        raise _fail("no_token", tr("bot_onboard.no_token"))
    api, me = connect(token, cfg)
    return finish(runner, api, me, token, chat, test=test)


def _probe(host, port, timeout):
    s = socket.create_connection((host, port), timeout=timeout)
    s.close()


def _spawn(fn):
    threading.Thread(target=fn, name="kaskad-bot-reach", daemon=True).start()


def _probe_now():
    try:
        _probe(TG_HOST[0], TG_HOST[1], REACH_TIMEOUT)
        ok = True
    except (OSError, ValueError):
        ok = False
    _reach.update(at=_now(), value=ok, busy=False)
    return ok


def reach(force=False):
    """Is api.telegram.org reachable straight from here (TCP 443 within 4 s); remembered REACH_TTL s. Only the first
    answer of the process waits for the probe: an old one is given at once and renewed in the background (the
    settings must not hang 4 s after an idle hour)."""
    now = _now()
    if not force and _reach["value"] is not None:
        if now - _reach["at"] >= REACH_TTL and not _reach.get("busy"):
            _reach["busy"] = True
            _spawn(_probe_now)
        return _reach["value"]
    return _probe_now()


def in_ru():
    """Is the server in Russia: True | False | None (not known). Only what is known without the network — the country
    the VLESS record keeps, else the address of the node in the Russian ranges of the rules (the panel must not wait).
    The banner «из РФ блокируется» needs True; otherwise it says neutrally that Telegram is unreachable from here."""
    try:
        from .. import state as kstate
        from ..gw.inbounds import vless, vless_lists
        st = kstate.load()
        code = (vless.country_of(st, detect=False) or {}).get("code") or ""
        if not code:
            code = vless_lists._local(vless_lists._public_ipv4(st))
    except Exception:                                   # noqa: BLE001 — a hint, never a broken status
        return None
    return (code == "RU") if code else None


def status(runner):
    from . import store
    cfg = _cfg()
    daemon = store.read_json(paths.P("/run/kaskad/bot.json"), None)
    daemon = daemon if isinstance(daemon, dict) else {}
    running = False
    if runner.which("systemctl"):
        running = runner.run(["systemctl", "is-active", UNIT], timeout=10).out.strip() == "active"
    proxy = bool((cfg.proxy or "").strip())
    rch = None if proxy else reach()
    return {"token": bool(cfg.token), "chat": bool(cfg.chat_id), "chat_id": cfg.chat_id or "",
            "username": str(daemon.get("username") or ""), "running": running,
            "last_error": util.redact(str(daemon.get("last_error") or ""))[:400], "proxy": proxy, "reach": rch,
            "need_exit": (not proxy) and rch is False, "ru": in_ru() if rch is False else None,
            "path": _path(cfg, daemon), "choice": _choices()}


def _path(cfg, daemon):
    """«Как бот ходит в Telegram» now (bot/proxy.py, SPEC-tg-proxy Р4) — None when it cannot be told."""
    from . import proxy as bproxy
    try:
        return bproxy.path(cfg=cfg, daemon=daemon)
    except Exception:                                   # noqa: BLE001 — a status, never a failure of /api/bot
        return None


def _choices():
    """The panel's choice (gw.tgproxy.choices) and the version of the exit policy for ``POST /api/bot/proxy``."""
    try:
        from ..gw import policy_ops, tgproxy
        out = tgproxy.choices()
        out["rev"] = policy_ops.current_rev()
        return out
    except Exception:                                   # noqa: BLE001 — without the gateway's core: no choice shown
        return None

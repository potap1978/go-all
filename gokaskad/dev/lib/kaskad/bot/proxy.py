"""How the bot goes to Telegram now (SPEC-tg-proxy Р4): one answer for the panel, the SSH menu, the CLI and MCP.

    path(cfg=None, daemon=None, now=None, st=None) -> {
        "state": "group" | "fallback" | "direct" | "none" | "off",
        "why":   "chosen" | "pending" (chosen, the bot not switched yet) | "not_built" (the group has keys, the proxy is
                 not built: a rebuild pending or failed) | "no_keys" | "no_pool" | "" (only for «direct»),
        "via":   "group" | "direct" — the path the bot takes now, "proxy": BOT_PROXY is set (never its value),
        "choice": the stored choice — a group id or "direct",
        "group": the id of the group whose exits carry the bot (its lines: gw.tgproxy.indexes), "label": its name,
        "text":  the words for people (the language of the server)}

«group» — BOT_PROXY is set and the daemon goes through it; «fallback» — the exits of the group do not answer and the
daemon went straight (bot/app.py, Р3); «direct» — no proxy: chosen «Напрямую», or the group (and «Основная») has no
keys, or there is no pool; «none» — the daemon runs, and its last error is newer than its last good poll by more than
NONE_AFTER s; «off» — no token. Neither keys nor addresses of exits nor the token are in it: the name of the group only.
"""

import os
import time

from .. import paths, util
from ..i18n import tr

NONE_AFTER = 120.0          # s without a good poll while the daemon reports an error — «нет связи с Telegram»
DAEMON_STALE = 180.0        # s: an older /run/kaskad/bot.json says nothing of now (the daemon writes every 30 s)


def _daemon():
    from . import store
    doc = store.read_json(paths.P("/run/kaskad/bot.json"), None)
    return doc if isinstance(doc, dict) else {}


def _group(st):
    """(choice, gid, label, keys) — the effective choice (``policy.load``: a group gone is «main») and the group whose lines
    the proxy takes (an empty chosen group — «main», D-042); («main», «main», "") when the core of the gateway fails
    (a broken state must not break the status)."""
    try:
        from .. import state
        from ..gw import policy, tgproxy
        st = state.load() if st is None else st
        res = tgproxy.indexes(st)
        if res.get("direct"):
            return policy.TG_DIRECT, "", "", 0
        choice = policy.load(st).get("tg_proxy_group") or policy.MAIN
        return choice, res.get("group") or policy.MAIN, str(res.get("label") or ""), len(res.get("indexes") or [])
    except Exception:                                   # noqa: BLE001 — a status line, never a failure
        return "main", "main", "", 0


def _pool():
    return bool((util.read_text(paths.egress_up()) or "").strip())


def path(cfg=None, daemon=None, now=None, st=None):
    if cfg is None:
        from . import config as bcfg
        cfg = bcfg.load()
    daemon = _daemon() if daemon is None else daemon
    now = time.time() if now is None else now
    choice, gid, label, keys = _group(st)
    proxied = bool((cfg.proxy or "").strip())
    out = {"choice": choice, "group": gid, "label": label, "why": "", "via": "group" if proxied else "direct",
           "proxy": proxied}
    if not cfg.token:
        out["state"] = "off"
    elif not proxied:
        out["state"] = "direct"
        out["why"] = "chosen" if choice == "direct" else "not_built" if keys else "no_keys" if _pool() else "no_pool"
    elif choice == "direct":                            # chosen, the rebuild of install.sh not done yet (≤ 1 min)
        out.update(state="direct", why="pending", via="group")
    elif daemon.get("via") == "fallback" and daemon.get("proxy") and daemon.get("running") \
            and isinstance(daemon.get("written"), (int, float)) and now - daemon["written"] <= DAEMON_STALE:   # REV-3310 П2
        out.update(state="fallback", via="direct")
    else:
        out["state"] = "group"
    fresh = isinstance(daemon.get("written"), (int, float)) and now - daemon["written"] <= DAEMON_STALE
    last = daemon.get("last_poll") if isinstance(daemon.get("last_poll"), (int, float)) else None
    if out["state"] != "off" and fresh and daemon.get("running") and daemon.get("last_error") \
            and (last is None or now - last > NONE_AFTER):
        out["state"] = "none"
    out["text"] = text(out)
    return out


def text(p):
    st = p.get("state")
    if st == "group":
        return tr("bot_proxy.group", group=p.get("label") or p.get("group") or "")
    if st == "fallback":
        return tr("bot_proxy.fallback", group=p.get("label") or p.get("group") or "")
    if st == "direct":
        why = p.get("why")
        if why == "chosen":
            return tr("bot_proxy.direct_chosen")
        if why == "pending":
            return tr("bot_proxy.direct_pending")
        if why == "not_built":
            return tr("bot_proxy.direct_not_built", group=p.get("label") or p.get("group") or "")
        if why == "no_keys":
            return tr("bot_proxy.direct_no_keys", group=p.get("label") or p.get("group") or "")
        return tr("bot_proxy.direct_no_pool")
    if st == "none":
        return tr("bot_proxy.none_group" if p.get("proxy") else "bot_proxy.none_direct",
                  group=p.get("label") or p.get("group") or "")
    return tr("bot_proxy.off")

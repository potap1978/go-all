"""«Прокси для API Telegram»: the keys of the chosen group (SPEC-stage1-exits §2.9, D-028, D-029, D-042, D-133).

The bot's proxy (xray ``kaskad-egress``, its own balancer ``bot-bal``) takes the lines of ``upstream.txt`` whose exits
are in the group ``exit_policy.tg_proxy_group`` (a foreign group, «main» by default; ``policy.load`` puts «main» in place
of a group that is gone or not foreign) — enabled ones, in any state (the failover is ``bot-bal``'s). No policy (the
gateway was never set up) — every line, as 3.3.3. The group has no such line — the lines of «main» (D-042); none there
either — none: ``egress_rebuild`` refuses as without keys. The Russian file (``upstream-ru.txt``) never goes there.

    indexes(st=None)   -> {"indexes": [N, …] (1-based numbers of the non-blank lines of upstream.txt, the numbers of the
                          bot's out_N, the leg kawgN and the mark 256+N), "group": gid, "label": the name of the group
                          for people (policy.label: «Основная»/«Main» of «main» with no name), "total": M, "policy":
                          False when there is none} — «group» is the group whose lines these are (the chosen one,
                          or «main» when the chosen one has none)

``kaskadctl gw tg-proxy-indexes [--label]`` prints them for install.sh (``egress_rebuild``, the header of the menu).

«Напрямую» (SPEC-tg-proxy Р1): ``tg_proxy_group`` = ``policy.TG_DIRECT`` — no lines, ``"direct": True`` (the CLI
prints «direct»: install.sh takes the bot's xray off, BOT_PROXY empty). Only with a stored policy — none stored is
3.3.3 (every line).

    choices(st=None, lang=None) -> {"group": the stored choice (gid | "direct"), "groups": [{"id", "label",
                          "keys": the enabled lines in it}] — the foreign groups, «main» first} (the panel's choice)
"""

from .. import state
from . import exits, model, policy


def _subs():
    from . import exitsubs
    try:
        return exitsubs.load()
    except Exception:                                   # noqa: BLE001 — the hint of a subscription is only a hint
        return None


def indexes(st=None):
    st = state.load() if st is None else st
    ids = exits.line_ids()                              # [(exit id, N)] — the ids of pool() without the parser
    total = len(ids)
    if policy.stored(st) is None:
        doc = policy.load(st)
        main = policy.group(doc, policy.MAIN)
        return {"indexes": [n for _, n in ids], "group": policy.MAIN, "label": policy.label(main), "total": total,
                "policy": False}
    doc = policy.load(st, pool=[eid for eid, _ in ids], subs=_subs())
    gid = doc.get("tg_proxy_group") or policy.MAIN
    if gid == policy.TG_DIRECT:
        return {"indexes": [], "group": policy.TG_DIRECT, "label": "", "total": total, "policy": True, "direct": True}

    def of(g):
        members = set(policy.exits_of(policy.group(doc, g)))
        return [n for eid, n in ids if eid in members and model.exit_pref(st, eid)["enabled"]]

    got = of(gid)
    if not got and gid != policy.MAIN:                  # the group has no keys: «main» (D-042) — and its name is said
        gid, got = policy.MAIN, of(policy.MAIN)
    return {"indexes": got, "group": gid, "label": policy.label(policy.group(doc, gid)), "total": total,
            "policy": True}


def choices(st=None, lang=None):
    """What the panel offers (SPEC-tg-proxy Р5): the stored choice and the foreign groups with their enabled lines."""
    st = state.load() if st is None else st
    ids = exits.line_ids()
    doc = policy.load(st, pool=[eid for eid, _ in ids], subs=_subs())
    enabled = set(eid for eid, _ in ids if model.exit_pref(st, eid)["enabled"])
    out = []
    for g in doc.get("groups") or []:
        if not isinstance(g, dict) or g.get("role", "abroad") != "abroad":
            continue
        out.append({"id": g.get("id"), "label": policy.label(g, lang),
                    "keys": len([e for e in policy.exits_of(g) if e in enabled])})
    out.sort(key=lambda x: x["id"] != policy.MAIN)
    return {"group": doc.get("tg_proxy_group") or policy.MAIN, "groups": out}

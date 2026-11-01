"""«Установите приложение» of an issuance: which apps take this protocol on this system, with the store links.

The links live in catalogs, not in the code: ``gw/install_apps.json`` (WireGuard, VLESS, OpenVPN, OpenConnect…, the
built-in clients of the systems) and, for AmneziaWG, ``vendor/clients.json`` (the goAmnezia pro catalog: its apps with
their support of the lines 1.0/1.5/2.0/3.1 — an app of ``install_apps.json`` with ``from_vendor`` takes its links there).

    block(kind, platform) -> {"supported", "builtin", "apps": [{"id", "name", "recommended", "note",
                              "links": [{"kind", "label", "url", "note"}]}], "text"}

``supported`` False — no app for this protocol on this system: ``text`` says so honestly («VLESS на Keenetic — нет
приложения: выберите другой протокол или систему»), the protocol is not swapped for another (owner, 3.3.2: the choice
of the protocol jumped to another one when the system changed). ``builtin`` — the system's own client (the words of
the catalog). The first app is the recommended one (the same as the steps of ``issue_os.howto`` name). Only https
links go out. The language of the notes — the language of the call.
"""

import json
import os
import re

from kaskad.i18n import current_lang, tr
from kaskad.vendor import clients

from . import issue_os

CATALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "install_apps.json")
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "panel", "static")
_ICON = re.compile(r"^apps/[a-z0-9-]{1,40}\.png$")
KINDS = ("appstore", "googleplay", "github", "site", "microsoft", "izzy")
_cache = {}


def catalog():
    """The catalog (read once per process); a broken or absent file — empty: the block says «no app», never fails."""
    if "doc" not in _cache:
        try:
            with open(CATALOG, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            doc = {}
        _cache["doc"] = doc if isinstance(doc, dict) else {}
    return _cache["doc"]


def _loc(d):
    if isinstance(d, str):
        return d
    if not isinstance(d, dict):
        return ""
    lang = current_lang() or "ru"
    return str(d.get(lang) or d.get("en") or d.get("ru") or "")


def _https(url):
    url = str(url or "")
    return url if url.startswith("https://") and not any(c in url for c in " \"'<>") else ""


def _link(raw):
    if not isinstance(raw, dict):
        return None
    kind, url = str(raw.get("kind") or ""), _https(raw.get("url"))
    if not url:
        return None
    return {"kind": kind, "label": tr("gw_issue.store." + kind) if kind in KINDS else kind, "url": url,
            "note": _loc(raw.get("note"))}


def _vendor_links(app_id, p):
    cat_platform = (issue_os._CATALOG.get(p) or (None,))[0]
    for app in clients().get("apps") or []:
        if app.get("id") == app_id:
            info = (app.get("platforms") or {}).get(cat_platform) or {}
            return [x for x in (_link(r) for r in info.get("links") or []) if x]
    return []


def icon_of(rel):
    """The path of an app's icon under panel/static («apps/karing.png») when the file is there, else "": the icons
    ship with the product — the browser of the reader fetches nothing from the stores."""
    rel = str(rel or "")
    if not _ICON.match(rel):
        return ""
    return rel if os.path.isfile(os.path.join(STATIC, rel)) else ""


def _app(app_id, p, kind=None):
    a = (catalog().get("apps") or {}).get(app_id)
    if not isinstance(a, dict):
        return None
    links = [x for x in (_link(r) for r in (a.get("links") or {}).get(p) or []) if x]
    if a.get("from_vendor"):
        # links of vendor/clients.json (the copy of goAmnezia pro, not edited here) first, then our own for the platforms
        # the vendor does not know (UI-1 F09: incy on Windows and Linux — the developer's GitHub)
        vendor = _vendor_links(a["from_vendor"], p)
        links = vendor + [x for x in links if x["url"] not in set(v["url"] for v in vendor)]
    if not links:
        return None
    # «не проверено на устройстве»: список платформ (с unverified_kinds — только для этих протоколов) или пары
    # {платформа: [протокол, …] | "*"} — когда на разных платформах не проверены разные протоколы (UI-1 круг 2)
    marks = a.get("unverified") or ()
    if isinstance(marks, dict):
        ks = marks.get(p)
        unverified = ks == "*" or (isinstance(ks, list) and kind in ks)
    else:
        kinds = a.get("unverified_kinds")
        unverified = p in marks and (not kinds or kind in kinds)
    return {"id": app_id, "name": _loc(a.get("name")) or app_id, "recommended": False,
            "note": _loc((a.get("note") or {}).get(p)), "links": links, "icon": icon_of(a.get("icon")),
            # в магазине есть подделки с тем же названием (ios-apps-ru-2026-10 §5): ставить только по ссылке
            "fakes": tr("gw_issue.install.fakes") if p in (a.get("fakes") or ()) else "",
            "unverified": tr("gw_issue.support.unverified") if unverified else ""}


def platform_title(p):
    from kaskad.setup import product
    return product.platform_title(p)


def none_text(kind, p):
    return tr("gw_issue.install.none", proto=issue_os.TITLES.get(kind, kind), os=platform_title(p))


def block(kind, platform_name):
    p = issue_os.platform(platform_name)
    entry = ((catalog().get("protocols") or {}).get(kind) or {}).get(p)
    if entry == "builtin":
        note = ((catalog().get("builtin_note") or {}).get(kind) or {}).get(p)
        return {"supported": True, "builtin": _loc(note) or tr("gw_issue.install.builtin"), "apps": [], "text": ""}
    apps = [a for a in (_app(x, p, kind) for x in (entry if isinstance(entry, list) else [])) if a]
    if not apps:
        return {"supported": False, "builtin": "", "apps": [], "text": none_text(kind, p)}
    apps[0]["recommended"] = True
    return {"supported": True, "builtin": "", "apps": apps, "text": ""}


def sub_block(platform_name):
    """«Установите приложение» of the subscription (the tab «Подписка» of «Выдать настройки»): the apps of
    ``subscription[platform]`` of the catalog — the same rows as ``block`` (links, icon, note, fakes; «не проверено» as
    for VLESS: the list of the subscription is ``protocols.vless`` of the platform), the first one recommended.
    ``steps`` of an app — its own steps of adding a subscription (``sub_steps[platform]`` of the catalog app, both
    languages), only when the catalog has them: the panel says the neutral steps otherwise. No app — ``supported``
    False, the panel does not offer the platform."""
    p = issue_os.platform(platform_name)
    ids = (catalog().get("subscription") or {}).get(p)
    apps = [a for a in (_app(x, p, "vless") for x in (ids if isinstance(ids, list) else [])) if a]
    if not apps:
        return {"supported": False, "builtin": "", "apps": [], "text": ""}
    for a in apps:
        raw = (catalog().get("apps") or {}).get(a["id"]) or {}
        steps = [s for s in (_loc(x) for x in ((raw.get("sub_steps") or {}).get(p) or [])) if s]
        if steps:
            a["steps"] = steps
    apps[0]["recommended"] = True
    return {"supported": True, "builtin": "", "apps": apps, "text": ""}


def sub_blocks():
    """{platform: sub_block} of the platforms of ``subscription`` that have an app, in the order of the platforms
    (``userpage.admin_sub_doc`` → the field ``apps``)."""
    subs = catalog().get("subscription") or {}
    out = {}
    for p in issue_os.PLATFORMS:
        if p in subs:
            b = sub_block(p)
            if b["supported"]:
                out[p] = b
    return out


# ── the support table «платформа × протокол» (SPEC-issue-fallbacks.md, owner 2026-10-11) ──────────────────────────
LEVELS = ("native", "package", "app", "workaround", "none")
READY, DEVICE_OFF, SERVER_OFF = "ready", "device_off", "server_off"


def _rows(p):
    rows = (catalog().get("support") or {}).get(p)
    return [r for r in rows if isinstance(r, dict) and r.get("proto") in issue_os.TITLES] if isinstance(rows, list) else []


def order(platform_name):
    """The protocols of the platform in the order of recommendation (the catalog sets it)."""
    return [r["proto"] for r in _rows(issue_os.platform(platform_name))]


def entry(kind, platform_name):
    """The raw row of the table; a pair the table lacks — «none» (no way known), never an error."""
    for r in _rows(issue_os.platform(platform_name)):
        if r["proto"] == kind:
            return r if r.get("level") in LEVELS else dict(r, level="none")
    return {"proto": kind, "level": "none"}


def levels(platform_name):
    """{kind: level} of every protocol on the platform — the window dims the «none» chips."""
    p = issue_os.platform(platform_name)
    return dict((k, entry(k, p)["level"]) for k in issue_os.TITLES)


def _guide(raw):
    return _https(_loc(raw))


def _alt_text(state, title, where):
    if state == READY:
        return tr("gw_issue.support.alt_ready", proto=title)
    if state == DEVICE_OFF:
        return tr("gw_issue.support.alt_device_page" if where == "page" else "gw_issue.support.alt_device", proto=title)
    key = {"page": "alt_server_page", "menu": "alt_server_menu", "bot": "alt_server_bot"}.get(where, "alt_server")
    return tr("gw_issue.support." + key, proto=title)


def alternatives(kind, platform_name, server=(), device=(), known=None, where="panel"):
    """The other protocols the platform takes, best first, with their state on this server and device:
    ``ready`` — on the server and granted to the device (the window switches to it), ``device_off`` — on the server,
    not granted («Добавить устройству» in the panel), ``server_off`` — not on the server («Включить на сервере» in the
    panel, «Попросите администратора…» on the page). ``known`` — the kinds this version can install (None — all)."""
    p = issue_os.platform(platform_name)
    out = []
    for r in _rows(p):
        k = r["proto"]
        if k == kind or r.get("level") not in LEVELS or r["level"] == "none" or (known is not None and k not in known):
            continue
        state = READY if (k in server and k in device) else DEVICE_OFF if k in server else SERVER_OFF
        title = issue_os.TITLES.get(k, k)
        # the note of the pair (owner 3.3.2, design: a triangle by «L2TP» — «Без шифрования…») for the window
        out.append({"proto": k, "title": title, "level": r["level"], "state": state, "text": _alt_text(state, title, where),
                    "note": _loc(r.get("note"))})
    return out


def support(kind, platform_name, alts=None, where="panel"):
    """``support`` of an issuance: {level, min, guide, note, links, unverified, levels, text, hints} in the language
    of the call. ``text`` — the head line for ``workaround`` («{os} не умеет {proto} штатно…») and ``none`` («{os} не
    поддерживает {proto}. Подойдёт:»); ``hints`` — every line a plain screen prints (SSH, bot, CLI): the head line,
    the condition, the note, the guide, then the alternatives (``alts`` — of ``alternatives()``)."""
    p = issue_os.platform(platform_name)
    e = entry(kind, p)
    level = e["level"]
    proto, os_title = issue_os.TITLES.get(kind, kind), platform_title(p)
    guide = _guide(e.get("guide"))
    links = [x for x in ({"label": _loc(l.get("label")), "url": _https(l.get("url"))} for l in e.get("links") or []
                         if isinstance(l, dict)) if x["url"]]
    out = {"level": level, "min": _loc(e.get("min")), "guide": guide, "note": _loc(e.get("note")), "links": links,
           "unverified": tr("gw_issue.support.unverified") if e.get("unverified") else "", "levels": levels(p),
           "text": "", "hints": []}
    alts = list(alts or [])
    if level == "workaround":
        out["text"] = tr("gw_issue.support.workaround", os=os_title, proto=proto)
    elif level == "none":
        out["text"] = tr("gw_issue.support.none", os=os_title, proto=proto) if alts else \
            tr("gw_issue.support.nothing", os=os_title)
    hints = [out["text"]] if out["text"] else []
    if level == "none":                                  # «Подойдёт:» — сразу протоколы, потом пометка
        hints.extend("• " + a["text"] for a in alts)
    if out["min"]:
        hints.append(tr("gw_issue.support.min", min=out["min"]))
    if out["note"]:
        hints.append(out["note"])
    if out["unverified"]:
        hints.append(out["unverified"])
    if guide:
        hints.append(tr("gw_issue.support.guide", url=guide))
    if level == "workaround" and alts:
        hints.append(tr("gw_issue.support.also", list=", ".join(a["title"] for a in alts[:4])))
    out["hints"] = hints
    return out

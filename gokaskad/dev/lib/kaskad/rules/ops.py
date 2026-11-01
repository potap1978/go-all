"""Operations of the rules (docs/k3/SPEC-wave3.md §2) — the code ``kaskadctl rules``, the timer
(kaskad-gw-rules.service) and the panel share:

    update(runner, force)        download (no state lock) -> decode and check -> store (atomic, last good kept) ->
                                 apply to the live gateway under the state lock: table (gw_rd), the networks of
                                 Russia (gw_ru, a transaction of its own), xray (files for SOCKS5), dnsmasq
    load(runner, force)          the networks of Russia into gw_ru when the table lacks the stored lists
                                 (kaskad-gw-rules-load.timer; a pause after a failure)
    set_default(runner, p, rf, ver)       gateway.rules.default: ru_direct | all_exit (``ver`` — the version of the
                                 form «Правила», checked under the lock: 409 ``object_changed``)
    set_profile(runner, target, ref, p, rf)   rules of a user or a device: inherit | all_exit | ru_direct
    set_ru_via(runner, target, ref, via) / set_ru_via_many(runner, items)   «Российские сайты» (T15): inherit |
                                 direct | <gid> of a Russian group; no consent ⚠РФ asked or given; an item's
                                 ``ver`` (``ru_ver``, REV-3310 П1) — checked under the lock: 409 ``object_changed``
    consent(runner, rf)          the tick ⚠РФ alone («Подтвердить»: modes legacy, need, uncovered)
    set_blocks(runner, ads, torrent)      the switches «Реклама», «Торренты»
    status(st, runner)           what the lists are, what applies, the last update, ⚠РФ ``rf`` (read-only)

A new «Россия напрямую» needs the tick «скрипт не используется на территории РФ» (``rf`` = {"confirm", "via", "who",
"lang", "orgs"}): the check is ``rules.rfgate.check`` inside ``gw/ops._change`` — every path of a write goes there
(docs/k3/SPEC-stage1-rf-mark-builtin.md §3.5).

Changes of the model go through ``kaskad.gw.ops._change`` (load -> change a copy -> apply when the gateway is on
-> save): the same path as every ``kaskadctl gw`` change.

What an update refuses (the stored lists stay, ``status.json`` says why): a file whose sha256 differs from the
published one, that does not decode, a geoip with fewer than MIN_DIRECT networks of ``direct``, a geosite without
the categories of the rules, a geoip with less than half of the networks of the stored one (``--force`` takes
it). One source failing is a warning when the other part could be had or the stored lists stay.
"""

import json
import os
import re
import time

from .. import nft, secrets, state, sysrun, util
from ..errors import CONFLICT, USAGE, KaskadError
from ..gw import model
from ..i18n import fmt_datetime, tr
from . import attribution, dat, lists, rfgate, source, store
from . import apply as rapply

APPLY_LOCK_TIMEOUT = 120.0           # s: the timer's update waits for a long ``gw apply`` of the menu
MIN_DIRECT = 1000                    # networks of geoip ``direct`` below this — a broken release
MIN_TEXT = 1000
MIN_CATEGORIES = 5                   # categories of the rules a geosite must have
DROP_LIMIT = 0.5                     # a geoip with fewer networks than half of the stored ones is refused


def _runner(runner):
    return runner or sysrun.default_runner()


# ── checks of the downloaded data (raise -> the source is refused) ─────────

def _check_geoip(data):
    g = dat.geoip(data, [lists.GEOIP_DIRECT])
    n = len((g.get(lists.GEOIP_DIRECT.upper()) or {}).get("v4") or [])
    if n < MIN_DIRECT:
        raise util.err(ValueError, "rules.check.direct", n=n, min=MIN_DIRECT)


def _check_geosite(data):
    codes = set(c.lower() for c in dat.codes(data))
    known = [c for c in lists.CATEGORIES if c in codes]
    if len(known) < MIN_CATEGORIES:
        raise util.err(ValueError, "rules.check.categories", n=len(known), min=MIN_CATEGORIES)


def _check_text(data):
    nets, _ = lists.text_cidrs(data.decode("utf-8", "replace"))
    if len(nets) < MIN_TEXT:
        raise util.err(ValueError, "rules.check.text", n=len(nets), min=MIN_TEXT)


# ── building a version ─────────────────────────────────────────────────────

def _ranges_bytes(ranges):
    return ("".join(line + "\n" for line in lists.ranges_text(ranges))).encode("ascii")


def _geoip_part(got):
    data = got["data"]
    g = dat.geoip(data)
    direct = g.get(lists.GEOIP_DIRECT.upper()) or {}
    ranges, wide = lists.merge(direct.get("v4") or [])
    meta = {"source": got["source"], "url": got.get("url"), "tag": got.get("tag"), "date": got.get("date"),
            "sha256": got["sha256"], "size": len(data), "codes": sorted(g), "checked": util.now_iso(),
            "direct_v4": len(direct.get("v4") or []), "direct_v6": len(direct.get("v6") or []),
            "bad": direct.get("bad", 0)}
    kernel = {"from": "geoip", "ranges": len(ranges), "cidrs": meta["direct_v4"], "v6": meta["direct_v6"],
              "wide": wide}
    return meta, kernel, _ranges_bytes(ranges)


def _text_part(got):
    nets, counts = lists.text_cidrs(got["data"].decode("utf-8", "replace"))
    ranges, wide = lists.merge(nets)
    meta = {"source": got["source"], "url": got.get("url"), "license": got.get("license"), "date": got.get("date"),
            "sha256": got.get("sha256"), "size": got.get("size"), "v4": len(nets), "v6": counts["v6"],
            "bad": counts["bad"], "checked": util.now_iso()}
    kernel = {"from": "text", "ranges": len(ranges), "cidrs": len(nets), "v6": counts["v6"], "wide": wide}
    return meta, kernel, _ranges_bytes(ranges)


def _geosite_part(got):
    data = got["data"]
    g = dat.geosite(data)
    cats = dict((code.lower(), lists.category(entries)) for code, entries in g.items())
    meta = {"source": got["source"], "url": got.get("url"), "tag": got.get("tag"), "date": got.get("date"),
            "sha256": got["sha256"], "size": len(data), "codes": sorted(g), "checked": util.now_iso()}
    dmeta = {"categories": dict((c, dict((k, len(v) if k == "domains" else v) for k, v in rec.items()))
                                for c, rec in sorted(cats.items())),
             "keyword": sum(r["keyword"] for r in cats.values()), "regexp": sum(r["regexp"] for r in cats.values()),
             "invalid": sum(r["invalid"] for r in cats.values())}
    text = json.dumps(cats, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    return meta, dmeta, text.encode("ascii")


def when_utc(raw):
    """A moment for people: the common format of the language (fmt_datetime) in UTC, «UTC» said when the raw value
    carries its zone («2026-10-11T03:08:51Z» → «11.10, 03:08 UTC» / «Oct 11, 03:08 UTC»); a value it can't read — as
    is. Shared by the version label and «last update» of `rules status` (M6 332c №11)."""
    raw = "" if raw is None else str(raw)
    when = fmt_datetime(raw, utc=True)
    if when != raw and re.search(r"(?:[Zz]|[+-]\d\d:?\d\d)$", raw):
        when += " UTC"
    return when


def label(v):
    """«от 04.10, 09:42 UTC (geoip 202610040942)» / «of Oct 4, 09:42 UTC …» of a version, for people: the date in the
    common format of the language (fmt_datetime, T-M6-332b №7), a moment with its zone — in UTC and said so."""
    if v is None:
        return tr("rules.label.none")
    gi = v.meta.get("geoip") or v.meta.get("text") or {}
    when = when_utc(gi.get("date") or v.meta.get("created") or "")
    tag = gi.get("tag")
    return tr("rules.label.at", when=when or "?", tag=tr("rules.label.tag", tag=tag) if tag else "")


def _refuse_drop(cur, kernel):
    old = ((cur.meta.get("kernel") or {}).get("cidrs") or 0) if cur is not None else 0
    if old and (cur.meta.get("kernel") or {}).get("from") == "geoip" and kernel["cidrs"] < old * DROP_LIMIT:
        raise util.err(KaskadError, "rules.update.drop", n=kernel["cidrs"], old=old)


def _fetch_and_store(cur, force, http):
    http = http or source.Http()
    warnings = []                       # kept messages (util.msg): said in the answer, kept as codes in status.json
    known_ip = cur.sha256(store.GEOIP) if cur is not None else None
    known_site = cur.sha256(store.GEOSITE) if cur is not None else None
    gi = gs = None
    gi_err = gs_err = None
    try:
        gi = source.fetch_dat("geoip", http, known_ip, force, check=_check_geoip)
    except source.SourceError as e:
        gi_err = e
        warnings.append(util.msg("rules.update.geoip_failed", error=e))
    try:
        gs = source.fetch_dat("geosite", http, known_site, force, check=_check_geosite)
    except source.SourceError as e:
        gs_err = e
        warnings.append(util.msg("rules.update.geosite_failed", error=e))
    text = None
    if gi is None and (cur is None or not rapply.ready(cur)):     # no networks at all: the last resort
        try:
            text = source.fetch_text(http, check=_check_text)
            warnings.append(util.msg("rules.update.text_used", source=text["source"]))
        except source.SourceError as e:
            warnings.append(util.msg("rules.update.text_failed", error=e))
    new_ip = gi is not None and not gi.get("unchanged")
    new_site = gs is not None and not gs.get("unchanged")
    if not new_ip and not new_site and text is None:
        if gi is None and gs is None:
            raise util.err(KaskadError, "rules.update.none", geoip=gi_err, geosite=gs_err)
        return {"ok": True, "changed": False, "warnings": warnings, "version": cur.id if cur is not None else None}
    files = {}
    meta = {"created": util.now_iso(), "attribution": attribution()}
    if new_ip:
        meta["geoip"], meta["kernel"], files[store.RANGES] = _geoip_part(gi)
        if not force:
            _refuse_drop(cur, meta["kernel"])
        files[store.GEOIP] = gi["data"]
    elif cur is not None and cur.file(store.GEOIP):
        files[store.GEOIP] = cur.read(store.GEOIP)
        files[store.RANGES] = cur.read(store.RANGES)
        meta["geoip"] = dict(cur.meta.get("geoip") or {}, checked=util.now_iso() if gi else
                             (cur.meta.get("geoip") or {}).get("checked"))
        meta["kernel"] = cur.meta.get("kernel")
    elif text is not None:
        meta["text"], meta["kernel"], files[store.RANGES] = _text_part(text)
    elif cur is not None and cur.file(store.RANGES):
        files[store.RANGES] = cur.read(store.RANGES)
        meta["text"] = cur.meta.get("text")
        meta["kernel"] = cur.meta.get("kernel")
    if new_site:
        meta["geosite"], meta["domains"], files[store.DOMAINS] = _geosite_part(gs)
        files[store.GEOSITE] = gs["data"]
    elif cur is not None and cur.file(store.GEOSITE):
        files[store.GEOSITE] = cur.read(store.GEOSITE)
        if cur.file(store.DOMAINS):
            files[store.DOMAINS] = cur.read(store.DOMAINS)
        meta["geosite"] = dict(cur.meta.get("geosite") or {}, checked=util.now_iso() if gs else
                               (cur.meta.get("geosite") or {}).get("checked"))
        meta["domains"] = cur.meta.get("domains")
    v = store.commit(files, meta)
    return {"ok": True, "changed": True, "warnings": warnings, "version": v.id, "geoip": bool(new_ip),
            "geosite": bool(new_site), "text": text is not None}


def last_status():
    """status.json of the last update for a reader: the error and the warnings said again in the language of the call
    from their kept codes (``error_msg``, ``warnings_msg``; ``error``/``warnings`` — the text of the moment of writing,
    for readers of before 3.3.2 and records without codes). None before the first update."""
    doc = store.load_status()
    if not doc:
        return None
    doc = dict(doc)
    m = doc.pop("error_msg", None)
    if m:
        doc["error"] = util.redact(util.said(m))
    w = doc.pop("warnings_msg", None)
    if isinstance(w, list):
        doc["warnings"] = [util.redact(util.said(x)) for x in w]
    return doc


def update(runner=None, force=False, http=None, apply_live=True, lock_timeout=APPLY_LOCK_TIMEOUT, log=None):
    """``kaskadctl rules update [--force]`` / the timer. Returns {"ok", "changed", "warnings", "version", "lists",
    "apply"?}. Raises KaskadError when nothing could be downloaded (the stored lists stay) and LockedError when an
    update is already running."""
    log = log or util.log_action
    with store.update_lock():
        cur = store.active()
        at = util.now_iso()
        try:
            res = _fetch_and_store(cur, force, http)
        except KaskadError as e:
            store.save_status({"at": at, "ok": False, "changed": False, "error": util.redact(str(e)),
                               "error_msg": getattr(e, "msg", None), "kept": cur.id if cur is not None else None})
            with util.journal_lang():
                log(tr("rules.log.update_failed", error=util.said(getattr(e, "msg", None) or str(e)),
                       kept=tr("rules.log.kept", lists=label(cur)) if cur else ""))
            raise
        v = store.active()
        # ⚠РФ: the marks of the lists counted again when they are new, missing or stale — a failure is a warning, the
        # update stays done and the old result stays (REV-SPEC-stage1-fix-21 З-3); written under this update_lock
        res["rf_scan"] = rfgate.refresh_scan(v, locked=True)
        if not res["rf_scan"]["ok"]:
            res["warnings"].append(util.msg("rules.rf.scan_failed", error=res["rf_scan"].get("error")))
        kept = res["warnings"]
        res["warnings"] = [util.redact(util.said(w)) for w in kept]
        store.save_status({"at": at, "ok": True, "changed": res["changed"], "warnings": res["warnings"],
                           "warnings_msg": kept, "version": v.id if v else None})
    res["lists"] = label(v)
    with util.journal_lang():                   # the journal: the language of the server, words of the codes too
        said = "; ".join(util.redact(util.said(w)) for w in kept)
        if res["changed"]:
            k = (v.meta.get("kernel") or {}) if v is not None else {}
            log(tr("rules.log.updated", lists=label(v), cidrs=k.get("cidrs", "—"), ranges=k.get("ranges", "—"),
                   warnings=("; " + said) if kept else ""))
        elif kept:
            log(tr("rules.log.unchanged", warnings=said))
    if res["changed"] and apply_live:
        res["apply"] = apply_system(runner, lock_timeout)
    return res


def apply_system(runner=None, lock_timeout=APPLY_LOCK_TIMEOUT):
    """New lists into the running gateway (as the collector does for a new pool): the table (gw_rd; no ranges in
    it), then the networks of Russia in a transaction of their own (``ensure_kernel``: gw_ru + gw_ru_ver, ``nft -f``
    with a timeout, never ``nft -c``), xray (files and rules of SOCKS5), dnsmasq (its lines). Nothing when the
    gateway is off. Failures are warnings: the lists are stored; a load that failed is tried again by
    kaskad-gw-rules-load.timer after its pause."""
    from ..gw import dns, exits, xray
    from ..gw import sys as gsys
    runner = _runner(runner)
    with state.locked(timeout=lock_timeout):
        st = state.load()
        if not model.enabled(st) or st.get("engine") != "nft":
            return {"applied": False, "warnings": []}
        res = {"applied": True, "warnings": []}
        try:
            nft.apply(st, runner)
        except KaskadError as e:
            res["warnings"].append(tr("rules.apply.table", error=e))
        res["kernel"] = rapply.ensure_kernel(runner, st, force=True)
        if res["kernel"]["action"] == "failed":
            res["warnings"].append(tr("rules.apply.kernel", error=res["kernel"].get("error")))
        sec = secrets.load()
        try:
            pool = exits.pool(runner, roles=exits.ROLES)     # REV-q-int З-1: the Russian exits too (gr-, TPROXY)
        except KaskadError as e:
            res["warnings"].append(tr("rules.apply.pool", error=e))
            return res
        dec = None
        try:                                   # stage 1 (§2.4, REV-s1-t5 С1): the decision of the collector, a number
            res["xray"], dec = xray.apply_now(st, sec, runner, pool)        # of the render under this state lock
            # what xray says (no gw_xports, VLESS cut…) — shown by «rules update» too, as by gw/ops._apply (REV-s1-b3 П3)
            for w in list(res["xray"].get("warnings") or []) + list(res["xray"].get("notes") or []):
                if w and w not in res["warnings"]:
                    res["warnings"].append(w)
        except KaskadError as e:
            res["warnings"].append(tr("rules.apply.xray", error=e))
            if (getattr(e, "data", None) or {}).get("ru_closed"):      # SEC-s1-r2 З2: the old config is stopped
                res["warnings"].append(tr("gw_xray.warn.ru_closed"))
            elif (getattr(e, "data", None) or {}).get("ru_open"):      # REV-s1-b5 П1: … and xray did not stop
                res["warnings"].append(tr("gw_xray.warn.ru_open"))
        if os.path.exists(gsys.unit_path(dns.UNIT)):
            try:
                res["dns"] = dns.apply(st, runner, via_pool=xray.dns_via_pool(st, pool),
                                       direct=exits.direct_needed(st, (dec or {}).get("policy")))
            except KaskadError as e:
                res["warnings"].append(tr("rules.apply.dns", error=e))
        return res


def load(runner=None, force=False, lock_timeout=APPLY_LOCK_TIMEOUT):
    """``kaskadctl rules load [--force]`` (kaskad-gw-rules-load.timer): the networks of Russia into gw_ru when the
    table does not hold the stored lists (``rules.apply.ensure_kernel``; a pause after a failure unless ``force``).
    Returns its result with "ok" (False only when a load was tried and failed)."""
    runner = _runner(runner)
    with state.locked(timeout=lock_timeout):
        st = state.load()
        if not model.enabled(st) or st.get("engine") != "nft":
            return {"ok": True, "action": "off"}
        res = rapply.ensure_kernel(runner, st, force=force)
    res["ok"] = res["action"] != "failed"
    return res


# ── the model: profiles and switches ───────────────────────────────────────

def _change(runner, mutate, rf=None):
    from ..gw import ops as gops
    return gops._change(_runner(runner), mutate, rf=rf)


def rules_ver(st):
    """``ver`` of the form «Правила» (SPEC-stage1-exits §7.9 «Версия объекта»): 12 hex of sha1 of the canonical JSON
    of its object — the profile of the node as it acts (``model.rules_conf``). The switches, the profiles of people and
    devices and the tick ⚠РФ are not in it."""
    return _ver_of(model.rules_conf(st)["default"])


def _ver_of(default):
    import hashlib
    obj = {"default": default}
    return hashlib.sha1(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:12]


def rf_of(confirm=False, via="cli", who=None, lang=None, orgs=None, must_show=False):
    """``rf`` of a caller: None without the tick. ``orgs`` — the organisations of the block the person SAW (None — the
    caller does not know); ``must_show`` — a surface that has to say them (MCP, the wizard: SEC-s1-r67 №1): without
    ``orgs`` the tick is refused with the block, so a consent never covers an organisation nobody was shown."""
    if not confirm:
        return None
    return {"confirm": True, "via": via, "who": who, "lang": lang, "orgs": orgs, "must_show": bool(must_show)}


def set_default(runner, profile, rf=None, ver=None):
    """The profile of the node (devices and users with ``inherit``): ru_direct | all_exit. ``ver`` — the version the
    form read (``status()["ver"]``): another one now — KaskadError CONFLICT ``object_changed``, nothing written."""
    model.parse_profile(profile, allow_inherit=False)

    def mutate(new, sec):
        if ver is not None and str(ver) != rules_ver(new):
            raise KaskadError(tr("rules.ver_changed"), code=CONFLICT,
                              data={"code": "object_changed", "ver": rules_ver(new)})
        return model.set_rules_conf(new, default=profile)
    conf, res = _change(runner, mutate, rf=rf)
    util.log_action("RULES default: %s" % conf["default"])
    return {"ok": True, "rules": conf, "apply": res, "ver": _ver_of(conf["default"])}


TARGETS = ("user", "device")


def _yes_no(v):
    return tr("rules.common.yes") if v else tr("rules.common.no")


def set_profile(runner, target, ref, profile, rf=None):
    """The profile of a user or a device: inherit | all_exit | ru_direct (``rf`` — the tick ⚠РФ, see the module)."""
    if target not in TARGETS:
        raise KaskadError(tr("rules.profile_target", target=repr(target), kinds="user|device"), code=USAGE)
    model.parse_profile(profile)

    def mutate(new, sec):
        obj = model.get_user(new, ref) if target == "user" else model.get_device(new, ref)
        model.set_profile(obj, profile)
        return dict(obj)
    obj, res = _change(runner, mutate, rf=rf)
    util.log_action("RULES profile: %s %s (%s) -> %s" % (target, obj.get("name"), obj.get("id"), profile))
    return {"ok": True, "target": target, "id": obj.get("id"), "name": obj.get("name"), "profile": profile,
            "apply": res}


def ru_ver(value):
    """``ver`` of the own «Российские сайты» of a person or a device (REV-3310 П1; SE §7.9 «Версия объекта»): 12 hex
    of sha1 of {"ru_via": <the own value, ``inherit`` when none>} — ``status()`` gives it in every row (``ru_ver``),
    an item of ``set_ru_via_many`` sends it back; another one under the lock — 409 ``object_changed``."""
    import hashlib
    obj = {"ru_via": value if isinstance(value, str) and value else model.RU_VIA_INHERIT}
    return hashlib.sha1(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:12]


def _ru_via_items(items):
    """The form of ``items`` of ``set_ru_via_many``: [{"user"|"device": ref, "via": value, "ver"?: ru_ver}] ->
    [(target, ref, via, ver|None)]."""
    if not isinstance(items, list) or not items:
        raise KaskadError(tr("rules.ru_via_items"), code=USAGE)
    out = []
    for it in items:
        keys = [t for t in TARGETS if isinstance(it, dict) and it.get(t) not in (None, "")]
        if len(keys) != 1 or "via" not in it:
            raise KaskadError(tr("rules.ru_via_items"), code=USAGE)
        ver = it.get("ver")
        out.append((keys[0], str(it[keys[0]]), it["via"], None if ver in (None, "") else str(ver)))
    return out


def set_ru_via_many(runner, items):
    """«Российские сайты» of several people and devices in ONE write (the dialog «Выбрать людей», SPEC-groups §8.2):
    ``items`` = [{"user"|"device": ref, "via": inherit|direct|<gid>}] (``model.parse_ru_via`` — a Russian group of the
    policy). All or nothing: one refusal — nothing is written. ``ru_via`` asks no consent ⚠РФ and gives none (no
    ``rf``: ``rfgate.check`` sees no new «Россия напрямую»). An item with ``ver`` (``ru_ver`` of the value the page
    read; REV-3310 П1) is compared under the lock BEFORE anything is written: the person or the device was changed
    meanwhile (another tab, the scheme, the bot, the CLI) — KaskadError CONFLICT ``object_changed`` with its id and the
    ``ver`` now, nothing written (two tabs never overwrite each other's choice silently; other rows of a tab are not
    refused for a change of somebody else). -> {"ok", "changed", "items", "apply"}."""
    rows = _ru_via_items(items)

    def mutate(new, sec):
        for target, ref, via, ver in rows:
            if ver is None:
                continue
            obj = model.get_user(new, ref) if target == "user" else model.get_device(new, ref)
            cur = ru_ver(obj.get("ru_via"))
            if ver != cur:
                raise KaskadError(tr("rules.ru_via_changed", name=obj.get("name") or obj.get("id")), code=CONFLICT,
                                  data={"code": "object_changed", "target": target, "id": obj.get("id"), "ver": cur})
        done, first = [], {}
        for target, ref, via, _ver in rows:
            obj = model.get_user(new, ref) if target == "user" else model.get_device(new, ref)
            first.setdefault((target, obj.get("id")), obj.get("ru_via") or model.RU_VIA_INHERIT)
            got = model.set_ru_via(new, obj, via)
            done.append({"target": target, "id": obj.get("id"), "name": obj.get("name"), "ru_via": got})
        last = dict(((r["target"], r["id"]), i) for i, r in enumerate(done))
        for i, r in enumerate(done):        # REV-s1-q1 №3: the same one twice — changed once, by what stays written
            r["changed"] = last[(r["target"], r["id"])] == i and r["ru_via"] != first[(r["target"], r["id"])]
        return done
    done, res = _change(runner, mutate)
    for r in done:
        if r["changed"]:
            util.log_action("RULES ru_via: %s %s (%s) -> %s" % (r["target"], r["name"], r["id"], r["ru_via"]))
    return {"ok": True, "changed": sum(1 for r in done if r["changed"]), "items": done, "apply": res}


def set_ru_via(runner, target, ref, via):
    """«Российские сайты» of a person or a device: inherit | direct | <gid> of a Russian group (``set_ru_via_many``
    with one item). -> {"ok", "target", "id", "name", "ru_via", "changed", "apply"}."""
    if target not in TARGETS:
        raise KaskadError(tr("rules.ru_via_target", target=repr(target), kinds="user|device"), code=USAGE)
    out = set_ru_via_many(runner, [{target: ref, "via": via}])
    return dict(out["items"][0], ok=True, apply=out["apply"])


def consent(runner, rf):
    """«Подтвердить» ⚠РФ (modes legacy, need, uncovered — §3.3, §6): the consent alone, no rule changes. Without
    ``ru_direct`` chosen anywhere there is nothing to confirm (nothing written). -> {"ok", "granted", "rf"}."""
    if not (rf or {}).get("confirm"):
        rfgate.refuse_need(rfgate.block_orgs(), (rf or {}).get("lang"))
    got = {}

    def mutate(new, sec):
        got["selected"] = rfgate.selected(new)
        return None
    _, res = _change(runner, mutate, rf=rf)
    st = state.load()
    return {"ok": True, "granted": bool(got.get("selected")), "rf": rfgate.state(st), "apply": res}


def set_blocks(runner, ads=None, torrent=None):
    """The switches: ``ads`` — category-ads + win-spy, ``torrent`` — torrent trackers (None — unchanged)."""
    if ads is None and torrent is None:
        raise KaskadError(tr("rules.blocks_nothing"), code=USAGE)

    def mutate(new, sec):
        return model.set_rules_conf(new, block_ads=ads, block_torrent=torrent)
    conf, res = _change(runner, mutate)
    with util.journal_lang():
        util.log_action(tr("rules.log.block", ads=_yes_no(conf["block_ads"]), torrent=_yes_no(conf["block_torrent"])))
    return {"ok": True, "rules": conf, "apply": res}


# ── status ─────────────────────────────────────────────────────────────────

def _dns_features(runner):
    from ..gw import dns
    if runner is None:
        f = dns.saved_features()
        return {"nftset": f.get("nftset"), "version": f.get("version")}
    f = dns.features(runner)
    ver = f.get("version")
    return {"nftset": f.get("nftset"), "version": ".".join(str(x) for x in ver) if ver else None}


def status(st=None, runner=None):
    """What the rules are now — for ``rules status``, ``gw status`` and the panel. Read-only; without ``runner`` it
    runs no command (dnsmasq's abilities come from what the last apply saved)."""
    st = state.load() if st is None else st
    cur = store.load(store.CURRENT)
    v = store.active()
    conf = model.rules_conf(st)
    k = rapply.kernel_meta(v)
    dp = rapply.profile(st, {}, k["ready"])         # the default: a device of nobody, with no choice of its own (R2)
    doc = {"ok": True, "ready": k["ready"], "default": conf["default"], "ver": rules_ver(st),
           "default_applied": dp["applied"], "default_why": dp["why"],
           "rf": rfgate.state(st, v),
           "block_ads": conf["block_ads"], "block_torrent": conf["block_torrent"], "lists": None, "groups": None,
           "last": last_status(), "attribution": attribution(),
           "sources": [s["url"] for s in source.SOURCES["geoip"] + source.SOURCES["geosite"]]}
    if v is not None:
        doc["lists"] = {"version": v.id, "label": label(v), "created": v.meta.get("created"),
                        "geoip": v.meta.get("geoip"), "text": v.meta.get("text"), "geosite": v.meta.get("geosite"),
                        "kernel": v.meta.get("kernel"), "domains": v.meta.get("domains"),
                        "fallback": cur is None or cur.path != v.path}
        g = rapply.groups(st, v)
        if g is not None:
            doc["groups"] = {"block": len(g["block"]), "force": len(g["force"]), "direct": len(g["direct"]),
                             "missing": g["missing"]}
    devices = []
    for d in model.devices(st):
        p = rapply.profile(st, d, k["ready"])
        devices.append({"id": d.get("id"), "name": d.get("name"), "user": d.get("user"),
                        "rules": d.get("rules") or "inherit", "profile": p["profile"], "from": p["from"],
                        "applied": p["applied"], "active": model.device_active(st, d)})
        devices[-1].update(_ru_view(st, d, k["ready"]))
    users = [{"id": u.get("id"), "name": u.get("name"), "rules": u.get("rules") or "inherit"} for u in model.users(st)]
    for u in users:
        u.update(_ru_view(st, {"user": u["id"]}))
    doc["devices"] = devices
    doc["users"] = users
    doc["ru_groups"] = _ru_groups(st, devices, users)
    doc["ru_direct"] = sum(1 for d in devices if d["applied"] == "ru_direct" and d["active"])
    doc["dns"] = _dns_features(runner)
    doc["table"] = _table_status(runner)
    if runner is not None:
        from . import timer
        ts = timer.status(runner)
        doc["timer"] = ts["timer"]
        doc["load_timer"] = ts["load_timer"]
    return doc


def _ru_view(st, obj, ready=None):
    """The fields «Российские сайты» of a row of ``status()`` (T15 Q1): ``ru_via`` — the own value (``inherit`` when
    none), ``ru`` — ``model.ru_via_of`` (for a person: what a device of theirs without its own value gets), and for a
    device ``ru_applies`` — whether «Россия напрямую» acts for it at all (``rules.apply.device_rd``); without it the
    choice is kept and does nothing (SPEC-groups §2.5)."""
    own = obj.get("ru_via") if "id" in obj else (model.find_user(st, obj.get("user")) or {}).get("ru_via")
    out = {"ru_via": own if own is not None else model.RU_VIA_INHERIT, "ru": model.ru_via_of(st, obj),
           "ru_ver": ru_ver(own)}
    if "id" in obj:
        out["ru_applies"] = rapply.device_rd(st, obj, ready)
    return out


def _ru_groups(st, devices, users):
    """The Russian groups of the policy for the line «Российские сайты» (§8.2): [{"id", "name", "label", "users" — the
    people who chose it themselves, "devices" — the devices it resolves to, "applies" — of them, where it acts}]."""
    from ..gw import policy
    out = []
    for grp in (policy.stored(st) or {}).get("groups") or []:
        if not isinstance(grp, dict) or grp.get("role", "abroad") != "ru" or not grp.get("id"):
            continue
        gid = grp["id"]
        out.append({"id": gid, "name": grp.get("name", ""), "label": policy.label(grp),
                    "users": [u["id"] for u in users if u["ru"]["via"] == gid and u["ru"]["from"] == "user"],
                    "devices": [d["id"] for d in devices if d["ru"]["via"] == gid],
                    "applies": [d["id"] for d in devices if d["ru"]["via"] == gid and d["ru_applies"]]})
    return out


def _table_status(runner):
    """Are the networks of Russia in the table: {"loaded": True | False | None (unknown), "wanted": version, "state":
    what the last load did}. With ``runner`` gw_ru_ver is read (``nft -j list set``, small); without — from the state
    file of the last load (the panel runs no command)."""
    from ..gw import net
    wanted = net.ru_wanted()
    lstate = rapply.load_state()
    have = None
    if runner is not None:
        try:
            have = net.read_ru_version(runner)
        except KaskadError:
            have = None
    elif lstate.get("ver") == wanted:
        have = wanted if lstate.get("ok") else -1
    loaded = None if have is None else (have == wanted and bool(wanted))
    return {"loaded": loaded, "wanted": wanted, "state": lstate}


def table_text(t):
    """«загружены» / «НЕ загружены (ошибка; снова в 12:30)» / «неизвестно» of ``status()["table"]``."""
    if t.get("loaded"):
        return tr("rules.table.loaded")
    s = t.get("state") or {}
    if t.get("loaded") is False and s.get("ver") == t.get("wanted") and not s.get("ok") and s.get("error"):
        nxt = s.get("next")
        return tr("rules.table.failed", error=s["error"],
                  next=tr("rules.table.again", at=time.strftime("%H:%M", time.localtime(nxt)))
                  if isinstance(nxt, (int, float)) else "")
    if t.get("loaded") is False:
        return tr("rules.table.not_loaded")
    return tr("rules.common.unknown")


def summary(doc):
    """One line for ``gw status``."""
    if not doc.get("lists"):
        return tr("rules.summary.no_lists")
    lst = doc["lists"]
    k = lst.get("kernel") or {}
    parts = [tr("rules.summary.lists", label=lst["label"]),
             tr("rules.summary.nets", cidrs=k.get("cidrs", "—"), ranges=k.get("ranges", "—"))]
    parts.append(tr("rules.summary.table", state=table_text(doc.get("table") or {})))
    g = doc.get("groups")
    if g:
        parts.append(tr("rules.summary.groups", direct=g["direct"], force=g["force"], block=g["block"]))
    nftset = (doc.get("dns") or {}).get("nftset")
    parts.append(tr("rules.summary.nftset", state=tr("rules.summary.nftset_yes") if nftset else (
        tr("rules.summary.nftset_no") if nftset is False else tr("rules.common.unknown"))))
    parts.append(tr("rules.summary.default", profile=tr("rules.profile.ru_direct")
                    if doc["default_applied"] == "ru_direct" else tr("rules.profile.all_exit")))
    if (doc.get("rf") or {}).get("mode") in ("legacy", "need"):
        parts.append(tr("rules.summary.rf_" + doc["rf"]["mode"]))
    if lst.get("fallback"):
        parts.append(tr("rules.summary.fallback"))
    return "; ".join(parts)

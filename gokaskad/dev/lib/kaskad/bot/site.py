"""The domain in the bot (docs/k3/SPEC-wave4.md §4.1, §7.1): the screen «🌐 Домен» — the domain, its DNS, the
certificate of Let's Encrypt with the days left and who uses it, the cover site (4.0-C). Changing — the same
``kaskad.site.ops`` calls as ``kaskadctl site`` (the way gateway.py calls kaskad.gw.ops), then ``snapshot.bump()``.

    sd             the screen
    sdn            «Задать домен»: the next text is the domain (the chat's dialog «site_domain»)
    sdd / sdw      check DNS now / wait until it is ready, up to 5 minutes (a job of the chat)
    sdc            issue the certificate (a job: «⏳ …» first, then the result or the refusal in words)
    sdu            SSTP / OpenConnect on the certificate of the domain: sdu:<kind> — how many devices need new
                   settings and the question, sduy:<kind>:<on|off> — do it
    sdx / sdxy     remove the domain: the consequences and the question / do it; VLESS on TLS of this domain with
                   devices (§3.6, REV-vless4 «было до» 1): the price in the question (the cover of Reality the bot
                   picked and named) and sdxy:Y<k>[:<site>] — do it with ``yes`` while the price still names the
                   devices of <k> (``_price_key``: their number and, with devices, a print of who they are —
                   REV-vless-fix З1, REV-3378-fix придирка 4; else the question again with the new price, nothing
                   done); sdxy — no
                   price was in the question (none, or a button sent before 3.3.7.8): no ``yes``, the core asks and
                   the bot asks again with it
    sdm            the cover site on (a random template) or off: the consequences and the question;
                   sdmy:onY<k> | sdmy:offY<k>[:<site>] — do it (a job) with ``yes`` while the price of VLESS
                   still names the devices of <k> (the question's ``_price_key``, 0 — no price; and the cover of
                   Reality the bot picked and named — §3.6); not so (a device came or went since — or one went and
                   another came: the print differs), or no <k> (sdmy:onY |
                   sdmy:offY[:<site>] sent before T-3378 Н-13) — nothing done, the question again with the price as it
                   is now; sdmy:on0 | sdmy:off0 — the price was not counted: the core asks it (no ``yes``);
                   sdmy:on | sdmy:off — buttons sent before 3.3.8 (no price in them): no ``yes`` either
                   (REV-vless-sh2 придирка 3)
    sdt:<p>        templates: categories by 8 (the catalog is brought by a job when it is not on the node)
    sdtc:<i>:<p>   templates of category <i> (its index in the catalog) by 8, the names link the previews
    sdtq[:<i>]     search: the next text is the word (the dialog «site_tpl_query»), sdts:<p> — its pages
    sdtr / sdti:<k> a random template / the template of short_key <k> (sha1(id)[:12]): the card with the preview
    sdty:<k>       put it: without the site — the site is switched on with it, with it — the template is replaced
                   «Жив ли шаблон» (3.2.21, site.tplhealth): lists and the card come at once by the cache; what it does
                   not know is checked in a thread and the same message is drawn again (unless another screen took it).
                   Removed by the author — «❌ удалён», no preview, no «Поставить»; the preview taken down — no link.
    sdp            the panel's address on the domain, masked; sdps — the whole address in a message that the bot
                   deletes in 5 minutes; sdpr / sdpry — a new address (the old one is 404 at once)
    sdq            check the site (a job): the verdict in words

Every callback_data is at most 64 bytes (``_DATA_MAX``, Telegram's limit): the longest — sdmy:offY<k>:<site> and
sdxy:Y<k>:<site> — fall back to sdmy:off0 / sdxy when they do not fit (the core asks the price again). The texts of refusals come from the core; the bot names the reason and what to do.
"""

import hashlib
import re
import threading

from .. import snapshot, util
from ..errors import KaskadError
from . import data, menus
from .lang import fmt_date, fmt_num, get_lang, tr, trn, use_lang
from .registry import callback, reply_key, state
from .telegram import h, inline

DNS_WAIT = 300
KINDS = ("sstp", "ocserv")
TITLES = {"sstp": "SSTP", "ocserv": "OpenConnect"}
DNS_STATES = ("ok", "partial", "elsewhere", "none", "unknown")
WHY = ("port80", "dns", "limit", "net", "apt")
SITE_WHY = {                    # reason of ops.Refusal -> (in words, what to do): keys of bot.site.*
    "no_domain": ("site_why_no_domain", "site_todo_no_domain"),
    "no_cert": ("site_why_no_cert", "site_todo_no_cert"),
    "port80": ("site_why_port80", "site_todo_without_site"),
    "port443": ("site_why_port443", "site_todo_without_site"),
    "neighbour": ("site_why_neighbour", "site_todo_without_site"),
    "apt": ("site_why_apt", "site_todo_apt"),
    "nginx_test": ("site_why_nginx_test", "site_todo_nginx_test"),
}
TEMPLATE_WHY = ("download_failed", "no_index", "app_shell", "broken_assets", "timeout", "too_big", "bad_repo",
                "nokey", "net", "gone")     # template_reason of the refusal «template» -> bot.site.tpl_why_<reason>
CHECK_VERDICTS = ("ok", "dns_mismatch", "no_cert", "http_error", "no_site", "cert_expiring", "panel_unreachable")
PER_SCREEN = 8                  # categories and templates on one screen of the bot
SECRET_TTL = 300                # s: the message with the whole address of the panel deletes itself


def back_site():
    """The row «⬅️ Домен» (to the screen of the domain)."""
    return [(tr("bot.site.btn_back_domain"), "sd")]


def _cancel(data_):
    return [menus.cancel_btn(data_)]


def _ops():
    from ..site import ops
    return ops


def _smodel():
    from ..site import model
    return model


def date_text(iso):
    """A date for a text of the bot in its language: «30.12.2026» / «Dec 30, 2026» (kaskad.i18n.fmt_date)."""
    return fmt_date(str(iso)[:10] if iso else iso)


def days_left(days):
    """«осталось 83 дн.» / «83 days left»; «?» when the number is unknown."""
    if isinstance(days, int) and not isinstance(days, bool):
        return trn("bot.site.days_left", days)
    return tr("bot.site.days_left_unknown")


def _name(doc):
    return doc.get("domain_unicode") or doc.get("domain") or ""


def cert_le(st=None):
    """(domain as people typed it, its end date in the current language — date_text) when the copy of the certificate of Let's Encrypt for the domain
    is valid — the panel's open mode and the pages /p/ serve it; else None."""
    try:
        model = _smodel()
        st = data.state_doc() if st is None else st
        info = model.active(st)
    except (KaskadError, ImportError, OSError, ValueError):
        return None
    if info is None:
        return None
    return model.unicode(model.domain_of(st)), date_text(info.get("not_after"))


def dns_html(d):
    if not d:
        return tr("bot.site.dns_not_checked")
    st = d.get("state")
    word = tr("bot.site.dns_" + st) if st in DNS_STATES else (st or "?")
    if d.get("ready"):
        line = tr("bot.site.dns_ready", word=word)
    else:
        line = tr("bot.site.dns_not_ready", word=word,
                  a=h(", ".join(d.get("a") or []) or tr("bot.site.dns_a_none")),
                  own=h(", ".join(d.get("own") or []) or tr("bot.site.dns_own_unknown")))
    if d.get("advice"):
        line += "\n<i>%s</i>" % h(d["advice"])
    return line


def cert_html(c):
    if not c or c.get("kind") != "letsencrypt":
        return tr("bot.site.cert_self")
    days = c.get("days_left")
    mark = "🔒" if isinstance(days, int) and days >= 21 else "⚠️"
    return tr("bot.site.cert_le", mark=mark, staging=tr("bot.site.cert_staging") if c.get("staging") else "",
              date=date_text(c.get("not_after")), left=days_left(days))


def users_text(doc):
    u = doc.get("use") or {}
    names = [t for k, t in (("panel", tr("bot.site.use_panel")), ("pages", tr("bot.site.use_pages")),
                            ("sstp", "SSTP"), ("ocserv", "OpenConnect")) if u.get(k)]
    return tr("bot.site.users", names=", ".join(names) or tr("bot.site.users_nobody"))


def _gw_set_up(st):
    from ..gw import model
    g = model.peek(st)
    return bool(g["enabled"] or model.installed_kinds(st))


def _my_ip(ctx):
    try:
        from ..gw import model
        host = model.public_host(data.state_doc())
    except KaskadError:
        host = ""
    return host or ctx.app.my_ip or tr("bot.site.server_address")


def _template_name(t):
    """The name of the template on the site: its name, else its id, else «простая страница»."""
    t = t or {}
    return t.get("name") or t.get("id") or tr("bot.site.simple_page")


def status_text(doc, ctx):
    if not doc.get("domain"):
        return tr("bot.site.no_domain", ip=h(_my_ip(ctx)))
    name = _name(doc)
    lines = [tr("bot.site.domain_line", name=h(name),
                ascii=(" (%s)" % h(doc["domain"])) if name != doc["domain"] else ""),
             dns_html(doc.get("dns")), cert_html(doc.get("cert"))]
    site = doc.get("site") or {}
    if doc.get("mode") == "site":
        lines.append(tr("bot.site.site_on", template=h(_template_name(site.get("template")))))
        if site.get("panel_url_masked"):
            lines.append(tr("bot.site.panel_on_domain", url=h(site["panel_url_masked"])))
    else:
        lines.append(tr("bot.site.site_off"))
        p443 = site.get("port443") or {}
        if p443.get("free") is False and _le(doc):
            lines.append(tr("bot.site.port443_busy", who=h(p443.get("who") or "?")))
    if (doc.get("cert") or {}).get("kind") == "letsencrypt":
        lines.append(users_text(doc))
    p80 = doc.get("port80") or {}
    if p80.get("free") is False:
        lines.append(tr("bot.site.port80_busy", who=h(p80.get("who") or "?")))
    if not doc.get("host_is_domain") and _gw_set_up(data.state_doc()):
        lines.append(tr("bot.site.host_not_domain"))
    lines += vless_lines(data.state_doc())
    return "\n".join(lines)


def vless_lines(st):
    """SPEC-vless-tls-default §4.2: the mode of VLESS in the bot — TLS, or Reality with its reason, and the
    recommendation with the price. The bot gives no consent and no choice of Reality (К2, В5): the way to switch is the
    panel or the SSH menu. -> [lines] ([] — no VLESS)."""
    from ..gw import model as gm
    from ..gw import ops as gops
    try:
        if not isinstance(st, dict) or gm.inbound(st, "vless") is None:
            return []
        v = gops.vless_view(st)
    except Exception:                                   # noqa: BLE001 — a summary line only
        return []
    if v.get("security") == "tls":
        return [tr("bot.site.vless_tls")]
    why = (v.get("reason_text") or "").rstrip(" .")
    out = [tr("bot.site.vless_reality", why=tr("bot.site.vless_why", why=h(why)) if why else "")]
    rec = (v.get("recommend") or {}).get("text")
    fix = (v.get("fix") or {}).get("text")
    if rec:
        out.append(tr("bot.site.vless_recommend", text=h(rec)))
    elif fix:
        out.append(tr("bot.site.vless_fix", text=h(fix.rstrip(" ."))))
    return out


def _le(doc):
    c = doc.get("cert") or {}
    return c.get("kind") == "letsencrypt"


def screen_kb(doc):
    if not doc.get("domain"):
        return inline([[(tr("bot.site.btn_set_domain"), "sdn")], menus.back_row()])
    le = _le(doc)
    rows = [[(tr("bot.site.btn_check_dns"), "sdd"),
             (tr("bot.site.btn_check_cert") if le else tr("bot.site.btn_issue_cert"), "sdc")],
            [(tr("bot.site.btn_use"), "sdu")]]
    if doc.get("mode") == "site":
        rows += [[(tr("bot.site.btn_change_template"), "sdt:1"), (tr("bot.site.btn_panel_address"), "sdp")],
                 [(tr("bot.site.btn_check_site"), "sdq"), (tr("bot.site.btn_site_off"), "sdm")]]
    elif le:                                            # the site needs a certificate of Let's Encrypt
        rows += [[(tr("bot.site.btn_site_random"), "sdm")],
                 [(tr("bot.site.btn_pick_template"), "sdt:1")]]
    rows += [[(tr("bot.site.btn_change_domain"), "sdn"), (tr("bot.site.btn_remove_domain"), "sdx")],
             [(tr("bot.site.btn_refresh"), "sd")], menus.back_row()]
    return inline(rows)


@callback("sd")
def cb_site(ctx, d):
    doc = _ops().status(ctx.runner)
    ctx.reply(status_text(doc, ctx), screen_kb(doc))


@reply_key("bot.menu.btn_domain")
def rp_site(ctx, text):
    cb_site(ctx, "sd")


# ── the domain ─────────────────────────────────────────────────────────────

@callback("sdn")
def cb_domain_ask(ctx, d):
    ctx.set_state("site_domain", {})
    ctx.reply(tr("bot.site.domain_ask", ip=h(_my_ip(ctx))), inline([_cancel("sd")]))


@state("site_domain")
def st_domain(ctx, text, d):
    name = text.strip()
    if _ops().replaces_working(name):
        return _change_domain(ctx, name)
    try:
        res = _ops().set_domain(ctx.runner, name)
    except KaskadError as e:
        ctx.set_state("site_domain", {})
        ctx.send(tr("bot.site.domain_bad", error=h(util.redact(str(e)))), inline([_cancel("sd")]))
        return
    ctx.clear_state()
    snapshot.bump()
    doc = res["site"]
    lines = [tr("bot.site.domain_set", name=h(_name(doc))), dns_html(res["dns"])]
    if res.get("text"):
        lines.append("⚠️ %s" % h(res["text"]))
    ctx.send("\n".join(lines), _after_dns_kb(res["dns"]))


def _change_domain(ctx, name):
    """A working domain replaced (T-M6-qs330d №1): the new name checked and given its certificate while the old one
    serves, then the switch — a long action; any refusal leaves the old domain as it was."""
    app = ctx.app
    ops = _ops()
    ctx.clear_state()
    old = _name(ops.status(ctx.runner))

    def run():
        try:
            res = ops.set_domain(app.runner, name)
        except ops.Refusal as e:
            ctx.send(tr("bot.site.domain_not_changed", text=h(util.redact(e.doc.get("text") or str(e)))),
                     inline([[(tr("bot.site.btn_other_domain"), "sdn")], back_site()]))
            return
        except KaskadError as e:
            ctx.send("❌ %s" % h(util.redact(str(e))), inline([[(tr("bot.site.btn_other_domain"), "sdn")],
                                                               back_site()]))
            return
        snapshot.bump()
        lines = [tr("bot.site.domain_changed", old=h(old), new=h(_name(res["site"])))]
        v = res.get("vless") or {}
        if v.get("text"):
            lines.append("ℹ️ %s" % h(util.redact(v["text"])))
        for w in (res.get("warnings") or [])[:5]:
            lines.append("⚠️ %s" % h(util.redact(w)))
        ctx.send("\n".join(lines), inline([back_site()]))

    ctx.reply(tr("bot.site.domain_changing", name=h(name), old=h(old)))
    ctx.job(tr("bot.site.job_change_domain"), run)


def _after_dns_kb(dns):
    if dns and dns.get("ready"):
        return inline([[(tr("bot.site.btn_issue_cert"), "sdc")], back_site()])
    return inline([[(tr("bot.site.btn_wait_dns"), "sdw"), (tr("bot.site.btn_check_again"), "sdd")],
                   [(tr("bot.site.btn_issue_anyway"), "sdc")] if dns and dns.get("state") in ("partial", "unknown")
                   else [],
                   back_site()])


@callback("sdd")
def cb_dns(ctx, d):
    doc = _ops().check_dns(ctx.runner)
    ctx.reply(tr("bot.site.dns_check_head") + "\n" + dns_html(doc), _after_dns_kb(doc))


@callback("sdw")
def cb_dns_wait(ctx, d):
    app = ctx.app

    def run():
        doc = _ops().check_dns(app.runner, wait=DNS_WAIT)
        head = tr("bot.site.dns_wait_ready") if doc.get("ready") else tr("bot.site.dns_wait_timeout")
        ctx.send("%s\n%s" % (head, dns_html(doc)), _after_dns_kb(doc))

    ctx.reply(tr("bot.site.dns_waiting"))
    ctx.job(tr("bot.site.job_dns_wait"), run)


# ── the certificate ────────────────────────────────────────────────────────

def refusal_html(doc):
    reason = doc.get("reason")
    head = tr("bot.site.cert_refused_why", why=tr("bot.site.why_" + reason)) if reason in WHY else \
        tr("bot.site.cert_refused")
    out = "%s\n%s" % (head, h(doc.get("text") or ""))
    if reason == "apt":
        out += "\n" + tr("bot.site.cert_apt_hint")
    return out


def issued_html(res):
    c = res.get("cert") or {}
    lines = ["✅ %s" % h(res.get("text") or tr("bot.site.cert_issued_default")),
             tr("bot.site.cert_valid", date=date_text(c.get("not_after")), left=days_left(c.get("days_left")))]
    if any(x.get("name") == "panel" for x in res.get("consumers") or [] if isinstance(x, dict)):
        lines.append(tr("bot.site.cert_panel_on"))
    for w in (res.get("warnings") or [])[:5]:
        lines.append("⚠️ %s" % h(util.redact(w)))
    return "\n".join(lines)


@callback("sdc")
def cb_issue(ctx, d):
    app = ctx.app
    ops = _ops()
    doc = ops.status(ctx.runner)
    if not doc.get("domain"):
        return _no_domain(ctx)
    name = _name(doc)

    def run():
        try:
            res = ops.issue(app.runner)
        except ops.Refusal as e:
            ctx.send(refusal_html(e.doc), inline([[(tr("bot.site.btn_retry"), "sdc")], back_site()]))
            return
        snapshot.bump()
        st = data.state_doc()
        from ..gw import model as gm
        rows = []
        if any(k in gm.installed_kinds(st) for k in KINDS):
            rows.append([(tr("bot.site.btn_use"), "sdu")])
        rows.append(back_site())
        ctx.send(issued_html(res), inline(rows))

    ctx.reply(tr("bot.site.cert_issuing", name=h(name)))
    ctx.job(tr("bot.site.job_issue"), run)


def _no_domain(ctx):
    return ctx.reply(tr("bot.site.no_domain_first"),
                     inline([[(tr("bot.site.btn_set_domain"), "sdn")], back_site()]))


# ── the cover site (4.0-C): on / off, templates, the panel's address, the check ──

def _site_refusal_html(head, e):
    """A refusal of the core (ops.Refusal: reason no_domain | no_cert | port80 | port443 | neighbour | apt | template |
    nginx_test) — the reason in words, the core's text and what to do; any other error — its text."""
    doc = getattr(e, "doc", None)
    reason = doc.get("reason") if isinstance(doc, dict) else None
    text = (doc.get("text") if isinstance(doc, dict) else None) or str(e)
    if reason == "template":
        tpl = doc.get("template_reason")
        why = tr("bot.site.tpl_not_set",
                 why=tr("bot.site.tpl_why_" + (tpl if tpl in TEMPLATE_WHY else "download_failed")))
        todo = tr("bot.site.tpl_todo_" + (tpl if tpl in ("nokey", "net") else "other"))
        todo += " " + tr("bot.site.tpl_todo_kept")
    elif reason in SITE_WHY:
        why, todo = (tr("bot.site." + k) for k in SITE_WHY[reason])
    else:
        return "❌ %s: %s" % (head, h(util.redact(str(e))))
    return "❌ %s: %s.\n%s\n%s" % (head, why, h(util.redact(text)), todo)


def _template_error(e):
    """The catalog or a template could not be brought: no GitHub key — the way to set it; else the core's text."""
    data = getattr(e, "data", None) or {}
    if isinstance(data, dict) and data.get("reason") == "nokey":
        return tr("bot.site.catalog_nokey")
    return "❌ %s" % h(util.redact(str(e)))


def _templates():
    from ..site import templates
    return templates


def _health():
    from ..site import tplhealth
    return tplhealth


def _spawn(fn):
    """The check of templates runs in a thread of its own (tests run it in place)."""
    t = threading.Thread(target=fn, name="kaskad-bot-tplcheck")
    t.daemon = True
    t.start()


def _with_health(ctx, ids, render, preview=True):
    """``render(health, checking) -> (text, markup)`` on the screen at once by the cache (an unchecked id — None,
    ``checking`` True); the unchecked ids are checked in a thread (up to 40 s; not the chat's job — it may be busy
    with a certificate), then the same message is drawn again through the chat's lane — only when no other screen was
    drawn in it since (app.edits): a late answer never covers another screen, and «⏳» does not stay on a list the
    person left for a new message."""
    H = _health()
    health = H.cached(ids)
    need = [i for i in ids if health.get(i) is None]
    text, markup = render(health, bool(need))
    sent = ctx.reply(text, markup, preview=preview)
    mid = sent.get("message_id") if isinstance(sent, dict) else None
    mid = mid or (None if ctx.new else ctx.message_id)
    if not need or mid is None:
        return
    app, chat, make = ctx.app, ctx.chat_id, type(ctx)
    mark = app.edit_mark(chat, mid)
    lang = get_lang()                   # the thread and the late redraw speak the language of this answer

    def work():
        with use_lang(lang):
            try:
                got = H.check(app.runner, need)
            except Exception:           # noqa: BLE001 — the catalog went away, a broken check: «не проверить»
                got = {}
            full = dict(health)
            full.update(dict((i, got.get(i) or H.doc("unknown")) for i in need))

        def redraw():
            if app.edit_mark(chat, mid) != mark:            # the message shows another screen now
                return
            with use_lang(lang):
                t2, m2 = render(full, False)
            app.safe(make(app, chat).edit, mid, t2, m2, preview)
        app.scheduler.submit(chat, redraw)
    _spawn(work)


def _with_catalog(ctx, then):
    """``then(templates)`` once the catalog is on the node; without it — «⏳» and a job that brings it (a key of GitHub
    and up to two minutes), then ``then``. The catalog is never fetched by the status screen."""
    T = _templates()
    try:
        T.catalog()
    except T.CatalogMissing:
        app = ctx.app

        def run():
            try:
                T.prepare(app.runner)
            except KaskadError as e:
                ctx.send(_template_error(e), inline([back_site()]))
                return
            then(T)
        ctx.reply(tr("bot.site.catalog_loading"))
        ctx.job(tr("bot.site.job_catalog"), run)
        return
    then(T)


def _num(n):
    """33 467 in Russian, 33,467 in English (kaskad.i18n.fmt_num)."""
    return fmt_num(int(n or 0))


def _cut(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def _preview(url):
    url = str(url or "")
    return url if url.startswith(("https://", "http://")) else ""


def _cat_at(T, index):
    """A category of the catalog by its index (callback_data keeps the index, not the id)."""
    try:
        i = int(index)
    except (TypeError, ValueError):
        return None
    cats = T.catalog()["categories"]
    return cats[i] if 0 <= i < len(cats) else None


@callback("sdm")
def cb_site_mode_ask(ctx, d):
    doc = _ops().status(ctx.runner)
    if not doc.get("domain"):
        return _no_domain(ctx)
    name = h(_name(doc))
    if doc.get("mode") == "site":
        root = h((doc.get("site") or {}).get("root") or "/var/www/kaskad-site")
        head = tr("bot.site.off_ask", name=name, root=root)
        line, known, pick, n = _vless_line(ctx, "domain")
        if pick:                                        # §3.6: the cover of Reality is picked (network) — a job
            app = ctx.app

            def run():
                ln, kn, site, nn = _vless_line(ctx, "domain", pick=True, runner=app.runner)
                ctx.send(head + ln, inline([[(tr("bot.site.btn_yes_off"), _off_data(kn, site, nn))]
                                            + _cancel("sd")]))
            ctx.reply(tr("bot.site.vless_picking"))
            return ctx.job(tr("bot.site.job_pick"), run)
        return ctx.reply(head + line, inline([[(tr("bot.site.btn_yes_off"), _off_data(known, None, n))]
                                              + _cancel("sd")]))
    c = doc.get("cert") or {}
    if c.get("kind") != "letsencrypt" or c.get("staging"):
        return ctx.reply(tr("bot.site.need_le"), inline([[(tr("bot.site.btn_issue_cert"), "sdc")], back_site()]))
    line, known, _, n = _vless_line(ctx, "site")
    ctx.reply(tr("bot.site.on_ask", name=name) + line,
              inline([[(tr("bot.site.btn_turn_on"), "sdmy:onY%s" % n if known else "sdmy:on0")] + _cancel("sd")]))


_DATA_MAX = 64                                          # bytes of callback_data (Telegram)


def _off_data(known, site, n):
    """The button «Да, выключить»: with ``yes`` only when the price was counted for the question — <n> the key of the
    devices it names (``_price_key``; T-3378 Н-13: «Да» is the consent to that price); the cover the bot named goes
    with it (it fits callback_data — else the core asks the price itself)."""
    if not known:
        return "sdmy:off0"
    data = "sdmy:offY%s" % n + (":" + site if site else "")
    return data if len(data.encode("utf-8")) <= _DATA_MAX else "sdmy:off0"


def _pick_cover(st):
    """§3.6: the cover of Reality for the site off — the rule «Подобрать автоматически» (``vless.pick_mask``) on the node
    as it will be (the site off: the node's own site is no cover then); only a site that passed the check. -> host |
    None."""
    import copy
    from ..gw.inbounds import vless as vin
    cp = copy.deepcopy(st)
    if isinstance(cp.get("site"), dict):
        cp["site"]["mode"] = "domain"
    got = vin.pick_mask(cp)
    ok = got.get("mask") != "own" and (got.get("mask_check") or {}).get("ok") and vin.valid_host(got.get("sni"))
    return got["sni"] if ok else None


def _vless_line(ctx, value, pick=False, runner=None, drop_domain=False):
    """The price for VLESS of switching the site on/off (SPEC-vless-tls-default §3.6/§3.7) — a line of the question:
    the bot asks it itself, and «yes» to its question is the answer passed to the core (``mode(…, yes=True)``).
    -> (line, known, site|need): ``known`` False — the price could not be counted (SEC-vless-sh12 придирка 4): the
    question goes without it, and its «yes» is NOT passed — the core asks the price itself. The site off with no cover
    kept (``need_pick``): without ``pick`` -> (…, True) — the caller picks it in a job; with ``pick`` the bot picks the
    first site that passes the check, names it in the price (REV-vless-sh2 заметно 3) -> (line, True, site); none
    passed -> the price without «yes» (the core asks). ``drop_domain`` — «Убрать домен» (the site off and the domain
    away). The 4th item — the key of the devices the price names (``_price_n``, "0" — no price): the consent of «Да»
    (REV-vless-fix З1)."""
    from .. import state
    runner = runner or ctx.runner
    try:
        _ops().vless_price(state.load(), runner, value, drop_domain=drop_domain)
    except KaskadError as e:
        if not (e.data or {}).get("need_pick"):
            return "\n\n⚠️ " + h(util.redact(str(e))), True, None, _price_n(e)
        if not pick:
            return "", True, True, "0"
        try:
            site = _pick_cover(state.load())
        except Exception:                               # noqa: BLE001 — no network: the core asks the price
            site = None
        if not site:
            return "\n\n⚠️ " + h(tr("bot.site.vless_no_pick")), False, None, "0"
        try:
            _ops().vless_price(state.load(), runner, value, vless_site=site, drop_domain=drop_domain)
        except KaskadError as e2:
            return "\n\n⚠️ " + h(util.redact(str(e2))), True, site, _price_n(e2)
        except Exception:                               # noqa: BLE001
            return "", False, None, "0"
        return "", True, site, "0"
    except Exception:                                   # noqa: BLE001 — a hint only: the core asks again anyway
        return "", False, None, "0"
    return "", True, None, "0"


def _price_key(devs):
    """The consent of «Да» to a price (T-3378 Н-13, REV-3378-fix придирка 4): «<n>» — the number of devices the price
    names, and with devices «<n>.<6 hex>» — a print of who they are (sha256 of their ids, sorted): a device that went
    and another that came keep the number, not the print. «0» — no devices (no price)."""
    ids = sorted(str(d.get("id") or "") for d in (devs or []) if isinstance(d, dict))
    if not ids:
        return "0"
    return "%d.%s" % (len(ids), hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()[:6])


def _price_n(err):
    """The key of the devices in the price of VLESS (``vless_price`` -> CONFLICT data «devices»; the text names them)
    — ``_price_key``."""
    return _price_key((getattr(err, "data", None) or {}).get("devices"))


def _clear_n(runner, site):
    """REV-vless-fix З1: the key of the devices the price of «Убрать домен» names now (``_price_key``) — counted as
    the question counts it (``vless_price`` with the cover of the button); "0" — no price; None — could not count."""
    return _now_n(runner, "domain", site, drop_domain=True)


def _now_n(runner, value, site=None, drop_domain=False):
    """The key of the devices the price of VLESS names now (``_price_key``) for the site ``value`` (on «site» / off
    «domain»; the domain away — ``drop_domain``), counted as the question counts it (``vless_price`` with the cover of
    the button); "0" — no price; None — could not count (the question again)."""
    from .. import state
    try:
        _ops().vless_price(state.load(), runner, value, vless_site=site, drop_domain=drop_domain)
    except KaskadError as e:
        return _price_n(e)
    except Exception:                                   # noqa: BLE001 — not counted: the question again
        return None
    return "0"


def _enabled_html(res):
    t = res.get("template") or {}
    lines = [tr("bot.site.enabled", name=h(_name((res.get("site") or {}))), template=h(_template_name(t)))]
    if res.get("fallback") or t.get("id") == "builtin_stub":
        lines.append(tr("bot.site.enabled_stub"))
    for w in (res.get("warnings") or [])[:3]:
        lines.append("⚠️ %s" % h(util.redact(w)))
    mask = ((res.get("site") or {}).get("site") or {}).get("panel_url_masked")
    if mask:
        lines.append(tr("bot.site.enabled_panel", url=h(mask)))
    return "\n".join(lines)


def _mode_job(ctx, value, template_id=None, yes=False, vless_site=None):
    """site mode site|domain as a job: «⏳ …» first, then the result or the refusal in words. ``yes`` — only the answer
    to the bot's own question with the price of VLESS in it (``_vless_line``); without it the core asks the price."""
    app = ctx.app
    ops = _ops()

    def run():
        try:
            res = ops.mode(app.runner, value, template_id=template_id, yes=yes, via="bot",     # yes: its own question
                           vless_site=vless_site)
        except KaskadError as e:
            head = tr("bot.site.head_site_not_on") if value == "site" else tr("bot.site.head_site_not_off")
            ctx.send(_site_refusal_html(head, e), inline([[(tr("bot.site.btn_retry"), "sdm")], back_site()]))
            return
        snapshot.bump()
        if value == "site":
            ctx.send(_enabled_html(res), inline([[(tr("bot.site.btn_check_site"), "sdq")], back_site()]))
        else:                                   # VLESS went to Reality: why and whom to give the links again (§3.6)
            lines = [tr("bot.site.disabled")] + ["⚠️ %s" % h(util.redact(w)) for w in (res.get("warnings") or [])[:3]]
            ctx.send("\n".join(lines + vless_lines(data.state_doc())), inline([back_site()]))

    if value == "site":
        ctx.reply(tr("bot.site.enabling"))
    else:
        ctx.reply(tr("bot.site.disabling"))
    ctx.job(tr("bot.site.job_site"), run)


def _old(ctx):
    """A button of a previous menu (its data no longer parses)."""
    return ctx.reply(menus.old_button(), menus.back())


_KEY = r"[0-9]{1,6}(?:\.[0-9a-f]{6})?"                 # _price_key: «<n>» | «<n>.<print>»
_MODE_YES = re.compile(r"(on|off)Y(%s)?(?::(.+))?" % _KEY, re.S)


@callback("sdmy:", prefix=True)
def cb_site_mode_do(ctx, d):
    """sdmy:onY<k> | sdmy:offY<k>[:<site>] — the price of VLESS was in the question (<k> — ``_price_key`` of its
    devices, 0 — none; the cover the bot named): ``yes`` while the price still names those devices (``_now_n``, as
    the question counts). Not so, or no <k> (a button sent before T-3378 Н-13), or a number with no print (before
    REV-3378-fix) — nothing done, the question again with the price as it is now (the stale «Да», as sdxy:Y<k>). The
    data is the chat's input: what does not parse — an old button."""
    from ..gw.inbounds import vless as vin
    m = _MODE_YES.fullmatch(d[len("sdmy:"):])
    if m:
        value, site = ("site" if m.group(1) == "on" else "domain"), m.group(3)
        if site is not None and (value == "site" or not vin.valid_host(site)):
            return _old(ctx)
        if m.group(2) is None or _now_n(ctx.runner, value, site) != m.group(2):
            return _ask_again(ctx, value)
        return _mode_job(ctx, value, yes=True, vless_site=site)
    value = {"sdmy:on0": "site", "sdmy:off0": "domain", "sdmy:on": "site", "sdmy:off": "domain"}.get(d)
    if value is None:
        return _old(ctx)
    _mode_job(ctx, value, yes=False)


def _ask_again(ctx, value):
    """The stale «Да» of sdm: the question again with the price as it is now — or, the site already as the button
    wanted (switched by the panel, say), the screen of the domain: no question the other way round."""
    if (_ops().status(ctx.runner).get("mode") == "site") == (value == "site"):
        return cb_site(ctx, "sd")
    return cb_site_mode_ask(ctx, "sdm")


# ── templates: categories by 8, templates by 8, search, «🎲 Наугад», the card with the preview ──

def _nav(page, pages, fmt):
    row = []
    if page > 1:
        row.append((tr("bot.site.btn_prev"), fmt % (page - 1)))
    if page < pages:
        row.append((tr("bot.site.btn_next"), fmt % (page + 1)))
    return [row] if row else []


@callback("sdt:", prefix=True)
def cb_templates(ctx, d):
    try:
        page = int(d[4:])
    except ValueError:
        return _old(ctx)

    def show(T):
        cat = T.catalog()
        cats = cat["categories"]
        pages = max(1, (len(cats) + PER_SCREEN - 1) // PER_SCREEN)
        p = max(1, min(page, pages))
        first = (p - 1) * PER_SCREEN
        rows = [[("%s (%s)" % (_cut(x.get("title") or x["id"], 40), _num(x.get("count"))), "sdtc:%d:1" % (first + i))]
                for i, x in enumerate(cats[first:first + PER_SCREEN])]
        rows += _nav(p, pages, "sdt:%d")
        rows += [[(tr("bot.site.btn_random"), "sdtr"), (tr("bot.site.btn_search"), "sdtq")], back_site()]
        ctx.reply(tr("bot.site.templates_head", total=_num(cat["total"]),
                     pages=tr("bot.site.pages_of", p=p, pages=pages) if pages > 1 else ""),
                  inline(rows))
    _with_catalog(ctx, show)


def _state(health, tid):
    return ((health or {}).get(tid) or {}).get("state")


def _list_html(head, res, health=None, checking=False):
    """A page of templates: «N. <a href=preview>Name</a> — description» (the preview is the link). Removed by the
    author — «❌ <s>Name</s> — удалён автором», the preview taken down — the name without a link."""
    lines = [head]
    if not res["items"]:
        lines.append(tr("bot.site.nothing_found"))
    for i, it in enumerate(res["items"], 1):
        name = h(_cut(it.get("name") or it["id"], 60))
        st = _state(health, it["id"])
        if st == "gone":
            lines.append(tr("bot.site.item_gone", i=i, name=name))
            continue
        url = _preview(it.get("preview")) if st != "no_preview" else ""
        title = '<a href="%s">%s</a>' % (h(url), name) if url else name
        desc = _cut(it.get("description"), 80)
        tail = tr("bot.site.item_no_preview") if st == "no_preview" else ((" — " + h(desc)) if desc else "")
        lines.append("%d. %s%s" % (i, title, tail))
    if res["items"]:
        lines.append("\n" + (tr("bot.site.list_checking") if checking else tr("bot.site.list_hint")))
    return "\n".join(lines)


def _item_rows(T, res, health=None):
    buttons = [("%d · %s%s" % (i, "❌ " if _state(health, it["id"]) == "gone" else "",
                                _cut(it.get("name") or it["id"], 24)), "sdti:" + T.short_key(it["id"]))
               for i, it in enumerate(res["items"], 1)]
    return [buttons[i:i + 2] for i in range(0, len(buttons), 2)]


@callback("sdtc:", prefix=True)
def cb_templates_category(ctx, d):
    parts = d.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return _old(ctx)

    def show(T):
        cat = _cat_at(T, parts[1])
        if cat is None:
            return _old(ctx)
        res = T.search(category=cat["id"], page=int(parts[2]), per_page=PER_SCREEN)

        def render(health, checking):
            head = tr("bot.site.category_head", title=h(cat.get("title") or cat["id"]), total=_num(res["total"]),
                      page=res["page"], pages=res["pages"])
            rows = _item_rows(T, res, health) + _nav(res["page"], res["pages"], "sdtc:%s:%%d" % parts[1])
            rows += [[(tr("bot.site.btn_random"), "sdtr"), (tr("bot.site.btn_search"), "sdtq:%s" % parts[1])],
                     [(tr("bot.site.btn_categories"), "sdt:1")], back_site()]
            return _list_html(head, res, health, checking), inline(rows)
        _with_health(ctx, [it["id"] for it in res["items"]], render, preview=False)
    _with_catalog(ctx, show)


@callback("sdtq", prefix=True)
def cb_templates_search_ask(ctx, d):
    cat = d[5:] if d.startswith("sdtq:") else ""
    if cat and not cat.isdigit():
        return _old(ctx)

    def ask(T):
        c = _cat_at(T, cat) if cat else None
        ctx.set_state("site_tpl_query", {"c": cat if c else ""})
        where = tr("bot.site.search_in_category", title=h(c.get("title") or c["id"])) if c else \
            tr("bot.site.search_everywhere")
        ctx.reply(tr("bot.site.search_ask", where=where), inline([_cancel("sdtc:%s:1" % cat if c else "sdt:1")]))
    _with_catalog(ctx, ask)


def _found(ctx, T, cat, query, page):
    c = _cat_at(T, cat) if cat else None
    res = T.search(category=c["id"] if c else None, query=query, page=page, per_page=PER_SCREEN)
    ctx.set_state("site_tpl_query", {"c": cat if c else "", "q": res["query"] or ""})

    def render(health, checking):
        where = tr("bot.site.found_where", title=h(c.get("title") or c["id"])) if c else ""
        head = tr("bot.site.found_head", query=h(res["query"] or ""), where=where, total=_num(res["total"]),
                  page=res["page"], pages=res["pages"])
        rows = _item_rows(T, res, health) + _nav(res["page"], res["pages"], "sdts:%d")
        rows += [[(tr("bot.site.btn_categories"), "sdt:1")], back_site()]
        return _list_html(head + "\n" + tr("bot.site.found_more"), res, health, checking), inline(rows)
    _with_health(ctx, [it["id"] for it in res["items"]], render, preview=False)


@state("site_tpl_query")
def st_templates_search(ctx, text, data):
    q = _templates().norm_query(text)
    if not q:
        return ctx.send(tr("bot.site.search_empty"), inline([[(tr("bot.site.btn_categories"), "sdt:1")]]))
    _with_catalog(ctx, lambda T: _found(ctx, T, (data or {}).get("c") or "", q, 1))


@callback("sdts:", prefix=True, dialog=True)
def cb_templates_search_page(ctx, d):
    try:
        page = int(d[5:])
    except ValueError:
        return _old(ctx)
    st, data, _ = ctx.app.dialogs.get(ctx.chat_id)
    if st != "site_tpl_query" or not (data or {}).get("q"):
        return ctx.reply(tr("bot.site.search_stale"), inline([[(tr("bot.site.btn_search"), "sdtq")], back_site()]))
    _with_catalog(ctx, lambda T: _found(ctx, T, data.get("c") or "", data["q"], page))


def _card(ctx, T, tid, random=False):
    it = T.get(tid) if tid else None
    if it is None:
        return _old(ctx)
    doc = _ops().status(ctx.runner)
    cats = dict((c["id"], c.get("title") or c["id"]) for c in T.catalog()["categories"])

    def render(health, checking):
        st = _state(health, tid)
        lines = ["🎨 <b>%s</b>" % h(it.get("name") or tid),
                 tr("bot.site.card_category", category=h(cats.get(it.get("category"), "—")))]
        if it.get("description"):
            lines.append(h(it["description"]))
        lines.append(tr("bot.site.card_license", license=h(it.get("license") or tr("bot.site.license_none")),
                        author=h(it.get("author") or "?")))
        again = [[(tr("bot.site.btn_another_random"), "sdtr")]] if random else []
        to_list = [[(tr("bot.site.btn_to_templates"), "sdt:1")], back_site()]
        if st == "gone":                # removed by the author: nothing to look at, nothing to put
            lines.append("\n" + tr("bot.site.card_gone"))
            return "\n".join(lines), inline(again + to_list)
        url = _preview(it.get("preview"))
        if st == "no_preview":
            lines.append(tr("bot.site.card_no_demo"))
        else:
            lines.append(tr("bot.site.card_preview", url=h(url)) if url else tr("bot.site.card_no_preview"))
        if checking:
            lines.append(tr("bot.site.card_checking"))
        if doc.get("mode") == "site":
            lines.append("\n" + tr("bot.site.card_replace",
                                   template=h(_template_name((doc.get("site") or {}).get("template")))))
        else:
            lines.append("\n" + tr("bot.site.card_turn_on"))
        rows = [[(tr("bot.site.btn_install"), "sdty:" + T.short_key(tid))]] + again + to_list
        return "\n".join(lines), inline(rows)
    _with_health(ctx, [tid], render)


@callback("sdti:", prefix=True)
def cb_template_card(ctx, d):
    _with_catalog(ctx, lambda T: _card(ctx, T, T.by_short_key(d[5:])))


@callback("sdtr")
def cb_template_random(ctx, d):
    def pick(T):
        tid = T.pick_random()
        if not tid:
            return ctx.reply(tr("bot.site.no_template"), inline([back_site()]))
        _card(ctx, T, tid, random=True)
    _with_catalog(ctx, pick)


def _set_html(res, tid):
    if not res.get("ok"):
        why = util.redact(res.get("text") or tr("bot.site.reason_unknown"))
        return tr("bot.site.tpl_not_installed", why=h(why))
    if res.get("fallback"):
        lines = [tr("bot.site.set_stub")]
    else:
        lines = [tr("bot.site.set_ok", name=h(res.get("name") or tid))]
    if res.get("backup"):
        lines.append(tr("bot.site.set_backup", path=h(res["backup"])))
    for w in (res.get("warnings") or [])[:3]:
        lines.append("⚠️ %s" % h(util.redact(w)))
    return "\n".join(lines)


@callback("sdty:", prefix=True)
def cb_template_set(ctx, d):
    """Without the site — it is switched on with this template; with it — the template is replaced (a job)."""
    app = ctx.app
    T = _templates()
    try:
        tid = T.by_short_key(d[5:])
    except KaskadError as e:                            # the catalog vanished since the card: open it again
        return ctx.reply(_template_error(e), inline([[(tr("bot.site.btn_templates"), "sdt:1")], back_site()]))
    if not tid:
        return _old(ctx)
    if _health().is_gone(tid):          # known as removed: no job, no clone
        return ctx.reply(tr("bot.site.tpl_gone", name=h((T.get(tid) or {}).get("name") or tid)),
                         inline([[(tr("bot.site.btn_templates"), "sdt:1")], back_site()]))
    ops = _ops()
    if ops.status(ctx.runner).get("mode") != "site":
        return _mode_job(ctx, "site", template_id=tid)

    def run():
        try:
            res = ops.template_set(app.runner, tid)
        except KaskadError as e:
            ctx.send(_template_error(e), inline([back_site()]))
            return
        snapshot.bump()
        ctx.send(_set_html(res, tid), inline([[(tr("bot.site.btn_another_template"), "sdt:1")], back_site()]))

    ctx.reply(tr("bot.site.installing", name=h((T.get(tid) or {}).get("name") or tid)))
    ctx.job(tr("bot.site.job_template"), run)


# ── the panel's address on the domain: masked; shown in full by a message that deletes itself ──

@callback("sdp")
def cb_panel_url(ctx, d):
    try:
        res = _ops().panel_url()
    except KaskadError as e:
        return ctx.reply("❌ %s" % h(util.redact(str(e))), inline([back_site()]))
    text = tr("bot.site.panel_url", url=h(res["url"]))
    if res.get("fallback"):                      # 3.3.7.5: the open mode beside the domain, masked as well
        text += "\n" + tr("bot.site.panel_fallback", url=h(res["fallback"]))
    ctx.reply(text,
              inline([[(tr("bot.site.btn_show_full"), "sdps")], [(tr("bot.site.btn_change_address"), "sdpr")],
                      back_site()]))


@callback("sdps")
def cb_panel_url_show(ctx, d):
    try:
        res = _ops().panel_url(show=True)
    except KaskadError as e:
        return ctx.reply("❌ %s" % h(util.redact(str(e))), inline([back_site()]))
    text = tr("bot.site.panel_secret", minutes=SECRET_TTL // 60, url=h(res["url"]))
    if res.get("fallback"):
        text += "\n" + tr("bot.site.panel_fallback", url=h(res["fallback"]))
    sent = ctx.send(text, preview=False)
    mid = sent.get("message_id") if isinstance(sent, dict) else None
    if mid is not None:
        ctx.app.expire_later(ctx.chat_id, mid, SECRET_TTL)


@callback("sdpr")
def cb_panel_path_ask(ctx, d):
    ctx.reply(tr("bot.site.rotate_ask"), inline([[(tr("bot.site.btn_yes_change"), "sdpry")] + _cancel("sdp")]))


@callback("sdpry")
def cb_panel_path_do(ctx, d):
    app = ctx.app

    def run():
        try:
            res = _ops().rotate_panel_path(app.runner)
        except KaskadError as e:
            ctx.send(_site_refusal_html(tr("bot.site.head_address_not_changed"), e), inline([back_site()]))
            return
        snapshot.bump()
        ctx.send(tr("bot.site.rotated", url=h(res["url"])),
                 inline([[(tr("bot.site.btn_show_full"), "sdps")], back_site()]))

    ctx.reply(tr("bot.site.rotating"))
    ctx.job(tr("bot.site.job_rotate"), run)


def check_text(res):
    """The verdict of ``site check`` in words (and the reason with what to do, when the core names it)."""
    v = res.get("verdict")
    if v == "http_error":
        hp = res.get("http") or {}
        text = tr("bot.site.check_http_error", root=hp.get("root", "?"), redirect=hp.get("redirect", "?"))
    elif v == "cert_expiring":
        text = tr("bot.site.check_cert_expiring", left=days_left(res.get("days_left")))
    elif v in CHECK_VERDICTS:
        text = tr("bot.site.check_" + v)
    else:
        text = tr("bot.site.check_unknown", verdict=h(v))
    if res.get("why"):                         # nginx не открывает сокет панели — причина и что сделать
        text += "\n" + tr("bot.site.check_why", why=h(res["why"]), fix=h(res.get("fix") or ""))
    return text


@callback("sdq")
def cb_site_check(ctx, d):
    app = ctx.app

    def run():
        res = _ops().check(app.runner)
        snapshot.bump()
        ctx.send("%s\n%s" % (tr("bot.site.check_head"), check_text(res)),
                 inline([[(tr("bot.site.btn_recheck"), "sdq")], back_site()]))

    ctx.reply(tr("bot.site.checking_site"))
    ctx.job(tr("bot.site.job_check"), run)


# ── SSTP and OpenConnect ───────────────────────────────────────────────────

@callback("sdu")
def cb_use(ctx, d):
    doc = _ops().status(ctx.runner)
    use = doc.get("use") or {}
    imp = doc.get("use_impact") or {}
    lines = [tr("bot.site.use_head"), tr("bot.site.use_about", name=h(_name(doc))), ""]
    rows = []
    for k in KINDS:
        n = imp.get(k, 0)
        lines.append(tr("bot.site.use_line", title=TITLES[k],
                        now=tr("bot.site.use_now_domain") if use.get(k) else tr("bot.site.use_now_self"),
                        change=tr("bot.site.use_change_back") if use.get(k) else tr("bot.site.use_change_to"),
                        n="%d" % n))
        rows.append([(tr("bot.site.btn_use_back" if use.get(k) else "bot.site.btn_use_to", title=TITLES[k]),
                      "sdu:" + k)])
    rows.append(back_site())
    ctx.reply("\n".join(lines), inline(rows))


def _kind(s):
    return s if s in KINDS else None


@callback("sdu:", prefix=True)
def cb_use_ask(ctx, d):
    kind = _kind(d[4:])
    if kind is None:
        return _old(ctx)
    ops = _ops()
    on = not (ops.status(ctx.runner).get("use") or {}).get(kind)
    try:
        res = ops.use(ctx.runner, kind, on, dry_run=True)
    except KaskadError as e:
        return ctx.reply("❌ %s" % h(util.redact(str(e))),
                         inline([[(tr("bot.site.btn_issue_cert"), "sdc")], back_site()]))
    n = len(res.get("affected") or [])
    title = TITLES[kind]
    lines = [tr("bot.site.use_ask_on" if on else "bot.site.use_ask_off", title=title)]
    if n:
        lines.append(tr("bot.site.use_devices", n=n))
        lines.append(h(res.get("text") or ""))
    else:
        lines.append(tr("bot.site.use_no_devices", title=title))
    for w in (res.get("warnings") or [])[:5]:
        lines.append("⚠️ %s" % h(util.redact(w)))
    ctx.reply("\n".join(lines), inline([[(tr("bot.site.btn_yes_move") if on else tr("bot.site.btn_yes_return"),
                                          "sduy:%s:%s" % (kind, "on" if on else "off"))] + _cancel("sdu")]))


@callback("sduy:", prefix=True)
def cb_use_do(ctx, d):
    parts = d.split(":")
    if len(parts) != 3 or _kind(parts[1]) is None or parts[2] not in ("on", "off"):
        return _old(ctx)
    kind, on = parts[1], parts[2] == "on"
    res = _ops().use(ctx.runner, kind, on)
    snapshot.bump()
    lines = [tr("bot.site.use_done", title=TITLES[kind],
                what=tr("bot.site.use_done_domain") if (res.get("use") or {}).get(kind)
                else tr("bot.site.use_done_self"))]
    if res.get("text"):
        lines.append(h(res["text"]))
    for w in (res.get("warnings") or [])[:5]:
        lines.append("⚠️ %s" % h(util.redact(w)))
    ctx.reply("\n".join(lines), inline([[("🔐 SSTP / OpenConnect", "sdu")], back_site()]))


# ── removing the domain ────────────────────────────────────────────────────

@callback("sdx")
def cb_clear_ask(ctx, d):
    doc = _ops().status(ctx.runner)
    if not doc.get("domain"):
        return ctx.reply(tr("bot.site.no_domain_short"), inline([back_site()]))
    use = doc.get("use") or {}
    imp = doc.get("use_impact") or {}
    lines = [tr("bot.site.clear_ask", name=h(_name(doc)))]
    if doc.get("mode") == "site":
        lines.append(tr("bot.site.clear_site"))
    lines.append(tr("bot.site.clear_self"))
    on = [k for k in KINDS if use.get(k)]
    if on:
        total = doc.get("use_impact_total")
        if not isinstance(total, int) or total < 0:
            total = sum(imp.get(k, 0) for k in on)
        split = "; ".join("%s: %d" % (TITLES[k], imp.get(k, 0)) for k in on)
        lines.append(tr("bot.site.clear_use", titles=tr("bot.site.and").join(TITLES[k] for k in on),
                        total="%d" % total, split=split))
    n_hy2 = _hy2_domain_devices(ctx.runner)
    if n_hy2:                                   # T-3378 Н-12: the price of Hysteria2, as the panel (site.clearHy2)
        lines.append(tr("bot.site.clear_hy2", n="%d" % n_hy2))
    for w in (doc.get("people_warn") or {}).get("clear") or []:      # 3.3.7.5 круг 3: links of people, the port
        lines.append("• %s" % h(w))
    lines.append(tr("bot.site.clear_keep"))
    if doc.get("host_is_domain"):
        lines.append(tr("bot.site.clear_host"))
    head = "\n".join(lines)
    # the price of VLESS (§3.6): TLS on this domain goes to Reality — the same text of the core as «Выключить сайт»
    line, known, pick, n = _vless_line(ctx, "domain", drop_domain=True)
    if pick:                                        # no cover kept: the bot picks it (network) — a job
        app = ctx.app

        def run():
            ln, kn, site, nn = _vless_line(ctx, "domain", pick=True, runner=app.runner, drop_domain=True)
            if ln and not kn:                       # none passed the check: without a cover the core refuses — no «Да»
                ctx.send(head + "\n\n⚠️ " + h(tr("bot.site.clear_no_pick")),
                         inline([[(tr("bot.site.btn_retry"), "sdx")] + _cancel("sd")]))
                return
            ctx.send(head + ln, _clear_kb(ln, kn, site, nn))
        ctx.reply(tr("bot.site.vless_picking"))
        return ctx.job(tr("bot.site.job_pick"), run)
    ctx.reply(head + line, _clear_kb(line, known, None, n))


def _hy2_domain_devices(runner):
    """Devices of Hysteria2 on the domain's certificate (``gw hy2 show``: cert_mode «domain»): with the domain away it
    goes back to the self-signed one — their links are given again. 0 — none, no Hysteria2, or not readable (a line of
    the question only)."""
    from ..gw import ops as gops
    try:
        h2 = gops.hy2_show(runner)
    except Exception:                                   # noqa: BLE001 — a line of the question only
        return 0
    n = h2.get("devices") if h2.get("installed") and h2.get("cert_mode") == "domain" else 0
    return n if isinstance(n, int) and n > 0 else 0


def _clear_kb(line, known, site, n):
    """«Да, убрать»: with ``yes`` (sdxy:Y<k>) only when the price of VLESS was in the question — <k> the key of the
    devices it names (``_price_key``; REV-vless-fix З1: «Да» is the consent to that price), the cover the bot named
    with it (when it fits callback_data); else sdxy — no ``yes``: the core asks the price and the bot asks again."""
    data = "sdxy"
    if known and line:
        data = "sdxy:Y%s" % n + (":" + site if site else "")
        if len(data.encode("utf-8")) > _DATA_MAX:
            data = "sdxy"
    return inline([[(tr("bot.site.btn_yes_remove"), data)] + _cancel("sd")])


@callback("sdxy")
def cb_clear(ctx, d):
    """No price of VLESS was in the question (none, or a button sent before 3.3.7.8): no ``yes``."""
    _clear_do(ctx)


_CLEAR_YES = re.compile(r"Y(%s)?(?::(.+))?" % _KEY, re.S)


@callback("sdxy:", prefix=True)
def cb_clear_yes(ctx, d):
    """sdxy:Y<k>[:<site>] — the price of VLESS was in the question (and the cover the bot named): ``yes`` while the
    price still names the devices of <k> (``_clear_n``, as the question counts); not so (a device came or went since,
    or one went and another came), or no <k> (sdxy:Y[:<site>] of a 3.3.7.8 build before REV-vless-fix), or a number
    with no print (before REV-3378-fix) — nothing done, the question again with the price as it is now (REV-vless-fix
    З1, the stale «Да»)."""
    from ..gw.inbounds import vless as vin
    m = _CLEAR_YES.fullmatch(d[len("sdxy:"):])
    if not m:
        return _old(ctx)
    site = m.group(2)
    if site is not None and not vin.valid_host(site):
        return _old(ctx)
    if m.group(1) is None or _clear_n(ctx.runner, site) != m.group(1):
        return cb_clear_ask(ctx, "sdx")
    _clear_do(ctx, yes=True, site=site)


def _clear_do(ctx, yes=False, site=None):
    try:
        res = _ops().clear(ctx.runner, way_in=True, yes=yes, vless_site=site)
    except KaskadError as e:
        if (e.data or {}).get("need") != "vless_change":
            raise
        # the links of VLESS would change and the price was not in the question (or it changed since): nothing was
        # done — the question again, now with the price
        return cb_clear_ask(ctx, "sdx")
    snapshot.bump()
    lines = [tr("bot.site.cleared")]
    if res.get("text"):
        lines.append("⚠️ %s" % h(res["text"]))
    for w in res.get("warnings") or []:
        lines.append("⚠️ %s" % h(w))
    if res.get("kept"):
        lines.append(tr("bot.site.cleared_kept", names=h(", ".join(res["kept"]))))
    ctx.reply("\n".join(lines), inline([[(tr("bot.menu.btn_domain"), "sd")], menus.back_row()]))

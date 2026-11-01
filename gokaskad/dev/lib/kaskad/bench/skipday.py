"""The push «2 scheduled checks skipped in a day» (SPEC-node-score-schedule-share §2.5 п. 7, §11.1 п. 1, 5, 18, 27; B11).

* The day of a skip — the local day of the node by the NOMINAL moment of the event (``run.ts`` of the skip, §3.1),
  only ``busy`` and ``locked`` of ``by == "schedule"`` count (``spool.SKIP_COUNTED``; a manual check or a repeat that
  found the server busy is no skipped scheduled check).
* The mark ``/var/lib/kaskad/bench/skip-push.json`` = ``{"day": "YYYY-MM-DD", "sent": bool}`` (0600, atomic) — «the
  bot decided about this day». ONLY the bot writes it (``decide``): after the push ``sent: true``, the switch off —
  ``sent: false`` (final too). The mark only grows: a day ≤ ``day`` is decided — never sent, never written again.
  The collector reads it (``spool.drain`` gives the event of the transition 1 -> 2 only for a later day).
* With the gateway the bot takes the event ``bench_skipped_day`` of live.json; without it the monitor of the bot
  (``host_tick``) counts the skips of the queue itself: «yesterday» first, then «today» (``days_to_tell``).
"""

import calendar
import json
import os
import re
import time

from .. import paths, tz, util

KIND = "bench_skipped_day"
MIN = 2
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")   # not by the locale


def path():
    return os.path.join(paths.bench_dir(), "skip-push.json")


def mark(now=None):
    """``day`` of the mark ("" — none or broken). A day later than tomorrow (the clock ran ahead once) is no mark
    (SEC П-2): the push of the skips is never silenced till that day."""
    try:
        doc = json.loads(util.read_text(path()) or "{}")
    except (ValueError, RecursionError):
        return ""
    d = doc.get("day") if isinstance(doc, dict) else None
    if not (isinstance(d, str) and _DAY.fullmatch(d)):
        return ""
    return d if d <= tz.day((time.time() if now is None else now) + 86400) else ""


def decided(day):
    """The bot already decided about ``day`` (a day not later than the mark)."""
    return not (isinstance(day, str) and _DAY.fullmatch(day)) or day <= mark()


def write(day, sent):
    """The mark — only when ``day`` is later than the written one (it only grows). -> written."""
    if decided(day):
        return False
    util.ensure_dir(paths.bench_dir(), 0o700)
    util.atomic_write(path(), json.dumps({"day": day, "sent": bool(sent)}, sort_keys=True), 0o600)
    return True


def _date(day):
    y, m, d = (int(x) for x in day.split("-"))
    return y, m, d


def text(day, n, now=None, lang=None):
    """«Сегодня/Вчера/17.10 пропущено 2 проверки…» by the nominal day relative to the moment of sending."""
    from ..i18n import fmt_num, tr
    now = time.time() if now is None else now
    today = tz.day(now)
    y, m, d = _date(today)
    yesterday = time.strftime("%Y-%m-%d", time.gmtime(calendar.timegm((y, m, d, 12, 0, 0)) - 86400))
    num = fmt_num(n, lang)
    if day == today:
        return tr("bench.skipday.today", lang, n=num)
    if day == yesterday:
        return tr("bench.skipday.yesterday", lang, n=num)
    from ..i18n import get_lang, normalize
    _y, mo, dd = _date(day)
    en = (normalize(lang, "en") if lang else get_lang()) == "en"
    return tr("bench.skipday.date", lang, n=num, date=("%s %d" % (_MON[mo - 1], dd)) if en
              else "%02d.%02d" % (dd, mo))


def counts(runs):
    """{day: the skips busy/locked OF THE SCHEDULE} of the rows of the history (``ts`` — the nominal moment): a manual
    check or a repeat that found the server busy is no skipped scheduled check (REV З-5, SEC З-1)."""
    from .spool import SKIP_COUNTED
    out = {}
    for r in runs:
        if r.get("status") == "skipped" and r.get("by") == "schedule" and r.get("why") in SKIP_COUNTED \
                and isinstance(r.get("ts"), int):
            d = tz.day(r["ts"])
            out[d] = out.get(d, 0) + 1
    return out


def days_to_tell(runs, now=None):
    """Without the gateway (§2.5 п. 7): of «yesterday», then «today» (the nominal days) — those with ≥ 2 skips and
    later than the mark, in this order. -> [(day, n)]."""
    now = time.time() if now is None else now
    today = tz.day(now)
    y, m, d = _date(today)
    yesterday = time.strftime("%Y-%m-%d", time.gmtime(calendar.timegm((y, m, d, 12, 0, 0)) - 86400))
    c = counts(runs)
    return [(day, c[day]) for day in (yesterday, today) if c.get(day, 0) >= MIN and not decided(day)]

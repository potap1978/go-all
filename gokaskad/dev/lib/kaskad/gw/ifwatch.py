"""The interfaces of the WireGuard and AmneziaWG inbounds under watch (T-M6-qs330b №8).

After ``systemctl reboot`` a client of the inbound AmneziaWG 3.1 (on the kernel module) came back, lost the inbound for
≈21 s once more and only then stayed; the clients of WireGuard and VLESS came back once. A lost inbound costs every
AmneziaWG client 15–25 s (it handshakes again only after KeepaliveTimeout + RekeyTimeout), and nothing in the journal
said who had touched the inbound. Restarts by Kaskad are written down (tunnel.note_restart: the reason and the command);
what systemd or someone else does is not. So the collector looks every round (``observe``):

    the interface    /sys/class/net/<iface>/ifindex — another index: it was recreated; none: it is gone
    the unit         its starts since the boot: every start of a unit of START_KINDS appends a line to
                     /run/kaskad/gw/start.<unit> (gw/gate.py, the drop-in kaskad-gate.conf): a new line — started again
    the peers        the counters of the clients' sessions (the adapters' status of the round): lower than before on the
                     same interface — their sessions were reset without a new interface (setconf, a peer removed and
                     added again)

and says each event in ONE line of /var/log/kaskad.log, with who did it: Kaskad's own restart of this inbound when it
is fresh (its reason and command), else systemd or a command not of Kaskad. The first round after the boot also tells
a unit started more than once since the boot (a restart before the collector ran). The baseline of the last round is
/run/kaskad/gw/ifwatch.json (tmpfs: a reboot starts afresh, a restart of the collector does not). Never fails a round.
"""

import json
import os
import time

from .. import boot, paths, util
from ..i18n import tr, trn
from . import gate, model, registry

STATE_FILE = "ifwatch.json"
FIRST_MAX = 3600            # s of uptime: the first round tells starts since the boot only this early

_now = time.time            # tests patch it


def state_path():
    return os.path.join(paths.gw_run_dir(), STATE_FILE)


def ifindex(iface):
    raw = (util.read_text(paths.P("/sys/class/net/%s/ifindex" % iface)) or "").strip()
    return int(raw) if raw.isdigit() else None


def starts(unit):
    """[seconds since the boot] of every start of the unit since the boot (gate.start_line)."""
    out = []
    for ln in (util.read_text(gate.start_record(unit)) or "").splitlines():
        f = ln.split()
        if f and f[0].isdigit():
            out.append(int(f[0]))
    return out


def _load():
    raw = util.read_text(state_path())
    try:
        doc = json.loads(raw) if raw else {}
    except ValueError:
        doc = {}
    return doc if isinstance(doc, dict) else {}


def _save(doc):
    try:
        util.atomic_write(state_path(), json.dumps(doc, sort_keys=True) + "\n", 0o600)
    except OSError:
        pass


def _peers(stat):
    """{device or address: rx + tx} of the sessions in an adapter's status."""
    out = {}
    for s in (stat or {}).get("sessions") or []:
        key = str(s.get("device") or s.get("ip") or "")
        if key:
            out[key] = int(s.get("rx") or 0) + int(s.get("tx") or 0)
    return out


def _kaskad(kind, since):
    """Kaskad's restart of ``kind`` at or after ``since``: «Kaskad — причина «…», кто: …», else None."""
    from .inbounds import tunnel
    doc = tunnel.read_json(tunnel.restarts_path(), {})
    rec = doc.get(kind) if isinstance(doc, dict) else None
    if not isinstance(rec, dict) or not isinstance(rec.get("at"), int) or rec["at"] < since:
        return None
    return tr("gw_ifwatch.by_kaskad", why=tunnel.RESTART_WHY.get(rec.get("why"), rec.get("why") or "?"),
              by=rec.get("by") or tr("gw_ifwatch.by_unknown"))


def _core(ad):
    try:
        core = ad.unit_core() if hasattr(ad, "unit_core") else None
    except Exception:                       # noqa: BLE001 — the watch never fails the round
        core = None
    return tr("gw_ifwatch.core." + core) if core in ("kernel", "userspace") else None


def _when(up):
    return tr("gw_ifwatch.when", up=up) if up is not None else tr("gw_ifwatch.when_unknown")


def _idx(v):
    return v if v is not None else tr("gw_ifwatch.none")


def _starts_text(recs):
    return ", ".join(tr("gw_ifwatch.start_at", t=t) for t in recs)


def observe(st, runner, stats, log=None, now=None):
    """One round of the watch (the collector, after the adapters' status ``stats`` of the round). -> the lines logged
    (journal lines: in the language of the server)."""
    with util.journal_lang():
        return _observe(st, runner, stats, log, now)


def _observe(st, runner, stats, log=None, now=None):
    log = log or util.log_action
    now = _now() if now is None else now
    up = boot.uptime()
    up = int(up) if up is not None else None
    prev = _load()
    first = not prev
    pk = prev.get("kinds") if isinstance(prev.get("kinds"), dict) else {}
    since = int(prev.get("at") or 0) - 2 if not first else int(now - (up or 0)) - 2
    cur, lines = {}, []
    for kind in model.enabled_kinds(st):
        if kind not in gate.START_KINDS:
            continue
        ad = registry.get(kind)
        unit, iface = getattr(ad, "unit", None), getattr(ad, "iface", None)
        if not isinstance(unit, str) or not isinstance(iface, str):
            continue
        idx, recs = ifindex(iface), starts(unit)
        peers = _peers((stats or {}).get(kind))
        cur[kind] = {"iface": iface, "ifindex": idx, "starts": len(recs), "peers": peers}
        head = tr("gw_ifwatch.head", kind=kind, iface=iface)
        who_c = "WireGuard" if kind == "wg" else "AmneziaWG"
        core = _core(ad)
        tail = tr("gw_ifwatch.engine", core=core) if core else ""
        p = pk.get(kind)
        if not isinstance(p, dict):
            if first and len(recs) > 1 and (up is None or up < FIRST_MAX):
                who = _kaskad(kind, since) or tr("gw_ifwatch.who_not_kaskad", unit=unit)
                lines.append(tr("gw_ifwatch.started_times", head=head, unit=unit,
                                times=trn("gw_ifwatch.times", len(recs)), starts=_starts_text(recs),
                                clients=who_c, who=who, tail=tail))
            continue
        was_idx, was_starts = p.get("ifindex"), int(p.get("starts") or 0)
        new = recs[was_starts:] if len(recs) > was_starts else []
        if (was_idx is not None and idx is not None and idx != was_idx) or \
                (new and (was_idx is not None or was_starts > 0 or len(new) > 1)):
            if new:
                who = _kaskad(kind, since) or tr("gw_ifwatch.who_restarted", unit=unit, starts=_starts_text(new))
            else:
                who = _kaskad(kind, since) or tr("gw_ifwatch.who_bypassed")
            lines.append(tr("gw_ifwatch.recreated", head=head, when=_when(up), was=_idx(was_idx), now=_idx(idx),
                            clients=who_c, who=who, tail=tail))
        elif was_idx is not None and idx is None:
            who = _kaskad(kind, since) or tr("gw_ifwatch.who_not_removed")
            lines.append(tr("gw_ifwatch.gone", head=head, when=_when(up), was=was_idx, unit=unit, who=who, tail=tail))
        elif idx is not None and idx == was_idx:
            old = p.get("peers") if isinstance(p.get("peers"), dict) else {}
            reset = sorted(k for k, v in peers.items() if isinstance(old.get(k), int) and v < old[k])
            if reset:
                who = _kaskad(kind, since) or tr("gw_ifwatch.who_not_set")
                lines.append(tr("gw_ifwatch.reset", head=head, when=_when(up), n=len(reset), total=len(peers),
                                who=who, tail=tail))
    _save({"at": int(now), "uptime": up, "kinds": cur})
    for ln in lines:
        log(ln)
    return lines


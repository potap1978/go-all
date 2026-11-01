"""What the bot reads: state.json and live.json through ``kaskad.snapshot`` (cached by signature, no forks),
the forwards of either engine, the targets of ping and monitoring.

Engine nft: the forwards are state.json's. Engine legacy (iptables, the 2.x format): read-only, exactly what
2.x ``get_rules_list`` read — DNAT rules with a ``kaskad:<port>:<proto>`` comment in nat PREROUTING (one
``iptables -S``); the bot never writes iptables (adding, deleting, «Сбросить всё» are refused there).
"""

import ipaddress
import re

from .. import ipt, paths, snapshot
from .. import state as kstate
from .lang import tr


def legacy_text():
    """Why the bot does not add or delete forwards on the engine legacy (iptables)."""
    return tr("bot.fwd.legacy")


# ASCII digits only (SEC-3372 П-4: «\d» took «١.٢.٣.٤», which validate_ip of bash refuses), the whole text (``\Z``)
_IPV4 = re.compile(r"^(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])(\.(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])){3}\Z")


def valid_ip(text):
    """A dotted IPv4 address without leading zeros (validate_ip of 2.x)."""
    return bool(_IPV4.match(str(text or "").strip()))


def valid_port(text):
    s = str(text or "").strip()
    return bool(re.match(r"^[1-9]\d{0,4}$", s)) and 1 <= int(s) <= 65535


def state_doc():
    doc = snapshot.read_json(paths.state())
    return kstate.as_loaded(doc) if isinstance(doc, dict) else {}      # ``gen`` as ``load`` sets it (D-135)


def live_doc():
    """live.json of the collector (docs/k3/SPEC-wave3.md §9.2) or {} — missing file or fields = no data."""
    doc = snapshot.read_json(paths.live_json())
    if not isinstance(doc, dict):
        return {}
    try:
        from ..gw import live as gw_live            # its texts in the language of the chat (3.3.2)
    except ImportError:
        return doc
    return gw_live.localize(doc)


def engine():
    return "nft" if state_doc().get("engine") == "nft" else "legacy"


def forwards(runner=None):
    """(engine, rows): rows {"proto", "port", "to", "to_port", "name"} sorted by (port, proto)."""
    st = state_doc()
    if st.get("engine") == "nft":
        rows = []
        for f in st.get("forwards") or []:
            try:
                rows.append(kstate.normalize_forward(f))
            except Exception:                       # noqa: BLE001 — a broken record is not shown
                continue
        return "nft", kstate.sort_forwards(rows)
    return "legacy", legacy_forwards(runner)


def legacy_forwards(runner):
    if runner is None or not runner.which("iptables"):
        return []
    res = runner.run(["iptables", "-t", "nat", "-S", "PREROUTING"], timeout=15)
    if not res.ok:
        return []
    rows = []
    seen = set()
    for r in ipt.parse_S(res.out, "nat").get("PREROUTING", []):
        m = ipt.LABEL_RE.match(r.comment or "")
        if r.target != "DNAT" or not m or not r.dport or not str(r.dport).isdigit():
            continue
        dest = ipt.parse_to_destination(r.to_destination, int(r.dport))
        proto = r.proto or m.group(2)
        key = (proto, int(r.dport))
        if dest is None or key in seen or proto not in ("tcp", "udp"):
            continue
        seen.add(key)
        rows.append({"proto": proto, "port": int(r.dport), "to": dest[0], "to_port": dest[1], "name": ""})
    return sorted(rows, key=lambda f: (f["port"], f["proto"]))


def targets(rows):
    """Distinct target addresses in the order of the forwards (get_target_ips of 2.x sorts them)."""
    out = []
    for f in rows:
        if f["to"] not in out:
            out.append(f["to"])
    return sorted(out, key=lambda ip: tuple(int(x) for x in ip.split(".")))


def port_for(rows, ip):
    """(to_port, proto) of the first forward to ``ip`` (get_port_for_ip / get_proto_for_ip of 2.x), or (None, None)."""
    for f in rows:
        if f["to"] == ip:
            return f["to_port"], f["proto"]
    return None, None


def forward_key(f):
    return "%s:%d" % (f["proto"], int(f["port"]))


def target_ok(ip):
    """Why a forward to ``ip`` is impossible ("" — it is fine): the core's own check."""
    try:
        kstate.parse_target_ip(ip)
        return ""
    except Exception as e:                          # noqa: BLE001 — StateError with the Russian text
        return str(e)


def sort_ips(ips):
    def key(ip):
        try:
            return (0, int(ipaddress.IPv4Address(ip)))
        except ValueError:
            return (1, 0)
    return sorted(ips, key=key)

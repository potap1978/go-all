"""Stand-in of kaskad/gw/model.py (WP4): devices of an inbound and the route of a device (SPEC §2)."""

XRAY_KINDS = ("socks5", "vless")       # kaskad.rules.apply reads it at import (VLESS: the per-user chains of wave 3)


def devices_for(st, kind):
    return [d for d in (st.get("gateway") or {}).get("devices") or [] if kind in (d.get("access") or [])]


def route_of(st, device):
    gw = st.get("gateway") or {}
    route = device.get("route")
    if not route:
        for u in gw.get("users") or []:
            if u.get("id") == device.get("user"):
                route = u.get("route")
    if not route:
        route = (gw.get("routes") or {}).get("default") or {"via": "pool", "fail": "block"}
    if isinstance(route, str):
        route = {"via": route, "fail": "block"}
    return route

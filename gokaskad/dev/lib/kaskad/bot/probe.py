"""Checks of a target server: GeoIP and pings (smart_ping / probe_server_tg of 2.x).

* ICMP — ``ping -c 1 -W <s> <ip>`` (argv, no shell) through the runner; the time from ``time=12.3 ms``;
* TCP — a connect to the forward's port (only for TCP: UDP services answer nothing), the time of the connect;
* GeoIP — ``http://ip-api.com/json/<ip>`` (plain HTTP, 5 s, directly — as 2.x curl did).

``smart(ip, port, proto)`` -> ("ICMP", ms) | ("TCP:443", ms) | None.
``server(ip, port, proto)`` -> {"geo", "pings": [(method, ms) | None] ×3, "avg", "lost"} — 1 s between pings.
"""

import json
import re
import socket
import time

from ..net import socks
from .data import valid_ip

GEOIP_URL = "http://ip-api.com/json/%s?fields=status,country,regionName,city,isp,org"
GEOIP_TIMEOUT = 5
_TIME = re.compile(r"time[=<]\s*([0-9]+(?:\.[0-9]+)?)\s*ms")


def fmt_ms(ms):
    if ms is None:
        return "—"
    if ms < 10:
        return "%.2f" % ms
    if ms < 100:
        return "%.1f" % ms
    return "%.0f" % ms


def _tcp_connect(ip, port, timeout):
    t0 = time.monotonic()
    s = socket.create_connection((ip, int(port)), timeout=timeout)
    try:
        return (time.monotonic() - t0) * 1000.0
    finally:
        s.close()


class Prober(object):
    def __init__(self, runner, tcp_connect=None, geoip_url=None, sleep=None):
        self.runner = runner
        self.tcp_connect = tcp_connect or _tcp_connect
        self.geoip_url = geoip_url or GEOIP_URL
        self.sleep = sleep or time.sleep

    def icmp(self, ip, timeout=3):
        if not valid_ip(ip):
            return None
        res = self.runner.run(["ping", "-c", "1", "-W", str(int(timeout)), ip], timeout=int(timeout) + 3)
        if not res.ok:
            return None
        m = _TIME.search(res.out or "")
        return float(m.group(1)) if m else None

    def tcp(self, ip, port, timeout=3):
        try:
            ms = self.tcp_connect(ip, int(port), timeout)
        except (OSError, ValueError):
            return None
        return ms if ms is not None and ms > 0 else None

    def smart(self, ip, timeout=3, port=None, proto=None):
        ms = self.icmp(ip, timeout)
        if ms is not None:
            return ("ICMP", ms)
        if proto == "udp" or not port:
            return None
        ms = self.tcp(ip, port, timeout)
        if ms is not None:
            return ("TCP:%d" % int(port), ms)
        return None

    def geoip(self, ip):
        """{"country", "city", "region", "isp", "org"} or None (no answer, «fail», not JSON)."""
        if not valid_ip(ip):
            return None
        try:
            resp = socks.request(self.geoip_url % ip, "GET", timeout=GEOIP_TIMEOUT, limit=65536)
            doc = json.loads(resp.body.decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        if not isinstance(doc, dict) or doc.get("status") != "success":
            return None
        return {"country": str(doc.get("country") or ""), "city": str(doc.get("city") or ""),
                "region": str(doc.get("regionName") or ""), "isp": str(doc.get("isp") or ""),
                "org": str(doc.get("org") or "")}

    def server(self, ip, port=None, proto=None, count=3):
        geo = self.geoip(ip)
        pings = []
        for n in range(count):
            pings.append(self.smart(ip, 3, port, proto))
            if n < count - 1:
                self.sleep(1)
        got = [p[1] for p in pings if p is not None]
        return {"geo": geo, "pings": pings, "avg": (sum(got) / len(got)) if got else None,
                "lost": sum(1 for p in pings if p is None)}

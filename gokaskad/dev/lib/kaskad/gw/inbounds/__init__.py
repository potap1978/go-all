"""Adapters of the gateway's inbounds. Importing this package imports every adapter module that exists;
a module that is not written yet (work packages 5 and 6) is skipped — ModuleNotFoundError naming exactly
that module — while a broken import INSIDE an existing module surfaces (the same rule as kaskad/cli.py).

    wg.py        WireGuard (wg)                         work package 5
    awg.py       AmneziaWG awg1 / awg2 / awg3           work package 5
    openvpn.py   OpenVPN udp + tcp                      work package 5
    ocserv.py    OpenConnect                            work package 5
    socks5.py    SOCKS5 (an inbound of kaskad-gw-xray)  work package 5
    vless.py     VLESS Reality (an inbound of kaskad-gw-xray, docs/k3/SPEC-vless-inbound.md)
    accel.py     accel-ppp build and service            work package 6
    l2tp.py, pptp.py, sstp.py, ikev2.py                 work package 6

Each module calls ``kaskad.gw.registry.register(<class>)`` at import time. The interface is
``kaskad.gw.inbounds.base.Inbound``.
"""

import importlib

from .. import registry

MODULES = ("wg", "awg", "openvpn", "ocserv", "socks5", "vless", "hy2", "accel", "l2tp", "pptp", "sstp", "ikev2")


def load_all():
    loaded = []
    was = registry._state["loading"]
    registry._state["loading"] = True          # what registers now is "builtin" (registry.reset keeps it)
    try:
        for name in MODULES:
            modname = "%s.%s" % (__name__, name)
            try:
                importlib.import_module(modname)
            except ModuleNotFoundError as e:
                if e.name != modname:
                    raise
                continue
            loaded.append(name)
    finally:
        registry._state["loading"] = was
    return loaded


LOADED = load_all()

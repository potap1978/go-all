"""Minimal stand-ins of the gateway core (work package 4) for the tests of the tunnel inbounds (WP5).

Only the surface the adapters use (docs/k3/SPEC-wave2-gateway.md §7, reached through
kaskad/gw/inbounds/tunnel.py):

    kaskad.gw.inbounds.base   Inbound, InboundError
    kaskad.gw.registry        register(cls)  (+ get, kinds for the tests)
    kaskad.secrets            set(key, value), new_password(n=14)  (+ load, get, delete, new_psk)
    kaskad.gw.addr            subnet(kind), allocate(st, device, kind)  (+ server_ip, release)
    kaskad.gw.model           devices_for(st, kind), route_of(st, device)
    kaskad.gw.sys             apt_install(runner, pkgs), write_unit(name, text), modprobe(runner, mods),
                              ufw_allow(runner, port, proto, comment), ufw_delete(runner, port, proto, comment)

``Stubbed`` puts fresh copies of these modules into ``sys.modules`` under the real names (and package
stand-ins for ``kaskad.gw`` / ``kaskad.gw.inbounds`` whose ``__path__`` is the real directory, so the
adapter files load from lib/ while the core's own ``__init__`` never runs), imports the adapters
against them, and on exit restores ``sys.modules`` and the attributes of ``kaskad`` exactly — the
real core modules of WP4 (once merged) are not touched by these tests and see no stub afterwards.
"""

import importlib
import importlib.machinery
import importlib.util
import os
import sys
import types

import kaskad

HERE = os.path.dirname(os.path.abspath(__file__))
LIB_GW = os.path.join(os.path.dirname(os.path.abspath(kaskad.__file__)), "gw")

STUBS = (                      # base first: the other stand-ins import InboundError from it
    ("kaskad.gw.inbounds.base", "stub_base.py"),
    ("kaskad.gw.registry", "stub_registry.py"),
    ("kaskad.secrets", "stub_secrets.py"),
    ("kaskad.gw.addr", "stub_addr.py"),
    ("kaskad.gw.model", "stub_model.py"),
    ("kaskad.gw.sys", "stub_sys.py"),
)
PACKAGES = {"kaskad.gw": LIB_GW, "kaskad.gw.inbounds": os.path.join(LIB_GW, "inbounds")}
ADAPTERS = ("wg", "awg", "openvpn", "ocserv", "socks5", "vless")
# work package 6: the engines (accel-ppp, strongSwan) and the PPP/IPsec adapters — Stubbed(PPP_ADAPTERS)
PPP_ADAPTERS = ("accel", "ipsec", "l2tp", "pptp", "sstp", "ikev2")


def _ours(name):
    return name == "kaskad.secrets" or name == "kaskad.gw" or name.startswith("kaskad.gw.")


def _package(name, path):
    mod = types.ModuleType(name)
    mod.__path__ = [path]
    mod.__package__ = name
    mod.__spec__ = importlib.machinery.ModuleSpec(name, None, is_package=True)
    mod.__spec__.submodule_search_locations = [path]
    return mod


def _stub(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class Stubbed(object):
    """``with Stubbed() as gw:`` — gw.wg, gw.awg, gw.openvpn, gw.ocserv, gw.socks5, gw.tunnel, gw.pki,
    gw.issue_os are the adapter modules; gw.secrets, gw.registry, gw.addr, gw.model, gw.sys, gw.base
    the stand-ins. ``Stubbed(PPP_ADAPTERS)`` loads the modules of work package 6 instead (gw.accel,
    gw.ipsec, gw.l2tp, gw.pptp, gw.sstp, gw.ikev2)."""

    def __init__(self, adapters=ADAPTERS):
        self.adapters = tuple(adapters)

    def __enter__(self):
        self._saved = {k: v for k, v in sys.modules.items() if _ours(k)}
        self._attrs = {a: getattr(kaskad, a) for a in ("gw", "secrets") if hasattr(kaskad, a)}
        for k in list(self._saved):
            del sys.modules[k]
        for name, path in PACKAGES.items():
            sys.modules[name] = _package(name, path)
        setattr(kaskad, "gw", sys.modules["kaskad.gw"])
        sys.modules["kaskad.gw"].inbounds = sys.modules["kaskad.gw.inbounds"]
        for name, filename in STUBS:
            mod = _stub(name, filename)
            parent, _, leaf = name.rpartition(".")
            setattr(sys.modules[parent], leaf, mod)
        self.secrets = sys.modules["kaskad.secrets"]
        self.registry = sys.modules["kaskad.gw.registry"]
        self.addr = sys.modules["kaskad.gw.addr"]
        self.model = sys.modules["kaskad.gw.model"]
        self.sys = sys.modules["kaskad.gw.sys"]
        self.base = sys.modules["kaskad.gw.inbounds.base"]
        self.tunnel = importlib.import_module("kaskad.gw.inbounds.tunnel")
        self.pki = importlib.import_module("kaskad.gw.inbounds.pki")
        self.issue_os = importlib.import_module("kaskad.gw.issue_os")
        for a in self.adapters:
            setattr(self, a, importlib.import_module("kaskad.gw.inbounds." + a))
        return self

    def __exit__(self, *exc):
        for k in [k for k in sys.modules if _ours(k)]:
            del sys.modules[k]
        sys.modules.update(self._saved)
        for a in ("gw", "secrets"):
            if a in self._attrs:
                setattr(kaskad, a, self._attrs[a])
            elif hasattr(kaskad, a):
                delattr(kaskad, a)
        return False

    def adapter(self, kind):
        return self.registry.get(kind)()

"""Registry of the inbound adapters (spec §7).

    register(cls_or_instance)   adapters call it at import time (a class is instantiated without arguments)
    get(kind)                   the adapter instance or None
    title(kind), titles(kinds)  the name(s) of protocols for people (AmneziaWG 2.0, not awg2)
    kinds()                     registered kinds in the order of model.INBOUND_KINDS
    load()                      import ``kaskad.gw.inbounds`` (it imports every adapter module that exists)

Tests register ``DummyInbound(kind=...)`` instances over the real ones and call ``reset()`` afterwards:
it drops test registrations and brings back what the adapter modules registered at import time.
"""

import importlib

_ADAPTERS = {}
_BUILTIN = {}               # registered while kaskad.gw.inbounds was being imported
_state = {"loaded": False, "loading": False}


def register(obj):
    """Register an adapter class (instantiated here) or instance under ``obj.kind``. Returns ``obj``, so it
    works as a class decorator. A second registration of the same kind replaces the first."""
    inst = obj() if isinstance(obj, type) else obj
    kind = getattr(inst, "kind", "")
    if not kind:
        raise ValueError("adapter without kind: %r" % (obj,))
    _ADAPTERS[kind] = inst
    if _state["loading"]:
        _BUILTIN[kind] = inst
    return obj


def unregister(kind):
    return _ADAPTERS.pop(kind, None)


def reset():
    """Back to the adapters of the modules (test registrations dropped)."""
    load()
    _ADAPTERS.clear()
    _ADAPTERS.update(_BUILTIN)


def load():
    """Import the adapters package once (adapter modules register themselves)."""
    if _state["loaded"]:
        return
    _state["loading"] = True
    try:
        importlib.import_module("kaskad.gw.inbounds")
    finally:
        _state["loading"] = False
    _state["loaded"] = True


def get(kind):
    load()
    return _ADAPTERS.get(kind)


def title(kind):
    """The name of a protocol for people — «AmneziaWG 2.0», «WireGuard», «L2TP/IPsec»: the adapter's title, else the
    table of issue_os (a kind this version has no adapter for), else the code itself (an unknown kind). Texts for a
    person (panel, SSH menu, bot, warnings, errors) name protocols by this, never by the code (awg2, wg, l2tp) —
    the code is for the console (kaskadctl gw …), the journal and the API (I18N-3.3.2)."""
    ad = get(kind)
    t = getattr(ad, "title", "") if ad is not None else ""
    if not t:
        from .issue_os import TITLES
        t = TITLES.get(kind, "")
    return t or str(kind)


def titles(kinds_):
    """[title(k) for k in kinds_] — a list of protocols for people."""
    return [title(k) for k in kinds_ or []]


def kinds():
    from .model import INBOUND_KINDS
    load()
    known = [k for k in INBOUND_KINDS if k in _ADAPTERS]
    return known + sorted(k for k in _ADAPTERS if k not in INBOUND_KINDS)


def all_adapters():
    """{kind: adapter} of everything registered."""
    load()
    return dict(_ADAPTERS)

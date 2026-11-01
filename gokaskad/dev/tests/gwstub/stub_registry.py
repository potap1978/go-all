"""Stand-in of kaskad/gw/registry.py (WP4)."""

_REGISTRY = {}


def register(cls):
    _REGISTRY[cls.kind] = cls
    return cls


def get(kind):
    return _REGISTRY[kind]


def kinds():
    return sorted(_REGISTRY)

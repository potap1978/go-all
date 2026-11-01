"""Stand-in of kaskad/secrets.py (WP4): an in-memory store the tests seed and inspect.

``DATA`` is the store, ``SETS`` the keys written through set() in order. new_password() is
deterministic (golden outputs); the real one is random letters and digits without look-alikes.
"""

DATA = {}
SETS = []
_count = [0]


def load():
    return dict(DATA)


def get(key):
    return DATA.get(key)


def set(key, value):            # noqa: A001 — the contract name
    DATA[key] = value
    SETS.append(key)


def delete(key):
    DATA.pop(key, None)


def new_password(n=14):
    _count[0] += 1
    return ("Pw%d" % _count[0]).ljust(n, "q")


def new_psk():
    _count[0] += 1
    return "psk%d" % _count[0]

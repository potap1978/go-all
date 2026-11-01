"""Words of the inbound adapters that other modules read as constants (3.3.2, k3/handoff/I18N-3.3.2.md): a constant
computed at import would freeze one language — these call tr() at every read. Not in base.py: the tests of the
adapters replace base.py with a stand-in (tests/gwstub), this module loads as it is."""

from collections.abc import Mapping

from kaskad.i18n import tr


class TrMap(Mapping):
    """{code: catalog key} read as {code: text in the language of the moment}: a module constant of words that other
    modules import (``tunnel.RESTART_WHY``, ``vless_lists.TITLES``) — tr() at every lookup, never at import."""

    def __init__(self, keys):
        self._keys = dict(keys)

    def __getitem__(self, code):
        return tr(self._keys[code])

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)


def lazy_texts(modname, texts):
    """A module ``__getattr__`` for text constants other modules (and tests) read as ``module.NAME``: {NAME: key} or
    {NAME: (key, kw)} -> tr() at every read, never at import. Inside the module call tr() itself (a global name does
    not go through ``__getattr__``)."""
    def __getattr__(name):
        if name in texts:
            key, kw = texts[name] if isinstance(texts[name], tuple) else (texts[name], None)
            return tr(key, **kw) if kw else tr(key)
        raise AttributeError("module %r has no attribute %r" % (modname, name))
    return __getattr__

"""A thin layer over commands other branches bring (owner 2026-10-09: the subscription refresh of exits and the backup
file are made in the branch k3-feat, docs/k3/SPEC-subs-backup.md):

    kaskadctl gw exit-sub add|list|del|refresh --json      the address of a subscription on stdin
    kaskadctl backup export|import                         (the steps call kaskad.backup in process, as the panel)

The quick setup calls the subscription as a command (the address on stdin, never argv). Until a command exists in
this kaskadctl, ``has()`` says so and the step answers «появится в этой версии» — never a failure.
"""

import json

from .. import paths, util
from ..errors import KaskadError
from ..i18n import tr

SOON = "setup.features.soon"            # a key of the catalog: soon() gives the words


def soon():
    """«Появится в этой версии…» in words of the current language."""
    return tr(SOON)


def _choices(parser):
    for act in getattr(parser, "_actions", []):
        ch = getattr(act, "choices", None)
        if isinstance(ch, dict):
            return ch
    return {}


def has(*words):
    """Does ``kaskadctl <words…>`` exist here (parsed, nothing run)?"""
    try:
        from .. import cli
        p = cli.build_parser()
    except (ImportError, SystemExit, KaskadError):
        return False
    for w in words:
        ch = _choices(p)
        if w not in ch:
            return False
        p = ch[w]
    return True


def call(runner, words, stdin=None, timeout=600):
    """``kaskadctl <words…> --json`` with ``stdin`` -> its JSON (a failure: KaskadError with its text)."""
    if not has(*words):
        raise KaskadError(soon(), data={"reason": "soon"})
    res = runner.run([paths.kaskadctl()] + list(words) + ["--json"], input=stdin, timeout=timeout)
    try:
        doc = json.loads(res.out) if res.out.strip() else {}
    except ValueError:
        doc = {}
    if not res.ok:
        raise KaskadError(util.redact(str(doc.get("error") or (res.err or "").strip()[:300] or
                                          tr("setup.features.exitCode", rc=res.rc))),
                          code=res.rc if 0 < res.rc < 10 else None, data=doc if isinstance(doc, dict) else None)
    return doc if isinstance(doc, dict) else {"ok": True}

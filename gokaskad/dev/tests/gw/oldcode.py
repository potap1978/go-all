"""A module of an earlier release, read with ``git show`` (PLAN-order §3.4: «старая читает новое»): the code of
``lib/kaskad/gw/<name>.py`` at ``ref`` run as a module of the package ``kaskad.gw`` — its relative imports take the
modules of today (``assign`` of 3.3.7.12 with ``policy`` and ``exits`` of now). None — no git, no repository or no such
commit here (a copy of the tree without history): the test is skipped, never failed."""

import os
import subprocess
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REL_3_3_7_12 = "8582b624"


def load(name, ref=REL_3_3_7_12):
    try:
        res = subprocess.run(["git", "-C", ROOT, "show", "%s:lib/kaskad/gw/%s.py" % (ref, name)],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0 or not res.stdout:
        return None
    mod = types.ModuleType("kaskad.gw._old_%s_%s" % (name, ref))
    mod.__package__ = "kaskad.gw"
    mod.__file__ = "<%s:%s.py>" % (ref, name)
    exec(compile(res.stdout, mod.__file__, "exec"), mod.__dict__)
    return mod

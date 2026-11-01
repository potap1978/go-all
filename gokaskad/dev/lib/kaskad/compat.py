"""One iptables rule that lets Kaskad's forwarded connections through foreign FORWARD policies
(work package 1 implements this file).

Why: UFW (DEFAULT_FORWARD_POLICY="DROP") and Docker (FORWARD policy DROP) drop forwarded packets
in the iptables FORWARD chain, and a drop in ANY base chain of the hook wins over an accept in
our nft table. Instead of 2.x's two FORWARD rules per forward we keep exactly one rule::

    iptables -I FORWARD 1 -m connmark --mark 0x4b000000/0xff000000 \
             -m comment --comment kaskad:compat -j ACCEPT

It matches the ct mark set by ``fwd_dnat`` (see nft.py). The rule is never persisted by Kaskad;
kaskad-net.service and the watch timer re-add it (iptables-restore at boot, ``ufw reload``,
``iptables -F`` from the free script can drop it).

No ``iptables`` binary means no UFW and no Docker on iptables, so nothing to let through:
every function then does nothing (spec §3.3) — ensure() returns False instead of raising.
All calls use ``-w 5`` (wait for the xtables lock that Docker/UFW may hold).

The gateway (wave 2, spec §1.2) needs the same rule on INPUT, ``kaskad:compat-in``: connections that
TPROXY hands to xray (path 0x200) and the clients' DNS to 10.77.0.1 end in INPUT, where UFW's policy
is DROP too; gw_pre gives them the same ct mark. ``ensure_in``/``present_in``/``remove_in`` — only the
gateway calls them (enable, watchdog, disable).

CONTRACT — keep the signatures.
"""

from . import state
from .errors import KaskadError
from .i18n import tr

COMMENT = "kaskad:compat"
COMMENT_IN = "kaskad:compat-in"
IPTABLES = "iptables"
WAIT = ["-w", "5"]


def _args(chain, comment):
    return [chain, "-m", "connmark", "--mark", "0x%08x/0x%08x" % (state.MARK, state.MARK_MASK),
            "-m", "comment", "--comment", comment, "-j", "ACCEPT"]


def rule_args():
    """argv tail after ``iptables`` for -C / -D, e.g. ["FORWARD", "-m", "connmark", ...]."""
    return _args("FORWARD", COMMENT)


def rule_args_in():
    """The INPUT twin for the gateway: ["INPUT", "-m", "connmark", ..., "kaskad:compat-in", ...]."""
    return _args("INPUT", COMMENT_IN)


def available(runner):
    """True if there is an iptables binary at all (otherwise the rule is not needed)."""
    return bool(runner.which(IPTABLES))


def _present(runner, args):
    if not available(runner):
        return False
    return runner.run([IPTABLES] + WAIT + ["-C"] + args).ok


def _ensure(runner, args, comment):
    if not available(runner) or _present(runner, args):
        return False
    res = runner.run([IPTABLES] + WAIT + ["-I", args[0], "1"] + args[1:])
    if not res.ok:
        detail = (res.err or res.out or "").strip().splitlines()
        raise KaskadError(tr("compat.refused", comment=comment,
                             detail=detail[0] if detail else tr("compat.rc", rc=res.rc)))
    return True


def _remove(runner, args):
    if not available(runner):
        return 0
    n = 0
    for _ in range(100):
        if not runner.run([IPTABLES] + WAIT + ["-D"] + args).ok:
            break
        n += 1
    return n


def present(runner):
    """True if the rule is in FORWARD (``iptables -C``). False without iptables."""
    return _present(runner, rule_args())


def ensure(runner):
    """Insert at position 1 if missing. Returns True if it was added, False if already there
    (or there is no iptables binary — then the rule is not needed). Raises KaskadError if
    iptables refuses the rule."""
    return _ensure(runner, rule_args(), COMMENT)


def remove(runner):
    """Delete every copy of the rule. Returns how many were removed. Never fails if absent."""
    return _remove(runner, rule_args())


def present_in(runner):
    """True if ``kaskad:compat-in`` is in INPUT. False without iptables."""
    return _present(runner, rule_args_in())


def ensure_in(runner):
    """Insert ``kaskad:compat-in`` at INPUT position 1 if missing (same return/raise as ensure)."""
    return _ensure(runner, rule_args_in(), COMMENT_IN)


def remove_in(runner):
    """Delete every copy of ``kaskad:compat-in``. Returns how many were removed."""
    return _remove(runner, rule_args_in())

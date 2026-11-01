"""Exit codes and exceptions shared by every kaskadctl command (see docs/k3/SPEC-wave1.md, «Коды выхода»)."""

OK = 0
ERROR = 1            # failed, nothing changed (or changes reverted before returning)
USAGE = 2            # bad arguments
CONFLICT = 3         # refused: port conflict / unmanaged state; retry with --force where allowed
ROLLED_BACK = 4      # migration verify failed, the previous state was restored
UNSUPPORTED = 5      # engine nft unavailable here, or command needs engine nft while engine is legacy
LOCKED = 6           # another kaskadctl holds the state lock


class KaskadError(Exception):
    """Error with a user-facing Russian message and an exit code."""

    code = ERROR

    def __init__(self, message, code=None, data=None):
        super().__init__(message)
        if code is not None:
            self.code = code
        self.data = data or {}


class StateError(KaskadError):
    """state.json is missing required fields, has an unknown schema or is corrupted."""


class LockedError(KaskadError):
    code = LOCKED


class NestedLockError(LockedError):
    """The state lock taken again by the thread that holds it (``state.locked`` inside ``state.transaction``) — an error
    of the code, not «busy»: told at once (no 15 s wait), the code ERROR, not LOCKED — a «retry» would only repeat it
    (SPEC-stage1-exits §3.1 «Счёт — два входа»). Handlers of «busy» still catch it as a LockedError."""
    code = ERROR


class ConflictError(KaskadError):
    code = CONFLICT


class UnsupportedError(KaskadError):
    code = UNSUPPORTED

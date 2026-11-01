"""One count at a time for a read of the panel that runs commands (``Route.runs``) — the way of
``kaskad.panel.host._price_once`` (REV-3377-stand-fix 1), by a key: a request while the count of the same key runs waits
for that count and gets its answer (or its error) — no second round of the same commands; a count of another key waits
its turn (the commands of two counts never run side by side); the next request after a count counts anew (the state may
have changed since). The counts end by the timeouts of their commands, so a waiter does not wait for ever."""

import threading


class Once(object):
    def __init__(self):
        self._lock = threading.Lock()                # the table of the counts under way
        self._turn = threading.Lock()                # one count runs at a time
        self.flight = {}                             # key -> {"done", "out", "err", "n"} (tests read "n")

    def __call__(self, key, fn):
        with self._lock:
            f = self.flight.get(key)
            lead = f is None
            if lead:
                f = self.flight[key] = {"done": threading.Event(), "out": None, "err": None, "n": 0}
            f["n"] += 1
        if lead:
            try:
                with self._turn:
                    f["out"] = fn()
            except BaseException as e:               # noqa: BLE001 — the waiters get the same error
                f["err"] = e
            finally:
                with self._lock:
                    self.flight.pop(key, None)
                f["done"].set()
        else:
            f["done"].wait()
        if f["err"] is not None:
            raise f["err"]
        return f["out"]

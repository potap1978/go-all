"""A small pool of worker threads for the bot (docs/k3/SPEC-wave3.md §4.4).

* lanes — every update of a chat is one item of that chat's lane; a lane runs one item at a time, in order
  (a dialog never sees two of its messages at once), different chats run in parallel; after each item the lane
  goes to the back of the queue (one busy chat does not starve the others);
* jobs — long actions (ping 10×/60 s, issuing settings): at most one per chat at a time (``start_job`` says
  which one is running instead), they do not hold the chat's lane, so the menu stays responsive meanwhile.

The receiver never waits for a job; it waits for the lane items of its batch (``Batch``) before it saves
the offset. An exception of an item is handed to ``on_error`` and the worker goes on.
"""

import collections
import queue
import threading


class Batch(object):
    """Counts the lane items of one getUpdates answer; ``wait`` until all of them ran (or the timeout)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._left = 0
        self._done = threading.Event()
        self._done.set()

    def add(self):
        with self._lock:
            self._left += 1
            self._done.clear()

    def done(self):
        with self._lock:
            self._left -= 1
            if self._left <= 0:
                self._left = 0
                self._done.set()

    def wait(self, timeout=None):
        return self._done.wait(timeout)


class Scheduler(object):
    def __init__(self, workers=4, on_error=None):
        self.workers = workers
        self._on_error = on_error
        self._q = queue.Queue()
        self._lock = threading.Lock()
        self._lanes = {}               # chat -> deque of callables
        self._busy = set()             # chats whose lane is queued or running
        self._jobs = {}                # chat -> name of the running job
        self._threads = []
        self._idle = threading.Condition(self._lock)
        self._running = 0

    def start(self):
        for i in range(self.workers):
            t = threading.Thread(target=self._worker, name="kaskad-bot-%d" % i)
            t.daemon = True
            t.start()
            self._threads.append(t)

    def submit(self, chat, fn, batch=None):
        """Add ``fn`` to the lane of ``chat``."""
        if batch is not None:
            batch.add()
            item = _Tracked(fn, batch)
        else:
            item = fn
        with self._lock:
            self._lanes.setdefault(chat, collections.deque()).append(item)
            if chat not in self._busy:
                self._busy.add(chat)
                self._q.put(("lane", chat, None))

    def start_job(self, chat, name, fn):
        """Run ``fn`` as the chat's job. Returns None when started, else the name of the job already running."""
        with self._lock:
            if chat in self._jobs:
                return self._jobs[chat]
            self._jobs[chat] = name
            self._q.put(("job", chat, fn))
        return None

    def job(self, chat):
        with self._lock:
            return self._jobs.get(chat)

    def wait_idle(self, timeout=10.0):
        """For tests and shutdown: no lane item and no job is queued or running."""
        with self._idle:
            return self._idle.wait_for(lambda: not self._busy and not self._jobs and self._running == 0, timeout)

    def stop(self, timeout=5.0):
        for _ in self._threads:
            self._q.put(None)
        for t in self._threads:
            t.join(timeout)
        self._threads = []

    def _run(self, fn):
        try:
            fn()
        except BaseException as e:                 # noqa: BLE001 — one broken handler must not kill a worker
            if self._on_error is not None:
                try:
                    self._on_error(e)
                except BaseException:              # noqa: BLE001
                    pass

    def _worker(self):
        while True:
            unit = self._q.get()
            if unit is None:
                return
            kind, chat, fn = unit
            with self._lock:
                self._running += 1
            try:
                if kind == "lane":
                    with self._lock:
                        lane = self._lanes.get(chat)
                        item = lane.popleft() if lane else None
                    if item is not None:
                        self._run(item)
                    with self._lock:
                        lane = self._lanes.get(chat)
                        if lane:
                            self._q.put(("lane", chat, None))
                        else:
                            self._lanes.pop(chat, None)
                            self._busy.discard(chat)
                else:
                    try:
                        self._run(fn)
                    finally:
                        with self._lock:
                            self._jobs.pop(chat, None)
            finally:
                with self._lock:
                    self._running -= 1
                    self._idle.notify_all()


class _Tracked(object):
    __slots__ = ("fn", "batch")

    def __init__(self, fn, batch):
        self.fn = fn
        self.batch = batch

    def __call__(self):
        try:
            self.fn()
        finally:
            self.batch.done()

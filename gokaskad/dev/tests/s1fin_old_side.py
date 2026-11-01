"""А4 (dev-s1fin), the migration (the owner's rule 24.10): the side of the RELEASED 3.3.7.10. ``tests/test_s1fin_migration.py``
copies this file (and ``tests/test_n2_undo_site.py`` for its world ``_UndoCase``) into the tree of 44cee282 and runs it
there (``S1FIN_MODE``, ``S1FIN_IO``, ``S1FIN_CASE``):

* ``make`` — the data 3.3.7.10 itself writes, the whole fake root of each world copied out:
  ``policy`` — the same key in upstream.txt and upstream-ru.txt and a Russian group «Дом»: its collector (``_place``)
  puts the repeat «e-…-201» into «Дом» and every write of the policy is refused (Н-3);
  ``snap`` — a node with the site, its certificate and VLESS on TLS imports a copy of another server with another domain:
  the snapshot for «Вернуть как было» (/var/backups/kaskad) and the node after the import (Н-2);
  ``live`` — rounds of its collector with a device online and an exit with traffic: live.json and metrics.db (the
  automatic refresh, 3.3.7.11, reads them after the update);
* ``read`` — the rollback: 3.3.7.10 on the root the new version left (one case, ``S1FIN_CASE``) — what it says, for
  the new side to judge.

Only what 3.3.7.10 has is used. Not a test of this tree: no ``test_`` in the name, every case is skipped without
``S1FIN_MODE``."""

import json
import os
import shutil
import unittest
from unittest import mock

from kaskad import paths, snapshot, state
from kaskad.gw import collect, exits, live, metrics, policy, policy_ops

MODE = os.environ.get("S1FIN_MODE")
IO = os.environ.get("S1FIN_IO", "")
CASE = os.environ.get("S1FIN_CASE")


def put(name, obj, io=None):
    with open(os.path.join(io or IO, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=True)


def get(name, io=None):
    with open(os.path.join(io or IO, name), encoding="utf-8") as f:
        return json.load(f)


def save_root(name, io=None):
    """The fake root of the world (KASKAD_ROOT) — every file the version wrote — into ``<io>/<name>``."""
    dst = os.path.join(io or IO, name)
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(paths.root(), dst, symlinks=True)


def load_root(name, io=None):
    """The root of the world replaced by ``<io>/<name>``: the files are the other version's, the fakes this world's."""
    root = paths.root()
    for n in os.listdir(root):
        p = os.path.join(root, n)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
    shutil.copytree(os.path.join(io or IO, name), root, symlinks=True, dirs_exist_ok=True)
    snapshot.forget()


def need(mode, case):
    if MODE != mode or (mode == "read" and CASE != case):
        raise unittest.SkipTest("the old side of tests/test_s1fin_migration.py (%s %s)" % (mode, case))


def repeats(doc):
    return sorted(e for g in doc.get("groups") or [] for t in g.get("tiers") or [] for e in t.get("exits") or []
                  if "-" in e[2:])


# ── policy: the repeat «e-…-201» (Н-3) ──
from tests.fakes_gw import VLESS_A, VLESS_B, write_pool, write_ru_pool      # noqa: E402
from tests.gw.test_s1_ru_pool_ops import HOME, RuCase                       # noqa: E402


def _place(runner):
    with state.locked():
        st = state.load()
        return collect._place(st, exits.pool(runner, roles=exits.ROLES))


class PolicyMake(RuCase):
    def setUp(self):
        need("make", "policy")
        super().setUp()

    def test_make(self):
        write_pool([VLESS_A, VLESS_B])
        write_ru_pool([VLESS_A])
        _place(self.w.runner)
        stored = state.load()["gateway"]["exit_policy"]
        try:
            policy_ops.exit_move(self.w.runner, exits.exit_id(VLESS_B), policy.MAIN)
            err = None
        except Exception as e:                                       # noqa: BLE001 — what it says is the fact
            err = str(e)
        put("policy.json", {"home": repeats(dict(groups=[policy.group(stored, HOME)])), "write_error": err})
        save_root("policy-root")


class PolicyRead(RuCase):
    def setUp(self):
        need("read", "policy")
        super().setUp()

    def test_read(self):
        load_root("root")
        view = policy.load(state.load())
        a, b = exits.exit_id(VLESS_A), exits.exit_id(VLESS_B)
        res = policy_ops.exit_move(self.w.runner, b, policy.MAIN)
        put("read.json", {"view_repeats": repeats(view), "a_group": (policy.group_of(view, a) or {}).get("id"),
                          "write_ok": bool(res.get("ok")), "ru": self.ru(),
                          "placed_again": [list(x) for x in _place(self.w.runner)],     # its own collector after
                          "stored_repeats": repeats(state.load()["gateway"]["exit_policy"])})


# ── the snapshot for «Вернуть как было» (Н-2) ──
from tests.test_n2_undo_site import _UndoCase                               # noqa: E402 — the world of Н-2, copied in
from kaskad import backup                                                   # noqa: E402
from kaskad.site import model as smodel                                     # noqa: E402


class SnapMake(_UndoCase):
    def setUp(self):
        need("make", "snap")
        super().setUp()

    def test_make(self):
        backup.apply(self.w.runner, self.other_copy())
        s = smodel.peek(state.load())
        put("snap.json", {"before": self.before,                       # the links before the import (setUp)
                          "after_import": [s["domain"], s["mode"], s["cert"], self.rec()["security"]],
                          "snapshot": backup.last_snapshot() and os.path.relpath(backup.last_snapshot(), paths.root())})
        save_root("snap-root")


class SnapRead(_UndoCase):
    def setUp(self):
        need("read", "snap")
        super().setUp()

    def test_read(self):
        load_root("root")
        s = smodel.peek(state.load())
        snaps = sorted(os.listdir(paths.backups()))
        read = [n for n in snaps if backup.read_snapshot(os.path.join(paths.backups(), n))]
        put("read.json", {"site": [s["domain"], s["mode"], s["cert"]], "vless": [self.rec()["security"],
                          self.rec()["port"]], "links": self.link_fields(), "snapshots": snaps, "snapshots_read": read})


# ── the collector: live.json and metrics.db (the refresh ≤ 5 s) ──
from tests.gw.test_collect_activity import ActWorld                         # noqa: E402


class _LiveWorld(ActWorld):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(collect, "_clock_synced", lambda runner: True)
        p.start()
        self.addCleanup(p.stop)

    def counters(self, rx, tx):
        self.write("/sys/class/net/kawg2/statistics/rx_bytes", "%d\n" % rx)
        self.write("/sys/class/net/kawg2/statistics/tx_bytes", "%d\n" % tx)


class LiveMake(_LiveWorld):
    def setUp(self):
        need("make", "live")
        super().setUp()

    def test_make(self):
        self.online.add(self.did)
        self.counters(10 ** 6, 2 * 10 ** 6)
        self.round()
        for i in range(1, 8):                                       # past a point of m1, traffic of the device
            self.traffic(self.did, 40000, 4000)
            self.counters(10 ** 6 + 1000 * i, 2 * 10 ** 6 + 3000 * i)
            self.round(10)
        doc = json.loads(self.read("/run/kaskad/live.json"))
        put("live.json", {"now": self.now, "did": self.did, "aid": self.aid, "metrics": doc.get("metrics"),
                          "since": doc["devices"][self.did].get("since"), "events": [e["id"] for e in doc["events"]]})
        save_root("live-root")


class LiveRead(_LiveWorld):
    def setUp(self):
        need("read", "live")
        super().setUp()

    def test_read(self):
        from kaskad.panel import api
        load_root("root")
        self.now, self.did = get("now.json")["now"], get("now.json")["did"]
        with open(paths.live_json(), encoding="utf-8") as f:
            new = json.load(f)
        with mock.patch.object(collect, "LIVE", live.Live()):       # its collector starts on the file of the new one
            self.online.add(self.did)
            self.round(10)
            doc = json.loads(self.read("/run/kaskad/live.json"))
        snapshot.forget()
        got, age = api._live_now()[:2]
        put("read.json", {"new_metrics": sorted(new.get("metrics") or {}), "round_metrics": doc.get("metrics"),
                          "since": doc["devices"][self.did].get("since"),
                          "overview": sorted(api._with_live(api._overview_core(True), got, age, True)),
                          "summary": sorted(metrics.exit_summary(int(self.now) // 60 * 60)["exits"])})

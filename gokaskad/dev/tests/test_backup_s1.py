"""T7 — the part ``gateway`` of a copy in stage 1 (SPEC-stage1-exits §1.3, §10.1 «backup.py»; R-s1-t3 З5): the exit
policy and ``gateway.notify`` come with ``gateway``, and so do the zone of the node (KASKAD_TZ) and the counters of the
quotas (traffic.json); a copy without a policy (3.3.x) comes as «Как раньше» (compat), never as the «Стандарт» the
generation of the new node would give it at ``gw enable``."""

import json
import os
import time

from kaskad import backup, paths, state, util
from kaskad.gw import policy as P
from tests.test_backup import PW, BackupCase

TRAFFIC = {"version": 1, "exits": {"e-0123456789": {"period_start": "2026-10-01", "period_bytes": 123456789}}}


class GeoOfTheCopyTest(BackupCase):
    def test_the_restore_keeps_the_country_of_this_server(self):
        """SEC-ns-join З-1, REV-ns-join П-3 (by the behaviour, not by the text of the code): a copy with the country
        «DE» and the SAME machine-id (two servers of one template) restored on a server whose country is «RU» — the
        country stays «RU»; on a server with none — none; the upload of a node in Russia never goes abroad by it."""
        from kaskad.bench import geo, machine
        util.atomic_write(paths.P("/etc/machine-id"), "0123456789abcdef0123456789abcdef\n", 0o444)
        here = machine.mid()
        self.old_node()
        st = state.load()
        st["gateway"].setdefault("hostmon", {})["geo"] = {"country": "DE", "ru": False, "src": "exits_geo",
                                                          "ts": int(time.time()), "mid": here}
        state.save(st)
        text = backup.export(self.w.runner, PW)["text"]
        self.new_node()
        st = state.load()
        st.setdefault("gateway", {}).setdefault("hostmon", {})["geo"] = {
            "country": "RU", "ru": True, "src": "exits_geo", "ts": int(time.time()), "mid": here}
        state.save(st)
        backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        self.assertEqual(geo.country(state.load())["country"], "RU")
        self.new_node()
        st = state.load()
        st.get("gateway", {}).get("hostmon", {}).pop("geo", None)
        state.save(st)
        backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        self.assertIsNone(geo.stored(state.load()))


class GatewayPartTest(BackupCase):
    def test_policy_notify_zone_and_traffic_come_with_the_gateway(self):
        self.old_node()
        st = state.load()
        doc = P.load(st)
        doc["groups"][0]["fail"] = "direct"
        P.save(st, doc)
        st["gateway"]["notify"] = {"push": {"exit_dead": False}, "flap_summary": False}
        state.save(st)
        util.atomic_write(paths.config(), util.read_text(paths.config()) + 'KASKAD_TZ="Europe/Berlin"\n', 0o600)
        os.makedirs(os.path.dirname(paths.traffic_json()), exist_ok=True)
        util.atomic_write(paths.traffic_json(), json.dumps(TRAFFIC), 0o600)
        text = backup.export(self.w.runner, PW)["text"]
        files = backup.unseal(self.w.runner, text, PW)["files"]
        self.assertIn(backup.F_TRAFFIC, files)
        self.assertIn('KASKAD_TZ="Europe/Berlin"', files[backup.F_CONFIG])
        self.new_node()
        self.assertFalse(os.path.exists(paths.traffic_json()))          # gw purge took /var/lib/kaskad/gw (T7)
        backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        st = state.load()
        self.assertEqual(P.load(st)["groups"][0]["fail"], "direct")
        self.assertEqual(st["gateway"]["notify"], {"push": {"exit_dead": False}, "flap_summary": False})
        self.assertIn('KASKAD_TZ="Europe/Berlin"', util.read_text(paths.config()))
        self.assertEqual(json.loads(util.read_text(paths.traffic_json())), TRAFFIC)

    def test_a_copy_without_a_policy_comes_as_compat(self):
        self.old_node()
        st = state.load()
        st["gateway"].pop("exit_policy", None)                         # a copy of 3.3.x
        state.save(st)
        text = backup.export(self.w.runner, PW)["text"]
        self.new_node()
        st = state.load()
        self.assertTrue(state.born_new(st))                            # this node would get «Стандарт» at enable
        backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        st = state.load()
        self.assertEqual(P.stored(st)["groups"][0]["preset"], "compat")

    def test_a_copy_of_a_gateway_never_switched_on_writes_no_policy(self):
        # проверка T7, З-3: a node of stage 1 before its first enable (people only) — no «Как раньше» from its copy,
        # and «Вернуть как было» likewise; ``gw enable`` here then gives «Стандарт» by this node's generation
        from kaskad.gw import ops
        from tests.fakes_gw import VLESS_A, install_parser, write_pool
        from tests.fakes_nft import F443, save_state
        self.ads = self.dummies()
        save_state([F443])
        install_parser()
        write_pool([VLESS_A])
        self.ok("gw", "user", "add", "Мама")
        st = state.load()
        self.assertIsNone(P.stored(st))
        self.assertFalse(st["gateway"].get("enabled_at"))
        text = backup.export(self.w.runner, PW)["text"]
        util.atomic_write(paths.aliases(), "", 0o600)
        self.new_node()
        backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        st = state.load()
        self.assertEqual([u["name"] for u in st["gateway"]["users"]], ["Мама"])
        self.assertIsNone(P.stored(st))
        backup.undo(self.w.runner)
        self.assertIsNone(P.stored(state.load()))
        backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        ops.enable(self.w.runner)
        self.assertEqual(P.stored(state.load())["groups"][0]["preset"], "standard")

    def test_a_failed_write_of_the_counters_keeps_the_keys_of_the_copy(self):
        # проверка безопасности T7, З-2: the zone / traffic.json written after secrets.json; their failure — a warning
        from unittest import mock
        from kaskad import secrets
        self.old_node()
        os.makedirs(os.path.dirname(paths.traffic_json()), exist_ok=True)
        util.atomic_write(paths.traffic_json(), json.dumps(TRAFFIC), 0o600)
        text = backup.export(self.w.runner, PW)["text"]
        old_sec = dict(secrets.load())
        self.new_node()
        real = util.atomic_write

        def full(p, *a, **kw):
            if p == paths.traffic_json():
                raise OSError(28, "No space left on device")
            return real(p, *a, **kw)
        with mock.patch.object(util, "atomic_write", full):
            res = backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        sec = dict(secrets.load())
        for k in old_sec:
            if k.startswith(("wg.", "awg1.", "dev.")):
                self.assertEqual(sec.get(k), old_sec[k], k)
        self.assertTrue(any("No space left" in w for w in res.get("warnings") or []), res.get("warnings"))


class RuPoolPartTest(BackupCase):
    """T15 Q4 (SPEC-groups §2.9 «.kbk»): upstream-ru.txt goes into the same part of the copy as upstream.txt and comes
    back from it into its own file (its legs — never the bot's pool); a node without Russian keys writes no such file
    into the copy (an older Kaskad would refuse a file it does not know)."""

    def test_russian_keys_come_with_the_pool(self):
        from tests.fakes_gw import VLESS_A, VLESS_B, write_ru_pool
        from kaskad.gw import exits
        self.old_node()
        files = backup.unseal(self.w.runner, backup.export(self.w.runner, PW)["text"], PW)["files"]
        self.assertNotIn(backup.F_POOL_RU, files)
        ru = "vless://aaaaaaaa-1111-2222-3333-444444444444@203.0.113.60:443?type=tcp&security=reality#Router"
        write_ru_pool([ru])
        text = backup.export(self.w.runner, PW)["text"]
        files = backup.unseal(self.w.runner, text, PW)["files"]
        self.assertEqual(files[backup.F_POOL_RU], ru + "\n")
        self.assertEqual(backup.summary(files, ["exits"])["exits"]["ru_keys"], 1)
        self.new_node()
        write_ru_pool([])
        from unittest import mock
        from kaskad import util
        logged = []
        with mock.patch.object(util, "log_action", lambda m, *a, **k: logged.append(m)):
            backup.apply(self.w.runner, backup.unseal(self.w.runner, text, PW))
        self.assertEqual(exits.pool_lines("ru"), [ru])
        self.assertEqual(exits.pool_lines(), [VLESS_A, VLESS_B])
        ex = [m for m in logged if "exits" in m or "выходов" in m]        # REV-s1-q4 П2: the Russian key counted too
        self.assertTrue(ex, logged)
        from kaskad.i18n import tr
        subs = backup.summary(backup.unseal(self.w.runner, text, PW)["files"], ["exits"])["exits"].get("subs") or 0
        want = [tr("backup.log.exits_ru", lang, keys=3, ru=1, subs=subs) for lang in ("ru", "en")]
        self.assertTrue([m for m in ex if m.endswith(tuple(want))], (ex, want))
        self.assertFalse([m for m in logged if "203.0.113.60" in m or "aaaaaaaa-" in m], logged)

"""R3 — the consent ⚠РФ for «Россия напрямую» (docs/k3/SPEC-stage1-rf-mark-builtin.md §3–§4, §9): one check in
``gw/ops._change`` (``rules.rfgate.check``), the default of the node by the generation of the state, the marks of an
updated node (``migrate``), the kept result of the scan, the import of a copy, the version of the form «Правила» and
``kaskadctl rules … --confirm-not-rf``. Domains of the tests — example.com and invented ones."""

import json
import os
import time
from unittest import mock

from kaskad import errors, state
from kaskad.gw import model
from kaskad.gw import ops as gops
from kaskad.rules import dat, rfgate, rfmark, store
from kaskad.rules import ops as rops
from tests.rules_helpers import GEOSITE_CATS, seed_small
from tests.test_rules_gw import RulesGwCase

CONSENT = {"confirm": True, "via": "panel", "who": "admin", "lang": "ru"}
META_CATS = dict(GEOSITE_CATS, **{"CATEGORY-GEOBLOCK-RU": ["meta.com", (dat.PLAIN, "meta.ai"), "example.com"]})


def _all_orgs():
    return [o["id"] for o in rfmark.default().orgs if o.get("domains")]


class RfCase(RulesGwCase):
    def node(self, gen=2, enabled=False, default=None):
        """The scenario of test_gw_net (Мама u-00000001: d1, d3; Дача u-00000002: d2) as a node of ``gen``."""
        st = self.scenario(enabled=enabled)
        st["gen"] = gen
        if default:
            st["gateway"]["rules"] = {"default": default}
        state.save(st)
        return state.load()

    def raw(self):
        return self.read("/etc/kaskad/state.json")

    def g(self):
        return state.load()["gateway"]

    def secrets_raw(self):
        p = self.path("/etc/kaskad/secrets.json")
        return self.read("/etc/kaskad/secrets.json") if os.path.exists(p) else None

    def scan_with(self, orgs):
        """A kept result of the scan of the active lists that marks ``orgs`` in «всегда через выход»."""
        v = store.active() or seed_small()
        rows = [["category-geoblock-ru", "domain", "x%d.example.com" % i, o] for i, o in enumerate(orgs)]
        doc = {"schema": rfgate.SCAN_SCHEMA, "at": "2026-10-16T00:00:00Z", "version": v.id,
               "dat": v.sha256(store.GEOSITE), "registry": rfmark.default().version, "stats": {},
               "marked": {"force": rows, "direct": [], "block": []}, "counts": {"force": len(rows)},
               "orgs": {"force": list(orgs), "direct": [], "block": []}}
        os.makedirs(store.root(), exist_ok=True)
        with open(rfgate.scan_path(), "w", encoding="utf-8") as f:
            json.dump(doc, f)


class FreshNodeTest(RfCase):
    """Б2: a new node (gen 2) — all_exit by default, mode none, nothing written."""

    def test_default_by_generation(self):
        st = state.load()                                   # no file: a new node
        self.assertEqual(st["gen"], 2)
        self.assertEqual(model.rules_conf(st)["default"], "all_exit")
        self.assertEqual(rfgate.mode(st), "none")
        new = json.loads(json.dumps(st))
        self.assertIsNone(rfgate.check(st, new))
        self.assertNotIn("rf_gate", new["gateway"])         # gen 2: no mark is written
        old = dict(st, gen=1)
        self.assertEqual(model.rules_conf(old)["default"], "ru_direct")     # an updated node: as 3.3.x
        self.assertEqual(model.RULES_DEFAULTS["default"], "ru_direct")

    def test_set_default_without_the_tick_is_refused_and_nothing_changes(self):
        self.node(enabled=True)
        before, sec_before = self.raw(), self.secrets_raw()
        n = len(self.w.runner.calls)
        with self.assertRaises(errors.KaskadError) as cm:
            rops.set_default(self.w.runner, "ru_direct")
        e = cm.exception
        self.assertEqual(e.code, errors.CONFLICT)
        self.assertEqual(e.data["need"], "rf_consent")
        self.assertEqual(e.data["orgs"], _all_orgs())        # no lists: every organisation with domains
        self.assertIn("Meta", e.data["block"])
        self.assertIn("Скрипт не используется на территории РФ", e.data["consent"])
        self.assertEqual(self.raw(), before)                 # the model is as it was
        self.assertEqual(self.secrets_raw(), sec_before)
        self.assertEqual(self.w.runner.calls[n:], [])         # С6: no nft, xray, dnsmasq — nothing ran

    def test_the_tick_is_written_before_the_apply(self):
        self.node(enabled=True)
        seen = []
        real = gops._apply

        def spy(st, *a, **kw):
            seen.append(json.loads(json.dumps(st["gateway"].get("rf_consent"))))
            return real(st, *a, **kw)
        with mock.patch.object(gops, "_apply", spy):
            res = rops.set_default(self.w.runner, "ru_direct", rf=CONSENT)
        self.assertEqual(res["rules"]["default"], "ru_direct")
        self.assertEqual(seen[0]["via"], "panel")             # the rules applied in this _apply already have it
        c = self.g()["rf_consent"]
        self.assertEqual((c["via"], c["who"], c["lang"], c["orgs"]), ("panel", "admin", "ru", _all_orgs()))
        self.assertTrue(c["at"].endswith("Z"))
        self.assertEqual(rfgate.mode(state.load()), "full")
        self.assertIn("RULES rf-consent: via=panel", self.log())

    def test_the_switches_keep_the_consent(self):
        self.node()
        rops.set_default(self.w.runner, "ru_direct", rf=CONSENT)
        rops.set_blocks(self.w.runner, ads=False)
        self.assertIn("rf_consent", self.g())                 # set_rules_conf writes only gateway.rules
        self.assertEqual(model.rules_conf(state.load())["default"], "ru_direct")

    def test_all_exit_everywhere_resets_the_consent(self):
        self.node()
        rops.set_default(self.w.runner, "ru_direct", rf=CONSENT)
        rops.set_profile(self.w.runner, "user", "u-00000001", "ru_direct")       # covered: no tick needed
        rops.set_default(self.w.runner, "all_exit")
        self.assertIn("rf_consent", self.g())                 # Мама still has ru_direct
        rops.set_profile(self.w.runner, "user", "u-00000001", "inherit")
        self.assertNotIn("rf_consent", self.g())
        self.assertIn("RULES rf-consent: reset", self.log())
        with self.assertRaises(errors.KaskadError):           # a new choice asks again
            rops.set_profile(self.w.runner, "device", "d-00000002", "ru_direct")

    def test_people_and_devices_and_removing_the_last_one(self):
        self.node()
        for target, ref in (("user", "u-00000001"), ("device", "d-00000002")):
            with self.assertRaises(errors.KaskadError) as cm:
                rops.set_profile(self.w.runner, target, ref, "ru_direct")
            self.assertEqual(cm.exception.data["need"], "rf_consent")
        rops.set_profile(self.w.runner, "device", "d-00000002", "ru_direct", rf=dict(CONSENT, via="cli"))
        self.assertEqual(self.g()["rf_consent"]["via"], "cli")
        gops.device_del(self.w.runner, "d-00000002")          # the last ru_direct goes with its device
        self.assertNotIn("rf_consent", self.g())
        self.assertEqual(rfgate.mode(state.load()), "none")

    def test_orgs_are_those_of_the_shown_block(self):
        self.node()
        seed_small()
        self.scan_with(["meta"])
        with self.assertRaises(errors.KaskadError) as cm:
            rops.set_default(self.w.runner, "ru_direct")
        self.assertEqual(cm.exception.data["orgs"], ["meta"])
        self.assertEqual(cm.exception.data["names"], "Meta")
        self.scan_with(["meta", "fbk"])                        # the lists changed while the block was read
        before = self.raw()
        with self.assertRaises(errors.KaskadError) as cm:
            rops.set_default(self.w.runner, "ru_direct", rf=dict(CONSENT, orgs=["meta"]))
        self.assertEqual((cm.exception.data["need"], cm.exception.data["orgs"]), ("rf_consent", ["meta", "fbk"]))
        self.assertEqual(self.raw(), before)
        rops.set_default(self.w.runner, "ru_direct", rf=dict(CONSENT, orgs=["meta", "fbk"]))
        self.assertEqual(self.g()["rf_consent"]["orgs"], ["meta", "fbk"])

    def test_new_organisation_is_uncovered_and_consent_adds_it(self):
        self.node()
        seed_small()
        self.scan_with(["meta"])
        rops.set_default(self.w.runner, "ru_direct", rf=dict(CONSENT, orgs=["meta"]))
        self.scan_with(["meta", "fbk"])                        # a new organisation after an update of the lists
        rf = rfgate.state(state.load())
        self.assertEqual((rf["mode"], rf["uncovered"]), ("full", ["fbk"]))
        rops.set_profile(self.w.runner, "user", "u-00000002", "ru_direct")       # applies without a question
        with self.assertRaises(errors.KaskadError):
            rops.consent(self.w.runner, None)
        res = rops.consent(self.w.runner, dict(CONSENT, via="ssh"))
        self.assertTrue(res["granted"])
        self.assertEqual(self.g()["rf_consent"]["orgs"], ["meta", "fbk"])
        self.assertEqual(rfgate.state(state.load())["uncovered"], [])

    def test_consent_with_nothing_chosen_writes_nothing(self):
        self.node()
        res = rops.consent(self.w.runner, CONSENT)
        self.assertFalse(res["granted"])
        self.assertNotIn("rf_consent", self.g())

    def test_scheme_is_refused_by_the_same_check(self):
        """Б1: the scheme changes a profile past rules/ops — the check of _change refuses it all the same."""
        from kaskad.panel import scheme
        from kaskad.panel.routes import HttpError
        self.node()
        before = self.raw()
        with self.assertRaises(HttpError) as cm:
            scheme.apply({"intents": [{"type": "route", "device": "d-00000002", "via": "block"},
                                      {"type": "rules", "device": "d-00000001", "profile": "ru_direct"}]},
                         runner=self.w.runner)
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.raw(), before)                  # neither intent of the batch is applied


class VersionTest(RfCase):
    def test_ver_of_the_form(self):
        st = self.node()
        ver = rops.status(st)["ver"]
        before = self.raw()
        with self.assertRaises(errors.KaskadError) as cm:
            rops.set_default(self.w.runner, "ru_direct", rf=CONSENT, ver="0" * 12)
        self.assertEqual((cm.exception.code, cm.exception.data["code"]), (errors.CONFLICT, "object_changed"))
        self.assertEqual(cm.exception.data["ver"], ver)
        self.assertEqual(self.raw(), before)
        rops.set_blocks(self.w.runner, ads=False)              # the switches are not in the object
        self.assertEqual(rops.status(state.load())["ver"], ver)
        res = rops.set_default(self.w.runner, "ru_direct", rf=CONSENT, ver=ver)
        self.assertNotEqual(res["ver"], ver)
        self.assertEqual(res["ver"], rops.status(state.load())["ver"])


class LegacyNodeTest(RfCase):
    """A node of 3.3.x (no gen) with ru_direct: works as before (legacy), a new choice asks the tick."""

    def test_legacy_mode_and_a_new_choice(self):
        self.node(gen=1)
        st = state.load()
        self.assertTrue(rfgate.allowed(st))
        self.assertEqual(rfgate.mode(st), "legacy")
        rops.set_blocks(self.w.runner, ads=False)              # the first write marks the node
        g = self.g()
        self.assertEqual(g["rf_gate"], 1)
        self.assertEqual(g["rf_legacy"]["from"], "gen1")
        self.assertEqual(rfgate.mode(state.load()), "legacy")
        with self.assertRaises(errors.KaskadError):
            rops.set_profile(self.w.runner, "user", "u-00000002", "ru_direct")
        rops.set_profile(self.w.runner, "user", "u-00000002", "ru_direct", rf=CONSENT)
        g = self.g()
        self.assertNotIn("rf_legacy", g)
        self.assertEqual(rfgate.mode(state.load()), "full")

    def test_migrate_is_idempotent_and_nothing_on_gen2(self):
        self.node(gen=1)
        r1, r2 = rfgate.migrate_node(), rfgate.migrate_node()
        self.assertEqual((r1["migrated"], r2["migrated"]), (True, False))
        self.assertEqual(self.log().count("RULES rf-gate: migrated"), 1)
        self.assertIn("rf_legacy", self.g())
        self.node(gen=2)
        before = self.raw()
        self.assertFalse(rfgate.migrate_node()["migrated"])
        self.assertEqual(self.raw(), before)

    def test_update_units_runs_the_migration(self):
        from kaskad import unit_refresh
        self.node(gen=1)
        res = unit_refresh.refresh(self.w.runner)
        self.assertTrue(res["rf"]["migrated"])
        self.assertEqual(self.g()["rf_gate"], 1)

    def test_gen1_all_exit_marked_without_legacy_then_need(self):
        self.node(gen=1, default="all_exit")
        seed_small()
        rfgate.migrate_node()
        self.assertNotIn("rf_legacy", self.g())
        st = state.load()
        st["gateway"]["rules"]["default"] = "ru_direct"      # a hand edit: chosen, never applied (рубеж 2 — R2)
        self.assertEqual(rfgate.mode(st), "need")
        self.assertFalse(rfgate.allowed(st))
        self.assertEqual(rops.status(st)["default_applied"], "all_exit")
        self.assertEqual(rops.status(st)["default_why"], "no_consent")


class PurgeTest(RfCase):
    def test_purge_with_people(self):
        self.node(gen=2, enabled=False)
        gops.purge(self.w.runner, users=True)
        st = state.load()
        self.assertEqual((model.rules_conf(st)["default"], rfgate.mode(st)), ("all_exit", "none"))

    def test_purge_keeps_the_marks_of_gen1(self):
        self.node(gen=1, enabled=False)
        rfgate.migrate_node()
        gops.purge(self.w.runner, users=True)
        g = self.g()
        self.assertEqual(g["rf_gate"], 1)
        self.assertIn("rf_legacy", g)
        self.assertEqual(rfgate.mode(state.load()), "legacy")

    def test_purge_of_gen1_without_legacy_is_need(self):
        self.node(gen=1, enabled=False, default="all_exit")
        rfgate.migrate_node()
        gops.purge(self.w.runner, users=True)
        st = state.load()
        self.assertEqual(model.rules_conf(st)["default"], "ru_direct")
        self.assertEqual(rfgate.mode(st), "need")


class PurgeMarksTest(RfCase):
    """REV-s1-r3 З2: ``purge`` keeps the marks ``rf_*``, migrates by the state before it and resets the consent."""

    def rf_keys(self):
        return sorted(k for k in self.g() if k.startswith("rf_"))

    def test_s11_purge_of_an_unmarked_gen1_with_all_exit_is_need(self):
        self.node(gen=1, default="all_exit")                 # not migrated yet; «Россия напрямую» chosen nowhere
        gops.purge(self.w.runner, users=True)
        st = state.load()
        self.assertEqual(model.rules_conf(st)["default"], "ru_direct")       # the default of 3.3.x comes back
        self.assertEqual(rfgate.mode(st), "need")            # not legacy: before purge nothing chose ru_direct
        self.assertEqual(self.rf_keys(), ["rf_gate"])

    def test_s12_purge_of_gen1_keeps_the_consent(self):
        self.node(gen=1)
        rops.set_profile(self.w.runner, "user", "u-00000002", "ru_direct", rf=CONSENT)
        gops.purge(self.w.runner, users=True)
        self.assertEqual(rfgate.mode(state.load()), "full")
        self.assertEqual(self.rf_keys(), ["rf_consent", "rf_gate"])
        self.assertEqual(self.g()["rf_consent"]["via"], "panel")

    def test_s13_purge_of_gen2_resets_the_consent(self):
        self.node()
        rops.set_profile(self.w.runner, "user", "u-00000002", "ru_direct", rf=CONSENT)
        gops.purge(self.w.runner, users=True)
        self.assertEqual(rfgate.mode(state.load()), "none")
        self.assertEqual(self.rf_keys(), [])
        self.assertIn("RULES rf-consent: reset", self.log())


class JournalTest(RfCase):
    """REV-s1-r3 придирка 1: the lines ⚠РФ of the journal — only of a write that is made."""

    def test_a_refused_write_says_nothing(self):
        self.node(gen=1, default="all_exit")
        for _ in range(2):
            with self.assertRaises(errors.KaskadError):
                rops.set_default(self.w.runner, "ru_direct")
        self.assertNotIn("rf-gate: migrated", self.log())
        self.assertIsNone(self.g().get("rf_gate"))
        rops.set_default(self.w.runner, "ru_direct", rf=CONSENT)
        self.assertEqual(self.log().count("RULES rf-gate: migrated"), 1)
        self.assertEqual(self.log().count("RULES rf-consent: via=panel"), 1)

    def test_a_failed_apply_says_nothing(self):
        self.node(gen=1, enabled=True, default="all_exit")
        with mock.patch.object(gops, "_apply", side_effect=errors.KaskadError("boom")):
            with self.assertRaises(errors.KaskadError):
                rops.set_default(self.w.runner, "ru_direct", rf=CONSENT)
        self.assertNotIn("rf-gate: migrated", self.log())
        self.assertNotIn("rf-consent", self.log())
        self.assertNotIn("rf_consent", self.g())


class ScanTest(RfCase):
    def test_kept_result_after_update_and_migrate(self):
        self.node()
        v = seed_small(cats=META_CATS)
        self.assertIsNone(rfgate.state(state.load())["scan"])
        self.assertEqual(rfgate.block_orgs(), _all_orgs())    # no result: every organisation with domains
        res = rfgate.migrate_node()                           # a new package (the registry): counted
        self.assertEqual(res["scan"]["action"], "counted")
        doc = rfgate.load_scan()
        self.assertEqual((doc["dat"], doc["registry"]), (v.sha256(store.GEOSITE), rfmark.default().version))
        rows = doc["marked"]["force"]
        self.assertEqual(sorted(r[2] for r in rows), ["meta.ai", "meta.com"])
        self.assertEqual(rfgate.migrate_node()["scan"]["action"], "kept")
        # the readers never start a process
        with mock.patch("subprocess.Popen", side_effect=AssertionError("a process")):
            rf = rops.status(state.load())["rf"]
            self.assertEqual(rfgate.block_orgs(), ["meta"])
            with self.assertRaises(errors.KaskadError) as cm:
                rops.set_default(self.w.runner, "ru_direct")
            self.assertEqual(cm.exception.data["orgs"], ["meta"])
        self.assertEqual(rf["marked"]["force"][0]["org"], "meta")
        self.assertEqual(rf["marked_n"]["force"], 2)
        self.assertFalse(rf["scan"]["stale"])
        self.assertEqual(rf["scan"]["entries"], doc["stats"]["entries"])
        seed_small(cats=dict(META_CATS, TELEGRAM=["telegram.org"]))     # new lists, not counted yet
        self.assertTrue(rfgate.state(state.load())["scan"]["stale"])
        self.assertEqual(rfgate.block_orgs(), ["meta"])       # shown as it is

    def test_update_counts_and_a_failure_is_a_warning(self):
        from tests.rules_helpers import FakeHttp, release
        self.node()
        http = release(FakeHttp(), geosite=None)
        res = rops.update(self.w.runner, http=http, apply_live=False)
        self.assertTrue(res["changed"])
        self.assertEqual(res["rf_scan"]["action"], "counted")
        self.assertIsNotNone(rfgate.load_scan())
        os.unlink(rfgate.scan_path())
        with mock.patch.object(rfmark, "default", side_effect=errors.KaskadError("broken registry")):
            res = rops.update(self.w.runner, http=http, apply_live=False)
        self.assertTrue(res["ok"])                            # the update stays done
        self.assertEqual(res["rf_scan"]["action"], "failed")
        self.assertTrue(any("⚠РФ" in w for w in res["warnings"]), res["warnings"])

    def test_status_lines_say_the_incomplete_mark(self):
        rf = {"mode": "need", "orgs": [], "scan": {"regexp_unchecked": 2, "unknown": 1, "stale": True,
                                                   "regexp_unchecked_by": {"dangerous": 1, "timeout": 1}}}
        text = "\n".join(rfgate.notes(rf, "ru"))
        self.assertIn("Не проверено выражений: 2", text)
        self.assertIn("опасное — 1", text)
        self.assertIn("Записей неизвестного типа: 1", text)
        self.assertIn("по прежним спискам", text)
        self.assertIn("не включена", text)
        en = "\n".join(rfgate.notes(rf, "en"))
        self.assertIn("Expressions not checked: 2", en)


class BackupTest(RfCase):
    def test_foreign_copy_does_not_bring_the_consent(self):
        from kaskad import backup
        self.node()
        rops.set_default(self.w.runner, "ru_direct", rf=CONSENT)
        doc = backup.payload(self.w.runner, ["gateway"])
        self.assertIn("rf_consent", json.loads(doc["files"][backup.F_STATE])["gateway"])
        self.node()                                           # another node
        ctx = backup._Ctx(self.w.runner, doc, {"changed": False}, None)
        backup._apply_gateway(ctx)
        g = self.g()
        self.assertNotIn("rf_consent", g)
        self.assertEqual(g["rf_gate"], 1)
        self.assertEqual(rfgate.mode(state.load()), "need")   # chosen, not applied until the tick
        self.assertTrue(any("подтвердите ⚠РФ" in n for n in ctx.notes), ctx.notes)
        # the copy of this very server (undo, rollback): the marks come as they are
        self.node()
        ctx = backup._Ctx(self.w.runner, doc, {"changed": False}, None, exact=True, same_node=True)
        backup._apply_gateway(ctx)
        self.assertEqual(self.g()["rf_consent"]["via"], "panel")
        self.assertEqual(rfgate.mode(state.load()), "full")


class BackupProfileTest(RfCase):
    """REV-s1-r3 С1, З1: a copy carries the effective default of its node; a copy without it is of 3.3.x
    (``ru_direct``); the line «подтвердите ⚠РФ» whenever the result is the mode ``need``."""

    def setUp(self):
        super().setUp()
        from kaskad import backup
        self.backup = backup

    def copy(self, drop_default=False):
        doc = self.backup.payload(self.w.runner, ["gateway"])
        if drop_default:                                      # a copy made by 3.3.x: no gen, no gateway.rules.default
            fs = json.loads(doc["files"][self.backup.F_STATE])
            fs["gateway"].pop("rules", None)
            doc["files"][self.backup.F_STATE] = json.dumps(fs)
        return doc

    def put(self, doc, **kw):
        ctx = self.backup._Ctx(self.w.runner, doc, {"changed": False}, None, **kw)
        self.backup._apply_gateway(ctx)
        return ctx

    def said(self, ctx):
        return any("подтвердите ⚠РФ" in n for n in ctx.notes)

    def test_the_copy_carries_the_effective_default(self):
        for gen, want in ((2, "all_exit"), (1, "ru_direct")):
            self.node(gen=gen)
            fs = json.loads(self.copy()["files"][self.backup.F_STATE])
            self.assertEqual(fs["gateway"]["rules"]["default"], want, gen)

    def test_s3_a_335_copy_on_a_fresh_node_is_need(self):
        self.node(gen=1)
        doc = self.copy(drop_default=True)
        self.node(gen=2)                                      # a new VPS: the move of a node of 3.3.5
        ctx = self.put(doc)
        st = state.load()
        self.assertEqual(st["gateway"]["rules"]["default"], "ru_direct")      # written, not by this node's gen
        self.assertEqual(rfgate.mode(st), "need")
        self.assertTrue(self.said(ctx), ctx.notes)

    def test_s9_a_new_node_copy_on_an_updated_node_stays_all_exit(self):
        self.node(gen=2)
        doc = self.copy()
        self.node(gen=1, default="all_exit")
        rfgate.migrate_node()
        ctx = self.put(doc)
        st = state.load()
        self.assertEqual(model.rules_conf(st)["default"], "all_exit")
        self.assertEqual(model.profile_of(st, st["gateway"]["devices"][0])["profile"], "all_exit")
        self.assertEqual(rfgate.mode(st), "none")
        self.assertFalse(self.said(ctx), ctx.notes)

    def test_s1_own_copy_with_the_consent_says_need(self):
        self.node()
        rops.set_default(self.w.runner, "ru_direct", rf=CONSENT)
        ctx = self.put(self.copy())                           # the import of a file: no same_node — a foreign copy
        self.assertEqual(rfgate.mode(state.load()), "need")
        self.assertTrue(self.said(ctx), ctx.notes)

    def test_s2_legacy_node_own_copy_says_need(self):
        self.node(gen=1)
        rfgate.migrate_node()
        self.assertEqual(rfgate.mode(state.load()), "legacy")
        ctx = self.put(self.copy())
        self.assertEqual(rfgate.mode(state.load()), "need")
        self.assertTrue(self.said(ctx), ctx.notes)

    def test_undo_brings_the_profile_and_the_consent_back(self):
        self.node()
        rops.set_default(self.w.runner, "ru_direct", rf=CONSENT)
        snap = self.copy()                                    # what apply keeps before an import
        other = json.loads(snap["files"][self.backup.F_STATE])
        other["gateway"]["rules"]["default"] = "all_exit"     # the copy of another node: «весь трафик через выход»
        self.put(dict(snap, files=dict(snap["files"], **{self.backup.F_STATE: json.dumps(other)})))
        self.assertEqual((model.rules_conf(state.load())["default"], rfgate.mode(state.load())), ("all_exit", "none"))
        ctx = self.put(snap, exact=True, same_node=True)      # undo
        st = state.load()
        self.assertEqual((model.rules_conf(st)["default"], rfgate.mode(st)), ("ru_direct", "full"))
        self.assertEqual(st["gateway"]["rf_consent"]["via"], "panel")
        self.assertFalse(self.said(ctx), ctx.notes)

    def test_undo_of_a_335_import_on_a_fresh_node(self):
        self.node(gen=1)
        old = self.copy(drop_default=True)
        self.node(gen=2)
        snap = self.copy()
        self.put(old)
        self.assertEqual(rfgate.mode(state.load()), "need")
        ctx = self.put(snap, exact=True, same_node=True)
        st = state.load()
        self.assertEqual((model.rules_conf(st)["default"], rfgate.mode(st)), ("all_exit", "none"))
        self.assertFalse(self.said(ctx), ctx.notes)

    def test_rollback_to_a_335_snapshot_of_this_updated_node(self):
        self.node(gen=1, default="all_exit")
        rfgate.migrate_node()
        snap = self.copy(drop_default=True)                   # taken by 3.3.5 before the update: no default
        snap_st = json.loads(snap["files"][self.backup.F_STATE])
        snap_st["gateway"]["rules"] = {"block_ads": False}   # other switches stay as they are
        for k in rfgate.KEYS:                                 # 3.3.5 had no marks ⚠РФ
            snap_st["gateway"].pop(k, None)
        snap["files"][self.backup.F_STATE] = json.dumps(snap_st)
        ctx = self.put(snap, exact=True, same_node=True)
        st = state.load()
        self.assertEqual(st["gateway"]["rules"], {"block_ads": False})        # nothing written on this node's copy
        self.assertEqual(model.rules_conf(st)["default"], "ru_direct")       # gen 1: as 3.3.5 had it
        # the node was migrated with all_exit: «Россия напрямую» of the old snapshot is a new choice — not legacy
        self.assertEqual((st["gateway"].get("rf_gate"), rfgate.mode(st)), (1, "need"))
        self.assertTrue(self.said(ctx), ctx.notes)


class CliTest(RfCase):
    def test_cli_flag_via_and_consent(self):
        self.node()
        before = self.raw()
        rc, doc, _ = self.run_json("rules", "default", "ru_direct")
        self.assertEqual(rc, errors.CONFLICT)
        self.assertEqual(doc["data"]["need"], "rf_consent")
        self.assertEqual(self.raw(), before)
        rc, out, err = self.run_cli("rules", "default", "ru_direct")
        self.assertEqual(rc, 3)
        self.assertIn("--confirm-not-rf", err)
        self.assertIn("Meta", err)
        rc, doc, _ = self.run_json("rules", "default", "ru_direct", "--confirm-not-rf", "--via", "ssh")
        self.assertEqual(rc, 0)
        self.assertEqual(self.g()["rf_consent"]["via"], "ssh")
        self.assertIsNone(self.g()["rf_consent"]["who"])
        rc, doc, _ = self.run_json("rules", "consent", "--confirm-not-rf", "--via", "mcp")
        self.assertEqual(rc, 0)         # the consent in force covers the block: a spare tick, nothing written (П-4)
        self.assertEqual(self.g()["rf_consent"]["via"], "ssh")   # without a consent MCP must say what it showed —
                                                                 # test_rules_rf_ssh.ShownOrgsTest
        rc, doc, _ = self.run_json("rules", "consent", "--confirm-not-rf", "--via", "mcp", "--orgs", "meta,fbk")
        self.assertEqual((rc, doc["granted"]), (0, True))
        c = self.g()["rf_consent"]
        self.assertEqual((c["via"], c["who"]), ("mcp", "mcp"))
        self.assertNotIn("ip", json.dumps(c).lower().replace("ssh", ""))
        rc, doc, _ = self.run_json("rules", "status")
        self.assertEqual(doc["rf"]["mode"], "full")
        out, _ = self.ok("rules", "status")
        self.assertIn("⚠РФ: подтверждено", out)


class BrokenRegistryTest(RfCase):
    """К2-9 (REV-s1-kt2-final З-1): the registry ⚠РФ is broken — the block cannot be named, so no tick writes a consent:
    not ``--via env`` without ``--orgs`` (the install without the wizard), not with the codes of a kept result (names are
    the codes then), not a surface that says what it showed. A refusal without ``need`` (no tick to offer)."""

    def _broken(self):
        return mock.patch.object(rfmark, "default", side_effect=errors.KaskadError("broken registry"))

    def _refused(self, *args):
        before = self.raw()
        with self._broken():
            rc, doc, _ = self.run_json(*args)
        self.assertEqual(rc, errors.CONFLICT, doc)
        self.assertNotIn("need", doc.get("data") or {})
        self.assertIn("согласие не записано", doc["error"] if isinstance(doc.get("error"), str) else json.dumps(doc,
                      ensure_ascii=False))
        self.assertEqual(self.raw(), before)
        self.assertNotIn("rf_consent", self.g())
        return doc

    def test_env_without_orgs_is_refused(self):
        self.node()
        self._refused("rules", "default", "ru_direct", "--confirm-not-rf", "--via", "env")

    def test_codes_of_a_kept_result_are_refused(self):
        self.node()
        seed_small()
        self.scan_with(["meta"])
        with self._broken():
            self.assertEqual(rfgate.state()["orgs"], [{"id": "meta", "name": "meta", "since": None}])
            self.assertIn("error", rfgate.state())
        self._refused("rules", "default", "ru_direct", "--confirm-not-rf", "--via", "env", "--orgs", "meta")
        self._refused("rules", "default", "ru_direct", "--confirm-not-rf", "--via", "ssh", "--orgs", "meta")

    def test_a_panel_tick_is_refused_and_an_import_is_need(self):
        self.node()
        before = self.raw()
        with self._broken(), self.assertRaises(errors.KaskadError) as cm:
            rops.set_default(self.w.runner, "ru_direct", rf=CONSENT)
        self.assertEqual(cm.exception.code, errors.CONFLICT)
        self.assertIn("согласие не записано", str(cm.exception))
        self.assertEqual(self.raw(), before)
        st = state.load()
        new = json.loads(json.dumps(st))
        new["gateway"].setdefault("rules", {})["default"] = "ru_direct"
        with self._broken():
            got = rfgate.check(st, new, dict(CONSENT), refuse=False, log=[])
        self.assertIn("need", got)
        self.assertNotIn("rf_consent", new["gateway"])

    def test_the_texts_have_no_path_the_journal_has(self):
        """REV-338 П-2: «не прочитал список организаций» и «реестр повреждён» — для человека без пути к файлу и без
        технических слов; путь — в журнале (/var/log/kaskad.log)."""
        from kaskad import paths
        from kaskad.i18n import use_lang
        self.node()
        doc = self._refused("rules", "default", "ru_direct", "--confirm-not-rf", "--via", "env")
        for lang in ("ru", "en"):
            with self._broken(), use_lang(lang), self.assertRaises(errors.KaskadError) as cm:
                rops.set_default(self.w.runner, "ru_direct", rf=dict(CONSENT, lang=lang))
            with self._broken():
                bad = rfgate.state(lang=lang)["error"]
            for text in (str(cm.exception), bad, doc["error"]):
                self.assertNotIn(rfmark.PATH, text)
                self.assertNotIn("/", text)
                self.assertNotIn("registry)", text.replace("(the registry is broken)", ""))
        with open(paths.log(), encoding="utf-8") as f:
            self.assertIn("RULES rf-consent: refused, the registry %s is unreadable" % rfmark.PATH, f.read())

    def test_a_whole_registry_still_grants(self):
        self.node()
        rc, doc, _ = self.run_json("rules", "default", "ru_direct", "--confirm-not-rf", "--via", "env")
        self.assertEqual(rc, 0, doc)
        self.assertEqual(self.g()["rf_consent"]["orgs"], _all_orgs())

class RfmarkR3Test(RfCase):
    def test_a_worker_that_never_starts_is_timeout_for_all(self):
        """REV-SPEC-stage1-fix-21 З-1: a hung start is «timeout» for every expression, never «budget»."""
        reg = rfmark.default()
        cats = {"CATEGORY-GEOBLOCK-RU": [(dat.REGEX, r"^a%d\.example\.org$" % i) for i in range(4)]}
        stats = {}
        hang = os.path.join(self.root, "hang.sh")              # a «python» that never says «ready»
        with open(hang, "w") as f:
            f.write("#!/bin/sh\nsleep 2\n")
        os.chmod(hang, 0o755)
        with mock.patch.object(rfmark, "WORKER_START_S", 0.3), mock.patch.object(rfmark, "REGEXP_BUDGET_S", 0.05), \
                mock.patch.object(rfmark.sys, "executable", hang):
            reg.scan(cats, stats=stats)
        self.assertEqual(stats["regexp_unchecked_by"]["timeout"], 4)
        self.assertEqual(stats["regexp_unchecked_by"]["budget"], 0)

    def test_worst_static_parse_is_bounded(self):
        """D-099: the worst parse without a run (REV-s1-w0-5 Р1 forms: 7–10 ms before R3) stays within 15 parses of
        the expression by ``re`` itself (under 5 ms on a free machine)."""
        import itertools
        import string
        reg = rfmark.default()
        alts = ["".join(p) for p in itertools.product(string.ascii_lowercase, string.ascii_lowercase)]
        forms = ["(x|x)" * 70 + "(?:" + "|".join(alts[:52]) + ")", "(x)?" * 65 + "(?:" + "|".join(alts[:82]) + ")",
                 "^" + "(x|x)" * 70 + r"\." + "(?:" + "|".join(alts[:38]) + r")\.facebook\.com$",
                 "^" + "(x|x)" * 40 + "(?:" + "|".join(a + r"\.ai" for a in alts[:43]) + ")$"]
        try:
            import sre_parse as parser                        # 3.8–3.10
        except ImportError:                                   # pragma: no cover — 3.11+
            from re import _parser as parser

        def whole(expr):
            py, tree, why = rfmark._regexp_prepare(expr)
            if not why:
                reg._literal_org(tree)
                rfmark._dangerous(tree, icase=bool(tree.state.flags & rfmark._IGNORECASE))

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            for expr in forms:
                expr = expr[:rfmark.REGEXP_MAX]
                best, ref = 1.0, 1.0
                for _ in range(7):                            # the best of runs side by side: one load for both
                    t0 = time.perf_counter()
                    whole(expr)
                    t1 = time.perf_counter()
                    parser.parse(expr)
                    best, ref = min(best, t1 - t0), min(ref, time.perf_counter() - t1)
                # Not a bound in ms (a busy VPS under -j4 is slower, REV-s1-r3 З4): the parse of the same expression
                # by ``re`` itself, measured side by side — ≤ 8× of it now (~1–2 ms on a Mac), 21–33× before D-099
                with self.subTest(expr=expr[:30], ms=round(best * 1000, 2), ratio=round(best / ref, 1)):
                    self.assertLess(best, 15 * ref)
        self.assertEqual(rfmark.regexp_entry(r"^(x|x)" * 20 + r"\.facebook\.com$"), ("meta", None))


if __name__ == "__main__":
    import unittest
    unittest.main()

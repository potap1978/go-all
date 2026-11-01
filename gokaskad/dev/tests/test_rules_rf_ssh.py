"""R6 — ⚠РФ in the SSH menu and the install without the wizard (docs/k3/SPEC-stage1-rf-mark-builtin.md §5.2, §5.3):
the line of the header of the main menu (``summary --json → rf``), the command of ``KASKAD_CONFIRM_NOT_RF=1`` as
install.sh calls it, the texts of the block and of the tick in the bash catalogue — word for word those of the core.
The menu itself — tools/k3_install_test.sh, section 54."""

import os
import re

from kaskad import errors, state, summary
from kaskad.i18n import tr
from kaskad.rules import rfgate, rfmark
from kaskad.rules import ops as rops
from tests.rules_helpers import seed_small
from tests.test_rules_rf_consent import CONSENT, RfCase, _all_orgs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SH = os.path.join(ROOT, "lib", "kaskad", "i18n", "sh")
INSTALL = os.path.join(ROOT, "install.sh")
ENV_CMD = "rules default ru_direct --confirm-not-rf --via env"


def us(text):
    """The bash catalogues spell English the US way (tests/test_i18n_terms_sh.py); the words of the core may not."""
    return text.replace("organisation", "organization").replace("recognised", "recognized")


def sh_texts(lang):
    out = {}
    with open(os.path.join(SH, "gwrules.%s.sh" % lang), encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^M_%s_(GWR_RF_\w+)='(.*)'$" % lang, line.rstrip("\n"))
            if m:
                out[m.group(1)] = m.group(2)
    return out


class HeaderTest(RfCase):
    def test_new_node_none(self):
        self.node(gen=2)
        rf = summary.summary()["rf"]
        self.assertEqual(rf, {"mode": "none", "uncovered_names": ""})

    def test_updated_node_legacy(self):
        self.node(gen=1)
        self.assertEqual(summary.summary()["rf"]["mode"], "legacy")

    def test_need_after_a_hand_edit(self):
        self.node(gen=2)
        st = state.load()
        st["gateway"]["rules"] = {"default": "ru_direct"}
        state.save(st)
        self.assertEqual(summary.summary()["rf"]["mode"], "need")

    def test_full_and_a_new_organisation_by_name(self):
        self.node(gen=2)
        seed_small()
        self.scan_with(["meta"])
        rops.set_default(self.w.runner, "ru_direct", rf=dict(CONSENT, orgs=["meta"]))
        self.assertEqual(summary.summary()["rf"], {"mode": "full", "uncovered_names": ""})
        self.scan_with(["meta", "fbk"])
        self.assertEqual(summary.summary()["rf"],
                         {"mode": "full", "uncovered_names": rfmark.default().names(["fbk"])})

    def test_the_header_never_fails_over_the_mark(self):
        self.node(gen=1)
        from unittest import mock
        with mock.patch.object(rfgate, "state", side_effect=ValueError("broken")):
            doc = summary.summary()
        self.assertIsNone(doc["rf"])
        self.assertEqual(doc["engine"], state.load()["engine"])


class EnvConsentTest(RfCase):
    def test_install_sh_calls_this_command(self):
        with open(INSTALL, encoding="utf-8") as f:
            text = f.read()
        # К2-9 (T-KT2): the command with the codes of the block printed before it (--orgs), not a consent on all;
        # never without --orgs — a broken registry is a refusal (REV-s1-kt2-final З-1)
        self.assertIn('set -- %s --orgs "$ids"' % ENV_CMD, text)
        self.assertNotIn("set -- %s\n" % ENV_CMD, text)
        self.assertIn('if out=$(k3ctl "$@" 2>&1); then', text)

    def test_env_with_the_shown_orgs(self):
        """К2-9: what install.sh does — ``rules status`` gives the organisations, the consent is on them alone."""
        rc, st, err = self.run_json("rules", "status")
        self.assertEqual(rc, 0, err)
        ids = [o["id"] for o in st["rf"]["orgs"]]
        self.assertTrue(ids)
        rc, doc, err = self.run_json(*(ENV_CMD.split() + ["--orgs", ",".join(ids)]))
        self.assertEqual(rc, 0, err)
        self.assertEqual(sorted(state.load()["gateway"]["rf_consent"]["orgs"]), sorted(ids))

    def test_fresh_node_without_the_variable_is_all_exit(self):
        st = state.load()                                     # no state.json: a new node (gen 2)
        self.assertEqual(st["gen"], 2)
        self.assertEqual(rops.status(st)["default"], "all_exit")
        self.assertEqual(rfgate.mode(st), "none")

    def test_fresh_node_with_the_variable(self):
        rc, doc, err = self.run_json(*ENV_CMD.split())
        self.assertEqual(rc, 0, err)
        g = state.load()["gateway"]
        self.assertEqual(g["rules"]["default"], "ru_direct")
        self.assertEqual((g["rf_consent"]["via"], g["rf_consent"]["who"]), ("env", None))
        self.assertEqual(rfgate.mode(state.load()), "full")
        self.assertIn("via=env", self.log())

    def test_without_the_flag_the_core_refuses(self):
        rc, doc, _ = self.run_json("rules", "default", "ru_direct")
        self.assertEqual(rc, 3)
        self.assertEqual(doc["data"]["need"], "rf_consent")
        self.assertNotIn("rf_consent", state.load()["gateway"])


class ShownOrgsTest(RfCase):
    """SEC-s1-r67 №1: the consent covers only the organisations of the block the person SAW — ``--orgs`` of the SSH
    menu and MCP; the block names more now (the lists were updated meanwhile) — a refusal with the new block."""

    def test_orgs_as_shown_consent_on_them(self):
        rc, doc, err = self.run_json("rules", "default", "ru_direct", "--confirm-not-rf", "--via", "ssh",
                                     "--orgs", "meta,fbk")
        self.assertEqual(rc, 0, err)
        self.assertEqual(state.load()["gateway"]["rf_consent"]["orgs"], ["meta", "fbk"])

    def test_a_narrower_block_than_now_is_refused(self):
        rc, doc, _ = self.run_json("rules", "default", "ru_direct", "--confirm-not-rf", "--via", "ssh", "--orgs", "meta")
        self.assertEqual(rc, 3)
        self.assertEqual((doc["data"]["need"], doc["data"]["orgs"]), ("rf_consent", ["meta", "fbk"]))
        self.assertNotIn("rf_consent", state.load()["gateway"])
        self.assertEqual(state.load()["gateway"].get("rules", {}).get("default", "all_exit"), "all_exit")

    def test_mcp_must_say_what_it_showed(self):
        rc, doc, _ = self.run_json("rules", "default", "ru_direct", "--confirm-not-rf", "--via", "mcp")
        self.assertEqual(rc, 3)
        self.assertEqual(doc["data"]["need"], "rf_consent")
        self.assertNotIn("rf_consent", state.load()["gateway"])
        rc, doc, err = self.run_json("rules", "default", "ru_direct", "--confirm-not-rf", "--via", "mcp",
                                     "--orgs", "meta,fbk")
        self.assertEqual(rc, 0, err)
        self.assertEqual(state.load()["gateway"]["rf_consent"]["via"], "mcp")

    def test_a_spare_tick_without_orgs_is_ignored(self):
        # REV-s1-rf-2 П-4: the consent in force covers the block — a phrase without orgs (MCP) changes nothing and
        # refuses nothing: lowering a device to all_exit, ru_direct for another one, «Подтвердить» once more.
        self.node(enabled=True)
        rops.set_default(self.w.runner, "ru_direct", rf=rops.rf_of(True, via="mcp", who="mcp", orgs=_all_orgs(),
                                                                    must_show=True))
        c0 = dict(self.g()["rf_consent"])
        spare = rops.rf_of(True, via="mcp", who="mcp", must_show=True)
        rops.set_profile(self.w.runner, "device", "d-00000001", "all_exit", rf=spare)
        rops.set_profile(self.w.runner, "device", "d-00000002", "ru_direct", rf=spare)
        rops.consent(self.w.runner, spare)
        self.assertEqual(self.g()["rf_consent"], c0)                    # nothing written over the consent
        devs = dict((d["id"], d.get("rules")) for d in self.g()["devices"])
        self.assertEqual((devs["d-00000001"], devs["d-00000002"]), ("all_exit", "ru_direct"))
        rc, doc, err = self.run_json("rules", "profile", "device", "d-00000003", "all_exit", "--confirm-not-rf",
                                     "--via", "mcp")
        self.assertEqual(rc, 0, err)

    def test_a_tick_that_would_write_still_needs_orgs(self):
        # the tick would write a consent (legacy; the block grew beyond the consent) — without orgs still refused
        self.node(gen=1)
        rops.set_blocks(self.w.runner, ads=False)
        self.assertEqual(rfgate.mode(state.load()), "legacy")
        spare = rops.rf_of(True, via="mcp", who="mcp", must_show=True)
        with self.assertRaises(errors.KaskadError) as e:
            rops.set_profile(self.w.runner, "device", "d-00000001", "all_exit", rf=spare)
        self.assertEqual((e.exception.code, e.exception.data["need"]), (errors.CONFLICT, "rf_consent"))
        self.assertNotIn("rf_consent", self.g())
        rops.set_profile(self.w.runner, "device", "d-00000001", "all_exit")      # without the phrase — fine
        rops.consent(self.w.runner, rops.rf_of(True, via="mcp", who="mcp", orgs=_all_orgs(), must_show=True))
        st = state.load()
        st["gateway"]["rf_consent"]["orgs"] = _all_orgs()[:1]               # the block grew since
        state.save(st)
        c0 = dict(self.g()["rf_consent"])
        with self.assertRaises(errors.KaskadError):
            rops.set_profile(self.w.runner, "device", "d-00000002", "all_exit", rf=spare)
        self.assertEqual(self.g()["rf_consent"], c0)

    def test_the_cli_by_hand_may_omit_orgs(self):
        rc, _, err = self.run_json("rules", "default", "ru_direct", "--confirm-not-rf")
        self.assertEqual(rc, 0, err)

    def test_bad_orgs_is_usage(self):
        for bad in ("Meta", "meta,,--x", "meta;fbk", "a" * 40):
            rc, _, _ = self.run_json("rules", "consent", "--confirm-not-rf", "--orgs", bad)
            self.assertEqual(rc, 2, bad)
        self.assertNotIn("rf_consent", state.load().get("gateway") or {})


class TextsTest(RfCase):
    def test_block_and_tick_word_for_word(self):
        for lang in ("ru", "en"):
            t = sh_texts(lang)
            self.assertEqual(t["GWR_RF_BLOCK"].replace("%s", "{names}"),
                             us(tr("rules.rf.block", lang, names="{names}")), lang)
            consent = tr("rules.rf.consent", lang)
            self.assertTrue(t["GWR_RF_YES"].endswith(consent[0].lower() + consent[1:]), (lang, t["GWR_RF_YES"]))
            self.assertIn(consent[0].lower() + consent[1:], t["GWR_RF_ASK"])
            # REV-338-sec: the menu with a broken registry says the refusal of the core, word for word
            self.assertEqual(t["GWR_RF_UNREAD"], us(tr("rules.rf.unread", lang)), lang)

    def test_same_keys_and_placeholders_no_cyrillic_in_en(self):
        ru, en = sh_texts("ru"), sh_texts("en")
        self.assertEqual(sorted(ru), sorted(en))
        for k in ru:
            self.assertEqual(ru[k].count("%s"), en[k].count("%s"), k)
            self.assertIsNone(re.search("[А-Яа-яЁё]", en[k]), k)
        self.assertIn("⚠RU", en["GWR_RF_LEGACY"])
        self.assertNotIn("⚠RF", " ".join(en.values()))

    def test_examples_without_ru_domains(self):
        for lang in ("ru", "en"):
            self.assertNotRegex(" ".join(sh_texts(lang).values()), r"\.ru\b")

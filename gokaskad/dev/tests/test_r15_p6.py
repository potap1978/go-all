"""3.3.7.15 П6 (stand/logs/T-3315.md, заметное и мелочи стенда, проход 2).

П6-1 (Заметно-2 п. 2.6): the address B of «напрямую» gone at the hoster — nft took its SNAT away within one round of
the collector, while xray kept ``sendThrough`` B of «direct» 40–90 s more (VLESS and Hysteria2 without the internet):
«direct» is the base of xray (a restart), and the collector held the restart back for the window of the storm
(RESTART_GAP_S after the last one — the write of the choice had just restarted it). Now an outbound that sends from
another address (``xray.sends_changed``) restarts in the same round as nft when the address is gone; back — through the
window of the storm (П7, REV-3315-p256 З-1: a flapping address restarted xray every round); a round without a change
restarts nothing; the restart a write of the panel owes to its answer (П3, ``xray-after.json``) still holds it.

П6-2 (мелочь п. 2.2): ``gw inbound undo sstp`` restarted accel-ppp (L2TP and PPTP cut) without a question and took no
``--yes``. Now «Вернуть прежние» asks where «Применить» asks for a shared service — accel-ppp with live sessions, the
xray of the gateway: CLI ``--dry``/``--yes``, API ``{yes}`` (409 ``need: "yes"`` with the plan), the page asks, the SSH
menu shows the plan before its question; the timer of F7 and a live wait of the protocol never ask.

П6-3 (мелочь п. 2.9): the text of ``give6`` named Hysteria2 among the IPv6 lines with no word of the mask — the code is
as SPEC-R1 F13 (Hysteria2 with the mask and AmneziaWG «программа» — only on the source of the IPv6 route); the text
says it now, RU/EN.

П7 (REV-3315-p256): З-1 — the address back waits for the window of the storm (a flapping address restarted xray every
round); Б-1 — «Вернуть прежние» that cuts the administrator's own link asks (``UndoAdminTest``); П-2 — the failed note
of the preview logs its own words (``PreviewNoteLogTest``)."""

import json
import os
import subprocess
import time
import urllib.parse
from unittest import mock

from kaskad import errors, i18n, state, util
from kaskad.errors import ConflictError
from kaskad.gw import exits, mip, ops, pending, registry, xray
from kaskad.gw.inbounds import accel, tunnel
from tests.panel.panelcase import PanelCase
from tests.test_3351_cmdinj import function
from tests.test_gw_ops import CliGwCase
from tests.test_mip_s34 import A_IP, B_IP, chain, two_addresses
from tests.test_r15_direct import DIRECT, Roles, outbounds
from tests.test_r15_sub6 import S6, X6, Sub6Case
from tests.test_r15_tls import TlsWorld


class SendsChangedTest(Roles):
    def restarts(self):
        return self.w.sysd.restarts.get(xray.UNIT, 0)

    def round_(self):
        self.ok("gw", "collect", "--once")

    def window_over(self):
        t = time.time() - xray.RESTART_GAP_S - 1                                # the last restart a window ago
        os.utime(xray._restart_stamp(), (t, t))

    def test_gone_in_the_same_round_as_nft_back_in_the_window(self):
        mip.set_direct(self.w.runner, B_IP)                    # restarts xray: the window of the storm is on now
        said = self.said()
        self.round_()
        self.assertEqual(outbounds()["direct"].get("sendThrough"), B_IP)
        self.assertFalse(xray.restart_due())                    # the case of the stand: a restart just made
        n = self.restarts()
        two_addresses(self, A_IP, None)                         # B gone at the hoster
        self.w.leg(2)
        self.round_()
        self.assertNotIn(DIRECT % B_IP, chain("gw_post"))
        self.assertNotIn("sendThrough", outbounds()["direct"])  # the same collect(): xray without B
        self.assertEqual(self.restarts(), n + 1)
        self.assertIn("gw_mip.log.direct_gone", said)
        text = util.read_text(xray.config_path())
        self.round_()                                           # nothing changed: no restart every tick
        self.assertEqual((self.restarts(), util.read_text(xray.config_path())), (n + 1, text))
        two_addresses(self)                                     # B back
        self.w.leg(2)
        self.round_()
        self.assertIn(DIRECT % B_IP, chain("gw_post"))
        self.assertIn("gw_mip.log.direct_back", said)
        # П7 (З-1): back — the window of the storm (xray sends from A meanwhile, nothing is cut), the file untouched
        self.assertEqual((self.restarts(), util.read_text(xray.config_path())), (n + 1, text))
        self.window_over()
        self.round_()
        self.assertEqual(outbounds()["direct"].get("sendThrough"), B_IP)
        self.assertEqual(self.restarts(), n + 2)
        self.round_()
        self.assertEqual(self.restarts(), n + 2)

    def test_a_flapping_address_restarts_xray_once_a_window(self):
        # П7 (REV-3315-p256 З-1, scratchpad rev_flap): B gone and back every round — was a restart every round (6 of 6)
        mip.set_direct(self.w.runner, B_IP)
        self.round_()
        n = self.restarts()
        for i in range(6):
            if i % 2 == 0:
                two_addresses(self, A_IP, None)                 # gone
            else:
                two_addresses(self)                             # back
            self.w.leg(2)
            self.round_()
            self.assertNotIn("sendThrough", outbounds()["direct"])   # never from B while it flaps: B came back late
        self.assertEqual(self.restarts() - n, 1)                # the first loss only
        self.window_over()                                      # B stays; the window passed — B again, one restart
        self.round_()
        self.assertEqual(outbounds()["direct"].get("sendThrough"), B_IP)
        self.assertEqual(self.restarts() - n, 2)

    def test_direct_and_an_exit_of_b_one_restart(self):
        self.ok("gw", "addresses", "exit", self.vid, B_IP)
        mip.set_direct(self.w.runner, B_IP)
        self.round_()
        tag = exits.tag(self.vid)
        self.assertEqual((outbounds()["direct"].get("sendThrough"), outbounds()[tag].get("sendThrough")),
                         (B_IP, B_IP))
        n = self.restarts()
        two_addresses(self, A_IP, None)
        self.w.leg(2)
        self.round_()
        self.assertNotIn("sendThrough", outbounds()["direct"])
        self.assertNotIn("sendThrough", outbounds()[tag])
        self.assertEqual(self.restarts(), n + 1)
        self.assertEqual(mip.src_map(state.load()), {self.vid: B_IP})          # the choice stays

    def test_the_answer_of_the_panel_still_holds_the_restart(self):
        mip.set_direct(self.w.runner, B_IP)
        self.round_()
        n = self.restarts()
        with open(xray._after_path(), "w", encoding="utf-8") as f:              # П3: a write owes its restart
            json.dump({"token": "a" * 16, "at": int(time.time())}, f)
        two_addresses(self, A_IP, None)
        self.w.leg(2)
        self.round_()
        self.assertNotIn(DIRECT % B_IP, chain("gw_post"))                      # nft in this round anyway
        self.assertEqual(outbounds()["direct"].get("sendThrough"), B_IP)        # xray held: the answer first
        self.assertEqual(self.restarts(), n)
        with open(xray._after_path(), "w", encoding="utf-8") as f:              # the hold is over (a dead panel)
            json.dump({"token": "a" * 16, "at": int(time.time()) - xray.AFTER_HOLD_S - 1}, f)
        self.round_()
        self.assertNotIn("sendThrough", outbounds()["direct"])
        self.assertEqual(self.restarts(), n + 1)

    def test_restart_due_and_sends_changed(self):
        os.makedirs(os.path.dirname(xray._restart_stamp()), exist_ok=True)
        with open(xray._restart_stamp(), "w", encoding="utf-8") as f:           # restarted a moment ago
            f.write("%d\n" % int(time.time()))
        self.assertFalse(xray.restart_due())                                    # any other change: the storm
        self.assertTrue(xray.restart_due(sends=True))
        with open(xray._after_path(), "w", encoding="utf-8") as f:
            json.dump({"token": "b" * 16, "at": int(time.time())}, f)
        self.assertFalse(xray.restart_due(sends=True))                          # П3 holds it still
        d = {"tag": "direct", "protocol": "freedom"}
        e = {"tag": "gx-e-1", "protocol": "vless"}

        def cfg(*obs):
            return {"outbounds": list(obs)}
        self.assertTrue(xray.sends_changed(cfg(dict(d, sendThrough=B_IP)), cfg(d)))           # gone
        self.assertFalse(xray.sends_changed(cfg(d), cfg(dict(d, sendThrough=B_IP))))          # back: the window (П7)
        self.assertTrue(xray.sends_changed(cfg(d, dict(e, sendThrough=B_IP)), cfg(d, dict(e, sendThrough=A_IP))))
        self.assertFalse(xray.sends_changed(cfg(dict(d, sendThrough=B_IP)), cfg(dict(d, sendThrough=B_IP))))
        self.assertFalse(xray.sends_changed(cfg(d, dict(e, sendThrough=B_IP)), cfg(d)))       # an exit goes
        self.assertFalse(xray.sends_changed(cfg(d), cfg(d, dict(e, sendThrough=B_IP))))       # an exit comes
        self.assertFalse(xray.sends_changed(None, cfg(d)))
        self.assertFalse(xray.sends_changed(cfg(d), "x"))


# ── П6-2 (мелочь п. 2.2): «Вернуть прежние» SSTP перезапускал accel-ppp без вопроса и не брал --yes ─────────────

ONE = [{"ifname": "kgw-pptp0", "type": "pptp"}]                                  # a live session of accel-ppp


class UndoWorld(TlsWorld):
    def accel(self, sessions):
        """accel-ppp here (the dummy SSTP) with ``sessions`` live."""
        here = tunnel.unit_installed
        for p in (mock.patch.object(tunnel, "unit_installed", lambda n: n == accel.UNIT or here(n)),
                  mock.patch.object(accel, "sessions", return_value=sessions)):
            p.start()
            self.addCleanup(p.stop)

    def sstp_13(self, sessions=ONE):
        self.world()
        self.accel(sessions)
        ops.inbound_settings(self.w.runner, "sstp", tls="1.3", yes=True)
        self.assertEqual(self.rec("sstp")["tls"], {"versions": "1.3"})
        return ops._last_read()["sstp"]["id"]


class UndoAsksTest(UndoWorld, CliGwCase):
    def test_sstp_with_a_live_session_asks_dry_and_yes(self):
        aid = self.sstp_13()
        with self.assertRaises(ConflictError) as cm:
            ops.inbound_settings_undo(self.w.runner, "sstp")
        self.assertEqual(cm.exception.data["need"], "yes")
        text = str(cm.exception)
        self.assertIn("SSTP: вернуть прежние (TLS только 1.3 → 1.2 и 1.3) — нужно подтверждение.", text)
        self.assertIn("перезапуск accel-ppp", text)
        self.assertIn("Оборвётся 1 подключение (SSTP, L2TP, PPTP).", text)
        self.assertEqual(self.rec("sstp")["tls"], {"versions": "1.3"})                # nothing applied …
        self.assertEqual(ops._last_read()["sstp"]["id"], aid)                          # … the record stays
        plan = ops.inbound_settings_undo(self.w.runner, "sstp", dry=True)
        self.assertTrue(plan["dry"] and plan["need_yes"])
        self.assertEqual(plan["what"], "TLS только 1.3 → 1.2 и 1.3")
        self.assertEqual([r["word"] for r in plan["plan"]["restart"]["rows"]], ["sstp"])
        self.assertIn("Оборвётся 1 подключение (SSTP, L2TP, PPTP).", plan["lines"])
        self.assertEqual((self.rec("sstp")["tls"], ops._last_read()["sstp"]["id"]), ({"versions": "1.3"}, aid))
        # the CLI of the stand: without --yes — CONFLICT and the hint; --dry — the plan; --yes — back
        rc, doc, _ = self.run_json("gw", "inbound", "undo", "sstp")
        self.assertEqual(rc, errors.CONFLICT)
        self.assertIn("Вернуть прежние — с --yes.", doc["error"])
        out, _ = self.ok("gw", "inbound", "undo", "sstp", "--dry")
        self.assertIn("SSTP: вернуть прежние — TLS только 1.3 → 1.2 и 1.3", out)
        self.assertIn("  Оборвётся 1 подключение (SSTP, L2TP, PPTP).", out)
        self.assertEqual(self.rec("sstp")["tls"], {"versions": "1.3"})
        out, _ = self.ok("gw", "inbound", "undo", "sstp", "--yes")
        self.assertIn("SSTP: прежние настройки возвращены — TLS только 1.3 → 1.2 и 1.3.", out)
        self.assertNotIn("tls", self.rec("sstp"))
        self.assertNotIn("sstp", ops._last_read())

    def test_no_live_session_no_question_as_set(self):
        self.sstp_13(sessions=[])
        plan = ops.inbound_settings_undo(self.w.runner, "sstp", dry=True)
        self.assertFalse(plan["need_yes"])
        self.assertFalse([x for x in plan["lines"] if "Оборв" in x])
        self.assertIn("перезапуск accel-ppp", " ".join(plan["lines"]))                # said, not asked
        res = ops.inbound_settings_undo(self.w.runner, "sstp")
        self.assertEqual(res["undone"], {"tls": {"old": "1.3", "new": "1.2-1.3"}})

    def test_a_kind_of_its_own_service_never_asks(self):
        self.world()
        self.accel(ONE)
        ops.inbound_settings(self.w.runner, "ocserv", tls="1.3", yes=True)
        self.assertFalse(ops.inbound_settings_undo(self.w.runner, "ocserv", dry=True)["need_yes"])
        self.assertEqual(ops.inbound_settings_undo(self.w.runner, "ocserv")["undone"],
                         {"tls": {"old": "1.3", "new": "1.2-1.3"}})
        ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True)
        self.assertEqual(ops.inbound_settings_undo(self.w.runner, "wg")["undone"]["port"]["new"], 51820)

    def test_the_timer_and_a_live_wait_never_ask(self):
        aid = self.sstp_13()
        res = ops.inbound_settings_undo(self.w.runner, "sstp", pending={"id": aid})   # the timer of F7
        self.assertNotIn("tls", self.rec("sstp"))
        self.assertTrue(res["ok"])
        ops.inbound_settings(self.w.runner, "sstp", tls="1.3", yes=True)
        gone = []
        with mock.patch.object(pending, "view", return_value={"scope": "sstp", "token": "t"}), \
                mock.patch.object(pending, "disarm", lambda runner: gone.append(1)):
            ops.inbound_settings_undo(self.w.runner, "sstp")                         # «Вернуть прежние сейчас»
        self.assertEqual((gone, self.rec("sstp").get("tls")), ([1], None))
        ops.inbound_settings(self.w.runner, "sstp", tls="1.3", yes=True)
        with mock.patch.object(pending, "view", return_value={"scope": "wg", "token": "t"}), \
                mock.patch.object(pending, "disarm", lambda runner: gone.append(2)):
            with self.assertRaises(ConflictError):                                   # a wait of another: asks
                ops.inbound_settings_undo(self.w.runner, "sstp")
        self.assertEqual((gone, self.rec("sstp")["tls"]), ([1], {"versions": "1.3"}))   # never disarmed

    def test_a_moved_record_is_kept_by_the_dry(self):
        self.sstp_13()
        with state.transaction() as st:
            st["gateway"]["inbounds"]["sstp"]["tls"] = {"versions": "1.2"}
        with self.assertRaises(ConflictError) as cm:
            ops.inbound_settings_undo(self.w.runner, "sstp", dry=True)
        self.assertEqual(cm.exception.data["why"], "moved")
        self.assertIn("sstp", ops._last_read())                                       # the plan writes nothing
        with self.assertRaises(ConflictError):
            ops.inbound_settings_undo(self.w.runner, "sstp", yes=True)
        self.assertNotIn("sstp", ops._last_read())                                    # as before: the record goes

    def test_the_words_ru_en(self):
        for lang in ("ru", "en"):
            cat = i18n.catalog(lang)
            for key in ("gw_ops.settings.undo_need_yes", "gw_cli.settings.undo_need_yes", "gw_cli.settings.undo_plan",
                        "gw_cli.help.undo_yes"):
                self.assertTrue(cat.get(key), (lang, key))
        self.assertIn("--yes", i18n.catalog("en")["gw_cli.settings.undo_need_yes"])
        rc, out, err = self.run_cli("gw", "inbound", "undo", "--help")
        self.assertEqual(rc, 0, err)
        self.assertIn("--yes", out)
        self.assertIn("--dry", out)


class UndoApiTest(UndoWorld, PanelCase, CliGwCase):
    def test_post_undo_409_with_the_plan_then_yes(self):
        self.sstp_13()
        need = self.err(409, "POST", "/api/gw/inbounds/sstp/settings/undo", {})
        self.assertEqual(need["data"]["need"], "yes")
        self.assertIn("Оборвётся 1 подключение (SSTP, L2TP, PPTP).", need["data"]["plan"]["lines"])
        self.assertEqual(self.rec("sstp")["tls"], {"versions": "1.3"})
        self.err(400, "POST", "/api/gw/inbounds/sstp/settings/undo", {"yes": "да"})
        done = self.ok("POST", "/api/gw/inbounds/sstp/settings/undo", {"yes": True})
        self.assertEqual(done["undone"], {"tls": {"old": "1.3", "new": "1.2-1.3"}})
        self.assertNotIn("tls", self.rec("sstp"))

    def test_the_page_asks_and_sends_yes(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "lib", "kaskad", "panel", "static", "app.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn('setUndo: (k, yes) => request("POST", "/api/gw/inbounds/" + enc(k) + "/settings/undo", '
                      'yes ? { yes: true } : {}', js)
        part = js[js.index("function psLastNode("):js.index("function psDraw(")]
        self.assertIn('e.data.need === "yes"', part)
        self.assertIn("confirmDialog(", part)
        self.assertIn("go(true)", part)
        for lang in ("ru", "en"):
            with open(os.path.join(here, "..", "lib", "kaskad", "panel", "static", "i18n-%s.js" % lang),
                      encoding="utf-8") as f:
                words = f.read()
            self.assertIn('"ps.undoConfT"', words)
            self.assertIn('"ps.undoConfX"', words)


class UndoAdminTest(UndoWorld, CliGwCase):
    """П7 (REV-3315-p256 Б-1): «Вернуть прежние», что обрывает связь самого администратора (его адрес — в пуле
    протокола, возвращается порт или TLS), спрашивает, как «Применить», — своими словами: у возврата нет таймера.
    MTU сам по себе связь не рвёт; чужой адрес и консоль без SSH — без вопроса."""

    ADMIN = {"via": "panel", "ip": "10.77.1.10"}                # a client of WireGuard: its pool

    def hit(self, lang="ru", what=None):
        return i18n.catalog(lang)["gw_ops.settings.undo_hit"].format(what=what or registry.title("wg"))

    def wg_port(self):
        self.world()
        ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True)        # from the console: no timer
        return ops._last_read()["wg"]["id"]

    def test_the_undo_that_cuts_the_admin_asks(self):
        aid = self.wg_port()
        with self.assertRaises(ConflictError) as cm:
            ops.inbound_settings_undo(self.w.runner, "wg", who=self.ADMIN)
        self.assertEqual(cm.exception.data["need"], "yes")
        self.assertEqual(cm.exception.data["plan"]["admin_hit"], "pool")
        self.assertIn(self.hit(), str(cm.exception))
        self.assertEqual((self.rec("wg")["port"], ops._last_read()["wg"]["id"]), (51999, aid))   # nothing undone
        plan = ops.inbound_settings_undo(self.w.runner, "wg", dry=True, who=self.ADMIN)
        self.assertTrue(plan["need_yes"])
        self.assertEqual(plan["plan"]["hit_text"], self.hit())
        self.assertIn(self.hit(), plan["lines"])
        for who in ({"via": "panel", "ip": "198.51.100.7"}, None):          # another address, the console
            self.assertFalse(ops.inbound_settings_undo(self.w.runner, "wg", dry=True, who=who)["need_yes"], who)
        res = ops.inbound_settings_undo(self.w.runner, "wg", yes=True, who=self.ADMIN)
        self.assertEqual(res["undone"]["port"], {"old": 51999, "new": 51820})

    def test_mtu_alone_never_cuts(self):
        self.world()
        ops.inbound_settings(self.w.runner, "wg", mtu=1300, yes=True)
        plan = ops.inbound_settings_undo(self.w.runner, "wg", dry=True, who=self.ADMIN)
        self.assertFalse(plan["need_yes"])
        self.assertFalse(plan["plan"]["admin_hit"])

    def test_the_cli_and_the_menu_take_ssh_connection(self):
        self.wg_port()
        with mock.patch.dict(os.environ, {"SSH_CONNECTION": "10.77.1.10 50000 203.0.113.1 22"}):
            rc, doc, _ = self.run_json("gw", "inbound", "undo", "wg")
            self.assertEqual(rc, errors.CONFLICT)
            self.assertIn(self.hit(), doc["error"])
            self.assertIn("Вернуть прежние — с --yes.", doc["error"])
            out, _ = self.ok("gw", "inbound", "undo", "wg", "--dry")            # the SSH menu: the plan first
            self.assertIn("  " + self.hit(), out)
            self.assertEqual(self.rec("wg")["port"], 51999)
            out, _ = self.ok("gw", "inbound", "undo", "wg", "--yes")
        self.assertEqual(self.rec("wg")["port"], 51820)

    def test_the_words_ru_en(self):
        for lang in ("ru", "en"):
            words = self.hit(lang, "WireGuard")
            self.assertIn("WireGuard", words)
            self.assertIn({"ru": "2 мин", "en": "2 min"}[lang], words)
            self.assertNotEqual(words, i18n.catalog(lang)["gw_pending.hit.pool"].format(what="WireGuard"))


class UndoAdminApiTest(UndoWorld, PanelCase, CliGwCase):
    def test_the_panel_passes_its_client_and_asks(self):
        self.world()
        ops.inbound_settings(self.w.runner, "wg", port=51999, yes=True)
        real, seen = pending.admin_hit, []

        def hit(st_old, st_new, scope, who, changed=None):                      # the client of the test is 127.0.0.1
            seen.append(who)
            return real(st_old, st_new, scope, dict(who or {}, ip="10.77.1.10"), changed)
        with mock.patch.object(pending, "admin_hit", hit):
            need = self.err(409, "POST", "/api/gw/inbounds/wg/settings/undo", {})
            self.assertEqual(need["data"]["need"], "yes")
            words = i18n.catalog("ru")["gw_ops.settings.undo_hit"].format(what=registry.title("wg"))
            self.assertIn(words, need["data"]["plan"]["lines"])                 # the page shows them in its dialog
            self.assertEqual(self.rec("wg")["port"], 51999)
            done = self.ok("POST", "/api/gw/inbounds/wg/settings/undo", {"yes": True})
        self.assertEqual(done["undone"]["port"]["new"], 51820)
        self.assertEqual(seen[0]["via"], "panel")
        self.assertTrue(seen[0]["ip"])


class PreviewNoteLogTest(Sub6Case):
    """П7 (REV-3315-p256 П-2): a failed note of «Скачать мои новые настройки» (issue-preview.json) — its own words in the
    log, not those of the mark «ждут новых настроек» (nobody touched a mark)."""

    def test_its_own_key(self):
        said = []
        with mock.patch.object(ops, "_preview_read", side_effect=OSError("disk")), \
                mock.patch.object(util, "log_tr", lambda key, **kw: said.append(key)):
            ops._preview_note("wg", "d-00000001", {})
            self.assertIsNone(ops._preview_given("wg", {}))
        self.assertEqual(said, ["gw_ops.log.preview_note"] * 2)
        for lang in ("ru", "en"):
            self.assertIn("issue-preview.json", i18n.catalog(lang)["gw_ops.log.preview_note"], lang)


class UndoMenuTest(UndoWorld, CliGwCase):
    """The SSH menu (F9): «Вернуть прежние» — ``--dry`` first (what restarts, whom it cuts), the question after it, then
    ``--yes`` (the question of the menu is the confirmation, as «Сменить порт»); «Отмена» — nothing undone."""

    STUBS = r"""
t() { printf '%s' "$1"; }
tf() { local k="$1"; shift; printf '%s:%s' "$k" "$*"; }
k3_gw_fold() { printf '%s%s\n' "$1" "$2"; }
k3_gw_pause() { :; }
k3_gw_show_warn() { :; }
k3_choose() { echo "ASK:$1" >&2; echo "$ANS"; }
k3_gw() {
    echo "CALL:$*" >&2
    K3_GW_RC=0; K3_GW_ERR=""
    case " $* " in
        *" --dry "*) K3_GW_OUT='{"ok":true,"dry":true,"need_yes":true,"lines":["Порт: перезапуск accel-ppp","Оборвётся 1 подключение"]}' ;;
        *" --yes "*) K3_GW_OUT='{"ok":true,"text":"SSTP: прежние настройки возвращены"}' ;;
        *) K3_GW_RC=4; K3_GW_ERR="нужно подтверждение"; K3_GW_OUT='{}'; return 4 ;;
    esac
}
RED=""; GREEN=""; NC=""
"""

    def run_menu(self, ans):
        names = ("k3_jq", "k3_term_esc", "k3_gw_proto_undo")
        src = "".join(function(n) for n in names) + self.STUBS + 'k3_gw_proto_undo sstp SSTP "TLS только 1.3 → 1.2 и 1.3"\n'
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8", "ANS": ans}
        r = subprocess.run(["bash", "-c", src], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout + r.stderr

    def test_dry_then_the_question_then_yes(self):
        out = self.run_menu("1")
        calls = [ln for ln in out.splitlines() if ln.startswith("CALL:")]
        self.assertEqual(calls, ["CALL:inbound undo sstp --dry", "CALL:inbound undo sstp --yes"])
        self.assertLess(out.index("Оборвётся 1 подключение"), out.index("ASK:GWP_UNDO_Q"))     # read before the choice
        self.assertIn("[OK] SSTP: прежние настройки возвращены", out)
        out = self.run_menu("2")                                                           # «Отмена»
        self.assertEqual([ln for ln in out.splitlines() if ln.startswith("CALL:")], ["CALL:inbound undo sstp --dry"])



# ── П6-3 (мелочь п. 2.9): текст give6 называл Hysteria2 без оговорки, а у Hy2 с маскировкой строки IPv6 не было ────


class Give6WordsTest(Sub6Case):
    def test_the_text_says_who_answers_from_the_route_address_only(self):
        rc, doc, err = self.run_json("gw", "addresses", "give6", S6, "--yes")    # not the source of the IPv6 route
        self.assertEqual(rc, 0, err)
        self.assertIn("(Hysteria2 с маскировкой и AmneziaWG на движке «программа» — только если это адрес маршрута IPv6)",
                      doc["text"])
        self.assertTrue([w for w in doc["warnings"] if "отвечают только с адреса маршрута IPv6" in w], doc["warnings"])
        six = [ln for ln in self.sub() if " · IPv6" in urllib.parse.unquote(ln)]
        self.assertTrue(six)
        self.assertFalse([ln for ln in six if ln.startswith("hysteria2://")], six)   # the code as SPEC-R1 F13: none
        rc, doc, err = self.run_json("gw", "addresses", "give6", X6, "--yes")    # the source: Hysteria2 too
        self.assertEqual(rc, 0, err)
        self.assertTrue([ln for ln in self.sub() if ln.startswith("hysteria2://") and "[%s]" % X6 in ln])
        en = i18n.catalog("en")["gw_mip.give6.set"]
        self.assertIn("Hysteria2 with the mask and AmneziaWG on the userspace AmneziaWG engine — only when it is the "
                      "IPv6 route address", en)
        for lang in ("ru", "en"):                                                # the words of the limits themselves
            cat = i18n.catalog(lang)
            self.assertIn(cat["gw_mip.limit.hy2"], cat["gw_mip.give6.set"], lang)
            # the same words as the warning (gw_mip.warn.give6_limits via mip.py: limit.awg with the title)
            self.assertIn(cat["gw_mip.limit.awg"].format(title="AmneziaWG"), cat["gw_mip.give6.set"], lang)

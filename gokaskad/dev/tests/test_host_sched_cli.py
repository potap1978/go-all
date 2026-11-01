"""B14 (k3/handoff/SPEC-node-score-schedule-share.md §6.1, §6.3, §2.8 п. 5): ``kaskadctl host bench schedule|history|
show|compare|export`` and ``host set --bench-retry/--dev-threshold/--dev-spread`` over the same functions as the API of
the panel (``host_sched``): the same errors of the checker, the same price and version; exit codes 0 / 1 / 2 / 3; heavy —
a question in a terminal, a refusal without one (or with --by-mcp: MCP asks no questions); the agent saves only with the
``code`` of its preview."""

import json
import os
from unittest import mock

from kaskad import host_sched, state
from kaskad.bench import history, sched_unit
from tests.panel.test_host_bench_api import HistoryCase, ent
from tests.test_host_cli import run_cli, run_json

WEEK = [1, 2, 3, 4, 5, 6, 7]


class SchedCliCase(HistoryCase):
    def setUp(self):
        super().setUp()
        host_sched.reset()
        self.addCleanup(host_sched.reset)

    def sched(self):
        return self.get().json()["schedule"]

    def add(self, *more, t="12:00", mode="quick", days="all"):
        return run_json("host", "bench", "schedule", "add", "--time", t, "--mode", mode, "--days", days, *more)


class ListTest(SchedCliCase):
    def test_list_is_the_answer_of_the_panel(self):
        self.bench([ent("03:45"), ent("12:00", "full", [6])])
        rc, doc, _ = run_json("host", "bench", "schedule", "list")
        self.assertEqual(rc, 0)
        panel = self.get().json()
        for k in ("schedule", "etag", "cost", "next", "armed", "retry", "limits", "tz"):
            self.assertEqual(doc[k], panel[k], k)
        rc, text, _ = run_cli("host", "bench", "schedule")
        self.assertEqual(rc, 0)
        self.assertIn(" 1) 03:45 · быстрая · каждый день · только когда свободен · включена", text)
        self.assertIn(" 2) 12:00 · полная · Сб", text)
        self.assertIn("Следующая: ", text)
        self.assertIn("Цена: ≈ ", text)
        rc, doc, _ = run_json("host", "bench", "schedule", "cost")
        self.assertEqual(doc["cost"], panel["cost"])

    def test_english(self):
        self.bench([ent("03:45")])
        with mock.patch.dict(os.environ, {"KASKAD_LANG": "en"}):
            rc, text, _ = run_cli("host", "bench", "schedule", "list")
        self.assertEqual(rc, 0, text)
        self.assertRegex(text, r"03:45 · quick · every day · only when free · on")


class ChangeTest(SchedCliCase):
    def test_add_dry_saves_nothing_then_add(self):
        self.bench([ent("03:45")])
        self.get()                                       # the ids of a fix are saved by the read itself (as GET)
        before = self.state_bytes()
        rc, doc, _ = self.add("--dry")
        self.assertEqual(rc, 0)
        self.assertTrue(doc["dry"])
        self.assertRegex(doc["code"], r"^[0-9a-f]{10}$")
        self.assertEqual([e["time"] for e in doc["schedule"]], ["03:45", "12:00"])
        self.assertEqual(self.state_bytes(), before)
        rc, doc, _ = self.add()
        self.assertEqual(rc, 0, doc)
        self.assertEqual([e["time"] for e in self.sched()], ["03:45", "12:00"])
        self.assertTrue(doc["apply"]["applied"])

    def test_the_checker_is_the_panels(self):
        """The same body, the same errors and texts as the preview of the panel (one function)."""
        self.bench([ent("12:00")])
        rc, doc, err = self.add(t="13:00")
        self.assertEqual(rc, 2, doc)
        panel = self.call("POST", "/api/host/bench/schedule/preview", {"schedule": [
            dict(ent("12:00"), id=self.get().json()["schedule"][0]["id"]), ent("13:00")]}).json()
        self.assertEqual(doc["data"]["errors"], panel["errors"])
        self.assertEqual(doc["data"]["key"], "invalid")
        self.assertEqual(len(self.sched()), 1)
        rc, doc, _ = self.add(t="25:00")
        self.assertEqual(rc, 2)
        self.assertEqual([e["code"] for e in doc["data"]["errors"]], ["time"])

    def test_monthly_needs_one_day(self):
        self.bench([])
        rc, doc, _ = self.add("--monthly", days="1,3")
        self.assertEqual(rc, 2)
        self.assertEqual(doc["data"]["errors"][0]["code"], "monthly_days")
        rc, doc, _ = self.add("--monthly", days="6")
        self.assertEqual(rc, 0, doc)
        self.assertTrue(self.sched()[0]["monthly"])

    def test_flags_off_and_no_quiet(self):
        self.bench([])
        self.assertEqual(self.add("--off", "--no-quiet", days="1-5")[0], 0)
        e = self.sched()[0]
        self.assertEqual((e["on"], e["quiet"], e["days"]), (False, False, [1, 2, 3, 4, 5]))

    def test_heavy_needs_consent(self):
        self.bench([ent("03:00", "full")])
        self.get()
        before = self.state_bytes()
        rc, doc, _ = self.add(mode="full")
        self.assertEqual(rc, 2, doc)
        self.assertEqual(doc["data"]["key"], "need_confirm")
        self.assertIn("heavy", [n["code"] for n in doc["data"]["cost"]["notes"]])
        self.assertEqual(self.state_bytes(), before)
        rc, doc, _ = self.add("--yes", mode="full")
        self.assertEqual(rc, 0, doc)
        self.assertEqual(len(self.sched()), 2)

    def test_heavy_in_a_terminal_is_a_question_by_numbers(self):
        self.bench([ent("03:00", "full")])
        with mock.patch.object(host_sched, "_isatty", return_value=True), \
                mock.patch("sys.stdin.readline", return_value="2\n"):
            rc, text, _ = run_cli("host", "bench", "schedule", "add", "--time", "12:00", "--mode", "full", "--days", "all")
        self.assertEqual(rc, 1)
        self.assertIn("1) Сохранить", text)
        self.assertIn("2) Отмена", text)
        self.assertIn("Выбор [1]:", text)
        self.assertNotRegex(text, r"\[[yY]/[nN]\]")
        self.assertEqual(len(self.sched()), 1)
        with mock.patch.object(host_sched, "_isatty", return_value=True), \
                mock.patch("sys.stdin.readline", return_value="1\n"):
            rc, _text, _ = run_cli("host", "bench", "schedule", "add", "--time", "12:00", "--mode", "full", "--days", "all")
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.sched()), 2)

    def test_rm_on_off_by_number_and_id(self):
        self.bench([ent("03:45"), ent("12:00")])
        sid = self.get().json()["schedule"][1]["id"]
        self.assertEqual(run_json("host", "bench", "schedule", "off", "2")[0], 0)
        self.assertIs(self.sched()[1]["on"], False)
        self.assertEqual(run_json("host", "bench", "schedule", "on", sid)[0], 0)
        self.assertIs(self.sched()[1]["on"], True)
        self.assertEqual(run_json("host", "bench", "schedule", "rm", "1")[0], 0)
        self.assertEqual([e["time"] for e in self.sched()], ["12:00"])
        for bad in ("3", "0", "abcdef", "../x"):
            rc, doc, _ = run_json("host", "bench", "schedule", "rm", bad)
            self.assertEqual(rc, 2, bad)
            self.assertEqual(doc["data"]["key"], "no_entry")
        self.assertNotIn("../x", run_json("host", "bench", "schedule", "rm", "../x")[1]["error"])

    def test_retry_off_by_host_set_stops_the_repeats(self):
        self.bench([])
        with mock.patch.object(sched_unit, "stop_retries") as stop:
            rc, doc, _ = run_json("host", "set", "--bench-retry", "off")
            self.assertEqual(rc, 0, doc)
            self.assertEqual(doc["changed"], ["bench_retry"])
            self.assertEqual(stop.call_count, 1)
            self.assertIs(self.saved()["retry"], False)
            self.assertIs(self.get().json()["retry"], False)
            self.assertEqual(run_json("host", "set", "--bench-retry", "off")[1]["changed"], [])
            self.assertEqual(stop.call_count, 1)
            self.assertEqual(run_json("host", "set", "--bench-retry", "on")[1]["changed"], ["bench_retry"])
        self.assertEqual(run_json("host", "set", "--bench-retry", "maybe")[0], 2)


class McpChangeTest(SchedCliCase):
    """--by-mcp (the tool host_bench_schedule): the first call is the preview with its code; the save only with that
    code; heavy is refused (SS §6.3)."""

    def test_preview_then_the_code(self):
        self.bench([ent("03:45")])
        self.get()
        before = self.state_bytes()
        rc, doc, _ = self.add("--by-mcp")
        self.assertEqual(rc, 0)
        self.assertTrue(doc["dry"])
        self.assertEqual(self.state_bytes(), before)
        rc, bad, _ = self.add("--by-mcp", "--code", "0" * 10)
        self.assertEqual(rc, 3)
        self.assertEqual(bad["data"]["key"], "code_mismatch")
        rc, other, _ = self.add("--by-mcp", "--code", doc["code"], t="14:00")      # another change, the same code
        self.assertEqual(rc, 3)
        self.assertEqual(self.state_bytes(), before)
        rc, ok, _ = self.add("--by-mcp", "--code", doc["code"])
        self.assertEqual(rc, 0, ok)
        self.assertEqual(len(self.sched()), 2)
        rc, again, _ = self.add("--by-mcp", "--code", doc["code"])      # the version moved: the old code is no more
        self.assertEqual(rc, 3)

    def test_the_schedule_changed_meanwhile(self):
        self.bench([ent("03:45")])
        _rc, doc, _ = run_json("host", "bench", "schedule", "off", "1", "--by-mcp")
        self.assertEqual(run_json("host", "set", "--bench-retry", "off")[0], 0)            # another window
        rc, got, _ = run_json("host", "bench", "schedule", "off", "1", "--by-mcp", "--code", doc["code"])
        self.assertEqual(rc, 3)
        self.assertIs(self.sched()[0]["on"], True)

    def test_heavy_is_refused_even_with_the_code(self):
        self.bench([ent("03:00", "full")])
        _rc, doc, _ = self.add("--by-mcp", mode="full")
        self.assertTrue(doc["heavy"])
        rc, got, _ = self.add("--by-mcp", "--code", doc["code"], "--yes", mode="full")
        self.assertEqual(rc, 2, got)
        self.assertEqual(got["data"]["key"], "heavy")
        self.assertEqual(len(self.sched()), 1)

    def test_catalog(self):
        """REV-ns-b14 З-1, З-3: the writes — one tool host_bench_schedule (action add|on|off|rm), «ask the human»
        (ask_human), rm — destructive; the reads — actions of host_score; the history — 50 rows by default (П-2)."""
        from tests.test_mcp import TOOLS
        w = TOOLS["host_bench_schedule"]
        self.assertTrue(w["ask_human"])
        self.assertFalse(w["readonly"])
        self.assertNotIn("--yes", json.dumps(w))
        for op, v in w["by_action"].items():
            self.assertEqual(v["cmd"], ["host", "bench", "schedule", op], op)
            self.assertEqual(v["args"], ["--by-mcp"], op)
            self.assertIn("code", v["params"], op)
            self.assertEqual(bool(v.get("destructive")), op == "rm", op)
        r = TOOLS["host_score"]["by_action"]
        for op in ("history", "show", "schedule"):
            self.assertTrue(r[op]["readonly"], op)
        self.assertEqual(r["history"]["args"], ["--limit=50"])
        self.assertNotIn("ask_human", TOOLS["host_set"])


# A heavy list as usual: one daily full check is ≈ 121 GB a month already (REV-ns-b14 P4) — over 100 GB.
HEAVY = [ent("03:00", "full"), ent("12:00")]


class ConsentOnGrowthTest(SchedCliCase):
    """REV-ns-b14 С-1: «much traffic» asks only when the change makes the traffic of a month grow; to turn off, delete
    or make lighter a heavy list — never (MCP, kaskadctl without a terminal, the menu, the panel — one rule)."""

    def test_mcp_turns_off_and_deletes_in_a_heavy_list(self):
        self.bench(list(HEAVY))
        for op, ref in (("off", "2"), ("rm", "2"), ("rm", "1")):
            rc, pv, _ = run_json("host", "bench", "schedule", op, ref, "--by-mcp")
            self.assertEqual(rc, 0, pv)
            self.assertTrue(pv["dry"])
            self.assertFalse(pv["consent"], (op, ref))
            rc, got, _ = run_json("host", "bench", "schedule", op, ref, "--by-mcp", "--code", pv["code"])
            self.assertEqual(rc, 0, (op, ref, got))
        self.assertEqual(self.sched(), [])

    def test_mcp_cannot_make_it_grow(self):
        self.bench(list(HEAVY))
        _rc, pv, _ = self.add("--by-mcp", t="18:00")
        self.assertTrue(pv["heavy"])
        self.assertTrue(pv["consent"])
        rc, got, _ = self.add("--by-mcp", "--code", pv["code"], t="18:00")
        self.assertEqual(rc, 2, got)
        self.assertEqual(got["data"]["key"], "heavy")
        self.assertEqual(len(self.sched()), 2)
        # REV-ns-p3a П-5: the refusal names the whole command of the terminal, with its subcommand and values
        self.assertIn("kaskadctl host bench schedule add --time 18:00 --mode quick --days all.", json.dumps(got,
                      ensure_ascii=False))
        self.assertNotIn("{cmd}", json.dumps(got, ensure_ascii=False))

    def test_mcp_refusal_names_the_subcommand(self):
        """REV-ns-p3a П-5: «on N» refused (heavy) — «kaskadctl host bench schedule on N», not the bare list command;
        the days as the flag takes them; a value off the rules — the subcommand with --help."""
        from kaskad import host_sched as hs
        self.bench([ent("03:00", "full"), dict(ent("12:00"), on=False)])
        rc, pv, _ = run_json("host", "bench", "schedule", "on", "2", "--by-mcp")
        self.assertTrue(pv["consent"], pv)
        rc, got, _ = run_json("host", "bench", "schedule", "on", "2", "--by-mcp", "--code", pv["code"])
        self.assertEqual(rc, 2, got)
        self.assertIn("kaskadctl host bench schedule on 2.", json.dumps(got, ensure_ascii=False))
        cur = {"schedule": [ent("03:00")]}
        e = dict(ent("04:05", "full", [1, 3, 7]), quiet=False, monthly=True)
        self.assertEqual(hs.terminal_cmd("add", None, e, cur), "kaskadctl host bench schedule add --time 04:05 "
                         "--mode full --days 1,3,7 --no-quiet --monthly")
        self.assertEqual(hs.terminal_cmd("add", None, dict(e, time="4:05; id"), cur),
                         "kaskadctl host bench schedule add --help")
        self.assertEqual(hs.terminal_cmd("on", "9", None, cur), "kaskadctl host bench schedule on --help")

    def test_cli_without_a_terminal(self):
        self.bench(list(HEAVY))
        self.assertEqual(run_json("host", "bench", "schedule", "off", "1")[0], 0)        # no --yes: it gets lighter
        self.assertIs(self.sched()[0]["on"], False)
        rc, doc, _ = run_json("host", "bench", "schedule", "on", "1")                     # grows: --yes or no
        self.assertEqual(rc, 2, doc)
        self.assertEqual(doc["data"]["key"], "need_confirm")
        self.assertIs(self.sched()[0]["on"], False)
        self.assertEqual(run_json("host", "bench", "schedule", "on", "1", "--yes")[0], 0)
        self.assertEqual(run_json("host", "bench", "schedule", "rm", "2")[0], 0)
        self.assertEqual([e["time"] for e in self.sched()], ["03:00"])

    def test_the_panel_by_the_same_rule(self):
        self.bench(list(HEAVY))
        cur = self.get().json()["schedule"]
        self.assertIs(self.ok("POST", "/api/host/bench/schedule/preview", {"schedule": cur[:1]})["consent"], False)
        self.assertIs(self.ok("POST", "/api/host/bench/schedule/preview",
                              {"schedule": cur + [ent("18:00")]})["consent"], True)
        self.assertEqual(self.put(cur[:1]).status, 200)                       # one deleted: no confirm
        r = self.put(self.get().json()["schedule"] + [ent("18:00")])
        self.assertEqual(r.status, 409, r)
        self.assertEqual(r.json()["code"], "need_confirm")
        self.bench(list(HEAVY), retry=False)
        r = self.put(self.get().json()["schedule"], retry=True)              # the repeats: up to 8 GB a day more
        self.assertEqual(r.status, 409, r)
        self.assertEqual(self.put(self.get().json()["schedule"], retry=True, confirm=True).status, 200)
        self.assertEqual(self.put(self.get().json()["schedule"], retry=False).status, 200)

    def test_the_rule(self):
        light = {"month": {"rx_mb": 100.0, "tx_mb": 0.0}, "notes": []}
        big = {"month": {"rx_mb": 120000.0, "tx_mb": 0.0}, "notes": [{"code": "heavy"}]}
        bigger = {"month": {"rx_mb": 121000.0, "tx_mb": 0.0}, "notes": [{"code": "heavy"}]}
        self.assertTrue(host_sched.needs_consent(light, big))
        self.assertTrue(host_sched.needs_consent(big, bigger))
        self.assertFalse(host_sched.needs_consent(bigger, big))
        self.assertFalse(host_sched.needs_consent(big, big))
        self.assertFalse(host_sched.needs_consent(big, light))
        self.assertTrue(host_sched.needs_consent(big, dict(big, retry={"max_mb": 8000})))


class RetryConsentTest(SchedCliCase):
    """REV-ns-b14-2 З-1: ``host set --bench-retry on`` with a heavy list — the same rule С-1 as the panel and the
    schedule: MCP — the preview shows the price and ``consent``, the code is refused (heavy); kaskadctl without a
    terminal — ``need_confirm`` without --yes; in a terminal — the question. A light list — at once."""

    def test_mcp_preview_and_refusal(self):
        self.bench(list(HEAVY), retry=False)
        before = self.state_bytes()
        rc, pv, _ = run_json("host", "set", "--bench-retry", "on", "--by-mcp")
        self.assertEqual(rc, 0, pv)
        self.assertTrue(pv["dry"])
        self.assertIs(pv["consent"], True)
        self.assertGreater(pv["cost"]["retry"]["max_mb"], 0)
        self.assertTrue(any("ГБ" in n or "МБ" in n or "GB" in n or "MB" in n for n in pv["notes"]), pv["notes"])
        for more in ((), ("--yes",)):
            rc, got, _ = run_json("host", "set", "--bench-retry", "on", "--by-mcp", "--code", pv["code"], *more)
            self.assertEqual(rc, 2, got)
            self.assertEqual(got["data"]["key"], "heavy")
        self.assertEqual(self.state_bytes(), before)
        self.assertIs(self.saved()["retry"], False)

    def test_cli_without_a_terminal(self):
        self.bench(list(HEAVY), retry=False)
        for argv in (("host", "set", "--bench-retry", "on"), ("host", "set", "--bench-retry", "on", "--json")):
            rc, out, err = run_cli(*argv)
            self.assertEqual(rc, 2, out + err)
            self.assertIs(self.saved()["retry"], False)
        rc, got, _ = run_json("host", "set", "--bench-retry", "on")
        self.assertEqual(got["data"]["key"], "need_confirm")
        rc, ok, _ = run_json("host", "set", "--bench-retry", "on", "--yes")
        self.assertEqual(rc, 0, ok)
        self.assertIs(self.saved()["retry"], True)
        self.assertEqual(run_json("host", "set", "--bench-retry", "off")[0], 0)        # lighter: never a question

    def test_in_a_terminal_the_question(self):
        self.bench(list(HEAVY), retry=False)
        with mock.patch.object(host_sched, "_isatty", return_value=True), \
                mock.patch.object(host_sched, "_ask_heavy", return_value=False) as ask:
            rc, _out, _err = run_cli("host", "set", "--bench-retry", "on")
            self.assertEqual(rc, 1)
            self.assertEqual(ask.call_count, 1)
            self.assertIs(self.saved()["retry"], False)
            ask.return_value = True
            self.assertEqual(run_cli("host", "set", "--bench-retry", "on")[0], 0)
        self.assertIs(self.saved()["retry"], True)

    def test_in_a_terminal_the_question_text(self):
        """REV-ns-b14-3 З-1: the real question (no stub of ``_ask_heavy``) — a number, never a raw ``{mb}``."""
        import io
        self.bench(list(HEAVY), retry=False)
        with mock.patch.object(host_sched, "_isatty", return_value=True), \
                mock.patch("sys.stdin", io.StringIO("2\n")):
            rc, out, err = run_cli("host", "set", "--bench-retry", "on")
        self.assertEqual(rc, 1, out + err)
        self.assertIs(self.saved()["retry"], False)
        q = [ln for ln in out.splitlines() if "?" in ln]
        self.assertTrue(q, out)
        self.assertNotIn("{", out)
        self.assertRegex(q[-1], r"\d")

    def test_mcp_refusals_name_the_real_way(self):
        """REV-ns-b14-3 З-2: the refusals of MCP send the person where it is done today — the repeats: the terminal
        (kaskadctl host set --bench-retry on), the schedule: SSH menu 22 → 4 or kaskadctl; no panel (no screen yet)."""
        from kaskad import i18n
        from kaskad.mcp import __file__ as mcp_init
        for lang in ("ru", "en"):
            r = i18n.lookup("host_cli.set.retry_heavy_mcp", lang)
            s = i18n.lookup("host_sched.err.heavy_mcp", lang)
            self.assertIn("kaskadctl host set --bench-retry on", r)
            self.assertIn("22 → 4", s)
            self.assertIn("{cmd}", s)                                    # the whole command (REV-ns-p3a П-5)
            for t in (r, s):
                self.assertNotRegex(t.lower(), r"панел|panel")
            self.assertNotRegex(r.lower(), r"ssh")
        with open(os.path.join(os.path.dirname(mcp_init), "AGENT_GUIDE.md"), encoding="utf-8") as f:
            guide = f.read()
        self.assertGreaterEqual(guide.count("kaskadctl host set --bench-retry on"), 2)          # ru and en
        self.assertNotIn("в панели или в SSH-меню", guide)
        self.assertNotIn("the panel or the SSH menu", guide)

    def test_a_light_list_at_once(self):
        self.bench([ent("03:45")], retry=False)
        rc, pv, _ = run_json("host", "set", "--bench-retry", "on", "--by-mcp")
        self.assertIs(pv["consent"], False)
        self.assertNotIn("cost", pv)
        self.assertEqual(run_json("host", "set", "--bench-retry", "on", "--by-mcp", "--code", pv["code"])[0], 0)
        self.assertIs(self.saved()["retry"], True)

    def test_mcp_never_passes_yes(self):
        from tests.test_mcp import TOOLS
        self.assertNotIn("--yes", json.dumps(TOOLS["host_set"]))


class ScreenWordsTest(SchedCliCase):
    """REV-ns-b14 З-5: the screen of the SSH menu (--screen) has no flags and commands of kaskadctl."""

    FLAGS = ("--", "kaskadctl", "Код изменения", "страниц", "Code of", "page")

    def test_dry_on_the_screen(self):
        self.bench(list(HEAVY))
        rc, text, _ = run_cli("host", "bench", "schedule", "add", "--time", "18:00", "--mode", "quick", "--days", "all",
                              "--dry", "--screen")
        self.assertEqual(rc, 0, text)
        self.assertIn("много трафика", text)
        for w in self.FLAGS:
            self.assertNotIn(w, text, w)
        rc, text, _ = run_cli("host", "bench", "schedule", "add", "--time", "18:00", "--mode", "quick", "--days", "all",
                              "--dry")
        self.assertIn("Код изменения", text)                                   # kaskadctl itself: the code is there
        rc, text, _ = run_cli("host", "bench", "schedule", "off", "1", "--dry", "--screen")
        self.assertNotIn("много трафика", text)                                 # it gets lighter: no question

    def test_refusals_have_no_page_and_no_flags(self):
        for key in ("host_sched.err.code", "host_sched.err.stale", "host_sched.err.tz_changed"):
            for lang in ("ru", "en"):
                from kaskad.i18n import tr
                t = tr(key, lang)
                for w in ("--", "страниц", "page", "window", "окне"):
                    self.assertNotIn(w, t, (key, lang))

    def test_timer_hint_is_the_item_of_the_menu(self):
        self.bench([ent("03:45")])
        with mock.patch.object(host_sched, "timer", return_value={"active": False, "applied": False, "why": "stale"}):
            rc, text, _ = run_cli("host", "bench", "schedule", "list", "--screen")
            self.assertIn("5) Обновить таймер", text)
            self.assertNotIn("kaskadctl", text)
            rc, text, _ = run_cli("host", "bench", "schedule", "list")
            self.assertIn("kaskadctl host bench schedule apply", text)


class FlagsBeforeTheSubcommandTest(SchedCliCase):
    """REV-ns-b14 П-1: --yes, --dry and --json given before «schedule» are not lost."""

    def test_parser(self):
        from kaskad.cli import build_parser
        a = build_parser().parse_args(["host", "bench", "--yes", "schedule", "add", "--time", "12:00", "--mode",
                                       "quick", "--days", "all"])
        self.assertIs(a.yes, True)
        a = build_parser().parse_args(["host", "bench", "--dry", "--json", "schedule", "rm", "1"])
        self.assertIs(a.dry, True)
        self.assertIs(a.json, True)
        a = build_parser().parse_args(["host", "bench", "schedule", "rm", "1"])
        self.assertIs(a.dry, False)
        self.assertIs(a.yes, False)

    def test_dry_before_saves_nothing(self):
        self.bench([ent("03:45")])
        self.get()
        before = self.state_bytes()
        rc, out, _ = run_cli("host", "bench", "--dry", "schedule", "rm", "1")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.state_bytes(), before)
        rc, _o, _ = run_cli("host", "bench", "--yes", "schedule", "add", "--time", "12:00", "--mode", "full",
                            "--days", "all")
        self.assertEqual(rc, 0, _o)


class HostSetCodeTest(SchedCliCase):
    """REV-ns-b14 З-2 (ТЗ §2.8 п. 5): MCP host_set (--by-mcp) changes bench_retry and dev_* only with the code of its
    preview — the server checks it, not the hint of the client; the other settings — at once, as in 3.3.7."""

    def test_preview_then_the_code(self):
        self.bench([])
        self.get()
        before = self.state_bytes()
        rc, pv, _ = run_json("host", "set", "--bench-retry", "off", "--dev-threshold", "20", "--by-mcp")
        self.assertEqual(rc, 0, pv)
        self.assertTrue(pv["dry"])
        self.assertRegex(pv["code"], r"^[0-9a-f]{10}$")
        self.assertEqual(set(pv["changed"]), {"bench_retry", "dev"})
        self.assertEqual(self.state_bytes(), before)                     # nothing written, the repeats not stopped
        with mock.patch.object(sched_unit, "stop_retries") as stop:
            rc, bad, _ = run_json("host", "set", "--bench-retry", "off", "--dev-threshold", "25", "--by-mcp",
                                  "--code", pv["code"])                   # another change, the same code
            self.assertEqual(rc, 3, bad)
            self.assertEqual(self.state_bytes(), before)
            rc, ok, _ = run_json("host", "set", "--bench-retry", "off", "--dev-threshold", "20", "--by-mcp",
                                 "--code", pv["code"])
            self.assertEqual(rc, 0, ok)
            self.assertNotIn("dry", ok)
            self.assertEqual(stop.call_count, 1)
        self.assertIs(self.saved()["retry"], False)
        self.assertEqual(state.load()["gateway"]["hostmon"]["dev"]["pct"], 20)
        rc, again, _ = run_json("host", "set", "--bench-retry", "off", "--dev-threshold", "20", "--by-mcp",
                                "--code", pv["code"])                     # the settings moved: the old code is no more
        self.assertEqual(rc, 3, again)

    def test_changed_meanwhile(self):
        self.bench([])
        _rc, pv, _ = run_json("host", "set", "--dev-spread", "off", "--by-mcp")
        self.assertEqual(run_json("host", "set", "--dev-threshold", "15")[0], 0)          # the panel, a person
        rc, got, _ = run_json("host", "set", "--dev-spread", "off", "--by-mcp", "--code", pv["code"])
        self.assertEqual(rc, 3, got)
        self.assertTrue(state.load()["gateway"]["hostmon"]["dev"]["use_spread"])

    def test_other_settings_at_once(self):
        self.bench([])
        rc, doc, _ = run_json("host", "set", "--push-level", "warn", "--by-mcp")
        self.assertEqual(rc, 0, doc)
        self.assertNotIn("dry", doc)
        self.assertEqual(doc["push_level"], "warn")


class DaysTest(SchedCliCase):
    def test_days(self):
        for s, want in (("all", WEEK), ("1-5", [1, 2, 3, 4, 5]), ("6-7", [6, 7]), ("1,3,7", [1, 3, 7]),
                        ("1-3,5", [1, 2, 3, 5]), (" ALL ", WEEK)):
            self.assertEqual(host_sched.parse_days(s), want, s)
        for s in ("", "8", "0", "5-1", "1-", "1,,2", "mon", "1;2", "1-5 6"):
            with self.assertRaises(host_sched.Refused, msg=s):
                host_sched.parse_days(s)


class HistoryCliTest(SchedCliCase):
    def test_history_show_compare(self):
        a = self.check(3600)
        b = self.check(1800, by="auto", retry_of=a)
        c = self.check(900, status="skipped", why="busy", vals={})
        rc, doc, _ = run_json("host", "bench", "history", "--days", "14")
        self.assertEqual(rc, 0)
        self.assertEqual([r["id"] for r in doc["runs"]], [c, b, a])
        self.assertEqual(doc["runs"], self.ok("GET", "/api/host/bench?from=%s" % _days_ago(self.now, 13))["runs"])
        rc, text, _ = run_cli("host", "bench", "history")
        self.assertIn("повтор после резкого падения", text)
        self.assertIn("пропущена", text)
        rc, doc, _ = run_json("host", "bench", "history", "--skipped")
        self.assertEqual([r["id"] for r in doc["runs"]], [c])
        rc, doc, _ = run_json("host", "bench", "show", a)
        self.assertEqual(rc, 0)
        self.assertEqual(doc["id"], a)
        rc, text, _ = run_cli("host", "bench", "show", a)
        self.assertIn("Процессор, одно ядро", text)
        rc, doc, _ = run_json("host", "bench", "compare", a, b)
        self.assertEqual(rc, 0)
        self.assertEqual(doc["a"]["id"], a)

    def test_compare_the_raw_values(self):
        """NS-D33: compare — the raw values with the change in % (no score of a test, no «better» of a row); rel_total
        of both only when both have it (here: too little history — no line). The text of show — the values only."""
        q = lambda **v: dict((k, {"v": x, "mode": "quick"}) for k, x in v.items())      # noqa: E731
        a = self.check(3600, vals=q(cpu_1t=1000.0, dns=40.0), score=700)
        b = self.check(1800, vals=q(cpu_1t=1100.0, dns=20.0, net_down=0.0), score=650)
        rc, doc, _ = run_json("host", "bench", "compare", a, b)
        self.assertEqual(rc, 0)
        self.assertEqual([(r["test"], r["delta"], r["delta_pct"]) for r in doc["rows"]],
                         [("cpu_1t", 100.0, 10.0), ("dns", -20.0, -50.0), ("net_down", None, None)])
        self.assertEqual((doc["rel_a"], doc["rel_b"]), (None, None))
        self.assertNotIn("better", doc)
        rc, text, _ = run_cli("host", "bench", "compare", a, b)
        self.assertIn("  Процессор, одно ядро: 1\u202f000 событий/с → 1\u202f100 событий/с (+10,0%)", text)
        self.assertIn("  Ответ DNS: 40,0 мс → 20,0 мс (-50,0%)", text)
        self.assertIn("  Скачивание: — → 0,0 Мбит/с\n", text)
        self.assertNotIn("Относительно обычного", text)
        with mock.patch.object(history, "rel_pair", return_value=(96, 104)):
            self.assertIn("Относительно обычного: A — 96, B — 104", host_sched.compare_lines(history.compare(a, b)))
        rc, text, _ = run_cli("host", "bench", "show", a)
        self.assertEqual(text.splitlines()[1:], ["  Процессор, одно ядро: 1\u202f000 событий/с",
                                                 "  Ответ DNS: 40,0 мс"])

    def test_refusals(self):
        for argv in (("history", "--to", "9999-12-31"), ("history", "--from", "2026-02-30"),
                     ("history", "--days", "401"), ("history", "--days", "3", "--from", "2026-01-01"),
                     ("history", "--mode", "slow"), ("history", "--by", "x"), ("history", "--limit", "0"),
                     ("show", "../etc"), ("show", "00000000ff"), ("compare", "00000000ff", "00000000fe"),
                     ("export", "--format", "xml")):
            rc, doc, _ = run_json("host", "bench", *argv)
            self.assertEqual(rc, 2, argv)
            self.assertNotIn("../etc", doc["error"])

    def test_export_to_a_new_file_only(self):
        self.check(3600)
        out = self.path("/root/bench.csv")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        rc, doc, _ = run_json("host", "bench", "export", "--out", out)
        self.assertEqual(rc, 0, doc)
        self.assertEqual(os.stat(out).st_mode & 0o777, 0o600)
        with open(out, encoding="utf-8", newline="") as f:
            body = f.read()
        r = self.call("GET", "/api/host/bench/export?format=csv")
        self.assertEqual(body, r.body.decode("utf-8"))
        rc, doc, _ = run_json("host", "bench", "export", "--out", out)
        self.assertEqual(rc, 3)


class DevRuleTest(SchedCliCase):
    def test_dev_threshold_goes_to_the_score(self):
        self.bench([])
        rc, doc, _ = run_json("host", "set", "--dev-threshold", "15", "--dev-spread", "off")
        self.assertEqual(rc, 0, doc)
        self.assertEqual(doc["dev_rule"], {"pct": 15, "use_spread": False})
        self.assertEqual(state.load()["gateway"]["hostmon"]["dev"], {"pct": 15, "use_spread": False})
        self.assertEqual(self.ok("GET", "/api/host/score")["dev_rule"], {"pct": 15, "use_spread": False})
        before = self.state_bytes()
        for bad in ("4", "31", "x", "10.5", "-10"):
            self.assertEqual(run_json("host", "set", "--dev-threshold", bad)[0], 2, bad)
        self.assertEqual(run_json("host", "set", "--dev-spread", "maybe")[0], 2)
        self.assertEqual(self.state_bytes(), before)

    def test_the_rule_reaches_host_score_and_the_push(self):
        """REV-ns-b14 З-6 (порчи M5, M6): ▲/▼ by the rule of «host set» in «host score» (item 22, MCP) and in the
        push of a check (bench_done)."""
        from kaskad.bench import donepush, hostscore
        self.bench([])
        self.assertEqual(run_json("host", "set", "--dev-threshold", "20", "--dev-spread", "off")[0], 0)
        rc, doc, _ = run_json("host", "score")
        self.assertEqual(rc, 0, doc)
        self.assertEqual(doc["dev_rule"], {"pct": 20, "use_spread": False})
        real, seen = hostscore.score_doc, []

        def spy(spec, *a, **kw):
            seen.append(kw.get("rule"))
            return real(spec, *a, **kw)
        with mock.patch.object(hostscore, "score_doc", side_effect=spy):
            self.assertIn("rel", donepush.with_rel({"id": "0123456789"}))
        self.assertEqual(seen, [{"pct": 20, "use_spread": False}])

    def test_a_broken_rule_is_the_default(self):
        from kaskad.gw import model
        with state.transaction() as st:
            st["gateway"]["hostmon"]["dev"] = {"pct": 99}
        self.assertEqual(model.dev_rule(state.load()), {"pct": 10, "use_spread": True})
        self.assertEqual(self.ok("GET", "/api/host/score")["dev_rule"], {"pct": 10, "use_spread": True})


def _days_ago(now, n):
    from tests.panel.test_host_bench_api import datetime_days_ago
    return datetime_days_ago(now, n)


if __name__ == "__main__":
    import unittest
    unittest.main()

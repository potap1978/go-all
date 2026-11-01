"""B7 (k3/handoff/SPEC-node-score.md §6.2 B7; SPEC-node-score-schedule-share.md §4, §6.1, §6.3): kaskadctl host score
[--explain] — the same document as GET /api/host/score and MCP host_score, the words of SS §4 after the cut of 23.10
(NS-D30–NS-D34: «В целом: N от обычного», «сравнивать пока не с чем: история N из 3 дней», the hour, first, stale; the links — the raw
value now · «обычно» · rel; no absolute score, band or verdict); kaskadctl host bench [--quick|--full] [--dry] [--yes] —
the price before the start, the questions by numbers, no start without a terminal or --yes, the answer about the
packages; MCP host_score action=price / host_bench — the flags of the CLI, the human asked by the client. Круг 2: MCP
host_bench — the key ``confirm`` of the price checked by the server (С-1), never ``--yes``; small numbers not «0,0»
(З-2); Ctrl+C does not cut apt (З-5); another check — refused before the questions (П-5); the words of a confirmed drop
(П-2)."""

import io
import json
import os
import signal
from unittest import mock

from kaskad import host_bench_cli as HB
from kaskad import i18n, tz
from kaskad.bench import cost, history, pkgs, run
from tests.panel.test_panel_score import DAY, LINK_IDS, S4, ScoreCase
from tests.test_host_cli import run_cli, run_json
from tests.test_mcp import TOOLS, McpCase, call

H = 3600


class ScoreCliTest(ScoreCase):
    def text(self, *argv):
        with i18n.use_lang("ru"):
            rc, out, err = run_cli("host", "score", *argv)
        self.assertEqual(rc, 0, err)
        return out

    def test_json_is_the_answer_of_the_panel(self):
        for d in range(6, 0, -1):
            self.check(d * DAY + H)
        rc, doc, err = run_json("host", "score")
        self.assertEqual(rc, 0, err)
        api = self.score()
        self.assertEqual(doc, api)

    def test_no_checks(self):
        out = self.text()
        self.assertIn("Сравнивать пока не с чем: сервер ни разу не проверялся", out)
        self.assertIn("kaskadctl host bench", out)
        self.assertIn("пункт 3", self.text("--screen"))

    def history(self, low=None):
        """Six usual checks, then three more — the last three with ``low`` × the download (NS-D30: «обычно» of the
        first six, the fresh value of the last three)."""
        for d in range(10, 4, -1):
            self.check(d * DAY)
        for d in (3, 2, 1):
            self.check(d * DAY - H, dict(S4, net_down=S4["net_down"] * low) if low else None)

    def test_the_main_number_and_hour(self):
        """NS-D31: «В целом: N от обычного (100 — как обычно у этого сервера)» and the hour; --explain — every link
        with its raw value now, «обычно» and rel; no score, band or verdict."""
        self.history(0.5)
        doc = run_json("host", "score")[1]
        sm = doc["summary"]
        out = self.text()
        self.assertIn("В целом: %d от обычного (100 — как обычно у этого сервера)" % sm["rel_total"], out)
        self.assertIn(sm["checked"]["hhmm"], out)
        self.assertNotIn("Звенья", out)                                  # the links — only in «Подробнее»
        ex = self.text("--explain")
        self.assertIn("Звенья (сейчас · обычно · от обычного):", ex)
        for lid in LINK_IDS:
            self.assertIn("  " + i18n.tr("score.link." + lid, "ru") + ":", ex)
        self.assertIn("  Скачивание: 850 Мбит/с · обычно 1\u202f700 Мбит/с · 50 от обычного · ▼ хуже обычного", ex)
        self.assertIn("  Отдача: 1\u202f300 Мбит/с · обычно 1\u202f300 Мбит/с · 100 от обычного", ex)
        self.assertIn("  Канал: 75 от обычного", ex)

    def test_nothing_to_compare_with_yet(self):
        """NS-D30, NS-D35: «обычно» needs 3 days of history — fewer, no number: «Сравнивать пока не с чем: история N
        из 3 дней»; a link the same in --explain."""
        self.check(2 * H)
        out = self.text("--explain")
        self.assertIn("Сравнивать пока не с чем: история 0 из 3 дней", out)
        self.assertIn("  Скачивание: 1\u202f700 Мбит/с · обычно нет · сравнивать пока не с чем: история 0 из 3 дней", out)
        self.assertNotIn("от обычного", out.replace("сейчас · обычно · от обычного", ""))

    def test_days_of_history_not_checks(self):
        """NS-D35 (REV-score-cut С-2, вариант б): «обычно» — по дням; one check a day for five days — «история 2 из 3
        дней» (the base is the days before the last two), RU and EN."""
        for d in range(5, 0, -1):
            self.check(d * DAY + H)
        out = self.text("--explain")
        self.assertIn("Сравнивать пока не с чем: история 2 из 3 дней", out)
        with i18n.use_lang("en"):
            rc, en, err = run_cli("host", "score")
        self.assertEqual(rc, 0, err)
        self.assertIn("Nothing to compare with yet: 2 of 3 days of history", en)

    def test_score_lines_of_the_summary(self):
        """The lines by ``summary`` alone: rel_total; links with rel fewer than 3 — rel_few; none — rel_none with the
        most ``usual_n`` of the links (days of history, NS-D35); the history is there but no rel (no fresh value) —
        «В целом: —», never «3 из 3» (REV-score-cut З-1); stale — the date of the latest check, no number."""
        def lines(sm, links=(), explain=False):
            with i18n.use_lang("ru"):
                return HB.score_lines({"summary": dict({"last_id": "0000000001"}, **sm), "links": list(links)},
                                      explain=explain)
        self.assertEqual(lines({"rel_total": 96, "rel_n": 5})[0],
                         "В целом: 96 от обычного (100 — как обычно у этого сервера)")
        self.assertEqual(lines({"rel_total": None, "rel_n": 2})[0],
                         "В целом сравнивать пока не с чем: звеньев с историей 2 из 3")
        self.assertEqual(lines({"rel_total": None, "rel_n": 0}, [{"usual_n": 1}, {"usual_n": 2}])[0],
                         "Сравнивать пока не с чем: история 2 из 3 дней")
        self.assertEqual(lines({"rel_total": None, "rel_n": 0}, [{"usual_n": 7}])[0], "В целом: —")
        down = {"id": "down", "test": "net_down", "rel": None, "usual_n": 6, "fresh_raw": None, "usual_raw": 300.0}
        for lang, word in (("ru", "из 3"), ("en", "of 3")):
            with i18n.use_lang(lang):
                got = HB.score_lines({"summary": {"last_id": "0000000001", "rel_total": None, "rel_n": 0},
                                      "links": [down]}, explain=True)
            self.assertFalse([x for x in got if word in x], (lang, got))
            self.assertTrue(got[0].endswith(": —"), got)
            self.assertTrue([x for x in got if x.startswith("  ") and x.endswith(" · —")], got)
        self.assertEqual(lines({"stale": True, "last_id": None, "rel_total": 96})[0],
                         "Давно не проверялся · последняя проверка ?")
        grp = {"id": "cpu", "rel": 104, "usual_n": 5}
        one = {"id": "up", "test": "net_up", "rel": None, "usual_n": 1, "fresh_raw": 640.0, "usual_raw": None}
        got = lines({"rel_total": 100, "rel_n": 3}, [grp, one], explain=True)
        self.assertIn("  Процессор: 104 от обычного", got)
        self.assertIn("  Отдача: 640 Мбит/с · обычно нет · сравнивать пока не с чем: история 1 из 3 дней", got)
        with i18n.use_lang("en"):
            en = HB.score_lines({"summary": {"last_id": "0000000001", "rel_total": 96}, "links": [grp, one]},
                                explain=True)
        self.assertEqual(en[0], "Overall: 96 of usual (100 — as usual for this server)")
        self.assertIn("  Upload: 640 Mbit/s · usually none · nothing to compare with yet: 1 of 3 days of history", en)

    def test_the_threshold_of_the_marks_is_said(self):
        """T-3377 Н-4: the threshold of ▲/▼ (dev_rule of the score, «Настройки › Проверка сервера») is in the text of
        ``host score``, not only in --json; never checked — no line (nothing to mark); a broken value — no line."""
        doc = {"summary": {"last_id": "0000000001", "rel_total": 96}, "links": [], "dev_rule": {"pct": 15, "use_spread": True}}
        for lang, line in (("ru", "  Порог отметок ▲/▼: 15%"), ("en", "  ▲/▼ mark threshold: 15%")):
            with i18n.use_lang(lang):
                self.assertEqual(HB.score_lines(doc).count(line), 1, lang)
                self.assertEqual(HB.score_lines(doc, explain=True).count(line), 1, lang)
        with i18n.use_lang("ru"):
            self.assertFalse([x for x in HB.score_lines(dict(doc, dev_rule={"pct": "15"})) if "▲/▼" in x])
            self.assertFalse([x for x in HB.score_lines(dict(doc, summary={"last_id": None})) if "▲/▼" in x])

    def test_small_numbers_are_not_zero(self):
        """З-2: two significant digits below 1."""
        with i18n.use_lang("ru"):
            got = [HB._num(v) for v in (0.04, 0.006, 0.4, 0.45, 0.1, 5.25, 712.4, 0, 12.0, 1500)]
        self.assertEqual(got[:5], ["0,04", "0,006", "0,4", "0,45", "0,1"])
        self.assertIn(got[5], ("5,2", "5,3"))
        self.assertEqual(got[6:8], ["712", "0"])
        self.assertEqual(got[8], "12")
        with i18n.use_lang("en"):
            self.assertEqual(HB._num(0.04), "0.04")
        lk = {"id": "loss", "test": "loss", "fresh_raw": 0.04, "usual_raw": 0.01, "rel": 25, "usual_n": 5,
              "dev": "down", "hint": {"test": "loss", "unit": "%", "days": [["2026-10-18", 0.04], ["2026-10-19", 0.03]]}}
        with i18n.use_lang("ru"):
            self.assertEqual(HB._link_line(lk), "  Потери: 0,04 % · обычно 0,01 % · 25 от обычного · ▼ хуже обычного · "
                                                "два последних дня: 0,04 и 0,03 %")

    def test_a_confirmed_drop_is_not_waiting(self):
        """П-2 (NS-D25): «ждёт подтверждения» — only before the repeat."""
        lk = {"id": "down", "test": "net_down", "fresh_raw": 5.0, "usual_raw": 600.0, "rel": 1, "usual_n": 5,
              "dev": None, "dev_pending": "down", "hint": {"test": "net_down", "unit": "Мбит/с", "now": 5.0}}
        with i18n.use_lang("ru"):
            for kind, words in (("retry_wait", "ждёт подтверждения"),
                                ("retry_confirmed", "повторная проверка подтвердила падение"),
                                ("drop", "резкое падение подтверждено")):
                self.assertIn(words, HB._link_line(dict(lk, dev_pending_kind=kind)))
        with i18n.use_lang("en"):
            en = HB._link_line(dict(lk, dev_pending_kind="drop"))
        self.assertNotRegex(en, "[А-Яа-яЁё]")                              # the unit of the hint too
        self.assertTrue(en.endswith("a sharp drop is confirmed · 5 Mbit/s"), en)

    def test_first_and_stale(self):
        self.check(2 * H)
        self.assertIn("Первая проверка, уточнится за сутки", self.text())
        import shutil
        from kaskad.bench import spool
        shutil.rmtree(spool.spool_dir(), ignore_errors=True)
        self.check(46 * DAY)
        out = self.text()
        self.assertIn("Давно не проверялся", out)
        self.assertIn(i18n.fmt_date(tz.day(self.now - 46 * DAY), "ru"), out)

    def test_english(self):
        """The whole text in English — the units of the links and of the hint of ▼ too (the core keeps them in
        Russian)."""
        self.history(0.5)
        with i18n.use_lang("en"):
            rc, out, _ = run_cli("host", "score", "--explain")
        self.assertEqual(rc, 0)
        self.assertIn("Overall: 100 of usual (100 — as usual for this server)", out)
        self.assertIn("Download: 850 Mbit/s · usually 1,700 Mbit/s · 50 of usual · ▼ worse than usual · "
                      "the last two days: 850 and 850 Mbit/s", out)
        self.assertNotRegex(out, "[А-Яа-яЁё]")


class FakeRunner(object):
    def __init__(self, missing=()):
        self.missing = set(missing)

    def which(self, name):
        return None if name in self.missing else "/usr/bin/" + name


class BenchCliTest(ScoreCase):
    def setUp(self):
        super().setUp()
        self.runner = FakeRunner()
        self.plan = {"missing": [], "available": True, "install": [], "kib": 0, "why": None}
        self.started = []
        self.busy = []
        self.clock = 1000.0
        ps = [mock.patch("kaskad.sysrun.default_runner", lambda: self.runner),
              mock.patch.object(run, "active", lambda runner: list(self.busy)),
              mock.patch.object(pkgs, "plan", self.fake_plan),
              mock.patch.object(run, "start", self.start),
              mock.patch.object(HB, "_sleep", self.sleep), mock.patch.object(HB, "_mono", lambda: self.clock)]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)

    def sleep(self, s):
        self.clock += s

    def fake_plan(self, runner, names=None, update=False, by="cli", iperf=False):
        """As ``pkgs.plan``: the iperf3 of ``self.plan`` only with ``iperf`` (С2 REV-score-rename: the fake ignored it)."""
        p = dict(self.plan)
        for k in ("missing", "install"):
            p[k] = [x for x in p.get(k) or () if iperf or x != "iperf3"]
        return p

    def start(self, mode, by, install=None, runner=None, **kw):
        self.started.append((mode, by, install))
        self.kw = kw
        self.sigint = signal.getsignal(signal.SIGINT)
        return {"id": "0123456789", "unit": run.unit_name("0123456789"), "missing": []}

    def bench(self, *argv, tty=False, stdin=""):
        with mock.patch.object(HB, "_isatty", lambda: tty), mock.patch("sys.stdin", io.StringIO(stdin)), \
                i18n.use_lang("ru"):
            return run_cli("host", "bench", *argv)

    def test_dry_both_modes_and_one(self):
        rc, out, err = self.bench("--dry", "--json")
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        self.assertEqual(set(doc["modes"]), {"quick", "full"})
        want, _b = cost.per_run("full", {"history": [], "pkgs": "yes"})
        self.assertEqual(doc["modes"]["full"]["rx_mb"], want["rx_mb"])
        self.assertEqual(set(json.loads(self.bench("--dry", "--quick", "--json")[1])["modes"]), {"quick"})
        rc, out, _ = self.bench("--dry")
        self.assertIn("Цена одной проверки", out)
        self.assertIn("Быстрая", out)
        self.assertEqual(self.started, [])                       # --dry starts nothing

    def test_dry_unknown_country_says_as_for_russia_and_keeps_the_upper_bound(self):
        """REV-ns-p3a П-3: the country not known without the network — the price says the link is measured as for
        Russia unless the delays find it; the price stays the caps abroad (never lower than the traffic: the key of
        the consent of MCP); a known country — no line. The country is found offline: no connection at all."""
        from kaskad.bench import channel
        with mock.patch.object(channel, "connect_ms", side_effect=AssertionError("network")):
            self.assertIsNone(HB.country_offline())                       # an empty state: nothing known
        abroad, _b = cost.per_run("full", {"history": [], "pkgs": "yes", "ru": False})
        for known, line in ((None, True), (False, False), (True, False)):
            with mock.patch.object(HB, "country_offline", lambda: known):
                doc = json.loads(self.bench("--dry", "--json")[1])
                out = self.bench("--dry")[1]
            self.assertEqual(doc["geo"], "unknown_ru" if line else None, known)
            self.assertEqual(doc["modes"]["full"]["tx_mb"], abroad["tx_mb"], known)
            self.assertEqual("как для России" in out, line, (known, out))
            self.assertNotIn("{", out)

    def test_the_upload_in_russia_by_default_and_turned_off(self):
        """Владелец 29.10 (D-144): a full check of a node that may be in Russia measures the upload to the iperf3
        points by default — the price says so and asks «1) Замерить отдачу 2) Не замерять отдачу» (numbers, Enter — 1);
        ``--no-upload-ru`` (MCP — its parameter) turns it off; the quick kind never; abroad — no line, no question."""
        with mock.patch.object(HB, "country_offline", lambda: True):
            doc = json.loads(self.bench("--dry", "--full", "--json")[1])
            self.assertIn("увидит адрес этого сервера", doc["upload_ru"]["text"])
            self.assertIn("отдачу — до серверов замера скорости российских провайдеров", self.bench("--dry")[1])
            self.assertIsNone(json.loads(self.bench("--dry", "--quick", "--json")[1])["upload_ru"])
            for argv, on in ((("--yes", "--full", "--json"), True), (("--yes", "--full", "--no-upload-ru", "--json"), False),
                             (("--yes", "--quick", "--json"), False)):
                out = json.loads(self.bench(*argv)[1])
                self.assertEqual((self.kw["iperf"], out["upload_ru"]), (on, on), argv)
            key = json.loads(self.bench("--dry", "--full", "--json")[1])["modes"]["full"]["confirm"]
            self.bench("--confirm", key, "--full", "--no-upload-ru", "--json", "--by-mcp")
            self.assertEqual((self.started[-1][1], self.kw["iperf"]), ("mcp", False))
            self.bench("--confirm", key, "--full", "--json", "--by-mcp")
            self.assertEqual((self.started[-1][1], self.kw["iperf"]), ("mcp", True))
            with mock.patch.object(run, "status", return_value={"state": "unknown"}):
                rc, out, _ = self.bench("--full", tty=True, stdin="1\n2\n")   # go; the upload — «2) Не замерять»
            self.assertEqual(rc, 0)
            self.assertIn("2) Не замерять отдачу", out)
            self.assertFalse(self.kw["iperf"])
        with mock.patch.object(HB, "country_offline", lambda: False):
            self.assertIsNone(json.loads(self.bench("--dry", "--json")[1])["upload_ru"])
            self.assertNotIn("российских провайдеров", self.bench("--dry")[1])

    def test_the_price_of_a_full_check_in_russia_counts_the_upload_of_8b(self):
        """С1 REV-score-rename: the price is measured by the scheduled checks, and they never run 8b (NS-S5) — a full
        manual check of a node that may be in Russia runs it by default (D-144, up to 2000 MB): its upload is never
        below the cap of 8b («до», the key of MCP with it); abroad and the quick kind — as measured."""
        rows = [{"mode": m, "by": "schedule", "status": "ok", "ts": i, "rx": 30e6, "tx": 3e6, "cpu_s": 120}
                for m in ("quick", "full") for i in range(3)]
        with mock.patch.object(history, "runs", lambda **kw: {"runs": rows}):
            for ru, tx, basis in ((True, 2000.0, "caps"), (None, 2000.0, "caps"), (False, 3.0, "measured")):
                with mock.patch.object(HB, "country_offline", lambda: ru):
                    doc = json.loads(self.bench("--dry", "--json")[1])
                    key = json.loads(self.bench("--dry", "--full", "--json")[1])["modes"]["full"]["confirm"]
                full, quick = doc["modes"]["full"], doc["modes"]["quick"]
                self.assertEqual((full["tx_mb"], full["basis"]), (tx, basis), ru)       # без правки: 3.0, measured
                self.assertEqual((full["rx_mb"], quick["tx_mb"], quick["basis"]), (30.0, 3.0, "measured"), ru)
                self.assertEqual(key, full["confirm"])
                self.assertEqual(key, HB.confirm_key("full", {k: full[k] for k in full if k != "confirm"},
                                                     {"missing": [], "available": True}))

    def test_iperf3_only_when_8b_goes(self):
        """С2 REV-score-rename: iperf3 is asked and installed only when 8b may go — a full check with the upload on;
        the quick kind and the upload turned off never ask it (no question, nothing installed, the key without it);
        the SSH/CLI questions: first the upload, then the programs of the answer."""
        self.runner.missing = {"iperf3"}
        self.plan.update(missing=["iperf3"], install=["iperf3"], kib=330)
        with mock.patch.object(HB, "country_offline", lambda: True):
            doc = json.loads(self.bench("--dry", "--json")[1])
            self.assertEqual((doc["pkgs"]["missing"], doc["pkgs"]["question"]), ([], None))   # без правки: ["iperf3"]
            self.assertEqual(doc["upload_ru"]["pkgs"]["missing"], ["iperf3"])
            self.assertIn("iperf3", doc["upload_ru"]["pkgs"]["question"]["text"])
            for argv, inst, w3 in ((("--yes", "--quick", "--json"), None, False),
                                   (("--yes", "--full", "--no-upload-ru", "--json"), None, False),
                                   (("--yes", "--full", "--json"), True, True)):
                self.bench(*argv)
                self.assertEqual((self.started[-1][2], self.kw["with_iperf3"]), (inst, w3), argv)
            quick = doc["modes"]["quick"]["confirm"]
            self.bench("--confirm", quick, "--quick", "--json", "--by-mcp")
            self.assertEqual((self.started[-1][2], self.kw["with_iperf3"]), (None, False))
            with mock.patch.object(run, "status", return_value={"state": "unknown"}):
                rc, out, _ = self.bench("--full", tty=True, stdin="1\n2\n")      # go; the upload — «2»: no iperf3
                self.assertEqual(rc, 0)
                self.assertNotIn("нужна программа iperf3", out)
                self.assertEqual((self.started[-1][2], self.kw["with_iperf3"]), (None, False))
                rc, out, _ = self.bench("--full", tty=True, stdin="1\n1\n1\n")  # go; the upload; install iperf3
                self.assertEqual(rc, 0)
                self.assertLess(out.index("2) Не замерять отдачу"), out.index("нужна программа iperf3"))
                self.assertEqual((self.started[-1][2], self.kw["with_iperf3"]), (True, True))
                rc, out, _ = self.bench("--quick", tty=True, stdin="1\n")
                self.assertNotIn("iperf3", out)
                self.assertEqual((self.started[-1][2], self.kw["with_iperf3"]), (None, False))
        self.runner.missing, self.plan["missing"], self.plan["install"] = set(), [], []
        with mock.patch.object(HB, "country_offline", lambda: True):
            self.assertEqual(json.loads(self.bench("--dry", "--json")[1])["modes"]["quick"]["confirm"], quick)

    def test_dry_says_the_missing_packages(self):
        self.runner.missing = {"sysbench", "fio"}
        self.plan.update(missing=["sysbench", "fio"], install=["sysbench", "fio"], kib=50000)
        doc = json.loads(self.bench("--dry", "--json")[1])
        self.assertEqual(doc["pkgs"]["missing"], ["sysbench", "fio"])
        self.assertEqual([c[0] for c in doc["pkgs"]["question"]["choices"]], [1, 2])
        self.assertIn("sysbench fio", self.bench("--dry")[1])

    def test_no_terminal_no_yes_no_start(self):
        rc, out, err = self.bench()
        self.assertEqual(rc, 2)
        self.assertIn("--yes", out + err)
        rc, _o, _e = self.bench("--json")
        self.assertEqual(rc, 2)
        self.assertEqual(self.started, [])

    def test_quick_and_full_together(self):
        self.assertEqual(self.bench("--quick", "--full", "--yes")[0], 2)

    def test_yes_json_answers_at_once(self):
        rc, out, err = self.bench("--yes", "--full", "--json")
        self.assertEqual(rc, 0, err)
        doc = json.loads(out)
        self.assertEqual((doc["id"], doc["mode"], doc["ok"]), ("0123456789", "full", True))
        self.assertIn("rx_mb", doc["price"])
        self.assertEqual(self.started, [("full", "cli", None)])

    def test_yes_installs_the_missing_packages(self):
        self.runner.missing = {"fio"}
        self.plan.update(missing=["fio"], install=["fio"], kib=1000)
        key = json.loads(self.bench("--dry", "--quick", "--json")[1])["modes"]["quick"]["confirm"]
        self.bench("--confirm", key, "--json", "--by-mcp")
        self.assertEqual(self.started, [("quick", "mcp", True)])
        self.started[:] = []
        self.plan.update(available=False, why="not_found")       # apt does not know them: the check goes without
        self.bench("--yes", "--json")
        self.assertEqual(self.started, [("quick", "cli", None)])

    def test_questions_by_numbers(self):
        self.runner.missing = {"sysbench", "fio"}
        self.plan.update(missing=["sysbench", "fio"], install=["sysbench", "fio"], kib=50000)
        with mock.patch.object(run, "status", return_value={"state": "unknown"}):
            rc, out, _ = self.bench(tty=True, stdin="2\n\n\n")   # the mode — full; the packages, the upload — Enter (1)
        self.assertEqual(rc, 0)
        self.assertEqual(self.started, [("full", "cli", True)])
        self.assertTrue(self.kw["iperf"])
        self.assertIn("1) Быстрая", out)
        self.assertIn("0) Отмена", out)
        self.assertIn("Выбор [1]:", out)
        self.started[:] = []
        rc, out, _ = self.bench(tty=True, stdin="0\n")
        self.assertIn("Проверка не запущена", out)
        rc, out, _ = self.bench(tty=True, stdin="1\n2\n")          # the packages — «Отмена»
        self.assertIn("Проверка не запущена", out)
        rc, out, _ = self.bench("--quick", tty=True, stdin="7\n2\n")   # a wrong number — asked again; then «Отмена»
        self.assertIn("Нет такого пункта", out)
        self.assertEqual(self.started, [])

    def test_mcp_needs_the_key_of_the_price(self):
        """С-1: MCP (--by-mcp) without the key, with --yes, with another key, the key of another mode, the price or the
        packages changed — a refusal (code 2), nothing started or installed; the key of the price — the start."""
        self.runner.missing = {"fio"}
        self.plan.update(missing=["fio"], install=["fio"], kib=1000)
        doc = json.loads(self.bench("--dry", "--json")[1])
        q, f = doc["modes"]["quick"]["confirm"], doc["modes"]["full"]["confirm"]
        self.assertRegex(q, "^[0-9a-f]{8}$")
        self.assertNotEqual(q, f)
        for argv in (("--json", "--by-mcp"), ("--yes", "--json", "--by-mcp"), ("--confirm", "00000000", "--by-mcp"),
                     ("--full", "--confirm", q, "--json", "--by-mcp"), ("--confirm", q, "--yes", "--by-mcp")):
            rc, out, err = self.bench(*argv)
            self.assertEqual(rc, 2, (argv, out, err))
        rc, out, err = self.bench("--confirm", "00000000", "--json", "--by-mcp")
        self.assertIn("host_score action=price", out + err)
        self.plan.update(missing=["fio", "sysbench"], install=["fio", "sysbench"])     # the packages changed
        self.assertEqual(self.bench("--confirm", q, "--json", "--by-mcp")[0], 2)
        self.plan.update(missing=["fio"], install=["fio"])
        with mock.patch.object(cost, "per_run", lambda m, ctx: ({"rx_mb": 999.0, "tx_mb": 1.0, "cpu_min": 1.0},
                                                                 "measured")):
            self.assertEqual(self.bench("--confirm", q, "--json", "--by-mcp")[0], 2)  # the price changed
        self.assertEqual(self.started, [])
        rc, out, err = self.bench("--full", "--confirm", f, "--json", "--by-mcp")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.started, [("full", "mcp", True)])
        self.assertEqual(self.bench("--dry", "--json")[1] and
                         json.loads(self.bench("--dry", "--json")[1])["modes"]["quick"]["confirm"], q)   # stable

    def test_mcp_has_the_limits_of_a_day_cli_has_none(self):
        """Б-1 REV-ns-p4a: MCP — the limits of the panel (6 a day, 2 full), its own count: the 7th check through MCP
        is a refusal ``limit``, nothing started; the panel's checks are not in it; the CLI in a terminal — no limit."""
        moscow = tz.Zone("Europe/Moscow", types=[(10800, False)])
        now = (self.now + 3 * 3600) // DAY * DAY - 3 * 3600 - DAY + 12 * 3600   # yesterday 12:00 by Moscow
        self.now = now
        for _ in range(HB.PANEL_FULL_PER_DAY):
            self.check(600, mode="full", by="mcp")
        for _ in range(3):
            self.check(500, mode="quick", by="panel")                     # the panel's — not MCP's count
        f = json.loads(self.bench("--dry", "--json")[1])["modes"]
        with mock.patch.object(tz, "zone", lambda: moscow), mock.patch.object(HB.time, "time", lambda: float(now)):
            rc, out, err = self.bench("--full", "--confirm", f["full"]["confirm"], "--json", "--by-mcp")
            self.assertEqual(rc, 2, (out, err))
            self.assertIn("ИИ-помощника", out + err)
            self.assertEqual(self.started, [])
            for _ in range(HB.PANEL_PER_DAY - HB.PANEL_FULL_PER_DAY - 1):
                self.check(400, mode="quick", by="mcp")
            self.assertEqual(self.bench("--confirm", f["quick"]["confirm"], "--json", "--by-mcp")[0], 0)  # the 6th
            self.check(300, mode="quick", by="mcp")
            rc, out, err = self.bench("--confirm", f["quick"]["confirm"], "--json", "--by-mcp")       # the 7th
            self.assertEqual(rc, 2, (out, err))
            self.assertIn('"limit"', out + err)
            self.assertEqual(self.bench("--full", "--yes", "--json")[0], 0)                          # the CLI
        self.assertEqual(self.started, [("quick", "mcp", None), ("full", "cli", None)])

    def test_another_check_running_before_the_questions(self):
        """П-5: another check runs — refused before the price and the questions."""
        self.busy = ["0123456789"]
        rc, out, err = self.bench(tty=True, stdin="1\n1\n")
        self.assertNotEqual(rc, 0)
        self.assertNotIn("Какую проверку", out)
        self.assertEqual(self.started, [])

    def test_ctrl_c_does_not_cut_apt(self):
        """З-5: the line before apt, SIGINT ignored while it installs (an ignored signal passes to apt-get), restored
        after."""
        self.runner.missing = {"fio"}
        self.plan.update(missing=["fio"], install=["fio"], kib=2048)
        before = signal.getsignal(signal.SIGINT)
        with mock.patch.object(run, "status", side_effect=KeyboardInterrupt):
            rc, out, _ = self.bench("--yes")
        self.assertEqual(rc, 0)
        self.assertIn("Ставлю fio (≈ 2 МБ). Пока идёт установка, Ctrl+C не действует", out)
        self.assertIs(self.sigint, signal.SIG_IGN)
        self.assertIs(signal.getsignal(signal.SIGINT), before)
        self.runner.missing = set()
        self.plan.update(missing=[], install=[], kib=0)
        self.bench("--yes", "--json")
        self.assertIsNot(self.sigint, signal.SIG_IGN)                 # nothing to install: Ctrl+C as always

    def test_json_in_a_terminal_asks_nothing(self):
        """REV З-3 (A14): --json without --yes in a terminal — a refusal, no questions."""
        rc, out, err = self.bench("--json", tty=True, stdin="1\n1\n")
        self.assertEqual(rc, 2)
        self.assertNotIn("Выбор", out)
        self.assertEqual(self.started, [])

    def test_waits_and_says_the_result(self):
        self.check(30 * DAY)
        done = {"v": 1, "run": {"id": "0123456789", "status": "ok", "why": None}, "tests": []}
        seq = [{"state": "running", "progress": {"i": 1, "n": 9}}, {"state": "running", "progress": {"i": 1, "n": 9}},
               {"state": "running", "progress": {"i": 4, "n": 9}}, {"state": "done", "result": done}]
        with mock.patch.object(run, "status", side_effect=seq):
            rc, out, _ = self.bench("--quick", tty=True, stdin="1\n")
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("шаг 1 из 9"), 1)
        self.assertIn("шаг 4 из 9", out)
        self.assertIn("Проверка закончена", out)
        self.assertIn("Сравнивать пока не с чем: история 0 из 3 дней", out)    # the lines of «host score» after it

    def test_the_stage_comes_first(self):
        """T-3377 Н-8: «шаг i из n» of every step began anew and jumped (5 из 5, 4 из 7, 1 из 3) — the stage k of m of
        the whole check is said first, the step inside it; a progress without the stage (started before) — as before."""
        self.check(30 * DAY)
        done = {"v": 1, "run": {"id": "0123456789", "status": "ok", "why": None}, "tests": []}
        seq = [{"state": "running", "progress": {"step": "inv", "i": 0, "n": 0, "k": 1, "m": 10}},
               {"state": "running", "progress": {"step": "cpu_a", "i": 5, "n": 5, "k": 2, "m": 10}},
               {"state": "running", "progress": {"step": "dns", "i": 1, "n": 3, "k": 4, "m": 10}},
               {"state": "running", "progress": {"step": "disk", "i": 2, "n": 3}},
               {"state": "done", "result": done}]
        with mock.patch.object(run, "status", side_effect=seq):
            out = self.bench("--yes")[1]
        lines = [x.strip() for x in out.splitlines() if x.strip().startswith("Идёт проверка")]
        self.assertEqual(lines, ["Идёт проверка: этап 1 из 10", "Идёт проверка: этап 2 из 10, шаг 5 из 5",
                                 "Идёт проверка: этап 4 из 10, шаг 1 из 3", "Идёт проверка: шаг 2 из 3"])

    def test_a_unit_just_started_is_not_lost(self):
        """П-3: «unknown» right after systemd-run --no-block — still starting, not «итог не найден»."""
        done = {"v": 1, "run": {"id": "0123456789", "status": "ok", "why": None}, "tests": []}
        seq = [{"state": "unknown"}, {"state": "unknown"}, {"state": "running", "progress": {"i": 2, "n": 9}},
               {"state": "done", "result": done}]
        with mock.patch.object(run, "status", side_effect=seq):
            out = self.bench("--yes")[1]
        self.assertIn("Проверка закончена", out)
        self.assertNotIn("не найден", out)
        with mock.patch.object(run, "status", return_value={"state": "unknown"}):
            out = self.bench("--yes")[1]                            # still unknown after START_S — said so
        self.assertIn("не найден", out)

    def test_skipped_says_why(self):
        done = {"run": {"id": "0123456789", "status": "skipped", "why": "locked"}}
        with mock.patch.object(run, "status", return_value={"state": "done", "result": done}):
            out = self.bench("--yes")[1]
        self.assertIn("Проверка не началась: " + run.why_text("locked", None, "ru"), out)

    def test_ctrl_c_stops_waiting_only(self):
        with mock.patch.object(run, "status", side_effect=KeyboardInterrupt):
            rc, out, _ = self.bench("--yes")
        self.assertEqual(rc, 0)
        self.assertIn("без ожидания", out)


class McpBenchTest(McpCase):
    def test_tools(self):
        sc, b = TOOLS["host_score"], TOOLS["host_bench"]          # REV-ns-b14 З-3: price — действие host_score
        pr = sc["by_action"]["price"]
        self.assertEqual((sc["by_action"]["score"]["cmd"], sc["readonly"], sc["action_default"]),
                         (["host", "score"], True, "score"))
        self.assertEqual((pr["cmd"], pr["args"], pr["readonly"]), (["host", "bench"], ["--dry"], True))
        self.assertEqual((b["cmd"], b["readonly"], b["destructive"], b["ask_human"], b["open_world"]),
                         (["host", "bench"], False, False, True, True))
        self.assertNotIn("--yes", b["args"])                      # С-1, SS §6.3: the consent of MCP is the key
        self.assertEqual(b["params"]["confirm"], {"ref": "bench_confirm", "flag": "--confirm", "required": True})

    def test_argv(self):
        sess = self.run_session([call(1, "host_bench", {"full": True, "confirm": "0a1b2c3d"}),
                                 call(2, "host_score", {"action": "price"}), req_list(3),
                                 call(4, "host_bench", {"full": True}),
                                 call(5, "host_bench", {"confirm": "--yes"})])
        argv = sess.result(1)["structuredContent"]["argv"]
        self.assertEqual(argv[:2], ["host", "bench"])
        for a in ("--by-mcp", "--full", "--json", "--confirm=0a1b2c3d"):
            self.assertIn(a, argv)
        self.assertNotIn("--yes", argv)
        self.assertEqual(sess.error(4)["data"]["missing"], ["confirm"])   # no key — not even started
        self.assertTrue(sess.error(5))                                    # not a key (the pattern)
        self.assertEqual(len([c for c in sess.calls if "--by-mcp" in json.dumps(c)]), 1)   # only the call with the key
        argv = sess.result(2)["structuredContent"]["argv"]
        self.assertIn("--dry", argv)
        self.assertNotIn("--yes", argv)
        tools = dict((t["name"], t) for t in sess.result(3)["tools"])
        self.assertIs(tools["host_bench"]["annotations"]["destructiveHint"], True)   # the client asks the human
        self.assertIs(tools["host_score"]["annotations"]["readOnlyHint"], True)

    def test_host_score_through_mcp_is_the_cli_answer(self):
        root = os.path.join(self.tmp, "root")
        with mock.patch.dict(os.environ, {"KASKAD_ROOT": root}):
            rc, doc, _ = run_json("host", "score")
        self.assertEqual(rc, 0)
        sess = self.run_session([call(1, "host_score")], KASKAD_MCP_CTL="", KASKAD_MCP_LANG="ru", timeout=120)
        r = sess.result(1)
        self.assertFalse(r["isError"], r)
        self.assertEqual(r["structuredContent"]["summary"], doc["summary"])
        self.assertNotIn("score", r["structuredContent"])
        self.assertEqual(r["structuredContent"]["links"], doc["links"])


def req_list(msg_id):
    from tests.test_mcp import req
    return req(msg_id, "tools/list", {})


class TimerRouteTest(ScoreCase):
    """REV-ns-b7-2 З-6: «host bench --scheduled / --retry ID / schedule apply» (the timer of B11) go to B11 through the
    parser of kaskadctl — not to the run of B7 (without a terminal and --yes it would refuse: no check by the schedule
    would ever run). Through ``cli.build_parser()``, as kaskadctl does it."""

    def go(self, *argv):
        from kaskad import cli
        args = cli.build_parser().parse_args(list(argv))
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            return args.func(args)

    def test_the_timer_goes_to_b11(self):
        from kaskad import host_cli
        from kaskad.bench import sched_run
        with mock.patch.object(sched_run, "scheduled", return_value={"ok": True}) as sc, \
                mock.patch.object(sched_run, "retry", return_value={"ok": True}) as rt, \
                mock.patch.object(host_cli, "schedule_apply", return_value={"applied": True}) as ap, \
                mock.patch.object(run, "start") as start:
            self.assertEqual(self.go("host", "bench", "--scheduled"), 0)
            self.assertEqual(self.go("host", "bench", "--scheduled", "--json"), 0)
            self.assertEqual(self.go("host", "bench", "--scheduled", "--by-mcp"), 0)   # B11's way, not a refusal
            self.assertEqual(self.go("host", "bench", "--retry", "0123456789"), 0)
            self.assertEqual(self.go("host", "bench", "schedule", "apply"), 0)
        self.assertEqual(sc.call_count, 3)
        rt.assert_called_once_with("0123456789", links=[])           # no --links: the timer of B11 before
        ap.assert_called_once_with()
        start.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()

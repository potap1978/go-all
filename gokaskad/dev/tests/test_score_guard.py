"""Сторож сокращения оценки сервера (решение владельца 23.10; NS-D30–NS-D34 — начало k3/handoff/SPEC-node-score.md).

Абсолютный балл ни разу не выпускался, поэтому от шкалы 0–1000 не остаётся ничего ни на одной поверхности: ни ключей
``band``, ``capacity``, ``fit_for``, ``offline_budget``, ``weakest``, ``fresh_total``, ``last_total``, ``score`` (на
любой глубине), ни слов-вердиктов («хороший», «слабый», «почти не работает», «слабое звено», «0–1000»; barely works,
weak link, very poor). Остаются сырые цифры, «обычно» этого сервера, ▲/▼ и ``rel`` («N от обычного»).

История — несколько проверок (обычные, быстрая, три с упавшим скачиванием и событием резкого падения, автоповтор,
сбой, пропуск) и лёгкие пробы; в базе лежат и старые баллы проверок и тестов (``bench_run.score``/``links``,
``bench.score`` — формат очереди их ещё принимает): их не показывает никто. Обходятся выводы всех поверхностей:
API — /api/host/score, /api/host/day/<d> (все 30 дней полосы), /api/host/bench, /bench/<id> (каждая), /bench/compare,
/bench/export csv и json; CLI --json и текст на RU и EN — host score [--explain, --screen], host bench history, show,
compare; MCP — описание host_score/host_bench в actions.json и AGENT_GUIDE.md (отрицание «без балла 0–1000» — можно) и
ответы host_score живого сервера MCP; лента и бот — bench_done всех видов и причины дня «drop»; каталоги текстов этих
поверхностей. Сторож самодостаточен (свои значения тестов): он же прогоняется на коде до сокращения и там падает.
"""

import csv
import io
import json
import os
import re
import subprocess
import sys
from unittest import mock

from kaskad import i18n, tz
from kaskad.bench import hostscore as HS
from kaskad.bench import spool
from kaskad.bot import monitor
from kaskad.gw import collect, live
from tests.panel.test_panel_score import DAY, ScoreCase
from tests.test_host_cli import run_cli, run_json
from tests.test_mcp import LIB, Session, call, init_messages, req

# k3-select: files=lib/kaskad/mcp/actions.json,lib/kaskad/mcp/AGENT_GUIDE.md,lib/kaskad/i18n/catalog/*/*.json
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = 3600
KEYS = ("band", "capacity", "fit_for", "offline_budget", "weakest", "fresh_total", "last_total", "score")
VERDICT = re.compile(r"(?i)\bхорош(ий|ая|ее|ие|его|ему|ем|ую|ими|их)\b|\bслаб(ый|ая|ое|ые|ого|ому|ым|ых|ую)\b|"
                     r"почти не работает|barely works|weak link|very poor")
SCALE = r"\b0\s*[–—…-]\s*1\s?000\b"
WORDS = re.compile(VERDICT.pattern + "|" + SCALE)
# в описаниях для ИИ сказать «без балла 0–1000» / «no band» можно: это запрет, а не обещание
NEGATION = re.compile(r"(?i)(без балла|без|no|not|нет)\s*$")
PROMISE = re.compile(r"(?i)\b(band|capacity|fit_for|offline_budget|weakest|fresh_total|last_total)\b|" + SCALE)
GONE_KEYS = ("score.band.", "score.cap.", "score.fit.", "score.budget.", "score.star", "gw_live.event.bench_barely",
             "host_bench.score.total", "host_bench.score.barely", "host_bench.score.last", "host_bench.score.usual")
# сырые значения тестов звеньев (быстрый сервер); пробные тесты (отклик, потери, дрожание, steal) — из лёгких проб
VALS = {"net_down": 1700.0, "net_up": 1300.0, "dns": 5.0, "cpu_1t": 2700.0, "cpu_mt": 10500.0, "chacha_1t": 2400.0,
        "aes_1t": 7000.0, "cpu_cv": 1.2, "cpu_sustain": 0.99, "mem_gb": 8.0, "mem_bw": 20000.0, "disk_rr": 130000.0,
        "disk_rw": 85000.0, "disk_fsync": 0.15}
OLD = {"score": 700, "scale_version": "1.0", "links": {"down": 650, "cpu": 720}}   # старый балл проверки в базе


def walk(x, path="$"):
    """(путь, ключ | None, строка | None) всех ключей и строк документа на любой глубине."""
    if isinstance(x, dict):
        for k, v in x.items():
            p = "%s.%s" % (path, k)
            yield p, k, None
            for got in walk(v, p):
                yield got
    elif isinstance(x, (list, tuple)):
        for i, v in enumerate(x):
            for got in walk(v, "%s[%d]" % (path, i)):
                yield got
    elif isinstance(x, str):
        yield path, None, x


def promises(text):
    """Места текста для ИИ, где обещано убранное (а не сказано «без него»), и слова-вердикты."""
    said = [m.group(0) for m in PROMISE.finditer(text)
            if not NEGATION.search(text[max(0, m.start() - 12):m.start()])]
    return said + [m.group(0) for m in VERDICT.finditer(text)]


class GuardCase(ScoreCase):
    def setUp(self):
        super().setUp()
        self.found = []
        self.history()

    def put(self, ago, vals, drops=None, **head):
        self.n += 1
        h = {"id": "%010x" % self.n, "ts": self.now - int(ago), "mode": "full", "by": "schedule", "status": "ok"}
        h.update(head)
        if h["status"] == "ok":
            h = dict(OLD, **h)
        v = dict((k, {"v": x, "mode": h["mode"]}) for k, x in vals.items())
        more = dict((k, {"score": 640, "scale_version": "1.0"}) for k in vals)     # старый балл теста в базе
        try:
            spool.put(spool.check_doc(h, v, more=more, drops=drops))
        except ValueError:
            # код до сокращения не знает условия «rel» — там проверка без события (сторож падает на находках, а не
            # на истории); здесь событие есть: test_the_reasons_of_a_day требует причину дня «drop»
            spool.put(spool.check_doc(h, v, more=more))
        return h["id"]

    def history(self):
        self.first = [self.put(d * DAY, VALS) for d in range(12, 4, -1)][0]    # обычные полные по расписанию
        self.quick = self.put(4 * DAY + 2 * H, VALS, mode="quick", by="panel")
        for d in (3, 2):                                               # скачивание упало втрое
            self.put(d * DAY - H, dict(VALS, net_down=510.0))
        ts = self.now - DAY + H
        self.bad = self.put(DAY - H, dict(VALS, net_down=510.0), drops=[{
            "link": "down", "cond": "rel", "day": tz.day(ts), "matched": ["rel"], "push": True, "now": 30,
            "test": "net_down", "value": 510.0, "usual": 1700.0, "unit": "Мбит/с"}])
        self.auto = self.put(DAY - H - 900, dict(VALS, net_down=520.0), by="auto", retry_of=self.bad)
        self.failed = self.put(6 * H, {}, status="failed", why="error", by="panel")
        self.skipped = self.put(3 * H, {}, status="skipped", why="busy")
        self.ids = ["%010x" % i for i in range(1, self.n + 1)]
        self.probes(hours=10 * 24)

    # ── проверка одного вывода ──

    def doc(self, where, doc):
        for path, key, text in walk(doc):
            if key is not None and key in KEYS:
                self.found.append((where, path))
            if text is not None and WORDS.search(text):
                self.found.append((where, "%s = %r" % (path, text[:160])))

    def text(self, where, text):
        for line in text.splitlines():
            if WORDS.search(line):
                self.found.append((where, line.strip()[:160]))

    def clean(self):
        self.assertEqual(self.found, [], "остатки абсолютной оценки (NS-D34)")


class ApiGuardTest(GuardCase):
    def test_every_answer_of_the_api(self):
        doc = self.score()
        self.doc("/api/host/score", doc)
        for d in doc["days"]:
            self.doc("/api/host/day/" + d["day"], self.ok("GET", "/api/host/day/" + d["day"]))
        frm = tz.day(self.now - 40 * DAY)
        runs = self.ok("GET", "/api/host/bench?from=" + frm)
        self.doc("/api/host/bench", runs)
        for rid in self.ids:
            self.doc("/api/host/bench/" + rid, self.ok("GET", "/api/host/bench/" + rid))
        for a, b in ((self.first, self.bad), (self.bad, self.quick), (self.failed, self.skipped)):
            self.doc("/api/host/bench/compare?a=%s&b=%s" % (a, b),
                     self.ok("GET", "/api/host/bench/compare?a=%s&b=%s" % (a, b)))
        r = self.call("GET", "/api/host/bench/export?format=json&from=" + frm)
        self.assertEqual(r.status, 200, r)
        self.doc("/api/host/bench/export json", r.json())
        r = self.call("GET", "/api/host/bench/export?format=csv&from=" + frm)
        self.assertEqual(r.status, 200, r)
        rows = list(csv.reader(io.StringIO(r.text)))
        self.doc("/api/host/bench/export csv: заголовок", dict((c, None) for c in rows[0]))
        self.text("/api/host/bench/export csv", r.text)
        self.assertEqual(len(runs["runs"]), len(self.ids))
        self.clean()


class CliGuardTest(GuardCase):
    def test_json_of_the_cli(self):
        for argv in (("host", "score"), ("host", "bench", "history", "--days", "40"),
                     ("host", "bench", "compare", self.first, self.bad)) + \
                tuple(("host", "bench", "show", rid) for rid in self.ids):
            rc, doc, err = run_json(*argv)
            self.assertEqual(rc, 0, (argv, err))
            self.doc(" ".join(argv) + " --json", doc)
        self.clean()

    def test_text_of_the_cli_ru_and_en(self):
        argvs = [("host", "score"), ("host", "score", "--explain"), ("host", "score", "--screen"),
                 ("host", "bench", "history", "--days", "40"), ("host", "bench", "compare", self.first, self.bad),
                 ("host", "bench", "compare", self.bad, self.quick)] + \
            [("host", "bench", "show", rid) for rid in self.ids]
        for lang in ("ru", "en"):
            for argv in argvs:
                with i18n.use_lang(lang):
                    rc, out, err = run_cli(*argv)
                self.assertEqual(rc, 0, (argv, err))
                self.assertTrue(out.strip(), argv)
                self.text("%s [%s]" % (" ".join(argv), lang), out)
        self.clean()


class McpGuardTest(GuardCase):
    def session(self, lang, messages):
        env = dict((k, v) for k, v in os.environ.items() if not k.startswith(("FAKE_CTL_", "KASKAD_MCP_")))
        env.update({"PYTHONPATH": LIB, "KASKAD_ROOT": self.root, "KASKAD_MCP_CTL": "", "KASKAD_MCP_LANG": lang,
                    "KASKAD_MCP_AUDIT": os.path.join(self.root, "mcp-audit.log"), "LANG": "en_US.UTF-8",
                    "LC_ALL": "en_US.UTF-8"})
        data = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in init_messages() + messages).encode("utf-8")
        proc = subprocess.run([sys.executable, "-m", "kaskad.mcp"], input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env, timeout=180, cwd=self.root)
        return Session(proc, self.root)

    def test_the_words_for_the_ai(self):
        """actions.json и AGENT_GUIDE.md не обещают убранного (отрицание «без балла 0–1000» — можно)."""
        with open(os.path.join(ROOT, "lib", "kaskad", "mcp", "actions.json"), encoding="utf-8") as f:
            acts = json.load(f)["actions"]
        for name in ("host_score", "host_bench"):
            for path, _key, text in walk(acts[name]):
                if text is not None:
                    self.found.extend(("actions.json %s %s" % (name, path), p) for p in promises(text))
        with open(os.path.join(ROOT, "lib", "kaskad", "mcp", "AGENT_GUIDE.md"), encoding="utf-8") as f:
            guide = f.read()
        for n, line in enumerate(guide.splitlines(), 1):
            self.found.extend(("AGENT_GUIDE.md:%d" % n, p) for p in promises(line))
        self.clean()

    def test_the_answers_of_host_score(self):
        msgs = [req(1, "tools/list"), call(2, "host_score"), call(3, "host_score", {"action": "history", "days": 40}),
                call(4, "host_score", {"action": "show", "id": self.bad})]
        for lang in ("ru", "en"):
            s = self.session(lang, msgs)
            tools = dict((t["name"], t) for t in s.result(1)["tools"])
            for name in ("host_score", "host_bench"):
                d = tools[name]["description"]
                self.found.extend(("tools/list %s [%s]" % (name, lang), p) for p in promises(d))
            for i in (2, 3, 4):
                r = s.result(i)
                self.assertFalse(r.get("isError"), (lang, i, r))
                self.assertTrue(r.get("structuredContent"), (lang, i, r))
                self.doc("MCP host_score #%d [%s]" % (i, lang), r)
            self.clean()
            self.assertIn("summary", s.result(2)["structuredContent"])


class FeedGuardTest(GuardCase):
    def events(self, args):
        L = live.Live()
        L._loaded = True
        L.publish([{"kind": "bench_done", "args": args}], {}, {}, {"exits": {}}, self.now)
        return L.take_published()

    def test_bench_done_of_every_kind(self):
        """Лента и бот: bench_done по настоящему пути (collect.bench_done_args — rel узла после проверки), без числа
        (оценку не прочитать), частичная, сбой и событие старого вида (total/band/weak) — тексты RU/EN и строка бота."""
        base = {"ts": self.now - H, "id": self.bad, "mode": "full", "by": "schedule", "status": "ok", "why": None}
        cases = [collect.bench_done_args(dict(base)),
                 collect.bench_done_args(dict(base, status="partial")),
                 collect.bench_done_args(dict(base, status="failed", why="error")),
                 dict(base, total=40.0, band="barely", weak="down"), dict(base, total=712.0, band="good")]
        with mock.patch.object(HS, "node_doc", side_effect=RuntimeError("no base")):
            cases.append(collect.bench_done_args(dict(base, mode="quick")))
        for a in cases:
            for ev in self.events(a):
                self.doc("bench_done %r" % (a,), ev)
                for lang in ("ru", "en"):
                    self.text("bench_done [%s]" % lang, live.event_text(ev, lang))
                    with i18n.use_lang(lang):
                        self.text("бот [%s]" % lang, monitor.gw_message([monitor.HostLine(monitor.gw_line(ev))]))
        self.clean()
        self.assertIsNotNone(cases[0].get("rel"), cases[0])                # the history gives rel_total

    def test_stored_events_of_before_the_cut(self):
        """REV-score-cut З-2: события, уже сохранённые в live.json узлом dev-3374 (ключ bench_barely*, bench_done с
        {total}/{band} без rel), в ленте и в боте — «закончена» без числа по сегодняшним правилам (частичная и сбой — по
        статусу), не сырой ключ и не «{rel}»."""
        old = {"ts": self.now - H, "id": self.bad, "mode": "full", "by": "schedule", "status": "ok", "why": None,
               "total": 40, "band": "barely", "weak": "down", "mode_text": {"key": "host_bench.mode_in.full"},
               "band_text": {"key": "score.band.barely"}, "weak_text": {"key": "score.link.down"}}
        stored = [("gw_live.event.bench_barely", old, "bench_done_bare"),
                  ("gw_live.event.bench_barely_bare", old, "bench_done_bare"),
                  ("gw_live.event.bench_done", dict(old, total=712, band="good"), "bench_done_bare"),
                  ("gw_live.event.bench_done", dict(old, status="partial"), "bench_partial"),
                  ("gw_live.event.bench_failed", dict(old, status="failed", why="error",
                                                      why_text={"key": "bench_run.why.error"}), "bench_failed")]
        for key, args, want in stored:
            ev = {"kind": "bench_done", "key": key, "args": args, "ts": self.now - H, "level": "info"}
            for lang in ("ru", "en"):
                got = live.event_text(ev, lang)
                with i18n.use_lang(lang):
                    line = monitor.gw_line(ev)
                    expect = i18n.tr("gw_live.event." + want, mode_text=i18n.tr("host_bench.mode_in.full"),
                                     why_text=i18n.tr("bench_run.why.error"))
                for x in (got, line):
                    self.assertNotIn("{", x, (key, lang))
                    self.assertNotIn("gw_live.", x, (key, lang))
                    self.text("сохранённое bench_done [%s]" % lang, x)
                self.assertEqual(got, expect, (key, lang))
        self.clean()

    def test_the_reasons_of_a_day(self):
        """Причина дня «drop» (резкое падение) — со звеном и сырыми цифрами, у группы — rel, иначе одно звено."""
        days = [self.ok("GET", "/api/host/day/" + d["day"]) for d in self.score()["days"]]
        codes = set(d["reason"]["code"] for d in days if d.get("reason"))
        self.assertIn("drop", codes)                                       # the history reaches the path
        for args in ({"link": "down", "test": "net_down", "now": 510.0, "usual": 1700.0, "unit": "Мбит/с", "rel": 30},
                     {"link": "net_bw", "rel": 45}, {"link": "crypto"}):
            for lang in ("ru", "en"):
                with i18n.use_lang(lang):
                    msg = HS.reason_msg({"code": "drop", "args": args})["msg"]
                self.assertTrue(msg and not msg.startswith("score."), (args, lang, msg))
                self.text("причина дня drop [%s]" % lang, msg)
        self.clean()


class CatalogGuardTest(ScoreCase):
    def test_the_texts_of_the_surfaces(self):
        """Каталоги текстов поверхностей: ни слов-вердиктов, ни ключей убранного (полосы, ёмкость, «годится для…»,
        бюджет, звезда, «почти не работает» ленты, итог 0–1000 в CLI)."""
        found = []
        for lang in ("ru", "en"):
            for name in ("score", "host_bench", "host_sched", "gw_live", "bench_run"):
                path = os.path.join(ROOT, "lib", "kaskad", "i18n", "catalog", lang, name + ".json")
                with open(path, encoding="utf-8") as f:
                    cat = json.load(f)
                found.extend((lang, k) for k in cat if k.startswith(GONE_KEYS))
                found.extend((lang, k, v) for k, v in cat.items() if isinstance(v, str) and WORDS.search(v))
        self.assertEqual(found, [])


if __name__ == "__main__":
    import unittest
    unittest.main()

# -*- coding: utf-8 -*-
"""Короткие ссылки подписки (lib/shortlinks.py, docs/SUBSCRIPTION.md §11) без службы и моста: правила владельца по
часам — подменными часами, поэтому окна 15 минут и 30 дней, лестница банов (сутки → месяц → месяц), заморозка и
предел 5 запросов в час проверяются за миллисекунды. Сквозной прогон со службой, мостом и nginx —
tests/test_short_dryrun.sh.
"""
import json
import os
import random
import shutil
import stat
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import shortlinks as sl  # noqa: E402

DAY, MONTH = sl.DAY, sl.MONTH


class Clock(object):
    def __init__(self, t=1800000000.0):
        self.t = t

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t += seconds


class Base(unittest.TestCase):
    """Каталог продукта: config.json с доменом, users.json, подписки anna и bob, .env бота с токеном и админом."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ga-short-unit.")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.gdir = os.path.join(self.tmp, "goamnezia")
        self.subs = os.path.join(self.gdir, "subs")
        for name in ("anna", "bob"):
            self.make_sub(name)
        with open(os.path.join(self.gdir, "config.json"), "w") as fh:
            json.dump({"domain": "example.com"}, fh)
        with open(os.path.join(self.gdir, "users.json"), "w") as fh:
            json.dump({"users": [{"name": "anna", "label": "Анна"}, {"name": "bob", "label": "Боб"}]}, fh,
                      ensure_ascii=False)
        self.bot = os.path.join(self.tmp, "bot")
        os.makedirs(self.bot)
        self.env("BOT_TOKEN=1:x\nALLOWED_IDS=111\n")
        self.run_dir = os.path.join(self.tmp, "run")
        self.clock = Clock()
        self.audit = []
        self.links = self.make_links()

    def make_links(self, **kw):
        return sl.ShortLinks(gdir=self.gdir, bot_dir=self.bot, lock_dir=self.run_dir, now=self.clock,
                             audit=lambda a, d: self.audit.append((a, d)), **kw)

    def make_sub(self, name, generation=1):
        d = os.path.join(self.subs, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "meta.json"), "w") as fh:
            json.dump({"user": name, "generation": generation}, fh)
        with open(os.path.join(d, "secret"), "w") as fh:
            fh.write((name[0].upper() * 43)[:43])

    def env(self, text):
        with open(os.path.join(self.bot, ".env"), "w") as fh:
            fh.write(text)

    def state(self):
        with open(os.path.join(self.subs, ".short", "state.json")) as fh:
            return json.load(fh)

    def events(self, queue="short-confirm"):
        d = os.path.join(self.run_dir, queue)
        try:
            names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
        except OSError:
            return []
        out = []
        for n in names:
            with open(os.path.join(d, n)) as fh:
                out.append(json.loads(fh.read()))
        return out

    def code(self, name="anna", suffix="all.yaml"):
        return self.links.ensure(name, suffix)

    def login(self, display, ip):
        v = self.links.visit(display, ip, browser=True)
        self.assertEqual((v["do"], v["state"]), ("page", "wait"), v)
        return v["request"], v["number"]

    def wrong(self, rid, number):
        req = self.state()["requests"][rid]
        return str(next(o for o in req["options"] if o != number))

    def miss(self, ip, *codes):
        for c in codes:
            self.assertEqual(self.links.visit(c, ip, browser=True)["do"], "404")

    def addr(self, ip):
        return self.state()["addrs"].get(sl.addr_key(ip)) or {}


class CodeTests(Base):
    def test_alphabet_generator_display_and_normalize(self):
        self.assertEqual(len(sl.ALPHABET), 32)
        self.assertFalse(set("ILOU") & set(sl.ALPHABET))
        for _ in range(300):
            code = sl.new_code()
            self.assertRegex(code, sl.CODE_RE)
            shown = sl.display_of(code)
            self.assertEqual(shown.replace("-", ""), code)
            self.assertTrue(1 <= shown.count("-") <= 3 and "--" not in shown, shown)
            self.assertFalse(shown.startswith("-") or shown.endswith("-"), shown)
            self.assertEqual(sl.normalize(shown.lower()), code)
        self.assertEqual(sl.normalize("Oo-Il-1L-ab-cd"), "", "10 знаков — не код")
        self.assertEqual(sl.normalize("Oo-Il-1L-ab"), "001111AB")
        for bad in ("UUUUUUUU", "NV43.9BKX", "NV439BK", "", None, "NV43_9BKX"):
            self.assertEqual(sl.normalize(bad), "", bad)

    def test_ensure_is_idempotent_and_needs_the_bot(self):
        a = self.code()
        self.assertTrue(a["new"])
        self.assertEqual(a["url"], "https://example.com/" + a["display"])
        b = self.code()
        self.assertEqual((b["code"], b["display"], b["new"]), (a["code"], a["display"], False))
        path = os.path.join(self.subs, "anna", "short.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.subs, ".short")).st_mode), 0o700)
        self.env("BOT_TOKEN=1:x\nALLOWED_IDS=\n")
        with self.assertRaises(sl.ShortError) as err:
            self.code(suffix="all.txt")
        self.assertEqual(err.exception.code, "short_need_bot")
        with self.assertRaises(sl.ShortError) as err:
            self.links.ensure("ghost", "all.yaml")
        self.assertIn(err.exception.code, ("short_need_bot", "sub_not_found"))
        self.env("BOT_TOKEN=1:x\nALLOWED_IDS=111\n")
        with self.assertRaises(sl.ShortError) as err:
            self.links.ensure("ghost", "all.yaml")
        self.assertEqual(err.exception.code, "sub_not_found")
        with self.assertRaises(sl.ShortError) as err:
            self.links.ensure("anna", "mihomo.yaml")
        self.assertEqual(err.exception.code, "bad_suffix")

    def test_codes_are_unique_across_subscriptions_and_graveyard(self):
        seen = set()
        for name in ("anna", "bob"):
            for suffix in sl.SUFFIXES:
                seen.add(self.links.ensure(name, suffix)["code"])
        self.assertEqual(len(seen), 2 * len(sl.SUFFIXES))
        st = self.state()
        for c in seen:
            self.assertTrue(self.links._taken(st, c, self.clock()))

    def test_env_is_read_like_the_bot_and_ssh(self):
        cases = [("BOT_TOKEN=1:x\nALLOWED_IDS=111\n", True),
                 ("export BOT_TOKEN='1:x'\n  ALLOWED_IDS = 111, 222  # админы\n", True),
                 ("BOT_TOKEN=1:x\nALLOWED_IDS=111\nALLOWED_IDS=\n", False),          # побеждает последняя строка
                 ("# BOT_TOKEN=1:x\nALLOWED_IDS=111\n", False),
                 ('BOT_TOKEN="1:x # не комментарий"\nALLOWED_IDS=" , "\n', False),
                 ("BOT_TOKEN=\nALLOWED_IDS=111\n", False)]
        for text, want in cases:
            self.env(text)
            self.assertEqual(self.make_links().available(), want, text)
        self.assertEqual(sl.read_env(os.path.join(self.bot, "nope")), {})
        self.env('BOT_TOKEN="1:x # не комментарий"\nX=a # комментарий\n')
        self.assertEqual(sl.read_env(os.path.join(self.bot, ".env")), {"BOT_TOKEN": "1:x # не комментарий", "X": "a"})


class WordsAndShapeTests(Base):
    """Слова сайта — не коды (решение 04.10.2026 после сведения): у выданного кода всегда не меньше двух цифр и двух
    букв, а кодом считается только путь, где после нормализации ровно 8 знаков алфавита и хотя бы одна цифра набрана
    цифрой. /download (D0WN10AD после O → 0, L → 1) и прочие слова — обычный 404: без балла и без тревоги."""

    WORDS = ("download", "wp-admin", "phpmyadmin", "robots", "contacts", "database", "password", "register",
             "settings", "Download", "WP-ADMIN", "doc-pages", "about-us", "oolllioo")

    def test_generated_codes_have_two_digits_and_two_letters(self):
        seen = set()
        for _ in range(10000):
            code = sl.new_code()
            digits = sum(1 for ch in code if ch.isdigit())
            self.assertTrue(digits >= 2 and len(code) - digits >= 2, code)
            self.assertEqual(sl.as_code(code), code, "выданный код — всегда код")
            seen.update(code)
        self.assertEqual(seen, set(sl.ALPHABET), "все 32 знака встречаются")
        # Число вариантов — по числу цифр d = 2…6: C(8, d) · 10^d · 22^(8−d), независимо от формулы модуля
        by_digits = (28 * 10 ** 2 * 22 ** 6, 56 * 10 ** 3 * 22 ** 5, 70 * 10 ** 4 * 22 ** 4, 56 * 10 ** 5 * 22 ** 3,
                     28 * 10 ** 6 * 22 ** 2)
        self.assertEqual(sl.code_space(), sum(by_digits))
        self.assertEqual(sl.code_space(), 843227123200)

    def test_site_words_are_not_codes_but_typed_codes_are(self):
        for word in self.WORDS:
            self.assertEqual(sl.as_code(word), "", word)
        self.assertEqual(sl.normalize("download"), "D0WN10AD", "без правила о цифре это слово было бы кодом")
        self.assertEqual(sl.as_code("NV43-9BKX"), "NV439BKX")
        self.assertEqual(sl.as_code("nv43 9bkx"), "NV439BKX", "строчными и с пробелом — тот же код")
        self.assertEqual(sl.as_code("nv4o-9bkx"), "NV409BKX", "O вместо 0 — можно, если другие цифры набраны цифрами")
        self.assertEqual(sl.as_code("OOIIABCD"), "", "одни буквы — не код, даже если O и I дали цифры")
        self.assertEqual(sl.target_key("nv43 9bkx"), ("code", "NV439BKX"), "снятие заморозки понимает тот же ввод")

    def test_words_score_no_points_and_raise_no_alarm(self):
        a = self.code()
        for i in range(150):
            word = self.WORDS[i % len(self.WORDS)]
            v = self.links.visit(word, "10.7.%d.%d" % (i // 200, i % 200 + 1), browser=True)
            self.assertEqual(v, {"do": "404"}, word)
        st = self.state()
        self.assertEqual((st["addrs"], st["misses"]), ({}, {}), "ни балла, ни счёта промахов")
        self.assertEqual(self.events("notify"), [], "и тревоги нет")
        self.assertEqual(self.events(), [], "и запросов в бот")
        # Те же адреса настоящими неверными кодами — баллы как прежде
        self.miss("10.7.0.1", "ZZZZ2222")
        self.assertEqual(self.addr("10.7.0.1")["p"], 1)
        self.assertEqual(self.links.visit(a["display"].lower(), "10.7.0.2", browser=True)["do"], "page")

    # Даты и номера: после нормализации букв меньше двух (O, I и L, ставшие цифрами, — не буквы)
    NUMBERS = ("12345678", "2024-10-01", "20241001", "2024 10 01", "12-34-56-78", "1234567A", "8765432z", "OOII1234",
               "1o1o-1o1o", "0000-0000", "2O24-1O-O1")

    def test_dates_and_numbers_are_not_codes_and_score_no_points(self):
        self.code()
        for path in self.NUMBERS:
            self.assertEqual(sl.as_code(path), "", path)
        self.assertEqual(sl.normalize("1234567A"), "1234567A", "без правила о буквах это был бы код")
        for i in range(150):
            path = self.NUMBERS[i % len(self.NUMBERS)]
            self.assertEqual(self.links.visit(path, "10.8.%d.%d" % (i // 200, i % 200 + 1), browser=True),
                             {"do": "404"}, path)
        st = self.state()
        self.assertEqual((st["addrs"], st["misses"]), ({}, {}), "ни балла, ни счёта промахов")
        self.assertEqual((self.events("notify"), self.events()), ([], []), "ни тревоги, ни запросов в бот")
        # Две буквы и набранная цифра — уже код: промах по нему даёт балл, как прежде
        self.assertEqual(sl.as_code("123456AB"), "123456AB")
        self.miss("10.8.0.1", "123456AB")
        self.assertEqual(self.addr("10.8.0.1")["p"], 1)

    def test_copy_frames_from_messengers_are_the_same_code(self):
        # SL-LOG-02/03: «КОД.», «(КОД)», «[КОД]», ««КОД»», «“КОД”», «*КОД*», «_КОД_», «`КОД`», «<КОД>», «:КОД», «.КОД»,
        # «КОД%20», «КОД%09», «КОД•» — обрамление копирования снимает нормализация с обоих краёв; в середине кода это
        # уже не обрамление, а не код
        frames = ("\t", " ", "\u00a0", "!", '"', "'", "(", ")", ",", ".", ":", ";", "<", ">", "?", "[", "]", "_", "`",
                  "{", "}", "~", "*", "+", "\u00ab", "\u00bb", "\u2013", "\u2014", "\u2018", "\u2019", "\u201c",
                  "\u201d", "\u201e", "\u2022", "\u2026")
        for ch in frames:
            for typed in ("NV43-9BKX", "nv43 9bkx", "NV439BKX"):
                for framed in (typed + ch, ch + typed, ch + typed + ch, ch * 3 + typed + ch * 6):
                    self.assertEqual(sl.normalize(framed), "NV439BKX", repr(framed))
                    self.assertEqual(sl.as_code(framed), "NV439BKX", repr(framed))
        for typed in ("(NV43-9BKX).", "\u00abNV43-9BKX\u00bb.", "*_NV43-9BKX_*", "`nv4o-9bkx`", " \u201cNV43-9BKX\u201d\u2026",
                      ".NV43-9BKX", "..NV43-9BKX", "<NV43-9BKX>", ":NV43-9BKX", "\u2022 NV43-9BKX", "NV43-9BKX\t"):
            self.assertIn(sl.as_code(typed), ("NV439BKX", "NV409BKX"), repr(typed))
        for bad in ("NV43.9BKX", "N(V43-9BKX", "NV439BK.", "(NV439BK)", "NV43-9BKX.x", "xNV43-9BKX", "NV43-9BKX\u00a7",
                    "#NV43-9BKX", "...", "()", ""):
            self.assertEqual(sl.normalize(bad), "", repr(bad))
        # Для правил «цифра, набранная цифрой» и «две буквы» обрамление не в счёт
        for bad in ("(ABCDEFGH)", "*OOII1234*", "\u00ab1234567A\u00bb", ".2024-10-01.", "(download)", "_12345678_"):
            self.assertEqual(sl.as_code(bad), "", repr(bad))
        self.assertEqual(sl.target_key("(nv43-9bkx)."), ("code", "NV439BKX"), "снятие заморозки — тот же ввод")
        # Вход по ссылке в обрамлении — тот же код: браузеру страница подтверждения, балла нет
        a = self.code()
        v = self.links.visit("(" + a["display"] + ")\u2026", "10.10.0.1", browser=True)
        self.assertEqual((v["do"], v.get("state")), ("page", "wait"), v)
        self.assertEqual(self.addr("10.10.0.1").get("p", 0), 0)
        self.assertEqual(set(sl.FRAME), set(frames), "набор обрамления — тот же, что ловит nginx (GA_SITE_SHORT_FRAME)")

    def test_honest_codes_typed_with_o_i_l_stay_codes(self):
        # Выданный код, набранный строчными, с пробелом или тире и с O, I, L вместо 0 и 1 — тот же код, пока хоть одна
        # цифра набрана цифрой: две буквы у него есть всегда, а O, I, L их не отнимают
        rnd = random.Random(11)
        for _ in range(3000):
            code = sl.new_code()
            keep = rnd.choice([i for i, ch in enumerate(code) if ch.isdigit()])
            typed = "".join(ch if i == keep else rnd.choice("Oo") if ch == "0" else rnd.choice("IiLl") if ch == "1"
                            else rnd.choice((ch, ch.lower())) for i, ch in enumerate(code))
            typed = typed[:3] + rnd.choice("- ") + typed[3:]
            self.assertEqual(sl.as_code(typed), code, typed)
        self.assertEqual(sl.as_code("OAIB2345"), "0A1B2345", "код из двух букв — с O и I вместо 0 и 1")
        a = self.code()
        swapped = a["display"].lower().replace("0", "o").replace("1", "l")
        if any(ch.isdigit() for ch in swapped):
            self.assertEqual(self.links.visit(swapped, "10.9.0.1", browser=True)["do"], "page", swapped)


class LoginTests(Base):
    def test_browser_gets_a_request_app_gets_404(self):
        a = self.code()
        self.assertEqual(self.links.visit(a["display"], "198.51.100.1")["do"], "404", "приложение без доверия")
        self.assertEqual(self.events(), [])
        rid, number = self.login(a["display"].lower(), "198.51.100.1")
        self.assertTrue(10 <= number <= 99)
        self.assertRegex(rid, r"^[A-Za-z0-9_-]{16}$")
        ev = self.events()
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["event"], "short_confirm")
        self.assertEqual(sorted(ev[0]), sorted(["ts", "event", "request", "kind", "name", "label", "suffix", "device",
                                                "ip", "geo", "options", "who", "created", "expires", "left"]))
        self.assertNotIn("number", ev[0])
        self.assertEqual(len(set(ev[0]["options"])), 10)
        self.assertIn(number, ev[0]["options"])
        pending = self.links.pending()
        self.assertEqual([p["request"] for p in pending], [rid])
        self.assertNotIn("number", pending[0])
        self.assertTrue(self.links.visit(a["display"], "198.51.100.1", browser=True, head=True)["do"] == "404",
                        "HEAD браузера запроса не создаёт")

    def test_open_window_15_minutes_then_trust_30_days_sliding(self):
        a = self.code()
        rid, number = self.login(a["display"], "198.51.100.1")
        self.assertEqual(self.links.decide(rid, str(number), "bot:Иван")["decision"], "ok")
        self.assertEqual(self.links.visit(a["display"], "198.51.100.1", rid=rid)["status"]["decision"], "ok")
        self.clock.tick(899)
        v = self.links.visit(a["display"], "198.51.100.1")
        self.assertEqual(v["do"], "serve")
        self.clock.tick(2)
        self.assertEqual(self.links.visit(a["display"], "198.51.100.1")["do"], "404", "15 минут без забора")
        # Снова: подтверждение и первый забор в окне — доверие на 30 дней
        self.clock.tick(sl.REQUEST_TTL)
        rid, number = self.login(a["display"], "198.51.100.1")
        self.links.decide(rid, str(number), "bot:Иван")
        self.clock.tick(100)
        self.assertEqual(self.links.visit(a["display"], "198.51.100.1")["do"], "serve")
        self.links.fetched(a["code"], "198.51.100.1")
        acc = self.state()["access"][a["code"] + " 198.51.100.1"]
        self.assertEqual((acc["s"], acc["u"]), ("trust", int(self.clock() + sl.TRUST_TTL)))
        self.clock.tick(29 * DAY)
        self.assertEqual(self.links.visit(a["display"], "198.51.100.1")["do"], "serve")
        self.links.fetched(a["code"], "198.51.100.1")
        self.assertEqual(self.state()["access"][a["code"] + " 198.51.100.1"]["u"], int(self.clock() + sl.TRUST_TTL),
                         "окно сдвигается с каждым забором")
        before = self.state()["access"][a["code"] + " 198.51.100.1"]["u"]
        self.clock.tick(600)
        self.links.fetched(a["code"], "198.51.100.1")
        self.assertEqual(self.state()["access"][a["code"] + " 198.51.100.1"]["u"], before,
                         "сдвиг пишется не чаще раза в час")
        self.clock.tick(31 * DAY)
        self.assertEqual(self.links.visit(a["display"], "198.51.100.1")["do"], "404", "30 дней без забора")

    def test_one_pending_per_code_and_five_per_hour_expiry_is_not_a_refusal(self):
        a = self.code()
        rid, number = self.login(a["display"], "198.51.100.1")
        self.assertEqual(self.login(a["display"], "198.51.100.1"), (rid, number), "тот же адрес — тот же запрос")
        busy = self.links.visit(a["display"], "198.51.100.2", browser=True)
        self.assertEqual((busy["do"], busy["state"]), ("page", "busy"))
        with self.assertRaises(sl.ShortError) as err:
            self.links.replace_request("anna", "all.yaml", "page")
        self.assertEqual(err.exception.code, "short_pending", "со страницы — ждать (администратору вход не помеха)")
        for _ in range(4):
            self.clock.tick(sl.REQUEST_TTL + 1)
            self.login(a["display"], "198.51.100.1")
        self.clock.tick(sl.REQUEST_TTL + 1)
        lim = self.links.visit(a["display"], "198.51.100.1", browser=True)
        self.assertEqual((lim["do"], lim["state"]), ("page", "limit"))
        self.assertTrue(0 < lim["left"] <= 3600)
        expired = [e for e in self.events() if e["event"] == "short_decided" and e["status"] == "expired"]
        self.assertEqual(len(expired), 5)
        self.assertEqual(self.state()["codes"][a["code"]].get("w", 0), 0, "истечение отказом не считается")
        self.clock.tick(lim["left"])
        self.login(a["display"], "198.51.100.1")

    def test_admin_replace_closes_someone_elses_pending_login(self):
        # SL-UX-03: ожидающий вход или замена по коду не мешают администратору — из панели, SSH и бота они закрыты «заменён»
        # (short_decided, status swapped, by = who), замена идёт дальше; со страницы — по-прежнему short_pending
        for i, who in enumerate(("panel", "ssh", "bot", "bot:111111111")):
            a = self.code(suffix=("all.yaml", "all.txt", "all.vpn", "awg20.conf")[i])
            ip = "198.51.100.%d" % (i + 1)
            rid, _number = self.login(a["display"], ip)
            with self.assertRaises(sl.ShortError) as err:
                self.links.replace_request("anna", a["suffix"], "page", ip="203.0.113.%d" % (i + 1))
            self.assertEqual(err.exception.code, "short_pending", "со страницы — ждать, как раньше")
            rep = self.links.replace_request("anna", a["suffix"], who)
            self.assertTrue(rep["request"] and rep["request"] != rid, who)
            req = self.state()["requests"][rid]
            self.assertEqual((req["status"], req["by"]), ("swapped", who))
            closed = [e for e in self.events() if e["event"] == "short_decided" and e["request"] == rid]
            self.assertEqual([(e["status"], e["by"]) for e in closed], [("swapped", who)])
            self.assertEqual(self.links.visit(a["display"], ip, browser=True, rid=rid)["status"]["decision"], "swapped",
                             "страница входа узнаёт итог")
            self.assertEqual(self.state()["requests"][rep["request"]]["kind"], "replace")
        # Прежняя замена тоже не держит администратора: закрыта «заменён» (by — кто заменил), новая — своя;
        # со страницы при ожидающей замене — по-прежнему short_pending
        prev = [rid for rid, r in self.state()["requests"].items()
                if r.get("kind") == "replace" and r.get("suffix") == "all.yaml" and r.get("status") == "pending"]
        self.assertEqual(len(prev), 1)
        with self.assertRaises(sl.ShortError) as err:
            self.links.replace_request("anna", "all.yaml", "page")
        self.assertEqual(err.exception.code, "short_pending")
        rep = self.links.replace_request("anna", "all.yaml", "ssh")
        old = self.state()["requests"][prev[0]]
        self.assertEqual((old["status"], old["by"]), ("swapped", "ssh"))
        self.assertEqual(self.links.status(prev[0])["decision"], "swapped", "панель и SSH узнают итог прежней замены")
        self.assertEqual(self.state()["requests"][rep["request"]]["status"], "pending")

    def test_first_presser_decides(self):
        a = self.code()
        rid, number = self.login(a["display"], "198.51.100.1")
        self.assertEqual(self.links.decide(rid, "deny", "bot:Иван"), {"decision": "denied"})
        again = self.links.decide(rid, str(number), "bot:Пётр")
        self.assertEqual((again["decision"], again["already"], again["by"]), ("denied", True, "bot:Иван"))
        decided = [e for e in self.events() if e["event"] == "short_decided"]
        self.assertEqual(decided, [{"ts": decided[0]["ts"], "event": "short_decided", "request": rid,
                                    "status": "denied", "by": "bot:Иван"}])
        with self.assertRaises(sl.ShortError) as err:
            self.links.decide("A" * 16, "10", "x")
        self.assertEqual(err.exception.code, "short_request_not_found")
        with self.assertRaises(sl.ShortError) as err:
            self.links.decide(rid, "100", "x")
        self.assertEqual(err.exception.code, "short_bad_answer")

    def test_page_transition_after_ok_serves_or_asks_for_a_new_check_never_a_new_request(self):
        # SHORT-02, SHORT-03 (владелец 05.10): после «Вход подтверждён» «Показать содержимое» ведёт на тот же адрес с
        # меткой ?go=<запрос>. Открытый адрес получает подписку (первый забор — как у приложения); не открытый и не
        # доверенный — сменился (IPv4 ↔ IPv6, другая сеть) или время вышло — moved, «Нужна новая проверка», без нового
        # запроса в бот и без счёта в предел 5 в час: иначе новое число, подтверждение, снова переход — по кругу
        a = self.code()
        rid, number = self.login(a["display"], "198.51.100.1")
        self.assertEqual(self.links.decide(rid, str(number), "bot:Иван")["decision"], "ok")
        events, stamps = len(self.events()), list(self.state()["codes"][a["code"]]["rq"])
        moved = self.links.visit(a["display"], "2001:db8::7", browser=True, after=rid)
        self.assertEqual(moved, {"do": "page", "state": "moved"}, "адрес сменился — ни запроса, ни числа")
        self.assertEqual(len(self.events()), events, "в бот ничего нового")
        self.assertEqual(self.links.pending(), [])
        self.assertEqual(self.state()["codes"][a["code"]]["rq"], stamps, "в предел 5 запросов в час не засчитано")
        self.assertEqual(self.links.visit(a["display"], "2001:db8::7", browser=True, head=True, after=rid)["do"], "404")
        self.assertEqual(self.links.visit(a["display"], "2001:db8::7", after=rid)["do"], "404",
                         "приложению без доверия — 404, как и без метки")
        served = self.links.visit(a["display"].lower(), "198.51.100.1", browser=True, after=rid)
        self.assertEqual((served["do"], served["suffix"]), ("serve", "all.yaml"), "открытый адрес получает подписку")
        # Время вышло: 15 минут без забора — тоже moved, а не новый запрос
        self.clock.tick(sl.OPEN_TTL + 1)
        self.assertEqual(self.links.visit(a["display"], "198.51.100.1", browser=True, after=rid)["state"], "moved")
        self.assertEqual(len(self.events()), events)
        # Метка не спасает от балла за неверный код и не открывает чужой код
        self.assertEqual(self.links.visit("ZZZZ2222", "203.0.113.5", browser=True, after=rid)["do"], "404")
        self.assertEqual(self.state()["addrs"]["203.0.113.5"]["p"], 1)
        # Метка в счёт, только если это подтверждённый вход по этому самому коду (номер знает лишь его страница):
        # выдуманная, не по форме, от другого кода или неподтверждённая — как без метки, обычный запрос в бот. Так живой
        # код не проверить молча, мимо запроса в бот
        b = self.code(suffix="all.txt")
        rid_b, number_b = self.login(b["display"], "198.51.100.9")
        self.assertEqual(self.links.decide(rid_b, str(number_b), "bot:Иван")["decision"], "ok")
        confirms = len([e for e in self.events() if e["event"] == "short_confirm"])
        first = self.links.visit(a["display"], "2001:db8::7", browser=True, after="A" * 16)
        self.assertEqual((first["do"], first["state"]), ("page", "wait"), "выдуманная метка — обычный запрос в бот")
        self.assertEqual(len([e for e in self.events() if e["event"] == "short_confirm"]), confirms + 1)
        for mark in ("../x", rid_b, first["request"]):
            v = self.links.visit(a["display"], "2001:db8::7", browser=True, after=mark)
            self.assertEqual((v["do"], v["state"], v["request"]), ("page", "wait", first["request"]),
                             "не по форме, вход другого кода, неподтверждённый — как без метки: %s" % mark)


class FullLinkTests(Base):
    """SHORT-03 (владелец 05.10): после «Вход подтверждён» страница выдаёт этому устройству полную ссылку подписки —
    только по номеру подтверждённого входа по этому самому коду и 15 минут после решения; без баллов и банов."""

    def test_confirmed_only_for_the_ok_login_of_this_code_within_15_minutes(self):
        a = self.code()
        b = self.code(suffix="all.txt")
        rid, number = self.login(a["display"], "198.51.100.1")
        self.assertIsNone(self.links.confirmed(a["display"], rid), "до решения — ничего")
        self.assertEqual(self.links.decide(rid, str(number), "bot:Иван")["decision"], "ok")
        before = (self.state()["addrs"], len(self.events()))
        self.assertEqual(self.links.confirmed(a["display"].lower(), rid),
                         {"name": "anna", "suffix": "all.yaml", "left": sl.FULL_TTL})
        self.assertEqual(sl.FULL_TTL, sl.OPEN_TTL, "окно выдачи — как окно первого забора")
        for raw, mark in ((b["display"], rid), (a["display"], "A" * 16), (a["display"], "../x"), ("download", rid),
                          (a["display"], "")):
            self.assertIsNone(self.links.confirmed(raw, mark), (raw, mark))
        self.assertEqual((self.state()["addrs"], len(self.events())), before, "ни баллов, ни банов, ни событий")
        poll = self.links.visit(a["display"], "198.51.100.1", rid=rid)
        self.assertEqual((poll["status"]["full_left"], poll["here"]), (sl.FULL_TTL, True))
        self.assertFalse(self.links.visit(a["display"], "2001:db8::5", rid=rid)["here"], "опрос с другого адреса")
        self.clock.tick(sl.FULL_TTL - 1)
        self.assertEqual(self.links.confirmed(a["display"], rid)["left"], 1)
        self.clock.tick(1)
        self.assertEqual(self.links.confirmed(a["display"], rid), {"expired": True}, "окно вышло — «Нужна новая проверка»")
        self.assertEqual(self.links.visit(a["display"], "198.51.100.1", rid=rid)["status"]["full_left"], 0)

    def test_not_ok_decisions_and_killed_codes_give_nothing(self):
        a = self.code()
        rid, number = self.login(a["display"], "198.51.100.1")
        self.links.decide(rid, self.wrong(rid, number), "bot:Иван")
        self.assertIsNone(self.links.confirmed(a["display"], rid), "неверное число")
        self.clock.tick(1)
        rid, number = self.login(a["display"], "198.51.100.1")
        self.links.decide(rid, str(number), "bot:Иван")
        self.assertTrue(self.links.confirmed(a["display"], rid))
        self.links.kill("anna", "rotate")
        self.assertIsNone(self.links.confirmed(a["display"], rid), "перевыпуск гасит код — полной ссылки нет")
        c = self.code(name="bob")
        rid, _number = self.login(c["display"], "198.51.100.2")
        self.links.decide(rid, "deny", "bot:Иван")
        self.assertIsNone(self.links.confirmed(c["display"], rid), "«Это не я»")


class BanTests(Base):
    def test_ladder_day_month_month(self):
        self.code()
        ip = "203.0.113.5"
        self.miss(ip, "ZZZZ2222", "ZZZZ3333")
        self.assertEqual(self.addr(ip).get("p"), 2)
        self.miss(ip, "ZZZZ4444")
        rec = self.addr(ip)
        self.assertEqual((rec["lv"], rec["u"], rec["p"], rec["why"]), (1, int(self.clock() + DAY), 0, "misses"))
        self.clock.tick(DAY + 1)
        self.miss(ip, "YYYY2222", "YYYY3333", "YYYY4444")
        self.assertEqual((self.addr(ip)["lv"], self.addr(ip)["u"]), (2, int(self.clock() + MONTH)))
        self.clock.tick(MONTH + 1)
        self.miss(ip, "XXXX2222", "XXXX3333", "XXXX4444")
        self.assertEqual((self.addr(ip)["lv"], self.addr(ip)["u"]), (3, int(self.clock() + MONTH)))
        bans = [b for b in self.links.bans() if b["kind"] == "ip"]
        self.assertEqual([(b["target"], b["level"], b["reason"]) for b in bans], [(ip, 3, "misses")])
        self.assertEqual(self.links.unban(ip), {"unbanned": True, "kind": "ip"})
        self.miss(ip, "WWWW2222", "WWWW3333", "WWWW4444")
        self.assertEqual(self.addr(ip)["lv"], 1, "снятие начинает лестницу заново")

    def test_repeat_graveyard_and_old_generation_do_not_count(self):
        a = self.code()
        ip = "203.0.113.6"
        self.miss(ip, "ZZZZ2222", "zzzz-2222", "ZZZZ2222")
        self.assertEqual(self.addr(ip)["p"], 1, "повтор того же кода — не балл")
        rid, _n = self.login(a["display"], "198.51.100.9")
        self.links.decide(rid, "swap", "bot:Иван")
        self.miss(ip, a["display"], a["display"].lower())
        self.assertEqual(self.addr(ip)["p"], 1, "кладбище — не балл")
        b = self.code(name="bob")
        self.make_sub("bob", generation=2)              # перевыпуск без погашения: код прежнего поколения мёртв
        self.miss(ip, b["display"])
        self.assertEqual(self.addr(ip)["p"], 1, "код прежнего поколения — не балл")
        self.clock.tick(sl.GRAVE_TTL + 1)
        self.links.sweep(tidy=True)
        self.assertNotIn(a["code"], self.state()["graveyard"])
        before = self.addr(ip).get("p", 0)
        self.miss(ip, a["display"])
        self.assertEqual(self.addr(ip)["p"], before + 1, "через 30 дней старый код — уже просто неверный")

    def test_live_code_between_misses_resets_points(self):
        a = self.code()
        ip = "203.0.113.7"
        self.miss(ip, "ZZZZ2222", "ZZZZ3333")
        self.assertEqual(self.links.visit(a["display"], ip)["do"], "404")
        self.assertEqual(self.addr(ip)["p"], 0)
        self.miss(ip, "ZZZZ4444", "ZZZZ5555")
        self.assertNotIn("u", self.addr(ip))

    def test_ipv6_is_banned_by_64_and_ipv4_mapped_is_ipv4(self):
        self.code()
        for i, c in enumerate(("ZZZZ2222", "ZZZZ3333", "ZZZZ4444")):
            self.miss("2001:db8:5:6::%d" % (i + 1), c)
        self.assertEqual(self.state()["addrs"]["2001:db8:5:6::/64"]["lv"], 1)
        self.assertEqual(sl.addr_key("::ffff:198.51.100.3"), "198.51.100.3")
        self.assertEqual(sl.addr_key("2001:db8:5:6:aaaa:bbbb:cccc:dddd"), "2001:db8:5:6::/64")
        self.assertEqual(sl.addr_key("not an ip"), "")
        self.assertEqual(sl.target_key("2001:db8:5:6::/64"), ("ip", "2001:db8:5:6::/64"))
        self.assertEqual(sl.target_key("2001:db8:5:6::99"), ("ip", "2001:db8:5:6::/64"))
        self.assertEqual(sl.target_key("nv43-9bkx"), ("code", "NV439BKX"))
        self.assertEqual(sl.target_key("??"), ("", ""))

    def test_own_client_works_during_a_ban_other_codes_do_not(self):
        a = self.code()
        b = self.code(suffix="awg20.conf")
        ip = "198.51.100.1"
        rid, number = self.login(a["display"], ip)
        self.links.decide(rid, str(number), "x")
        self.links.fetched(a["code"], ip)
        self.miss(ip, "ZZZZ2222", "ZZZZ3333", "ZZZZ4444")
        self.assertEqual(self.addr(ip)["lv"], 1)
        self.assertEqual(self.links.visit(a["display"], ip)["do"], "serve")
        self.assertEqual(self.links.visit(b["display"], ip, browser=True)["do"], "404")

    def test_deny_bans_by_the_same_ladder_and_status_poll_still_works(self):
        a = self.code()
        ip = "198.51.100.4"
        rid, _n = self.login(a["display"], ip)
        self.assertEqual(self.links.decide(rid, "deny", "x"), {"decision": "denied"})
        rec = self.addr(ip)
        self.assertEqual((rec["lv"], rec["u"], rec["why"]), (1, int(self.clock() + DAY), "denied"))
        st = self.links.visit(a["display"], ip, rid=rid)
        self.assertEqual((st["do"], st["status"]["decision"], st["status"]["days"]), ("status", "denied", 1))
        self.assertEqual(self.links.visit(a["display"], ip, browser=True)["do"], "404")
        self.clock.tick(DAY + 1)
        rid, _n = self.login(a["display"], ip)
        self.links.decide(rid, "deny", "x")
        self.assertEqual((self.addr(ip)["lv"], self.addr(ip)["u"]), (2, int(self.clock() + MONTH)))
        self.assertEqual(self.links.visit(a["display"], ip, rid=rid)["status"]["days"], 30)

    def test_alarm_over_100_misses_an_hour_once_per_hour(self):
        self.code()
        for i in range(101):
            self.miss("10.1.%d.%d" % (i // 250, i % 250 + 1), "QQQQ%04d" % (2000 + i))
        alarms = self.events("notify")
        self.assertEqual([(e["event"], e["misses_hour"]) for e in alarms], [("short_alarm", 101)])
        self.miss("10.2.0.1", "QQQQ9999")
        self.assertEqual(len(self.events("notify")), 1)
        self.clock.tick(3601)
        for i in range(101):
            self.miss("10.3.%d.%d" % (i // 250, i % 250 + 1), "RRRR%04d" % (2000 + i))
        self.assertEqual(len(self.events("notify")), 2)

    def test_addr_table_is_capped_but_keeps_the_current_and_banned(self):
        self.code()
        old = sl.ADDR_MAX
        sl.ADDR_MAX = 5
        self.addCleanup(setattr, sl, "ADDR_MAX", old)
        self.miss("10.0.0.1", "ZZZZ2222", "ZZZZ3333", "ZZZZ4444")         # бан
        for i in range(2, 12):
            self.clock.tick(1)
            self.miss("10.0.0.%d" % i, "ZZZZ%04d" % (5000 + i))
        table = self.state()["addrs"]
        self.assertLessEqual(len(table), 5)
        self.assertIn("10.0.0.1", table, "действующий бан вытесняется последним")
        self.assertIn("10.0.0.11", table, "запись, ради которой место освобождали, на месте")


class FreezeAndReplaceTests(Base):
    def freeze_round(self, display, ip):
        statuses = []
        for _ in range(3):
            rid, number = self.login(display, ip)
            statuses.append(self.links.decide(rid, self.wrong(rid, number), "x")["decision"])
        return statuses

    def test_freeze_day_then_month_correct_answer_resets(self):
        a = self.code()
        rid, number = self.login(a["display"], "198.51.100.1")
        self.links.decide(rid, self.wrong(rid, number), "x")
        rid, number = self.login(a["display"], "198.51.100.2")
        self.links.decide(rid, self.wrong(rid, number), "x")
        rid, number = self.login(a["display"], "198.51.100.3")
        self.assertEqual(self.links.decide(rid, str(number), "x")["decision"], "ok")
        self.links.fetched(a["code"], "198.51.100.3")
        self.assertEqual(self.state()["codes"][a["code"]]["w"], 0, "верное число обнуляет серию")
        self.clock.tick(3600)
        self.assertEqual(self.freeze_round(a["display"], "198.51.100.4"), ["wrong", "wrong", "frozen"])
        cst = self.state()["codes"][a["code"]]
        self.assertEqual((cst["fl"], cst["fu"]), (1, int(self.clock() + DAY)))
        fr = self.links.visit(a["display"], "198.51.100.5", browser=True)
        self.assertEqual((fr["do"], fr["state"], fr["days"]), ("page", "frozen", 1))
        self.assertEqual(self.links.visit(a["display"], "198.51.100.3")["do"], "serve",
                         "доверенный адрес работает и у замороженного кода")
        self.clock.tick(DAY + 3600)
        self.assertEqual(self.freeze_round(a["display"], "198.51.100.6"), ["wrong", "wrong", "frozen"])
        self.assertEqual(self.state()["codes"][a["code"]]["fu"], int(self.clock() + MONTH))
        freezes = [b for b in self.links.bans() if b["kind"] == "code"]
        self.assertEqual([(b["target"], b["level"], b["name"], b["suffix"]) for b in freezes],
                         [(a["display"], 2, "anna", "all.yaml")])
        self.assertEqual(self.links.unban(a["display"].lower())["unbanned"], True)
        self.assertEqual(self.links.visit(a["display"], "198.51.100.7", browser=True)["state"], "wait")

    def test_swap_and_replace_issue_a_new_code_old_goes_to_graveyard(self):
        a = self.code()
        ip = "198.51.100.1"
        rid, number = self.login(a["display"], ip)
        self.links.decide(rid, str(number), "x")
        self.links.fetched(a["code"], ip)
        rid2, _n = self.login(a["display"], "198.51.100.2")
        res = self.links.decide(rid2, "swap", "bot:Иван")
        self.assertEqual(res["decision"], "swapped")
        self.assertEqual(res["url"], "https://example.com/" + res["display"])
        st = self.state()
        self.assertEqual((st["graveyard"][a["code"]]["why"], st["graveyard"][a["code"]]["until"]),
                         ("swap", int(self.clock() + sl.GRAVE_TTL)))
        self.assertFalse([k for k in st["access"] if k.startswith(a["code"])], "доверие старого кода снято")
        self.assertEqual(self.links.visit(a["display"], ip)["do"], "404")
        page = self.links.visit(a["display"], "198.51.100.2", rid=rid2)["status"]
        self.assertEqual(page, {"decision": "swapped", "left": 0}, "странице входа — без нового кода")
        self.assertEqual(self.links.status(rid2)["display"], res["display"], "мосту (администратору) — с ним")
        # Замена кнопкой ↻: число у просящего, верное — новый код; у замены swap нет
        rep = self.links.replace_request("anna", "all.yaml", "page", ip="198.51.100.7", device="iPhone · Safari")
        ev = self.events()[-1]
        self.assertEqual((ev["kind"], ev["who"], ev["ip"], ev["device"]), ("replace", "page", "198.51.100.7",
                                                                           "iPhone · Safari"))
        with self.assertRaises(sl.ShortError):
            self.links.decide(rep["request"], "swap", "x")
        res = self.links.decide(rep["request"], str(rep["number"]), "x")
        self.assertEqual(res["decision"], "ok")
        self.assertEqual(self.links.status(rep["request"], name="anna")["display"], res["display"])
        with self.assertRaises(sl.ShortError):
            self.links.status(rep["request"], name="bob")
        # «Это не я» у замены со страницы — бан адреса; у замены из панели адреса нет — и бана нет
        rep = self.links.replace_request("anna", "all.yaml", "page", ip="198.51.100.8")
        self.links.decide(rep["request"], "deny", "x")
        self.assertEqual(self.addr("198.51.100.8")["lv"], 1)
        rep = self.links.replace_request("anna", "all.yaml", "bot:123456789")
        self.assertEqual(self.events()[-1]["who"], "bot:123456789")
        self.assertEqual(self.links.decide(rep["request"], "deny", "bot:123456789")["decision"], "denied")
        self.assertEqual(self.events()[-1]["by"], "bot:123456789")

    def test_kill_buries_codes_and_closes_pending(self):
        a = self.code()
        b = self.code(suffix="awg20.vpn")
        rid, _n = self.login(a["display"], "198.51.100.1")
        self.assertEqual(self.links.kill("anna", "rotate"), 2)
        self.assertEqual(self.links.codes_of("anna"), {})
        st = self.state()
        self.assertEqual({st["graveyard"][c]["why"] for c in (a["code"], b["code"])}, {"rotate"})
        self.assertEqual(st["requests"][rid]["status"], "expired")
        self.assertEqual(self.links.decide(rid, "deny", "x")["decision"], "expired")
        self.assertEqual(self.links.kill("anna", "rotate"), 0)
        self.make_sub("anna", generation=2)
        self.assertEqual(self.code()["new"], True)
        with open(os.path.join(self.subs, "anna", "short.json")) as fh:
            self.assertEqual(json.load(fh)["all.yaml"]["generation"], 2)


class HousekeepingTests(Base):
    def test_reads_create_nothing_and_tidy_forgets_old_records(self):
        self.assertEqual(self.links.bans(), [])
        self.assertEqual(self.links.pending(), [])
        self.assertEqual(self.links.unban("198.51.100.1"), {"unbanned": False, "kind": "ip"})
        with self.assertRaises(sl.ShortError):
            self.links.status("A" * 16)
        self.assertFalse(os.path.exists(os.path.join(self.subs, ".short")), "чтение каталога состояния не заводит")
        a = self.code()
        rid, number = self.login(a["display"], "198.51.100.1")
        self.links.decide(rid, str(number), "x")
        self.miss("203.0.113.1", "ZZZZ2222")                                      # балл без бана
        self.miss("203.0.113.2", "ZZZZ3333", "ZZZZ4444", "ZZZZ5555")              # бан
        self.clock.tick(sl.DECIDED_KEEP + sl.ADDR_IDLE_KEEP + 1)
        self.links.sweep(tidy=True)
        st = self.state()
        self.assertNotIn(rid, st["requests"], "решённый запрос — только час")
        self.assertNotIn("203.0.113.1", st["addrs"], "баллы без бана — 30 дней")
        self.assertIn("203.0.113.2", st["addrs"], "запись с баном держит лестницу год")
        self.assertEqual(st["access"], {}, "кончившееся доверие убрано")
        self.clock.tick(sl.LEVEL_KEEP)
        self.links.sweep(tidy=True)
        self.assertNotIn("203.0.113.2", self.state()["addrs"])

    def test_event_queue_is_trimmed_and_files_are_private(self):
        old = sl.QUEUE_MAX
        sl.QUEUE_MAX = 3
        self.addCleanup(setattr, sl, "QUEUE_MAX", old)
        d = os.path.join(self.tmp, "q")
        for i in range(5):
            self.assertTrue(sl.emit_event(d, "short_alarm", {"misses_hour": i}, self.clock()))
        names = sorted(os.listdir(d))
        self.assertEqual(len(names), 3)
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
        with open(os.path.join(d, names[-1])) as fh:
            line = fh.read()
        self.assertEqual(json.loads(line)["misses_hour"], 4)
        self.assertTrue(line.endswith("\n") and line.count("\n") == 1)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, names[-1])).st_mode), 0o600)

    def test_state_survives_a_new_process_and_parallel_writers_do_not_lose_codes(self):
        a = self.code()
        rid, number = self.login(a["display"], "198.51.100.1")
        other = self.make_links()                                   # «другой процесс» — свой кеш, тот же диск
        self.assertEqual(other.decide(rid, str(number), "x")["decision"], "ok")
        self.assertEqual(self.links.visit(a["display"], "198.51.100.1")["do"], "serve", "служба видит решение моста")
        errors = []

        def worker(links, suffix):
            try:
                links.ensure("bob", suffix)
            except Exception as exc:                                # noqa: BLE001
                errors.append(exc)
        threads = [threading.Thread(target=worker, args=(self.links if i % 2 else other, s))
                   for i, s in enumerate(sl.SUFFIXES)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(self.links.codes_of("bob")), sorted(sl.SUFFIXES))

    def test_device_and_geo_parsers(self):
        cases = {"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                 "Version/17.0 Mobile/15E148 Safari/604.1": "iPhone · Safari",
                 "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36":
                     "Android · Chrome",
                 "Mozilla/5.0 (Linux; Android 12; BRAVIA 4K) AppleWebKit/537.36 Chrome/120.0 Safari/537.36":
                     "Android TV · Chrome",
                 "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36 Edg/126.0":
                     "Windows · Edge",
                 "clash-verge/v2.4.2": "Clash Verge", "curl/8.5.0": "curl", "": "", "???": ""}
        for agent, want in cases.items():
            self.assertEqual(sl.describe_device(agent), want, agent)
        self.assertEqual(sl.parse_mmdb('\n  "Moscow" <utf8_string>\n'), "Moscow")
        self.assertEqual(sl.parse_mmdb("Could not find an entry"), "")
        self.assertEqual(sl.parse_geoiplookup("GeoIP Country Edition: RU, Russian Federation\n"), "Russian Federation")
        self.assertEqual(sl.parse_geoiplookup("GeoIP Country Edition: IP Address not found\n"), "")
        self.assertEqual(sl.geo_lookup("10.0.0.1"), "", "внутренние адреса не ищутся")

    def test_cli_answers_one_json_line(self):
        import io
        import contextlib
        os.environ["GOAMNEZIA_DIR"] = self.gdir
        os.environ["GOAMNEZIA_BOT_DIR"] = self.bot
        os.environ["GOAMNEZIA_LOCK_DIR"] = self.run_dir
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("GOAMNEZIA_DIR", "GOAMNEZIA_BOT_DIR",
                                                                    "GOAMNEZIA_LOCK_DIR")])
        for argv, rc, want in ((["normalize", "--text", "nv43-9bkx"], 0, {"code": "NV439BKX"}),
                               (["available"], 0, {"available": True}),
                               (["ensure", "--name", "ghost", "--suffix", "all.yaml"], 3, {"error": "sub_not_found"}),
                               (["decide", "--request", "x", "--answer", "10"], 3, {"error": "short_request_not_found"}),
                               (["nope"], 2, {"error": "bad_arg"})):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                got = sl.main(argv)
            self.assertEqual(got, rc, argv)
            self.assertEqual(json.loads(buf.getvalue()), want, argv)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(sl.main(["ensure", "--name", "anna", "--suffix", "all.txt"]), 0)
        out = json.loads(buf.getvalue())
        self.assertEqual(sorted(out), ["code", "created", "display", "new", "suffix", "url"])


if __name__ == "__main__":
    unittest.main()

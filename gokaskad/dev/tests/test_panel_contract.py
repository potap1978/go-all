"""Every call of the panel has a route on the server with that very method: ``API`` of app.js and ``call()`` of
graph.js against the routes of ``kaskad.panel.MODULES``. A path the server does not know shows the man «Нет такого
пути» on a button that looks fine (3.2.11: the profile of a device went to PUT /api/gw/devices/{id}/rules, which
never existed — the mock answered it, the server did not)."""

import importlib
import inspect
import os
import re
import unittest

from kaskad import panel
from kaskad.panel import routes

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")

# what the panel puts into a path after such a prefix (ids come from the server's answers; kind — from KINDS)
SAMPLES = ((r"/api/forwards/$", "tcp"), (r"/api/forwards/tcp/$", "17000"), (r"/api/gw/inbounds/$", "wg"),
           (r"/api/gw/users/$", "u-1a2b3c4d"), (r"/api/gw/devices/$", "d-1a2b3c4d"),
           (r"/api/gw/exits/$", "e-41a19b9cbe"), (r"/api/gw/exit-subs/$", "s-1a2b3c"), (r"/api/gw/groups/$", "g-5f1a2b"),
           (r"/api/mcp/keys/$", "SHA256%3Aq3v9Yx0bZp2kLm7Nf4Rt8Hc1Wd6Ej5Ga0Ks2Uo9Ty3Iu"),     # 3.3.3: ключ ИИ-агента
           (r"/api/host/bench/$", "0123456789"),                                  # B15: деталь проверки
           (r"/api/host/day/$", "2026-10-19"))                                    # 6C: день полосы
# GET, что ждут дольше обычного чтения: путь -> (метод, путь), как долго ждёт тот (REV-3377-stand-fix 2: цена «Проверить
# сервер» — как прежний POST без ключа)
LONG_GETS = {"/api/host/bench/price": ("POST", "/api/host/bench")}
# строка запроса пути не меняет: (force ? qs(…) : ""), (dry ? "?dry=1" : ""), qs({…}), qs(o)
TERM = r'(?:"[^"]*"|enc\(\w+\)|\(force \? qs\(\{ force: 1 \}\) : ""\)|\(dry \? "\?dry=1" : ""\)|qs\(\{[^}]*\}\)|qs\(\w+\))'
CALL = re.compile(r'request\("(GET|POST|PUT|DELETE)", (%s(?: \+ %s)*)' % (TERM, TERM))


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def api_block(js):
    a = js.index("  const API = {")
    return js[a:js.index("\n  };\n", a)]


def build_path(expr):
    out = ""
    for term in expr.split(" + "):
        if term.startswith('"'):
            out += term[1:-1]
        elif term.startswith("enc(") and not term.startswith("qs("):
            sample = next((v for rx, v in SAMPLES if re.search(rx, out)), None)
            if sample is None:
                raise AssertionError("нет образца для %s после %s" % (term, out))
            out += sample
        # (force ? qs(…) : "") — строка запроса: путь не меняет
    return out


# Stage 1: routes of T7 (SPEC-stage1-exits §7.9) whose forms are not in the panel yet — «Настройки → Время» (§7.4)
# and the size of metrics.db (T3, the card of the gateway settings): the next step of T8 (R-s1-t8, «Часть 2»).
# The rest of the T7 routes have callers since T8 part 2 — the list holds only what is left; drop a name with its form.
UNTIL_T8 = ("gw_metrics_get", "gw_metrics_set", "settings_time_get", "settings_time_set")
# Stage 1, B6: the score API (host_score, host_day) waited here for the tab «Сейчас» of «Диагностика»; since П4-а (6C,
# the server part) app.js has their calls (API.host.score/day, bench.start) — the list is gone. GET /api/host/curves is
# gone with the absolute score (NS-D34): neither the server nor the panel has it (test_no_curves_any_more).
# Stage C, C1: the tariff API whose screen is C8 (by the mock of Claude Design) — waits without a caller.
# Круг 1, Т2, №41: DELETE /api/gw/activity ждал здесь кнопки «Удалить историю активности» — с API.activity.wipe в
# app.js у него есть вызов, из списка он ушёл.
UNTIL_C8 = ("host_tariff_get", "host_tariff_post")
# PLAN-multi-ip Ш3/Ш4: «Приём подключений» и «IP выхода» — API есть (одно ядро gw/mip.py с CLI, MCP и SSH-меню), экраны —
# шаг Ш5 через Claude Design; до них маршруты ждут здесь без вызова. 3.3.7.15 F10/F11: страница «Адреса сервера»
# (GET /api/gw/addresses) и «Приём по IPv6» (PUT …/in6) — тоже только API, экран строится в этом выпуске; F13 —
# «IPv6 строкой подписки» (PUT …/give) и F12а — «напрямую», «для выхода», «Сбросить выбор» (…/direct, …/out, …/reset) —
# так же.
UNTIL_MIP5 = ()      # 3.3.7.15 V2: страница «Адреса сервера» зовёт все маршруты адресов (API.addresses), и F12б — «Разнести
                     # выходы» (…/spread)
# 3.3.7.15 F1/F3 (SPEC-R1: сначала код, панель — только API): «Ждут новых настроек» и окно «Настройки протокола» ждали
# здесь экранов; с видом №13 r2 (шаг V1) у них есть вызовы — список пуст.
UNTIL_R15 = ()
# 3.3.7.15, полоса Г (SPEC-R1 F14, F7): на шаге кода в панели только API, экраны — после Claude Design (§6а);
# до натяжки вида маршруты ждут здесь без вызова (убирать по одному вместе с кнопкой). V1: ожидание F7, «Скачать мои
# новые настройки» и «Сеть шлюза» F6 — с кнопками; V2: «Разложить заново» F14 — с кнопкой; список пуст
UNTIL_R15G = ()
# PUT …/inbounds/<kind>/port — псевдоним «Настроек протокола» без ver только для старого клиента (SPEC-R1 F3): панель
# 3.3.7.15 зовёт PUT …/settings; маршрут остаётся (CLI, MCP inbound_port и вкладка старой версии после обновления)
OLD_CLIENT = ("gw_inbound_port",)
# T15 Q6: PUT /api/rules/ru-via ждал своих экранов (UNTIL_Q6) — с «Правила › Российские сайты» и окном «Выбрать
# пользователей…» (API.rules.ruVia) у него есть вызов, списка больше нет.
# the browser test (SPEC-browser-channel BC1/BC5): the worker speed-worker.js calls these itself (not through request() of
# app.js) — each pattern must be in the worker's source exactly so, with this method; budget is read through app.js
WORKER_CALLS = ((r'api\("start"', "POST", "/api/host/speed/start"), (r'url\("ping"', "GET", "/api/host/speed/ping"),
                (r'url\("down"', "GET", "/api/host/speed/down"), (r'open\("POST", url\("up"', "POST", "/api/host/speed/up"),
                (r'api\("finish"', "POST", "/api/host/speed/finish"))
# UI-B 23.10: активность (SPEC-activity §5) — панель читала её раньше сервера (список UNTIL_ACTIVITY); с 3.3.7.9
# маршруты на сервере есть (AC4) — списка больше нет, вызовы сверяются как все (test_activity_calls).
ACTIVITY_CALLS = (("GET", "/api/gw/activity"), ("GET", "/api/gw/devices/d-1a2b3c4d/activity"),
                  ("GET", "/api/gw/users/u-1a2b3c4d/activity"))


class PanelContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for name in panel.MODULES:
            importlib.import_module(name)

    def calls(self):
        block = api_block(read("app.js"))
        found = [(m.group(1), build_path(m.group(2))) for m in CALL.finditer(block)]
        self.assertEqual(len(found), block.count("request("), "вызов API, который этот тест не разобрал")
        for m in re.finditer(r"call\('(GET|POST|PUT|DELETE)', '(/[^']*)'", read("graph.js")):
            found.append((m.group(1), "/api" + m.group(2)))
        worker = read("speed-worker.js")
        self.assertIn('return base + "api/host/speed/" + route', worker)              # url(): маршруты замера
        self.assertIn('call(base + "api/host/speed/" + route, { method: "POST"', worker)  # api(): POST
        for rx, method, path in WORKER_CALLS:
            self.assertRegex(worker, rx, path)
            found.append((method, path))
        return found

    def test_every_call_has_a_route(self):
        calls = self.calls()
        self.assertGreater(len(calls), 35)
        for method, path in calls:
            r, _ = routes.find(method, path)
            self.assertIsInstance(r, routes.Route, "%s %s: на сервере нет такого маршрута" % (method, path))

    def test_every_admin_route_has_a_caller(self):
        """What the server can do, the panel can do (the critic's C5: pause, the person's page, the panel's access
        had routes and no buttons). GET /api/events is opened by EventSource, not by request()."""
        calls = self.calls()
        self.assertIn('new ES(apiUrl("/api/events"))', read("app.js"))          # under the panel's prefix
        missing = ["%s %s" % (r.method, r.pattern) for r in routes.ROUTES
                   if r.auth == "admin" and (r.method, r.pattern) != ("GET", r"/api/events")
                   and r.handler.__name__ not in UNTIL_T8 + UNTIL_C8 + UNTIL_MIP5 + UNTIL_R15 + UNTIL_R15G + OLD_CLIENT
                   and not any(m == r.method and r.rx.match(p) for m, p in calls)]
        self.assertEqual(missing, [], "маршруты без кнопки в панели")

    def test_waiting_routes_have_no_caller_yet(self):
        """A name of UNTIL_C8 is an admin route of the server that the panel does not call yet: a stale or mistyped
        name would let some other route go without a button unnoticed."""
        calls = self.calls()
        by_name = {r.handler.__name__: r for r in routes.ROUTES}
        for name in UNTIL_C8 + UNTIL_MIP5 + UNTIL_R15 + UNTIL_R15G + OLD_CLIENT:
            r = by_name.get(name)
            self.assertIsNotNone(r, "%s: такого маршрута нет — уберите из UNTIL_C8" % name)
            self.assertEqual(r.auth, "admin", name)
            self.assertFalse(any(m == r.method and r.rx.match(p) for m, p in calls),
                             "%s: у маршрута уже есть вызов в панели — уберите из UNTIL_C8" % name)

    def test_no_curves_any_more(self):
        """NS-D34: the scale 0–1000 is gone — no route GET /api/host/curves on the server and no call of it in app.js;
        «Сейчас» reads only /api/host/score and /api/host/day/<d>."""
        calls = self.calls()
        self.assertFalse(any(p.startswith("/api/host/curves") for _m, p in calls))
        self.assertFalse(any(r.pattern.startswith(r"/api/host/curves") for r in routes.ROUTES))
        self.assertIn(("GET", "/api/host/score"), calls)
        self.assertIn(("GET", "/api/host/day/2026-10-19"), calls)

    def test_activity_calls(self):
        """3.3.7.9 (AC4): вызовы активности панели — маршруты сервера, только администратор; ресурс по-прежнему
        необязательный (сервер старше 3.3.7.9 — 404 и has.activity = false, линии «нет данных»)."""
        calls = self.calls()
        for method, path in ACTIVITY_CALLS:
            self.assertIn((method, path), calls)
            r, _ = routes.find(method, path)
            self.assertIsInstance(r, routes.Route, "%s %s" % (method, path))
            self.assertEqual(r.auth, "admin", path)
        self.assertIn('defRes("activity", API.activity.sum, normActivity, { optional: true, need: "activity" });', read("app.js"))

    def test_rules_calls(self):
        calls = set(self.calls())
        for need in (("PUT", "/api/rules/profile"), ("PUT", "/api/rules/blocks"), ("POST", "/api/rules/update")):
            self.assertIn(need, calls)
        self.assertNotIn(("PUT", "/api/gw/devices/d-1a2b3c4d/rules"), calls)

    def test_changes_that_apply_the_gateway_wait_longer(self):
        """C16: how long app.js waits is decided by the ROUTE (TIMEOUT_BY_ROUTE), and every call whose handler applies
        the whole gateway on the server (ops._change → _apply, apply_all, rules apply_system) waits at least
        APPLY_TIMEOUT_MS; the rest — API_TIMEOUT_MS; nothing waits for ever."""
        js = read("app.js")
        consts = {}
        for name, expr in re.findall(r"const (\w+_TIMEOUT_MS) = ([0-9 *]+);", js):
            value = 1
            for f in expr.split("*"):
                value *= int(f)
            consts[name] = value
        for name in ("API_TIMEOUT_MS", "APPLY_TIMEOUT_MS", "LONG_TIMEOUT_MS"):
            self.assertIn(name, consts)
        a = js.index("const TIMEOUT_BY_ROUTE = [")
        table = [(re.compile(rx), name) for rx, name in re.findall(r"\[/(.+?)/, (\w+)\]", js[a:js.index("];", a)])]
        self.assertGreaterEqual(len(table), 4)

        def timeout(method, path):
            key = method + " " + path.split("?")[0]
            return next((consts[n] for rx, n in table if rx.search(key)), consts["API_TIMEOUT_MS"])
        applied = []
        for method, path in self.calls():
            r, _ = routes.find(method, path)
            t = timeout(method, path)
            self.assertTrue(0 < t <= consts["LONG_TIMEOUT_MS"], path)
            if method == "GET" and path in LONG_GETS:
                self.assertEqual(t, timeout(*LONG_GETS[path]), path)
            elif method == "GET":
                self.assertEqual(t, consts["API_TIMEOUT_MS"], path)
            elif applies(r.handler):
                applied.append(path)
                self.assertGreaterEqual(t, consts["APPLY_TIMEOUT_MS"], "%s %s применяет шлюз — ждать дольше" % (method, path))
        # REV-3377-stand-fix 2: the price of «Проверить сервер» — a read the server counts up to ~200 s (systemctl 20,
        # apt-get -s 120, apt-cache 60): it waits as the POST without a key it replaced
        self.assertIn(("GET", "/api/host/bench/price"), self.calls())
        self.assertGreaterEqual(timeout("GET", "/api/host/bench/price"), (20 + 120 + 60) * 1000)
        for need in ("/api/gw/devices/d-1a2b3c4d/access", "/api/gw/exits/e-41a19b9cbe", "/api/rules/profile",
                     "/api/scheme/apply", "/api/gw/route", "/api/gw/inbounds/wg/install", "/api/gw/users"):
            self.assertIn(need, applied)
        # 3.2.21: a key of an exit — the first AmneziaWG key puts the kernel headers in and builds the module (minutes)
        calls = self.calls()
        for method, path in (("POST", "/api/gw/exits"), ("DELETE", "/api/gw/exits/e-41a19b9cbe")):
            self.assertIn((method, path), calls)
            self.assertEqual(timeout(method, path), consts["LONG_TIMEOUT_MS"], "%s %s" % (method, path))
        self.assertEqual(timeout("DELETE", "/api/gw/exits/e-41a19b9cbe-3"), consts["LONG_TIMEOUT_MS"])   # a repeated key
        self.assertEqual(timeout("PUT", "/api/gw/exits/e-41a19b9cbe"), consts["APPLY_TIMEOUT_MS"])      # a name: as before
        for method, path in (("POST", "/api/gw/devices/d-1a2b3c4d/issue"), ("PUT", "/api/scheme/layout"),
                             ("POST", "/api/forwards"), ("PUT", "/api/appearance"), ("POST", "/api/login")):
            self.assertNotIn(path, applied)
            self.assertEqual(timeout(method, path), consts["API_TIMEOUT_MS"], path)

    def test_site_calls_wait_as_long_as_the_node_works(self):
        """4.0-B: the issue of the certificate waits for apt (its lock up to 10 minutes) and certbot — 20 minutes; the
        DNS check with waiting — 300 s of polling and one more check; what moves SSTP/OpenConnect applies the gateway."""
        js = read("app.js")
        a = js.index("const TIMEOUT_BY_ROUTE = [")
        table = [(re.compile(rx), name) for rx, name in re.findall(r"\[/(.+?)/, (\w+)\]", js[a:js.index("];", a)])]

        def name_of(method, path):
            key = method + " " + path
            return next((n for rx, n in table if rx.search(key)), "API_TIMEOUT_MS")
        self.assertIn("const CERT_TIMEOUT_MS = 20 * 60 * 1000;", js)
        self.assertIn("const DNS_TIMEOUT_MS = 330 * 1000;", js)
        self.assertEqual(name_of("POST", "/api/site/cert"), "CERT_TIMEOUT_MS")
        self.assertEqual(name_of("POST", "/api/site/cert/renew"), "CERT_TIMEOUT_MS")
        self.assertEqual(name_of("POST", "/api/site/dns"), "DNS_TIMEOUT_MS")
        self.assertEqual(name_of("PUT", "/api/site/use"), "APPLY_TIMEOUT_MS")
        self.assertEqual(name_of("DELETE", "/api/site/domain"), "APPLY_TIMEOUT_MS")
        self.assertEqual(name_of("PUT", "/api/site/email"), "SITE_TIMEOUT_MS")
        self.assertEqual(name_of("GET", "/api/site"), "API_TIMEOUT_MS")
        calls = set(self.calls())
        for need in (("GET", "/api/site"), ("PUT", "/api/site/domain"), ("DELETE", "/api/site/domain"), ("POST", "/api/site/dns"),
                     ("PUT", "/api/site/email"), ("POST", "/api/site/cert"), ("POST", "/api/site/cert/renew"), ("PUT", "/api/site/use")):
            self.assertIn(need, calls)

    def test_cover_site_calls_and_their_waits(self):
        """4.0-C (SPEC-wave4 §7.1): the panel calls every route of the cover site; the mode and the template wait
        20 minutes (apt with its lock, nginx, git clones of up to three templates), the check and the rotation of the
        secret path — 60 s; the catalog is a GET of the usual table (its first download waits on the call itself)."""
        js = read("app.js")
        a = js.index("const TIMEOUT_BY_ROUTE = [")
        table = [(re.compile(rx), name) for rx, name in re.findall(r"\[/(.+?)/, (\w+)\]", js[a:js.index("];", a)])]

        def name_of(method, path):
            key = method + " " + path
            return next((n for rx, n in table if rx.search(key)), "API_TIMEOUT_MS")
        self.assertIn("const SITE_DEPLOY_TIMEOUT_MS = 20 * 60 * 1000;", js)
        self.assertIn("const SITE_CHECK_TIMEOUT_MS = 60 * 1000;", js)
        self.assertEqual(name_of("PUT", "/api/site/mode"), "SITE_DEPLOY_TIMEOUT_MS")
        self.assertEqual(name_of("POST", "/api/site/template"), "SITE_DEPLOY_TIMEOUT_MS")
        self.assertEqual(name_of("POST", "/api/site/check"), "SITE_CHECK_TIMEOUT_MS")
        self.assertEqual(name_of("POST", "/api/site/panel-path/rotate"), "SITE_CHECK_TIMEOUT_MS")
        # 3.2.21: «жив ли шаблон» — пакет видимой страницы, сервер укладывается в 40 с: ждём минуту
        self.assertEqual(name_of("POST", "/api/site/templates/check"), "SITE_CHECK_TIMEOUT_MS")
        for path in ("/api/site/templates", "/api/site/categories", "/api/site/panel-url"):
            self.assertEqual(name_of("GET", path), "API_TIMEOUT_MS", path)
        self.assertIn('request("GET", "/api/site/categories", undefined, { timeout: SITE_TIMEOUT_MS })', js)
        calls = set(self.calls())
        for need in (("PUT", "/api/site/mode"), ("GET", "/api/site/categories"), ("GET", "/api/site/templates"),
                     ("POST", "/api/site/template"), ("GET", "/api/site/panel-url"),
                     ("POST", "/api/site/panel-path/rotate"), ("POST", "/api/site/check"),
                     ("POST", "/api/site/templates/check")):
            self.assertIn(need, calls)

    def test_subscriptions_and_the_copy_wait_as_long_as_the_node_works(self):
        """Подписки выходов: скачать и поменять ключи пула — первый ключ AmneziaWG собирает модуль ядра (минуты), как
        у ключа выхода; копия настроек: перенос и «Вернуть как было» ставят протоколы — тоже минуты; проверка и
        скачивание копии (PBKDF2, openssl, сбор файлов) — дольше обычного; сводка — обычный GET. Каждый маршрут
        подписок и копии панель вызывает."""
        js = read("app.js")
        a = js.index("const TIMEOUT_BY_ROUTE = [")
        table = [(re.compile(rx), name) for rx, name in re.findall(r"\[/(.+?)/, (\w+)\]", js[a:js.index("];", a)])]

        def name_of(method, path):
            key = method + " " + path.split("?")[0]
            return next((n for rx, n in table if rx.search(key)), "API_TIMEOUT_MS")
        for method, path in (("POST", "/api/gw/exit-subs"), ("POST", "/api/gw/exit-subs/refresh"),
                             ("POST", "/api/gw/exit-subs/s-1a2b3c/refresh"), ("DELETE", "/api/gw/exit-subs/s-1a2b3c"),
                             ("DELETE", "/api/gw/exit-subs/s-1a2b3c?keep_keys=1"), ("POST", "/api/gw/exit-subs/s-1a2b3c/restore"),
                             ("POST", "/api/backup/import"), ("POST", "/api/backup/undo")):
            self.assertEqual(name_of(method, path), "LONG_TIMEOUT_MS", "%s %s" % (method, path))
        self.assertEqual(name_of("PUT", "/api/gw/exit-subs/s-1a2b3c"), "APPLY_TIMEOUT_MS")
        self.assertEqual(name_of("POST", "/api/backup/export"), "APPLY_TIMEOUT_MS")
        self.assertEqual(name_of("POST", "/api/backup/preview"), "APPLY_TIMEOUT_MS")
        self.assertEqual(name_of("GET", "/api/backup/summary"), "API_TIMEOUT_MS")
        self.assertEqual(name_of("POST", "/api/gw/exits"), "LONG_TIMEOUT_MS")             # ключ выхода — как был
        calls = set(self.calls())
        for need in (("POST", "/api/gw/exit-subs"), ("POST", "/api/gw/exit-subs/refresh"),
                     ("POST", "/api/gw/exit-subs/s-1a2b3c/refresh"), ("PUT", "/api/gw/exit-subs/s-1a2b3c"),
                     ("POST", "/api/gw/exit-subs/s-1a2b3c/restore"),
                     ("DELETE", "/api/gw/exit-subs/s-1a2b3c"), ("GET", "/api/backup/summary"),
                     ("POST", "/api/backup/export"), ("POST", "/api/backup/preview"), ("POST", "/api/backup/import"),
                     ("POST", "/api/backup/undo")):
            self.assertIn(need, calls)
        # «оставить ключи» — строкой запроса, как у сервера (api.gw_exit_sub_del: keep_keys=1)
        self.assertIn('"/api/gw/exit-subs/" + enc(id) + qs({ keep_keys: keep ? 1 : null })', js)


_MARKERS = ("_change(", "_apply(", "apply_system(", "apply_all(")


def _marker(fn, src):
    """A call of the gateway's apply in ``src``: ``ops._apply(…)`` of a module, or a bare ``_apply(…)`` that is the
    gateway's, the rules' or the panel's own (4.0-C: kaskad.site.nginx has an ``_apply`` of its own — the transaction
    of its files, not the gateway)."""
    g = getattr(fn, "__globals__", {})
    for m in _MARKERS:
        name = m[:-1]
        for hit in re.finditer(r"(\.)?(?<!\w)%s\(" % re.escape(name), src):
            if hit.group(1):
                return True
            target = g.get(name)
            mod = getattr(target, "__module__", "") or ""
            if not inspect.isfunction(target) or mod.startswith(("kaskad.gw", "kaskad.rules", "kaskad.panel")):
                return True
    return False


def applies(fn, depth=3, seen=None):
    """Does ``fn`` (a route handler) bring the whole gateway to the state (gw.ops._change / _apply, rules.ops
    apply_system) — directly or through the core functions it names (``ops.device_grant`` passed to ``_call``)?"""
    seen = set() if seen is None else seen
    if fn in seen:
        return False
    seen.add(fn)
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return False
    if _marker(fn, src):
        return True
    if depth == 0:
        return False
    g = getattr(fn, "__globals__", {})
    names = [getattr(g.get(mod), attr, None) for mod, attr in re.findall(r"\b([a-z_]\w*)\.([a-z_]\w*)\b", src)
             if inspect.ismodule(g.get(mod))]
    names += [g.get(n) for n in re.findall(r"(?<![.\w])([a-z_]\w*)\(", src)]
    return any(inspect.isfunction(f) and (f.__module__ or "").startswith("kaskad") and applies(f, depth - 1, seen)
               for f in names)


if __name__ == "__main__":
    unittest.main()

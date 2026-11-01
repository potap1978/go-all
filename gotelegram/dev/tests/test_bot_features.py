import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "gotelegram-bot" / "bot.py"
CATALOG_PATH = ROOT / "templates_catalog.json"
INSTALL_PATH = ROOT / "install.sh"


class BotFeatureTests(unittest.TestCase):
    def test_catalog_contains_template_ids_that_break_raw_callback_data(self):
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        raw_lengths = [
            len(f"pro_tpl_{tpl['id']}".encode("utf-8"))
            for cat in catalog.get("categories", [])
            for tpl in cat.get("templates", [])
        ]

        self.assertTrue(any(length > 64 for length in raw_lengths))

    def test_bot_uses_short_template_callback_keys(self):
        source = BOT_PATH.read_text(encoding="utf-8")

        self.assertNotIn("callback_data=f\"pro_tpl_{tpl['id']}\"", source)
        self.assertNotIn('callback_data=f"pro_confirm_{tpl_id}"', source)
        self.assertIn("pro_template_map", source)
        self.assertIn("resolve_pro_template_id", source)

    def test_template_callbacks_are_restart_safe_hashes(self):
        source = BOT_PATH.read_text(encoding="utf-8")
        category_body = re.search(
            r"async def cb_pro_category\(.*?(?=\n\n(?:async )?def |\n\n#)",
            source,
            flags=re.S,
        )
        resolve_body = re.search(
            r"def resolve_pro_template_id\(.*?(?=\n\n(?:async )?def |\n\n#)",
            source,
            flags=re.S,
        )
        self.assertIsNotNone(category_body)
        self.assertIsNotNone(resolve_body)

        self.assertIn('pro_template_key_for_id(context, tpl["id"])', category_body.group(0))
        self.assertNotIn("enumerate(templates)", category_body.group(0))
        self.assertNotIn("mapping.clear()", category_body.group(0))
        self.assertIn("load_json(TEMPLATES_CATALOG)", resolve_body.group(0))
        self.assertIn("hashlib.sha1", resolve_body.group(0))

    def test_telemt_version_checks_systemd_path_fallbacks(self):
        source = BOT_PATH.read_text(encoding="utf-8")
        version_body = re.search(
            r"async def get_telemt_version\(\).*?(?=\n\n(?:async )?def |\n\n#)",
            source,
            flags=re.S,
        )
        self.assertIsNotNone(version_body)
        body = version_body.group(0)

        self.assertIn('"--version"', body)
        self.assertIn('"-V"', body)
        self.assertIn('"/usr/local/bin/telemt"', body)
        self.assertIn("for command in", body)
        self.assertIn("for args in", body)
        self.assertNotIn('"-v"', body)

    def test_telemt_update_menu_reuses_version_helper(self):
        source = BOT_PATH.read_text(encoding="utf-8")
        update_body = re.search(
            r"async def cb_menu_update\(.*?(?=\n\n(?:async )?def |\n\n#)",
            source,
            flags=re.S,
        )
        self.assertIsNotNone(update_body)
        body = update_body.group(0)

        self.assertIn("await get_telemt_version()", body)
        self.assertNotIn('sh("telemt", "--version")', body)

    def test_installer_auto_updates_existing_bot_files(self):
        source = INSTALL_PATH.read_text(encoding="utf-8")
        auto_body = re.search(
            r"auto_update_bot_if_possible\(\).*?(?=\n\n[A-Za-z0-9_]+\(\) |\n\n#)",
            source,
            flags=re.S,
        )
        self.assertIsNotNone(auto_body)

        auto_text = auto_body.group(0)
        self.assertIn('bot_service_status', auto_text)
        self.assertIn('bot_install', auto_text)
        self.assertIn('cmp -s "$SCRIPT_DIR/gotelegram-bot/bot.py" "$BOT_DIR/bot.py"', auto_text)
        self.assertIn('cmp -s "$SCRIPT_DIR/gotelegram-bot/i18n.py" "$BOT_DIR/i18n.py"', auto_text)
        self.assertIn('cmp -s "$SCRIPT_DIR/gotelegram-bot/requirements.txt" "$BOT_DIR/requirements.txt"', auto_text)
        # Проверяем ПОРЯДОК, а не буквальное соседство: между вызовами со временем
        # появился auto_update_telemt_if_possible, и жёсткая склейка строк начала
        # падать на нетронутой ветке — тест краснел, ничего не находя по существу.
        steps = (
            'auto_migrate_legacy_state || true',
            'auto_update_bot_if_possible || true',
            'auto_install_admin_web_if_possible || true',
        )
        for step in steps:
            self.assertIn(step, source, f'нет вызова: {step}')
        positions = [source.index(step) for step in steps]
        self.assertEqual(
            positions, sorted(positions),
            'порядок должен быть: миграция -> обновление бота -> установка веб-панели',
        )




class BotWebLimitsTests(unittest.TestCase):
    def test_limits_inserted_before_vhosts(self):
        source = BOT_PATH.read_text(encoding="utf-8")
        ns = {"re": re, "List": list}
        exec("from typing import List\n" + source[source.index("WEB_SECRET_MODE = "):source.index("def save_telemt_users(")], ns)
        lines = ["[web]", 'carrier = "https"', "[[web.vhosts]]", 'host = "x"']
        out = ns["_ensure_web_limits"](lines)
        self.assertIn("[web.limits]", out)
        self.assertLess(out.index("[web.limits]"), out.index("[[web.vhosts]]"))
        self.assertEqual(ns["_ensure_web_limits"](out), out)
        body = re.search(r"def save_telemt_users\(.*?(?=\n\n(?:async )?def )", source, flags=re.S)
        self.assertIn("_ensure_web_limits(out)", body.group(0))

class BotWebProxyTests(unittest.TestCase):
    """Помощники профилей WEB в боте выполняются как есть, из исходника."""

    def _helpers(self):
        source = BOT_PATH.read_text(encoding="utf-8")
        start = source.index("WEB_SECRET_MODE = ")
        end = source.index("def save_telemt_users(")
        ns = {"re": re, "List": list}
        exec("from typing import List\n" + source[start:end], ns)
        return ns

    def test_web_profiles_follow_users(self):
        ns = self._helpers()
        lines = ["[access.users]", 'main = "aa"', "", "[[web.vhosts]]", 'host = "x"',
                 "", "[[web.vhosts.profiles]]", 'user = "gone"', 'secret_mode = "dd"', ""]
        stripped = ns["_strip_web_profiles"](lines)
        self.assertNotIn('user = "gone"', stripped)
        self.assertIn("[[web.vhosts]]", stripped)
        rendered = ['main = "aa"', '"Dima" = "bb"']
        profiles = ns["_web_profile_lines"](rendered)
        self.assertEqual([l for l in profiles if l.startswith("user = ")], ['user = "main"', 'user = "Dima"'])

    def test_save_telemt_users_syncs_profiles(self):
        source = BOT_PATH.read_text(encoding="utf-8")
        body = re.search(r"def save_telemt_users\(.*?(?=\n\n(?:async )?def )", source, flags=re.S)
        self.assertIsNotNone(body)
        self.assertIn("_strip_web_profiles(out)", body.group(0))
        self.assertIn("_web_profile_lines(rendered)", body.group(0))

class BotWebHostTests(unittest.TestCase):
    """Меню WEB: отдельное имя, legacy-предупреждение, блокеры своими словами.

    Блок от «WEB-прокси» до cb_web_on исполняется как есть; install.sh и
    Telegram подменены, _t возвращает ключ словаря.
    """

    def _ns(self, status=None, action_result=None, domain="example.com"):
        import asyncio
        import html
        import types
        from typing import Dict, List, Optional

        source = BOT_PATH.read_text(encoding="utf-8")
        chunk = source[source.index("# ── WEB-прокси (Telegram Desktop 7.1+) ─"):source.index("async def cb_web_on(")]
        state = {"actions": [], "edits": [], "replies": [], "busy": False}

        class Button:
            def __init__(self, text, callback_data=None):
                self.text, self.callback_data = text, callback_data

        class Markup:
            def __init__(self, rows):
                self.rows = rows

        async def run_bot_action(action, timeout=300, **params):
            state["actions"].append((action, timeout, params))
            if action == "web-status":
                return dict(status or {"status": "ok", "enabled": False})
            return dict(action_result or {"status": "ok", "message": "ok"})

        async def safe_edit_message(query, text, reply_markup=None, parse_mode=None):
            state["edits"].append((text, [b.callback_data for row in reply_markup.rows for b in row]))

        async def warp_busy(query):
            return state["busy"]

        ns = {
            "re": re, "html": html, "Any": object, "Dict": Dict, "List": List, "Optional": Optional,
            "Update": object, "ContextTypes": types.SimpleNamespace(DEFAULT_TYPE=object),
            "InlineKeyboardButton": Button, "InlineKeyboardMarkup": Markup,
            "run_bot_action": run_bot_action, "safe_edit_message": safe_edit_message,
            "_warp_busy": warp_busy, "_BOT_ACTION_LOCK": asyncio.Lock(),
            "_t": lambda uid, key, **kw: key, "_uid": lambda update: 1,
            "_back_row": lambda uid, cb: [Button("back", cb)],
            "_arm_text_waiter": lambda ctx: ctx.user_data.__setitem__("_waiter_since", 0),
            "load_json": lambda path: {"domain": domain}, "GOTELEGRAM_CONFIG": "cfg",
        }
        # общие помощники плашки «адрес не в DNS» живут у экрана адресов
        exec(source[source.index("DNS_LIST_MAX = "):source.index("DNS_LIST_MAX = ") + 20].splitlines()[0], ns)
        exec(source[source.index("def _dns_ip_list("):source.index("def _edge_dns_tag(")], ns)
        exec(chunk, ns)   # блок включает и set_web_host_from_text

        async def answer(*a, **kw):
            return None

        async def reply_text(text, reply_markup=None, parse_mode=None):
            state["replies"].append((text, [b.callback_data for row in reply_markup.rows for b in row] if reply_markup else []))

        update = types.SimpleNamespace(
            callback_query=types.SimpleNamespace(answer=answer),
            effective_user=types.SimpleNamespace(id=1),
            message=types.SimpleNamespace(reply_text=reply_text),
        )
        context = types.SimpleNamespace(user_data={})
        return ns, state, update, context

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_host_input_rejects_obvious_garbage(self):
        ns, *_ = self._ns()
        norm = ns["_web_host_normalize"]
        self.assertEqual(norm(" WWW.Example.COM. "), "www.example.com")
        for bad in ("", "www example.com", "example", "a..example.com", "a" * 250 + ".com", "x.com;id"):
            self.assertEqual(norm(bad), "", bad)

    def test_legacy_on_warns_and_offers_host(self):
        ns, state, update, context = self._ns(status={
            "status": "ok", "enabled": True, "legacy": True, "blocker": "need_web_host",
            "carrier": "https", "bridge": "ok", "link": "tg://webproxy?server=example.com&secret=dd00"})
        self._run(ns["_render_web_menu"](update, context))
        text, buttons = state["edits"][-1]
        self.assertIn("web_legacy_warn", text)
        self.assertIn("web_host", buttons)
        self.assertNotIn("web_block_need_web_host", text)   # включён — блокер не показываем

    def test_need_web_host_blocker_explained_and_input_offered(self):
        ns, state, update, context = self._ns(status={
            "status": "ok", "enabled": False, "blocker": "need_web_host", "web_host": ""})
        self._run(ns["_render_web_menu"](update, context))
        text, buttons = state["edits"][-1]
        self.assertIn("web_block_need_web_host", text)
        self.assertNotIn("web_on", buttons)
        self.assertIn("web_host", buttons)

    def test_name_shown_and_no_host_button_without_pro(self):
        ns, state, update, context = self._ns(status={
            "status": "ok", "enabled": False, "blocker": "", "web_host": "www.example.com"})
        self._run(ns["_render_web_menu"](update, context))
        text, buttons = state["edits"][-1]
        self.assertIn("web_host_label: <code>www.example.com</code>", text)
        self.assertIn("web_on", buttons)
        ns, state, update, context = self._ns(status={"status": "ok", "enabled": False, "blocker": "need_pro"})
        self._run(ns["_render_web_menu"](update, context))
        self.assertNotIn("web_host", state["edits"][-1][1])

    def test_no_host_button_without_core_or_address(self):
        for blocker in ("need_core", "need_addr", "need_cert", "need_pro"):
            ns, state, update, context = self._ns(status={"status": "ok", "enabled": False, "blocker": blocker})
            self._run(ns["_render_web_menu"](update, context))
            self.assertNotIn("web_host", state["edits"][-1][1], blocker)

    def test_ip_like_name_rejected(self):
        ns, *_ = self._ns()
        self.assertEqual(ns["_web_host_normalize"]("1.2.3.45"), "")
        self.assertEqual(ns["_web_host_normalize"]("web.3com.io"), "web.3com.io")

    def test_legacy_name_change_warns_about_old_links(self):
        ns, state, update, context = self._ns(status={
            "status": "ok", "enabled": True, "legacy": True, "blocker": "need_web_host"})
        self._run(ns["_render_web_menu"](update, context))
        self._run(ns["cb_web_host"](update, context))
        self.assertIn("web_host_legacy_warn", state["edits"][-1][0])
        ns, state, update, context = self._ns(status={
            "status": "ok", "enabled": True, "legacy": False, "web_host": "www.example.com"})
        self._run(ns["_render_web_menu"](update, context))
        self._run(ns["cb_web_host"](update, context))
        self.assertNotIn("web_host_legacy_warn", state["edits"][-1][0])

    def test_transport_failure_is_translated(self):
        ns, state, update, context = self._ns(action_result={
            "status": "error", "message": "install.sh did not return a JSON result", "code": "transport"})
        context.user_data["awaiting_web_host"] = True
        self._run(ns["set_web_host_from_text"](update, context, "www.example.com"))
        self.assertIn("web_result_unknown", state["replies"][-1][0])
        self.assertNotIn("install.sh", state["replies"][-1][0])

    def test_web_host_timeout_covers_worst_case(self):
        ns, *_ = self._ns()
        self.assertGreaterEqual(ns["_WEB_HOST_TIMEOUT"], 420)

    def test_vhost_host_parser_is_exact(self):
        source = BOT_PATH.read_text(encoding="utf-8")
        ns = {}
        exec(source[source.index("def _web_vhost_host("):source.index("def _main_secret(")], ns)
        parse = ns["_web_vhost_host"]
        core = '[[web.vhosts]]\nhosts_extra = "bad"\nhostname = "bad2"\nhost = "www.example.com" # WEB\n'
        self.assertEqual(parse(core), "www.example.com")
        self.assertEqual(parse('[[web.vhosts]]\nhost = www.example.com # c\n'), "www.example.com")
        self.assertEqual(parse('[server]\nhost = "x"\n'), "")

    def test_web_menu_warns_about_missing_dns(self):
        ns, state, update, context = self._ns(status={
            "status": "ok", "enabled": True, "web_host": "www.example.com", "server_ips": 2,
            "dns_web_missing": ["2.2.2.2"]})
        self._run(ns["_render_web_menu"](update, context))
        self.assertIn("dns_missing_one", state["edits"][-1][0])
        ns, state, update, context = self._ns(status={
            "status": "ok", "enabled": True, "web_host": "www.example.com"})   # старый install.sh
        self._run(ns["_render_web_menu"](update, context))
        self.assertNotIn("dns_missing", state["edits"][-1][0])

    def test_host_must_be_subdomain_before_install_sh(self):
        ns, state, update, context = self._ns()
        context.user_data["awaiting_web_host"] = True
        for bad in ("www.other.com", "example.com", "wwwexample.com"):
            self._run(ns["set_web_host_from_text"](update, context, bad))
            self.assertIn("web_host_not_sub", state["replies"][-1][0], bad)
        self.assertEqual(state["actions"], [])
        self.assertTrue(context.user_data.get("awaiting_web_host"))
        self.assertTrue(ns["_web_host_is_sub"]("www.example.com", "Example.com."))
        self.assertTrue(ns["_web_host_is_sub"]("www.x.com", ""))   # домен не известен — не судим

    def test_installer_not_sub_code_translated(self):
        ns, state, update, context = self._ns(action_result={
            "status": "error", "message": "raw", "code": "web_host_not_sub"})
        context.user_data["awaiting_web_host"] = True
        self._run(ns["set_web_host_from_text"](update, context, "www.example.com"))
        self.assertIn("web_host_not_sub", state["replies"][-1][0])
        self.assertNotIn("raw", state["replies"][-1][0])

    def test_host_success_warns_about_missing_dns(self):
        ns, state, update, context = self._ns(action_result={
            "status": "ok", "message": "set", "web_host": "www.example.com", "dns_missing": "2.2.2.2 3.3.3.3"})
        context.user_data["awaiting_web_host"] = True
        self._run(ns["set_web_host_from_text"](update, context, "www.example.com"))
        self.assertIn("dns_missing_many", state["replies"][-1][0])
        ns, state, update, context = self._ns(action_result={"status": "ok", "message": "set", "dns_missing": ""})
        context.user_data["awaiting_web_host"] = True
        self._run(ns["set_web_host_from_text"](update, context, "www.example.com"))
        self.assertNotIn("dns_missing", state["replies"][-1][0])

    def test_shared443_blocker_uses_bot_text_not_raw_code(self):
        ns, state, update, context = self._ns(status={"status": "ok", "enabled": False, "blocker": "need_no_shared443"})
        self._run(ns["_render_web_menu"](update, context))
        self.assertIn("web_block_need_no_shared443", state["edits"][-1][0])
        ns, state, update, context = self._ns(action_result={
            "status": "error", "message": "raw bash text", "code": "need_no_shared443"})
        self._run(ns["_web_toggle"](update, context, "web-on"))
        self.assertIn("web_block_need_no_shared443", state["edits"][-1][0])
        self.assertNotIn("raw bash text", state["edits"][-1][0])

    def test_enable_without_name_goes_straight_to_input(self):
        ns, state, update, context = self._ns(action_result={
            "status": "error", "message": "… WEB-прокси → 4.", "code": "need_web_host"})
        self._run(ns["_web_toggle"](update, context, "web-on"))
        text, buttons = state["edits"][-1]
        self.assertTrue(context.user_data.get("awaiting_web_host"))
        self.assertIn("web_block_need_web_host", text)
        self.assertIn("web_host_ask", text)
        self.assertEqual(buttons, ["menu_web"])

    def test_carrier_change_warns_about_iphone(self):
        ns, state, update, context = self._ns()
        self._run(ns["_web_toggle"](update, context, "web-carrier", carrier="websocket"))
        self.assertIn("web_carrier_ios", state["edits"][-1][0])
        ns, state, update, context = self._ns()
        self._run(ns["_web_toggle"](update, context, "web-carrier", carrier="https"))
        self.assertNotIn("web_carrier_ios", state["edits"][-1][0])

    def test_host_text_input_runs_web_host_with_long_timeout(self):
        ns, state, update, context = self._ns(action_result={"status": "ok", "message": "set www.example.com"})
        context.user_data["awaiting_web_host"] = True
        self._run(ns["set_web_host_from_text"](update, context, "bad name"))
        self.assertEqual(state["replies"][-1][0], "web_host_badfmt")
        self.assertTrue(context.user_data.get("awaiting_web_host"))   # ждём исправленное имя
        self.assertEqual(state["actions"], [])
        self._run(ns["set_web_host_from_text"](update, context, "WWW.Example.com"))
        action, timeout, params = state["actions"][-1]
        self.assertEqual((action, params), ("web-host", {"host": "www.example.com"}))
        self.assertGreaterEqual(timeout, 150)
        self.assertNotIn("awaiting_web_host", context.user_data)
        self.assertTrue(state["replies"][-1][0].startswith("\u2705"))

    def test_host_failure_offers_retry(self):
        ns, state, update, context = self._ns(action_result={
            "status": "error", "message": "Name www.example.com does not point <here>", "code": "web_host"})
        context.user_data["awaiting_web_host"] = True
        self._run(ns["set_web_host_from_text"](update, context, "www.example.com"))
        text, buttons = state["replies"][-1]
        self.assertIn("&lt;here&gt;", text)          # текст install.sh экранирован под HTML
        self.assertEqual(buttons, ["web_host", "menu_web"])

    def test_host_waiter_is_wired(self):
        source = BOT_PATH.read_text(encoding="utf-8")
        clear = re.search(r"def _clear_text_waiters\(.*?(?=\n\S)", source, flags=re.S).group(0)
        self.assertIn('"awaiting_web_host"', clear)
        self.assertIn('"web_host": cb_web_host,', source)
        handler = re.search(r"async def handle_text_message\(.*?(?=\n\nasync def |\n\ndef )", source, flags=re.S).group(0)
        self.assertIn('context.user_data.get("awaiting_web_host", False)', handler)
        # ожидание ловится раньше ввода имени ключа — иначе имя WEB ушло бы в новый ключ
        self.assertLess(handler.index("awaiting_web_host"), handler.index('get("awaiting_user_name"'))


class BotEdgeDnsTests(unittest.TestCase):
    """Экран адресов: отметки DNS у каждого адреса и плашка о пропущенных."""

    def _render(self, payload, domain="example.com"):
        import asyncio
        import html
        import types
        from typing import Any, Dict, List, Optional

        source = BOT_PATH.read_text(encoding="utf-8")
        chunk = source[source.index("EDGE_MAX_ROWS = 40"):source.index("async def cb_sec_state(")]
        edits = []

        class Button:
            def __init__(self, text, callback_data=None):
                self.text, self.callback_data = text, callback_data

        async def run_bot_action(action, timeout=300, **params):
            return dict(payload)

        async def safe_edit_message(query, text, reply_markup=None, parse_mode=None):
            edits.append(text)

        async def require_auth(update, context):
            return True

        async def answer(*a, **kw):
            return None

        ns = {
            "html": html, "Any": Any, "Dict": Dict, "List": List, "Optional": Optional,
            "Update": object, "ContextTypes": types.SimpleNamespace(DEFAULT_TYPE=object),
            "InlineKeyboardButton": Button, "InlineKeyboardMarkup": lambda rows: rows,
            "run_bot_action": run_bot_action, "safe_edit_message": safe_edit_message,
            "require_auth": require_auth, "_uid": lambda u: 1,
            "_t": lambda uid, key, **kw: {"dns_missing_one": "ONE {ips} @ {name}",
                                          "dns_missing_many": "MANY {ips} @ {name}"}.get(key, key),
            "_hum_bytes": lambda v, uid: "0", "_back_row": lambda uid, cb: [Button("back", cb)],
            "load_json": lambda path: {"domain": domain}, "GOTELEGRAM_CONFIG": "cfg",
        }
        exec(chunk, ns)
        update = types.SimpleNamespace(callback_query=types.SimpleNamespace(answer=answer))
        asyncio.run(ns["cb_edgeips"](update, None))
        return edits[-1], ns

    def _ips(self, **extra):
        base = [{"ip": "1.1.1.1", "reach": "ok", "conns": 3}, {"ip": "2.2.2.2", "reach": "ok", "conns": 0}]
        for e in base:
            e.update(extra)
        return base

    def test_marks_and_plaques_when_dns_checked(self):
        ips = self._ips(dns_domain=True, dns_web=True)
        ips[1].update(dns_domain=False, dns_web=False)
        text, _ = self._render({"ips": ips, "web_host": "www.example.com", "dns": {
            "checked": True, "domain_missing": ["2.2.2.2"], "web_missing": ["2.2.2.2"]}})
        self.assertIn("DNS ✓ WEB ✓", text)
        self.assertIn("DNS ✗ WEB ✗", text)
        self.assertIn("ONE 2.2.2.2 @ example.com", text)
        self.assertIn("ONE 2.2.2.2 @ www.example.com", text)
        self.assertIn("edge_dns_legend", text)

    def test_no_web_mark_without_web_name(self):
        text, _ = self._render({"ips": self._ips(dns_domain=True, dns_web=None),
                                "dns": {"checked": True, "domain_missing": [], "web_missing": []}})
        self.assertIn("DNS ✓", text)
        self.assertNotIn("WEB ✓", text)
        self.assertNotIn("WEB ✗", text)
        self.assertNotIn("@", text)

    def test_unchecked_or_old_installer_claims_nothing(self):
        for payload in ({"ips": self._ips(dns_domain=False, dns_web=False),
                         "dns": {"checked": False, "domain_missing": ["1.1.1.1"]}},
                        {"ips": self._ips()}):
            text, _ = self._render(payload)
            self.assertNotIn("DNS ✗", text)
            self.assertNotIn("@", text)
            self.assertNotIn("edge_dns_legend", text)

    def test_many_missing_listed_compactly(self):
        _, ns = self._render({"ips": self._ips()})
        many = ns["_dns_missing_text"](1, "example.com", [f"10.0.0.{i}" for i in range(12)])
        self.assertTrue(many.startswith("MANY 10.0.0.0, "))
        self.assertIn("(+4)", many)
        self.assertEqual(ns["_dns_ip_list"]("1.1.1.1  2.2.2.2"), ["1.1.1.1", "2.2.2.2"])
        self.assertEqual(ns["_dns_ip_list"](""), [])
        self.assertEqual(ns["_dns_ip_list"](None), [])


class BotTelemtHotReloadTests(unittest.TestCase):
    """Правка ключей применяется перезагрузкой конфига (SIGHUP + API ядра), рестарт — запасной путь.

    Блок исполняется как есть, из исходника; ядро и systemd подменены.
    """

    def _ns(self, version="3.4.18", status="ready", apply_on_hup=True, last_offset=-50):
        import asyncio
        import os
        import signal
        import tempfile
        import time
        import types
        from typing import Any, Dict, List, Optional, Tuple
        from urllib.parse import quote

        source = BOT_PATH.read_text(encoding="utf-8")
        start = source.index("TELEMT_HOT_REVOKE_MIN_VERSION = ")
        end = source.index("async def telemt_api_get(")
        tmp = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
        tmp.write("[access.users]\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        now = int(time.time())
        state = {
            "version": version, "status": status, "started": now - 100, "count": 5,
            "last": now + last_offset, "ready_at": now - 10, "in_runtime": False,
            "kills": [], "sh": [], "restarts": 0,
        }

        async def fake_sh(*args, timeout=60):
            state["sh"].append(args)
            if args[:2] == ("systemctl", "is-active"):
                return 0, "active\n", ""
            if args[:2] == ("systemctl", "show"):
                return 0, "4242\n", ""
            return 0, "", ""

        async def fake_api(path):
            if path == "/v1/system/info":
                return {"ok": True, "data": {
                    "version": state["version"], "process_started_at_epoch_secs": state["started"],
                    "config_reload_count": state["count"], "last_config_reload_epoch_secs": state["last"]}}
            if path == "/v1/runtime/initialization":
                return {"ok": True, "data": {"status": state["status"], "ready_at_epoch_secs": state["ready_at"]}}
            if path.startswith("/v1/users/"):
                return {"ok": True, "data": {"username": path.rsplit("/", 1)[1], "in_runtime": state["in_runtime"]}}
            return None

        def fake_kill(pid, sig):
            state["kills"].append((pid, sig))
            if apply_on_hup:
                state["count"] += 1
                state["last"] = int(time.time())
                state["in_runtime"] = True

        async def fake_restart():
            state["restarts"] += 1
            return True

        ns = {
            "asyncio": asyncio, "re": re, "signal": signal, "time": time, "quote": quote,
            "os": types.SimpleNamespace(getenv=os.getenv, stat=os.stat, kill=fake_kill),
            "Any": Any, "Dict": Dict, "List": List, "Optional": Optional, "Tuple": Tuple,
            "sh": fake_sh, "telemt_api_get": fake_api, "spawn_task": asyncio.ensure_future,
            "_telemt_request_restart": fake_restart, "TELEMT_SERVICE": "telemt",
            "TELEMT_CONFIG": tmp.name, "_TELEMT_RESTART_REQUIRED": False,
            "logger": types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        }
        exec(source[start:end], ns)
        ns["TELEMT_RELOAD_TIMEOUT"] = 1.5
        ns["TELEMT_RELOAD_POLL"] = 0.02
        ns["TELEMT_RELOAD_SETTLE"] = 0.05
        return ns, state

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_new_key_applied_by_sighup_without_restart(self):
        import signal
        ns, state = self._ns()
        self.assertTrue(self._run(ns["telemt_hot_reload"](False, ["bob"])))
        self.assertEqual(state["kills"], [(4242, signal.SIGHUP)])
        self.assertFalse(any("restart" in a for a in state["sh"]))

    def test_revoke_on_old_core_needs_restart(self):
        # 3.4.18/3.5.5: перезагрузка не рвёт живые сессии отозванного ключа
        ns, state = self._ns(version="3.4.18")
        self.assertFalse(self._run(ns["telemt_hot_reload"](True, [])))
        self.assertEqual(state["kills"], [])

    def test_revoke_on_new_core_is_hot(self):
        ns, state = self._ns(version="3.5.14")
        self.assertTrue(self._run(ns["telemt_hot_reload"](True, [])))
        self.assertEqual(len(state["kills"]), 1)

    def test_unconfirmed_reload_falls_back(self):
        ns, state = self._ns(apply_on_hup=False)
        self.assertFalse(self._run(ns["telemt_hot_reload"](False, ["bob"])))
        self.assertEqual(len(state["kills"]), 1)

    def test_no_sighup_while_core_is_starting(self):
        # SIGHUP до регистрации обработчика убил бы процесс
        ns, state = self._ns(status="initializing")
        self.assertFalse(self._run(ns["telemt_hot_reload"](False, [])))
        self.assertEqual(state["kills"], [])

    def test_watcher_already_applied_is_confirmed(self):
        # inotify-watcher опубликовал снимок раньше нас: счётчик после SIGHUP не растёт
        ns, state = self._ns(apply_on_hup=False, last_offset=1)
        state["in_runtime"] = True
        self.assertTrue(self._run(ns["telemt_hot_reload"](False, ["bob"])))

    def test_restart_only_fields_force_restart(self):
        ns, state = self._ns()
        ns["_TELEMT_RESTART_REQUIRED"] = True
        self.assertFalse(self._run(ns["telemt_hot_reload"](False, [])))
        self.assertEqual(state["kills"], [])

    def test_strict_mark_ignores_same_second_reload(self):
        # снимок до записи: подтверждает только рост счётчика, не секундная метка
        ns, _ = self._ns()
        applied = ns["_telemt_config_applied"]
        before = {"process_started_at_epoch_secs": 100, "config_reload_count": 3}
        same = {"process_started_at_epoch_secs": 100, "config_reload_count": 3, "last_config_reload_epoch_secs": 200}
        self.assertFalse(applied(before, same, 200.7, True, True))
        self.assertTrue(applied(before, dict(same, config_reload_count=4), 200.7, True, True))

    def test_mark_before_write_confirms_by_counter(self):
        import time
        ns, state = self._ns(version="3.5.14", apply_on_hup=False, last_offset=1)
        state["in_runtime"] = True
        mark = {"process_started_at_epoch_secs": state["started"], "config_reload_count": state["count"]}
        # счётчик не вырос: секундная метка «после записи» больше не подтверждает
        self.assertFalse(self._run(ns["telemt_hot_reload"](True, [], mark)))
        # watcher опубликовал запись: счётчик вырос относительно снимка — без SIGHUP
        ns, state = self._ns(version="3.5.14", apply_on_hup=False)
        mark = {"process_started_at_epoch_secs": state["started"], "config_reload_count": state["count"]}
        state["count"] += 1
        state["last"] = int(time.time())
        self.assertTrue(self._run(ns["telemt_hot_reload"](True, [], mark)))
        self.assertEqual(state["kills"], [])

    def test_no_sighup_when_core_restarted_during_pid_read(self):
        import time
        for names, expect in (([], True), (["bob"], False)):
            ns, state = self._ns()
            inner = ns["sh"]

            async def sh_restarting(*args, timeout=60, _inner=inner, _state=state):
                if args[:2] == ("systemctl", "show"):
                    _state["started"] = int(time.time()) + 5   # ядро перезапустили в окне
                return await _inner(*args, timeout=timeout)

            ns["sh"] = sh_restarting
            self.assertEqual(self._run(ns["telemt_hot_reload"](False, names)), expect)
            self.assertEqual(state["kills"], [])

    def test_only_not_found_means_key_is_gone(self):
        ns, _ = self._ns()
        payloads = {}

        async def api(path):
            return payloads.get(path)

        ns["telemt_api_get"] = api
        payloads["/v1/users/bob"] = {"ok": False, "error": {"code": "not_found", "message": "User not found"}}
        self.assertTrue(self._run(ns["_telemt_users_in_runtime"](["bob"])))
        for err in ({"code": "unauthorized"}, {"code": "internal_error"}, "boom", None):
            payloads["/v1/users/bob"] = {"ok": False, "error": err}
            self.assertFalse(self._run(ns["_telemt_users_in_runtime"](["bob"])), err)
        payloads["/v1/users/bob"] = None
        self.assertFalse(self._run(ns["_telemt_users_in_runtime"](["bob"])))

    def test_config_applied_signals(self):
        ns, _ = self._ns()
        applied = ns["_telemt_config_applied"]
        before = {"process_started_at_epoch_secs": 100, "config_reload_count": 3}
        self.assertTrue(applied(None, {"process_started_at_epoch_secs": 201}, 200.5, False))
        self.assertFalse(applied(None, {"process_started_at_epoch_secs": 200}, 200.5, False))
        self.assertTrue(applied(before, {"process_started_at_epoch_secs": 100, "config_reload_count": 4}, 200, False))
        # перезапуск процесса обнуляет счётчик — сравнивать счётчики разных процессов нельзя
        self.assertFalse(applied(before, {"process_started_at_epoch_secs": 150, "config_reload_count": 9}, 200, False))
        now = {"process_started_at_epoch_secs": 100, "config_reload_count": 3, "last_config_reload_epoch_secs": 200}
        self.assertFalse(applied(before, now, 200.7, False))
        self.assertTrue(applied(before, now, 200.7, True))
        self.assertFalse(applied(before, None, 200, True))
        self.assertEqual(ns["_telemt_version_tuple"]("3.5.14"), (3, 5, 14))
        self.assertLess(ns["_telemt_version_tuple"]("3.5.5"), ns["TELEMT_HOT_REVOKE_MIN_VERSION"])
        self.assertEqual(ns["_telemt_version_tuple"]("garbage"), ())

    def test_worker_restarts_only_when_reload_fails(self):
        import asyncio

        async def scenario(ns):
            await ns["refresh_telemt_after_user_change"]("add", ["bob"])
            await ns["refresh_telemt_after_user_change"]("limit")
            while ns["_TELEMT_APPLY_RUNNING"]:
                await asyncio.sleep(0.01)

        ns, state = self._ns()
        self._run(scenario(ns))
        self.assertEqual(state["restarts"], 0)
        ns, state = self._ns(apply_on_hup=False)
        self._run(scenario(ns))
        self.assertGreaterEqual(state["restarts"], 1)

    def test_user_change_call_sites_pass_change_kind(self):
        source = BOT_PATH.read_text(encoding="utf-8")
        self.assertIn('refresh_telemt_after_user_change("add", [name], before=mark)', source)
        self.assertIn('refresh_telemt_after_user_change("revoke", before=mark)', source)
        self.assertIn('refresh_telemt_after_user_change("limit", before=mark)', source)
        self.assertNotIn("refresh_telemt_after_user_change()", source)
        # снимок счётчика берётся до записи: в каждом месте вызова mark есть раньше save_*
        self.assertEqual(source.count("mark = await telemt_reload_mark()"), 4)
        body = re.search(r"async def refresh_telemt_after_user_change\(.*?(?=\n\n(?:async )?def )", source, flags=re.S)
        self.assertNotIn('"restart"', body.group(0))
        save = re.search(r"def save_telemt_users\(.*?(?=\n\n(?:async )?def )", source, flags=re.S)
        self.assertIn("_TELEMT_RESTART_REQUIRED = True", save.group(0))


if __name__ == "__main__":
    unittest.main()

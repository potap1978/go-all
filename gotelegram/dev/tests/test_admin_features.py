import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "admin-web" / "server.py"


def load_server(tmpdir: Path):
    os.environ["GOTELEGRAM_BACKUP_DIR"] = str(tmpdir / "backups")
    os.environ["GOTELEGRAM_DIR"] = str(tmpdir / "gotelegram")
    os.environ["TELEMT_CONFIG"] = str(tmpdir / "etc" / "telemt" / "config.toml")
    os.environ["GOTELEGRAM_DISABLED_USERS"] = str(tmpdir / "gotelegram" / "disabled_users.json")
    module_name = "gotelegram_admin_server_test"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AdminFeatureTests(unittest.TestCase):
    def test_backup_path_accepts_only_local_archives(self):
        with tempfile.TemporaryDirectory() as raw:
            tmpdir = Path(raw)
            server = load_server(tmpdir)
            server.BACKUP_DIR.mkdir(parents=True)
            good = server.BACKUP_DIR / "gotelegram_backup_20260425_120000.tar.gz"
            good.write_text("backup", encoding="utf-8")
            encrypted = server.BACKUP_DIR / "gotelegram_backup_20260425_120001.tar.gz.enc"
            encrypted.write_text("backup", encoding="utf-8")
            legacy = server.BACKUP_DIR / "backup_20260425_120002.tar.gz"
            legacy.write_text("backup", encoding="utf-8")

            self.assertEqual(server.safe_backup_path(good.name), good.resolve())
            self.assertEqual(server.safe_backup_path(encrypted.name), encrypted.resolve())
            self.assertEqual(server.safe_backup_path(legacy.name), legacy.resolve())

            with self.assertRaises(ValueError):
                server.safe_backup_path("../outside.tar.gz")
            with self.assertRaises(ValueError):
                server.safe_backup_path("gotelegram_backup_20260425_120000.tar.gz.sha256")
            with self.assertRaises(FileNotFoundError):
                server.safe_backup_path("missing.tar.gz")

    def test_backup_schedule_calendar_rejects_unknown_values(self):
        with tempfile.TemporaryDirectory() as raw:
            server = load_server(Path(raw))
            self.assertEqual(server.backup_schedule_calendar("daily"), "*-*-* 03:20:00")
            self.assertEqual(server.backup_schedule_calendar("weekly"), "Sun 03:20:00")
            self.assertEqual(server.backup_schedule_calendar("monthly"), "*-*-01 03:20:00")
            self.assertIsNone(server.backup_schedule_calendar("off"))

            with self.assertRaises(ValueError):
                server.backup_schedule_calendar("hourly")

    def test_user_records_include_active_disabled_and_ip_limits(self):
        with tempfile.TemporaryDirectory() as raw:
            tmpdir = Path(raw)
            server = load_server(tmpdir)
            server.TELEMT_CONFIG.parent.mkdir(parents=True)
            server.TELEMT_CONFIG.write_text(
                "\n".join([
                    "[access.users]",
                    'main = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
                    'client = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"',
                    "",
                    "[access.user_max_unique_ips]",
                    "main = 0",
                    "client = 2",
                    "disabled = 1",
                    "",
                ]),
                encoding="utf-8",
            )
            server.DISABLED_USERS_FILE.parent.mkdir(parents=True)
            server.DISABLED_USERS_FILE.write_text(
                json.dumps({"users": {"disabled": "cccccccccccccccccccccccccccccccc"}}),
                encoding="utf-8",
            )

            records = server.read_user_records()

            self.assertTrue(records["client"]["enabled"])
            self.assertEqual(records["client"]["max_unique_ips"], 2)
            self.assertFalse(records["disabled"]["enabled"])
            self.assertEqual(records["disabled"]["max_unique_ips"], 1)
            self.assertEqual(records["main"]["max_unique_ips"], 0)

    def test_write_user_max_unique_ips_preserves_other_toml_sections(self):
        with tempfile.TemporaryDirectory() as raw:
            server = load_server(Path(raw))
            server.TELEMT_CONFIG.parent.mkdir(parents=True)
            server.TELEMT_CONFIG.write_text(
                "\n".join([
                    "[server]",
                    "port = 443",
                    "",
                    "[access.users]",
                    'main = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
                    'client = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"',
                    "",
                    "[access.user_max_unique_ips]",
                    "client = 3",
                    "old = 4",
                    "",
                    "[network]",
                    'dns_overrides = ["example.com:8443:127.0.0.1"]',
                    "",
                ]),
                encoding="utf-8",
            )

            server.write_user_max_unique_ips({"main": 1, "client": 0, "new": 5})
            text = server.TELEMT_CONFIG.read_text(encoding="utf-8")

            self.assertIn("[server]\nport = 443", text)
            self.assertIn("[network]\ndns_overrides", text)
            self.assertIn("[access.user_max_unique_ips]", text)
            self.assertIn('"main" = 1', text)
            self.assertIn('"new" = 5', text)
            self.assertNotIn("client = 3", text)
            self.assertNotIn("old = 4", text)

    def test_key_card_traffic_uses_live_ip_counts_not_stale_history(self):
        with tempfile.TemporaryDirectory() as raw:
            server = load_server(Path(raw))
            stale_history = {
                "epoch": 1000,
                "total_octets": 100,
                "current_connections": 16,
                "active_unique_ips": 8,
                "recent_unique_ips": 5,
            }
            original = server.runtime_user_traffic
            server.runtime_user_traffic = lambda name, enabled=True: {
                "ok": True,
                "enabled": True,
                "total_octets": 200,
                "current_connections": 0,
                "active_unique_ips": 0,
                "recent_unique_ips": 0,
            }
            try:
                snapshot = server.current_user_traffic_snapshot("client", True, stale_history, now=2000)
            finally:
                server.runtime_user_traffic = original

            self.assertEqual(snapshot["epoch"], 2000)
            self.assertEqual(snapshot["total_octets"], 200)
            self.assertEqual(snapshot["current_connections"], 0)
            self.assertEqual(snapshot["active_unique_ips"], 0)
            self.assertEqual(snapshot["recent_unique_ips"], 0)

    def test_key_card_traffic_fallback_keeps_only_historical_total(self):
        with tempfile.TemporaryDirectory() as raw:
            server = load_server(Path(raw))
            stale_history = {
                "epoch": 1000,
                "total_octets": 100,
                "current_connections": 16,
                "active_unique_ips": 8,
                "recent_unique_ips": 5,
            }
            original = server.runtime_user_traffic
            server.runtime_user_traffic = lambda name, enabled=True: {"ok": False}
            try:
                snapshot = server.current_user_traffic_snapshot("client", True, stale_history, now=2000)
            finally:
                server.runtime_user_traffic = original

            self.assertEqual(snapshot["epoch"], 1000)
            self.assertEqual(snapshot["total_octets"], 100)
            self.assertEqual(snapshot["current_connections"], 0)
            self.assertEqual(snapshot["active_unique_ips"], 0)
            self.assertEqual(snapshot["recent_unique_ips"], 0)

    def test_keys_view_uses_card_layout_without_horizontal_table(self):
        app_js = (ROOT / "admin-web" / "static" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "admin-web" / "static" / "styles.css").read_text(encoding="utf-8")
        index = (ROOT / "admin-web" / "static" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn('class="actions"', app_js)
        self.assertIn('class="key-card', app_js)
        self.assertIn('class="key-card-actions"', app_js)
        self.assertIn('class="action-buttons"', app_js)
        self.assertIn('class="key-name-button"', app_js)
        self.assertIn(".key-card .mini-actions,\n.key-card .action-buttons", styles)
        self.assertIn("grid-template-columns: 1fr", styles)
        self.assertIn("min-height: 44px", styles)
        self.assertNotIn("td.actions", styles)
        self.assertIn('class="keys-list" id="usersTable"', index)
        self.assertNotIn('class="keys-table"', index)

    def test_topbar_has_five_second_auto_refresh_toggle(self):
        app_js = (ROOT / "admin-web" / "static" / "app.js").read_text(encoding="utf-8")
        styles = (ROOT / "admin-web" / "static" / "styles.css").read_text(encoding="utf-8")
        index = (ROOT / "admin-web" / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="autoRefreshToggle"', index)
        self.assertIn('data-i18n-title="autoRefresh"', index)
        self.assertIn("gotelegram-auto-refresh", app_js)
        self.assertIn("AUTO_REFRESH_MS = 5000", app_js)
        self.assertIn("setInterval", app_js)
        self.assertIn("clearInterval", app_js)
        self.assertIn(".auto-refresh-toggle", styles)

    def test_get_config_value_secret_accepts_quoted_main_user(self):
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "config.toml"
            config.write_text(
                "\n".join([
                    "[access.users]",
                    '"main" = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
                    '"client" = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"',
                    "",
                ]),
                encoding="utf-8",
            )
            script = "\n".join([
                "set -e",
                f"source {shlex.quote(str(ROOT / 'lib' / 'telemt_config.sh'))}",
                f"get_config_value secret {shlex.quote(str(config))}",
            ])

            result = subprocess.run(
                ["bash", "-lc", script],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    def test_telemt_users_block_detects_quoted_main_user(self):
        script = "\n".join([
            "set -e",
            f"source {shlex.quote(str(ROOT / 'lib' / 'telemt_config.sh'))}",
            "block=$'\"main\" = \"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"\\n\"client\" = \"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\"'",
            "telemt_users_block_has_main \"$block\"",
        ])

        result = subprocess.run(
            ["bash", "-lc", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_telemt_restart_requests_are_debounced(self):
        with tempfile.TemporaryDirectory() as raw:
            server = load_server(Path(raw))
            calls = []
            original_run = server.run
            original_status = server.service_status
            try:
                server._LAST_TELEMT_RESTART = 0.0
                server.TELEMT_RESTART_DEBOUNCE_SECONDS = 30.0
                server.service_status = lambda name: "running"

                def fake_run(cmd, timeout=8):
                    calls.append(cmd)
                    return 0, "", ""

                server.run = fake_run
                self.assertTrue(server.request_service_restart("telemt"))
                self.assertTrue(server.request_service_restart("telemt"))
            finally:
                server.run = original_run
                server.service_status = original_status

            restart_calls = [cmd for cmd in calls if cmd[:3] == ["systemctl", "--no-block", "restart"]]
            self.assertEqual(len(restart_calls), 1)


class SiteStatusTests(unittest.TestCase):
    """Проверка сайта идёт на 127.0.0.1:443 с SNI домена, мимо DNS сервера
    (29.10.2026: резолвер хостера терял запросы, панель видела «сайт лежит»)."""

    def test_site_check_goes_local_without_dns(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = load_server(Path(tmp))
            calls = []

            def fake_run(cmd, timeout=8):
                calls.append((cmd, timeout))
                return 0, "200", ""

            server.run = fake_run
            data = server.site_status({"domain": "example.com", "mode": "pro"}, force=True)
            self.assertEqual(len(calls), 1)
            cmd, timeout = calls[0]
            self.assertEqual(cmd[0], "curl")
            i = cmd.index("--resolve")
            self.assertEqual(cmd[i + 1], "example.com:443:127.0.0.1")
            i = cmd.index("--max-time")
            self.assertEqual(cmd[i + 1], "5")
            # при https_proxy в окружении curl ушёл бы в прокси мимо --resolve
            i = cmd.index("--noproxy")
            self.assertEqual(cmd[i + 1], "*")
            self.assertEqual(cmd[-1], "https://example.com/")
            self.assertLessEqual(timeout, 8)
            self.assertTrue(data["ok"])
            self.assertEqual((data["http_code"], data["url"]), (200, "https://example.com/"))

            server.run = lambda cmd, timeout=8: (28, "000", "curl: (28) timed out")
            self.assertFalse(server.site_status({"domain": "example.com", "mode": "pro"}, force=True)["ok"])

    def test_site_check_skips_lite_and_empty_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = load_server(Path(tmp))
            calls = []
            server.run = lambda cmd, timeout=8: calls.append(cmd) or (0, "200", "")
            lite = server.site_status({"domain": "example.com", "mode": "lite"}, force=True)
            empty = server.site_status({"domain": "", "mode": "pro"}, force=True)
            self.assertEqual(calls, [])
            self.assertFalse(lite["checked"])
            self.assertEqual(empty["error"], "domain_missing")




class WebLimitsTests(unittest.TestCase):
    """[web.limits].max_profiles: у ядра по умолчанию 32, профиль — на каждый ключ."""

    def test_limits_inserted_before_vhosts(self):
        with tempfile.TemporaryDirectory() as raw:
            server = load_server(Path(raw))
            lines = ["[web]", 'carrier = "https"', "", "[[web.vhosts]]", 'host = "x"', "[[web.vhosts.profiles]]", 'user = "main"']
            out = server._ensure_web_limits(lines)
            i = out.index("[web.limits]")
            self.assertEqual(out[i + 1], f"max_profiles = {server.WEB_MAX_PROFILES}")
            self.assertLess(i, out.index("[[web.vhosts]]"))
            with_limits = ["[web.limits]", "max_profiles = 4096", "[[web.vhosts]]"]
            self.assertEqual(server._ensure_web_limits(list(with_limits)), with_limits)
            no_web = ["[access.users]", '"main" = "ab"']
            self.assertEqual(server._ensure_web_limits(list(no_web)), no_web)

    def test_write_telemt_users_adds_limits_to_old_config(self):
        with tempfile.TemporaryDirectory() as raw:
            server = load_server(Path(raw))
            server.TELEMT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            body = ['[general]', 'use_middle_proxy = true', '', '[access.users]', 'main = "aa"', '', '[network]', '',
                    '[web]', 'enabled = true', '', '[[web.vhosts]]', 'host = "example.test"', '',
                    '[[web.vhosts.profiles]]', 'user = "main"', 'secret_mode = "dd"']
            server.TELEMT_CONFIG.write_text("\n".join(body) + "\n", encoding="utf-8")
            server.write_telemt_users({"main": "aa", "fresh": "cc"})
            text = server.TELEMT_CONFIG.read_text(encoding="utf-8")
            self.assertIn("[web.limits]", text)
            self.assertLess(text.index("[web.limits]"), text.index("[[web.vhosts]]"))
            self.assertEqual(text.count("[web.limits]"), 1)

class WebProxyTests(unittest.TestCase):
    """WEB-профили пересобираются из [access.users] в той же записи, что и ключи.

    Профиль, ссылающийся на удалённого пользователя, делает конфиг telemt
    невалидным — а панель после правки ключей перезапускает telemt.
    """

    def _cfg(self, server, web: bool) -> None:
        server.TELEMT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        body = ['[general]', 'use_middle_proxy = true', '', '[access.users]', 'main = "aa"', 'old = "bb"', '', '[network]']
        if web:
            body += ['', '[web]', 'enabled = true', '', '[[web.vhosts]]', 'host = "example.test"',
                     '', '[web.vhosts.decoy]', 'mode = "http_upstream"',
                     '', '[[web.vhosts.profiles]]', 'user = "main"', 'secret_mode = "dd"',
                     '', '[[web.vhosts.profiles]]', 'user = "old"', 'secret_mode = "dd"']
        server.TELEMT_CONFIG.write_text("\n".join(body) + "\n", encoding="utf-8")

    def test_write_telemt_users_keeps_web_profiles_in_sync(self):
        with tempfile.TemporaryDirectory() as raw:
            server = load_server(Path(raw))
            self._cfg(server, web=True)
            server.write_telemt_users({"main": "aa", "fresh": "cc"})
            text = server.TELEMT_CONFIG.read_text(encoding="utf-8")
            profiles = re.findall(r'\[\[web\.vhosts\.profiles\]\]\nuser = "([^"]+)"', text)
            self.assertEqual(profiles, ["main", "fresh"])          # удалённый old исчез, новый fresh появился
            self.assertEqual(text.count("[[web.vhosts]]"), 1)      # сама vhost не тронута
            self.assertIn('[network]', text)
            # профили стоят ПОСЛЕ vhost: иначе TOML отнёс бы их не к той таблице
            self.assertLess(text.index("[[web.vhosts]]"), text.index("[[web.vhosts.profiles]]"))

    def test_write_telemt_users_without_web_adds_no_profiles(self):
        with tempfile.TemporaryDirectory() as raw:
            server = load_server(Path(raw))
            self._cfg(server, web=False)
            server.write_telemt_users({"main": "aa"})
            self.assertNotIn("[[web.vhosts.profiles]]", server.TELEMT_CONFIG.read_text(encoding="utf-8"))

    def test_web_link_only_when_enabled_in_pro(self):
        with tempfile.TemporaryDirectory() as raw:
            server = load_server(Path(raw))
            # ссылка требует не только флаг, но и WEB-listener в конфиге ядра
            self._cfg(server, web=True)
            server.TELEMT_CONFIG.write_text(server.TELEMT_CONFIG.read_text(encoding="utf-8")
                                            + '\n[[server.listeners]]\nip = "127.0.0.1"\nport = 18080\ntransport = "web"\n', encoding="utf-8")
            on = {"mode": "pro", "domain": "example.test", "web_enabled": "true"}
            self.assertEqual(server.web_link("ab" * 16, on), "tg://webproxy?server=example.test&secret=dd" + "ab" * 16)
            self.assertEqual(server.web_link("ab" * 16, dict(on, web_enabled="false")), "")
            self.assertEqual(server.web_link("ab" * 16, dict(on, mode="lite")), "")
            self.assertEqual(server.web_link("", on), "")
            # флаг есть, а в конфиге ядра WEB нет (ядро без поддержки) — ссылки быть не должно
            self._cfg(server, web=False)
            self.assertEqual(server.web_link("ab" * 16, on), "")


class WebHostTests(unittest.TestCase):
    """Отдельное имя WEB: панель задаёт его через install.sh --action=web-host."""

    def test_host_input_rejects_obvious_garbage(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = load_server(Path(tmp))
            norm = server.web_host_normalize
            self.assertEqual(norm(" WWW.Example.COM. "), "www.example.com")
            self.assertEqual(norm("web.xn--p1ai"), "web.xn--p1ai")
            for bad in ("", "www example.com", "example", "-a.example.com", "a..example.com",
                        "www.example.com;reboot", "a" * 250 + ".com", "www.пример.рф"):
                self.assertEqual(norm(bad), "", bad)

    def test_web_cli_passes_host_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = load_server(Path(tmp))
            calls = []

            def fake_run(cmd, timeout=8):
                calls.append((cmd, timeout))
                return 0, '{"status":"ok","message":"set","web_host":"www.example.com"}\n', ""

            server.run = fake_run
            data, error = server.web_cli("web-host", timeout=server.WEB_HOST_TIMEOUT, host="www.example.com")
            self.assertEqual((data.get("status"), error), ("ok", ""))
            cmd, timeout = calls[0]
            self.assertIn("--action=web-host", cmd)
            self.assertIn("--host=www.example.com", cmd)
            self.assertFalse(any(c.startswith("--carrier=") for c in cmd))
            # certbot --expand идёт до двух минут — таймаут с запасом
            self.assertGreaterEqual(timeout, 150)

    def test_post_web_routes_host_with_long_timeout(self):
        source = SERVER_PATH.read_text(encoding="utf-8")
        body = re.search(r'if path == "/api/web":\n\s+act = .*?return\n\n', source, flags=re.S)
        self.assertIsNotNone(body)
        text = body.group(0)
        self.assertIn('"host": "web-host"', text)
        self.assertIn("web_host_normalize(", text)
        self.assertIn("WEB_HOST_TIMEOUT if act == \"host\"", text)

    def test_ip_like_name_rejected_and_timeout_covers_worst_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = load_server(Path(tmp))
            self.assertEqual(server.web_host_normalize("1.2.3.45"), "")
            self.assertEqual(server.web_host_normalize("web.3com.io"), "web.3com.io")
            self.assertGreaterEqual(server.WEB_HOST_TIMEOUT, 420)

    def test_vhost_host_is_exact_key_and_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = load_server(Path(tmp))
            server.TELEMT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            server.TELEMT_CONFIG.write_text(
                '[[server.listeners]]\ntransport = "web"\n\n[[web.vhosts]]\nhosts_extra = "bad"\n'
                'hostname = "bad2"\nhost = "www.example.com" # WEB\n', encoding="utf-8")
            self.assertEqual(server.web_vhost_host(), "www.example.com")
            self.assertTrue(server.web_configured_in_core())
            self.assertEqual(server._web_vhost_host_in('[[web.vhosts]]\nhost = www.example.com # c\n'), "www.example.com")
            # список ключей не перечитывает файл на каждый ключ
            reads = []
            orig = type(server.TELEMT_CONFIG).read_text

            def counting(path_self, *a, **kw):
                reads.append(1)
                return orig(path_self, *a, **kw)

            from unittest import mock
            with mock.patch.object(type(server.TELEMT_CONFIG), "read_text", counting):
                cfg = {"mode": "pro", "domain": "example.com", "web_enabled": "true"}
                links = [server.web_link("ab" * 16, cfg) for _ in range(50)]
            self.assertEqual(reads, [])
            self.assertTrue(all(l.startswith("tg://webproxy?server=www.example.com&") for l in links))
            # файл сменился — разбор заново
            server.TELEMT_CONFIG.write_text('[[web.vhosts]]\nhost = "w2.example.com"\n', encoding="utf-8")
            self.assertEqual(server.web_vhost_host(), "w2.example.com")
            self.assertFalse(server.web_configured_in_core())

    def test_post_web_single_flight_and_transport_code(self):
        source = SERVER_PATH.read_text(encoding="utf-8")
        body = re.search(r'if path == "/api/web":\n\s+act = .*?return\n\n', source, flags=re.S).group(0)
        self.assertIn("_WEB_ACTION_LOCK.acquire(blocking=False)", body)
        self.assertIn('{"code": "busy"}', body)
        self.assertIn('{"code": "transport"}', body)

    def test_host_must_be_subdomain(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = load_server(Path(tmp))
            sub = server.web_host_is_sub
            self.assertTrue(sub("www.example.com", "example.com"))
            self.assertTrue(sub("www.example.com", "Example.COM."))
            self.assertFalse(sub("example.com", "example.com"))
            self.assertFalse(sub("wwwexample.com", "example.com"))
            self.assertFalse(sub("www.other.com", "example.com"))
            self.assertTrue(sub("www.other.com", ""))
        source = SERVER_PATH.read_text(encoding="utf-8")
        body = re.search(r'if path == "/api/web":\n\s+act = .*?return\n\n', source, flags=re.S).group(0)
        # проверка до вызова install.sh
        self.assertLess(body.index('"web_host_not_sub"'), body.index("web_cli("))

    def test_dns_marks_and_plaques_in_panel(self):
        app_js = (ROOT / "admin-web" / "static" / "app.js").read_text(encoding="utf-8")
        for key in ("webHostNotSub", "webDomainExample", "dnsMissingOne", "dnsMissingMany", "edgeDnsLegend"):
            self.assertEqual(len(re.findall(r"\n    %s: " % key, app_js)), 2, key)
        edge = app_js[app_js.index("function renderEdgeIps("):app_js.index("function renderPort443(")]
        # без dns.checked ничего не утверждаем; dns_web = null — без отметки WEB
        self.assertIn("const dnsChecked = dns.checked === true;", edge)
        self.assertIn('dnsChecked && typeof e.dns_web === "boolean"', edge)
        self.assertIn("dns.domain_missing", edge)
        self.assertIn("dns.web_missing", edge)
        self.assertIn("dnsIpList(d.dns_web_missing)", app_js)
        self.assertIn("dnsIpList(data && data.dns_missing)", app_js)
        # отказ «не поддомен» — до запроса к серверу и переведённым текстом
        action = app_js[app_js.index("async function webAction("):app_js.index("function webHostIsSub(")]
        self.assertLess(action.index("webHostIsSub(body.host)"), action.index('api("/api/web"'))
        self.assertIn('code === "web_host_not_sub" ? webHostNotSubText()', action)

    def test_web_card_shows_name_legacy_and_blockers_in_both_languages(self):
        app_js = (ROOT / "admin-web" / "static" / "app.js").read_text(encoding="utf-8")
        for key in ("webBlock_need_web_host", "webBlock_need_no_shared443", "webHostLabel",
                    "webHostSet", "webHostBad", "webLegacyWarn", "webHostApplying",
                    "webHostLegacyHint", "webResultUnknown", "webBusy"):
            self.assertEqual(len(re.findall(r"\n    %s: " % key, app_js)), 2, key)
        self.assertIn("d.legacy === true", app_js)
        self.assertIn("data-web-host", app_js)
        self.assertIn('data-web="host"', app_js)
        # код отказа install.sh доезжает до экрана, а не теряется в api()
        self.assertIn("err.code = String(data.data.code)", app_js)
        self.assertIn('needHost = code === "need_web_host"', app_js)
        # поле имени не показываем там, где имя бесполезно
        self.assertIn('const WEB_HOST_BLOCKERS = ["need_pro", "need_cert", "need_core", "need_addr"];', app_js)
        # после успеха поле перестаёт держать набранный текст
        self.assertIn("delete inp.dataset.dirty", app_js)
        self.assertIn("(?![0-9]+$)", app_js)
        self.assertIn('transport: "webResultUnknown"', app_js)
        self.assertIn('timeout: act === "host" ? WEB_HOST_CLIENT_TIMEOUT_MS', app_js)


class TelemtHotReloadTests(unittest.TestCase):
    """Правка ключей применяется перезагрузкой конфига (SIGHUP + API ядра), рестарт — запасной путь."""

    def _server(self, raw, version="3.4.18", status="ready", apply_on_hup=True, api_up=True):
        import signal
        import time
        from unittest import mock

        server = load_server(Path(raw))
        server.TELEMT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        server.TELEMT_CONFIG.write_text("[access.users]\n", encoding="utf-8")
        server.TELEMT_RELOAD_TIMEOUT = 1.5
        server.TELEMT_RELOAD_POLL = 0.02
        server.TELEMT_RELOAD_SETTLE = 0.05
        now = int(time.time())
        state = {"started": now - 100, "count": 5, "last": now - 50, "in_runtime": False,
                 "kills": [], "runs": [], "restarts": 0}

        def fake_run(cmd, timeout=8):
            state["runs"].append(cmd)
            if cmd[:2] == ["systemctl", "is-active"]:
                return 0, "active\n", ""
            if cmd[:2] == ["systemctl", "show"]:
                return 0, "4242\n", ""
            return 0, "", ""

        def fake_api(path, http_errors=False):
            if not api_up:
                return None
            if path == "/v1/system/info":
                return {"ok": True, "data": {
                    "version": version, "process_started_at_epoch_secs": state["started"],
                    "config_reload_count": state["count"], "last_config_reload_epoch_secs": state["last"]}}
            if path == "/v1/runtime/initialization":
                return {"ok": True, "data": {"status": status, "ready_at_epoch_secs": now - 10}}
            if path.startswith("/v1/users/"):
                return {"ok": True, "data": {"in_runtime": state["in_runtime"]}}
            return None

        def fake_kill(pid, sig):
            state["kills"].append((pid, sig))
            if apply_on_hup:
                state["count"] += 1
                state["last"] = int(time.time())
                state["in_runtime"] = True

        def fake_restart(name):
            state["restarts"] += 1
            return True

        server.run = fake_run
        server.telemt_api = fake_api
        server.request_service_restart = fake_restart
        patcher = mock.patch.object(server.os, "kill", fake_kill)
        patcher.start()
        self.addCleanup(patcher.stop)
        state["sighup"] = signal.SIGHUP
        return server, state

    def test_new_key_applied_by_sighup_without_restart(self):
        with tempfile.TemporaryDirectory() as raw:
            server, state = self._server(raw)
            self.assertTrue(server.telemt_hot_reload(False, ["bob"]))
            self.assertEqual(state["kills"], [(4242, state["sighup"])])
            self.assertFalse(any("restart" in cmd for cmd in state["runs"]))

    def test_revoke_hot_only_on_cores_that_cut_live_sessions(self):
        with tempfile.TemporaryDirectory() as raw:
            server, state = self._server(raw, version="3.5.5")
            self.assertFalse(server.telemt_hot_reload(True, []))
            self.assertEqual(state["kills"], [])
        with tempfile.TemporaryDirectory() as raw:
            server, state = self._server(raw, version="3.5.14")
            self.assertTrue(server.telemt_hot_reload(True, []))

    def test_silent_api_or_starting_core_means_restart_without_sighup(self):
        with tempfile.TemporaryDirectory() as raw:
            server, state = self._server(raw, api_up=False)
            self.assertFalse(server.telemt_hot_reload(False, []))
            self.assertEqual(state["kills"], [])
        with tempfile.TemporaryDirectory() as raw:
            server, state = self._server(raw, status="initializing")
            self.assertFalse(server.telemt_hot_reload(False, []))
            self.assertEqual(state["kills"], [])

    def test_unconfirmed_reload_falls_back_to_restart(self):
        with tempfile.TemporaryDirectory() as raw:
            server, state = self._server(raw, apply_on_hup=False)
            self.assertTrue(server.request_telemt_apply("add", ["bob"]))
            for _ in range(300):
                if not server._TELEMT_APPLY_RUNNING:
                    break
                time.sleep(0.01)
            self.assertEqual(len(state["kills"]), 1)
            self.assertEqual(state["restarts"], 1)

    def test_confirmed_reload_does_not_restart(self):
        with tempfile.TemporaryDirectory() as raw:
            server, state = self._server(raw)
            self.assertTrue(server.request_telemt_apply("add", ["bob"]))
            self.assertTrue(server.request_telemt_apply("limit"))
            for _ in range(300):
                if not server._TELEMT_APPLY_RUNNING:
                    break
                time.sleep(0.01)
            self.assertEqual(state["restarts"], 0)
            self.assertGreaterEqual(len(state["kills"]), 1)

    def test_added_web_limits_require_restart(self):
        with tempfile.TemporaryDirectory() as raw:
            server, state = self._server(raw)
            server.TELEMT_CONFIG.write_text(
                "[access.users]\nmain = \"aa\"\n\n[web]\ncarrier = \"https\"\n\n[[web.vhosts]]\nhost = \"x\"\n",
                encoding="utf-8")
            server.write_telemt_users({"main": "aa"})
            self.assertTrue(server._TELEMT_RESTART_REQUIRED)
            self.assertFalse(server.telemt_hot_reload(False, []))
            self.assertEqual(state["kills"], [])

    def test_user_endpoints_use_hot_apply(self):
        source = SERVER_PATH.read_text(encoding="utf-8")
        handlers = source[source.index('if path == "/api/users":'):source.index("def send_static(")]
        self.assertNotIn('request_service_restart("telemt")', handlers)
        self.assertIn('request_telemt_apply("add", [name], before=mark)', handlers)
        self.assertIn('request_telemt_apply("revoke", before=mark)', handlers)
        self.assertIn('request_telemt_apply("limit", before=mark)', handlers)
        # снимок счётчика перезагрузок — до записи, в каждом обработчике ключей
        self.assertEqual(handlers.count("mark = telemt_reload_mark()"), 4)

    def test_core_404_means_key_is_gone(self):
        import io
        import urllib.error
        from unittest import mock
        with tempfile.TemporaryDirectory() as raw:
            server = load_server(Path(raw))

            def answer(body, code):
                def fake_urlopen(url, timeout=0):
                    raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(body))
                return fake_urlopen

            not_found = b'{"ok":false,"error":{"code":"not_found","message":"User not found"}}'
            with mock.patch.object(server.urllib.request, "urlopen", answer(not_found, 404)):
                self.assertIsNone(server.telemt_api("/v1/users/bob"))   # прежние вызовы не задеты
                self.assertTrue(server._telemt_users_in_runtime(["bob"]))
            with mock.patch.object(server.urllib.request, "urlopen", answer(b"not json", 404)):
                self.assertTrue(server._telemt_users_in_runtime(["bob"]))
            # 401/5xx и ошибки разбора — не «ключа нет», а «не подтверждено»
            for body, code in ((b'{"ok":false,"error":{"code":"unauthorized"}}', 401),
                               (b'{"ok":false,"error":{"code":"internal_error"}}', 500),
                               (b"oops", 503)):
                with mock.patch.object(server.urllib.request, "urlopen", answer(body, code)):
                    self.assertFalse(server._telemt_users_in_runtime(["bob"]), code)

    def test_mark_before_write_confirms_by_counter(self):
        import time
        with tempfile.TemporaryDirectory() as raw:
            server, state = self._server(raw, version="3.5.14", apply_on_hup=False)
            state["last"] = int(time.time()) + 1        # секундная метка «после записи»
            mark = {"process_started_at_epoch_secs": state["started"], "config_reload_count": state["count"]}
            self.assertTrue(server.telemt_hot_reload(True, []))          # по-старому: подтверждено
            self.assertFalse(server.telemt_hot_reload(True, [], mark))   # со снимком: счётчик не вырос
            state["count"] += 1
            kills = len(state["kills"])
            self.assertTrue(server.telemt_hot_reload(True, [], mark))
            self.assertEqual(len(state["kills"]), kills)                 # без SIGHUP

    def test_no_sighup_when_core_restarted_during_pid_read(self):
        import time
        for names, expect in (([], True), (["bob"], False)):
            with tempfile.TemporaryDirectory() as raw:
                server, state = self._server(raw)
                inner = server.run

                def run_restarting(cmd, timeout=8, _inner=inner, _state=state):
                    if cmd[:2] == ["systemctl", "show"]:
                        _state["started"] = int(time.time()) + 5   # ядро перезапустили в окне
                    return _inner(cmd, timeout=timeout)

                server.run = run_restarting
                self.assertEqual(server.telemt_hot_reload(False, names), expect)
                self.assertEqual(state["kills"], [])


class UsersShHotReloadTests(unittest.TestCase):
    """lib/users.sh: SIGHUP + подтверждение через API ядра; curl/systemctl/kill подменены."""

    def _run(self, call, version="3.4.18", apply_on_hup=True, api_up=True):
        with tempfile.TemporaryDirectory() as raw:
            d = Path(raw)
            (d / "config.toml").write_text("[access.users]\n", encoding="utf-8")
            script = "\n".join([
                f"D={shlex.quote(str(d))}",
                f"VER={shlex.quote(version)}",
                f"APPLY={'1' if apply_on_hup else '0'}",
                f"API={'1' if api_up else '0'}",
                'TELEMT_SERVICE=telemt; TELEMT_CONFIG="$D/config.toml"',
                "export GOTELEGRAM_TELEMT_RELOAD_TIMEOUT=2",
                f"source {shlex.quote(str(ROOT / 'lib' / 'users.sh'))}",
                'NOW=$(date +%s)',
                'systemctl() { case "$1" in is-active) return 0 ;; show) echo 4242 ;; esac; }',
                'kill() { echo "$*" >> "$D/kills"; [ "$APPLY" = 1 ] && : > "$D/hup"; return 0; }',
                'restart_telemt() { echo restarted >> "$D/restarts"; return 0; }',
                'curl() {',
                '  local url="${@: -1}"; [ "$API" = 1 ] || return 7',
                '  local cnt=5 last=$((NOW-50)) rt=false',
                '  [ -f "$D/hup" ] && { cnt=6; last=$(date +%s); rt=true; }',
                '  case "$url" in',
                '    */v1/system/info) echo "{\\"ok\\":true,\\"data\\":{\\"version\\":\\"$VER\\",\\"process_started_at_epoch_secs\\":$((NOW-100)),\\"config_reload_count\\":$cnt,\\"last_config_reload_epoch_secs\\":$last}}" ;;',
                '    */v1/runtime/initialization) echo "{\\"ok\\":true,\\"data\\":{\\"status\\":\\"ready\\",\\"ready_at_epoch_secs\\":$((NOW-10))}}" ;;',
                '    */v1/users/*) echo "{\\"ok\\":true,\\"data\\":{\\"in_runtime\\":$rt}}" ;;',
                '  esac',
                '}',
                f"{call}; rc=$?",
                'echo "rc=$rc kills=$(wc -l < "$D/kills" 2>/dev/null | tr -d " " || echo 0) restarts=$(cat "$D/restarts" 2>/dev/null | wc -l | tr -d " ")"',
            ])
            result = subprocess.run(["bash", "-c", script], cwd=ROOT, text=True,
                                    capture_output=True, check=False, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip().splitlines()[-1]

    def test_new_key_applied_without_restart(self):
        out = self._run("telemt_apply_user_change add bob")
        self.assertTrue(out.startswith("rc=0 kills=1 "), out)
        self.assertTrue(out.endswith("restarts=0"), out)

    def test_revoke_on_old_core_restarts_without_sighup(self):
        out = self._run("telemt_apply_user_change revoke", version="3.4.18")
        self.assertIn("rc=0", out)
        self.assertTrue(out.endswith("restarts=1"), out)
        self.assertNotIn("kills=1", out)

    def test_revoke_on_new_core_is_hot(self):
        out = self._run("telemt_hot_reload revoke", version="3.5.14")
        self.assertTrue(out.startswith("rc=0 kills=1"), out)

    def test_unconfirmed_or_silent_api_falls_back(self):
        out = self._run("telemt_apply_user_change limit", apply_on_hup=False)
        self.assertTrue(out.startswith("rc=0 kills=1"), out)
        self.assertTrue(out.endswith("restarts=1"), out)
        out = self._run("telemt_hot_reload add bob", api_up=False)
        self.assertTrue(out.startswith("rc=1"), out)
        self.assertNotIn("kills=1", out)

    def test_callers_use_hot_apply(self):
        source = (ROOT / "lib" / "users.sh").read_text(encoding="utf-8")
        # рестарт остался только там, где он нужен: дописанный [web.limits] читается при старте
        self.assertEqual(source.count("restart_telemt >/dev/null"), 1)
        self.assertIn("web_limits_added=1", source)
        self.assertIn('_users_commit "$block" add "$name"', source)
        self.assertIn('_users_commit "$block" revoke', source)
        self.assertIn("telemt_apply_user_change limit", source)

if __name__ == "__main__":
    unittest.main()

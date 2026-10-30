"""Регрессии интеграции 0.4.6: секреты argv и изоляция восстановления от /etc.

Только синтетические данные. Системные команды перехвачены ещё до вызова продукта:
при возврате ошибки старый код оставит свидетельство, а не затрёт файлы машины.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
BASH = os.environ.get("BASH5") or shutil.which("bash") or "bash"


@unittest.skipUnless(shutil.which("jq"), "нужен jq")
class ReleaseSecurity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ga-release-security-")
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)

    def shell(self, script, **overrides):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("GOAMNEZIA_") and k not in {"BASH_ENV", "ENV"}}
        env.update(TEST_ROOT=str(ROOT), TEST_TMP=str(self.tmp), **overrides)
        p = subprocess.run([BASH, "-c", script], env=env, cwd=ROOT,
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def check_agent_card(self, transport):
        secret, token, code = "synthetic_path_secret", "synthetic_bearer_token", "synthetic_enroll_code"
        rec = {"id": "a-test", "secret": secret, "token": token, "require_token": True,
               "scope": "admin", "enabled": True, "mode": "static", "transport": transport}
        p = self.shell(r'''
source "$TEST_ROOT/lib/agents.sh"
declare -A I18N
source "$TEST_ROOT/lib/lang/ru.d/agents.sh"
t() { printf '%s' "${I18N[$1]:-$1}"; }
tf() { local fmt; fmt=$(t "$1"); shift; printf "$fmt" "$@"; }
ga_agents_get() { printf '%s' "$TEST_RECORD"; }
ga_agents_endpoint() { printf '%s' '{"base":"https://example.invalid","cert_fingerprint_sha256":"","via":"domain","reachable":true}'; }
ga_agents_http_enabled() { return 0; }
ga_agents_server_ip() { printf '%s' '203.0.113.10'; }
jq() { printf '%s\0' "$@" >>"$TEST_TMP/jq-argv"; command jq "$@"; }
ga_agents_card a-test "" "$TEST_CODE"
''', TEST_RECORD=json.dumps(rec), TEST_CODE=code)
        argv = (self.tmp / "jq-argv").read_bytes()
        card = json.loads(p.stdout)
        credential = code if transport == "connect" else token
        self.assertIn(credential, card["connect"])
        self.assertIn(credential, card["hint"])
        for value in (secret, token, code):
            self.assertNotIn(value.encode(), argv, "секрет в аргументах jq")

    def test_static_agent_card_keeps_hint_token_out_of_argv(self):
        self.check_agent_card("")

    def test_connect_agent_card_keeps_hint_code_out_of_argv(self):
        self.check_agent_card("connect")

    def restore(self, dry, redirected):
        archive = self.tmp / "archive"
        archive.mkdir()
        (archive / "config.json").write_text('{"profiles":{},"mode":"lite"}')
        (archive / "nginx.conf").write_text("# synthetic secret panel path\n")
        for part in ("live", "archive", "renewal"):
            folder = archive / "letsencrypt" / part
            folder.mkdir(parents=True)
            (folder / "synthetic.pem").write_text(part)
        self.shell(r'''
source "$TEST_ROOT/lib/backup.sh"
GOAMNEZIA_DIR="$TEST_TMP/product"
CONFIG_FILE="$GOAMNEZIA_DIR/config.json"; USERS_FILE="$GOAMNEZIA_DIR/users.json"
STATE_DIR="$GOAMNEZIA_DIR/state"; BACKUP_DIR="$GOAMNEZIA_DIR/backups"
BOT_DIR="$TEST_TMP/bot"; ADMIN_DIR="$TEST_TMP/admin"
BOT_SERVICE=goamnezia-bot; ADMIN_SERVICE=goamnezia-admin
NGINX_SITE_CONF=/etc/nginx/sites-available/goamnezia
NGINX_SITE_LINK=/etc/nginx/sites-enabled/goamnezia
if [ "$TEST_REDIRECT" = 1 ]; then
    GOAMNEZIA_NGINX_DIR="$TEST_TMP/nginx"
    GOAMNEZIA_LE_DIR="$TEST_TMP/le"
fi
# Не выполнять НИ ОДНОЙ операции над /etc, даже если регрессия вернула старый код.
_intercept() {
    local cmd="$1" arg; shift
    for arg in "$@"; do
        case "$arg" in /etc|/etc/*) printf '%s %s\n' "$cmd" "$*" >>"$TEST_TMP/host-attempts"; return 0 ;; esac
    done
    command "$cmd" "$@"
}
cp() { _intercept cp "$@"; }
mkdir() { _intercept mkdir "$@"; }
ln() { _intercept ln "$@"; }
chmod() { _intercept chmod "$@"; }
systemctl() { printf '%s\n' "$*" >>"$TEST_TMP/systemctl"; }
ga_dry() { [ "$GOAMNEZIA_DRY_RUN" = 1 ]; }
ga_dry_echo() { :; }
ga_put_json_file() { cp "$1" "$2"; }
_restore_snapshot() { :; }
_restore_wipe_tmp() { :; }
_ga_restore_in_unit() { return 1; }
_restore_converge_mode() { :; }
_restore_drop_unlisted_profiles() { :; }
_restore_server_ip_check() { :; }
log_info() { :; }; log_success() { :; }; log_warning() { :; }
t() { printf '%s' "$1"; }; tf() { printf '%s' "$1"; }; ga_audit() { :; }
_restore_apply_locked synthetic.tar.gz "$TEST_TMP" "$TEST_TMP/archive"
''', GOAMNEZIA_DRY_RUN=str(dry), TEST_REDIRECT=str(int(redirected)))
        self.assertFalse((self.tmp / "host-attempts").exists(), "восстановление обратилось к /etc")
        if dry:
            self.assertFalse((self.tmp / "systemctl").exists(), "dry-run запустил systemctl")
        if redirected:
            conf = self.tmp / "nginx/sites-available/goamnezia"
            self.assertEqual(conf.read_text(), (archive / "nginx.conf").read_text())
            self.assertEqual(conf.stat().st_mode & 0o777, 0o600)
            self.assertEqual((self.tmp / "nginx/sites-enabled/goamnezia").resolve(), conf.resolve())
            for part in ("live", "archive", "renewal"):
                self.assertEqual((self.tmp / "le" / part / "synthetic.pem").read_text(), part)

    def test_dry_restore_skips_host_nginx_and_certificates(self):
        self.restore(dry=1, redirected=False)

    def test_dry_restore_writes_only_redirected_service_files(self):
        self.restore(dry=1, redirected=True)

    def test_normal_restore_keeps_certificate_and_nginx_contents(self):
        self.restore(dry=0, redirected=True)


if __name__ == "__main__":
    unittest.main()

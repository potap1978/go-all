# -*- coding: utf-8 -*-
"""Инструменты живых проверок и SSH: общий канал на прогон и повтор при обрыве (tests/live/ssh_link.py, TOOL-01).

Тестовые серверы под фоновым перебором паролей рвут свежие рукопожатия (MaxStartups): «Connection closed by …
port 22», kex_exchange_identification, код 255 — и из-за одного обрыва падал весь прогон acceptance.py. Здесь к
серверам никто не ходит: первым в PATH стоит подставной ssh. Он пишет каждый вызов (аргументы, режим каталога
сокетов, хеш stdin) в журнал и отвечает по плану: обрыв на рукопожатии, отказ соединения, отказ ключа, обрыв посреди
начатой команды, свой код команды, успех. Удалённой команды он не выполняет — только печатает метку начала, если
вызывающий её просил.

Проверяется: обрыв до начала команды повторяется с растущей паузой и пределом; удачный вызов — один; свой отказ
команды и отказ ключа — без повтора; обрыв посреди повторяется только у безопасного шага; stdin уходит целиком в
каждую попытку; канал — ControlMaster=auto, каталог 0700, ControlPersist, в конце `ssh -O exit` и каталога нет, в
том числе когда прогон упал; в журнале нет ни команды, ни ввода; труба сервер → клиент повторяется целиком;
bot_live.sh шаг, меняющий сервер, вслепую не повторяет, а заливку — повторяет с тем же содержимым.

Запуск: python3 -m unittest tests.test_live_ssh
"""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = ROOT / 'tests' / 'live'
BASH = os.environ.get('BASH5') or shutil.which('bash') or 'bash'
MARK = 'GA-LIVE-SSH-STARTED'
SERVER, CLIENT = 'root@203.0.113.20', 'root@198.51.100.10'

FAKE_SSH = r'''#!/usr/bin/env python3
# Подставной ssh проверок tests/test_live_ssh.py: журнал вызова и ответ по плану, ничего не выполняет.
import fcntl, hashlib, json, os, sys, time
state = os.environ["FAKE_SSH_STATE"]
args = sys.argv[1:]
ctl = next((args[i + 1].split("=", 1)[1] for i in range(len(args) - 1)
            if args[i] == "-o" and args[i + 1].startswith("ControlPath=")), None)
try:
    mode = oct(os.stat(os.path.dirname(ctl)).st_mode & 0o777) if ctl else None
except OSError:
    mode = "missing"
control = args[args.index("-O") + 1] if "-O" in args else None
cmd = "" if control else args[-1]
data = b"" if control else sys.stdin.buffer.read()
match = os.environ.get("FAKE_SSH_MATCH", "")
counted = not control and (not match or match in cmd)
with open(os.path.join(state, "calls.jsonl"), "a") as log:
    fcntl.flock(log, fcntl.LOCK_EX)
    n = 0
    if counted:
        with open(os.path.join(state, "count"), "a+") as c:
            c.seek(0)
            n = len(c.read())
            c.write("x")
    step = "ok"
    plan = [s for s in os.environ.get("FAKE_SSH_PLAN", "").split(",")]
    if counted and n < len(plan) and plan[n]:
        step = plan[n]
    log.write(json.dumps({"t": time.time(), "argv": args, "control": control, "ctl_mode": mode, "cmd": cmd,
                          "counted": counted, "step": step if counted else "", "stdin_len": len(data),
                          "stdin_sha": hashlib.sha256(data).hexdigest()}) + "\n")
if control:
    sys.exit(0)
host = args[-2].split("@")[-1] if len(args) > 1 else "?"
if step == "drop":
    sys.stderr.write("kex_exchange_identification: Connection closed by remote host\r\n"
                     "Connection closed by %s port 22\r\n" % host)
    sys.exit(255)
if step == "refused":
    sys.stderr.write("ssh: connect to host %s port 22: Connection refused\r\n" % host)
    sys.exit(255)
if step == "deny":
    sys.stderr.write("root@%s: Permission denied (publickey).\r\n" % host)
    sys.exit(255)
if os.environ.get("FAKE_SSH_MARK", "") and os.environ["FAKE_SSH_MARK"] in cmd:
    sys.stderr.write(os.environ["FAKE_SSH_MARK"] + "\n")
    sys.stderr.flush()
if step == "lost":
    sys.stdout.write("half")
    sys.stdout.flush()
    sys.stderr.write("Connection to %s closed by remote host.\r\n" % host)
    sys.exit(255)
sys.stdout.write(os.environ.get("FAKE_SSH_OUT", ""))
sys.exit(int(step[3:]) if step.startswith("rc:") else 0)
'''

# Один вызов Host из acceptance.py в отдельном процессе: свой канал прогона, свой выход (atexit закрывает канал).
DRIVER = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import acceptance
kw = json.loads(sys.argv[3])
data = kw.pop("input", None)
if data is not None:
    kw["input"] = data.encode()
h = acceptance.Host("%s", "/nonexistent-key")
rc, out, err = h.run(sys.argv[2], **kw)
print(json.dumps({"rc": rc, "out": out, "err": err}))
''' % SERVER


class FakeSsh:
    """Каталог с подставным ssh и его журналом; env — окружение процесса проверки."""

    def __init__(self, plan='', match='', out=''):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix='ga-fakessh-'))
        (self.tmp / 'bin').mkdir()
        (self.tmp / 'state').mkdir()
        exe = self.tmp / 'bin' / 'ssh'
        exe.write_text(FAKE_SSH, encoding='utf-8')
        exe.chmod(0o755)
        # scp — тоже подставной и всегда с отказом: в сеть отсюда не ходит ничто (прежний bot_live.sh звал scp)
        scp = self.tmp / 'bin' / 'scp'
        scp.write_text('#!/bin/sh\nexit 1\n', encoding='utf-8')
        scp.chmod(0o755)
        self.env = dict(os.environ)
        self.env.update({
            'PATH': str(self.tmp / 'bin') + os.pathsep + os.environ.get('PATH', ''),
            'FAKE_SSH_STATE': str(self.tmp / 'state'), 'FAKE_SSH_PLAN': plan, 'FAKE_SSH_MATCH': match,
            'FAKE_SSH_OUT': out, 'FAKE_SSH_MARK': MARK,
            'GA_LIVE_SSH_PAUSE': '0.02',
        })
        self.env.pop('GA_LIVE_SSH_DIR', None)
        self.env.pop('GA_LIVE_SSH_TRIES', None)

    def calls(self):
        p = self.tmp / 'state' / 'calls.jsonl'
        if not p.exists():
            return []
        return [json.loads(ln) for ln in p.read_text(encoding='utf-8').splitlines() if ln.strip()]

    def counted(self):
        return [c for c in self.calls() if c['counted']]

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class HostRunRetries(unittest.TestCase):
    """acceptance.Host.run — то, чем ходит вся приёмка."""

    def setUp(self):
        self.fakes = []

    def tearDown(self):
        for f in self.fakes:
            f.close()

    def run_host(self, cmd='echo hello', plan='', out='hello\n', **kw):
        fake = FakeSsh(plan=plan, out=out)
        self.fakes.append(fake)
        p = subprocess.run([sys.executable, '-c', DRIVER, str(LIVE), cmd, json.dumps(kw)], env=fake.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr[-1500:])
        return json.loads(p.stdout.strip().splitlines()[-1]), fake, p.stderr

    def test_connection_closed_before_the_command_is_retried(self):
        # Ровно случай TOOL-01: сервер дважды рвёт рукопожатие, третья попытка проходит — прогон не падает.
        r, fake, log = self.run_host(plan='drop,drop')
        self.assertEqual((r['rc'], r['out']), (0, 'hello\n'), log)
        self.assertEqual(len(fake.counted()), 3)
        self.assertIn('Connection closed by 203.0.113.20 port 22', log)    # почему повтор — строкой самого ssh
        self.assertNotIn(MARK, r['err'])                         # метка начала команды — не вывод команды

    def test_refused_connection_is_retried_too(self):
        r, fake, _ = self.run_host(plan='refused')
        self.assertEqual((r['rc'], len(fake.counted())), (0, 2))

    def test_a_good_call_is_made_once(self):
        r, fake, log = self.run_host()
        self.assertEqual((r['rc'], r['out'], len(fake.counted())), (0, 'hello\n', 1))
        self.assertEqual(log.strip(), '')

    def test_the_command_own_failure_is_not_retried(self):
        # Команда дошла и ответила сама — это ответ, а не обрыв; даже её собственный код 255.
        for code in ('1', '255'):
            r, fake, _ = self.run_host(plan='rc:' + code)
            self.assertEqual((r['rc'], len(fake.counted())), (int(code), 1))

    def test_a_refused_key_is_not_retried(self):
        r, fake, _ = self.run_host(plan='deny')
        self.assertEqual((r['rc'], len(fake.counted())), (255, 1))

    def test_a_step_that_changes_the_server_is_not_repeated_after_a_drop_in_the_middle(self):
        r, fake, log = self.run_host(plan='lost')
        self.assertEqual((r['rc'], len(fake.counted())), (255, 1))
        self.assertIn('not repeated blindly', log)

    def test_a_read_is_repeated_after_a_drop_in_the_middle(self):
        r, fake, _ = self.run_host(plan='lost', safe=True)
        self.assertEqual((r['rc'], r['out'], len(fake.counted())), (0, 'hello\n', 2))

    def test_retries_have_a_limit_and_growing_pauses(self):
        r, fake, log = self.run_host(plan='drop,drop,drop,drop,drop,drop,drop')
        calls = fake.counted()
        self.assertEqual((r['rc'], len(calls)), (255, 5))
        self.assertIn('gave up after 5 attempts', log)
        gaps = [b['t'] - a['t'] for a, b in zip(calls, calls[1:])]
        self.assertTrue(all(b > a for a, b in zip(gaps, gaps[1:])), gaps)    # 0,02 → 0,04 → 0,08 → 0,16

    def test_input_goes_whole_into_every_attempt(self):
        data = 'umask-me ' * 5000
        r, fake, _ = self.run_host(cmd='cat > /root/ga-live/x', plan='drop', input=data, safe=True)
        shas = {c['stdin_sha'] for c in fake.counted()}
        self.assertEqual((r['rc'], len(fake.counted())), (0, 2))
        self.assertEqual(shas, {hashlib.sha256(data.encode()).hexdigest()})

    def test_the_log_has_no_command_and_no_input(self):
        secret_cmd = "printf %s 'vpn://SECRETSECRETSECRETSECRETSECRET' | bash /opt/goamnezia/install.sh"
        r, fake, log = self.run_host(cmd=secret_cmd, plan='drop,lost', input='PrivateKey = SECRETKEYSECRETKEY', safe=True)
        self.assertEqual(r['rc'], 0)
        self.assertIn('attempt 2/5', log)
        self.assertIn('attempt 3/5', log)
        for leak in ('SECRET', 'install.sh', 'PrivateKey', 'half'):
            self.assertNotIn(leak, log)

    def test_one_channel_in_a_private_dir_closed_at_the_end(self):
        r, fake, _ = self.run_host()
        calls = fake.calls()
        run = [c for c in calls if not c['control']]
        argv = run[0]['argv']
        opts = [argv[i + 1] for i in range(len(argv) - 1) if argv[i] == '-o']
        self.assertIn('ControlMaster=auto', opts)
        self.assertTrue(any(o.startswith('ControlPersist=') for o in opts), opts)
        ctl = next(o.split('=', 1)[1] for o in opts if o.startswith('ControlPath='))
        self.assertEqual(run[0]['ctl_mode'], '0o700')
        # в конце процесса — `ssh -O exit` этой машины, и каталога сокетов больше нет
        self.assertEqual([(c['control'], c['argv'][-1]) for c in calls if c['control']], [('exit', SERVER)])
        self.assertFalse(os.path.exists(os.path.dirname(ctl)))


class ChannelAndPipe(unittest.TestCase):

    def setUp(self):
        sys.path.insert(0, str(LIVE))
        import ssh_link
        self.ssh_link = ssh_link

    def tearDown(self):
        sys.path.remove(str(LIVE))

    def drive(self, code, fake):
        return subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, %r)\n' % str(LIVE) + code],
                              env=fake.env, capture_output=True, text=True, timeout=60)

    def test_the_channel_is_closed_when_the_run_fails(self):
        # acceptance.py целиком: заливка проверок отказала — SystemExit, а канал всё равно закрыт и каталог убран.
        fake = FakeSsh(plan='rc:1', match='cat >')
        try:
            p = subprocess.run([sys.executable, str(LIVE / 'acceptance.py'), '--server', SERVER, '--client', CLIENT,
                                '--key', '/nonexistent-key', '--checks', 'bridge'], env=fake.env, capture_output=True,
                               text=True, timeout=60)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn('upload', p.stderr)
            calls = fake.calls()
            ctl = {a.split('=', 1)[1] for c in calls for a in c['argv'] if a.startswith('ControlPath=')}
            self.assertEqual(len(ctl), 1, ctl)                   # один канал на обе машины
            self.assertEqual(sorted(c['argv'][-1] for c in calls if c['control'] == 'exit'), sorted([CLIENT, SERVER]))
            self.assertFalse(os.path.exists(os.path.dirname(ctl.pop())))
        finally:
            fake.close()

    def test_a_channel_given_by_the_caller_is_used_and_left_open(self):
        fake = FakeSsh()
        own = tempfile.mkdtemp(prefix='ga-ssh-own-')
        try:
            fake.env['GA_LIVE_SSH_DIR'] = own
            p = self.drive('import acceptance\nprint(acceptance.Host(%r, "/k").run("true")[0])' % SERVER, fake)
            self.assertEqual(p.stdout.strip(), '0', p.stderr)
            self.assertTrue(any('ControlPath=%s/%%C' % own in c['argv'] for c in fake.calls()))
            self.assertEqual([c for c in fake.calls() if c['control']], [])     # закроет открывший
            self.assertTrue(os.path.isdir(own))
        finally:
            fake.close()
            shutil.rmtree(own, ignore_errors=True)

    def test_a_pipe_server_to_client_is_retried_whole(self):
        # Конфиг идёт трубой сервер → клиент; клиент дважды не дождался рукопожатия — вся труба заново.
        fake = FakeSsh(plan='drop,drop', match='cat >', out='[Interface]\n')
        try:
            code = ('import ssh_link\nc = ssh_link.Channel()\ns = ssh_link.Host(%r, "/k", c)\nd = ssh_link.Host(%r, "/k", c)\n'
                    'print(s.pipe("conf", d, "cat > /root/ga-live/a.conf", safe=True)[0])\nc.close()' % (SERVER, CLIENT))
            p = self.drive(code, fake)
            self.assertEqual(p.stdout.strip(), '0', p.stderr)
            dest = fake.counted()
            self.assertEqual(len(dest), 3)
            self.assertEqual(dest[-1]['stdin_sha'], hashlib.sha256(b'[Interface]\n').hexdigest())
        finally:
            fake.close()

    def test_verdicts(self):
        v = self.ssh_link.verdict
        self.assertEqual(v(0, '', True), 'done')
        self.assertEqual(v(255, 'kex_exchange_identification: Connection closed by remote host', False), 'retry')
        self.assertEqual(v(255, 'root@h: Permission denied (publickey).', False), 'fatal')
        self.assertEqual(v(255, 'Connection to h closed by remote host.', True), 'lost')
        self.assertEqual(v(255, 'the command said 255 itself', True), 'done')


class BotLiveScript(unittest.TestCase):
    """bot_live.sh ходит тем же каналом (ssh_link.py командной строкой)."""

    def run_bot_live(self, step, fake, seconds=40):
        return subprocess.run([BASH, str(LIVE / 'bot_live.sh'), SERVER, '--key', '/nonexistent-key', step],
                              env=fake.env, capture_output=True, text=True, timeout=seconds)

    def test_the_scenario_step_is_not_repeated_after_a_drop_in_the_middle(self):
        # run (bot_scenario.py) заводит людей: прежний remote() повторял его на любом коде 255.
        fake = FakeSsh(plan='lost', match='bot_scenario.py')
        try:
            try:
                p = self.run_bot_live('run', fake)
            except subprocess.TimeoutExpired:
                self.fail('bot_live.sh повторял шаг по кругу')
            self.assertNotEqual(p.returncode, 0)
            self.assertEqual(len(fake.counted()), 1, p.stderr)
            self.assertEqual([c['control'] for c in fake.calls() if c['control']], ['exit'])   # канал закрыт
        finally:
            fake.close()

    def test_a_handshake_drop_is_retried_and_the_upload_goes_whole_again(self):
        fake = FakeSsh(plan='drop,drop', match='cat > /root/ga-live/bot/bot_scenario.py')
        try:
            p = self.run_bot_live('setup', fake, seconds=60)
            self.assertEqual(p.returncode, 0, p.stderr[-1500:])
            sha = hashlib.sha256((LIVE / 'remote' / 'bot_scenario.py').read_bytes()).hexdigest()
            self.assertEqual([c['stdin_sha'] for c in fake.counted()], [sha] * 3)
        finally:
            fake.close()


if __name__ == '__main__':
    unittest.main()

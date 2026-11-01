# -*- coding: utf-8 -*-
"""Живая проверка kmod (tests/live/acceptance.py --checks kmod) — сама проверка, без серверов.

Серверов здесь нет: сервер и клиент — подставные машины в памяти. Сервер отвечает на вызовы моста (kmod-check,
kmod-status, profiles-status, user-add/user-del, kmod-install / kmod-engine / kmod-remove службой systemd-run) и на
remote/kmod_server.py, клиент — на remote/kmod_client.sh, по простой модели: движок каждого профиля, модуль есть или
нет, люди. Так проверяется ход самой проверки и её вердикты, которые иначе впервые увидел бы проверяющий на стенде:

  • последовательность: проверка хоста → снимок → временный человек → клиенты → программа (исходное) → kmod-install →
    модуль → программа → модуль → как было (программа, kmod-remove) → клиенты прочь, человек удалён → журнал ядра →
    снимок до/после; все строки OK;
  • хост без модулей (контейнер LXC) — одна строка SKIP, ничего не ставится и не создаётся;
  • ключ клиента сменился между шагами — FAIL «KEYS CHANGED» у этого профиля;
  • перевод откатился (клиенты не вернулись) — FAIL, дальше шаги не идут, а возврат как было, удаление человека и
    клиентов всё равно делаются;
  • модуль был до проверки (сосед или прежний выбор) — kmod-remove не зовётся;
  • строки журнала ядра с BUG и soft lockup — FAIL;
  • обрыв SSH сразу после запуска службы долгого шага — служба не запускается второй раз, шаг дожидается ответа.
Вдобавок — разбор движка в remote/kmod_server.py (как ga_amn_engine продукта) и его фильтр журнала ядра.

Запуск: python3 -m unittest tests.test_live_kmod
"""
import json
import pathlib
import re
import sys
import unittest
from unittest import mock

LIVE = pathlib.Path(__file__).resolve().parent / 'live'
sys.path.insert(0, str(LIVE))
sys.path.insert(0, str(LIVE / 'remote'))
import acceptance  # noqa: E402
import kmod_server  # noqa: E402


class Model:
    """Сервер goAmnezia с профилями на программе; модуль ставится и убирается вызовами моста."""

    def __init__(self, profs=('awg2', 'awg3'), check_ok=True, rollback_on=None, key_change_on=None, had_module=False,
                 klog_bad=()):
        self.profs = list(profs)
        self.engine = {p: 'program' for p in profs}
        self.file = {p: '' for p in profs}
        self.installed = self.loaded = had_module
        self.want = ''
        self.check_ok = check_ok
        self.rollback_on, self.key_change_on = rollback_on, key_change_on
        self.klog_bad = list(klog_bad)
        self.users = set()
        self.actions = []           # вызовы моста по порядку (изменяющие — с движком)
        self.results = {}           # файл ответа службы → JSON
        self.switches = 0
        self.clients = {}           # тег → профиль
        self.changed_keys = set()

    # ── мост ──
    def bridge(self, action, cmd):
        if action == 'kmod-check':
            if not self.check_ok:
                return {'status': 'success', 'check': {'ok': False, 'code': 'container',
                                                       'reason': 'модуль ядра здесь нельзя: это контейнер LXC'}}
            return {'status': 'success', 'check': {
                'ok': True, 'kernel': '6.8.0-36-generic', 'reuse': False, 'kaskad': False,
                'module': {'installed': self.installed, 'ko_version': ''},
                'plan': {'install': ['dkms', 'gcc', 'make'], 'size_mb': 120}}}
        if action == 'kmod-status':
            return {'status': 'success', 'kmod': {'state': 'on' if self.want == 'kernel' else 'off', 'want': self.want,
                                                  'installed': self.installed, 'progress': None}}
        if action == 'profiles-status':
            return {'status': 'success', 'profiles': [{'profile': p, 'engine': self.engine[p],
                                                       'engine_want': self.file[p]} for p in self.profs]}
        if action == 'user-add':
            self.users.add(re.search(r"--name=([\w-]+)", cmd).group(1))
            return {'status': 'success'}
        if action == 'user-del':
            self.users.discard(re.search(r"--name=([\w-]+)", cmd).group(1))
            return {'status': 'success'}
        raise AssertionError('unexpected bridge action %s' % action)

    def long_action(self, action, cmd):
        if action == 'kmod-install':
            self.installed = self.loaded = True
            for p in self.profs:
                self.file[p] = self.file[p] or 'program'
            return {'status': 'success', 'state': 'ready', 'version': '3.1.20260906+kaskad-k31.1',
                    'kernel': '6.8.0-36-generic', 'reused': False, 'p218': False, 'packages': ['dkms', 'gcc', 'make'],
                    'selftest': {'ok': True, 'cases': [{'id': 'awg2p', 'ok': True}, {'id': 'awg2k', 'ok': True}]},
                    'duration_s': 95}
        if action == 'kmod-engine':
            target = re.search(r'--engine=(\w+)', cmd).group(1)
            self.switches += 1
            if target == 'kernel' and not self.loaded:
                return {'status': 'error', 'code': 'kmod_not_ready', 'message': 'нет модуля'}
            if self.rollback_on == self.switches:
                p = self.profs[0]
                return {'status': 'error', 'code': 'kmod_clients_lost', 'message': 'никто не вернулся', 'rolled_back': True,
                        'profiles': [{'profile': p, 'from': self.engine[p], 'to': target, 'restarted': True,
                                      'alive_before': 1, 'returned': 0, 'return_s': None, 'note': 'lost'}]}
            rows = []
            for p in self.profs:
                if self.engine[p] == target:
                    rows.append({'profile': p, 'from': target, 'to': target, 'restarted': False, 'note': 'already'})
                    continue
                rows.append({'profile': p, 'from': self.engine[p], 'to': target, 'restarted': True, 'alive_before': 1,
                             'returned': 1, 'return_s': 17, 'note': ''})
                self.engine[p] = target
                self.file[p] = target
            self.want = target
            if self.key_change_on == self.switches:
                self.changed_keys.add(self.profs[0])
            return {'status': 'success', 'engine': target, 'want': target, 'profiles': rows, 'rolled_back': False}
        if action == 'kmod-remove':
            for p in self.profs:
                self.engine[p] = 'program'
            self.installed = self.loaded = False
            self.want = 'program'
            return {'status': 'success', 'removed': True, 'kept_for_neighbour': False, 'packages': ['dkms'],
                    'command': 'apt remove dkms'}
        raise AssertionError('unexpected long action %s' % action)

    # ── remote/kmod_server.py ──
    def facts(self, user, profs, since):
        out = {'profiles': {}, 'module': {'loaded': self.loaded, 'dkms': ['amneziawg/3.1.20260906: installed']
                                          if self.installed else [], 'hooks': ['/etc/kernel/postinst.d']
                                          if self.installed else [], 'modules_load': self.installed}}
        for p in profs:
            out['profiles'][p] = {'container': 'amnezia-' + p, 'running': True, 'engine': self.engine[p],
                                  'engine_file': self.file[p], 'engine_block': True, 'server_fp': 'srv' + p,
                                  'user_fp': ('usr' + p) if user in self.users else '', 'mss': {'v4': 2, 'v6': None},
                                  'warp': 'none', 'sh_n': True}
        if since:
            out['klog'] = {'bad': self.klog_bad, 'count': len(self.klog_bad), 'source': 'journalctl -k'}
        return out

    # ── remote/kmod_client.sh ──
    def client(self, sub, tag, args):
        p = tag.rsplit('-', 1)[1]
        pub = ('new' if p in self.changed_keys else 'usr') + p
        if sub == 'up':
            self.clients[tag] = p
            return 'UP %s hs=2s ping=ok(31ms) pub=%s srv=srv%s engine=tun iperf=yes server=10.8.2.0' % (tag, pub, p)
        if sub == 'probe':
            return 'PROBE %s hs=1 now=2 ping=ok(30ms) pub=%s srv=srv%s' % (tag, pub, p)
        if sub == 'wait':
            return 'BACK %s after=17' % tag
        if sub == 'iperf':
            return 'IPERF %s up=%d down=%d' % (tag, 400 if self.engine[p] == 'kernel' else 300, 500)
        if sub == 'down':
            self.clients.pop(tag, None)
            return 'DOWN %s' % tag
        raise AssertionError(sub)


class FakeServer:
    def __init__(self, m, drop_after_start=()):
        self.m = m
        self.drop_after_start = set(drop_after_start)   # действия, на которых SSH рвётся уже после запуска службы

    def run(self, cmd, input=None, timeout=900, safe=False, tries=None):
        m = self.m
        if 'systemd-run' in cmd:
            unit = re.search(r'--unit=(\S+)', cmd).group(1)
            action = re.search(r'--action=([a-z-]+)', cmd).group(1)
            m.actions.append(action + (':' + re.search(r'--engine=(\w+)', cmd).group(1) if action == 'kmod-engine' else ''))
            m.results[unit] = m.long_action(action, cmd)
            if action in self.drop_after_start:
                self.drop_after_start.discard(action)
                return 255, '', 'Connection to 203.0.113.20 closed by remote host.'
            return 0, '', ''
        if cmd.startswith('systemctl is-active'):
            unit = re.search(r'is-active (\S+)', cmd).group(1)
            return 0, 'inactive\n' + ('answered\n' if 'answered' in cmd and unit in m.results else ''), ''
        if cmd.startswith('cat ') and 'ga-live-kmod-' in cmd:
            unit = re.search(r'(ga-live-kmod-\w+)\.json', cmd).group(1)
            return 0, json.dumps(m.results.pop(unit)) + '\n', ''
        if cmd.startswith('rm -f '):
            return 0, '', ''
        if 'kmod_server.py facts' in cmd:
            a = cmd.split('kmod_server.py facts ', 1)[1].split()
            return 0, json.dumps(m.facts(a[0].strip("'"), a[1].split(','), int(a[2]))) + '\n', ''
        if 'kmod_server.py iperf-' in cmd:
            return 0, '{"ok": true}\n', ''
        if cmd == 'date +%s':
            return 0, '1760000000\n', ''
        if 'snapshot.py' in cmd and 'compare.py' not in cmd:
            return 0, '', ''
        if 'compare.py' in cmd:
            return 0, 'PASS keys\nSUMMARY: PASS 40, FAIL 0, WARN 1\n', ''
        mm = re.search(r'--action=([a-z-]+)', cmd)
        if mm:
            if mm.group(1) == 'kmod-status' or mm.group(1) == 'profiles-status' or mm.group(1) == 'kmod-check':
                return 0, json.dumps(m.bridge(mm.group(1), cmd)) + '\n', ''
            m.actions.append(mm.group(1))
            return 0, json.dumps(m.bridge(mm.group(1), cmd)) + '\n', ''
        raise AssertionError('unexpected server command: %s' % cmd[:120])

    def pipe(self, cmd, dest, dest_cmd, timeout=300, safe=False, tries=None):
        assert 'user-key' in cmd and '--format=conf' in cmd, cmd
        return dest.run(dest_cmd, input=b'[Interface]\n', timeout=timeout, safe=safe)


class FakeClient:
    def __init__(self, m):
        self.m = m

    def run(self, cmd, input=None, timeout=900, safe=False, tries=None):
        if cmd == 'date +%s':
            return 0, '1760000100\n', ''
        mm = re.search(r'kmod_client\.sh (\w+) (\S+)\s*(.*)$', cmd)
        if not mm:
            raise AssertionError('unexpected client command: %s' % cmd[:120])
        return 0, self.m.client(mm.group(1), mm.group(2), mm.group(3).split()) + '\n', ''


def ctx_for(m, drop_after_start=(), **over):
    ctx = acceptance.Ctx()
    args = dict(kmod_profiles=None, kmod_keep_module=False, kmod_iperf=10, kmod_wait=180, kmod_install_timeout=2400,
                kmod_engine_timeout=1200)
    args.update(over)
    ctx.a = mock.Mock(**args)
    ctx.server, ctx.client = FakeServer(m, drop_after_start), FakeClient(m)
    ctx.cfg = {'profiles': {p: {'port': 40000 + i, 'container': 'amnezia-' + p} for i, p in enumerate(m.profs)}}
    ctx.cfg['profiles']['awg1'] = {'port': 39999, 'container': 'amnezia-awg', 'iface': 'wg0', 'imported': True}
    ctx.image = 'ga-test-client:latest'
    return ctx


class KmodLiveCheck(unittest.TestCase):

    def setUp(self):
        acceptance.ROWS.clear()
        p = mock.patch.object(acceptance.time, 'sleep', lambda s: None)
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch('builtins.print')
        q.start()
        self.addCleanup(q.stop)

    def rows(self):
        return [(r[0], r[1]) for r in acceptance.ROWS]

    def fails(self):
        return [r for r in acceptance.ROWS if r[1] == 'FAIL']

    def test_the_whole_road_both_ways_is_ok(self):
        m = Model()
        acceptance.c_kmod(ctx_for(m))
        self.assertEqual(self.fails(), [])
        self.assertEqual(m.actions, ['user-add', 'kmod-install', 'kmod-engine:kernel', 'kmod-engine:program',
                                     'kmod-engine:kernel', 'kmod-engine:program', 'kmod-remove', 'user-del'])
        names = [r[0] for r in acceptance.ROWS]
        for want in ('kmod: host check', 'kmod: clients up, one key each for the whole check', 'kmod: baseline awg2 client',
                     'kmod: kmod-install', 'kmod: leg 1 → kernel', 'kmod: leg 1 awg2 client', 'kmod: leg 2 → program',
                     'kmod: leg 3 → kernel', 'kmod: leg 3 awg3 client', 'kmod: TCP MSS rules once after every restart',
                     'kmod: start.sh — goWARP block intact, engine block, sh -n', 'kmod: restore → program',
                     'kmod: restore awg2 client', 'kmod: kmod-remove', 'kmod: engines as before the check',
                     'kmod: kernel log clean (BUG, soft lockup, Oops)', 'kmod: snapshot before/after'):
            self.assertIn(want, names)
        self.assertNotIn('awg1', ' '.join(names), 'профиль приложения на wg0 модуль не переключает')
        self.assertEqual(m.clients, {}, 'клиенты убраны')
        self.assertEqual(m.users, set(), 'временный человек удалён')
        self.assertEqual(set(m.engine.values()), {'program'})
        self.assertFalse(m.installed)
        ev = dict((r[0], r[2]) for r in acceptance.ROWS)
        self.assertIn('back 1/1 in 17s', ev['kmod: leg 1 → kernel'])
        self.assertIn('keys same (client usrawg2, server srvawg2)', ev['kmod: leg 2 awg2 client'])
        self.assertIn('iperf 400/500 Mbit/s', ev['kmod: leg 1 awg2 client'])
        self.assertIn('kernel 400/500', ev['kmod: iperf3 program vs kernel (Mbit/s up/down, for the record)'])

    def test_no_modules_on_this_host_is_one_skip_and_nothing_else(self):
        m = Model(check_ok=False)
        acceptance.c_kmod(ctx_for(m))
        self.assertEqual(self.rows(), [('kmod: host check', 'SKIP')])
        self.assertIn('container', acceptance.ROWS[0][2])
        self.assertEqual(m.actions, [])

    def test_a_key_changed_between_steps_fails_that_profile(self):
        m = Model(key_change_on=1)
        acceptance.c_kmod(ctx_for(m))
        bad = dict((r[0], r[2]) for r in self.fails())
        self.assertIn('kmod: leg 1 awg2 client', bad)
        self.assertIn('KEYS CHANGED', bad['kmod: leg 1 awg2 client'])
        self.assertNotIn('kmod: leg 1 awg3 client', bad)

    def test_a_rolled_back_switch_stops_the_legs_but_restores_and_cleans_up(self):
        m = Model(rollback_on=1)
        acceptance.c_kmod(ctx_for(m))
        bad = [r[0] for r in self.fails()]
        self.assertIn('kmod: leg 1 → kernel', bad)
        self.assertIn('kmod_clients_lost', dict((r[0], r[2]) for r in acceptance.ROWS)['kmod: leg 1 → kernel'])
        self.assertNotIn('kmod-engine:program', m.actions[:m.actions.index('kmod-engine:kernel') + 1])
        self.assertEqual(m.actions.count('kmod-engine:kernel'), 1, 'после отката плечи дальше не идут')
        self.assertEqual(m.actions[-2:], ['kmod-remove', 'user-del'])
        self.assertEqual(m.clients, {})
        self.assertEqual(m.users, set())

    def test_a_module_that_was_there_before_is_left(self):
        m = Model(had_module=True)
        acceptance.c_kmod(ctx_for(m))
        self.assertNotIn('kmod-remove', m.actions)
        self.assertIn(('kmod: module left installed', 'OK'), self.rows())
        self.assertEqual(self.fails(), [])

    def test_a_drop_right_after_the_service_started_is_not_a_second_start(self):
        # SSH оборвался, когда служба kmod-install уже запущена: harness не зовёт её снова и не валит шаг — ждёт ответа
        m = Model()
        acceptance.c_kmod(ctx_for(m, drop_after_start=('kmod-install',)))
        self.assertEqual(self.fails(), [])
        self.assertEqual(m.actions.count('kmod-install'), 1)

    def test_bug_lines_in_the_kernel_log_fail(self):
        m = Model(klog_bad=['BUG: soft lockup - CPU#0 stuck for 22s! [kworker/0:1:42]'])
        acceptance.c_kmod(ctx_for(m))
        self.assertEqual([r[0] for r in self.fails()], ['kmod: kernel log clean (BUG, soft lockup, Oops)'])


class AcceptanceKeys(unittest.TestCase):
    """KMODB-04: у сервера и клиента бывают разные ключи SSH (почасовые стенды) — --key у сервера, --client-key у
    клиента; без --client-key клиент идёт с --key, как раньше. Обходить ssh-agent'ом больше не нужно."""

    def parse(self, *extra):
        return acceptance.build_parser().parse_args(
            ['--server', 'root@203.0.113.20', '--client', 'root@198.51.100.10'] + list(extra))

    def test_each_host_gets_its_own_key(self):
        with mock.patch.object(acceptance, 'Host', lambda target, key: (target, key)):
            a = self.parse('--key', '/keys/stand', '--client-key', '/keys/client', '--checks', 'kmod')
            self.assertEqual(acceptance.hosts(a), (('root@203.0.113.20', '/keys/stand'),
                                                   ('root@198.51.100.10', '/keys/client')))
            a = self.parse('--key', '/keys/stand')
            self.assertEqual(acceptance.hosts(a), (('root@203.0.113.20', '/keys/stand'),
                                                   ('root@198.51.100.10', '/keys/stand')))

    def test_ssh_argv_carries_the_client_key(self):
        chan = mock.Mock()
        chan.hosts = {}
        chan.opts.return_value = []
        client = acceptance.Host('root@198.51.100.10', '/keys/client', chan)
        argv = client.argv('true')
        self.assertEqual(argv[argv.index('-i') + 1], '/keys/client')


class KmodServerHelper(unittest.TestCase):

    def engine(self, out):
        with mock.patch.object(kmod_server, 'dexec', lambda c, cmd, timeout=60: (0, out, '')):
            return kmod_server.engine_of('amnezia-awg2', 'awg0')

    def test_engine_like_the_product(self):
        self.assertEqual(self.engine('5: awg0: <POINTOPOINT,NOARP,UP> mtu 1280\n    link/none\n    amneziawg addrgenmode none\n'),
                         'kernel')
        self.assertEqual(self.engine('5: awg0: <POINTOPOINT,UP> mtu 1280\n    link/none\n    tun type tun pi off\ntun_flags=1\n'),
                         'program')
        self.assertEqual(self.engine(''), 'unknown')

    def test_kernel_log_filter(self):
        for bad in ('BUG: unable to handle page fault', 'watchdog: BUG: soft lockup - CPU#0 stuck for 23s!',
                    'Oops: 0000 [#1] PREEMPT SMP', 'general protection fault, probably for non-canonical address',
                    'Call Trace:', 'INFO: task kworker/0:2:91 blocked for more than 122 seconds.'):
            self.assertRegex(bad, kmod_server.BAD)
        for fine in ('amneziawg: loading out-of-tree module taints kernel.', 'amneziawg: AmneziaWG 3.1.20260906+kaskad-k31.1 loaded'):
            self.assertNotRegex(fine, kmod_server.BAD)

    def test_fingerprint_matches_the_client_script(self):
        # kmod_client.sh: `awg show … public-key | sha256sum | cut -c1-16` — ключ с переводом строки
        import hashlib
        self.assertEqual(kmod_server.fp('abc='), hashlib.sha256(b'abc=\n').hexdigest()[:16])
        self.assertEqual(kmod_server.fp(''), '')


if __name__ == '__main__':
    unittest.main()
